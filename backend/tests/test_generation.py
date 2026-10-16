"""Topic-grain generation: admin submit, index, outline HITL, accept/discard/retry."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.url import to_sync_url
from education_platform.modules.academics.constants import POC_SUBJECT_CODE
from education_platform.modules.academics.models import (
    AcademicPeriod,
    Grade,
    GradeSubjectOffering,
    LearningOutcome,
    PeriodGrade,
    Subject,
    Subtopic,
    Topic,
)
from education_platform.modules.generation.models import (
    ContentGenerationOutlineNode,
    ContentGenerationRun,
    GenerationJob,
)
from education_platform.modules.generation.types import (
    GenerationJobKind,
    GenerationJobStatus,
    ProposedOutline,
    ProposedOutlineNode,
    RunPhase,
)
from education_platform.modules.generation.worker import process_generation_job_sync
from education_platform.modules.materials.models import (
    SourceMaterial,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
)
from education_platform.modules.rag.chunking import TextChunk, content_hash
from education_platform.modules.rag.models import IngestJob, IngestJobStatus
from education_platform.modules.synthetic.generator import SchoolSpec, generate_school
from education_platform.workers.ingest import process_ingest_job_sync

TINY_PDF = (
    b"%PDF-1.1\n"
    b"1 0 obj<<>>endobj\n"
    b"2 0 obj<< /Length 44 >>stream\n"
    b"BT /F1 12 Tf 100 700 Td (Hello RAG) Tj ET\n"
    b"endstream\nendobj\n"
    b"3 0 obj<< /Type /Page /Parent 4 0 R /Contents 2 0 R >>endobj\n"
    b"4 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"5 0 obj<< /Type /Catalog /Pages 4 0 R >>endobj\n"
    b"xref\n0 6\n0000000000 65535 f \n"
    b"trailer<< /Size 6 /Root 5 0 R >>\nstartxref\n0\n%%EOF\n"
)

SAMPLE_SECTION = "Properties of squares"
SAMPLE_CHUNK_TEXT = f"{SAMPLE_SECTION}\nA square has four equal sides and four right angles."

TEST_SPEC = SchoolSpec(sections_per_grade=2, students_per_section=3, term_weeks=2)
SCHOOL = TEST_SPEC.institution_name
PASSWORD = "demo1234"
ADMIN = "fatima.almansouri@alnoor.school"
TEACHER = "meera.krishnan@alnoor.school"

SEEDED_SLUG = "rectangles_squares_properties"


def _sample_chunks(*, text: str = SAMPLE_CHUNK_TEXT) -> list[TextChunk]:
    digest = content_hash(text)
    return [
        TextChunk(
            ordinal=1,
            text=text,
            content_hash=digest,
            token_count=len(text.split()),
            page_number=1,
            section_heading=SAMPLE_SECTION,
        )
    ]


def _second_chunk() -> list[TextChunk]:
    first = _sample_chunks()[0]
    extra = "Angles of a triangle\nInterior angles sum to 180 degrees."
    return [
        first,
        TextChunk(
            ordinal=2,
            text=extra,
            content_hash=content_hash(extra),
            token_count=len(extra.split()),
            page_number=2,
            section_heading="Angles of a triangle",
        ),
    ]


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


@pytest.fixture()
def seeded_topic_id(seeded_db: Session) -> UUID:
    subtopic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    return subtopic.topic_id


@pytest.fixture()
def alnoor(client: TestClient, clean_db: str) -> Iterator[TestClient]:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        generate_school(session, TEST_SPEC)
        session.commit()
    engine.dispose()
    yield client


def _headers(api: TestClient, email: str, institution_name: str = SCHOOL) -> dict[str, str]:
    response = api.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "institution_name": institution_name},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def _submit_admin(
    client: TestClient,
    headers: dict[str, str],
    topic_id: UUID,
    *,
    title: str = "Topic source PDF",
    target_item_count: int | None = None,
) -> Any:
    data: dict[str, str] = {"title": title}
    if target_item_count is not None:
        data["target_item_count"] = str(target_item_count)
    return client.post(
        f"/api/v1/admin/topics/{topic_id}/generation-runs",
        headers=headers,
        data=data,
        files={"file": ("topic.pdf", TINY_PDF, "application/pdf")},
    )


def _silence_embed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "education_platform.workers.ingest.embed_texts",
        lambda texts: [[0.02] * 384 for _ in texts],
    )


def _write_two_nodes(clusters: Any) -> ProposedOutline:
    first = clusters[0]
    second_heading = clusters[1].heading if len(clusters) > 1 else "Created node"
    second_mass = clusters[1].token_mass if len(clusters) > 1 else 4
    return ProposedOutline(
        nodes=(
            ProposedOutlineNode(
                key="n1",
                parent_key=None,
                slug=SEEDED_SLUG,
                title="Properties of Rectangles and Squares",
                token_mass=first.token_mass,
                prerequisite_score=Decimal("0.5"),
                centrality=Decimal("0.8"),
                proposed_outcomes=("Identify properties of squares",),
            ),
            ProposedOutlineNode(
                key="n2",
                parent_key=None,
                slug="angles-of-a-triangle",
                title=second_heading,
                token_mass=second_mass,
                prerequisite_score=Decimal("0.4"),
                centrality=Decimal("0.5"),
                proposed_outcomes=("State the angle sum",),
            ),
        )
    )


def _write_one_node(clusters: Any) -> ProposedOutline:
    heading = clusters[0].heading
    return ProposedOutline(
        nodes=(
            ProposedOutlineNode(
                key="n1",
                parent_key=None,
                slug="fresh-node",
                title=heading,
                token_mass=clusters[0].token_mass,
                prerequisite_score=Decimal("0.5"),
                centrality=Decimal("1"),
                proposed_outcomes=("Describe the heading",),
            ),
        )
    )


def _index_run(
    seeded_db: Session,
    version_id: UUID,
    *,
    chunks: list[TextChunk] | None = None,
) -> None:
    job = seeded_db.scalar(
        select(IngestJob).where(IngestJob.source_material_version_id == version_id)
    )
    assert job is not None
    process_ingest_job_sync(
        str(job.id),
        parse_pdf=lambda _path: chunks if chunks is not None else _sample_chunks(),
    )
    seeded_db.expire_all()


def _outline_run(
    seeded_db: Session,
    run_id: UUID,
    writer: Any,
) -> None:
    job = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.OUTLINE,
        )
    )
    assert job is not None
    process_generation_job_sync(job.id, write_outline=writer)
    seeded_db.expire_all()


def _directory_version(directory: dict[str, Any], subtopic_id: str) -> str | None:
    for subject in directory["subjects"]:
        for topic in subject["topics"]:
            for node in topic["subtopics"]:
                if node["id"] == subtopic_id:
                    return node.get("source_material_version_id")
    raise AssertionError(f"subtopic {subtopic_id} not in learning directory")


def test_admin_submit_indexes_topic_source(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
) -> None:
    response = _submit_admin(client, admin_headers, seeded_topic_id)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["phase"] == "indexing"
    assert body["topic_id"] == str(seeded_topic_id)
    run_id = UUID(body["run_id"])

    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.INDEXING
    assert run.intake_source_material_version_id is not None
    version = seeded_db.get(SourceMaterialVersion, run.intake_source_material_version_id)
    assert version is not None
    material = seeded_db.get(SourceMaterial, version.source_material_id)
    assert material is not None
    assert material.topic_id == seeded_topic_id
    assert material.subtopic_id is None
    assert material.slug == "source"
    job = seeded_db.scalar(
        select(IngestJob).where(IngestJob.source_material_version_id == version.id)
    )
    assert job is not None
    assert job.status is IngestJobStatus.QUEUED


def test_teacher_forbidden_on_admin_submit(alnoor: TestClient) -> None:
    response = alnoor.post(
        f"/api/v1/admin/topics/{uuid4()}/generation-runs",
        headers=_headers(alnoor, TEACHER),
        data={"title": "Worksheet"},
        files={"file": ("topic.pdf", TINY_PDF, "application/pdf")},
    )
    assert response.status_code == 403


def test_student_forbidden_on_admin_submit(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_topic_id: UUID,
) -> None:
    response = _submit_admin(client, enrolled_student_headers, seeded_topic_id)
    assert response.status_code == 403


def test_in_flight_second_submit_is_409(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
) -> None:
    first = _submit_admin(client, admin_headers, seeded_topic_id)
    assert first.status_code == 202, first.text
    second = _submit_admin(client, admin_headers, seeded_topic_id, title="Again")
    assert second.status_code == 409


def _seeded_subject_id(seeded_db: Session) -> UUID:
    subject = seeded_db.scalar(select(Subject).where(Subject.code == POC_SUBJECT_CODE))
    assert subject is not None
    return subject.id


def _submit_subject(
    client: TestClient,
    headers: dict[str, str],
    subject_id: UUID,
    *,
    title: str = "New topic PDF",
    grade_name: str | None = None,
) -> Any:
    data = {"title": title}
    if grade_name is not None:
        data["grade_name"] = grade_name
    return client.post(
        f"/api/v1/admin/subjects/{subject_id}/generation-runs",
        headers=headers,
        data=data,
        files={"file": ("topic.pdf", TINY_PDF, "application/pdf")},
    )


def _directory_topic_ids(directory: dict[str, Any]) -> set[str]:
    return {topic["id"] for subject in directory["subjects"] for topic in subject["topics"]}


def test_admin_subject_submit_creates_topic_and_starts_run(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    subject_id = _seeded_subject_id(seeded_db)
    seeded_topic = seeded_db.scalar(select(Topic).order_by(Topic.sequence))
    assert seeded_topic is not None
    before_ids = set(seeded_db.scalars(select(Topic.id)).all())
    max_sequence = seeded_db.scalar(
        select(func.max(Topic.sequence)).where(
            Topic.grade_subject_offering_id == seeded_topic.grade_subject_offering_id
        )
    )

    response = _submit_subject(client, admin_headers, subject_id, title="Fractions")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["phase"] == "indexing"
    topic_id = UUID(body["topic_id"])
    assert topic_id not in before_ids

    seeded_db.expire_all()
    topic = seeded_db.get(Topic, topic_id)
    assert topic is not None
    assert topic.name == "Fractions"
    assert topic.slug == "fractions"
    assert topic.sequence == (max_sequence or 0) + 1
    assert topic.grade_subject_offering_id == seeded_topic.grade_subject_offering_id

    run = seeded_db.get(ContentGenerationRun, UUID(body["run_id"]))
    assert run is not None
    assert run.topic_id == topic_id
    assert run.phase is RunPhase.INDEXING

    second = _submit_admin(client, admin_headers, topic_id, title="Again")
    assert second.status_code == 409


def test_subject_submit_uses_named_grade_when_several_exist(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    subject_id = _seeded_subject_id(seeded_db)
    subject = seeded_db.get(Subject, subject_id)
    assert subject is not None
    period = seeded_db.scalar(
        select(AcademicPeriod).where(AcademicPeriod.institution_id == subject.institution_id)
    )
    assert period is not None
    grade = Grade(institution_id=subject.institution_id, name="Grade 9", sort_order=9)
    seeded_db.add(grade)
    seeded_db.flush()
    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    seeded_db.add(period_grade)
    seeded_db.flush()
    offering = GradeSubjectOffering(period_grade_id=period_grade.id, subject_id=subject.id)
    seeded_db.add(offering)
    seeded_db.commit()

    ambiguous = _submit_subject(client, admin_headers, subject_id, title="Which grade")
    assert ambiguous.status_code == 409, ambiguous.text
    assert ambiguous.json()["detail"] == "This subject is offered on more than one grade."

    missing = _submit_subject(
        client, admin_headers, subject_id, title="Missing grade", grade_name="Grade 7"
    )
    assert missing.status_code == 404, missing.text

    placed = _submit_subject(
        client, admin_headers, subject_id, title="Grade nine unit", grade_name="Grade 9"
    )
    assert placed.status_code == 202, placed.text
    seeded_db.expire_all()
    topic = seeded_db.get(Topic, UUID(placed.json()["topic_id"]))
    assert topic is not None
    assert topic.grade_subject_offering_id == offering.id


def test_unpublished_upload_is_hidden_from_students(
    client: TestClient,
    admin_headers: dict[str, str],
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    subject_id = _seeded_subject_id(seeded_db)
    response = _submit_subject(client, admin_headers, subject_id, title="Secret Draft Unit")
    assert response.status_code == 202, response.text
    topic_id = response.json()["topic_id"]

    student = client.get("/api/v1/me/learning-directory", headers=enrolled_student_headers)
    assert student.status_code == 200, student.text
    assert topic_id not in _directory_topic_ids(student.json())
    titles = [
        topic["title"] for subject in student.json()["subjects"] for topic in subject["topics"]
    ]
    assert "Secret Draft Unit" not in titles

    admin = client.get("/api/v1/me/learning-directory", headers=admin_headers)
    assert admin.status_code == 200, admin.text
    assert topic_id in _directory_topic_ids(admin.json())


def test_teacher_forbidden_on_admin_subject_submit(alnoor: TestClient) -> None:
    response = alnoor.post(
        f"/api/v1/admin/subjects/{uuid4()}/generation-runs",
        headers=_headers(alnoor, TEACHER),
        data={"title": "Worksheet"},
        files={"file": ("topic.pdf", TINY_PDF, "application/pdf")},
    )
    assert response.status_code == 403


def test_admin_subject_submit_unknown_subject_is_404(
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    response = _submit_subject(client, admin_headers, uuid4())
    assert response.status_code == 404


def test_admin_subtopic_materials_stays_index_only(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    subtopic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    response = client.post(
        f"/api/v1/admin/subtopics/{subtopic.id}/materials",
        headers=admin_headers,
        data={"title": "Geometry PDF"},
        files={"file": ("lesson.pdf", TINY_PDF, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    version_id = UUID(response.json()["version_id"])
    _index_run(seeded_db, version_id)
    version = seeded_db.get(SourceMaterialVersion, version_id)
    assert version is not None
    assert version.lifecycle_status is SourceMaterialVersionStatus.READY
    run = seeded_db.scalar(
        select(ContentGenerationRun).where(
            ContentGenerationRun.intake_source_material_version_id == version_id
        )
    )
    assert run is None
    get_settings.cache_clear()


def test_intake_index_enqueues_outline(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    response = _submit_admin(client, admin_headers, seeded_topic_id)
    run_id = UUID(response.json()["run_id"])
    intake_id = UUID(
        client.get(
            f"/api/v1/teaching/generation-runs/{run_id}",
            headers=admin_headers,
        ).json()["intake_version_id"]
    )
    _index_run(seeded_db, intake_id)
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.OUTLINING
    version = seeded_db.get(SourceMaterialVersion, intake_id)
    assert version is not None
    assert version.lifecycle_status is SourceMaterialVersionStatus.READY
    job = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.OUTLINE,
        )
    )
    assert job is not None
    assert job.status is GenerationJobStatus.QUEUED
    get_settings.cache_clear()


def test_outline_writer_persists_nodes(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    response = _submit_admin(client, admin_headers, seeded_topic_id)
    run_id = UUID(response.json()["run_id"])
    intake_id = UUID(
        client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()[
            "intake_version_id"
        ]
    )
    _index_run(seeded_db, intake_id)
    _outline_run(seeded_db, run_id, _write_one_node)
    fetched = client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["phase"] == "outline_review"
    assert body["outline"]["nodes"]
    version = seeded_db.get(SourceMaterialVersion, intake_id)
    assert version is not None
    assert version.lifecycle_status is SourceMaterialVersionStatus.READY
    get_settings.cache_clear()


def test_outline_writer_failure_keeps_intake_ready(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    response = _submit_admin(client, admin_headers, seeded_topic_id)
    run_id = UUID(response.json()["run_id"])
    intake_id = UUID(
        client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()[
            "intake_version_id"
        ]
    )
    _index_run(seeded_db, intake_id)

    def _boom(_clusters: Any) -> ProposedOutline:
        raise RuntimeError("LLM down")

    _outline_run(seeded_db, run_id, _boom)
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.FAILED
    job = seeded_db.scalar(select(GenerationJob).where(GenerationJob.run_id == run_id))
    assert job is not None
    assert job.status is GenerationJobStatus.FAILED
    version = seeded_db.get(SourceMaterialVersion, intake_id)
    assert version is not None
    assert version.lifecycle_status is SourceMaterialVersionStatus.READY

    retried = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/retry",
        headers=admin_headers,
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["phase"] == "outlining"
    queued = list(
        seeded_db.scalars(
            select(GenerationJob).where(
                GenerationJob.run_id == run_id,
                GenerationJob.status == GenerationJobStatus.QUEUED,
            )
        ).all()
    )
    assert len(queued) == 1
    get_settings.cache_clear()


def test_index_failure_blocks_retry(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    response = _submit_admin(client, admin_headers, seeded_topic_id)
    run_id = UUID(response.json()["run_id"])
    intake_id = UUID(
        client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()[
            "intake_version_id"
        ]
    )

    def _boom(_path: Path) -> list[TextChunk]:
        raise RuntimeError("Docling parse failed")

    job = seeded_db.scalar(
        select(IngestJob).where(IngestJob.source_material_version_id == intake_id)
    )
    assert job is not None
    process_ingest_job_sync(str(job.id), parse_pdf=_boom)
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.FAILED
    version = seeded_db.get(SourceMaterialVersion, intake_id)
    assert version is not None
    assert version.lifecycle_status is SourceMaterialVersionStatus.FAILED
    retried = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/retry",
        headers=admin_headers,
    )
    assert retried.status_code == 409
    get_settings.cache_clear()


def test_patch_outline_replaces_and_drops(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    response = _submit_admin(client, admin_headers, seeded_topic_id)
    run_id = UUID(response.json()["run_id"])
    intake_id = UUID(
        client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()[
            "intake_version_id"
        ]
    )
    _index_run(seeded_db, intake_id, chunks=_second_chunk())
    _outline_run(seeded_db, run_id, _write_two_nodes)
    body = client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()
    nodes = body["outline"]["nodes"]
    assert len(nodes) == 2
    keep = nodes[0]
    dropped = nodes[1]
    patched = client.patch(
        f"/api/v1/teaching/generation-runs/{run_id}/outline",
        headers=admin_headers,
        json={
            "nodes": [
                {
                    "id": keep["id"],
                    "parent_id": None,
                    "slug": keep["slug"],
                    "title": "Renamed node",
                    "weight": "2.5",
                    "matched_subtopic_id": keep["matched_subtopic_id"],
                    "force_create": False,
                    "proposed_outcomes": ["Identify properties of squares"],
                    "sequence": 1,
                }
            ]
        },
    )
    assert patched.status_code == 410, patched.text
    remaining = client.get(
        f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers
    ).json()["outline"]["nodes"]
    assert len(remaining) == 2
    seeded_db.expire_all()
    assert seeded_db.get(ContentGenerationOutlineNode, UUID(dropped["id"])) is not None
    get_settings.cache_clear()


def test_teacher_can_get_and_decide_but_not_close(
    alnoor: TestClient,
    clean_db: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    teacher_headers = _headers(alnoor, TEACHER)
    listed = alnoor.get("/api/v1/authoring/subtopics", headers=teacher_headers)
    assert listed.status_code == 200, listed.text
    math = [row for row in listed.json() if row["subject"] == "Mathematics"]
    assert math
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        subtopic = session.get(Subtopic, UUID(math[0]["id"]))
        assert subtopic is not None
        topic_id = subtopic.topic_id
    engine.dispose()

    admin_headers = _headers(alnoor, ADMIN)
    created = _submit_admin(alnoor, admin_headers, topic_id)
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]
    fetched = alnoor.get(
        f"/api/v1/teaching/generation-runs/{run_id}",
        headers=teacher_headers,
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["phase"] == "indexing"

    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        intake_id = UUID(fetched.json()["intake_version_id"])
        _index_run(session, intake_id)
        _outline_run(session, UUID(run_id), _write_one_node)
    engine.dispose()

    reviewed = alnoor.get(
        f"/api/v1/teaching/generation-runs/{run_id}",
        headers=teacher_headers,
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["phase"] == "outline_review"
    keep = reviewed.json()["outline"]["nodes"][0]
    patched = alnoor.patch(
        f"/api/v1/teaching/generation-runs/{run_id}/outline",
        headers=teacher_headers,
        json={
            "nodes": [
                {
                    "id": keep["id"],
                    "parent_id": None,
                    "slug": keep["slug"],
                    "title": "Teacher edit",
                    "weight": str(keep["weight"]),
                    "matched_subtopic_id": keep["matched_subtopic_id"],
                    "force_create": False,
                    "proposed_outcomes": keep["proposed_outcomes"],
                    "sequence": keep["sequence"],
                }
            ]
        },
    )
    assert patched.status_code == 410, patched.text

    workspace = alnoor.get(
        f"/api/v1/teaching/generation-runs/{run_id}/review",
        headers=teacher_headers,
    )
    assert workspace.status_code == 200, workspace.text
    round_id = workspace.json()["open_round"]["id"]
    revision_id = workspace.json()["active_revision"]["id"]
    decided = alnoor.post(
        f"/api/v1/teaching/generation-runs/{run_id}/review-rounds/{round_id}/decisions",
        headers=teacher_headers,
        json={"revision_id": revision_id, "verdict": "approve"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["verdict"] == "approve"

    listed_runs = alnoor.get(
        f"/api/v1/teaching/topics/{topic_id}/generation-runs",
        headers=teacher_headers,
    )
    assert listed_runs.status_code == 200, listed_runs.text
    assert listed_runs.json()[0]["id"] == run_id

    for action in ("accept-outline", "discard", "retry"):
        blocked = alnoor.post(
            f"/api/v1/teaching/generation-runs/{run_id}/{action}",
            headers=teacher_headers,
        )
        assert blocked.status_code == 403, action
    closed = alnoor.post(
        f"/api/v1/teaching/generation-runs/{run_id}/review-rounds/{round_id}/close",
        headers=teacher_headers,
        json={"revision_id": revision_id, "action": "accept_current"},
    )
    assert closed.status_code == 403
    get_settings.cache_clear()


def test_admin_accept_freezes_and_matches(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    subtopic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    original_name = subtopic.name
    original_sequence = subtopic.sequence
    response = _submit_admin(client, admin_headers, seeded_topic_id)
    run_id = UUID(response.json()["run_id"])
    intake_id = UUID(
        client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()[
            "intake_version_id"
        ]
    )
    _index_run(seeded_db, intake_id, chunks=_second_chunk())
    _outline_run(seeded_db, run_id, _write_two_nodes)
    accepted = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/accept-outline",
        headers=admin_headers,
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["phase"] == "generating"
    kinds = [job["kind"] for job in body["jobs"]]
    assert set(kinds) == {"lesson"}
    assert all(job["status"] == "queued" for job in body["jobs"])
    nodes = body["outline"]["nodes"]
    assert all(node["quota"] is not None for node in nodes)
    assert all(node["accepted_subtopic_id"] for node in nodes)
    seeded_db.expire_all()
    matched = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert matched is not None
    assert matched.name == original_name
    assert matched.sequence == original_sequence
    created = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == "angles-of-a-triangle"))
    assert created is not None
    outcomes = list(
        seeded_db.scalars(
            select(LearningOutcome).where(LearningOutcome.subtopic_id == created.id)
        ).all()
    )
    assert any(row.statement == "State the angle sum" for row in outcomes)
    get_settings.cache_clear()


def test_discard_allows_new_submit(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    response = _submit_admin(client, admin_headers, seeded_topic_id)
    run_id = UUID(response.json()["run_id"])
    intake_id = UUID(
        client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()[
            "intake_version_id"
        ]
    )
    _index_run(seeded_db, intake_id)
    _outline_run(seeded_db, run_id, _write_one_node)
    discarded = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/discard",
        headers=admin_headers,
    )
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["phase"] == "discarded"
    again = _submit_admin(client, admin_headers, seeded_topic_id, title="Second run")
    assert again.status_code == 202, again.text
    assert again.json()["run_id"] != str(run_id)
    get_settings.cache_clear()


def test_cancel_in_progress_run_then_retry_same_pdf(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    response = _submit_admin(client, admin_headers, seeded_topic_id)
    run_id = UUID(response.json()["run_id"])
    intake_id = UUID(
        client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()[
            "intake_version_id"
        ]
    )
    _index_run(seeded_db, intake_id)
    cancelled = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/discard",
        headers=admin_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["phase"] == "discarded"
    seeded_db.expire_all()
    outline_job = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.OUTLINE,
        )
    )
    assert outline_job is not None
    assert outline_job.status is GenerationJobStatus.FAILED
    assert outline_job.error == "Cancelled."

    outline_job.status = GenerationJobStatus.QUEUED
    outline_job.error = None
    seeded_db.commit()
    process_generation_job_sync(outline_job.id, write_outline=_write_one_node)
    seeded_db.expire_all()
    revived = seeded_db.get(ContentGenerationRun, run_id)
    assert revived is not None
    assert revived.phase is RunPhase.DISCARDED

    retried = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/retry",
        headers=admin_headers,
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["phase"] == "outlining"
    seeded_db.expire_all()
    queued = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.OUTLINE,
            GenerationJob.status == GenerationJobStatus.QUEUED,
        )
    )
    assert queued is not None
    get_settings.cache_clear()


def test_delete_unpublished_topic_removes_the_attempt(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    subject_id = _seeded_subject_id(seeded_db)
    created = _submit_subject(client, admin_headers, subject_id, title="Scratch chapter")
    assert created.status_code == 202, created.text
    topic_id = UUID(created.json()["topic_id"])
    run_id = UUID(created.json()["run_id"])
    intake_id = UUID(
        client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()[
            "intake_version_id"
        ]
    )
    _index_run(seeded_db, intake_id)
    _outline_run(seeded_db, run_id, _write_one_node)

    refused = client.delete(f"/api/v1/admin/topics/{seeded_topic_id}", headers=admin_headers)
    assert refused.status_code == 409, refused.text

    removed = client.delete(f"/api/v1/admin/topics/{topic_id}", headers=admin_headers)
    assert removed.status_code == 204, removed.text
    seeded_db.expire_all()
    assert seeded_db.get(Topic, topic_id) is None
    assert seeded_db.get(ContentGenerationRun, run_id) is None
    assert seeded_db.get(Topic, seeded_topic_id) is not None
    assert (
        seeded_db.scalar(select(SourceMaterial).where(SourceMaterial.topic_id == topic_id)) is None
    )
    get_settings.cache_clear()


def test_deprecated_alias_returns_run_id(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    subtopic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    response = client.post(
        f"/api/v1/teaching/subtopics/{subtopic.id}/lesson-proposals",
        headers=admin_headers,
        data={"title": "Legacy alias"},
        files={"file": ("topic.pdf", TINY_PDF, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert "run_id" in body
    assert body["phase"] == "indexing"
    assert body["topic_id"] == str(subtopic.topic_id)


def test_teacher_lesson_proposal_post_is_gone(alnoor: TestClient) -> None:
    listed = alnoor.get("/api/v1/authoring/subtopics", headers=_headers(alnoor, TEACHER))
    assert listed.status_code == 200, listed.text
    math = [row for row in listed.json() if row["subject"] == "Mathematics"]
    assert math
    response = alnoor.post(
        f"/api/v1/teaching/subtopics/{math[0]['id']}/lesson-proposals",
        headers=_headers(alnoor, TEACHER),
        data={"title": "Teacher upload"},
        files={"file": ("topic.pdf", TINY_PDF, "application/pdf")},
    )
    assert response.status_code == 410


def test_old_proposal_routes_are_gone(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    subtopic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    listed = client.get(
        f"/api/v1/teaching/subtopics/{subtopic.id}/lesson-proposals",
        headers=admin_headers,
    )
    assert listed.status_code == 410
    fetched = client.get(
        f"/api/v1/teaching/lesson-proposals/{uuid4()}",
        headers=admin_headers,
    )
    assert fetched.status_code == 410


def test_student_catalog_unchanged_after_submit(
    client: TestClient,
    admin_headers: dict[str, str],
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
    seeded_topic_id: UUID,
) -> None:
    subtopic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    before = client.get("/api/v1/me/learning-directory", headers=enrolled_student_headers)
    assert before.status_code == 200, before.text
    published_id = _directory_version(before.json(), str(subtopic.id))
    assert published_id is not None
    material = client.get(
        f"/api/v1/subtopics/{subtopic.id}/material",
        headers=enrolled_student_headers,
    )
    assert material.status_code == 200, material.text
    assert material.json()["source_material_version_id"] == published_id

    response = _submit_admin(client, admin_headers, seeded_topic_id)
    assert response.status_code == 202, response.text

    after = client.get("/api/v1/me/learning-directory", headers=enrolled_student_headers).json()
    assert _directory_version(after, str(subtopic.id)) == published_id
    still = client.get(
        f"/api/v1/subtopics/{subtopic.id}/material",
        headers=enrolled_student_headers,
    )
    assert still.json()["source_material_version_id"] == published_id
