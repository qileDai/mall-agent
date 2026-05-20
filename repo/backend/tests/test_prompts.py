"""Tests for prompt helpers."""

from __future__ import annotations

from app.core.prompts import payment_rag_human, risk_adjudication_human


def test_payment_rag_human_includes_sections():
    body = payment_rag_human("退款多久", '{"hits":[]}')
    assert "【用户问题】" in body
    assert "退款多久" in body
    assert "【知识库片段】" in body


def test_risk_adjudication_human_wrapper():
    body = risk_adjudication_human('{"assessment":{}}')
    assert body.startswith("【案件输入】")
