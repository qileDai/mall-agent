"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_ROOT.parent


def _env_files() -> tuple[str, ...]:
    """
  Collect ``.env`` paths: ``backend/.env`` first, then repo root ``.env``.

  pydantic-settings loads the first existing file in order when given a tuple.
  """
    candidates = (_BACKEND_ROOT / ".env", _REPO_ROOT / ".env")
    return tuple(str(p) for p in candidates if p.is_file()) or (str(_BACKEND_ROOT / ".env"),)


class Settings(BaseSettings):
    """Runtime configuration for API, LLM, vector store, Redis, and LangSmith."""

    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenAI (compatible gateway / 中转) ---
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    openai_api_base: str = Field(
        default="https://api.openai.com/v1",
        validation_alias="OPENAI_API_BASE",
        description="OpenAI-compatible base URL, usually ends with /v1 (中转网关).",
    )
    openai_chat_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_CHAT_MODEL")
    openai_embed_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="OPENAI_EMBED_MODEL",
    )
    openai_max_retries: int = Field(default=2, validation_alias="OPENAI_MAX_RETRIES")
    openai_timeout_seconds: float = Field(default=120.0, validation_alias="OPENAI_TIMEOUT_SECONDS")

    # --- Qdrant ---
    qdrant_url: str = Field(default="http://127.0.0.1:6333", validation_alias="QDRANT_URL")
    qdrant_collection: str = Field(default="mall_cs_kb", validation_alias="QDRANT_COLLECTION")
    qdrant_api_key: str | None = Field(default=None, validation_alias="QDRANT_API_KEY")

    # --- Redis ---
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", validation_alias="REDIS_URL")
    redis_enabled: bool = Field(default=True, validation_alias="REDIS_ENABLED")
    session_ttl_seconds: int = Field(default=86400, validation_alias="SESSION_TTL_SECONDS")

    # --- LangSmith (set LANGCHAIN_TRACING_V2=true to enable) ---
    langchain_tracing_v2: bool = Field(default=False, validation_alias="LANGCHAIN_TRACING_V2")
    langchain_api_key: str | None = Field(default=None, validation_alias="LANGCHAIN_API_KEY")
    langsmith_api_key: str | None = Field(default=None, validation_alias="LANGSMITH_API_KEY")
    langchain_project: str = Field(default="mall-agent", validation_alias="LANGCHAIN_PROJECT")
    langchain_endpoint: str = Field(
        default="https://api.smith.langchain.com",
        validation_alias="LANGCHAIN_ENDPOINT",
    )

    # --- HTTP ---
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:8080",
        validation_alias="CORS_ORIGINS",
    )
    data_dir: str = Field(default="./data", validation_alias="DATA_DIR")

    @field_validator("openai_api_base")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        """Normalize base URL for OpenAI-compatible clients."""
        return v.rstrip("/")

    @property
    def cors_origin_list(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def export_langsmith_env(self) -> None:
        """
        Mirror LangSmith-related settings into process env for automatic tracing.

        LangChain/LangSmith reads LANGCHAIN_* from the environment at import time.
        """
        import os

        if self.langchain_tracing_v2:
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
            os.environ["LANGSMITH_TRACING"] = "true"
        if self.langchain_api_key:
            os.environ["LANGCHAIN_API_KEY"] = self.langchain_api_key
        elif self.langsmith_api_key:
            os.environ["LANGCHAIN_API_KEY"] = self.langsmith_api_key
        if self.langchain_project:
            os.environ["LANGCHAIN_PROJECT"] = self.langchain_project
        if self.langchain_endpoint:
            os.environ["LANGCHAIN_ENDPOINT"] = self.langchain_endpoint


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    s = Settings()
    s.export_langsmith_env()
    return s
