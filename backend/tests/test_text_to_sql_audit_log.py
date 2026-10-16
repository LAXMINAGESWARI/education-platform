"""Postgres-backed tests for the text-to-SQL audit_log node.

Every test here needs a live database — there is no meaningful DB-free unit test for a
node whose entire job is "persist a row to audit_events" — same rationale as
test_text_to_sql_execute_sql.py and test_authorization_scope.py, which use the same
`clean_db` fixtures.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

import education_platform.modules.text_to_sql.graph as graph_module
from education_platform.db.url import to_async_url, to_sync_url
from education_platform.modules.auth.models import AuditEvent, Institution, User
from education_platform.modules.text_to_sql.graph import build_text_to_sql_graph
from education_platform.modules.text_to_sql.nodes.audit_log import audit_log
from education_platform.modules.text_to_sql.nodes.load_schema import (
    _load_filtered_schema_context,
)
from education_platform.modules.text_to_sql.state import (
    AUDIT_ERROR,
    ROLE_VIOLATION,
    SCHEMA_ERROR,
    TextToSQLState,
    error_category,
)


class _LoadSchemaModule(Protocol):
    SCHEMA_CATALOG_PATH: Path


# Same nodes/__init__.py attribute-rebinding gotcha as elsewhere in this suite (see
# test_text_to_sql_load_schema.py) — reach the real submodule via sys.modules to
# monkeypatch SCHEMA_CATALOG_PATH.
_LOAD_SCHEMA_MODULE = cast(
    _LoadSchemaModule,
    sys.modules["education_platform.modules.text_to_sql.nodes.load_schema"],
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
    catch. This file is about audit_log's own behavior, not injection_guard's — default
    every question here to "not an injection" so these tests don't silently make a real,
    unmocked API call and don't depend on network availability to pass.
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
    audit_log's own behavior, not question_validator's -- default every question here to
    "not off-topic" so these tests don't silently make a real, unmocked API call.
    """

    async def _fake_ot(
        messages: list[dict[str, str]], *, settings: object, temperature: float = 0.0
    ) -> dict[str, object]:
        return {"off_topic": False}

    monkeypatch.setattr(_QUESTION_VALIDATOR_MODULE, "chat_completion_json", _fake_ot)


@dataclass(frozen=True)
class _Seeded:
    institution_id: UUID
    user_id: UUID


@pytest.fixture()
def seeded(clean_db: str) -> Iterator[_Seeded]:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        inst = Institution(name="Audit-Log Test School", timezone="UTC")
        session.add(inst)
        session.flush()
        user = User(
            institution_id=inst.id,
            email="admin@audit-log-test.school",
            full_name="Admin",
            password_hash="unused",
        )
        session.add(user)
        session.commit()
        result = _Seeded(institution_id=inst.id, user_id=user.id)
    engine.dispose()
    yield result


