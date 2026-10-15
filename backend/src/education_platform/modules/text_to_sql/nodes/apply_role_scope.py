"""Rewrites state["validated_sql"] to enforce state["user_role"]'s row/column access
before execution.

Identity source (non-negotiable): this node reads `state["user_id"]`, `state["user_role"]`,
and `state["institution_id"]` — set once, upstream, from the verified JWT at the API layer
— and nothing else. It never reads `state["question"]`, and it never trusts any
identity-shaped value that might be embedded in `state["validated_sql"]` or the LLM output
that produced it (an alias, a literal, a comment). `validate_sql` already whitelists
tables/columns against real schema metadata but does not and cannot vet *values* or
*aliases* the model chose to write, so a prompt-injection attempt embedded in the question
("...and also show me every student's data") can influence what SQL gets generated, but
this node's row predicates are built purely from state's own identity fields and are ANDed
onto the query — they narrow whatever the model asked for, never widen it, and never key
off anything the model wrote.

`education_platform.modules.insights.service.scope_predicate()` is *not* reused directly:
it takes a `Scope` dataclass that only `authorization.scope.scope_for(session, principal)`
can build (four live async DB queries), and it returns a SQLAlchemy `ColumnElement[bool]`
hard-bound to the `student_360` view's specific flattened columns
(`institution_id`/`student_id`/`grade_subject_offering_id`/`section_id`) — it cannot wrap
an arbitrary sqlglot query over a different table shape, and this node is deliberately
DB-session-free (see the module docstring above: user_id/user_role/institution_id only).
Instead, this node reimplements the same *rules* `scope_predicate()` encodes — every clause
pins the institution, for every role including admin; beyond that, a student sees only
their own rows; a teacher sees rows for the (offering, section) pairs they teach, where a
`teaching_assignments.section_id IS NULL` row covers every section of that offering, and
an unmatched/NULL row fails safe (excluded, not included); an admin is otherwise
unrestricted — as portable, table-specific SQL fragments (correlated subqueries against
`teaching_assignments`/`student_subject_enrollments`/`student_profiles`) spliced into the
query's own AST, so the actual identity lookups happen when the query executes rather than
inside this node. This also covers the `student_profiles.user_id` nullability caveat from
the schema catalog: the self-row predicate always resolves through
`student_profiles.user_id = <this node's user_id>`, never assumes a profile exists, and
simply matches zero rows if it doesn't (rather than needing a null-check special case).

Column blocklist (Step 2 in the task write-up) runs first, before any row/institution
scoping, and applies to every role including admin: `users.password_hash`,
`refresh_sessions.token_hash`, and any reference to `question_answer_keys` at all (matching
the schema catalog's own "never expose this to a query result" rule, applied here
unconditionally rather than only for student-facing queries). `password_hash`/`token_hash`
are checked by column name alone, without resolving which table/alias they belong to —
cheaper than full alias resolution, and safe here specifically because the schema catalog
confirms both names are unique to their one table across the whole schema, so any
occurrence, from any alias, is unambiguous and always worth rejecting. A bare `SELECT *`/
`alias.*` produces no `exp.Column` node named `password_hash`/`token_hash` for the check
above to catch by name, so `_find_blocklist_violation` separately refuses a star against
`users`/`refresh_sessions` specifically (`_BLOCKED_COLUMN_TABLES`) — the same gap
`_find_redacted_column_misuse` closes for `_REDACTED_IDENTITY_COLUMNS` tables below, found
while building that mechanism and fixed here too rather than left as a known gap: confirmed
live that the `text_to_sql_reader` DB role already denies `SELECT * FROM users`/
`refresh_sessions` outright at the Postgres grant level (a real, independent backstop —
see migration `c9d0e1f2a3b4`), but this application-layer check shouldn't depend on that
second layer being the only thing holding the line. `validate_sql`'s own column allowlist
check has the analogous gap (it also walks `exp.Column` nodes only) but is out of scope
here — noted so it isn't lost, not fixed in this pass.

Table coverage (fail-closed allowlist, not a blocklist): a live investigation found that a
question routed entirely through `student_subject_enrollments`/`grade_subject_offerings`/
`subjects`/`period_grades` — none of which were in this node's original 5-table sensitive
list — returned real, unscoped, institution-wide data (a school-wide enrollment count, not
one teacher's own students) with no warning. The original design ("scope only these named
tables, everything else passes through untouched") makes every future addition to
`load_schema.REQUIRED_TABLES` exempt by default unless someone remembers to also add it
here. This is inverted: every table `validate_sql` can hand back (i.e. every name in
`REQUIRED_TABLES` other than the always-blocked `question_answer_keys`) must be classified
into exactly one of `STUDENT_SCOPED_TABLES` or `INSTITUTION_SCOPED_TABLES` below, or the
query is refused outright (`_find_unscopable_table_reference`) — a table skips scoping only
because someone deliberately, reviewably placed it on one of these two lists, never because
nobody thought to.

`STUDENT_SCOPED_TABLES` carries rows scoped to an individual student — every SELECT scope
that touches one gets the institution pin *and* the role's self/taught row predicate (a
student sees only their own rows; a teacher sees rows for the (offering, section) pairs
they teach). `INSTITUTION_SCOPED_TABLES` is institution-boundary data that isn't
individually student- or teacher-restricted by nature (a grade name, a subject list, the
institution's own row) but does still belong to exactly one institution and must not leak
across tenants — every SELECT scope that touches one gets the institution pin only, for
every role including admin.

Curriculum-content tables (`topics`, `subtopics`, `learning_outcomes`, `source_materials`,
`source_material_versions`, `source_chunks`, `questions`, `question_versions`,
`question_options`, `question_outcome_tags`, `common_mastery_quizzes`, `quiz_versions`,
`quiz_items`, `quiz_material_bindings`, `quiz_releases`) were deferred here as a known,
documented gap when the fail-closed inversion above first landed: a live check against
this database confirmed they *are* institution-partitioned in practice (a shared top-level
curriculum is the eventual design intent per the module README, but today each
institution's `grade_subject_offerings` hangs its own `topics`, so the two seeded
institutions have disjoint topic/question sets), but each one's institution path is a
multi-hop join chain distinct per table and reviewing all of them was real, separate work.
That work is done now — every one of the 15 is classified into `INSTITUTION_SCOPED_TABLES`
below (none of them has a bare student/teacher self-identity column that reaches
`STUDENT_SCOPED_TABLES`-style row narrowing; `quiz_releases` is the one exception, and it's
a narrower, column-level case — see below). None was a genuinely fresh multi-join
predicate: each table's institution path runs entirely through *other tables on this same
list*, so scoped in dependency order (topics -> subtopics -> {learning_outcomes,
source_materials, questions, common_mastery_quizzes} -> {source_material_versions,
question_versions, quiz_versions} -> {source_chunks, question_options,
question_outcome_tags, quiz_items, quiz_material_bindings, quiz_releases}), every predicate
is exactly one hop from an already-scoped, already-tested parent (`question_outcome_tags`
is the one case where "one hop" means one hop from a *Batch 1* table on one FK side, not
Batch 2 on both — see its own case in `_institution_predicate_sql` for why that doesn't
matter structurally). `_institution_scoped_subquery` below composes that one hop by
calling back into `_institution_predicate_sql` for the parent rather than hand-inlining the
parent's own join chain again — a chain N levels deep becomes N independently-reviewable
one-hop wrappers instead of one hand-assembled N-join expression someone could
mistranscribe. Landed and verified in three dependency-ordered batches, each batch's tests
run and confirmed green against a live database before the next batch's predicates (which
lean on the previous batch's) were written — a bug in an earlier batch's predicate would
otherwise propagate silently into every table stacked on top of it. Three tables
(`question_outcome_tags`, `quiz_items`, `quiz_material_bindings`) have two non-nullable FKs
into two different already-scoped tables; both are checked (ANDed), not just one, even
though the application layer already enforces both sides share an institution at creation
time (`authoring/service.py`) — trusting only the layer above is exactly the
single-point-of-trust posture this project's four-layer defense-in-depth model exists to
avoid (see Finding 4). `common_mastery_quizzes` has two *nullable* FKs (`subtopic_id` xor
`topic_id`, CHECK-enforced), so its predicate is a genuine `OR` of two conditional branches
rather than a plain `IN` — structured so that if neither were ever set (unreachable given
the CHECK constraint, but not relied upon), both branches evaluate false and the row is
denied, not silently passed through. `quiz_releases` also has two FKs, but the second
(`released_by_user_id`, nullable, -> `users`) is an identity column, not a second
curriculum-scoping path — its row gets the ordinary single-hop institution pin (via
`quiz_version_id` only), and the identity column itself is handled separately (see
`_REDACTED_IDENTITY_COLUMNS` below): unlike `teaching_assignments`/`user_roles`/
`refresh_sessions`, hiding the whole row would also hide release status for quizzes a
teacher/student has every reason to see, just because someone else released them, so only
the one column's *value* is redacted to NULL for a non-self reader, not the row.

`query_source` branch (Step 4): a value of `"template"` skips the AST rewrite entirely (a
template's SQL already has the scoping logic — row *and* institution — hand-written into it
by its author) and just records identity in the audit entry. There is no template caller
yet — this is a clean no-op path for a future task, not dead code. The column blocklist
above still runs unconditionally regardless of this branch, per its own "applies ...
regardless of query_source" rule — a template author could still make the same mistake a
generated query could. The fail-closed table-coverage check below is likewise skipped for
templates, on the same reasoning: their scoping (or deliberate lack of it) is hand-reviewed
by the template's own author, not this node.

Self-reference sentinel (`'__CURRENT_USER_ID__'`): `STUDENT_SCOPED_TABLES` never needs
this — apply_role_scope already narrows those to the asking user's own rows silently, so
the model never has to write a self-filter for them at all. `teaching_assignments` used to
need it too (see `_apply_row_scoping`'s docstring for why that changed: a teacher's
"show all teaching assignments" question, with no self-filter to resolve, returned the
whole school's staff roster) — it's now narrowed the same silent way as
`STUDENT_SCOPED_TABLES`, despite still being classified under `INSTITUTION_SCOPED_TABLES`
for the fail-closed table-coverage check. `user_roles`/`refresh_sessions` followed the
same path shortly after — found by the drift-guard extension
(`test_institution_scoped_identity_columns_are_deliberately_reviewed`) rather than
another live leak, and now silently self-narrowed via `_SELF_USER_ID_SCOPED_TABLES`
(both teacher and student, unlike teaching_assignments' teacher-only narrowing, since
both roles have a legitimate "what's my own role/session" reading here). The rest of
`INSTITUTION_SCOPED_TABLES` still only gets the institution pin (deliberately — see that
constant's own docstring), so a question like "what's my email" (users) genuinely needs a
`<owner column> = <the asking user>` filter that only the model can write, and
generate_sql never gives it a real identity value to use. Left unaddressed, the model
either fabricates a placeholder that matches nothing (the observed
failure: a literal like `'Your Name Here'`) or drops the self-filter entirely, over-
returning institution-wide data. generate_sql's prompt instead teaches one fixed, literal
token; `_resolve_current_user_sentinel` finds and replaces it with the real
`state["user_id"]` here, before any row/institution scoping runs — the same node, and
the same identity-sourcing discipline (state only, never the question or model output),
already used for every other identity-keyed predicate in this file. This runs for every
role, not just teacher/student: nothing about self-reference is role-specific, and this
node is not the layer that decides whether a role should be allowed to ask about itself.

If the model needs self-reference but doesn't use the token — a still-fabricated literal,
or a subquery guessing at identity — this is deliberately *not* caught by a dedicated
rejection or pattern-matching check here. Any such check would be pattern-matching an
open-ended set of possible fabrications (the model could invent any string, not just the
one literal observed so far), which is exactly the kind of fragile, easily-incomplete text
heuristic this pipeline already avoids leaning on (see sanity_check.py's own two
heuristic checks, each explicitly labeled as a guess, never a certainty). The residual
failure mode is also a data-quality problem, not a security one: apply_role_scope's own
institution/row predicates below are ANDed on regardless of what the model's own WHERE
clause says, so a still-wrong self-reference can only ever produce a wrong *answer*
(most often an empty one), never a widened one — and sanity_check's existing
`zero_rows`/`zero_valued_aggregate` checks already catch that exact symptom and downgrade
confidence accordingly, with no new mechanism needed.

One shape *is* caught here, though, deliberately: the sentinel used correctly (a real
self-reference, not a fabrication) but bound against a column that structurally can never
match the asking role's identity — observed live as `student_360.student_id =
'__CURRENT_USER_ID__'` for a *teacher* token, which is guaranteed to return nothing no
matter what the real data says (a teacher's user_id is never a real student_id), yet
still reported `high` confidence with a refusal-sounding but misleading empty answer.
Unlike the fabricated-literal case above, this one doesn't require guessing at an
open-ended set of possible mistakes — the finite, already-enumerated set of identity
columns in `STUDENT_SCOPED_TABLES`/`INSTITUTION_SCOPED_TABLES` is exactly the kind of
reviewable allowlist this file already builds elsewhere (see `STUDENT_SCOPED_TABLES`
itself), so `_find_identity_mismatch` checks the binding against it and rejects
(`ROLE_VIOLATION` — see that function's docstring for why this category, not a new one,
and why rejecting outright rather than rewriting the query to a correct self-reference is
the right response) before `_resolve_current_user_sentinel` ever performs the binding.
"""

