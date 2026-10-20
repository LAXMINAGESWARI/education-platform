"""Chat-guard integration tests through the HTTP API.

Jev is stubbed at ``judge.client``'s httpx transport, so ``post_decisions`` and
``classify_noul`` run. Probabilities come from the paid replay file. GPT is stubbed
at the completion function those callers already use, so ``chat_completion_json`` and
``legacy_gpt_flag`` run. A Jev pass on a data question also calls GPT. When both
allow, the audit stage is ``jev_gpt``. An HTTP 402 allow stays ``gpt``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from education_platform.core.config import Settings, get_settings
from education_platform.core.llm import OpenRouterError
from education_platform.db.url import to_sync_url
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.assistant.generation_strategy import (
    GUARD_UNAVAILABLE_REPLY as REVIEW_GUARD_UNAVAILABLE,
)
from education_platform.modules.assistant.generation_strategy import (
    INJECTION_BLOCKED_REPLY as REVIEW_INJECTION_REPLY,
)
from education_platform.modules.assistant.graph import (
    GUARD_UNAVAILABLE_REPLY as POLICY_GUARD_UNAVAILABLE,
)
from education_platform.modules.assistant.graph import (
    INJECTION_BLOCKED_REPLY as POLICY_INJECTION_REPLY,
)
from education_platform.modules.assistant.graph import (
    SCOPE_REPLY,
)
from education_platform.modules.audit.models import AuditEvent
from education_platform.modules.auth.models import User
from education_platform.modules.generation.models import ContentGenerationRun, GenerationRevision
from education_platform.modules.generation.types import ReviewStage, RevisionOrigin, RunPhase
from education_platform.modules.guard.classifier import (
    DATA_INJECTION,
    classify_noul,
    legacy_gpt_flag,
)
from education_platform.modules.judge.errors import JudgeError
from education_platform.modules.synthetic.generator import generate_school
from evals.guard.dataset import load_dataset
from test_generation import TEACHER, TEST_SPEC, _headers

_REPLAY = Path(__file__).resolve().parents[1] / "evals/guard/datasets/raw_scores_a0b00a3.jsonl"
_ASK = "/api/v1/text-to-sql/ask"
_DATA_INJECTION_ANSWER = (
    "I can't process that request. Please ask a concrete question about your "
    "students, classes, or school data."
)
_DATA_OFF_TOPIC_ANSWER = (
    "I can only help with questions about your school's students, classes, "
    "attendance, or curriculum data. Try asking something in that scope."
)
_REVIEW_ANSWER = "The frozen outline already states that outcome."

_ROW_TEXT = {row.id: row.text for row in load_dataset().rows}
_TEXT_ROW = {text: row_id for row_id, text in _ROW_TEXT.items()}
_REPLAY_ROWS: dict[tuple[str, str], dict[str, Any]] = {}
for _line in _REPLAY.read_text(encoding="utf-8").splitlines():
    _record = json.loads(_line)
    _REPLAY_ROWS[(str(_record["check"]), str(_record["row_id"]))] = _record


def _noul(check: str, row_id: str) -> float:
    probability = _REPLAY_ROWS[(check, row_id)]["probability"]
    assert isinstance(probability, float)
    return probability


def _positive(check: str, row_id: str) -> bool | None:
    value = _REPLAY_ROWS[(check, row_id)]["positive"]
    if value is None:
        return None
    assert isinstance(value, bool)
    return value


def _row_in(blob: str) -> str:
    matches = [row_id for row_id, text in _ROW_TEXT.items() if text and text in blob]
    if not matches:
        raise AssertionError(f"no replay row in {blob[:180]!r}")
    return max(matches, key=lambda row_id: len(_ROW_TEXT[row_id]))


class _Jev:
    """Decisions transport. ``pay_once`` bills only the first call of this script."""

    def __init__(self) -> None:
        self.mode = "scores"
        self.calls = 0
        self.statuses: list[object] = []

    def begin(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0
        self.statuses = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.mode == "timeout":
            self.statuses.append("timeout")
            raise httpx.TimeoutException("slow")
        if self.mode == "down":
            self.statuses.append(503)
            return httpx.Response(503, text="unavailable")
        if self.mode == "pay" or (self.mode == "pay_once" and self.calls == 1):
            self.statuses.append(402)
            return httpx.Response(402, json={"error": {"message": "insufficient credits"}})
        if self.mode == "score_answer":
            self.statuses.append(200)
            return httpx.Response(
                200,
                json={
                    "model": "typesafe/jev-1.14",
                    "answers": {"decision": {"type": "score", "score": 3}},
                },
            )
        body = json.loads(request.content)
        questions = body["questions"]["decision"]
        instructions = questions["instructions"]
        if "role-based data access" in instructions:
            check = "data.jev_injection"
        elif "nothing to" in instructions:
            check = "data.jev_off_topic"
        elif "education-policy assistant" in instructions:
            check = "policy.jev_injection"
        elif "frozen lesson" in instructions:
            check = "review.jev_injection"
        else:
            raise AssertionError(instructions)
        state = body["state"]
        message = state["message"] if isinstance(state, dict) else str(state)
        noul = _noul(check, _TEXT_ROW[message])
        self.statuses.append(200)
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.14",
                "answers": {"decision": {"type": "noul", "noul": noul}},
            },
        )


class _Gpt:
    """Completion stub. Classifier flags default to the stored replay decision."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_classifiers = False
        self.injection: bool | None = None
        self.off_topic: bool | None = None
        self.scope_valid: bool | None = None

    def begin(self) -> None:
        self.calls = []
        self.fail_classifiers = False
        self.injection = None
        self.off_topic = None
        self.scope_valid = None

    async def __call__(
        self,
        messages: list[dict[str, str]],
        *,
        settings: object = None,
        temperature: float = 0.0,
        response_json: bool = False,
        timeout: float | None = None,
    ) -> str:
        del settings, temperature, timeout
        system = next((item["content"] for item in messages if item.get("role") == "system"), "")
        user = "\n".join(item.get("content", "") for item in messages)
        kind = _completion_kind(system)
        self.calls.append(kind)
        if self.fail_classifiers and kind in {
            "data_injection",
            "data_off_topic",
            "policy_injection",
            "review_injection",
        }:
            raise OpenRouterError("stub gpt down")
        if kind == "intent":
            return json.dumps(
                {
                    "intent": None,
                    "confidence": 0,
                    "parameters": {},
                    "operation": None,
                    "ambiguous": False,
                    "boundary_conflict": False,
                }
            )
        if kind == "sql":
            return "```sql\nSELECT COUNT(*) AS n FROM student_360\n```"
        if kind == "compose":
            return "There are 12 students."
        if kind == "review_answer":
            return json.dumps({"content": _REVIEW_ANSWER, "draft_change_request": None})
        if kind == "policy_answer":
            return "Attendance is recorded by the office."
        if kind == "scope":
            row_id = _row_in(user)
            stored = _positive("policy.gpt_scope", row_id)
            valid = self.scope_valid if self.scope_valid is not None else not bool(stored)
            return json.dumps({"valid": valid, "reason": "replay"})
        flag = self._flag(kind, _row_in(user))
        if kind == "data_off_topic":
            return json.dumps({"off_topic": flag, "reason": "replay"})
        return json.dumps({"injection": flag, "reason": "replay"})

    def _flag(self, kind: str, row_id: str) -> bool:
        if kind in {"data_injection", "policy_injection", "review_injection"}:
            if self.injection is not None:
                return self.injection
            check = {
                "data_injection": "data.gpt_injection",
                "policy_injection": "policy.gpt_injection",
                "review_injection": "review.gpt_injection",
            }[kind]
        else:
            if self.off_topic is not None:
                return self.off_topic
            check = "data.gpt_off_topic"
        stored = _positive(check, row_id)
        return bool(stored)


