"""SFO Hub storage — compatibility shim.

Storage is pluggable (see the ``storage`` package, selected by the
``DATA_STORAGE`` env var: ``neo4j`` default, or ``sqlite``). This module gives the
app, agents, cross-sell engine and seeder a single ``import sfostore as store``
surface — every ``store.<method>(...)`` call is forwarded to the active backend
instance — so swapping the backend is invisible to callers.
"""

from __future__ import annotations

from storage import get_store, reset_store, utcnow  # noqa: F401  (re-exported)


def __getattr__(name: str):
    """Forward attribute access to the configured storage backend, e.g.
    ``store.upsert_sfo(...)`` → ``get_store().upsert_sfo(...)``."""
    return getattr(get_store(), name)


if __name__ == "__main__":
    store = get_store()
    store.init_db()
    print("Initialized SFO Hub storage:", type(store).__name__)
    print(store.stats())