from __future__ import annotations

from typing import Final

import sqlglot
from sqlglot import exp

from education_platform.modules.text_to_sql.nodes.validate_sql import (
    is_cte_or_derived_table_ref,
)
from education_platform.modules.text_to_sql.state import (
    ROLE_VIOLATION,
    TextToSQLState,
    format_error,
)

# Tables whose rows carry information scoped to an individual student — every SELECT
# scope (outer query, CTE, or nested subquery) that touches one of these gets the
# institution pin *and* a self/taught row predicate ANDed on, keyed off
# state["user_id"]/state["user_role"]. Named and reviewable here rather than inferred
# from, say, "has a student_id column" at runtime.
STUDENT_SCOPED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "student_360",
        "student_profiles",
        "quiz_attempts",
        "attendance_records",
        "student_material_progress",
        "student_subject_enrollments",
        "student_grade_enrollments",
        "attempt_answers",
    }
)

# Tables that belong to exactly one institution but aren't individually student- or
# teacher-restricted by nature (a grade's name, a subject list, the institution's own
# row) — every SELECT scope that touches one of these gets the institution pin only, for
# every role including admin. See the module docstring's "Table coverage" section for why
# this list exists (as the allowlist half of a fail-closed inversion) and why the
# curriculum-content tables below are deliberately on neither list yet.
INSTITUTION_SCOPED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "institutions",
        "users",
        "user_roles",
        "refresh_sessions",
        "grades",
        "subjects",
        "academic_periods",
        "period_grades",
        "sections",
        "grade_subject_offerings",
        "teaching_assignments",
        # Batch 1 of the deferred-curriculum-table scoping project: each reaches
        # institution through the tables above (or each other) in 1-2 hops. See
        # `_institution_predicate_sql`'s cases for these tables and the module
        # docstring's "Curriculum-content tables" section for the dependency order
        # this batching follows.
        "topics",
        "subtopics",
        "learning_outcomes",
        "source_materials",
        "questions",
        "common_mastery_quizzes",
        # Batch 2: each is one hop from a Batch-1 table.
        "source_material_versions",
        "question_versions",
        "quiz_versions",
        # Batch 3 (final batch): each is one hop from a Batch-2 table, except
        # question_outcome_tags, whose second FK lands on a Batch-1 table
        # (learning_outcomes) instead -- see that table's case in
        # _institution_predicate_sql for why this doesn't matter structurally.
        "source_chunks",
        "question_options",
        "question_outcome_tags",
        "quiz_items",
        "quiz_material_bindings",
        "quiz_releases",
    }
)

# Column names that must never appear in a text-to-SQL query, for any role, under any
# circumstances. Table-qualified names aren't needed for the first two: schema_catalog.md
# confirms `password_hash` and `token_hash` each appear on exactly one table in the whole
# schema, so an unqualified name match is unambiguous.
_BLOCKED_COLUMN_NAMES: Final[frozenset[str]] = frozenset({"password_hash", "token_hash"})
_BLOCKED_TABLE_NAMES: Final[frozenset[str]] = frozenset({"question_answer_keys"})

# Which table each `_BLOCKED_COLUMN_NAMES` entry actually lives on -- needed only for the
# `SELECT *`/`alias.*` case below, where there is no explicit `exp.Column` node to check
# by name and the table a star belongs to has to be resolved instead. Confirmed live
# (2026-09-03): the `text_to_sql_reader` DB role (migration c9d0e1f2a3b4) already denies
# `SELECT * FROM users`/`SELECT * FROM refresh_sessions` outright at the Postgres grant
# level (`InsufficientPrivilege`, the whole statement fails, not a partial leak of the
# non-blocked columns) -- a real, independent backstop that already holds today even
# though this application-layer check didn't yet exist. This closes the gap at this layer
# too, so the DB grant stops being the *only* thing standing in the way, matching this
# node's own "two independent layers, never just one" posture (see Finding 4) rather than
# leaving `_find_blocklist_violation` itself dependent on a layer it doesn't control.
_BLOCKED_COLUMN_TABLES: Final[dict[str, str]] = {
    "password_hash": "users",
    "token_hash": "refresh_sessions",
}

# Alias prefix used for identifiers this node injects inside its own correlated
# subqueries. Kept maximally distinctive (unlikely to be naturally generated, and never
# validated/restricted by validate_sql, which only vets *schema* names, not aliases) so a
# question crafted to make generate_sql alias a real table `ta`/`sse`/`sge`/`sp` can never
# shadow — and thereby defeat — the injected predicate's own internal correlation. That
# reasoning alone only covers one direction: a real table *elsewhere* in the query
# colliding with an alias one of the predicate builders below introduces. The mirror
# direction — the table this file is about to scope being *itself* aliased with this
# same reserved prefix — is refused outright by `_find_reserved_alias_collision`, called
# before any alias here is trusted for anything; see that function's docstring for the
# live exploit this closes.
_ALIAS_PREFIX: Final[str] = "__ars_"


