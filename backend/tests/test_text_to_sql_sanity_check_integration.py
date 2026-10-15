"""Postgres-backed tests for the text-to-SQL sanity_check node.

Two things that need a live database and can't be proven by a pure unit test:

1. state["confidence"] surviving unchanged from sanity_check, through routing, through
   the still-placeholder compose_answer, all the way out of the compiled graph.
2. The row-cap-truncation check actually chained from a real execute_sql (Task 7)
   truncation — not a hand-set audit_entry flag, which test_text_to_sql_sanity_check.py
   already covers as a pure unit test on the flag in isolation.
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
from education_platform.modules.text_to_sql.nodes.execute_sql import execute_sql
from education_platform.modules.text_to_sql.nodes.sanity_check import sanity_check
from education_platform.modules.text_to_sql.state import TextToSQLState


class _ExecuteSqlModule(Protocol):
    ROW_CAP: int


# `nodes/__init__.py` does `from .execute_sql import execute_sql as execute_sql`, which
# rebinds the `nodes.execute_sql` *attribute* from the submodule to the function (same
# gotcha as elsewhere in this test suite — see test_text_to_sql_execute_sql.py). Reach
# the real submodule via sys.modules to monkeypatch ROW_CAP.
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
    catch. This file is about sanity_check's own behavior, not injection_guard's --
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
    sanity_check's own behavior, not question_validator's -- default every question here
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
        inst = Institution(name="Sanity-Check Test School", timezone="UTC")
        session.add(inst)
        session.commit()
        institution_id = inst.id
    engine.dispose()
    yield institution_id


@pytest.fixture()
def seeded_admin_user_id(seeded_institution: UUID, clean_db: str) -> Iterator[UUID]:
    # A real users.id row: audit_log (Task 10) now runs unconditionally at the end of
    # every full graph invocation and needs a real UUID satisfying AuditEvent.actor_user_id's
    # FK — a placeholder string would make the full-graph tests below exercise audit_log's
    # failure path instead of its success path.
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        user = User(
            institution_id=seeded_institution,
            email="admin@sanity-check-test.school",
            full_name="Admin",
            password_hash="unused",
        )
        session.add(user)
        session.commit()
        user_id = user.id
    engine.dispose()
    yield user_id


def _initial_state(institution_id: UUID, user_id: UUID) -> TextToSQLState:
    return {
        "question": "how many institutions are there?",
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


# --- confidence reaches compose_answer unchanged, through the full compiled graph -------


async def test_confidence_reaches_compose_answer_unchanged_through_full_graph(
    monkeypatch: pytest.MonkeyPatch, seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    async def _fake_generate_sql(state: TextToSQLState) -> TextToSQLState:
        return {**state, "generated_sql": "SELECT id FROM institutions", "error": None}

    monkeypatch.setattr(graph_module, "generate_sql", _fake_generate_sql)

    graph = build_text_to_sql_graph()
    result = await graph.ainvoke(
        _initial_state(seeded_institution, seeded_admin_user_id), config={"recursion_limit": 15}
    )

    assert result["error"] is None
    # One real institution row, question is aggregate-shaped ("how many") — clean per
    # sanity_check's own logic (unit-tested in isolation in test_text_to_sql_sanity_check.py):
    # "high", no triggers. compose_answer is still a pass-through placeholder (Task 9 not
    # yet implemented), so this value can only have survived unmodified from sanity_check.
    assert result["confidence"] == "high"
    audit_entry = result["audit_entry"]
    assert audit_entry is not None
    assert audit_entry["sanity_check_triggers"] == []


async def test_low_confidence_also_reaches_compose_answer_unchanged_through_full_graph(
    monkeypatch: pytest.MonkeyPatch, seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    # Same graph path, but forced through the "suspicious" edge: zero rows.
    async def _fake_generate_sql(state: TextToSQLState) -> TextToSQLState:
        return {
            **state,
            "generated_sql": "SELECT id FROM institutions WHERE name = 'no such school'",
            "error": None,
        }

    monkeypatch.setattr(graph_module, "generate_sql", _fake_generate_sql)

    graph = build_text_to_sql_graph()
    result = await graph.ainvoke(
        _initial_state(seeded_institution, seeded_admin_user_id), config={"recursion_limit": 15}
    )

    assert result["error"] is None
    assert result["result_row_count"] == 0
    assert result["confidence"] == "medium"  # zero_rows's own severity, per sanity_check.py
    audit_entry = result["audit_entry"]
    assert audit_entry is not None
    assert any(t.startswith("zero_rows:") for t in audit_entry["sanity_check_triggers"])


# --- Fix (Finding 1): COUNT-shaped and list-shaped zero get matching confidence -------


async def test_zero_valued_count_and_zero_row_list_get_matching_confidence_through_full_graph(
    monkeypatch: pytest.MonkeyPatch, seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    # Same real-world fact ("no institution matches"), phrased once as a COUNT (returns
    # exactly one row, value 0) and once as a list (returns zero rows) — before this fix,
    # the COUNT phrasing read as "high" confidence purely because SELECT COUNT(*) always
    # returns one row even when the count is 0, so sanity_check's zero_rows check
    # (result_row_count == 0) could never catch it.
    async def _fake_generate_sql_count(state: TextToSQLState) -> TextToSQLState:
        return {
            **state,
            "generated_sql": "SELECT COUNT(*) AS n FROM institutions WHERE name = 'no such school'",
            "error": None,
        }

    monkeypatch.setattr(graph_module, "generate_sql", _fake_generate_sql_count)
    count_graph = build_text_to_sql_graph()
    count_state = _initial_state(seeded_institution, seeded_admin_user_id)
    count_state["question"] = "how many institutions match 'no such school'?"
    count_result = await count_graph.ainvoke(count_state, config={"recursion_limit": 15})

    assert count_result["error"] is None
    assert count_result["result_row_count"] == 1  # COUNT(*) always returns one row
    assert count_result["confidence"] == "medium"
    count_triggers = count_result["audit_entry"]["sanity_check_triggers"]
    assert any(t.startswith("zero_valued_aggregate:") for t in count_triggers)

    async def _fake_generate_sql_list(state: TextToSQLState) -> TextToSQLState:
        return {
            **state,
            "generated_sql": "SELECT id FROM institutions WHERE name = 'no such school'",
            "error": None,
        }

    monkeypatch.setattr(graph_module, "generate_sql", _fake_generate_sql_list)
    list_graph = build_text_to_sql_graph()
    list_state = _initial_state(seeded_institution, seeded_admin_user_id)
    list_state["question"] = "which institutions match 'no such school'?"
    list_result = await list_graph.ainvoke(list_state, config={"recursion_limit": 15})

    assert list_result["error"] is None
    assert list_result["result_row_count"] == 0
    assert list_result["confidence"] == "medium"

    # The whole point: two phrasings of the identical underlying fact no longer diverge.
    assert count_result["confidence"] == list_result["confidence"]


async def test_null_valued_aggregate_downgrades_confidence_end_to_end(
    monkeypatch: pytest.MonkeyPatch, seeded_institution: UUID, seeded_admin_user_id: UUID
) -> None:
    # NULL-valued-aggregate (AVG over zero matching rows) now downgrades to "medium"
    # through the full graph, same as a literal zero would — a real, confirmed-live bug
    # fixed here (confidence used to stay "high" for a legitimately-empty AVG() result,
    # with nothing anywhere flagging the uncertainty except the answer text itself).
    # Regression-tested end to end, not just at the sanity_check-unit level above.
    async def _fake_generate_sql(state: TextToSQLState) -> TextToSQLState:
        return {
            **state,
            "generated_sql": ("SELECT AVG(1) AS n FROM institutions WHERE name = 'no such school'"),
            "error": None,
        }

    monkeypatch.setattr(graph_module, "generate_sql", _fake_generate_sql)
    graph = build_text_to_sql_graph()
    state = _initial_state(seeded_institution, seeded_admin_user_id)
    state["question"] = "what is the average for 'no such school'?"
    result = await graph.ainvoke(state, config={"recursion_limit": 15})

    assert result["error"] is None
    assert result["result_row_count"] == 1
    assert result["query_result"] == [{"n": None}]
    assert result["confidence"] == "medium"
    triggers = result["audit_entry"]["sanity_check_triggers"]
    assert len(triggers) == 1
    assert triggers[0].startswith("zero_valued_aggregate:")


# --- row-cap truncation, chained from a real execute_sql truncation --------------------


async def test_row_cap_truncation_chains_from_a_real_execute_sql_truncation(
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
    assert exec_result["result_row_count"] == 10

    sanity_state: TextToSQLState = {**exec_result, "question": "list all quiz attempts"}
    result = await sanity_check(sanity_state)

    assert result["confidence"] == "low"
    triggers = result["audit_entry"]["sanity_check_triggers"]  # type: ignore[index]
    assert any(t.startswith("row_cap_truncated:") for t in triggers)
    # The flag execute_sql set survives into sanity_check's own audit additions, merged
    # rather than clobbered.
    assert result["audit_entry"]["row_cap_truncated"] is True  # type: ignore[index]
