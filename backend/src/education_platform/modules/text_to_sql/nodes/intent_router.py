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


def _valid_parameters(template: dict[str, Any], parameters: dict[str, Any]) -> bool:
    definitions = template.get("parameters", {})
    router = template.get("router", {})
    for name in router.get("required_parameters", []):
        if name not in parameters or parameters[name] in (None, ""):
            return False
    for name, definition in definitions.items():
        if name not in parameters:
            if definition.get("required", False):
                return False
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
    }


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
        data = await chat_completion_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a conservative intent classifier. Use only the governed "
                        "catalog below. Return JSON with intent, confidence, parameters, "
                        "operation, ambiguous, and boundary_conflict. Choose null intent "
                        "when no template is an exact semantic match. Never classify a "
                        "template requiring signoff as usable.\n\n" + _router_context(catalog)
                    ),
                },
                {"role": "user", "content": state.get("question") or ""},
            ],
            settings=settings,
        )
        decision = _RouterDecision.model_validate(data)
    except (OSError, ValueError, OpenRouterError, json.JSONDecodeError, TypeError, ValidationError):
        return _free_form(state)

    selected = _select_template(decision, catalog, state.get("question") or "")
    if selected is None:
        return _free_form(state, decision)

    template, parameters = selected
    shape = parameters.get("shape")
    sql_key = f"sql_{shape}" if shape else "sql"
    sql = template.get(sql_key)
    if not isinstance(sql, str) or not sql.strip():
        return _free_form(state, decision)
    return {
        **state,
        "intent_route": "template",
        "intent": template["name"],
        "intent_confidence": decision.confidence,
        "intent_parameters": parameters,
        "query_source": "template",
        "generated_sql": sql.strip(),
        "error": None,
    }


__all__ = ["intent_router", "template_route_min_confidence"]
