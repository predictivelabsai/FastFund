"""Generic, reusable form scraper (httpx + optional Playwright)."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ingest import fetch as _fetch

ROOT = Path(__file__).resolve().parent.parent.parent
FORMS_DIR = ROOT / "data" / "forms"


def discover_pdf_links(page_url: str, match: str = ".pdf",
                       use_browser: bool = False) -> list[dict]:
    """Find PDF links on a page. Returns [{url, text}], dedup-ed, absolute URLs.

    Tries httpx first; on failure (or use_browser=True) falls back to Playwright
    so JS/UA-gated authority sites still yield links. Never raises — returns []
    on any error so one bad source can't abort a whole scrape run.
    """
    html = None
    if not use_browser:
        try:
            html = _fetch.fetch_http(page_url)["content"]
        except Exception:  # noqa: BLE001
            html = None
    if html is None:
        try:
            html = _fetch.fetch_browser(page_url)["content"]
        except Exception:  # noqa: BLE001
            return []

    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    pat = match.lower()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        absu = urljoin(page_url, href)
        low = absu.lower()
        # Match by extension or by an explicit substring/regex in `match`.
        hit = low.endswith(pat) or pat in low
        if not hit:
            continue
        if absu in seen:
            continue
        seen.add(absu)
        out.append({"url": absu, "text": " ".join(a.get_text().split())[:200]})
    return out


def download_pdf(url: str, dest: Path, use_browser: bool = False) -> Path | None:
    """Download a PDF to ``dest``. Verifies it's really a PDF (%PDF magic).
    Returns the path on success, else None (never raises)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        res = _fetch.fetch_http(url)
        content = res["content"]
    except Exception:  # noqa: BLE001
        return None
    if not content or not content[:5].startswith(b"%PDF"):
        # Some endpoints serve the PDF only after JS; a browser fetch won't get
        # bytes, so we just reject non-PDF responses here.
        if b"%PDF" not in content[:1024]:
            return None
    dest.write_bytes(content)
    return dest


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60]


class FormScraper:
    """Config-driven scraper for one jurisdiction's forms.

    Subclass and override ``discover`` for sites that need bespoke handling.
    """

    def __init__(self, jurisdiction_code: str, use_browser: bool = False):
        self.jurisdiction_code = jurisdiction_code
        self.use_browser = use_browser

    def discover(self, source_url: str, match: str = ".pdf") -> list[dict]:
        return discover_pdf_links(source_url, match=match, use_browser=self.use_browser)

    def fetch_form_pdf(self, form: dict) -> Path | None:
        """Resolve and download a form's PDF given its catalogue entry.

        Order: a direct ``url`` (download it); else ``source`` page (discover the
        best-matching PDF link, then download). Returns the local path or None.
        """
        dest = FORMS_DIR / self.jurisdiction_code / f"{form['form_key']}.pdf"
        if form.get("url"):
            return download_pdf(form["url"], dest, use_browser=self.use_browser)
        src = form.get("source")
        if not src:
            return None
        links = self.discover(src, match=form.get("match", ".pdf"))
        if not links:
            return None
        best = self._best_link(links, form)
        return download_pdf(best["url"], dest, use_browser=self.use_browser)

    @staticmethod
    def _best_link(links: list[dict], form: dict) -> dict:
        """Pick the most relevant PDF link for the form by keyword overlap."""
        terms = set(re.split(r"\W+", (form.get("title", "") + " " +
                                      form.get("form_type", "")).lower())) - {""}
        def score(l):  # noqa: E741
            t = (l["text"] + " " + l["url"]).lower()
            return sum(1 for w in terms if len(w) > 2 and w in t)
        return max(links, key=score)
