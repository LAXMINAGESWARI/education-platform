"""Blind solve stores agreement on the answer key and flags real disagreements in QA."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.assessments.models import (
    QuestionAnswerKey,
    QuestionVersion,
    QuizItem,
)
from education_platform.modules.generation.blind_solve import (
    BlindSolver,
    BlindVerdict,
    blind_solve_items,
    blind_solve_record,
    question_text,
    resolve_blind_solver,
)
from education_platform.modules.generation.items import (
    GeneratedItem,
    NodeItemRequest,
    items_from_chunks,
)
from education_platform.modules.generation.lesson import section_from_chunks
from education_platform.modules.generation.models import ContentGenerationRun, GenerationJob
from education_platform.modules.generation.schemas import QaItemOut
from education_platform.modules.generation.types import (
    GenerationJobKind,
    GenerationJobStatus,
    QaItem,
    RunPhase,
)
from education_platform.modules.generation.worker import process_generation_job_sync
from test_generation import SEEDED_SLUG
from test_generation_items import _accept_two_node_run, _silence_embed

_ITEM_NUMBER = re.compile(r"Item (\d+) \(")
_LABELS = ("A", "B", "C", "D")


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


def _qa(blind_solve: dict[str, object] | None) -> QaItemOut:
    return QaItemOut.from_domain(
        QaItem(
            question_id=uuid4(),
            question_version_id=uuid4(),
            prompt="Stem",
            options=(("A", "a"), ("B", "b"), ("C", "c"), ("D", "d")),
            correct_label="A",
            correct_rationale="because A",
            distractor_rationales={"B": "not B"},
            subtopic_id=uuid4(),
            sequence=1,
            blind_solve=blind_solve,
        )
    )


def _item_number(prompt: str) -> int | None:
    match = _ITEM_NUMBER.match(prompt)
    if match is None:
        return None
    return int(match.group(1))


def _jobs(session: Session, run_id: UUID, kind: GenerationJobKind) -> list[GenerationJob]:
    return list(
        session.scalars(
            select(GenerationJob).where(
                GenerationJob.run_id == run_id,
                GenerationJob.kind == kind,
            )
        ).all()
    )


class _Stem:
    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        self.options = {"A": "a", "B": "b", "C": "c", "D": "d", "correct_label": "A"}


def test_question_text_is_stem_and_options_only() -> None:
    text = question_text(
        "Which is even?",
        {
            "A": "2",
            "B": "3",
            "C": "5",
            "D": "7",
            "correct_label": "A",
            "correct_rationale": "Two is even.",
        },
    )
    assert text == "Which is even?\n\nA. 2\nB. 3\nC. 5\nD. 7"
    assert "correct_rationale" not in text
    assert "Two is even." not in text
    assert "correct_label" not in text


def test_blind_solve_items_records_error_without_failing_the_batch() -> None:
    seen: list[tuple[str, Mapping[str, str]]] = []

    def solver(prompt: str, options: Mapping[str, str]) -> BlindVerdict:
        seen.append((prompt, options))
        if prompt == "bad":
            raise RuntimeError("solver down")
        return BlindVerdict(answer="B", confidence=0.5, status="ok")

    verdicts = blind_solve_items([_Stem("ok"), _Stem("bad"), _Stem("later")], solver)
    assert [verdict.status for verdict in verdicts] == ["ok", "error", "ok"]
    assert [verdict.answer for verdict in verdicts] == ["B", None, "B"]
    assert sorted(prompt for prompt, _options in seen) == ["bad", "later", "ok"]
    for _prompt, options in seen:
        assert set(options) == {"A", "B", "C", "D"}
        assert "correct_label" not in options


def test_blind_solve_record_covers_agree_disagree_and_error() -> None:
    model = "anthropic/claude-sonnet-4.5"
    agree = blind_solve_record(
        BlindVerdict(answer="A", confidence=0.9, status="ok"),
        correct_label="A",
        model=model,
    )
    disagree = blind_solve_record(
        BlindVerdict(answer="c", confidence=1.4, status="ok"),
        correct_label="B",
        model=model,
    )
    error = blind_solve_record(
        BlindVerdict(answer="A", confidence=0.2, status="error"),
        correct_label="A",
        model=model,
    )
    assert agree == {
        "model": model,
        "answer": "A",
        "agrees": True,
        "confidence": 0.9,
        "status": "agree",
    }
    assert disagree["status"] == "disagree"
    assert disagree["answer"] == "C"
    assert disagree["agrees"] is False
    assert disagree["confidence"] == 1.0
    assert error["status"] == "error"
    assert error["answer"] is None
    assert error["agrees"] is False


def test_key_disputed_only_for_a_real_disagreement() -> None:
    disputed = _qa(
        {
            "model": "m",
            "answer": "C",
            "agrees": False,
            "confidence": 0.4,
            "status": "disagree",
        }
    )
    assert disputed.key_disputed is True
    assert disputed.blind_answer == "C"

    agreed = _qa(
        {"model": "m", "answer": "A", "agrees": True, "confidence": 0.9, "status": "agree"}
    )
    assert agreed.key_disputed is False
    assert agreed.blind_answer == "A"

    errored = _qa(
        {"model": "m", "answer": "B", "agrees": False, "confidence": 0.0, "status": "error"}
    )
    assert errored.key_disputed is False

    skipped = _qa(
        {"model": "m", "answer": None, "agrees": False, "confidence": 0.0, "status": "skipped"}
    )
    assert skipped.key_disputed is False
    assert skipped.blind_answer is None
    assert _qa(None).key_disputed is False
    assert _qa(None).blind_answer is None


def test_resolve_skips_when_openrouter_is_not_configured() -> None:
    assert resolve_blind_solver(None) is None

    def solver(prompt: str, options: Mapping[str, str]) -> BlindVerdict:
        return BlindVerdict(answer="A", confidence=1.0, status="ok")

    assert resolve_blind_solver(solver) is solver


def _solver_for(received: list[tuple[str, dict[str, str]]]) -> BlindSolver:
    def solver(*args: object, **kwargs: object) -> BlindVerdict:
        assert kwargs == {}
        assert len(args) == 2
        prompt, options = args
        assert isinstance(prompt, str)
        assert isinstance(options, dict)
        copied = {str(label): str(text) for label, text in options.items()}
        received.append((prompt, copied))
        number = _item_number(prompt)
        if number == 3:
            raise RuntimeError("solver failed")
        if number == 2:
            return BlindVerdict(answer="C", confidence=0.41, status="ok")
        label = _LABELS[((number or 1) - 1) % 4]
        return BlindVerdict(answer=label, confidence=0.93, status="ok")

    return solver


def _assert_solver_saw_no_key(
    received: Sequence[tuple[str, dict[str, str]]],
    produced: Sequence[GeneratedItem],
) -> None:
    by_prompt = {item.prompt: item for item in produced}
    assert len(by_prompt) == len(produced)
    assert len(received) == len(produced)
    for prompt, options in received:
        item = by_prompt[prompt]
        assert set(options) == set(_LABELS)
        assert options == {label: item.options[label] for label in _LABELS}
        blob = prompt + "\n" + "\n".join(options.values())
        assert item.correct_rationale not in blob
        for rationale in item.distractor_rationales.values():
            assert rationale not in blob
        assert "correct_rationale" not in blob
        assert "distractor_rationales" not in blob


def test_fake_solver_agree_disagree_and_error_reach_qa(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    lesson = _jobs(seeded_db, run_id, GenerationJobKind.LESSON)[0]
    process_generation_job_sync(lesson.id, write_lesson=section_from_chunks)
    seeded_db.expire_all()

    produced: list[GeneratedItem] = []
    received: list[tuple[str, dict[str, str]]] = []

    def _writer(request: NodeItemRequest) -> Sequence[GeneratedItem]:
        # Fallback stems repeat ("Which statement is correct?"). Stamp the slot
        # number the way this test classifies agree / disagree / error.
        batch = tuple(
            replace(
                item,
                prompt=(
                    f"Item {index + 1} ({item.bloom.value}) for {item.source_method}: {item.prompt}"
                ),
            )
            for index, item in enumerate(items_from_chunks(request))
        )
        produced.extend(batch)
        return batch

    items_job = _jobs(seeded_db, run_id, GenerationJobKind.ITEMS)[0]
    process_generation_job_sync(
        items_job.id, write_items=_writer, blind_solver=_solver_for(received)
    )
    seeded_db.expire_all()
    _assert_solver_saw_no_key(received, produced)

    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.QA_REVIEW
    assert run.draft_quiz_version_id is not None
    stored = seeded_db.execute(
        select(QuestionVersion.prompt, QuestionAnswerKey.scoring_rubric)
        .join(QuestionAnswerKey, QuestionAnswerKey.question_version_id == QuestionVersion.id)
        .join(QuizItem, QuizItem.question_version_id == QuestionVersion.id)
        .where(QuizItem.quiz_version_id == run.draft_quiz_version_id)
    ).all()
    counts = {1: 0, 2: 0, 3: 0}
    model = get_settings().blind_solver_model
    for prompt, rubric in stored:
        assert isinstance(rubric, dict)
        assert "bloom" in rubric
        blind = rubric["blind_solve"]
        assert isinstance(blind, dict)
        assert set(blind) == {"model", "answer", "agrees", "confidence", "status"}
        assert blind["model"] == model
        number = _item_number(str(prompt))
        if number == 1:
            counts[1] += 1
            assert blind["status"] == "agree"
            assert blind["answer"] == "A"
            assert blind["agrees"] is True
            assert blind["confidence"] == pytest.approx(0.93)
        elif number == 2:
            counts[2] += 1
            assert blind["status"] == "disagree"
            assert blind["answer"] == "C"
            assert blind["agrees"] is False
            assert blind["confidence"] == pytest.approx(0.41)
        elif number == 3:
            counts[3] += 1
            assert blind["status"] == "error"
            assert blind["answer"] is None
            assert blind["agrees"] is False
        else:
            assert blind["status"] == "agree"
            assert blind["agrees"] is True
    assert counts == {1: 2, 2: 2, 3: 2}

    body = client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers)
    assert body.status_code == 200, body.text
    qa_items = body.json()["qa_items"]
    assert qa_items
    disputed_id = ""
    for item in qa_items:
        number = _item_number(str(item["prompt"]))
        if number == 2:
            assert item["key_disputed"] is True
            assert item["blind_answer"] == "C"
            disputed_id = str(item["question_id"])
        elif number == 3:
            assert item["key_disputed"] is False
            assert item["blind_answer"] is None
        else:
            assert item["key_disputed"] is False
    assert disputed_id

    rejected = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/reject-items",
        headers=admin_headers,
        json={"question_ids": [disputed_id]},
    )
    assert rejected.status_code == 200, rejected.text
    seeded_db.expire_all()
    regen = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.REGENERATE_ITEMS,
            GenerationJob.status == GenerationJobStatus.QUEUED,
        )
    )
    assert regen is not None
    replaced: list[GeneratedItem] = []
    replace_seen: list[tuple[str, dict[str, str]]] = []

    def _replace_writer(request: NodeItemRequest) -> Sequence[GeneratedItem]:
        batch = items_from_chunks(request)
        replaced.extend(batch)
        return batch

    def _replace_solver(*args: object, **kwargs: object) -> BlindVerdict:
        assert kwargs == {}
        assert len(args) == 2
        prompt, options = args
        assert isinstance(prompt, str)
        assert isinstance(options, dict)
        replace_seen.append((prompt, {str(label): str(text) for label, text in options.items()}))
        return BlindVerdict(answer="D", confidence=0.2, status="ok")

    process_generation_job_sync(regen.id, write_items=_replace_writer, blind_solver=_replace_solver)
    seeded_db.expire_all()
    _assert_solver_saw_no_key(replace_seen, replaced)
    version = seeded_db.scalar(
        select(QuestionVersion)
        .where(QuestionVersion.question_id == UUID(disputed_id))
        .order_by(QuestionVersion.version_number.desc())
    )
    assert version is not None
    assert version.version_number == 2
    key = seeded_db.scalar(
        select(QuestionAnswerKey).where(QuestionAnswerKey.question_version_id == version.id)
    )
    assert key is not None
    assert key.scoring_rubric is not None
    replacement = key.scoring_rubric["blind_solve"]
    assert isinstance(replacement, dict)
    assert replacement["status"] == "disagree"
    assert replacement["answer"] == "D"
    assert replacement["agrees"] is False
    assert replacement["answer"] != key.correct_option_label

    again = client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers)
    assert again.status_code == 200, again.text
    payload = again.json()
    assert payload["phase"] == "qa_review"
    refreshed = next(item for item in payload["qa_items"] if item["question_id"] == disputed_id)
    assert refreshed["key_disputed"] is True
    assert refreshed["blind_answer"] == "D"
    get_settings.cache_clear()


def test_items_job_with_empty_lesson_fails_without_calling_the_solver(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    seeded_db.add(
        GenerationJob(
            run_id=run_id,
            kind=GenerationJobKind.ITEMS,
            status=GenerationJobStatus.QUEUED,
        )
    )
    seeded_db.commit()
    calls = {"writer": 0, "solver": 0}

    def _writer(request: NodeItemRequest) -> Sequence[GeneratedItem]:
        calls["writer"] += 1
        return items_from_chunks(request)

    def _solver(prompt: str, options: Mapping[str, str]) -> BlindVerdict:
        calls["solver"] += 1
        return BlindVerdict(answer="A", confidence=1.0, status="ok")

    job = _jobs(seeded_db, run_id, GenerationJobKind.ITEMS)[0]
    process_generation_job_sync(job.id, write_items=_writer, blind_solver=_solver)
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.FAILED
    assert run.failure_reason == "Cannot generate items before the lesson exists."
    assert calls == {"writer": 0, "solver": 0}
    failed = seeded_db.get(GenerationJob, job.id)
    assert failed is not None
    assert failed.status is GenerationJobStatus.FAILED
    get_settings.cache_clear()
