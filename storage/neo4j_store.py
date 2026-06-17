"""Neo4j (graph) storage backend — powers graph-RAG retrieval.

The data is already graph-shaped, so it maps cleanly:

    (:Jurisdiction)-[:HAS_DOCUMENT]->(:Document)-[:HAS_VERSION]->(:Version)
    (:Document)-[:CURRENT_VERSION]->(:Version)
    (:Document)-[:HAS_CHANGE]->(:Change)-[:TO_VERSION|FROM_VERSION]->(:Version)
    (:Version)-[:CITES {locator, snippet}]->(:Instrument)

The relational backend uses integer auto-increment ids that the web app puts in
URLs (``/document/{id}``) and threads between calls. Neo4j has no autoincrement,
and ``id()`` is deprecated on 5.x / AuraDB, so we mint our own stable integer
``uid`` per node from a ``(:Counter)`` — portable across 4.1 (local) and Aura.

Targets Neo4j 4.1 locally (full-text via the ``db.index.fulltext`` procedures,
``CREATE CONSTRAINT ... ASSERT``) and connects unchanged to AuraDB 5.x via Bolt.
"""

from __future__ import annotations

import json
import os
import re

from dotenv import load_dotenv
from neo4j import GraphDatabase

from .base import Storage, utcnow

load_dotenv()

_FULLTEXT_INDEX = "versionText"
_VECTOR_INDEX = "chunkEmbedding"


