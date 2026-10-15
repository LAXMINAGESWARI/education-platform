"""Focused tests for the YAML-backed Text-to-SQL intent router."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from education_platform.core.config import Settings
from education_platform.modules.text_to_sql.graph import build_text_to_sql_graph
from education_platform.modules.text_to_sql.state import TextToSQLState

_MODULE = importlib.import_module("education_platform.modules.text_to_sql.nodes.intent_router")


def _state(question: str = "How many students do I teach?") -> TextToSQLState:
    return {
        "question": question,
        "user_id": "user-1",
        "user_role": "teacher",
        "institution_id": "institution-1",
        "schema_context": "",
        "generated_sql": None,
        "validated_sql": None,
        "retry_count": 0,
        "error": None,
    }


def _mock_classifier(monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]) -> None:
    monkeypatch.setattr(_MODULE, "get_settings", lambda: Settings(openrouter_api_key="test"))
    monkeypatch.setattr(
        _MODULE,
        "chat_completion_json",
        AsyncMock(return_value=response),
    )


@pytest.mark.asyncio
async def test_high_confidence_template_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "count_my_students",
            "confidence": 0.96,
            "parameters": {},
            "operation": "count",
        },
    )

    result = await _MODULE.intent_router(_state())

    assert result["intent_route"] == "template"
    assert result["intent"] == "count_my_students"
    assert result["query_source"] == "template"
    assert result["generated_sql"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    ["students_meeting_performance_bar", "students_needing_support"],
)
async def test_approved_formerly_signoff_gated_templates_now_route_successfully(
    monkeypatch: pytest.MonkeyPatch,
    intent: str,
) -> None:
    """`students_meeting_performance_bar` ("doing well") and `students_needing_support`
    ("struggling") were both `requires_signoff: true` — permanently unselectable — until
    the client approved their thresholds (mastery >= 85 AND quizzes_passed >= 1 for the
    former; mastery < 60 OR attendance < 80, matching at_risk.engine.DEFAULT_THRESHOLDS,
    for the latter). This is the mirror of the removed case in
    test_invalid_or_ambiguous_decisions_fall_back_to_free_form — before approval, an
    otherwise-perfect decision for either intent still fell back to free_form solely
    because of the signoff flag; now it must route to template like any other approved
    one, proving `requires_signoff: false` actually re-enables selection rather than
    just silencing the flag.
    """
    operation = "count" if intent == "students_meeting_performance_bar" else "list"
    parameters = {} if intent == "students_meeting_performance_bar" else {"shape": "list"}
    _mock_classifier(
        monkeypatch,
        {"intent": intent, "confidence": 0.99, "parameters": parameters, "operation": operation},
    )

    result = await _MODULE.intent_router(_state())

    assert result["intent_route"] == "template"
    assert result["intent"] == intent
    assert result["query_source"] == "template"
    assert result["generated_sql"]


@pytest.mark.asyncio
async def test_at_risk_count_question_routes_to_students_needing_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "At risk" is treated as a synonym for students_needing_support's approved
    "struggling" definition (mastery < 60 OR attendance < 80), not a separate template —
    see that template's description. "How many students are at risk?" is a count
    question, so this also covers the template's sql_count/shape="count" path (the
    template was list-only before; this is the mirror of
    test_question_grounded_attendance_match_still_routes_to_template for this intent).
    """
    _mock_classifier(
        monkeypatch,
        {
            "intent": "students_needing_support",
            "confidence": 0.95,
            "parameters": {"shape": "count"},
            "operation": "count",
        },
    )

    result = await _MODULE.intent_router(_state("How many students are at risk?"))

    assert result["intent_route"] == "template"
    assert result["intent"] == "students_needing_support"
    assert result["query_source"] == "template"
    assert "COUNT" in (result["generated_sql"] or "")


