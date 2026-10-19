"""Topic-quiz feedback aggregation: means, missing attempts, and cleaned names."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.modules.academics.models import Subtopic, Topic
from education_platform.modules.assessments.feedback_hierarchy import (
    ScoredAttempt,
    feedback_concept_label,
    group_subtopic_strengths,
    rollup_subject,
    rollup_topic,
)
from education_platform.modules.assessments.models import (
    AttemptAnswer,
    CommonMasteryQuiz,
    Question,
    QuestionVersion,
    QuizAttempt,
    QuizAttemptStatus,
    QuizItem,
    QuizScope,
    QuizVersion,
)
from education_platform.modules.auth.models import StudentProfile, User

STUDENT_EMAIL = "student@example.com"


def test_topic_with_no_attempts_is_not_scored() -> None:
    rollup = rollup_topic([])
    assert rollup.best_score_percent is None
    assert rollup.latest_score_percent is None
    assert rollup.trend is None
    assert rollup.trend_delta is None
    assert rollup.badge == "not_attempted"
    assert rollup.attempt_count == 0


def test_one_attempt_has_a_best_score_and_no_trend() -> None:
    rollup = rollup_topic([ScoredAttempt(1, Decimal("40"))])
    assert rollup.best_score_percent == Decimal("40")
    assert rollup.latest_score_percent == Decimal("40")
    assert rollup.trend is None
    assert rollup.trend_delta is None
    assert rollup.badge == "keep_going"


def test_trend_compares_the_latest_attempt_with_the_previous_one() -> None:
    # Mensuration-style history: four attempts, latest is not the best.
    rollup = rollup_topic(
        [
            ScoredAttempt(1, Decimal("80")),
            ScoredAttempt(2, Decimal("60")),
            ScoredAttempt(3, Decimal("70")),
            ScoredAttempt(4, Decimal("55")),
        ]
    )
    assert rollup.best_score_percent == Decimal("80")
    assert rollup.latest_score_percent == Decimal("55")
    assert rollup.trend == "declining"
    assert rollup.trend_delta == Decimal("-15.00")
    assert rollup.attempt_count == 4
    assert rollup.badge == "passed"


def test_subject_score_is_the_mean_of_attempted_topics_only() -> None:
    mensuration = rollup_topic([ScoredAttempt(1, Decimal("40")), ScoredAttempt(2, Decimal("80"))])
    untouched = rollup_topic([])
    algebra = rollup_topic([ScoredAttempt(1, Decimal("60"))])
    subject = rollup_subject(
        [
            (UUID(int=1), "Mensuration", mensuration),
            (UUID(int=2), "Decimals", untouched),
            (UUID(int=3), "Algebra", algebra),
        ]
    )
    assert subject.score_percent == Decimal("70.00")
    assert subject.quizzes_taken == 3
    assert subject.trend == "improving"
    assert subject.trend_delta == Decimal("40.00")
    assert subject.strongest is not None and subject.strongest[1] == "Mensuration"
    assert subject.weakest is not None and subject.weakest[1] == "Algebra"
    assert subject.needing_attention == ("Algebra",)


def test_one_attempted_topic_is_not_also_the_weakest() -> None:
    subject = rollup_subject(
        [(UUID(int=1), "Mensuration", rollup_topic([ScoredAttempt(1, Decimal("80"))]))]
    )
    assert subject.score_percent == Decimal("80")
    assert subject.strongest is not None and subject.strongest[1] == "Mensuration"
    assert subject.weakest is None
    assert subject.trend is None


def test_all_subjects_mean_skips_subjects_with_no_attempts() -> None:
    math = rollup_subject(
        [(UUID(int=1), "Mensuration", rollup_topic([ScoredAttempt(1, Decimal("80"))]))]
    )
    science = rollup_subject([(UUID(int=2), "Cells", rollup_topic([]))])
    scores = [row.score_percent for row in (math, science) if row.score_percent is not None]
    assert scores == [Decimal("80")]
    assert sum(scores, Decimal("0")) / Decimal(len(scores)) == Decimal("80")


def test_filler_headings_fold_into_the_topic_and_numbers_are_stripped() -> None:
    assert feedback_concept_label("9.4.1 Cuboid") == "Cuboid"
    assert feedback_concept_label("9.6 Volume and Capacity") == "Volume and capacity"
    assert feedback_concept_label("EXERCISE 9.3") is None
    assert feedback_concept_label("EXERCISE 9.1") is None
    assert feedback_concept_label("Solution: Aman's method") is None
    assert feedback_concept_label("Ridhima's method") is None
    assert feedback_concept_label("WHAT HAVE WE DISCUSSED?") is None
    assert feedback_concept_label("TRY THESE") is None

    rows = group_subtopic_strengths(
        [
            ("9.4.1 Cuboid", 1, 1, 1),
            ("9.5.1 Cuboid", 2, 2, 3),
            ("EXERCISE 9.3", 3, 1, 2),
            ("Ridhima's method", 4, 0, 4),
            ("9.2 Area of a Polygon", 5, 1, 4),
        ]
    )
    by_name = {row.name: row for row in rows}
    assert set(by_name) == {"Cuboid", "Area of a polygon"}
    assert by_name["Cuboid"].correct_count == 3
    assert by_name["Cuboid"].answered_count == 4
    assert by_name["Cuboid"].band == "strong"
    assert by_name["Area of a polygon"].band == "needs_work"


def _student(seeded_db: Session) -> StudentProfile:
    profile = seeded_db.scalar(
        select(StudentProfile)
        .join(User, User.id == StudentProfile.user_id)
        .where(User.email == STUDENT_EMAIL)
    )
    assert profile is not None
    return profile


def test_seeded_topic_quiz_reports_four_attempts_and_an_unattempted_topic(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    before = client.get("/api/v1/me/feedback", headers=enrolled_student_headers)
    assert before.status_code == 200, before.text
    body = before.json()
    assert body["score_percent"] is None
    math = next(row for row in body["subjects"] if row["subject_name"] == "Mathematics")
    assert math["score_percent"] is None
    assert math["trend"] is None
    assert math["quizzes_taken"] == 0
    seeded = next(
        topic for topic in _subject_topics(client, enrolled_student_headers, math["subject_id"])
    )
    assert seeded["best_score_percent"] is None
    assert seeded["badge"] == "not_attempted"

    profile = _student(seeded_db)
    topic = seeded_db.get(Topic, UUID(seeded["topic_id"]))
    assert topic is not None
    offering_id = topic.grade_subject_offering_id
    seeded_db.add(
        Topic(
            grade_subject_offering_id=offering_id,
            name="Mensuration",
            slug="mensuration",
            sequence=topic.sequence + 1,
        )
    )
    quiz = seeded_db.scalar(
        select(CommonMasteryQuiz).where(
            CommonMasteryQuiz.topic_id == topic.id,
            CommonMasteryQuiz.quiz_scope == QuizScope.TOPIC_MASTERY,
        )
    )
    assert quiz is not None
    version = seeded_db.scalar(
        select(QuizVersion)
        .where(QuizVersion.quiz_id == quiz.id)
        .order_by(QuizVersion.version_number.desc())
    )
    assert version is not None
    scores = [Decimal("40.00"), Decimal("55.00"), Decimal("70.00"), Decimal("90.00")]
    attempts: list[QuizAttempt] = []
    for number, score in enumerate(scores, start=1):
        attempt = QuizAttempt(
            student_id=profile.id,
            quiz_version_id=version.id,
            attempt_number=number,
            status=QuizAttemptStatus.RELEASED,
            submitted_at=datetime(2026, 8, number, tzinfo=UTC),
            scored_at=datetime(2026, 8, number, tzinfo=UTC),
            score_percent=score,
            pass_threshold_percent=Decimal("70.00"),
            passed=score >= Decimal("70"),
        )
        seeded_db.add(attempt)
        attempts.append(attempt)
    held = QuizAttempt(
        student_id=profile.id,
        quiz_version_id=version.id,
        attempt_number=5,
        status=QuizAttemptStatus.HELD,
        score_percent=Decimal("0.00"),
        pass_threshold_percent=Decimal("70.00"),
        passed=False,
    )
    seeded_db.add(held)
    seeded_db.flush()

    item_rows = seeded_db.execute(
        select(QuestionVersion.id, Subtopic.id)
        .join(QuizItem, QuizItem.question_version_id == QuestionVersion.id)
        .join(Question, Question.id == QuestionVersion.question_id)
        .join(Subtopic, Subtopic.id == Question.subtopic_id)
        .where(QuizItem.quiz_version_id == version.id)
        .order_by(QuizItem.sequence)
    ).all()
    assert item_rows
    cuboid_id = item_rows[0][1]
    cuboid = seeded_db.get(Subtopic, cuboid_id)
    assert cuboid is not None
    cuboid.name = "9.4.1 Cuboid"
    if len({row[1] for row in item_rows}) > 1:
        other = seeded_db.get(Subtopic, item_rows[-1][1])
        assert other is not None
        other.name = "EXERCISE 9.3"
    seeded_db.flush()
    for question_version_id, subtopic_id in item_rows:
        correct = subtopic_id == cuboid_id
        seeded_db.add(
            AttemptAnswer(
                attempt_id=attempts[-1].id,
                question_version_id=question_version_id,
                selected_option_label="A" if correct else "B",
                is_correct=correct,
                marks_awarded=Decimal("1.00") if correct else Decimal("0.00"),
            )
        )
    seeded_db.commit()

    overview = client.get("/api/v1/me/feedback", headers=enrolled_student_headers)
    assert overview.status_code == 200, overview.text
    math = next(row for row in overview.json()["subjects"] if row["subject_name"] == "Mathematics")
    assert Decimal(math["score_percent"]) == Decimal("90.00")
    assert overview.json()["score_percent"] == math["score_percent"]
    assert math["quizzes_taken"] == 4
    assert math["trend"] == "improving"
    assert Decimal(math["trend_delta"]) == Decimal("20.00")
    assert math["strongest_topic"]["topic_name"] != "Mensuration"

    detail = client.get(
        f"/api/v1/me/feedback/subjects/{math['subject_id']}",
        headers=enrolled_student_headers,
    )
    assert detail.status_code == 200, detail.text
    topics = detail.json()["topics"]
    names = [row["topic_name"] for row in topics]
    assert names.index(seeded["topic_name"]) < names.index("Mensuration")
    mensuration = next(row for row in topics if row["topic_name"] == "Mensuration")
    assert mensuration["badge"] == "not_attempted"
    assert mensuration["best_score_percent"] is None
    assert mensuration["trend"] is None
    scored = next(row for row in topics if row["topic_id"] == seeded["topic_id"])
    assert Decimal(scored["best_score_percent"]) == Decimal("90.00")
    assert Decimal(scored["latest_score_percent"]) == Decimal("90.00")
    assert scored["attempt_count"] == 4
    assert scored["trend"] == "improving"
    assert "declining" not in (scored["trend"] or "")

    topic_view = client.get(
        f"/api/v1/me/feedback/topics/{seeded['topic_id']}",
        headers=enrolled_student_headers,
    )
    assert topic_view.status_code == 200, topic_view.text
    payload = topic_view.json()
    labels = [row["name"] for row in payload["subtopics"]]
    assert "Cuboid" in labels
    assert "9.4.1 Cuboid" not in labels
    assert not any(label.upper().startswith("EXERCISE") for label in labels)
    cuboid_row = next(row for row in payload["subtopics"] if row["name"] == "Cuboid")
    assert cuboid_row["band"] == "strong"


def _subject_topics(
    client: TestClient, headers: dict[str, str], subject_id: str
) -> list[dict[str, object]]:
    response = client.get(f"/api/v1/me/feedback/subjects/{subject_id}", headers=headers)
    assert response.status_code == 200, response.text
    topics = response.json()["topics"]
    assert isinstance(topics, list)
    return topics
