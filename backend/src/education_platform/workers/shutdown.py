"""Stop claiming work on SIGTERM and requeue the in-flight job after a grace period.

tini forwards SIGTERM to the child, so the handler is registered on this process
and the worker does not need to be PID 1.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from collections.abc import Callable
from types import FrameType
from typing import Any
from uuid import UUID

from education_platform.core.config import get_settings
from education_platform.workers.lease import release_lease

Clock = Callable[[], float]
WaitFn = Callable[[threading.Event, float], bool]
ReleaseFn = Callable[[type[Any], UUID], None]
ExitFn = Callable[[int], None]


def _wait_for_event(event: threading.Event, timeout: float) -> bool:
    return event.wait(timeout)


class WorkerShutdown:
    """Idempotent SIGTERM/SIGINT handling for the job-claim loop."""

    def __init__(
        self,
        *,
        grace_seconds: float | None = None,
        clock: Clock | None = None,
        wait: WaitFn | None = None,
        release: ReleaseFn | None = None,
        exit: ExitFn | None = None,
    ) -> None:
        if grace_seconds is None:
            self.grace_seconds = get_settings().worker_shutdown_grace_seconds
        else:
            self.grace_seconds = grace_seconds
        self._clock = clock if clock is not None else time.monotonic
        self._wait = wait if wait is not None else _wait_for_event
        self._release = release if release is not None else release_lease
        self._exit = exit if exit is not None else os._exit
        self.stop = threading.Event()
        self._job_finished = threading.Event()
        self._lock = threading.Lock()
        self._current: tuple[type[Any], UUID] | None = None
        self._signaled = False
        self._handled = False
        self._deadline_at: float | None = None
        self._thread: threading.Thread | None = None

    def install_signals(self) -> None:
        """Register SIGTERM and SIGINT, then watch for them on a daemon thread.

        A second call replaces the same handlers and does not start another thread.
        """
        signal.signal(signal.SIGTERM, self.note_signal)
        signal.signal(signal.SIGINT, self.note_signal)
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self.watch,
            name="worker-shutdown",
            daemon=True,
        )
        self._thread.start()

    def note_signal(self, _signum: int, _frame: FrameType | None = None) -> None:
        """Request shutdown. A second signal does not move the grace deadline."""
        with self._lock:
            self.stop.set()
            if self._signaled:
                return
            self._signaled = True
            self._deadline_at = self._clock() + self.grace_seconds

    def begin_job(self, model: type[Any], job_id: UUID) -> None:
        self._job_finished.clear()
        with self._lock:
            self._current = (model, job_id)

    def finish_job(self) -> None:
        with self._lock:
            self._current = None
        self._job_finished.set()

    def release_job(self, model: type[Any], job_id: UUID) -> None:
        """Return a claimed job to the queue without running it."""
        self._release(model, job_id)

    def watch(self) -> None:
        """After the signal, wait out the grace period or requeue the current job.

        Returns when the in-flight job finishes in time. Otherwise releases its
        lease and exits 0. A second call is a no-op.
        """
        self.stop.wait()
        with self._lock:
            if self._handled:
                return
            self._handled = True
            current = self._current
            deadline = self._deadline_at
        if current is None:
            return
        remaining = 0.0 if deadline is None else max(0.0, deadline - self._clock())
        if remaining > 0 and self._wait(self._job_finished, remaining):
            return
        with self._lock:
            if self._current is None or self._job_finished.is_set():
                return
            model, job_id = self._current
            self._current = None
        self._release(model, job_id)
        self._exit(0)
