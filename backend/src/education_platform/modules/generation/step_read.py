"""Read-only admin view of one generation run's pipeline steps.

Does not enqueue work, change phase, or rewrite artifacts. Teachers and students
are kept out at the router.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.modules.assessments.models import QuizVersion
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.generation.items import OutlineNodeRef, texts_for_nodes
from education_platform.modules.generation.models import (
    ContentGenerationOutlineNode,
    ContentGenerationRun,
    GenerationChangeRequest,
    GenerationJob,
    ReviewDecisionRow,
    ReviewRoundClosure,
    ReviewRoundParticipant,
    ReviewRoundRow,
)
from education_platform.modules.generation.outline import section_group_label, source_sections
from education_platform.modules.generation.service import (
    get_run,
    item_validation_for_runs,
    qa_items_for_run,
)
from education_platform.modules.generation.types import (
    GenerationError,
    GenerationJobKind,
    GenerationJobStatus,
    GenerationRun,
    QaItem,
    ReviewStage,
    RunPhase,
)
from education_platform.modules.judge.views import JudgeReadout, load_judge_readouts
from education_platform.modules.materials.models import SourceChunk, SourceMaterialVersion
from education_platform.modules.rag.models import IngestJob, IngestJobStatus

STEP_IDS: tuple[str, ...] = (
    "indexing",
    "outlining",
    "outline_review",
    "lesson",
    "items",
    "ready",
)
_STEP_LABELS = {
    "indexing": "Indexing PDF",
    "outlining": "Determining outline",
    "outline_review": "Outline review",
    "lesson": "Generating lesson",
    "items": "Generating quiz items",
    "ready": "Ready to review",
}
_STEP_INDEX = {step: index for index, step in enumerate(STEP_IDS)}
_OUTLINE_KINDS = frozenset({GenerationJobKind.OUTLINE, GenerationJobKind.REWRITE_OUTLINE})
_LESSON_KINDS = frozenset({GenerationJobKind.LESSON, GenerationJobKind.REWRITE_CONTENT})
_ITEM_KINDS = frozenset({GenerationJobKind.ITEMS, GenerationJobKind.REGENERATE_ITEMS})
_STEP_FOR_KIND = {
    GenerationJobKind.OUTLINE: "outlining",
    GenerationJobKind.REWRITE_OUTLINE: "outlining",
    GenerationJobKind.LESSON: "lesson",
    GenerationJobKind.REWRITE_CONTENT: "lesson",
    GenerationJobKind.ITEMS: "items",
    GenerationJobKind.REGENERATE_ITEMS: "items",
}
_TERMINAL_JOB = frozenset({GenerationJobStatus.SUCCEEDED, GenerationJobStatus.FAILED})
_TERMINAL_INGEST = frozenset({IngestJobStatus.SUCCEEDED, IngestJobStatus.FAILED})
_PREVIEW_CHARS = 320
_CHUNK_CAP = 400
_NOT_RUN = "This step has not run yet."


@dataclass(frozen=True, slots=True)
class StepTiming:
    step: str
    label: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    error: str | None
    available: bool
    hint: str | None


@dataclass(frozen=True, slots=True)
class ChunkPreview:
    ordinal: int
    page_number: int | None
    section_heading: str | None
    preview: str


@dataclass(frozen=True, slots=True)
class IndexingDetail:
    page_count: int | None
    chunk_count: int
    chunks: tuple[ChunkPreview, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class OutlineNodeDetail:
    id: UUID
    parent_id: UUID | None
    title: str
    sequence: int
    quota: int | None
    proposed_outcomes: tuple[str, ...]
    chunks: tuple[ChunkPreview, ...]


@dataclass(frozen=True, slots=True)
class OutlineDetail:
    target_item_count: int
    nodes: tuple[OutlineNodeDetail, ...]
    unmapped_chunks: tuple[ChunkPreview, ...]


@dataclass(frozen=True, slots=True)
class ReviewCommentDetail:
    author: str
    kind: str
    field: str
    comment: str
    target_title: str | None


@dataclass(frozen=True, slots=True)
class ReviewDecisionDetail:
    reviewer: str
    verdict: str


@dataclass(frozen=True, slots=True)
class ReviewRoundDetail:
    id: UUID
    stage: str
    number: int
    opened_at: datetime
    sealed_at: datetime | None
    decisions: tuple[ReviewDecisionDetail, ...]
    comments: tuple[ReviewCommentDetail, ...]
    closure_action: str | None
    closed_by: str | None
    rationale: str | None


@dataclass(frozen=True, slots=True)
class OutlineReviewDetail:
    rounds: tuple[ReviewRoundDetail, ...]


@dataclass(frozen=True, slots=True)
class LessonDetail:
    markdown: str | None
    published_lesson_version_id: UUID | None
    judge: tuple[JudgeReadout, ...] = ()
    needs_review: bool = False


@dataclass(frozen=True, slots=True)
class SubtopicCount:
    subtopic_id: UUID
    title: str
    count: int


@dataclass(frozen=True, slots=True)
class ItemsDetail:
    count: int
    difficulty_mix: dict[str, int]
    bloom_mix: dict[str, int]
    subtopic_counts: tuple[SubtopicCount, ...]
    passed: int | None
    fixed_on_retry: int | None
    dropped: int | None
    key_disputed: int
    items: tuple[QaItem, ...]
    judge: tuple[JudgeReadout, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadyDetail:
    phase: str
    published: bool
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    lesson_version_id: UUID | None
    quiz_version_id: UUID | None


@dataclass(frozen=True, slots=True)
class StepDetail:
    timing: StepTiming
    indexing: IndexingDetail | None = None
    outline: OutlineDetail | None = None
    outline_review: OutlineReviewDetail | None = None
    lesson: LessonDetail | None = None
    items: ItemsDetail | None = None
    ready: ReadyDetail | None = None


@dataclass(frozen=True, slots=True)
class _Facts:
    row: ContentGenerationRun
    jobs: tuple[GenerationJob, ...]
    ingest: IngestJob | None
    nodes: tuple[ContentGenerationOutlineNode, ...]
    rounds: tuple[ReviewRoundRow, ...]
    published_at: datetime | None
    domain: GenerationRun


def parse_step_id(raw: str) -> str:
    if raw not in _STEP_INDEX:
        raise GenerationError("That generation step does not exist.", status_code=404)
    return raw


async def list_step_timings(session: AsyncSession, scope: Scope, run_id: UUID) -> list[StepTiming]:
    facts = await _facts(session, scope, run_id)
    return _timings(facts)


async def get_step_detail(
    session: AsyncSession, scope: Scope, run_id: UUID, step: str
) -> StepDetail:
    step_id = parse_step_id(step)
    facts = await _facts(session, scope, run_id)
    timing = next(item for item in _timings(facts) if item.step == step_id)
    if not timing.available:
        return StepDetail(timing=timing)
    if step_id == "indexing":
        return StepDetail(timing=timing, indexing=await _indexing_detail(session, facts))
    if step_id == "outlining":
        return StepDetail(timing=timing, outline=await _outline_detail(session, facts))
    if step_id == "outline_review":
        return StepDetail(timing=timing, outline_review=await _review_detail(session, facts))
    if step_id == "lesson":
        markdown = (facts.row.draft_lesson_markdown or "").strip() or None
        readouts = await load_judge_readouts(session, facts.row.id)
        return StepDetail(
            timing=timing,
            lesson=LessonDetail(
                markdown=markdown,
                published_lesson_version_id=facts.row.published_lesson_version_id,
                judge=tuple(
                    item for item in readouts if item.subject_kind in {"lesson", "lesson_section"}
                ),
                needs_review=any(
                    item.subject_kind == "lesson" and item.decision == "needs_review"
                    for item in readouts
                ),
            ),
        )
    if step_id == "items":
        return StepDetail(timing=timing, items=await _items_detail(session, facts))
    return StepDetail(timing=timing, ready=_ready_detail(facts))


async def _facts(session: AsyncSession, scope: Scope, run_id: UUID) -> _Facts:
    domain = await get_run(session, scope, run_id, heal=False)
    row = await session.get(ContentGenerationRun, domain.id)
    if row is None:
        raise GenerationError("That generation run does not exist.", status_code=404)
    jobs = tuple(
        (
            await session.scalars(
                select(GenerationJob)
                .where(GenerationJob.run_id == row.id)
                .order_by(GenerationJob.created_at, GenerationJob.kind)
            )
        ).all()
    )
    nodes = tuple(
        (
            await session.scalars(
                select(ContentGenerationOutlineNode)
                .where(ContentGenerationOutlineNode.run_id == row.id)
                .order_by(ContentGenerationOutlineNode.sequence)
            )
        ).all()
    )
    rounds = tuple(
        (
            await session.scalars(
                select(ReviewRoundRow)
                .where(
                    ReviewRoundRow.run_id == row.id,
                    ReviewRoundRow.stage == ReviewStage.OUTLINE,
                )
                .order_by(ReviewRoundRow.number)
            )
        ).all()
    )
    ingest: IngestJob | None = None
    if row.intake_source_material_version_id is not None:
        ingest = await session.scalar(
            select(IngestJob)
            .where(IngestJob.source_material_version_id == row.intake_source_material_version_id)
            .order_by(IngestJob.created_at.desc())
        )
    return _Facts(
        row=row,
        jobs=jobs,
        ingest=ingest,
        nodes=nodes,
        rounds=rounds,
        published_at=await _published_at(session, row),
        domain=domain,
    )


def _timings(facts: _Facts) -> list[StepTiming]:
    statuses = _statuses(facts)
    failed_step = _failed_step(facts) if facts.row.phase is RunPhase.FAILED else None
    timings: list[StepTiming] = []
    for step in STEP_IDS:
        status = statuses[step]
        started_at, ended_at = _window(facts, step, status)
        error = _error(facts, step, status, failed_step)
        pending = status == "pending"
        timings.append(
            StepTiming(
                step=step,
                label=_STEP_LABELS[step],
                status=status,
                started_at=started_at,
                ended_at=ended_at,
                duration_seconds=_duration(started_at, ended_at),
                error=error,
                available=not pending,
                hint=_NOT_RUN if pending else None,
            )
        )
    return timings


def _statuses(facts: _Facts) -> dict[str, str]:
    phase = facts.row.phase
    if phase is RunPhase.PUBLISHED:
        return {step: "complete" for step in STEP_IDS}
    if phase is RunPhase.FAILED:
        failed = _failed_step(facts)
        index = _STEP_INDEX[failed]
        return {
            step: "complete" if position < index else "failed" if position == index else "pending"
            for position, step in enumerate(STEP_IDS)
        }
    if phase is RunPhase.DISCARDED:
        return {step: _discarded_status(facts, step) for step in STEP_IDS}
    active = _active_index(facts)
    return {
        step: "complete" if position < active else "current" if position == active else "pending"
        for position, step in enumerate(STEP_IDS)
    }


def _active_index(facts: _Facts) -> int:
    phase = facts.row.phase
    if phase is RunPhase.INDEXING:
        return 0
    if phase is RunPhase.OUTLINING:
        return 1
    if phase is RunPhase.OUTLINE_REVIEW:
        return 2
    if phase is RunPhase.QA_REVIEW:
        return 5
    if _inflight(facts.jobs, frozenset({GenerationJobKind.REGENERATE_ITEMS})):
        return 4
    if _inflight(facts.jobs, _LESSON_KINDS):
        return 3
    if _inflight(facts.jobs, _ITEM_KINDS):
        return 4
    if _inflight(facts.jobs, _OUTLINE_KINDS):
        return 1
    if not _succeeded(_latest(facts.jobs, _LESSON_KINDS)):
        return 3
    if _latest(facts.jobs, _ITEM_KINDS) is None:
        return 3
    return 4


def _discarded_status(facts: _Facts, step: str) -> str:
    if step == "indexing":
        if facts.ingest is not None and facts.ingest.status is IngestJobStatus.FAILED:
            return "failed"
        if facts.ingest is not None and facts.ingest.status is IngestJobStatus.SUCCEEDED:
            return "complete"
        if facts.nodes:
            return "complete"
        return "pending"
    if step == "outlining":
        return "complete" if facts.nodes else "pending"
    if step == "outline_review":
        return "complete" if facts.rounds else "pending"
    if step == "lesson":
        return "complete" if (facts.row.draft_lesson_markdown or "").strip() else "pending"
    if step == "items":
        if facts.row.draft_quiz_version_id is not None or facts.row.published_quiz_version_id:
            return "complete"
        return "pending"
    if facts.row.published_lesson_version_id is not None:
        return "complete"
    return "pending"


def _failed_step(facts: _Facts) -> str:
    failed = [job for job in facts.jobs if job.status is GenerationJobStatus.FAILED]
    if failed:
        latest = max(failed, key=lambda job: (job.created_at, str(job.id)))
        return _STEP_FOR_KIND[latest.kind]
    if facts.ingest is not None and facts.ingest.status is IngestJobStatus.FAILED:
        return "indexing"
    if not facts.nodes:
        return "indexing"
    if not (facts.row.draft_lesson_markdown or "").strip():
        return "outline_review"
    if facts.row.draft_quiz_version_id is None and facts.row.published_quiz_version_id is None:
        return "lesson"
    return "items"


def _window(facts: _Facts, step: str, status: str) -> tuple[datetime | None, datetime | None]:
    if status == "pending":
        return None, None
    if step == "indexing":
        return _ingest_times(facts.ingest)
    if step == "outlining":
        return _job_times(_latest(facts.jobs, _OUTLINE_KINDS))
    if step == "outline_review":
        return _review_times(facts.rounds, status)
    if step == "lesson":
        return _job_times(_latest(facts.jobs, _LESSON_KINDS))
    if step == "items":
        return _job_times(_latest(facts.jobs, _ITEM_KINDS))
    lesson_end = _job_times(_latest(facts.jobs, _LESSON_KINDS))[1]
    items_end = _job_times(_latest(facts.jobs, _ITEM_KINDS))[1]
    started = _later(lesson_end, items_end)
    ended = (
        facts.published_at
        if status == "complete" and facts.row.phase is RunPhase.PUBLISHED
        else None
    )
    return started, ended


def _error(facts: _Facts, step: str, status: str, failed_step: str | None) -> str | None:
    if status != "failed":
        return None
    if step == "indexing":
        if facts.ingest is not None and facts.ingest.error:
            return facts.ingest.error
        return facts.row.failure_reason
    kinds = _kinds_for(step)
    job = _latest(facts.jobs, kinds) if kinds else None
    if job is not None and job.status is GenerationJobStatus.FAILED and job.error:
        return job.error
    if failed_step == step:
        return facts.row.failure_reason
    return facts.row.failure_reason


def _kinds_for(step: str) -> frozenset[GenerationJobKind]:
    if step == "outlining":
        return _OUTLINE_KINDS
    if step == "lesson":
        return _LESSON_KINDS
    if step == "items":
        return _ITEM_KINDS
    return frozenset()


def _latest(
    jobs: Sequence[GenerationJob], kinds: frozenset[GenerationJobKind]
) -> GenerationJob | None:
    matched = [job for job in jobs if job.kind in kinds]
    if not matched:
        return None
    return max(matched, key=lambda job: (job.created_at, str(job.id)))


def _inflight(jobs: Sequence[GenerationJob], kinds: frozenset[GenerationJobKind]) -> bool:
    return any(
        job.kind in kinds
        and job.status in (GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)
        for job in jobs
    )


def _succeeded(job: GenerationJob | None) -> bool:
    return job is not None and job.status is GenerationJobStatus.SUCCEEDED


def _job_times(job: GenerationJob | None) -> tuple[datetime | None, datetime | None]:
    if job is None:
        return None, None
    ended = job.updated_at if job.status in _TERMINAL_JOB else None
    return job.created_at, ended


def _ingest_times(ingest: IngestJob | None) -> tuple[datetime | None, datetime | None]:
    if ingest is None:
        return None, None
    ended = ingest.updated_at if ingest.status in _TERMINAL_INGEST else None
    return ingest.created_at, ended


def _review_times(
    rounds: Sequence[ReviewRoundRow], status: str
) -> tuple[datetime | None, datetime | None]:
    if not rounds:
        return None, None
    started = min(row.opened_at for row in rounds)
    sealed = [row.sealed_at for row in rounds if row.sealed_at is not None]
    if status == "current" or len(sealed) != len(rounds):
        return started, None
    return started, max(sealed)


def _later(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if left >= right else right


def _duration(started: datetime | None, ended: datetime | None) -> int | None:
    if started is None or ended is None:
        return None
    return max(0, int((ended - started).total_seconds()))


def _ready_detail(facts: _Facts) -> ReadyDetail:
    return ReadyDetail(
        phase=facts.row.phase.value,
        published=facts.row.phase is RunPhase.PUBLISHED,
        published_at=facts.published_at,
        created_at=facts.row.created_at,
        updated_at=facts.row.updated_at,
        lesson_version_id=facts.row.published_lesson_version_id,
        quiz_version_id=facts.row.published_quiz_version_id,
    )


async def _published_at(session: AsyncSession, row: ContentGenerationRun) -> datetime | None:
    if row.published_lesson_version_id is not None:
        version = await session.get(SourceMaterialVersion, row.published_lesson_version_id)
        if version is not None and version.published_at is not None:
            return version.published_at
    if row.published_quiz_version_id is not None:
        quiz = await session.get(QuizVersion, row.published_quiz_version_id)
        if quiz is not None and quiz.released_at is not None:
            return quiz.released_at
    if row.phase is RunPhase.PUBLISHED:
        return row.updated_at
    return None


async def _chunks(session: AsyncSession, facts: _Facts) -> list[SourceChunk]:
    version_id = facts.row.intake_source_material_version_id
    if version_id is None:
        return []
    return list(
        (
            await session.scalars(
                select(SourceChunk)
                .where(SourceChunk.source_material_version_id == version_id)
                .order_by(SourceChunk.ordinal)
            )
        ).all()
    )


def _preview(chunk: SourceChunk) -> ChunkPreview:
    collapsed = " ".join(chunk.text.split())
    if len(collapsed) > _PREVIEW_CHARS:
        collapsed = collapsed[: _PREVIEW_CHARS - 1].rstrip() + "…"
    return ChunkPreview(
        ordinal=chunk.ordinal,
        page_number=chunk.page_number,
        section_heading=chunk.section_heading,
        preview=collapsed,
    )


async def _indexing_detail(session: AsyncSession, facts: _Facts) -> IndexingDetail:
    chunks = await _chunks(session, facts)
    pages = [chunk.page_number for chunk in chunks if chunk.page_number is not None]
    shown = chunks[:_CHUNK_CAP]
    return IndexingDetail(
        page_count=max(pages) if pages else None,
        chunk_count=len(chunks),
        chunks=tuple(_preview(chunk) for chunk in shown),
        truncated=len(chunks) > _CHUNK_CAP,
    )


async def _outline_detail(session: AsyncSession, facts: _Facts) -> OutlineDetail:
    chunks = await _chunks(session, facts)
    mapped, unmapped = _map_chunks(facts.nodes, chunks)
    nodes = tuple(
        OutlineNodeDetail(
            id=node.id,
            parent_id=node.parent_id,
            title=node.title,
            sequence=node.sequence,
            quota=node.quota,
            proposed_outcomes=tuple(_strings(node.proposed_outcomes)),
            chunks=tuple(_preview(chunk) for chunk in mapped.get(node.id, ())),
        )
        for node in facts.nodes
    )
    return OutlineDetail(
        target_item_count=facts.row.target_item_count,
        nodes=nodes,
        unmapped_chunks=tuple(_preview(chunk) for chunk in unmapped),
    )


def _map_chunks(
    nodes: Sequence[ContentGenerationOutlineNode],
    chunks: Sequence[SourceChunk],
) -> tuple[dict[UUID, tuple[SourceChunk, ...]], tuple[SourceChunk, ...]]:
    if not nodes:
        return {}, tuple(chunks)
    titles = {node.id: node.title for node in nodes}
    refs = tuple(
        OutlineNodeRef(
            id=node.id,
            slug=node.slug,
            title=node.title,
            sequence=node.sequence,
            parent_title=titles.get(node.parent_id) if node.parent_id is not None else None,
            source_headings=tuple(_strings(node.source_headings)),
        )
        for node in nodes
    )
    ordered = list(chunks)
    sections = source_sections(
        (
            chunk.section_heading,
            chunk.text,
            chunk.token_count or len(chunk.text.split()),
        )
        for chunk in ordered
    )
    remaining = list(ordered)
    groups: list[tuple[str, tuple[SourceChunk, ...]]] = []
    for section in sections:
        taken: list[SourceChunk] = []
        for text in section.texts:
            match_at = next(
                (index for index, chunk in enumerate(remaining) if chunk.text == text),
                None,
            )
            if match_at is None:
                continue
            taken.append(remaining.pop(match_at))
        groups.append((section_group_label(section), tuple(taken)))
    assigned = texts_for_nodes(
        refs,
        [(label, tuple(chunk.text for chunk in taken)) for label, taken in groups],
    )
    queues: dict[str, deque[SourceChunk]] = defaultdict(deque)
    for _label, grouped in groups:
        for chunk in grouped:
            queues[chunk.text].append(chunk)
    mapped: dict[UUID, tuple[SourceChunk, ...]] = {}
    used: set[UUID] = set()
    for node_id, texts in assigned.items():
        owned: list[SourceChunk] = []
        for text in texts:
            queue = queues.get(text)
            if queue:
                chunk = queue.popleft()
                owned.append(chunk)
                used.add(chunk.id)
        mapped[node_id] = tuple(owned)
    unmapped = tuple(chunk for chunk in ordered if chunk.id not in used)
    return mapped, unmapped


def _strings(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()]


async def _review_detail(session: AsyncSession, facts: _Facts) -> OutlineReviewDetail:
    rounds = facts.rounds
    if not rounds:
        return OutlineReviewDetail(rounds=())
    round_ids = tuple(row.id for row in rounds)
    participants = (
        await session.scalars(
            select(ReviewRoundParticipant).where(ReviewRoundParticipant.round_id.in_(round_ids))
        )
    ).all()
    decisions = (
        await session.scalars(
            select(ReviewDecisionRow).where(ReviewDecisionRow.round_id.in_(round_ids))
        )
    ).all()
    closures = (
        await session.scalars(
            select(ReviewRoundClosure).where(ReviewRoundClosure.round_id.in_(round_ids))
        )
    ).all()
    decision_ids = tuple(row.id for row in decisions)
    requests: Sequence[GenerationChangeRequest] = ()
    if decision_ids:
        requests = (
            await session.scalars(
                select(GenerationChangeRequest).where(
                    GenerationChangeRequest.decision_id.in_(decision_ids)
                )
            )
        ).all()
    titles = {node.id: node.title for node in facts.nodes}
    by_round_participants: dict[UUID, list[ReviewRoundParticipant]] = defaultdict(list)
    for participant in participants:
        by_round_participants[participant.round_id].append(participant)
    by_round_decisions: dict[UUID, list[ReviewDecisionRow]] = defaultdict(list)
    for decision in decisions:
        by_round_decisions[decision.round_id].append(decision)
    by_decision_requests: dict[UUID, list[GenerationChangeRequest]] = defaultdict(list)
    for request in requests:
        by_decision_requests[request.decision_id].append(request)
    closure_by_round = {closure.round_id: closure for closure in closures}
    detailed: list[ReviewRoundDetail] = []
    for row in rounds:
        round_decisions = by_round_decisions.get(row.id, [])
        decided = {item.reviewer_user_id: item for item in round_decisions}
        decision_rows: list[ReviewDecisionDetail] = []
        seen: set[UUID] = set()
        for participant in by_round_participants.get(row.id, []):
            seen.add(participant.reviewer_user_id)
            recorded = decided.get(participant.reviewer_user_id)
            if recorded is None:
                verdict = "pending" if row.sealed_at is None else "abstained"
                reviewer = participant.display_name
            else:
                verdict = recorded.verdict
                reviewer = recorded.display_name
            decision_rows.append(ReviewDecisionDetail(reviewer=reviewer, verdict=verdict))
        for decision in round_decisions:
            if decision.reviewer_user_id in seen:
                continue
            decision_rows.append(
                ReviewDecisionDetail(reviewer=decision.display_name, verdict=decision.verdict)
            )
        comments: list[ReviewCommentDetail] = []
        for decision in round_decisions:
            for request in by_decision_requests.get(decision.id, []):
                comments.append(
                    ReviewCommentDetail(
                        author=request.display_name,
                        kind=request.kind.value,
                        field=request.field,
                        comment=request.comment,
                        target_title=titles.get(request.node_key) if request.node_key else None,
                    )
                )
        closure = closure_by_round.get(row.id)
        detailed.append(
            ReviewRoundDetail(
                id=row.id,
                stage=row.stage.value,
                number=row.number,
                opened_at=row.opened_at,
                sealed_at=row.sealed_at,
                decisions=tuple(decision_rows),
                comments=tuple(comments),
                closure_action=closure.action.value if closure is not None else None,
                closed_by=closure.display_name if closure is not None else None,
                rationale=closure.rationale if closure is not None else None,
            )
        )
    return OutlineReviewDetail(rounds=tuple(detailed))


async def _items_detail(session: AsyncSession, facts: _Facts) -> ItemsDetail:
    items = tuple(await qa_items_for_run(session, facts.domain))
    readouts = await load_judge_readouts(session, facts.row.id)
    stored = await item_validation_for_runs(session, [facts.row.id])
    validation = stored.get(facts.row.id)
    passed, fixed, dropped = _validation_counts(validation, items)
    return ItemsDetail(
        count=len(items),
        difficulty_mix=_difficulty_mix(items),
        bloom_mix=_bloom_mix(items),
        subtopic_counts=tuple(_subtopic_counts(items)),
        passed=passed,
        fixed_on_retry=fixed,
        dropped=dropped,
        key_disputed=sum(
            1
            for item in items
            if isinstance(item.blind_solve, dict) and item.blind_solve.get("status") == "disagree"
        ),
        items=items,
        judge=tuple(item for item in readouts if item.subject_kind == "quiz_item"),
    )


def _validation_counts(
    stored: dict[str, int] | None, items: Sequence[QaItem]
) -> tuple[int | None, int | None, int | None]:
    if stored is not None:
        return stored["passed"], stored["fixed_on_retry"], stored["dropped"]
    if not any(item.check_verdict for item in items):
        return None, None, None
    fixed = sum(1 for item in items if item.check_verdict == "fixed_on_retry")
    dropped = sum(1 for item in items if item.check_verdict == "dropped")
    passed = sum(1 for item in items if item.check_verdict in {None, "passed"})
    return passed, fixed, dropped


def _difficulty_mix(items: Sequence[QaItem]) -> dict[str, int]:
    mix = {"easy": 0, "medium": 0, "hard": 0, "unlabeled": 0}
    for item in items:
        key = item.difficulty if item.difficulty in mix else "unlabeled"
        mix[key] += 1
    return mix


def _bloom_mix(items: Sequence[QaItem]) -> dict[str, int]:
    mix = {"remember": 0, "understand": 0, "apply": 0, "analyze": 0, "unlabeled": 0}
    for item in items:
        key = item.bloom.value if item.bloom is not None else "unlabeled"
        if key not in mix:
            key = "unlabeled"
        mix[key] += 1
    return mix


def _subtopic_counts(items: Sequence[QaItem]) -> list[SubtopicCount]:
    grouped: dict[UUID, SubtopicCount] = {}
    for item in items:
        current = grouped.get(item.subtopic_id)
        title = item.subtopic_title or "Untitled subtopic"
        if current is None:
            grouped[item.subtopic_id] = SubtopicCount(
                subtopic_id=item.subtopic_id, title=title, count=1
            )
            continue
        grouped[item.subtopic_id] = SubtopicCount(
            subtopic_id=item.subtopic_id, title=current.title, count=current.count + 1
        )
    return sorted(grouped.values(), key=lambda row: (-row.count, row.title))
