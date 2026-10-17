# CurioEd design

This directory contains the evolving design documentation for the platform. Documents move from
high-level product decisions toward detailed component designs, contracts, data models, and
operational concerns.

The POC is built around two directories:

- **SourceCurriculum** — administrator-owned AcademicPeriod → Grade → Subject → Topic → Subtopic
  folders with published source materials and one common mastery quiz per subtopic.
- **StudentLearningDirectory** — private per-student view that references published sources and
  stores progress, attempts, and four-pillar evaluation snapshots.

Students with active Grade and Grade–Subject enrollments study the shared published material, take
the common subtopic quiz, and review marks, anonymized peer context, weak subtopics, and progress
over time. Teachers consume assigned-group evidence. Broader teacher workspace planning, parent
views, adaptive practice, and private dynamic materials are under
[future scope](./future-scope/README.md).

Start with the [HTML project overview](../architecture/project-overview.html) (open in a browser),
[architecture diagrams](../architecture/diagrams.md), the
[abstract system view](./00-abstract-system-view.md), and
[student learning experience](./01-student-learning-experience.md).

See the [documentation hub](../README.md) for vision, reading paths, and the full implementation
status matrix.

## Design sequence

0. [HTML project overview](../architecture/project-overview.html) — detailed readable summary
0b. [Architecture diagrams](../architecture/diagrams.md) — diagram-only target views
1. [Abstract system view](./00-abstract-system-view.md)
2. [Student learning experience](./01-student-learning-experience.md)
3. [Identity, tenancy, and authorization](./02-identity-tenancy-and-authorization.md)
4. [Academic structure, enrollment, and timetable](./03-academic-structure-enrollment-and-timetable.md)
5. [Material lifecycle and source curriculum](./04-material-lifecycle-and-ai-artifacts.md)
6. [Assessment: common subtopic mastery quizzes](./05-assessment-common-subtopic-mastery-quizzes.md)
7. [Analytics and comparison insights](./06-analytics-and-comparison-insights.md)
8. [Relational data model](./07-relational-data-model.md) —
   [interactive HTML schema browser](./relational-schema.html)
9. [At-risk early warning](./08-at-risk-early-warning.md)

## Future scope

Deferred product surfaces live under [`future-scope/`](./future-scope/README.md), starting with
[teacher workspace and academic planning](./future-scope/teacher-workspace-and-planning.md).

Later design topics still to document:

- Document ingestion and knowledge indexing — see [research/ai-rag-integration.md](../research/ai-rag-integration.md)
- AI assistant orchestration — see [research/ai-rag-integration.md](../research/ai-rag-integration.md)
- Notifications, forums, and certifications
- API and React / React Native client contract
- Security, privacy, and auditability
- Deployment, observability, and disaster recovery

## How to use these documents

Each component document should answer:

- What problem does the component solve?
- Who can use it and what are the authorization boundaries?
- What are the inputs, outputs, states, and failure modes?
- Which workflows and APIs are required?
- What data does it own and what data does it consume?
- How is correctness measured and tested?
- What is included in the POC, and what is deferred?

Decisions should be recorded with their rationale. When a design is uncertain, document the
assumption and the alternative instead of hiding the uncertainty in implementation code.