class Neo4jStore(Storage):
    def __init__(self, uri: str | None = None, user: str | None = None,
                 password: str | None = None, database: str | None = None):
        self.uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.environ.get("NEO4J_USER", "neo4j")
        self.password = password or os.environ.get("NEO4J_PASSWORD", "neo4j")
        self.database = database or os.environ.get("NEO4J_DATABASE", "neo4j")
        self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def close(self) -> None:
        self._driver.close()

    def _session(self):
        return self._driver.session(database=self.database)

    @staticmethod
    def _next_id(tx, name: str) -> int:
        """Atomically increment and return a per-label integer counter."""
        rec = tx.run(
            "MERGE (c:Counter {name:$n}) ON CREATE SET c.value = 0 "
            "SET c.value = c.value + 1 RETURN c.value AS v",
            n=name,
        ).single()
        return rec["v"]

    # ── Lifecycle ──────────────────────────────────────────────────────────

    # (constraint_name, label, property) — single-property uniqueness, the only
    # constraint kind Neo4j Community supports.
    _UNIQUE = [
        ("jurisdiction_code", "Jurisdiction", "code"),
        ("user_email", "User", "email"),
        ("document_uid", "Document", "uid"),
        ("version_uid", "Version", "uid"),
        ("change_uid", "Change", "uid"),
        ("instrument_key", "Instrument", "key"),
        ("counter_name", "Counter", "name"),
    ]

    def init_db(self) -> None:
        with self._session() as s:
            for name, label, prop in self._UNIQUE:
                v = name[0]
                # 5.x/AuraDB uses REQUIRE; 4.x uses ASSERT — try both.
                _run_first_supported(s, [
                    f"CREATE CONSTRAINT {name} IF NOT EXISTS "
                    f"FOR ({v}:{label}) REQUIRE {v}.{prop} IS UNIQUE",
                    f"CREATE CONSTRAINT {name} IF NOT EXISTS "
                    f"ON ({v}:{label}) ASSERT {v}.{prop} IS UNIQUE",
                ])
            # Composite lookup index for upsert_document's natural key
            # (same DDL on 4.x and 5.x).
            _ignore_exists(
                s,
                "CREATE INDEX document_natural_key IF NOT EXISTS "
                "FOR (d:Document) ON (d.jurisdiction_code, d.doc_key)",
            )
            # Full-text index for RAG retrieval. 5.x removed the create procedure
            # in favour of DDL; 4.1 has only the procedure — try both.
            _run_first_supported(s, [
                (f"CREATE FULLTEXT INDEX {_FULLTEXT_INDEX} IF NOT EXISTS "
                 "FOR (v:Version) ON EACH [v.text_content]", {}),
                ("CALL db.index.fulltext.createNodeIndex"
                 "($name, ['Version'], ['text_content'])", {"name": _FULLTEXT_INDEX}),
            ])
        self._seed_admin()

    def _seed_admin(self) -> None:
        import bcrypt

        email = os.environ.get("ADMIN_EMAIL", "admin@jtcgroup.com")
        pw = os.environ.get("ADMIN_PASSWORD", "Funds2$2")
        with self._session() as s:
            exists = s.run("MATCH (u:User {email:$e}) RETURN u LIMIT 1", e=email).single()
            if not exists:
                pw_hash = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
                s.write_transaction(self._create_user, email, pw_hash, "JTC Admin", "admin")

    @classmethod
    def _create_user(cls, tx, email, pw_hash, name, role):
        uid = cls._next_id(tx, "User")
        tx.run(
            "CREATE (u:User {uid:$uid, email:$e, password_hash:$p, name:$n, "
            "role:$r, created_at:$now})",
            uid=uid, e=email, p=pw_hash, n=name, r=role, now=utcnow(),
        )

    # ── Jurisdictions / documents (write) ──────────────────────────────────

    def upsert_jurisdiction(self, code, name, region=None, authority=None) -> None:
        with self._session() as s:
            s.run(
                "MERGE (j:Jurisdiction {code:$code}) "
                "ON CREATE SET j.created_at=$now "
                "SET j.name=$name, j.region=$region, j.authority=$authority",
                code=code, name=name, region=region, authority=authority, now=utcnow(),
            )

    def upsert_document(self, doc: dict) -> int:
        tags = json.dumps(doc.get("tags") or [])
        with self._session() as s:
            return s.write_transaction(self._upsert_document, doc, tags)

    @classmethod
    def _upsert_document(cls, tx, doc, tags) -> int:
        existing = tx.run(
            "MATCH (d:Document {jurisdiction_code:$j, doc_key:$k}) RETURN d.uid AS uid",
            j=doc["jurisdiction_code"], k=doc["doc_key"],
        ).single()
        if existing:
            tx.run(
                "MATCH (d:Document {uid:$uid}) SET d.source=$source, d.doc_type=$doc_type, "
                "d.title=$title, d.reference=$reference, d.url=$url, d.format=$format, d.tags=$tags",
                uid=existing["uid"], source=doc.get("source"), doc_type=doc["doc_type"],
                title=doc["title"], reference=doc.get("reference"), url=doc["url"],
                format=doc.get("format", "html"), tags=tags,
            )
            return existing["uid"]
        uid = cls._next_id(tx, "Document")
        tx.run(
            "MATCH (j:Jurisdiction {code:$j}) "
            "CREATE (d:Document {uid:$uid, jurisdiction_code:$j, source:$source, "
            "doc_type:$doc_type, doc_key:$doc_key, title:$title, reference:$reference, "
            "url:$url, format:$format, tags:$tags, first_seen:$now, last_checked:null, "
            "last_changed:null, check_count:0, status:'active', current_version_id:null, "
            "current_hash:null, last_error:null}) "
            "CREATE (j)-[:HAS_DOCUMENT]->(d)",
            uid=uid, j=doc["jurisdiction_code"], source=doc.get("source"),
            doc_type=doc["doc_type"], doc_key=doc["doc_key"], title=doc["title"],
            reference=doc.get("reference"), url=doc["url"],
            format=doc.get("format", "html"), tags=tags, now=utcnow(),
        )
        return uid

    def latest_version(self, document_id: int) -> dict | None:
        with self._session() as s:
            rec = s.run(
                "MATCH (d:Document {uid:$d})-[:HAS_VERSION]->(v:Version) "
                "RETURN v{.*, id:v.uid} AS v ORDER BY v.version_no DESC LIMIT 1",
                d=document_id,
            ).single()
            return _ver(rec["v"]) if rec else None

    def add_version(self, document_id, content_hash, text_content, raw_path=None,
                    byte_size=None, http_last_modified=None, etag=None) -> dict:
        with self._session() as s:
            return s.write_transaction(
                self._add_version, document_id, content_hash, text_content,
                raw_path, byte_size, http_last_modified, etag,
            )

    @classmethod
    def _add_version(cls, tx, document_id, content_hash, text_content, raw_path,
                     byte_size, http_last_modified, etag) -> dict:
        mx = tx.run(
            "MATCH (d:Document {uid:$d})-[:HAS_VERSION]->(v:Version) "
            "RETURN max(v.version_no) AS mx", d=document_id,
        ).single()["mx"]
        version_no = (mx or 0) + 1
        uid = cls._next_id(tx, "Version")
        now = utcnow()
        tx.run(
            "MATCH (d:Document {uid:$d}) "
            "CREATE (v:Version {uid:$uid, document_id:$d, version_no:$vn, "
            "content_hash:$h, text_content:$t, raw_path:$rp, byte_size:$bs, "
            "char_count:$cc, http_last_modified:$lm, etag:$etag, fetched_at:$now}) "
            "CREATE (d)-[:HAS_VERSION]->(v) "
            "WITH d, v "
            "OPTIONAL MATCH (d)-[r:CURRENT_VERSION]->(:Version) DELETE r "
            "CREATE (d)-[:CURRENT_VERSION]->(v) "
            "SET d.current_version_id=v.uid, d.current_hash=$h, d.last_changed=$now",
            d=document_id, uid=uid, vn=version_no, h=content_hash, t=text_content,
            rp=raw_path, bs=byte_size, cc=len(text_content or ""),
            lm=http_last_modified, etag=etag, now=now,
        )
        return {"id": uid, "version_no": version_no}

    def mark_checked(self, document_id: int, error: str | None = None) -> None:
        with self._session() as s:
            s.run(
                "MATCH (d:Document {uid:$d}) SET d.last_checked=$now, "
                "d.check_count=coalesce(d.check_count,0)+1, "
                "d.status=$st, d.last_error=$err",
                d=document_id, now=utcnow(),
                st="error" if error else "active", err=error,
            )

    def record_change(self, document_id, from_version_id, to_version_id, change_type,
                      added_chars=0, removed_chars=0, diff_text=None, ai_summary=None,
                      ai_impact=None, ai_model=None) -> int:
        with self._session() as s:
            return s.write_transaction(
                self._record_change, document_id, from_version_id, to_version_id,
                change_type, added_chars, removed_chars, diff_text, ai_summary,
                ai_impact, ai_model,
            )

    @classmethod
    def _record_change(cls, tx, document_id, from_version_id, to_version_id,
                       change_type, added_chars, removed_chars, diff_text,
                       ai_summary, ai_impact, ai_model) -> int:
        uid = cls._next_id(tx, "Change")
        tx.run(
            "MATCH (d:Document {uid:$d}) "
            "CREATE (c:Change {uid:$uid, document_id:$d, from_version_id:$fv, "
            "to_version_id:$tv, change_type:$ct, added_chars:$ac, removed_chars:$rc, "
            "diff_text:$dt, ai_summary:$sum, ai_impact:$imp, ai_model:$m, detected_at:$now}) "
            "CREATE (d)-[:HAS_CHANGE]->(c) "
            "WITH c "
            "MATCH (tv:Version {uid:$tv}) CREATE (c)-[:TO_VERSION]->(tv)",
            d=document_id, uid=uid, fv=from_version_id, tv=to_version_id,
            ct=change_type, ac=added_chars, rc=removed_chars, dt=diff_text,
            sum=ai_summary, imp=ai_impact, m=ai_model, now=utcnow(),
        )
        if from_version_id is not None:
            tx.run(
                "MATCH (c:Change {uid:$uid}), (fv:Version {uid:$fv}) "
                "CREATE (c)-[:FROM_VERSION]->(fv)",
                uid=uid, fv=from_version_id,
            )
        return uid

    def add_citations(self, document_id, version_id, cites: list[dict]) -> int:
        if not cites:
            return 0
        with self._session() as s:
            s.write_transaction(self._add_citations, version_id, cites)
        return len(cites)

    @staticmethod
    def _add_citations(tx, version_id, cites):
        for ct in cites:
            instrument = ct.get("instrument")
            if not instrument:
                continue
            jur = ct.get("jurisdiction") or ""
            key = f"{instrument.strip().lower()}|{jur}"
            tx.run(
                "MATCH (v:Version {uid:$vid}) "
                "MERGE (i:Instrument {key:$key}) "
                "ON CREATE SET i.name=$name, i.jurisdiction=$jur "
                "MERGE (v)-[r:CITES]->(i) "
                "SET r.locator=$loc, r.snippet=$snip",
                vid=version_id, key=key, name=instrument, jur=jur,
                loc=ct.get("locator"), snip=ct.get("snippet"),
            )

    def overwrite_version_content(self, version_id, document_id, text_content,
                                  content_hash) -> None:
        with self._session() as s:
            s.run(
                "MATCH (v:Version {uid:$vid}) SET v.text_content=$t, v.content_hash=$h "
                "WITH v MATCH (d:Document {uid:$did}) SET d.current_hash=$h",
                vid=version_id, did=document_id, t=text_content, h=content_hash,
            )

    # ── Scrape runs ────────────────────────────────────────────────────────

    def start_run(self, jurisdiction_code: str) -> int:
        with self._session() as s:
            return s.write_transaction(self._start_run, jurisdiction_code)

    @classmethod
    def _start_run(cls, tx, jurisdiction_code) -> int:
        uid = cls._next_id(tx, "ScrapeRun")
        tx.run(
            "CREATE (r:ScrapeRun {uid:$uid, jurisdiction_code:$j, started_at:$now, "
            "status:'running'}) "
            "WITH r OPTIONAL MATCH (j:Jurisdiction {code:$j}) "
            "FOREACH (_ IN CASE WHEN j IS NULL THEN [] ELSE [1] END | CREATE (r)-[:FOR]->(j))",
            uid=uid, j=jurisdiction_code, now=utcnow(),
        )
        return uid

    def finish_run(self, run_id, checked, new, changed, errors, status="done",
                   notes=None) -> None:
        with self._session() as s:
            s.run(
                "MATCH (r:ScrapeRun {uid:$id}) SET r.finished_at=$now, "
                "r.docs_checked=$ch, r.docs_new=$new, r.docs_changed=$cg, "
                "r.docs_error=$er, r.status=$st, r.notes=$notes",
                id=run_id, now=utcnow(), ch=checked, new=new, cg=changed,
                er=errors, st=status, notes=notes,
            )

    # ── Reads ──────────────────────────────────────────────────────────────

    def get_document(self, jurisdiction_code, doc_key) -> dict | None:
        with self._session() as s:
            rec = s.run(
                "MATCH (d:Document {jurisdiction_code:$j, doc_key:$k}) "
                "RETURN d{.*, id:d.uid} AS d", j=jurisdiction_code, k=doc_key,
            ).single()
            return _doc(rec["d"]) if rec else None

    def get_document_by_id(self, document_id: int) -> dict | None:
        with self._session() as s:
            rec = s.run(
                "MATCH (d:Document {uid:$i}) RETURN d{.*, id:d.uid} AS d", i=document_id,
            ).single()
            return _doc(rec["d"]) if rec else None

    def list_versions(self, document_id: int) -> list[dict]:
        with self._session() as s:
            rows = s.run(
                "MATCH (d:Document {uid:$i})-[:HAS_VERSION]->(v:Version) "
                "RETURN v{.*, id:v.uid} AS v ORDER BY v.version_no DESC", i=document_id,
            ).data()
            return [_ver(r["v"]) for r in rows]

    _CHANGE_PROJECTION = (
        "c{.*, id:c.uid, title:d.title, jurisdiction_code:d.jurisdiction_code, "
        "doc_type:d.doc_type, url:d.url, document_id:d.uid}"
    )

    def recent_changes(self, limit=50, jurisdiction_code=None) -> list[dict]:
        where = "WHERE d.jurisdiction_code = $j " if jurisdiction_code else ""
        with self._session() as s:
            rows = s.run(
                f"MATCH (d:Document)-[:HAS_CHANGE]->(c:Change) {where}"
                f"RETURN {self._CHANGE_PROJECTION} AS c "
                "ORDER BY c.detected_at DESC LIMIT $lim",
                j=jurisdiction_code, lim=limit,
            ).data()
            return [_chg(r["c"]) for r in rows]

    def list_changes_for_document(self, document_id: int) -> list[dict]:
        with self._session() as s:
            rows = s.run(
                f"MATCH (d:Document {{uid:$i}})-[:HAS_CHANGE]->(c:Change) "
                f"RETURN {self._CHANGE_PROJECTION} AS c "
                "ORDER BY c.detected_at DESC", i=document_id,
            ).data()
            return [_chg(r["c"]) for r in rows]

    def get_change(self, change_id: int) -> dict | None:
        with self._session() as s:
            rec = s.run(
                f"MATCH (d:Document)-[:HAS_CHANGE]->(c:Change {{uid:$i}}) "
                f"RETURN {self._CHANGE_PROJECTION} AS c", i=change_id,
            ).single()
            return _chg(rec["c"]) if rec else None

    def list_citations(self, document_id: int, limit: int = 40) -> list[dict]:
        with self._session() as s:
            rows = s.run(
                "MATCH (d:Document {uid:$i})-[:HAS_VERSION]->(:Version)-[c:CITES]->(i:Instrument) "
                "RETURN DISTINCT i.name AS cited_instrument, c.locator AS cited_locator "
                "LIMIT $lim", i=document_id, lim=limit,
            ).data()
            return rows

    def get_jurisdiction(self, code: str) -> dict | None:
        with self._session() as s:
            rec = s.run(
                "MATCH (j:Jurisdiction {code:$c}) RETURN j{.*} AS j", c=code,
            ).single()
            return dict(rec["j"]) if rec else None

    def list_jurisdictions_with_counts(self) -> list[dict]:
        with self._session() as s:
            rows = s.run(
                "MATCH (j:Jurisdiction) "
                "OPTIONAL MATCH (j)-[:HAS_DOCUMENT]->(d:Document) "
                "RETURN j.code AS code, j.name AS name, j.authority AS authority, "
                "count(d) AS docs ORDER BY j.name",
            ).data()
            return rows

    def list_documents_for_jurisdiction(self, code: str) -> list[dict]:
        with self._session() as s:
            rows = s.run(
                "MATCH (j:Jurisdiction {code:$c})-[:HAS_DOCUMENT]->(d:Document) "
                "OPTIONAL MATCH (d)-[:HAS_VERSION]->(v:Version) "
                "WITH d, count(v) AS versions "
                "RETURN d{.*, id:d.uid, versions:versions} AS d "
                "ORDER BY d.doc_type, d.title", c=code,
            ).data()
            return [_doc(r["d"]) for r in rows]

    def stats(self) -> dict:
        with self._session() as s:
            counts = s.run(
                "OPTIONAL MATCH (j:Jurisdiction) WITH count(j) AS jc "
                "OPTIONAL MATCH (d:Document) WITH jc, count(d) AS dc "
                "OPTIONAL MATCH (v:Version) WITH jc, dc, count(v) AS vc "
                "OPTIONAL MATCH (c:Change) RETURN jc, dc, vc, count(c) AS cc",
            ).single()
            by_jur = s.run(
                "MATCH (d:Document) RETURN d.jurisdiction_code AS jurisdiction_code, "
                "count(*) AS docs ORDER BY jurisdiction_code",
            ).data()
            return {
                "jurisdictions": counts["jc"], "documents": counts["dc"],
                "versions": counts["vc"], "changes": counts["cc"],
                "by_jurisdiction": by_jur,
            }

    def get_user_by_email(self, email: str) -> dict | None:
        with self._session() as s:
            rec = s.run(
                "MATCH (u:User {email:$e}) RETURN u.uid AS id, u.password_hash AS password_hash",
                e=email.strip().lower(),
            ).single()
            return dict(rec) if rec else None

    # ── Graph-RAG retrieval (Lucene full-text over current versions) ────────

    def search_versions(self, query: str, limit: int = 8) -> list[dict]:
        terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 2]
        if not terms:
            return []
        lucene = " OR ".join(terms)
        with self._session() as s:
            rows = s.run(
                "CALL db.index.fulltext.queryNodes($idx, $q) YIELD node, score "
                "MATCH (d:Document)-[:CURRENT_VERSION]->(node) "
                "RETURN d.uid AS document_id, d.doc_key AS doc_key, d.title AS title, "
                "d.jurisdiction_code AS jurisdiction_code, d.doc_type AS doc_type, "
                "d.url AS url, d.reference AS reference, node.version_no AS version_no, "
                "node.text_content AS text_content, score "
                "ORDER BY score DESC LIMIT $lim",
                idx=_FULLTEXT_INDEX, q=lucene, lim=limit,
            ).data()
            return rows

    # ── Vector retrieval ───────────────────────────────────────────────────
    def ensure_vector_index(self, dim: int) -> None:
        ddl = (f"CREATE VECTOR INDEX {_VECTOR_INDEX} IF NOT EXISTS "
               "FOR (c:Chunk) ON (c.embedding) OPTIONS {indexConfig: "
               f"{{`vector.dimensions`: {int(dim)}, "
               "`vector.similarity_function`: 'cosine'}}")
        with self._session() as s:
            try:
                s.run(ddl).consume()
            except Exception as e:  # noqa: BLE001 — 4.x has no vector index; optional
                print(f"[neo4j] vector index unavailable: {e}")

    def index_version_chunks(self, version_id, document_id, chunks) -> int:
        with self._session() as s:
            return s.write_transaction(
                self._index_version_chunks, version_id, document_id, chunks)

    @staticmethod
    def _index_version_chunks(tx, version_id, document_id, chunks) -> int:
        tx.run("MATCH (v:Version {uid:$vid})-[:HAS_CHUNK]->(c:Chunk) "
               "DETACH DELETE c", vid=version_id)
        if not chunks:
            return 0
        payload = [{"ord": o, "text": t, "emb": e} for (o, t, e) in chunks]
        tx.run("MATCH (v:Version {uid:$vid}) UNWIND $chunks AS ch "
               "CREATE (v)-[:HAS_CHUNK]->(:Chunk {document_id:$did, "
               "version_id:$vid, ord:ch.ord, text:ch.text, embedding:ch.emb})",
               vid=version_id, did=document_id, chunks=payload)
        return len(payload)

    def vector_search_chunks(self, query_vector, limit: int = 8) -> list[dict]:
        with self._session() as s:
            return s.run(
                "CALL db.index.vector.queryNodes($idx, $k, $vec) YIELD node AS c, score "
                "MATCH (d:Document)-[:CURRENT_VERSION]->(:Version)-[:HAS_CHUNK]->(c) "
                "RETURN d.uid AS document_id, d.doc_key AS doc_key, d.title AS title, "
                "d.jurisdiction_code AS jurisdiction_code, d.doc_type AS doc_type, "
                "d.url AS url, d.reference AS reference, c.text AS chunk_text, score "
                "ORDER BY score DESC LIMIT $lim",
                idx=_VECTOR_INDEX, k=max(limit * 4, limit), vec=query_vector, lim=limit,
            ).data()

    def count_chunks(self) -> int:
        with self._session() as s:
            return s.run("MATCH (c:Chunk) RETURN count(c) AS n").single()["n"]

    # ── Tax forms ──────────────────────────────────────────────────────────
    _FORM_PROPS = ("jurisdiction_code", "category", "form_type", "form_key", "title",
                   "authority", "url", "file_path", "year", "who_files", "deadline",
                   "frequency", "summary", "legislation_ref", "filing_type")
    _FORM_RETURN = (
        "f.uid AS id, f.jurisdiction_code AS jurisdiction_code, f.category AS category, "
        "f.form_type AS form_type, f.form_key AS form_key, f.title AS title, "
        "f.authority AS authority, f.url AS url, f.file_path AS file_path, "
        "f.year AS year, f.who_files AS who_files, f.deadline AS deadline, "
        "f.frequency AS frequency, f.summary AS summary, f.legislation_ref AS legislation_ref, f.filing_type AS filing_type")

    def upsert_form(self, form: dict) -> int:
        # Only provided keys → partial upserts aren't destructive.
        props = {k: form[k] for k in self._FORM_PROPS if k in form}
        with self._session() as s:
            return s.write_transaction(self._upsert_form, props)

    @classmethod
    def _upsert_form(cls, tx, props) -> int:
        jc, fk, now = props["jurisdiction_code"], props["form_key"], utcnow()
        ex = tx.run("MATCH (f:Form {jurisdiction_code:$jc, form_key:$fk}) "
                    "RETURN f.uid AS uid", jc=jc, fk=fk).single()
        if ex:
            tx.run("MATCH (f:Form {uid:$uid}) SET f += $props, f.last_seen=$now",
                   uid=ex["uid"], props=props, now=now)
            return ex["uid"]
        uid = cls._next_id(tx, "Form")
        tx.run("MATCH (j:Jurisdiction {code:$jc}) "
               "CREATE (f:Form {uid:$uid, first_seen:$now, last_seen:$now}) "
               "SET f += $props CREATE (j)-[:HAS_FORM]->(f)",
               jc=jc, uid=uid, props=props, now=now)
        return uid

    def get_form(self, form_id: int) -> dict | None:
        with self._session() as s:
            r = s.run(f"MATCH (f:Form {{uid:$i}}) RETURN {self._FORM_RETURN}",
                      i=form_id).single()
            return dict(r) if r else None

    def list_forms(self, jurisdiction_code=None, category=None, limit=500) -> list[dict]:
        where = []
        p = {"lim": limit}
        if jurisdiction_code:
            where.append("f.jurisdiction_code=$jc"); p["jc"] = jurisdiction_code
        if category:
            where.append("f.category=$cat"); p["cat"] = category
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._session() as s:
            return s.run(
                f"MATCH (f:Form){clause} RETURN {self._FORM_RETURN} "
                "ORDER BY f.jurisdiction_code, f.category, f.form_type, f.title "
                "LIMIT $lim", **p).data()

    def search_forms(self, query: str, limit: int = 10) -> list[dict]:
        terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 2]
        if not terms:
            return []
        rows = self.list_forms(limit=2000)
        scored = []
        for r in rows:
            hay = " ".join(str(r.get(k) or "") for k in
                           ("title", "category", "form_type", "summary",
                            "who_files", "jurisdiction_code")).lower()
            score = sum(hay.count(t) for t in terms)
            if score:
                scored.append({**r, "score": float(score)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def build_provenance_edges(self) -> dict:
        from .base import legislation_label
        with self._session() as s:
            pairs = [{"uid": r["uid"], "url": r["ref"],
                      "label": legislation_label(r["ref"])}
                     for r in s.run(
                         "MATCH (f:Form) WHERE coalesce(f.legislation_ref,'')<>'' "
                         "RETURN f.uid AS uid, f.legislation_ref AS ref").data()]
            if pairs:
                s.run(
                    "UNWIND $pairs AS p "
                    "MERGE (l:Legislation {url:p.url}) "
                    "  ON CREATE SET l.label=p.label "
                    "WITH p,l MATCH (f:Form {uid:p.uid}) "
                    "MERGE (f)-[:IMPLEMENTS]->(l)", pairs=pairs)
            sourced = s.run(
                "MATCH (l:Legislation),(d:Document) WHERE d.url=l.url "
                "MERGE (l)-[:SOURCED_FROM]->(d) RETURN count(*) AS c").single()["c"]
            nleg = s.run("MATCH (l:Legislation) RETURN count(l) AS c").single()["c"]
            nimpl = s.run("MATCH (:Form)-[r:IMPLEMENTS]->() "
                          "RETURN count(r) AS c").single()["c"]
        return {"legislation_nodes": nleg, "implements_edges": nimpl,
                "sourced_from_edges": sourced}

    # ── Entities ───────────────────────────────────────────────────────────
    _ENTITY_PROPS = ("name", "type", "domicile", "jurisdictions", "fy_end",
                     "activities", "client_ref", "status")
    _ENTITY_RETURN = (
        "e.uid AS id, e.name AS name, e.type AS type, e.domicile AS domicile, "
        "coalesce(e.jurisdictions,[]) AS jurisdictions, e.fy_end AS fy_end, "
        "coalesce(e.activities,[]) AS activities, e.client_ref AS client_ref, "
        "e.status AS status")

    def upsert_entity(self, entity: dict) -> int:
        props = {k: entity[k] for k in self._ENTITY_PROPS if k in entity}
        key = entity.get("client_ref") or entity.get("name")
        with self._session() as s:
            return s.write_transaction(self._upsert_entity, key, props)

    @classmethod
    def _upsert_entity(cls, tx, key, props) -> int:
        now = utcnow()
        ex = tx.run("MATCH (e:Entity) WHERE coalesce(e.client_ref,e.name)=$k "
                    "RETURN e.uid AS uid", k=key).single()
        if ex:
            tx.run("MATCH (e:Entity {uid:$uid}) SET e += $props, e.updated_at=$now",
                   uid=ex["uid"], props=props, now=now)
            return ex["uid"]
        uid = cls._next_id(tx, "Entity")
        props.setdefault("status", "active")
        tx.run("CREATE (e:Entity {uid:$uid, created_at:$now, updated_at:$now}) "
               "SET e += $props", uid=uid, props=props, now=now)
        for jc in (props.get("jurisdictions") or []):
            tx.run("MATCH (e:Entity {uid:$uid}) MERGE (j:Jurisdiction {code:$jc}) "
                   "MERGE (e)-[:IN_JURISDICTION]->(j)", uid=uid, jc=jc)
        return uid

    def get_entity(self, entity_id: int) -> dict | None:
        with self._session() as s:
            r = s.run(f"MATCH (e:Entity {{uid:$i}}) RETURN {self._ENTITY_RETURN}",
                      i=entity_id).single()
            return dict(r) if r else None

    def list_entities(self, limit: int = 500) -> list[dict]:
        with self._session() as s:
            return [dict(r) for r in s.run(
                f"MATCH (e:Entity) RETURN {self._ENTITY_RETURN} "
                "ORDER BY e.name LIMIT $lim", lim=limit)]

    def delete_entity(self, entity_id: int) -> None:
        with self._session() as s:
            s.run("MATCH (e:Entity {uid:$i}) DETACH DELETE e", i=entity_id)

    def count_entities(self) -> int:
        with self._session() as s:
            return s.run("MATCH (e:Entity) RETURN count(e) AS n").single()["n"]

    # ── Obligations ────────────────────────────────────────────────────────
    _OB_DESC = ("title", "jurisdiction_code", "category", "deadline", "period")
    _OB_RETURN = (
        "o.uid AS id, o.entity_id AS entity_id, o.form_id AS form_id, "
        "o.title AS title, o.jurisdiction_code AS jurisdiction_code, "
        "o.category AS category, o.deadline AS deadline, o.period AS period, "
        "o.status AS status, coalesce(o.verified,false) AS verified")

    def upsert_obligation(self, ob: dict) -> int:
        desc = {k: ob.get(k) for k in self._OB_DESC}
        with self._session() as s:
            return s.write_transaction(
                self._upsert_obligation, ob["entity_id"], ob["form_id"], desc)

    @classmethod
    def _upsert_obligation(cls, tx, eid, fid, desc) -> int:
        now = utcnow()
        ex = tx.run("MATCH (o:Obligation {entity_id:$e, form_id:$f}) "
                    "RETURN o.uid AS uid", e=eid, f=fid).single()
        if ex:  # refresh descriptive fields only — keep status/verified
            tx.run("MATCH (o:Obligation {uid:$uid}) SET o += $desc, o.updated_at=$now",
                   uid=ex["uid"], desc=desc, now=now)
            return ex["uid"]
        uid = cls._next_id(tx, "Obligation")
        tx.run("MATCH (e:Entity {uid:$e}) "
               "CREATE (o:Obligation {uid:$uid, entity_id:$e, form_id:$f, "
               "status:'not_started', verified:false, created_at:$now, updated_at:$now}) "
               "SET o += $desc CREATE (e)-[:HAS_OBLIGATION]->(o) "
               "WITH o MATCH (fm:Form {uid:$f}) MERGE (o)-[:FOR_FORM]->(fm)",
               e=eid, f=fid, uid=uid, desc=desc, now=now)
        return uid

    def get_obligation(self, obligation_id: int) -> dict | None:
        with self._session() as s:
            r = s.run(f"MATCH (o:Obligation {{uid:$i}}) RETURN {self._OB_RETURN}",
                      i=obligation_id).single()
            return dict(r) if r else None

    def list_obligations(self, entity_id=None, status=None, limit=1000) -> list[dict]:
        where = []
        if entity_id is not None:
            where.append("o.entity_id=$eid")
        if status:
            where.append("o.status=$status")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with self._session() as s:
            return [dict(r) for r in s.run(
                f"MATCH (o:Obligation) {clause} RETURN {self._OB_RETURN} "
                "ORDER BY o.jurisdiction_code, o.category LIMIT $lim",
                eid=entity_id, status=status, lim=limit)]

    def set_obligation_status(self, obligation_id: int, status: str) -> None:
        with self._session() as s:
            s.run("MATCH (o:Obligation {uid:$i}) SET o.status=$s, o.updated_at=$now",
                  i=obligation_id, s=status, now=utcnow())

    def set_obligation_verified(self, obligation_id: int, verified: bool) -> None:
        with self._session() as s:
            s.run("MATCH (o:Obligation {uid:$i}) SET o.verified=$v, o.updated_at=$now",
                  i=obligation_id, v=bool(verified), now=utcnow())

    def delete_obligation(self, obligation_id: int) -> None:
        with self._session() as s:
            s.run("MATCH (o:Obligation {uid:$i}) DETACH DELETE o", i=obligation_id)

    # ── Chat history ───────────────────────────────────────────────────────
    def create_chat_session(self, user_email: str, title: str = "") -> int:
        with self._session() as s:
            return s.write_transaction(self._create_chat_session, user_email, title)

    @classmethod
    def _create_chat_session(cls, tx, user_email, title) -> int:
        uid = cls._next_id(tx, "ChatSession")
        now = utcnow()
        tx.run("CREATE (cs:ChatSession {uid:$uid, user_email:$e, title:$t, "
               "created_at:$now, updated_at:$now})",
               uid=uid, e=user_email, t=title or "New chat", now=now)
        return uid

    def add_chat_message(self, session_id: int, role: str, content: str) -> None:
        now = utcnow()
        with self._session() as s:
            s.run("MATCH (cs:ChatSession {uid:$sid}) "
                  "CREATE (cs)-[:HAS_MESSAGE]->(m:ChatMessage {role:$r, content:$c, created_at:$now}) "
                  "SET cs.updated_at=$now",
                  sid=session_id, r=role, c=content, now=now).consume()

    def list_chat_sessions(self, user_email: str, limit: int = 30) -> list[dict]:
        with self._session() as s:
            return s.run(
                "MATCH (cs:ChatSession {user_email:$e}) "
                "RETURN cs.uid AS id, cs.title AS title, cs.updated_at AS updated_at "
                "ORDER BY cs.updated_at DESC LIMIT $lim",
                e=user_email, lim=limit).data()

    def get_chat_messages(self, session_id: int) -> list[dict]:
        with self._session() as s:
            return s.run(
                "MATCH (cs:ChatSession {uid:$sid})-[:HAS_MESSAGE]->(m:ChatMessage) "
                "RETURN m.role AS role, m.content AS content, m.created_at AS created_at "
                "ORDER BY m.created_at",
                sid=session_id).data()


# Neo4j drops properties set to null (it has no null storage), so a node read
# back omits keys that were never set. The relational backend always returns the
# full column set (with None), and the web app accesses several nullable fields
# by key. These canonical key sets re-materialise the missing keys as None so
# both backends hand the app identically-shaped dicts.
_DOC_KEYS = ("id", "jurisdiction_code", "source", "doc_type", "doc_key", "title",
             "reference", "url", "format", "tags", "current_version_id",
             "current_hash", "first_seen", "last_checked", "last_changed",
             "check_count", "status", "last_error")
_VER_KEYS = ("id", "document_id", "version_no", "content_hash", "text_content",
             "raw_path", "byte_size", "char_count", "http_last_modified", "etag",
             "fetched_at")
_CHG_KEYS = ("id", "document_id", "from_version_id", "to_version_id", "change_type",
             "added_chars", "removed_chars", "diff_text", "ai_summary", "ai_impact",
             "ai_model", "detected_at")


def _fill(d: dict, keys) -> dict:
    """Return ``d`` with every key in ``keys`` present (missing → None)."""
    return {**{k: None for k in keys}, **dict(d)}


def _doc(d):
    return _fill(d, _DOC_KEYS)


def _ver(v):
    return _fill(v, _VER_KEYS)


def _chg(c):
    return _fill(c, _CHG_KEYS)


def _is_exists_error(msg: str) -> bool:
    return "already exists" in msg or "equivalent" in msg or "exist" in msg


def _ignore_exists(session, stmt: str, **params) -> None:
    """Run a DDL/procedure statement, tolerating 'already exists' errors.

    Neo4j 4.1 has no IF NOT EXISTS for full-text procedures, and re-running
    constraint DDL can raise 'equivalent' errors — both are no-ops for us.
    """
    try:
        session.run(stmt, **params).consume()
    except Exception as e:  # noqa: BLE001
        if _is_exists_error(str(e).lower()):
            return
        raise


def _run_first_supported(session, variants) -> None:
    """Run the first syntactically-supported variant of a DDL statement.

    Bridges Neo4j 4.x (local) and 5.x/AuraDB, whose constraint and full-text
    index syntaxes differ. Each variant is a ``str`` or ``(str, params)`` tuple;
    we try them in order, treating 'already exists' as success and a syntax
    error as "try the next dialect". Raises the last error if none apply.
    """
    last = None
    for v in variants:
        stmt, params = v if isinstance(v, tuple) else (v, {})
        try:
            session.run(stmt, **params).consume()
            return
        except Exception as e:  # noqa: BLE001
            if _is_exists_error(str(e).lower()):
                return
            last = e
    if last:
        raise last
