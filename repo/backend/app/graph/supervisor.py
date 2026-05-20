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
from app.core.fast_route import try_fast_route
from app.core.routing import normalize_sub_tasks
from app.core.prompts import AGGREGATE, CLASSIFY_INTENT, HUMAN_HANDOFF_SUFFIX
from app.graph.specialists import run_specialists_parallel
from app.graph.state import GraphState
from app.services.llm import get_chat_model

logger = logging.getLogger(__name__)


class IntentSchema(BaseModel):
    """Structured routing decision produced by the supervisor classifier."""

    task_type: Literal["payment", "risk", "wallet", "mixed", "unknown"] = Field(
        description="主领域。闲聊/问候无业务→unknown；仅一个领域→对应值；两个及以上→mixed。"
    )
    sub_tasks: list[str] = Field(
        default_factory=list,
        description="待执行专家：payment/risk/wallet 的子集。unknown 或纯转人工时可为空。",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="0–1。意图明确≥0.85；略模糊 0.45–0.7；无法判断<0.4。",
    )
    needs_human: bool = Field(
        default=False,
        description="true=用户要人工/投诉升级，或涉安全威胁且不宜自动回复。",
    )
    rationale: str = Field(description="内部一句话理由，禁止复制到 user_reply。")


_txn_re = re.compile(r"(?:订单|交易|txn|TXN)[\s:：#-]*([A-Za-z0-9_-]{6,})", re.I)


def _last_user_text(state: GraphState) -> str:
    """Extract latest human message from state."""
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


async def classify_intent(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """Classify intent via keyword fast-path or LLM structured output."""
    last = _last_user_text(state)
    settings = get_settings()

    if settings.enable_fast_route:
        fast = try_fast_route(last)
        if fast is not None:
            ctx = dict(state.get("user_context") or {})
            m = _txn_re.search(last)
            if m:
                ctx["last_transaction_id"] = m.group(1)
            fast["user_context"] = ctx
            fast["tool_failure_streak"] = 0
            return fast

    structured = get_chat_model().with_structured_output(IntentSchema)
    sys = SystemMessage(content=CLASSIFY_INTENT)
    intent: IntentSchema = await structured.ainvoke(
        [sys, HumanMessage(content=last)],
        config=config or RunnableConfig(tags=["classify_intent"]),
    )
    sub = normalize_sub_tasks(intent.task_type, intent.sub_tasks)
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
    """Normalise ``sub_tasks`` ordering."""
    order = ["payment", "risk", "wallet"]
    sub = [t for t in order if t in set(state.get("sub_tasks", []))]
    return {
        "sub_tasks": sub,
        "stream_events": [{"type": "thinking", "agent": "supervisor", "detail": f"dispatch order={sub}"}],
    }


async def run_specialists_node(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """
    Run specialists in parallel (default) or serially when disabled in settings.

    Parallel cuts wall-clock time when multiple agents are active (e.g. payment+risk).
    """
    settings = get_settings()
    if settings.enable_parallel_specialists:
        return await run_specialists_parallel(state, config)

    merged: dict[str, Any] = {}
    for fn in (run_payment_agent, run_risk_agent, run_wallet_agent):
        part = await fn(state, config)
        if not part:
            continue
        if "agent_outputs" in part:
            merged.setdefault("agent_outputs", {}).update(part["agent_outputs"])
        if "stream_events" in part:
            merged.setdefault("stream_events", []).extend(part["stream_events"])
        if "user_context" in part:
            merged.setdefault("user_context", {}).update(part["user_context"])
        if part.get("needs_human"):
            merged["needs_human"] = True
    return merged


async def finalize_direct_node(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """Use specialist summary or direct_reply as final reply (skips aggregate LLM)."""
    preset = (state.get("direct_reply") or "").strip()
    if preset:
        text = preset
        writer = get_stream_writer()
        for i in range(0, len(text), 48):
            writer({"sse_event": "token", "text": text[i : i + 48]})
        return {
            "final_response": text,
            "messages": [AIMessage(content=text)],
            "stream_events": [
                {"type": "thinking", "agent": "supervisor", "detail": "finalize_direct (direct_reply)"}
            ],
        }

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
            {"type": "thinking", "agent": "supervisor", "detail": "finalize_direct (skipped aggregate)"}
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
    sys = SystemMessage(content=AGGREGATE)
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
    """Prepare handoff message with compact transcript."""
    tail = []
    for m in state["messages"][-6:]:
        role = m.type if hasattr(m, "type") else m.__class__.__name__
        tail.append(f"{role}: {str(m.content)[:400]}")
    transcript = "\n".join(tail)
    base = state.get("final_response") or HUMAN_HANDOFF_SUFFIX
    msg = base + "\n\n---\n工单上下文摘要：\n" + transcript[:1500]
    writer = get_stream_writer()
    for i in range(0, len(msg), 48):
        writer({"sse_event": "token", "text": msg[i : i + 48]})
    return {
        "final_response": msg,
        "needs_human": True,
        "messages": [AIMessage(content=msg)],
        "stream_events": [{"type": "thinking", "agent": "human_handoff", "detail": "ticket prepared"}],
    }


def route_after_classify(
    state: GraphState,
) -> Literal["human_handoff", "dispatch", "finalize_direct"]:
    """Route low-confidence, human, or chitchat before specialists."""
    if state.get("needs_human"):
        return "human_handoff"
    if float(state.get("confidence", 1.0)) < 0.35:
        return "human_handoff"
    if (state.get("direct_reply") or "").strip():
        return "finalize_direct"
    return "dispatch"


def route_after_specialists(state: GraphState) -> str:
    """After specialists, skip aggregate when a single agent already answered."""
    if should_skip_aggregate(state):
        return "finalize_direct"
    return "aggregate"


def route_after_aggregate(state: GraphState) -> Any:
    """Escalate to human if needed."""
    if state.get("needs_human"):
        return "human_handoff"
    return END


def build_supervisor_graph() -> StateGraph:
    """Build supervisor graph with parallel specialists node."""
    g = StateGraph(GraphState)
    g.add_node("classify_intent", classify_intent)
    g.add_node("dispatch", dispatch_node)
    g.add_node("specialists", run_specialists_node)
    g.add_node("finalize_direct", finalize_direct_node)
    g.add_node("aggregate", aggregate_node)
    g.add_node("human_handoff", human_handoff_node)

    g.add_edge(START, "classify_intent")
    g.add_conditional_edges(
        "classify_intent",
        route_after_classify,
        {
            "human_handoff": "human_handoff",
            "dispatch": "dispatch",
            "finalize_direct": "finalize_direct",
        },
    )
    g.add_edge("dispatch", "specialists")
    g.add_conditional_edges(
        "specialists",
        route_after_specialists,
        {"finalize_direct": "finalize_direct", "aggregate": "aggregate"},
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
    """Compile the supervisor graph."""
    return build_supervisor_graph().compile()
