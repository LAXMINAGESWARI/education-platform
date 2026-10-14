# Backend

FastAPI application with SQLAlchemy, Alembic migrations, and Dockerized Postgres + pgvector.
See the [documentation hub](../docs/README.md) for product vision and implementation status.

## Stack

- **FastAPI** — REST API with OpenAPI docs at `/docs`
- **SQLAlchemy 2 (async)** — ORM; Postgres via `asyncpg` (API) and `psycopg` (Alembic/seed/worker)
- **Alembic** — schema migrations under `alembic/`
- **JWT** — access + refresh tokens (`python-jose`)
- **Postgres ingest queue** — `ingest_jobs` claimed with `FOR UPDATE SKIP LOCKED` (`workers/`)
- **pgvector** — chunk embeddings in the same Postgres database

## uv workflow

All Python commands run from `backend/` with `uv`:

```bash
# from repo root
docker compose up -d postgres

cd backend
uv sync
uv run alembic upgrade head
uv run python -m education_platform.modules.materials.seed
uv run uvicorn education_platform.main:app --reload
```

The API auto-seeds from `docs/curriculum/` on startup when no topics exist.

### Ingest worker

PDF ingest polls Postgres `ingest_jobs` (no Redis). Run a worker process:

```bash
# from repo root
docker compose up -d postgres

# from backend/ (separate terminal)
uv run python -m education_platform.workers
```

First Docling / sentence-transformers runs download models (can take several minutes). Uploads are
stored under `backend/var/uploads/`; embeddings live in Postgres `chunk_embeddings` (pgvector).

## Module rules

The backend is a modular monolith. Keep HTTP in `router.py`, logic in `service.py`, ORM in
`models.py`, and request/response models in `schemas.py`. Do not introduce Clean Architecture
folder trees or auto-discovered routers.

**Allowed**

- Anyone may import another module’s **models** (SQLAlchemy tables are shared persistence).
- A module may import another module’s **small query helpers** (for example `load_subtopic_node`,
  `subject_enrollment_for`).
- Routers are thin: parse/validate, call own service, map domain errors to HTTP, return schemas.
- `authorization` may import `academics.models` (scope is derived from enrollments/assignments).

**Forbidden**

- Service → another module’s **service** (this is the cycle).
- Services importing `fastapi.HTTPException` or `api.deps.Principal`.
- Routers defining the only copy of response models (put them in `schemas.py`).
- `__init__.py` importing `router` (keeps package imports cheap).

**Ownership**

| Module | Owns | May read |
| --- | --- | --- |
| auth | users, roles, institution, tokens | — |
| authorization | `Scope`, `Principal`, predicates | auth + academics models |
| academics | curriculum tree, enrollments, learning-directory composition | materials + assessments **models** |
| materials | source materials, versions, student progress, content seed | academics query helpers |
| assessments | quizzes, questions, attempts, quiz DTOs, question assembly | academics query helpers, materials **models** |
| rag | knowledge docs, ingest jobs, embeddings | materials models |
| assistant | chats, LangGraph | rag query/search, not materials service |
| insights | `student_360` reads + column register | attendance models, predicate |
| at_risk | flags + engine | insights view/register, predicate |
| authoring | draft generation use-case | academics + assessments models; shared LLM client |
| audit | `AuditEvent` + `record_event` | — |
| attendance | ORM only | — |
| synthetic | CLI seeder | models only |

**Cross-cutting**

- Errors: domain exception at service; routers (or a FastAPI handler) map to HTTP.
- Transactions: services do not commit; the request-scoped session commits on success.
- Authz: `Scope` is the single read model. Role deps for write/admin. SQL predicates for list
  endpoints. In-memory `covers_offering` for single-resource loads.
- Identity: `Principal` lives with authorization. `api.deps` is FastAPI wiring only.

## Module map

| Module | Responsibility |
| --- | --- |
| `modules/auth` | Login, refresh, logout, `/auth/me`, student provisioning |
| `modules/academics` | Enrollments, demo bootstrap, `GET /me/learning-directory` |
| `modules/materials` | Topics, lessons, progress, legacy flat `/materials` list, admin curriculum PDF upload |
| `modules/rag` | Knowledge-document upload/status, local blobs, chunking, embeddings, pgvector |
| `modules/assistant` | Admin policy multi-chat; OpenRouter + LangGraph; tool registry (`retrieve_chunks`) |
| `modules/assessments` | Quiz attempts, scoring (no answer keys on student responses) |
| `workers` | Postgres claim loop: DoclingDocument + HybridChunker → embed → index |

