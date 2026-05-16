# Backend (mall-agent)

Install with [uv](https://github.com/astral-sh/uv):

```bash
cd backend
uv sync
# 当前 pyproject 使用 tool.uv.package=false（不安装本包 entry points），请用：
uv run python -c "from app.serve import main; main()"
# 或 Windows：.\dev.ps1
```

**工作目录**：必须在 **`backend`** 下执行上述命令（包名为 `app`，路径是 `backend/app`）。在仓库根目录 `mall-agent` 直接跑 `uvicorn app.main:app` 会报 **`No module named 'app'`**。可从仓库根目录执行 **`.\run-backend.ps1`**（见根目录 README）。

上述命令与 `mall-serve` 等价（带 reload），且与 `uv sync` 使用同一套依赖。

**Windows / 多 Python 注意**：在 PowerShell 里直接输入 `uvicorn` 时，`PATH` 可能先命中 **`D:\tools\`** 下的全局安装；重载子进程会跟着用那套 `site-packages`，从而出现旧版 `langchain_openai` 依赖已删除的 `langchain_core.pydantic_v1` 的报错。请始终使用下面之一：

- `uv run python -c "from app.serve import main; main()"`（推荐）
- `.\dev.ps1`
- `.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

自查：`Get-Command uvicorn` / `where.exe uvicorn`。

环境变量见仓库根目录 `.env.example`。
