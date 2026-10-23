"""Slice 2 item generation: Bloom mix, per-node writers, draft topic_mastery bank."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, inspect, select
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.url import to_sync_url
from education_platform.modules.academics.models import LearningOutcome, Subtopic
from education_platform.modules.assessments import queries as assessment_queries
from education_platform.modules.assessments.models import (
    CommonMasteryQuiz,
    Question,
    QuestionAnswerKey,
    QuestionOutcomeTag,
    QuestionVersion,
    QuestionVersionStatus,
    QuizItem,
    QuizRelease,
    QuizScope,
    QuizVersion,
    QuizVersionStatus,
)
from education_platform.modules.generation.adk import KATEX_MARKDOWN_RULES
from education_platform.modules.generation.items import (
    _ITEMS_SYSTEM,
    BloomLevel,
    GeneratedItem,
    NodeItemRequest,
    OutlineNodeRef,
    bloom_mix_for_quota,
    items_from_chunks,
    node_is_heavy,
    parse_generated_items,
    texts_for_nodes,
)
from education_platform.modules.generation.lesson import section_from_chunks
from education_platform.modules.generation.models import (
    ContentGenerationOutlineNode,
    ContentGenerationRun,
    GenerationJob,
)
from education_platform.modules.generation.outline import is_outline_noise_heading
from education_platform.modules.generation.types import (
    GenerationJobKind,
    GenerationJobStatus,
    RunPhase,
)
from education_platform.modules.generation.worker import process_generation_job_sync
from education_platform.modules.materials import service as materials_service
from test_generation import (
    SEEDED_SLUG,
    _index_run,
    _outline_run,
    _second_chunk,
    _silence_embed,
    _submit_admin,
    _write_two_nodes,
)


@pytest.fixture()
def admin_headers(client: TestClient) -> dict[str, str]:
    settings = get_settings()
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.demo_admin_email,
            "password": settings.demo_admin_password,
            "institution_name": "POC Demo School",
        },
    )
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["access_token"]}


@pytest.fixture()
def seeded_topic_id(seeded_db: Session) -> UUID:
    subtopic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    return subtopic.topic_id


def _items_from_request(request: NodeItemRequest) -> Sequence[GeneratedItem]:
    return items_from_chunks(request)


def _accept_two_node_run(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    *,
    target_item_count: int = 60,
) -> UUID:
    response = _submit_admin(
        client, admin_headers, seeded_topic_id, target_item_count=target_item_count
    )
    run_id = UUID(response.json()["run_id"])
    intake_id = UUID(
        client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()[
            "intake_version_id"
        ]
    )
    _index_run(seeded_db, intake_id, chunks=_second_chunk())
    _outline_run(seeded_db, run_id, _write_two_nodes)
    accepted = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/accept-outline",
        headers=admin_headers,
    )
    assert accepted.status_code == 200, accepted.text
    return run_id


@pytest.mark.regression
def test_accept_outline_folds_a_heading_into_the_concept(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    response = _submit_admin(client, admin_headers, seeded_topic_id)
    run_id = UUID(response.json()["run_id"])
    intake_id = UUID(
        client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()[
            "intake_version_id"
        ]
    )
    _index_run(seeded_db, intake_id, chunks=_second_chunk())
    _outline_run(seeded_db, run_id, _write_two_nodes)
    seeded_db.expire_all()
    existing = list(
        seeded_db.scalars(
            select(ContentGenerationOutlineNode).where(
                ContentGenerationOutlineNode.run_id == run_id
            )
        )
    )
    assert existing
    previous = max(existing, key=lambda node: node.sequence)
    exercise = ContentGenerationOutlineNode(
        run_id=run_id,
        slug="exercise-9-2-fold",
        title="EXERCISE 9.2",
        token_mass=4,
        prerequisite_score=Decimal("0.2"),
        centrality=Decimal("0.2"),
        weight=Decimal("1"),
        proposed_outcomes=["Practice the exercise"],
        source_headings=["EXERCISE 9.2"],
        sequence=previous.sequence + 1,
    )
    seeded_db.add(exercise)
    seeded_db.commit()

    accepted = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/accept-outline",
        headers=admin_headers,
    )
    assert accepted.status_code == 200, accepted.text
    seeded_db.expire_all()
    names = list(
        seeded_db.scalars(select(Subtopic.name).where(Subtopic.topic_id == seeded_topic_id))
    )
    assert "EXERCISE 9.2" not in names
    folded = seeded_db.get(ContentGenerationOutlineNode, exercise.id)
    concept = seeded_db.get(ContentGenerationOutlineNode, previous.id)
    assert folded is not None and concept is not None
    assert folded.accepted_subtopic_id == concept.accepted_subtopic_id
    assert folded.accepted_subtopic_id is not None
    target = seeded_db.get(Subtopic, folded.accepted_subtopic_id)
    assert target is not None
    assert not is_outline_noise_heading(target.name)


def test_bloom_mix_sums_to_quota_and_leans() -> None:
    light = bloom_mix_for_quota(10, heavy=False)
    heavy = bloom_mix_for_quota(10, heavy=True)
    assert len(light) == 10
    assert len(heavy) == 10
    light_lower = sum(1 for level in light if level in {BloomLevel.REMEMBER, BloomLevel.UNDERSTAND})
    heavy_higher = sum(1 for level in heavy if level in {BloomLevel.APPLY, BloomLevel.ANALYZE})
    assert light_lower >= 6
    assert heavy_higher >= 6
    assert node_is_heavy(40, node_count=2, target=60) is True
    assert node_is_heavy(20, node_count=2, target=60) is False


def test_item_system_prompt_requires_renderable_katex() -> None:
    assert KATEX_MARKDOWN_RULES in _ITEMS_SYSTEM
    assert "$2x + 3 = 11$" in _ITEMS_SYSTEM
    assert r"\(" in _ITEMS_SYSTEM
    assert "```markdown" in _ITEMS_SYSTEM
    assert "```json" in _ITEMS_SYSTEM
    assert "bare command" in _ITEMS_SYSTEM
    assert "base_1 + base_2" in _ITEMS_SYSTEM
    assert r"\frac" in _ITEMS_SYSTEM


def test_texts_for_nodes_does_not_dump_whole_pdf() -> None:
    first_id = UUID("00000000-0000-0000-0000-000000000001")
    second_id = UUID("00000000-0000-0000-0000-000000000002")
    nodes = (
        OutlineNodeRef(id=first_id, slug="squares", title="Properties of squares", sequence=1),
        OutlineNodeRef(id=second_id, slug="angles", title="Angles of a triangle", sequence=2),
    )
    groups = (
        ("Properties of squares", ("A square has four equal sides.",)),
        ("Angles of a triangle", ("Interior angles sum to 180 degrees.",)),
    )
    assigned = texts_for_nodes(nodes, groups)
    assert assigned[first_id] == ("A square has four equal sides.",)
    assert assigned[second_id] == ("Interior angles sum to 180 degrees.",)
    assert "180" not in "".join(assigned[first_id])
    assert "equal sides" not in "".join(assigned[second_id])


def test_texts_for_nodes_uses_neighbors_when_heading_missing() -> None:
    first_id = UUID("00000000-0000-0000-0000-000000000011")
    second_id = UUID("00000000-0000-0000-0000-000000000012")
    third_id = UUID("00000000-0000-0000-0000-000000000013")
    nodes = (
        OutlineNodeRef(id=first_id, slug="alpha", title="Alpha", sequence=1),
        OutlineNodeRef(id=second_id, slug="orphan", title="Orphan node", sequence=2),
        OutlineNodeRef(id=third_id, slug="gamma", title="Gamma", sequence=3),
    )
    groups = (
        ("Alpha", ("alpha excerpt",)),
        ("Gamma", ("gamma excerpt",)),
    )
    assigned = texts_for_nodes(nodes, groups)
    assert assigned[first_id] == ("alpha excerpt",)
    assert assigned[third_id] == ("gamma excerpt",)
    assert assigned[second_id] == ("alpha excerpt", "gamma excerpt")


def test_texts_for_nodes_gives_folded_runs_to_the_node_before_them() -> None:
    fast_id = UUID("00000000-0000-0000-0000-000000000021")
    squares_id = UUID("00000000-0000-0000-0000-000000000022")
    nodes = (
        OutlineNodeRef(
            id=fast_id,
            slug="fast",
            title="Fast Multiplications Using the Distributive Property",
            sequence=1,
        ),
        OutlineNodeRef(
            id=squares_id,
            slug="squares",
            title="Square of the Sum/Difference of Two Numbers",
            sequence=2,
        ),
    )
    groups = (
        ("Fast Multiplications Using the Distributive Property", ("heading only",)),
        ("When one of the numbers is 11, 101, 1001, ...", ("47 × 11",)),
        ("Figure it Out", ("exercise after fast multiplication",)),
        ("Square of the Sum/Difference of Two Numbers", ("$$(a + b)^2$$",)),
        ("Figure it Out", ("exercise after squares",)),
    )
    assigned = texts_for_nodes(nodes, groups)
    assert assigned[fast_id] == (
        "heading only",
        "47 × 11",
        "exercise after fast multiplication",
    )
    assert assigned[squares_id] == ("$$(a + b)^2$$", "exercise after squares")


def test_texts_for_nodes_gives_a_subheading_only_its_own_path() -> None:
    section_id = UUID("00000000-0000-0000-0000-000000000041")
    increments_id = UUID("00000000-0000-0000-0000-000000000042")
    figure_id = UUID("00000000-0000-0000-0000-000000000043")
    square_id = UUID("00000000-0000-0000-0000-000000000044")
    parent = "6.1 Some Properties of Multiplication"
    nodes = (
        OutlineNodeRef(id=section_id, slug="s61", title=parent, sequence=1),
        OutlineNodeRef(
            id=increments_id,
            slug="increments",
            title="Increments in Products",
            sequence=2,
            parent_title=parent,
        ),
        OutlineNodeRef(
            id=figure_id,
            slug="figure",
            title="Figure it Out",
            sequence=3,
            parent_title=parent,
        ),
        OutlineNodeRef(
            id=square_id,
            slug="square",
            title="Square of the Sum/Difference of Two Numbers",
            sequence=4,
            parent_title="6.2 Special Cases of the Distributive Property",
        ),
    )
    groups = (
        (f"{parent} > Increments in Products", ("23 × 27",)),
        (f"{parent} > Figure it Out", ("exercise",)),
        (
            "6.2 Special Cases of the Distributive Property > "
            "Square of the Sum/Difference of Two Numbers",
            ("$$(a + b)^2$$",),
        ),
    )
    assigned = texts_for_nodes(nodes, groups)
    assert assigned[section_id] == ()
    assert assigned[increments_id] == ("23 × 27",)
    assert assigned[figure_id] == ("exercise",)
    assert assigned[square_id] == ("$$(a + b)^2$$",)


def test_texts_for_nodes_renamed_node_takes_next_unmatched_group() -> None:
    kept_id = UUID("00000000-0000-0000-0000-000000000031")
    renamed_id = UUID("00000000-0000-0000-0000-000000000032")
    nodes = (
        OutlineNodeRef(id=kept_id, slug="alpha", title="Alpha", sequence=1),
        OutlineNodeRef(id=renamed_id, slug="teacher", title="Teacher title", sequence=2),
    )
    groups = (("Alpha", ("alpha",)), ("Beta", ("beta",)))
    assigned = texts_for_nodes(nodes, groups)
    assert assigned[kept_id] == ("alpha",)
    assert assigned[renamed_id] == ("beta",)


def test_parse_generated_items_drops_invalid() -> None:
    request = NodeItemRequest(
        subtopic_id=UUID("00000000-0000-0000-0000-000000000003"),
        learning_outcome_ids=(),
        heading="Squares",
        chunk_texts=("A square has four equal sides.",),
        quota=1,
        bloom=(BloomLevel.REMEMBER,),
    )
    with pytest.raises(ValueError, match="questions"):
        parse_generated_items({"questions": "nope"}, request)
    parsed = parse_generated_items(
        {
            "questions": [
                {"prompt": "short"},
                {
                    "prompt": "Which statement matches the square excerpt?",
                    "options": {
                        "A": "four sides",
                        "B": "three sides",
                        "C": "five sides",
                        "D": "no sides",
                    },
                    "correct": "A",
                    "correct_rationale": "The excerpt says four equal sides.",
                    "distractor_rationales": {
                        "B": "Triangles have three.",
                        "C": "Not in the excerpt.",
                        "D": "Not in the excerpt.",
                    },
                    "bloom": "remember",
                },
            ]
        },
        request,
    )
    assert len(parsed) == 1
    assert parsed[0].correct_label == "A"


def test_accept_enqueues_items_and_lesson(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    body = client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()
    assert body["phase"] == "generating"
    assert {job["kind"] for job in body["jobs"]} == {"lesson"}
    jobs = list(
        seeded_db.scalars(select(GenerationJob).where(GenerationJob.run_id == run_id)).all()
    )
    kinds = sorted(job.kind.value for job in jobs)
    assert kinds.count("items") == 0
    assert kinds.count("lesson") == 1
    nodes = body["outline"]["nodes"]
    assert sum(node["quota"] for node in nodes) == body["target_item_count"]
    again = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/accept-outline",
        headers=admin_headers,
    )
    assert again.status_code == 200, again.text
    items_jobs = list(
        seeded_db.scalars(
            select(GenerationJob).where(
                GenerationJob.run_id == run_id,
                GenerationJob.kind == GenerationJobKind.ITEMS,
            )
        ).all()
    )
    lesson_jobs = list(
        seeded_db.scalars(
            select(GenerationJob).where(
                GenerationJob.run_id == run_id,
                GenerationJob.kind == GenerationJobKind.LESSON,
            )
        ).all()
    )
    assert len(items_jobs) == 0
    assert len(lesson_jobs) == 1
    get_settings.cache_clear()


def test_generating_without_jobs_requeues_on_get(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Leftover slice-1 parks (generating, jobs: []) resume the lesson on GET."""
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    seeded_db.execute(delete(GenerationJob).where(GenerationJob.run_id == run_id))
    seeded_db.commit()
    empty = client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["phase"] == "generating"
    assert {job["kind"] for job in body["jobs"]} == {"lesson"}
    assert all(job["status"] == "queued" for job in body["jobs"])
    listed = client.get(
        f"/api/v1/teaching/topics/{seeded_topic_id}/generation-runs",
        headers=admin_headers,
    )
    assert listed.status_code == 200, listed.text
    listed_jobs = listed.json()[0]["jobs"]
    assert {job["kind"] for job in listed_jobs} == {"lesson"}
    seeded_db.expire_all()
    jobs = list(
        seeded_db.scalars(select(GenerationJob).where(GenerationJob.run_id == run_id)).all()
    )
    assert sorted(job.kind.value for job in jobs) == ["lesson"]
    get_settings.cache_clear()