# The fixed, literal self-reference token generate_sql's prompt teaches the model to
# write verbatim whenever a question needs to compare a column against the asking
# user's own identity (e.g. `teacher_user_id = '__CURRENT_USER_ID__'`) — see
# generate_sql.py's own docstring/system prompt for why this exists. An ordinary string
# literal, not SQL bind-parameter syntax (`:name`): validate_sql and sqlglot need no
# special-casing to accept it, it round-trips through ordinary parsing exactly like any
# other string value the model might have written, and it's resolved here — the one
# place in this pipeline that already builds every other identity-keyed literal from
# state["user_id"] — via the same find-then-replace mechanism `_apply_row_scoping` uses
# for its own injected predicates.
_CURRENT_USER_SENTINEL: Final[str] = "__CURRENT_USER_ID__"

# Fail-closed allowlist (same posture as STUDENT_SCOPED_TABLES/INSTITUTION_SCOPED_TABLES
# above, not a blocklist of known-bad columns) of which (table, column) pairs the sentinel
# may legitimately be compared against, per asking role. Enumerated by walking every
# identity-shaped column in STUDENT_SCOPED_TABLES/INSTITUTION_SCOPED_TABLES, not guessed
# from the one observed failure (`student_360.student_id` for a teacher) — see
# `_find_identity_mismatch`'s docstring for the full reasoning and the columns considered
# and excluded.
_TEACHER_IDENTITY_TARGETS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("teaching_assignments", "teacher_user_id"),
        ("attendance_records", "recorded_by_user_id"),
    }
)
_STUDENT_IDENTITY_TARGETS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("student_360", "student_id"),
        ("student_profiles", "user_id"),
        ("quiz_attempts", "student_id"),
        ("attendance_records", "student_id"),
        ("student_subject_enrollments", "student_id"),
        ("student_grade_enrollments", "student_id"),
    }
)
# `users.id`/`user_roles.user_id`/`refresh_sessions.user_id` all key off the same raw
# users.id value regardless of which role that user happens to hold — "what's my email"
# is equally valid self-reference for a teacher or a student, unlike a student_id or
# teacher_user_id column, which is only ever the right comparison for one specific role.
_ROLE_AGNOSTIC_IDENTITY_TARGETS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("users", "id"),
        ("user_roles", "user_id"),
        ("refresh_sessions", "user_id"),
    }
)


def _valid_identity_targets_for_role(role: str) -> frozenset[tuple[str, str]]:
    if role == "teacher":
        return _TEACHER_IDENTITY_TARGETS | _ROLE_AGNOSTIC_IDENTITY_TARGETS
    if role == "student":
        return _STUDENT_IDENTITY_TARGETS | _ROLE_AGNOSTIC_IDENTITY_TARGETS
    # admin (and anything else that reaches here) is otherwise unrestricted elsewhere in
    # this file (institution pin only, no row predicate) — extending that same posture
    # here rather than inventing a narrower rule with no basis: allow either identity
    # shape rather than assume admin can never legitimately hold a teaching or student
    # role too.
    return _TEACHER_IDENTITY_TARGETS | _STUDENT_IDENTITY_TARGETS | _ROLE_AGNOSTIC_IDENTITY_TARGETS


def _table_for_alias(tree: exp.Expr, alias_or_name: str) -> str | None:
    """The one real table name `alias_or_name` refers to somewhere in `tree`, or `None`
    if it doesn't resolve to exactly one — ambiguous or absent is treated as "cannot
    verify", never as a violation; `_find_identity_mismatch` only ever rejects on a
    positive, confident match against the allowlist, per its own docstring.
    """
    matches = {
        table.name.lower()
        for table in tree.find_all(exp.Table)
        if (table.alias or table.name).lower() == alias_or_name.lower()
    }
    if len(matches) != 1:
        return None
    return next(iter(matches))


def _find_identity_mismatch(tree: exp.Expr, excluded_aliases: set[str], role: str) -> str | None:
    """A `<column> = '__CURRENT_USER_ID__'` comparison whose column is confidently
    resolved to a table this role's identity could never actually match — e.g. a teacher
    token compared against `student_360.student_id`, which is guaranteed to return
    nothing regardless of real data (a teacher's user_id is never a real student_id).
    Observed live: "what is the average score across all 3 subjects?" produced exactly
    this shape and still returned `high` confidence with a refusal-sounding but
    misleading empty answer.

    Rejects rather than repairs: this node narrows queries, it never guesses intent that
    wasn't expressed — auto-rewriting `student_360.student_id = <teacher>` into a real
    join through `teaching_assignments`/`student_subject_enrollments` would be inventing
    what the question "really meant" instead of what it said, the same overreach every
    other check in this file avoids. A clean rejection lets `honest_refusal` give an
    honest "that question doesn't resolve the way you meant" instead.

    Category: `ROLE_VIOLATION`, not a new one. This is mechanically identical to every
    other rejection this node already raises under that category — same node, same "no
    retry, straight to honest_refusal" routing (a retry can't fix this; the model would
    need a fundamentally different query shape, not a correction it can apply to the one
    it wrote) — and reads as one once you take "the asking user_role isn't allowed to...
    see it" to cover "isn't allowed to claim this identity": a teacher role can no more
    assert "I am this specific student" than it can read another institution's rows. A
    sixth top-level category would need its own honest_refusal message, its own
    audit_log/graph-routing consideration, and its own place in state.py's category
    docstring for a case ROLE_VIOLATION's existing definition already covers — the same
    "reuse an established mechanism over multiplying one-off categories" call this
    pipeline already made for EXECUTION_ERROR/AUDIT_ERROR sharing one honest_refusal
    message rather than inventing bespoke phrasing per failure node.

    Runs on the *original*, pre-substitution tree — this must decide whether the binding
    is valid before `_resolve_current_user_sentinel` performs it, not after (once
    substituted, the literal is just the real UUID and this check can no longer tell it
    apart from any other identity literal already in the query).

    Deliberately conservative, same reasoning as `STUDENT_SCOPED_TABLES`'s fail-closed
    table check but inverted in which direction "can't tell" defaults to: only the
    documented `column = '__CURRENT_USER_ID__'` shape (or reversed operand order) is
    checked; a *qualified* column (`alias.column`) is resolved via `_table_for_alias`,
    and an *unqualified* one (row 28's real shape — `student_id`, no prefix) only when
    its own enclosing `SELECT`'s FROM/JOIN has exactly one table, so there's no ambiguity
    about which table's schema it belongs to (a bare column inside a multi-table join is
    left unchecked rather than guessed at). Callers only ever reach this after the
    fail-closed unscopable-table check has already passed, so every table considered here
    is one this file has actually classified — that ordering matters: a query touching an
    unreviewed table gets refused for *that*, not for a coincidentally-also-true identity
    mismatch on a column nobody has reviewed either way. Checked only against the
    reviewed allowlist in `_valid_identity_targets_for_role` — every other shape (the
    sentinel inside a subquery, compared with an operator other than `=`) is left alone
    rather than guessed at, so this cannot become "a false-positive machine that rejects
    legitimate self-reference queries" over a shape nobody has actually reviewed.
    Column-by-column reasoning behind the allowlist:
    student-identity columns (`student_360.student_id`, `student_profiles.user_id`,
    `quiz_attempts.student_id`, `attendance_records.student_id`,
    `student_subject_enrollments.student_id`, `student_grade_enrollments.student_id`) are
    valid only for a student's own token; teacher-identity columns
    (`teaching_assignments.teacher_user_id`, `attendance_records.recorded_by_user_id`)
    only for a teacher's; `users.id`/`user_roles.user_id`/`refresh_sessions.user_id` key
    off the same raw account identity regardless of role, so both. Every other column in
    `STUDENT_SCOPED_TABLES`/`INSTITUTION_SCOPED_TABLES` — `student_profiles.id` (a
    profile's own primary key, a different ID space from `users.id` entirely),
    `attendance_records`/`student_material_progress`/`attempt_answers`'s non-identity
    columns, and every plain reference-table column (`grades`, `subjects`,
    `academic_periods`, `period_grades`, `sections`, `grade_subject_offerings`,
    `institutions` — none of which carry a user or student identity column at all) — has
    no legitimate self-reference reading for any role and is never in the allowlist.
    """
    valid_targets = _valid_identity_targets_for_role(role)
    for literal in tree.find_all(exp.Literal):
        if not (literal.is_string and literal.this == _CURRENT_USER_SENTINEL):
            continue
        parent = literal.parent
        if not isinstance(parent, exp.EQ):
            continue  # only the documented `column = sentinel` shape is checked
        other = parent.expression if parent.this is literal else parent.this
        if not isinstance(other, exp.Column):
            continue  # can't confidently identify a comparison target; don't reject on it
        if other.table:
            # Qualified (`alias.column` or `table.column`) -- resolve the qualifier.
            if other.table.lower() in excluded_aliases:
                continue  # a CTE/derived alias, not a real table -- can't classify, skip
            table_name = _table_for_alias(tree, other.table)
        else:
            # Unqualified (`column`, no prefix) -- the real shape row 28's live query
            # actually used. Only resolvable without ambiguity when the comparison's own
            # enclosing SELECT has exactly one table in its FROM/JOIN (no join partner's
            # schema to also consider) -- resolved via the enclosing scope, not the whole
            # tree, so a bare `student_id` inside a nested subquery is checked against
            # *that* subquery's own single table, not some unrelated outer one.
            enclosing = other.find_ancestor(exp.Select)
            if enclosing is None:
                continue
            direct_tables = _direct_tables(enclosing)
            if len(direct_tables) != 1:
                continue  # a join -- which table this bare column belongs to is
                # genuinely ambiguous without per-table schema knowledge; don't guess
            table_name = direct_tables[0].name.lower()
        if table_name is None:
            continue
        if (table_name, other.name.lower()) in valid_targets:
            continue
        return (
            f"self-reference token compared against `{table_name}.{other.name}`, which "
            f"does not identify the asking user for role {role!r}"
        )
    return None


