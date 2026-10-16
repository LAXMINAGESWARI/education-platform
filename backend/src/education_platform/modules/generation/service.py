"""Submit, get, patch, accept, discard, retry, reject-items, and publish generation runs."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.academics.models import (
    AcademicPeriod,
    AcademicPeriodStatus,
    Grade,
    GradeSubjectOffering,
    LearningOutcome,
    PeriodGrade,
    Subject,
    Subtopic,
    Topic,
)
from education_platform.modules.assessments.models import (
    AttemptAnswer,
    CommonMasteryQuiz,
    Question,
    QuestionAnswerKey,
    QuestionOption,
    QuestionOutcomeTag,
    QuestionVersion,
    QuestionVersionStatus,
    QuizAttempt,
    QuizItem,
    QuizMaterialBinding,
    QuizRelease,
    QuizReleaseStatus,
    QuizVersion,
    QuizVersionStatus,
)
from education_platform.modules.audit.service import AuditAction, record_event
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.generation.items import (
    bloom_from_rubric,
    misconceptions_from_rubric,
)
from education_platform.modules.generation.models import (
    ContentGenerationOutlineNode,
    ContentGenerationRun,
    CurriculumGenerationJob,
    GenerationChangeRequest,
    GenerationJob,
    GenerationRevision,
    ReviewRoundClosure,
    ReviewRoundRow,
)
from education_platform.modules.generation.outline import largest_remainder
from education_platform.modules.generation.types import (
    IN_FLIGHT_PHASES,
    PATCH_OUTLINE_GONE,
    AcceptedRun,
    GenerationError,
    GenerationJobKind,
    GenerationJobStatus,
    GenerationRun,
    ItemCount,
    OutlineDocument,
    OutlineNode,
    OutlinePatch,
    ProposedOutcome,
    PublishedTopic,
    QaItem,
    RunPhase,
)
from education_platform.modules.materials.models import (
    SourceChunk,
    SourceMaterial,
    SourceMaterialStatus,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
    StudentMaterialProgress,
)
from education_platform.modules.progress.types import run_subject
from education_platform.modules.progress.wake import publish_wake_async
from education_platform.modules.rag import storage
from education_platform.modules.rag.models import ChunkEmbedding, IngestJob
from education_platform.modules.rag.queue import queue_topic_intake_pdf, validate_pdf_upload
from education_platform.modules.rag.storage import delete_blob

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_IN_FLIGHT_UNIQUE = "uq_content_generation_runs_one_in_flight_topic"
_POST_ACCEPT = frozenset({RunPhase.GENERATING, RunPhase.QA_REVIEW, RunPhase.PUBLISHED})


def _in_flight_conflict(exc: IntegrityError) -> bool:
    return _IN_FLIGHT_UNIQUE in str(exc.orig or exc)


def _require_closer(principal: Principal) -> None:
    if not principal.is_administrator:
        raise GenerationError("Only an administrator can complete this step.", status_code=403)


async def _authorised_topic(session: AsyncSession, scope: Scope, topic_id: UUID) -> Topic:
    row = (
        await session.execute(
            select(Topic, Topic.grade_subject_offering_id, Subject.institution_id)
            .join(
                GradeSubjectOffering,
                GradeSubjectOffering.id == Topic.grade_subject_offering_id,
            )
            .join(Subject, Subject.id == GradeSubjectOffering.subject_id)
            .where(Topic.id == topic_id)
        )
    ).first()
    if row is None:
        raise GenerationError("That topic does not exist.", status_code=404)
    topic, offering_id, subject_institution_id = row
    if subject_institution_id != scope.institution_id:
        raise GenerationError("That topic does not exist.", status_code=404)
    if not (scope.unrestricted or offering_id in scope.taught_offering_ids):
        raise GenerationError(
            "You do not teach this subject, so you cannot work on this topic.",
            status_code=403,
        )
    return cast(Topic, topic)


async def _in_flight_run_id(session: AsyncSession, topic_id: UUID) -> UUID | None:
    run_id = await session.scalar(
        select(ContentGenerationRun.id).where(
            ContentGenerationRun.topic_id == topic_id,
            ContentGenerationRun.phase.in_(tuple(IN_FLIGHT_PHASES)),
        )
    )
    if run_id is None:
        return None
    if not isinstance(run_id, UUID):
        raise TypeError("in-flight run id must be a UUID")
    return run_id


def _outcomes_from_json(raw: object) -> tuple[ProposedOutcome, ...]:
    if not isinstance(raw, list):
        return ()
    statements: list[ProposedOutcome] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            statements.append(ProposedOutcome(statement=item.strip()))
    return tuple(statements[:3])


def _node_from_row(row: ContentGenerationOutlineNode, quota: int | None) -> OutlineNode:
    return OutlineNode(
        id=row.id,
        parent_id=row.parent_id,
        slug=row.slug,
        title=row.title,
        token_mass=row.token_mass,
        prerequisite_score=Decimal(row.prerequisite_score),
        centrality=Decimal(row.centrality),
        weight=Decimal(row.weight),
        quota=quota,
        matched_subtopic_id=row.matched_subtopic_id,
        force_create=row.force_create,
        accepted_subtopic_id=row.accepted_subtopic_id,
        proposed_outcomes=_outcomes_from_json(row.proposed_outcomes),
        sequence=row.sequence,
    )


def _document_from_rows(
    target_item_count: ItemCount,
    rows: Sequence[ContentGenerationOutlineNode],
) -> OutlineDocument | None:
    if not rows:
        return None
    ordered = sorted(rows, key=lambda row: row.sequence)
    stored = tuple(_node_from_row(row, row.quota) for row in ordered)
    document = OutlineDocument(target_item_count=target_item_count, nodes=stored)
    if not any(row.quota is None for row in ordered):
        return document
    try:
        previews = largest_remainder(document.normalized_weights(), target_item_count.value)
    except ValueError:
        return document
    previewed = tuple(
        _node_from_row(row, row.quota if row.quota is not None else previews[index])
        for index, row in enumerate(ordered)
    )
    return OutlineDocument(target_item_count=target_item_count, nodes=previewed)


def _to_domain(
    row: ContentGenerationRun,
    nodes: Sequence[ContentGenerationOutlineNode],
) -> GenerationRun:
    count = ItemCount(row.target_item_count)
    outline = _document_from_rows(count, nodes)
    if row.phase is RunPhase.OUTLINING and outline is None:
        outline = OutlineDocument(target_item_count=count, nodes=())
    return GenerationRun(
        id=row.id,
        topic_id=row.topic_id,
        title=row.title,
        phase=row.phase,
        target_item_count=count,
        submitted_by_user_id=row.submitted_by_user_id,
        intake_version_id=row.intake_source_material_version_id,
        failure_reason=row.failure_reason,
        outline=outline,
        draft_lesson_markdown=row.draft_lesson_markdown,
        published_lesson_version_id=row.published_lesson_version_id,
        published_quiz_version_id=row.published_quiz_version_id,
        created_at=row.created_at,
    )


async def _load_nodes(session: AsyncSession, run_id: UUID) -> list[ContentGenerationOutlineNode]:
    return list(
        (
            await session.scalars(
                select(ContentGenerationOutlineNode)
                .where(ContentGenerationOutlineNode.run_id == run_id)
                .order_by(ContentGenerationOutlineNode.sequence)
            )
        ).all()
    )


async def _load_run_row(session: AsyncSession, run_id: UUID) -> ContentGenerationRun:
    row = await session.get(ContentGenerationRun, run_id)
    if row is None:
        raise GenerationError("That generation run does not exist.", status_code=404)
    return row


async def in_flight_jobs(
    session: AsyncSession, run_ids: Sequence[UUID]
) -> dict[UUID, list[tuple[str, str]]]:
    if not run_ids:
        return {}
    rows = (
        await session.scalars(
            select(GenerationJob)
            .where(
                GenerationJob.run_id.in_(tuple(run_ids)),
                GenerationJob.status.in_((GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)),
            )
            .order_by(GenerationJob.created_at, GenerationJob.kind)
        )
    ).all()
    grouped: dict[UUID, list[tuple[str, str]]] = {run_id: [] for run_id in run_ids}
    for row in rows:
        grouped.setdefault(row.run_id, []).append((row.kind.value, row.status.value))
    return grouped


async def jobs_for_runs(
    session: AsyncSession, runs: Sequence[GenerationRun]
) -> dict[UUID, list[tuple[str, str]]]:
    """In-flight jobs, plus the latest failed job once a run has failed.

    Queued and running jobs drive the live stepper. A failed lesson or item job
    is no longer in flight, so without it the page falls back to outline review.
    """
    grouped = await in_flight_jobs(session, [run.id for run in runs])
    failed_ids = [run.id for run in runs if run.phase is RunPhase.FAILED]
    if not failed_ids:
        return grouped
    rows = (
        await session.scalars(
            select(GenerationJob)
            .where(
                GenerationJob.run_id.in_(tuple(failed_ids)),
                GenerationJob.status == GenerationJobStatus.FAILED,
            )
            .order_by(GenerationJob.created_at.desc())
        )
    ).all()
    seen: set[UUID] = set()
    for row in rows:
        if row.run_id in seen:
            continue
        seen.add(row.run_id)
        marker = (row.kind.value, row.status.value)
        existing = grouped.setdefault(row.run_id, [])
        if marker not in existing:
            existing.append(marker)
    return grouped


async def submit_pdf(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    *,
    topic_id: UUID,
    title: str,
    file: UploadFile,
    target_item_count: int | None = None,
) -> AcceptedRun:
    topic = await _authorised_topic(session, scope, topic_id)
    try:
        count = ItemCount.parse(target_item_count)
    except ValueError as exc:
        raise GenerationError(str(exc), status_code=400) from exc
    if await _in_flight_run_id(session, topic.id) is not None:
        raise GenerationError(
            "A generation run is already in flight for this topic.",
            status_code=409,
        )

    data = await file.read()
    content_type = validate_pdf_upload(file, data)
    object_key = storage.build_object_key(
        institution_id=principal.institution_id,
        kind="source_materials",
        filename=file.filename or "material.pdf",
    )
    storage.store_bytes(object_key, data)

    try:
        async with session.begin_nested():
            run = ContentGenerationRun(
                topic_id=topic.id,
                submitted_by_user_id=principal.user_id,
                title=title.strip() or "Topic source",
                phase=RunPhase.INDEXING,
                target_item_count=count.value,
            )
            session.add(run)
            await session.flush()
            queued = await queue_topic_intake_pdf(
                session,
                topic_id=topic.id,
                title=run.title,
                data=data,
                content_type=content_type,
                object_key=object_key,
                submitted_by_user_id=principal.user_id,
            )
            run.intake_source_material_version_id = queued.version.id
    except IntegrityError as exc:
        if _in_flight_conflict(exc):
            raise GenerationError(
                "A generation run is already in flight for this topic.",
                status_code=409,
            ) from exc
        raise

    await record_event(
        session,
        institution_id=principal.institution_id,
        actor_user_id=principal.user_id,
        event_type=AuditAction.DOCUMENT_UPLOADED,
        entity_type="content_generation_run",
        entity_id=run.id,
        payload={"topic_id": str(topic.id)},
    )
    return AcceptedRun(run_id=run.id, topic_id=topic.id, phase=RunPhase.INDEXING)


async def _authorised_offering_for_subject(
    session: AsyncSession,
    scope: Scope,
    subject_id: UUID,
    *,
    grade_name: str | None = None,
) -> GradeSubjectOffering:
    """Pick the offering the upload is for.

    A subject code is unique per institution and is offered once per grade. The materials
    browser is already on one grade, so the caller names that grade. Without a grade, a
    single offering still resolves; several grades stay a 409 rather than guessing.
    """
    requested = grade_name.strip().casefold() if grade_name and grade_name.strip() else None
    rows = (
        await session.execute(
            select(
                GradeSubjectOffering,
                Subject.institution_id,
                Grade.name,
                AcademicPeriod.status,
            )
            .join(Subject, Subject.id == GradeSubjectOffering.subject_id)
            .join(PeriodGrade, PeriodGrade.id == GradeSubjectOffering.period_grade_id)
            .join(Grade, Grade.id == PeriodGrade.grade_id)
            .join(AcademicPeriod, AcademicPeriod.id == PeriodGrade.academic_period_id)
            .where(GradeSubjectOffering.subject_id == subject_id)
        )
    ).all()
    matching = [
        (offering, status)
        for offering, institution_id, grade, status in rows
        if institution_id == scope.institution_id
        and (requested is None or grade.casefold() == requested)
    ]
    if not matching:
        if requested is None:
            raise GenerationError("That subject does not exist.", status_code=404)
        raise GenerationError("That subject is not offered in this grade.", status_code=404)
    if not scope.unrestricted:
        matching = [
            (offering, status)
            for offering, status in matching
            if offering.id in scope.taught_offering_ids
        ]
        if not matching:
            raise GenerationError(
                "You do not teach this subject, so you cannot start a generation run.",
                status_code=403,
            )
    if len(matching) > 1:
        active = [
            (offering, status)
            for offering, status in matching
            if status is AcademicPeriodStatus.ACTIVE
        ]
        if len(active) == 1:
            matching = active
    if len(matching) > 1:
        raise GenerationError(
            "This subject is offered on more than one grade.",
            status_code=409,
        )
    return cast(GradeSubjectOffering, matching[0][0])


async def _create_next_topic(session: AsyncSession, offering_id: UUID, title: str) -> Topic:
    name = (title.strip() or "Untitled topic")[:200]
    used = set(
        (
            await session.scalars(
                select(Topic.slug).where(Topic.grade_subject_offering_id == offering_id)
            )
        ).all()
    )
    max_sequence = await session.scalar(
        select(func.max(Topic.sequence)).where(Topic.grade_subject_offering_id == offering_id)
    )
    topic = Topic(
        grade_subject_offering_id=offering_id,
        name=name,
        slug=_unique_slug(_slugify(name, fallback="topic"), used),
        sequence=(max_sequence or 0) + 1,
    )
    session.add(topic)
    await session.flush()
    return topic


async def submit_pdf_for_subject(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    *,
    subject_id: UUID,
    title: str,
    file: UploadFile,
    target_item_count: int | None = None,
    grade_name: str | None = None,
) -> AcceptedRun:
    offering = await _authorised_offering_for_subject(
        session, scope, subject_id, grade_name=grade_name
    )
    topic = await _create_next_topic(session, offering.id, title)
    return await submit_pdf(
        session,
        scope,
        principal,
        topic_id=topic.id,
        title=title,
        file=file,
        target_item_count=target_item_count,
    )


async def submit_pdf_for_subtopic_alias(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    *,
    subtopic_id: UUID,
    title: str,
    file: UploadFile,
) -> AcceptedRun:
    row = (
        await session.execute(
            select(Subtopic, Topic.id)
            .join(Topic, Topic.id == Subtopic.topic_id)
            .where(Subtopic.id == subtopic_id)
        )
    ).first()
    if row is None:
        raise GenerationError("That subtopic does not exist.", status_code=404)
    _subtopic, topic_id = row
    return await submit_pdf(
        session,
        scope,
        principal,
        topic_id=topic_id,
        title=title,
        file=file,
    )


async def get_run(
    session: AsyncSession, scope: Scope, run_id: UUID, *, heal: bool = True
) -> GenerationRun:
    row = await _load_run_row(session, run_id)
    await _authorised_topic(session, scope, row.topic_id)
    if heal:
        await _ensure_generating_jobs(session, row)
    nodes = await _load_nodes(session, row.id)
    return _to_domain(row, nodes)


async def list_runs(session: AsyncSession, scope: Scope, topic_id: UUID) -> list[GenerationRun]:
    await _authorised_topic(session, scope, topic_id)
    in_flight = ContentGenerationRun.phase.in_(tuple(IN_FLIGHT_PHASES))
    rows = (
        await session.scalars(
            select(ContentGenerationRun)
            .where(ContentGenerationRun.topic_id == topic_id)
            .order_by(case((in_flight, 0), else_=1), ContentGenerationRun.created_at.desc())
        )
    ).all()
    out: list[GenerationRun] = []
    for row in rows:
        await _ensure_generating_jobs(session, row)
        nodes = await _load_nodes(session, row.id)
        out.append(_to_domain(row, nodes))
    return out


async def patch_outline(
    session: AsyncSession,
    scope: Scope,
    run_id: UUID,
    patch: OutlinePatch,
) -> GenerationRun:
    del session, scope, run_id, patch
    raise GenerationError(PATCH_OUTLINE_GONE, status_code=410)


async def accept_outline(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    run_id: UUID,
) -> GenerationRun:
    _require_closer(principal)
    run = await get_run(session, scope, run_id)
    if run.phase in _POST_ACCEPT:
        return run
    if run.phase is not RunPhase.OUTLINE_REVIEW:
        raise GenerationError(
            "Only an outline waiting for review can be accepted.",
            status_code=409,
        )
    if not run.outline or not run.outline.nodes:
        raise GenerationError("Cannot accept an empty outline.", status_code=409)

    quotas = largest_remainder(run.outline.normalized_weights(), run.target_item_count.value)
    existing = list(
        (
            await session.scalars(
                select(Subtopic)
                .where(Subtopic.topic_id == run.topic_id)
                .order_by(Subtopic.sequence)
            )
        ).all()
    )
    used_slugs = {subtopic.slug for subtopic in existing}
    max_sequence = max((subtopic.sequence for subtopic in existing), default=0)
    node_rows = await _load_nodes(session, run.id)
    by_id = {node.id: node for node in node_rows}

    for index, domain_node in enumerate(run.outline.nodes):
        row = by_id[domain_node.id]
        if domain_node.force_create or domain_node.matched_subtopic_id is None:
            slug = _unique_slug(_slugify(domain_node.slug or domain_node.title), used_slugs)
            max_sequence += 1
            subtopic = Subtopic(
                topic_id=run.topic_id,
                name=domain_node.title,
                slug=slug,
                sequence=max_sequence,
            )
            session.add(subtopic)
            await session.flush()
            used_slugs.add(slug)
        else:
            matched = await session.get(Subtopic, domain_node.matched_subtopic_id)
            if matched is None or matched.topic_id != run.topic_id:
                raise GenerationError(
                    "Matched subtopic does not belong to this topic.",
                    status_code=400,
                )
            subtopic = matched
        await _insert_outcome_stubs(session, subtopic.id, domain_node.proposed_outcomes)
        row.quota = quotas[index]
        row.accepted_subtopic_id = subtopic.id

    stored = await _load_run_row(session, run_id)
    stored.phase = RunPhase.GENERATING
    stored.failure_reason = None
    await _enqueue_content_jobs(session, stored.id)
    await session.flush()
    await _notify_run(session, stored.id)
    return await get_run(session, scope, run_id)


async def discard_outline(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    run_id: UUID,
) -> GenerationRun:
    _require_closer(principal)
    row = await _load_run_row(session, run_id)
    await _authorised_topic(session, scope, row.topic_id)
    if row.phase is RunPhase.DISCARDED:
        nodes = await _load_nodes(session, row.id)
        return _to_domain(row, nodes)
    if row.phase is RunPhase.PUBLISHED:
        raise GenerationError("A published run cannot be discarded.", status_code=409)
    if row.phase not in IN_FLIGHT_PHASES and row.phase is not RunPhase.FAILED:
        raise GenerationError("This run cannot be discarded.", status_code=409)
    await _cancel_open_jobs(session, row.id)
    row.phase = RunPhase.DISCARDED
    row.failure_reason = None
    await session.flush()
    await _notify_run(session, row.id)
    return await get_run(session, scope, run_id)


async def retry_failed(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    run_id: UUID,
) -> GenerationRun:
    _require_closer(principal)
    row = await _load_run_row(session, run_id)
    await _authorised_topic(session, scope, row.topic_id)
    if row.phase not in {RunPhase.FAILED, RunPhase.DISCARDED}:
        raise GenerationError("Only a failed or discarded run can be retried.", status_code=409)
    if row.intake_source_material_version_id is None:
        raise GenerationError("Intake indexing failed. Submit a new PDF.", status_code=409)
    intake = await session.get(SourceMaterialVersion, row.intake_source_material_version_id)
    if intake is None or intake.lifecycle_status is not SourceMaterialVersionStatus.READY:
        raise GenerationError("Intake indexing failed. Submit a new PDF.", status_code=409)
    kind = await _latest_failed_kind(session, row.id)
    if kind is GenerationJobKind.REGENERATE_ITEMS:
        row.phase = RunPhase.GENERATING
        row.failure_reason = None
        await _enqueue_generation_job(
            session, row.id, GenerationJobKind.REGENERATE_ITEMS, repeatable=True
        )
    elif kind is GenerationJobKind.ITEMS or kind is GenerationJobKind.LESSON:
        row.phase = RunPhase.GENERATING
        row.failure_reason = None
        await _enqueue_content_jobs(session, row.id)
    else:
        row.phase = RunPhase.OUTLINING
        row.failure_reason = None
        session.add(
            GenerationJob(
                run_id=row.id,
                kind=GenerationJobKind.OUTLINE,
                status=GenerationJobStatus.QUEUED,
            )
        )
    await session.flush()
    await _notify_run(session, row.id)
    return await get_run(session, scope, run_id)


def _topic_or_subtopics(
    topic_column: Any,
    subtopic_column: Any,
    topic_id: UUID,
    subtopic_ids: list[UUID],
) -> Any:
    clause = topic_column == topic_id
    if subtopic_ids:
        clause = clause | subtopic_column.in_(subtopic_ids)
    return clause


async def delete_unpublished_topic(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    topic_id: UUID,
) -> None:
    """Remove a generation attempt that was never published to students."""
    _require_closer(principal)
    topic = await _authorised_topic(session, scope, topic_id)
    subtopic_ids = list(
        await session.scalars(select(Subtopic.id).where(Subtopic.topic_id == topic.id))
    )
    if await _topic_is_live(session, topic.id, subtopic_ids):
        raise GenerationError(
            "This unit is published. Remove is only for generation attempts.",
            status_code=409,
        )
    blob_keys = await _delete_topic_rows(session, topic.id, subtopic_ids)
    await session.delete(topic)
    await session.flush()
    for key in blob_keys:
        delete_blob(key)


async def _topic_is_live(session: AsyncSession, topic_id: UUID, subtopic_ids: list[UUID]) -> bool:
    published_run = await session.scalar(
        select(ContentGenerationRun.id).where(
            ContentGenerationRun.topic_id == topic_id,
            ContentGenerationRun.phase == RunPhase.PUBLISHED,
        )
    )
    if published_run is not None:
        return True
    material_ids = list(
        await session.scalars(
            select(SourceMaterial.id).where(
                _topic_or_subtopics(
                    SourceMaterial.topic_id,
                    SourceMaterial.subtopic_id,
                    topic_id,
                    subtopic_ids,
                )
            )
        )
    )
    if material_ids:
        published_material = await session.scalar(
            select(SourceMaterialVersion.id).where(
                SourceMaterialVersion.source_material_id.in_(material_ids),
                SourceMaterialVersion.lifecycle_status == SourceMaterialVersionStatus.PUBLISHED,
            )
        )
        if published_material is not None:
            return True
        progressed = await session.scalar(
            select(StudentMaterialProgress.id).where(
                StudentMaterialProgress.source_material_version_id.in_(
                    select(SourceMaterialVersion.id).where(
                        SourceMaterialVersion.source_material_id.in_(material_ids)
                    )
                )
            )
        )
        if progressed is not None:
            return True
    quiz_ids = list(
        await session.scalars(
            select(CommonMasteryQuiz.id).where(
                _topic_or_subtopics(
                    CommonMasteryQuiz.topic_id,
                    CommonMasteryQuiz.subtopic_id,
                    topic_id,
                    subtopic_ids,
                )
            )
        )
    )
    if not quiz_ids:
        return False
    version_ids = select(QuizVersion.id).where(QuizVersion.quiz_id.in_(quiz_ids))
    released = await session.scalar(
        select(QuizVersion.id).where(
            QuizVersion.quiz_id.in_(quiz_ids),
            QuizVersion.lifecycle_status == QuizVersionStatus.RELEASED,
        )
    )
    if released is not None:
        return True
    attempted = await session.scalar(
        select(QuizAttempt.id).where(QuizAttempt.quiz_version_id.in_(version_ids))
    )
    return attempted is not None


async def _delete_topic_rows(
    session: AsyncSession, topic_id: UUID, subtopic_ids: list[UUID]
) -> list[str]:
    run_ids = list(
        await session.scalars(
            select(ContentGenerationRun.id).where(ContentGenerationRun.topic_id == topic_id)
        )
    )
    revision_ids = (
        list(
            await session.scalars(
                select(GenerationRevision.id).where(GenerationRevision.run_id.in_(run_ids))
            )
        )
        if run_ids
        else []
    )
    round_ids = (
        list(
            await session.scalars(
                select(ReviewRoundRow.id).where(ReviewRoundRow.run_id.in_(run_ids))
            )
        )
        if run_ids
        else []
    )
    if run_ids:
        await session.execute(delete(GenerationJob).where(GenerationJob.run_id.in_(run_ids)))
    if revision_ids:
        await session.execute(
            delete(GenerationChangeRequest).where(
                GenerationChangeRequest.revision_id.in_(revision_ids)
            )
        )
    if round_ids:
        await session.execute(
            delete(ReviewRoundClosure).where(ReviewRoundClosure.round_id.in_(round_ids))
        )
        await session.execute(delete(ReviewRoundRow).where(ReviewRoundRow.id.in_(round_ids)))
    if revision_ids:
        await session.execute(
            update(GenerationRevision)
            .where(GenerationRevision.run_id.in_(run_ids))
            .values(parent_revision_id=None)
        )
        await session.execute(
            delete(GenerationRevision).where(GenerationRevision.run_id.in_(run_ids))
        )
    if run_ids:
        await session.execute(
            delete(ContentGenerationRun).where(ContentGenerationRun.id.in_(run_ids))
        )
    if subtopic_ids:
        await session.execute(
            delete(CurriculumGenerationJob).where(
                CurriculumGenerationJob.subtopic_id.in_(subtopic_ids)
            )
        )

    material_filter = _topic_or_subtopics(
        SourceMaterial.topic_id,
        SourceMaterial.subtopic_id,
        topic_id,
        subtopic_ids,
    )
    material_ids = list(await session.scalars(select(SourceMaterial.id).where(material_filter)))
    version_ids = (
        list(
            await session.scalars(
                select(SourceMaterialVersion.id).where(
                    SourceMaterialVersion.source_material_id.in_(material_ids)
                )
            )
        )
        if material_ids
        else []
    )
    blob_keys = (
        list(
            await session.scalars(
                select(SourceMaterialVersion.blob_object_key).where(
                    SourceMaterialVersion.id.in_(version_ids),
                    SourceMaterialVersion.blob_object_key.is_not(None),
                )
            )
        )
        if version_ids
        else []
    )
    chunk_ids = (
        list(
            await session.scalars(
                select(SourceChunk.id).where(
                    SourceChunk.source_material_version_id.in_(version_ids)
                )
            )
        )
        if version_ids
        else []
    )
    if chunk_ids:
        await session.execute(delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunk_ids)))
        await session.execute(delete(SourceChunk).where(SourceChunk.id.in_(chunk_ids)))
    if version_ids:
        await session.execute(
            delete(IngestJob).where(IngestJob.source_material_version_id.in_(version_ids))
        )
        await session.execute(
            delete(QuizMaterialBinding).where(
                QuizMaterialBinding.source_material_version_id.in_(version_ids)
            )
        )
        await session.execute(
            delete(SourceMaterialVersion).where(SourceMaterialVersion.id.in_(version_ids))
        )
    if material_ids:
        await session.execute(delete(SourceMaterial).where(SourceMaterial.id.in_(material_ids)))

    quiz_ids = list(
        await session.scalars(
            select(CommonMasteryQuiz.id).where(
                _topic_or_subtopics(
                    CommonMasteryQuiz.topic_id,
                    CommonMasteryQuiz.subtopic_id,
                    topic_id,
                    subtopic_ids,
                )
            )
        )
    )
    quiz_version_ids = (
        list(await session.scalars(select(QuizVersion.id).where(QuizVersion.quiz_id.in_(quiz_ids))))
        if quiz_ids
        else []
    )
    question_ids = (
        list(
            await session.scalars(select(Question.id).where(Question.subtopic_id.in_(subtopic_ids)))
        )
        if subtopic_ids
        else []
    )
    question_version_ids = (
        list(
            await session.scalars(
                select(QuestionVersion.id).where(QuestionVersion.question_id.in_(question_ids))
            )
        )
        if question_ids
        else []
    )
    if quiz_version_ids:
        await session.execute(
            delete(QuizItem).where(QuizItem.quiz_version_id.in_(quiz_version_ids))
        )
        await session.execute(
            delete(QuizRelease).where(QuizRelease.quiz_version_id.in_(quiz_version_ids))
        )
        await session.execute(
            delete(QuizMaterialBinding).where(
                QuizMaterialBinding.quiz_version_id.in_(quiz_version_ids)
            )
        )
    if question_version_ids:
        await session.execute(
            delete(AttemptAnswer).where(AttemptAnswer.question_version_id.in_(question_version_ids))
        )
        await session.execute(
            delete(QuestionAnswerKey).where(
                QuestionAnswerKey.question_version_id.in_(question_version_ids)
            )
        )
        await session.execute(
            delete(QuestionOption).where(
                QuestionOption.question_version_id.in_(question_version_ids)
            )
        )
        await session.execute(
            delete(QuestionOutcomeTag).where(
                QuestionOutcomeTag.question_version_id.in_(question_version_ids)
            )
        )
        await session.execute(
            delete(QuestionVersion).where(QuestionVersion.id.in_(question_version_ids))
        )
    if question_ids:
        await session.execute(delete(Question).where(Question.id.in_(question_ids)))
    if subtopic_ids:
        await session.execute(
            delete(LearningOutcome).where(LearningOutcome.subtopic_id.in_(subtopic_ids))
        )
    if quiz_version_ids:
        await session.execute(delete(QuizVersion).where(QuizVersion.id.in_(quiz_version_ids)))
    if quiz_ids:
        await session.execute(delete(CommonMasteryQuiz).where(CommonMasteryQuiz.id.in_(quiz_ids)))
    if subtopic_ids:
        await session.execute(delete(Subtopic).where(Subtopic.id.in_(subtopic_ids)))
    return [key for key in blob_keys if key]


async def qa_items_for_run(session: AsyncSession, run: GenerationRun) -> list[QaItem]:
    """Teacher QA table. Joins keys on purpose; student routes must not call this."""
    if run.phase not in {RunPhase.QA_REVIEW, RunPhase.PUBLISHED}:
        return []
    stored = await _load_run_row(session, run.id)
    quiz_version_id = stored.published_quiz_version_id or stored.draft_quiz_version_id
    if quiz_version_id is None:
        return []
    items = (
        await session.scalars(
            select(QuizItem)
            .where(QuizItem.quiz_version_id == quiz_version_id)
            .order_by(QuizItem.sequence)
        )
    ).all()
    out: list[QaItem] = []
    for item in items:
        version = await session.get(QuestionVersion, item.question_version_id)
        if version is None:
            continue
        question = await session.get(Question, version.question_id)
        if question is None:
            continue
        options = (
            await session.scalars(
                select(QuestionOption)
                .where(QuestionOption.question_version_id == version.id)
                .order_by(QuestionOption.sequence, QuestionOption.label)
            )
        ).all()
        key = await session.scalar(
            select(QuestionAnswerKey).where(QuestionAnswerKey.question_version_id == version.id)
        )
        rationales = key.distractor_rationales if key is not None else None
        rubric = key.scoring_rubric if key is not None else None
        raw_blind = rubric.get("blind_solve") if isinstance(rubric, dict) else None
        blind_solve = cast(dict[str, object], raw_blind) if isinstance(raw_blind, dict) else None
        out.append(
            QaItem(
                question_id=question.id,
                question_version_id=version.id,
                prompt=version.prompt,
                options=tuple((option.label, option.text) for option in options),
                correct_label=key.correct_option_label or "" if key is not None else "",
                correct_rationale=key.correct_rationale or "" if key is not None else "",
                distractor_rationales=dict(rationales) if isinstance(rationales, dict) else {},
                subtopic_id=question.subtopic_id,
                sequence=item.sequence,
                bloom=bloom_from_rubric(rubric),
                misconception_labels=misconceptions_from_rubric(rubric),
                blind_solve=blind_solve,
            )
        )
    return out


async def reject_items(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    run_id: UUID,
    *,
    question_ids: Sequence[UUID],
) -> GenerationRun:
    _require_closer(principal)
    row = await _load_run_row(session, run_id)
    await _authorised_topic(session, scope, row.topic_id)
    if row.phase is not RunPhase.QA_REVIEW:
        raise GenerationError("Only a run in QA review can reject items.", status_code=409)
    if not question_ids:
        raise GenerationError("Select at least one item to regenerate.", status_code=400)
    if row.draft_quiz_version_id is None:
        raise GenerationError("Cannot reject items before the draft bank exists.", status_code=409)
    wanted = {item for item in question_ids}
    versions = (
        await session.scalars(
            select(QuestionVersion)
            .join(QuizItem, QuizItem.question_version_id == QuestionVersion.id)
            .where(
                QuizItem.quiz_version_id == row.draft_quiz_version_id,
                QuestionVersion.question_id.in_(tuple(wanted)),
            )
        )
    ).all()
    found = {version.question_id for version in versions}
    missing = wanted - found
    if missing:
        raise GenerationError(
            "Those items do not belong to this run's draft bank.", status_code=400
        )
    for version in versions:
        if version.lifecycle_status is QuestionVersionStatus.DRAFT:
            version.lifecycle_status = QuestionVersionStatus.ARCHIVED
    row.phase = RunPhase.GENERATING
    row.failure_reason = None
    session.add(
        GenerationJob(
            run_id=row.id,
            kind=GenerationJobKind.REGENERATE_ITEMS,
            status=GenerationJobStatus.QUEUED,
            payload={"question_ids": [str(item) for item in question_ids]},
        )
    )
    await session.flush()
    await _notify_run(session, row.id)
    return await get_run(session, scope, run_id)


async def publish(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    run_id: UUID,
) -> PublishedTopic:
    _require_closer(principal)
    row = await _load_run_row(session, run_id)
    await _authorised_topic(session, scope, row.topic_id)
    if row.phase is RunPhase.PUBLISHED:
        return await _published_topic_from_row(session, row)
    if row.phase is not RunPhase.QA_REVIEW:
        raise GenerationError("Only a run in QA review can be published.", status_code=409)
    markdown = (row.draft_lesson_markdown or "").strip()
    if not markdown or row.draft_quiz_version_id is None:
        raise GenerationError(
            "Cannot publish until both the draft lesson and item bank exist.",
            status_code=409,
        )
    quiz_version = await session.get(QuizVersion, row.draft_quiz_version_id)
    if quiz_version is None or quiz_version.lifecycle_status is not QuizVersionStatus.DRAFT:
        raise GenerationError(
            "Cannot publish until both the draft lesson and item bank exist.",
            status_code=409,
        )
    item_count = await _quiz_item_count(session, quiz_version.id)
    if item_count < 1:
        raise GenerationError(
            "Cannot publish until both the draft lesson and item bank exist.",
            status_code=409,
        )

    lesson_version = await _publish_topic_lesson(session, row, markdown, principal.user_id)
    now = datetime.now(UTC)
    quiz_version.lifecycle_status = QuizVersionStatus.RELEASED
    quiz_version.released_at = quiz_version.released_at or now
    await _publish_draft_question_versions(session, quiz_version.id)
    await _ensure_open_quiz_release(session, quiz_version.id, principal.user_id)
    await _ensure_quiz_material_binding(session, quiz_version.id, lesson_version.id)

    row.published_lesson_version_id = lesson_version.id
    row.published_quiz_version_id = quiz_version.id
    row.phase = RunPhase.PUBLISHED
    row.failure_reason = None
    await session.flush()
    await record_event(
        session,
        institution_id=principal.institution_id,
        actor_user_id=principal.user_id,
        event_type=AuditAction.QUIZ_APPROVED,
        entity_type="content_generation_run",
        entity_id=row.id,
        payload={
            "topic_id": str(row.topic_id),
            "lesson_version_id": str(lesson_version.id),
            "quiz_version_id": str(quiz_version.id),
        },
    )
    await _notify_run(session, row.id)
    return PublishedTopic(
        run_id=row.id,
        topic_id=row.topic_id,
        lesson_version_id=lesson_version.id,
        quiz_version_id=quiz_version.id,
        item_count=item_count,
    )


async def _published_topic_from_row(
    session: AsyncSession, row: ContentGenerationRun
) -> PublishedTopic:
    if row.published_lesson_version_id is None or row.published_quiz_version_id is None:
        raise GenerationError(
            "Published runs require lesson and quiz version ids.", status_code=409
        )
    return PublishedTopic(
        run_id=row.id,
        topic_id=row.topic_id,
        lesson_version_id=row.published_lesson_version_id,
        quiz_version_id=row.published_quiz_version_id,
        item_count=await _quiz_item_count(session, row.published_quiz_version_id),
    )


async def _publish_topic_lesson(
    session: AsyncSession,
    row: ContentGenerationRun,
    markdown: str,
    submitted_by_user_id: UUID,
) -> SourceMaterialVersion:
    material = await session.scalar(
        select(SourceMaterial).where(
            SourceMaterial.topic_id == row.topic_id,
            SourceMaterial.slug == "lesson",
        )
    )
    if material is None:
        material = SourceMaterial(
            topic_id=row.topic_id,
            title=row.title.strip() or "Topic lesson",
            slug="lesson",
            status=SourceMaterialStatus.PUBLISHED,
        )
        session.add(material)
        await session.flush()
    else:
        material.status = SourceMaterialStatus.PUBLISHED
        material.title = row.title.strip() or material.title

    previous = await session.scalar(
        select(SourceMaterialVersion).where(
            SourceMaterialVersion.source_material_id == material.id,
            SourceMaterialVersion.lifecycle_status == SourceMaterialVersionStatus.PUBLISHED,
        )
    )
    if previous is not None:
        previous.lifecycle_status = SourceMaterialVersionStatus.SUPERSEDED
        await session.flush()

    next_number = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(SourceMaterialVersion.version_number), 0)).where(
                    SourceMaterialVersion.source_material_id == material.id
                )
            )
        )
        or 0
    )
    version = SourceMaterialVersion(
        source_material_id=material.id,
        version_number=next_number + 1,
        lifecycle_status=SourceMaterialVersionStatus.PUBLISHED,
        title=row.title.strip() or material.title,
        content_markdown=markdown,
        content_format="markdown",
        submitted_by_user_id=submitted_by_user_id,
        published_at=datetime.now(UTC),
    )
    session.add(version)
    await session.flush()
    return version


async def _publish_draft_question_versions(session: AsyncSession, quiz_version_id: UUID) -> None:
    versions = (
        await session.scalars(
            select(QuestionVersion)
            .join(QuizItem, QuizItem.question_version_id == QuestionVersion.id)
            .where(QuizItem.quiz_version_id == quiz_version_id)
        )
    ).all()
    for version in versions:
        if version.lifecycle_status is QuestionVersionStatus.DRAFT:
            version.lifecycle_status = QuestionVersionStatus.PUBLISHED


async def _ensure_open_quiz_release(
    session: AsyncSession, quiz_version_id: UUID, released_by_user_id: UUID
) -> None:
    existing = await session.scalar(
        select(QuizRelease.id).where(
            QuizRelease.quiz_version_id == quiz_version_id,
            QuizRelease.status == QuizReleaseStatus.OPEN,
        )
    )
    if existing is not None:
        return
    session.add(
        QuizRelease(
            quiz_version_id=quiz_version_id,
            status=QuizReleaseStatus.OPEN,
            released_by_user_id=released_by_user_id,
        )
    )


async def _ensure_quiz_material_binding(
    session: AsyncSession, quiz_version_id: UUID, lesson_version_id: UUID
) -> None:
    existing = await session.scalar(
        select(QuizMaterialBinding.id).where(
            QuizMaterialBinding.quiz_version_id == quiz_version_id,
            QuizMaterialBinding.source_material_version_id == lesson_version_id,
        )
    )
    if existing is not None:
        return
    session.add(
        QuizMaterialBinding(
            quiz_version_id=quiz_version_id,
            source_material_version_id=lesson_version_id,
        )
    )


async def _quiz_item_count(session: AsyncSession, quiz_version_id: UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(QuizItem)
        .where(QuizItem.quiz_version_id == quiz_version_id)
    )
    return int(count or 0)


async def _ensure_generating_jobs(session: AsyncSession, run: ContentGenerationRun) -> None:
    """Resume slice-2/3 work for leftover slice-1 parks (generating, jobs: [])."""
    if run.phase is not RunPhase.GENERATING:
        return
    if run.intake_source_material_version_id is None:
        run.phase = RunPhase.FAILED
        run.failure_reason = "Intake version is missing; cannot generate items."
        await session.flush()
        return
    await _enqueue_content_jobs(session, run.id)
    await session.flush()


async def _cancel_open_jobs(session: AsyncSession, run_id: UUID) -> None:
    jobs = (
        await session.scalars(
            select(GenerationJob).where(
                GenerationJob.run_id == run_id,
                GenerationJob.status.in_((GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)),
            )
        )
    ).all()
    for job in jobs:
        job.status = GenerationJobStatus.FAILED
        job.error = "Cancelled."


async def _notify_run(session: AsyncSession, run_id: UUID) -> None:
    await publish_wake_async(session, run_subject(run_id))


async def _enqueue_content_jobs(session: AsyncSession, run_id: UUID) -> None:
    """Enqueue the lesson first. Items wait until that lesson job has succeeded."""
    succeeded = await _succeeded_kinds(session, run_id)
    if GenerationJobKind.LESSON in succeeded:
        await _enqueue_generation_job(session, run_id, GenerationJobKind.ITEMS)
    else:
        await _enqueue_generation_job(session, run_id, GenerationJobKind.LESSON)


async def _enqueue_generation_job(
    session: AsyncSession,
    run_id: UUID,
    kind: GenerationJobKind,
    *,
    repeatable: bool = False,
) -> None:
    blocking = (
        (GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)
        if repeatable
        else (
            GenerationJobStatus.QUEUED,
            GenerationJobStatus.RUNNING,
            GenerationJobStatus.SUCCEEDED,
        )
    )
    existing = await session.scalar(
        select(GenerationJob.id).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == kind,
            GenerationJob.status.in_(blocking),
        )
    )
    if existing is not None:
        return
    try:
        async with session.begin_nested():
            session.add(
                GenerationJob(
                    run_id=run_id,
                    kind=kind,
                    status=GenerationJobStatus.QUEUED,
                )
            )
            await session.flush()
    except IntegrityError:
        return


async def _succeeded_kinds(session: AsyncSession, run_id: UUID) -> set[GenerationJobKind]:
    rows = (
        await session.scalars(
            select(GenerationJob.kind).where(
                GenerationJob.run_id == run_id,
                GenerationJob.status == GenerationJobStatus.SUCCEEDED,
            )
        )
    ).all()
    return set(rows)


async def _latest_failed_kind(session: AsyncSession, run_id: UUID) -> GenerationJobKind | None:
    job = (
        await session.scalars(
            select(GenerationJob)
            .where(
                GenerationJob.run_id == run_id,
                GenerationJob.status == GenerationJobStatus.FAILED,
            )
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
    ).first()
    return None if job is None else job.kind


async def _insert_outcome_stubs(
    session: AsyncSession,
    subtopic_id: UUID,
    proposed: Sequence[ProposedOutcome],
) -> None:
    existing = list(
        (
            await session.scalars(
                select(LearningOutcome).where(LearningOutcome.subtopic_id == subtopic_id)
            )
        ).all()
    )
    statements = {row.statement.casefold() for row in existing}
    codes = {row.code for row in existing}
    next_sequence = max((row.sequence for row in existing), default=0)
    next_lo = 1
    for outcome in proposed:
        statement = outcome.statement.strip()
        if not statement or statement.casefold() in statements:
            continue
        while f"LO{next_lo}" in codes:
            next_lo += 1
        code = f"LO{next_lo}"
        next_lo += 1
        next_sequence += 1
        session.add(
            LearningOutcome(
                subtopic_id=subtopic_id,
                code=code,
                statement=statement,
                sequence=next_sequence,
            )
        )
        statements.add(statement.casefold())
        codes.add(code)
    await session.flush()


def _slugify(value: str, *, fallback: str = "subtopic") -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return (slug[:100] or fallback)[:100]


def _unique_slug(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    index = 2
    while True:
        suffix = f"-{index}"
        candidate = f"{base[: 100 - len(suffix)]}{suffix}"
        if candidate not in used:
            return candidate
        index += 1
