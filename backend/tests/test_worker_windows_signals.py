"""Windows has no SIGUSR1. The worker still installs handlers and stops cleanly."""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from education_platform.workers.lease import OwnedLease
from education_platform.workers.runner import run_forever
from education_platform.workers.shutdown import (
    WorkerShutdown,
    _interrupt_job,
    clear_interrupt_request,
    interrupt_after_release_requested,
    request_interrupt_after_release,
    wake_worker_main,
)


@pytest.fixture(autouse=True)
def _reset_interrupt_request() -> Iterator[None]:
    clear_interrupt_request()
    yield
    clear_interrupt_request()


def _drop_usr1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(signal, "SIGUSR1", raising=False)
    monkeypatch.delattr(signal, "pthread_kill", raising=False)


def _shutdown() -> WorkerShutdown:
    return WorkerShutdown(
        grace_seconds=25,
        release=lambda _lease: None,
        exit=lambda _code: None,
    )


def _capture_signals(monkeypatch: pytest.MonkeyPatch) -> dict[int, object]:
    registered: dict[int, object] = {}

    def _register(sig: int, handler: object) -> None:
        registered[sig] = handler

    monkeypatch.setattr(signal, "signal", _register)
    return registered


def _stub_thread(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    started: list[str] = []

    class _Thread:
        def __init__(self, *, target: object, name: str, daemon: bool) -> None:
            assert name == "worker-shutdown"
            assert daemon is True
            assert target is not None

        def start(self) -> None:
            started.append("start")

    monkeypatch.setattr("education_platform.workers.shutdown.threading.Thread", _Thread)
    return started


def _record_interrupt(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        "education_platform.workers.shutdown._thread.interrupt_main",
        lambda: calls.append("interrupt"),
    )
    return calls


def test_install_signals_without_usr1_registers_sigint_and_sigterm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drop_usr1(monkeypatch)
    monkeypatch.delattr(signal, "SIGBREAK", raising=False)
    registered = _capture_signals(monkeypatch)
    started = _stub_thread(monkeypatch)
    state = _shutdown()

    state.install_signals()
    state.install_signals()

    assert set(registered) == {signal.SIGINT, signal.SIGTERM}
    assert registered[signal.SIGINT] == state.note_signal
    assert registered[signal.SIGTERM] == state.note_signal
    assert started == ["start"]


def test_install_signals_registers_sigbreak_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drop_usr1(monkeypatch)
    monkeypatch.setattr(signal, "SIGBREAK", 21, raising=False)
    registered = _capture_signals(monkeypatch)
    _stub_thread(monkeypatch)
    state = _shutdown()

    state.install_signals()

    assert set(registered) == {signal.SIGINT, signal.SIGTERM, signal.SIGBREAK}
    assert registered[signal.SIGINT] == state.note_signal
    assert registered[signal.SIGTERM] == state.note_signal
    assert registered[signal.SIGBREAK] == state.note_signal


def test_install_signals_on_this_process_does_not_raise() -> None:
    watched = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGUSR1"):
        watched.append(signal.SIGUSR1)
    if hasattr(signal, "SIGBREAK"):
        watched.append(signal.SIGBREAK)
    previous = {signum: signal.getsignal(signum) for signum in watched}
    state = _shutdown()
    try:
        state.install_signals()
        assert signal.getsignal(signal.SIGINT) == state.note_signal
        assert signal.getsignal(signal.SIGTERM) == state.note_signal
        if hasattr(signal, "SIGUSR1"):
            assert signal.getsignal(signal.SIGUSR1) == _interrupt_job
        if hasattr(signal, "SIGBREAK"):
            assert signal.getsignal(signal.SIGBREAK) == state.note_signal
    finally:
        state.stop.set()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def test_wake_without_usr1_interrupts_only_when_the_flag_allows_it(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _drop_usr1(monkeypatch)
    calls = _record_interrupt(monkeypatch)
    caplog.set_level(logging.WARNING, logger="education_platform.workers.shutdown")

    wake_worker_main()
    wake_worker_main()
    assert calls == []
    assert "Ignored main-thread interrupt" in caplog.text
    assert interrupt_after_release_requested() is False

    request_interrupt_after_release()
    wake_worker_main()
    assert calls == ["interrupt"]
    assert interrupt_after_release_requested() is True

    clear_interrupt_request()
    wake_worker_main()
    assert calls == ["interrupt"]


def test_posix_wake_uses_pthread_kill_with_sigusr1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(signal, "SIGUSR1"):
        monkeypatch.setattr(signal, "SIGUSR1", 10, raising=False)
    kills: list[tuple[int, int]] = []

    def _kill(ident: int, sig: int) -> None:
        kills.append((ident, sig))

    monkeypatch.setattr(signal, "pthread_kill", _kill, raising=False)
    interrupts = _record_interrupt(monkeypatch)
    request_interrupt_after_release()

    wake_worker_main()

    ident = threading.main_thread().ident
    assert ident is not None
    assert kills == [(ident, signal.SIGUSR1)]
    assert interrupts == []
    assert interrupt_after_release_requested() is True


def test_windows_sigint_raises_only_after_the_release_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drop_usr1(monkeypatch)
    state = _shutdown()

    state.note_signal(signal.SIGINT, None)
    state.note_signal(signal.SIGTERM, None)
    assert state.stop.is_set()

    request_interrupt_after_release()
    with pytest.raises(KeyboardInterrupt):
        state.note_signal(signal.SIGINT, None)
    assert interrupt_after_release_requested() is False

    state.note_signal(signal.SIGINT, None)
    assert state.stop.is_set()


def test_posix_sigint_does_not_raise_when_the_release_flag_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(signal, "SIGUSR1"):
        monkeypatch.setattr(signal, "SIGUSR1", 10, raising=False)
    if not hasattr(signal, "pthread_kill"):
        monkeypatch.setattr(signal, "pthread_kill", lambda _ident, _sig: None, raising=False)
    state = _shutdown()
    request_interrupt_after_release()

    state.note_signal(signal.SIGINT, None)

    assert state.stop.is_set()
    assert interrupt_after_release_requested() is True


def test_posix_wake_returns_when_the_main_thread_has_no_ident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(signal, "SIGUSR1"):
        monkeypatch.setattr(signal, "SIGUSR1", 10, raising=False)
    kills: list[tuple[int, int]] = []
    monkeypatch.setattr(
        signal,
        "pthread_kill",
        lambda ident, sig: kills.append((ident, sig)),
        raising=False,
    )

    class _Main:
        ident = None

    monkeypatch.setattr(
        "education_platform.workers.shutdown.threading.main_thread",
        lambda: _Main(),
    )
    wake_worker_main()
    assert kills == []


def test_windows_watch_interrupts_only_after_the_lease_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drop_usr1(monkeypatch)
    calls = _record_interrupt(monkeypatch)
    order: list[str] = []

    def _release(_lease: OwnedLease) -> bool:
        order.append("release")
        assert calls == []
        assert interrupt_after_release_requested() is False
        return True

    state = WorkerShutdown(grace_seconds=0, clock=lambda: 0.0, release=_release)
    lease = OwnedLease(
        model=object,
        job_id=uuid4(),
        attempts=1,
        locked_until=datetime(2026, 10, 20, tzinfo=UTC),
    )
    assert state.begin_job(lease) is True
    state.note_signal(signal.SIGTERM, None)
    thread = threading.Thread(target=state.watch)
    thread.start()
    thread.join(timeout=2)
    assert thread.is_alive() is False
    assert order == ["release"]
    assert calls == ["interrupt"]
    assert state.release_matched is True
    assert interrupt_after_release_requested() is True


def test_real_interrupt_main_raises_only_after_the_release_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drop_usr1(monkeypatch)
    state = _shutdown()
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, state.note_signal)
    sleeping = threading.Event()

    def _wake_without_flag() -> None:
        assert sleeping.wait(timeout=2)
        wake_worker_main()

    thread = threading.Thread(target=_wake_without_flag, daemon=True)
    thread.start()
    try:
        sleeping.set()
        time.sleep(0.2)
        thread.join(timeout=2)
        assert thread.is_alive() is False
        assert state.stop.is_set() is False

        def _wake_with_flag() -> None:
            time.sleep(0.05)
            request_interrupt_after_release()
            wake_worker_main()

        threading.Thread(target=_wake_with_flag, daemon=True).start()
        with pytest.raises(KeyboardInterrupt):
            time.sleep(2)
        assert interrupt_after_release_requested() is False
    finally:
        signal.signal(signal.SIGINT, previous)


def test_run_forever_uses_the_graceful_stop_for_the_windows_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _drop_usr1(monkeypatch)
    caplog.set_level(logging.INFO, logger="education_platform.workers.runner")
    state = _shutdown()
    state.release_matched = True

    def _poll() -> None:
        request_interrupt_after_release()
        state.note_signal(signal.SIGINT, None)

    run_forever(poll_interval_seconds=60, shutdown=state, poll=_poll)
    assert "after releasing its lease" in caplog.text
