"""Text-to-SQL engine for FastFund analytics questions.

Takes a natural-language question, generates a single read-only SQL SELECT via the
LLM against the SQLite schema, executes it, and returns a formatted answer. Mirrors
the approach in the sister `ai-marketing` repo (utils/crm_query.ask_crm), but for
FastFund's relational store (no Cypher — the live backend is SQLite/Postgres).

Used both by the eval harness (`evals/run_evals.py`) and as the advisor's
`data_agent` tool for quantitative questions.
"""
from __future__ import annotations

import os
import re

from sqlalchemy import create_engine, text

from rag import llm

DB_URL = os.environ.get("DB_URL", "sqlite:///fastfund.db")

SCHEMA_DESCRIPTION = """
You have access to these SQLite tables for a FastFund Family Office cross/upsell CRM.

## sfos  — single family office (SFO) client profiles
Columns: id, client_ref, name, family_name, aum_usd (REAL, assets under management
in USD), family_size (INT), generations (INT), domicile (2-letter code e.g. JE GG
LU IE KY VG GB CH SG US AE), jurisdictions (JSON array of codes, TEXT),
current_services (JSON array of service keys, TEXT), asset_mix (JSON object of
{asset_class: percent}, TEXT), pain_points (JSON array, TEXT),
stage ('lead' | 'onboarding' | 'client'), contact_name, contact_email, created_at

## services  — the FastFund service catalogue
Columns: id, key (e.g. trusts, tax_reporting, fund_admin, luxury_assets,
real_estate_admin, edge, governance, nextgen_education, banking_treasury,
compliance, private_office), name, category (structuring | fund | luxury |
reporting | governance | banking | compliance | advisory), tier ('core' |
'premium'), description, url, keywords (JSON array, TEXT)

## cross_sells  — service bundling graph
Columns: from_id (FK services.id), to_id (FK services.id), weight (REAL 0-1)

## recommendations  — the cross/upsell funnel (one per sfo+service)
Columns: id, sfo_id (FK sfos.id), service_id (FK services.id),
kind ('cross_sell' | 'upsell'), score (REAL 0-1, the fit), rationale,
est_value_usd (REAL, estimated annual value), source ('rule'|'graph'|'hybrid'),
proposal (TEXT or NULL), status ('suggested'|'presented'|'accepted'|'booked'|'declined'), created_at

## family_members
Columns: id, sfo_id (FK sfos.id), name, role ('principal'|'spouse'|'next_gen'|'advisor'),
generation (INT), age (INT), notes

## documents
Columns: id, sfo_id (FK sfos.id), name, doc_type ('portfolio'|'trust_deed'|'asset_inventory'|'report'|'other'),
storage_key, byte_size, content_text, uploaded_by, created_at

## next_actions  — the pipeline calendar
Columns: id, sfo_id (FK sfos.id), recommendation_id, kind ('consultation'|'proposal'|'follow_up'|'review'),
title, due_date (ISO date TEXT), status ('open'|'done'|'cancelled'), notes

## conversations / messages
conversations: id, user_email, sfo_id (FK sfos.id), title, created_at, updated_at
messages: id, conversation_id (FK conversations.id), role, content, created_at

IMPORTANT RULES (SQLite):
- Only generate a single SELECT statement. Never INSERT/UPDATE/DELETE/DROP/ALTER/CREATE.
- jurisdictions, current_services, pain_points, keywords are JSON arrays stored as
  TEXT. To test membership use LIKE, e.g. a family that HOLDS trusts:
  current_services LIKE '%"trusts"%'.
- asset_mix is a JSON object stored as TEXT. Read a class with SQLite json_extract,
  e.g. average private-equity allocation:
  SELECT AVG(json_extract(asset_mix,'$.private_equity')) FROM sfos.
- "pipeline value" / "won pipeline" = SUM(est_value_usd) for recommendations with
  status IN ('accepted','booked').
- "clients" = sfos with stage='client'; "leads" = stage='lead'; "onboarding" likewise.
- A service is "held" by an SFO if its key is in sfos.current_services; it is
  "recommended" if a recommendations row links them.
- Return COUNT(*) for "how many" questions with a clear alias.
- Money is in USD (aum_usd, est_value_usd). Do not divide unless asked.
- Do NOT add LIMIT unless the user asks for a specific number (e.g. "top 5").
- ORDER BY the relevant column for "highest/largest/top" questions.

Examples:
Q: "How many family offices do we have?"
SELECT COUNT(*) AS family_offices FROM sfos

Q: "How many clients vs leads?"
SELECT stage, COUNT(*) AS n FROM sfos GROUP BY stage

Q: "How many family offices have AUM over $1bn?"
SELECT COUNT(*) AS n FROM sfos WHERE aum_usd > 1000000000

Q: "What is the total pipeline value (accepted or booked)?"
SELECT SUM(est_value_usd) AS pipeline_usd FROM recommendations WHERE status IN ('accepted','booked')

Q: "Which service is recommended most often?"
SELECT s.name, COUNT(*) AS n FROM recommendations r JOIN services s ON s.id=r.service_id GROUP BY s.id ORDER BY n DESC LIMIT 1

Q: "How many family offices already hold trusts?"
SELECT COUNT(*) AS n FROM sfos WHERE current_services LIKE '%"trusts"%'

Q: "What is the average private equity allocation across the book?"
SELECT ROUND(AVG(json_extract(asset_mix,'$.private_equity')),1) AS avg_pe FROM sfos

Q: "Which family office has the highest AUM?"
SELECT name, aum_usd FROM sfos ORDER BY aum_usd DESC LIMIT 1

Q: "How many overdue next actions are there?"
SELECT COUNT(*) AS n FROM next_actions WHERE status='open' AND due_date < date('now')
"""

