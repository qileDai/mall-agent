"""LangGraph supervisor: intent routing, specialist chain, aggregation, human handoff."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agents.payment_agent import run_payment_agent
from app.agents.risk_agent import run_risk_agent
from app.agents.wallet_agent import run_wallet_agent
from app.core.config import get_settings
from app.core.context import (
    compact_agent_outputs_for_aggregate,
    extract_agent_text,
    should_skip_aggregate,
)
from app.graph.state import GraphState
from app.services.llm import get_chat_model

logger = logging.getLogger(__name__)


class IntentSchema(BaseModel):
    """Structured routing decision produced by the supervisor classifier."""

    task_type: Literal["payment", "risk", "wallet", "mixed", "unknown"] = Field(
        description="Primary business domain for the latest user message."
    )
    sub_tasks: list[str] = Field(
        default_factory=list,
        description="Specialists to invoke: subset of payment/risk/wallet.",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Routing confidence.")
    needs_human: bool = Field(
        default=False,
        description="True when user explicitly requests human or content is unsafe/ambiguous.",
    )
    rationale: str = Field(description="Short internal justification (Chinese ok).")


_txn_re = re.compile(r"(?:订单|交易|txn|TXN)[\s:：#-]*([A-Za-z0-9_-]{6,})", re.I)


async def classify_intent(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """Classify the latest user intent and populate routing fields."""
    structured = get_chat_model().with_structured_output(IntentSchema)
    last = ""
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            last = str(m.content)
            break
    sys = SystemMessage(
        content=(
            "客服调度：选 task_type 与 sub_tasks（payment/risk/wallet）。"
            "退款/支付→payment；风控→risk；余额/账单→wallet；要转人工→needs_human=true。"
            "尽量只选必要专家，避免 mixed 时全选。"
        )
    )
    intent: IntentSchema = await structured.ainvoke(
        [sys, HumanMessage(content=last)],
        config=config or RunnableConfig(tags=["classify_intent"]),
    )
    sub = [s for s in intent.sub_tasks if s in {"payment", "risk", "wallet"}]
    if intent.task_type == "mixed" and not sub:
        sub = ["payment", "risk"]
    if intent.task_type in {"payment", "risk", "wallet"} and intent.task_type not in sub:
        sub.append(intent.task_type)
    if not sub and intent.task_type == "unknown":
        sub = ["payment"]
    ctx = dict(state.get("user_context") or {})
    m = _txn_re.search(last)
    if m:
        ctx["last_transaction_id"] = m.group(1)
    return {
        "task_type": intent.task_type,
        "sub_tasks": sub,
        "confidence": intent.confidence,
        "needs_human": intent.needs_human,
        "user_context": ctx,
        "stream_events": [
            {
                "type": "thinking",
                "agent": "supervisor",
                "detail": f"classify intent={intent.task_type} sub={sub} conf={intent.confidence}",
            }
        ],
        "tool_failure_streak": 0,
    }


async def dispatch_node(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """Normalise ``sub_tasks`` ordering for deterministic specialist execution."""
    order = ["payment", "risk", "wallet"]
    sub = [t for t in order if t in set(state.get("sub_tasks", []))]
    return {
        "sub_tasks": sub,
        "stream_events": [
            {
                "type": "thinking",
                "agent": "supervisor",
                "detail": f"dispatch order={sub}",
            }
        ],
    }


async def finalize_direct_node(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """
    Use a single specialist summary as the final reply (skips aggregate LLM).

    Streams tokens via LangGraph custom writer for SSE clients.
    """
    sub = state.get("sub_tasks") or []
    outputs = state.get("agent_outputs") or {}
    text = ""
    if len(sub) == 1:
        text = extract_agent_text(sub[0], outputs.get(sub[0]) or {})
    if not text:
        for agent in sub:
            text = extract_agent_text(agent, outputs.get(agent) or {})
            if text:
                break
    text = text.strip()
    writer = get_stream_writer()
    for i in range(0, len(text), 48):
        writer({"sse_event": "token", "text": text[i : i + 48]})
    needs = bool(state.get("needs_human"))
    updates: dict[str, Any] = {
        "final_response": text,
        "stream_events": [
            {
                "type": "thinking",
                "agent": "supervisor",
                "detail": "finalize_direct (skipped aggregate LLM)",
            }
        ],
    }
    if text and not needs:
        updates["messages"] = [AIMessage(content=text)]
    return updates


async def aggregate_node(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """Merge specialist summaries into one user-facing Chinese reply."""
    settings = get_settings()
    outputs = state.get("agent_outputs") or {}
    compact = compact_agent_outputs_for_aggregate(
        outputs,
        settings.aggregate_summary_max_chars,
    )
    sys = SystemMessage(content="整合以下专家摘要为一段中文客服回复；冲突以 risk 为准；勿暴露 JSON。")
    human = HumanMessage(content=json.dumps(compact, ensure_ascii=False))
    llm = get_chat_model()
    writer = get_stream_writer()
    text_parts: list[str] = []
    async for chunk in llm.astream(
        [sys, human],
        config=config or RunnableConfig(tags=["aggregate", "final_reply"]),
    ):
        piece = chunk.content if isinstance(chunk.content, str) else ""
        if not piece and chunk.content:
            piece = str(chunk.content)
        if piece:
            text_parts.append(piece)
            writer({"sse_event": "token", "text": piece})
    text = "".join(text_parts).strip()
    needs = bool(state.get("needs_human"))
    updates: dict[str, Any] = {
        "final_response": text,
        "needs_human": needs,
        "stream_events": [{"type": "thinking", "agent": "supervisor", "detail": "aggregate complete"}],
    }
    if text and not needs:
        updates["messages"] = [AIMessage(content=text)]
    return updates


async def human_handoff_node(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """Prepare a handoff message with compact transcript context for human agents."""
    tail = []
    for m in state["messages"][-6:]:
        role = m.type if hasattr(m, "type") else m.__class__.__name__
        tail.append(f"{role}: {str(m.content)[:400]}")
    transcript = "\n".join(tail)
    base = state.get("final_response") or "我们已为你连接人工客服，请稍候。"
    msg = (
        base
        + "\n\n---\n工单上下文摘要：\n"
        + transcript[:1500]
        + "\n（TODO：推送到工单系统/IM）"
    )
    writer = get_stream_writer()
    for i in range(0, len(msg), 48):
        writer({"sse_event": "token", "text": msg[i : i + 48]})
    return {
        "final_response": msg,
        "needs_human": True,
        "messages": [AIMessage(content=msg)],
        "stream_events": [{"type": "thinking", "agent": "human_handoff", "detail": "ticket prepared"}],
    }


def route_after_classify(state: GraphState) -> Literal["human_handoff", "dispatch"]:
    """Route low-confidence or explicit human requests before specialists."""
    if state.get("needs_human"):
        return "human_handoff"
    if float(state.get("confidence", 1.0)) < 0.35:
        return "human_handoff"
    return "dispatch"


def route_after_dispatch(state: GraphState) -> str:
    """Enter the first required specialist, or finalize if none."""
    sub = state.get("sub_tasks") or []
    if "payment" in sub:
        return "payment_agent"
    if "risk" in sub:
        return "risk_agent"
    if "wallet" in sub:
        return "wallet_agent"
    return "finalize_direct"


def route_after_payment(state: GraphState) -> str:
    """Next specialist after payment, or finish."""
    sub = set(state.get("sub_tasks") or [])
    if "risk" in sub:
        return "risk_agent"
    if "wallet" in sub:
        return "wallet_agent"
    return "finalize_direct" if should_skip_aggregate(state) else "aggregate"


def route_after_risk(state: GraphState) -> str:
    """Next specialist after risk, or finish."""
    sub = set(state.get("sub_tasks") or [])
    if "wallet" in sub:
        return "wallet_agent"
    return "finalize_direct" if should_skip_aggregate(state) else "aggregate"


def route_after_wallet(state: GraphState) -> str:
    """After wallet, aggregate or direct finalize."""
    return "finalize_direct" if should_skip_aggregate(state) else "aggregate"


def route_after_aggregate(state: GraphState) -> Any:
    """Escalate to human if aggregate flagged ``needs_human``."""
    if state.get("needs_human"):
        return "human_handoff"
    return END


def build_supervisor_graph() -> StateGraph:
    """Build and return the uncompiled ``StateGraph`` for extension/testing."""
    g = StateGraph(GraphState)
    g.add_node("classify_intent", classify_intent)
    g.add_node("dispatch", dispatch_node)
    g.add_node("payment_agent", run_payment_agent)
    g.add_node("risk_agent", run_risk_agent)
    g.add_node("wallet_agent", run_wallet_agent)
    g.add_node("finalize_direct", finalize_direct_node)
    g.add_node("aggregate", aggregate_node)
    g.add_node("human_handoff", human_handoff_node)

    g.add_edge(START, "classify_intent")
    g.add_conditional_edges(
        "classify_intent",
        route_after_classify,
        {"human_handoff": "human_handoff", "dispatch": "dispatch"},
    )
    g.add_conditional_edges(
        "dispatch",
        route_after_dispatch,
        {
            "payment_agent": "payment_agent",
            "risk_agent": "risk_agent",
            "wallet_agent": "wallet_agent",
            "finalize_direct": "finalize_direct",
        },
    )
    g.add_conditional_edges(
        "payment_agent",
        route_after_payment,
        {
            "risk_agent": "risk_agent",
            "wallet_agent": "wallet_agent",
            "aggregate": "aggregate",
            "finalize_direct": "finalize_direct",
        },
    )
    g.add_conditional_edges(
        "risk_agent",
        route_after_risk,
        {
            "wallet_agent": "wallet_agent",
            "aggregate": "aggregate",
            "finalize_direct": "finalize_direct",
        },
    )
    g.add_conditional_edges(
        "wallet_agent",
        route_after_wallet,
        {"aggregate": "aggregate", "finalize_direct": "finalize_direct"},
    )
    g.add_conditional_edges(
        "aggregate",
        route_after_aggregate,
        {"human_handoff": "human_handoff", END: END},
    )
    g.add_edge("finalize_direct", END)
    g.add_edge("human_handoff", END)
    return g


def compile_supervisor():
    """Compile the supervisor graph with default LangGraph runtime settings."""
    return build_supervisor_graph().compile()
