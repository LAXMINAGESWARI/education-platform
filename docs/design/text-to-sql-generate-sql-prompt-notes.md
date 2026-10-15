# `generate_sql` prompt design notes

This document holds the incident history and rationale behind each rule in
`backend/src/education_platform/modules/text_to_sql/nodes/generate_sql.py`'s system
prompt (`_SYSTEM_PROMPT`). It exists so the module's own docstring can stay short — a
contract statement, not an incident log — without losing *why* each rule earned its
place. If you're about to trim, remove, or "simplify" a rule in that prompt, read its
entry here first: several of these were added specifically because the same defect was
observed twice, or because a catalog-only (no prompt-level reinforcement) version of the
same guidance was tried and measured to not work. Don't re-derive a fix a live eval run
already paid for.

Evidence for every claim below (row numbers, compliance rates) comes from repeated
live-model runs, not a single pass — see `backend/tests/test_text_to_sql_generate_sql_live.py`
for the harness and its own "reads a rate, not a boolean" discipline. Re-run it whenever
this prompt changes.

## Retry handling

`generate_sql` owns `state["retry_count"]`. If it's entered with `state["error"]` already
set (reached via the `validate_sql` → invalid → `generate_sql` edge, not the graph's entry
point), it increments `retry_count` before calling the LLM and folds the previous attempt's
SQL and rejection reason into the prompt, so the model has a real correction signal instead
of regenerating blind. The retry-count *ceiling* (routing to `honest_refusal` once
`MAX_RETRIES` is hit) is `graph.py`'s job — this node only increments.

LLM-call failures (network/API errors, missing `OPENROUTER_API_KEY`) are tagged with
`state.py`'s `LLM_ERROR` category — a different failure class from a SQL validation
rejection (no SQL was produced at all, vs. produced SQL was rejected) — so the two stay
distinguishable wherever `state["error"]` is read later (logs, `audit_log`). This only
makes them distinguishable by *content*; an LLM failure still consumes one of the three
retries the same as a validation rejection would. Exempting LLM failures from the retry
budget would need a `graph.py`/`validate_sql.py` change (a separate signal the routing
checks) — out of this node's scope.

**`query_source` reset on every retry — security-critical, not cosmetic.** A retry means
this call's own output is free-form LLM SQL, regardless of how the query that just got
rejected was sourced. This matters specifically for a template match that failed
`validate_sql`: `intent_router` set `state["query_source"] = "template"` and routed
straight here past `load_schema`/`link_schema`, so `schema_context` is empty for this
call — but `apply_role_scope` trusts `query_source == "template"` unconditionally and
skips its entire row/institution-scoping rewrite. Leaving the stale `"template"` tag on a
retry would let this call's real, unscoped LLM output execute with no role-based row
scoping at all. `generate_sql` clears it on every retry entry, regardless of outcome, so a
template-origin retry always falls through to the full free-form scoping rewrite once real
generation happens.

## Self-reference sentinel (`'__CURRENT_USER_ID__'`)

