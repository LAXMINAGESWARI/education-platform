"""Structurally validates state["generated_sql"] using sqlglot (`read="postgres"`) as
the sole basis for every check in this module — no regex/string-based SQL inspection.

Five ordered checks, any of which rejects immediately:

1. Parse — `sqlglot.parse_one(sql, read="postgres")`. A parse failure is a rejection,
   not a crash.
2. Shape — the parsed result must be exactly one statement, and its root must be
   `exp.Select`. The full tree (not just the root) is then walked for any
   `exp.Insert`/`Update`/`Delete`/`Drop`/`Grant`/`Command` node, including ones nested
   inside a CTE or subquery (`WITH x AS (DELETE ...) SELECT * FROM x` parses with a
   `Select` root but still contains a `Delete` — confirmed empirically, this is a real
   bypass a root-type-only check would miss).
3. Table whitelist — every `exp.Table` found via `find_all` (which walks CTEs,
   subqueries, and joins) must resolve to a table in `SCOPED_TABLES`: real SQLAlchemy
   `Table` objects, not schema_catalog.md text, restricted to the same tables
   `load_schema.py`'s `REQUIRED_TABLES` keeps in scope. This is the actual backstop for
   Task 3's exclusion — schema_context never mentions chunk_embeddings/ingest_jobs/etc,
   but nothing stops the model from guessing a name anyway, and this is what catches it
   if it does. CTE names and derived-table (subquery-in-FROM) aliases are excluded from
   this check — they're query-local, not schema objects.
4. Column existence — for each table reference that passed step 3, any *explicitly
   named* column must exist on that table (again via SQLAlchemy metadata).
   `SELECT *` is allowed — see the decision note below.
5. LIMIT — if absent, injected (not rejected) — see the decision note below.

On success: `state["validated_sql"]` gets the (possibly LIMIT-injected) query,
serialized back through sqlglot; `state["generated_sql"]` is left untouched, preserving
what the model actually said for audit/debugging. `state["error"]` is cleared.
On rejection: `state["error"] = format_error(VALIDATION_ERROR, detail)`, with `detail`
naming the specific offending table/column/clause so generate_sql's retry prompt has
something concrete to act on. `state["retry_count"]` is never touched here — that's
generate_sql's job on its next entry (Task 4).

Two decisions the task explicitly left to this implementation:

- **SELECT * stays allowed.** Column-level sensitivity (e.g. withholding
  `question_answer_keys` from a student) is a row/role concern, not a structural SQL
  validity one — that's apply_role_scope's job (Task 6), not this node's.
- **A missing LIMIT is injected, not rejected.** Rejecting would burn one of the 3
  retries on something that isn't a defect in the model's SQL logic — closer to a
  mechanical policy requirement than a correction the model needs to make, so handling
  it here costs nothing. The cap is 500, matching the existing
  `insights.service.MAX_ROWS` convention in this codebase ("hard ceiling on any single
  read, so no question can pull the whole school by accident") rather than picking a
  new number.

One more scope decision worth flagging explicitly: the task's own text names the
whitelist scope as "academics, assessments, attendance, and the student_360 view," but
also says "the same scope Task 3 enforces." Task 3's actual kept scope is wider than
that four-item list — it also keeps `materials` and `auth` (minus `audit_events`). This
module uses the wider, actual Task 3 scope (`REQUIRED_TABLES`, imported directly from
load_schema.py rather than re-declared here) because the two *must* match: schema_context
tells the model it may reference e.g. `student_profiles` or `source_material_versions`,
and if this whitelist were narrower, this node would reject perfectly legitimate SQL
the model was explicitly told was in scope.
"""

from __future__ import annotations

import re
from typing import Final

import sqlglot
from sqlalchemy import Table
from sqlglot import exp
from sqlglot.errors import SqlglotError

import education_platform.db.models as _all_models
from education_platform.db.base import Base
from education_platform.modules.insights.models import student_360
from education_platform.modules.text_to_sql.nodes.load_schema import REQUIRED_TABLES
from education_platform.modules.text_to_sql.state import (
    LLM_ERROR,
    VALIDATION_ERROR,
    TextToSQLState,
    error_category,
    format_error,
)

_ = _all_models  # import side effect: registers every module's tables on Base.metadata

DEFAULT_ROW_LIMIT: Final[int] = 500

_DML_DDL_TYPES: Final[tuple[type[exp.Expression], ...]] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Grant,
    exp.Command,
    exp.Create,
    exp.Into,
)

