"""Answers the struggling / at-risk template from the at-risk flag service.

The previous template SQL (`mastery_percent < 60 OR attendance_percent < 80` on
`student_360`) was a second definition. It counted every student a teacher teaches
whose attendance was under 80%, including students whose only flag is attendance.
That flag has a null subject offering, and the shared scope predicate hides it from
teachers (docs/design/08-at-risk-early-warning.md §7.2). The SQL also missed a
student flagged only because mastery was declining. This node calls
`list_at_risk_students`, which dedupes `list_flags`, so the assistant and the
at-risk page name the same students for the same viewer.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from education_platform.db.session import get_session_factory
from education_platform.modules.at_risk.service import (
    AtRiskStudent,
    list_at_risk_students,
    record_flag_view,
)
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.authorization.scope import scope_for
from education_platform.modules.text_to_sql.state import (
    EXECUTION_ERROR,
    TextToSQLState,
    format_error,
)

logger = logging.getLogger(__name__)

_GENERIC_FAILURE_MESSAGE = "the at-risk list could not be loaded"


def _result_rows(students: list[AtRiskStudent], shape: object) -> list[dict[str, Any]]:
    if shape == "count":
        return [{"student_count": len(students)}]
    return [
        {"full_name": student.student_name, "reasons": "; ".join(student.reasons)}
        for student in students
    ]


async def resolve_at_risk(state: TextToSQLState) -> TextToSQLState:
    """Load the viewer's at-risk students and store them as the query result."""
    try:
        user_id = UUID(state["user_id"])
        institution_id = UUID(state["institution_id"])
        role = state["user_role"]
    except (KeyError, ValueError) as exc:
        logger.warning("resolve_at_risk: missing identity: %s", exc)
        return {
            **state,
            "query_result": None,
            "result_row_count": None,
            "error": format_error(EXECUTION_ERROR, _GENERIC_FAILURE_MESSAGE),
        }

    principal = Principal(
        user_id=user_id,
        institution_id=institution_id,
        email="",
        roles=frozenset({role}),
        student_profile_id=None,
        status="active",
    )
    shape = (state.get("intent_parameters") or {}).get("shape")
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            scope = await scope_for(session, principal)
            students = await list_at_risk_students(session, scope)
            await record_flag_view(
                session,
                institution_id=institution_id,
                actor_user_id=user_id,
                rows_returned=len(students),
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — user sees a generic message; the detail stays in the log
        logger.warning("resolve_at_risk: failed to load flags", exc_info=True)
        return {
            **state,
            "query_result": None,
            "result_row_count": None,
            "error": format_error(EXECUTION_ERROR, _GENERIC_FAILURE_MESSAGE),
            "audit_entry": {
                **(state.get("audit_entry") or {}),
                "execution_error_type": type(exc).__name__,
                "execution_error_detail": str(exc),
            },
        }

    rows = _result_rows(students, shape)
    return {
        **state,
        "query_result": rows,
        "result_row_count": len(rows),
        "error": None,
        "audit_entry": {
            **(state.get("audit_entry") or {}),
            "role_scope_applied": "at_risk_service",
            "scoped_by_user_id": str(user_id),
        },
    }


__all__ = ["resolve_at_risk"]
