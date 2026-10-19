"""One-call outline/content rewrite from frozen change requests. Never accepts or publishes."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from education_platform.modules.generation.types import (
    ChangeRequest,
    DraftLessonRequest,
    DraftOutlineRequest,
    DraftQuizRequest,
    LessonSectionSnapshot,
    OutlineField,
    OutlineNodeSnapshot,
    OutlineSnapshot,
    ProposedOutcomeSnapshot,
    QuizItemSnapshot,
)


def rewrite_outline_snapshot(
    base: OutlineSnapshot, requests: Sequence[ChangeRequest]
) -> OutlineSnapshot:
    """Heuristic successor: preserve stable keys and apply title/outcome comments."""
    comments_by_key: dict[object, list[str]] = {}
    structure_notes: list[str] = []
    for item in requests:
        request = item.request
        if not isinstance(request, DraftOutlineRequest):
            continue
        comment = request.comment.strip()
        if request.target.kind == "outline_document":
            structure_notes.append(comment)
            continue
        comments_by_key.setdefault(request.target.node_key, []).append(comment)
        if request.field is OutlineField.OUTLINE_STRUCTURE:
            structure_notes.append(comment)
    nodes: list[OutlineNodeSnapshot] = []
    for node in base.nodes:
        notes = comments_by_key.get(node.node_key, [])
        title = node.title
        outcomes = node.proposed_outcomes
        if notes:
            title = _apply_title_note(title, notes[0])
            extra = notes[0]
            if len(outcomes) < 3 and extra not in {item.statement for item in outcomes}:
                outcomes = (*outcomes, ProposedOutcomeSnapshot(statement=extra[:200]))
        nodes.append(
            OutlineNodeSnapshot(
                node_key=node.node_key,
                parent_node_key=node.parent_node_key,
                slug=node.slug,
                title=title,
                token_mass=node.token_mass,
                prerequisite_score=node.prerequisite_score,
                centrality=node.centrality,
                weight=node.weight,
                matched_subtopic_id=node.matched_subtopic_id,
                force_create=node.force_create,
                proposed_outcomes=outcomes[:3],
                sequence=node.sequence,
                source_headings=node.source_headings,
            )
        )
    if structure_notes and nodes:
        first = nodes[0]
        nodes[0] = OutlineNodeSnapshot(
            node_key=first.node_key,
            parent_node_key=first.parent_node_key,
            slug=first.slug,
            title=first.title,
            token_mass=first.token_mass,
            prerequisite_score=first.prerequisite_score,
            centrality=first.centrality,
            weight=first.weight,
            matched_subtopic_id=first.matched_subtopic_id,
            force_create=first.force_create,
            proposed_outcomes=first.proposed_outcomes,
            sequence=first.sequence,
            source_headings=first.source_headings,
        )
    return OutlineSnapshot(target_item_count=base.target_item_count, nodes=tuple(nodes))


def _apply_title_note(title: str, comment: str) -> str:
    marker = comment.strip()
    if not marker or marker.casefold() in title.casefold():
        return title
    suffix = f" ({marker[:80]})"
    if len(title) + len(suffix) > 200:
        return title
    return f"{title}{suffix}"


def targeted_section_keys(requests: Sequence[ChangeRequest]) -> frozenset[UUID]:
    keys: set[UUID] = set()
    for item in requests:
        request = item.request
        if isinstance(request, DraftLessonRequest):
            keys.add(request.target.section_key)
    return frozenset(keys)


def targeted_item_keys(requests: Sequence[ChangeRequest]) -> frozenset[UUID]:
    keys: set[UUID] = set()
    for item in requests:
        request = item.request
        if isinstance(request, DraftQuizRequest):
            keys.add(request.target.item_key)
    return frozenset(keys)


def comments_for_section(requests: Sequence[ChangeRequest], section_key: UUID) -> tuple[str, ...]:
    notes: list[str] = []
    for item in requests:
        request = item.request
        if isinstance(request, DraftLessonRequest) and request.target.section_key == section_key:
            notes.append(request.comment.strip())
    return tuple(note for note in notes if note)


def comments_for_item(requests: Sequence[ChangeRequest], item_key: UUID) -> tuple[str, ...]:
    notes: list[str] = []
    for item in requests:
        request = item.request
        if isinstance(request, DraftQuizRequest) and request.target.item_key == item_key:
            notes.append(request.comment.strip())
    return tuple(note for note in notes if note)


def apply_section_notes(
    section: LessonSectionSnapshot, notes: Sequence[str]
) -> LessonSectionSnapshot:
    if not notes:
        return section
    extra = " ".join(notes)[:400]
    marker = f"\n\n**Teacher request.** {extra}\n"
    if extra.casefold() in section.markdown.casefold():
        return section
    return LessonSectionSnapshot(
        section_key=section.section_key,
        heading=section.heading,
        markdown=section.markdown.rstrip() + marker,
        sequence=section.sequence,
    )


def apply_item_notes(item: QuizItemSnapshot, notes: Sequence[str]) -> QuizItemSnapshot:
    if not notes:
        return item
    extra = notes[0][:120]
    prompt = item.prompt
    if extra and extra.casefold() not in prompt.casefold():
        prompt = f"{prompt} ({extra})"
    return QuizItemSnapshot(
        item_key=item.item_key,
        question_id=item.question_id,
        question_version_id=item.question_version_id,
        subtopic_id=item.subtopic_id,
        prompt=prompt,
        options=item.options,
        answer_key=item.answer_key,
        sequence=item.sequence,
    )
