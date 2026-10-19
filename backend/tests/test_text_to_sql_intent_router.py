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
    """Both intents used to be permanently unselectable (`requires_signoff: true`).
    They must still route. `students_needing_support` is answered by the at-risk
    service, so it carries no SQL; the performance-bar template still does.
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
    if intent == "students_needing_support":
        assert result["query_source"] == "service"
        assert result["generated_sql"] is None
    else:
        assert result["query_source"] == "template"
        assert result["generated_sql"]


@pytest.mark.asyncio
async def test_at_risk_count_question_routes_to_students_needing_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "At risk" routes to students_needing_support, which the at-risk service
    answers. The count shape is carried in parameters; there is no SQL to count with.
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
    assert result["query_source"] == "service"
    assert result["generated_sql"] is None
    assert result["intent_parameters"]["shape"] == "count"
    assert result["answer_criterion"] == (
        "using the at-risk definition for your classes, the same students as the at-risk "
        "flags. A flag is low mastery (below 60%), a mastery decline of more than 15 points, "
        "or low attendance (below 80%) when that flag is visible to you. Attendance-only "
        "flags are an administrator view and are not included for a teacher"
    )


async def test_free_form_route_has_no_answer_criterion(monkeypatch: pytest.MonkeyPatch) -> None:
    """The free_form path has no single fixed definition to name, and must not carry over
    a stale answer_criterion from a prior template-matched decision on the same state —
    _free_form always clears it explicitly rather than leaving it unset.
    """
    _mock_classifier(
        monkeypatch,
        {"intent": None, "confidence": 0.73, "parameters": {}, "operation": None},
    )

    stale_state = _state()
    stale_state["answer_criterion"] = "mastery below 60% or attendance below 80%"
    result = await _MODULE.intent_router(stale_state)

    assert result["intent_route"] == "free_form"
    assert result["answer_criterion"] is None


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


# --- top-N: the requested N must reach the executed SQL as a literal LIMIT ------------------


async def _routed_top_n(
    monkeypatch: pytest.MonkeyPatch, parameters: dict[str, Any]
) -> TextToSQLState:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "top_students_by_subject",
            "confidence": 0.97,
            "parameters": parameters,
            "operation": "top_n",
        },
    )
    return await _MODULE.intent_router(_state("Who are my top students in Science?"))


@pytest.mark.asyncio
async def test_top_n_limit_is_inlined_as_a_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await _routed_top_n(monkeypatch, {"subject": "Science", "limit": 3})

    assert result["intent_route"] == "template"
    sql = result["generated_sql"]
    assert sql is not None
    assert "LIMIT 3" in sql
    assert ":limit" not in sql


@pytest.mark.asyncio
async def test_top_n_without_a_stated_count_uses_the_declared_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _routed_top_n(monkeypatch, {"subject": "Science"})

    sql = result["generated_sql"]
    assert sql is not None
    assert "LIMIT 5" in sql
    assert ":limit" not in sql


@pytest.mark.asyncio
async def test_top_n_limit_survives_validate_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    """The original bug: validate_sql only honours a literal LIMIT and silently replaced a
    bound `LIMIT COALESCE(:limit, 5)` with `LIMIT 500`, so "top 5" returned every row."""
    from education_platform.modules.text_to_sql.nodes.validate_sql import validate_sql

    routed = await _routed_top_n(monkeypatch, {"subject": "Science", "limit": 3})
    validated = await validate_sql(routed)

    assert validated["error"] is None
    sql = validated["validated_sql"]
    assert sql is not None
    assert sql.rstrip().endswith("LIMIT 3")
    assert "LIMIT 500" not in sql


def test_inline_limit_refuses_sql_that_still_has_an_unbound_limit_placeholder() -> None:
    template = {"parameters": {"limit": {"type": "integer", "default": 5}}}
    # A LIMIT shape the substitution does not recognise must not slip through as ":limit".
    assert _MODULE._inline_limit("SELECT 1 LIMIT LEAST(:limit, 10)", template, {"limit": 3}) is None


def test_inline_limit_leaves_templates_without_a_limit_parameter_untouched() -> None:
    sql = "SELECT 1"
    assert _MODULE._inline_limit(sql, {"parameters": {}}, {}) == sql


# --- optional parameters: an explicit null means "not stated", same as an omitted key -------


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["Who took the last quiz?", "Who took the most recent quiz?"])
async def test_unqualified_latest_quiz_with_null_subject_routes_to_the_template(
    monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    # The classifier reports the absent optional `subject` as an explicit null, not by
    # omitting the key. That used to be validated against the subject enum and rejected,
    # sending every unqualified latest-quiz question to free-form.
    _mock_classifier(
        monkeypatch,
        {
            "intent": "latest_quiz_attempt",
            "confidence": 1.0,
            "parameters": {"subject": None},
            "operation": "latest",
        },
    )

    result = await _MODULE.intent_router(_state(question))

    assert result["intent_route"] == "template"
    assert result["intent"] == "latest_quiz_attempt"
    assert result["intent_parameters"] == {"subject": None}


@pytest.mark.asyncio
async def test_latest_quiz_with_a_named_subject_is_unaffected_by_null_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "latest_quiz_attempt",
            "confidence": 1.0,
            "parameters": {"subject": "Maths"},
            "operation": "latest",
        },
    )

    result = await _MODULE.intent_router(_state("Who took the last quiz in Maths?"))

    assert result["intent_route"] == "template"
    assert result["intent_parameters"] == {"subject": "Mathematics"}


@pytest.mark.asyncio
async def test_null_for_a_required_parameter_is_still_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "average_mastery_by_subject",
            "confidence": 0.97,
            "parameters": {"subject": None},
            "operation": "average",
        },
    )

    result = await _MODULE.intent_router(_state("What is my class average?"))

    assert result["intent_route"] == "free_form"


@pytest.mark.asyncio
async def test_null_top_n_limit_falls_back_to_the_default_instead_of_free_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _routed_top_n(monkeypatch, {"subject": "Science", "limit": None})

    assert result["intent_route"] == "template"
    sql = result["generated_sql"]
    assert sql is not None
    assert "LIMIT 5" in sql


# --- my_subjects vs my_sections: each must name what it lists --------------------------------


@pytest.mark.asyncio
async def test_subjects_question_misclassified_as_sections_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The evaluated failure (Q023): "assigned to" is shared wording, the classifier picked
    # my_sections at full confidence, and the teacher was answered with sections.
    _mock_classifier(
        monkeypatch,
        {"intent": "my_sections", "confidence": 1.0, "parameters": {}, "operation": "list"},
    )

    result = await _MODULE.intent_router(_state("Which subjects am I assigned to?"))

    assert result["intent_route"] == "free_form"
    assert result["query_source"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("What subject do I teach?", "my_subjects"),
        ("Which subjects am I assigned to?", "my_subjects"),
        ("What do I teach?", "my_subjects"),
        ("What sections am I assigned to?", "my_sections"),
        ("Which classes do I teach?", "my_sections"),
        ("Which sections do I teach?", "my_sections"),
    ],
)
async def test_self_reference_questions_still_route_to_the_right_template(
    monkeypatch: pytest.MonkeyPatch, question: str, intent: str
) -> None:
    _mock_classifier(
        monkeypatch,
        {"intent": intent, "confidence": 1.0, "parameters": {}, "operation": "list"},
    )

    result = await _MODULE.intent_router(_state(question))

    assert result["intent_route"] == "template"
    assert result["intent"] == intent


@pytest.mark.asyncio
async def test_sections_question_misclassified_as_subjects_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {"intent": "my_subjects", "confidence": 1.0, "parameters": {}, "operation": "list"},
    )

    result = await _MODULE.intent_router(_state("What sections am I assigned to?"))

    assert result["intent_route"] == "free_form"


# --- F5: a qualifier the template has no parameter for forces free-form ----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "intent", "parameters"),
    [
        # subject named, template has no subject parameter
        (
            "How many students are doing well in Mathematics?",
            "students_meeting_performance_bar",
            {},
        ),
        ("Which students need support in Science?", "students_needing_support", {"shape": "list"}),
        ("What is the average attendance in Maths?", "average_attendance_rate", {}),
        # number given, template takes no value parameter
        (
            "How many students have attendance below 40%?",
            "students_needing_support",
            {"shape": "count"},
        ),
        ("How many students do I have in grade 8?", "count_my_students", {}),
    ],
)
async def test_unsupported_qualifier_forces_free_form(
    monkeypatch: pytest.MonkeyPatch, question: str, intent: str, parameters: dict[str, Any]
) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": intent,
            "confidence": 0.97,
            "parameters": parameters,
            "operation": "count" if parameters.get("shape") != "list" else "list",
        },
    )

    result = await _MODULE.intent_router(_state(question))

    assert result["intent_route"] == "free_form"
    assert result["query_source"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "intent", "parameters", "operation"),
    [
        (
            "How many of my students are doing well?",
            "students_meeting_performance_bar",
            {},
            "count",
        ),
        ("Show me the struggling students.", "students_needing_support", {"shape": "list"}, "list"),
        (
            "List my students in section 8A.",
            "list_students_in_section",
            {"section_name": "8A"},
            "list",
        ),
        (
            "Show me the top 10 in Mathematics.",
            "top_students_by_subject",
            {"subject": "Mathematics", "limit": 10},
            "top_n",
        ),
        ("Who took the last quiz in Maths?", "latest_quiz_attempt", {"subject": "Maths"}, "latest"),
        (
            "Which of my students scored below 40% in Mathematics?",
            "list_students_below_score_in_subject",
            {"subject": "Mathematics", "threshold": 40},
            "list",
        ),
        (
            "How many students have attendance less than 40%?",
            "students_below_attendance_threshold",
            {"threshold": 40, "shape": "count"},
            "count",
        ),
    ],
)
async def test_questions_whose_qualifiers_the_template_supports_still_route(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    intent: str,
    parameters: dict[str, Any],
    operation: str,
) -> None:
    _mock_classifier(
        monkeypatch,
        {"intent": intent, "confidence": 0.97, "parameters": parameters, "operation": operation},
    )

    result = await _MODULE.intent_router(_state(question))

    assert result["intent_route"] == "template"
    assert result["intent"] == intent


# --- F10: zero-result handling declared per template -----------------------------------------


@pytest.mark.asyncio
async def test_threshold_template_carries_a_formatted_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "list_students_below_score_in_subject",
            "confidence": 0.97,
            "parameters": {"subject": "Mathematics", "threshold": 40},
            "operation": "list",
        },
    )

    result = await _MODULE.intent_router(
        _state("Which of my students scored below 40% in Mathematics?")
    )

    assert result["zero_result_is_valid"] is True
    assert result["empty_answer"] == "None of your students scored below 40% in Mathematics."


@pytest.mark.asyncio
async def test_support_template_empty_answer_includes_its_criterion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "students_needing_support",
            "confidence": 0.97,
            "parameters": {"shape": "list"},
            "operation": "list",
        },
    )

    result = await _MODULE.intent_router(_state("Show me the struggling students."))

    assert result["empty_answer"] == (
        "None of your students are on the at-risk list for your classes "
        "(using the at-risk definition for your classes, the same students as the at-risk "
        "flags. A flag is low mastery (below 60%), a mastery decline of more than 15 points, "
        "or low attendance (below 80%) when that flag is visible to you. Attendance-only "
        "flags are an administrator view and are not included for a teacher)."
    )


@pytest.mark.asyncio
async def test_template_without_zero_is_valid_gets_no_leniency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {"intent": "count_my_students", "confidence": 0.97, "parameters": {}, "operation": "count"},
    )

    result = await _MODULE.intent_router(_state("How many students do I teach?"))

    assert result["zero_result_is_valid"] is False
    assert result["empty_answer"] is None


@pytest.mark.asyncio
async def test_free_form_route_clears_zero_result_leniency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {"intent": None, "confidence": 0.0, "parameters": {}, "operation": None},
    )
    state = _state("What is the weather?")
    state["zero_result_is_valid"] = True
    state["empty_answer"] = "stale"

    result = await _MODULE.intent_router(state)

    assert result["intent_route"] == "free_form"
    assert result["zero_result_is_valid"] is False
    assert result["empty_answer"] is None


# --- below-score template: natural comparison wording, still guarded by its subject ---------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    ["Show me students under 50 in Science.", "Who scored under 50 in Science?"],
)
async def test_below_score_routes_when_the_question_uses_comparison_wording(
    monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "list_students_below_score_in_subject",
            "confidence": 0.95,
            "parameters": {"subject": "Science", "threshold": 50},
            "operation": "list",
        },
    )

    result = await _MODULE.intent_router(_state(question))

    assert result["intent_route"] == "template"
    assert result["intent"] == "list_students_below_score_in_subject"


@pytest.mark.asyncio
async def test_below_score_still_refuses_an_attendance_question_without_a_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "below" now satisfies the keyword gate, but this template still requires a subject.
    _mock_classifier(
        monkeypatch,
        {
            "intent": "list_students_below_score_in_subject",
            "confidence": 0.95,
            "parameters": {"threshold": 40},
            "operation": "list",
        },
    )

    result = await _MODULE.intent_router(_state("Which of my students have attendance below 40%?"))

    assert result["intent_route"] == "free_form"


# --- zero-result probes: reference queries attached for execute_sql -------------------------


@pytest.mark.asyncio
async def test_zero_valid_template_gets_the_visible_students_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "students_below_attendance_threshold",
            "confidence": 0.97,
            "parameters": {"threshold": 40, "shape": "count"},
            "operation": "count",
        },
    )

    result = await _MODULE.intent_router(_state("How many students have attendance less than 40%?"))

    probes = result["zero_probe_sql"]
    assert set(probes) == {"visible_students"}
    assert "COUNT(DISTINCT sse.student_id)" in probes["visible_students"]
    assert ":current_user_id" in probes["visible_students"]
    assert result["zero_probe_results"] is None


@pytest.mark.asyncio
async def test_subject_template_gets_the_taught_subjects_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "average_mastery_by_subject",
            "confidence": 1.0,
            "parameters": {"subject": "English"},
            "operation": "average",
        },
    )

    result = await _MODULE.intent_router(_state("What is my class average in English?"))

    assert set(result["zero_probe_sql"]) == {"taught_subjects"}
    assert "teaching_assignments" in result["zero_probe_sql"]["taught_subjects"]


@pytest.mark.asyncio
async def test_unqualified_latest_quiz_gets_no_subject_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {
            "intent": "latest_quiz_attempt",
            "confidence": 1.0,
            "parameters": {"subject": None},
            "operation": "latest",
        },
    )

    result = await _MODULE.intent_router(_state("Who took the last quiz?"))

    assert result["zero_probe_sql"] == {}


@pytest.mark.asyncio
async def test_templates_with_neither_trait_get_no_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_classifier(
        monkeypatch,
        {"intent": "count_my_students", "confidence": 0.97, "parameters": {}, "operation": "count"},
    )

    result = await _MODULE.intent_router(_state("How many students do I teach?"))

    assert result["zero_probe_sql"] == {}


@pytest.mark.asyncio
async def test_free_form_route_clears_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_classifier(
        monkeypatch,
        {"intent": None, "confidence": 0.0, "parameters": {}, "operation": None},
    )
    state = _state("What is the weather?")
    state["zero_probe_sql"] = {"visible_students": "SELECT 1"}
    state["zero_probe_results"] = {"visible_students": 5}

    result = await _MODULE.intent_router(state)

    assert result["zero_probe_sql"] == {}
    assert result["zero_probe_results"] is None
