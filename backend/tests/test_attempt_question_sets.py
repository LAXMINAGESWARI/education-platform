"""Served question sets: draw size, submit, rotation, and legacy attempts."""

from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from education_platform.modules.assessments.models import (
    AttemptQuestion,
    CommonMasteryQuiz,
    QuestionAnswerKey,
    QuestionVersion,
    QuizAttempt,
    QuizItem,
    QuizScope,
    QuizVersion,
    QuizVersionStatus,
)
from education_platform.modules.assessments.question_selection import questions_for_attempt
from test_mock_parity import _complete_lesson, _perfect_answers


def _topic_quiz(seeded_db: Session) -> tuple[CommonMasteryQuiz, QuizVersion]:
    quiz = seeded_db.scalar(
        select(CommonMasteryQuiz).where(CommonMasteryQuiz.quiz_scope == QuizScope.TOPIC_MASTERY)
    )
    assert quiz is not None
    version = seeded_db.scalar(
        select(QuizVersion)
        .where(
            QuizVersion.quiz_id == quiz.id,
            QuizVersion.lifecycle_status == QuizVersionStatus.RELEASED,
        )
        .order_by(QuizVersion.version_number.desc())
    )
    assert version is not None
    return quiz, version


def _bank_prompts(seeded_db: Session, quiz_version_id: UUID) -> list[str]:
    items = seeded_db.scalars(
        select(QuizItem)
        .where(QuizItem.quiz_version_id == quiz_version_id)
        .order_by(QuizItem.sequence)
    ).all()
    prompts: list[str] = []
    for item in items:
        version = seeded_db.get(QuestionVersion, item.question_version_id)
        assert version is not None
        prompts.append(version.prompt)
    return prompts


def _served_ids(seeded_db: Session, attempt_id: str) -> set[UUID]:
    seeded_db.expire_all()
    return set(
        seeded_db.scalars(
            select(AttemptQuestion.question_version_id).where(
                AttemptQuestion.attempt_id == UUID(attempt_id)
            )
        ).all()
    )


def _unlock_topic_quiz(client: TestClient, headers: dict[str, str], seeded_db: Session) -> Any:
    directory = client.get("/api/v1/me/learning-directory", headers=headers)
    assert directory.status_code == 200, directory.text
    topic = directory.json()["subjects"][0]["topics"][0]
    for subtopic in topic["subtopics"]:
        _complete_lesson(client, headers, subtopic["id"])
        start = client.post(
            f"/api/v1/quizzes/{subtopic['quiz']['id']}/attempts",
            headers=headers,
        )
        assert start.status_code == 200, start.text
        submit = client.post(
            f"/api/v1/attempts/{start.json()['id']}/submit",
            headers=headers,
            json={"answers": _perfect_answers(seeded_db, start.json()["quiz_version_id"])},
        )
        assert submit.status_code == 200, submit.text
        assert submit.json()["passed"] is True
    return topic


def _set_draw_size(seeded_db: Session, count: int | None) -> QuizVersion:
    _quiz, version = _topic_quiz(seeded_db)
    version.attempt_question_count = count
    seeded_db.commit()
    return version


def _answers(
    seeded_db: Session, attempt_id: str, *, correct_positions: set[int]
) -> list[dict[str, object]]:
    seeded_db.expire_all()
    rows = seeded_db.scalars(
        select(AttemptQuestion)
        .where(AttemptQuestion.attempt_id == UUID(attempt_id))
        .order_by(AttemptQuestion.position)
    ).all()
    answers: list[dict[str, object]] = []
    for row in rows:
        key = seeded_db.scalar(
            select(QuestionAnswerKey).where(
                QuestionAnswerKey.question_version_id == row.question_version_id
            )
        )
        assert key is not None and key.correct_option_label is not None
        label = key.correct_option_label
        if row.position not in correct_positions:
            label = "B" if label.upper() == "A" else "A"
        answers.append({"question_number": row.position, "selected_option_label": label})
    return answers


def test_subtopic_bank_of_ten_is_served_in_full(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    preview = client.get(
        "/api/v1/materials/square_numbers_patterns/quiz", headers=enrolled_student_headers
    )
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["questions"]) == 10

    directory = client.get("/api/v1/me/learning-directory", headers=enrolled_student_headers)
    subtopic_id = next(
        subtopic["id"]
        for subject in directory.json()["subjects"]
        for topic in subject["topics"]
        for subtopic in topic["subtopics"]
        if subtopic["slug"] == "square_numbers_patterns"
    )
    _complete_lesson(client, enrolled_student_headers, subtopic_id)
    start = client.post(
        f"/api/v1/quizzes/{preview.json()['id']}/attempts",
        headers=enrolled_student_headers,
    )
    assert start.status_code == 200, start.text
    served = start.json()["questions"]
    assert [question["number"] for question in served] == [
        question["number"] for question in preview.json()["questions"]
    ]
    assert [question["prompt"] for question in served] == [
        question["prompt"] for question in preview.json()["questions"]
    ]
    assert "correct_option_label" not in start.text


