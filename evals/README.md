# SFO Hub — text-to-SQL evals

Scores the text-to-SQL engine (`rag/text2sql.ask_data`) against a ground-truth set
with a deepeval GEval correctness metric judged by xAI Grok. SQL-only — the live
backend is SQLite/Postgres (no Cypher). Mirrors the sister `ai-marketing` harness.

## Run

```bash
pip install -r evals/requirements.txt          # deepeval + openai (eval-only)
# seed the canonical dataset the ground truth was computed from (== production):
DB_URL=sqlite:///sfohub.db python -c "from data import synth; synth.run_seed(100,42)"
DB_URL=sqlite:///sfohub.db XAI_API_KEY=... python evals/run_evals.py
```

`--category <name>`, `--limit N`, `--dry-run` are supported.

## Files

- `ground_truth.csv` — question, expected_answer, category (33 cases).
- `verify_data.py` — recomputes the expected values from the seeded DB (run after
  changing the seeder to refresh ground truth).
- `run_evals.py` — the harness; writes `eval-results-<ts>.csv` (gitignored).

Last full run: **33/33 PASS (100%)**.
