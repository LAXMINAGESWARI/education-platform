"""Largest Remainder and outline helpers."""

import json
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from education_platform.core.config import get_settings
from education_platform.modules.generation.adk import (
    OutlineRunRequest,
    OutlineRunResult,
    outline_writer_instruction,
)
from education_platform.modules.generation.adk_live import LiveOutlineRunner
from education_platform.modules.generation.outline import (
    assert_dag,
    combined_weight,
    ground_outline,
    is_outline_noise_heading,
    largest_remainder,
    outline_clusters,
    outline_from_headings,
    parse_proposed_outline,
    source_sections,
    write_outline,
)
from education_platform.modules.generation.types import (
    HeadingCluster,
    OutlineNodeEdit,
    ReviewStatus,
)


def test_largest_remainder_unit_weights() -> None:
    quotas = largest_remainder(
        (Decimal("0.46"), Decimal("0.33"), Decimal("0.21")),
        80,
    )
    assert quotas == (37, 26, 17)


def test_largest_remainder_tie_breaks_original_order() -> None:
    quotas = largest_remainder(
        (Decimal("0.8"), Decimal("0.4"), Decimal("0.8")),
        2,
    )
    assert quotas == (1, 0, 1)


def test_largest_remainder_rejects_all_zero_weights() -> None:
    with pytest.raises(ValueError, match="all-zero"):
        largest_remainder((Decimal("0"), Decimal("0")), 80)


def test_combined_weight_blends_equally() -> None:
    assert combined_weight(
        token_mass=3,
        prerequisite_score=Decimal("0.5"),
        centrality=Decimal("0.5"),
    ) == Decimal("4") / Decimal("3")


def test_outline_from_headings_is_flat() -> None:
    outline = outline_from_headings(
        (
            HeadingCluster(heading="Squares", token_mass=10, sample_texts=("a",)),
            HeadingCluster(heading="Triangles", token_mass=4, sample_texts=("b",)),
        )
    )
    assert len(outline.nodes) == 2
    assert outline.nodes[0].parent_key is None
    assert outline.nodes[0].prerequisite_score == Decimal("0.5")
    assert outline.nodes[0].slug == "squares"


def test_parse_proposed_outline_rejects_half_graphs() -> None:
    with pytest.raises(ValueError, match="nodes"):
        parse_proposed_outline({"nodes": []})
    with pytest.raises(ValueError, match="parent_key"):
        parse_proposed_outline(
            {
                "nodes": [
                    {
                        "key": "a",
                        "parent_key": "missing",
                        "slug": "a",
                        "title": "A",
                        "token_mass": 1,
                        "prerequisite_score": 0.5,
                        "centrality": 0.5,
                        "proposed_outcomes": ["Know A"],
                    }
                ]
            }
        )


def test_assert_dag_rejects_unknown_parent_and_cycles() -> None:
    node_id = uuid4()
    other = uuid4()
    with pytest.raises(ValueError, match="unknown parent"):
        assert_dag(
            (
                OutlineNodeEdit(
                    id=node_id,
                    parent_id=other,
                    slug="a",
                    title="A",
                    weight=Decimal("1"),
                    matched_subtopic_id=None,
                    force_create=False,
                    proposed_outcomes=("Know A",),
                    sequence=1,
                ),
            )
        )
    with pytest.raises(ValueError, match="cycle"):
        a = uuid4()
        b = uuid4()
        assert_dag(
            (
                OutlineNodeEdit(
                    id=a,
                    parent_id=b,
                    slug="a",
                    title="A",
                    weight=Decimal("1"),
                    matched_subtopic_id=None,
                    force_create=False,
                    proposed_outcomes=("Know A",),
                    sequence=1,
                ),
                OutlineNodeEdit(
                    id=b,
                    parent_id=a,
                    slug="b",
                    title="B",
                    weight=Decimal("1"),
                    matched_subtopic_id=None,
                    force_create=False,
                    proposed_outcomes=("Know B",),
                    sequence=2,
                ),
            )
        )


