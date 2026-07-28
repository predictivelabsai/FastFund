"""Graph-RAG question answering over the tracked tax corpus.

The retrieval is *structure-aware*, not just keyword search:

1. **Seed** — full-text search the current version of every tracked document
   for the question's terms (Neo4j's Lucene index; a substring scan on SQLite).
2. **Expand through the graph** — for each seed document, pull what the graph
   knows *around* it: the statutory instruments it cites (traceability) and the
   latest AI change summary (what recently moved). This is the graph payoff —
   an answer grounded not only in matching text but in the document's citations
   and amendment history.
3. **Generate** — hand the assembled, numbered context to Grok and ask for an
   answer that cites its sources by number.

Retrieval and generation are deliberately separate: ``Retriever`` is an
interface, so a future ``VectorRetriever`` (once embeddings land) drops in
without touching ``answer()``. Everything goes through the ``Storage`` layer,
so graph-RAG works on whichever backend ``DATA_STORAGE`` selects — but it is
genuinely a graph traversal on Neo4j.
"""

from __future__ import annotations

import abc
import os
import re

from . import llm as taxai
from . import embeddings as taxembed
import taxstore as store

_MAX_SNIPPET = 600
_RRF_K = 60  # reciprocal-rank-fusion damping constant (Neo4j/Elastic default)


class Retriever(abc.ABC):
    """Turns a question into a ranked list of grounded context blocks."""

    @abc.abstractmethod
    def retrieve(self, question: str, k: int = 6) -> list[dict]:
        ...


def _expand(seeds: list[dict]) -> list[dict]:
    """Graph step: enrich each seed document with what the graph knows around
    it — the statutory instruments it cites and its latest AI change summary."""
    blocks = []
    for s in seeds:
        doc_id = s["document_id"]
        citations = store.list_citations(doc_id, limit=8)
        changes = store.list_changes_for_document(doc_id)
        latest = next((c["ai_summary"] for c in changes if c.get("ai_summary")), None)
        blocks.append({
            "document_id": doc_id,
            "doc_key": s.get("doc_key"),
            "title": s["title"],
            "jurisdiction_code": s["jurisdiction_code"],
            "doc_type": s.get("doc_type"),
            "url": s.get("url"),
            "reference": s.get("reference"),
            "score": s.get("score"),
            "snippet": s["snippet"],
            "cited_instruments": [c["cited_instrument"] for c in citations
                                  if c.get("cited_instrument")],
            "latest_change": latest,
        })
    return blocks


def _meta(row: dict) -> dict:
    return {k: row.get(k) for k in
            ("document_id", "doc_key", "title", "jurisdiction_code",
             "doc_type", "url", "reference")}


def _seed_fulltext(question: str, k: int) -> list[dict]:
    seeds = []
    for h in store.search_versions(question, limit=k):
        seeds.append({**_meta(h), "score": h.get("score"),
                      "snippet": _snippet(h.get("text_content"), question)})
    return seeds


def _seed_vector(question: str, k: int) -> list[dict]:
    """Embed the question, nearest-chunk search, best chunk per document."""
    qvec = taxembed.embed_query(question)
    seen, seeds = set(), []
    for r in store.vector_search_chunks(qvec, limit=k * 3):
        did = r["document_id"]
        if did in seen:               # keep the top-scoring chunk per document
            continue
        seen.add(did)
        snippet = (r.get("chunk_text") or "")[:_MAX_SNIPPET]
        seeds.append({**_meta(r), "score": r.get("score"), "snippet": snippet})
        if len(seeds) >= k:
            break
    return seeds


def _fuse_rrf(seed_lists: list[list[dict]], k: int) -> list[dict]:
    """Reciprocal-rank fusion across retrievers, keyed by document_id."""
    fused: dict[int, dict] = {}
    for seeds in seed_lists:
        for rank, s in enumerate(seeds, 1):
            did = s["document_id"]
            cur = fused.get(did)
            contrib = 1.0 / (_RRF_K + rank)
            if cur is None:
                fused[did] = {**s, "score": contrib}
            else:
                cur["score"] += contrib
                # Prefer a vector chunk snippet (later list) when present.
                if s.get("snippet"):
                    cur["snippet"] = s["snippet"]
    ranked = sorted(fused.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:k]


def _vectors_ready() -> bool:
    try:
        return taxembed.available() and store.count_chunks() > 0
    except Exception:  # noqa: BLE001
        return False


