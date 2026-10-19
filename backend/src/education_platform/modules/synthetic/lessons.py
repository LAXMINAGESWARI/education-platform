"""Published lessons for the synthetic school.

Content is a pure function of ``SYNTHETIC_SEED`` and the topic name, so a rebuild
matches and the score generator's random stream stays untouched.

Each lesson is submitted by the subject teacher, held as awaiting approval long
enough to require markdown, then published. Student reads only see the published
version. Quizzes get an open release window and a binding to that version.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.modules.academics.models import Subtopic, Topic
from education_platform.modules.assessments.models import (
    QuizMaterialBinding,
    QuizRelease,
    QuizReleaseStatus,
    QuizVersion,
)
from education_platform.modules.materials.models import (
    MaterialProgressStatus,
    SourceMaterial,
    SourceMaterialStatus,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
    StudentMaterialProgress,
)
from education_platform.modules.materials.seed_content import checksum


def subtopic_lesson_markdown(
    *,
    seed: int,
    grade_name: str,
    subject_name: str,
    title: str,
) -> str:
    """Three short slides a student can actually read."""
    rng = random.Random(f"{seed}|{grade_name}|{subject_name}|{title}|lesson")
    whole = rng.randint(4, 12)
    part = rng.randint(1, whole - 1)
    return (
        f"# {title}\n\n"
        f"## Slide 1 — Learning objectives\n"
        f"- Explain {title.lower()} in the language of {grade_name} {subject_name}.\n"
        f"- Use one worked example before the mastery check.\n\n"
        f"## Slide 2 — The idea\n"
        f"{title} is a {grade_name} {subject_name} subtopic. "
        f"Students start from a concrete case, name the parts, and then apply the same "
        f"step to a new question.\n\n"
        f"Worked example: split a set of {whole} into equal groups and take {part}. "
        f"Say what the {part} represents, and what stays unchanged when the whole changes.\n\n"
        f"## Slide 3 — Before the quiz\n"
        f"Restate {title.lower()} in one sentence, then answer the mastery check. "
        f"The quiz stays locked until this lesson is marked complete.\n"
    )


def topic_lesson_markdown(
    *,
    seed: int,
    grade_name: str,
    subject_name: str,
    title: str,
    subtopics: tuple[str, ...],
) -> str:
    """One topic lesson. Subtopic names are ideas inside it, not separate lessons."""
    rng = random.Random(f"{seed}|{grade_name}|{subject_name}|{title}|topic")
    minutes = rng.randint(8, 14)
    idea_lines = "\n".join(f"- {name}" for name in subtopics)
    return (
        f"# {title}\n\n"
        f"## Slide 1 — This topic\n"
        f"{title} is a {grade_name} {subject_name} topic. "
        f"Plan about {minutes} minutes, then take the topic quiz.\n\n"
        f"## Slide 2 — Ideas in this topic\n"
        f"{idea_lines}\n\n"
        f"The topic quiz stays locked until this lesson is marked complete. "
        f"A quiz you have already passed stays marked passed.\n"
    )


def publish_approved_lesson(
    session: Session,
    *,
    topic: Topic | None = None,
    subtopic: Subtopic | None = None,
    title: str,
    markdown: str,
    submitted_by_user_id: UUID,
) -> SourceMaterialVersion:
    """Teacher submits markdown, then the row is published for students."""
    if (topic is None) == (subtopic is None):
        raise ValueError("A lesson belongs to a topic or a subtopic, not both.")

    material = _lesson_material(session, topic=topic, subtopic=subtopic, title=title)
    digest = checksum(markdown)
    published = session.scalar(
        select(SourceMaterialVersion).where(
            SourceMaterialVersion.source_material_id == material.id,
            SourceMaterialVersion.lifecycle_status == SourceMaterialVersionStatus.PUBLISHED,
        )
    )
    if published is not None and published.checksum == digest:
        published.submitted_by_user_id = published.submitted_by_user_id or submitted_by_user_id
        material.status = SourceMaterialStatus.PUBLISHED
        material.title = title
        return published

    if published is not None:
        published.lifecycle_status = SourceMaterialVersionStatus.SUPERSEDED

    next_version = (
        int(
            session.scalar(
                select(SourceMaterialVersion.version_number)
                .where(SourceMaterialVersion.source_material_id == material.id)
                .order_by(SourceMaterialVersion.version_number.desc())
                .limit(1)
            )
            or 0
        )
        + 1
    )
    version = SourceMaterialVersion(
        source_material_id=material.id,
        version_number=next_version,
        lifecycle_status=SourceMaterialVersionStatus.AWAITING_APPROVAL,
        title=title,
        submitted_by_user_id=submitted_by_user_id,
        content_markdown=markdown,
        content_format="markdown",
        checksum=digest,
    )
    session.add(version)
    session.flush()
    version.lifecycle_status = SourceMaterialVersionStatus.PUBLISHED
    version.published_at = datetime.now(UTC)
    material.status = SourceMaterialStatus.PUBLISHED
    material.title = title
    session.flush()
    return version


def ensure_open_quiz_window(
    session: Session,
    quiz_version: QuizVersion,
    *,
    starts_at: datetime,
    ends_at: datetime,
    released_by_user_id: UUID,
) -> None:
    """One open release whose window contains the seeded term and the following year."""
    release = session.scalar(
        select(QuizRelease).where(
            QuizRelease.quiz_version_id == quiz_version.id,
            QuizRelease.status == QuizReleaseStatus.OPEN,
        )
    )
    if release is None:
        session.add(
            QuizRelease(
                quiz_version_id=quiz_version.id,
                status=QuizReleaseStatus.OPEN,
                window_starts_at=starts_at,
                window_ends_at=ends_at,
                released_by_user_id=released_by_user_id,
            )
        )
        return
    now = datetime.now(UTC)
    if release.window_starts_at is not None and release.window_starts_at > now:
        release.window_starts_at = starts_at
    if release.window_ends_at is not None and release.window_ends_at < now:
        release.window_ends_at = ends_at
    release.released_by_user_id = release.released_by_user_id or released_by_user_id


def bind_quiz_to_lesson(session: Session, quiz_version_id: UUID, lesson_version_id: UUID) -> None:
    existing = session.scalar(
        select(QuizMaterialBinding.id).where(
            QuizMaterialBinding.quiz_version_id == quiz_version_id,
            QuizMaterialBinding.source_material_version_id == lesson_version_id,
        )
    )
    if existing is None:
        session.add(
            QuizMaterialBinding(
                quiz_version_id=quiz_version_id,
                source_material_version_id=lesson_version_id,
            )
        )


def complete_lesson(
    session: Session,
    *,
    enrollment_id: UUID,
    version_id: UUID,
    completed_at: datetime,
) -> None:
    """Record the lesson finished before a seeded quiz attempt."""
    existing = session.scalar(
        select(StudentMaterialProgress).where(
            StudentMaterialProgress.student_subject_enrollment_id == enrollment_id,
            StudentMaterialProgress.source_material_version_id == version_id,
        )
    )
    if existing is not None:
        existing.status = MaterialProgressStatus.COMPLETED
        existing.completed_at = existing.completed_at or completed_at
        return
    opened_at = completed_at - timedelta(minutes=20)
    session.add(
        StudentMaterialProgress(
            student_subject_enrollment_id=enrollment_id,
            source_material_version_id=version_id,
            status=MaterialProgressStatus.COMPLETED,
            opened_at=opened_at,
            last_opened_at=completed_at,
            completed_at=completed_at,
            last_unit_ordinal=3,
        )
    )


def _lesson_material(
    session: Session,
    *,
    topic: Topic | None,
    subtopic: Subtopic | None,
    title: str,
) -> SourceMaterial:
    if subtopic is not None:
        material = session.scalar(
            select(SourceMaterial).where(
                SourceMaterial.subtopic_id == subtopic.id,
                SourceMaterial.slug == "lesson",
            )
        )
        parent = {"subtopic_id": subtopic.id}
    else:
        assert topic is not None
        material = session.scalar(
            select(SourceMaterial).where(
                SourceMaterial.topic_id == topic.id,
                SourceMaterial.slug == "lesson",
            )
        )
        parent = {"topic_id": topic.id}
    if material is None:
        material = SourceMaterial(
            title=title,
            slug="lesson",
            status=SourceMaterialStatus.DRAFT,
            **parent,
        )
        session.add(material)
        session.flush()
    return material
