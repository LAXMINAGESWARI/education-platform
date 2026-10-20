"""Progress stream for one lean text-to-SQL ask.

Frames follow the shared envelope. Nothing from SQL, the schema, or result rows
is written before the guard and validation gates the design doc names.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import education_platform.modules.text_to_sql.guards as guards_mod
import education_platform.modules.text_to_sql.nodes as nodes
import education_platform.modules.text_to_sql.stream as stream_mod
from education_platform.api.deps import get_current_user_detached
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.text_to_sql import router as router_mod
from education_platform.modules.text_to_sql.run_store import MemoryRunStore
from education_platform.modules.text_to_sql.state import (
    INJECTION_BLOCKED,
    TextToSQLState,
    format_error,
)
from education_platform.modules.text_to_sql.stream import iter_ask_stream

_USER = "00000000-0000-0000-0000-000000000001"
_INST = "00000000-0000-0000-0000-000000000002"
_REQUEST = "2f1c0c2e-7b1a-4e0a-9c3d-1a2b3c4d5e6f"
_SECRET_SQL = "SELECT secret_column FROM secret_table"
_STREAM_URL = "/api/v1/text-to-sql/ask/stream"


@pytest.fixture(autouse=True)
def _memory_store(monkeypatch: pytest.MonkeyPatch) -> MemoryRunStore:
    """Stream tests share no Postgres row. One store per test."""
    store = MemoryRunStore()
    monkeypatch.setattr(stream_mod, "default_store", lambda: store)
    return store


def _state(**overrides: Any) -> TextToSQLState:
    state: TextToSQLState = {
        "question": "How many students do I teach?",
        "user_id": _USER,
        "user_role": "teacher",
        "institution_id": _INST,
        "schema_context": "",
        "retry_count": 0,
        "audit_entry": {},
        "stream_request_id": _REQUEST,
    }
    state.update(overrides)
    return state


def _principal(*roles: str) -> Principal:
    return Principal(
        user_id=UUID(_USER),
        institution_id=UUID(_INST),
        email="meera.krishnan@alnoor.school",
        roles=frozenset(roles or ("teacher",)),
        student_profile_id=None,
        status="active",
    )


def _parse(raw: bytes) -> list[tuple[str, dict[str, Any] | None]]:
    frames: list[tuple[str, dict[str, Any] | None]] = []
    for block in raw.split(b"\n\n"):
        if not block:
            continue
        if block == b": heartbeat":
            frames.append(("heartbeat", None))
            continue
        event = ""
        data = ""
        for line in block.decode().split("\n"):
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        frames.append((event, json.loads(data)))
    return frames


async def _collect(**kwargs: Any) -> list[tuple[str, dict[str, Any] | None]]:
    options: dict[str, Any] = {
        "timeout_seconds": 5,
        "timeout_message": router_mod._TIMEOUT_ANSWER,
        "unexpected_message": router_mod._UNEXPECTED_FAILURE_ANSWER,
    }
    options.update(kwargs)
    raw = b""
    async for chunk in iter_ask_stream(_state(), request_id=_REQUEST, **options):
        raw += chunk
    return _parse(raw)


async def _audit_ok(state: TextToSQLState) -> TextToSQLState:
    return state


def _patch_front(monkeypatch: pytest.MonkeyPatch, **handlers: Any) -> None:
    defaults = {
        "run_data_guards": lambda state: _done(state),
        "intent_router": lambda state: _done({**state, "intent_route": "free_form"}),
        "load_schema": lambda state: _done({**state, "schema_context": "institutions(id)"}),
        "link_schema": lambda state: _done(state),
        "generate_sql": lambda state: _done({**state, "generated_sql": _SECRET_SQL, "error": None}),
        "validate_sql": lambda state: _done({**state, "validated_sql": "SELECT 1", "error": None}),
        "apply_role_scope": lambda state: _done(state),
        "execute_sql": lambda state: _done(state),
        "sanity_check": lambda state: _done({**state, "confidence": "high"}),
        "resolve_at_risk": lambda state: _done(state),
        "audit_log": _audit_ok,
    }
    defaults.update(handlers)
    monkeypatch.setattr(guards_mod, "run_data_guards", defaults["run_data_guards"])
    for name in (
        "intent_router",
        "load_schema",
        "link_schema",
        "generate_sql",
        "validate_sql",
        "apply_role_scope",
        "execute_sql",
        "sanity_check",
        "resolve_at_risk",
        "audit_log",
    ):
        monkeypatch.setattr(nodes, name, defaults[name])


def _done(state: TextToSQLState) -> Any:
    async def _inner() -> TextToSQLState:
        return state

    return _inner()


def _rows() -> list[dict[str, Any]]:
    return [{"subject": "Mathematics", "score": 3}, {"subject": "Science", "score": 4}]


async def test_happy_path_emits_stages_then_table_then_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def execute(state: TextToSQLState) -> TextToSQLState:
        return {**state, "query_result": _rows(), "result_row_count": 2, "error": None}

    _patch_front(monkeypatch, execute_sql=execute)

    frames = await _collect()
    progress = [payload for name, payload in frames if name == "progress"]
    stages = [(item["stage"], item["status"]) for item in progress]

    assert stages == [
        ("checking", "running"),
        ("understanding", "running"),
        ("checking", "done"),
        ("understanding", "done"),
        ("writing_query", "running"),
        ("writing_query", "done"),
        ("validating", "running"),
        ("validating", "done"),
        ("running", "running"),
        ("running", "done"),
        ("summarising", "running"),
        ("summarising", "done"),
    ]
    assert [item["seq"] for item in progress] == list(range(1, len(progress) + 1))
    assert progress[0]["v"] == 1
    assert progress[0]["kind"] == "text_to_sql"
    assert progress[0]["subject_id"] == _REQUEST
    assert progress[0]["message"] == "Checking your question"
    assert progress[2]["message"] is None
    assert progress[0]["percent"] is None
    assert progress[0]["current"] is None
    assert progress[0]["total"] is None
    assert progress[0]["unit"] is None
    assert progress[0]["updated_at"].endswith("Z")

    table_at = next(index for index, (name, _) in enumerate(frames) if name == "table")
    summarising_done = next(
        index
        for index, (name, payload) in enumerate(frames)
        if name == "progress"
        and payload is not None
        and payload["status"] == "done"
        and payload["stage"] == "summarising"
    )
    running_done = next(
        index
        for index, (name, payload) in enumerate(frames)
        if name == "progress"
        and payload is not None
        and payload["stage"] == "running"
        and payload["status"] == "done"
    )
    assert running_done < table_at < summarising_done
    table = frames[table_at][1]
    assert table is not None
    assert table["columns"] == ["Subject", "Score"]
    assert table["rows"] == [["Mathematics", 3], ["Science", 4]]
    assert "answer_delta" not in {name for name, _ in frames}
    final = frames[-1]
    assert final[0] == "final"
    assert final[1] is not None
    assert final[1]["confidence"] == "high"
    assert "subjects" not in (final[1]["provenance"] or "")
    assert _SECRET_SQL not in json.dumps(frames)


async def test_guard_refusal_stops_before_sql_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    async def guards(state: TextToSQLState) -> TextToSQLState:
        return {**state, "error": format_error(INJECTION_BLOCKED, _SECRET_SQL)}

    async def generate(state: TextToSQLState) -> TextToSQLState:
        return {**state, "generated_sql": _SECRET_SQL, "schema_context": "secret_table(secret)"}

    _patch_front(monkeypatch, run_data_guards=guards, generate_sql=generate)

    frames = await _collect()
    names = [name for name, _ in frames]
    assert names == ["progress", "progress", "progress", "refusal"]
    assert [
        payload["stage"] + "/" + payload["status"]
        for _, payload in frames
        if payload and "stage" in payload
    ] == [
        "checking/running",
        "understanding/running",
        "checking/failed",
    ]
    refusal = frames[-1][1]
    assert refusal is not None
    assert refusal["message"].startswith("I can't process that request")
    blob = json.dumps(frames)
    assert _SECRET_SQL not in blob
    assert "secret_table" not in blob
    assert "INJECTION_BLOCKED" not in blob
    assert "final" not in names
    assert "table" not in names
    assert "answer_delta" not in names


async def test_validation_refusal_emits_no_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    async def validate(state: TextToSQLState) -> TextToSQLState:
        return {
            **state,
            "error": format_error("VALIDATION_ERROR", _SECRET_SQL),
            "retry_count": 1,
            "validated_sql": None,
        }

    async def execute(state: TextToSQLState) -> TextToSQLState:
        raise AssertionError("execute_sql ran after validation failed")

    _patch_front(monkeypatch, validate_sql=validate, execute_sql=execute)

    frames = await _collect()
    stages = [
        (payload["stage"], payload["status"])
        for name, payload in frames
        if name == "progress" and payload is not None
    ]
    assert ("validating", "failed") in stages
    assert ("running", "running") not in stages
    assert frames[-1][0] == "refusal"
    assert "table" not in {name for name, _ in frames}
    assert _SECRET_SQL not in json.dumps(frames)


async def test_service_path_skips_sql_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    async def intent(state: TextToSQLState) -> TextToSQLState:
        return {**state, "intent_route": "template", "query_source": "service"}

    async def generate(state: TextToSQLState) -> TextToSQLState:
        return {**state, "generated_sql": _SECRET_SQL}

    async def resolve(state: TextToSQLState) -> TextToSQLState:
        return {**state, "query_result": _rows(), "result_row_count": 2, "error": None}

    _patch_front(monkeypatch, intent_router=intent, generate_sql=generate, resolve_at_risk=resolve)

    frames = await _collect()
    stages = [
        payload["stage"] for name, payload in frames if name == "progress" and payload is not None
    ]
    assert "writing_query" not in stages
    assert "validating" not in stages
    assert "running" in stages
    assert frames[-1][0] == "final"
    assert _SECRET_SQL not in json.dumps(frames)


async def test_unexpected_exception_is_one_generic_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def explode(state: TextToSQLState, events: Any = None) -> TextToSQLState:
        raise RuntimeError(_SECRET_SQL)

    monkeypatch.setattr(stream_mod, "run_lean", explode)

    frames = await _collect()
    assert [name for name, _ in frames] == ["error"]
    error = frames[0][1]
    assert error is not None
    assert error["message"] == router_mod._UNEXPECTED_FAILURE_ANSWER
    assert _SECRET_SQL not in json.dumps(frames)


async def test_timeout_emits_the_ask_timeout_string(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang(state: TextToSQLState, events: Any = None) -> TextToSQLState:
        await asyncio.Event().wait()
        return state

    monkeypatch.setattr(stream_mod, "run_lean", hang)

    frames = await _collect(timeout_seconds=0.01)
    assert [name for name, _ in frames] == ["error"]
    error = frames[0][1]
    assert error is not None
    assert error["message"] == router_mod._TIMEOUT_ANSWER


async def test_heartbeat_does_not_advance_seq_or_drop_the_next_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()

    async def paced(state: TextToSQLState, events: Any = None) -> TextToSQLState:
        await events.progress("checking", "running")
        await release.wait()
        await events.progress("checking", "done")
        await events.final("Done.", "high", None)
        return state

    monkeypatch.setattr(stream_mod, "run_lean", paced)
    clock = {"now": 0.0}
    beats = {"n": 0}

    async def wait(tasks: set[asyncio.Task[Any]], timeout: float) -> set[asyncio.Task[Any]]:
        await asyncio.sleep(0)
        getter = next(iter(tasks))
        if getter.done():
            return {getter}
        clock["now"] += timeout
        beats["n"] += 1
        if beats["n"] == 1:
            release.set()
        return set()

    frames = await _collect(
        heartbeat_seconds=15,
        clock=lambda: clock["now"],
        wait=wait,
    )
    names = [name for name, _ in frames]
    assert "heartbeat" in names
    progress = [payload for name, payload in frames if name == "progress"]
    assert [item["seq"] for item in progress] == [1, 2]
    assert names.index("heartbeat") < names.index("final")
    assert frames[-1][0] == "final"


def test_stream_route_rejects_non_teachers_and_streams_the_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def paced(state: TextToSQLState, events: Any = None) -> TextToSQLState:
        assert state["stream_request_id"] == _REQUEST
        assert state["user_role"] == "teacher"
        await events.progress("checking", "running")
        await events.final("Done.", None, None)
        return state

    monkeypatch.setattr(stream_mod, "run_lean", paced)
    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")

    body = {"question": "How many students do I teach?", "request_id": _REQUEST}
    with TestClient(app) as client:
        missing = client.post(_STREAM_URL, json=body)
        assert missing.status_code == 401

        app.dependency_overrides[get_current_user_detached] = lambda: _principal("student")
        forbidden = client.post(
            _STREAM_URL, json=body, headers={"last-event-id": "99", "authorization": "Bearer x"}
        )
        assert forbidden.status_code == 403
        assert forbidden.headers["content-type"].startswith("application/json")

        app.dependency_overrides[get_current_user_detached] = lambda: _principal("teacher")
        with client.stream(
            "POST",
            _STREAM_URL,
            json=body,
            headers={"last-event-id": "99", "authorization": "Bearer x"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers["x-accel-buffering"] == "no"
            frames = _parse(b"".join(response.iter_bytes()))

    assert frames[0][0] == "progress"
    assert frames[0][1] is not None
    assert frames[0][1]["seq"] == 1
    assert frames[0][1]["subject_id"] == _REQUEST
    assert frames[-1][0] == "final"
    assert frames[-1][1] == {
        "natural_answer": "Done.",
        "confidence": None,
        "provenance": None,
    }

    invalid = TestClient(app).post(_STREAM_URL, json={"question": "How many?"})
    assert invalid.status_code == 422