def _node(
    key: str, title: str, token_mass: int, parent_key: str | None = None
) -> dict[str, object]:
    return {
        "key": key,
        "parent_key": parent_key,
        "slug": key,
        "title": title,
        "token_mass": token_mass,
        "prerequisite_score": 0.5,
        "centrality": 0.5,
        "proposed_outcomes": [f"Study {title}"],
    }


# Headings and token masses from the indexed NCERT chapter hegp106
# ("We Distribute, Yet Things Multiply"), in document order.
_HEGP106_CLUSTERS = (
    HeadingCluster("WE DISTRIBUTE, YET THINGS MULTIPLY", 119, ("Chapter title.",)),
    HeadingCluster(
        "Increments in Products",
        513,
        (
            "a ( b + c ) = ab + ac",
            "This property can be visualised nicely using a diagram:\n$$a ( b + c ) = ab + ac$$",
        ),
    ),
    HeadingCluster("How do we expand this?", 1473, ("$$(a + m)(b + n) = ab + an + mb + mn$$",)),
    HeadingCluster("Can any two terms be added to get a single term?", 602, ()),
    HeadingCluster("A Pinch of History", 262, ()),
    HeadingCluster("Figure it Out", 3334, ()),
    HeadingCluster("Fast Multiplications Using the Distributive Property", 37, ()),
    HeadingCluster("When one of the numbers is 11, 101, 1001, ...", 674, ()),
    HeadingCluster("Square of the Sum/Difference of Two Numbers", 2243, ()),
    HeadingCluster("Pattern 1", 305, ()),
    HeadingCluster("Pattern 2", 256, ()),
    HeadingCluster("Hint:", 56, ()),
    HeadingCluster("Why is this identity true?", 107, ()),
    HeadingCluster("6.3 Mind the Mistake, Mend the Mistake", 74, ()),
    HeadingCluster("6.4 This Way or That Way, All Ways Lead to the Bay", 74, ()),
    HeadingCluster("Method 4", 353, ()),
    HeadingCluster("Tadang's method:", 66, ()),
    HeadingCluster("Yusuf's method:", 94, ()),
    HeadingCluster("Anusha's method:", 48, ()),
    HeadingCluster("Vaishnavi's method:", 74, ()),
    HeadingCluster("Aditya's method:", 159, ()),
    HeadingCluster("SUMMARY", 315, ()),
    HeadingCluster("Page No. 138", 88, ()),
    HeadingCluster("Ans.", 2148, ()),
    HeadingCluster("Ans. Let a and b be two numbers.", 104, ()),
    HeadingCluster("Step y .", 98, ()),
)


