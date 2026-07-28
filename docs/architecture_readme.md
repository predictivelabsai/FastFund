# FastFund Technical Architecture

FastFund is one FastHTML application combining family-office relationship
outreach with multijurisdiction tax filing intelligence.

## Runtime

```text
Browser
  │
  ▼
FastHTML / Starlette application (`web.app:app`)
  ├── Shared identity, teams, roles, invitations and audit events
  ├── Tax & Compliance routes
  ├── Knowledge and regulatory-change routes
  └── `/family` family-office domain routes
       └── Same process, environment, DB_URL and APP_SECRET
```

The family route package preserves the complete family-office feature set while
using FastFund's shared runtime and navigation. Both domains are imported into
the FastFund repository with their original Git histories.

## Domain model

```text
(:FamilyOffice)-[:HAS_MEMBER]->(:FamilyMember)
(:FamilyOffice)-[:HAS_HOLDING]->(:Holding)
(:FamilyOffice)-[:RECOMMENDED]->(:Service)
(:FamilyOffice)-[:OWNS_ENTITY]->(:LegalEntity)

(:LegalEntity)-[:IN_JURISDICTION]->(:Jurisdiction)
(:LegalEntity)-[:OWES]->(:FilingObligation)-[:USES_FORM]->(:TaxForm)

(:Jurisdiction)-[:HAS_DOCUMENT]->(:TaxDocument)
(:TaxDocument)-[:HAS_VERSION]->(:Version)
(:TaxDocument)-[:HAS_CHANGE]->(:Change)
(:Version)-[:CITES]->(:Instrument)
```

SQLite stores the same relationship through `entities.sfo_id`; Neo4j
materialises `OWNS_ENTITY`. This is the bridge between family-office outreach
and tax operations.

## Source layout

```text
web/                 shared shell, tax, knowledge and administration UI
family/web/          family-office advisor and relationship UI
storage/             tax/platform SQLite and Neo4j contracts
family/storage/      family-office SQLite and Neo4j contracts
agents/              tax, forms, change and metadata agents
family/agents/       profile, needs, service and recommendation agents
ingest/              official tax-source ingestion and form discovery
rag/                 tax retrieval, embeddings and cited generation
family/rag/          service knowledge and family data retrieval
engine/              family-office recommendation and proposal engine
family/engine/       namespaced complete family implementation
config/              jurisdiction, tax-source and tax-form catalogues
family/data/         services catalogue and synthetic family generator
```

## Storage

Set `DATA_STORAGE` to:

- `sqlite` for SQLite or Postgres through `DB_URL`.
- `neo4j` for a local server, self-hosted deployment or AuraDB.

FastFund adds idempotent schema migrations for existing source databases,
including the richer identity fields and the `entities.sfo_id` ownership link.

Uploaded family documents use local storage or S3-compatible object storage
through `DOC_STORAGE`.

## Agent orchestration

The assistants use LangGraph over specialist tools:

- Family profile and needs analysis
- Service catalogue and explainable recommendation generation
- Tax form discovery
- Tax-law retrieval with citations
- Regulatory change retrieval
- Entity and obligation data queries

LLM access is OpenAI-compatible. xAI and Azure AI Foundry are supported.
Deterministic filing and recommendation workflows continue without an LLM key.

## Security and tenancy

- Password or Google OAuth authentication
- Global and team administration
- Team-scoped entities, conversations and analytics
- Invitations and role management
- Audit events
- A shared `APP_SECRET` across every FastFund route

`FASTFUND_PUBLIC=1` is intended only for local demonstrations.

## Deployment

The supplied Docker image runs the FastHTML application on port 5011. Docker
Compose includes:

- `web`: the combined FastFund application
- `scrape`: on-demand or scheduled official-source ingestion
- `neo4j`: optional self-hosted graph database

For production, use a persistent database and document store, a strong
`APP_SECRET`, restricted OAuth domains and a managed secrets service.

## Verification

```bash
python -m pytest tests -q
python -m family.data.synth --count 24 --seed 7
python -m uvicorn web.app:app --port 5011
```

The combined smoke tests verify both route families and the
`FamilyOffice → LegalEntity` ownership link.
