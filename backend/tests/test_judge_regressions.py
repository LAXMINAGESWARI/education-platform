"""Review regressions for the Jev enforce gate. Each one failed on 03d9ede."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.core.config import Settings, get_settings
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.generation.item_gate import gate_generated_item
from education_platform.modules.generation.items import NodeItemRequest
from education_platform.modules.generation.lesson import LessonSectionRequest, section_from_chunks
from education_platform.modules.generation.models import GenerationJob
from education_platform.modules.generation.types import BloomLevel, GenerationJobKind
from education_platform.modules.generation.worker import (
    _persist_produced,
    process_generation_job_sync,
)
from education_platform.modules.judge.checks import ItemTarget, LessonTarget
from education_platform.modules.judge.client import OPENROUTER_DECISIONS_URL, HttpJudge
from education_platform.modules.judge.evaluate import (
    Evaluation,
    evaluate_item,
    evaluate_lesson,
    store_evaluation,
)
from education_platform.modules.judge.hook import ItemJudgeTracker
from education_platform.modules.judge.lesson_gate import review_lesson_sections
from education_platform.modules.judge.models import JudgeEvaluation
from education_platform.modules.judge.types import DecisionRequest, DecisionResult
from test_generation import SEEDED_SLUG
from test_generation_publish import _reach_qa_review, _reach_qa_with_replay
from test_judge_enforce import _item, _ScriptedLessonJudge, _section, _target
from test_materials import (
    POC_INSTITUTION_NAME,
    POC_TEACHER_EMAIL,
    POC_TEACHER_PASSWORD,
    _assign_poc_teacher,
)

_HOLD_CHECK = {
    "check_id": "hold",
    "kind": "marker",
    "decision": "needs_review",
    "summary": "hold",
    "reasons": [],
    "probabilities": {},
    "drives_gate": False,
}


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
def teacher_headers(client: TestClient, seeded_db: Session) -> dict[str, str]:
    _assign_poc_teacher(seeded_db)
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": POC_TEACHER_EMAIL,
            "password": POC_TEACHER_PASSWORD,
            "institution_name": POC_INSTITUTION_NAME,
        },
    )
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["access_token"]}


@pytest.fixture()
def seeded_topic_id(seeded_db: Session) -> UUID:
    subtopic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    return subtopic.topic_id


def _section_from_request(request: LessonSectionRequest) -> str:
    return section_from_chunks(request)


def _accepting_result() -> DecisionResult:
    return DecisionResult(
        model="typesafe/jev-1.14",
        answers={},
        input_tokens=1,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=1,
        request_id=None,
        provider="replay",
    )


@pytest.mark.regression
def test_borderline_lesson_without_hold_publishes(
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
            mode="enforce",
        )
        seeded_db.expire_all()
        row = seeded_db.scalar(
            select(JudgeEvaluation).where(
                JudgeEvaluation.run_id == run_id,
                JudgeEvaluation.subject_kind == "lesson",
            )
        )
        assert row is not None
        assert row.decision == "needs_review"
        checks = row.checks if isinstance(row.checks, list) else []
        assert not any(isinstance(item, dict) and item.get("check_id") == "hold" for item in checks)
        published = client.post(
            f"/api/v1/teaching/generation-runs/{run_id}/publish",
            headers=admin_headers,
        )
        assert published.status_code == 200, published.text
    finally:
        get_settings.cache_clear()


@pytest.mark.regression
def test_error_row_after_hold_keeps_the_lesson_blocked(
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
    opened = datetime.now(UTC)
    seeded_db.add(
        JudgeEvaluation(
            run_id=run_id,
            subject_kind="lesson",
            subject_key=run_id,
            label="Lesson needs review",
            mode="enforce",
            decision="needs_review",
            reasons=["A section stayed ungrounded after one rewrite."],
            checks=[_HOLD_CHECK],
            created_at=opened,
        )
    )
    seeded_db.add(
        JudgeEvaluation(
            run_id=run_id,
            subject_kind="lesson",
            subject_key=run_id,
            label="Lesson",
            mode="enforce",
            decision="error",
            reasons=["Jev request failed with HTTP 500."],
            checks=[],
            error="Jev request failed with HTTP 500.",
            created_at=opened + timedelta(minutes=1),
        )
    )
    seeded_db.commit()
    blocked = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/publish",
        headers=admin_headers,
    )
    assert blocked.status_code == 409, blocked.text


@pytest.mark.regression
def test_repair_rejudges_only_flagged_sections(
    client: TestClient,
    admin_headers: dict[str, str],
    teacher_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import education_platform.modules.generation.worker as worker_mod

    try:
        run_id = _reach_qa_with_replay(
            client,
            admin_headers,
            seeded_topic_id,
            seeded_db,
            monkeypatch,
            tmp_path,
            source_noul=0.95,
            item_noul=0.95,
            mode="shadow",
        )
        workspace = client.get(
            f"/api/v1/teaching/generation-runs/{run_id}/review",
            headers=teacher_headers,
        )
        assert workspace.status_code == 200, workspace.text
        body = workspace.json()
        sections = body["active_revision"]["snapshot"]["lesson_sections"]
        assert isinstance(sections, list) and len(sections) >= 2
        target = sections[0]
        assert isinstance(target, dict)
        seen: list[int] = []
        real = worker_mod.review_lesson_sections

        def spy(
            targets: Sequence[object],
            judge: object,
            *,
            mode: str,
            rewrite: object,
        ) -> object:
            seen.append(len(tuple(targets)))
            return real(targets, judge, mode=mode, rewrite=rewrite)

        monkeypatch.setattr(worker_mod, "review_lesson_sections", spy)
        decided = client.post(
            f"/api/v1/teaching/generation-runs/{run_id}/review-rounds/{body['open_round']['id']}/decisions",
            headers=teacher_headers,
            json={
                "revision_id": body["active_revision"]["id"],
                "verdict": "changes_requested",
                "requests": [
                    {
                        "target": {
                            "kind": "lesson_section",
                            "section_key": target["section_key"],
                        },
                        "field": "body",
                        "kind": "pedagogy",
                        "comment": "Clarify this section only.",
                    }
                ],
            },
        )
        assert decided.status_code == 200, decided.text
        rewritten = client.post(
            f"/api/v1/teaching/generation-runs/{run_id}/review-rounds/{body['open_round']['id']}/close",
            headers=admin_headers,
            json={"revision_id": body["active_revision"]["id"], "action": "rewrite"},
        )
        assert rewritten.status_code == 200, rewritten.text
        job = seeded_db.scalar(
            select(GenerationJob).where(
                GenerationJob.run_id == run_id,
                GenerationJob.kind == GenerationJobKind.REWRITE_CONTENT,
            )
        )
        assert job is not None
        process_generation_job_sync(job.id, write_lesson=_section_from_request)
        assert seen, "repair should re-judge the flagged section"
        assert seen == [1]
    finally:
        get_settings.cache_clear()


@pytest.mark.regression
def test_item_judge_evaluation_does_not_leak_into_the_next_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JEV_JUDGE_MODE", "enforce")
    get_settings.cache_clear()
    stored: list[Evaluation | None] = []

    def persist(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(id=uuid4())

    def capture(_session: object, evaluation: Evaluation | None, **_kwargs: object) -> None:
        stored.append(evaluation)

    monkeypatch.setattr(
        "education_platform.modules.generation.worker._persist_generated_item",
        persist,
    )
    monkeypatch.setattr(
        "education_platform.modules.generation.worker.remember",
        capture,
    )

    class _Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def decide(self, _request: DecisionRequest) -> DecisionResult:
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("jev down")
            return _accepting_result()

    first = _item("Which shape has four right angles?")
    second = _item("Which shape has no corners at all?")
    request = NodeItemRequest(
        subtopic_id=uuid4(),
        learning_outcome_ids=(),
        heading="Shapes",
        chunk_texts=("A rectangle has four right angles.",),
        quota=2,
        bloom=(BloomLevel.REMEMBER, BloomLevel.REMEMBER),
    )
    _persist_produced(
        cast(Any, SimpleNamespace()),
        uuid4(),
        uuid4(),
        (first, second),
        1,
        solver=None,
        writer=None,
        request=request,
        lesson_markdown="Shapes have sides.",
        job_id=None,
        taken=[],
        stats={},
        judge=_Flaky(),
        grade_voice="Grade 6",
    )
    assert [item.label if item is not None else None for item in stored] == [
        first.prompt,
        None,
    ]


@pytest.mark.regression
def test_lesson_rejudge_exception_fails_open(caplog: pytest.LogCaptureFixture) -> None:
    judge = _ScriptedLessonJudge([0.1, RuntimeError("jev exploded")])
    with caplog.at_level(logging.WARNING):
        reviews, held = review_lesson_sections(
            (_section("The area is always 12."),),
            judge,
            mode="enforce",
            rewrite=lambda target, _reasons: target.markdown + " revised",
        )
    assert held is False
    assert reviews[0].regenerated is True
    assert any("failed open" in record.message.lower() for record in caplog.records)


def _low_grounded_body(request: httpx.Request) -> dict[str, object]:
    body = json.loads(request.content)
    state = body.get("state") if isinstance(body.get("state"), dict) else {}
    questions = body.get("questions") if isinstance(body.get("questions"), dict) else {}
    answers: dict[str, object] = {}
    for question_id, question in questions.items():
        if not isinstance(question, dict):
            continue
        kind = question.get("type")
        key = str(question_id)
        if kind == "noul":
            low = key == "grounded" or key.startswith("claim_") or key.startswith("coverage_")
            answers[key] = {"type": "noul", "noul": 0.1 if low else 0.95}
        elif kind == "choice":
            stated = str(state.get("stated_difficulty") or "medium")
            answers[key] = {"type": "choice", "choice": stated, "confidence": 0.9}
        else:
            answers[key] = {
                "type": "score",
                "score": 2.0,
                "confidence": 0.9,
                "probabilities": {"0": 0.0, "1": 0.1, "2": 0.8, "3": 0.1},
            }
    return {
        "id": "replayed-decision",
        "model": "typesafe/jev-1.14",
        "provider": "replay",
        "answers": answers,
        "usage": {"input_tokens": 8, "output_tokens": 0, "cost": 0},
    }


@pytest.mark.regression
def test_http_5xx_fails_open_with_replayed_transport(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Replayed HTTP only. The transport never leaves the process."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert str(request.url) == OPENROUTER_DECISIONS_URL
        if calls["n"] == 1:
            return httpx.Response(200, json=_low_grounded_body(request))
        return httpx.Response(500, text="unavailable")

    settings = Settings(
        _env_file=None,
        openrouter_api_key="replay-not-a-live-key",
        jev_model="typesafe/jev-1.14",
        jev_max_retries=0,
        jev_judge_mode="enforce",
    )
    judge = HttpJudge(
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    _reviews, held = review_lesson_sections(
        (_section("The area is always 12."),),
        judge,
        mode="enforce",
        rewrite=lambda target, _reasons: target.markdown + " revised",
    )
    assert held is False
    assert calls["n"] >= 2

    def always_500(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPENROUTER_DECISIONS_URL
        return httpx.Response(503, text="unavailable")

    item_judge = HttpJudge(
        settings,
        client=httpx.Client(transport=httpx.MockTransport(always_500)),
        sleep=lambda _seconds: None,
    )
    target = _target()
    assert isinstance(target, ItemTarget)
    failed = evaluate_item(target, item_judge, settings=settings)
    assert failed.decision == "error"
    tracker = ItemJudgeTracker(item_judge, lambda _item: target, enforce=True)
    gated = gate_generated_item(
        _item(),
        solve=lambda _item: {"status": "agree", "answer": "A", "working": ""},
        rewrite=None,
        taken=[],
        judge=tracker,
        enforce_judge=True,
    )
    assert gated.servable is True
    assert gated.verdict == "passed"

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
            checks=[_HOLD_CHECK],
            created_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    seeded_db.commit()
    unavailable = evaluate_lesson(
        LessonTarget(
            heading="Area",
            grade="Grade 6",
            outcomes=("Find the area.",),
            chunks=("Area is length times width.",),
            markdown="Area is length times width.",
            kind="lesson",
        ),
        item_judge,
        include_claims=False,
        settings=settings,
    )
    assert unavailable.decision == "error"
    store_evaluation(
        seeded_db,
        unavailable,
        run_id=run_id,
        generation_job_id=None,
        subject_key=run_id,
        question_version_id=None,
        mode="enforce",
        gate_verdict=None,
        gate_servable=None,
        blind_status=None,
        blind_answer=None,
    )
    seeded_db.commit()
    blocked = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/publish",
        headers=admin_headers,
    )
    assert blocked.status_code == 409, blocked.text
