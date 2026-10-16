"""Unit tests for the text-to-SQL load_schema node's excluded-content filter.

No database needed for load_schema itself — it's pure file I/O plus string processing —
with one exception: test_load_schema_failure_routes_to_honest_refusal_via_graph runs the
full compiled graph, and audit_log (Task 10) now runs unconditionally at the end of every
full graph invocation regardless of which path was taken, so that one test needs Postgres
via the usual `clean_db` fixture plus a real seeded institution/user.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from education_platform.db.url import to_sync_url
from education_platform.modules.auth.models import Institution, User
from education_platform.modules.text_to_sql.graph import build_text_to_sql_graph
from education_platform.modules.text_to_sql.nodes.load_schema import (
    EXCLUDED_TABLES,
    REQUIRED_TABLES,
    SchemaCatalogError,
    _filter_schema_catalog,
    _load_filtered_schema_context,
    _validate_filtered,
    load_schema,
)
from education_platform.modules.text_to_sql.state import (
    SCHEMA_ERROR,
    TextToSQLState,
    error_category,
)


class _LoadSchemaModule(Protocol):
    """Typed view of load_schema.py's public-for-testing surface, for mypy's benefit —
    see the sys.modules note below for why this can't just be a normal import.
    """

    SCHEMA_CATALOG_PATH: Path


# `nodes/__init__.py` does `from .load_schema import load_schema as load_schema`, which
# rebinds the `nodes.load_schema` *attribute* from the submodule to the function. That
# makes `import ...nodes.load_schema as alias` resolve to the function, not the module
# (import-as on a dotted path walks attributes, unlike `from X import Y`). Reach the
# real submodule via sys.modules instead, to monkeypatch its globals below.
_MODULE = cast(
    _LoadSchemaModule, sys.modules["education_platform.modules.text_to_sql.nodes.load_schema"]
)


class _InjectionGuardModule(Protocol):
    async def chat_completion_json(
        self, messages: list[dict[str, str]], *, settings: object, temperature: float = 0.0
    ) -> dict[str, object]: ...


_INJECTION_GUARD_MODULE = cast(
    _InjectionGuardModule,
    sys.modules["education_platform.modules.text_to_sql.nodes.injection_guard"],
)


@pytest.fixture(autouse=True)
def _pass_injection_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """injection_guard is now the graph's entry node, ahead of load_schema, and makes its
    own live OpenRouter classifier call for any question its heuristic regex doesn't
    already catch -- including the one full-graph test in this file, which would
    otherwise reach a real, unmocked API before load_schema ever gets a chance to fail.
    Default every question here to "not an injection" so it doesn't depend on network
    availability to pass.
    """

    async def _fake(
        messages: list[dict[str, str]], *, settings: object, temperature: float = 0.0
    ) -> dict[str, object]:
        return {"injection": False}

    monkeypatch.setattr(_INJECTION_GUARD_MODULE, "chat_completion_json", _fake)


class _QuestionValidatorModule(Protocol):
    async def chat_completion_json(
        self, messages: list[dict[str, str]], *, settings: object, temperature: float = 0.0
    ) -> dict[str, object]: ...


_QUESTION_VALIDATOR_MODULE = cast(
    _QuestionValidatorModule,
    sys.modules["education_platform.modules.text_to_sql.nodes.question_validator"],
)


@pytest.fixture(autouse=True)
def _pass_question_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """question_validator is the node right after injection_guard and makes its own live
    OpenRouter classifier call for every question that reaches it. This file is about
    load_schema's own behavior, not question_validator's -- default every question here
    to "not off-topic" so these tests don't silently make a real, unmocked API call.
    """

    async def _fake_ot(
        messages: list[dict[str, str]], *, settings: object, temperature: float = 0.0
    ) -> dict[str, object]:
        return {"off_topic": False}

    monkeypatch.setattr(_QUESTION_VALIDATOR_MODULE, "chat_completion_json", _fake_ot)


@pytest.fixture()
def seeded_admin_user(clean_db: str) -> Iterator[tuple[UUID, UUID]]:
    """(institution_id, user_id) real rows -- needed only by the one full-graph test in
    this file, now that audit_log writes a real AuditEvent (FK-constrained to both) at
    the end of every invocation.
    """
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        inst = Institution(name="Load-Schema Test School", timezone="UTC")
        session.add(inst)
        session.flush()
        user = User(
            institution_id=inst.id,
            email="admin@load-schema-test.school",
            full_name="Admin",
            password_hash="unused",
        )
        session.add(user)
        session.commit()
        ids = (inst.id, user.id)
    engine.dispose()
    yield ids


def _raw_catalog() -> str:
    return _MODULE.SCHEMA_CATALOG_PATH.read_text(encoding="utf-8")


def _base_state(role: str = "admin") -> TextToSQLState:
    return {
        "question": "How many students passed the last quiz?",
        "user_id": "u1",
        "user_role": role,
        "schema_context": "",
        "error": None,
    }


# --- Excluded content never leaks, for any role ----------------------------------


@pytest.mark.parametrize("role", ["admin", "teacher", "student", "parent"])
async def test_no_excluded_table_reaches_schema_context_for_any_role(role: str) -> None:
    result = await load_schema(_base_state(role))
    assert result["error"] is None
    context = result["schema_context"]
    for table in EXCLUDED_TABLES:
        assert table not in context, f"{table!r} leaked into schema_context for role={role}"


@pytest.mark.parametrize("role", ["admin", "teacher", "student", "parent"])
async def test_password_hash_and_token_hash_columns_are_absent_for_any_role(role: str) -> None:
    result = await load_schema(_base_state(role))
    assert result["error"] is None
    context = result["schema_context"]
    assert "password_hash" not in context, f"leaked into schema_context for role={role}"
    assert "token_hash" not in context, f"leaked into schema_context for role={role}"


def test_account_tables_are_removed_and_roster_columns_survive() -> None:
    filtered = _filter_schema_catalog(_raw_catalog())
    assert "#### `users`" not in filtered
    assert "#### `user_roles`" not in filtered
    assert "#### `refresh_sessions`" not in filtered
    assert "#### `student_profiles`" in filtered
    for column in ("full_name", "student_identifier", "status"):
        assert f"`{column}`" in filtered, f"{column!r} should survive account-table removal"


def test_excluded_tables_cover_the_required_set() -> None:
    # Guards the test file itself against silent drift from the spec.
    assert set(EXCLUDED_TABLES) == {
        "chat_conversations",
        "chat_messages",
        "knowledge_documents",
        "knowledge_document_versions",
        "knowledge_chunks",
        "ingest_jobs",
        "chunk_embeddings",
        "audit_events",
        "question_answer_keys",
        "user_roles",
        "refresh_sessions",
    }


def test_assistant_and_rag_module_sections_fully_removed() -> None:
    filtered = _filter_schema_catalog(_raw_catalog())
    assert "Module: `assistant`" not in filtered
    assert "Module: `rag`" not in filtered


def test_audit_events_and_account_tables_removed_but_roster_kept() -> None:
    filtered = _filter_schema_catalog(_raw_catalog())
    assert "#### `audit_events`" not in filtered
    for table in ("users", "user_roles", "refresh_sessions"):
        assert f"#### `{table}`" not in filtered
    for table in ("institutions", "student_profiles"):
        assert f"#### `{table}`" in filtered, f"roster table {table!r} should survive"


# --- Required content survives ----------------------------------------------------


def test_all_required_tables_and_views_present() -> None:
    filtered = _filter_schema_catalog(_raw_catalog())
    for table in REQUIRED_TABLES:
        assert re.search(rf"\b{re.escape(table)}\b", filtered), f"{table!r} missing after filtering"


def test_academics_assessments_attendance_and_student_360_survive() -> None:
    filtered = _filter_schema_catalog(_raw_catalog())
    assert "### Module: `academics`" in filtered
    assert "### Module: `assessments`" in filtered
    assert "### Module: `attendance`" in filtered
    assert "## 5. Derived View: `student_360`" in filtered
    for table in (
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
    ):
        assert f"#### `{table}`" in filtered, f"academics table {table!r} missing"
    for table in (
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
    ):
        assert f"#### `{table}`" in filtered, f"assessments table {table!r} missing"
    assert "#### `attendance_records`" in filtered


def test_general_conventions_in_section_1_survive() -> None:
    filtered = _filter_schema_catalog(_raw_catalog())
    assert "## 1. Conventions" in filtered
    assert "**Multi-tenancy**" in filtered
    assert "**Versioning pattern**" in filtered
    assert "**Role model**" in filtered
    # The "Answer keys are server-only" bullet existed solely to describe the now
    # fully-excluded question_answer_keys table and is gone, not rewritten.
    assert "**Answer keys are server-only**" not in filtered
    # Rewritten (originally mentioned chunk_embeddings/audit_events as exceptions).
    assert "**Primary keys**" in filtered
    assert "**Timestamps**" in filtered
    # This bullet existed solely to introduce the now-fully-excluded polymorphic
    # columns (audit_events.entity_id, chunk_embeddings.*) and is gone, not rewritten.
    assert "Polymorphic references without a DB-level foreign key" not in filtered


def test_relevant_glossary_rows_survive() -> None:
    filtered = _filter_schema_catalog(_raw_catalog())
    for snippet in (
        '"passed" (a quiz attempt)',
        '"failed" (a quiz attempt)',
        '"attendance rate" / "attendance percentage"',
        '"enrolled in a subject"',
        '"a teacher\'s students"',
        '"logged in recently" / "active session"',
    ):
        assert snippet in filtered, f"glossary row {snippet!r} should survive"


def test_answer_key_glossary_row_removed() -> None:
    # This row was entirely about the now fully-excluded question_answer_keys table.
    filtered = _filter_schema_catalog(_raw_catalog())
    assert '"correct answer" / "answer key" for a question' not in filtered


def test_general_gotcha_bullets_survive_scrubbed_of_excluded_names() -> None:
    # The double-prefixed-CHECK-constraint gotcha bullet that used to live here was
    # removed from schema_catalog.md entirely (migration d0e1f2a3b4c5 fixed the bug it
    # described, and the root cause is fixed at the source too -- see db/base.py -- so
    # there is nothing left to warn a reader about). This test now only covers the
    # *other* general bullets that still need scrubbing to survive the excluded-table
    # line-drop.
    filtered = _filter_schema_catalog(_raw_catalog())
    assert "silently double-prefixed" not in filtered
    for snippet in (
        "mastery_percent` is `0`, not `NULL`",
        "Excused attendance is excluded from the denominator",
        "teaching_assignments.section_id IS NULL",
        "student_profiles.user_id` is frequently `NULL`",
        "Institution scoping",
    ):
        assert snippet in filtered, f"general gotcha {snippet!r} should survive"


def test_output_is_well_formed_markdown() -> None:
    filtered = _filter_schema_catalog(_raw_catalog())
    # No stacked separators or orphaned references left behind by the removals.
    assert "---\n\n---" not in filtered
    assert "Polymorphic references" not in filtered
    for header in (
        "## 1. Conventions",
        "## 2. Table Catalog",
        "## 3. Enum Reference",
        "## 4. Foreign Key Relationships",
        "## 5. Derived View",
        "## 6. Glossary",
        "## 7. Query Notes & Gotchas",
    ):
        assert header in filtered


# --- File is read once, cached thereafter -----------------------------------------


async def test_file_read_only_once_across_repeated_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    read_count = 0
    original_read_text = Path.read_text

    def _counting_read_text(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        nonlocal read_count
        if self == _MODULE.SCHEMA_CATALOG_PATH:
            read_count += 1
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", _counting_read_text)

    await load_schema(_base_state())
    await load_schema(_base_state())
    await load_schema(_base_state())

    assert read_count == 1


# --- Failure modes: error state, never a silent/broken context --------------------


async def test_missing_file_sets_error_not_a_broken_context(tmp_path: Path) -> None:
    original_path = _MODULE.SCHEMA_CATALOG_PATH
    _MODULE.SCHEMA_CATALOG_PATH = tmp_path / "does_not_exist.md"
    try:
        result = await load_schema(_base_state())
    finally:
        _MODULE.SCHEMA_CATALOG_PATH = original_path
        _load_filtered_schema_context.cache_clear()

    assert result["error"] is not None
    assert error_category(result["error"]) == SCHEMA_ERROR
    assert result.get("schema_context") in (None, "")


async def test_malformed_file_sets_error_not_a_broken_context(tmp_path: Path) -> None:
    bad_file = tmp_path / "broken.md"
    bad_file.write_text("not a real schema catalog, just some text", encoding="utf-8")
    original_path = _MODULE.SCHEMA_CATALOG_PATH
    _MODULE.SCHEMA_CATALOG_PATH = bad_file
    try:
        result = await load_schema(_base_state())
    finally:
        _MODULE.SCHEMA_CATALOG_PATH = original_path
        _load_filtered_schema_context.cache_clear()

    assert result["error"] is not None
    assert error_category(result["error"]) == SCHEMA_ERROR
    assert result.get("schema_context") in (None, "")


def test_filter_raises_on_empty_input() -> None:
    with pytest.raises(SchemaCatalogError):
        _filter_schema_catalog("")


def test_filter_raises_if_module_section_header_goes_missing() -> None:
    # Simulates schema_catalog.md's structure drifting under the filter: if the
    # `assistant` module header were ever renamed/reworded, the structural-removal step
    # can no longer find it and must fail loudly rather than silently leave the
    # (now unrecognized) assistant content — and its excluded tables — in the output.
    raw = _raw_catalog()
    mutated = raw.replace("### Module: `assistant`", "### Module: `assistant_renamed`", 1)
    with pytest.raises(SchemaCatalogError, match="assistant"):
        _filter_schema_catalog(mutated)


def test_validate_filtered_raises_if_a_required_table_goes_missing() -> None:
    # Unlike the structural guard above, this exercises _validate_filtered's own
    # completeness check directly: even if the structural removal steps all succeeded,
    # losing a required table anywhere along the way must still be caught.
    filtered = _filter_schema_catalog(_raw_catalog())
    mutated = filtered.replace("attendance_records", "renamed_table")
    with pytest.raises(SchemaCatalogError, match="attendance_records"):
        _validate_filtered(mutated)


async def test_load_schema_failure_routes_to_honest_refusal_via_graph(
    tmp_path: Path, seeded_admin_user: tuple[UUID, UUID]
) -> None:
    institution_id, user_id = seeded_admin_user
    original_path = _MODULE.SCHEMA_CATALOG_PATH
    _MODULE.SCHEMA_CATALOG_PATH = tmp_path / "missing.md"
    try:
        graph = build_text_to_sql_graph()
        initial: TextToSQLState = {
            "question": "q",
            "user_id": str(user_id),
            "user_role": "admin",
            "institution_id": str(institution_id),
            "schema_context": "",
            "generated_sql": None,
            "validated_sql": None,
            "retry_count": 0,
            "query_result": None,
            "result_row_count": None,
            "natural_answer": None,
            "confidence": None,
            "provenance": None,
            "error": None,
            "audit_entry": None,
        }
        result = await graph.ainvoke(initial, config={"recursion_limit": 10})
    finally:
        _MODULE.SCHEMA_CATALOG_PATH = original_path
        _load_filtered_schema_context.cache_clear()

    assert result["error"] is not None
    assert error_category(result["error"]) == SCHEMA_ERROR
    # generate_sql/validate_sql/sanity_check never ran: schema_context stayed empty.
    assert result["schema_context"] == ""
    # sanity_check itself never touched confidence (it never ran on this failure path),
    # but honest_refusal (Task 11) sets it to "low" whenever it's still unset by the time
    # a refusal is produced — the correct, spec'd behavior, not a bug.
    assert result["confidence"] == "low"
    # honest_refusal's fixed, category-keyed message reached the caller -- the real
    # exception text (which can carry the catalog file's real path) never did.
    assert result["natural_answer"] == "Something went wrong on our end — please try again."
    assert "missing.md" not in result["natural_answer"]
