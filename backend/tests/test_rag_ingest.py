"""Admin RAG ingest API and worker unit tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from docling_core.transforms.chunker.hierarchical_chunker import ChunkingDocSerializer
from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer
from docling_core.transforms.serializer.markdown import MarkdownTableSerializer
from docling_core.types.doc.document import DoclingDocument, PictureItem, TableCell, TableData
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.core.config import Settings, get_settings
from education_platform.core.llm import chat_completion_vision_sync
from education_platform.modules.academics.models import Subtopic
from education_platform.modules.generation.models import ContentGenerationRun
from education_platform.modules.materials.models import (
    SourceChunk,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
)
from education_platform.modules.rag.chunking import (
    DIAGRAM_PLACEHOLDER,
    FORMULA_NOT_DECODED,
    SECTION_HEADING_MAX_LEN,
    FormulaAwareChunkingSerializerProvider,
    FormulaOrigMarkdownTextSerializer,
    TextChunk,
    _page_number_from_meta,
    _section_heading_from_meta,
    assign_numbered_heading_levels,
    chunk_docling_document,
    content_hash,
    promote_formula_orig_text,
    replace_formula_not_decoded,
)
from education_platform.modules.rag.models import (
    IngestJob,
    IngestJobStatus,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeDocumentVersionStatus,
)
from education_platform.modules.rag.vector_store import (
    VectorRow,
    count_for_version,
    delete_by_version,
    search_similar,
    upsert_rows,
)
from education_platform.workers.ingest import (
    apply_formula_pipeline_options,
    convert_pdf_with_docling,
    docling_accelerator_device,
    enrich_formulas_with_openrouter,
    parse_pdf_with_docling,
    process_ingest_job_sync,
    public_ingest_error,
)
from education_platform.workers.runner import claim_next_job, poll_once

TINY_PDF = (
    b"%PDF-1.1\n"
    b"1 0 obj<<>>endobj\n"
    b"2 0 obj<< /Length 44 >>stream\n"
    b"BT /F1 12 Tf 100 700 Td (Hello RAG) Tj ET\n"
    b"endstream\nendobj\n"
    b"3 0 obj<< /Type /Page /Parent 4 0 R /Contents 2 0 R >>endobj\n"
    b"4 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"5 0 obj<< /Type /Catalog /Pages 4 0 R >>endobj\n"
    b"xref\n0 6\n0000000000 65535 f \n"
    b"trailer<< /Size 6 /Root 5 0 R >>\nstartxref\n0\n%%EOF\n"
)

SAMPLE_SECTION = "Properties of squares"
SAMPLE_CHUNK_TEXT = f"{SAMPLE_SECTION}\nA square has four equal sides and four right angles."


def _sample_chunks(*, text: str = SAMPLE_CHUNK_TEXT) -> list[TextChunk]:
    digest = content_hash(text)
    return [
        TextChunk(
            ordinal=1,
            text=text,
            content_hash=digest,
            token_count=len(text.split()),
            page_number=1,
            section_heading=SAMPLE_SECTION,
        )
    ]


class _StubProv:
    def __init__(self, page_no: int) -> None:
        self.page_no = page_no


class _StubDocItem:
    def __init__(self, *page_nos: int) -> None:
        self.prov = [_StubProv(n) for n in page_nos]


class _StubMeta:
    def __init__(
        self,
        *,
        headings: list[str] | None = None,
        doc_items: list[_StubDocItem] | None = None,
    ) -> None:
        self.headings = headings
        self.doc_items = doc_items or []


class _StubChunk:
    def __init__(self, text: str, meta: _StubMeta) -> None:
        self.text = text
        self.meta = meta


class _StubChunker:
    def __init__(self, chunks: list[_StubChunk]) -> None:
        self._chunks = chunks

    def chunk(self, dl_doc: object, **_kwargs: object) -> list[_StubChunk]:
        _ = dl_doc
        return list(self._chunks)

    def contextualize(self, chunk: _StubChunk) -> str:
        headings = chunk.meta.headings or []
        if headings:
            return "\n".join([*headings, chunk.text])
        return chunk.text


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
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture()
def seeded_subtopic_id(seeded_db: Session) -> UUID:
    subtopic = seeded_db.scalar(
        select(Subtopic).where(Subtopic.slug == "rectangles_squares_properties")
    )
    assert subtopic is not None
    return subtopic.id


def test_student_forbidden_on_curriculum_upload(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
    seeded_subtopic_id: UUID,
) -> None:
    response = client.post(
        f"/api/v1/admin/subtopics/{seeded_subtopic_id}/materials",
        headers=enrolled_student_headers,
        data={"title": "Lesson PDF"},
        files={"file": ("lesson.pdf", TINY_PDF, "application/pdf")},
    )
    assert response.status_code == 403


def test_student_forbidden_on_knowledge_upload(
    client: TestClient,
    enrolled_student_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/v1/admin/knowledge-documents",
        headers=enrolled_student_headers,
        data={"title": "Handbook", "doc_type": "handbook"},
        files={"file": ("handbook.pdf", TINY_PDF, "application/pdf")},
    )
    assert response.status_code == 403


def test_admin_curriculum_upload_enqueues(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_subtopic_id: UUID,
    seeded_db: Session,
) -> None:
    response = client.post(
        f"/api/v1/admin/subtopics/{seeded_subtopic_id}/materials",
        headers=admin_headers,
        data={"title": "Geometry PDF"},
        files={"file": ("lesson.pdf", TINY_PDF, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["lifecycle_status"] == "processing"
    assert body["version_number"] >= 1
    version_id = UUID(body["version_id"])

    seeded_db.expire_all()
    version = seeded_db.get(SourceMaterialVersion, version_id)
    assert version is not None
    assert version.lifecycle_status == SourceMaterialVersionStatus.PROCESSING
    assert version.submitted_by_user_id is None
    assert version.blob_object_key
    blob_path = get_settings().upload_dir / version.blob_object_key
    assert blob_path.is_file()

    job = seeded_db.get(IngestJob, UUID(body["ingest_job_id"]))
    assert job is not None
    assert job.source_material_version_id == version_id
    assert job.knowledge_document_version_id is None
    assert job.status == IngestJobStatus.QUEUED

    status = client.get(
        f"/api/v1/admin/material-versions/{version_id}",
        headers=admin_headers,
    )
    assert status.status_code == 200
    assert status.json()["lifecycle_status"] == "processing"
    assert status.json()["chunk_count"] == 0


def test_admin_knowledge_upload_and_list(
    client: TestClient,
    admin_headers: dict[str, str],
    seeded_db: Session,
) -> None:
    response = client.post(
        "/api/v1/admin/knowledge-documents",
        headers=admin_headers,
        data={
            "title": "Attendance Policy",
            "doc_type": "policy",
            "required_roles": '["administrator","teacher"]',
        },
        files={"file": ("policy.pdf", TINY_PDF, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    document_id = UUID(body["document_id"])
    version_id = UUID(body["version_id"])

    listed = client.get("/api/v1/admin/knowledge-documents", headers=admin_headers)
    assert listed.status_code == 200
    assert any(item["id"] == str(document_id) for item in listed.json())

    detail = client.get(
        f"/api/v1/admin/knowledge-documents/{document_id}",
        headers=admin_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["versions"][0]["id"] == str(version_id)

    status = client.get(
        f"/api/v1/admin/knowledge-document-versions/{version_id}",
        headers=admin_headers,
    )
    assert status.status_code == 200
    assert status.json()["lifecycle_status"] == "processing"

    seeded_db.expire_all()
    document = seeded_db.get(KnowledgeDocument, document_id)
    assert document is not None
    assert document.doc_type == "policy"
    job = seeded_db.get(IngestJob, UUID(body["ingest_job_id"]))
    assert job is not None
    assert job.status == IngestJobStatus.QUEUED


def test_page_number_from_meta_uses_first_provenance() -> None:
    meta = _StubMeta(doc_items=[_StubDocItem(2, 3), _StubDocItem(1)])
    assert _page_number_from_meta(meta) == 2
    assert _page_number_from_meta(_StubMeta()) is None


def test_section_heading_from_meta_joins_and_clips() -> None:
    meta = _StubMeta(headings=["Chapter 1", "Properties of squares"])
    assert _section_heading_from_meta(meta) == "Chapter 1 > Properties of squares"

    long_heading = "A" * (SECTION_HEADING_MAX_LEN + 40)
    clipped = _section_heading_from_meta(_StubMeta(headings=[long_heading]))
    assert clipped is not None
    assert len(clipped) == SECTION_HEADING_MAX_LEN
    assert _section_heading_from_meta(_StubMeta(headings=None)) is None


def test_chunk_docling_document_preserves_tables_and_dedupes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table_md = "| col | value |\n| --- | --- |\n| a | 1 |"
    stub_chunks = [
        _StubChunk(table_md, _StubMeta(headings=["Tables"], doc_items=[_StubDocItem(1)])),
        _StubChunk(table_md, _StubMeta(headings=["Tables"], doc_items=[_StubDocItem(1)])),
        _StubChunk("Unique prose", _StubMeta(headings=["Prose"], doc_items=[_StubDocItem(2)])),
    ]

    class _FakeHybridChunker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self._inner = _StubChunker(stub_chunks)

        def chunk(self, dl_doc: object, **kwargs: object) -> list[_StubChunk]:
            return self._inner.chunk(dl_doc, **kwargs)

        def contextualize(self, chunk: _StubChunk) -> str:
            return self._inner.contextualize(chunk)

    class _FakeTokenizer:
        def count_tokens(self, text: str) -> int:
            return len(text.split())

        def get_max_tokens(self) -> int:
            return 256

    monkeypatch.setattr(
        "docling_core.transforms.chunker.hybrid_chunker.HybridChunker",
        _FakeHybridChunker,
    )
    monkeypatch.setattr(
        "docling_core.transforms.chunker.tokenizer.huggingface.HuggingFaceTokenizer",
        lambda **_kwargs: _FakeTokenizer(),
    )
    monkeypatch.setattr(
        "transformers.AutoTokenizer.from_pretrained",
        lambda *_args, **_kwargs: object(),
    )

    chunks = chunk_docling_document(document=object())
    assert len(chunks) == 2
    assert "| col | value |" in chunks[0].text
    assert "\n" in chunks[0].text
    assert chunks[0].page_number == 1
    assert chunks[0].section_heading == "Tables"
    assert chunks[1].ordinal == 2
    assert chunks[1].page_number == 2
    assert len({c.content_hash for c in chunks}) == 2


def test_formula_aware_provider_uses_markdown_table_serializer() -> None:
    doc = DoclingDocument(name="provider")
    serializer = FormulaAwareChunkingSerializerProvider().get_serializer(doc)
    assert isinstance(serializer.table_serializer, MarkdownTableSerializer)
    assert isinstance(serializer.text_serializer, FormulaOrigMarkdownTextSerializer)
    assert serializer.params.image_placeholder == DIAGRAM_PLACEHOLDER


def _policy_table_data() -> TableData:
    return TableData(
        num_rows=2,
        num_cols=2,
        table_cells=[
            TableCell(
                text="Offence",
                start_row_offset_idx=0,
                end_row_offset_idx=1,
                start_col_offset_idx=0,
                end_col_offset_idx=1,
                column_header=True,
            ),
            TableCell(
                text="Action",
                start_row_offset_idx=0,
                end_row_offset_idx=1,
                start_col_offset_idx=1,
                end_col_offset_idx=2,
                column_header=True,
            ),
            TableCell(
                text="Late",
                start_row_offset_idx=1,
                end_row_offset_idx=2,
                start_col_offset_idx=0,
                end_col_offset_idx=1,
            ),
            TableCell(
                text="Warning",
                start_row_offset_idx=1,
                end_row_offset_idx=2,
                start_col_offset_idx=1,
                end_col_offset_idx=2,
            ),
        ],
    )


def test_chunk_docling_document_serializes_table_item_as_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_chunker_tokenizer(monkeypatch)
    doc = DoclingDocument(name="policy-table")
    doc.add_table(data=_policy_table_data())
    chunks = chunk_docling_document(doc)
    joined = "\n".join(chunk.text for chunk in chunks)
    assert chunks
    assert "|" in joined
    assert "Offence" in joined
    assert "Action" in joined
    assert "Late" in joined
    assert "Warning" in joined
    assert "Offence, Action = " not in joined


class FormulaItem:
    def __init__(self, text: str, orig: str, *, label: str = "formula") -> None:
        self.text = text
        self.orig = orig
        self.label = SimpleNamespace(value=label)


class _FormulaDoc:
    def __init__(self, items: list[object]) -> None:
        self.items = items

    def iterate_items(self) -> list[tuple[object, int]]:
        return [(item, 0) for item in self.items]


def test_promote_formula_orig_text_fills_empty_and_placeholder_latex() -> None:
    empty = FormulaItem(text="", orig="x + 2 = 5")
    placeholder = FormulaItem(text=FORMULA_NOT_DECODED, orig="2x - 3 = 7")
    decoded = FormulaItem(text="x^2=4", orig="ignored")
    prose = SimpleNamespace(text="", orig="not a formula", label="paragraph")
    promote_formula_orig_text(_FormulaDoc([empty, placeholder, decoded, prose]))
    assert empty.text == "x + 2 = 5"
    assert placeholder.text == "2x - 3 = 7"
    assert decoded.text == "x^2=4"
    assert prose.text == ""


def test_promote_formula_orig_text_skips_missing_orig_and_iterate() -> None:
    item = FormulaItem(text="", orig=FORMULA_NOT_DECODED)
    promote_formula_orig_text(_FormulaDoc([item]))
    assert item.text == ""
    promote_formula_orig_text(object())


def test_replace_formula_not_decoded_wraps_orig_as_math() -> None:
    assert (
        replace_formula_not_decoded(FORMULA_NOT_DECODED, "x + 2 = 5", is_inline_scope=False)
        == "$$x + 2 = 5$$"
    )
    assert (
        replace_formula_not_decoded(FORMULA_NOT_DECODED, "x + 2 = 5", is_inline_scope=True)
        == "$x + 2 = 5$"
    )
    assert replace_formula_not_decoded("keep", "x + 2 = 5", is_inline_scope=False) == "keep"
    assert replace_formula_not_decoded(FORMULA_NOT_DECODED, "", is_inline_scope=False) == ""
    assert (
        replace_formula_not_decoded(FORMULA_NOT_DECODED, FORMULA_NOT_DECODED, is_inline_scope=False)
        == ""
    )


def test_formula_serializer_emits_orig_math_not_html_comment() -> None:
    doc = DoclingDocument(name="formula-orig")
    item = doc.add_formula(text="", orig="x + 2 = 5")
    default = ChunkingDocSerializer(doc=doc).serialize(item=item)
    assert default.text == FORMULA_NOT_DECODED

    result = ChunkingDocSerializer(
        doc=doc, text_serializer=FormulaOrigMarkdownTextSerializer()
    ).serialize(item=item)
    assert FORMULA_NOT_DECODED not in result.text
    assert "x + 2 = 5" in result.text
    assert "$$" in result.text

    decoded = doc.add_formula(text=r"\frac{x}{2}", orig="ignored")
    kept = ChunkingDocSerializer(
        doc=doc, text_serializer=FormulaOrigMarkdownTextSerializer()
    ).serialize(item=decoded)
    assert r"\frac{x}{2}" in kept.text
    assert FORMULA_NOT_DECODED not in kept.text


def _patch_chunker_tokenizer(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeTokenizer(BaseTokenizer):
        def count_tokens(self, text: str) -> int:
            return len(text.split())

        def get_max_tokens(self) -> int:
            return 256

        def get_tokenizer(self) -> object:
            return object()

    monkeypatch.setattr(
        "docling_core.transforms.chunker.tokenizer.huggingface.HuggingFaceTokenizer",
        lambda **_kwargs: _FakeTokenizer(),
    )
    monkeypatch.setattr(
        "transformers.AutoTokenizer.from_pretrained",
        lambda *_args, **_kwargs: object(),
    )


def test_chunk_empty_formula_text_uses_orig_never_html_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "education_platform.modules.rag.chunking.promote_formula_orig_text",
        lambda _document: None,
    )
    _patch_chunker_tokenizer(monkeypatch)

    doc = DoclingDocument(name="formula-chunk")
    doc.add_formula(text="", orig="0.25(4 f - 3) = 0.05(10 f - 9)")
    chunks = chunk_docling_document(doc)
    joined = "\n".join(chunk.text for chunk in chunks)
    assert chunks
    assert FORMULA_NOT_DECODED not in joined
    assert "0.25(4 f - 3) = 0.05(10 f - 9)" in joined


def test_chunk_keeps_formula_and_diagram_from_distributive_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hegp106-style page: a figure plus the identity must both survive chunking."""
    _patch_chunker_tokenizer(monkeypatch)
    doc = DoclingDocument(name="hegp106-diagram")
    doc.add_title(text="Increments in Products")
    doc.add_text(
        label="paragraph",
        text="This property can be visualised nicely using a diagram:",
    )
    doc.add_picture()
    doc.add_formula(text="", orig="a(b + c) = ab + ac")

    chunks = chunk_docling_document(doc)
    joined = "\n".join(chunk.text for chunk in chunks)
    assert chunks
    assert "This property can be visualised nicely using a diagram:" in joined
    assert DIAGRAM_PLACEHOLDER in joined
    assert "a(b + c) = ab + ac" in joined
    assert FORMULA_NOT_DECODED not in joined


