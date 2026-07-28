"""Knowledge retrieval for the advisor — FastFund services + industry benchmarks.

Two small corpora the conversational agent grounds its answers in:

1. The **FastFund service catalogue** stored in the graph (``store.search_services``) —
   what the relationship manager can actually offer and cross-sell.
2. A compact set of **public industry benchmarks** (aggregated SFO allocation /
   AUM / governance stats) used to make recommendations sound advisory rather
   than salesy. These are aggregate, non-attributed figures suitable for a demo.

Retrieval is keyword-based today (substring over service text). The interface is
deliberately the same shape as FastFund's retriever ABC, so a vector / hybrid
retriever (fastembed + a vector index) can be slotted in later without touching
the agent — see the architecture roadmap.
"""

from __future__ import annotations

from family import sfostore as store

# Aggregate public-benchmark snippets the advisor can cite (illustrative, non-attributed).
BENCHMARKS = [
    {"topic": "allocation", "text": "Single family offices allocate on average "
     "~28% to private equity, ~20% to public equities, ~15% to real estate, with "
     "the balance in fixed income, cash and alternatives."},
    {"topic": "governance", "text": "A majority of family offices cite succession "
     "planning and next-generation governance as their top long-term challenge; "
     "fewer than half have a formal, documented governance framework."},
    {"topic": "luxury", "text": "Passion and luxury assets (yachts, aircraft, art) "
     "are an increasing share of UHNW balance sheets and create distinct "
     "administration, insurance and reporting needs often unmet by core advisors."},
    {"topic": "reporting", "text": "Consolidated, multi-entity, multi-currency "
     "reporting is a recurring pain point as portfolios diversify across funds, "
     "direct deals and private assets — a driver of demand for platforms like FastFund Edge."},
    {"topic": "structuring", "text": "As wealth crosses generations and "
     "jurisdictions, demand rises for trust/foundation structuring, tax reporting "
     "and treasury/banking services delivered under one relationship."},
]


def search_benchmarks(query: str, limit: int = 3) -> list[dict]:
    q = (query or "").lower()
    hits = [b for b in BENCHMARKS if any(w in b["text"].lower() or w in b["topic"]
            for w in q.split() if len(w) > 3)]
    return (hits or BENCHMARKS)[:limit]


def services_context(query: str, limit: int = 6) -> str:
    """Numbered, citable context blocks of matching FastFund services."""
    svcs = store.search_services(query, limit=limit) if query else store.list_services(limit=limit)
    if not svcs:
        return ""
    lines = []
    for i, s in enumerate(svcs, 1):
        lines.append(f"[{i}] {s['name']} ({s.get('category','')}, {s.get('tier','')}): "
                     f"{s.get('description','')} [service:{s['id']}]")
    return "\n".join(lines)
