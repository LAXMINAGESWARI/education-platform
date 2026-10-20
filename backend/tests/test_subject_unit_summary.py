"""Subject feedback talks about units, and ignores textbook headings."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from education_platform.modules.assessments.feedback_hierarchy import (
    ScoredAttempt,
    display_unit_name,
    rollup_subject,
    rollup_topic,
    subject_summary_prompt,
    topic_summary,
    unit_template,
    units_from_topic_concepts,
)
from education_platform.modules.assessments.feedback_service import (
    AnswerOutcomeRow,
    aggregate_by_unit,
)
from education_platform.modules.assessments.models import QuestionDifficulty
from education_platform.modules.assessments.schemas import FeedbackSubtopicStrength
from education_platform.modules.generation.outline import is_outline_noise_heading

MENSURATION = UUID(int=1)
RATIOS = UUID(int=2)
PERCENTAGES = UUID(int=3)
ALGEBRA = UUID(int=4)
GEOMETRY = UUID(int=5)
ATTEMPT = UUID(int=50)


def _answer(
    *,
    subtopic_id: UUID,
    subtopic_name: str,
    topic_id: UUID,
    topic_name: str,
    correct: int,
    answered: int,
) -> list[AnswerOutcomeRow]:
    rows: list[AnswerOutcomeRow] = []
    for index in range(answered):
        rows.append(
            AnswerOutcomeRow(
                attempt_id=ATTEMPT,
                subtopic_id=subtopic_id,
                subtopic_name=subtopic_name,
                topic_id=topic_id,
                topic_name=topic_name,
                marks_awarded=Decimal("1") if index < correct else Decimal("0"),
                marks_available=Decimal("1"),
                question_version_id=UUID(int=1000 + subtopic_id.int * 20 + index),
                selected_option_label="A",
                is_correct=index < correct,
                difficulty=QuestionDifficulty.MEDIUM,
                submitted_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
    return rows


def _grade8_rows() -> list[AnswerOutcomeRow]:
    """Grade 8 Mathematics sample behind the subject card.

    ``WHAT HAVE WE DISCUSSED?`` is a perfect noise heading. ``EXERCISE 9.2`` and
    ``Solid Shapes`` are the weak names the old summary quoted. Solid Shapes is
    a real concept inside mensuration; the exercise is not.
    """
    rows: list[AnswerOutcomeRow] = []
    rows.extend(
        _answer(
            subtopic_id=UUID(int=11),
            subtopic_name="WHAT HAVE WE DISCUSSED?",
            topic_id=MENSURATION,
            topic_name="mensuration",
            correct=4,
            answered=4,
        )
    )
    rows.extend(
        _answer(
            subtopic_id=UUID(int=12),
            subtopic_name="EXERCISE 9.2",
            topic_id=MENSURATION,
            topic_name="mensuration",
            correct=1,
            answered=5,
        )
    )
    rows.extend(
        _answer(
            subtopic_id=UUID(int=13),
            subtopic_name="Solid Shapes",
            topic_id=MENSURATION,
            topic_name="mensuration",
            correct=3,
            answered=10,
        )
    )
    rows.extend(
        _answer(
            subtopic_id=UUID(int=14),
            subtopic_name="Area of a trapezium",
            topic_id=MENSURATION,
            topic_name="mensuration",
            correct=11,
            answered=20,
        )
    )
    rows.extend(
        _answer(
            subtopic_id=UUID(int=21),
            subtopic_name="Ratio and proportion",
            topic_id=RATIOS,
            topic_name="Ratios",
            correct=12,
            answered=25,
        )
    )
    rows.extend(
        _answer(
            subtopic_id=UUID(int=22),
            subtopic_name="TRY THESE",
            topic_id=RATIOS,
            topic_name="Ratios",
            correct=1,
            answered=10,
        )
    )
    rows.extend(
        _answer(
            subtopic_id=UUID(int=31),
            subtopic_name="Percentage increase",
            topic_id=PERCENTAGES,
            topic_name="Percentages",
            correct=3,
            answered=5,
        )
    )
    rows.extend(
        _answer(
            subtopic_id=UUID(int=32),
            subtopic_name="Summary",
            topic_id=PERCENTAGES,
            topic_name="Percentages",
            correct=9,
            answered=10,
        )
    )
    rows.extend(
        _answer(
            subtopic_id=UUID(int=41),
            subtopic_name="Algebraic expressions",
            topic_id=ALGEBRA,
            topic_name="Algebra",
            correct=31,
            answered=50,
        )
    )
    rows.extend(
        _answer(
            subtopic_id=UUID(int=42),
            subtopic_name="Think, Discuss and Write",
            topic_id=ALGEBRA,
            topic_name="Algebra",
            correct=1,
            answered=8,
        )
    )
    rows.extend(
        _answer(
            subtopic_id=UUID(int=51),
            subtopic_name="Do This",
            topic_id=GEOMETRY,
            topic_name="Geometry",
            correct=2,
            answered=5,
        )
    )
    rows.extend(
        _answer(
            subtopic_id=UUID(int=52),
            subtopic_name="Quadrilaterals",
            topic_id=GEOMETRY,
            topic_name="Geometry",
            correct=9,
            answered=10,
        )
    )
    return rows


def test_textbook_headings_in_the_sample_are_noise() -> None:
    for heading in (
        "WHAT HAVE WE DISCUSSED?",
        "EXERCISE 9.2",
        "TRY THESE",
        "Summary",
        "Think, Discuss and Write",
        "Do This",
    ):
        assert is_outline_noise_heading(heading)
    assert not is_outline_noise_heading("Solid Shapes")
    assert not is_outline_noise_heading("Area of a trapezium")


def test_subject_prompt_lists_units_and_drops_heading_rows() -> None:
    units = aggregate_by_unit(_grade8_rows())
    prompt = subject_summary_prompt("Mathematics", units)
    assert [unit.name for unit in units] == [
        "Mensuration",
        "Ratios",
        "Percentages",
        "Algebra",
        "Geometry",
    ]
    # Solid Shapes 30% and Area of a trapezium 55% average to 42.50.
    # The 100% discussion heading and the 20% exercise are not in that average.
    mensuration = units[0]
    assert mensuration.percent == Decimal("42.50")
    assert mensuration.attempt_count == 1
    assert "WHAT HAVE WE DISCUSSED" not in prompt
    assert "EXERCISE" not in prompt
    assert "TRY THESE" not in prompt
    assert "Solid Shapes" not in prompt
    assert "Think, Discuss and Write" not in prompt
    assert "Do This" not in prompt
    assert "subtopic" not in prompt.lower()
    assert "Units:" in prompt
    assert "- Mensuration: 42.50% over 1 attempt" in prompt
    assert "Strongest unit: Geometry (90.00%)" in prompt
    assert "Weakest unit: Mensuration (42.50%)" in prompt
    assert "Work on next: Mensuration, Ratios, Percentages, Algebra" in prompt
    fallback = unit_template("Mathematics", units)
    assert fallback.startswith("You're doing well in Geometry.")
    assert "Mensuration is at 42.50% over 1 attempt." in fallback
    assert "Algebra is at 62.00% over 1 attempt." in fallback
    assert "Work on Mensuration, Ratios, Percentages, Algebra next." in fallback
    assert "Solid Shapes" not in fallback


def test_unit_facts_from_topic_concepts_skip_noise_and_title_case() -> None:
    units = units_from_topic_concepts(
        [
            (
                "mensuration",
                2,
                [
                    ("WHAT HAVE WE DISCUSSED?", 4, 4),
                    ("EXERCISE 9.2", 1, 5),
                    ("Solid Shapes", 3, 10),
                    ("Area of a trapezium", 11, 20),
                ],
            ),
            ("Ratios", 1, [("Ratio and proportion", 12, 25), ("TRY THESE", 1, 10)]),
            ("EXERCISE 9.2", 3, [("A real idea", 1, 1)]),
            ("Geometry", 0, [("Quadrilaterals", 9, 10)]),
        ]
    )
    assert [(unit.name, unit.percent, unit.attempt_count) for unit in units] == [
        ("Mensuration", Decimal("42.50"), 2),
        ("Ratios", Decimal("48.00"), 1),
    ]


def test_needs_attention_uses_stored_unit_names_in_title_case() -> None:
    assert display_unit_name("mensuration") == "Mensuration"
    assert display_unit_name("Ratios") == "Ratios"
    assert display_unit_name("Data handling") == "Data Handling"
    assert display_unit_name("  mensuration  ") == "Mensuration"
    subject = rollup_subject(
        [
            (
                UUID(int=1),
                "mensuration",
                rollup_topic([ScoredAttempt(1, Decimal("6")), ScoredAttempt(2, Decimal("55"))]),
            ),
            (UUID(int=2), "Ratios", rollup_topic([ScoredAttempt(1, Decimal("48"))])),
            (UUID(int=3), "Percentages", rollup_topic([ScoredAttempt(1, Decimal("60"))])),
            (UUID(int=4), "Algebra", rollup_topic([ScoredAttempt(1, Decimal("62"))])),
            (UUID(int=5), "Geometry", rollup_topic([ScoredAttempt(1, Decimal("90"))])),
            (UUID(int=6), "Data handling", rollup_topic([ScoredAttempt(1, Decimal("87"))])),
            (UUID(int=7), "EXERCISE 9.2", rollup_topic([ScoredAttempt(1, Decimal("10"))])),
        ]
    )
    assert subject.score_percent == Decimal("67.00")
    assert subject.trend_delta == Decimal("49.00")
    assert subject.needing_attention == ("Mensuration", "Ratios", "Percentages", "Algebra")
    assert subject.strongest is not None and subject.strongest[1] == "Geometry"
    assert subject.weakest is not None and subject.weakest[1] == "Ratios"


def test_topic_page_summary_stays_at_concept_level() -> None:
    summary = topic_summary(
        "Mensuration",
        [
            FeedbackSubtopicStrength(
                subtopic_id=UUID(int=1),
                name="Solid Shapes",
                correct_count=3,
                answered_count=10,
                percent=Decimal("30"),
                band="needs_work",
            ),
            FeedbackSubtopicStrength(
                subtopic_id=UUID(int=2),
                name="Area of a trapezium",
                correct_count=8,
                answered_count=10,
                percent=Decimal("80"),
                band="strong",
            ),
        ],
    )
    assert "Solid Shapes" in summary
    assert "Area of a trapezium" in summary
    assert "EXERCISE" not in summary
