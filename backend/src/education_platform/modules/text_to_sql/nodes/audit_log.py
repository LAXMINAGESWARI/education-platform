"""Writes one complete audit record for every run of this graph — success or refusal —
via the existing `audit_events` table / `audit.service.record_event()`, the same
generic append-only log every other governed read in this codebase already writes to.

**Reusing `audit_events` rather than a dedicated table** (see Task 10's own write-up for
the full investigation): `AuditEvent`'s shape — typed `institution_id`/`actor_user_id`/
`event_type`/`entity_type`/`entity_id` columns plus a free-form JSON `payload` — already
exists specifically to carry event-specific richness (`record_scoped_read()` already
stores several structured fields in `payload`), and `AuditAction.ASK_DATA` ("data.ask") is
an unused enum member clearly reserved for this exact feature — "ask-the-data" is this
pipeline's own product name elsewhere in the codebase (see e.g.
`frontend/src/pages/teacher/AssistantStubPage.tsx`). A second table would only fragment
the audit trail across two places an administrator has to check, for no capability this
pipeline actually needs (no relational queries over audit data are required — a JSON
payload search/display is enough). Task 3's exclusion of `audit_events` from the LLM's
`schema_context`, and Task 7's exclusion of it from the restricted `text_to_sql_reader`
role's grants, are both about the *text-to-SQL query path itself* (what the model may
reference, what that read-only role may touch) — neither applies here: this node writes
via the normal `education` app role (`db.session.get_session_factory()`, not
`get_text_to_sql_session_factory()`), the same session/role every other audit write in
this codebase already uses, since persisting an audit row requires INSERT, which the
restricted reader role deliberately does not have.

**What's recorded, every time:** `question`/`user_role` (no dedicated columns for these,
so they go in `payload`); `user_id`/`institution_id` go in `AuditEvent`'s own typed
`actor_user_id`/`institution_id` columns, not duplicated into `payload`; `generated_sql`
and `validated_sql` in full — this is the one place in the whole pipeline raw SQL is
allowed to be persisted (it must never reach `compose_answer`'s output or any user-facing
field, but an auditor needs the real query); the full `schema_linking_tables_selected`
list link_schema chose for this question (or `null` if it fell back to the full,
unnarrowed catalog — either because no table matched lexically, or because this node ran
before link_schema existed on a given code path) — retrieval-trace logging, the same
practice a RAG pipeline's context-selection step would get, since it's the direct answer
to "why did/didn't the model know about table X for this question" that neither
`generated_sql` nor `validated_sql` alone can show; `result_row_count`, `confidence`, the
full `sanity_check_triggers` list; `apply_role_scope`'s own `role_scope_applied`
("rewritten" or "template_builtin") and `scoped_by_user_id`, so an auditor can see whether
row scoping actually ran for a given query rather than only inferring it from the SQL text;
`execute_sql`'s `execution_error_type`/`execution_error_detail`/`execution_error_cause`/
`execution_dbapi_type`/`execution_dbapi_detail` on a genuine execution failure, and
`row_cap_truncated` when a result had to be cut to `ROW_CAP` — all of these were already
computed into `state["audit_entry"]` by the nodes that own them, specifically so this node
could persist them, and previously were not; `retry_count`, and `outcome`
("answered"/"refused") plus, if refused, both the formatted error *category*
(`error_category(error)`) and the raw error text (`error_detail`) — every node in this
pipeline now formats `state["error"]` via
`format_error()` (load_schema's SCHEMA_ERROR closed the last gap, where its own
unformatted string used to make `error_category()` come back `None` for it specifically),
but `error_detail` is kept regardless, for every category, not as a fallback for an
unparseable one: `SchemaCatalogError`'s message can carry the real catalog file path,
`SQLAlchemyError`'s can carry schema/constraint fragments — detail an auditor
investigating a real failure needs and the category alone doesn't carry, the same reason
`execute_sql` separately records `execution_error_type`/`execution_error_detail` rather
than trusting `EXECUTION_ERROR` alone to be enough. `created_at` is `AuditEvent`'s own
server-defaulted timestamp column — nothing extra needed for that.

**Not recorded: `query_result`'s actual row contents, only `result_row_count`.** Copying
result rows into `payload` would create a second, JSON-typed, harder-to-govern copy of
exactly the sensitive data `apply_role_scope` exists to restrict — no field-level
redaction, no schema, unbounded growth as more queries run, sitting in a table with its
own access surface (`/admin/audit-events`) separate from the governed read paths the rest
of this codebase uses. `validated_sql` (already logged in full) plus `result_row_count` is
enough to reconstruct or re-run the query for debugging or a compliance investigation; a
genuine need to freeze the *exact historical* result (distinct from "what does re-running
this query show now") would be a deliberate, separately access-controlled archival
decision, not something to fold into a general-purpose audit payload by default.

**Fail-closed on the audit write itself failing.** This project's charter treats
role-based access and the audit trail as equally committed guarantees (see `AUDIT_ERROR`
in `state.py`), not one a fallback for the other — the same reasoning that makes
`apply_role_scope` refuse rather than serve a policy-violating answer applies here: an
answer that exists with no corresponding audit record is a charter violation, not a minor
bookkeeping gap. Concretely, `audit_log` is the terminal node (no routing edge exists to
send it back through honest_refusal), so "fail-closed" means overwriting
`state["natural_answer"]` with an honest refusal and setting `state["error"]` to
`AUDIT_ERROR` before returning — the caller never receives the already-composed answer
without a matching audit record existing.

The content that would have been the audit row — question, both SQL strings, confidence,
retry count, everything `_build_payload` assembled — is logged in full alongside the
exception when the write fails, not just a fixed message. Fail-closed only protects
against an *unaudited answer reaching the user*; it does nothing for the record itself if
the only trace of it disappears too. The application logger (not the database) is the
fallback durable store for that content in this failure path — recoverable from logs even
though it never made it into `audit_events`, which is a materially better outcome than
losing the content outright.

**No retry on the audit write itself — a deliberate choice, not an oversight.** A
transient connection blip could in principle be absorbed by retrying before treating the
write as failed. This pipeline already has a precedent for exactly this class of decision:
`execute_sql` (Task 7) does not retry a DB-level failure either — a timeout or connection
error there routes straight to `honest_refusal`, no retry loop. The *only* retry loop in
this whole graph (`generate_sql` re-prompting the model, gated by `MAX_RETRIES`) exists
because that failure is model-correctable; a DB connection hiccup is not the kind of thing
another attempt at composing SQL fixes, and retrying here would be the only place in the
pipeline treating an infrastructure failure as retryable, inconsistent with how
`execute_sql` already handles the identical failure class one node earlier. Given
`audit_log`'s write goes through the same Postgres instance the rest of the request path
already depended on to get this far, a failure here is at least as likely to be a real,
non-transient problem (a constraint violation, a schema mismatch) as a one-off blip a
retry would actually help with. Accepted as a real availability cost for this POC, not
resolved — a production version of this feature would more likely decouple the write
entirely (e.g. an outbox/queue with its own retry policy) rather than retry synchronously
inline here.
"""

