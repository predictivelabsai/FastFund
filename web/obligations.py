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


def determine_obligations(store, entity: dict) -> list[dict]:
    """Match curated catalogue forms to the entity → obligation dicts (not saved)."""
    cats = relevant_categories(entity)
    obs, seen = [], set()
    for jc in (entity.get("jurisdictions") or []):
        for f in store.list_forms(jurisdiction_code=jc, limit=2000):
            if (f.get("form_key") or "").startswith("auto_"):
                continue  # bulk-discovered PDFs aren't obligations — only curated forms
            if (f.get("category") or "") not in cats:
                continue
            if f["id"] in seen:
                continue
            seen.add(f["id"])
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
    for o in obs:
        store.upsert_obligation(o)
    return {"entity": e["name"], "obligations": len(obs),
            "categories": sorted(relevant_categories(e))}