@pytest.mark.asyncio
async def test_requires_signoff_flag_still_blocks_routing_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No real template in intent_templates.yaml is `requires_signoff: true` any more
    (both formerly-gated templates were approved), which would otherwise leave the gate
    itself — `_select_template`'s `template.get("requires_signoff") or
    router.get("requires_approved_policy")` check — completely untested. Monkeypatches a
    synthetic one-template catalog so this code path stays covered for whenever a future
    template needs the same treatment.
    """
    fake_catalog = {
        "intent_router": {"routing_policy": {"template_route_min_confidence": 0.90}},
        "templates": [
            {
                "name": "fake_gated_template",
                "parameters": {},
                "requires_signoff": True,
                "router": {"required_parameters": [], "supported_operations": ["count"]},
                "sql": "SELECT 1",
            }
        ],
    }
    monkeypatch.setattr(_MODULE, "_load_catalog", lambda: fake_catalog)
    _mock_classifier(
        monkeypatch,
        {
            "intent": "fake_gated_template",
            "confidence": 0.99,
            "parameters": {},
            "operation": "count",
        },
    )

    result = await _MODULE.intent_router(_state())

    assert result["intent_route"] == "free_form"
    assert result.get("query_source") is None


@pytest.mark.asyncio
async def test_wrong_template_match_refused_when_question_lacks_required_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces a live incident (golden-eval row 25): the classifier matched "Do any of
    my students have a mastery score of exactly 0?" to students_below_attendance_threshold
    at 0.9 confidence -- a fabricated wrong-template match, since the question never
    mentions attendance at all. students_below_attendance_threshold now declares
    router.requires_keywords: [attend] in intent_templates.yaml; _matches_required_keywords
    must refuse this match before parameter validation even runs, forcing free_form instead
    of the wrong template silently answering the wrong question.
    """
    _mock_classifier(
        monkeypatch,
        {
            "intent": "students_below_attendance_threshold",
            "confidence": 0.9,
            "parameters": {"threshold": 0, "shape": "list"},
            "operation": "list",
        },
    )

    result = await _MODULE.intent_router(
        _state("Do any of my students have a mastery score of exactly 0? Name them if so.")
    )

    assert result["intent_route"] == "free_form"
    assert result.get("query_source") is None
    assert result.get("generated_sql") is None


@pytest.mark.asyncio
async def test_question_grounded_attendance_match_still_routes_to_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mirror of the case above: a question that genuinely does name attendance must
    still route normally -- the new requires_keywords check must not over-refuse a
    legitimate match just because it exists.
    """
    _mock_classifier(
        monkeypatch,
        {
            "intent": "students_below_attendance_threshold",
            "confidence": 0.95,
            "parameters": {"threshold": 40, "shape": "count"},
            "operation": "count",
        },
    )

    result = await _MODULE.intent_router(_state("How many students have attendance less than 40%?"))

    assert result["intent_route"] == "template"
    assert result["intent"] == "students_below_attendance_threshold"
    assert result["query_source"] == "template"
    assert result["generated_sql"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subject", "canonical"),
    [
        ("Math", "Mathematics"),
        ("Maths", "Mathematics"),
        ("Mathematics", "Mathematics"),
        ("Sci", "Science"),
        ("Science", "Science"),
        ("Eng", "English"),
        # Physics deliberately not a valid value for this template's `subject` enum —
        # no Physics subject exists in this schema (see intent_templates.yaml's own
        # note on this and the sibling templates it matches for consistency); a "Phy"
        # alias must fall through to free_form here, not match this template.
    ],
)
async def test_latest_quiz_attempt_subject_alias_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
    subject: str,
    canonical: str,
) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "latest_quiz_attempt",
            "confidence": 0.95,
            "parameters": {"subject": subject},
            "operation": "last",
        },
    )

    result = await _MODULE.intent_router(_state())

    assert result["intent_route"] == "template"
    assert result["intent"] == "latest_quiz_attempt"
    assert result["intent_parameters"] == {"subject": canonical}
    assert ":subject" in (result["generated_sql"] or "")


@pytest.mark.asyncio
async def test_latest_quiz_attempt_physics_falls_back_to_free_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Physics is not a valid `subject` value for this template — no Physics subject
    exists in this schema (see intent_templates.yaml's own note, shared with the three
    sibling templates that already exclude it). A classifier match naming Physics must
    fail validation and fall through to free_form, never silently accept a subject that
    can never return data.
    """
    _mock_classifier(
        monkeypatch,
        {
            "intent": "latest_quiz_attempt",
            "confidence": 0.95,
            "parameters": {"subject": "Physics"},
            "operation": "last",
        },
    )

    result = await _MODULE.intent_router(_state())

    assert result["intent_route"] == "free_form"
    assert result["query_source"] is None


@pytest.mark.asyncio
async def test_latest_quiz_attempt_without_subject_uses_null_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "latest_quiz_attempt",
            "confidence": 0.95,
            "parameters": {},
            "operation": "latest",
        },
    )

    result = await _MODULE.intent_router(_state())

    assert result["intent_route"] == "template"
    assert result["intent"] == "latest_quiz_attempt"
    assert result["intent_parameters"] == {"subject": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"intent": "count_my_students", "confidence": 0.89, "parameters": {}, "operation": "count"},
        {
            "intent": "list_students_below_score_in_subject",
            "confidence": 0.95,
            "parameters": {"subject": "Mathematics"},
            "operation": "list",
        },
        {
            "intent": "list_students_in_section",
            "confidence": 0.95,
            "parameters": {"section_name": "8A"},
            "operation": "count",
        },
        {
            "intent": "count_my_students",
            "confidence": 0.95,
            "parameters": {},
            "operation": "list",
            "ambiguous": True,
        },
        {"intent": "unknown_intent", "confidence": 0.99, "parameters": {}, "operation": "count"},
    ],
)
async def test_invalid_or_ambiguous_decisions_fall_back_to_free_form(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any],
) -> None:
    _mock_classifier(monkeypatch, response)

    result = await _MODULE.intent_router(_state())

    assert result["intent_route"] == "free_form"
    assert result.get("query_source") is None
    assert result.get("generated_sql") is None


@pytest.mark.asyncio
async def test_free_form_question_preserves_existing_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_classifier(
        monkeypatch,
        {"intent": None, "confidence": 0.73, "parameters": {}, "operation": None},
    )

    result = await _MODULE.intent_router(_state())

    assert result["intent_route"] == "free_form"
    assert result["query_source"] is None


def test_graph_places_router_after_question_validator() -> None:
    graph = build_text_to_sql_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("question_validator", "intent_router") in edges
    assert ("intent_router", "validate_sql") in edges
    assert ("intent_router", "load_schema") in edges
    assert ("validate_sql", "generate_sql") in edges
    assert ("validate_sql", "apply_role_scope") in edges
