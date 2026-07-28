"""Specialist agents, exposed as tools the orchestrator can call.

Each "agent" is a focused capability of the FastFund Family Office advisor over the
FastFund graph. The orchestrator (a LangGraph tool-calling agent) routes the
user's request to the right one and composes the final answer. Tools return
markdown the model can cite; the UI parses ``[service:<id>]`` / ``[sfo:<id>]`` /
``[rec:<id>]`` markers to render open-in-panel links.
"""
from __future__ import annotations

from langchain_core.tools import tool

from family import sfostore as store
from family.agents.context import active_sfo
from family.engine import crosssell
from family.rag import knowledge

CATEGORY_LABELS = {
    "structuring": "Trusts & structuring", "fund": "Fund administration",
    "luxury": "Luxury asset administration", "reporting": "Reporting & Edge",
    "governance": "Family governance", "banking": "Banking & treasury",
    "compliance": "Regulatory & compliance", "advisory": "Advisory",
}


def _profile_blob(sfo: dict) -> str:
    mix = ", ".join(f"{k.replace('_',' ')} {v}%" for k, v in (sfo.get("asset_mix") or {}).items())
    return (
        f"**{sfo['name']}** (ref {sfo.get('client_ref','—')}) — stage: {sfo.get('stage')}.\n"
        f"- AUM: ${(sfo.get('aum_usd') or 0)/1e6:,.0f}M · family {sfo.get('family_size','—')} "
        f"across {sfo.get('generations','—')} generation(s) · domicile {sfo.get('domicile','—')}\n"
        f"- Current FastFund services: {', '.join(sfo.get('current_services') or []) or 'none'}\n"
        f"- Asset mix: {mix or '—'}\n"
        f"- Pain points: {', '.join(sfo.get('pain_points') or []) or '—'} [sfo:{sfo['id']}]")


@tool
def profile_agent(name: str = "") -> str:
    """Look up the profile of a family office (SFO) — AUM, family/generations,
    asset mix, current FastFund services and stated pain points. Pass a name/ref
    substring, or leave blank to use the family currently open in the workspace.
    Use this first to ground any advice in who the client actually is."""
    sfo = None
    if name:
        hits = store.search_sfos(name, limit=1)
        sfo = hits[0] if hits else None
    if sfo is None and active_sfo() is not None:
        sfo = store.get_sfo(active_sfo())
    if sfo is None:
        return "No family office selected. Open one from the left panel, or name it."
    return _profile_blob(sfo)


@tool
def needs_agent(situation: str) -> str:
    """Analyse a described situation or governance/portfolio setup and surface the
    GAPS plus the FastFund service CATEGORIES that address them. Use when the principal
    describes their setup ("we have trusts but no consolidated reporting", "tell me
    about our governance"). Returns the gaps and which services to explore."""
    svc_ctx = knowledge.services_context(situation, limit=6)
    bench = knowledge.search_benchmarks(situation, limit=2)
    out = ["Relevant FastFund services for that situation:", svc_ctx or "(none matched)"]
    if bench:
        out.append("\nIndustry context:")
        out += [f"- {b['text']}" for b in bench]
    out.append("\nUse recommend_agent to turn this into ranked, costed next steps for the client.")
    return "\n".join(out)


@tool
def services_agent(query: str = "") -> str:
    """Describe FastFund Family Office SERVICES that match a query (e.g. "luxury yacht
    administration", "consolidated reporting", "trust structuring"). Use to explain
    what FastFund offers. Returns services with category, tier and an open marker."""
    svcs = store.search_services(query, limit=8) if query else store.list_services(limit=12)
    if not svcs:
        return "No matching FastFund service found."
    return "\n".join(
        f"- **{s['name']}** ({CATEGORY_LABELS.get(s.get('category'), s.get('category',''))}, "
        f"{s.get('tier')}) — {s.get('description','')} [service:{s['id']}]" for s in svcs)


@tool
def recommend_agent(name: str = "") -> str:
    """Generate ranked CROSS-SELL / UPSELL recommendations for a family office,
    each with a rationale and an estimated annual value. Use when the user wants
    "what should we offer them", "next best service", or "opportunities for this
    client". Uses the family currently open in the workspace unless a name is given."""
    sfo_id = active_sfo()
    if name:
        hits = store.search_sfos(name, limit=1)
        if hits:
            sfo_id = hits[0]["id"]
    if sfo_id is None:
        return "No family office selected. Open one from the left panel, or name it."
    recs = crosssell.recommend(sfo_id, persist=True, use_ai=True)
    if not recs:
        return "No cross/upsell opportunities surfaced — the client already holds the natural next services."
    lines = ["Recommended next services (ranked):"]
    for r in recs[:8]:
        kind = "UPSELL" if r["kind"] == "upsell" else "CROSS-SELL"
        lines.append(
            f"- **{r['service_name']}** · {kind} · fit {r['score']:.0%} · "
            f"~${r['est_value_usd']/1e3:,.0f}k/yr [service:{r['service_id']}]\n"
            f"  {r['rationale']}")
    return "\n".join(lines)


@tool
def benchmark_agent(topic: str = "") -> str:
    """Return aggregate INDUSTRY BENCHMARKS for SFOs (allocations, governance,
    reporting, luxury-asset trends) to frame advice with public data. Use for
    "how do families typically allocate", "what's the governance norm"."""
    bench = knowledge.search_benchmarks(topic, limit=4)
    return "\n".join(f"- {b['text']}" for b in bench)


@tool
def data_agent(question: str) -> str:
    """Answer a QUANTITATIVE question about the whole book via text-to-SQL over the
    FastFund database. Use for counts, totals, averages, rankings and aggregates
    across all clients — e.g. "how many family offices have AUM over $1bn",
    "total pipeline value", "which service is recommended most", "average private
    equity allocation", "how many clients vs leads". For a single named family use
    profile_agent instead."""
    from family.rag.text2sql import ask_data
    return ask_data(question)


ALL_TOOLS = [profile_agent, needs_agent, services_agent, recommend_agent,
             benchmark_agent, data_agent]
