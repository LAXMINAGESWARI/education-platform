"""Unit tests for the text-to-SQL compose_answer node.

No database needed — chat_completion is monkeypatched throughout. The row-cap-truncation
test chained from a *real* execute_sql call lives in
test_text_to_sql_compose_answer_integration.py, which needs Postgres, matching the rigor
of Task 8's equivalent test.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from typing import Protocol, cast

import pytest

from education_platform.core.config import Settings
from education_platform.modules.assistant.openrouter import OpenRouterError
from education_platform.modules.text_to_sql.nodes.compose_answer import (
    _HEDGE_LEAD,
    _SCOPE_NARROWING_DISCLOSURE,
    _TRUNCATION_DISCLOSURE,
    compose_answer,
)
from education_platform.modules.text_to_sql.state import TextToSQLState


class _ComposeAnswerModule(Protocol):
    """Typed view of compose_answer.py's module namespace, for patching chat_completion
    where compose_answer.py actually looks it up.
    """

    async def chat_completion(
        self, messages: list[dict[str, str]], *, settings: Settings, temperature: float = 0.0
    ) -> str: ...

    def get_settings(self) -> Settings: ...


# `nodes/__init__.py` does `from .compose_answer import compose_answer as compose_answer`,
# which rebinds the `nodes.compose_answer` *attribute* from the submodule to the function
# (same gotcha as generate_sql/execute_sql — see test_text_to_sql_generate_sql.py). Reach
# the real submodule via sys.modules to monkeypatch chat_completion.
_MODULE = cast(
    _ComposeAnswerModule,
    sys.modules["education_platform.modules.text_to_sql.nodes.compose_answer"],
)


def _base_state(**overrides: object) -> TextToSQLState:
    state: TextToSQLState = {
        "question": "how many students are enrolled?",
        "validated_sql": "SELECT COUNT(*) AS count FROM student_subject_enrollments",
        "query_result": [{"count": 42}],
        "result_row_count": 1,
        "confidence": "high",
        "audit_entry": None,
        "error": None,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _fake_chat_completion(response: str) -> tuple[object, dict[str, object]]:
    captured: dict[str, object] = {}

    async def _fake(
        messages: list[dict[str, str]], *, settings: Settings, temperature: float = 0.0
    ) -> str:
        captured["messages"] = messages
        return response

    return _fake, captured


async def _never_called(*args: object, **kwargs: object) -> str:
    raise AssertionError("chat_completion should not have been called for this result shape")


def _answer(result: TextToSQLState) -> str:
    value = result["natural_answer"]
    assert value is not None
    return value


def _provenance_of(result: TextToSQLState) -> str:
    value = result["provenance"]
    assert value is not None
    return value


# --- LLM call vs. deterministic templating -----------------------------------------------


async def test_single_scalar_result_does_not_call_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(_base_state(query_result=[{"count": 42}], result_row_count=1))

    assert _answer(result) == "The count is 42."


async def test_single_scalar_result_appends_template_criterion_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A template-matched question (e.g. "how many of my students are at risk") sets
    state["answer_criterion"] to the template's own short, teacher-facing definition —
    without it, a bare count like "12" gives a teacher no way to tell it apart from a
    mismatch with a related-but-differently-scoped figure elsewhere in the app (e.g. the
    at-risk dashboard's own, narrower-scoped count). Appended deterministically, not via
    the LLM path (see test_single_scalar_result_does_not_call_the_llm) — same discipline
    as every other deterministic path in this node.
    """
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            query_result=[{"student_count": 12}],
            result_row_count=1,
            answer_criterion="mastery below 60% or attendance below 80%",
        )
    )

    assert _answer(result) == "The student count is 12 (mastery below 60% or attendance below 80%)."