from __future__ import annotations

import logging
from typing import Any, Final
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from education_platform.db.session import get_session_factory
from education_platform.modules.audit.service import AuditAction, record_event
from education_platform.modules.text_to_sql.state import (
    AUDIT_ERROR,
    TextToSQLState,
    error_category,
    format_error,
)

logger = logging.getLogger(__name__)

_ENTITY_TYPE: Final[str] = "text_to_sql_query"

_AUDIT_FAILURE_ANSWER: Final[str] = (
    "This request could not be completed right now — the answer could not be recorded "
    "to the audit trail, so it is not being returned. Please try again."
)


def _build_payload(state: TextToSQLState) -> dict[str, Any]:
    error = state.get("error")
    audit_entry = state.get("audit_entry") or {}
    return {
        "question": state.get("question"),
        "user_role": state.get("user_role"),
        "query_source": state.get("query_source"),
        # intent_router's own classification — which named template matched (`None` on
        # the free-form path), its confidence, and, critically for a template match, the
        # actual bound parameter values. A template's `validated_sql` only ever contains
        # unbound `:subject`/`:threshold`/... placeholders (apply_role_scope's
        # "template_builtin" branch never rewrites it — see that module's own docstring),
        # so without these three fields an auditor looking at a template-sourced audit
        # row could see the query shape but never what data it actually touched. Computed
        # by intent_router on every path, previously never persisted.
        "intent": state.get("intent"),
        "intent_confidence": state.get("intent_confidence"),
        "intent_parameters": state.get("intent_parameters"),
        "generated_sql": state.get("generated_sql"),
        "validated_sql": state.get("validated_sql"),
        # None here is itself meaningful (link_schema fell back to the full, unnarrowed
        # catalog) and deliberately not defaulted to []/some other stand-in the way
        # sanity_check_triggers is below -- see link_schema.py's own fail-safe docstring
        # section for why None means "nothing excluded," not "unknown."
        "schema_linking_tables_selected": audit_entry.get("schema_linking_tables_selected"),
        "result_row_count": state.get("result_row_count"),
        "confidence": state.get("confidence"),
        "sanity_check_triggers": audit_entry.get("sanity_check_triggers", []),
        # apply_role_scope's own record of what it did to this query — "rewritten" (the
        # full row/institution predicate splice) or "template_builtin" (skipped, trusting
        # the template's own hand-authored scoping) — and which user_id it scoped by.
        # Previously computed but silently dropped before reaching audit_events; an
        # auditor investigating a role-scope decision had nothing to check it against.
        "role_scope_applied": audit_entry.get("role_scope_applied"),
        "scoped_by_user_id": audit_entry.get("scoped_by_user_id"),
        # execute_sql's real exception detail, per that module's own docstring ("goes to
        # logger and to state['audit_entry'] only") — was computed for exactly this audit
        # record and then never actually reached it.
        "execution_error_type": audit_entry.get("execution_error_type"),
        "execution_error_detail": audit_entry.get("execution_error_detail"),
        "execution_error_cause": audit_entry.get("execution_error_cause"),
        "execution_dbapi_type": audit_entry.get("execution_dbapi_type"),
        "execution_dbapi_detail": audit_entry.get("execution_dbapi_detail"),
        # Whether execute_sql had to truncate to ROW_CAP — a known-incomplete-by-
        # construction result, not merely uncertain (see sanity_check.py's own severity
        # note for this trigger). False, not None, is the right "didn't happen" default:
        # execute_sql only ever sets this key to True, so its absence always means no
        # truncation occurred, never "unknown."
        "row_cap_truncated": audit_entry.get("row_cap_truncated", False),
        "retry_count": state.get("retry_count", 0),
        "outcome": "refused" if error else "answered",
        "error_category": error_category(error),
        "error_detail": error,
    }


