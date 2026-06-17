# SFO Hub — Technical Architecture (Agentic)

The AI conversational advisor for cross-selling/upselling to single family offices
(SFOs). This document describes the **agentic architecture**: the LangGraph
orchestrator, its specialist agents, the hybrid recommendation engine, the
text-to-SQL data agent, and how the whole system is wired and deployed.

Rendered diagrams live in [`docs/diagrams/`](diagrams/); a slide deck is at
[`docs/technical_architecture_slides.pdf`](technical_architecture_slides.pdf).

---

## 1. System overview

A single FastHTML app serves the 3-pane advisor UI and routes every chat message
to a LangGraph tool-calling orchestrator. The orchestrator calls specialist
agents, which read/write the backend-neutral store and call an OpenAI-compatible
LLM. Documents go to Cloudflare R2.

```mermaid
flowchart TB
    USER(["SFO principal / advisor / sales team"])
    subgraph APP["FastHTML app (Coolify · port 5021)"]
        UI["3-pane UI + multi-page views<br/>advisor · pipeline kanban · dashboard · clients"]
        ORCH["LangGraph orchestrator<br/>(create_react_agent, SSE streaming)"]
        ENGINE["Hybrid cross/upsell engine"]
        STORE["Storage interface"]
    end
    LLM[["OpenAI-compatible LLM<br/>xAI Grok (dev) → Azure AI Foundry (prod)"]]
    DB[("SQLite / Postgres<br/>(Neo4j AuraDB target)")]
    R2[("Cloudflare R2<br/>documents")]

    USER -->|HTTPS| UI
    UI -->|/chat SSE| ORCH
    ORCH -->|tool calls| ENGINE
    ORCH --> STORE
    ENGINE --> STORE
    ORCH -->|tokens + tool steps| LLM
    STORE --> DB
    UI -->|upload / fetch| R2
```

---

## 2. Agent orchestration

The orchestrator is a LangGraph `create_react_agent` with a relationship-manager
persona. It routes each message to one or more of six specialist agents (exposed
as tools) and composes a warm, cited answer. Tokens and tool steps stream to the
UI over SSE; tool-internal LLM calls are tagged `nested_llm` and filtered out of
the user-visible stream.

```mermaid
flowchart TB
    MSG(["User message (+ open SFO context)"])
    ORCH{{"Orchestrator<br/>LangGraph react-agent · Grok"}}
    MSG --> ORCH

    PROFILE["profile_agent<br/>who the client is"]
    NEEDS["needs_agent<br/>detect gaps → categories"]
    SERVICES["services_agent<br/>explain JTC services"]
    RECOMMEND["recommend_agent<br/>ranked cross/upsell"]
    BENCH["benchmark_agent<br/>industry context"]
    DATA["data_agent<br/>text-to-SQL analytics"]

    ORCH --> PROFILE
    ORCH --> NEEDS
    ORCH --> SERVICES
    ORCH --> RECOMMEND
    ORCH --> BENCH
    ORCH --> DATA

    STORE[("Storage")]
    ENGINE["Hybrid engine"]
    KB["Services + benchmarks"]
    SQL["SQLite/Postgres"]
    PROFILE --> STORE
    NEEDS --> KB
    SERVICES --> STORE
    RECOMMEND --> ENGINE --> STORE
    BENCH --> KB
    DATA --> SQL
    ORCH -->|"composed, cited answer (SSE)"| OUT(["Reply + [service:N]/[sfo:N] markers"])
```

| Agent | Job | Backed by |
|-------|-----|-----------|
| `profile_agent` | Ground in the open family (AUM, mix, services, pains) | `store.get_sfo / search_sfos` |
| `needs_agent` | Detect gaps from a described setup → service categories | `rag/knowledge.py` |
| `services_agent` | Explain JTC services for a topic | `store.search_services` |
| `recommend_agent` | Ranked cross/upsell with rationale + value | `engine/crosssell.py` |
| `benchmark_agent` | Aggregate industry benchmarks | `rag/knowledge.py` |
| `data_agent` | Quantitative book-wide questions via text-to-SQL | `rag/text2sql.py` |

---

## 3. Hybrid recommendation engine

Recommendations are **never invented by the LLM alone**. A transparent rule
catalogue and the cross-sell graph produce the candidate set; the AI layer only
re-ranks and rewrites rationales. Every recommendation is traceable to the rule
or graph edge that produced it.

