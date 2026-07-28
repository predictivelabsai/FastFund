"""Per-request conversation context for the agent tools.

The chat usually happens *about* a selected family office. Rather than thread the
SFO id through every tool signature, the web route stamps it here before
streaming; the specialist tools read it. A ``contextvar`` keeps it correct under
concurrent SSE streams.
"""
from __future__ import annotations

from contextvars import ContextVar

_active_sfo: ContextVar[int | None] = ContextVar("active_sfo", default=None)


def set_active_sfo(sfo_id: int | None) -> None:
    _active_sfo.set(sfo_id)


def active_sfo() -> int | None:
    return _active_sfo.get()
