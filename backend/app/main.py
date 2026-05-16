"""FastAPI application entrypoint: routes, CORS, lifespan, and observability hooks."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings

# LangSmith / LangChain tracing must be in os.environ before graph & LLM imports.
get_settings()

from app.api import chat  # noqa: E402
from app.db import qdrant as qdb
from app.db.redis import close_redis

logging.basicConfig(level=logging.INFO)
# OpenAI SDK retry lines (429/5xx) are noisy at INFO during multi-agent turns.
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan: seed vector store and release resources on shutdown.

    Args:
        app: FastAPI application instance.

    Yields:
        Control back to FastAPI between startup and shutdown.
    """
    settings = get_settings()
    settings.export_langsmith_env()
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY is empty — LLM calls will fail.")
    logger.info(
        "OpenAI base_url=%s model=%s (set OPENAI_API_BASE for relay/中转)",
        settings.openai_api_base,
        settings.openai_chat_model,
    )
    if settings.langchain_tracing_v2:
        logger.info(
            "LangSmith tracing ON project=%s endpoint=%s",
            settings.langchain_project,
            settings.langchain_endpoint,
        )
    try:
        await qdb.maybe_seed_from_data_dir()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qdrant seed skipped: %s", exc)
    yield
    await close_redis()


def create_app() -> FastAPI:
    """
    Build FastAPI app with middleware and routers.

    Returns:
        Configured FastAPI instance.
    """
    settings = get_settings()
    app = FastAPI(title="Mall Multi-Agent CS", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(chat.router, prefix="/api")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Lightweight readiness probe for orchestrators."""
        return {"status": "ok"}

    @app.get("/api/config/langsmith-check")
    async def langsmith_check() -> dict[str, str | bool]:
        """Confirm LangSmith tracing flags (no secrets)."""
        s = get_settings()
        return {
            "langchain_tracing_v2": s.langchain_tracing_v2,
            "langchain_project": s.langchain_project,
            "langchain_endpoint": s.langchain_endpoint,
            "api_key_configured": bool(s.langchain_api_key or s.langsmith_api_key),
            "dashboard": "https://smith.langchain.com",
        }

    @app.get("/api/config/openai-check")
    async def openai_check() -> dict[str, str | bool]:
        """
        Non-secret sanity check: which gateway URL and model the server will use.

        Does not call OpenAI; use after changing ``.env`` to confirm reload picked it up.
        """
        s = get_settings()
        return {
            "openai_api_base": s.openai_api_base,
            "openai_chat_model": s.openai_chat_model,
            "openai_api_key_set": bool(s.openai_api_key),
            "hint": (
                "403 on api.openai.com usually means region block or wrong key; "
                "set OPENAI_API_BASE to your compatible relay URL ending in /v1"
            ),
        }

    return app


app = create_app()