## Key endpoints

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/v1/auth/login` | Returns access + refresh tokens |
| `GET` | `/api/v1/auth/me` | Current user, roles, institution |
| `GET` | `/api/v1/me/enrollments` | Student enrollment summary |
| `POST` | `/api/v1/me/enrollments/poc-math` | Dev: enroll in seeded Grade 8 Math |
| `GET` | `/api/v1/me/learning-directory` | Primary catalog (grades → subjects → topics) |
| `GET` | `/api/v1/materials` | Legacy flat topic list |
| `GET` | `/api/v1/subtopics/{id}/material` | Lesson content |
| `GET` | `/api/v1/materials/{topic_id}/quiz` | Quiz without answer keys |
| `POST` | `/api/v1/quizzes/{id}/attempts` | Start attempt |
| `POST` | `/api/v1/attempts/{id}/submit` | Submit and score |
| `POST` | `/api/v1/me/demo/bootstrap` | Dev: quick-demo enrollments + progress |
| `POST` | `/api/v1/admin/subtopics/{id}/materials` | Admin: curriculum PDF ingest (202) |
| `GET` | `/api/v1/admin/material-versions/{id}` | Admin: curriculum ingest status |
| `POST` | `/api/v1/admin/knowledge-documents` | Admin: policy/handbook PDF ingest (202) |
| `GET` | `/api/v1/admin/knowledge-documents` | Admin: list knowledge docs |
| `GET` | `/api/v1/admin/knowledge-documents/{id}` | Admin: doc detail + versions |
| `GET` | `/api/v1/admin/knowledge-document-versions/{id}` | Admin: knowledge ingest status |
| `GET` | `/api/v1/insights/dashboard` | Headline numbers + per-subject mastery, narrowed by `Scope` |
| `GET` | `/api/v1/chats` | Admin: list policy conversations |
| `POST` | `/api/v1/chats` | Admin: create conversation |
| `GET` | `/api/v1/chats/{id}` | Admin: conversation + messages + context % |
| `POST` | `/api/v1/chats/{id}/messages` | Admin: send message (LangGraph turn) |
| `DELETE` | `/api/v1/chats/{id}` | Admin: delete conversation |

## Access rules

- **Students** require active Grade and Grade–Subject enrollments for material, quiz, and attempt
  access.
- **Administrators** may call `GET /me/learning-directory` without student enrollments.
- **Administrators** only for ingest upload/status routes (`require_administrator`).
- **Administrators** only for `/chats*` policy assistant routes.
- **Teachers** — role exists in schema; no teacher backend module or APIs yet (frontend uses
  fixtures).
- Student-facing quiz endpoints never expose answer keys or correct option labels.

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `MATERIALS_DIR` | `<repo>/docs/curriculum` | Markdown seed source |
| `DATABASE_URL` | Compose Postgres `education` DB | Async SQLAlchemy URL (`postgresql+asyncpg://...`) |
| `JWT_SECRET` | dev placeholder | Token signing |
| `DEMO_STUDENT_EMAIL` / `DEMO_ADMIN_EMAIL` | `@demo.school` accounts | Seeded demo users |
| `UPLOAD_DIR` | `backend/var/uploads` | Local PDF blobs |
| `EMBEDDING_MODEL_NAME` | `all-MiniLM-L6-v2` | Local embedding model |
| `MAX_UPLOAD_BYTES` | `20971520` (20MB) | Upload size cap |
| `OPENROUTER_API_KEY` | unset | Required for live LLM stages (Policy assistant, ADK generation) |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API base |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | Chat model id |
| `ADK_MODEL` | `openrouter/openai/gpt-4o-mini` | Google ADK LiteLlm model id |
| `ADK_MAX_REVIEW_ROUNDS` | `4` | Writer/reviewer loop cap |
| `CHAT_CONTEXT_LIMIT_TOKENS` | `20000` | Context meter denominator |
| `CHAT_RETRIEVAL_LIMIT` | `6` | Default `retrieve_chunks` top-k |

Pytest expects Compose Postgres and the `education_test` database (created by
`docker/postgres/init.sql`).

## Quality gate

```bash
cd backend
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run alembic upgrade head
uv run pytest
```

Coverage gate: 80%. Design contracts: [docs/design/](../docs/design/README.md).
