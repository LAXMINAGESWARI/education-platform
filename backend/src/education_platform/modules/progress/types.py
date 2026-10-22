from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from education_platform.modules.generation.schemas import GenerationRunOut
from education_platform.modules.progress.events import ProgressEvent
from education_platform.modules.rag.schemas import (
    KnowledgeVersionStatusOut,
    MaterialVersionStatusOut,
)

type ProgressSnapshot = GenerationRunOut | MaterialVersionStatusOut | KnowledgeVersionStatusOut


@dataclass(frozen=True, slots=True)
class RunSubject:
    id: UUID

    def key(self) -> str:
        return f"run:{self.id}"


@dataclass(frozen=True, slots=True)
class VersionSubject:
    id: UUID

    def key(self) -> str:
        return f"version:{self.id}"


type ProgressSubject = RunSubject | VersionSubject


def run_subject(run_id: UUID) -> RunSubject:
    return RunSubject(id=run_id)


def version_subject(version_id: UUID) -> VersionSubject:
    return VersionSubject(id=version_id)


def parse_subject_key(raw: str) -> ProgressSubject | None:
    kind, sep, rest = raw.partition(":")
    if sep == "" or rest == "":
        return None
    try:
        uid = UUID(rest)
    except ValueError:
        return None
    if kind == "run":
        return run_subject(uid)
    if kind == "version":
        return version_subject(uid)
    return None


@dataclass(frozen=True, slots=True)
class EventId:
    value: str


def event_id_for(subject: ProgressSubject, snapshot: ProgressSnapshot) -> EventId:
    digest = hashlib.sha256(snapshot.model_dump_json().encode("utf-8")).hexdigest()[:12]
    return EventId(f"{subject.key()}:{digest}")


def event_id_from_header(raw: str | None) -> EventId | None:
    if raw is None or raw.strip() == "":
        return None
    return EventId(raw.strip())


@dataclass(frozen=True, slots=True)
class SnapshotFrame:
    event_id: EventId
    snapshot: ProgressSnapshot
    close_after: bool


@dataclass(frozen=True, slots=True)
class ProgressEventFrame:
    """Upload progress built from the same snapshot as ``SnapshotFrame``."""

    event_id: EventId
    event: ProgressEvent


@dataclass(frozen=True, slots=True)
class HeartbeatFrame:
    """SSE comment. ``connected`` flushes headers; ``heartbeat`` is the 15s keepalive."""

    comment: Literal["heartbeat", "connected"] = "heartbeat"


@dataclass(frozen=True, slots=True)
class CloseFrame:
    reason: Literal["terminal"]


@dataclass(frozen=True, slots=True)
class ErrorFrame:
    status: int
    detail: str


type ProgressFrame = SnapshotFrame | ProgressEventFrame | HeartbeatFrame | CloseFrame | ErrorFrame
