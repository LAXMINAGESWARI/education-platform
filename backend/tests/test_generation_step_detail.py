"""Read-only admin views of a generation run's pipeline steps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.test_generation import TINY_PDF

from education_platform.core.config import get_settings
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.assessments.models import CommonMasteryQuiz, QuizVersion
from education_platform.modules.auth.models import RoleName, User, UserRole, UserStatus
from education_platform.modules.auth.security import hash_password
from education_platform.modules.generation.models import (
    ContentGenerationOutlineNode,
    ContentGenerationRun,
    GenerationChangeRequest,
    GenerationJob,
    GenerationRevision,
    ReviewDecisionRow,
    ReviewRoundClosure,
    ReviewRoundParticipant,
    ReviewRoundRow,
)
from education_platform.modules.generation.types import (
    ChangeKind,
    CloseAction,
    GenerationJobKind,
    GenerationJobStatus,
    ReviewStage,
    RevisionOrigin,
    RunPhase,
)
from education_platform.modules.materials.models import SourceChunk, SourceMaterialVersion
from education_platform.modules.rag.chunking import content_hash
from education_platform.modules.rag.models import IngestJob, IngestJobStatus

pytest_plugins = ["tests.test_generation"]


def _submit(client: TestClient, headers: dict[str, str], topic_id: UUID) -> UUID:
    response = client.post(
        f"/api/v1/admin/topics/{topic_id}/generation-runs",
        headers=headers,
        data={"title": "Mensuration source"},
        files={"file": ("topic.pdf", TINY_PDF, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    return UUID(response.json()["run_id"])


def _steps(client: TestClient, headers: dict[str, str], run_id: UUID) -> Any:
    response = client.get(f"/api/v1/admin/generation-runs/{run_id}/steps", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _step(client: TestClient, headers: dict[str, str], run_id: UUID, step: str) -> Any:
    response = client.get(
        f"/api/v1/admin/generation-runs/{run_id}/steps/{step}",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _teacher_headers(client: TestClient, seeded_db: Session) -> dict[str, str]:
    settings = get_settings()
    admin = seeded_db.scalar(select(User).where(User.email == settings.demo_admin_email))
    assert admin is not None
    teacher = User(
        institution_id=admin.institution_id,
        email="step.reader.teacher@demo.school",
        full_name="Step Reader",
        password_hash=hash_password(settings.demo_admin_password),
        status=UserStatus.ACTIVE,
    )
    seeded_db.add(teacher)
    seeded_db.flush()
    seeded_db.add(UserRole(user_id=teacher.id, role=RoleName.TEACHER))
    seeded_db.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": teacher.email,
            "password": settings.demo_admin_password,
            "institution_name": "POC Demo School",
        },
    )
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["access_token"]}


def _mark_published(session: Session, run_id: UUID, topic_id: UUID) -> None:
    run = session.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.intake_source_material_version_id is not None
    version = session.get(SourceMaterialVersion, run.intake_source_material_version_id)
    assert version is not None
    ingest = session.scalar(
        select(IngestJob).where(
            IngestJob.source_material_version_id == version.id,
        )
    )
    assert ingest is not None
    started = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    ingest.status = IngestJobStatus.SUCCEEDED
    ingest.error = None
    ingest.created_at = started
    ingest.updated_at = started + timedelta(minutes=2)

    area = "Area of a polygon\nA polygon is a closed plane figure."
    volume = "Volume of a cube\nA cube has equal edges."
    extra = "Extra notes\nThis chunk is leftover."
    session.add_all(
        [
            SourceChunk(
                source_material_version_id=version.id,
                ordinal=1,
                text=area,
                content_hash=content_hash(area),
                page_number=3,
                section_heading="Area of a polygon",
                token_count=8,
            ),
            SourceChunk(
                source_material_version_id=version.id,
                ordinal=2,
                text=volume,
                content_hash=content_hash(volume),
                page_number=4,
                section_heading="Volume of a cube",
                token_count=6,
            ),
            SourceChunk(
                source_material_version_id=version.id,
                ordinal=3,
                text=extra,
                content_hash=content_hash(extra),
                page_number=4,
                section_heading="Extra notes",
                token_count=4,
            ),
        ]
    )
    parent = ContentGenerationOutlineNode(
        run_id=run.id,
        parent_id=None,
        slug="mensuration",
        title="Mensuration",
        token_mass=20,
        prerequisite_score=Decimal("0.200000"),
        centrality=Decimal("0.800000"),
        weight=Decimal("1.00000000"),
        quota=None,
        proposed_outcomes=["Measure plane and solid figures"],
        source_headings=[],
        sequence=0,
    )
    session.add(parent)
    session.flush()
    area_node = ContentGenerationOutlineNode(
        run_id=run.id,
        parent_id=parent.id,
        slug="area-of-a-polygon",
        title="Area of a polygon",
        token_mass=10,
        prerequisite_score=Decimal("0.300000"),
        centrality=Decimal("0.600000"),
        weight=Decimal("0.50000000"),
        quota=30,
        proposed_outcomes=["Find the area of a polygon"],
        source_headings=["Area of a polygon"],
        sequence=1,
    )
    volume_node = ContentGenerationOutlineNode(
        run_id=run.id,
        parent_id=parent.id,
        slug="volume-of-a-cube",
        title="Volume of a cube",
        token_mass=8,
        prerequisite_score=Decimal("0.400000"),
        centrality=Decimal("0.500000"),
        weight=Decimal("0.50000000"),
        quota=20,
        proposed_outcomes=["Find the volume of a cube"],
        source_headings=["Volume of a cube"],
        sequence=2,
    )
    session.add_all([area_node, volume_node])
    session.flush()

    outline_job = GenerationJob(
        run_id=run.id,
        kind=GenerationJobKind.OUTLINE,
        status=GenerationJobStatus.SUCCEEDED,
        payload={},
        created_at=started + timedelta(minutes=3),
        updated_at=started + timedelta(minutes=5),
    )
    lesson_job = GenerationJob(
        run_id=run.id,
        kind=GenerationJobKind.LESSON,
        status=GenerationJobStatus.SUCCEEDED,
        payload={},
        created_at=started + timedelta(minutes=20),
        updated_at=started + timedelta(minutes=25),
    )
    items_job = GenerationJob(
        run_id=run.id,
        kind=GenerationJobKind.ITEMS,
        status=GenerationJobStatus.SUCCEEDED,
        payload={"item_validation": {"passed": 5, "fixed_on_retry": 2, "dropped": 1}},
        created_at=started + timedelta(minutes=26),
        updated_at=started + timedelta(minutes=40),
    )
    session.add_all([outline_job, lesson_job, items_job])
    session.flush()

    revision = GenerationRevision(
        run_id=run.id,
        stage=ReviewStage.OUTLINE,
        number=1,
        origin=RevisionOrigin.INITIAL_GENERATION,
        created_by_user_id=None,
        created_by_job_id=outline_job.id,
        snapshot_hash="a" * 64,
        snapshot={},
    )
    session.add(revision)
    session.flush()
    opened = started + timedelta(minutes=6)
    sealed = started + timedelta(minutes=18)
    review = ReviewRoundRow(
        run_id=run.id,
        revision_id=revision.id,
        stage=ReviewStage.OUTLINE,
        number=1,
        opened_at=opened,
        due_at=opened + timedelta(days=2),
        sealed_at=sealed,
    )
    session.add(review)
    session.flush()
    session.add(
        ReviewRoundParticipant(
            round_id=review.id,
            reviewer_user_id=run.submitted_by_user_id,
            display_name="Fatima Admin",
            teaching_assignment_ids=[],
        )
    )
    decision = ReviewDecisionRow(
        round_id=review.id,
        reviewer_user_id=run.submitted_by_user_id,
        revision_id=revision.id,
        verdict="approve",
        display_name="Fatima Admin",
    )
    session.add(decision)
    session.flush()
    session.add(
        GenerationChangeRequest(
            decision_id=decision.id,
            revision_id=revision.id,
            author_user_id=run.submitted_by_user_id,
            display_name="Fatima Admin",
            target_kind="outline_node",
            node_key=area_node.id,
            field="title",
            kind=ChangeKind.STRUCTURE,
            comment="Split the polygon section.",
        )
    )
    session.add(
        ReviewRoundClosure(
            round_id=review.id,
            base_revision_id=revision.id,
            action=CloseAction.ACCEPT_CURRENT,
            actor_user_id=run.submitted_by_user_id,
            display_name="Fatima Admin",
            collated_request_ids=[],
            rationale="Outline matches the source.",
        )
    )

    quiz = session.scalar(select(CommonMasteryQuiz).where(CommonMasteryQuiz.topic_id == topic_id))
    assert quiz is not None
    quiz_version = session.scalar(
        select(QuizVersion)
        .where(QuizVersion.quiz_id == quiz.id)
        .order_by(QuizVersion.version_number.desc())
    )
    assert quiz_version is not None
    published_at = started + timedelta(hours=2)
    version.published_at = published_at
    run.phase = RunPhase.PUBLISHED
    run.failure_reason = None
    run.draft_lesson_markdown = "The area of the figure is $n^2$ square units."
    run.published_lesson_version_id = version.id
    run.published_quiz_version_id = quiz_version.id
    session.commit()


def test_indexing_run_exposes_only_the_current_step(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
) -> None:
    run_id = _submit(client, admin_headers, seeded_topic_id)
    timings = _steps(client, admin_headers, run_id)
    by_step = {row["step"]: row for row in timings}
    assert list(by_step) == [
        "indexing",
        "outlining",
        "outline_review",
        "lesson",
        "items",
        "ready",
    ]
    assert by_step["indexing"]["status"] == "current"
    assert by_step["indexing"]["available"] is True
    assert by_step["indexing"]["label"] == "Indexing PDF"
    for step in ("outlining", "outline_review", "lesson", "items", "ready"):
        assert by_step[step]["status"] == "pending"
        assert by_step[step]["available"] is False
        assert by_step[step]["hint"] == "This step has not run yet."
        assert by_step[step]["started_at"] is None
        assert by_step[step]["error"] is None

    lesson = _step(client, admin_headers, run_id, "lesson")
    assert lesson["timing"]["available"] is False
    assert lesson["lesson"] is None
    indexing = _step(client, admin_headers, run_id, "indexing")
    assert indexing["indexing"]["chunk_count"] == 0
    assert indexing["indexing"]["page_count"] is None


def test_step_reads_require_an_administrator(
    client: TestClient,
    admin_headers: dict[str, str],
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
    seeded_topic_id: UUID,
) -> None:
    run_id = _submit(client, admin_headers, seeded_topic_id)
    missing = client.get(f"/api/v1/admin/generation-runs/{run_id}/steps")
    assert missing.status_code == 401
    student = client.get(
        f"/api/v1/admin/generation-runs/{run_id}/steps",
        headers=enrolled_student_headers,
    )
    assert student.status_code == 403
    assert student.json()["detail"] == "Administrator role required"
    teacher = client.get(
        f"/api/v1/admin/generation-runs/{run_id}/steps/indexing",
        headers=_teacher_headers(client, seeded_db),
    )
    assert teacher.status_code == 403
    unknown = client.get(
        f"/api/v1/admin/generation-runs/{uuid4()}/steps",
        headers=admin_headers,
    )
    assert unknown.status_code == 404
    bad_step = client.get(
        f"/api/v1/admin/generation-runs/{run_id}/steps/not-a-step",
        headers=admin_headers,
    )
    assert bad_step.status_code == 404


def test_published_run_step_details_use_stored_artifacts(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_db: Session,
    seeded_topic_id: UUID,
) -> None:
    run_id = _submit(client, admin_headers, seeded_topic_id)
    _mark_published(seeded_db, run_id, seeded_topic_id)
    assert seeded_db.scalar(select(Subtopic).where(Subtopic.topic_id == seeded_topic_id))

    timings = _steps(client, admin_headers, run_id)
    assert {row["status"] for row in timings} == {"complete"}
    assert all(row["available"] for row in timings)
    indexing_timing = next(row for row in timings if row["step"] == "indexing")
    assert indexing_timing["started_at"] is not None
    assert indexing_timing["ended_at"] is not None
    assert indexing_timing["duration_seconds"] is not None
    assert indexing_timing["duration_seconds"] >= 0
    assert indexing_timing["error"] is None

    indexing = _step(client, admin_headers, run_id, "indexing")
    assert indexing["indexing"]["page_count"] == 4
    assert indexing["indexing"]["chunk_count"] == 3
    assert indexing["indexing"]["truncated"] is False
    assert "closed plane figure" in indexing["indexing"]["chunks"][0]["preview"]

    outline = _step(client, admin_headers, run_id, "outlining")["outline"]
    assert outline["target_item_count"] >= 60
    by_title = {node["title"]: node for node in outline["nodes"]}
    assert "closed plane figure" in by_title["Area of a polygon"]["chunks"][0]["preview"]
    assert "equal edges" in by_title["Volume of a cube"]["chunks"][0]["preview"]
    assert by_title["Mensuration"]["chunks"] == []
    assert any("leftover" in chunk["preview"] for chunk in outline["unmapped_chunks"])

    review = _step(client, admin_headers, run_id, "outline_review")["outline_review"]
    assert len(review["rounds"]) == 1
    round_one = review["rounds"][0]
    assert round_one["decisions"] == [{"reviewer": "Fatima Admin", "verdict": "approve"}]
    assert round_one["comments"][0]["comment"] == "Split the polygon section."
    assert round_one["comments"][0]["target_title"] == "Area of a polygon"
    assert round_one["closure_action"] == "accept_current"
    assert round_one["closed_by"] == "Fatima Admin"
    assert round_one["sealed_at"] is not None

    lesson = _step(client, admin_headers, run_id, "lesson")["lesson"]
    assert "$n^2$" in lesson["markdown"]
    assert lesson["published_lesson_version_id"] is not None

    items = _step(client, admin_headers, run_id, "items")["items"]
    assert items["count"] >= 1
    mix = items["difficulty_mix"]
    assert mix["easy"] + mix["medium"] + mix["hard"] + mix["unlabeled"] == items["count"]
    assert sum(row["count"] for row in items["subtopic_counts"]) == items["count"]
    assert items["passed"] == 5
    assert items["fixed_on_retry"] == 2
    assert items["dropped"] == 1
    assert "correct_label" in items["items"][0]
    assert items["items"][0]["subtopic_title"]

    ready = _step(client, admin_headers, run_id, "ready")["ready"]
    assert ready["published"] is True
    assert ready["phase"] == "published"
    assert ready["published_at"] is not None
    assert ready["lesson_version_id"] is not None
    assert ready["quiz_version_id"] is not None
