FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv

ENV UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    PATH="/app/.venv/bin:$PATH" \
    TODOMATE_ENV_FILE=/data/.env \
    TODOMATE_CREDENTIALS_FILE=/data/credentials.json

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
RUN useradd --create-home app \
    && install -d --owner=app --group=app /data

USER app

EXPOSE 8000

CMD ["todomate-mcp", "http", "--host", "0.0.0.0"]
