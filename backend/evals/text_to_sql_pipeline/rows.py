"""Turn SQLAlchemy / YAML cells into one comparable shape.

Numeric values become ``int`` when they are whole, otherwise a plain decimal
string. ``Decimal('70.00')``, ``70``, and ``70.0`` therefore compare equal, and
``73.33`` does not depend on binary float formatting.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID


def canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): canonicalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [canonicalize(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, Decimal):
        return _number(value)
    if isinstance(value, float):
        return _number(Decimal(str(value)))
    if isinstance(value, int):
        return value
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return _maybe_number(value)
    return value


def _maybe_number(value: str) -> Any:
    stripped = value.strip()
    if not stripped or any(character.isalpha() for character in stripped):
        return value
    try:
        number = Decimal(stripped)
    except InvalidOperation:
        return value
    return _number(number)


def _number(value: Decimal) -> int | str:
    if value == value.to_integral_value():
        return int(value)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
