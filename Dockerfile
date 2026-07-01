FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

FROM base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev

FROM base AS runtime
RUN adduser --disabled-password --gecos "" appuser
COPY --from=builder /app/.venv /app/.venv
COPY . .
ENV PATH="/app/.venv/bin:$PATH"
RUN mkdir -p /app/logs && chown -R appuser:appuser /app
USER appuser
# aiohttp server (health/metrics + provider webhooks). Declaring the single port lets
# Traefik/Coolify target it; the app must also bind 0.0.0.0 (HTTP__HOST) to be reachable.
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import os, urllib.request; port = os.getenv('HTTP__PORT') or os.getenv('HTTP_PORT') or '8080'; urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3).read()"
CMD ["python", "-m", "bot"]