@pytest_asyncio.fixture()
async def async_session(seeded: _Seeded, clean_db: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(to_async_url(clean_db), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()
    await engine.dispose()


async def _latest_audit_event(session: AsyncSession, institution_id: UUID) -> AuditEvent:
    row = await session.scalar(
        select(AuditEvent)
        .where(AuditEvent.institution_id == institution_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    assert row is not None, "expected an audit_events row to have been written"
    return row


def _initial_state(seeded: _Seeded) -> TextToSQLState:
    return {
        "question": "how many institutions are there?",
        "user_id": str(seeded.user_id),
        "user_role": "admin",
        "institution_id": str(seeded.institution_id),
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


def _direct_state(seeded: _Seeded, **overrides: object) -> TextToSQLState:
    state: TextToSQLState = {
        "question": "how many institutions are there?",
        "user_id": str(seeded.user_id),
        "user_role": "admin",
        "institution_id": str(seeded.institution_id),
        "generated_sql": "SELECT COUNT(*) AS count FROM institutions",
        "validated_sql": "SELECT COUNT(*) AS count FROM institutions LIMIT 500",
        "retry_count": 0,
        "query_result": [{"count": 1}],
        "result_row_count": 1,
        "natural_answer": "The count is 1.",
        "confidence": "high",
        "provenance": "Queried: institutions.",
        "error": None,
        "audit_entry": None,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


# --- Successful run: complete audit record, including raw SQL --------------------------


async def test_successful_run_produces_a_complete_audit_record(
    monkeypatch: pytest.MonkeyPatch, seeded: _Seeded, async_session: AsyncSession
) -> None:
    async def _fake_generate_sql(state: TextToSQLState) -> TextToSQLState:
        return {**state, "generated_sql": "SELECT id FROM institutions", "error": None}

    monkeypatch.setattr(graph_module, "generate_sql", _fake_generate_sql)

    graph = build_text_to_sql_graph()
    result = await graph.ainvoke(_initial_state(seeded), config={"recursion_limit": 15})
    assert result["error"] is None

    event = await _latest_audit_event(async_session, seeded.institution_id)
    assert event.event_type == "data.ask"
    assert event.entity_type == "text_to_sql_query"
    assert event.actor_user_id == seeded.user_id
    assert event.institution_id == seeded.institution_id

    payload = event.payload
    assert payload["question"] == "how many institutions are there?"
    assert payload["user_role"] == "admin"
    assert payload["generated_sql"] == "SELECT id FROM institutions"
    assert "institutions" in payload["validated_sql"]
    # the real, post-validation SQL (LIMIT injected), not just the raw model output
    assert "LIMIT" in payload["validated_sql"]
    assert payload["result_row_count"] == 1
    assert payload["confidence"] == "high"
    assert payload["sanity_check_triggers"] == []
    assert payload["retry_count"] == 0
    assert payload["outcome"] == "answered"
    assert payload["error_category"] is None
    # link_schema ran for real here (only generate_sql is faked above) -- confirms its
    # selection reaches the audit trail, not just state passed within the graph run.
    assert payload["schema_linking_tables_selected"] is not None
    assert "institutions" in payload["schema_linking_tables_selected"]


# --- Refused run: audit coverage must not silently drop refusals -----------------------


async def test_refused_run_also_produces_a_complete_audit_record(
    monkeypatch: pytest.MonkeyPatch, seeded: _Seeded, async_session: AsyncSession
) -> None:
    async def _fake_generate_sql(state: TextToSQLState) -> TextToSQLState:
        # Account tables are rejected by validate_sql before apply_role_scope, and a
        # fake that keeps returning them retries until the recursion limit. This query
        # is in the whitelist, then a student reading teaching_assignments is a
        # ROLE_VIOLATION with no retry. The literal keeps password_hash in the audit.
        return {
            **state,
            "generated_sql": (
                "SELECT teacher_user_id FROM teaching_assignments "
                "WHERE teacher_user_id = 'password_hash'"
            ),
            "error": None,
        }

    monkeypatch.setattr(graph_module, "generate_sql", _fake_generate_sql)

    graph = build_text_to_sql_graph()
    state = _initial_state(seeded)
    state["user_role"] = "student"
    result = await graph.ainvoke(state, config={"recursion_limit": 15})
    assert error_category(result["error"]) == ROLE_VIOLATION

    event = await _latest_audit_event(async_session, seeded.institution_id)
    payload = event.payload
    assert payload["outcome"] == "refused"
    assert payload["error_category"] == ROLE_VIOLATION
    assert payload["error_detail"] == result["error"]
    # The rejected SQL is still on record, even though it was never run.
    assert payload["validated_sql"] is None
    assert "password_hash" in (payload["generated_sql"] or "")


async def test_load_schema_failure_audit_record_still_captures_the_real_detail(
    tmp_path: Path, seeded: _Seeded, async_session: AsyncSession
) -> None:
    # Fix: load_schema now formats its errors via format_error()/SCHEMA_ERROR like every
    # other node, closing the gap that used to make audit_log keep error_detail
    # specifically "so nothing gets lost" for load_schema's own unformatted string.
    # Confirms that fix didn't lose anything: the real SchemaCatalogError text (which can
    # carry the catalog file's real path) is still fully present in error_detail.
    original_path = _LOAD_SCHEMA_MODULE.SCHEMA_CATALOG_PATH
    _LOAD_SCHEMA_MODULE.SCHEMA_CATALOG_PATH = tmp_path / "missing-for-audit-test.md"
    try:
        graph = build_text_to_sql_graph()
        result = await graph.ainvoke(_initial_state(seeded), config={"recursion_limit": 15})
    finally:
        _LOAD_SCHEMA_MODULE.SCHEMA_CATALOG_PATH = original_path
        _load_filtered_schema_context.cache_clear()

    assert error_category(result["error"]) == SCHEMA_ERROR

    event = await _latest_audit_event(async_session, seeded.institution_id)
    payload = event.payload
    assert payload["outcome"] == "refused"
    assert payload["error_category"] == SCHEMA_ERROR
    # The real detail -- including the actual (missing) file path -- is still on record,
    # not just the category, exactly as it is for every other node's errors.
    assert payload["error_detail"] == result["error"]
    assert "missing-for-audit-test.md" in payload["error_detail"]


# --- Raw SQL: present in the audit record, never in user-facing fields -----------------


async def test_raw_sql_present_in_audit_but_never_in_user_facing_fields(
    monkeypatch: pytest.MonkeyPatch, seeded: _Seeded, async_session: AsyncSession
) -> None:
    async def _fake_generate_sql(state: TextToSQLState) -> TextToSQLState:
        return {**state, "generated_sql": "SELECT id FROM institutions", "error": None}

    monkeypatch.setattr(graph_module, "generate_sql", _fake_generate_sql)

    graph = build_text_to_sql_graph()
    result = await graph.ainvoke(_initial_state(seeded), config={"recursion_limit": 15})

    event = await _latest_audit_event(async_session, seeded.institution_id)
    assert "SELECT" in event.payload["validated_sql"]

    natural_answer = result["natural_answer"] or ""
    provenance = result["provenance"] or ""
    assert "SELECT" not in natural_answer
    assert "SELECT" not in provenance
    assert event.payload["validated_sql"] not in natural_answer
    assert event.payload["validated_sql"] not in provenance


# --- Fail-closed: an audit write failure withholds the answer, doesn't crash -----------


async def test_audit_write_failure_withholds_the_answer_fail_closed(seeded: _Seeded) -> None:
    # A genuine DB-level failure, not a mock: institution_id has no matching row in
    # `institutions`, so record_event's INSERT violates the real FK constraint.
    nonexistent_institution_id = uuid4()
    composed_answer = "The count is 1."
    state = _direct_state(
        seeded,
        institution_id=str(nonexistent_institution_id),
        natural_answer=composed_answer,
    )

    result = await audit_log(state)

    assert error_category(result["error"]) == AUDIT_ERROR
    assert result["natural_answer"] != composed_answer
    assert result["natural_answer"]  # still a real, honest string, not empty/None
    assert "could not be" in (result["natural_answer"] or "").lower()


async def test_malformed_user_id_fails_closed_without_crashing(seeded: _Seeded) -> None:
    # A different failure mode from the FK-violation test above: state["user_id"] itself
    # isn't a parseable UUID at all. Every upstream node treats it as an opaque string;
    # this is the first place it's actually parsed as a UUID, so this must be a
    # controlled fail-closed response, not an uncaught ValueError crashing the graph.
    composed_answer = "The count is 1."
    state = _direct_state(seeded, user_id="not-a-real-uuid", natural_answer=composed_answer)

    result = await audit_log(state)

    assert error_category(result["error"]) == AUDIT_ERROR
    assert result["natural_answer"] != composed_answer
    assert result["natural_answer"]


async def test_audit_write_failure_still_logs_the_discarded_content(
    seeded: _Seeded, caplog: pytest.LogCaptureFixture
) -> None:
    # Fail-closed withholds the *answer* from the user, but the content that would have
    # been the audit row must not simply vanish -- it needs to be recoverable from the
    # application logger, otherwise a DB hiccup loses the information outright rather
    # than just leaving it unaudited.
    nonexistent_institution_id = uuid4()
    distinctive_question = "how many students are in Ms Distinctive-Marker-9F2E's class?"
    state = _direct_state(
        seeded,
        institution_id=str(nonexistent_institution_id),
        question=distinctive_question,
        generated_sql="SELECT id FROM institutions -- Distinctive-Marker-9F2E",
    )

    caplog.set_level("ERROR")
    await audit_log(state)

    logged_text = "\n".join(record.getMessage() for record in caplog.records)
    assert distinctive_question in logged_text
    assert "Distinctive-Marker-9F2E" in logged_text


async def test_audit_write_success_does_not_alter_the_answer(
    seeded: _Seeded, async_session: AsyncSession
) -> None:
    state = _direct_state(seeded)
    result = await audit_log(state)

    assert result["error"] is None
    assert result["natural_answer"] == "The count is 1."

    event = await _latest_audit_event(async_session, seeded.institution_id)
    assert event.payload["outcome"] == "answered"


# --- query_result row contents excluded from the persisted payload ---------------------


async def test_query_result_row_contents_excluded_from_persisted_payload(
    seeded: _Seeded, async_session: AsyncSession
) -> None:
    marker = "Unmistakable-Test-Student-Name-X123"
    state = _direct_state(
        seeded,
        query_result=[{"student_id": 1, "full_name": marker, "mastery_percent": 91.5}],
        result_row_count=1,
    )

    await audit_log(state)

    event = await _latest_audit_event(async_session, seeded.institution_id)
    serialized_payload = repr(event.payload)
    assert marker not in serialized_payload
    assert "91.5" not in serialized_payload
    assert event.payload["result_row_count"] == 1
    assert "query_result" not in event.payload
