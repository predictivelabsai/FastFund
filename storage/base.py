"""Storage interface for TaxHub.

The persistence backbone is pluggable: a relational backend (SQLite, and by
extension Postgres) and a graph backend (Neo4j) both implement this one
interface, selected at runtime by the ``DATA_STORAGE`` environment variable.

Everything the scraper produces — jurisdictions, documents, every captured
version, detected changes, AI change summaries and citations — flows through
these methods. No caller (scraper, web app, demo tools) touches a backend
directly; that is what makes the backend swappable.

Return shapes are plain ``dict`` / ``list[dict]`` so the web layer and CLI are
identical regardless of backend. Timestamps are ISO-8601 UTC strings.
"""

from __future__ import annotations

import abc
from datetime import datetime, timezone


def utcnow() -> str:
    """ISO-8601 UTC timestamp string (no backend stores a native tz)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage(abc.ABC):
    """Backend-neutral persistence + read API for TaxHub.

    A document has many immutable versions; whenever a freshly scraped
    version's content hash differs from the stored current version, a new
    version row/node is appended (history is never mutated) and a change is
    recorded — the audit trail a fund back office needs for "what changed,
    and when".
    """

    # ── Lifecycle ──────────────────────────────────────────────────────────

    @abc.abstractmethod
    def init_db(self) -> None:
        """Create schema/constraints/indexes and seed the admin user. Idempotent."""

    # ── Jurisdictions / documents (write) ──────────────────────────────────

    @abc.abstractmethod
    def upsert_jurisdiction(self, code: str, name: str, region: str | None = None,
                            authority: str | None = None) -> None:
        ...

    @abc.abstractmethod
    def upsert_document(self, doc: dict) -> int:
        """Insert or update a tracked document; return its id.

        ``doc`` keys: jurisdiction_code, source, doc_type, doc_key, title,
        reference, url, format, tags (list).
        """

    @abc.abstractmethod
    def add_version(self, document_id: int, content_hash: str, text_content: str,
                    raw_path: str | None = None, byte_size: int | None = None,
                    http_last_modified: str | None = None,
                    etag: str | None = None) -> dict:
        """Append a new immutable version and point the document at it.

        Returns ``{id, version_no}``.
        """

    @abc.abstractmethod
    def mark_checked(self, document_id: int, error: str | None = None) -> None:
        """Stamp last_checked / increment check_count; set status from ``error``."""

    @abc.abstractmethod
    def record_change(self, document_id: int, from_version_id: int | None,
                      to_version_id: int, change_type: str, added_chars: int = 0,
                      removed_chars: int = 0, diff_text: str | None = None,
                      ai_summary: str | None = None, ai_impact: str | None = None,
                      ai_model: str | None = None) -> int:
        """Record a detected change between two versions; return its id."""

    @abc.abstractmethod
    def add_citations(self, document_id: int, version_id: int,
                      cites: list[dict]) -> int:
        """Attach statutory cross-references to a version; return count added."""

    @abc.abstractmethod
    def overwrite_version_content(self, version_id: int, document_id: int,
                                  text_content: str, content_hash: str) -> None:
        """Rewrite a stored version's text + hash in place (demo/repair only).

        This is the one mutation of history we allow, used by ``demo_change.py``
        to simulate a prior version so an amendment + diff can be demonstrated.
        """

    # ── Scrape runs ────────────────────────────────────────────────────────

    @abc.abstractmethod
    def start_run(self, jurisdiction_code: str) -> int:
        ...

    @abc.abstractmethod
    def finish_run(self, run_id: int, checked: int, new: int, changed: int,
                   errors: int, status: str = "done",
                   notes: str | None = None) -> None:
        ...

    # ── Reads ──────────────────────────────────────────────────────────────

    @abc.abstractmethod
    def get_document(self, jurisdiction_code: str, doc_key: str) -> dict | None:
        ...

    @abc.abstractmethod
    def get_document_by_id(self, document_id: int) -> dict | None:
        ...

    @abc.abstractmethod
    def latest_version(self, document_id: int) -> dict | None:
        ...

    @abc.abstractmethod
    def list_versions(self, document_id: int) -> list[dict]:
        """Versions of a document, newest first."""

    @abc.abstractmethod
    def recent_changes(self, limit: int = 50,
                       jurisdiction_code: str | None = None) -> list[dict]:
        """Changes newest-first, enriched with document title/jurisdiction/type/url."""

    @abc.abstractmethod
    def list_changes_for_document(self, document_id: int) -> list[dict]:
        """Changes for one document, newest first, enriched like ``recent_changes``."""

    @abc.abstractmethod
    def get_change(self, change_id: int) -> dict | None:
        """One change, enriched with document title + jurisdiction_code."""

    @abc.abstractmethod
    def list_citations(self, document_id: int, limit: int = 40) -> list[dict]:
        """Distinct statutory references for a document.

        Each row: ``{cited_instrument, cited_locator}``.
        """

    @abc.abstractmethod
    def get_jurisdiction(self, code: str) -> dict | None:
        ...

    @abc.abstractmethod
    def list_jurisdictions_with_counts(self) -> list[dict]:
        """Jurisdictions ordered by name, each with a ``docs`` count."""

    @abc.abstractmethod
    def list_documents_for_jurisdiction(self, code: str) -> list[dict]:
        """Documents in a jurisdiction (with a ``versions`` count), ordered by type/title."""

    @abc.abstractmethod
    def stats(self) -> dict:
        """Corpus totals + per-jurisdiction document counts."""

    # ── Users / auth ───────────────────────────────────────────────────────

    @abc.abstractmethod
    def get_user_by_email(self, email: str) -> dict | None:
        """Return ``{id, password_hash}`` for a user, or None."""

    # ── Graph-RAG retrieval ────────────────────────────────────────────────

    @abc.abstractmethod
    def search_versions(self, query: str, limit: int = 8) -> list[dict]:
        """Full-text search over version text for the RAG retriever.

        Returns the best-matching *current* document versions, each as
        ``{document_id, doc_key, title, jurisdiction_code, doc_type, url,
        reference, version_no, text_content, score}``. Backends without a
        full-text index fall back to a substring scan.
        """

    # ── Vector retrieval (chunk-level embeddings) ──────────────────────────
    # Optional semantic layer: each version's text is split into passage
    # chunks, each embedded, and stored for nearest-neighbour search. Powers
    # the vector and hybrid retrievers; the full-text path works without it.

    @abc.abstractmethod
    def ensure_vector_index(self, dim: int) -> None:
        """Create the chunk vector index for ``dim``-dimensional embeddings
        (cosine). No-op if it already exists; relational backends may no-op."""

    @abc.abstractmethod
    def index_version_chunks(self, version_id: int, document_id: int,
                             chunks: list[tuple[int, str, list[float]]]) -> int:
        """Replace the stored chunks for ``version_id`` with ``chunks``
        (``(ord, text, embedding)`` tuples). Idempotent. Returns the count."""

    @abc.abstractmethod
    def vector_search_chunks(self, query_vector: list[float],
                             limit: int = 8) -> list[dict]:
        """Nearest chunks to ``query_vector`` whose version is its document's
        CURRENT version. Each row: ``{document_id, doc_key, title,
        jurisdiction_code, doc_type, url, reference, chunk_text, score}``."""

    @abc.abstractmethod
    def count_chunks(self) -> int:
        """Number of stored chunk embeddings (0 ⇒ vector retrieval unavailable)."""
