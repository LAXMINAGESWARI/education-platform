"""Inspects state["query_result"]/state["result_row_count"] — already executed by Task 7
— and sets state["confidence"]. Observes and annotates only: never modifies the query,
never re-runs anything, never touches `query_result` itself.

Eight independent checks, each capable of downgrading confidence off a `"high"` start.
More than one can fire on the same result; the recorded reasons list every trigger that
fired, and the final confidence is the **worst** (lowest) of their individual severities
— a "low"-severity trigger anywhere wins over any number of "medium" ones, never averaged
or overridden by a later check. Each trigger's own severity is fixed in `_TRIGGER_SEVERITY`
below, reviewable in one place:

* **Zero rows** (`zero_rows`, medium) — always recorded when it happens, so compose_answer
  can phrase "no matching records" honestly rather than as if a number/list were found, but
  deliberately *not* treated as "no downgrade at all". A genuinely correct query can return
  nothing (the task's own example: "how many students failed" when none did), so this never
  drops to "low" on its own. But this node has no reliable, non-fragile way to tell "this
  empty result is the expected answer" apart from "role-scoping or a WHERE clause silently
  excluded everything" — re-parsing the question to guess intent is exactly the kind of
  fragile heuristic the other checks below are already leaning on as little as possible.
  A flat, unconditional "medium" is the simple, predictable, safe-direction default: it
  never overclaims confidence in an empty result that might be a bug, and it's exactly
  the same "fail toward caution, not toward false confidence" choice this codebase already
  makes elsewhere (e.g. `authorization.predicate._taught_predicate`'s NULL-section handling).
* **Zero-valued aggregate** (`zero_valued_aggregate`, medium) — `SELECT COUNT(*) ...`
  always returns exactly one row, even when the count is 0 (unlike a list-shaped query,
  which returns zero rows for the same real-world fact) — so `zero_rows` above, which keys
  off `result_row_count == 0`, can never catch this shape at all, and a genuinely-zero
  count previously read as fully "high" confidence purely because of *how* the question
  was phrased, not because the underlying result was any more trustworthy. Fires on the
  same single-row/single-column shape `compose_answer._compose_core_answer` already uses
  to detect "this is a scalar answer" (deliberately reusing that same simple, existing
  result-shape check rather than adding fragile SQL-text/AST "is this an aggregate query"
  detection that nothing else in this pipeline needs), when that one value is a numeric
  zero **or `NULL`**. `NULL` used to be routed around this check entirely, on the
  assumption `compose_answer._single_scalar_answer`'s "No `<label>` data is available for
  that." text made the uncertainty visible on its own — confirmed live that it doesn't:
  that function only ever affects the rendered *text*, never `state["confidence"]`, and
  nothing else downgrades confidence for a `NULL` scalar (an `AVG()`-shaped query that
  legitimately matches zero rows returns one row with a `NULL` value, not zero rows, so
  `zero_rows` can't catch it either) — a query correctly excluded by a `WHERE` clause, or a
  genuine data gap, was reading as full "high" confidence with an honest-sounding but
  misleadingly-confident answer. Same "medium" severity as the zero-count case and as
  `zero_rows`, for the identical reason: a genuine zero/no-data result and a role-scoping
  or filter silently excluding everything are indistinguishable from the result alone.
* **Row-cap truncation** (`row_cap_truncated`, low) — reads
  `state["audit_entry"]["row_cap_truncated"]`, the flag `execute_sql` (Task 7) sets when it
  had to truncate. Lower severity than the heuristic checks below: this isn't a guess, it's
  a known fact that the result is incomplete by construction, not merely uncertain.
* **Aggregate sanity bounds** (`aggregate_out_of_bounds`, low) — any row's value for a
  known 0-100 percentage-shaped column (`PERCENTAGE_COLUMNS`, matched against the actual
  column names schema_catalog.md documents as `Numeric` 0-100 percentages, not guessed)
  outside `[0, 100]`. This is structurally impossible from correct SQL against these
  columns, so if it happens something upstream is wrong — "low", the strongest downgrade.
* **Single-row result for a list-shaped question** (`single_row_for_list_question`,
  medium) — a heuristic on `state["question"]`, deliberately conservative: only fires when
  the question matches a roster/list phrase (`_LIST_SIGNAL_PHRASES`) *and* doesn't also
  match an aggregate phrase (`_AGGREGATE_SIGNAL_PHRASES`) — "what's the school's overall
  attendance rate" matches neither pattern's intent to override the other incorrectly
  ("overall"/"rate" suppress the list read), so it stays at "high" as it should. A guess,
  not a hard rule, hence "medium" rather than "low".
* **Suspiciously large result for a bounded-scope question**
  (`large_result_near_cap`, medium) — a question implying a personally-scoped, bounded
  roster (`_BOUNDED_SCOPE_PHRASES`, e.g. "my students") returning close to `execute_sql`'s
  own row cap is a useful cross-check against Task 6, not just a data-quality signal: it
  can mean apply_role_scope's rewrite didn't actually narrow anything. Still a heuristic on
  free text, so "medium" like the check above, not "low" — but worth investigating
  regardless of the label if it ever fires. A question that returns a genuinely large,
  correctly-scoped result without any bounded-scope phrasing (e.g. "how many students are
  in the whole school") never matches `_BOUNDED_SCOPE_PHRASES` and so never fires this at
  all — see the false-positive test in test_text_to_sql_sanity_check.py.

* **Duplicate entity in a roster result** (`duplicate_entity_in_roster`, medium) — the
  same `full_name` value appears in more than one row of a multi-row result. Added after
  a live, confirmed production case: a question asking for students' names alongside their
  marks joined `quiz_attempts` directly and returned the same student once per qualifying
  attempt (see `generate_sql.py`'s own docstring for the full incident) — a syntactically
  valid query, correctly authorized, that still returned a materially wrong answer no
  earlier check here could catch (it isn't zero rows, isn't an out-of-bounds percentage,
  and a duplicated name doesn't shrink `result_row_count` the way `row_cap_truncated`
  would). This check does not — and structurally cannot — determine *why* two rows share a
  name: it could be exactly that duplicate-row bug, or it could be two different real
  students who happen to share a name (confirmed to actually happen in this platform's own
  seed data — multiple distinct students named e.g. "Sami Yusuf" exist). Both readings are
  worth surfacing rather than silently trusting a duplicate-looking roster, so this fires
  either way at "medium," the same severity as the other two heuristic checks below, never
  "low" — a repeated name is a real, visible fact about the result, but which explanation
  applies isn't something this node can resolve from the rows alone. Fires against the raw
  `query_result`, independent of which shape `compose_answer` ends up rendering it in (its
  own deterministic roster-with-attribute path renders duplicates as-is rather than hiding
  them, by the same reasoning — this check is what actually flags that they're there worth
  a second look, not just that they're rendered honestly).

* **Borderline template-match confidence** (`borderline_template_confidence`, medium) —
  `state["query_source"] == "template"` and `state["intent_confidence"]` cleared
  `intent_router`'s routing floor (`template_route_min_confidence()`, imported from that
  module rather than a second copy of the 0.90 default so the two can't drift) by less
  than `_BORDERLINE_CONFIDENCE_MARGIN`. Added because `intent_confidence` was previously a
  fully dead signal — computed by `intent_router`, never read by any downstream node or
  persisted — so a template match that barely cleared the bar (0.90) looked, everywhere
  including the audit trail, identical to one the classifier was highly sure of (0.99).
  This is the same kind of routing-uncertainty signal `single_row_for_list_question`/
  `large_result_near_cap` already surface for the free-form path; a template match is the
  one shape those two structurally cannot cover (they reason about the *result*, and a
  template's result can look perfectly ordinary even when the *routing decision* that
  picked it was shaky). Only fires on the template path — a `None` `intent_confidence`
  (free-form, or `intent_router` itself failed and fell back) never triggers this, since
  there is no routing floor to have barely cleared.

All four of the last checks are guesses, not certainties — their recorded reason strings
say so explicitly (a `"heuristic (...): ..."` prefix), so the audit trail itself never
reads as a confirmed finding for these four, unlike the row-cap/aggregate-bounds checks
above, which are facts.

Routing (graph.py): `state["confidence"]` is the authoritative signal — it is fully
decided here, not by which graph edge fires afterward. graph.py's conditional edge after
this node stays the binary `"suspicious"`/`"normal"` split that already existed as
scaffolding: `"medium"` routes the same way `"low"` does, both via `"suspicious"`, because
nothing downstream currently behaves differently between the two — compose_answer reads
`state["confidence"]` directly for its own phrasing, not the edge that was taken to reach
it. Collapsing "medium" and "low" into one edge loses no information for exactly that
reason. See graph.py's `_route_after_sanity_check` for where this is decided.
"""

