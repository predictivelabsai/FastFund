"""Demonstrate the change-detection + AI-summary path on real captured data.

The first scrape of any document is a baseline (no diff). To show what happens
when the law *changes*, this script rewrites a stored version's text to a
plausible earlier state, then re-runs the scraper for that one document — so the
live fetch is seen as an amendment, producing a real diff + Grok summary.

Usage:
    python3.12 demo_change.py JE income_tax_law_1961
    python3.12 demo_change.py            # defaults to the Jersey income tax law
"""

from __future__ import annotations

import sys

import taxstore as store


def simulate_prior_version(jur: str, doc_key: str) -> None:
    doc = store.get_document(jur, doc_key)
    if not doc:
        sys.exit(f"No such document {jur}/{doc_key}. Run the scraper first.")
    ver = store.latest_version(doc["id"])
    if not ver:
        sys.exit("Document has no captured version yet.")

    original = ver["text_content"]
    # Plausible "last month's text": flip a standard rate and a key threshold.
    edited = (original
              .replace("20%", "21%", 1)
              .replace("standard rate", "revised standard rate", 1))
    if edited == original:
        # Fallback: tweak the first long line so a diff is guaranteed.
        lines = original.split("\n")
        for i, ln in enumerate(lines):
            if len(ln) > 60:
                lines[i] = ln + "  [prior wording — superseded]"
                break
        edited = "\n".join(lines)

    from taxfetch import content_hash
    store.overwrite_version_content(ver["id"], doc["id"], edited, content_hash(edited))
    print(f"Rewrote {jur}/{doc_key} v{ver['version_no']} to a simulated prior state.")
    print("Now re-scrape this document to detect the amendment:\n")
    print(f"    python3.12 scrape_tax.py --jurisdiction {jur} --doc {doc_key}\n")


if __name__ == "__main__":
    jur = sys.argv[1] if len(sys.argv) > 1 else "JE"
    key = sys.argv[2] if len(sys.argv) > 2 else "income_tax_law_1961"
    simulate_prior_version(jur, key)
