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
    """
    Classify the latest user intent and populate routing fields.

    Args:
        state: Current graph state including ``messages``.
        config: Optional runnable config for tracing.

    Returns:
        Partial state with ``task_type``, ``sub_tasks``, ``confidence``,
        ``needs_human``, ``user_context`` enrichments, and ``stream_events``.
    """
    structured = get_chat_model().with_structured_output(IntentSchema)
    last = ""
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            last = str(m.content)
            break
    sys = SystemMessage(
        content=(
            "你是客服调度中枢。根据用户最新输入，选择 task_type 与 sub_tasks。"
            "sub_tasks 只能包含 payment/risk/wallet 之一或多个。"
            "涉及退款/支付失败/手续费走 payment；实名/风控/拦截走 risk；"
            "余额/账单/凭证走 wallet；跨域问题用 mixed。"
            "若用户要求转人工、辱骂、或完全无关且无法服务，needs_human=true。"
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
    events = [
        {
            "type": "thinking",
            "agent": "supervisor",
            "detail": f"classify intent={intent.task_type} sub={sub} conf={intent.confidence}",
        }
    ]
    return {
        "task_type": intent.task_type,
        "sub_tasks": sub,
        "confidence": intent.confidence,
        "needs_human": intent.needs_human,
        "user_context": ctx,
        "stream_events": events,
        "tool_failure_streak": 0,
    }


async def dispatch_node(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """
    Normalise ``sub_tasks`` ordering for deterministic specialist execution.

    Args:
        state: Graph state.
        config: Runnable config.

    Returns:
        Partial state update (ordering + thinking event).

    Notes:
        Specialists are wired **serially** (payment → risk → wallet) to avoid
        ambiguous fan-in while still allowing each node to no-op when inactive.
        TODO: replace with ``Send`` parallel fan-out + join when scale requires it.
    """
    order = ["payment", "risk", "wallet"]
    sub = [t for t in order if t in set(state.get("sub_tasks", []))]
    return {
        "sub_tasks": sub,
        "stream_events": [
            {
                "type": "thinking",
                "agent": "supervisor",
                "detail": f"dispatch serial order={sub}",
            }
        ],
    }


async def aggregate_node(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """
    Merge specialist outputs into a single user-facing Chinese reply.

    Args:
        state: Graph state after specialists.
        config: Runnable config.

    Returns:
        Partial state with ``final_response`` and optional ``needs_human`` escalation.
    """
    outputs = state.get("agent_outputs") or {}
    sys = SystemMessage(
        content=(
            "你是客服总线，请将各专家 JSON 输出整合为一段连贯中文答复。"
            "不要暴露内部 JSON；若存在冲突，以风控结论优先。"
            "如需要用户补充材料，清楚列出。"
        )
    )
    human = HumanMessage(content=json.dumps({"agent_outputs": outputs}, ensure_ascii=False))
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
    if not needs:
        updates["messages"] = [AIMessage(content=text)]
    return updates


async def human_handoff_node(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """
    Prepare a handoff message with compact transcript context for human agents.

    Args:
        state: Graph state.
        config: Runnable config.

    Returns:
        Partial state with ``final_response`` updated for user visibility.
    """
    tail = []
    for m in state["messages"][-8:]:
        role = m.type if hasattr(m, "type") else m.__class__.__name__
        tail.append(f"{role}: {m.content}")
    transcript = "\n".join(tail)
    base = state.get("final_response") or "我们已为你连接人工客服，请稍候。"
    msg = (
        base
        + "\n\n---\n已为你创建人工工单，以下为上下文摘要：\n"
        + transcript[:2000]
        + "\n（TODO：推送到工单系统/IM）"
    )
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


def route_after_aggregate(state: GraphState) -> Any:
    """Escalate to human if aggregate flagged ``needs_human``."""
    if state.get("needs_human"):
        return "human_handoff"
    return END


def build_supervisor_graph() -> StateGraph:
    """
    Build and return the uncompiled ``StateGraph`` for extension/testing.

    Returns:
        StateGraph instance ready to be compiled.

    Notes:
        Compile with ``.compile()`` in callers to attach checkpointers if needed.
    """
    g = StateGraph(GraphState)
    g.add_node("classify_intent", classify_intent)
    g.add_node("dispatch", dispatch_node)
    g.add_node("payment_agent", run_payment_agent)
    g.add_node("risk_agent", run_risk_agent)
    g.add_node("wallet_agent", run_wallet_agent)
    g.add_node("aggregate", aggregate_node)
    g.add_node("human_handoff", human_handoff_node)

    g.add_edge(START, "classify_intent")
    g.add_conditional_edges(
        "classify_intent",
        route_after_classify,
        {
            "human_handoff": "human_handoff",
            "dispatch": "dispatch",
        },
    )
    g.add_edge("dispatch", "payment_agent")
    g.add_edge("payment_agent", "risk_agent")
    g.add_edge("risk_agent", "wallet_agent")
    g.add_edge("wallet_agent", "aggregate")
    g.add_conditional_edges(
        "aggregate",
        route_after_aggregate,
        {
            "human_handoff": "human_handoff",
            END: END,
        },
    )
    g.add_edge("human_handoff", END)
    return g


def compile_supervisor():
    """Compile the supervisor graph with default LangGraph runtime settings."""
    return build_supervisor_graph().compile()
