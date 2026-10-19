"""Materials catalog and content reads from Postgres."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.errors import DomainError
from education_platform.modules.academics.directory import build_learning_directory
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.academics.service import (
    CurriculumNode,
    load_subtopic_node,
    load_topic_node,
    require_subject_enrollment,
    subject_enrollment_for,
)
from education_platform.modules.assessments.models import QuizScope
from education_platform.modules.assessments.queries import questions_for_quiz_version, released_quiz
from education_platform.modules.assessments.schemas import QuizMaterial
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.generation.recap import present_topic_recap
from education_platform.modules.materials.markdown_parser import parse_slides
from education_platform.modules.materials.models import (
    MaterialProgressStatus,
    StudentMaterialProgress,
)
from education_platform.modules.materials.queries import (
    progress_for,
    published_material_version,
    published_topic_material_version,
    subtopic_by_slug,
)
from education_platform.modules.materials.schemas import (
    LessonMaterial,
    LessonSlide,
    MaterialProgressOut,
    MaterialProgressUpdate,
    TopicSummary,
)


def _progress_out(progress: StudentMaterialProgress | None) -> MaterialProgressOut | None:
    if progress is None:
        return None
    return MaterialProgressOut(
        status=progress.status.value,
        opened_at=progress.opened_at,
        last_opened_at=progress.last_opened_at,
        completed_at=progress.completed_at,
        last_unit_ordinal=progress.last_unit_ordinal,
        source_material_version_id=progress.source_material_version_id,
    )


async def list_topics(session: AsyncSession, scope: Scope) -> list[TopicSummary]:
    directory = await build_learning_directory(session, scope)
    topics: list[TopicSummary] = []
    for subject in directory.subjects:
        for topic in subject.topics:
            for subtopic in topic.subtopics:
                has_quiz = bool(subtopic.quiz and subtopic.quiz.available)
                if not subtopic.has_lesson and not has_quiz:
                    continue
                topics.append(
                    TopicSummary(
                        id=subtopic.slug,
                        title=subtopic.title,
                        has_lesson=subtopic.has_lesson,
                        has_quiz=has_quiz,
                    )
                )
    return topics


async def get_subtopic_by_slug(session: AsyncSession, topic_id: str, *, scope: Scope) -> Subtopic:
    match = await subtopic_by_slug(session, topic_id, scope=scope)
    if match is None:
        raise DomainError(f"Topic '{topic_id}' not found", status_code=404)
    return match


async def _covered_subtopic_node(
    session: AsyncSession, scope: Scope, subtopic_id: UUID
) -> CurriculumNode:
    node = await load_subtopic_node(session, subtopic_id)
    if node is None or node.subtopic is None:
        raise DomainError("Topic not found", status_code=404)
    if not scope.covers_offering(node.offering.id, institution_id=node.institution.id):
        raise DomainError("Topic not found", status_code=404)
    return node


async def _covered_topic_node(
    session: AsyncSession, scope: Scope, topic_id: UUID
) -> CurriculumNode:
    node = await load_topic_node(session, topic_id)
    if node is None:
        raise DomainError("Topic not found", status_code=404)
    if not scope.covers_offering(node.offering.id, institution_id=node.institution.id):
        raise DomainError("Topic not found", status_code=404)
    return node


async def get_lesson(session: AsyncSession, scope: Scope, topic_id: str) -> LessonMaterial:
    subtopic = await get_subtopic_by_slug(session, topic_id, scope=scope)
    return await get_subtopic_lesson(session, scope, subtopic.id)


async def get_subtopic_lesson(
    session: AsyncSession, scope: Scope, subtopic_id: UUID
) -> LessonMaterial:
    node = await _covered_subtopic_node(session, scope, subtopic_id)
    version = await published_material_version(session, subtopic_id)
    if version is None or not version.content_markdown:
        raise DomainError("Lesson not found", status_code=404)
    enrollment = None
    if scope.self_student_id is not None:
        enrollment = await subject_enrollment_for(session, scope.self_student_id, node.offering.id)
    progress = await progress_for(
        session,
        enrollment.id if enrollment else None,
        version.id,
    )
    quiz = await released_quiz(
        session, quiz_scope=QuizScope.SUBTOPIC_MASTERY, target_id=subtopic_id
    )
    markdown = present_topic_recap(version.content_markdown)
    slides = [
        LessonSlide(number=slide.number, title=slide.title, content=slide.content)
        for slide in parse_slides(markdown)
    ]
    return LessonMaterial(
        id=str(subtopic_id),
        title=version.title,
        markdown=markdown,
        slides=slides,
        progress=_progress_out(progress),
        source_material_version_id=version.id,
        published_at=version.published_at,
        quiz_unlocked=progress is not None and progress.status == MaterialProgressStatus.COMPLETED,
        quiz_id=quiz[0].id if quiz else None,
    )


async def get_topic_lesson(session: AsyncSession, scope: Scope, topic_id: UUID) -> LessonMaterial:
    """Published topic ``slug=lesson`` markdown. Never joins answer keys."""
    node = await _covered_topic_node(session, scope, topic_id)
    version = await published_topic_material_version(session, topic_id)
    if version is None or not version.content_markdown:
        raise DomainError("Lesson not found", status_code=404)
    enrollment = None
    if scope.self_student_id is not None:
        enrollment = await subject_enrollment_for(session, scope.self_student_id, node.offering.id)
    progress = await progress_for(
        session,
        enrollment.id if enrollment else None,
        version.id,
    )
    quiz = await released_quiz(session, quiz_scope=QuizScope.TOPIC_MASTERY, target_id=topic_id)
    markdown = present_topic_recap(version.content_markdown)
    slides = [
        LessonSlide(number=slide.number, title=slide.title, content=slide.content)
        for slide in parse_slides(markdown)
    ]
    return LessonMaterial(
        id=str(topic_id),
        title=version.title,
        markdown=markdown,
        slides=slides,
        progress=_progress_out(progress),
        source_material_version_id=version.id,
        published_at=version.published_at,
        quiz_unlocked=(
            quiz is not None
            and progress is not None
            and progress.status == MaterialProgressStatus.COMPLETED
        ),
        quiz_id=quiz[0].id if quiz else None,
    )


async def get_quiz(session: AsyncSession, scope: Scope, topic_id: str) -> QuizMaterial:
    """Return quiz questions without joining question_answer_keys."""
    subtopic = await get_subtopic_by_slug(session, topic_id, scope=scope)
    return await get_subtopic_quiz(session, scope, subtopic.id)


async def get_subtopic_quiz(session: AsyncSession, scope: Scope, subtopic_id: UUID) -> QuizMaterial:
    """Released subtopic quiz by id — unambiguous even when slugs collide across offerings."""
    await _covered_subtopic_node(session, scope, subtopic_id)
    released = await released_quiz(
        session, quiz_scope=QuizScope.SUBTOPIC_MASTERY, target_id=subtopic_id
    )
    if released is None:
        raise DomainError("Quiz not found", status_code=404)
    quiz, quiz_version = released

    questions = await questions_for_quiz_version(session, quiz_version.id)
    if not questions:
        raise DomainError(
            f"Quiz for subtopic '{subtopic_id}' has no questions",
            status_code=500,
        )
    return QuizMaterial(
        id=quiz.id,
        title=quiz.title,
        questions=questions,
        pass_threshold_percent=quiz_version.pass_threshold_percent,
        duration_seconds=quiz_version.duration_seconds,
        max_attempts=quiz_version.max_attempts,
        result_release_mode=quiz_version.result_release_mode.value,
    )


async def update_material_progress(
    session: AsyncSession,
    scope: Scope,
    subtopic_id: UUID,
    payload: MaterialProgressUpdate,
) -> MaterialProgressOut:
    node = await _covered_subtopic_node(session, scope, subtopic_id)
    enrollment = await require_subject_enrollment(session, scope, node.offering.id)
    version = await published_material_version(session, subtopic_id)
    if version is None:
        raise DomainError("Lesson not found", status_code=404)
    return await _save_progress(session, enrollment.id, version.id, payload)


async def update_topic_material_progress(
    session: AsyncSession,
    scope: Scope,
    topic_id: UUID,
    payload: MaterialProgressUpdate,
) -> MaterialProgressOut:
    node = await _covered_topic_node(session, scope, topic_id)
    enrollment = await require_subject_enrollment(session, scope, node.offering.id)
    version = await published_topic_material_version(session, topic_id)
    if version is None:
        raise DomainError("Lesson not found", status_code=404)
    return await _save_progress(session, enrollment.id, version.id, payload)


async def _save_progress(
    session: AsyncSession,
    enrollment_id: UUID,
    version_id: UUID,
    payload: MaterialProgressUpdate,
) -> MaterialProgressOut:
    now = datetime.now(UTC)
    progress = await progress_for(session, enrollment_id, version_id)
    if progress is None:
        progress = StudentMaterialProgress(
            student_subject_enrollment_id=enrollment_id,
            source_material_version_id=version_id,
            status=MaterialProgressStatus.OPENED,
            opened_at=now,
            last_opened_at=now,
            last_unit_ordinal=payload.last_unit_ordinal,
        )
        session.add(progress)
    else:
        progress.last_opened_at = now
        if payload.last_unit_ordinal is not None:
            progress.last_unit_ordinal = payload.last_unit_ordinal
    if payload.status == "completed":
        progress.status = MaterialProgressStatus.COMPLETED
        progress.completed_at = progress.completed_at or now
    await session.flush()
    await session.refresh(progress)
    out = _progress_out(progress)
    assert out is not None
    return out
