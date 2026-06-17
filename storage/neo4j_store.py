"""Graph storage backend for SFO Hub (Neo4j / AuraDB) — the default backend.

The data is naturally graph-shaped:

```
(:SFO)-[:HOLDS_SERVICE]->(:Service)
(:SFO)-[:HAS_CONVERSATION]->(:Conversation)-[:HAS_MESSAGE]->(:Message)
(:SFO)-[:RECOMMENDED {kind, score, status, ...}]->(:Service)
(:Service)-[:CROSS_SELLS_TO {weight}]->(:Service)
```

Design mirrors TaxHub's Neo4j store so the same operational story holds:

* **Stable integer ids** are minted from a single ``(:Counter)`` node, so
  ``/sfo/{id}`` URLs and id-based joins work identically to the relational
  backend and the deprecated ``id()`` function is avoided — portable straight to
  AuraDB.
* **Version-tolerant DDL** — ``init_db`` tries 5.x constraint syntax and falls
  back to 4.x, so the same code runs on a local Neo4j 4.x and AuraDB 5.x.
* **``asset_mix``** (a map) is JSON-encoded into a string property — Neo4j
  property values must be primitives or arrays of primitives; the list fields
  (jurisdictions, current_services, pain_points, keywords) are stored as native
  string arrays and re-materialised to ``[]`` on read so both backends behave
  identically.
"""

from __future__ import annotations

import json
import os

from .base import Storage, utcnow

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "change-me")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

_LISTS = ("jurisdictions", "current_services", "pain_points")


