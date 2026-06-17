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
