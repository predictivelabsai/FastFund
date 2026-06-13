"""The orchestrator — a LangGraph tool-calling agent over the specialist agents.

It routes a user request to the right specialist (document_agent for finding
forms, law_agent for tax-law Q&A, metadata_agent for structured lookups,
changes_agent for recent changes), then composes a cited answer. Streaming uses
LangGraph ``astream_events`` so the UI shows tokens *and* which agent is working.
"""
from __future__ import annotations

from datetime import date

from rag import llm as taxai
from agents import sse
from agents.tools import ALL_TOOLS

SYSTEM = (
    f"Today's date is {date.today().isoformat()}. "
    "You are TaxHub's orchestrator for a fund-management back office. Your PRIMARY "
    "job is to help the user find the CORRECT TAX FORM to file, and secondarily to "
    "answer tax-law questions with traceable sources.\n\n"
    "Route to your specialist tools:\n"
    "- document_agent: find the right tax FORM(s) for a need.\n"
    "- law_agent: tax-LAW questions (definitions, economic substance, requirements).\n"
    "- metadata_agent: list forms / deadlines for a jurisdiction or category.\n"
    "- changes_agent: recent changes to tracked documents.\n"
    "- entity_agent: what the administered ENTITIES owe — their obligations, due "
    "dates, file-status and human-verification. Use whenever the question names a "
    "fund/SPV or asks who owes what, what's overdue, or what's due before a date. "
    "For 'overdue/late' pass urgency='overdue'; for relative windows ('this quarter', "
    "'this year', 'by 30 Sep') compute an ISO due_before from today's date.\n\n"
    "Prefer document_agent when the user needs a form, and entity_agent for anything "
    "about a specific entity or the portfolio's deadlines/compliance. Always keep the "
    "[form:N] and [doc:N] markers from tool output in your answer so the UI can open "
    "them, and preserve any [name](/entity/N) entity links. When you report an "
    "obligation, state whether it is human-verified (✓) or still awaiting sign-off "
    "(⚠), as entity_agent reports. Be concise, factual, and cite. If nothing matches, "
    "say so plainly."
)

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        from langgraph.prebuilt import create_react_agent
        _agent = create_react_agent(taxai.get_llm(), ALL_TOOLS, prompt=SYSTEM)
    return _agent


def answer(question: str) -> dict:
    """Non-streaming answer (used by tests / the /ask fallback)."""
    if not taxai.ai_available():
        # No LLM: degrade to a direct form search so the app is still useful.
        import taxstore as store
        forms = store.search_forms(question, limit=5)
        if forms:
            body = "\n".join(f"- {f['title']} ({f['jurisdiction_code']}) [form:{f['id']}]"
                             for f in forms)
            return {"answer": "AI is unavailable; closest tax forms:\n" + body, "tools": []}
        return {"answer": "AI synthesis unavailable (no XAI_API_KEY).", "tools": []}
    result = get_agent().invoke({"messages": [("user", question)]})
    msg = result["messages"][-1]
    tools = [m.name for m in result["messages"] if getattr(m, "type", "") == "tool"]
    return {"answer": getattr(msg, "content", str(msg)), "tools": tools}


async def astream(question: str, history: list | None = None):
    """Yield SSE strings: token / tool_start / tool_end / done."""
    if not taxai.ai_available():
        res = answer(question)
        yield sse.event(sse.TOKEN, {"text": res["answer"]})
        yield sse.event(sse.DONE, {"tools": 0})
        return
    msgs = list(history or []) + [("user", question)]
    tool_count = 0
    try:
        async for ev in get_agent().astream_events({"messages": msgs}, version="v2"):
            kind = ev["event"]
            if kind == "on_chat_model_stream":
                chunk = ev["data"].get("chunk")
                text = getattr(chunk, "content", "") if chunk else ""
                if text:
                    yield sse.event(sse.TOKEN, {"text": text})
            elif kind == "on_tool_start":
                tool_count += 1
                yield sse.event(sse.TOOL_START, {"name": ev.get("name", "agent")})
            elif kind == "on_tool_end":
                out = ev["data"].get("output", "")
                out = getattr(out, "content", None) or (out if isinstance(out, str) else str(out))
                yield sse.event(sse.TOOL_END, {"name": ev.get("name", "agent"),
                                               "output": out[:400]})
    except Exception as e:  # noqa: BLE001
        yield sse.event(sse.ERROR, {"message": str(e)})
    yield sse.event(sse.DONE, {"tools": tool_count})
