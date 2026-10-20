"""SIGTERM stops new claims and requeues an in-flight job after the grace period."""

from __future__ import annotations

import signal
import threading
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.sql.dml import Update

from education_platform.core.config import Settings, get_settings
from education_platform.modules.generation.models import GenerationJob
from education_platform.modules.generation.types import GenerationJobStatus
from education_platform.workers.runner import _run_claimed, poll_once, run_forever
from education_platform.workers.shutdown import WorkerShutdown


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _LeaseSession:
    def __init__(self, job: dict[str, Any], job_id: Any, executed: list[int]) -> None:
        self._job = job
        self._job_id = job_id
        self._executed = executed

    def execute(self, statement: Update) -> _LeaseResult:
        params = statement.compile().params
        assert params["status"] is GenerationJobStatus.QUEUED
        assert params["locked_until"] is None
        assert params["status_1"] is GenerationJobStatus.RUNNING
        assert params["id_1"] == self._job_id
        self._executed.append(1)
        if self._job["status"] is GenerationJobStatus.RUNNING:
            self._job["status"] = GenerationJobStatus.QUEUED
            self._job["locked_until"] = None
        return _LeaseResult()

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class _LeaseResult:
    rowcount = 1


def test_shutdown_grace_defaults_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKER_SHUTDOWN_GRACE_SECONDS", raising=False)
    get_settings.cache_clear()
    try:
        assert Settings(_env_file=None).worker_shutdown_grace_seconds == 25.0
        state = WorkerShutdown(release=lambda *_args: None, exit=lambda _code: None)
        assert state.grace_seconds == 25.0
        monkeypatch.setenv("WORKER_SHUTDOWN_GRACE_SECONDS", "10")
        assert Settings(_env_file=None).worker_shutdown_grace_seconds == 10.0
        with pytest.raises(ValidationError):
            Settings(_env_file=None, worker_shutdown_grace_seconds=-1)
    finally:
        get_settings.cache_clear()


def test_signal_stops_claiming(monkeypatch: pytest.MonkeyPatch) -> None:
    state = WorkerShutdown(
        grace_seconds=25,
        clock=FakeClock(),
        release=lambda *_args: None,
        exit=lambda _code: None,
    )
    state.note_signal(signal.SIGTERM, None)

    def _unexpected_session() -> None:
        raise AssertionError("shutdown claimed a new job")

    monkeypatch.setattr(
        "education_platform.workers.runner._sync_session",
        _unexpected_session,
    )
    assert poll_once(shutdown=state) is None

    calls: list[int] = []

    def _poll() -> None:
        calls.append(1)

    run_forever(poll_interval_seconds=60, shutdown=state, poll=_poll)
    assert calls == []

    job_id = uuid4()
    ran: list[bool] = []
    released: list[object] = []
    stopped = WorkerShutdown(
        grace_seconds=25,
        clock=FakeClock(),
        release=lambda model, claimed_id: released.append((model, claimed_id)),
        exit=lambda _code: None,
    )
    stopped.note_signal(signal.SIGINT, None)
    assert _run_claimed(stopped, GenerationJob, job_id, lambda: ran.append(True)) is None
    assert ran == []
    assert released == [(GenerationJob, job_id)]


def test_signal_during_a_poll_does_not_claim_again() -> None:
    state = WorkerShutdown(
        grace_seconds=25,
        clock=FakeClock(),
        release=lambda *_args: None,
        exit=lambda _code: None,
    )
    calls: list[int] = []

    def _poll() -> object:
        calls.append(1)
        state.note_signal(signal.SIGTERM, None)
        return uuid4()

    run_forever(poll_interval_seconds=60, shutdown=state, poll=_poll)
    assert calls == [1]


def test_job_finishing_within_grace_is_marked_done(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    job_id = uuid4()
    job: dict[str, Any] = {
        "status": GenerationJobStatus.RUNNING,
        "locked_until": datetime.now(UTC),
    }
    exits: list[int] = []

    class _Session:
        def execute(self, _statement: object) -> None:
            raise AssertionError("finished job must keep its status")

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr("education_platform.workers.lease.sync_session", lambda: _Session())
    holder: list[WorkerShutdown] = []

    def _wait(_event: threading.Event, timeout: float) -> bool:
        assert timeout == 25
        clock.now += 5
        job["status"] = GenerationJobStatus.SUCCEEDED
        job["locked_until"] = None
        holder[0].finish_job()
        return True

    state = WorkerShutdown(grace_seconds=25, clock=clock, wait=_wait, exit=exits.append)
    holder.append(state)

    def _process() -> None:
        state.note_signal(signal.SIGTERM, None)
        state.watch()

    assert _run_claimed(state, GenerationJob, job_id, _process) == job_id
    assert job["status"] is GenerationJobStatus.SUCCEEDED
    assert clock.now == 5
    assert exits == []
    state.note_signal(signal.SIGINT, None)
    state.watch()
    assert job["status"] is GenerationJobStatus.SUCCEEDED
    assert exits == []


def test_job_past_grace_is_requeued(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    job_id = uuid4()
    job: dict[str, Any] = {
        "status": GenerationJobStatus.RUNNING,
        "locked_until": datetime.now(UTC),
    }
    executed: list[int] = []
    exits: list[int] = []

    def _session() -> _LeaseSession:
        return _LeaseSession(job, job_id, executed)

    monkeypatch.setattr("education_platform.workers.lease.sync_session", _session)
    waits: list[float] = []

    def _wait(_event: threading.Event, timeout: float) -> bool:
        waits.append(timeout)
        clock.now += timeout
        return False

    state = WorkerShutdown(grace_seconds=25, clock=clock, wait=_wait, exit=exits.append)

    def _process() -> None:
        state.note_signal(signal.SIGTERM, None)
        clock.now = 1
        state.note_signal(signal.SIGTERM, None)
        state.watch()

    _run_claimed(state, GenerationJob, job_id, _process)
    assert waits == [24]
    assert clock.now == 25
    assert job["status"] is GenerationJobStatus.QUEUED
    assert job["locked_until"] is None
    assert executed == [1]
    assert exits == [0]
    state.note_signal(signal.SIGINT, None)
    state.watch()
    assert executed == [1]
    assert exits == [0]


def test_signal_handlers_are_registered_on_the_child_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: dict[int, object] = {}

    def _register(sig: int, handler: object) -> None:
        registered[sig] = handler

    monkeypatch.setattr(signal, "signal", _register)
    started: list[str] = []

    class _Thread:
        def __init__(self, *, target: object, name: str, daemon: bool) -> None:
            assert name == "worker-shutdown"
            assert daemon is True
            assert target is not None

        def start(self) -> None:
            started.append("start")

    monkeypatch.setattr(
        "education_platform.workers.shutdown.threading.Thread",
        _Thread,
    )
    state = WorkerShutdown(
        grace_seconds=25,
        clock=FakeClock(),
        release=lambda *_args: None,
        exit=lambda _code: None,
    )
    state.install_signals()
    state.install_signals()
    assert registered[signal.SIGTERM] == state.note_signal
    assert registered[signal.SIGINT] == state.note_signal
    assert started == ["start"]
    state.note_signal(signal.SIGTERM, None)
    state.note_signal(signal.SIGINT, None)
    assert state.stop.is_set()
