"""Real job handlers must discard a terminal write after the lease is stolen."""

from __future__ import annotations

import ast
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.session import sync_session
from education_platform.modules.academics.models import Subject, Subtopic
from education_platform.modules.assessments.models import (
    FeedbackSummaryJob,
    FeedbackSummaryJobStatus,
    FeedbackSummaryOrigin,
    SubjectFeedbackSummary,
)
from education_platform.modules.auth.models import StudentProfile, User
from education_platform.modules.generation.adk import AdkRunRequest, AdkRunResult
from education_platform.modules.generation.models import (
    ContentGenerationRun,
    CurriculumGenerationJob,
    GenerationJob,
)
from education_platform.modules.generation.types import (
    GenerationJobKind,
    GenerationJobStatus,
    RunPhase,
)
from education_platform.modules.materials.models import (
    SourceChunk,
    SourceMaterial,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
)
from education_platform.modules.rag.models import IngestJob, IngestJobStatus
from education_platform.workers.runner import poll_once

_SEEDED_SLUG = "rectangles_squares_properties"
_STOLEN_UNTIL = datetime(2030, 1, 2, tzinfo=UTC)
_JOB_MODELS = frozenset(
    {"FeedbackSummaryJob", "IngestJob", "GenerationJob", "CurriculumGenerationJob"}
)
_JOB_TABLES = frozenset(
    {
        "feedback_summary_jobs",
        "ingest_jobs",
        "generation_jobs",
        "curriculum_generation_jobs",
    }
)
_HANDLER_MODULES = (
    "education_platform.workers.feedback_summary",
    "education_platform.workers.ingest",
    "education_platform.modules.generation.worker",
    "education_platform.modules.generation.curriculum_worker",
)

_PLAIN_COMMIT = "def run(session):\n    session.commit()\n"
_STATUS_UPDATE = (
    "def run(session):\n    session.execute(update(GenerationJob).values(status='succeeded'))\n"
)
_STATUS_SQL = (
    "def run(session):\n"
    "    session.execute(text(\"UPDATE generation_jobs SET status = 'succeeded'\"))\n"
)


def _steal(model: type[object], job_id: UUID) -> None:
    """Bump the lease from another session, the way a reclaiming worker would."""
    other = sync_session()
    try:
        row = other.get(model, job_id)
        assert row is not None
        row.attempts = int(row.attempts) + 1
        row.locked_until = _STOLEN_UNTIL
        other.commit()
    finally:
        other.close()


def _admin(session: Session) -> User:
    user = session.scalar(select(User).where(User.email == get_settings().demo_admin_email))
    assert user is not None
    return user


def _subtopic(session: Session) -> Subtopic:
    subtopic = session.scalar(select(Subtopic).where(Subtopic.slug == _SEEDED_SLUG))
    assert subtopic is not None
    return subtopic


def _chunk(version_id: UUID) -> SourceChunk:
    text = "Squares have four equal sides and four right angles. " * 12
    return SourceChunk(
        source_material_version_id=version_id,
        ordinal=1,
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        page_number=1,
        section_heading="Properties of squares",
        token_count=len(text.split()),
    )


def _queue_feedback(session: Session) -> UUID:
    subject = session.scalar(select(Subject).limit(1))
    assert subject is not None
    student = StudentProfile(
        institution_id=subject.institution_id,
        student_identifier=f"fence-{uuid4().hex[:8]}",
        full_name="Fence Student",
    )
    session.add(student)
    session.flush()
    facts_hash = "ab" * 32
    session.add(
        SubjectFeedbackSummary(
            student_id=student.id,
            subject_id=subject.id,
            facts_hash=None,
            requested_hash=facts_hash,
            summary="original template",
            summary_origin=FeedbackSummaryOrigin.TEMPLATE,
        )
    )
    job = FeedbackSummaryJob(
        student_id=student.id,
        subject_id=subject.id,
        facts_hash=facts_hash,
        prompt="Summarise these scores.",
        template_summary="original template",
        status=FeedbackSummaryJobStatus.QUEUED,
    )
    session.add(job)
    session.commit()
    return job.id


def _queue_ingest(session: Session) -> UUID:
    subtopic = _subtopic(session)
    material = SourceMaterial(
        subtopic_id=subtopic.id,
        title="Fence ingest",
        slug=f"fence-ingest-{uuid4().hex[:8]}",
    )
    session.add(material)
    session.flush()
    version = SourceMaterialVersion(
        source_material_id=material.id,
        version_number=1,
        title="Fence ingest",
        content_format="pdf",
        blob_object_key="fence/lesson.pdf",
        lifecycle_status=SourceMaterialVersionStatus.PROCESSING,
    )
    session.add(version)
    session.flush()
    job = IngestJob(
        source_material_version_id=version.id,
        status=IngestJobStatus.QUEUED,
    )
    session.add(job)
    session.commit()
    return job.id


def _queue_generation(session: Session) -> UUID:
    subtopic = _subtopic(session)
    material = SourceMaterial(
        subtopic_id=subtopic.id,
        title="Fence outline",
        slug=f"fence-outline-{uuid4().hex[:8]}",
    )
    session.add(material)
    session.flush()
    version = SourceMaterialVersion(
        source_material_id=material.id,
        version_number=1,
        title="Fence outline",
        lifecycle_status=SourceMaterialVersionStatus.READY,
    )
    session.add(version)
    session.flush()
    session.add(_chunk(version.id))
    run = ContentGenerationRun(
        topic_id=subtopic.topic_id,
        submitted_by_user_id=_admin(session).id,
        title="Fence",
        phase=RunPhase.GENERATING,
        target_item_count=60,
        intake_source_material_version_id=version.id,
    )
    session.add(run)
    session.flush()
    job = GenerationJob(
        run_id=run.id,
        kind=GenerationJobKind.OUTLINE,
        status=GenerationJobStatus.QUEUED,
    )
    session.add(job)
    session.commit()
    return job.id


