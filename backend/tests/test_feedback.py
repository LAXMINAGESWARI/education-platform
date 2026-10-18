from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.core.llm import OpenRouterError
from education_platform.modules.academics.models import Subject
from education_platform.modules.assessments import feedback_service
from education_platform.modules.assessments.models import (
    QuestionAnswerKey,
    QuizItem,
    SubjectFeedbackSummary,
)
from education_platform.modules.materials.seed import POC_INSTITUTION_NAME, POC_SUBJECT_CODE


@pytest.fixture(autouse=True)
def _fake_summary_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(_prompt: str) -> str:
        return "Fake summary for tests."

    monkeypatch.setattr(feedback_service, "_openrouter_summary_writer", _fake)


def _submit_attempt(
    client: TestClient,
    headers: dict[str, str],
    seeded_db: Session,
    *,
    correct: bool,
) -> tuple[str, str]:
    """Complete the square-numbers-patterns quiz.

    Returns (subject_id, attempt_id).
    """
    quiz = client.get("/api/v1/materials/square_numbers_patterns/quiz", headers=headers)
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
        selected = key.correct_option_label
        if not correct:
            selected = "A" if key.correct_option_label != "A" else "B"
        answers.append(
            {
                "question_number": item.sequence,
                "selected_option_label": selected,
            }
        )

    submit = client.post(
        f"/api/v1/attempts/{attempt_id}/submit",
        headers=headers,
        json={"answers": answers},
    )
    assert submit.status_code == 200, submit.text
    return subject_id, attempt_id


def _submit_perfect_attempt(
    client: TestClient, headers: dict[str, str], seeded_db: Session
) -> tuple[str, str]:
    return _submit_attempt(client, headers, seeded_db, correct=True)


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
    # The response is sent before OpenRouter runs. The first body is the template;
    # the background task stores the model summary for the next read of the same facts.
    assert payload["summary"].startswith("You're doing well in")
    assert payload["summary"] != "Fake summary for tests."

    cached = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert cached.status_code == 200, cached.text
    assert cached.json()["summary"] == "Fake summary for tests."


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


def test_feedback_without_attempts_does_not_call_the_summary_writer(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def _fake(_prompt: str) -> str:
        calls["n"] += 1
        return "should not run"

    monkeypatch.setattr(feedback_service, "_openrouter_summary_writer", _fake)
    directory = client.get("/api/v1/me/learning-directory", headers=enrolled_student_headers)
    assert directory.status_code == 200, directory.text
    subject_id = directory.json()["subjects"][0]["id"]

    feedback = client.get(
        f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers
    )
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["attempt_count"] == 0
    assert calls["n"] == 0


def test_summary_writer_failure_is_cached_as_the_template(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def _boom(_prompt: str) -> str:
        calls["n"] += 1
        raise OpenRouterError("down")

    monkeypatch.setattr(feedback_service, "_openrouter_summary_writer", _boom)
    subject_id, _attempt_id = _submit_perfect_attempt(client, enrolled_student_headers, seeded_db)

    first = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert first.status_code == 200, first.text
    assert first.json()["summary"].startswith("You're doing well in")
    assert calls["n"] == 1

    second = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert second.status_code == 200, second.text
    assert second.json()["summary"] == first.json()["summary"]
    assert calls["n"] == 1


def _enroll(client: TestClient, email: str, identifier: str) -> dict[str, str]:
    provision = client.post(
        "/api/v1/auth/provision-student",
        json={
            "email": email,
            "password": "password123",
            "full_name": "Classmate",
            "student_identifier": identifier,
            "institution_name": POC_INSTITUTION_NAME,
        },
    )
    assert provision.status_code == 200, provision.text
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "password123",
            "institution_name": POC_INSTITUTION_NAME,
        },
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    enroll = client.post(
        "/api/v1/me/enrollments/poc-math",
        json={"confirm": True},
        headers=headers,
    )
    assert enroll.status_code == 200, enroll.text
    return headers


def test_classmate_average_and_weak_remediation_use_batched_queries(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    _submit_perfect_attempt(client, enrolled_student_headers, seeded_db)
    classmate_headers = _enroll(client, "classmate@example.com", "S-200")
    subject_id, _attempt_id = _submit_attempt(client, classmate_headers, seeded_db, correct=False)

    feedback = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=classmate_headers)
    assert feedback.status_code == 200, feedback.text
    subtopic = feedback.json()["subtopics"][0]
    assert subtopic["is_weak"] is True
    assert subtopic["class_sample_size"] == 1
    assert float(subtopic["class_avg_percent"]) == 100.0
    assert subtopic["remediation"]["retake_quiz_id"]
    assert subtopic["remediation"]["material_title"]


def test_in_flight_summary_refresh_is_not_started_twice(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def _fake(_prompt: str) -> str:
        calls["n"] += 1
        return "Generated once."

    monkeypatch.setattr(feedback_service, "_openrouter_summary_writer", _fake)
    subject_id, _attempt_id = _submit_perfect_attempt(client, enrolled_student_headers, seeded_db)
    first = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert first.status_code == 200, first.text
    assert calls["n"] == 1

    row = seeded_db.scalar(select(SubjectFeedbackSummary))
    assert row is not None and row.facts_hash is not None
    row.requested_hash = row.facts_hash
    row.facts_hash = None
    row.summary = None
    row.updated_at = datetime.now(UTC)
    seeded_db.commit()

    second = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert second.status_code == 200, second.text
    assert second.json()["summary"].startswith("You're doing well in")
    assert calls["n"] == 1
