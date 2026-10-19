"""HTTP endpoint for the text-to-SQL ("ask-the-data") pipeline.

**Teacher-only, deliberately.** `require_role("teacher")` (the same reusable role-gate
dependency every other role-restricted endpoint in this codebase already uses — see
`api.deps.require_role`, used exactly this way by e.g. `insights.router`) rejects with 403
before the route handler body runs at all, so a non-teacher request never reaches
`_GRAPH.ainvoke(...)` — not a silent no-op, and not something routed into the graph to be
refused there. Student, administrator, and parent access are each a **separate, not-yet-made
design decision**, not an oversight: a student's own-data scope, an admin's institution-wide
scope, and a parent's scope in particular (which has no existing design or test coverage
anywhere in this codebase — `authorization.scope.Scope` has no parent concept at all) would
each need their own review before this endpoint should serve them. Widening the allow-list
here is a deliberate future change, not a bug to fix.

**Identity**: `question` is the *only* field this endpoint reads from the request body.
`user_id`/`user_role`/`institution_id` come only from `principal` (`api.deps.Principal`,
resolved from the verified JWT by `require_role`/`get_current_user`) — the same identity
rule Tasks 6-10 enforce inside the pipeline, applied one layer further out. `user_role` is
hardcoded to `"teacher"` here rather than read off `principal.roles`: this endpoint is the
teacher capability specifically, so the pipeline should apply teacher scoping even for a
principal who happens to also hold another role, not whichever role happened to be "first."

**The compiled graph is built once, at import time** (`_GRAPH`), not per request.
`build_text_to_sql_graph()` takes no per-request arguments — every node reads identity from
`state`, not from a closure. The policy assistant graph is compiled the same way
(`assistant.graph.get_assistant_graph()`): `user_id` / `institution_id` / `roles` ride on
each turn's state. A compiled `StateGraph` is just wiring — a fixed set of node functions and edges
— with no per-invocation mutable state of its own (no checkpointer is configured here), so
it's safe to share across concurrent requests, and re-registering every node/edge on every
single call would be pure overhead for no benefit.

**Response shape is exactly three fields**: `natural_answer`, `confidence`, `provenance`.
`generated_sql`, `validated_sql`, `query_result`, and `audit_entry` are never included —
enforced structurally, not just by omission: the handler builds an `AskOut` instance
field-by-field rather than ever doing `AskOut(**result)` or returning the raw graph state,
so there is no code path that could accidentally widen the response to include an internal
field later.

**Outermost timeout/failure net.** Task 11 documents `honest_refusal` as provably
exception-free, and every failure category upstream of it is designed to route there rather
than raise — but this is the outermost layer of the whole system, the one place with no
further catch above it, so it does not trust either guarantee blindly. `asyncio.wait_for`
bounds the whole graph run; both a timeout and any exception that still escapes (a bug, not
an expected path) are caught here and turned into a generic, no-internal-detail HTTP error —
the same "real detail to logs, generic detail to the response" split `execute_sql` (Task 7)
and `audit_log` (Task 10) already established, applied one layer further out.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from education_platform.api.deps import Principal, require_role
from education_platform.modules.text_to_sql.graph import build_text_to_sql_graph
from education_platform.modules.text_to_sql.state import TextToSQLState

logger = logging.getLogger(__name__)

router = APIRouter(tags=["text-to-sql"])

# See module docstring: built once, reused for every request.
_GRAPH = build_text_to_sql_graph()

# Generous enough for a few LLM round-trips (a retry or two through generate_sql, plus
# compose_answer) and execute_sql's own 5s DB statement timeout, without leaving a hung
# OpenRouter call able to tie up a request indefinitely -- nothing upstream of this
# endpoint enforces an overall wall-clock budget on its own.
_GRAPH_TIMEOUT_SECONDS: Final[float] = 60.0
_RECURSION_LIMIT: Final[int] = 25

_TIMEOUT_ANSWER: Final[str] = "That took too long to answer — please try again."
_UNEXPECTED_FAILURE_ANSWER: Final[str] = "Something went wrong on our end — please try again."


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AskOut(BaseModel):
    natural_answer: str
    confidence: str | None
    provenance: str | None


def _initial_state(*, question: str, principal: Principal) -> TextToSQLState:
    return {
        "question": question,
        "user_id": str(principal.user_id),
        "user_role": "teacher",
        "institution_id": str(principal.institution_id),
        "schema_context": "",
        "query_source": None,
        "generated_sql": None,
        "validated_sql": None,
        "retry_count": 0,
        "query_result": None,
        "result_row_count": None,
        "natural_answer": None,
        "confidence": None,
        "provenance": None,
        "error": None,
        "audit_entry": None,
    }


@router.post("/text-to-sql/ask", response_model=AskOut)
async def ask(
    body: AskIn,
    principal: Principal = Depends(require_role("teacher")),
) -> AskOut:
    """Answer a natural-language question about the caller's own teaching scope.

    Teacher-only for now — see module docstring for why student/admin/parent access is a
    deliberately separate, unmade decision rather than an oversight.
    """
    initial = _initial_state(question=body.question, principal=principal)

    try:
        result = await asyncio.wait_for(
            _GRAPH.ainvoke(initial, config={"recursion_limit": _RECURSION_LIMIT}),
            timeout=_GRAPH_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.error(
            "text_to_sql.ask: graph run exceeded %.0fs, user_id=%s",
            _GRAPH_TIMEOUT_SECONDS,
            principal.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=_TIMEOUT_ANSWER,
        ) from None
    except Exception:
        # Should not happen — Task 11's honest_refusal is designed to catch every failure
        # category before it becomes a raised exception — but this is the outermost layer
        # with nothing further to catch it, so an unexpected escape still gets a generic,
        # no-internal-detail response rather than a raw 500 with a stack trace attached.
        logger.error(
            "text_to_sql.ask: unhandled exception escaped the graph, user_id=%s",
            principal.user_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_UNEXPECTED_FAILURE_ANSWER,
        ) from None

    return AskOut(
        natural_answer=result.get("natural_answer") or _UNEXPECTED_FAILURE_ANSWER,
        confidence=result.get("confidence"),
        provenance=result.get("provenance"),
    )
