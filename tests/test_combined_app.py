"""Smoke tests for the merged FastFund application surface."""

from __future__ import annotations

import os
import tempfile

_fd, _db_path = tempfile.mkstemp(prefix="fastfund-combined-", suffix=".db")
os.close(_fd)
os.environ["DATA_STORAGE"] = "sqlite"
os.environ["DB_URL"] = f"sqlite:///{_db_path}"
os.environ["FASTFUND_PUBLIC"] = "1"

from starlette.testclient import TestClient

import taxstore
from family import sfostore
from web.app import app


def test_tax_and_family_routes_share_one_application():
    with TestClient(app) as client:
        for path in (
            "/",
            "/dashboard",
            "/entities",
            "/obligations",
            "/family/",
            "/family/clients",
            "/family/dashboard",
            "/family/services",
            "/user-guide",
            "/guide-assets/fastfund-assistant",
        ):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 200, path
            if path.startswith("/guide-assets/"):
                assert response.headers["content-type"].startswith("image/png")
            else:
                assert b"FastFund" in response.content


def test_family_office_owns_tax_entity():
    family_id = sfostore.upsert_sfo(
        {"client_ref": "FF-TEST", "name": "FastFund Test Family"}
    )
    entity_id = taxstore.upsert_entity(
        {
            "name": "FastFund Test Holdings Ltd",
            "client_ref": "FF-TEST-ENTITY",
            "type": "holdco",
            "domicile": "JE",
            "jurisdictions": ["JE"],
            "activities": ["holding"],
            "sfo_id": family_id,
        }
    )

    assert taxstore.get_entity(entity_id)["sfo_id"] == family_id
    with TestClient(app) as client:
        family_page = client.get(f"/family/sfo/{family_id}")
        entity_page = client.get(f"/entity/{entity_id}")
    assert b"FastFund Test Holdings Ltd" in family_page.content
    assert b"FastFund Test Family" in entity_page.content
