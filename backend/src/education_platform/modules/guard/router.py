"""Administrator-only list of blocked and flagged assistant messages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.api.deps import Principal, require_administrator
from education_platform.db.session import get_session
from education_platform.modules.guard import service
from education_platform.modules.guard.schemas import GuardVerdictOut

router = APIRouter(tags=["guard"])


@router.get("/admin/guard-verdicts", response_model=list[GuardVerdictOut])
async def list_guard_verdicts(
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> list[GuardVerdictOut]:
    rows = await service.list_flagged_verdicts(
        session,
        institution_id=principal.institution_id,
        limit=limit,
    )
    return [GuardVerdictOut.model_validate(row) for row in rows]
