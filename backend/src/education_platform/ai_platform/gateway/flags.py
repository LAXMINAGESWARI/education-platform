"""Feature flags for the LLM gateway.

Every flag defaults off. A comma-separated ``AI_GATEWAY_FEATURES`` list enables
the same names as the per-feature booleans, so later migrations can flip one
caller without a new setting.
"""

from __future__ import annotations

from education_platform.core.config import Settings

POLICY_CHAT = "policy_chat"
TEXT_TO_SQL = "text_to_sql"
GENERATION = "generation"
FEEDBACK = "feedback"
REVIEW_CHAT = "review_chat"
AUTHORING = "authoring"


def gateway_enabled(feature: str, settings: Settings) -> bool:
    """True when this feature should call the gateway instead of its legacy client."""
    name = feature.strip().lower()
    if not name:
        return False
    listed = {
        part.strip().lower() for part in settings.ai_gateway_features.split(",") if part.strip()
    }
    if name in listed:
        return True
    toggles = {
        POLICY_CHAT: settings.ai_gateway_policy_chat,
        TEXT_TO_SQL: settings.ai_gateway_text_to_sql,
        GENERATION: settings.ai_gateway_generation,
        FEEDBACK: settings.ai_gateway_feedback,
        REVIEW_CHAT: settings.ai_gateway_review_chat,
        AUTHORING: settings.ai_gateway_authoring,
    }
    return bool(toggles.get(name, False))
