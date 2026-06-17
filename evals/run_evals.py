#!/usr/bin/env python3
"""SFO Hub text-to-SQL evaluation harness.

Runs each ground-truth question through the text-to-SQL engine
(`rag.text2sql.ask_data`) and scores the answer against the expected output with a
deepeval GEval correctness metric judged by xAI Grok. Mirrors the sister
`ai-marketing` harness, but SQL-only (the live backend is SQLite/Postgres, no
Cypher).

Inputs:  evals/ground_truth.csv   (question, expected_answer, category)
Outputs: evals/eval-results-{timestamp}.csv
         columns: question, category, expected_answer, ai_answer, sql, result, score, reason

Prereqs:
    pip install -r evals/requirements.txt
    XAI_API_KEY set (engine + judge)
    A seeded DB matching the ground truth:
        DB_URL=sqlite:///sfohub.db python -c "from data import synth; synth.run_seed(100,42)"

Usage:
    DB_URL=sqlite:///sfohub.db python evals/run_evals.py
    python evals/run_evals.py --category funnel       # one category
    python evals/run_evals.py --limit 5               # first N
    python evals/run_evals.py --dry-run               # list cases, no LLM calls
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_DISABLE_PROGRESS_BAR", "YES")

ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

THRESHOLD = 0.5


# ── Agents under test ───────────────────────────────────────────────────────────
def run_text_to_sql(question: str) -> str:
    """The text-to-SQL engine in isolation."""
    from rag.text2sql import ask_data
    try:
        return ask_data(question)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"


def run_assistant(question: str) -> str:
    """The ACTUAL AI assistant — the LangGraph orchestrator with all tools. Tests
    the real product path (routing → data_agent → text-to-SQL → synthesis)."""
    from agents import orchestrator
    from agents.context import set_active_sfo
    set_active_sfo(None)  # book-wide questions, no single SFO in context
    try:
        return orchestrator.answer(question).get("answer", "")
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"


AGENT_RUNNERS = {"sql": run_text_to_sql, "assistant": run_assistant}


# ── GrokJudge — deepeval LLM wrapper for xAI Grok ───────────────────────────────
def build_judge():
    from deepeval.models.base_model import DeepEvalBaseLLM

    api_key = os.getenv("XAI_API_KEY", "")
    model = os.getenv("LLM_JUDGE_MODEL", "grok-3-mini")
    if not api_key:
        raise RuntimeError("XAI_API_KEY not set — the judge needs Grok.")

    class GrokJudge(DeepEvalBaseLLM):
        def __init__(self):
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1",
                                  timeout=90.0, max_retries=2)
            self._model = model

        def load_model(self):
            return self._client

        def generate(self, prompt: str, schema=None):
            def _call(extra=""):
                resp = self._client.chat.completions.create(
                    model=self._model, temperature=0, max_tokens=2000,
                    messages=[{"role": "user", "content": prompt + extra}])
                return resp.choices[0].message.content.strip()
            if schema is not None:
                try:
                    raw = _call()
                    return schema(**json.loads(raw[raw.find("{"):raw.rfind("}") + 1]))
                except Exception:
                    raw = _call("\n\nRespond ONLY with valid JSON for the schema.")
                    return schema(**json.loads(raw[raw.find("{"):raw.rfind("}") + 1]))
            return _call()

        async def a_generate(self, prompt: str, schema=None):
            return self.generate(prompt, schema)

        def get_model_name(self):
            return f"grok ({self._model})"

    return GrokJudge()


def build_metric(judge):
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCaseParams
    return GEval(
        name="Correctness",
        criteria=(
            "Determine whether the 'actual output' is factually correct and "
            "consistent with the 'expected output' for the given SFO Hub data "
            "question. Reward answers that capture the key facts of the expected "
            "output even if worded or formatted differently, or include extra "
            "detail. For count questions the number must be within 10% of the "
            "expected value to score highly. For money questions (AUM, pipeline "
            "value) the figure must be within 10%. For lookup/list questions the "
            "key names must match. Penalise contradictions, wrong numbers, empty "
            "results when data was expected, or hallucinated data."),
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT,
                           LLMTestCaseParams.EXPECTED_OUTPUT],
        model=judge, threshold=THRESHOLD)


def judge_verdict(metric, question, expected, ai_answer):
    from deepeval.test_case import LLMTestCase
    try:
        tc = LLMTestCase(input=question, actual_output=ai_answer, expected_output=expected)
        metric.measure(tc)
        score = round(float(metric.score or 0.0), 3)
        return ("PASS" if score >= THRESHOLD else "FAIL"), score, (metric.reason or "").replace("\n", " ").strip()
    except Exception as e:  # noqa: BLE001
        return "ERROR", 0.0, f"{type(e).__name__}: {e}"


def load_ground_truth(path, category=None, limit=None):
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if category:
        rows = [r for r in rows if r["category"] == category]
    if limit:
        rows = rows[:limit]
    if not rows:
        sys.exit(f"No ground-truth rows in {path} (category={category})")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["assistant", "sql"], default="assistant",
                    help="'assistant' = full orchestrator (default); 'sql' = text-to-SQL engine only")
    ap.add_argument("--category", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    runner = AGENT_RUNNERS[args.agent]

    rows = load_ground_truth(EVALS_DIR / "ground_truth.csv",
                             args.category or None, args.limit or None)
    print(f"Loaded {len(rows)} eval cases.")
    if args.dry_run:
        for r in rows:
            print(f"  [{r['category']}] {r['question']}  →  {r['expected_answer']}")
        return

    print(f"Agent under test: {args.agent}")
    metric = build_metric(build_judge())
    results, passed = [], 0
    for i, row in enumerate(rows, 1):
        q, expected, cat = row["question"], row["expected_answer"], row["category"]
        ai = runner(q)
        result, score, reason = judge_verdict(metric, q, expected, ai)
        passed += result == "PASS"
        print(f"[{i}/{len(rows)}] {result} ({score}) — {q}")
        if result != "PASS":
            print(f"      expected: {expected}\n      got:      {ai[:300]}")
        results.append({"question": q, "category": cat, "agent": args.agent,
                        "expected_answer": expected, "ai_answer": ai,
                        "result": result, "score": score, "reason": reason})

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = EVALS_DIR / f"eval-results-{args.agent}-{ts}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["question", "category", "agent", "expected_answer",
                                          "ai_answer", "result", "score", "reason"])
        w.writeheader()
        w.writerows(results)

    rate = passed / len(results) * 100 if results else 0
    print(f"\n{'='*60}\nAgent {args.agent}: PASS {passed}/{len(results)} ({rate:.0f}%)  ·  → {out.name}")


if __name__ == "__main__":
    main()
