"""Identity of a quiz stem, so near-copies count as one question.

Exact key keeps the numbers. Stem key masks them, so a cylinder with radius 3
and a cylinder with radius 4 are the same question. Token overlap catches a
reworded copy of that stem.
"""

from __future__ import annotations

import re

_SPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_TOKEN = re.compile(r"[a-z0-9]+")
_JACCARD_LIMIT = 0.85


def exact_key(prompt: str) -> str:
    """Normalized stem with the numbers kept."""
    text = prompt.casefold().replace("$", "")
    text = text.replace("\\", "")
    return _SPACE.sub(" ", text).strip()


def stem_key(prompt: str) -> str:
    """Normalized stem with numbers masked."""
    return _NUMBER.sub("#", exact_key(prompt))


def same_stem(left: str, right: str) -> bool:
    """True when two prompts are the same question, or a number-only variant."""
    if not left.strip() or not right.strip():
        return False
    if exact_key(left) == exact_key(right):
        return True
    if stem_key(left) == stem_key(right):
        return True
    return _jaccard(left, right) >= _JACCARD_LIMIT


def _jaccard(left: str, right: str) -> float:
    left_tokens = set(_TOKEN.findall(stem_key(left)))
    right_tokens = set(_TOKEN.findall(stem_key(right)))
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens & right_tokens
    return len(shared) / len(left_tokens | right_tokens)
