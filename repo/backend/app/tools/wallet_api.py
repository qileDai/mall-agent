"""Mock wallet APIs and LangChain Tool wrappers."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class BalanceInput(BaseModel):
    """Query wallet balance."""

    user_id: str = Field(description="Internal user id.")


class BillsInput(BaseModel):
    """Fetch recent wallet bills."""

    user_id: str = Field(description="Internal user id.")
    limit: int = Field(default=5, ge=1, le=50, description="Max rows.")


class ExportVoucherInput(BaseModel):
    """Export transaction voucher (sensitive)."""

    user_id: str = Field(description="Internal user id.")
    transaction_id: str = Field(description="Wallet ledger transaction id.")
    otp_code: str = Field(default="", description="Simulated OTP for step-up auth.")


def _hash_amount(user_id: str) -> float:
    """Derive a demo balance from user id."""
    h = int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16)
    return round((h % 1_000_000) / 100.0, 2)


async def wallet_get_balance(user_id: str) -> dict[str, Any]:
    """
    Mock: return wallet balance.

    Args:
        user_id: User id.

    Returns:
        Balance payload.

    TODO: Replace with signed internal wallet service.
    """
    return {"user_id": user_id, "currency": "CNY", "available": _hash_amount(user_id)}


async def wallet_list_bills(user_id: str, limit: int = 5) -> dict[str, Any]:
    """
    Mock: return synthetic bill rows.

    Args:
        user_id: User id.
        limit: Row cap.

    Returns:
        List under key `items`.
    """
    items = []
    for i in range(limit):
        items.append(
            {
                "id": f"bill-{user_id}-{i}",
                "amount": round(10 + i * 3.5, 2),
                "type": "credit" if i % 2 == 0 else "debit",
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
    return {"user_id": user_id, "items": items}


async def wallet_export_voucher(
    user_id: str,
    transaction_id: str,
    otp_code: str,
) -> dict[str, Any]:
    """
    Mock sensitive export with OTP gate.

    Args:
        user_id: User id.
        transaction_id: Ledger id.
        otp_code: Demo OTP; accept ``123456``.

    Returns:
        Voucher metadata or error dict.

    TODO: Integrate real OTP provider + object storage presign URL.
    """
    if otp_code != "123456":
        return {"status": "otp_required", "message": "二次验证失败：请输入模拟 OTP 123456"}
    return {
        "status": "ok",
        "user_id": user_id,
        "transaction_id": transaction_id,
        "download_url": f"https://mock-cdn.example/vouchers/{transaction_id}.pdf",
        "expires_at": datetime.now(timezone.utc).isoformat(),
    }


@tool("wallet_balance", args_schema=BalanceInput)
async def wallet_balance_tool(user_id: str) -> str:
    """Tool: query mock wallet balance."""
    import json

    return json.dumps(await wallet_get_balance(user_id), ensure_ascii=False)


@tool("wallet_bills", args_schema=BillsInput)
async def wallet_bills_tool(user_id: str, limit: int = 5) -> str:
    """Tool: list mock bills."""
    import json

    return json.dumps(await wallet_list_bills(user_id, limit), ensure_ascii=False)


@tool("wallet_export_voucher", args_schema=ExportVoucherInput)
async def wallet_export_voucher_tool(user_id: str, transaction_id: str, otp_code: str = "") -> str:
    """Tool: export voucher with OTP step-up (mock)."""
    import json

    return json.dumps(await wallet_export_voucher(user_id, transaction_id, otp_code), ensure_ascii=False)
