"""Personalized quiz feedback, aggregated across a student's attempts in one subject.

`answer_rows_for_attempts` is the single join from attempt answers down to topic/subtopic
names. Every other function here consumes its output rather than re-joining, so a future
change to the upstream question/subtopic/topic tables has exactly one query to update.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.config import get_settings
from education_platform.core.errors import DomainError
from education_platform.core.llm import OpenRouterError, chat_completion_json
from education_platform.modules.academics.models import (
    GradeSubjectOffering,
    Subject,
    Subtopic,
    Topic,
)
from education_platform.modules.assessments.models import (
    AttemptAnswer,
    CommonMasteryQuiz,
    Question,
    QuestionDifficulty,
    QuestionVersion,
    QuizAttempt,
    QuizAttemptStatus,
    QuizScope,
    QuizVersion,
)
from education_platform.modules.assessments.schemas import (
    FeedbackHighlights,
    RegressionOut,
    RemediationOut,
    ReviewNudgeOut,
    SubjectFeedbackDashboard,
    SubtopicAttemptScore,
    SubtopicFeedback,
    TrajectoryOut,
)
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.materials.queries import published_material_version

_RELEASED_STATUSES = (QuizAttemptStatus.SCORED, QuizAttemptStatus.RELEASED)


@dataclass(frozen=True, slots=True)
class AnswerOutcomeRow:
    attempt_id: UUID
    subtopic_id: UUID
    subtopic_name: str
    topic_name: str
    marks_awarded: Decimal
    marks_available: Decimal
    question_version_id: UUID
    selected_option_label: str | None
    is_correct: bool | None
    difficulty: QuestionDifficulty | None
    submitted_at: datetime | None


@dataclass(frozen=True, slots=True)
class SubtopicPerformance:
    subtopic_id: UUID
    subtopic_name: str
    topic_name: str
    percent: Decimal
    question_count: int
    percent_easy: Decimal | None
    easy_question_count: int
    percent_medium: Decimal | None
    medium_question_count: int
    percent_hard: Decimal | None
    hard_question_count: int


async def answer_rows_for_attempts(
    session: AsyncSession, attempt_ids: list[UUID]
) -> list[AnswerOutcomeRow]:
    """attempt_answers -> question_versions -> questions -> subtopics -> topics."""
    if not attempt_ids:
        return []
    rows = (
        await session.execute(
            select(
                AttemptAnswer.attempt_id,
                Subtopic.id,
                Subtopic.name,
                Topic.name,
                AttemptAnswer.marks_awarded,
                QuestionVersion.marks,
                AttemptAnswer.question_version_id,
                AttemptAnswer.selected_option_label,
                AttemptAnswer.is_correct,
                QuestionVersion.difficulty,
                QuizAttempt.submitted_at,
            )
            .join(QuestionVersion, QuestionVersion.id == AttemptAnswer.question_version_id)
            .join(Question, Question.id == QuestionVersion.question_id)
            .join(Subtopic, Subtopic.id == Question.subtopic_id)
            .join(Topic, Topic.id == Subtopic.topic_id)
            .join(QuizAttempt, QuizAttempt.id == AttemptAnswer.attempt_id)
            .where(AttemptAnswer.attempt_id.in_(attempt_ids))
        )
    ).all()
    return [
        AnswerOutcomeRow(
            attempt_id=attempt_id,
            subtopic_id=subtopic_id,
            subtopic_name=subtopic_name,
            topic_name=topic_name,
            marks_awarded=marks_awarded or Decimal("0"),
            marks_available=marks_available,
            question_version_id=question_version_id,
            selected_option_label=selected_option_label,
            is_correct=is_correct,
            difficulty=difficulty,
            submitted_at=submitted_at,
        )
        for (
            attempt_id,
            subtopic_id,
            subtopic_name,
            topic_name,
            marks_awarded,
            marks_available,
            question_version_id,
            selected_option_label,
            is_correct,
            difficulty,
            submitted_at,
        ) in rows
    ]


def _percent_for(rows: list[AnswerOutcomeRow]) -> Decimal | None:
    if not rows:
        return None
    earned = sum((row.marks_awarded for row in rows), Decimal("0"))
    available = sum((row.marks_available for row in rows), Decimal("0"))
    if available <= 0:
        return None
    return (earned / available * Decimal("100")).quantize(Decimal("0.01"))


def aggregate_by_subtopic(rows: list[AnswerOutcomeRow]) -> dict[UUID, SubtopicPerformance]:
    grouped: dict[UUID, list[AnswerOutcomeRow]] = defaultdict(list)
    for row in rows:
        grouped[row.subtopic_id].append(row)

    result: dict[UUID, SubtopicPerformance] = {}
    for subtopic_id, subtopic_rows in grouped.items():
        percent = _percent_for(subtopic_rows) or Decimal("0")
        easy_rows = [r for r in subtopic_rows if r.difficulty == QuestionDifficulty.EASY]
        medium_rows = [r for r in subtopic_rows if r.difficulty == QuestionDifficulty.MEDIUM]
        hard_rows = [r for r in subtopic_rows if r.difficulty == QuestionDifficulty.HARD]
        first = subtopic_rows[0]
        result[subtopic_id] = SubtopicPerformance(
            subtopic_id=subtopic_id,
            subtopic_name=first.subtopic_name,
            topic_name=first.topic_name,
            percent=percent,
            question_count=len(subtopic_rows),
            percent_easy=_percent_for(easy_rows),
            easy_question_count=len(easy_rows),
            percent_medium=_percent_for(medium_rows),
            medium_question_count=len(medium_rows),
            percent_hard=_percent_for(hard_rows),
            hard_question_count=len(hard_rows),
        )
    return result


_EPOCH = datetime.min.replace(tzinfo=UTC)


def _group_by_subtopic_attempt(
    rows: list[AnswerOutcomeRow],
) -> dict[UUID, dict[UUID, list[AnswerOutcomeRow]]]:
    grouped: dict[UUID, dict[UUID, list[AnswerOutcomeRow]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row.subtopic_id][row.attempt_id].append(row)
    return grouped


def _last_two_attempts(attempts: dict[UUID, list[AnswerOutcomeRow]]) -> tuple[UUID, UUID] | None:
    """(prior_attempt_id, latest_attempt_id) by submitted_at, or None with fewer than two."""
    ordered = sorted(attempts.keys(), key=lambda aid: attempts[aid][0].submitted_at or _EPOCH)
    if len(ordered) < 2:
        return None
    return ordered[-2], ordered[-1]


def attempt_history_by_subtopic(
    rows: list[AnswerOutcomeRow],
) -> dict[UUID, list[SubtopicAttemptScore]]:
    """Per subtopic: every scored attempt's percent, in chronological order, 1-indexed.

    `sequence` numbers this student's scored attempts on that subtopic in the order they
    happened — it is not QuizAttempt.attempt_number, which can include abandoned attempts
    this view already filters out.
    """
    by_subtopic_attempt = _group_by_subtopic_attempt(rows)
    result: dict[UUID, list[SubtopicAttemptScore]] = {}
    for subtopic_id, attempts in by_subtopic_attempt.items():
        ordered = sorted(attempts.keys(), key=lambda aid: attempts[aid][0].submitted_at or _EPOCH)
        result[subtopic_id] = [
            SubtopicAttemptScore(
                sequence=index,
                percent=_percent_for(attempts[attempt_id]) or Decimal("0"),
                submitted_at=attempts[attempt_id][0].submitted_at,
            )
            for index, attempt_id in enumerate(ordered, start=1)
        ]
    return result


def compute_trend(
    rows: list[AnswerOutcomeRow],
) -> dict[UUID, tuple[Decimal | None, Decimal | None]]:
    """Per subtopic: (prior_percent, delta) comparing the two most recent attempts touching it.

    Both are None when the student has fewer than two attempts on that subtopic — there's
    nothing to compare yet, not a zero change.
    """
    by_subtopic_attempt = _group_by_subtopic_attempt(rows)
    result: dict[UUID, tuple[Decimal | None, Decimal | None]] = {}
    for subtopic_id, attempts in by_subtopic_attempt.items():
        pair = _last_two_attempts(attempts)
        if pair is None:
            result[subtopic_id] = (None, None)
            continue
        prior_id, latest_id = pair
        prior_percent = _percent_for(attempts[prior_id])
        latest_percent = _percent_for(attempts[latest_id])
        delta = (
            (latest_percent - prior_percent)
            if prior_percent is not None and latest_percent is not None
            else None
        )
        result[subtopic_id] = (prior_percent, delta)
    return result


def detect_regressions(rows: list[AnswerOutcomeRow]) -> dict[UUID, int]:
    """Per subtopic: how many questions were correct on the previous attempt but are wrong
    on the most recent one — matched by question_version_id across the two most recent
    attempts touching that subtopic. Signals review/carelessness, not "never knew it"."""
    by_subtopic_attempt = _group_by_subtopic_attempt(rows)
    result: dict[UUID, int] = {}
    for subtopic_id, attempts in by_subtopic_attempt.items():
        pair = _last_two_attempts(attempts)
        if pair is None:
            continue
        prior_id, latest_id = pair
        prior_by_question = {r.question_version_id: r.is_correct for r in attempts[prior_id]}
        latest_by_question = {r.question_version_id: r.is_correct for r in attempts[latest_id]}
        count = sum(
            1
            for question_version_id, was_correct in prior_by_question.items()
            if was_correct is True and latest_by_question.get(question_version_id) is False
        )
        if count:
            result[subtopic_id] = count
    return result


def find_repeated_distractor_pattern(
    rows: list[AnswerOutcomeRow], subtopic_id: UUID, *, min_repeats: int = 2
) -> str | None:
    """The most common wrong option label this student picked in this subtopic, if it
    recurs enough to be worth a hedged mention — evidence consistent with a misconception,
    not a diagnosed one."""
    wrong_labels = [
        row.selected_option_label
        for row in rows
        if row.subtopic_id == subtopic_id and row.is_correct is False and row.selected_option_label
    ]
    if not wrong_labels:
        return None
    label, count = Counter(wrong_labels).most_common(1)[0]
    return label if count >= min_repeats else None


async def _subtopic_mastery_quiz(
    session: AsyncSession, subtopic_id: UUID
) -> CommonMasteryQuiz | None:
    return cast(
        "CommonMasteryQuiz | None",
        await session.scalar(
            select(CommonMasteryQuiz).where(
                CommonMasteryQuiz.subtopic_id == subtopic_id,
                CommonMasteryQuiz.quiz_scope == QuizScope.SUBTOPIC_MASTERY,
            )
        ),
    )


async def _classmate_percents_for_subtopic(
    session: AsyncSession, subtopic_id: UUID, exclude_student_id: UUID
) -> list[Decimal]:
    """Percent-per-attempt for every other student's scored attempts on this subtopic's quiz."""
    quiz = await _subtopic_mastery_quiz(session, subtopic_id)
    if quiz is None:
        return []
    version_ids = list(
        await session.scalars(select(QuizVersion.id).where(QuizVersion.quiz_id == quiz.id))
    )
    if not version_ids:
        return []
    attempt_ids = list(
        await session.scalars(
            select(QuizAttempt.id).where(
                QuizAttempt.quiz_version_id.in_(version_ids),
                QuizAttempt.student_id != exclude_student_id,
                QuizAttempt.status.in_(_RELEASED_STATUSES),
            )
        )
    )
    if not attempt_ids:
        return []
    rows = [
        row
        for row in await answer_rows_for_attempts(session, attempt_ids)
        if row.subtopic_id == subtopic_id
    ]
    by_attempt: dict[UUID, list[AnswerOutcomeRow]] = defaultdict(list)
    for row in rows:
        by_attempt[row.attempt_id].append(row)

    percents: list[Decimal] = []
    for attempt_rows in by_attempt.values():
        earned = sum((row.marks_awarded for row in attempt_rows), Decimal("0"))
        available = sum((row.marks_available for row in attempt_rows), Decimal("0"))
        if available > 0:
            percents.append((earned / available * Decimal("100")).quantize(Decimal("0.01")))
    return percents


async def _remediation_for_subtopic(
    session: AsyncSession, subtopic_id: UUID
) -> RemediationOut | None:
    """A lesson to review and/or a retake-just-this-subtopic quiz, for a flagged weak subtopic."""
    material_version = await published_material_version(session, subtopic_id)
    quiz = await _subtopic_mastery_quiz(session, subtopic_id)
    if material_version is None and quiz is None:
        return None
    return RemediationOut(
        material_id=material_version.source_material_id if material_version else None,
        material_title=material_version.title if material_version else None,
        retake_quiz_id=quiz.id if quiz else None,
    )


_REVIEW_SCHEDULE_DAYS = (3, 10, 21)
_TRAJECTORY_FLAT_BAND_PERCENT = Decimal("10")


def _passing_streak(attempts: list[SubtopicAttemptScore], pass_mark: Decimal) -> int:
    """How many of the most recent attempts, ending at the latest, all passed."""
    streak = 0
    for attempt in reversed(attempts):
        if attempt.percent < pass_mark:
            break
        streak += 1
    return streak


def _review_interval_days(streak: int) -> int:
    """A fixed spaced-repetition schedule indexed by consecutive passes, capped at the last
    value — good enough for v1, not a full SM-2 algorithm."""
    index = min(max(streak, 1) - 1, len(_REVIEW_SCHEDULE_DAYS) - 1)
    return _REVIEW_SCHEDULE_DAYS[index]


def learning_trajectory(
    history: list[SubtopicAttemptScore],
) -> tuple[Literal["improving", "flat", "declining"], Decimal, Literal["low", "medium"]] | None:
    """Direction of a student's recent history in one subtopic — never a projected number of
    future attempts (a linear fit through a couple of points has no reliable confidence
    interval and ignores ceiling effects near 100%; a heuristic direction is the honest
    signal here).

    None with fewer than 2 attempts — nothing to compare yet. Thresholds below are scaled to
    this platform's realistic attempt budget (quizzes typically allow 3 attempts), not a
    large sample: `evidence_level` maxes out at "medium" once most/all of that budget has
    been used, and the flat/noise band is wide enough that one question's difference on a
    typical 10-question quiz doesn't read as a real trend.
    """
    if len(history) < 2:
        return None
    change = history[-1].percent - history[0].percent
    if change > _TRAJECTORY_FLAT_BAND_PERCENT:
        trend: Literal["improving", "flat", "declining"] = "improving"
    elif change < -_TRAJECTORY_FLAT_BAND_PERCENT:
        trend = "declining"
    else:
        trend = "flat"
    recent_change = history[-1].percent - history[-2].percent
    evidence_level: Literal["low", "medium"] = "low" if len(history) < 3 else "medium"
    return trend, recent_change, evidence_level


async def subtopics_due_for_review(
    session: AsyncSession,
    rows: list[AnswerOutcomeRow],
    *,
    pass_mark: Decimal,
    now: datetime | None = None,
) -> list[ReviewNudgeOut]:
    """Subtopics once mastered but likely decaying, flagged before a bad quiz score reveals it."""
    current_time = now or datetime.now(UTC)
    history = attempt_history_by_subtopic(rows)
    performance = aggregate_by_subtopic(rows)

    nudges: list[ReviewNudgeOut] = []
    for subtopic_id, attempts in history.items():
        if not attempts:
            continue
        latest = attempts[-1]
        if latest.percent < pass_mark or latest.submitted_at is None:
            continue
        interval = _review_interval_days(_passing_streak(attempts, pass_mark))
        days_since = (current_time - latest.submitted_at).days
        if days_since < interval:
            continue
        perf = performance.get(subtopic_id)
        if perf is None:
            continue
        nudges.append(
            ReviewNudgeOut(
                subtopic_id=subtopic_id,
                subtopic_name=perf.subtopic_name,
                topic_name=perf.topic_name,
                days_since_last_attempt=days_since,
                due_interval_days=interval,
                remediation=await _remediation_for_subtopic(session, subtopic_id),
            )
        )
    nudges.sort(key=lambda n: n.days_since_last_attempt - n.due_interval_days, reverse=True)
    return nudges


def _trajectories_for_rows(rows: list[AnswerOutcomeRow]) -> list[TrajectoryOut]:
    history = attempt_history_by_subtopic(rows)
    performance = aggregate_by_subtopic(rows)
    trajectories: list[TrajectoryOut] = []
    for subtopic_id, attempts in history.items():
        result = learning_trajectory(attempts)
        if result is None:
            continue
        perf = performance.get(subtopic_id)
        if perf is None:
            continue
        trend, recent_change, evidence_level = result
        trajectories.append(
            TrajectoryOut(
                subtopic_id=subtopic_id,
                subtopic_name=perf.subtopic_name,
                trend=trend,
                recent_change=recent_change,
                evidence_level=evidence_level,
            )
        )
    return trajectories


async def get_feedback_highlights(session: AsyncSession, scope: Scope) -> FeedbackHighlights:
    """Cross-subject dashboard highlights: review nudges and learning trajectories.

    Unlike get_subject_feedback, this spans every subject the student has attempted —
    that's the point of a dashboard-home view rather than a per-subject page.
    """
    if scope.self_student_id is None:
        raise DomainError("Student enrollment required", status_code=403)
    student_id = scope.self_student_id
    settings = get_settings()

    attempt_ids = list(
        await session.scalars(
            select(QuizAttempt.id).where(
                QuizAttempt.student_id == student_id,
                QuizAttempt.status.in_(_RELEASED_STATUSES),
            )
        )
    )
    rows = await answer_rows_for_attempts(session, attempt_ids)
    pass_mark = Decimal(str(settings.mastery_pass_percent))

    due_reviews = await subtopics_due_for_review(session, rows, pass_mark=pass_mark)
    trajectories = _trajectories_for_rows(rows)

    return FeedbackHighlights(due_reviews=due_reviews, trajectories=trajectories)


def _classify_weak(
    percent: Decimal,
    question_count: int,
    classmate_percents: list[Decimal],
    *,
    threshold_percent: Decimal,
    relative_sd: float,
    min_class_n: int,
    min_student_n: int,
) -> bool | None:
    if question_count < min_student_n:
        return None
    bar = threshold_percent
    if len(classmate_percents) >= min_class_n:
        mean = sum(classmate_percents, Decimal("0")) / len(classmate_percents)
        variance = sum(((p - mean) ** 2 for p in classmate_percents), Decimal("0")) / len(
            classmate_percents
        )
        stddev = variance.sqrt()
        adaptive_bar = mean - Decimal(str(relative_sd)) * stddev
        bar = min(bar, adaptive_bar)
    return percent < bar


async def _attempt_ids_for_subject(
    session: AsyncSession, student_id: UUID, subject_id: UUID
) -> list[UUID]:
    subtopic_target_ids = (
        select(Subtopic.id)
        .join(Topic, Topic.id == Subtopic.topic_id)
        .join(GradeSubjectOffering, GradeSubjectOffering.id == Topic.grade_subject_offering_id)
        .where(GradeSubjectOffering.subject_id == subject_id)
    )
    topic_target_ids = (
        select(Topic.id)
        .join(GradeSubjectOffering, GradeSubjectOffering.id == Topic.grade_subject_offering_id)
        .where(GradeSubjectOffering.subject_id == subject_id)
    )
    quiz_ids = select(CommonMasteryQuiz.id).where(
        CommonMasteryQuiz.subtopic_id.in_(subtopic_target_ids)
        | CommonMasteryQuiz.topic_id.in_(topic_target_ids)
    )
    version_ids = select(QuizVersion.id).where(QuizVersion.quiz_id.in_(quiz_ids))
    return list(
        await session.scalars(
            select(QuizAttempt.id).where(
                QuizAttempt.student_id == student_id,
                QuizAttempt.quiz_version_id.in_(version_ids),
                QuizAttempt.status.in_(_RELEASED_STATUSES),
            )
        )
    )


def _template_summary(strong: SubtopicPerformance | None, weak: list[SubtopicPerformance]) -> str:
    parts: list[str] = []
    if strong is not None:
        parts.append(f"You're doing well in {strong.subtopic_name}.")
    if weak:
        names = ", ".join(w.subtopic_name for w in weak[:3])
        parts.append(f"Focus your next study session on: {names}.")
    else:
        parts.append("No specific weak areas stand out yet — keep practicing consistently.")
    return " ".join(parts)


async def _openrouter_summary_writer(prompt: str) -> str:
    payload = await chat_completion_json(
        [
            {
                "role": "system",
                "content": (
                    "You write short, encouraging feedback summaries for a student, based only "
                    "on the facts given. Never invent claims not present in the facts. Return "
                    'JSON only, in this exact shape: {"summary": "..."}'
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    summary = payload.get("summary")
    return str(summary) if isinstance(summary, str) and summary.strip() else ""


def _prompt_for(
    subject_name: str,
    strong: SubtopicPerformance | None,
    weak: list[SubtopicPerformance],
    overall_percent: Decimal | None,
    trend: dict[UUID, tuple[Decimal | None, Decimal | None]],
    distractor_labels: dict[UUID, str],
    regressions: dict[UUID, int],
) -> str:
    lines = [f"Subject: {subject_name}"]
    if overall_percent is not None:
        lines.append(f"Overall percent across scored attempts: {overall_percent}%")
    if strong is not None:
        lines.append(f"Strongest subtopic: {strong.subtopic_name} ({strong.percent}%)")
        _prior, strong_delta = trend.get(strong.subtopic_id, (None, None))
        if strong_delta is not None:
            sign = "+" if strong_delta >= 0 else ""
            lines.append(f"  - Trend since last attempt: {sign}{strong_delta}%")
    if weak:
        weak_desc = ", ".join(f"{w.subtopic_name} ({w.percent}%)" for w in weak)
        lines.append(f"Weak subtopics: {weak_desc}")
        for w in weak:
            _prior, w_delta = trend.get(w.subtopic_id, (None, None))
            if w_delta is not None:
                sign = "+" if w_delta >= 0 else ""
                lines.append(f"  - {w.subtopic_name}: trend since last attempt {sign}{w_delta}%")
            tiers = [
                ("easy", w.percent_easy),
                ("medium", w.percent_medium),
                ("hard", w.percent_hard),
            ]
            known = [(label, pct) for label, pct in tiers if pct is not None]
            if w.percent_easy is not None and w.percent_easy < Decimal("50"):
                lines.append(
                    f"  - {w.subtopic_name}: struggling on foundational (easy) questions "
                    f"({w.percent_easy}%) — this looks like a basics gap, not just a "
                    "hard-question gap."
                )
            elif w.percent_hard is not None and (
                w.percent_easy is not None or w.percent_medium is not None
            ):
                easier_desc = ", ".join(
                    f"{label} {pct}%" for label, pct in known if label != "hard"
                )
                lines.append(
                    f"  - {w.subtopic_name}: solid on easier questions ({easier_desc}) but "
                    f"weaker on hard ones ({w.percent_hard}%) — a stretch/extension gap, not "
                    "a basics gap."
                )
            distractor_label = distractor_labels.get(w.subtopic_id)
            if distractor_label:
                lines.append(
                    f"  - {w.subtopic_name}: repeatedly picked option {distractor_label} when "
                    "incorrect — may point to a specific mix-up, but this is a pattern, not a "
                    'confirmed diagnosis; phrase it as a hedge ("you may be mixing up...").'
                )
            regressed_count = regressions.get(w.subtopic_id)
            if regressed_count:
                lines.append(
                    f"  - {w.subtopic_name}: {regressed_count} question(s) answered correctly "
                    "before are now wrong on the latest attempt — likely a review/carelessness "
                    "slip, not a new knowledge gap."
                )
    else:
        lines.append("No subtopic is flagged as weak.")
    lines.append(
        "Write 2-3 encouraging sentences: lead with the strength, then name what to focus on. "
        "If a trend since last attempt is given, mention direction (improving/declining) for "
        "at most one subtopic — don't list every number."
    )
    return "\n".join(lines)


async def get_subject_feedback(
    session: AsyncSession, scope: Scope, subject_id: UUID
) -> SubjectFeedbackDashboard:
    if scope.self_student_id is None:
        raise DomainError("Student enrollment required", status_code=403)
    student_id = scope.self_student_id

    subject = await session.get(Subject, subject_id)
    if subject is None:
        raise DomainError("Subject not found", status_code=404)

    offering_ids = list(
        await session.scalars(
            select(GradeSubjectOffering.id).where(GradeSubjectOffering.subject_id == subject_id)
        )
    )
    covered = any(
        scope.covers_offering(offering_id, institution_id=scope.institution_id)
        for offering_id in offering_ids
    )
    if not covered:
        raise DomainError("Subject not found", status_code=404)

    settings = get_settings()
    attempt_ids = await _attempt_ids_for_subject(session, student_id, subject_id)
    rows = await answer_rows_for_attempts(session, attempt_ids)
    performance = aggregate_by_subtopic(rows)
    trend = compute_trend(rows)
    regressions_by_subtopic = detect_regressions(rows)
    attempt_history = attempt_history_by_subtopic(rows)

    total_earned = sum((row.marks_awarded for row in rows), Decimal("0"))
    total_available = sum((row.marks_available for row in rows), Decimal("0"))
    overall_percent = (
        (total_earned / total_available * Decimal("100")).quantize(Decimal("0.01"))
        if total_available > 0
        else None
    )

    subtopic_feedback: list[SubtopicFeedback] = []
    strongest: SubtopicPerformance | None = None
    weak_list: list[SubtopicPerformance] = []
    distractor_labels: dict[UUID, str] = {}

    for perf in performance.values():
        classmate_percents = await _classmate_percents_for_subtopic(
            session, perf.subtopic_id, student_id
        )
        is_weak = _classify_weak(
            perf.percent,
            perf.question_count,
            classmate_percents,
            threshold_percent=Decimal(str(settings.feedback_weak_threshold_percent)),
            relative_sd=settings.feedback_relative_sd,
            min_class_n=settings.feedback_min_class_n,
            min_student_n=settings.feedback_min_student_n,
        )
        class_avg = (
            (sum(classmate_percents, Decimal("0")) / len(classmate_percents)).quantize(
                Decimal("0.01")
            )
            if classmate_percents
            else None
        )
        prior_percent, delta = trend.get(perf.subtopic_id, (None, None))
        remediation = (
            await _remediation_for_subtopic(session, perf.subtopic_id) if is_weak else None
        )
        if is_weak:
            distractor_label = find_repeated_distractor_pattern(rows, perf.subtopic_id)
            if distractor_label:
                distractor_labels[perf.subtopic_id] = distractor_label

        # Deferred import: goals_service imports this module for its own percent lookup.
        from education_platform.modules.assessments import goals_service

        goal_row = await goals_service.goal_for_subtopic(session, student_id, perf.subtopic_id)
        goal = (
            goals_service.goal_out(goal_row, perf.subtopic_name, perf.percent)
            if goal_row is not None
            else None
        )

        subtopic_feedback.append(
            SubtopicFeedback(
                subtopic_id=perf.subtopic_id,
                subtopic_name=perf.subtopic_name,
                topic_name=perf.topic_name,
                percent=perf.percent,
                question_count=perf.question_count,
                attempts=attempt_history.get(perf.subtopic_id, []),
                prior_percent=prior_percent,
                delta=delta,
                percent_easy=perf.percent_easy,
                easy_question_count=perf.easy_question_count,
                percent_medium=perf.percent_medium,
                medium_question_count=perf.medium_question_count,
                percent_hard=perf.percent_hard,
                remediation=remediation,
                hard_question_count=perf.hard_question_count,
                class_avg_percent=class_avg,
                class_sample_size=len(classmate_percents),
                is_weak=is_weak,
                goal=goal,
            )
        )
        if is_weak:
            weak_list.append(perf)
        if strongest is None or perf.percent > strongest.percent:
            strongest = perf

    if not weak_list and performance:
        lowest = min(performance.values(), key=lambda p: p.percent)
        weak_list = [lowest]

    weak_list.sort(key=lambda p: p.percent)
    subtopic_feedback.sort(key=lambda s: s.percent)

    pass_mark = Decimal(str(settings.mastery_pass_percent))
    passing_strength = (
        strongest if strongest is not None and strongest.percent >= pass_mark else None
    )

    prompt = _prompt_for(
        subject.name,
        passing_strength,
        weak_list,
        overall_percent,
        trend,
        distractor_labels,
        regressions_by_subtopic,
    )
    try:
        summary = await _openrouter_summary_writer(prompt)
    except OpenRouterError:
        summary = ""
    if not summary:
        summary = _template_summary(passing_strength, weak_list)

    regressions = [
        RegressionOut(
            subtopic_id=perf.subtopic_id,
            subtopic_name=perf.subtopic_name,
            regressed_question_count=count,
        )
        for perf in performance.values()
        if (count := regressions_by_subtopic.get(perf.subtopic_id))
    ]

    return SubjectFeedbackDashboard(
        subject_id=subject.id,
        subject_name=subject.name,
        overall_percent=overall_percent,
        attempt_count=len(attempt_ids),
        subtopics=subtopic_feedback,
        strength_subtopic_id=strongest.subtopic_id if strongest else None,
        top_focus_subtopic_ids=[p.subtopic_id for p in weak_list[:3]],
        regressions=regressions,
        summary=summary,
    )
