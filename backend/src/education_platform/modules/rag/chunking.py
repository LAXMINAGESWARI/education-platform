"""Layout-aware chunking for Docling documents."""

from __future__ import annotations

import hashlib
import re
from typing import Any, override

from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingDocSerializer,
    ChunkingSerializerProvider,
)
from docling_core.transforms.serializer.base import BaseDocSerializer, SerializationResult
from docling_core.transforms.serializer.common import create_ser_result
from docling_core.transforms.serializer.markdown import (
    MarkdownParams,
    MarkdownTableSerializer,
    MarkdownTextSerializer,
)
from docling_core.types.doc.base import ImageRefMode
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.text import TextItem

from education_platform.modules.rag.contracts import TextChunk

SECTION_HEADING_MAX_LEN = 500
EMBEDDING_TOKENIZER_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
FORMULA_NOT_DECODED = "<!-- formula-not-decoded -->"
DIAGRAM_PLACEHOLDER = "[Diagram]"
_NUMBERED_HEADING = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s+\S")
# Activity boxes are all caps in this book. They must not become level-1
# parents of the next numbered section (9.4.3 under TRY THESE).
_BOX_HEADING = re.compile(
    r"^(?:try\s+these|do\s+this|think[\s,]+discuss(?:[\s,]+and[\s,]+write)?)$",
    re.IGNORECASE,
)
_TRAILING_BOX_PUNCT = re.compile(r"[\s:.;,!?]+$")
_BOX_WHITESPACE = re.compile(r"\s+")

__all__ = [
    "DIAGRAM_PLACEHOLDER",
    "FORMULA_NOT_DECODED",
    "FormulaAwareChunkingSerializerProvider",
    "FormulaOrigMarkdownTextSerializer",
    "SECTION_HEADING_MAX_LEN",
    "TextChunk",
    "assign_numbered_heading_levels",
    "chunk_docling_document",
    "content_hash",
    "estimate_token_count",
    "promote_formula_orig_text",
    "replace_formula_not_decoded",
]


def estimate_token_count(text: str) -> int:
    """Approximate tokens without a tokenizer dependency (words ≈ tokens)."""
    return len(text.split())


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _page_number_from_meta(meta: Any) -> int | None:
    """Return the first provenance page number from chunk meta, if any."""
    doc_items = getattr(meta, "doc_items", None) or []
    for item in doc_items:
        for prov in getattr(item, "prov", None) or []:
            page_no = getattr(prov, "page_no", None)
            if page_no is not None:
                return int(page_no)
    return None


def _section_heading_from_meta(meta: Any) -> str | None:
    """Join Docling headings with ' > ', clipped to the DB column limit."""
    headings = getattr(meta, "headings", None)
    if not headings:
        return None
    joined = " > ".join(str(heading) for heading in headings if heading)
    if not joined:
        return None
    return joined[:SECTION_HEADING_MAX_LEN]


def _is_blank_or_undecoded_formula(value: str) -> bool:
    stripped = value.strip()
    return not stripped or stripped == FORMULA_NOT_DECODED


def _is_formula_item(item: Any) -> bool:
    if type(item).__name__ == "FormulaItem":
        return True
    label = getattr(item, "label", None)
    if label is None:
        return False
    raw = getattr(label, "value", label)
    return str(raw).lower() == "formula"


def replace_formula_not_decoded(serialized: str, orig: str, *, is_inline_scope: bool) -> str:
    """Swap Docling's undecoded-formula comment for orig wrapped as math."""
    if FORMULA_NOT_DECODED not in serialized:
        return serialized
    cleaned = orig.strip()
    if _is_blank_or_undecoded_formula(cleaned):
        return serialized.replace(FORMULA_NOT_DECODED, "").strip()
    wrapped = f"${cleaned}$" if is_inline_scope else f"$${cleaned}$$"
    return serialized.replace(FORMULA_NOT_DECODED, wrapped)


class FormulaOrigMarkdownTextSerializer(MarkdownTextSerializer):
    """Serialize undecoded formulas as ``$orig$`` / ``$$orig$$``, never the HTML comment."""

    @override
    def serialize(
        self,
        *,
        item: TextItem,
        doc_serializer: BaseDocSerializer,
        doc: DoclingDocument,
        is_inline_scope: bool = False,
        visited: set[str] | None = None,
        **kwargs: Any,
    ) -> SerializationResult:
        result = super().serialize(
            item=item,
            doc_serializer=doc_serializer,
            doc=doc,
            is_inline_scope=is_inline_scope,
            visited=visited,
            **kwargs,
        )
        orig = str(getattr(item, "orig", "") or "")
        replacement = replace_formula_not_decoded(
            result.text, orig, is_inline_scope=is_inline_scope
        )
        if replacement == result.text:
            return result
        return create_ser_result(text=replacement, span_source=item)


