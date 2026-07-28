"""Fetch + extract layer for the tax-document scraper.

Two fetch modes, chosen per-document in ``tax_sources.yaml``:

* ``http``    — plain httpx GET. Used for static/server-rendered HTML and
                direct PDF links. Fast, no browser.
* ``browser`` — Playwright Chromium render. Used only where the document
                list or body is built by JavaScript (e.g. jerseylaw.je's
                SharePoint search, IRAS e-Tax guides).

Extraction normalises to plain text so that the content hash is stable
across cosmetic markup churn (rotating CSRF tokens, ad slots, "last
generated" timestamps). We hash the *normalised extracted text*, never the
raw bytes — that is what makes change-detection trustworthy.
"""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from urllib.parse import urlparse

import warnings

import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# legilux.public.lu serves XHTML; we parse it with the HTML parser deliberately.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 FastFund/1.0 (+https://fastfund.predictivelabs.ai)"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9,fr;q=0.8"}

# Government hosts that serve a valid cert but omit the intermediate CA in the
# chain, so strict verification fails ("unable to get local issuer certificate").
# The cert itself is genuine — we relax verification for these hosts only.
INSECURE_HOSTS = {"www.mof.gov.cy", "mof.gov.cy"}

# Tags whose text is navigational chrome, not document content.
_STRIP_TAGS = ["script", "style", "noscript", "nav", "header", "footer",
               "form", "svg", "iframe", "button"]


# ──────────────────────────────────────────────────────────────────────────
# Fetch
# ──────────────────────────────────────────────────────────────────────────

def fetch_http(url: str, timeout: float = 60.0) -> dict:
    """GET a URL. Returns {content: bytes, headers: dict, content_type, url}."""
    verify = urlparse(url).hostname not in INSECURE_HOSTS
    if not verify:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=timeout,
                      verify=verify) as c:
        r = c.get(url)
        r.raise_for_status()
        return {
            "content": r.content,
            "headers": dict(r.headers),
            "content_type": r.headers.get("content-type", ""),
            "last_modified": r.headers.get("last-modified"),
            "etag": r.headers.get("etag"),
            "url": str(r.url),
        }


def fetch_browser(url: str, wait_selector: str = None, wait_ms: int = 2500,
                  headless: bool = True) -> dict:
    """Render a URL with Playwright Chromium and return final HTML.

    Imported lazily so http-only runs never spin up a browser.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(user_agent=UA)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=15000)
                except Exception:
                    pass
            page.wait_for_timeout(wait_ms)
            html = page.content()
            final_url = page.url
        finally:
            browser.close()
    return {
        "content": html.encode("utf-8"),
        "headers": {},
        "content_type": "text/html",
        "last_modified": None,
        "etag": None,
        "url": final_url,
    }


# ──────────────────────────────────────────────────────────────────────────
# Extract
# ──────────────────────────────────────────────────────────────────────────

def extract_html_text(html: bytes | str, selector: str = None) -> str:
    """Strip chrome and return the readable text of an HTML document.

    If ``selector`` is given, only that container's text is used — the way
    to pin extraction to a law's body and ignore site furniture.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    _strip_cookie_chrome(soup)
    root = soup.select_one(selector) if selector else None
    target = root or soup.body or soup
    text = target.get_text("\n")
    return normalize_text(text)


def _strip_cookie_chrome(soup) -> None:
    """Remove cookie-consent / Cookiebot widgets — volatile boilerplate that
    would otherwise pollute the content hash and diffs (gov.je, jerseylaw.je)."""
    for el in soup.select('[id*="Cookiebot"], [id*="CybotCookiebot"], '
                          '[class*="cookie"], [id*="cookie-"], #cookie-consent'):
        el.decompose()


def extract_pdf_text(content: bytes) -> str:
    """Extract text from a PDF. Try pdfminer, fall back to PyPDF2."""
    try:
        from pdfminer.high_level import extract_text
        txt = extract_text(io.BytesIO(content))
        if txt and txt.strip():
            return normalize_text(txt)
    except Exception:
        pass
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(content))
        txt = "\n".join((pg.extract_text() or "") for pg in reader.pages)
        return normalize_text(txt)
    except Exception:
        return ""


