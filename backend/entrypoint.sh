#!/usr/bin/env bash
set -euo pipefail

ROLE="${AUREA_ROLE:-api}"
echo "[aurea] starting (role=${ROLE})"

if [ "${ROLE}" = "worker" ]; then
  echo "[aurea] launching atlas worker..."
  exec python -m app.atlas.worker
fi

if [ "${AUREA_RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "[aurea] running database migrations..."
  python -m app.db_bootstrap
fi

if [ "${AUREA_RUN_SEED:-false}" = "true" ]; then
  echo "[aurea] seeding demo firm + synthetic clients (idempotent)..."
  python -m seed.run || echo "[aurea] seed skipped/failed (non-fatal)"
  echo "[aurea] seeding the India demonstration firm (idempotent, does nothing if it exists)..."
  python -m seed.india_demo || echo "[aurea] India seed skipped/failed (non-fatal)"
  python -m seed.nz_demo_label_backfill || echo "[aurea] demo label skipped/failed (non-fatal)"
fi

echo "[aurea] launching API on :8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
