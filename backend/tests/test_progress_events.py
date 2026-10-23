"""Upload stage progress on the existing job SSE."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from tests.progress_http import open_progress_server

from education_platform.core.config import get_settings
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.audit.models import AuditEvent
from education_platform.modules.audit.service import (
    SUBSCRIBE_AUDIT_WINDOW,
    progress_subscribe_dedupe_key,
    record_event_within_window,
)
from education_platform.modules.auth.models import User, UserStatus
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.generation.models import ContentGenerationRun
from education_platform.modules.generation.types import RunPhase
from education_platform.modules.materials.models import (
    SourceMaterial,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
)
from education_platform.modules.progress.events import (
    MESSAGE_INDEX,
    RESTART_MESSAGE,
    friendly_failure_message,
    overall_percent,
    progress_from_snapshot,
    should_persist_stage,
)
from education_platform.modules.progress.sse import iter_sse
from education_platform.modules.progress.stage_store import (
    StageClaim,
    persist_upload_stage,
    reset_stage_on_requeue,
    stage_claim_matches_job,
)
from education_platform.modules.progress.subscribe import subscribe
from education_platform.modules.progress.types import (
    CloseFrame,
    EventId,
    ProgressEventFrame,
    SnapshotFrame,
    event_id_for,
    version_subject,
)
from education_platform.modules.progress.wake import WakeResult
from education_platform.modules.rag import storage
from education_platform.modules.rag.chunking import TextChunk, content_hash
from education_platform.modules.rag.models import (
    IngestJob,
    IngestJobStatus,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeDocumentVersionStatus,
)
from education_platform.modules.rag.schemas import MaterialVersionStatusOut
from education_platform.workers.ingest import _live_claim, process_ingest_job_sync
from education_platform.workers.lease import Claimed, OwnedLease, bind_lease, claim, release_lease
from education_platform.workers.runner import poll_once
from test_rag_ingest import TINY_PDF

SEEDED_SLUG = "rectangles_squares_properties"

_NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _principal() -> Principal:
    return Principal(
        user_id=uuid4(),
        institution_id=uuid4(),
        email="admin@example.com",
        roles=frozenset({"administrator"}),
        student_profile_id=None,
        status="active",
    )


def _scope(principal: Principal) -> Scope:
    return Scope(
        institution_id=principal.institution_id,
        roles=principal.roles,
        unrestricted=True,
        taught_offering_sections=frozenset(),
        enrolled_offering_sections=frozenset(),
        student_ids=frozenset(),
        self_student_id=None,
    )


class SequenceReader:
    def __init__(self, snapshots: list[MaterialVersionStatusOut]) -> None:
        self._snapshots = list(snapshots)

    async def authorize_and_read(
        self,
        principal: Principal,
        scope: Scope,
        subject: object,
        *,
        heal: bool,
    ) -> MaterialVersionStatusOut:
        del principal, scope, subject, heal
        return self._snapshots.pop(0)


class FakeWakeWaiter:
    def __init__(self, results: list[WakeResult] | None = None) -> None:
        self._results = list(results or [])

    async def wait(self, subject: object, timeout: float) -> WakeResult:
        del subject
        if self._results:
            return self._results.pop(0)
        if timeout <= 0:
            return WakeResult.TIMED_OUT
        return WakeResult.NOTIFIED

    async def aclose(self) -> None:
        return None


def _chunks(count: int) -> list[TextChunk]:
    rows: list[TextChunk] = []
    for index in range(count):
        text = f"Section {index}\nBody {index} explains the idea in a full sentence."
        rows.append(
            TextChunk(
                ordinal=index + 1,
                text=text,
                content_hash=content_hash(text),
                token_count=8,
                page_number=1,
                section_heading="Properties" if index == 0 else None,
            )
        )
    return rows


def _version_out(**overrides: object) -> MaterialVersionStatusOut:
    payload: dict[str, object] = {
        "id": uuid4(),
        "source_material_id": uuid4(),
        "version_number": 1,
        "title": "Lesson",
        "lifecycle_status": "processing",
        "failure_reason": None,
        "chunk_count": 0,
        "blob_content_type": "application/pdf",
        "stage": "parsing",
        "stage_seq": 2,
        "stage_current": None,
        "stage_total": None,
        "stage_message": "Reading the document…",
        "stage_updated_at": _NOW,
    }
    payload.update(overrides)
    return MaterialVersionStatusOut.model_validate(payload)


@pytest.fixture()
def seeded_subtopic_id(seeded_db: Session):
    subtopic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    return subtopic.id


def _store_version(
    session: Session,
    *,
    subtopic_id: object,
    stage: str | None = None,
    stage_seq: int = 0,
    stage_current: int | None = None,
    stage_total: int | None = None,
    stage_message: str | None = None,
    attempts: int = 0,
    status: IngestJobStatus = IngestJobStatus.QUEUED,
    locked_until: datetime | None = None,
) -> tuple[SourceMaterialVersion, IngestJob]:
    material = SourceMaterial(
        subtopic_id=subtopic_id,
        title="Progress lesson",
        slug=f"progress-{uuid4().hex[:8]}",
    )
    session.add(material)
    session.flush()
    object_key = storage.build_object_key(
        institution_id=uuid4(),
        kind="source_materials",
        filename="lesson.pdf",
    )
    storage.store_bytes(object_key, TINY_PDF)
    version = SourceMaterialVersion(
        source_material_id=material.id,
        version_number=1,
        lifecycle_status=SourceMaterialVersionStatus.PROCESSING,
        title="Progress lesson",
        content_format="pdf",
        blob_object_key=object_key,
        blob_content_type="application/pdf",
        checksum=storage.sha256_hex(TINY_PDF),
        stage=stage,
        stage_seq=stage_seq,
        stage_current=stage_current,
        stage_total=stage_total,
        stage_message=stage_message,
        stage_updated_at=_NOW if stage is not None else None,
    )
    session.add(version)
    session.flush()
    job = IngestJob(
        source_material_version_id=version.id,
        status=status,
        attempts=attempts,
        locked_until=locked_until,
    )
    session.add(job)
    session.commit()
    return version, job


def test_upload_percent_uses_fixed_stage_weights() -> None:
    assert overall_percent("received", None, None) == 0
    assert overall_percent("parsing", None, None) == 0
    assert overall_percent("chunking", 4, 4) == 50
    assert overall_percent("embedding", 5, 10) == 70
    assert overall_percent("extracting_topics", None, None) == 90
    assert overall_percent("done", None, None) == 100
    assert overall_percent("failed", 5, 10) is None


def test_stage_writes_throttle_inside_one_second_unless_percent_moves() -> None:
    updated = _NOW
    assert (
        should_persist_stage(
            previous_stage="embedding",
            previous_current=0,
            previous_total=100,
            previous_updated_at=updated,
            stage="embedding",
            current=1,
            total=100,
            now=updated + timedelta(milliseconds=200),
        )
        is False
    )
    assert (
        should_persist_stage(
            previous_stage="embedding",
            previous_current=0,
            previous_total=100,
            previous_updated_at=updated,
            stage="embedding",
            current=13,
            total=100,
            now=updated + timedelta(milliseconds=200),
        )
        is True
    )
    assert (
        should_persist_stage(
            previous_stage="parsing",
            previous_current=None,
            previous_total=None,
            previous_updated_at=updated,
            stage="chunking",
            current=4,
            total=4,
            now=updated + timedelta(milliseconds=10),
        )
        is True
    )


def test_stage_claim_matches_attempts_and_optional_lease_token() -> None:
    locked = _NOW
    assert (
        stage_claim_matches_job(
            job_status="running",
            job_attempts=2,
            job_locked_until=locked,
            claim=StageClaim(attempts=2),
        )
        is True
    )
    assert (
        stage_claim_matches_job(
            job_status="running",
            job_attempts=2,
            job_locked_until=locked,
            claim=StageClaim(attempts=1),
        )
        is False
    )
    assert (
        stage_claim_matches_job(
            job_status="queued",
            job_attempts=2,
            job_locked_until=None,
            claim=StageClaim(attempts=2),
        )
        is False
    )
    assert (
        stage_claim_matches_job(
            job_status="running",
            job_attempts=2,
            job_locked_until=locked,
            claim=StageClaim(attempts=2, locked_until=locked),
        )
        is True
    )
    assert (
        stage_claim_matches_job(
            job_status="running",
            job_attempts=2,
            job_locked_until=locked,
            claim=StageClaim(attempts=2, locked_until=locked + timedelta(seconds=1)),
        )
        is False
    )


def test_friendly_failure_hides_paths() -> None:
    reason = "embed failed at /var/secret/lesson.pdf"
    message = friendly_failure_message("embedding", reason)
    assert message == MESSAGE_INDEX
    assert "/var/secret" not in message
    assert "pdf" not in message


def test_persist_skips_sub_five_percent_inside_one_second(
    seeded_db: Session, seeded_subtopic_id: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    version, job = _store_version(
        seeded_db,
        subtopic_id=seeded_subtopic_id,
        status=IngestJobStatus.RUNNING,
        attempts=1,
    )
    claim = StageClaim(attempts=1)
    first = persist_upload_stage(
        version,
        job,
        claim=claim,
        stage="embedding",
        message="Embedding 0 of 100 chunks.",
        current=0,
        total=100,
        now=_NOW,
        force=True,
    )
    skipped = persist_upload_stage(
        version,
        job,
        claim=claim,
        stage="embedding",
        message="Embedding 1 of 100 chunks.",
        current=1,
        total=100,
        now=_NOW + timedelta(milliseconds=200),
    )
    written = persist_upload_stage(
        version,
        job,
        claim=claim,
        stage="embedding",
        message="Embedding 13 of 100 chunks.",
        current=13,
        total=100,
        now=_NOW + timedelta(milliseconds=400),
    )
    assert first == 1
    assert skipped is None
    assert written == 2
    seeded_db.expire_all()
    refreshed = seeded_db.get(SourceMaterialVersion, version.id)
    assert refreshed is not None
    assert refreshed.stage_current == 13
    assert refreshed.stage_seq == 2
    get_settings.cache_clear()


def test_successful_ingest_emits_monotonic_stages(
    seeded_db: Session,
    seeded_subtopic_id: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "education_platform.workers.ingest.embed_texts",
        lambda texts: [[0.02] * 384 for _ in texts],
    )
    monkeypatch.setattr("education_platform.workers.ingest.EMBEDDING_PROGRESS_BATCH", 1)
    seen: list[tuple[str, int | None, int | None, int | None]] = []
    real = persist_upload_stage

    def spy(
        version: SourceMaterialVersion,
        job: IngestJob,
        *,
        claim: StageClaim,
        stage: str,
        message: str | None,
        current: int | None = None,
        total: int | None = None,
        now: datetime | None = None,
        force: bool = False,
    ) -> int | None:
        seq = real(
            version,
            job,
            claim=claim,
            stage=stage,
            message=message,
            current=current,
            total=total,
            now=now,
            force=force,
        )
        seen.append((stage, current, total, seq))
        return seq

    monkeypatch.setattr("education_platform.workers.ingest.persist_upload_stage", spy)
    version, job = _store_version(seeded_db, subtopic_id=seeded_subtopic_id)
    chunks = _chunks(4)
    process_ingest_job_sync(str(job.id), parse_pdf=lambda _path: chunks)

    stages = [item[0] for item in seen]
    assert stages[:3] == ["received", "parsing", "chunking"]
    assert stages[-1] == "extracting_topics"
    embedding = [item for item in seen if item[0] == "embedding"]
    assert [item[1] for item in embedding] == [1, 2, 3, 4]
    assert embedding[-1][1] == 4
    assert embedding[-1][2] == 4
    seqs = [item[3] for item in seen]
    assert all(seq is not None for seq in seqs)
    assert seqs == sorted(seqs)
    assert seqs[0] == 1
    percents = [overall_percent(stage, current, total) for stage, current, total, _seq in seen]
    assert percents == sorted(value for value in percents if value is not None)

    seeded_db.expire_all()
    refreshed = seeded_db.get(SourceMaterialVersion, version.id)
    assert refreshed is not None
    assert refreshed.lifecycle_status is SourceMaterialVersionStatus.READY
    assert refreshed.stage == "done"
    assert refreshed.stage_message == "Ready."
    assert refreshed.stage_seq == seqs[-1] + 1
    assert overall_percent("done", None, None) == 100
    get_settings.cache_clear()


def test_embedding_failure_closes_stream_with_a_friendly_message(
    client: TestClient,
    seeded_db: Session,
    seeded_subtopic_id: object,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    monkeypatch.setattr("education_platform.workers.ingest.EMBEDDING_PROGRESS_BATCH", 1)
    calls = {"n": 0}

    def boom(texts: list[str]) -> list[list[float]]:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("embed failed at /var/secret/lesson.pdf")
        return [[0.02] * 384 for _ in texts]

    monkeypatch.setattr("education_platform.workers.ingest.embed_texts", boom)
    version, job = _store_version(seeded_db, subtopic_id=seeded_subtopic_id)
    process_ingest_job_sync(str(job.id), parse_pdf=lambda _path: _chunks(2))

    seeded_db.expire_all()
    refreshed = seeded_db.get(SourceMaterialVersion, version.id)
    assert refreshed is not None
    assert refreshed.lifecycle_status is SourceMaterialVersionStatus.FAILED
    assert refreshed.stage == "failed"
    assert refreshed.stage_message == MESSAGE_INDEX
    assert refreshed.failure_reason is not None
    assert "embed failed" in refreshed.failure_reason

    with client.stream(
        "GET", f"/api/v1/progress/versions/{version.id}", headers=admin_headers
    ) as response:
        assert response.status_code == 200
        body = b"".join(response.iter_bytes()).decode()
    assert "event: snapshot" in body
    assert '"lifecycle_status":"failed"' in body
    assert "event: progress" in body
    assert "event: close" in body
    progress = _progress_payloads(body)
    assert progress[-1]["stage"] == "failed"
    assert progress[-1]["status"] == "failed"
    assert progress[-1]["message"] == MESSAGE_INDEX
    assert progress[-1]["percent"] is None
    assert "/var/secret" not in json.dumps(progress[-1])
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_reconnect_receives_only_the_current_stage() -> None:
    parsing = _version_out()
    embedding = parsing.model_copy(
        update={
            "stage": "embedding",
            "stage_seq": 4,
            "stage_current": 2,
            "stage_total": 10,
            "stage_message": "Embedding 2 of 10 chunks.",
            "stage_updated_at": _NOW + timedelta(seconds=5),
        }
    )
    done = embedding.model_copy(
        update={
            "stage": "done",
            "stage_seq": 6,
            "lifecycle_status": "ready",
            "stage_current": None,
            "stage_total": None,
            "stage_message": "Ready.",
            "chunk_count": 10,
            "stage_updated_at": _NOW + timedelta(seconds=8),
        }
    )
    subject = version_subject(parsing.id)
    principal = _principal()
    scope = _scope(principal)
    old_id = event_id_for(subject, parsing)
    frames = [
        frame
        async for frame in subscribe(
            principal,
            scope,
            subject,
            last_event_id=old_id,
            reader=SequenceReader([embedding, done]),
            wakes=FakeWakeWaiter([WakeResult.TIMED_OUT, WakeResult.NOTIFIED]),
        )
    ]
    progress = [frame.event.stage for frame in frames if isinstance(frame, ProgressEventFrame)]
    assert progress == ["embedding", "done"]
    assert isinstance(frames[-1], CloseFrame)

    current_id = event_id_for(subject, embedding)
    again = [
        frame
        async for frame in subscribe(
            principal,
            scope,
            subject,
            last_event_id=current_id,
            reader=SequenceReader([embedding, done]),
            wakes=FakeWakeWaiter([WakeResult.TIMED_OUT, WakeResult.NOTIFIED]),
        )
    ]
    again_progress = [frame.event for frame in again if isinstance(frame, ProgressEventFrame)]
    assert [event.stage for event in again_progress] == ["done"]
    assert again_progress[0].seq == 6


@pytest.mark.asyncio
async def test_iter_sse_keeps_snapshot_and_adds_progress() -> None:
    snapshot = _version_out(stage="embedding", stage_seq=3, stage_current=2, stage_total=8)
    progress = progress_from_snapshot(snapshot)
    assert progress is not None

    async def frames():
        yield SnapshotFrame(
            event_id=EventId("version:1:abc"),
            snapshot=snapshot,
            close_after=False,
        )
        yield ProgressEventFrame(event_id=EventId("version:1:abc"), event=progress)

    body = "".join([chunk.decode() async for chunk in iter_sse(frames())])
    assert "event: snapshot" in body
    assert '"lifecycle_status":"processing"' in body
    assert '"failure_reason":null' in body
    assert '"chunk_count":0' in body
    assert "event: progress" in body
    assert '"kind":"upload"' in body
    assert '"stage":"embedding"' in body


def test_expired_lease_resets_the_stage(
    seeded_db: Session, seeded_subtopic_id: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    version, job = _store_version(
        seeded_db,
        subtopic_id=seeded_subtopic_id,
        stage="parsing",
        stage_seq=3,
        stage_message="Reading the document…",
        attempts=1,
        status=IngestJobStatus.RUNNING,
        locked_until=datetime.now(UTC) - timedelta(minutes=5),
    )
    claimed = claim(seeded_db, IngestJob)
    assert isinstance(claimed, Claimed)
    assert claimed.job_id == job.id
    seeded_db.expire_all()
    refreshed = seeded_db.get(SourceMaterialVersion, version.id)
    assert refreshed is not None
    assert refreshed.stage == "received"
    assert refreshed.stage_message == RESTART_MESSAGE
    assert refreshed.stage_current is None
    assert refreshed.stage_total is None
    assert refreshed.stage_seq == 4
    get_settings.cache_clear()


def test_fresh_queue_is_not_restarted(
    seeded_db: Session, seeded_subtopic_id: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    version, job = _store_version(
        seeded_db,
        subtopic_id=seeded_subtopic_id,
        stage="received",
        stage_seq=1,
        stage_message="File received.",
        attempts=0,
        status=IngestJobStatus.QUEUED,
    )
    claimed = claim(seeded_db, IngestJob)
    assert isinstance(claimed, Claimed)
    assert claimed.job_id == job.id
    seeded_db.expire_all()
    refreshed = seeded_db.get(SourceMaterialVersion, version.id)
    assert refreshed is not None
    assert refreshed.stage == "received"
    assert refreshed.stage_message == "File received."
    assert refreshed.stage_seq == 1
    get_settings.cache_clear()


def test_sigterm_requeue_shape_resets_on_read_and_claim(
    client: TestClient,
    seeded_db: Session,
    seeded_subtopic_id: object,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A requeue that kept its attempt is shown as Restarting without a write.

    PR #200 at 2a00407 does not leave this shape on a graceful SIGTERM: that
    release refunds the attempt. This covers a queued row whose attempt was kept.
    """
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    version, first_job = _store_version(
        seeded_db,
        subtopic_id=seeded_subtopic_id,
        stage="embedding",
        stage_seq=5,
        stage_current=4,
        stage_total=10,
        stage_message="Embedding 4 of 10 chunks.",
        attempts=1,
        status=IngestJobStatus.QUEUED,
        locked_until=None,
    )
    status = client.get(f"/api/v1/admin/material-versions/{version.id}", headers=admin_headers)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["stage"] == "received"
    assert body["stage_message"] == RESTART_MESSAGE
    assert body["stage_current"] is None
    assert body["stage_seq"] == 6
    seeded_db.expire_all()
    stored = seeded_db.get(SourceMaterialVersion, version.id)
    assert stored is not None
    assert stored.stage == "embedding"
    assert stored.stage_seq == 5
    finished = seeded_db.get(IngestJob, first_job.id)
    assert finished is not None
    finished.status = IngestJobStatus.SUCCEEDED
    seeded_db.commit()

    version_b, job_b = _store_version(
        seeded_db,
        subtopic_id=seeded_subtopic_id,
        stage="parsing",
        stage_seq=2,
        stage_current=3,
        stage_total=8,
        stage_message="Reading the document…",
        attempts=1,
        status=IngestJobStatus.QUEUED,
        locked_until=None,
    )
    claimed = claim(seeded_db, IngestJob)
    assert isinstance(claimed, Claimed)
    assert claimed.job_id == job_b.id
    seeded_db.expire_all()
    claimed_version = seeded_db.get(SourceMaterialVersion, version_b.id)
    assert claimed_version is not None
    assert claimed_version.stage_message == RESTART_MESSAGE
    assert claimed_version.stage_current is None
    assert claimed_version.stage_seq == 3
    get_settings.cache_clear()


