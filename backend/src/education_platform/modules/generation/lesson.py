"""Sequential section-wise topic lesson authoring. Never a one-shot PDF dump.

``write_topic_lesson`` still persists one markdown string (``draft_lesson_markdown``).
Internally it walks frozen outline nodes in sequence, then a thin stitch pass.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID, uuid4

from education_platform.core.config import get_settings
from education_platform.modules.generation.adk import (
    LessonRunner,
    LessonRunRequest,
    LessonSectionSpec,
    instructional_designer_instruction,
    live_lesson_runner,
    markdown_from_lesson_draft,
    repair_latex_controls,
    stitch_agent_instruction,
)
from education_platform.modules.generation.blueprint import section_source_length_error
from education_platform.modules.generation.recap import present_topic_recap, topic_recap_markdown

_OVERFLOW_CHARS = 8000
_MERMAID_FENCE = re.compile(r"```mermaid\s*([\s\S]*?)```", re.IGNORECASE)
_MERMAID_START = re.compile(
    r"^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|"
    r"erDiagram|journey|gantt|pie|mindmap)\b",
    re.IGNORECASE,
)
MERMAID_UNREPAIRED_PREFIX = "Unrepaired mermaid diagram"

_SECTION_SYSTEM = instructional_designer_instruction(heading_style="section")
_STITCH_SYSTEM = stitch_agent_instruction()
_HEADING_LINE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SLIDE_PREFIX = re.compile(r"^slide\s+\d+\s+[—–-]\s+", re.IGNORECASE)


class SectionPass(StrEnum):
    FULL = "full"
    IDEA = "idea"
    EXAMPLES = "examples"


@dataclass(frozen=True, slots=True)
class LessonSectionRequest:
    heading: str
    objectives: tuple[str, ...]
    chunk_texts: tuple[str, ...]
    grade_voice: str
    glossary: tuple[tuple[str, str], ...]
    prior_titles: tuple[str, ...]
    defined_terms: tuple[str, ...]
    prior_recap: str
    pass_kind: SectionPass = SectionPass.FULL
    repair_note: str = ""


@dataclass(frozen=True, slots=True)
class LessonSection:
    heading: str
    markdown: str
    recap: str
    defined_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StitchRequest:
    grade_voice: str
    sections: tuple[LessonSection, ...]


SectionWriter = Callable[[LessonSectionRequest], str]
StitchWriter = Callable[[StitchRequest], str]
TopicLessonWriter = SectionWriter


def carry_forward_brief(request: LessonSectionRequest) -> str:
    """What earlier sections already taught, which words are defined, what not to repeat."""
    if not request.prior_titles:
        return (
            "This is the first section. Define new terms as they appear. "
            "Do not assume earlier teaching."
        )
    terms = ", ".join(request.defined_terms) or "none yet"
    titles = "; ".join(request.prior_titles)
    recap = request.prior_recap.strip() or "See earlier recaps."
    return (
        f"Already taught: {titles}. Already defined: {terms}. "
        f"Do not repeat those definitions. Carry-forward recap: {recap}"
    )


def _mermaid_label(heading: str) -> str:
    cleaned = heading.replace('"', "'")
    return cleaned[:60] or "Idea"


def parse_mermaid_diagram(source: str) -> str:
    """Boundary parse of one mermaid body. Invalid → ValueError, not a half-diagram."""
    text = source.strip()
    if not text:
        raise ValueError("mermaid diagram is empty")
    first = text.splitlines()[0].strip()
    if _MERMAID_START.match(first) is None:
        raise ValueError("unrecognized mermaid diagram type")
    if text.count("[") != text.count("]"):
        raise ValueError("unbalanced mermaid brackets")
    if text.count("(") != text.count(")"):
        raise ValueError("unbalanced mermaid parentheses")
    return text


def repair_mermaid_diagram(source: str, heading: str = "Concept") -> str:
    """Repair unbalanced brackets in a non-empty fence. Empty source raises."""
    original = source.strip()
    if not original:
        raise ValueError("mermaid diagram is empty")
    text = original
    first = text.splitlines()[0].strip()
    if _MERMAID_START.match(first) is None:
        text = "flowchart TD\n  " + text
    diff_brackets = text.count("[") - text.count("]")
    if diff_brackets > 0:
        text += "]" * diff_brackets
    elif diff_brackets < 0:
        text = text.replace("]", "", abs(diff_brackets))
    diff_parens = text.count("(") - text.count(")")
    if diff_parens > 0:
        text += ")" * diff_parens
    elif diff_parens < 0:
        text = text.replace(")", "", abs(diff_parens))
    try:
        return parse_mermaid_diagram(text)
    except ValueError as exc:
        raise ValueError(
            f"{MERMAID_UNREPAIRED_PREFIX} in {heading!r}: {exc}. "
            "Refusing to replace source content with a generic stub."
        ) from exc


def repair_mermaid_fences(markdown: str, heading: str = "Concept") -> str:
    """Repair any mermaid fences present. A lesson without a diagram is valid."""
    repaired_markdown = markdown
    for match in reversed(list(_MERMAID_FENCE.finditer(markdown))):
        body = match.group(1)
        try:
            parse_mermaid_diagram(body)
            continue
        except ValueError:
            pass
        repaired_body = repair_mermaid_diagram(body, heading)
        repaired_fence = f"```mermaid\n{repaired_body.strip()}\n```"
        repaired_markdown = (
            repaired_markdown[: match.start()] + repaired_fence + repaired_markdown[match.end() :]
        )
    return repaired_markdown


def assert_parseable_mermaid(markdown: str) -> None:
    """Every mermaid fence present must parse. No fence is required."""
    for body in _MERMAID_FENCE.findall(markdown):
        parse_mermaid_diagram(body)


def normalize_lesson_heading(text: str) -> str:
    """Case-folded heading with a leading 'Slide N —' prefix removed."""
    cleaned = _SLIDE_PREFIX.sub("", text.strip())
    return " ".join(cleaned.casefold().split())


def extract_lesson_section(markdown: str, heading: str) -> str:
    """Markdown for one heading, without later headings' teaching text."""
    wanted = normalize_lesson_heading(heading)
    if not wanted:
        return ""
    lines = markdown.splitlines()
    start: int | None = None
    level = 2
    for index, line in enumerate(lines):
        match = _HEADING_LINE.match(line.strip())
        if match is None:
            continue
        if normalize_lesson_heading(match.group(2)) == wanted:
            start = index
            level = len(match.group(1))
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = _HEADING_LINE.match(lines[index].strip())
        if match is not None and len(match.group(1)) <= level:
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def extract_recap(markdown: str, heading: str) -> str:
    match = re.search(
        r"\*\*Recap\.?\*\*\s*(.+?)(?:\n\s*\n|\Z)",
        markdown,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is not None:
        return " ".join(match.group(1).split())[:400]
    # Narrative sections may end with a plain closing paragraph instead of a bold label.
    paragraphs = [p.strip() for p in markdown.strip().split("\n\n") if p.strip()]
    for paragraph in reversed(paragraphs):
        if paragraph.startswith("##") or paragraph.startswith("```"):
            continue
        return " ".join(paragraph.split())[:400]
    return heading


def coerce_lesson_markdown(raw: str) -> str:
    """Turn a model JSON envelope into student markdown and restore LaTeX commands."""
    stripped = raw.strip()
    if stripped.startswith("{") or stripped.startswith("```"):
        extracted = markdown_from_lesson_draft(stripped).strip()
        if extracted:
            stripped = extracted
    return repair_latex_controls(stripped)


def section_from_chunks(request: LessonSectionRequest) -> str:
    """Heuristic narrative rewrite grounded in this node's excerpts only."""
    excerpt = _snippet(" ".join(request.chunk_texts), 400)
    idea_line = (
        f"{request.heading} means this: {excerpt}"
        if request.pass_kind is not SectionPass.EXAMPLES
        else f"Keep the idea of {request.heading} and look at the source numbers."
    )
    example_line = (
        f"From the source for {request.heading}: {excerpt}"
        if request.pass_kind is not SectionPass.IDEA
        else f"Hold the definition of {request.heading}; examples come next."
    )
    prior = ", ".join(request.prior_titles) or "nothing yet in this topic"
    return (
        f"## {request.heading}\n\n"
        f"{idea_line}\n\n"
        f"Building on {prior}, here is how it works in practice. {example_line}\n\n"
        f"Now try one yourself: using only {request.heading}, "
        f"answer a question about {_snippet(excerpt, 80)}.\n\n"
        f"By the end of this section you can explain {request.heading} and use it: "
        f"{_snippet(excerpt, 120)}\n"
    )


def stitch_sections(request: StitchRequest) -> str:
    """Short narrative transitions and one topic recap. Does not rewrite the teaching."""
    if not request.sections:
        raise ValueError("cannot stitch an empty lesson")
    # grade_voice is a model instruction ("Write for Grade 8 students studying …").
    # It is not part of the lesson students read.
    parts: list[str] = ["# Lesson"]
    titles = [section.heading for section in request.sections]
    for index, section in enumerate(request.sections):
        if index > 0:
            previous = titles[index - 1]
            parts.append(
                f"You just saw {previous}. Now we use that to understand {section.heading}."
            )
        parts.append(coerce_lesson_markdown(section.markdown))
    recap = topic_recap_markdown(titles)
    if recap:
        parts.append(recap)
    return "\n\n".join(parts).strip() + "\n"


@dataclass(frozen=True, slots=True)
class WrittenTopicLesson:
    sections: tuple[LessonSection, ...]
    markdown: str


def write_topic_lesson(
    sections: Sequence[LessonSectionRequest],
    *,
    write_section: SectionWriter | None = None,
    write_stitch: StitchWriter | None = None,
    runner: LessonRunner | None = None,
    job_id: UUID | None = None,
) -> str:
    """Sequential section writers then stitch. One markdown string for the run.

    Do not pass every PDF chunk as a single section. The worker loops accepted
    outline nodes in ``sequence`` and supplies that heading's chunks (neighbors
    only when the node has none).
    """
    return write_topic_lesson_document(
        sections,
        write_section=write_section,
        write_stitch=write_stitch,
        runner=runner,
        job_id=job_id,
    ).markdown


def _spec_from_request(item: LessonSectionRequest) -> LessonSectionSpec:
    return LessonSectionSpec(
        heading=item.heading,
        objectives=item.objectives,
        chunk_texts=item.chunk_texts,
        grade_voice=item.grade_voice,
        glossary=item.glossary,
        prior_titles=item.prior_titles,
        defined_terms=item.defined_terms,
        prior_recap=item.prior_recap,
        pass_kind=item.pass_kind.value,
        heading_style="section",
        repair_note=item.repair_note,
    )


def _live_section_writer(runner: LessonRunner, job_id: UUID | None) -> SectionWriter:
    """One heading per runner call. The caller stitches the finished sections."""

    def write(request: LessonSectionRequest) -> str:
        settings = get_settings()
        result = runner.generate(
            LessonRunRequest(
                job_id=job_id or uuid4(),
                sections=(_spec_from_request(request),),
                model=settings.adk_model,
                max_review_rounds=settings.adk_max_review_rounds,
            )
        )
        text = result.sections_markdown[0] if result.sections_markdown else result.markdown
        if result.review_status.value != "approved" and not text.strip():
            raise ValueError(
                result.reviewer_notes or "Lesson pedagogy reviewer rejected the draft."
            )
        return text

    return write


def write_topic_lesson_document(
    sections: Sequence[LessonSectionRequest],
    *,
    write_section: SectionWriter | None = None,
    write_stitch: StitchWriter | None = None,
    runner: LessonRunner | None = None,
    job_id: UUID | None = None,
) -> WrittenTopicLesson:
    """Same sequential walk as ``write_topic_lesson``, plus the per-node sections.

    A live run stays on this loop: each step is one heading, with that heading's
    excerpts and the previous recap. ``stitch_sections`` joins the results.
    """
    if not sections:
        raise ValueError("cannot write a lesson with no sections")
    using_live = False
    if write_section is None and write_stitch is None:
        if runner is None and get_settings().openrouter_configured:
            runner = live_lesson_runner()
        if runner is not None:
            write_section = _live_section_writer(runner, job_id)
            using_live = True
    written: list[LessonSection] = []
    glossary = sections[0].glossary
    defined = sections[0].defined_terms
    prior_titles: tuple[str, ...] = ()
    prior_recap = ""
    for seed in sections:
        request = replace(
            seed,
            glossary=glossary,
            defined_terms=defined,
            prior_titles=prior_titles,
            prior_recap=prior_recap,
        )
        markdown = _write_section_passes(request, write_section)
        if using_live:
            length_error = section_source_length_error(
                markdown, request.chunk_texts, heading=request.heading
            )
            if length_error is not None:
                raise ValueError(length_error)
        recap = extract_recap(markdown, request.heading)
        terms = _merge_terms(defined, request.heading)
        written.append(
            LessonSection(
                heading=request.heading,
                markdown=markdown,
                recap=recap,
                defined_terms=terms,
            )
        )
        glossary = _merge_glossary(glossary, request.heading, recap)
        defined = terms
        prior_titles = (*prior_titles, request.heading)
        prior_recap = recap
    stitch_request = StitchRequest(grade_voice=sections[0].grade_voice, sections=tuple(written))
    if write_stitch is not None:
        markdown = write_stitch(stitch_request)
    else:
        markdown = stitch_sections(stitch_request)
    markdown = present_topic_recap(markdown)
    return WrittenTopicLesson(sections=tuple(written), markdown=markdown)


def _write_section_passes(request: LessonSectionRequest, writer: SectionWriter | None) -> str:
    passes = _split_if_needed(request)
    parts = [_render_section(item, writer) for item in passes]
    return "\n\n".join(parts)


def _split_if_needed(request: LessonSectionRequest) -> tuple[LessonSectionRequest, ...]:
    total = sum(len(text) for text in request.chunk_texts)
    if (
        request.pass_kind is not SectionPass.FULL
        or total <= _OVERFLOW_CHARS
        or len(request.chunk_texts) < 2
    ):
        return (request,)
    mid = max(1, len(request.chunk_texts) // 2)
    idea = replace(
        request,
        chunk_texts=request.chunk_texts[:mid],
        pass_kind=SectionPass.IDEA,
    )
    examples = replace(
        request,
        chunk_texts=request.chunk_texts[mid:],
        pass_kind=SectionPass.EXAMPLES,
    )
    return (idea, examples)


def _render_section(request: LessonSectionRequest, writer: SectionWriter | None) -> str:
    markdown = _produce_section(request, writer)
    try:
        assert_parseable_mermaid(markdown)
        return markdown
    except ValueError:
        markdown = _produce_section(request, writer)
        try:
            assert_parseable_mermaid(markdown)
            return markdown
        except ValueError:
            repaired = repair_mermaid_fences(markdown, request.heading)
            assert_parseable_mermaid(repaired)
            return repaired


def _produce_section(request: LessonSectionRequest, writer: SectionWriter | None) -> str:
    raw = writer(request) if writer is not None else section_from_chunks(request)
    return coerce_lesson_markdown(raw)


def _merge_glossary(
    glossary: tuple[tuple[str, str], ...], heading: str, recap: str
) -> tuple[tuple[str, str], ...]:
    key = heading.strip().casefold()
    if any(term.strip().casefold() == key for term, _definition in glossary):
        return glossary
    return (*glossary, (heading, recap[:200] or heading))


def _merge_terms(existing: tuple[str, ...], heading: str) -> tuple[str, ...]:
    if heading in existing:
        return existing
    return (*existing, heading)


def _snippet(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if not collapsed:
        return "the source excerpt"
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"
