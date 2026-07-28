"""Recommendation-quality eval harness.

Scores the deterministic rule engine (``engine.rules.fire`` + the cross-sell
graph) against a ground-truth set in ``evals/cases.yaml`` — the set of services a
FastFund relationship manager would expect for each profile. Reports per-case hits and
aggregate precision / recall / hit-rate, the SFO-domain analogue of FastFund's
retriever eval.

Usage:
    python -m evals.recommend_eval            # rules only (no DB, no AI needed)
    python -m evals.recommend_eval --graph    # also expand via the cross-sell graph (needs a seeded DB)
"""
from __future__ import annotations

import argparse
import os

import yaml

from engine import rules

_HERE = os.path.dirname(__file__)


def _graph_expand(profile, base_keys):
    """Optionally widen candidates via the seeded cross-sell graph."""
    import sfostore as store
    out = set(base_keys)
    for held in profile.get("current_services") or []:
        for partner in store.list_cross_sells(held):
            out.add(partner["key"])
    return out


def run(use_graph: bool = False) -> dict:
    cases = yaml.safe_load(open(os.path.join(_HERE, "cases.yaml")))["cases"]
    rows, tp, fp, fn = [], 0, 0, 0
    for c in cases:
        got = {r["service"] for r in rules.fire(c["profile"])}
        if use_graph:
            got = _graph_expand(c["profile"], got)
        expect = set(c["expect"])
        hits = got & expect
        missed = expect - got
        extra = got - expect
        tp += len(hits)
        fn += len(missed)
        fp += len(extra)
        rows.append({"name": c["name"], "hits": sorted(hits),
                     "missed": sorted(missed), "extra": sorted(extra),
                     "recall": len(hits) / len(expect) if expect else 1.0})
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    hit_rate = sum(1 for r in rows if not r["missed"]) / len(rows) if rows else 0.0
    return {"rows": rows, "precision": precision, "recall": recall,
            "hit_rate": hit_rate, "tp": tp, "fp": fp, "fn": fn}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", action="store_true", help="expand via cross-sell graph (needs seeded DB)")
    args = ap.parse_args()
    res = run(use_graph=args.graph)
    for r in res["rows"]:
        mark = "✓" if not r["missed"] else "✗"
        print(f"{mark} {r['name']}: recall {r['recall']:.0%}"
              f"  hits={r['hits']}" + (f"  MISSED={r['missed']}" if r["missed"] else ""))
    print(f"\nPrecision {res['precision']:.0%} · Recall {res['recall']:.0%} · "
          f"Full-hit cases {res['hit_rate']:.0%}  (tp={res['tp']} fp={res['fp']} fn={res['fn']})")


if __name__ == "__main__":
    main()
