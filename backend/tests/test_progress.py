from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.progress_http import open_progress_server

from education_platform.core.config import get_settings
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.audit.models import AuditEvent
from education_platform.modules.auth.models import User
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.generation.models import ContentGenerationRun
from education_platform.modules.generation.schemas import GenerationJobOut, GenerationRunOut
from education_platform.modules.generation.types import RunPhase
from education_platform.modules.progress.close import (
    GenerationDisposition,
    generation_disposition,
    should_close,
)
from education_platform.modules.progress.sse import iter_sse
from education_platform.modules.progress.subscribe import HEARTBEAT_SECONDS, subscribe
from education_platform.modules.progress.types import (
    CloseFrame,
    ErrorFrame,
    EventId,
    HeartbeatFrame,
    SnapshotFrame,
    event_id_for,
    parse_subject_key,
    run_subject,
    version_subject,
)
from education_platform.modules.progress.wake import (
    PROGRESS_CHANNEL,
    WakeResult,
    publish_wake,
)
from education_platform.modules.rag.schemas import MaterialVersionStatusOut

SEEDED_SLUG = "rectangles_squares_properties"


def _run_out(*, phase: RunPhase, jobs: list[tuple[str, str]] = ()) -> GenerationRunOut:
    now = datetime(2026, 9, 13, tzinfo=UTC)
    return GenerationRunOut(
        id=uuid4(),
        topic_id=uuid4(),
        title="Unit source",
        phase=phase,
        target_item_count=80,
        submitted_by_user_id=uuid4(),
        intake_version_id=None,
        failure_reason=None,
        outline=None,
        jobs=[GenerationJobOut(kind=kind, status=status) for kind, status in jobs],
        created_at=now,
    )


def _principal() -> Principal:
    return Principal(
        user_id=uuid4(),
        institution_id=uuid4(),
        email="admin@example.com",
        roles=frozenset({"administrator"}),
        student_profile_id=None,
        status="active",
    )


def _scope(principal: Principal) -> Scope:
    return Scope(
        institution_id=principal.institution_id,
        roles=principal.roles,
        unrestricted=True,
        taught_offering_sections=frozenset(),
        enrolled_offering_sections=frozenset(),
        student_ids=frozenset(),
        self_student_id=None,
    )


class SequenceReader:
    def __init__(self, snapshots: list[GenerationRunOut | MaterialVersionStatusOut]) -> None:
        self._snapshots = list(snapshots)
        self.heals: list[bool] = []

    async def authorize_and_read(
        self,
        principal: Principal,
        scope: Scope,
        subject: object,
        *,
        heal: bool,
    ) -> GenerationRunOut | MaterialVersionStatusOut:
        del principal, scope, subject
        self.heals.append(heal)
        return self._snapshots.pop(0)


class FakeWakeWaiter:
    def __init__(self, results: list[WakeResult] | None = None) -> None:
        self._results = list(results or [])
        self.closed = False
        self.waits: list[float] = []

    async def wait(self, subject: object, timeout: float) -> WakeResult:
        del subject
        self.waits.append(timeout)
        if self._results:
            return self._results.pop(0)
        if timeout <= 0:
            return WakeResult.TIMED_OUT
        return WakeResult.NOTIFIED

    async def aclose(self) -> None:
        self.closed = True


class _BlockingHeartbeatWaiter:
    """Returns immediately for the zero-timeout drain and fails a 15s wait."""

    def __init__(self) -> None:
        self.waits: list[float] = []
        self.closed = False

    async def wait(self, subject: object, timeout: float) -> WakeResult:
        del subject
        self.waits.append(timeout)
        if timeout >= HEARTBEAT_SECONDS:
            raise AssertionError("heartbeat wait before the first byte")
        return WakeResult.TIMED_OUT

    async def aclose(self) -> None:
        self.closed = True


async def _one_frame(frame: HeartbeatFrame):
    yield frame


def test_parse_subject_key_round_trip() -> None:
    run_id = uuid4()
    version_id = uuid4()
    assert parse_subject_key(run_subject(run_id).key()) == run_subject(run_id)
    assert parse_subject_key(version_subject(version_id).key()) == version_subject(version_id)
    assert parse_subject_key("nope") is None
    assert parse_subject_key("run:not-a-uuid") is None
    assert parse_subject_key("other:00000000-0000-0000-0000-000000000000") is None


@pytest.mark.asyncio
async def test_iter_sse_encodes_heartbeat_error_and_close() -> None:
    snapshot = _run_out(phase=RunPhase.FAILED)

    async def frames():
        yield SnapshotFrame(
            event_id=EventId("run:1:abc"),
            snapshot=snapshot,
            close_after=True,
        )
        yield HeartbeatFrame()
        yield ErrorFrame(status=404, detail="gone")
        yield CloseFrame(reason="terminal")

    chunks = [chunk.decode() async for chunk in iter_sse(frames())]
    body = "".join(chunks)
    assert "event: snapshot" in body
    assert ": heartbeat" in body
    assert "event: error" in body
    assert '"detail":"gone"' in body
    assert "event: close" in body


