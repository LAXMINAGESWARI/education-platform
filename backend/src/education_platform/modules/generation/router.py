"""Thin HTTP adapters over generation.service."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.api.deps import (
    Principal,
    ScopedRequest,
    require_administrator,
    require_role,
    scoped,
)
from education_platform.db.session import get_session
from education_platform.modules.assessments.item_stats import TopicItemStats, item_stats_for_topic
from education_platform.modules.authorization.scope import scope_for
from education_platform.modules.generation import chat, review, service
from education_platform.modules.generation.schemas import (
    AcceptedRunOut,
    CloseRoundIn,
    CloseRoundResultOut,
    CurriculumGenerationJobOut,
    DraftChangeRequestOut,
    GenerationAssistantReplyOut,
    GenerationAssistantTurnIn,
    GenerationRunOut,
    OutlinePatchIn,
    PublishedTopicOut,
    RejectItemsIn,
    ReviewWorkspaceOut,
    TeacherDecisionIn,
    TeacherDecisionOut,
    close_round_command,
    teacher_decision_command,
)
from education_platform.modules.generation.types import (
    PATCH_OUTLINE_GONE,
    SUBTOPIC_GENERATION_GONE,
    TEACHER_UPLOAD_GONE,
)

router = APIRouter(tags=["generation"])


async def _run_out(request: ScopedRequest, run_id: UUID) -> GenerationRunOut:
    run = await service.get_run(request.session, request.scope, run_id)
    jobs = await service.jobs_for_runs(request.session, [run])
    qa_items = await service.qa_items_for_run(request.session, run)
    return GenerationRunOut.from_domain(run, jobs.get(run.id, ()), qa_items)


@router.post(
    "/admin/topics/{topic_id}/generation-runs",
    response_model=AcceptedRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit(
    topic_id: UUID,
    title: str = Form(...),
    file: UploadFile = File(...),
    target_item_count: int | None = Form(None),
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> AcceptedRunOut:
    scope = await scope_for(session, principal)
    accepted = await service.submit_pdf(
        session,
        scope,
        principal,
        topic_id=topic_id,
        title=title,
        file=file,
        target_item_count=target_item_count,
    )
    return AcceptedRunOut.from_domain(accepted)


@router.delete("/admin/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic(
    topic_id: UUID,
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> None:
    scope = await scope_for(session, principal)
    await service.delete_unpublished_topic(session, scope, principal, topic_id)


@router.get(
    "/admin/topics/{topic_id}/item-stats",
    response_model=TopicItemStats,
)
async def topic_item_stats(
    topic_id: UUID,
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> TopicItemStats:
    # Same institution/offering gate as sibling admin topic routes — without it an
    # administrator who knows another tenant's topic UUID receives answer keys.
    await service._authorised_topic(session, await scope_for(session, principal), topic_id)
    return await item_stats_for_topic(session, topic_id)


@router.post(
    "/admin/subjects/{subject_id}/generation-runs",
    response_model=AcceptedRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_for_subject(
    subject_id: UUID,
    title: str = Form(...),
    file: UploadFile = File(...),
    target_item_count: int | None = Form(None),
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> AcceptedRunOut:
    accepted = await service.submit_pdf_for_subject(
        session,
        await scope_for(session, principal),
        principal,
        subject_id=subject_id,
        title=title,
        file=file,
        target_item_count=target_item_count,
    )
    return AcceptedRunOut.from_domain(accepted)


@router.post(
    "/admin/subtopics/{subtopic_id}/generate-curriculum",
    response_model=CurriculumGenerationJobOut,
    status_code=status.HTTP_410_GONE,
)
async def generate_curriculum(
    subtopic_id: UUID,
    principal: Principal = Depends(require_administrator),
) -> CurriculumGenerationJobOut:
    del subtopic_id, principal
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=SUBTOPIC_GENERATION_GONE)


@router.get(
    "/admin/generation-jobs/{job_id}",
    response_model=CurriculumGenerationJobOut,
    status_code=status.HTTP_410_GONE,
)
async def show_curriculum_job(
    job_id: UUID,
    principal: Principal = Depends(require_administrator),
) -> CurriculumGenerationJobOut:
    del job_id, principal
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=SUBTOPIC_GENERATION_GONE)


@router.get(
    "/teaching/generation-runs/{run_id}",
    response_model=GenerationRunOut,
    dependencies=[Depends(require_role("teacher", "administrator"))],
)
async def show(
    run_id: UUID,
    request: ScopedRequest = Depends(scoped("teaching.generation.get")),
) -> GenerationRunOut:
    payload = await _run_out(request, run_id)
    await request.record_rows(1, detail=f"run={run_id}")
    return payload


@router.get(
    "/teaching/topics/{topic_id}/generation-runs",
    response_model=list[GenerationRunOut],
    dependencies=[Depends(require_role("teacher", "administrator"))],
)
async def list_for_topic(
    topic_id: UUID,
    request: ScopedRequest = Depends(scoped("teaching.generation.list")),
) -> list[GenerationRunOut]:
    rows = await service.list_runs(request.session, request.scope, topic_id)
    jobs = await service.jobs_for_runs(request.session, rows)
    await request.record_rows(len(rows))
    out: list[GenerationRunOut] = []
    for row in rows:
        qa_items = await service.qa_items_for_run(request.session, row)
        out.append(GenerationRunOut.from_domain(row, jobs.get(row.id, ()), qa_items))
    return out


@router.patch(
    "/teaching/generation-runs/{run_id}/outline",
    response_model=GenerationRunOut,
    dependencies=[Depends(require_role("teacher", "administrator"))],
)
async def patch(
    run_id: UUID,
    body: OutlinePatchIn,
    request: ScopedRequest = Depends(scoped("teaching.generation.patch")),
) -> GenerationRunOut:
    del run_id, body, request
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=PATCH_OUTLINE_GONE)


@router.get(
    "/teaching/generation-runs/{run_id}/review",
    response_model=ReviewWorkspaceOut,
    dependencies=[Depends(require_role("teacher", "administrator"))],
)
async def show_review(
    run_id: UUID,
    request: ScopedRequest = Depends(scoped("teaching.generation.review.get")),
) -> ReviewWorkspaceOut:
    workspace = await review.get_review_workspace(
        request.session, request.scope, request.principal, run_id
    )
    await request.record_rows(1, detail=f"review={run_id}")
    return ReviewWorkspaceOut.from_domain(workspace)


@router.post(
    "/teaching/generation-runs/{run_id}/review-rounds/{round_id}/decisions",
    response_model=TeacherDecisionOut,
    dependencies=[Depends(require_role("teacher", "administrator"))],
)
async def submit_decision(
    run_id: UUID,
    round_id: UUID,
    body: TeacherDecisionIn,
    request: ScopedRequest = Depends(scoped("teaching.generation.review.submit")),
) -> TeacherDecisionOut:
    decision = await review.submit_teacher_decision(
        request.session,
        request.scope,
        request.principal,
        run_id,
        round_id,
        teacher_decision_command(body),
    )
    await request.record_rows(1, detail=f"decision={decision.id}")
    return TeacherDecisionOut.from_domain(decision)


@router.post(
    "/teaching/generation-runs/{run_id}/review-rounds/{round_id}/close",
    response_model=CloseRoundResultOut,
)
async def close_round(
    run_id: UUID,
    round_id: UUID,
    body: CloseRoundIn,
    request: ScopedRequest = Depends(scoped("teaching.generation.review.close")),
    _admin: Principal = Depends(require_administrator),
) -> CloseRoundResultOut:
    result = await review.close_review_round(
        request.session,
        request.scope,
        request.principal,
        run_id,
        round_id,
        close_round_command(body),
    )
    await request.record_rows(1, detail=f"closed={round_id}")
    return CloseRoundResultOut.from_domain(result)


@router.post(
    "/teaching/generation-runs/{run_id}/assistant/turns",
    response_model=GenerationAssistantReplyOut,
    dependencies=[Depends(require_role("teacher", "administrator"))],
)
async def generation_assistant_turn(
    run_id: UUID,
    body: GenerationAssistantTurnIn,
    request: ScopedRequest = Depends(scoped("teaching.generation.assistant")),
) -> GenerationAssistantReplyOut:
    reply = await chat.post_review_chat(
        request.session,
        request.scope,
        request.principal,
        run_id,
        revision_id=body.revision_id,
        message=body.message,
        target=body.target_domain(),
    )
    await request.record_rows(1, detail=f"assistant={run_id}")
    draft = reply.draft_change_request
    return GenerationAssistantReplyOut(
        content=reply.content,
        citations=[
            {"id": item.id, "label": item.label, "excerpt": item.excerpt}
            for item in reply.citations
        ],
        draft_change_request=None if draft is None else DraftChangeRequestOut.from_domain(draft),
    )


@router.post(
    "/teaching/generation-runs/{run_id}/accept-outline",
    response_model=GenerationRunOut,
)
async def accept(
    run_id: UUID,
    request: ScopedRequest = Depends(scoped("teaching.generation.accept")),
    _admin: Principal = Depends(require_administrator),
) -> GenerationRunOut:
    await review.seal_open_outline_round_for_accept(request.session, run_id, request.principal)
    await service.accept_outline(request.session, request.scope, request.principal, run_id)
    payload = await _run_out(request, run_id)
    await request.record_rows(1, detail=f"accepted={run_id}")
    return payload


@router.post(
    "/teaching/generation-runs/{run_id}/discard",
    response_model=GenerationRunOut,
)
async def discard(
    run_id: UUID,
    request: ScopedRequest = Depends(scoped("teaching.generation.discard")),
    _admin: Principal = Depends(require_administrator),
) -> GenerationRunOut:
    await service.discard_outline(request.session, request.scope, request.principal, run_id)
    payload = await _run_out(request, run_id)
    await request.record_rows(1, detail=f"discarded={run_id}")
    return payload


@router.post(
    "/teaching/generation-runs/{run_id}/retry",
    response_model=GenerationRunOut,
)
async def retry(
    run_id: UUID,
    request: ScopedRequest = Depends(scoped("teaching.generation.retry")),
    _admin: Principal = Depends(require_administrator),
) -> GenerationRunOut:
    await service.retry_failed(request.session, request.scope, request.principal, run_id)
    payload = await _run_out(request, run_id)
    await request.record_rows(1, detail=f"retried={run_id}")
    return payload


@router.post(
    "/teaching/generation-runs/{run_id}/reject-items",
    response_model=GenerationRunOut,
)
async def reject(
    run_id: UUID,
    body: RejectItemsIn,
    request: ScopedRequest = Depends(scoped("teaching.generation.reject")),
    _admin: Principal = Depends(require_administrator),
) -> GenerationRunOut:
    await service.reject_items(
        request.session,
        request.scope,
        request.principal,
        run_id,
        question_ids=body.question_ids,
    )
    payload = await _run_out(request, run_id)
    await request.record_rows(1, detail=f"rejected={run_id}")
    return payload


@router.post(
    "/teaching/generation-runs/{run_id}/publish",
    response_model=PublishedTopicOut,
)
async def publish(
    run_id: UUID,
    request: ScopedRequest = Depends(scoped("teaching.generation.publish")),
    _admin: Principal = Depends(require_administrator),
) -> PublishedTopicOut:
    await review.seal_open_round_for_publish(request.session, run_id, request.principal)
    published = await service.publish(request.session, request.scope, request.principal, run_id)
    await request.record_rows(1, detail=f"published={run_id}")
    return PublishedTopicOut.from_domain(published)


@router.post(
    "/teaching/subtopics/{subtopic_id}/lesson-proposals",
    response_model=AcceptedRunOut,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
    dependencies=[Depends(require_role("teacher", "administrator"))],
)
async def submit_alias(
    subtopic_id: UUID,
    title: str = Form(...),
    file: UploadFile = File(...),
    request: ScopedRequest = Depends(scoped("teaching.generation.submit")),
) -> AcceptedRunOut:
    if not request.principal.is_administrator:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=TEACHER_UPLOAD_GONE)
    accepted = await service.submit_pdf_for_subtopic_alias(
        request.session,
        request.scope,
        request.principal,
        subtopic_id=subtopic_id,
        title=title,
        file=file,
    )
    await request.record_rows(1, detail=f"submitted={accepted.run_id}")
    return AcceptedRunOut.from_domain(accepted)


@router.get(
    "/teaching/lesson-proposals/{proposal_id}",
    dependencies=[Depends(require_role("teacher", "administrator"))],
)
async def gone_get_proposal() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Lesson proposals have been replaced by topic generation runs.",
    )


@router.get(
    "/teaching/subtopics/{subtopic_id}/lesson-proposals",
    dependencies=[Depends(require_role("teacher", "administrator"))],
)
async def gone_list_proposals() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Lesson proposals have been replaced by topic generation runs.",
    )