from __future__ import annotations

from typing import Any, Final

from education_platform.modules.text_to_sql.nodes.execute_sql import ROW_CAP
from education_platform.modules.text_to_sql.nodes.intent_router import (
    template_route_min_confidence,
)
from education_platform.modules.text_to_sql.state import TextToSQLState

# Columns schema_catalog.md documents as `Numeric`, 0-100-scale percentages (see its
# "Money/percent fields" convention note) among tables this pipeline can actually query
# (load_schema.REQUIRED_TABLES). Deliberately excludes chat_conversations.context_used_percent
# — that table is out of scope for text-to-SQL entirely (load_schema.EXCLUDED_TABLES), so a
# query here can never produce it.
PERCENTAGE_COLUMNS: Final[frozenset[str]] = frozenset(
    {"score_percent", "mastery_percent", "attendance_percent", "pass_threshold_percent"}
)

_LIST_SIGNAL_PHRASES: Final[tuple[str, ...]] = (
    "list all",
    "list of",
    "which students",
    "who are the",
    "names of the",
    "show all",
    "show me the students",
    "every student",
    "each student",
    "students who",
    "all students",
)
_AGGREGATE_SIGNAL_PHRASES: Final[tuple[str, ...]] = (
    "how many",
    "average",
    "overall",
    "total number",
    "rate",
    "percentage",
    "count of",
)
_BOUNDED_SCOPE_PHRASES: Final[tuple[str, ...]] = (
    "my students",
    "my student",
    "my class",
    "my classes",
    "my section",
    "my sections",
)
# "Close to" execute_sql's own row cap — not the cap itself, since a result of exactly
# ROW_CAP could also just be genuinely large for an unbounded question.
_LARGE_RESULT_THRESHOLD: Final[int] = round(ROW_CAP * 0.9)
# How close to intent_router's own routing floor still counts as "barely cleared it" —
# a judgment call, not a value with a natural derivation, same as _LARGE_RESULT_THRESHOLD's
# 0.9 factor above.
_BORDERLINE_CONFIDENCE_MARGIN: Final[float] = 0.05