```mermaid
flowchart LR
    P(["SFO profile"]) --> RULES["1 · Rule catalogue<br/>engine/rules.py"]
    RULES --> GRAPH["2 · Graph expansion<br/>CROSS_SELLS_TO partners"]
    GRAPH --> AI["3 · AI re-rank + rationale<br/>(tagged nested_llm)"]
    AI --> VALUE["4 · Estimated value<br/>AUM × tier bps"]
    VALUE --> PERSIST[("5 · Persist<br/>RECOMMENDED rows")]
    PERSIST --> UI(["Ranked cards · proposals · pipeline kanban"])
    AI -. "no LLM key" .-> DEGRADE["Degrade: rule/graph scores stand"]
```

---

## 4. Data agent — text-to-SQL + evals

The `data_agent` answers quantitative, book-wide questions by generating a
read-only SQL `SELECT` over the relational schema, executing it, and formatting
the result. An eval harness scores answers against a ground-truth set with a
deepeval GEval correctness metric judged by Grok — run against the **real
assistant** (full orchestrator), not just the engine.

```mermaid
flowchart TB
    Q(["Question: 'how many family offices over $1bn?'"])
    Q --> GEN["LLM generates SQL<br/>(schema-grounded, SELECT-only guard)"]
    GEN --> EXEC[("Execute on SQLite/Postgres")]
    EXEC --> FMT["Format answer"]
    FMT --> A(["'41 family offices have AUM over $1bn'"])

    subgraph EVAL["Eval harness (evals/run_evals.py)"]
        GT[("ground_truth.csv")]
        RUN["run via assistant or sql"]
        JUDGE["deepeval GEval · Grok judge"]
        GT --> RUN --> JUDGE --> SCORE(["PASS/FAIL + score"])
    end
    A -. tested by .-> RUN
```

---

## 5. Data & graph model

`(:SFO)` is the hub. The relational store mirrors the same shape as tables; the
Neo4j target uses first-class edges (`CROSS_SELLS_TO`, `RECOMMENDED`).

```mermaid
graph LR
    O["(:SFO)"] -- HOLDS_SERVICE --> SV["(:Service)"]
    O -- "RECOMMENDED {kind,score,status}" --> SV
    O -- HAS_MEMBER --> M["(:Member)"]
    O -- HAS_DOCUMENT --> D["(:Doc)"]
    O -- HAS_ACTION --> ACT["(:Action)"]
    O -- HAS_CONVERSATION --> C["(:Conversation)"]
    C -- HAS_MESSAGE --> MSG["(:Message)"]
    SV -- "CROSS_SELLS_TO {weight}" --> SV
```

---

## 6. Deployment & CI/CD

Push to `main` → GitHub Actions → Coolify deploy webhook (`force=true`) → Docker
build + rolling update behind Cloudflare. Documents in R2; the LLM is xAI Grok in
dev, Azure AI Foundry the production target.

```mermaid
flowchart LR
    DEV["git push main"] --> GH["GitHub Actions<br/>deploy.yml"]
    GH -->|"/api/v1/deploy webhook"| COOL["Coolify<br/>(JTCGroup project)"]
    COOL --> BUILD["Docker build<br/>Dockerfile · port 5021"]
    BUILD --> RUN["Rolling update<br/>healthcheck /health"]
    RUN --> LIVE(["sfohub.predictivelabs.ai<br/>behind Cloudflare"])
    LIVE --> R2[("Cloudflare R2")]
    LIVE --> GROK[["xAI Grok / Azure AI Foundry"]]
```

---

## 7. Request lifecycle (end to end)

```mermaid
sequenceDiagram
    participant U as User
    participant W as FastHTML /chat
    participant O as Orchestrator (Grok)
    participant T as Specialist agent
    participant S as Store / SQL / engine
    U->>W: message (SSE)
    W->>O: astream_events(messages)
    O->>T: tool call (e.g. recommend_agent / data_agent)
    T->>S: read/write (rules, graph, SQL)
    S-->>T: data
    T-->>O: tool result (markdown, [markers])
    Note over O,T: tool-internal LLM calls tagged nested_llm,<br/>filtered from the user stream
    O-->>W: streamed tokens (synthesis only)
    W-->>U: live answer + open-in-panel links
```

---

For the broader system design, Azure target and phased plan see
[`architecture_readme.md`](architecture_readme.md).
