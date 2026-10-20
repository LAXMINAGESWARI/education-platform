"""Outline names stay student-facing without dropping PDF chunks."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from education_platform.modules.academics.models import Subtopic
from education_platform.modules.generation.items import OutlineNodeRef, texts_for_nodes
from education_platform.modules.generation.outline import (
    MAX_OUTLINE_NODES,
    heading_key,
    is_activity_noise_heading,
    is_outline_noise_heading,
    match_existing_subtopics,
    nearest_concept_position,
    normalize_outline_title,
    outline_clusters,
    outline_from_headings,
    qualify_child_title,
    section_group_label,
    source_sections,
)
from education_platform.modules.generation.rewrite import rewrite_outline_snapshot
from education_platform.modules.generation.types import (
    ChangeKind,
    ChangeRequest,
    DraftOutlineRequest,
    HumanActorStamp,
    ItemCount,
    OutlineField,
    OutlineNodeSnapshot,
    OutlineNodeTarget,
    OutlineSnapshot,
    ProposedOutcomeSnapshot,
)

# Grade 8 mensuration headings, in PDF order. Noise boxes sit after the concept
# they belong to. Each text is a unique chunk so coverage can be checked exactly.
_MENSURATION_ROWS: tuple[tuple[str, str, int], ...] = (
    ("9.1    Introduction", "chunk-intro", 90),
    ("TRY THESE", "chunk-try-intro", 40),
    ("9.2 Area of a trapezium", "chunk-trapezium", 200),
    ("DO THIS", "chunk-do-trapezium", 30),
    ("EXAMPLE 1", "chunk-example-trapezium", 50),
    ("Solution: Aman's method:", "chunk-solution-aman", 70),
    ("EXERCISE 9.1", "chunk-exercise-9-1", 80),
    ("9.3 Area of a general quadrilateral", "chunk-quadrilateral", 180),
    ("THINK, DISCUSS AND WRITE", "chunk-think-quad", 25),
    ("THINK DISCUSS AND WRITE", "chunk-think-quad-plain", 25),
    ("EXAMPLE 2", "chunk-example-quad", 40),
    ("9.4 Area of a polygon", "chunk-polygon", 160),
    ("Figure 9.3 A pentagon", "chunk-figure-pentagon", 20),
    ("Table 9.1", "chunk-table-9-1", 15),
    ("SURFACE AREA OF A CUBOID", "chunk-cuboid-area", 150),
    ("EXERCISE 9.2", "chunk-exercise-9-2", 60),
    ("Volume of a cylinder", "chunk-cylinder-volume", 170),
    ("What have we discussed?", "chunk-discussed", 40),
)

_NOISE_HEADINGS = (
    "TRY THESE",
    "THINK, DISCUSS AND WRITE",
    "THINK DISCUSS AND WRITE",
    "DO THIS",
    "EXAMPLE 1",
    "EXAMPLE 2",
    "Solution: Aman's method:",
    "EXERCISE 9.1",
    "EXERCISE 9.2",
    "What have we discussed?",
    "Figure 9.3 A pentagon",
    "Table 9.1",
    "SUMMARY",
    "Figure it Out",
)


def test_noise_headings_are_not_concepts() -> None:
    for heading in _NOISE_HEADINGS:
        assert is_activity_noise_heading(heading), heading
        assert is_outline_noise_heading(heading), heading
    assert is_outline_noise_heading("Ans.")
    assert is_outline_noise_heading("Page No. 138")
    assert is_outline_noise_heading("Hint:")
    assert not is_activity_noise_heading("Ans.")
    for heading in (
        "Area of a trapezium",
        "Surface area of a cuboid",
        "Volume of a cylinder",
        "9.2 Area of a trapezium",
    ):
        assert not is_activity_noise_heading(heading), heading
    assert is_activity_noise_heading("Tadang's method:")
    assert is_activity_noise_heading("Ridhima's method:")
    assert is_activity_noise_heading("Method 1")
    for heading in (
        "WHAT HAVE WE DISCUSSED?",
        "EXERCISE 9.2",
        "exercise 9.2",
        "Exercise 9.2.",
        "TRY THESE",
        "Summary",
        "Think, Discuss and Write",
        "Do This",
    ):
        assert is_outline_noise_heading(heading), heading


def test_normalize_outline_title_cleans_mensuration_names() -> None:
    assert normalize_outline_title("9.1    Introduction") == "Introduction"
    assert normalize_outline_title("9.2 Area of a trapezium") == "Area of a trapezium"
    assert normalize_outline_title("SURFACE AREA OF A CUBOID") == "Surface area of a cuboid"
    assert normalize_outline_title("Volume of a cylinder") == "Volume of a cylinder"
    assert normalize_outline_title("Area of a Trapezium") == "Area of a trapezium"
    assert normalize_outline_title("Tadang's method:") == "Tadang's method"
    assert normalize_outline_title("Solution: Aman's method:") == "Solution: Aman's method"
    assert heading_key("Solution: Aman's method:") == "Solution: Aman's method"
    assert (
        normalize_outline_title("6.4 This Way or That Way, All Ways Lead to the Bay")
        == "This way or that way, all ways lead to the bay"
    )
    assert (
        normalize_outline_title("9.4 Surface Area of Cube, Cuboid and Cylinder")
        == "Surface area of cube, cuboid and cylinder"
    )
    assert (
        normalize_outline_title("WE DISTRIBUTE, YET THINGS MULTIPLY")
        == "We distribute, yet things multiply"
    )
    assert normalize_outline_title("  9.1    Introduction  ") == normalize_outline_title(
        "Introduction"
    )
    full = normalize_outline_title("9.4 Surface Area of Cube, Cuboid and Cylinder")
    assert "cylinder" in full.casefold()
    assert "cuboid" in full.casefold()


def test_one_word_child_takes_the_parent_concept() -> None:
    parent = "9.4 Surface Area of Cube, Cuboid and Cylinder"
    assert qualify_child_title(parent, "9.4.1 Cuboid") == "Surface area of a cuboid"
    assert qualify_child_title(parent, "9.4.2 Cube") == "Surface area of a cube"
    assert qualify_child_title(parent, "9.4.3 Cylinders") == "Surface area of a cylinder"
    volume = "9.5 Volume of Cube, Cuboid and Cylinder"
    assert qualify_child_title(volume, "Cube") == "Volume of a cube"
    assert (
        qualify_child_title("6.2 Special Cases of the Distributive Property", "Pattern 1") is None
    )
    assert qualify_child_title("6.2 Special Cases", "Shared pattern") is None


def _groups(
    rows: tuple[tuple[str, str, int], ...],
) -> list[tuple[str, tuple[str, ...]]]:
    return [(section_group_label(section), section.texts) for section in source_sections(rows)]


def _refs_for(rows: tuple[tuple[str, str, int], ...]) -> tuple[list[OutlineNodeRef], list[str]]:
    outline = outline_from_headings(outline_clusters(source_sections(rows)))
    by_key = {node.key: node for node in outline.nodes}
    refs: list[OutlineNodeRef] = []
    titles: list[str] = []
    for index, node in enumerate(outline.nodes):
        parent = by_key[node.parent_key] if node.parent_key is not None else None
        refs.append(
            OutlineNodeRef(
                id=uuid4(),
                slug=node.slug,
                title=node.title,
                sequence=index + 1,
                parent_title=None if parent is None else parent.title,
                source_headings=node.source_headings,
            )
        )
        titles.append(node.title)
    return refs, titles


def test_mensuration_outline_drops_noise_and_stays_in_range() -> None:
    _refs, titles = _refs_for(_MENSURATION_ROWS)
    assert titles == [
        "Introduction",
        "Area of a trapezium",
        "Area of a general quadrilateral",
        "Area of a polygon",
        "Surface area of a cuboid",
        "Volume of a cylinder",
    ]
    assert 3 <= len(titles) <= MAX_OUTLINE_NODES
    folded = {title.casefold() for title in titles}
    for heading in _NOISE_HEADINGS:
        assert normalize_outline_title(heading).casefold() not in folded


def test_chunk_coverage_is_unchanged_after_normalization() -> None:
    """Every chunk that mapped to a heading still maps to exactly one node.

    Mapping keys are the original group labels (``source_headings``), not the
    cleaned display title. Lesson and item generation walk nodes in this same
    sequence, which follows PDF order.
    """
    groups = _groups(_MENSURATION_ROWS)
    before = [text for _label, texts in groups for text in texts]
    refs, titles = _refs_for(_MENSURATION_ROWS)
    assigned = texts_for_nodes(tuple(refs), groups)
    after = [text for texts in assigned.values() for text in texts]
    assert sorted(after) == sorted(before)
    assert len(after) == len(before)
    assert len(set(after)) == len(before)

    by_title = {ref.title: ref for ref in refs}
    trapezium = by_title["Area of a trapezium"]
    trapezium_texts = assigned[trapezium.id]
    assert trapezium_texts == (
        "chunk-trapezium",
        "chunk-do-trapezium",
        "chunk-example-trapezium",
        "chunk-solution-aman",
        "chunk-exercise-9-1",
    )
    assert "9.2 Area of a trapezium" in trapezium.source_headings
    assert "EXERCISE 9.1" in trapezium.source_headings
    assert "Solution: Aman's method:" in trapezium.source_headings
    assert trapezium.title != "9.2 Area of a trapezium"
    assert assigned[by_title["Introduction"].id] == ("chunk-intro", "chunk-try-intro")
    assert assigned[by_title["Surface area of a cuboid"].id] == (
        "chunk-cuboid-area",
        "chunk-exercise-9-2",
    )
    assert assigned[by_title["Volume of a cylinder"].id][-1] == "chunk-discussed"
    assert titles == [
        "Introduction",
        "Area of a trapezium",
        "Area of a general quadrilateral",
        "Area of a polygon",
        "Surface area of a cuboid",
        "Volume of a cylinder",
    ]


# Prod mensuration failure: the same box heading repeats, and Docling stored
# later numbered sections under that box. Each box chunk belongs to the
# concept that precedes it. 9.4 and 9.5 keep their own intro chunks. 9.4.2
# stays even when the stored path parents it under DO THIS.
_REPEATED_BOX_ROWS: tuple[tuple[str, str, int], ...] = (
    ("9.1 Introduction", "intro", 115),
    ("9.2 Area of a Polygon", "polygon", 191),
    ("TRY THESE", "try-page-2", 40),
    ("Solution: Aman's method:", "aman", 100),
    ("Ridhima's method:", "ridhima", 133),
    ("EXERCISE 9.1", "exercise-9-1", 200),
    ("9.3 Solid Shapes", "solids", 245),
    ("DO THIS", "do-page-5", 30),
    ("THINK, DISCUSS AND WRITE", "think-page-6", 20),
    ("9.4 Surface Area of Cube, Cuboid and Cylinder", "surface-intro", 90),
    ("9.4 Surface Area of Cube, Cuboid and Cylinder > 9.4.1 Cuboid", "cuboid-surface", 283),
    ("TRY THESE", "try-page-7", 15),
    ("DO THIS > 9.4.2 Cube", "cube-surface", 80),
    ("THINK, DISCUSS AND WRITE > 9.4.3 Cylinders", "cylinder-surface", 38),
    ("TRY THESE", "try-page-10", 18),
    ("9.5 Volume of Cube, Cuboid and Cylinder", "volume-intro", 70),
    ("9.5 Volume of Cube, Cuboid and Cylinder > 9.5.1 Cuboid", "cuboid-volume", 320),
    ("DO THIS > 9.5.2 Cube", "cube-volume", 40),
    ("THINK, DISCUSS AND WRITE > 9.5.3 Cylinder", "cylinder-volume", 130),
    ("TRY THESE", "try-page-15", 8),
    ("9.6 Volume and Capacity", "capacity", 400),
)


def test_repeated_boxes_follow_reading_order_and_keep_numbered_sections() -> None:
    groups = _groups(_REPEATED_BOX_ROWS)
    before = [text for _label, texts in groups for text in texts]
    outline = outline_from_headings(outline_clusters(source_sections(_REPEATED_BOX_ROWS)))
    refs, titles = _refs_for(_REPEATED_BOX_ROWS)
    assigned = texts_for_nodes(tuple(refs), groups)
    after = [text for texts in assigned.values() for text in texts]
    assert sorted(after) == sorted(before)
    assert len(after) == len(before) == len(set(before))

    by_title = {ref.title: ref for ref in refs}
    folded = {title.casefold() for title in titles}
    for noise in (
        "TRY THESE",
        "DO THIS",
        "THINK, DISCUSS AND WRITE",
        "EXERCISE 9.1",
        "Ridhima's method:",
        "Solution: Aman's method:",
    ):
        assert noise.casefold() not in folded
        assert normalize_outline_title(noise).casefold() not in folded
    assert all(ref.parent_title is None for ref in refs)
    assert titles == [
        "Area of a polygon",
        "Solid shapes",
        "Surface area of a cuboid",
        "Surface area of a cube",
        "Surface area of a cylinder",
        "Volume of a cuboid",
        "Volume of a cube",
        "Volume of a cylinder",
        "Volume and capacity",
    ]
    # Each solid measure is its own concept, so this chapter sits one over the target.
    assert 3 <= len(titles) <= MAX_OUTLINE_NODES + 1
    assert len({title.casefold() for title in titles}) == len(titles)

    by_node = {node.title: node for node in outline.nodes}
    polygon = by_title["Area of a polygon"]
    assert assigned[polygon.id] == (
        "intro",
        "polygon",
        "try-page-2",
        "aman",
        "ridhima",
        "exercise-9-1",
    )
    assert by_node["Area of a polygon"].token_mass == 115 + 191 + 40 + 100 + 133 + 200
    assert "Ridhima's method:" in polygon.source_headings
    assert assigned[by_title["Surface area of a cuboid"].id] == (
        "surface-intro",
        "cuboid-surface",
        "try-page-7",
    )
    assert by_node["Surface area of a cuboid"].token_mass >= 90
    assert assigned[by_title["Surface area of a cube"].id] == ("cube-surface",)
    assert assigned[by_title["Surface area of a cylinder"].id] == (
        "cylinder-surface",
        "try-page-10",
    )
    assert assigned[by_title["Volume of a cuboid"].id] == ("volume-intro", "cuboid-volume")
    assert by_node["Volume of a cuboid"].token_mass >= 70
    assert assigned[by_title["Volume of a cube"].id] == ("cube-volume",)
    assert assigned[by_title["Volume of a cylinder"].id] == ("cylinder-volume", "try-page-15")
    assert "try-page-2" not in assigned[by_title["Surface area of a cuboid"].id]
    assert "try-page-7" not in assigned[by_title["Surface area of a cube"].id]


def test_box_under_a_new_numbered_section_opens_that_section() -> None:
    """9.4.2 has no chunk of its own. Its body is stored under the box path."""
    rows = (
        ("9.4 Surface Area of Cube, Cuboid and Cylinder", "surface-intro", 90),
        ("9.4 Surface Area of Cube, Cuboid and Cylinder > 9.4.1 Cuboid", "cuboid", 100),
        ("9.4 Surface Area of Cube, Cuboid and Cylinder > 9.4.2 Cube > DO THIS", "cube-body", 80),
        ("9.4 Surface Area of Cube, Cuboid and Cylinder > 9.4.3 Cylinders", "cylinders", 40),
    )
    groups = _groups(rows)
    refs, titles = _refs_for(rows)
    assigned = texts_for_nodes(tuple(refs), groups)
    assert titles == [
        "Surface area of cube, cuboid and cylinder",
        "Surface area of a cuboid",
        "Surface area of a cube",
        "Surface area of a cylinder",
    ]
    cube = next(ref for ref in refs if ref.title == "Surface area of a cube")
    assert cube.parent_title == "Surface area of cube, cuboid and cylinder"
    assert assigned[cube.id] == ("cube-body",)
    cuboid = next(ref for ref in refs if ref.title == "Surface area of a cuboid")
    assert "cube-body" not in assigned[cuboid.id]
    before = [text for _label, texts in groups for text in texts]
    after = [text for texts in assigned.values() for text in texts]
    assert sorted(after) == sorted(before)
    assert len(after) == len(set(before))


def test_tiny_siblings_merge_and_keep_every_chunk() -> None:
    rows = tuple((f"Concept {index}", f"chunk-{index}", 30) for index in range(10))
    groups = _groups(rows)
    before = [text for _label, texts in groups for text in texts]
    refs, titles = _refs_for(rows)
    assert len(titles) == MAX_OUTLINE_NODES
    assigned = texts_for_nodes(tuple(refs), groups)
    after = [text for texts in assigned.values() for text in texts]
    assert sorted(after) == sorted(before)
    assert len(after) == len(set(before))


def test_two_concepts_are_not_split_to_fill_the_range() -> None:
    rows = (
        ("Area of a trapezium", "chunk-a", 200),
        ("Volume of a cylinder", "chunk-b", 200),
    )
    _refs, titles = _refs_for(rows)
    assert titles == ["Area of a trapezium", "Volume of a cylinder"]


def test_existing_subtopic_name_is_not_rewritten_when_titles_match() -> None:
    kept = Subtopic(
        id=uuid4(),
        topic_id=uuid4(),
        name="9.1    Introduction",
        slug="9-1-introduction",
        sequence=1,
    )
    outline = outline_from_headings(
        outline_clusters(source_sections([("9.1    Introduction", "chunk-intro", 90)]))
    )
    matched = match_existing_subtopics(outline, (kept,))
    assert matched == (kept.id,)
    assert kept.name == "9.1    Introduction"
    assert outline.nodes[0].title == "Introduction"
    assert outline.nodes[0].slug == "9-1-introduction"


def test_teacher_title_edit_keeps_chunk_keys() -> None:
    node_key = uuid4()
    raw_title = "9.1    Introduction"
    keys = ("9.1    Introduction", "TRY THESE")
    base = OutlineSnapshot(
        target_item_count=ItemCount(60),
        nodes=(
            OutlineNodeSnapshot(
                node_key=node_key,
                parent_node_key=None,
                slug="9-1-introduction",
                title=raw_title,
                token_mass=90,
                prerequisite_score=Decimal("0.5"),
                centrality=Decimal("1"),
                weight=Decimal("1"),
                matched_subtopic_id=None,
                force_create=False,
                proposed_outcomes=(ProposedOutcomeSnapshot(statement="See the introduction"),),
                sequence=1,
                source_headings=keys,
            ),
        ),
    )
    request = ChangeRequest(
        id=uuid4(),
        decision_id=uuid4(),
        revision_id=uuid4(),
        author=HumanActorStamp(
            user_id=uuid4(),
            display_name="Meera",
            occurred_at=datetime.now(UTC),
        ),
        request=DraftOutlineRequest(
            target=OutlineNodeTarget(kind="outline_node", node_key=node_key),
            field=OutlineField.TITLE,
            kind=ChangeKind.PEDAGOGY,
            comment="Call this Starting out",
        ),
    )
    rewritten = rewrite_outline_snapshot(base, (request,))
    node = rewritten.nodes[0]
    assert node.title == "9.1    Introduction (Call this Starting out)"
    assert node.title != normalize_outline_title(raw_title)
    assert node.source_headings == keys
    assert node.slug == "9-1-introduction"


def test_noise_heading_folds_into_the_previous_concept() -> None:
    assert nearest_concept_position(1, [0, 2]) == 0
    assert nearest_concept_position(0, [1]) == 1
    assert nearest_concept_position(0, []) is None
