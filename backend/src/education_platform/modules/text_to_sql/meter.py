"""Per-request timings for the text-to-SQL audit payload.

The meter is a mutable object on a contextvar so parallel lean tasks share one
counter. ``audit_log`` copies the numbers into the payload for both pipelines.
"""

from __future__ import annotations

import time
from contextvars import ContextVar, Token
from dataclasses import dataclass

_METER: ContextVar[PipelineMeter | None] = ContextVar("text_to_sql_meter", default=None)


@dataclass(slots=True)
class PipelineMeter:
    started: float
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    guard_ms: int = 0
    intent_ms: int = 0
    generate_ms: int = 0
    execute_ms: int = 0
    compose_ms: int = 0


def start_meter() -> Token[PipelineMeter | None]:
    return _METER.set(PipelineMeter(started=time.perf_counter()))


def reset_meter(token: Token[PipelineMeter | None]) -> None:
    _METER.reset(token)


def ensure_meter() -> PipelineMeter:
    meter = _METER.get()
    if meter is None:
        meter = PipelineMeter(started=time.perf_counter())
        _METER.set(meter)
    return meter


def note_llm_call() -> None:
    meter = _METER.get()
    if meter is not None:
        meter.llm_calls += 1


def note_tokens(prompt_tokens: int, completion_tokens: int) -> None:
    meter = _METER.get()
    if meter is None:
        return
    meter.prompt_tokens += prompt_tokens
    meter.completion_tokens += completion_tokens


def add_ms(key: str, elapsed_ms: int) -> None:
    meter = _METER.get()
    if meter is None:
        return
    current = getattr(meter, key, 0)
    setattr(meter, key, int(current) + elapsed_ms)


def meter_payload(retries: int) -> dict[str, int]:
    """Timing fields for the audit payload. Zeros when no meter was started."""
    meter = _METER.get()
    if meter is None:
        return {
            "guard_ms": 0,
            "intent_ms": 0,
            "generate_ms": 0,
            "execute_ms": 0,
            "compose_ms": 0,
            "total_ms": 0,
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "retries": retries,
        }
    elapsed = int((time.perf_counter() - meter.started) * 1000)
    return {
        "guard_ms": meter.guard_ms,
        "intent_ms": meter.intent_ms,
        "generate_ms": meter.generate_ms,
        "execute_ms": meter.execute_ms,
        "compose_ms": meter.compose_ms,
        "total_ms": elapsed if elapsed > 0 else 0,
        "llm_calls": meter.llm_calls,
        "prompt_tokens": meter.prompt_tokens,
        "completion_tokens": meter.completion_tokens,
        "retries": retries,
    }
