"""Load a generation run's lesson and item bank for the comparison script."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from education_platform.core.config import get_settings
from education_platform.db.session import create_general_sync_engine
from education_platform.db.url import to_sync_url
from education_platform.modules.assessments.models import (
    Question,
    QuestionAnswerKey,
    QuestionOption,
    QuestionVersion,
    QuizItem,
)
from education_platform.modules.generation.items import texts_for_nodes
from education_platform.modules.generation.models import (
    ContentGenerationLessonSection,
    ContentGenerationOutlineNode,
    ContentGenerationRun,
)
from education_platform.modules.generation.worker import (
    _grade_voice_for_topic,
    _heading_text_groups,
    _outline_refs,
)
from education_platform.modules.judge.checks import ItemTarget, LessonTarget
from education_platform.modules.judge.compare import (
    BankItem,
    BankLesson,
    ComparisonReport,
    score_pair,
)
from education_platform.modules.judge.models import JudgeEvaluation
from education_platform.modules.materials.models import SourceChunk


def load_run_bank(run_id: UUID) -> tuple[list[BankLesson], list[BankItem]]:
    with _session() as session:
        run = session.get(ContentGenerationRun, run_id)
        if run is None:
            raise SystemExit(f"No generation run {run_id}.")
        return _lessons(session, run), _items(session, run)


def load_stored_report(run_id: UUID) -> ComparisonReport:
    with _session() as session:
        run = session.get(ContentGenerationRun, run_id)
        if run is None:
            raise SystemExit(f"No generation run {run_id}.")
        items = _items(session, run)
        rows = list(
            session.scalars(
                select(JudgeEvaluation)
                .where(JudgeEvaluation.run_id == run_id)
                .order_by(JudgeEvaluation.created_at)
            ).all()
        )
    latest: dict[tuple[str, str], JudgeEvaluation] = {}
    for row in rows:
        key = (row.subject_kind, str(row.subject_key or row.question_version_id or row.id))
        latest[key] = row
    by_version = {
        row.question_version_id: row
        for row in latest.values()
        if row.subject_kind == "quiz_item" and row.question_version_id is not None
    }
    lesson_rows: list[tuple[str, str, tuple[str, ...]]] = []
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    latency = 0
    for row in latest.values():
        if row.subject_kind not in {"lesson", "lesson_section"}:
            continue
        reasons = tuple(str(item) for item in row.reasons if isinstance(item, str))
        lesson_rows.append((row.label or row.subject_kind, row.decision, reasons))
        input_tokens += row.input_tokens
        output_tokens += row.output_tokens
        cost += float(row.cost_usd)
        latency += row.latency_ms
    comparable = 0
    agreements = 0
    needs_review = 0
    blind_unscored = 0
    disagreements = []
    judged = 0
    for item in items:
        stored = by_version.get(UUID(item.key))
        if stored is None:
            continue
        judged += 1
        reasons = tuple(str(reason) for reason in stored.reasons if isinstance(reason, str))
        pair = score_pair(item, stored.decision, reasons)
        needs_review += int(pair.needs_review)
        blind_unscored += int(pair.blind_unscored)
        comparable += int(pair.comparable)
        agreements += int(pair.agreed)
        if pair.disagreement is not None:
            disagreements.append(pair.disagreement)
        input_tokens += stored.input_tokens
        output_tokens += stored.output_tokens
        cost += float(stored.cost_usd)
        latency += stored.latency_ms
    return ComparisonReport(
        lessons=tuple(lesson_rows),
        item_count=judged,
        comparable=comparable,
        agreements=agreements,
        disagreements=tuple(disagreements),
        needs_review=needs_review,
        blind_unscored=blind_unscored,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        latency_ms=latency,
    )


def _session() -> Session:
    engine = create_general_sync_engine(to_sync_url(get_settings().database_url))
    return sessionmaker(bind=engine)()


def _lessons(session: Session, run: ContentGenerationRun) -> list[BankLesson]:
    nodes = list(
        session.scalars(
            select(ContentGenerationOutlineNode)
            .where(ContentGenerationOutlineNode.run_id == run.id)
            .order_by(ContentGenerationOutlineNode.sequence)
        ).all()
    )
    sections = list(
        session.scalars(
            select(ContentGenerationLessonSection)
            .where(ContentGenerationLessonSection.run_id == run.id)
            .order_by(ContentGenerationLessonSection.sequence)
        ).all()
    )
    grade = _grade_voice_for_topic(session, run.topic_id)
    chunks = _chunks_by_node(session, run, nodes)
    lessons: list[BankLesson] = []
    all_outcomes: list[str] = []
    all_chunks: list[str] = []
    for section in sections:
        node = next((item for item in nodes if item.id == section.outline_node_id), None)
        outcomes = _outcomes(node.proposed_outcomes) if node is not None else ()
        section_chunks = chunks.get(section.outline_node_id, ())
        all_outcomes.extend(outcomes)
        all_chunks.extend(section_chunks)
        target = LessonTarget(
            heading=section.heading,
            grade=grade,
            outcomes=outcomes,
            chunks=section_chunks,
            markdown=section.markdown,
            kind="lesson_section",
        )
        lessons.append(BankLesson(key=str(section.outline_node_id), target=target))
    if run.draft_lesson_markdown and run.draft_lesson_markdown.strip():
        lessons.append(
            BankLesson(
                key=str(run.id),
                target=LessonTarget(
                    heading=run.title,
                    grade=grade,
                    outcomes=tuple(dict.fromkeys(all_outcomes)),
                    chunks=tuple(dict.fromkeys(all_chunks)),
                    markdown=run.draft_lesson_markdown,
                    kind="lesson",
                ),
            )
        )
    return lessons


def _items(session: Session, run: ContentGenerationRun) -> list[BankItem]:
    if run.draft_quiz_version_id is None:
        return []
    nodes = list(
        session.scalars(
            select(ContentGenerationOutlineNode).where(
                ContentGenerationOutlineNode.run_id == run.id
            )
        ).all()
    )
    by_subtopic = {
        node.accepted_subtopic_id: node for node in nodes if node.accepted_subtopic_id is not None
    }
    grade = _grade_voice_for_topic(session, run.topic_id)
    chunks = _chunks_by_node(session, run, nodes)
    quiz_items = list(
        session.scalars(
            select(QuizItem)
            .where(QuizItem.quiz_version_id == run.draft_quiz_version_id)
            .order_by(QuizItem.sequence)
        ).all()
    )
    if not quiz_items:
        return []
    version_ids = [item.question_version_id for item in quiz_items]
    versions = {
        version.id: version
        for version in session.scalars(
            select(QuestionVersion).where(QuestionVersion.id.in_(version_ids))
        ).all()
    }
    questions = {
        question.id: question
        for question in session.scalars(
            select(Question).where(
                Question.id.in_([version.question_id for version in versions.values()])
            )
        ).all()
    }
    options = list(
        session.scalars(
            select(QuestionOption).where(QuestionOption.question_version_id.in_(version_ids))
        ).all()
    )
    keys = {
        key.question_version_id: key
        for key in session.scalars(
            select(QuestionAnswerKey).where(QuestionAnswerKey.question_version_id.in_(version_ids))
        ).all()
    }
    option_map: dict[UUID, dict[str, str]] = {}
    for option in options:
        option_map.setdefault(option.question_version_id, {})[option.label] = option.text
    loaded: list[BankItem] = []
    for quiz_item in quiz_items:
        version = versions.get(quiz_item.question_version_id)
        if version is None:
            continue
        question = questions.get(version.question_id)
        if question is None:
            continue
        node = by_subtopic.get(question.subtopic_id)
        answer = keys.get(version.id)
        rubric = answer.scoring_rubric if answer is not None else None
        blind = rubric.get("blind_solve") if isinstance(rubric, dict) else None
        status, blind_answer, working = _blind(blind)
        difficulty = version.difficulty.value if version.difficulty is not None else "medium"
        target = ItemTarget(
            heading=node.title if node is not None else "",
            grade=grade,
            outcomes=_outcomes(node.proposed_outcomes) if node is not None else (),
            chunks=chunks.get(node.id, ()) if node is not None else (),
            prompt=version.prompt,
            options=option_map.get(version.id, {}),
            correct_label=(answer.correct_option_label or "") if answer is not None else "",
            correct_rationale=(answer.correct_rationale or "") if answer is not None else "",
            difficulty=difficulty,
        )
        loaded.append(
            BankItem(
                key=str(version.id),
                target=target,
                blind_status=status,
                blind_answer=blind_answer,
                blind_working=working,
            )
        )
    return loaded


def _chunks_by_node(
    session: Session,
    run: ContentGenerationRun,
    nodes: list[ContentGenerationOutlineNode],
) -> dict[UUID, tuple[str, ...]]:
    if run.intake_source_material_version_id is None or not nodes:
        return {}
    chunks = list(
        session.scalars(
            select(SourceChunk)
            .where(SourceChunk.source_material_version_id == run.intake_source_material_version_id)
            .order_by(SourceChunk.ordinal)
        ).all()
    )
    return texts_for_nodes(_outline_refs(nodes), _heading_text_groups(chunks))


def _outcomes(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())


def _blind(raw: object) -> tuple[str | None, str | None, str]:
    if not isinstance(raw, dict):
        return None, None, ""
    status = raw.get("status")
    answer = raw.get("answer")
    working = raw.get("working")
    return (
        status if isinstance(status, str) else None,
        answer if isinstance(answer, str) else None,
        working if isinstance(working, str) else "",
    )
