# CurioEd documentation

**CurioEd** is a multi-role system where administrators publish shared source curriculum, enrolled
students study through a private learning directory (reference-not-copy), and teachers consume class
evidence. The POC proves a focused loop:
publish → study → common subtopic quiz → four-pillar evaluation → teacher evidence.

Two complementary directories anchor the product model:

- **SourceCurriculum** — administrator-owned AcademicPeriod → Grade → Subject → Topic → Subtopic
  tree with published source materials and one common mastery quiz per subtopic.
- **StudentLearningDirectory** — private per-student view that references published sources and
  stores progress, attempts, and evaluation snapshots.

See [abstract system view](./design/00-abstract-system-view.md) for the full component model.

## Documentation map

| Path | For | Contents |
| --- | --- | --- |
| [architecture/](./architecture/README.md) | Everyone | Diagrams, HTML overview, target vs POC infrastructure |
| [design/](./design/README.md) | Product + engineering | Component specs, data model, future scope |
| [research/](./research/README.md) | AI/UI deep dives | RAG pipeline, UI paradigm research |
| [curriculum/](./curriculum/) | Admins + seeding | Approved markdown lesson/quiz source |
| [assets/](./assets/) | Design review | HTML mockups and moodboards |

## Reading paths

- **New developer:** [Repository README](../README.md) → [backend README](../backend/README.md) →
  design [02 identity](./design/02-identity-tenancy-and-authorization.md) +
  [07 relational data model](./design/07-relational-data-model.md) → run setup scripts
- **Product review:** [architecture/project-overview.html](./architecture/project-overview.html) →
  design [00 abstract system view](./design/00-abstract-system-view.md) →
  [01 student learning experience](./design/01-student-learning-experience.md)
- **AI/RAG work:** design [04 material lifecycle](./design/04-material-lifecycle-and-ai-artifacts.md) →
  [research/ai-rag-integration.md](./research/ai-rag-integration.md)

## Implementation status

Status legend: **Built** | **Partial** | **Mock** | **Deferred**

| Area | Status | Notes |
| --- | --- | --- |
| JWT auth (student + admin) | **Built** | Demo accounts seeded in development |
| Roles in schema/API | **Built** | `administrator`, `teacher`, `student` |
| Grade + Grade–Subject enrollment gate | **Built** | Students require active enrollments |
| `GET /me/learning-directory` | **Built** | Primary catalog API; admin bypasses enrollment |
| Curriculum seed from markdown | **Built** | [`docs/curriculum/`](./curriculum/) → Postgres |
| Student study + quiz + scoring | **Built** | No answer keys on student APIs |
| Legacy `GET /materials` | **Built** | Flat list; prefer learning-directory |
| Admin materials browser (`/admin/materials`) | **Built** | Live API; curriculum PDF upload + ingest status |
| Admin policy assistant | **Built** | Live `/chats*` + UI (LangGraph: inject → validate → retrieve → summarize); `OPENROUTER_API_KEY` optional for LLM stages (heuristic + stub summarize without it) |
| Teacher workspace (`/teacher/*`) | **Mock** | Frontend fixtures; no teacher backend APIs |
| Material upload / publish UI | **Partial** | Admin PDF ingest for curriculum + knowledge docs is live; full CMS / markdown publish editor still Deferred ([04](./design/04-material-lifecycle-and-ai-artifacts.md)) |
| Four-pillar evaluation snapshots | **Deferred** | Designed in [06](./design/06-analytics-and-comparison-insights.md); not in backend/frontend yet |
| Ingestion + vector indexing | **Built** | Admin PDF upload → Postgres `ingest_jobs` → chunks + pgvector; student grounded assistant still Deferred |
| Grounded AI assistant (student) | **Deferred** | Policy retrieval is live; student-facing grounded assistant is not |
| Postgres / pgvector | **Built** | Compose `pgvector/pgvector:pg16`; embeddings in `chunk_embeddings` |
| Postgres ingest workers | **Built** | `ingest_jobs` + `FOR UPDATE SKIP LOCKED`; local `UPLOAD_DIR` blobs (MinIO still Deferred) |
| Jev lesson and item judge | **Built** | Default `JEV_JUDGE_MODE=shadow` stores scores and leaves the gate unchanged. `enforce` is an explicit opt-in |
| MinIO object storage | **Deferred** | Local uploads for POC |
| React Native client | **Deferred** | Target in [architecture diagrams](./architecture/diagrams.md) |