def test_hegp106_outline_stays_in_source_order_with_real_masses() -> None:
    """Renamed sections, wrong token mass, and dropped 6.3, 6.4, and Summary."""
    proposed = parse_proposed_outline(
        {
            "nodes": [
                _node("n1", "Understanding Distributive Property", 119),
                _node("n2", "Visual and Practical Applications", 162, "n1"),
                _node("n3", "Increments in Products", 1473, "n2"),
                _node("n4", "Expanding Algebraic Expressions", 602, "n3"),
                _node("n5", "Using Algebraic Identities", 2243, "n4"),
                _node("n6", "Algebraic Patterns", 561, "n5"),
                _node("n7", "Historical Context", 262, "n1"),
                _node("n8", "Fast Multiplication Strategies", 674, "n6"),
            ]
        }
    )
    grounded = ground_outline(proposed, _HEGP106_CLUSTERS)
    titles = [node.title for node in grounded.nodes]

    assert titles.index("Increments in Products") < titles.index("How do we expand this")
    assert titles.index("A Pinch of History") < titles.index(
        "Fast Multiplications Using the Distributive Property"
    )
    assert titles.index("Fast Multiplications Using the Distributive Property") < titles.index(
        "Square of the Sum/Difference of Two"
    )
    assert titles.index("Square of the Sum/Difference of Two") < titles.index("Pattern 1")
    assert titles[0] == "We distribute, yet things multiply"
    assert "Figure it Out" not in titles
    assert "Mind the Mistake, Mend the Mistake" in titles
    assert "This Way or That Way, All" in titles
    assert "SUMMARY" not in titles
    assert "Step y" in titles
    assert "Understanding Distributive Property" not in titles
    assert "Visual and Practical Applications" not in titles
    assert "Historical Context" not in titles
    assert all(not is_outline_noise_heading(title) for title in titles)
    assert "Ans." not in titles
    assert "Page No. 138" not in titles
    assert "Hint:" not in titles

    by_title = {node.title: node for node in grounded.nodes}
    assert by_title["Increments in Products"].token_mass == 513
    assert "This property can be visualised nicely using a diagram:" not in titles
    assert by_title["How do we expand this"].token_mass == 1473
    assert by_title["How do we expand this"].source_headings == ("How do we expand this?",)
    assert by_title["Square of the Sum/Difference of Two"].token_mass == 2243
    pinch = by_title["A Pinch of History"]
    assert pinch.token_mass == 262 + 3334
    assert pinch.source_headings == ("A Pinch of History", "Figure it Out")
    pattern_2 = by_title["Pattern 2"]
    assert pattern_2.token_mass == 256 + 56
    assert pattern_2.source_headings == ("Pattern 2", "Hint:")
    aditya = by_title["Aditya's method"]
    assert aditya.token_mass == 159 + 315
    assert aditya.source_headings == ("Aditya's method:", "SUMMARY")
    dropped = {"Page No. 138", "Ans.", "Ans. Let a and b be two numbers."}
    owners: dict[str, str] = {}
    for node in grounded.nodes:
        for key in node.source_headings:
            assert key not in owners
            owners[key] = node.title
    for cluster in _HEGP106_CLUSTERS:
        if cluster.heading in dropped:
            assert cluster.heading not in owners
        else:
            assert owners[cluster.heading]

    order = {node.key: index for index, node in enumerate(grounded.nodes)}
    for node in grounded.nodes:
        if node.parent_key is not None:
            assert order[node.parent_key] < order[node.key]


def test_ground_outline_groups_cited_headings_and_drops_forward_parents() -> None:
    clusters = (
        HeadingCluster("Pattern 1", 305, ()),
        HeadingCluster("Pattern 2", 256, ()),
        HeadingCluster("SUMMARY", 315, ()),
    )
    proposed = parse_proposed_outline(
        {
            "nodes": [
                {
                    **_node("summary", "SUMMARY", 1, "patterns"),
                    "headings": ["SUMMARY"],
                },
                {
                    **_node("patterns", "Algebraic Patterns", 1),
                    "headings": ["Pattern 1", "Pattern 2"],
                },
            ]
        }
    )
    grounded = ground_outline(proposed, clusters)
    assert [node.title for node in grounded.nodes] == ["Pattern 1", "Pattern 2"]
    assert [node.token_mass for node in grounded.nodes] == [305, 256 + 315]
    assert all(node.parent_key is None for node in grounded.nodes)
    assert grounded.nodes[1].source_headings == ("Pattern 2", "SUMMARY")
    assert grounded.nodes[1].proposed_outcomes == ()

    omitted = parse_proposed_outline({"nodes": [_node("only", "SUMMARY", 1)]})
    restored = ground_outline(
        omitted,
        (
            HeadingCluster("6.1 Some Properties of Multiplication", 0, (), None),
            HeadingCluster(
                "Increments in Products", 171, (), "6.1 Some Properties of Multiplication"
            ),
            HeadingCluster("SUMMARY", 80, ()),
        ),
    )
    by_title = {node.title: node for node in restored.nodes}
    assert (
        by_title["Increments in Products"].parent_key
        == by_title["Some Properties of Multiplication"].key
    )
    assert by_title["Some Properties of Multiplication"].parent_key is None
    assert "SUMMARY" not in by_title
    assert by_title["Increments in Products"].token_mass == 171 + 80
    assert by_title["Increments in Products"].source_headings == (
        "6.1 Some Properties of Multiplication > Increments in Products",
        "SUMMARY",
    )
    assert by_title["Some Properties of Multiplication"].token_mass == 0
    assert by_title["Some Properties of Multiplication"].prerequisite_score == Decimal("0")


