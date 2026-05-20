"""Chat model factory with OpenAI-compatible base URL (中转) and tool calling."""

from __future__ import annotations

import logging
from typing import Any

from langchain_openai import ChatOpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_chat: ChatOpenAI | None = None


def get_chat_model(**kwargs: Any) -> ChatOpenAI:
    """
    Return a configured ChatOpenAI instance.

    Args:
        **kwargs: Overrides passed to ChatOpenAI (e.g. temperature, model_kwargs).

    Returns:
        ChatOpenAI: LangChain chat model bound to gateway in settings.
    """
    global _chat
    s = get_settings()
    # Allow per-call overrides without mutating singleton defaults
    common = {
        "api_key": s.openai_api_key or "dummy",
        "base_url": s.openai_api_base,
        "max_retries": s.openai_max_retries,
        "timeout": s.openai_timeout_seconds,
    }
    if kwargs:
        return ChatOpenAI(
            model=kwargs.pop("model", s.openai_chat_model),
            **common,
            **kwargs,
        )
    if _chat is None:
        _chat = ChatOpenAI(
            model=s.openai_chat_model,
            temperature=0.2,
            **common,
        )
    return _chat
