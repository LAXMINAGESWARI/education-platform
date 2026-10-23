"""Shared progress-event envelope. Job streams send this beside the snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from education_platform.modules.rag.schemas import (
    KnowledgeVersionStatusOut,
    MaterialVersionStatusOut,
)

SCHEMA_VERSION: Literal[1] = 1
MIN_INTERVAL_SECONDS = 1.0
MIN_PERCENT_STEP = 5

RECEIVED_MESSAGE = "File received."
PARSING_MESSAGE = "Reading the document…"
EXTRACTING_MESSAGE = "Noting section headings."
DONE_MESSAGE = "Ready."
RESTART_MESSAGE = "Restarting..."

MESSAGE_MISSING_FILE = "The uploaded file could not be found."
MESSAGE_EMPTY = "No readable text was found in this document."
MESSAGE_UNREADABLE = "The document could not be read."
MESSAGE_INDEX = "The document could not be indexed."
MESSAGE_NOT_LINKED = "This upload is missing required information."
MESSAGE_INTERRUPTED = "Processing was interrupted and could not finish."
MESSAGE_UNKNOWN = "The upload could not be processed."

UploadStageName = Literal[
    "received",
    "parsing",
    "chunking",
    "embedding",
    "extracting_topics",
    "done",
    "failed",
]
ProgressStatusName = Literal["running", "done", "failed"]
ProgressUnitName = Literal["page", "chunk"]

# Band start and width. Width is the stage's share of the overall percent.
_UPLOAD_BANDS: dict[str, tuple[int, int] | None] = {
    "received": (0, 0),
    "parsing": (0, 40),
    "chunking": (40, 10),
    "embedding": (50, 40),
    "extracting_topics": (90, 10),
    "done": (100, 0),
    "failed": None,
}

_UPLOAD_STAGES = frozenset(_UPLOAD_BANDS)


class ProgressEvent(BaseModel):
    """Full current state for one subject. Not a delta."""

    v: Literal[1] = SCHEMA_VERSION
    kind: Literal["upload", "generation", "text_to_sql", "policy_chat"]
    subject_id: UUID
    seq: int = Field(ge=1)
    stage: str
    status: ProgressStatusName
    percent: int | None = Field(default=None, ge=0, le=100)
    current: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    unit: ProgressUnitName | None = None
    message: str | None = None
    updated_at: datetime


def overall_percent(stage: str, current: int | None, total: int | None) -> int | None:
    """Overall percent for an upload stage. Failed is null; missing counts sit at the band start."""
    band = _UPLOAD_BANDS.get(stage)
    if band is None:
        return None
    start, weight = band
    if stage == "done":
        return 100
    if current is None or total is None or total <= 0:
        return start
    fraction = min(1.0, max(0.0, current / total))
    value = round(start + weight * fraction)
    return max(0, min(100, int(value)))


def progress_unit(stage: str, current: int | None, total: int | None) -> ProgressUnitName | None:
    if current is None or total is None:
        return None
    if stage == "parsing":
        return "page"
    if stage in {"chunking", "embedding"}:
        return "chunk"
    return None


def progress_status(stage: str) -> ProgressStatusName:
    if stage == "done":
        return "done"
    if stage == "failed":
        return "failed"
    return "running"


def should_persist_stage(
    *,
    previous_stage: str | None,
    previous_current: int | None,
    previous_total: int | None,
    previous_updated_at: datetime | None,
    stage: str,
    current: int | None,
    total: int | None,
    now: datetime,
    min_interval_seconds: float = MIN_INTERVAL_SECONDS,
    min_percent_step: int = MIN_PERCENT_STEP,
) -> bool:
    """True on a stage change, a percent jump of at least 5, or once a second has passed."""
    if previous_stage != stage:
        return True
    previous_percent = overall_percent(previous_stage, previous_current, previous_total)
    next_percent = overall_percent(stage, current, total)
    if (
        previous_percent is not None
        and next_percent is not None
        and abs(next_percent - previous_percent) >= min_percent_step
    ):
        return True
    if previous_updated_at is None:
        return True
    return (now - previous_updated_at).total_seconds() >= min_interval_seconds


def same_stage_state(
    *,
    previous_stage: str | None,
    previous_current: int | None,
    previous_total: int | None,
    previous_message: str | None,
    stage: str,
    current: int | None,
    total: int | None,
    message: str | None,
) -> bool:
    return (
        previous_stage == stage
        and previous_current == current
        and previous_total == total
        and previous_message == message
    )


def friendly_failure_message(stage: str | None, reason: str) -> str:
    """User-facing failure. The raw reason is only used to pick a category."""
    text = reason.casefold()
    if "no extractable text" in text:
        return MESSAGE_EMPTY
    if "missing blob" in text or "no such file" in text or "file not found" in text:
        return MESSAGE_MISSING_FILE
    if "worker lost this job" in text:
        return MESSAGE_INTERRUPTED
    if (
        "unable to resolve institution" in text
        or "source material missing" in text
        or "knowledge document missing" in text
        or "no target" in text
        or "version not found" in text
        or "invalid ingest" in text
    ):
        return MESSAGE_NOT_LINKED
    if stage in {"embedding", "extracting_topics"}:
        return MESSAGE_INDEX
    if stage in {"received", "parsing", "chunking"}:
        return MESSAGE_UNREADABLE
    return MESSAGE_UNKNOWN


def chunking_message(count: int) -> str:
    noun = "section" if count == 1 else "sections"
    return f"Splitting the document into {count} {noun}."


def embedding_message(current: int, total: int) -> str:
    return f"Embedding {current} of {total} chunks."


def progress_for_snapshot(snapshot: object) -> ProgressEvent | None:
    """Upload progress for a version snapshot. Generation snapshots stay snapshot-only."""
    if isinstance(snapshot, (MaterialVersionStatusOut, KnowledgeVersionStatusOut)):
        return progress_from_snapshot(snapshot)
    return None


def progress_from_snapshot(
    snapshot: MaterialVersionStatusOut | KnowledgeVersionStatusOut,
) -> ProgressEvent | None:
    """Build the upload event carried by a version snapshot. None when no stage is stored."""
    stage = snapshot.stage
    if (
        not isinstance(stage, str)
        or stage not in _UPLOAD_STAGES
        or snapshot.stage_updated_at is None
        or snapshot.stage_seq < 1
    ):
        return None
    return ProgressEvent(
        kind="upload",
        subject_id=snapshot.id,
        seq=snapshot.stage_seq,
        stage=stage,
        status=progress_status(stage),
        percent=overall_percent(stage, snapshot.stage_current, snapshot.stage_total),
        current=snapshot.stage_current,
        total=snapshot.stage_total,
        unit=progress_unit(stage, snapshot.stage_current, snapshot.stage_total),
        message=snapshot.stage_message,
        updated_at=snapshot.stage_updated_at,
    )
