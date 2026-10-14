"""Insights endpoints — one register, role-dependent scope."""

from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from education_platform.api.deps import ScopedRequest, scoped
from education_platform.core.errors import DomainError
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.insights import service
from education_platform.modules.insights.schemas import (
    DashboardSummaryOut,
    StudentDetailOut,
    StudentSummaryOut,
    StudentSummaryPage,
)

router = APIRouter(tags=["insights"])


def _describe(scope: Scope) -> str:
    if scope.unrestricted:
        return "Whole institution"
    if scope.is_empty or not scope.student_ids:
        return "No students in scope"
    if scope.self_student_id is not None and scope.student_ids == {scope.self_student_id}:
        return "Your own record only"
    students = len(scope.student_ids)
    assignments = len(scope.taught_offering_sections)
    return (
        f"{students} student{'s' if students != 1 else ''} across "
        f"{assignments} assignment{'s' if assignments != 1 else ''}"
    )


@router.get("/insights/dashboard", response_model=DashboardSummaryOut)
async def get_dashboard_summary(
    request: ScopedRequest = Depends(scoped("insights.dashboard")),
) -> DashboardSummaryOut:
    """Headline numbers for the admin dashboard, narrowed to what the caller may see."""
    summary = await service.dashboard_summary(request.session, request.scope)

    await request.record_rows(len(summary.subjects), detail="dashboard")

    return DashboardSummaryOut(scope_description=_describe(request.scope), **asdict(summary))


@router.get("/insights/students", response_model=StudentSummaryPage)
async def list_student_summaries(
    subject: str | None = Query(default=None),
    grade: str | None = Query(default=None),
    section: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=service.MAX_ROWS),
    request: ScopedRequest = Depends(scoped("insights.students")),
) -> StudentSummaryPage:
    rows = await service.query_student_360(
        request.session,
        request.scope,
        subject=subject,
        grade=grade,
        section=section,
        limit=limit,
    )

    await request.record_rows(
        len(rows),
        detail=", ".join(
            part
            for part in (
                f"subject={subject}" if subject else "",
                f"grade={grade}" if grade else "",
                f"section={section}" if section else "",
            )
            if part
        )
        or None,
    )

    return StudentSummaryPage(
        scope_description=_describe(request.scope),
        rows_returned=len(rows),
        items=[StudentSummaryOut(**asdict(row)) for row in rows],
    )


@router.get("/insights/students/{student_id}", response_model=StudentDetailOut)
async def get_student_detail(
    student_id: UUID,
    request: ScopedRequest = Depends(scoped("insights.student_detail")),
) -> StudentDetailOut:
    detail = await service.student_detail(request.session, request.scope, student_id)

    await request.record_rows(
        0 if detail is None else len(detail.subjects),
        detail=f"student={student_id}" + ("" if detail else " (not in scope)"),
    )

    if detail is None:
        raise DomainError("No such student", status_code=404)

    return StudentDetailOut(**asdict(detail))