def test_generating_is_never_a_close() -> None:
    empty = _run_out(phase=RunPhase.GENERATING, jobs=())
    busy = _run_out(phase=RunPhase.GENERATING, jobs=[("items", "running")])
    assert generation_disposition(empty) is GenerationDisposition.WORKER_MOVING
    assert generation_disposition(busy) is GenerationDisposition.WORKER_MOVING
    assert should_close(empty) is False
    assert should_close(busy) is False


def test_hitl_park_stays_open_rewrite_keeps_running() -> None:
    parked = _run_out(phase=RunPhase.OUTLINE_REVIEW, jobs=())
    rewrite = _run_out(phase=RunPhase.OUTLINE_REVIEW, jobs=[("rewrite_outline", "queued")])
    assert generation_disposition(parked) is GenerationDisposition.HITL_PARK
    assert generation_disposition(rewrite) is GenerationDisposition.REWRITE_RUNNING
    assert should_close(parked) is False
    assert should_close(rewrite) is False


@pytest.mark.parametrize(
    "phase",
    [RunPhase.PUBLISHED, RunPhase.FAILED, RunPhase.DISCARDED],
)
def test_true_terminal_closes(phase: RunPhase) -> None:
    snapshot = _run_out(phase=phase)
    assert generation_disposition(snapshot) is GenerationDisposition.TERMINAL
    assert should_close(snapshot) is True


def test_version_ready_closes() -> None:
    snapshot = MaterialVersionStatusOut(
        id=uuid4(),
        source_material_id=uuid4(),
        version_number=1,
        title="Lesson",
        lifecycle_status="ready",
        failure_reason=None,
        chunk_count=3,
        blob_content_type="application/pdf",
    )
    assert should_close(snapshot) is True


def test_version_processing_stays_open() -> None:
    snapshot = MaterialVersionStatusOut(
        id=uuid4(),
        source_material_id=uuid4(),
        version_number=1,
        title="Lesson",
        lifecycle_status="processing",
        failure_reason=None,
        chunk_count=0,
        blob_content_type="application/pdf",
    )
    assert should_close(snapshot) is False


@pytest.mark.asyncio
async def test_subscribe_emits_snapshot_then_close_for_failed_run() -> None:
    principal = _principal()
    snapshot = _run_out(phase=RunPhase.FAILED)
    reader = SequenceReader([snapshot])
    waiter = FakeWakeWaiter()
    frames = [
        frame
        async for frame in subscribe(
            principal,
            _scope(principal),
            run_subject(snapshot.id),
            reader=reader,
            wakes=waiter,
        )
    ]
    assert isinstance(frames[0], SnapshotFrame)
    assert frames[0].close_after is True
    assert isinstance(frames[1], CloseFrame)
    assert frames[1].reason == "terminal"
    assert reader.heals == [True]
    assert waiter.closed is True


@pytest.mark.asyncio
async def test_subscribe_heals_once_then_skips_unchanged() -> None:
    principal = _principal()
    moving = _run_out(phase=RunPhase.GENERATING, jobs=[("items", "running")])
    same = moving.model_copy()
    done = moving.model_copy(update={"phase": RunPhase.FAILED, "jobs": []})
    reader = SequenceReader([moving, same, done])
    waiter = FakeWakeWaiter([WakeResult.TIMED_OUT, WakeResult.TIMED_OUT, WakeResult.NOTIFIED])
    kinds = []
    async for frame in subscribe(
        principal,
        _scope(principal),
        run_subject(moving.id),
        reader=reader,
        wakes=waiter,
    ):
        kinds.append(type(frame).__name__)
    assert kinds == [
        "SnapshotFrame",
        "HeartbeatFrame",
        "SnapshotFrame",
        "CloseFrame",
    ]
    assert reader.heals == [True, False, False]


def test_publish_wake_notifies_unique_subject_keys() -> None:
    session = _ExecuteRecorder()
    run_id = uuid4()
    publish_wake(session, run_subject(run_id), run_subject(run_id), version_subject(run_id))
    assert len(session.calls) == 2
    payloads = [params["payload"] for _stmt, params in session.calls]
    assert payloads == [f"run:{run_id}", f"version:{run_id}"]
    assert all(params["channel"] == PROGRESS_CHANNEL for _stmt, params in session.calls)


class _ExecuteRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, str]]] = []

    def execute(self, statement: object, params: dict[str, str] | None = None) -> None:
        self.calls.append((statement, params or {}))


@pytest.fixture()
def admin_headers(client: TestClient) -> dict[str, str]:
    settings = get_settings()
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.demo_admin_email,
            "password": settings.demo_admin_password,
            "institution_name": "POC Demo School",
        },
    )
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["access_token"]}


def test_progress_run_requires_auth(client: TestClient) -> None:
    response = client.get(f"/api/v1/progress/runs/{uuid4()}")
    assert response.status_code == 401


