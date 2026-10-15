# Education Platform — Schema Catalog

Generated from the SQLAlchemy models in `backend/src/education_platform/modules/{auth,academics,materials,assessments,attendance,assistant,rag}/models.py`, the `student_360` view definition in `backend/alembic/versions/d3e4f5a6b7c8_attendance_and_student_360.py`, and the `insights` module's read-only view binding (`backend/src/education_platform/modules/insights/models.py`).

This document is context for a **text-to-SQL LLM pipeline**. It is organized as:

1. [Conventions](#1-conventions) — patterns that apply across every table, so the model doesn't need to rediscover them per-query.
2. [Table Catalog](#2-table-catalog) — every table, every column, types, and descriptions.
3. [Enum Reference](#3-enum-reference) — every allowed string literal for enum-typed columns (use these exact values in `WHERE` clauses).
4. [Foreign Key Relationships](#4-foreign-key-relationships) — flat list of every FK edge.
5. [Derived View: `student_360`](#5-derived-view-student_360) — the pre-joined analytics view, with the exact SQL formulas it encodes.
6. [Glossary](#6-glossary-english--sql) — natural-language education terms mapped to precise SQL fragments.
7. [Query Notes & Gotchas](#7-query-notes--gotchas) — traps that produce subtly wrong results if ignored.

---

## 1. Conventions

- **Primary keys**: every table (except the `student_360` view and the single exception `chunk_embeddings`, noted below) has a UUID `id` primary key, Postgres type `uuid`, application-generated (`uuid4()`), column type `Uuid`.
- **Timestamps**: every table except `audit_events` and `chunk_embeddings` has `created_at` and `updated_at`, both `TIMESTAMP WITH TIME ZONE`, server-defaulted to `now()` (`updated_at` also updates `onupdate=now()`). `audit_events` has `created_at` only (append-only log, never updated); `chunk_embeddings` has neither (see below).
- **Polymorphic references without a DB-level foreign key**: a handful of columns point at "whichever table matches a sibling `*_kind`/`*_type` column" instead of a single fixed table. Postgres cannot enforce these as real FKs, and a plain `JOIN` on them will silently return rows for only one branch. They are listed individually in [§4](#4-foreign-key-relationships) and must be resolved with a `CASE`/`UNION ALL` keyed on the discriminator column, not a single join: `audit_events.entity_id` (keyed by `entity_type`, free text) and `chunk_embeddings.chunk_id` / `chunk_embeddings.doc_id` / `chunk_embeddings.version_id` (all keyed by `chunk_embeddings.doc_kind`). `ingest_jobs` used to belong on this list (`target_id`/`target_kind`) but was migrated to real per-kind FK columns instead — see [§2](#2-table-catalog) and [§7](#7-query-notes--gotchas).
- **Enums are stored as `VARCHAR`, not native Postgres enums** (`native_enum=False`). Compare with plain string literals, e.g. `status = 'active'`, not enum casts.
- **Soft state via status columns**: rows are essentially never hard-deleted. Lifecycle is modeled with `status`/`lifecycle_status` enum columns (e.g. `draft` → `published` → `archived`, or `active` → `withdrawn`). "Deleted" in natural language almost always means a status transition, not a missing row.
- **Versioning pattern**: several entities separate an immutable identity row from versioned content rows (`source_materials` → `source_material_versions`, `questions` → `question_versions`, `common_mastery_quizzes` → `quiz_versions`). The identity row's id never changes; only the current/published version's content is relevant for "what does the student see" queries — filter the version table on its lifecycle status (`published` / `released`), not just the newest `version_number`.
- **Multi-tenancy**: `institutions` is the tenant root. `institution_id` lives directly on `institutions`-adjacent tables (`users`, `student_profiles`, `grades`, `subjects`, `academic_periods`) and is transitively implied for everything else via those FKs. If a query needs to be institution-scoped, join up to the nearest table carrying `institution_id`.
- **Money/percent fields** are `Numeric`, not float — `score_percent`, `mastery_percent`, `attendance_percent`, `pass_threshold_percent` are all `Numeric(5,2)` or `Numeric(6,2)`, representing a percentage on a 0–100 scale (e.g. `70.00` = 70%).
- **`student_profiles` vs `users`**: a student's identity record is `student_profiles`, which *optionally* links to a `users` login row (`user_id`, nullable). Not every student has a login yet ("provisioned" students). Never assume `student_profiles.user_id IS NOT NULL`.
- **Role model**: a `users` row can hold multiple roles via `user_roles` (one row per role: `administrator`, `teacher`, `student`). Role is not a column on `users` — always join `user_roles`.
- **Answer keys are server-only**: `question_answer_keys` holds correct answers and must never be joined into a student-facing result set. Flag any query that would expose it to a student as suspicious.
- **Section names follow a `<grade number><letter>` convention** (`sections.name`), e.g. `8A`, `8B`, `9A` — a Grade 8 class named "Section A" is stored as `8A`, never as the spelled-out ordinal `Section A`/`Section 1`. A question phrased as "Grade 8 Section A" means `sections.name = '8A'`, not `sections.name = 'Section A'` — the grade number and section letter are one literal, not two words to concatenate literally.
- **`grades` never joins directly to `grade_subject_offerings`** — there is no such foreign key. The real chain is `grades ← period_grades → grade_subject_offerings`: `period_grades.grade_id` references `grades.id`, and separately `grade_subject_offerings.period_grade_id` references `period_grades.id`. Anything that needs both a grade and a subject/offering together must pass through `period_grades` as the intermediate table, e.g. "topics in Grade 8 Science" is `grades g JOIN period_grades pg ON pg.grade_id = g.id JOIN grade_subject_offerings gso ON gso.period_grade_id = pg.id JOIN subjects s ON s.id = gso.subject_id WHERE g.name = 'Grade 8' AND s.name = 'Science'` — never `grades g ON g.id = gso.period_grade_id`, which compares a grade's id against a period_grade's id and can never match.
- **`topics` are broad, per-offering top-level groupings; `subtopics` are the specific, nameable concepts underneath them** — confirmed against real seed data: the topic `'Mathematics Core'` contains subtopics `'Fractions'`, `'Decimals'`, `'Ratios'`, `'Percentages'`, and `'Algebra'`. A question naming a specific curriculum concept ("the Fractions topic", "the Photosynthesis unit") almost always means `subtopics.name`, not `topics.name` — `topics.name` values are broad, course-level labels (`'Mathematics Core'`, `'Science Core'`), not the answer to "which specific concept". To reach a subject/offering from a subtopic name: `subtopics st JOIN topics t ON t.id = st.topic_id JOIN grade_subject_offerings gso ON gso.id = t.grade_subject_offering_id JOIN subjects s ON s.id = gso.subject_id WHERE st.name = 'Fractions' AND s.name = 'Mathematics'`.

---

## 2. Table Catalog

### Module: `auth` (identity, tenancy, sessions, audit)

#### `institutions`
The tenant root. One row per school/organization.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` | DateTime(tz) | No | Row creation timestamp. |
| `updated_at` | DateTime(tz) | No | Last update timestamp. |
| `name` | String(200) | No | Institution display name. Unique across the table. |
| `timezone` | String(64) | No | IANA timezone string, default `"UTC"`. |
| `status` | Enum(`institution_status`) | No | `active` or `archived`. Default `active`. |

#### `users`
Login-capable accounts (admins, teachers, and students who have been provisioned a login).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `institution_id` | Uuid (FK) | No | Owning institution. |
| `email` | String(320) | No | Login email. Unique per `(institution_id, email)`. |
| `full_name` | String(200) | No | Display name. |
| `password_hash` | Text | No | Argon2 password hash. Never expose in query results. |
| `status` | Enum(`user_status`) | No | `provisioned` / `active` / `deactivated` / `archived`. Default `provisioned`. |

#### `user_roles`
Join table granting a user one or more roles. A user can be, e.g., both `administrator` and `teacher`.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `user_id` | Uuid (FK) | No | The user granted this role. |
| `role` | Enum(`role_name`) | No | `administrator` / `teacher` / `student`. Unique per `(user_id, role)`. |

#### `student_profiles`
The canonical student identity record — distinct from `users` because a student may exist (be enrolled, tracked) before they have login credentials.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. **This is the "student_id" referenced everywhere else.** |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `institution_id` | Uuid (FK) | No | Owning institution. |
| `user_id` | Uuid (FK, nullable) | Yes | Linked login account, if the student has been provisioned one. At most one student profile per `user_id` (partial unique index where not null). |
| `student_identifier` | String(100) | No | School-assigned student ID/roll number. Unique per `(institution_id, student_identifier)`. |
| `full_name` | String(200) | No | Student's display name. |
| `status` | Enum(`student_profile_status`) | No | `active` / `inactive` / `archived`. Default `active`. |

#### `refresh_sessions`
JWT refresh-token sessions for login persistence.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `user_id` | Uuid (FK) | No | Session owner. |
| `token_hash` | String(64) | No | Hash of the refresh token (unique, indexed). |
| `expires_at` | DateTime(tz) | No | Expiry timestamp. |
| `revoked_at` | DateTime(tz, nullable) | Yes | Set when the session was explicitly revoked (logout); `NULL` means still valid until `expires_at`. |

#### `audit_events`
Append-only log of administrative/system actions. **No `updated_at` — rows are never modified.**

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `institution_id` | Uuid (FK) | No | Institution the event belongs to. |
| `actor_user_id` | Uuid (FK, nullable) | Yes | The user who performed the action; `NULL` for system-initiated events. |
| `event_type` | String(100) | No | Machine-readable event name, e.g. `"material.published"`, `"quiz.released"`. |
| `entity_type` | String(100) | No | The kind of entity acted upon, e.g. `"source_material"`, `"quiz_version"`. |
| `entity_id` | Uuid (nullable) | Yes | The affected row's id (not a formal FK — polymorphic reference, target table depends on `entity_type`). |
| `payload` | JSON | No | Arbitrary structured event detail (default `{}`). |
| `created_at` | DateTime(tz) | No | When the event occurred. Server-defaulted to `now()`. |

---

### Module: `academics` (curriculum structure, enrollment, teaching assignments)

#### `grades`
A grade/year level (e.g. "Grade 6").

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `institution_id` | Uuid (FK) | No | Owning institution. |
| `name` | String(100) | No | Grade name, e.g. `"Grade 6"`. Unique per `(institution_id, name)`. |
| `sort_order` | Integer | No | Display ordering. Default `0`. |

#### `subjects`
A subject catalog entry (e.g. "Mathematics"), institution-wide (not tied to a grade).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `institution_id` | Uuid (FK) | No | Owning institution. |
| `name` | String(200) | No | Subject display name, e.g. `"Mathematics"`. |
| `code` | String(50) | No | Short subject code, e.g. `"MATH"`. Unique per `(institution_id, code)`. |

#### `academic_periods`
A term/school year (e.g. "2026 Term 1").

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `institution_id` | Uuid (FK) | No | Owning institution. |
| `name` | String(200) | No | Period name, e.g. `"2026 Term 1"`. Unique per `(institution_id, name)`. |
| `start_date` | Date | No | Period start date. |
| `end_date` | Date | No | Period end date. |
| `status` | Enum(`academic_period_status`) | No | `planned` / `active` / `closed` / `archived`. **At most one `active` period per institution at a time** (partial unique index). This is "the current term." |

#### `period_grades`
Join: which grades are offered in a given academic period.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `academic_period_id` | Uuid (FK) | No | The period. |
| `grade_id` | Uuid (FK) | No | The grade offered in that period. Unique per `(academic_period_id, grade_id)`. |

#### `sections`
A class section within a period+grade (e.g. "6A").

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `period_grade_id` | Uuid (FK) | No | The period+grade this section belongs to. |
| `name` | String(100) | No | Section name, e.g. `"6A"`. Unique per `(period_grade_id, name)`. |

#### `grade_subject_offerings`
Join: which subjects are taught in a given period+grade. This is the pivot most curriculum and enrollment data hangs off (often abbreviated "GSO").

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `period_grade_id` | Uuid (FK) | No | The period+grade. |
| `subject_id` | Uuid (FK) | No | The subject offered. Unique per `(period_grade_id, subject_id)`. |

#### `topics`
A curriculum topic within a subject offering (e.g. "Fractions").

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `grade_subject_offering_id` | Uuid (FK) | No | The subject offering this topic belongs to. |
| `name` | String(200) | No | Topic name, e.g. `"Fractions"`. |
| `slug` | String(100) | No | URL-safe identifier. Unique per `(grade_subject_offering_id, slug)`. |
| `sequence` | Integer | No | Display/teaching order. Unique per `(grade_subject_offering_id, sequence)`. |

#### `subtopics`
A curriculum subtopic within a topic (e.g. "Adding Fractions with Unlike Denominators"). **Subtopics are the unit that lessons and mastery quizzes attach to.**

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `topic_id` | Uuid (FK) | No | Parent topic. |
| `name` | String(200) | No | Subtopic name. |
| `slug` | String(100) | No | URL-safe identifier. Unique per `(topic_id, slug)`. |
| `sequence` | Integer | No | Display/teaching order. Unique per `(topic_id, sequence)`. |

#### `learning_outcomes`
A specific, taggable learning objective within a subtopic, used to tag quiz questions.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `subtopic_id` | Uuid (FK) | No | Parent subtopic. |
| `code` | String(50) | No | Short outcome code. Unique per `(subtopic_id, code)`. |
| `statement` | Text | No | Full text of the learning outcome. |
| `sequence` | Integer | No | Display order. Default `0`. |

#### `student_grade_enrollments`
A student's enrollment into a grade for a given academic period (and optionally a section).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `student_id` | Uuid (FK → `student_profiles.id`) | No | The enrolled student. |
| `academic_period_id` | Uuid (FK) | No | The period enrolled into. |
| `period_grade_id` | Uuid (FK) | No | The specific period+grade enrolled into. |
| `section_id` | Uuid (FK, nullable) | Yes | Assigned section, if any. |
| `status` | Enum(`enrollment_status`) | No | `active` / `withdrawn`. Default `active`. **At most one `active` grade-enrollment per `(student_id, academic_period_id)`** (partial unique index) — a student cannot be actively enrolled in two grades in the same period. |

#### `student_subject_enrollments`
A student's enrollment into a specific subject offering (implies grade enrollment). **This is the row most progress/quiz/attendance data joins through — often abbreviated "SSE".**

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `student_id` | Uuid (FK → `student_profiles.id`) | No | The enrolled student. |
| `grade_enrollment_id` | Uuid (FK → `student_grade_enrollments.id`) | No | The parent grade enrollment this subject enrollment belongs to. |
| `grade_subject_offering_id` | Uuid (FK) | No | The subject offering enrolled into. |
| `status` | Enum(`enrollment_status`) | No | `active` / `withdrawn`. Default `active`. **At most one `active` subject-enrollment per `(student_id, grade_subject_offering_id)`** (partial unique index). |

#### `teaching_assignments`
Which teacher is assigned to which subject offering (optionally scoped to one section; `NULL` section means "all sections").

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `teacher_user_id` | Uuid (FK → `users.id`) | No | The assigned teacher. |
| `academic_period_id` | Uuid (FK) | No | The period of the assignment. |
| `grade_subject_offering_id` | Uuid (FK) | No | The subject offering taught. |
| `section_id` | Uuid (FK, nullable) | Yes | Section scope; `NULL` = the whole offering (all sections). |
| `status` | Enum(`teaching_assignment_status`) | No | `active` / `ended`. Default `active`. |

---

### Module: `materials` (curriculum content and student progress)

#### `source_materials`
The identity record for a lesson/reading attached to a subtopic (content itself lives in versions).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `subtopic_id` | Uuid (FK) | No | The subtopic this material teaches. |
| `title` | String(200) | No | Material title. |
| `slug` | String(100) | No | URL-safe identifier. Unique per `(subtopic_id, slug)`. |
| `status` | Enum(`source_material_status`) | No | `draft` / `published` / `archived`. Default `draft`. |

#### `source_material_versions`
A specific version of a material's content. **Only the version with `lifecycle_status = 'published'` is what students see** (at most one published version per material, enforced by partial unique index).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `source_material_id` | Uuid (FK) | No | Parent material. |
| `version_number` | Integer | No | Sequential version number (≥1). Unique per `(source_material_id, version_number)`. |
| `lifecycle_status` | Enum(`source_material_version_status`) | No | `draft` / `processing` / `ready` / `published` / `failed` / `superseded` / `archived`. Default `draft`. |
| `title` | String(200) | No | Version title (may differ slightly from the parent's). |
| `content_markdown` | Text (nullable) | Yes | Rendered lesson content in Markdown, when authored directly (not via PDF ingest). |
| `content_format` | String(50) | No | Content format tag, default `"markdown"`. |
| `blob_object_key` | String(500, nullable) | Yes | Storage key for the original uploaded file (e.g. PDF), if ingested. |
| `blob_content_type` | String(100, nullable) | Yes | MIME type of the uploaded blob. |
| `checksum` | String(64, nullable) | Yes | Content checksum for dedup/integrity. |
| `failure_reason` | Text (nullable) | Yes | Populated when `lifecycle_status = 'failed'`. |
| `published_at` | DateTime(tz, nullable) | Yes | When this version became published. |

#### `source_chunks`
Normalized text chunks produced by ingesting a material version (used for RAG/embeddings elsewhere; also the addressable "page/section" granularity of a lesson).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `source_material_version_id` | Uuid (FK) | No | The material version this chunk was extracted from. |
| `ordinal` | Integer | No | 1-based position of the chunk within the version. |
| `text` | Text | No | Chunk text content. |
| `content_hash` | String(64) | No | Hash of `text`, for dedup. Unique per `(source_material_version_id, content_hash)`. |
| `page_number` | Integer (nullable) | Yes | Source PDF page number, if applicable. |
| `section_heading` | String(500, nullable) | Yes | Nearest heading above this chunk. |
| `token_count` | Integer (nullable) | Yes | Token count of `text` (for LLM context budgeting). |

#### `student_material_progress`
A student's progress against one material version, scoped to their subject enrollment.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `student_subject_enrollment_id` | Uuid (FK) | No | The student's enrollment this progress is tracked under. |
| `source_material_version_id` | Uuid (FK) | No | The (published, immutable) material version being tracked. Unique per `(student_subject_enrollment_id, source_material_version_id)`. |
| `status` | Enum(`material_progress_status`) | No | `opened` / `completed`. Default `opened`. |
| `opened_at` | DateTime(tz) | No | First time the student opened this material. |
| `last_opened_at` | DateTime(tz) | No | Most recent open timestamp. |
| `completed_at` | DateTime(tz, nullable) | Yes | Set when `status = 'completed'`; `NULL` otherwise (enforced by check constraint). |
| `last_unit_ordinal` | Integer (nullable) | Yes | Last reading position (chunk/slide ordinal) reached, ≥1 if set. |

---

### Module: `assessments` (questions, quizzes, attempts)

#### `questions`
Identity record for a question (content lives in versions).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `subtopic_id` | Uuid (FK) | No | The subtopic this question tests. |
| `code` | String(50, nullable) | Yes | Optional short reference code for the question bank. |

#### `question_versions`
A specific version of a question's content. **Only versions with `lifecycle_status = 'published'` are usable in a live quiz.**

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `question_id` | Uuid (FK) | No | Parent question. |
| `version_number` | Integer | No | ≥1. Unique per `(question_id, version_number)`. |
| `prompt` | Text | No | The question text shown to students. |
| `question_type` | Enum(`question_type`) | No | `multiple_choice` / `true_false` / `matching` / `numeric`. |
| `difficulty` | Enum(`question_difficulty`, nullable) | Yes | `easy` / `medium` / `hard`. |
| `marks` | Numeric(8,2) | No | Points this question is worth (≥0). Default `1.00`. |
| `explanation` | Text (nullable) | Yes | Post-answer explanation text. |
| `lifecycle_status` | Enum(`question_version_status`) | No | `draft` / `published` / `archived`. Default `draft`. |

#### `question_options`
The answer choices for a `multiple_choice`/`matching`-style question version.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `question_version_id` | Uuid (FK) | No | The question version this option belongs to. |
| `label` | String(10) | No | Option label, e.g. `"A"`, `"B"`. Unique per `(question_version_id, label)`. |
| `text` | Text | No | Option display text. |
| `sequence` | Integer | No | Display order. Default `0`. |

#### `question_answer_keys`
**Server-only.** The correct answer(s) for a question version. Never expose to student-facing endpoints/queries.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `question_version_id` | Uuid (FK, unique) | No | The question version this key answers. One key per question version. |
| `correct_option_label` | String(10, nullable) | Yes | Correct option label, for `multiple_choice`. |
| `correct_boolean` | Boolean (nullable) | Yes | Correct value, for `true_false`. |
| `correct_numeric` | Numeric(18,6, nullable) | Yes | Correct value, for `numeric`. |
| `correct_mapping` | JSON (nullable) | Yes | Correct mapping, for `matching`. |
| `scoring_rubric` | JSON (nullable) | Yes | Optional structured rubric for partial credit. |

#### `question_outcome_tags`
Join: which learning outcome(s) a question version assesses.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `question_version_id` | Uuid (FK) | No | The tagged question version. |
| `learning_outcome_id` | Uuid (FK) | No | The learning outcome it assesses. Unique per `(question_version_id, learning_outcome_id)`. |

#### `common_mastery_quizzes`
Identity record for a quiz. Targets **exactly one** subtopic or one topic (never both, never neither — enforced by check constraint).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `quiz_scope` | Enum(`quiz_scope`) | No | `subtopic_mastery` / `topic_mastery`. Default `subtopic_mastery`. |
| `subtopic_id` | Uuid (FK, nullable) | Yes | Set iff `quiz_scope = 'subtopic_mastery'`. At most one quiz per subtopic. |
| `topic_id` | Uuid (FK, nullable) | Yes | Set iff `quiz_scope = 'topic_mastery'`. At most one quiz per topic. |
| `title` | String(200) | No | Quiz display title. |

#### `quiz_versions`
A specific, gradeable version of a quiz's configuration. **Only versions with `lifecycle_status = 'released'` are live for students.**

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `quiz_id` | Uuid (FK) | No | Parent quiz. |
| `version_number` | Integer | No | ≥1. Unique per `(quiz_id, version_number)`. |
| `lifecycle_status` | Enum(`quiz_version_status`) | No | `draft` / `ready` / `released` / `archived`. Default `draft`. |
| `duration_seconds` | Integer (nullable) | Yes | Time limit, if any (>0 if set). |
| `max_attempts` | Integer (nullable) | Yes | Max attempts allowed per student, if capped (>0 if set). |
| `result_release_mode` | Enum(`quiz_result_release_mode`) | No | `immediate` (student sees score right after submitting) or `admin_release` (score withheld until an admin opens a `quiz_releases` window). Default `immediate`. |
| `pass_threshold_percent` | Numeric(5,2) | No | **The passing score threshold for this quiz version**, 0–100. Default `70.00`. This is the authoritative "passing score" — do not hardcode a global pass mark. |
| `released_at` | DateTime(tz, nullable) | Yes | When this version's lifecycle status became `released`. |

#### `quiz_items`
The ordered set of questions on a quiz version.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `quiz_version_id` | Uuid (FK) | No | The quiz version. |
| `question_version_id` | Uuid (FK) | No | The question version included. Combination unique per version; a question version also appears at most once per quiz version. |
| `sequence` | Integer | No | Position within the quiz (≥1). Unique per `(quiz_version_id, sequence)`. |

#### `quiz_material_bindings`
Join: which lesson material version(s) a quiz version is tied to (for "study this before you take the quiz" linking).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `quiz_version_id` | Uuid (FK) | No | The quiz version. |
| `source_material_version_id` | Uuid (FK) | No | The bound material version. Unique per pair. |

#### `quiz_releases`
An open/close window controlling when a quiz version is available to students (used for `admin_release` result mode and scheduled availability).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `quiz_version_id` | Uuid (FK) | No | The quiz version this release governs. At most one `open` release per quiz version (partial unique index). |
| `window_starts_at` | DateTime(tz, nullable) | Yes | When the window opens, if scheduled. |
| `window_ends_at` | DateTime(tz, nullable) | Yes | When the window closes, if scheduled. Must be ≥ `window_starts_at` if both set. |
| `released_by_user_id` | Uuid (FK → `users.id`, nullable) | Yes | The admin who opened/released it, if manual. |
| `status` | Enum(`quiz_release_status`) | No | `scheduled` / `open` / `closed`. Default `scheduled`. |

#### `quiz_attempts`
One student's attempt at a quiz version.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `student_id` | Uuid (FK → `student_profiles.id`) | No | The attempting student. |
| `student_subject_enrollment_id` | Uuid (FK, nullable) | Yes | The subject enrollment the attempt is scoped under. |
| `quiz_version_id` | Uuid (FK) | No | The quiz version attempted. |
| `quiz_release_id` | Uuid (FK, nullable) | Yes | The release window the attempt was taken under, if applicable. |
| `attempt_number` | Integer | No | 1-based attempt count for this student+quiz version. Unique per `(student_id, quiz_version_id, attempt_number)`. |
| `status` | Enum(`quiz_attempt_status`) | No | `not_started` / `in_progress` / `submitted` / `abandoned` / `expired` / `scored` / `released` / `held`. Default `not_started`. At most one `in_progress` attempt per `(student_subject_enrollment_id, quiz_version_id)`. |
| `started_at` | DateTime(tz, nullable) | Yes | When the student started the attempt. |
| `deadline_at` | DateTime(tz, nullable) | Yes | Attempt deadline, if timed. |
| `submitted_at` | DateTime(tz, nullable) | Yes | When the student submitted. |
| `scored_at` | DateTime(tz, nullable) | Yes | When the attempt was scored. |
| `score_raw` | Numeric(10,2, nullable) | Yes | Raw marks earned. |
| `score_percent` | Numeric(5,2, nullable) | Yes | Score as a 0–100 percentage. |
| `pass_threshold_percent` | Numeric(5,2, nullable) | Yes | Snapshot of the quiz version's pass threshold at scoring time (immutable even if the quiz's threshold later changes). |
| `passed` | Boolean (nullable) | Yes | **The authoritative pass/fail flag** — `TRUE` if `score_percent >= pass_threshold_percent` at scoring time, `NULL` if not yet scored. Prefer this column over recomputing from `score_percent`. |

#### `attempt_answers`
A student's answer to one question within one attempt.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `attempt_id` | Uuid (FK → `quiz_attempts.id`) | No | Parent attempt. |
| `question_version_id` | Uuid (FK) | No | The question answered. Unique per `(attempt_id, question_version_id)`. |
| `selected_option_label` | String(10, nullable) | Yes | Student's chosen option, for `multiple_choice`. |
| `selected_boolean` | Boolean (nullable) | Yes | Student's answer, for `true_false`. |
| `selected_numeric` | Numeric(18,6, nullable) | Yes | Student's answer, for `numeric`. |
| `selected_mapping` | JSON (nullable) | Yes | Student's answer, for `matching`. |
| `is_correct` | Boolean (nullable) | Yes | Whether this specific answer was correct. `NULL` until scored. |
| `marks_awarded` | Numeric(8,2, nullable) | Yes | Marks awarded for this answer. `NULL` until scored. |

---

### Module: `attendance`

#### `attendance_records`
One row per student per date, optionally scoped to a subject offering. A `NULL` `grade_subject_offering_id` means whole-day school attendance — this is what the platform's attendance-eligibility rule (75% across the term) is measured against.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `student_id` | Uuid (FK → `student_profiles.id`) | No | The student. |
| `academic_period_id` | Uuid (FK) | No | The period this record falls in. |
| `section_id` | Uuid (FK, nullable) | Yes | Section at time of recording, if applicable. |
| `grade_subject_offering_id` | Uuid (FK, nullable) | Yes | `NULL` = whole-day school attendance. Set = per-subject-period attendance. |
| `on_date` | Date | No | The calendar date. One whole-day record per `(student_id, on_date)` when `grade_subject_offering_id IS NULL`; one per-subject record per `(student_id, on_date, grade_subject_offering_id)` otherwise. |
| `status` | Enum(`attendance_status`) | No | `present` / `absent` / `late` / `excused`. Default `present`. **`present` and `late` both count as "attended"; `excused` is removed from the denominator entirely (does not count as attended or missed).** |
| `recorded_by_user_id` | Uuid (FK → `users.id`, nullable) | Yes | Who recorded this entry (typically a teacher), if known. |
| `note` | String(500, nullable) | Yes | Free-text note, e.g. reason for absence. |

---

### Module: `assistant` (admin-only policy chat)

Backs the admin "policy assistant" — a LangGraph-orchestrated chat that retrieves evidence from `rag` chunks and answers institution-policy questions. Not used by students or teachers.

#### `chat_conversations`
One chat thread, owned by one admin user.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `institution_id` | Uuid (FK) | No | Owning institution. |
| `owner_user_id` | Uuid (FK → `users.id`) | No | The admin who owns this conversation. |
| `title` | String(200) | No | Conversation title. Default `"New chat"`. |
| `context_used_tokens` | Integer | No | Running count of tokens consumed by this conversation's context window. Default `0`. |
| `context_limit_tokens` | Integer | No | Configured context budget for this conversation (mirrors `CHAT_CONTEXT_LIMIT_TOKENS` setting at creation time). Default `20000`. |
| `context_used_percent` | Integer | No | Stored, pre-computed `context_used_tokens / context_limit_tokens * 100`. **This is the authoritative value the UI reads — prefer it over recomputing, since it is written at the same time as `context_used_tokens` and won't drift from a stale `context_limit_tokens`.** Default `0`. |

#### `chat_messages`
One message (user, assistant, system, or tool) within a conversation.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps — `created_at` gives message order. |
| `conversation_id` | Uuid (FK → `chat_conversations.id`, `ON DELETE CASCADE`) | No | Parent conversation. Deleting a conversation deletes its messages. |
| `role` | Enum(`chat_message_role`) | No | `user` / `assistant` / `system` / `tool`. |
| `content` | Text | No | Message text. |
| `citations` | JSON (nullable) | Yes | Array of citation objects (`{id, label, excerpt, ...}`) attached to an assistant reply, referencing `source_chunks`/`knowledge_chunks` **by id value only, inside the JSON blob — not an enforced FK, and not a normalized join table.** Answering "which messages cite chunk X" requires a JSON containment/path query, not a `JOIN`. `NULL` for user/system messages. |
| `token_estimate` | Integer | No | Estimated token count of `content`. Default `0`. |

---

### Module: `rag` (knowledge documents, ingestion, embeddings)

Powers admin-uploaded policy/knowledge documents (parallel to `materials.source_materials`, but for non-curriculum reference documents) plus the shared ingest pipeline and vector index used by the `assistant` module's retrieval tool.

#### `knowledge_documents`
Identity record for an admin-uploaded knowledge document (content lives in versions — same pattern as `source_materials`).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `institution_id` | Uuid (FK) | No | Owning institution. |
| `title` | String(200) | No | Document title. |
| `slug` | String(100) | No | URL-safe identifier. Unique per `(institution_id, slug)`. |
| `doc_type` | String(50) | No | Free-text document category (e.g. `"policy"`, `"handbook"`) — not an enum. |
| `status` | Enum(`knowledge_document_status`) | No | `draft` / `active` / `archived`. Default `draft`. |
| `required_roles` | JSON | No | Array of role-name strings (e.g. `["administrator", "teacher"]`) gating which roles may retrieve this document via the assistant. **Not a normalized join table** — filtering "documents visible to role X" needs a JSON containment check, not a join. Default `["administrator", "teacher"]`. |

#### `knowledge_document_versions`
A specific version of a knowledge document's content. Same lifecycle pattern as `source_material_versions`: **only the version with `lifecycle_status = 'published'` is live** (at most one published version per document, enforced by partial unique index).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `document_id` | Uuid (FK → `knowledge_documents.id`) | No | Parent document. |
| `version_number` | Integer | No | ≥1. Unique per `(document_id, version_number)`. |
| `lifecycle_status` | Enum(`knowledge_document_version_status`) | No | `draft` / `processing` / `ready` / `published` / `failed` / `superseded` / `archived`. Default `draft`. |
| `blob_object_key` | String(500, nullable) | Yes | Storage key for the uploaded source file. |
| `blob_content_type` | String(100, nullable) | Yes | MIME type of the uploaded file. |
| `checksum` | String(64, nullable) | Yes | Content checksum for dedup/integrity. |
| `failure_reason` | Text (nullable) | Yes | Populated when `lifecycle_status = 'failed'`. |
| `published_at` | DateTime(tz, nullable) | Yes | When this version became published. |

#### `knowledge_chunks`
Normalized text chunks produced by ingesting a knowledge document version. Structurally identical to `materials.source_chunks`, but for knowledge documents rather than curriculum lessons.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps. |
| `knowledge_document_version_id` | Uuid (FK) | No | The document version this chunk was extracted from. |
| `ordinal` | Integer | No | 1-based position of the chunk within the version. |
| `text` | Text | No | Chunk text content. |
| `content_hash` | String(64) | No | Hash of `text`, for dedup. Unique per `(knowledge_document_version_id, content_hash)`. |
| `page_number` | Integer (nullable) | Yes | Source page number, if applicable. |
| `section_heading` | String(500, nullable) | Yes | Nearest heading above this chunk. |
| `token_count` | Integer (nullable) | Yes | Token count of `text`. |

#### `ingest_jobs`
Work-queue row for the standalone ingest worker (Postgres-only queue, no Redis — claimed via `FOR UPDATE SKIP LOCKED`). One job processes one material or document version through parsing → chunking → embedding. Target is an **exclusive arc**, not a polymorphic reference: two individually FK-constrained, nullable columns, with a `CHECK` requiring exactly one to be set. There is no separate `target_kind` discriminator — the non-null column alone tells you the job's kind, and it's DB-enforced end to end (unified onto the FK columns in migration `b9c0d1e2f3a4`; before that, this table used an unenforced `target_kind`/`target_id` pair like `chunk_embeddings.doc_kind` still does — see [§7](#7-query-notes--gotchas)).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | Uuid | No | Primary key — this is the "ingest job id" surfaced to the admin UI as an ingest-status badge. |
| `created_at` / `updated_at` | DateTime(tz) | No | Standard timestamps; `status` transitions update `updated_at`. |
| `source_material_version_id` | Uuid (FK → `source_material_versions.id`, nullable) | Yes | Set iff this job is ingesting a curriculum material version. Mutually exclusive with `knowledge_document_version_id` — exactly one of the two is non-null (`CHECK ck_ingest_jobs_exactly_one_target`). |
| `knowledge_document_version_id` | Uuid (FK → `knowledge_document_versions.id`, nullable) | Yes | Set iff this job is ingesting a knowledge document version. Mutually exclusive with `source_material_version_id`. |
| `status` | Enum(`ingest_job_status`) | No | `queued` / `running` / `succeeded` / `failed`. Default `queued`. |
| `error` | Text (nullable) | Yes | Failure detail, populated when `status = 'failed'`. |

#### `chunk_embeddings`
pgvector row holding one embedding per chunk, used for semantic retrieval by the `assistant` module. **Structural exception to the rest of the schema** — see [§7](#7-query-notes--gotchas).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `chunk_id` | Uuid | No | **Primary key of this table** (not `id`) — one embedding row per chunk, upserted on re-ingest. **Polymorphic reference, no DB-level FK**: references `source_chunks.id` when `doc_kind = 'source_material_version'`, or `knowledge_chunks.id` when `doc_kind = 'knowledge_document_version'`. |
| `embedding` | `Vector(384)` (pgvector) | No | The chunk's embedding, from the local `all-MiniLM-L6-v2` model (384 dimensions). Not comparable with standard SQL operators — similarity search requires pgvector distance operators (`<->` L2, `<=>` cosine, `<#>` inner product) in an `ORDER BY`, not a `WHERE`/`JOIN` predicate. |
| `doc_id` | Uuid | No | **Polymorphic reference, no DB-level FK.** References `source_materials.id` or `knowledge_documents.id`, keyed by `doc_kind`. |
| `doc_kind` | String(50) | No | `"source_material_version"` or `"knowledge_document_version"` (application-level `Literal`; not a DB enum type, but enforced by `CHECK (doc_kind IN ('source_material_version', 'knowledge_document_version'))`). Discriminates `chunk_id`, `doc_id`, and `version_id`. Matches `ingest_jobs.target_kind`'s vocabulary as of migration `a7b8c9d0e1f2` — prior to that migration this column used the unsuffixed forms `source_material` / `knowledge_document`; historical rows/exports predating it will need the same rename if reloaded. |
| `institution_id` | Uuid | No | Owning institution (no FK constraint declared, but always populated from `institutions.id` at write time). |
| `required_roles` | JSON | No | Snapshot of the owning document's `required_roles` at embedding time, used to filter retrieval results by the querying user's role without an extra join. |
| `doc_type` | String(50, nullable) | Yes | Snapshot of the owning document's `doc_type`/category at embedding time. |
| `page_number` | Integer (nullable) | Yes | Snapshot of the source chunk's page number. |
| `version_id` | Uuid | No | **Polymorphic reference, no DB-level FK.** References `source_material_versions.id` or `knowledge_document_versions.id`, keyed by `doc_kind`. |

---

## 3. Enum Reference

| Enum (DB name) | Column(s) | Allowed values |
|---|---|---|
| `institution_status` | `institutions.status` | `active`, `archived` |
| `user_status` | `users.status` | `provisioned`, `active`, `deactivated`, `archived` |
| `role_name` | `user_roles.role` | `administrator`, `teacher`, `student` |
| `student_profile_status` | `student_profiles.status` | `active`, `inactive`, `archived` |
| `academic_period_status` | `academic_periods.status` | `planned`, `active`, `closed`, `archived` |
| `enrollment_status` | `student_grade_enrollments.status`, `student_subject_enrollments.status` | `active`, `withdrawn` |
| `teaching_assignment_status` | `teaching_assignments.status` | `active`, `ended` |
| `source_material_status` | `source_materials.status` | `draft`, `published`, `archived` |
| `source_material_version_status` | `source_material_versions.lifecycle_status` | `draft`, `processing`, `ready`, `published`, `failed`, `superseded`, `archived` |
| `material_progress_status` | `student_material_progress.status` | `opened`, `completed` |
| `question_type` | `question_versions.question_type` | `multiple_choice`, `true_false`, `matching`, `numeric` |
| `question_difficulty` | `question_versions.difficulty` | `easy`, `medium`, `hard` |
| `question_version_status` | `question_versions.lifecycle_status` | `draft`, `published`, `archived` |
| `quiz_scope` | `common_mastery_quizzes.quiz_scope` | `subtopic_mastery`, `topic_mastery` |
| `quiz_version_status` | `quiz_versions.lifecycle_status` | `draft`, `ready`, `released`, `archived` |
| `quiz_result_release_mode` | `quiz_versions.result_release_mode` | `immediate`, `admin_release` |
| `quiz_release_status` | `quiz_releases.status` | `scheduled`, `open`, `closed` |
| `quiz_attempt_status` | `quiz_attempts.status` | `not_started`, `in_progress`, `submitted`, `abandoned`, `expired`, `scored`, `released`, `held` |
| `attendance_status` | `attendance_records.status` | `present`, `absent`, `late`, `excused` |
| `chat_message_role` | `chat_messages.role` | `user`, `assistant`, `system`, `tool` |
| `knowledge_document_status` | `knowledge_documents.status` | `draft`, `active`, `archived` |
| `knowledge_document_version_status` | `knowledge_document_versions.lifecycle_status` | `draft`, `processing`, `ready`, `published`, `failed`, `superseded`, `archived` |
| `ingest_job_status` | `ingest_jobs.status` | `queued`, `running`, `succeeded`, `failed` |

There is no `ingest_target_kind` enum anymore — `ingest_jobs` dropped its `target_kind`/`target_id` pair for two typed FK columns (see [§2](#2-table-catalog)); the exclusive-arc `CHECK` replaces what the enum used to gate.

**Not a DB enum type**, but DB-enforced via `CHECK (doc_kind IN ('source_material_version', 'knowledge_document_version'))`: `chunk_embeddings.doc_kind`. Same vocabulary `ingest_jobs` used before it moved to typed FKs — `chunk_embeddings` is now the only place in the schema still using this string-discriminator pattern instead of real foreign keys.

---

## 4. Foreign Key Relationships

```
users.institution_id                              references institutions.id
user_roles.user_id                                references users.id
student_profiles.institution_id                   references institutions.id
student_profiles.user_id                          references users.id
refresh_sessions.user_id                          references users.id
audit_events.institution_id                       references institutions.id
audit_events.actor_user_id                        references users.id

grades.institution_id                             references institutions.id
subjects.institution_id                           references institutions.id
academic_periods.institution_id                   references institutions.id
period_grades.academic_period_id                  references academic_periods.id
period_grades.grade_id                            references grades.id
sections.period_grade_id                          references period_grades.id
grade_subject_offerings.period_grade_id           references period_grades.id
grade_subject_offerings.subject_id                references subjects.id
topics.grade_subject_offering_id                  references grade_subject_offerings.id
subtopics.topic_id                                references topics.id
learning_outcomes.subtopic_id                     references subtopics.id

student_grade_enrollments.student_id              references student_profiles.id
student_grade_enrollments.academic_period_id      references academic_periods.id
student_grade_enrollments.period_grade_id         references period_grades.id
student_grade_enrollments.section_id              references sections.id
student_subject_enrollments.student_id            references student_profiles.id
student_subject_enrollments.grade_enrollment_id   references student_grade_enrollments.id
student_subject_enrollments.grade_subject_offering_id references grade_subject_offerings.id

teaching_assignments.teacher_user_id              references users.id
teaching_assignments.academic_period_id           references academic_periods.id
teaching_assignments.grade_subject_offering_id    references grade_subject_offerings.id
teaching_assignments.section_id                   references sections.id

source_materials.subtopic_id                      references subtopics.id
source_material_versions.source_material_id       references source_materials.id
source_chunks.source_material_version_id          references source_material_versions.id
student_material_progress.student_subject_enrollment_id references student_subject_enrollments.id
student_material_progress.source_material_version_id references source_material_versions.id

questions.subtopic_id                             references subtopics.id
question_versions.question_id                     references questions.id
question_options.question_version_id              references question_versions.id
question_answer_keys.question_version_id          references question_versions.id
question_outcome_tags.question_version_id         references question_versions.id
question_outcome_tags.learning_outcome_id         references learning_outcomes.id

common_mastery_quizzes.subtopic_id                references subtopics.id
common_mastery_quizzes.topic_id                   references topics.id
quiz_versions.quiz_id                             references common_mastery_quizzes.id
quiz_items.quiz_version_id                        references quiz_versions.id
quiz_items.question_version_id                    references question_versions.id
quiz_material_bindings.quiz_version_id            references quiz_versions.id
quiz_material_bindings.source_material_version_id references source_material_versions.id
quiz_releases.quiz_version_id                     references quiz_versions.id
quiz_releases.released_by_user_id                 references users.id

quiz_attempts.student_id                          references student_profiles.id
quiz_attempts.student_subject_enrollment_id       references student_subject_enrollments.id
quiz_attempts.quiz_version_id                     references quiz_versions.id
quiz_attempts.quiz_release_id                     references quiz_releases.id
attempt_answers.attempt_id                        references quiz_attempts.id
attempt_answers.question_version_id               references question_versions.id

attendance_records.student_id                     references student_profiles.id
attendance_records.academic_period_id             references academic_periods.id
attendance_records.section_id                     references sections.id
attendance_records.grade_subject_offering_id      references grade_subject_offerings.id
attendance_records.recorded_by_user_id            references users.id

chat_conversations.institution_id                 references institutions.id
chat_conversations.owner_user_id                  references users.id
chat_messages.conversation_id                     references chat_conversations.id  (ON DELETE CASCADE)

knowledge_documents.institution_id                references institutions.id
knowledge_document_versions.document_id           references knowledge_documents.id
knowledge_chunks.knowledge_document_version_id    references knowledge_document_versions.id

ingest_jobs.source_material_version_id            references source_material_versions.id  (nullable — exclusive arc, see below)
ingest_jobs.knowledge_document_version_id         references knowledge_document_versions.id  (nullable — exclusive arc, see below)
```

**Exclusive arc**: `ingest_jobs.source_material_version_id` and `ingest_jobs.knowledge_document_version_id` are both real, individually-enforced FKs, but exactly one is non-null per row (`CHECK ck_ingest_jobs_exactly_one_target`) — treat "which one is set" as the job's type discriminator. This is a straightforward `WHERE col IS NOT NULL` / `COALESCE`, not a polymorphic lookup, and needs no `CASE`/`UNION ALL` to join correctly — unlike the genuinely polymorphic columns below, which have no DB-level FK at all and do require one.

**Polymorphic references — not enforced as FK constraints in the database.** These must be resolved with a `CASE`/`UNION ALL` on the named discriminator column, never a plain join:

```
audit_events.entity_id           -> {many tables, keyed by entity_type (free text)}
chunk_embeddings.chunk_id        -> source_chunks.id                   when doc_kind = 'source_material_version'
                                  -> knowledge_chunks.id                when doc_kind = 'knowledge_document_version'
chunk_embeddings.doc_id          -> source_materials.id                when doc_kind = 'source_material_version'
                                  -> knowledge_documents.id             when doc_kind = 'knowledge_document_version'
chunk_embeddings.version_id      -> source_material_versions.id        when doc_kind = 'source_material_version'
                                  -> knowledge_document_versions.id     when doc_kind = 'knowledge_document_version'
chat_messages.citations           (JSON array; chunk ids referenced inside the blob, not via FK at all)
```

---

## 5. Derived View: `student_360`

`student_360` is a **read-only Postgres view** (not a table — no `id`-based writes, no FK constraints of its own) that pre-joins the analytics grain most dashboard/reporting/text-to-SQL questions want: **one row per student per subject-offering per academic period, for students currently actively enrolled in that subject.** Prefer querying this view over re-deriving the same joins/aggregates by hand whenever the question is about a student's overall standing in a subject.

Filter baked into the view: only rows where `student_subject_enrollments.status = 'active'` and `student_grade_enrollments.status = 'active'` are included — withdrawn enrollments never appear.

| Column | Type | Description |
|---|---|---|
| `student_id` | Uuid | `student_profiles.id`. |
| `institution_id` | Uuid | `student_profiles.institution_id`. |
| `student_identifier` | String | `student_profiles.student_identifier`. |
| `full_name` | String | `student_profiles.full_name`. |
| `user_id` | Uuid | `student_profiles.user_id` (nullable — no login yet if `NULL`). |
| `academic_period_id` | Uuid | The period this row is scoped to. |
| `academic_period` | String | Period name. |
| `grade_id` | Uuid | The student's grade in this period. |
| `grade` | String | Grade name. |
| `section_id` | Uuid (nullable) | The student's section, if assigned. |
| `section` | String (nullable) | Section name. |
| `subject_id` | Uuid | The subject this row is scoped to. |
| `subject` | String | Subject name. |
| `grade_subject_offering_id` | Uuid | The subject offering (grade+period+subject). |
| `student_subject_enrollment_id` | Uuid | The underlying active subject enrollment row. |
| `quizzes_taken` | Integer | Count of this student's quiz attempts in this subject with `status IN ('submitted','scored','released')` and a non-null `score_percent`. `0` if none. |
| `quizzes_passed` | Integer | Count of those attempts where `passed IS TRUE`. `0` if none. |
| `mastery_percent` | Numeric(6,2) | `AVG(score_percent)` across the counted attempts above, rounded to 2 decimals. `0` if no qualifying attempts (not `NULL`). **This is "the student's mastery score in this subject."** |
| `last_attempt_at` | DateTime(tz, nullable) | `MAX(submitted_at)` across the counted attempts. `NULL` if none. |
| `lessons_completed` | Integer | Count of `student_material_progress` rows (joined via this subject enrollment) with `status = 'completed'`. `0` if none. |
| `lessons_started` | Integer | Total count of `student_material_progress` rows for this subject enrollment (opened or completed). `0` if none. |
| `last_progress_at` | DateTime(tz, nullable) | `MAX(updated_at)` across this student's material-progress rows for the subject. |
| `days_present` | Integer | Count of **whole-day** (`grade_subject_offering_id IS NULL`) attendance records for this student in this academic period with `status IN ('present','late')`. `0` if none. |
| `days_counted` | Integer | Count of whole-day attendance records in this period with `status <> 'excused'` (i.e. `present`+`absent`+`late`; `excused` days are dropped from the denominator). `0` if none. |
| `attendance_percent` | Numeric(6,2, nullable) | `ROUND(days_present * 100.0 / days_counted, 2)`. `NULL` if `days_counted = 0` (no attendance data yet) — **do not treat `NULL` as `0%`.** |

---

## 6. Glossary (English → SQL)

Precise, LLM-usable mappings. Prefer the pre-computed `student_360` columns (`mastery_percent`, `attendance_percent`, `quizzes_passed`, `lessons_completed`) over hand-rolled aggregates whenever the question is about a student's standing in a subject — they encode the exact business rules below and are guaranteed consistent with the platform's own dashboards.

| English term | SQL equivalent |
|---|---|
| "passed" (a quiz attempt) | `quiz_attempts.passed = TRUE` — **do not** hardcode a percentage threshold; the threshold is per-quiz (`quiz_versions.pass_threshold_percent`, default 70%, snapshotted onto the attempt as `quiz_attempts.pass_threshold_percent`) and is already baked into this column. |
| "failed" (a quiz attempt) | `quiz_attempts.passed = FALSE` (only meaningful once scored; `passed IS NULL` means not yet scored — don't count those as failures). |
| "not yet scored" / "pending" attempt | `quiz_attempts.passed IS NULL` or `quiz_attempts.status NOT IN ('scored','released')`. |
| "a student's quiz score" (single attempt) | `quiz_attempts.score_percent` (0–100 scale). |
| "a student's mastery / average score in a subject" | `student_360.mastery_percent`, or manually: `AVG(quiz_attempts.score_percent)` over attempts joined via `student_subject_enrollments` where `quiz_attempts.status IN ('submitted','scored','released')` and `score_percent IS NOT NULL`. |
| "enrolled" (in a grade, for a period) | Row exists in `student_grade_enrollments` with `status = 'active'` for that `student_id` + `academic_period_id`. |
| "enrolled in a subject" | Row exists in `student_subject_enrollments` with `status = 'active'` for that `student_id` + `grade_subject_offering_id`. |
| "withdrawn" / "unenrolled" | `student_grade_enrollments.status = 'withdrawn'` or `student_subject_enrollments.status = 'withdrawn'` (row still exists — never deleted). |
| "completed" (a lesson/material) | `student_material_progress.status = 'completed'` (equivalently, `completed_at IS NOT NULL`). |
| "opened but not finished" (a lesson) | `student_material_progress.status = 'opened'`. |
| "hasn't started" (a lesson) | No row in `student_material_progress` for that `(student_subject_enrollment_id, source_material_version_id)`. |
| "published" (curriculum material students can see) | `source_materials.status = 'published'` **and** the corresponding `source_material_versions.lifecycle_status = 'published'` (at most one published version per material). |
| "live" / "released" (a quiz students can take) | `quiz_versions.lifecycle_status = 'released'`, and if `result_release_mode = 'admin_release'`, also check for a `quiz_releases` row with `status = 'open'` (and current time within `window_starts_at`/`window_ends_at` if set). |
| "attendance rate" / "attendance percentage" | `student_360.attendance_percent`, or manually: `COUNT(*) FILTER (WHERE status IN ('present','late')) * 100.0 / COUNT(*) FILTER (WHERE status <> 'excused')` over `attendance_records` for that student+period, restricted to `grade_subject_offering_id IS NULL` for whole-day attendance. |
| "present" (attendance) | `attendance_records.status IN ('present', 'late')` — late still counts as attended. |
| "absent" (attendance) | `attendance_records.status = 'absent'` (an `excused` absence is a distinct category — see below). |
| "excused absence" | `attendance_records.status = 'excused'` — excluded from both the numerator and denominator of attendance rate, i.e. does not count as present *or* absent. |
| "attendance-eligible" (POC rule: 75% threshold across the term) | `student_360.attendance_percent >= 75`. |
| "at risk" / "struggling" (a student) | `student_360.mastery_percent < 60 OR student_360.attendance_percent < 80` — matches `at_risk.engine.DEFAULT_THRESHOLDS`' calibrated level thresholds (60/80), approved for use by both the at-risk dashboard and the `students_needing_support` text-to-SQL template. **Distinct from "attendance-eligible" above** — that is a different, 75%-threshold POC rule for a different purpose; do not conflate the two. Note this captures only a single-reading ("level") check — the at_risk engine also has a mastery-*trend*-decline driver this view cannot reproduce (no per-attempt history here). |
| "doing well" / "performing well" (a student) | `student_360.mastery_percent >= 85 AND student_360.quizzes_passed >= 1` — matches the `students_meeting_performance_bar` text-to-SQL template's approved definition. No `at_risk`-engine equivalent exists for this positive bar (the engine only scores risk). |
| "current term" / "this school year" | The single `academic_periods` row with `status = 'active'` for the institution (at most one, enforced by the schema). |
| "a student's class" / "section" | Join `student_grade_enrollments.section_id → sections.id`; section name is `sections.name` (see §1's naming-convention note — `'8A'`, not `'Section A'`). |
| "Grade X" combined with a subject/offering (e.g. "Grade 8 Science") | Join through the intermediate table: `grades g JOIN period_grades pg ON pg.grade_id = g.id JOIN grade_subject_offerings gso ON gso.period_grade_id = pg.id`. Never join `grades.id` directly to `grade_subject_offerings` — no such foreign key exists (see §1's grade/offering-chain note). |
| "the `<X>` topic" / "the `<X>` unit" naming a specific curriculum concept (e.g. "the Fractions topic") | Almost always `subtopics.name = '<X>'`, not `topics.name` — `topics` are broad, per-offering groupings (e.g. `'Mathematics Core'`); `subtopics` are the specific concepts under them (e.g. `'Fractions'`). See §1's topic/subtopic note for the full join path down to subject/offering. |
| "a teacher's students" | `teaching_assignments` (matched on `teacher_user_id`, `grade_subject_offering_id`, and `section_id` if scoped) joined to `student_subject_enrollments` / `student_grade_enrollments` for the same offering (and section, if the assignment is section-scoped). |
| "administrator" / "admin" | `user_roles.role = 'administrator'` for that `user_id`. |
| "teacher" | `user_roles.role = 'teacher'`. |
| "student" (as a role, not the profile) | `user_roles.role = 'student'`. |
| "active student" | `student_profiles.status = 'active'`. |
| "a student's name" | `student_profiles.full_name`. |
| "correct answer" / "answer key" for a question | `question_answer_keys` — **server-only; never include in a query intended for a student-facing result.** |
| "which questions test outcome X" | Join `question_outcome_tags.learning_outcome_id → learning_outcomes.id`, filtered, then `question_outcome_tags.question_version_id → question_versions.id`. |
| "how many attempts has a student used" (on a quiz) | `COUNT(*)` from `quiz_attempts` for that `(student_id, quiz_version_id)`, or `MAX(attempt_number)` for the latest count; compare against `quiz_versions.max_attempts` (`NULL` = unlimited). |
| "audit trail for X" (e.g. a material, a quiz release) | `audit_events` filtered by `entity_type` and `entity_id`, ordered by `created_at`. |
| "who published/released X" | `audit_events.actor_user_id` for the relevant `event_type`/`entity_id`, or directly `quiz_releases.released_by_user_id` for quiz releases. |
| "who recorded attendance" | `attendance_records.recorded_by_user_id`. |
| "logged in recently" / "active session" | `refresh_sessions` where `revoked_at IS NULL AND expires_at > now()`. |
| "a knowledge document / policy document" | `knowledge_documents` (institution-uploaded reference material, distinct from curriculum `source_materials`). |
| "published" (a knowledge document) | `knowledge_documents.status = 'active'` **and** the corresponding `knowledge_document_versions.lifecycle_status = 'published'`. |
| "ingest status" / "processing status" (of an uploaded PDF/document) | `ingest_jobs.status`, matched via whichever of `source_material_version_id` / `knowledge_document_version_id` is non-null — not a column on the material/document table itself. |
| "a chat conversation" / "policy assistant thread" | `chat_conversations`, admin-only (`owner_user_id` always has role `administrator`). |
| "context usage" (of a chat) | `chat_conversations.context_used_percent` (stored, authoritative — don't recompute from tokens; see table notes). |
| "documents visible to a role" (e.g. "what can a teacher retrieve") | `knowledge_documents.required_roles` JSON array contains the role string — a containment check, not a join. |

---

## 7. Query Notes & Gotchas

- **`mastery_percent` is `0`, not `NULL`, when a student has no scored attempts** — but `attendance_percent` is `NULL` (not `0`) when there's no attendance data. Don't apply the same null-handling to both.
- **Versioned entities**: always filter to the *published*/*released* version when answering "what does the student currently see," not just `MAX(version_number)` — a newer draft version can exist without being live yet.
- **`quiz_attempts.pass_threshold_percent` is a snapshot**, taken at scoring time from `quiz_versions.pass_threshold_percent`. If a quiz's threshold changes later, past attempts' pass/fail does not retroactively change — always trust the attempt's own `passed`/`pass_threshold_percent`, not the current `quiz_versions` row, when analyzing historical results.
- **`common_mastery_quizzes` targets exactly one of `subtopic_id` or `topic_id`** — never assume both are populated; branch on `quiz_scope` first.
- **Excused attendance is excluded from the denominator**, not counted as absent — a naive `COUNT(*) WHERE status='absent' / COUNT(*)` will overcount absences relative to the platform's own definition.
- **`teaching_assignments.section_id IS NULL` means the teacher covers all sections** of that subject offering, not "no section" — don't filter it out when finding a teacher's full roster.
- **`student_profiles.user_id` is frequently `NULL`** (student provisioned but not yet given login credentials) — don't inner-join `users` when the question is just about the student profile/roster; use a `LEFT JOIN` if you need login info opportunistically.
- **Institution scoping**: this schema supports multiple institutions. Unless the question is explicitly cross-institution (admin/superuser context), scope by `institution_id` to avoid leaking another school's data into results.
- **`chunk_embeddings.doc_kind` is DB-enforced** (`CHECK (doc_kind IN ('source_material_version', 'knowledge_document_version'))`) — a raw insert with any other value is rejected at the database, not just the API layer.
- **`ingest_jobs` no longer has a `target_kind`/`target_id` pair.** As of migration `b9c0d1e2f3a4` it uses two typed, individually FK-constrained, nullable columns (`source_material_version_id`, `knowledge_document_version_id`) with an exclusive-arc `CHECK` — real foreign keys, not a string discriminator. `chunk_embeddings.doc_kind` is the last place in the schema still using the older string-discriminator pattern; don't assume the two tables' designs still match.
- **`chunk_embeddings` has no `id` column and no timestamps** — its primary key is `chunk_id` itself (one embedding per chunk; re-ingesting a chunk upserts in place rather than inserting a new row). A query assuming an `id`/`created_at` on this table, unlike every other table in the schema, will fail.
- **Vector similarity search is not expressible in generic SQL.** `chunk_embeddings.embedding` requires the pgvector extension's distance operators (`<->`, `<=>`, `<#>`) inside an `ORDER BY ... LIMIT` — a plain `WHERE embedding = ...` or numeric comparison is meaningless. If this text-to-SQL pipeline is expected to answer "find similar content" questions, it needs pgvector-aware prompting beyond standard SQL generation; otherwise, treat semantic-search questions as out of scope and route them to the assistant module's retrieval tool instead.
- **Three polymorphic ("tagged union") reference columns have no database-level FK constraint**: `audit_events.entity_id`, `chunk_embeddings.doc_id`, and `chunk_embeddings.version_id` (plus `chunk_embeddings.chunk_id`, its primary key). A generic FK-following join generator will either omit these entirely or guess wrong. Any query touching them must branch on the sibling discriminator column (`entity_type`, `doc_kind`) first, per the mapping in [§4](#4-foreign-key-relationships). `ingest_jobs`' equivalent columns are *not* in this category anymore — see above.
- **`chat_messages.citations` and `knowledge_documents`/`chunk_embeddings.required_roles` are untyped `JSON` (not `JSONB`)** — Postgres's plain `json` type has weaker operator/index support than `jsonb` (e.g. no `@>` containment index, slower `?`/`?|` key-existence checks). Structural questions about citation or role data require JSON path/functions (`json_array_elements`, `->>`, cast to `jsonb` at query time), not a join, and may be slow on large tables since there's no GIN index backing them.
