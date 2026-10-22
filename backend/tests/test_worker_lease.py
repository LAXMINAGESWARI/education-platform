"""Workers reclaim jobs left stuck in running, up to a retry limit."""

from __future__ import annotations

import signal
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.auth.models import User
from education_platform.modules.generation.models import ContentGenerationRun, GenerationJob
from education_platform.modules.generation.types import (
    GenerationJobKind,
    GenerationJobStatus,
    RunPhase,
)
from education_platform.modules.generation.worker import claim_next_generation_job
from education_platform.workers.lease import (
    Claimed,
    Exhausted,
    OwnedLease,
    _extend_lease,
    claim,
    heartbeat,
    release_lease,
)
from education_platform.workers.shutdown import WorkerShutdown
from test_generation import SEEDED_SLUG


def _generation_job(
    session: Session,
    *,
    status: GenerationJobStatus,
    attempts: int,
    locked_until: datetime | None,
    max_attempts: int = 3,
) -> tuple[ContentGenerationRun, GenerationJob]:
    settings = get_settings()
    subtopic = session.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    admin = session.scalar(select(User).where(User.email == settings.demo_admin_email))
    assert admin is not None
    run = ContentGenerationRun(
        topic_id=subtopic.topic_id,
        submitted_by_user_id=admin.id,
        title="Lease",
        phase=RunPhase.GENERATING,
        target_item_count=60,
    )
    session.add(run)
    session.flush()
    job = GenerationJob(
        run_id=run.id,
        kind=GenerationJobKind.LESSON,
        status=status,
        attempts=attempts,
        max_attempts=max_attempts,
        locked_until=locked_until,
    )
    session.add(job)
    session.commit()
    return run, job


def test_stale_running_job_is_reclaimed_and_attempts_increment(seeded_db: Session) -> None:
    _run, job = _generation_job(
        seeded_db,
        status=GenerationJobStatus.RUNNING,
        attempts=1,
        locked_until=datetime.now(UTC) - timedelta(minutes=5),
    )
    claimed = claim_next_generation_job(seeded_db)
    assert claimed == job.id
    seeded_db.expire_all()
    refreshed = seeded_db.get(GenerationJob, job.id)
    assert refreshed is not None
    assert refreshed.status is GenerationJobStatus.RUNNING
    assert refreshed.attempts == 2
    assert refreshed.locked_until is not None
    assert refreshed.locked_until > datetime.now(UTC)


def test_exhausted_job_fails_its_run(seeded_db: Session) -> None:
    run, job = _generation_job(
        seeded_db,
        status=GenerationJobStatus.RUNNING,
        attempts=3,
        locked_until=datetime.now(UTC) - timedelta(minutes=5),
    )
    assert claim_next_generation_job(seeded_db) is None
    seeded_db.expire_all()
    failed_run = seeded_db.get(ContentGenerationRun, run.id)
    assert failed_run is not None
    assert failed_run.phase is RunPhase.FAILED
    assert failed_run.failure_reason == "Job exceeded max attempts (3)"
    refreshed = seeded_db.get(GenerationJob, job.id)
    assert refreshed is not None
    assert refreshed.status is GenerationJobStatus.FAILED
    assert refreshed.attempts == 3


def test_running_job_without_a_lease_is_reclaimed(seeded_db: Session) -> None:
    """Rows already running when the lease column was added have a null lock."""
    _run, job = _generation_job(
        seeded_db,
        status=GenerationJobStatus.RUNNING,
        attempts=0,
        locked_until=None,
    )
    claimed = claim_next_generation_job(seeded_db)
    assert claimed == job.id
    seeded_db.expire_all()
    refreshed = seeded_db.get(GenerationJob, job.id)
    assert refreshed is not None
    assert refreshed.status is GenerationJobStatus.RUNNING
    assert refreshed.attempts == 1
    assert refreshed.locked_until is not None


