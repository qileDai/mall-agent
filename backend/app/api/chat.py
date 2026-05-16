"""Chat HTTP + SSE endpoints (streaming supervisor graph)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from langchain_core.messages import BaseMessage, HumanMessage, message_to_dict, messages_from_dict
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.db.redis import rate_limit_allow, session_get_json, session_set_json
from app.graph.state import GraphState
from app.graph.supervisor import compile_supervisor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_graph = None

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _get_graph():
    """Lazily compile LangGraph supervisor (singleton)."""
    global _graph
    if _graph is None:
        _graph = compile_supervisor()
    return _graph


def _sse(event: str, data: dict[str, Any]) -> str:
    """Format one Server-Sent Events frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _messages_to_store(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Serialize LangChain messages for Redis JSON storage."""
    return [message_to_dict(m) for m in messages]


def _messages_from_store(rows: list[dict[str, Any]]) -> list[BaseMessage]:
    """Deserialize LangChain messages from Redis JSON."""
    if not rows:
        return []
    return list(messages_from_dict(rows))


def _chunk_text(text: str, size: int = 24) -> list[str]:
    """Split text into small chunks for smoother SSE typing effect."""
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _emit_graph_thinking(
    final_state: dict[str, Any],
    seen_events: int,
) -> tuple[list[str], int]:
    """
    Build SSE frames for new ``stream_events`` since last index.

    Returns:
        Tuple of (sse frame strings, new seen_events count).
    """
    frames: list[str] = []
    evs = final_state.get("stream_events") or []
    for ev in evs[seen_events:]:
        et = str(ev.get("type", "thinking"))
        if et not in {"thinking", "tool_call", "token", "error", "done"}:
            et = "thinking"
        frames.append(_sse(et, ev))
    return frames, len(evs)


class ChatStreamRequest(BaseModel):
    """Inbound chat payload for SSE streaming."""

    message: str = Field(min_length=1, max_length=8000, description="End-user utterance.")
    session_id: str | None = Field(default=None, description="Stable client session id.")


@router.post("/stream")
async def chat_stream(request: Request, body: ChatStreamRequest) -> StreamingResponse:
    """
    Stream assistant progress and answer as SSE.

    Event types:
        - ``thinking``: graph step / agent reasoning breadcrumb
        - ``tool_call``: structured tool invocation metadata
        - ``token``: answer fragments for typewriter UI
        - ``error``: non-fatal or fatal issues
        - ``done``: terminal marker with ``session_id``
    """
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    if not await rate_limit_allow(f"rl:chat:{client_ip}", limit=60, window_seconds=60):
        raise HTTPException(status_code=429, detail="rate limited")

    session_id = body.session_id or str(uuid.uuid4())
    key = f"cs:session:{session_id}"
    stored = await session_get_json(key) or {}
    prior_msgs = _messages_from_store(stored.get("messages", []))
    user_ctx = dict(stored.get("user_context", {}))

    msgs = prior_msgs + [HumanMessage(content=body.message)]
    init: GraphState = {
        "messages": msgs,
        "session_id": session_id,
        "user_context": user_ctx,
        "stream_events": [],
    }

    async def gen() -> AsyncIterator[str]:
        graph = _get_graph()
        final_state: dict[str, Any] = dict(init)
        seen_events = 0
        streamed_answer = False
        try:
            yield _sse(
                "thinking",
                {
                    "agent": "system",
                    "detail": "已开始处理；若中转站限流，OpenAI SDK 会自动重试，请稍候…",
                },
            )

            async for mode, payload in graph.astream(
                init,
                config={"run_name": "chat_turn", "metadata": {"session_id": session_id}},
                stream_mode=["values", "custom"],
            ):
                if mode == "custom" and isinstance(payload, dict):
                    if payload.get("sse_event") == "token":
                        piece = str(payload.get("text", ""))
                        if piece:
                            streamed_answer = True
                            yield _sse("token", {"text": piece})
                    continue

                if mode != "values" or not isinstance(payload, dict):
                    continue

                final_state = dict(payload)
                frames, seen_events = _emit_graph_thinking(final_state, seen_events)
                for frame in frames:
                    yield frame

            text = str(final_state.get("final_response") or "").strip()
            if not text and not streamed_answer:
                yield _sse("error", {"message": "empty final_response"})
            elif text and not streamed_answer:
                for piece in _chunk_text(text):
                    yield _sse("token", {"text": piece})
                    await asyncio.sleep(0.005)
            to_save = {
                "messages": _messages_to_store(final_state.get("messages", msgs)),
                "user_context": dict(final_state.get("user_context") or user_ctx),
            }
            await session_set_json(key, to_save, settings.session_ttl_seconds)
            yield _sse("done", {"session_id": session_id})
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat_stream failed")
            yield _sse("error", {"message": str(exc)})
            yield _sse("done", {"session_id": session_id, "error": True})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.websocket("/ws")
async def chat_ws_placeholder(websocket: WebSocket) -> None:
    """
    WebSocket placeholder for future real-time features (typing indicators, push).

    Current behavior: accepts connection, sends a hello frame, echoes text messages.

    TODO: unify with graph lifecycle + auth.
    """
    await websocket.accept()
    await websocket.send_json({"type": "hello", "detail": "reserved channel; primary transport is SSE"})
    try:
        while True:
            msg = await websocket.receive_text()
            await websocket.send_json({"type": "echo", "body": msg})
    except WebSocketDisconnect:
        return
