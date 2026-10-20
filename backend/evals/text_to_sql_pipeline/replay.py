"""Run both pipelines on recorded model responses. No network and no database."""

from __future__ import annotations

import asyncio
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

import education_platform.modules.text_to_sql.graph as graph_mod
import education_platform.modules.text_to_sql.guards as guards_mod
import education_platform.modules.text_to_sql.llm as llm_mod
import education_platform.modules.text_to_sql.nodes as nodes
from education_platform.ai_platform.gateway.api import Completion
from education_platform.ai_platform.gateway.usage import TokenUsage
from education_platform.core.config import Settings
from education_platform.modules.guard.classifier import DATA_OFF_TOPIC
from education_platform.modules.judge.types import DecisionResult, NoulAnswer
from education_platform.modules.text_to_sql.graph import build_text_to_sql_graph
from education_platform.modules.text_to_sql.lean import run_lean
from education_platform.modules.text_to_sql.meter import reset_meter, start_meter
from education_platform.modules.text_to_sql.state import TextToSQLState

_CASE: ContextVar[dict[str, Any]] = ContextVar("text_to_sql_replay_case")
_USER = "00000000-0000-0000-0000-000000000001"
_INST = "00000000-0000-0000-0000-000000000002"
_SQL = "SELECT 1 AS subject"


def load_replay(path: Path) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("cases"), list):
        raise ValueError(f"{path} must contain a cases list")
    cases: list[dict[str, Any]] = []
    for item in loaded["cases"]:
        if not isinstance(item, dict) or "id" not in item:
            raise ValueError(f"each replay case needs an id: {item!r}")
        cases.append(item)
    if not cases:
        raise ValueError(f"{path} has no cases")
    return cases


def _settings() -> Settings:
    return Settings(_env_file=None, openrouter_api_key="replay-not-a-live-key")  # type: ignore[call-arg]


def _state(question: str, pipeline: str) -> TextToSQLState:
    return {
        "question": question,
        "pipeline": pipeline,
        "user_id": _USER,
        "user_role": "teacher",
        "institution_id": _INST,
        "schema_context": "",
        "query_source": None,
        "generated_sql": None,
        "validated_sql": None,
        "retry_count": 0,
        "query_result": None,
        "result_row_count": None,
        "natural_answer": None,
        "confidence": None,
        "provenance": None,
        "error": None,
        "audit_entry": {},
    }


def _decision(off_topic: float) -> DecisionResult:
    return DecisionResult(
        model="replay",
        answers={"injection": NoulAnswer(0.0), "off_topic": NoulAnswer(off_topic)},
        input_tokens=1,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=1,
        request_id=None,
        provider=None,
    )


def _completion(text: str, parsed: dict[str, Any] | None) -> Completion:
    return Completion(text=text, parsed_json=parsed, usage=TokenUsage(1, 1), served_model="replay")


async def _classify(**kwargs: Any) -> float:
    case = _CASE.get()
    question = kwargs.get("question")
    if question is DATA_OFF_TOPIC or "off_topic" in str(question):
        return 0.99 if case.get("route") == "refuse" else 0.0
    return 0.0


async def _gpt(**kwargs: Any) -> bool:
    case = _CASE.get()
    return bool(case.get("gpt_off_topic")) and kwargs.get("field") == "off_topic"


async def _post(**_kwargs: Any) -> DecisionResult:
    case = _CASE.get()
    off_topic = 0.99 if case.get("route") == "refuse" else 0.0
    return _decision(off_topic)


async def _complete(step: str, _messages: list[dict[str, Any]], **_kwargs: Any) -> Completion:
    case = _CASE.get()
    if step == "text_to_sql.guard":
        return _completion("{}", {"injection": False})
    if step == "text_to_sql.validator":
        return _completion("{}", {"off_topic": bool(case.get("gpt_off_topic"))})
    return _completion(str(case.get("compose_completion") or ""), None)


async def _chat(_messages: list[dict[str, str]], **_kwargs: Any) -> str:
    return str(_CASE.get().get("compose_completion") or "")


async def _intent(state: TextToSQLState) -> TextToSQLState:
    case = _CASE.get()
    if case.get("route") == "refuse":
        return {**state, "intent_route": "free_form", "error": None}
    return {
        **state,
        "intent_route": "free_form",
        "query_source": None,
        "generated_sql": _SQL,
        "error": None,
    }


