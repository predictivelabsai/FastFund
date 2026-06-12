"""Text embeddings + chunking for vector retrieval.

Default provider is **fastembed** — a local ONNX embedder (`BAAI/bge-small-en-v1.5`,
384-dim): no API key, no PyTorch, runs on CPU. xAI Grok has no embeddings
endpoint, so embeddings are intentionally decoupled from the chat LLM and
configurable via env:

    EMBEDDINGS   fastembed (default) | openai
    EMBED_MODEL  model name for the provider (default BAAI/bge-small-en-v1.5)
    EMBED_DIM    vector dimensions (default 384; must match the model)

Embeddings are optional: if the provider can't load, ``available()`` is False and
the retriever falls back to full-text only.
"""
from __future__ import annotations

import os
import re

PROVIDER = os.environ.get("EMBEDDINGS", "fastembed").lower()
MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5"
                       if PROVIDER == "fastembed" else "text-embedding-3-small")
DIM = int(os.environ.get("EMBED_DIM", "384" if PROVIDER == "fastembed" else "1536"))

_model = None
_load_error: Exception | None = None


def dim() -> int:
    return DIM


def _get():
    global _model, _load_error
    if _model is not None:
        return _model
    if _load_error is not None:
        raise _load_error
    try:
        if PROVIDER == "fastembed":
            from fastembed import TextEmbedding
            _model = TextEmbedding(model_name=MODEL)
        elif PROVIDER == "openai":
            from langchain_openai import OpenAIEmbeddings
            _model = OpenAIEmbeddings(model=MODEL)
        else:
            raise ValueError(f"Unknown EMBEDDINGS provider: {PROVIDER!r}")
    except Exception as e:  # noqa: BLE001
        _load_error = e
        raise
    return _model


def available() -> bool:
    """True if the embedding model can be loaded (so vector retrieval is usable)."""
    try:
        _get()
        return True
    except Exception:  # noqa: BLE001
        return False


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    m = _get()
    if PROVIDER == "fastembed":
        return [v.tolist() for v in m.embed(texts)]
    return m.embed_documents(texts)  # openai


def embed_query(text: str) -> list[float]:
    m = _get()
    if PROVIDER == "fastembed":
        return list(m.query_embed(text))[0].tolist()
    return m.embed_query(text)  # openai


def chunk_text(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    """Split text into overlapping character windows on whitespace boundaries.

    One vector per passage (not per document) so deep definitions/lists — which a
    single truncated doc-level embedding would miss — are individually retrievable.
    """
    text = re.sub(r"[ \t]+", " ", (text or "")).strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):  # extend to the next whitespace so we don't split a word
            nxt = text.find(" ", end)
            if nxt != -1 and nxt - end < 80:
                end = nxt
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - overlap
    return [c for c in chunks if c]
