"""Save-time checks for a four-option item.

Structural rules refuse an empty, repeated, or mis-keyed option. The numeric
check evaluates an equality in the rationale on its own, so a written result
such as ``π(3)^2(5) = 27π`` cannot pass when the product is ``45π``.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Mapping

OPTION_LABELS = ("A", "B", "C", "D")

_DOLLAR = re.compile(r"\$([^$]+)\$")
_QUANTITY = re.compile(
    r"((?:\d+(?:\.\d+)?\s*)?(?:\\pi|π)(?:\s*(?:\^|\*\*)\s*\{?\d+\}?)?)\s*$",
    re.IGNORECASE,
)
_UNIT = re.compile(r"(?:\\mathrm|\\text)\{[^{}]*\}|\bcm\^?3\b|cm³", re.IGNORECASE)


def structural_problems(
    options: Mapping[str, str],
    correct_label: str,
    explanation: str,
) -> tuple[str, ...]:
    """Four non-empty distinct options, and a key whose text matches the explanation."""
    reasons: list[str] = []
    present = {label: options.get(label, "") for label in OPTION_LABELS}
    if any(not text.strip() for text in present.values()):
        reasons.append("each option A-D must be non-empty")
    normalized = [_norm_math(text) for text in present.values() if text.strip()]
    if len(normalized) == 4 and len(set(normalized)) < 4:
        reasons.append("options A-D must be distinct")
    if correct_label not in OPTION_LABELS:
        reasons.append("answer key must be one of A-D")
        return tuple(reasons)
    final = final_math_value(explanation)
    if final is not None and _quantitative(final):
        keyed = present[correct_label]
        if _norm_math(final) != _norm_math(keyed):
            reasons.append("explanation final value does not match the keyed option")
    return tuple(reasons)


def numeric_problems(
    options: Mapping[str, str],
    correct_label: str,
    explanation: str,
) -> tuple[str, ...]:
    """Evaluate rationale equalities independently of the number written after ``=``."""
    values = [(side, evaluate_expression(side)) for side in _equality_sides(explanation)]
    evaluated = [(side, value) for side, value in values if value is not None]
    if len(evaluated) >= 2:
        anchor = evaluated[0][1]
        if any(not _close(value, anchor) for _side, value in evaluated[1:]):
            return ("rationale arithmetic does not match its expression",)
    computed = next((value for side, value in evaluated if _has_operator(side)), None)
    if computed is None or correct_label not in OPTION_LABELS:
        return ()
    keyed = evaluate_expression(options.get(correct_label, ""))
    if keyed is None or _close(keyed, computed):
        return ()
    return ("keyed option does not equal the evaluated expression",)


def final_math_value(text: str) -> str | None:
    """Last delimited quantity in an explanation, if it states one."""
    dollars = [str(segment).strip() for segment in _DOLLAR.findall(text) if str(segment).strip()]
    if dollars:
        return dollars[-1]
    match = _QUANTITY.search(text.strip())
    if match is None:
        return None
    return str(match.group(1)).strip()


def evaluate_expression(expr: str) -> float | None:
    """Arithmetic value of one side, or None when the side is not a plain expression."""
    if not expr or "\\frac" in expr or "\\sqrt" in expr:
        return None
    prepared = _prepare(expr)
    has_quantity = any(character.isdigit() or character == "π" for character in expr)
    if not prepared or not (has_quantity or "pi" in prepared):
        return None
    try:
        tree = ast.parse(prepared, mode="eval")
    except SyntaxError:
        return None
    try:
        value = _eval_node(tree.body)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _equality_sides(text: str) -> list[str]:
    if "=" not in text:
        return []
    return [side.strip(" .") for side in re.split(r"\s*=\s*", text) if side.strip(" .")]


def _prepare(expr: str) -> str:
    text = _UNIT.sub("", expr)
    text = text.replace("\\pi", "π").replace("π", "pi")
    text = text.replace("\\times", "*").replace("\\cdot", "*").replace("×", "*").replace("·", "*")
    text = text.replace("^", "**")
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    text = text.replace("$", "")
    text = re.sub(r"(\d)\s*pi", r"\1*pi", text)
    text = re.sub(r"pi\s*\(", "pi*(", text)
    text = re.sub(r"\)\s*\(", ")*(", text)
    text = re.sub(r"(\d)\s*\(", r"\1*(", text)
    text = re.sub(r"\)\s*(\d)", r")*\1", text)
    text = re.sub(r"\)\s*pi", ")*pi", text)
    return text.replace(" ", "")


def _eval_node(node: ast.AST) -> float:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id == "pi":
        return math.pi
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
        value = _eval_node(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and isinstance(
        node.op, ast.Add | ast.Sub | ast.Mult | ast.Div | ast.Pow
    ):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        return float(left**right)
    raise ValueError("unsupported expression")


def _has_operator(expr: str) -> bool:
    return any(symbol in expr for symbol in ("^", "*", "×", "·", "(", "\\pi", "π")) and bool(
        re.search(r"\d", expr)
    )


def _quantitative(text: str) -> bool:
    folded = text.casefold()
    return bool(re.search(r"\d", folded) or "π" in folded or "\\pi" in folded)


def _norm_math(text: str) -> str:
    value = text.casefold().replace("$", "").replace("\\pi", "π")
    value = re.sub(r"\s+", "", value)
    return value.replace("\\", "")


def _close(left: float, right: float) -> bool:
    scale = max(1.0, abs(left), abs(right))
    return abs(left - right) <= 1e-6 * scale
