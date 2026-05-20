"""Payment consultation specialist: hybrid RAG + multi-turn clarification."""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.core.config import get_settings
from app.core.prompts import PAYMENT_DIRECT_RAG, PAYMENT_FINAL, PAYMENT_TOOL_LOOP, payment_rag_human
from app.core.context import compact_rag_hits, recent_dialogue_snippet
from app.graph.state import GraphState
from app.services.llm import get_chat_model
from app.tools.rag_tool import hybrid_search, rag_hybrid_search_tool

logger = logging.getLogger(__name__)


def _user_prompt(messages: Sequence[BaseMessage]) -> str:
    """Latest user message plus optional short prior context."""
    last = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            last = str(m.content)
            break
    snippet = recent_dialogue_snippet(messages, max_turns=1)
    if snippet and "\n" in snippet:
        return f"{snippet}\n\n当前问题: {last}"
    return last


async def _payment_direct_rag(
    user_q: str,
    config: RunnableConfig | None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Fast path: retrieve once, answer once (no tool-calling loop).

    Returns:
        Tuple of (answer text, stream events).
    """
    settings = get_settings()
    events: list[dict[str, Any]] = [
        {
            "type": "tool_call",
            "agent": "payment",
            "name": "rag_hybrid_search",
            "args": {"query": user_q, "top_k": settings.rag_top_k},
        }
    ]
    hits = await hybrid_search(user_q, top_k=settings.rag_top_k)
    compact = compact_rag_hits(hits, settings.rag_chunk_max_chars)
    kb = json.dumps({"hits": compact}, ensure_ascii=False)
    ai: AIMessage = await get_chat_model().ainvoke(
        [
            SystemMessage(content=PAYMENT_DIRECT_RAG),
            HumanMessage(content=payment_rag_human(user_q, kb)),
        ],
        config=config or RunnableConfig(tags=["payment_agent_direct_rag"]),
    )
    return str(ai.content).strip(), events


async def run_payment_agent(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """
    Execute payment FAQ / policy reasoning with hybrid RAG.

    Uses direct RAG + single LLM when ``PAYMENT_DIRECT_RAG=true`` (default);
    otherwise falls back to a short tool-calling loop.
    """
    if "payment" not in state.get("sub_tasks", []):
        return {}

    settings = get_settings()
    user_q = _user_prompt(state["messages"])
    events: list[dict[str, Any]] = []

    if settings.payment_direct_rag:
        out, events = await _payment_direct_rag(user_q, config)
        return {
            "agent_outputs": {"payment": {"summary": out, "used_rag": True, "mode": "direct_rag"}},
            "stream_events": events + [{"type": "thinking", "agent": "payment", "detail": "direct_rag done"}],
        }

    sys = SystemMessage(content="支付咨询专家。先 rag_hybrid_search，再中文简洁答复；无依据则说明。")
    llm = get_chat_model().bind_tools([rag_hybrid_search_tool])
    turn_msgs: list[BaseMessage] = [sys, HumanMessage(content=user_q)]
    last_ai: AIMessage | None = None
    max_rounds = max(1, settings.payment_max_tool_rounds)
    for _ in range(max_rounds):
        ai: AIMessage = await llm.ainvoke(
            turn_msgs,
            config=config or RunnableConfig(tags=["payment_agent"]),
        )
        last_ai = ai
        turn_msgs.append(ai)
        if not ai.tool_calls:
            break
        for call in ai.tool_calls:
            name = call.get("name")
            args = call.get("args") or {}
            events.append({"type": "tool_call", "agent": "payment", "name": name, "args": args})
            if name != "rag_hybrid_search":
                turn_msgs.append(
                    ToolMessage(content="unsupported tool", tool_call_id=str(call.get("id") or "call"))
                )
                continue
            try:
                payload = await rag_hybrid_search_tool.ainvoke(args)
                turn_msgs.append(ToolMessage(content=payload, tool_call_id=str(call.get("id") or "call")))
            except Exception as exc:  # noqa: BLE001
                logger.exception("rag tool failed")
                turn_msgs.append(
                    ToolMessage(content=f"tool error: {exc}", tool_call_id=str(call.get("id") or "call"))
                )

    if last_ai and last_ai.content and not last_ai.tool_calls:
        out = str(last_ai.content).strip()
    else:
        final_ai: AIMessage = await get_chat_model().ainvoke(
            turn_msgs + [SystemMessage(content=PAYMENT_FINAL)],
            config=config or RunnableConfig(tags=["payment_agent_final"]),
        )
        out = str(final_ai.content).strip()

    return {
        "agent_outputs": {"payment": {"summary": out, "used_rag": True, "mode": "tool_loop"}},
        "stream_events": events + [{"type": "thinking", "agent": "payment", "detail": "done"}],
    }
