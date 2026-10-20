"""HTTP adapters for generation runs. Not imported by the worker or outline.py."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from education_platform.modules.generation.outline import preview_quotas
from education_platform.modules.generation.revisions import snapshot_from_json
from education_platform.modules.generation.types import (
    AcceptCurrent,
    AcceptedRun,
    ChangeKind,
    ChangeRequest,
    CloseRoundCommand,
    CloseRoundResult,
    ContentAccepted,
    ContentRevision,
    ContentRevisionDiff,
    CurriculumJob,
    DiscardRun,
    DraftChangeRequest,
    DraftLessonRequest,
    DraftOutlineRequest,
    DraftQuizRequest,
    FrozenRevision,
    GenerationRun,
    HumanActorStamp,
    ItemCount,
    LessonField,
    LessonSectionSnapshot,
    LessonSectionTarget,
    OutlineAccepted,
    OutlineDocumentTarget,
    OutlineField,
    OutlineNode,
    OutlineNodeEdit,
    OutlineNodeSnapshot,
    OutlineNodeTarget,
    OutlinePatch,
    OutlineRevision,
    OutlineRevisionDiff,
    OutlineSnapshot,
    OverrideOutlineAndAccept,
    PublishedTopic,
    QaItem,
    QuizField,
    QuizItemSnapshot,
    QuizItemTarget,
    RequestAgainstTarget,
    ReviewerState,
    ReviewRound,
    ReviewTarget,
    ReviewWorkspace,
    RewriteCurrent,
    RewriteQueued,
    RoundCloseRecord,
    RunDiscarded,
    RunPhase,
    SubmitApproval,
    SubmitChangesRequested,
    TeacherDecision,
    TeacherDecisionCommand,
    WorkerActorStamp,
)


class AcceptedRunOut(BaseModel):
    run_id: UUID
    topic_id: UUID
    phase: RunPhase

    @classmethod
    def from_domain(cls, accepted: AcceptedRun) -> AcceptedRunOut:
        return cls(run_id=accepted.run_id, topic_id=accepted.topic_id, phase=accepted.phase)


class GenerationJobOut(BaseModel):
    kind: str
    status: str


class CurriculumGenerationJobOut(BaseModel):
    id: UUID
    subtopic_id: UUID
    status: str
    round_count: int
    reviewer_notes: str | None
    error: str | None
    source_material_version_id: UUID | None
    quiz_version_id: UUID | None

    @classmethod
    def from_domain(cls, job: CurriculumJob) -> CurriculumGenerationJobOut:
        return cls(
            id=job.id,
            subtopic_id=job.subtopic_id,
            status=job.status.value,
            round_count=job.round_count,
            reviewer_notes=job.reviewer_notes,
            error=job.error,
            source_material_version_id=job.source_material_version_id,
            quiz_version_id=job.quiz_version_id,
        )


class OutlineNodeOut(BaseModel):
    id: UUID
    parent_id: UUID | None
    slug: str
    title: str
    token_mass: int
    prerequisite_score: Decimal
    centrality: Decimal
    weight: Decimal
    quota: int | None
    matched_subtopic_id: UUID | None
    force_create: bool
    accepted_subtopic_id: UUID | None
    proposed_outcomes: list[str]
    sequence: int

    @classmethod
    def from_domain(cls, node: OutlineNode) -> OutlineNodeOut:
        return cls(
            id=node.id,
            parent_id=node.parent_id,
            slug=node.slug,
            title=node.title,
            token_mass=node.token_mass,
            prerequisite_score=node.prerequisite_score,
            centrality=node.centrality,
            weight=node.weight,
            quota=node.quota,
            matched_subtopic_id=node.matched_subtopic_id,
            force_create=node.force_create,
            accepted_subtopic_id=node.accepted_subtopic_id,
            proposed_outcomes=[item.statement for item in node.proposed_outcomes],
            sequence=node.sequence,
        )


class OutlineOut(BaseModel):
    target_item_count: int
    nodes: list[OutlineNodeOut]


def _blind_qa_fields(raw: object) -> tuple[bool, str | None]:
    """key_disputed is a real disagreement only. Errors and skips are not disputes."""
    if not isinstance(raw, dict):
        return False, None
    answer = raw.get("answer")
    blind_answer = answer.strip() if isinstance(answer, str) and answer.strip() else None
    return raw.get("status") == "disagree", blind_answer


class QaItemOut(BaseModel):
    question_id: UUID
    question_version_id: UUID
    prompt: str
    options: dict[str, str]
    correct_label: str
    correct_rationale: str
    distractor_rationales: dict[str, str]
    subtopic_id: UUID
    sequence: int
    bloom: str | None = None
    misconception_labels: list[str] = Field(default_factory=list)
    key_disputed: bool = False
    blind_answer: str | None = None
    check_attempts: int | None = None
    check_verdict: str | None = None
    difficulty: str | None = None
    subtopic_title: str | None = None
    judge_needs_review: bool = False

    @classmethod
    def from_domain(cls, item: QaItem) -> QaItemOut:
        disputed, blind_answer = _blind_qa_fields(item.blind_solve)
        return cls(
            question_id=item.question_id,
            question_version_id=item.question_version_id,
            prompt=item.prompt,
            options={label: text for label, text in item.options},
            correct_label=item.correct_label,
            correct_rationale=item.correct_rationale,
            distractor_rationales=item.distractor_rationales,
            subtopic_id=item.subtopic_id,
            sequence=item.sequence,
            bloom=item.bloom.value if item.bloom is not None else None,
            misconception_labels=list(item.misconception_labels),
            key_disputed=disputed,
            blind_answer=blind_answer,
            check_attempts=item.check_attempts,
            check_verdict=item.check_verdict,
            difficulty=item.difficulty,
            subtopic_title=item.subtopic_title,
            judge_needs_review=item.judge_needs_review,
        )


class ItemValidationOut(BaseModel):
    passed: int = 0
    fixed_on_retry: int = 0
    dropped: int = 0


class PublishedTopicOut(BaseModel):
    run_id: UUID
    topic_id: UUID
    lesson_version_id: UUID
    quiz_version_id: UUID
    item_count: int

    @classmethod
    def from_domain(cls, published: PublishedTopic) -> PublishedTopicOut:
        return cls(
            run_id=published.run_id,
            topic_id=published.topic_id,
            lesson_version_id=published.lesson_version_id,
            quiz_version_id=published.quiz_version_id,
            item_count=published.item_count,
        )


class RejectItemsIn(BaseModel):
    question_ids: list[UUID] = Field(min_length=1)


class GenerationRunOut(BaseModel):
    id: UUID
    topic_id: UUID
    title: str
    phase: RunPhase
    target_item_count: int
    submitted_by_user_id: UUID
    intake_version_id: UUID | None
    failure_reason: str | None
    outline: OutlineOut | None
    draft_lesson_markdown: str | None = None
    published_lesson_version_id: UUID | None = None
    published_quiz_version_id: UUID | None = None
    qa_items: list[QaItemOut] = Field(default_factory=list)
    jobs: list[GenerationJobOut]
    item_validation: ItemValidationOut | None = None
    created_at: datetime

    @classmethod
    def from_domain(
        cls,
        run: GenerationRun,
        jobs: list[tuple[str, str]] | tuple[tuple[str, str], ...] = (),
        qa_items: list[QaItem] | tuple[QaItem, ...] = (),
        item_validation: ItemValidationOut | None = None,
    ) -> GenerationRunOut:
        outline = None
        if run.outline is not None:
            outline = OutlineOut(
                target_item_count=run.outline.target_item_count.value,
                nodes=[OutlineNodeOut.from_domain(node) for node in run.outline.nodes],
            )
        return cls(
            id=run.id,
            topic_id=run.topic_id,
            title=run.title,
            phase=run.phase,
            target_item_count=run.target_item_count.value,
            submitted_by_user_id=run.submitted_by_user_id,
            intake_version_id=run.intake_version_id,
            failure_reason=run.failure_reason,
            outline=outline,
            draft_lesson_markdown=run.draft_lesson_markdown,
            published_lesson_version_id=run.published_lesson_version_id,
            published_quiz_version_id=run.published_quiz_version_id,
            qa_items=[QaItemOut.from_domain(item) for item in qa_items],
            jobs=[GenerationJobOut(kind=kind, status=status) for kind, status in jobs],
            item_validation=item_validation,
            created_at=run.created_at,
        )


class OutlineNodeEditIn(BaseModel):
    id: UUID
    parent_id: UUID | None = None
    slug: str
    title: str
    weight: Decimal
    matched_subtopic_id: UUID | None = None
    force_create: bool = False
    proposed_outcomes: list[str] = Field(default_factory=list)
    sequence: int


class OutlinePatchIn(BaseModel):
    target_item_count: int | None = Field(default=None, ge=60, le=80)
    nodes: list[OutlineNodeEditIn]

    def to_domain(self) -> OutlinePatch:
        count = None if self.target_item_count is None else ItemCount(self.target_item_count)
        return OutlinePatch(
            target_item_count=count,
            nodes=tuple(
                OutlineNodeEdit(
                    id=node.id,
                    parent_id=node.parent_id,
                    slug=node.slug,
                    title=node.title,
                    weight=node.weight,
                    matched_subtopic_id=node.matched_subtopic_id,
                    force_create=node.force_create,
                    proposed_outcomes=tuple(node.proposed_outcomes),
                    sequence=node.sequence,
                )
                for node in self.nodes
            ),
        )


class OutlineNodeTargetIn(BaseModel):
    kind: Literal["outline_node"]
    node_key: UUID


class OutlineDocumentTargetIn(BaseModel):
    kind: Literal["outline_document"]


class LessonSectionTargetIn(BaseModel):
    kind: Literal["lesson_section"]
    section_key: UUID


class QuizItemTargetIn(BaseModel):
    kind: Literal["quiz_item"]
    item_key: UUID


class DraftChangeRequestIn(BaseModel):
    target: Annotated[
        OutlineNodeTargetIn | OutlineDocumentTargetIn | LessonSectionTargetIn | QuizItemTargetIn,
        Field(discriminator="kind"),
    ]
    field: str
    kind: ChangeKind
    comment: str

    def to_domain(self) -> DraftChangeRequest:
        if self.target.kind == "outline_document":
            return DraftOutlineRequest(
                target=OutlineDocumentTarget(kind="outline_document"),
                field=OutlineField(self.field),
                kind=self.kind,
                comment=self.comment,
            )
        if self.target.kind == "outline_node":
            return DraftOutlineRequest(
                target=OutlineNodeTarget(kind="outline_node", node_key=self.target.node_key),
                field=OutlineField(self.field),
                kind=self.kind,
                comment=self.comment,
            )
        if self.target.kind == "lesson_section":
            return DraftLessonRequest(
                target=LessonSectionTarget(
                    kind="lesson_section", section_key=self.target.section_key
                ),
                field=LessonField(self.field),
                kind=self.kind,
                comment=self.comment,
            )
        return DraftQuizRequest(
            target=QuizItemTarget(kind="quiz_item", item_key=self.target.item_key),
            field=QuizField(self.field),
            kind=self.kind,
            comment=self.comment,
        )


class DraftChangeRequestOut(BaseModel):
    target: Annotated[
        OutlineNodeTargetIn | OutlineDocumentTargetIn | LessonSectionTargetIn | QuizItemTargetIn,
        Field(discriminator="kind"),
    ]
    field: str
    kind: ChangeKind
    comment: str

    @classmethod
    def from_domain(cls, draft: DraftChangeRequest) -> DraftChangeRequestOut:
        target = draft.target
        if target.kind == "outline_document":
            parsed: (
                OutlineNodeTargetIn
                | OutlineDocumentTargetIn
                | LessonSectionTargetIn
                | QuizItemTargetIn
            ) = OutlineDocumentTargetIn(kind="outline_document")
        elif target.kind == "outline_node":
            parsed = OutlineNodeTargetIn(kind="outline_node", node_key=target.node_key)
        elif target.kind == "lesson_section":
            parsed = LessonSectionTargetIn(kind="lesson_section", section_key=target.section_key)
        else:
            parsed = QuizItemTargetIn(kind="quiz_item", item_key=target.item_key)
        return cls(target=parsed, field=draft.field.value, kind=draft.kind, comment=draft.comment)


class GenerationAssistantTurnIn(BaseModel):
    revision_id: UUID
    message: str = Field(min_length=1, max_length=8000)
    target: (
        OutlineNodeTargetIn
        | OutlineDocumentTargetIn
        | LessonSectionTargetIn
        | QuizItemTargetIn
        | None
    ) = None

    def target_domain(self) -> ReviewTarget | None:
        if self.target is None:
            return None
        if self.target.kind == "outline_document":
            return OutlineDocumentTarget(kind="outline_document")
        if self.target.kind == "outline_node":
            return OutlineNodeTarget(kind="outline_node", node_key=self.target.node_key)
        if self.target.kind == "lesson_section":
            return LessonSectionTarget(kind="lesson_section", section_key=self.target.section_key)
        return QuizItemTarget(kind="quiz_item", item_key=self.target.item_key)


class GenerationAssistantCitationOut(BaseModel):
    id: str
    label: str
    excerpt: str


class GenerationAssistantReplyOut(BaseModel):
    content: str
    citations: list[GenerationAssistantCitationOut]
    draft_change_request: DraftChangeRequestOut | None


class SubmitApprovalIn(BaseModel):
    revision_id: UUID
    verdict: Literal["approve"]
    snapshot_hash: str | None = None

    def to_domain(self) -> SubmitApproval:
        return SubmitApproval(
            revision_id=self.revision_id,
            verdict="approve",
            snapshot_hash=self.snapshot_hash,
        )


class SubmitChangesRequestedIn(BaseModel):
    revision_id: UUID
    verdict: Literal["changes_requested"]
    snapshot_hash: str | None = None
    requests: list[DraftChangeRequestIn] = Field(min_length=1)

    def to_domain(self) -> SubmitChangesRequested:
        return SubmitChangesRequested(
            revision_id=self.revision_id,
            verdict="changes_requested",
            snapshot_hash=self.snapshot_hash,
            requests=tuple(item.to_domain() for item in self.requests),
        )


TeacherDecisionIn = Annotated[
    SubmitApprovalIn | SubmitChangesRequestedIn, Field(discriminator="verdict")
]


class OutlineNodeSnapshotIn(BaseModel):
    node_key: UUID
    parent_node_key: UUID | None = None
    slug: str
    title: str
    token_mass: int
    prerequisite_score: Decimal
    centrality: Decimal
    weight: Decimal
    matched_subtopic_id: UUID | None = None
    force_create: bool = False
    proposed_outcomes: list[str] = Field(default_factory=list)
    sequence: int


class OutlineSnapshotIn(BaseModel):
    target_item_count: int = Field(ge=60, le=80)
    nodes: list[OutlineNodeSnapshotIn] = Field(min_length=1)

    def to_domain(self) -> OutlineSnapshot:
        return snapshot_from_json(
            {
                "target_item_count": self.target_item_count,
                "nodes": [
                    {
                        "node_key": str(node.node_key),
                        "parent_node_key": (
                            None if node.parent_node_key is None else str(node.parent_node_key)
                        ),
                        "slug": node.slug,
                        "title": node.title,
                        "token_mass": node.token_mass,
                        "prerequisite_score": str(node.prerequisite_score),
                        "centrality": str(node.centrality),
                        "weight": str(node.weight),
                        "matched_subtopic_id": (
                            None
                            if node.matched_subtopic_id is None
                            else str(node.matched_subtopic_id)
                        ),
                        "force_create": node.force_create,
                        "proposed_outcomes": node.proposed_outcomes,
                        "sequence": node.sequence,
                    }
                    for node in self.nodes
                ],
            }
        )


class RewriteCurrentIn(BaseModel):
    revision_id: UUID
    action: Literal["rewrite"]
    snapshot_hash: str | None = None

    def to_domain(self) -> RewriteCurrent:
        return RewriteCurrent(
            revision_id=self.revision_id, action="rewrite", snapshot_hash=self.snapshot_hash
        )


class AcceptCurrentIn(BaseModel):
    revision_id: UUID
    action: Literal["accept_current"]
    snapshot_hash: str | None = None

    def to_domain(self) -> AcceptCurrent:
        return AcceptCurrent(
            revision_id=self.revision_id,
            action="accept_current",
            snapshot_hash=self.snapshot_hash,
        )


class OverrideAndAcceptIn(BaseModel):
    revision_id: UUID
    action: Literal["override_and_accept"]
    replacement: OutlineSnapshotIn
    rationale: str
    snapshot_hash: str | None = None

    def to_domain(self) -> OverrideOutlineAndAccept:
        return OverrideOutlineAndAccept(
            revision_id=self.revision_id,
            action="override_and_accept",
            replacement=self.replacement.to_domain(),
            rationale=self.rationale,
            snapshot_hash=self.snapshot_hash,
        )


class DiscardRunIn(BaseModel):
    revision_id: UUID
    action: Literal["discard"]
    rationale: str | None = None
    snapshot_hash: str | None = None

    def to_domain(self) -> DiscardRun:
        return DiscardRun(
            revision_id=self.revision_id,
            action="discard",
            rationale=self.rationale,
            snapshot_hash=self.snapshot_hash,
        )


CloseRoundIn = Annotated[
    RewriteCurrentIn | AcceptCurrentIn | OverrideAndAcceptIn | DiscardRunIn,
    Field(discriminator="action"),
]


class ActorStampOut(BaseModel):
    user_id: UUID | None = None
    display_name: str
    job_id: UUID | None = None
    model: str | None = None
    occurred_at: datetime

    @classmethod
    def from_domain(cls, stamp: HumanActorStamp | WorkerActorStamp) -> ActorStampOut:
        if isinstance(stamp, HumanActorStamp):
            return cls(
                user_id=stamp.user_id,
                display_name=stamp.display_name,
                occurred_at=stamp.occurred_at,
            )
        if isinstance(stamp, WorkerActorStamp):
            return cls(
                display_name=stamp.model or "worker",
                job_id=stamp.job_id,
                model=stamp.model,
                occurred_at=stamp.occurred_at,
            )
        raise TypeError("unsupported revision author")


class OutlineNodeSnapshotOut(BaseModel):
    node_key: UUID
    parent_node_key: UUID | None
    slug: str
    title: str
    token_mass: int
    prerequisite_score: Decimal
    centrality: Decimal
    weight: Decimal
    matched_subtopic_id: UUID | None
    force_create: bool
    proposed_outcomes: list[str]
    sequence: int
    quota: int = 0

    @classmethod
    def from_domain(cls, node: OutlineNodeSnapshot, *, quota: int = 0) -> OutlineNodeSnapshotOut:
        return cls(
            node_key=node.node_key,
            parent_node_key=node.parent_node_key,
            slug=node.slug,
            title=node.title,
            token_mass=node.token_mass,
            prerequisite_score=node.prerequisite_score,
            centrality=node.centrality,
            weight=node.weight,
            matched_subtopic_id=node.matched_subtopic_id,
            force_create=node.force_create,
            proposed_outcomes=[item.statement for item in node.proposed_outcomes],
            sequence=node.sequence,
            quota=quota,
        )


class OutlineSnapshotOut(BaseModel):
    target_item_count: int
    nodes: list[OutlineNodeSnapshotOut]

    @classmethod
    def from_domain(cls, snapshot: OutlineSnapshot) -> OutlineSnapshotOut:
        quotas = preview_quotas(
            tuple(node.weight for node in snapshot.nodes),
            snapshot.target_item_count.value,
        )
        return cls(
            target_item_count=snapshot.target_item_count.value,
            nodes=[
                OutlineNodeSnapshotOut.from_domain(node, quota=quota)
                for node, quota in zip(snapshot.nodes, quotas, strict=True)
            ],
        )


class OutlineRevisionOut(BaseModel):
    stage: Literal["outline"] = "outline"
    id: UUID
    run_id: UUID
    number: int
    parent_id: UUID | None
    origin: str
    snapshot_hash: str
    created_at: datetime
    created_by: ActorStampOut
    snapshot: OutlineSnapshotOut

    @classmethod
    def from_domain(cls, revision: OutlineRevision) -> OutlineRevisionOut:
        return cls(
            stage="outline",
            id=revision.id,
            run_id=revision.run_id,
            number=revision.number.value,
            parent_id=revision.parent_id,
            origin=revision.origin.value,
            snapshot_hash=revision.snapshot_hash,
            created_at=revision.created_at,
            created_by=ActorStampOut.from_domain(revision.created_by),
            snapshot=OutlineSnapshotOut.from_domain(revision.snapshot),
        )


class LessonSectionSnapshotOut(BaseModel):
    section_key: UUID
    heading: str
    markdown: str
    sequence: int

    @classmethod
    def from_domain(cls, section: LessonSectionSnapshot) -> LessonSectionSnapshotOut:
        return cls(
            section_key=section.section_key,
            heading=section.heading,
            markdown=section.markdown,
            sequence=section.sequence,
        )


class QuizItemSnapshotOut(BaseModel):
    item_key: UUID
    question_id: UUID
    question_version_id: UUID
    subtopic_id: UUID
    prompt: str
    options: dict[str, str]
    correct_label: str
    correct_rationale: str
    distractor_rationales: dict[str, str]
    sequence: int

    @classmethod
    def from_domain(cls, item: QuizItemSnapshot) -> QuizItemSnapshotOut:
        return cls(
            item_key=item.item_key,
            question_id=item.question_id,
            question_version_id=item.question_version_id,
            subtopic_id=item.subtopic_id,
            prompt=item.prompt,
            options={option.label: option.text for option in item.options},
            correct_label=item.answer_key.correct_label,
            correct_rationale=item.answer_key.correct_rationale,
            distractor_rationales={
                label: text for label, text in item.answer_key.distractor_rationales
            },
            sequence=item.sequence,
        )


class ContentSnapshotOut(BaseModel):
    rendered_lesson_markdown: str
    lesson_sections: list[LessonSectionSnapshotOut]
    quiz_version_id: UUID
    quiz_items: list[QuizItemSnapshotOut]


class ContentRevisionOut(BaseModel):
    stage: Literal["qa"] = "qa"
    id: UUID
    run_id: UUID
    number: int
    parent_id: UUID | None
    origin: str
    snapshot_hash: str
    created_at: datetime
    created_by: ActorStampOut
    snapshot: ContentSnapshotOut

    @classmethod
    def from_domain(cls, revision: ContentRevision) -> ContentRevisionOut:
        return cls(
            stage="qa",
            id=revision.id,
            run_id=revision.run_id,
            number=revision.number.value,
            parent_id=revision.parent_id,
            origin=revision.origin.value,
            snapshot_hash=revision.snapshot_hash,
            created_at=revision.created_at,
            created_by=ActorStampOut.from_domain(revision.created_by),
            snapshot=ContentSnapshotOut(
                rendered_lesson_markdown=revision.snapshot.rendered_lesson_markdown,
                lesson_sections=[
                    LessonSectionSnapshotOut.from_domain(section)
                    for section in revision.snapshot.lesson_sections
                ],
                quiz_version_id=revision.snapshot.quiz_version_id,
                quiz_items=[
                    QuizItemSnapshotOut.from_domain(item) for item in revision.snapshot.quiz_items
                ],
            ),
        )


FrozenRevisionOut = Annotated[OutlineRevisionOut | ContentRevisionOut, Field(discriminator="stage")]


class OutlineNodeDeltaOut(BaseModel):
    node_key: UUID
    before: OutlineNodeSnapshotOut | None
    after: OutlineNodeSnapshotOut | None


class LessonSectionDeltaOut(BaseModel):
    section_key: UUID
    before: LessonSectionSnapshotOut | None
    after: LessonSectionSnapshotOut | None


class QuizItemDeltaOut(BaseModel):
    item_key: UUID
    before: QuizItemSnapshotOut | None
    after: QuizItemSnapshotOut | None


class ContentRevisionDiffOut(BaseModel):
    kind: Literal["content"]
    from_revision_id: UUID
    to_revision_id: UUID
    sections: list[LessonSectionDeltaOut]
    quiz_items: list[QuizItemDeltaOut]


class OutlineRevisionDiffOut(BaseModel):
    kind: Literal["outline"]
    from_revision_id: UUID
    to_revision_id: UUID
    target_item_count: dict[str, int | None]
    nodes: list[OutlineNodeDeltaOut]


class ReviewRoundOut(BaseModel):
    id: UUID
    run_id: UUID
    revision_id: UUID
    stage: str
    number: int
    opened_at: datetime
    due_at: datetime
    sealed_at: datetime | None

    @classmethod
    def from_domain(cls, round: ReviewRound) -> ReviewRoundOut:
        return cls(
            id=round.id,
            run_id=round.run_id,
            revision_id=round.revision_id,
            stage=round.stage.value,
            number=round.number.value,
            opened_at=round.opened_at,
            due_at=round.due_at,
            sealed_at=round.sealed_at,
        )


class ChangeRequestOut(BaseModel):
    id: UUID
    decision_id: UUID
    revision_id: UUID
    author: ActorStampOut
    target_kind: str
    node_key: UUID | None = None
    section_key: UUID | None = None
    item_key: UUID | None = None
    field: str
    kind: str
    comment: str

    @classmethod
    def from_domain(cls, request: ChangeRequest) -> ChangeRequestOut:
        target = request.request.target
        node_key = None
        section_key = None
        item_key = None
        if target.kind == "outline_node":
            node_key = target.node_key
        elif target.kind == "lesson_section":
            section_key = target.section_key
        elif target.kind == "quiz_item":
            item_key = target.item_key
        elif target.kind != "outline_document":
            raise TypeError(f"unsupported change-request target: {target.kind}")
        return cls(
            id=request.id,
            decision_id=request.decision_id,
            revision_id=request.revision_id,
            author=ActorStampOut.from_domain(request.author),
            target_kind=target.kind,
            node_key=node_key,
            section_key=section_key,
            item_key=item_key,
            field=request.request.field.value,
            kind=request.request.kind.value,
            comment=request.request.comment,
        )


class TeacherDecisionOut(BaseModel):
    id: UUID
    round_id: UUID
    revision_id: UUID
    verdict: str
    reviewer: ActorStampOut
    requests: list[ChangeRequestOut] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, decision: TeacherDecision) -> TeacherDecisionOut:
        requests: list[ChangeRequestOut] = []
        if decision.verdict == "changes_requested":
            requests = [ChangeRequestOut.from_domain(item) for item in decision.requests]
        return cls(
            id=decision.id,
            round_id=decision.round_id,
            revision_id=decision.revision_id,
            verdict=decision.verdict,
            reviewer=ActorStampOut.from_domain(decision.reviewer),
            requests=requests,
        )


class ReviewerStateOut(BaseModel):
    reviewer_user_id: UUID
    display_name: str
    state: str
    decision: TeacherDecisionOut | None

    @classmethod
    def from_domain(cls, state: ReviewerState) -> ReviewerStateOut:
        return cls(
            reviewer_user_id=state.participant.reviewer_user_id,
            display_name=state.participant.display_name,
            state=state.state.value,
            decision=None
            if state.decision is None
            else TeacherDecisionOut.from_domain(state.decision),
        )


class RequestThreadOut(BaseModel):
    change_request: ChangeRequestOut
    target_title: str | None

    @classmethod
    def from_domain(cls, thread: RequestAgainstTarget) -> RequestThreadOut:
        return cls(
            change_request=ChangeRequestOut.from_domain(thread.change_request),
            target_title=thread.target_title,
        )


class RoundCloseRecordOut(BaseModel):
    id: UUID
    round_id: UUID
    base_revision_id: UUID
    action: str
    actor: ActorStampOut
    collated_request_ids: list[UUID]
    rationale: str | None

    @classmethod
    def from_domain(cls, record: RoundCloseRecord) -> RoundCloseRecordOut:
        return cls(
            id=record.id,
            round_id=record.round_id,
            base_revision_id=record.base_revision_id,
            action=record.action.value,
            actor=ActorStampOut.from_domain(record.actor),
            collated_request_ids=list(record.collated_request_ids),
            rationale=record.rationale,
        )


class ReviewWorkspaceOut(BaseModel):
    run_id: UUID
    topic_id: UUID
    phase: RunPhase
    published_locked: bool
    viewer_is_closer: bool
    active_revision: FrozenRevisionOut
    diff_from_parent: OutlineRevisionDiffOut | ContentRevisionDiffOut | None
    open_round: ReviewRoundOut | None
    reviewer_states: list[ReviewerStateOut]
    request_threads: list[RequestThreadOut]
    history: list[RoundCloseRecordOut]

    @classmethod
    def from_domain(cls, workspace: ReviewWorkspace) -> ReviewWorkspaceOut:
        return cls(
            run_id=workspace.run_id,
            topic_id=workspace.topic_id,
            phase=workspace.phase,
            published_locked=workspace.published_locked,
            viewer_is_closer=workspace.viewer_is_closer,
            active_revision=_frozen_revision_out(workspace.active_revision),
            diff_from_parent=_diff_out(workspace.diff_from_parent),
            open_round=(
                None
                if workspace.open_round is None
                else ReviewRoundOut.from_domain(workspace.open_round)
            ),
            reviewer_states=[
                ReviewerStateOut.from_domain(item) for item in workspace.reviewer_states
            ],
            request_threads=[
                RequestThreadOut.from_domain(item) for item in workspace.request_threads
            ],
            history=[RoundCloseRecordOut.from_domain(item) for item in workspace.history],
        )


class CloseRoundResultOut(BaseModel):
    kind: str
    job_id: UUID | None = None
    run_id: UUID | None = None
    accepted_revision_id: UUID | None = None
    close_record: RoundCloseRecordOut | None = None

    @classmethod
    def from_domain(cls, result: CloseRoundResult) -> CloseRoundResultOut:
        if result.kind == "rewrite_queued":
            queued: RewriteQueued = result
            return cls(
                kind=queued.kind,
                job_id=queued.job_id,
                close_record=RoundCloseRecordOut.from_domain(queued.close_record),
            )
        if result.kind == "outline_accepted":
            accepted: OutlineAccepted = result
            return cls(
                kind=accepted.kind,
                run_id=accepted.run_id,
                accepted_revision_id=accepted.accepted_revision_id,
            )
        if result.kind == "content_accepted":
            content: ContentAccepted = result
            return cls(
                kind=content.kind,
                run_id=content.run_id,
                accepted_revision_id=content.accepted_revision_id,
            )
        discarded: RunDiscarded = result
        return cls(kind=discarded.kind, run_id=discarded.run_id)


def teacher_decision_command(
    body: SubmitApprovalIn | SubmitChangesRequestedIn,
) -> TeacherDecisionCommand:
    return body.to_domain()


def close_round_command(
    body: RewriteCurrentIn | AcceptCurrentIn | OverrideAndAcceptIn | DiscardRunIn,
) -> CloseRoundCommand:
    return body.to_domain()


def _frozen_revision_out(revision: FrozenRevision) -> OutlineRevisionOut | ContentRevisionOut:
    if isinstance(revision, ContentRevision):
        return ContentRevisionOut.from_domain(revision)
    return OutlineRevisionOut.from_domain(revision)


def _diff_out(
    diff: OutlineRevisionDiff | ContentRevisionDiff | None,
) -> OutlineRevisionDiffOut | ContentRevisionDiffOut | None:
    if diff is None:
        return None
    if diff.kind == "content":
        return ContentRevisionDiffOut(
            kind="content",
            from_revision_id=diff.from_revision_id,
            to_revision_id=diff.to_revision_id,
            sections=[
                LessonSectionDeltaOut(
                    section_key=section.section_key,
                    before=(
                        None
                        if section.before is None
                        else LessonSectionSnapshotOut.from_domain(section.before)
                    ),
                    after=(
                        None
                        if section.after is None
                        else LessonSectionSnapshotOut.from_domain(section.after)
                    ),
                )
                for section in diff.sections
            ],
            quiz_items=[
                QuizItemDeltaOut(
                    item_key=item.item_key,
                    before=(
                        None
                        if item.before is None
                        else QuizItemSnapshotOut.from_domain(item.before)
                    ),
                    after=(
                        None if item.after is None else QuizItemSnapshotOut.from_domain(item.after)
                    ),
                )
                for item in diff.quiz_items
            ],
        )
    return OutlineRevisionDiffOut(
        kind="outline",
        from_revision_id=diff.from_revision_id,
        to_revision_id=diff.to_revision_id,
        target_item_count={
            "before": diff.target_item_count.before,
            "after": diff.target_item_count.after,
        },
        nodes=[
            OutlineNodeDeltaOut(
                node_key=node.node_key,
                before=(
                    None if node.before is None else OutlineNodeSnapshotOut.from_domain(node.before)
                ),
                after=(
                    None if node.after is None else OutlineNodeSnapshotOut.from_domain(node.after)
                ),
            )
            for node in diff.nodes
        ],
    )
