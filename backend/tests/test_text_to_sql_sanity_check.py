"""Unit tests for the text-to-SQL sanity_check node.

No database needed — every test here hand-builds state["query_result"]/
state["audit_entry"] directly. The two exceptions (row-cap truncation actually chained
from a real execute_sql call, and confidence surviving the full compiled graph) live in
test_text_to_sql_sanity_check_integration.py, which needs Postgres.
"""

from __future__ import annotations

from typing import Any

from education_platform.modules.text_to_sql.nodes.sanity_check import sanity_check
from education_platform.modules.text_to_sql.state import TextToSQLState


def _state(
    *,
    question: str = "how many students are enrolled?",
    query_result: list[dict[str, Any]] | None = None,
    result_row_count: int | None = None,
    audit_entry: dict[str, Any] | None = None,
) -> TextToSQLState:
    rows = query_result if query_result is not None else []
    state: TextToSQLState = {
        "question": question,
        "query_result": rows,
        "result_row_count": result_row_count if result_row_count is not None else len(rows),
        "audit_entry": audit_entry,
        "error": None,
    }
    return state


def _triggers(result: TextToSQLState) -> list[str]:
    audit_entry = result["audit_entry"]
    assert audit_entry is not None
    return audit_entry["sanity_check_triggers"]  # type: ignore[no-any-return]


# --- Clean baseline --------------------------------------------------------------------


async def test_clean_result_gets_high_confidence_and_no_triggers() -> None:
    result = await sanity_check(
        _state(
            question="what is my class average mastery percent?",
            query_result=[{"mastery_percent": 82.5}],
        )
    )
    assert result["confidence"] == "high"
    assert _triggers(result) == []


# --- Each of the 5 checks, isolated -----------------------------------------------------


async def test_zero_rows_downgrades_to_medium_and_is_recorded() -> None:
    result = await sanity_check(_state(question="how many students failed?", query_result=[]))

    assert result["confidence"] == "medium"
    triggers = _triggers(result)
    assert len(triggers) == 1
    assert triggers[0].startswith("zero_rows:")


async def test_zero_valued_count_downgrades_to_medium_and_is_recorded() -> None:
    # SELECT COUNT(*) always returns exactly one row, even when the count is 0 — this is
    # the aggregate-shaped analog of test_zero_rows_downgrades_to_medium_and_is_recorded,
    # not a duplicate of it: result_row_count is 1 here, not 0, so zero_rows itself never
    # fires for this case at all.
    result = await sanity_check(
        _state(question="how many students failed?", query_result=[{"count": 0}])
    )
    assert result["confidence"] == "medium"
    triggers = _triggers(result)
    assert len(triggers) == 1
    assert triggers[0].startswith("zero_valued_aggregate:")


async def test_zero_valued_count_and_equivalent_zero_row_list_get_matching_confidence() -> None:
    # The same real-world fact ("no students failed"), phrased two ways, must no longer
    # produce different confidence just because one phrasing happens to be COUNT-shaped.
    count_result = await sanity_check(
        _state(question="how many students failed?", query_result=[{"count": 0}])
    )
    list_result = await sanity_check(_state(question="which students failed?", query_result=[]))
    assert count_result["confidence"] == list_result["confidence"] == "medium"


async def test_zero_valued_sum_also_downgrades_to_medium() -> None:
    # Not just COUNT — any single-value aggregate (SUM, etc.) that lands on exactly 0.
    result = await sanity_check(
        _state(question="what is the total marks awarded?", query_result=[{"total_marks": 0.0}])
    )
    assert result["confidence"] == "medium"
    assert _triggers(result)[0].startswith("zero_valued_aggregate:")


async def test_null_valued_aggregate_is_flagged_by_zero_valued_aggregate() -> None:
    # NULL fires the same zero_valued_aggregate trigger a literal 0 does — an AVG()-shaped
    # query that legitimately matches zero rows returns one row with a NULL value, not
    # zero rows, so zero_rows can never catch this shape. Previously nothing downgraded
    # confidence for it at all (a real, confirmed-live bug — see the module docstring's
    # "Zero-valued aggregate" entry). compose_answer's separate null-value text path still
    # renders "No <label> data is available for that."; this only affects confidence.
    result = await sanity_check(
        _state(question="what is the average score?", query_result=[{"average_score": None}])
    )
    assert result["confidence"] == "medium"
    triggers = _triggers(result)
    assert len(triggers) == 1
    assert triggers[0].startswith("zero_valued_aggregate:")