def test_parse_proposed_outline_rejects_bad_headings() -> None:
    with pytest.raises(ValueError, match="headings"):
        parse_proposed_outline({"nodes": [{**_node("a", "A", 1), "headings": "A"}]})
    with pytest.raises(ValueError, match="headings"):
        parse_proposed_outline({"nodes": [{**_node("a", "A", 1), "headings": [""]}]})


# Chunk rows (heading, text, tokens) in hegp106 document order. "Figure it Out"
# recurs after several sections and folds into the concept before it. Method 1–3
# stay even under MIN_SECTION_TOKENS. Fast Multiplications is under the breadth
# cap, so it folds into A Pinch of History while the outline is still wider than
# eight nodes.
_HEGP106_ROWS: tuple[tuple[str | None, str, int], ...] = (
    (None, "", 1),
    ("WE DISTRIBUTE, YET THINGS MULTIPLY", "intro [Diagram]", 122),
    ("Increments in Products", "23 × 27", 171),
    ("How do we expand this?", "$$(a + m)(b + n)$$", 1491),
    ("A Pinch of History", "Brahmagupta", 262),
    ("Figure it Out", "exercise after history", 900),
    ("Fast Multiplications Using the Distributive Property", "heading only", 37),
    ("When one of the numbers is 11, 101, 1001, ...", "47 × 11", 692),
    ("Figure it Out", "exercise after fast multiplication", 400),
    ("Square of the Sum/Difference of Two Numbers", "$$(a + b)^2$$", 2288),
    ("Figure it Out", "exercise after squares", 600),
    ("Method 1", "m1", 5),
    ("Method 2", "m2", 5),
    ("Method 3", "m3", 5),
    ("Method 4", "m4", 377),
    ("SUMMARY", "summary", 327),
    ("Ans.", "answer key", 2148),
    ("Page No. 140", "answers", 103),
)


def test_hegp106_clusters_fold_exercises_and_tiny_headings() -> None:
    clusters = outline_clusters(source_sections(_HEGP106_ROWS))
    by_heading = {cluster.heading: cluster.token_mass for cluster in clusters}
    assert [cluster.heading for cluster in clusters] == [
        "WE DISTRIBUTE, YET THINGS MULTIPLY",
        "Increments in Products",
        "How do we expand this?",
        "A Pinch of History",
        "When one of the numbers is 11, 101, 1001, ...",
        "Square of the Sum/Difference of Two Numbers",
        "Method 1",
        "Method 2",
        "Method 3",
        "Method 4",
    ]
    by_cluster = {cluster.heading: cluster for cluster in clusters}
    pinch = by_cluster["A Pinch of History"]
    assert pinch.token_mass == 262 + 900 + 37
    assert pinch.source_headings == (
        "A Pinch of History",
        "Figure it Out",
        "Fast Multiplications Using the Distributive Property",
    )
    assert by_heading["When one of the numbers is 11, 101, 1001, ..."] == 692 + 400
    assert by_heading["Square of the Sum/Difference of Two Numbers"] == 2288 + 600
    assert by_heading["Method 1"] == 5
    assert by_heading["Method 2"] == 5
    assert by_heading["Method 3"] == 5
    method_4 = by_cluster["Method 4"]
    assert method_4.token_mass == 377 + 327
    assert method_4.source_headings == ("Method 4", "SUMMARY")
    method_1 = next(cluster for cluster in clusters if cluster.heading == "Method 1")
    assert method_1.sample_texts == ("m1",)
    assert all(cluster.parent_heading is None for cluster in clusters)


