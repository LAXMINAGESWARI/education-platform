"""Sync generation jobs: outline, items, and lesson. Never publishes.

``generating`` → ``qa_review`` only after BOTH an items job and a lesson job have
succeeded and ``draft_quiz_version_id`` + ``draft_lesson_markdown`` are set.
Items-only or lesson-only success leaves the run in ``generating``. Worker never
writes published source material or released quizzes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError

from education_platform.core.config import get_settings
from education_platform.db.session import sync_session
from education_platform.modules.academics.models import (
    Grade,
    GradeSubjectOffering,
    LearningOutcome,
    PeriodGrade,
    Subtopic,
    Topic,
)
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    Question,
    QuestionAnswerKey,
    QuestionOption,
    QuestionOutcomeTag,
    QuestionType,
    QuestionVersion,
    QuestionVersionStatus,
    QuizItem,
    QuizScope,
    QuizVersion,
    QuizVersionStatus,
)
from education_platform.modules.assessments.question_selection import (
    DEFAULT_ATTEMPT_QUESTION_COUNT,
)
from education_platform.modules.generation.adk import difficulty_for_bloom, item_kind_for_bloom
from education_platform.modules.generation.blind_solve import (
    BlindSolver,
    blind_solve_items,
    blind_solve_record,
    resolve_blind_solver,
)
from education_platform.modules.generation.item_checks import numeric_problems, structural_problems
from education_platform.modules.generation.item_gate import gate_generated_item
from education_platform.modules.generation.items import (
    OPTION_LABELS,
    GeneratedItem,
    ItemsWriter,
    NodeItemRequest,
    OutlineNodeRef,
    bloom_mix_for_quota,
    item_scoring_rubric,
    node_is_heavy,
    texts_for_nodes,
    write_items_for_node,
    write_items_for_nodes,
)
from education_platform.modules.generation.latex_text import (
    bare_latex_outside_delimiters,
    normalize_quiz_math,
)
from education_platform.modules.generation.lesson import (
    LessonSection,
    LessonSectionRequest,
    StitchRequest,
    TopicLessonWriter,
    stitch_sections,
    write_topic_lesson_document,
)
from education_platform.modules.generation.models import (
    ContentGenerationLessonSection,
    ContentGenerationOutlineNode,
    ContentGenerationRun,
    GenerationChangeRequest,
    GenerationJob,
    GenerationRevision,
    ReviewRoundClosure,
    ReviewRoundRow,
)
from education_platform.modules.generation.outline import (
    OutlineWriter,
    SourceSection,
    combined_weight,
    match_existing_subtopics,
    outline_clusters,
    section_group_label,
    source_sections,
    write_outline,
)
from education_platform.modules.generation.revisions import (
    apply_snapshot_to_run_nodes,
    draft_request_from_row,
    install_initial_outline_revision,
    open_review_round,
    persist_content_revision,
    persist_outline_revision,
    replace_run_lesson_sections,
    snapshot_from_json,
)
from education_platform.modules.generation.rewrite import (
    apply_section_notes,
    comments_for_section,
    rewrite_outline_snapshot,
    targeted_item_keys,
    targeted_section_keys,
)
from education_platform.modules.generation.types import (
    ChangeRequest,
    ContentSnapshot,
    GenerationJobKind,
    GenerationJobStatus,
    HeadingCluster,
    HumanActorStamp,
    LessonSectionSnapshot,
    ProposedOutlineNode,
    QuizAnswerKeySnapshot,
    QuizItemSnapshot,
    QuizOptionSnapshot,
    ReviewStage,
    RevisionOrigin,
    RunPhase,
    TeacherRoundNumber,
)
from education_platform.modules.judge.checks import ItemTarget, LessonTarget
from education_platform.modules.judge.evaluate import Evaluation, evaluate_lesson, open_judge
from education_platform.modules.judge.hook import ItemJudgeTracker, blind_fields, remember
from education_platform.modules.judge.lesson_gate import (
    lesson_hold,
    review_lesson_sections,
    source_rejected,
)
from education_platform.modules.judge.policy import judge_mode
from education_platform.modules.judge.protocol import Judge
from education_platform.modules.materials.models import SourceChunk
from education_platform.modules.progress.types import run_subject
from education_platform.modules.progress.wake import publish_wake
from education_platform.workers.lease import Claimed, Exhausted, claim

logger = logging.getLogger(__name__)

_SLICE23_OK = frozenset({RunPhase.GENERATING, RunPhase.QA_REVIEW})


def on_intake_indexed(session: Session, version_id: UUID) -> None:
    """If a run in indexing points at this version: phase=outlining, enqueue outline."""
    run = session.scalar(
        select(ContentGenerationRun).where(
            ContentGenerationRun.intake_source_material_version_id == version_id,
            ContentGenerationRun.phase == RunPhase.INDEXING,
        )
    )
    if run is None:
        return
    run.phase = RunPhase.OUTLINING
    session.add(
        GenerationJob(
            run_id=run.id,
            kind=GenerationJobKind.OUTLINE,
            status=GenerationJobStatus.QUEUED,
        )
    )


def fail_run_for_intake(session: Session, version_id: UUID, reason: str) -> None:
    """If a run in indexing points at this version: phase=failed, copy reason."""
    run = session.scalar(
        select(ContentGenerationRun).where(
            ContentGenerationRun.intake_source_material_version_id == version_id,
            ContentGenerationRun.phase == RunPhase.INDEXING,
        )
    )
    if run is None:
        return
    run.phase = RunPhase.FAILED
    run.failure_reason = reason[:2000]


def claim_next_generation_job(session: Session) -> UUID | None:
    """Claim one queued or stale-running generation job. Exhausted jobs fail the run."""
    result = claim(session, GenerationJob)
    if isinstance(result, Exhausted):
        fail_exhausted_generation_job(session, result.job_id)
        return None
    if not isinstance(result, Claimed):
        return None
    job = session.get(GenerationJob, result.job_id)
    if job is not None:
        publish_wake(session, run_subject(job.run_id))
        session.commit()
    return result.job_id


def fail_exhausted_generation_job(session: Session, job_id: UUID) -> None:
    job = session.get(GenerationJob, job_id)
    times = 0 if job is None else job.attempts
    _fail_job_and_run(session, job_id, f"Worker lost this job {times} times")


def _cancelled(session: Session, run: ContentGenerationRun) -> bool:
    """True when an administrator discarded or removed the run while this job was in flight."""
    try:
        session.refresh(run)
    except ObjectDeletedError:
        return True
    return run.phase is RunPhase.DISCARDED


def process_generation_job_sync(
    job_id: UUID,
    *,
    write_outline: OutlineWriter | None = None,
    write_items: ItemsWriter | None = None,
    write_lesson: TopicLessonWriter | None = None,
    blind_solver: BlindSolver | None = None,
) -> None:
    session = sync_session()
    try:
        job = session.get(GenerationJob, job_id)
        if job is None:
            logger.error("Generation job %s not found", job_id)
            return
        run = session.get(ContentGenerationRun, job.run_id)
        if run is not None and _cancelled(session, run):
            job.status = GenerationJobStatus.FAILED
            job.error = "Cancelled."
            session.commit()
            return
        job.status = GenerationJobStatus.RUNNING
        publish_wake(session, run_subject(job.run_id))
        session.commit()
        if job.kind is GenerationJobKind.OUTLINE:
            _finish_outline(session, job.run_id, writer=write_outline, job_id=job_id)
            _complete_job(session, job_id, ok_phases=frozenset({RunPhase.OUTLINE_REVIEW}))
            return
        if job.kind is GenerationJobKind.REWRITE_OUTLINE:
            _finish_rewrite(session, job)
            _complete_job(session, job_id, ok_phases=frozenset({RunPhase.OUTLINE_REVIEW}))
            return
        if job.kind is GenerationJobKind.REWRITE_CONTENT:
            _finish_content_rewrite(
                session,
                job,
                write_items=write_items,
                write_lesson=write_lesson,
                blind_solver=blind_solver,
            )
            _complete_job(session, job_id, ok_phases=frozenset({RunPhase.QA_REVIEW}))
            return
        if job.kind is GenerationJobKind.ITEMS:
            _finish_items(
                session,
                job.run_id,
                writer=write_items,
                blind_solver=blind_solver,
                job_id=job_id,
            )
            _complete_job(session, job_id, ok_phases=_SLICE23_OK)
            return
        if job.kind is GenerationJobKind.LESSON:
            _finish_lesson(session, job.run_id, writer=write_lesson)
            _complete_job(session, job_id, ok_phases=_SLICE23_OK)
            return
        if job.kind is GenerationJobKind.REGENERATE_ITEMS:
            _finish_regenerate_items(session, job, writer=write_items, blind_solver=blind_solver)
            _complete_job(session, job_id, ok_phases=_SLICE23_OK)
            return
        logger.error("Unsupported generation job kind %s", job.kind)
        reason = f"Unsupported kind in this slice: {job.kind.value}"
        _fail_job_and_run(session, job_id, reason)
    except Exception as exc:
        logger.exception("Generation job %s failed", job_id)
        session.rollback()
        _fail_job_and_run(session, job_id, str(exc)[:2000])
    finally:
        session.close()


def _complete_job(session: Session, job_id: UUID, *, ok_phases: frozenset[RunPhase]) -> None:
    job = session.get(GenerationJob, job_id)
    run = session.get(ContentGenerationRun, job.run_id) if job is not None else None
    if job is None:
        return
    if run is not None and run.phase in ok_phases:
        job.status = GenerationJobStatus.SUCCEEDED
        job.error = None
    else:
        job.status = GenerationJobStatus.FAILED
        failed_reason = "generation job failed"
        if run is not None and run.failure_reason:
            failed_reason = run.failure_reason
        job.error = failed_reason[:2000]
    if run is not None:
        publish_wake(session, run_subject(run.id))
    session.commit()


def _fail_job_and_run(session: Session, job_id: UUID, reason: str) -> None:
    job = session.get(GenerationJob, job_id)
    if job is None:
        return
    job.status = GenerationJobStatus.FAILED
    job.error = reason[:2000]
    run = session.get(ContentGenerationRun, job.run_id)
    if run is not None and not _cancelled(session, run):
        run.phase = RunPhase.FAILED
        run.failure_reason = reason[:2000]
    if job is not None:
        publish_wake(session, run_subject(job.run_id))
    session.commit()


def _finish_outline(
    session: Session, run_id: UUID, *, writer: OutlineWriter | None, job_id: UUID | None = None
) -> None:
    run = session.get(ContentGenerationRun, run_id)
    if run is None or run.intake_source_material_version_id is None:
        return
    chunks = list(
        session.scalars(
            select(SourceChunk)
            .where(SourceChunk.source_material_version_id == run.intake_source_material_version_id)
            .order_by(SourceChunk.ordinal)
        ).all()
    )
    clusters = _cluster_by_heading(chunks)
    if not clusters:
        _fail_run(session, run, "No section headings to outline from")
        return
    try:
        proposed = write_outline(clusters, writer=writer, job_id=job_id)
    except Exception as exc:
        _fail_run(session, run, str(exc)[:2000])
        return
    if not proposed.nodes:
        _fail_run(session, run, "No section headings to outline from")
        return
    existing = list(
        session.scalars(select(Subtopic).where(Subtopic.topic_id == run.topic_id)).all()
    )
    matches = match_existing_subtopics(proposed, existing)
    if _cancelled(session, run):
        return
    _persist_nodes(session, run.id, proposed.nodes, matches)
    run.phase = RunPhase.OUTLINE_REVIEW
    run.failure_reason = None
    install_initial_outline_revision(session, run, job_id=job_id)


def _finish_rewrite(session: Session, job: GenerationJob) -> None:
    run = session.get(ContentGenerationRun, job.run_id)
    if run is None:
        return
    if run.phase is not RunPhase.OUTLINE_REVIEW:
        _fail_run(session, run, "Rewrite is only legal during outline review.")
        return
    if job.close_record_id is None:
        _fail_run(session, run, "Rewrite job is missing its close record.")
        return
    closure = session.get(ReviewRoundClosure, job.close_record_id)
    if closure is None:
        _fail_run(session, run, "Rewrite close record is missing.")
        return
    round_row = session.get(ReviewRoundRow, closure.round_id)
    revision = session.get(GenerationRevision, closure.base_revision_id)
    if round_row is None or revision is None:
        _fail_run(session, run, "Rewrite revision is missing.")
        return
    if TeacherRoundNumber(round_row.number).successor() is None:
        _fail_run(session, run, "Teacher round 2 cannot be rewritten.")
        return
    request_ids = tuple(UUID(str(item)) for item in closure.collated_request_ids)
    requests = _change_requests_for_ids(session, request_ids)
    if not requests:
        _fail_run(session, run, "Rewrite needs at least one change request.")
        return
    snapshot = rewrite_outline_snapshot(snapshot_from_json(revision.snapshot), requests)
    if _cancelled(session, run):
        return
    successor = persist_outline_revision(
        session,
        run_id=run.id,
        snapshot=snapshot,
        origin=RevisionOrigin.COLLATED_REWRITE,
        number=revision.number + 1,
        parent_id=revision.id,
        source_round_id=round_row.id,
        created_by_user_id=None,
        created_by_job_id=job.id,
        created_by_model="rewrite_outline",
    )
    apply_snapshot_to_run_nodes(session, run.id, snapshot)
    open_review_round(session, run=run, revision_id=successor.id, number=2)
    run.phase = RunPhase.OUTLINE_REVIEW
    run.failure_reason = None


def _change_requests_for_ids(
    session: Session, request_ids: tuple[UUID, ...]
) -> tuple[ChangeRequest, ...]:
    if not request_ids:
        return ()
    rows = list(
        session.scalars(
            select(GenerationChangeRequest).where(GenerationChangeRequest.id.in_(request_ids))
        ).all()
    )
    by_id = {row.id: row for row in rows}
    out: list[ChangeRequest] = []
    for request_id in request_ids:
        row = by_id.get(request_id)
        if row is None:
            continue
        draft = draft_request_from_row(row)
        if draft is None:
            continue
        out.append(
            ChangeRequest(
                id=row.id,
                decision_id=row.decision_id,
                revision_id=row.revision_id,
                author=HumanActorStamp(
                    user_id=row.author_user_id,
                    display_name=row.display_name,
                    occurred_at=row.created_at,
                ),
                request=draft,
            )
        )
    return tuple(out)


def _finish_items(
    session: Session,
    run_id: UUID,
    *,
    writer: ItemsWriter | None,
    blind_solver: BlindSolver | None = None,
    job_id: UUID | None = None,
) -> None:
    run = session.get(ContentGenerationRun, run_id)
    if run is None or run.phase is not RunPhase.GENERATING:
        return
    if not run.draft_lesson_markdown or not run.draft_lesson_markdown.strip():
        _fail_run(session, run, "Cannot generate items before the lesson exists.")
        return
    if run.intake_source_material_version_id is None:
        _fail_run(session, run, "Intake version is missing; cannot generate items.")
        return
    nodes = list(
        session.scalars(
            select(ContentGenerationOutlineNode)
            .where(ContentGenerationOutlineNode.run_id == run.id)
            .order_by(ContentGenerationOutlineNode.sequence)
        ).all()
    )
    if not nodes or any(node.accepted_subtopic_id is None or node.quota is None for node in nodes):
        _fail_run(session, run, "Accepted outline nodes are required before item generation.")
        return
    chunks = list(
        session.scalars(
            select(SourceChunk)
            .where(SourceChunk.source_material_version_id == run.intake_source_material_version_id)
            .order_by(SourceChunk.ordinal)
        ).all()
    )
    heading_groups = _heading_text_groups(chunks)
    texts_by_node = texts_for_nodes(_outline_refs(nodes), heading_groups)
    node_count = len(nodes)
    target = run.target_item_count
    expected = sum(node.quota or 0 for node in nodes)
    if expected != run.target_item_count:
        _fail_run(
            session,
            run,
            f"Frozen quotas sum to {expected}; expected {run.target_item_count}.",
        )
        return
    quiz_version = _draft_topic_quiz_version(session, run)
    _clear_draft_items(session, quiz_version.id)
    run.draft_quiz_version_id = quiz_version.id
    requests: list[NodeItemRequest] = []
    for node in nodes:
        quota = node.quota or 0
        if quota <= 0:
            continue
        chunk_texts = texts_by_node.get(node.id, ())
        if not chunk_texts:
            _fail_run(
                session,
                run,
                f"No source chunks for outline node {node.slug}; will not dump the whole PDF.",
            )
            return
        subtopic_id = node.accepted_subtopic_id
        if subtopic_id is None:
            _fail_run(session, run, "Accepted outline nodes are required before item generation.")
            return
        requests.append(
            NodeItemRequest(
                subtopic_id=subtopic_id,
                learning_outcome_ids=_outcome_ids_for_node(session, node),
                heading=node.title,
                chunk_texts=chunk_texts,
                quota=quota,
                bloom=bloom_mix_for_quota(
                    quota,
                    heavy=node_is_heavy(quota, node_count=node_count, target=target),
                ),
                outcome_statements=_outcome_statements(node),
            )
        )
    solver = resolve_blind_solver(blind_solver)
    judge = _open_item_judge()
    grade_voice = _grade_voice_for_topic(session, run.topic_id)
    sequence = 1
    considered = 0
    stats = {"passed": 0, "fixed_on_retry": 0, "dropped": 0}
    taken = _topic_prompts(session, run.topic_id)
    lesson_markdown = run.draft_lesson_markdown or ""
    if writer is not None:
        for request in requests:
            try:
                produced = write_items_for_node(
                    request,
                    writer=writer,
                    job_id=run.id,
                    lesson_markdown=lesson_markdown,
                )
            except Exception as exc:
                _fail_run(session, run, str(exc)[:2000])
                return
            sequence = _persist_produced(
                session,
                run.id,
                quiz_version.id,
                produced,
                sequence,
                solver=solver,
                writer=writer,
                request=request,
                lesson_markdown=lesson_markdown,
                job_id=job_id,
                taken=taken,
                stats=stats,
                judge=judge,
                grade_voice=grade_voice,
            )
            considered += len(produced)
            session.flush()
    else:
        try:
            grouped = write_items_for_nodes(
                requests,
                writer=None,
                job_id=run.id,
                lesson_markdown=lesson_markdown,
            )
        except Exception as exc:
            _fail_run(session, run, str(exc)[:2000])
            return
        for request, produced in zip(requests, grouped, strict=True):
            sequence = _persist_produced(
                session,
                run.id,
                quiz_version.id,
                produced,
                sequence,
                solver=solver,
                writer=None,
                request=request,
                lesson_markdown=lesson_markdown,
                job_id=job_id,
                taken=taken,
                stats=stats,
                judge=judge,
                grade_voice=grade_voice,
            )
            considered += len(produced)
            session.flush()
    if considered != expected:
        _fail_run(
            session,
            run,
            f"Item writer produced {considered} items; expected {expected}.",
        )
        return
    _store_item_validation(session, job_id, stats)
    run.failure_reason = None
    _maybe_advance_to_qa_review(session, run, completing=GenerationJobKind.ITEMS)


def _finish_lesson(session: Session, run_id: UUID, *, writer: TopicLessonWriter | None) -> None:
    run = session.get(ContentGenerationRun, run_id)
    if run is None or run.phase is not RunPhase.GENERATING:
        return
    if run.intake_source_material_version_id is None:
        _fail_run(session, run, "Intake version is missing; cannot generate a lesson.")
        return
    nodes = list(
        session.scalars(
            select(ContentGenerationOutlineNode)
            .where(ContentGenerationOutlineNode.run_id == run.id)
            .order_by(ContentGenerationOutlineNode.sequence)
        ).all()
    )
    if not nodes or any(node.accepted_subtopic_id is None for node in nodes):
        _fail_run(session, run, "Accepted outline nodes are required before lesson generation.")
        return
    chunks = list(
        session.scalars(
            select(SourceChunk)
            .where(SourceChunk.source_material_version_id == run.intake_source_material_version_id)
            .order_by(SourceChunk.ordinal)
        ).all()
    )
    heading_groups = _heading_text_groups(chunks)
    texts_by_node = texts_for_nodes(_outline_refs(nodes), heading_groups)
    grade_voice = _grade_voice_for_topic(session, run.topic_id)
    container_ids = {node.parent_id for node in nodes if node.parent_id is not None}
    section_requests: list[LessonSectionRequest] = []
    lesson_nodes: list[ContentGenerationOutlineNode] = []
    for node in nodes:
        chunk_texts = texts_by_node.get(node.id, ())
        if not chunk_texts:
            if node.id in container_ids:
                continue
            _fail_run(
                session,
                run,
                f"No source chunks for outline node {node.slug}; will not dump the whole PDF.",
            )
            return
        lesson_nodes.append(node)
        objectives = tuple(
            str(item).strip()
            for item in (node.proposed_outcomes or [])
            if isinstance(item, str) and item.strip()
        )
        section_requests.append(
            LessonSectionRequest(
                heading=node.title,
                objectives=objectives,
                chunk_texts=chunk_texts,
                grade_voice=grade_voice,
                glossary=(),
                prior_titles=(),
                defined_terms=(),
                prior_recap="",
            )
        )
    try:
        written = write_topic_lesson_document(section_requests, write_section=writer, job_id=run.id)
    except Exception as exc:
        _fail_run(session, run, str(exc)[:2000])
        return
    if run.phase is not RunPhase.GENERATING:
        return
    markdown, judged_sections = _apply_lesson_judge(
        session,
        run,
        lesson_nodes,
        written.sections,
        texts_by_node,
        grade_voice,
        writer=writer,
        job_id=None,
    )
    snapshots = tuple(
        LessonSectionSnapshot(
            section_key=node.id,
            heading=section.heading,
            markdown=section.markdown,
            sequence=node.sequence,
        )
        for node, section in zip(lesson_nodes, judged_sections, strict=True)
    )
    replace_run_lesson_sections(session, run.id, snapshots)
    run.draft_lesson_markdown = markdown
    run.failure_reason = None
    _enqueue_generation_job(session, run.id, GenerationJobKind.ITEMS)
    _maybe_advance_to_qa_review(session, run, completing=GenerationJobKind.LESSON)


def _enqueue_generation_job(session: Session, run_id: UUID, kind: GenerationJobKind) -> None:
    """Sync twin of the service helper. Skip when a live or succeeded job exists."""
    existing = session.scalar(
        select(GenerationJob.id).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == kind,
            GenerationJob.status.in_(
                (
                    GenerationJobStatus.QUEUED,
                    GenerationJobStatus.RUNNING,
                    GenerationJobStatus.SUCCEEDED,
                )
            ),
        )
    )
    if existing is not None:
        return
    try:
        with session.begin_nested():
            session.add(
                GenerationJob(
                    run_id=run_id,
                    kind=kind,
                    status=GenerationJobStatus.QUEUED,
                )
            )
            session.flush()
    except IntegrityError:
        return


def _maybe_advance_to_qa_review(
    session: Session, run: ContentGenerationRun, *, completing: GenerationJobKind
) -> None:
    """HITL 2 opens only after both draft products exist. Never publishes."""
    if _cancelled(session, run) or run.phase is not RunPhase.GENERATING:
        return
    if not run.draft_lesson_markdown or run.draft_quiz_version_id is None:
        return
    items_ok = completing in {
        GenerationJobKind.ITEMS,
        GenerationJobKind.REGENERATE_ITEMS,
    } or _has_succeeded_job(session, run.id, GenerationJobKind.ITEMS)
    lesson_ok = completing is GenerationJobKind.LESSON or _has_succeeded_job(
        session, run.id, GenerationJobKind.LESSON
    )
    if items_ok and lesson_ok:
        opened = install_initial_content_revision(session, run, job_id=None)
        if opened is None:
            existing = session.scalar(
                select(GenerationRevision.id).where(
                    GenerationRevision.run_id == run.id,
                    GenerationRevision.stage == ReviewStage.QA,
                )
            )
            if existing is None:
                _fail_run(
                    session,
                    run,
                    "Could not snapshot lesson sections and items for QA review.",
                )
                return
        run.phase = RunPhase.QA_REVIEW
        run.failure_reason = None


def _has_succeeded_job(session: Session, run_id: UUID, kind: GenerationJobKind) -> bool:
    job_id = session.scalar(
        select(GenerationJob.id).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == kind,
            GenerationJob.status == GenerationJobStatus.SUCCEEDED,
        )
    )
    return job_id is not None


def _grade_voice_for_topic(session: Session, topic_id: UUID) -> str:
    row = session.execute(
        select(Grade.name, Topic.name)
        .select_from(Topic)
        .join(
            GradeSubjectOffering,
            GradeSubjectOffering.id == Topic.grade_subject_offering_id,
        )
        .join(PeriodGrade, PeriodGrade.id == GradeSubjectOffering.period_grade_id)
        .join(Grade, Grade.id == PeriodGrade.grade_id)
        .where(Topic.id == topic_id)
    ).first()
    if row is None:
        return "Write for school students in clear, plain language."
    grade_name, topic_name = row
    return f"Write for {grade_name} students studying {topic_name}. Use plain language."


def _draft_topic_quiz_version(session: Session, run: ContentGenerationRun) -> QuizVersion:
    quiz = session.scalar(
        select(CommonMasteryQuiz).where(
            CommonMasteryQuiz.topic_id == run.topic_id,
            CommonMasteryQuiz.quiz_scope == QuizScope.TOPIC_MASTERY,
        )
    )
    if quiz is None:
        topic = session.get(Topic, run.topic_id)
        title = f"{topic.name} topic mastery" if topic is not None else "Topic mastery"
        quiz = CommonMasteryQuiz(
            quiz_scope=QuizScope.TOPIC_MASTERY,
            topic_id=run.topic_id,
            title=title,
        )
        session.add(quiz)
        session.flush()
    if run.draft_quiz_version_id is not None:
        existing = session.get(QuizVersion, run.draft_quiz_version_id)
        if existing is not None and existing.lifecycle_status is QuizVersionStatus.DRAFT:
            if existing.attempt_question_count is None:
                existing.attempt_question_count = DEFAULT_ATTEMPT_QUESTION_COUNT
            return existing
    version = QuizVersion(
        quiz_id=quiz.id,
        version_number=_next_quiz_version_number(session, quiz.id),
        lifecycle_status=QuizVersionStatus.DRAFT,
        attempt_question_count=DEFAULT_ATTEMPT_QUESTION_COUNT,
    )
    session.add(version)
    session.flush()
    return version


def _next_quiz_version_number(session: Session, quiz_id: UUID) -> int:
    current = session.scalar(
        select(func.max(QuizVersion.version_number)).where(QuizVersion.quiz_id == quiz_id)
    )
    return int(current or 0) + 1


def _clear_draft_items(session: Session, quiz_version_id: UUID) -> None:
    existing = list(
        session.scalars(select(QuizItem).where(QuizItem.quiz_version_id == quiz_version_id)).all()
    )
    question_version_ids = [row.question_version_id for row in existing]
    if question_version_ids:
        session.execute(delete(QuizItem).where(QuizItem.quiz_version_id == quiz_version_id))
        session.execute(
            delete(QuestionOutcomeTag).where(
                QuestionOutcomeTag.question_version_id.in_(tuple(question_version_ids))
            )
        )
        session.execute(
            delete(QuestionAnswerKey).where(
                QuestionAnswerKey.question_version_id.in_(tuple(question_version_ids))
            )
        )
        session.execute(
            delete(QuestionOption).where(
                QuestionOption.question_version_id.in_(tuple(question_version_ids))
            )
        )
        question_ids = list(
            session.scalars(
                select(QuestionVersion.question_id).where(
                    QuestionVersion.id.in_(tuple(question_version_ids))
                )
            ).all()
        )
        session.execute(
            delete(QuestionVersion).where(QuestionVersion.id.in_(tuple(question_version_ids)))
        )
        if question_ids:
            session.execute(delete(Question).where(Question.id.in_(tuple(question_ids))))
        session.flush()


def _rubric_with_blind_solve(
    item: GeneratedItem,
    blind_solve: dict[str, object] | None,
    item_check: dict[str, object] | None = None,
) -> dict[str, object]:
    rubric = item_scoring_rubric(item.bloom, item.misconception_labels)
    if blind_solve is not None:
        rubric["blind_solve"] = blind_solve
    if item_check is not None:
        rubric["item_check"] = item_check
    return rubric


def _topic_prompts(session: Session, topic_id: UUID) -> list[str]:
    """Stems already in this topic's bank, so a new run does not repeat them."""
    rows = session.scalars(
        select(QuestionVersion.prompt)
        .join(Question, Question.id == QuestionVersion.question_id)
        .join(Subtopic, Subtopic.id == Question.subtopic_id)
        .where(
            Subtopic.topic_id == topic_id,
            QuestionVersion.lifecycle_status != QuestionVersionStatus.ARCHIVED,
        )
    ).all()
    return [prompt for prompt in rows if prompt]


