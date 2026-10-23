from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from education_platform.api.deps import Principal, get_current_user_detached
from education_platform.db.session import get_session_factory
from education_platform.modules.audit.service import (
    SUBSCRIBE_AUDIT_WINDOW,
    AuditAction,
    record_event_within_window,
)
from education_platform.modules.authorization.scope import scope_for
from education_platform.modules.progress.sse import SSE_HEADERS, iter_sse
from education_platform.modules.progress.subscribe import subscribe
from education_platform.modules.progress.types import (
    ProgressFrame,
    ProgressSubject,
    event_id_from_header,
    run_subject,
    version_subject,
)

router = APIRouter(tags=["progress"])

# One scoped-read row per principal, subject, and 10-minute bucket.


async def _chain(
    first: ProgressFrame, rest: AsyncIterator[ProgressFrame]
) -> AsyncIterator[ProgressFrame]:
    yield first
    async for frame in rest:
        yield frame


async def _open_stream(
    *,
    principal: Principal,
    subject: ProgressSubject,
    resource: str,
    last_event_id: str | None,
) -> StreamingResponse:
    factory = get_session_factory()
    async with factory() as session:
        scope = await scope_for(session, principal)
        await record_event_within_window(
            session,
            window=SUBSCRIBE_AUDIT_WINDOW,
            institution_id=principal.institution_id,
            actor_user_id=principal.user_id,
            event_type=AuditAction.SCOPED_READ,
            entity_type=resource,
            match_payload={"detail": subject.key()},
            payload={
                "resource": resource,
                "unrestricted": scope.unrestricted,
                "scoped_students": len(scope.student_ids),
                "detail": subject.key(),
                "rows_returned": 1,
            },
        )
        frames = subscribe(
            principal,
            scope,
            subject,
            last_event_id=event_id_from_header(last_event_id),
            peek_session=session,
        )
        first = await anext(frames)
        await session.commit()
    return StreamingResponse(
        iter_sse(_chain(first, frames)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/progress/runs/{run_id}")
async def run_events(
    run_id: UUID,
    request: Request,
    principal: Principal = Depends(get_current_user_detached),
) -> StreamingResponse:
    if not (principal.roles & {"teacher", "administrator"}):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires one of: administrator, teacher",
        )
    return await _open_stream(
        principal=principal,
        subject=run_subject(run_id),
        resource="progress.run",
        last_event_id=request.headers.get("last-event-id"),
    )


@router.get("/progress/versions/{version_id}")
async def version_events(
    version_id: UUID,
    request: Request,
    principal: Principal = Depends(get_current_user_detached),
) -> StreamingResponse:
    if not principal.is_administrator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required",
        )
    return await _open_stream(
        principal=principal,
        subject=version_subject(version_id),
        resource="progress.version",
        last_event_id=request.headers.get("last-event-id"),
    )
