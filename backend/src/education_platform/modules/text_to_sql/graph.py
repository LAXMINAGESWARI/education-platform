"""Text-to-SQL LangGraph skeleton: structure and routing only, no node logic yet.

Happy path:

    injection_guard -> question_validator -> load_schema -> link_schema -> generate_sql
        -> validate_sql -> apply_role_scope -> execute_sql -> sanity_check
        -> compose_answer -> audit_log -> END

`injection_guard` is the entry point, ahead of everything else: a cost/UX check (skip the
LLM call, give a distinct audit signal for an unambiguous prompt-injection attempt), never
the security boundary — that's still `apply_role_scope`, unconditionally, on every path
that reaches it, regardless of what this node did or didn't catch. `question_validator`
sits right after it, ahead of `load_schema`: the same "before any SQL generation is
attempted" placement, for a question that's benign but off-topic rather than adversarial
(see that module's own docstring for why this is a separate node with its own classifier
call rather than folded into injection_guard's — that was tried, measured, and reverted).
The intent-routing stage sits after both of these, so every branch benefits from the same
early checks rather than needing its own copy. Template matches proceed directly to the
existing validation and authorization path; free-form questions continue through schema
loading and generation.

`link_schema` sits between `load_schema` and `generate_sql`, not merged into either:
`load_schema` caches its filtered catalog once, independent of the question (see that
module's own "not on every call" caching discipline), while `link_schema` narrows that
cached catalog *per question* — mixing the two would mean load_schema's cache could no
longer just be "the filtered catalog," it would have to vary per question too, defeating
the point of caching it at all. `link_schema` has no error path of its own (a narrowing
bug is a worse-prompt problem for generate_sql, never a correctness/authorization one —
see that module's own docstring for why) and no conditional edge, unlike every other
stage here — it always proceeds straight to generate_sql.

honest_refusal is reached from six independent failure branches, none of which loop back
into generate_sql's retry path: injection_guard's INJECTION_BLOCKED, question_validator's
OFF_TOPIC_REJECTED, load_schema's "error", validate_sql's "refuse" once retries are
exhausted, apply_role_scope's ROLE_VIOLATION, and execute_sql's EXECUTION_ERROR. validate_sql's
"retry" edge goes back to generate_sql directly (not to link_schema — a retry reuses the
same narrowed schema_context from the first pass rather than re-narrowing) whenever
schema_context is already populated, and only while retry_count < MAX_RETRIES — see
`_route_after_validate` and `MAX_RETRIES`. A rejected *template* match (intent_router's
fast path skips load_schema/link_schema entirely, so schema_context is still "" at this
point) instead takes the "retry_needs_schema" edge back to load_schema first — reusing the
existing load_schema -> link_schema -> generate_sql edges below rather than needing new
ones — so that retry has a real schema to generate against instead of being asked to
write SQL against nothing. state["error"] is deliberately left untouched by that detour,
so generate_sql still sees it as a retry (increments retry_count, folds the rejection
reason into its prompt) exactly as the direct "retry" edge would; only
`_route_after_load_schema` needed to change (checking state["error"]'s *category* rather
than its raw presence) so that this stale, pre-existing error doesn't get misread as a
load_schema failure once load_schema itself succeeds. honest_refusal itself then also
feeds into audit_log, same as compose_answer, so refusals get logged too.

Most nodes in `modules/text_to_sql/nodes/` are still pass-through placeholders (see that
package's docstring); `load_schema`, `generate_sql`, `validate_sql`, `apply_role_scope`,
`execute_sql`, `sanity_check`, `compose_answer`, `audit_log`, and `honest_refusal` are
implemented, so the routing functions for those stages inspect real signals. The rest
fall back to fixed placeholder decisions, each flagged with a TODO for the follow-up task
that implements the remaining node logic.

Note on `honest_refusal` and `audit_log`: `audit_log`'s own fail-closed path (Task 10,
`AUDIT_ERROR`) does not route back through `honest_refusal` — `audit_log` runs strictly
after it on every path (see the edges below: `honest_refusal -> audit_log -> END`,
unconditional), so it produces its own refusal text directly rather than looping back.
`honest_refusal` still formats a message for `AUDIT_ERROR` as one of its seven
categories, for defensive completeness and direct-invocation testing, but that branch is
not reachable through the compiled graph today.
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from education_platform.modules.text_to_sql.nodes import (
    apply_role_scope,
    audit_log,
    compose_answer,
    execute_sql,
    generate_sql,
    honest_refusal,
    injection_guard,
    intent_router,
    link_schema,
    load_schema,
    question_validator,
    sanity_check,
    validate_sql,
)
from education_platform.modules.text_to_sql.state import (
    MAX_RETRIES,
    SCHEMA_ERROR,
    TextToSQLState,
    error_category,
)


def _route_after_injection_guard(state: TextToSQLState) -> Literal["ok", "blocked"]:
    # injection_guard sets state["error"] (INJECTION_BLOCKED) when the heuristic regex
    # or the LLM classifier judged the question itself a prompt-injection attempt —
    # straight to honest_refusal, never into question_validator/load_schema/generate_sql.
    return "blocked" if state.get("error") else "ok"


def _route_after_question_validator(state: TextToSQLState) -> Literal["ok", "blocked"]:
    # question_validator sets state["error"] (OFF_TOPIC_REJECTED) when its classifier
    # judged the question unrelated to school data entirely — straight to honest_refusal,
    # never into load_schema/generate_sql.
    return "blocked" if state.get("error") else "ok"


def _route_after_intent_router(state: TextToSQLState) -> Literal["template", "free_form"]:
    return "template" if state.get("intent_route") == "template" else "free_form"


def _route_after_load_schema(state: TextToSQLState) -> Literal["ok", "error"]:
    # load_schema sets state["error"] (not raise) when schema_catalog.md is missing or
    # the excluded-content filter fails its own post-check — never proceed to
    # generate_sql with a broken/absent schema_context. Checked by *category*, not raw
    # truthiness: this node can also be reached via _route_after_validate's
    # "retry_needs_schema" edge below, with a stale VALIDATION_ERROR still sitting in
    # state["error"] from the validate_sql rejection that triggered the redirect. That
    # value is deliberately left in place (generate_sql's retry-prompt/retry_count
    # logic still needs it), so a load_schema success in that case must not be
    # misread as a load_schema failure just because *some* error is present.
    # SCHEMA_ERROR is raised by load_schema and only by load_schema (state.py).
    return "error" if error_category(state.get("error")) == SCHEMA_ERROR else "ok"


def _route_after_validate(
    state: TextToSQLState,
) -> Literal["retry", "retry_needs_schema", "refuse", "valid"]:
    if state.get("validated_sql"):
        return "valid"
    if state.get("retry_count", 0) < MAX_RETRIES:
        # The template fast-path (intent_router -> validate_sql) never runs
        # load_schema/link_schema, so schema_context is still "" here if a template's
        # hand-authored SQL failed validation. Retrying generate_sql straight away
        # would run it with no schema at all — its prompt forbids inventing tables/
        # columns not shown in the schema, so every remaining retry would be all but
        # guaranteed to fail too. Detour through load_schema (-> link_schema ->
        # generate_sql, the existing free-form edges) once instead, so the retry has
        # a real schema to work from.
        if not state.get("schema_context"):
            return "retry_needs_schema"
        return "retry"
    return "refuse"


def _route_after_apply_role_scope(state: TextToSQLState) -> Literal["ok", "refuse"]:
    # apply_role_scope sets state["error"] (ROLE_VIOLATION) when the query is rejected on
    # authorization grounds — a policy rejection, not a correctness one, so it must never
    # loop back through generate_sql's retry path; it goes straight to honest_refusal,
    # same as a validate_sql retry-exhaustion refusal.
    return "refuse" if state.get("error") else "ok"


def _route_after_execute(state: TextToSQLState) -> Literal["ok", "refuse"]:
    # execute_sql sets state["error"] (EXECUTION_ERROR) on a genuine DB-level failure —
    # timeout, connection failure, unexpected constraint violation. The SQL was already
    # proven valid and authorized by the time it got here, so generate_sql rewriting it
    # again can't fix a database failure; this goes straight to honest_refusal, same as
    # a validate_sql/apply_role_scope rejection, never back into the retry loop.
    return "refuse" if state.get("error") else "ok"


def _route_after_sanity_check(state: TextToSQLState) -> Literal["suspicious", "normal"]:
    # sanity_check (Task 8) is the sole owner of state["confidence"] — it's fully decided
    # there, not by this router or by which node runs next. "medium" and "low" both route
    # via "suspicious": nothing downstream currently behaves differently between them
    # (compose_answer reads state["confidence"] directly for its own phrasing, not the
    # edge that was taken), so collapsing them here loses no information. See
    # sanity_check.py's module docstring for the full reasoning.
    return "normal" if state.get("confidence") == "high" else "suspicious"


def build_text_to_sql_graph() -> CompiledStateGraph[TextToSQLState, None, TextToSQLState]:
    graph: StateGraph[TextToSQLState] = StateGraph(TextToSQLState)

    graph.add_node("injection_guard", injection_guard)
    graph.add_node("question_validator", question_validator)
    graph.add_node("intent_router", intent_router)
    graph.add_node("load_schema", load_schema)
    graph.add_node("link_schema", link_schema)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("validate_sql", validate_sql)
    graph.add_node("apply_role_scope", apply_role_scope)
    graph.add_node("execute_sql", execute_sql)
    graph.add_node("sanity_check", sanity_check)
    graph.add_node("compose_answer", compose_answer)
    graph.add_node("audit_log", audit_log)
    graph.add_node("honest_refusal", honest_refusal)

    graph.set_entry_point("injection_guard")
    graph.add_conditional_edges(
        "injection_guard",
        _route_after_injection_guard,
        {
            "ok": "question_validator",
            "blocked": "honest_refusal",
        },
    )
    graph.add_conditional_edges(
        "question_validator",
        _route_after_question_validator,
        {
            "ok": "intent_router",
            "blocked": "honest_refusal",
        },
    )
    graph.add_conditional_edges(
        "intent_router",
        _route_after_intent_router,
        {
            "template": "validate_sql",
            "free_form": "load_schema",
        },
    )
    graph.add_conditional_edges(
        "load_schema",
        _route_after_load_schema,
        {
            "ok": "link_schema",
            "error": "honest_refusal",
        },
    )
    graph.add_edge("link_schema", "generate_sql")
    graph.add_edge("generate_sql", "validate_sql")

    graph.add_conditional_edges(
        "validate_sql",
        _route_after_validate,
        {
            "retry": "generate_sql",
            "retry_needs_schema": "load_schema",
            "refuse": "honest_refusal",
            "valid": "apply_role_scope",
        },
    )

    graph.add_conditional_edges(
        "apply_role_scope",
        _route_after_apply_role_scope,
        {
            "ok": "execute_sql",
            "refuse": "honest_refusal",
        },
    )
    graph.add_conditional_edges(
        "execute_sql",
        _route_after_execute,
        {
            "ok": "sanity_check",
            "refuse": "honest_refusal",
        },
    )

    # Both keys route to the same node: sanity_check already decided state["confidence"]
    # ("medium" and "low" both take the "suspicious" edge — see _route_after_sanity_check).
    # Kept as a conditional edge with two named keys, rather than a single add_edge,
    # purely for graph-shape/trace readability — there is deliberately no glue node here
    # anymore to set confidence, since sanity_check already did.
    graph.add_conditional_edges(
        "sanity_check",
        _route_after_sanity_check,
        {
            "suspicious": "compose_answer",
            "normal": "compose_answer",
        },
    )

    graph.add_edge("compose_answer", "audit_log")
    graph.add_edge("honest_refusal", "audit_log")
    graph.add_edge("audit_log", END)

    return graph.compile()