class Neo4jStore(Storage):
    def __init__(self):
        from neo4j import GraphDatabase
        self._driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        self._db = NEO4J_DATABASE

    # ── low-level helpers ───────────────────────────────────────────────────
    def _run(self, cypher: str, **params):
        with self._driver.session(database=self._db) as s:
            return list(s.run(cypher, **params))

    def _write(self, cypher: str, **params):
        with self._driver.session(database=self._db) as s:
            return s.execute_write(lambda tx: list(tx.run(cypher, **params)))

    def _next_id(self) -> int:
        rows = self._write(
            "MERGE (c:Counter {name:'global'}) "
            "ON CREATE SET c.value=0 SET c.value=c.value+1 RETURN c.value AS v")
        return rows[0]["v"]

    # ── Lifecycle ───────────────────────────────────────────────────────────
    def init_db(self) -> None:
        # Version-tolerant uniqueness constraints (5.x REQUIRE → 4.x ASSERT).
        constraints = [
            ("SFO", "uid"), ("SFO", "client_ref"), ("Service", "uid"),
            ("Service", "key"), ("Recommendation", "uid"),
            ("Conversation", "uid"), ("User", "email"),
        ]
        for label, prop in constraints:
            try:
                self._run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) "
                          f"REQUIRE n.{prop} IS UNIQUE")
            except Exception:  # noqa: BLE001 — fall back to 4.x syntax
                try:
                    self._run(f"CREATE CONSTRAINT ON (n:{label}) "
                              f"ASSERT n.{prop} IS UNIQUE")
                except Exception:  # noqa: BLE001 — already exists
                    pass
        self._seed_admin()

    def _seed_admin(self) -> None:
        email = os.environ.get("ADMIN_EMAIL", "admin@jtcgroup.com")
        pw = os.environ.get("ADMIN_PASSWORD", "change-me")
        if self.get_user_by_email(email):
            return
        import bcrypt
        h = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        self._write("MERGE (u:User {email:$e}) "
                    "ON CREATE SET u.password_hash=$h, u.role='admin', u.created_at=$t",
                    e=email, h=h, t=utcnow())

    # ── Users ────────────────────────────────────────────────────────────────
    def get_user_by_email(self, email: str) -> dict | None:
        rows = self._run("MATCH (u:User {email:$e}) "
                         "RETURN u.email AS email, u.password_hash AS password_hash, "
                         "u.role AS role", e=email)
        if not rows:
            return None
        d = dict(rows[0])
        d["id"] = d["email"]
        return d

    # ── SFOs ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _sfo_props(sfo: dict) -> dict:
        return {
            "name": sfo.get("name"), "family_name": sfo.get("family_name"),
            "aum_usd": sfo.get("aum_usd"), "family_size": sfo.get("family_size"),
            "generations": sfo.get("generations"), "domicile": sfo.get("domicile"),
            "jurisdictions": sfo.get("jurisdictions") or [],
            "current_services": sfo.get("current_services") or [],
            "pain_points": sfo.get("pain_points") or [],
            "asset_mix": json.dumps(sfo.get("asset_mix") or {}),
            "stage": sfo.get("stage") or "lead",
            "contact_name": sfo.get("contact_name"),
            "contact_email": sfo.get("contact_email"),
        }

    @staticmethod
    def _materialise_sfo(d: dict) -> dict:
        for k in _LISTS:
            d[k] = d.get(k) or []
        d["asset_mix"] = json.loads(d.get("asset_mix") or "{}")
        return d

    def upsert_sfo(self, sfo: dict) -> int:
        ref = sfo.get("client_ref") or sfo.get("name")
        props = self._sfo_props(sfo)
        rows = self._write(
            "MERGE (n:SFO {client_ref:$ref}) "
            "ON CREATE SET n.uid=$uid, n.created_at=$t "
            "SET n += $props RETURN n.uid AS uid",
            ref=ref, uid=self._next_id(), t=utcnow(), props=props)
        return rows[0]["uid"]

    def get_sfo(self, sfo_id: int) -> dict | None:
        rows = self._run("MATCH (n:SFO {uid:$i}) RETURN n", i=sfo_id)
        if not rows:
            return None
        d = dict(rows[0]["n"])
        d["id"] = d.pop("uid")
        return self._materialise_sfo(d)

    def list_sfos(self, stage: str | None = None, limit: int = 500) -> list[dict]:
        where = " WHERE n.stage=$stage" if stage else ""
        rows = self._run(f"MATCH (n:SFO){where} RETURN n "
                         "ORDER BY n.aum_usd DESC LIMIT $l", stage=stage, l=limit)
        out = []
        for r in rows:
            d = dict(r["n"])
            d["id"] = d.pop("uid")
            out.append(self._materialise_sfo(d))
        return out

    def search_sfos(self, query: str, limit: int = 10) -> list[dict]:
        q = (query or "").lower()
        rows = self._run(
            "MATCH (n:SFO) WHERE toLower(n.name) CONTAINS $q OR "
            "toLower(coalesce(n.family_name,'')) CONTAINS $q OR "
            "toLower(coalesce(n.client_ref,'')) CONTAINS $q "
            "RETURN n ORDER BY n.aum_usd DESC LIMIT $l", q=q, l=limit)
        out = []
        for r in rows:
            d = dict(r["n"])
            d["id"] = d.pop("uid")
            out.append(self._materialise_sfo(d))
        return out

    def count_sfos(self) -> int:
        return self._run("MATCH (n:SFO) RETURN count(n) AS c")[0]["c"]

    def delete_sfo(self, sfo_id: int) -> None:
        self._write("MATCH (n:SFO {uid:$i}) "
                    "OPTIONAL MATCH (n)-[:HAS_CONVERSATION]->(c:Conversation)"
                    "-[:HAS_MESSAGE]->(m:Message) "
                    "DETACH DELETE n, c, m", i=sfo_id)

    # ── Services ──────────────────────────────────────────────────────────────
    def upsert_service(self, service: dict) -> int:
        props = {
            "name": service.get("name"), "category": service.get("category"),
            "tier": service.get("tier") or "core",
            "description": service.get("description"), "url": service.get("url"),
            "keywords": service.get("keywords") or [],
        }
        rows = self._write(
            "MERGE (s:Service {key:$key}) ON CREATE SET s.uid=$uid, s.created_at=$t "
            "SET s += $props RETURN s.uid AS uid",
            key=service["key"], uid=self._next_id(), t=utcnow(), props=props)
        return rows[0]["uid"]

    @staticmethod
    def _svc(node) -> dict:
        d = dict(node)
        d["id"] = d.pop("uid", None)
        d["keywords"] = d.get("keywords") or []
        return d

    def get_service(self, service_id: int) -> dict | None:
        rows = self._run("MATCH (s:Service {uid:$i}) RETURN s", i=service_id)
        return self._svc(rows[0]["s"]) if rows else None

    def get_service_by_key(self, key: str) -> dict | None:
        rows = self._run("MATCH (s:Service {key:$k}) RETURN s", k=key)
        return self._svc(rows[0]["s"]) if rows else None

    def list_services(self, category: str | None = None, limit: int = 500) -> list[dict]:
        where = " WHERE s.category=$cat" if category else ""
        rows = self._run(f"MATCH (s:Service){where} RETURN s "
                         "ORDER BY s.category, s.name LIMIT $l", cat=category, l=limit)
        return [self._svc(r["s"]) for r in rows]

    def search_services(self, query: str, limit: int = 8) -> list[dict]:
        q = (query or "").lower()
        rows = self._run(
            "MATCH (s:Service) WHERE toLower(s.name) CONTAINS $q OR "
            "toLower(coalesce(s.category,'')) CONTAINS $q OR "
            "toLower(coalesce(s.description,'')) CONTAINS $q OR "
            "any(k IN s.keywords WHERE toLower(k) CONTAINS $q) "
            "RETURN s ORDER BY s.tier DESC, s.name LIMIT $l", q=q, l=limit)
        return [self._svc(r["s"]) for r in rows]

    def add_cross_sell_edge(self, from_key: str, to_key: str, weight: float = 1.0) -> None:
        self._write(
            "MATCH (a:Service {key:$f}), (b:Service {key:$t}) "
            "MERGE (a)-[r:CROSS_SELLS_TO]->(b) SET r.weight=$w",
            f=from_key, t=to_key, w=weight)

    def list_cross_sells(self, service_key: str) -> list[dict]:
        rows = self._run(
            "MATCH (a:Service {key:$k})-[r:CROSS_SELLS_TO]->(b:Service) "
            "RETURN b, r.weight AS weight ORDER BY r.weight DESC", k=service_key)
        out = []
        for r in rows:
            d = self._svc(r["b"])
            d["weight"] = r["weight"]
            out.append(d)
        return out

    # ── Recommendations ───────────────────────────────────────────────────────
    def upsert_recommendation(self, rec: dict) -> int:
        # Update only supplied fields (via coalesce against $set defaults of the
        # current value) so a partial upsert never clobbers score/value/rationale.
        sets = {"kind": rec.get("kind"), "score": rec.get("score"),
                "rationale": rec.get("rationale"), "est_value_usd": rec.get("est_value_usd"),
                "source": rec.get("source")}
        rows = self._write(
            "MATCH (o:SFO {uid:$sfo}), (s:Service {uid:$svc}) "
            "MERGE (o)-[r:RECOMMENDED]->(s) "
            "ON CREATE SET r.uid=$uid, r.status='suggested', r.created_at=$t, "
            "r.kind='cross_sell', r.score=0.0, r.est_value_usd=0.0, r.source='hybrid' "
            "SET r.kind=coalesce($set.kind, r.kind), r.score=coalesce($set.score, r.score), "
            "r.rationale=coalesce($set.rationale, r.rationale), "
            "r.est_value_usd=coalesce($set.est_value_usd, r.est_value_usd), "
            "r.source=coalesce($set.source, r.source) RETURN r.uid AS uid",
            sfo=rec["sfo_id"], svc=rec["service_id"], uid=self._next_id(), t=utcnow(),
            set=sets)
        return rows[0]["uid"]

    def list_recommendations(self, sfo_id: int | None = None, status: str | None = None,
                             limit: int = 500) -> list[dict]:
        clauses, params = [], {"l": limit}
        if sfo_id is not None:
            clauses.append("o.uid=$sfo")
            params["sfo"] = sfo_id
        if status:
            clauses.append("r.status=$st")
            params["st"] = status
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._run(
            f"MATCH (o:SFO)-[r:RECOMMENDED]->(s:Service){where} "
            "RETURN r.uid AS id, o.uid AS sfo_id, s.uid AS service_id, r.kind AS kind, "
            "r.score AS score, r.rationale AS rationale, r.est_value_usd AS est_value_usd, "
            "r.source AS source, r.status AS status, s.name AS service_name, "
            "s.category AS service_category, s.tier AS service_tier, o.name AS sfo_name "
            "ORDER BY r.score DESC LIMIT $l", **params)
        return [dict(r) for r in rows]

    def set_recommendation_status(self, recommendation_id: int, status: str) -> None:
        self._write("MATCH ()-[r:RECOMMENDED {uid:$i}]->() SET r.status=$s",
                    i=recommendation_id, s=status)

    # ── Conversations ─────────────────────────────────────────────────────────
    def create_conversation(self, user_email: str, sfo_id: int | None = None,
                            title: str = "") -> int:
        uid = self._next_id()
        self._write(
            "CREATE (c:Conversation {uid:$uid, user_email:$u, title:$t, "
            "created_at:$now, updated_at:$now}) "
            "WITH c MATCH (o:SFO {uid:$sfo}) MERGE (o)-[:HAS_CONVERSATION]->(c)",
            uid=uid, u=user_email, t=title or "New conversation", now=utcnow(),
            sfo=sfo_id if sfo_id is not None else -1)
        return uid

    def add_message(self, conversation_id: int, role: str, content: str) -> None:
        self._write(
            "MATCH (c:Conversation {uid:$c}) "
            "CREATE (m:Message {uid:$uid, role:$r, content:$m, created_at:$t}) "
            "MERGE (c)-[:HAS_MESSAGE]->(m) SET c.updated_at=$t",
            c=conversation_id, uid=self._next_id(), r=role, m=content, t=utcnow())

    def list_conversations(self, user_email: str | None = None, sfo_id: int | None = None,
                           limit: int = 30) -> list[dict]:
        clauses, params = [], {"l": limit}
        if user_email:
            clauses.append("c.user_email=$u")
            params["u"] = user_email
        match = "MATCH (c:Conversation)"
        if sfo_id is not None:
            match = "MATCH (o:SFO {uid:$sfo})-[:HAS_CONVERSATION]->(c:Conversation)"
            params["sfo"] = sfo_id
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._run(
            f"{match}{where} OPTIONAL MATCH (oo:SFO)-[:HAS_CONVERSATION]->(c) "
            "RETURN c.uid AS id, c.title AS title, oo.uid AS sfo_id, "
            "c.updated_at AS updated_at ORDER BY c.updated_at DESC LIMIT $l", **params)
        return [dict(r) for r in rows]

    def get_messages(self, conversation_id: int) -> list[dict]:
        rows = self._run(
            "MATCH (c:Conversation {uid:$c})-[:HAS_MESSAGE]->(m:Message) "
            "RETURN m.role AS role, m.content AS content, m.created_at AS created_at "
            "ORDER BY m.created_at, m.uid", c=conversation_id)
        return [dict(r) for r in rows]

    # ── Analytics ─────────────────────────────────────────────────────────────
    def stats(self) -> dict:
        def one(c):
            return self._run(c)[0]["c"]
        by_stage = {r["s"]: r["c"] for r in self._run(
            "MATCH (n:SFO) RETURN n.stage AS s, count(*) AS c")}
        return {
            "sfos": one("MATCH (n:SFO) RETURN count(n) AS c"),
            "services": one("MATCH (n:Service) RETURN count(n) AS c"),
            "conversations": one("MATCH (n:Conversation) RETURN count(n) AS c"),
            "recommendations": one("MATCH ()-[r:RECOMMENDED]->() RETURN count(r) AS c"),
            "by_stage": by_stage,
        }

    def service_interest_counts(self) -> list[dict]:
        rows = self._run(
            "MATCH (s:Service) OPTIONAL MATCH ()-[r:RECOMMENDED]->(s) "
            "RETURN s.key AS key, s.name AS name, s.category AS category, "
            "count(r) AS count ORDER BY count DESC, s.name")
        return [dict(r) for r in rows]

    def upsell_funnel(self) -> dict:
        by_status = {r["s"]: r["c"] for r in self._run(
            "MATCH ()-[r:RECOMMENDED]->() RETURN r.status AS s, count(*) AS c")}
        pipeline = self._run(
            "MATCH ()-[r:RECOMMENDED]->() WHERE r.status IN ['accepted','booked'] "
            "RETURN coalesce(sum(r.est_value_usd),0) AS p")[0]["p"]
        return {"by_status": by_status, "pipeline_usd": float(pipeline)}
