"""LLM-as-judge for logged chat turns.

Complements the offline ground-truth evals in ``evals/`` (which score the agents
against a fixed CSV). This one scores *live* assistant replies that users actually
received — the question+answer pairs logged in chat — so the admin analytics page
can surface low-quality turns and an average quality score alongside the human
👍/👎 feedback. Reuses the same Grok client as the app ([[rag/llm]]).

``run_judge(store, ...)`` pulls un-judged turns, scores each, and writes the
verdict back via ``store.add_message_judge``. Safe to run repeatedly (only
un-judged turns are picked up) and callable from the admin page or a cron/CLI.
"""
from __future__ import annotations

import json

from rag import llm as taxai

JUDGE_SYSTEM = "You are a precise QA evaluator. Return valid JSON only — no prose, no fences."

JUDGE_PROMPT = """\
You are a strict QA evaluator for **FastFund**, an assistant for fund back-office tax
compliance: finding the right tax FORM, answering tax-LAW questions with citations,
and reporting entities' filing obligations, deadlines and FATCA/CRS readiness.

You are given one user QUESTION and the assistant's ANSWER. Score the answer.
Return valid JSON only.

Scoring (integers 1–5, 5 = excellent):
- relevance — does it address what was actually asked?
- groundedness — grounded in the cited forms/law/portfolio data; avoids inventing facts
- completeness — does it fully answer, with the key form names / dates / figures?
- score — overall holistic quality
- verdict — "good" (4–5), "fair" (3) or "poor" (1–2)

Return exactly:
{"score":1-5,"verdict":"good|fair|poor","relevance":1-5,"groundedness":1-5,"completeness":1-5,"reason":"one concise sentence"}

QUESTION:
{question}

ANSWER:
{answer}
"""


def _int(v, default=0):
    try:
        return max(1, min(5, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


def judge_turn(question: str, answer: str) -> dict | None:
    """Score one (question, answer) pair with the Grok judge. None on failure."""
    if not taxai.ai_available():
        return None
    from langchain_core.messages import HumanMessage, SystemMessage
    prompt = (JUDGE_PROMPT.replace("{question}", (question or "")[:4000])
                          .replace("{answer}", (answer or "")[:8000]))
    try:
        resp = taxai.get_llm().invoke(
            [SystemMessage(content=JUDGE_SYSTEM), HumanMessage(content=prompt)])
        content = (resp.content or "").strip()
        if content.startswith("```"):
            content = content.strip("`")
            content = content[content.find("{"):content.rfind("}") + 1]
        elif "{" in content:
            content = content[content.find("{"):content.rfind("}") + 1]
        d = json.loads(content)
    except Exception:  # noqa: BLE001
        return None
    return {"score": _int(d.get("score")), "verdict": str(d.get("verdict", ""))[:8],
            "relevance": _int(d.get("relevance")), "groundedness": _int(d.get("groundedness")),
            "completeness": _int(d.get("completeness")), "reason": str(d.get("reason", ""))[:400],
            "model": taxai.GROK_MODEL}


def run_judge(store, team_id=None, limit: int = 25) -> dict:
    """Judge up to ``limit`` un-judged turns; persist verdicts. Returns counts."""
    turns = store.list_chat_turns(team_id=team_id, limit=limit, needs_judge=True)
    judged = skipped = 0
    for t in turns:
        if not (t.get("answer") and t.get("question")):
            skipped += 1
            continue
        v = judge_turn(t["question"], t["answer"])
        if not v:
            skipped += 1
            continue
        store.add_message_judge(t["message_id"], v["score"], v["verdict"],
                                v["relevance"], v["groundedness"], v["completeness"],
                                v["reason"], v["model"])
        judged += 1
    return {"candidates": len(turns), "judged": judged, "skipped": skipped}


if __name__ == "__main__":  # CLI: python3.12 -m web.chat_eval [limit]
    import sys
    import taxstore as store
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    print(run_judge(store, limit=n))
