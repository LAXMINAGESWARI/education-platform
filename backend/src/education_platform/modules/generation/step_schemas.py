"""HTTP models for the admin generation-step view. Not used by the worker."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from education_platform.modules.generation.schemas import QaItemOut
from education_platform.modules.generation.step_read import (
    ChunkPreview,
    IndexingDetail,
    ItemsDetail,
    LessonDetail,
    OutlineDetail,
    OutlineNodeDetail,
    OutlineReviewDetail,
    ReadyDetail,
    ReviewCommentDetail,
    ReviewDecisionDetail,
    ReviewRoundDetail,
    StepDetail,
    StepTiming,
    SubtopicCount,
)
from education_platform.modules.judge.views import JudgeCheckView, JudgeReadout


class GenerationStepTimingOut(BaseModel):
    step: str
    label: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    error: str | None
    available: bool
    hint: str | None

    @classmethod
    def from_domain(cls, timing: StepTiming) -> GenerationStepTimingOut:
        return cls(
            step=timing.step,
            label=timing.label,
            status=timing.status,
            started_at=timing.started_at,
            ended_at=timing.ended_at,
            duration_seconds=timing.duration_seconds,
            error=timing.error,
            available=timing.available,
            hint=timing.hint,
        )


class ChunkPreviewOut(BaseModel):
    ordinal: int
    page_number: int | None
    section_heading: str | None
    preview: str

    @classmethod
    def from_domain(cls, chunk: ChunkPreview) -> ChunkPreviewOut:
        return cls(
            ordinal=chunk.ordinal,
            page_number=chunk.page_number,
            section_heading=chunk.section_heading,
            preview=chunk.preview,
        )


class IndexingStepOut(BaseModel):
    page_count: int | None
    chunk_count: int
    chunks: list[ChunkPreviewOut]
    truncated: bool

    @classmethod
    def from_domain(cls, detail: IndexingDetail) -> IndexingStepOut:
        return cls(
            page_count=detail.page_count,
            chunk_count=detail.chunk_count,
            chunks=[ChunkPreviewOut.from_domain(chunk) for chunk in detail.chunks],
            truncated=detail.truncated,
        )


class OutlineNodeStepOut(BaseModel):
    id: UUID
    parent_id: UUID | None
    title: str
    sequence: int
    quota: int | None
    proposed_outcomes: list[str]
    chunks: list[ChunkPreviewOut]

    @classmethod
    def from_domain(cls, node: OutlineNodeDetail) -> OutlineNodeStepOut:
        return cls(
            id=node.id,
            parent_id=node.parent_id,
            title=node.title,
            sequence=node.sequence,
            quota=node.quota,
            proposed_outcomes=list(node.proposed_outcomes),
            chunks=[ChunkPreviewOut.from_domain(chunk) for chunk in node.chunks],
        )


class OutlineStepOut(BaseModel):
    target_item_count: int
    nodes: list[OutlineNodeStepOut]
    unmapped_chunks: list[ChunkPreviewOut]

    @classmethod
    def from_domain(cls, detail: OutlineDetail) -> OutlineStepOut:
        return cls(
            target_item_count=detail.target_item_count,
            nodes=[OutlineNodeStepOut.from_domain(node) for node in detail.nodes],
            unmapped_chunks=[
                ChunkPreviewOut.from_domain(chunk) for chunk in detail.unmapped_chunks
            ],
        )


class ReviewCommentOut(BaseModel):
    author: str
    kind: str
    field: str
    comment: str
    target_title: str | None

    @classmethod
    def from_domain(cls, comment: ReviewCommentDetail) -> ReviewCommentOut:
        return cls(
            author=comment.author,
            kind=comment.kind,
            field=comment.field,
            comment=comment.comment,
            target_title=comment.target_title,
        )


class ReviewDecisionStepOut(BaseModel):
    reviewer: str
    verdict: str

    @classmethod
    def from_domain(cls, decision: ReviewDecisionDetail) -> ReviewDecisionStepOut:
        return cls(reviewer=decision.reviewer, verdict=decision.verdict)


class ReviewRoundStepOut(BaseModel):
    id: UUID
    stage: str
    number: int
    opened_at: datetime
    sealed_at: datetime | None
    decisions: list[ReviewDecisionStepOut]
    comments: list[ReviewCommentOut]
    closure_action: str | None
    closed_by: str | None
    rationale: str | None

    @classmethod
    def from_domain(cls, row: ReviewRoundDetail) -> ReviewRoundStepOut:
        return cls(
            id=row.id,
            stage=row.stage,
            number=row.number,
            opened_at=row.opened_at,
            sealed_at=row.sealed_at,
            decisions=[ReviewDecisionStepOut.from_domain(item) for item in row.decisions],
            comments=[ReviewCommentOut.from_domain(item) for item in row.comments],
            closure_action=row.closure_action,
            closed_by=row.closed_by,
            rationale=row.rationale,
        )


class OutlineReviewStepOut(BaseModel):
    rounds: list[ReviewRoundStepOut]

    @classmethod
    def from_domain(cls, detail: OutlineReviewDetail) -> OutlineReviewStepOut:
        return cls(rounds=[ReviewRoundStepOut.from_domain(row) for row in detail.rounds])


class JudgeCheckOut(BaseModel):
    check_id: str
    kind: str
    decision: str
    summary: str
    probabilities: dict[str, float]

    @classmethod
    def from_domain(cls, check: JudgeCheckView) -> JudgeCheckOut:
        return cls(
            check_id=check.check_id,
            kind=check.kind,
            decision=check.decision,
            summary=check.summary,
            probabilities=check.probabilities,
        )


class JudgeReadoutOut(BaseModel):
    subject_kind: str
    subject_key: UUID | None
    label: str
    decision: str
    reasons: list[str]
    checks: list[JudgeCheckOut]
    model: str | None
    question_version_id: UUID | None = None

    @classmethod
    def from_domain(cls, readout: JudgeReadout) -> JudgeReadoutOut:
        return cls(
            subject_kind=readout.subject_kind,
            subject_key=readout.subject_key,
            label=readout.label,
            decision=readout.decision,
            reasons=list(readout.reasons),
            checks=[JudgeCheckOut.from_domain(check) for check in readout.checks],
            model=readout.model,
            question_version_id=readout.question_version_id,
        )


class LessonStepOut(BaseModel):
    markdown: str | None
    published_lesson_version_id: UUID | None
    judge: list[JudgeReadoutOut] = Field(default_factory=list)
    needs_review: bool = False

    @classmethod
    def from_domain(cls, detail: LessonDetail) -> LessonStepOut:
        return cls(
            markdown=detail.markdown,
            published_lesson_version_id=detail.published_lesson_version_id,
            judge=[JudgeReadoutOut.from_domain(item) for item in detail.judge],
            needs_review=detail.needs_review,
        )


class DifficultyMixOut(BaseModel):
    easy: int
    medium: int
    hard: int
    unlabeled: int


class BloomMixOut(BaseModel):
    remember: int
    understand: int
    apply: int
    analyze: int
    unlabeled: int


class SubtopicCountOut(BaseModel):
    subtopic_id: UUID
    title: str
    count: int

    @classmethod
    def from_domain(cls, row: SubtopicCount) -> SubtopicCountOut:
        return cls(subtopic_id=row.subtopic_id, title=row.title, count=row.count)


class ItemsStepOut(BaseModel):
    count: int
    difficulty_mix: DifficultyMixOut
    bloom_mix: BloomMixOut
    subtopic_counts: list[SubtopicCountOut]
    passed: int | None
    fixed_on_retry: int | None
    dropped: int | None
    key_disputed: int
    items: list[QaItemOut]
    judge: list[JudgeReadoutOut] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, detail: ItemsDetail) -> ItemsStepOut:
        return cls(
            count=detail.count,
            difficulty_mix=DifficultyMixOut.model_validate(detail.difficulty_mix),
            bloom_mix=BloomMixOut.model_validate(detail.bloom_mix),
            subtopic_counts=[SubtopicCountOut.from_domain(row) for row in detail.subtopic_counts],
            passed=detail.passed,
            fixed_on_retry=detail.fixed_on_retry,
            dropped=detail.dropped,
            key_disputed=detail.key_disputed,
            items=[QaItemOut.from_domain(item) for item in detail.items],
            judge=[JudgeReadoutOut.from_domain(item) for item in detail.judge],
        )


class ReadyStepOut(BaseModel):
    phase: str
    published: bool
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    lesson_version_id: UUID | None
    quiz_version_id: UUID | None

    @classmethod
    def from_domain(cls, detail: ReadyDetail) -> ReadyStepOut:
        return cls(
            phase=detail.phase,
            published=detail.published,
            published_at=detail.published_at,
            created_at=detail.created_at,
            updated_at=detail.updated_at,
            lesson_version_id=detail.lesson_version_id,
            quiz_version_id=detail.quiz_version_id,
        )


class GenerationStepDetailOut(BaseModel):
    timing: GenerationStepTimingOut
    indexing: IndexingStepOut | None = None
    outline: OutlineStepOut | None = None
    outline_review: OutlineReviewStepOut | None = None
    lesson: LessonStepOut | None = None
    items: ItemsStepOut | None = None
    ready: ReadyStepOut | None = None

    @classmethod
    def from_domain(cls, detail: StepDetail) -> GenerationStepDetailOut:
        return cls(
            timing=GenerationStepTimingOut.from_domain(detail.timing),
            indexing=None
            if detail.indexing is None
            else IndexingStepOut.from_domain(detail.indexing),
            outline=None if detail.outline is None else OutlineStepOut.from_domain(detail.outline),
            outline_review=None
            if detail.outline_review is None
            else OutlineReviewStepOut.from_domain(detail.outline_review),
            lesson=None if detail.lesson is None else LessonStepOut.from_domain(detail.lesson),
            items=None if detail.items is None else ItemsStepOut.from_domain(detail.items),
            ready=None if detail.ready is None else ReadyStepOut.from_domain(detail.ready),
        )
