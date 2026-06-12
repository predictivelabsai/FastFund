"""Specialist agents, exposed as tools the orchestrator can call.

Each "agent" is a focused capability over the TaxHub graph. The orchestrator
(a LangGraph tool-calling agent) routes the user's request to the right one and
composes the final answer. Tools return markdown the model can cite; the UI also
parses ``[form:<id>]`` / ``[doc:<id>]`` markers to render open-in-viewer links.
"""
from __future__ import annotations

from langchain_core.tools import tool

import taxstore as store
from rag import retrieval as taxrag


@tool
def document_agent(need: str) -> str:
    """Find the correct TAX FORM(s) to file for a described need or situation.
    Use for questions like "which form do I file for Cayman economic substance"
    or "Jersey company tax return". Returns matching forms with who files them,
    deadlines, and an open marker."""
    forms = store.search_forms(need, limit=5)
    if not forms:
        return "No matching tax form found in the catalogue. Consider web_search_agent."
    lines = ["Matching tax forms:"]
    for f in forms:
        lines.append(
            f"- **{f['title']}** ({f['jurisdiction_code']}, {f.get('category','')}/"
            f"{f.get('form_type','')}) [form:{f['id']}]\n"
            f"  Who files: {f.get('who_files','—')}. Deadline: {f.get('deadline','—')}. "
            f"Frequency: {f.get('frequency','—')}.")
    return "\n".join(lines)


@tool
def law_agent(question: str) -> str:
    """Answer a tax-LAW question (legislation, guidance, economic substance,
    definitions) using graph-RAG over the tracked corpus with citations. Use for
    "what is CIGA", "what are the economic substance requirements", etc."""
    res = taxrag.answer(question)
    out = res.get("answer", "")
    srcs = res.get("sources") or []
    if srcs:
        out += "\n\nSources: " + "; ".join(
            f"[{i}] {s.get('title','')} ({s.get('jurisdiction_code','')}) [doc:{s.get('document_id')}]"
            for i, s in enumerate(srcs[:6], 1))
    return out


@tool
def metadata_agent(jurisdiction_code: str = "", category: str = "") -> str:
    """List the tax forms (and their deadlines) for a jurisdiction and/or
    category — a structured lookup. Use for "what forms are there for Cayman" or
    "AEOI forms". jurisdiction_code is a 2-letter code (JE, GG, LU, IE, KY, VG)."""
    forms = store.list_forms(jurisdiction_code=jurisdiction_code or None,
                             category=category or None, limit=50)
    if not forms:
        return "No forms found for that filter."
    return "\n".join(
        f"- {f['jurisdiction_code']} · {f.get('category','')} · {f['title']} "
        f"(due {f.get('deadline','—')}) [form:{f['id']}]" for f in forms)


@tool
def changes_agent(jurisdiction_code: str = "") -> str:
    """Report RECENT CHANGES to tracked tax documents (what moved lately). Use
    for "what changed recently" or "recent changes in Jersey"."""
    changes = store.recent_changes(15, jurisdiction_code=jurisdiction_code or None)
    if not changes:
        return "No recent changes recorded."
    out = []
    for c in changes:
        line = (f"- [{c.get('detected_at','')[:10]}] {c['jurisdiction_code']} "
                f"{c['change_type'].upper()}: {c['title']} [doc:{c.get('document_id')}]")
        if c.get("ai_summary"):
            line += f"\n  {c['ai_summary']}"
        out.append(line)
    return "\n".join(out)


ALL_TOOLS = [document_agent, law_agent, metadata_agent, changes_agent]
