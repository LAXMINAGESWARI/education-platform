"""Immutable outline snapshots, fingerprints, diffs, and revision inserts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.modules.academics.models import (
    TeachingAssignment,
    TeachingAssignmentStatus,
    Topic,
)
from education_platform.modules.auth.models import User
from education_platform.modules.generation.models import (
    ContentGenerationLessonSection,
    ContentGenerationOutlineNode,
    ContentGenerationRun,
    GenerationChangeRequest,
    GenerationLessonSectionSnapshot,
    GenerationOutlineRevisionNode,
    GenerationRevision,
    ReviewRoundParticipant,
    ReviewRoundRow,
)
from education_platform.modules.generation.types import (
    ROUND_DURATION,
    ChangeKind,
    ContentRevision,
    ContentRevisionDiff,
    ContentSnapshot,
    DraftChangeRequest,
    DraftLessonRequest,
    DraftOutlineRequest,
    DraftQuizRequest,
    FrozenRevision,
    GenerationError,
    HumanActorStamp,
    ItemCount,
    LessonField,
    LessonSectionDelta,
    LessonSectionSnapshot,
    LessonSectionTarget,
    OutlineDocument,
    OutlineDocumentTarget,
    OutlineField,
    OutlineNode,
    OutlineNodeDelta,
    OutlineNodeSnapshot,
    OutlineNodeTarget,
    OutlineRevision,
    OutlineRevisionDiff,
    OutlineSnapshot,
    ProposedOutcome,
    ProposedOutcomeSnapshot,
    QuizAnswerKeySnapshot,
    QuizField,
    QuizItemDelta,
    QuizItemSnapshot,
    QuizItemTarget,
    QuizOptionSnapshot,
    ReviewParticipant,
    ReviewRound,
    ReviewStage,
    ReviewTarget,
    RevisionNumber,
    RevisionOrigin,
    TeacherRoundNumber,
    ValueDelta,
    WorkerActorStamp,
)


def snapshot_from_document(document: OutlineDocument) -> OutlineSnapshot:
    nodes = tuple(
        OutlineNodeSnapshot(
            node_key=node.id,
            parent_node_key=node.parent_id,
            slug=node.slug,
            title=node.title,
            token_mass=node.token_mass,
            prerequisite_score=node.prerequisite_score,
            centrality=node.centrality,
            weight=node.weight,
            matched_subtopic_id=node.matched_subtopic_id,
            force_create=node.force_create,
            proposed_outcomes=tuple(
                ProposedOutcomeSnapshot(statement=item.statement) for item in node.proposed_outcomes
            ),
            sequence=node.sequence,
            source_headings=node.source_headings,
        )
        for node in document.nodes
    )
    return OutlineSnapshot(target_item_count=document.target_item_count, nodes=nodes)


def snapshot_from_node_rows(
    target_item_count: int, rows: Sequence[ContentGenerationOutlineNode]
) -> OutlineSnapshot:
    ordered = sorted(rows, key=lambda row: row.sequence)
    nodes = tuple(
        OutlineNodeSnapshot(
            node_key=row.id,
            parent_node_key=row.parent_id,
            slug=row.slug,
            title=row.title,
            token_mass=row.token_mass,
            prerequisite_score=Decimal(row.prerequisite_score),
            centrality=Decimal(row.centrality),
            weight=Decimal(row.weight),
            matched_subtopic_id=row.matched_subtopic_id,
            force_create=row.force_create,
            proposed_outcomes=_outcomes_from_json(row.proposed_outcomes),
            sequence=row.sequence,
            source_headings=tuple(
                item.strip()
                for item in (row.source_headings or [])
                if isinstance(item, str) and item.strip()
            ),
        )
        for row in ordered
    )
    return OutlineSnapshot(target_item_count=ItemCount(target_item_count), nodes=nodes)


def snapshot_to_json(snapshot: OutlineSnapshot) -> dict[str, Any]:
    return {
        "target_item_count": snapshot.target_item_count.value,
        "nodes": [_node_to_json(node) for node in snapshot.nodes],
    }


def snapshot_from_json(raw: object) -> OutlineSnapshot:
    if not isinstance(raw, dict):
        raise ValueError("outline snapshot must be an object")
    count = raw.get("target_item_count")
    nodes_raw = raw.get("nodes")
    if not isinstance(count, int) or not isinstance(nodes_raw, list):
        raise ValueError("outline snapshot is missing nodes or target_item_count")
    nodes = tuple(_node_from_json(item) for item in nodes_raw)
    return OutlineSnapshot(target_item_count=ItemCount(count), nodes=nodes)


def fingerprint_snapshot(snapshot: OutlineSnapshot) -> str:
    payload = json.dumps(snapshot_to_json(snapshot), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def revision_from_row(row: GenerationRevision, *, display_name: str = "") -> OutlineRevision:
    created_at = row.created_at
    if row.created_by_user_id is not None:
        author: HumanActorStamp | WorkerActorStamp = HumanActorStamp(
            user_id=row.created_by_user_id,
            display_name=display_name,
            occurred_at=created_at,
        )
    else:
        author = WorkerActorStamp(
            job_id=row.created_by_job_id or row.id,
            model=row.created_by_model or "outline",
            occurred_at=created_at,
        )
    return OutlineRevision(
        id=row.id,
        run_id=row.run_id,
        number=RevisionNumber(row.number),
        parent_id=row.parent_revision_id,
        origin=row.origin,
        created_by=author,
        source_round_id=row.source_round_id,
        snapshot_hash=row.snapshot_hash,
        snapshot=snapshot_from_json(row.snapshot),
        created_at=created_at,
        stage="outline",
    )


def content_revision_from_row(
    row: GenerationRevision, *, display_name: str = ""
) -> ContentRevision:
    created_at = row.created_at
    if row.created_by_user_id is not None:
        author: HumanActorStamp | WorkerActorStamp = HumanActorStamp(
            user_id=row.created_by_user_id,
            display_name=display_name,
            occurred_at=created_at,
        )
    else:
        author = WorkerActorStamp(
            job_id=row.created_by_job_id or row.id,
            model=row.created_by_model or "lesson",
            occurred_at=created_at,
        )
    return ContentRevision(
        id=row.id,
        run_id=row.run_id,
        number=RevisionNumber(row.number),
        parent_id=row.parent_revision_id,
        origin=row.origin,
        created_by=author,
        source_round_id=row.source_round_id,
        snapshot_hash=row.snapshot_hash,
        snapshot=content_snapshot_from_json(row.snapshot),
        created_at=created_at,
        stage="qa",
    )


def frozen_revision_from_row(row: GenerationRevision, *, display_name: str = "") -> FrozenRevision:
    if row.stage is ReviewStage.QA:
        return content_revision_from_row(row, display_name=display_name)
    return revision_from_row(row, display_name=display_name)


def diff_snapshots(parent: OutlineRevision, current: OutlineRevision) -> OutlineRevisionDiff:
    before = {node.node_key: node for node in parent.snapshot.nodes}
    after = {node.node_key: node for node in current.snapshot.nodes}
    keys = tuple(dict.fromkeys((*before.keys(), *after.keys())))
    return OutlineRevisionDiff(
        kind="outline",
        from_revision_id=parent.id,
        to_revision_id=current.id,
        target_item_count=ValueDelta(
            before=parent.snapshot.target_item_count.value,
            after=current.snapshot.target_item_count.value,
        ),
        nodes=tuple(
            OutlineNodeDelta(node_key=key, before=before.get(key), after=after.get(key))
            for key in keys
            if before.get(key) != after.get(key)
        ),
    )


def make_revision_row(
    *,
    run_id: UUID,
    snapshot: OutlineSnapshot | ContentSnapshot,
    origin: RevisionOrigin,
    number: int,
    parent_id: UUID | None,
    source_round_id: UUID | None,
    created_by_user_id: UUID | None,
    created_by_job_id: UUID | None,
    created_by_model: str | None,
    stage: ReviewStage = ReviewStage.OUTLINE,
) -> GenerationRevision:
    if isinstance(snapshot, ContentSnapshot):
        payload = content_snapshot_to_json(snapshot)
        digest = fingerprint_content_snapshot(snapshot)
        stage = ReviewStage.QA
    else:
        payload = snapshot_to_json(snapshot)
        digest = fingerprint_snapshot(snapshot)
    return GenerationRevision(
        id=uuid4(),
        run_id=run_id,
        stage=stage,
        number=number,
        parent_revision_id=parent_id,
        origin=origin,
        created_by_user_id=created_by_user_id,
        created_by_job_id=created_by_job_id,
        created_by_model=created_by_model,
        source_round_id=source_round_id,
        snapshot_hash=digest,
        snapshot=payload,
    )


def make_node_rows(
    revision_id: UUID, snapshot: OutlineSnapshot
) -> list[GenerationOutlineRevisionNode]:
    return [
        GenerationOutlineRevisionNode(
            revision_id=revision_id,
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
            source_headings=list(node.source_headings),
            sequence=node.sequence,
        )
        for node in snapshot.nodes
    ]


def persist_outline_revision(
    session: Session,
    *,
    run_id: UUID,
    snapshot: OutlineSnapshot,
    origin: RevisionOrigin,
    number: int,
    parent_id: UUID | None,
    source_round_id: UUID | None,
    created_by_user_id: UUID | None,
    created_by_job_id: UUID | None,
    created_by_model: str | None,
) -> GenerationRevision:
    revision = make_revision_row(
        run_id=run_id,
        snapshot=snapshot,
        origin=origin,
        number=number,
        parent_id=parent_id,
        source_round_id=source_round_id,
        created_by_user_id=created_by_user_id,
        created_by_job_id=created_by_job_id,
        created_by_model=created_by_model,
    )
    session.add(revision)
    session.flush()
    for node in make_node_rows(revision.id, snapshot):
        session.add(node)
    session.flush()
    return revision


def persist_content_revision(
    session: Session,
    *,
    run_id: UUID,
    snapshot: ContentSnapshot,
    origin: RevisionOrigin,
    number: int,
    parent_id: UUID | None,
    source_round_id: UUID | None,
    created_by_user_id: UUID | None,
    created_by_job_id: UUID | None,
    created_by_model: str | None,
) -> GenerationRevision:
    revision = make_revision_row(
        run_id=run_id,
        snapshot=snapshot,
        origin=origin,
        number=number,
        parent_id=parent_id,
        source_round_id=source_round_id,
        created_by_user_id=created_by_user_id,
        created_by_job_id=created_by_job_id,
        created_by_model=created_by_model,
        stage=ReviewStage.QA,
    )
    session.add(revision)
    session.flush()
    for section in snapshot.lesson_sections:
        session.add(
            GenerationLessonSectionSnapshot(
                revision_id=revision.id,
                section_key=section.section_key,
                heading=section.heading,
                markdown=section.markdown,
                sequence=section.sequence,
            )
        )
    session.flush()
    return revision


def replace_run_lesson_sections(
    session: Session, run_id: UUID, sections: Sequence[LessonSectionSnapshot]
) -> None:
    existing = list(
        session.scalars(
            select(ContentGenerationLessonSection).where(
                ContentGenerationLessonSection.run_id == run_id
            )
        ).all()
    )
    by_key = {row.outline_node_id: row for row in existing}
    keep = {section.section_key for section in sections}
    for existing_row in existing:
        if existing_row.outline_node_id not in keep:
            session.delete(existing_row)
    session.flush()
    for section in sections:
        section_row = by_key.get(section.section_key)
        if section_row is None:
            session.add(
                ContentGenerationLessonSection(
                    run_id=run_id,
                    outline_node_id=section.section_key,
                    heading=section.heading,
                    markdown=section.markdown,
                    sequence=section.sequence,
                )
            )
            continue
        section_row.heading = section.heading
        section_row.markdown = section.markdown
        section_row.sequence = section.sequence
    session.flush()


def apply_snapshot_to_run_nodes(session: Session, run_id: UUID, snapshot: OutlineSnapshot) -> None:
    existing = list(
        session.scalars(
            select(ContentGenerationOutlineNode).where(
                ContentGenerationOutlineNode.run_id == run_id
            )
        ).all()
    )
    by_id = {row.id: row for row in existing}
    keep_ids = {node.node_key for node in snapshot.nodes}
    for row in existing:
        if row.id not in keep_ids:
            row.parent_id = None
    session.flush()
    for row in existing:
        if row.id not in keep_ids:
            session.delete(row)
    session.flush()
    pending_parents: list[tuple[ContentGenerationOutlineNode, UUID | None]] = []
    for node in snapshot.nodes:
        existing_row = by_id.get(node.node_key)
        if existing_row is None:
            row = ContentGenerationOutlineNode(
                id=node.node_key,
                run_id=run_id,
                parent_id=None,
                slug=node.slug,
                title=node.title,
                token_mass=node.token_mass,
                prerequisite_score=node.prerequisite_score,
                centrality=node.centrality,
                weight=node.weight,
                quota=None,
                matched_subtopic_id=node.matched_subtopic_id,
                force_create=node.force_create,
                accepted_subtopic_id=None,
                proposed_outcomes=[item.statement for item in node.proposed_outcomes],
                source_headings=list(node.source_headings),
                sequence=node.sequence,
            )
            session.add(row)
        else:
            row = existing_row
            row.slug = node.slug
            row.title = node.title
            row.token_mass = node.token_mass
            row.prerequisite_score = node.prerequisite_score
            row.centrality = node.centrality
            row.weight = node.weight
            row.matched_subtopic_id = node.matched_subtopic_id
            row.force_create = node.force_create
            row.proposed_outcomes = [item.statement for item in node.proposed_outcomes]
            row.source_headings = list(node.source_headings)
            row.sequence = node.sequence
            row.quota = None
            row.accepted_subtopic_id = None
        pending_parents.append((row, node.parent_node_key))
    session.flush()
    for row, parent_key in pending_parents:
        row.parent_id = parent_key
    session.flush()


def install_initial_outline_revision(
    session: Session, run: ContentGenerationRun, *, job_id: UUID | None
) -> ReviewRoundRow | None:
    nodes = list(
        session.scalars(
            select(ContentGenerationOutlineNode)
            .where(ContentGenerationOutlineNode.run_id == run.id)
            .order_by(ContentGenerationOutlineNode.sequence)
        ).all()
    )
    if not nodes:
        return None
    existing = session.scalar(
        select(GenerationRevision.id).where(
            GenerationRevision.run_id == run.id,
            GenerationRevision.stage == ReviewStage.OUTLINE,
            GenerationRevision.number == 1,
        )
    )
    if existing is not None:
        return session.scalar(
            select(ReviewRoundRow).where(
                ReviewRoundRow.run_id == run.id,
                ReviewRoundRow.stage == ReviewStage.OUTLINE,
                ReviewRoundRow.number == 1,
            )
        )
    snapshot = snapshot_from_node_rows(run.target_item_count, nodes)
    revision = persist_outline_revision(
        session,
        run_id=run.id,
        snapshot=snapshot,
        origin=RevisionOrigin.INITIAL_GENERATION,
        number=1,
        parent_id=None,
        source_round_id=None,
        created_by_user_id=None,
        created_by_job_id=job_id or run.id,
        created_by_model="outline",
    )
    return open_review_round(session, run=run, revision_id=revision.id, number=1)


def open_review_round(
    session: Session,
    *,
    run: ContentGenerationRun,
    revision_id: UUID,
    number: int,
    stage: ReviewStage = ReviewStage.OUTLINE,
) -> ReviewRoundRow:
    now = datetime.now(UTC)
    round_row = ReviewRoundRow(
        run_id=run.id,
        revision_id=revision_id,
        stage=stage,
        number=TeacherRoundNumber(number).value,
        opened_at=now,
        due_at=now + ROUND_DURATION,
        sealed_at=None,
    )
    session.add(round_row)
    session.flush()
    for participant in offering_teachers(session, run.topic_id):
        session.add(
            ReviewRoundParticipant(
                round_id=round_row.id,
                reviewer_user_id=participant.reviewer_user_id,
                display_name=participant.display_name,
                teaching_assignment_ids=[str(item) for item in participant.teaching_assignment_ids],
            )
        )
    session.flush()
    return round_row


def offering_teachers(session: Session, topic_id: UUID) -> tuple[ReviewParticipant, ...]:
    topic = session.get(Topic, topic_id)
    if topic is None:
        return ()
    offering_id = topic.grade_subject_offering_id
    rows = session.execute(
        select(
            TeachingAssignment.teacher_user_id,
            User.full_name,
            TeachingAssignment.id,
        )
        .join(User, User.id == TeachingAssignment.teacher_user_id)
        .where(
            TeachingAssignment.grade_subject_offering_id == offering_id,
            TeachingAssignment.status == TeachingAssignmentStatus.ACTIVE,
        )
        .order_by(User.full_name, TeachingAssignment.id)
    ).all()
    grouped: dict[UUID, ReviewParticipant] = {}
    for user_id, full_name, assignment_id in rows:
        current = grouped.get(user_id)
        if current is None:
            grouped[user_id] = ReviewParticipant(
                reviewer_user_id=user_id,
                display_name=full_name,
                teaching_assignment_ids=(assignment_id,),
            )
        else:
            grouped[user_id] = ReviewParticipant(
                reviewer_user_id=user_id,
                display_name=full_name,
                teaching_assignment_ids=(*current.teaching_assignment_ids, assignment_id),
            )
    return tuple(grouped.values())


def document_from_snapshot(snapshot: OutlineSnapshot) -> OutlineDocument:
    return OutlineDocument(
        target_item_count=snapshot.target_item_count,
        nodes=tuple(
            OutlineNode(
                id=node.node_key,
                parent_id=node.parent_node_key,
                slug=node.slug,
                title=node.title,
                token_mass=node.token_mass,
                prerequisite_score=node.prerequisite_score,
                centrality=node.centrality,
                weight=node.weight,
                quota=None,
                matched_subtopic_id=node.matched_subtopic_id,
                force_create=node.force_create,
                accepted_subtopic_id=None,
                proposed_outcomes=tuple(
                    ProposedOutcome(statement=item.statement) for item in node.proposed_outcomes
                ),
                sequence=node.sequence,
                source_headings=node.source_headings,
            )
            for node in snapshot.nodes
        ),
    )


def round_from_row(
    row: ReviewRoundRow, participants: Sequence[ReviewRoundParticipant]
) -> ReviewRound:
    return ReviewRound(
        id=row.id,
        run_id=row.run_id,
        revision_id=row.revision_id,
        stage=row.stage,
        number=TeacherRoundNumber(row.number),
        participants=tuple(
            ReviewParticipant(
                reviewer_user_id=item.reviewer_user_id,
                display_name=item.display_name,
                teaching_assignment_ids=tuple(
                    UUID(str(value)) for value in item.teaching_assignment_ids
                ),
            )
            for item in participants
        ),
        opened_at=row.opened_at,
        due_at=row.due_at,
        sealed_at=row.sealed_at,
    )


def leaf_outline_revision(session: Session, run_id: UUID) -> GenerationRevision | None:
    return leaf_revision(session, run_id, ReviewStage.OUTLINE)


def leaf_revision(session: Session, run_id: UUID, stage: ReviewStage) -> GenerationRevision | None:
    rows = list(
        session.scalars(
            select(GenerationRevision)
            .where(GenerationRevision.run_id == run_id, GenerationRevision.stage == stage)
            .order_by(GenerationRevision.number.desc())
        ).all()
    )
    if not rows:
        return None
    children = {row.parent_revision_id for row in rows if row.parent_revision_id is not None}
    for row in rows:
        if row.id not in children:
            return row
    return rows[0]


def _node_to_json(node: OutlineNodeSnapshot) -> dict[str, Any]:
    payload = {
        "node_key": str(node.node_key),
        "parent_node_key": None if node.parent_node_key is None else str(node.parent_node_key),
        "slug": node.slug,
        "title": node.title,
        "token_mass": node.token_mass,
        "prerequisite_score": str(node.prerequisite_score),
        "centrality": str(node.centrality),
        "weight": str(node.weight),
        "matched_subtopic_id": (
            None if node.matched_subtopic_id is None else str(node.matched_subtopic_id)
        ),
        "force_create": node.force_create,
        "proposed_outcomes": [item.statement for item in node.proposed_outcomes],
        "sequence": node.sequence,
    }
    if node.source_headings:
        payload["source_headings"] = list(node.source_headings)
    return payload


def _node_from_json(item: object) -> OutlineNodeSnapshot:
    if not isinstance(item, dict):
        raise ValueError("outline node snapshot must be an object")
    parent_raw = item.get("parent_node_key")
    matched_raw = item.get("matched_subtopic_id")
    outcomes_raw = item.get("proposed_outcomes")
    statements = outcomes_raw if isinstance(outcomes_raw, list) else []
    return OutlineNodeSnapshot(
        node_key=UUID(str(item["node_key"])),
        parent_node_key=None if parent_raw in (None, "") else UUID(str(parent_raw)),
        slug=str(item["slug"]),
        title=str(item["title"]),
        token_mass=int(item["token_mass"]),
        prerequisite_score=Decimal(str(item["prerequisite_score"])),
        centrality=Decimal(str(item["centrality"])),
        weight=Decimal(str(item["weight"])),
        matched_subtopic_id=None if matched_raw in (None, "") else UUID(str(matched_raw)),
        force_create=bool(item.get("force_create", False)),
        proposed_outcomes=tuple(
            ProposedOutcomeSnapshot(statement=str(statement).strip())
            for statement in statements
            if str(statement).strip()
        ),
        sequence=int(item["sequence"]),
        source_headings=_snapshot_source_headings(item.get("source_headings")),
    )


def _snapshot_source_headings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())


def content_snapshot_to_json(snapshot: ContentSnapshot) -> dict[str, Any]:
    return {
        "rendered_lesson_markdown": snapshot.rendered_lesson_markdown,
        "quiz_version_id": str(snapshot.quiz_version_id),
        "lesson_sections": [
            {
                "section_key": str(section.section_key),
                "heading": section.heading,
                "markdown": section.markdown,
                "sequence": section.sequence,
            }
            for section in snapshot.lesson_sections
        ],
        "quiz_items": [_quiz_item_to_json(item) for item in snapshot.quiz_items],
    }


def content_snapshot_from_json(raw: object) -> ContentSnapshot:
    if not isinstance(raw, dict):
        raise ValueError("content snapshot must be an object")
    markdown = str(raw.get("rendered_lesson_markdown") or "")
    quiz_raw = raw.get("quiz_version_id")
    sections_raw = raw.get("lesson_sections")
    items_raw = raw.get("quiz_items")
    if (
        quiz_raw in (None, "")
        or not isinstance(sections_raw, list)
        or not isinstance(items_raw, list)
    ):
        raise ValueError("content snapshot is missing sections or quiz items")
    sections = tuple(
        sorted((_section_from_json(item) for item in sections_raw), key=lambda item: item.sequence)
    )
    items = tuple(
        sorted((_quiz_item_from_json(item) for item in items_raw), key=lambda item: item.sequence)
    )
    return ContentSnapshot(
        rendered_lesson_markdown=markdown,
        lesson_sections=sections,
        quiz_version_id=UUID(str(quiz_raw)),
        quiz_items=items,
    )


def fingerprint_content_snapshot(snapshot: ContentSnapshot) -> str:
    payload = json.dumps(content_snapshot_to_json(snapshot), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def diff_content_snapshots(
    parent: ContentRevision, current: ContentRevision
) -> ContentRevisionDiff:
    before_sections = {item.section_key: item for item in parent.snapshot.lesson_sections}
    after_sections = {item.section_key: item for item in current.snapshot.lesson_sections}
    section_keys = tuple(dict.fromkeys((*before_sections.keys(), *after_sections.keys())))
    before_items = {item.item_key: item for item in parent.snapshot.quiz_items}
    after_items = {item.item_key: item for item in current.snapshot.quiz_items}
    item_keys = tuple(dict.fromkeys((*before_items.keys(), *after_items.keys())))
    return ContentRevisionDiff(
        kind="content",
        from_revision_id=parent.id,
        to_revision_id=current.id,
        sections=tuple(
            LessonSectionDelta(
                section_key=key,
                before=before_sections.get(key),
                after=after_sections.get(key),
            )
            for key in section_keys
            if before_sections.get(key) != after_sections.get(key)
        ),
        quiz_items=tuple(
            QuizItemDelta(item_key=key, before=before_items.get(key), after=after_items.get(key))
            for key in item_keys
            if before_items.get(key) != after_items.get(key)
        ),
    )


def diff_frozen(
    parent: FrozenRevision, current: FrozenRevision
) -> OutlineRevisionDiff | ContentRevisionDiff:
    if isinstance(parent, ContentRevision) and isinstance(current, ContentRevision):
        return diff_content_snapshots(parent, current)
    if isinstance(parent, OutlineRevision) and isinstance(current, OutlineRevision):
        return diff_snapshots(parent, current)
    raise ValueError("cannot diff mixed outline and content revisions")


def _quiz_item_to_json(item: QuizItemSnapshot) -> dict[str, Any]:
    return {
        "item_key": str(item.item_key),
        "question_id": str(item.question_id),
        "question_version_id": str(item.question_version_id),
        "subtopic_id": str(item.subtopic_id),
        "prompt": item.prompt,
        "options": [
            {"label": option.label, "text": option.text, "sequence": option.sequence}
            for option in item.options
        ],
        "answer_key": {
            "correct_label": item.answer_key.correct_label,
            "correct_rationale": item.answer_key.correct_rationale,
            "distractor_rationales": [
                [label, text] for label, text in item.answer_key.distractor_rationales
            ],
        },
        "sequence": item.sequence,
    }


def _section_from_json(item: object) -> LessonSectionSnapshot:
    if not isinstance(item, dict):
        raise ValueError("lesson section snapshot must be an object")
    return LessonSectionSnapshot(
        section_key=UUID(str(item["section_key"])),
        heading=str(item["heading"]),
        markdown=str(item["markdown"]),
        sequence=int(item["sequence"]),
    )


def _quiz_item_from_json(item: object) -> QuizItemSnapshot:
    if not isinstance(item, dict):
        raise ValueError("quiz item snapshot must be an object")
    options_raw = item.get("options")
    options: tuple[QuizOptionSnapshot, ...] = ()
    if isinstance(options_raw, list):
        options = tuple(
            QuizOptionSnapshot(
                label=str(option["label"]),
                text=str(option["text"]),
                sequence=int(option["sequence"]),
            )
            for option in options_raw
            if isinstance(option, dict)
        )
    key_raw = item.get("answer_key")
    if not isinstance(key_raw, dict):
        raise ValueError("quiz item snapshot is missing answer_key")
    distractors_raw = key_raw.get("distractor_rationales")
    distractors: list[tuple[str, str]] = []
    if isinstance(distractors_raw, list):
        for pair in distractors_raw:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                distractors.append((str(pair[0]), str(pair[1])))
            elif isinstance(pair, dict):
                distractors.append((str(pair.get("label") or ""), str(pair.get("text") or "")))
    return QuizItemSnapshot(
        item_key=UUID(str(item["item_key"])),
        question_id=UUID(str(item["question_id"])),
        question_version_id=UUID(str(item["question_version_id"])),
        subtopic_id=UUID(str(item["subtopic_id"])),
        prompt=str(item["prompt"]),
        options=options,
        answer_key=QuizAnswerKeySnapshot(
            correct_label=str(key_raw.get("correct_label") or ""),
            correct_rationale=str(key_raw.get("correct_rationale") or ""),
            distractor_rationales=tuple(distractors),
        ),
        sequence=int(item["sequence"]),
    )


def _outcomes_from_json(raw: object) -> tuple[ProposedOutcomeSnapshot, ...]:
    if not isinstance(raw, list):
        return ()
    statements: list[ProposedOutcomeSnapshot] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            statements.append(ProposedOutcomeSnapshot(statement=item.strip()))
    return tuple(statements[:3])


def draft_request_from_row(row: GenerationChangeRequest) -> DraftChangeRequest | None:
    kind = row.kind if isinstance(row.kind, ChangeKind) else ChangeKind(row.kind)
    comment = row.comment
    target_kind = row.target_kind
    if target_kind == "outline_document":
        return DraftOutlineRequest(
            target=OutlineDocumentTarget(kind="outline_document"),
            field=_outline_field(row.field),
            kind=kind,
            comment=comment,
        )
    if target_kind == "outline_node":
        if row.node_key is None:
            return None
        return DraftOutlineRequest(
            target=OutlineNodeTarget(kind="outline_node", node_key=row.node_key),
            field=_outline_field(row.field),
            kind=kind,
            comment=comment,
        )
    if target_kind == "lesson_section":
        if row.node_key is None:
            return None
        return DraftLessonRequest(
            target=LessonSectionTarget(kind="lesson_section", section_key=row.node_key),
            field=_lesson_field(row.field),
            kind=kind,
            comment=comment,
        )
    if target_kind == "quiz_item":
        if row.node_key is None:
            return None
        return DraftQuizRequest(
            target=QuizItemTarget(kind="quiz_item", item_key=row.node_key),
            field=_quiz_field(row.field),
            kind=kind,
            comment=comment,
        )
    return None


def _outline_field(raw: object) -> OutlineField:
    if isinstance(raw, OutlineField):
        return raw
    return OutlineField(str(raw))


def _lesson_field(raw: object) -> LessonField:
    if isinstance(raw, LessonField):
        return raw
    return LessonField(str(raw))


def _quiz_field(raw: object) -> QuizField:
    if isinstance(raw, QuizField):
        return raw
    return QuizField(str(raw))


def assert_target_on_revision(target: ReviewTarget, revision: FrozenRevision) -> None:
    if isinstance(revision, OutlineRevision):
        if target.kind == "outline_document":
            return
        if target.kind != "outline_node":
            raise GenerationError(
                "Outline review only accepts outline change requests.", status_code=400
            )
        node_keys = {node.node_key for node in revision.snapshot.nodes}
        if target.node_key not in node_keys:
            raise GenerationError(
                "Change requests must target a node on this revision.", status_code=400
            )
        return
    if not isinstance(revision, ContentRevision):
        raise GenerationError("QA review requires a content revision.", status_code=409)
    if target.kind == "lesson_section":
        section_keys = {item.section_key for item in revision.snapshot.lesson_sections}
        if target.section_key not in section_keys:
            raise GenerationError(
                "Change requests must target a lesson section on this revision.",
                status_code=400,
            )
        return
    if target.kind == "quiz_item":
        item_keys = {item.item_key for item in revision.snapshot.quiz_items}
        if target.item_key not in item_keys:
            raise GenerationError(
                "Change requests must target a quiz item on this revision.",
                status_code=400,
            )
        return
    raise GenerationError(
        "QA review only accepts lesson section or quiz item change requests.",
        status_code=400,
    )


def assert_draft_matches_revision(revision: FrozenRevision, draft: DraftChangeRequest) -> None:
    if isinstance(revision, OutlineRevision):
        if not isinstance(draft, DraftOutlineRequest):
            raise GenerationError(
                "Outline review only accepts outline change requests.", status_code=400
            )
        if draft.target.kind == "outline_document":
            if draft.field is not OutlineField.OUTLINE_STRUCTURE:
                raise GenerationError(
                    "Whole-outline requests must use the outline_structure field.",
                    status_code=400,
                )
            return
        assert_target_on_revision(draft.target, revision)
        return
    if not isinstance(revision, ContentRevision):
        raise GenerationError("QA review requires a content revision.", status_code=409)
    if isinstance(draft, DraftLessonRequest):
        assert_target_on_revision(draft.target, revision)
        return
    if isinstance(draft, DraftQuizRequest):
        assert_target_on_revision(draft.target, revision)
        return
    raise GenerationError(
        "QA review only accepts lesson section or quiz item change requests.",
        status_code=400,
    )
