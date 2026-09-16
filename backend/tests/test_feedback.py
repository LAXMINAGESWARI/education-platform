from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.modules.assessments import feedback_service
from education_platform.modules.assessments.models import QuestionAnswerKey, QuizItem
from education_platform.modules.academics.models import Subject
from education_platform.modules.materials.seed import POC_INSTITUTION_NAME, POC_SUBJECT_CODE


@pytest.fixture(autouse=True)
def _fake_summary_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(_prompt: str) -> str:
        return "Fake summary for tests."

    monkeypatch.setattr(feedback_service, "_openrouter_summary_writer", _fake)


def _submit_perfect_attempt(
    client: TestClient, headers: dict[str, str], seeded_db: Session
) -> tuple[str, str]:
    """Complete the square-numbers-patterns quiz with every answer correct.

    Returns (subject_id, attempt_id).
    """
    quiz = client.get(
        "/api/v1/materials/square_numbers_patterns/quiz", headers=headers
    )
    assert quiz.status_code == 200
    quiz_id = quiz.json()["id"]

    directory = client.get("/api/v1/me/learning-directory", headers=headers)
    assert directory.status_code == 200
    subjects = directory.json()["subjects"]
    subject_id = subjects[0]["id"]
    subtopic_id = next(
        subtopic["id"]
        for subject in subjects
        for topic in subject["topics"]
        for subtopic in topic["subtopics"]
        if subtopic["slug"] == "square_numbers_patterns"
    )
    progress = client.put(
        f"/api/v1/subtopics/{subtopic_id}/material-progress",
        headers=headers,
        json={"status": "completed"},
    )
    assert progress.status_code == 200, progress.text

    start = client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=headers)
    assert start.status_code == 200
    attempt_id = start.json()["id"]
    attempt_version_id = start.json()["quiz_version_id"]

    items = seeded_db.scalars(
        select(QuizItem)
        .where(QuizItem.quiz_version_id == UUID(str(attempt_version_id)))
        .order_by(QuizItem.sequence)
    ).all()
    answers = []
    for item in items:
        key = seeded_db.scalar(
            select(QuestionAnswerKey).where(
                QuestionAnswerKey.question_version_id == item.question_version_id
            )
        )
        assert key is not None and key.correct_option_label is not None
        answers.append(
            {
                "question_number": item.sequence,
                "selected_option_label": key.correct_option_label,
            }
        )

    submit = client.post(
        f"/api/v1/attempts/{attempt_id}/submit",
        headers=headers,
        json={"answers": answers},
    )
    assert submit.status_code == 200, submit.text
    return subject_id, attempt_id


def test_subject_feedback_reflects_scored_attempt(
    client: TestClient, enrolled_student_headers: dict[str, str], seeded_db: Session
) -> None:
    subject_id, _attempt_id = _submit_perfect_attempt(client, enrolled_student_headers, seeded_db)

    feedback = client.get(
        f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers
    )
    assert feedback.status_code == 200, feedback.text
    payload = feedback.json()
    assert payload["subject_name"] == "Mathematics"
    assert payload["attempt_count"] == 1
    assert float(payload["overall_percent"]) == 100.0
    assert len(payload["subtopics"]) == 1
    assert payload["subtopics"][0]["is_weak"] is False
    assert payload["summary"] == "Fake summary for tests."


def test_subject_feedback_404_for_unenrolled_subject(
    client: TestClient, seeded_db: Session
) -> None:
    provision = client.post(
        "/api/v1/auth/provision-student",
        json={
            "email": "no-subject@example.com",
            "password": "password123",
            "full_name": "No Subject",
            "student_identifier": "S-nosub",
            "institution_name": POC_INSTITUTION_NAME,
        },
    )
    assert provision.status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "no-subject@example.com", "password": "password123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Not enrolled in any subject: the seeded Mathematics subject should 404, not leak data.
    directory = client.get("/api/v1/me/enrollments", headers=headers)
    assert directory.status_code == 200
    assert directory.json()["subject_enrollments"] == []

    subject = seeded_db.scalar(select(Subject).where(Subject.code == POC_SUBJECT_CODE))
    assert subject is not None
    feedback = client.get(f"/api/v1/subjects/{subject.id}/feedback", headers=headers)
    assert feedback.status_code == 404