def test_generating_without_intake_fails_on_get(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    stored = seeded_db.get(ContentGenerationRun, run_id)
    assert stored is not None
    stored.intake_source_material_version_id = None
    seeded_db.execute(delete(GenerationJob).where(GenerationJob.run_id == run_id))
    seeded_db.commit()
    response = client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["phase"] == "failed"
    assert body["failure_reason"] == "Intake version is missing; cannot generate items."
    seeded_db.expire_all()
    stored = seeded_db.get(ContentGenerationRun, run_id)
    assert stored is not None
    assert stored.phase is RunPhase.FAILED
    get_settings.cache_clear()


def test_items_worker_is_per_node_and_does_not_publish(
    client: TestClient,
    admin_headers: dict[str, str],
    enrolled_student_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    stored = seeded_db.get(ContentGenerationRun, run_id)
    assert stored is not None
    stored.draft_lesson_markdown = "Draft lesson so item generation is allowed."
    seeded_db.add(
        GenerationJob(
            run_id=run_id,
            kind=GenerationJobKind.ITEMS,
            status=GenerationJobStatus.QUEUED,
        )
    )
    seeded_db.commit()
    captured: list[NodeItemRequest] = []

    def _capture(request: NodeItemRequest) -> Sequence[GeneratedItem]:
        captured.append(request)
        return items_from_chunks(request)

    job = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.ITEMS,
        )
    )
    assert job is not None
    process_generation_job_sync(job.id, write_items=_capture)
    seeded_db.expire_all()

    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.GENERATING
    assert run.draft_quiz_version_id is not None
    quiz_version = seeded_db.get(QuizVersion, run.draft_quiz_version_id)
    assert quiz_version is not None
    assert quiz_version.lifecycle_status is QuizVersionStatus.DRAFT
    assert (
        seeded_db.scalar(select(QuizRelease).where(QuizRelease.quiz_version_id == quiz_version.id))
        is None
    )

    items = list(
        seeded_db.scalars(
            select(QuizItem)
            .where(QuizItem.quiz_version_id == quiz_version.id)
            .order_by(QuizItem.sequence)
        ).all()
    )
    assert len(items) == 60
    assert len(captured) == 2
    assert sum(request.quota for request in captured) == 60
    assert all(len(request.bloom) == request.quota for request in captured)
    first_texts = " ".join(captured[0].chunk_texts)
    second_texts = " ".join(captured[1].chunk_texts)
    assert "four equal sides" in first_texts.lower()
    assert "180" in second_texts
    assert "180" not in first_texts
    assert "four equal sides" not in second_texts.lower()

    questions = list(
        seeded_db.scalars(
            select(Question)
            .join(QuestionVersion, QuestionVersion.question_id == Question.id)
            .join(QuizItem, QuizItem.question_version_id == QuestionVersion.id)
            .where(QuizItem.quiz_version_id == quiz_version.id)
        ).all()
    )
    subtopic_ids = {question.subtopic_id for question in questions}
    assert None not in subtopic_ids
    tagged = seeded_db.scalars(
        select(QuestionOutcomeTag)
        .join(QuizItem, QuizItem.question_version_id == QuestionOutcomeTag.question_version_id)
        .where(QuizItem.quiz_version_id == quiz_version.id)
    ).all()
    assert len(list(tagged)) == 60
    versions = list(
        seeded_db.scalars(
            select(QuestionVersion)
            .join(QuizItem, QuizItem.question_version_id == QuestionVersion.id)
            .where(QuizItem.quiz_version_id == quiz_version.id)
        ).all()
    )
    assert all(version.lifecycle_status is QuestionVersionStatus.DRAFT for version in versions)
    keys = list(
        seeded_db.scalars(
            select(QuestionAnswerKey)
            .join(QuizItem, QuizItem.question_version_id == QuestionAnswerKey.question_version_id)
            .where(QuizItem.quiz_version_id == quiz_version.id)
        ).all()
    )
    assert len(keys) == 60
    assert all(key.correct_rationale for key in keys)
    assert all(key.distractor_rationales for key in keys)

    mastery = seeded_db.scalar(
        select(CommonMasteryQuiz).where(
            CommonMasteryQuiz.topic_id == seeded_topic_id,
            CommonMasteryQuiz.quiz_scope == QuizScope.TOPIC_MASTERY,
        )
    )
    assert mastery is not None
    released = list(
        seeded_db.scalars(
            select(QuizVersion).where(
                QuizVersion.quiz_id == mastery.id,
                QuizVersion.lifecycle_status == QuizVersionStatus.RELEASED,
            )
        ).all()
    )
    assert released
    assert quiz_version.id not in {row.id for row in released}

    subtopic = seeded_db.scalar(
        select(Subtopic).where(Subtopic.slug == "rectangles_squares_properties")
    )
    assert subtopic is not None
    student_quiz = client.get(
        f"/api/v1/subtopics/{subtopic.id}/quiz", headers=enrolled_student_headers
    )
    assert student_quiz.status_code == 200, student_quiz.text
    payload = str(student_quiz.json())
    assert "correct_rationale" not in payload
    assert "distractor_rationales" not in payload
    assert "correct_option_label" not in payload
    get_settings.cache_clear()


