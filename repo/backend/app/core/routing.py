"""Pure routing helpers (classify post-processing)."""

from __future__ import annotations

_VALID = frozenset({"payment", "risk", "wallet"})
_ORDER = ("payment", "risk", "wallet")


def normalize_sub_tasks(task_type: str, sub_tasks: list[str]) -> list[str]:
    """
    Normalize LLM classify output into ordered specialist list.

    Args:
        task_type: Intent task_type from classifier.
        sub_tasks: Raw sub_tasks from classifier.

    Returns:
        Filtered, deduped specialists in payment → risk → wallet order.
        ``unknown`` with no experts stays empty (chitchat / handoff paths).
    """
    sub = [s for s in sub_tasks if s in _VALID]
    if task_type == "mixed" and not sub:
        sub = ["payment", "risk"]
    if task_type in _VALID and task_type not in sub:
        sub.append(task_type)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in _ORDER:
        if name in sub and name not in seen:
            ordered.append(name)
            seen.add(name)
    return ordered
