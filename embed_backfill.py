#!/usr/bin/env python3.12
"""Backfill chunk embeddings for vector / hybrid retrieval.

For each document's current version: split the text into passage chunks, embed
them (fastembed by default — see ``taxembed``), and store them in the active
backend (``DATA_STORAGE``). Idempotent: re-running replaces a version's chunks.

    python3.12 embed_backfill.py            # (re)embed every current version

Run after a scrape adds new versions, or once to enable vector search on an
existing corpus. Idempotent — each run replaces a version's chunks.
"""
from __future__ import annotations

import sys

import taxembed
import taxstore as store


def main() -> None:
    if not taxembed.available():
        sys.exit("Embedding model unavailable. Install fastembed: "
                 "pip install --user --break-system-packages fastembed")

    dim = taxembed.dim()
    store.ensure_vector_index(dim)
    print(f"Vector index ready (dim={dim}, model={taxembed.MODEL}).")

    docs = []
    for j in store.list_jurisdictions_with_counts():
        docs.extend(store.list_documents_for_jurisdiction(j["code"]))
    print(f"{len(docs)} documents to process...\n")

    total_chunks = embedded_docs = skipped = 0
    for d in docs:
        ver = store.latest_version(d["id"])
        if not ver or not (ver.get("text_content") or "").strip():
            continue
        chunks = taxembed.chunk_text(ver["text_content"])
        if not chunks:
            continue
        vectors = taxembed.embed_texts(chunks)
        payload = [(i, c, v) for i, (c, v) in enumerate(zip(chunks, vectors))]
        store.index_version_chunks(ver["id"], d["id"], payload)
        total_chunks += len(payload)
        embedded_docs += 1
        print(f"  [{d.get('jurisdiction_code','??')}] {d.get('doc_key','')[:34]:34} "
              f"v{ver['version_no']}  {len(payload):>3} chunks")

    print(f"\n{'='*60}")
    print(f"Embedded {embedded_docs} documents into {total_chunks} chunks.")
    print(f"Total chunks in store: {store.count_chunks()}")


if __name__ == "__main__":
    main()
