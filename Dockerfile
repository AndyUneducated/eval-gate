FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.14

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic.ini ./

RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uvicorn", "evalgate.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
