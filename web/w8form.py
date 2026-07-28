"""W-8BEN-E auto-fill — a conceptual demo of an agent completing a US tax form.

The right-hand pane normally shows the changes feed or a PDF viewer. This module
backs a third mode: a **W-8BEN-E facsimile** (Certificate of Foreign Status of
Beneficial Owner, the entity form) that an agent fills in live from what FastFund
already knows about the entity, then validates.

Nothing is fabricated from thin air: every value is *sourced* from the entity
record + its AEOI profile ([[aeoi]]). Because that profile already carries the
FATCA classification, GIIN, foreign TIN, treaty position and self-cert dates, the
W-8 is essentially a re-presentation of it in the IRS form's own structure — and
the same validation rules that drive the AEOI dashboard light up the form's
weak spots (missing GIIN, treaty claim without a TIN, a lapsed certification).

This is a demo facsimile, not a filable IRS PDF; a human edits any field and
signs off. ``fill()`` returns the ordered fields (with per-field validation
badges); the web layer renders them as editable inputs and animates them in.
"""
from __future__ import annotations

from datetime import date

from web import aeoi

FORM_TITLE = "Form W-8BEN-E"
FORM_SUBTITLE = ("Certificate of Status of Beneficial Owner for United States Tax "
                 "Withholding and Reporting (Entities)")

# A plausible registered-office line per domicile, so the address field looks real.
_ADDRESSES = {
    "KY": "Ugland House, South Church Street, George Town, Cayman Islands",
    "JE": "44 Esplanade, St Helier, Jersey JE4 9WG",
    "GG": "Glategny Court, Glategny Esplanade, St Peter Port, Guernsey GY1 1WR",
    "LU": "6 rue Eugène Ruppert, L-2453 Luxembourg",
    "IE": "70 Sir John Rogerson's Quay, Dublin 2, D02 R296, Ireland",
    "VG": "Craigmuir Chambers, Road Town, Tortola, British Virgin Islands",
    "BM": "Clarendon House, 2 Church Street, Hamilton HM 11, Bermuda",
}

# Chapter 3 status (entity type) inferred from the FastFund entity type.
_CH3 = {"fund": "Corporation", "spv": "Corporation", "holdco": "Corporation",
        "company": "Corporation", "gp": "Partnership", "trust": "Simple trust"}

# A demo signatory per entity type — purely illustrative.
_CAPACITY = {"gp": "General Partner", "trust": "Trustee"}


def _country(code: str | None, jur_names: dict | None = None) -> str:
    names = jur_names or {}
    return names.get(code, code or "—")


def _signatory(entity: dict) -> str:
    # Stable demo name from the AEOI controlling-person pool / entity seed.
    h = aeoi._seed(entity)
    return aeoi._CP_NAMES[h % len(aeoi._CP_NAMES)]


