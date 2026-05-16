"""Wallet operations specialist with mock APIs, permission checks, and OTP step-up."""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.graph.state import GraphState
from app.services.llm import get_chat_model
from app.tools.wallet_api import wallet_balance_tool, wallet_bills_tool, wallet_export_voucher_tool

logger = logging.getLogger(__name__)

_otp_re = re.compile(r"\b(\d{6})\b")


def _last_user_text(messages: Sequence[BaseMessage]) -> str:
    """Return the latest human utterance or empty string."""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


async def run_wallet_agent(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """
    Execute wallet queries with permission checks tied to risk ``kyc_status``.

    Sensitive exports require simulated OTP ``123456`` (or any 6 digits passed
    through to the mock tool — demo accepts ``123456`` only).

    Args:
        state: LangGraph state after risk classification.
        config: Runnable config for tracing.

    Returns:
        Partial updates for ``agent_outputs`` and ``stream_events``.

    TODO: Integrate hardware token / SMS OTP and signed download URLs.
    """
    if "wallet" not in state.get("sub_tasks", []):
        return {}

    ctx = dict(state.get("user_context") or {})
    kyc = ctx.get("kyc_status", "unknown")
    events: list[dict[str, Any]] = []
    if kyc == "blocked":
        msg = "账户处于风控冻结状态，暂无法提供钱包操作。请联系人工客服。"
        events.append({"type": "thinking", "agent": "wallet", "detail": "blocked by kyc_status"})
        return {
            "agent_outputs": {"wallet": {"error": "kyc_blocked", "message": msg}},
            "stream_events": events,
        }

    user_id = ctx.get("user_id") or "demo-user"
    txn = ctx.get("last_transaction_id") or "demo-txn"
    user_text = _last_user_text(state["messages"])
    otp_match = _otp_re.search(user_text)
    otp = otp_match.group(1) if otp_match else ""

    sys = SystemMessage(
        content=(
            "你是钱包助手，可调用工具：wallet_balance / wallet_bills / wallet_export_voucher。"
            "仅在用户明确要凭证且语境允许时调用导出；导出需要 OTP。"
            f"当前用户 {user_id}，默认交易号 {txn}。若用户未提供 OTP，先提示使用演示 OTP 123456。"
        )
    )
    llm = get_chat_model().bind_tools(
        [wallet_balance_tool, wallet_bills_tool, wallet_export_voucher_tool]
    )
    turn_msgs: list[BaseMessage] = [
        sys,
        HumanMessage(content=user_text),
    ]
    for _ in range(3):
        ai: AIMessage = await llm.ainvoke(
            turn_msgs,
            config=config or RunnableConfig(tags=["wallet_agent"]),
        )
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
                    ToolMessage(content="unknown tool", tool_call_id=call["id"])
                )
                continue
            try:
                payload = await tool_fn.ainvoke(args)
                turn_msgs.append(ToolMessage(content=payload, tool_call_id=call["id"]))
            except Exception as exc:  # noqa: BLE001
                logger.exception("wallet tool failed")
                turn_msgs.append(ToolMessage(content=f"tool error: {exc}", tool_call_id=call["id"]))

    final_ai: AIMessage = await get_chat_model().ainvoke(
        turn_msgs + [SystemMessage(content="用中文给出最终答复，简洁礼貌。")],
        config=config or RunnableConfig(tags=["wallet_agent_final"]),
    )
    ctx["wallet_last_otp_hint"] = "演示环境请使用 OTP：123456"
    return {
        "agent_outputs": {"wallet": {"summary": str(final_ai.content)}},
        "user_context": ctx,
        "stream_events": events + [{"type": "thinking", "agent": "wallet", "detail": "wallet agent done"}],
    }
