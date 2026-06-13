"""The Monitor phase — turn deadline *rules* into concrete dates.

The Determine engine ([[obligations]]) carries each obligation's deadline as the
expert-curated *rule text* ("Within 6 months of the financial year-end",
"30 November of the year following the year of assessment", …) plus the entity's
financial year-end. This module resolves that pair into a real calendar date so
we can build a deadline calendar, flag overdue/due-soon filings, and drive
alert digests.

It is **deterministic** — a small ordered set of regex patterns over the
real deadline strings in ``config/tax_forms.yaml``, no model in the loop. Every
resolution returns a ``basis`` string (how the date was derived) and an
``indicative`` flag (True where the rule is approximate or assumption-laden,
e.g. "generally", "~1 month", multiple dates, or a convention like Year of
Assessment). Rules that are not independently datable from a FY-end alone —
filed-with-the-return, anniversary-of-formation, relative-to-a-notice — resolve
to ``due=None`` with an explanatory basis rather than a fabricated date.

No persistence: dates are computed on the fly from the obligation + entity, so
re-running Determine or changing an entity's FY-end re-dates everything for free.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}

# Statuses that close an obligation — never overdue, shown as done.
_DONE = {"filed", "confirmed", "na"}

# Urgency thresholds (days from today), and their display colours.
_DUE_SOON_DAYS = 30
_UPCOMING_DAYS = 90
URGENCY_COLOR = {"done": "#1c7c44", "overdue": "#c0392b", "due_soon": "#b06b00",
                 "upcoming": "#2c6fb0", "scheduled": "#7a7a85", "undated": "#9a93a6"}
URGENCY_LABEL = {"done": "Done", "overdue": "Overdue", "due_soon": "Due soon",
                 "upcoming": "Upcoming", "scheduled": "Scheduled", "undated": "No fixed date"}


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def add_months(d: date, n: int) -> date:
    """Add ``n`` months to ``d``, clamping the day to the target month length."""
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, _last_day(y, m)))


def parse_fy_end(fy_end: str | None, today: date) -> date | None:
    """Resolve a ``"DD Month"`` financial year-end to the close of the most
    recent *completed* financial year (the latest such date strictly before
    today). Returns None if the string can't be parsed."""
    if not fy_end:
        return None
    m = re.search(r"(\d{1,2})\s+([a-z]+)", fy_end.strip().lower())
    if not m:
        return None
    day, mon = int(m.group(1)), MONTHS.get(m.group(2))
    if not mon or not (1 <= day <= _last_day(today.year, mon)):
        return None
    cand = date(today.year, mon, min(day, _last_day(today.year, mon)))
    if cand >= today:                       # this year's hasn't closed yet
        cand = date(today.year - 1, mon, min(day, _last_day(today.year - 1, mon)))
    return cand


def _next_occurrence(month: int, day: int, today: date) -> date:
    """Next calendar occurrence of DD/Month on or after today."""
    y = today.year
    d = date(y, month, min(day, _last_day(y, month)))
    if d < today:
        d = date(y + 1, month, min(day, _last_day(y + 1, month)))
    return d


def _soft_indicative(rule: str) -> bool:
    return any(w in rule for w in ("generally", "approx", "~", "later", "estimate",
                                   "cantonal", "before ", "block extension", "extension"))