_SHAPE_HINTS: Final[dict[type[exp.Expr], str]] = {
    exp.Block: "multiple statements are not allowed — write exactly one SELECT",
    exp.Union: "UNION queries are not supported — write a single SELECT statement",
    exp.Delete: "DELETE statements are not allowed — only SELECT is permitted",
    exp.Insert: "INSERT statements are not allowed — only SELECT is permitted",
    exp.Update: "UPDATE statements are not allowed — only SELECT is permitted",
    exp.Drop: "DROP statements are not allowed — only SELECT is permitted",
    exp.Create: "CREATE statements are not allowed — only SELECT is permitted",
    exp.Into: "SELECT INTO is not allowed — only a plain SELECT is permitted",
    exp.Command: "that statement type is not allowed — only SELECT is permitted",
    exp.Set: "SET statements are not allowed — only SELECT is permitted",
}

# Session mutators / non-read helpers that can defeat RLS, poison the connection pool,
# or hang the statement — even inside an otherwise-valid SELECT or CTE.
#
# `query_to_xml` / `query_to_xmlschema` / `cursor_to_xml*` are blocked because their
# string (or cursor) argument is opaque to the AST walker: an inner `set_config(...)`
# never appears as `exp.Anonymous`, so the blocklist below would otherwise miss it.
# Confirmed live: `text_to_sql_reader` has EXECUTE on these by default (PUBLIC), and
# `query_to_xml($$SELECT set_config('app.current_institution_id', …) …$$, …)` rewrites
# RLS GUCs mid-statement and returns cross-tenant rows. Blocking the outer call closes
# that channel; a companion migration also REVOKEs EXECUTE as a DB-level backstop.
_BLOCKED_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        "set_config",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "pg_advisory_unlock",
        "pg_advisory_unlock_all",
        "pg_sleep",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "lo_import",
        "lo_export",
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        "cursor_to_xml",
        "cursor_to_xmlschema",
    }
)


def _is_in_recursive_union_term(table_node: exp.Table, cte: exp.CTE) -> bool:
    """True when `table_node` sits in the recursive (right) arm of `cte`'s top-level UNION.

    Postgres `WITH RECURSIVE` name resolution: the CTE name is *not* in scope in the
    non-recursive (left) term, so an unqualified same-named `FROM t` there binds to the
    real table. Only the recursive (right) term's self-reference binds to the CTE.
    """
    query = cte.this
    if not isinstance(query, exp.Union):
        return False
    node: exp.Expr | None = table_node
    while node is not None and node is not query:
        parent = node.parent
        if parent is query:
            return node is query.expression
        node = parent
    return False


def is_cte_or_derived_table_ref(table_node: exp.Table, local_aliases: set[str]) -> bool:
    """True when `table_node` names a CTE or derived-table alias, not a real schema table.

    Critical nuance: a non-RECURSIVE CTE whose alias collides with a real table name
    (`WITH student_profiles AS (SELECT … FROM student_profiles) …`) binds the *inner*
    `FROM student_profiles` to the base table in Postgres. Treating that inner ref as a
    CTE alias would skip whitelist / role-scope checks on the real table — the exact
    gap a same-named CTE can use to defeat apply_role_scope.

    Recursive CTEs have a second gap: only the *recursive term* (right arm of the
    top-level UNION) may self-reference the CTE. In the non-recursive (base) term the
    CTE name is not yet in scope, so an unqualified same-named `FROM student_profiles`
    is a real base-table scan — same as the schema-qualified form PR #139 closed.

    Schema-/catalog-qualified names (`public.student_profiles`, `pg_catalog.pg_roles`)
    are never CTE refs — Postgres resolves them to the real relation even inside a
    RECURSIVE CTE whose alias collides with that table. Treating those as CTE aliases
    would skip whitelist and apply_role_scope on the real scan (and combine with a
    session-mutator bypass into a full RLS escalation).
    """
    if table_node.db or table_node.catalog:
        return False
    name = table_node.name.lower()
    if name not in local_aliases:
        return False
    parent: exp.Expr | None = table_node.parent
    while parent is not None:
        if isinstance(parent, exp.CTE) and (parent.alias or "").lower() == name:
            with_node = parent.parent
            if not (isinstance(with_node, exp.With) and with_node.args.get("recursive")):
                return False  # non-RECURSIVE same-named CTE body → real table
            # RECURSIVE: only the recursive-term self-ref is a CTE alias.
            return _is_in_recursive_union_term(table_node, parent)
        parent = parent.parent
    return True


def _build_scoped_tables() -> dict[str, Table]:
    scoped: dict[str, Table] = {}
    for name in REQUIRED_TABLES:
        if name == "student_360":
            scoped[name] = student_360
            continue
        try:
            scoped[name] = Base.metadata.tables[name]
        except KeyError as exc:
            raise RuntimeError(
                f"text_to_sql scope drift: {name!r} is in load_schema.REQUIRED_TABLES "
                "but has no matching table in Base.metadata — has it been renamed or "
                "dropped?"
            ) from exc
    return scoped


