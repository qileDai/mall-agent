# Mall Agent — 生产级多智能体客服（演示骨架）

企业内部多智能体客服：**Supervisor（LangGraph）** 路由 → 支付（RAG + Qdrant 混合检索）/ 风控（Mock API + 结构化裁决）/ 钱包（Mock API + OTP 模拟）→ 汇总回复；**SSE** 流式输出（`thinking` / `tool_call` / `token` / `error` / `done`）；**Redis** 会话；**LangSmith** 通过环境变量一键接入；OpenAI 兼容网关通过 **`OPENAI_API_BASE`** 支持中转。

## 技术栈

| 层级 | 选型 |
|------|------|
| 后端 | Python 3.11+、FastAPI、SSE、`uv` |
| 编排 | LangGraph（状态图 + 条件边） |
| LLM / Embeddings | LangChain `ChatOpenAI` / `OpenAIEmbeddings` → **gpt-4o-mini**、**text-embedding-3-small** |
| 向量库 | Qdrant（稠密检索 + 本地 BM25 + **RRF** 融合） |
| 缓存 | Redis（会话 JSON、简单限流） |
| 前端 | Vite + React 18 + TypeScript + Tailwind |
| 可观测 | LangSmith（`LANGCHAIN_TRACING_V2` 等） |

## 快速开始（Docker）

```bash
cp .env.example .env
# 填写 OPENAI_API_KEY 与 OPENAI_API_BASE（中转地址，通常以 /v1 结尾）
docker compose --env-file .env up --build
```

- 前端（Nginx）：http://localhost:8080  
- 后端直连：http://localhost:8000/docs  
- Qdrant Dashboard：http://localhost:6333/dashboard  
- Redis：localhost:6379  

## 本地开发（uv + npm）

### 1. 基础设施

```bash
docker compose up -d qdrant redis
```

### 2. 后端

```powershell
cd backend
uv sync --no-dev
$env:OPENAI_API_KEY="sk-..."
$env:OPENAI_API_BASE="https://你的中转/v1"
$env:QDRANT_URL="http://127.0.0.1:6333"
$env:REDIS_URL="redis://127.0.0.1:6379/0"
uv run python -c "from app.serve import main; main()"
```

Windows 上请勿依赖裸命令 `uvicorn`（易命中 `D:\tools` 全局环境）；请用上面命令、`backend\dev.ps1` 或根目录 `.\run-backend.ps1`。

**若在仓库根目录 `mall-agent` 下启动**，会出现 `No module named 'app'`（因为 `app` 在 `backend/app`）。任选其一：

- 在仓库根目录执行：`.\run-backend.ps1`（会自动 `cd backend` 并用 `.venv\Scripts\python.exe`）
- 或先：`cd backend`，再：`uv run mall-serve`

### 3. 前端

```powershell
cd frontend
npm install
npm run dev
```

Vite 将 `/api` 代理到 `http://127.0.0.1:8000`。

### SSE 与 WebSocket

- **SSE（主通道）**：`POST /api/chat/stream`，body：`{"message":"...","session_id":"..."}`（`session_id` 可空，服务端会生成）。  
- **WebSocket（预留）**：`GET ws://127.0.0.1:8000/api/chat/ws` — 当前为 echo 演示，可与 `useAgentChat` 并行演进。

## LangSmith

在 `.env` 中设置：

```env
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_...   # 或使用 LANGSMITH_API_KEY（代码会映射）
LANGCHAIN_PROJECT=mall-agent
```

图节点与模型调用已带 `run_name` / `tags` 便于在 Smith 中筛选。

## 目录结构（核心）

```
backend/app/
  main.py              # FastAPI 入口
  core/config.py       # pydantic-settings
  graph/state.py       # LangGraph 状态 + reducers
  graph/supervisor.py  # 分类 / 分发 / 子 Agent / 汇总 / 转人工
  agents/*.py          # 支付、风控、钱包
  tools/*.py           # RAG、风控 Mock、钱包 Mock
  api/chat.py          # SSE + WS 占位
frontend/src/
  hooks/useAgentChat.ts
  components/ChatWidget.tsx
  services/api.ts
```

## 安全提示

勿将真实 `OPENAI_API_KEY` 提交到 git。若密钥曾泄露，请在服务商侧轮换。

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 组件与数据流  
- [docs/CODE_WALKTHROUGH_zh.md](docs/CODE_WALKTHROUGH_zh.md) — 按文件阅读代码  
- [docs/TOKEN_OPTIMIZATION_zh.md](docs/TOKEN_OPTIMIZATION_zh.md) — **上下文 Token 优化总结**

## 旧文档

`docs/` 下其它历史说明若与本文冲突，以本 README 为准。
