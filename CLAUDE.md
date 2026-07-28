# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo.

## What this is

**SFO Hub** — an AI conversational advisor that cross-sells/upsells to single
family offices (SFOs), simulating a JTC Private Office relationship manager. It is
the sister app to **TaxHub** (`../taxhub`) and deliberately mirrors its stack,
look-and-feel, and operational model. When in doubt about a convention, check how
TaxHub does it.

## Stack

- **Python 3.12**, **FastHTML** single-app (3-pane UI), uvicorn, port **5021**.
- **LangGraph** `create_react_agent` orchestrator over specialist agent-tools; SSE streaming.
- Backend-neutral **`Storage`** interface: **Neo4j** graph (default) / **SQLite·Postgres**.
- OpenAI-compatible LLM: **xAI Grok** (dev) → **Azure AI Foundry** (prod); degrades gracefully with no key.
- bcrypt session auth; Docker + Coolify (dev) → Azure Container Apps + AuraDB (prod).

## Architecture map

```
storage/base.py            Storage ABC — the contract both backends implement
storage/sqlite_store.py    relational backend (zero-infra; DB_URL)
storage/neo4j_store.py     graph backend (default; AuraDB-portable)
sfostore.py                facade: `import sfostore as store` → active backend
data/services.yaml         JTC service catalogue + cross-sell graph (edit to extend services)
data/synth.py              synthetic SFO generator + seeder (Faker)
engine/rules.py            transparent cross/upsell rule catalogue (edit to add rules)
engine/crosssell.py        hybrid engine: rules → graph expansion → AI re-rank → persist
rag/llm.py                 LLM client (provider switch: xai | azure)
rag/knowledge.py           services + industry-benchmark retrieval (keyword today)
engine/proposals.py        AI proposal-text generation per recommendation
storage/docstore.py        document blob store: local volume / Cloudflare R2 / Azure Blob
agents/orchestrator.py     LangGraph advisor + SSE (SYSTEM persona here)
agents/tools.py            5 specialist agents (profile/needs/services/recommend/benchmark)
agents/context.py          contextvar carrying the open SFO id to the tools
web/monitor.py             next-action urgency + pipeline calendar roll-ups
web/coverage.py            SFO × service coverage matrix
web/graphdata.py           vis-network data (book + cross-sell schema)
web/app.py                 FastHTML multi-page app: shared Page() chrome, all routes/fragments
tests/test_storage.py      cross-backend contract suite
evals/                     recommendation-quality eval harness
docs/architecture_readme.md   architecture, Azure target, phased plan
```

Graph model: `(:SFO)` is the hub — `HOLDS_SERVICE`/`RECOMMENDED`→`(:Service)`,
`HAS_MEMBER`→`(:Member)`, `HAS_DOCUMENT`→`(:Doc)`, `HAS_ACTION`→`(:Action)`,
`HAS_CONVERSATION`→`(:Conversation)-[:HAS_MESSAGE]->(:Message)`; the cross-sell
graph is `(:Service)-[:CROSS_SELLS_TO {weight}]->(:Service)`.

## Core invariants (don't break these)

- **No caller touches a backend directly** — everything goes through `sfostore`/
  the `Storage` interface, so `DATA_STORAGE` stays a one-line switch. If you add a
  persistence method, add it to `storage/base.py` AND both backends AND
  `tests/test_storage.py`.
- **List/dict fields** (`jurisdictions`, `current_services`, `pain_points`,
  `asset_mix`, `keywords`) go in/out as Python lists/dicts on both backends.
  SQLite JSON-encodes them; Neo4j uses native arrays and JSON-encodes `asset_mix`.
- **Recommendation candidates are produced by rules + the cross-sell graph, never
  by the LLM alone.** The AI layer only re-ranks and rewrites rationales. Keep
  every rec traceable via its `source` (rule/graph/hybrid) and `rule_id`.
- **AI is optional.** Anything calling the LLM must degrade gracefully when
  `rag.llm.ai_available()` is false (see the orchestrator and `engine.crosssell`).
- **Stable ids:** Neo4j mints `uid` from a `(:Counter)` node — never use Neo4j's
  internal `id()`. DDL must stay version-tolerant (5.x `REQUIRE` → 4.x `ASSERT`).

## Run / test (zero-infra)

```bash
pip install -r requirements.txt
cp .env.example .env                       # DATA_STORAGE=sqlite for local
python -m data.synth --count 80           # seed catalogue + SFOs + funnel
python -m uvicorn web.app:app --port 5021 # SFOHUB_PUBLIC=1 to skip login
python -m pytest tests/ -q
```

See `SKILLS.md` for the full dev/test/deploy/Azure playbook.

## Conventions

- Match the surrounding code: terse module docstrings explaining the *why*, JTC
  brand palette in `web/app.py` CSS (`--navy #6b1766` / `--accent #ba2a84`), and
  the `[service:N]`/`[sfo:N]`/`[rec:N]` markers in tool output that the UI parses.
- All demo data is **synthetic** (`data/synth.py`). Never introduce real client data.
- **Always regenerate the user guide with a date stamp**: `scripts/render_user_guide.py`
  writes `docs/sfohub_user_guide_<YYYY-MM-DD>.pdf` and deletes the prior dated PDF
  (the app serves the newest at `/user-guide-pdf`). See `SKILLS.md §9`.
