"""Turns state["query_result"] into state["natural_answer"] and state["provenance"].
Honors everything upstream nodes already decided — never re-derives or second-guesses
state["confidence"], state["error"], or the data itself. This node phrases; it does not
judge.

**LLM call: only when the data actually needs natural-language shaping.** Three result
shapes are fully deterministic and get templated phrasing with no model call at all:

* **Zero rows** — "I couldn't find any records matching that question." is the honest,
  complete answer; there is nothing for a model to add.
* **A single row with a single column** — the classic COUNT/AVG/aggregate shape (e.g.
  `{"mastery_percent": 82.5}`). The value is rendered directly from the row, verbatim,
  with a light templated sentence built from the column name — never re-typed or
  paraphrased by a model, which is one less place a number that is already correct could
  come out wrong. (This is a *different* case from zero rows: `SELECT COUNT(*) ...`
  always returns exactly one row, even when the count is 0 — that row still goes through
  this path, not the zero-row one.) If `state["answer_criterion"]` is set — a template's
  own short phrase for a policy-defined criterion that isn't obvious from the number
  alone (e.g. "mastery below 60% or attendance below 80%" for the "struggling"/"at risk"
  template) — it's appended here too, the same deterministic, never-paraphrased way. A
  bare count like "12" otherwise gives a teacher no way to tell it apart from a mismatch
  with a related-but-differently-scoped figure elsewhere in the app (e.g. the at-risk
  dashboard's own count, which — correctly, per docs/design/08-at-risk-early-warning.md
  §7.2 — excludes attendance-only cases from a subject teacher's view); this makes the
  criterion visible instead of leaving that silently invisible.
* **A small, single-column list** (2 to `_ENUMERABLE_LIST_ROW_CAP` rows, every row
  carrying just one column of the same name — e.g. "what subjects do I teach" returning
  `[{"name": "Mathematics"}, {"name": "Science"}]`). This is the same shape as the
  single-scalar case, generalized along one dimension (several values instead of one),
  and it carries the exact same risk the single-scalar case was built to avoid: real
  evidence (a live eval run) showed the LLM-summarization path silently dropping one of
  three real values when asked "what subject do I teach" — a teacher with Mathematics and
  Science assignments got told "You teach Mathematics," Science omitted, no error, no low
  confidence, nothing to signal the answer was incomplete. That's exactly the "a number
  that is already correct could come out wrong" failure Task 9 built the single-scalar
  path to prevent, just for a short list instead of one value — so the same fix applies:
  enumerate the distinct values directly from the rows (deduplicated, order preserved),
  never re-typed by a model. The cap (10) is a readability judgment, not a correctness
  one: enumerating "Mathematics, Science, and English" reads fine; enumerating 30 rows
  does not, and at that size a real prose summary is more useful anyway — see the LLM
  path below for anything past the cap, and for any multi-column result at any size,
  where a safe generic template does not exist.

  **Scope, deliberately narrow — attribute lists, never entity rosters.** This path
  fires only when the shared column is literally named `name` (`_single_column_list_shape`
  gates on the exact string, not just "one column"), never on `full_name` or anything
  else. That's not an arbitrary restriction: deduplicating is only safe for a small,
  closed *attribute/category* set (a subject, a section, a grade — "teaches Mathematics"
  is one true fact no matter how many rows say so) and actively unsafe for an *entity
  roster* (student names, or any list meant to enumerate distinct people/things), where a
  repeated-looking value could be a genuine duplicate-row bug or two different real
  entities sharing a label — either way, silently merging them would hide something worth
  seeing rather than fix a display quirk. The schema already draws this line on its own:
  every person-identifying table (`users`, `student_profiles`) names this column
  `full_name`; every attribute table this pipeline exposes names it bare `name`. A future
  change that widens this path to more columns must re-justify that the new column is
  genuinely attribute-shaped, not assume the cap/shape checks alone are sufficient — they
  were never the safety mechanism here, the column name is.

* **A roster with one attribute value per person** (`full_name` plus exactly one other
  scalar column, up to `_ROSTER_ATTRIBUTE_ROW_CAP` rows — e.g. "list my students below
  60% and their marks" returning `[{"full_name": "...", "mastery_percent": 47.45}, ...]`).
  Added after a live failure: asked to summarize an 8-row result of exactly this shape,
  the LLM path below stated a wrong headline count ("There are five students...") over
  data that was already fully correct — `result_row_count` is always the real, exact
  count, so nothing here ever needed a model to (mis)count it. Unlike the single-column
  list above, this path never deduplicates — see `_roster_with_attribute_shape`'s own
  docstring for why merging roster entries is unsafe in a way merging attribute values
  isn't.

Everything else — a multi-row result with more than one column not matching the roster
shape just above, a single-column list past the enumeration cap, or a single-column list
whose column isn't `name` (a bare list of scores, ...) — goes through an LLM call (same
OpenRouter client/pattern as generate_sql) to turn the rows into readable prose, since
summarizing or
describing a larger, richer, or entity-identifying result well is exactly the kind of task
templating handles badly (or unsafely) and language models handle well. The prompt
instructs the model to use only the values it's
given and never invent, estimate, or alter any of them; if the call itself fails
(network/API error), this falls back to a generic-but-honest templated count rather than
surfacing a pipeline error over what is otherwise a fully successful query — the LLM only
shapes phrasing here, it was never the thing that could make the answer wrong. It can,
however, as the single-column-list finding above shows, make the answer *incomplete*
without any signal that it did — a real, open risk for every result shape still on this
path (multi-column, or single-column past the cap), not just the one case just fixed.

**Confidence must show up in the words, not just ride along as a field.** Any
`state["confidence"]` other than `"high"` gets a hedge sentence, built by `_confidence_hedge`
and appended deterministically after whatever core answer was produced, never left to the
model to remember to include. The hedge is severity- and trigger-aware rather than one fixed
string: `"medium"` and `"low"` get different lead clauses (`_HEDGE_LEAD`), and when a
recognized trigger fired, a plain-English reason clause is appended too, built only from the
hand-written `_HEDGE_REASON_PHRASES` table — keyed by trigger *name*, never built from a
trigger's own `detail` text, which can mention column/table names unsafe to echo back to the
user. This guarantees, by construction, that a medium/low-confidence answer can never read
identically to a high-confidence one, and that medium and low no longer read identically to
each other either (a real gap in the previous single-sentence version).

**Scope-narrowing disclosure is the same pattern, kept separate from the hedge.** When
`sanity_check`'s `cross_institution_scope_assumed` trigger fired (the question implied a
cross-institution comparison this pipeline structurally never performs — see that module's
docstring), a fixed, non-interpolated sentence (`_SCOPE_NARROWING_DISCLOSURE`) is appended
right after the core answer. It is a qualifier of *what was answered* ("this is your
institution's data only"), not a data-quality caveat, so it is appended unconditionally on
that trigger regardless of `confidence`, and before the confidence hedge. Being a fixed
string literal — never built with an f-string or any data — is what guarantees it can never
leak whether other institutions exist, how many, or anything about them; this is the one
constant to audit for that property rather than N call sites that could each phrase it
differently.

**Row-cap truncation is the same pattern.** If `state["audit_entry"]["row_cap_truncated"]`
is set (Task 7 truncated the result), a fixed disclosure sentence naming the actual cap
(`execute_sql.ROW_CAP`, imported, not a second literal) is appended the same
deterministic way — never dependent on the model choosing to mention it.

**provenance** describes what was queried and, if any, which confidence-affecting checks
fired — enough for a teacher or admin to sanity-check where a number came from — but never
the raw SQL text itself (this pipeline's own rule: SQL is never user-facing, only logged
via audit_log). Table names are recovered by parsing `state["validated_sql"]` with
sqlglot (the same tool this pipeline already uses everywhere else for structural SQL
work) and keeping only the table *names* found, discarding the query text itself — the
parse is a display convenience here, not a security boundary, so it doesn't need
apply_role_scope's CTE-alias rigor.

**state["error"] already set:** under graph.py's current wiring this is structurally
unreachable — compose_answer's only incoming edges come from sanity_check, which itself
only runs after execute_sql's "ok" edge (i.e. no error). But this node checks anyway and
returns state completely untouched if an error is already present, rather than trusting
that invariant to hold forever: a future routing change is exactly the kind of thing that
could quietly violate it, and fabricating a confident-sounding answer over a real error
would be a worse failure mode than a defensive check that should never fire.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from education_platform.core.config import get_settings
from education_platform.modules.assistant.openrouter import OpenRouterError, chat_completion
from education_platform.modules.guard.reminder import with_hardened_reminder
from education_platform.modules.text_to_sql.nodes.execute_sql import ROW_CAP, is_zero_result
from education_platform.modules.text_to_sql.nodes.sanity_check import PERCENTAGE_COLUMNS
from education_platform.modules.text_to_sql.state import TextToSQLState

_ZERO_ROW_ANSWER: Final[str] = "I couldn't find any records matching that question."

# Fixed, non-interpolated — see the module docstring's "Scope-narrowing disclosure"
# section for why this must never be built from data (count, institution name, ...).
_SCOPE_NARROWING_DISCLOSURE: Final[str] = (
    "I can only see and report on your own institution's data, so this doesn't "
    "include any other school or institution."
)

# Lead clause for the confidence hedge, keyed by state["confidence"]. Always differs
# between "medium" and "low" even when no recognized trigger name is present, so the two
# severities never render identically (see module docstring).
_HEDGE_LEAD: Final[dict[str, str]] = {
    "medium": "This is a preliminary estimate",
    "low": "This result may not be fully reliable",
}

# Plain-English reason clauses, keyed by sanity_check trigger *name* only — never built
# from a trigger's own `detail` string, which can carry column/table names unsafe to
# surface in a user-facing answer. One entry per sanity_check.py trigger except
# `cross_institution_scope_assumed`, which has its own separate, institution-agnostic
# disclosure (`_SCOPE_NARROWING_DISCLOSURE`) and is deliberately excluded here so the
# hedge's reason list can never carry scope-related wording.
_HEDGE_REASON_PHRASES: Final[dict[str, str]] = {
    "zero_rows": "no matching data was found",
    "zero_valued_aggregate": "the calculated value came back as zero or unavailable",
    "row_cap_truncated": "the result was too large to show in full",
    "aggregate_out_of_bounds": "a calculated percentage looked inconsistent",
    "single_row_for_list_question": (
        "the result looked smaller than expected for this kind of question"
    ),
    "large_result_near_cap": ("the result looked larger than expected for this kind of question"),
    "duplicate_entity_in_roster": "the same person appeared more than once in the results",
    "borderline_template_confidence": (
        "the question was only a borderline match to how it was answered"
    ),
}

_TRUNCATION_DISCLOSURE: Final[str] = (
    f"This shows only the first {ROW_CAP} results — narrow your question for a complete count."
)

_MAX_ROWS_IN_PROMPT: Final[int] = 50

# See the module docstring's third deterministic-templating bullet: a small, single-column
# list is enumerated directly rather than trusted to an LLM summarization call. Chosen so
# the enumerated sentence stays readable ("Mathematics, Science, and English") rather than
# becoming an unreadable wall of comma-separated items -- past this size a real prose
# summary from the LLM path is more useful than a flat list anyway.
_ENUMERABLE_LIST_ROW_CAP: Final[int] = 10

# Single-column lists safe to enumerate (and deduplicate): the column holds a closed
# attribute/category label, never a person. Maps the column name to the lead word of the
# sentence. `subject` / `section` are the aliases the governed templates use for exactly
# these category lists (my_subjects, my_sections, list_school_subjects); without them the
# LLM summarizer was free to rephrase a complete list ("You teach either Mathematics or
# Science" for a teacher who teaches both).
_ENUMERABLE_LIST_LEADS: Final[dict[str, str]] = {
    "name": "Name",
    "subject": "Subjects",
    "section": "Sections",
}

_SYSTEM_PROMPT = """You answer questions about school data using ONLY the query result \
rows you are given.

