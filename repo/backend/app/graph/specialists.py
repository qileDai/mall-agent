"""Parallel execution of specialist agents."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from langchain_core.runnables import RunnableConfig

from app.agents.payment_agent import run_payment_agent
from app.agents.risk_agent import run_risk_agent
from app.agents.wallet_agent import run_wallet_agent
from app.graph.state import GraphState

logger = logging.getLogger(__name__)

AgentFn = Callable[[GraphState, RunnableConfig | None], Awaitable[dict[str, Any]]]

_AGENTS: dict[str, AgentFn] = {
    "payment": run_payment_agent,
    "risk": run_risk_agent,
    "wallet": run_wallet_agent,
}


def _merge_partial_updates(results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Merge partial state dicts from parallel agent runs.

    Args:
        results: List of partial updates (may include empty dicts).

    Returns:
        Combined partial state for LangGraph.
    """
    merged: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    ctx: dict[str, Any] = {}
    needs_human = False

    for part in results:
        if not part:
            continue
        if "agent_outputs" in part:
            outputs.update(part["agent_outputs"] or {})
        if "stream_events" in part:
            events.extend(part["stream_events"] or [])
        if "user_context" in part:
            ctx.update(part["user_context"] or {})
        if part.get("needs_human"):
            needs_human = True

    if outputs:
        merged["agent_outputs"] = outputs
    if events:
        merged["stream_events"] = events
    if ctx:
        merged["user_context"] = ctx
    if needs_human:
        merged["needs_human"] = True
    return merged


async def run_specialists_parallel(
    state: GraphState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """
    Run all active specialists in ``sub_tasks`` concurrently via ``asyncio.gather``.

    Args:
        state: LangGraph state after dispatch.
        config: Runnable config for tracing.

    Returns:
        Merged partial state from payment / risk / wallet agents.
    """
    order = ["payment", "risk", "wallet"]
    sub = set(state.get("sub_tasks") or [])
    active = [name for name in order if name in sub]
    if not active:
        return {
            "stream_events": [
                {
                    "type": "thinking",
                    "agent": "supervisor",
                    "detail": "specialists_parallel: no active agents",
                }
            ]
        }

    tasks = [_AGENTS[name](state, config) for name in active]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    partials: list[dict[str, Any]] = []
    for name, res in zip(active, results, strict=True):
        if isinstance(res, Exception):
            logger.exception("agent %s failed", name, exc_info=res)
            partials.append(
                {
                    "agent_outputs": {name: {"error": str(res)}},
                    "stream_events": [
                        {"type": "error", "agent": name, "message": str(res)},
                    ],
                }
            )
        else:
            partials.append(res)

    merged = _merge_partial_updates(partials)
    merged.setdefault("stream_events", []).insert(
        0,
        {
            "type": "thinking",
            "agent": "supervisor",
            "detail": f"specialists_parallel: ran {active}",
        },
    )
    return merged
