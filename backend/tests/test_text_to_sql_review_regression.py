"""Review regressions for the lean ask stream. No paid model calls."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import education_platform.modules.text_to_sql.guards as guards_mod
import education_platform.modules.text_to_sql.nodes as nodes
import education_platform.modules.text_to_sql.stream as stream_mod
from education_platform.ai_platform.gateway.api import Completion
from education_platform.ai_platform.gateway.usage import TokenUsage
from education_platform.core.config import Settings
from education_platform.modules.audit.models import AuditEvent
from education_platform.modules.judge.types import DecisionResult, NoulAnswer
from education_platform.modules.text_to_sql import router as router_mod
from education_platform.modules.text_to_sql.lean import run_lean
from education_platform.modules.text_to_sql.nodes.audit_log import audit_log as write_audit
from education_platform.modules.text_to_sql.run_store import DbRunStore, MemoryRunStore
from education_platform.modules.text_to_sql.state import (
    OFF_TOPIC_REJECTED,
    TextToSQLState,
    error_category,
)
from education_platform.modules.text_to_sql.stream import HEARTBEAT, iter_ask_stream
from evals.guard.dataset import DEFAULT_DATASET, load_dataset
from evals.text_to_sql_pipeline import fixture as fixture_mod
from evals.text_to_sql_pipeline.compare import DEFAULT_REPLAY
from evals.text_to_sql_pipeline.replay import load_replay, replay_cases

_BACKEND = Path(__file__).resolve().parents[1]
_NAMESPACE = UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
_SECTION_SQL = """
SELECT sp.full_name
FROM student_profiles sp
WHERE sp.id IN (
  SELECT sse.student_id
  FROM student_subject_enrollments sse
  JOIN student_grade_enrollments sge ON sge.id = sse.grade_enrollment_id
  JOIN teaching_assignments ta
    ON ta.grade_subject_offering_id = sse.grade_subject_offering_id
  JOIN sections sec ON sec.id = sge.section_id
  WHERE sec.name = :section_name
    AND ta.teacher_user_id = :current_user_id
    AND ta.status = 'active'
    AND sse.status = 'active'
    AND (ta.section_id IS NULL OR ta.section_id = sge.section_id)
)
ORDER BY sp.full_name
"""
_ASK = "/api/v1/text-to-sql/ask"
_STREAM = "/api/v1/text-to-sql/ask/stream"
_FAST_HASHER = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
_SIX_A = ("Aisha Rahman", "Lina Farouk", "Omar Haddad")
_SIX_B = ("Hana Saleh", "Yusuf Ali")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key="test-key",
        text_to_sql_pipeline="legacy",
        text_to_sql_guard_batch=True,
        ai_gateway_text_to_sql=False,
    )


def _decision(injection: float, off_topic: float) -> DecisionResult:
    return DecisionResult(
        model="replay",
        answers={"injection": NoulAnswer(injection), "off_topic": NoulAnswer(off_topic)},
        input_tokens=1,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=1,
        request_id=None,
        provider=None,
    )


def _completion(parsed: dict[str, Any]) -> Completion:
    return Completion(text="{}", parsed_json=parsed, usage=TokenUsage(1, 1), served_model="test")


def _state(**overrides: Any) -> TextToSQLState:
    state: TextToSQLState = {
        "question": "How many students do I teach?",
        "user_id": "00000000-0000-0000-0000-000000000001",
        "user_role": "teacher",
        "institution_id": "00000000-0000-0000-0000-000000000002",
        "schema_context": "",
        "retry_count": 0,
        "audit_entry": {},
    }
    state.update(overrides)
    return state


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
        frames.append((event, json.loads(data) if data else None))
    return frames


async def _collect(state: TextToSQLState, request_id: str, **kwargs: Any) -> list[tuple[str, Any]]:
    options: dict[str, Any] = {
        "timeout_seconds": 5,
        "timeout_message": "timeout",
        "unexpected_message": "unexpected",
    }
    options.update(kwargs)
    raw = b""
    async for chunk in iter_ask_stream(state, request_id=request_id, **options):
        raw += chunk
    return _parse(raw)


def _as_async(fn: Any) -> Any:
    async def inner(state: TextToSQLState) -> TextToSQLState:
        result = fn(state)
        if asyncio.iscoroutine(result):
            return await result
        return result

    return inner


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, **handlers: Any) -> None:
    async def allow(state: TextToSQLState) -> TextToSQLState:
        return state

    async def audit(state: TextToSQLState) -> TextToSQLState:
        return state

    defaults: dict[str, Any] = {
        "run_data_guards": allow,
        "intent_router": lambda state: {**state, "intent_route": "free_form"},
        "load_schema": lambda state: {**state, "schema_context": "subjects(id)"},
        "link_schema": lambda state: state,
        "generate_sql": lambda state: {
            **state,
            "generated_sql": "SELECT 1 AS student_count",
            "schema_context": "subjects(id)",
            "error": None,
        },
        "validate_sql": lambda state: {
            **state,
            "validated_sql": state.get("generated_sql") or "SELECT 1",
            "error": None,
        },
        "apply_role_scope": lambda state: state,
        "execute_sql": lambda state: {
            **state,
            "query_result": [{"student_count": 1}],
            "result_row_count": 1,
            "error": None,
        },
        "sanity_check": lambda state: {**state, "confidence": "high"},
        "audit_log": audit,
        "compose_answer": lambda state: {
            **state,
            "natural_answer": "The student count is 1.",
            "confidence": "high",
            "provenance": "No tables were queried.",
        },
    }
    defaults.update(handlers)
    monkeypatch.setattr(guards_mod, "run_data_guards", _as_async(defaults["run_data_guards"]))
    for name in (
        "intent_router",
        "load_schema",
        "link_schema",
        "generate_sql",
        "validate_sql",
        "apply_role_scope",
        "execute_sql",
        "sanity_check",
        "audit_log",
        "compose_answer",
    ):
        monkeypatch.setattr(nodes, name, _as_async(defaults[name]))


def _ask_count(session: Session, user_id: UUID) -> int:
    session.expire_all()
    found = session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.actor_user_id == user_id, AuditEvent.event_type == "data.ask")
    )
    return int(found or 0)


@pytest.fixture
def bench(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> fixture_mod.BenchSchool:
    monkeypatch.setattr(fixture_mod, "hash_password", _FAST_HASHER.hash)
    school = fixture_mod.seed_school(db_session)
    db_session.commit()
    return school


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": fixture_mod.PASSWORD,
            "institution_name": fixture_mod.INSTITUTION_NAME,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.regression
async def test_recorded_fixture_matches_main_output_and_lean_tables() -> None:
    """Legacy /ask bytes follow the recorded main sentences. Lean may table them."""
    cases = {item["id"]: item for item in load_replay(DEFAULT_REPLAY)}
    assert cases["P2"]["legacy"]["natural_answer"] == cases["P2"]["compose_completion"]
    assert cases["P2"]["legacy"]["natural_answer"] != cases["P2"]["lean"]["natural_answer"]
    assert await replay_cases(load_replay(DEFAULT_REPLAY)) == 0


@pytest.mark.regression
def test_scope_and_allow_list_on_lean_ask_and_stream(
    client: TestClient, bench: fixture_mod.BenchSchool, monkeypatch: pytest.MonkeyPatch
) -> None:
    del bench
    monkeypatch.setattr(router_mod, "pipeline_mode", lambda _settings: "lean")

    async def guards(state: TextToSQLState) -> TextToSQLState:
        return state

    async def intent(state: TextToSQLState) -> TextToSQLState:
        return {
            **state,
            "intent_route": "template",
            "query_source": "template",
            "intent": "list_students_in_section",
            "intent_parameters": {"section_name": "6A"},
            "generated_sql": _SECTION_SQL,
            "error": None,
        }

    async def generate(state: TextToSQLState) -> TextToSQLState:
        return {**state, "generated_sql": "SELECT 1", "schema_context": "subjects(id)"}

    async def load(state: TextToSQLState) -> TextToSQLState:
        return {**state, "schema_context": "student_profiles(id, full_name)"}

    monkeypatch.setattr(guards_mod, "run_data_guards", guards)
    monkeypatch.setattr(nodes, "intent_router", intent)
    monkeypatch.setattr(nodes, "generate_sql", generate)
    monkeypatch.setattr(nodes, "load_schema", load)
    monkeypatch.setattr(nodes, "link_schema", load)

    student = _login(client, "aisha.bench@golden.school")
    denied = client.post(_ASK, json={"question": "Who is in 6A?"}, headers=student)
    assert denied.status_code == 403
    denied_stream = client.post(
        _STREAM,
        json={"question": "Who is in 6A?", "request_id": str(uuid4())},
        headers=student,
    )
    assert denied_stream.status_code == 403

    meera = _login(client, fixture_mod.TEACHER_EMAIL)
    body = {"question": "Who is in section 6A?"}
    asked = client.post(_ASK, json=body, headers=meera)
    assert asked.status_code == 200, asked.text
    answer = asked.json()["natural_answer"]
    for name in _SIX_A:
        assert name in answer
    for name in _SIX_B:
        assert name not in answer

    request_id = str(uuid4())
    with client.stream(
        "POST",
        _STREAM,
        json={**body, "request_id": request_id},
        headers=meera,
    ) as response:
        assert response.status_code == 200
        frames = _parse(b"".join(response.iter_bytes()))
    final = frames[-1][1]
    assert final is not None
    for name in _SIX_A:
        assert name in final["natural_answer"]
    for name in _SIX_B:
        assert name not in final["natural_answer"]

    noura = _login(client, fixture_mod.OTHER_TEACHER_EMAIL)
    hidden = client.post(_ASK, json=body, headers=noura)
    assert hidden.status_code == 200, hidden.text
    hidden_answer = hidden.json()["natural_answer"]
    for name in _SIX_A:
        assert name not in hidden_answer


@pytest.mark.regression
async def test_nothing_streams_before_guard_verdicts(monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = load_dataset(DEFAULT_DATASET)
    rows = [row for row in dataset.rows if row.in_scope("data")]
    assert len(rows) > 100
    by_text = {row.text: row for row in rows}
    settings = _settings()
    store = MemoryRunStore()

    async def post(**kwargs: Any) -> DecisionResult:
        row = by_text[kwargs["state"]["message"]]
        injection = 0.99 if row.label == "injection" else 0.0
        off_topic = 0.99 if row.label == "off_topic" else 0.0
        return _decision(injection, off_topic)

    async def allow(**_kwargs: Any) -> bool:
        return False

    async def complete(step: str, _messages: list[dict[str, Any]], **_kwargs: Any) -> Completion:
        if step == "text_to_sql.guard":
            return _completion({"injection": False})
        return _completion({"off_topic": False})

    monkeypatch.setattr(guards_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(guards_mod, "post_decisions", post)
    monkeypatch.setattr(guards_mod, "legacy_gpt_flag", allow)
    monkeypatch.setattr("education_platform.modules.text_to_sql.llm.complete", complete)
    _patch_pipeline(monkeypatch, run_data_guards=guards_mod.run_data_guards)

    for row in rows:
        request_id = str(uuid5(_NAMESPACE, row.id))
        frames = await _collect(
            _state(question=row.text, stream_request_id=request_id),
            request_id,
            store=store,
        )
        saw_verdict = False
        for name, payload in frames:
            if (
                name == "progress"
                and isinstance(payload, dict)
                and payload.get("stage") == "checking"
                and payload.get("status") in {"done", "failed"}
            ):
                saw_verdict = True
                break
            assert name == "progress"
            assert isinstance(payload, dict)
            assert payload["stage"] in {"checking", "understanding"}
            blob = json.dumps(payload)
            assert "SELECT" not in blob
            assert "writing_query" not in blob
            assert "answer_delta" not in blob
        assert saw_verdict


@pytest.mark.regression
async def test_uvicorn_emits_a_heartbeat_before_the_final_frame() -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    log_path = Path("/tmp/text-to-sql-heartbeat.log")
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "tests.text_to_sql_heartbeat_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=_BACKEND,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                log.flush()
                raise AssertionError(log_path.read_text(encoding="utf-8")[-2000:])
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                await asyncio.sleep(0.1)
        else:
            log.flush()
            raise AssertionError(log_path.read_text(encoding="utf-8")[-2000:])
        async with (
            httpx.AsyncClient(timeout=10) as client,
            client.stream("POST", f"http://127.0.0.1:{port}/stream") as response,
        ):
            assert response.status_code == 200
            raw = b""
            async for chunk in response.aiter_bytes():
                raw += chunk
                if b"event: final" in raw:
                    break
        assert b": heartbeat" in raw
        assert raw.index(b": heartbeat") < raw.index(b"event: final")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        log.close()


@pytest.mark.regression
async def test_disconnect_audits_once_and_reconnect_does_not_execute(
    bench: fixture_mod.BenchSchool,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    executes = {"n": 0}
    request_id = str(uuid4())
    store = DbRunStore(ttl_seconds=600)

    async def execute(state: TextToSQLState) -> TextToSQLState:
        executes["n"] += 1
        return {
            **state,
            "query_result": [{"student_count": 3}],
            "result_row_count": 1,
            "error": None,
        }

    async def compose(state: TextToSQLState) -> TextToSQLState:
        await asyncio.sleep(0.3)
        return {
            **state,
            "natural_answer": "The student count is 3.",
            "confidence": "high",
            "provenance": "No tables were queried.",
        }

    _patch_pipeline(monkeypatch, execute_sql=execute, compose_answer=compose)
    monkeypatch.setattr(nodes, "audit_log", write_audit)

    state = _state(
        user_id=str(bench.teacher_user_id),
        institution_id=str(bench.institution_id),
        stream_request_id=request_id,
    )
    stream = iter_ask_stream(
        state,
        request_id=request_id,
        timeout_seconds=5,
        timeout_message="timeout",
        unexpected_message="unexpected",
        store=store,
    )
    raw = b""
    with caplog.at_level(logging.INFO, logger=stream_mod.logger.name):
        async for chunk in stream:
            raw += chunk
            parsed = _parse(raw)
            if any(
                name == "progress"
                and isinstance(payload, dict)
                and payload.get("stage") == "running"
                and payload.get("status") == "done"
                for name, payload in parsed
            ):
                break
        await stream.aclose()
    assert any(
        record.levelno == logging.INFO and "client disconnected" in record.message
        for record in caplog.records
    )
    assert not any(record.exc_info for record in caplog.records if "disconnected" in record.message)

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        row = await store.load(str(bench.teacher_user_id), request_id)
        if row is not None and row.status == "done":
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("detached run did not finish")
    assert executes["n"] == 1
    assert _ask_count(db_session, bench.teacher_user_id) == 1

    again = await _collect(state, request_id, store=store)
    assert executes["n"] == 1
    assert _ask_count(db_session, bench.teacher_user_id) == 1
    assert again[-1][0] == "final"
    assert not any(name == "progress" for name, _ in again)

    other = _state(
        user_id=str(bench.other_teacher_user_id),
        institution_id=str(bench.institution_id),
        stream_request_id=request_id,
    )
    await _collect(other, request_id, store=store)
    assert executes["n"] == 2
    meera_again = await _collect(state, request_id, store=store)
    assert executes["n"] == 2
    assert meera_again[-1][0] == "final"


@pytest.mark.regression
async def test_inflight_reconnect_follows_and_does_not_execute_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executes = {"n": 0}
    entered = asyncio.Event()
    release = asyncio.Event()
    request_id = str(uuid4())
    store = MemoryRunStore()

    async def execute(state: TextToSQLState) -> TextToSQLState:
        executes["n"] += 1
        entered.set()
        await release.wait()
        return {
            **state,
            "query_result": [{"student_count": 1}],
            "result_row_count": 1,
            "error": None,
        }

    _patch_pipeline(monkeypatch, execute_sql=execute)
    state = _state(stream_request_id=request_id)

    async def first() -> list[tuple[str, Any]]:
        return await _collect(
            state, request_id, store=store, heartbeat_seconds=0.05, timeout_seconds=5
        )

    owner = asyncio.create_task(first())
    await asyncio.wait_for(entered.wait(), timeout=2)

    async def follow() -> bytes:
        raw = b""
        async for chunk in iter_ask_stream(
            state,
            request_id=request_id,
            timeout_seconds=5,
            timeout_message="timeout",
            unexpected_message="unexpected",
            heartbeat_seconds=0.05,
            store=store,
        ):
            raw += chunk
            if chunk == HEARTBEAT:
                release.set()
        return raw

    followed = await asyncio.wait_for(follow(), timeout=5)
    owner_frames = await asyncio.wait_for(owner, timeout=5)
    assert executes["n"] == 1
    assert b": heartbeat" in followed
    assert b"event: final" in followed
    assert owner_frames[-1][0] == "final"
    replay = await _collect(state, request_id, store=store)
    assert executes["n"] == 1
    assert replay[-1][0] == "final"


@pytest.mark.regression
async def test_jev_pass_and_gpt_off_topic_refuses_before_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    executed = {"n": 0}

    async def post(**_kwargs: Any) -> DecisionResult:
        return _decision(0.0, 0.0)

    async def gpt(**kwargs: Any) -> bool:
        return kwargs.get("field") == "off_topic"

    async def complete(step: str, _messages: list[dict[str, Any]], **_kwargs: Any) -> Completion:
        if step == "text_to_sql.guard":
            return _completion({"injection": False})
        if step == "text_to_sql.validator":
            return _completion({"off_topic": True})
        raise AssertionError(step)

    async def execute(state: TextToSQLState) -> TextToSQLState:
        executed["n"] += 1
        return state

    monkeypatch.setattr(guards_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(guards_mod, "post_decisions", post)
    monkeypatch.setattr(guards_mod, "legacy_gpt_flag", gpt)
    monkeypatch.setattr("education_platform.modules.text_to_sql.llm.complete", complete)
    _patch_pipeline(monkeypatch, run_data_guards=guards_mod.run_data_guards, execute_sql=execute)

    result = await run_lean(_state())
    assert error_category(result.get("error")) == OFF_TOPIC_REJECTED
    assert executed["n"] == 0
    assert (result.get("audit_entry") or {}).get("guard_stage") == "gpt"

    request_id = str(uuid4())
    frames = await _collect(
        _state(stream_request_id=request_id),
        request_id,
        store=MemoryRunStore(),
    )
    stages = [
        f"{payload['stage']}/{payload['status']}"
        for name, payload in frames
        if name == "progress" and isinstance(payload, dict)
    ]
    assert "checking/failed" in stages
    assert "writing_query/running" not in stages
    assert frames[-1][0] == "refusal"
    assert executed["n"] == 0


@pytest.mark.regression
async def test_cancelled_stream_logs_disconnect_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    started = asyncio.Event()

    async def hang(state: TextToSQLState, events: Any = None) -> TextToSQLState:
        await events.progress("checking", "running")
        started.set()
        await asyncio.Event().wait()
        return state

    monkeypatch.setattr(stream_mod, "run_lean", hang)
    store = MemoryRunStore()

    async def drive() -> None:
        async for _chunk in iter_ask_stream(
            _state(),
            request_id=str(uuid4()),
            timeout_seconds=5,
            timeout_message="timeout",
            unexpected_message="unexpected",
            store=store,
        ):
            pass

    task = asyncio.create_task(drive())
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0.05)
    with caplog.at_level(logging.DEBUG, logger=stream_mod.logger.name):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
    matches = [record for record in caplog.records if "client disconnected" in record.message]
    assert matches
    assert all(record.levelno == logging.INFO and record.exc_info is None for record in matches)
    assert "Traceback" not in caplog.text
    pending = list(stream_mod._BACKGROUND)
    for background in pending:
        background.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
