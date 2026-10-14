"""Unit tests for the text-to-SQL validate_sql node.

No database or network needed — sqlglot parsing and real (already-loaded) SQLAlchemy
metadata are enough to exercise every check.
"""

from __future__ import annotations

import pytest

from education_platform.modules.text_to_sql.nodes.load_schema import REQUIRED_TABLES
from education_platform.modules.text_to_sql.nodes.validate_sql import (
    DEFAULT_ROW_LIMIT,
    SCOPED_TABLES,
    validate_sql,
)
from education_platform.modules.text_to_sql.state import (
    LLM_ERROR,
    VALIDATION_ERROR,
    TextToSQLState,
    error_category,
    format_error,
)

EXCLUDED_TABLES = (
    "chat_conversations",
    "chat_messages",
    "knowledge_documents",
    "knowledge_document_versions",
    "knowledge_chunks",
    "ingest_jobs",
    "chunk_embeddings",
    "audit_events",
)


def _state(sql: str | None, **overrides: object) -> TextToSQLState:
    state: TextToSQLState = {
        "generated_sql": sql,
        "error": None,
        "retry_count": 0,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


async def _validated(sql: str) -> str:
    result = await validate_sql(_state(sql))
    assert result["error"] is None, f"expected success, got {result['error']!r}"
    validated = result.get("validated_sql")
    assert validated is not None
    return validated


async def _rejected(sql: str) -> str:
    result = await validate_sql(_state(sql))
    assert result.get("validated_sql") is None, "expected rejection, but got validated_sql"
    error = result.get("error")
    assert error is not None
    assert error_category(error) == VALIDATION_ERROR
    return error


async def test_validated_sql_retains_sqlalchemy_named_bind_syntax() -> None:
    validated = await _validated("SELECT id FROM institutions WHERE id = :current_user_id")

    assert ":current_user_id" in validated
    assert "%(current_user_id)s" not in validated
    assert "'current_user_id'" not in validated


# --- Scope: SCOPED_TABLES matches load_schema's REQUIRED_TABLES exactly ------------


def test_scoped_tables_matches_required_tables_exactly() -> None:
    assert set(SCOPED_TABLES.keys()) == set(REQUIRED_TABLES)


def test_scoped_tables_excludes_every_task_3_excluded_table() -> None:
    for table in EXCLUDED_TABLES:
        assert table not in SCOPED_TABLES


def test_student_360_view_is_in_scope() -> None:
    assert "student_360" in SCOPED_TABLES


# --- Step 1: parse -------------------------------------------------------------


async def test_garbage_sql_rejected_at_parse() -> None:
    error = await _rejected("this is not valid sql at all !!!")
    assert "failed to parse" in error.lower()


async def test_empty_string_rejected() -> None:
    result = await validate_sql(_state(""))
    assert error_category(result["error"]) == VALIDATION_ERROR
    assert "no sql was generated" in result["error"].lower()


async def test_none_generated_sql_with_no_prior_error_rejected() -> None:
    result = await validate_sql(_state(None))
    assert error_category(result["error"]) == VALIDATION_ERROR


async def test_none_generated_sql_with_prior_llm_error_passes_through_unchanged() -> None:
    prior = format_error(LLM_ERROR, "generate_sql: OpenRouter call failed: timeout")
    result = await validate_sql(_state(None, error=prior, retry_count=1))
    assert result["error"] == prior
    assert "validated_sql" not in result
    assert result["retry_count"] == 1


# --- Step 2: shape (single statement, single SELECT, no nested DML/DDL) ------------


async def test_valid_select_passes_shape_check() -> None:
    await _validated("SELECT id FROM quiz_attempts")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM quiz_attempts",
        "DROP TABLE quiz_attempts",
        "UPDATE quiz_attempts SET score_percent = 100",
        "INSERT INTO quiz_attempts (id) VALUES (1)",
    ],
)
async def test_non_select_statement_types_rejected(sql: str) -> None:
    error = await _rejected(sql)
    assert "select" in error.lower()


async def test_multi_statement_semicolon_smuggling_rejected() -> None:
    error = await _rejected("SELECT id FROM quiz_attempts; DROP TABLE quiz_attempts;")
    assert "multiple statements" in error.lower()


async def test_union_rejected() -> None:
    error = await _rejected("SELECT id FROM quiz_attempts UNION SELECT id FROM student_profiles")
    assert "union" in error.lower()


async def test_delete_nested_inside_cte_rejected_despite_select_root() -> None:
    # Root is Select; only walking the full tree catches the nested Delete.
    error = await _rejected("WITH x AS (DELETE FROM quiz_attempts RETURNING id) SELECT * FROM x")
    assert "delete" in error.lower()


async def test_drop_nested_inside_subquery_rejected() -> None:
    error = await _rejected(
        "SELECT id FROM quiz_attempts WHERE id IN (SELECT id FROM (DROP TABLE x) sub)"
    )
    # sqlglot may fail to parse this as invalid syntax, or may parse and expose a Drop
    # node — either way it must not validate successfully.
    assert error  # got here only via _rejected, which already asserts rejection


