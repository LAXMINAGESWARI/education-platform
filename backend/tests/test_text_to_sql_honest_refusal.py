"""Unit tests for the text-to-SQL honest_refusal node.

No database needed for honest_refusal itself — it's pure dict lookups and string
formatting — with one exception: test_retries_exhausted_full_graph_produces_a_refusal_and
_a_correctly_categorized_audit_record runs the full compiled graph, and audit_log
(Task 10) needs Postgres. Same pattern as test_text_to_sql_load_schema.py's one
graph-level test.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Protocol, cast
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

import education_platform.modules.text_to_sql.graph as graph_module
from education_platform.db.url import to_async_url, to_sync_url
from education_platform.modules.auth.models import AuditEvent, Institution, User
from education_platform.modules.text_to_sql.graph import build_text_to_sql_graph
from education_platform.modules.text_to_sql.nodes.honest_refusal import honest_refusal
from education_platform.modules.text_to_sql.state import (
    AUDIT_ERROR,
    EXECUTION_ERROR,
    INJECTION_BLOCKED,
    LLM_ERROR,
    OFF_TOPIC_REJECTED,
    ROLE_VIOLATION,
    SCHEMA_ERROR,
    VALIDATION_ERROR,
    TextToSQLState,
    format_error,
)

ALL_CATEGORIES = (
    SCHEMA_ERROR,
    LLM_ERROR,
    VALIDATION_ERROR,
    ROLE_VIOLATION,
    EXECUTION_ERROR,
    AUDIT_ERROR,
    INJECTION_BLOCKED,
    OFF_TOPIC_REJECTED,
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
    already catch -- including the one full-graph test in this file. Default every
    question here to "not an injection" so it doesn't depend on network availability to
    pass.
    """

    async def _fake(**_kwargs: object) -> float:
        return 0.0

    monkeypatch.setattr(_INJECTION_GUARD_MODULE, "classify_noul", _fake)


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
    OpenRouter classifier call for every question that reaches it, including the one
    full-graph test in this file. Default every question here to "not off-topic" so it
    doesn't depend on network availability to pass.
    """

    async def _fake_ot(**_kwargs: object) -> float:
        return 0.0

    monkeypatch.setattr(_QUESTION_VALIDATOR_MODULE, "classify_noul", _fake_ot)


# Distinctive markers that must never survive into natural_answer, standing in for the
# kinds of internals a real error/SQL string would carry.
_SECRET_SQL = "SELECT password_hash FROM users WHERE id = 'Marker-Row-9F2E'"
_SECRET_DETAIL = "constraint ck_ingest_jobs_9F2E_marker violated on table quiz_attempts"


def _state(*, error: str | None, **overrides: object) -> TextToSQLState:
    state: TextToSQLState = {
        "question": "irrelevant",
        "generated_sql": _SECRET_SQL,
        "validated_sql": None,
        "retry_count": 3,
        "confidence": None,
        "error": error,
        "audit_entry": None,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _answer(result: TextToSQLState) -> str:
    value = result["natural_answer"]
    assert value is not None
    return value


# --- One message per category, category-appropriate -------------------------------------


async def test_schema_error_message() -> None:
    result = await honest_refusal(_state(error=format_error(SCHEMA_ERROR, _SECRET_DETAIL)))
    answer = _answer(result).lower()
    assert "our end" in answer or "went wrong" in answer


async def test_llm_error_message() -> None:
    result = await honest_refusal(_state(error=format_error(LLM_ERROR, _SECRET_DETAIL)))
    answer = _answer(result).lower()
    assert "ai service" in answer or "try again" in answer


async def test_validation_error_message() -> None:
    result = await honest_refusal(_state(error=format_error(VALIDATION_ERROR, _SECRET_DETAIL)))
    answer = _answer(result).lower()
    assert "rephrasing" in answer or "safe query" in answer


async def test_role_violation_message() -> None:
    result = await honest_refusal(_state(error=format_error(ROLE_VIOLATION, _SECRET_DETAIL)))
    answer = _answer(result).lower()
    assert "access" in answer or "don't have" in answer


async def test_execution_error_message() -> None:
    result = await honest_refusal(_state(error=format_error(EXECUTION_ERROR, _SECRET_DETAIL)))
    answer = _answer(result).lower()
    assert "our end" in answer or "went wrong" in answer


async def test_audit_error_message() -> None:
    result = await honest_refusal(_state(error=format_error(AUDIT_ERROR, _SECRET_DETAIL)))
    answer = _answer(result).lower()
    assert "our end" in answer or "went wrong" in answer


async def test_off_topic_rejected_message() -> None:
    result = await honest_refusal(_state(error=format_error(OFF_TOPIC_REJECTED, _SECRET_DETAIL)))
    answer = _answer(result).lower()
    assert "school" in answer or "in scope" in answer


async def test_off_topic_rejected_and_injection_blocked_read_differently() -> None:
    # OFF_TOPIC_REJECTED fires for entirely benign questions ("what's the weather") --
    # reusing INJECTION_BLOCKED's wording would misrepresent a scope mismatch as an
    # accusation of attempting an attack.
    off_topic = _answer(
        await honest_refusal(_state(error=format_error(OFF_TOPIC_REJECTED, _SECRET_DETAIL)))
    )
    injection_blocked = _answer(
        await honest_refusal(_state(error=format_error(INJECTION_BLOCKED, _SECRET_DETAIL)))
    )
    assert off_topic != injection_blocked


async def test_role_violation_and_execution_error_read_differently() -> None:
    # "You can't see this" and "our side broke" are different facts a user should be able
    # to tell apart — must not collapse to the same generic phrasing.
    role_violation = _answer(
        await honest_refusal(_state(error=format_error(ROLE_VIOLATION, _SECRET_DETAIL)))
    )
    execution_error = _answer(
        await honest_refusal(_state(error=format_error(EXECUTION_ERROR, _SECRET_DETAIL)))
    )
    assert role_violation != execution_error
    assert "access" in role_violation.lower()
    assert "access" not in execution_error.lower()


async def test_execution_error_and_audit_error_share_the_generic_infrastructure_message() -> None:
    # Both are the task's own "infrastructure failure, not the user's fault" bucket.
    execution_error = _answer(
        await honest_refusal(_state(error=format_error(EXECUTION_ERROR, _SECRET_DETAIL)))
    )
    audit_error = _answer(
        await honest_refusal(_state(error=format_error(AUDIT_ERROR, _SECRET_DETAIL)))
    )
    assert execution_error == audit_error


async def test_schema_error_shares_the_generic_infrastructure_message_too() -> None:
    # SCHEMA_ERROR joins the same bucket as EXECUTION_ERROR/AUDIT_ERROR for the same
    # reason: infrastructure failure, not the user's fault, "try rephrasing" wouldn't help.
    schema_error = _answer(
        await honest_refusal(_state(error=format_error(SCHEMA_ERROR, _SECRET_DETAIL)))
    )
    execution_error = _answer(
        await honest_refusal(_state(error=format_error(EXECUTION_ERROR, _SECRET_DETAIL)))
    )
    assert schema_error == execution_error


# --- Never leaks internals, across every category ----------------------------------------


@pytest.mark.parametrize("category", ALL_CATEGORIES)
async def test_no_internals_leak_for_any_category(category: str) -> None:
    result = await honest_refusal(
        _state(
            error=format_error(category, _SECRET_DETAIL),
            generated_sql=_SECRET_SQL,
            validated_sql=_SECRET_SQL,
            retry_count=3,
        )
    )
    answer = _answer(result)
    assert _SECRET_SQL not in answer
    assert _SECRET_DETAIL not in answer
    assert "password_hash" not in answer
    assert "quiz_attempts" not in answer
    assert "SELECT" not in answer
    assert "3" not in answer  # retry_count must not appear either
    assert category not in answer  # no raw category token, e.g. "ROLE_VIOLATION"
    assert "honest_refusal" not in answer  # no internal node names


# --- Missing/unrecognized category: graceful fallback, never crashes --------------------


async def test_missing_error_falls_back_gracefully() -> None:
    result = await honest_refusal(_state(error=None))
    answer = _answer(result)
    assert answer  # a real, non-empty string
    assert "went wrong" in answer.lower()


async def test_unrecognized_error_text_falls_back_gracefully() -> None:
    # Every node in this pipeline formats state["error"] via format_error() today (see
    # state.py's own category list), but this fallback must not assume that always holds
    # -- a category consumer shouldn't break the moment some future producer regresses.
    # An arbitrary, deliberately-unformatted string exercises that defensively, not tied
    # to any one node's real (and currently correct) behavior.
    result = await honest_refusal(_state(error="some prehistoric unformatted failure text"))
    answer = _answer(result)
    assert answer
    assert "went wrong" in answer.lower()
    assert "prehistoric" not in answer


async def test_empty_state_does_not_crash() -> None:
    # As defensive as it gets: no error key at all.
    state: TextToSQLState = {}
    result = await honest_refusal(state)
    assert result.get("natural_answer")


# --- state["confidence"] set to a failure indicator if not already set ------------------


async def test_confidence_set_to_low_when_unset() -> None:
    result = await honest_refusal(_state(error=format_error(VALIDATION_ERROR, "x")))
    assert result["confidence"] == "low"


async def test_confidence_left_alone_if_already_set() -> None:
    result = await honest_refusal(
        _state(error=format_error(VALIDATION_ERROR, "x"), confidence="medium")
    )
    assert result["confidence"] == "medium"


# --- state["error"] is never overwritten --------------------------------------------------


async def test_error_is_unchanged_after_this_node() -> None:
    original = format_error(ROLE_VIOLATION, _SECRET_DETAIL)
    result = await honest_refusal(_state(error=original))
    assert result["error"] == original


# --- Full graph: retries exhausted -> honest_refusal -> audit_log -----------------------


@pytest.fixture()
def seeded_admin_user(clean_db: str) -> Iterator[tuple[UUID, UUID]]:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        inst = Institution(name="Honest-Refusal Test School", timezone="UTC")
        session.add(inst)
        session.flush()
        user = User(
            institution_id=inst.id,
            email="admin@honest-refusal-test.school",
            full_name="Admin",
            password_hash="unused",
        )
        session.add(user)
        session.commit()
        ids = (inst.id, user.id)
    engine.dispose()
    yield ids


async def test_retries_exhausted_full_graph_produces_a_refusal_and_a_correctly_categorized_audit_record(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch, seeded_admin_user: tuple[UUID, UUID], clean_db: str
) -> None:
    institution_id, user_id = seeded_admin_user

    async def _always_invalid_generate_sql(state: TextToSQLState) -> TextToSQLState:
        # Always references a table outside the schema whitelist -> validate_sql rejects
        # every attempt -> retry_count climbs to MAX_RETRIES -> "refuse" edge.
        retry_count = state.get("retry_count", 0)
        return {
            **state,
            "retry_count": retry_count + 1 if state.get("error") else retry_count,
            "generated_sql": "SELECT id FROM not_a_real_table",
            "error": None,
        }

    monkeypatch.setattr(graph_module, "generate_sql", _always_invalid_generate_sql)

    graph = build_text_to_sql_graph()
    initial: TextToSQLState = {
        "question": "irrelevant",
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
    result = await graph.ainvoke(initial, config={"recursion_limit": 20})

    assert result["natural_answer"]
    assert "not_a_real_table" not in result["natural_answer"]
    assert result["confidence"] == "low"

    engine = create_async_engine(to_async_url(clean_db), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        event = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.institution_id == institution_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(1)
        )
    await engine.dispose()

    assert event is not None
    assert event.payload["outcome"] == "refused"
    assert event.payload["error_category"] == VALIDATION_ERROR
