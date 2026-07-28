"""The orchestrator — a LangGraph tool-calling agent that plays the FastFund Private
Office relationship manager.

It routes a user message to the right specialist (profile_agent to ground in the
client, needs_agent to detect gaps, services_agent to explain offerings,
recommend_agent to produce ranked cross/upsell, benchmark_agent for industry
context), then composes a warm, advisory reply. Streaming uses LangGraph
``astream_events`` so the UI shows tokens *and* which agent is working.

Without an LLM key it degrades to a deterministic advisor: it runs the rule-based
recommender directly so the demo still produces value.
"""
from __future__ import annotations

from family.rag import llm
from family.agents import sse
from family.agents.tools import ALL_TOOLS

SYSTEM = (
    "You are FastFund — an AI relationship manager for FastFund's Private Office, "
    "advising principals and advisors of single family offices (SFOs). Your goal is "
    "to understand the family's needs through natural conversation and surface "
    "personalised, well-reasoned cross-sell and upsell recommendations — never "
    "pushy, always advisory and grounded in the client's actual profile.\n\n"
    "Route to your specialist tools:\n"
    "- profile_agent: ground in WHO the client is (AUM, family, asset mix, services, pains). "
    "Call this first when advising on a specific family.\n"
    "- needs_agent: detect GAPS from a described setup and map to FastFund service categories.\n"
    "- services_agent: explain what FastFund offers for a topic.\n"
    "- recommend_agent: produce RANKED cross/upsell recommendations with rationale and value.\n"
    "- benchmark_agent: aggregate industry context to frame advice with public data.\n"
    "- data_agent: QUANTITATIVE questions across the whole book (counts, totals, "
    "averages, rankings) answered via text-to-SQL — e.g. 'how many clients have AUM "
    "over $1bn', 'total pipeline value', 'most-recommended service'.\n\n"
    "Always keep the [service:N], [sfo:N] and [rec:N] markers from tool output in "
    "your answer so the UI can open them. Be concise, warm, and specific to this "
    "family. When you recommend a service, say WHY it fits them and what the next "
    "step is (a proposal, a consultation). If nothing fits, say so honestly."
)

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        from langgraph.prebuilt import create_react_agent
        _agent = create_react_agent(llm.get_llm(), ALL_TOOLS, prompt=SYSTEM)
    return _agent


def answer(question: str) -> dict:
    """Non-streaming answer (used by tests / fallback)."""
    if not llm.ai_available():
        from family.agents.context import active_sfo
        from family.engine import crosssell
        sfo_id = active_sfo()
        if sfo_id is not None:
            recs = crosssell.recommend(sfo_id, persist=True, use_ai=False)
            body = "\n".join(f"- {r['service_name']} ({r['kind']}, fit {r['score']:.0%}): "
                             f"{r['rationale']} [service:{r['service_id']}]" for r in recs[:6])
            return {"answer": "AI synthesis unavailable; rule-based recommendations:\n"
                    + (body or "none"), "tools": []}
        return {"answer": "AI is unavailable (no LLM key). Open a family office to "
                "get rule-based recommendations.", "tools": []}
    result = get_agent().invoke({"messages": [("user", question)]})
    msg = result["messages"][-1]
    tools = [m.name for m in result["messages"] if getattr(m, "type", "") == "tool"]
    return {"answer": getattr(msg, "content", str(msg)), "tools": tools}


async def astream(question: str, history: list | None = None):
    """Yield SSE strings: token / tool_start / tool_end / done."""
    if not llm.ai_available():
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
                # Skip tokens from tool-internal LLM calls (recommend re-rank,
                # proposal drafting, text-to-SQL) — they are tagged 'nested_llm'
                # and must not leak into the user-visible chat stream.
                if "nested_llm" in (ev.get("tags") or []):
                    continue
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
