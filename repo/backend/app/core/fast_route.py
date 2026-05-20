"""Keyword fast-path intent routing (skip LLM classify when confident)."""

from __future__ import annotations

import re
from typing import Any

from app.core.prompts import CHITCHAT_REPLY

_HUMAN = ("转人工", "人工客服", "真人客服", "投诉升级")
_CHITCHAT_RE = re.compile(
    r"^(你好|您好|hi|hello|谢谢|感谢|多谢|在吗|早上好|下午好|晚上好)[\!！\.。~\s]*$",
    re.IGNORECASE,
)
_PAYMENT = ("退款", "支付", "扣款", "到账", "手续费", "原路退回", "调单", "重复扣")
_RISK = ("风控", "审核", "拦截", "实名", "kyc", "风险", "冻结", "材料")
_WALLET = ("余额", "账单", "钱包", "otp", "凭证", "充值", "导出")


def try_fast_route(user_text: str) -> dict[str, Any] | None:
    """
    Return a partial graph update when keyword rules are confident enough.

    Args:
        user_text: Latest user message.

    Returns:
        Dict with ``task_type``, ``sub_tasks``, ``confidence``, ``needs_human``,
        or ``None`` to fall back to LLM classification.
    """
    text = user_text.strip()
    if not text:
        return None
    lower = text.lower()

    if _CHITCHAT_RE.match(text):
        return {
            "task_type": "unknown",
            "sub_tasks": [],
            "confidence": 0.95,
            "needs_human": False,
            "direct_reply": CHITCHAT_REPLY,
            "stream_events": [
                {
                    "type": "thinking",
                    "agent": "supervisor",
                    "detail": "fast_route: chitchat greeting",
                }
            ],
        }

    if any(k in text for k in _HUMAN):
        return {
            "task_type": "unknown",
            "sub_tasks": [],
            "confidence": 0.9,
            "needs_human": True,
            "stream_events": [
                {
                    "type": "thinking",
                    "agent": "supervisor",
                    "detail": "fast_route: human_handoff keywords",
                }
            ],
        }

    hits: list[str] = []
    if any(k in text for k in _PAYMENT):
        hits.append("payment")
    if any(k in text for k in _RISK):
        hits.append("risk")
    if any(k in text for k in _WALLET):
        hits.append("wallet")

    # English txn hint still useful for risk/wallet context
    if re.search(r"\b(txn|ord)[-_]?\w+", lower):
        if "risk" not in hits and any(k in text for k in ("审核", "风控", "拦截")):
            hits.append("risk")

    if not hits:
        return None

    hits = list(dict.fromkeys(hits))
    task_type = hits[0] if len(hits) == 1 else "mixed"
    return {
        "task_type": task_type,
        "sub_tasks": hits,
        "confidence": 0.88 if len(hits) == 1 else 0.75,
        "needs_human": False,
        "stream_events": [
            {
                "type": "thinking",
                "agent": "supervisor",
                "detail": f"fast_route: sub={hits} (no LLM classify)",
            }
        ],
    }
