"""SIGTERM must stop the in-flight job, and a lost lease must not complete it."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

import education_platform.workers.runner as runner
from education_platform.db.session import sync_session
from education_platform.modules.generation.models import GenerationJob
from education_platform.modules.generation.types import GenerationJobStatus
from education_platform.workers.lease import (
    Claimed,
    OwnedLease,
    bind_lease,
    claim,
    commit_owned,
    prepare_leased_writes,
)
from education_platform.workers.shutdown import WorkerShutdown
from test_worker_lease import _generation_job

_SLEEP_SECONDS = "30"
_GRACE_SECONDS = "1"


def _child() -> None:
    role = os.environ["WORKER_REGRESSION_ROLE"]

    def process(job_id: UUID, **_kwargs: object) -> None:
        session = sync_session()
        try:
            if role == "sleeper":
                current = session.get(GenerationJob, job_id)
                if current is None:
                    return
                current.error = "sleeping"
                commit_owned(session)
                time.sleep(float(os.environ["WORKER_REGRESSION_SLEEP_SECONDS"]))
            current = session.get(GenerationJob, job_id)
            if current is None:
                return
            current.status = GenerationJobStatus.SUCCEEDED
            current.error = role
            commit_owned(session)
        finally:
            session.close()

    runner.process_generation_job_sync = process
    if role == "sleeper":
        runner.run_forever(poll_interval_seconds=0.05)
        return
    state = WorkerShutdown()
    state.install_signals()

    def poll() -> UUID | None:
        claimed = runner.poll_once(shutdown=state)
        if claimed is not None:
            state.stop.set()
        return claimed

    runner.run_forever(shutdown=state, poll=poll, poll_interval_seconds=0.05)


def _popen(role: str) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["WORKER_REGRESSION_ROLE"] = role
    env["WORKER_REGRESSION_SLEEP_SECONDS"] = _SLEEP_SECONDS
    env["WORKER_SHUTDOWN_GRACE_SECONDS"] = _GRACE_SECONDS
    env["PYTHONUNBUFFERED"] = "1"
    backend = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(
        [backend, str(Path(backend) / "src"), env.get("PYTHONPATH", "")]
    )
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stderr(proc: subprocess.Popen[str] | None) -> str:
    if proc is None:
        return ""
    if proc.poll() is None:
        proc.kill()
    try:
        _out, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        _out, err = proc.communicate()
    return err


def _wait_for(
    session: Session,
    job_id: UUID,
    predicate: object,
    *,
    timeout: float,
) -> GenerationJob:
    deadline = time.monotonic() + timeout
    row: GenerationJob | None = None
    while time.monotonic() < deadline:
        session.expire_all()
        row = session.get(GenerationJob, job_id)
        if row is not None and predicate(row):  # type: ignore[operator]
            return row
        time.sleep(0.05)
    session.expire_all()
    row = session.get(GenerationJob, job_id)
    raise AssertionError(f"timed out waiting for {job_id}; row={row!r}")


@pytest.mark.regression
def test_sigterm_stops_the_worker_within_grace(seeded_db: Session) -> None:
    _run, job = _generation_job(
        seeded_db,
        status=GenerationJobStatus.QUEUED,
        attempts=0,
        locked_until=None,
    )
    proc = _popen("sleeper")
    elapsed = 0.0
    try:
        _wait_for(seeded_db, job.id, lambda row: row.error == "sleeping", timeout=120)
        time.sleep(0.3)
        started = time.monotonic()
        os.kill(proc.pid, signal.SIGTERM)
        assert proc.wait(timeout=15) == 0
        elapsed = time.monotonic() - started
    finally:
        err = _stderr(proc)
    assert elapsed < 10, f"SIGTERM-to-exit took {elapsed:.3f}s\n{err}"
    print(f"SIGTERM_TO_EXIT_SECONDS={elapsed:.3f}")
    seeded_db.expire_all()
    row = seeded_db.get(GenerationJob, job.id)
    assert row is not None
    assert row.status is GenerationJobStatus.QUEUED
    assert row.attempts == 0
    assert row.locked_until is None
    assert row.error != "sleeper"


@pytest.mark.regression
def test_two_workers_record_one_success_from_the_reclaiming_worker(seeded_db: Session) -> None:
    _run, job = _generation_job(
        seeded_db,
        status=GenerationJobStatus.QUEUED,
        attempts=0,
        locked_until=None,
    )
    sleeper = _popen("sleeper")
    finisher: subprocess.Popen[str] | None = None
    try:
        _wait_for(seeded_db, job.id, lambda row: row.error == "sleeping", timeout=120)
        time.sleep(0.3)
        os.kill(sleeper.pid, signal.SIGTERM)
        finisher = _popen("finisher")
        assert sleeper.wait(timeout=15) == 0
        assert finisher.wait(timeout=90) == 0
    finally:
        sleeper_err = _stderr(sleeper)
        finisher_err = _stderr(finisher)
    seeded_db.expire_all()
    row = seeded_db.get(GenerationJob, job.id)
    assert row is not None, f"{sleeper_err}\n{finisher_err}"
    assert row.status is GenerationJobStatus.SUCCEEDED, f"{sleeper_err}\n{finisher_err}"
    assert row.error == "finisher"
    assert row.attempts == 1


@pytest.mark.regression
def test_completion_after_a_lost_lease_is_a_noop(seeded_db: Session) -> None:
    _run, job = _generation_job(
        seeded_db,
        status=GenerationJobStatus.QUEUED,
        attempts=0,
        locked_until=None,
    )
    claimed = claim(seeded_db, GenerationJob)
    assert isinstance(claimed, Claimed)
    lease = OwnedLease.from_claimed(GenerationJob, claimed)
    stored = seeded_db.get(GenerationJob, job.id)
    assert stored is not None
    stored.attempts = claimed.attempts + 1
    stored.locked_until = datetime.now(UTC) + timedelta(hours=1)
    seeded_db.commit()
    seeded_db.expire_all()
    owned = seeded_db.get(GenerationJob, job.id)
    assert owned is not None
    snapshot = (owned.status, owned.attempts, owned.locked_until)

    session = sync_session()
    try:
        with bind_lease(lease):
            prepare_leased_writes(session)
            current = session.get(GenerationJob, job.id)
            assert current is not None
            current.status = GenerationJobStatus.SUCCEEDED
            current.error = "stale-owner"
            assert commit_owned(session) is False
    finally:
        session.close()

    seeded_db.expire_all()
    after = seeded_db.get(GenerationJob, job.id)
    assert after is not None
    assert (after.status, after.attempts, after.locked_until) == snapshot
    assert after.status is GenerationJobStatus.RUNNING
    assert after.error != "stale-owner"


@pytest.mark.regression
def test_crash_then_expiry_then_reclaim_completes_once(
    seeded_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run, job = _generation_job(
        seeded_db,
        status=GenerationJobStatus.QUEUED,
        attempts=0,
        locked_until=None,
    )
    claimed = claim(seeded_db, GenerationJob)
    assert isinstance(claimed, Claimed)
    assert claimed.attempts == 1
    abandoned = seeded_db.get(GenerationJob, job.id)
    assert abandoned is not None
    abandoned.locked_until = datetime.now(UTC) - timedelta(minutes=5)
    seeded_db.commit()

    def process(job_id: UUID, **_kwargs: object) -> None:
        session = sync_session()
        try:
            current = session.get(GenerationJob, job_id)
            assert current is not None
            current.status = GenerationJobStatus.SUCCEEDED
            current.error = "reclaimed"
            assert commit_owned(session) is True
        finally:
            session.close()

    monkeypatch.setattr(
        "education_platform.workers.runner.process_generation_job_sync",
        process,
    )
    assert runner.poll_once(session=seeded_db) == job.id
    seeded_db.expire_all()
    after = seeded_db.get(GenerationJob, job.id)
    assert after is not None
    assert after.status is GenerationJobStatus.SUCCEEDED
    assert after.attempts == 2
    assert after.error == "reclaimed"


if __name__ == "__main__":
    _child()