def _completion_kind(system: str) -> str:
    if "conservative intent classifier" in system:
        return "intent"
    if "SQL generation assistant" in system:
        return "sql"
    if system.startswith("You answer questions about school data"):
        return "compose"
    if '"off_topic"' in system:
        return "data_off_topic"
    if "valid question about" in system:
        return "scope"
    if "text-to-SQL data assistant" in system and "injection" in system:
        return "data_injection"
    if "education-policy assistant" in system:
        return "policy_injection"
    if "teacher reviewing a frozen lesson" in system:
        return "review_injection"
    if "draft_change_request" in system:
        return "review_answer"
    if "Policy assistant" in system:
        return "policy_answer"
    raise AssertionError(system[:240])


def _install(monkeypatch: pytest.MonkeyPatch, jev: _Jev, gpt: _Gpt) -> None:
    real_client = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(jev.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("education_platform.modules.judge.client.httpx.AsyncClient", factory)
    monkeypatch.setattr("education_platform.core.llm.chat_completion", gpt)
    monkeypatch.setattr("education_platform.modules.assistant.graph.chat_completion", gpt)
    for module_name in (
        "education_platform.modules.text_to_sql.nodes.generate_sql",
        "education_platform.modules.text_to_sql.nodes.compose_answer",
    ):
        monkeypatch.setattr(sys.modules[module_name], "chat_completion", gpt)


def _arm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "replay-not-a-live-key")
    get_settings.cache_clear()


