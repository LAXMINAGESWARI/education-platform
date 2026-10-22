from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.core.llm import OpenRouterError
from education_platform.modules.academics.models import Subject
from education_platform.modules.assessments import feedback_summary
from education_platform.modules.assessments.models import (
    FeedbackSummaryJob,
    FeedbackSummaryJobStatus,
    FeedbackSummaryOrigin,
    QuestionAnswerKey,
    QuizItem,
    SubjectFeedbackSummary,
)
from education_platform.modules.materials.seed import POC_INSTITUTION_NAME, POC_SUBJECT_CODE
from education_platform.workers.runner import poll_once


@pytest.fixture(autouse=True)
def _fake_summary_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake(_prompt: str) -> str:
        return "Fake summary for tests."

    monkeypatch.setattr(feedback_summary, "write_summary", _fake)


def _finish_summary(seeded_db: Session) -> None:
    seeded_db.expire_all()
    claimed = poll_once(session=seeded_db)
    assert claimed is not None
    seeded_db.expire_all()


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
    assert payload["summary_status"] == "pending"
    assert payload["summary"].startswith("You're doing well in")
    assert payload["summary"] != "Fake summary for tests."
    job = seeded_db.scalar(select(FeedbackSummaryJob))
    assert job is not None
    assert "Mathematics" in job.prompt
    assert "100" in job.prompt
    assert "Units:" in job.prompt
    assert "subtopic" not in job.prompt.lower()
    assert "Square Numbers" not in job.prompt

    _finish_summary(seeded_db)
    cached = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert cached.status_code == 200, cached.text
    assert cached.json()["summary_status"] == "ready"
    assert cached.json()["summary"] == "Fake summary for tests."
    stored = seeded_db.scalar(select(FeedbackSummaryJob))
    assert stored is not None
    assert stored.status == FeedbackSummaryJobStatus.SUCCEEDED


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
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _fake(_prompt: str) -> str:
        calls["n"] += 1
        return "should not run"

    monkeypatch.setattr(feedback_summary, "write_summary", _fake)
    directory = client.get("/api/v1/me/learning-directory", headers=enrolled_student_headers)
    assert directory.status_code == 200, directory.text
    subject_id = directory.json()["subjects"][0]["id"]

    feedback = client.get(
        f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers
    )
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["attempt_count"] == 0
    assert feedback.json()["summary_status"] == "fallback"
    assert seeded_db.scalar(select(FeedbackSummaryJob)) is None
    assert calls["n"] == 0


def test_summary_writer_failure_is_cached_as_the_template(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _boom(_prompt: str) -> str:
        calls["n"] += 1
        raise OpenRouterError("down")

    monkeypatch.setattr(feedback_summary, "write_summary", _boom)
    subject_id, _attempt_id = _submit_perfect_attempt(client, enrolled_student_headers, seeded_db)

    first = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert first.status_code == 200, first.text
    assert first.json()["summary_status"] == "pending"
    assert first.json()["summary"].startswith("You're doing well in")
    assert calls["n"] == 0

    _finish_summary(seeded_db)
    assert calls["n"] == 1

    second = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert second.status_code == 200, second.text
    assert second.json()["summary_status"] == "fallback"
    assert second.json()["summary"] == first.json()["summary"]
    assert calls["n"] == 1
    seeded_db.expire_all()
    stored = seeded_db.scalars(select(FeedbackSummaryJob)).all()
    assert len(stored) == 1
    assert stored[0].status == FeedbackSummaryJobStatus.SUCCEEDED


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


def test_in_flight_summary_job_is_not_enqueued_twice(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _fake(_prompt: str) -> str:
        calls["n"] += 1
        return "Generated once."

    monkeypatch.setattr(feedback_summary, "write_summary", _fake)
    subject_id, _attempt_id = _submit_perfect_attempt(client, enrolled_student_headers, seeded_db)
    first = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert first.status_code == 200, first.text
    assert first.json()["summary_status"] == "pending"
    assert calls["n"] == 0

    second = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert second.status_code == 200, second.text
    assert second.json()["summary_status"] == "pending"
    seeded_db.expire_all()
    assert len(seeded_db.scalars(select(FeedbackSummaryJob)).all()) == 1
    assert calls["n"] == 0

    _finish_summary(seeded_db)
    assert calls["n"] == 1
    ready = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert ready.status_code == 200, ready.text
    assert ready.json()["summary_status"] == "ready"
    assert ready.json()["summary"] == "Generated once."
    assert poll_once(session=seeded_db) is None
    assert calls["n"] == 1


def test_exhausted_summary_job_stores_the_template(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _fake(_prompt: str) -> str:
        calls["n"] += 1
        return "should not run"

    monkeypatch.setattr(feedback_summary, "write_summary", _fake)
    subject_id, _attempt_id = _submit_perfect_attempt(client, enrolled_student_headers, seeded_db)
    first = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert first.status_code == 200, first.text

    seeded_db.expire_all()
    job = seeded_db.scalar(select(FeedbackSummaryJob))
    assert job is not None
    job.status = FeedbackSummaryJobStatus.RUNNING
    job.attempts = job.max_attempts
    job.locked_until = datetime.now(UTC) - timedelta(seconds=5)
    seeded_db.commit()

    claimed = poll_once(session=seeded_db)
    assert claimed == job.id
    assert calls["n"] == 0
    seeded_db.expire_all()
    failed = seeded_db.get(FeedbackSummaryJob, job.id)
    assert failed is not None
    assert failed.status == FeedbackSummaryJobStatus.FAILED

    second = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert second.status_code == 200, second.text
    assert second.json()["summary_status"] == "fallback"
    assert second.json()["summary"] == first.json()["summary"]
    seeded_db.expire_all()
    origin = seeded_db.scalar(select(SubjectFeedbackSummary.summary_origin))
    assert origin == FeedbackSummaryOrigin.TEMPLATE


def test_writer_bug_releases_the_job_for_another_claim(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_prompt: str) -> str:
        raise RuntimeError("bug")

    monkeypatch.setattr(feedback_summary, "write_summary", _boom)
    subject_id, _attempt_id = _submit_perfect_attempt(client, enrolled_student_headers, seeded_db)
    first = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert first.json()["summary_status"] == "pending"

    assert poll_once(session=seeded_db) is not None
    seeded_db.expire_all()
    job = seeded_db.scalar(select(FeedbackSummaryJob))
    assert job is not None
    assert job.status == FeedbackSummaryJobStatus.RUNNING
    assert job.locked_until is not None
    assert job.locked_until < datetime.now(UTC)

    monkeypatch.setattr(feedback_summary, "write_summary", lambda _prompt: "Recovered.")
    assert poll_once(session=seeded_db) == job.id
    ready = client.get(f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers)
    assert ready.status_code == 200, ready.text
    assert ready.json()["summary_status"] == "ready"
    assert ready.json()["summary"] == "Recovered."
