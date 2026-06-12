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
import re

import taxai
import taxstore as store

_MAX_SNIPPET = 600


class Retriever(abc.ABC):
    """Turns a question into a ranked list of grounded context blocks."""

    @abc.abstractmethod
    def retrieve(self, question: str, k: int = 6) -> list[dict]:
        ...


class GraphFullTextRetriever(Retriever):
    """Full-text seed + citation/change graph expansion via the storage layer."""

    def retrieve(self, question: str, k: int = 6) -> list[dict]:
        hits = store.search_versions(question, limit=k)
        blocks: list[dict] = []
        for h in hits:
            doc_id = h["document_id"]
            citations = store.list_citations(doc_id, limit=8)
            changes = store.list_changes_for_document(doc_id)
            latest_summary = next(
                (c["ai_summary"] for c in changes if c.get("ai_summary")), None
            )
            blocks.append({
                "document_id": doc_id,
                "doc_key": h.get("doc_key"),
                "title": h["title"],
                "jurisdiction_code": h["jurisdiction_code"],
                "doc_type": h.get("doc_type"),
                "url": h.get("url"),
                "reference": h.get("reference"),
                "score": h.get("score"),
                "snippet": _snippet(h.get("text_content"), question),
                "cited_instruments": [c["cited_instrument"] for c in citations
                                      if c.get("cited_instrument")],
                "latest_change": latest_summary,
            })
        return blocks


_SYS = (
    "You are a tax-technical analyst supporting a fund-management back office. "
    "Answer the question using ONLY the numbered context provided — tracked "
    "official tax documents across JTC fund jurisdictions. Be precise and "
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
    retriever = retriever or GraphFullTextRetriever()
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