_SEVERITY_ORDER: Final[tuple[str, ...]] = ("low", "medium", "high")  # index 0 = worst
_TRIGGER_SEVERITY: Final[dict[str, str]] = {
    "zero_rows": "medium",
    "zero_valued_aggregate": "medium",
    "row_cap_truncated": "low",
    "aggregate_out_of_bounds": "low",
    "single_row_for_list_question": "medium",
    "large_result_near_cap": "medium",
    "duplicate_entity_in_roster": "medium",
    "borderline_template_confidence": "medium",
}


def _matches_any(lowered_text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in lowered_text for phrase in phrases)


def _zero_rows_trigger(row_count: int) -> tuple[str, str] | None:
    if row_count == 0:
        return "zero_rows", "query returned no rows"
    return None


def _zero_valued_aggregate_trigger(rows: list[dict[str, Any]]) -> tuple[str, str] | None:
    # Same single-row/single-column shape compose_answer already treats as "this is a
    # scalar answer" (row_count==1 alone isn't enough — a single row from a multi-column
    # SELECT isn't this shape at all). bool is excluded even though it's a subclass of
    # int in Python: no aggregate this pipeline produces is boolean-valued, and treating
    # False as "zero" would be answering a question this check was never meant to ask.
    #
    # NULL fires here too now, not just a literal 0 — see the module docstring's
    # "Zero-valued aggregate" entry for why routing NULL around this check entirely was a
    # real, confirmed-live bug (confidence stayed "high" for a legitimately-empty AVG()
    # result), not a deliberate, correctly-handled split.
    if len(rows) != 1 or len(rows[0]) != 1:
        return None
    ((column, value),) = rows[0].items()
    if isinstance(value, bool):
        return None
    if value is None:
        return (
            "zero_valued_aggregate",
            f"single-value result ({column}) is NULL — the same 'genuinely no data vs. "
            "role-scoping/a filter silently excluded everything' ambiguity zero_rows "
            "already flags for an empty row list, just in the scalar-aggregate form that "
            "always returns one row (with a NULL value) instead of zero rows",
        )
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != 0:
        return None
    return (
        "zero_valued_aggregate",
        f"single-value result ({column}) is zero — the same 'genuinely zero vs. "
        "role-scoping silently returned nothing' ambiguity zero_rows already flags for "
        "an empty row list, just in the COUNT/SUM-shaped form that always returns one "
        "row instead of zero",
    )


def _row_cap_trigger(state: TextToSQLState) -> tuple[str, str] | None:
    audit_entry = state.get("audit_entry") or {}
    if audit_entry.get("row_cap_truncated"):
        return (
            "row_cap_truncated",
            f"execute_sql truncated the result to ROW_CAP={ROW_CAP}; incomplete by construction",
        )
    return None


def _aggregate_bounds_trigger(rows: list[dict[str, Any]]) -> tuple[str, str] | None:
    offenders: set[str] = set()
    for row in rows:
        for key, value in row.items():
            if value is None or key.lower() not in PERCENTAGE_COLUMNS:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if numeric < 0 or numeric > 100:
                offenders.add(f"{key}={value!r}")
    if not offenders:
        return None
    shown = ", ".join(sorted(offenders)[:5])
    return "aggregate_out_of_bounds", f"{shown} outside [0, 100]"