async def test_single_scalar_result_omits_criterion_suffix_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The free_form path (and any template with no `criterion` declared) never sets
    answer_criterion — the sentence must read exactly as it did before this field
    existed, not with a stray "(None)" or empty parenthetical.
    """
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"count": 42}], result_row_count=1, answer_criterion=None)
    )

    assert _answer(result) == "The count is 42."


async def test_zero_row_result_does_not_call_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(_base_state(query_result=[], result_row_count=0))

    assert _answer(result) == "I couldn't find any records matching that question."


async def test_multi_row_result_calls_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured = _fake_chat_completion("Both students are passing their classes.")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    rows = [{"name": "A", "passed": True}, {"name": "B", "passed": True}]
    result = await compose_answer(
        _base_state(question="are my students passing?", query_result=rows, result_row_count=2)
    )

    assert _answer(result) == "Both students are passing their classes."
    messages = cast(list[dict[str, str]], captured["messages"])
    user_content = messages[-1]["content"]
    assert "are my students passing?" in user_content
    assert '"name": "A"' in user_content


async def test_single_row_multiple_columns_calls_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Row-count 1 but more than one column — not the single-scalar template shape.
    fake, _ = _fake_chat_completion("Aisha is in grade 8, section 8A.")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    result = await compose_answer(
        _base_state(
            question="what grade and section is Aisha in?",
            query_result=[{"grade": "8", "section": "8A"}],
            result_row_count=1,
        )
    )
    assert _answer(result) == "Aisha is in grade 8, section 8A."


# --- Fix (row 15/16 finding): small single-column lists are enumerated, not LLM-summarized


async def test_small_single_column_list_does_not_call_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The exact real-world shape that dropped a value: 3 rows, one column each, same
    # column name — "what subject do I teach" for a teacher with 2 Math assignments (one
    # per section) and 1 Science assignment.
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            question="what subject do I teach?",
            query_result=[{"name": "Mathematics"}, {"name": "Mathematics"}, {"name": "Science"}],
            result_row_count=3,
        )
    )

    assert _answer(result) == "Name: Mathematics and Science."


async def test_enumerated_list_every_distinct_value_present() -> None:
    result = await compose_answer(
        _base_state(
            question="what sections am I assigned to?",
            query_result=[{"name": "8A"}, {"name": "8B"}, {"name": "9A"}],
            result_row_count=3,
        )
    )
    answer = _answer(result)
    for value in ("8A", "8B", "9A"):
        assert value in answer


async def test_enumerated_list_deduplicates_repeated_values() -> None:
    # Two rows share a value (e.g. two teaching_assignment rows both naming "Mathematics")
    # — the answer must not repeat it just because the underlying rows did.
    result = await compose_answer(
        _base_state(
            query_result=[{"name": "Mathematics"}, {"name": "Mathematics"}, {"name": "Science"}],
            result_row_count=3,
        )
    )
    answer = _answer(result)
    assert answer.count("Mathematics") == 1


# --- Scope boundary: attribute lists only, never entity rosters -----------------------
#
# The enumeration path deduplicates, which is only safe for a small, closed
# attribute/category set (subject/section/grade names) where a repeated value is the
# same real-world fact stated twice. It must never fire for an entity roster (student
# names, or any list where each row is meant to represent a distinct person/thing) --
# there, a repeated-looking value could be a genuine duplicate-row bug, or two different
# real entities that happen to share a label, and silently merging them would hide
# something worth seeing. The schema's own naming convention draws this line: person-
# identifying tables use `full_name`, attribute/category tables use bare `name` -- see
# `_single_column_list_shape`'s docstring for the full reasoning.


async def test_full_name_roster_is_not_enumerated_even_when_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same shape (2 rows, one column each) as the enumerable case, but the column is
    # `full_name` (a person-identifying column, not an attribute label) -- must still go
    # through the LLM path, not be silently deduplicated.
    fake, _ = _fake_chat_completion("Your students are Aisha Rahman and Zainab Abdullah.")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)
    rows = [{"full_name": "Aisha Rahman"}, {"full_name": "Zainab Abdullah"}]
    result = await compose_answer(_base_state(query_result=rows, result_row_count=2))
    assert _answer(result) == "Your students are Aisha Rahman and Zainab Abdullah."


async def test_full_name_roster_with_a_repeated_value_reaches_the_llm_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If a duplicate-row bug (or two same-named students) ever produces a repeated
    # full_name, that must surface to the LLM path as real data to describe -- never get
    # silently collapsed to one entry the way a repeated subject/section name correctly
    # would be.
    captured: dict[str, object] = {}

    async def _fake(
        messages: list[dict[str, str]], *, settings: object, temperature: float = 0.0
    ) -> str:
        captured["messages"] = messages
        return "Two records for Aisha Rahman were found."

    monkeypatch.setattr(_MODULE, "chat_completion", _fake)
    rows = [{"full_name": "Aisha Rahman"}, {"full_name": "Aisha Rahman"}]
    result = await compose_answer(_base_state(query_result=rows, result_row_count=2))
    assert _answer(result) == "Two records for Aisha Rahman were found."
    messages = cast(list[dict[str, str]], captured["messages"])
    # Both raw rows were handed to the model -- the duplication itself was never erased
    # before the model even saw it.
    assert messages[-1]["content"].count('"Aisha Rahman"') == 2


async def test_non_name_single_column_list_is_not_enumerated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A bare list of measured values (no accompanying label) carries the same "is a
    # repeat the same fact or two coincidentally-equal ones?" ambiguity as a roster --
    # only the literal column `name` is in scope, nothing else.
    fake, _ = _fake_chat_completion("Scores of 82.5% and 91.0% were recorded.")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)
    rows = [{"mastery_percent": 82.5}, {"mastery_percent": 91.0}]
    result = await compose_answer(_base_state(query_result=rows, result_row_count=2))
    assert _answer(result) == "Scores of 82.5% and 91.0% were recorded."


async def test_single_column_list_at_cap_boundary_does_not_call_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)
    rows = [{"name": f"Subject {i}"} for i in range(10)]  # exactly the cap
    result = await compose_answer(_base_state(query_result=rows, result_row_count=10))
    answer = _answer(result)
    for i in range(10):
        assert f"Subject {i}" in answer


async def test_single_column_list_past_cap_calls_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, _ = _fake_chat_completion("There are 11 matching subjects.")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)
    rows = [{"name": f"Subject {i}"} for i in range(11)]  # one past the cap
    result = await compose_answer(_base_state(query_result=rows, result_row_count=11))
    assert _answer(result) == "There are 11 matching subjects."


async def test_multi_column_small_list_still_calls_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same row count as the enumerable case, but more than one column per row -- must NOT
    # be enumerated (no safe generic template exists for multi-column rows); still the
    # LLM path, unaffected by this fix.
    fake, _ = _fake_chat_completion("Two students: A and B.")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)
    rows = [{"name": "A", "grade": "8"}, {"name": "B", "grade": "9"}]
    result = await compose_answer(_base_state(query_result=rows, result_row_count=2))
    assert _answer(result) == "Two students: A and B."


async def test_llm_failure_falls_back_to_a_generic_honest_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raising(
        messages: list[dict[str, str]], *, settings: Settings, temperature: float = 0.0
    ) -> str:
        raise OpenRouterError("connection reset")

    monkeypatch.setattr(_MODULE, "chat_completion", _raising)

    # Multi-column, so this genuinely reaches the LLM path (a single-column list this
    # short is now enumerated deterministically instead — see the tests above).
    rows = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}, {"id": 3, "name": "C"}]
    result = await compose_answer(_base_state(query_result=rows, result_row_count=3))

    assert _answer(result) == "Found 3 matching records."


# --- Confidence must show up in the words --------------------------------------------


async def test_high_confidence_answer_has_no_hedging_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"count": 42}], result_row_count=1, confidence="high")
    )

    answer = _answer(result)
    assert "preliminary" not in answer.lower()
    assert _HEDGE_LEAD["medium"] not in answer
    assert _HEDGE_LEAD["low"] not in answer


async def test_medium_confidence_answer_differs_demonstrably_from_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    high = await compose_answer(
        _base_state(query_result=[{"count": 42}], result_row_count=1, confidence="high")
    )
    medium = await compose_answer(
        _base_state(query_result=[{"count": 42}], result_row_count=1, confidence="medium")
    )

    high_answer, medium_answer = _answer(high), _answer(medium)
    assert high_answer != medium_answer
    assert _HEDGE_LEAD["medium"] in medium_answer
    assert _HEDGE_LEAD["medium"] not in high_answer


async def test_low_confidence_answer_differs_demonstrably_from_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    high = await compose_answer(
        _base_state(query_result=[{"count": 42}], result_row_count=1, confidence="high")
    )
    low = await compose_answer(
        _base_state(query_result=[{"count": 42}], result_row_count=1, confidence="low")
    )

    high_answer, low_answer = _answer(high), _answer(low)
    assert high_answer != low_answer
    assert _HEDGE_LEAD["low"] in low_answer


async def test_medium_and_low_confidence_hedges_differ_from_each_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    medium = await compose_answer(
        _base_state(query_result=[{"count": 42}], result_row_count=1, confidence="medium")
    )
    low = await compose_answer(
        _base_state(query_result=[{"count": 42}], result_row_count=1, confidence="low")
    )

    medium_answer, low_answer = _answer(medium), _answer(low)
    assert medium_answer != low_answer
    assert _HEDGE_LEAD["medium"] in medium_answer
    assert _HEDGE_LEAD["low"] in low_answer


async def test_confidence_hedge_reflects_which_trigger_fired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            query_result=[{"count": 42}],
            result_row_count=1,
            confidence="medium",
            audit_entry={
                "sanity_check_triggers": [
                    "duplicate_entity_in_roster: full_name value(s) appear in more than "
                    "one row: Sami Yusuf"
                ]
            },
        )
    )

    answer = _answer(result)
    assert "the same person appeared more than once in the results" in answer
    # The raw trigger detail text (column names, specific values) must never leak into
    # the user-facing answer — only the hand-written, name-keyed phrase does.
    assert "Sami Yusuf" not in answer
    assert "full_name" not in answer


# --- Scope-narrowing disclosure --------------------------------------------------------


async def test_scope_narrowing_disclosure_appended_when_cross_institution_trigger_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            query_result=[{"count": 42}],
            result_row_count=1,
            confidence="medium",
            audit_entry={
                "sanity_check_triggers": [
                    "cross_institution_scope_assumed: heuristic (question-phrasing "
                    "guess, not a certainty): question implies scope beyond this "
                    "institution, but every query this pipeline runs is always scoped "
                    "to the asking user's own institution by design"
                ]
            },
        )
    )

    assert _SCOPE_NARROWING_DISCLOSURE in _answer(result)


async def test_scope_narrowing_disclosure_absent_without_the_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"count": 42}], result_row_count=1, confidence="high")
    )

    assert _SCOPE_NARROWING_DISCLOSURE not in _answer(result)


async def test_scope_narrowing_disclosure_never_contains_a_number_or_institution_name() -> None:
    # Regression guard on the constant itself, directly enforcing the "never reveal
    # whether other institutions exist, their count, or anything about them" requirement.
    assert not any(char.isdigit() for char in _SCOPE_NARROWING_DISCLOSURE)
    for leaky_word in ("poc", "demo school", "al noor"):
        assert leaky_word not in _SCOPE_NARROWING_DISCLOSURE.lower()


async def test_zero_row_answer_with_scope_trigger_still_reads_as_a_single_coherent_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            query_result=[],
            result_row_count=0,
            confidence="medium",
            audit_entry={
                "sanity_check_triggers": [
                    "zero_rows: query returned no rows",
                    "cross_institution_scope_assumed: heuristic (question-phrasing "
                    "guess, not a certainty): question implies scope beyond this "
                    "institution, but every query this pipeline runs is always scoped "
                    "to the asking user's own institution by design",
                ]
            },
        )
    )

    answer = _answer(result)
    assert "I couldn't find any records matching that question." in answer
    assert _SCOPE_NARROWING_DISCLOSURE in answer


# --- Row-cap truncation: always a real sentence, isolated flag version -----------------


async def test_row_cap_truncated_flag_produces_the_disclosure_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, _ = _fake_chat_completion("Here are the matching records.")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)

    rows = [{"id": i} for i in range(10)]
    result = await compose_answer(
        _base_state(
            query_result=rows,
            result_row_count=10,
            audit_entry={"row_cap_truncated": True},
        )
    )

    assert _TRUNCATION_DISCLOSURE in _answer(result)


async def test_row_cap_flag_absent_produces_no_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"count": 1}], result_row_count=1, audit_entry=None)
    )

    assert _TRUNCATION_DISCLOSURE not in _answer(result)


# --- Zero rows read as an honest, complete answer --------------------------------------


async def test_zero_row_result_reads_as_honest_not_a_broken_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            question="which students failed the quiz?",
            query_result=[],
            result_row_count=0,
            confidence="high",
        )
    )

    answer = _answer(result)
    assert answer  # not empty/falsy
    assert answer != "0"
    assert "None" not in answer
    assert answer == "I couldn't find any records matching that question."


# --- Single-scalar formatting details ---------------------------------------------------


async def test_single_scalar_percentage_column_formats_with_percent_sign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"mastery_percent": 82.5}], result_row_count=1)
    )
    assert _answer(result) == "The mastery percent is 82.5%."


async def test_single_scalar_null_value_phrased_as_no_data_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"attendance_percent": None}], result_row_count=1)
    )
    assert _answer(result) == "No attendance percent data is available for that."


# --- provenance: describes what was queried, never raw SQL -----------------------------


async def test_provenance_lists_queried_tables() -> None:
    result = await compose_answer(
        _base_state(
            validated_sql="SELECT sp.id FROM student_profiles sp JOIN quiz_attempts qa "
            "ON qa.student_id = sp.id",
            query_result=[{"count": 1}],
            result_row_count=1,
        )
    )
    provenance = _provenance_of(result)
    assert "student_profiles" in provenance
    assert "quiz_attempts" in provenance


async def test_provenance_never_contains_raw_sql() -> None:
    result = await compose_answer(
        _base_state(
            validated_sql=(
                "SELECT id FROM student_profiles WHERE full_name = 'Unmistakable Secret Name'"
            ),
            query_result=[{"count": 1}],
            result_row_count=1,
        )
    )
    provenance = _provenance_of(result)
    assert "Unmistakable Secret Name" not in provenance
    assert "SELECT" not in provenance
    assert "WHERE" not in provenance
    assert "student_profiles" in provenance  # the table name is fine, the query text isn't


async def test_provenance_mechanism_cannot_leak_sql_keywords_embedded_in_literal_values() -> None:
    # Table-name extraction walks the sqlglot AST for exp.Table nodes only -- it never
    # does string search/redaction over the SQL text -- so a literal *value* that happens
    # to contain a SQL-keyword-shaped substring can't leak through structurally, not just
    # "didn't happen to trigger in this particular test's data" the way the test above
    # only proves for one specific literal. A naive text-based redaction approach could
    # plausibly miss "Selecta" (contains "Select") or fail on "DROP-001-WHERE"; parsing
    # to an AST and reading only .name off exp.Table nodes never even looks at literals.
    result = await compose_answer(
        _base_state(
            validated_sql=(
                "SELECT id FROM student_profiles WHERE full_name = 'Selecta Smith' "
                "AND student_identifier = 'DROP-001-WHERE'"
            ),
            query_result=[{"count": 1}],
            result_row_count=1,
        )
    )
    provenance = _provenance_of(result)
    assert "Selecta Smith" not in provenance
    assert "DROP-001-WHERE" not in provenance
    assert "student_profiles" in provenance


async def test_provenance_includes_sanity_check_triggers_when_present() -> None:
    result = await compose_answer(
        _base_state(
            query_result=[],
            result_row_count=0,
            confidence="medium",
            audit_entry={"sanity_check_triggers": ["zero_rows: query returned no rows"]},
        )
    )
    assert "zero_rows" in _provenance_of(result)


async def test_provenance_has_no_triggers_line_when_clean() -> None:
    result = await compose_answer(
        _base_state(query_result=[{"count": 1}], result_row_count=1, audit_entry=None)
    )
    assert "flagged" not in _provenance_of(result).lower()


# --- state["error"] already set: never fabricate an answer over it ---------------------


async def test_preexisting_error_is_never_overwritten_with_a_fabricated_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    state = _base_state(
        error="EXECUTION_ERROR: the query could not be run",
        query_result=None,
        result_row_count=None,
    )
    result = await compose_answer(state)

    assert result == state
    assert result.get("natural_answer") is None
    assert result["error"] == "EXECUTION_ERROR: the query could not be run"


# --- Fix (F4/F9): multi-attribute rosters and category lists are never LLM-summarized -------

_STRUGGLING_ROWS = [
    {"full_name": "Zainab Abdullah", "mastery_percent": 52.35, "attendance_percent": 94.0},
    {"full_name": "Aisha Rahman", "mastery_percent": 56.0, "attendance_percent": 62.0},
    # Flagged on attendance alone: the LLM summaries used to leave exactly these out.
    {"full_name": "Sami Yusuf", "mastery_percent": 71.0, "attendance_percent": 75.5},
]


async def test_multi_attribute_roster_lists_every_student_without_calling_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            question="Who is falling behind?",
            query_result=_STRUGGLING_ROWS,
            result_row_count=3,
            answer_criterion="mastery below 60% or attendance below 80%",
        )
    )

    assert _answer(result) == (
        "Found 3 matching records (mastery below 60% or attendance below 80%): "
        "Zainab Abdullah (mastery 52.35%, attendance 94.0%); "
        "Aisha Rahman (mastery 56.0%, attendance 62.0%); "
        "Sami Yusuf (mastery 71.0%, attendance 75.5%)."
    )


async def test_multi_attribute_roster_omits_criterion_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=_STRUGGLING_ROWS, result_row_count=3, answer_criterion=None)
    )

    assert _answer(result).startswith("Found 3 matching records: Zainab Abdullah (")


async def test_multi_attribute_roster_keeps_same_named_students_as_separate_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)
    rows = [
        {"full_name": "Rania Yusuf", "mastery_percent": 50.0, "attendance_percent": 90.0},
        {"full_name": "Rania Yusuf", "mastery_percent": 58.0, "attendance_percent": 70.0},
    ]

    result = await compose_answer(_base_state(query_result=rows, result_row_count=2))

    assert _answer(result).count("Rania Yusuf") == 2


async def test_single_row_with_several_columns_still_uses_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, _ = _fake_chat_completion("Rania Yusuf took the last Maths quiz, scoring 69.28%.")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)
    rows = [{"full_name": "Rania Yusuf", "score_percent": 69.28, "submitted_at": "2026-10-07"}]

    result = await compose_answer(_base_state(query_result=rows, result_row_count=1))

    assert _answer(result) == "Rania Yusuf took the last Maths quiz, scoring 69.28%."


async def test_subject_category_list_is_enumerated_not_summarized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "What subject do I teach?" was answered "You teach either Mathematics or Science"
    # by the LLM for a teacher who teaches both.
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            question="What subject do I teach?",
            query_result=[{"subject": "Mathematics"}, {"subject": "Science"}],
            result_row_count=2,
        )
    )

    assert _answer(result) == "Subjects: Mathematics and Science."


async def test_section_category_list_is_enumerated_not_summarized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            query_result=[{"section": "8A"}, {"section": "8B"}, {"section": "9A"}],
            result_row_count=3,
        )
    )

    assert _answer(result) == "Sections: 8A, 8B, and 9A."


async def test_llm_roster_answer_that_omits_a_student_is_replaced_with_the_full_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Single-column rosters still go through the LLM; if it leaves someone out, the
    # complete listing wins.
    fake, _ = _fake_chat_completion("Your students include Aisha Rahman.")
    monkeypatch.setattr(_MODULE, "chat_completion", fake)
    rows = [{"full_name": "Aisha Rahman"}, {"full_name": "Zainab Abdullah"}]

    result = await compose_answer(_base_state(query_result=rows, result_row_count=2))

    assert "Aisha Rahman" in _answer(result)
    assert "Zainab Abdullah" in _answer(result)


async def test_llm_failure_on_a_roster_still_lists_every_student(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raising(
        messages: list[dict[str, str]], *, settings: Settings, temperature: float = 0.0
    ) -> str:
        raise OpenRouterError("connection reset")

    monkeypatch.setattr(_MODULE, "chat_completion", _raising)
    rows = [{"full_name": "Aisha Rahman"}, {"full_name": "Zainab Abdullah"}]

    result = await compose_answer(_base_state(query_result=rows, result_row_count=2))

    assert "Aisha Rahman" in _answer(result)
    assert "Zainab Abdullah" in _answer(result)


# --- Fix (F10): a template's own plain sentence for a genuine zero ---------------------------


async def test_zero_rows_uses_the_templates_empty_answer_with_no_hedge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            query_result=[],
            result_row_count=0,
            confidence="high",
            empty_answer="None of your students have attendance below 40%.",
        )
    )

    assert _answer(result) == "None of your students have attendance below 40%."


async def test_zero_rows_without_an_empty_answer_keeps_the_generic_sentence() -> None:
    result = await compose_answer(_base_state(query_result=[], result_row_count=0))

    assert _answer(result) == "I couldn't find any records matching that question."


async def test_empty_answer_is_still_hedged_if_confidence_was_downgraded() -> None:
    # The plain sentence replaces only the generic wording; it never suppresses a hedge
    # that sanity_check actually decided on.
    result = await compose_answer(
        _base_state(
            query_result=[],
            result_row_count=0,
            confidence="medium",
            empty_answer="None of your students have attendance below 40%.",
            audit_entry={"sanity_check_triggers": ["row_cap_truncated: x"]},
        )
    )

    assert _answer(result).startswith("None of your students have attendance below 40%.")
    assert "preliminary estimate" in _answer(result)


# --- Fix (F8): a subject the teacher doesn't teach is explained, not reported as a data fault


async def test_null_average_for_an_untaught_subject_says_so_plainly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            question="what is my class average in English?",
            query_result=[{"average_score": None}],
            result_row_count=1,
            intent_parameters={"subject": "English"},
            zero_probe_results={"taught_subjects": ["Mathematics", "Science"]},
        )
    )

    assert _answer(result) == (
        "You don't teach English, so there is nothing to show for it. "
        "The subjects you teach are Mathematics and Science."
    )


async def test_empty_list_for_an_untaught_subject_says_so_plainly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            query_result=[],
            result_row_count=0,
            intent_parameters={"subject": "Arabic"},
            zero_probe_results={"taught_subjects": ["Science"]},
            empty_answer="should not be used",
        )
    )

    assert _answer(result) == (
        "You don't teach Arabic, so there is nothing to show for it. "
        "The subjects you teach are Science."
    )


async def test_untaught_subject_message_is_not_used_for_a_subject_they_do_teach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            query_result=[],
            result_row_count=0,
            intent_parameters={"subject": "Science"},
            zero_probe_results={"taught_subjects": ["Mathematics", "Science"]},
        )
    )

    assert _answer(result) == "I couldn't find any records matching that question."


async def test_untaught_subject_message_needs_the_probe_to_have_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No probe result (never ran, or failed) means we cannot claim the teacher doesn't teach it.
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            query_result=[{"average_score": None}],
            result_row_count=1,
            intent_parameters={"subject": "English"},
            zero_probe_results=None,
        )
    )

    assert "You don't teach" not in _answer(result)


async def test_untaught_subject_message_is_not_used_when_there_is_real_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(
            query_result=[{"average_score": 70.08}],
            result_row_count=1,
            intent_parameters={"subject": "English"},
            zero_probe_results={"taught_subjects": ["Mathematics"]},
        )
    )

    assert _answer(result) == "The average score is 70.08%."


# --- percentages: an `average_*_percent` column reads as a percentage ----------------------


async def test_average_attendance_percent_reads_as_a_percentage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"average_attendance_percent": 90.83}], result_row_count=1)
    )

    assert _answer(result) == "The average attendance is 90.83%."


async def test_average_score_percent_reads_as_a_percentage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"average_score_percent": 70.08}], result_row_count=1)
    )

    assert _answer(result) == "The average score is 70.08%."


async def test_average_percent_with_no_data_reads_naturally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"average_score_percent": None}], result_row_count=1)
    )

    assert _answer(result).startswith("No average score data is available for that.")


async def test_any_percent_suffixed_column_gets_a_percent_sign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"pass_rate_percent": 64.5}], result_row_count=1)
    )

    assert _answer(result) == "The pass rate percent is 64.5%."


async def test_plain_schema_percent_column_keeps_its_established_wording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"mastery_percent": 82.5}], result_row_count=1)
    )

    assert _answer(result) == "The mastery percent is 82.5%."


async def test_a_count_is_never_given_a_percent_sign(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"student_count": 72}], result_row_count=1)
    )

    assert _answer(result) == "The student count is 72."


# --- free-form aggregate aliases of a percentage measure get a % and sane rounding ----------


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [
        # the unrounded AVG a free-form query returned for "average quiz score for my students"
        ("average_score", Decimal("71.2001388888888889"), "The average score is 71.20%."),
        ("highest_quiz_score", Decimal("100.00"), "The highest quiz score is 100.00%."),
        ("average_attendance", 90.8333333333, "The average attendance is 90.83%."),
        ("lowest_mastery", Decimal("36.77"), "The lowest mastery is 36.77%."),
        ("max_quiz_score", Decimal("99.5"), "The max quiz score is 99.5%."),
    ],
)
async def test_free_form_aggregate_of_a_percentage_measure_reads_as_a_rounded_percentage(
    monkeypatch: pytest.MonkeyPatch, column: str, value: object, expected: str
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(_base_state(query_result=[{column: value}], result_row_count=1))

    assert _answer(result) == expected


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [
        ("average_score_raw", 12.3456, "The average score raw is 12.3456."),  # points, not %
        ("average_raw_score", 3.25, "The average raw score is 3.25."),
        ("average_students", 24.5, "The average students is 24.5."),  # not a % measure
        ("highest_quiz_count", 7, "The highest quiz count is 7."),
        ("student_count", 72, "The student count is 72."),
    ],
)
async def test_other_aggregates_are_not_given_a_percent_sign_or_rounded(
    monkeypatch: pytest.MonkeyPatch, column: str, value: object, expected: str
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(_base_state(query_result=[{column: value}], result_row_count=1))

    assert _answer(result) == expected


async def test_percent_value_with_at_most_two_decimals_is_left_exactly_as_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "chat_completion", _never_called)

    result = await compose_answer(
        _base_state(query_result=[{"average_score": Decimal("70.08")}], result_row_count=1)
    )

    assert _answer(result) == "The average score is 70.08%."
