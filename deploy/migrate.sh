#!/bin/sh
# Alembic, then the optional synthetic demo school.
# SEED_SYNTHETIC=true seeds once. SEED_SYNTHETIC=always rebuilds that school.
set -eu

cd /app/backend

echo "Running alembic upgrade head"
alembic upgrade head

echo "Backing up the database before topic promotion"
backup_dir="${BACKUP_DIR:-/backups}"
mkdir -p "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
export BACKUP_FILE="${backup_dir}/education-${stamp}.dump"
python - <<'PY'
import os
import subprocess
import sys
from urllib.parse import urlparse

from education_platform.core.config import get_settings
from education_platform.db.url import to_sync_url

sync_url = to_sync_url(get_settings().database_url)
parsed = urlparse(sync_url.replace("postgresql+psycopg", "postgresql", 1))
env = os.environ.copy()
if parsed.password:
    env["PGPASSWORD"] = parsed.password
outfile = os.environ["BACKUP_FILE"]
command = [
    "pg_dump",
    "--format=custom",
    "--no-owner",
    "--file",
    outfile,
    "-h",
    parsed.hostname or "localhost",
    "-p",
    str(parsed.port or 5432),
    "-U",
    parsed.username or "education",
    "-d",
    (parsed.path or "/education").lstrip("/"),
]
completed = subprocess.run(command, env=env, check=False)
if completed.returncode != 0:
    print("Database backup failed. Topic promotion was not started.", file=sys.stderr)
    sys.exit(completed.returncode)
print(f"Database backup written: {os.path.basename(outfile)}")
PY

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
