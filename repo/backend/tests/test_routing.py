"""Tests for classify sub_tasks normalization."""

from __future__ import annotations

from app.core.routing import normalize_sub_tasks


def test_unknown_stays_empty():
    assert normalize_sub_tasks("unknown", []) == []


def test_mixed_default_when_empty():
    assert normalize_sub_tasks("mixed", []) == ["payment", "risk"]


def test_single_domain_appends_task_type():
    assert normalize_sub_tasks("wallet", []) == ["wallet"]


def test_filters_invalid_and_orders():
    assert normalize_sub_tasks("mixed", ["wallet", "invalid", "payment"]) == [
        "payment",
        "wallet",
    ]


def test_dedupes():
    assert normalize_sub_tasks("payment", ["payment", "payment"]) == ["payment"]
