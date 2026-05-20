---
name: mall-agent-prompts
description: Maintains and optimizes mall-agent customer-service LLM prompts in backend/app/core/prompts.py. Use when the user asks to tune, rewrite, or optimize prompts, agent tone, classify/aggregate/risk/payment/wallet instructions, or structured-output field descriptions for the LangGraph supervisor and specialists.
---

# Mall Agent — Prompt Maintenance

## Single source of truth

All **system prompts** for chat nodes live in:

`backend/app/core/prompts.py`

Do **not** scatter new prompt strings in `agents/*.py` or `graph/supervisor.py` unless wiring only (import constants from `prompts.py`).

## Prompt map

| Constant | Used by | Purpose |
|----------|---------|---------|
| `_CS_BASE` | Composed into payment/aggregate/wallet | Shared voice, safety, no-hallucination |
| `CLASSIFY_INTENT` | `supervisor.classify_intent` | Intent routing (structured) |
| `PAYMENT_DIRECT_RAG` | `payment_agent` fast path | KB-grounded payment reply |
| `PAYMENT_TOOL_LOOP` | `payment_agent` tool loop | RAG tool + reply |
| `PAYMENT_FINAL` | `payment_agent` final pass | After tools |
| `RISK_ADJUDICATION` | `risk_agent` | Structured verdict |
| `wallet_system()` | `wallet_agent` | Tool use + session ids |
| `WALLET_FINAL` | `wallet_agent` final pass | After tools |
| `AGGREGATE` | `supervisor.aggregate_node` | Merge specialist summaries |
| `WALLET_KYC_BLOCKED` | `wallet_agent` | Hard-coded blocked message |
| `HUMAN_HANDOFF_SUFFIX` | `supervisor.human_handoff_node` | Default handoff line |
| `payment_rag_human()` / `risk_adjudication_human()` | payment / risk agents | Human message wrappers |
| `_REPLY_SHAPE` | payment / wallet / aggregate | Shared 结论→依据→下一步 structure |

Structured-output **schemas** (not full prompts) stay next to nodes:

- `IntentSchema` → `backend/app/graph/supervisor.py`
- `RiskLLMVerdict` → `backend/app/agents/risk_agent.py`

RAG **tool** docstring for the model → `backend/app/tools/rag_tool.py` (`rag_hybrid_search`).

## Editing workflow

1. **Clarify scope** — which node(s): classify / payment / risk / wallet / aggregate / handoff?
2. **Edit `prompts.py`** — keep `_CS_BASE` short; node-specific rules below it.
3. **If structured output drifts** — update Chinese `Field(description=...)` on the Pydantic model in the same PR.
4. **Avoid token bloat** — prefer bullets over paragraphs; no duplicate rules across constants.
5. **Verify** — from `backend/`:
   ```bash
   uv run python -c "from app.graph.supervisor import compile_supervisor; compile_supervisor(); print('ok')"
   ```
6. **Manual check** — one SSE turn per changed node (e.g. refund FAQ → payment; mixed → aggregate).

## Non-negotiable rules (`_CS_BASE`)

When changing tone or length, **preserve**:

- Official mall CS voice: 专业、礼貌、简洁
- User-facing **中文 only**
- Never mention: 模型、工具、JSON、专家/智能体
- No fabricated policy, amounts, or timelines
- No KB/tool data → 「暂未查到相关规定」+ 订单号 or 转人工

## Node-specific constraints

| Node | Do | Don't |
|------|----|-------|
| Classify | Minimize `sub_tasks`; calibrate `confidence` | Route everything to all three agents |
| Payment | Ground answers in KB / tool results | Invent refund timelines |
| Risk | `user_reply` must be sendable as-is | Fabricate `assessment` fields |
| Wallet | Call tools before stating balances | Answer when `kyc_status=blocked` |
| Aggregate | One cohesive reply; risk wins conflicts | Expose JSON or agent names |

## Related config (not prompts)

Performance and routing flags are in `backend/app/core/config.py` and `docs/PERFORMANCE_zh.md` — do not mix env tuning into prompt text.

## More detail

- Field-level schema notes: [reference.md](reference.md)
- KB content (not prompts): `docs/KB_INGESTION_zh.md`