SQL_PROMPT = """Given the schema above and the user's question, output ONE SQLite SELECT query.
Rules: return ONLY the SQL (no markdown, no explanation); SELECT only; use clear
aliases for aggregates; no LIMIT unless the user asks for a specific number.

Question: {question}
"""

_DANGEROUS = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|ATTACH|PRAGMA)\b", re.I)
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DB_URL, future=True)
    return _engine


def _extract_sql(raw: str) -> str | None:
    cleaned = re.sub(r"^```(?:sql)?\s*", "", (raw or "").strip(), flags=re.M)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.M).strip()
    if not cleaned.upper().startswith("SELECT"):
        m = re.search(r"(SELECT\s.+)", cleaned, re.I | re.S)
        cleaned = m.group(1) if m else ""
    if not cleaned or _DANGEROUS.search(cleaned):
        return None
    return cleaned.rstrip(";").strip()


def _format(columns, rows) -> str:
    if not rows:
        return "No results found."
    if len(columns) == 1 and len(rows) == 1:
        v = rows[0][0]
        return f"**{0 if v is None else v}** {columns[0].replace('_', ' ')}"
    if len(rows) == 1:
        return " | ".join(f"**{c.replace('_',' ').title()}:** {('' if v is None else v)}"
                          for c, v in zip(columns, rows[0]))
    lines = []
    for i, row in enumerate(rows[:50], 1):
        label = str(row[0] if row[0] is not None else f"#{i}")
        rest = " | ".join(f"{c.replace('_',' ').title()}: {('' if v is None else v)}"
                          for c, v in zip(columns[1:], row[1:]))
        lines.append(f"{i}. **{label}**" + (f" — {rest}" if rest else ""))
    return f"**{len(rows)} results:**\n" + "\n".join(lines)


def generate_sql(question: str) -> str | None:
    """NL question → a single SELECT (or None if unavailable/unsafe)."""
    if not llm.ai_available():
        return None
    raw = llm.complete(SCHEMA_DESCRIPTION, SQL_PROMPT.format(question=question), temperature=0)
    return _extract_sql(raw)


def ask_data(question: str, return_sql: bool = False):
    """Answer a quantitative question over the FastFund DB via text-to-SQL.

    Returns the formatted answer string, or (answer, sql) when return_sql=True.
    Degrades to a clear message if AI is off or the SQL is unsafe/invalid.
    """
    sql = generate_sql(question)
    if not sql:
        ans = ("Data queries need an LLM key (text-to-SQL unavailable)."
               if not llm.ai_available() else "Could not generate a safe SQL query for that.")
        return (ans, None) if return_sql else ans
    try:
        with _get_engine().connect() as c:
            res = c.execute(text(sql))
            cols = list(res.keys())
            rows = res.fetchall()
        ans = _format(cols, rows)
    except Exception as e:  # noqa: BLE001
        ans = f"Query failed: {e}"
    return (ans, sql) if return_sql else ans
