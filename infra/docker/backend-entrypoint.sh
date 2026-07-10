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
exec "$@"