@pytest.fixture()
def alnoor(client: TestClient, clean_db: str) -> Iterator[TestClient]:
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        generate_school(session, TEST_SPEC)
        session.commit()
    engine.dispose()
    yield client


def _review_ids(api: TestClient, clean_db: str) -> tuple[str, str]:
    listed = api.get("/api/v1/authoring/subtopics", headers=_headers(api, TEACHER))
    assert listed.status_code == 200, listed.text
    math = [row for row in listed.json() if row["subject"] == "Mathematics"]
    assert math
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        subtopic = session.get(Subtopic, UUID(math[0]["id"]))
        assert subtopic is not None
        teacher = session.scalar(select(User).where(User.email == TEACHER))
        assert teacher is not None
        run = ContentGenerationRun(
            topic_id=subtopic.topic_id,
            submitted_by_user_id=teacher.id,
            title="Guard review",
            phase=RunPhase.OUTLINE_REVIEW,
            target_item_count=60,
        )
        session.add(run)
        session.flush()
        revision = GenerationRevision(
            run_id=run.id,
            stage=ReviewStage.OUTLINE,
            number=1,
            origin=RevisionOrigin.INITIAL_GENERATION,
            created_by_user_id=teacher.id,
            snapshot_hash="ab" * 32,
            snapshot={
                "target_item_count": 60,
                "nodes": [
                    {
                        "node_key": str(uuid4()),
                        "parent_node_key": None,
                        "slug": "guard-node",
                        "title": "Guard node",
                        "token_mass": 10,
                        "prerequisite_score": "0",
                        "centrality": "0",
                        "weight": "1",
                        "matched_subtopic_id": None,
                        "force_create": True,
                        "proposed_outcomes": ["State what the guard node covers."],
                        "sequence": 1,
                    }
                ],
            },
        )
        session.add(revision)
        session.commit()
        ids = (str(run.id), str(revision.id))
    engine.dispose()
    return ids


def _data_rows(db: Session) -> list[AuditEvent]:
    db.expire_all()
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "data.ask")
            .order_by(AuditEvent.created_at)
        ).all()
    )


def _refusals(db: Session, entity_type: str) -> list[AuditEvent]:
    db.expire_all()
    return list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.event_type == "assistant.refused",
                AuditEvent.entity_type == entity_type,
            )
            .order_by(AuditEvent.created_at)
        ).all()
    )


def _ask(api: TestClient, question: str) -> Any:
    response = api.post(_ASK, json={"question": question}, headers=_headers(api, TEACHER))
    assert response.status_code == 200, response.text
    return response.json()


def _policy(api: TestClient, content: str) -> Any:
    headers = _admin_headers(api)
    created = api.post("/api/v1/chats", json={"title": "Guard"}, headers=headers)
    assert created.status_code == 201, created.text
    posted = api.post(
        f"/api/v1/chats/{created.json()['id']}/messages",
        json={"content": content},
        headers=headers,
    )
    assert posted.status_code == 200, posted.text
    return posted.json()