def _store_item_validation(session: Session, job_id: UUID | None, stats: dict[str, int]) -> None:
    if job_id is None:
        return
    job = session.get(GenerationJob, job_id)
    if job is None:
        return
    payload = dict(job.payload) if isinstance(job.payload, dict) else {}
    payload["item_validation"] = dict(stats)
    job.payload = payload


def _refuse_unshippable(item: GeneratedItem, *, archive: bool) -> None:
    """Save-time backstop. Archived drops are kept for QA and are not served."""
    if archive:
        return
    reasons = list(structural_problems(item.options, item.correct_label, item.correct_rationale))
    reasons.extend(numeric_problems(item.options, item.correct_label, item.correct_rationale))
    if reasons:
        raise ValueError("Refusing to save an invalid multiple-choice item: " + "; ".join(reasons))


def _blind_records(
    items: Sequence[GeneratedItem], solver: BlindSolver | None
) -> list[dict[str, object] | None]:
    if solver is None or not items:
        return [None] * len(items)
    verdicts = blind_solve_items(items, solver)
    model = get_settings().blind_solver_model
    return [
        blind_solve_record(verdict, correct_label=item.correct_label, model=model)
        for item, verdict in zip(items, verdicts, strict=True)
    ]


def _outcome_statements(node: ContentGenerationOutlineNode) -> tuple[str, ...]:
    return tuple(
        str(item).strip()
        for item in (node.proposed_outcomes or [])
        if isinstance(item, str) and item.strip()
    )


