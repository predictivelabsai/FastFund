"""Relational storage backend for SFO Hub (SQLite, and Postgres via ``DB_URL``).

The zero-infra path: a single SQLAlchemy engine over ``DB_URL`` (default
``sqlite:///sfohub.db``). List/dict-valued fields (jurisdictions, asset_mix, …)
are JSON-encoded into TEXT columns so the schema is identical on SQLite and
Postgres. This backend has full parity with ``Neo4jStore`` — enforced by the
shared contract test in ``tests/test_storage.py``.
"""

from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, text

from .base import Storage, utcnow

DB_URL = os.environ.get("DB_URL", "sqlite:///sfohub.db")


def _j(v):
    """JSON-encode a list/dict for a TEXT column (None → None)."""
    return None if v is None else json.dumps(v)


def _u(v, default):
    """Decode a JSON TEXT column back to a Python value (tolerant)."""
    if v is None or v == "":
        return default
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


class SqliteStore(Storage):
    def __init__(self, db_url: str | None = None):
        url = db_url or DB_URL
        self.engine = create_engine(url, future=True)
        self._is_sqlite = url.startswith("sqlite")

    # ── helpers ────────────────────────────────────────────────────────────
    def _conn(self):
        return self.engine.begin()

    @staticmethod
    def _row(r):
        return dict(r._mapping) if r is not None else None

    # ── Lifecycle ──────────────────────────────────────────────────────────
    def init_db(self) -> None:
        ddl = [
            """CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE, password_hash TEXT, role TEXT DEFAULT 'admin',
                created_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS sfos(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_ref TEXT UNIQUE, name TEXT, family_name TEXT,
                aum_usd REAL, family_size INTEGER, generations INTEGER,
                domicile TEXT, jurisdictions TEXT, current_services TEXT,
                asset_mix TEXT, pain_points TEXT, stage TEXT DEFAULT 'lead',
                contact_name TEXT, contact_email TEXT, created_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS services(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE, name TEXT, category TEXT, tier TEXT,
                description TEXT, url TEXT, keywords TEXT, created_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS cross_sells(
                from_id INTEGER, to_id INTEGER, weight REAL DEFAULT 1.0,
                PRIMARY KEY(from_id, to_id))""",
            """CREATE TABLE IF NOT EXISTS recommendations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sfo_id INTEGER, service_id INTEGER, kind TEXT, score REAL,
                rationale TEXT, est_value_usd REAL, source TEXT,
                status TEXT DEFAULT 'suggested', created_at TEXT,
                UNIQUE(sfo_id, service_id))""",
            """CREATE TABLE IF NOT EXISTS conversations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email TEXT, sfo_id INTEGER, title TEXT,
                created_at TEXT, updated_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER, role TEXT, content TEXT, created_at TEXT)""",
        ]
        # AUTOINCREMENT is SQLite-only; Postgres uses SERIAL-like via SQLAlchemy.
        if not self._is_sqlite:
            ddl = [d.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                             "SERIAL PRIMARY KEY") for d in ddl]
        with self._conn() as c:
            for d in ddl:
                c.execute(text(d))
        self._seed_admin()

    def _seed_admin(self) -> None:
        email = os.environ.get("ADMIN_EMAIL", "admin@jtcgroup.com")
        pw = os.environ.get("ADMIN_PASSWORD", "change-me")
        if self.get_user_by_email(email):
            return
        import bcrypt
        h = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        with self._conn() as c:
            c.execute(text("INSERT INTO users(email,password_hash,role,created_at)"
                           " VALUES(:e,:h,'admin',:t)"),
                      {"e": email, "h": h, "t": utcnow()})

    # ── Users ──────────────────────────────────────────────────────────────
    def get_user_by_email(self, email: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(text("SELECT id,email,password_hash,role FROM users"
                               " WHERE email=:e"), {"e": email}).first()
        return self._row(r)

    # ── SFOs ───────────────────────────────────────────────────────────────
    def upsert_sfo(self, sfo: dict) -> int:
        ref = sfo.get("client_ref") or sfo.get("name")
        params = {
            "client_ref": ref, "name": sfo.get("name"),
            "family_name": sfo.get("family_name"),
            "aum_usd": sfo.get("aum_usd"), "family_size": sfo.get("family_size"),
            "generations": sfo.get("generations"), "domicile": sfo.get("domicile"),
            "jurisdictions": _j(sfo.get("jurisdictions") or []),
            "current_services": _j(sfo.get("current_services") or []),
            "asset_mix": _j(sfo.get("asset_mix") or {}),
            "pain_points": _j(sfo.get("pain_points") or []),
            "stage": sfo.get("stage") or "lead",
            "contact_name": sfo.get("contact_name"),
            "contact_email": sfo.get("contact_email"), "created_at": utcnow(),
        }
        with self._conn() as c:
            existing = c.execute(text("SELECT id FROM sfos WHERE client_ref=:r"),
                                 {"r": ref}).first()
            if existing:
                sfo_id = existing[0]
                cols = ",".join(f"{k}=:{k}" for k in params if k != "created_at")
                c.execute(text(f"UPDATE sfos SET {cols} WHERE id=:id"),
                          {**params, "id": sfo_id})
                return sfo_id
            keys = ",".join(params)
            vals = ",".join(f":{k}" for k in params)
            c.execute(text(f"INSERT INTO sfos({keys}) VALUES({vals})"), params)
            return c.execute(text("SELECT id FROM sfos WHERE client_ref=:r"),
                             {"r": ref}).first()[0]

    @staticmethod
    def _sfo(r):
        d = SqliteStore._row(r)
        if d is None:
            return None
        d["jurisdictions"] = _u(d.get("jurisdictions"), [])
        d["current_services"] = _u(d.get("current_services"), [])
        d["asset_mix"] = _u(d.get("asset_mix"), {})
        d["pain_points"] = _u(d.get("pain_points"), [])
        return d

    def get_sfo(self, sfo_id: int) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(text("SELECT * FROM sfos WHERE id=:i"), {"i": sfo_id}).first()
        return self._sfo(r)

    def list_sfos(self, stage: str | None = None, limit: int = 500) -> list[dict]:
        q = "SELECT * FROM sfos"
        p = {"l": limit}
        if stage:
            q += " WHERE stage=:s"
            p["s"] = stage
        q += " ORDER BY aum_usd DESC LIMIT :l"
        with self.engine.connect() as c:
            return [self._sfo(r) for r in c.execute(text(q), p)]

    def search_sfos(self, query: str, limit: int = 10) -> list[dict]:
        like = f"%{(query or '').lower()}%"
        with self.engine.connect() as c:
            rows = c.execute(text(
                "SELECT * FROM sfos WHERE lower(name) LIKE :q OR lower(family_name)"
                " LIKE :q OR lower(client_ref) LIKE :q ORDER BY aum_usd DESC LIMIT :l"),
                {"q": like, "l": limit})
            return [self._sfo(r) for r in rows]

    def count_sfos(self) -> int:
        with self.engine.connect() as c:
            return c.execute(text("SELECT COUNT(*) FROM sfos")).scalar() or 0

    def delete_sfo(self, sfo_id: int) -> None:
        with self._conn() as c:
            c.execute(text("DELETE FROM recommendations WHERE sfo_id=:i"), {"i": sfo_id})
            c.execute(text("DELETE FROM sfos WHERE id=:i"), {"i": sfo_id})

    # ── Services ───────────────────────────────────────────────────────────
    def upsert_service(self, service: dict) -> int:
        params = {
            "key": service["key"], "name": service.get("name"),
            "category": service.get("category"), "tier": service.get("tier") or "core",
            "description": service.get("description"), "url": service.get("url"),
            "keywords": _j(service.get("keywords") or []), "created_at": utcnow(),
        }
        with self._conn() as c:
            existing = c.execute(text("SELECT id FROM services WHERE key=:k"),
                                 {"k": params["key"]}).first()
            if existing:
                sid = existing[0]
                cols = ",".join(f"{k}=:{k}" for k in params if k != "created_at")
                c.execute(text(f"UPDATE services SET {cols} WHERE id=:id"),
                          {**params, "id": sid})
                return sid
            keys = ",".join(params)
            vals = ",".join(f":{k}" for k in params)
            c.execute(text(f"INSERT INTO services({keys}) VALUES({vals})"), params)
            return c.execute(text("SELECT id FROM services WHERE key=:k"),
                             {"k": params["key"]}).first()[0]

    @staticmethod
    def _svc(r):
        d = SqliteStore._row(r)
        if d is None:
            return None
        d["keywords"] = _u(d.get("keywords"), [])
        return d

    def get_service(self, service_id: int) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(text("SELECT * FROM services WHERE id=:i"),
                          {"i": service_id}).first()
        return self._svc(r)

    def get_service_by_key(self, key: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(text("SELECT * FROM services WHERE key=:k"), {"k": key}).first()
        return self._svc(r)

    def list_services(self, category: str | None = None, limit: int = 500) -> list[dict]:
        q = "SELECT * FROM services"
        p = {"l": limit}
        if category:
            q += " WHERE category=:c"
            p["c"] = category
        q += " ORDER BY category, name LIMIT :l"
        with self.engine.connect() as c:
            return [self._svc(r) for r in c.execute(text(q), p)]

    def search_services(self, query: str, limit: int = 8) -> list[dict]:
        like = f"%{(query or '').lower()}%"
        with self.engine.connect() as c:
            rows = c.execute(text(
                "SELECT * FROM services WHERE lower(name) LIKE :q OR lower(category)"
                " LIKE :q OR lower(description) LIKE :q OR lower(keywords) LIKE :q"
                " ORDER BY tier DESC, name LIMIT :l"), {"q": like, "l": limit})
            return [self._svc(r) for r in rows]

    def add_cross_sell_edge(self, from_key: str, to_key: str, weight: float = 1.0) -> None:
        with self._conn() as c:
            fi = c.execute(text("SELECT id FROM services WHERE key=:k"),
                           {"k": from_key}).first()
            ti = c.execute(text("SELECT id FROM services WHERE key=:k"),
                           {"k": to_key}).first()
            if not fi or not ti:
                return
            c.execute(text("DELETE FROM cross_sells WHERE from_id=:f AND to_id=:t"),
                      {"f": fi[0], "t": ti[0]})
            c.execute(text("INSERT INTO cross_sells(from_id,to_id,weight)"
                           " VALUES(:f,:t,:w)"), {"f": fi[0], "t": ti[0], "w": weight})

    def list_cross_sells(self, service_key: str) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(text(
                "SELECT s.*, x.weight FROM cross_sells x"
                " JOIN services f ON f.id=x.from_id"
                " JOIN services s ON s.id=x.to_id"
                " WHERE f.key=:k ORDER BY x.weight DESC"), {"k": service_key})
            out = []
            for r in rows:
                d = self._svc(r)
                out.append(d)
            return out

    # ── Recommendations ────────────────────────────────────────────────────
    def upsert_recommendation(self, rec: dict) -> int:
        params = {
            "sfo_id": rec["sfo_id"], "service_id": rec["service_id"],
            "kind": rec.get("kind") or "cross_sell", "score": rec.get("score") or 0.0,
            "rationale": rec.get("rationale"),
            "est_value_usd": rec.get("est_value_usd") or 0.0,
            "source": rec.get("source") or "hybrid", "created_at": utcnow(),
        }
        with self._conn() as c:
            existing = c.execute(text("SELECT id FROM recommendations"
                                      " WHERE sfo_id=:s AND service_id=:v"),
                                 {"s": params["sfo_id"], "v": params["service_id"]}).first()
            if existing:
                rid = existing[0]
                # Update only fields actually supplied — never clobber an existing
                # score/value/rationale with a default on a partial upsert.
                upd = [k for k in ("kind", "score", "rationale", "est_value_usd", "source")
                       if k in rec]
                if upd:
                    sets = ",".join(f"{k}=:{k}" for k in upd)
                    c.execute(text(f"UPDATE recommendations SET {sets} WHERE id=:id"),
                              {**{k: params[k] for k in upd}, "id": rid})
                return rid
            params["status"] = "suggested"
            keys = ",".join(params)
            vals = ",".join(f":{k}" for k in params)
            c.execute(text(f"INSERT INTO recommendations({keys}) VALUES({vals})"), params)
            return c.execute(text("SELECT id FROM recommendations WHERE sfo_id=:s"
                                  " AND service_id=:v"),
                             {"s": params["sfo_id"], "v": params["service_id"]}).first()[0]

    def list_recommendations(self, sfo_id: int | None = None, status: str | None = None,
                             limit: int = 500) -> list[dict]:
        q = ("SELECT r.*, s.name AS service_name, s.category AS service_category,"
             " s.tier AS service_tier, o.name AS sfo_name"
             " FROM recommendations r"
             " JOIN services s ON s.id=r.service_id"
             " JOIN sfos o ON o.id=r.sfo_id WHERE 1=1")
        p = {"l": limit}
        if sfo_id is not None:
            q += " AND r.sfo_id=:s"
            p["s"] = sfo_id
        if status:
            q += " AND r.status=:st"
            p["st"] = status
        q += " ORDER BY r.score DESC LIMIT :l"
        with self.engine.connect() as c:
            return [self._row(r) for r in c.execute(text(q), p)]

    def set_recommendation_status(self, recommendation_id: int, status: str) -> None:
        with self._conn() as c:
            c.execute(text("UPDATE recommendations SET status=:s WHERE id=:i"),
                      {"s": status, "i": recommendation_id})

    # ── Conversations ──────────────────────────────────────────────────────
    def create_conversation(self, user_email: str, sfo_id: int | None = None,
                            title: str = "") -> int:
        now = utcnow()
        with self._conn() as c:
            c.execute(text("INSERT INTO conversations(user_email,sfo_id,title,"
                           "created_at,updated_at) VALUES(:u,:s,:t,:c,:c)"),
                      {"u": user_email, "s": sfo_id, "t": title or "New conversation",
                       "c": now})
            return c.execute(text("SELECT id FROM conversations WHERE user_email=:u"
                                  " ORDER BY id DESC LIMIT 1"), {"u": user_email}).first()[0]

    def add_message(self, conversation_id: int, role: str, content: str) -> None:
        with self._conn() as c:
            c.execute(text("INSERT INTO messages(conversation_id,role,content,created_at)"
                           " VALUES(:c,:r,:m,:t)"),
                      {"c": conversation_id, "r": role, "m": content, "t": utcnow()})
            c.execute(text("UPDATE conversations SET updated_at=:t WHERE id=:i"),
                      {"t": utcnow(), "i": conversation_id})

    def list_conversations(self, user_email: str | None = None, sfo_id: int | None = None,
                           limit: int = 30) -> list[dict]:
        q = "SELECT id,title,sfo_id,updated_at FROM conversations WHERE 1=1"
        p = {"l": limit}
        if user_email:
            q += " AND user_email=:u"
            p["u"] = user_email
        if sfo_id is not None:
            q += " AND sfo_id=:s"
            p["s"] = sfo_id
        q += " ORDER BY updated_at DESC LIMIT :l"
        with self.engine.connect() as c:
            return [self._row(r) for r in c.execute(text(q), p)]

    def get_messages(self, conversation_id: int) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(text("SELECT role,content,created_at FROM messages"
                                  " WHERE conversation_id=:c ORDER BY id"),
                             {"c": conversation_id})
            return [self._row(r) for r in rows]

    # ── Analytics ──────────────────────────────────────────────────────────
    def stats(self) -> dict:
        with self.engine.connect() as c:
            n = lambda q: c.execute(text(q)).scalar() or 0  # noqa: E731
            by_stage = {r[0]: r[1] for r in c.execute(text(
                "SELECT stage, COUNT(*) FROM sfos GROUP BY stage"))}
            return {
                "sfos": n("SELECT COUNT(*) FROM sfos"),
                "services": n("SELECT COUNT(*) FROM services"),
                "conversations": n("SELECT COUNT(*) FROM conversations"),
                "recommendations": n("SELECT COUNT(*) FROM recommendations"),
                "by_stage": by_stage,
            }

    def service_interest_counts(self) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(text(
                "SELECT s.key,s.name,s.category,COUNT(r.id) AS count"
                " FROM services s LEFT JOIN recommendations r ON r.service_id=s.id"
                " GROUP BY s.id ORDER BY count DESC, s.name"))
            return [self._row(r) for r in rows]

    def upsell_funnel(self) -> dict:
        with self.engine.connect() as c:
            by_status = {r[0]: r[1] for r in c.execute(text(
                "SELECT status, COUNT(*) FROM recommendations GROUP BY status"))}
            pipeline = c.execute(text(
                "SELECT COALESCE(SUM(est_value_usd),0) FROM recommendations"
                " WHERE status IN ('accepted','booked')")).scalar() or 0.0
            return {"by_status": by_status, "pipeline_usd": float(pipeline)}