def _single_row_list_trigger(question: str, row_count: int) -> tuple[str, str] | None:
    if row_count != 1:
        return None
    lowered = question.lower()
    if _matches_any(lowered, _AGGREGATE_SIGNAL_PHRASES):
        return None  # aggregate-shaped question — one row is the expected shape
    if _matches_any(lowered, _LIST_SIGNAL_PHRASES):
        return (
            "single_row_for_list_question",
            "heuristic (question-phrasing guess, not a certainty): question implies a "
            "roster/list but only one row came back",
        )
    return None


def _large_result_trigger(question: str, row_count: int) -> tuple[str, str] | None:
    lowered = question.lower()
    if row_count >= _LARGE_RESULT_THRESHOLD and _matches_any(lowered, _BOUNDED_SCOPE_PHRASES):
        return (
            "large_result_near_cap",
            "heuristic (question-phrasing guess, not a certainty): "
            f"{row_count} rows (cap={ROW_CAP}) for a question phrased as a bounded/personal "
            "scope — worth checking whether role-scoping actually narrowed this, not a "
            "confirmed failure",
        )
    return None


def _duplicate_entity_trigger(rows: list[dict[str, Any]]) -> tuple[str, str] | None:
    if not rows or "full_name" not in rows[0]:
        return None
    seen: set[str] = set()
    duplicated: set[str] = set()
    for row in rows:
        name = row.get("full_name")
        if name is None:
            continue
        name = str(name)
        if name in seen:
            duplicated.add(name)
        seen.add(name)
    if not duplicated:
        return None
    shown = ", ".join(sorted(duplicated)[:5])
    return (
        "duplicate_entity_in_roster",
        f"heuristic (name collision or duplicate row, not distinguishable from the "
        f"result alone): full_name value(s) appear in more than one row: {shown}",
    )


def _borderline_template_confidence_trigger(state: TextToSQLState) -> tuple[str, str] | None:
    if state.get("query_source") != "template":
        return None
    confidence = state.get("intent_confidence")
    if confidence is None:
        return None
    floor = template_route_min_confidence()
    # Round the gap, not just compare raw floats: confidence values like 0.90/0.95 are
    # decimal-clean in every prompt/response/test this pipeline deals with, but IEEE 754
    # binary floats can't represent them exactly (confirmed live: `0.95 - 0.90` evaluates
    # to `0.04999999999999993`, and `0.90 + 0.05` to `0.9500000000000001` — either
    # formulation put an intent_confidence of exactly 0.95 on the wrong side of the
    # boundary against a 0.05 margin). Rounding to 6 decimal places is far finer than any
    # real confidence value this pipeline produces, so it only ever absorbs
    # representation noise, never a genuine difference.
    gap = round(confidence - floor, 6)
    if 0 <= gap < _BORDERLINE_CONFIDENCE_MARGIN:
        return (
            "borderline_template_confidence",
            "heuristic (routing-confidence guess, not a certainty): intent_confidence "
            f"{confidence:.2f} barely cleared the template routing floor ({floor:.2f}) — "
            "worth checking this was actually the right template, not a confirmed "
            "misroute",
        )
    return None


def _worst_confidence(triggered_names: list[str]) -> str:
    if not triggered_names:
        return "high"
    ranks = (_SEVERITY_ORDER.index(_TRIGGER_SEVERITY[name]) for name in triggered_names)
    return _SEVERITY_ORDER[min(ranks)]


async def sanity_check(state: TextToSQLState) -> TextToSQLState:
    rows = state.get("query_result") or []
    row_count = state.get("result_row_count")
    if row_count is None:
        row_count = len(rows)
    question = state.get("question") or ""

    triggers = [
        trigger
        for trigger in (
            _zero_rows_trigger(row_count),
            _zero_valued_aggregate_trigger(rows),
            _row_cap_trigger(state),
            _aggregate_bounds_trigger(rows),
            _single_row_list_trigger(question, row_count),
            _large_result_trigger(question, row_count),
            _duplicate_entity_trigger(rows),
            _borderline_template_confidence_trigger(state),
        )
        if trigger is not None
    ]

    confidence = _worst_confidence([name for name, _ in triggers])
    reasons = [f"{name}: {detail}" for name, detail in triggers]

    return {
        **state,
        "confidence": confidence,
        "audit_entry": {
            **(state.get("audit_entry") or {}),
            "sanity_check_triggers": reasons,
        },
    }
