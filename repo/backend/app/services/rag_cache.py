"""In-process caches for RAG: BM25 corpus + query embeddings."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from app.core.config import get_settings
from app.db import qdrant as qdb
from app.services.embedding import embed_query

logger = logging.getLogger(__name__)

_corpus_cache: tuple[float, list[dict[str, Any]]] | None = None
_bm25_cache: tuple[float, Any] | None = None
_embed_cache: dict[str, tuple[float, list[float]]] = {}


async def get_corpus_and_bm25() -> tuple[list[dict[str, Any]], Any]:
    """
    Return (corpus rows, BM25Okapi) with TTL cache to avoid scroll on every search.

    Returns:
        Tuple of corpus list and BM25 index object.
    """
    global _corpus_cache, _bm25_cache
    settings = get_settings()
    ttl = settings.rag_bm25_cache_ttl_seconds
    now = time.monotonic()
    if _corpus_cache and _bm25_cache and now - _corpus_cache[0] < ttl:
        return _corpus_cache[1], _bm25_cache[1]

    from rank_bm25 import BM25Okapi

    from app.tools.rag_tool import _tokenize

    corpus = await qdb.scroll_all_texts()
    tokenized = [_tokenize(row["text"]) for row in corpus]
    bm25 = BM25Okapi(tokenized) if tokenized else None
    _corpus_cache = (now, corpus)
    _bm25_cache = (now, bm25)
    logger.debug("BM25 corpus cache refreshed (%s docs)", len(corpus))
    return corpus, bm25


def invalidate_rag_cache() -> None:
    """Clear caches after KB re-ingestion (optional ops hook)."""
    global _corpus_cache, _bm25_cache, _embed_cache
    _corpus_cache = None
    _bm25_cache = None
    _embed_cache.clear()


async def embed_query_cached(query: str) -> list[float]:
    """
    Embed query with short TTL in-memory cache (same session repeated questions).

    Args:
        query: User or agent query string.

    Returns:
        Embedding vector.
    """
    settings = get_settings()
    ttl = settings.rag_embed_cache_ttl_seconds
    key = hashlib.sha256(query.strip().encode()).hexdigest()
    now = time.monotonic()
    hit = _embed_cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    vec = await embed_query(query)
    _embed_cache[key] = (now, vec)
    if len(_embed_cache) > 512:
        _embed_cache.clear()
    return vec
