# Backend + worker share one image; compose runs different commands.
#
# BASE IMAGE PINNING: `python:3.12-slim` is a moving tag. Pin it by digest for
# reproducible builds — get the digest with:
#   docker buildx imagetools inspect python:3.12-slim
# and write `FROM python:3.12-slim@sha256:<digest> AS base`. The Dependabot
# `docker` ecosystem entry (.github/dependabot.yml) then keeps the pin current.
FROM python:3.12-slim AS base

# `latest` here meant every rebuild could silently change the resolver. `0.9` is
# a concrete minor tag; pin it by digest for a reproducible build with:
#   docker buildx imagetools inspect ghcr.io/astral-sh/uv:0.9
# then use COPY --from=ghcr.io/astral-sh/uv:0.9@sha256:<digest> …
# MANUAL FOLLOW-UP: the digest could not be resolved offline.
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

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

# Reference docs — needed for roster import when a docs/ file is configured.
# PII workbooks are NOT in the repo or this image (see .dockerignore): the
# coordinator roster is mounted from the gitignored ./private/ directory.
COPY docs docs

COPY infra/docker/backend-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run as an unprivileged user. Both processes only ever read the image and
# write to Postgres.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

# Pre-generate deterministic demo Odoo fixtures at build time. Pure Python, no
# DB required, so the image works in fixture mode (ODOO_* blank) with no runtime
# volume mount — important for cloud deployments.
# ORDER MATTERS: this must run AFTER `USER appuser`. Compose mounts a named
# volume over backend/data, and Docker seeds a fresh volume from the image
# *including* its uid/gid — generating these as appuser is what leaves that
# volume writable by the running container.
RUN python -m app.odoo.fixtures.generate

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
