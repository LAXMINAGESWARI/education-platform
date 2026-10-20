"""Entry node: refuses a question that looks like a prompt-injection/jailbreak attempt
before any SQL is even attempted — the first node in the graph, before `load_schema`.

**This is a cost/UX layer, not the security boundary.** Nothing about the safety of this
pipeline depends on this node catching anything. `apply_role_scope`'s row predicates are
built purely from `state["user_id"]`/`state["user_role"]`/`state["institution_id"]` and
never key off the question or the LLM's output — a successfully-generated adversarial
query is already neutralized downstream regardless of whether this node ever fires (see
that node's own docstring, and golden-eval rows 35-38, which cover exactly this scenario
and pass today with no guard in front of them at all). What this node buys is real but
narrower: skip the OpenRouter round-trip entirely for the subset of questions that are
*unambiguously* adversarial by surface phrasing, and give the audit trail a distinct,
honest signal ("this looked like an attack attempt") instead of lumping it in with an
ordinary answered-or-refused query. A false negative here (a phrasing this doesn't
recognize) costs one wasted generation call, never a data exposure; a false positive
costs one legitimate question an unnecessary refusal, never a silent wrong answer (see
`_MESSAGES` in honest_refusal.py — the refusal text is honest about being a refusal, not
disguised as a real "no data" answer).

**Borrowed from `assistant.graph`'s own `injection_guard`, not reinvented.** That pipeline
(a document-retrieval/summarization graph, structurally unrelated to this one) already has
a working two-stage version of exactly this idea: `assistant.graph._heuristic_injection`'s
regex list for the cases that are essentially always attacks with almost no legitimate
phrasing collision, falling through to an LLM classifier for anything the regex doesn't
recognize. This is a fresh implementation with its own state shape and its own classifier
prompt (this pipeline's actual stakes — bypassing role-based *data access* — are
different from that one's "stay on policy topic" framing, and `TextToSQLState` carries no
multi-turn `history` the way `AssistantGraphState` does, so there's no prior-turn context
to feed the classifier here), but the regex list itself is reused verbatim: these
behavioral tells ("ignore previous instructions", "developer mode", "jailbreak") aren't
specific to what the LLM is being asked to do, only to how someone is trying to talk to
it. `chat_completion_json`/`OpenRouterError` are imported directly from
`assistant.openrouter` rather than duplicated — `generate_sql.py` already does the same
for its own, unrelated OpenRouter call (`chat_completion`), confirming this is a genuinely
shared, module-agnostic utility, not something specific to the assistant pipeline it
happens to live in.

**Fail-open when unconfigured, fail-closed when erroring — same asymmetry as the
original, kept deliberately rather than "simplified" away.** HTTP 402 is the
exception: Jev credits can be exhausted while chat completions still work, so a
402 falls back to the regex result plus the GPT classifier this node used before
Jev. If that GPT call fails, the node fails closed. A missing key still cannot
call GPT, so it stays fail-open after the regex. If `OPENROUTER_API_KEY`
isn't set at all, this is a known, permanent deployment state, not a live problem — the
heuristic regex still ran, and refusing every question outright because the *second*
stage can't run would make this cost/UX layer into an availability outage, for a check
that was never the security boundary to begin with. If OpenRouter *is* configured but the
call itself fails (network, timeout, 5xx, malformed response), that's a live, transient
signal worth being cautious about — refusing this one question and asking the user to
retry costs far less than silently skipping a check that was specifically requested to
run. Both branches still leave `apply_role_scope` as the only thing anyone should trust
for actual safety, regardless of which way this asymmetry resolves for a given call.

**Off-topic detection (`OFF_TOPIC_REJECTED`) was briefly combined into this same
classifier call, then measured and reverted — see `question_validator.py`, the node that
now owns it.** The combined design traded a small amount of one-node-one-category purity
for not doubling this pipeline's per-question LLM cost, on the explicit condition that
combined-call accuracy had to match a separate-call baseline before it could be trusted.
That comparison hadn't actually been run before it shipped; running it after the fact
found a real, 100%-reproducible (8/8 repeated trials) false positive on an ordinary,
common self-reference question — "What subject do I teach?" — under the combined prompt,
while the equivalent separate, off-topic-only classifier call got the same question right
every time, and the rest of the same question shape (rows 16-18: "what sections am I
assigned to", "what is my own attendance rate", "am I currently teaching Grade 7") stayed
clean under both, so this wasn't a systematic weak spot for self-reference questions in
general — just a real, decisive miss on this one, common phrasing, caused by the combined
call's own framing. A common question failing 100% of the time in production is a real
cost, not the marginal one the combined design was supposed to be trading for; see
`question_validator.py`'s own docstring for the full numbers and the current design.
"""