def test_fresh_lease_is_not_claimed(seeded_db: Session) -> None:
    _run, job = _generation_job(
        seeded_db,
        status=GenerationJobStatus.RUNNING,
        attempts=1,
        locked_until=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert claim(seeded_db, GenerationJob) is None
    assert claim_next_generation_job(seeded_db) is None
    seeded_db.expire_all()
    refreshed = seeded_db.get(GenerationJob, job.id)
    assert refreshed is not None
    assert refreshed.status is GenerationJobStatus.RUNNING
    assert refreshed.attempts == 1


def test_heartbeat_extends_locked_until(
    seeded_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "1")
    get_settings.cache_clear()
    _run, job = _generation_job(
        seeded_db,
        status=GenerationJobStatus.QUEUED,
        attempts=0,
        locked_until=None,
    )
    result = claim(seeded_db, GenerationJob)
    assert isinstance(result, Claimed)
    assert result.job_id == job.id
    seeded_db.expire_all()
    before = seeded_db.get(GenerationJob, job.id)
    assert before is not None and before.locked_until is not None
    started = before.locked_until
    with heartbeat(OwnedLease.from_claimed(GenerationJob, result)):
        time.sleep(1.5)
    seeded_db.expire_all()
    after = seeded_db.get(GenerationJob, job.id)
    assert after is not None and after.locked_until is not None
    assert after.locked_until > started
    get_settings.cache_clear()


def test_release_after_reclaim_leaves_the_new_lease_unchanged(seeded_db: Session) -> None:
    _run, job = _generation_job(
        seeded_db,
        status=GenerationJobStatus.QUEUED,
        attempts=0,
        locked_until=None,
    )
    first = claim(seeded_db, GenerationJob)
    assert isinstance(first, Claimed)
    first_lease = OwnedLease.from_claimed(GenerationJob, first)
    stored = seeded_db.get(GenerationJob, job.id)
    assert stored is not None
    stored.locked_until = datetime.now(UTC) - timedelta(minutes=5)
    seeded_db.commit()

    second = claim(seeded_db, GenerationJob)
    assert isinstance(second, Claimed)
    assert second.attempts == 2
    seeded_db.expire_all()
    owned = seeded_db.get(GenerationJob, job.id)
    assert owned is not None
    snapshot = (owned.status, owned.attempts, owned.locked_until)

    assert release_lease(first_lease) is False
    seeded_db.expire_all()
    after = seeded_db.get(GenerationJob, job.id)
    assert after is not None
    assert (after.status, after.attempts, after.locked_until) == snapshot
    assert after.status is GenerationJobStatus.RUNNING
    assert after.attempts == 2


def test_three_sigterm_cycles_keep_attempts_at_or_under_max(seeded_db: Session) -> None:
    """Three graceful shutdowns must not burn the remaining attempt."""
    _run, job = _generation_job(
        seeded_db,
        status=GenerationJobStatus.QUEUED,
        attempts=2,
        locked_until=None,
        max_attempts=3,
    )
    for _cycle in range(3):
        result = claim(seeded_db, GenerationJob)
        assert isinstance(result, Claimed)
        assert result.attempts == 3
        state = WorkerShutdown(grace_seconds=0, clock=lambda: 0.0)
        lease = OwnedLease.from_claimed(GenerationJob, result)
        assert state.begin_job(lease) is True
        state.note_signal(signal.SIGTERM, None)
        state.watch()
        seeded_db.expire_all()
        refreshed = seeded_db.get(GenerationJob, job.id)
        assert refreshed is not None
        assert refreshed.status is GenerationJobStatus.QUEUED
        assert refreshed.locked_until is None
        assert refreshed.attempts == 2
        assert refreshed.attempts <= refreshed.max_attempts
    again = claim(seeded_db, GenerationJob)
    assert isinstance(again, Claimed)
    assert again.attempts == 3


def test_claim_returns_exhausted_at_the_cap(seeded_db: Session) -> None:
    _run, queued = _generation_job(
        seeded_db,
        status=GenerationJobStatus.QUEUED,
        attempts=3,
        locked_until=None,
        max_attempts=3,
    )
    result = claim(seeded_db, GenerationJob)
    assert isinstance(result, Exhausted)
    assert result.job_id == queued.id
    assert queued.attempts == 3
    assert queued.status is GenerationJobStatus.QUEUED
    seeded_db.rollback()
    seeded_db.expire_all()
    refreshed = seeded_db.get(GenerationJob, queued.id)
    assert refreshed is not None
    assert refreshed.status is GenerationJobStatus.QUEUED
    assert refreshed.attempts == 3
    assert refreshed.locked_until is None


class _QueuedJob:
    def __init__(self, *, attempts: int, max_attempts: int = 3) -> None:
        self.id = uuid4()
        self.status = GenerationJobStatus.QUEUED
        self.attempts = attempts
        self.max_attempts = max_attempts
        self.locked_until: datetime | None = None


class _ClaimSession:
    def __init__(self, job: _QueuedJob) -> None:
        self.job = job
        self.commits = 0

    def scalar(self, _statement: object) -> _QueuedJob:
        return self.job

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, _job: _QueuedJob) -> None:
        return None


class JobRow:
    def __init__(
        self,
        job_id: object,
        *,
        status: GenerationJobStatus,
        attempts: int,
        locked_until: datetime | None,
    ) -> None:
        self.job_id = job_id
        self.status = status
        self.attempts = attempts
        self.locked_until = locked_until


