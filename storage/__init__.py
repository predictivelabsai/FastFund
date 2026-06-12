"""Pluggable storage for TaxHub.

The active backend is chosen by the ``DATA_STORAGE`` environment variable:

* ``neo4j``  (default) — graph backend, powers graph-RAG retrieval.
* ``sqlite`` — relational backend (also reachable as Postgres via ``DB_URL``).

Callers use ``get_store()`` (or the ``taxstore`` compat shim) and never import a
concrete backend, so swapping is a one-line env change.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from .base import Storage, utcnow

load_dotenv()

_store: Storage | None = None


def get_store() -> Storage:
    """Return the process-wide storage backend singleton."""
    global _store
    if _store is None:
        _store = _build_store()
    return _store


def _build_store() -> Storage:
    backend = os.environ.get("DATA_STORAGE", "neo4j").strip().lower()
    if backend == "sqlite":
        from .sqlite_store import SqliteStore
        return SqliteStore()
    if backend == "neo4j":
        from .neo4j_store import Neo4jStore
        return Neo4jStore()
    raise ValueError(
        f"Unknown DATA_STORAGE={backend!r}; expected 'neo4j' or 'sqlite'."
    )


def reset_store() -> None:
    """Drop the cached singleton (tests switch backends with this)."""
    global _store
    _store = None


__all__ = ["Storage", "get_store", "reset_store", "utcnow"]
