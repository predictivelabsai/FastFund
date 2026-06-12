"""AI + traceability helpers.

Two jobs:

1. ``summarize_change`` — given a unified diff between two versions of a tax
   document, ask xAI Grok to explain, in plain English for a fund back-office
   reader, *what changed* and *what the operational impact is*. This is the
   "how is the law changing" half of the product.

2. ``extract_citations`` — deterministically pull statutory cross-references
   ("Article 123A of the Income Tax (Jersey) Law 1961", "Regulation 5",
   "Schedule 2") out of a document so each guidance note can be traced back to
   the primary law it interprets. Regex-based: cheap, reproducible, no tokens.
"""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv

load_dotenv()

GROK_MODEL = os.environ.get("GROK_MODEL", "grok-4-1-fast-reasoning")
_XAI_KEY = os.environ.get("XAI_API_KEY", "")
_XAI_BASE = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")

_llm = None


def get_llm():
    """Lazily build a Grok chat client (OpenAI-compatible)."""
    global _llm
    if _llm is None:
        from langchain_openai import ChatOpenAI
        _llm = ChatOpenAI(
            model=GROK_MODEL, api_key=_XAI_KEY, base_url=_XAI_BASE,
            temperature=0.0, timeout=120,
        )
    return _llm


def ai_available() -> bool:
    return bool(_XAI_KEY)


_SUMMARY_SYS = (
    "You are a tax-technical analyst supporting a fund-management back office. "
    "You receive a diff between two versions of an official tax document "
    "(legislation, guidance, circular, or gazette). Be precise, factual, and "
    "concise. Never speculate beyond the diff. If the change is purely "
    "cosmetic (formatting, numbering, typo), say so plainly."
)

_SUMMARY_PROMPT = """Document: {title}
Jurisdiction: {jurisdiction}
Type: {doc_type}

A new version was published. Below is the unified diff (─ = removed, + = added).

```diff
{diff}
```

Respond in exactly two short sections, no preamble:

SUMMARY: 1-3 sentences stating what substantively changed.
IMPACT: 1-2 sentences on the practical impact for a fund administrator / back office (filing obligations, deadlines, rates, classifications, reporting). If none, write "No operational impact — cosmetic/editorial change."
"""


def summarize_change(title: str, jurisdiction: str, doc_type: str,
                     diff_text: str) -> dict:
    """Return {summary, impact, model}. Degrades gracefully without a key."""
    if not ai_available():
        return {"summary": None, "impact": None, "model": None}
    # Keep token use bounded — the head of a diff carries the salient edits.
    diff = (diff_text or "")[:12000]
    if not diff.strip():
        return {"summary": None, "impact": None, "model": None}
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        msg = _SUMMARY_PROMPT.format(
            title=title, jurisdiction=jurisdiction, doc_type=doc_type, diff=diff
        )
        resp = get_llm().invoke(
            [SystemMessage(content=_SUMMARY_SYS), HumanMessage(content=msg)]
        )
        return _parse_summary(resp.content)
    except Exception as e:
        return {"summary": f"[AI summary failed: {e}]", "impact": None,
                "model": GROK_MODEL}


def _parse_summary(text: str) -> dict:
    summary, impact = None, None
    m = re.search(r"SUMMARY:\s*(.+?)(?:\n\s*IMPACT:|\Z)", text, re.S | re.I)
    if m:
        summary = m.group(1).strip()
    m = re.search(r"IMPACT:\s*(.+)\Z", text, re.S | re.I)
    if m:
        impact = m.group(1).strip()
    if summary is None:
        summary = text.strip()
    return {"summary": summary, "impact": impact, "model": GROK_MODEL}


# ──────────────────────────────────────────────────────────────────────────
# Citation extraction (deterministic)
# ──────────────────────────────────────────────────────────────────────────

# Named instruments worth linking back to, by jurisdiction. Extend freely.
_INSTRUMENT_PATTERNS = [
    # Channel Islands: "... (Jersey) Law 1961", "Income Tax (Guernsey) Law, 1975"
    r"[A-Z][\w()&,'–\- ]+?\((?:Jersey|Guernsey|Bailiwick of Guernsey)\)\s+(?:Law|Regulations|Order|Ordinance)[,]?\s+\d{4}",
    # Acts (Ireland / Cayman / BVI), incl. "Act, 2018" and "Act (2021 Revision)"
    r"[A-Z][\w()&,'–\- ]+?Act(?:,?\s+\d{4}|\s+\(\d{4}\s+Revision\))",
    r"Loi\s+(?:modifiée\s+)?(?:du|de)\s+[\w\s.]+?\d{4}",   # Luxembourg laws
    r"L\.I\.R\.",                                     # Lux income tax law shorthand
    r"Income Tax \(Jersey\) Law 1961",
    r"Taxes Consolidation Act,? 1997",               # Ireland TCA
]

# Locators inside an instrument.
_LOCATOR_RE = re.compile(
    r"\b(?:Article|Art\.?|Section|Sec\.?|s\.|Regulation|Reg\.?|Schedule|Sch\.?|"
    r"Paragraph|Para\.?|Chapter|§|circulaire)\s+[A-Z]?\d+[A-Za-z]?(?:\(\d+\))?",
    re.I,
)

_INSTRUMENT_RE = re.compile("|".join(f"(?:{p})" for p in _INSTRUMENT_PATTERNS))


def extract_citations(text: str, jurisdiction: str, max_cites: int = 60) -> list[dict]:
    """Pull statutory cross-references from a document's text.

    Returns list of {instrument, locator, snippet, jurisdiction}.
    A locator within ~80 chars of an instrument name is paired with it.
    """
    if not text:
        return []
    cites: list[dict] = []
    seen: set[tuple] = set()

    for m in _INSTRUMENT_RE.finditer(text):
        instrument = re.sub(r"\s+", " ", m.group(0)).strip(" ,.")
        window = text[max(0, m.start() - 80): m.end() + 80]
        loc = _LOCATOR_RE.search(window)
        locator = loc.group(0).strip() if loc else None
        key = (instrument.lower(), (locator or "").lower())
        if key in seen:
            continue
        seen.add(key)
        snippet = re.sub(r"\s+", " ", window).strip()
        cites.append({
            "instrument": instrument,
            "locator": locator,
            "snippet": snippet[:300],
            "jurisdiction": jurisdiction,
        })
        if len(cites) >= max_cites:
            break
    return cites
