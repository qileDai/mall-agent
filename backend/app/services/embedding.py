"""OpenAI embedding wrapper (LangChain) with 中转 base URL support."""

from __future__ import annotations

import logging
from typing import List

from langchain_openai import OpenAIEmbeddings

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_embeddings: OpenAIEmbeddings | None = None


def get_embeddings() -> OpenAIEmbeddings:
    """Return singleton OpenAIEmbeddings configured for OpenAI-compatible gateways."""
    global _embeddings
    if _embeddings is None:
        s = get_settings()
        _embeddings = OpenAIEmbeddings(
            model=s.openai_embed_model,
            api_key=s.openai_api_key or "dummy",
            base_url=s.openai_api_base,
        )
    return _embeddings


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Embed a batch of texts.

    Args:
        texts: Raw strings.

    Returns:
        List of embedding vectors.
    """
    emb = get_embeddings()
    return await emb.aembed_documents(texts)


async def embed_query(text: str) -> List[float]:
    """
    Embed a single query string.

    Args:
        text: User or agent query.

    Returns:
        Embedding vector.
    """
    emb = get_embeddings()
    return await emb.aembed_query(text)
