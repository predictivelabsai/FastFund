"""The AEOI engine — entity classification + W-8 / CRS / FATCA validation.

Where the Determine engine ([[obligations]]) answers *which* AEOI filing an entity
owes and the Monitor phase ([[monitor]]) answers *when*, this module answers the
question the back office actually gets stuck on: **is the entity ready to file?**

That readiness — the heart of products like Quantios eFileConnect / vPoint — turns
on three things an entity must hold *before* a CRS/FATCA return can be produced:

  1. a **classification** under each regime (CRS: Financial Institution vs Active /
     Passive NFE; FATCA: FFI vs NFFE, plus a GIIN for registered FFIs);
  2. valid **self-certification / W-8 documentation** (W-8BEN-E for an entity, with
     a treaty claim where relevant) that has not lapsed; and
  3. for a Passive NFE, identified **controlling persons** with a tax residence and
     a TIN so they can be reported.

Like Monitor, this is **deterministic and persistence-free**: an entity's AEOI
profile is *derived* from its existing attributes (type, domicile, jurisdictions,
activities) so the feature works for every entity — seeded or user-added — with no
schema change to either backend. Dates age against ``today`` so the demo stays
live. A small, stable per-entity seed (hash of the client ref) varies the data and
plants realistic defects (a lapsed W-8, a missing GIIN, an undocumented controlling
person) so the validation rules below have something to find.

The validation rules are codified in ``RULES`` and run by ``validate()`` →
``findings`` (each with a severity + plain-English fix) → a per-entity
``readiness`` verdict. Nothing here is filed automatically; a human still signs off.
"""
from __future__ import annotations

import hashlib
from datetime import date

# ── Classification vocabularies ──────────────────────────────────────────────
# CRS (OECD Common Reporting Standard) entity classifications.
CRS_FI = "Investment Entity"               # a Financial Institution sub-type
CRS_ACTIVE_NFE = "Active NFE"
CRS_PASSIVE_NFE = "Passive NFE"

# FATCA (US) classifications.
FATCA_FFI = "Reporting Model 1 FFI"        # a Foreign Financial Institution
FATCA_ACTIVE_NFFE = "Active NFFE"
FATCA_PASSIVE_NFFE = "Passive NFFE"

# Self-certification / W-8 documentation form an entity gives its account-keeping
# institution (the withholding agent) — never filed with the IRS by the holder.
W8BEN_E = "W-8BEN-E"        # foreign entity beneficial owner
W8IMY = "W-8IMY"           # intermediary / flow-through (e.g. a partnership/GP)
CRS_SELF_CERT = "CRS Self-Certification"

# A W-8BEN-E is, by IRS rule, valid until the last day of the 3rd calendar year
# after signing (absent a change in circumstances). We model the validity window
# as three years for a concrete, demo-friendly expiry date.
W8_VALIDITY_YEARS = 3
_EXPIRING_SOON_DAYS = 90

SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}
READINESS = {
    "not_ready": {"label": "Not ready", "color": "#c0392b"},
    "review":    {"label": "Needs review", "color": "#b06b00"},
    "ready":     {"label": "Filing-ready", "color": "#1c7c44"},
}

_CP_NAMES = ["A. Petrov", "M. Khan", "S. Okonkwo", "L. Dubois", "R. Tan",
             "E. Rossi", "J. Andersson", "N. Haddad", "C. Mbeki", "F. Costa"]
_CP_RESIDENCES = ["GB", "DE", "FR", "US", "CH", "AE", "SG", "ZA", "BR", "NL"]


def _seed(entity: dict) -> int:
    """Stable per-entity integer seed (hash of client_ref or name) — varies the
    derived profile deterministically so re-runs are identical."""
    key = (entity.get("client_ref") or entity.get("name") or "").encode()
    return int(hashlib.sha1(key).hexdigest(), 16)


def _add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:            # 29 Feb → 28 Feb
        return d.replace(year=d.year + n, day=28)


