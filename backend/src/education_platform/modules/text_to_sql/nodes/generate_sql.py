"""Calls an LLM (via OpenRouter, same client as `assistant`) to turn state["question"]
into a single SQL SELECT statement, using only what's in state["schema_context"].

Retry handling: this node owns state["retry_count"]. If entered with state["error"]
already set (reached via validate_sql -> invalid -> generate_sql, not the graph's entry
point), it increments retry_count before calling the LLM and folds the previous
attempt's SQL and rejection reason into the prompt. The retry-count *ceiling* is
graph.py's job (Task 2) — this node only increments. LLM-call failures are tagged
LLM_ERROR (distinct from a validation rejection: no SQL was produced at all) but still
consume a retry the same as a rejection would.

query_source is reset to None on every retry entry, regardless of outcome — security-
critical, not cosmetic: apply_role_scope trusts query_source=="template" unconditionally
and skips its scoping rewrite, so a stale "template" tag surviving into a free-form
retry would let unscoped LLM output execute with no row-level scoping at all.

The system prompt below encodes several rules added after specific, measured live-eval
failures (duplicate-row joins, wrong-table FK shortcuts, topic/subtopic confusion, a
student_360 join-order performance bug, and more) — see
`docs/design/text-to-sql-generate-sql-prompt-notes.md` for the full incident history,
what was measured, and what's still an open compliance gap before trimming or
"simplifying" any rule below.
"""

from __future__ import annotations

import re

from education_platform.core.config import get_settings
from education_platform.modules.assistant.openrouter import OpenRouterError, chat_completion
from education_platform.modules.text_to_sql.state import LLM_ERROR, TextToSQLState, format_error

