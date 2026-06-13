# TaxHub — "Ontology" View Plan

> **Decision (2026-06):** This splits into two surfaces.
> - **Document Hierarchy** — a *tree* (Jurisdiction ▸ Category ▸ Form type ▸ Form),
>   rendered with **Plotly** (icicle/treemap/sunburst), click-to-open. Lives in the
>   main **Navigate** menu. **Implemented** — `/document-hierarchy`, off existing data.
> - **Ontology** — the *network/provenance* graph (forms ↔ the legislation they
>   implement ↔ jurisdictions, plus the schema/meta-graph). More technical, so it
>   lives under **Help** (next to the User Guide). **Deferred** — needs real
>   `(:Form)-[:IMPLEMENTS]->(:Document)` edges first (phase 4 below). The NetworkX
>   design below is for this Ontology surface.


A new left-menu item **Ontology** that renders an interactive **NetworkX graph**
of the corpus: both *how the graph DB is organised* (the schema/meta-graph) and
*how the actual documents relate* (the instance graph — jurisdictions, the tax
forms they publish, and the legislation those forms implement).

This makes the data model legible to a fund back-office user: "show me, visually,
what we hold and how a form traces back to the law."

---

## 1. What it visualises (two layers, one toggle)

**A. Schema / meta-graph** (small, fixed) — the ontology itself: the node labels
and the relationships between them. Answers *"how is the graph DB organised?"*

```
(:Jurisdiction) ──HAS_FORM──▶ (:Form) ──IMPLEMENTS──▶ (:Document) ──HAS_VERSION──▶ (:Version) ──HAS_CHUNK──▶ (:Chunk)
       │                                                   │                            │
       └──────────────────HAS_DOCUMENT────────────────────┘                       HAS_CHANGE ▶ (:Change)
(:ChatSession) ──HAS_MESSAGE──▶ (:ChatMessage)            (:Version) ──CITES {locator,snippet}──▶ ...
```

**B. Instance graph** (the real data, filterable) — actual nodes for a chosen
jurisdiction (or all): each `Jurisdiction` → its `Form`s → the `Document`
(legislation) each form's `legislation_ref` points to → that document's
`Version`s/`Change`s. This is the **provenance map** — the core "forms trace back
to law" story, drawn.

A toggle (Schema ⇄ Data) and a jurisdiction filter sit above the canvas. Node
colour = label (JTC palette); node size = degree. Clicking a `Form`/`Document`
node opens it in the right-hand PDF/detail pane (reusing `/form/{id}` and
`/document/{id}`), so the graph becomes a navigation surface, not just a picture.

---

## 2. Why NetworkX (+ how it renders in the browser)

- **NetworkX** builds the graph server-side and gives us graph *analytics* for
  free: degree/centrality (to size nodes), connected components (orphan forms with
  no `legislation_ref`), and "most-cited law" rankings — useful signal, not just decoration.
- **Rendering:** two viable paths —
  - **Fast path — `pyvis`** (wraps vis.js, built directly on a NetworkX graph):
    `Network.from_nx(G)` → `write_html()` → embed the generated HTML in an
    `<iframe>` in the Ontology pane. Minimal custom JS; physics/zoom/drag included.
  - **Integrated path — NetworkX → JSON → vis-network/cytoscape.js**: serialise
    `G` to `{nodes, edges}` and render with a CDN lib so we own click events
    (node-click → open in right pane) and styling. Recommended for v1 because the
    click-to-open integration is the whole point.
- Add `networkx` (and optionally `pyvis`) to `requirements.txt`. Both are pure-Python, light.

---

## 3. Backend

Add to the `Storage` ABC + both backends (`neo4j_store.py`, `sqlite_store.py`):

- `graph_schema() -> {nodes:[{label, count}], edges:[{from, to, type, count}]}`
  — the meta-graph with live counts (Neo4j: `CALL db.schema.visualization()` or
  hand-rolled `MATCH` counts; sqlite: synthesised from table row counts + the
  known FK relationships).
- `graph_instance(jurisdiction=None, limit=400) -> {nodes, edges}` — actual nodes
  capped for legibility. Neo4j: a few `MATCH` queries (Jurisdiction→Form,
  Form→Document via `legislation_ref`/`IMPLEMENTS`, Document→Version→Change).
  sqlite: joins over `tax_forms` / `documents` / `versions` / `changes`, linking
  `tax_forms.legislation_ref` to `documents.url` where they match.

A small `web/ontology.py` (or a function in `ingest/`/`rag/`) assembles the
NetworkX `DiGraph` from those dicts, computes degree for sizing, and returns the
serialised payload. **Note:** today `Form → Document` provenance is a *string*
(`legislation_ref` URL), not a real edge — the instance graph will link a form to
a document when its `legislation_ref` matches a tracked document's URL; otherwise
the form shows a dangling "law" node (the URL) we can later promote to a real
`(:Legislation)`/`IMPLEMENTS` edge.

## 4. Frontend

- **Left menu:** add `Ontology` under Dashboard/Jurisdictions (and a Forms-Tree
  sibling). New route `@rt("/ontology")` → `Page(...)` with a full-height canvas
  `<div id="net">`, the Schema/Data toggle, and a jurisdiction `<select>`.
- Load vis-network from CDN; fetch `/api/ontology?mode=schema|data&jur=XX`
  (returns the JSON payload) and render. Node groups styled with the JTC palette
  (Jurisdiction = purple `#6B1766`, Form = magenta `#BA2A84`, Document = slate,
  Version/Change/Chunk = lighter tints).
- Node-click handler: if `group==form` → load `/form/{id}` in the right pane; if
  `document` → `/document/{id}`; jurisdiction → filter to that jurisdiction.
- A small legend + the NetworkX-computed stats line ("142 forms · 61 documents ·
  most-cited: Income Tax (Jersey) Law 1961").

## 5. Phases

1. **Schema view** — `graph_schema()` + static meta-graph render (no per-row
   data). Ships the "how the DB is organised" answer immediately. (~½ day)
2. **Instance view** — `graph_instance()` + jurisdiction filter + node sizing by
   degree. (~1 day)
3. **Click-to-open** integration with the right pane + stats line. (~½ day)
4. **Provenance hardening** (optional) — promote `legislation_ref` to a real
   `(:Form)-[:IMPLEMENTS]->(:Document)` edge during ingest so the graph is exact
   rather than URL-matched. (~½ day)

## 6. Risks / notes

- **Scale/legibility:** the full instance graph (140+ forms × jurisdictions) is
  too dense to read at once — always default to a jurisdiction filter and cap at
  ~400 nodes; offer "expand neighbours" on click rather than dumping everything.
- **No new datastore:** this is a *read/visualise* feature over the existing
  graph; no schema migration required for phases 1–3.
- **Embeddings/Chunks** are many — exclude `(:Chunk)` from the default instance
  view (show only in schema view, or behind a "show chunks" toggle).