def _resolve_current_user_sentinel(tree: exp.Expr, user_id: str) -> None:
    # Materialize the matching literal nodes to a list *before* replacing any of them:
    # `find_all` is a live traversal, and `Literal.replace()` swaps a node in place —
    # exactly the mutate-while-iterating hazard `_apply_row_scoping`'s own docstring
    # warns about, here on exp.Literal instead of exp.Select.
    targets = [
        literal
        for literal in tree.find_all(exp.Literal)
        if literal.is_string and literal.this == _CURRENT_USER_SENTINEL
    ]
    for literal in targets:
        literal.replace(exp.Literal.string(user_id))


def _find_blocklist_violation(tree: exp.Expr) -> str | None:
    for table_node in tree.find_all(exp.Table):
        if table_node.name.lower() in _BLOCKED_TABLE_NAMES:
            return f"table `{table_node.name}` may never be referenced by a text-to-SQL query"
    for col in tree.find_all(exp.Column):
        if col.name.lower() in _BLOCKED_COLUMN_NAMES:
            qualifier = f"{col.table}." if col.table else ""
            return f"column `{qualifier}{col.name}` may never be referenced by a text-to-SQL query"
    # `SELECT *`/`alias.*` produces no explicit `exp.Column` node for the check above to
    # catch by name, even though it would still expand to include a blocked column at
    # execution -- were it not for the independent `text_to_sql_reader` DB-grant backstop
    # (see `_BLOCKED_COLUMN_TABLES`'s own comment). Deliberately narrow, not a blanket
    # star ban: only `users`/`refresh_sessions` (the two tables that actually carry a
    # blocked column) are refused this way, matching this file's consistent
    # narrow-exception-list posture elsewhere (`_ROLE_FORBIDDEN_TABLES`,
    # `_SELF_USER_ID_SCOPED_TABLES`, `_REDACTED_IDENTITY_COLUMNS`) rather than
    # restricting every table's legitimate `SELECT *` usage (e.g. `subjects`, which
    # carries no sensitive column at all) for a risk that only exists on two tables.
    blocked_tables = set(_BLOCKED_COLUMN_TABLES.values())
    for select_node in tree.find_all(exp.Select):
        star_aliases = {
            table_node.alias or table_node.name
            for table_node in _direct_tables(select_node)
            if table_node.name.lower() in blocked_tables
        }
        if not star_aliases:
            continue
        for expr in select_node.expressions:
            is_bare_star = isinstance(expr, exp.Star)
            is_qualified_star = isinstance(expr, exp.Column) and isinstance(expr.this, exp.Star)
            if not (is_bare_star or is_qualified_star):
                continue
            if is_bare_star or expr.table in star_aliases:
                return (
                    f"`{expr.table + '.' if is_qualified_star else ''}*` cannot be "
                    "scoped safely against a table with a blocked column and cannot be "
                    "queried by the text-to-SQL assistant"
                )
    return None


def _cte_and_derived_aliases(tree: exp.Expr) -> set[str]:
    """Names that refer to a CTE or a derived (subquery-in-FROM) table anywhere in the
    query, not a real schema table — so a query that (oddly, but legally) names a CTE
    the same as a sensitive table isn't mistaken for actually touching that table.
    Mirrors validate_sql._local_aliases's reasoning (including that module's `exp.Lateral`
    handling — sqlglot parses `JOIN LATERAL (...) AS alias ON true` as an `exp.Lateral`
    node whose own `.alias` carries the real name, not as a named `exp.Subquery`; see
    that function's docstring for how this was caught live); computed globally since CTE
    names are query-wide.
    Mirrors validate_sql._local_aliases's reasoning; computed globally since CTE names
    are query-wide.

    Aliases on a same-named *base-table* ref inside a non-RECURSIVE CTE body are omitted
    — those name the real table (see `is_cte_or_derived_table_ref`).
    """
    aliases: set[str] = set()
    cte_names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        if cte.alias:
            cte_names.add(cte.alias.lower())
    aliases |= cte_names
    for subq in tree.find_all(exp.Subquery):
        if subq.alias:
            aliases.add(subq.alias.lower())
    for lateral in tree.find_all(exp.Lateral):
        if lateral.alias:
            aliases.add(lateral.alias.lower())
    for table_node in tree.find_all(exp.Table):
        if not table_node.alias:
            continue
        if table_node.name.lower() not in cte_names:
            continue
        if not is_cte_or_derived_table_ref(table_node, cte_names):
            continue  # alias of a real base table inside a same-named CTE body
        aliases.add(table_node.alias.lower())
    return aliases


def _direct_tables(select_node: exp.Select) -> list[exp.Table]:
    """Tables in this select's own FROM/JOIN clauses only — deliberately not a
    tree-wide find_all from this node, which would also pick up tables belonging to a
    nested subquery's own, separate select scope (that subquery is visited on its own
    when the caller walks every exp.Select in the tree).
    """
    tables: list[exp.Table] = []
    from_ = select_node.args.get("from_")
    if isinstance(from_, exp.From) and isinstance(from_.this, exp.Table):
        tables.append(from_.this)
    for join in select_node.args.get("joins") or []:
        if isinstance(join.this, exp.Table):
            tables.append(join.this)
    return tables


def _scoped_table_refs(
    select_node: exp.Select, excluded_aliases: set[str]
) -> list[tuple[str, str]]:
    """(alias-or-name, canonical table name) for each table this select scope directly
    references that is on `STUDENT_SCOPED_TABLES` or `INSTITUTION_SCOPED_TABLES`. Which of
    the two a name belongs to is re-derived by the caller (`_apply_row_scoping`) rather
    than tagged here, since the two tiers dispatch to different predicate builders below.
    """
    refs: list[tuple[str, str]] = []
    for table_node in _direct_tables(select_node):
        name = table_node.name
        if is_cte_or_derived_table_ref(table_node, excluded_aliases):
            continue  # a reference to a CTE/derived table by name, not a real table
        if name.lower() not in STUDENT_SCOPED_TABLES and name.lower() not in (
            INSTITUTION_SCOPED_TABLES
        ):
            continue
        alias = table_node.alias or table_node.name
        refs.append((alias, name.lower()))
    return refs


def _find_unscopable_table_reference(tree: exp.Expr, excluded_aliases: set[str]) -> str | None:
    """A table this query directly references that is on neither `STUDENT_SCOPED_TABLES`
    nor `INSTITUTION_SCOPED_TABLES` — i.e. one `validate_sql` allowed through (it's in
    `load_schema.REQUIRED_TABLES`) but that nobody has yet reviewed and classified for
    role-based scoping here. Fail closed: refuse the query rather than let it pass through
    unscoped, which is exactly the gap this rewrite exists to close. See the module
    docstring's "Table coverage" section for the current list of deferred tables this
    surfaces for (curriculum content, not personal data).
    """
    for select_node in tree.find_all(exp.Select):
        for table_node in _direct_tables(select_node):
            name = table_node.name.lower()
            if is_cte_or_derived_table_ref(table_node, excluded_aliases):
                continue
            if name in STUDENT_SCOPED_TABLES or name in INSTITUTION_SCOPED_TABLES:
                continue
            return (
                f"table `{table_node.name}` is not yet reviewed for role-based scoping "
                "and cannot be queried by the text-to-SQL assistant"
            )
    return None


# Tables where a specific role has no legitimate reading at all — not a row predicate
# that happens to always evaluate to zero rows for that role (which would read, in the
# audit trail and to sanity_check, as an ordinary benign empty answer — see
# sanity_check.py's zero_rows trigger, which can't tell "no rows currently match" apart
# from "this role should never have been let near this table"), but a table that role's
# data model has no concept of at all. `teaching_assignments` for `student` is the one
# entry: a student has no legitimate reading of "my teaching assignment" — see
# `_apply_row_scoping`'s own teaching_assignments case for the incident that surfaced
# this (a teacher's "show all teaching assignments" question returning the whole staff
# roster, because self-filtering had been left to the model). Checked via
# `_find_role_forbidden_table_reference` and refused outright, before execution, so
# honest_refusal produces a real, distinguishable "not something I can answer for your
# role" — same fail-closed shape and same "refuse for the right reason" standard
# `_find_unscopable_table_reference`/`_find_identity_mismatch` already apply, not a new
# heuristic. A role not listed here is unaffected; this is a narrow, explicit,
# individually-reviewed exception list, same posture as everything else in this file.
_ROLE_FORBIDDEN_TABLES: Final[dict[str, frozenset[str]]] = {
    "student": frozenset({"teaching_assignments"}),
}