def _open_item_judge() -> Judge | None:
    try:
        return open_judge()
    except Exception:
        logger.warning("Jev judge is unavailable", exc_info=True)
        return None


def _item_tracker(
    judge: Judge | None,
    request: NodeItemRequest,
    grade_voice: str,
    mode: str,
) -> ItemJudgeTracker | None:
    if judge is None or mode == "off":
        return None

    def target_for(item: GeneratedItem) -> ItemTarget:
        return _item_target(item, request, grade_voice)

    return ItemJudgeTracker(judge, target_for, enforce=mode == "enforce")


def _item_target(item: GeneratedItem, request: NodeItemRequest, grade_voice: str) -> ItemTarget:
    return ItemTarget(
        heading=request.heading,
        grade=grade_voice,
        outcomes=request.outcome_statements,
        chunks=request.chunk_texts,
        prompt=item.prompt,
        options={label: item.options.get(label, "") for label in OPTION_LABELS},
        correct_label=item.correct_label,
        correct_rationale=item.correct_rationale,
        difficulty=difficulty_for_bloom(item.bloom).value,
    )


def _apply_lesson_judge(
    session: Session,
    run: ContentGenerationRun,
    nodes: Sequence[ContentGenerationOutlineNode],
    sections: Sequence[LessonSection],
    texts_by_node: Mapping[UUID, tuple[str, ...]],
    grade_voice: str,
    *,
    writer: TopicLessonWriter | None,
    job_id: UUID | None,
) -> tuple[str, tuple[LessonSection, ...]]:
    """Judge sections, rewrite a source reject once, and return stitched markdown."""
    targets = tuple(
        LessonTarget(
            heading=section.heading,
            grade=grade_voice,
            outcomes=_outcome_statements(node),
            chunks=texts_by_node.get(node.id, ()),
            markdown=section.markdown,
            kind="lesson_section",
        )
        for node, section in zip(nodes, sections, strict=True)
    )
    judge = _open_item_judge()
    mode = judge_mode()
    reviews, held = review_lesson_sections(
        targets,
        judge,
        mode=mode,
        rewrite=_section_rewriter(writer, job_id=job_id) if mode == "enforce" else None,
    )
    updated = tuple(
        replace(section, heading=review.target.heading, markdown=review.target.markdown)
        for section, review in zip(sections, reviews, strict=True)
    )
    markdown = stitch_sections(StitchRequest(grade_voice=grade_voice, sections=updated))
    for node, review in zip(nodes, reviews, strict=True):
        _store_lesson_evaluation(
            session,
            run,
            review.evaluation,
            subject_key=node.id,
            job_id=job_id,
            mode=mode,
        )
    whole = _whole_lesson_evaluation(
        run,
        judge,
        grade_voice=grade_voice,
        markdown=markdown,
        reviews=reviews,
        mode=mode,
    )
    if whole is not None and source_rejected(whole):
        held = True
    if held:
        reasons = tuple(
            reason
            for review in reviews
            if review.evaluation is not None and source_rejected(review.evaluation)
            for reason in (review.evaluation.gate_reasons or review.evaluation.reasons)
        )
        if whole is not None and source_rejected(whole):
            reasons = reasons + (whole.gate_reasons or whole.reasons)
        if not reasons:
            reasons = ("A section stayed ungrounded or off the source after one rewrite.",)
        model = whole.model if whole is not None else None
        _store_lesson_evaluation(
            session,
            run,
            lesson_hold(reasons, model=model),
            subject_key=run.id,
            job_id=job_id,
            mode=mode,
        )
    else:
        _store_lesson_evaluation(session, run, whole, subject_key=run.id, job_id=job_id, mode=mode)
    return markdown, updated


