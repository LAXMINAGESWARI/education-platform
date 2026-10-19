"""Topic goals: status, collapse of legacy subtopic rows, and the HTTP API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.modules.academics.models import Topic
from education_platform.modules.assessments.goal_collapse import (
    SubtopicGoalSource,
    collapse_subtopic_goals,
)
from education_platform.modules.assessments.goals_service import compute_goal_status
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    QuizAttempt,
    QuizAttemptStatus,
    QuizScope,
    QuizVersion,
)
from education_platform.modules.auth.models import StudentProfile, User

DUE = datetime(2026, 6, 1, tzinfo=UTC)
BEFORE_DUE = DUE - timedelta(days=1)
AFTER_DUE = DUE + timedelta(days=1)


def test_achieved_when_current_meets_target() -> None:
    status = compute_goal_status(Decimal("70"), Decimal("70"), DUE, BEFORE_DUE)
    assert status == "achieved"


def test_achieved_when_current_exceeds_target_even_past_due() -> None:
    """Hitting the target always wins, even if it happened after the deadline."""
    status = compute_goal_status(Decimal("95"), Decimal("70"), DUE, AFTER_DUE)
    assert status == "achieved"


def test_on_track_before_due_and_below_target() -> None:
    status = compute_goal_status(Decimal("50"), Decimal("70"), DUE, BEFORE_DUE)
    assert status == "on_track"


def test_missed_after_due_and_below_target() -> None:
    status = compute_goal_status(Decimal("50"), Decimal("70"), DUE, AFTER_DUE)
    assert status == "missed"


def test_on_track_with_no_attempts_yet() -> None:
    """No scored attempts on the subtopic yet -> current_percent is None, not a failure."""
    status = compute_goal_status(None, Decimal("70"), DUE, BEFORE_DUE)
    assert status == "on_track"


def test_missed_with_no_attempts_and_past_due() -> None:
    status = compute_goal_status(None, Decimal("70"), DUE, AFTER_DUE)
    assert status == "missed"


def test_collapse_keeps_highest_target_and_latest_due_per_topic() -> None:
    student = UUID(int=1)
    other = UUID(int=2)
    topic = UUID(int=10)
    other_topic = UUID(int=11)
    early = datetime(2026, 6, 1, tzinfo=UTC)
    late = datetime(2026, 8, 1, tzinfo=UTC)
    low = UUID(int=3)
    high = UUID(int=4)
    collapsed = collapse_subtopic_goals(
        [
            SubtopicGoalSource(high, student, topic, Decimal("70"), late),
            SubtopicGoalSource(low, student, topic, Decimal("90"), early),
            SubtopicGoalSource(UUID(int=5), student, other_topic, Decimal("60"), early),
            SubtopicGoalSource(UUID(int=6), other, topic, Decimal("75"), early),
        ]
    )
    by_key = {(row.student_id, row.topic_id): row for row in collapsed}
    kept = by_key[(student, topic)]
    assert kept.keep_id == low
    assert kept.target_percent == Decimal("90")
    assert kept.due_at == late
    assert kept.drop_ids == (high,)
    assert by_key[(student, other_topic)].drop_ids == ()
    assert by_key[(other, topic)].target_percent == Decimal("75")


def test_set_update_and_remove_topic_goal(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    overview = client.get("/api/v1/me/feedback", headers=enrolled_student_headers)
    assert overview.status_code == 200, overview.text
    math = next(row for row in overview.json()["subjects"] if row["subject_name"] == "Mathematics")
    subject = client.get(
        f"/api/v1/me/feedback/subjects/{math['subject_id']}",
        headers=enrolled_student_headers,
    )
    assert subject.status_code == 200, subject.text
    topic_row = subject.json()["topics"][0]
    topic_id = topic_row["topic_id"]
    due = (datetime.now(UTC) + timedelta(days=30)).isoformat()

    created = client.put(
        f"/api/v1/topics/{topic_id}/goal",
        headers=enrolled_student_headers,
        json={"target_percent": "80", "due_at": due},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["topic_id"] == topic_id
    assert Decimal(body["target_percent"]) == Decimal("80")
    assert body["current_percent"] is None
    assert body["status"] == "on_track"
    before_attempt = client.get(
        f"/api/v1/me/feedback/subjects/{math['subject_id']}",
        headers=enrolled_student_headers,
    )
    assert before_attempt.json()["focus"] is None

    past = client.put(
        f"/api/v1/topics/{topic_id}/goal",
        headers=enrolled_student_headers,
        json={
            "target_percent": "80",
            "due_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    )
    assert past.status_code == 422

    _score_topic(seeded_db, UUID(topic_id), Decimal("60.00"), Decimal("100.00"))

    detail = client.get(f"/api/v1/me/feedback/topics/{topic_id}", headers=enrolled_student_headers)
    assert detail.status_code == 200, detail.text
    goal = detail.json()["goal"]
    assert Decimal(goal["current_percent"]) == Decimal("60.00")
    assert goal["status"] == "on_track"
    assert all("goal" not in row for row in detail.json()["subtopics"])

    subject_after = client.get(
        f"/api/v1/me/feedback/subjects/{math['subject_id']}",
        headers=enrolled_student_headers,
    )
    assert subject_after.status_code == 200, subject_after.text
    scored = next(row for row in subject_after.json()["topics"] if row["topic_id"] == topic_id)
    assert Decimal(scored["goal"]["current_percent"]) == Decimal("60.00")
    focus = subject_after.json()["focus"]
    assert focus["topic_id"] == topic_id
    assert focus["topic_name"] == topic_row["topic_name"]
    assert focus["gap_percent"] == 20

    updated = client.put(
        f"/api/v1/topics/{topic_id}/goal",
        headers=enrolled_student_headers,
        json={"target_percent": "50", "due_at": due},
    )
    assert updated.status_code == 200, updated.text
    assert Decimal(updated.json()["target_percent"]) == Decimal("50")
    assert updated.json()["status"] == "achieved"
    assert updated.json()["current_percent"] is not None
    achieved_subject = client.get(
        f"/api/v1/me/feedback/subjects/{math['subject_id']}",
        headers=enrolled_student_headers,
    )
    assert achieved_subject.json()["focus"] is None

    removed = client.delete(
        f"/api/v1/topics/{topic_id}/goal",
        headers=enrolled_student_headers,
    )
    assert removed.status_code == 204
    gone = client.get(f"/api/v1/me/feedback/topics/{topic_id}", headers=enrolled_student_headers)
    assert gone.json()["goal"] is None
    missing = client.delete(
        f"/api/v1/topics/{topic_id}/goal",
        headers=enrolled_student_headers,
    )
    assert missing.status_code == 404
    retired = client.put(
        f"/api/v1/subtopics/{topic_id}/goal",
        headers=enrolled_student_headers,
        json={"target_percent": "80", "due_at": due},
    )
    assert retired.status_code == 404


def _score_topic(seeded_db: Session, topic_id: UUID, visible: Decimal, held: Decimal) -> None:
    profile = seeded_db.scalar(
        select(StudentProfile)
        .join(User, User.id == StudentProfile.user_id)
        .where(User.email == "student@example.com")
    )
    assert profile is not None
    topic = seeded_db.get(Topic, topic_id)
    assert topic is not None
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
    seeded_db.add(
        QuizAttempt(
            student_id=profile.id,
            quiz_version_id=version.id,
            attempt_number=1,
            status=QuizAttemptStatus.RELEASED,
            submitted_at=datetime(2026, 8, 1, tzinfo=UTC),
            scored_at=datetime(2026, 8, 1, tzinfo=UTC),
            score_percent=visible,
            pass_threshold_percent=Decimal("70.00"),
            passed=visible >= Decimal("70"),
        )
    )
    seeded_db.add(
        QuizAttempt(
            student_id=profile.id,
            quiz_version_id=version.id,
            attempt_number=2,
            status=QuizAttemptStatus.HELD,
            score_percent=held,
            pass_threshold_percent=Decimal("70.00"),
            passed=held >= Decimal("70"),
        )
    )
    seeded_db.commit()
