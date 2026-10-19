# Testing strategy

How CurioEd is tested, what runs on every PR, and what runs only on demand.
Paths are relative to the repo root. Anything marked **(new)** does not exist yet.

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
**no `timeout-minutes`** on either job (GitHub default: 360 min). Fix that first (section 7).

## 2. Integration tests: real app, real Postgres, stubbed LLM

**Rule:** integration tests go through the real FastAPI app (`education_platform.main:app`
via `fastapi.testclient.TestClient`, the `client` fixture in `backend/tests/conftest.py`)
against a real Postgres 16 + pgvector database migrated to `head`. Only the LLM edge is fake.

- **Database.** CI uses the `postgres` service container in `ci.yml`
  (`pgvector/pgvector:pg16`, `education_test`, `pg_isready` health check). Locally use
  `docker compose up -d postgres`. `conftest.py::migrated_database` runs Alembic once per
  session; `clean_db` truncates every table except `alembic_version` per test.
  Testcontainers is optional for local runs without Compose: a session fixture that starts
  `pgvector/pgvector:pg16` and exports `DATABASE_URL` before `migrated_database` runs.
  Do not use SQLite: RLS (`test_text_to_sql_row_level_security.py`), pgvector retrieval and
  check constraints only exist in Postgres.
- **LLM boundary = the `ai_platform` gateway.** PR #189 adds
  `backend/src/education_platform/ai_platform/gateway/` (`api.py` `complete` /
  `complete_sync`, `client.py` `bind_http_clients`, `registry.py` step profiles,
  `usage.py` metering to `llm_gateway_calls`, `flags.py` per-feature flags). Tests stub there,
  not inside feature code: bind an `httpx.MockTransport` with `bind_http_clients(...)`
  (pattern already used in `backend/tests/test_ai_gateway.py::_bind_async`) that replays
  recorded responses.
  - Recorded fixtures live in `backend/tests/fixtures/llm/<feature>/<step>/<case>.json`
    **(new)**, keyed by step name (e.g. `policy_chat.answer`, `policy_chat.guard`). The replay
    transport fails the test on any request it has no recording for, so a new call path
    cannot silently go live.
  - Jev (OpenRouter Decisions API) is not behind the gateway yet. Stub it at its client:
    the `Judge` protocol / `open_judge` used by `modules/generation/worker.py`, and
    `modules/guard/jev_client.post_decisions` (PR #187). Reuse
    `backend/tests/fixtures/jev_decision_response.json`.
  - Features not yet on the gateway (text-to-SQL, generation, feedback) keep their current
    seams (`core/llm.chat_completion*`, `classify_noul`) until their flag migrates.
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
| Upload PDF -> publish lesson + quiz | `tests/test_rag_ingest.py::test_admin_curriculum_upload_enqueues`, `tests/test_generation_publish.py::test_publish_is_idempotent_and_unlocks_student_material` | One chained test **(new)** `tests/test_regression_publish_journey.py`: upload fixture PDF -> worker claim (`process_generation_job_sync`) with gateway/Jev replay -> QA accept -> publish -> student sees lesson and quiz without answer keys |
| Policy chat | `tests/test_assistant_chat.py::test_chat_crud_and_message_without_openrouter` | Gateway-on variant with recorded `policy_chat.*` responses through `POST /api/v1/chats/{id}/messages` **(new)** |
| Text-to-SQL golden set with role scoping | `tests/test_text_to_sql_template_execution_integration.py`, `tests/test_text_to_sql_router.py::test_full_round_trip_reflects_the_teachers_real_scoped_data`, `tests/test_text_to_sql_row_level_security.py` | Parametrised golden rows **(new)** `tests/fixtures/text_to_sql_golden.yaml` (question, role, expected rows per role) built from the "golden eval row" comments in `modules/text_to_sql/config/intent_templates.yaml`; recorded SQL, real execution under RLS |
| At-risk list: page == assistant | `tests/test_at_risk_assistant_agreement.py::test_assistant_and_flag_endpoint_return_the_same_students`, `tests/test_at_risk_api.py` | none |
| Health probes live / ready | `tests/test_health.py` (PR #188 adds `/health/live`, `/health/ready`) | Real-DB-down case (see PR #188 below) |

## 4. Playwright e2e smoke

Specs exist (`frontend/e2e/workflow.spec.ts`: login, student lesson + quiz, teacher
review + assistant, admin PDF upload; `chats.spec.ts`: teacher assistant, admin policy chat).
They need a running stack and today tolerate a missing OpenRouter key by recording
`blocked`.

Plan: new CI job `e2e` in `ci.yml` (needs `quality`), Postgres service as in `quality`,
`alembic upgrade head`, API with `uvicorn` (gateway replay mode via env
`AI_GATEWAY_REPLAY_DIR=tests/fixtures/llm` **(new)**, no key), `npm run build && npx vite preview`,
`npx playwright install --with-deps chromium`, then
`PLAYWRIGHT_BASE_URL=http://127.0.0.1:4173 npx playwright test --grep @regression --project desktop`.
Upload `playwright-report/` and `e2e/artifacts/` on failure. Full both-project runs stay
nightly. Waits for API readiness with `curl --retry 30 --retry-delay 2 --retry-connrefused
http://127.0.0.1:8000/health/ready` so a dead API fails in ~60 s, not 240 s per test.

## 5. Migration tests

New file **(new)** `backend/tests/test_alembic_heads.py`:

1. **Single head.** `ScriptDirectory.from_config(cfg).get_heads()` has length 1. Fails at
   PR time when two branches add revisions off the same parent. This is live risk now:
   PR #186 `d6e7f8a9b0c1` and PR #189 `b3c4d5e6f7a8` both have `down_revision = "c5d6e7f8a9b0"`.
2. **Round trip on a seeded DB.** With `seeded_db` loaded, `downgrade` one step
   (and to the previous release tag), then `upgrade head`; assert row counts of core tables
   are unchanged and `alembic check` (autogenerate diff) is empty.
3. **Per-revision downgrade with data.** Revisions that change constraints insert a row the
   new constraint allows before downgrading (pattern: `tests/test_judge_migration.py`,
   `tests/test_migration_fix_double_prefixed_check_constraints.py`).

Avoid hard-coding the head id in tests (`test_ai_gateway.py::test_migration_creates_usage_table`
asserts `["b3c4d5e6f7a8"]`); compare with `get_current_head()` instead.

## 6. Later: k6 load test for the k8s cutover

`deploy/load/k6-smoke.js` **(new)**, run by a `workflow_dispatch` workflow against staging,
never on PRs. Target **300 req/s** constant arrival for 10 min, thresholds
`http_req_failed < 1%` and `http_req_duration p(95) < 800`. Mix: `/health/ready`,
login, materials list, quiz fetch, at-risk flags; LLM routes excluded (or replay mode only)
so load tests never spend money. Gate the cutover on two consecutive green runs.

## 7. How it runs in CI

| What | Workflow / job | Trigger | Timeouts |
| --- | --- | --- | --- |
| ruff, mypy, alembic upgrade, pytest (incl. `@regression`, migrations, stubbed-gateway integration) | `ci.yml` / `quality` | `pull_request` | job `timeout-minutes: 20` **(add)**; per-test `--timeout=120` via `pytest-timeout` **(add to dev group)**; DB wait below |
| Vitest + build | `ci.yml` / `frontend` | `pull_request` | `timeout-minutes: 10` **(add)** |
| Playwright `@regression` smoke | `ci.yml` / `e2e` **(new)** | `pull_request` | `timeout-minutes: 20`; Playwright `timeout: 240_000` per test (config), API-ready curl ~60 s |
| Full Playwright both projects | `nightly.yml` **(new)** | `schedule` + `workflow_dispatch` | `timeout-minutes: 45` |
| AI regression evals (paid) | `ai-evals.yml` **(new)** | `schedule` + `workflow_dispatch` only | `timeout-minutes: 30` + spend cap (section 8) |
| k6 | `load.yml` **(new)** | `workflow_dispatch` | `timeout-minutes: 20` |
| Deploy + health curl | `deploy.yml` / `deploy` | push to `main` | `timeout-minutes: 90` (exists) |

**Fail fast when the DB is missing.** The service health check gives up after
10 x 5 s. `conftest.py::migrated_database` already does `SELECT 1` and calls `pytest.fail`,
but `create_engine(sync_url, pool_pre_ping=True)` has no connect timeout, so a black-holed
host can hang for the OS TCP timeout. Add `connect_args={"connect_timeout": 5}` there, and a
`pg_isready -h localhost -t 30` step before `alembic upgrade head` in `quality`.
With `pytest-timeout`, any single hung test dies at 120 s with a stack dump.

## 8. AI regression layer (opt-in, nightly, paid)

Normal CI proves wiring with recorded responses. It cannot tell you whether a new model,
prompt or threshold is *worse*. That is this layer's job, and it is the only place live
model calls are allowed.

**Eval sets** (fixed, versioned, in the repo under `backend/evals/sets/` **(new)** unless noted):

| Feature | Set | Scorer |
| --- | --- | --- |
| Chat guard | `modules/guard/fixtures/golden_guard_rows.json` (PR #187): injection attacks (`inj-ignore`, `inj-dan`, ...) and benign teacher questions incl. "What subject do I teach?", "Am I currently teaching Grade 7?" | precision / recall / false-block rate at `jev_block_threshold`; runner `backend/scripts/eval_chat_guard.py` -> `modules/guard/eval_live.py` (PR #187) |
| Text-to-SQL | golden set (the rows referenced in `intent_templates.yaml`; source sheet `Text-to-SQL-Golden-Eval-Set-50_Set-2.xlsx` is **not committed**, export it to `text_to_sql_golden.yaml`) per role | execution-match vs expected rows under role scope + refusal correctness; extend `tests/test_text_to_sql_generate_sql_live.py` |
| Quiz item validity | frozen item bank `bank.json` (lessons + items) | Jev vs blind-solve agreement, reject rate: `python -m education_platform.modules.judge.compare bank.json`; `scripts/audit_quiz_items.py --dry-run` for stored items |
| Lesson grounding | frozen lesson sections + source chunks | Jev `grounded` / source-reject rate (`modules/judge/lesson_gate.py`, PR #186) |
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
  `usage.cost` from each response (gateway `usage.py` already reads it; Jev responses carry
  it in `DecisionsResponse.cost_usd`) and aborts the run at the cap. Also use a dedicated
  OpenRouter key with a provider-side credit limit. `eval_live.py` prints cost today but
  does not stop at a cap: add that before scheduling it.
- `timeout-minutes: 30`, `concurrency: ai-evals` (no overlap), report uploaded as an artifact.

## 9. Rules of thumb

- Every behaviour change ships with a test (AGENTS.md). New journeys get `@regression`.
- New LLM call sites go through the gateway and get a recorded fixture in the same PR.
- New migrations: single-head test must pass; constraint changes need a downgrade-with-data test.
- Never put a real key in CI for `ci.yml`; never call paid models from pytest by default.
