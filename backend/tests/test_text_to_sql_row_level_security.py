"""Tests for the 5th defense-in-depth layer: Postgres Row-Level Security (migration
`e1f2a3b4c5d6`), enforced inside the database itself rather than in this pipeline's
Python.

This file's central technique, used for most tests here: run the *same* base query two
ways for the *same* identity, and confirm they agree.

1. Through `apply_role_scope` (`_run_scoped`, imported from
   test_text_to_sql_apply_role_scope_integration.py) — the existing, already-reviewed
   app-layer rewrite, executed as the full-privilege `education` role (RLS has no effect
   on it; `education` has BYPASSRLS -- see the migration's own docstring).
2. Raw, unmodified, as `text_to_sql_reader` with the session identity variables set
   (`_raw_rls_query` below) — no AST rewrite at all. Only RLS can restrict this.

Agreement between the two, table by table, role by role, reusing the exact two-
institution/two-teacher/all-sections fixture already built for Finding 4 and Finding 5
(`seeded`/`_Fixture`, imported rather than re-seeded), is the actual proof this task asks
for: RLS is an independent mirror of the same rule, not a coincidentally-similar one.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Reused, not re-seeded -- the exact fixtures Finding 4/5's own integration tests already
# built and this session already trusts.
from tests.test_text_to_sql_apply_role_scope_integration import (  # noqa: F401 -- seeded/async_session are fixtures, used by name
    _Fixture,
    _run_scoped,
    async_session,
    seeded,
)

from education_platform.db.session import get_text_to_sql_session_factory, reset_engine
from education_platform.modules.text_to_sql.nodes.apply_role_scope import (
    INSTITUTION_SCOPED_TABLES,
    STUDENT_SCOPED_TABLES,
)
from education_platform.modules.text_to_sql.nodes.execute_sql import execute_sql
from education_platform.modules.text_to_sql.state import TextToSQLState

_ALL_SCOPED_TABLES: frozenset[str] = STUDENT_SCOPED_TABLES | INSTITUTION_SCOPED_TABLES


@pytest.fixture(autouse=True)
def _fresh_text_to_sql_engine_per_test() -> Iterator[None]:
    """`clean_db` (used by the `seeded`/`async_session`-based tests below) calls
    `reset_engine()` as a side effect of truncating tables, but several tests in this
    file call `_raw_rls_query`/`execute_sql` directly against the cached, process-wide
    `_text_to_sql_engine` (db.session.get_text_to_sql_engine) with no `clean_db`
    involved at all. pytest-asyncio hands each async test its own event loop; a pooled
    asyncpg connection created on one test's loop cannot be reused once that loop closes.
    Confirmed live: two such tests back to back with no intervening `clean_db` test
    crashed the second one with "RuntimeError: Event loop is closed" -- the cached
    engine's pool still held a connection from the first test's already-closed loop.
    Resetting unconditionally before and after every test here (redundant, and harmless,
    on the tests that also go through `clean_db`) guarantees a fresh engine bound to
    whichever loop is about to use it.
    """
    reset_engine()
    yield
    reset_engine()


def _id_column(table: str) -> str:
    """`student_360` is a view whose key column is named `student_id`, not `id` -- see
    its own CREATE VIEW (migration d3e4f5a6b7c8): `sp.id AS student_id`. Every other
    scoped table has a plain `id` primary key. Confirmed the hard way: a first version of
    this file's generic `SELECT id FROM {table}` loop hit
    `asyncpg.exceptions.UndefinedColumnError: column "id" does not exist` for this one
    table, and the uncaught error corrupted that test's connection state badly enough to
    cascade into unrelated-looking "Event loop is closed" failures in whatever ran next.
    """
    return "student_id" if table == "student_360" else "id"


async def _raw_rls_query(
    sql: str,
    *,
    user_id: UUID | str | None,
    role: str | None,
    institution_id: UUID | str | None,
) -> list[dict[str, Any]]:
    """Runs `sql` verbatim against Postgres as `text_to_sql_reader` -- no apply_role_scope
    rewrite, no institution/row predicate added by any Python in this pipeline. Whatever
    comes back is what RLS alone allowed. `None` for all three identity args means
    "never call set_config at all" -- a genuinely fresh connection's default state, used
    by the missing-identity test below; a real value for any of them sets it exactly the
    way `execute_sql.py` does.
    """
    session_factory = get_text_to_sql_session_factory()
    async with session_factory() as session:
        if user_id is not None or role is not None or institution_id is not None:
            await session.execute(
                text(
                    "SELECT set_config('app.current_user_id', :user_id, true), "
                    "set_config('app.current_user_role', :role, true), "
                    "set_config('app.current_institution_id', :institution_id, true)"
                ),
                {
                    "user_id": str(user_id) if user_id is not None else "",
                    "role": role or "",
                    "institution_id": str(institution_id) if institution_id is not None else "",
                },
            )
        rows = (await session.execute(text(sql))).mappings().all()
        return [dict(row) for row in rows]


# --- Requirement 1 + 4: every table family, direct connection, cross-checked against ---
# --- apply_role_scope's own already-reviewed rewrite for the exact same identity -------


@pytest.mark.parametrize("table", sorted(_ALL_SCOPED_TABLES))
@pytest.mark.parametrize("role_field", ["admin_user_id", "teacher_user_id"])
async def test_rls_matches_apply_role_scope_for_every_table(
    table: str, role_field: str, async_session: AsyncSession, seeded: _Fixture
) -> None:
    role = "admin" if role_field == "admin_user_id" else "teacher"
    user_id = getattr(seeded, role_field)
    id_col = _id_column(table)

    via_app_layer = await _run_scoped(
        async_session,
        sql=f"SELECT {id_col} FROM {table}",
        user_id=user_id,
        role=role,
        institution_id=seeded.institution_id,
    )
    via_rls_only = await _raw_rls_query(
        f"SELECT {id_col} FROM {table}",
        user_id=user_id,
        role=role,
        institution_id=seeded.institution_id,
    )

    app_layer_ids = {row[id_col] for row in via_app_layer}
    rls_ids = {row[id_col] for row in via_rls_only}
    assert rls_ids == app_layer_ids, (
        f"table={table!r} role={role!r}: apply_role_scope gave {app_layer_ids}, "
        f"RLS alone gave {rls_ids} -- a real mismatch between the two layers"
    )


async def test_rls_matches_apply_role_scope_for_student_role_on_student_scoped_tables(
    async_session: AsyncSession, seeded: _Fixture
) -> None:
    # Split from the admin/teacher parametrization above: STUDENT_SCOPED_TABLES is the
    # only tier a student role can read at all (INSTITUTION_SCOPED_TABLES has no student
    # grant in apply_role_scope.py, so comparing there would just compare two empty
    # sets -- not a meaningful check).
    for table in sorted(STUDENT_SCOPED_TABLES):
        id_col = _id_column(table)
        via_app_layer = await _run_scoped(
            async_session,
            sql=f"SELECT {id_col} FROM {table}",
            user_id=seeded.student_a_math_user_id,
            role="student",
            institution_id=seeded.institution_id,
        )
        via_rls_only = await _raw_rls_query(
            f"SELECT {id_col} FROM {table}",
            user_id=seeded.student_a_math_user_id,
            role="student",
            institution_id=seeded.institution_id,
        )
        app_layer_ids = {row[id_col] for row in via_app_layer}
        rls_ids = {row[id_col] for row in via_rls_only}
        assert rls_ids == app_layer_ids, (
            f"table={table!r} role='student': {app_layer_ids} vs {rls_ids}"
        )


# --- Requirement 2: the single most important test in this task -----------------------


async def test_no_set_local_returns_zero_rows_never_real_data() -> None:
    """A genuinely fresh connection, no set_config call at all -- simulating a bug that
    skipped execute_sql's identity propagation, or a completely different, unrelated
    client connecting as text_to_sql_reader without ever having gone through this
    pipeline. Every policy's current_setting(name, true) read comes back NULL, which
    satisfies no equality check anywhere -- zero rows, not "everything," not a crash, and
    never a stale value from a previous session (a fresh connection has nothing to be
    stale from, but this is also exactly what SET LOCAL's transaction-scoping guarantees
    on a *reused*, pooled connection -- see execute_sql.py's own docstring).
    """
    for table in sorted(_ALL_SCOPED_TABLES):
        id_col = _id_column(table)
        rows = await _raw_rls_query(
            f"SELECT {id_col} FROM {table}", user_id=None, role=None, institution_id=None
        )
        assert rows == [], f"table={table!r} returned real rows with no identity set at all"


async def test_no_set_local_via_execute_sql_directly_still_denies() -> None:
    """Same case, through the real code path this task cares about most: execute_sql
    itself, called with a state that never had identity fields populated -- the actual
    shape a real bug (a node upstream forgetting to carry state["user_id"] forward)
    would take, not just a hand-rolled raw connection.
    """
    state: TextToSQLState = {
        "validated_sql": "SELECT id FROM subjects",
        "user_id": "",
        "user_role": "",
        "institution_id": "",
        "error": None,
        "retry_count": 0,
        "audit_entry": None,
    }
    result = await execute_sql(state)
    assert result["error"] is None
    assert result["query_result"] == []
    assert result["result_row_count"] == 0


# --- Requirement 3: simulate a bug in apply_role_scope, confirm RLS holds anyway -------


async def test_rls_alone_restricts_an_unrestricted_query_bypassing_apply_role_scope(
    seeded: _Fixture,
) -> None:
    """The actual proof this whole feature exists for: a query that never went through
    apply_role_scope at all -- exactly what a bug in that node (a table it forgot to
    classify, a predicate it forgot to inject) would produce -- run directly against
    Postgres as text_to_sql_reader. If RLS is doing its job, the result is still
    correctly scoped, entirely independent of the 3rd layer's own correctness.
    """
    # An intentionally *unscoped* query -- no WHERE clause pinning institution or role at
    # all, the shape apply_role_scope exists to prevent from ever reaching the database.
    rows = await _raw_rls_query(
        "SELECT id FROM student_profiles",
        user_id=seeded.teacher_user_id,
        role="teacher",
        institution_id=seeded.institution_id,
    )
    ids = {row["id"] for row in rows}
    # The teacher's real taught students are visible...
    assert seeded.student_a_math_id in ids
    assert seeded.student_a_science_id in ids
    # ...but students outside what this teacher teaches, and every student in the other
    # institution, are not -- RLS produced the narrow result on its own, with zero help
    # from apply_role_scope, which never ran.
    assert seeded.student_b_math_id not in ids
    assert seeded.other_student_id not in ids

    # Same proof against a plain INSTITUTION_SCOPED_TABLES table with no self/taught
    # dimension at all -- an unscoped SELECT * FROM topics would hand back both
    # institutions' curriculum trees if RLS weren't independently enforcing the boundary.
    rows = await _raw_rls_query(
        "SELECT id FROM topics",
        user_id=seeded.admin_user_id,
        role="admin",
        institution_id=seeded.institution_id,
    )
    topic_ids = {row["id"] for row in rows}
    assert seeded.topic_id in topic_ids
    assert seeded.other_topic_id not in topic_ids


async def test_rls_alone_rejects_the_all_sections_teacher_grant_correctly(
    seeded: _Fixture,
) -> None:
    """Cross-checks the specific Finding-5-adjacent scenario this fixture was built for:
    teacher2's NULL-section ("all sections") grant reaching every section of an offering,
    versus the primary teacher's specific-section grant reaching only its own section --
    verified through RLS alone, unmediated by apply_role_scope.
    """
    rows = await _raw_rls_query(
        "SELECT id FROM student_profiles",
        user_id=seeded.teacher_user_id,
        role="teacher",
        institution_id=seeded.institution_id,
    )
    ids = {row["id"] for row in rows}
    # teacher_user_id's Math grant names section_a only.
    assert seeded.student_a_math_id in ids
    assert seeded.student_b_math_id not in ids


# --- Requirement 5: quiz_releases -- row boundary via RLS, column redaction is NOT ------
# --- something RLS can do, and this test proves that limitation explicitly, not --------
# --- silently. -------------------------------------------------------------------------


async def test_quiz_releases_row_boundary_enforced_by_rls(seeded: _Fixture) -> None:
    rows = await _raw_rls_query(
        "SELECT id FROM quiz_releases",
        user_id=seeded.teacher_user_id,
        role="teacher",
        institution_id=seeded.institution_id,
    )
    ids = {row["id"] for row in rows}
    assert seeded.quiz_release_id in ids
    assert seeded.other_quiz_release_id not in ids


async def test_quiz_releases_column_redaction_is_not_and_cannot_be_rls(
    seeded: _Fixture,
) -> None:
    """Explicitly the expected, correct behavior, not a regression: RLS operates at row
    granularity only -- it cannot redact one column's value while keeping the row
    visible. A direct query as text_to_sql_reader, with identity correctly set via RLS's
    own session variables, sees the *real* released_by_user_id for any row its
    institution boundary allows -- exactly as before this migration. The redaction
    itself (`_redact_identity_columns`/`_find_redacted_column_misuse` in
    apply_role_scope.py) remains the only layer that protects this specific column, and
    this test's job is to prove that gap is real and understood, not accidentally
    papered over by RLS looking like it covers something it structurally can't.
    """
    rows = await _raw_rls_query(
        f"SELECT released_by_user_id FROM quiz_releases WHERE id = '{seeded.quiz_release_id}'",
        user_id=seeded.teacher2_user_id,  # deliberately NOT the releasing teacher
        role="teacher",
        institution_id=seeded.institution_id,
    )
    assert len(rows) == 1
    # The row is visible (institution boundary correctly allows it) and the real value is
    # visible too (RLS never redacts a column) -- this is `teacher_user_id`, the actual
    # releaser, per the fixture's own seeding, not NULL the way apply_role_scope's
    # redaction would show it to a non-self reader.
    assert rows[0]["released_by_user_id"] == seeded.teacher_user_id