For tables `apply_role_scope` scopes down to *exactly* the asking user's own rows
(`student_360`, `quiz_attempts`, `teaching_assignments`, etc.), the model never needs to
write a self-filter at all — `apply_role_scope` adds it silently. (`teaching_assignments`
joined that list after a live incident: a teacher's "show all teaching assignments"
question, with no self-filter for the model to write, returned the whole school's staff
roster.) But for a table it only institution-pins (`users`, ...), a question like "what's
my email" genuinely needs a `<owner column> = <me>` filter only the model can write, and
this node never gives the model a real identity value (no user_id, no name, no email — by
design). Observed failure without this rule: the model fabricates a placeholder literal
(e.g. `'Your Name Here'`) that matches nothing, producing a confidently-wrong empty answer
for a real, answerable question. The prompt teaches one fixed literal token for every
table, `teaching_assignments` included — writing it there now is harmless and redundant
(`apply_role_scope`'s own predicate is authoritative regardless) rather than required, but
gives the model one consistent rule instead of a per-table exception to remember. The
token is never a name/guess/subquery; `apply_role_scope` resolves it to the real
`state["user_id"]` before execution.

## Multi-row-per-entity joins (DISTINCT/EXISTS)

A live eval run found "list the students enrolled in my subject offerings" returning 120
rows for a teacher with 72 real students. Root cause, confirmed against real data: the
query joined `teaching_assignments` directly, and every teacher checked in the seed data
(24 of 24) has two assignment rows for the same subject offering, one per section taught.
Every matching student got counted once per matching assignment row. Not
`apply_role_scope`'s bug — its own injected predicates already use `EXISTS(...)`
specifically to avoid this — the model's own join, written for filtering rather than
reading an assignment-specific column, multiplies rows the same way any un-deduplicated
JOIN would. `quiz_attempts` has the identical shape for a different reason
(`attempt_number`). The prompt teaches the general pattern (`DISTINCT` or `EXISTS`) rather
than special-casing either table, since this is a structural property of the schema (any
one-to-many table the question doesn't care about the "many" side of), not a one-off.

## Curriculum-table routing

A live eval run found 3 of 39 questions (a specific quiz's pass rate, a subject-filtered
score list, unsubmitted quiz attempts) routed through `quiz_versions`/`topics` and got
refused by `apply_role_scope`'s Roadmap-7A deferred-table gate — even though structurally
similar questions elsewhere in the same run answered successfully via a simpler path
(`subjects` directly, or `quiz_attempts` alone). A real, recurring pattern, not a security
concern (the refusal was safe), but avoidable: the prompt asks the model to prefer the
simplest join path and reserve curriculum tables for when the question genuinely needs
them.

**Status update (2026-09):** all three batches of the deferred-curriculum-table scoping
project have since landed — see `apply_role_scope.py`'s module docstring and
`INSTITUTION_SCOPED_TABLES`. Every curriculum table, including
`quiz_items`/`quiz_material_bindings`/`quiz_releases`, is now classified and scoped, so no
table triggers that refusal any more. The guidance still stands, but the reason has
shifted from "avoid a security refusal" to "avoid unnecessary join complexity/duplication
risk on a now-safely-scoped but still more complex path." Live-eval measured compliance at
100% (10/10) for some phrasings and only 60% (3/5) for a differently-worded question with
the same intent — a real, only-partially-solved prompt-compliance gap, not a closed one.

## Multi-hop FK chains — don't skip the intermediate table

Re-running eval rows 3/45/46 after curriculum-table Batches 1-2 unblocked
topics/subtopics/questions/common_mastery_quizzes/quiz_versions surfaced a bug those
tables' fail-closed refusal had been hiding: `generate_sql` collapsed a real two-hop
foreign-key chain into a single, structurally wrong join by comparing two columns related
only *transitively*, never directly. Two confirmed live:

1. `JOIN grades g ON g.id = gso.period_grade_id` — a grade's id compared against a
   period_grade's id. There is no direct `grade_subject_offerings → grades` edge; the real
   chain is `grade_subject_offerings.period_grade_id → period_grades.id` and separately
   `period_grades.grade_id → grades.id`.
2. `JOIN grade_subject_offerings gso ON cmq.subtopic_id = gso.id` — a subtopic's id
   compared against an offering's id. The real chain is
   `common_mastery_quizzes.subtopic_id → subtopics.id → subtopics.topic_id → topics.id →
   topics.grade_subject_offering_id → grade_subject_offerings.id`.

Both queries executed without error — comparing two UUID columns from unrelated tables is
syntactically valid SQL, just structurally never true — so this failed silently as a
confidently-wrong empty answer, not a rejection. Having the individual FK facts right (the
schema catalog already did, in both cases) wasn't enough on its own to keep the model from
shortcutting a chain it hadn't been told not to shortcut.

**What was actually measured, precisely** (this matters for anyone considering moving this
guidance into the schema catalog and dropping the prompt-level example): the *prompt-only*
version of this rule (no catalog addition) measured 0/5 (0%) compliance on the
grades/period_grades case over 5 live trials. Adding the same FK chain to
`schema_catalog.md`'s Conventions/Glossary sections, **on top of** the existing prompt
rule, re-measured at 1/5 (20%) — a small, real improvement, but still wrong 4 times out of
5. The topic/subtopic naming case (below) got the identical catalog addition and stayed at
0/5 (0%) — no improvement at all. **Neither experiment removed the prompt-level rule to
test a catalog-only version in isolation** — so there is no direct evidence either way for
"prompt rule replaced entirely by catalog documentation." Treat that as an open, testable
question, not a settled one, before acting on it.

## Topic vs. subtopic granularity

Eval row 46 asked about "the Mathematics topic 'Fractions'" and the model filtered
`topics.name = 'Fractions'` — but in this schema `topics` are broad, top-level groupings
(e.g. "Mathematics Core") and `subtopics` are the specific, nameable concepts underneath
them (e.g. "Fractions", "Linear Equations") — confirmed against real seeded data: no topic
named "Fractions" exists anywhere, but a "Fractions" subtopic does. The word "topic" in a
question is natural language, not a promise that the schema's `topics` table is the right
one to filter by name.

Compliance history: prompt guidance alone measured 0/5 (0%). Adding the same distinction
to `schema_catalog.md`'s Conventions and Glossary (with the real topic→subtopics mapping
confirmed against actual seed data — "Mathematics Core" → Fractions/Decimals/Ratios/
Percentages/Algebra — as a worked example) re-measured at 0/5 (0%) again, completely
unchanged, despite being the most concrete, data-grounded correction attempted so far.
Working hypothesis: the question's own wording ("the Mathematics topic Fractions") pulls
toward `topics.name` regardless of documentation. No further prompt/documentation
iteration is planned on this specific case — it's now evidence for the governed-hybrid
(template) approach on this question shape, not an open prompt-tuning task.

## `common_mastery_quizzes` scope branching

`common_mastery_quizzes` targets exactly one of `subtopic_id` or `topic_id`, enforced by a
database check constraint. A prompt example covering only the subtopic branch is
insufficient — it teaches the model to assume `subtopic_id` is always populated even for a
`topic_mastery` quiz, silently mishandling the other branch. The prompt states both
branches explicitly.

## Entity-vs-attempt ambiguity: why `student_360`, not a hand-rolled "latest attempt"

A live production case ("how many of my students scored less than 60% in mathematics")
got two different, both-wrong answers depending only on phrasing. "How many..." alone
undercounted; adding "...mention their marks as well" made the model join `quiz_attempts`
directly and return one row per *qualifying attempt*, not per student — the same student
appeared multiple times (once per attempt below the threshold) with different scores, so a
verified-correct 28-distinct-student answer came back as 15 duplicate-inflated rows.

The first fix attempt taught a hand-rolled "pick each student's latest attempt via a
lateral join" pattern. That resolved the duplication (confirmed: 8 distinct rows, matching
a verified-correct count for "most recent attempt per student") — but it was solving the
wrong ambiguity. `schema_catalog.md` already has a settled answer for exactly this shape:
§6's glossary entry for "a student's mastery / average score in a subject" names
`student_360.mastery_percent` specifically, and §5 says to prefer querying that view over
re-deriving the same joins/aggregates by hand whenever the question is about a student's
overall standing in a subject. `mastery_percent` is `ROUND(AVG(quiz_attempts.score_percent),
2)` across all of a student's scored attempts in that subject (see the view's own
migration) — a genuinely different number from "their latest attempt's score," and the one
this schema had already decided is the canonical answer to "how well is this student doing
in Mathematics."

