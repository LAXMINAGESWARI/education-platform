"""Frozen-revision review: workspace, teacher decision, administrator close."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.academics.models import (
    TeachingAssignment,
    TeachingAssignmentStatus,
    Topic,
)
from education_platform.modules.auth.models import User
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.generation.models import (
    ContentGenerationRun,
    GenerationChangeRequest,
    GenerationJob,
    GenerationRevision,
    ReviewDecisionRow,
    ReviewRoundClosure,
    ReviewRoundParticipant,
    ReviewRoundRow,
)
from education_platform.modules.generation.revisions import (
    apply_snapshot_to_run_nodes,
    assert_draft_matches_revision,
    diff_frozen,
    draft_request_from_row,
    frozen_revision_from_row,
    make_node_rows,
    make_revision_row,
    round_from_row,
)
from education_platform.modules.generation.service import (
    _authorised_topic,
    _require_closer,
    accept_outline,
    discard_outline,
)
from education_platform.modules.generation.types import (
    ApprovalDecision,
    ChangeRequest,
    ChangesRequestedDecision,
    CloseAction,
    CloseRoundCommand,
    CloseRoundResult,
    ContentAccepted,
    DiscardRun,
    DraftChangeRequest,
    DraftLessonRequest,
    DraftOutlineRequest,
    FrozenRevision,
    GenerationError,
    GenerationJobKind,
    GenerationJobStatus,
    HumanActorStamp,
    OutlineAccepted,
    OutlineRevision,
    OverrideOutlineAndAccept,
    RequestAgainstTarget,
    ReviewerState,
    ReviewerStateKind,
    ReviewRound,
    ReviewStage,
    ReviewWorkspace,
    RevisionOrigin,
    RewriteQueued,
    RoundCloseRecord,
    RunDiscarded,
    RunPhase,
    TeacherDecision,
    TeacherDecisionCommand,
    TeacherRoundNumber,
)
from education_platform.modules.judge.publish_gate import lesson_publish_blocked
from education_platform.modules.progress.types import run_subject
from education_platform.modules.progress.wake import publish_wake_async


async def get_review_workspace(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    run_id: UUID,
) -> ReviewWorkspace:
    run = await _load_authorised_run(session, scope, run_id)
    stage = _review_stage_for_phase(run.phase)
    revision_row = await _leaf_revision(session, run.id, stage)
    if revision_row is None:
        if stage is ReviewStage.QA:
            raise GenerationError(
                "Review is not open until a content revision exists.", status_code=409
            )
        raise GenerationError(
            "Review is not open until an outline revision exists.", status_code=409
        )
    display_name = await _user_name(session, revision_row.created_by_user_id)
    revision = frozen_revision_from_row(revision_row, display_name=display_name)
    parent_diff = None
    if revision.parent_id is not None:
        parent_row = await session.get(GenerationRevision, revision.parent_id)
        if parent_row is not None:
            parent_name = await _user_name(session, parent_row.created_by_user_id)
            parent_diff = diff_frozen(
                frozen_revision_from_row(parent_row, display_name=parent_name), revision
            )
    open_row = await _open_round(session, run.id)
    open_round = None
    reviewer_states: tuple[ReviewerState, ...] = ()
    if open_row is not None:
        participants = await _participants(session, open_row.id)
        open_round = round_from_row(open_row, participants)
        decisions = await _decisions_for_round(session, open_row)
        reviewer_states = _project_reviewer_states(open_round, decisions, datetime.now(UTC))
    elif run.phase in {RunPhase.OUTLINE_REVIEW, RunPhase.QA_REVIEW}:
        latest = await _latest_round(session, run.id, stage)
        if latest is not None:
            participants = await _participants(session, latest.id)
            sealed = round_from_row(latest, participants)
            decisions = await _decisions_for_round(session, latest)
            reviewer_states = _project_reviewer_states(sealed, decisions, datetime.now(UTC))
    threads = await _request_threads(session, run.id, revision)
    history = await _close_history(session, run.id)
    return ReviewWorkspace(
        run_id=run.id,
        topic_id=run.topic_id,
        phase=run.phase,
        published_locked=run.phase is RunPhase.PUBLISHED,
        viewer_is_closer=principal.is_administrator,
        active_revision=revision,
        diff_from_parent=parent_diff,
        open_round=open_round,
        reviewer_states=reviewer_states,
        request_threads=threads,
        history=history,
    )


async def submit_teacher_decision(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    run_id: UUID,
    round_id: UUID,
    command: TeacherDecisionCommand,
) -> TeacherDecision:
    if principal.is_administrator:
        raise GenerationError(
            "Administrators close this review; they do not file a teacher decision.",
            status_code=403,
        )
    run = await _load_authorised_run(session, scope, run_id)
    if run.phase is RunPhase.PUBLISHED:
        raise GenerationError("Published curriculum is locked.", status_code=409)
    round_row = await session.get(ReviewRoundRow, round_id)
    if round_row is None or round_row.run_id != run.id:
        raise GenerationError("That review round does not exist.", status_code=404)
    if round_row.sealed_at is not None:
        raise GenerationError("This review round is already closed.", status_code=409)
    revision_row = await session.get(GenerationRevision, round_row.revision_id)
    if revision_row is None:
        raise GenerationError("That revision does not exist.", status_code=404)
    _assert_revision_command(
        command.revision_id,
        command.snapshot_hash,
        revision_row,
    )
    if not await _teaches_topic(session, principal.user_id, run.topic_id):
        raise GenerationError(
            "You do not teach this subject, so you cannot review this run.",
            status_code=403,
        )
    user = await session.get(User, principal.user_id)
    display_name = user.full_name if user is not None else principal.email
    now = datetime.now(UTC)
    if datetime.now(UTC) >= round_row.due_at:
        raise GenerationError("This review round has timed out.", status_code=409)
    already = await session.scalar(
        select(ReviewDecisionRow.id).where(
            ReviewDecisionRow.round_id == round_row.id,
            ReviewDecisionRow.reviewer_user_id == principal.user_id,
        )
    )
    if already is not None:
        raise GenerationError(
            "You have already submitted a decision for this round.",
            status_code=409,
        )
    decision = ReviewDecisionRow(
        id=uuid4(),
        round_id=round_row.id,
        reviewer_user_id=principal.user_id,
        revision_id=revision_row.id,
        verdict=command.verdict,
        display_name=display_name,
    )
    try:
        async with session.begin_nested():
            session.add(decision)
            await session.flush()
    except IntegrityError as exc:
        raise GenerationError(
            "You have already submitted a decision for this round.",
            status_code=409,
        ) from exc
    stamp = HumanActorStamp(user_id=principal.user_id, display_name=display_name, occurred_at=now)
    if command.verdict == "approve":
        return ApprovalDecision(
            id=decision.id,
            round_id=round_row.id,
            revision_id=revision_row.id,
            reviewer=stamp,
            verdict="approve",
        )
    snapshot = frozen_revision_from_row(revision_row)
    stored: list[ChangeRequest] = []
    for draft in command.requests:
        assert_draft_matches_revision(snapshot, draft)
        row = GenerationChangeRequest(
            decision_id=decision.id,
            revision_id=revision_row.id,
            author_user_id=principal.user_id,
            display_name=display_name,
            target_kind=draft.target.kind,
            node_key=_target_key(draft),
            field=draft.field.value,
            kind=draft.kind,
            comment=draft.comment.strip(),
        )
        session.add(row)
        await session.flush()
        stored.append(
            ChangeRequest(
                id=row.id,
                decision_id=decision.id,
                revision_id=revision_row.id,
                author=stamp,
                request=draft,
            )
        )
    return ChangesRequestedDecision(
        id=decision.id,
        round_id=round_row.id,
        revision_id=revision_row.id,
        reviewer=stamp,
        verdict="changes_requested",
        requests=tuple(stored),
    )


async def close_review_round(
    session: AsyncSession,
    scope: Scope,
    principal: Principal,
    run_id: UUID,
    round_id: UUID,
    command: CloseRoundCommand,
) -> CloseRoundResult:
    _require_closer(principal)
    run = await _load_authorised_run(session, scope, run_id)
    round_row = await session.get(ReviewRoundRow, round_id)
    if round_row is None or round_row.run_id != run.id:
        raise GenerationError("That review round does not exist.", status_code=404)
    if round_row.sealed_at is not None:
        raise GenerationError("This review round is already closed.", status_code=409)
    revision_row = await session.get(GenerationRevision, round_row.revision_id)
    if revision_row is None:
        raise GenerationError("That revision does not exist.", status_code=404)
    _assert_revision_command(command.revision_id, command.snapshot_hash, revision_row)
    actor = await _actor_stamp(session, principal)
    request_ids = await _round_request_ids(session, round_row.id)
    if command.action == "rewrite":
        return await _close_rewrite(
            session,
            run=run,
            round_row=round_row,
            revision_row=revision_row,
            actor=actor,
            request_ids=request_ids,
        )
    if command.action == "accept_current":
        await _seal_round(
            session,
            round_row=round_row,
            revision_id=revision_row.id,
            actor=actor,
            action=CloseAction.ACCEPT_CURRENT,
            request_ids=request_ids,
            rationale=None,
        )
        if round_row.stage is ReviewStage.QA:
            return ContentAccepted(
                kind="content_accepted",
                run_id=run.id,
                accepted_revision_id=revision_row.id,
            )
        accepted = await accept_outline(session, scope, principal, run.id)
        return OutlineAccepted(
            kind="outline_accepted",
            run_id=accepted.id,
            accepted_revision_id=revision_row.id,
        )
    if command.action == "override_and_accept":
        if round_row.stage is ReviewStage.QA:
            raise GenerationError(
                "Content override is not available in this slice. Rewrite, accept, or discard.",
                status_code=409,
            )
        if not isinstance(command, OverrideOutlineAndAccept):
            raise GenerationError("Override requires a replacement outline.", status_code=400)
        return await _close_override(
            session,
            scope=scope,
            principal=principal,
            run=run,
            round_row=round_row,
            revision_row=revision_row,
            actor=actor,
            command=command,
            request_ids=request_ids,
        )
    if command.action == "discard":
        if not isinstance(command, DiscardRun):
            raise GenerationError("Discard requires a discard command.", status_code=400)
        await _seal_round(
            session,
            round_row=round_row,
            revision_id=revision_row.id,
            actor=actor,
            action=CloseAction.DISCARD,
            request_ids=request_ids,
            rationale=command.rationale,
        )
        discarded = await discard_outline(session, scope, principal, run.id)
        return RunDiscarded(kind="run_discarded", run_id=discarded.id)
    raise GenerationError("Unsupported close action.", status_code=400)


async def _close_rewrite(
    session: AsyncSession,
    *,
    run: ContentGenerationRun,
    round_row: ReviewRoundRow,
    revision_row: GenerationRevision,
    actor: HumanActorStamp,
    request_ids: tuple[UUID, ...],
) -> RewriteQueued:
    if TeacherRoundNumber(round_row.number).successor() is None:
        raise GenerationError("Teacher round 2 cannot be rewritten.", status_code=409)
    if not request_ids:
        raise GenerationError("Rewrite needs at least one change request.", status_code=409)
    if round_row.stage is ReviewStage.QA:
        if run.phase is not RunPhase.QA_REVIEW:
            raise GenerationError("Only a QA review can be rewritten.", status_code=409)
        job_kind = GenerationJobKind.REWRITE_CONTENT
    elif run.phase is not RunPhase.OUTLINE_REVIEW:
        raise GenerationError(
            "Only an outline waiting for review can be rewritten.", status_code=409
        )
    else:
        job_kind = GenerationJobKind.REWRITE_OUTLINE
    closure = await _seal_round(
        session,
        round_row=round_row,
        revision_id=revision_row.id,
        actor=actor,
        action=CloseAction.REWRITE,
        request_ids=request_ids,
        rationale=None,
    )
    job = GenerationJob(
        run_id=run.id,
        kind=job_kind,
        status=GenerationJobStatus.QUEUED,
        payload={"close_record_id": str(closure.id)},
        close_record_id=closure.id,
    )
    session.add(job)
    await session.flush()
    await publish_wake_async(session, run_subject(run.id))
    return RewriteQueued(
        kind="rewrite_queued",
        job_id=job.id,
        close_record=_closure_to_domain(closure, actor),
    )


async def _close_override(
    session: AsyncSession,
    *,
    scope: Scope,
    principal: Principal,
    run: ContentGenerationRun,
    round_row: ReviewRoundRow,
    revision_row: GenerationRevision,
    actor: HumanActorStamp,
    command: OverrideOutlineAndAccept,
    request_ids: tuple[UUID, ...],
) -> OutlineAccepted:
    if run.phase is not RunPhase.OUTLINE_REVIEW:
        raise GenerationError(
            "Only an outline waiting for review can be overridden.",
            status_code=409,
        )
    successor = make_revision_row(
        run_id=run.id,
        snapshot=command.replacement,
        origin=RevisionOrigin.ADMIN_OVERRIDE,
        number=revision_row.number + 1,
        parent_id=revision_row.id,
        source_round_id=round_row.id,
        created_by_user_id=principal.user_id,
        created_by_job_id=None,
        created_by_model=None,
    )
    session.add(successor)
    await session.flush()
    for node in make_node_rows(successor.id, command.replacement):
        session.add(node)
    await session.flush()
    await session.run_sync(apply_snapshot_to_run_nodes, run.id, command.replacement)
    await session.flush()
    await _seal_round(
        session,
        round_row=round_row,
        revision_id=revision_row.id,
        actor=actor,
        action=CloseAction.OVERRIDE_AND_ACCEPT,
        request_ids=request_ids,
        rationale=command.rationale,
    )
    accepted = await accept_outline(session, scope, principal, run.id)
    return OutlineAccepted(
        kind="outline_accepted",
        run_id=accepted.id,
        accepted_revision_id=successor.id,
    )


async def _seal_round(
    session: AsyncSession,
    *,
    round_row: ReviewRoundRow,
    revision_id: UUID,
    actor: HumanActorStamp,
    action: CloseAction,
    request_ids: tuple[UUID, ...],
    rationale: str | None,
) -> ReviewRoundClosure:
    now = datetime.now(UTC)
    round_row.sealed_at = now
    closure = ReviewRoundClosure(
        round_id=round_row.id,
        base_revision_id=revision_id,
        action=action,
        actor_user_id=actor.user_id,
        display_name=actor.display_name,
        collated_request_ids=[str(item) for item in request_ids],
        rationale=rationale,
    )
    session.add(closure)
    await session.flush()
    return closure


async def seal_open_outline_round_for_accept(
    session: AsyncSession, run_id: UUID, principal: Principal
) -> None:
    round_row = await _open_round(session, run_id)
    if round_row is None:
        return
    revision_id = round_row.revision_id
    actor = await _actor_stamp(session, principal)
    request_ids = await _round_request_ids(session, round_row.id)
    await _seal_round(
        session,
        round_row=round_row,
        revision_id=revision_id,
        actor=actor,
        action=CloseAction.ACCEPT_CURRENT,
        request_ids=request_ids,
        rationale=None,
    )


def _assert_revision_command(
    revision_id: UUID,
    snapshot_hash: str | None,
    revision_row: GenerationRevision,
) -> None:
    if revision_id != revision_row.id:
        raise GenerationError(
            "This decision is for a stale revision. Reload the review workspace.",
            status_code=409,
        )
    if snapshot_hash is not None and snapshot_hash != revision_row.snapshot_hash:
        raise GenerationError(
            "This decision is for a stale revision. Reload the review workspace.",
            status_code=409,
        )


async def seal_open_round_for_publish(
    session: AsyncSession, run_id: UUID, principal: Principal
) -> None:
    """Seal an open QA round as accepted, unless a Jev lesson hold is still open.

    Publish auto-accepts a quiet round. That seal is not an admin approval, so
    it must not clear a lesson that still needs review. An earlier
    ``accept_current`` close is the approval that lets this return.
    """
    if await lesson_publish_blocked(session, run_id):
        raise GenerationError(
            "This lesson needs review. Accept it in QA review before publishing.",
            status_code=409,
        )
    round_row = await _open_round(session, run_id)
    if round_row is None:
        return
    actor = await _actor_stamp(session, principal)
    request_ids = await _round_request_ids(session, round_row.id)
    await _seal_round(
        session,
        round_row=round_row,
        revision_id=round_row.revision_id,
        actor=actor,
        action=CloseAction.ACCEPT_CURRENT,
        request_ids=request_ids,
        rationale=None,
    )


def _review_stage_for_phase(phase: RunPhase) -> ReviewStage:
    if phase in {RunPhase.QA_REVIEW, RunPhase.PUBLISHED}:
        return ReviewStage.QA
    return ReviewStage.OUTLINE


def _target_key(draft: DraftChangeRequest) -> UUID | None:
    if isinstance(draft, DraftOutlineRequest):
        target = draft.target
        if target.kind == "outline_document":
            return None
        return target.node_key
    if isinstance(draft, DraftLessonRequest):
        return draft.target.section_key
    return draft.target.item_key


def _project_reviewer_states(
    round: ReviewRound,
    decisions: dict[UUID, TeacherDecision],
    now: datetime,
) -> tuple[ReviewerState, ...]:
    timed_out = now >= round.due_at or round.sealed_at is not None
    states: list[ReviewerState] = []
    for participant in round.participants:
        decision = decisions.get(participant.reviewer_user_id)
        if decision is None:
            state = ReviewerStateKind.ABSTAINED if timed_out else ReviewerStateKind.PENDING
        elif decision.verdict == "approve":
            state = ReviewerStateKind.APPROVED
        else:
            state = ReviewerStateKind.CHANGES_REQUESTED
        states.append(ReviewerState(participant=participant, state=state, decision=decision))
    return tuple(states)


async def _load_authorised_run(
    session: AsyncSession, scope: Scope, run_id: UUID
) -> ContentGenerationRun:
    run = await session.get(ContentGenerationRun, run_id)
    if run is None:
        raise GenerationError("That generation run does not exist.", status_code=404)
    await _authorised_topic(session, scope, run.topic_id)
    return run


async def _leaf_revision(
    session: AsyncSession, run_id: UUID, stage: ReviewStage
) -> GenerationRevision | None:
    rows = list(
        (
            await session.scalars(
                select(GenerationRevision)
                .where(GenerationRevision.run_id == run_id, GenerationRevision.stage == stage)
                .order_by(GenerationRevision.number.desc())
            )
        ).all()
    )
    if not rows:
        return None
    children = {row.parent_revision_id for row in rows if row.parent_revision_id is not None}
    for row in rows:
        if row.id not in children:
            return row
    return rows[0]


async def _open_round(session: AsyncSession, run_id: UUID) -> ReviewRoundRow | None:
    return (
        await session.scalars(
            select(ReviewRoundRow).where(
                ReviewRoundRow.run_id == run_id,
                ReviewRoundRow.sealed_at.is_(None),
            )
        )
    ).first()


async def _latest_round(
    session: AsyncSession, run_id: UUID, stage: ReviewStage
) -> ReviewRoundRow | None:
    return (
        await session.scalars(
            select(ReviewRoundRow)
            .where(ReviewRoundRow.run_id == run_id, ReviewRoundRow.stage == stage)
            .order_by(ReviewRoundRow.number.desc())
            .limit(1)
        )
    ).first()


async def _participants(session: AsyncSession, round_id: UUID) -> list[ReviewRoundParticipant]:
    return list(
        (
            await session.scalars(
                select(ReviewRoundParticipant)
                .where(ReviewRoundParticipant.round_id == round_id)
                .order_by(ReviewRoundParticipant.display_name)
            )
        ).all()
    )


async def _decisions_for_round(
    session: AsyncSession, round_row: ReviewRoundRow
) -> dict[UUID, TeacherDecision]:
    rows = list(
        (
            await session.scalars(
                select(ReviewDecisionRow).where(ReviewDecisionRow.round_id == round_row.id)
            )
        ).all()
    )
    out: dict[UUID, TeacherDecision] = {}
    for row in rows:
        stamp = HumanActorStamp(
            user_id=row.reviewer_user_id,
            display_name=row.display_name,
            occurred_at=row.created_at,
        )
        if row.verdict == "approve":
            out[row.reviewer_user_id] = ApprovalDecision(
                id=row.id,
                round_id=row.round_id,
                revision_id=row.revision_id,
                reviewer=stamp,
                verdict="approve",
            )
            continue
        requests = await _requests_for_decision(session, row, stamp)
        out[row.reviewer_user_id] = ChangesRequestedDecision(
            id=row.id,
            round_id=row.round_id,
            revision_id=row.revision_id,
            reviewer=stamp,
            verdict="changes_requested",
            requests=requests,
        )
    return out


async def _requests_for_decision(
    session: AsyncSession,
    decision: ReviewDecisionRow,
    stamp: HumanActorStamp,
) -> tuple[ChangeRequest, ...]:
    rows = list(
        (
            await session.scalars(
                select(GenerationChangeRequest).where(
                    GenerationChangeRequest.decision_id == decision.id
                )
            )
        ).all()
    )
    items: list[ChangeRequest] = []
    for row in rows:
        draft = draft_request_from_row(row)
        if draft is None:
            continue
        items.append(
            ChangeRequest(
                id=row.id,
                decision_id=decision.id,
                revision_id=row.revision_id,
                author=stamp,
                request=draft,
            )
        )
    return tuple(items)


async def _request_threads(
    session: AsyncSession, run_id: UUID, revision: FrozenRevision
) -> tuple[RequestAgainstTarget, ...]:
    round_ids = list(
        await session.scalars(select(ReviewRoundRow.id).where(ReviewRoundRow.run_id == run_id))
    )
    if not round_ids:
        return ()
    decision_ids = list(
        await session.scalars(
            select(ReviewDecisionRow.id).where(ReviewDecisionRow.round_id.in_(tuple(round_ids)))
        )
    )
    if not decision_ids:
        return ()
    rows = list(
        (
            await session.scalars(
                select(GenerationChangeRequest)
                .where(GenerationChangeRequest.decision_id.in_(tuple(decision_ids)))
                .order_by(GenerationChangeRequest.created_at)
            )
        ).all()
    )
    titles = _target_titles(revision)
    threads: list[RequestAgainstTarget] = []
    for row in rows:
        draft = draft_request_from_row(row)
        if draft is None:
            continue
        stamp = HumanActorStamp(
            user_id=row.author_user_id,
            display_name=row.display_name,
            occurred_at=row.created_at,
        )
        request = ChangeRequest(
            id=row.id,
            decision_id=row.decision_id,
            revision_id=row.revision_id,
            author=stamp,
            request=draft,
        )
        threads.append(
            RequestAgainstTarget(
                change_request=request, target_title=titles.get(_target_key(draft))
            )
        )
    return tuple(threads)


def _target_titles(revision: FrozenRevision) -> dict[UUID | None, str | None]:
    titles: dict[UUID | None, str | None] = {None: None}
    if isinstance(revision, OutlineRevision):
        for node in revision.snapshot.nodes:
            titles[node.node_key] = node.title
        return titles
    for section in revision.snapshot.lesson_sections:
        titles[section.section_key] = section.heading
    for item in revision.snapshot.quiz_items:
        titles[item.item_key] = item.prompt
    return titles


async def _close_history(session: AsyncSession, run_id: UUID) -> tuple[RoundCloseRecord, ...]:
    round_ids = list(
        await session.scalars(select(ReviewRoundRow.id).where(ReviewRoundRow.run_id == run_id))
    )
    if not round_ids:
        return ()
    rows = list(
        (
            await session.scalars(
                select(ReviewRoundClosure)
                .where(ReviewRoundClosure.round_id.in_(tuple(round_ids)))
                .order_by(ReviewRoundClosure.created_at)
            )
        ).all()
    )
    return tuple(
        RoundCloseRecord(
            id=row.id,
            round_id=row.round_id,
            base_revision_id=row.base_revision_id,
            action=row.action,
            actor=HumanActorStamp(
                user_id=row.actor_user_id,
                display_name=row.display_name,
                occurred_at=row.created_at,
            ),
            collated_request_ids=tuple(UUID(str(item)) for item in row.collated_request_ids),
            rationale=row.rationale,
        )
        for row in rows
    )


async def _round_request_ids(session: AsyncSession, round_id: UUID) -> tuple[UUID, ...]:
    decision_ids = list(
        await session.scalars(
            select(ReviewDecisionRow.id).where(ReviewDecisionRow.round_id == round_id)
        )
    )
    if not decision_ids:
        return ()
    ids = list(
        await session.scalars(
            select(GenerationChangeRequest.id).where(
                GenerationChangeRequest.decision_id.in_(tuple(decision_ids))
            )
        )
    )
    return tuple(ids)


async def _teaches_topic(session: AsyncSession, user_id: UUID, topic_id: UUID) -> bool:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        return False
    assignment_id = await session.scalar(
        select(TeachingAssignment.id).where(
            TeachingAssignment.teacher_user_id == user_id,
            TeachingAssignment.grade_subject_offering_id == topic.grade_subject_offering_id,
            TeachingAssignment.status == TeachingAssignmentStatus.ACTIVE,
        )
    )
    return assignment_id is not None


async def _user_name(session: AsyncSession, user_id: UUID | None) -> str:
    if user_id is None:
        return ""
    user = await session.get(User, user_id)
    return user.full_name if user is not None else ""


async def _actor_stamp(session: AsyncSession, principal: Principal) -> HumanActorStamp:
    name = await _user_name(session, principal.user_id)
    return HumanActorStamp(
        user_id=principal.user_id,
        display_name=name or principal.email,
        occurred_at=datetime.now(UTC),
    )


def _closure_to_domain(row: ReviewRoundClosure, actor: HumanActorStamp) -> RoundCloseRecord:
    return RoundCloseRecord(
        id=row.id,
        round_id=row.round_id,
        base_revision_id=row.base_revision_id,
        action=row.action,
        actor=actor,
        collated_request_ids=tuple(UUID(str(item)) for item in row.collated_request_ids),
        rationale=row.rationale,
    )
