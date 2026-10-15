"""Unit tests for graph.py's routing functions around the template-retry/empty-schema fix.

Pure state-dict-in, string-literal-out functions — no LLM, no DB, no compiled graph
needed to exercise the routing decision itself.
"""

from __future__ import annotations

from education_platform.modules.text_to_sql.graph import (
    _route_after_load_schema,
    _route_after_validate,
)
from education_platform.modules.text_to_sql.state import (
    MAX_RETRIES,
    SCHEMA_ERROR,
    VALIDATION_ERROR,
    format_error,
)


def test_route_after_validate_valid_sql_goes_to_apply_role_scope() -> None:
    state = {"validated_sql": "SELECT 1"}
    assert _route_after_validate(state) == "valid"


def test_route_after_validate_retries_normally_once_schema_is_populated() -> None:
    # free-form path: schema_context was already loaded before generate_sql ran
    state = {"retry_count": 0, "schema_context": "## 1. Conventions\n..."}
    assert _route_after_validate(state) == "retry"


def test_route_after_validate_template_failure_routes_to_load_schema_first() -> None:
    # template fast-path: intent_router -> validate_sql skipped load_schema entirely,
    # so schema_context is still "" when the template SQL fails validation
    state = {"retry_count": 0, "schema_context": ""}
    assert _route_after_validate(state) == "retry_needs_schema"


def test_route_after_validate_missing_schema_context_key_also_needs_schema() -> None:
    # intent_router's template branch never sets schema_context at all (only
    # router.py's _initial_state does, to "") — .get() must treat missing the same as
    # empty, not KeyError or None-is-falsy-only.
    state = {"retry_count": 0}
    assert _route_after_validate(state) == "retry_needs_schema"


def test_route_after_validate_refuses_once_retries_exhausted() -> None:
    state = {"retry_count": MAX_RETRIES, "schema_context": ""}
    assert _route_after_validate(state) == "refuse"


def test_route_after_validate_refuses_when_retries_exhausted_even_with_schema() -> None:
    state = {"retry_count": MAX_RETRIES, "schema_context": "## 1. Conventions\n..."}
    assert _route_after_validate(state) == "refuse"


def test_route_after_load_schema_ok_despite_stale_validation_error() -> None:
    # simulates arriving at load_schema via the new "retry_needs_schema" edge: the old
    # validate_sql rejection is still sitting in state["error"], load_schema itself
    # succeeded (schema_context populated) — must NOT be misread as a load_schema
    # failure just because *an* error happens to be present.
    state = {
        "error": format_error(VALIDATION_ERROR, "query must be a single SELECT"),
        "schema_context": "## 1. Conventions\n...",
    }
    assert _route_after_load_schema(state) == "ok"


def test_route_after_load_schema_error_on_real_schema_failure() -> None:
    state = {"error": format_error(SCHEMA_ERROR, "schema_catalog.md not readable")}
    assert _route_after_load_schema(state) == "error"


def test_route_after_load_schema_ok_on_fresh_entry_with_no_error() -> None:
    state = {"error": None, "schema_context": "## 1. Conventions\n..."}
    assert _route_after_load_schema(state) == "ok"


def test_route_after_load_schema_ok_on_unformatted_legacy_error_text() -> None:
    # error_category() returns None for any string not produced by format_error() —
    # confirm that degrades to "ok" (never SCHEMA_ERROR) rather than raising/misrouting.
    state = {"error": "some unrelated raw string", "schema_context": "## 1. Conventions\n..."}
    assert _route_after_load_schema(state) == "ok"
