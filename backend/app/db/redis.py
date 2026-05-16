"""Redis connection helpers for sessions, rate limits, and short-term memory."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_client: Optional[redis.Redis] = None


async def get_redis() -> Optional[redis.Redis]:
    """
    Return a shared async Redis client, or None if Redis is disabled or unreachable.

    Returns:
        redis.Redis | None: Async client when available.
    """
    global _client
    settings = get_settings()
    if not settings.redis_enabled:
        return None
    if _client is not None:
        return _client
    try:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
        await _client.ping()
        return _client
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis unavailable, continuing without cache: %s", exc)
        _client = None
        return None


async def close_redis() -> None:
    """Close the global Redis client if it was opened."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def session_get_json(key: str) -> Any | None:
    """
    Load JSON value for a session-scoped key.

    Args:
        key: Redis key.

    Returns:
        Parsed JSON or None if missing / Redis down.
    """
    r = await get_redis()
    if r is None:
        return None
    raw = await r.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def session_set_json(key: str, value: Any, ttl_seconds: int | None = None) -> None:
    """
    Store JSON-serializable value under key with optional TTL.

    Args:
        key: Redis key.
        value: JSON-serializable object.
        ttl_seconds: Expiration in seconds.
    """
    r = await get_redis()
    if r is None:
        return
    payload = json.dumps(value, ensure_ascii=False)
    if ttl_seconds:
        await r.setex(key, ttl_seconds, payload)
    else:
        await r.set(key, payload)


async def rate_limit_allow(key: str, limit: int, window_seconds: int) -> bool:
    """
    Simple fixed-window rate limiter.

    Args:
        key: Redis key for counter.
        limit: Max events per window.
        window_seconds: Window length.

    Returns:
        True if request is allowed, False if rate limited.
    """
    r = await get_redis()
    if r is None:
        return True
    try:
        pipe = r.pipeline()
        pipe.incr(key, 1)
        pipe.ttl(key)
        count, ttl = await pipe.execute()
        if ttl == -1:
            await r.expire(key, window_seconds)
        return int(count) <= limit
    except Exception as exc:  # noqa: BLE001
        logger.warning("rate_limit_allow failed open: %s", exc)
        return True
