"""Backfill the Neo4j graph from the existing SQLite corpus.

Reads every jurisdiction → document → version → change → citation out of the
SQLite backend and replays it into Neo4j through the same ``Storage`` interface
the scraper uses, so the graph ends up identical to what a fresh scrape would
build — but without re-fetching a single government website.

Re-runnable: documents/jurisdictions are MERGE-keyed, so a second run updates
in place. Versions/changes are append-only, so to avoid duplicates the script
**skips any document that already has versions in Neo4j** (use --wipe for a
clean rebuild).

Usage:
    python3.12 migrate_sqlite_to_neo4j.py            # incremental backfill
    python3.12 migrate_sqlite_to_neo4j.py --wipe     # clear graph, full rebuild
    DB_URL=sqlite:///taxhub.db python3.12 migrate_sqlite_to_neo4j.py
"""

from __future__ import annotations
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent))

import argparse
import json

from sqlalchemy import text

from storage.sqlite_store import SqliteStore
from storage.neo4j_store import Neo4jStore


def _rows(src: SqliteStore, sql: str, **params) -> list[dict]:
    with src.conn() as c:
        return [dict(r) for r in c.execute(text(sql), params).mappings().all()]


def migrate(wipe: bool = False) -> None:
    src = SqliteStore()
    dst = Neo4jStore()

    if wipe:
        with dst._session() as s:
            s.run("MATCH (n) DETACH DELETE n").consume()
        print("Wiped Neo4j graph.")

    dst.init_db()

    jurs = _rows(src, "SELECT * FROM jurisdictions ORDER BY code")
    print(f"Migrating {len(jurs)} jurisdictions...")
    for j in jurs:
        dst.upsert_jurisdiction(j["code"], j["name"], j.get("region"), j.get("authority"))

    docs = _rows(src, "SELECT * FROM tax_documents ORDER BY id")
    n_docs = n_versions = n_changes = n_cites = 0
    skipped = 0

    for d in docs:
        # Map SQLite document id -> Neo4j document uid (idempotent upsert).
        new_doc_id = dst.upsert_document({
            "jurisdiction_code": d["jurisdiction_code"], "source": d.get("source"),
            "doc_type": d["doc_type"], "doc_key": d["doc_key"], "title": d["title"],
            "reference": d.get("reference"), "url": d["url"],
            "format": d.get("format", "html"), "tags": _tags(d),
        })
        n_docs += 1

        if dst.latest_version(new_doc_id) is not None:
            skipped += 1
            continue  # already backfilled — don't duplicate append-only history

        versions = _rows(src,
            "SELECT * FROM document_versions WHERE document_id=:i ORDER BY version_no",
            i=d["id"])
        # Old SQLite version id -> new Neo4j version uid, to wire change edges.
        vmap: dict[int, int] = {}
        for v in versions:
            nv = dst.add_version(
                new_doc_id, v["content_hash"], v.get("text_content"),
                raw_path=v.get("raw_path"), byte_size=v.get("byte_size"),
                http_last_modified=v.get("http_last_modified"), etag=v.get("etag"),
            )
            vmap[v["id"]] = nv["id"]
            n_versions += 1

            cites = _rows(src,
                "SELECT cited_instrument, cited_locator, snippet, cited_jurisdiction "
                "FROM citations WHERE version_id=:vid", vid=v["id"])
            if cites:
                dst.add_citations(new_doc_id, nv["id"], [{
                    "instrument": ct["cited_instrument"], "locator": ct["cited_locator"],
                    "snippet": ct["snippet"], "jurisdiction": ct["cited_jurisdiction"],
                } for ct in cites if ct["cited_instrument"]])
                n_cites += len([ct for ct in cites if ct["cited_instrument"]])

        dst.mark_checked(new_doc_id)

        changes = _rows(src,
            "SELECT * FROM document_changes WHERE document_id=:i ORDER BY id", i=d["id"])
        for ch in changes:
            dst.record_change(
                new_doc_id, vmap.get(ch.get("from_version_id")),
                vmap.get(ch["to_version_id"]), ch["change_type"],
                added_chars=ch.get("added_chars", 0), removed_chars=ch.get("removed_chars", 0),
                diff_text=ch.get("diff_text"), ai_summary=ch.get("ai_summary"),
                ai_impact=ch.get("ai_impact"), ai_model=ch.get("ai_model"),
            )
            n_changes += 1

    print(f"Done. {n_docs} documents ({skipped} already present, skipped), "
          f"{n_versions} versions, {n_changes} changes, {n_cites} citations.")
    print("Neo4j stats:", dst.stats())
    dst.close()


def _tags(doc_row: dict) -> list:
    try:
        return json.loads(doc_row.get("tags") or "[]")
    except Exception:
        return []


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backfill Neo4j from SQLite")
    ap.add_argument("--wipe", action="store_true",
                    help="Clear the Neo4j graph before migrating (full rebuild)")
    args = ap.parse_args()
    migrate(wipe=args.wipe)