async def test_nonzero_count_is_not_flagged_by_zero_valued_aggregate() -> None:
    result = await sanity_check(
        _state(question="how many students are enrolled?", query_result=[{"count": 42}])
    )
    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_zero_valued_aggregate_ignores_boolean_false() -> None:
    # bool is a subclass of int in Python (float(False) == 0.0) — a boolean-valued single
    # result (this pipeline doesn't currently produce one, but the check shouldn't assume
    # that) must not be misread as a numeric zero.
    result = await sanity_check(
        _state(question="did any student pass?", query_result=[{"passed": False}])
    )
    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_row_cap_truncated_flag_downgrades_to_low() -> None:
    result = await sanity_check(
        _state(
            question="list all quiz attempts",
            query_result=[{"id": i} for i in range(10)],
            audit_entry={"row_cap_truncated": True},
        )
    )

    assert result["confidence"] == "low"
    triggers = _triggers(result)
    assert len(triggers) == 1
    assert triggers[0].startswith("row_cap_truncated:")


async def test_row_cap_truncated_flag_absent_does_not_trigger() -> None:
    result = await sanity_check(
        _state(query_result=[{"id": 1}], audit_entry={"role_scope_applied": "rewritten"})
    )
    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_aggregate_out_of_bounds_downgrades_to_low() -> None:
    result = await sanity_check(
        _state(
            question="what is this student's mastery percent?",
            query_result=[{"mastery_percent": 142.0}],
        )
    )

    assert result["confidence"] == "low"
    triggers = _triggers(result)
    assert len(triggers) == 1
    assert triggers[0].startswith("aggregate_out_of_bounds:")
    assert "mastery_percent" in triggers[0]


async def test_aggregate_out_of_bounds_ignores_null_percentage_values() -> None:
    # attendance_percent is legitimately NULL when there's no attendance data yet —
    # schema_catalog.md is explicit this must never be treated as an out-of-range value by
    # THIS check specifically. It's still a single-scalar NULL result though, so
    # zero_valued_aggregate now correctly downgrades confidence for the "no data" vs.
    # "silently excluded" ambiguity — this test only asserts aggregate_out_of_bounds
    # itself stays silent, not that nothing fires at all.
    result = await sanity_check(
        _state(
            question="what is this student's attendance percent?",
            query_result=[{"attendance_percent": None}],
        )
    )
    assert result["confidence"] == "medium"
    triggers = _triggers(result)
    assert len(triggers) == 1
    assert triggers[0].startswith("zero_valued_aggregate:")


async def test_aggregate_out_of_bounds_ignores_unrelated_columns() -> None:
    # A column named e.g. "count" or "score" (not one of the known percentage columns)
    # can legitimately be > 100 or negative — must not be flagged.
    result = await sanity_check(_state(query_result=[{"count": 142}, {"count": -5}]))
    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_single_row_for_list_question_downgrades_to_medium() -> None:
    result = await sanity_check(
        _state(question="list all students in grade 8", query_result=[{"id": 1}])
    )

    assert result["confidence"] == "medium"
    triggers = _triggers(result)
    assert len(triggers) == 1
    assert triggers[0].startswith("single_row_for_list_question:")


async def test_single_row_is_not_flagged_for_an_aggregate_question() -> None:
    # The task's own example: correctly one row, must not be flagged even though "list"
    # heuristics elsewhere exist — "overall"/"rate" mark this as aggregate-shaped.
    result = await sanity_check(
        _state(
            question="what's the school's overall attendance rate?",
            query_result=[{"attendance_percent": 91.0}],
        )
    )
    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_single_row_is_not_flagged_when_question_has_no_list_signal() -> None:
    result = await sanity_check(
        _state(question="what is Aisha's current grade?", query_result=[{"grade": "8"}])
    )
    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_large_result_near_cap_for_bounded_question_downgrades_to_medium() -> None:
    rows = [{"id": i} for i in range(460)]
    result = await sanity_check(_state(question="how are my students doing", query_result=rows))

    assert result["confidence"] == "medium"
    triggers = _triggers(result)
    assert len(triggers) == 1
    assert triggers[0].startswith("large_result_near_cap:")


