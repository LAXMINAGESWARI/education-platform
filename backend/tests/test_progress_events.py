"""Upload stage progress on the existing job SSE."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.session import get_session_factory
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.authorization.scope import Scope
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
    heal_expired_ingest_stage,
    persist_upload_stage,
    reset_stage_on_requeue,
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
from education_platform.modules.rag.models import IngestJob, IngestJobStatus
from education_platform.modules.rag.schemas import MaterialVersionStatusOut
from education_platform.workers.ingest import process_ingest_job_sync
from education_platform.workers.lease import Claimed, claim
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
    version, job = _store_version(seeded_db, subtopic_id=seeded_subtopic_id)
    first = persist_upload_stage(
        version,
        job,
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
        stage="embedding",
        message="Embedding 1 of 100 chunks.",
        current=1,
        total=100,
        now=_NOW + timedelta(milliseconds=200),
    )
    written = persist_upload_stage(
        version,
        job,
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
    assert embedding[0][1:] == (0, 4, embedding[0][3])
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


@pytest.mark.asyncio
async def test_sigterm_requeue_shape_resets_on_read_and_claim(
    seeded_db: Session, seeded_subtopic_id: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A requeue that kept its attempt still clears the stage on read and claim.

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
    factory = get_session_factory()
    async with factory() as session:
        await heal_expired_ingest_stage(session, version.id)
        await session.commit()
    seeded_db.expire_all()
    healed = seeded_db.get(SourceMaterialVersion, version.id)
    assert healed is not None
    assert healed.stage == "received"
    assert healed.stage_message == RESTART_MESSAGE
    assert healed.stage_current is None
    assert healed.stage_seq == 6

    async with factory() as session:
        await heal_expired_ingest_stage(session, version.id)
        await session.commit()
    seeded_db.expire_all()
    again = seeded_db.get(SourceMaterialVersion, version.id)
    assert again is not None
    assert again.stage_seq == 6
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


@pytest.mark.asyncio
async def test_refunded_sigterm_stays_until_release_resets(
    seeded_db: Session, seeded_subtopic_id: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
    factory = get_session_factory()
    async with factory() as session:
        await heal_expired_ingest_stage(session, version.id)
        await session.commit()
    seeded_db.expire_all()
    healed = seeded_db.get(SourceMaterialVersion, version.id)
    assert healed is not None
    assert healed.stage == "embedding"
    assert healed.stage_seq == 5

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
