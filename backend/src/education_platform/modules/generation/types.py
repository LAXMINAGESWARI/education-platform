"""Caller-facing generation-run types. Persistence lives in generation.models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from education_platform.core.errors import DomainError


class RunPhase(StrEnum):
    INDEXING = "indexing"
    OUTLINING = "outlining"
    OUTLINE_REVIEW = "outline_review"
    GENERATING = "generating"  # items + lesson jobs; qa_review only after both succeed
    QA_REVIEW = "qa_review"
    PUBLISHED = "published"
    FAILED = "failed"
    DISCARDED = "discarded"


IN_FLIGHT_PHASES = frozenset(
    {
        RunPhase.INDEXING,
        RunPhase.OUTLINING,
        RunPhase.OUTLINE_REVIEW,
        RunPhase.GENERATING,
        RunPhase.QA_REVIEW,
    }
)


class GenerationJobKind(StrEnum):
    OUTLINE = "outline"
    ITEMS = "items"
    LESSON = "lesson"
    REGENERATE_ITEMS = "regenerate_items"
    REWRITE_OUTLINE = "rewrite_outline"
    REWRITE_CONTENT = "rewrite_content"


class GenerationJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class BloomLevel(StrEnum):
    REMEMBER = "remember"
    UNDERSTAND = "understand"
    APPLY = "apply"
    ANALYZE = "analyze"


@dataclass(frozen=True, slots=True)
class CurriculumJob:
    """Admin ADK generate–review job. Distinct from topic-run generation_jobs."""

    id: UUID
    subtopic_id: UUID
    status: GenerationJobStatus
    round_count: int
    reviewer_notes: str | None
    error: str | None
    source_material_version_id: UUID | None
    quiz_version_id: UUID | None


@dataclass(frozen=True, slots=True)
class ItemCount:
    """Bank size. Constructor is the clamp."""

    value: int

    def __post_init__(self) -> None:
        if not 60 <= self.value <= 80:
            raise ValueError("target_item_count must be between 60 and 80")

    @classmethod
    def parse(cls, raw: int | None) -> ItemCount:
        return cls(80 if raw is None else int(raw))


@dataclass(frozen=True, slots=True)
class ProposedOutcome:
    statement: str

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("outcome statement must be non-empty")


@dataclass(frozen=True, slots=True)
class OutlineNode:
    """Run-local DAG node. Not a Subtopic. accepted_subtopic_id is None until accept."""

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
    proposed_outcomes: tuple[ProposedOutcome, ...]
    sequence: int
    source_headings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise ValueError("weight must be >= 0")
        if self.quota is not None and self.quota < 0:
            raise ValueError("quota must be >= 0")
        if self.force_create and self.matched_subtopic_id is not None:
            raise ValueError("force_create cannot carry a matched_subtopic_id")
        if len(self.proposed_outcomes) > 3:
            raise ValueError("at most 3 proposed outcomes per node")


@dataclass(frozen=True, slots=True)
class OutlineDocument:
    """HITL 1 document. The live node list is the spec; PATCH replaces it."""

    target_item_count: ItemCount
    nodes: tuple[OutlineNode, ...]

    def normalized_weights(self) -> tuple[Decimal, ...]:
        if not self.nodes:
            return ()
        total = sum((node.weight for node in self.nodes), Decimal("0"))
        if total == 0:
            raise ValueError("all-zero weights")
        return tuple(node.weight / total for node in self.nodes)


@dataclass(frozen=True, slots=True)
class OutlineNodeEdit:
    id: UUID
    parent_id: UUID | None
    slug: str
    title: str
    weight: Decimal
    matched_subtopic_id: UUID | None
    force_create: bool
    proposed_outcomes: tuple[str, ...]
    sequence: int


@dataclass(frozen=True, slots=True)
class OutlinePatch:
    """Caller-supplied replacement. Node ids must already belong to the run."""

    target_item_count: ItemCount | None
    nodes: tuple[OutlineNodeEdit, ...]


@dataclass(frozen=True, slots=True)
class GenerationRun:
    id: UUID
    topic_id: UUID
    title: str
    phase: RunPhase
    target_item_count: ItemCount
    submitted_by_user_id: UUID
    intake_version_id: UUID | None
    failure_reason: str | None
    outline: OutlineDocument | None
    draft_lesson_markdown: str | None
    published_lesson_version_id: UUID | None
    published_quiz_version_id: UUID | None
    created_at: datetime

    def __post_init__(self) -> None:
        if self.phase is RunPhase.FAILED and not self.failure_reason:
            raise ValueError("failed runs require a reason")
        if self.phase is RunPhase.OUTLINE_REVIEW and (
            self.outline is None or not self.outline.nodes
        ):
            raise ValueError("outline_review requires nodes")
        if self.phase is RunPhase.PUBLISHED and (
            self.published_lesson_version_id is None or self.published_quiz_version_id is None
        ):
            raise ValueError("published runs require lesson and quiz version ids")


@dataclass(frozen=True, slots=True)
class AcceptedRun:
    run_id: UUID
    topic_id: UUID
    phase: RunPhase


@dataclass(frozen=True, slots=True)
class PublishedTopic:
    run_id: UUID
    topic_id: UUID
    lesson_version_id: UUID
    quiz_version_id: UUID
    item_count: int


@dataclass(frozen=True, slots=True)
class QaItem:
    """Teacher QA row. Never serialized onto student routes."""

    question_id: UUID
    question_version_id: UUID
    prompt: str
    options: tuple[tuple[str, str], ...]
    correct_label: str
    correct_rationale: str
    distractor_rationales: dict[str, str]
    subtopic_id: UUID
    sequence: int
    bloom: BloomLevel | None = None
    misconception_labels: tuple[str, ...] = ()
    blind_solve: dict[str, object] | None = None
    check_attempts: int | None = None
    check_verdict: str | None = None
    difficulty: str | None = None
    subtopic_title: str | None = None
    judge_needs_review: bool = False


@dataclass(frozen=True, slots=True)
class HeadingCluster:
    """Pure input to outline: a heading, its token mass, and its parent heading.

    ``source_headings`` are the original chunk-group labels this cluster owns.
    Empty means the cluster heading itself (and its parent, when it has one).
    """

    heading: str
    token_mass: int
    sample_texts: tuple[str, ...]
    parent_heading: str | None = None
    source_headings: tuple[str, ...] = ()
    title_hint: str | None = None
    # Original parent heading when the container was folded into this child.
    scope_parent: str | None = None


@dataclass(frozen=True, slots=True)
class ProposedOutlineNode:
    key: str
    parent_key: str | None
    slug: str
    title: str
    token_mass: int
    prerequisite_score: Decimal
    centrality: Decimal
    proposed_outcomes: tuple[str, ...]
    source_headings: tuple[str, ...] = ()
    parent_title: str | None = None


@dataclass(frozen=True, slots=True)
class ProposedOutline:
    """Pure LLM/heuristic result. Not rows. Worker persists it onto the run."""

    nodes: tuple[ProposedOutlineNode, ...]


class GenerationError(DomainError):
    """Safe to show a teacher. 403 scope, 404 missing, 409 illegal phase / in-flight."""

    def __init__(self, detail: str, status_code: int = 403) -> None:
        super().__init__(detail, status_code=status_code)


ROUND_DURATION = timedelta(days=3)

PATCH_OUTLINE_GONE = (
    "Live outline edits are gone. Teachers approve or request changes on a frozen "
    "revision; an administrator closes the round."
)
TEACHER_UPLOAD_GONE = "Teachers do not upload source PDFs. An administrator submits the topic PDF."
SUBTOPIC_GENERATION_GONE = (
    "Subtopic-level one-shot generation is retired. An administrator submits the topic PDF "
    "via /admin/topics/{topic_id}/generation-runs for full outline and QA review."
)


class ReviewStage(StrEnum):
    OUTLINE = "outline"
    QA = "qa"


class RevisionOrigin(StrEnum):
    INITIAL_GENERATION = "initial_generation"
    COLLATED_REWRITE = "collated_rewrite"
    ADMIN_OVERRIDE = "admin_override"


class ChangeKind(StrEnum):
    CURRICULUM_ALIGNMENT = "curriculum_alignment"
    FACTUAL_ACCURACY = "factual_accuracy"
    PEDAGOGY = "pedagogy"
    STRUCTURE = "structure"
    ASSESSMENT_VALIDITY = "assessment_validity"
    ANSWER_KEY = "answer_key"
    ACCESSIBILITY = "accessibility"
    OTHER = "other"


class OutlineField(StrEnum):
    WHOLE_NODE = "whole_node"
    TITLE = "title"
    PARENT = "parent"
    WEIGHT = "weight"
    PROPOSED_OUTCOMES = "proposed_outcomes"
    SUBTOPIC_MATCH = "subtopic_match"
    OUTLINE_STRUCTURE = "outline_structure"


class LessonField(StrEnum):
    WHOLE_SECTION = "whole_section"
    HEADING = "heading"
    BODY = "body"
    ORDER = "order"


class QuizField(StrEnum):
    WHOLE_ITEM = "whole_item"
    PROMPT = "prompt"
    OPTIONS = "options"
    ANSWER_KEY = "answer_key"
    RATIONALES = "rationales"
    ORDER = "order"


class ReviewerStateKind(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    ABSTAINED = "abstained"


class CloseAction(StrEnum):
    REWRITE = "rewrite"
    ACCEPT_CURRENT = "accept_current"
    OVERRIDE_AND_ACCEPT = "override_and_accept"
    DISCARD = "discard"


@dataclass(frozen=True, slots=True)
class RevisionNumber:
    value: int

    def __post_init__(self) -> None:
        if self.value < 1:
            raise ValueError("revision number must be >= 1")


@dataclass(frozen=True, slots=True)
class TeacherRoundNumber:
    """1 or 2. successor() is None on 2 — that is the cap."""

    value: Literal[1, 2]

    def __init__(self, value: int) -> None:
        if value not in (1, 2):
            raise ValueError("teacher rounds are 1 or 2")
        object.__setattr__(self, "value", value)

    def successor(self) -> TeacherRoundNumber | None:
        return TeacherRoundNumber(2) if self.value == 1 else None


@dataclass(frozen=True, slots=True)
class HumanActorStamp:
    user_id: UUID
    display_name: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class WorkerActorStamp:
    job_id: UUID
    model: str
    occurred_at: datetime


RevisionAuthor = HumanActorStamp | WorkerActorStamp


@dataclass(frozen=True, slots=True)
class ProposedOutcomeSnapshot:
    statement: str

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("outcome statement must be non-empty")


@dataclass(frozen=True, slots=True)
class OutlineNodeSnapshot:
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
    proposed_outcomes: tuple[ProposedOutcomeSnapshot, ...]
    sequence: int
    source_headings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise ValueError("weight must be >= 0")
        if self.force_create and self.matched_subtopic_id is not None:
            raise ValueError("force_create cannot carry a matched_subtopic_id")
        if len(self.proposed_outcomes) > 3:
            raise ValueError("at most 3 proposed outcomes per node")
        if self.parent_node_key == self.node_key:
            raise ValueError("outline node cannot parent itself")


@dataclass(frozen=True, slots=True)
class OutlineSnapshot:
    target_item_count: ItemCount
    nodes: tuple[OutlineNodeSnapshot, ...]

    def __post_init__(self) -> None:
        if not self.nodes:
            raise ValueError("outline snapshot requires nodes")
        keys = [node.node_key for node in self.nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("outline node keys must be unique")
        slugs = [node.slug for node in self.nodes]
        if len(slugs) != len(set(slugs)):
            raise ValueError("outline slugs must be unique")
        keyset = set(keys)
        parent_of = {node.node_key: node.parent_node_key for node in self.nodes}
        for node in self.nodes:
            if node.parent_node_key is not None and node.parent_node_key not in keyset:
                raise ValueError("unknown parent_node_key")
        for start in keys:
            seen: set[UUID] = set()
            current: UUID | None = start
            while current is not None:
                if current in seen:
                    raise ValueError("outline DAG has a cycle")
                seen.add(current)
                current = parent_of.get(current)


@dataclass(frozen=True, slots=True)
class OutlineRevision:
    id: UUID
    run_id: UUID
    number: RevisionNumber
    parent_id: UUID | None
    origin: RevisionOrigin
    created_by: RevisionAuthor
    source_round_id: UUID | None
    snapshot_hash: str
    snapshot: OutlineSnapshot
    created_at: datetime
    stage: Literal["outline"] = "outline"


@dataclass(frozen=True, slots=True)
class LessonSectionSnapshot:
    section_key: UUID
    heading: str
    markdown: str
    sequence: int

    def __post_init__(self) -> None:
        if not self.heading.strip():
            raise ValueError("lesson section heading must be non-empty")
        if not self.markdown.strip():
            raise ValueError("lesson section markdown must be non-empty")


@dataclass(frozen=True, slots=True)
class QuizOptionSnapshot:
    label: str
    text: str
    sequence: int


@dataclass(frozen=True, slots=True)
class QuizAnswerKeySnapshot:
    correct_label: str
    correct_rationale: str
    distractor_rationales: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class QuizItemSnapshot:
    item_key: UUID
    question_id: UUID
    question_version_id: UUID
    subtopic_id: UUID
    prompt: str
    options: tuple[QuizOptionSnapshot, ...]
    answer_key: QuizAnswerKeySnapshot
    sequence: int


@dataclass(frozen=True, slots=True)
class ContentSnapshot:
    rendered_lesson_markdown: str
    lesson_sections: tuple[LessonSectionSnapshot, ...]
    quiz_version_id: UUID
    quiz_items: tuple[QuizItemSnapshot, ...]

    def __post_init__(self) -> None:
        if not self.rendered_lesson_markdown.strip():
            raise ValueError("content snapshot requires rendered lesson markdown")
        if not self.lesson_sections:
            raise ValueError("content snapshot requires lesson sections")
        if not self.quiz_items:
            raise ValueError("content snapshot requires quiz items")
        section_keys = [item.section_key for item in self.lesson_sections]
        if len(section_keys) != len(set(section_keys)):
            raise ValueError("lesson section keys must be unique")
        item_keys = [item.item_key for item in self.quiz_items]
        if len(item_keys) != len(set(item_keys)):
            raise ValueError("quiz item keys must be unique")
        sequences = [item.sequence for item in self.lesson_sections]
        if sequences != sorted(sequences):
            raise ValueError("lesson sections must be ordered by sequence")
        item_sequences = [item.sequence for item in self.quiz_items]
        if item_sequences != sorted(item_sequences):
            raise ValueError("quiz items must be ordered by sequence")


@dataclass(frozen=True, slots=True)
class ContentRevision:
    id: UUID
    run_id: UUID
    number: RevisionNumber
    parent_id: UUID | None
    origin: RevisionOrigin
    created_by: RevisionAuthor
    source_round_id: UUID | None
    snapshot_hash: str
    snapshot: ContentSnapshot
    created_at: datetime
    stage: Literal["qa"] = "qa"


FrozenRevision = OutlineRevision | ContentRevision


@dataclass(frozen=True, slots=True)
class OutlineNodeTarget:
    kind: Literal["outline_node"]
    node_key: UUID


@dataclass(frozen=True, slots=True)
class OutlineDocumentTarget:
    kind: Literal["outline_document"]


OutlineReviewTarget = OutlineNodeTarget | OutlineDocumentTarget


@dataclass(frozen=True, slots=True)
class LessonSectionTarget:
    kind: Literal["lesson_section"]
    section_key: UUID


@dataclass(frozen=True, slots=True)
class QuizItemTarget:
    kind: Literal["quiz_item"]
    item_key: UUID


@dataclass(frozen=True, slots=True)
class DraftOutlineRequest:
    target: OutlineReviewTarget
    field: OutlineField
    kind: ChangeKind
    comment: str

    def __post_init__(self) -> None:
        if not self.comment.strip():
            raise ValueError("change request comment must be non-empty")


@dataclass(frozen=True, slots=True)
class DraftLessonRequest:
    target: LessonSectionTarget
    field: LessonField
    kind: ChangeKind
    comment: str

    def __post_init__(self) -> None:
        if not self.comment.strip():
            raise ValueError("change request comment must be non-empty")


@dataclass(frozen=True, slots=True)
class DraftQuizRequest:
    target: QuizItemTarget
    field: QuizField
    kind: ChangeKind
    comment: str

    def __post_init__(self) -> None:
        if not self.comment.strip():
            raise ValueError("change request comment must be non-empty")


DraftChangeRequest = DraftOutlineRequest | DraftLessonRequest | DraftQuizRequest
ReviewTarget = OutlineNodeTarget | OutlineDocumentTarget | LessonSectionTarget | QuizItemTarget


@dataclass(frozen=True, slots=True)
class SubmitApproval:
    revision_id: UUID
    verdict: Literal["approve"]
    snapshot_hash: str | None = None


@dataclass(frozen=True, slots=True)
class SubmitChangesRequested:
    revision_id: UUID
    verdict: Literal["changes_requested"]
    requests: tuple[DraftChangeRequest, ...]
    snapshot_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.requests:
            raise ValueError("changes_requested requires at least one request")


TeacherDecisionCommand = SubmitApproval | SubmitChangesRequested


@dataclass(frozen=True, slots=True)
class ChangeRequest:
    id: UUID
    decision_id: UUID
    revision_id: UUID
    author: HumanActorStamp
    request: DraftChangeRequest


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    id: UUID
    round_id: UUID
    revision_id: UUID
    reviewer: HumanActorStamp
    verdict: Literal["approve"]


@dataclass(frozen=True, slots=True)
class ChangesRequestedDecision:
    id: UUID
    round_id: UUID
    revision_id: UUID
    reviewer: HumanActorStamp
    verdict: Literal["changes_requested"]
    requests: tuple[ChangeRequest, ...]


TeacherDecision = ApprovalDecision | ChangesRequestedDecision


@dataclass(frozen=True, slots=True)
class ReviewParticipant:
    reviewer_user_id: UUID
    display_name: str
    teaching_assignment_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class ReviewRound:
    id: UUID
    run_id: UUID
    revision_id: UUID
    stage: ReviewStage
    number: TeacherRoundNumber
    participants: tuple[ReviewParticipant, ...]
    opened_at: datetime
    due_at: datetime
    sealed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReviewerState:
    participant: ReviewParticipant
    state: ReviewerStateKind
    decision: TeacherDecision | None


@dataclass(frozen=True, slots=True)
class RewriteCurrent:
    revision_id: UUID
    action: Literal["rewrite"]
    snapshot_hash: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptCurrent:
    revision_id: UUID
    action: Literal["accept_current"]
    snapshot_hash: str | None = None


@dataclass(frozen=True, slots=True)
class OverrideOutlineAndAccept:
    revision_id: UUID
    action: Literal["override_and_accept"]
    replacement: OutlineSnapshot
    rationale: str
    snapshot_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError("override requires a rationale")


@dataclass(frozen=True, slots=True)
class DiscardRun:
    revision_id: UUID
    action: Literal["discard"]
    rationale: str | None = None
    snapshot_hash: str | None = None


CloseRoundCommand = RewriteCurrent | AcceptCurrent | OverrideOutlineAndAccept | DiscardRun


@dataclass(frozen=True, slots=True)
class RoundCloseRecord:
    id: UUID
    round_id: UUID
    base_revision_id: UUID
    action: CloseAction
    actor: HumanActorStamp
    collated_request_ids: tuple[UUID, ...]
    rationale: str | None


@dataclass(frozen=True, slots=True)
class RewriteQueued:
    kind: Literal["rewrite_queued"]
    job_id: UUID
    close_record: RoundCloseRecord


@dataclass(frozen=True, slots=True)
class OutlineAccepted:
    kind: Literal["outline_accepted"]
    run_id: UUID
    accepted_revision_id: UUID


@dataclass(frozen=True, slots=True)
class ContentAccepted:
    kind: Literal["content_accepted"]
    run_id: UUID
    accepted_revision_id: UUID


@dataclass(frozen=True, slots=True)
class RunDiscarded:
    kind: Literal["run_discarded"]
    run_id: UUID


CloseRoundResult = RewriteQueued | OutlineAccepted | ContentAccepted | RunDiscarded


@dataclass(frozen=True, slots=True)
class ValueDelta:
    before: int | None
    after: int | None


@dataclass(frozen=True, slots=True)
class OutlineNodeDelta:
    node_key: UUID
    before: OutlineNodeSnapshot | None
    after: OutlineNodeSnapshot | None


@dataclass(frozen=True, slots=True)
class OutlineRevisionDiff:
    kind: Literal["outline"]
    from_revision_id: UUID
    to_revision_id: UUID
    target_item_count: ValueDelta
    nodes: tuple[OutlineNodeDelta, ...]


@dataclass(frozen=True, slots=True)
class LessonSectionDelta:
    section_key: UUID
    before: LessonSectionSnapshot | None
    after: LessonSectionSnapshot | None


@dataclass(frozen=True, slots=True)
class QuizItemDelta:
    item_key: UUID
    before: QuizItemSnapshot | None
    after: QuizItemSnapshot | None


@dataclass(frozen=True, slots=True)
class ContentRevisionDiff:
    kind: Literal["content"]
    from_revision_id: UUID
    to_revision_id: UUID
    sections: tuple[LessonSectionDelta, ...]
    quiz_items: tuple[QuizItemDelta, ...]


RevisionDiff = OutlineRevisionDiff | ContentRevisionDiff


@dataclass(frozen=True, slots=True)
class RequestAgainstTarget:
    change_request: ChangeRequest
    target_title: str | None


@dataclass(frozen=True, slots=True)
class ReviewWorkspace:
    run_id: UUID
    topic_id: UUID
    phase: RunPhase
    published_locked: bool
    viewer_is_closer: bool
    active_revision: FrozenRevision
    diff_from_parent: RevisionDiff | None
    open_round: ReviewRound | None
    reviewer_states: tuple[ReviewerState, ...]
    request_threads: tuple[RequestAgainstTarget, ...]
    history: tuple[RoundCloseRecord, ...]