# Real hegp106 heading paths after numbered-level repair (Docling + chunk_docling_document).
_P61 = "6.1 Some Properties of Multiplication"
_P62 = "6.2 Special Cases of the Distributive Property"
_P64 = "6.4 This Way or That Way, All Ways Lead to the Bay"
_APPENDIX = "6 WE DISTRIUTE, YET THINGS MULTIPLY"
_HEGP106_PATH_ROWS: tuple[tuple[str | None, str, int], ...] = (
    (None, "", 1),
    ("WE DISTRIBUTE, YET THINGS MULTIPLY", "intro [Diagram]", 122),
    (f"{_P61} > Increments in Products", "23 × 27", 171),
    (f"{_P61} > This property can be visualised nicely using a diagram:", "[Diagram]", 354),
    (f"{_P61} > How do we expand this?", "$$(a + m)(b + n)$$", 1491),
    (f"{_P61} > A Pinch of History", "Brahmagupta", 262),
    (f"{_P61} > Figure it Out", "exercises", 900),
    (f"{_P61} > Fast Multiplications Using the Distributive Property", "heading", 37),
    (f"{_P62} > Square of the Sum/Difference of Two Numbers", "$$(a + b)^2$$", 2288),
    (f"{_P62} > Pattern 1", "p1", 305),
    (f"{_P62} > Hint:", "Hint: sridharacharya", 59),
    (f"{_P62} > Why is this identity true?", "because", 107),
    ("6.3 Mind the Mistake, Mend the Mistake", "check the simplifications", 77),
    (_P64, "intro", 80),
    (f"{_P64} > Method 1", "m1", 5),
    (f"{_P64} > Tadang's method:", "tadang", 66),
    (f"{_P64} > Aditya's method:", "aditya", 168),
    ("SUMMARY", "summary", 327),
    (f"{_APPENDIX} > Page No. 138", "answers", 88),
    (f"{_APPENDIX} > Figure it Out", "answer exercises", 1500),
    (f"{_APPENDIX} > Step y .", "answer step", 101),
)


def test_hegp106_numbered_sections_become_the_outline() -> None:
    clusters = outline_clusters(source_sections(_HEGP106_PATH_ROWS))
    assert [(cluster.heading, cluster.parent_heading) for cluster in clusters] == [
        ("WE DISTRIBUTE, YET THINGS MULTIPLY", None),
        (_P61, None),
        ("Increments in Products", _P61),
        ("How do we expand this?", _P61),
        ("A Pinch of History", _P61),
        (_P62, None),
        ("Square of the Sum/Difference of Two Numbers", _P62),
        ("Pattern 1", _P62),
        ("Why is this identity true?", _P62),
        ("6.3 Mind the Mistake, Mend the Mistake", None),
        (_P64, None),
        ("Method 1", _P64),
        ("Tadang's method:", _P64),
        ("Aditya's method:", _P64),
    ]
    by_heading = {cluster.heading: cluster for cluster in clusters}
    assert by_heading[_P61].token_mass == 0
    assert by_heading[_P62].token_mass == 0
    increments = by_heading["Increments in Products"]
    assert increments.token_mass == 171 + 354
    assert increments.sample_texts == (
        "23 × 27",
        "This property can be visualised nicely using a diagram:",
        "[Diagram]",
    )
    pinch = by_heading["A Pinch of History"]
    assert pinch.token_mass == 262 + 900 + 37
    assert pinch.source_headings == (
        f"{_P61} > A Pinch of History",
        f"{_P61} > Figure it Out",
        f"{_P61} > Fast Multiplications Using the Distributive Property",
    )
    assert by_heading["Pattern 1"].token_mass == 305
    why = by_heading["Why is this identity true?"]
    assert why.token_mass == 107 + 59
    assert why.sample_texts == ("sridharacharya", "because")
    assert by_heading["Square of the Sum/Difference of Two Numbers"].token_mass == 2288
    assert by_heading["Method 1"].token_mass == 5
    assert by_heading["Method 1"].sample_texts == ("m1",)
    assert by_heading["Tadang's method:"].token_mass == 66
    assert by_heading["Tadang's method:"].sample_texts == ("tadang",)
    aditya = by_heading["Aditya's method:"]
    assert aditya.token_mass == 168 + 327
    assert aditya.sample_texts == ("aditya", "summary")
    assert aditya.source_headings == (
        f"{_P64} > Aditya's method:",
        "SUMMARY",
    )
    assert by_heading[_P64].sample_texts == ("intro",)
    assert all("answer" not in text for cluster in clusters for text in cluster.sample_texts)
    assert all(cluster.heading != "Hint:" for cluster in clusters)

    crossed = outline_clusters(
        source_sections(
            [
                (f"{_P62} > Pattern 1", "p1", 40),
                (f"{_P62} > Hint:", "Hint: earlier", 8),
                ("6.3 Mind the Mistake, Mend the Mistake", "check", 77),
            ]
        )
    )
    crossed_by = {cluster.heading: cluster for cluster in crossed}
    assert crossed_by["Pattern 1"].token_mass == 48
    assert crossed_by["Pattern 1"].sample_texts == ("p1", "earlier")
    assert crossed_by["6.3 Mind the Mistake, Mend the Mistake"].token_mass == 77
    assert "Hint:" not in crossed_by

    outline = outline_from_headings(clusters)
    nodes = {node.title: node for node in outline.nodes}
    container = nodes["Some Properties of Multiplication"]
    assert container.parent_key is None
    assert container.token_mass == 0
    assert container.prerequisite_score == Decimal("0")
    assert container.centrality == Decimal("0")
    assert container.source_headings == (_P61,)
    assert nodes["Increments in Products"].parent_key == container.key
    assert nodes["Special Cases of the Distributive Property"].parent_key is None
    weights = tuple(
        combined_weight(
            token_mass=node.token_mass,
            prerequisite_score=node.prerequisite_score,
            centrality=node.centrality,
        )
        for node in outline.nodes
    )
    quotas = largest_remainder(weights, 80)
    assert quotas[outline.nodes.index(container)] == 0
    assert sum(quotas) == 80


