"""Per-process concurrency caps and a small circuit breaker.

The breaker counts exhausted provider failures, not each retry. Open skips the
primary model. After the cooldown, one half-open probe may try the primary.
Fallback failures do not move that cooldown.
"""

from __future__ import annotations

import asyncio
import enum
import threading
import time
import weakref
from weakref import WeakKeyDictionary

from education_platform.core.llm import OpenRouterError

_guard = threading.Lock()
# The semaphore is held weakly. A contended ``asyncio.Semaphore`` stores the
# loop on itself, so a strong value would keep that loop alive.
_async_semaphores: WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, weakref.ReferenceType[asyncio.Semaphore]]
] = WeakKeyDictionary()
_sync_semaphores: dict[str, threading.BoundedSemaphore] = {}


def _now() -> float:
    return time.monotonic()


class _CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, *, threshold: int, cooldown_seconds: float) -> None:
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._consecutive = 0
        self._open_until = 0.0
        self._state = _CircuitState.CLOSED
        self._lock = threading.Lock()

    def primary_allowed(self) -> bool:
        """True when this caller should use the primary model.

        The open state refuses the primary. Once the cooldown has elapsed, the
        first caller becomes the single half-open probe and later callers stay
        on the fallback until that probe finishes.
        """
        with self._lock:
            if self._state is _CircuitState.CLOSED:
                return True
            if self._state is _CircuitState.HALF_OPEN:
                return False
            if _now() < self._open_until:
                return False
            self._state = _CircuitState.HALF_OPEN
            return True

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0
            self._open_until = 0.0
            self._state = _CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            if self._state is _CircuitState.OPEN:
                return
            if self._state is _CircuitState.HALF_OPEN:
                self._trip()
                return
            self._consecutive += 1
            if self._consecutive >= self._threshold:
                self._trip()

    def release_probe(self) -> None:
        """Give back a half-open probe that did not finish.

        Cancellation and a non-retryable client error must not leave the
        breaker half-open, and they must not move the cooldown.
        """
        with self._lock:
            if self._state is _CircuitState.HALF_OPEN:
                self._state = _CircuitState.OPEN

    def _trip(self) -> None:
        self._state = _CircuitState.OPEN
        self._consecutive = 0
        self._open_until = _now() + self._cooldown


_breakers: dict[str, CircuitBreaker] = {}


def breaker_for(step: str, *, threshold: int, cooldown_seconds: float) -> CircuitBreaker:
    with _guard:
        existing = _breakers.get(step)
        if existing is None:
            existing = CircuitBreaker(threshold=threshold, cooldown_seconds=cooldown_seconds)
            _breakers[step] = existing
        return existing


def async_semaphore(name: str, limit: int) -> asyncio.Semaphore:
    """Return the semaphore for this concurrency class on the running loop.

    An ``asyncio.Semaphore`` is bound to the loop that created it. Workers that
    each call ``asyncio.run`` need their own.
    """
    loop = asyncio.get_running_loop()
    with _guard:
        bucket = _async_semaphores.get(loop)
        if bucket is None:
            bucket = {}
            _async_semaphores[loop] = bucket
        ref = bucket.get(name)
        if ref is not None:
            existing = ref()
            if existing is not None:
                return existing
        if limit < 1:
            raise OpenRouterError(f"Concurrency limit for {name} must be at least 1")
        created = asyncio.Semaphore(limit)
        bucket[name] = weakref.ref(created)
        return created


def sync_semaphore(name: str, limit: int) -> threading.BoundedSemaphore:
    with _guard:
        existing = _sync_semaphores.get(name)
        if existing is not None:
            return existing
        if limit < 1:
            raise OpenRouterError(f"Concurrency limit for {name} must be at least 1")
        created = threading.BoundedSemaphore(limit)
        _sync_semaphores[name] = created
        return created


def reset_controls() -> None:
    with _guard:
        _async_semaphores.clear()
        _sync_semaphores.clear()
        _breakers.clear()