def test_items_retry_does_not_duplicate_bank(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    lesson = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.LESSON,
        )
    )
    assert lesson is not None
    process_generation_job_sync(lesson.id, write_lesson=section_from_chunks)
    seeded_db.expire_all()
    calls = {"count": 0}

    def _fail_second(request: NodeItemRequest) -> Sequence[GeneratedItem]:
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("LLM down on second node")
        return items_from_chunks(request)

    job = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.ITEMS,
        )
    )
    assert job is not None
    process_generation_job_sync(job.id, write_items=_fail_second)
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.FAILED
    partial = 0
    if run.draft_quiz_version_id is not None:
        partial = len(
            list(
                seeded_db.scalars(
                    select(QuizItem).where(QuizItem.quiz_version_id == run.draft_quiz_version_id)
                ).all()
            )
        )
        assert partial > 0
        assert partial < 60

    retried = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/retry",
        headers=admin_headers,
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["phase"] == "generating"
    queued = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.ITEMS,
            GenerationJob.status == GenerationJobStatus.QUEUED,
        )
    )
    assert queued is not None
    process_generation_job_sync(queued.id, write_items=_items_from_request)
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.QA_REVIEW
    assert run.draft_quiz_version_id is not None
    items = list(
        seeded_db.scalars(
            select(QuizItem).where(QuizItem.quiz_version_id == run.draft_quiz_version_id)
        ).all()
    )
    assert len(items) == 60
    get_settings.cache_clear()


