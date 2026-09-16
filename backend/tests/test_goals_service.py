"""Unit tests for goals_service's pure status logic — no DB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from education_platform.modules.assessments.goals_service import compute_goal_status

DUE = datetime(2026, 6, 1, tzinfo=UTC)
BEFORE_DUE = DUE - timedelta(days=1)
AFTER_DUE = DUE + timedelta(days=1)


def test_achieved_when_current_meets_target() -> None:
    status = compute_goal_status(Decimal("70"), Decimal("70"), DUE, BEFORE_DUE)
    assert status == "achieved"


def test_achieved_when_current_exceeds_target_even_past_due() -> None:
    """Hitting the target always wins, even if it happened after the deadline."""
    status = compute_goal_status(Decimal("95"), Decimal("70"), DUE, AFTER_DUE)
    assert status == "achieved"


def test_on_track_before_due_and_below_target() -> None:
    status = compute_goal_status(Decimal("50"), Decimal("70"), DUE, BEFORE_DUE)
    assert status == "on_track"


def test_missed_after_due_and_below_target() -> None:
    status = compute_goal_status(Decimal("50"), Decimal("70"), DUE, AFTER_DUE)
    assert status == "missed"


def test_on_track_with_no_attempts_yet() -> None:
    """No scored attempts on the subtopic yet -> current_percent is None, not a failure."""
    status = compute_goal_status(None, Decimal("70"), DUE, BEFORE_DUE)
    assert status == "on_track"


def test_missed_with_no_attempts_and_past_due() -> None:
    status = compute_goal_status(None, Decimal("70"), DUE, AFTER_DUE)
    assert status == "missed"
