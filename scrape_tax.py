"""TaxHub scraper orchestrator (config-driven).

Usage:
    python3.12 scrape_tax.py --list
    python3.12 scrape_tax.py --jurisdiction JE
    python3.12 scrape_tax.py --jurisdiction JE --doc income_tax_law_1961
    python3.12 scrape_tax.py --all
    python3.12 scrape_tax.py --all --no-ai          # skip Grok summaries
    python3.12 scrape_tax.py --changes              # show recent changes

One generic loop drives every jurisdiction. Per document it:
  1. fetches + extracts text (http or browser, html or pdf)
  2. hashes the normalised text
  3. if unseen           -> records a baseline version (change_type=new)
     if hash changed     -> records a new version + unified diff + Grok summary
     if unchanged        -> just stamps last_checked
  4. extracts statutory citations for traceability

History is append-only, so the full "how the law changed" trail is preserved.
"""

from __future__ import annotations

import argparse
import difflib
import logging
import sys
import time
from pathlib import Path

import yaml

import taxstore as store
import taxfetch as fetch
import taxai

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scrape")

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config" / "tax_sources.yaml"


def load_config() -> dict:
    with open(CONFIG, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_diff(old: str, new: str) -> tuple[str, int, int]:
    """Unified diff + (added_chars, removed_chars)."""
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    diff = list(difflib.unified_diff(
        old_lines, new_lines, fromfile="previous", tofile="current", lineterm=""
    ))
    added = sum(len(l) for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(len(l) for l in diff if l.startswith("-") and not l.startswith("---"))
    return "\n".join(diff), added, removed


def process_document(jur_code: str, source: str, rec: dict, use_ai: bool) -> str:
    """Fetch + reconcile one document. Returns: 'new' | 'changed' | 'same' | 'error'."""
    doc_id = store.upsert_document({
        "jurisdiction_code": jur_code,
        "source": source,
        "doc_type": rec["doc_type"],
        "doc_key": rec["id"],
        "title": rec["title"],
        "reference": rec.get("reference"),
        "url": rec["url"],
        "format": rec.get("format", "html"),
        "tags": rec.get("tags"),
    })

    try:
        result = fetch.fetch_document(rec)
    except Exception as e:
        log.warning("  ✗ %s — fetch error: %s", rec["id"], e)
        store.mark_checked(doc_id, error=str(e)[:500])
        return "error"

    if not result["text"].strip():
        log.warning("  ✗ %s — empty extraction", rec["id"])
        store.mark_checked(doc_id, error="empty extraction")
        return "error"

    prev = store.latest_version(doc_id)
    store.mark_checked(doc_id)

    # Unchanged
    if prev and prev["content_hash"] == result["hash"]:
        log.info("  · %s — unchanged", rec["id"])
        return "same"

    # New version (baseline or amendment)
    version_no = (prev["version_no"] + 1) if prev else 1
    raw_path = fetch.save_raw(jur_code, rec["id"], version_no,
                              result["raw_bytes"], rec.get("format", "html"))
    new_ver = store.add_version(
        document_id=doc_id, content_hash=result["hash"],
        text_content=result["text"], raw_path=raw_path,
        byte_size=result["byte_size"], http_last_modified=result["last_modified"],
        etag=result["etag"],
    )

    # Citations (traceability) on every captured version
    cites = taxai.extract_citations(result["text"], jur_code)
    store.add_citations(doc_id, new_ver["id"], cites)

    if not prev:
        store.record_change(doc_id, None, new_ver["id"], "new")
        log.info("  + %s — NEW baseline (%d chars, %d citations)",
                 rec["id"], len(result["text"]), len(cites))
        return "new"

    # Amendment: diff + AI summary
    diff_text, added, removed = make_diff(prev["text_content"], result["text"])
    summary = {"summary": None, "impact": None, "model": None}
    if use_ai:
        summary = taxai.summarize_change(
            rec["title"], jur_code, rec["doc_type"], diff_text)
    store.record_change(
        doc_id, prev["id"], new_ver["id"], "amended",
        added_chars=added, removed_chars=removed, diff_text=diff_text[:20000],
        ai_summary=summary["summary"], ai_impact=summary["impact"],
        ai_model=summary["model"],
    )
    log.info("  ✎ %s — CHANGED (+%d/-%d chars)%s",
             rec["id"], added, removed,
             f" — {summary['summary'][:80]}" if summary.get("summary") else "")
    return "changed"


def scrape_jurisdiction(jur_code: str, cfg: dict, use_ai: bool,
                        only_doc: str = None) -> dict:
    jur = cfg["jurisdictions"][jur_code]
    store.upsert_jurisdiction(jur_code, jur["name"], jur.get("region"),
                              jur.get("authority"))
    run_id = store.start_run(jur_code)
    counts = {"new": 0, "changed": 0, "same": 0, "error": 0}

    log.info("=== %s (%s) ===", jur["name"], jur_code)
    for src in jur.get("sources", []):
        if not src.get("enabled", True):
            log.info("  (source %s disabled — skipping)", src["name"])
            continue
        source_name = src["name"]
        for rec in src.get("documents", []):
            if only_doc and rec["id"] != only_doc:
                continue
            if not rec.get("enabled", True):
                continue
            outcome = process_document(jur_code, source_name, rec, use_ai)
            counts[outcome] += 1
            time.sleep(src.get("delay", 1.0))  # be polite to gov servers

    store.finish_run(run_id, sum(counts.values()), counts["new"],
                     counts["changed"], counts["error"])
    log.info("--- %s done: %d new, %d changed, %d same, %d error ---",
             jur_code, counts["new"], counts["changed"], counts["same"],
             counts["error"])
    return counts


def main():
    ap = argparse.ArgumentParser(description="TaxHub document scraper")
    ap.add_argument("--jurisdiction", "-j", help="Jurisdiction code, e.g. JE")
    ap.add_argument("--doc", help="Single document id (with --jurisdiction)")
    ap.add_argument("--all", action="store_true", help="Scrape all jurisdictions")
    ap.add_argument("--no-ai", dest="ai", action="store_false",
                    help="Skip Grok change summaries")
    ap.add_argument("--list", action="store_true", help="List configured docs")
    ap.add_argument("--changes", action="store_true", help="Show recent changes")
    ap.add_argument("--stats", action="store_true", help="Show DB stats")
    args = ap.parse_args()

    store.init_db()
    cfg = load_config()

    if args.list:
        for jc, jur in cfg["jurisdictions"].items():
            n = sum(len(s.get("documents", [])) for s in jur.get("sources", []))
            print(f"{jc}  {jur['name']:<16} {n:>3} docs")
            for s in jur.get("sources", []):
                for d in s.get("documents", []):
                    print(f"     [{d['doc_type'][:4]}] {d['id']:<34} {d['url']}")
        return

    if args.stats:
        import json
        print(json.dumps(store.stats(), indent=2))
        return

    if args.changes:
        for ch in store.recent_changes(40):
            print(f"\n[{ch['detected_at']}] {ch['jurisdiction_code']} "
                  f"{ch['change_type'].upper()} — {ch['title']}")
            if ch.get("ai_summary"):
                print(f"   {ch['ai_summary']}")
            if ch.get("ai_impact"):
                print(f"   IMPACT: {ch['ai_impact']}")
        return

    if args.all:
        totals = {"new": 0, "changed": 0, "same": 0, "error": 0}
        for jc in cfg["jurisdictions"]:
            c = scrape_jurisdiction(jc, cfg, args.ai)
            for k in totals:
                totals[k] += c[k]
        log.info("==== TOTAL: %d new, %d changed, %d same, %d error ====",
                 totals["new"], totals["changed"], totals["same"], totals["error"])
    elif args.jurisdiction:
        if args.jurisdiction not in cfg["jurisdictions"]:
            ap.error(f"Unknown jurisdiction {args.jurisdiction}")
        scrape_jurisdiction(args.jurisdiction, cfg, args.ai, only_doc=args.doc)
    else:
        ap.error("Specify --jurisdiction, --all, --list, --changes, or --stats")


if __name__ == "__main__":
    main()
