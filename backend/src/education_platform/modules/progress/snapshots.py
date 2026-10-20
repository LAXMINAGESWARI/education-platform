from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.errors import DomainError
from education_platform.db.session import get_session_factory
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.generation import service as generation_service
from education_platform.modules.generation.schemas import GenerationRunOut
from education_platform.modules.materials.models import SourceMaterial, SourceMaterialVersion
from education_platform.modules.progress.stage_store import heal_expired_ingest_stage
from education_platform.modules.progress.types import (
    ProgressSnapshot,
    ProgressSubject,
    RunSubject,
    VersionSubject,
)
from education_platform.modules.rag import service as rag_service
from education_platform.modules.rag.schemas import (
    KnowledgeVersionStatusOut,
    MaterialVersionStatusOut,
)


class PostgresSnapshotReader:
    def __init__(self, peek_session: AsyncSession | None = None) -> None:
        self._peek_session = peek_session
        self._used_peek = False

    async def authorize_and_read(
        self,
        principal: Principal,
        scope: Scope,
        subject: ProgressSubject,
        *,
        heal: bool,
    ) -> ProgressSnapshot:
        if self._peek_session is not None and not self._used_peek:
            self._used_peek = True
            return await self._read(self._peek_session, principal, scope, subject, heal=heal)
        factory = get_session_factory()
        async with factory() as session:
            snapshot = await self._read(session, principal, scope, subject, heal=heal)
            await session.commit()
            return snapshot

    async def _read(
        self,
        session: AsyncSession,
        principal: Principal,
        scope: Scope,
        subject: ProgressSubject,
        *,
        heal: bool,
    ) -> ProgressSnapshot:
        if isinstance(subject, RunSubject):
            return await self._read_run(session, scope, subject.id, heal=heal)
        if isinstance(subject, VersionSubject):
            return await self._read_version(session, principal, subject.id)
        raise TypeError(f"Unsupported subject: {type(subject)!r}")

    async def _read_run(
        self,
        session: AsyncSession,
        scope: Scope,
        run_id: UUID,
        *,
        heal: bool,
    ) -> GenerationRunOut:
        run = await generation_service.get_run(session, scope, run_id, heal=heal)
        jobs = await generation_service.jobs_for_runs(session, [run])
        qa_items = await generation_service.qa_items_for_run(session, run)
        return GenerationRunOut.from_domain(run, jobs.get(run.id, ()), qa_items)

    async def _read_version(
        self,
        session: AsyncSession,
        principal: Principal,
        version_id: UUID,
    ) -> MaterialVersionStatusOut | KnowledgeVersionStatusOut:
        # Every read, not only the first. A lease can expire or be requeued
        # mid-stream, and the reset is a no-op while a worker still holds it.
        await heal_expired_ingest_stage(session, version_id)
        row = (
            await session.execute(
                select(SourceMaterialVersion, SourceMaterial)
                .join(SourceMaterial, SourceMaterial.id == SourceMaterialVersion.source_material_id)
                .where(SourceMaterialVersion.id == version_id)
            )
        ).first()
        if row is not None:
            _version, material = row
            if material.subtopic_id is None:
                raise DomainError("Version not found", status_code=404)
            return await rag_service.get_material_version_status(session, principal, version_id)
        return await rag_service.get_knowledge_version_status(session, principal, version_id)
