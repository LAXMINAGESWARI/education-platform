"""Quiz attempt start/submit/score logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.errors import DomainError
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.academics.service import (
    CurriculumNode,
    load_subtopic_node,
    load_topic_node,
    require_subject_enrollment,
)
from education_platform.modules.assessments.models import (
    AttemptAnswer,
    AttemptQuestion,
    CommonMasteryQuiz,
    QuestionAnswerKey,
    QuestionOption,
    QuestionVersion,
    QuizAttempt,
    QuizAttemptStatus,
    QuizResultReleaseMode,
    QuizScope,
    QuizVersion,
    QuizVersionStatus,
)
from education_platform.modules.assessments.queries import (
    PASS_CREDIT_STATUSES,
    history_item_for,
    open_release_for_quiz_version,
)
from education_platform.modules.assessments.question_selection import (
    assign_attempt_questions,
    questions_for_attempt,
    served_questions_for_attempt,
)
from education_platform.modules.assessments.schemas import (
    AttemptAnswerOut,
    AttemptHistoryItem,
    AttemptResult,
    StartAttemptResponse,
    SubmitAttemptRequest,
)
from education_platform.modules.audit.service import AuditAction, record_event
from education_platform.modules.auth.models import StudentProfile
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.materials.models import (
    MaterialProgressStatus,
    SourceMaterial,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
    StudentMaterialProgress,
)
from education_platform.modules.materials.queries import (
    published_topic_material_version,
    subtopic_by_slug,
)


async def _released_quiz_version_by_quiz_id(
    session: AsyncSession, quiz_id: UUID
) -> tuple[CommonMasteryQuiz, QuizVersion]:
    row = (
        await session.execute(
            select(CommonMasteryQuiz, QuizVersion)
            .join(QuizVersion, QuizVersion.quiz_id == CommonMasteryQuiz.id)
            .where(
                CommonMasteryQuiz.id == quiz_id,
                QuizVersion.lifecycle_status == QuizVersionStatus.RELEASED,
            )
            .order_by(QuizVersion.version_number.desc())
        )
    ).first()
    if row is None:
        raise DomainError("Quiz not found", status_code=404)
    return row[0], row[1]


async def _node_for_quiz(session: AsyncSession, quiz: CommonMasteryQuiz) -> CurriculumNode:
    node: CurriculumNode | None
    if quiz.quiz_scope == QuizScope.SUBTOPIC_MASTERY and quiz.subtopic_id is not None:
        node = await load_subtopic_node(session, quiz.subtopic_id)
    elif quiz.quiz_scope == QuizScope.TOPIC_MASTERY and quiz.topic_id is not None:
        node = await load_topic_node(session, quiz.topic_id)
    else:
        node = None
    if node is None:
        raise DomainError("Quiz not found", status_code=404)
    return node


def _require_covers_quiz(scope: Scope, node: CurriculumNode) -> None:
    if not scope.covers_offering(node.offering.id, institution_id=node.institution.id):
        raise DomainError("Quiz not found", status_code=404)


async def _ensure_lesson_completed(
    session: AsyncSession, *, subtopic_id: UUID, enrollment_id: UUID
) -> None:
    version = await session.scalar(
        select(SourceMaterialVersion)
        .join(SourceMaterial, SourceMaterial.id == SourceMaterialVersion.source_material_id)
        .where(
            SourceMaterial.subtopic_id == subtopic_id,
            SourceMaterialVersion.lifecycle_status == SourceMaterialVersionStatus.PUBLISHED,
        )
        .order_by(SourceMaterialVersion.version_number.desc())
    )
    if version is None:
        return
    progress = await session.scalar(
        select(StudentMaterialProgress).where(
            StudentMaterialProgress.student_subject_enrollment_id == enrollment_id,
            StudentMaterialProgress.source_material_version_id == version.id,
            StudentMaterialProgress.status == MaterialProgressStatus.COMPLETED,
        )
    )
    if progress is None:
        raise DomainError("Complete the lesson first", status_code=403)


async def _ensure_topic_lesson_completed(
    session: AsyncSession, *, topic_id: UUID, enrollment_id: UUID
) -> None:
    version = await published_topic_material_version(session, topic_id)
    if version is None:
        return
    progress = await session.scalar(
        select(StudentMaterialProgress).where(
            StudentMaterialProgress.student_subject_enrollment_id == enrollment_id,
            StudentMaterialProgress.source_material_version_id == version.id,
            StudentMaterialProgress.status == MaterialProgressStatus.COMPLETED,
        )
    )
    if progress is None:
        raise DomainError("Complete the topic lesson first", status_code=403)


async def _ensure_subtopic_quizzes_passed(
    session: AsyncSession, student_id: UUID, topic_id: UUID
) -> None:
    """Require a pass on every subtopic quiz the learning directory would also require.

    The directory unlocks the topic quiz when each *available* subtopic quiz has a pass on
    any of its versions (``_quiz_summary``). This gate used to look only at the latest
    released version and to 403 when a subtopic had no quiz at all, so a curriculum reseed
    that minted version 2 — or a subtopic without a quiz — left the UI saying unlocked
    while start-attempt returned 403.
    """
    subtopic_ids = (
        await session.scalars(select(Subtopic.id).where(Subtopic.topic_id == topic_id))
    ).all()
    for subtopic_id in subtopic_ids:
        quiz = await session.scalar(
            select(CommonMasteryQuiz).where(
                CommonMasteryQuiz.quiz_scope == QuizScope.SUBTOPIC_MASTERY,
                CommonMasteryQuiz.subtopic_id == subtopic_id,
            )
        )
        if quiz is None:
            continue
        version_ids = list(
            await session.scalars(select(QuizVersion.id).where(QuizVersion.quiz_id == quiz.id))
        )
        if not version_ids:
            continue
        passed = await session.scalar(
            select(QuizAttempt.id).where(
                QuizAttempt.student_id == student_id,
                QuizAttempt.quiz_version_id.in_(version_ids),
                QuizAttempt.passed.is_(True),
                QuizAttempt.status.in_(PASS_CREDIT_STATUSES),
            )
        )
        if passed is None:
            raise DomainError("Pass all subtopic quizzes first", status_code=403)


async def start_attempt(
    session: AsyncSession, scope: Scope, quiz_id: UUID | str
) -> StartAttemptResponse:
    if isinstance(quiz_id, str):
        try:
            quiz_uuid = UUID(quiz_id)
        except ValueError:
            subtopic = await subtopic_by_slug(session, quiz_id, scope=scope)
            if subtopic is None:
                raise DomainError("Quiz not found", status_code=404) from None
            quiz = await session.scalar(
                select(CommonMasteryQuiz).where(
                    CommonMasteryQuiz.subtopic_id == subtopic.id,
                    CommonMasteryQuiz.quiz_scope == QuizScope.SUBTOPIC_MASTERY,
                )
            )
            if quiz is None:
                raise DomainError("Quiz not found", status_code=404) from None
            quiz_uuid = quiz.id
    else:
        quiz_uuid = quiz_id
    quiz, quiz_version = await _released_quiz_version_by_quiz_id(session, quiz_uuid)
    node = await _node_for_quiz(session, quiz)
    _require_covers_quiz(scope, node)
    enrollment = await require_subject_enrollment(session, scope, node.offering.id)
    enrollment_id = enrollment.id
    student_id = enrollment.student_id
    if quiz.quiz_scope == QuizScope.SUBTOPIC_MASTERY:
        if node.subtopic is not None:
            await _ensure_lesson_completed(
                session, subtopic_id=node.subtopic.id, enrollment_id=enrollment_id
            )
    else:
        topic_lesson = await published_topic_material_version(session, node.topic.id)
        if topic_lesson is None:
            await _ensure_subtopic_quizzes_passed(session, student_id, node.topic.id)
        else:
            await _ensure_topic_lesson_completed(
                session, topic_id=node.topic.id, enrollment_id=enrollment_id
            )
    release = await open_release_for_quiz_version(session, quiz_version.id)
    if release is None:
        raise DomainError("Quiz is not open", status_code=403)

    last_error: BaseException | None = None
    for _ in range(3):
        try:
            async with session.begin_nested():
                return await _open_new_attempt(
                    session,
                    student_id=student_id,
                    quiz=quiz,
                    quiz_version=quiz_version,
                    release_id=release.id,
                    enrollment_id=enrollment_id,
                )
        except IntegrityError as exc:
            last_error = exc
            # Savepoint rollback expires loaded ORM instances. Re-query before retrying
            # so async lazy-loads cannot raise MissingGreenlet and 500 the request.
            quiz, quiz_version = await _released_quiz_version_by_quiz_id(session, quiz_uuid)
            release = await open_release_for_quiz_version(session, quiz_version.id)
            if release is None:
                raise DomainError("Quiz is not open", status_code=403) from exc
    raise DomainError(
        "Could not start a new quiz attempt. Try again.",
        status_code=409,
    ) from last_error


async def _open_new_attempt(
    session: AsyncSession,
    *,
    student_id: UUID,
    quiz: CommonMasteryQuiz,
    quiz_version: QuizVersion,
    release_id: UUID,
    enrollment_id: UUID,
) -> StartAttemptResponse:
    in_progress = await session.scalar(
        select(QuizAttempt)
        .where(
            QuizAttempt.student_id == student_id,
            QuizAttempt.quiz_version_id == quiz_version.id,
            QuizAttempt.status == QuizAttemptStatus.IN_PROGRESS,
        )
        .with_for_update()
    )
    if in_progress is not None:
        await session.execute(
            delete(AttemptQuestion).where(AttemptQuestion.attempt_id == in_progress.id)
        )
        await session.execute(
            delete(AttemptAnswer).where(AttemptAnswer.attempt_id == in_progress.id)
        )
        await session.delete(in_progress)
        await session.flush()

    current_max = await session.scalar(
        select(func.max(QuizAttempt.attempt_number)).where(
            QuizAttempt.student_id == student_id,
            QuizAttempt.quiz_version_id == quiz_version.id,
        )
    )
    attempt_number = int(current_max or 0) + 1
    counted = await session.scalar(
        select(func.count()).where(
            QuizAttempt.student_id == student_id,
            QuizAttempt.quiz_version_id == quiz_version.id,
            QuizAttempt.status != QuizAttemptStatus.ABANDONED,
        )
    )
    if quiz_version.max_attempts is not None and int(counted or 0) >= quiz_version.max_attempts:
        raise DomainError("Maximum attempts reached", status_code=409)
    now = datetime.now(UTC)
    deadline_at = (
        now + timedelta(seconds=quiz_version.duration_seconds)
        if quiz_version.duration_seconds is not None
        else None
    )
    attempt = QuizAttempt(
        student_id=student_id,
        student_subject_enrollment_id=enrollment_id,
        quiz_version_id=quiz_version.id,
        quiz_release_id=release_id,
        attempt_number=attempt_number,
        status=QuizAttemptStatus.IN_PROGRESS,
        started_at=now,
        deadline_at=deadline_at,
        pass_threshold_percent=quiz_version.pass_threshold_percent,
    )
    session.add(attempt)
    await session.flush()
    await session.refresh(attempt)
    await assign_attempt_questions(
        session,
        attempt=attempt,
        quiz_id=quiz.id,
        quiz_version=quiz_version,
    )
    return await _start_response(session, quiz, quiz_version, attempt)


async def _start_response(
    session: AsyncSession,
    quiz: CommonMasteryQuiz,
    quiz_version: QuizVersion,
    attempt: QuizAttempt,
) -> StartAttemptResponse:
    target_id = quiz.subtopic_id if quiz.subtopic_id is not None else quiz.topic_id
    if target_id is None:
        raise DomainError("Quiz not found", status_code=404)
    return StartAttemptResponse(
        id=attempt.id,
        quiz_id=quiz.id,
        quiz_version_id=quiz_version.id,
        attempt_number=attempt.attempt_number,
        status=attempt.status.value,
        started_at=attempt.started_at,
        deadline_at=attempt.deadline_at,
        pass_threshold_percent=quiz_version.pass_threshold_percent,
        result_release_mode=quiz_version.result_release_mode.value,
        questions=await questions_for_attempt(session, attempt),
        title=quiz.title,
        scope=quiz.quiz_scope.value,
        target_id=target_id,
    )


async def _owned_attempt(
    session: AsyncSession,
    scope: Scope,
    attempt_id: UUID,
    *,
    for_update: bool = False,
) -> QuizAttempt:
    attempt = await session.get(QuizAttempt, attempt_id, with_for_update=for_update)
    if attempt is None:
        raise DomainError("Attempt not found", status_code=404)
    if scope.self_student_id is None or attempt.student_id != scope.self_student_id:
        raise DomainError("Attempt not found", status_code=404)
    return attempt


async def _quiz_for_quiz_version(session: AsyncSession, quiz_version_id: UUID) -> CommonMasteryQuiz:
    quiz = await session.scalar(
        select(CommonMasteryQuiz)
        .join(QuizVersion, QuizVersion.quiz_id == CommonMasteryQuiz.id)
        .where(QuizVersion.id == quiz_version_id)
    )
    if quiz is None:
        raise DomainError("Quiz not found", status_code=404)
    return quiz


async def submit_attempt(
    session: AsyncSession,
    scope: Scope,
    attempt_id: UUID,
    payload: SubmitAttemptRequest,
) -> AttemptResult:
    # Lock the row for the rest of this transaction so a double-submit cannot both
    # pass the status check and then collide on uq_attempt_answers_attempt_question.
    attempt = await _owned_attempt(session, scope, attempt_id, for_update=True)
    if attempt.status not in {QuizAttemptStatus.IN_PROGRESS, QuizAttemptStatus.NOT_STARTED}:
        raise DomainError("Attempt is not open for submission", status_code=409)

    quiz_version = await session.get(QuizVersion, attempt.quiz_version_id)
    if quiz_version is None:
        raise DomainError("Quiz not found", status_code=404)
    if attempt.deadline_at is not None:
        deadline = attempt.deadline_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        if deadline < datetime.now(UTC):
            attempt.status = QuizAttemptStatus.EXPIRED
            await session.flush()
            raise DomainError("Attempt expired", status_code=409)

    served = await served_questions_for_attempt(session, attempt.id, quiz_version.id)
    item_by_number = {question.number: question for question in served}
    numbers = [answer.question_number for answer in payload.answers]
    if len(numbers) != len(set(numbers)):
        raise DomainError("Duplicate answers", status_code=422)
    if set(numbers) != set(item_by_number):
        raise DomainError("Answers must match the quiz question set", status_code=422)
    answers_by_number = {answer.question_number: answer for answer in payload.answers}

    total_marks = Decimal("0")
    earned = Decimal("0")
    answer_rows: list[AttemptAnswer] = []

    for number, item in item_by_number.items():
        version = await session.get(QuestionVersion, item.question_version_id)
        if version is None:
            continue
        key = await session.scalar(
            select(QuestionAnswerKey).where(QuestionAnswerKey.question_version_id == version.id)
        )
        selected = answers_by_number.get(number)
        selected_label = selected.selected_option_label.upper() if selected else None
        label_exists = await session.scalar(
            select(QuestionOption.id).where(
                QuestionOption.question_version_id == version.id,
                QuestionOption.label == selected_label,
            )
        )
        if label_exists is None:
            raise DomainError(f"Invalid option for question {number}", status_code=422)
        is_correct = bool(
            key is not None
            and selected_label is not None
            and key.correct_option_label is not None
            and selected_label == key.correct_option_label.upper()
        )
        marks = version.marks if is_correct else Decimal("0")
        total_marks += version.marks
        earned += marks
        answer_rows.append(
            AttemptAnswer(
                attempt_id=attempt.id,
                question_version_id=version.id,
                selected_option_label=selected_label,
                is_correct=is_correct,
                marks_awarded=marks,
            )
        )

    existing = (
        await session.scalars(select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id))
    ).all()
    for row in existing:
        await session.delete(row)

    for row in answer_rows:
        session.add(row)

    now = datetime.now(UTC)
    percent = (
        (earned / total_marks * Decimal("100")).quantize(Decimal("0.01"))
        if total_marks > 0
        else Decimal("0")
    )
    pass_threshold = quiz_version.pass_threshold_percent
    attempt.status = (
        QuizAttemptStatus.HELD
        if quiz_version.result_release_mode == QuizResultReleaseMode.ADMIN_RELEASE
        else QuizAttemptStatus.SCORED
    )
    attempt.submitted_at = now
    attempt.scored_at = now
    attempt.score_raw = earned
    attempt.score_percent = percent
    attempt.pass_threshold_percent = pass_threshold
    attempt.passed = percent >= pass_threshold
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DomainError("Attempt is not open for submission", status_code=409) from exc

    quiz = await _quiz_for_quiz_version(session, attempt.quiz_version_id)
    profile = await session.get(StudentProfile, attempt.student_id)
    await record_event(
        session,
        institution_id=scope.institution_id,
        actor_user_id=profile.user_id if profile is not None else None,
        event_type=AuditAction.QUIZ_SUBMITTED,
        entity_type="quiz_attempt",
        entity_id=attempt.id,
        payload={"quiz_id": str(quiz.id), "passed": attempt.passed},
    )

    return await get_attempt(session, scope, attempt.id)


async def get_attempt(
    session: AsyncSession,
    scope: Scope,
    attempt_id: UUID,
) -> AttemptResult:
    attempt = await _owned_attempt(session, scope, attempt_id)
    quiz = await _quiz_for_quiz_version(session, attempt.quiz_version_id)
    quiz_version = await session.get(QuizVersion, attempt.quiz_version_id)
    review_available = not (
        quiz_version is not None
        and quiz_version.result_release_mode == QuizResultReleaseMode.ADMIN_RELEASE
        and attempt.status == QuizAttemptStatus.HELD
    )
    served = await served_questions_for_attempt(session, attempt.id, attempt.quiz_version_id)
    number_by_qv = {question.question_version_id: question.number for question in served}
    answers = (
        await session.scalars(select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id))
    ).all()

    target_id = quiz.subtopic_id if quiz.subtopic_id is not None else quiz.topic_id
    return AttemptResult(
        id=attempt.id,
        quiz_id=quiz.id,
        target_id=target_id,
        scope=quiz.quiz_scope.value,
        attempt_number=attempt.attempt_number,
        status=attempt.status.value,
        started_at=attempt.started_at,
        deadline_at=attempt.deadline_at,
        submitted_at=attempt.submitted_at,
        scored_at=attempt.scored_at,
        score_raw=attempt.score_raw if review_available else None,
        score_percent=attempt.score_percent if review_available else None,
        pass_threshold_percent=attempt.pass_threshold_percent,
        passed=attempt.passed if review_available else None,
        review_available=review_available,
        answers=sorted(
            (
                AttemptAnswerOut(
                    question_number=number_by_qv[answer.question_version_id],
                    selected_option_label=answer.selected_option_label,
                    is_correct=answer.is_correct if review_available else None,
                    marks_awarded=answer.marks_awarded if review_available else None,
                )
                for answer in answers
                if answer.question_version_id in number_by_qv
            ),
            key=lambda answer: answer.question_number,
        )
        if review_available
        else [],
    )


async def list_attempts_for_quiz(
    session: AsyncSession, scope: Scope, quiz_id: UUID
) -> list[AttemptHistoryItem]:
    quiz, _version = await _released_quiz_version_by_quiz_id(session, quiz_id)
    node = await _node_for_quiz(session, quiz)
    _require_covers_quiz(scope, node)
    enrollment = await require_subject_enrollment(session, scope, node.offering.id)
    version_ids = (
        await session.scalars(select(QuizVersion.id).where(QuizVersion.quiz_id == quiz.id))
    ).all()
    attempts = (
        await session.scalars(
            select(QuizAttempt)
            .where(
                QuizAttempt.student_id == enrollment.student_id,
                QuizAttempt.quiz_version_id.in_(version_ids),
                QuizAttempt.status != QuizAttemptStatus.ABANDONED,
            )
            .order_by(QuizAttempt.attempt_number.desc())
        )
    ).all()
    return [history_item_for(attempt) for attempt in attempts]
