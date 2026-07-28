"""Pipeline monitor — next-action due dates, urgency, and calendar roll-ups.

The SFO-domain analogue of FastFund's deadline monitor. A *next action* (book a
consultation, send a proposal, follow up, review) has a due date; this module
classifies its urgency relative to today and rolls the book up into a calendar
and a dashboard digest.
"""
from __future__ import annotations

from datetime import date, datetime

URGENCY_LABEL = {"overdue": "Overdue", "due_soon": "Due soon",
                 "upcoming": "Upcoming", "scheduled": "Scheduled", "done": "Done"}
URGENCY_COLOR = {"overdue": "#c0392b", "due_soon": "#b06b00",
                 "upcoming": "#102a43", "scheduled": "#7a7a85", "done": "#1c7c44"}


def _parse(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return datetime.fromisoformat(str(d)[:10]).date()
    except ValueError:
        return None


def urgency_of(due: date | None, status: str | None, today: date) -> str:
    if status in ("done", "cancelled"):
        return "done"
    if due is None:
        return "scheduled"
    days = (due - today).days
    if days < 0:
        return "overdue"
    if days <= 14:
        return "due_soon"
    if days <= 60:
        return "upcoming"
    return "scheduled"


def annotate(action: dict, today: date) -> dict:
    """Add ``urgency`` and ``days_out`` to a next-action row."""
    due = _parse(action.get("due_date"))
    a = dict(action)
    a["urgency"] = urgency_of(due, action.get("status"), today)
    a["days_out"] = (due - today).days if due else None
    return a


def pipeline_calendar(store, today: date, limit: int = 1000) -> list[dict]:
    """All open next-actions, annotated and sorted soonest-due first."""
    rows = [annotate(a, today) for a in store.list_next_actions(status="open", limit=limit)]
    rows.sort(key=lambda r: (r["days_out"] is None, r["days_out"] if r["days_out"] is not None else 10**6))
    return rows


def deadline_digest(store, today: date, horizon_days: int = 90) -> dict:
    """Overdue + soon buckets for the dashboard."""
    rows = pipeline_calendar(store, today)
    overdue = [r for r in rows if r["urgency"] == "overdue"]
    upcoming = [r for r in rows if r["urgency"] in ("due_soon", "upcoming")
                and (r["days_out"] is None or r["days_out"] <= horizon_days)]
    return {"overdue": overdue, "upcoming": upcoming,
            "counts": {"overdue": len(overdue), "upcoming": len(upcoming)}}
