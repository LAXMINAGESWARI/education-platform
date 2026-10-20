$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $RootDir "backend"
Set-Location $BackendDir

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Step "Installing uv..."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$HOME\.local\bin;$env:Path"
}

Write-Step "Installing Python 3.12..."
uv python install 3.12

Write-Step "Syncing backend dependencies..."
uv sync --all-groups

Write-Step "Installing pre-commit hooks..."
uv run pre-commit install

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Step "Created backend/.env from .env.example."
} else {
    Write-Step "backend/.env already exists; leaving it unchanged."
}

Write-Step "Starting Postgres (Docker Compose)..."
Set-Location $RootDir
if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker compose up -d postgres
} else {
    Write-Step "Docker not found; start Postgres manually before migrating."
}

Write-Host @"

Setup complete.

Next steps:
  1. Infra: docker compose up -d postgres  (wait until healthy)
  2. Apply schema: cd backend; uv run alembic upgrade head
     Load curriculum: uv run python -m education_platform.modules.materials.seed
     (the API does not seed on startup)
  3. Start the API: cd backend; uv run uvicorn education_platform.main:app --reload --host 127.0.0.1 --port 8000
  4. Frontend: cd frontend; copy .env.example .env; npm install; npm run dev
  5. Optional ingest worker: cd backend; uv run python -m education_platform.workers
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

Frontend: cd frontend; npm test

Approved curriculum lives in docs/curriculum/.
Schema browser: docs/design/relational-schema.html
"@
