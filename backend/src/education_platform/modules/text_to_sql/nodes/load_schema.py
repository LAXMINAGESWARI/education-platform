"""Entry node: loads a filtered `schema_context` from schema_catalog.md.

The `assistant` module (chat_conversations, chat_messages), the `rag` module
(knowledge_documents, knowledge_document_versions, knowledge_chunks, ingest_jobs,
chunk_embeddings), the `audit_events` table (auth module), and `question_answer_keys`
(already unconditionally blocked by `apply_role_scope`'s blocklist and by DB-level GRANTs,
for every role) are out of scope for the text-to-SQL assistant and must never reach the LLM
as schema context — for any role, under any circumstances. `users.password_hash` and
`refresh_sessions.token_hash` are excluded the same way at the column level: those two
tables otherwise stay fully in scope, only these two columns never reach the LLM. None of
this changes enforcement — it exists purely so the model is never tempted to write a query
against something it can never actually read, wasting a generate/validate/retry cycle on a
query guaranteed to be rejected downstream.

This is enforced as a hard filter, not a prompt instruction: the excluded content is
structurally removed from the markdown before it is ever assigned to
`state["schema_context"]`, and a post-filter guard re-scans the result and refuses to
return it if any excluded table or column name survived. Read the catalog once (cached
thereafter, see `_load_filtered_schema_context`), not on every call.

Filtering approach, in order:
1. Structurally remove the `assistant` and `rag` `### Module: ...` sections of §2
   wholesale (every table under them is excluded).
2. Structurally remove the `audit_events` and `question_answer_keys` table blocks from
   within their module sections (their siblings — institutions, users, questions, ... —
   are kept).
3. Structurally remove the single `password_hash` row from inside the `users` block and
   the single `token_hash` row from inside the `refresh_sessions` block — both tables
   otherwise stay fully in scope.
4. Rewrite the handful of genuinely general §1/§7 bullets that happen to *mention* an
   excluded table only incidentally (as an example), so their real, still-applicable
   point survives without the excluded name attached. This has to happen before step 5,
   or the blanket line-drop would delete these bullets outright along with everything
   else that mentions an excluded name.
5. Blanket-drop every remaining single line (table row, FK line, glossary row, gotcha
   bullet) that mentions an excluded table name — safe here because every remaining
   such line in this catalog is self-contained (see the module-level tests for the one
   case, a glossary row mixing an excluded and a kept table, where this deliberately
   drops the whole row rather than trying to save half of it).
6. Remove the "Polymorphic references" intro sentence in §4, which does not itself name
   an excluded table but is left pointing at an empty code fence once step 5 has run
   (every line inside that fence names one), and collapse any empty code fences and
   excess blank lines left behind by the removals above.
7. Validate: no excluded table or column name survives anywhere in the result, every
   table that *should* survive still does, and the result isn't suspiciously short or
   missing its top-level section headers. Any failure here means the filter itself broke
   (e.g. schema_catalog.md's structure changed) — fail loudly rather than ship a silently
   gutted or leaky context.

Any `SchemaCatalogError` from the steps above (missing/unreadable file, or step 6's own
validation failing) is reported via `format_error(SCHEMA_ERROR, str(exc))`, the same
category-prefix convention every other node in this pipeline uses — this node used to set
`state["error"]` as a raw, unformatted string, which meant `error_category()` came back
`None` for it specifically and audit_log had to keep the raw text alongside the (missing)
category "so nothing gets lost." Now that this node formats its errors like everything
else, `error_category()` classifies it correctly too; `state["error"]`'s full text
(`SchemaCatalogError`'s message, which can include the real catalog file path) still goes
to `state["audit_entry"]`/logging only, never to the user-facing message — same no-leak
discipline as `EXECUTION_ERROR`, which `SCHEMA_ERROR` shares its generic honest_refusal
wording with (see `state.py`'s own docstring for why this got a new category rather than
reusing EXECUTION_ERROR's).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from education_platform.modules.text_to_sql.state import (
    SCHEMA_ERROR,
    TextToSQLState,
    format_error,
)

SCHEMA_CATALOG_PATH = Path(__file__).resolve().parent.parent / "config" / "schema_catalog.md"

# Tables that must never appear in schema_context, for any role, ever.
EXCLUDED_TABLES: tuple[str, ...] = (
    "chat_conversations",
    "chat_messages",
    "knowledge_documents",
    "knowledge_document_versions",
    "knowledge_chunks",
    "ingest_jobs",
    "chunk_embeddings",
    "audit_events",
    "question_answer_keys",
    # Account tables. `users` is NOT in this word list: foreign-key lines name
    # `users.id` as a target, and a whole-word scrub would delete those lines.
    # The users table block is removed explicitly, and lines that describe the
    # accounts table itself are rewritten in `_scrub_auth_account_lines`.
    "user_roles",
    "refresh_sessions",
)

# Columns that must never appear in schema_context, for any role, ever — the table they
# live on otherwise stays fully in scope, so these are stripped row-by-row rather than
# via EXCLUDED_TABLES's whole-table removal.
_EXCLUDED_COLUMNS: tuple[str, ...] = ("password_hash", "token_hash")

# Tables/views that must survive filtering. If any go missing, the filter over-stripped
# (most likely schema_catalog.md's structure changed under it) and load_schema should
# fail rather than silently ship a gutted context.
REQUIRED_TABLES: tuple[str, ...] = (
    "institutions",
    "student_profiles",
    "grades",
    "subjects",
    "academic_periods",
    "period_grades",
    "sections",
    "grade_subject_offerings",
    "topics",
    "subtopics",
    "learning_outcomes",
    "student_grade_enrollments",
    "student_subject_enrollments",
    "teaching_assignments",
    "source_materials",
    "source_material_versions",
    "source_chunks",
    "student_material_progress",
    "questions",
    "question_versions",
    "question_options",
    "question_outcome_tags",
    "common_mastery_quizzes",
    "quiz_versions",
    "quiz_items",
    "quiz_material_bindings",
    "quiz_releases",
    "quiz_attempts",
    "attempt_answers",
    "attendance_records",
    "student_360",
)

_MIN_LENGTH = 2000  # a real filtered catalog is tens of KB; anything this short is broken
_REQUIRED_HEADERS = (
    "## 1. Conventions",
    "## 2. Table Catalog",
    "## 3. Enum Reference",
    "## 4. Foreign Key Relationships",
    "## 5. Derived View",
    "## 6. Glossary",
    "## 7. Query Notes & Gotchas",
)


class SchemaCatalogError(Exception):
    """Raised when schema_catalog.md is missing, unreadable, or the filtered result is
    malformed (leaked excluded content, lost required content, or came out too short).
    """


def _excluded_pattern() -> re.Pattern[str]:
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in EXCLUDED_TABLES) + r")\b")


def _remove_module_section(text: str, module_name: str) -> str:
    """Remove an entire `### Module: \\`name\\` ...` §2 subsection, header through its
    last table, up to (not including) the next `---` separator.
    """
    pattern = re.compile(
        rf"### Module: `{re.escape(module_name)}`.*?(?=\n---\n)",
        re.DOTALL,
    )
    new_text, count = pattern.subn("", text, count=1)
    if count != 1:
        raise SchemaCatalogError(
            f"expected exactly one '{module_name}' module section in schema_catalog.md, "
            f"found {count} — has the document structure changed?"
        )
    return new_text


def _remove_table_block(text: str, table_name: str) -> str:
    """Remove one `#### \\`table_name\\` ...` block, up to (not including) the next
    table header or module separator.
    """
    pattern = re.compile(
        rf"#### `{re.escape(table_name)}`\n.*?(?=\n#### |\n---\n)",
        re.DOTALL,
    )
    new_text, count = pattern.subn("", text, count=1)
    if count != 1:
        raise SchemaCatalogError(
            f"expected exactly one '{table_name}' table block in schema_catalog.md, "
            f"found {count} — has the document structure changed?"
        )
    return new_text


def _remove_column_row(text: str, table_name: str, column_name: str) -> str:
    """Remove one `| \\`column_name\\` | ... |` row from inside the named table's
    `#### \\`table_name\\`` block only — scoped to that table, never a document-wide
    match, so a column name that happened to recur elsewhere would be left alone.
    """
    block_pattern = re.compile(
        rf"(#### `{re.escape(table_name)}`\n.*?)(?=\n#### |\n---\n)",
        re.DOTALL,
    )
    match = block_pattern.search(text)
    if match is None:
        raise SchemaCatalogError(
            f"expected to find table block '{table_name}' to strip column "
            f"'{column_name}' from, found none — has the document structure changed?"
        )
    block = match.group(1)
    row_pattern = re.compile(rf"\n\| `{re.escape(column_name)}` \|[^\n]*")
    new_block, count = row_pattern.subn("", block, count=1)
    if count != 1:
        raise SchemaCatalogError(
            f"expected exactly one '{column_name}' row inside the '{table_name}' "
            f"block, found {count} — has the wording changed?"
        )
    return text[: match.start(1)] + new_block + text[match.end(1) :]


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    """`str.replace`, but fails loudly if `old` isn't found exactly once — a silent
    no-op here would mean a bullet meant to be scrubbed of excluded-table names instead
    survives untouched into the blanket line-drop and gets deleted outright, quietly
    losing genuinely general content.
    """
    count = text.count(old)
    if count != 1:
        raise SchemaCatalogError(
            f"expected to find {label} exactly once in schema_catalog.md, found {count} "
            "— has the wording changed?"
        )
    return text.replace(old, new, 1)


def _scrub_general_bullets(text: str) -> str:
    """Rewrite the handful of §1/§7 bullets that are genuinely general (apply to the
    tables that remain after filtering) but happen to *mention* an excluded table only
    as an illustrative example. Must run before the blanket line-drop, or these bullets
    get deleted wholesale along with the ones that are actually only about excluded
    tables.
    """
    text = _replace_once(
        text,
        "- **Primary keys**: every table (except the `student_360` view and the single "
        "exception `chunk_embeddings`, noted below) has a UUID `id` primary key, "
        "Postgres type `uuid`, application-generated (`uuid4()`), column type `Uuid`.",
        "- **Primary keys**: every table (except the `student_360` view) has a UUID "
        "`id` primary key, Postgres type `uuid`, application-generated (`uuid4()`), "
        "column type `Uuid`.",
        label="the primary-keys convention bullet",
    )
    text = _replace_once(
        text,
        "- **Timestamps**: every table except `audit_events` and `chunk_embeddings` "
        "has `created_at` and `updated_at`, both `TIMESTAMP WITH TIME ZONE`, "
        "server-defaulted to `now()` (`updated_at` also updates `onupdate=now()`). "
        "`audit_events` has `created_at` only (append-only log, never updated); "
        "`chunk_embeddings` has neither (see below).",
        "- **Timestamps**: every table has `created_at` and `updated_at`, both "
        "`TIMESTAMP WITH TIME ZONE`, server-defaulted to `now()` (`updated_at` also "
        "updates `onupdate=now()`).",
        label="the timestamps convention bullet",
    )
    return text


def _scrub_auth_account_lines(text: str) -> str:
    """Remove the accounts table from the prompt without deleting every `users.id` FK.

    `users` stays out of `EXCLUDED_TABLES` on purpose (see that constant). These
    rewrites drop the lines that describe the accounts table itself and stop
    telling the model to join it for email or login.
    """
    text = _replace_once(
        text,
        "`institution_id` lives directly on `institutions`-adjacent tables (`users`, "
        "`student_profiles`, `grades`, `subjects`, `academic_periods`)",
        "`institution_id` lives directly on `institutions`-adjacent tables "
        "(`student_profiles`, `grades`, `subjects`, `academic_periods`)",
        label="the multi-tenancy convention bullet",
    )
    text = _replace_once(
        text,
        "- **`student_profiles` vs `users`**: a student's identity record is "
        "`student_profiles`, which *optionally* links to a `users` login row "
        '(`user_id`, nullable). Not every student has a login yet ("provisioned" '
        "students). Never assume `student_profiles.user_id IS NOT NULL`.\n",
        "- **Student identity**: a student's identity record is `student_profiles`. "
        "Login accounts are not available to this assistant. Never assume "
        "`student_profiles.user_id IS NOT NULL`.\n",
        label="the student-identity convention bullet",
    )
    text = _replace_once(
        text,
        "The canonical student identity record — distinct from `users` because a student "
        "may exist (be enrolled, tracked) before they have login credentials.",
        "The canonical student identity record. A student may exist (be enrolled, "
        "tracked) before they have login credentials. Login accounts are not queryable "
        "here.",
        label="the student_profiles intro",
    )
    text = _replace_once(
        text,
        "- **`student_profiles.user_id` is frequently `NULL`** (student provisioned but "
        "not yet given login credentials) — don't inner-join `users` when the question "
        "is just about the student profile/roster; use a `LEFT JOIN` if you need login "
        "info opportunistically.\n",
        "- **`student_profiles.user_id` is frequently `NULL`** (a student may be "
        "provisioned before they have login credentials). Roster and name questions are "
        "answered from `student_profiles` and `student_360`. Login accounts, email "
        "addresses, passwords, and sessions are not available to this assistant.\n",
        label="the student_profiles.user_id gotcha",
    )
    text = _replace_once(
        text,
        "| `user_status` | `users.status` | `provisioned`, `active`, `deactivated`, `archived` |\n",
        "",
        label="the user_status enum row",
    )
    text = _replace_once(
        text,
        "users.institution_id                              references institutions.id\n",
        "",
        label="the users.institution_id foreign key line",
    )
    text = _replace_once(
        text,
        "- **Role model**: a `users` row can hold multiple roles via `user_roles` "
        "(one row per role: `administrator`, `teacher`, `student`). Role is not a "
        "column on `users` — always join `user_roles`.\n",
        "- **Role model**: login roles and sessions are not available to this "
        "assistant. Answer roster questions from `student_profiles` and enrollments, "
        "not from an accounts table.\n",
        label="the role-model convention bullet",
    )
    text = _replace_once(
        text,
        '| "logged in recently" / "active session" | `refresh_sessions` where '
        "`revoked_at IS NULL AND expires_at > now()`. |\n",
        '| "logged in recently" / "active session" | Login sessions are not available '
        "to this assistant. |\n",
        label="the active-session glossary row",
    )
    return text


def _drop_lines_mentioning_excluded(text: str) -> str:
    """Drop every remaining single line that mentions an excluded table by name —
    §1's polymorphic-reference bullet, §3 enum rows and footnotes, §4 FK lines and the
    polymorphic-reference code block, §6 glossary rows, and §7 gotcha bullets. Safe as a
    blanket per-line rule because every one of those is a self-contained line, and
    `_scrub_general_bullets` already rewrote the few lines that mix excluded-table
    mentions with genuinely general, must-survive content.
    """
    excluded = _excluded_pattern()
    return "\n".join(line for line in text.split("\n") if not excluded.search(line))


def _tidy(text: str) -> str:
    """Drop the now-orphaned "Polymorphic references" intro sentence (its code fence
    was emptied by _drop_lines_mentioning_excluded, since every line inside named an
    excluded table), collapse any now-empty code fences, and collapse excess blank
    lines left behind by all of the above.
    """
    text = re.sub(
        r"\*\*Polymorphic references[^\n]*\n\n?",
        "",
        text,
    )
    text = re.sub(r"```\n\n+```\n", "", text)
    # Normalize blank-line runs first, so the separator-collapse pattern below (which
    # expects exactly one blank line between/after `---` markers) actually matches.
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Two adjacent module sections both getting removed (e.g. assistant then rag, back
    # to back) leaves their separators stacked; collapse any run of them to one.
    text = re.sub(r"(?:---\n\n){2,}", "---\n\n", text)
    return text.strip() + "\n"


def _filter_schema_catalog(raw: str) -> str:
    text = raw
    text = _remove_module_section(text, "assistant")
    text = _remove_module_section(text, "rag")
    text = _remove_table_block(text, "audit_events")
    text = _remove_table_block(text, "question_answer_keys")
    text = _remove_table_block(text, "users")
    text = _remove_table_block(text, "user_roles")
    text = _remove_table_block(text, "refresh_sessions")
    text = _scrub_general_bullets(text)
    text = _scrub_auth_account_lines(text)
    text = _drop_lines_mentioning_excluded(text)
    text = _tidy(text)
    _validate_filtered(text)
    return text


def _validate_filtered(text: str) -> None:
    if len(text) < _MIN_LENGTH:
        raise SchemaCatalogError(
            f"filtered schema_context is only {len(text)} chars — expected a full catalog"
        )
    for header in _REQUIRED_HEADERS:
        if header not in text:
            raise SchemaCatalogError(f"filtered schema_context is missing section {header!r}")
    for table in REQUIRED_TABLES:
        if not re.search(rf"\b{re.escape(table)}\b", text):
            raise SchemaCatalogError(
                f"filtered schema_context is missing required table/view {table!r} — "
                "the filter over-stripped"
            )
    leaked = _excluded_pattern().findall(text)
    if leaked:
        raise SchemaCatalogError(
            f"filtered schema_context still contains excluded table(s): {sorted(set(leaked))}"
        )
    for column in _EXCLUDED_COLUMNS:
        if re.search(rf"`{re.escape(column)}`", text):
            raise SchemaCatalogError(
                f"filtered schema_context still contains excluded column {column!r}"
            )


@lru_cache(maxsize=1)
def _load_filtered_schema_context() -> str:
    try:
        raw = SCHEMA_CATALOG_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaCatalogError(
            f"schema_catalog.md not readable at {SCHEMA_CATALOG_PATH}: {exc}"
        ) from exc
    return _filter_schema_catalog(raw)


async def load_schema(state: TextToSQLState) -> TextToSQLState:
    try:
        schema_context = _load_filtered_schema_context()
    except SchemaCatalogError as exc:
        return {**state, "error": format_error(SCHEMA_ERROR, str(exc))}
    return {**state, "schema_context": schema_context}
