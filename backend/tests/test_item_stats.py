"""Item statistics from counted quiz attempts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from education_platform.modules.academics.models import (
    AcademicPeriod,
    AcademicPeriodStatus,
    Grade,
    GradeSubjectOffering,
    PeriodGrade,
    Subject,
    Subtopic,
    Topic,
)
from education_platform.modules.assessments.item_stats import (
    ItemOptionStat,
    ItemStat,
    TopicItemStats,
    item_stats_for_quiz_version,
    item_stats_for_topic,
)
from education_platform.modules.assessments.models import (
    AttemptAnswer,
    CommonMasteryQuiz,
    Question,
    QuestionAnswerKey,
    QuestionOption,
    QuestionType,
    QuestionVersion,
    QuizAttempt,
    QuizAttemptStatus,
    QuizItem,
    QuizScope,
    QuizVersion,
    QuizVersionStatus,
)
from education_platform.modules.auth.models import Institution, StudentProfile

FOCAL = "Focal stem"
COMPANION = "Companion stem"
_OPTION_TEXT = {"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta"}


@dataclass(frozen=True)
class Row:
    label: str
    rest: int
    status: QuizAttemptStatus = QuizAttemptStatus.SCORED


@dataclass(frozen=True)
class Seeded:
    topic_id: UUID
    quiz_version_id: UUID
    focal_id: UUID


def _focal(items: list[ItemStat]) -> ItemStat:
    found = [item for item in items if item.prompt == FOCAL]
    assert len(found) == 1
    return found[0]


def _option(item: ItemStat, label: str) -> ItemOptionStat:
    found = [option for option in item.options if option.label == label]
    assert len(found) == 1
    return found[0]


async def _topic_tree(session: AsyncSession) -> tuple[Topic, Subtopic, StudentProfile]:
    institution = Institution(name=f"Stats School {uuid4().hex[:8]}")
    session.add(institution)
    await session.flush()
    period = AcademicPeriod(
        institution_id=institution.id,
        name="2026-27",
        start_date=date(2026, 6, 1),
        end_date=date(2027, 3, 31),
        status=AcademicPeriodStatus.ACTIVE,
    )
    grade = Grade(institution_id=institution.id, name="Grade 8", sort_order=8)
    subject = Subject(institution_id=institution.id, name="Mathematics", code="MATH")
    session.add_all([period, grade, subject])
    await session.flush()
    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    session.add(period_grade)
    await session.flush()
    offering = GradeSubjectOffering(period_grade_id=period_grade.id, subject_id=subject.id)
    session.add(offering)
    await session.flush()
    topic = Topic(
        grade_subject_offering_id=offering.id,
        name="Numbers",
        slug="numbers",
        sequence=1,
    )
    session.add(topic)
    await session.flush()
    subtopic = Subtopic(topic_id=topic.id, name="Place value", slug="place-value", sequence=1)
    session.add(subtopic)
    await session.flush()
    student = StudentProfile(
        institution_id=institution.id,
        student_identifier="S-1",
        full_name="Stat Student",
    )
    session.add(student)
    await session.flush()
    return topic, subtopic, student


async def _question(session: AsyncSession, subtopic_id: UUID, prompt: str) -> QuestionVersion:
    question = Question(subtopic_id=subtopic_id, code=None)
    session.add(question)
    await session.flush()
    version = QuestionVersion(
        question_id=question.id,
        version_number=1,
        prompt=prompt,
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=Decimal("1.00"),
    )
    session.add(version)
    await session.flush()
    session.add_all(
        [
            QuestionOption(
                question_version_id=version.id,
                label=label,
                text=text,
                sequence=index,
            )
            for index, (label, text) in enumerate(_OPTION_TEXT.items(), start=1)
        ]
    )
    session.add(QuestionAnswerKey(question_version_id=version.id, correct_option_label="A"))
    await session.flush()
    return version


async def _attempts(
    session: AsyncSession,
    *,
    student_id: UUID,
    quiz_version_id: UUID,
    focal_id: UUID,
    companion_id: UUID,
    rows: list[Row],
    roll_up_score: bool = True,
) -> None:
    pending: list[tuple[QuizAttempt, Row, bool, Decimal, Decimal]] = []
    for number, row in enumerate(rows, start=1):
        if row.rest not in (0, 1):
            raise AssertionError("rest must be 0 or 1")
        focal_correct = row.label == "A"
        focal_marks = Decimal("1.00") if focal_correct else Decimal("0.00")
        companion_marks = Decimal("1.00") if row.rest else Decimal("0.00")
        pending.append(
            (
                QuizAttempt(
                    student_id=student_id,
                    quiz_version_id=quiz_version_id,
                    attempt_number=number,
                    status=row.status,
                    score_raw=focal_marks + companion_marks if roll_up_score else None,
                ),
                row,
                focal_correct,
                focal_marks,
                companion_marks,
            )
        )
    session.add_all([attempt for attempt, *_rest in pending])
    await session.flush()
    answers: list[AttemptAnswer] = []
    for attempt, row, focal_correct, focal_marks, companion_marks in pending:
        answers.append(
            AttemptAnswer(
                attempt_id=attempt.id,
                question_version_id=focal_id,
                selected_option_label=row.label,
                is_correct=focal_correct,
                marks_awarded=focal_marks,
            )
        )
        answers.append(
            AttemptAnswer(
                attempt_id=attempt.id,
                question_version_id=companion_id,
                selected_option_label="A" if row.rest else "B",
                is_correct=row.rest == 1,
                marks_awarded=companion_marks,
            )
        )
    session.add_all(answers)
    await session.flush()


async def _seed(session: AsyncSession, rows: list[Row], *, roll_up_score: bool = True) -> Seeded:
    topic, subtopic, student = await _topic_tree(session)
    focal = await _question(session, subtopic.id, FOCAL)
    companion = await _question(session, subtopic.id, COMPANION)
    quiz = CommonMasteryQuiz(
        quiz_scope=QuizScope.TOPIC_MASTERY,
        topic_id=topic.id,
        title="Topic mastery",
    )
    session.add(quiz)
    await session.flush()
    version = QuizVersion(
        quiz_id=quiz.id,
        version_number=1,
        lifecycle_status=QuizVersionStatus.RELEASED,
    )
    session.add(version)
    await session.flush()
    session.add_all(
        [
            QuizItem(quiz_version_id=version.id, question_version_id=focal.id, sequence=1),
            QuizItem(quiz_version_id=version.id, question_version_id=companion.id, sequence=2),
        ]
    )
    await session.flush()
    await _attempts(
        session,
        student_id=student.id,
        quiz_version_id=version.id,
        focal_id=focal.id,
        companion_id=companion.id,
        rows=rows,
        roll_up_score=roll_up_score,
    )
    return Seeded(topic_id=topic.id, quiz_version_id=version.id, focal_id=focal.id)


def _balanced_rows() -> list[Row]:
    """Twenty answers whose rest-scores are independent of correctness (r = 0).

    Statuses mix submitted, scored, and released. The 27% tails are attempts 1–5
    (all key) and 16–20 (C, C, D, D, D). Option B is picked once (exactly 5%).
    """
    pattern = (
        [("A", 1)] * 5
        + [("B", 1), ("C", 1), ("C", 1), ("D", 1), ("D", 1)]
        + [("A", 0)] * 5
        + [("C", 0), ("C", 0), ("D", 0), ("D", 0), ("D", 0)]
    )
    statuses = (
        [QuizAttemptStatus.SCORED] * 7
        + [QuizAttemptStatus.SUBMITTED] * 7
        + [QuizAttemptStatus.RELEASED] * 6
    )
    return [
        Row(label, rest, status) for (label, rest), status in zip(pattern, statuses, strict=True)
    ]


def _negative_rows() -> list[Row]:
    return [Row("B", rest=1) for _ in range(10)] + [Row("A", rest=0) for _ in range(10)]


async def test_unknown_quiz_version_has_no_items(async_db_session: AsyncSession) -> None:
    assert await item_stats_for_quiz_version(async_db_session, uuid4()) == []


async def test_unknown_topic_has_no_released_quiz(async_db_session: AsyncSession) -> None:
    report = await item_stats_for_topic(async_db_session, uuid4())
    assert report.quiz_version_id is None
    assert report.minimum_n == 20
    assert report.items == []


async def test_counted_attempts_report_rates_and_low_discrimination(
    async_db_session: AsyncSession,
) -> None:
    seeded = await _seed(async_db_session, _balanced_rows())
    stats = await item_stats_for_quiz_version(async_db_session, seeded.quiz_version_id)
    via_topic = await item_stats_for_topic(async_db_session, seeded.topic_id)

    assert via_topic.quiz_version_id == seeded.quiz_version_id
    assert via_topic.minimum_n == 20
    assert via_topic.items == stats
    assert [item.prompt for item in stats] == [FOCAL, COMPANION]
    assert [item.sequence for item in stats] == [1, 2]

    item = _focal(stats)
    assert item.question_version_id == seeded.focal_id
    assert item.correct_option_label == "A"
    assert item.n == 20
    assert item.p_correct == pytest.approx(0.5)
    assert item.discrimination == pytest.approx(0.0, abs=1e-9)
    assert item.flags == ["low_discrimination"]
    assert [option.label for option in item.options] == ["A", "B", "C", "D"]
    assert _option(item, "A").is_key is True
    assert _option(item, "A").text == "Alpha"
    assert _option(item, "B").is_key is False
    assert _option(item, "A").pick_rate == pytest.approx(0.5)
    assert _option(item, "B").pick_rate == pytest.approx(0.05)
    assert _option(item, "C").pick_rate == pytest.approx(0.2)
    assert _option(item, "D").pick_rate == pytest.approx(0.25)
    assert _option(item, "A").top_group_pick_rate == pytest.approx(1)
    assert _option(item, "B").top_group_pick_rate == pytest.approx(0)
    assert _option(item, "A").bottom_group_pick_rate == pytest.approx(0)
    assert _option(item, "C").bottom_group_pick_rate == pytest.approx(0.4)
    assert _option(item, "D").bottom_group_pick_rate == pytest.approx(0.6)

    dumped = item.model_dump(mode="json")
    assert set(dumped) == {
        "question_version_id",
        "sequence",
        "prompt",
        "correct_option_label",
        "n",
        "p_correct",
        "discrimination",
        "options",
        "flags",
    }
    assert isinstance(dumped["question_version_id"], str)
    assert set(dumped["options"][0]) == {
        "label",
        "text",
        "is_key",
        "pick_rate",
        "top_group_pick_rate",
        "bottom_group_pick_rate",
    }


async def test_rest_score_uses_other_item_marks_when_the_total_is_missing(
    async_db_session: AsyncSession,
) -> None:
    seeded = await _seed(async_db_session, _balanced_rows(), roll_up_score=False)
    item = _focal(await item_stats_for_quiz_version(async_db_session, seeded.quiz_version_id))
    assert item.n == 20
    assert item.p_correct == pytest.approx(0.5)
    assert item.discrimination == pytest.approx(0.0, abs=1e-9)
    assert _option(item, "A").top_group_pick_rate == pytest.approx(1)
    assert _option(item, "C").bottom_group_pick_rate == pytest.approx(0.4)


async def test_too_easy_when_everyone_is_correct(async_db_session: AsyncSession) -> None:
    seeded = await _seed(async_db_session, [Row("A", 0) for _ in range(20)])
    item = _focal(await item_stats_for_quiz_version(async_db_session, seeded.quiz_version_id))
    assert item.n == 20
    assert item.p_correct == pytest.approx(1)
    assert item.discrimination is None
    assert item.flags == ["too_easy", "dead_distractor"]


async def test_too_hard_when_almost_nobody_is_correct(async_db_session: AsyncSession) -> None:
    rows = [Row("A", 1) for _ in range(3)] + [Row("B", 0) for _ in range(17)]
    seeded = await _seed(async_db_session, rows)
    item = _focal(await item_stats_for_quiz_version(async_db_session, seeded.quiz_version_id))
    assert item.n == 20
    assert item.p_correct == pytest.approx(0.15)
    assert item.discrimination == pytest.approx(1)
    assert item.flags == ["too_hard", "dead_distractor"]
    assert _option(item, "A").top_group_pick_rate == pytest.approx(0.6)
    assert _option(item, "B").top_group_pick_rate == pytest.approx(0.4)


async def test_negative_discrimination(async_db_session: AsyncSession) -> None:
    seeded = await _seed(async_db_session, _negative_rows())
    item = _focal(await item_stats_for_quiz_version(async_db_session, seeded.quiz_version_id))
    assert item.p_correct == pytest.approx(0.5)
    assert item.discrimination == pytest.approx(-1)
    assert item.flags == [
        "low_discrimination",
        "negative_discrimination",
        "dead_distractor",
        "distractor_beats_key_top_group",
    ]


async def test_dead_distractor_when_a_wrong_option_is_unused(
    async_db_session: AsyncSession,
) -> None:
    rows = [Row("A", 1) for _ in range(10)] + [Row("B", 0) for _ in range(5)]
    rows += [Row("C", 0) for _ in range(5)]
    seeded = await _seed(async_db_session, rows)
    item = _focal(await item_stats_for_quiz_version(async_db_session, seeded.quiz_version_id))
    assert item.discrimination == pytest.approx(1)
    assert item.flags == ["dead_distractor"]
    assert _option(item, "D").pick_rate == pytest.approx(0)
    assert _option(item, "B").pick_rate == pytest.approx(0.25)


async def test_distractor_beats_key_in_the_top_group(async_db_session: AsyncSession) -> None:
    seeded = await _seed(async_db_session, _negative_rows())
    item = _focal(await item_stats_for_quiz_version(async_db_session, seeded.quiz_version_id))
    assert "distractor_beats_key_top_group" in item.flags
    assert _option(item, "B").top_group_pick_rate == pytest.approx(1)
    assert _option(item, "A").top_group_pick_rate == pytest.approx(0)


async def test_boundary_proportions_are_not_flagged(async_db_session: AsyncSession) -> None:
    easy = await _seed(async_db_session, [Row("A", 0) for _ in range(19)] + [Row("B", 0)])
    easy_item = _focal(await item_stats_for_quiz_version(async_db_session, easy.quiz_version_id))
    assert easy_item.n == 20
    assert easy_item.p_correct == pytest.approx(0.95)
    assert "too_easy" not in easy_item.flags

    hard_rows = [Row("A", 1) for _ in range(4)] + [Row("B", 0) for _ in range(16)]
    hard = await _seed(async_db_session, hard_rows)
    hard_item = _focal(await item_stats_for_quiz_version(async_db_session, hard.quiz_version_id))
    assert hard_item.p_correct == pytest.approx(0.2)
    assert "too_hard" not in hard_item.flags


async def test_nothing_is_flagged_below_the_minimum(async_db_session: AsyncSession) -> None:
    rows = [Row("A", 0) for _ in range(19)]
    rows += [
        Row("B", 1, QuizAttemptStatus.IN_PROGRESS),
        Row("B", 1, QuizAttemptStatus.ABANDONED),
        Row("B", 1, QuizAttemptStatus.EXPIRED),
        Row("B", 1, QuizAttemptStatus.HELD),
        Row("B", 1, QuizAttemptStatus.NOT_STARTED),
    ]
    seeded = await _seed(async_db_session, rows)
    item = _focal(await item_stats_for_quiz_version(async_db_session, seeded.quiz_version_id))
    assert item.n == 19
    assert item.p_correct == pytest.approx(1)
    assert item.flags == []


async def test_topic_stats_use_the_latest_released_topic_mastery_quiz(
    async_db_session: AsyncSession,
) -> None:
    topic, subtopic, student = await _topic_tree(async_db_session)
    subtopic_question = await _question(async_db_session, subtopic.id, "Subtopic stem")
    subtopic_quiz = CommonMasteryQuiz(
        quiz_scope=QuizScope.SUBTOPIC_MASTERY,
        subtopic_id=subtopic.id,
        title="Subtopic mastery",
    )
    session = async_db_session
    session.add(subtopic_quiz)
    await session.flush()
    subtopic_version = QuizVersion(
        quiz_id=subtopic_quiz.id,
        version_number=1,
        lifecycle_status=QuizVersionStatus.RELEASED,
    )
    session.add(subtopic_version)
    await session.flush()
    session.add(
        QuizItem(
            quiz_version_id=subtopic_version.id,
            question_version_id=subtopic_question.id,
            sequence=1,
        )
    )
    await session.flush()

    empty = await item_stats_for_topic(session, topic.id)
    assert empty.quiz_version_id is None
    assert empty.items == []

    topic_quiz = CommonMasteryQuiz(
        quiz_scope=QuizScope.TOPIC_MASTERY,
        topic_id=topic.id,
        title="Topic mastery",
    )
    session.add(topic_quiz)
    await session.flush()
    draft_question = await _question(session, subtopic.id, "Draft stem")
    draft = QuizVersion(
        quiz_id=topic_quiz.id,
        version_number=1,
        lifecycle_status=QuizVersionStatus.DRAFT,
    )
    session.add(draft)
    await session.flush()
    session.add(
        QuizItem(
            quiz_version_id=draft.id,
            question_version_id=draft_question.id,
            sequence=1,
        )
    )
    await session.flush()
    still_empty = await item_stats_for_topic(session, topic.id)
    assert still_empty.quiz_version_id is None

    first_question = await _question(session, subtopic.id, "Version one")
    companion = await _question(session, subtopic.id, COMPANION)
    first = QuizVersion(
        quiz_id=topic_quiz.id,
        version_number=2,
        lifecycle_status=QuizVersionStatus.RELEASED,
    )
    session.add(first)
    await session.flush()
    session.add_all(
        [
            QuizItem(quiz_version_id=first.id, question_version_id=first_question.id, sequence=1),
            QuizItem(quiz_version_id=first.id, question_version_id=companion.id, sequence=2),
        ]
    )
    await session.flush()
    await _attempts(
        session,
        student_id=student.id,
        quiz_version_id=first.id,
        focal_id=first_question.id,
        companion_id=companion.id,
        rows=[Row("A", 0) for _ in range(20)],
    )
    first_report = await item_stats_for_topic(session, topic.id)
    assert first_report.quiz_version_id == first.id
    assert [item.prompt for item in first_report.items] == ["Version one", COMPANION]
    assert first_report.items[0].flags == ["too_easy", "dead_distractor"]

    second_question = await _question(session, subtopic.id, "Version two")
    second = QuizVersion(
        quiz_id=topic_quiz.id,
        version_number=3,
        lifecycle_status=QuizVersionStatus.RELEASED,
    )
    session.add(second)
    await session.flush()
    session.add(
        QuizItem(
            quiz_version_id=second.id,
            question_version_id=second_question.id,
            sequence=1,
        )
    )
    await session.flush()
    second_report = await item_stats_for_topic(session, topic.id)
    assert second_report.quiz_version_id == second.id
    assert [item.prompt for item in second_report.items] == ["Version two"]
    assert second_report.items[0].n == 0
    assert second_report.items[0].p_correct is None
    assert second_report.items[0].flags == []


def _poc_admin_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "admin@demo.school",
            "password": "demo1234",
            "institution_name": "POC Demo School",
        },
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_item_stats_route_is_admin_only(
    client: TestClient,
    seeded_db: Session,
    enrolled_student_headers: dict[str, str],
) -> None:
    quiz = seeded_db.scalar(
        select(CommonMasteryQuiz).where(CommonMasteryQuiz.quiz_scope == QuizScope.TOPIC_MASTERY)
    )
    assert quiz is not None and quiz.topic_id is not None
    url = f"/api/v1/admin/topics/{quiz.topic_id}/item-stats"

    anonymous = client.get(url)
    assert anonymous.status_code == 401

    student = client.get(url, headers=enrolled_student_headers)
    assert student.status_code == 403

    admin = client.get(url, headers=_poc_admin_headers(client))
    assert admin.status_code == 200, admin.text
    body = admin.json()
    assert body["topic_id"] == str(quiz.topic_id)
    assert body["minimum_n"] == 20
    assert body["quiz_version_id"]
    assert isinstance(body["items"], list)
    report = TopicItemStats.model_validate(body)
    assert report.topic_id == quiz.topic_id
    if report.items:
        item = body["items"][0]
        assert set(item) == {
            "question_version_id",
            "sequence",
            "prompt",
            "correct_option_label",
            "n",
            "p_correct",
            "discrimination",
            "options",
            "flags",
        }
        assert isinstance(item["prompt"], str)
        assert isinstance(item["flags"], list)
        if item["options"]:
            assert set(item["options"][0]) == {
                "label",
                "text",
                "is_key",
                "pick_rate",
                "top_group_pick_rate",
                "bottom_group_pick_rate",
            }


def test_item_stats_route_hides_other_institution_topics(
    client: TestClient,
    seeded_db: Session,
) -> None:
    """Administrators must not read answer keys for another tenant's topic UUID."""
    institution = Institution(name=f"Foreign Stats {uuid4().hex[:8]}")
    seeded_db.add(institution)
    seeded_db.flush()
    period = AcademicPeriod(
        institution_id=institution.id,
        name="2026-27",
        start_date=date(2026, 6, 1),
        end_date=date(2027, 3, 31),
        status=AcademicPeriodStatus.ACTIVE,
    )
    grade = Grade(institution_id=institution.id, name="Grade 8", sort_order=8)
    subject = Subject(institution_id=institution.id, name="Mathematics", code="MATH")
    seeded_db.add_all([period, grade, subject])
    seeded_db.flush()
    period_grade = PeriodGrade(academic_period_id=period.id, grade_id=grade.id)
    seeded_db.add(period_grade)
    seeded_db.flush()
    offering = GradeSubjectOffering(period_grade_id=period_grade.id, subject_id=subject.id)
    seeded_db.add(offering)
    seeded_db.flush()
    topic = Topic(
        grade_subject_offering_id=offering.id,
        name="Foreign Numbers",
        slug="foreign-numbers",
        sequence=1,
    )
    seeded_db.add(topic)
    seeded_db.commit()

    response = client.get(
        f"/api/v1/admin/topics/{topic.id}/item-stats",
        headers=_poc_admin_headers(client),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "That topic does not exist."