def _queue_curriculum(session: Session) -> UUID:
    subtopic = _subtopic(session)
    material = session.scalar(
        select(SourceMaterial).where(
            SourceMaterial.subtopic_id == subtopic.id,
            SourceMaterial.slug == "lesson",
        )
    )
    assert material is not None
    next_version = 50
    version = SourceMaterialVersion(
        source_material_id=material.id,
        version_number=next_version,
        title="Fence curriculum",
        lifecycle_status=SourceMaterialVersionStatus.READY,
    )
    session.add(version)
    session.flush()
    session.add(_chunk(version.id))
    job = CurriculumGenerationJob(
        subtopic_id=subtopic.id,
        status=GenerationJobStatus.QUEUED,
        created_by=_admin(session).id,
        round_count=0,
    )
    session.add(job)
    session.commit()
    return job.id


def _assert_unchanged(session: Session, model: type[object], job_id: UUID) -> None:
    session.expire_all()
    row = session.get(model, job_id)
    assert row is not None
    status = getattr(row.status, "value", row.status)
    assert status == "running", row.status
    assert row.attempts == 2
    assert row.locked_until is not None
    assert row.locked_until == _STOLEN_UNTIL
    assert row.error is None
    if isinstance(row, CurriculumGenerationJob):
        assert row.reviewer_notes is None
        assert row.round_count == 0
        assert row.source_material_version_id is None
        assert row.quiz_version_id is None


@pytest.mark.regression
@pytest.mark.parametrize(
    "handler",
    ["feedback_summary", "ingest", "generation", "curriculum"],
)
def test_real_handler_drops_a_terminal_write_after_the_lease_is_stolen(
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    handler: str,
) -> None:
    """Steal the lease inside the handler's external call, then require a no-op commit."""
    if handler == "feedback_summary":
        job_id = _queue_feedback(seeded_db)

        def _write_summary(_prompt: str) -> str:
            _steal(FeedbackSummaryJob, job_id)
            return "model prose that must not land"

        monkeypatch.setattr(
            "education_platform.workers.feedback_summary.feedback_summary.write_summary",
            _write_summary,
        )
        assert poll_once(session=seeded_db) == job_id
        _assert_unchanged(seeded_db, FeedbackSummaryJob, job_id)
        stored = seeded_db.scalar(
            select(SubjectFeedbackSummary).where(SubjectFeedbackSummary.requested_hash == "ab" * 32)
        )
        assert stored is not None
        assert stored.summary == "original template"
        return
    if handler == "ingest":
        job_id = _queue_ingest(seeded_db)

        def _parse(_path: object) -> list[object]:
            _steal(IngestJob, job_id)
            return []

        assert poll_once(session=seeded_db, parse_pdf=_parse) == job_id
        _assert_unchanged(seeded_db, IngestJob, job_id)
        return
    if handler == "generation":
        job_id = _queue_generation(seeded_db)

        def _writer(_clusters: object) -> object:
            _steal(GenerationJob, job_id)
            raise RuntimeError("lease stolen during outline")

        assert poll_once(session=seeded_db, write_outline=_writer) == job_id
        _assert_unchanged(seeded_db, GenerationJob, job_id)
        return
    if handler == "curriculum":
        job_id = _queue_curriculum(seeded_db)

        class _Runner:
            def generate(self, request: AdkRunRequest) -> AdkRunResult:
                del request
                _steal(CurriculumGenerationJob, job_id)
                raise RuntimeError("lease stolen during curriculum")

        assert poll_once(session=seeded_db, curriculum_runner=_Runner()) == job_id
        _assert_unchanged(seeded_db, CurriculumGenerationJob, job_id)
        return
    raise AssertionError(handler)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _update_target(node: ast.AST) -> str | None:
    current: ast.AST | None = node
    while isinstance(current, ast.Call):
        func = current.func
        if isinstance(func, ast.Name) and func.id == "update":
            if not current.args:
                return None
            return _call_name(current.args[0])
        if isinstance(func, ast.Attribute):
            current = func.value
            continue
        return None
    return None


def _values_set_status(node: ast.Call) -> bool:
    return any(keyword.arg == "status" for keyword in node.keywords)


def _sql_updates_job_status(value: str) -> bool:
    lowered = value.lower()
    return (
        "update" in lowered
        and "status" in lowered
        and any(table in lowered for table in _JOB_TABLES)
    )


def handler_write_violations(source: str, filename: str) -> list[str]:
    """session.commit() or a job-status UPDATE outside commit_owned."""
    tree = ast.parse(source, filename=filename)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "commit":
                found.append(f"{filename}:{node.lineno}: session.commit()")
            if node.func.attr == "values" and _values_set_status(node):
                target = _update_target(node)
                if target in _JOB_MODELS:
                    found.append(f"{filename}:{node.lineno}: UPDATE {target}.status")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _sql_updates_job_status(node.value)
        ):
            found.append(f"{filename}:{node.lineno}: SQL updates a job status")
    return found


@pytest.mark.regression
def test_handler_modules_do_not_commit_or_update_job_status() -> None:
    violations: list[str] = []
    for module_name in _HANDLER_MODULES:
        module = __import__(module_name, fromlist=["__file__"])
        path = Path(module.__file__ or "")
        violations.extend(handler_write_violations(path.read_text(), str(path)))
    assert violations == []
    assert handler_write_violations(_PLAIN_COMMIT, "plain_commit.py")
    assert handler_write_violations(_STATUS_UPDATE, "status_update.py")
    assert handler_write_violations(_STATUS_SQL, "status_sql.py")