SCOPED_TABLES: Final[dict[str, Table]] = _build_scoped_tables()


def _sql_with_named_parameters(tree: exp.Select) -> str:
    """Serialize validated SQL while retaining SQLAlchemy's ``:name`` bind syntax."""
    rendered = tree.sql(dialect="postgres")
    return re.sub(r"%\(([A-Za-z_]\w*)\)s", r":\1", rendered)


def _is_quoted(identifier: exp.Expression | None) -> bool:
    return bool(identifier is not None and getattr(identifier, "quoted", False))


def _name_matches(raw: str, quoted: bool, real_name: str) -> bool:
    # Real names are all lowercase snake_case. An unquoted identifier folds to
    # lowercase (Postgres's default behavior); a quoted one must match exactly.
    return raw == real_name if quoted else raw.lower() == real_name


def _resolve_table(table_node: exp.Table) -> str | None:
    raw = table_node.name
    quoted = _is_quoted(table_node.this)
    for real_name in SCOPED_TABLES:
        if _name_matches(raw, quoted, real_name):
            return real_name
    return None


def _column_exists(table: Table, raw: str, quoted: bool) -> bool:
    # Not `for col_name in table.columns` (ruff SIM118's usual fix): SQLAlchemy's
    # ColumnCollection iterates Column *objects*, not name strings, unlike a plain
    # dict where iteration and .keys() coincide — .keys() is required here.
    return any(
        _name_matches(raw, quoted, col_name)
        for col_name in table.columns.keys()  # noqa: SIM118
    )