async def test_large_result_not_flagged_without_bounded_scope_phrase() -> None:
    # Same row count, but nothing in the question implies a bounded/personal roster —
    # a genuinely school-wide question returning many rows is not inherently suspicious.
    rows = [{"id": i} for i in range(460)]
    result = await sanity_check(
        _state(question="list every student in the school", query_result=rows)
    )

    # Note: "list every student" also matches the list-signal phrase, but row_count != 1,
    # so that check doesn't fire either — this result should be clean.
    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_large_result_not_flagged_for_a_genuinely_unbounded_school_wide_question() -> None:
    # A correctly-scoped admin query legitimately returning near-cap rows for a
    # school-wide (not personally-scoped) question must not be flagged — this is the
    # specific false-positive case the "my students" gating exists to avoid tripping on.
    rows = [{"id": i} for i in range(460)]
    result = await sanity_check(
        _state(question="how many students are in the whole school", query_result=rows)
    )
    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_large_result_trigger_reason_is_explicitly_flagged_as_a_heuristic() -> None:
    rows = [{"id": i} for i in range(460)]
    result = await sanity_check(_state(question="how are my students doing", query_result=rows))
    triggers = _triggers(result)
    assert len(triggers) == 1
    assert "heuristic" in triggers[0]
    assert "not a certainty" in triggers[0]


async def test_single_row_list_trigger_reason_is_explicitly_flagged_as_a_heuristic() -> None:
    result = await sanity_check(
        _state(question="list all students in grade 8", query_result=[{"id": 1}])
    )
    triggers = _triggers(result)
    assert len(triggers) == 1
    assert "heuristic" in triggers[0]
    assert "not a certainty" in triggers[0]


async def test_large_result_not_flagged_below_threshold() -> None:
    rows = [{"id": i} for i in range(10)]
    result = await sanity_check(_state(question="how are my students doing", query_result=rows))
    assert result["confidence"] == "high"
    assert _triggers(result) == []


# --- Multiple simultaneous triggers: all recorded, worst severity wins -----------------


async def test_multiple_triggers_are_all_recorded_and_worst_severity_wins() -> None:
    rows = [{"mastery_percent": 50.0} for _ in range(459)] + [{"mastery_percent": 142.0}]
    result = await sanity_check(
        _state(question="how are my students performing", query_result=rows)
    )

    triggers = _triggers(result)
    names = {t.split(":", 1)[0] for t in triggers}
    assert names == {"aggregate_out_of_bounds", "large_result_near_cap"}
    # aggregate_out_of_bounds is "low", large_result_near_cap is "medium" — low wins.
    assert result["confidence"] == "low"


async def test_result_row_count_falls_back_to_len_query_result_when_unset() -> None:
    state = _state(query_result=[{"id": 1}])
    del state["result_row_count"]
    result = await sanity_check(state)
    assert result["confidence"] == "high"


# --- Cross-institution scope assumed ----------------------------------------------------


async def test_cross_institution_scope_phrase_downgrades_to_medium_and_is_recorded() -> None:
    result = await sanity_check(
        _state(
            question="how does our school compare to other schools",
            query_result=[{"avg_score": 71.2}],
        )
    )

    assert result["confidence"] == "medium"
    triggers = _triggers(result)
    names = {t.split(":", 1)[0] for t in triggers}
    assert "cross_institution_scope_assumed" in names


async def test_cross_institution_scope_not_flagged_for_an_ordinary_question() -> None:
    result = await sanity_check(
        _state(
            question="what is my school's average quiz score",
            query_result=[{"avg_score": 71.2}],
        )
    )

    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_cross_institution_scope_trigger_reason_is_explicitly_flagged_as_a_heuristic() -> (
    None
):
    result = await sanity_check(
        _state(
            question="how do we compare to other institutions nationwide",
            query_result=[{"avg_score": 71.2}],
        )
    )

    triggers = _triggers(result)
    names = {t.split(":", 1)[0] for t in triggers}
    assert "cross_institution_scope_assumed" in names
    matching = next(t for t in triggers if t.startswith("cross_institution_scope_assumed:"))
    assert "heuristic" in matching
    assert "not a certainty" in matching


async def test_cross_institution_scope_trigger_reason_never_mentions_a_count_or_name() -> None:
    result = await sanity_check(
        _state(
            question="how does our school compare to other schools",
            query_result=[{"avg_score": 71.2}],
        )
    )

    matching = next(
        t for t in _triggers(result) if t.startswith("cross_institution_scope_assumed:")
    )
    assert not any(char.isdigit() for char in matching)


# --- Never mutates query_result ---------------------------------------------------------


async def test_does_not_modify_query_result() -> None:
    rows = [{"mastery_percent": 142.0}, {"mastery_percent": 50.0}]
    original = [dict(row) for row in rows]
    result = await sanity_check(_state(query_result=rows))
    assert result["query_result"] == original


