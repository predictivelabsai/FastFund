"""Cross-backend storage contract test.

One suite parametrised over both backends so ``DATA_STORAGE`` can't silently
change behaviour. The Neo4j case skips if no Neo4j is reachable.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from storage.sqlite_store import SqliteStore


def _sqlite():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SqliteStore(db_url=f"sqlite:///{path}")
    s.init_db()
    return s


def _neo4j():
    try:
        from storage.neo4j_store import Neo4jStore
        s = Neo4jStore()
        s.init_db()
        return s
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Neo4j unavailable: {e}")


@pytest.fixture(params=["sqlite", "neo4j"])
def store(request):
    return _sqlite() if request.param == "sqlite" else _neo4j()


def test_sfo_roundtrip(store):
    sid = store.upsert_sfo({
        "client_ref": "T-1", "name": "Test Family Office", "family_name": "Test",
        "aum_usd": 5e8, "family_size": 6, "generations": 3, "domicile": "JE",
        "jurisdictions": ["JE", "LU"], "current_services": ["trusts"],
        "asset_mix": {"private_equity": 30, "cash": 10}, "pain_points": ["governance gap"],
        "stage": "client"})
    sfo = store.get_sfo(sid)
    assert sfo["name"] == "Test Family Office"
    assert sfo["jurisdictions"] == ["JE", "LU"]
    assert sfo["asset_mix"]["private_equity"] == 30
    assert sfo["pain_points"] == ["governance gap"]
    # Upsert is idempotent on client_ref.
    assert store.upsert_sfo({"client_ref": "T-1", "name": "Renamed FO"}) == sid
    assert store.get_sfo(sid)["name"] == "Renamed FO"


def test_services_and_cross_sell(store):
    store.upsert_service({"key": "trusts", "name": "Trusts", "category": "structuring",
                          "tier": "core", "description": "trust admin", "keywords": ["trust"]})
    store.upsert_service({"key": "tax", "name": "Tax", "category": "compliance",
                          "tier": "core", "description": "tax", "keywords": ["tax"]})
    store.add_cross_sell_edge("trusts", "tax", 0.9)
    partners = store.list_cross_sells("trusts")
    assert [p["key"] for p in partners] == ["tax"]
    assert partners[0]["weight"] == pytest.approx(0.9)
    assert store.search_services("trust")[0]["key"] == "trusts"


def test_recommendation_funnel(store):
    sid = store.upsert_sfo({"client_ref": "R-1", "name": "Rec FO", "aum_usd": 1e9})
    vid = store.upsert_service({"key": "edge", "name": "Edge", "category": "reporting",
                               "tier": "premium", "description": "reporting"})
    rid = store.upsert_recommendation({"sfo_id": sid, "service_id": vid, "kind": "upsell",
                                       "score": 0.7, "rationale": "fits", "est_value_usd": 1e5})
    assert store.upsert_recommendation({"sfo_id": sid, "service_id": vid, "score": 0.9}) == rid
    recs = store.list_recommendations(sfo_id=sid)
    assert recs[0]["status"] == "suggested" and recs[0]["service_name"] == "Edge"
    store.set_recommendation_status(rid, "booked")
    funnel = store.upsell_funnel()
    assert funnel["by_status"].get("booked", 0) >= 1
    assert funnel["pipeline_usd"] >= 1e5


def test_conversations(store):
    sid = store.upsert_sfo({"client_ref": "C-1", "name": "Conv FO"})
    cid = store.create_conversation("admin@jtcgroup.com", sfo_id=sid, title="Intro")
    store.add_message(cid, "user", "hi")
    store.add_message(cid, "assistant", "hello")
    msgs = store.get_messages(cid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert store.list_conversations(user_email="admin@jtcgroup.com")[0]["id"] == cid


def test_family_members(store):
    sid = store.upsert_sfo({"client_ref": "F-1", "name": "Fam FO"})
    mid = store.upsert_family_member({"sfo_id": sid, "name": "Anna", "role": "principal",
                                      "generation": 1, "age": 68})
    assert store.upsert_family_member({"sfo_id": sid, "name": "Anna", "age": 69}) == mid
    members = store.list_family_members(sid)
    assert len(members) == 1 and members[0]["age"] == 69
    store.delete_family_member(mid)
    assert store.list_family_members(sid) == []


def test_documents(store):
    sid = store.upsert_sfo({"client_ref": "D-1", "name": "Doc FO"})
    did = store.add_document({"sfo_id": sid, "name": "portfolio.pdf",
                              "doc_type": "portfolio", "storage_key": "k/1.pdf",
                              "byte_size": 1234})
    doc = store.get_document(did)
    assert doc["name"] == "portfolio.pdf" and doc["sfo_id"] == sid
    assert store.list_documents(sfo_id=sid)[0]["sfo_name"] == "Doc FO"
    store.delete_document(did)
    assert store.get_document(did) is None


def test_next_actions(store):
    sid = store.upsert_sfo({"client_ref": "N-1", "name": "Act FO"})
    aid = store.upsert_next_action({"sfo_id": sid, "kind": "consultation",
                                    "title": "Intro call", "due_date": "2026-07-01"})
    aid2 = store.upsert_next_action({"sfo_id": sid, "kind": "follow_up",
                                     "title": "Send proposal", "due_date": "2026-06-20"})
    acts = store.list_next_actions(sfo_id=sid)
    assert [a["title"] for a in acts] == ["Send proposal", "Intro call"]  # soonest first
    assert store.list_next_actions(due_before="2026-06-25")[0]["id"] == aid2
    store.set_next_action_status(aid, "done")
    assert store.list_next_actions(sfo_id=sid, status="done")[0]["id"] == aid
    store.delete_next_action(aid2)


def test_proposal(store):
    sid = store.upsert_sfo({"client_ref": "P-1", "name": "Prop FO"})
    vid = store.upsert_service({"key": "gov", "name": "Governance", "category": "governance",
                               "tier": "premium", "description": "gov"})
    rid = store.upsert_recommendation({"sfo_id": sid, "service_id": vid, "score": 0.8,
                                       "est_value_usd": 5e5})
    store.set_recommendation_proposal(rid, "Dear family, we propose ...")
    rec = store.list_recommendations(sfo_id=sid)[0]
    assert rec["proposal"].startswith("Dear family")
    # partial upsert must not clobber the proposal or value
    store.upsert_recommendation({"sfo_id": sid, "service_id": vid, "score": 0.95})
    rec = store.list_recommendations(sfo_id=sid)[0]
    assert rec["proposal"].startswith("Dear family") and rec["est_value_usd"] == 5e5
