"""Lightweight file-based RAG: JSONL documents + numpy embedding index.

Knowledge base layout under RAG_DB_PATH:
  docs.jsonl      — one JSON object per line: {id, text, source, tags[]}
  embeddings.npy  — float32 matrix (N, D), row i = embedding of docs[i]

Ingest a new document with `lusterna rag add <file>` (see cli.py).
"""
import json
import logging
import math
from pathlib import Path
from typing import Any

import anthropic
import numpy as np

from . import config

log = logging.getLogger(__name__)

_EMBED_MODEL = "voyage-3"  # Anthropic embedding model via voyageai client

_client: anthropic.Anthropic | None = None
_docs: list[dict[str, Any]] = []
_matrix: np.ndarray | None = None  # shape (N, D)


def _embed_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _embed_texts(texts: list[str]) -> np.ndarray:
    """Return a float32 (N, D) matrix of embeddings via Anthropic Voyage."""
    import voyageai  # optional dep; only needed for ingest

    vc = voyageai.Client()
    result = vc.embed(texts, model=_EMBED_MODEL)
    return np.array(result.embeddings, dtype=np.float32)


def _load() -> None:
    global _docs, _matrix
    docs_path = config.RAG_DB_PATH / "docs.jsonl"
    emb_path = config.RAG_DB_PATH / "embeddings.npy"
    if not docs_path.exists():
        log.warning("RAG knowledge base not found at %s — RAG disabled", config.RAG_DB_PATH)
        return
    _docs = [json.loads(l) for l in docs_path.read_text().splitlines() if l.strip()]
    if emb_path.exists():
        _matrix = np.load(str(emb_path))
        log.info("RAG loaded: %d documents, embedding dim=%d", len(_docs), _matrix.shape[1])
    else:
        log.warning("RAG embeddings missing — run `lusterna rag build` to index documents")


def _cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise cosine similarity of query vector a (D,) against matrix b (N, D)."""
    a_norm = a / (np.linalg.norm(a) + 1e-10)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
    return b_norm @ a_norm


def query(text: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Return top_k most relevant documents for *text*."""
    if _matrix is None or not _docs:
        _load()
    if _matrix is None or not _docs:
        return []
    q_emb = _embed_texts([text])[0]
    scores = _cosine(q_emb, _matrix)
    indices = np.argsort(scores)[::-1][:top_k]
    return [{"score": float(scores[i]), **_docs[i]} for i in indices]


# ── ingest helpers (used by CLI) ──────────────────────────────────────────────

def ingest(docs: list[dict[str, Any]]) -> None:
    """Add *docs* to the knowledge base and rebuild embeddings."""
    config.RAG_DB_PATH.mkdir(parents=True, exist_ok=True)
    docs_path = config.RAG_DB_PATH / "docs.jsonl"
    emb_path = config.RAG_DB_PATH / "embeddings.npy"

    existing: list[dict[str, Any]] = []
    if docs_path.exists():
        existing = [json.loads(l) for l in docs_path.read_text().splitlines() if l.strip()]

    all_docs = existing + docs
    texts = [d["text"] for d in all_docs]
    log.info("Embedding %d documents …", len(texts))
    matrix = _embed_texts(texts)

    docs_path.write_text("\n".join(json.dumps(d) for d in all_docs))
    np.save(str(emb_path), matrix)
    log.info("RAG index written: %d docs, shape=%s", len(all_docs), matrix.shape)
