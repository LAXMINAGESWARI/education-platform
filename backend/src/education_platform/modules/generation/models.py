"""ORM for generation runs, outline nodes, and generation jobs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from education_platform.db.base import Base, UUIDTimestampMixin
from education_platform.db.types import JsonDict, partial_unique_index, str_enum
from education_platform.modules.generation.types import (
    ChangeKind,
    CloseAction,
    GenerationJobKind,
    GenerationJobStatus,
    ReviewStage,
    RevisionOrigin,
    RunPhase,
)

_IN_FLIGHT_SQL = "phase IN ('indexing', 'outlining', 'outline_review', 'generating', 'qa_review')"


class ContentGenerationRun(UUIDTimestampMixin, Base):
    __tablename__ = "content_generation_runs"
    __table_args__ = (
        CheckConstraint(
            "target_item_count >= 60 AND target_item_count <= 80",
            name="ck_content_generation_runs_target_item_count",
        ),
        partial_unique_index(
            "uq_content_generation_runs_one_in_flight_topic",
            "topic_id",
            where=_IN_FLIGHT_SQL,
        ),
    )

    topic_id: Mapped[UUID] = mapped_column(ForeignKey("topics.id"), index=True)
    submitted_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    phase: Mapped[RunPhase] = mapped_column(
        str_enum(RunPhase, "content_generation_run_phase", length=32),
        index=True,
    )
    target_item_count: Mapped[int] = mapped_column(Integer)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    intake_source_material_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_material_versions.id"), nullable=True, index=True
    )
    draft_quiz_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("quiz_versions.id"), nullable=True
    )
    draft_lesson_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_lesson_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_material_versions.id"), nullable=True
    )
    published_quiz_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("quiz_versions.id"), nullable=True
    )


class ContentGenerationOutlineNode(UUIDTimestampMixin, Base):
    __tablename__ = "content_generation_outline_nodes"
    __table_args__ = (
        UniqueConstraint("run_id", "slug", name="uq_content_generation_outline_nodes_run_id_slug"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_generation_runs.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("content_generation_outline_nodes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    token_mass: Mapped[int] = mapped_column(Integer)
    prerequisite_score: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    centrality: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    weight: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matched_subtopic_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("subtopics.id"), nullable=True, index=True
    )
    force_create: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted_subtopic_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("subtopics.id"), nullable=True, index=True
    )
    proposed_outcomes: Mapped[list[Any]] = mapped_column(JsonDict)
    # Original chunk-group labels. Display ``title`` is the cleaned concept name.
    source_headings: Mapped[list[Any]] = mapped_column(JsonDict, default=list)
    sequence: Mapped[int] = mapped_column(Integer)


class GenerationJob(UUIDTimestampMixin, Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        Index("ix_generation_jobs_status_created_at", "status", "created_at"),
        partial_unique_index(
            "uq_generation_jobs_one_in_flight_outline",
            "run_id",
            "kind",
            where="kind = 'outline' AND status IN ('queued', 'running')",
        ),
        partial_unique_index(
            "uq_generation_jobs_one_in_flight_items",
            "run_id",
            "kind",
            where="kind = 'items' AND status IN ('queued', 'running')",
        ),
        partial_unique_index(
            "uq_generation_jobs_one_in_flight_lesson",
            "run_id",
            "kind",
            where="kind = 'lesson' AND status IN ('queued', 'running')",
        ),
        partial_unique_index(
            "uq_generation_jobs_one_in_flight_rewrite_outline",
            "run_id",
            "kind",
            where="kind = 'rewrite_outline' AND status IN ('queued', 'running')",
        ),
        partial_unique_index(
            "uq_generation_jobs_one_in_flight_rewrite_content",
            "run_id",
            "kind",
            where="kind = 'rewrite_content' AND status IN ('queued', 'running')",
        ),
        partial_unique_index(
            "uq_generation_jobs_close_record_id",
            "close_record_id",
            where="close_record_id IS NOT NULL",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("content_generation_runs.id"), index=True)
    kind: Mapped[GenerationJobKind] = mapped_column(
        str_enum(GenerationJobKind, "generation_job_kind", length=32)
    )
    status: Mapped[GenerationJobStatus] = mapped_column(
        str_enum(GenerationJobStatus, "generation_job_status", length=32)
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    close_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("review_round_closures.id"),
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GenerationRevision(UUIDTimestampMixin, Base):
    __tablename__ = "generation_revisions"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "stage", "number", name="uq_generation_revisions_run_stage_number"
        ),
        UniqueConstraint("parent_revision_id", name="uq_generation_revisions_parent_revision_id"),
        CheckConstraint("number >= 1", name="ck_generation_revisions_number"),
        CheckConstraint(
            "(created_by_user_id IS NOT NULL AND created_by_job_id IS NULL) OR "
            "(created_by_user_id IS NULL AND created_by_job_id IS NOT NULL)",
            name="ck_generation_revisions_one_author",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_generation_runs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[ReviewStage] = mapped_column(
        str_enum(ReviewStage, "generation_revision_stage", length=16)
    )
    number: Mapped[int] = mapped_column(Integer)
    parent_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("generation_revisions.id"), nullable=True
    )
    origin: Mapped[RevisionOrigin] = mapped_column(
        str_enum(RevisionOrigin, "generation_revision_origin", length=32)
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    created_by_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_by_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_round_id: Mapped[UUID | None] = mapped_column(nullable=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JsonDict)


class ContentGenerationLessonSection(UUIDTimestampMixin, Base):
    __tablename__ = "content_generation_lesson_sections"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "outline_node_id",
            name="uq_content_generation_lesson_sections_run_outline_node",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_generation_runs.id", ondelete="CASCADE"), index=True
    )
    outline_node_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_generation_outline_nodes.id", ondelete="CASCADE"), index=True
    )
    heading: Mapped[str] = mapped_column(String(200))
    markdown: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)


class GenerationLessonSectionSnapshot(UUIDTimestampMixin, Base):
    __tablename__ = "generation_lesson_section_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "revision_id",
            "section_key",
            name="uq_generation_lesson_section_snapshots_revision_section_key",
        ),
    )

    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("generation_revisions.id", ondelete="CASCADE"), index=True
    )
    section_key: Mapped[UUID] = mapped_column(index=True)
    heading: Mapped[str] = mapped_column(String(200))
    markdown: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)


class GenerationOutlineRevisionNode(UUIDTimestampMixin, Base):
    __tablename__ = "generation_outline_revision_nodes"
    __table_args__ = (
        UniqueConstraint(
            "revision_id",
            "node_key",
            name="uq_generation_outline_revision_nodes_revision_node_key",
        ),
        UniqueConstraint(
            "revision_id",
            "slug",
            name="uq_generation_outline_revision_nodes_revision_slug",
        ),
    )

    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("generation_revisions.id", ondelete="CASCADE"), index=True
    )
    node_key: Mapped[UUID] = mapped_column(index=True)
    parent_node_key: Mapped[UUID | None] = mapped_column(nullable=True)
    slug: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    token_mass: Mapped[int] = mapped_column(Integer)
    prerequisite_score: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    centrality: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    weight: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    matched_subtopic_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("subtopics.id"), nullable=True
    )
    force_create: Mapped[bool] = mapped_column(Boolean, default=False)
    proposed_outcomes: Mapped[list[Any]] = mapped_column(JsonDict)
    source_headings: Mapped[list[Any]] = mapped_column(JsonDict, default=list)
    sequence: Mapped[int] = mapped_column(Integer)


class ReviewRoundRow(UUIDTimestampMixin, Base):
    __tablename__ = "review_rounds"
    __table_args__ = (
        UniqueConstraint("run_id", "stage", "number", name="uq_review_rounds_run_stage_number"),
        UniqueConstraint("revision_id", name="uq_review_rounds_revision_id"),
        CheckConstraint("number IN (1, 2)", name="ck_review_rounds_number"),
        partial_unique_index(
            "uq_review_rounds_one_open_per_run",
            "run_id",
            where="sealed_at IS NULL",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_generation_runs.id", ondelete="CASCADE"), index=True
    )
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("generation_revisions.id"), index=True)
    stage: Mapped[ReviewStage] = mapped_column(
        str_enum(ReviewStage, "review_round_stage", length=16)
    )
    number: Mapped[int] = mapped_column(Integer)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewRoundParticipant(UUIDTimestampMixin, Base):
    __tablename__ = "review_round_participants"
    __table_args__ = (
        UniqueConstraint(
            "round_id",
            "reviewer_user_id",
            name="uq_review_round_participants_round_reviewer",
        ),
    )

    round_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_rounds.id", ondelete="CASCADE"), index=True
    )
    reviewer_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    teaching_assignment_ids: Mapped[list[Any]] = mapped_column(JsonDict)


class ReviewDecisionRow(UUIDTimestampMixin, Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        UniqueConstraint(
            "round_id",
            "reviewer_user_id",
            name="uq_review_decisions_round_reviewer",
        ),
        CheckConstraint(
            "verdict IN ('approve', 'changes_requested')",
            name="ck_review_decisions_verdict",
        ),
    )

    round_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_rounds.id", ondelete="CASCADE"), index=True
    )
    reviewer_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("generation_revisions.id"), index=True)
    verdict: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(200))


class GenerationChangeRequest(UUIDTimestampMixin, Base):
    __tablename__ = "generation_change_requests"

    decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_decisions.id", ondelete="CASCADE"), index=True
    )
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("generation_revisions.id"), index=True)
    author_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    target_kind: Mapped[str] = mapped_column(String(32))
    node_key: Mapped[UUID | None] = mapped_column(nullable=True)
    field: Mapped[str] = mapped_column(String(32))
    kind: Mapped[ChangeKind] = mapped_column(
        str_enum(ChangeKind, "generation_change_request_kind", length=32)
    )
    comment: Mapped[str] = mapped_column(Text)


class GenerationReviewChat(UUIDTimestampMixin, Base):
    """Per-reviewer thread on a generation run. Not a policy `chat_conversations` row."""

    __tablename__ = "content_generation_review_chats"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "owner_user_id",
            name="uq_content_generation_review_chats_run_owner",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_generation_runs.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)


class GenerationReviewMessage(UUIDTimestampMixin, Base):
    __tablename__ = "content_generation_review_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_content_generation_review_messages_role",
        ),
    )

    chat_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_generation_review_chats.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[Any] | None] = mapped_column(JsonDict, nullable=True)
    draft_change_request: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)


class ReviewRoundClosure(UUIDTimestampMixin, Base):
    __tablename__ = "review_round_closures"
    __table_args__ = (UniqueConstraint("round_id", name="uq_review_round_closures_round_id"),)

    round_id: Mapped[UUID] = mapped_column(ForeignKey("review_rounds.id"), index=True)
    base_revision_id: Mapped[UUID] = mapped_column(ForeignKey("generation_revisions.id"))
    action: Mapped[CloseAction] = mapped_column(
        str_enum(CloseAction, "review_round_closure_action", length=32)
    )
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    collated_request_ids: Mapped[list[Any]] = mapped_column(JsonDict)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)


class CurriculumGenerationJob(UUIDTimestampMixin, Base):
    """Admin ADK lesson+quiz generation. Not a topic-run ``generation_jobs`` row."""

    __tablename__ = "curriculum_generation_jobs"
    __table_args__ = (
        Index("ix_curriculum_generation_jobs_status_created_at", "status", "created_at"),
        partial_unique_index(
            "uq_curriculum_generation_jobs_one_in_flight_subtopic",
            "subtopic_id",
            where="status IN ('queued', 'running')",
        ),
    )

    subtopic_id: Mapped[UUID] = mapped_column(ForeignKey("subtopics.id"), index=True)
    status: Mapped[GenerationJobStatus] = mapped_column(
        str_enum(GenerationJobStatus, "curriculum_generation_job_status", length=32)
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    transcript: Mapped[dict[str, Any] | None] = mapped_column(JsonDict, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    round_count: Mapped[int] = mapped_column(Integer, default=0)
    source_material_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_material_versions.id"), nullable=True
    )
    quiz_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("quiz_versions.id"), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
