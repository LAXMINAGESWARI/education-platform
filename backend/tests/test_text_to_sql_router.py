"""End-to-end tests through the real HTTP layer for POST /api/v1/text-to-sql/ask.

Same pattern as test_insights_api.py: a generated school (`synthetic.generator`), real
JWTs from `/api/v1/auth/login`, and the actual FastAPI app via the `client` fixture — not
the compiled graph invoked directly. `chat_completion` (inside nodes.generate_sql's own
module namespace) is monkeypatched to avoid depending on a live OpenRouter key; everything
else — auth, role gating, apply_role_scope, execute_sql against the real restricted DB
role, sanity_check, compose_answer — runs for real.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Protocol, cast
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from education_platform.core.config import Settings
from education_platform.db.url import to_sync_url
from education_platform.modules.synthetic.generator import SchoolSpec, generate_school

TEST_SPEC = SchoolSpec(sections_per_grade=2, students_per_section=3, term_weeks=2)
SCHOOL = TEST_SPEC.institution_name
PASSWORD = "demo1234"

ADMIN = "fatima.almansouri@alnoor.school"
TEACHER = "meera.krishnan@alnoor.school"
STUDENT = "student25@alnoor.school"

ASK_URL = "/api/v1/text-to-sql/ask"


class _GenerateSqlModule(Protocol):
    chat_completion: AsyncMock


# `nodes/__init__.py` does `from .generate_sql import generate_sql as generate_sql`, which
# rebinds the `nodes.generate_sql` *attribute* from the submodule to the function (same
# gotcha as test_text_to_sql_generate_sql.py). Reach the real submodule via sys.modules to
# monkeypatch chat_completion — this works regardless of when the router's module-level
# `_GRAPH` singleton was compiled, since `generate_sql` re-reads this module's own global
# `chat_completion` fresh on every call, not once at graph-build time.
_MODULE = cast(
    _GenerateSqlModule, sys.modules["education_platform.modules.text_to_sql.nodes.generate_sql"]
)


class _InjectionGuardModule(Protocol):
    chat_completion_json: AsyncMock


_INJECTION_GUARD_MODULE = cast(
    _InjectionGuardModule,
    sys.modules["education_platform.modules.text_to_sql.nodes.injection_guard"],
)


@pytest.fixture(autouse=True)
def _pass_injection_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """injection_guard is the graph's real entry node now, ahead of load_schema, and
    makes its own live OpenRouter classifier call for any question its heuristic regex
    doesn't already catch. Every test in this file is about routing/auth/scoping, not
    injection_guard itself -- default every question here to "not an injection" so these
    HTTP-level tests don't silently make a real, unmocked API call per request.
    """
    monkeypatch.setattr(
        _INJECTION_GUARD_MODULE,
        "classify_noul",
        AsyncMock(return_value=0.0),
    )


class _QuestionValidatorModule(Protocol):
    chat_completion_json: AsyncMock


_QUESTION_VALIDATOR_MODULE = cast(
    _QuestionValidatorModule,
    sys.modules["education_platform.modules.text_to_sql.nodes.question_validator"],
)


@pytest.fixture(autouse=True)
def _pass_question_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """question_validator is the node right after injection_guard, and makes its own live
    OpenRouter classifier call for every question that reaches it. Every test in this file
    is about routing/auth/scoping, not question_validator itself -- default every question
    here to "not off-topic" so these HTTP-level tests don't silently make a real, unmocked
    API call per request.
    """
    monkeypatch.setattr(
        _QUESTION_VALIDATOR_MODULE,
        "classify_noul",
        AsyncMock(return_value=0.0),
    )


@pytest.fixture()
def api(client: TestClient, clean_db: str) -> Iterator[TestClient]:
    """The real app, backed by a generated school."""
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        generate_school(session, TEST_SPEC)
        session.commit()
    engine.dispose()
    yield client


def _headers(api: TestClient, email: str) -> dict[str, str]:
    response = api.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "institution_name": SCHOOL},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _mock_generated_sql(sql: str) -> AsyncMock:
    async def _fake(
        messages: list[dict[str, str]], *, settings: Settings, temperature: float = 0.0
    ) -> str:
        return f"```sql\n{sql}\n```"

    return AsyncMock(side_effect=_fake)


# --- Teacher: valid question -> 200, exactly the three allowed fields -------------------


def test_teacher_valid_question_returns_200_with_only_allowed_fields(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _MODULE, "chat_completion", _mock_generated_sql("SELECT COUNT(*) AS n FROM student_360")
    )

    response = api.post(
        ASK_URL, json={"question": "how many students do I teach?"}, headers=_headers(api, TEACHER)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body.keys()) == {"natural_answer", "confidence", "provenance"}
    assert isinstance(body["natural_answer"], str) and body["natural_answer"]
    assert body["confidence"] in {"high", "medium", "low"}


def test_response_never_contains_internal_fields(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _MODULE, "chat_completion", _mock_generated_sql("SELECT COUNT(*) AS n FROM student_360")
    )

    response = api.post(
        ASK_URL, json={"question": "how many students do I teach?"}, headers=_headers(api, TEACHER)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    for forbidden in ("generated_sql", "validated_sql", "query_result", "audit_entry", "error"):
        assert forbidden not in body


# --- Non-teacher roles: 403, and the graph must never run -------------------------------


def test_student_role_is_forbidden_and_the_graph_never_runs(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    never_called = AsyncMock(
        side_effect=AssertionError("generate_sql must never run for a rejected role")
    )
    monkeypatch.setattr(_MODULE, "chat_completion", never_called)

    response = api.post(ASK_URL, json={"question": "anything"}, headers=_headers(api, STUDENT))

    assert response.status_code == 403
    never_called.assert_not_called()


def test_admin_role_is_forbidden_and_the_graph_never_runs(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    never_called = AsyncMock(
        side_effect=AssertionError("generate_sql must never run for a rejected role")
    )
    monkeypatch.setattr(_MODULE, "chat_completion", never_called)

    response = api.post(ASK_URL, json={"question": "anything"}, headers=_headers(api, ADMIN))

    assert response.status_code == 403
    never_called.assert_not_called()


# --- Identity comes from the JWT, never from the request body ---------------------------


def test_body_identity_fields_are_ignored_jwt_wins(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _MODULE, "chat_completion", _mock_generated_sql("SELECT COUNT(*) AS n FROM student_360")
    )

    ground_truth = api.get(
        "/api/v1/insights/students?limit=500", headers=_headers(api, TEACHER)
    ).json()
    expected_count = ground_truth["rows_returned"]

    # Body claims a different identity entirely -- an admin role and a made-up
    # institution/user id. None of this must have any effect: the endpoint only ever
    # reads `question` from the body.
    response = api.post(
        ASK_URL,
        json={
            "question": "how many students do I teach?",
            "user_id": "00000000-0000-0000-0000-000000000099",
            "user_role": "admin",
            "institution_id": "00000000-0000-0000-0000-000000000099",
        },
        headers=_headers(api, TEACHER),
    )

    assert response.status_code == 200, response.text
    assert str(expected_count) in response.json()["natural_answer"]


# --- Full round trip: real teacher, real scoped data -------------------------------------


def test_full_round_trip_reflects_the_teachers_real_scoped_data(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first test in the project exercising the complete system as a real HTTP call:
    real login, real role gate, real apply_role_scope rewrite, real execute_sql against
    the restricted `text_to_sql_reader` DB role, real sanity_check/compose_answer.

    Ground truth comes from the already-proven insights endpoint
    (test_insights_api.py's test_same_endpoint_returns_different_answers_per_role), which
    reports this exact teacher's row-scoped count over the same `student_360` grain —
    apply_role_scope's own cross-check tests (Task 6) already establish these two
    independent scoping implementations agree; this confirms it holds through the real
    HTTP stack too, not just node-to-node.
    """
    ground_truth = api.get(
        "/api/v1/insights/students?limit=500", headers=_headers(api, TEACHER)
    ).json()
    expected_count = ground_truth["rows_returned"]
    assert expected_count > 0, "fixture bug: teacher must have at least one scoped row"

    monkeypatch.setattr(
        _MODULE, "chat_completion", _mock_generated_sql("SELECT COUNT(*) AS n FROM student_360")
    )

    response = api.post(
        ASK_URL,
        json={"question": "how many students do I teach across all my subjects?"},
        headers=_headers(api, TEACHER),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert str(expected_count) in body["natural_answer"]
    assert body["confidence"] == "high"


# --- Missing/malformed JWT: inherited from the existing auth dependency, for free -------


def test_missing_jwt_returns_401(api: TestClient) -> None:
    response = api.post(ASK_URL, json={"question": "anything"})
    assert response.status_code == 401


def test_malformed_jwt_returns_401(api: TestClient) -> None:
    response = api.post(
        ASK_URL,
        json={"question": "anything"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
