"""Slice 4/5 shared contract: QA publish, student topic lesson, directory unlock."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.assessments.models import (
    QuestionVersion,
    QuestionVersionStatus,
    QuizItem,
    QuizMaterialBinding,
    QuizRelease,
    QuizVersion,
    QuizVersionStatus,
)
from education_platform.modules.generation.items import (
    GeneratedItem,
    NodeItemRequest,
    items_from_chunks,
)
from education_platform.modules.generation.lesson import LessonSectionRequest, section_from_chunks
from education_platform.modules.generation.models import ContentGenerationRun, GenerationJob
from education_platform.modules.generation.types import GenerationJobKind, RunPhase
from education_platform.modules.generation.worker import process_generation_job_sync
from education_platform.modules.judge.client import HttpJudge
from education_platform.modules.judge.models import JudgeEvaluation
from education_platform.modules.materials.models import (
    SourceMaterial,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
)
from test_generation import SEEDED_SLUG
from test_generation_items import _accept_two_node_run, _silence_embed


@pytest.fixture()
def admin_headers(client: TestClient) -> dict[str, str]:
    settings = get_settings()
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.demo_admin_email,
            "password": settings.demo_admin_password,
            "institution_name": "POC Demo School",
        },
    )
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["access_token"]}


@pytest.fixture()
def seeded_topic_id(seeded_db: Session) -> UUID:
    subtopic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    return subtopic.topic_id


def _items_from_request(request: NodeItemRequest) -> Sequence[GeneratedItem]:
    return items_from_chunks(request)


def _section_from_request(request: LessonSectionRequest) -> str:
    return section_from_chunks(request)


def _job(session: Session, run_id: UUID, kind: GenerationJobKind) -> GenerationJob:
    job = session.scalar(
        select(GenerationJob).where(GenerationJob.run_id == run_id, GenerationJob.kind == kind)
    )
    assert job is not None
    return job


def _directory_topic(directory: dict[str, Any], topic_id: UUID) -> dict[str, Any]:
    for subject in directory["subjects"]:
        for topic in subject["topics"]:
            if topic["id"] == str(topic_id):
                return topic
    raise AssertionError(f"topic {topic_id} not in learning directory")


def _reach_qa_review(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> UUID:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    process_generation_job_sync(
        _job(seeded_db, run_id, GenerationJobKind.LESSON).id,
        write_lesson=_section_from_request,
    )
    seeded_db.expire_all()
    process_generation_job_sync(
        _job(seeded_db, run_id, GenerationJobKind.ITEMS).id,
        write_items=_items_from_request,
    )
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.QA_REVIEW
    return run_id


def test_publish_requires_qa_review_and_both_drafts(
    client: TestClient,
    admin_headers: dict[str, str],
    enrolled_student_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    too_early = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/publish",
        headers=admin_headers,
    )
    assert too_early.status_code == 409, too_early.text

    process_generation_job_sync(
        _job(seeded_db, run_id, GenerationJobKind.LESSON).id,
        write_lesson=_section_from_request,
    )
    still_generating = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/publish",
        headers=admin_headers,
    )
    assert still_generating.status_code == 409, still_generating.text
    missing = client.get(
        f"/api/v1/topics/{seeded_topic_id}/material",
        headers=enrolled_student_headers,
    )
    assert missing.status_code == 404
    get_settings.cache_clear()


def test_publish_is_idempotent_and_unlocks_student_material(
    client: TestClient,
    admin_headers: dict[str, str],
    enrolled_student_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = _reach_qa_review(
        client, admin_headers, seeded_topic_id, seeded_db, monkeypatch, tmp_path
    )
    before = client.get("/api/v1/me/learning-directory", headers=enrolled_student_headers)
    assert before.status_code == 200, before.text
    before_topic = _directory_topic(before.json(), seeded_topic_id)
    assert before_topic["has_topic_lesson"] is False
    assert before_topic["overall_quiz"] is not None
    assert before_topic["overall_quiz"]["unlocked"] is False

    preview = client.get(
        f"/api/v1/teaching/generation-runs/{run_id}",
        headers=admin_headers,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["phase"] == "qa_review"
    assert body["qa_items"]
    assert body["qa_items"][0]["correct_label"]
    assert body["qa_items"][0]["correct_rationale"]

    student_blocked = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/publish",
        headers=enrolled_student_headers,
    )
    assert student_blocked.status_code in {401, 403}

    first = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/publish",
        headers=admin_headers,
    )
    assert first.status_code == 200, first.text
    published = first.json()
    assert published["run_id"] == str(run_id)
    assert published["topic_id"] == str(seeded_topic_id)
    assert published["item_count"] == 60

    second = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/publish",
        headers=admin_headers,
    )
    assert second.status_code == 200, second.text
    assert second.json() == published

    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.PUBLISHED
    assert run.published_lesson_version_id == UUID(published["lesson_version_id"])
    assert run.published_quiz_version_id == UUID(published["quiz_version_id"])

    lesson_parents = list(
        seeded_db.scalars(
            select(SourceMaterial).where(
                SourceMaterial.topic_id == seeded_topic_id,
                SourceMaterial.slug == "lesson",
            )
        ).all()
    )
    assert len(lesson_parents) == 1
    published_versions = list(
        seeded_db.scalars(
            select(SourceMaterialVersion).where(
                SourceMaterialVersion.source_material_id == lesson_parents[0].id,
                SourceMaterialVersion.lifecycle_status == SourceMaterialVersionStatus.PUBLISHED,
            )
        ).all()
    )
    assert len(published_versions) == 1
    assert published_versions[0].id == run.published_lesson_version_id
    assert published_versions[0].content_markdown
    released_count = seeded_db.scalar(
        select(func.count())
        .select_from(QuizVersion)
        .where(
            QuizVersion.id == run.published_quiz_version_id,
            QuizVersion.lifecycle_status == QuizVersionStatus.RELEASED,
        )
    )
    assert released_count == 1
    assert (
        seeded_db.scalar(
            select(QuizRelease).where(QuizRelease.quiz_version_id == run.published_quiz_version_id)
        )
        is not None
    )
    assert (
        seeded_db.scalar(
            select(QuizMaterialBinding).where(
                QuizMaterialBinding.quiz_version_id == run.published_quiz_version_id,
                QuizMaterialBinding.source_material_version_id == run.published_lesson_version_id,
            )
        )
        is not None
    )
    question_statuses = list(
        seeded_db.scalars(
            select(QuestionVersion.lifecycle_status)
            .join(QuizItem, QuizItem.question_version_id == QuestionVersion.id)
            .where(QuizItem.quiz_version_id == run.published_quiz_version_id)
        ).all()
    )
    assert question_statuses
    assert all(status is QuestionVersionStatus.PUBLISHED for status in question_statuses)

    material = client.get(
        f"/api/v1/topics/{seeded_topic_id}/material",
        headers=enrolled_student_headers,
    )
    assert material.status_code == 200, material.text
    payload = material.json()
    assert payload["markdown"]
    serialized = str(payload)
    assert "correct_rationale" not in serialized
    assert "distractor_rationales" not in serialized
    assert "correct_option_label" not in serialized
    assert payload["quiz_id"]
    assert payload["quiz_unlocked"] is False

    after = client.get("/api/v1/me/learning-directory", headers=enrolled_student_headers)
    assert after.status_code == 200, after.text
    after_topic = _directory_topic(after.json(), seeded_topic_id)
    assert after_topic["has_topic_lesson"] is True
    assert after_topic["topic_source_material_version_id"] == published["lesson_version_id"]
    assert after_topic["overall_quiz"] is not None
    assert after_topic["overall_quiz"]["unlocked"] is False
    assert after_topic["overall_quiz"]["locked_reason"] == "Complete the topic lesson first"
    blocked = client.post(
        f"/api/v1/quizzes/{after_topic['overall_quiz']['id']}/attempts",
        headers=enrolled_student_headers,
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Complete the topic lesson first"
    completed = client.put(
        f"/api/v1/topics/{seeded_topic_id}/material-progress",
        headers=enrolled_student_headers,
        json={"status": "completed"},
    )
    assert completed.status_code == 200, completed.text
    unlocked = client.get(
        f"/api/v1/topics/{seeded_topic_id}/material",
        headers=enrolled_student_headers,
    )
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["quiz_unlocked"] is True
    after_progress = client.get("/api/v1/me/learning-directory", headers=enrolled_student_headers)
    assert after_progress.status_code == 200, after_progress.text
    progressed = _directory_topic(after_progress.json(), seeded_topic_id)
    assert progressed["topic_lesson_completed"] is True
    assert progressed["topic_lesson_progress"]["status"] == "completed"
    assert progressed["overall_quiz"]["unlocked"] is True
    assert progressed["overall_quiz"]["locked_reason"] is None
    get_settings.cache_clear()


def test_reject_items_returns_to_generating(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = _reach_qa_review(
        client, admin_headers, seeded_topic_id, seeded_db, monkeypatch, tmp_path
    )
    preview = client.get(
        f"/api/v1/teaching/generation-runs/{run_id}",
        headers=admin_headers,
    ).json()
    question_id = preview["qa_items"][0]["question_id"]
    kept_ids = {
        UUID(item["question_id"])
        for item in preview["qa_items"]
        if item["question_id"] != question_id
    }
    rejected = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/reject-items",
        headers=admin_headers,
        json={"question_ids": [question_id]},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["phase"] == "generating"
    seeded_db.expire_all()
    job = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.REGENERATE_ITEMS,
        )
    )
    assert job is not None
    process_generation_job_sync(job.id, write_items=_items_from_request)
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.QA_REVIEW
    assert run.draft_quiz_version_id is not None
    items = list(
        seeded_db.scalars(
            select(QuizItem).where(QuizItem.quiz_version_id == run.draft_quiz_version_id)
        ).all()
    )
    assert len(items) == 60
    after_ids = set(
        seeded_db.scalars(
            select(QuestionVersion.question_id).where(
                QuestionVersion.id.in_(tuple(row.question_version_id for row in items))
            )
        ).all()
    )
    assert kept_ids <= after_ids
    get_settings.cache_clear()


def test_jev_hold_blocks_publish_until_qa_accept(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = _reach_qa_review(
        client, admin_headers, seeded_topic_id, seeded_db, monkeypatch, tmp_path
    )
    seeded_db.add(
        JudgeEvaluation(
            run_id=run_id,
            subject_kind="lesson",
            subject_key=run_id,
            label="Lesson needs review",
            mode="enforce",
            decision="needs_review",
            reasons=["A section stayed ungrounded after one rewrite."],
            checks=[
                {
                    "check_id": "hold",
                    "kind": "marker",
                    "decision": "needs_review",
                    "summary": "hold",
                    "reasons": [],
                    "probabilities": {},
                    "drives_gate": False,
                }
            ],
        )
    )
    seeded_db.commit()
    blocked = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/publish",
        headers=admin_headers,
    )
    assert blocked.status_code == 409, blocked.text
    assert "needs review" in blocked.text.lower()

    workspace = client.get(
        f"/api/v1/teaching/generation-runs/{run_id}/review",
        headers=admin_headers,
    )
    assert workspace.status_code == 200, workspace.text
    body = workspace.json()
    open_round = body["open_round"]
    active = body["active_revision"]
    assert isinstance(open_round, dict)
    assert isinstance(active, dict)
    accepted = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/review-rounds/{open_round['id']}/close",
        headers=admin_headers,
        json={"revision_id": active["id"], "action": "accept_current"},
    )
    assert accepted.status_code == 200, accepted.text
    published = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/publish",
        headers=admin_headers,
    )
    assert published.status_code == 200, published.text
    get_settings.cache_clear()


def _replay_answer(
    question_id: str, question: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    kind = question.get("type")
    subject = state.get("kind")
    if kind == "noul":
        noul = 0.95
        if subject in {"lesson", "lesson_section"} and (
            question_id == "grounded" or question_id.startswith("claim_")
        ):
            noul = float(state.get("_source_noul", 0.95))
        elif subject == "quiz_item" and question_id == "keyed_correct":
            noul = float(state.get("_item_noul", 0.95))
        return {"type": "noul", "noul": noul}
    if kind == "choice":
        stated = str(state.get("stated_difficulty") or "medium")
        return {
            "type": "choice",
            "choice": stated,
            "confidence": 0.9,
            "probabilities": {stated: 0.9},
        }
    if question_id == "grade_level":
        return {
            "type": "score",
            "score": 1.0,
            "confidence": 0.9,
            "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1},
        }
    return {
        "type": "score",
        "score": 2.0,
        "confidence": 0.9,
        "probabilities": {"0": 0.0, "1": 0.1, "2": 0.8, "3": 0.1},
    }


def _install_judge_replay(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_noul: float,
    item_noul: float,
) -> None:
    """Replay OpenRouter decisions. The transport never leaves the process."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        state = body.get("state") if isinstance(body.get("state"), dict) else {}
        tagged = {**state, "_source_noul": source_noul, "_item_noul": item_noul}
        questions = body.get("questions") if isinstance(body.get("questions"), dict) else {}
        answers = {
            str(question_id): _replay_answer(str(question_id), question, tagged)
            for question_id, question in questions.items()
            if isinstance(question, dict)
        }
        return httpx.Response(
            200,
            json={
                "id": "replayed-decision",
                "model": "typesafe/jev-1.14",
                "provider": "replay",
                "answers": answers,
                "usage": {"input_tokens": 8, "output_tokens": 0, "cost": 0},
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    original = HttpJudge.__init__

    def patched(
        self: HttpJudge,
        settings: Any = None,
        *,
        client: httpx.Client | None = None,
        sleep: Any = None,
        clock: Any = None,
    ) -> None:
        kwargs: dict[str, Any] = {"client": client or replay_client, "sleep": lambda _seconds: None}
        if clock is not None:
            kwargs["clock"] = clock
        original(self, settings, **kwargs)

    replay_client = client
    monkeypatch.setattr(HttpJudge, "__init__", patched)
    # A configured key would also turn on the live blind solver. These journeys
    # replay Jev only, so the solver stays the same no-op the rest of the suite uses.
    monkeypatch.setattr(
        "education_platform.modules.generation.worker.resolve_blind_solver",
        lambda _injected: None,
    )


def _reach_qa_with_replay(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    source_noul: float,
    item_noul: float,
    mode: str = "enforce",
) -> UUID:
    monkeypatch.setenv("OPENROUTER_API_KEY", "replay-not-a-live-key")
    monkeypatch.setenv("JEV_JUDGE_MODE", mode)
    get_settings.cache_clear()
    _install_judge_replay(monkeypatch, source_noul=source_noul, item_noul=item_noul)
    return _reach_qa_review(
        client, admin_headers, seeded_topic_id, seeded_db, monkeypatch, tmp_path
    )


def _accept_current(client: TestClient, admin_headers: dict[str, str], run_id: UUID) -> None:
    workspace = client.get(
        f"/api/v1/teaching/generation-runs/{run_id}/review",
        headers=admin_headers,
    )
    assert workspace.status_code == 200, workspace.text
    body = workspace.json()
    accepted = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/review-rounds/{body['open_round']['id']}/close",
        headers=admin_headers,
        json={"revision_id": body["active_revision"]["id"], "action": "accept_current"},
    )
    assert accepted.status_code == 200, accepted.text


@pytest.mark.regression
def test_worker_enforce_lesson_hold_blocks_publish(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    try:
        run_id = _reach_qa_with_replay(
            client,
            admin_headers,
            seeded_topic_id,
            seeded_db,
            monkeypatch,
            tmp_path,
            source_noul=0.1,
            item_noul=0.95,
        )
        seeded_db.expire_all()
        hold = seeded_db.scalar(
            select(JudgeEvaluation).where(
                JudgeEvaluation.run_id == run_id,
                JudgeEvaluation.subject_kind == "lesson",
                JudgeEvaluation.decision == "needs_review",
            )
        )
        assert hold is not None
        assert hold.mode == "enforce"
        step = client.get(
            f"/api/v1/admin/generation-runs/{run_id}/steps/lesson",
            headers=admin_headers,
        )
        assert step.status_code == 200, step.text
        assert step.json()["lesson"]["needs_review"] is True
        blocked = client.post(
            f"/api/v1/teaching/generation-runs/{run_id}/publish",
            headers=admin_headers,
        )
        assert blocked.status_code == 409, blocked.text
        _accept_current(client, admin_headers, run_id)
        cleared = client.get(
            f"/api/v1/admin/generation-runs/{run_id}/steps/lesson",
            headers=admin_headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["lesson"]["needs_review"] is False
        published = client.post(
            f"/api/v1/teaching/generation-runs/{run_id}/publish",
            headers=admin_headers,
        )
        assert published.status_code == 200, published.text
    finally:
        get_settings.cache_clear()


@pytest.mark.regression
def test_new_hold_after_accept_blocks_again(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    try:
        run_id = _reach_qa_with_replay(
            client,
            admin_headers,
            seeded_topic_id,
            seeded_db,
            monkeypatch,
            tmp_path,
            source_noul=0.1,
            item_noul=0.95,
        )
        blocked = client.post(
            f"/api/v1/teaching/generation-runs/{run_id}/publish",
            headers=admin_headers,
        )
        assert blocked.status_code == 409, blocked.text
        _accept_current(client, admin_headers, run_id)
        seeded_db.add(
            JudgeEvaluation(
                run_id=run_id,
                subject_kind="lesson",
                subject_key=run_id,
                label="Lesson needs review",
                mode="enforce",
                decision="needs_review",
                reasons=["A later source check failed."],
                checks=[
                    {
                        "check_id": "hold",
                        "kind": "marker",
                        "decision": "needs_review",
                        "summary": "hold",
                        "reasons": [],
                        "probabilities": {},
                        "drives_gate": False,
                    }
                ],
                created_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        seeded_db.commit()
        again = client.post(
            f"/api/v1/teaching/generation-runs/{run_id}/publish",
            headers=admin_headers,
        )
        assert again.status_code == 409, again.text
    finally:
        get_settings.cache_clear()


@pytest.mark.regression
def test_shadow_needs_review_does_not_block_publish(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    try:
        run_id = _reach_qa_with_replay(
            client,
            admin_headers,
            seeded_topic_id,
            seeded_db,
            monkeypatch,
            tmp_path,
            source_noul=0.5,
            item_noul=0.95,
            mode="shadow",
        )
        seeded_db.expire_all()
        row = seeded_db.scalar(
            select(JudgeEvaluation).where(
                JudgeEvaluation.run_id == run_id,
                JudgeEvaluation.subject_kind == "lesson",
            )
        )
        assert row is not None
        assert row.mode == "shadow"
        assert row.decision == "needs_review"
        step = client.get(
            f"/api/v1/admin/generation-runs/{run_id}/steps/lesson",
            headers=admin_headers,
        )
        assert step.status_code == 200, step.text
        assert step.json()["lesson"]["needs_review"] is False
        published = client.post(
            f"/api/v1/teaching/generation-runs/{run_id}/publish",
            headers=admin_headers,
        )
        assert published.status_code == 200, published.text
    finally:
        get_settings.cache_clear()


@pytest.mark.regression
def test_qa_items_expose_judge_needs_review(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    try:
        run_id = _reach_qa_with_replay(
            client,
            admin_headers,
            seeded_topic_id,
            seeded_db,
            monkeypatch,
            tmp_path,
            source_noul=0.95,
            item_noul=0.5,
        )
        preview = client.get(
            f"/api/v1/teaching/generation-runs/{run_id}",
            headers=admin_headers,
        )
        assert preview.status_code == 200, preview.text
        items = preview.json()["qa_items"]
        assert items
        assert all(item["judge_needs_review"] is True for item in items)
        version_ids = [UUID(item["question_version_id"]) for item in items]
        versions = seeded_db.scalars(
            select(QuestionVersion).where(QuestionVersion.id.in_(version_ids))
        ).all()
        assert versions
        assert all(version.lifecycle_status is QuestionVersionStatus.DRAFT for version in versions)
    finally:
        get_settings.cache_clear()