def test_null_draw_size_serves_the_topic_bank(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    topic = _unlock_topic_quiz(client, enrolled_student_headers, seeded_db)
    _quiz, version = _topic_quiz(seeded_db)
    assert version.attempt_question_count is None
    prompts = _bank_prompts(seeded_db, version.id)
    assert len(prompts) > 10

    start = client.post(
        f"/api/v1/quizzes/{topic['overall_quiz']['id']}/attempts",
        headers=enrolled_student_headers,
    )
    assert start.status_code == 200, start.text
    questions = start.json()["questions"]
    assert [question["prompt"] for question in questions] == prompts
    assert [question["number"] for question in questions] == list(range(1, len(prompts) + 1))


def test_topic_draw_scores_rotates_and_feeds_back_the_served_set(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    topic = _unlock_topic_quiz(client, enrolled_student_headers, seeded_db)
    version = _set_draw_size(seeded_db, 10)
    quiz_id = topic["overall_quiz"]["id"]
    bank_size = len(_bank_prompts(seeded_db, version.id))
    assert bank_size > 10

    first = client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=enrolled_student_headers)
    assert first.status_code == 200, first.text
    questions = first.json()["questions"]
    assert [question["number"] for question in questions] == list(range(1, 11))
    assert [question["difficulty"] for question in questions] == ["Easy"] * 4 + ["Medium"] * 3 + [
        "Hard"
    ] * 3
    assert "correct_option_label" not in first.text
    full_bank = _perfect_answers(seeded_db, first.json()["quiz_version_id"])
    rejected = client.post(
        f"/api/v1/attempts/{first.json()['id']}/submit",
        headers=enrolled_student_headers,
        json={"answers": full_bank},
    )
    assert rejected.status_code == 422, rejected.text

    half = _answers(seeded_db, first.json()["id"], correct_positions={1, 2, 3, 4, 5})
    scored = client.post(
        f"/api/v1/attempts/{first.json()['id']}/submit",
        headers=enrolled_student_headers,
        json={"answers": half},
    )
    assert scored.status_code == 200, scored.text
    assert float(scored.json()["score_percent"]) == 50.0
    assert len(scored.json()["answers"]) == 10
    assert [answer["question_number"] for answer in scored.json()["answers"]] == list(range(1, 11))
    assert "correct_option_label" not in scored.text

    directory = client.get("/api/v1/me/learning-directory", headers=enrolled_student_headers)
    subject_id = directory.json()["subjects"][0]["id"]
    feedback = client.get(
        f"/api/v1/subjects/{subject_id}/feedback", headers=enrolled_student_headers
    )
    assert feedback.status_code == 200, feedback.text
    # Two subtopic quizzes of 10, plus this 10-question topic draw. The 20-item
    # topic bank must not be what the breakdown counts.
    served_answers = sum(row["question_count"] for row in feedback.json()["subtopics"])
    assert served_answers == 30

    second = client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=enrolled_student_headers)
    assert second.status_code == 200, second.text
    first_ids = _served_ids(seeded_db, first.json()["id"])
    second_ids = _served_ids(seeded_db, second.json()["id"])
    assert len(second_ids) == 10
    assert first_ids.isdisjoint(second_ids)
    submit_second = client.post(
        f"/api/v1/attempts/{second.json()['id']}/submit",
        headers=enrolled_student_headers,
        json={
            "answers": _answers(seeded_db, second.json()["id"], correct_positions=set(range(1, 11)))
        },
    )
    assert submit_second.status_code == 200, submit_second.text

    third = client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=enrolled_student_headers)
    assert third.status_code == 200, third.text
    assert _served_ids(seeded_db, third.json()["id"]) == first_ids


async def test_restart_draws_again_and_resume_reads_the_stored_set(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
    async_db_session: AsyncSession,
) -> None:
    topic = _unlock_topic_quiz(client, enrolled_student_headers, seeded_db)
    _set_draw_size(seeded_db, 10)
    quiz_id = topic["overall_quiz"]["id"]
    first = client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=enrolled_student_headers)
    assert first.status_code == 200, first.text

    attempt = await async_db_session.get(QuizAttempt, UUID(first.json()["id"]))
    assert attempt is not None
    again = await questions_for_attempt(async_db_session, attempt)
    assert [question.prompt for question in again] == [
        question["prompt"] for question in first.json()["questions"]
    ]
    assert [question.number for question in again] == [
        question["number"] for question in first.json()["questions"]
    ]

    restarted = client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=enrolled_student_headers)
    assert restarted.status_code == 200, restarted.text
    assert restarted.json()["id"] != first.json()["id"]
    assert len(restarted.json()["questions"]) == 10
    seeded_db.expire_all()
    assert seeded_db.get(QuizAttempt, UUID(first.json()["id"])) is None
    assert len(_served_ids(seeded_db, restarted.json()["id"])) == 10


def test_legacy_attempt_without_stored_rows_grades_the_full_bank(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    topic = _unlock_topic_quiz(client, enrolled_student_headers, seeded_db)
    version = _set_draw_size(seeded_db, 10)
    start = client.post(
        f"/api/v1/quizzes/{topic['overall_quiz']['id']}/attempts",
        headers=enrolled_student_headers,
    )
    assert start.status_code == 200, start.text
    assert len(start.json()["questions"]) == 10

    seeded_db.execute(
        delete(AttemptQuestion).where(AttemptQuestion.attempt_id == UUID(start.json()["id"]))
    )
    seeded_db.commit()

    partial = client.post(
        f"/api/v1/attempts/{start.json()['id']}/submit",
        headers=enrolled_student_headers,
        json={"answers": _perfect_answers(seeded_db, str(version.id))[:10]},
    )
    assert partial.status_code == 422, partial.text

    full = client.post(
        f"/api/v1/attempts/{start.json()['id']}/submit",
        headers=enrolled_student_headers,
        json={"answers": _perfect_answers(seeded_db, str(version.id))},
    )
    assert full.status_code == 200, full.text
    assert float(full.json()["score_percent"]) == 100.0
    assert len(full.json()["answers"]) == len(_bank_prompts(seeded_db, version.id))