def test_outline_clusters_merges_trailing_tiny_section_backward() -> None:
    sections = source_sections([("Main", "body", 200), ("Tail", "t", 3)])
    clusters = outline_clusters(sections)
    assert [(cluster.heading, cluster.token_mass) for cluster in clusters] == [("Main", 203)]
    lone = outline_clusters(source_sections([("Only", "x", 3)]))
    assert [(cluster.heading, cluster.token_mass) for cluster in lone] == [("Only", 3)]


class _RejectingRunner:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def generate(self, request: OutlineRunRequest) -> OutlineRunResult:
        _ = request
        return OutlineRunResult(
            review_status=ReviewStatus.REJECTED,
            reviewer_notes="6.3 should be merged into another section.",
            round_count=3,
            nodes_payload=self.payload,
            transcript={},
        )


def test_write_outline_keeps_grounded_draft_when_critic_rejects() -> None:
    clusters = (
        HeadingCluster("Pattern 1", 305, ()),
        HeadingCluster("6.3 Mind the Mistake, Mend the Mistake", 74, ()),
        HeadingCluster("Ans.", 900, ()),
    )
    runner = _RejectingRunner({"nodes": [{**_node("p", "Patterns", 1), "headings": ["Pattern 1"]}]})
    outline = write_outline(clusters, writer=None, runner=runner)
    assert [node.title for node in outline.nodes] == [
        "Pattern 1",
        "Mind the Mistake, Mend the Mistake",
    ]
    assert outline.nodes[0].token_mass == 305


def test_parse_proposed_outline_defaults_missing_or_bad_scores() -> None:
    raw = _node("a", "A", 1)
    del raw["prerequisite_score"]
    del raw["token_mass"]
    raw["centrality"] = "high"
    clamped = {**_node("b", "B", 1), "prerequisite_score": 1.7, "centrality": "NaN"}
    outline = parse_proposed_outline({"nodes": [raw, clamped]})
    assert outline.nodes[0].prerequisite_score == Decimal("0.5")
    assert outline.nodes[0].centrality == Decimal("0.5")
    assert outline.nodes[0].token_mass == 0
    assert outline.nodes[1].prerequisite_score == Decimal("1")
    assert outline.nodes[1].centrality == Decimal("0.5")


