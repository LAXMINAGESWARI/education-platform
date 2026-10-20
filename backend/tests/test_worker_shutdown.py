"""SIGTERM stops new claims and requeues an in-flight job after the grace period."""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from education_platform.core.config import Settings, get_settings
from education_platform.modules.generation.models import GenerationJob
from education_platform.modules.generation.types import GenerationJobStatus
from education_platform.workers.lease import OwnedLease, close_session, release_lease
from education_platform.workers.runner import _run_claimed, poll_once, run_forever
from education_platform.workers.shutdown import (
    WorkerShutdown,
    _interrupt_job,
    clear_interrupt_request,
    interrupt_after_release_requested,
    request_interrupt_after_release,
)
from test_worker_lease import JobRow, ReleaseSession


@pytest.fixture(autouse=True)
def _reset_interrupt_request() -> Iterator[None]:
    clear_interrupt_request()
    yield
    clear_interrupt_request()


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _lease(
    job_id: object | None = None,
    *,
    attempts: int = 1,
    locked_until: datetime | None = None,
) -> OwnedLease:
    return OwnedLease(
        model=GenerationJob,
        job_id=job_id or uuid4(),
        attempts=attempts,
        locked_until=locked_until or datetime(2026, 10, 20, tzinfo=UTC),
    )


def test_shutdown_grace_defaults_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKER_SHUTDOWN_GRACE_SECONDS", raising=False)
    get_settings.cache_clear()
    try:
        assert Settings(_env_file=None).worker_shutdown_grace_seconds == 25.0
        state = WorkerShutdown(release=lambda _lease: None, exit=lambda _code: None)
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
        release=lambda _lease: None,
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

    lease = _lease()
    ran: list[bool] = []
    released: list[OwnedLease] = []
    stopped = WorkerShutdown(
        grace_seconds=25,
        clock=FakeClock(),
        release=released.append,
        exit=lambda _code: None,
    )
    stopped.note_signal(signal.SIGINT, None)
    assert _run_claimed(stopped, lease, lambda: ran.append(True)) is None
    assert ran == []
    assert released == [lease]