def test_refunded_sigterm_stays_until_release_resets(
    client: TestClient,
    seeded_db: Session,
    seeded_subtopic_id: object,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A refunded attempt looks like a first queue, so only the release may reset it."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    version, job = _store_version(
        seeded_db,
        subtopic_id=seeded_subtopic_id,
        stage="embedding",
        stage_seq=5,
        stage_current=4,
        stage_total=10,
        stage_message="Embedding 4 of 10 chunks.",
        attempts=0,
        status=IngestJobStatus.QUEUED,
        locked_until=None,
    )
    status = client.get(f"/api/v1/admin/material-versions/{version.id}", headers=admin_headers)
    assert status.status_code == 200, status.text
    assert status.json()["stage"] == "embedding"
    assert status.json()["stage_seq"] == 5

    reset_stage_on_requeue(seeded_db, job)
    seeded_db.commit()
    seeded_db.expire_all()
    restarted = seeded_db.get(SourceMaterialVersion, version.id)
    assert restarted is not None
    assert restarted.stage == "received"
    assert restarted.stage_message == RESTART_MESSAGE
    assert restarted.stage_current is None
    assert restarted.stage_total is None
    assert restarted.stage_seq == 6
    get_settings.cache_clear()


def _progress_payloads(body: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for block in body.split("\n\n"):
        if "event: progress" not in block:
            continue
        data = next(line[5:].strip() for line in block.splitlines() if line.startswith("data:"))
        payloads.append(json.loads(data))
    return payloads


@pytest.fixture()
def admin_headers(client: TestClient) -> dict[str, str]:
    settings = get_settings()
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.demo_admin_email,
            "password": settings.demo_admin_password,
            "institution_name": "POC Demo School",
        },
    )
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["access_token"]}


