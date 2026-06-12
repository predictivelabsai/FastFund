# JTC TaxHub

Tax-law traceability for a fund-management back office. TaxHub scrapes official
tax documents across the jurisdictions JTC Group operates in, captures **every
version**, detects and explains **every change**, and traces guidance back to
the **primary law** it interprets.

Deployed at **taxhub.predictivelabs.ai**.

> **Architecture & diagrams:** see [`docs/architecture_readme.md`](docs/architecture_readme.md)
> for component/data-flow/graph-model Mermaid diagrams, the full environment-variable
> reference, and AuraDB deployment steps.

## What it does

- **Scrapes** legislation, guidance/circulars, gazettes and treaties from
  official government sources (config-driven, one entry per document).
- **Versions** every capture immutably — the audit trail of how the law changed.
- **Diffs** consecutive versions and uses **xAI Grok** to summarise, in plain
  English, *what changed* and *the back-office impact*.
- **Traces citations** — links each guidance note to the statutory provisions
  it cites (e.g. "Article 123A of the Income Tax (Jersey) Law 1961").
- **Answers questions** — a graph-RAG agent ("Ask the law") retrieves over the
  citation/change graph and answers, in plain English, with cited sources.

## Jurisdictions (MVP)

| Code | Jurisdiction | Primary sources |
|------|--------------|-----------------|
| JE | Jersey | jerseylaw.je (consolidated PDFs), gov.je (Revenue Jersey) |
| GG | Guernsey | gov.gg Revenue Service, guernseylegalresources.gg |
| LU | Luxembourg | impotsdirects.public.lu, legilux.public.lu |
| IE | Ireland | revenue.ie (TDMs), irishstatutebook.ie |
| KY | Cayman Islands | ditc.ky, legislation.gov.ky |
| VG | British Virgin Islands | bviita.vg, bvi.gov.vg |

## Architecture

```
config/tax_sources.yaml      document catalogue (per jurisdiction → source → doc)
ingest/fetch.py              fetch (http/browser) + extract (html/pdf) + hash
ingest/cli.py                orchestrator CLI  (python -m ingest.cli)
storage/base.py              Storage interface (backend-neutral)
storage/sqlite_store.py        ├─ SQLite/Postgres backend  (+ brute-force vectors)
storage/neo4j_store.py         └─ Neo4j graph backend      (+ native vector index)
taxstore.py                  store facade → active backend (import taxstore as store)
rag/llm.py                   Grok change summaries + citation extraction
rag/embeddings.py            chunking + local embeddings (fastembed) for vectors
rag/retrieval.py             graph-RAG retrievers: fulltext / vector / hybrid + Q&A
web/app.py                   FastHTML web viewer (+ /ask)   (uvicorn web.app:app)
scripts/embed_backfill.py    chunk + embed every current version (enables vectors)
scripts/migrate_sqlite_to_neo4j.py   backfill the graph from SQLite
scripts/neo4j_local.sh       run a local, user-owned Neo4j for dev
```

The scraper hashes the *normalised text* of each document; when the hash changes,
a new immutable version is appended and a change record (with diff + AI summary)
is written.

### Storage backends (`DATA_STORAGE`)

No caller touches a database directly — everything goes through the `Storage`
interface, selected at runtime:

- **`neo4j`** (default) — graph backend. The data is naturally graph-shaped:

  ```
  (:Jurisdiction)-[:HAS_DOCUMENT]->(:Document)-[:HAS_VERSION]->(:Version)
  (:Document)-[:CURRENT_VERSION]->(:Version)
  (:Document)-[:HAS_CHANGE]->(:Change)-[:TO_VERSION|FROM_VERSION]->(:Version)
  (:Version)-[:CITES {locator}]->(:Instrument)
  ```

  Citations are first-class edges, which is what powers graph-RAG retrieval.

- **`sqlite`** — the original zero-infra relational backend (also Postgres via
  `DB_URL`). Full parity is enforced by a single contract test run against both
  (`tests/test_storage.py`).

Switching backends is a one-line `.env` change. Stable integer ids are minted
from a `(:Counter)` node so URLs (`/document/{id}`) work identically and the
schema is portable from local Neo4j 4.x to AuraDB 5.x with no code change.

### Graph-RAG ("Ask the law")

`taxrag.py` retrieves in three steps: **full-text seed** (Neo4j Lucene index /
SQLite scan) → **graph expansion** (each hit's cited instruments + latest change
summary) → **generate** (Grok answers, citing sources by number). Retrieval and
generation are separated, so a vector retriever can be added later without
touching answer generation. Embeddings are deferred for now.

## Usage

```bash
python3.12 -m ingest.cli --list                 # show configured documents
python3.12 -m ingest.cli --jurisdiction JE      # scrape one jurisdiction
python3.12 -m ingest.cli --all                  # scrape everything
python3.12 -m ingest.cli --all --no-ai          # skip Grok summaries
python3.12 -m ingest.cli --changes              # recent changes (CLI)
python3.12 -m ingest.cli --stats                # DB summary

python3.12 -m uvicorn web.app:app --port 8000    # web viewer
```

## Configuration (`.env`)

Copy `.env.example` to `.env`. Key settings:

```
DATA_STORAGE=neo4j        # 'neo4j' (default) or 'sqlite'
DB_URL=sqlite:///taxhub.db
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=...
NEO4J_DATABASE=neo4j
XAI_API_KEY=...           # xAI Grok (OpenAI-compatible); optional, AI degrades gracefully
XAI_BASE_URL=https://api.x.ai/v1
GROK_MODEL=grok-4-1-fast-reasoning
LLM_PROVIDER=xai
ADMIN_EMAIL=admin@jtgroup.com
ADMIN_PASSWORD=...
```

## Local Neo4j (dev)

A self-contained, user-owned instance (no sudo, separate from any system Neo4j):

```bash
scripts/neo4j_local.sh setup     # one-time: build config + set initial password
scripts/neo4j_local.sh start     # http://localhost:7474, bolt://localhost:7687
python3.12 scripts/migrate_sqlite_to_neo4j.py --wipe   # backfill the graph from SQLite
```

`NEO4J_PASSWORD` (default `taxhub-dev-password`) is read from the environment at
setup time. To run on SQLite instead, set `DATA_STORAGE=sqlite`.

## Deploy

Docker / Coolify (port 5011). The `web` service serves the viewer; the `scrape`
service (profile `tools`) runs the scraper against the shared data volume:

```bash
docker compose up -d web
docker compose run --rm scrape --all
```

Storage in production:

- **AuraDB (recommended):** set `NEO4J_URI=neo4j+s://<id>.databases.neo4j.io`
  plus `NEO4J_USER`/`NEO4J_PASSWORD`. No code change — the schema is created
  version-tolerantly (4.x and 5.x syntaxes).
- **Self-hosted Neo4j:** `docker compose --profile neo4j up -d` brings up a
  bundled `neo4j:5.26-community` service.
- **SQLite:** `DATA_STORAGE=sqlite` for a zero-infra deploy.

Run the scraper on a schedule (daily/weekly) to build the change history.

## Adding a document

Add a record under the relevant jurisdiction's source in
`config/tax_sources.yaml`:

```yaml
- id: my_new_doc
  title: Official Title
  doc_type: legislation        # legislation | guidance | gazette | treaty
  url: https://.../document.pdf
  format: pdf                  # html | pdf
  fetch: http                  # http | browser
  # selector: "main"           # optional CSS pin for html
```
