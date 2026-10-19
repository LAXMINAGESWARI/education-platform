"""Subject → topic → subtopic feedback, from the same topic-quiz attempts as the directory.

Aggregation
-----------
A topic's best score is the highest ``score_percent`` among that student's
visible topic-quiz attempts (not abandoned, not held, score present). That is
the same number the learning directory shows as ``best_score_percent``.

A subject's score is the mean of its *attempted* topics' best scores. A topic
with no visible score is "not attempted" and is left out of the mean. It is
not treated as 0.

The all-subjects score is the mean of those subject scores. A subject with no
attempted topic is left out of that mean.

Trend compares the latest visible scored attempt with the previous one, ordered
by ``attempt_number``, per topic. One scored attempt has no trend. A subject's
trend delta is the mean of its topics' deltas, and only topics with two or more
scored attempts contribute. No such topic means the subject has no trend.

Subtopic strength uses correct/answered counts on the questions tagged with
that subtopic, across the student's visible attempts of the topic quiz.
Filler headings (exercises, solutions, "X's method", summaries) are not their
own rows; those questions stay inside the topic score. Display names go through
``normalize_outline_title``, the same helper the outline writer uses.

Bands, using the mastery pass mark of 70:
- strong: at least 70% correct
- okay: at least 50% and below 70%
- needs work: below 50%
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.errors import DomainError
from education_platform.modules.academics.models import (
    AcademicPeriod,
    Grade,
    GradeSubjectOffering,
    PeriodGrade,
    Subject,
    Subtopic,
    Topic,
)
from education_platform.modules.assessments.models import (
    AttemptAnswer,
    CommonMasteryQuiz,
    Question,
    QuestionVersion,
    QuizAttempt,
    QuizAttemptStatus,
    QuizScope,
    QuizVersion,
    SubjectFeedbackSummary,
)
from education_platform.modules.assessments.schemas import (
    FeedbackOverview,
    FeedbackSubjectCard,
    FeedbackSubjectDetail,
    FeedbackSubtopicStrength,
    FeedbackTopicDetail,
    FeedbackTopicRef,
    FeedbackTopicScore,
)
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.generation.outline import (
    is_outline_noise_heading,
    normalize_outline_title,
)
from education_platform.modules.materials.models import (
    SourceMaterial,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
)

PASS_PERCENT = Decimal("70")
OKAY_PERCENT = Decimal("50")
_CENT = Decimal("0.01")
TrendName = Literal["improving", "flat", "declining"]
BadgeName = Literal["not_attempted", "keep_going", "passed"]
BandName = Literal["strong", "okay", "needs_work"]


@dataclass(frozen=True, slots=True)
class ScoredAttempt:
    attempt_number: int
    score_percent: Decimal


@dataclass(frozen=True, slots=True)
class TopicRollup:
    best_score_percent: Decimal | None
    latest_score_percent: Decimal | None
    attempt_count: int
    trend: TrendName | None
    trend_delta: Decimal | None
    badge: BadgeName


@dataclass(frozen=True, slots=True)
class SubjectRollup:
    score_percent: Decimal | None
    quizzes_taken: int
    trend: TrendName | None
    trend_delta: Decimal | None
    strongest: tuple[UUID, str, Decimal] | None
    weakest: tuple[UUID, str, Decimal] | None
    needing_attention: tuple[str, ...]


def feedback_concept_label(name: str) -> str | None:
    """Student-facing concept name, or None when the heading is filler.

    Reuses the outline writer's noise check and title normalizer so a stored
    heading such as "9.4.1 Cuboid" becomes "Cuboid", and "EXERCISE 9.3" or
    "Ridhima's method" is folded into the topic instead of listed on its own.
    """
    if is_outline_noise_heading(name):
        return None
    cleaned = normalize_outline_title(name)
    if not cleaned or is_outline_noise_heading(cleaned):
        return None
    return cleaned


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, Decimal("0")) / Decimal(len(values))).quantize(_CENT)


def _trend(delta: Decimal | None) -> TrendName | None:
    if delta is None:
        return None
    if delta > 0:
        return "improving"
    if delta < 0:
        return "declining"
    return "flat"


def rollup_topic(attempts: list[ScoredAttempt]) -> TopicRollup:
    """Best, latest, and trend for one topic quiz. Empty means not attempted."""
    if not attempts:
        return TopicRollup(None, None, 0, None, None, "not_attempted")
    ordered = sorted(attempts, key=lambda attempt: attempt.attempt_number)
    best = max(attempt.score_percent for attempt in ordered)
    latest = ordered[-1].score_percent
    delta: Decimal | None = None
    if len(ordered) >= 2:
        delta = (latest - ordered[-2].score_percent).quantize(_CENT)
    badge: BadgeName = "passed" if best >= PASS_PERCENT else "keep_going"
    return TopicRollup(best, latest, len(ordered), _trend(delta), delta, badge)


def rollup_subject(topics: list[tuple[UUID, str, TopicRollup]]) -> SubjectRollup:
    """Mean of attempted topics. Unattempted topics do not pull the mean to 0."""
    attempted = [
        (topic_id, name, rollup)
        for topic_id, name, rollup in topics
        if rollup.best_score_percent is not None
    ]
    deltas = [rollup.trend_delta for _, _, rollup in attempted if rollup.trend_delta is not None]
    trend_delta = _mean(deltas)
    strongest: tuple[UUID, str, Decimal] | None = None
    weakest: tuple[UUID, str, Decimal] | None = None
    needing: list[str] = []
    for topic_id, name, rollup in attempted:
        score = rollup.best_score_percent
        assert score is not None
        if strongest is None or score > strongest[2]:
            strongest = (topic_id, name, score)
        if weakest is None or score < weakest[2]:
            weakest = (topic_id, name, score)
        if score < PASS_PERCENT:
            needing.append(name)
    if len(attempted) < 2:
        weakest = None
    bests = [
        rollup.best_score_percent
        for _, _, rollup in attempted
        if rollup.best_score_percent is not None
    ]
    return SubjectRollup(
        score_percent=_mean(bests),
        quizzes_taken=sum(rollup.attempt_count for _, _, rollup in topics),
        trend=_trend(trend_delta),
        trend_delta=trend_delta,
        strongest=strongest,
        weakest=weakest,
        needing_attention=tuple(needing),
    )


def subtopic_band(correct: int, answered: int) -> BandName:
    if answered <= 0:
        raise ValueError("answered must be positive")
    percent = (Decimal(correct) / Decimal(answered)) * Decimal("100")
    if percent >= PASS_PERCENT:
        return "strong"
    if percent >= OKAY_PERCENT:
        return "okay"
    return "needs_work"


def group_subtopic_strengths(
    rows: list[tuple[str, int, int, int]],
) -> list[FeedbackSubtopicStrength]:
    """Merge filler headings away and combine rows that clean to the same name.

    Each row is (raw name, sequence, correct, answered).
    """
    buckets: dict[str, list[int]] = {}
    order: dict[str, int] = {}
    for name, sequence, correct, answered in rows:
        label = feedback_concept_label(name)
        if label is None or answered <= 0:
            continue
        bucket = buckets.setdefault(label, [0, 0])
        bucket[0] += correct
        bucket[1] += answered
        order[label] = min(sequence, order.get(label, sequence))
    strengths: list[FeedbackSubtopicStrength] = []
    for label, _sequence in sorted(order.items(), key=lambda item: (item[1], item[0])):
        correct, answered = buckets[label]
        percent = ((Decimal(correct) / Decimal(answered)) * Decimal("100")).quantize(_CENT)
        strengths.append(
            FeedbackSubtopicStrength(
                name=label,
                correct_count=correct,
                answered_count=answered,
                percent=percent,
                band=subtopic_band(correct, answered),
            )
        )
    return strengths


def topic_summary(topic_name: str, strengths: list[FeedbackSubtopicStrength]) -> str:
    strong = [row.name for row in strengths if row.band == "strong"]
    work = [row.name for row in strengths if row.band == "needs_work"]
    parts: list[str] = []
    if strong:
        parts.append(f"Strong on {', '.join(strong[:3])}.")
    if work:
        parts.append(f"Needs work on {', '.join(work[:3])}.")
    if not parts:
        if strengths:
            parts.append(
                f"{topic_name} is in between. Another attempt will show which ideas are sticking."
            )
        else:
            parts.append(
                f"Take the {topic_name} quiz to see which ideas are strong and which need work."
            )
    return " ".join(parts)


def subject_template(subject_name: str, strongest: str | None, needing: tuple[str, ...]) -> str:
    parts: list[str] = []
    if strongest:
        parts.append(f"You're doing well in {strongest}.")
    if needing:
        parts.append(f"Focus next on {', '.join(needing[:3])}.")
    elif strongest:
        parts.append(
            "Nothing you have tried is below the pass mark. "
            "Keep going on topics you have not attempted."
        )
    else:
        parts.append(f"Take a topic quiz in {subject_name} to see where you stand.")
    return " ".join(parts)


@dataclass(frozen=True, slots=True)
class _OfferingSubject:
    offering_id: UUID
    subject_id: UUID
    subject_name: str
    grade_name: str
    grade_sort: int
    subject_code: str


@dataclass(frozen=True, slots=True)
class _TopicRow:
    id: UUID
    offering_id: UUID
    name: str
    sequence: int


def _require_student(scope: Scope) -> UUID:
    if scope.self_student_id is None:
        raise DomainError("Student enrollment required", status_code=403)
    return scope.self_student_id


async def _offerings(session: AsyncSession, scope: Scope) -> list[_OfferingSubject]:
    query = (
        select(GradeSubjectOffering, Subject, Grade)
        .join(Subject, Subject.id == GradeSubjectOffering.subject_id)
        .join(PeriodGrade, PeriodGrade.id == GradeSubjectOffering.period_grade_id)
        .join(Grade, Grade.id == PeriodGrade.grade_id)
        .join(AcademicPeriod, AcademicPeriod.id == PeriodGrade.academic_period_id)
        .where(Subject.institution_id == scope.institution_id)
        .order_by(Grade.sort_order, Subject.code)
    )
    if not scope.unrestricted:
        if not scope.offering_ids:
            return []
        query = query.where(GradeSubjectOffering.id.in_(scope.offering_ids))
    rows = (await session.execute(query)).all()
    return [
        _OfferingSubject(
            offering.id,
            subject.id,
            subject.name,
            grade.name,
            grade.sort_order,
            subject.code,
        )
        for offering, subject, grade in rows
    ]


async def _topics(session: AsyncSession, offering_ids: list[UUID]) -> list[_TopicRow]:
    if not offering_ids:
        return []
    rows = (
        await session.scalars(
            select(Topic)
            .where(Topic.grade_subject_offering_id.in_(offering_ids))
            .order_by(Topic.sequence, Topic.slug)
        )
    ).all()
    return [
        _TopicRow(topic.id, topic.grade_subject_offering_id, topic.name, topic.sequence)
        for topic in rows
    ]


async def _scored_attempts(
    session: AsyncSession, student_id: UUID, topic_ids: list[UUID]
) -> dict[UUID, list[ScoredAttempt]]:
    if not topic_ids:
        return {}
    rows = (
        await session.execute(
            select(
                CommonMasteryQuiz.topic_id,
                QuizAttempt.attempt_number,
                QuizAttempt.score_percent,
                QuizAttempt.status,
            )
            .join(QuizVersion, QuizVersion.id == QuizAttempt.quiz_version_id)
            .join(CommonMasteryQuiz, CommonMasteryQuiz.id == QuizVersion.quiz_id)
            .where(
                QuizAttempt.student_id == student_id,
                CommonMasteryQuiz.quiz_scope == QuizScope.TOPIC_MASTERY,
                CommonMasteryQuiz.topic_id.in_(topic_ids),
                QuizAttempt.status != QuizAttemptStatus.ABANDONED,
            )
        )
    ).all()
    grouped: dict[UUID, list[ScoredAttempt]] = defaultdict(list)
    for topic_id, attempt_number, score, status in rows:
        # Held scores stay on the server, matching directory ``results_visible``.
        if topic_id is None or score is None or status == QuizAttemptStatus.HELD:
            continue
        grouped[topic_id].append(ScoredAttempt(int(attempt_number), Decimal(score)))
    return grouped


async def _quiz_ids(session: AsyncSession, topic_ids: list[UUID]) -> dict[UUID, UUID]:
    if not topic_ids:
        return {}
    rows = (
        await session.execute(
            select(CommonMasteryQuiz.topic_id, CommonMasteryQuiz.id).where(
                CommonMasteryQuiz.quiz_scope == QuizScope.TOPIC_MASTERY,
                CommonMasteryQuiz.topic_id.in_(topic_ids),
            )
        )
    ).all()
    return {topic_id: quiz_id for topic_id, quiz_id in rows if topic_id is not None}


async def _lesson_topic_ids(session: AsyncSession, topic_ids: list[UUID]) -> set[UUID]:
    if not topic_ids:
        return set()
    rows = (
        await session.scalars(
            select(SourceMaterial.topic_id)
            .join(
                SourceMaterialVersion, SourceMaterialVersion.source_material_id == SourceMaterial.id
            )
            .where(
                SourceMaterial.topic_id.in_(topic_ids),
                SourceMaterial.slug == "lesson",
                SourceMaterialVersion.lifecycle_status == SourceMaterialVersionStatus.PUBLISHED,
            )
        )
    ).all()
    return {topic_id for topic_id in rows if topic_id is not None}


async def _stored_summaries(
    session: AsyncSession, student_id: UUID, subject_ids: list[UUID]
) -> dict[UUID, str]:
    if not subject_ids:
        return {}
    rows = (
        await session.scalars(
            select(SubjectFeedbackSummary).where(
                SubjectFeedbackSummary.student_id == student_id,
                SubjectFeedbackSummary.subject_id.in_(subject_ids),
            )
        )
    ).all()
    return {row.subject_id: row.summary for row in rows if row.summary}


def _topic_score(
    topic: _TopicRow, rollup: TopicRollup, quiz_id: UUID | None, has_lesson: bool
) -> FeedbackTopicScore:
    return FeedbackTopicScore(
        topic_id=topic.id,
        topic_name=topic.name,
        sequence=topic.sequence,
        best_score_percent=rollup.best_score_percent,
        latest_score_percent=rollup.latest_score_percent,
        attempt_count=rollup.attempt_count,
        trend=rollup.trend,
        trend_delta=rollup.trend_delta,
        badge=rollup.badge,
        quiz_id=quiz_id,
        has_lesson=has_lesson,
    )


def _ref(picked: tuple[UUID, str, Decimal] | None) -> FeedbackTopicRef | None:
    if picked is None:
        return None
    topic_id, name, score = picked
    return FeedbackTopicRef(topic_id=topic_id, topic_name=name, best_score_percent=score)


async def _bundle(
    session: AsyncSession, scope: Scope
) -> tuple[
    UUID,
    list[_OfferingSubject],
    list[_TopicRow],
    dict[UUID, TopicRollup],
    dict[UUID, UUID],
    set[UUID],
]:
    student_id = _require_student(scope)
    offerings = await _offerings(session, scope)
    topics = await _topics(session, [row.offering_id for row in offerings])
    topic_ids = [topic.id for topic in topics]
    attempts = await _scored_attempts(session, student_id, topic_ids)
    rollups = {topic.id: rollup_topic(attempts.get(topic.id, [])) for topic in topics}
    quiz_ids = await _quiz_ids(session, topic_ids)
    lessons = await _lesson_topic_ids(session, topic_ids)
    return student_id, offerings, topics, rollups, quiz_ids, lessons


def _subject_topics(offering: _OfferingSubject, topics: list[_TopicRow]) -> list[_TopicRow]:
    return [topic for topic in topics if topic.offering_id == offering.offering_id]


def _card(
    offering: _OfferingSubject, topics: list[_TopicRow], rollups: dict[UUID, TopicRollup]
) -> FeedbackSubjectCard:
    subject = rollup_subject([(topic.id, topic.name, rollups[topic.id]) for topic in topics])
    return FeedbackSubjectCard(
        subject_id=offering.subject_id,
        subject_name=offering.subject_name,
        grade_name=offering.grade_name,
        score_percent=subject.score_percent,
        quizzes_taken=subject.quizzes_taken,
        trend=subject.trend,
        trend_delta=subject.trend_delta,
        strongest_topic=_ref(subject.strongest),
        weakest_topic=_ref(subject.weakest),
    )


async def get_overview(session: AsyncSession, scope: Scope) -> FeedbackOverview:
    _student_id, offerings, topics, rollups, _quiz_ids, _lessons = await _bundle(session, scope)
    cards = [_card(offering, _subject_topics(offering, topics), rollups) for offering in offerings]
    return FeedbackOverview(
        score_percent=_mean(
            [card.score_percent for card in cards if card.score_percent is not None]
        ),
        subjects=cards,
    )


async def get_subject(
    session: AsyncSession, scope: Scope, subject_id: UUID
) -> FeedbackSubjectDetail:
    student_id, offerings, topics, rollups, quiz_ids, lessons = await _bundle(session, scope)
    matched = [offering for offering in offerings if offering.subject_id == subject_id]
    if not matched:
        raise DomainError("Subject not found", status_code=404)
    offering = matched[0]
    subject_topics = [topic for row in matched for topic in _subject_topics(row, topics)]
    subject_topics.sort(key=lambda topic: (topic.sequence, topic.name))
    subject = rollup_subject(
        [(topic.id, topic.name, rollups[topic.id]) for topic in subject_topics]
    )
    stored = await _stored_summaries(session, student_id, [subject_id])
    summary = stored.get(subject_id)
    status: Literal["ready", "fallback"] = "ready" if summary else "fallback"
    if not summary:
        strongest_name = subject.strongest[1] if subject.strongest else None
        summary = subject_template(offering.subject_name, strongest_name, subject.needing_attention)
    return FeedbackSubjectDetail(
        subject_id=offering.subject_id,
        subject_name=offering.subject_name,
        grade_name=offering.grade_name,
        score_percent=subject.score_percent,
        quizzes_taken=subject.quizzes_taken,
        trend=subject.trend,
        trend_delta=subject.trend_delta,
        topics_needing_attention=list(subject.needing_attention),
        summary=summary,
        summary_status=status,
        topics=[
            _topic_score(topic, rollups[topic.id], quiz_ids.get(topic.id), topic.id in lessons)
            for topic in subject_topics
        ],
    )


async def _subtopic_rows(
    session: AsyncSession, student_id: UUID, topic_id: UUID
) -> list[tuple[str, int, int, int]]:
    rows = (
        await session.execute(
            select(
                Subtopic.name,
                Subtopic.sequence,
                AttemptAnswer.is_correct,
            )
            .join(Question, Question.subtopic_id == Subtopic.id)
            .join(QuestionVersion, QuestionVersion.question_id == Question.id)
            .join(AttemptAnswer, AttemptAnswer.question_version_id == QuestionVersion.id)
            .join(QuizAttempt, QuizAttempt.id == AttemptAnswer.attempt_id)
            .join(QuizVersion, QuizVersion.id == QuizAttempt.quiz_version_id)
            .join(CommonMasteryQuiz, CommonMasteryQuiz.id == QuizVersion.quiz_id)
            .where(
                Subtopic.topic_id == topic_id,
                CommonMasteryQuiz.topic_id == topic_id,
                CommonMasteryQuiz.quiz_scope == QuizScope.TOPIC_MASTERY,
                QuizAttempt.student_id == student_id,
                QuizAttempt.status != QuizAttemptStatus.ABANDONED,
                QuizAttempt.status != QuizAttemptStatus.HELD,
                AttemptAnswer.is_correct.is_not(None),
            )
        )
    ).all()
    counts: dict[tuple[str, int], list[int]] = {}
    for name, sequence, correct in rows:
        bucket = counts.setdefault((name, int(sequence)), [0, 0])
        bucket[1] += 1
        if correct:
            bucket[0] += 1
    return [
        (name, sequence, correct, answered)
        for (name, sequence), (correct, answered) in counts.items()
    ]


async def get_topic(session: AsyncSession, scope: Scope, topic_id: UUID) -> FeedbackTopicDetail:
    student_id, offerings, topics, rollups, quiz_ids, lessons = await _bundle(session, scope)
    topic = next((row for row in topics if row.id == topic_id), None)
    if topic is None:
        raise DomainError("Topic not found", status_code=404)
    offering = next(row for row in offerings if row.offering_id == topic.offering_id)
    rollup = rollups[topic.id]
    strengths = group_subtopic_strengths(await _subtopic_rows(session, student_id, topic_id))
    return FeedbackTopicDetail(
        subject_id=offering.subject_id,
        subject_name=offering.subject_name,
        topic_id=topic.id,
        topic_name=topic.name,
        best_score_percent=rollup.best_score_percent,
        latest_score_percent=rollup.latest_score_percent,
        attempt_count=rollup.attempt_count,
        trend=rollup.trend,
        trend_delta=rollup.trend_delta,
        badge=rollup.badge,
        quiz_id=quiz_ids.get(topic.id),
        has_lesson=topic.id in lessons,
        summary=topic_summary(topic.name, strengths),
        subtopics=strengths,
    )
