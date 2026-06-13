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

# How the form is filed — make this unambiguous in answers.
FILING_LABELS = {
    "downloadable": "Downloadable PDF form",
    "online": "Online filing (web portal / e-file — no PDF to complete)",
    "reference": "Reference / guidance (not a fileable form)",
}


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
        filing = FILING_LABELS.get(f.get("filing_type") or "downloadable", "—")
        lines.append(
            f"- **{f['title']}** ({f['jurisdiction_code']}, {f.get('category') or '—'}/"
            f"{f.get('form_type') or 'form'}) [form:{f['id']}]\n"
            f"  Filing type: {filing}. Who files: {f.get('who_files') or '—'}. "
            f"Deadline: {f.get('deadline') or '—'}. Frequency: {f.get('frequency') or '—'}.")
    return ("Matching tax forms (note the Filing type — some are downloadable PDFs, "
            "others are filed online):\n" + "\n".join(lines[1:])) if lines[1:] else lines[0]


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
    "AEOI forms". jurisdiction_code is a 2-letter code, e.g. JE GG LU IE KY VG
    (MVP) or IM MU MT CY NL CH GB DE AT PL US HK SG MY NZ AE BS BR ZA BM."""
    forms = store.list_forms(jurisdiction_code=jurisdiction_code or None,
                             category=category or None, limit=50)
    if not forms:
        return "No forms found for that filter."
    return "\n".join(
        f"- {f['jurisdiction_code']} · {f.get('category') or '—'} · {f['title']} "
        f"(due {f.get('deadline') or '—'}) [form:{f['id']}]" for f in forms)


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


def _resolve_entities(entity: str) -> list[dict]:
    """Entities whose name or client_ref contains ``entity`` (all if blank)."""
    ents = store.list_entities(limit=5000)
    if not entity:
        return ents
    q = entity.strip().lower()
    hits = [e for e in ents if q in (e.get("name") or "").lower()
            or q in (e.get("client_ref") or "").lower()]
    return hits or ents  # fall back to all if no name match, so the model still answers


@tool
def entity_agent(entity: str = "", category: str = "", status: str = "",
                 due_before: str = "", due_within_days: int = 0) -> str:
    """Answer questions about the FUND ENTITIES JTC administers and what they OWE —
    their filing obligations, computed DUE DATES, file-status, and whether each is
    human-VERIFIED. Use for: "what does Aurora owe this year?", "which entities have
    economic-substance due before 30 September?", "show overdue filings", "is the
    Helios Jersey return signed off yet?".

    All params optional:
    - entity: name or client_ref substring to focus on one entity (blank = whole book).
    - category: corporate_tax | economic_substance | aeoi | partnership | fund.
    - status: not_started | in_progress | prepared | filed | confirmed | na.
    - due_before: ISO date 'YYYY-MM-DD' — keep only obligations due on/before it
      (use this for "this quarter", "before 30 Sep", "by year-end").
    - due_within_days: keep obligations due within N days from today.

    Each obligation shows its resolved due date, urgency, status, and a ✓ (verified)
    or ⚠ (awaiting sign-off) flag. Carries [form:N] markers so the UI can open forms."""
    from datetime import date, timedelta
    from web import monitor
    today = date.today()
    ents = _resolve_entities(entity)
    if not ents:
        return "No entities found. Add entities under Navigate ▸ Entities (or CSV import)."

    cutoff = None
    if due_before:
        try:
            cutoff = date.fromisoformat(due_before.strip()[:10])
        except ValueError:
            cutoff = None
    if due_within_days and not cutoff:
        cutoff = today + timedelta(days=int(due_within_days))

    by_ent = {}
    for e in ents:
        rows = []
        for ob in store.list_obligations(entity_id=e["id"], limit=2000):
            if category and (ob.get("category") or "") != category:
                continue
            if status and (ob.get("status") or "not_started") != status:
                continue
            a = monitor.annotate(ob, e, today)
            if cutoff is not None:
                due = a.get("due")
                # Keep only dated obligations on/before the cutoff.
                if not due or date.fromisoformat(due) > cutoff:
                    continue
            rows.append(a)
        if rows:
            rows.sort(key=lambda r: (r["due"] is None, r["due"] or "9999"))
            by_ent[e["id"]] = (e, rows)

    if not by_ent:
        scope = f" for {ents[0]['name']}" if entity and len(ents) == 1 else ""
        filt = " matching that filter" if (category or status or cutoff) else ""
        return f"No obligations{scope}{filt}. (Run Determine on the entity if it has none yet.)"

    # No-filter, whole-book → a compact portfolio summary instead of every row.
    if not entity and not (category or status or cutoff):
        lines = ["Entities and their filing status:"]
        for e, rows in by_ent.values():
            filed = sum(1 for r in rows if (r.get("status") in ("filed", "confirmed")))
            ver = sum(1 for r in rows if r.get("verified"))
            jurs = ", ".join(e.get("jurisdictions") or []) or "—"
            lines.append(f"- **[{e['name']}](/entity/{e['id']})** ({jurs}) — "
                         f"{filed}/{len(rows)} filed, {ver} verified.")
        lines.append("\nAsk about a specific entity, category, or deadline window for detail.")
        return "\n".join(lines)

    def fmt(r):
        flag = "✓ verified" if r.get("verified") else "⚠ awaiting sign-off"
        due = r.get("due") or f"no fixed date ({r.get('due_basis')})"
        urg = monitor.URGENCY_LABEL.get(r.get("urgency"), r.get("urgency") or "")
        days = r.get("days_out")
        when = ""
        if days is not None:
            when = " — today" if days == 0 else (f" — {-days}d overdue" if days < 0 else f" — in {days}d")
        return (f"  - {r.get('title') or 'form'} [{r.get('jurisdiction_code')}] [form:{r['form_id']}] "
                f"· due {due}{when} · {urg} · {r.get('status') or 'not_started'} · {flag}")

    out = []
    for e, rows in by_ent.values():
        out.append(f"**[{e['name']}](/entity/{e['id']})** — {len(rows)} obligation(s):")
        out.extend(fmt(r) for r in rows[:40])
    return "\n".join(out)


ALL_TOOLS = [document_agent, law_agent, metadata_agent, changes_agent, entity_agent]
