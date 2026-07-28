"""The cross/upsell rule catalogue.

A transparent, deterministic layer the hybrid engine runs *before* any AI
scoring. Each rule fires on a condition over the SFO profile (current services,
asset mix, pain points, AUM, lifecycle stage) and proposes a FastFund service to
cross-sell or upsell, with a base score, a kind, and a plain-English rationale.

This is the auditable backbone — every recommendation can be traced to the rule
(or graph edge) that produced it, which matters for a regulated wealth business.
The AI layer (``engine.crosssell``) re-ranks and enriches; it never invents the
candidate set alone.

A rule is a dict:
    {
      "id": str,
      "when": callable(profile) -> bool,
      "service": service_key,        # target FastFund service
      "kind": "cross_sell" | "upsell",
      "score": float (0-1 base),
      "rationale": str,              # human explanation
    }
"""

from __future__ import annotations


def _has(profile, key) -> bool:
    return key in (profile.get("current_services") or [])


def _pain(profile, *needles) -> bool:
    blob = " ".join(profile.get("pain_points") or []).lower()
    return any(n in blob for n in needles)


def _alloc(profile, asset_class) -> float:
    return float((profile.get("asset_mix") or {}).get(asset_class, 0) or 0)


RULES = [
    # ── Governance / succession ──────────────────────────────────────────────
    {"id": "gov_gap", "when": lambda p: not _has(p, "governance")
        and (p.get("generations", 1) >= 2 or _pain(p, "succession", "governance", "next-gen", "conflict")),
     "service": "governance", "kind": "cross_sell", "score": 0.85,
     "rationale": "Multi-generational family with no formal governance framework — "
                  "a family governance and succession mandate addresses the stated gap."},
    {"id": "nextgen_edu", "when": lambda p: _has(p, "governance")
        and p.get("generations", 1) >= 2,
     "service": "nextgen_education", "kind": "upsell", "score": 0.6,
     "rationale": "Governance already in place — extend into next-generation "
                  "education / legacy planning to operationalise the framework."},

    # ── Trusts → tax & banking ──────────────────────────────────────────────
    {"id": "trust_to_tax", "when": lambda p: _has(p, "trusts") and not _has(p, "tax_reporting"),
     "service": "tax_reporting", "kind": "cross_sell", "score": 0.8,
     "rationale": "Existing trust client without tax reporting — natural cross-sell "
                  "to multi-jurisdiction tax compliance and reporting."},
    {"id": "trust_to_banking", "when": lambda p: _has(p, "trusts") and not _has(p, "banking_treasury"),
     "service": "banking_treasury", "kind": "cross_sell", "score": 0.65,
     "rationale": "Trust structures generate cash-management needs — banking & "
                  "treasury services keep liquidity under one relationship."},

    # ── Asset-mix driven ────────────────────────────────────────────────────
    {"id": "pe_to_fundadmin", "when": lambda p: _alloc(p, "private_equity") >= 15
        and not _has(p, "fund_admin"),
     "service": "fund_admin", "kind": "cross_sell", "score": 0.82,
     "rationale": "Material private-equity / direct-deal exposure — fund "
                  "administration for the PE holdings reduces operational burden."},
    {"id": "luxury_admin", "when": lambda p: _alloc(p, "luxury") >= 5
        and not _has(p, "luxury_assets"),
     "service": "luxury_assets", "kind": "cross_sell", "score": 0.78,
     "rationale": "Significant passion / luxury assets (yacht, aircraft, art) — "
                  "specialist luxury asset administration and insurance coordination."},
    {"id": "realestate_admin", "when": lambda p: _alloc(p, "real_estate") >= 15
        and not _has(p, "real_estate_admin"),
     "service": "real_estate_admin", "kind": "cross_sell", "score": 0.6,
     "rationale": "Sizeable real-estate allocation — SPV administration and "
                  "property-holding structuring across jurisdictions."},

    # ── Reporting / Edge platform upsell ─────────────────────────────────────
    {"id": "edge_reporting", "when": lambda p: (_pain(p, "reporting", "consolidat", "visibility", "complexity")
        or len(p.get("current_services") or []) >= 3) and not _has(p, "edge"),
     "service": "edge", "kind": "upsell", "score": 0.7,
     "rationale": "Reporting complexity across multiple services — FastFund Edge gives "
                  "consolidated, real-time multi-entity reporting."},

    # ── Scale-driven upsells ────────────────────────────────────────────────
    {"id": "large_aum_private_office", "when": lambda p: (p.get("aum_usd") or 0) >= 1_000_000_000
        and not _has(p, "private_office"),
     "service": "private_office", "kind": "upsell", "score": 0.75,
     "rationale": "Billion-dollar-plus AUM — a dedicated FastFund Family Office "
                  "relationship consolidates the full service stack."},
    {"id": "regulatory_compliance", "when": lambda p: _pain(p, "regulat", "compliance", "aeoi", "fatca", "crs")
        and not _has(p, "compliance"),
     "service": "compliance", "kind": "cross_sell", "score": 0.62,
     "rationale": "Stated regulatory / AEOI burden — managed regulatory & "
                  "compliance reporting (FATCA/CRS, economic substance)."},
]


def fire(profile: dict) -> list[dict]:
    """Return the rule recommendations triggered by ``profile``.

    Each item: {service, kind, score, rationale, source='rule', rule_id}.
    """
    out = []
    for r in RULES:
        try:
            if r["when"](profile):
                out.append({"service": r["service"], "kind": r["kind"],
                            "score": r["score"], "rationale": r["rationale"],
                            "source": "rule", "rule_id": r["id"]})
        except Exception:  # noqa: BLE001 — a malformed profile never breaks the engine
            continue
    return out