def _record_repaired_lesson(
    session: Session,
    run: ContentGenerationRun,
    snapshots: Sequence[LessonSectionSnapshot],
    texts_by_node: Mapping[UUID, tuple[str, ...]],
    grade_voice: str,
    writer: TopicLessonWriter | None,
) -> None:
    if _open_item_judge() is None or judge_mode() == "off":
        return
    nodes = {
        node.id: node
        for node in session.scalars(
            select(ContentGenerationOutlineNode).where(
                ContentGenerationOutlineNode.run_id == run.id
            )
        ).all()
    }
    ordered: list[ContentGenerationOutlineNode] = []
    sections: list[LessonSection] = []
    for snapshot in snapshots:
        node = nodes.get(snapshot.section_key)
        if node is None:
            return
        ordered.append(node)
        sections.append(
            LessonSection(
                heading=snapshot.heading,
                markdown=snapshot.markdown,
                recap=snapshot.heading,
                defined_terms=(),
            )
        )
    markdown, updated = _apply_lesson_judge(
        session,
        run,
        ordered,
        tuple(sections),
        texts_by_node,
        grade_voice,
        writer=writer,
        job_id=None,
    )
    replace_run_lesson_sections(
        session,
        run.id,
        tuple(
            LessonSectionSnapshot(
                section_key=node.id,
                heading=section.heading,
                markdown=section.markdown,
                sequence=snapshot.sequence,
            )
            for node, section, snapshot in zip(ordered, updated, snapshots, strict=True)
        ),
    )
    run.draft_lesson_markdown = markdown


