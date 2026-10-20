"""Routes approved in-domain questions to governed YAML templates or free-form SQL.

Role gate on template matching, enforced in code, not just in the router prompt:
every one of `intent_templates.yaml`'s templates hand-authors its own row-scoping
predicate against a *teacher* identity (`ta.teacher_user_id = :current_user_id`, or —
for `list_school_subjects` — no per-user predicate at all, deliberately treated as
teacher-only anyway per the API's own charter: see `router.py`'s module docstring on why
student/admin/parent access is a separate, not-yet-made decision). `decision_rules` in
the YAML already tells the classifier "Admin-, Student-, and Parent-scoped questions
remain free_form," but that was, until now, the *only* enforcement of it — an LLM
instruction, not a structural check. Today's sole caller (`router.py`'s `/text-to-sql/ask`
endpoint) already hardcodes `state["user_role"] = "teacher"` and gates on
`require_role("teacher")` before the graph even runs, so this has never been reachable
with a different role in production — but `intent_router` is a pure function of `state`,
not of who happens to call it today, and nothing stopped a future second caller (another
endpoint reusing `build_text_to_sql_graph()`) from invoking it with `user_role` set to
`"admin"`/`"student"`/`"parent"` and getting a template match anyway. The wrong role's
`user_id` simply not matching any `teaching_assignments.teacher_user_id` row would still
mean an empty result rather than a data leak — this was never a live vulnerability — but
that safety is incidental to the schema, not a designed guarantee, unlike every other
role boundary in this pipeline (`apply_role_scope`'s allowlists, `_ROLE_FORBIDDEN_TABLES`),
which are all enforced in code. The check below closes that gap the same way: template
matching is skipped entirely — falling through to `_free_form`, the same fallback an
off-topic or ambiguous question already takes — for any role other than `"teacher"`,
before the classifier LLM call even runs (a free efficiency win for a role that could
never validly match anyway, not just a correctness fix).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from education_platform.core.config import get_settings
from education_platform.modules.assistant.openrouter import OpenRouterError, chat_completion_json
from education_platform.modules.text_to_sql.llm import gateway_json
from education_platform.modules.text_to_sql.meter import note_llm_call
from education_platform.modules.text_to_sql.pipeline import use_text_to_sql_gateway
from education_platform.modules.text_to_sql.state import TextToSQLState

_TEMPLATE_FILE: Final[str] = "intent_templates.yaml"
_TEXT_TO_SQL_ROOT = Path(__file__).resolve().parents[1]


class _RouterDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: str | None = None
    confidence: float = 0.0
    parameters: dict[str, Any] = Field(default_factory=dict)
    operation: str | None = None
    ambiguous: bool = False
    boundary_conflict: bool = False


@lru_cache(maxsize=1)
def _load_catalog() -> dict[str, Any]:
    path = _TEXT_TO_SQL_ROOT / "config" / _TEMPLATE_FILE
    with path.open(encoding="utf-8") as stream:
        catalog = yaml.safe_load(stream)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("templates"), list):
        raise ValueError(f"invalid intent-router catalogue: {path}")
    return catalog


def _router_context(catalog: dict[str, Any]) -> str:
    config = catalog.get("intent_router", {})
    templates = []
    for template in catalog["templates"]:
        templates.append(
            {
                "name": template.get("name"),
                "description": template.get("description"),
                "parameters": template.get("parameters", {}),
                "requires_signoff": template.get("requires_signoff", False),
                "router": template.get("router", {}),
            }
        )
    return json.dumps({"intent_router": config, "templates": templates}, ensure_ascii=True)


def _normalize_parameters(parameters: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(parameters)
    subject_rules = catalog.get("intent_router", {}).get("normalization", {}).get("subject", {})
    aliases = subject_rules.get("aliases", {})
    if isinstance(aliases, dict) and normalized.get("subject") in aliases:
        normalized["subject"] = aliases[normalized["subject"]]
    return normalized


def _matches_required_keywords(template: dict[str, Any], question: str) -> bool:
    """A live incident (golden-eval row 25) showed the classifier can match a question to
    a template whose own domain the question never mentions at all: "Do any of my students
    have a mastery score of exactly 0?" was matched to `students_below_attendance_threshold`
    at 0.9 confidence — a fabricated wrong-template match, not just a fabricated parameter
    value (the number "0" genuinely appears in the question, so a numeric-presence check
    alone would not have caught this; the mismatch is which *metric* that number belongs
    to). `router.requires_keywords`, when a template declares it, is a small, explicit,
    human-reviewed list of words the template's own domain is expected to be named by
    (e.g. "attendance" for the attendance-threshold template) — at least one must appear
    in the raw question, case-insensitively, or the match is refused here, before
    confidence/parameter checks even run. Templates that don't declare this list are
    unaffected (returns True) — this is an opt-in, additive safety net, not a retroactive
    requirement on every template.
    """
    keywords = template.get("router", {}).get("requires_keywords")
    if not keywords:
        return True
    lowered = question.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


_NUMBER = re.compile(r"\d")


def _has_unsupported_qualifier(
    template: dict[str, Any], question: str, catalog: dict[str, Any]
) -> bool:
    """True when the question narrows its subject matter in a way this template has no
    parameter to honour, so answering from the template would silently drop the narrowing.

    The router's own rule ("unsupported qualifiers force free_form") was only a prompt
    instruction and the classifier did not reliably follow it: "How many students are doing
    well *in Mathematics*?" matched `students_meeting_performance_bar` (no subject
    parameter) and was answered with a figure covering all of the teacher's subjects, and
    "How many students have attendance below *40%*?" was once matched to
    `students_needing_support` (no number parameter), answering under a different
    criterion. Enforced here instead, deterministically:

    * a subject name (or alias) in the question needs a `subject` parameter;
    * a digit in the question needs a number/integer/string parameter to carry it — so
      "section 8A" (string parameter) and "top 10" (integer) are unaffected, while a
      number given to a template that takes none (count, support, doing-well, ...) is.

    Refusing only ever falls back to the free-form path; it can never produce a wrong
    template answer.
    """
    parameters = template.get("parameters", {}) or {}
    if "subject" not in parameters:
        rules = catalog.get("intent_router", {}).get("normalization", {}).get("subject", {})
        names = [*rules.get("canonical_values", []), *rules.get("aliases", {})]
        if names:
            pattern = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
            if re.search(rf"\b(?:{pattern})\b", question, re.IGNORECASE):
                return True
    takes_a_value = any(
        definition.get("type") in {"number", "integer", "string"}
        for definition in parameters.values()
    )
    return not takes_a_value and _NUMBER.search(question) is not None


def _valid_parameters(template: dict[str, Any], parameters: dict[str, Any]) -> bool:
    definitions = template.get("parameters", {})
    router = template.get("router", {})
    for name in router.get("required_parameters", []):
        if name not in parameters or parameters[name] in (None, ""):
            return False
    for name, definition in definitions.items():
        # An explicit null from the classifier means the same as an omitted key: "the
        # question did not state this". Without this, `{"subject": null}` for the optional
        # `latest_quiz_attempt.subject` was checked against the enum, rejected, and every
        # unqualified "Who took the last quiz?" fell through to free-form.
        if parameters.get(name) is None:
            if definition.get("required", False):
                return False
            parameters.pop(name, None)
            if "default" in definition:
                parameters[name] = definition["default"]
            continue
        value = parameters[name]
        if definition.get("type") == "enum" and value not in definition.get("values", []):
            return False
        if definition.get("type") in {"number", "integer"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            if definition.get("type") == "integer" and not isinstance(value, int):
                return False
            if "min" in definition and value < definition["min"]:
                return False
            if "max" in definition and value > definition["max"]:
                return False
        if "pattern" in definition and (
            not isinstance(value, str) or re.fullmatch(definition["pattern"], value) is None
        ):
            return False
    return True


# `LIMIT :limit` or `LIMIT COALESCE(:limit, <n>)` — the only two shapes a template may use
# for a caller-supplied row limit.
_PARAMETERIZED_LIMIT = re.compile(
    r"\bLIMIT\s+(?:COALESCE\s*\(\s*:limit\s*,\s*\d+\s*\)|:limit\b)", re.IGNORECASE
)


def _inline_limit(sql: str, template: dict[str, Any], parameters: dict[str, Any]) -> str | None:
    """Replace a parameterized `LIMIT` with the validated integer as a literal, or `None`
    if the SQL still carries a bind placeholder for it afterwards (caller routes to free-form).

    `validate_sql` only recognizes a literal `LIMIT n` (`_limit_value`); anything else is
    treated as "no limit" and overwritten with `LIMIT {DEFAULT_ROW_LIMIT}`, so a bound
    `LIMIT :limit` / `COALESCE(:limit, 5)` silently returned every row for "top 5". The value
    is safe to splice in as text: `_valid_parameters` has already checked it is a real
    `int` (not a bool) within the template's declared `min`/`max`, and a missing or null
    value falls back to the template's declared `default`.
    """
    definition = template.get("parameters", {}).get("limit")
    if definition is None:
        return sql
    value = parameters.get("limit")
    if value is None:
        value = definition.get("default")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    inlined = _PARAMETERIZED_LIMIT.sub(f"LIMIT {value}", sql)
    return None if ":limit" in inlined else inlined


def template_route_min_confidence() -> float:
    """The configured confidence floor a template match must clear (`_select_template`'s
    own threshold, exposed here so other nodes — `sanity_check`, to flag a borderline
    match — read the same live value from `intent_templates.yaml` rather than a second,
    driftable copy of the literal 0.90 default)."""
    catalog = _load_catalog()
    routing_policy = catalog.get("intent_router", {}).get("routing_policy", {})
    threshold = routing_policy.get("template_route_min_confidence", 0.90)
    if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        return 0.90
    return float(threshold)


def _select_template(
    decision: _RouterDecision, catalog: dict[str, Any], question: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    routing_policy = catalog.get("intent_router", {}).get("routing_policy", {})
    threshold = routing_policy.get("template_route_min_confidence", 0.90)
    if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        return None
    if not 0 <= decision.confidence <= 1:
        return None
    if decision.confidence < threshold or decision.intent is None:
        return None
    if decision.ambiguous or decision.boundary_conflict:
        return None
    for template in catalog["templates"]:
        if template.get("name") != decision.intent:
            continue
        router = template.get("router", {})
        if template.get("requires_signoff") or router.get("requires_approved_policy"):
            return None
        if decision.operation not in router.get("supported_operations", []):
            return None
        if not _matches_required_keywords(template, question):
            return None
        if _has_unsupported_qualifier(template, question, catalog):
            return None
        parameters = _normalize_parameters(decision.parameters, catalog)
        if not _valid_parameters(template, parameters):
            return None
        return template, parameters
    return None


def _free_form(state: TextToSQLState, decision: _RouterDecision | None = None) -> TextToSQLState:
    return {
        **state,
        "intent_route": "free_form",
        "intent": decision.intent if decision else None,
        "intent_confidence": decision.confidence if decision else 0.0,
        "intent_parameters": decision.parameters if decision else {},
        "query_source": None,
        # No single fixed definition to name on the free-form path — and clears any
        # stale value a prior template-matched attempt on this same state may have set.
        "answer_criterion": None,
        # Same reasoning: a zero from generated SQL stays ambiguous, so its hedge applies.
        "zero_result_is_valid": False,
        "empty_answer": None,
        "zero_probe_sql": {},
        "zero_probe_results": None,
    }


def _zero_probes(
    template: dict[str, Any], parameters: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, str]:
    """Reference queries execute_sql should run if (and only if) this template's result
    comes back empty or zero, so the answer can say *why* instead of just hedging.

    Both are existing governed templates, reused verbatim rather than a second copy of
    their SQL: `count_my_students` shows the teacher's scope can see students at all (what
    makes a confident "none" trustworthy), and `my_subjects` shows whether an asked-about
    subject is one they teach."""
    by_name = {t.get("name"): t for t in catalog["templates"]}
    probes: dict[str, str] = {}

    def _sql(name: str) -> str | None:
        sql = (by_name.get(name) or {}).get("sql")
        return sql.strip() if isinstance(sql, str) and sql.strip() else None

    if template.get("zero_is_valid") and (sql := _sql("count_my_students")):
        probes["visible_students"] = sql
    asks_about_subject = "subject" in (template.get("parameters") or {}) and parameters.get(
        "subject"
    )
    if asks_about_subject and (sql := _sql("my_subjects")):
        probes["taught_subjects"] = sql
    return probes


_INTENT_SYSTEM = (
    "You are a conservative intent classifier. Use only the governed "
    "catalog below. Return JSON with intent, confidence, parameters, "
    "operation, ambiguous, and boundary_conflict. Choose null intent "
    "when no template is an exact semantic match. Never classify a "
    "template requiring signoff as usable.\n\n"
)


def intent_messages(question: str) -> list[dict[str, str]]:
    """The classifier prompt. The cost dry-run counts these tokens and does not send them."""
    catalog = _load_catalog()
    return [
        {"role": "system", "content": _INTENT_SYSTEM + _router_context(catalog)},
        {"role": "user", "content": question},
    ]


def _empty_answer(
    template: dict[str, Any], parameters: dict[str, Any], criterion: Any
) -> str | None:
    """The template's `empty_answer` sentence formatted with its validated parameters (and
    `criterion`), or `None` if it declares none or it can't be formatted. Only validated
    parameters are interpolated (enum / bounded number / pattern-checked string)."""
    text = template.get("empty_answer")
    if not isinstance(text, str) or not text.strip():
        return None
    values = {**parameters, "criterion": criterion if isinstance(criterion, str) else ""}
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return None


async def intent_router(state: TextToSQLState) -> TextToSQLState:
    # Every template is teacher-shaped by construction (see module docstring) — no
    # non-teacher role can ever validly match one, so skip template matching (and the
    # classifier call it would otherwise cost) entirely for any other role.
    if state.get("user_role") != "teacher":
        return _free_form(state)
    try:
        catalog = _load_catalog()
        settings = get_settings()
        if not settings.openrouter_configured:
            return _free_form(state)
        messages = intent_messages(state.get("question") or "")
        note_llm_call()
        if use_text_to_sql_gateway(settings, state):
            data = await gateway_json("text_to_sql.intent", messages, settings)
        else:
            data = await chat_completion_json(messages, settings=settings)
        decision = _RouterDecision.model_validate(data)
    except (OSError, ValueError, OpenRouterError, json.JSONDecodeError, TypeError, ValidationError):
        return _free_form(state)

    selected = _select_template(decision, catalog, state.get("question") or "")
    if selected is None:
        return _free_form(state, decision)

    template, parameters = selected
    criterion = template.get("criterion")
    if template.get("service") == "at_risk_students":
        # No SQL. The graph calls the at-risk service so the answer is the same
        # student set as GET /at-risk/flags for this viewer.
        return {
            **state,
            "intent_route": "template",
            "intent": template["name"],
            "intent_confidence": decision.confidence,
            "intent_parameters": parameters,
            "query_source": "service",
            "generated_sql": None,
            "answer_criterion": criterion
            if isinstance(criterion, str) and criterion.strip()
            else None,
            "zero_result_is_valid": bool(template.get("zero_is_valid")),
            "empty_answer": _empty_answer(template, parameters, criterion),
            "zero_probe_sql": {},
            "zero_probe_results": None,
            "error": None,
        }
    shape = parameters.get("shape")
    sql_key = f"sql_{shape}" if shape else "sql"
    sql = template.get(sql_key)
    if not isinstance(sql, str) or not sql.strip():
        return _free_form(state, decision)
    sql = _inline_limit(sql, template, parameters)
    if sql is None:
        return _free_form(state, decision)
    criterion = template.get("criterion")
    return {
        **state,
        "intent_route": "template",
        "intent": template["name"],
        "intent_confidence": decision.confidence,
        "intent_parameters": parameters,
        "query_source": "template",
        "generated_sql": sql.strip(),
        "answer_criterion": criterion if isinstance(criterion, str) and criterion.strip() else None,
        "zero_result_is_valid": bool(template.get("zero_is_valid")),
        "empty_answer": _empty_answer(template, parameters, criterion),
        "zero_probe_sql": _zero_probes(template, parameters, catalog),
        "zero_probe_results": None,
        "error": None,
    }


__all__ = ["intent_router", "template_route_min_confidence"]
