#!/usr/bin/env python3.12
"""Compare retrievers (fulltext vs vector vs hybrid) on the same ground truth.

Runs each retriever through the full eval suite (agent answer + Grok judge),
writes one per-retriever results CSV plus a side-by-side comparison CSV, and
prints a summary table. Judge verdicts are cached by (question, answer) so
identical answers across retrievers aren't re-judged.

Usage:
    python3.12 evals/compare_retrievers.py
    python3.12 evals/compare_retrievers.py --retrievers fulltext,hybrid
    python3.12 evals/compare_retrievers.py path/to/gt.csv
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

import run_evals as R  # noqa: E402


def main():
    args = list(sys.argv[1:])
    retrievers = ["fulltext", "vector", "hybrid"]
    if "--retrievers" in args:
        i = args.index("--retrievers")
        retrievers = [x.strip() for x in args[i + 1].split(",")]
        del args[i:i + 2]
    gt_path = Path(args[0]) if args else ROOT / "evals" / "ground_truth.csv"

    rows = list(csv.DictReader(gt_path.open()))
    if not rows:
        sys.exit(f"No ground-truth rows in {gt_path}")
    if not R.taxai.ai_available():
        sys.exit("XAI_API_KEY not set — judge and agents need Grok. Check .env.")

    metric = R.build_metric(R.build_judge())
    cache: dict = {}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    by_retriever = {}
    for name in retrievers:
        print(f"\n=== retriever: {name} ===")
        results = R.run_suite(rows, name, metric, verdict_cache=cache)
        R.write_results(results, name, stamp)
        by_retriever[name] = results

    # Side-by-side comparison CSV.
    out_dir = ROOT / "eval-results"
    cmp_path = out_dir / f"compare_{stamp}.csv"
    cols = ["question", "expected_answer"]
    for name in retrievers:
        cols += [f"{name}_status", f"{name}_score"]
    with cmp_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for idx, row in enumerate(rows):
            rec = {"question": row["question"], "expected_answer": row["expected_answer"]}
            for name in retrievers:
                rr = by_retriever[name][idx]
                rec[f"{name}_status"] = rr["status"]
                rec[f"{name}_score"] = rr["score"]
            w.writerow(rec)

    # Summary table.
    n = len(rows)
    print(f"\n{'='*64}\nCOMPARISON ({n} questions, threshold={R.THRESHOLD})\n{'='*64}")
    print(f"{'retriever':<12} {'PASS':>5} {'FAIL':>5} {'ERR':>4} {'pass rate':>10} {'avg score':>10}")
    for name in retrievers:
        rs = by_retriever[name]
        p = sum(1 for r in rs if r["status"] == "PASS")
        fa = sum(1 for r in rs if r["status"] == "FAIL")
        er = sum(1 for r in rs if r["status"] == "ERROR")
        avg = sum(r["score"] for r in rs) / n
        print(f"{name:<12} {p:>5} {fa:>5} {er:>4} {p/n:>9.0%} {avg:>10.3f}")
    print(f"\nComparison CSV: {cmp_path.relative_to(ROOT)}")
    print(f"Per-retriever CSVs: eval-results/eval_<retriever>_{stamp}.csv")


if __name__ == "__main__":
    main()