def _whole_lesson_evaluation(
    run: ContentGenerationRun,
    judge: Judge | None,
    *,
    grade_voice: str,
    markdown: str,
    reviews: Sequence[object],
    mode: str,
) -> Evaluation | None:
    if judge is None or mode == "off" or not markdown.strip():
        return None
    outcomes: list[str] = []
    chunks: list[str] = []
    for review in reviews:
        target = getattr(review, "target", None)
        if target is None:
            continue
        outcomes.extend(target.outcomes)
        chunks.extend(target.chunks)
    try:
        return evaluate_lesson(
            LessonTarget(
                heading=run.title,
                grade=grade_voice,
                outcomes=tuple(dict.fromkeys(outcomes)),
                chunks=tuple(dict.fromkeys(chunks)),
                markdown=markdown,
                kind="lesson",
            ),
            judge,
            include_claims=False,
        )
    except Exception:
        logger.warning("Jev whole-lesson judge failed open", exc_info=True)
        return None


def _section_rewriter(
    writer: TopicLessonWriter | None, *, job_id: UUID | None
) -> Callable[[LessonTarget, tuple[str, ...]], str | None]:
    def rewrite(target: LessonTarget, reasons: tuple[str, ...]) -> str | None:
        note = "Jev rejected this section as ungrounded or off the source. Rewrite it. " + " ".join(
            reasons
        )
        request = LessonSectionRequest(
            heading=target.heading,
            objectives=target.outcomes,
            chunk_texts=target.chunks,
            grade_voice=target.grade,
            glossary=(),
            prior_titles=(),
            defined_terms=(),
            prior_recap="",
            repair_note=note,
        )
        try:
            produced = write_topic_lesson_document((request,), write_section=writer, job_id=job_id)
        except Exception:
            logger.warning("Jev section rewrite failed open", exc_info=True)
            return None
        if not produced.sections:
            return None
        return produced.sections[0].markdown

    return rewrite


def _store_lesson_evaluation(
    session: Session,
    run: ContentGenerationRun,
    evaluation: Evaluation | None,
    *,
    subject_key: UUID,
    job_id: UUID | None,
    mode: str,
) -> None:
    remember(
        session,
        evaluation,
        run_id=run.id,
        generation_job_id=job_id,
        subject_key=subject_key,
        question_version_id=None,
        gate_verdict=None,
        gate_servable=None,
        blind_status=None,
        blind_answer=None,
        mode=mode,
    )


def _persist_produced(
    session: Session,
    run_id: UUID,
    quiz_version_id: UUID,
    produced: Sequence[GeneratedItem],
    sequence: int,
    *,
    solver: BlindSolver | None,
    writer: ItemsWriter | None,
    request: NodeItemRequest,
    lesson_markdown: str,
    job_id: UUID | None,
    taken: list[str],
    stats: dict[str, int],
    judge: Judge | None = None,
    grade_voice: str = "",
) -> int:
    mode = judge_mode()
    tracker = _item_tracker(judge, request, grade_voice, mode)
    for item in produced:
        gated = gate_generated_item(
            item,
            solve=_solver_for(solver) if solver is not None else None,
            rewrite=_rewriter(
                writer,
                request,
                lesson_markdown=lesson_markdown,
                job_id=job_id,
            ),
            taken=taken,
            judge=tracker,
            enforce_judge=mode == "enforce",
        )
        evaluation = tracker.evaluation if tracker is not None else None
        stats[gated.verdict] = stats.get(gated.verdict, 0) + 1
        version = _persist_generated_item(
            session,
            quiz_version_id,
            gated.item,
            sequence,
            blind_solve=gated.blind_solve,
            item_check=gated.rubric(),
            lifecycle_status=(
                QuestionVersionStatus.DRAFT if gated.servable else QuestionVersionStatus.ARCHIVED
            ),
        )
        status, answer = blind_fields(gated.blind_solve)
        remember(
            session,
            evaluation,
            run_id=run_id,
            generation_job_id=job_id,
            subject_key=version.id,
            question_version_id=version.id,
            gate_verdict=gated.verdict,
            gate_servable=gated.servable,
            blind_status=status,
            blind_answer=answer,
        )
        sequence += 1
    return sequence


def _solver_for(solver: BlindSolver) -> Callable[[GeneratedItem], dict[str, object] | None]:
    def solve(item: GeneratedItem) -> dict[str, object] | None:
        records = _blind_records((item,), solver)
        return records[0]

    return solve


def _rewriter(
    writer: ItemsWriter | None,
    request: NodeItemRequest,
    *,
    lesson_markdown: str,
    job_id: UUID | None,
) -> Callable[[GeneratedItem, str], GeneratedItem | None]:
    def rewrite(item: GeneratedItem, note: str) -> GeneratedItem | None:
        repair = NodeItemRequest(
            subtopic_id=item.subtopic_id,
            learning_outcome_ids=item.learning_outcome_ids,
            heading=request.heading,
            chunk_texts=request.chunk_texts,
            quota=1,
            bloom=(item.bloom,),
            repair_note=note,
        )
        try:
            produced = write_items_for_node(
                repair,
                writer=writer,
                job_id=job_id,
                lesson_markdown=lesson_markdown,
            )
        except Exception:
            logger.warning("Item rewrite failed", exc_info=True)
            return None
        if not produced:
            return None
        return produced[0]

    return rewrite


