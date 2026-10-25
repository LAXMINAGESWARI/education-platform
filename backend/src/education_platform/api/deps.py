"""Authentication dependencies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.db.session import get_session, get_session_factory
from education_platform.modules.audit.models import AuditEvent
from education_platform.modules.audit.service import AuditAction, record_event
from education_platform.modules.auth.models import (
    StudentProfile,
    User,
    UserRole,
    UserStatus,
)
from education_platform.modules.auth.security import decode_token
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.authorization.scope import Scope, scope_for

_bearer = HTTPBearer(auto_error=False)


async def principal_from_bearer(
    session: AsyncSession,
    credentials: HTTPAuthorizationCredentials | None,
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token"
        ) from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    user = await session.get(User, UUID(str(user_id)))
    if user is None or user.status not in {UserStatus.ACTIVE, UserStatus.PROVISIONED}:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    roles = (await session.scalars(select(UserRole.role).where(UserRole.user_id == user.id))).all()
    profile_id = await session.scalar(
        select(StudentProfile.id).where(StudentProfile.user_id == user.id)
    )
    return Principal(
        user_id=user.id,
        institution_id=user.institution_id,
        email=user.email,
        roles=frozenset(role.value for role in roles),
        student_profile_id=profile_id,
        status=user.status.value,
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    return await principal_from_bearer(session, credentials)


async def get_current_user_detached(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """Authenticate without holding the request session for StreamingResponse."""
    factory = get_session_factory()
    async with factory() as session:
        principal = await principal_from_bearer(session, credentials)
        await session.commit()
        return principal


async def require_administrator(
    principal: Principal = Depends(get_current_user),
) -> Principal:
    if not principal.is_administrator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required",
        )
    return principal


async def require_administrator_detached(
    principal: Principal = Depends(get_current_user_detached),
) -> Principal:
    """Administrator check that does not hold the request session open."""
    if not principal.is_administrator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required",
        )
    return principal


def require_role(*roles: str) -> Callable[..., Awaitable[Principal]]:
    """Require at least one of the given roles."""
    allowed = frozenset(roles)

    async def _dependency(
        principal: Principal = Depends(get_current_user),
    ) -> Principal:
        if not (principal.roles & allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(sorted(allowed))}",
            )
        return principal

    return _dependency


async def get_scope(
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Scope:
    return await scope_for(session, principal)


@dataclass(frozen=True, slots=True)
class ScopedRequest:
    principal: Principal
    scope: Scope
    session: AsyncSession
    audit_event: AuditEvent

    async def record_rows(self, rows_returned: int, detail: str | None = None) -> None:
        """Fill in row count for the audit entry opened by `scoped()`."""
        payload = {**self.audit_event.payload, "rows_returned": rows_returned}
        if detail:
            payload["detail"] = detail
        # Reassign rather than mutate: SQLAlchemy does not track in-place JSON edits.
        self.audit_event.payload = payload
        await self.session.flush()


def scoped(resource: str) -> Callable[..., Awaitable[ScopedRequest]]:
    """Resolve scope and open a scoped-read audit entry for `resource`."""

    async def _dependency(
        principal: Principal = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> ScopedRequest:
        scope = await scope_for(session, principal)
        event = await record_event(
            session,
            institution_id=principal.institution_id,
            actor_user_id=principal.user_id,
            event_type=AuditAction.SCOPED_READ,
            entity_type=resource,
            payload={
                "resource": resource,
                "unrestricted": scope.unrestricted,
                "scoped_students": len(scope.student_ids),
            },
        )
        return ScopedRequest(principal=principal, scope=scope, session=session, audit_event=event)

    return _dependency


__all__ = [
    "Principal",
    "ScopedRequest",
    "get_current_user",
    "get_current_user_detached",
    "get_scope",
    "principal_from_bearer",
    "require_administrator",
    "require_role",
    "scoped",
]
