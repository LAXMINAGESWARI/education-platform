"""Lean text-to-SQL run: parallel front, then the same security nodes.

Guards, intent routing, and a speculative ``generate_sql`` start together.
``execute_sql`` runs only after both guard verdicts have passed and, on a SQL
path, ``validate_sql`` and ``apply_role_scope`` have passed. A template or
at-risk match discards the speculative SQL. Repair is at most one attempt, and
a transport failure is not retried above the gateway.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Coroutine
from typing import Any

from education_platform.modules.text_to_sql import guards, nodes
from education_platform.modules.text_to_sql.compose_stream import (
    AskEvents,
    push_compose_stream,
    reset_compose_stream,
)
from education_platform.modules.text_to_sql.meter import (
    add_ms,
    ensure_meter,
    meter_payload,
    reset_meter,
    start_meter,
)
from education_platform.modules.text_to_sql.pipeline import LEAN, LEAN_MAX_RETRIES
from education_platform.modules.text_to_sql.run_store import active_store
from education_platform.modules.text_to_sql.state import (
    LLM_ERROR,
    SCHEMA_ERROR,
    TextToSQLState,
    error_category,
    format_error,
)


def _merge_audit(*parts: TextToSQLState | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for part in parts:
        if part:
            merged.update(part.get("audit_entry") or {})
    return merged


async def _timed(key: str, work: Awaitable[TextToSQLState]) -> TextToSQLState:
    ensure_meter()
    started = time.perf_counter()
    try:
        return await work
    finally:
        elapsed = int((time.perf_counter() - started) * 1000)
        add_ms(key, elapsed if elapsed > 0 else 0)


def _cancel(task: asyncio.Task[TextToSQLState]) -> None:
    if not task.done():
        task.cancel()


async def _join(task: asyncio.Task[TextToSQLState]) -> TextToSQLState | None:
    try:
        return await task
    except asyncio.CancelledError:
        return None


async def _speculative(state: TextToSQLState) -> TextToSQLState:
    """Filtered catalog, then SQL. The full catalog stays in front of the prompt."""
    loaded = await nodes.load_schema({**state, "pipeline": LEAN})
    if error_category(loaded.get("error")) == SCHEMA_ERROR:
        return loaded
    linked = await nodes.link_schema(loaded)
    full = loaded.get("schema_context") or ""
    hinted: TextToSQLState = {
        **linked,
        "schema_context": full,
        "pipeline": LEAN,
        "error": None,
        "generated_sql": None,
        "query_source": None,
    }
    return await nodes.generate_sql(hinted)


def _take_template(
    guard: TextToSQLState, intent: TextToSQLState, generated: TextToSQLState | None
) -> TextToSQLState:
    """Template SQL wins. Speculative SQL is dropped. A schema miss does not."""
    schema = ""
    if generated is not None and error_category(generated.get("error")) != SCHEMA_ERROR:
        schema = generated.get("schema_context") or ""
    return {
        **intent,
        "pipeline": LEAN,
        "generated_sql": intent.get("generated_sql"),
        "query_source": "template",
        "schema_context": schema,
        "retry_count": 0,
        "error": None,
        "audit_entry": _merge_audit(guard, intent, generated),
    }


def _take_service(guard: TextToSQLState, intent: TextToSQLState) -> TextToSQLState:
    return {
        **intent,
        "pipeline": LEAN,
        "generated_sql": None,
        "query_source": "service",
        "error": None,
        "audit_entry": _merge_audit(guard, intent),
    }


def _take_free_form(
    guard: TextToSQLState, intent: TextToSQLState, generated: TextToSQLState | None
) -> TextToSQLState:
    if generated is None:
        failure = format_error(LLM_ERROR, "generate_sql: speculative generation was cancelled")
        produced: TextToSQLState = {"error": failure}
    else:
        produced = generated
    return {
        **intent,
        "pipeline": LEAN,
        "generated_sql": produced.get("generated_sql"),
        "query_source": None,
        "schema_context": produced.get("schema_context") or "",
        "retry_count": produced.get("retry_count") or 0,
        "error": produced.get("error"),
        "audit_entry": _merge_audit(guard, intent, generated),
        "answer_criterion": None,
        "zero_result_is_valid": False,
        "empty_answer": None,
        "zero_probe_sql": {},
        "zero_probe_results": None,
    }


async def _stage(events: AskEvents | None, stage: str, status: str) -> None:
    if events is not None:
        await events.progress(stage, status)


class _AuditOnce:
    """One ``audit_log`` task for the run, including a cancelled stream."""

    def __init__(self) -> None:
        self._task: asyncio.Task[TextToSQLState] | None = None

    def schedule(self, state: TextToSQLState) -> asyncio.Task[TextToSQLState]:
        if self._task is None:
            self._task = asyncio.create_task(nodes.audit_log(state))
        return self._task

    async def wait(self, state: TextToSQLState) -> TextToSQLState:
        return await _protect(self.schedule(state))

    async def finish(self, executed: TextToSQLState | None) -> None:
        if self._task is None and executed is not None:
            self.schedule(executed)
        if self._task is not None and not self._task.done():
            await _protect(self._task)


async def _protect(task: asyncio.Task[TextToSQLState]) -> TextToSQLState:
    """Finish ``task`` even when this run is being cancelled."""
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        current = asyncio.current_task()
        pending = current.cancelling() if current is not None else 0
        if current is not None:
            while current.cancelling():
                current.uncancel()
        try:
            if not task.done():
                await task
            task.result()
        finally:
            if current is not None:
                for _ in range(pending):
                    current.cancel()
        raise


async def _refuse(
    state: TextToSQLState,
    events: AskEvents | None = None,
    audits: _AuditOnce | None = None,
) -> TextToSQLState:
    refused = await nodes.honest_refusal(state)
    logged = await (audits.wait(refused) if audits is not None else nodes.audit_log(refused))
    if events is not None:
        await events.refusal(logged.get("natural_answer") or "")
    return logged


async def _summarise(
    state: TextToSQLState, events: AskEvents | None, audits: _AuditOnce | None = None
) -> TextToSQLState:
    await _stage(events, "summarising", "running")
    hook = push_compose_stream(events) if events is not None else None
    try:
        composed = await _timed("compose_ms", nodes.compose_answer(state))
    finally:
        if hook is not None:
            reset_compose_stream(hook)
    logged = await (audits.wait(composed) if audits is not None else nodes.audit_log(composed))
    if logged.get("error"):
        await _stage(events, "summarising", "failed")
        if events is not None:
            await events.refusal(logged.get("natural_answer") or "")
        return logged
    await _stage(events, "summarising", "done")
    if events is not None:
        await events.final(
            logged.get("natural_answer") or "",
            logged.get("confidence"),
            logged.get("provenance"),
        )
    return logged


async def _after_rows(
    state: TextToSQLState, events: AskEvents | None, audits: _AuditOnce | None = None
) -> TextToSQLState:
    checked = await nodes.sanity_check(state)
    if checked.get("error"):
        await _stage(events, "running", "failed")
        return await _refuse(checked, events, audits)
    await _stage(events, "running", "done")
    return await _summarise(checked, events, audits)


def _needs_repair(state: TextToSQLState) -> bool:
    if state.get("validated_sql"):
        return False
    if error_category(state.get("error")) == LLM_ERROR:
        return False
    return state.get("retry_count", 0) < LEAN_MAX_RETRIES


async def _validate_once_repaired(state: TextToSQLState) -> TextToSQLState:
    checked = await nodes.validate_sql(state)
    if not _needs_repair(checked):
        return checked
    if not checked.get("schema_context"):
        loaded = await nodes.load_schema(checked)
        if error_category(loaded.get("error")) == SCHEMA_ERROR:
            return loaded
        linked = await nodes.link_schema(loaded)
        checked = {
            **linked,
            "schema_context": loaded.get("schema_context") or "",
            "pipeline": LEAN,
            "error": checked.get("error"),
            "generated_sql": checked.get("generated_sql"),
            "retry_count": checked.get("retry_count", 0),
            "query_source": checked.get("query_source"),
        }
    repaired = await _timed("generate_ms", nodes.generate_sql({**checked, "pipeline": LEAN}))
    if error_category(repaired.get("error")) == LLM_ERROR:
        return repaired
    return await nodes.validate_sql(repaired)


async def _note_executed(state: TextToSQLState, executed_box: dict[str, TextToSQLState]) -> None:
    executed_box["state"] = state
    request_id = state.get("stream_request_id")
    store = active_store()
    if store is None or not request_id:
        return
    await store.mark_sql_executed(str(state["user_id"]), str(request_id))


async def _sql_path(
    state: TextToSQLState,
    events: AskEvents | None = None,
    audits: _AuditOnce | None = None,
    executed_box: dict[str, TextToSQLState] | None = None,
) -> TextToSQLState:
    await _stage(events, "writing_query", "running")
    if error_category(state.get("error")) in {LLM_ERROR, SCHEMA_ERROR}:
        await _stage(events, "writing_query", "failed")
        return await _refuse(state, events, audits)
    await _stage(events, "writing_query", "done")
    await _stage(events, "validating", "running")
    validated = await _validate_once_repaired(state)
    if not validated.get("validated_sql"):
        await _stage(events, "validating", "failed")
        return await _refuse(validated, events, audits)
    scoped = await nodes.apply_role_scope(validated)
    if scoped.get("error"):
        await _stage(events, "validating", "failed")
        return await _refuse(scoped, events, audits)
    await _stage(events, "validating", "done")
    await _stage(events, "running", "running")
    executed = await _timed("execute_ms", nodes.execute_sql(scoped))
    if executed.get("error"):
        await _stage(events, "running", "failed")
        return await _refuse(executed, events, audits)
    if executed_box is not None:
        await _note_executed(executed, executed_box)
    return await _after_rows(executed, events, audits)


async def _service_path(
    state: TextToSQLState,
    events: AskEvents | None = None,
    audits: _AuditOnce | None = None,
    executed_box: dict[str, TextToSQLState] | None = None,
) -> TextToSQLState:
    await _stage(events, "running", "running")
    resolved = await _timed("execute_ms", nodes.resolve_at_risk(state))
    if resolved.get("error"):
        await _stage(events, "running", "failed")
        return await _refuse(resolved, events, audits)
    if executed_box is not None:
        executed_box["state"] = resolved
    return await _after_rows(resolved, events, audits)


def _spawn(key: str, work: Coroutine[Any, Any, TextToSQLState]) -> asyncio.Task[TextToSQLState]:
    return asyncio.create_task(_timed(key, work))


def _measured(state: TextToSQLState) -> TextToSQLState:
    retries = state.get("retry_count") or 0
    return {**state, "pipeline_metrics": meter_payload(int(retries))}


async def run_lean(state: TextToSQLState, events: AskEvents | None = None) -> TextToSQLState:
    """Run the lean pipeline. Does not execute SQL before the guards pass.

    ``events`` is set only by the progress stream. ``/ask`` leaves it unset, so
    the legacy response shape does not gain frames.
    """
    token = start_meter()
    audits = _AuditOnce()
    executed_box: dict[str, TextToSQLState] = {}
    try:
        initial: TextToSQLState = {**state, "pipeline": LEAN}
        await _stage(events, "checking", "running")
        await _stage(events, "understanding", "running")
        guard_task = _spawn("guard_ms", guards.run_data_guards(initial))
        intent_task = _spawn("intent_ms", nodes.intent_router(initial))
        generate_task = _spawn("generate_ms", _speculative(initial))

        guarded = await guard_task
        if guarded.get("error"):
            _cancel(intent_task)
            _cancel(generate_task)
            await _join(intent_task)
            await _join(generate_task)
            blocked: TextToSQLState = {**guarded, "pipeline": LEAN, "generated_sql": None}
            await _stage(events, "checking", "failed")
            return _measured(await _refuse(blocked, events, audits))

        await _stage(events, "checking", "done")
        intent_state = await intent_task
        await _stage(events, "understanding", "done")
        route = intent_state.get("intent_route")
        source = intent_state.get("query_source")
        if route == "template" and source == "service":
            _cancel(generate_task)
            await _join(generate_task)
            return _measured(
                await _service_path(
                    _take_service(guarded, intent_state), events, audits, executed_box
                )
            )
        if route == "template" and source == "template":
            # The template SQL is the one that executes. Keep a finished schema
            # load for a repair; cancel a generation that is still in flight.
            if not generate_task.done():
                _cancel(generate_task)
            generated = await _join(generate_task)
            return _measured(
                await _sql_path(
                    _take_template(guarded, intent_state, generated), events, audits, executed_box
                )
            )
        if not generate_task.done():
            generated = await generate_task
        else:
            generated = await _join(generate_task)
        return _measured(
            await _sql_path(
                _take_free_form(guarded, intent_state, generated), events, audits, executed_box
            )
        )
    finally:
        try:
            await audits.finish(executed_box.get("state"))
        finally:
            reset_meter(token)


__all__ = ["run_lean"]