# INSTITUTION_SCOPED_TABLES tables that ALSO get a bare "user_id = me" self-restriction
# for both teacher and student (unlike teaching_assignments, which restricts only
# teacher and forbids student outright): `user_roles`/`refresh_sessions` have a
# legitimate "what's my own role"/"my own sessions" reading for every role, so there's
# no "no legitimate reading at all" case here to route through
# `_ROLE_FORBIDDEN_TABLES` — both roles are narrowed to their own row instead. Found by
# `test_institution_scoped_identity_columns_are_deliberately_reviewed` (the drift-guard
# extension written after the teaching_assignments incident) rather than by another
# live leak — same identity-column signature (a bare `user_id` column), caught this
# time before it repeated. Applied in `_apply_row_scoping`.
_SELF_USER_ID_SCOPED_TABLES: Final[frozenset[str]] = frozenset({"user_roles", "refresh_sessions"})

# INSTITUTION_SCOPED_TABLES tables where one column -- not the whole row -- needs
# hiding from a non-self reader, mapped to that column's name. Different shape from
# both _ROLE_FORBIDDEN_TABLES (no legitimate reading at all) and
# _SELF_USER_ID_SCOPED_TABLES (the whole row is self-restricted): `quiz_releases`'s
# core content (a release window/status for an already institution-scoped quiz) has
# the same institution-wide legitimate readership as every other curriculum table --
# hiding the whole row the way user_roles/refresh_sessions do would also hide release
# status for quizzes a teacher/student has every reason to see, just because someone
# else released it. Only `released_by_user_id` -- incidental audit metadata about
# who performed the release, not the row's core meaning -- needs protecting, so it's
# redacted to NULL for a non-self reader instead of the row being filtered out. Found
# via the same real ORM introspection as _REVIEWED_IDENTITY_COLUMNS below, during
# Batch 3 review, not a live leak. Applied in `_apply_row_scoping`; `_direct_tables`-
# scoped misuse (WHERE/HAVING/GROUP BY/ORDER BY/JOIN..ON, and `SELECT *`) is refused
# outright by `_find_redacted_column_misuse` below rather than redacted, since a
# filter/sort on the real (pre-redaction) value can still leak the answer through
# which rows come back at all -- redaction alone can't close that oracle channel.
_REDACTED_IDENTITY_COLUMNS: Final[dict[str, str]] = {"quiz_releases": "released_by_user_id"}


def _find_redacted_column_misuse(
    tree: exp.Expr, excluded_aliases: set[str], role: str
) -> str | None:
    """A `_REDACTED_IDENTITY_COLUMNS` column referenced somewhere in a select scope
    other than its own SELECT projection list, or a `SELECT *`/`alias.*` that would
    expand to include one, for a role that column is redacted for (teacher/student;
    admin is never restricted here). `_redact_identity_columns` (called from
    `_apply_row_scoping`) only rewrites the *displayed* value in the projection list --
    it cannot stop a `WHERE released_by_user_id = '<uuid>'` filter from still matching
    against the real, unredacted value underneath and leaking the answer through which
    rows come back at all, the classic oracle side-channel a display-only fix can't
    close. A star is refused for a related but distinct reason: it expands to real
    columns in the database, after this node has already finished rewriting the AST,
    so there is no explicit `exp.Column` node here for `_redact_identity_columns` to
    find and rewrite -- letting it through would silently resurrect the exact
    unredacted-column leak this whole mechanism exists to prevent.

    Column-table resolution here deliberately does NOT follow
    `_find_identity_mismatch`'s "can't confidently resolve -> skip" convention: skipping
    there just means a missed *detection* (the row-scoping predicates elsewhere still
    apply regardless), but skipping here would mean leaving a security-sensitive column
    unchecked and exposed. So an unqualified reference to a `_REDACTED_IDENTITY_COLUMNS`
    column NAME is treated as a match whenever this select scope touches a table that
    column belongs to, even if some other, unrelated table elsewhere could coincidentally
    share that column name -- over-refusing a coincidental name clash is an acceptable
    cost for a column name this distinctive; silently letting a real one through is not.
    Scoped to each select's own direct clauses (not a tree-wide walk), same reasoning as
    `_direct_tables`: a nested subquery's own references are visited separately when the
    caller walks every `exp.Select` in the tree.
    """
    if role not in ("teacher", "student"):
        return None
    for select_node in tree.find_all(exp.Select):
        redacted_aliases = {
            table_node.alias or table_node.name: _REDACTED_IDENTITY_COLUMNS[table_node.name.lower()]
            for table_node in _direct_tables(select_node)
            if not is_cte_or_derived_table_ref(table_node, excluded_aliases)
            and table_node.name.lower() in _REDACTED_IDENTITY_COLUMNS
        }
        if not redacted_aliases:
            continue
        redacted_names = set(redacted_aliases.values())
        for expr in select_node.expressions:
            # Bare `*` is an `exp.Star` directly; `alias.*` is an `exp.Column` whose
            # `this` is an `exp.Star` -- both must be caught here, since neither has an
            # explicit `released_by_user_id` column node for `_redact_identity_columns`
            # to find and rewrite.
            is_bare_star = isinstance(expr, exp.Star)
            is_qualified_star = isinstance(expr, exp.Column) and isinstance(expr.this, exp.Star)
            if not (is_bare_star or is_qualified_star):
                continue
            if is_bare_star or expr.table in redacted_aliases:
                return (
                    f"`{expr.table + '.' if is_qualified_star else ''}*` cannot be "
                    f"scoped safely against a table with a redacted column for role "
                    f"{role!r} and cannot be queried by the text-to-SQL assistant"
                )
        projected = {
            id(col) for proj in select_node.expressions for col in proj.find_all(exp.Column)
        }
        clause_roots = [
            select_node.args.get("where"),
            select_node.args.get("group"),
            select_node.args.get("having"),
            select_node.args.get("order"),
            *(join.args.get("on") for join in select_node.args.get("joins") or []),
        ]
        for root in clause_roots:
            if root is None:
                continue
            for col in root.find_all(exp.Column):
                if isinstance(col.this, exp.Star):
                    if not col.table or col.table in redacted_aliases:
                        return (
                            f"`{col.table or '*'}.*` cannot be scoped safely against a "
                            f"redacted column for role {role!r} and cannot be queried "
                            "by the text-to-SQL assistant"
                        )
                    continue
                if id(col) in projected:
                    continue
                if col.table and col.table not in redacted_aliases:
                    continue  # confidently a different, non-redacted table
                if col.name.lower() not in redacted_names:
                    continue
                return (
                    f"column `{col.table or next(iter(redacted_aliases))}.{col.name}` "
                    f"may only appear in the result list for role {role!r}, not in a "
                    "filter/sort/group clause"
                )
    return None


def _redact_identity_columns(
    select_node: exp.Select, *, alias: str, column_name: str, user_id_literal: str
) -> None:
    """Rewrites any projection in `select_node`'s own SELECT list that reads
    `column_name` on `alias` -- qualified (`alias.column_name`) or, since this table is
    the only one `_find_redacted_column_misuse` would have let reach here unqualified
    without refusing first, unqualified too -- into a CASE that reveals the real value
    only when it matches the asking user, else NULL. Only ever narrows a *value* within
    a row that's already visible (the institution pin already ran, unrelated to this) --
    never which rows are visible, and never touched outside the projection list, since
    `_find_redacted_column_misuse` already refused any WHERE/GROUP BY/HAVING/ORDER
    BY/JOIN..ON use before this runs.

    A bare `alias.column_name` projection (no explicit `AS`) is re-aliased to its
    original column name after the rewrite -- an unaliased `CASE ... END` would
    otherwise come back from Postgres under an auto-generated name (`case`), silently
    renaming the result column a caller (execute_sql, or a human reading the answer)
    would still expect to find by its real name.
    """
    for proj in list(select_node.expressions):
        for col in list(proj.find_all(exp.Column)):
            if col.name.lower() != column_name:
                continue
            if col.table and col.table != alias:
                continue
            replacement: exp.Expr = exp.maybe_parse(
                f"CASE WHEN {alias}.{column_name} = {user_id_literal} "
                f"THEN {alias}.{column_name} ELSE NULL END",
                dialect="postgres",
            )
            if col is proj:
                replacement = exp.alias_(replacement, col.name, quoted=False)
            col.replace(replacement)


def _find_role_forbidden_table_reference(
    tree: exp.Expr, excluded_aliases: set[str], role: str
) -> str | None:
    """A table this query directly references (outer scope or any nested subquery —
    walks every `exp.Select` in the tree, same traversal as
    `_find_unscopable_table_reference`) that `role` has no legitimate reading of at all,
    per `_ROLE_FORBIDDEN_TABLES`. Runs before `_apply_row_scoping`, so the forbidden
    table is never even reached to compute a (structurally always-empty) row predicate
    for it.
    """
    forbidden = _ROLE_FORBIDDEN_TABLES.get(role, frozenset())
    if not forbidden:
        return None
    for select_node in tree.find_all(exp.Select):
        for table_node in _direct_tables(select_node):
            name = table_node.name.lower()
            if is_cte_or_derived_table_ref(table_node, excluded_aliases):
                continue
            if name in forbidden:
                return (
                    f"table `{table_node.name}` has no meaning for role {role!r} and "
                    "cannot be queried by the text-to-SQL assistant"
                )
    return None


