"""Plan, price and (only when asked) make the paid classifier calls.

``plan_jobs`` and ``estimate`` never touch the network. ``collect`` is the only function
that sends requests, and it does so through the ``httpx.AsyncClient`` it is handed.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from education_platform.core.config import Settings
from education_platform.modules.guard.classifier import guard_state
from education_platform.modules.judge.client import INPUT_USD_PER_MILLION, post_decisions
from education_platform.modules.judge.errors import JudgeError, JudgePaymentRequired
from education_platform.modules.judge.types import NoulAnswer
from evals.guard.dataset import Row
from evals.guard.surfaces import Check

# USD per million tokens (input, output). Jev output tokens are free.
GPT_PRICES: dict[str, tuple[float, float]] = {
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
}
UNKNOWN_GPT_PRICE = (5.00, 15.00)  # deliberately pessimistic
GPT_OUTPUT_TOKENS_ESTIMATE = 40
# Reserve this multiple of the estimate before each call: covers the estimator's error
# and post_decisions' one internal retry, so the cap is not crossed by in-flight calls.
RESERVE_FACTOR = 1.5
# Planning latencies for the runtime estimate (seconds per call).
LATENCY_ESTIMATE = {"jev": 0.8, "gpt": 1.2}
MAX_CONSECUTIVE_ERRORS = 15


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Job:
    check: Check
    row: Row

    @property
    def key(self) -> str:
        return f"{self.check.key}|{self.row.id}"


@dataclass(slots=True)
class CallResult:
    check: str
    row_id: str
    model: str
    probability: float | None = None
    positive: bool | None = None
    error: str | None = None
    status_code: int | None = None
    cost_usd: float = 0.0
    cost_source: str = "estimate"
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Budget:
    max_usd: float
    spent: float = 0.0
    reserved: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def reserve(self, amount: float) -> None:
        async with self._lock:
            if self.spent + self.reserved + amount > self.max_usd:
                raise BudgetExceeded(
                    f"next call (~${amount:.6f} reserved) would pass --max-usd "
                    f"{self.max_usd:.2f} (spent ${self.spent:.6f})"
                )
            self.reserved += amount

    async def settle(self, reserved: float, actual: float) -> None:
        async with self._lock:
            self.reserved -= reserved
            self.spent += actual


def jev_body(job: Job, settings: Settings) -> dict[str, Any]:
    history = list(job.row.history) if job.check.uses_history else []
    return {
        "model": settings.resolved_guard_jev_model,
        "state": guard_state(job.row.text, history),
        "questions": {"decision": job.check.jev_question},
    }


def gpt_body(job: Job, settings: Settings) -> dict[str, Any]:
    return {
        "model": settings.openrouter_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
        "messages": [
            {"role": "system", "content": job.check.gpt_prompt or ""},
            {"role": "user", "content": job.check.gpt_user_content(job.row)},
        ],
    }


def _tokens(payload: object) -> int:
    return math.ceil(len(json.dumps(payload, ensure_ascii=False)) / 4) + 20


def gpt_price(model: str) -> tuple[float, float, bool]:
    if model in GPT_PRICES:
        inp, out = GPT_PRICES[model]
        return inp, out, True
    return UNKNOWN_GPT_PRICE[0], UNKNOWN_GPT_PRICE[1], False


def estimate_job(job: Job, settings: Settings) -> float:
    if job.check.model_kind == "jev":
        return _tokens(jev_body(job, settings)) / 1e6 * INPUT_USD_PER_MILLION
    inp, out, _known = gpt_price(settings.openrouter_model)
    return (_tokens(gpt_body(job, settings)) * inp + GPT_OUTPUT_TOKENS_ESTIMATE * out) / 1e6


def plan_jobs(rows: tuple[Row, ...], checks: list[Check]) -> list[Job]:
    return [Job(check, row) for check in checks for row in rows if check.applies(row)]


@dataclass(frozen=True, slots=True)
class Estimate:
    calls: dict[str, int]
    usd: dict[str, float]
    total_calls: int
    total_usd: float
    runtime_seconds: float
    gpt_price_known: bool


def estimate(jobs: list[Job], settings: Settings, *, concurrency: int) -> Estimate:
    calls: dict[str, int] = {}
    usd: dict[str, float] = {}
    serial = 0.0
    for job in jobs:
        kind = job.check.model_kind
        calls[kind] = calls.get(kind, 0) + 1
        usd[kind] = usd.get(kind, 0.0) + estimate_job(job, settings)
        serial += LATENCY_ESTIMATE[kind]
    return Estimate(
        calls=calls,
        usd=usd,
        total_calls=len(jobs),
        total_usd=sum(usd.values()),
        runtime_seconds=serial / max(1, concurrency),
        gpt_price_known=gpt_price(settings.openrouter_model)[2],
    )


async def _call_jev(job: Job, settings: Settings, client: httpx.AsyncClient) -> CallResult:
    body = jev_body(job, settings)
    result = CallResult(
        check=job.check.key, row_id=job.row.id, model=settings.resolved_guard_jev_model
    )
    started = time.perf_counter()
    try:
        response = await post_decisions(
            state=body["state"],
            questions=body["questions"],
            settings=settings,
            client=client,
            model=settings.resolved_guard_jev_model,
        )
    except JudgePaymentRequired as exc:
        result.error = str(exc)
        result.status_code = 402
        result.latency_ms = (time.perf_counter() - started) * 1000
        return result
    except JudgeError as exc:
        result.error = str(exc)
        status = getattr(exc, "status_code", None)
        result.status_code = status if isinstance(status, int) else None
        result.latency_ms = (time.perf_counter() - started) * 1000
        return result
    result.latency_ms = (time.perf_counter() - started) * 1000
    answer = response.answers.get("decision")
    if not isinstance(answer, NoulAnswer):
        result.error = "Decisions response missing a noul probability"
    else:
        result.probability = answer.noul
    result.input_tokens = response.input_tokens
    result.output_tokens = response.output_tokens
    if response.cost_usd is not None:
        result.cost_usd, result.cost_source = response.cost_usd, "usage.cost"
    elif response.input_tokens is not None:
        result.cost_usd = response.input_tokens / 1e6 * INPUT_USD_PER_MILLION
        result.cost_source = "tokens"
    else:
        result.cost_usd = estimate_job(job, settings)
    return result


async def _call_gpt(job: Job, settings: Settings, client: httpx.AsyncClient) -> CallResult:
    result = CallResult(check=job.check.key, row_id=job.row.id, model=settings.openrouter_model)
    url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key or ''}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/aushakanuj/education-platform",
        "X-Title": "CurioEd guard eval",
    }
    started = time.perf_counter()
    response: httpx.Response | None = None
    for attempt in range(2):
        try:
            response = await client.post(url, headers=headers, json=gpt_body(job, settings))
        except httpx.HTTPError as exc:
            if attempt == 1:
                result.error = f"GPT request failed: {type(exc).__name__}"
                return result
            await asyncio.sleep(2)
            continue
        if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
            await asyncio.sleep(2)
            continue
        break
    result.latency_ms = (time.perf_counter() - started) * 1000
    assert response is not None
    if response.status_code >= 400:
        result.error = f"GPT HTTP {response.status_code}"
        result.status_code = response.status_code
        return result
    body: Any = None
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        value = parsed[job.check.gpt_field or ""]
        if not isinstance(value, bool):
            raise TypeError("classifier field is not a boolean")
        result.positive = value is job.check.gpt_positive_when
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        # main's guards failed closed on malformed classifier output.
        result.error = f"malformed GPT classifier output: {type(exc).__name__}"
    usage = body.get("usage") if isinstance(body, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens, completion_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
    result.input_tokens = prompt_tokens if isinstance(prompt_tokens, int) else None
    result.output_tokens = completion_tokens if isinstance(completion_tokens, int) else None
    cost = usage.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        result.cost_usd, result.cost_source = float(cost), "usage.cost"
    elif result.input_tokens is not None:
        inp, out, _ = gpt_price(settings.openrouter_model)
        result.cost_usd = (result.input_tokens * inp + (result.output_tokens or 0) * out) / 1e6
        result.cost_source = "tokens"
    else:
        result.cost_usd = estimate_job(job, settings)
    return result


@dataclass(slots=True)
class Collection:
    results: list[CallResult]
    spent_usd: float
    aborted: str | None
    skipped_jobs: int


async def collect(
    jobs: list[Job],
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    max_usd: float,
    concurrency: int = 4,
    progress: bool = False,
) -> Collection:
    """Run ``jobs`` until done, the spend cap, a 402, or a run of consecutive errors."""
    budget = Budget(max_usd=max_usd)
    queue: asyncio.Queue[Job] = asyncio.Queue()
    for job in jobs:
        queue.put_nowait(job)
    results: list[CallResult] = []
    state: dict[str, Any] = {"aborted": None, "consecutive_errors": 0, "done": 0}

    async def worker() -> None:
        while state["aborted"] is None:
            try:
                job = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            reserve = estimate_job(job, settings) * RESERVE_FACTOR
            try:
                await budget.reserve(reserve)
            except BudgetExceeded as exc:
                state["aborted"] = f"spend cap: {exc}"
                return
            call = _call_jev if job.check.model_kind == "jev" else _call_gpt
            result = await call(job, settings, client)
            if result.error is not None and result.cost_source == "estimate":
                result.cost_usd = reserve  # unknown charge on a failed call: assume the reserve
            await budget.settle(reserve, result.cost_usd)
            results.append(result)
            state["done"] += 1
            if result.status_code == 402:
                state["aborted"] = "OpenRouter returned 402 (insufficient credits)"
            elif result.error is not None:
                state["consecutive_errors"] += 1
                if state["consecutive_errors"] >= MAX_CONSECUTIVE_ERRORS:
                    state["aborted"] = (
                        f"{MAX_CONSECUTIVE_ERRORS} consecutive errors: {result.error}"
                    )
            else:
                state["consecutive_errors"] = 0
            if progress and state["done"] % 50 == 0:
                print(f"  {state['done']}/{len(jobs)} calls, spent ${budget.spent:.4f}", flush=True)

    await asyncio.gather(*(worker() for _ in range(max(1, concurrency))))
    return Collection(
        results=results,
        spent_usd=budget.spent,
        aborted=state["aborted"],
        skipped_jobs=len(jobs) - len(results),
    )