def normalize_text(text: str) -> str:
    """Collapse whitespace so hashing ignores cosmetic reflow."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────────────────
# Combined: fetch a document per its config record
# ──────────────────────────────────────────────────────────────────────────

def _extract(res: dict, rec: dict) -> str:
    fmt = rec.get("format", "html")
    ctype = (res.get("content_type") or "").lower()
    raw = res["content"]
    if fmt == "pdf" or "application/pdf" in ctype:
        return extract_pdf_text(raw)
    text = extract_html_text(raw, selector=rec.get("selector"))
    return "" if is_blocked(text) else text


# Signatures of bot/UA/cookie walls served instead of real content. We never
# store these as a document version — better an honest "blocked" error than a
# junk baseline that triggers false change alerts on every run.
_BLOCK_SIGNATURES = (
    "not compatible with your web browser",
    "performing security verification",
    "verifies you are not a bot",
    "enable javascript and cookies to continue",
    "checking your browser before accessing",
)


def is_blocked(text: str) -> bool:
    if not text:
        return False
    head = text[:1500].lower()
    if any(sig in head for sig in _BLOCK_SIGNATURES):
        return True
    # A short page that is essentially just the cookie banner.
    if len(text) < 1200 and head.startswith("cookies on "):
        return True
    return False


def fetch_document(rec: dict, retries: int = 2, retry_delay: float = 2.0) -> dict:
    """Fetch + extract one document, resiliently.

    ``rec`` is a tax_sources.yaml document record:
      {url, format: html|pdf, fetch: http|browser, selector?, wait_selector?,
       min_text?}

    Strategy: some government CMSes (e.g. jerseylaw.je's SharePoint) sometimes
    return a JS shell on a cold hit and full server-rendered HTML on a warm one.
    So for http+html we retry on a suspiciously short extraction, then escalate
    to a real browser render before giving up.

    Returns {text, hash, raw_bytes, byte_size, last_modified, etag, final_url}.
    """
    import time

    mode = rec.get("fetch", "http")
    fmt = rec.get("format", "html")
    # PDFs are inherently long; HTML laws/guidance should clear a low floor.
    min_text = rec.get("min_text", 0 if fmt == "pdf" else 200)

    res, text = None, ""
    if mode == "http" and fmt == "pdf":
        # PDFs are static bytes — one read suffices.
        res = fetch_http(rec["url"])
        text = _extract(res, rec)
    elif mode == "http":
        # HTML over http: some gov CMSes stream partial renders. Read until the
        # extraction length stabilises (two equal consecutive reads) and keep
        # the longest seen — defeats partial/cold-cache responses.
        best_res, best_text, prev_len = None, "", -1
        for attempt in range(retries + 2):
            res = fetch_http(rec["url"])
            text = _extract(res, rec)
            if len(text) > len(best_text):
                best_res, best_text = res, text
            if len(best_text) >= min_text and len(text) == prev_len:
                break
            prev_len = len(text)
            time.sleep(retry_delay)
        res, text = best_res, best_text
        # Still thin → escalate to a real browser render.
        if len(text) < min_text:
            try:
                bres = fetch_browser(rec["url"], wait_selector=rec.get("wait_selector"))
                btext = _extract(bres, rec)
                if len(btext) > len(text):
                    res, text = bres, btext
            except Exception:
                pass
    else:
        # Browser mode: gov.je/Cloudflare sometimes serve a cookie/bot wall on a
        # cold load. Retry, keeping the longest non-blocked extraction.
        best_res, best_text = None, ""
        for attempt in range(retries + 2):
            res = fetch_browser(rec["url"], wait_selector=rec.get("wait_selector"),
                                wait_ms=3000 + attempt * 2000)
            text = _extract(res, rec)
            if best_res is None or len(text) > len(best_text):
                best_res, best_text = res, text
            if len(best_text) >= min_text:
                break
            time.sleep(retry_delay)
        res, text = best_res, best_text

    return {
        "text": text,
        "hash": content_hash(text),
        "raw_bytes": res["content"],
        "byte_size": len(res["content"]),
        "last_modified": res.get("last_modified"),
        "etag": res.get("etag"),
        "final_url": res.get("url", rec["url"]),
    }


def save_raw(jurisdiction: str, doc_key: str, version_no: int,
             raw: bytes, fmt: str) -> str:
    """Persist raw bytes for audit; returns relative path."""
    ext = "pdf" if fmt == "pdf" else "html"
    d = RAW_DIR / jurisdiction / doc_key
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"v{version_no:04d}.{ext}"
    path.write_bytes(raw)
    return str(path.relative_to(ROOT))