Rules:
- Use ONLY the values in the provided rows. Never invent, estimate, round differently, \
or alter any number, name, or fact that isn't literally present in the data.
- Write 1-4 sentences of plain, natural language a teacher or administrator would find \
clear and easy to read.
- Prefer plain-English phrasing over raw field names where it reads naturally (e.g. \
"attendance rate" rather than "attendance_percent"), but never change the underlying \
value while doing so.
- Do not mention SQL, tables, columns, queries, or any other database internals.
- Do not add any caveats about confidence, completeness, or data quality — that is \
handled separately, outside your answer.
- Answer only the question asked; do not add unrelated commentary."""


def _humanize_column(name: str) -> str:
    return name.replace("_", " ").strip()


# An aggregate (average / highest / lowest ...) of one of the percentage measures, as the
# free-form SQL tends to alias it: `average_score`, `highest_quiz_score`, `average_attendance`,
# `lowest_mastery`. Deliberately narrow: the name must end in score / attendance / mastery,
# and anything marked raw is excluded (`score_raw` is points, not a percentage).
_AGGREGATE_PERCENT_ALIAS: Final[re.Pattern[str]] = re.compile(
    r"^(?:average|avg|highest|lowest|maximum|minimum|max|min)_(?:[a-z]+_)*(?:score|attendance|mastery)$"
)


def _is_percent_column(lowered: str) -> bool:
    if lowered in PERCENTAGE_COLUMNS or lowered.endswith("_percent"):
        return True
    return "raw" not in lowered and _AGGREGATE_PERCENT_ALIAS.match(lowered) is not None


def _format_percent_value(value: Any) -> str:
    """A percentage as text: a value with more than two decimal places (an unrounded AVG such
    as 71.2001388888888889) is rounded to two; anything already at two or fewer is untouched."""
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    exponent = number.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        number = number.quantize(Decimal("0.01"))
    return str(number)


def _scalar_label_and_unit(column: str) -> tuple[str, bool]:
    """The phrase naming a scalar column and whether its value is a percentage.

    A column is a percentage if it is one of the schema's known 0-100 columns
    (`PERCENTAGE_COLUMNS`), is named `*_percent`, or is an aggregate alias of a percentage
    measure (`_AGGREGATE_PERCENT_ALIAS`). The templates alias their averages
    `average_attendance_percent` / `average_score_percent` so "What is the average attendance
    rate?" reads "The average attendance is 90.83%." and not a bare "90.83"; the free-form
    path's `average_score` / `highest_quiz_score` read the same way.
    For `average_*_percent` aliases the trailing "percent" is dropped from the phrase (the %
    sign carries it); a plain schema column keeps its established wording, e.g.
    "The mastery percent is 82.5%."."""
    lowered = column.lower()
    label = _humanize_column(column)
    if lowered.startswith("average_") and lowered.endswith("_percent"):
        label = label[: -len(" percent")]
    return label, _is_percent_column(lowered)


def _single_scalar_answer(column: str, value: Any, criterion: str | None = None) -> str:
    label, is_percent = _scalar_label_and_unit(column)
    if value is None:
        return f"No {label} data is available for that."
    if is_percent:
        sentence = f"The {label} is {_format_percent_value(value)}%."
    else:
        sentence = f"The {label} is {value}."
    if not criterion:
        return sentence
    # Appended verbatim, not re-typed or paraphrased — same discipline as the rest of
    # this node's deterministic paths (see module docstring). A template's `criterion`
    # names a policy-defined threshold that isn't obvious from the number alone (e.g.
    # "mastery below 60% or attendance below 80%"); without it, a count like "12" reads
    # as potentially inconsistent with a related but differently-scoped figure
    # elsewhere in the app (e.g. the at-risk dashboard's own, narrower-scoped count) —
    # this makes the criterion visible instead of letting that look like a bug.
    return f"{sentence[:-1]} ({criterion})."


def _english_join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _single_column_list_shape(rows: list[dict[str, Any]], row_count: int) -> str | None:
    """The shared column name if every row in `rows` has exactly one column, all sharing
    that same name, that name is the schema's own attribute-label convention (see below),
    and the list is short enough to enumerate directly (see `_ENUMERABLE_LIST_ROW_CAP`) —
    `None` if this isn't that shape at all. `row_count` is the caller's own already-
    computed count (`state["result_row_count"]`, falling back to `len(rows)`), not
    re-derived from `len(rows)` here, so a truncated `query_result` list can't be mistaken
    for a genuinely short result.

    Scope boundary, deliberate and load-bearing — do not widen without re-reading this:
    this path deduplicates (see `_enumerated_list_answer`), which is only safe for a
    small, closed *attribute/category* set (a subject name, a section name, a grade name)
    where two rows carrying the same value are, semantically, the same real-world fact
    stated twice — "teaches Mathematics" is true once no matter how many of a teacher's
    rows say so. It is NOT safe for an *entity roster* (student names, or any list where
    each row is meant to represent a distinct person or thing): two rows with the same
    name there could be a genuine duplicate-row bug (undetected by this node, which never
    re-derives correctness — see the module docstring) or, just as plausibly, two
    different real students who happen to share a name — either way, silently merging
    them into one listed entry would hide something worth seeing, not fix a display quirk.
    The schema's own naming convention already draws this line for us: every person-
    identifying table (`users`, `student_profiles`) names this column `full_name`;
    every attribute/category table this pipeline exposes (`subjects`, `grades`,
    `sections`, and their peers) names it bare `name`. Gating on the literal column name
    `"name"` — not just "single column, short list" — is what keeps this path out of
    roster territory without needing to know what table the model actually queried. The
    templates alias their category lists `subject` / `section` (see
    `_ENUMERABLE_LIST_LEADS`); those are category labels too and are gated the same way.
    """
    if not (1 < row_count <= _ENUMERABLE_LIST_ROW_CAP) or len(rows) != row_count:
        return None
    columns = {frozenset(row.keys()) for row in rows}
    if len(columns) != 1:
        return None
    (only_columns,) = columns
    if len(only_columns) != 1:
        return None
    (column,) = only_columns
    if column not in _ENUMERABLE_LIST_LEADS:
        return None
    return column


def _enumerated_list_answer(column: str, rows: list[dict[str, Any]]) -> str:
    # `column` is always one of `_ENUMERABLE_LIST_LEADS`' keys here — see
    # `_single_column_list_shape`'s gate — never one of PERCENTAGE_COLUMNS, so no
    # percent-sign formatting applies (a bare list of percentage values would carry the
    # same "two rows, same value, is that one real fact or two coincidentally-equal ones?"
    # ambiguity as an entity roster, which is exactly what that gate exists to keep out of
    # this deterministic path).
    label = _ENUMERABLE_LIST_LEADS[column]  # sentence-initial here, unlike the
    # single-scalar template's "The {label} is ..." mid-sentence placement.
    seen: dict[str, None] = {}  # dict, not set: preserves first-seen order: deduplicated,
    # never re-typed by a model, directly from the real rows. Deduplication is safe only
    # because `column == "name"` restricts this to attribute/category labels (subjects,
    # sections, grades) — see the scope-boundary note on `_single_column_list_shape`.
    for row in rows:
        seen.setdefault(str(row[column]), None)
    return f"{label}: {_english_join(list(seen))}."


# A roster (`full_name`) alongside one attribute value per person (a score, a mastery
# percent, an attendance percent, a count) — e.g. "list my students below 60% and their
# marks" — is a materially larger, class-sized result than the bare-category lists
# `_ENUMERABLE_LIST_ROW_CAP` was sized for, so it gets its own, more generous cap rather
# than reusing that constant. Past this, `_llm_answer` is still the fallback (a genuine
# prose summary reads better than a 40-item sentence anyway).
_ROSTER_ATTRIBUTE_ROW_CAP: Final[int] = 40


def _roster_with_attribute_shape(rows: list[dict[str, Any]], row_count: int) -> str | None:
    """The attribute column name if every row in `rows` has exactly two columns — the
    schema's own person-identifying `full_name` convention (see `_single_column_list_shape`'s
    docstring for why that's the naming line this pipeline already draws) plus exactly one
    other scalar column describing that person — and the list is short enough to enumerate
    directly; `None` if this isn't that shape at all.

    This exists because the multi-column path below (`_llm_answer`) has a confirmed,
    live failure mode for exactly this shape: asked to summarize an 8-row
    `(full_name, mastery_percent)` result, the model wrote "There are five students... "
    then separately named two more by name afterward — a wrong headline count over data
    that was already fully correct (`result_row_count` is always the real, exact count;
    nothing here needed the model to count anything). This is the same
    "a number that is already correct could come out wrong" risk Task 9's single-scalar
    path and the module docstring's single-column-list fix both exist to prevent, just for
    a two-column roster instead of a bare scalar or a one-column list — the fix is the same
    shape: never ask a model to restate a count or transcribe a list of real values when the
    real values are already sitting in `rows`.

    Deliberately NOT deduplicated, unlike `_enumerated_list_answer`: two rows sharing a
    `full_name` here are two roster entries, each with its own attribute value that might
    differ — collapsing them could hide a real duplicate-row bug or silently drop a
    genuinely distinct person who happens to share a name, exactly the risk
    `_single_column_list_shape`'s docstring already flags for roster data. Every row is
    rendered exactly as returned.
    """
    if not (1 <= row_count <= _ROSTER_ATTRIBUTE_ROW_CAP) or len(rows) != row_count:
        return None
    columns = {frozenset(row.keys()) for row in rows}
    if len(columns) != 1:
        return None
    (only_columns,) = columns
    if len(only_columns) != 2 or "full_name" not in only_columns:
        return None
    (attribute_column,) = only_columns - {"full_name"}
    return attribute_column


def _roster_with_attribute_answer(
    column: str, rows: list[dict[str, Any]], criterion: str | None = None
) -> str:
    suffix = "%" if column.lower() in PERCENTAGE_COLUMNS else ""
    entries = [f"{row['full_name']} ({row[column]}{suffix})" for row in rows]
    count = len(rows)
    noun = "record" if count == 1 else "records"
    qualifier = f" ({criterion})" if criterion else ""
    return f"Found {count} matching {noun}{qualifier}: {_english_join(entries)}."


def _roster_with_attributes_shape(rows: list[dict[str, Any]], row_count: int) -> list[str] | None:
    """The attribute columns, in query order, if every row in `rows` carries `full_name`
    plus two or more other columns (e.g. the "struggling students" list: name, mastery,
    attendance) and the list is short enough to enumerate; `None` otherwise.

    The two-column case above has its own path and wording; this is the same idea for the
    wider shape that used to fall through to `_llm_answer`. That fall-through is what
    dropped students: asked to summarize a 12-row `(full_name, mastery_percent,
    attendance_percent)` result the model named 9, 2, or even 1 of them (typically
    omitting those flagged on attendance alone), so a teacher read a shorter list than the
    database returned. Every row is rendered exactly as returned, none deduplicated, for
    the same reasons given on `_roster_with_attribute_shape`. Requires at least two rows:
    a single-row result keeps going to the LLM path, which can phrase one person's
    several values naturally and cannot drop anyone.
    """
    if not (2 <= row_count <= _ROSTER_ATTRIBUTE_ROW_CAP) or len(rows) != row_count:
        return None
    key_orders = {tuple(row.keys()) for row in rows}
    if len(key_orders) != 1:
        return None
    (keys,) = key_orders
    if "full_name" not in keys or len(keys) < 3:
        return None
    return [key for key in keys if key != "full_name"]


def _attribute_label(column: str) -> str:
    label = _humanize_column(column)
    return label[: -len(" percent")] if label.endswith(" percent") else label


def _attribute_value(column: str, value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{value}%" if column.lower() in PERCENTAGE_COLUMNS else str(value)


def _roster_with_attributes_answer(
    attribute_columns: list[str], rows: list[dict[str, Any]], criterion: str | None = None
) -> str:
    entries = []
    for row in rows:
        details = ", ".join(
            f"{_attribute_label(column)} {_attribute_value(column, row[column])}"
            for column in attribute_columns
        )
        entries.append(f"{row['full_name']} ({details})" if details else str(row["full_name"]))
    count = len(rows)
    noun = "record" if count == 1 else "records"
    # `;` rather than `, ` between entries: each entry already contains commas.
    qualifier = f" ({criterion})" if criterion else ""
    return f"Found {count} matching {noun}{qualifier}: {'; '.join(entries)}."


def _ensure_roster_complete(answer: str, rows: list[dict[str, Any]], row_count: int) -> str:
    """Guard on the LLM path: if every row names a person but the model's prose leaves
    anyone out, replace it with the complete, deterministic listing. The model is told to
    use only the given rows, but nothing before this checked that it used all of them.
    Only applies when the whole result was handed to the model (`_MAX_ROWS_IN_PROMPT`)
    and every row has the same columns, so the replacement is always well-defined."""
    if not rows or row_count != len(rows) or row_count > _MAX_ROWS_IN_PROMPT:
        return answer
    key_orders = {tuple(row.keys()) for row in rows}
    if len(key_orders) != 1:
        return answer
    (keys,) = key_orders
    if "full_name" not in keys:
        return answer
    if all(str(row["full_name"]) in answer for row in rows):
        return answer
    return _roster_with_attributes_answer([key for key in keys if key != "full_name"], rows)


def _serialize_rows_for_prompt(rows: list[dict[str, Any]]) -> str:
    shown = rows[:_MAX_ROWS_IN_PROMPT]
    lines = [json.dumps(row, default=str) for row in shown]
    body = "\n".join(lines)
    if len(rows) > _MAX_ROWS_IN_PROMPT:
        body += f"\n... and {len(rows) - _MAX_ROWS_IN_PROMPT} more row(s) not shown here."
    return body


def _build_messages(question: str, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    user_content = (
        f"Question: {question}\n\n"
        f"Query result rows ({len(rows)} total):\n{_serialize_rows_for_prompt(rows)}"
    )
    return [
        {"role": "system", "content": with_hardened_reminder(_SYSTEM_PROMPT)},
        {"role": "user", "content": user_content},
    ]


def _fallback_summary(rows: list[dict[str, Any]]) -> str:
    count = len(rows)
    noun = "record" if count == 1 else "records"
    return f"Found {count} matching {noun}."


async def _llm_answer(question: str, rows: list[dict[str, Any]]) -> str:
    settings = get_settings()
    messages = _build_messages(question, rows)
    try:
        raw = await chat_completion(messages, settings=settings, temperature=0.0)
    except OpenRouterError:
        return _fallback_summary(rows)
    text = raw.strip()
    return text if text else _fallback_summary(rows)


async def _compose_core_answer(
    question: str,
    rows: list[dict[str, Any]],
    row_count: int,
    criterion: str | None = None,
    empty_answer: str | None = None,
) -> str:
    if row_count == 0:
        return empty_answer or _ZERO_ROW_ANSWER
    if row_count == 1 and len(rows[0]) == 1:
        ((column, value),) = rows[0].items()
        return _single_scalar_answer(column, value, criterion)
    list_column = _single_column_list_shape(rows, row_count)
    if list_column is not None:
        return _enumerated_list_answer(list_column, rows)
    roster_column = _roster_with_attribute_shape(rows, row_count)
    if roster_column is not None:
        return _roster_with_attribute_answer(roster_column, rows, criterion)
    attribute_columns = _roster_with_attributes_shape(rows, row_count)
    if attribute_columns is not None:
        return _roster_with_attributes_answer(attribute_columns, rows, criterion)
    return _ensure_roster_complete(await _llm_answer(question, rows), rows, row_count)


def _not_taught_answer(state: TextToSQLState, rows: list[dict[str, Any]]) -> str | None:
    """Plain explanation for an empty result about a subject the teacher doesn't teach.

    Only when the `taught_subjects` probe (run by execute_sql in the same RLS-scoped
    transaction) actually answered and the asked-about subject isn't in it. Row-level
    security hides other teachers' subjects, so the underlying query legitimately comes
    back empty or NULL ("No average score data is available...", "I couldn't find any
    records...") — true, but it read like a data fault. The subject and the list of
    subjects come from validated parameters and the teacher's own assignments, never from
    free text."""
    subject = (state.get("intent_parameters") or {}).get("subject")
    taught = (state.get("zero_probe_results") or {}).get("taught_subjects")
    if not subject or taught is None or subject in taught or not is_zero_result(rows):
        return None
    sentence = f"You don't teach {subject}, so there is nothing to show for it."
    if taught:
        sentence += f" The subjects you teach are {_english_join(list(taught))}."
    return sentence


def _triggered_names(audit_entry: dict[str, Any]) -> list[str]:
    """Trigger names (not full reason strings) sanity_check recorded, parsed back out of
    the "name: detail" format sanity_check.py's own `reasons = [f"{name}: {detail}" ...]`
    always writes. Parsing this back out, rather than adding a second structured field
    alongside `audit_entry["sanity_check_triggers"]`, reuses the one existing mechanism
    instead of introducing a parallel one; this is safe because sanity_check.py always
    formats with exactly this "name: " prefix (confirmed in that module's own `reasons`
    line) and trigger names never themselves contain ": ".
    """
    triggers = audit_entry.get("sanity_check_triggers") or []
    return [t.split(": ", 1)[0] for t in triggers]


def _confidence_hedge(confidence: str, triggered_names: list[str]) -> str:
    lead = _HEDGE_LEAD.get(confidence, _HEDGE_LEAD["medium"])
    reasons = [
        _HEDGE_REASON_PHRASES[name] for name in triggered_names if name in _HEDGE_REASON_PHRASES
    ]
    reason_clause = f" because {_english_join(reasons)}" if reasons else ""
    return (
        f"{lead}{reason_clause} — a data-quality check flagged this result, so please "
        "treat it as approximate rather than final (see the audit trail for specifics)."
    )


def _tables_referenced(sql: str | None) -> list[str]:
    if not sql:
        return []
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except SqlglotError:
        return []
    return sorted({table.name.lower() for table in tree.find_all(exp.Table)})


def _provenance(state: TextToSQLState) -> str:
    if state.get("query_source") == "service":
        parts = ["Read the at-risk flags visible to this viewer."]
    else:
        tables = _tables_referenced(state.get("validated_sql"))
        parts = [f"Queried: {', '.join(tables)}." if tables else "No tables were queried."]
    audit_entry = state.get("audit_entry") or {}
    triggers = audit_entry.get("sanity_check_triggers") or []
    if triggers:
        parts.append("Data-quality checks flagged: " + "; ".join(triggers) + ".")
    return " ".join(parts)


async def compose_answer(state: TextToSQLState) -> TextToSQLState:
    if state.get("error"):
        return state

    rows = state.get("query_result") or []
    row_count = state.get("result_row_count")
    if row_count is None:
        row_count = len(rows)
    question = state.get("question") or ""
    confidence = state.get("confidence") or "high"
    audit_entry = state.get("audit_entry") or {}
    criterion = state.get("answer_criterion")

    triggered_names = _triggered_names(audit_entry)

    empty_answer = state.get("empty_answer")
    not_taught = _not_taught_answer(state, rows)
    sentences = [
        not_taught
        if not_taught is not None
        else await _compose_core_answer(question, rows, row_count, criterion, empty_answer)
    ]
    if "cross_institution_scope_assumed" in triggered_names:
        sentences.append(_SCOPE_NARROWING_DISCLOSURE)
    if confidence != "high":
        sentences.append(_confidence_hedge(confidence, triggered_names))
    if audit_entry.get("row_cap_truncated"):
        sentences.append(_TRUNCATION_DISCLOSURE)

    return {
        **state,
        "natural_answer": " ".join(sentences),
        "provenance": _provenance(state),
    }
