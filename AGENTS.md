# CurioEd

- Use `uv` for all Python commands from `backend/`; do not add `requirements.txt`.
- Keep HTTP routes thin. Place business logic in module services.
- Treat `docs/curriculum/` markdown as the approved curriculum source for seeding the Postgres
  materials/quiz tables. Student-facing quiz endpoints must not expose answer keys.
- Auth uses JWT access/refresh tokens; do not invent access grants beyond roles and enrollments.
- Student material/quiz/attempt access requires active Grade and Grade–Subject enrollments.
- Administrators may read institution learning-directory without student enrollments.
- Teacher role exists in schema; no teacher backend module yet — frontend uses fixtures.
- Read [`docs/README.md`](docs/README.md) for vision and implementation status before large features.
- Add or update tests for every behavior change. Before finishing, run Ruff format/check, mypy, and
  pytest from `backend/` (coverage gate is 80%).
- Postgres + pgvector (Compose) and Alembic schema live under `backend/`. Materials API reads
  published rows from Postgres after seed. PDF ingest is a Postgres `ingest_jobs` claim worker
  (`uv run python -m education_platform.workers`); keep local `UPLOAD_DIR` for PDF blobs; do not
  introduce MinIO until a feature needs shared object storage.

## Plugin & Skills Setup

This repo includes **pstack** and **cursor-team-kit** plugins, which provide specialized agent skills for common development patterns. All team members automatically gain access to these skills when working in this repo.

### Available pstack Skills

The following skills are available from the pstack plugin:

- **`/how`** — Explains subsystem architecture, runtime flow, ownership, and layering. Use for "how does X work" and code walkthroughs.
- **`/why`** — Discovers available MCPs and queries design rationale, regressions, postmortems, and data-backed decisions.
- **`/setup-pstack`** — Configure which models pstack uses per role and override skill defaults.
- **TypeScript Best Practices** — Best practices for `.ts` and `.tsx` files.
- **Unslop** — Remove AI-generated code slop and clean up code style.

### Available cursor-team-kit Skills

Key skills include:

- **Check Compiler Errors** — Run compile and type-check commands and report failures.
- **Fix CI** — Find failing PR checks and apply focused fixes.
- **Fix Merge Conflicts** — Resolve merge conflicts non-interactively and validate build.
- **Loop on CI** — Monitor PR checks and fix failures until green.
- **New Branch and PR** — Create a fresh branch, complete work, and open a pull request.
- **Review and Ship** — Review branch for bugs, run tests, and open/update PR.
- **Run Smoke Tests** — Run Playwright smoke tests and verify fixes.
- **Verify This** — Verify a claim with fresh local evidence.
- **Weekly Review** — Produce a weekly synthesis of authored commits.
- **What Did I Get Done** — Summarize commits over a time period.

See `.cursor/skills-cursor/` and `.cursor/plugins/` for full skill documentation.

## Cursor Cloud specific instructions

### Services (POC core)

| Service | Command | Notes |
| --- | --- | --- |
| Postgres + pgvector | From repo root: `docker compose up -d postgres` | Required. Defaults match `backend/.env.example` (`education`/`education` on `:5432`). Wait for health before migrating. |
| Backend API | From `backend/`: `uv run uvicorn education_platform.main:app --reload --host 127.0.0.1 --port 8000` | After Postgres is up: `uv run alembic upgrade head`. API auto-seeds from `docs/curriculum/` when no topics exist. |
| Frontend | From `frontend/`: `npm run dev` | Needs `frontend/.env` with `VITE_API_BASE_URL=http://127.0.0.1:8000` (copy from `.env.example`). Dev server on `:5173`. |
| Ingest worker | Optional | `uv run python -m education_platform.workers` from `backend/` — only for admin PDF ingest. Not required for student quiz or admin materials browse. |
| Policy assistant | Admin `/admin/policy` | Needs `OPENROUTER_API_KEY` in `backend/.env` for LLM injection/validate/summarize stages. Without the key, heuristic injection + stub summarize still run; retrieval uses pgvector. |

Standard quality commands: root [`README.md`](README.md) and [`backend/README.md`](backend/README.md). CI runs backend (ruff, mypy, pytest, 80% coverage) and frontend (`npm test` / Vitest) in parallel. Prefer `npm run dev` over `npm run build` for day-to-day work — `tsc --noEmit` in the production build currently fails on two pre-existing issues unrelated to runtime.

### Demo accounts

Synthetic Al Noor school (password `demo1234`):

- Admin: `fatima.almansouri@alnoor.school`
- Teacher: `meera.krishnan@alnoor.school`
- Student: `student1@alnoor.school` (Adnan, Grade 6)
- Second student: `student97@alnoor.school` (Aisha Rahman, Grade 8)

`student@demo.school` and `admin@demo.school` are created only when `ENVIRONMENT=development` by the curriculum seed. They are not the login-page accounts.

### Gotchas

- `uv` must be on `PATH` (`$HOME/.local/bin` after Astral install). All Python work uses `uv` from `backend/`.
- Nested Docker in Cloud Agents may need `fuse-overlayfs` + `iptables-legacy` (see Cursor env-setup guidance) before `docker compose up -d postgres` works.
- Set `OPENROUTER_API_KEY` (and optionally `OPENROUTER_MODEL`) for live Policy assistant LLM calls; never commit the key.
- Pre-commit hooks live in `.pre-commit-config.yaml` (ruff on `backend/`); optional for agents, required quality gate is still the backend commands above.