# --- Step 3: table whitelist against real SQLAlchemy metadata ---------------------


@pytest.mark.parametrize("table", EXCLUDED_TABLES)
async def test_every_excluded_table_rejected_even_if_hallucinated(table: str) -> None:
    error = await _rejected(f"SELECT id FROM {table}")
    assert table in error
    assert "not part of the available schema" in error


@pytest.mark.parametrize(
    "table",
    [
        "institutions",
        "users",
        "student_profiles",
        "grades",
        "subjects",
        "academic_periods",
        "source_materials",
        "source_material_versions",
        "questions",
        "common_mastery_quizzes",
        "quiz_attempts",
        "attendance_records",
        "student_360",
    ],
)
async def test_representative_kept_tables_all_pass(table: str) -> None:
    await _validated(f"SELECT * FROM {table}")


async def test_nonexistent_table_not_in_schema_at_all_rejected() -> None:
    error = await _rejected("SELECT id FROM totally_made_up_table")
    assert "totally_made_up_table" in error


async def test_excluded_table_hidden_inside_cte_still_caught() -> None:
    # This is the exact scenario the task calls "the actual backstop for the exclusion
    # decision" -- the model never saw chunk_embeddings in schema_context, but nothing
    # stops it from guessing the name, and this must still catch it.
    error = await _rejected("WITH bad AS (SELECT id FROM chunk_embeddings) SELECT * FROM bad")
    assert "chunk_embeddings" in error


async def test_excluded_table_hidden_inside_subquery_still_caught() -> None:
    error = await _rejected("SELECT id FROM quiz_attempts WHERE id IN (SELECT id FROM ingest_jobs)")
    assert "ingest_jobs" in error


async def test_cte_name_itself_not_treated_as_a_hallucinated_table() -> None:
    # "recent" is a CTE alias, not a real table -- must not be whitelist-checked.
    await _validated(
        "WITH recent AS (SELECT student_id, score_percent FROM quiz_attempts) "
        "SELECT r.student_id, r.score_percent FROM recent r"
    )


async def test_derived_table_subquery_alias_not_treated_as_hallucinated_table() -> None:
    await _validated("SELECT sub.x FROM (SELECT id AS x FROM student_profiles) sub")


async def test_recursive_cte_self_reference_does_not_break() -> None:
    await _validated(
        "WITH RECURSIVE t AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM t WHERE n < 5) "
        "SELECT * FROM t"
    )


async def test_unquoted_table_name_is_case_insensitive() -> None:
    # Matches Postgres's own fold-to-lowercase behavior for unquoted identifiers.
    await _validated("SELECT id FROM Quiz_Attempts")


async def test_quoted_table_name_is_case_sensitive_and_rejected() -> None:
    # A double-quoted "Quiz_Attempts" is a *different* identifier from quiz_attempts in
    # real Postgres, so this must be rejected, not silently folded to match.
    error = await _rejected('SELECT id FROM "Quiz_Attempts"')
    assert "Quiz_Attempts" in error


# --- Step 4: column existence ---------------------------------------------------


async def test_select_star_is_allowed() -> None:
    await _validated("SELECT * FROM quiz_attempts")


async def test_valid_qualified_column_passes() -> None:
    await _validated("SELECT qa.score_percent FROM quiz_attempts qa")


async def test_invalid_qualified_column_rejected() -> None:
    error = await _rejected("SELECT qa.not_a_real_column FROM quiz_attempts qa")
    assert "not_a_real_column" in error
    assert "quiz_attempts" in error


async def test_invalid_unqualified_column_rejected() -> None:
    error = await _rejected("SELECT not_a_real_column FROM quiz_attempts")
    assert "not_a_real_column" in error


async def test_valid_unqualified_column_passes() -> None:
    await _validated("SELECT score_percent FROM quiz_attempts")


async def test_column_qualified_by_unknown_alias_rejected() -> None:
    error = await _rejected("SELECT zz.id FROM quiz_attempts qa")
    assert "unknown table alias" in error.lower()


async def test_join_column_resolution_respects_each_alias() -> None:
    await _validated(
        "SELECT sp.full_name, qa.score_percent "
        "FROM student_profiles sp JOIN quiz_attempts qa ON qa.student_id = sp.id"
    )


async def test_join_column_from_wrong_table_alias_rejected() -> None:
    # full_name exists on student_profiles, not quiz_attempts.
    error = await _rejected(
        "SELECT qa.full_name "
        "FROM student_profiles sp JOIN quiz_attempts qa ON qa.student_id = sp.id"
    )
    assert "full_name" in error
    assert "quiz_attempts" in error


# --- Step 5: LIMIT enforcement (injected, not rejected) ---------------------------


async def test_missing_limit_gets_default_injected() -> None:
    validated = await _validated("SELECT id FROM quiz_attempts")
    assert f"LIMIT {DEFAULT_ROW_LIMIT}" in validated


