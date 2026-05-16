# 知识库数据目录

将 FAQ、历史工单、产品文档放在此目录下，由入库脚本写入 Qdrant。

- `faq/*.jsonl` — 支付 FAQ（`source=payment_faq`）
- `tickets/*.jsonl` — 历史工单（`source=historical_ticket`）
- `product/*.md` — 产品/政策文档（`source=product_doc`）

详细说明见仓库 [docs/KB_INGESTION_zh.md](../../../docs/KB_INGESTION_zh.md)。

```powershell
cd backend
uv run python -m app.cli.ingest_kb
```
