"""One-method call surface so tests can pass a fake OpenRouter client."""

from __future__ import annotations

from typing import Protocol

from education_platform.modules.judge.types import DecisionRequest, DecisionResult


class Judge(Protocol):
    def decide(self, request: DecisionRequest) -> DecisionResult:
        """Score every question against one shared state."""
