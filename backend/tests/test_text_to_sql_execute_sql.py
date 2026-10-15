"""Postgres-backed tests for the text-to-SQL execute_sql node.

Every test here needs a live database — there is no meaningful DB-free unit test for a
node whose entire job is "run this SQL against Postgres" — so this file uses the same
`clean_db`/Postgres fixtures as test_authorization_scope.py and
test_text_to_sql_apply_role_scope_integration.py, not a mocked session.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Protocol, cast
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import education_platform.modules.text_to_sql.graph as graph_module
from education_platform.db.session import get_text_to_sql_session_factory
from education_platform.db.url import to_sync_url
from education_platform.modules.auth.models import Institution, User
from education_platform.modules.text_to_sql.graph import build_text_to_sql_graph
from education_platform.modules.text_to_sql.nodes.execute_sql import ROW_CAP, execute_sql
from education_platform.modules.text_to_sql.state import (
    EXECUTION_ERROR,
    TextToSQLState,
    error_category,
)


class _ExecuteSqlModule(Protocol):
    """Typed view of execute_sql.py's module namespace, for patching STATEMENT_TIMEOUT_MS/
    ROW_CAP where execute_sql.py actually looks them up.
    """

    STATEMENT_TIMEOUT_MS: int
    ROW_CAP: int


# `nodes/__init__.py` does `from .execute_sql import execute_sql as execute_sql`, which
# rebinds the `nodes.execute_sql` *attribute* from the submodule to the function (same
# gotcha as generate_sql/load_schema — see test_text_to_sql_generate_sql.py). Reach the
# real submodule via sys.modules to monkeypatch its module-level constants.
_MODULE = cast(
    _ExecuteSqlModule, sys.modules["education_platform.modules.text_to_sql.nodes.execute_sql"]
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
    """injection_guard (the graph's entry node, ahead of load_schema) makes its own live
    OpenRouter classifier call for any question its heuristic regex doesn't already
    catch. This file is about execute_sql's own behavior, not injection_guard's --
    default every question here to "not an injection" so these tests don't silently make
    a real, unmocked API call and don't depend on network availability to pass.
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
    execute_sql's own behavior, not question_validator's -- default every question here
    to "not off-topic" so these tests don't silently make a real, unmocked API call.
    """

    async def _fake_ot(
        messages: list[dict[str, str]], *, settings: object, temperature: float = 0.0
    ) -> dict[str, object]:
        return {"off_topic": False}

    monkeypatch.setattr(_QUESTION_VALIDATOR_MODULE, "chat_completion_json", _fake_ot)


@pytest.fixture()
def seeded_institution(clean_db: str) -> Iterator[UUID]:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        inst = Institution(name="Execute-SQL Test School", timezone="UTC")
        session.add(inst)
        session.commit()
        institution_id = inst.id
    engine.dispose()
    yield institution_id


@pytest.fixture()
def seeded_admin_user_id(seeded_institution: UUID, clean_db: str) -> Iterator[UUID]:
    # A real users.id row: now that audit_log (Task 10) runs unconditionally at the end
    # of every full graph invocation, state["user_id"] must be a real UUID that satisfies
    # AuditEvent.actor_user_id's FK — a placeholder string like "admin-1" makes the
    # graph-level test below exercise audit_log's *failure* path instead of its success
    # path, silently changing what the test actually proves.
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        user = User(
            institution_id=seeded_institution,
            email="admin@execute-sql-test.school",
            full_name="Admin",
            password_hash="unused",
        )
        session.add(user)
        session.commit()
        user_id = user.id
    engine.dispose()
    yield user_id


def _state(sql: str | None, **overrides: object) -> TextToSQLState:
    state: TextToSQLState = {
        "validated_sql": sql,
        "error": None,
        "retry_count": 0,
        "audit_entry": None,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _admin_state(
    sql: str | None, *, institution_id: UUID, user_id: UUID, **overrides: object
) -> TextToSQLState:
    """`_state()` plus the identity fields execute_sql now requires unconditionally (RLS
    policies read them via the SET LOCAL/set_config calls added for migration
    e1f2a3b4c5d6 -- a KeyError here means a real graph-invariant violation, not something
    to default away). Admin, not teacher/student: these tests exercise execute_sql's own
    mechanics (timeouts, row caps, error handling), not role-specific row scoping, so the
    least-restrictive real role keeps them focused on what they're actually testing.
    """
    return _state(
        sql,
        user_id=str(user_id),
        user_role="admin",
        institution_id=str(institution_id),
        **overrides,
    )


# --- Missing-identity guard (DB-free) --------------------------------------------------
#
# Unlike every other test in this file, these three don't need Postgres at all: the
# identity guard at the top of execute_sql returns before get_text_to_sql_session_factory
# is ever called, so a state missing user_id/user_role/institution_id never reaches the
# DB layer. Without the guard, these would raise an uncaught KeyError instead of
# producing a controlled EXECUTION_ERROR.


async def test_missing_user_id_fails_closed_instead_of_crashing() -> None:
    state = _state("SELECT 1", user_role="admin", institution_id="inst-1")
    result = await execute_sql(state)
    assert result["query_result"] is None
    assert result["result_row_count"] is None
    assert error_category(result["error"]) == EXECUTION_ERROR


async def test_missing_user_role_fails_closed_instead_of_crashing() -> None:
    state = _state("SELECT 1", user_id="u-1", institution_id="inst-1")
    result = await execute_sql(state)
    assert result["query_result"] is None
    assert result["result_row_count"] is None
    assert error_category(result["error"]) == EXECUTION_ERROR


async def test_missing_institution_id_fails_closed_instead_of_crashing() -> None:
    state = _state("SELECT 1", user_id="u-1", user_role="admin")
    result = await execute_sql(state)
    assert result["query_result"] is None
    assert result["result_row_count"] is None
    assert error_category(result["error"]) == EXECUTION_ERROR


# --- Success path ----------------------------------------------------------------------


async def test_valid_sql_populates_query_result_and_row_count(
    seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    result = await execute_sql(
        _admin_state(
            f"SELECT id, name FROM institutions WHERE id = '{seeded_institution}'",
            institution_id=seeded_institution,
            user_id=seeded_admin_user_id,
        )
    )

    assert result["error"] is None
    assert result["result_row_count"] == 1
    rows = result["query_result"]
    assert rows is not None
    assert rows[0]["id"] == seeded_institution
    assert rows[0]["name"] == "Execute-SQL Test School"


async def test_zero_row_result_is_not_an_error(
    seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    result = await execute_sql(
        _admin_state(
            "SELECT id FROM institutions WHERE name = 'no such school'",
            institution_id=seeded_institution,
            user_id=seeded_admin_user_id,
        )
    )

    assert result["error"] is None
    assert result["query_result"] == []
    assert result["result_row_count"] == 0


async def test_current_user_id_bind_uses_authenticated_state_without_intent_parameters(
    seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    result = await execute_sql(
        _admin_state(
            "SELECT :current_user_id AS authenticated_user_id",
            institution_id=seeded_institution,
            user_id=seeded_admin_user_id,
            intent_parameters={},
        )
    )

    assert result["error"] is None
    rows = result["query_result"]
    assert rows is not None
    assert rows[0]["authenticated_user_id"] == seeded_admin_user_id


async def test_current_user_id_and_institution_id_bind_together(
    seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    result = await execute_sql(
        _admin_state(
            "SELECT :current_user_id AS authenticated_user_id, "
            ":current_institution_id AS authenticated_institution_id",
            institution_id=seeded_institution,
            user_id=seeded_admin_user_id,
            intent_parameters={},
        )
    )

    assert result["error"] is None
    rows = result["query_result"]
    assert rows is not None
    assert rows[0]["authenticated_user_id"] == seeded_admin_user_id
    assert rows[0]["authenticated_institution_id"] == seeded_institution


# --- Statement timeout -------------------------------------------------------------------


async def test_slow_query_is_cancelled_by_statement_timeout(
    monkeypatch: pytest.MonkeyPatch, seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    # A tiny timeout, not the real 5s default, so this test doesn't itself take 5+ seconds.
    monkeypatch.setattr(_MODULE, "STATEMENT_TIMEOUT_MS", 200)

    result = await execute_sql(
        _admin_state(
            "SELECT pg_sleep(5)", institution_id=seeded_institution, user_id=seeded_admin_user_id
        )
    )

    assert error_category(result["error"]) == EXECUTION_ERROR
    assert result["query_result"] is None
    assert result["result_row_count"] is None


# --- Read-only transaction ---------------------------------------------------------------


async def test_read_only_transaction_rejects_a_temp_table(
    seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    # `text_to_sql_reader` has no table GRANTs revoked that would touch this -- CREATE
    # TEMP privilege on the database is Postgres' PUBLIC default, and confirmed directly
    # (`docker exec ... psql -U text_to_sql_reader`) that this exact statement succeeds
    # for this role outside a read-only transaction. So a failure here proves the new
    # `SET TRANSACTION READ ONLY` is what's blocking it, unlike a plain INSERT/UPDATE/
    # DELETE against a real table, which the reader role's existing GRANTs (migration
    # c9d0e1f2a3b4) already block regardless of this change and so wouldn't prove
    # anything new about this specific transaction-level setting.
    result = await execute_sql(
        _admin_state(
            "CREATE TEMP TABLE execute_sql_read_only_probe (x int)",
            institution_id=seeded_institution,
            user_id=seeded_admin_user_id,
        )
    )

    assert error_category(result["error"]) == EXECUTION_ERROR
    assert result["query_result"] is None
    assert result["result_row_count"] is None


# --- Row cap, belt-and-suspenders ---------------------------------------------------------


async def test_row_cap_truncates_if_more_rows_than_expected_come_back(
    monkeypatch: pytest.MonkeyPatch, seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    # generate_series stands in for "somehow more rows came back than ROW_CAP expects" —
    # this node must not blindly trust validate_sql's own LIMIT injection.
    monkeypatch.setattr(_MODULE, "ROW_CAP", 10)

    result = await execute_sql(
        _admin_state(
            "SELECT generate_series(1, 25) AS n",
            institution_id=seeded_institution,
            user_id=seeded_admin_user_id,
        )
    )

    assert result["error"] is None
    assert result["result_row_count"] == 10
    rows = result["query_result"]
    assert rows is not None
    assert len(rows) == 10
    audit_entry = result["audit_entry"]
    assert audit_entry is not None
    assert audit_entry["row_cap_truncated"] is True


def test_row_cap_is_imported_from_validate_sql_not_a_second_literal() -> None:
    from education_platform.modules.text_to_sql.nodes.validate_sql import DEFAULT_ROW_LIMIT

    # `is`, not `==` — proves this is the same imported constant (see execute_sql.py's
    # `from ...validate_sql import DEFAULT_ROW_LIMIT as ROW_CAP`), not a second `500`
    # literal that happens to match today and could silently drift tomorrow.
    assert ROW_CAP is DEFAULT_ROW_LIMIT


# --- DB-level failure: classification, no leak, no retry-loop routing --------------------


async def test_db_level_failure_produces_execution_error_not_a_crash(
    seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    result = await execute_sql(
        _admin_state(
            "SELECT 1 / 0", institution_id=seeded_institution, user_id=seeded_admin_user_id
        )
    )

    assert error_category(result["error"]) == EXECUTION_ERROR
    assert result["query_result"] is None
    assert result["result_row_count"] is None


async def test_raw_postgres_error_text_not_leaked_into_user_facing_error(
    seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    result = await execute_sql(
        _admin_state(
            "SELECT 1 / 0", institution_id=seeded_institution, user_id=seeded_admin_user_id
        )
    )

    error = result["error"]
    assert error is not None
    # The generic message is present...
    assert "the query could not be run" in error
    # ...but nothing Postgres/asyncpg-specific about *why* leaks into it.
    lowered = error.lower()
    for leaky_fragment in ("division", "asyncpg", "psql", "postgres", "1 / 0", "zero"):
        assert leaky_fragment not in lowered, f"{leaky_fragment!r} leaked into state['error']"
    # The real detail is still recorded, just not in the user-facing field.
    audit_entry = result["audit_entry"]
    assert audit_entry is not None
    assert audit_entry["execution_error_type"]


async def test_execution_error_routes_to_honest_refusal_through_compiled_graph(
    monkeypatch: pytest.MonkeyPatch, seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    async def _fake_generate_sql(state: TextToSQLState) -> TextToSQLState:
        return {**state, "generated_sql": "SELECT 1 / 0", "error": None}

    monkeypatch.setattr(graph_module, "generate_sql", _fake_generate_sql)

    graph = build_text_to_sql_graph()
    initial: TextToSQLState = {
        "question": "irrelevant",
        "user_id": str(seeded_admin_user_id),
        "user_role": "admin",
        "institution_id": str(seeded_institution),
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
    result = await graph.ainvoke(initial, config={"recursion_limit": 15})

    assert error_category(result["error"]) == EXECUTION_ERROR
    # Did not loop back into generate_sql's retry path.
    assert result["retry_count"] == 0


# --- Least-privilege DB role -------------------------------------------------------------


async def _expect_permission_denied(sql: str) -> None:
    session_factory = get_text_to_sql_session_factory()
    async with session_factory() as session:
        with pytest.raises(SQLAlchemyError, match="permission denied"):
            await session.execute(text(sql))


async def test_reader_role_can_read_a_granted_table(
    seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    # Positive control: proves the negative tests below fail for the *right* reason
    # (a real REVOKE), not because the role/connection itself is broken. Now that RLS
    # (migration e1f2a3b4c5d6) is live on `institutions`, this also needs the identity
    # session variables set -- without them the row would be invisible for a *different*
    # reason (RLS's own fail-closed default), which would defeat this test's actual
    # purpose of isolating the grant check.
    session_factory = get_text_to_sql_session_factory()
    async with session_factory() as session:
        await session.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_user_role', 'admin', true), "
                "set_config('app.current_institution_id', :institution_id, true)"
            ),
            {"user_id": str(seeded_admin_user_id), "institution_id": str(seeded_institution)},
        )
        rows = (await session.execute(text("SELECT id FROM institutions"))).all()
    assert len(rows) == 1


async def test_reader_role_cannot_read_password_hash_column(clean_db: str) -> None:
    await _expect_permission_denied("SELECT password_hash FROM users")


async def test_reader_role_cannot_read_token_hash_column(clean_db: str) -> None:
    await _expect_permission_denied("SELECT token_hash FROM refresh_sessions")


async def test_reader_role_cannot_read_question_answer_keys_at_all(clean_db: str) -> None:
    await _expect_permission_denied("SELECT id FROM question_answer_keys")


async def test_reader_role_cannot_read_a_table_outside_the_pipelines_scope(clean_db: str) -> None:
    # chat_conversations is neither in load_schema.REQUIRED_TABLES nor granted by the
    # migration at all — this role should have literally no privilege on it.
    await _expect_permission_denied("SELECT id FROM chat_conversations")


# All three against attendance_records specifically, an in-scope table this role *can*
# SELECT from (full-table grant — see _FULL_TABLE_GRANTS in migration c9d0e1f2a3b4) —
# proving the missing privilege is write access specifically, on a table it can otherwise
# read, rather than just re-confirming there's no SELECT grant at all.
_INSERT_ATTENDANCE_RECORD = (
    "INSERT INTO attendance_records "
    "(id, student_id, academic_period_id, section_id, grade_subject_offering_id, "
    "on_date, status) "
    "VALUES ("
    "'00000000-0000-0000-0000-000000000001', "
    "'00000000-0000-0000-0000-000000000002', "
    "'00000000-0000-0000-0000-000000000003', "
    "NULL, NULL, '2026-01-01', 'present')"
)


async def test_reader_role_cannot_insert_into_an_in_scope_table(clean_db: str) -> None:
    await _expect_permission_denied(_INSERT_ATTENDANCE_RECORD)


async def test_reader_role_cannot_update_an_in_scope_table(clean_db: str) -> None:
    await _expect_permission_denied("UPDATE attendance_records SET note = 'tampered' WHERE TRUE")


async def test_reader_role_cannot_delete_from_an_in_scope_table(clean_db: str) -> None:
    await _expect_permission_denied("DELETE FROM attendance_records WHERE TRUE")


async def test_out_of_scope_query_that_bypassed_the_graph_still_fails_at_the_db(
    clean_db: str,
) -> None:
    # Simulates a hypothetical bug in validate_sql/apply_role_scope letting an
    # out-of-scope column through: even executed directly, unmediated by execute_sql's
    # own logic, the DB role itself is the backstop.
    await _expect_permission_denied(
        "SELECT u.password_hash FROM users u JOIN institutions i ON True"
    )
