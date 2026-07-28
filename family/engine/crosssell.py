"""The hybrid cross/upsell engine.

Pipeline (rules → graph expansion → AI re-rank → persist):

1. **Rules** (``engine.rules.fire``) produce the transparent candidate set from
   the SFO profile.
2. **Graph expansion** walks ``(:Service)-[:CROSS_SELLS_TO]->(:Service)`` from
   each held service to surface bundle partners the rules didn't name (down-
   weighted, marked ``source='graph'``).
3. **AI re-rank** (optional) asks the LLM to score each candidate's fit for *this*
   family and rewrite the rationale in a relationship-manager voice. Degrades
   gracefully: with no LLM the rule/graph scores stand (``source`` unchanged).
4. **Persist** each candidate as a ``RECOMMENDED`` edge / row via the store, so
   the funnel and analytics see it.

Estimated pipeline value is a simple, explainable function of AUM × a per-service
fee-rate heuristic — enough to demo a credible upsell pipeline number.
"""

from __future__ import annotations

import json

from datetime import date, timedelta

from family import sfostore as store
from family.rag import llm
from family.engine import rules

# Indicative annual-fee basis points by service tier, applied to AUM for a
# demo-grade "pipeline value". Purely illustrative.
_TIER_BPS = {"core": 8, "premium": 18}
_VALUE_CAP = 5_000_000  # cap a single rec's estimated annual value


def _est_value(profile: dict, service: dict) -> float:
    aum = float(profile.get("aum_usd") or 0)
    bps = _TIER_BPS.get(service.get("tier") or "core", 8)
    return min(aum * bps / 10_000.0, _VALUE_CAP)


def _graph_candidates(profile: dict, already: set[str]) -> list[dict]:
    """Bundle partners reachable from the family's held services."""
    out = []
    for held in profile.get("current_services") or []:
        for partner in store.list_cross_sells(held):
            key = partner.get("key")
            if not key or key in already or key in (profile.get("current_services") or []):
                continue
            w = float(partner.get("weight") or 0.5)
            out.append({"service": key, "kind": "cross_sell",
                        "score": round(0.4 + 0.4 * w, 3),
                        "rationale": f"Commonly bundled after {held.replace('_', ' ')}.",
                        "source": "graph", "rule_id": None})
            already.add(key)
    return out


_AI_SYS = (
    "You are a FastFund Family Office relationship manager. Given a family-office "
    "profile and a list of candidate services, score each candidate 0-1 for how "
    "well it fits THIS family right now and rewrite its rationale in one warm, "
    "specific sentence a relationship manager would say. Respond ONLY as a JSON "
    "array of objects: {\"service\": key, \"score\": number, \"rationale\": text}. "
    "Keep the same service keys; do not invent services."
)


def _ai_rerank(profile: dict, candidates: list[dict]) -> list[dict]:
    if not llm.ai_available() or not candidates:
        return candidates
    prof = {k: profile.get(k) for k in ("name", "aum_usd", "generations",
            "current_services", "asset_mix", "pain_points", "stage")}
    user = (f"Profile:\n{json.dumps(prof, default=str)}\n\n"
            f"Candidates:\n{json.dumps([{'service': c['service'], 'rationale': c['rationale']} for c in candidates])}")
    raw = llm.complete(_AI_SYS, user)
    try:
        start, end = raw.find("["), raw.rfind("]")
        scored = {o["service"]: o for o in json.loads(raw[start:end + 1])}
    except Exception:  # noqa: BLE001 — keep rule scores if the model misbehaves
        return candidates
    for c in candidates:
        o = scored.get(c["service"])
        if not o:
            continue
        try:
            c["score"] = round((c["score"] + float(o["score"])) / 2, 3)
        except (TypeError, ValueError):
            pass
        if o.get("rationale"):
            c["rationale"] = o["rationale"]
        c["source"] = "hybrid"
    return candidates


def recommend(sfo_id: int, persist: bool = True, use_ai: bool = True) -> list[dict]:
    """Generate (and optionally persist) ranked recommendations for one SFO."""
    profile = store.get_sfo(sfo_id)
    if not profile:
        return []

    candidates = rules.fire(profile)
    seen = {c["service"] for c in candidates}
    candidates += _graph_candidates(profile, seen)
    if use_ai:
        candidates = _ai_rerank(profile, candidates)

    # Resolve to real services, attach value, sort, dedupe by service.
    resolved, out_keys = [], set()
    for c in sorted(candidates, key=lambda x: x["score"], reverse=True):
        if c["service"] in out_keys:
            continue
        svc = store.get_service_by_key(c["service"])
        if not svc:
            continue
        out_keys.add(c["service"])
        rec = {**c, "service_id": svc["id"], "service_name": svc["name"],
               "service_category": svc.get("category"),
               "est_value_usd": round(_est_value(profile, svc), 2)}
        resolved.append(rec)

    if persist:
        for rec in resolved:
            store.upsert_recommendation({
                "sfo_id": sfo_id, "service_id": rec["service_id"],
                "kind": rec["kind"], "score": rec["score"],
                "rationale": rec["rationale"], "est_value_usd": rec["est_value_usd"],
                "source": rec["source"]})
    return resolved


def generate_proposal(recommendation_id: int) -> str:
    """Draft + store an AI proposal for one persisted recommendation."""
    from family.engine import proposals
    rec = next((r for r in store.list_recommendations(limit=10000)
                if r["id"] == recommendation_id), None)
    if not rec:
        return ""
    profile = store.get_sfo(rec["sfo_id"])
    service = store.get_service(rec["service_id"])
    if not profile or not service:
        return ""
    text = proposals.draft(profile, service, rec.get("rationale") or "")
    store.set_recommendation_proposal(recommendation_id, text)
    return text


def schedule_action(sfo_id: int, kind: str, title: str, due_days: int,
                    recommendation_id: int | None = None, notes: str = "") -> int:
    """Create a next-action due ``due_days`` from today (the pipeline calendar)."""
    due = (date.today() + timedelta(days=due_days)).isoformat()
    return store.upsert_next_action({
        "sfo_id": sfo_id, "recommendation_id": recommendation_id, "kind": kind,
        "title": title, "due_date": due, "status": "open", "notes": notes})
