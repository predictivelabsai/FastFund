#!/usr/bin/env python3.12
"""Run TaxHub agent evals with an LLM judge (deepeval).

For each ground-truth row we run the named agent to produce an answer, then a
deepeval ``GEval`` correctness metric — judged by xAI Grok — scores the answer
against the expected answer. status = PASS if score >= THRESHOLD else FAIL.

Inputs:  evals/ground_truth.csv  (id, question, expected_answer, agent_type)
Outputs: eval-results/eval_<UTC timestamp>.csv
         columns: question, expected_answer, ai_answer, agent_type, status, score, reason

Usage:   python3.12 evals/run_evals.py [path/to/ground_truth.csv]

The agents query the active storage backend (AuraDB when DATA_STORAGE=neo4j) and
Grok via the same code path as the deployed /ask route, so this is an
end-to-end eval of the real system, not a mock.
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Quieter, network-light deepeval.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_DISABLE_PROGRESS_BAR", "YES")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import taxai          # noqa: E402
import taxrag         # noqa: E402

THRESHOLD = 0.5


# ── Agents under test ───────────────────────────────────────────────────────
def run_graph_rag(question: str) -> str:
    """The /ask graph-RAG agent: full-text seed -> graph expansion -> Grok."""
    return taxrag.answer(question).get("answer", "")


AGENTS = {
    "graph_rag": run_graph_rag,
}


# ── Grok as the deepeval judge model ────────────────────────────────────────
def build_judge():
    from deepeval.models.base_model import DeepEvalBaseLLM

    class GrokJudge(DeepEvalBaseLLM):
        def __init__(self):
            self._llm = taxai.get_llm()

        def load_model(self):
            return self._llm

        def generate(self, prompt: str, schema=None):
            if schema is not None:
                # deepeval asks for structured (score/reason/steps) output.
                try:
                    return self._llm.with_structured_output(schema).invoke(prompt)
                except Exception:
                    import json
                    raw = self._llm.invoke(
                        prompt + "\n\nRespond ONLY with valid JSON for the schema."
                    ).content
                    raw = raw[raw.find("{"): raw.rfind("}") + 1]
                    return schema(**json.loads(raw))
            return self._llm.invoke(prompt).content

        async def a_generate(self, prompt: str, schema=None):
            return self.generate(prompt, schema)

        def get_model_name(self):
            return f"grok ({taxai.GROK_MODEL})"

    return GrokJudge()


def build_metric(judge):
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCaseParams

    return GEval(
        name="Correctness",
        criteria=(
            "Determine whether the 'actual output' is factually correct and "
            "consistent with the 'expected output' for the given tax-law "
            "question. Reward answers that capture the key facts of the "
            "expected output even if worded differently or with extra detail; "
            "penalise contradictions, missing key facts, or hallucinated law."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
        model=judge,
        threshold=THRESHOLD,
    )


def main():
    gt_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "evals" / "ground_truth.csv"
    rows = list(csv.DictReader(gt_path.open()))
    if not rows:
        sys.exit(f"No ground-truth rows in {gt_path}")

    if not taxai.ai_available():
        sys.exit("XAI_API_KEY not set — the judge and agents need Grok. Check .env.")

    from deepeval.test_case import LLMTestCase

    judge = build_judge()
    metric = build_metric(judge)

    results = []
    print(f"Running {len(rows)} evals (threshold={THRESHOLD})...\n")
    for r in rows:
        q, expected = r["question"], r["expected_answer"]
        agent_type = r.get("agent_type", "graph_rag")
        agent = AGENTS.get(agent_type)
        if agent is None:
            ai_answer, status, score, reason = "", "ERROR", 0.0, f"unknown agent_type {agent_type}"
        else:
            try:
                ai_answer = agent(q)
                tc = LLMTestCase(input=q, actual_output=ai_answer, expected_output=expected)
                metric.measure(tc)
                score = round(float(metric.score or 0.0), 3)
                status = "PASS" if score >= THRESHOLD else "FAIL"
                reason = (metric.reason or "").replace("\n", " ").strip()
            except Exception as e:  # noqa: BLE001
                ai_answer = locals().get("ai_answer", "")
                status, score, reason = "ERROR", 0.0, f"{type(e).__name__}: {e}"

        results.append({
            "question": q, "expected_answer": expected, "ai_answer": ai_answer,
            "agent_type": agent_type, "status": status, "score": score, "reason": reason,
        })
        print(f"  [{status}] score={score:<5} {q[:62]}")

    # Write results CSV.
    out_dir = ROOT / "eval-results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"eval_{stamp}.csv"
    cols = ["question", "expected_answer", "ai_answer", "agent_type", "status", "score", "reason"]
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    errored = sum(1 for r in results if r["status"] == "ERROR")
    print(f"\n{'='*60}")
    print(f"PASS {passed}/{len(results)}  |  FAIL {failed}  |  ERROR {errored}"
          f"  |  pass rate {passed/len(results):.0%}")
    print(f"Results: {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
