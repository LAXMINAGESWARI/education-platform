"""Per-process concurrency caps and a small circuit breaker.

The breaker counts exhausted provider failures, not each retry. While it is
open the next calls skip the primary model and use the first fallback until
the cooldown ends.
"""

from __future__ import annotations

import asyncio
import threading
import time

from education_platform.core.llm import OpenRouterError

_guard = threading.Lock()
_async_semaphores: dict[str, asyncio.Semaphore] = {}
_sync_semaphores: dict[str, threading.BoundedSemaphore] = {}


def _now() -> float:
    return time.monotonic()


class CircuitBreaker:
    def __init__(self, *, threshold: int, cooldown_seconds: float) -> None:
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._consecutive = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def primary_allowed(self) -> bool:
        with self._lock:
            return _now() >= self._open_until

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0
            self._open_until = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive += 1
            if self._consecutive >= self._threshold:
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
    with _guard:
        existing = _async_semaphores.get(name)
        if existing is not None:
            return existing
        if limit < 1:
            raise OpenRouterError(f"Concurrency limit for {name} must be at least 1")
        created = asyncio.Semaphore(limit)
        _async_semaphores[name] = created
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
