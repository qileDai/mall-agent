"""Token-saving helpers: message windows, RAG compaction, aggregate payloads."""

from __future__ import annotations

from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

# Defaults; overridden via Settings when available.
DEFAULT_SESSION_MAX_MESSAGES = 16
DEFAULT_RAG_TOP_K = 4
DEFAULT_RAG_CHUNK_CHARS = 480
DEFAULT_AGGREGATE_SUMMARY_CHARS = 800


def truncate_session_messages(
    messages: Sequence[BaseMessage],
    max_messages: int = DEFAULT_SESSION_MAX_MESSAGES,
) -> list[BaseMessage]:
    """
    Keep only the most recent messages for Redis / graph state.

    Args:
        messages: Full conversation history.
        max_messages: Maximum number of messages to retain.

    Returns:
        Truncated list (copy).
    """
    if max_messages <= 0 or len(messages) <= max_messages:
        return list(messages)
    return list(messages[-max_messages:])


def compact_rag_hit(row: dict[str, Any], max_chars: int) -> dict[str, str]:
    """
    Shrink one retrieval hit for tool / LLM consumption.

    Args:
        row: Hit with ``text`` and optional ``metadata``.
        max_chars: Max characters for ``text``.

    Returns:
        Dict with ``text`` and ``source`` only.
    """
    text = str(row.get("text", ""))[:max_chars]
    meta = row.get("metadata") or {}
    source = str(meta.get("source", meta.get("topic", "")))[:120]
    return {"text": text, "source": source}


def compact_rag_hits(
    hits: list[dict[str, Any]],
    max_chars: int = DEFAULT_RAG_CHUNK_CHARS,
) -> list[dict[str, str]]:
    """Compact a list of RAG hits."""
    return [compact_rag_hit(h, max_chars) for h in hits]


def extract_agent_text(agent: str, block: dict[str, Any]) -> str:
    """
  Pull user-facing text from an agent output blob.

    Args:
        agent: Agent key (payment / risk / wallet).
        block: Value from ``agent_outputs[agent]``.

    Returns:
        Summary string or empty.
    """
    if not block:
        return ""
    if agent == "risk":
        verdict = block.get("verdict") or {}
        if isinstance(verdict, dict):
            return str(verdict.get("user_reply", ""))[:DEFAULT_AGGREGATE_SUMMARY_CHARS]
    for key in ("summary", "message", "user_reply"):
        val = block.get(key)
        if val:
            return str(val)[:DEFAULT_AGGREGATE_SUMMARY_CHARS]
    return ""


def compact_agent_outputs_for_aggregate(
    outputs: dict[str, Any],
    max_chars: int = DEFAULT_AGGREGATE_SUMMARY_CHARS,
) -> dict[str, str]:
    """
    Build a minimal dict for the aggregate LLM (summaries only).

    Args:
        outputs: Full ``agent_outputs`` from graph state.
        max_chars: Per-agent summary cap.

    Returns:
        Agent name -> short summary text.
    """
    compact: dict[str, str] = {}
    for agent, block in outputs.items():
        if not isinstance(block, dict):
            continue
        text = extract_agent_text(agent, block)
        if text:
            compact[agent] = text[:max_chars]
        elif block.get("error"):
            compact[agent] = f"[{block.get('error')}] {block.get('message', '')}"[:max_chars]
    return compact


def should_skip_aggregate(state: dict[str, Any]) -> bool:
    """
    Return True when a single specialist already produced a complete reply.

    Args:
        state: LangGraph state dict.

    Returns:
        Whether to skip the aggregate LLM call.
    """
    sub = state.get("sub_tasks") or []
    if len(sub) != 1:
        return False
    outputs = state.get("agent_outputs") or {}
    text = extract_agent_text(sub[0], outputs.get(sub[0]) or {})
    return bool(text.strip())


def recent_dialogue_snippet(
    messages: Sequence[BaseMessage],
    max_turns: int = 2,
) -> str:
    """
    Optional short context: last N user/assistant pairs (for clarification flows).

    Args:
        messages: Conversation history.
        max_turns: Number of user messages to anchor from.

    Returns:
        Compact newline-separated snippet.
    """
    lines: list[str] = []
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            lines.append(f"用户: {str(m.content)[:300]}")
        elif isinstance(m, AIMessage):
            lines.append(f"客服: {str(m.content)[:300]}")
        if len([ln for ln in lines if ln.startswith("用户:")]) >= max_turns:
            break
    lines.reverse()
    return "\n".join(lines)