def test_chunk_rebuilds_numbered_section_paths_from_flat_docling_headings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hegp106: Docling puts every heading at level 1, so 6.1 was shadowed and lost."""
    _patch_chunker_tokenizer(monkeypatch)
    doc = DoclingDocument(name="hegp106-levels")
    doc.add_heading(text="6.1 Some Properties of Multiplication", level=1)
    doc.add_heading(text="Increments in Products", level=1)
    doc.add_text(label="paragraph", text="Consider 23 × 27.")
    doc.add_heading(text="6.2 Special Cases of the Distributive Property", level=1)
    doc.add_heading(text="Pattern 1", level=1)
    doc.add_text(label="paragraph", text="Square numbers ending in 5.")
    doc.add_heading(text="SUMMARY", level=1)
    doc.add_text(label="paragraph", text="We extended the distributive property.")

    headings = [chunk.section_heading for chunk in chunk_docling_document(doc)]
    assert headings == [
        "6.1 Some Properties of Multiplication > Increments in Products",
        "6.2 Special Cases of the Distributive Property > Pattern 1",
        "SUMMARY",
    ]


def test_assign_numbered_heading_levels_leaves_unnumbered_documents() -> None:
    doc = DoclingDocument(name="flat")
    first = doc.add_heading(text="Squares", level=1)
    second = doc.add_heading(text="Triangles", level=1)
    assign_numbered_heading_levels(doc)
    assert (first.level, second.level) == (1, 1)
    assign_numbered_heading_levels(object())

    nested = DoclingDocument(name="nested")
    top = nested.add_heading(text="2.1 Angles", level=1)
    deep = nested.add_heading(text="2.1.3 Exterior angles", level=1)
    child = nested.add_heading(text="Try this", level=1)
    assign_numbered_heading_levels(nested)
    assert (top.level, deep.level, child.level) == (1, 2, 3)


class _VisionFormulaItem:
    def __init__(self, text: str, orig: str) -> None:
        self.text = text
        self.orig = orig
        self.label = SimpleNamespace(value="formula")

    def get_image(self, document: object, prov_index: int = 0) -> Image.Image:
        _ = document, prov_index
        return Image.new("RGB", (8, 8), "white")


def test_enrich_formulas_with_openrouter_writes_latex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _VisionFormulaItem(text="", orig="")
    called: list[object] = []

    def _vision(messages: list[dict[str, object]], **_kwargs: object) -> str:
        called.append(messages)
        return r"$$\frac{x}{2}$$"

    monkeypatch.setattr(
        "education_platform.workers.ingest.chat_completion_vision_sync",
        _vision,
    )
    enrich_formulas_with_openrouter(
        _FormulaDoc([item]),
        settings=Settings(openrouter_api_key="test-key"),
    )
    assert item.text == r"\frac{x}{2}"
    assert called


def test_enrich_formulas_skips_vision_when_text_layer_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _VisionFormulaItem(text="", orig="x + 2 = 5")

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("text-layer formulas must not call OpenRouter")

    monkeypatch.setattr(
        "education_platform.workers.ingest.chat_completion_vision_sync",
        _boom,
    )
    enrich_formulas_with_openrouter(
        _FormulaDoc([item]),
        settings=Settings(openrouter_api_key="test-key"),
    )
    assert item.text == ""


def test_enrich_formulas_repairs_damaged_text_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean = _VisionFormulaItem(text="", orig="x + 2 = 5")
    split_letter = _VisionFormulaItem(text="", orig="a 2")
    split_number = _VisionFormulaItem(text="", orig="65 2")
    split_group = _VisionFormulaItem(text="", orig="(a+b) 2")
    callout = _VisionFormulaItem(
        text="",
        orig="Increase a ( b + 1) = ab × a",
    )
    called: list[list[dict[str, object]]] = []

    def _vision(messages: list[dict[str, object]], **_kwargs: object) -> str:
        called.append(messages)
        return json.dumps([r"a^{2}", r"65^{2}", r"(a+b)^{2}", r"a(b+1)=ab\times a"])

    monkeypatch.setattr(
        "education_platform.workers.ingest.chat_completion_vision_sync",
        _vision,
    )
    enrich_formulas_with_openrouter(
        _FormulaDoc([clean, split_letter, split_number, split_group, callout]),
        settings=Settings(openrouter_api_key="test-key"),
    )
    assert clean.text == ""
    assert split_letter.text == r"a^{2}"
    assert split_number.text == r"65^{2}"
    assert split_group.text == r"(a+b)^{2}"
    assert callout.text == r"a(b+1)=ab\times a"
    assert len(called) == 1
    content = called[0][0]["content"]
    assert isinstance(content, list)
    images = [part for part in content if part.get("type") == "image_url"]
    assert len(images) == 4


def _picture_crop(self: object, document: object, prov_index: int = 0) -> Image.Image:
    _ = self, document, prov_index
    return Image.new("RGB", (8, 8), "white")


def _vision_image_count(messages: list[dict[str, object]]) -> int:
    content = messages[0]["content"]
    assert isinstance(content, list)
    return len([part for part in content if part.get("type") == "image_url"])


def test_enrich_method_picture_keeps_diagram_when_vision_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = DoclingDocument(name="method-picture")
    doc.add_heading(text="Increments in Products", level=1)
    area = doc.add_picture()
    doc.add_heading(text="Method 1", level=1)
    method = doc.add_picture()
    doc.add_heading(text="6.3 Mind the Mistake, Mend the Mistake", level=1)
    mistake = doc.add_picture()
    called: list[list[dict[str, object]]] = []

    def _vision(messages: list[dict[str, object]], **_kwargs: object) -> str:
        called.append(messages)
        return json.dumps([""] * _vision_image_count(messages))

    monkeypatch.setattr(PictureItem, "get_image", _picture_crop)
    monkeypatch.setattr(
        "education_platform.workers.ingest.chat_completion_vision_sync",
        _vision,
    )
    enrich_formulas_with_openrouter(
        doc,
        settings=Settings(openrouter_api_key="test-key"),
    )
    assert len(called) == 1
    content = called[0][0]["content"]
    assert isinstance(content, list)
    prompt = content[0]
    assert isinstance(prompt, dict)
    assert "geometric figure" in str(prompt.get("text"))
    assert _vision_image_count(called[0]) == 2
    assert area in doc.pictures
    assert method in doc.pictures
    assert mistake in doc.pictures
    _patch_chunker_tokenizer(monkeypatch)
    chunks = chunk_docling_document(doc)
    joined = "\n".join(chunk.text for chunk in chunks)
    assert DIAGRAM_PLACEHOLDER in joined


def test_enrich_method_picture_stores_algebra_latex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = DoclingDocument(name="method-algebra")
    doc.add_heading(text="Method 2", level=1)
    picture = doc.add_picture()

    def _vision(messages: list[dict[str, object]], **_kwargs: object) -> str:
        assert _vision_image_count(messages) == 1
        return json.dumps([r"a^{2}"])

    monkeypatch.setattr(PictureItem, "get_image", _picture_crop)
    monkeypatch.setattr(
        "education_platform.workers.ingest.chat_completion_vision_sync",
        _vision,
    )
    enrich_formulas_with_openrouter(
        doc,
        settings=Settings(openrouter_api_key="test-key"),
    )
    assert picture not in doc.pictures
    _patch_chunker_tokenizer(monkeypatch)
    chunks = chunk_docling_document(doc)
    joined = "\n".join(chunk.text for chunk in chunks)
    assert r"a^{2}" in joined
    assert DIAGRAM_PLACEHOLDER not in joined


def test_enrich_formulas_batches_scans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _VisionFormulaItem(text="", orig="")
    second = _VisionFormulaItem(text=FORMULA_NOT_DECODED, orig="")
    called: list[list[dict[str, object]]] = []

    def _vision(messages: list[dict[str, object]], **_kwargs: object) -> str:
        called.append(messages)
        return json.dumps([r"\frac{1}{2}", r"a^{2}"])

    monkeypatch.setattr(
        "education_platform.workers.ingest.chat_completion_vision_sync",
        _vision,
    )
    enrich_formulas_with_openrouter(
        _FormulaDoc([first, second]),
        settings=Settings(openrouter_api_key="test-key"),
    )
    assert first.text == r"\frac{1}{2}"
    assert second.text == r"a^{2}"
    assert len(called) == 1
    content = called[0][0]["content"]
    assert isinstance(content, list)
    images = [part for part in content if part.get("type") == "image_url"]
    assert len(images) == 2


def test_enrich_formulas_skips_api_when_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _VisionFormulaItem(text="", orig="x + 2 = 5")

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("must not call OpenRouter when the key is missing")

    monkeypatch.setattr(
        "education_platform.workers.ingest.chat_completion_vision_sync",
        _boom,
    )
    enrich_formulas_with_openrouter(
        _FormulaDoc([item]),
        settings=Settings(openrouter_api_key=""),
    )
    assert item.text == ""
    _patch_chunker_tokenizer(monkeypatch)
    doc = DoclingDocument(name="formula-orig-fallback")
    doc.add_formula(text=item.text, orig=item.orig)
    chunks = chunk_docling_document(doc)
    joined = "\n".join(chunk.text for chunk in chunks)
    assert "x + 2 = 5" in joined
    assert FORMULA_NOT_DECODED not in joined


def test_enrich_formulas_keeps_orig_when_crop_or_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NoCrop:
        def __init__(self) -> None:
            self.text = FORMULA_NOT_DECODED
            self.orig = "a^2"
            self.label = SimpleNamespace(value="formula")

        def get_image(self, document: object, prov_index: int = 0) -> None:
            _ = document, prov_index
            return None

    no_crop = _NoCrop()
    no_crop.orig = ""
    boom = _VisionFormulaItem(text="", orig="")

    def _fail(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("OpenRouter down")

    monkeypatch.setattr(
        "education_platform.workers.ingest.chat_completion_vision_sync",
        _fail,
    )
    enrich_formulas_with_openrouter(
        _FormulaDoc([no_crop, boom]),
        settings=Settings(openrouter_api_key="test-key"),
    )
    assert no_crop.text == FORMULA_NOT_DECODED
    assert boom.text == ""


def test_chat_completion_vision_sync_sends_image_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Completions:
        def create(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=r"\frac{1}{2}"))]
            )

    class _Client:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(
        "education_platform.core.llm.build_openrouter_sync_client",
        lambda _settings=None: _Client(),
    )
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "one LaTeX expression"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AAAA"},
                },
            ],
        }
    ]
    out = chat_completion_vision_sync(
        messages,
        settings=Settings(openrouter_api_key="test-key"),
    )
    assert out == r"\frac{1}{2}"
    assert captured["model"] == "openai/gpt-4o-mini"
    assert captured["messages"] == messages


def test_apply_formula_pipeline_options_disables_local_codeformula() -> None:
    options = SimpleNamespace(
        do_formula_enrichment=True,
        generate_page_images=False,
        do_table_structure=False,
    )
    apply_formula_pipeline_options(options)
    assert options.do_formula_enrichment is False
    assert options.generate_page_images is True
    assert options.do_table_structure is True
    assert options.accelerator_options.device == docling_accelerator_device()
    assert options.accelerator_options.device in {"cuda", "mps", "cpu"}


def test_apply_formula_pipeline_options_prefers_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("education_platform.workers.ingest.cuda_is_available", lambda: True)
    monkeypatch.setattr("education_platform.workers.ingest.mps_is_available", lambda: True)
    options = SimpleNamespace(
        do_formula_enrichment=False,
        accelerator_options=SimpleNamespace(device="auto"),
    )
    apply_formula_pipeline_options(options)
    assert options.accelerator_options.device == "cuda"
    assert docling_accelerator_device() == "cuda"


def test_apply_formula_pipeline_options_sets_mps_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("education_platform.workers.ingest.cuda_is_available", lambda: False)
    monkeypatch.setattr(
        "education_platform.workers.ingest.mps_is_available",
        lambda: True,
    )
    options = SimpleNamespace(
        do_formula_enrichment=False,
        accelerator_options=SimpleNamespace(device="auto"),
    )
    apply_formula_pipeline_options(options)
    assert options.accelerator_options.device == "mps"
    assert docling_accelerator_device() == "mps"


def test_apply_formula_pipeline_options_sets_cpu_when_mps_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("education_platform.workers.ingest.cuda_is_available", lambda: False)
    monkeypatch.setattr(
        "education_platform.workers.ingest.mps_is_available",
        lambda: False,
    )
    options = SimpleNamespace(
        do_formula_enrichment=False,
        accelerator_options=SimpleNamespace(device="auto"),
    )
    apply_formula_pipeline_options(options)
    assert options.accelerator_options.device == "cpu"
    assert docling_accelerator_device() == "cpu"


def test_allow_mps_patch_lets_auto_select_metal_when_available() -> None:
    from docling.datamodel.accelerator_options import AcceleratorDevice
    from docling.models.inference_engines.vlm import transformers_engine as te

    from education_platform.workers import ingest as ingest_mod

    ingest_mod._CODEFORMULA_MPS_PATCHED = False
    ingest_mod._allow_mps_on_codeformula_transformers()
    device = te.decide_device(
        "auto",
        supported_devices=[
            AcceleratorDevice.CPU,
            AcceleratorDevice.CUDA,
            AcceleratorDevice.XPU,
        ],
    )
    if ingest_mod.mps_is_available():
        assert device == "mps"
    else:
        # AUTO still prefers CUDA/XPU when MPS is absent (Linux GPU hosts).
        assert device == "cpu" or device.startswith("cuda") or device.startswith("xpu")


def test_convert_pdf_with_docling_disables_formula_enrichment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    mps_patch_calls = {"n": 0}
    enrich_calls = {"n": 0}
    monkeypatch.setattr("education_platform.workers.ingest.cuda_is_available", lambda: True)
    monkeypatch.setattr(
        "education_platform.workers.ingest.mps_is_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "education_platform.workers.ingest._allow_mps_on_codeformula_transformers",
        lambda: mps_patch_calls.__setitem__("n", mps_patch_calls["n"] + 1),
    )
    monkeypatch.setattr(
        "education_platform.workers.ingest.enrich_formulas_with_openrouter",
        lambda _document: enrich_calls.__setitem__("n", enrich_calls["n"] + 1),
    )

    class FakePipelineOptions:
        def __init__(self) -> None:
            self.do_formula_enrichment = True
            self.generate_page_images = False
            self.do_table_structure = False
            self.images_scale = 1.0
            self.accelerator_options = SimpleNamespace(device="auto")

    class FakePdfFormatOption:
        def __init__(self, *, pipeline_options: FakePipelineOptions) -> None:
            captured["pipeline_options"] = pipeline_options

    class FakeConverter:
        def __init__(self, format_options: object = None) -> None:
            captured["format_options"] = format_options

        def convert(self, source: str) -> object:
            captured["source"] = source
            return SimpleNamespace(document="decoded-doc")

    fake_pdf = SimpleNamespace(PDF="pdf")
    for name in ("docling", "docling.datamodel"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "docling.datamodel.pipeline_options",
        SimpleNamespace(PdfPipelineOptions=FakePipelineOptions),
    )
    monkeypatch.setitem(
        sys.modules,
        "docling.datamodel.base_models",
        SimpleNamespace(InputFormat=fake_pdf),
    )
    monkeypatch.setitem(
        sys.modules,
        "docling.document_converter",
        SimpleNamespace(DocumentConverter=FakeConverter, PdfFormatOption=FakePdfFormatOption),
    )

    pdf = tmp_path / "exercise.pdf"
    pdf.write_bytes(TINY_PDF)
    document = convert_pdf_with_docling(pdf)
    assert document == "decoded-doc"
    assert mps_patch_calls["n"] == 0
    assert enrich_calls["n"] == 0
    options = captured["pipeline_options"]
    assert isinstance(options, FakePipelineOptions)
    assert options.do_formula_enrichment is False
    assert options.generate_page_images is True
    assert options.do_table_structure is True
    assert options.images_scale == 1.0
    assert options.accelerator_options.device == "cuda"
    format_options = captured["format_options"]
    assert isinstance(format_options, dict)
    assert fake_pdf.PDF in format_options


def test_pdf_pipeline_keeps_layout_compile_enabled() -> None:
    import os

    from docling.datamodel.object_detection_engine_options import (
        TransformersObjectDetectionEngineOptions,
    )
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.settings import settings as docling_settings

    assert os.environ.get("TORCH_COMPILE_DISABLE") != "1"
    assert os.environ.get("TORCHDYNAMO_DISABLE") != "1"
    assert os.environ.get("DOCLING_INFERENCE_COMPILE_TORCH_MODELS") != "false"
    docling_settings.inference.compile_torch_models = True
    options = PdfPipelineOptions()
    engine = options.layout_options.engine_options
    assert isinstance(engine, TransformersObjectDetectionEngineOptions)
    assert engine.compile_model is True


def test_public_ingest_error_keeps_docling_cause() -> None:
    from docling.exceptions import ConversionError

    cause = RuntimeError(
        "InvalidCxxCompiler: No working C++ compiler found in "
        "torch._inductor.config.cpp.cxx: (None, 'g++')"
    )
    try:
        raise ConversionError("Conversion failed for: hemh109.pdf with status: failure.") from cause
    except ConversionError as exc:
        message = public_ingest_error(exc)
    assert "hemh109.pdf" in message
    assert "InvalidCxxCompiler" in message
    assert "g++" in message
    assert "\n" not in message


def test_parse_pdf_with_docling_enriches_before_chunk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    order: list[str] = []

    def _convert(_path: Path) -> str:
        order.append("convert")
        return "doc"

    def _enrich(document: str) -> None:
        assert document == "doc"
        order.append("enrich")

    def _chunk(document: str) -> list[str]:
        assert document == "doc"
        order.append("chunk")
        return [r"$\frac{1}{2}$"]

    monkeypatch.setattr("education_platform.workers.ingest.convert_pdf_with_docling", _convert)
    monkeypatch.setattr(
        "education_platform.workers.ingest.enrich_formulas_with_openrouter",
        _enrich,
    )
    monkeypatch.setattr("education_platform.workers.ingest.chunk_docling_document", _chunk)
    pdf = tmp_path / "exercise.pdf"
    pdf.write_bytes(TINY_PDF)
    chunks = parse_pdf_with_docling(pdf)
    assert order == ["convert", "enrich", "chunk"]
    assert chunks == [r"$\frac{1}{2}$"]


def test_pgvector_upsert_delete_and_search(clean_db: str) -> None:
    _ = clean_db
    get_settings.cache_clear()

    version_id = uuid4()
    chunk_id = uuid4()
    institution_id = uuid4()
    embedding = [0.01] * 384
    upsert_rows(
        [
            VectorRow(
                chunk_id=chunk_id,
                embedding=embedding,
                doc_id=uuid4(),
                doc_kind="knowledge_document_version",
                institution_id=institution_id,
                required_roles=["administrator"],
                doc_type="policy",
                page_number=1,
                version_id=version_id,
            )
        ]
    )
    assert count_for_version(version_id) == 1
    hits = search_similar(
        embedding,
        institution_id=institution_id,
        limit=5,
        required_role="administrator",
        doc_kind="knowledge_document_version",
        doc_type="policy",
    )
    assert len(hits) == 1
    assert hits[0].chunk_id == chunk_id
    assert delete_by_version(version_id) == 1
    assert count_for_version(version_id) == 0
    get_settings.cache_clear()


def test_claim_loop_processes_queued_job(
    seeded_db: Session,
    seeded_subtopic_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from education_platform.modules.materials.models import SourceMaterial
    from education_platform.modules.rag import storage

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "education_platform.workers.ingest.embed_texts",
        lambda texts: [[0.03] * 384 for _ in texts],
    )

    material = SourceMaterial(
        subtopic_id=seeded_subtopic_id,
        title="Claim loop lesson",
        slug=f"claim-loop-{uuid4().hex[:8]}",
    )
    seeded_db.add(material)
    seeded_db.flush()
    object_key = storage.build_object_key(
        institution_id=uuid4(),
        kind="source_materials",
        filename="lesson.pdf",
    )
    storage.store_bytes(object_key, TINY_PDF)
    version = SourceMaterialVersion(
        source_material_id=material.id,
        version_number=1,
        lifecycle_status=SourceMaterialVersionStatus.PROCESSING,
        title="Claim loop lesson",
        content_format="pdf",
        blob_object_key=object_key,
        blob_content_type="application/pdf",
        checksum=storage.sha256_hex(TINY_PDF),
    )
    seeded_db.add(version)
    seeded_db.flush()
    job = IngestJob(
        source_material_version_id=version.id,
        status=IngestJobStatus.QUEUED,
    )
    seeded_db.add(job)
    seeded_db.commit()

    claimed = claim_next_job(seeded_db)
    assert claimed == job.id
    seeded_db.expire_all()
    running = seeded_db.get(IngestJob, job.id)
    assert running is not None
    assert running.status == IngestJobStatus.RUNNING

    process_ingest_job_sync(
        str(job.id),
        parse_pdf=lambda _path: _sample_chunks(),
    )

    seeded_db.expire_all()
    done = seeded_db.get(IngestJob, job.id)
    assert done is not None
    assert done.status == IngestJobStatus.SUCCEEDED, done.error
    refreshed = seeded_db.get(SourceMaterialVersion, version.id)
    assert refreshed is not None
    assert refreshed.lifecycle_status == SourceMaterialVersionStatus.READY
    assert count_for_version(version.id) >= 1

    assert poll_once(session=seeded_db) is None
    get_settings.cache_clear()


def test_idle_poll_reuses_shared_sync_engine(
    seeded_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker loop must not create a new SQLAlchemy engine on every poll.

    Previously ``_sync_session`` called ``create_engine`` per iteration and only
    closed the Session, leaking connection pools until Postgres refused new
    clients.
    """
    from sqlalchemy import create_engine as real_create_engine

    from education_platform.db.session import get_sync_engine, reset_engine

    _ = seeded_db
    reset_engine()
    engine = get_sync_engine()
    created: list[object] = []

    def _counting_create_engine(*args: object, **kwargs: object) -> object:
        created.append(object())
        return real_create_engine(*args, **kwargs)

    monkeypatch.setattr("education_platform.db.session.create_engine", _counting_create_engine)
    assert poll_once() is None
    assert poll_once() is None
    assert poll_once() is None
    assert get_sync_engine() is engine
    assert created == []
    reset_engine()
    get_settings.cache_clear()


