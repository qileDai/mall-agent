# 代码导读（中文）

本文按「请求从进入到落地」的顺序，把仓库里最重要的文件串起来读。配合 `docs/ARCHITECTURE.md` 的框图更快。

## 1. 入口与环境：`app/main.py` + `app/config.py`

- `load_dotenv()`：从进程环境或 `.env` 注入变量。  
- `lifespan`：服务启动时执行一次 `ensure_demo_data()`，然后构造 `RagPipeline` 与 LangGraph `CompiledStateGraph`。  
- `Settings`（`config.py`）用 `dataclass` 承载 `OPENAI_*`、`QDRANT_*`、`DATA_DIR` 等，避免在业务层手写 Pydantic 模型。

## 2. 启动种子：`app/bootstrap.py` + `app/mocks/*`

- `write_orders_json`：生成约 400 条订单，字段覆盖状态、渠道、地区、SKU、金额、地址等。  
- `ensure_kb_pdfs`：若 `data/kb` 下没有 PDF，则用 `fpdf2` 生成三份英文政策文件（避免默认 Helvetica 无法渲染中文的问题）。  
- 若不存在 `chunk_store.json` 且配置了真实 `OPENAI_API_KEY`，会调用 `RagPipeline.ingest_kb_dir` 入库。

## 3. HTTP 层：`app/main.py`

- `/api/chat`：把前端 `history` 转成 `HumanMessage` / `AIMessage`，再 `graph.invoke(...)`。  
  初始 state 显式带上 `route`、`order_snippets`、`rag_snippets` 空串，避免 LangGraph 状态缺键。  
- `/api/orders`：结构化查询参数 → `OrderQuery` → `query_orders`。  
- `/api/orders/export?fmt=csv|json`：复用同一套过滤逻辑，CSV 用 `csv.DictWriter` 扁平化嵌套字段。  
- `/api/admin/reingest`：管理员式重建向量集合（演示环境使用；生产需鉴权）。

## 4. 多智能体编排：`app/graph/workflow.py`

核心是一个 `StateGraph(AgentState)`：

1. `router`：系统提示要求模型**只输出一行** `ROUTE=...`；代码用正则截取，失败则回退 `general`。  
2. 条件边：  
   - `orders` →（若 `route == both`）`rag` → `answer`  
   - 否则 `orders` 直接 `answer`  
   - `kb` → `rag` → `answer`  
   - `general` → 直接 `answer`  
3. `orders_node`：轻量规则从自然语言提取 `ORD-...`、`\bstatus\b`、`\bchannel\b`、中文金额阈值等，再调用 `query_orders`。  
4. `rag_node`：调用 `RagPipeline.hybrid_retrieve`。  
5. `answer_node`：把订单 JSON 片段与 KB 摘录拼到提示词里，约束模型不要编造。

> 说明：这是「可读的工程化路由」示例；若需更严谨的工具调用，可升级为 OpenAI tool schema / LangGraph 预置 `ToolNode`。

## 5. RAG 管道：`app/rag/*`

### 5.1 PDF 与清洗

- `pdf_extract.py`：`pypdf` 逐页 `extract_text()`。  
- `text_clean.py`：`NFKC`、控制字符剔除、空白折叠；`chunk_with_template` 做滑动窗口并在每段前加 `[KB片段]` 前缀模板。

### 5.2 入库与向量库

- `pipeline.py`：`OpenAIEmbeddings` 批量 `embed_documents`，维度用于 `create_collection`。  
- 为演示一致性，`ingest_kb_dir` 会 **delete_collection + create_collection** 再 `upsert`。  
- 同步写 `chunk_store.json`：保存 `id/text/source`，供 BM25 使用。

### 5.3 混合检索 + 重排

1. **稠密**：Qdrant `search` 取 TopK，`hit.id` 作为 chunk id。  
2. **稀疏**：`rank_bm25` 在内存语料上打分；分词 `_tokenize` 同时支持拉丁词与单字 CJK。  
3. **融合**：`_rrf_fuse` 对两个排序列表做 RRF（倒数排名融合）。  
4. **重排**：把候选片段编号喂给 `gpt-4o-mini`，要求只输出 JSON 排序数组；解析失败则保持原顺序。

### 5.4 无密钥/无向量时的行为

- `OPENAI_API_KEY` 为空：`hybrid_retrieve` 直接 `[]`；启动不入库。  
- 客户端构造仍传入占位 key（`DUMMY_LOCAL_KEY`）以满足新版 OpenAI SDK 的非空校验，但**不会**在空密钥路径下调用远程嵌入。

## 6. 订单服务：`app/services/orders.py`

- 纯本地 JSON 过滤：状态/渠道/地区精确匹配；`keyword` 对整行 JSON 做子串匹配（演示够用）。  
- `limit` 在扫描过程中达到即停止：适合大文件快速返回。

## 7. 前端：`frontend/src/App.jsx`

- `postChat`：维护最多 20 条上下文。  
- 订单页：`fetchOrders` + `downloadExport` 通过查询字符串传筛选条件。  
- 样式：`styles.css` 深色面板风，便于演示大屏。

## 8. Docker

- `backend/Dockerfile`：`uv sync --frozen` 复现依赖。  
- `frontend/Dockerfile`：`npm ci` + `nginx` 反代 `/api`。  
- `docker-compose.yml`：Qdrant + Backend + Frontend 三容器，数据卷持久化。

## 9. 你可以改哪里来贴近真实业务？

- 把 `orders.json` 换成真实数据库查询层（保留 `OrderQuery` 形状即可）。  
- 在 `ingest_kb_dir` 前增加权限校验与增量更新策略。  
- 为 `/api/admin/reingest` 增加 JWT / 内网限制。  
- 将 `_rerank_llm` 替换为专用重排模型（如 Cohere / bge-reranker）以降低时延与成本。
