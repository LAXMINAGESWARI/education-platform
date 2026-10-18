"""Executes state["validated_sql"] — already validated (Task 5) and role/institution
scoped (Task 6) — against Postgres, and populates state["query_result"]/
state["result_row_count"].

Connects as the restricted `text_to_sql_reader` DB role (see
`db.session.get_text_to_sql_engine` / migration `c9d0e1f2a3b4`), not the application's own
`education` role — SELECT-only, granted on exactly `load_schema.REQUIRED_TABLES`, with
`users.password_hash`/`refresh_sessions.token_hash` excluded at the column-privilege level
and `question_answer_keys` excluded entirely. This is deliberate defense-in-depth,
independent of Task 5's structural whitelist and Task 6's row/column/institution scoping:
if either of those has an undiscovered bug, a query that reaches this node still cannot
read a column or table it wasn't granted, or write anything at all, regardless of what SQL
text made it this far.

Four more layers enforced here, at the database connection itself rather than in Python:

* **Read-only transaction** (`SET TRANSACTION READ ONLY`) — belt-and-suspenders on top of
  `text_to_sql_reader`'s missing INSERT/UPDATE/DELETE grants: even if a future migration or
  manual `GRANT` ever loosened that role's privileges, or a bug elsewhere in this pipeline
  let a mutating statement reach here, Postgres itself refuses to run it in this
  transaction, independent of role privileges entirely.
* **Row-Level Security identity propagation** (migration `e1f2a3b4c5d6`) — the 5th
  defense-in-depth layer, and the one that still holds if `apply_role_scope` (Task 6) has
  a bug nobody's found yet: RLS policies on every scoped table re-derive the same row-
  visibility rule that node already computed, enforced inside Postgres itself regardless
  of what SQL text arrives. Those policies read three session variables —
  `app.current_user_id`/`app.current_user_role`/`app.current_institution_id` — that only
  this node sets, via `set_config(..., true)` (the parameterized equivalent of
  `SET LOCAL`, chosen over string-interpolating a `SET LOCAL` statement so these values
  go through the driver's normal bind-parameter path like any other untrusted-shaped
  string, even though they're already sourced from the same verified `state["user_id"]`/
  `state["user_role"]`/`state["institution_id"]` fields every other node in this pipeline
  trusts — never from the question or LLM output). The `true` third argument is what
  makes each `set_config` call transaction-scoped, identical to `SET LOCAL`: it can never
  leak onto a pooled connection some other session picks up next. If this node were ever
  skipped or reached with these fields unset, every RLS policy's `current_setting(name,
  true)` read comes back NULL, which satisfies no equality check — the policies deny by
  default (zero rows), not "fall back to unscoped," so a bug here fails toward less
  access, not more.
* **Statement timeout** (`STATEMENT_TIMEOUT_MS`) — set via `SET LOCAL` inside the same
  transaction as the query, so a malformed or unexpectedly expensive query (a bad join
  fan-out, a missing index) is cancelled by Postgres itself rather than hanging this node,
  the event loop, or degrading the database for other users. `SET LOCAL` (not plain `SET`)
  matters: it only lasts for the current transaction, so it can never leak onto a pooled
  connection some other query picks up next.
* **Zero-result probes** (`state["zero_probe_sql"]`) — only when the main query returned an
  empty or all-zero result, and in the same transaction (same RLS session variables, still
  read-only, still under the statement timeout), run the small reference queries the router
  attached (`visible_students`, `taught_subjects`; both are existing governed templates) and
  record their answers in `state["zero_probe_results"]`. They exist so that a "none" can be
  told apart from "the role-scoping silently excluded everything": sanity_check and
  compose_answer only trust a zero when a probe shows the teacher's scope can see students,
  or explains it (the subject is not one they teach). A probe that fails is run inside a
  SAVEPOINT and simply recorded as missing — it can never discard the main result.
* **Row cap, belt-and-suspenders** (`ROW_CAP` — imported as
  `validate_sql.DEFAULT_ROW_LIMIT`, not a second literal `500`, so the two can't silently
  drift apart) — validate_sql already injects `LIMIT 500` onto the root SELECT if the
  model's SQL didn't have one, and that structurally bounds the outermost result set, not
  just some subquery's. This node still doesn't fully trust that: if the row count ever
  exceeds `ROW_CAP` anyway (a bug in that injection, an apply_role_scope rewrite that
  somehow dropped the LIMIT, ...), the rows are truncated to `ROW_CAP` rather than either
  trusted as-is or turned into a hard failure — truncating is the direction that can never
  return more than intended, and a purely defensive over-count shouldn't turn what is
  otherwise a fine answer into a user-facing refusal. Logged as a warning either way, since
  it means validate_sql's own guarantee didn't hold.

Failure handling: `state["user_id"]`/`state["user_role"]`/`state["institution_id"]` are
subscripted directly (not `.get()`) since a missing value must never silently fall
through to an unscoped query — but that means a genuinely missing field raises
`KeyError`, which is guarded for explicitly right at the top of this function (same
fail-closed shape as audit_log.py's own guard on the same fields) rather than left to
propagate uncaught if the graph's identity-always-populated invariant is ever violated.

Any `SQLAlchemyError` here (timeout, connection failure, an unexpected
constraint violation) is a genuine execution failure, not a correctness or authorization
one — the SQL was already proven valid and authorized by the time it reaches this node.
`state["error"]` is always the same generic, user-safe text; the real exception (which can
carry schema/constraint names or fragments of the query) goes to `logger` and to
`state["audit_entry"]` only, never into the user-facing error string. `state["retry_count"]`
is never touched — generate_sql can't fix a database-level failure by rewriting SQL, so
this routes to honest_refusal (see graph.py's `_route_after_execute`), never back into the
retry loop.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from education_platform.db.session import get_text_to_sql_session_factory
from education_platform.modules.text_to_sql.nodes.validate_sql import (
    DEFAULT_ROW_LIMIT as ROW_CAP,
)
from education_platform.modules.text_to_sql.state import (
    EXECUTION_ERROR,
    TextToSQLState,
    format_error,
)

logger = logging.getLogger(__name__)

# ROW_CAP is a rename-on-import (from validate_sql.DEFAULT_ROW_LIMIT), which mypy's
# implicit-reexport check (implied by `strict = true`) doesn't treat as re-exported by
# default — sanity_check.py imports it from here deliberately (see its own docstring:
# "the cap that was actually applied to the executed result"), so it needs to be listed
# explicitly.
__all__ = ["execute_sql", "is_zero_result", "ROW_CAP"]

# Sized from measurement, not guessed: the free-form queries that read `student_360` as the
# restricted role take ~3-4 s *on their own* (the same SQL runs in ~6 ms as the table owner —
# the cost is row-level security re-evaluating its per-row institution/teaching-assignment
# checks over every attendance and quiz row the view aggregates). At 5 s they sat on the edge
# and timed out as soon as a few requests overlapped ("Something went wrong on our end" for
# "what's the average attendance of my students?"). 15 s leaves ~4x headroom while staying far
# below the endpoint's 60 s graph budget; the statement is still read-only and cancelled by
# Postgres itself. The proper fix is cheaper RLS policies (a separate, security-reviewed
# migration), at which point this can come back down.
STATEMENT_TIMEOUT_MS: Final[int] = 15_000

_GENERIC_FAILURE_MESSAGE: Final[str] = "the query could not be run"

# current_user_id/current_institution_id are always uuid columns on the scoped tables they're
# compared against (teacher_user_id, institution_id, ...). Without an explicit type here,
# asyncpg sends a plain Python str as $N::VARCHAR, and Postgres has no `uuid = character
# varying` operator for a bound (as opposed to literal) parameter -- confirmed live: every
# template filtering on :current_user_id/:current_institution_id fails with
# asyncpg.exceptions.UndefinedFunctionError without it.
_UUID_PARAMS: Final[frozenset[str]] = frozenset({"current_user_id", "current_institution_id"})


def _bind_statement(sql: str, parameters: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """The executable statement for `sql` plus exactly the parameters it references."""
    unbound_statement = text(sql)
    required_parameters = {
        name: parameters[name] for name in unbound_statement._bindparams if name in parameters
    }
    statement = unbound_statement.bindparams(
        *(
            bindparam(name, value=value, type_=Uuid() if name in _UUID_PARAMS else None)
            for name, value in required_parameters.items()
        )
    )
    return statement, required_parameters


def is_zero_result(rows: list[dict[str, Any]]) -> bool:
    """No rows, or a single one-column row that is NULL or 0 (a COUNT/AVG with nothing under it)."""
    if not rows:
        return True
    if len(rows) == 1 and len(rows[0]) == 1:
        value = next(iter(rows[0].values()))
        return value is None or value == 0
    return False


def _probe_value(name: str, probe_rows: list[dict[str, Any]]) -> Any:
    if name == "visible_students":
        return int(next(iter(probe_rows[0].values()))) if probe_rows else 0
    if name == "taught_subjects":
        return sorted({str(next(iter(row.values()))) for row in probe_rows})
    return None


async def _run_zero_probes(
    session: Any, probe_sql: dict[str, str], parameters: dict[str, Any]
) -> dict[str, Any]:
    """Run each governed reference query in a SAVEPOINT so one failing probe can neither
    abort the transaction nor cost the caller the main result; a failed probe is just absent."""
    results: dict[str, Any] = {}
    for name, sql in probe_sql.items():
        try:
            async with session.begin_nested():
                statement, required = _bind_statement(sql, parameters)
                result = await session.execute(statement, required)
                probe_rows = [dict(row) for row in result.mappings().all()]
        except SQLAlchemyError:
            logger.warning("execute_sql: zero-result probe %r failed", name, exc_info=True)
            continue
        value = _probe_value(name, probe_rows)
        if value is not None:
            results[name] = value
    return results


async def execute_sql(state: TextToSQLState) -> TextToSQLState:
    sql = state.get("validated_sql")
    if not sql:
        # Graph invariant: this node only runs after apply_role_scope's "ok" edge, which
        # guarantees a non-empty validated_sql. Fail loudly rather than execute nothing
        # and silently report success if that invariant is ever violated.
        return {
            **state,
            "query_result": None,
            "result_row_count": None,
            "error": format_error(EXECUTION_ERROR, "execute_sql: no validated_sql to run"),
        }

    try:
        user_id = state["user_id"]
        user_role = state["user_role"]
        institution_id = state["institution_id"]
    except KeyError as exc:
        # These three are documented as always populated from the verified JWT by the
        # time the graph runs, and every node up to this one already trusts them as
        # opaque strings without re-checking that -- but they're subscripted directly
        # below, inside a try that only catches SQLAlchemyError, so a missing value
        # here would otherwise be an uncaught KeyError instead of a controlled failure
        # (audit_log.py guards the same fields explicitly, for the same reason -- see
        # its own docstring). Fail closed the same way: no query is run at all.
        return {
            **state,
            "query_result": None,
            "result_row_count": None,
            "error": format_error(
                EXECUTION_ERROR, f"execute_sql: state is missing required field {exc}"
            ),
        }

    session_factory = get_text_to_sql_session_factory()
    probe_results: dict[str, Any] | None = None
    try:
        async with session_factory() as session:
            # Must run before the query itself: RLS policies read these on every table
            # access, so they need to already be set by the time `sql` executes, in the
            # same transaction (SQLAlchemy's async session keeps one transaction open
            # across these statements by default, same as the existing statement_timeout
            # call below).
            await session.execute(
                text(
                    "SELECT set_config('app.current_user_id', :user_id, true), "
                    "set_config('app.current_user_role', :user_role, true), "
                    "set_config('app.current_institution_id', :institution_id, true)"
                ),
                {
                    "user_id": user_id,
                    "user_role": user_role,
                    "institution_id": institution_id,
                },
            )
            await session.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
            # Defense-in-depth: `text_to_sql_reader` already has no INSERT/UPDATE/DELETE
            # grants (migration c9d0e1f2a3b4), but mark the transaction read-only at the
            # Postgres level too, so a future grant regression or an unexpected mutating
            # statement is rejected by Postgres itself, independent of role privileges.
            await session.execute(text("SET TRANSACTION READ ONLY"))
            parameters = {
                **(state.get("intent_parameters") or {}),
                "current_user_id": user_id,
                "current_institution_id": institution_id,
            }
            statement, required_parameters = _bind_statement(sql, parameters)
            result = await session.execute(statement, required_parameters)
            rows: list[dict[str, Any]] = [dict(row) for row in result.mappings().all()]
            # Same transaction => same RLS variables, still read-only, still timed out.
            probe_sql = state.get("zero_probe_sql") or {}
            if probe_sql and is_zero_result(rows):
                probe_results = await _run_zero_probes(session, probe_sql, parameters)
    except SQLAlchemyError as exc:
        # Postgres/driver error text can carry schema, constraint, or query fragments —
        # it goes to the log and the (internal-only) audit entry, never into
        # state["error"], which is the eventually user-facing value.
        orig = exc.orig if isinstance(exc, DBAPIError) and exc.orig is not None else None
        logger.warning(
            "execute_sql: query failed: type=%s detail=%s cause=%s orig=%s",
            type(exc).__name__,
            str(exc),
            str(exc.__cause__) if exc.__cause__ else None,
            str(orig) if orig else None,
            exc_info=True,
        )
        return {
            **state,
            "query_result": None,
            "result_row_count": None,
            "error": format_error(EXECUTION_ERROR, _GENERIC_FAILURE_MESSAGE),
            "audit_entry": {
                **(state.get("audit_entry") or {}),
                "execution_error_type": type(exc).__name__,
                "execution_error_detail": str(exc),
                "execution_error_cause": str(exc.__cause__) if exc.__cause__ else None,
                "execution_dbapi_type": type(orig).__name__ if orig else None,
                "execution_dbapi_detail": str(orig) if orig else None,
            },
        }

    if len(rows) > ROW_CAP:
        logger.warning(
            "execute_sql: result had %d rows, exceeding ROW_CAP=%d despite validate_sql's "
            "LIMIT injection — truncating",
            len(rows),
            ROW_CAP,
        )
        rows = rows[:ROW_CAP]
        return {
            **state,
            "query_result": rows,
            "result_row_count": len(rows),
            "zero_probe_results": probe_results,
            "error": None,
            "audit_entry": {**(state.get("audit_entry") or {}), "row_cap_truncated": True},
        }

    audit_entry = state.get("audit_entry")
    if probe_results is not None:
        audit_entry = {**(audit_entry or {}), "zero_probe_results": probe_results}
    return {
        **state,
        "query_result": rows,
        "result_row_count": len(rows),
        "zero_probe_results": probe_results,
        "audit_entry": audit_entry,
        "error": None,
    }