def _fail_closed(state: TextToSQLState, payload: dict[str, Any], reason: str) -> TextToSQLState:
    # The record never made it into audit_events, so `payload` is logged here in full —
    # recoverable from logs even though it's unaudited, rather than lost outright. The
    # fail-closed response withholds the answer regardless of *why* the write failed.
    logger.error("audit_log: %s; content follows: %r", reason, payload, exc_info=True)
    return {
        **state,
        "natural_answer": _AUDIT_FAILURE_ANSWER,
        "error": format_error(AUDIT_ERROR, f"audit_log: {reason}"),
    }


async def audit_log(state: TextToSQLState) -> TextToSQLState:
    payload = _build_payload(state)

    try:
        institution_id = UUID(state["institution_id"])
        actor_user_id = UUID(state["user_id"])
    except (KeyError, TypeError, ValueError):
        # state["institution_id"]/state["user_id"] are documented as always populated
        # from the verified JWT by the time the graph runs, and every upstream node
        # already trusts them as opaque strings without validating that -- this is the
        # first place they're actually parsed as UUIDs, so a malformed/missing value here
        # would otherwise be an uncaught crash rather than a controlled failure. Same
        # fail-closed outcome as a DB write failure: no valid audit record could be
        # constructed, so no answer is served without one.
        return _fail_closed(state, payload, "state is missing a valid institution_id/user_id")

    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            await record_event(
                session,
                institution_id=institution_id,
                actor_user_id=actor_user_id,
                event_type=AuditAction.ASK_DATA,
                entity_type=_ENTITY_TYPE,
                payload=payload,
            )
            await session.commit()
    except SQLAlchemyError:
        return _fail_closed(state, payload, "failed to persist the audit record")

    return {
        **state,
        "audit_entry": {**(state.get("audit_entry") or {}), **payload},
    }
