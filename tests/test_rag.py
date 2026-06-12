"""Graph-RAG smoke test (no network).

Seeds a throwaway SQLite store, makes it the active backend, and checks that
``taxrag`` retrieves the seeded document and assembles a numbered context —
with AI synthesis stubbed off so the test never calls Grok.
"""

from __future__ import annotations

import pytest

import storage
import taxai
import taxrag
from storage.sqlite_store import SqliteStore


@pytest.fixture
def seeded_store(tmp_path, monkeypatch):
    s = SqliteStore(db_url=f"sqlite:///{tmp_path/'rag.db'}")
    s.init_db()
    s.upsert_jurisdiction("JE", "Jersey", "Crown Dependencies", "Revenue Jersey")
    doc_id = s.upsert_document({
        "jurisdiction_code": "JE", "doc_type": "legislation", "doc_key": "es_law",
        "title": "Economic Substance (Jersey) Law 2019", "reference": "L.3/2019",
        "url": "http://example.test/es", "format": "html", "tags": ["economic-substance"]})
    v = s.add_version(doc_id, "h1",
                      "A company carrying on relevant activity must demonstrate "
                      "adequate economic substance in Jersey, including core "
                      "income-generating activities and adequate employees.")
    s.record_change(doc_id, None, v["id"], "new")
    s.add_citations(doc_id, v["id"], [{
        "instrument": "Income Tax (Jersey) Law 1961", "locator": "Article 123",
        "snippet": "...", "jurisdiction": "JE"}])
    # Make this the process-wide active backend for taxstore/taxrag.
    monkeypatch.setattr(storage, "_store", s)
    return s


def test_retriever_walks_the_graph(seeded_store):
    blocks = taxrag.GraphFullTextRetriever().retrieve(
        "economic substance obligations Jersey", k=5)
    assert blocks, "retriever found nothing"
    top = blocks[0]
    assert top["jurisdiction_code"] == "JE"
    assert top["snippet"]
    # graph expansion surfaced the cited instrument
    assert any("Income Tax (Jersey) Law 1961" in c for c in top["cited_instruments"])


def test_answer_degrades_without_ai(seeded_store, monkeypatch):
    monkeypatch.setattr(taxai, "ai_available", lambda: False)
    result = taxrag.answer("What economic substance applies in Jersey?")
    assert result["model"] is None              # no LLM call
    assert result["sources"]                    # but grounded sources returned
    assert "Economic Substance (Jersey) Law 2019" == result["sources"][0]["title"]


def test_answer_no_match_is_graceful(seeded_store, monkeypatch):
    monkeypatch.setattr(taxai, "ai_available", lambda: False)
    result = taxrag.answer("xyznonexistenttopic12345")
    assert result["sources"] == []
    assert "No tracked document" in result["answer"]
