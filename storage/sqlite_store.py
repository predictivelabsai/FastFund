"""SQLite (SQLAlchemy) storage backend.

The original, zero-infra backend: local dev and the first production deploy
need no external service. The schema mirrors the scaffold's price-history
pattern — a document has many immutable versions; an amendment appends a new
version and records a change. Plain enough to point ``DB_URL`` at Postgres if
volume ever demands it.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager

from sqlalchemy import create_engine, text

from .base import Storage, utcnow


class SqliteStore(Storage):
    def __init__(self, db_url: str | None = None):
        self.db_url = db_url or os.environ.get("DB_URL", "sqlite:///taxhub.db")
        # check_same_thread=False so the FastHTML app and CLI scrapers can share it.
        connect_args = {"check_same_thread": False} if self.db_url.startswith("sqlite") else {}
        self.engine = create_engine(self.db_url, future=True, connect_args=connect_args)

    @contextmanager
    def conn(self):
        with self.engine.begin() as c:
            if self.db_url.startswith("sqlite"):
                c.execute(text("PRAGMA foreign_keys=ON"))
                c.execute(text("PRAGMA journal_mode=WAL"))
            yield c

    # ── Schema ─────────────────────────────────────────────────────────────

    DDL = [
        """CREATE TABLE IF NOT EXISTS jurisdictions (
            code        TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            region      TEXT,
            authority   TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS tax_documents (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            jurisdiction_code  TEXT NOT NULL REFERENCES jurisdictions(code),
            source             TEXT,
            doc_type           TEXT NOT NULL,
            doc_key            TEXT NOT NULL,
            title              TEXT NOT NULL,
            reference          TEXT,
            url                TEXT NOT NULL UNIQUE,
            format             TEXT DEFAULT 'html',
            tags               TEXT,
            current_version_id INTEGER,
            current_hash       TEXT,
            first_seen         TEXT,
            last_checked       TEXT,
            last_changed       TEXT,
            check_count        INTEGER DEFAULT 0,
            status             TEXT DEFAULT 'active',
            last_error         TEXT,
            UNIQUE(jurisdiction_code, doc_key)
        )""",
        """CREATE TABLE IF NOT EXISTS document_versions (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id        INTEGER NOT NULL REFERENCES tax_documents(id),
            version_no         INTEGER NOT NULL,
            content_hash       TEXT NOT NULL,
            text_content       TEXT,
            raw_path           TEXT,
            byte_size          INTEGER,
            char_count         INTEGER,
            http_last_modified TEXT,
            etag               TEXT,
            fetched_at         TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS document_changes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id     INTEGER NOT NULL REFERENCES tax_documents(id),
            from_version_id INTEGER REFERENCES document_versions(id),
            to_version_id   INTEGER NOT NULL REFERENCES document_versions(id),
            change_type     TEXT NOT NULL,
            added_chars     INTEGER DEFAULT 0,
            removed_chars   INTEGER DEFAULT 0,
            diff_text       TEXT,
            ai_summary      TEXT,
            ai_impact       TEXT,
            ai_model        TEXT,
            detected_at     TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS citations (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id        INTEGER NOT NULL REFERENCES tax_documents(id),
            version_id         INTEGER REFERENCES document_versions(id),
            cited_jurisdiction TEXT,
            cited_instrument   TEXT,
            cited_locator      TEXT,
            snippet            TEXT,
            created_at         TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS scrape_runs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            jurisdiction_code TEXT,
            started_at        TEXT,
            finished_at       TEXT,
            docs_checked      INTEGER DEFAULT 0,
            docs_new          INTEGER DEFAULT 0,
            docs_changed      INTEGER DEFAULT 0,
            docs_error        INTEGER DEFAULT 0,
            status            TEXT DEFAULT 'running',
            notes             TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            name          TEXT,
            role          TEXT DEFAULT 'user',
            created_at    TEXT DEFAULT (datetime('now'))
        )""",
        # Chunk-level embeddings for vector retrieval (embedding = JSON float list).
        """CREATE TABLE IF NOT EXISTS version_chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id  INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            ord         INTEGER NOT NULL,
            text        TEXT,
            embedding   TEXT
        )""",
        # Tax forms (the form-finder corpus).
        """CREATE TABLE IF NOT EXISTS tax_forms (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            jurisdiction_code TEXT NOT NULL,
            category          TEXT,
            form_type         TEXT,
            form_key          TEXT NOT NULL,
            title             TEXT,
            authority         TEXT,
            url               TEXT,
            file_path         TEXT,
            year              TEXT,
            who_files         TEXT,
            deadline          TEXT,
            frequency         TEXT,
            summary           TEXT,
            legislation_ref   TEXT,
            filing_type       TEXT,
            first_seen        TEXT,
            last_seen         TEXT,
            UNIQUE(jurisdiction_code, form_key)
        )""",
        # Entities (the funds/SPVs JTC administers). jurisdictions/activities
        # are stored as comma-joined text and returned as lists.
        """CREATE TABLE IF NOT EXISTS entities (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            type          TEXT,
            domicile      TEXT,
            jurisdictions TEXT,
            fy_end        TEXT,
            activities    TEXT,
            client_ref    TEXT,
            status        TEXT,
            team_id       INTEGER,
            created_at    TEXT,
            updated_at    TEXT,
            UNIQUE(client_ref)
        )""",
        # Obligations (Determine engine output): one per (entity, form).
        """CREATE TABLE IF NOT EXISTS obligations (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id         INTEGER NOT NULL,
            form_id           INTEGER NOT NULL,
            title             TEXT,
            jurisdiction_code TEXT,
            category          TEXT,
            deadline          TEXT,
            period            TEXT,
            status            TEXT,
            verified          INTEGER,
            created_at        TEXT,
            updated_at        TEXT,
            UNIQUE(entity_id, form_id)
        )""",
        """CREATE TABLE IF NOT EXISTS chat_sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            team_id    INTEGER,
            title      TEXT,
            created_at TEXT,
            updated_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS chat_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role       TEXT,
            content    TEXT,
            created_at TEXT
        )""",
        # ── Teams / RBAC ────────────────────────────────────────────────────
        # A Team is a workspace; client data (entities/obligations/chats) is
        # scoped to it. Users join a team via team_members with a per-team role.
        """CREATE TABLE IF NOT EXISTS teams (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            created_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS team_members (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            role       TEXT DEFAULT 'member',
            created_at TEXT,
            UNIQUE(team_id, user_id)
        )""",
        # Invites: an admin invites an email to a team with a role; the token
        # link lets the invitee set a password (Google sign-in also works).
        """CREATE TABLE IF NOT EXISTS invites (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            email      TEXT NOT NULL,
            team_id    INTEGER,
            role       TEXT DEFAULT 'member',
            token      TEXT UNIQUE NOT NULL,
            inviter_id INTEGER,
            created_at TEXT,
            expires_at TEXT,
            used_at    TEXT
        )""",
    ]

    INDEXES = [
        "CREATE INDEX IF NOT EXISTS idx_docs_jur ON tax_documents(jurisdiction_code)",
        "CREATE INDEX IF NOT EXISTS idx_docs_type ON tax_documents(doc_type)",
        "CREATE INDEX IF NOT EXISTS idx_docs_status ON tax_documents(status)",
        "CREATE INDEX IF NOT EXISTS idx_versions_doc ON document_versions(document_id)",
        "CREATE INDEX IF NOT EXISTS idx_versions_hash ON document_versions(content_hash)",
        "CREATE INDEX IF NOT EXISTS idx_changes_doc ON document_changes(document_id)",
        "CREATE INDEX IF NOT EXISTS idx_changes_detected ON document_changes(detected_at)",
        "CREATE INDEX IF NOT EXISTS idx_citations_doc ON citations(document_id)",
        "CREATE INDEX IF NOT EXISTS idx_chunks_version ON version_chunks(version_id)",
        "CREATE INDEX IF NOT EXISTS idx_forms_jur ON tax_forms(jurisdiction_code)",
        "CREATE INDEX IF NOT EXISTS idx_forms_cat ON tax_forms(category)",
    ]

    def init_db(self) -> None:
        with self.conn() as c:
            for stmt in self.DDL:
                c.execute(text(stmt))
            for stmt in self.INDEXES:
                c.execute(text(stmt))
            # Idempotent column migrations for pre-existing DBs.
            for tbl, col, decl in (("entities", "team_id", "INTEGER"),
                                   ("chat_sessions", "team_id", "INTEGER")):
                try:
                    c.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}"))
                except Exception:  # noqa: BLE001 — column already exists
                    pass
        self._seed_admin()
        self._seed_platform()

    def _seed_admin(self) -> None:
        import bcrypt

        email = os.environ.get("ADMIN_EMAIL", "admin@jtcgroup.com")
        pw = os.environ.get("ADMIN_PASSWORD", "Funds2$2")
        with self.conn() as c:
            exists = c.execute(
                text("SELECT 1 FROM users WHERE email = :e"), {"e": email}
            ).fetchone()
            if not exists:
                pw_hash = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
                c.execute(
                    text(
                        "INSERT INTO users (email, password_hash, name, role) "
                        "VALUES (:e, :p, :n, 'admin')"
                    ),
                    {"e": email, "p": pw_hash, "n": "JTC Admin"},
                )

    # Platform admins seeded as global admins (comma-separated env override).
    PLATFORM_ADMINS = ("julian@predictivelabs.co.uk", "kaljuvee@gmail.com")
    DEFAULT_TEAM = "JTC Group"

    def _seed_platform(self) -> None:
        """Ensure a default team exists, the platform admins are global admins +
        members, and any team-less entities/users are attached. Idempotent."""
        import bcrypt
        extra = [e.strip().lower() for e in
                 os.environ.get("PLATFORM_ADMINS", "").split(",") if e.strip()]
        admins = list(dict.fromkeys(list(self.PLATFORM_ADMINS) + extra))
        now = utcnow()
        with self.conn() as c:
            row = c.execute(text("SELECT id FROM teams WHERE name=:n"),
                            {"n": self.DEFAULT_TEAM}).first()
            team_id = row[0] if row else int(c.execute(
                text("INSERT INTO teams (name, created_at) VALUES (:n,:t)"),
                {"n": self.DEFAULT_TEAM, "t": now}).lastrowid)
            # Promote/seed platform admins.
            for email in admins:
                u = c.execute(text("SELECT id, role FROM users WHERE email=:e"),
                              {"e": email}).mappings().first()
                if not u:
                    pw = bcrypt.hashpw(os.environ.get("ADMIN_PASSWORD", "Funds2$2").encode(),
                                       bcrypt.gensalt()).decode()
                    uid = int(c.execute(text(
                        "INSERT INTO users (email, password_hash, name, role) "
                        "VALUES (:e,:p,:n,'admin')"),
                        {"e": email, "p": pw, "n": email.split("@")[0]}).lastrowid)
                else:
                    uid = u["id"]
                    if u["role"] != "admin":
                        c.execute(text("UPDATE users SET role='admin' WHERE id=:i"), {"i": uid})
                c.execute(text("INSERT OR IGNORE INTO team_members "
                               "(team_id,user_id,role,created_at) VALUES (:t,:u,'admin',:n)"),
                          {"t": team_id, "u": uid, "n": now})
            # Backfill: every existing user belongs to the default team.
            for u in c.execute(text("SELECT id FROM users")).all():
                c.execute(text("INSERT OR IGNORE INTO team_members "
                               "(team_id,user_id,role,created_at) VALUES (:t,:u,'member',:n)"),
                          {"t": team_id, "u": u[0], "n": now})
            # Attach team-less entities to the default team.
            c.execute(text("UPDATE entities SET team_id=:t WHERE team_id IS NULL"),
                      {"t": team_id})

    # ── Users (profile + RBAC) ─────────────────────────────────────────────
    def get_user(self, user_id: int) -> dict | None:
        with self.conn() as c:
            r = c.execute(text("SELECT id, email, name, role, created_at FROM users "
                               "WHERE id=:i"), {"i": user_id}).mappings().first()
            return dict(r) if r else None

    def list_users(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(text(
                "SELECT id, email, name, role, created_at FROM users ORDER BY email"
            )).mappings().all()]

    def set_user_role(self, user_id: int, role: str) -> None:
        with self.conn() as c:
            c.execute(text("UPDATE users SET role=:r WHERE id=:i"),
                      {"r": role, "i": user_id})

    def set_user_password(self, user_id: int, password_hash: str) -> None:
        with self.conn() as c:
            c.execute(text("UPDATE users SET password_hash=:p WHERE id=:i"),
                      {"p": password_hash, "i": user_id})

    def update_user_name(self, user_id: int, name: str) -> None:
        with self.conn() as c:
            c.execute(text("UPDATE users SET name=:n WHERE id=:i"),
                      {"n": name, "i": user_id})

    # ── Teams / membership ─────────────────────────────────────────────────
    def create_team(self, name: str) -> int:
        with self.conn() as c:
            return int(c.execute(text("INSERT INTO teams (name, created_at) "
                                      "VALUES (:n,:t)"),
                                 {"n": name, "t": utcnow()}).lastrowid)

    def list_teams(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(text(
                "SELECT t.id, t.name, t.created_at, "
                "(SELECT COUNT(*) FROM team_members m WHERE m.team_id=t.id) AS members, "
                "(SELECT COUNT(*) FROM entities e WHERE e.team_id=t.id) AS entities "
                "FROM teams t ORDER BY t.name")).mappings().all()]

    def get_team(self, team_id: int) -> dict | None:
        with self.conn() as c:
            r = c.execute(text("SELECT id, name, created_at FROM teams WHERE id=:i"),
                          {"i": team_id}).mappings().first()
            return dict(r) if r else None

    def add_team_member(self, team_id: int, user_id: int, role: str = "member") -> None:
        with self.conn() as c:
            c.execute(text("INSERT INTO team_members (team_id,user_id,role,created_at) "
                           "VALUES (:t,:u,:r,:n) "
                           "ON CONFLICT(team_id,user_id) DO UPDATE SET role=:r"),
                      {"t": team_id, "u": user_id, "r": role, "n": utcnow()})

    def remove_team_member(self, team_id: int, user_id: int) -> None:
        with self.conn() as c:
            c.execute(text("DELETE FROM team_members WHERE team_id=:t AND user_id=:u"),
                      {"t": team_id, "u": user_id})

    def list_team_members(self, team_id: int) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(text(
                "SELECT u.id AS user_id, u.email, u.name, m.role "
                "FROM team_members m JOIN users u ON u.id=m.user_id "
                "WHERE m.team_id=:t ORDER BY u.email"), {"t": team_id}).mappings().all()]

    def list_teams_for_user(self, user_id: int) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(text(
                "SELECT t.id, t.name, m.role FROM team_members m "
                "JOIN teams t ON t.id=m.team_id WHERE m.user_id=:u ORDER BY t.name"),
                {"u": user_id}).mappings().all()]

    def team_role(self, team_id: int, user_id: int) -> str | None:
        with self.conn() as c:
            r = c.execute(text("SELECT role FROM team_members WHERE team_id=:t AND user_id=:u"),
                          {"t": team_id, "u": user_id}).first()
            return r[0] if r else None

    # ── Invites ────────────────────────────────────────────────────────────
    def create_invite(self, email: str, team_id: int | None, role: str,
                      token: str, inviter_id: int | None, expires_at: str) -> int:
        with self.conn() as c:
            return int(c.execute(text(
                "INSERT INTO invites (email,team_id,role,token,inviter_id,created_at,expires_at) "
                "VALUES (:e,:t,:r,:k,:i,:n,:x)"),
                {"e": email.strip().lower(), "t": team_id, "r": role, "k": token,
                 "i": inviter_id, "n": utcnow(), "x": expires_at}).lastrowid)

    def get_invite_by_token(self, token: str) -> dict | None:
        with self.conn() as c:
            r = c.execute(text("SELECT * FROM invites WHERE token=:k"),
                          {"k": token}).mappings().first()
            return dict(r) if r else None

    def mark_invite_used(self, token: str) -> None:
        with self.conn() as c:
            c.execute(text("UPDATE invites SET used_at=:n WHERE token=:k"),
                      {"n": utcnow(), "k": token})

    def list_invites(self, team_id: int | None = None) -> list[dict]:
        clause = "WHERE team_id=:t" if team_id is not None else ""
        with self.conn() as c:
            return [dict(r) for r in c.execute(text(
                f"SELECT id, email, team_id, role, created_at, expires_at, used_at "
                f"FROM invites {clause} ORDER BY created_at DESC"),
                ({"t": team_id} if team_id is not None else {})).mappings().all()]

    # ── Jurisdictions / documents (write) ──────────────────────────────────

    def upsert_jurisdiction(self, code, name, region=None, authority=None) -> None:
        with self.conn() as c:
            c.execute(
                text(
                    "INSERT INTO jurisdictions (code, name, region, authority) "
                    "VALUES (:code, :name, :region, :authority) "
                    "ON CONFLICT(code) DO UPDATE SET name=:name, region=:region, authority=:authority"
                ),
                {"code": code, "name": name, "region": region, "authority": authority},
            )

    def get_document(self, jurisdiction_code, doc_key) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                text("SELECT * FROM tax_documents WHERE jurisdiction_code = :j AND doc_key = :k"),
                {"j": jurisdiction_code, "k": doc_key},
            ).mappings().fetchone()
            return dict(row) if row else None

    def upsert_document(self, doc: dict) -> int:
        tags = json.dumps(doc.get("tags") or [])
        existing = self.get_document(doc["jurisdiction_code"], doc["doc_key"])
        with self.conn() as c:
            if existing:
                c.execute(
                    text(
                        "UPDATE tax_documents SET source=:source, doc_type=:doc_type, "
                        "title=:title, reference=:reference, url=:url, format=:format, "
                        "tags=:tags WHERE id=:id"
                    ),
                    {**doc, "tags": tags, "id": existing["id"]},
                )
                return existing["id"]
            res = c.execute(
                text(
                    "INSERT INTO tax_documents "
                    "(jurisdiction_code, source, doc_type, doc_key, title, reference, "
                    " url, format, tags, first_seen, status) "
                    "VALUES (:jurisdiction_code, :source, :doc_type, :doc_key, :title, "
                    " :reference, :url, :format, :tags, :now, 'active')"
                ),
                {
                    "jurisdiction_code": doc["jurisdiction_code"],
                    "source": doc.get("source"),
                    "doc_type": doc["doc_type"],
                    "doc_key": doc["doc_key"],
                    "title": doc["title"],
                    "reference": doc.get("reference"),
                    "url": doc["url"],
                    "format": doc.get("format", "html"),
                    "tags": tags,
                    "now": utcnow(),
                },
            )
            return res.lastrowid

    def latest_version(self, document_id: int) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                text(
                    "SELECT * FROM document_versions WHERE document_id = :d "
                    "ORDER BY version_no DESC LIMIT 1"
                ),
                {"d": document_id},
            ).mappings().fetchone()
            return dict(row) if row else None

    def add_version(self, document_id, content_hash, text_content, raw_path=None,
                    byte_size=None, http_last_modified=None, etag=None) -> dict:
        prev = self.latest_version(document_id)
        version_no = (prev["version_no"] + 1) if prev else 1
        with self.conn() as c:
            res = c.execute(
                text(
                    "INSERT INTO document_versions "
                    "(document_id, version_no, content_hash, text_content, raw_path, "
                    " byte_size, char_count, http_last_modified, etag, fetched_at) "
                    "VALUES (:d, :v, :h, :t, :rp, :bs, :cc, :lm, :etag, :now)"
                ),
                {
                    "d": document_id, "v": version_no, "h": content_hash,
                    "t": text_content, "rp": raw_path, "bs": byte_size,
                    "cc": len(text_content or ""), "lm": http_last_modified,
                    "etag": etag, "now": utcnow(),
                },
            )
            vid = res.lastrowid
            c.execute(
                text(
                    "UPDATE tax_documents SET current_version_id=:vid, current_hash=:h, "
                    "last_changed=:now WHERE id=:d"
                ),
                {"vid": vid, "h": content_hash, "now": utcnow(), "d": document_id},
            )
        return {"id": vid, "version_no": version_no}

    def mark_checked(self, document_id: int, error: str | None = None) -> None:
        with self.conn() as c:
            c.execute(
                text(
                    "UPDATE tax_documents SET last_checked=:now, "
                    "check_count=check_count+1, status=:st, last_error=:err WHERE id=:d"
                ),
                {
                    "now": utcnow(), "d": document_id,
                    "st": "error" if error else "active", "err": error,
                },
            )

    def record_change(self, document_id, from_version_id, to_version_id, change_type,
                      added_chars=0, removed_chars=0, diff_text=None, ai_summary=None,
                      ai_impact=None, ai_model=None) -> int:
        with self.conn() as c:
            res = c.execute(
                text(
                    "INSERT INTO document_changes "
                    "(document_id, from_version_id, to_version_id, change_type, "
                    " added_chars, removed_chars, diff_text, ai_summary, ai_impact, "
                    " ai_model, detected_at) "
                    "VALUES (:d, :fv, :tv, :ct, :ac, :rc, :dt, :sum, :imp, :m, :now)"
                ),
                {
                    "d": document_id, "fv": from_version_id, "tv": to_version_id,
                    "ct": change_type, "ac": added_chars, "rc": removed_chars,
                    "dt": diff_text, "sum": ai_summary, "imp": ai_impact,
                    "m": ai_model, "now": utcnow(),
                },
            )
            return res.lastrowid

    def add_citations(self, document_id, version_id, cites: list[dict]) -> int:
        if not cites:
            return 0
        with self.conn() as c:
            for ct in cites:
                c.execute(
                    text(
                        "INSERT INTO citations (document_id, version_id, "
                        "cited_jurisdiction, cited_instrument, cited_locator, snippet, "
                        "created_at) VALUES (:d, :v, :cj, :ci, :cl, :sn, :now)"
                    ),
                    {
                        "d": document_id, "v": version_id,
                        "cj": ct.get("jurisdiction"), "ci": ct.get("instrument"),
                        "cl": ct.get("locator"), "sn": ct.get("snippet"),
                        "now": utcnow(),
                    },
                )
        return len(cites)

    def overwrite_version_content(self, version_id, document_id, text_content,
                                  content_hash) -> None:
        with self.conn() as c:
            c.execute(
                text("UPDATE document_versions SET text_content=:t, content_hash=:h "
                     "WHERE id=:i"),
                {"t": text_content, "h": content_hash, "i": version_id},
            )
            c.execute(
                text("UPDATE tax_documents SET current_hash=:h WHERE id=:i"),
                {"h": content_hash, "i": document_id},
            )

    # ── Scrape runs ────────────────────────────────────────────────────────

    def start_run(self, jurisdiction_code: str) -> int:
        with self.conn() as c:
            res = c.execute(
                text(
                    "INSERT INTO scrape_runs (jurisdiction_code, started_at, status) "
                    "VALUES (:j, :now, 'running')"
                ),
                {"j": jurisdiction_code, "now": utcnow()},
            )
            return res.lastrowid

    def finish_run(self, run_id, checked, new, changed, errors, status="done",
                   notes=None) -> None:
        with self.conn() as c:
            c.execute(
                text(
                    "UPDATE scrape_runs SET finished_at=:now, docs_checked=:ch, "
                    "docs_new=:new, docs_changed=:cg, docs_error=:er, status=:st, "
                    "notes=:notes WHERE id=:id"
                ),
                {
                    "now": utcnow(), "ch": checked, "new": new, "cg": changed,
                    "er": errors, "st": status, "notes": notes, "id": run_id,
                },
            )

    # ── Reads ──────────────────────────────────────────────────────────────

    def get_document_by_id(self, document_id: int) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                text("SELECT * FROM tax_documents WHERE id=:i"), {"i": document_id}
            ).mappings().fetchone()
            return dict(row) if row else None

    def list_versions(self, document_id: int) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                text("SELECT * FROM document_versions WHERE document_id=:i "
                     "ORDER BY version_no DESC"),
                {"i": document_id},
            ).mappings().all()
            return [dict(r) for r in rows]

    _CHANGE_SELECT = (
        "SELECT ch.*, d.title, d.jurisdiction_code, d.doc_type, d.url "
        "FROM document_changes ch JOIN tax_documents d ON d.id = ch.document_id "
    )

    def recent_changes(self, limit=50, jurisdiction_code=None) -> list[dict]:
        q = self._CHANGE_SELECT
        params = {"lim": limit}
        if jurisdiction_code:
            q += "WHERE d.jurisdiction_code = :j "
            params["j"] = jurisdiction_code
        q += "ORDER BY ch.detected_at DESC LIMIT :lim"
        with self.conn() as c:
            rows = c.execute(text(q), params).mappings().all()
            return [dict(r) for r in rows]

    def list_changes_for_document(self, document_id: int) -> list[dict]:
        q = self._CHANGE_SELECT + "WHERE ch.document_id=:i ORDER BY ch.detected_at DESC"
        with self.conn() as c:
            rows = c.execute(text(q), {"i": document_id}).mappings().all()
            return [dict(r) for r in rows]

    def get_change(self, change_id: int) -> dict | None:
        q = self._CHANGE_SELECT + "WHERE ch.id=:i"
        with self.conn() as c:
            row = c.execute(text(q), {"i": change_id}).mappings().fetchone()
            return dict(row) if row else None

    def list_citations(self, document_id: int, limit: int = 40) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                text(
                    "SELECT DISTINCT cited_instrument, cited_locator FROM citations "
                    "WHERE document_id=:i AND cited_instrument IS NOT NULL LIMIT :lim"
                ),
                {"i": document_id, "lim": limit},
            ).mappings().all()
            return [dict(r) for r in rows]

    def get_jurisdiction(self, code: str) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                text("SELECT * FROM jurisdictions WHERE code=:c"), {"c": code}
            ).mappings().fetchone()
            return dict(row) if row else None

    def list_jurisdictions_with_counts(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(text(
                "SELECT j.code, j.name, j.authority, "
                "(SELECT COUNT(*) FROM tax_documents d WHERE d.jurisdiction_code=j.code) docs "
                "FROM jurisdictions j ORDER BY j.name")).mappings().all()
            return [dict(r) for r in rows]

    def list_documents_for_jurisdiction(self, code: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(text(
                "SELECT d.*, (SELECT COUNT(*) FROM document_versions v WHERE v.document_id=d.id) versions "
                "FROM tax_documents d WHERE d.jurisdiction_code=:c ORDER BY d.doc_type, d.title"),
                {"c": code}).mappings().all()
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self.conn() as c:
            def scalar(q):
                return c.execute(text(q)).scalar() or 0
            return {
                "jurisdictions": scalar("SELECT COUNT(*) FROM jurisdictions"),
                "documents": scalar("SELECT COUNT(*) FROM tax_documents"),
                "versions": scalar("SELECT COUNT(*) FROM document_versions"),
                "changes": scalar("SELECT COUNT(*) FROM document_changes"),
                "by_jurisdiction": [
                    dict(r) for r in c.execute(text(
                        "SELECT jurisdiction_code, COUNT(*) AS docs FROM tax_documents "
                        "GROUP BY jurisdiction_code ORDER BY jurisdiction_code"
                    )).mappings().all()
                ],
            }

    def get_user_by_email(self, email: str) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                text("SELECT id, password_hash FROM users WHERE email=:e"),
                {"e": email.strip().lower()},
            ).mappings().fetchone()
            return dict(row) if row else None

    def get_or_create_oauth_user(self, email: str, name: str | None = None) -> dict:
        email = email.strip().lower()
        with self.conn() as c:
            row = c.execute(
                text("SELECT id FROM users WHERE email=:e"), {"e": email}
            ).mappings().fetchone()
            if row:
                return {"id": row["id"], "email": email}
            c.execute(
                text("INSERT INTO users (email, password_hash, name, role) "
                     "VALUES (:e, NULL, :n, 'user')"),
                {"e": email, "n": name or email.split("@")[0]},
            )
            row = c.execute(
                text("SELECT id FROM users WHERE email=:e"), {"e": email}
            ).mappings().fetchone()
            return {"id": row["id"], "email": email}

    # ── Graph-RAG retrieval (full-text over current versions) ───────────────

    def search_versions(self, query: str, limit: int = 8) -> list[dict]:
        """Substring/keyword scan over each document's current version.

        SQLite has no built-in semantic search; we score the current version of
        each document by how many query terms it contains. Good enough as the
        relational fallback — the Neo4j backend uses a real full-text index.
        """
        terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 2]
        if not terms:
            return []
        with self.conn() as c:
            rows = c.execute(text(
                "SELECT d.id AS document_id, d.doc_key, d.title, d.jurisdiction_code, "
                "       d.doc_type, d.url, d.reference, v.version_no, v.text_content "
                "FROM tax_documents d JOIN document_versions v ON v.id = d.current_version_id "
                "WHERE d.status='active'")).mappings().all()
        scored = []
        for r in rows:
            hay = (r["text_content"] or "").lower()
            score = sum(hay.count(t) for t in terms)
            if score:
                scored.append({**dict(r), "score": float(score)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    # ── Vector retrieval (brute-force cosine; the relational fallback) ──────
    def ensure_vector_index(self, dim: int) -> None:
        # No native vector index in SQLite; the table + brute-force scan suffice.
        pass

    def index_version_chunks(self, version_id, document_id, chunks) -> int:
        with self.conn() as c:
            c.execute(text("DELETE FROM version_chunks WHERE version_id = :v"),
                      {"v": version_id})
            for ordn, txt, emb in chunks:
                c.execute(text(
                    "INSERT INTO version_chunks (version_id, document_id, ord, text, embedding) "
                    "VALUES (:v, :d, :o, :t, :e)"),
                    {"v": version_id, "d": document_id, "o": ordn,
                     "t": txt, "e": json.dumps(emb)})
        return len(chunks)

    def vector_search_chunks(self, query_vector, limit: int = 8) -> list[dict]:
        import math
        q = query_vector
        qn = math.sqrt(sum(x * x for x in q)) or 1.0
        with self.conn() as c:
            rows = c.execute(text(
                "SELECT vc.text AS chunk_text, vc.embedding AS embedding, "
                "       d.id AS document_id, d.doc_key, d.title, d.jurisdiction_code, "
                "       d.doc_type, d.url, d.reference "
                "FROM version_chunks vc "
                "JOIN tax_documents d ON d.current_version_id = vc.version_id "
                "WHERE d.status='active'")).mappings().all()
        scored = []
        for r in rows:
            e = json.loads(r["embedding"])
            dot = sum(a * b for a, b in zip(q, e))
            en = math.sqrt(sum(x * x for x in e)) or 1.0
            score = dot / (qn * en)
            row = {k: r[k] for k in r.keys() if k != "embedding"}
            scored.append({**row, "score": float(score)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def count_chunks(self) -> int:
        with self.conn() as c:
            return c.execute(text("SELECT COUNT(*) FROM version_chunks")).scalar() or 0

    # ── Tax forms ──────────────────────────────────────────────────────────
    _FORM_COLS = ("jurisdiction_code", "category", "form_type", "form_key", "title",
                  "authority", "url", "file_path", "year", "who_files", "deadline",
                  "frequency", "summary", "legislation_ref", "filing_type")

    def upsert_form(self, form: dict) -> int:
        now = utcnow()
        # Only touch keys actually provided → partial upserts aren't destructive.
        vals = {k: form[k] for k in self._FORM_COLS if k in form}
        with self.conn() as c:
            row = c.execute(text(
                "SELECT id FROM tax_forms WHERE jurisdiction_code=:j AND form_key=:k"),
                {"j": form["jurisdiction_code"], "k": form["form_key"]}).first()
            if row:
                upd = {k: v for k, v in vals.items()
                       if k not in ("jurisdiction_code", "form_key")}
                if upd:
                    sets = ", ".join(f"{k}=:{k}" for k in upd)
                    c.execute(text(f"UPDATE tax_forms SET {sets}, last_seen=:now WHERE id=:id"),
                              {**upd, "now": now, "id": row[0]})
                return row[0]
            cols = ", ".join(vals)
            ph = ", ".join(f":{k}" for k in vals)
            res = c.execute(text(
                f"INSERT INTO tax_forms ({cols}, first_seen, last_seen) "
                f"VALUES ({ph}, :now, :now)"), {**vals, "now": now})
            return res.lastrowid

    def get_form(self, form_id: int) -> dict | None:
        with self.conn() as c:
            r = c.execute(text("SELECT * FROM tax_forms WHERE id=:i"),
                          {"i": form_id}).mappings().first()
            return dict(r) if r else None

    def list_forms(self, jurisdiction_code=None, category=None, limit=500) -> list[dict]:
        q = "SELECT * FROM tax_forms WHERE 1=1"
        p = {}
        if jurisdiction_code:
            q += " AND jurisdiction_code=:j"; p["j"] = jurisdiction_code
        if category:
            q += " AND category=:c"; p["c"] = category
        q += " ORDER BY jurisdiction_code, category, form_type, title LIMIT :lim"
        p["lim"] = limit
        with self.conn() as c:
            return [dict(r) for r in c.execute(text(q), p).mappings().all()]

    def search_forms(self, query: str, limit: int = 10) -> list[dict]:
        terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 2]
        if not terms:
            return []
        with self.conn() as c:
            rows = c.execute(text("SELECT * FROM tax_forms")).mappings().all()
        scored = []
        for r in rows:
            hay = " ".join(str(r[k] or "") for k in
                           ("title", "category", "form_type", "summary",
                            "who_files", "jurisdiction_code")).lower()
            score = sum(hay.count(t) for t in terms)
            if score:
                scored.append({**dict(r), "score": float(score)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def build_provenance_edges(self) -> dict:
        # Relational backend: provenance is the tax_forms.legislation_ref column;
        # there are no separate edge rows to materialise. Report counts so the
        # call is meaningful and parity with the graph backend is observable.
        with self.conn() as c:
            refs = [r[0] for r in c.execute(text(
                "SELECT legislation_ref FROM tax_forms "
                "WHERE coalesce(legislation_ref,'')<>''")).all()]
            doc_urls = {r[0] for r in c.execute(text(
                "SELECT url FROM tax_documents WHERE coalesce(url,'')<>''")).all()}
        laws = set(refs)
        return {"legislation_nodes": len(laws), "implements_edges": len(refs),
                "sourced_from_edges": len(laws & doc_urls), "backend": "sqlite-derived"}

    # ── Entities ───────────────────────────────────────────────────────────
    @staticmethod
    def _entity_row(r) -> dict:
        d = dict(r)
        d["jurisdictions"] = [x for x in (d.get("jurisdictions") or "").split(",") if x]
        d["activities"] = [x for x in (d.get("activities") or "").split(",") if x]
        return d

    def upsert_entity(self, entity: dict) -> int:
        now = utcnow()
        v = {
            "name": entity.get("name"), "type": entity.get("type"),
            "domicile": entity.get("domicile"),
            "jurisdictions": ",".join(entity.get("jurisdictions") or []),
            "fy_end": entity.get("fy_end"),
            "activities": ",".join(entity.get("activities") or []),
            "client_ref": entity.get("client_ref") or entity.get("name"),
            "status": entity.get("status") or "active",
            "team_id": entity.get("team_id"),
        }
        with self.conn() as c:
            row = c.execute(text("SELECT id FROM entities WHERE client_ref=:k"),
                            {"k": v["client_ref"]}).first()
            if row:
                # Don't overwrite an existing team_id with NULL on a metadata refresh.
                upd = {k: val for k, val in v.items()
                       if not (k == "team_id" and val is None)}
                sets = ", ".join(f"{k}=:{k}" for k in upd)
                c.execute(text(f"UPDATE entities SET {sets}, updated_at=:now WHERE id=:id"),
                          {**upd, "now": now, "id": row[0]})
                return row[0]
            res = c.execute(text(
                "INSERT INTO entities (name,type,domicile,jurisdictions,fy_end,"
                "activities,client_ref,status,team_id,created_at,updated_at) VALUES "
                "(:name,:type,:domicile,:jurisdictions,:fy_end,:activities,"
                ":client_ref,:status,:team_id,:now,:now)"), {**v, "now": now})
            return int(res.lastrowid)

    def get_entity(self, entity_id: int) -> dict | None:
        with self.conn() as c:
            r = c.execute(text("SELECT * FROM entities WHERE id=:i"),
                          {"i": entity_id}).mappings().first()
            return self._entity_row(r) if r else None

    def list_entities(self, limit: int = 500, team_id: int | None = None) -> list[dict]:
        clause, params = "", {"l": limit}
        if team_id is not None:
            clause = "WHERE team_id=:t "; params["t"] = team_id
        with self.conn() as c:
            rows = c.execute(text(f"SELECT * FROM entities {clause}ORDER BY name LIMIT :l"),
                             params).mappings().all()
        return [self._entity_row(r) for r in rows]

    def delete_entity(self, entity_id: int) -> None:
        with self.conn() as c:
            c.execute(text("DELETE FROM entities WHERE id=:i"), {"i": entity_id})

    def count_entities(self, team_id: int | None = None) -> int:
        clause = "WHERE team_id=:t" if team_id is not None else ""
        with self.conn() as c:
            return int(c.execute(text(f"SELECT count(*) FROM entities {clause}"),
                                 ({"t": team_id} if team_id is not None else {})).scalar() or 0)

    # ── Obligations ────────────────────────────────────────────────────────
    @staticmethod
    def _ob_row(r) -> dict:
        d = dict(r)
        d["verified"] = bool(d.get("verified"))
        return d

    def upsert_obligation(self, ob: dict) -> int:
        now = utcnow()
        desc = {"title": ob.get("title"), "jurisdiction_code": ob.get("jurisdiction_code"),
                "category": ob.get("category"), "deadline": ob.get("deadline"),
                "period": ob.get("period")}
        with self.conn() as c:
            row = c.execute(text(
                "SELECT id FROM obligations WHERE entity_id=:e AND form_id=:f"),
                {"e": ob["entity_id"], "f": ob["form_id"]}).first()
            if row:  # refresh descriptive fields only — keep status/verified
                sets = ", ".join(f"{k}=:{k}" for k in desc)
                c.execute(text(f"UPDATE obligations SET {sets}, updated_at=:now WHERE id=:id"),
                          {**desc, "now": now, "id": row[0]})
                return row[0]
            res = c.execute(text(
                "INSERT INTO obligations (entity_id,form_id,title,jurisdiction_code,"
                "category,deadline,period,status,verified,created_at,updated_at) VALUES "
                "(:e,:f,:title,:jurisdiction_code,:category,:deadline,:period,"
                "'not_started',0,:now,:now)"),
                {"e": ob["entity_id"], "f": ob["form_id"], **desc, "now": now})
            return int(res.lastrowid)

    def get_obligation(self, obligation_id: int) -> dict | None:
        with self.conn() as c:
            r = c.execute(text("SELECT * FROM obligations WHERE id=:i"),
                          {"i": obligation_id}).mappings().first()
            return self._ob_row(r) if r else None

    def list_obligations(self, entity_id=None, status=None, limit=1000,
                         team_id=None) -> list[dict]:
        where, params = [], {"l": limit}
        if entity_id is not None:
            where.append("o.entity_id=:e"); params["e"] = entity_id
        if status:
            where.append("o.status=:s"); params["s"] = status
        join = ""
        if team_id is not None:
            join = "JOIN entities en ON en.id=o.entity_id "
            where.append("en.team_id=:t"); params["t"] = team_id
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with self.conn() as c:
            rows = c.execute(text(
                f"SELECT o.* FROM obligations o {join}{clause} "
                "ORDER BY o.jurisdiction_code, o.category LIMIT :l"), params).mappings().all()
        return [self._ob_row(r) for r in rows]

    def set_obligation_status(self, obligation_id: int, status: str) -> None:
        with self.conn() as c:
            c.execute(text("UPDATE obligations SET status=:s, updated_at=:n WHERE id=:i"),
                      {"s": status, "n": utcnow(), "i": obligation_id})

    def set_obligation_verified(self, obligation_id: int, verified: bool) -> None:
        with self.conn() as c:
            c.execute(text("UPDATE obligations SET verified=:v, updated_at=:n WHERE id=:i"),
                      {"v": 1 if verified else 0, "n": utcnow(), "i": obligation_id})

    def delete_obligation(self, obligation_id: int) -> None:
        with self.conn() as c:
            c.execute(text("DELETE FROM obligations WHERE id=:i"), {"i": obligation_id})

    # ── Chat history ───────────────────────────────────────────────────────
    def create_chat_session(self, user_email: str, title: str = "",
                            team_id: int | None = None) -> int:
        now = utcnow()
        with self.conn() as c:
            res = c.execute(text(
                "INSERT INTO chat_sessions (user_email, team_id, title, created_at, updated_at) "
                "VALUES (:e, :tid, :t, :now, :now)"),
                {"e": user_email, "tid": team_id, "t": title or "New chat", "now": now})
            return res.lastrowid

    def add_chat_message(self, session_id: int, role: str, content: str) -> None:
        now = utcnow()
        with self.conn() as c:
            c.execute(text("INSERT INTO chat_messages (session_id, role, content, created_at) "
                           "VALUES (:s, :r, :c, :now)"),
                      {"s": session_id, "r": role, "c": content, "now": now})
            c.execute(text("UPDATE chat_sessions SET updated_at=:now WHERE id=:s"),
                      {"now": now, "s": session_id})

    def list_chat_sessions(self, user_email: str, limit: int = 30) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(text(
                "SELECT id, title, updated_at FROM chat_sessions WHERE user_email=:e "
                "ORDER BY updated_at DESC LIMIT :lim"),
                {"e": user_email, "lim": limit}).mappings().all()]

    def get_chat_messages(self, session_id: int) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(text(
                "SELECT role, content, created_at FROM chat_messages "
                "WHERE session_id=:s ORDER BY id"), {"s": session_id}).mappings().all()]
