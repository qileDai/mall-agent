"""LangGraph shared state definition for the customer-service supervisor."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

import operator
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_agent_outputs(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Shallow-merge agent output blobs from parallel/sequential node updates.

    Args:
        left: Previous merged outputs (may be None on first write).
        right: New partial outputs from a graph node.

    Returns:
        Combined dictionary.
    """
    base = dict(left or {})
    base.update(right or {})
    return base


def merge_user_context(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge per-turn user/session context updates from multiple agents."""
    base = dict(left or {})
    base.update(right or {})
    return base


class UserContext(TypedDict, total=False):
    """Arbitrary session-scoped fields shared between agents."""

    user_id: str
    last_transaction_id: str
    kyc_status: Literal["verified", "pending", "blocked", "unknown"]
    risk_pending_question: str
    risk_last_decision: Literal["approve", "need_docs", "reject", ""]
    wallet_last_otp_hint: str
    amount_cents: int


class GraphState(TypedDict, total=False):
    """
    Conversation and routing state flowing through the supervisor graph.

    Notes:
        ``messages`` uses LangGraph's ``add_messages`` reducer for safe appends.
        ``agent_outputs`` merges partial dicts from specialist nodes.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    task_type: Literal["payment", "risk", "wallet", "mixed", "unknown", ""]
    sub_tasks: list[str]
    agent_outputs: Annotated[dict[str, Any], merge_agent_outputs]
    final_response: str
    confidence: float
    needs_human: bool
    user_context: Annotated[UserContext, merge_user_context]
    tool_failure_streak: int
    session_id: str
    stream_events: Annotated[list[dict[str, Any]], operator.add]
