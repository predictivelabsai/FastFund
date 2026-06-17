# JTC SFO Hub

An AI conversational advisor for **cross-selling and upselling to single family
offices (SFOs)** — a JTC Private Office relationship manager, simulated. It
engages principals and advisors in natural dialogue, analyses the family's
profile, and surfaces personalised, well-reasoned service recommendations (e.g.
trusts → tax reporting, PE holdings → fund admin, governance → next-gen
education + JTC Edge).

Built on the same stack and look-and-feel as its sister app
[TaxHub](../taxhub): **FastHTML** 3-pane agentic UI, a **LangGraph** orchestrator
over specialist agent tools, a backend-neutral **Storage** interface
(**Neo4j** graph default / **SQLite·Postgres**), an **OpenAI-compatible** LLM
(xAI Grok in dev → **Azure AI Foundry** in production). Dev-deployed on
Coolify/Cloudflare; the production target is Microsoft Azure.

> **Architecture & diagrams:** see [`docs/architecture_readme.md`](docs/architecture_readme.md)
> for component/data-flow/graph-model diagrams, the environment-variable
> reference, and the Azure deployment guide.
>
> **User guide:** [`docs/sfohub_user_guide.md`](docs/sfohub_user_guide.md).

## What it does

- **Conversational intake** — "Tell me about your family's governance setup" →
  detects gaps → maps them to JTC service categories.
- **Personalised insights** — grounds advice in the family's AUM, generations,
  asset mix, current services and pain points (plus aggregate industry
  benchmarks).
- **Hybrid cross/upsell engine** — a transparent **rule catalogue** + a
  **cross-sell graph** produce the candidate set; an **AI layer** re-ranks and
  rewrites each rationale in a relationship-manager voice. Every recommendation
  is traceable to the rule or graph edge that produced it.
- **Recommendations & next steps** — ranked services with an estimated annual
  value and a funnel status (suggested → presented → accepted → booked).
- **Analytics dashboard** — service-interest heatmap, upsell funnel, simulated
  pipeline value, clients by lifecycle stage.
- **Synthetic data** — privacy-compliant demo book of fictional family offices;
  no real client data is ever used.

## Architecture

```
storage/base.py            Storage interface (backend-neutral)
storage/sqlite_store.py      ├─ SQLite/Postgres backend (zero-infra)
storage/neo4j_store.py       └─ Neo4j graph backend (default)
storage/docstore.py        document blob store: local volume / Cloudflare R2 / Blob
sfostore.py                store facade → active backend (import sfostore as store)
data/services.yaml         JTC service catalogue + cross-sell graph
data/synth.py              synthetic generator: SFOs, members, conversations, docs, actions
engine/rules.py            transparent cross/upsell rule catalogue
engine/crosssell.py        hybrid engine: rules → graph expansion → AI re-rank + scheduling
engine/proposals.py        AI proposal-text generation per recommendation
rag/llm.py                 OpenAI-compatible LLM client (Grok → Azure Foundry)
rag/knowledge.py           services + industry-benchmark retrieval
agents/orchestrator.py     LangGraph tool-calling advisor (+ SSE streaming)
agents/tools.py            specialist agents: profile / needs / services / recommend / benchmark
web/monitor.py             next-action urgency + pipeline calendar roll-ups
web/coverage.py            SFO × service coverage matrix
web/graphdata.py           vis-network data (client book + cross-sell schema)
web/app.py                 FastHTML multi-page app (uvicorn web.app:app)
evals/                     recommendation-quality eval harness
```

The graph shape (Neo4j):

```
(:SFO)-[:HOLDS_SERVICE]->(:Service)
(:SFO)-[:RECOMMENDED {kind, score, status, proposal}]->(:Service)
(:SFO)-[:HAS_MEMBER]->(:Member)
(:SFO)-[:HAS_DOCUMENT]->(:Doc)
(:SFO)-[:HAS_ACTION]->(:Action)
(:SFO)-[:HAS_CONVERSATION]->(:Conversation)-[:HAS_MESSAGE]->(:Message)
(:Service)-[:CROSS_SELLS_TO {weight}]->(:Service)
```

`CROSS_SELLS_TO` is the cross-sell knowledge graph — first-class edges, the
direct analogue of TaxHub's citation graph.

## Quick start (zero-infra, SQLite)

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # DATA_STORAGE=sqlite is the demo default

python -m data.synth --count 80      # seed catalogue + 80 synthetic SFOs + funnel
python -m uvicorn web.app:app --port 5021
# open http://localhost:5021  (login admin@jtcgroup.com / change-me, or set SFOHUB_PUBLIC=1)
```

Without an LLM key the app still runs: the chat degrades to the rule-based
advisor and AI scoring is skipped, so the demo never hard-fails. Set
`XAI_API_KEY` (or the Azure Foundry vars) to enable the conversational agent.

## Local Neo4j (the default backend)

```bash
scripts/neo4j_local.sh setup && scripts/neo4j_local.sh start   # Neo4j on :7474/:7687
# set DATA_STORAGE=neo4j and NEO4J_* in .env, then:
python -m data.synth --count 80
```

## Usage (CLI)

```bash
python -m data.synth --services-only          # seed only the service catalogue + graph
python -m data.synth --count 120 --seed 7     # reproducible demo book
python sfostore.py                            # init schema + print stats
```

## Deploy

Docker / Coolify (port 5021). The `web` service serves the app; the `seed`
service (profile `tools`) populates demo data on the shared volume:

```bash
docker compose up -d web
docker compose run --rm seed --count 80
```

Production target is **Microsoft Azure** — Azure Container Apps + Azure AI
Foundry + Neo4j AuraDB. See [`docs/architecture_readme.md` §7](docs/architecture_readme.md).

## Testing

```bash
python -m pytest tests/ -q
```

`tests/test_storage.py` is one contract suite parametrised over **both** backends
so `DATA_STORAGE` can't silently change behaviour (the Neo4j case skips if no
Neo4j is reachable).

```bash
python -m evals.recommend_eval          # recommendation-quality eval (rules, no DB/AI needed)
python -m evals.recommend_eval --graph  # also expand via the cross-sell graph (needs seeded DB)
```