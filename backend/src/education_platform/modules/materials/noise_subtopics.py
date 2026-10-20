"""Merge textbook-heading subtopics into a real concept.

Outline generation already folds headings such as ``EXERCISE 9.2`` into the
concept they belong to. Rows created before that check, or stored by an older
accept path, still sit in ``subtopics``. This module reattaches their questions,
outcomes, materials, and quizzes to the previous concept in the same unit (or
the next concept when the heading comes first) and then deletes the heading.

Attempts stay on their quiz. A heading quiz is pointed at the concept when that
concept has no quiz of its own. When the concept already has a subtopic quiz,
the heading row stays (the quiz cannot move onto a second subtopic) so its
attempts are not dropped. Questions, outcomes, and materials still move.

The function is idempotent: a second run finds nothing left to merge, apart from
rows that were blocked because both quizzes have attempts.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import InstrumentedAttribute, Session

from education_platform.modules.academics.models import LearningOutcome, Subtopic
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    Question,
    QuestionOutcomeTag,
    QuizAttempt,
    QuizVersion,
)
from education_platform.modules.engagement.models import SlideEngagement
from education_platform.modules.generation.models import (
    ContentGenerationOutlineNode,
    CurriculumGenerationJob,
    GenerationOutlineRevisionNode,
)
from education_platform.modules.generation.outline import is_outline_noise_heading
from education_platform.modules.generation.types import GenerationJobStatus
from education_platform.modules.materials.models import SourceMaterial

_IN_FLIGHT = (GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)


@dataclass(frozen=True, slots=True)
class SubtopicRef:
    id: UUID
    topic_id: UUID
    name: str
    sequence: int


@dataclass(frozen=True, slots=True)
class CleanupReport:
    noise_subtopics: int = 0
    merged: int = 0
    skipped_no_sibling: int = 0
    questions_moved: int = 0
    outcomes_moved: int = 0
    materials_moved: int = 0
    quizzes_retargeted: int = 0
    attempts_preserved: int = 0
    blocked_by_quiz: int = 0

    def render(self) -> str:
        lines = [
            f"noise_subtopics: {self.noise_subtopics}",
            f"merged: {self.merged}",
            f"skipped_no_sibling: {self.skipped_no_sibling}",
            f"questions_moved: {self.questions_moved}",
            f"outcomes_moved: {self.outcomes_moved}",
            f"materials_moved: {self.materials_moved}",
            f"quizzes_retargeted: {self.quizzes_retargeted}",
            f"attempts_preserved: {self.attempts_preserved}",
            f"blocked_by_quiz: {self.blocked_by_quiz}",
        ]
        return "\n".join(lines)


def merge_plan(rows: list[SubtopicRef]) -> tuple[list[tuple[UUID, UUID]], list[UUID]]:
    """``(noise id, concept id)`` pairs, and noise ids that have no concept sibling.

    A heading folds into the nearest earlier concept in the same unit, matching
    the outline writer. A heading that opens the unit folds into the next concept.
    """
    by_topic: dict[UUID, list[SubtopicRef]] = defaultdict(list)
    for row in rows:
        by_topic[row.topic_id].append(row)
    pairs: list[tuple[UUID, UUID]] = []
    skipped: list[UUID] = []
    for group in by_topic.values():
        ordered = sorted(group, key=lambda row: (row.sequence, str(row.id)))
        for index, row in enumerate(ordered):
            if not is_outline_noise_heading(row.name):
                continue
            earlier = [item for item in ordered[:index] if not is_outline_noise_heading(item.name)]
            later = [
                item for item in ordered[index + 1 :] if not is_outline_noise_heading(item.name)
            ]
            if earlier:
                pairs.append((row.id, earlier[-1].id))
            elif later:
                pairs.append((row.id, later[0].id))
            else:
                skipped.append(row.id)
    return pairs, skipped


def cleanup_noise_subtopics(session: Session, *, dry_run: bool) -> CleanupReport:
    """Merge heading subtopics. ``dry_run`` counts rows and writes nothing."""
    refs = [
        SubtopicRef(row.id, row.topic_id, row.name, row.sequence)
        for row in session.scalars(select(Subtopic).order_by(Subtopic.sequence, Subtopic.id))
    ]
    pairs, skipped = merge_plan(refs)
    report = CleanupReport(
        noise_subtopics=len(pairs) + len(skipped),
        skipped_no_sibling=len(skipped),
    )
    for noise_id, target_id in pairs:
        report = _merge_pair(session, report, noise_id, target_id, dry_run=dry_run)
    if not dry_run:
        session.flush()
    return report


def _merge_pair(
    session: Session,
    report: CleanupReport,
    noise_id: UUID,
    target_id: UUID,
    *,
    dry_run: bool,
) -> CleanupReport:
    questions = _count(session, Question.subtopic_id, noise_id)
    outcomes = _count(session, LearningOutcome.subtopic_id, noise_id)
    materials = _count(session, SourceMaterial.subtopic_id, noise_id)
    noise_quiz = session.scalar(
        select(CommonMasteryQuiz).where(CommonMasteryQuiz.subtopic_id == noise_id)
    )
    target_quiz = session.scalar(
        select(CommonMasteryQuiz).where(CommonMasteryQuiz.subtopic_id == target_id)
    )
    noise_attempts = _quiz_attempts(session, noise_quiz)
    blocked = noise_quiz is not None and target_quiz is not None
    if not blocked and _in_flight_job_conflict(session, noise_id, target_id):
        blocked = True
    retarget = noise_quiz is not None and not blocked
    preserved = noise_attempts if noise_quiz is not None else 0
    if dry_run:
        return _with_counts(
            report,
            questions=questions,
            outcomes=outcomes,
            materials=materials,
            retargeted=1 if retarget else 0,
            preserved=preserved,
            merged=0 if blocked else 1,
            blocked=1 if blocked else 0,
        )

    _move_questions(session, noise_id, target_id)
    _move_outcomes(session, noise_id, target_id)
    _move_materials(session, noise_id, target_id)
    _retarget_references(session, noise_id, target_id)
    if retarget and noise_quiz is not None:
        noise_quiz.subtopic_id = target_id
        session.flush()
    if blocked:
        return _with_counts(
            report,
            questions=questions,
            outcomes=outcomes,
            materials=materials,
            retargeted=0,
            preserved=preserved,
            merged=0,
            blocked=1,
        )
    _retarget_jobs(session, noise_id, target_id)
    noise = session.get(Subtopic, noise_id)
    if noise is not None:
        session.delete(noise)
        session.flush()
    return _with_counts(
        report,
        questions=questions,
        outcomes=outcomes,
        materials=materials,
        retargeted=1 if retarget else 0,
        preserved=preserved,
        merged=1,
        blocked=0,
    )


def _with_counts(
    report: CleanupReport,
    *,
    questions: int,
    outcomes: int,
    materials: int,
    retargeted: int,
    preserved: int,
    merged: int,
    blocked: int,
) -> CleanupReport:
    return CleanupReport(
        noise_subtopics=report.noise_subtopics,
        merged=report.merged + merged,
        skipped_no_sibling=report.skipped_no_sibling,
        questions_moved=report.questions_moved + questions,
        outcomes_moved=report.outcomes_moved + outcomes,
        materials_moved=report.materials_moved + materials,
        quizzes_retargeted=report.quizzes_retargeted + retargeted,
        attempts_preserved=report.attempts_preserved + preserved,
        blocked_by_quiz=report.blocked_by_quiz + blocked,
    )


def _count(session: Session, column: InstrumentedAttribute[UUID | None], subtopic_id: UUID) -> int:
    counted = session.scalar(select(func.count()).where(column == subtopic_id))
    return int(counted or 0)


def _quiz_attempts(session: Session, quiz: CommonMasteryQuiz | None) -> int:
    if quiz is None:
        return 0
    version_ids = select(QuizVersion.id).where(QuizVersion.quiz_id == quiz.id)
    return int(
        session.scalar(
            select(func.count())
            .select_from(QuizAttempt)
            .where(QuizAttempt.quiz_version_id.in_(version_ids))
        )
        or 0
    )


def _move_questions(session: Session, noise_id: UUID, target_id: UUID) -> None:
    session.execute(
        update(Question).where(Question.subtopic_id == noise_id).values(subtopic_id=target_id)
    )


def _move_outcomes(session: Session, noise_id: UUID, target_id: UUID) -> None:
    outcomes = list(
        session.scalars(select(LearningOutcome).where(LearningOutcome.subtopic_id == noise_id))
    )
    for outcome in outcomes:
        clash = session.scalar(
            select(LearningOutcome).where(
                LearningOutcome.subtopic_id == target_id,
                LearningOutcome.code == outcome.code,
            )
        )
        if clash is None:
            outcome.subtopic_id = target_id
            continue
        tags = list(
            session.scalars(
                select(QuestionOutcomeTag).where(
                    QuestionOutcomeTag.learning_outcome_id == outcome.id
                )
            )
        )
        for tag in tags:
            existing = session.scalar(
                select(QuestionOutcomeTag).where(
                    QuestionOutcomeTag.question_version_id == tag.question_version_id,
                    QuestionOutcomeTag.learning_outcome_id == clash.id,
                )
            )
            if existing is None:
                tag.learning_outcome_id = clash.id
            else:
                session.delete(tag)
        session.flush()
        session.delete(outcome)
    session.flush()


def _move_materials(session: Session, noise_id: UUID, target_id: UUID) -> None:
    materials = list(
        session.scalars(select(SourceMaterial).where(SourceMaterial.subtopic_id == noise_id))
    )
    taken = {
        slug
        for slug in session.scalars(
            select(SourceMaterial.slug).where(SourceMaterial.subtopic_id == target_id)
        )
        if slug is not None
    }
    for material in materials:
        slug = material.slug
        if slug in taken:
            suffix = 2
            while f"{slug}-{suffix}" in taken:
                suffix += 1
            slug = f"{slug}-{suffix}"[:100]
            material.slug = slug
        taken.add(slug)
        material.subtopic_id = target_id
    session.flush()


def _retarget_references(session: Session, noise_id: UUID, target_id: UUID) -> None:
    session.execute(
        update(SlideEngagement)
        .where(SlideEngagement.subtopic_id == noise_id)
        .values(subtopic_id=target_id)
    )
    session.execute(
        update(ContentGenerationOutlineNode)
        .where(ContentGenerationOutlineNode.matched_subtopic_id == noise_id)
        .values(matched_subtopic_id=target_id)
    )
    session.execute(
        update(ContentGenerationOutlineNode)
        .where(ContentGenerationOutlineNode.accepted_subtopic_id == noise_id)
        .values(accepted_subtopic_id=target_id)
    )
    session.execute(
        update(GenerationOutlineRevisionNode)
        .where(GenerationOutlineRevisionNode.matched_subtopic_id == noise_id)
        .values(matched_subtopic_id=target_id)
    )


def _in_flight_job_conflict(session: Session, noise_id: UUID, target_id: UUID) -> bool:
    noise_open = session.scalar(
        select(CurriculumGenerationJob.id).where(
            CurriculumGenerationJob.subtopic_id == noise_id,
            CurriculumGenerationJob.status.in_(_IN_FLIGHT),
        )
    )
    if noise_open is None:
        return False
    target_open = session.scalar(
        select(CurriculumGenerationJob.id).where(
            CurriculumGenerationJob.subtopic_id == target_id,
            CurriculumGenerationJob.status.in_(_IN_FLIGHT),
        )
    )
    return target_open is not None


def _retarget_jobs(session: Session, noise_id: UUID, target_id: UUID) -> None:
    session.execute(
        update(CurriculumGenerationJob)
        .where(CurriculumGenerationJob.subtopic_id == noise_id)
        .values(subtopic_id=target_id)
    )
