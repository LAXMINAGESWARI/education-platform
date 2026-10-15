"""Disable JIT compilation for the text_to_sql_reader role.

**The problem, measured live.** `average_attendance_rate` (and every other template/
free-form query that joins several `STUDENT_SCOPED_TABLES`/`INSTITUTION_SCOPED_TABLES`
tables in one statement) was timing out against the real, production-scale dataset —
`asyncpg.exceptions.QueryCanceledError: canceling statement due to statement timeout` —
despite `attendance_records` holding only 12,000 rows, nowhere near enough data to
justify a 5-second query on its own. `EXPLAIN (ANALYZE, BUFFERS)` run live as
`text_to_sql_reader` with real RLS session variables set showed why: of a 4,315 ms total
execution time, 3,374 ms (78%) was Postgres's own JIT compilation phase (652 separate JIT
functions), not row processing. Re-running the identical query with `SET LOCAL jit = off`
dropped execution to 382 ms — an 11x speedup, with byte-for-byte identical rows returned
(JIT changes how an expression is evaluated, never what it evaluates to).

**Why this role's queries trip Postgres's JIT heuristic specifically.** Every query this
role runs stacks two independent, by-design-redundant scoping layers on the same tables:
`apply_role_scope`'s AST-injected `EXISTS(__ars_...)` predicates (Layer 3) and each
table's own RLS policy (Layer 5, its own `SECURITY DEFINER` function calls and correlated
subqueries) — see migrations `e1f2a3b4c5d6`/`f7a8b9c0d1e2`. A query joining several scoped
tables therefore produces an unusually deep, heavily-nested plan (14+ SubPlans in the
`average_attendance_rate` case) whose estimated cost crosses Postgres's `jit_above_cost`
threshold, even though the actual table sizes here are small. JIT compilation cost scales
with plan complexity, not row count, which is exactly the mismatch that made this
performant against the tiny pytest fixture (a shallow plan, JIT never triggers) and slow
against real seeded data (same row counts, but every table this role's queries touch now
carries real rows across both scoping layers, deepening the plan).

**Why this is safe.** JIT is purely a query-execution optimization — it decides whether
expression evaluation is compiled to native code or run through Postgres's normal
interpreter; it has no bearing on which rows RLS, `apply_role_scope`, or the GRANT-level
column/table restrictions allow through. Disabling it changes timing only, never the
result set or the access boundary — confirmed directly by the identical-rows comparison
above. Scoped to this one role (`ALTER ROLE ... SET jit = off`, not a database- or
cluster-wide `SET`) so it affects only the deeply-nested, RLS-heavy, capped-at-500-rows
queries this pipeline runs — never the application's own `education` role or any other
connection to this database, which may still benefit from JIT on genuinely large queries.

Revision ID: 157c8c534a74
Revises: a352c5d4a076
Create Date: 2026-09-15 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str | Sequence[str] | None = "157c8c534a74"
down_revision: str | Sequence[str] | None = "a352c5d4a076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = "text_to_sql_reader"


def upgrade() -> None:
    op.execute(f"ALTER ROLE {_ROLE} SET jit = off")


def downgrade() -> None:
    op.execute(f"ALTER ROLE {_ROLE} RESET jit")
