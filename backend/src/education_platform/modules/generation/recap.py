"""Topic recap text. Stored lessons and the published read path share this cleaner."""

from __future__ import annotations

import re
from collections.abc import Sequence

from education_platform.modules.assessments.feedback_hierarchy import feedback_concept_label

_RECAP_MARK = re.compile(r"\*\*Topic recap\.?\*\*", re.IGNORECASE)
_RECAP_START = re.compile(r"^\s*\*\*Topic recap\.?\*\*\s*", re.IGNORECASE)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TAUGHT = re.compile(r"^this lesson taught\s+", re.IGNORECASE)


def topic_recap_markdown(titles: Sequence[str]) -> str:
    """Bullet list of concept names. Activity headings and section numbers are dropped."""
    labels: list[str] = []
    seen: set[str] = set()
    for title in titles:
        label = feedback_concept_label(title)
        if label is None:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
    if not labels:
        return ""
    bullets = "\n".join(f"- {label}" for label in labels)
    return f"**Topic recap.**\n\n{bullets}"


def recap_titles_from_body(body: str) -> list[str]:
    """Titles from a recap paragraph, a markdown list, or a semicolon run-on."""
    text = body.strip()
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if any(_LIST_ITEM.match(line) for line in lines):
        titles: list[str] = []
        for line in lines:
            if _RECAP_START.match(line):
                continue
            item = _LIST_ITEM.sub("", line).strip()
            if item:
                titles.append(item)
        return titles
    flat = _TAUGHT.sub("", " ".join(lines)).strip().rstrip(".")
    if ";" in flat:
        return [part.strip() for part in flat.split(";") if part.strip()]
    return [flat] if flat else []


def _looks_like_title_list(block: str) -> bool:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    return bool(lines) and all(_LIST_ITEM.match(line) for line in lines)


def present_topic_recap(markdown: str) -> str:
    """Replace a raw topic-recap paragraph with a cleaned concept list.

    Already published lessons keep their stored markdown. This runs when a
    lesson is shown, and again after stitch so a new draft is stored clean.
    A second pass leaves a cleaned list unchanged.
    """
    if not markdown or _RECAP_MARK.search(markdown) is None:
        return markdown
    trailing_newline = markdown.endswith("\n")
    blocks = re.split(r"\n\s*\n", markdown.strip())
    rendered: list[str] = []
    index = 0
    while index < len(blocks):
        block = blocks[index].strip()
        if _RECAP_START.match(block):
            body = _RECAP_START.sub("", block).strip()
            if not body and index + 1 < len(blocks) and _looks_like_title_list(blocks[index + 1]):
                body = blocks[index + 1]
                index += 1
            recap = topic_recap_markdown(recap_titles_from_body(body))
            if recap:
                rendered.append(recap)
            index += 1
            continue
        rendered.append(block)
        index += 1
    text = "\n\n".join(rendered).strip()
    if not text:
        return "\n" if trailing_newline else ""
    return text + ("\n" if trailing_newline else "")