def test_student_query_paths_do_not_join_answer_keys() -> None:
    assert "QuestionAnswerKey" not in assessment_queries.__dict__
    assert "QuestionAnswerKey" not in materials_service.__dict__


def test_question_answer_keys_have_rationale_columns(clean_db: str) -> None:
    db = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    columns = {column["name"] for column in inspect(db).get_columns("question_answer_keys")}
    db.dispose()
    assert "correct_rationale" in columns
    assert "distractor_rationales" in columns


def test_generated_items_carry_accepted_subtopic_and_outcomes(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    lesson = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.LESSON,
        )
    )
    assert lesson is not None
    process_generation_job_sync(lesson.id, write_lesson=section_from_chunks)
    seeded_db.expire_all()
    body = client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()
    accepted_by_title = {
        node["title"]: UUID(node["accepted_subtopic_id"]) for node in body["outline"]["nodes"]
    }
    job = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.ITEMS,
        )
    )
    assert job is not None
    process_generation_job_sync(job.id, write_items=_items_from_request)
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None and run.draft_quiz_version_id is not None
    rows = list(
        seeded_db.execute(
            select(Question.subtopic_id, QuestionVersion.prompt, QuestionVersion.id)
            .join(QuestionVersion, QuestionVersion.question_id == Question.id)
            .join(QuizItem, QuizItem.question_version_id == QuestionVersion.id)
            .where(QuizItem.quiz_version_id == run.draft_quiz_version_id)
        ).all()
    )
    assert rows
    used_subtopics = {row.subtopic_id for row in rows}
    assert used_subtopics <= set(accepted_by_title.values())
    for row in rows:
        tags = list(
            seeded_db.scalars(
                select(QuestionOutcomeTag.learning_outcome_id).where(
                    QuestionOutcomeTag.question_version_id == row.id
                )
            ).all()
        )
        assert tags
        outcomes = list(
            seeded_db.scalars(
                select(LearningOutcome).where(LearningOutcome.id.in_(tuple(tags)))
            ).all()
        )
        assert all(outcome.subtopic_id == row.subtopic_id for outcome in outcomes)
    get_settings.cache_clear()