def _classify(entity: dict) -> dict:
    """Derive CRS + FATCA classifications from the entity's own attributes.

    Funds, GPs and professionally-managed trusts are Investment Entities (FIs);
    holdcos/SPVs/holding companies whose income is passive are Passive NFEs.
    """
    etype = (entity.get("type") or "").lower()
    acts = {a.lower() for a in (entity.get("activities") or [])}
    if etype in {"fund", "gp"} or "fund management" in acts:
        crs, fatca, is_fi = CRS_FI, (W8IMY if etype == "gp" else FATCA_FFI), True
    elif etype == "trust":
        crs, fatca, is_fi = CRS_FI, FATCA_FFI, True       # managed trust = FI
    elif acts and acts <= {"holding"} or etype in {"holdco", "spv"}:
        crs, fatca, is_fi = CRS_PASSIVE_NFE, FATCA_PASSIVE_NFFE, False
    elif acts - {"holding"}:                              # has trading activity
        crs, fatca, is_fi = CRS_ACTIVE_NFE, FATCA_ACTIVE_NFFE, False
    else:
        crs, fatca, is_fi = CRS_PASSIVE_NFE, FATCA_PASSIVE_NFFE, False
    return {"crs": crs, "fatca": fatca, "is_fi": is_fi,
            "is_passive": crs == CRS_PASSIVE_NFE}