def _admin_headers(api: TestClient) -> dict[str, str]:
    settings = get_settings()
    login = api.post(
        "/api/v1/auth/login",
        json={
            "email": settings.demo_admin_email,
            "password": settings.demo_admin_password,
            "institution_name": "POC Demo School",
        },
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _review(api: TestClient, clean_db: str, message: str, ids: tuple[str, str]) -> Any:
    run_id, revision_id = ids
    posted = api.post(
        f"/api/v1/teaching/generation-runs/{run_id}/assistant/turns",
        headers=_headers(api, TEACHER),
        json={"revision_id": revision_id, "message": message},
    )
    assert posted.status_code == 200, posted.text
    return posted.json()


@pytest.mark.regression
def test_data_chat_refuses_injection_and_answers_a_benign_question(
    alnoor: TestClient,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 1. Jev blocks di-01. ok-attendance clears Jev and both GPT checks."""
    _arm(monkeypatch)
    jev, gpt = _Jev(), _Gpt()
    _install(monkeypatch, jev, gpt)
    attack = _ROW_TEXT["di-01"]
    benign = _ROW_TEXT["ok-attendance"]

    refused = _ask(alnoor, attack)
    assert refused["natural_answer"] == _DATA_INJECTION_ANSWER
    assert jev.calls == 1
    assert "data_injection" not in gpt.calls
    payload = _data_rows(seeded_db)[-1].payload
    assert payload["question"] == attack
    assert payload["guard_stage"] == "jev"
    assert payload["injection_probability"] == _noul("data.jev_injection", "di-01")
    assert payload["outcome"] == "refused"
    assert payload["error_category"] == "INJECTION_BLOCKED"
    assert "message_preview" not in payload

    jev.begin("scores")
    gpt.begin()
    answered = _ask(alnoor, benign)
    assert answered["natural_answer"] != _DATA_INJECTION_ANSWER
    assert answered["natural_answer"] != _DATA_OFF_TOPIC_ANSWER
    assert set(answered) == {"natural_answer", "confidence", "provenance"}
    assert gpt.calls.count("data_injection") == 1
    assert gpt.calls.count("data_off_topic") == 1
    allowed = _data_rows(seeded_db)[-1].payload
    assert allowed["guard_stage"] == "jev_gpt"
    assert allowed["outcome"] == "answered"
    assert allowed["injection_probability"] == _noul("data.jev_injection", "ok-attendance")
    assert allowed["off_topic_probability"] == _noul("data.jev_off_topic", "ok-attendance")


def test_data_chat_refuses_an_off_topic_question(
    alnoor: TestClient,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 2. ot-01 injection is under threshold; the off-topic noul refuses it."""
    _arm(monkeypatch)
    jev, gpt = _Jev(), _Gpt()
    _install(monkeypatch, jev, gpt)
    question = _ROW_TEXT["ot-01"]

    refused = _ask(alnoor, question)
    assert refused["natural_answer"] == _DATA_OFF_TOPIC_ANSWER
    assert gpt.calls.count("data_injection") == 1
    assert "data_off_topic" not in gpt.calls
    payload = _data_rows(seeded_db)[-1].payload
    assert payload["guard_stage"] == "jev"
    assert payload["outcome"] == "refused"
    assert payload["error_category"] == "OFF_TOPIC_REJECTED"
    assert payload["injection_probability"] == _noul("data.jev_injection", "ot-01")
    assert payload["off_topic_probability"] == _noul("data.jev_off_topic", "ot-01")
    assert "message_preview" not in payload


def test_policy_chat_off_topic_is_refused_by_gpt_scope(
    client: TestClient,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 3. Scope refuses ot-01. The audit keeps the injection stage that passed."""
    _arm(monkeypatch)
    jev, gpt = _Jev(), _Gpt()
    _install(monkeypatch, jev, gpt)

    body = _policy(client, _ROW_TEXT["ot-01"])
    assert body["assistant_message"]["content"] == SCOPE_REPLY
    assert gpt.calls.count("scope") == 1
    assert "policy_injection" not in gpt.calls
    payload = _refusals(seeded_db, "policy_chat")[-1].payload
    assert payload["guard_stage"] == "jev"
    assert payload["injection_blocked"] is False
    assert payload["question_valid"] is False
    assert payload["injection_probability"] == _noul("policy.jev_injection", "ot-01")
    assert "message_preview" not in payload


@pytest.mark.parametrize("mode", ["timeout", "down"])
def test_policy_and_data_jev_outage_fails_closed_after_one_retry(
    mode: str,
    alnoor: TestClient,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 4. Timeout and HTTP 5xx each try the Decisions call twice, then jev_error."""
    _arm(monkeypatch)
    jev, gpt = _Jev(), _Gpt()
    _install(monkeypatch, jev, gpt)
    jev.begin(mode)

    body = _policy(alnoor, _ROW_TEXT["bl-01"])
    assert body["assistant_message"]["content"] == POLICY_GUARD_UNAVAILABLE
    assert jev.calls == 2
    assert jev.statuses == ["timeout", "timeout"] if mode == "timeout" else [503, 503]
    assert gpt.calls == []
    policy_payload = _refusals(seeded_db, "policy_chat")[-1].payload
    assert policy_payload["guard_stage"] == "jev_error"
    assert "message_preview" not in policy_payload

    jev.begin(mode)
    gpt.begin()
    refused = _ask(alnoor, _ROW_TEXT["ok-attendance"])
    assert refused["natural_answer"] == _DATA_INJECTION_ANSWER
    assert jev.calls == 2
    assert gpt.calls == []
    data_payload = _data_rows(seeded_db)[-1].payload
    assert data_payload["guard_stage"] == "jev_error"
    assert data_payload["outcome"] == "refused"
    assert "safety check is unavailable" in data_payload["error_detail"]


def test_policy_and_data_402_falls_back_to_gpt(
    alnoor: TestClient,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 4. HTTP 402 is not retried. GPT blocks policy di-01 and allows data."""
    _arm(monkeypatch)
    jev, gpt = _Jev(), _Gpt()
    _install(monkeypatch, jev, gpt)
    jev.begin("pay")

    blocked = _policy(alnoor, _ROW_TEXT["di-01"])
    assert blocked["assistant_message"]["content"] == POLICY_INJECTION_REPLY
    assert jev.statuses == [402]
    assert gpt.calls == ["policy_injection"]
    assert _refusals(seeded_db, "policy_chat")[-1].payload["guard_stage"] == "gpt"

    jev.begin("pay_once")
    gpt.begin()
    answered = _ask(alnoor, _ROW_TEXT["ok-attendance"])
    assert answered["natural_answer"] not in {_DATA_INJECTION_ANSWER, _DATA_OFF_TOPIC_ANSWER}
    assert jev.statuses == [402, 200]
    assert gpt.calls.count("data_injection") == 1
    assert gpt.calls.count("data_off_topic") == 1
    payload = _data_rows(seeded_db)[-1].payload
    assert payload["guard_stage"] == "gpt"
    assert payload["outcome"] == "answered"
    assert payload["off_topic_probability"] == _noul("data.jev_off_topic", "ok-attendance")


def test_policy_and_data_402_then_gpt_failure_fails_closed(
    alnoor: TestClient,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 4. A 402 whose GPT classifier fails closed is gpt_error, still one Jev call."""
    _arm(monkeypatch)
    jev, gpt = _Jev(), _Gpt()
    gpt.fail_classifiers = True
    _install(monkeypatch, jev, gpt)
    jev.begin("pay")

    policy = _policy(alnoor, _ROW_TEXT["bl-01"])
    assert policy["assistant_message"]["content"] == POLICY_GUARD_UNAVAILABLE
    assert jev.statuses == [402]
    assert _refusals(seeded_db, "policy_chat")[-1].payload["guard_stage"] == "gpt_error"

    jev.begin("pay")
    gpt.begin()
    gpt.fail_classifiers = True
    refused = _ask(alnoor, _ROW_TEXT["ok-attendance"])
    assert refused["natural_answer"] == _DATA_INJECTION_ANSWER
    assert jev.statuses == [402]
    payload = _data_rows(seeded_db)[-1].payload
    assert payload["guard_stage"] == "gpt_error"
    assert payload["outcome"] == "refused"
    assert "safety check is unavailable" in payload["error_detail"]


@pytest.mark.regression
def test_review_chat_refuses_injection_and_answers_a_benign_question(
    alnoor: TestClient,
    seeded_db: Session,
    clean_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 5. ii-06's review noul refuses; rv-01 is under threshold and is answered."""
    _arm(monkeypatch)
    jev, gpt = _Jev(), _Gpt()
    _install(monkeypatch, jev, gpt)
    ids = _review_ids(alnoor, clean_db)

    refused = _review(alnoor, clean_db, _ROW_TEXT["ii-06"], ids)
    assert refused["content"] == REVIEW_INJECTION_REPLY
    assert jev.calls == 1
    assert "review_answer" not in gpt.calls
    payload = _refusals(seeded_db, "generation_review")[-1].payload
    assert payload["guard_stage"] == "jev"
    assert payload["surface"] == "generation_review"
    assert "message_preview" not in payload
    assert jev.statuses == [200]

    jev.begin("scores")
    gpt.begin()
    answered = _review(alnoor, clean_db, _ROW_TEXT["rv-01"], ids)
    assert answered["content"] == _REVIEW_ANSWER
    assert "review_injection" not in gpt.calls
    assert "review_answer" in gpt.calls
    assert len(_refusals(seeded_db, "generation_review")) == 1


def test_review_chat_402_gpt_fallback_allows_blocks_and_errors(
    alnoor: TestClient,
    seeded_db: Session,
    clean_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 6. Review HTTP 402 runs _gpt_review_guard: allow, block, then gpt_error."""
    _arm(monkeypatch)
    jev, gpt = _Jev(), _Gpt()
    _install(monkeypatch, jev, gpt)
    ids = _review_ids(alnoor, clean_db)
    question = _ROW_TEXT["rv-01"]

    jev.begin("pay")
    gpt.injection = False
    allowed = _review(alnoor, clean_db, question, ids)
    assert allowed["content"] == _REVIEW_ANSWER
    assert jev.statuses == [402]
    assert "review_injection" in gpt.calls
    assert _refusals(seeded_db, "generation_review") == []

    jev.begin("pay")
    gpt.begin()
    gpt.injection = True
    blocked = _review(alnoor, clean_db, question, ids)
    assert blocked["content"] == REVIEW_INJECTION_REPLY
    assert jev.statuses == [402]
    assert _refusals(seeded_db, "generation_review")[-1].payload["guard_stage"] == "gpt"

    jev.begin("pay")
    gpt.begin()
    gpt.fail_classifiers = True
    failed = _review(alnoor, clean_db, question, ids)
    assert failed["content"] == REVIEW_GUARD_UNAVAILABLE
    assert jev.statuses == [402]
    assert _refusals(seeded_db, "generation_review")[-1].payload["guard_stage"] == "gpt_error"


@pytest.mark.parametrize("mode", ["timeout", "down"])
def test_review_chat_jev_outage_fails_closed_after_one_retry(
    mode: str,
    alnoor: TestClient,
    seeded_db: Session,
    clean_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 7. Review timeout and HTTP 5xx go through the real transport and retry once."""
    _arm(monkeypatch)
    jev, gpt = _Jev(), _Gpt()
    _install(monkeypatch, jev, gpt)
    jev.begin(mode)
    ids = _review_ids(alnoor, clean_db)

    body = _review(alnoor, clean_db, _ROW_TEXT["rv-01"], ids)
    assert body["content"] == REVIEW_GUARD_UNAVAILABLE
    assert jev.calls == 2
    assert jev.statuses == ["timeout", "timeout"] if mode == "timeout" else [503, 503]
    assert "review_injection" not in gpt.calls
    assert _refusals(seeded_db, "generation_review")[-1].payload["guard_stage"] == "jev_error"


@pytest.mark.asyncio
async def test_legacy_gpt_flag_rejects_bad_json_and_a_missing_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 8. The real classifier wraps a bad completion and a missing boolean."""
    settings = Settings(openrouter_api_key="test-key")

    async def bad_json(*_args: object, **_kwargs: object) -> str:
        return "not-json"

    monkeypatch.setattr("education_platform.core.llm.chat_completion", bad_json)
    with pytest.raises(JudgeError, match="GPT classifier failed"):
        await legacy_gpt_flag(
            settings=settings,
            system="system",
            user="user",
            field="injection",
        )

    async def missing_field(*_args: object, **_kwargs: object) -> str:
        return '{"reason": "no flag"}'

    monkeypatch.setattr("education_platform.core.llm.chat_completion", missing_field)
    with pytest.raises(JudgeError, match="GPT classifier omitted injection"):
        await legacy_gpt_flag(
            settings=settings,
            system="system",
            user="user",
            field="injection",
        )


@pytest.mark.asyncio
async def test_classify_noul_rejects_a_decision_that_is_not_a_noul(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 8. A parsed score answer is not a noul, so classify_noul rejects it."""
    settings = Settings(openrouter_api_key="test-key")
    jev = _Jev()
    _install(monkeypatch, jev, _Gpt())

    jev.begin("score_answer")
    with pytest.raises(JudgeError, match="Decisions response missing the noul answer"):
        await classify_noul(state="hello", question=DATA_INJECTION, settings=settings)
    assert jev.calls == 1
