"""Token and public-price estimate for the chat-guard dry-run.

``collect.estimate`` stays on its char/4 rates so the live reserve does not
move. This module prices the actual request bodies and a retry worst case.
It does not call a model.
"""

from __future__ import annotations

from dataclasses import dataclass

from education_platform.core.config import Settings
from education_platform.modules.guard.classifier import guard_state
from evals.guard.collect import GPT_OUTPUT_TOKENS_ESTIMATE, Job, gpt_body, jev_body
from evals.pricing import ModelPrice, count_json, count_messages

_JEV_COMPLETION_TOKENS = 20
_DATA_BATCH = frozenset({"data.jev_injection", "data.jev_off_topic"})


@dataclass(frozen=True, slots=True)
class PricedPlan:
    jev_calls: int
    gpt_calls: int
    prompt_tokens: int
    completion_tokens: int
    expected_usd: float
    worst_calls: int
    worst_prompt_tokens: int
    worst_completion_tokens: int
    worst_usd: float
    jev_attempts: int
    gpt_attempts: int


def price_jobs(
    jobs: list[Job],
    settings: Settings,
    prices: dict[str, ModelPrice],
    *,
    data_guard_batch: bool,
) -> PricedPlan:
    jev_model = settings.resolved_guard_jev_model
    gpt_model = settings.openrouter_model.removeprefix("openrouter/")
    jev_price = prices[jev_model]
    gpt_price = prices[gpt_model]
    jev_attempts = settings.jev_max_retries + 1
    gpt_attempts = 2
    jev_calls, jev_prompt, jev_completion = _jev(jobs, settings, data_guard_batch)
    gpt_calls, gpt_prompt, gpt_completion = _gpt(jobs, settings)
    expected = _usd(jev_prompt, jev_completion, jev_price) + _usd(
        gpt_prompt, gpt_completion, gpt_price
    )
    worst_prompt = jev_prompt * jev_attempts + gpt_prompt * gpt_attempts
    worst_completion = jev_completion * jev_attempts + gpt_completion * gpt_attempts
    worst = _usd(jev_prompt * jev_attempts, jev_completion * jev_attempts, jev_price) + _usd(
        gpt_prompt * gpt_attempts, gpt_completion * gpt_attempts, gpt_price
    )
    return PricedPlan(
        jev_calls=jev_calls,
        gpt_calls=gpt_calls,
        prompt_tokens=jev_prompt + gpt_prompt,
        completion_tokens=jev_completion + gpt_completion,
        expected_usd=expected,
        worst_calls=jev_calls * jev_attempts + gpt_calls * gpt_attempts,
        worst_prompt_tokens=worst_prompt,
        worst_completion_tokens=worst_completion,
        worst_usd=worst,
        jev_attempts=jev_attempts,
        gpt_attempts=gpt_attempts,
    )


def render_priced(
    plan: PricedPlan,
    prices: dict[str, ModelPrice],
    price_note: str,
    *,
    jev_model: str,
    gpt_model: str,
    tokenizer: str,
) -> str:
    lines = [
        f"priced models: jev={jev_model} gpt={gpt_model}",
        f"priced from: {price_note}",
        f"tokenizer: {tokenizer}",
    ]
    for model_id in (jev_model, gpt_model):
        price = prices[model_id]
        lines.append(
            f"  {model_id}: prompt ${price.prompt_per_token:.12f}/token "
            f"completion ${price.completion_per_token:.12f}/token ({price.source})"
        )
    lines.append(
        "priced calls: "
        f"jev={plan.jev_calls} gpt={plan.gpt_calls} "
        f"prompt_tokens={plan.prompt_tokens} completion_tokens={plan.completion_tokens}"
    )
    lines.append(f"priced usd expected: ${plan.expected_usd:.6f}")
    lines.append(
        "priced worst with transport retries: "
        f"calls={plan.worst_calls} "
        f"prompt_tokens={plan.worst_prompt_tokens} "
        f"completion_tokens={plan.worst_completion_tokens} "
        f"usd=${plan.worst_usd:.6f} "
        f"(jev attempts={plan.jev_attempts}, gpt attempts={plan.gpt_attempts})"
    )
    return "\n".join(lines)


def _jev(jobs: list[Job], settings: Settings, batch: bool) -> tuple[int, int, int]:
    calls = 0
    prompt = 0
    completion = 0
    grouped: dict[str, list[Job]] = {}
    for job in jobs:
        if job.check.model_kind != "jev":
            continue
        if batch and job.check.key in _DATA_BATCH:
            grouped.setdefault(job.row.id, []).append(job)
            continue
        body = jev_body(job, settings)
        calls += 1
        prompt += count_json(body)
        completion += _JEV_COMPLETION_TOKENS
    for group in grouped.values():
        questions = {
            job.check.task: job.check.jev_question
            for job in group
            if job.check.jev_question is not None
        }
        history = list(group[0].row.history) if group[0].check.uses_history else []
        body = {
            "model": settings.resolved_guard_jev_model,
            "state": guard_state(group[0].row.text, history),
            "questions": questions,
        }
        calls += 1
        prompt += count_json(body)
        completion += _JEV_COMPLETION_TOKENS * len(questions)
    return calls, prompt, completion


def _gpt(jobs: list[Job], settings: Settings) -> tuple[int, int, int]:
    calls = 0
    prompt = 0
    completion = 0
    for job in jobs:
        if job.check.model_kind != "gpt":
            continue
        body = gpt_body(job, settings)
        messages = body["messages"]
        calls += 1
        prompt += count_messages(
            [
                {"role": "system", "content": str(messages[0]["content"])},
                {"role": "user", "content": str(messages[1]["content"])},
            ]
        )
        completion += GPT_OUTPUT_TOKENS_ESTIMATE
    return calls, prompt, completion


def _usd(prompt: int, completion: int, price: ModelPrice) -> float:
    return prompt * price.prompt_per_token + completion * price.completion_per_token