def _persist_generated_item(
    session: Session,
    quiz_version_id: UUID,
    item: GeneratedItem,
    sequence: int,
    *,
    blind_solve: dict[str, object] | None = None,
    item_check: dict[str, object] | None = None,
    lifecycle_status: QuestionVersionStatus = QuestionVersionStatus.DRAFT,
) -> QuestionVersion:
    _refuse_unshippable(item, archive=lifecycle_status is QuestionVersionStatus.ARCHIVED)
    item = _item_for_storage(item)
    question = Question(subtopic_id=item.subtopic_id)
    session.add(question)
    session.flush()
    version = QuestionVersion(
        question_id=question.id,
        version_number=1,
        prompt=item.prompt,
        question_type=QuestionType.MULTIPLE_CHOICE,
        difficulty=difficulty_for_bloom(item.bloom),
        item_kind=item.item_kind or item_kind_for_bloom(item.bloom),
        lifecycle_status=lifecycle_status,
    )
    session.add(version)
    session.flush()
    for option_sequence, label in enumerate(OPTION_LABELS):
        session.add(
            QuestionOption(
                question_version_id=version.id,
                label=label,
                text=item.options[label],
                sequence=option_sequence,
            )
        )
    session.add(
        QuestionAnswerKey(
            question_version_id=version.id,
            correct_option_label=item.correct_label,
            correct_rationale=item.correct_rationale,
            distractor_rationales=dict(item.distractor_rationales),
            scoring_rubric=_rubric_with_blind_solve(item, blind_solve, item_check),
        )
    )
    for outcome_id in item.learning_outcome_ids:
        session.add(
            QuestionOutcomeTag(question_version_id=version.id, learning_outcome_id=outcome_id)
        )
    session.add(
        QuizItem(
            quiz_version_id=quiz_version_id,
            question_version_id=version.id,
            sequence=sequence,
        )
    )
    return version


def _outcome_ids_for_node(session: Session, node: ContentGenerationOutlineNode) -> tuple[UUID, ...]:
    if node.accepted_subtopic_id is None:
        return ()
    rows = list(
        session.scalars(
            select(LearningOutcome)
            .where(LearningOutcome.subtopic_id == node.accepted_subtopic_id)
            .order_by(LearningOutcome.sequence)
        ).all()
    )
    wanted = {
        str(item).strip().casefold()
        for item in (node.proposed_outcomes or [])
        if isinstance(item, str) and item.strip()
    }
    matched = tuple(row.id for row in rows if row.statement.strip().casefold() in wanted)
    if matched:
        return matched
    return tuple(row.id for row in rows[:3])


def _source_sections(chunks: Sequence[SourceChunk]) -> list[SourceSection]:
    return source_sections(
        (chunk.section_heading, chunk.text, chunk.token_count or len(chunk.text.split()))
        for chunk in chunks
    )


def _heading_text_groups(chunks: Sequence[SourceChunk]) -> list[tuple[str, tuple[str, ...]]]:
    """One group per source section, in PDF order. Labels are the mapping keys."""
    return [(section_group_label(section), section.texts) for section in _source_sections(chunks)]


def _outline_refs(nodes: Sequence[ContentGenerationOutlineNode]) -> tuple[OutlineNodeRef, ...]:
    titles = {node.id: node.title for node in nodes}
    return tuple(
        OutlineNodeRef(
            id=node.id,
            slug=node.slug,
            title=node.title,
            sequence=node.sequence,
            parent_title=titles.get(node.parent_id) if node.parent_id is not None else None,
            source_headings=_stored_source_headings(node.source_headings),
        )
        for node in nodes
    )


def _stored_source_headings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())


def _persist_nodes(
    session: Session,
    run_id: UUID,
    nodes: Sequence[ProposedOutlineNode],
    matches: Sequence[UUID | None],
) -> None:
    session.execute(
        delete(ContentGenerationOutlineNode).where(ContentGenerationOutlineNode.run_id == run_id)
    )
    session.flush()
    ids = {node.key: uuid4() for node in nodes}
    used_slugs: set[str] = set()
    pending: list[tuple[ContentGenerationOutlineNode, str | None]] = []
    for index, node in enumerate(nodes):
        slug = node.slug
        if slug in used_slugs:
            suffix = 2
            while f"{node.slug}-{suffix}" in used_slugs:
                suffix += 1
            slug = f"{node.slug}-{suffix}"
        used_slugs.add(slug)
        outcomes = list(node.proposed_outcomes[:3])
        if not outcomes:
            outcomes = [node.title]
        parent_id = None
        row = ContentGenerationOutlineNode(
            id=ids[node.key],
            run_id=run_id,
            parent_id=parent_id,
            slug=slug,
            title=node.title,
            token_mass=node.token_mass,
            prerequisite_score=node.prerequisite_score,
            centrality=node.centrality,
            weight=combined_weight(
                token_mass=node.token_mass,
                prerequisite_score=node.prerequisite_score,
                centrality=node.centrality,
            ),
            quota=None,
            matched_subtopic_id=matches[index],
            force_create=False,
            accepted_subtopic_id=None,
            proposed_outcomes=outcomes,
            source_headings=list(node.source_headings),
            sequence=index + 1,
        )
        session.add(row)
        pending.append((row, node.parent_key))
    session.flush()
    for row, parent_key in pending:
        if parent_key is not None:
            row.parent_id = ids[parent_key]
    session.flush()


def _cluster_by_heading(chunks: Sequence[SourceChunk]) -> list[HeadingCluster]:
    return outline_clusters(_source_sections(chunks))


def _fail_run(session: Session, run: ContentGenerationRun, reason: str) -> None:
    if _cancelled(session, run):
        return
    run.phase = RunPhase.FAILED
    run.failure_reason = reason[:2000]


def install_initial_content_revision(
    session: Session, run: ContentGenerationRun, *, job_id: UUID | None
) -> ReviewRoundRow | None:
    existing = session.scalar(
        select(GenerationRevision.id).where(
            GenerationRevision.run_id == run.id,
            GenerationRevision.stage == ReviewStage.QA,
            GenerationRevision.number == 1,
        )
    )
    if existing is not None:
        return session.scalar(
            select(ReviewRoundRow).where(
                ReviewRoundRow.run_id == run.id,
                ReviewRoundRow.stage == ReviewStage.QA,
                ReviewRoundRow.number == 1,
            )
        )
    snapshot = _content_snapshot_from_drafts(session, run)
    if snapshot is None:
        return None
    revision = persist_content_revision(
        session,
        run_id=run.id,
        snapshot=snapshot,
        origin=RevisionOrigin.INITIAL_GENERATION,
        number=1,
        parent_id=None,
        source_round_id=None,
        created_by_user_id=None,
        created_by_job_id=job_id or run.id,
        created_by_model="content",
    )
    return open_review_round(
        session, run=run, revision_id=revision.id, number=1, stage=ReviewStage.QA
    )


def _finish_regenerate_items(
    session: Session,
    job: GenerationJob,
    *,
    writer: ItemsWriter | None,
    blind_solver: BlindSolver | None = None,
) -> None:
    run = session.get(ContentGenerationRun, job.run_id)
    if run is None or run.phase not in {RunPhase.GENERATING, RunPhase.QA_REVIEW}:
        return
    payload = job.payload if isinstance(job.payload, dict) else {}
    raw_ids = payload.get("question_ids")
    question_ids = tuple(UUID(str(item)) for item in raw_ids) if isinstance(raw_ids, list) else ()
    if not question_ids:
        _fail_run(session, run, "Selective item repair requires question ids.")
        return
    kept = _replace_selected_items(
        session, run, question_ids, writer=writer, blind_solver=blind_solver
    )
    if kept is None or _cancelled(session, run):
        return
    run.failure_reason = None
    if run.phase is RunPhase.GENERATING:
        _maybe_advance_to_qa_review(session, run, completing=GenerationJobKind.REGENERATE_ITEMS)
        return
    run.phase = RunPhase.QA_REVIEW


