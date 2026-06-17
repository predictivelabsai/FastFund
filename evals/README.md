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

By default the harness runs each question through the **actual AI assistant** (the
full LangGraph orchestrator — routing → `data_agent` → text-to-SQL → synthesis),
so evals exercise the real product path, not just the engine in isolation:

```bash
python evals/run_evals.py --agent assistant   # default — the real assistant
python evals/run_evals.py --agent sql          # the text-to-SQL engine alone
```

`--category <name>`, `--limit N`, `--dry-run` are also supported.

## Files

- `ground_truth.csv` — question, expected_answer, category (33 cases).
- `verify_data.py` — recomputes the expected values from the seeded DB (run after
  changing the seeder to refresh ground truth).
- `run_evals.py` — the harness; writes `eval-results-{agent}-<ts>.csv` (gitignored).

Last full runs: **assistant 33/33 PASS (100%)**, **sql 33/33 PASS (100%)**.
Also verified end-to-end against the live chat UI via Playwright.