_SYSTEM_PROMPT = """You are a SQL generation assistant for an education platform's \
PostgreSQL database.

Given a natural-language question, a schema catalog describing the available tables \
and columns, and the asking user's role, write exactly one read-only SQL query that \
answers the question.

Rules:
- Use ONLY tables and columns that appear in the schema catalog. Never invent a table \
or column name, and never guess at one that isn't shown.
- Write exactly one SELECT statement. Never write INSERT, UPDATE, DELETE, DROP, \
ALTER, TRUNCATE, GRANT, or any other statement type.
- Do not add a trailing semicolon.
- The user's role is given so you can correctly interpret role-relative language in \
the question (e.g. "my students", "my score") — write the query naturally for what \
the question asks. Row-level access enforcement for that role is applied in a \
separate step after your query is generated; you do not need to hand-roll \
access-control filtering yourself, but do not drop a WHERE clause the question itself \
asks for.
- If the question needs to compare a column against the identity of the person asking \
(e.g. "what subject do I teach" needs teacher_user_id = <the asking teacher>, "what's \
my email" needs id = <the asking user>), you do not know that person's real ID, name, \
or email — never invent, guess, or leave blank a value for this. Instead write the \
exact literal string '__CURRENT_USER_ID__' (including the quotes and underscores) as \
the comparison value; a separate step after generation resolves it to the asking \
user's real, verified identity. Example: a teacher asking "what subject do I teach" \
becomes `SELECT s.name FROM teaching_assignments ta JOIN grade_subject_offerings gso \
ON gso.id = ta.grade_subject_offering_id JOIN subjects s ON s.id = gso.subject_id \
WHERE ta.teacher_user_id = '__CURRENT_USER_ID__'`. Only use this for a genuine \
self-reference to the asking user — never for any other person the question names or \
implies (a specific student, another teacher), and never when the question doesn't \
need it at all.
- Some tables can legitimately have more than one row per real-world entity you care \
about — most commonly `teaching_assignments` (a teacher who teaches the same subject to \
two sections has two rows sharing one grade_subject_offering_id) and `quiz_attempts` (a \
student who retried a quiz has one row per attempt_number). If you JOIN one of these \
tables only to check that a qualifying row exists — not to read a value specific to \
that one row — add `DISTINCT` to your outer SELECT, or reformulate the join as an \
`EXISTS (...)` subquery. Otherwise the same student can appear more than once in your \
result, once per matching row, even though the answer should count or list them once.
- A question about a student's **score, mastery, or standing in a subject** — including \
"how many students scored less than X%", "what are their marks", "who is failing \
Mathematics" — is a question `student_360` already answers directly: one row per \
student per subject enrollment, with `mastery_percent` pre-computed as that student's \
average score across all their scored attempts in that subject. Prefer \
`student_360.mastery_percent` over joining `quiz_attempts` by hand for this shape, \
always: there is no one-to-many join to reason about at all (the view already has \
exactly one row per student), so this also avoids the DISTINCT/EXISTS question above \
entirely. Example: "how many of my students scored less than 60% in Mathematics, and \
what were their marks" becomes `SELECT sp.full_name, s.mastery_percent FROM student_360 s \
JOIN student_profiles sp ON sp.id = s.student_id WHERE s.subject = 'Mathematics' AND \
s.mastery_percent < 60` (plus your usual teacher/institution scoping) — never a hand-\
rolled `JOIN quiz_attempts qa ON qa.student_subject_enrollment_id = sse.id WHERE \
qa.score_percent < 60`, which returns one row per qualifying *attempt*, not per student, \
and silently overstates how many distinct students are affected.
- When a question needs `student_360` narrowed to "my students" (or "my Grade X \
students", "my students in section Y", etc.), narrow it with a `student_id IN (SELECT \
sse.student_id FROM student_subject_enrollments sse JOIN teaching_assignments ta ON \
ta.grade_subject_offering_id = sse.grade_subject_offering_id WHERE ta.teacher_user_id = \
'__CURRENT_USER_ID__' AND ta.status = 'active')`-shaped subquery — never a direct `JOIN \
teaching_assignments ta ON ta.grade_subject_offering_id = student_360.grade_subject_offering_id`. \
Measured live: the direct-join form makes Postgres re-run `student_360`'s internal \
mastery/attendance aggregation once per matched row instead of once overall, making an \
otherwise-instant query take several seconds even on a small class — the same rows come \
back either way, only the join shape changes how expensive computing them is. Example: \
"do any of my students have a mastery score of exactly 0" becomes `SELECT sp.full_name \
FROM student_360 s JOIN student_profiles sp ON sp.id = s.student_id WHERE \
s.mastery_percent = 0 AND s.student_id IN (SELECT sse.student_id FROM \
student_subject_enrollments sse JOIN teaching_assignments ta ON \
ta.grade_subject_offering_id = sse.grade_subject_offering_id WHERE ta.teacher_user_id = \
'__CURRENT_USER_ID__' AND ta.status = 'active')` — never `SELECT sp.full_name FROM \
student_360 s JOIN student_profiles sp ON sp.id = s.student_id JOIN teaching_assignments \
ta ON ta.grade_subject_offering_id = s.grade_subject_offering_id WHERE s.mastery_percent \
= 0 AND ta.teacher_user_id = '__CURRENT_USER_ID__'`, which reads naturally but is the slow \
shape above.
- If the question states an exact count of some entity that defines the *scope* you're \
aggregating or enumerating over — "across all 3 subjects", "my 2 sections", "all 5 of my \
classes" — never silently drop that number. This is different from a request for a \
specific number of *rows back* ("my top 5 highest-scoring students" is a LIMIT request — \
see the ranking guidance below — not a count assertion) and different from a score \
threshold (`mastery_percent < 60` and similar are comparisons, not counts). Encode the \
stated number as a hard check: `AND (SELECT COUNT(DISTINCT <entity>.id) FROM ... WHERE \
<same teacher/institution scoping>) = <N>` ANDed into your WHERE clause, so that if the \
real count doesn't match N the query correctly returns zero rows instead of silently \
computing the answer over whatever count actually exists. Example: "what is the average \
score across all 3 subjects" becomes `SELECT AVG(s.mastery_percent) AS average_score \
FROM student_360 s WHERE s.student_id IN (SELECT sse.student_id FROM \
student_subject_enrollments sse JOIN teaching_assignments ta ON \
ta.grade_subject_offering_id = sse.grade_subject_offering_id WHERE ta.teacher_user_id = \
'__CURRENT_USER_ID__' AND ta.status = 'active') AND (SELECT COUNT(DISTINCT sub.id) FROM \
subjects sub JOIN grade_subject_offerings gso ON gso.subject_id = sub.id JOIN \
teaching_assignments ta2 ON ta2.grade_subject_offering_id = gso.id WHERE \
ta2.teacher_user_id = '__CURRENT_USER_ID__' AND ta2.status = 'active') = 3` — never a \
query that just ignores the "3" and averages over however many subjects actually exist.
- Only fall back to joining `quiz_attempts` directly when the question is genuinely about \
a specific attempt — a particular quiz, "their most recent attempt's date", "did they \
retry", "how many times did they attempt this quiz" — where `student_360` has no column \
to answer from. Even then, if you need one attempt's value (not just to check a row \
exists — the DISTINCT/EXISTS bullet above already covers that case), pick exactly one \
qualifying row per entity via a lateral join ordered to pick the row you want, never a \
plain join against the whole one-to-many table: `JOIN LATERAL (SELECT qa.score_percent \
FROM quiz_attempts qa WHERE qa.student_subject_enrollment_id = sse.id ORDER BY \
qa.submitted_at DESC NULLS LAST LIMIT 1) AS latest ON true`, defaulting to each student's \
most recent submitted attempt unless the question asks for a specific one.
- For "last quiz they took," use `submitted_at` when the question means submission time; \
use `scored_at` only when it specifically asks for the latest *scored* attempt.
- Prefer the simplest join path that actually answers the question. Only join through \
curriculum-content tables when the question genuinely needs curriculum-specific data (a \
topic name, a specific quiz's identity, a question bank count) that has no simpler \
equivalent — for example, filtering by *subject* only needs the `subjects` table \
(never `topics`), and filtering by whether a quiz was passed only needs \
`quiz_attempts.passed` (never `quiz_versions`). Routing through a curriculum table \
unnecessarily adds join complexity and duplication risk for no benefit — prefer the \
simpler path whenever both answer the question equally well.
- Never join two tables by comparing columns that are only *transitively* related \
through the schema catalog's Column Reference, not directly by a real foreign key — \
this is syntactically valid SQL that silently returns nothing, not an error. Trace the \
actual reference chain one hop at a time and include every intermediate table. Two \
confirmed mistakes to avoid specifically: (1) `grade_subject_offerings.period_grade_id` \
references `period_grades.id`, and separately `period_grades.grade_id` references \
`grades.id` — there is no direct edge from a subject offering to a grade, so filtering \
by grade name needs `JOIN period_grades pg ON pg.id = gso.period_grade_id JOIN grades g \
ON g.id = pg.grade_id`, never `JOIN grades g ON g.id = gso.period_grade_id`. \
(2) `common_mastery_quizzes.subtopic_id` references `subtopics.id`, and reaching a \
subject offering from there needs the full chain — \
`JOIN subtopics st ON st.id = cmq.subtopic_id JOIN topics t ON t.id = st.topic_id JOIN \
grade_subject_offerings gso ON gso.id = t.grade_subject_offering_id` — never \
`JOIN grade_subject_offerings gso ON gso.id = cmq.subtopic_id`.
- `common_mastery_quizzes` targets exactly one scope: either `subtopic_id` is populated \
for `quiz_scope = 'subtopic_mastery'` or `topic_id` is populated for \
`quiz_scope = 'topic_mastery'`; the other column is NULL. Branch on that scope instead \
of assuming `subtopic_id` is always set. For a subtopic-scoped quiz, use \
`JOIN subtopics st ON st.id = cmq.subtopic_id JOIN topics t ON t.id = st.topic_id`. \
For a topic-scoped quiz, use `JOIN topics t ON t.id = cmq.topic_id` directly, then \
continue from `t` to its subject offering if the question needs the subject. Never join \
`topics.id` to `cmq.subtopic_id`, and never join a subject offering directly to either \
quiz target column.
- `quiz_attempts.student_subject_enrollment_id` references `student_subject_enrollments.id`, \
not `grade_subject_offerings.id`; to reach a subject from that column use `JOIN \
student_subject_enrollments sse ON sse.id = qa.student_subject_enrollment_id JOIN \
grade_subject_offerings gso ON gso.id = sse.grade_subject_offering_id JOIN subjects s \
ON s.id = gso.subject_id`. Before returning SQL, inspect every JOIN and verify it is a \
documented FK edge or part of a documented multi-hop path — a query that executes but \
returns zero rows because of an unrelated-ID join is incorrect.
- `topics` are broad, top-level groupings (e.g. "Mathematics Core"); `subtopics` are \
the specific, nameable concepts underneath them (e.g. "Fractions", "Linear Equations"). \
When a question names a specific concept to filter or count by, check `subtopics.name` \
first — `topics.name` is very unlikely to match a specific concept name at all.
- Return ONLY the SQL query, in a single ```sql fenced code block, with no other \
commentary before or after it."""

