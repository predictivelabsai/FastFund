# FastFund — Sphere-Inspired Expansion Plan

Evolve FastFund from a **forms reference + traceability** tool into an **AI-native
fund-tax compliance platform**: entity-centric, with the full obligation
lifecycle. Inspired by [Sphere/getsphere.com](https://www.getsphere.com)'s model
(Monitor → Register → Calculate → File, powered by an expert-reviewed, codified
tax engine) — but scoped to FastFund's domain: **fund/trust/corporate direct tax,
economic substance, AEOI (FATCA/CRS), beneficial ownership**. No indirect (VAT/GST).

## Decisions (locked)
- **Ambition:** entity-centric compliance platform (funds/SPVs → obligations → deadlines → filing status).
- **Stages:** build all four — Monitor, Determine, Coverage Map, File-status.
- **Tax scope:** fund direct-tax only (not VAT/GST).
- **Entity data:** manual entry + CSV import (design for a future FastFund entity-system connector).

## What we reuse (already built)
- The **26-jurisdiction forms catalogue** with structured `who_files / deadline /
  frequency / category / filing_type` — this is our deterministic rules base
  (Sphere's "codified tax" analogue; we already have it, expert-curated).
- **Provenance edges** `(:Form)-[:IMPLEMENTS]->(:Legislation)-[:SOURCED_FROM]->(:Document)` for grounding.
- The **orchestrator + agents**, the **Copilot**, the 3-pane UI, AuraDB + the Storage ABC.

---

## 1. Data model (new nodes)

```
(:Entity {uid, name, type[fund|spv|gp|trust|company|holdco],
          domicile, jurisdictions[], fy_end, activities[], client_ref, status})
   -[:HAS_OBLIGATION]->
(:Obligation {uid, title, category, period, due_date,
              status[not_started|in_progress|prepared|filed|confirmed|na],
              verified[bool], assignee, notes})
   -[:FOR_FORM]-> (:Form)          # the form/filing this obligation requires
(:Entity)-[:IN_JURISDICTION]->(:Jurisdiction)
```

Relational mirror: `entities`, `obligations` tables (sqlite/Postgres parity).
Storage ABC gains: `upsert_entity / get_entity / list_entities / delete_entity`,
`upsert_obligation / list_obligations(entity?, jurisdiction?, status?, due_before?)`,
`set_obligation_status`. Implement in both backends (Neo4j + sqlite).

---

## 2. The four stages

### A. Determine — the obligation engine ("TRAM" analogue)
Given an entity's `jurisdictions + type + activities`, determine which
forms/obligations apply, and instantiate `(:Obligation)` rows with computed due
dates. Two layers, Sphere-style:
- **Deterministic core:** match catalogue forms where `jurisdiction ∈ entity.jurisdictions`
  and `category` is relevant to the entity's type/activities; due date = derived
  from `form.deadline` + `entity.fy_end` (e.g. "30 November following year-end").
- **AI assist (grounded):** an LLM maps free-text `activities` → relevant
  categories (e.g. "fund management, holding" → economic_substance + corporate_tax)
  and drafts the obligation set; each obligation carries a **`verified` flag** so a
  human can confirm — the human-in-the-loop guardrail (no silent hallucination).
- Re-runnable: `POST /entity/{id}/determine` refreshes the obligation set
  non-destructively (keeps status/verified on existing matches).

### B. Monitor — deadline calendar + alerts
- `/calendar` view (and per-entity) over `obligation.due_date`; colour by status/urgency.
- A scheduled **digest** (reuse the changes pipeline) — upcoming deadlines +
  relevant **law changes** for each entity's jurisdictions, emailed/Slacked.
- Ties the existing change-tracking to entities: a change in JE law flags JE entities.

### C. Coverage Map
- A visual world map / matrix: **jurisdiction × obligation type × coverage**
  (downloadable / online / verified), from the existing audit + catalogue.
- Plus a **portfolio coverage** lens: entities × obligations × status (how much of
  the book is filed/outstanding). Plotly choropleth or a matrix heatmap.

### D. File-status tracking
- Obligation `status` workflow: not_started → in_progress → prepared → filed →
  confirmed (+ `na`). Per-entity and portfolio roll-up on the **Dashboard**
  (Sphere's "X% compliant" feel).
- Attach the filed PDF / reference to the obligation; link to the source `Form`.

---

## 3. UI / routes
- **Navigate ▸ Entities** → `/entities` (list, search, **add form + CSV import**),
  `/entity/{id}` (profile · obligations table with status toggles · "Determine
  obligations" button · upcoming deadlines).
- **Navigate ▸ Calendar** → `/calendar` (Monitor).
- **Dashboard** gains a portfolio roll-up (entities, obligations by status, next deadlines).
- **Coverage Map** → `/coverage` (or fold into Dashboard).
- **Copilot/Assistant:** add an `entity_agent` tool so you can ask *"what does
  Fund A owe this quarter?"* / *"which entities have economic-substance due before 30 Jun?"*
- **CSV import** schema: `name,type,domicile,jurisdictions,fy_end,activities,client_ref`.

---

## 4. Phased delivery
1. **Entity model + CRUD + CSV import** — storage (both backends) + `/entities` +
   `/entity/{id}` + Dashboard entity count. *(foundation)*
2. **Determine (obligation engine)** — deterministic matcher + AI activity-mapper +
   `verified` flag + obligations table on the entity page.
3. **File-status** — status workflow + portfolio roll-up on the Dashboard.
4. **Monitor** — `/calendar` + due-date computation + deadline/law-change digest. ✅ DONE
   - `web/monitor.py`: deterministic due-date resolver (ordered regex over the real
     deadline rules) → concrete date + `basis` + `indicative` flag; genuinely
     undatable rules (filed-with-return, anniversary, notice-relative) resolve to
     `None` with an explanation. No persistence — dates computed on the fly from
     obligation rule + entity FY-end (re-running Determine re-dates for free).
   - `/calendar` (urgency chips overdue/due-soon/upcoming/scheduled/undated/done +
     jurisdiction filter); `/admin/digest` JSON (overdue + ≤90-day upcoming) as the
     alert foundation; Dashboard "Deadlines" panel; per-entity "Due" column.
   - *Remaining for a later pass:* scheduled email/Slack send + joining law-change
     events to entity jurisdictions (the digest data is already assembled).
5. **Coverage Map** — `/coverage` visual (jurisdiction + portfolio lenses). ✅ DONE
   - `web/coverage.py`: `portfolio_matrix` (entity × file-status grid + headline
     "% filed/confirmed") and `catalogue_matrix` (jurisdiction × category form
     counts + filing-type breakdown). Pure data assembly.
   - `/coverage`: two Plotly heatmaps (portfolio purple, catalogue green) + a
     summary header (book % filed, catalogue totals by filing type). Cells link
     out / hover to the filing-type split.
6. **entity_agent** for Copilot/Assistant + expert-verified badges across answers. ✅ DONE
   - `entity_agent` tool (agents/tools.py) over entities + obligations + the Monitor
     due-date resolver: focus one entity or scan the book, filter by category /
     status / due_before (ISO) / due_within_days; each row shows resolved due date,
     urgency, status and a ✓ verified / ⚠ awaiting-sign-off flag. Registered in
     ALL_TOOLS; orchestrator routes to it for entity/portfolio/deadline questions
     and is told to surface the verification flag. Answers like "what does Aurora
     owe this year?" and "which entities have economic-substance due before 30 Sep?".
   - **UX:** the Navigate pages now share the Assistant's 3-pane shell — left nav,
     centre content, and a persistent **Copilot** pane on the right (replacing the
     changes feed) that chats with the same agents while the page stays visible.
     Recent chats moved directly under "New chat" in the left rail.

Each phase is independently shippable and deploys via the existing pipeline.
No new infrastructure — new nodes/tables in AuraDB, new routes in the FastHTML app.

## 5. Risks / notes
- **Due-date computation** varies (anniversary-based vs fixed-calendar vs
  N-months-after-year-end) — model `deadline` as a small rule, not a literal date;
  start with the common patterns already in the catalogue and flag the rest for review.
- **Determine accuracy** — keep it grounded in the curated catalogue + `verified`
  human sign-off; never auto-mark an obligation "confirmed".
- **Entity data sensitivity** — real fund/SPV data is confidential; keep behind
  login, and design the CSV/connector so prod data stays on the Coolify/AuraDB
  instance only.