class _ApprovingRunner(_RejectingRunner):
    def generate(self, request: OutlineRunRequest) -> OutlineRunResult:
        _ = request
        return OutlineRunResult(
            review_status=ReviewStatus.APPROVED,
            reviewer_notes="",
            round_count=1,
            nodes_payload=self.payload,
            transcript={},
        )


def test_write_outline_approved_but_malformed_draft_does_not_fail_run() -> None:
    clusters = (HeadingCluster("Pattern 1", 305, ()), HeadingCluster("SUMMARY", 80, ()))
    outline = write_outline(
        clusters, writer=None, runner=_ApprovingRunner({"nodes": [{"key": "x"}]})
    )
    assert [node.title for node in outline.nodes] == ["Pattern 1"]
    assert outline.nodes[0].token_mass == 305 + 80
    assert outline.nodes[0].source_headings == ("Pattern 1", "SUMMARY")


def test_write_outline_falls_back_to_headings_when_rejected_draft_is_invalid() -> None:
    clusters = (
        HeadingCluster("Pattern 1", 305, ()),
        HeadingCluster("Page No. 140", 50, ()),
    )
    outline = write_outline(clusters, writer=None, runner=_RejectingRunner({"nodes": []}))
    assert [node.title for node in outline.nodes] == ["Pattern 1"]


def test_outline_writer_instruction_asks_for_title_parent_and_outcomes() -> None:
    text = outline_writer_instruction()
    assert '"parent_title": "Cleaned parent heading or null"' in text
    assert "at most 6 words" in text
    assert "Exercise n.n" in text
    assert "proposed_outcomes" in text
    assert "untrusted_source_excerpts" not in text
    assert "parent_key" not in text


def test_ground_outline_matches_outcomes_on_title_and_parent() -> None:
    """Same title under two parents must not share outcomes."""
    clusters = (
        HeadingCluster(_P61, 0, ("container text " * 40,), None),
        HeadingCluster("Shared pattern", 900, ("exercise after history",), _P61),
        HeadingCluster(_P62, 0, (), None),
        HeadingCluster("Shared pattern", 600, ("exercise after squares",), _P62),
        HeadingCluster("Pattern 1", 305, ("p1",), _P62),
        HeadingCluster("Closing notes", 80, ("recap",), None),
    )
    proposed = parse_proposed_outline(
        {
            "nodes": [
                {
                    "title": _P61,
                    "parent_title": None,
                    "proposed_outcomes": ["Use the distributive property"],
                },
                {
                    "title": "Shared pattern",
                    "parent_title": _P61,
                    "proposed_outcomes": ["Solve exercises on increments"],
                },
                {
                    "title": "shared pattern",
                    "parent_title": _P62.casefold(),
                    "proposed_outcomes": ["Solve exercises on squares"],
                },
                {
                    "title": "Shared pattern",
                    "parent_title": "6.3 Mind the Mistake, Mend the Mistake",
                    "proposed_outcomes": ["These outcomes must not attach"],
                },
                {
                    "title": "Pattern 1",
                    "parent_title": _P61,
                    "proposed_outcomes": ["Wrong parent outcomes"],
                },
                {
                    "title": "closing notes",
                    "parent_title": None,
                    "proposed_outcomes": ["Recap the chapter"],
                },
            ]
        }
    )
    assert [node.key for node in proposed.nodes] == ["n1", "n2", "n3", "n4", "n5", "n6"]
    assert all(node.parent_key is None for node in proposed.nodes)
    assert all(node.token_mass == 0 for node in proposed.nodes)
    assert proposed.nodes[0].prerequisite_score == Decimal("0.5")
    assert proposed.nodes[1].parent_title == _P61

    grounded = ground_outline(proposed, clusters)
    by_key = {node.key: node for node in grounded.nodes}

    def parent_title(node: Any) -> str | None:
        if node.parent_key is None:
            return None
        return str(by_key[node.parent_key].title)

    figures = [node for node in grounded.nodes if node.title == "Shared pattern"]
    assert [parent_title(node) for node in figures] == [
        "Some Properties of Multiplication",
        "Special Cases of the Distributive Property",
    ]
    assert figures[0].proposed_outcomes == ("Solve exercises on increments",)
    assert figures[1].proposed_outcomes == ("Solve exercises on squares",)
    assert figures[0].proposed_outcomes != figures[1].proposed_outcomes

    pattern = next(node for node in grounded.nodes if node.title == "Pattern 1")
    assert parent_title(pattern) == "Special Cases of the Distributive Property"
    assert pattern.proposed_outcomes == ()

    summary = next(node for node in grounded.nodes if node.title == "Closing notes")
    assert summary.parent_key is None
    assert summary.proposed_outcomes == ("Recap the chapter",)
    assert summary.token_mass == 80

    container = next(node for node in grounded.nodes if node.source_headings == (_P61,))
    assert container.token_mass == 0
    assert container.proposed_outcomes == ("Use the distributive property",)
    assert all(
        "must not attach" not in outcome and "Wrong parent" not in outcome
        for node in grounded.nodes
        for outcome in node.proposed_outcomes
    )