def test_worker_happy_path_source_material(
    seeded_db: Session,
    seeded_subtopic_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from education_platform.modules.materials.models import SourceMaterial
    from education_platform.modules.rag import storage

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()

    monkeypatch.setattr(
        "education_platform.workers.ingest.embed_texts",
        lambda texts: [[0.02] * 384 for _ in texts],
    )

    material = SourceMaterial(
        subtopic_id=seeded_subtopic_id,
        title="Upload lesson",
        slug="upload-lesson",
    )
    seeded_db.add(material)
    seeded_db.flush()
    object_key = storage.build_object_key(
        institution_id=uuid4(),
        kind="source_materials",
        filename="lesson.pdf",
    )
    storage.store_bytes(object_key, TINY_PDF)
    version = SourceMaterialVersion(
        source_material_id=material.id,
        version_number=1,
        lifecycle_status=SourceMaterialVersionStatus.PROCESSING,
        title="Upload lesson",
        content_format="pdf",
        blob_object_key=object_key,
        blob_content_type="application/pdf",
        checksum=storage.sha256_hex(TINY_PDF),
    )
    seeded_db.add(version)
    seeded_db.flush()
    job = IngestJob(
        source_material_version_id=version.id,
        status=IngestJobStatus.QUEUED,
    )
    seeded_db.add(job)
    seeded_db.commit()

    process_ingest_job_sync(
        str(job.id),
        parse_pdf=lambda _path: _sample_chunks(),
    )

    seeded_db.expire_all()
    refreshed = seeded_db.get(SourceMaterialVersion, version.id)
    assert refreshed is not None
    assert refreshed.lifecycle_status == SourceMaterialVersionStatus.READY
    chunks = seeded_db.scalars(
        select(SourceChunk).where(SourceChunk.source_material_version_id == version.id)
    ).all()
    assert len(chunks) == 1
    assert chunks[0].page_number == 1
    assert chunks[0].section_heading == SAMPLE_SECTION
    done = seeded_db.get(IngestJob, job.id)
    assert done is not None
    assert done.status == IngestJobStatus.SUCCEEDED, done.error
    assert count_for_version(version.id) >= 1
    assert (
        seeded_db.scalar(
            select(ContentGenerationRun).where(
                ContentGenerationRun.intake_source_material_version_id == version.id
            )
        )
        is None
    )
    get_settings.cache_clear()


def test_worker_happy_path_knowledge_document(
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from education_platform.modules.rag import storage

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "education_platform.workers.ingest.embed_texts",
        lambda texts: [[0.04] * 384 for _ in texts],
    )

    document = KnowledgeDocument(
        institution_id=_institution_id(seeded_db),
        title="Handbook",
        slug=f"handbook-{uuid4().hex[:8]}",
        doc_type="handbook",
        required_roles=["administrator", "teacher"],
    )
    seeded_db.add(document)
    seeded_db.flush()
    object_key = storage.build_object_key(
        institution_id=document.institution_id,
        kind="knowledge_documents",
        filename="handbook.pdf",
    )
    storage.store_bytes(object_key, TINY_PDF)
    version = KnowledgeDocumentVersion(
        document_id=document.id,
        version_number=1,
        lifecycle_status=KnowledgeDocumentVersionStatus.PROCESSING,
        blob_object_key=object_key,
        blob_content_type="application/pdf",
        checksum=storage.sha256_hex(TINY_PDF),
    )
    seeded_db.add(version)
    seeded_db.flush()
    job = IngestJob(
        knowledge_document_version_id=version.id,
        status=IngestJobStatus.QUEUED,
    )
    seeded_db.add(job)
    seeded_db.commit()

    process_ingest_job_sync(
        str(job.id),
        parse_pdf=lambda _path: _sample_chunks(),
    )

    seeded_db.expire_all()
    refreshed = seeded_db.get(KnowledgeDocumentVersion, version.id)
    assert refreshed is not None
    assert refreshed.lifecycle_status == KnowledgeDocumentVersionStatus.READY
    chunks = seeded_db.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.knowledge_document_version_id == version.id)
    ).all()
    assert len(chunks) == 1
    assert chunks[0].page_number == 1
    assert chunks[0].section_heading == SAMPLE_SECTION
    done = seeded_db.get(IngestJob, job.id)
    assert done is not None
    assert done.status == IngestJobStatus.SUCCEEDED, done.error
    assert count_for_version(version.id) >= 1
    get_settings.cache_clear()


