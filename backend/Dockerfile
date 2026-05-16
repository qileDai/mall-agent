FROM python:3.11-slim-bookworm

WORKDIR /app

RUN pip install --no-cache-dir uv==0.5.14

COPY pyproject.toml README.md /app/
RUN uv sync --no-dev

COPY app /app/app
COPY data /app/data

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
