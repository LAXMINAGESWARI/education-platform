"""Unit tests verifying generation improvements and bug fixes."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from education_platform.modules.generation.adk import (
    ItemNodeSpec,
    ItemsRunRequest,
    extract_json_object,
    items_from_node_state,
    items_result_from_state,
    markdown_from_lesson_draft,
    repair_latex_controls,
)
from education_platform.modules.generation.adk_live import LiveItemsRunner
from education_platform.modules.generation.blueprint import ITEM_BATCH_MAX
from education_platform.modules.generation.items import (
    items_from_chunks,
    write_items_for_nodes,
)
from education_platform.modules.generation.lesson import (
    assert_parseable_mermaid,
    repair_mermaid_diagram,
    repair_mermaid_fences,
)
from education_platform.modules.generation.lesson_checks import source_method_missing
from education_platform.modules.generation.router import (
    generate_curriculum,
    show_curriculum_job,
)
from education_platform.modules.generation.types import (
    SUBTOPIC_GENERATION_GONE,
    ReviewStatus,
)


def test_extract_json_object_with_latex_and_trailing_commas() -> None:
    # Contains LaTeX escapes (\frac, \times, \alpha) which break standard json.loads if unescaped
    raw_response = r"""
    Here is the JSON response:
    ```json
    {
      "explanation": "Compute \frac{a}{b} \times \alpha_1",
      "items": [
        {"prompt": "Calculate $x \implies y$", "value": 42},
      ]
    }
    ```
    """
    data = extract_json_object(raw_response)
    assert data is not None
    assert data["items"][0]["value"] == 42
    assert "frac" in data["explanation"]
    assert "implies" in data["items"][0]["prompt"]


def test_extract_json_object_keeps_json_string_newlines() -> None:
    data = extract_json_object('{"text": "line1\\nline2"}')
    assert data is not None
    assert data["text"] == "line1\nline2"


def test_extract_json_object_newlines_survive_latex_sanitize() -> None:
    raw = r"""
    {
      "text": "line1\nline2",
      "latex": "Compute \frac{a}{b} \times \begin{align} \text{ok}",
    }
    """
    data = extract_json_object(raw)
    assert data is not None
    assert data["text"] == "line1\nline2"
    assert r"\frac" in data["latex"]
    assert r"\times" in data["latex"]
    assert r"\begin" in data["latex"]
    assert r"\text" in data["latex"]


def test_extract_json_object_keeps_latex_delimiters_json_would_reject() -> None:
    raw = (
        '{"lesson_markdown": "## Solving\\n\\n'
        "See \\(2x - 3 = 7\\) and \\frac{1}{2}.\\n"
        '$$\\n2x &= 4 \\\\ next$$"}'
    )
    text = markdown_from_lesson_draft(raw)
    assert text.startswith("## Solving")
    assert r"\(2x - 3 = 7\)" in text
    assert r"\frac{1}{2}" in text
    assert '{"lesson_markdown"' not in text
    assert "\x0c" not in text


def test_repair_latex_controls_restores_frac_and_times() -> None:
    broken = "$$2 \\\x0crac{5}{4}$$ and $2 \\\times 5$"
    repaired = repair_latex_controls(broken)
    assert r"\frac{5}{4}" in repaired
    assert r"\times" in repaired
    assert "\x0c" not in repaired
    assert "\t" not in repaired


def test_extract_json_object_keeps_mermaid_inside_json_string() -> None:
    lesson = "# Squares\n\n```mermaid\nflowchart LR\n    side --> area\n```\n"
    data = extract_json_object(json.dumps({"lesson_markdown": lesson}))
    assert data is not None
    assert data["lesson_markdown"] == lesson


def test_items_from_chunks_balances_correct_labels() -> None:
    from uuid import uuid4

    from education_platform.modules.generation.items import NodeItemRequest
    from education_platform.modules.generation.types import BloomLevel

    node = NodeItemRequest(
        subtopic_id=uuid4(),
        learning_outcome_ids=(),
        heading="Fraction Multiplication",
        chunk_texts=("Multiply numerators and denominators.",),
        quota=8,
        bloom=(BloomLevel.APPLY,) * 8,
    )
    items = items_from_chunks(node)
    assert len(items) == 8
    assert all("source excerpt" not in item.prompt.casefold() for item in items)
    assert all(not item.prompt.startswith("Item ") for item in items)
    assert "Fraction Multiplication" not in items[0].prompt
    assert "excerpt" not in " ".join(items[0].options.values()).casefold()
    exercise = items_from_chunks(
        NodeItemRequest(
            subtopic_id=node.subtopic_id,
            learning_outcome_ids=(),
            heading="EXERCISE 2.2",
            chunk_texts=("EXERCISE 2.2 Solve $$x + 1 = 4$$.",),
            quota=1,
            bloom=(BloomLevel.REMEMBER,),
        )
    )[0]
    blob = f"{exercise.prompt} {' '.join(exercise.options.values())}"
    assert "EXERCISE" not in blob
    assert "source excerpt" not in blob.casefold()
    assert "x + 1 = 4" in blob
    labels = [item.correct_label for item in items]
    assert set(labels) == {"A", "B", "C", "D"}
    # Verify not all items are 'A'
    assert labels[0] == "A"
    assert labels[1] == "B"
    assert labels[2] == "C"
    assert labels[3] == "D"


def test_live_items_runner_scopes_each_node(monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[str] = []

    def _run(**kwargs: object) -> tuple[dict[str, object], list[object]]:
        message = str(kwargs["user_message"])
        messages.append(message)
        session_id = str(kwargs["session_id"])
        if session_id.endswith(":misconceptions"):
            return (
                {"misconceptions": {"misconceptions": [{"id": "m1", "label": "sign-error"}]}},
                [],
            )
        return (
            {
                "items_review_0": {"review_status": "approved", "reviewer_notes": "ok"},
                "quiz_review_status": "approved",
            },
            [],
        )

    monkeypatch.setattr(
        "education_platform.modules.generation.adk_live.build_misconception_agent",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "education_platform.modules.generation.adk_live.build_single_node_items_agent",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr("education_platform.modules.generation.adk_live.run_live_agent", _run)
    lesson = (
        "## Alpha shapes\n\n"
        "Alpha teaching talks about equal sides.\n\n"
        "## Beta angles\n\n"
        "Beta teaching talks about angle sums.\n"
    )
    LiveItemsRunner().generate(
        ItemsRunRequest(
            job_id=uuid4(),
            nodes=(
                ItemNodeSpec(
                    key="alpha",
                    heading="Alpha shapes",
                    chunk_texts=("UNIQUE_ALPHA_EXCERPT",),
                    quota=2,
                    bloom=("remember", "apply"),
                ),
                ItemNodeSpec(
                    key="beta",
                    heading="Beta angles",
                    chunk_texts=("UNIQUE_BETA_EXCERPT",),
                    quota=4,
                    bloom=("understand",),
                ),
            ),
            lesson_markdown=lesson,
            model="openrouter/openai/gpt-4o-mini",
            max_review_rounds=1,
        )
    )
    assert len(messages) == 3
    assert "UNIQUE_ALPHA_EXCERPT" not in messages[0]
    assert "UNIQUE_BETA_EXCERPT" not in messages[0]
    assert "equal sides" in messages[0]
    assert "angle sums" in messages[0]
    assert "sign-error" not in messages[0]
    assert "UNIQUE_ALPHA_EXCERPT" in messages[1]
    assert "UNIQUE_BETA_EXCERPT" not in messages[1]
    assert "equal sides" in messages[1]
    assert "angle sums" not in messages[1]
    assert "sign-error" in messages[1]
    assert '"quota": 2' in messages[1]
    assert '"quota": 4' not in messages[1]
    assert "UNIQUE_BETA_EXCERPT" in messages[2]
    assert "UNIQUE_ALPHA_EXCERPT" not in messages[2]
    assert "angle sums" in messages[2]
    assert "equal sides" not in messages[2]
    assert '"quota": 4' in messages[2]
    assert '"quota": 2' not in messages[2]


def test_write_items_for_nodes_batches_large_quota() -> None:
    from education_platform.modules.assessments.models import (
        QuestionDifficulty,
        QuestionItemKind,
    )
    from education_platform.modules.generation.adk import (
        BankItem,
        ItemsRunResult,
    )
    from education_platform.modules.generation.items import NodeItemRequest
    from education_platform.modules.generation.types import BloomLevel, ReviewStatus

    calls: list[ItemsRunRequest] = []

    class MockRunner:
        def generate(self, request: ItemsRunRequest) -> ItemsRunResult:
            calls.append(request)
            items: list[BankItem] = []
            for spec in request.nodes:
                items.extend(
                    [
                        BankItem(
                            prompt=f"Item {i}",
                            options={"A": "1", "B": "2", "C": "3", "D": "4"},
                            correct_label="A",
                            explanation="",
                            difficulty=QuestionDifficulty.EASY,
                            item_kind=QuestionItemKind.PROBLEM,
                            source_method=spec.heading,
                            bloom=BloomLevel.APPLY,
                        )
                        for i in range(spec.quota)
                    ]
                )
            return ItemsRunResult(
                review_status=ReviewStatus.APPROVED,
                reviewer_notes="",
                round_count=1,
                items=tuple(items),
                misconceptions=(),
                transcript={},
            )

    # Request a node with quota 35 (> ITEM_BATCH_MAX = 16)
    large_node = NodeItemRequest(
        subtopic_id=uuid4(),
        learning_outcome_ids=(),
        heading="Large Topic",
        chunk_texts=("Some reference text",),
        quota=35,
        bloom=(BloomLevel.APPLY,) * 35,
    )
    written = write_items_for_nodes((large_node,), writer=None, runner=MockRunner())

    assert len(written[0]) == 35
    # 35 items should be split into 3 batch specs sent to runner: 16, 16, 3
    assert len(calls) == 1
    assert len(calls[0].nodes) == 3
    assert calls[0].nodes[0].quota == ITEM_BATCH_MAX
    assert calls[0].nodes[1].quota == ITEM_BATCH_MAX
    assert calls[0].nodes[2].quota == 3


def test_source_method_missing_fuzzy_token_overlap() -> None:
    lesson_md = (
        "# Quadratic Equations\n"
        "## Factoring Methods\n"
        "In this section, we study step-by-step factoring methods to find quadratic roots.\n"
    )
    # Exact match
    assert not source_method_missing(lesson_md, "step-by-step factoring methods")
    # Slight wording variance / case difference
    assert not source_method_missing(lesson_md, "Factoring Methods with step-by-step approach")
    # Completely missing method
    assert source_method_missing(lesson_md, "Completing the square geometric proof")


def test_repair_mermaid_diagram_fixes_syntax() -> None:
    # Missing flowchart start
    broken_1 = "idea[Idea] --> step[Step]\n"
    repaired_1 = repair_mermaid_diagram(broken_1, "My Section")
    assert repaired_1.startswith("flowchart TD")
    assert "Idea" in repaired_1
    assert "apply[Apply]" not in repaired_1

    # Unbalanced brackets
    broken_2 = "flowchart TD\n  idea[Missing closing bracket --> step[Step]"
    repaired_2 = repair_mermaid_diagram(broken_2, "My Section")
    assert repaired_2.count("[") == repaired_2.count("]")
    assert "Missing closing bracket" in repaired_2
    assert "apply[Apply]" not in repaired_2

    # Empty fence is not repaired into a stub
    with pytest.raises(ValueError, match="empty"):
        repair_mermaid_diagram("", "Fallback Section")


def test_repair_mermaid_keeps_labeled_diagram_not_generic_stub() -> None:
    broken = "side[Side length] --> area[Area of square"
    repaired = repair_mermaid_diagram(broken, "Squares")
    assert "Side length" in repaired
    assert "Area of square" in repaired
    assert "apply[Apply]" not in repaired


def test_lesson_without_mermaid_is_valid() -> None:
    lesson_without_mermaid = "## Introduction\nTeaching prose.\n\nA worked example. Sample problem."
    assert repair_mermaid_fences(lesson_without_mermaid, "Introduction") == lesson_without_mermaid
    assert_parseable_mermaid(lesson_without_mermaid)


def test_repair_mermaid_fences_does_not_stub_broken_labeled_fence() -> None:
    markdown = (
        "## Squares\n```mermaid\nside[Side length] --> area[Area of square\n```\n"
        "A worked example. Sample."
    )
    repaired = repair_mermaid_fences(markdown, "Squares")
    assert "Side length" in repaired
    assert "apply[Apply]" not in repaired
    assert_parseable_mermaid(repaired)


def test_items_from_node_state_orders_by_numeric_index() -> None:
    items = items_from_node_state(
        {
            "items_node_10": [{"prompt": "ten"}],
            "items_node_2": [{"prompt": "two"}],
        }
    )
    assert [item["prompt"] for item in items] == ["two", "ten"]


def test_items_result_aggregates_reviews_any_reject_wins() -> None:
    result = items_result_from_state(
        {
            "items_review_0": {
                "review_status": "approved",
                "reviewer_notes": "Node 0 keys match.",
            },
            "items_review_1": {
                "review_status": "rejected",
                "reviewer_notes": "Node 1 distractors collide.",
            },
        }
    )
    assert result.review_status is ReviewStatus.REJECTED
    assert "Node 0 keys match." in result.reviewer_notes
    assert "Node 1 distractors collide." in result.reviewer_notes


@pytest.mark.asyncio
async def test_subtopic_endpoints_return_410_gone() -> None:
    from uuid import uuid4

    from education_platform.api.deps import Principal

    admin = Principal(
        user_id=uuid4(),
        institution_id=uuid4(),
        roles=frozenset({"administrator"}),
        email="admin@example.com",
        student_profile_id=None,
        status="active",
    )

    with pytest.raises(HTTPException) as exc_info:
        await generate_curriculum(uuid4(), principal=admin)
    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == SUBTOPIC_GENERATION_GONE

    with pytest.raises(HTTPException) as exc_info:
        await show_curriculum_job(uuid4(), principal=admin)
    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == SUBTOPIC_GENERATION_GONE
