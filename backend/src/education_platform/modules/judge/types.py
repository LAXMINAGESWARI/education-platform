"""Request and response types shared by OpenRouter and OpenJev.

Both servers take ``model``, ``state``, and a map of typed ``questions``, and
return ``answers`` plus ``usage``. OpenRouter also returns ``id``, ``provider``,
and ``usage.cost``. Those fields are optional so a local ``/v1/systemone``
response still parses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

StateValue = str | dict[str, object] | list[object]
QuestionKind = Literal["noul", "choice", "score"]
CheckRule = Literal["noul", "coverage", "grade", "difficulty"]


@dataclass(frozen=True, slots=True)
class NoulQuestion:
    instructions: str
    true_meaning: str
    false_meaning: str

    def to_api(self) -> dict[str, object]:
        return {
            "type": "noul",
            "instructions": self.instructions,
            "criteria": {"true": self.true_meaning, "false": self.false_meaning},
        }


@dataclass(frozen=True, slots=True)
class ChoiceQuestion:
    instructions: str
    criteria: Mapping[str, str]

    def to_api(self) -> dict[str, object]:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": dict(self.criteria),
        }


@dataclass(frozen=True, slots=True)
class ScoreQuestion:
    instructions: str
    criteria: tuple[str, ...]

    def to_api(self) -> dict[str, object]:
        return {
            "type": "score",
            "instructions": self.instructions,
            "criteria": list(self.criteria),
        }


Question = NoulQuestion | ChoiceQuestion | ScoreQuestion


@dataclass(frozen=True, slots=True)
class PlannedCheck:
    """One question plus the policy rule that will read its answer."""

    check_id: str
    question: Question
    rule: CheckRule
    label: str
    stated: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    state: StateValue
    questions: Mapping[str, Question]
    model: str | None = None
    session_id: str | None = None

    def to_api(self, *, model: str) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self.model or model,
            "state": self.state,
            "questions": {
                check_id: question.to_api() for check_id, question in self.questions.items()
            },
        }
        if self.session_id:
            body["session_id"] = self.session_id
        return body


@dataclass(frozen=True, slots=True)
class NoulAnswer:
    noul: float


@dataclass(frozen=True, slots=True)
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float] | None
    confidence: float | None


@dataclass(frozen=True, slots=True)
class ScoreAnswer:
    score: float
    probabilities: dict[str, float] | None
    confidence: float | None
    legend: dict[str, str] | None


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


@dataclass(frozen=True, slots=True)
class DecisionResult:
    model: str
    answers: Mapping[str, Answer]
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_ms: int
    request_id: str | None
    provider: str | None


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    kind: str
    decision: str
    summary: str
    reasons: tuple[str, ...]
    probabilities: dict[str, float]
    drives_gate: bool
    noul: float | None = None
    choice: str | None = None
    score: float | None = None
    confidence: float | None = None
    legend: dict[str, str] | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "kind": self.kind,
            "decision": self.decision,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "probabilities": self.probabilities,
            "drives_gate": self.drives_gate,
            "noul": self.noul,
            "choice": self.choice,
            "score": self.score,
            "confidence": self.confidence,
            "legend": self.legend,
        }