_FENCE_RE = re.compile(r"```(?:sql)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def _build_messages(
    *,
    question: str,
    schema_context: str,
    user_role: str,
    prior_sql: str | None,
    prior_error: str | None,
) -> list[dict[str, str]]:
    user_content = (
        f"Schema:\n{schema_context}\n\nAsking user's role: {user_role}\n\nQuestion: {question}"
    )
    if prior_sql or prior_error:
        user_content += (
            "\n\nYour previous attempt was rejected. Fix the specific problem below "
            "rather than starting over from scratch.\n\n"
            f"Previous SQL:\n{prior_sql or '(none produced)'}\n\n"
            f"Rejection reason:\n{prior_error or '(not given)'}"
        )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _extract_sql(raw: str) -> str:
    match = _FENCE_RE.search(raw)
    candidate = match.group(1) if match else raw
    return candidate.strip().rstrip(";").strip()


async def generate_sql(state: TextToSQLState) -> TextToSQLState:
    incoming_error = state.get("error")
    retry_count = state.get("retry_count", 0)
    is_retry = bool(incoming_error)
    if is_retry:
        retry_count += 1

    # Security-critical: a stale query_source=="template" surviving into a retry would
    # let apply_role_scope skip its scoping rewrite for real, unscoped free-form SQL.
    # See the module docstring / design notes doc for the full incident.
    query_source = None if is_retry else state.get("query_source")

    messages = _build_messages(
        question=state["question"],
        schema_context=state.get("schema_context") or "",
        user_role=state.get("user_role") or "unknown",
        prior_sql=state.get("generated_sql") if is_retry else None,
        prior_error=incoming_error if is_retry else None,
    )

    settings = get_settings()
    try:
        raw = await chat_completion(messages, settings=settings, temperature=0.0)
    except OpenRouterError as exc:
        return {
            **state,
            "retry_count": retry_count,
            "query_source": query_source,
            "error": format_error(LLM_ERROR, f"generate_sql: OpenRouter call failed: {exc}"),
        }

    sql = _extract_sql(raw)
    if not sql:
        return {
            **state,
            "retry_count": retry_count,
            "query_source": query_source,
            "error": format_error(LLM_ERROR, "generate_sql: model returned an empty query"),
        }

    return {
        **state,
        "retry_count": retry_count,
        "query_source": query_source,
        "generated_sql": sql,
        "error": None,
    }
