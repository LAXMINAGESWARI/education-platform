"""Read-only admin view of screened messages."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class GuardVerdictOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    role: str
    surface: str
    message_sha256: str
    message_preview: str
    injection_probability: float | None
    exfiltration_probability: float | None
    topic: str | None
    topic_probabilities: dict[str, Any] | None
    decision: str
    source: str
    latency_ms: int
    created_at: datetime
