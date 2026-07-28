"""Platform layer — teams, RBAC, per-team isolation, invites, chat feedback/evals.

Runs against SqliteStore (fast, deterministic). The Neo4j backend implements the
same methods and is smoke-tested separately; these lock the behaviour/contract.
"""
from __future__ import annotations

import pytest

from storage.sqlite_store import SqliteStore


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(db_url=f"sqlite:///{tmp_path/'platform.db'}")
    s.init_db()
    return s


def _entity(name, team_id, ref):
    return {"name": name, "type": "fund", "domicile": "KY", "jurisdictions": ["KY"],
            "fy_end": "31 December", "activities": ["holding"], "client_ref": ref,
            "team_id": team_id}


# ── Seeding / platform admins ────────────────────────────────────────────────
def test_seed_creates_default_team_and_admins(store):
    teams = store.list_teams()
    assert any(t["name"] == "FastFund" for t in teams)
    emails = {u["email"]: u["role"] for u in store.list_users()}
    assert emails.get("julian@predictivelabs.co.uk") == "admin"
    assert emails.get("kaljuvee@gmail.com") == "admin"


# ── Teams + membership + roles ───────────────────────────────────────────────
def test_team_membership_and_roles(store):
    t = store.create_team("Acme FO")
    uid = store.get_or_create_oauth_user("m@acme.com", "M")["id"]
    store.add_team_member(t, uid, "member")
    assert store.team_role(t, uid) == "member"
    store.add_team_member(t, uid, "admin")  # upsert promotes
    assert store.team_role(t, uid) == "admin"
    assert any(m["user_id"] == uid for m in store.list_team_members(t))
    assert {x["id"] for x in store.list_teams_for_user(uid)} == {t}
    store.remove_team_member(t, uid)
    assert store.team_role(t, uid) is None


# ── Per-team data isolation ──────────────────────────────────────────────────
def test_entity_isolation_by_team(store):
    a = store.list_teams()[0]["id"]
    b = store.create_team("Team B")
    store.upsert_entity(_entity("Alpha", a, "A1"))
    store.upsert_entity(_entity("Bravo", b, "B1"))
    assert [e["name"] for e in store.list_entities(team_id=a)] == ["Alpha"]
    assert [e["name"] for e in store.list_entities(team_id=b)] == ["Bravo"]
    assert store.count_entities(team_id=a) == 1
    assert store.count_entities() == 2  # unscoped sees both


def test_obligation_isolation_by_team(store):
    a = store.list_teams()[0]["id"]
    b = store.create_team("Team B")
    ea = store.upsert_entity(_entity("Alpha", a, "A1"))
    eb = store.upsert_entity(_entity("Bravo", b, "B1"))
    fa = store.upsert_form({"jurisdiction_code": "KY", "form_key": "k1", "title": "F1",
                            "category": "aeoi", "form_type": "return"})
    store.upsert_obligation({"entity_id": ea, "form_id": fa, "title": "Oa",
                             "jurisdiction_code": "KY", "category": "aeoi"})
    store.upsert_obligation({"entity_id": eb, "form_id": fa, "title": "Ob",
                             "jurisdiction_code": "KY", "category": "aeoi"})
    assert [o["entity_id"] for o in store.list_obligations(team_id=a)] == [ea]
    assert [o["entity_id"] for o in store.list_obligations(team_id=b)] == [eb]
    assert len(store.list_obligations()) == 2


# ── Invites ──────────────────────────────────────────────────────────────────
def test_invite_lifecycle(store):
    t = store.list_teams()[0]["id"]
    inviter = store.list_users()[0]["id"]
    store.create_invite("new@firm.com", t, "member", "tok123", inviter,
                        "2099-01-01T00:00:00+00:00")
    inv = store.get_invite_by_token("tok123")
    assert inv and inv["email"] == "new@firm.com" and inv["team_id"] == t
    assert inv["used_at"] is None
    store.mark_invite_used("tok123")
    assert store.get_invite_by_token("tok123")["used_at"] is not None
    iid = store.list_invites(team_id=t)[0]["id"]
    store.delete_invite(iid)
    assert store.get_invite_by_token("tok123") is None


# ── Chat logging + feedback + judge + analytics ──────────────────────────────
def test_chat_feedback_judge_and_analytics(store):
    t = store.list_teams()[0]["id"]
    sid = store.create_chat_session("u@x.com", "hi", team_id=t)
    q = store.add_chat_message(sid, "user", "What does Alpha owe?")
    a = store.add_chat_message(sid, "assistant", "Alpha owes X.")
    assert isinstance(a, int) and a > q
    store.add_message_feedback(a, "u@x.com", t, 1, "good")
    assert store.feedback_for_messages([q, a]) == {a: 1}
    # un-judged before, judged after
    assert store.list_chat_turns(team_id=t, needs_judge=True)
    store.add_message_judge(a, 5, "good", 5, 5, 4, "solid", "grok")
    assert not store.list_chat_turns(team_id=t, needs_judge=True)
    turn = next(x for x in store.list_chat_turns(team_id=t) if x["message_id"] == a)
    assert turn["question"] == "What does Alpha owe?" and turn["rating"] == 1
    assert turn["judge_score"] == 5
    an = store.chat_analytics(team_id=t)
    assert an["turns"] == 1 and an["thumbs_up"] == 1 and an["judged"] == 1
    assert an["avg_judge"] == 5.0


def test_chat_history_scoped_by_team(store):
    a = store.list_teams()[0]["id"]
    b = store.create_team("Team B")
    store.create_chat_session("u@x.com", "in A", team_id=a)
    store.create_chat_session("u@x.com", "in B", team_id=b)
    titles_a = {s["title"] for s in store.list_chat_sessions("u@x.com", team_id=a)}
    assert "in A" in titles_a and "in B" not in titles_a


def test_event_log(store):
    t = store.list_teams()[0]["id"]
    store.log_event("login", "u@x.com", t, "ok")
    store.log_event("chat_turn", "u@x.com", t, "asked X")
    evs = store.list_events(limit=10, team_id=t)
    assert {e["type"] for e in evs} == {"login", "chat_turn"}