def test_signal_during_a_poll_does_not_claim_again() -> None:
    state = WorkerShutdown(
        grace_seconds=25,
        clock=FakeClock(),
        release=lambda _lease: None,
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
    lease = _lease()
    job = {"status": GenerationJobStatus.RUNNING}
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
        holder[0].finish_job()
        return True

    state = WorkerShutdown(grace_seconds=25, clock=clock, wait=_wait, exit=exits.append)
    holder.append(state)

    def _process() -> None:
        state.note_signal(signal.SIGTERM, None)
        state.watch()

    assert _run_claimed(state, lease, _process) == lease.job_id
    assert job["status"] is GenerationJobStatus.SUCCEEDED
    assert clock.now == 5
    assert exits == []
    state.note_signal(signal.SIGINT, None)
    state.watch()
    assert job["status"] is GenerationJobStatus.SUCCEEDED
    assert exits == []


def test_job_past_grace_is_requeued_and_refunded(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    job_id = uuid4()
    locked_until = datetime(2026, 10, 20, tzinfo=UTC)
    row = JobRow(
        job_id,
        status=GenerationJobStatus.RUNNING,
        attempts=1,
        locked_until=locked_until,
    )
    session = ReleaseSession(row)
    exits: list[int] = []
    monkeypatch.setattr("education_platform.workers.lease.sync_session", lambda: session)
    waits: list[float] = []

    def _wait(_event: threading.Event, timeout: float) -> bool:
        waits.append(timeout)
        clock.now += timeout
        return False

    state = WorkerShutdown(grace_seconds=25, clock=clock, wait=_wait, exit=exits.append)
    lease = _lease(job_id, attempts=1, locked_until=locked_until)

    def _process() -> None:
        state.note_signal(signal.SIGTERM, None)
        clock.now = 1
        state.note_signal(signal.SIGTERM, None)
        state.watch()

    _run_claimed(state, lease, _process)
    assert waits == [24]
    assert clock.now == 25
    assert row.status is GenerationJobStatus.QUEUED
    assert row.locked_until is None
    assert row.attempts == 0
    assert session.executed == 1
    assert session.commits == 1
    assert exits == [0]
    state.note_signal(signal.SIGINT, None)
    state.watch()
    assert session.executed == 1
    assert exits == [0]


def test_stop_between_claim_and_begin_refunds(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = uuid4()
    locked_until = datetime(2026, 10, 20, 3, tzinfo=UTC)
    row = JobRow(
        job_id,
        status=GenerationJobStatus.RUNNING,
        attempts=2,
        locked_until=locked_until,
    )
    monkeypatch.setattr(
        "education_platform.workers.lease.sync_session",
        lambda: ReleaseSession(row),
    )
    state = WorkerShutdown(grace_seconds=25, clock=FakeClock(), exit=lambda _code: None)
    state.note_signal(signal.SIGTERM, None)
    lease = _lease(job_id, attempts=2, locked_until=locked_until)
    ran: list[bool] = []
    assert _run_claimed(state, lease, lambda: ran.append(True)) is None
    assert ran == []
    assert row.status is GenerationJobStatus.QUEUED
    assert row.locked_until is None
    assert row.attempts == 1


def test_grace_expiry_releases_before_interrupting_and_does_not_os_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[object] = []
    monkeypatch.setattr(os, "_exit", lambda code: order.append(("os_exit", code)))

    def _wake() -> None:
        order.append("interrupt")
        assert interrupt_after_release_requested() is True

    monkeypatch.setattr(
        "education_platform.workers.shutdown.wake_worker_main",
        _wake,
    )
    job_id = uuid4()
    locked_until = datetime(2026, 10, 20, 4, tzinfo=UTC)
    row = JobRow(
        job_id,
        status=GenerationJobStatus.RUNNING,
        attempts=1,
        locked_until=locked_until,
    )
    monkeypatch.setattr(
        "education_platform.workers.lease.sync_session",
        lambda: ReleaseSession(row),
    )

    def _release(lease: OwnedLease) -> None:
        order.append("release")
        assert release_lease(lease) is True
        assert row.attempts == 0
        assert row.status is GenerationJobStatus.QUEUED

    state = WorkerShutdown(grace_seconds=0, clock=FakeClock(), release=_release)
    lease = _lease(job_id, attempts=1, locked_until=locked_until)
    assert state.begin_job(lease) is True
    state.note_signal(signal.SIGTERM, None)
    thread = threading.Thread(target=state.watch)
    thread.start()
    thread.join(timeout=1)
    assert thread.is_alive() is False
    assert order == ["release", "interrupt"]
    assert row.locked_until is None


def test_keyboard_interrupt_runs_the_job_finally() -> None:
    finished: list[str] = []
    state = WorkerShutdown(grace_seconds=25, clock=FakeClock(), release=lambda _lease: None)
    lease = _lease()

    def _process() -> None:
        try:
            raise KeyboardInterrupt
        finally:
            finished.append("finally")

    with pytest.raises(KeyboardInterrupt):
        _run_claimed(state, lease, _process)
    assert finished == ["finally"]


def test_run_forever_returns_after_keyboard_interrupt() -> None:
    state = WorkerShutdown(grace_seconds=25, clock=FakeClock(), release=lambda _lease: None)

    def _poll() -> None:
        raise KeyboardInterrupt

    run_forever(poll_interval_seconds=60, shutdown=state, poll=_poll)


def test_close_session_propagates_keyboard_interrupt() -> None:
    class _Session:
        def close(self) -> None:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        close_session(_Session())  # type: ignore[arg-type]


def test_stray_sigusr1_is_ignored(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="education_platform.workers.shutdown")
    clear_interrupt_request()
    _interrupt_job(signal.SIGUSR1, None)
    assert interrupt_after_release_requested() is False
    assert "Ignored SIGUSR1" in caplog.text


def test_sigusr1_raises_only_after_the_release_flag() -> None:
    request_interrupt_after_release()
    with pytest.raises(KeyboardInterrupt):
        _interrupt_job(signal.SIGUSR1, None)
    assert interrupt_after_release_requested() is False
    _interrupt_job(signal.SIGUSR1, None)


def test_keyboard_interrupt_during_close_returns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="education_platform.workers.runner")
    state = WorkerShutdown(grace_seconds=25, clock=FakeClock(), release=lambda _lease: False)
    state.release_matched = False
    closed: list[str] = []

    def _poll() -> None:
        try:
            closed.append("body")
        finally:
            raise KeyboardInterrupt

    run_forever(poll_interval_seconds=60, shutdown=state, poll=_poll)
    assert closed == ["body"]
    assert "its lease release did not match" in caplog.text
    assert "after releasing its lease" not in caplog.text


def test_shutdown_log_reports_a_matched_release(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="education_platform.workers.runner")
    state = WorkerShutdown(grace_seconds=25, clock=FakeClock(), release=lambda _lease: True)
    state.release_matched = True

    def _poll() -> None:
        raise KeyboardInterrupt

    run_forever(poll_interval_seconds=60, shutdown=state, poll=_poll)
    assert "after releasing its lease" in caplog.text


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
        release=lambda _lease: None,
        exit=lambda _code: None,
    )
    state.install_signals()
    state.install_signals()
    assert registered[signal.SIGTERM] == state.note_signal
    assert registered[signal.SIGINT] == state.note_signal
    assert registered[signal.SIGUSR1] == _interrupt_job
    assert started == ["start"]
    state.note_signal(signal.SIGTERM, None)
    state.note_signal(signal.SIGINT, None)
    assert state.stop.is_set()


def test_compose_worker_stop_grace_period_is_at_least_40_seconds() -> None:
    path = Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.prod.yml"
    document = yaml.safe_load(path.read_text())
    raw = document["services"]["worker"]["stop_grace_period"]
    if isinstance(raw, int):
        seconds = raw
    else:
        text = str(raw).strip()
        assert text.endswith("s")
        seconds = int(text[:-1])
    assert seconds >= 40
