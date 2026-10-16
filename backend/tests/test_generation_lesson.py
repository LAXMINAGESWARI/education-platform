"""Slice 3 lesson generation: sequential sections, stitch, draft markdown, qa_review."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.generation.adk import (
    KATEX_MARKDOWN_RULES,
    LessonRunRequest,
    LessonRunResult,
    LessonSectionSpec,
    lesson_reviewer_instruction,
)
from education_platform.modules.generation.adk_live import LiveLessonRunner
from education_platform.modules.generation.blueprint import (
    DIAGRAM_PLACEHOLDER,
    MIN_SOURCED_PROSE_CHARS,
)
from education_platform.modules.generation.items import (
    GeneratedItem,
    NodeItemRequest,
    items_from_chunks,
)
from education_platform.modules.generation.lesson import (
    _SECTION_SYSTEM,
    _STITCH_SYSTEM,
    LessonSection,
    LessonSectionRequest,
    SectionPass,
    StitchRequest,
    assert_parseable_mermaid,
    parse_mermaid_diagram,
    section_from_chunks,
    stitch_sections,
    write_topic_lesson,
    write_topic_lesson_document,
)
from education_platform.modules.generation.lesson_checks import check_lesson_quality
from education_platform.modules.generation.models import ContentGenerationRun, GenerationJob
from education_platform.modules.generation.types import (
    GenerationJobKind,
    GenerationJobStatus,
    ReviewStatus,
    RunPhase,
)
from education_platform.modules.generation.worker import process_generation_job_sync
from education_platform.modules.materials.models import SourceMaterial
from education_platform.modules.rag.chunking import DIAGRAM_PLACEHOLDER as CHUNK_DIAGRAM_PLACEHOLDER
from test_generation import SEEDED_SLUG
from test_generation_items import _accept_two_node_run, _silence_embed


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


def _section_from_request(request: LessonSectionRequest) -> str:
    return section_from_chunks(request)


def _job(session: Session, run_id: UUID, kind: GenerationJobKind) -> GenerationJob:
    job = session.scalar(
        select(GenerationJob).where(GenerationJob.run_id == run_id, GenerationJob.kind == kind)
    )
    assert job is not None
    return job


def test_mermaid_parse_rejects_invalid() -> None:
    parse_mermaid_diagram("flowchart TD\n  A[Idea] --> B[Try]")
    with pytest.raises(ValueError, match="empty"):
        parse_mermaid_diagram("  ")
    with pytest.raises(ValueError, match="unrecognized"):
        parse_mermaid_diagram("not a diagram")
    with pytest.raises(ValueError, match="unbalanced"):
        parse_mermaid_diagram("flowchart TD\n  A[Idea")


def test_lesson_system_prompt_requires_renderable_katex() -> None:
    assert "{heading}" not in _SECTION_SYSTEM
    assert "this section's heading" in _SECTION_SYSTEM
    assert KATEX_MARKDOWN_RULES in _SECTION_SYSTEM
    assert "$2x + 3 = 11$" in KATEX_MARKDOWN_RULES
    assert r"\frac{2x}{2} = 6" in KATEX_MARKDOWN_RULES
    assert r"\(" in KATEX_MARKDOWN_RULES
    assert r"\\(" in KATEX_MARKDOWN_RULES
    assert r"\\frac" in KATEX_MARKDOWN_RULES
    assert "```markdown" in KATEX_MARKDOWN_RULES
    assert "```json" in KATEX_MARKDOWN_RULES
    assert "$x $" in KATEX_MARKDOWN_RULES
    assert "$...$" in _STITCH_SYSTEM
    assert r"\(" in _STITCH_SYSTEM


def test_write_topic_lesson_is_sequential_not_a_dump() -> None:
    captured: list[LessonSectionRequest] = []

    def _capture(request: LessonSectionRequest) -> str:
        captured.append(request)
        return section_from_chunks(request)

    first = LessonSectionRequest(
        heading="Properties of squares",
        objectives=("Identify properties of squares",),
        chunk_texts=("A square has four equal sides.",),
        grade_voice="Write for Grade 6 students studying Shapes.",
        glossary=(),
        prior_titles=(),
        defined_terms=(),
        prior_recap="",
    )
    second = LessonSectionRequest(
        heading="Angles of a triangle",
        objectives=("State the angle sum",),
        chunk_texts=("Interior angles sum to 180 degrees.",),
        grade_voice=first.grade_voice,
        glossary=(),
        prior_titles=(),
        defined_terms=(),
        prior_recap="",
    )
    markdown = write_topic_lesson((first, second), write_section=_capture)
    assert len(captured) == 2
    assert captured[0].heading == "Properties of squares"
    assert captured[1].heading == "Angles of a triangle"
    assert captured[0].prior_titles == ()
    assert "Properties of squares" in captured[1].prior_titles
    assert "180" not in "".join(captured[0].chunk_texts)
    assert "equal sides" not in "".join(captured[1].chunk_texts)
    assert "four equal sides" in markdown
    assert "180 degrees" in markdown
    assert "**Topic recap.**" in markdown
    assert markdown.index("Properties of squares") < markdown.index("Angles of a triangle")
    assert_parseable_mermaid(markdown)


def test_json_section_unwraps_and_grade_voice_stays_off_the_page() -> None:
    def _json(request: LessonSectionRequest) -> str:
        return (
            '{"lesson_markdown": "## ' + request.heading + '\\n\\nFour equal sides. \\(2x = 4\\)."}'
        )

    request = LessonSectionRequest(
        heading="Properties of squares",
        objectives=("Identify squares",),
        chunk_texts=("A square has four equal sides.",),
        grade_voice="Write for Grade 6 students studying Shapes. Use plain language.",
        glossary=(),
        prior_titles=(),
        defined_terms=(),
        prior_recap="",
    )
    markdown = write_topic_lesson((request,), write_section=_json)
    assert "## Properties of squares" in markdown
    assert r"\(2x = 4\)" in markdown
    assert '{"lesson_markdown"' not in markdown
    assert "studying Shapes" not in markdown
    assert "Use plain language" not in markdown


def test_overflow_splits_idea_then_examples() -> None:
    captured: list[LessonSectionRequest] = []

    def _capture(request: LessonSectionRequest) -> str:
        captured.append(request)
        return section_from_chunks(request)

    long_a = "alpha excerpt " + ("nines " * 1200)
    long_b = "beta excerpt " + ("eights " * 1200)
    request = LessonSectionRequest(
        heading="Long section",
        objectives=("Keep the idea",),
        chunk_texts=(long_a, long_b),
        grade_voice="Write for Grade 6 students.",
        glossary=(),
        prior_titles=(),
        defined_terms=(),
        prior_recap="",
    )
    markdown = write_topic_lesson((request,), write_section=_capture)
    assert [item.pass_kind for item in captured] == [SectionPass.IDEA, SectionPass.EXAMPLES]
    assert "nines" in captured[0].chunk_texts[0]
    assert "eights" in captured[1].chunk_texts[0]
    assert "Long section" in markdown


def test_mermaid_retry_on_parser_error() -> None:
    calls = {"count": 0}

    def _flaky(request: LessonSectionRequest) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            return f"## {request.heading}\n```mermaid\nnot a diagram\n```\n"
        return section_from_chunks(request)

    request = LessonSectionRequest(
        heading="Squares",
        objectives=("Identify squares",),
        chunk_texts=("A square has four equal sides.",),
        grade_voice="Write for Grade 6 students.",
        glossary=(),
        prior_titles=(),
        defined_terms=(),
        prior_recap="",
    )
    markdown = write_topic_lesson((request,), write_section=_flaky)
    assert calls["count"] == 2
    assert_parseable_mermaid(markdown)


def test_section_without_mermaid_is_accepted() -> None:
    def _no_diagram(request: LessonSectionRequest) -> str:
        return (
            f"## {request.heading}\n\n"
            "A square has four equal sides and four right angles. "
            "Think of a tile on the floor: every side matches the next. "
            "Because all sides match, you can measure one side and know them all.\n\n"
            "For example, if one side of a square is 5 cm, the perimeter is 4 times 5, "
            "which is 20 cm. You only needed one measurement.\n\n"
            "Now you can explain what makes a square a square and find its perimeter "
            "from a single side.\n"
        )

    request = LessonSectionRequest(
        heading="Squares",
        objectives=("Identify squares",),
        chunk_texts=("A square has four equal sides.",),
        grade_voice="Write for Grade 6 students.",
        glossary=(),
        prior_titles=(),
        defined_terms=(),
        prior_recap="",
    )
    markdown = write_topic_lesson((request,), write_section=_no_diagram)
    assert "```mermaid" not in markdown
    assert "perimeter" in markdown


def test_stitch_does_not_rewrite_teaching() -> None:
    section_md = section_from_chunks(
        LessonSectionRequest(
            heading="Squares",
            objectives=("Identify squares",),
            chunk_texts=("A square has four equal sides.",),
            grade_voice="Write for Grade 6 students.",
            glossary=(),
            prior_titles=(),
            defined_terms=(),
            prior_recap="",
        )
    )
    stitched = stitch_sections(
        StitchRequest(
            grade_voice="Write for Grade 6 students.",
            sections=(
                LessonSection(
                    heading="Squares",
                    markdown=section_md,
                    recap="Squares",
                    defined_terms=("Squares",),
                ),
            ),
        )
    )
    assert section_md.strip() in stitched
    assert "**Topic recap.**" in stitched
    assert "Write for Grade 6" not in stitched


def test_items_only_success_stays_generating(
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
    stored.draft_lesson_markdown = "# Lesson\n\nReady for questions."
    seeded_db.add(
        GenerationJob(
            run_id=run_id,
            kind=GenerationJobKind.ITEMS,
            status=GenerationJobStatus.QUEUED,
        )
    )
    seeded_db.commit()
    process_generation_job_sync(
        _job(seeded_db, run_id, GenerationJobKind.ITEMS).id,
        write_items=_items_from_request,
    )
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.GENERATING
    assert run.draft_quiz_version_id is not None
    lesson = _job(seeded_db, run_id, GenerationJobKind.LESSON)
    assert lesson.status is GenerationJobStatus.QUEUED
    get_settings.cache_clear()


def test_lesson_worker_is_per_node_and_does_not_publish(
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
    captured: list[LessonSectionRequest] = []

    def _capture(request: LessonSectionRequest) -> str:
        captured.append(request)
        return section_from_chunks(request)

    process_generation_job_sync(
        _job(seeded_db, run_id, GenerationJobKind.LESSON).id,
        write_lesson=_capture,
    )
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.GENERATING
    assert run.draft_lesson_markdown is not None
    assert "four equal sides" in run.draft_lesson_markdown.lower()
    assert "180" in run.draft_lesson_markdown
    assert "**Topic recap.**" in run.draft_lesson_markdown
    assert len(captured) == 2
    assert "180" not in "".join(captured[0].chunk_texts)
    assert "equal sides" not in "".join(captured[1].chunk_texts).lower()
    topic_lessons = list(
        seeded_db.scalars(
            select(SourceMaterial).where(
                SourceMaterial.topic_id == seeded_topic_id,
                SourceMaterial.slug == "lesson",
            )
        ).all()
    )
    assert topic_lessons == []
    subtopic = seeded_db.scalar(select(Subtopic).where(Subtopic.slug == SEEDED_SLUG))
    assert subtopic is not None
    student = client.get(
        f"/api/v1/subtopics/{subtopic.id}/material",
        headers=enrolled_student_headers,
    )
    assert student.status_code == 200, student.text
    get_settings.cache_clear()


def test_both_jobs_advance_to_qa_review(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    process_generation_job_sync(
        _job(seeded_db, run_id, GenerationJobKind.LESSON).id,
        write_lesson=_section_from_request,
    )
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.GENERATING
    process_generation_job_sync(
        _job(seeded_db, run_id, GenerationJobKind.ITEMS).id,
        write_items=_items_from_request,
    )
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.QA_REVIEW
    assert run.draft_lesson_markdown
    assert run.draft_quiz_version_id is not None
    assert run.published_lesson_version_id is None
    assert run.published_quiz_version_id is None
    body = client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers).json()
    assert body["phase"] == "qa_review"
    assert body["draft_lesson_markdown"]
    get_settings.cache_clear()


def test_lesson_then_items_also_reaches_qa_review(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    process_generation_job_sync(
        _job(seeded_db, run_id, GenerationJobKind.LESSON).id,
        write_lesson=_section_from_request,
    )
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.GENERATING
    process_generation_job_sync(
        _job(seeded_db, run_id, GenerationJobKind.ITEMS).id,
        write_items=_items_from_request,
    )
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.QA_REVIEW
    get_settings.cache_clear()


def test_lesson_retry_does_not_duplicate_markdown(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_topic_id: UUID,
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _silence_embed(monkeypatch, tmp_path)
    run_id = _accept_two_node_run(client, admin_headers, seeded_topic_id, seeded_db)
    calls = {"count": 0}

    def _fail_first(request: LessonSectionRequest) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("LLM down on first section")
        return section_from_chunks(request)

    process_generation_job_sync(
        _job(seeded_db, run_id, GenerationJobKind.LESSON).id,
        write_lesson=_fail_first,
    )
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.FAILED
    assert run.draft_lesson_markdown is None
    failed = client.get(f"/api/v1/teaching/generation-runs/{run_id}", headers=admin_headers)
    assert failed.status_code == 200, failed.text
    assert {"kind": "lesson", "status": "failed"} in failed.json()["jobs"]

    retried = client.post(
        f"/api/v1/teaching/generation-runs/{run_id}/retry",
        headers=admin_headers,
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["phase"] == "generating"
    queued = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.LESSON,
            GenerationJob.status == GenerationJobStatus.QUEUED,
        )
    )
    assert queued is not None
    process_generation_job_sync(queued.id, write_lesson=_section_from_request)
    seeded_db.expire_all()
    items = seeded_db.scalar(
        select(GenerationJob).where(
            GenerationJob.run_id == run_id,
            GenerationJob.kind == GenerationJobKind.ITEMS,
            GenerationJob.status == GenerationJobStatus.QUEUED,
        )
    )
    assert items is not None
    process_generation_job_sync(items.id, write_items=_items_from_request)
    seeded_db.expire_all()
    run = seeded_db.get(ContentGenerationRun, run_id)
    assert run is not None
    assert run.phase is RunPhase.QA_REVIEW
    assert run.draft_lesson_markdown is not None
    assert run.draft_lesson_markdown.count("**Topic recap.**") == 1
    lesson_jobs = list(
        seeded_db.scalars(
            select(GenerationJob).where(
                GenerationJob.run_id == run_id,
                GenerationJob.kind == GenerationJobKind.LESSON,
            )
        ).all()
    )
    assert len(lesson_jobs) == 2
    get_settings.cache_clear()


def _section_request(heading: str, excerpt: str) -> LessonSectionRequest:
    return LessonSectionRequest(
        heading=heading,
        objectives=(f"Explain {heading}",),
        chunk_texts=(excerpt,),
        grade_voice="Write for Grade 6 students.",
        glossary=(),
        prior_titles=(),
        defined_terms=(),
        prior_recap="",
    )


def _rich_section(heading: str, recap: str) -> str:
    sentence = (
        f"{heading} follows the source method. "
        "Each step uses a rule already written in the excerpt, and the reason for that step "
        "is the same reason the source gives. "
    )
    body = "\n\n".join([sentence] * 8)
    return f"## {heading}\n\n{body}\n\n**Recap.** {recap}\n"


def test_designer_prompt_asks_for_depth_and_forbids_invented_facts() -> None:
    reviewer = lesson_reviewer_instruction()
    assert "worked example" in _SECTION_SYSTEM
    assert "each step explained" in _SECTION_SYSTEM
    assert "800-1500" in _SECTION_SYSTEM
    assert "absent from the untrusted source excerpts" in _SECTION_SYSTEM
    assert "[Diagram]" in _SECTION_SYSTEM
    assert "not transcribed" in _SECTION_SYSTEM
    assert re.search(r"\{[A-Za-z_][A-Za-z0-9_]*\}", _SECTION_SYSTEM) is None
    assert "one short paragraph" in reviewer
    assert "not transcribed" in reviewer
    assert "diagram-only" in reviewer
    assert DIAGRAM_PLACEHOLDER == CHUNK_DIAGRAM_PLACEHOLDER


def test_live_lesson_calls_are_one_heading_then_python_stitch() -> None:
    class _RecordingRunner:
        def __init__(self) -> None:
            self.requests: list[LessonRunRequest] = []

        def generate(self, request: LessonRunRequest) -> LessonRunResult:
            self.requests.append(request)
            heading = request.sections[0].heading
            recap = (
                "Alpha recap stays local."
                if heading.startswith("Alpha")
                else "Beta recap stays local."
            )
            markdown = _rich_section(heading, recap)
            return LessonRunResult(
                review_status=ReviewStatus.APPROVED,
                reviewer_notes="",
                round_count=1,
                sections_markdown=(markdown,),
                markdown=markdown,
                transcript={},
            )

    runner = _RecordingRunner()
    markdown = write_topic_lesson_document(
        (
            _section_request("Alpha shapes", "UNIQUE_ALPHA_EXCERPT"),
            _section_request("Beta angles", "UNIQUE_BETA_EXCERPT"),
        ),
        runner=runner,
    ).markdown
    assert len(runner.requests) == 2
    assert all(len(call.sections) == 1 for call in runner.requests)
    first, second = runner.requests
    assert first.sections[0].chunk_texts == ("UNIQUE_ALPHA_EXCERPT",)
    assert first.sections[0].prior_recap == ""
    assert "UNIQUE_BETA_EXCERPT" not in first.sections[0].chunk_texts
    assert second.sections[0].chunk_texts == ("UNIQUE_BETA_EXCERPT",)
    assert "UNIQUE_ALPHA_EXCERPT" not in "".join(second.sections[0].chunk_texts)
    assert second.sections[0].prior_recap == "Alpha recap stays local."
    assert "You just saw" in markdown
    assert "**Topic recap.**" in markdown
    assert "UNIQUE_ALPHA_EXCERPT" not in second.sections[0].prior_recap


def test_live_lesson_runner_builds_one_message_per_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []
    built: list[dict[str, object]] = []

    def _build(**kwargs: object) -> object:
        built.append(kwargs)
        return object()

    def _run(**kwargs: object) -> tuple[dict[str, str], list[object]]:
        message = str(kwargs["user_message"])
        messages.append(message)
        heading = "Alpha shapes" if "UNIQUE_ALPHA_EXCERPT" in message else "Beta angles"
        markdown = f"## {heading}\n\nTaught {heading}.\n"
        return (
            {
                "lesson_draft": markdown,
                "approved_lesson": markdown,
                "lesson_review_status": "approved",
            },
            [],
        )

    monkeypatch.setattr(
        "education_platform.modules.generation.adk_live.build_lesson_agent",
        _build,
    )
    monkeypatch.setattr(
        "education_platform.modules.generation.adk_live.run_live_agent",
        _run,
    )
    LiveLessonRunner().generate(
        LessonRunRequest(
            job_id=uuid4(),
            sections=(
                LessonSectionSpec(
                    heading="Alpha shapes",
                    objectives=("State the alpha property",),
                    chunk_texts=("UNIQUE_ALPHA_EXCERPT",),
                    grade_voice="Write for Grade 6 students.",
                    prior_recap="",
                ),
                LessonSectionSpec(
                    heading="Beta angles",
                    objectives=("State the beta property",),
                    chunk_texts=("UNIQUE_BETA_EXCERPT",),
                    grade_voice="Write for Grade 6 students.",
                    prior_recap="Alpha recap stays local.",
                ),
            ),
            model="openrouter/openai/gpt-4o-mini",
            max_review_rounds=1,
        )
    )
    assert len(messages) == 2
    assert len(built) == 2
    assert all(call["include_stitch"] is False and call["section_count"] == 1 for call in built)
    assert "UNIQUE_ALPHA_EXCERPT" in messages[0]
    assert "UNIQUE_BETA_EXCERPT" not in messages[0]
    assert "State the alpha property" in messages[0]
    assert "Write for Grade 6 students." in messages[0]
    assert "UNIQUE_BETA_EXCERPT" in messages[1]
    assert "UNIQUE_ALPHA_EXCERPT" not in messages[1]
    assert "Alpha recap stays local." in messages[1]
    assert "State the beta property" in messages[1]


def test_length_gate_allows_diagram_only_and_rejects_paraphrase() -> None:
    diagram = "## Figures\n\nThe figure in this source was not transcribed.\n"
    assert (
        check_lesson_quality(
            diagram,
            section_sources=(("Figures", ("[Diagram]",)),),
        )
        is None
    )
    paraphrase = "## Squares\n\nA square has four equal sides and four right angles.\n"
    error = check_lesson_quality(
        paraphrase,
        section_sources=(
            (
                "Squares",
                (
                    "A square has four equal sides. The diagonals are equal and bisect "
                    "each other at right angles, which is why the area is side times side.",
                ),
            ),
        ),
    )
    assert error is not None
    assert "Squares" in error
    assert str(MIN_SOURCED_PROSE_CHARS) in error
    mixed = check_lesson_quality(
        "## Squares\n\nSee the figure.\n",
        section_sources=(("Squares", ("intro text [Diagram]",)),),
    )
    assert mixed is not None
    long_body = " ".join(["The source method uses equal sides at every step."] * 40)
    both = (
        "## Figures\n\nThe figure in this source was not transcribed.\n\n"
        f"## Squares\n\n{long_body}\n"
    )
    assert (
        check_lesson_quality(
            both,
            section_sources=(
                ("Figures", ("[Diagram]",)),
                ("Squares", ("A square has four equal sides and the area is side times side.",)),
            ),
        )
        is None
    )


def test_live_path_length_gate_matches_source() -> None:
    class _Fixed:
        def __init__(self, body: str) -> None:
            self.body = body

        def generate(self, request: LessonRunRequest) -> LessonRunResult:
            heading = request.sections[0].heading
            markdown = f"## {heading}\n\n{self.body}\n"
            return LessonRunResult(
                review_status=ReviewStatus.APPROVED,
                reviewer_notes="",
                round_count=1,
                sections_markdown=(markdown,),
                markdown=markdown,
                transcript={},
            )

    diagram = write_topic_lesson_document(
        (_section_request("Figures", "[Diagram]"),),
        runner=_Fixed("The figure in this source was not transcribed."),
    )
    assert "not transcribed" in diagram.markdown
    with pytest.raises(ValueError, match=str(MIN_SOURCED_PROSE_CHARS)):
        write_topic_lesson_document(
            (
                _section_request(
                    "Squares",
                    "A square has four equal sides and the area is side times side.",
                ),
            ),
            runner=_Fixed("A square has four equal sides."),
        )


def test_injected_writer_skips_source_length_gate() -> None:
    def _short(request: LessonSectionRequest) -> str:
        return f"## {request.heading}\n\nA square has four equal sides.\n"

    markdown = write_topic_lesson(
        (
            _section_request(
                "Squares",
                "A square has four equal sides and the area is side times side.",
            ),
        ),
        write_section=_short,
    )
    assert "four equal sides" in markdown
