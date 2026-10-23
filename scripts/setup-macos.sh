#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
cd "$BACKEND_DIR"

log() {
  echo "==> $*"
}

if ! command -v uv >/dev/null 2>&1; then
  log "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

log "Installing Python 3.12..."
uv python install 3.12

log "Syncing backend dependencies..."
uv sync --all-groups

log "Installing pre-commit hooks..."
uv run pre-commit install --config ../.pre-commit-config.yaml || uv run pre-commit install

if [[ ! -f .env ]]; then
  cp .env.example .env
  log "Created backend/.env from .env.example."
else
  log "backend/.env already exists; leaving it unchanged."
fi

log "Starting Postgres (Docker Compose)..."
cd "$ROOT_DIR"
if command -v docker >/dev/null 2>&1; then
  docker compose up -d postgres
else
  log "Docker not found; start Postgres manually before migrating."
fi

cat <<'EOF'

Setup complete.

Next steps:
  1. Infra: docker compose up -d postgres  (wait until healthy)
  2. Apply schema: cd backend && uv run alembic upgrade head
     Load curriculum: uv run python -m education_platform.modules.materials.seed
     (the API does not seed on startup)
  3. Start the API: cd backend && uv run uvicorn education_platform.main:app --reload --host 127.0.0.1 --port 8000
  4. Frontend: cd frontend && cp .env.example .env && npm install && npm run dev
  5. Optional ingest worker: cd backend && uv run python -m education_platform.workers
  6. Optional policy LLM: set OPENROUTER_API_KEY in backend/.env
  7. Open: API docs http://127.0.0.1:8000/docs  |  app http://localhost:5173

Demo accounts:
  student@demo.school / demo1234
  admin@demo.school / demo1234

Quality checks (from backend/):
  uv run ruff format --check .
  uv run ruff check .
  uv run mypy src
  uv run alembic upgrade head
  uv run pytest

Frontend: cd frontend && npm test

Approved curriculum lives in docs/curriculum/.
Schema browser: docs/design/relational-schema.html
EOF
