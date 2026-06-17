"""Storage interface for SFO Hub.

The persistence backbone is pluggable: a relational backend (SQLite, and by
extension Postgres) and a graph backend (Neo4j) both implement this one
interface, selected at runtime by the ``DATA_STORAGE`` environment variable.

The domain is a JTC Private Office relationship-manager simulator. Everything the
app produces — single-family-office (SFO) client profiles, the JTC service
catalogue and the cross-sell graph between services, conversation logs, and the
cross/upsell recommendations generated for each client — flows through these
methods. No caller (web app, agents, cross-sell engine, seeder) touches a backend
directly; that is what makes the backend swappable.

Return shapes are plain ``dict`` / ``list[dict]`` so the web layer and CLI are
identical regardless of backend. Timestamps are ISO-8601 UTC strings; list-valued
fields (``jurisdictions``, ``current_services``, ``pain_points``) go in and come
out as Python lists regardless of backend.
"""

from __future__ import annotations

import abc
from datetime import datetime, timezone


def utcnow() -> str:
    """ISO-8601 UTC timestamp string (no backend stores a native tz)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def week_buckets(rec_dates, conv_dates, weeks: int = 12) -> list[dict]:
    """Bucket two lists of ISO date/datetime strings into the last ``weeks``
    Monday-aligned weeks. Shared by both backends' ``activity_trends`` so the
    weekly roll-up is identical regardless of store."""
    from collections import Counter
    from datetime import date, timedelta

    def monday(s):
        if not s:
            return None
        try:
            d = datetime.fromisoformat(str(s)[:10]).date()
        except ValueError:
            return None
        return d - timedelta(days=d.weekday())

    today = date.today()
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    labels = [start + timedelta(weeks=i) for i in range(weeks)]
    rec = Counter(m for m in map(monday, rec_dates) if m is not None)
    conv = Counter(m for m in map(monday, conv_dates) if m is not None)
    return [{"label": w.strftime("%d %b"), "recommendations": rec.get(w, 0),
             "conversations": conv.get(w, 0)} for w in labels]


class Storage(abc.ABC):
    """Backend-neutral persistence + read API for SFO Hub.

    The graph shape this models (Neo4j) is:

    ```
    (:SFO)-[:HOLDS_SERVICE]->(:Service)
    (:SFO)-[:HAS_CONVERSATION]->(:Conversation)-[:HAS_MESSAGE]->(:Message)
    (:SFO)-[:RECOMMENDED {kind, score, status}]->(:Service)
    (:Service)-[:CROSS_SELLS_TO {weight}]->(:Service)
    ```

    ``CROSS_SELLS_TO`` is the cross-sell knowledge graph — first-class edges, the
    direct analogue of TaxHub's citation graph, and what the hybrid engine and a
    future graph-RAG traversal walk.
    """

    # ── Lifecycle ──────────────────────────────────────────────────────────

    @abc.abstractmethod
    def init_db(self) -> None:
        """Create schema/constraints/indexes and seed the admin user. Idempotent."""

    # ── Users / auth ───────────────────────────────────────────────────────

    @abc.abstractmethod
    def get_user_by_email(self, email: str) -> dict | None:
        """Return ``{id, email, password_hash, role}`` for a user, or None."""

    # ── SFO client profiles ────────────────────────────────────────────────
    # The family-office records the relationship manager talks to. ``asset_mix``
    # is a ``{asset_class: percent}`` dict; ``jurisdictions``, ``current_services``
    # and ``pain_points`` are lists in/out regardless of backend.

    @abc.abstractmethod
    def upsert_sfo(self, sfo: dict) -> int:
        """Insert/update an SFO by natural key ``client_ref`` (falls back to
        ``name``). Fields: name, family_name, aum_usd, family_size, generations,
        domicile, jurisdictions[list], current_services[list], asset_mix[dict],
        pain_points[list], stage, client_ref, contact_name, contact_email.
        Returns the SFO id."""

    @abc.abstractmethod
    def get_sfo(self, sfo_id: int) -> dict | None:
        ...

    @abc.abstractmethod
    def list_sfos(self, stage: str | None = None, limit: int = 500) -> list[dict]:
        """SFOs, optionally filtered by lifecycle ``stage`` (lead/onboarding/client)."""

    @abc.abstractmethod
    def search_sfos(self, query: str, limit: int = 10) -> list[dict]:
        """Keyword search over SFO name / family / client_ref."""

    @abc.abstractmethod
    def count_sfos(self) -> int:
        ...

    @abc.abstractmethod
    def delete_sfo(self, sfo_id: int) -> None:
        ...

    # ── JTC service catalogue + cross-sell graph ───────────────────────────
    # A Service is a JTC Private Office offering (trusts, fund admin, luxury asset
    # administration, Edge reporting, banking/treasury, governance, …). The
    # cross-sell graph links services that commonly bundle together.

    @abc.abstractmethod
    def upsert_service(self, service: dict) -> int:
        """Insert/update a service by natural key ``key``. Fields: key, name,
        category, tier (core/premium), description, url, keywords[list].
        Returns the service id."""

    @abc.abstractmethod
    def get_service(self, service_id: int) -> dict | None:
        ...

    @abc.abstractmethod
    def get_service_by_key(self, key: str) -> dict | None:
        ...

    @abc.abstractmethod
    def list_services(self, category: str | None = None,
                      limit: int = 500) -> list[dict]:
        ...

    @abc.abstractmethod
    def search_services(self, query: str, limit: int = 8) -> list[dict]:
        """Keyword search over service name/category/description/keywords — the
        retrieval primitive for the services-knowledge RAG tool."""

    @abc.abstractmethod
    def add_cross_sell_edge(self, from_key: str, to_key: str,
                            weight: float = 1.0) -> None:
        """Record ``(:Service {from_key})-[:CROSS_SELLS_TO {weight}]->(:Service {to_key})``."""

    @abc.abstractmethod
    def list_cross_sells(self, service_key: str) -> list[dict]:
        """Services commonly bundled after ``service_key``. Each row is a service
        dict plus a ``weight``. Drives the rule layer's graph expansion."""

    # ── Recommendations (the cross/upsell funnel) ──────────────────────────
    # One recommendation per (sfo, service). The hybrid engine upserts these;
    # the UI advances ``status`` along the funnel.

    @abc.abstractmethod
    def upsert_recommendation(self, rec: dict) -> int:
        """Insert/update a recommendation by (sfo_id, service_id). Fields:
        sfo_id, service_id, kind (cross_sell/upsell), score, rationale,
        est_value_usd, source (rule/ai/hybrid). On create sets status='suggested';
        on update refreshes score/rationale but never clobbers an advanced status.
        Returns the recommendation id."""

    @abc.abstractmethod
    def list_recommendations(self, sfo_id: int | None = None,
                             status: str | None = None,
                             limit: int = 500) -> list[dict]:
        """Recommendations, enriched with service name/category and SFO name,
        highest score first."""

    @abc.abstractmethod
    def set_recommendation_status(self, recommendation_id: int, status: str) -> None:
        """Advance the funnel: suggested → presented → accepted → booked, or declined."""

    # ── Conversations (assistant sessions, optionally tied to an SFO) ───────

    @abc.abstractmethod
    def create_conversation(self, user_email: str, sfo_id: int | None = None,
                            title: str = "") -> int:
        ...

    @abc.abstractmethod
    def add_message(self, conversation_id: int, role: str, content: str) -> None:
        ...

    @abc.abstractmethod
    def list_conversations(self, user_email: str | None = None,
                           sfo_id: int | None = None,
                           limit: int = 30) -> list[dict]:
        """Recent conversations: ``{id, title, sfo_id, updated_at}`` newest first."""

    @abc.abstractmethod
    def get_messages(self, conversation_id: int) -> list[dict]:
        """Messages for a conversation in order: ``{role, content, created_at}``."""

    @abc.abstractmethod
    def set_recommendation_proposal(self, recommendation_id: int, proposal: str) -> None:
        """Store an AI-generated proposal/next-step narrative on a recommendation."""

    # ── Family members (principals, next-gen, advisors) ─────────────────────
    # An SFO has people: the (:SFO)-[:HAS_MEMBER]->(:Member) sub-graph used by the
    # profile page and the governance/next-gen recommendations.

    @abc.abstractmethod
    def upsert_family_member(self, member: dict) -> int:
        """Insert/update a member by (sfo_id, name). Fields: sfo_id, name, role
        (principal/spouse/next_gen/advisor), generation, age, notes. Returns id."""

    @abc.abstractmethod
    def list_family_members(self, sfo_id: int) -> list[dict]:
        ...

    @abc.abstractmethod
    def delete_family_member(self, member_id: int) -> None:
        ...

    # ── Documents (uploaded portfolio summaries, trust deeds, inventories) ───
    # The file bytes live in the doc store (local volume / Cloudflare R2 / Blob);
    # this records the metadata + storage key and attaches it to an SFO.

    @abc.abstractmethod
    def add_document(self, doc: dict) -> int:
        """Record an uploaded document. Fields: sfo_id, name, doc_type, storage_key,
        byte_size, content_text (optional extracted text), uploaded_by. Returns id."""

    @abc.abstractmethod
    def get_document(self, document_id: int) -> dict | None:
        ...

    @abc.abstractmethod
    def list_documents(self, sfo_id: int | None = None, limit: int = 500) -> list[dict]:
        """Documents, newest first, enriched with the SFO name."""

    @abc.abstractmethod
    def delete_document(self, document_id: int) -> None:
        ...

    # ── Next actions (the pipeline calendar: consultations, follow-ups) ──────
    # A scheduled task tied to an SFO (and optionally a recommendation): book a
    # consultation, send a proposal, follow up. Powers the calendar + dashboard.

    @abc.abstractmethod
    def upsert_next_action(self, action: dict) -> int:
        """Insert/update by id (or create). Fields: sfo_id, recommendation_id
        (optional), kind (consultation/proposal/follow_up/review), title,
        due_date (ISO), status (open/done/cancelled), notes. Returns id."""

    @abc.abstractmethod
    def list_next_actions(self, sfo_id: int | None = None, status: str | None = None,
                          due_before: str | None = None, limit: int = 1000) -> list[dict]:
        """Next actions, soonest due first, enriched with the SFO name."""

    @abc.abstractmethod
    def set_next_action_status(self, action_id: int, status: str) -> None:
        ...

    @abc.abstractmethod
    def delete_next_action(self, action_id: int) -> None:
        ...

    # ── Portfolio: holdings + transactions ─────────────────────────────────
    # Mock holdings (funds, direct deals, luxury assets) with a performance
    # number, and cash-flow transactions (capital calls, distributions, buys).

    @abc.abstractmethod
    def add_holding(self, holding: dict) -> int:
        """Fields: sfo_id, name, asset_class, value_usd, performance_pct, notes."""

    @abc.abstractmethod
    def list_holdings(self, sfo_id: int) -> list[dict]:
        """Holdings for an SFO, largest value first."""

    @abc.abstractmethod
    def add_transaction(self, txn: dict) -> int:
        """Fields: sfo_id, txn_date (ISO), kind (capital_call/distribution/buy/sell/fee),
        amount_usd, description."""

    @abc.abstractmethod
    def list_transactions(self, sfo_id: int, limit: int = 200) -> list[dict]:
        """Transactions for an SFO, newest first."""

    # ── Demo helpers ───────────────────────────────────────────────────────
    @abc.abstractmethod
    def spread_demo_timestamps(self, days: int = 90) -> None:
        """Backdate conversations + recommendations created_at across the last
        ``days`` so activity-trend charts are realistic. Demo/seed only."""

    # ── Analytics (the admin/sales dashboard) ──────────────────────────────

    @abc.abstractmethod
    def stats(self) -> dict:
        """Top-line totals: sfos, services, conversations, recommendations,
        plus SFO counts by stage."""

    @abc.abstractmethod
    def service_interest_counts(self) -> list[dict]:
        """Per-service recommendation counts (the interest heatmap):
        ``{key, name, category, count}`` most-recommended first."""

    @abc.abstractmethod
    def upsell_funnel(self) -> dict:
        """Recommendation counts by status + summed ``est_value_usd`` of the
        accepted/booked pipeline."""

    @abc.abstractmethod
    def activity_trends(self, weeks: int = 12) -> list[dict]:
        """Weekly counts of new conversations + recommendations over the last
        ``weeks`` (see ``week_buckets``)."""
