"""Reusable tax-form scrapers.

``FormScraper`` is a generic, config-driven crawler: it discovers PDF links on
an authority's forms page (via httpx, or Playwright for JS/UA-gated sites) and
downloads them. Per-site adapters subclass it to override discovery for awkward
sites (Cloudflare, JS portals). One class covers most jurisdictions.
"""
from .base import FormScraper, discover_pdf_links, download_pdf  # noqa: F401
