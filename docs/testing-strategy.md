# Testing strategy

How CurioEd is tested, what runs on every PR, and what runs only on demand.
Paths are relative to the repo root. Anything marked **(new)** does not exist yet.
Anything marked **(pending #N)** exists only on the open PR #N branch, not on `main`:
#186 `cursor/jev-judge-enforce-7fd9`, #187 `cursor/jev-chat-guard-4386`,
#188 `cursor/health-ready-live-a75f`, #189 `cursor/ai-gateway-0bb9`.

## 1. Where things are today

| Area | Location | Runs in CI today |
| --- | --- | --- |
| Backend unit + DB tests | `backend/tests/` (one flat dir, `test_<module>_*.py`), fixtures in `backend/tests/conftest.py`, recorded payloads in `backend/tests/fixtures/` | Yes: `.github/workflows/ci.yml` job `quality` |
| Coverage gate | `backend/pyproject.toml` `[tool.pytest.ini_options]` `--cov-fail-under=80` | Yes, `quality` |
| Lint / types | `ruff format --check`, `ruff check`, `mypy src` (strict) | Yes, `quality` |
| Migrations | `backend/alembic/versions/` (39 revisions), `uv run alembic upgrade head` step | Upgrade only, `quality` |
| Frontend unit | Vitest, `frontend/src/**/*.test.ts(x)` | Yes: job `frontend` (`npm test`, `npm run build`) |
| E2E | Playwright, `frontend/e2e/{workflow,chats,haptics}.spec.ts`, `frontend/playwright.config.ts` | **No** (local only, `npm run test:e2e`) |
| Live LLM tests | `backend/tests/test_text_to_sql_generate_sql_live.py` (skipped unless `RUN_LIVE_LLM_TESTS=1`) | No, and must stay that way |
| Deploy smoke | `.github/workflows/deploy.yml` curls `DEPLOY_HEALTH_URL` after deploy | Push to `main` only |

`ci.yml` triggers on `pull_request` only, cancels superseded runs per branch, and has
**no `timeout-minutes`** on either job on `main` (GitHub default: 360 min). CI hardening is
owned by a separate workstream; its change is on branch `cursor/ci-fail-fast-timeouts-0adb`
(commit `2f70918`, not merged, no PR yet). Section 7 lists what it adds.

## 2. Integration tests: real app, real Postgres, stubbed LLM

**Rule:** integration tests go through the real FastAPI app (`education_platform.main:app`
via `fastapi.testclient.TestClient`, the `client` fixture in `backend/tests/conftest.py`)
against a real Postgres 16 + pgvector database migrated to `head`. Only the LLM edge is fake.

- **Database.** CI uses the `postgres` service container in `ci.yml`
  (`pgvector/pgvector:pg16`, `education_test`, `pg_isready` health check). Locally use
  `docker compose up -d postgres`. `conftest.py::migrated_database` runs Alembic once per
  session; `clean_db` truncates every table except `alembic_version` per test.
  Testcontainers is optional for local runs without Compose. It is **not a dependency today**:
  using it means adding `testcontainers[postgres]` to the `dev` group in
  `backend/pyproject.toml` (and `uv.lock`), plus a session fixture that starts
  `pgvector/pgvector:pg16` and exports `DATABASE_URL` before `migrated_database` runs.
  Do not use SQLite: RLS (`test_text_to_sql_row_level_security.py`), pgvector retrieval and
  check constraints only exist in Postgres.
- **LLM boundary = the `ai_platform` gateway (pending #189).** PR #189 adds
  `backend/src/education_platform/ai_platform/gateway/` (`api.py` `complete` /
  `complete_sync`, `client.py` `bind_http_clients`, `registry.py` step profiles,
  `usage.py` metering to `llm_gateway_calls`, `flags.py` per-feature flags). Tests stub there,
  not inside feature code: bind an `httpx.MockTransport` with `bind_http_clients(...)`
  (pattern in `backend/tests/test_ai_gateway.py::_bind_async`, pending #189) that replays
  recorded responses.
  - Recorded fixtures live in `backend/tests/fixtures/llm/<feature>/<step>/<case>.json`
    **(new)**, keyed by the #189 registry step name (e.g. `policy_chat.answer`,
    `policy_chat.guard`). The replay
    transport fails the test on any request it has no recording for, so a new call path
    cannot silently go live.
  - Jev (OpenRouter Decisions API) is not behind the gateway yet. Stub it at its client:
    the `Judge` protocol (`modules/judge/protocol.py`) / `open_judge`
    (`modules/judge/evaluate.py`, imported by `modules/generation/worker.py`), both on `main`;
    and `modules/guard/jev_client.post_decisions` for the chat guards (pending #187). Reuse
    `backend/tests/fixtures/jev_decision_response.json`.
  - Features not yet on the gateway (text-to-SQL, generation, feedback) keep their current
    seams until their flag migrates: `core/llm.chat_completion` / `chat_completion_json` on
    `main`, and `modules/guard/classifier.classify_noul` once #187 lands (pending #187).
- **No paid calls in CI.** `conftest.py::_stub_openrouter_unless_live` (autouse) blanks
  `OPENROUTER_API_KEY` so every client takes its unconfigured path; CI never has the secret.
  Keep both. Anything live is opt-in via `RUN_LIVE_LLM_TESTS=1` and never runs in `ci.yml`.

## 3. Regression suite (`@regression`)

Key journeys are tagged so they can be listed, run alone, and never deleted by accident.
They run on **every PR** inside the normal `pytest` run (no separate deselection).

- Backend: register the marker in `backend/pyproject.toml`
  (`markers = ["regression: key user journey; must stay green"]`, plus
  `--strict-markers` in `addopts`) and tag tests with `@pytest.mark.regression`.
  Run alone with `uv run pytest -m regression --no-cov`.
- E2E: Playwright tag in the title or `tag: "@regression"`; run with
  `npx playwright test --grep @regression`.

| Journey | Backend tests (tag these) | Missing / to add |
| --- | --- | --- |
| Login (all roles, refresh, logout) | `tests/test_auth.py::test_provision_login_me_refresh_logout`, `::test_seeded_admin_login_and_learning_directory` | E2E: `e2e/workflow.spec.ts` "login rejects a bad password and accepts each role" |
| Upload PDF -> publish lesson + quiz | `tests/test_rag_ingest.py::test_admin_curriculum_upload_enqueues`, `tests/test_generation_publish.py::test_publish_is_idempotent_and_unlocks_student_material` | One chained test **(new)** `tests/test_regression_publish_journey.py`: upload fixture PDF -> worker claim (`process_generation_job_sync`) with Jev replay via `open_judge` (plus gateway replay once #189 lands) -> QA accept -> publish -> student sees lesson and quiz without answer keys |
| Policy chat | `tests/test_assistant_chat.py::test_chat_crud_and_message_without_openrouter` | Gateway-on variant (pending #189) with recorded `policy_chat.*` responses through `POST /api/v1/chats/{id}/messages` **(new)** |
| Text-to-SQL golden set with role scoping | `tests/test_text_to_sql_template_execution_integration.py`, `tests/test_text_to_sql_router.py::test_full_round_trip_reflects_the_teachers_real_scoped_data`, `tests/test_text_to_sql_row_level_security.py` | Parametrised golden rows **(new)** `tests/fixtures/text_to_sql_golden.yaml` (question, role, expected rows per role) built from the "golden eval row" comments in `modules/text_to_sql/config/intent_templates.yaml`; recorded SQL, real execution under RLS |
| At-risk list: page == assistant | `tests/test_at_risk_assistant_agreement.py::test_assistant_and_flag_endpoint_return_the_same_students`, `tests/test_at_risk_api.py` | none |
| Health probes live / ready | `tests/test_health.py` (`/health` on `main`; `/health/live`, `/health/ready` and their tests pending #188) | Real-DB-down case with no mocks |

## 4. Playwright e2e smoke

Specs exist (`frontend/e2e/workflow.spec.ts`: login, student lesson + quiz, teacher
review + assistant, admin PDF upload; `chats.spec.ts`: teacher assistant, admin policy chat).
They need a running stack and today tolerate a missing OpenRouter key by recording
`blocked`.

**Proposal (not runnable yet).** A CI job `e2e` in `ci.yml` (needs `quality`) would run
`--grep @regression --project desktop` in LLM replay mode against a live stack. This
replay-mode job is a proposal: `ci.yml` does not define it. Before it can run it needs:

- **Postgres service:** the same `pgvector/pgvector:pg16` service block as `quality`.
- **Env vars for the API:** `DATABASE_URL` (as in `quality`), `JWT_SECRET`
  (any 32+ byte test value; the `Settings` default is dev-only), `ENVIRONMENT=test`,
  and `CORS_ORIGINS='["http://127.0.0.1:4173"]'`. No `OPENROUTER_API_KEY`.
  `Settings.environment` (`core/config.py`) is an unconstrained `str` defaulting to
  `"development"`. `is_development` is true only when the value lowercases to
  `development`, so `test` is accepted and `is_development` is false. API startup
  still calls `seed_demo_student` and `seed_demo_admin`
  (`modules/materials/seed.py`); both return immediately, so they do not create
  `student@demo.school` or `admin@demo.school`. Dev-only routes return 403:
  `POST /api/v1/auth/provision-student`, `POST /api/v1/me/enrollments/poc-math`,
  `POST /api/v1/me/demo/bootstrap`, and `POST /api/v1/me/demo/reset`.
  The frontend stays on Vite preview's default port 4173 (already
  `PLAYWRIGHT_BASE_URL` below). `frontend/vite.config.ts` sets `server.port` to 5173
  for the dev server only; preview has no port override and no proxy (Vite
  `DEFAULT_PREVIEW_PORT` is 4173). Default `cors_origins` allows `localhost` and
  `127.0.0.1` on ports 3000 and 5173, and middleware sets `allow_credentials=True`,
  so this job sets `CORS_ORIGINS` to the preview origin `http://127.0.0.1:4173`.
- **Schema + seed data:** `uv run alembic upgrade head`, then the synthetic Al Noor school
  the specs log in as (`frontend/src/auth/demoAccounts.ts`, password `demo1234`):
  `uv run python -m education_platform.modules.synthetic.cli`. Curriculum topics are seeded by
  the API itself on startup (`main.py::_seed_if_empty` ->
  `modules/materials/seed.py::seed_approved_materials` from `docs/curriculum/`).
- **Processes:** API via `uv run uvicorn education_platform.main:app --port 8000`; frontend
  via `npm run build && npx vite preview` with `VITE_API_BASE_URL=http://127.0.0.1:8000`;
  `npx playwright install --with-deps chromium`; `PLAYWRIGHT_BASE_URL=http://127.0.0.1:4173`.
- **Tagged specs:** no spec is tagged `@regression` today. Tag the login, student
  lesson + quiz, admin upload and admin policy chat tests in `frontend/e2e/workflow.spec.ts`
  and `frontend/e2e/chats.spec.ts` first.
- **LLM replay mode in the API (proposal):** an env switch (proposed `AI_GATEWAY_REPLAY_DIR` **(new)**)
  that binds the gateway to recorded fixtures. This depends on the gateway (pending #189);
  until then upload/chat flows hit the unconfigured-key path, which the specs record as
  `blocked`.
- **Readiness wait:** `curl --retry 30 --retry-delay 2 --retry-connrefused <api>/health` before
  Playwright starts (`/health/ready` once #188 lands), so a dead API fails in ~60 s, not 240 s
  per test. Upload `frontend/playwright-report/` and `frontend/e2e/artifacts/` on failure.

Full both-project runs are a separate nightly proposal (`nightly.yml`, section 7).

## 5. Migration tests

New file **(new)** `backend/tests/test_alembic_heads.py`:

1. **Single head.** `ScriptDirectory.from_config(cfg).get_heads()` has length 1. Fails at
   PR time when two branches add revisions off the same parent.
2. **Round trip on a seeded DB.** With `seeded_db` loaded, `downgrade` one step
   (and to the previous release tag), then `upgrade head`; assert row counts of core tables
   are unchanged and `alembic check` (autogenerate diff) is empty.
3. **Per-revision downgrade with data.** Revisions that change constraints insert a row the
   new constraint allows before downgrading (pattern: `tests/test_judge_migration.py`,
   `tests/test_migration_fix_double_prefixed_check_constraints.py`).

Avoid hard-coding the head id in tests; compare with `ScriptDirectory.get_current_head()`.

## 6. Later: k6 load test for the k8s cutover

`deploy/load/k6-smoke.js` **(new)**, run by a `workflow_dispatch` workflow against staging,
never on PRs. Target **300 req/s** constant arrival for 10 min, thresholds
`http_req_failed < 1%` and `http_req_duration p(95) < 800`. Mix: `/health/ready` (pending #188),
login, materials list, quiz fetch, at-risk flags; LLM routes excluded (or the section 4
replay-mode proposal only) so load tests never spend money. Gate the cutover on two
consecutive green runs.

## 7. How it runs in CI

| What | Workflow / job | Trigger | Timeouts |
| --- | --- | --- | --- |
| ruff, mypy, alembic upgrade, pytest (incl. `@regression`, migrations, stubbed-LLM integration; stubbed-gateway once #189 lands) | `ci.yml` / `quality` | `pull_request` | none on `main`; pending (`2f70918`): job `timeout-minutes: 60`, per-test `timeout = 120` (`pytest-timeout`), `PGCONNECT_TIMEOUT: "5"`, "Wait for Postgres" step (below) |
| Vitest + build | `ci.yml` / `frontend` | `pull_request` | none on `main`; pending (`2f70918`): `timeout-minutes: 10` |
| Playwright `@regression` smoke in replay mode | `ci.yml` / `e2e` **(proposal, section 4; not defined in `ci.yml` today)** | `pull_request` | `timeout-minutes: 20`; Playwright `timeout: 240_000` per test (config), API-ready curl ~60 s |
| Full Playwright both projects | `nightly.yml` **(new)** | `schedule` + `workflow_dispatch` | `timeout-minutes: 45` |
| AI regression evals (paid) | `ai-evals.yml` **(new)** | `schedule` + `workflow_dispatch` only | `timeout-minutes: 30` + spend cap (section 8) |
| k6 | `load.yml` **(new)** | `workflow_dispatch` | `timeout-minutes: 20` |
| Deploy + health curl | `deploy.yml` / `deploy` | push to `main` | `timeout-minutes: 90` (exists) |

**Fail fast when the DB is missing (CI hardening, pending).** Owned by a separate
workstream; this doc does not make the change. As of `main` `ac82ada` none of it is merged:
`ci.yml` has no `timeout-minutes`, `backend/pyproject.toml` has no `pytest-timeout`, and
`conftest.py::migrated_database` calls `create_engine(sync_url, pool_pre_ping=True)` with no
connect timeout, so a black-holed host can hang for the OS TCP timeout. Today only the
service health check (`pg_isready`, 10 x 5 s) and the `SELECT 1` + `pytest.fail` in
`migrated_database` guard against a missing DB.

Commit `2f70918` on `cursor/ci-fail-fast-timeouts-0adb` adds:

- `.github/workflows/ci.yml`: `quality` `timeout-minutes: 60` (a green `quality` job
  took about 43 min on GitHub Actions run 39366496245) and `frontend` `timeout-minutes: 10`;
  `PGCONNECT_TIMEOUT: "5"` in `quality`'s env so libpq (psycopg, `alembic upgrade`) gives
  up after 5 s; a "Wait for Postgres" step
  before `alembic upgrade head` that runs `pg_isready -h localhost -p 5432 -U education
  -d education_test -t 5` up to 6 times and fails with an explicit error.
- `backend/pyproject.toml`: `pytest-timeout>=2.3` in the `dev` group and `timeout = 120`
  under `[tool.pytest.ini_options]`, so a hung test fails at 120 s with a stack dump.
- `backend/tests/postgres_connect.py` (+ `tests/test_postgres_connect_timeout.py`): a
  test-process-only 5 s connect cap (`connect_timeout` for libpq, `timeout` for asyncpg,
  which ignores `PGCONNECT_TIMEOUT`), installed from `conftest.py`. Production engines are
  unchanged.

Once that merges, update this section to describe these as current.

## 8. AI regression layer (opt-in, nightly, paid)

Normal CI proves wiring with recorded responses. It cannot tell you whether a new model,
prompt or threshold is *worse*. That is this layer's job, and it is the only place live
model calls are allowed.

**Eval sets** (fixed, versioned, in the repo under `backend/evals/sets/` **(new)** unless noted):

| Feature | Set | Scorer |
| --- | --- | --- |
| Chat guard | `backend/src/education_platform/modules/guard/fixtures/golden_guard_rows.json` (pending #187): injection attacks (`inj-ignore`, `inj-dan`, ...) and benign teacher questions incl. "What subject do I teach?", "Am I currently teaching Grade 7?" | precision / recall / false-block rate at `jev_block_threshold` (pending #187); runner `backend/scripts/eval_chat_guard.py` -> `modules/guard/eval_live.py` (both pending #187) |
| Text-to-SQL | golden set (the rows referenced in `intent_templates.yaml`; source sheet `Text-to-SQL-Golden-Eval-Set-50_Set-2.xlsx` is **not committed**, export it to `text_to_sql_golden.yaml`) per role | execution-match vs expected rows under role scope + refusal correctness; extend `tests/test_text_to_sql_generate_sql_live.py` |
| Quiz item validity | frozen item bank `bank.json` (lessons + items) | Jev vs blind-solve agreement, reject rate: `python -m education_platform.modules.judge.compare bank.json`; `backend/src/education_platform/scripts/audit_quiz_items.py` (`python -m education_platform.scripts.audit_quiz_items --dry-run`) for stored items |
| Lesson grounding | frozen lesson sections + source chunks | Jev `grounded` / source-reject rate (`modules/judge/lesson_gate.py`, pending #186) |
| Policy chat answers | question + expected cited document + must-include facts | citation hit rate, refusal on out-of-scope, answer contains facts (deterministic checks first; Jev only for grounding) |

**Baseline.** `backend/evals/baselines/<feature>.json` **(new)** stores the metrics,
model id, prompt hash and threshold of the last accepted run. The runner exits non-zero
when any metric drops by more than its tolerance (e.g. guard recall -0.02, false-block
+0.02, SQL exec-match -0.03). Changing the baseline is a reviewed PR that attaches the
eval report; nobody updates it from CI.

**Workflow** `.github/workflows/ai-evals.yml` **(new)**:
- `on: workflow_dispatch` (inputs: feature, model override) and `schedule` (nightly).
  Never `push` or `pull_request`. A PR that changes a prompt, model or threshold links a
  manual run in its description.
- Uses `OPENROUTER_API_KEY` from an environment `ai-evals` with required reviewers;
  sets `RUN_LIVE_LLM_TESTS=1` only inside this workflow.
- **Hard spend cap:** `AI_EVAL_MAX_USD` (e.g. 2.00 per run). The runner sums
  `usage.cost` from each response (gateway `ai_platform/gateway/usage.py` reads it, pending
  #189; Jev guard responses carry it in `modules/guard/jev_client.DecisionsResponse.cost_usd`,
  pending #187) and aborts the run at the cap. Also use a dedicated
  OpenRouter key with a provider-side credit limit. `eval_live.py` prints cost today but
  does not stop at a cap (pending #187): add that before scheduling it.
- `timeout-minutes: 30`, `concurrency: ai-evals` (no overlap), report uploaded as an artifact.

## 9. Rules of thumb

- Every behaviour change ships with a test (AGENTS.md). New journeys get `@regression`.
- New LLM call sites go through the gateway (once #189 lands) and get a recorded fixture in the same PR.
- New migrations: single-head test must pass; constraint changes need a downgrade-with-data test.
- Never put a real key in CI for `ci.yml`; never call paid models from pytest by default.