def _finish_content_rewrite(
    session: Session,
    job: GenerationJob,
    *,
    write_items: ItemsWriter | None,
    write_lesson: TopicLessonWriter | None,
    blind_solver: BlindSolver | None = None,
) -> None:
    run = session.get(ContentGenerationRun, job.run_id)
    if run is None:
        return
    if run.phase is not RunPhase.QA_REVIEW:
        _fail_run(session, run, "Content rewrite is only legal during QA review.")
        return
    if job.close_record_id is None:
        _fail_run(session, run, "Rewrite job is missing its close record.")
        return
    closure = session.get(ReviewRoundClosure, job.close_record_id)
    if closure is None:
        _fail_run(session, run, "Rewrite close record is missing.")
        return
    round_row = session.get(ReviewRoundRow, closure.round_id)
    revision = session.get(GenerationRevision, closure.base_revision_id)
    if round_row is None or revision is None or revision.stage is not ReviewStage.QA:
        _fail_run(session, run, "Rewrite revision is missing.")
        return
    if TeacherRoundNumber(round_row.number).successor() is None:
        _fail_run(session, run, "Teacher round 2 cannot be rewritten.")
        return
    request_ids = tuple(UUID(str(item)) for item in closure.collated_request_ids)
    requests = _change_requests_for_ids(session, request_ids)
    if not requests:
        _fail_run(session, run, "Rewrite needs at least one change request.")
        return
    section_keys = targeted_section_keys(requests)
    item_keys = targeted_item_keys(requests)
    if section_keys:
        repaired = _repair_lesson_sections(
            session, run, section_keys, requests, writer=write_lesson
        )
        if not repaired:
            return
    if item_keys:
        replaced = _replace_selected_items(
            session, run, item_keys, writer=write_items, blind_solver=blind_solver
        )
        if replaced is None:
            return
    snapshot = _content_snapshot_from_drafts(session, run)
    if snapshot is None:
        _fail_run(session, run, "Could not snapshot repaired lesson and items.")
        return
    if _cancelled(session, run):
        return
    successor = persist_content_revision(
        session,
        run_id=run.id,
        snapshot=snapshot,
        origin=RevisionOrigin.COLLATED_REWRITE,
        number=revision.number + 1,
        parent_id=revision.id,
        source_round_id=round_row.id,
        created_by_user_id=None,
        created_by_job_id=job.id,
        created_by_model="rewrite_content",
    )
    open_review_round(session, run=run, revision_id=successor.id, number=2, stage=ReviewStage.QA)
    run.phase = RunPhase.QA_REVIEW
    run.failure_reason = None


def _repair_lesson_sections(
    session: Session,
    run: ContentGenerationRun,
    section_keys: frozenset[UUID],
    requests: tuple[ChangeRequest, ...],
    *,
    writer: TopicLessonWriter | None,
) -> bool:
    nodes = list(
        session.scalars(
            select(ContentGenerationOutlineNode)
            .where(ContentGenerationOutlineNode.run_id == run.id)
            .order_by(ContentGenerationOutlineNode.sequence)
        ).all()
    )
    rows = list(
        session.scalars(
            select(ContentGenerationLessonSection)
            .where(ContentGenerationLessonSection.run_id == run.id)
            .order_by(ContentGenerationLessonSection.sequence)
        ).all()
    )
    if not rows:
        _fail_run(
            session, run, "Lesson sections are missing; cannot repair from stitched markdown."
        )
        return False
    chunks = []
    if run.intake_source_material_version_id is not None:
        chunks = list(
            session.scalars(
                select(SourceChunk)
                .where(
                    SourceChunk.source_material_version_id == run.intake_source_material_version_id
                )
                .order_by(SourceChunk.ordinal)
            ).all()
        )
    heading_groups = _heading_text_groups(chunks)
    texts_by_node = texts_for_nodes(_outline_refs(nodes), heading_groups) if nodes else {}
    grade_voice = _grade_voice_for_topic(session, run.topic_id)
    snapshots: list[LessonSectionSnapshot] = []
    stitch_sections_in: list[LessonSection] = []
    for row in rows:
        markdown = row.markdown
        heading = row.heading
        if row.outline_node_id in section_keys:
            notes = comments_for_section(requests, row.outline_node_id)
            node = next((item for item in nodes if item.id == row.outline_node_id), None)
            if writer is not None and node is not None:
                chunk_texts = texts_by_node.get(node.id, ())
                request = LessonSectionRequest(
                    heading=node.title,
                    objectives=tuple(
                        str(item).strip()
                        for item in (node.proposed_outcomes or [])
                        if isinstance(item, str) and item.strip()
                    ),
                    chunk_texts=chunk_texts or (node.title,),
                    grade_voice=grade_voice,
                    glossary=(),
                    prior_titles=(),
                    defined_terms=(),
                    prior_recap="",
                )
                markdown = writer(request)
                heading = node.title
            else:
                updated = apply_section_notes(
                    LessonSectionSnapshot(
                        section_key=row.outline_node_id,
                        heading=heading,
                        markdown=markdown,
                        sequence=row.sequence,
                    ),
                    notes,
                )
                markdown = updated.markdown
                heading = updated.heading
        snapshots.append(
            LessonSectionSnapshot(
                section_key=row.outline_node_id,
                heading=heading,
                markdown=markdown,
                sequence=row.sequence,
            )
        )
        stitch_sections_in.append(
            LessonSection(heading=heading, markdown=markdown, recap=heading, defined_terms=())
        )
    replace_run_lesson_sections(session, run.id, tuple(snapshots))
    run.draft_lesson_markdown = stitch_sections(
        StitchRequest(grade_voice=grade_voice, sections=tuple(stitch_sections_in))
    )
    _record_repaired_lesson(session, run, snapshots, texts_by_node, grade_voice, writer)
    return True