def _find_reserved_alias_collision(tree: exp.Expr) -> str | None:
    """A table/subquery/lateral/CTE alias in the incoming query itself uses
    `_ALIAS_PREFIX`'s reserved namespace (case-insensitive).

    `_ALIAS_PREFIX`'s own docstring only reasoned about one direction of collision — a
    real table elsewhere in the query aliased e.g. `ta`/`sse`/`gso` to shadow the
    predicate builders' *internal* correlation aliases. It missed the mirror case: if
    the table this file is about to scope is *itself* aliased with the reserved prefix
    (e.g. `student_360 __ars_ta`), `_taught_offering_exists`/`_taught_period_grade_exists`
    splice that exact alias into a correlated subquery that also introduces its own
    inner table using that same literal alias (`FROM teaching_assignments __ars_ta`).
    Inner scope shadows the outer correlation name, so the spliced-in "outer" column
    reference silently resolves to the newly-introduced inner row instead — collapsing
    the correlation into a self-tautology (`x = x`, always true for any row satisfying
    the rest of the inner EXISTS) rather than raising an error. Confirmed live: a
    teacher aliasing their FROM/JOIN table `__ars_ta` (student_360/attendance_records)
    or `__ars_gso` (student_grade_enrollments) can read every row for their institution
    instead of only their own taught students. Refusing any reserved-prefix alias
    outright — rather than trying to rename around the collision — keeps this fail-
    closed without having to reason about every current and future predicate builder's
    own internal names one at a time.
    """
    prefix = _ALIAS_PREFIX.lower()
    for table_node in tree.find_all(exp.Table):
        if table_node.alias and table_node.alias.lower().startswith(prefix):
            return (
                f"table alias `{table_node.alias}` uses a reserved internal prefix and "
                "cannot be used"
            )
    for subq in tree.find_all(exp.Subquery):
        if subq.alias and subq.alias.lower().startswith(prefix):
            return (
                f"subquery alias `{subq.alias}` uses a reserved internal prefix and cannot be used"
            )
    for lateral in tree.find_all(exp.Lateral):
        if lateral.alias and lateral.alias.lower().startswith(prefix):
            return (
                f"lateral alias `{lateral.alias}` uses a reserved internal prefix and "
                "cannot be used"
            )
    for cte in tree.find_all(exp.CTE):
        if cte.alias and cte.alias.lower().startswith(prefix):
            return f"CTE alias `{cte.alias}` uses a reserved internal prefix and cannot be used"
    return None


def _self_student_subquery(user_id_literal: str) -> str:
    p = _ALIAS_PREFIX
    return f"(SELECT {p}sp.id FROM student_profiles {p}sp WHERE {p}sp.user_id = {user_id_literal})"


def _taught_student_ids_subquery(user_id_literal: str) -> str:
    p = _ALIAS_PREFIX
    return (
        f"(SELECT {p}sse.student_id FROM teaching_assignments {p}ta "
        f"JOIN student_subject_enrollments {p}sse "
        f"ON {p}sse.grade_subject_offering_id = {p}ta.grade_subject_offering_id "
        f"AND {p}sse.status = 'active' "
        f"LEFT JOIN student_grade_enrollments {p}sge ON {p}sge.id = {p}sse.grade_enrollment_id "
        f"WHERE {p}ta.teacher_user_id = {user_id_literal} "
        f"AND {p}ta.status = 'active' "
        f"AND ({p}ta.section_id IS NULL OR {p}ta.section_id = {p}sge.section_id))"
    )


def _taught_offering_exists(user_id_literal: str, offering_col: str, section_col: str) -> str:
    p = _ALIAS_PREFIX
    return (
        f"EXISTS (SELECT 1 FROM teaching_assignments {p}ta "
        f"WHERE {p}ta.teacher_user_id = {user_id_literal} "
        f"AND {p}ta.status = 'active' "
        f"AND {p}ta.grade_subject_offering_id = {offering_col} "
        f"AND ({p}ta.section_id IS NULL OR {p}ta.section_id = {section_col}))"
    )


def _taught_via_enrollment_exists(user_id_literal: str, enrollment_col: str) -> str:
    p = _ALIAS_PREFIX
    return (
        f"EXISTS (SELECT 1 FROM student_subject_enrollments {p}sse "
        f"JOIN teaching_assignments {p}ta "
        f"ON {p}ta.grade_subject_offering_id = {p}sse.grade_subject_offering_id "
        f"AND {p}ta.status = 'active' "
        f"LEFT JOIN student_grade_enrollments {p}sge ON {p}sge.id = {p}sse.grade_enrollment_id "
        f"WHERE {p}sse.id = {enrollment_col} "
        f"AND {p}ta.teacher_user_id = {user_id_literal} "
        f"AND ({p}ta.section_id IS NULL OR {p}ta.section_id = {p}sge.section_id))"
    )


def _taught_period_grade_exists(
    user_id_literal: str, period_grade_col: str, section_col: str
) -> str:
    """A teacher "teaches" a `student_grade_enrollments` row if any of their teaching
    assignments' offering belongs to that row's `period_grade_id` — one level up from
    `_taught_offering_exists`, which matches a specific offering rather than the grade as
    a whole, since `student_grade_enrollments` predates subject enrollment. This is
    deliberately coarser than the subject-level tables: `student_grade_enrollments` has
    no subject dimension of its own, so an all-sections (`section_id IS NULL`) grant for
    *any* subject taught in that grade correctly reaches every student's grade-level row
    in that grade — not only the students of that one subject.
    """
    p = _ALIAS_PREFIX
    return (
        f"EXISTS (SELECT 1 FROM teaching_assignments {p}ta "
        f"JOIN grade_subject_offerings {p}gso ON {p}gso.id = {p}ta.grade_subject_offering_id "
        f"WHERE {p}ta.teacher_user_id = {user_id_literal} "
        f"AND {p}ta.status = 'active' "
        f"AND {p}gso.period_grade_id = {period_grade_col} "
        f"AND ({p}ta.section_id IS NULL OR {p}ta.section_id = {section_col}))"
    )


def _self_predicate_sql(table: str, alias: str, user_id_literal: str) -> str:
    if table == "student_profiles":
        return f"{alias}.user_id = {user_id_literal}"
    if table in (
        "student_360",
        "quiz_attempts",
        "attendance_records",
        "student_subject_enrollments",
        "student_grade_enrollments",
    ):
        return f"{alias}.student_id IN {_self_student_subquery(user_id_literal)}"
    if table == "student_material_progress":
        return (
            f"{alias}.student_subject_enrollment_id IN "
            "(SELECT id FROM student_subject_enrollments WHERE student_id IN "
            f"{_self_student_subquery(user_id_literal)})"
        )
    if table == "attempt_answers":
        return (
            f"{alias}.attempt_id IN (SELECT id FROM quiz_attempts WHERE student_id IN "
            f"{_self_student_subquery(user_id_literal)})"
        )
    raise AssertionError(f"unhandled sensitive table {table!r}")


def _taught_predicate_sql(table: str, alias: str, user_id_literal: str) -> str:
    p = _ALIAS_PREFIX
    if table == "student_profiles":
        return f"{alias}.id IN {_taught_student_ids_subquery(user_id_literal)}"
    if table in ("student_360", "attendance_records"):
        return _taught_offering_exists(
            user_id_literal, f"{alias}.grade_subject_offering_id", f"{alias}.section_id"
        )
    if table in ("quiz_attempts", "student_material_progress"):
        return _taught_via_enrollment_exists(
            user_id_literal, f"{alias}.student_subject_enrollment_id"
        )
    if table == "student_subject_enrollments":
        section_subquery = (
            f"(SELECT {p}sge.section_id FROM student_grade_enrollments {p}sge "
            f"WHERE {p}sge.id = {alias}.grade_enrollment_id)"
        )
        return _taught_offering_exists(
            user_id_literal, f"{alias}.grade_subject_offering_id", section_subquery
        )
    if table == "student_grade_enrollments":
        return _taught_period_grade_exists(
            user_id_literal, f"{alias}.period_grade_id", f"{alias}.section_id"
        )
    if table == "attempt_answers":
        enrollment_subquery = (
            f"(SELECT {p}qa.student_subject_enrollment_id FROM quiz_attempts {p}qa "
            f"WHERE {p}qa.id = {alias}.attempt_id)"
        )
        return _taught_via_enrollment_exists(user_id_literal, enrollment_subquery)
    raise AssertionError(f"unhandled sensitive table {table!r}")


def _row_predicate_sql(table: str, alias: str, role: str, user_id_literal: str) -> str:
    if role == "teacher":
        return _taught_predicate_sql(table, alias, user_id_literal)
    if role == "student":
        return _self_predicate_sql(table, alias, user_id_literal)
    # Any role other than "teacher"/"student" that reaches here (not "admin" — callers
    # apply institution scoping but skip this role predicate entirely for admin) has no
    # defined grant. Fail safe: no rows.
    return "FALSE"


