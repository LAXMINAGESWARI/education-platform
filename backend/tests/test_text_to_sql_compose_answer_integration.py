"""Postgres-backed tests for the text-to-SQL compose_answer node.

One thing here needs a live database and can't be proven by a pure unit test: the
row-cap-truncation disclosure sentence chained from a *real* execute_sql (Task 7)
truncation through sanity_check (Task 8) into compose_answer — not a hand-set
audit_entry flag, which test_text_to_sql_compose_answer.py already covers as a pure unit
test in isolation. Same rigor as Task 8's equivalent chained test.

Also includes one full-compiled-graph smoke test, now that compose_answer is no longer a
pass-through placeholder — confirming a real natural_answer/provenance actually comes out
the other end of the whole pipeline, not just confidence (which Task 8's own integration
tests already covered).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Protocol, cast
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import education_platform.modules.text_to_sql.graph as graph_module
from education_platform.db.url import to_sync_url
from education_platform.modules.auth.models import Institution, User
from education_platform.modules.text_to_sql.graph import build_text_to_sql_graph
from education_platform.modules.text_to_sql.nodes.compose_answer import (
    _TRUNCATION_DISCLOSURE,
    compose_answer,
)
from education_platform.modules.text_to_sql.nodes.execute_sql import execute_sql
from education_platform.modules.text_to_sql.nodes.sanity_check import sanity_check
from education_platform.modules.text_to_sql.state import TextToSQLState


class _ExecuteSqlModule(Protocol):
    ROW_CAP: int


# `nodes/__init__.py` does `from .execute_sql import execute_sql as execute_sql`, which
# rebinds the `nodes.execute_sql` *attribute* from the submodule to the function (same
# gotcha as elsewhere in this test suite — see test_text_to_sql_execute_sql.py). Reach the
# real submodule via sys.modules to monkeypatch ROW_CAP.
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
    catch. This file is about compose_answer's own behavior, not injection_guard's --
    default every question here to "not an injection" so these tests don't silently make
    a real, unmocked API call and don't depend on network availability to pass.
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
    OpenRouter classifier call for every question that reaches it. This file is about
    compose_answer's own behavior, not question_validator's -- default every question
    here to "not off-topic" so these tests don't silently make a real, unmocked API call.
    """

    async def _fake_ot(**_kwargs: object) -> float:
        return 0.0

    monkeypatch.setattr(_QUESTION_VALIDATOR_MODULE, "classify_noul", _fake_ot)


@pytest.fixture()
def seeded_institution(clean_db: str) -> Iterator[UUID]:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        inst = Institution(name="Compose-Answer Test School", timezone="UTC")
        session.add(inst)
        session.commit()
        institution_id = inst.id
    engine.dispose()
    yield institution_id


@pytest.fixture()
def seeded_admin_user_id(seeded_institution: UUID, clean_db: str) -> Iterator[UUID]:
    # A real users.id row: audit_log (Task 10) now runs unconditionally at the end of
    # every full graph invocation and needs a real UUID satisfying AuditEvent.actor_user_id's
    # FK — a placeholder string would make the full-graph test below exercise audit_log's
    # failure path instead of its success path.
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        user = User(
            institution_id=seeded_institution,
            email="admin@compose-answer-test.school",
            full_name="Admin",
            password_hash="unused",
        )
        session.add(user)
        session.commit()
        user_id = user.id
    engine.dispose()
    yield user_id


# --- row-cap truncation, chained from a real execute_sql -> sanity_check truncation ----


async def test_row_cap_disclosure_chains_from_a_real_execute_sql_truncation(
    monkeypatch: pytest.MonkeyPatch, clean_db: str
) -> None:
    monkeypatch.setattr(_MODULE, "ROW_CAP", 10)

    exec_state: TextToSQLState = {
        "validated_sql": "SELECT generate_series(1, 50) AS n",
        # execute_sql now sets these unconditionally for RLS (migration e1f2a3b4c5d6) --
        # arbitrary but present is enough here since generate_series touches no
        # RLS-protected table at all; a real seeded fixture isn't needed for this query.
        "user_id": "00000000-0000-0000-0000-000000000001",
        "user_role": "admin",
        "institution_id": "00000000-0000-0000-0000-000000000002",
        "error": None,
        "retry_count": 0,
        "audit_entry": None,
    }
    exec_result = await execute_sql(exec_state)
    exec_audit_entry = exec_result["audit_entry"]
    assert exec_audit_entry is not None
    assert exec_audit_entry["row_cap_truncated"] is True

    sanity_state: TextToSQLState = {**exec_result, "question": "list all quiz attempts"}
    sanity_result = await sanity_check(sanity_state)
    assert sanity_result["confidence"] == "low"

    result = await compose_answer(sanity_result)

    natural_answer = result["natural_answer"]
    assert natural_answer is not None
    assert _TRUNCATION_DISCLOSURE in natural_answer


# --- full compiled graph: a genuine natural_answer comes out the other end -------------


async def test_full_graph_produces_a_real_natural_answer_and_provenance(
    monkeypatch: pytest.MonkeyPatch, seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    async def _fake_generate_sql(state: TextToSQLState) -> TextToSQLState:
        return {**state, "generated_sql": "SELECT id FROM institutions", "error": None}

    monkeypatch.setattr(graph_module, "generate_sql", _fake_generate_sql)

    graph = build_text_to_sql_graph()
    initial: TextToSQLState = {
        "question": "how many institutions are there?",
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

    assert result["error"] is None
    assert result["confidence"] == "high"
    natural_answer = result["natural_answer"]
    assert natural_answer is not None
    assert natural_answer != ""
    provenance = result["provenance"]
    assert provenance is not None
    assert "institutions" in provenance
    assert "SELECT" not in provenance
