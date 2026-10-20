#!/usr/bin/env bash
# Run the pytest subset implied by the diff against origin/main.
# Committed branch changes and the working tree are both included.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

run_fast() {
  make -C "$BACKEND_DIR" test-fast
}

if ! git -C "$ROOT_DIR" rev-parse --verify origin/main >/dev/null 2>&1; then
  echo "origin/main is not available; running the fast suite."
  run_fast
  exit
fi

mapfile -t changed < <(
  {
    git -C "$ROOT_DIR" diff --name-only "origin/main..."
    git -C "$ROOT_DIR" diff --name-only
    git -C "$ROOT_DIR" diff --name-only --cached
  } | sed '/^$/d' | sort -u
)

if ((${#changed[@]} == 0)); then
  echo "No changes against origin/main."
  exit 0
fi

# Same filenames conftest marks with `ml`.
ml_list="$BACKEND_DIR/tests/ml_test_files.txt"

fallback=0
declare -A selected=()

add_matches() {
  local pattern="$1"
  local match
  shopt -s nullglob
  for match in "$BACKEND_DIR"/$pattern; do
    selected["${match#"$BACKEND_DIR"/}"]=1
  done
  shopt -u nullglob
}

for path in "${changed[@]}"; do
  case "$path" in
    backend/tests/conftest.py | backend/tests/ml_test_files.txt | backend/tests/postgres_connect.py | backend/alembic/* | backend/src/education_platform/db/*)
      fallback=1
      ;;
    backend/tests/test_*.py)
      selected["${path#backend/}"]=1
      ;;
    backend/src/education_platform/modules/*/*)
      module="${path#backend/src/education_platform/modules/}"
      module="${module%%/*}"
      add_matches "tests/test_${module}*.py"
      ;;
    backend/src/education_platform/workers/*)
      while IFS= read -r name || [[ -n "${name:-}" ]]; do
        name="${name%%$'\r'}"
        name="${name#"${name%%[![:space:]]*}"}"
        [[ -z "$name" || "$name" == \#* ]] && continue
        selected["tests/${name}"]=1
      done < "$ml_list"
      add_matches "tests/test_worker*.py"
      ;;
  esac
done

if ((fallback)); then
  echo "Shared test infrastructure, Alembic, or db/ changed; running the fast suite."
  run_fast
  exit
fi

if ((${#selected[@]} == 0)); then
  echo "No mapped backend tests for this diff."
  exit 0
fi

mapfile -t files < <(printf '%s\n' "${!selected[@]}" | sort)
echo "Running ${#files[@]} test file(s): ${files[*]}"
cd "$BACKEND_DIR"
uv run pytest -n auto --dist loadfile --no-cov -q "${files[@]}"
