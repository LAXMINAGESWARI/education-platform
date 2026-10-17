# Architecture

Narrative companion to the [diagram-only views](./diagrams.md) and the
[HTML project overview](./project-overview.html). Component contracts and data model detail live in
[design docs](../design/README.md).

## System context

**CurioEd** serves administrators, teachers, and students within institution-scoped tenants. Clients
(React web today; React Native target) call a FastAPI backend over JWT-authenticated REST. Bounded
components include identity and access, academic structure, source curriculum, ingestion and
indexing, assessment, analytics,
student learning experience, grounded AI assistance, and platform infrastructure.

The [repository README](../../README.md) describes the **implemented POC slice**. Diagrams in this
folder remain **target** product views unless a POC overlay note says otherwise.

See [abstract system view](../design/00-abstract-system-view.md) for the full component map.

## Two-directory model

```text
SourceCurriculum (shared, admin-owned)
└── AcademicPeriod
    └── Grade
        └── Subject
            └── Topic
                └── Subtopic
                    ├── SourceMaterialVersions (published lesson content)
                    └── CommonMasteryQuiz (one per subtopic)

StudentLearningDirectory (private, per student)
└── AcademicPeriod
    └── Grade
        └── Subject
            └── Topic
                └── Subtopic
                    ├── SourceMaterialReference → points at published version (not a copy)
                    ├── MaterialProgress
                    ├── MasteryQuizAttempts
                    └── EvaluationSnapshots (four-pillar evidence)
```

Students with active Grade and Grade–Subject enrollments see published material through their
private directory. Teachers consume assigned-class evidence; administrators own publication and
enrollment policy.

## Authorization gate

Protected requests pass through a layered gate (see [diagram 4](./diagrams.md#4-authorization-gate)):

1. Authenticated and active account
2. Same institution (tenant)
3. Academic period permits access
4. Role and scope permit the action
5. Active Grade and Grade–Subject enrollment (students)
6. Content is published or results are released

Administrators may read the institution learning directory without student enrollments. Student
material, quiz, and attempt access requires active enrollments.

## POC vs target infrastructure

| Layer | POC (today) | Target (design) |
| --- | --- | --- |
| API | FastAPI + PostgreSQL | FastAPI + PostgreSQL |
| Curriculum storage | Markdown seed + Postgres rows | Object storage + versions |
| Search/AI | pgvector + admin policy retrieval (student grounded assistant Deferred) | pgvector + grounded retrieval |
| Queue | Postgres `ingest_jobs` (SKIP LOCKED) | Worker + durable queue |
| Blobs | Local `UPLOAD_DIR` | MinIO/S3 |

## Related documents

- [Architecture diagrams](./diagrams.md) — Mermaid target views with POC overlay notes
- [Project overview (HTML)](./project-overview.html) — readable narrative for product review
- [Design sequence](../design/README.md) — component specs and relational schema
- [Documentation hub](../README.md) — vision, reading paths, implementation matrix
