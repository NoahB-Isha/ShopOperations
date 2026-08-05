#!/bin/sh
set -e
cd /app/backend

# Wait for Postgres, then apply migrations. RUN_MIGRATIONS=0 for the worker —
# only one process should migrate.
python - <<'PY'
import sys, time
from sqlalchemy import create_engine, text
from app.config import get_settings

engine = create_engine(get_settings().database_url)
for attempt in range(60):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        sys.exit(0)
    except Exception:
        time.sleep(1)
print("database never became reachable", file=sys.stderr)
sys.exit(1)
PY

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  alembic upgrade head
fi

cd /app

# Trust the reverse proxy's forwarding headers, but only from the proxy.
# Without --proxy-headers, request.client.host is the proxy's address, so the
# rate limiter and audit log would see every visitor as one caller.
# FORWARDED_ALLOW_IPS must name the proxy (Caddy's container address, or the
# platform edge on a hosted deploy): "*" would let ANY caller spoof its own
# address via X-Forwarded-For and walk straight past an IP-keyed limiter.
# Done here rather than in CMD because exec-form CMD cannot expand variables.
if [ "$1" = "uvicorn" ]; then
  set -- "$@" --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
fi

exec "$@"
