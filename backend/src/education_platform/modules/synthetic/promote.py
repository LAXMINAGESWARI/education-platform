"""Promote synthetic '{Subject} Core' topics into real topics.

Old subtopics become topics. Their lesson and subtopic quiz move with them, so
material-version ids and quiz-attempt ids stay put. Diagnostic subtopics are
created only to tag those questions. The Core overview lesson is deleted, then
the Core topic, when nothing else still references it.

The pass is idempotent. A second run finds no ``*-core`` topics and returns.
A Core topic that still has a generation run, a PDF, or any other unexpected
reference is left in place and logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Select

from education_platform.core.config import get_settings
from education_platform.db.session import create_general_sync_engine
from education_platform.db.url import to_sync_url
from education_platform.modules.academics.models import (
    GradeSubjectOffering,
    LearningOutcome,
    Subject,
    Subtopic,
    Topic,
)
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    Question,
    QuizMaterialBinding,
    QuizScope,
    QuizVersion,
    StudentSubtopicGoal,
)
from education_platform.modules.engagement.models import SlideEngagement
from education_platform.modules.generation.models import (
    ContentGenerationOutlineNode,
    ContentGenerationRun,
    CurriculumGenerationJob,
    GenerationOutlineRevisionNode,
)
from education_platform.modules.materials.models import (
    SourceChunk,
    SourceMaterial,
    SourceMaterialVersion,
    StudentMaterialProgress,
)
from education_platform.modules.rag.models import IngestJob
from education_platform.modules.synthetic.curriculum import diagnostic_names, slugify
from education_platform.modules.synthetic.quizzes import retag_questions

logger = logging.getLogger(__name__)


@dataclass
class PromoteResult:
    promoted_topics: int = 0
    deleted_core_topics: int = 0
    skipped: list[str] = field(default_factory=list)


def promote_core_topics(session: Session) -> PromoteResult:
    """Move synthetic Core children up to topics, then delete empty Core rows."""
    result = PromoteResult()
    cores = list(session.scalars(_core_topic_query()))
    for core in cores:
        subject_name = _subject_name(session, core)
        if subject_name is None or core.name != f"{subject_name} Core":
            continue
        if not core.slug.endswith("-core"):
            continue
        _promote_one_core(session, core, subject_name, result)
    return result


def _core_topic_query() -> Select[tuple[Topic]]:
    return select(Topic).where(Topic.slug.endswith("-core")).order_by(Topic.sequence, Topic.slug)


def _subject_name(session: Session, topic: Topic) -> str | None:
    return session.scalar(
        select(Subject.name)
        .join(GradeSubjectOffering, GradeSubjectOffering.subject_id == Subject.id)
        .where(GradeSubjectOffering.id == topic.grade_subject_offering_id)
    )


def _promote_one_core(
    session: Session,
    core: Topic,
    subject_name: str,
    result: PromoteResult,
) -> None:
    children = list(
        session.scalars(
            select(Subtopic)
            .where(Subtopic.topic_id == core.id)
            .order_by(Subtopic.sequence, Subtopic.slug)
        )
    )
    _park_core_sequence(session, core)
    for index, child in enumerate(children, start=1):
        if _promote_child(session, core, child, subject_name, index):
            result.promoted_topics += 1
        else:
            result.skipped.append(f"{core.name} ({core.id}): left subtopic {child.name} in place")
    session.flush()
    if not _delete_core_if_clear(session, core, result):
        return
    result.deleted_core_topics += 1


def _park_core_sequence(session: Session, core: Topic) -> None:
    current_max = session.scalar(
        select(func.max(Topic.sequence)).where(
            Topic.grade_subject_offering_id == core.grade_subject_offering_id
        )
    )
    parked = (current_max or 0) + 1000
    if core.sequence == parked:
        return
    core.sequence = parked
    session.flush()


def _promote_child(
    session: Session,
    core: Topic,
    child: Subtopic,
    subject_name: str,
    preferred_sequence: int,
) -> bool:
    topic = _ensure_topic(session, core, child, preferred_sequence)
    diagnostics = _ensure_diagnostics(session, topic, subject_name)
    if not diagnostics:
        _skip_log(f"{core.name}: {child.name} has no diagnostic subtopics")
        return False
    _reparent_materials(session, child, topic)
    _retarget_quiz(session, child, topic)
    question_ids = list(
        session.scalars(select(Question.id).where(Question.subtopic_id == child.id))
    )
    retag_questions(session, question_ids, diagnostics)
    _reparent_loose_refs(session, child, diagnostics[0])
    session.flush()
    blockers = _subtopic_blockers(session, child.id)
    if blockers:
        _skip_log(f"{core.name}: subtopic {child.name} still referenced by {', '.join(blockers)}")
        return False
    session.delete(child)
    session.flush()
    return True


def _ensure_topic(session: Session, core: Topic, child: Subtopic, preferred: int) -> Topic:
    slug = slugify(child.name)
    existing = session.scalar(
        select(Topic).where(
            Topic.grade_subject_offering_id == core.grade_subject_offering_id,
            Topic.slug == slug,
        )
    )
    if existing is not None:
        return existing
    sequence = _free_sequence(session, core, preferred)
    topic = Topic(
        grade_subject_offering_id=core.grade_subject_offering_id,
        name=child.name,
        slug=slug,
        sequence=sequence,
    )
    session.add(topic)
    session.flush()
    return topic


def _free_sequence(session: Session, core: Topic, preferred: int) -> int:
    taken = set(
        session.scalars(
            select(Topic.sequence).where(
                Topic.grade_subject_offering_id == core.grade_subject_offering_id,
                Topic.id != core.id,
            )
        )
    )
    sequence = preferred
    while sequence in taken:
        sequence += 1
    return sequence


def _ensure_diagnostics(session: Session, topic: Topic, subject_name: str) -> list[Subtopic]:
    existing = list(
        session.scalars(
            select(Subtopic).where(Subtopic.topic_id == topic.id).order_by(Subtopic.sequence)
        )
    )
    if existing:
        return existing
    made: list[Subtopic] = []
    for sequence, name in enumerate(diagnostic_names(subject_name, topic.name), start=1):
        subtopic = Subtopic(
            topic_id=topic.id,
            name=name,
            slug=slugify(name, fallback=f"idea-{sequence}"),
            sequence=sequence,
        )
        session.add(subtopic)
        session.flush()
        session.add(
            LearningOutcome(
                subtopic_id=subtopic.id,
                code="LO1",
                statement=f"Explain {name} in {topic.name}.",
                sequence=1,
            )
        )
        made.append(subtopic)
    session.flush()
    return made


def _reparent_materials(session: Session, child: Subtopic, topic: Topic) -> None:
    materials = list(
        session.scalars(select(SourceMaterial).where(SourceMaterial.subtopic_id == child.id))
    )
    for material in materials:
        clash = session.scalar(
            select(SourceMaterial.id).where(
                SourceMaterial.topic_id == topic.id,
                SourceMaterial.slug == material.slug,
            )
        )
        if clash is not None:
            _skip_log(f"{topic.name}: material slug {material.slug} already exists on the topic")
            continue
        session.execute(
            update(SourceMaterial)
            .where(SourceMaterial.id == material.id)
            .values(topic_id=topic.id, subtopic_id=None)
        )


def _retarget_quiz(session: Session, child: Subtopic, topic: Topic) -> None:
    quiz = session.scalar(
        select(CommonMasteryQuiz).where(CommonMasteryQuiz.subtopic_id == child.id)
    )
    if quiz is None or quiz.quiz_scope is not QuizScope.SUBTOPIC_MASTERY:
        return
    existing = session.scalar(
        select(CommonMasteryQuiz.id).where(
            CommonMasteryQuiz.topic_id == topic.id,
            CommonMasteryQuiz.quiz_scope == QuizScope.TOPIC_MASTERY,
        )
    )
    if existing is not None:
        _skip_log(f"{topic.name}: topic quiz already exists; left the subtopic quiz")
        return
    session.execute(
        update(CommonMasteryQuiz)
        .where(CommonMasteryQuiz.id == quiz.id)
        .values(
            quiz_scope=QuizScope.TOPIC_MASTERY,
            topic_id=topic.id,
            subtopic_id=None,
            title=f"{topic.name} topic mastery",
        )
    )
    session.execute(
        update(QuizVersion).where(QuizVersion.quiz_id == quiz.id).values(max_attempts=None)
    )


def _reparent_loose_refs(session: Session, child: Subtopic, destination: Subtopic) -> None:
    _move_outcomes(session, child.id, destination.id)
    session.execute(
        update(SlideEngagement)
        .where(SlideEngagement.subtopic_id == child.id)
        .values(subtopic_id=destination.id)
    )
    session.execute(
        update(ContentGenerationOutlineNode)
        .where(ContentGenerationOutlineNode.matched_subtopic_id == child.id)
        .values(matched_subtopic_id=destination.id)
    )
    session.execute(
        update(ContentGenerationOutlineNode)
        .where(ContentGenerationOutlineNode.accepted_subtopic_id == child.id)
        .values(accepted_subtopic_id=destination.id)
    )
    session.execute(
        update(GenerationOutlineRevisionNode)
        .where(GenerationOutlineRevisionNode.matched_subtopic_id == child.id)
        .values(matched_subtopic_id=destination.id)
    )
    session.execute(
        update(CurriculumGenerationJob)
        .where(CurriculumGenerationJob.subtopic_id == child.id)
        .values(subtopic_id=destination.id)
    )


def _move_outcomes(session: Session, source_id: UUID, destination_id: UUID) -> None:
    taken = set(
        session.scalars(
            select(LearningOutcome.code).where(LearningOutcome.subtopic_id == destination_id)
        )
    )
    outcomes = list(
        session.scalars(select(LearningOutcome).where(LearningOutcome.subtopic_id == source_id))
    )
    for outcome in outcomes:
        code = outcome.code
        if code in taken:
            code = f"{code}-prior"
            while code in taken:
                code = f"{code}-x"
        outcome.subtopic_id = destination_id
        outcome.code = code
        taken.add(code)


def _count(session: Session, statement: Select[tuple[int]]) -> int:
    return int(session.scalar(statement) or 0)


def _subtopic_blockers(session: Session, subtopic_id: UUID) -> list[str]:
    checks: list[tuple[str, Select[tuple[int]]]] = [
        (
            "questions",
            select(func.count()).select_from(Question).where(Question.subtopic_id == subtopic_id),
        ),
        (
            "materials",
            select(func.count())
            .select_from(SourceMaterial)
            .where(SourceMaterial.subtopic_id == subtopic_id),
        ),
        (
            "quizzes",
            select(func.count())
            .select_from(CommonMasteryQuiz)
            .where(CommonMasteryQuiz.subtopic_id == subtopic_id),
        ),
        (
            "outcomes",
            select(func.count())
            .select_from(LearningOutcome)
            .where(LearningOutcome.subtopic_id == subtopic_id),
        ),
        (
            "engagement",
            select(func.count())
            .select_from(SlideEngagement)
            .where(SlideEngagement.subtopic_id == subtopic_id),
        ),
        (
            "outline matches",
            select(func.count())
            .select_from(ContentGenerationOutlineNode)
            .where(
                (ContentGenerationOutlineNode.matched_subtopic_id == subtopic_id)
                | (ContentGenerationOutlineNode.accepted_subtopic_id == subtopic_id)
            ),
        ),
        (
            "outline revisions",
            select(func.count())
            .select_from(GenerationOutlineRevisionNode)
            .where(GenerationOutlineRevisionNode.matched_subtopic_id == subtopic_id),
        ),
        (
            "curriculum jobs",
            select(func.count())
            .select_from(CurriculumGenerationJob)
            .where(CurriculumGenerationJob.subtopic_id == subtopic_id),
        ),
    ]
    return [label for label, query in checks if _count(session, query) > 0]


def _delete_core_if_clear(session: Session, core: Topic, result: PromoteResult) -> bool:
    reason = _core_delete_blockers(session, core)
    if reason:
        message = f"{core.name} ({core.id}): skipped delete ({reason})"
        result.skipped.append(message)
        _skip_log(message)
        return False
    version_ids = _overview_version_ids(session, core.id)
    if version_ids:
        session.execute(
            delete(StudentMaterialProgress).where(
                StudentMaterialProgress.source_material_version_id.in_(version_ids)
            )
        )
        session.execute(
            delete(QuizMaterialBinding).where(
                QuizMaterialBinding.source_material_version_id.in_(version_ids)
            )
        )
        session.execute(
            delete(SourceMaterialVersion).where(SourceMaterialVersion.id.in_(version_ids))
        )
        session.execute(
            delete(SourceMaterial).where(
                SourceMaterial.topic_id == core.id,
                SourceMaterial.slug == "lesson",
            )
        )
    session.delete(core)
    session.flush()
    return True


def _overview_version_ids(session: Session, topic_id: UUID) -> list[UUID]:
    return list(
        session.scalars(
            select(SourceMaterialVersion.id)
            .join(SourceMaterial, SourceMaterial.id == SourceMaterialVersion.source_material_id)
            .where(SourceMaterial.topic_id == topic_id, SourceMaterial.slug == "lesson")
        )
    )


def _core_delete_blockers(session: Session, core: Topic) -> str | None:
    if session.scalar(
        select(func.count()).select_from(Subtopic).where(Subtopic.topic_id == core.id)
    ):
        return "subtopics remain"
    if session.scalar(
        select(func.count())
        .select_from(StudentSubtopicGoal)
        .where(StudentSubtopicGoal.topic_id == core.id)
    ):
        return "a student goal targets the topic"
    if session.scalar(
        select(func.count())
        .select_from(CommonMasteryQuiz)
        .where(CommonMasteryQuiz.topic_id == core.id)
    ):
        return "a quiz still targets the topic"
    if session.scalar(
        select(func.count())
        .select_from(ContentGenerationRun)
        .where(ContentGenerationRun.topic_id == core.id)
    ):
        return "a generation run targets the topic"
    materials = list(
        session.scalars(select(SourceMaterial).where(SourceMaterial.topic_id == core.id))
    )
    if any(material.slug != "lesson" for material in materials):
        return "non-lesson material remains"
    version_ids = _overview_version_ids(session, core.id)
    if not version_ids:
        return None
    if session.scalar(
        select(func.count())
        .select_from(SourceMaterialVersion)
        .where(
            SourceMaterialVersion.id.in_(version_ids),
            SourceMaterialVersion.blob_object_key.is_not(None),
        )
    ):
        return "overview lesson has a file blob"
    if session.scalar(
        select(func.count())
        .select_from(SourceChunk)
        .where(SourceChunk.source_material_version_id.in_(version_ids))
    ):
        return "overview lesson has indexed chunks"
    if session.scalar(
        select(func.count())
        .select_from(IngestJob)
        .where(IngestJob.source_material_version_id.in_(version_ids))
    ):
        return "overview lesson has an ingest job"
    if session.scalar(
        select(func.count())
        .select_from(ContentGenerationRun)
        .where(
            ContentGenerationRun.intake_source_material_version_id.in_(version_ids)
            | ContentGenerationRun.published_lesson_version_id.in_(version_ids)
        )
    ):
        return "a generation run references the overview lesson"
    if session.scalar(
        select(func.count())
        .select_from(CurriculumGenerationJob)
        .where(CurriculumGenerationJob.source_material_version_id.in_(version_ids))
    ):
        return "a curriculum job references the overview lesson"
    return None


def _skip_log(message: str) -> None:
    logger.warning("core topic promotion: %s", message)


def main() -> None:
    settings = get_settings()
    engine = create_general_sync_engine(
        to_sync_url(settings.database_url),
        pool_pre_ping=True,
    )
    with Session(engine) as session:
        result = promote_core_topics(session)
        session.commit()
    engine.dispose()
    print(f"Promoted topics: {result.promoted_topics}")
    print(f"Deleted core topics: {result.deleted_core_topics}")
    for line in result.skipped:
        print(f"Skipped: {line}")


if __name__ == "__main__":
    main()
