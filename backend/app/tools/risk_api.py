"""Mock risk APIs and LangChain Tool wrappers for the risk agent."""

from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RiskLookupInput(BaseModel):
    """Arguments for fetching a synthetic risk assessment."""

    user_id: str = Field(description="Internal stable user identifier.")
    transaction_id: str = Field(description="Payment or wallet transaction id.")
    amount_cents: int = Field(default=0, description="Amount in minor currency units.")


def _deterministic_score(user_id: str, transaction_id: str) -> float:
    """Map (user, txn) to a pseudo-stable score in [0,1] for demos."""
    h = hashlib.sha256(f"{user_id}:{transaction_id}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


async def fetch_risk_assessment(
    user_id: str,
    transaction_id: str,
    amount_cents: int = 0,
) -> dict[str, Any]:
    """
    Mock internal risk service: returns score, KYC tier, velocity flags.

    Args:
        user_id: User id.
        transaction_id: Transaction id.
        amount_cents: Optional amount for thresholding.

    Returns:
        Structured risk payload (stable for the same inputs in demo mode).

    TODO: Replace with real HTTP call to risk microservice with mTLS + audit headers.
    """
    base = _deterministic_score(user_id, transaction_id)
    jitter = random.Random(f"{user_id}:{transaction_id}").uniform(-0.05, 0.05)
    score = max(0.0, min(1.0, base + jitter))
    high_amount = amount_cents >= 500_000  # 5000.00 in major units if cents
    return {
        "risk_score": round(score, 4),
        "kyc_status": "verified" if score < 0.55 else ("pending" if score < 0.8 else "blocked"),
        "identity_match": score < 0.75,
        "velocity_flag": high_amount and score > 0.45,
        "device_trust": "high" if score < 0.5 else "medium",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "transaction_id": transaction_id,
        "user_id": user_id,
    }


async def freeze_transaction_temporarily(
    transaction_id: str,
    reason: str,
    ttl_minutes: int = 30,
) -> dict[str, Any]:
    """
    Mock temporary freeze for high-risk flows; notifies human queue.

    Args:
        transaction_id: Target transaction.
        reason: Human-readable reason for audit trail.
        ttl_minutes: Auto-release window (mock).

    Returns:
        Ack payload with ticket id.

    TODO: Wire to ledger freeze API + incident webhook.
    """
    ticket = hashlib.md5(f"{transaction_id}:{reason}".encode()).hexdigest()[:12]
    logger.warning("MOCK freeze txn=%s reason=%s ticket=%s", transaction_id, reason, ticket)
    return {
        "status": "frozen",
        "transaction_id": transaction_id,
        "ticket": ticket,
        "ttl_minutes": ttl_minutes,
        "human_notified": True,
    }


@tool("risk_lookup", args_schema=RiskLookupInput)
async def risk_lookup_tool(user_id: str, transaction_id: str, amount_cents: int = 0) -> str:
    """
    Tool: fetch synthetic risk assessment for a user/transaction pair.

    Returns:
        JSON string for LLM consumption.
    """
    import json

    data = await fetch_risk_assessment(user_id, transaction_id, amount_cents)
    return json.dumps(data, ensure_ascii=False)


class FreezeInput(BaseModel):
    """Arguments for temporary freeze."""

    transaction_id: str = Field(description="Transaction to freeze.")
    reason: str = Field(description="Short reason code or free text for audit.")


@tool("risk_freeze_transaction", args_schema=FreezeInput)
async def risk_freeze_transaction_tool(transaction_id: str, reason: str) -> str:
    """
    Tool: trigger temporary freeze (mock) for escalated risk.

    Returns:
        JSON string ack.
    """
    import json

    data = await freeze_transaction_temporarily(transaction_id, reason)
    return json.dumps(data, ensure_ascii=False)