async def test_preserves_existing_audit_entry_keys() -> None:
    result = await sanity_check(
        _state(
            query_result=[],
            audit_entry={"role_scope_applied": "rewritten", "scoped_by_user_id": "u1"},
        )
    )
    audit_entry = result["audit_entry"]
    assert audit_entry is not None
    assert audit_entry["role_scope_applied"] == "rewritten"
    assert audit_entry["scoped_by_user_id"] == "u1"
    assert "sanity_check_triggers" in audit_entry


# --- zero_result_is_valid: a template whose zero is a genuine answer --------------------------


async def test_zero_rows_from_zero_valid_template_stay_high_when_students_visible() -> None:
    state = _state(question="which students have attendance below 40%?", query_result=[])
    state["zero_result_is_valid"] = True
    state["zero_probe_results"] = {"visible_students": 72}

    result = await sanity_check(state)

    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_zero_count_from_zero_valid_template_stays_high_when_students_visible() -> None:
    state = _state(
        question="how many students have attendance below 40%?",
        query_result=[{"student_count": 0}],
    )
    state["zero_result_is_valid"] = True
    state["zero_probe_results"] = {"visible_students": 72}

    result = await sanity_check(state)

    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_zero_is_valid_flag_alone_is_not_trusted_without_a_probe() -> None:
    # The whole point of the visibility probe: a flag on the template cannot tell "nothing
    # matches" from "role-scoping silently returned nothing".
    state = _state(question="which students have attendance below 40%?", query_result=[])
    state["zero_result_is_valid"] = True

    result = await sanity_check(state)

    assert result["confidence"] == "medium"
    assert _triggers(result)[0].startswith("zero_rows:")


async def test_zero_with_no_students_visible_keeps_the_hedge() -> None:
    state = _state(question="which students have attendance below 40%?", query_result=[])
    state["zero_result_is_valid"] = True
    state["zero_probe_results"] = {"visible_students": 0}

    result = await sanity_check(state)

    assert result["confidence"] == "medium"


async def test_zero_with_a_failed_probe_keeps_the_hedge() -> None:
    # A probe that errored is absent from the results, not "assume fine".
    state = _state(question="how many students have attendance below 40%?", query_result=[{"c": 0}])
    state["zero_result_is_valid"] = True
    state["zero_probe_results"] = {}

    result = await sanity_check(state)

    assert result["confidence"] == "medium"


async def test_empty_result_for_a_subject_the_teacher_does_not_teach_is_explained() -> None:
    state = _state(question="average mastery in English?", query_result=[{"average_score": None}])
    state["intent_parameters"] = {"subject": "English"}
    state["zero_probe_results"] = {"taught_subjects": ["Mathematics", "Science"]}

    result = await sanity_check(state)

    assert result["confidence"] == "high"
    assert _triggers(result) == []


async def test_empty_result_for_a_subject_the_teacher_does_teach_is_still_hedged() -> None:
    state = _state(question="average mastery in Science?", query_result=[{"average_score": None}])
    state["intent_parameters"] = {"subject": "Science"}
    state["zero_probe_results"] = {"taught_subjects": ["Mathematics", "Science"]}

    result = await sanity_check(state)

    assert result["confidence"] == "medium"


async def test_zero_is_valid_does_not_silence_other_triggers() -> None:
    # Only the two zero-result triggers are lifted: a percentage outside 0-100 is still
    # "low" no matter what the template declares about zeros.
    state = _state(query_result=[{"attendance_percent": 140.0}])
    state["zero_result_is_valid"] = True
    state["zero_probe_results"] = {"visible_students": 72}

    result = await sanity_check(state)

    assert result["confidence"] == "low"


async def test_zero_rows_without_the_flag_still_downgrade() -> None:
    state = _state(question="how many students failed?", query_result=[])
    state["zero_result_is_valid"] = False

    result = await sanity_check(state)

    assert result["confidence"] == "medium"


# --- the templates' averaged percentage aliases get the same 0-100 bounds check --------------


async def test_average_attendance_percent_above_100_is_flagged_low() -> None:
    result = await sanity_check(_state(query_result=[{"average_attendance_percent": 140.0}]))

    assert result["confidence"] == "low"
    assert _triggers(result)[0].startswith("aggregate_out_of_bounds:")


async def test_average_score_percent_within_bounds_is_not_flagged() -> None:
    result = await sanity_check(_state(query_result=[{"average_score_percent": 70.08}]))

    assert result["confidence"] == "high"
    assert _triggers(result) == []