class GraphFullTextRetriever(Retriever):
    """Full-text seed + citation/change graph expansion."""

    name = "fulltext"

    def retrieve(self, question: str, k: int = 6) -> list[dict]:
        return _expand(_seed_fulltext(question, k))


class VectorRetriever(Retriever):
    """Semantic chunk seed + graph expansion. Falls back to full-text if no
    embeddings/chunks are available."""

    name = "vector"

    def retrieve(self, question: str, k: int = 6) -> list[dict]:
        if not _vectors_ready():
            return _expand(_seed_fulltext(question, k))
        return _expand(_seed_vector(question, k))


class HybridRetriever(Retriever):
    """Full-text ∪ vector seeds fused by reciprocal-rank, then graph expansion —
    the recommended pattern. Degrades to full-text when vectors are unavailable."""

    name = "hybrid"

    def retrieve(self, question: str, k: int = 6) -> list[dict]:
        ft = _seed_fulltext(question, k)
        if not _vectors_ready():
            return _expand(ft)
        vec = _seed_vector(question, k)
        return _expand(_fuse_rrf([ft, vec], k))


_RETRIEVERS = {r.name: r for r in (GraphFullTextRetriever, VectorRetriever, HybridRetriever)}


def get_retriever(name: str | None = None) -> Retriever:
    """Retriever selected by ``name`` / ``RAG_RETRIEVER`` env / default hybrid."""
    name = (name or os.environ.get("RAG_RETRIEVER", "hybrid")).lower()
    return _RETRIEVERS.get(name, HybridRetriever)()


_SYS = (
    "You are a tax-technical analyst supporting a fund-management back office. "
    "Answer the question using ONLY the numbered context provided — tracked "
    "official tax documents across FastFund fund jurisdictions. Be precise and "
    "factual; never speculate beyond the context. Cite the documents you rely "
    "on inline by their number, e.g. [1], [3]. If the context does not contain "
    "the answer, say so plainly and suggest which jurisdiction's documents to "
    "check. Note relevant cross-references and any recent amendments."
)


def answer(question: str, retriever: Retriever | None = None, k: int = 6) -> dict:
    """Answer a question over the corpus.

    Returns ``{answer, sources, model}`` where ``sources`` is the list of
    retrieved context blocks (numbered to match the inline citations).
    """
    retriever = retriever or get_retriever()
    blocks = retriever.retrieve(question, k=k)
    if not blocks:
        return {
            "answer": "No tracked document matched that question. Try different "
                      "terms, or browse by jurisdiction.",
            "sources": [], "model": None,
        }

    if not taxai.ai_available():
        # Degrade gracefully: return the retrieved sources without synthesis.
        return {
            "answer": "AI synthesis is unavailable (no XAI_API_KEY set). The most "
                      "relevant tracked documents are listed below.",
            "sources": blocks, "model": None,
        }

    context = _format_context(blocks)
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        prompt = (f"Question: {question}\n\nContext:\n{context}\n\n"
                  "Answer for a fund back office, citing sources by number.")
        resp = taxai.get_llm().invoke(
            [SystemMessage(content=_SYS), HumanMessage(content=prompt)]
        )
        return {"answer": resp.content.strip(), "sources": blocks,
                "model": taxai.GROK_MODEL}
    except Exception as e:  # noqa: BLE001
        return {"answer": f"[AI answer failed: {e}]", "sources": blocks,
                "model": taxai.GROK_MODEL}


def _format_context(blocks: list[dict]) -> str:
    parts = []
    for i, b in enumerate(blocks, 1):
        lines = [f"[{i}] {b['title']} ({b['jurisdiction_code']}"
                 + (f", {b['reference']}" if b.get("reference") else "") + ")"]
        if b.get("latest_change"):
            lines.append(f"    Recent change: {b['latest_change']}")
        if b.get("cited_instruments"):
            lines.append("    Cites: " + "; ".join(b["cited_instruments"][:5]))
        lines.append(f"    Excerpt: {b['snippet']}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _snippet(text: str | None, question: str, width: int = _MAX_SNIPPET) -> str:
    """Return a window of ``text`` around the first matching query term."""
    if not text:
        return ""
    terms = [t for t in re.split(r"\W+", question.lower()) if len(t) > 2]
    low = text.lower()
    pos = next((low.find(t) for t in terms if low.find(t) != -1), -1)
    if pos == -1:
        return re.sub(r"\s+", " ", text[:width]).strip()
    start = max(0, pos - width // 3)
    snippet = text[start:start + width]
    return ("…" if start else "") + re.sub(r"\s+", " ", snippet).strip() + "…"
