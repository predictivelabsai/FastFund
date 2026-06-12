#!/usr/bin/env python3.12
"""Run TaxHub agent evals with an LLM judge (deepeval).

For each ground-truth row we run the named agent — with a chosen retriever — to
produce an answer, then a deepeval ``GEval`` correctness metric (judged by xAI
Grok) scores it against the expected answer. status = PASS if score >= THRESHOLD.

Inputs:  evals/ground_truth.csv  (id, question, expected_answer, agent_type)
Outputs: eval-results/eval_<retriever>_<UTC>.csv
         columns: question, expected_answer, ai_answer, agent_type, status, score, reason

Usage:
    python3.12 evals/run_evals.py                       # retriever from RAG_RETRIEVER env (default hybrid)
    python3.12 evals/run_evals.py --retriever vector    # fulltext | vector | hybrid
    python3.12 evals/run_evals.py path/to/gt.csv --retriever fulltext

The agents query the live backend (AuraDB when DATA_STORAGE=neo4j) and Grok via
the same code path as the deployed /ask route — an end-to-end eval, not a mock.
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_DISABLE_PROGRESS_BAR", "YES")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag import llm as taxai  # noqa: E402
from rag import retrieval as taxrag  # noqa: E402

THRESHOLD = 0.5


# ── Agent under test ────────────────────────────────────────────────────────
def run_graph_rag(question: str, retriever_name: str) -> str:
    """The /ask graph-RAG agent with an explicit retriever."""
    return taxrag.answer(question, retriever=taxrag.get_retriever(retriever_name)).get("answer", "")


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
            "question. Reward answers that capture the key facts of the expected "
            "output even if worded differently or with extra detail; penalise "
            "contradictions, missing key facts, or hallucinated law."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
        model=judge,
        threshold=THRESHOLD,
    )


def judge_verdict(metric, question: str, expected: str, ai_answer: str):
    """Return (status, score, reason) for one answer."""
    from deepeval.test_case import LLMTestCase
    try:
        tc = LLMTestCase(input=question, actual_output=ai_answer, expected_output=expected)
        metric.measure(tc)
        score = round(float(metric.score or 0.0), 3)
        status = "PASS" if score >= THRESHOLD else "FAIL"
        return status, score, (metric.reason or "").replace("\n", " ").strip()
    except Exception as e:  # noqa: BLE001
        return "ERROR", 0.0, f"{type(e).__name__}: {e}"


def run_suite(rows, retriever_name, metric, verdict_cache=None):
    """Run every row through the agent (with retriever_name) + judge."""
    results = []
    for r in rows:
        q, expected = r["question"], r["expected_answer"]
        ai_answer = run_graph_rag(q, retriever_name)
        key = (q, ai_answer)
        if verdict_cache is not None and key in verdict_cache:
            status, score, reason = verdict_cache[key]
        else:
            status, score, reason = judge_verdict(metric, q, expected, ai_answer)
            if verdict_cache is not None:
                verdict_cache[key] = (status, score, reason)
        results.append({
            "question": q, "expected_answer": expected, "ai_answer": ai_answer,
            "agent_type": f"graph_rag:{retriever_name}",
            "status": status, "score": score, "reason": reason,
        })
        print(f"  [{status}] score={score:<5} {q[:60]}")
    return results


def write_results(results, retriever_name, stamp):
    out_dir = ROOT / "eval-results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"eval_{retriever_name}_{stamp}.csv"
    cols = ["question", "expected_answer", "ai_answer", "agent_type", "status", "score", "reason"]
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)
    return out_path


def main():
    args = [a for a in sys.argv[1:]]
    retriever = os.environ.get("RAG_RETRIEVER", "hybrid")
    if "--retriever" in args:
        i = args.index("--retriever")
        retriever = args[i + 1]
        del args[i:i + 2]
    gt_path = Path(args[0]) if args else ROOT / "evals" / "ground_truth.csv"

    rows = list(csv.DictReader(gt_path.open()))
    if not rows:
        sys.exit(f"No ground-truth rows in {gt_path}")
    if not taxai.ai_available():
        sys.exit("XAI_API_KEY not set — judge and agents need Grok. Check .env.")

    metric = build_metric(build_judge())
    print(f"Running {len(rows)} evals | retriever={retriever} | threshold={THRESHOLD}\n")
    results = run_suite(rows, retriever, metric)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = write_results(results, retriever, stamp)

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    errored = sum(1 for r in results if r["status"] == "ERROR")
    print(f"\n{'='*60}")
    print(f"retriever={retriever}  PASS {passed}/{len(results)}  FAIL {failed}  "
          f"ERROR {errored}  pass rate {passed/len(results):.0%}")
    print(f"Results: {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
