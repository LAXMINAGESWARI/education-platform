#!/bin/sh
# Timestamped backup, then Alembic, then synthetic Core topic promotion,
# then the optional synthetic demo school.
# The approved markdown curriculum is a separate one-off:
# python -m education_platform.modules.materials.seed
# Compose runs that as the `seed` service after this script.
# A failed backup stops the script before migrations.
# SEED_SYNTHETIC=true seeds once. SEED_SYNTHETIC=always rebuilds that school.
set -eu

cd /app/backend

echo "Backing up the database before migrations and topic promotion"
backup_dir="${BACKUP_DIR:-/backups}"
mkdir -p "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
export BACKUP_FILE="${backup_dir}/education-${stamp}.dump"
if ! python -m education_platform.db.pg_backup; then
  echo "Database backup failed. Stopping before migrations." >&2
  exit 1
fi

echo "Running alembic upgrade head"
alembic upgrade head

echo "Promoting synthetic Core topics"
python -m education_platform.modules.synthetic.promote

mode="${SEED_SYNTHETIC:-false}"
case "$mode" in
  false | 0 | "")
    echo "SEED_SYNTHETIC is off. Skipping the synthetic demo school."
    exit 0
    ;;
  true | 1 | once | always) ;;
  *)
    echo "SEED_SYNTHETIC must be false, true, once, or always (got: $mode)" >&2
    exit 1
    ;;
esac

institution="${SYNTHETIC_INSTITUTION:-Al Noor International}"
export SYNTHETIC_INSTITUTION="$institution"

if [ "$mode" != "always" ]; then
  exists="$(python - <<'PY'
import os

from sqlalchemy import create_engine, text

from education_platform.core.config import get_settings
from education_platform.db.url import to_sync_url

engine = create_engine(to_sync_url(get_settings().database_url), pool_pre_ping=True)
name = os.environ["SYNTHETIC_INSTITUTION"]
with engine.connect() as conn:
    row = conn.execute(
        text("SELECT 1 FROM institutions WHERE name = :name"),
        {"name": name},
    ).first()
engine.dispose()
print("yes" if row else "no")
PY
)"
  if [ "$exists" = "yes" ]; then
    echo "Synthetic school '$institution' is already present. Set SEED_SYNTHETIC=always to rebuild it."
    exit 0
  fi
fi

set -- python -m education_platform.modules.synthetic.cli --institution "$institution"
if [ -n "${SYNTHETIC_STUDENTS_PER_SECTION:-}" ]; then
  set -- "$@" --students-per-section "$SYNTHETIC_STUDENTS_PER_SECTION"
fi
if [ -n "${SYNTHETIC_SECTIONS_PER_GRADE:-}" ]; then
  set -- "$@" --sections-per-grade "$SYNTHETIC_SECTIONS_PER_GRADE"
fi
if [ -n "${SYNTHETIC_TERM_WEEKS:-}" ]; then
  set -- "$@" --term-weeks "$SYNTHETIC_TERM_WEEKS"
fi
if [ -n "${SYNTHETIC_SEED:-}" ]; then
  set -- "$@" --seed "$SYNTHETIC_SEED"
fi

echo "Seeding synthetic school ($institution)"
"$@"