def _replace_selected_items(
    session: Session,
    run: ContentGenerationRun,
    question_ids: tuple[UUID, ...] | frozenset[UUID],
    *,
    writer: ItemsWriter | None,
    blind_solver: BlindSolver | None = None,
) -> int | None:
    if run.draft_quiz_version_id is None:
        _fail_run(session, run, "Cannot repair items before the draft bank exists.")
        return None
    wanted = {item for item in question_ids}
    items = list(
        session.scalars(
            select(QuizItem)
            .where(QuizItem.quiz_version_id == run.draft_quiz_version_id)
            .order_by(QuizItem.sequence)
        ).all()
    )
    versions = {
        version.id: version
        for version in session.scalars(
            select(QuestionVersion).where(
                QuestionVersion.id.in_(tuple(item.question_version_id for item in items))
            )
        ).all()
    }
    questions = {
        question.id: question
        for question in session.scalars(
            select(Question).where(
                Question.id.in_(tuple(version.question_id for version in versions.values()))
            )
        ).all()
    }
    matched: list[tuple[QuizItem, QuestionVersion, Question]] = []
    for item in items:
        version = versions.get(item.question_version_id)
        if version is None:
            continue
        question = questions.get(version.question_id)
        if question is None or question.id not in wanted:
            continue
        matched.append((item, version, question))
    missing = wanted - {question.id for _item, _version, question in matched}
    if missing:
        _fail_run(session, run, "Those items do not belong to this run's draft bank.")
        return None
    nodes = list(
        session.scalars(
            select(ContentGenerationOutlineNode)
            .where(ContentGenerationOutlineNode.run_id == run.id)
            .order_by(ContentGenerationOutlineNode.sequence)
        ).all()
    )
    node_by_subtopic = {
        node.accepted_subtopic_id: node for node in nodes if node.accepted_subtopic_id is not None
    }
    chunks = []
    if run.intake_source_material_version_id is not None:
        chunks = list(
            session.scalars(
                select(SourceChunk)
                .where(
                    SourceChunk.source_material_version_id == run.intake_source_material_version_id
                )
                .order_by(SourceChunk.ordinal)
            ).all()
        )
    heading_groups = _heading_text_groups(chunks)
    texts_by_node = texts_for_nodes(_outline_refs(nodes), heading_groups)
    node_count = len(nodes)
    target = run.target_item_count
    solver = resolve_blind_solver(blind_solver)
    judge = _open_item_judge()
    grade_voice = _grade_voice_for_topic(session, run.topic_id)
    for item, version, question in matched:
        node = node_by_subtopic.get(question.subtopic_id)
        if node is None:
            _fail_run(session, run, "Accepted outline nodes are required before item repair.")
            return None
        chunk_texts = texts_by_node.get(node.id, ())
        if not chunk_texts:
            _fail_run(
                session,
                run,
                f"No source chunks for outline node {node.slug}; will not dump the whole PDF.",
            )
            return None
        quota = 1
        request = NodeItemRequest(
            subtopic_id=question.subtopic_id,
            learning_outcome_ids=_outcome_ids_for_node(session, node),
            heading=node.title,
            chunk_texts=chunk_texts,
            quota=quota,
            bloom=bloom_mix_for_quota(
                quota,
                heavy=node_is_heavy(node.quota or 1, node_count=node_count, target=target),
            )[:1]
            or bloom_mix_for_quota(1, heavy=False),
            outcome_statements=_outcome_statements(node),
        )
        try:
            produced = write_items_for_node(request, writer=writer)
        except Exception as exc:
            _fail_run(session, run, str(exc)[:2000])
            return None
        if not produced:
            _fail_run(session, run, "Item writer produced no replacement item.")
            return None
        taken = _topic_prompts(session, run.topic_id)
        mode = judge_mode()
        tracker = _item_tracker(judge, request, grade_voice, mode)
        gated = gate_generated_item(
            produced[0],
            solve=_solver_for(solver) if solver is not None else None,
            rewrite=_rewriter(
                writer,
                request,
                lesson_markdown=run.draft_lesson_markdown or "",
                job_id=None,
            ),
            taken=taken,
            judge=tracker,
            enforce_judge=mode == "enforce",
        )
        evaluation = tracker.evaluation if tracker is not None else None
        if version.lifecycle_status is QuestionVersionStatus.DRAFT:
            version.lifecycle_status = QuestionVersionStatus.ARCHIVED
        new_version = _persist_question_version(
            session,
            question.id,
            gated.item,
            blind_solve=gated.blind_solve,
            item_check=gated.rubric(),
            lifecycle_status=(
                QuestionVersionStatus.DRAFT if gated.servable else QuestionVersionStatus.ARCHIVED
            ),
        )
        item.question_version_id = new_version.id
        status, answer = blind_fields(gated.blind_solve)
        remember(
            session,
            evaluation,
            run_id=run.id,
            generation_job_id=None,
            subject_key=new_version.id,
            question_version_id=new_version.id,
            gate_verdict=gated.verdict,
            gate_servable=gated.servable,
            blind_status=status,
            blind_answer=answer,
        )
        session.flush()
    return len(items)


def _math_for_storage(text: str, *, field: str) -> str:
    """Item-save schema: math commands belong inside ``$...$`` or ``$$...$$``."""
    cleaned = normalize_quiz_math(text)
    if bare_latex_outside_delimiters(cleaned):
        logger.warning("Undelimited LaTeX remains in quiz item %s", field)
    return cleaned


def _item_for_storage(item: GeneratedItem) -> GeneratedItem:
    options: Mapping[str, str] = item.options
    distractors: Mapping[str, str] = item.distractor_rationales
    return replace(
        item,
        prompt=_math_for_storage(item.prompt, field="prompt"),
        options={
            label: _math_for_storage(text, field=f"option {label}")
            for label, text in options.items()
        },
        correct_rationale=_math_for_storage(item.correct_rationale, field="correct_rationale"),
        distractor_rationales={
            label: _math_for_storage(text, field=f"distractor {label}")
            for label, text in distractors.items()
        },
    )


def _persist_question_version(
    session: Session,
    question_id: UUID,
    item: GeneratedItem,
    *,
    blind_solve: dict[str, object] | None = None,
    item_check: dict[str, object] | None = None,
    lifecycle_status: QuestionVersionStatus = QuestionVersionStatus.DRAFT,
) -> QuestionVersion:
    _refuse_unshippable(item, archive=lifecycle_status is QuestionVersionStatus.ARCHIVED)
    item = _item_for_storage(item)
    current = session.scalar(
        select(func.max(QuestionVersion.version_number)).where(
            QuestionVersion.question_id == question_id
        )
    )
    version = QuestionVersion(
        question_id=question_id,
        version_number=int(current or 0) + 1,
        prompt=item.prompt,
        question_type=QuestionType.MULTIPLE_CHOICE,
        difficulty=difficulty_for_bloom(item.bloom),
        item_kind=item.item_kind or item_kind_for_bloom(item.bloom),
        lifecycle_status=lifecycle_status,
    )
    session.add(version)
    session.flush()
    for option_sequence, label in enumerate(OPTION_LABELS):
        session.add(
            QuestionOption(
                question_version_id=version.id,
                label=label,
                text=item.options[label],
                sequence=option_sequence,
            )
        )
    session.add(
        QuestionAnswerKey(
            question_version_id=version.id,
            correct_option_label=item.correct_label,
            correct_rationale=item.correct_rationale,
            distractor_rationales=dict(item.distractor_rationales),
            scoring_rubric=_rubric_with_blind_solve(item, blind_solve, item_check),
        )
    )
    for outcome_id in item.learning_outcome_ids:
        session.add(
            QuestionOutcomeTag(question_version_id=version.id, learning_outcome_id=outcome_id)
        )
    return version


def _content_snapshot_from_drafts(
    session: Session, run: ContentGenerationRun
) -> ContentSnapshot | None:
    markdown = (run.draft_lesson_markdown or "").strip()
    if not markdown or run.draft_quiz_version_id is None:
        return None
    section_rows = list(
        session.scalars(
            select(ContentGenerationLessonSection)
            .where(ContentGenerationLessonSection.run_id == run.id)
            .order_by(ContentGenerationLessonSection.sequence)
        ).all()
    )
    if not section_rows:
        return None
    items = _quiz_item_snapshots(session, run.draft_quiz_version_id)
    if not items:
        return None
    return ContentSnapshot(
        rendered_lesson_markdown=markdown,
        lesson_sections=tuple(
            LessonSectionSnapshot(
                section_key=row.outline_node_id,
                heading=row.heading,
                markdown=row.markdown,
                sequence=row.sequence,
            )
            for row in section_rows
        ),
        quiz_version_id=run.draft_quiz_version_id,
        quiz_items=items,
    )


def _quiz_item_snapshots(session: Session, quiz_version_id: UUID) -> tuple[QuizItemSnapshot, ...]:
    items = list(
        session.scalars(
            select(QuizItem)
            .where(QuizItem.quiz_version_id == quiz_version_id)
            .order_by(QuizItem.sequence)
        ).all()
    )
    out: list[QuizItemSnapshot] = []
    for item in items:
        version = session.get(QuestionVersion, item.question_version_id)
        if version is None:
            continue
        question = session.get(Question, version.question_id)
        if question is None:
            continue
        options = list(
            session.scalars(
                select(QuestionOption)
                .where(QuestionOption.question_version_id == version.id)
                .order_by(QuestionOption.sequence, QuestionOption.label)
            ).all()
        )
        key = session.scalar(
            select(QuestionAnswerKey).where(QuestionAnswerKey.question_version_id == version.id)
        )
        rationales = key.distractor_rationales if key is not None else {}
        pairs: tuple[tuple[str, str], ...] = ()
        if isinstance(rationales, dict):
            pairs = tuple((str(label), str(text)) for label, text in rationales.items())
        out.append(
            QuizItemSnapshot(
                item_key=question.id,
                question_id=question.id,
                question_version_id=version.id,
                subtopic_id=question.subtopic_id,
                prompt=version.prompt,
                options=tuple(
                    QuizOptionSnapshot(
                        label=option.label, text=option.text, sequence=option.sequence
                    )
                    for option in options
                ),
                answer_key=QuizAnswerKeySnapshot(
                    correct_label=key.correct_option_label or "" if key is not None else "",
                    correct_rationale=key.correct_rationale or "" if key is not None else "",
                    distractor_rationales=pairs,
                ),
                sequence=item.sequence,
            )
        )
    return tuple(out)
