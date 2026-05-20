"""Risk review specialist with mock APIs, autonomous tool use, and follow-up loop."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app.core.prompts import RISK_ADJUDICATION, risk_adjudication_human
from app.graph.state import GraphState
from app.services.llm import get_chat_model
from app.tools.risk_api import fetch_risk_assessment, freeze_transaction_temporarily

logger = logging.getLogger(__name__)

_RISK_ASSESSMENT_KEYS = (
    "risk_score",
    "kyc_status",
    "identity_match",
    "velocity_flag",
    "device_trust",
)


def _slim_assessment(assessment: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields needed for LLM adjudication (token savings)."""
    return {k: assessment[k] for k in _RISK_ASSESSMENT_KEYS if k in assessment}


class RiskLLMVerdict(BaseModel):
    """Structured decision emitted by the risk reasoning model."""

    decision: Literal["approve", "need_docs", "reject"] = Field(
        description="approve=通过；need_docs=需补材料；reject=拒绝/不予通过。"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="对本次裁决的置信度。")
    user_reply: str = Field(
        description="直接发给用户的中文（2–5句）：结论+依据+下一步；need_docs 时写清材料清单。",
    )
    requested_documents: list[str] = Field(
        default_factory=list,
        description="仅 need_docs：材料简称列表，如「身份证正反面」「银行卡流水」。",
    )


def _last_user_text(messages: Sequence[BaseMessage]) -> str:
    """Return the latest human utterance or empty string."""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


async def run_risk_agent(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """
    Run autonomous risk assessment using mock services and LLM adjudication.

    The agent:
        - Pulls deterministic mock risk facts.
        - Optionally triggers temporary freeze for very high scores.
        - Produces approve / need_docs / reject with user-facing wording.
        - When ``need_docs``, stores a pending question in ``user_context`` for
          the next user turn (multi-turn loop).

    Args:
        state: LangGraph state.
        config: Runnable config for tracing tags.

    Returns:
        Partial state updates for ``agent_outputs``, ``user_context``,
        ``needs_human``, and ``stream_events``.

    TODO: Replace mock assessment with signed internal RPC + case management ids.
    """
    if "risk" not in state.get("sub_tasks", []):
        return {}

    events: list[dict[str, Any]] = []
    ctx = dict(state.get("user_context") or {})
    user_id = ctx.get("user_id") or "demo-user"
    txn = ctx.get("last_transaction_id") or "demo-txn"
    user_text = _last_user_text(state["messages"])

    assessment = await fetch_risk_assessment(user_id, txn, amount_cents=ctx.get("amount_cents", 0) or 0)
    events.append(
        {
            "type": "tool_call",
            "agent": "risk",
            "name": "fetch_risk_assessment",
            "args": {"user_id": user_id, "transaction_id": txn},
            "result_preview": json.dumps(assessment, ensure_ascii=False)[:500],
        }
    )

    ctx["kyc_status"] = assessment.get("kyc_status", ctx.get("kyc_status", "unknown"))
    if assessment["risk_score"] >= 0.92:
        freeze = await freeze_transaction_temporarily(txn, reason="auto_high_risk")
        events.append({"type": "tool_call", "agent": "risk", "name": "freeze_transaction", "result": freeze})
        ctx["kyc_status"] = "blocked"

    prior_q = ctx.get("risk_pending_question", "")
    structured = get_chat_model().with_structured_output(RiskLLMVerdict)
    slim = _slim_assessment(assessment)
    sys = SystemMessage(content=RISK_ADJUDICATION)
    human = HumanMessage(
        content=risk_adjudication_human(
            json.dumps(
                {
                    "assessment": slim,
                    "user_latest": user_text[:500],
                    "prior_pending_question": (prior_q or "")[:300],
                },
                ensure_ascii=False,
            )
        )
    )
    verdict: RiskLLMVerdict = await structured.ainvoke(
        [sys, human],
        config=config or RunnableConfig(tags=["risk_agent"]),
    )
    events.append(
        {
            "type": "thinking",
            "agent": "risk",
            "detail": f"verdict={verdict.decision} conf={verdict.confidence}",
        }
    )

    ctx["risk_last_decision"] = verdict.decision
    if verdict.decision == "need_docs":
        ctx["risk_pending_question"] = verdict.user_reply
    else:
        ctx.pop("risk_pending_question", None)

    needs_human = False
    if verdict.decision == "reject" and verdict.confidence < 0.55:
        needs_human = True
    if assessment["risk_score"] >= 0.92:
        needs_human = True  # escalated path always gets human eyes in demo policy

    return {
        "agent_outputs": {
            "risk": {
                "summary": verdict.user_reply,
                "verdict": {
                    "decision": verdict.decision,
                    "confidence": verdict.confidence,
                },
            }
        },
        "user_context": ctx,
        "needs_human": bool(state.get("needs_human")) or needs_human,
        "stream_events": events,
    }