def _expire_running(
    session: Session,
    version: SourceMaterialVersion | KnowledgeDocumentVersion,
    job: IngestJob,
) -> None:
    version.stage = "embedding"
    version.stage_seq = 4
    version.stage_current = 16
    version.stage_total = 64
    version.stage_message = "Embedding 16 of 64 chunks."
    version.stage_updated_at = datetime.now(UTC)
    job.status = IngestJobStatus.RUNNING
    job.attempts = 1
    job.locked_until = datetime.now(UTC) - timedelta(minutes=5)
    session.commit()


@pytest.mark.regression
def test_status_api_after_lease_expiry_without_a_stream(
    client: TestClient,
    seeded_db: Session,
    seeded_subtopic_id: object,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    material, material_job = _store_version(seeded_db, subtopic_id=seeded_subtopic_id)
    _expire_running(seeded_db, material, material_job)
    admin = seeded_db.scalar(select(User).where(User.email == get_settings().demo_admin_email))
    assert admin is not None
    document = KnowledgeDocument(
        institution_id=admin.institution_id,
        title="Expired handbook",
        slug=f"expired-{uuid4().hex[:8]}",
        doc_type="handbook",
        required_roles=["administrator"],
    )
    seeded_db.add(document)
    seeded_db.flush()
    object_key = storage.build_object_key(
        institution_id=document.institution_id,
        kind="knowledge_documents",
        filename="handbook.pdf",
    )
    storage.store_bytes(object_key, TINY_PDF)
    knowledge = KnowledgeDocumentVersion(
        document_id=document.id,
        version_number=1,
        lifecycle_status=KnowledgeDocumentVersionStatus.PROCESSING,
        blob_object_key=object_key,
        blob_content_type="application/pdf",
        checksum=storage.sha256_hex(TINY_PDF),
    )
    seeded_db.add(knowledge)
    seeded_db.flush()
    knowledge_job = IngestJob(knowledge_document_version_id=knowledge.id)
    seeded_db.add(knowledge_job)
    seeded_db.commit()
    _expire_running(seeded_db, knowledge, knowledge_job)

    material_status = client.get(
        f"/api/v1/admin/material-versions/{material.id}", headers=admin_headers
    )
    knowledge_status = client.get(
        f"/api/v1/admin/knowledge-document-versions/{knowledge.id}",
        headers=admin_headers,
    )
    assert material_status.status_code == 200, material_status.text
    assert knowledge_status.status_code == 200, knowledge_status.text
    for body in (material_status.json(), knowledge_status.json()):
        assert body["stage"] == "received"
        assert body["stage_message"] == RESTART_MESSAGE
        assert body["stage_current"] is None
        assert body["stage_total"] is None
        assert body["stage_seq"] == 5
    seeded_db.expire_all()
    stored_material = seeded_db.get(SourceMaterialVersion, material.id)
    stored_knowledge = seeded_db.get(KnowledgeDocumentVersion, knowledge.id)
    assert stored_material is not None and stored_material.stage == "embedding"
    assert stored_knowledge is not None and stored_knowledge.stage == "embedding"
    assert stored_material.stage_message == "Embedding 16 of 64 chunks."
    get_settings.cache_clear()


@pytest.mark.regression
def test_two_quick_stage_writes_show_the_latest_state(
    client: TestClient,
    seeded_db: Session,
    seeded_subtopic_id: object,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    now = datetime.now(UTC)
    version, job = _store_version(
        seeded_db,
        subtopic_id=seeded_subtopic_id,
        status=IngestJobStatus.RUNNING,
        attempts=1,
        locked_until=now + timedelta(minutes=5),
    )
    claim = StageClaim(attempts=1)
    first = persist_upload_stage(
        version,
        job,
        claim=claim,
        stage="chunking",
        message="Splitting the document into 4 sections.",
        current=4,
        total=4,
        now=now,
        force=True,
    )
    second = persist_upload_stage(
        version,
        job,
        claim=claim,
        stage="embedding",
        message="Embedding 1 of 4 chunks.",
        current=1,
        total=4,
        now=now + timedelta(milliseconds=5),
        force=True,
    )
    assert first == 1
    assert second == 2
    url = f"/api/v1/progress/versions/{version.id}"
    with (
        open_progress_server() as base,
        httpx.Client(base_url=base, timeout=5) as http,
        http.stream("GET", url, headers=admin_headers) as response,
    ):
        assert response.status_code == 200
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if b"event: progress" in body:
                break
    payloads = _progress_payloads(body.decode())
    assert len(payloads) == 1
    assert payloads[0]["stage"] == "embedding"
    assert payloads[0]["seq"] == 2
    assert payloads[0]["current"] == 1
    assert payloads[0]["seq"] != first
    get_settings.cache_clear()


@pytest.mark.regression
def test_stale_worker_stage_write_leaves_reclaimed_stage(
    client: TestClient,
    seeded_db: Session,
    seeded_subtopic_id: object,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Worker A keeps its claimed attempts. After B reclaims, A's stage write is a no-op."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    version, job = _store_version(
        seeded_db,
        subtopic_id=seeded_subtopic_id,
        stage="embedding",
        stage_seq=5,
        stage_current=16,
        stage_total=100,
        stage_message="Embedding 16 of 100 chunks.",
        attempts=0,
        status=IngestJobStatus.QUEUED,
    )
    claimed = claim(seeded_db, IngestJob)
    assert isinstance(claimed, Claimed)
    assert claimed.job_id == job.id
    seeded_db.expire_all()
    owned = seeded_db.get(IngestJob, job.id)
    assert owned is not None
    assert owned.attempts == 1
    a_claim = StageClaim(attempts=owned.attempts)
    owned.locked_until = datetime.now(UTC) - timedelta(minutes=5)
    seeded_db.commit()

    reclaimed = claim(seeded_db, IngestJob)
    assert isinstance(reclaimed, Claimed)
    assert reclaimed.job_id == job.id
    seeded_db.expire_all()
    stored = seeded_db.get(SourceMaterialVersion, version.id)
    running = seeded_db.get(IngestJob, job.id)
    assert stored is not None and running is not None
    assert running.attempts == 2
    assert stored.stage == "received"
    assert stored.stage_message == RESTART_MESSAGE
    kept_seq = stored.stage_seq

    with caplog.at_level(logging.INFO):
        skipped = persist_upload_stage(
            stored,
            running,
            claim=a_claim,
            stage="embedding",
            message="Embedding 32 of 100 chunks.",
            current=32,
            total=100,
            force=True,
        )
    assert skipped is None
    assert "Skipped stage write" in caplog.text

    seeded_db.expire_all()
    after = seeded_db.get(SourceMaterialVersion, version.id)
    assert after is not None
    assert after.stage == "received"
    assert after.stage_message == RESTART_MESSAGE
    assert after.stage_current is None
    assert after.stage_seq == kept_seq

    status = client.get(f"/api/v1/admin/material-versions/{version.id}", headers=admin_headers)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["stage"] == "received"
    assert body["stage_message"] == RESTART_MESSAGE
    assert body["stage_seq"] == kept_seq

    url = f"/api/v1/progress/versions/{version.id}"
    with (
        open_progress_server() as base,
        httpx.Client(base_url=base, timeout=5) as http,
        http.stream("GET", url, headers=admin_headers) as response,
    ):
        assert response.status_code == 200
        raw = bytearray()
        for chunk in response.iter_bytes():
            raw.extend(chunk)
            if b"event: progress" in raw:
                break
    payloads = _progress_payloads(raw.decode())
    assert len(payloads) == 1
    assert payloads[0]["stage"] == "received"
    assert payloads[0]["message"] == RESTART_MESSAGE
    assert payloads[0]["seq"] == kept_seq
    get_settings.cache_clear()


@pytest.mark.regression
async def test_subscribe_audits_are_one_row_per_actor(
    seeded_db: Session,
    async_db_session: AsyncSession,
) -> None:
    """A second actor in the same window gets a row. The same actor does not."""
    admin = seeded_db.scalar(select(User).where(User.email == get_settings().demo_admin_email))
    assert admin is not None
    other = User(
        institution_id=admin.institution_id,
        email=f"other-{uuid4().hex[:8]}@demo.school",
        full_name="Other Admin",
        password_hash="not-used",
        status=UserStatus.ACTIVE,
    )
    seeded_db.add(other)
    seeded_db.commit()
    detail = f"version:{uuid4()}"
    payload = {"detail": detail}

    async def _record(actor_id: UUID) -> object:
        return await record_event_within_window(
            async_db_session,
            window=SUBSCRIBE_AUDIT_WINDOW,
            institution_id=admin.institution_id,
            actor_user_id=actor_id,
            event_type="data.scoped_read",
            entity_type="progress.version",
            match_payload={"detail": detail},
            payload=payload,
        )

    first = await _record(admin.id)
    second = await _record(other.id)
    third = await _record(admin.id)
    assert first is not None
    assert second is not None
    assert third is None


def _failed_version_for_audit(
    session: Session, subtopic_id: object, tmp_path: Path
) -> SourceMaterialVersion:
    version, job = _store_version(
        session,
        subtopic_id=subtopic_id,
        stage="failed",
        stage_seq=2,
        stage_message="The upload could not be processed.",
        status=IngestJobStatus.FAILED,
    )
    version.lifecycle_status = SourceMaterialVersionStatus.FAILED
    job.status = IngestJobStatus.FAILED
    session.commit()
    del tmp_path
    return version


@pytest.mark.regression
def test_concurrent_subscribes_write_one_audit_row(
    seeded_db: Session,
    seeded_subtopic_id: object,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    version = _failed_version_for_audit(seeded_db, seeded_subtopic_id, tmp_path)
    url = f"/api/v1/progress/versions/{version.id}"
    headers = {**admin_headers, "Last-Event-ID": f"version:{version.id}:stale"}
    errors: list[BaseException] = []
    ready = threading.Barrier(6)

    def once(base: str) -> None:
        try:
            ready.wait(timeout=10)
            with (
                httpx.Client(base_url=base, timeout=15) as http,
                http.stream("GET", url, headers=headers) as response,
            ):
                assert response.status_code == 200
                response.read()
        except BaseException as exc:  # noqa: BLE001 — collected for the assertion
            errors.append(exc)

    with open_progress_server() as base:
        threads = [threading.Thread(target=once, args=(base,)) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    seeded_db.expire_all()
    rows = seeded_db.scalars(
        select(AuditEvent).where(
            AuditEvent.event_type == "data.scoped_read",
            AuditEvent.entity_type == "progress.version",
        )
    ).all()
    matched = [row for row in rows if row.payload.get("detail") == f"version:{version.id}"]
    assert len(matched) == 1
    get_settings.cache_clear()


@pytest.mark.regression
def test_subscribe_audits_again_after_the_window(
    client: TestClient,
    seeded_db: Session,
    seeded_subtopic_id: object,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    version = _failed_version_for_audit(seeded_db, seeded_subtopic_id, tmp_path)
    url = f"/api/v1/progress/versions/{version.id}"
    headers = {**admin_headers, "Last-Event-ID": f"version:{version.id}:stale"}
    with client.stream("GET", url, headers=headers) as response:
        assert response.status_code == 200
        b"".join(response.iter_bytes())
    admin = seeded_db.scalar(select(User).where(User.email == get_settings().demo_admin_email))
    assert admin is not None
    seeded_db.expire_all()
    row = seeded_db.scalar(
        select(AuditEvent).where(
            AuditEvent.entity_type == "progress.version",
            AuditEvent.actor_user_id == admin.id,
        )
    )
    assert row is not None
    row.dedupe_key = progress_subscribe_dedupe_key(
        institution_id=admin.institution_id,
        actor_user_id=admin.id,
        entity_type="progress.version",
        detail=f"version:{version.id}",
        now=datetime.now(UTC) - SUBSCRIBE_AUDIT_WINDOW,
    )
    row.created_at = datetime.now(UTC) - SUBSCRIBE_AUDIT_WINDOW - timedelta(seconds=1)
    seeded_db.commit()
    with client.stream("GET", url, headers=headers) as response:
        assert response.status_code == 200
        b"".join(response.iter_bytes())
    seeded_db.expire_all()
    rows = seeded_db.scalars(
        select(AuditEvent).where(
            AuditEvent.event_type == "data.scoped_read",
            AuditEvent.entity_type == "progress.version",
            AuditEvent.actor_user_id == admin.id,
        )
    ).all()
    matched = [item for item in rows if item.payload.get("detail") == f"version:{version.id}"]
    assert len(matched) == 2
    get_settings.cache_clear()


_BLOCKING_WORKER = """
import os
import time

from education_platform.workers.runner import poll_once

def block(_path: str) -> list[object]:
    with open(os.environ["WORKER_READY_FILE"], "w", encoding="utf-8") as handle:
        handle.write("ready")
    time.sleep(120)
    return []

poll_once(parse_pdf=block)
"""


@pytest.mark.regression
def test_sigkill_reclaim_resets_status_and_stream(
    client: TestClient,
    seeded_db: Session,
    seeded_subtopic_id: object,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    version, job = _store_version(seeded_db, subtopic_id=seeded_subtopic_id)
    ready = tmp_path / "worker-ready"
    env = os.environ.copy()
    env["WORKER_READY_FILE"] = str(ready)
    worker = subprocess.Popen(
        [sys.executable, "-c", _BLOCKING_WORKER],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline and not ready.exists():
            if worker.poll() is not None:
                stderr = worker.stderr.read().decode() if worker.stderr is not None else ""
                raise AssertionError(f"worker exited early: {stderr}")
            time.sleep(0.05)
        assert ready.exists()
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait(timeout=5)
    finally:
        if worker.poll() is None:
            os.kill(worker.pid, signal.SIGKILL)
            worker.wait(timeout=5)

    seeded_db.expire_all()
    running = seeded_db.get(IngestJob, job.id)
    stored = seeded_db.get(SourceMaterialVersion, version.id)
    assert running is not None and stored is not None
    assert running.status is IngestJobStatus.RUNNING
    assert stored.stage == "parsing"
    running.locked_until = datetime.now(UTC) - timedelta(minutes=5)
    seeded_db.commit()

    seen = bytearray()

    def read_stream(base: str) -> None:
        with (
            httpx.Client(base_url=base, timeout=20) as http,
            http.stream(
                "GET",
                f"/api/v1/progress/versions/{version.id}",
                headers=admin_headers,
            ) as response,
        ):
            assert response.status_code == 200
            for chunk in response.iter_bytes():
                seen.extend(chunk)
                if b'"stage":"done"' in seen:
                    return

    with open_progress_server() as base:
        reader = threading.Thread(target=read_stream, args=(base,))
        reader.start()
        try:
            stream_deadline = time.monotonic() + 5
            while time.monotonic() < stream_deadline and RESTART_MESSAGE.encode() not in seen:
                time.sleep(0.05)
            assert RESTART_MESSAGE.encode() in seen

            status = client.get(
                f"/api/v1/admin/material-versions/{version.id}", headers=admin_headers
            )
            assert status.status_code == 200, status.text
            assert status.json()["stage_message"] == RESTART_MESSAGE
            seeded_db.expire_all()
            before_reclaim = seeded_db.get(SourceMaterialVersion, version.id)
            assert before_reclaim is not None
            assert before_reclaim.stage == "parsing"

            monkeypatch.setattr(
                "education_platform.workers.ingest.embed_texts",
                lambda texts: [[0.02] * 384 for _ in texts],
            )
            poll_once(parse_pdf=lambda _path: _chunks(2))
        finally:
            reader.join(timeout=20)
    assert not reader.is_alive()
    text = seen.decode()
    assert text.index(RESTART_MESSAGE) < text.index('"stage":"done"')
    after = client.get(f"/api/v1/admin/material-versions/{version.id}", headers=admin_headers)
    assert after.status_code == 200, after.text
    assert after.json()["stage"] == "done"
    get_settings.cache_clear()


def test_live_claim_reads_the_bound_lease_token() -> None:
    fallback = StageClaim(attempts=2)
    assert _live_claim(fallback) == fallback
    first = datetime(2030, 1, 1, tzinfo=UTC)
    later = datetime(2030, 1, 1, 0, 0, 30, tzinfo=UTC)
    lease = OwnedLease(model=IngestJob, job_id=uuid4(), attempts=2, locked_until=first)
    with bind_lease(lease):
        assert _live_claim(fallback) == StageClaim(attempts=2, locked_until=first)
        lease.locked_until = later
        assert _live_claim(fallback) == StageClaim(attempts=2, locked_until=later)


@pytest.mark.regression
def test_same_attempt_reclaim_drops_the_previous_lease_token(
    seeded_db: Session,
    seeded_subtopic_id: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A refund plus a new claim can reuse the attempt count with a new token."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    first_until = datetime.now(UTC) + timedelta(minutes=5)
    version, job = _store_version(
        seeded_db,
        subtopic_id=seeded_subtopic_id,
        stage="embedding",
        stage_seq=4,
        stage_current=16,
        stage_total=100,
        stage_message="Embedding 16 of 100 chunks.",
        attempts=1,
        status=IngestJobStatus.RUNNING,
        locked_until=first_until,
    )
    released = release_lease(
        OwnedLease(
            model=IngestJob,
            job_id=job.id,
            attempts=1,
            locked_until=first_until,
        )
    )
    assert released is True
    seeded_db.expire_all()
    refunded = seeded_db.get(IngestJob, job.id)
    assert refunded is not None
    assert refunded.status == IngestJobStatus.QUEUED
    assert refunded.attempts == 0
    refunded.attempts = 1
    refunded.status = IngestJobStatus.RUNNING
    refunded.locked_until = first_until + timedelta(seconds=30)
    seeded_db.commit()
    seeded_db.expire_all()
    stored = seeded_db.get(SourceMaterialVersion, version.id)
    running = seeded_db.get(IngestJob, job.id)
    assert stored is not None and running is not None
    assert stored.stage_message == RESTART_MESSAGE
    kept_seq = stored.stage_seq

    skipped = persist_upload_stage(
        stored,
        running,
        claim=StageClaim(attempts=1, locked_until=first_until),
        stage="embedding",
        message="Embedding 40 of 100 chunks.",
        current=40,
        total=100,
        force=True,
    )
    assert skipped is None
    seeded_db.expire_all()
    after = seeded_db.get(SourceMaterialVersion, version.id)
    assert after is not None
    assert after.stage_message == RESTART_MESSAGE
    assert after.stage_seq == kept_seq
    get_settings.cache_clear()


@pytest.mark.regression
def test_indexing_run_stream_emits_upload_progress(
    client: TestClient,
    seeded_db: Session,
    seeded_subtopic_id: object,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Stage wakes on the intake version reach the generation-run SSE."""
    del client
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    now = datetime.now(UTC)
    version, job = _store_version(
        seeded_db,
        subtopic_id=seeded_subtopic_id,
        stage="embedding",
        stage_seq=2,
        stage_current=3,
        stage_total=10,
        stage_message="Embedding 3 of 10 chunks.",
        attempts=1,
        status=IngestJobStatus.RUNNING,
        locked_until=now + timedelta(minutes=5),
    )
    settings = get_settings()
    topic = seeded_db.get(Subtopic, seeded_subtopic_id)
    admin = seeded_db.scalar(select(User).where(User.email == settings.demo_admin_email))
    assert topic is not None and admin is not None
    run = ContentGenerationRun(
        topic_id=topic.topic_id,
        submitted_by_user_id=admin.id,
        title="Indexing intake",
        phase=RunPhase.INDEXING,
        target_item_count=60,
        intake_source_material_version_id=version.id,
    )
    seeded_db.add(run)
    seeded_db.commit()

    seen = bytearray()

    def read_stream(base: str) -> None:
        url = f"/api/v1/progress/runs/{run.id}"
        with (
            httpx.Client(base_url=base, timeout=5) as http,
            http.stream("GET", url, headers=admin_headers) as response,
        ):
            assert response.status_code == 200
            for chunk in response.iter_bytes():
                seen.extend(chunk)
                if seen.count(b"event: progress") >= 2:
                    return

    with open_progress_server() as base:
        reader = threading.Thread(target=read_stream, args=(base,))
        reader.start()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and b"event: progress" not in seen:
                time.sleep(0.05)
            assert b"event: progress" in seen
            seeded_db.expire_all()
            current = seeded_db.get(SourceMaterialVersion, version.id)
            owned = seeded_db.get(IngestJob, job.id)
            assert current is not None and owned is not None
            written = persist_upload_stage(
                current,
                owned,
                claim=StageClaim(attempts=1, locked_until=owned.locked_until),
                stage="embedding",
                message="Embedding 8 of 10 chunks.",
                current=8,
                total=10,
                force=True,
            )
            assert written == 3
        finally:
            reader.join(timeout=20)
    assert not reader.is_alive()
    payloads = _progress_payloads(seen.decode())
    assert [payload["seq"] for payload in payloads] == [2, 3]
    assert payloads[0]["stage"] == "embedding"
    assert payloads[0]["current"] == 3
    assert payloads[1]["current"] == 8
    assert payloads[0]["subject_id"] == str(version.id)
    get_settings.cache_clear()
