"""Pure outline math, DAG checks, and heuristic/LLM writers."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from uuid import UUID, uuid4

from education_platform.core.config import get_settings
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.generation.adk import (
    OutlineRunner,
    OutlineRunRequest,
    live_outline_runner,
)
from education_platform.modules.generation.types import (
    HeadingCluster,
    OutlineNodeEdit,
    ProposedOutline,
    ProposedOutlineNode,
    ReviewStatus,
)

OutlineWriter = Callable[[Sequence[HeadingCluster]], ProposedOutline]

_logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_APPENDIX_HEADING = re.compile(r"^(?:ans\.?\b|page\s+no\.?\b)", re.IGNORECASE)
_HINT_HEADING = re.compile(r"^hint\s*:", re.IGNORECASE)
_HINT_LABEL = re.compile(r"^hint\s*:\s*", re.IGNORECASE)
_ANY_METHOD = re.compile(r"^method\s+\d+\s*:?$", re.IGNORECASE)
_KEPT_METHOD = re.compile(r"^method\s+[1-4]\s*:?$", re.IGNORECASE)
_POSSESSIVE_METHOD = re.compile(r".+['\u2019]s method\s*:?$", re.IGNORECASE)
_SECTION_PREFIX = re.compile(r"^(?:\d+\.)+\d*\.?\s+")
_TRAILING_PUNCT = re.compile(r"[\s:.;,!?]+$")
_WHITESPACE = re.compile(r"\s+")
# Activity boxes, worked examples, exercises, summaries, and captions are not
# concepts. Their chunk groups stay addressable by the original heading and are
# claimed by the concept section they fold into.
_ACTIVITY_NOISE = re.compile(
    r"^(?:"
    r"try\s+this(?:\s+out)?"
    r"|try\s+these"
    r"|do\s+this"
    r"|do\s+you\s+know"
    r"|think[\s,]+discuss(?:[\s,]+and[\s,]+write)?"
    r"|(?:solved\s+)?examples?(?:\s+\d+(?:\.\d+)*)?"
    r"|exercises?(?:\s+\d+(?:\.\d+)*)?"
    r"|solution\b.*"
    r"|summary"
    r"|what\s+have\s+(?:we|you)\s+discussed"
    r"|figure\s+it\s+out"
    r"|(?:fig(?:ure)?|table)\.?\s+\d+\b.*"
    r")$",
    re.IGNORECASE,
)
_TITLE_STOPWORDS = frozenset(
    {"a", "an", "the", "of", "and", "or", "to", "in", "for", "with", "on", "at", "by"}
)
_THREE = Decimal("3")
_NEUTRAL_SCORE = Decimal("0.5")
_ZERO = Decimal("0")
# Short headings fold into the next sibling. Method 1–4 stay even when they
# are under this floor.
MIN_SECTION_TOKENS = 21
# While an outline is wider than this, fold the smallest short sibling into its
# neighbor. Sections at or above this mass stay, so a chapter of real concepts
# is not glued together to force the target.
BREADTH_MERGE_TOKENS = 80
MAX_OUTLINE_NODES = 8
MAX_TITLE_WORDS = 6
HEADING_PATH_SEPARATOR = " > "


@dataclass(frozen=True, slots=True)
class SourceSection:
    heading: str
    texts: tuple[str, ...]
    token_mass: int
    parent_heading: str | None = None
    # Original chunk-group labels this section owns. Item generation matches
    # these, not the cleaned display title. Empty means "the group label".
    source_headings: tuple[str, ...] = ()


def preview_quotas(weights: Sequence[Decimal], target: int) -> tuple[int, ...]:
    """Question counts a review can show before the outline is accepted."""
    if target <= 0 or not weights:
        return tuple(0 for _ in weights)
    if sum(weights, Decimal("0")) == 0:
        return tuple(0 for _ in weights)
    return largest_remainder(tuple(Decimal(weight) for weight in weights), target)


def largest_remainder(weights: Sequence[Decimal], n: int) -> tuple[int, ...]:
    """Integer quotas that sum to n. Hamilton / largest-remainder."""
    if n < 0:
        raise ValueError("n must be >= 0")
    if not weights:
        return ()
    values = [Decimal(weight) for weight in weights]
    total = sum(values, Decimal("0"))
    if total == 0:
        raise ValueError("all-zero weights")
    exact = [value / total * n for value in values]
    floors = [int(item.to_integral_value(rounding=ROUND_DOWN)) for item in exact]
    remainders = [item - floor for item, floor in zip(exact, floors, strict=True)]
    leftover = n - sum(floors)
    order = sorted(
        (index for index, value in enumerate(values) if value > 0),
        key=lambda index: (-remainders[index], index),
    )
    quotas = list(floors)
    for index in order[:leftover]:
        quotas[index] += 1
    return tuple(quotas)


def combined_weight(
    *,
    token_mass: int,
    prerequisite_score: Decimal,
    centrality: Decimal,
) -> Decimal:
    """Linear blend then left unnormalized. Equal coefficients on the three factors."""
    return (Decimal(token_mass) + prerequisite_score + centrality) / _THREE


def heading_key(heading: str) -> str:
    """Collapsed heading with trailing punctuation removed, for noise checks."""
    text = _WHITESPACE.sub(" ", heading.strip())
    return _TRAILING_PUNCT.sub("", text).strip()


def is_activity_noise_heading(heading: str) -> bool:
    """Activity boxes, examples, solutions, exercises, summaries, and captions.

    Answer keys are not included: those are dropped, not folded into a concept.
    """
    return _ACTIVITY_NOISE.match(heading_key(heading)) is not None


def is_outline_noise_heading(heading: str) -> bool:
    """Non-concept headings. Not a subtopic of their own."""
    key = heading_key(heading)
    if not key:
        return False
    if _APPENDIX_HEADING.match(key) or _HINT_HEADING.match(heading.strip()):
        return True
    return is_activity_noise_heading(key)


def normalize_outline_title(heading: str) -> str:
    """Student-facing concept name. Does not invent words that are not in the heading.

    Strips a leading section number, collapses whitespace, drops trailing
    punctuation, turns an ALL-CAPS heading into sentence case, and keeps at
    most six words. Mixed-case headings keep their capitals. Idempotent.
    """
    text = _WHITESPACE.sub(" ", heading.strip())
    stripped = _SECTION_PREFIX.sub("", text).strip()
    text = stripped or text
    text = _TRAILING_PUNCT.sub("", text).strip()
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return ""
    if _is_all_caps(text):
        text = _sentence_case(text)
    words = text.split(" ")
    if len(words) > MAX_TITLE_WORDS:
        words = words[:MAX_TITLE_WORDS]
        tail = words[-1].strip(".,;:").casefold()
        if len(words) > 1 and tail in _TITLE_STOPWORDS:
            words = words[:-1]
        text = " ".join(words).rstrip(".,;:")
    return text


def section_group_label(section: SourceSection) -> str:
    """Chunk-group key. The same string ``_heading_text_groups`` builds."""
    if section.parent_heading:
        return f"{section.parent_heading}{HEADING_PATH_SEPARATOR}{section.heading}"
    return section.heading


def _is_all_caps(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return len(letters) >= 2 and all(char.isupper() for char in letters)


def _sentence_case(text: str) -> str:
    lowered = text.lower()
    for index, char in enumerate(lowered):
        if char.isalpha():
            return lowered[:index] + char.upper() + lowered[index + 1 :]
    return lowered


def source_sections(rows: Iterable[tuple[str | None, str, int]]) -> list[SourceSection]:
    """One run per heading path, in document order.

    "6.1 Some Properties > Increments in Products" is the leaf Increments in
    Products under the parent 6.1. A path with one part is a top-level section.
    A leaf that ends with ":" and is not Method 1–4 or a possessive method
    ("Tadang's method:") is body text and joins the previous sibling under the
    same parent. A Hint: chunk is not a section: the label is removed and the
    text joins the next sibling under the same parent, or the previous sibling
    when the next heading has a different parent. Everything from the first
    answer-key heading ("Ans.", "Page No.") onward is dropped.
    """
    sections: list[SourceSection] = []
    pending_hints: list[tuple[str | None, str, int]] = []
    for heading, text, tokens in rows:
        parts = [part.strip() for part in (heading or "").split(HEADING_PATH_SEPARATOR)]
        parts = [part for part in parts if part]
        if not parts:
            continue
        if any(_APPENDIX_HEADING.match(part) for part in parts):
            break
        leaf = parts[-1]
        parent = parts[0] if len(parts) > 1 else None
        # Answer-key parents are already cut off above. Activity parents stay so
        # their chunks can be folded onto a concept instead of discarded.
        if (
            parent is not None
            and is_outline_noise_heading(parent)
            and not is_activity_noise_heading(parent)
        ):
            continue
        if _HINT_HEADING.match(leaf):
            pending_hints.append((parent, _strip_hint_label(text), tokens))
            continue
        if is_activity_noise_heading(leaf):
            sections.append(SourceSection(leaf, (text,), tokens, parent))
            continue
        if _is_colon_sentence(leaf):
            _absorb_colon_sentence(sections, leaf, text, tokens, parent)
            continue
        if is_outline_noise_heading(leaf):
            continue
        section = _section_with_pending_hints(
            SourceSection(leaf, (text,), tokens, parent),
            pending_hints,
            sections,
        )
        pending_hints.clear()
        previous = sections[-1] if sections else None
        if previous is not None and previous.heading == leaf and previous.parent_heading == parent:
            sections[-1] = _join_sections(previous, section)
        else:
            sections.append(section)
    _flush_hints(sections, pending_hints)
    return sections


def outline_clusters(
    sections: Sequence[SourceSection],
    *,
    min_tokens: int = MIN_SECTION_TOKENS,
) -> list[HeadingCluster]:
    """One cluster per concept heading, with numbered sections as parents.

    Activity headings fold into the concept they follow (or the next sibling
    under the same parent when they open that parent). Their original group
    labels stay on ``source_headings`` so chunk mapping still finds them. A
    repeated leaf under the same parent joins its first run. A heading under
    ``min_tokens`` merges into the next sibling under that parent, or into the
    previous sibling when it is the last one. Method 1–4 stay even when they
    are shorter than ``min_tokens``. While the outline is still wider than
    ``MAX_OUTLINE_NODES``, the smallest non-method sibling under
    ``BREADTH_MERGE_TOKENS`` folds into its larger neighbor. A numbered section
    that has children is a container: its token mass stays 0 and any text of
    its own is kept only as a sample.
    """
    keyed = [_ensure_source_key(section) for section in sections]
    folded = _fold_activity_sections(keyed)
    merged = _merge_until_breadth(
        _merge_tiny_siblings(_collapse_repeated_leaves(folded), min_tokens)
    )
    parents = {section.parent_heading for section in merged if section.parent_heading}
    exclusive = {
        section.heading: section
        for section in merged
        if section.parent_heading is None and section.heading in parents
    }
    clusters: list[HeadingCluster] = []
    emitted_parents: set[str] = set()
    for section in merged:
        parent = section.parent_heading
        if parent:
            if parent not in emitted_parents:
                clusters.append(_container_cluster(parent, exclusive.get(parent)))
                emitted_parents.add(parent)
            clusters.append(_leaf_cluster(section))
            continue
        if section.heading in parents:
            if section.heading not in emitted_parents:
                clusters.append(_container_cluster(section.heading, section))
                emitted_parents.add(section.heading)
            continue
        clusters.append(_leaf_cluster(section))
    return clusters


def _collapse_repeated_leaves(sections: Sequence[SourceSection]) -> list[SourceSection]:
    order: list[tuple[str | None, str]] = []
    by_key: dict[tuple[str | None, str], SourceSection] = {}
    for section in sections:
        key = (section.parent_heading, section.heading)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = section
            order.append(key)
        else:
            by_key[key] = _join_sections(existing, section)
    return [by_key[key] for key in order]


def _merge_tiny_siblings(sections: Sequence[SourceSection], min_tokens: int) -> list[SourceSection]:
    merged: list[SourceSection] = []
    pending: SourceSection | None = None
    for section in sections:
        if pending is not None and pending.parent_heading != section.parent_heading:
            _flush_tiny(merged, pending)
            pending = None
        if _folds_away(section, min_tokens, len(sections)):
            pending = section if pending is None else _join_sections(pending, section)
            continue
        if pending is not None:
            section = SourceSection(
                heading=section.heading,
                texts=pending.texts + section.texts,
                token_mass=pending.token_mass + section.token_mass,
                parent_heading=section.parent_heading,
            )
            pending = None
        merged.append(section)
    if pending is not None:
        _flush_tiny(merged, pending)
    return merged


def _folds_away(section: SourceSection, min_tokens: int, section_count: int) -> bool:
    if section_count <= 1 or section.token_mass >= min_tokens:
        return False
    return _KEPT_METHOD.match(section.heading.strip()) is None


def _is_colon_sentence(heading: str) -> bool:
    text = heading.strip()
    if not text.endswith(":"):
        return False
    if _ANY_METHOD.match(text) is not None:
        return False
    return _POSSESSIVE_METHOD.match(text) is None


def _strip_hint_label(text: str) -> str:
    return _HINT_LABEL.sub("", text.strip(), count=1).strip()


def _colon_sentence_texts(heading: str, text: str) -> tuple[str, ...]:
    sentence = heading.strip()
    body = text.strip()
    if body and sentence and sentence not in body:
        return (sentence, body)
    if body:
        return (body,)
    if sentence:
        return (sentence,)
    return ()


def _index_of_previous_sibling(sections: Sequence[SourceSection], parent: str | None) -> int | None:
    for index in range(len(sections) - 1, -1, -1):
        if sections[index].parent_heading == parent:
            return index
    return None


def _absorb_colon_sentence(
    sections: list[SourceSection],
    heading: str,
    text: str,
    tokens: int,
    parent: str | None,
) -> None:
    incoming = SourceSection(heading, _colon_sentence_texts(heading, text), tokens, parent)
    index = _index_of_previous_sibling(sections, parent)
    if index is None:
        sections.append(incoming)
        return
    sections[index] = _join_sections(sections[index], incoming)


def _section_with_pending_hints(
    section: SourceSection,
    pending: Sequence[tuple[str | None, str, int]],
    sections: list[SourceSection],
) -> SourceSection:
    matched_texts: list[str] = []
    matched_tokens = 0
    for hint_parent, hint_text, hint_tokens in pending:
        if hint_parent == section.parent_heading:
            if hint_text:
                matched_texts.append(hint_text)
            matched_tokens += hint_tokens
            continue
        _attach_hint(sections, hint_parent, hint_text, hint_tokens)
    if matched_tokens == 0 and not matched_texts:
        return section
    return SourceSection(
        heading=section.heading,
        texts=tuple(matched_texts) + section.texts,
        token_mass=section.token_mass + matched_tokens,
        parent_heading=section.parent_heading,
    )


def _flush_hints(
    sections: list[SourceSection], pending: Sequence[tuple[str | None, str, int]]
) -> None:
    for hint_parent, hint_text, hint_tokens in pending:
        _attach_hint(sections, hint_parent, hint_text, hint_tokens)


def _attach_hint(sections: list[SourceSection], parent: str | None, text: str, tokens: int) -> None:
    index = _index_of_previous_sibling(sections, parent)
    if index is None:
        return
    extra = (text,) if text else ()
    current = sections[index]
    sections[index] = SourceSection(
        heading=current.heading,
        texts=current.texts + extra,
        token_mass=current.token_mass + tokens,
        parent_heading=current.parent_heading,
    )


def _flush_tiny(merged: list[SourceSection], pending: SourceSection) -> None:
    if merged and merged[-1].parent_heading == pending.parent_heading:
        merged[-1] = _join_sections(merged[-1], pending)
    else:
        merged.append(pending)


def _container_cluster(heading: str, exclusive: SourceSection | None) -> HeadingCluster:
    samples = () if exclusive is None else tuple(text[:400] for text in exclusive.texts[:3])
    keys = _source_keys(exclusive) if exclusive is not None else (heading,)
    return HeadingCluster(
        heading=heading,
        token_mass=0,
        sample_texts=samples,
        parent_heading=None,
        source_headings=keys,
    )


def _leaf_cluster(section: SourceSection) -> HeadingCluster:
    return HeadingCluster(
        heading=section.heading,
        token_mass=section.token_mass,
        sample_texts=tuple(text[:400] for text in section.texts[:3]),
        parent_heading=section.parent_heading,
        source_headings=_source_keys(section),
    )


def _source_keys(section: SourceSection) -> tuple[str, ...]:
    if section.source_headings:
        return section.source_headings
    return (section_group_label(section),)


def _ensure_source_key(section: SourceSection) -> SourceSection:
    if section.source_headings:
        return section
    return SourceSection(
        heading=section.heading,
        texts=section.texts,
        token_mass=section.token_mass,
        parent_heading=section.parent_heading,
        source_headings=(section_group_label(section),),
    )


def _union_sections(
    owner: SourceSection, extra: SourceSection, *, extra_first: bool
) -> SourceSection:
    texts = extra.texts + owner.texts if extra_first else owner.texts + extra.texts
    keys = tuple(dict.fromkeys((*_source_keys(owner), *_source_keys(extra))))
    return SourceSection(
        heading=owner.heading,
        texts=texts,
        token_mass=owner.token_mass + extra.token_mass,
        parent_heading=owner.parent_heading,
        source_headings=keys,
    )


def _join_sections(first: SourceSection, second: SourceSection) -> SourceSection:
    """Keep the first heading. Union texts, tokens, and chunk-group keys."""
    return _union_sections(first, second, extra_first=False)


def _defer_activity(folded: Sequence[SourceSection], noise: SourceSection) -> bool:
    """Hold a box that is the first child of a numbered heading for the next concept."""
    if not folded or noise.parent_heading is None:
        return not folded
    previous = folded[-1]
    return previous.parent_heading is None and previous.heading == noise.parent_heading


def _absorb_pending(section: SourceSection, pending: list[SourceSection]) -> SourceSection:
    kept: list[SourceSection] = []
    for noise in pending:
        same_parent = noise.parent_heading == section.parent_heading
        leading = noise.parent_heading is None
        if same_parent or leading:
            section = _union_sections(section, noise, extra_first=True)
        else:
            kept.append(noise)
    pending[:] = kept
    return section


def _fold_activity_sections(sections: Sequence[SourceSection]) -> list[SourceSection]:
    """Fold activity headings into the concept that owns their chunks.

    A box after a concept joins that concept. A box that opens a numbered
    section waits for the next child under that parent, then joins it. Answer
    keys never reach this list. Chunk-group labels are unioned onto the owner
    so later retrieval still addresses the original heading.
    """
    folded: list[SourceSection] = []
    pending: list[SourceSection] = []
    for section in sections:
        if is_activity_noise_heading(section.heading) or (
            section.parent_heading is not None and is_activity_noise_heading(section.parent_heading)
        ):
            if _defer_activity(folded, section):
                pending.append(section)
            else:
                folded[-1] = _union_sections(folded[-1], section, extra_first=False)
            continue
        folded.append(_absorb_pending(section, pending))
    if pending and folded:
        for noise in pending:
            folded[-1] = _union_sections(folded[-1], noise, extra_first=False)
    elif pending:
        return list(pending)
    return folded


def _projected_node_count(sections: Sequence[SourceSection]) -> int:
    parent_names = {section.parent_heading for section in sections if section.parent_heading}
    top_level = {section.heading for section in sections if section.parent_heading is None}
    return len(sections) + len(parent_names - top_level)


def _protected_method(heading: str) -> bool:
    text = heading.strip()
    return _KEPT_METHOD.match(text) is not None or _POSSESSIVE_METHOD.match(text) is not None


def _merge_until_breadth(sections: Sequence[SourceSection]) -> list[SourceSection]:
    """Fold the smallest short sibling while the outline is wider than the target.

    Method 1–4 and possessive methods stay. A section at or above
    ``BREADTH_MERGE_TOKENS`` stays. Fewer than three real concepts are left as
    they are: this never invents a heading.
    """
    current = list(sections)
    while _projected_node_count(current) > MAX_OUTLINE_NODES:
        merged = _merge_smallest_short_leaf(current)
        if merged is None:
            break
        current = merged
    return current


def _merge_smallest_short_leaf(sections: Sequence[SourceSection]) -> list[SourceSection] | None:
    parent_names = {section.parent_heading for section in sections if section.parent_heading}
    candidates: list[tuple[int, int, int]] = []
    for index, section in enumerate(sections):
        if section.token_mass > BREADTH_MERGE_TOKENS or _protected_method(section.heading):
            continue
        if section.parent_heading is None and section.heading in parent_names:
            continue
        neighbor = _breadth_neighbor(sections, index, parent_names)
        if neighbor is None:
            continue
        candidates.append((section.token_mass, index, neighbor))
    if not candidates:
        return None
    _mass, index, neighbor = min(candidates)
    return _combine_leaf(sections, keep_index=neighbor, drop_index=index)


def _breadth_neighbor(
    sections: Sequence[SourceSection], index: int, parent_names: set[str]
) -> int | None:
    section = sections[index]
    options: list[int] = []
    for neighbor in (index - 1, index + 1):
        if neighbor < 0 or neighbor >= len(sections):
            continue
        other = sections[neighbor]
        if other.parent_heading != section.parent_heading:
            continue
        if other.parent_heading is None and other.heading in parent_names:
            continue
        options.append(neighbor)
    if not options:
        return None
    return min(options, key=lambda item: (-sections[item].token_mass, item))


def _combine_leaf(
    sections: Sequence[SourceSection], *, keep_index: int, drop_index: int
) -> list[SourceSection]:
    keep = sections[keep_index]
    drop = sections[drop_index]
    extra_first = drop_index < keep_index
    combined = _union_sections(keep, drop, extra_first=extra_first)
    result: list[SourceSection] = []
    for index, section in enumerate(sections):
        if index == drop_index:
            continue
        result.append(combined if index == keep_index else section)
    return result


def _cluster_label(cluster: HeadingCluster) -> str:
    if cluster.parent_heading:
        return f"{cluster.parent_heading}{HEADING_PATH_SEPARATOR}{cluster.heading}"
    return cluster.heading


def _cluster_keys(cluster: HeadingCluster) -> tuple[str, ...]:
    if cluster.source_headings:
        return cluster.source_headings
    return (_cluster_label(cluster),)


def _absorb_clusters(
    owner: HeadingCluster,
    extras: Sequence[HeadingCluster],
    *,
    extra_first: bool,
) -> HeadingCluster:
    keys = list(_cluster_keys(owner))
    samples = list(owner.sample_texts)
    mass = owner.token_mass
    for extra in extras:
        for key in _cluster_keys(extra):
            if key not in keys:
                keys.append(key)
        mass += extra.token_mass
        if extra_first:
            samples = [*extra.sample_texts, *samples]
        else:
            samples.extend(extra.sample_texts)
    return HeadingCluster(
        heading=owner.heading,
        token_mass=mass,
        sample_texts=tuple(samples),
        parent_heading=owner.parent_heading,
        source_headings=tuple(keys),
    )


def _defer_cluster(prepared: Sequence[HeadingCluster], cluster: HeadingCluster) -> bool:
    if not prepared or cluster.parent_heading is None:
        return not bool(prepared)
    previous = prepared[-1]
    return previous.parent_heading is None and previous.heading == cluster.parent_heading


def _prepare_clusters(clusters: Sequence[HeadingCluster]) -> list[HeadingCluster]:
    """Fold activity clusters into the concept that owns them. Drop answer keys.

    Chunk-group labels move onto the owner. Display names are applied later.
    """
    prepared: list[HeadingCluster] = []
    pending: list[HeadingCluster] = []
    for cluster in clusters:
        heading = cluster.heading.strip()
        if not heading:
            continue
        if _APPENDIX_HEADING.match(heading_key(heading)):
            continue
        if is_outline_noise_heading(heading):
            if prepared and not _defer_cluster(prepared, cluster):
                prepared[-1] = _absorb_clusters(prepared[-1], (cluster,), extra_first=False)
            else:
                pending.append(cluster)
            continue
        if pending:
            owned = [noise for noise in pending if _noise_belongs(noise, cluster)]
            pending = [noise for noise in pending if noise not in owned]
            if owned:
                cluster = _absorb_clusters(cluster, owned, extra_first=True)
        prepared.append(cluster)
    if pending and prepared:
        prepared[-1] = _absorb_clusters(prepared[-1], pending, extra_first=False)
    elif pending:
        # Nothing to fold into. Keep the boxes so their chunks still have a node.
        return list(pending)
    return prepared


def _noise_belongs(noise: HeadingCluster, section: HeadingCluster) -> bool:
    if noise.parent_heading is None:
        return section.parent_heading is None
    return noise.parent_heading == section.parent_heading or noise.parent_heading == section.heading


def ground_outline(
    proposed: ProposedOutline,
    clusters: Sequence[HeadingCluster],
) -> ProposedOutline:
    """Rebuild the outline from source clusters and keep matching model outcomes.

    Order, parents, and token masses come from the clusters. Activity headings
    fold into the concept that owns their chunks; answer keys are dropped.
    A numbered section is a sibling of the other numbered sections; its
    subheadings are its children. A model parent that points at a sibling is
    ignored. Container nodes carry no quota weight. Proposed outcomes attach
    only when the normalized title and the normalized parent both match. A null
    parent matches a node with no parent. A title match with a different parent
    does not attach. Display titles are cleaned; chunk keys stay on
    ``source_headings``.
    """
    usable = _prepare_clusters(clusters)
    if not usable:
        return proposed
    return _outline_from_clusters(usable, _outcome_index(proposed.nodes))


def outline_from_headings(clusters: Sequence[HeadingCluster]) -> ProposedOutline:
    """One node per concept cluster. Children point at their numbered section."""
    return _outline_from_clusters(_prepare_clusters(clusters), {})


def _folded_parent(parent: str | None) -> str | None:
    """Normalized parent key. Raw '6.1 Area' and cleaned 'Area' match."""
    if parent is None:
        return None
    folded = normalize_outline_title(parent).casefold()
    return folded or None


def _outcome_index(
    nodes: Sequence[ProposedOutlineNode],
) -> dict[tuple[str, str | None], tuple[str, ...]]:
    """Outcomes keyed by normalized title and parent. Later duplicates win."""
    indexed: dict[tuple[str, str | None], tuple[str, ...]] = {}
    for node in nodes:
        indexed[
            (normalize_outline_title(node.title).casefold(), _folded_parent(node.parent_title))
        ] = node.proposed_outcomes
    return indexed


def _outline_from_clusters(
    clusters: Sequence[HeadingCluster],
    outcomes: dict[tuple[str, str | None], tuple[str, ...]],
) -> ProposedOutline:
    parent_names = {
        cluster.parent_heading.casefold()
        for cluster in clusters
        if cluster.parent_heading is not None
    }
    count = len(clusters)
    used_slugs: set[str] = set()
    parent_keys: dict[str, str] = {}
    nodes: list[ProposedOutlineNode] = []
    for index, cluster in enumerate(clusters):
        raw_heading = cluster.heading.strip() or f"Section {index + 1}"
        # Slug stays on the original heading so an existing subtopic still matches.
        # Chunk keys live on source_headings and do not follow the display rename.
        keys = _cluster_keys(cluster)
        title = normalize_outline_title(raw_heading) or raw_heading
        key = f"h{index + 1}"
        slug = _unique_slug(_slugify(raw_heading), used_slugs)
        used_slugs.add(slug)
        is_container = cluster.parent_heading is None and raw_heading.casefold() in parent_names
        parent_key = (
            None
            if cluster.parent_heading is None
            else parent_keys.get(cluster.parent_heading.casefold())
        )
        if cluster.parent_heading is None:
            parent_keys[raw_heading.casefold()] = key
        centrality = (
            _ZERO
            if is_container
            else (Decimal("1") if count == 1 else Decimal(count - index) / Decimal(count))
        )
        nodes.append(
            ProposedOutlineNode(
                key=key,
                parent_key=parent_key,
                slug=slug,
                title=title,
                token_mass=0 if is_container else cluster.token_mass,
                prerequisite_score=_ZERO if is_container else Decimal("0.5"),
                centrality=centrality,
                proposed_outcomes=outcomes.get(
                    (title.casefold(), _folded_parent(cluster.parent_heading)),
                    (),
                ),
                source_headings=keys,
            )
        )
    return ProposedOutline(nodes=tuple(nodes))


def parse_proposed_outline(payload: dict[str, object]) -> ProposedOutline:
    """Boundary parse of LLM JSON. Invalid → ValueError, not a half-graph."""
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("outline JSON must include a non-empty nodes array")
    parsed: list[ProposedOutlineNode] = []
    keys: set[str] = set()
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise ValueError("each outline node must be an object")
        node = _parse_proposed_node(raw, fallback_key=f"n{index + 1}")
        if node.key in keys:
            raise ValueError(f"duplicate outline key {node.key}")
        keys.add(node.key)
        parsed.append(node)
    keyset = keys
    for node in parsed:
        if node.parent_key is not None and node.parent_key not in keyset:
            raise ValueError(f"unknown parent_key {node.parent_key}")
        if node.parent_key == node.key:
            raise ValueError("outline node cannot parent itself")
    _assert_proposed_acyclic(parsed)
    return ProposedOutline(nodes=tuple(parsed))


def match_existing_subtopics(
    proposed: ProposedOutline,
    existing: Sequence[Subtopic],
) -> tuple[UUID | None, ...]:
    """Per node: slug, else exact name, else normalized name. Never renames a row."""
    by_slug = {row.slug: row.id for row in existing}
    by_name = {row.name.casefold(): row.id for row in existing}
    by_normal: dict[str, UUID] = {}
    for row in existing:
        by_normal.setdefault(normalize_outline_title(row.name).casefold(), row.id)
    matches: list[UUID | None] = []
    for node in proposed.nodes:
        slug_hit = by_slug.get(node.slug)
        if slug_hit is not None:
            matches.append(slug_hit)
            continue
        exact = by_name.get(node.title.casefold())
        if exact is not None:
            matches.append(exact)
            continue
        matches.append(by_normal.get(normalize_outline_title(node.title).casefold()))
    return tuple(matches)


def assert_dag(nodes: Sequence[OutlineNodeEdit]) -> None:
    """Reject unknown parent_id, cycles, duplicate ids. Called from patch_outline."""
    ids = [node.id for node in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate outline node ids")
    idset = set(ids)
    parent_of = {node.id: node.parent_id for node in nodes}
    for node in nodes:
        if node.parent_id is not None and node.parent_id not in idset:
            raise ValueError("unknown parent_id")
        if node.parent_id == node.id:
            raise ValueError("outline node cannot parent itself")
    for start in ids:
        seen: set[UUID] = set()
        current: UUID | None = start
        while current is not None:
            if current in seen:
                raise ValueError("outline DAG has a cycle")
            seen.add(current)
            current = parent_of.get(current)


def write_outline(
    clusters: Sequence[HeadingCluster],
    *,
    writer: OutlineWriter | None,
    runner: OutlineRunner | None = None,
    job_id: UUID | None = None,
) -> ProposedOutline:
    """If writer is None and OpenRouter is configured, default ADK outline graph."""
    if writer is not None:
        return writer(clusters)
    if runner is None and get_settings().openrouter_configured:
        runner = live_outline_runner()
    if runner is not None:
        return _outline_from_runner(clusters, runner, job_id=job_id)
    return outline_from_headings(clusters)


def _outline_from_runner(
    clusters: Sequence[HeadingCluster],
    runner: OutlineRunner,
    *,
    job_id: UUID | None,
) -> ProposedOutline:
    settings = get_settings()
    result = runner.generate(
        OutlineRunRequest(
            job_id=job_id or uuid4(),
            clusters=tuple(clusters),
            model=settings.openrouter_model,
            max_review_rounds=settings.adk_max_review_rounds,
        )
    )
    # Grounding enforces source order and coverage; teachers review the draft next,
    # so neither an unapproved draft nor a malformed one fails the run.
    try:
        proposed = parse_proposed_outline(result.nodes_payload)
    except ValueError as exc:
        _logger.warning(
            "Outline draft is unparseable (%s, review %s); using source headings. Draft: %.1500s",
            exc,
            result.review_status.value,
            result.nodes_payload,
        )
        return outline_from_headings(clusters)
    if result.review_status is not ReviewStatus.APPROVED:
        _logger.warning(
            "Outline draft was not approved; keeping grounded draft: %s", result.reviewer_notes
        )
    return ground_outline(proposed, clusters)


def _parse_proposed_node(raw: dict[str, object], *, fallback_key: str) -> ProposedOutlineNode:
    title = _required_str(raw, "title")
    key = _optional_key(raw, fallback_key)
    parent_raw = raw.get("parent_key")
    if parent_raw is None or parent_raw == "":
        parent_key = None
    elif isinstance(parent_raw, str):
        parent_key = parent_raw
    else:
        raise ValueError("parent_key must be a string or null")
    outcomes_raw = raw.get("proposed_outcomes") or []
    if not isinstance(outcomes_raw, list):
        raise ValueError("proposed_outcomes must be a list")
    outcomes: list[str] = []
    for item in outcomes_raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("proposed outcome statements must be non-empty strings")
        outcomes.append(item.strip())
    if len(outcomes) > 3:
        raise ValueError("at most 3 proposed outcomes per node")
    return ProposedOutlineNode(
        key=key,
        parent_key=parent_key,
        slug=_slugify(_optional_slug(raw) or title),
        title=title,
        token_mass=_required_int(raw, "token_mass") if "token_mass" in raw else 0,
        prerequisite_score=_unit_score(raw, "prerequisite_score"),
        centrality=_unit_score(raw, "centrality"),
        proposed_outcomes=tuple(outcomes),
        source_headings=_source_headings(raw),
        parent_title=_optional_parent_title(raw),
    )


def _optional_key(raw: dict[str, object], fallback: str) -> str:
    if "key" not in raw or raw.get("key") in (None, ""):
        return fallback
    return _required_str(raw, "key")


def _optional_parent_title(raw: dict[str, object]) -> str | None:
    if "parent_title" not in raw:
        return None
    value = raw.get("parent_title")
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("parent_title must be a string or null")
    return value.strip() or None


def _source_headings(raw: dict[str, object]) -> tuple[str, ...]:
    value = raw.get("headings")
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("headings must be a list of strings")
    headings: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("headings must be non-empty strings")
        headings.append(item.strip())
    return tuple(headings)


def _assert_proposed_acyclic(nodes: Sequence[ProposedOutlineNode]) -> None:
    parent_of = {node.key: node.parent_key for node in nodes}
    for start in parent_of:
        seen: set[str] = set()
        current: str | None = start
        while current is not None:
            if current in seen:
                raise ValueError("outline DAG has a cycle")
            seen.add(current)
            current = parent_of.get(current)


def _required_str(raw: dict[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_slug(raw: dict[str, object]) -> str:
    value = raw.get("slug")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _required_int(raw: dict[str, object], field: str) -> int:
    value = raw.get(field)
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    else:
        raise ValueError(f"{field} must be an integer")
    if number < 0:
        raise ValueError(f"{field} must be >= 0")
    return number


def _unit_score(raw: dict[str, object], field: str) -> Decimal:
    """Scores only weight quotas, so a missing or malformed one is neutral, not fatal."""
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        return _NEUTRAL_SCORE
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return _NEUTRAL_SCORE
    if not number.is_finite():
        return _NEUTRAL_SCORE
    return min(Decimal("1"), max(Decimal("0"), number))


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return (slug[:100] or "section")[:100]


def _unique_slug(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    index = 2
    while True:
        suffix = f"-{index}"
        candidate = f"{base[: 100 - len(suffix)]}{suffix}"
        if candidate not in used:
            return candidate
        index += 1