def fill(entity: dict, jur_names: dict | None = None,
         today: date | None = None) -> dict:
    """Produce the W-8BEN-E field set for ``entity``, each field carrying a
    sourced value and a validation badge. Returns ``{title, subtitle, sections,
    readiness, counts}`` where ``sections`` is ``[{part, heading, fields}]`` and
    each field is ``{id, line, label, value, hint, badge}`` (badge ∈
    ok|error|warning|empty)."""
    today = today or date.today()
    v = aeoi.validate(entity, today=today)
    p = v["profile"]
    cls = p["classification"]
    sc = p["self_cert"]
    etype = (entity.get("type") or "").lower()

    # Index findings by the W-8 line they bear on, so we can badge that field.
    by_rule = {f["rule"]: f for f in v["findings"]}

    def badge_for(rules, value):
        for r in rules:
            f = by_rule.get(r)
            if f:
                return f["severity"], f["message"]
        if value in (None, "", "—"):
            return "empty", ""
        return "ok", ""

    treaty = sc.get("treaty_claim")
    giin = p.get("giin")
    ftin = sc.get("foreign_tin")

    def field(fid, line, label, value, hint="", rules=()):
        sev, msg = badge_for(rules, value)
        return {"id": fid, "line": line, "label": label,
                "value": value if value not in (None, "") else "",
                "hint": (msg or hint), "badge": sev}

    part1 = [
        field("name", "1", "Name of organization (beneficial owner)", entity.get("name")),
        field("country", "2", "Country of incorporation or organization",
              _country(entity.get("domicile"), jur_names)),
        field("ch3", "4", "Chapter 3 status (entity type)",
              _CH3.get(etype, "Corporation")),
        field("ch4", "5", "Chapter 4 (FATCA) status", cls["fatca"]),
        field("address", "6", "Permanent residence address",
              _ADDRESSES.get(entity.get("domicile"),
                             f"Registered office, {_country(entity.get('domicile'), jur_names)}")),
        field("giin", "9a", "GIIN", giin,
              hint="Required for a registered FFI", rules=("FATCA_FFI_NO_GIIN",)),
        field("ftin", "9b", "Foreign TIN", ftin,
              hint="Needed to support a treaty claim", rules=("W8_TREATY_NO_TIN",)),
        field("ref", "10", "Reference number(s)", entity.get("client_ref")),
    ]
    part3 = [
        field("treaty_country", "14a", "Country of residence for treaty claim",
              _country(entity.get("domicile"), jur_names) if treaty else "",
              hint="Only if claiming income-tax-treaty benefits"),
        field("treaty_article", "15", "Treaty article & withholding rate",
              "Article 10 (dividends) — 15%" if treaty else "",
              hint="Cite the specific article, paragraph and rate"),
    ]
    fatca_part = "Active NFFE (Part XXV)" if cls["fatca"] == aeoi.FATCA_ACTIVE_NFFE else (
        "Passive NFFE (Part XXVI)" if cls["fatca"] == aeoi.FATCA_PASSIVE_NFFE else
        "FFI certification (per Chapter 4 status)")
    part4 = [
        field("fatca_cert", "—", "Chapter 4 status certification", fatca_part,
              hint="The certification part keyed to the FATCA status on line 5"),
    ]
    cert = [
        field("sign_name", "—", "Print name of signatory", _signatory(entity)),
        field("sign_capacity", "—", "Capacity in which acting",
              _CAPACITY.get(etype, "Director / Authorized officer")),
        field("sign_date", "—", "Date signed", sc.get("signed"),
              hint="W-8BEN-E is valid ~3 years from signing",
              rules=("W8_EXPIRED", "W8_EXPIRING")),
    ]
    sections = [
        {"part": "Part I", "heading": "Identification of Beneficial Owner", "fields": part1},
        {"part": "Part III", "heading": "Claim of Tax Treaty Benefits", "fields": part3},
        {"part": "Part IV–XXVIII", "heading": "Chapter 4 (FATCA) Certification", "fields": part4},
        {"part": "Part XXX", "heading": "Certification & Signature", "fields": cert},
    ]
    return {"title": FORM_TITLE, "subtitle": FORM_SUBTITLE,
            "sections": sections, "readiness": v["readiness"],
            "counts": v["counts"], "entity_id": entity.get("id"),
            "entity_name": entity.get("name")}


def revalidate(entity: dict, values: dict, today: date | None = None) -> dict:
    """Re-check the W-8 against the user's *edited* field values (not the derived
    profile) so correcting a field clears its badge. Returns ``{badges, counts,
    readiness}`` where ``badges`` maps field id → ``{badge, hint}``."""
    today = today or date.today()
    cls = aeoi.validate(entity, today=today)["profile"]["classification"]
    badges, errors, warnings = {}, 0, 0

    def set_badge(fid, sev, hint=""):
        nonlocal errors, warnings
        badges[fid] = {"badge": sev, "hint": hint}
        if sev == "error":
            errors += 1
        elif sev == "warning":
            warnings += 1

    g = lambda k: (values.get(k) or "").strip()  # noqa: E731

    # GIIN required for an FFI (Chapter 4 status names an FFI).
    ch4 = g("ch4")
    if "FFI" in ch4 and not g("giin"):
        set_badge("giin", "error", "Required for a registered FFI — none entered.")
    elif g("giin"):
        set_badge("giin", "ok")
    else:
        set_badge("giin", "empty")

    # Treaty claim without a foreign TIN.
    treaty = bool(g("treaty_country") or g("treaty_article"))
    if treaty and not g("ftin"):
        set_badge("ftin", "error", "Treaty claimed but no foreign TIN — rate invalid.")
    elif g("ftin"):
        set_badge("ftin", "ok")
    else:
        set_badge("ftin", "empty")

    # Signature date drives W-8 validity (~3 years).
    sd = g("sign_date")
    if sd:
        try:
            signed = date.fromisoformat(sd[:10])
            exp = aeoi._add_years(signed, aeoi.W8_VALIDITY_YEARS)
            days = (exp - today).days
            if days < 0:
                set_badge("sign_date", "error",
                          f"Certification lapsed on {exp:%d %b %Y}.")
            elif days <= aeoi._EXPIRING_SOON_DAYS:
                set_badge("sign_date", "warning",
                          f"Expires {exp:%d %b %Y} (in {days} days).")
            else:
                set_badge("sign_date", "ok")
        except ValueError:
            set_badge("sign_date", "warning", "Unparseable date.")
    else:
        set_badge("sign_date", "empty")

    # Required identity fields.
    for fid in ("name", "country", "ch3", "ch4", "address"):
        set_badge(fid, "ok" if g(fid) else "error",
                  "" if g(fid) else "Required field is empty.")

    readiness = ("not_ready" if errors else "review" if warnings else "ready")
    return {"badges": badges, "counts": {"error": errors, "warning": warnings},
            "readiness": readiness}
