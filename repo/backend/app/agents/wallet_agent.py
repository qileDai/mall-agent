"""Wallet operations specialist with mock APIs, permission checks, and OTP step-up."""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.core.context import recent_dialogue_snippet
from app.core.prompts import WALLET_FINAL, WALLET_KYC_BLOCKED, wallet_system
from app.graph.state import GraphState
from app.services.llm import get_chat_model
from app.tools.wallet_api import wallet_balance_tool, wallet_bills_tool, wallet_export_voucher_tool

logger = logging.getLogger(__name__)

_otp_re = re.compile(r"\b(\d{6})\b")


def _user_prompt(messages: Sequence[BaseMessage]) -> str:
    """Latest user message with optional one-turn context."""
    last = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            last = str(m.content)
            break
    snippet = recent_dialogue_snippet(messages, max_turns=1)
    if snippet and "\n" in snippet:
        return f"{snippet}\n\n当前问题: {last}"
    return last


async def run_wallet_agent(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """
    Execute wallet queries with permission checks tied to risk ``kyc_status``.

    Args:
        state: LangGraph state after risk classification.
        config: Runnable config for tracing.

    Returns:
        Partial updates for ``agent_outputs`` and ``stream_events``.
    """
    if "wallet" not in state.get("sub_tasks", []):
        return {}

    ctx = dict(state.get("user_context") or {})
    kyc = ctx.get("kyc_status", "unknown")
    events: list[dict[str, Any]] = []
    if kyc == "blocked":
        msg = WALLET_KYC_BLOCKED
        events.append({"type": "thinking", "agent": "wallet", "detail": "blocked by kyc_status"})
        return {
            "agent_outputs": {"wallet": {"summary": msg, "error": "kyc_blocked"}},
            "stream_events": events,
        }

    user_id = ctx.get("user_id") or "demo-user"
    txn = ctx.get("last_transaction_id") or "demo-txn"
    user_text = _user_prompt(state["messages"])
    otp_match = _otp_re.search(user_text)
    otp = otp_match.group(1) if otp_match else ""

    sys = SystemMessage(
        content=(
            f"钱包助手，工具: wallet_balance/bills/export_voucher。用户={user_id} 交易={txn}。"
            "导出需 OTP，演示 OTP 123456。"
        )
    )
    llm = get_chat_model().bind_tools(
        [wallet_balance_tool, wallet_bills_tool, wallet_export_voucher_tool]
    )
    turn_msgs: list[BaseMessage] = [sys, HumanMessage(content=user_text)]
    last_ai: AIMessage | None = None
    for _ in range(3):
        ai: AIMessage = await llm.ainvoke(
            turn_msgs,
            config=config or RunnableConfig(tags=["wallet_agent"]),
        )
        last_ai = ai
        turn_msgs.append(ai)
        if not ai.tool_calls:
            break
        for call in ai.tool_calls:
            name = call.get("name")
            args = dict(call.get("args") or {})
            events.append({"type": "tool_call", "agent": "wallet", "name": name, "args": args})
            if name == "wallet_export_voucher":
                args.setdefault("user_id", user_id)
                args.setdefault("transaction_id", txn)
                args.setdefault("otp_code", otp or args.get("otp_code", ""))
            if name == "wallet_balance":
                args.setdefault("user_id", user_id)
            if name == "wallet_bills":
                args.setdefault("user_id", user_id)
            tool_fn = {
                "wallet_balance": wallet_balance_tool,
                "wallet_bills": wallet_bills_tool,
                "wallet_export_voucher": wallet_export_voucher_tool,
            }.get(name)
            if tool_fn is None:
                turn_msgs.append(
                    ToolMessage(content="unknown tool", tool_call_id=str(call.get("id") or "call"))
                )
                continue
            try:
                payload = await tool_fn.ainvoke(args)
                turn_msgs.append(ToolMessage(content=payload, tool_call_id=str(call.get("id") or "call")))
            except Exception as exc:  # noqa: BLE001
                logger.exception("wallet tool failed")
                turn_msgs.append(
                    ToolMessage(content=f"tool error: {exc}", tool_call_id=str(call.get("id") or "call"))
                )

    if last_ai and last_ai.content and not last_ai.tool_calls:
        out = str(last_ai.content).strip()
    else:
        final_ai: AIMessage = await get_chat_model().ainvoke(
            turn_msgs + [SystemMessage(content=WALLET_FINAL)],
            config=config or RunnableConfig(tags=["wallet_agent_final"]),
        )
        out = str(final_ai.content).strip()

    ctx["wallet_last_otp_hint"] = "演示环境请使用 OTP：123456"
    return {
        "agent_outputs": {"wallet": {"summary": out}},
        "user_context": ctx,
        "stream_events": events + [{"type": "thinking", "agent": "wallet", "detail": "done"}],
    }
