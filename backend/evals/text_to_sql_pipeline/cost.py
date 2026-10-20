"""Dry-run cost for one pass of the legacy graph and the lean pipeline.

Builds the real guard, intent, generate, and compose prompts. Counts tokens
with tiktoken (or a character estimate). Prices come from the public OpenRouter
model list. Nothing in this module calls a model or opens the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from education_platform.ai_platform.gateway.registry import openrouter_model_id, resolve
from education_platform.core.config import Settings
from education_platform.modules.guard.classifier import DATA_INJECTION, DATA_OFF_TOPIC, guard_state
from education_platform.modules.text_to_sql.nodes.compose_answer import (
    _build_messages as compose_messages,
)
from education_platform.modules.text_to_sql.nodes.compose_answer import (
    _roster_with_attribute_shape,
    _roster_with_attributes_shape,
    _single_column_list_shape,
    small_result_table,
)
from education_platform.modules.text_to_sql.nodes.generate_sql import _build_messages
from education_platform.modules.text_to_sql.nodes.injection_guard import _heuristic_injection
from education_platform.modules.text_to_sql.nodes.intent_router import intent_messages
from education_platform.modules.text_to_sql.nodes.load_schema import _load_filtered_schema_context
from education_platform.modules.text_to_sql.pipeline import LEAN_MAX_RETRIES
from education_platform.modules.text_to_sql.state import MAX_RETRIES
from evals.pricing import ModelPrice, count_json, count_messages, count_text, tokenizer_name

_JEV_COMPLETION_TOKENS = 20
_SQL_SAMPLE = (
    "SELECT sp.full_name, ROUND(AVG(qa.score_percent), 2) AS average_score_percent\n"
    "FROM quiz_attempts qa\n"
    "WHERE qa.score_percent IS NOT NULL"
)
_INTENT_SAMPLE = (
    '{"intent": "average_mastery_by_subject", "confidence": "high", '
    '"parameters": {"subject": "Mathematics"}, "operation": "average", '
    '"ambiguous": false, "boundary_conflict": false}'
)
_COMPOSE_SAMPLE = "Your class average in Mathematics is 70."
_REPAIR_SQL = "SELECT 1"
_REPAIR_ERROR = "validate_sql: the statement was rejected"

_STAGES = ("guard", "intent", "generate", "compose")


@dataclass
class Bucket:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, calls: int, prompt_tokens: int, completion_tokens: int) -> None:
        self.calls += calls
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens


def _scale(bucket: Bucket, factor: int) -> Bucket:
    return Bucket(
        bucket.calls * factor,
        bucket.prompt_tokens * factor,
        bucket.completion_tokens * factor,
    )


@dataclass
class PipelineCost:
    expected: dict[str, Bucket] = field(default_factory=dict)
    pipeline_worst: dict[str, Bucket] = field(default_factory=dict)
    transport_worst: dict[str, Bucket] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in _STAGES:
            self.expected.setdefault(name, Bucket())
            self.pipeline_worst.setdefault(name, Bucket())
            self.transport_worst.setdefault(name, Bucket())


@dataclass(frozen=True, slots=True)
class CostReport:
    legacy_model: str
    lean_sql_model: str
    lean_compose_model: str
    jev_model: str
    prices: dict[str, ModelPrice]
    price_note: str
    tokenizer: str
    jev_attempts: int
    gateway_attempts: int
    legacy: PipelineCost
    lean: PipelineCost
    completion_tokens: dict[str, int]

    @property
    def expected_usd(self) -> float:
        return self._usd(self.legacy, "expected") + self._usd(self.lean, "expected")

    @property
    def pipeline_worst_usd(self) -> float:
        return self._usd(self.legacy, "pipeline_worst") + self._usd(self.lean, "pipeline_worst")

    @property
    def worst_usd(self) -> float:
        return self._usd(self.legacy, "transport_worst") + self._usd(self.lean, "transport_worst")

    def _usd(self, pipeline: PipelineCost, which: str) -> float:
        stages: dict[str, Bucket] = getattr(pipeline, which)
        if pipeline is self.legacy:
            chat = self.prices[self.legacy_model]
        else:
            chat = self.prices[self.lean_sql_model]
            compose = self.prices[self.lean_compose_model]
            return (
                _bucket_usd(stages["guard"], self.prices[self.jev_model])
                + _bucket_usd(stages["intent"], chat)
                + _bucket_usd(stages["generate"], chat)
                + _bucket_usd(stages["compose"], compose)
            )
        return (
            _bucket_usd(stages["guard"], self.prices[self.jev_model])
            + _bucket_usd(stages["intent"], chat)
            + _bucket_usd(stages["generate"], chat)
            + _bucket_usd(stages["compose"], chat)
        )


def _bucket_usd(bucket: Bucket, price: ModelPrice) -> float:
    return (
        bucket.prompt_tokens * price.prompt_per_token
        + bucket.completion_tokens * price.completion_per_token
    )


def _route(item: dict[str, Any]) -> str:
    question = str(item.get("question") or "")
    if _heuristic_injection(question):
        return "refuse_regex"
    route = item.get("route")
    if route in {"template", "free_form", "refuse", "refuse_regex"}:
        return str(route)
    if item.get("expect_refused"):
        return "refuse"
    if item.get("expect_intent"):
        return "template"
    return "free_form"


def compose_call_count(rows: object) -> int:
    """1 when compose_answer would call the model for these rows."""
    if not isinstance(rows, list) or not rows:
        return 0
    if not all(isinstance(row, dict) for row in rows):
        return 1
    typed: list[dict[str, Any]] = rows
    row_count = len(typed)
    if row_count == 1 and len(typed[0]) == 1:
        return 0
    if _single_column_list_shape(typed, row_count) is not None:
        return 0
    if _roster_with_attribute_shape(typed, row_count) is not None:
        return 0
    if _roster_with_attributes_shape(typed, row_count) is not None:
        return 0
    if small_result_table(typed, row_count) is not None:
        return 0
    return 1


def estimate(
    questions: list[dict[str, Any]],
    settings: Settings,
    prices: dict[str, ModelPrice],
    price_note: str,
) -> CostReport:
    schema = _load_filtered_schema_context()
    completion = {
        "guard": _JEV_COMPLETION_TOKENS,
        "intent": count_text(_INTENT_SAMPLE),
        "generate": count_text(_SQL_SAMPLE),
        "compose": count_text(_COMPOSE_SAMPLE),
    }
    legacy = PipelineCost()
    lean = PipelineCost()
    jev_attempts = settings.jev_max_retries + 1
    gateway_attempts = settings.ai_gateway_max_retries + 1
    for item in questions:
        _add_question(
            item, settings, schema, completion, legacy, lean, jev_attempts, gateway_attempts
        )
    lean_sql = openrouter_model_id(resolve("text_to_sql.generate", settings).model)
    lean_compose = openrouter_model_id(resolve("text_to_sql.compose", settings).model)
    legacy_model = openrouter_model_id(settings.openrouter_model)
    return CostReport(
        legacy_model=legacy_model,
        lean_sql_model=lean_sql,
        lean_compose_model=lean_compose,
        jev_model=settings.resolved_guard_jev_model,
        prices=prices,
        price_note=price_note,
        tokenizer=tokenizer_name(),
        jev_attempts=jev_attempts,
        gateway_attempts=gateway_attempts,
        legacy=legacy,
        lean=lean,
        completion_tokens=completion,
    )


def _add_question(
    item: dict[str, Any],
    settings: Settings,
    schema: str,
    completion: dict[str, int],
    legacy: PipelineCost,
    lean: PipelineCost,
    jev_attempts: int,
    gateway_attempts: int,
) -> None:
    question = str(item.get("question") or "")
    route = _route(item)
    refused = route in {"refuse", "refuse_regex"}
    compose_calls = 0 if refused else compose_call_count(item.get("expect_rows"))
    compose_prompt = 0
    if compose_calls:
        rows = item.get("expect_rows")
        typed = rows if isinstance(rows, list) else []
        compose_prompt = count_messages(compose_messages(question, typed))

    legacy_guards = [] if route == "refuse_regex" else _legacy_guard_prompts(question, settings)
    lean_guard = 0 if route == "refuse_regex" else count_json(_lean_guard_body(question, settings))
    # A batched Jev reply carries both decisions. Count 20 tokens for each.
    lean_guard_completion = 0 if route == "refuse_regex" else completion["guard"] * 2
    intent_prompt = count_messages(intent_messages(question))
    legacy_generate = _generate_tokens(question, schema, lean=False, repair=False)
    lean_generate = _generate_tokens(question, schema, lean=True, repair=False)
    legacy_repair = _generate_tokens(question, schema, lean=False, repair=True)
    lean_repair = _generate_tokens(question, schema, lean=True, repair=True)

    _fill_same(
        legacy,
        "guard",
        calls=len(legacy_guards),
        prompt=sum(legacy_guards),
        completion=len(legacy_guards) * completion["guard"],
        transport_factor=jev_attempts,
    )
    _fill_same(
        lean,
        "guard",
        calls=0 if route == "refuse_regex" else 1,
        prompt=lean_guard,
        completion=lean_guard_completion,
        transport_factor=jev_attempts,
    )
    _fill_same(
        legacy,
        "intent",
        calls=0 if refused else 1,
        prompt=0 if refused else intent_prompt,
        completion=0 if refused else completion["intent"],
        transport_factor=1,
    )
    lean_intent_expected = 0 if route == "refuse_regex" else 1
    # A regex hit cancels intent, but the task is already started, so the worst
    # case still pays for that one call.
    lean_intent_worst = 1
    _fill_expected_and_worst(
        lean,
        "intent",
        expected_calls=lean_intent_expected,
        worst_calls=lean_intent_worst,
        prompt_each=intent_prompt,
        completion_each=completion["intent"],
        transport_factor=gateway_attempts,
    )
    _fill_generate(
        legacy,
        expected_calls=0 if route != "free_form" else 1,
        worst_normal=0 if refused else 1,
        worst_repairs=0 if refused else MAX_RETRIES,
        normal_prompt=legacy_generate,
        repair_prompt=legacy_repair,
        completion_each=completion["generate"],
        transport_factor=1,
    )
    if route == "refuse_regex":
        lean_expected, lean_worst_normal, lean_worst_repairs = 0, 1, 0
    elif route == "refuse":
        lean_expected, lean_worst_normal, lean_worst_repairs = 1, 1, 0
    else:
        lean_expected, lean_worst_normal, lean_worst_repairs = 1, 1, LEAN_MAX_RETRIES
    _fill_generate(
        lean,
        expected_calls=lean_expected,
        worst_normal=lean_worst_normal,
        worst_repairs=lean_worst_repairs,
        normal_prompt=lean_generate,
        repair_prompt=lean_repair,
        completion_each=completion["generate"],
        transport_factor=gateway_attempts,
    )
    _fill_same(
        legacy,
        "compose",
        calls=compose_calls,
        prompt=compose_prompt,
        completion=compose_calls * completion["compose"],
        transport_factor=1,
    )
    _fill_same(
        lean,
        "compose",
        calls=compose_calls,
        prompt=compose_prompt,
        completion=compose_calls * completion["compose"],
        transport_factor=gateway_attempts,
    )


def _generate_tokens(question: str, schema: str, *, lean: bool, repair: bool) -> int:
    return count_messages(
        _build_messages(
            question=question,
            schema_context=schema,
            user_role="teacher",
            prior_sql=_REPAIR_SQL if repair else None,
            prior_error=_REPAIR_ERROR if repair else None,
            lean=lean,
        )
    )


def _legacy_guard_prompts(question: str, settings: Settings) -> list[int]:
    model = settings.resolved_guard_jev_model
    state = guard_state(question)
    bodies = (
        {"model": model, "state": state, "questions": {"decision": DATA_INJECTION}},
        {"model": model, "state": state, "questions": {"decision": DATA_OFF_TOPIC}},
    )
    return [count_json(body) for body in bodies]


def _lean_guard_body(question: str, settings: Settings) -> dict[str, Any]:
    return {
        "model": settings.resolved_guard_jev_model,
        "state": guard_state(question),
        "questions": {"injection": DATA_INJECTION, "off_topic": DATA_OFF_TOPIC},
    }


def _fill_same(
    pipeline: PipelineCost,
    stage: str,
    *,
    calls: int,
    prompt: int,
    completion: int,
    transport_factor: int,
) -> None:
    pipeline.expected[stage].add(calls, prompt, completion)
    pipeline.pipeline_worst[stage].add(calls, prompt, completion)
    pipeline.transport_worst[stage].add(
        calls * transport_factor,
        prompt * transport_factor,
        completion * transport_factor,
    )


def _fill_expected_and_worst(
    pipeline: PipelineCost,
    stage: str,
    *,
    expected_calls: int,
    worst_calls: int,
    prompt_each: int,
    completion_each: int,
    transport_factor: int,
) -> None:
    pipeline.expected[stage].add(
        expected_calls, expected_calls * prompt_each, expected_calls * completion_each
    )
    pipeline.pipeline_worst[stage].add(
        worst_calls, worst_calls * prompt_each, worst_calls * completion_each
    )
    pipeline.transport_worst[stage].add(
        worst_calls * transport_factor,
        worst_calls * prompt_each * transport_factor,
        worst_calls * completion_each * transport_factor,
    )


def _fill_generate(
    pipeline: PipelineCost,
    *,
    expected_calls: int,
    worst_normal: int,
    worst_repairs: int,
    normal_prompt: int,
    repair_prompt: int,
    completion_each: int,
    transport_factor: int,
) -> None:
    pipeline.expected["generate"].add(
        expected_calls, expected_calls * normal_prompt, expected_calls * completion_each
    )
    worst_calls = worst_normal + worst_repairs
    worst_prompt = worst_normal * normal_prompt + worst_repairs * repair_prompt
    worst_completion = worst_calls * completion_each
    pipeline.pipeline_worst["generate"].add(worst_calls, worst_prompt, worst_completion)
    pipeline.transport_worst["generate"].add(
        worst_calls * transport_factor,
        worst_prompt * transport_factor,
        worst_completion * transport_factor,
    )


def render(report: CostReport) -> str:
    lines = [
        "models: "
        f"legacy={report.legacy_model} "
        f"lean_sql={report.lean_sql_model} "
        f"lean_compose={report.lean_compose_model} "
        f"jev={report.jev_model}",
        f"prices: {report.price_note}",
    ]
    for model_id, price in report.prices.items():
        lines.append(
            f"  {model_id}: prompt ${price.prompt_per_token:.12f}/token "
            f"completion ${price.completion_per_token:.12f}/token ({price.source})"
        )
    lines.append(f"tokenizer: {report.tokenizer}")
    lines.append(
        "completion estimate per call: "
        f"jev={report.completion_tokens['guard']} "
        f"intent={report.completion_tokens['intent']} "
        f"generate={report.completion_tokens['generate']} "
        f"compose={report.completion_tokens['compose']}"
    )
    lines.append(
        "transport attempts: "
        f"jev={report.jev_attempts} (jev_max_retries + 1), "
        f"lean gateway={report.gateway_attempts} (ai_gateway_max_retries + 1), "
        "legacy chat_completion=1"
    )
    lines.append(
        "assumption: a configured key, so guards and intent run. "
        "This dry-run did not load backend/.env and did not call a model."
    )
    for label, pipeline in (("legacy", report.legacy), ("lean", report.lean)):
        lines.append(f"{label} expected: {_stages(pipeline.expected)}")
        lines.append(f"{label} worst pipeline retries: {_stages(pipeline.pipeline_worst)}")
        lines.append(f"{label} worst with transport retries: {_stages(pipeline.transport_worst)}")
    lines.append(
        "tokens expected: "
        f"legacy {_tokens(report.legacy.expected)} "
        f"lean {_tokens(report.lean.expected)}"
    )
    lines.append(
        "tokens worst pipeline retries: "
        f"legacy {_tokens(report.legacy.pipeline_worst)} "
        f"lean {_tokens(report.lean.pipeline_worst)}"
    )
    lines.append(
        "tokens worst with transport retries: "
        f"legacy {_tokens(report.legacy.transport_worst)} "
        f"lean {_tokens(report.lean.transport_worst)}"
    )
    lines.append(
        "usd expected: "
        f"legacy ${report._usd(report.legacy, 'expected'):.6f} "
        f"lean ${report._usd(report.lean, 'expected'):.6f} "
        f"total ${report.expected_usd:.6f}"
    )
    lines.append(
        "usd worst pipeline retries: "
        f"legacy ${report._usd(report.legacy, 'pipeline_worst'):.6f} "
        f"lean ${report._usd(report.lean, 'pipeline_worst'):.6f} "
        f"total ${report.pipeline_worst_usd:.6f}"
    )
    lines.append(
        "usd worst with transport retries: "
        f"legacy ${report._usd(report.legacy, 'transport_worst'):.6f} "
        f"lean ${report._usd(report.lean, 'transport_worst'):.6f} "
        f"total ${report.worst_usd:.6f}"
    )
    lines.append("A live compare runs both pipelines, so the cap uses the combined total.")
    return "\n".join(lines)


def _stages(stages: dict[str, Bucket]) -> str:
    return " ".join(
        f"{name}={stages[name].calls} "
        f"(prompt {stages[name].prompt_tokens}, completion {stages[name].completion_tokens})"
        for name in _STAGES
    )


def _tokens(stages: dict[str, Bucket]) -> str:
    prompt = sum(bucket.prompt_tokens for bucket in stages.values())
    completion = sum(bucket.completion_tokens for bucket in stages.values())
    return f"prompt={prompt} completion={completion}"