def test_progress_version_forbids_student(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    del admin_headers
    settings = get_settings()
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.demo_student_email,
            "password": settings.demo_student_password,
            "institution_name": "POC Demo School",
        },
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    response = client.get(f"/api/v1/progress/versions/{uuid4()}", headers=headers)
    assert response.status_code == 403


def test_missing_run_is_json_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.get(f"/api/v1/progress/runs/{uuid4()}", headers=admin_headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "detail" in response.json()


def test_failed_run_stream_sends_snapshot_and_close(
    client: TestClient, seeded_db: Session, admin_headers: dict[str, str]
) -> None:
    settings = get_settings()
    topic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert topic is not None
    admin = seeded_db.scalar(select(User).where(User.email == settings.demo_admin_email))
    assert admin is not None
    run = ContentGenerationRun(
        topic_id=topic.topic_id,
        submitted_by_user_id=admin.id,
        title="Failed source",
        phase=RunPhase.FAILED,
        target_item_count=80,
        failure_reason="boom",
    )
    seeded_db.add(run)
    seeded_db.commit()

    with client.stream("GET", f"/api/v1/progress/runs/{run.id}", headers=admin_headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = b"".join(response.iter_bytes()).decode()
    assert "event: snapshot" in body
    assert '"phase":"failed"' in body
    assert "event: close" in body
    assert '"reason":"terminal"' in body


@pytest.mark.asyncio
async def test_unchanged_resume_flushes_connected_before_heartbeat_wait() -> None:
    principal = _principal()
    snapshot = _run_out(phase=RunPhase.GENERATING, jobs=[("items", "running")])
    subject = run_subject(snapshot.id)
    waiter = _BlockingHeartbeatWaiter()
    stream = subscribe(
        principal,
        _scope(principal),
        subject,
        last_event_id=event_id_for(subject, snapshot),
        reader=SequenceReader([snapshot]),
        wakes=waiter,
    )
    first = await asyncio.wait_for(anext(stream), timeout=1)
    assert isinstance(first, HeartbeatFrame)
    assert first.comment == "connected"
    assert waiter.waits == [0.0]
    encoded = [chunk async for chunk in iter_sse(_one_frame(first))]
    assert encoded == [b": connected\n\n"]
    await stream.aclose()
    assert waiter.closed is True


@pytest.mark.regression
def test_http_resume_first_byte_is_immediate(
    client: TestClient, seeded_db: Session, admin_headers: dict[str, str]
) -> None:
    del client
    settings = get_settings()
    topic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert topic is not None
    admin = seeded_db.scalar(select(User).where(User.email == settings.demo_admin_email))
    assert admin is not None
    run = ContentGenerationRun(
        topic_id=topic.topic_id,
        submitted_by_user_id=admin.id,
        title="Open source",
        phase=RunPhase.QA_REVIEW,
        target_item_count=80,
    )
    seeded_db.add(run)
    seeded_db.commit()
    url = f"/api/v1/progress/runs/{run.id}"
    with open_progress_server() as base, httpx.Client(base_url=base, timeout=5) as http:
        with http.stream("GET", url, headers=admin_headers) as response:
            assert response.status_code == 200
            buffered = bytearray()
            for chunk in response.iter_bytes():
                buffered.extend(chunk)
                if b"\n\n" in buffered and b"id:" in buffered:
                    break
        matched = re.search(rb"^id: (\S+)", bytes(buffered), re.MULTILINE)
        assert matched is not None
        started = time.perf_counter()
        with http.stream(
            "GET",
            url,
            headers={**admin_headers, "Last-Event-ID": matched.group(1).decode()},
        ) as response:
            assert response.status_code == 200
            first = next(response.iter_bytes())
        elapsed = time.perf_counter() - started
    assert elapsed < 1
    assert first.startswith(b": connected")


def test_reconnects_with_last_event_id_write_one_audit_event(
    client: TestClient, seeded_db: Session, admin_headers: dict[str, str]
) -> None:
    settings = get_settings()
    topic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert topic is not None
    admin = seeded_db.scalar(select(User).where(User.email == settings.demo_admin_email))
    assert admin is not None
    run = ContentGenerationRun(
        topic_id=topic.topic_id,
        submitted_by_user_id=admin.id,
        title="Reconnect source",
        phase=RunPhase.FAILED,
        target_item_count=80,
        failure_reason="boom",
    )
    seeded_db.add(run)
    seeded_db.commit()
    headers = {**admin_headers, "Last-Event-ID": f"run:{run.id}:stale"}
    for _ in range(4):
        with client.stream("GET", f"/api/v1/progress/runs/{run.id}", headers=headers) as response:
            assert response.status_code == 200
            body = b"".join(response.iter_bytes()).decode()
        assert "event: snapshot" in body or "event: close" in body

    seeded_db.expire_all()
    rows = seeded_db.scalars(
        select(AuditEvent).where(
            AuditEvent.event_type == "data.scoped_read",
            AuditEvent.entity_type == "progress.run",
            AuditEvent.actor_user_id == admin.id,
        )
    ).all()
    matched = [row for row in rows if row.payload.get("detail") == f"run:{run.id}"]
    assert len(matched) == 1
