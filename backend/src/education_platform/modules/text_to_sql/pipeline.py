"""Which text-to-SQL pipeline and which LLM client a request uses."""

from __future__ import annotations

from typing import Final

from education_platform.ai_platform.gateway.flags import TEXT_TO_SQL, gateway_enabled
from education_platform.core.config import Settings
from education_platform.modules.text_to_sql.state import TextToSQLState

LEAN: Final[str] = "lean"
LEGACY: Final[str] = "legacy"
LEAN_MAX_RETRIES: Final[int] = 1


def pipeline_mode(settings: Settings) -> str:
    """``lean`` or ``legacy``. Any other value stays on the legacy graph."""
    if settings.text_to_sql_pipeline.strip().lower() == LEAN:
        return LEAN
    return LEGACY


def guard_batch_enabled(settings: Settings) -> bool:
    return bool(settings.text_to_sql_guard_batch)


def use_text_to_sql_gateway(settings: Settings, state: TextToSQLState | None = None) -> bool:
    """Lean always uses the gateway. Legacy uses it when the feature flag is on.

    An explicit ``pipeline="legacy"`` on the state stays on the legacy client even
    when the process default is lean, so a bench can run both in one process.
    """
    if state is not None and state.get("pipeline") == LEAN:
        return True
    if state is not None and state.get("pipeline") == LEGACY:
        return gateway_enabled(TEXT_TO_SQL, settings)
    if pipeline_mode(settings) == LEAN:
        return True
    return gateway_enabled(TEXT_TO_SQL, settings)
