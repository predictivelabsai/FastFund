"""Scrape and store tax forms from the catalogue, and build the Forms Tree.

``scrape_forms`` walks ``config/tax_forms.yaml``: for each form it records the
metadata (always) and, when ``download=True``, fetches the PDF via the reusable
``FormScraper`` and stores its local path. Metadata-only runs need no network,
so the corpus is usable immediately and PDFs backfill opportunistically.
"""
from __future__ import annotations

from pathlib import Path

import yaml

import taxstore as store
from ingest.scrapers import FormScraper
from ingest.scrapers.base import FORMS_DIR  # noqa: F401  (re-exported for callers)

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "tax_forms.yaml"

# Jurisdictions whose sites are JS/UA-gated → use a browser to discover links.
_BROWSER_JURISDICTIONS = {"JE", "GG"}


def load_catalogue() -> dict:
    with open(CONFIG, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["jurisdictions"]


def scrape_forms(jurisdiction: str | None = None, download: bool = True) -> dict:
    cat = load_catalogue()
    codes = [jurisdiction] if jurisdiction else list(cat)
    recorded = downloaded = failed = 0
    for code in codes:
        jur = cat.get(code)
        if not jur:
            continue
        store.upsert_jurisdiction(code, jur.get("name", code),
                                  authority=jur.get("authority"))
        scraper = FormScraper(code, use_browser=code in _BROWSER_JURISDICTIONS)
        for form in jur.get("forms", []):
            rec = {
                "jurisdiction_code": code,
                "category": form.get("category"),
                "form_type": form.get("form_type"),
                "form_key": form["form_key"],
                "title": form.get("title"),
                "authority": jur.get("authority"),
                "url": form.get("source") or form.get("url"),
                "file_path": None,
                "year": form.get("year"),
                "who_files": form.get("who_files"),
                "deadline": form.get("deadline"),
                "frequency": form.get("frequency"),
                "summary": form.get("summary"),
            }
            if download:
                path = scraper.fetch_form_pdf(form)
                if path:
                    rec["file_path"] = str(path.relative_to(ROOT))
                    downloaded += 1
                else:
                    failed += 1
            store.upsert_form(rec)
            recorded += 1
            print(f"  [{code}] {form['form_key']:<32} "
                  f"{'pdf ✓' if rec['file_path'] else 'meta only'}")
    return {"recorded": recorded, "downloaded": downloaded, "failed_downloads": failed}


def forms_tree(jurisdiction: str | None = None) -> list[dict]:
    """Nested taxonomy for the Forms Tree:
    [{code, name, categories:[{category, types:[{form_type, forms:[...]}]}]}]."""
    forms = store.list_forms(jurisdiction_code=jurisdiction, limit=2000)
    jmap: dict[str, dict] = {}
    for f in forms:
        jc = f["jurisdiction_code"]
        j = jmap.setdefault(jc, {"code": jc, "categories": {}})
        cat = f.get("category") or "other"
        c = j["categories"].setdefault(cat, {"category": cat, "types": {}})
        ft = f.get("form_type") or "form"
        t = c["types"].setdefault(ft, {"form_type": ft, "forms": []})
        t["forms"].append(f)
    # flatten dicts → ordered lists
    out = []
    for jc in sorted(jmap):
        j = jmap[jc]
        cats = []
        for cat in sorted(j["categories"]):
            c = j["categories"][cat]
            types = [c["types"][ft] for ft in sorted(c["types"])]
            cats.append({"category": cat, "types": types})
        out.append({"code": jc, "categories": cats})
    return out


if __name__ == "__main__":
    import sys
    juris = sys.argv[1] if len(sys.argv) > 1 else None
    print(scrape_forms(juris, download="--no-download" not in sys.argv))
