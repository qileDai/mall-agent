"""Tests for keyword fast-path routing."""

from __future__ import annotations

from app.core.fast_route import try_fast_route


def test_human_handoff_keywords():
    result = try_fast_route("我要转人工客服")
    assert result is not None
    assert result["needs_human"] is True
    assert result["sub_tasks"] == []


def test_payment_refund():
    result = try_fast_route("退款多久到账")
    assert result is not None
    assert result["sub_tasks"] == ["payment"]
    assert result["task_type"] == "payment"
    assert result["needs_human"] is False


def test_mixed_payment_and_risk():
    result = try_fast_route("支付被风控拦截了")
    assert result is not None
    assert "payment" in result["sub_tasks"]
    assert "risk" in result["sub_tasks"]
    assert result["task_type"] == "mixed"


def test_chitchat_greeting():
    result = try_fast_route("你好")
    assert result is not None
    assert result["task_type"] == "unknown"
    assert result["sub_tasks"] == []
    assert result.get("direct_reply")


def test_empty_returns_none():
    assert try_fast_route("") is None
    assert try_fast_route("   ") is None


def test_unmatched_falls_through():
    assert try_fast_route("今天天气怎么样") is None
