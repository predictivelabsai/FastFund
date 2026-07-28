"""Storage contract — one suite, run against every backend.

Asserts that SqliteStore and Neo4jStore are behaviourally identical through the
``Storage`` interface, so swapping ``DATA_STORAGE`` cannot silently change app
behaviour. The Neo4j cases skip automatically if no Neo4j is reachable, and
clean up their own subgraph (test jurisdiction ``T9``) so they don't disturb
dev/migrated data.
"""

from __future__ import annotations

import pytest

from storage.sqlite_store import SqliteStore
from storage.neo4j_store import Neo4jStore

TEST_CODE = "T9"
TERM = "zzqxsubstance"  # distinctive token so search matches only our fixture
INSTR_KEY = f"test instrument 2026|{TEST_CODE}"


@pytest.fixture
def sqlite_store(tmp_path):
    return SqliteStore(db_url=f"sqlite:///{tmp_path/'contract.db'}")


@pytest.fixture
def neo4j_store():
    try:
        s = Neo4jStore()
        with s._session() as sess:
            sess.run("RETURN 1").consume()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Neo4j not reachable: {e}")
    _wipe_neo4j(s)
    yield s
    _wipe_neo4j(s)
    s.close()


def _wipe_neo4j(s: Neo4jStore):
    with s._session() as sess:
        sess.run(
            "MATCH (j:Jurisdiction {code:$c}) "
            "OPTIONAL MATCH (j)-[:HAS_DOCUMENT]->(d:Document) "
            "OPTIONAL MATCH (d)-[:HAS_VERSION]->(v:Version) "
            "OPTIONAL MATCH (d)-[:HAS_CHANGE]->(ch:Change) "
            "DETACH DELETE j, d, v, ch", c=TEST_CODE).consume()
        sess.run("MATCH (r:ScrapeRun {jurisdiction_code:$c}) DETACH DELETE r",
                 c=TEST_CODE).consume()
        sess.run("MATCH (i:Instrument {key:$k}) DETACH DELETE i", k=INSTR_KEY).consume()


@pytest.fixture(params=["sqlite", "neo4j"])
def store(request):
    return request.getfixturevalue(f"{request.param}_store")


def test_storage_contract(store):
    store.init_db()

    # Jurisdiction
    store.upsert_jurisdiction(TEST_CODE, "Testland", "TestRegion", "Test Authority")
    jur = store.get_jurisdiction(TEST_CODE)
    assert jur["name"] == "Testland"
    assert jur["authority"] == "Test Authority"

    # Document — idempotent upsert returns the same id
    doc = {"jurisdiction_code": TEST_CODE, "source": "src", "doc_type": "legislation",
           "doc_key": "test_law", "title": "Test Law 2026", "reference": "L.1/2026",
           "url": f"http://example.test/{TEST_CODE}/law", "format": "html",
           "tags": ["alpha", "beta"]}
    doc_id = store.upsert_document(doc)
    assert store.upsert_document(doc) == doc_id

    got = store.get_document(TEST_CODE, "test_law")
    assert got["id"] == doc_id and got["title"] == "Test Law 2026"
    # nullable field present as a key on both backends
    assert "last_error" in store.get_document_by_id(doc_id)

    # Versions (immutable history)
    v1 = store.add_version(doc_id, "hash1", f"Article 1. {TERM} applies to funds.")
    assert v1["version_no"] == 1
    store.mark_checked(doc_id)
    assert store.latest_version(doc_id)["content_hash"] == "hash1"

    store.record_change(doc_id, None, v1["id"], "new")
    store.add_citations(doc_id, v1["id"], [{
        "instrument": "Test Instrument 2026", "locator": "Article 1",
        "snippet": "Article 1 ...", "jurisdiction": TEST_CODE}])

    v2 = store.add_version(doc_id, "hash2", f"Article 1. {TERM} revised in 2026.")
    assert v2["version_no"] == 2
    chg_id = store.record_change(doc_id, v1["id"], v2["id"], "amended",
                                 added_chars=10, removed_chars=3, diff_text="- a\n+ b",
                                 ai_summary="Revised.", ai_impact="Review filings.",
                                 ai_model="test")

    # Reads
    assert [v["version_no"] for v in store.list_versions(doc_id)] == [2, 1]
    assert store.latest_version(doc_id)["content_hash"] == "hash2"

    recent = store.recent_changes(10, jurisdiction_code=TEST_CODE)
    assert len(recent) == 2
    assert {c["change_type"] for c in recent} == {"new", "amended"}
    assert all(c["title"] == "Test Law 2026" for c in recent)  # enriched join

    one = store.get_change(chg_id)
    assert one["diff_text"] == "- a\n+ b"  # nullable key present and correct
    assert one["jurisdiction_code"] == TEST_CODE
    assert one["from_version_id"] == v1["id"] and one["to_version_id"] == v2["id"]
    assert len(store.list_changes_for_document(doc_id)) == 2

    cites = store.list_citations(doc_id)
    assert any(c["cited_instrument"] == "Test Instrument 2026" for c in cites)

    docs = store.list_documents_for_jurisdiction(TEST_CODE)
    assert len(docs) == 1 and docs[0]["versions"] == 2 and docs[0]["status"] == "active"

    counts = {j["code"]: j["docs"] for j in store.list_jurisdictions_with_counts()}
    assert counts.get(TEST_CODE) == 1

    # Scrape run lifecycle
    run_id = store.start_run(TEST_CODE)
    store.finish_run(run_id, checked=1, new=1, changed=0, errors=0)

    # Admin user seeded by init_db
    admin = store.get_user_by_email("admin@fastfund.org")
    assert admin and admin["password_hash"]

    # Full-text / graph-RAG retrieval finds our distinctive token
    hits = store.search_versions(f"{TERM} funds", limit=5)
    assert any(h["doc_key"] == "test_law" for h in hits)

    # overwrite_version_content (demo/repair path)
    store.overwrite_version_content(v2["id"], doc_id, "edited text", "hash3")
    assert store.latest_version(doc_id)["content_hash"] == "hash3"
