"""Shared guard values that must not import the service (avoids a cycle)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GuardTurn:
    role: str
    content: str
