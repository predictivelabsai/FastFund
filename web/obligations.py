"""The Determine engine — generate an entity's filing obligations.

Sphere-style: a **deterministic** matcher over the expert-curated forms catalogue
(zero hallucination). For an entity, we take its operating jurisdictions and map
its activities/type to the relevant obligation categories, then create one
obligation per *curated* key form (not the bulk auto-discovered PDFs) in those
jurisdictions+categories. Each obligation is born ``verified=False`` so a human
signs it off — the human-in-the-loop guardrail.

Due-date *computation* (turning a deadline rule + the entity's FY-end into a
concrete date) is deferred to the Monitor phase; here we carry the deadline rule
and the entity's financial year-end as the obligation ``period``.
"""
from __future__ import annotations

import re

# Always-relevant obligation categories for a fund-domiciled entity.
_BASE_CATEGORIES = {"corporate_tax", "economic_substance", "aeoi"}

# Activity → additional relevant categories (the "codified" mapping).
_ACTIVITY_CATEGORIES = {
    "fund management": {"fund", "partnership"},
    "holding": set(),
    "finance & leasing": set(),
    "headquartering": set(),
    "intellectual property": set(),
    "distribution & service centre": set(),
}


def relevant_categories(entity: dict) -> set[str]:
    cats = set(_BASE_CATEGORIES)
    for a in (entity.get("activities") or []):
        cats |= _ACTIVITY_CATEGORIES.get(a.strip().lower(), set())
    # GP/partnership entity types pull in partnership filings.
    if (entity.get("type") or "").lower() in {"gp", "fund"}:
        cats.add("partnership")
    return cats


def _norm_title(title: str, jc: str) -> str:
    """Normalise a form title for de-duplication: lowercase, drop the jurisdiction
    name/code and any parenthetical, collapse punctuation. Catches catalogue
    near-duplicates (e.g. two 'Economic Substance Notification — User Guide' rows)."""
    t = (title or "").lower()
    t = re.sub(r"\(.*?\)", " ", t)                 # drop "(Cayman)" etc.
    for w in ("cayman", "jersey", "guernsey", "luxembourg", "ireland", jc.lower()):
        t = t.replace(w, " ")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def determine_obligations(store, entity: dict) -> list[dict]:
    """Match curated catalogue forms to the entity → obligation dicts (not saved).
    De-duplicates near-identical titles within a jurisdiction (prefers the entry
    that carries a filing deadline)."""
    cats = relevant_categories(entity)
    obs, seen_ids = [], set()
    best: dict[tuple, dict] = {}            # (jc, normalised title) -> chosen form
    for jc in (entity.get("jurisdictions") or []):
        for f in store.list_forms(jurisdiction_code=jc, limit=2000):
            if (f.get("form_key") or "").startswith("auto_"):
                continue  # bulk-discovered PDFs aren't obligations — only curated forms
            if (f.get("category") or "") not in cats:
                continue
            if f["id"] in seen_ids:
                continue
            seen_ids.add(f["id"])
            key = (jc, f.get("category"), _norm_title(f.get("title"), jc))
            cur = best.get(key)
            if cur is None or (not cur.get("deadline") and f.get("deadline")):
                best[key] = f
    for jc in (entity.get("jurisdictions") or []):
        for (k_jc, _cat, _t), f in best.items():
            if k_jc != jc:
                continue
            obs.append({
                "entity_id": entity["id"], "form_id": f["id"], "title": f.get("title"),
                "jurisdiction_code": f.get("jurisdiction_code") or jc,
                "category": f.get("category"), "deadline": f.get("deadline"),
                "period": entity.get("fy_end"),
            })
    return obs


def run_determine(store, entity_id: int) -> dict:
    """Generate/refresh obligations for an entity. Idempotent (upsert preserves
    status/verified). Returns counts."""
    e = store.get_entity(entity_id)
    if not e:
        return {"error": "entity not found"}
    obs = determine_obligations(store, e)
    keep = {o["form_id"] for o in obs}
    for o in obs:
        store.upsert_obligation(o)
    # Reconcile: prune obligations that no longer apply (jurisdiction/activity
    # change, or de-duplicated catalogue rows) so the set is authoritative.
    pruned = 0
    for ex in store.list_obligations(entity_id=entity_id):
        if ex["form_id"] not in keep:
            store.delete_obligation(ex["id"]); pruned += 1
    return {"entity": e["name"], "obligations": len(obs), "pruned": pruned,
            "categories": sorted(relevant_categories(e))}