def _institution_predicate_sql(table: str, alias: str, institution_id_literal: str) -> str:
    """Pins `alias` (a reference to `table`) to the caller's own institution. Applied for
    every role, admin included — mirrors scope_predicate()'s `institution` clause, which
    is ANDed on before the unrestricted-for-admin short-circuit, never after it. Covers
    both `STUDENT_SCOPED_TABLES` (paired with a self/taught row predicate) and
    `INSTITUTION_SCOPED_TABLES` (this predicate alone).
    """
    p = _ALIAS_PREFIX
    if table in ("student_360", "student_profiles"):
        return f"{alias}.institution_id = {institution_id_literal}"
    if table in (
        "quiz_attempts",
        "attendance_records",
        "student_subject_enrollments",
        "student_grade_enrollments",
    ):
        return (
            f"{alias}.student_id IN "
            f"(SELECT id FROM student_profiles WHERE institution_id = {institution_id_literal})"
        )
    if table == "student_material_progress":
        return (
            f"{alias}.student_subject_enrollment_id IN "
            f"(SELECT {p}sse.id FROM student_subject_enrollments {p}sse "
            f"JOIN student_profiles {p}sp ON {p}sp.id = {p}sse.student_id "
            f"WHERE {p}sp.institution_id = {institution_id_literal})"
        )
    if table == "attempt_answers":
        return (
            f"{alias}.attempt_id IN (SELECT {p}qa.id FROM quiz_attempts {p}qa "
            f"WHERE {p}qa.student_id IN "
            f"(SELECT id FROM student_profiles WHERE institution_id = {institution_id_literal}))"
        )
    if table == "institutions":
        return f"{alias}.id = {institution_id_literal}"
    if table == "users":
        return f"{alias}.institution_id = {institution_id_literal}"
    if table in ("user_roles", "refresh_sessions"):
        return (
            f"{alias}.user_id IN "
            f"(SELECT id FROM users WHERE institution_id = {institution_id_literal})"
        )
    if table in ("grades", "subjects", "academic_periods"):
        return f"{alias}.institution_id = {institution_id_literal}"
    if table == "period_grades":
        return (
            f"{alias}.academic_period_id IN "
            f"(SELECT id FROM academic_periods WHERE institution_id = {institution_id_literal})"
        )
    if table in ("sections", "grade_subject_offerings"):
        return (
            f"{alias}.period_grade_id IN "
            f"(SELECT {p}pg.id FROM period_grades {p}pg "
            f"JOIN academic_periods {p}ap ON {p}ap.id = {p}pg.academic_period_id "
            f"WHERE {p}ap.institution_id = {institution_id_literal})"
        )
    if table == "teaching_assignments":
        return (
            f"{alias}.academic_period_id IN "
            f"(SELECT id FROM academic_periods WHERE institution_id = {institution_id_literal})"
        )
    if table == "topics":
        return (
            f"{alias}.grade_subject_offering_id IN "
            f"{_institution_scoped_subquery('grade_subject_offerings', institution_id_literal)}"
        )
    if table == "subtopics":
        return (
            f"{alias}.topic_id IN {_institution_scoped_subquery('topics', institution_id_literal)}"
        )
    if table in ("learning_outcomes", "source_materials", "questions"):
        # All three hang directly off subtopics (one FK apiece: subtopic_id) --
        # same one-hop shape, deliberately not merged with the subtopics/topics cases
        # above since the FK column happens to share a name only by coincidence of
        # schema design, not because these tables are otherwise alike.
        return (
            f"{alias}.subtopic_id IN "
            f"{_institution_scoped_subquery('subtopics', institution_id_literal)}"
        )
    if table == "common_mastery_quizzes":
        # Exactly one of subtopic_id/topic_id is set (CHECK-enforced) -- an OR of two
        # conditional branches, not a plain IN. If neither were ever set (unreachable
        # given the CHECK constraint, but not relied upon: see the module docstring),
        # both IS NOT NULL guards fail, the OR is false, and the row is denied rather
        # than silently passed through.
        return (
            f"(({alias}.subtopic_id IS NOT NULL AND {alias}.subtopic_id IN "
            f"{_institution_scoped_subquery('subtopics', institution_id_literal)}) "
            f"OR ({alias}.topic_id IS NOT NULL AND {alias}.topic_id IN "
            f"{_institution_scoped_subquery('topics', institution_id_literal)}))"
        )
    if table == "source_material_versions":
        return (
            f"{alias}.source_material_id IN "
            f"{_institution_scoped_subquery('source_materials', institution_id_literal)}"
        )
    if table == "question_versions":
        return (
            f"{alias}.question_id IN "
            f"{_institution_scoped_subquery('questions', institution_id_literal)}"
        )
    if table == "quiz_versions":
        return (
            f"{alias}.quiz_id IN "
            f"{_institution_scoped_subquery('common_mastery_quizzes', institution_id_literal)}"
        )
    if table == "source_chunks":
        return (
            f"{alias}.source_material_version_id IN "
            f"{_institution_scoped_subquery('source_material_versions', institution_id_literal)}"
        )
    if table == "question_options":
        return (
            f"{alias}.question_version_id IN "
            f"{_institution_scoped_subquery('question_versions', institution_id_literal)}"
        )
    if table == "quiz_items":
        # Two non-nullable FKs into two different already-scoped Batch-2 tables; both
        # are checked (ANDed), not just one, even though the application layer already
        # enforces both sides share an institution at creation time
        # (authoring/service.py) -- trusting only that layer is exactly the
        # single-point-of-trust posture this project's four-layer defense-in-depth
        # model exists to avoid (see Finding 4).
        return (
            f"{alias}.quiz_version_id IN "
            f"{_institution_scoped_subquery('quiz_versions', institution_id_literal)} "
            f"AND {alias}.question_version_id IN "
            f"{_institution_scoped_subquery('question_versions', institution_id_literal)}"
        )
    if table == "quiz_material_bindings":
        # Same both-sides-ANDed reasoning as quiz_items above.
        return (
            f"{alias}.quiz_version_id IN "
            f"{_institution_scoped_subquery('quiz_versions', institution_id_literal)} "
            f"AND {alias}.source_material_version_id IN "
            f"{_institution_scoped_subquery('source_material_versions', institution_id_literal)}"
        )
    if table == "question_outcome_tags":
        # Same both-sides-ANDed reasoning as quiz_items/quiz_material_bindings above,
        # but its second FK (learning_outcome_id) lands on a Batch 1 table
        # (learning_outcomes), not a Batch 2 one like the other two dual-FK tables --
        # _institution_scoped_subquery doesn't care which batch introduced a parent's
        # predicate, only that one already exists, so the composition is identical.
        return (
            f"{alias}.question_version_id IN "
            f"{_institution_scoped_subquery('question_versions', institution_id_literal)} "
            f"AND {alias}.learning_outcome_id IN "
            f"{_institution_scoped_subquery('learning_outcomes', institution_id_literal)}"
        )
    if table == "quiz_releases":
        # released_by_user_id is a second FK (-> users) but it's an identity column,
        # not a second curriculum-scoping path -- see _REDACTED_IDENTITY_COLUMNS and
        # _apply_row_scoping's redaction branch for how its *value* is handled. This
        # predicate only pins the row via the one FK that's actually about curriculum
        # scoping.
        return (
            f"{alias}.quiz_version_id IN "
            f"{_institution_scoped_subquery('quiz_versions', institution_id_literal)}"
        )
    raise AssertionError(f"unhandled scoped table {table!r}")


def _institution_scoped_subquery(table: str, institution_id_literal: str) -> str:
    """`(SELECT <alias>.id FROM <table> <alias> WHERE <table's own institution predicate>)`
    for a `table` already in `INSTITUTION_SCOPED_TABLES` -- used to build a *new* table's
    institution predicate as "my parent FK's value is in this set of already-correctly-
    scoped parent ids", one hop at a time. Calls back into `_institution_predicate_sql`
    for `table` rather than hand-inlining that table's own join chain again here: a chain
    N levels deep becomes N independently-reviewable one-hop wrappers instead of one
    hand-assembled N-join expression someone could mistranscribe partway through (see the
    module docstring's "Curriculum-content tables" section). Aliased by the parent table's
    own name (via `_ALIAS_PREFIX`) so a query with several of these nested at different
    depths stays readable; safe to reuse the same alias string at unrelated nesting depths
    since each is its own independently-scoped subquery, never correlated to a sibling.
    """
    alias = f"{_ALIAS_PREFIX}{table}"
    predicate = _institution_predicate_sql(table, alias, institution_id_literal)
    return f"(SELECT {alias}.id FROM {table} {alias} WHERE {predicate})"


