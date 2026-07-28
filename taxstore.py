"""TaxHub storage — compatibility shim.

Persistence used to live here against SQLite. Storage is now pluggable (see the
``storage`` package, selected by the ``DATA_STORAGE`` env var: ``neo4j`` default,
or ``sqlite``). This module preserves the historical ``import taxstore as store``
surface — every ``store.<method>(...)`` call is forwarded to the active backend
instance — so the scraper, web app and tools are unchanged by the swap.
"""

from __future__ import annotations

from storage import get_store, reset_store, utcnow  # noqa: F401  (re-exported)


def __getattr__(name: str):
    """Forward any attribute access to the configured storage backend.

    e.g. ``store.upsert_document(...)`` resolves to
    ``get_store().upsert_document(...)``.
    """
    return getattr(get_store(), name)


if __name__ == "__main__":
    store = get_store()
    store.init_db()
    print("Initialized TaxHub storage:", type(store).__name__)
    print(store.stats())