class FormulaAwareChunkingSerializerProvider(ChunkingSerializerProvider):
    """HybridChunker provider that never indexes ``<!-- formula-not-decoded -->``."""

    @override
    def get_serializer(self, doc: DoclingDocument) -> BaseDocSerializer:
        return ChunkingDocSerializer(
            doc=doc,
            text_serializer=FormulaOrigMarkdownTextSerializer(),
            table_serializer=MarkdownTableSerializer(),
            params=MarkdownParams(
                image_mode=ImageRefMode.PLACEHOLDER,
                image_placeholder=DIAGRAM_PLACEHOLDER,
                escape_underscores=False,
                escape_html=False,
            ),
        )


def promote_formula_orig_text(document: Any) -> None:
    """Copy PDF-layer formula text into ``item.text`` when LaTeX is missing.

    Docling's markdown serializer emits ``<!-- formula-not-decoded -->`` when a
    FORMULA item has empty ``text``. Curriculum PDFs often still have a usable
    ``orig`` string (for example ``x + 2 = 5``); promoting it means HybridChunker
    wraps that as math instead of indexing the HTML comment.
    """
    iterate = getattr(document, "iterate_items", None)
    if iterate is None:
        return
    for item, _level in iterate():
        if not _is_formula_item(item):
            continue
        text = str(getattr(item, "text", "") or "")
        orig = str(getattr(item, "orig", "") or "").strip()
        if not _is_blank_or_undecoded_formula(text):
            continue
        if _is_blank_or_undecoded_formula(orig):
            continue
        item.text = orig


def _is_section_header(item: Any) -> bool:
    label = getattr(item, "label", None)
    return str(getattr(label, "value", label)).lower() == "section_header"


def _is_box_heading(text: str) -> bool:
    key = _BOX_WHITESPACE.sub(" ", text.strip())
    key = _TRAILING_BOX_PUNCT.sub("", key).strip()
    return _BOX_HEADING.match(key) is not None


def assign_numbered_heading_levels(document: Any) -> None:
    """Rebuild heading depth from section numbers.

    Docling gives every section header level 1, so "6.1 Some Properties" is
    shadowed by the next subheading and never reaches chunk metadata. When the
    document numbers its sections, "6.1" is level 1, "6.1.2" level 2, an
    all-caps heading (SUMMARY) level 1, and any other heading sits one level
    below the numbered section before it.
    """
    iterate = getattr(document, "iterate_items", None)
    if iterate is None:
        return
    headers = [item for item, _level in iterate() if _is_section_header(item)]
    if not any(_NUMBERED_HEADING.match(str(item.text)) for item in headers):
        return
    current = 0
    for item in headers:
        text = str(item.text).strip()
        if _NUMBERED_HEADING.match(text):
            current = text.split()[0].rstrip(".").count(".")
            item.level = max(1, current)
        elif _is_box_heading(text):
            # Stay under the open numbered section. Do not reset the stack,
            # or the next 9.4.x heading is parented by the box.
            item.level = current + 1 if current else 1
        elif text.isupper():
            current = 1
            item.level = 1
        else:
            item.level = current + 1 if current else 1


def chunk_docling_document(document: Any) -> list[TextChunk]:
    """Chunk a DoclingDocument with HybridChunker (MiniLM tokenizer).

    Stored text is ``chunker.contextualize(chunk)`` so embeddings include heading
    context. Table markdown is preserved (no normalize + word-split flatten).
    """
    promote_formula_orig_text(document)
    assign_numbered_heading_levels(document)
    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
    from transformers import AutoTokenizer

    # MiniLM max_seq_length is 256 (sentence_bert_config); do not rely on BERT's 512 default.
    tokenizer = HuggingFaceTokenizer(
        tokenizer=AutoTokenizer.from_pretrained(EMBEDDING_TOKENIZER_MODEL),
        max_tokens=256,
    )
    chunker = HybridChunker(
        tokenizer=tokenizer,
        merge_peers=True,
        repeat_table_header=True,
        serializer_provider=FormulaAwareChunkingSerializerProvider(),
    )

    chunks: list[TextChunk] = []
    seen_hashes: set[str] = set()
    ordinal = 1
    for chunk in chunker.chunk(dl_doc=document):
        text = chunker.contextualize(chunk).strip()
        if not text:
            continue
        digest = content_hash(text)
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        try:
            token_count = int(tokenizer.count_tokens(text))
        except Exception:
            token_count = estimate_token_count(text)
        chunks.append(
            TextChunk(
                ordinal=ordinal,
                text=text,
                content_hash=digest,
                token_count=token_count,
                page_number=_page_number_from_meta(chunk.meta),
                section_heading=_section_heading_from_meta(chunk.meta),
            )
        )
        ordinal += 1
    return chunks