def test_outline_user_message_sends_one_short_excerpt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _capture(**kwargs: Any) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
        captured["user_message"] = kwargs["user_message"]
        captured["initial_state"] = kwargs["initial_state"]
        return {"outline_draft": "", "review_status": "approved"}, []

    monkeypatch.setattr(
        "education_platform.modules.generation.adk_live.run_live_agent",
        _capture,
    )
    long_excerpt = "A" * 400
    LiveOutlineRunner().generate(
        OutlineRunRequest(
            job_id=uuid4(),
            clusters=(
                HeadingCluster("6.1 Properties", 0, (long_excerpt, "container extra"), None),
                HeadingCluster(
                    "Increments in Products",
                    171,
                    (long_excerpt, "second sample"),
                    "6.1 Properties",
                ),
            ),
            model="openrouter/openai/gpt-4o",
            max_review_rounds=2,
        )
    )
    message = captured["user_message"]
    assert isinstance(message, str)
    state = captured["initial_state"]
    assert isinstance(state, dict)
    assert "untrusted_source_excerpts" not in state
    assert "<<<SOURCE" not in message
    rows = json.loads(message.split("\n", 1)[1])
    assert rows[0] == {"heading": "6.1 Properties", "parent": None}
    assert "excerpt" not in rows[0]
    assert rows[1]["heading"] == "Increments in Products"
    assert rows[1]["parent"] == "6.1 Properties"
    assert rows[1]["excerpt"] == "A" * 200
    assert long_excerpt not in message
    assert "second sample" not in message
    assert "container extra" not in message


def test_write_outline_uses_openrouter_model(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_model", "openai/gpt-4o-mini")
    monkeypatch.setattr(settings, "adk_model", "openrouter/openai/gpt-4o")
    seen: dict[str, str] = {}

    class _Capture(_RejectingRunner):
        def generate(self, request: OutlineRunRequest) -> OutlineRunResult:
            seen["model"] = request.model
            return super().generate(request)

    outline = write_outline(
        (HeadingCluster("Pattern 1", 305, ("body",)),),
        writer=None,
        runner=_Capture(
            {
                "nodes": [
                    {
                        "title": "Pattern 1",
                        "parent_title": None,
                        "proposed_outcomes": ["See the pattern"],
                    }
                ]
            }
        ),
    )
    assert seen["model"] == "openai/gpt-4o-mini"
    assert seen["model"] != "openrouter/openai/gpt-4o"
    assert outline.nodes[0].proposed_outcomes == ("See the pattern",)
    assert outline.nodes[0].token_mass == 305


def test_ground_outline_leaves_proposal_when_headings_are_all_noise() -> None:
    proposed = parse_proposed_outline({"nodes": [_node("a", "Kept", 4)]})
    grounded = ground_outline(proposed, (HeadingCluster("Ans.", 9, ()),))
    assert grounded.nodes[0].title == "Kept"
    assert grounded.nodes[0].token_mass == 4