async def test_existing_limit_is_preserved_not_overridden() -> None:
    validated = await _validated("SELECT id FROM quiz_attempts LIMIT 10")
    assert "LIMIT 10" in validated
    assert f"LIMIT {DEFAULT_ROW_LIMIT}" not in validated


async def test_oversized_explicit_limit_is_clamped_to_default() -> None:
    validated = await _validated("SELECT id FROM quiz_attempts LIMIT 1000000")
    assert f"LIMIT {DEFAULT_ROW_LIMIT}" in validated
    assert "LIMIT 1000000" not in validated


async def test_set_config_is_rejected_even_inside_cte() -> None:
    error = await _rejected(
        "WITH setup AS (SELECT set_config('app.current_user_role', 'admin', true) AS a) "
        "SELECT id FROM subjects, setup WHERE setup.a IS NOT NULL"
    )
    assert "set_config" in error.lower()


async def test_quoted_set_config_is_rejected() -> None:
    # sqlglot stores double-quoted function names as Identifier, not str — the
    # blocklist must still catch them or RLS GUCs remain attacker-writable.
    error = await _rejected(
        "SELECT id, \"set_config\"('app.current_user_role', 'admin', true) FROM subjects"
    )
    assert "set_config" in error.lower()


async def test_schema_qualified_quoted_set_config_is_rejected() -> None:
    error = await _rejected(
        "SELECT pg_catalog.\"set_config\"('app.current_institution_id', "
        "'00000000-0000-0000-0000-000000000099', true) FROM subjects"
    )
    assert "set_config" in error.lower()


async def test_quoted_pg_sleep_is_rejected() -> None:
    error = await _rejected('SELECT "pg_sleep"(1) FROM subjects')
    assert "pg_sleep" in error.lower()


async def test_pg_advisory_lock_is_rejected() -> None:
    error = await _rejected("SELECT pg_advisory_lock(1) FROM subjects LIMIT 1")
    assert "pg_advisory_lock" in error.lower()


async def test_select_into_is_rejected() -> None:
    error = await _rejected("SELECT id, name INTO subjects FROM grades")
    assert "into" in error.lower()


async def test_same_named_cte_still_whitelists_inner_base_table() -> None:
    # Inner FROM student_profiles is the real table (non-RECURSIVE CTE shadowing).
    # It must remain whitelist-checked — an excluded table must still be caught.
    error = await _rejected(
        "WITH ingest_jobs AS (SELECT id FROM ingest_jobs) SELECT id FROM ingest_jobs"
    )
    assert "ingest_jobs" in error


async def test_recursive_same_named_cte_schema_qualified_still_whitelists() -> None:
    # RECURSIVE + same alias + public.<table> is a real base-table scan in Postgres.
    # It must remain whitelist-checked (and later role-scoped), not skipped as a CTE ref.
    error = await _rejected(
        "WITH RECURSIVE ingest_jobs AS ("
        " SELECT id FROM public.ingest_jobs"
        " UNION ALL"
        " SELECT id FROM ingest_jobs WHERE false"
        ") SELECT id FROM ingest_jobs"
    )
    assert "ingest_jobs" in error


async def test_recursive_same_named_cte_unqualified_base_still_whitelists() -> None:
    # Unqualified base-term FROM ingest_jobs inside WITH RECURSIVE ingest_jobs AS (…)
    # also binds to the real table in Postgres (CTE name is not in scope there).
    # Must remain whitelist-checked — not skipped as a recursive self-ref.
    error = await _rejected(
        "WITH RECURSIVE ingest_jobs AS ("
        " SELECT id FROM ingest_jobs"
        " UNION ALL"
        " SELECT id FROM ingest_jobs WHERE false"
        ") SELECT id FROM ingest_jobs"
    )
    assert "ingest_jobs" in error


def test_default_row_limit_matches_insights_module_precedent() -> None:
    # Not testing insights.service directly (out of this module's business), just
    # documenting that the chosen cap isn't an arbitrary new number.
    assert DEFAULT_ROW_LIMIT == 500


# --- Success path: what exactly gets written back ---------------------------------


async def test_success_clears_error() -> None:
    result = await validate_sql(_state("SELECT id FROM quiz_attempts", error="stale"))
    assert result["error"] is None


async def test_success_does_not_touch_generated_sql() -> None:
    original = "SELECT id FROM quiz_attempts"
    result = await validate_sql(_state(original))
    assert result["generated_sql"] == original


@pytest.mark.parametrize("starting_retry_count", [0, 1, 2])
async def test_retry_count_never_touched_on_success(starting_retry_count: int) -> None:
    result = await validate_sql(
        _state("SELECT id FROM quiz_attempts", retry_count=starting_retry_count)
    )
    assert result["retry_count"] == starting_retry_count


@pytest.mark.parametrize("starting_retry_count", [0, 1, 2])
async def test_retry_count_never_touched_on_rejection(starting_retry_count: int) -> None:
    result = await validate_sql(
        _state("SELECT id FROM chunk_embeddings", retry_count=starting_retry_count)
    )
    assert result["retry_count"] == starting_retry_count