def resolve_due_date(deadline: str | None, fy_end: date | None,
                     today: date) -> dict:
    """Resolve a deadline rule (+ resolved FY-end) to ``{due, basis, indicative}``.

    ``due`` is a ``date`` or ``None`` (rule not independently datable). ``basis``
    explains the derivation; ``indicative`` flags an approximate/assumption-laden
    date the user should sanity-check."""
    rule = (deadline or "").strip()
    if not rule:
        return {"due": None, "basis": "No deadline recorded", "indicative": False}
    r = rule.lower()
    soft = _soft_indicative(r)

    # ── Not independently datable from a FY-end ──────────────────────────────
    if re.search(r"\bfiled with\b", r) or re.search(r"\bwith the .*return\b", r):
        return {"due": None, "basis": "Filed with the tax return", "indicative": False}
    if "anniversary" in r:
        return {"due": None, "basis": "Anniversary of formation — incorporation date not held",
                "indicative": True}
    if "date of issue" in r or "before payment" in r or "before / " in r:
        return {"due": None, "basis": "Relative to a notice/payment date not held",
                "indicative": True}

    # ── Datable patterns (need a FY-end) ─────────────────────────────────────
    if fy_end is not None:
        # "Later of N months after year-end or DD Month following …"
        if "later of" in r:
            m1 = re.search(r"(\d+)\s+months?", r)
            m2 = re.search(r"(\d{1,2})\s+([a-z]+)\s+following", r)
            cands = []
            if m1:
                cands.append(add_months(fy_end, int(m1.group(1))))
            if m2 and MONTHS.get(m2.group(2)):
                cands.append(date(fy_end.year + 1, MONTHS[m2.group(2)],
                                  min(int(m2.group(1)), _last_day(fy_end.year + 1, MONTHS[m2.group(2)]))))
            if cands:
                return {"due": max(cands), "basis": "Later of two statutory dates",
                        "indicative": True}

        # "DD Month of the second year following …"
        m = re.search(r"(\d{1,2})\s+([a-z]+)\s+of the second year following", r)
        if m and MONTHS.get(m.group(2)):
            mon = MONTHS[m.group(2)]
            return {"due": date(fy_end.year + 2, mon, min(int(m.group(1)), _last_day(fy_end.year + 2, mon))),
                    "basis": "Fixed date, 2nd year after year-end", "indicative": soft}

        # "DD Month of the (year following | following year)"
        m = re.search(r"(\d{1,2})\s+([a-z]+)\s+of (?:the year following|the following year)", r)
        if m and MONTHS.get(m.group(2)):
            mon = MONTHS[m.group(2)]
            return {"due": date(fy_end.year + 1, mon, min(int(m.group(1)), _last_day(fy_end.year + 1, mon))),
                    "basis": "Fixed date, year after year-end", "indicative": soft}

        # "DD Month of the Year of Assessment" (YA ≈ year after the basis period)
        m = re.search(r"(\d{1,2})\s+([a-z]+)\s+of the year of assessment", r)
        if m and MONTHS.get(m.group(2)):
            mon = MONTHS[m.group(2)]
            return {"due": date(fy_end.year + 1, mon, min(int(m.group(1)), _last_day(fy_end.year + 1, mon))),
                    "basis": "Year of Assessment (year after basis period)", "indicative": True}

        # "Last business day of Month of the year following …"
        m = re.search(r"last business day of ([a-z]+) of the year following", r)
        if m and MONTHS.get(m.group(1)):
            mon = MONTHS[m.group(1)]
            return {"due": date(fy_end.year + 1, mon, _last_day(fy_end.year + 1, mon)),
                    "basis": "Last business day, year after year-end", "indicative": True}

        # "Nth day of the Mth month after …" (e.g. 15th day of the 4th month)
        m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+)?of the (\d{1,2})(?:st|nd|rd|th)?\s+month after", r)
        if m:
            base = add_months(fy_end, int(m.group(2)))
            return {"due": date(base.year, base.month, min(int(m.group(1)), _last_day(base.year, base.month))),
                    "basis": f"{m.group(1)} day of month {m.group(2)} after year-end", "indicative": soft}

        # "end of the Nth month after …"
        m = re.search(r"end of the (\d{1,2})(?:st|nd|rd|th)?\s+month after", r)
        if m:
            base = add_months(fy_end, int(m.group(1)))
            return {"due": date(base.year, base.month, _last_day(base.year, base.month)),
                    "basis": f"End of month {m.group(1)} after year-end", "indicative": soft}

        # Generic "N months after / within N months of … year-end / period"
        m = re.search(r"(\d{1,2})\s+months?", r)
        if m:
            due = add_months(fy_end, int(m.group(1)))
            if "and 1 day" in r:
                due += timedelta(days=1)
            elif "end of the month" in r:
                due = date(due.year, due.month, _last_day(due.year, due.month))
            return {"due": due, "basis": f"{m.group(1)} months after year-end", "indicative": soft}

    # ── Fixed recurring annual date (no FY-end needed) ───────────────────────
    m = re.search(r"(\d{1,2})\s+([a-z]+)", r)
    if m and MONTHS.get(m.group(2)):
        mon, day = MONTHS[m.group(2)], int(m.group(1))
        multiple = ";" in r or " or " in r
        return {"due": _next_occurrence(mon, min(day, _last_day(today.year, mon)), today),
                "basis": "Fixed annual date", "indicative": soft or multiple}

    return {"due": None, "basis": rule[:60], "indicative": True}


def urgency_of(due: date | None, status: str | None, today: date) -> str:
    if (status or "") in _DONE:
        return "done"
    if due is None:
        return "undated"
    days = (due - today).days
    if days < 0:
        return "overdue"
    if days <= _DUE_SOON_DAYS:
        return "due_soon"
    if days <= _UPCOMING_DAYS:
        return "upcoming"
    return "scheduled"


def annotate(ob: dict, entity: dict | None, today: date) -> dict:
    """Return ``ob`` enriched with ``due`` (ISO or None), ``due_basis``,
    ``indicative``, ``urgency``, ``days_out`` (signed int or None)."""
    fy = parse_fy_end((entity or {}).get("fy_end"), today)
    res = resolve_due_date(ob.get("deadline"), fy, today)
    due = res["due"]
    out = dict(ob)
    out["due"] = due.isoformat() if due else None
    out["due_basis"] = res["basis"]
    out["indicative"] = res["indicative"]
    out["urgency"] = urgency_of(due, ob.get("status"), today)
    out["days_out"] = (due - today).days if due else None
    return out


def portfolio_calendar(store, today: date) -> list[dict]:
    """Every obligation across the portfolio, annotated with a resolved due date
    and entity name, sorted by due date (dated first, ascending; undated last)."""
    ents = {e["id"]: e for e in store.list_entities(limit=5000)}
    rows = []
    for ob in store.list_obligations(limit=20000):
        a = annotate(ob, ents.get(ob.get("entity_id")), today)
        e = ents.get(ob.get("entity_id"))
        a["entity_name"] = e["name"] if e else "—"
        rows.append(a)
    rows.sort(key=lambda r: (r["due"] is None, r["due"] or "9999"))
    return rows


def deadline_digest(store, today: date, horizon_days: int = 90) -> dict:
    """Summarise the calendar for alerting: overdue + upcoming (≤ horizon)
    open obligations, plus counts. The basis for an email/Slack digest."""
    cal = portfolio_calendar(store, today)
    overdue = [r for r in cal if r["urgency"] == "overdue"]
    upcoming = [r for r in cal if r["urgency"] in ("due_soon", "upcoming")
                and r["days_out"] is not None and r["days_out"] <= horizon_days]
    return {"generated": today.isoformat(), "horizon_days": horizon_days,
            "overdue": overdue, "upcoming": upcoming,
            "counts": {"overdue": len(overdue), "upcoming": len(upcoming),
                       "total_open": sum(1 for r in cal if r["urgency"] not in ("done",))}}
