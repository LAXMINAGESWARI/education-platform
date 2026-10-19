"""Subject → topic → diagnostic subtopic names for the synthetic school.

Subtopic names tag quiz questions. They are not lessons and they do not have quizzes.
"""

from __future__ import annotations

import re

CURRICULUM_TOPICS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "Mathematics": (
        (
            "Fractions",
            ("Equivalent fractions", "Adding and subtracting fractions", "Mixed numbers"),
        ),
        (
            "Decimals",
            ("Place value", "Comparing decimals", "Adding and subtracting decimals"),
        ),
        ("Ratios", ("Ratio language", "Equivalent ratios", "Sharing in a ratio")),
        ("Percentages", ("Percent of a quantity", "Percent increase and decrease")),
        ("Algebra", ("Expressions", "One-step equations", "Patterns and rules")),
    ),
    "Science": (
        ("Forces and Motion", ("Balanced forces", "Speed and acceleration", "Friction")),
        ("Light and Optics", ("Reflection", "Refraction", "How the eye sees")),
        ("Cells", ("Plant and animal cells", "Cell parts", "From cells to organisms")),
        ("Materials", ("Properties of materials", "Changing materials")),
        ("Energy", ("Energy stores", "Energy transfers", "Conserving energy")),
    ),
    "English": (
        ("Comprehension", ("Main idea", "Inference", "Evidence from the text")),
        ("Persuasive Writing", ("Claims and reasons", "Audience and tone")),
        ("Grammar", ("Sentence structure", "Verb tense", "Punctuation")),
        ("Poetry", ("Imagery", "Rhythm and rhyme")),
        ("Vocabulary", ("Word meaning in context", "Prefixes and roots")),
    ),
    "Arabic": (
        ("Reading", ("Reading aloud", "Understanding a passage")),
        ("Grammar", ("Sentence patterns", "Verb forms")),
        ("Poetry", ("Listening to a poem", "Images in a poem")),
        ("Composition", ("Planning a paragraph", "Connecting ideas")),
        ("Listening", ("Key details", "Following instructions")),
    ),
    "Social Studies": (
        ("Trade Routes", ("Why routes mattered", "Goods and exchange")),
        ("Geography", ("Maps and scale", "Climate and settlement")),
        ("Civics", ("Rights and responsibilities", "How decisions are made")),
        ("History", ("Using a source", "Cause and change")),
        ("Economics", ("Needs and wants", "Price and choice")),
    ),
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def concepts_for(subject_name: str, limit: int) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Topic name plus the diagnostic subtopic names under it."""
    return CURRICULUM_TOPICS[subject_name][:limit]


def diagnostic_names(subject_name: str, topic_name: str) -> tuple[str, ...]:
    """Subtopic tags for a topic. Unknown topics get two generic tags."""
    for name, subtopics in CURRICULUM_TOPICS.get(subject_name, ()):
        if name == topic_name:
            return subtopics
    return (f"{topic_name} ideas", f"{topic_name} practice")


def slugify(value: str, *, fallback: str = "topic") -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return (slug[:100] or fallback)[:100]