Ignoring that existing convention in favor of a fresh hand-rolled pattern is exactly how a
*second* inconsistency appeared: a pre-existing template
(`list_students_below_score_in_subject`-shaped match) already answered close phrasings of
this same question via `student_360.mastery_percent`, so a hand-rolled "latest attempt"
answer from the free-form path disagreed with the template's answer for a phrasing one
word away — even after the duplication bug was fixed. The rule teaches `student_360` first
for any "score/mastery in a subject" question — one row per student per subject enrollment
already, so the one-to-many join and the "which attempt" question never come up at all —
and keeps the one-attempt-per-student lateral-join pattern only as a fallback for a
question genuinely about a specific attempt (a particular quiz, "their most recent
attempt's date," "did they retry"), where `student_360` has no column to answer from.

## `student_360` join-order performance bug (2026-09)

Golden-dataset rows 25/28 timed out (~5.2s, against a 5s statement timeout) even after an
unrelated JIT-compilation fix (migration `157c8c534a74`) resolved every other
attendance-touching timeout in the same eval pass. `EXPLAIN ANALYZE` on the live query
found the actual cause: the model's generated SQL joined `student_360` directly to
`teaching_assignments` (`JOIN teaching_assignments ta ON ta.grade_subject_offering_id =
student_360.grade_subject_offering_id`) instead of first narrowing to the teacher's own
students via a `student_id IN (SELECT ... FROM student_subject_enrollments JOIN
teaching_assignments ...)` subquery — the exact idiom every passing template already uses.
The direct-join shape forced Postgres to re-run `student_360`'s internal mastery/attendance
`GROUP BY` aggregation up to 15,552 times instead of once (one `GroupAggregate` node alone
accounted for 4M+ buffer reads) — a join-order cost blowup, unrelated to indexing or JIT.

Fix: added a prompt rule requiring the `IN (...)` subquery form whenever `student_360` is
narrowed to "my students," with the failing question as a worked example. Measured 10/10
compliance across 5 repeated live trials on both originally-failing questions; raw DB
execution time dropped from ~5.2s to 669ms for the same query. Not yet tested against
question phrasings beyond the two that originally failed — if a new "my students" +
`student_360` phrasing times out again, check the generated SQL's join order before
assuming a new root cause.

## Stale prompt content and duplicated guidance (2026-09 cleanup)

Two issues found by re-reading the live `_SYSTEM_PROMPT` text against current code, not
against the module docstring (which had already been kept accurate):

- The curriculum-table-routing bullet claimed "some curriculum tables are still refused
  outright by a later step regardless of role" — true when written, false by the time all
  three deferred-curriculum-table scoping batches landed (see `apply_role_scope.py`'s own
  docstring: "every one of the 15 is classified... that work is done now"). Telling the
  model there's a refusal risk that no longer exists risks the model over-avoiding
  curriculum tables out of a false fear. Corrected to state the real, still-valid reason
  (avoidable join complexity), not a refusal that can't happen any more.
- The FK-authority bullet (the `quiz_attempts.student_subject_enrollment_id` example)
  restated the "never join transitively related columns" principle already stated in the
  bullet above it, and separately restated the DISTINCT/EXISTS guidance already given in
  full elsewhere. Trimmed to just its own unique worked example; the bolted-on, unrelated
  `submitted_at` vs. `scored_at` column-choice rule was split into its own one-line bullet
  rather than staying attached to an FK-tracing paragraph it has nothing to do with.

Both were pure de-duplication/correction — no content removed that wasn't already stated
elsewhere (or, for the stale claim, no longer true) — verified via `_SYSTEM_PROMPT`
length/hash comparison and the full `test_text_to_sql_generate_sql.py` suite passing
unchanged.

## Silently-dropped numeric qualifiers, honest-refusal fix (2026-09)

Golden-dataset row 28 ("What is the average score across all 3 subjects?") — this
project's own documented "Finding 3" gap — had its timeout fixed by the join-order rule
above, but still silently dropped the "3" entirely: `generate_sql` produced `SELECT
AVG(s.mastery_percent) ... WHERE s.student_id IN (...)`, never referencing the number at
all, quietly averaging across however many subjects the teacher actually teaches (2, for
Meera) with no disclosure that "3" didn't match.

**Two designs were considered.** A "specific count" version would have the SQL echo back
both the requested and actual counts as two new columns (`requested_subject_count`,
`actual_subject_count`), with a new `sanity_check` trigger string-matching that naming
convention and `compose_answer` rendering a specific "you teach 2, not 3" message.
Rejected: it depends on the model reliably naming two columns by convention every time —
exactly the kind of LLM-output-naming-convention dependency this project has repeatedly
found unreliable elsewhere — and a naming miss degrades *silently* back to the original
bug (a wrong-looking answer with no flag at all), not to a safe failure.

**Shipped instead: a structural zero-row assertion.** The prompt now teaches the model to
encode a stated exact-count qualifier ("across all 3 subjects", "my 2 sections") as a hard
boolean check ANDed into the query — `AND (SELECT COUNT(DISTINCT <entity>.id) FROM ... WHERE
<same scoping>) = <N>` — so a mismatch makes the query itself return no matching rows,
routing through already-trusted infrastructure instead of new naming-convention-dependent
code. Explicitly distinguished from "top N" phrasing (a `LIMIT` request, not a count
assertion) with a negative example, since conflating the two was the most likely misfire
mode. Measured 10/10 compliance across 5 trials each for the positive case (the failing
question) and the negative case (a real "top 5" question, confirmed unaffected).

**A second, deeper bug surfaced during verification, not anticipated by the plan.** The
structural assertion works — but `AVG()` without `GROUP BY` returns one row with a `NULL`
value when nothing matches, not zero rows, so `sanity_check`'s `zero_rows` trigger can
never catch it. `zero_valued_aggregate` was supposed to cover exactly this gap but
explicitly excluded `NULL`, deferring to `compose_answer._single_scalar_answer`'s "No
`<label>` data is available for that." text — which turned out to only ever affect the
*text*, never `state["confidence"]`. Net effect, confirmed live and reproducibly (5/5
trials): the honest-sounding answer was delivered at "high" confidence, exactly the same
shape as an earlier, independently-found bug (a `student_360` multi-condition query
returning a legitimate `NULL` average at "high" confidence, found during the golden-eval
pass before this fix). Fixed by having `zero_valued_aggregate` fire on `NULL` too (medium
severity, same reasoning as the zero-count case) — a small, separate change to
`sanity_check.py`, not `generate_sql.py`. Confirmed end-to-end: the row-28 question now
reads "medium" confidence with an honest answer and a `zero_valued_aggregate` trigger
recorded, and the full test suite (including two pre-existing unit/integration tests that
had encoded the old, buggy "stays high" behavior as expected — updated, not deleted) passes
clean.
