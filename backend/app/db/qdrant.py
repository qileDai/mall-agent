"""Qdrant client bootstrap, collection management, and KB seeding."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Sequence

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from app.core.config import get_settings
from app.services.embedding import embed_texts

logger = logging.getLogger(__name__)

_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    """Return singleton AsyncQdrantClient."""
    global _client
    if _client is None:
        settings = get_settings()
        kwargs: dict[str, Any] = {"url": settings.qdrant_url}
        if settings.qdrant_api_key:
            kwargs["api_key"] = settings.qdrant_api_key
        _client = AsyncQdrantClient(**kwargs)
    return _client


async def ensure_collection(vector_size: int) -> None:
    """
    Create collection if missing (cosine + payload indexes for text filter).

    Args:
        vector_size: Embedding dimensionality.
    """
    client = get_qdrant_client()
    settings = get_settings()
    name = settings.qdrant_collection
    exists = await client.collection_exists(name)
    if exists:
        return
    await client.create_collection(
        collection_name=name,
        vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
    )
    await client.create_payload_index(
        collection_name=name,
        field_name="text",
        field_schema=qm.TextIndexParams(
            type="text",
            tokenizer=qm.TokenizerType.WORD,
            min_token_len=2,
            max_token_len=20,
            lowercase=True,
        ),
    )
    logger.info("Created Qdrant collection %s", name)


async def seed_demo_documents(docs: Sequence[dict[str, Any]]) -> int:
    """
    Upsert demo KB documents with dense vectors.

    Args:
        docs: Each item must include keys: id (str), text (str), metadata (dict).

    Returns:
        Number of points upserted.
    """
    if not docs:
        return 0
    settings = get_settings()
    client = get_qdrant_client()
    texts = [d["text"] for d in docs]
    vectors = await embed_texts(texts)
    await ensure_collection(len(vectors[0]))
    points: list[qm.PointStruct] = []
    for row, vec in zip(docs, vectors, strict=True):
        pid = row.get("id") or str(uuid.uuid4())
        points.append(
            qm.PointStruct(
                id=pid,
                vector=vec,
                payload={"text": row["text"], **(row.get("metadata") or {})},
            )
        )
    await client.upsert(collection_name=settings.qdrant_collection, points=points)
    return len(points)


async def scroll_all_texts(limit: int = 2000) -> list[dict[str, Any]]:
    """
    Scroll collection payloads for local BM25 index construction.

    Args:
        limit: Max records to pull (demo KB is small).

    Returns:
        List of dicts with id, text, metadata.
    """
    settings = get_settings()
    client = get_qdrant_client()
    if not await client.collection_exists(settings.qdrant_collection):
        return []
    out: list[dict[str, Any]] = []
    offset = None
    while True:
        records, offset = await client.scroll(
            collection_name=settings.qdrant_collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for rec in records:
            payload = rec.payload or {}
            text = str(payload.get("text", ""))
            out.append({"id": str(rec.id), "text": text, "metadata": dict(payload)})
            if len(out) >= limit:
                return out
        if offset is None:
            break
    return out


def default_seed_docs() -> list[dict[str, Any]]:
    """Return static demo KB snippets (payment FAQ / policy)."""
    return [
        {
            "id": "pay-001",
            "text": "退款将在 3-7 个工作日原路退回；若超时请提供订单号与支付渠道。",
            "metadata": {"source": "payment_faq", "topic": "refund"},
        },
        {
            "id": "pay-002",
            "text": "跨境支付可能产生额外手续费，具体以发卡行与通道规则为准。",
            "metadata": {"source": "payment_faq", "topic": "cross_border"},
        },
        {
            "id": "pay-003",
            "text": "重复扣款：请截图账单流水并提交工单，我们会发起调单核查。",
            "metadata": {"source": "payment_faq", "topic": "duplicate_charge"},
        },
        {
            "id": "pay-004",
            "text": "企业钱包充值支持对公转账与在线支付；到账时间取决于银行清算。",
            "metadata": {"source": "product_doc", "topic": "wallet_topup"},
        },
    ]


async def maybe_seed_from_data_dir() -> None:
    """
    If collection empty, seed demo documents.

    TODO: Replace with ingestion pipeline (PDF/HTML) and scheduled re-embed.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY missing — skipping Qdrant embedding seed.")
        return
    client = get_qdrant_client()
    if not await client.collection_exists(settings.qdrant_collection):
        docs = default_seed_docs()
        n = await seed_demo_documents(docs)
        logger.info("Seeded Qdrant with %s points", n)
        return
    info = await client.count(collection_name=settings.qdrant_collection, exact=True)
    if info.count == 0:
        n = await seed_demo_documents(default_seed_docs())
        logger.info("Seeded empty Qdrant collection with %s points", n)
