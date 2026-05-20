"""Tests for context helpers."""

from __future__ import annotations

from app.core.context import compact_rag_hit, should_skip_aggregate


def test_compact_rag_hit_truncates_text():
    row = {"text": "x" * 100, "metadata": {"source": "faq-refund"}}
    out = compact_rag_hit(row, max_chars=20)
    assert len(out["text"]) == 20
    assert out["source"] == "faq-refund"


def test_should_skip_aggregate_single_agent_with_summary():
    state = {
        "sub_tasks": ["payment"],
        "agent_outputs": {"payment": {"summary": "退款 3-7 个工作日到账。"}},
    }
    assert should_skip_aggregate(state) is True


def test_should_skip_aggregate_multiple_agents():
    state = {
        "sub_tasks": ["payment", "risk"],
        "agent_outputs": {
            "payment": {"summary": "a"},
            "risk": {"summary": "b"},
        },
    }
    assert should_skip_aggregate(state) is False
