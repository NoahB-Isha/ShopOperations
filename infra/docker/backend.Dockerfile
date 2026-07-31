# Backend + worker share one image; compose runs different commands.
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer (cache-friendly): workspace manifests first.
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/pyproject.toml
COPY worker/pyproject.toml worker/pyproject.toml
RUN uv sync --frozen --no-install-workspace --no-dev

# App code
COPY backend backend
COPY worker worker
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Reference docs (coordinator xlsx, etc.) — needed for roster import.
# Not in .dockerignore so this COPY always succeeds.
COPY docs docs

# Pre-generate deterministic demo Odoo fixtures at build time.
# Pure Python, no DB required. The image works in fixture mode (ODOO_* blank)
# without any runtime volume mount — important for cloud deployments.
RUN python -m app.odoo.fixtures.generate

COPY infra/docker/backend-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