async def _generate(state: TextToSQLState) -> TextToSQLState:
    return {**state, "generated_sql": _SQL, "schema_context": "subjects(id)", "error": None}


async def _validate(state: TextToSQLState) -> TextToSQLState:
    return {**state, "validated_sql": state.get("generated_sql") or _SQL, "error": None}


async def _scope(state: TextToSQLState) -> TextToSQLState:
    return state


async def _execute(state: TextToSQLState) -> TextToSQLState:
    rows = list(_CASE.get().get("rows") or [])
    return {**state, "query_result": rows, "result_row_count": len(rows), "error": None}


async def _sanity(state: TextToSQLState) -> TextToSQLState:
    return {**state, "confidence": "high"}


async def _audit(state: TextToSQLState) -> TextToSQLState:
    return state


def _view(result: TextToSQLState) -> dict[str, Any]:
    return {
        "natural_answer": result.get("natural_answer"),
        "confidence": result.get("confidence"),
        "provenance": result.get("provenance"),
    }


def _install() -> list[Any]:
    """Patch the call sites both pipelines use. Returns the patchers, started."""
    settings = _settings()
    # nodes/__init__ rebinds these names to the functions. Patch the modules.
    injection = sys.modules["education_platform.modules.text_to_sql.nodes.injection_guard"]
    validator = sys.modules["education_platform.modules.text_to_sql.nodes.question_validator"]
    compose = sys.modules["education_platform.modules.text_to_sql.nodes.compose_answer"]
    targets = (
        (injection, "classify_noul", _classify),
        (validator, "classify_noul", _classify),
        (guards_mod, "post_decisions", _post),
        (compose, "chat_completion", _chat),
        (llm_mod, "complete", _complete),
        (injection, "legacy_gpt_flag", _gpt),
        (validator, "legacy_gpt_flag", _gpt),
        (injection, "get_settings", lambda: settings),
        (validator, "get_settings", lambda: settings),
        (guards_mod, "get_settings", lambda: settings),
    )
    patchers: list[Any] = [patch.object(module, name, fn) for module, name, fn in targets]
    for module in (nodes, graph_mod):
        for name, fn in (
            ("intent_router", _intent),
            ("generate_sql", _generate),
            ("validate_sql", _validate),
            ("apply_role_scope", _scope),
            ("execute_sql", _execute),
            ("sanity_check", _sanity),
            ("audit_log", _audit),
        ):
            patchers.append(patch.object(module, name, fn))
    for patcher in patchers:
        patcher.start()
    return patchers


async def _run_case(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    token = _CASE.set(case)
    question = str(case["question"])
    meter = start_meter()
    try:
        graph = build_text_to_sql_graph()
        legacy = await graph.ainvoke(_state(question, "legacy"), config={"recursion_limit": 25})
    finally:
        reset_meter(meter)
    lean = await run_lean(_state(question, "lean"))
    _CASE.reset(token)
    return _view(legacy), _view(lean)


def _matches(expected: object, actual: dict[str, Any]) -> bool:
    if not isinstance(expected, dict):
        return False
    keys = ("natural_answer", "confidence", "provenance")
    return all(actual.get(key) == expected.get(key) for key in keys)


async def replay_cases(cases: list[dict[str, Any]]) -> int:
    patchers = _install()
    failures = 0
    try:
        print(f"replay: {len(cases)} cases, no network")
        for case in cases:
            legacy, lean = await _run_case(case)
            legacy_ok = _matches(case.get("legacy"), legacy)
            lean_ok = _matches(case.get("lean"), lean)
            print(
                f"  {case['id']}: legacy_ok={legacy_ok} lean_ok={lean_ok} "
                f"legacy={legacy['natural_answer']!r} lean={lean['natural_answer']!r}"
            )
            if not legacy_ok or not lean_ok:
                failures += 1
    finally:
        for patcher in reversed(patchers):
            patcher.stop()
    print(f"replay failures: {failures}")
    return 1 if failures else 0


def run_replay(path: Path) -> int:
    return asyncio.run(replay_cases(load_replay(path)))