def test_reprocess_failure_keeps_prior_embeddings_atomic(
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Embedding writes must share the worker transaction with chunk writes.

    Previously ``delete_by_version`` / ``upsert_rows`` opened their own sessions and
    committed immediately. A failed re-ingest then rolled back restored chunks while
    the committed vector delete stayed — searchable index wiped for that version.
    """
    from education_platform.modules.rag import storage

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    monkeypatch.setattr(
        "education_platform.workers.ingest.embed_texts",
        lambda texts: [[0.05] * 384 for _ in texts],
    )

    document = KnowledgeDocument(
        institution_id=_institution_id(seeded_db),
        title="Atomic Policy",
        slug=f"atomic-{uuid4().hex[:8]}",
        doc_type="policy",
        required_roles=["administrator"],
    )
    seeded_db.add(document)
    seeded_db.flush()
    object_key = storage.build_object_key(
        institution_id=document.institution_id,
        kind="knowledge_documents",
        filename="atomic.pdf",
    )
    storage.store_bytes(object_key, TINY_PDF)
    version = KnowledgeDocumentVersion(
        document_id=document.id,
        version_number=1,
        lifecycle_status=KnowledgeDocumentVersionStatus.PROCESSING,
        blob_object_key=object_key,
        blob_content_type="application/pdf",
        checksum=storage.sha256_hex(TINY_PDF),
    )
    seeded_db.add(version)
    seeded_db.flush()
    job = IngestJob(
        knowledge_document_version_id=version.id,
        status=IngestJobStatus.QUEUED,
    )
    seeded_db.add(job)
    seeded_db.commit()

    process_ingest_job_sync(str(job.id), parse_pdf=lambda _path: _sample_chunks())
    seeded_db.expire_all()
    assert count_for_version(version.id) >= 1
    prior_chunks = seeded_db.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.knowledge_document_version_id == version.id)
    ).all()
    assert len(prior_chunks) == 1
    prior_chunk_id = prior_chunks[0].id

    retry = IngestJob(
        knowledge_document_version_id=version.id,
        status=IngestJobStatus.QUEUED,
    )
    seeded_db.add(retry)
    seeded_db.commit()

    def _boom_embed(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embed failed after chunk clear")

    monkeypatch.setattr("education_platform.workers.ingest.embed_texts", _boom_embed)
    process_ingest_job_sync(str(retry.id), parse_pdf=lambda _path: _sample_chunks())

    seeded_db.expire_all()
    done = seeded_db.get(IngestJob, retry.id)
    assert done is not None
    assert done.status == IngestJobStatus.FAILED
    chunks = seeded_db.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.knowledge_document_version_id == version.id)
    ).all()
    assert len(chunks) == 1
    assert chunks[0].id == prior_chunk_id
    assert count_for_version(version.id) >= 1
    get_settings.cache_clear()


def test_worker_failed_parse_marks_failed(
    seeded_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from education_platform.modules.rag import storage

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()

    document = KnowledgeDocument(
        institution_id=_institution_id(seeded_db),
        title="Broken Policy",
        slug=f"broken-{uuid4().hex[:8]}",
        doc_type="policy",
        required_roles=["administrator"],
    )
    seeded_db.add(document)
    seeded_db.flush()
    object_key = storage.build_object_key(
        institution_id=document.institution_id,
        kind="knowledge_documents",
        filename="bad.pdf",
    )
    storage.store_bytes(object_key, TINY_PDF)
    version = KnowledgeDocumentVersion(
        document_id=document.id,
        version_number=1,
        lifecycle_status=KnowledgeDocumentVersionStatus.PROCESSING,
        blob_object_key=object_key,
        blob_content_type="application/pdf",
        checksum=storage.sha256_hex(TINY_PDF),
    )
    seeded_db.add(version)
    seeded_db.flush()
    job = IngestJob(
        knowledge_document_version_id=version.id,
        status=IngestJobStatus.QUEUED,
    )
    seeded_db.add(job)
    seeded_db.commit()

    def _boom(_path: Path) -> list[TextChunk]:
        raise RuntimeError("Docling parse failed")

    process_ingest_job_sync(str(job.id), parse_pdf=_boom)

    seeded_db.expire_all()
    refreshed = seeded_db.get(KnowledgeDocumentVersion, version.id)
    assert refreshed is not None
    assert refreshed.lifecycle_status == KnowledgeDocumentVersionStatus.FAILED
    assert refreshed.failure_reason
    assert "Docling" in refreshed.failure_reason
    done = seeded_db.get(IngestJob, job.id)
    assert done is not None
    assert done.status == IngestJobStatus.FAILED
    assert (
        seeded_db.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.knowledge_document_version_id == version.id)
        ).all()
        == []
    )
    get_settings.cache_clear()


def _institution_id(session: Session) -> UUID:
    from education_platform.modules.auth.models import Institution

    institution = session.scalar(select(Institution).limit(1))
    assert institution is not None
    return institution.id
