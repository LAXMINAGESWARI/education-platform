"""A second model answers each item without the key.

The solver receives the stem and options A–D only. Agreement with the answer key
is computed afterwards and stored on the key's scoring rubric. One item's
failure is recorded as ``status="error"`` and does not fail the generation job.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from openai import OpenAI, OpenAIError

from education_platform.core.config import get_settings
from education_platform.core.llm import OpenRouterError, build_openrouter_sync_client

logger = logging.getLogger(__name__)

_LABELS = ("A", "B", "C", "D")
_NONE = "E"
_POOL_WORKERS = 8
_SYSTEM = (
    "Answer the multiple-choice question from the stem and options alone. "
    "E means none of A-D is correct. Choose E when the right value is missing. "
    "Reply with JSON only: "
    '{"answer": "A", "B", "C", "D", or "E", "working": "the steps you used", '
    '"confidence": a number from 0 to 1}.'
)

SolveStatus = Literal["ok", "error"]
StoredStatus = Literal["agree", "disagree", "error", "skipped"]


@dataclass(frozen=True, slots=True)
class BlindVerdict:
    answer: str | None
    confidence: float
    status: SolveStatus
    working: str = ""


class BlindSolver(Protocol):
    def __call__(self, prompt: str, options: Mapping[str, str]) -> BlindVerdict:
        """Return a verdict from the stem and options. Never receives the key."""


class BlindItem(Protocol):
    """Read-only view of a stem. ``dict`` options satisfy ``Mapping``."""

    @property
    def prompt(self) -> str: ...

    @property
    def options(self) -> Mapping[str, str]: ...


def error_verdict() -> BlindVerdict:
    return BlindVerdict(answer=None, confidence=0.0, status="error")


def question_text(prompt: str, options: Mapping[str, str]) -> str:
    """Stem plus options A–D. Extra keys on ``options`` are ignored."""
    lines = [prompt.strip(), ""]
    for label in _LABELS:
        text = options.get(label)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"missing option {label}")
        lines.append(f"{label}. {text.strip()}")
    lines.append(f"{_NONE}. None of these is correct")
    return "\n".join(lines)


def blind_solve_items(items: Sequence[BlindItem], solver: BlindSolver) -> list[BlindVerdict]:
    """Fan out across a pool of 8. One item's exception becomes status ``error``."""
    if not items:
        return []

    def _one(item: BlindItem) -> BlindVerdict:
        try:
            verdict = solver(item.prompt, {label: item.options[label] for label in _LABELS})
        except Exception:
            logger.warning("Blind solve failed for one item", exc_info=True)
            return error_verdict()
        if not isinstance(verdict, BlindVerdict):
            return error_verdict()
        return verdict

    with ThreadPoolExecutor(max_workers=_POOL_WORKERS) as pool:
        return list(pool.map(_one, items))


def blind_solve_record(
    verdict: BlindVerdict, *, correct_label: str, model: str
) -> dict[str, object]:
    """Shape stored at ``scoring_rubric["blind_solve"]``. The solver never sees the label."""
    answer = _canonical_label(verdict.answer)
    confidence = _clamp_confidence(verdict.confidence)
    if verdict.status != "ok" or answer is None:
        return {
            "model": model,
            "answer": None,
            "agrees": False,
            "confidence": confidence,
            "status": "error",
            "working": verdict.working.strip(),
        }
    agrees = answer == _canonical_label(correct_label)
    status: StoredStatus = "agree" if agrees else "disagree"
    return {
        "model": model,
        "answer": answer,
        "agrees": agrees,
        "confidence": confidence,
        "status": status,
        "working": verdict.working.strip(),
    }


def resolve_blind_solver(injected: BlindSolver | None) -> BlindSolver | None:
    """Use an injected solver in tests. Skip the check when OpenRouter is not configured."""
    if injected is not None:
        return injected
    if not get_settings().openrouter_configured:
        return None
    return live_blind_solver()


def live_blind_solver() -> BlindSolver:
    settings = get_settings()
    client = build_openrouter_sync_client(settings)
    model = settings.blind_solver_model

    def solve(prompt: str, options: Mapping[str, str]) -> BlindVerdict:
        try:
            payload = _complete(client, model=model, prompt=prompt, options=options)
        except (OpenRouterError, OpenAIError, ValueError, IndexError, KeyError):
            logger.warning("Blind solver request failed", exc_info=True)
            return error_verdict()
        return _verdict_from_payload(payload)

    return solve


def _complete(
    client: OpenAI, *, model: str, prompt: str, options: Mapping[str, str]
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": question_text(prompt, options)},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    try:
        response = client.chat.completions.create(**kwargs)
    except OpenAIError as exc:
        raise OpenRouterError(str(exc)) from exc
    if not response.choices:
        raise OpenRouterError("OpenRouter returned no choices")
    content = response.choices[0].message.content
    if not content:
        raise OpenRouterError("OpenRouter returned an empty completion")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise OpenRouterError("Expected a JSON object from the blind solver")
    return data


def _verdict_from_payload(data: Mapping[str, Any]) -> BlindVerdict:
    answer = _canonical_label(data.get("answer") if isinstance(data.get("answer"), str) else None)
    if answer is None:
        return error_verdict()
    working = data.get("working")
    return BlindVerdict(
        answer=answer,
        confidence=_clamp_confidence(_as_float(data.get("confidence"))),
        status="ok",
        working=working.strip() if isinstance(working, str) else "",
    )


def _canonical_label(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip().upper()
    if text in _LABELS or text == _NONE:
        return text
    return None


def _as_float(raw: object) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return 0.0
    return float(raw)


def _clamp_confidence(value: float) -> float:
    if math.isnan(value):
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