class _ReleaseResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class ReleaseSession:
    """Apply the ownership WHERE from a compiled release UPDATE onto one row."""

    def __init__(self, row: JobRow) -> None:
        self.row = row
        self.commits = 0
        self.executed = 0

    def execute(self, statement: object) -> _ReleaseResult:
        compile_params = getattr(statement, "compile", None)
        assert compile_params is not None
        params = statement.compile().params  # type: ignore[attr-defined]
        matched = (
            params["id_1"] == self.row.job_id
            and params["status_1"] is self.row.status
            and params["attempts_2"] == self.row.attempts
            and params["locked_until_1"] == self.row.locked_until
        )
        self.executed += 1
        if not matched:
            return _ReleaseResult(0)
        self.row.status = params["status"]
        self.row.locked_until = params["locked_until"]
        self.row.attempts -= params["attempts_1"]
        return _ReleaseResult(1)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_claim_enforces_max_attempts_on_queued_rows() -> None:
    job = _QueuedJob(attempts=3, max_attempts=3)
    session = _ClaimSession(job)
    result = claim(session, GenerationJob)  # type: ignore[arg-type]
    assert isinstance(result, Exhausted)
    assert result.job_id == job.id
    assert job.status is GenerationJobStatus.QUEUED
    assert job.attempts == 3
    assert session.commits == 0


def test_release_after_another_worker_reclaimed_is_a_logged_noop(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job_id = uuid4()
    ours = datetime(2026, 10, 20, 1, tzinfo=UTC)
    theirs = datetime(2026, 10, 20, 2, tzinfo=UTC)
    row = JobRow(
        job_id,
        status=GenerationJobStatus.RUNNING,
        attempts=4,
        locked_until=theirs,
    )
    before = (row.status, row.attempts, row.locked_until)
    monkeypatch.setattr(
        "education_platform.workers.lease.sync_session",
        lambda: ReleaseSession(row),
    )
    lease = OwnedLease(
        model=GenerationJob,
        job_id=job_id,
        attempts=3,
        locked_until=ours,
    )
    with caplog.at_level("INFO", logger="education_platform.workers.lease"):
        assert release_lease(lease) is False
    assert (row.status, row.attempts, row.locked_until) == before
    assert "unchanged" in caplog.text
    assert lease.lost is True


def test_graceful_release_refunds_and_repeated_cycles_stay_under_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = uuid4()
    row = JobRow(job_id, status=GenerationJobStatus.QUEUED, attempts=0, locked_until=None)
    monkeypatch.setattr(
        "education_platform.workers.lease.sync_session",
        lambda: ReleaseSession(row),
    )
    max_attempts = 3
    for _cycle in range(10):
        assert row.attempts < max_attempts
        row.attempts += 1
        row.status = GenerationJobStatus.RUNNING
        locked_until = datetime(2026, 10, 20, tzinfo=UTC)
        row.locked_until = locked_until
        lease = OwnedLease(
            model=GenerationJob,
            job_id=job_id,
            attempts=row.attempts,
            locked_until=locked_until,
        )
        assert release_lease(lease) is True
        assert row.status is GenerationJobStatus.QUEUED
        assert row.locked_until is None
        assert row.attempts == 0
    crashes = 0
    while crashes < max_attempts:
        assert row.attempts < max_attempts
        row.attempts += 1
        crashes += 1
    assert row.attempts == max_attempts
    exhausted = _QueuedJob(attempts=row.attempts, max_attempts=max_attempts)
    assert isinstance(claim(_ClaimSession(exhausted), GenerationJob), Exhausted)  # type: ignore[arg-type]


def test_heartbeat_stops_when_the_lease_is_no_longer_owned(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Lost:
        def scalar_one_or_none(self) -> None:
            return None

    class _Session:
        def execute(self, _statement: object) -> _Lost:
            return _Lost()

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr("education_platform.workers.lease.sync_session", _Session)
    lease = OwnedLease(
        model=GenerationJob,
        job_id=uuid4(),
        attempts=1,
        locked_until=datetime(2026, 10, 20, tzinfo=UTC),
    )
    with caplog.at_level("INFO", logger="education_platform.workers.lease"):
        assert _extend_lease(lease) is False
    assert lease.lost is True
    assert "Heartbeat stopped" in caplog.text

    def _unexpected() -> None:
        raise AssertionError("heartbeat must not extend a lost lease")

    monkeypatch.setattr("education_platform.workers.lease.sync_session", _unexpected)
    assert _extend_lease(lease) is False
    assert release_lease(lease) is False


def test_heartbeat_keeps_the_lease_after_a_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Down:
        def execute(self, _statement: object) -> None:
            raise RuntimeError("database unavailable")

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr("education_platform.workers.lease.sync_session", _Down)
    locked_until = datetime(2026, 10, 20, tzinfo=UTC)
    lease = OwnedLease(
        model=GenerationJob,
        job_id=uuid4(),
        attempts=1,
        locked_until=locked_until,
    )
    assert _extend_lease(lease) is True
    assert lease.lost is False
    assert lease.locked_until == locked_until