def _apply_row_scoping(
    tree: exp.Expr,
    *,
    excluded_aliases: set[str],
    role: str,
    user_id_literal: str,
    institution_id_literal: str,
) -> None:
    # Materialize the select scopes (and each one's scoped-table refs) to a plain list
    # *before* mutating anything: `.where()` below parses and splices in a new subquery,
    # which is itself an exp.Select containing further scoped-table references (an
    # INSTITUTION_SCOPED_TABLES predicate can reference another INSTITUTION_SCOPED_TABLES
    # table, e.g. grade_subject_offerings -> period_grades -> academic_periods). Since
    # `find_all` is a live traversal of the tree, iterating it directly here would walk
    # into those newly-injected subqueries too and try to scope them all over again —
    # each pass injecting another nested subquery for `find_all` to discover, which never
    # terminates. Injected subqueries are this function's own, already-correct-by-
    # construction SQL; they must never be re-scanned by the mechanism that wrote them.
    work = [
        (select_node, refs)
        for select_node in tree.find_all(exp.Select)
        if (refs := _scoped_table_refs(select_node, excluded_aliases))
    ]
    for select_node, refs in work:
        for alias, table in refs:
            # .where() ANDs onto any existing WHERE by default (append=True) — this can
            # only narrow what the query already asks for, never replace or OR into it.
            institution_sql = _institution_predicate_sql(table, alias, institution_id_literal)
            select_node.where(institution_sql, copy=False, dialect="postgres")
            if table == "teaching_assignments":
                # Narrow exception to the "INSTITUTION_SCOPED_TABLES gets the institution
                # pin only" rule below: observed live, a teacher's "show all teaching
                # assignments" question returned the whole school's staff roster (every
                # teacher's own assignments), not just theirs, because this table's
                # self-filtering had been left to the model writing the
                # '__CURRENT_USER_ID__' sentinel itself (see the module docstring's
                # "Self-reference sentinel" section) rather than enforced here — and a
                # question that never asks "what do I teach" has no reason to write one.
                # "Who teaches what" is staff-roster data, not individual-student data, so
                # this still isn't promoted to STUDENT_SCOPED_TABLES (that set's own
                # self/taught machinery is about reaching a teacher's *students*, not a
                # teacher's own row) — instead, structurally enforced here, same posture as
                # every other row predicate in this file: a teacher sees only their own
                # assignment rows, and admin stays unrestricted beyond the institution
                # pin, mirroring Scope.unrestricted. The student branch below is a
                # defense-in-depth backstop, not the primary mechanism: a student query
                # is refused outright by `_find_role_forbidden_table_reference` before
                # this function ever runs, specifically so the audit trail shows a real
                # ROLE_VIOLATION refusal rather than an indistinguishable-from-benign
                # empty result — this FALSE only matters if that upfront check is ever
                # bypassed or a future bug reaches here anyway.
                if role == "teacher":
                    select_node.where(
                        f"{alias}.teacher_user_id = {user_id_literal}",
                        copy=False,
                        dialect="postgres",
                    )
                elif role == "student":
                    select_node.where("FALSE", copy=False, dialect="postgres")
                continue
            if table in _SELF_USER_ID_SCOPED_TABLES:
                # Same identity-column signature as teaching_assignments above, caught
                # by the drift-guard extension this time (test_institution_scoped_
                # identity_columns_are_deliberately_reviewed) rather than by another
                # live leak. Unlike teaching_assignments, both teacher and student have
                # a legitimate self-reading here ("what's my role", "my own sessions"),
                # so both get self-restricted rather than one being forbidden outright
                # — there's no "no legitimate reading at all" case to route through
                # _ROLE_FORBIDDEN_TABLES for either role. Admin stays unrestricted
                # beyond the institution pin, same as everywhere else in this file.
                if role in ("teacher", "student"):
                    select_node.where(
                        f"{alias}.user_id = {user_id_literal}", copy=False, dialect="postgres"
                    )
                continue
            if table in _REDACTED_IDENTITY_COLUMNS and role in ("teacher", "student"):
                # Column-level, not row-level: the institution predicate already
                # applied above still governs which rows are visible at all; this only
                # narrows the one identity-shaped column's *value* within those rows.
                # No `continue` -- falls through to the ordinary INSTITUTION_SCOPED_
                # TABLES handling below, which adds no further row predicate for a
                # table that isn't in STUDENT_SCOPED_TABLES, exactly as intended.
                _redact_identity_columns(
                    select_node,
                    alias=alias,
                    column_name=_REDACTED_IDENTITY_COLUMNS[table],
                    user_id_literal=user_id_literal,
                )
            if table not in STUDENT_SCOPED_TABLES:
                continue  # INSTITUTION_SCOPED_TABLES gets the institution pin only
            if role == "admin":
                continue  # unrestricted beyond institution, mirrors Scope.unrestricted
            role_sql = _row_predicate_sql(table, alias, role, user_id_literal)
            select_node.where(role_sql, copy=False, dialect="postgres")


async def apply_role_scope(state: TextToSQLState) -> TextToSQLState:
    user_id = state["user_id"]
    role = state["user_role"]
    institution_id = state["institution_id"]
    sql = state["validated_sql"]
    if not sql:
        # Graph invariant: this node is only reached via validate_sql's "valid" edge,
        # which guarantees a non-empty validated_sql. Fail loudly rather than pass
        # through silently if that invariant is ever violated (e.g. direct node call).
        return {
            **state,
            "validated_sql": None,
            "error": format_error(ROLE_VIOLATION, "apply_role_scope: no validated_sql to scope"),
        }

    # validate_sql already parsed and accepted this exact string; a parse failure here
    # would mean validate_sql's own output is broken, not a role-scoping concern — let
    # that raise rather than misreport it as a ROLE_VIOLATION.
    tree: exp.Expr = sqlglot.parse_one(sql, read="postgres")

    violation = _find_blocklist_violation(tree)
    if violation is not None:
        return {
            **state,
            "validated_sql": None,
            "error": format_error(ROLE_VIOLATION, violation),
        }

    if state.get("query_source") == "template":
        return {
            **state,
            "audit_entry": {
                **(state.get("audit_entry") or {}),
                "role_scope_applied": "template_builtin",
                "scoped_by_user_id": user_id,
            },
        }

    # Runs before any alias is trusted for anything else below (excluded_aliases,
    # _scoped_table_refs, the predicate builders' own splicing) — see
    # _find_reserved_alias_collision's docstring for exactly why a reserved-prefix
    # alias must be refused outright rather than allowed to reach the rewrite at all.
    reserved_alias_collision = _find_reserved_alias_collision(tree)
    if reserved_alias_collision is not None:
        return {
            **state,
            "validated_sql": None,
            "error": format_error(ROLE_VIOLATION, reserved_alias_collision),
        }

    excluded_aliases = _cte_and_derived_aliases(tree)

    # Runs before the identity-mismatch check below on purpose: that check only means
    # anything against a table this file has actually reviewed and classified (its
    # allowlist is built from STUDENT_SCOPED_TABLES/INSTITUTION_SCOPED_TABLES). A query
    # touching an unreviewed/deferred table should be refused *for that* — "this table
    # isn't reviewed at all" — not for a coincidentally-also-true identity mismatch on a
    # column nobody has classified either way; same "don't refuse for the wrong reason"
    # standard the Fix 5 eval-set correction applied to rows 39/40's refusals.
    unscopable = _find_unscopable_table_reference(tree, excluded_aliases)
    if unscopable is not None:
        return {
            **state,
            "validated_sql": None,
            "error": format_error(ROLE_VIOLATION, unscopable),
        }

    # Same "refuse for the right reason" ordering as the unscopable-table check above:
    # a table this role's data model has no concept of at all (_ROLE_FORBIDDEN_TABLES)
    # should be refused *for that*, not for a coincidentally-also-true identity mismatch.
    role_forbidden = _find_role_forbidden_table_reference(tree, excluded_aliases, role)
    if role_forbidden is not None:
        return {
            **state,
            "validated_sql": None,
            "error": format_error(ROLE_VIOLATION, role_forbidden),
        }

    # Same "refuse for the right reason" ordering as the two checks above: a
    # WHERE/GROUP BY/HAVING/ORDER BY/JOIN..ON use of a redacted column (or a star that
    # would expand to include one) should be refused *for that* -- the display-only
    # redaction below can't close that oracle channel, so this must run first.
    redacted_misuse = _find_redacted_column_misuse(tree, excluded_aliases, role)
    if redacted_misuse is not None:
        return {
            **state,
            "validated_sql": None,
            "error": format_error(ROLE_VIOLATION, redacted_misuse),
        }

    # Must run before _resolve_current_user_sentinel: once the sentinel is substituted
    # for the real user_id, this can no longer tell "the model's own self-reference
    # comparison" apart from any other identity literal already in the query.
    mismatch = _find_identity_mismatch(tree, excluded_aliases, role)
    if mismatch is not None:
        return {
            **state,
            "validated_sql": None,
            "error": format_error(ROLE_VIOLATION, mismatch),
        }

    _resolve_current_user_sentinel(tree, user_id)

    user_id_literal = exp.Literal.string(user_id).sql(dialect="postgres")
    institution_id_literal = exp.Literal.string(institution_id).sql(dialect="postgres")
    _apply_row_scoping(
        tree,
        excluded_aliases=excluded_aliases,
        role=role,
        user_id_literal=user_id_literal,
        institution_id_literal=institution_id_literal,
    )

    return {
        **state,
        "validated_sql": tree.sql(dialect="postgres"),
        "audit_entry": {
            **(state.get("audit_entry") or {}),
            "role_scope_applied": "rewritten",
            "scoped_by_user_id": user_id,
        },
    }
