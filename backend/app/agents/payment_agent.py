"""Payment consultation specialist: hybrid RAG + multi-turn clarification."""

from __future__ import annotations

import logging
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.graph.state import GraphState
from app.services.llm import get_chat_model
from app.tools.rag_tool import rag_hybrid_search_tool

logger = logging.getLogger(__name__)


def _last_user_text(messages: Sequence[BaseMessage]) -> str:
    """Return the latest human utterance or empty string."""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


async def run_payment_agent(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """
    Execute payment FAQ / policy reasoning with hybrid RAG tool access.

    Args:
        state: Current LangGraph state including ``messages`` and ``user_context``.
        config: Optional LangChain runnable config (propagates tags for LangSmith).

    Returns:
        Partial state update for ``agent_outputs`` and ``stream_events``.

    Notes:
        Implements a short tool-calling loop (manual ReAct) capped at 3 rounds.
        Serial graph wiring avoids ambiguous fan-in; specialists no-op when not
        listed in ``sub_tasks``.
    """
    if "payment" not in state.get("sub_tasks", []):
        return {}

    user_q = _last_user_text(state["messages"])
    sys = SystemMessage(
        content=(
            "你是支付咨询专家。优先调用 rag_hybrid_search 获取内部知识，再结合用户语境用中文回答。"
            "若知识不足，明确说明并给出安全建议。不要编造监管条款。"
        )
    )
    llm = get_chat_model().bind_tools([rag_hybrid_search_tool])
    turn_msgs: list[BaseMessage] = [sys, HumanMessage(content=user_q)]
    for _ in range(3):
        ai: AIMessage = await llm.ainvoke(
            turn_msgs,
            config=config or RunnableConfig(tags=["payment_agent"]),
        )
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
    final_ai: AIMessage = await get_chat_model().ainvoke(
        turn_msgs
        + [
            SystemMessage(
                content="请基于工具结果给出最终简洁答复（中文），引用要点但不暴露 JSON 原文。"
            )
        ],
        config=config or RunnableConfig(tags=["payment_agent_final"]),
    )
    out = str(final_ai.content)
    return {
        "agent_outputs": {"payment": {"summary": out, "used_rag": True}},
        "stream_events": events
        + [{"type": "thinking", "agent": "payment", "detail": "payment agent completed"}],
    }