def _local_aliases(tree: exp.Select) -> set[str]:
    """Names that refer to a CTE or a derived (subquery-in-FROM) table within this
    query — not real schema tables, so neither the whitelist nor column-existence
    checks apply to them. A CTE's own name shows up as an `exp.Table` reference
    wherever it's used later in the query (confirmed empirically); a derived table's
    alias does not appear as `exp.Table` at all, only as `exp.Subquery.alias` — the two
    need collecting from different node types but get the same treatment here.

    Also collects the alias given to a CTE *reference*, not just the CTE's own name:
    `WITH recent AS (...) SELECT r.x FROM recent r` qualifies its columns with `r`, not
    `recent` — both need to resolve as "this is a CTE, not a real table" or `r.x` gets
    wrongly rejected as an unknown table alias (caught by testing this exact query).

    A `JOIN LATERAL (...) AS alias ON true` derived table is its own case, not covered
    by the `exp.Subquery` walk above: sqlglot parses it as an `exp.Lateral` node whose
    *own* `.alias` carries the real name (`latest` in `JOIN LATERAL (...) AS latest ON
    true`) — the `exp.Subquery` node it wraps is anonymous (`alias == ""`), confirmed
    empirically. Missing this meant a lateral join's alias was never recognized as
    local, so any column qualified by it (`latest.score_percent`) was wrongly rejected
    as an unknown table alias — caught live: this is exactly the join shape
    generate_sql's own system prompt now teaches for "one row per entity, picking a
    specific attempt's value" questions (see that module's docstring), so rejecting it
    here would silently defeat that fix.
    Aliases attached to a same-named *base-table* ref inside a non-RECURSIVE CTE body
    are intentionally omitted — those name the real table (see
    `is_cte_or_derived_table_ref`).
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


def _check_tables(tree: exp.Select, local_aliases: set[str]) -> tuple[dict[str, str], str | None]:
    """Returns (alias/name -> real scoped table name, rejection detail or None)."""
    alias_map: dict[str, str] = {}
    for table_node in tree.find_all(exp.Table):
        if is_cte_or_derived_table_ref(table_node, local_aliases):
            continue  # a reference to a CTE by name, not a real table
        real_name = _resolve_table(table_node)
        if real_name is None:
            return alias_map, f"table `{table_node.name}` is not part of the available schema"
        local_key = (table_node.alias or table_node.name).lower()
        alias_map[local_key] = real_name
    return alias_map, None


def _check_columns(
    tree: exp.Select, alias_map: dict[str, str], local_aliases: set[str]
) -> str | None:
    real_tables_used = set(alias_map.values())
    for col in tree.find_all(exp.Column):
        raw = col.name
        quoted = _is_quoted(col.this)
        qualifier = col.table.lower() if col.table else None

        if qualifier:
            if qualifier in local_aliases and qualifier not in alias_map:
                continue  # column on a CTE/derived table — not checked, see docstring
            real_name = alias_map.get(qualifier)
            if real_name is None:
                return f"column `{col.table}.{col.name}` references an unknown table alias"
            if not _column_exists(SCOPED_TABLES[real_name], raw, quoted):
                return f"column `{raw}` does not exist on table `{real_name}`"
        else:
            if not real_tables_used:
                continue
            if not any(_column_exists(SCOPED_TABLES[t], raw, quoted) for t in real_tables_used):
                tables_listed = ", ".join(sorted(real_tables_used))
                return f"column `{raw}` does not exist on any referenced table ({tables_listed})"
    return None


def _anonymous_function_name(node: exp.Anonymous) -> str | None:
    """Normalize sqlglot's function-name payload to a lowercase bare name.

    Unquoted `set_config(...)` stores the name as a plain `str`. Double-quoted
    `"set_config"(...)` (and `pg_catalog."set_config"(...)`) store an
    `exp.Identifier` instead — comparing only `isinstance(raw, str)` would miss
    the quoted form and let a session mutator through validate_sql.
    """
    raw = node.this
    if isinstance(raw, str):
        return raw.lower()
    if isinstance(raw, exp.Identifier):
        return raw.name.lower()
    return None


def _find_blocked_function(tree: exp.Expr) -> str | None:
    for node in tree.find_all(exp.Anonymous):
        name = _anonymous_function_name(node)
        if name is not None and name in _BLOCKED_FUNCTIONS:
            return name
    return None


def _limit_value(tree: exp.Select) -> int | None:
    limit = tree.args.get("limit")
    if not isinstance(limit, exp.Limit):
        return None
    expression = limit.expression
    if not isinstance(expression, exp.Literal) or expression.is_string:
        return None
    try:
        return int(expression.this)
    except (TypeError, ValueError):
        return None


async def validate_sql(state: TextToSQLState) -> TextToSQLState:
    sql = state.get("generated_sql")
    if not sql:
        if error_category(state.get("error")) == LLM_ERROR:
            # generate_sql itself produced nothing this round; there's nothing to
            # validate. Leave its error as-is rather than overwrite it with a less
            # specific message — the retry routing only checks
            # validated_sql/retry_count, so this still falls through to a retry (or
            # honest_refusal once exhausted) correctly either way.
            return {**state}
        return {
            **state,
            "error": format_error(VALIDATION_ERROR, "no SQL was generated to validate"),
        }

    # 1. Parse.
    try:
        tree: exp.Expr = sqlglot.parse_one(sql, read="postgres")
    except SqlglotError as exc:
        return {**state, "error": format_error(VALIDATION_ERROR, f"SQL failed to parse: {exc}")}

    # 2. Single-statement, single-SELECT shape (+ DML/DDL anywhere in the tree).
    if not isinstance(tree, exp.Select):
        hint = _SHAPE_HINTS.get(type(tree), f"got a {type(tree).__name__} statement, not a SELECT")
        return {
            **state,
            "error": format_error(VALIDATION_ERROR, f"query must be a single SELECT — {hint}"),
        }

    forbidden = next((n for n in tree.walk() if isinstance(n, _DML_DDL_TYPES)), None)
    if forbidden is not None:
        return {
            **state,
            "error": format_error(
                VALIDATION_ERROR,
                f"query contains a {type(forbidden).__name__} statement, which is not "
                "allowed even nested inside a CTE or subquery — only a plain SELECT is "
                "permitted",
            ),
        }

    # 3. Table whitelist, against real SQLAlchemy metadata.
    local_aliases = _local_aliases(tree)
    alias_map, table_error = _check_tables(tree, local_aliases)
    if table_error is not None:
        return {**state, "error": format_error(VALIDATION_ERROR, table_error)}

    # 4. Column existence (SELECT * is allowed; see module docstring).
    column_error = _check_columns(tree, alias_map, local_aliases)
    if column_error is not None:
        return {**state, "error": format_error(VALIDATION_ERROR, column_error)}

    # 5. Block session-mutating / non-read functions (set_config, advisory locks, …).
    blocked = _find_blocked_function(tree)
    if blocked is not None:
        return {
            **state,
            "error": format_error(
                VALIDATION_ERROR,
                f"function `{blocked}` is not allowed in text-to-SQL queries",
            ),
        }

    # 6. LIMIT enforcement — inject a default, and clamp any explicit oversize LIMIT.
    limit_value = _limit_value(tree)
    if limit_value is None or limit_value > DEFAULT_ROW_LIMIT:
        tree = tree.limit(DEFAULT_ROW_LIMIT)

    return {**state, "validated_sql": _sql_with_named_parameters(tree), "error": None}
