"""Stop claiming work on SIGTERM and requeue the in-flight job after a grace period.

tini forwards SIGTERM to the child, so the handler is registered on this process
and the worker does not need to be PID 1. A second signal does not move the
deadline or release twice.

After the grace period the lease is released and committed first. The watch
thread then sets a flag and wakes the main thread. On Linux and macOS that wake
is SIGUSR1. ``_interrupt_job`` raises KeyboardInterrupt only when the flag is
set, including when the job is blocked in sleep, so ``finally`` blocks run. A
SIGUSR1 from anywhere else is logged and ignored, so it cannot abort a job
whose lease was not released. On those platforms SIGINT cannot do this: it is
already handled by ``note_signal``, and a second SIGINT does not raise.

Without SIGUSR1 (Windows), the wake is ``_thread.interrupt_main`` and it runs
only when that same flag is set. ``interrupt_main`` schedules SIGINT for the
main thread; ``note_signal`` raises KeyboardInterrupt for that delivery alone.
Ctrl+C and Ctrl+Break still request a graceful shutdown before the lease is
released. ``interrupt_main`` cannot interrupt a blocking C call that does not
return to the interpreter.

``os._exit`` is not used: it would skip those blocks and drop a job that was
about to commit.
"""

from __future__ import annotations

import _thread
import logging
import signal
import threading
import time
from collections.abc import Callable
from types import FrameType

from education_platform.core.config import get_settings
from education_platform.workers.lease import OwnedLease, release_lease

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
WaitFn = Callable[[threading.Event, float], bool]
ReleaseFn = Callable[[OwnedLease], object]
ExitFn = Callable[[int], None]


def _wait_for_event(event: threading.Event, timeout: float) -> bool:
    return event.wait(timeout)


def _has_usr1() -> bool:
    """True when SIGUSR1 can be delivered to the main thread.

    Read on each call so a platform without ``SIGUSR1`` or ``pthread_kill``
    never touches those attributes. Linux and macOS keep the SIGUSR1 path.
    """
    return hasattr(signal, "SIGUSR1") and hasattr(signal, "pthread_kill")


_interrupt_after_release = threading.Event()


def interrupt_after_release_requested() -> bool:
    """True after the watcher has released the lease and asked the main thread to stop."""
    return _interrupt_after_release.is_set()


def clear_interrupt_request() -> None:
    """Drop a pending interrupt. Tests use this so one case cannot leak into the next."""
    _interrupt_after_release.clear()


def request_interrupt_after_release() -> None:
    """Allow the next main-thread wake to raise. The watcher calls this after the release."""
    _interrupt_after_release.set()


def _interrupt_job(_signum: int, _frame: FrameType | None) -> None:
    """Stop the in-flight job once its lease release has committed.

    A SIGUSR1 that arrives before that flag is set is not a shutdown. Raising
    here would abandon the job without releasing its lease.
    """
    if not _interrupt_after_release.is_set():
        logger.warning("Ignored SIGUSR1; shutdown has not released the in-flight lease")
        return
    _interrupt_after_release.clear()
    raise KeyboardInterrupt


def wake_worker_main() -> None:
    """Raise KeyboardInterrupt on the main thread and wake a blocking sleep.

    With SIGUSR1, deliver that signal and let ``_interrupt_job`` apply the
    lease-release flag. Without it, ``_thread.interrupt_main`` runs only when
    the flag is already set. That call schedules SIGINT; it does not interrupt
    a blocking C call that never returns to the interpreter.
    """
    if _has_usr1():
        ident = threading.main_thread().ident
        if ident is None:
            return
        signal.pthread_kill(ident, signal.SIGUSR1)
        return
    if not _interrupt_after_release.is_set():
        logger.warning(
            "Ignored main-thread interrupt; shutdown has not released the in-flight lease"
        )
        return
    _thread.interrupt_main()


class WorkerShutdown:
    """Idempotent SIGTERM/SIGINT/SIGBREAK handling for the job-claim loop."""

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
        self._exit = exit
        self.stop = threading.Event()
        self._job_finished = threading.Event()
        self._lock = threading.Lock()
        self._current: OwnedLease | None = None
        self._signaled = False
        self._handled = False
        self._deadline_at: float | None = None
        self._thread: threading.Thread | None = None
        self.release_matched: bool | None = None

    def install_signals(self) -> None:
        """Register SIGTERM and SIGINT, then watch for them on a daemon thread.

        SIGUSR1 is registered only where it can be delivered to the main thread.
        SIGBREAK (Windows Ctrl+Break) uses the same handler as SIGTERM and SIGINT.
        A second call replaces the same handlers and does not start another thread.
        """
        signal.signal(signal.SIGTERM, self.note_signal)
        signal.signal(signal.SIGINT, self.note_signal)
        if _has_usr1():
            signal.signal(signal.SIGUSR1, _interrupt_job)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, self.note_signal)
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self.watch,
            name="worker-shutdown",
            daemon=True,
        )
        self._thread.start()

    def note_signal(self, _signum: int, _frame: FrameType | None = None) -> None:
        """Request shutdown. A second signal does not move the grace deadline.

        Where SIGUSR1 exists this never raises. Without it, ``interrupt_main``
        re-enters this handler via SIGINT, and that delivery raises only after
        the lease release has committed.
        """
        if not _has_usr1() and _interrupt_after_release.is_set():
            _interrupt_after_release.clear()
            raise KeyboardInterrupt
        with self._lock:
            self.stop.set()
            if self._signaled:
                return
            self._signaled = True
            self._deadline_at = self._clock() + self.grace_seconds

    def begin_job(self, lease: OwnedLease) -> bool:
        """Record ``lease`` as in flight. False when shutdown already won the race.

        The stop flag and the current lease are updated under one lock, so a
        signal cannot land between the check and the claim record.
        """
        with self._lock:
            if self.stop.is_set():
                return False
            self._job_finished.clear()
            self._current = lease
            return True

    def finish_job(self) -> None:
        with self._lock:
            self._current = None
        self._job_finished.set()

    def release_job(self, lease: OwnedLease) -> None:
        """Requeue a claimed job that will not run, refunding its attempt."""
        self._release(lease)

    def watch(self) -> None:
        """After the signal, wait out the grace period or requeue the current job.

        Returns when the in-flight job finishes in time. Otherwise releases its
        lease (the release commits before this returns) and, on the watch
        thread, interrupts the main thread so ``finally`` blocks run. A second
        call is a no-op.
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
            lease = self._current
            self._current = None
        matched = self._release(lease)
        if isinstance(matched, bool):
            self.release_matched = matched
        if threading.current_thread() is not threading.main_thread():
            request_interrupt_after_release()
            wake_worker_main()
        if self._exit is not None:
            self._exit(0)
