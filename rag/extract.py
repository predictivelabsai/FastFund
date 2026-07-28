"""Extract structured SFO signals from an uploaded document.

Turns the text of a portfolio summary / trust deed / asset inventory into a
partial profile (asset mix, pain points, services in place) using the LLM, so an
upload can update the family's profile and drive fresh recommendations. Also pulls
plain text out of PDFs. Degrades to {} when AI is unavailable.
"""
from __future__ import annotations

import json

from rag import llm

SERVICE_KEYS = ["trusts", "tax_reporting", "fund_admin", "luxury_assets",
                "real_estate_admin", "edge", "governance", "nextgen_education",
                "banking_treasury", "compliance", "private_office"]
ASSET_CLASSES = ["private_equity", "public_equity", "real_estate", "luxury",
                 "cash", "alternatives"]

_SYS = (
    "You read a single family office's document (portfolio summary, trust deed, or "
    "asset inventory) and extract a STRUCTURED profile. Respond ONLY as JSON: "
    '{"asset_mix": {"<class>": <percent>}, "pain_points": ["..."], '
    '"current_services": ["<key>"], "summary": "one line"}. '
    f"asset_mix classes must be from {ASSET_CLASSES} and percentages are integers that "
    f"roughly sum to 100. current_services keys must be from {SERVICE_KEYS}. Only "
    "include fields the document actually supports; use empty objects/lists otherwise.")


def text_from_upload(filename: str, data: bytes) -> str:
    """Best-effort plain text from an uploaded file (txt/md/csv directly, PDF via
    pdfminer). Returns '' if it can't be read as text."""
    name = (filename or "").lower()
    if name.endswith((".txt", ".md", ".csv", ".json")):
        return data[:40000].decode("utf-8", "ignore")
    if name.endswith(".pdf"):
        try:
            import io
            from pdfminer.high_level import extract_text
            return (extract_text(io.BytesIO(data)) or "")[:40000]
        except Exception:  # noqa: BLE001
            return ""
    return ""


def extract_profile(text: str) -> dict:
    """LLM-extract {asset_mix, pain_points, current_services, summary} from text."""
    if not text or not llm.ai_available():
        return {}
    raw = llm.complete(_SYS, text[:12000], temperature=0)
    try:
        obj = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    mix = {k: int(v) for k, v in (obj.get("asset_mix") or {}).items()
           if k in ASSET_CLASSES and str(v).lstrip("-").isdigit()}
    if mix:
        out["asset_mix"] = mix
    pains = [str(p).strip() for p in (obj.get("pain_points") or []) if str(p).strip()]
    if pains:
        out["pain_points"] = pains[:6]
    svcs = [s for s in (obj.get("current_services") or []) if s in SERVICE_KEYS]
    if svcs:
        out["current_services"] = svcs
    if obj.get("summary"):
        out["summary"] = str(obj["summary"])[:200]
    return out


def apply_to_sfo(store, sfo_id: int, extracted: dict) -> bool:
    """Merge extracted signals into the SFO profile (replace asset_mix; union
    pain_points + current_services). Returns True if anything changed."""
    sfo = store.get_sfo(sfo_id)
    if not sfo or not extracted:
        return False
    changed = False
    if extracted.get("asset_mix"):
        sfo["asset_mix"] = extracted["asset_mix"]
        changed = True
    if extracted.get("pain_points"):
        merged = list(dict.fromkeys((sfo.get("pain_points") or []) + extracted["pain_points"]))
        sfo["pain_points"] = merged
        changed = True
    if extracted.get("current_services"):
        merged = sorted(set((sfo.get("current_services") or []) + extracted["current_services"]))
        sfo["current_services"] = merged
        changed = True
    if changed:
        store.upsert_sfo(sfo)
    return changed
