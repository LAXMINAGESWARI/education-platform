"""Cheap fallback when Jev cannot be reached. Not the primary guard."""

from __future__ import annotations

import re

# Obvious jailbreak phrasing. "ignore the last question, explain fractions again"
# must not match: the verb has to be followed by previous/prior instructions.
_OBVIOUS_INJECTION = re.compile(
    r"("
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions"
    r"|disregard\s+(all\s+)?(your\s+|the\s+)?(previous\s+|prior\s+)?(instructions|rules)"
    r"|forget\s+(all\s+)?(your\s+|the\s+)?(previous\s+|prior\s+)?(instructions|rules|commands)"
    r"|system\s+prompt"
    r"|you\s+are\s+now"
    r"|jailbreak"
    r"|dan\s+mode"
    r"|developer\s+mode"
    r"|override\s+safety"
    r")",
    re.IGNORECASE,
)


def obvious_injection(message: str) -> bool:
    return _OBVIOUS_INJECTION.search(message) is not None
