"""Qdrant dense + BM25 hybrid retrieval with RRF fusion, exposed as LangChain Tool."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import Any, Sequence

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.db import qdrant as qdb
from app.services.embedding import embed_query

logger = logging.getLogger(__name__)

_token_re = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Simple unicode-friendly tokenizer for BM25."""
    return [t.lower() for t in _token_re.findall(text)]


def _rrf(rank_lists: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]:
    """
    Reciprocal Rank Fusion across ordered id lists.

    Args:
        rank_lists: Each list is doc ids ordered by relevance (best first).
        k: RRF smoothing constant.

    Returns:
        Sorted (doc_id, score) descending by fused score.
    """
    scores: dict[str, float] = defaultdict(float)
    for ids in rank_lists:
        for rank, doc_id in enumerate(ids):
            scores[str(doc_id)] += 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


async def hybrid_search(
    query: str,
    top_k: int = 8,
    dense_candidates: int = 20,
    bm25_candidates: int = 50,
) -> list[dict[str, Any]]:
    """
    Run dense vector search in Qdrant + BM25 over scrolled corpus, fuse with RRF.

    Args:
        query: Natural language query.
        top_k: Number of fused results to return.
        dense_candidates: Vector search depth.
        bm25_candidates: BM25 candidate depth.

    Returns:
        List of dicts: id, text, score, sources from metadata.

    TODO: Use Qdrant native sparse vectors + two-stage retrieval for large corpora.
    """
    settings = get_settings()
    client = qdb.get_qdrant_client()
    if not await client.collection_exists(settings.qdrant_collection):
        return []

    vec = await embed_query(query)
    dense_hits = await client.search(
        collection_name=settings.qdrant_collection,
        query_vector=vec,
        limit=dense_candidates,
        with_payload=True,
    )
    dense_ids = [str(hit.id) for hit in dense_hits]

    corpus = await qdb.scroll_all_texts()
    if not corpus:
        return []

    from rank_bm25 import BM25Okapi  # local import to keep module import light

    tokenized_corpus = [_tokenize(row["text"]) for row in corpus]
    bm25 = BM25Okapi(tokenized_corpus)
    q_tokens = _tokenize(query)
    scores = bm25.get_scores(q_tokens)
    ranked_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[
        :bm25_candidates
    ]
    bm25_ids = [str(corpus[i]["id"]) for i in ranked_idx]

    fused = _rrf([dense_ids, bm25_ids])[:top_k]
    id_to_row = {str(row["id"]): row for row in corpus}
    # enrich with dense payloads if missing in scroll (same ids)
    out: list[dict[str, Any]] = []
    for doc_id, rrf_score in fused:
        row = id_to_row.get(doc_id)
        payload_text = None
        if row is None:
            # fetch point payload if not in scroll slice (rare)
            try:
                pts = await client.retrieve(
                    collection_name=settings.qdrant_collection,
                    ids=[doc_id],
                    with_payload=True,
                )
                if pts:
                    payload = pts[0].payload or {}
                    payload_text = str(payload.get("text", ""))
                    meta = {k: v for k, v in payload.items() if k != "text"}
            except Exception as exc:  # noqa: BLE001
                logger.debug("retrieve fallback failed: %s", exc)
                payload_text = ""
                meta = {}
        else:
            payload_text = row["text"]
            meta = {k: v for k, v in row.items() if k not in ("id", "text")}
        out.append(
            {
                "id": doc_id,
                "text": payload_text or "",
                "rrf": round(float(rrf_score), 6),
                "metadata": meta,
            }
        )
    return out


class RagQuery(BaseModel):
    """Arguments for hybrid KB search."""

    query: str = Field(description="User question or rewritten query for KB retrieval.")
    top_k: int = Field(default=6, ge=1, le=20, description="How many chunks to return.")


@tool("rag_hybrid_search", args_schema=RagQuery)
async def rag_hybrid_search_tool(query: str, top_k: int = 6) -> str:
    """
    Tool: hybrid dense+BM25 retrieval with RRF fusion over internal payment KB.

    Returns:
        JSON string of top passages for the LLM.
    """
    hits = await hybrid_search(query, top_k=top_k)
    return json.dumps({"hits": hits}, ensure_ascii=False)
