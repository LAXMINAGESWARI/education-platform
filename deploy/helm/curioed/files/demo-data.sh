#!/bin/sh
# Wait for CNPG, migrate, seed curriculum, then the synthetic school.
set -eu

i=0
until pg_isready -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB"; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "postgres did not become ready" >&2
    exit 1
  fi
  sleep 5
done

alembic upgrade head
python -m education_platform.modules.materials.seed
DEMO_SEED=1 python /app/backend/scripts/seed_demo.py
