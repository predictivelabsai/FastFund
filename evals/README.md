# FastFund Evals

LLM-judged evaluation of FastFund's agents against a ground-truth set, using the
[deepeval](https://github.com/confident-ai/deepeval) framework.

## What it does

For each ground-truth row, the runner executes the named **agent** to produce an
answer, then a deepeval **`GEval` correctness metric — judged by xAI Grok** —
scores that answer against the expected answer. `status = PASS` if the judge
score ≥ `THRESHOLD` (0.5), else `FAIL`.

The agents query the live storage backend (AuraDB when `DATA_STORAGE=neo4j`) and
Grok through the *same code path as the deployed `/ask` route* — so this is an
end-to-end eval of the real system, not a mock.

## Files

| Path | Purpose |
|------|---------|
| `evals/ground_truth.csv` | Questions: `id, question, expected_answer, agent_type` |
| `evals/run_evals.py` | Runner (agent → Grok judge → PASS/FAIL) |
| `eval-results/eval_<UTC>.csv` | One file per run: `question, expected_answer, ai_answer, agent_type, status, score, reason` |

`agent_type` selects which agent answers (`graph_rag` = the `/ask` graph-RAG
agent). Add new agents to the `AGENTS` dict in `run_evals.py`.

## Run

```bash
python3.12 evals/run_evals.py                 # uses evals/ground_truth.csv
python3.12 evals/run_evals.py path/to/gt.csv  # custom ground-truth file
```

Requires `XAI_API_KEY` (judge + agents use Grok) and a reachable backend — both
read from `.env`. Install deps once:
`pip install --user --break-system-packages deepeval`.

## Interpreting results

The judge is tolerant of wording — it rewards answers that capture the expected
key facts and penalises contradictions, missing facts, or hallucinated law.

A `FAIL` is usually one of:
- **Retrieval gap** — the agent answered *"the provided context does not contain…"*
  because the full-text seed (k=6) didn't surface the passage, even when the
  corpus contains it. This is the dominant failure mode in the current run and is
  the prime candidate for improvement (higher `k`, query expansion, or vector
  embeddings behind the existing `Retriever` interface).
- **Partial answer** — some but not all expected key facts present.

Keep failing questions in the set: they track real agent capability over time.
Don't tune ground truth to inflate the pass rate — only fix questions that are
*mis-scoped* (e.g. asking a tax-document RAG about application features).
