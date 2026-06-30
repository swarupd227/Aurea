#!/usr/bin/env bash
set -euo pipefail

echo "[aurea] backend starting (role=${AUREA_ROLE:-api})"

if [ "${AUREA_RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "[aurea] running database migrations..."
  python -m app.db_bootstrap
fi

if [ "${AUREA_RUN_SEED:-false}" = "true" ]; then
  echo "[aurea] seeding demo firm + synthetic clients (idempotent)..."
  python -m seed.run || echo "[aurea] seed skipped/failed (non-fatal)"
fi

echo "[aurea] launching API on :8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
