"""Local development server entrypoint (always uses the active venv / uv project env)."""

from __future__ import annotations


def main() -> None:
    """
    Run Uvicorn with reload for ``app.main:app``.

    Prefer invoking via ``uv run mall-serve`` so the interpreter matches
    ``uv sync`` dependencies and does not pick up a global ``uvicorn``/site-packages
    from another Python install (e.g. ``D:\\tools`` on Windows).
    """
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
