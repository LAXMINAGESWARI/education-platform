"""Self-evaluation API endpoints.

POST /api/v1/me/subjects/{subject_id}/self-evaluation  — record a confidence rating
GET  /api/v1/me/subjects/{subject_id}/self-evaluation  — read the latest rating
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.api.deps import get_scope
from education_platform.db.session import get_session
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.self_evaluation import service
from education_platform.modules.self_evaluation.schemas import (
    SelfEvaluationIn,
    SelfEvaluationOut,
)

router = APIRouter(tags=["self-evaluation"])


@router.post(
    "/me/subjects/{subject_id}/self-evaluation",
    response_model=SelfEvaluationOut,
    summary="Record a self-evaluation",
)
async def post_self_evaluation(
    subject_id: UUID,
    payload: SelfEvaluationIn,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> SelfEvaluationOut:
    return await service.record_self_evaluation(session, scope, subject_id, payload)


@router.get(
    "/me/subjects/{subject_id}/self-evaluation",
    response_model=SelfEvaluationOut,
    summary="Get latest self-evaluation",
)
async def get_self_evaluation(
    subject_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> SelfEvaluationOut:
    return await service.get_latest_self_evaluation(session, scope, subject_id)
