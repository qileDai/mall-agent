"""Payment consultation specialist: hybrid RAG + multi-turn clarification."""

from __future__ import annotations

import logging
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.core.context import recent_dialogue_snippet
from app.graph.state import GraphState
from app.services.llm import get_chat_model
from app.tools.rag_tool import rag_hybrid_search_tool

logger = logging.getLogger(__name__)


def _user_prompt(messages: Sequence[BaseMessage]) -> str:
    """Latest user message plus optional short prior context."""
    last = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            last = str(m.content)
            break
    snippet = recent_dialogue_snippet(messages, max_turns=1)
    if snippet and snippet.count("\n") > 0:
        return f"{snippet}\n\n当前问题: {last}"
    return last


async def run_payment_agent(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """
    Execute payment FAQ / policy reasoning with hybrid RAG tool access.

    Args:
        state: Current LangGraph state including ``messages`` and ``user_context``.
        config: Optional LangChain runnable config (propagates tags for LangSmith).

    Returns:
        Partial state update for ``agent_outputs`` and ``stream_events``.
    """
    if "payment" not in state.get("sub_tasks", []):
        return {}

    events: list[dict[str, Any]] = []
    user_q = _user_prompt(state["messages"])
    sys = SystemMessage(
        content=(
            "支付咨询专家。先 rag_hybrid_search，再中文简洁答复；无依据则说明，勿编造。"
        )
    )
    llm = get_chat_model().bind_tools([rag_hybrid_search_tool])
    turn_msgs: list[BaseMessage] = [sys, HumanMessage(content=user_q)]
    last_ai: AIMessage | None = None
    for _ in range(3):
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

    out = ""
    if last_ai and last_ai.content and not last_ai.tool_calls:
        out = str(last_ai.content).strip()
    else:
        final_ai: AIMessage = await get_chat_model().ainvoke(
            turn_msgs
            + [SystemMessage(content="基于工具结果用中文给出简短最终答复。")],
            config=config or RunnableConfig(tags=["payment_agent_final"]),
        )
        out = str(final_ai.content).strip()

    return {
        "agent_outputs": {"payment": {"summary": out, "used_rag": True}},
        "stream_events": events + [{"type": "thinking", "agent": "payment", "detail": "done"}],
    }
