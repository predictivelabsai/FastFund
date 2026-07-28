"""Document (blob) storage — local volume or Cloudflare R2.

Uploaded files (portfolio summaries, trust deeds, luxury-asset inventories) are
written here; the metadata + returned ``storage_key`` are recorded in the graph
via ``Storage.add_document``. Selected by ``DOC_STORAGE``:

* ``local`` (default) — files under ``DOC_LOCAL_DIR`` (a Coolify persistent
  volume in dev). Zero-infra.
* ``r2`` — Cloudflare R2 over its S3-compatible API (boto3). Set
  ``R2_ACCOUNT_ID``, ``R2_ACCESS_KEY_ID``, ``R2_SECRET_ACCESS_KEY``,
  ``R2_BUCKET``. The Azure-target equivalent is Blob Storage (same interface).

The interface is backend-neutral (``put`` / ``get`` / ``url``) so the web layer
doesn't know where bytes live — the same pattern as the graph `Storage`.
"""
from __future__ import annotations

import os
import re
import uuid

DOC_STORAGE = os.environ.get("DOC_STORAGE", "local").strip().lower()
DOC_LOCAL_DIR = os.environ.get("DOC_LOCAL_DIR", "data/uploads")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name or "file")


def _key(sfo_id: int | None, filename: str) -> str:
    return f"sfo-{sfo_id or 'misc'}/{uuid.uuid4().hex[:8]}-{_safe(filename)}"


class _LocalStore:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def put(self, sfo_id, filename, data: bytes) -> str:
        key = _key(sfo_id, filename)
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return key

    def get(self, key: str) -> bytes | None:
        path = os.path.join(self.root, key)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def url(self, key: str) -> str | None:
        return None  # served via the app's /document/{id}/file route


class _R2Store:
    def __init__(self):
        import boto3
        acct = os.environ["R2_ACCOUNT_ID"]
        self.bucket = os.environ["R2_BUCKET"]
        self.client = boto3.client(
            "s3", endpoint_url=f"https://{acct}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto")

    def put(self, sfo_id, filename, data: bytes) -> str:
        key = _key(sfo_id, filename)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def get(self, key: str) -> bytes | None:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
            return obj["Body"].read()
        except Exception:  # noqa: BLE001
            return None

    def url(self, key: str, expires: int = 3600) -> str | None:
        try:
            return self.client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires)
        except Exception:  # noqa: BLE001
            return None


_store = None


def get_docstore():
    global _store
    if _store is None:
        _store = _R2Store() if DOC_STORAGE == "r2" else _LocalStore(DOC_LOCAL_DIR)
    return _store