def derive_profile(entity: dict, today: date | None = None) -> dict:
    """Build an entity's full AEOI profile: classification, GIIN, self-cert
    documentation and (for Passive NFEs) controlling persons. Deterministic."""
    today = today or date.today()
    h = _seed(entity)
    cls = _classify(entity)
    reports_to_us = "US" in (entity.get("jurisdictions") or [])

    # GIIN — required for a registered FFI. Plant a gap on ~1 in 4 FFIs.
    giin = None
    if cls["fatca"] == FATCA_FFI:
        giin = None if (h % 4 == 0) else (
            f"{hashlib.sha1(str(h).encode()).hexdigest()[:6].upper()}."
            f"{h % 99999:05d}.SL.{(entity.get('domicile') or 'XX')[:2].upper()}")

    # Self-certification / W-8 documentation, aged off `today` for a live spread.
    form = W8IMY if cls["fatca"] == W8IMY else (
        W8BEN_E if reports_to_us or cls["is_fi"] else CRS_SELF_CERT)
    years_ago = 1 + (h % 5)                       # 1..5 → some lapse the 3-yr window
    days_jit = (h // 7) % 270
    signed = date(today.year - years_ago, 1 + (h % 12), 1 + (h % 27))
    from datetime import timedelta
    signed = signed + timedelta(days=days_jit)
    if signed > today:
        signed = _add_years(signed, -1)
    expiry = _add_years(signed, W8_VALIDITY_YEARS) if form != CRS_SELF_CERT else None
    treaty_claim = reports_to_us and form == W8BEN_E and (h % 3 != 0)
    # Plant a treaty-without-TIN defect on a subset.
    foreign_tin = None if (treaty_claim and h % 5 == 0) else f"TIN-{h % 9000000 + 1000000}"
    self_cert = {"form": form, "signed": signed.isoformat(),
                 "expiry": expiry.isoformat() if expiry else None,
                 "treaty_claim": treaty_claim, "foreign_tin": foreign_tin}

    # Controlling persons — required to report a Passive NFE. Plant gaps.
    controlling = []
    if cls["is_passive"]:
        n = 1 + (h % 3)
        for i in range(n):
            j = (h // (i + 1)) % len(_CP_NAMES)
            has_tin = not (i == 0 and h % 6 == 0)         # ~1 CP missing a TIN
            has_sc = not (i == n - 1 and h % 7 == 0)      # ~1 CP self-cert missing
            controlling.append({
                "name": _CP_NAMES[j],
                "residence": _CP_RESIDENCES[(h // (i + 2)) % len(_CP_RESIDENCES)],
                "tin": f"CP-{(h // (i + 3)) % 9000000 + 1000000}" if has_tin else None,
                "self_cert": has_sc})

    return {"classification": cls, "giin": giin, "self_cert": self_cert,
            "controlling_persons": controlling, "reports_to_us": reports_to_us}


# ── Validation rules (W-8 / FATCA / CRS, incl. CRS 3.0) ──────────────────────
# Each rule inspects an entity + its derived profile and yields zero or more
# findings: {severity, rule, message, fix}. Codified here so the rule set is
# auditable and the UI can group/filter by rule id.

def _rule_w8_expiry(e, p, today):
    sc = p["self_cert"]
    if not sc.get("expiry"):
        return
    exp = date.fromisoformat(sc["expiry"])
    days = (exp - today).days
    if days < 0:
        yield ("error", "W8_EXPIRED",
               f"{sc['form']} lapsed on {exp:%d %b %Y} ({-days} days ago).",
               "Request a fresh self-certification before the next reporting cycle.")
    elif days <= _EXPIRING_SOON_DAYS:
        yield ("warning", "W8_EXPIRING",
               f"{sc['form']} expires on {exp:%d %b %Y} (in {days} days).",
               "Re-paper now to avoid presumption-rule withholding (30%).")


def _rule_treaty_tin(e, p, today):
    sc = p["self_cert"]
    if sc.get("treaty_claim") and not sc.get("foreign_tin"):
        yield ("error", "W8_TREATY_NO_TIN",
               "Treaty-rate claim on the W-8BEN-E but no foreign TIN recorded.",
               "Capture the foreign TIN; without it the treaty rate is invalid and "
               "30% withholding applies.")


def _rule_fatca_giin(e, p, today):
    if p["classification"]["fatca"] == FATCA_FFI and not p.get("giin"):
        yield ("error", "FATCA_FFI_NO_GIIN",
               "Classified as a Reporting Model 1 FFI but no GIIN held.",
               "Register with the IRS for a GIIN; a missing GIIN exposes the entity "
               "to FATCA withholding.")


def _rule_crs_passive_cps(e, p, today):
    cls = p["classification"]
    if not cls["is_passive"]:
        return
    cps = p["controlling_persons"]
    if not cps:
        yield ("error", "CRS_PASSIVE_NO_CP",
               "Passive NFE with no controlling persons identified.",
               "Identify and document the controlling persons before any CRS report.")
        return
    for cp in cps:
        if not cp.get("residence"):
            yield ("error", "CRS_CP_NO_RESIDENCE",
                   f"Controlling person {cp['name']} has no tax residence.",
                   "Obtain a self-certification stating tax residence jurisdiction(s).")
        if not cp.get("tin"):
            yield ("error", "CRS_CP_NO_TIN",
                   f"Controlling person {cp['name']} ({cp.get('residence') or '?'}) "
                   "has no TIN.",
                   "Capture the TIN or a valid TIN-absence reason code (CRS allows "
                   "a coded reason where the jurisdiction issues none).")
        if not cp.get("self_cert"):
            # CRS 3.0 makes validation of self-certifications mandatory.
            yield ("warning", "CRS3_CP_NO_SELF_CERT",
                   f"No valid self-certification on file for {cp['name']}.",
                   "CRS 3.0 requires a validated self-certification for every "
                   "controlling person.")


RULES = [_rule_w8_expiry, _rule_treaty_tin, _rule_fatca_giin, _rule_crs_passive_cps]


def validate(entity: dict, profile: dict | None = None,
             today: date | None = None) -> dict:
    """Run every rule over an entity. Returns ``{profile, findings, counts,
    readiness}``. ``readiness`` is 'not_ready' (any error), 'review' (warnings
    only) or 'ready' (clean)."""
    today = today or date.today()
    p = profile or derive_profile(entity, today)
    findings = []
    for rule in RULES:
        for sev, rid, msg, fix in rule(entity, p, today):
            findings.append({"severity": sev, "rule": rid, "message": msg, "fix": fix})
    findings.sort(key=lambda f: SEVERITY_RANK.get(f["severity"], 9))
    counts = {s: sum(1 for f in findings if f["severity"] == s)
              for s in ("error", "warning", "info")}
    readiness = ("not_ready" if counts["error"] else
                 "review" if counts["warning"] else "ready")
    return {"profile": p, "findings": findings, "counts": counts,
            "readiness": readiness}


def portfolio_aeoi(store, today: date | None = None, team_id=None) -> list[dict]:
    """AEOI readiness for every entity, worst-first (errors, then warnings).
    Each row: entity fields + classification + counts + readiness. Scoped to
    ``team_id`` when given."""
    today = today or date.today()
    rows = []
    for e in store.list_entities(limit=5000, team_id=team_id):
        v = validate(e, today=today)
        rows.append({
            "id": e["id"], "name": e["name"], "type": e.get("type"),
            "domicile": e.get("domicile"),
            "crs": v["profile"]["classification"]["crs"],
            "fatca": v["profile"]["classification"]["fatca"],
            "counts": v["counts"], "readiness": v["readiness"]})
    order = {"not_ready": 0, "review": 1, "ready": 2}
    rows.sort(key=lambda r: (order[r["readiness"]],
                             -r["counts"]["error"], -r["counts"]["warning"]))
    return rows


def portfolio_summary(rows: list[dict]) -> dict:
    """Roll up ``portfolio_aeoi`` rows into headline counts for the dashboard."""
    total = len(rows)
    by = {k: sum(1 for r in rows if r["readiness"] == k) for k in READINESS}
    errors = sum(r["counts"]["error"] for r in rows)
    warnings = sum(r["counts"]["warning"] for r in rows)
    pct = round(100 * by["ready"] / total) if total else 0
    return {"total": total, "ready_pct": pct, "by_readiness": by,
            "errors": errors, "warnings": warnings}