from __future__ import annotations

import re
from typing import Any, Final

from education_platform.core.config import Settings, get_settings
from education_platform.modules.guard.classifier import (
    DATA_GPT_INJECTION,
    DATA_INJECTION,
    classify_noul,
    guard_state,
    is_positive,
    legacy_gpt_flag,
)
from education_platform.modules.judge.errors import JudgeError, JudgePaymentRequired
from education_platform.modules.text_to_sql.state import (
    INJECTION_BLOCKED,
    TextToSQLState,
    format_error,
)

# Reused verbatim from assistant.graph._INJECTION_PATTERNS -- see this module's own
# docstring for why the same list applies here despite the two pipelines being otherwise
# unrelated: these are tells about how someone is addressing the model, not about what
# the model is being asked to do.
# Known false positives ("jailbreak school tablets", "developer mode setting") stay
# until the shared-guards change.
_INJECTION_PATTERNS: Final[re.Pattern[str]] = re.compile(
    r"(ignore\s+(all\s+)?(previous|prior)\s+instructions|"
    r"forget\s+(all\s+)?(the\s+)?((previous|prior)\s+)?(instructions|commands)|"
    r"system\s+prompt|jailbreak|dan\s+mode|developer\s+mode|override\s+safety)",
    re.IGNORECASE,
)

_HEURISTIC_BLOCKED_REPLY: Final[str] = (
    "I can't process requests that try to override system instructions. "
    "Please rephrase your question about your school data."
)
_CLASSIFIER_BLOCKED_REPLY: Final[str] = (
    "I can't process that request. Ask a concrete question about your students, "
    "classes, or school data."
)
_GUARD_UNAVAILABLE_REPLY: Final[str] = (
    "This request's safety check is unavailable right now — please try again shortly."
)


def _note(state: TextToSQLState, **fields: Any) -> dict[str, Any]:
    return {**(state.get("audit_entry") or {}), **fields}


def _heuristic_injection(question: str) -> bool:
    return bool(_INJECTION_PATTERNS.search(question))


async def injection_guard(state: TextToSQLState) -> TextToSQLState:
    question = state.get("question") or ""

    if _heuristic_injection(question):
        return {
            **state,
            "audit_entry": _note(state, guard_stage="heuristic"),
            "error": format_error(INJECTION_BLOCKED, _HEURISTIC_BLOCKED_REPLY),
        }

    settings = get_settings()
    if not settings.openrouter_configured:
        # Fail open -- see module docstring's "Fail-open when unconfigured" section.
        return {**state, "audit_entry": _note(state, guard_stage="unconfigured")}

    try:
        # The question text only. Identity stays in state for apply_role_scope.
        probability = await classify_noul(
            state=guard_state(question),
            question=DATA_INJECTION,
            settings=settings,
        )
    except JudgePaymentRequired:
        return await _gpt_injection_fallback(state, question, settings)
    except JudgeError:
        # Fail closed -- see module docstring's "fail-closed when erroring" section.
        return {
            **state,
            "audit_entry": _note(state, guard_stage="jev_error"),
            "error": format_error(INJECTION_BLOCKED, _GUARD_UNAVAILABLE_REPLY),
        }

    noted = _note(state, guard_stage="jev", injection_probability=probability)
    if is_positive(probability, settings):
        return {
            **state,
            "audit_entry": noted,
            "error": format_error(INJECTION_BLOCKED, _CLASSIFIER_BLOCKED_REPLY),
        }

    return {**state, "audit_entry": noted}


async def _gpt_injection_fallback(
    state: TextToSQLState,
    question: str,
    settings: Settings,
) -> TextToSQLState:
    """Regex already ran. HTTP 402 uses the pre-Jev GPT classifier."""
    try:
        flagged = await legacy_gpt_flag(
            settings=settings,
            system=DATA_GPT_INJECTION,
            user=question,
            field="injection",
        )
    except JudgeError:
        return {
            **state,
            "audit_entry": _note(state, guard_stage="gpt_error"),
            "error": format_error(INJECTION_BLOCKED, _GUARD_UNAVAILABLE_REPLY),
        }
    noted = _note(state, guard_stage="gpt")
    if flagged:
        return {
            **state,
            "audit_entry": noted,
            "error": format_error(INJECTION_BLOCKED, _CLASSIFIER_BLOCKED_REPLY),
        }
    return {**state, "audit_entry": noted}
