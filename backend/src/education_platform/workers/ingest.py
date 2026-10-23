"""Ingest worker: parse → chunk → embed → index uploaded PDFs."""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import torch
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from education_platform.core.config import Settings, get_settings
from education_platform.core.llm import chat_completion_vision_sync
from education_platform.db.session import sync_session
from education_platform.modules.academics.models import (
    AcademicPeriod,
    GradeSubjectOffering,
    PeriodGrade,
    Subtopic,
    Topic,
)
from education_platform.modules.generation.models import ContentGenerationRun
from education_platform.modules.generation.worker import fail_run_for_intake, on_intake_indexed
from education_platform.modules.materials.models import (
    SourceChunk,
    SourceMaterial,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
)
from education_platform.modules.progress.events import (
    DONE_MESSAGE,
    EXTRACTING_MESSAGE,
    PARSING_MESSAGE,
    RECEIVED_MESSAGE,
    chunking_message,
    embedding_message,
    friendly_failure_message,
)
from education_platform.modules.progress.stage_store import (
    StageClaim,
    commit_upload_terminal,
    persist_upload_stage,
)
from education_platform.modules.progress.types import ProgressSubject, run_subject, version_subject
from education_platform.modules.progress.wake import publish_wake
from education_platform.modules.rag import storage
from education_platform.modules.rag.chunking import (
    FORMULA_NOT_DECODED,
    TextChunk,
    _is_blank_or_undecoded_formula,
    _is_formula_item,
    _is_section_header,
    chunk_docling_document,
)
from education_platform.modules.rag.contracts import IngestJobClaim
from education_platform.modules.rag.embeddings import embed_texts
from education_platform.modules.rag.models import (
    IngestJob,
    IngestJobStatus,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeDocumentVersionStatus,
)
from education_platform.modules.rag.vector_store import VectorRow, delete_by_version, upsert_rows
from education_platform.workers.lease import (
    bound_lease,
    close_session,
    commit_owned,
    exceeded_max_attempts_message,
    prepare_leased_writes,
)

logger = logging.getLogger(__name__)

ParsePdfFn = Callable[[Path], list[TextChunk]]
_FAILURE_REASON_LIMIT = 2000
EMBEDDING_PROGRESS_BATCH = 16
type _VersionRow = SourceMaterialVersion | KnowledgeDocumentVersion

_CODEFORMULA_MPS_PATCHED = False
FORMULA_VISION_BATCH_SIZE = 8
FORMULA_VISION_BATCH_PROMPT = (
    "Each image is one mathematical formula whose PDF text layer was empty. "
    "Return a JSON array of LaTeX strings, one per image, in the same order. "
    "Do not wrap expressions in $ or $$ fences. Do not add any prose."
)
PICTURE_VISION_BATCH_PROMPT = (
    "Each image is one textbook figure. "
    "Return a JSON array of strings, one per image, in the same order. "
    "If the image is algebra, return its LaTeX. "
    "If the image is a geometric figure, return an empty string. "
    "Do not wrap expressions in $ or $$ fences. Do not add any prose."
)
_MISTAKE_SECTION = "6.3 Mind the Mistake, Mend the Mistake"
_METHOD_HEADING = re.compile(r"^Method \d+$")
_CALLOUT_WORD = re.compile(r"\bIncrease\b")
_SPLIT_POWER = re.compile(r"(?:[A-Za-z]|\d+|\([^)]+\))\s+\d+")


def cuda_is_available() -> bool:
    """True when PyTorch can run on an NVIDIA GPU."""
    return bool(torch.cuda.is_available())


def mps_is_available() -> bool:
    """True when PyTorch can run on Apple Silicon Metal (MPS)."""
    return bool(torch.backends.mps.is_built() and torch.backends.mps.is_available())


def docling_accelerator_device() -> str:
    """Docling layout device: CUDA, then MPS, then CPU.

    MPS must not win while CUDA is present. Layout on CPU is the ingest spike.
    """
    if cuda_is_available():
        return "cuda"
    if mps_is_available():
        return "mps"
    return "cpu"


def _allow_mps_on_codeformula_transformers() -> None:
    """Let CodeFormula's Transformers engine use Metal.

    ``TransformersVlmEngine`` and legacy ``CodeFormulaModel`` pass
    ``supported_devices=[CPU, CUDA, XPU]`` into ``decide_device``. AUTO then
    strips MPS and falls back to CPU; an explicit MPS request raises. Layout
    already lists MPS; the VLM path is the bottleneck and does run on Metal
    once ``device_map='mps'``.
    """
    global _CODEFORMULA_MPS_PATCHED
    if _CODEFORMULA_MPS_PATCHED:
        return
    from docling.datamodel.accelerator_options import AcceleratorDevice
    from docling.models.inference_engines.vlm import transformers_engine as te
    from docling.models.stages.code_formula import code_formula_model as cfm

    def _with_mps(decide: Callable[..., str]) -> Callable[..., str]:
        def wrapped(
            accelerator_device: str,
            supported_devices: list[Any] | None = None,
        ) -> str:
            if supported_devices is not None and AcceleratorDevice.MPS not in supported_devices:
                supported_devices = [*supported_devices, AcceleratorDevice.MPS]
            return decide(accelerator_device, supported_devices)

        return wrapped

    te.decide_device = _with_mps(te.decide_device)  # type: ignore[attr-defined]
    cfm.decide_device = _with_mps(cfm.decide_device)  # type: ignore[attr-defined]
    _CODEFORMULA_MPS_PATCHED = True
    logger.info("CodeFormula Transformers engine: MPS added to supported_devices")


def apply_formula_pipeline_options(options: Any) -> Any:
    """Keep page images for formula crops. Never load local CodeFormula.

    ``do_formula_enrichment`` would download CodeFormulaV2 and was slower than
    the vision loop on CPU. Page images exist so ``FormulaItem.get_image`` can
    crop formulas whose PDF text layer is empty or damaged, and selected
    pictures; ``enrich_formulas_with_openrouter`` writes that LaTeX onto the
    item before chunking. A clean formula text layer skips vision and is copied
    into the chunk as math. TableFormer stays on so policy tables survive as
    markdown.
    """
    options.do_formula_enrichment = False
    options.generate_page_images = True
    options.do_table_structure = True
    device = docling_accelerator_device()
    accel = getattr(options, "accelerator_options", None)
    if accel is None:
        options.accelerator_options = SimpleNamespace(device=device)
    else:
        accel.device = device
    return options


def _png_base64(image: Any) -> str | None:
    if image is None:
        return None
    try:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
    except Exception:
        logger.exception("Failed to encode formula crop as PNG")
        return None
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _latex_from_vision_response(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("latex"):
            cleaned = cleaned[5:]
        cleaned = cleaned.strip()
    if cleaned.startswith("$$") and cleaned.endswith("$$") and len(cleaned) >= 4:
        cleaned = cleaned[2:-2].strip()
    elif cleaned.startswith("$") and cleaned.endswith("$") and len(cleaned) >= 2:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _formula_text_layer(item: Any) -> str:
    return str(getattr(item, "orig", "") or "").strip()


def _formula_layer_is_damaged(orig: str) -> bool:
    """True when the PDF text layer is a callout or a power split off its base."""
    return _CALLOUT_WORD.search(orig) is not None or _SPLIT_POWER.search(orig) is not None


def _picture_heading_needs_vision(heading: str) -> bool:
    return heading == _MISTAKE_SECTION or _METHOD_HEADING.fullmatch(heading) is not None


def _is_picture_item(item: Any) -> bool:
    if type(item).__name__ == "PictureItem":
        return True
    label = getattr(item, "label", None)
    if label is None:
        return False
    raw = getattr(label, "value", label)
    return str(raw).lower() == "picture"


def _replace_picture_with_formula(document: Any, picture: Any, latex: str) -> None:
    document.insert_formula(picture, latex)
    document.delete_items(node_items=[picture])


def _formula_vision_batch_messages(
    images_png_base64: Sequence[str],
    *,
    prompt: str,
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for encoded in images_png_base64:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            }
        )
    return [{"role": "user", "content": content}]


def _latex_list_from_vision_response(raw: str, *, expected: int) -> list[str]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        elif cleaned.lower().startswith("latex"):
            cleaned = cleaned[5:]
        cleaned = cleaned.strip()
    parsed: object
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        values = [_latex_from_vision_response(str(item)) for item in parsed]
        if len(values) < expected:
            values.extend([""] * (expected - len(values)))
        return values[:expected]
    if expected == 1:
        return [_latex_from_vision_response(cleaned)]
    return [""] * expected


def _store_formula_latex(item: Any, latex: str) -> None:
    item.text = latex


def _run_vision_batches(
    document: Any,
    pending: Sequence[Any],
    *,
    settings: Settings,
    prompt: str,
    store: Callable[[Any, str], None],
) -> tuple[int, int]:
    """Crop ``pending`` items and store returned LaTeX. Never raises."""
    enriched = 0
    skipped = 0
    for start in range(0, len(pending), FORMULA_VISION_BATCH_SIZE):
        batch = pending[start : start + FORMULA_VISION_BATCH_SIZE]
        encoded_batch: list[str] = []
        items: list[Any] = []
        for item in batch:
            try:
                encoded = _png_base64(item.get_image(document))
            except Exception:
                logger.exception("OpenRouter vision crop failed; keeping the item")
                skipped += 1
                continue
            if not encoded:
                skipped += 1
                continue
            encoded_batch.append(encoded)
            items.append(item)
        if not encoded_batch:
            continue
        try:
            latexes = _latex_list_from_vision_response(
                chat_completion_vision_sync(
                    _formula_vision_batch_messages(encoded_batch, prompt=prompt),
                    settings=settings,
                ),
                expected=len(encoded_batch),
            )
        except Exception:
            logger.exception("OpenRouter vision failed; keeping the item")
            skipped += len(items)
            continue
        for item, latex in zip(items, latexes, strict=True):
            if not latex or latex == FORMULA_NOT_DECODED:
                skipped += 1
                continue
            try:
                store(item, latex)
            except Exception:
                logger.exception("OpenRouter vision result was not stored; keeping the item")
                skipped += 1
                continue
            enriched += 1
    return enriched, skipped


def enrich_formulas_with_openrouter(
    document: Any,
    *,
    settings: Settings | None = None,
) -> None:
    """Write LaTeX onto empty or damaged formulas, and onto selected pictures.

    ``parse_pdf_with_docling`` calls this before chunking. A clean text layer
    such as ``x + 2 = 5`` is left alone and chunking copies it. An empty layer,
    a callout such as ``Increase``, or a split power such as ``a 2`` is cropped
    and sent to OpenRouter.

    Pictures whose current section heading is ``Method N`` or
    ``6.3 Mind the Mistake, Mend the Mistake`` are sent with a prompt that
    returns LaTeX for algebra and an empty string for a geometric figure.
    An empty result leaves the picture, so chunking keeps the diagram
    placeholder. Other pictures are not sent.

    A missing key, a failed crop, or an API error leaves the item in place
    and does not fail the PDF.
    """
    cfg = settings or get_settings()
    if not cfg.openrouter_configured:
        return
    iterate = getattr(document, "iterate_items", None)
    if iterate is None:
        return
    pending: list[Any] = []
    pictures: list[Any] = []
    skipped = 0
    current_heading = ""
    for item, _level in iterate():
        if _is_section_header(item):
            current_heading = str(getattr(item, "text", "") or "").strip()
            continue
        if _is_formula_item(item):
            text = str(getattr(item, "text", "") or "")
            if not _is_blank_or_undecoded_formula(text):
                continue
            layer = _formula_text_layer(item)
            if not _is_blank_or_undecoded_formula(layer) and not _formula_layer_is_damaged(layer):
                skipped += 1
                continue
            pending.append(item)
            continue
        if _is_picture_item(item) and _picture_heading_needs_vision(current_heading):
            pictures.append(item)

    def store_picture_latex(picture: Any, latex: str) -> None:
        _replace_picture_with_formula(document, picture, latex)

    enriched, formula_skipped = _run_vision_batches(
        document,
        pending,
        settings=cfg,
        prompt=FORMULA_VISION_BATCH_PROMPT,
        store=_store_formula_latex,
    )
    picture_enriched, picture_skipped = _run_vision_batches(
        document,
        pictures,
        settings=cfg,
        prompt=PICTURE_VISION_BATCH_PROMPT,
        store=store_picture_latex,
    )
    logger.info(
        "OpenRouter formula vision: enriched=%s skipped=%s",
        enriched + picture_enriched,
        skipped + formula_skipped + picture_skipped,
    )


def public_ingest_error(exc: BaseException) -> str:
    """One admin-facing reason. Keeps Docling's message and a distinct cause."""
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(parts) < 4:
        text = " ".join(str(current).split())
        if text and all(text not in earlier for earlier in parts):
            parts.append(text)
        current = current.__cause__ or current.__context__
    message = " — ".join(parts) if parts else type(exc).__name__
    if len(message) > _FAILURE_REASON_LIMIT:
        return message[: _FAILURE_REASON_LIMIT - 1].rstrip() + "…"
    return message


def convert_pdf_with_docling(path: Path) -> Any:
    """Convert a PDF to a DoclingDocument (lazy import; keeps layout structure)."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = apply_formula_pipeline_options(PdfPipelineOptions())
    layout_options = getattr(pipeline_options, "layout_options", None)
    layout_engine = getattr(layout_options, "engine_options", None)
    logger.info(
        "Docling convert accelerator_options.device=%s cuda_available=%s mps_available=%s "
        "do_formula_enrichment=%s generate_page_images=%s do_table_structure=%s "
        "layout_compile_model=%s",
        pipeline_options.accelerator_options.device,
        cuda_is_available(),
        mps_is_available(),
        pipeline_options.do_formula_enrichment,
        pipeline_options.generate_page_images,
        pipeline_options.do_table_structure,
        getattr(layout_engine, "compile_model", None),
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )
    result = converter.convert(str(path))
    return result.document


def parse_pdf_with_docling(path: Path) -> list[TextChunk]:
    """Parse a PDF, fill scan formulas, then chunk so the LaTeX is indexed."""
    document = convert_pdf_with_docling(path)
    enrich_formulas_with_openrouter(document)
    return chunk_docling_document(document)


def _sync_session() -> Session:
    return sync_session()


def _wake_ingest(session: Session, job: IngestJob) -> None:
    subjects: list[ProgressSubject] = []
    if job.source_material_version_id is not None:
        subjects.append(version_subject(job.source_material_version_id))
        run_id = session.scalar(
            select(ContentGenerationRun.id).where(
                ContentGenerationRun.intake_source_material_version_id
                == job.source_material_version_id
            )
        )
        if run_id is not None:
            subjects.append(run_subject(run_id))
    if job.knowledge_document_version_id is not None:
        subjects.append(version_subject(job.knowledge_document_version_id))
    if subjects:
        publish_wake(session, *subjects)


def _fail_job(session: Session, job: IngestJob, reason: str) -> None:
    job.status = IngestJobStatus.FAILED
    job.error = reason
    _wake_ingest(session, job)
    commit_owned(session)


def _live_claim(claim: StageClaim) -> StageClaim:
    """Keep the frozen attempt count and the lease token the heartbeat last wrote.

    A graceful release refunds ``attempts`` and the next claim can land on the
    same count with a new ``locked_until``. Matching attempts alone would let
    the old worker keep writing stages.
    """
    lease = bound_lease()
    if lease is None:
        return claim
    with lease.lock:
        return StageClaim(attempts=claim.attempts, locked_until=lease.locked_until)


def _note_stage(
    version: _VersionRow,
    job: IngestJob,
    claim: StageClaim,
    *,
    stage: str,
    message: str,
    current: int | None = None,
    total: int | None = None,
    force: bool = False,
) -> None:
    persist_upload_stage(
        version,
        job,
        claim=_live_claim(claim),
        stage=stage,
        message=message,
        current=current,
        total=total,
        force=force,
    )


def _fail_version(session: Session, job: IngestJob, version: _VersionRow, reason: str) -> None:
    """Mark the version and job failed, with a friendly progress message."""
    stored = reason[:_FAILURE_REASON_LIMIT]
    lifecycle: SourceMaterialVersionStatus | KnowledgeDocumentVersionStatus
    if isinstance(version, SourceMaterialVersion):
        fail_run_for_intake(session, version.id, stored)
        lifecycle = SourceMaterialVersionStatus.FAILED
    else:
        lifecycle = KnowledgeDocumentVersionStatus.FAILED
    # The worker session does not expire on commit, so reload the stage written
    # by the short progress transactions before choosing the category.
    session.refresh(version, attribute_names=["stage"])
    commit_upload_terminal(
        session,
        version,
        job,
        stage="failed",
        lifecycle=lifecycle,
        failure_reason=stored,
        job_status=IngestJobStatus.FAILED,
        job_error=stored,
        message=friendly_failure_message(version.stage, stored),
    )
    commit_owned(session)


def fail_exhausted_ingest_job(session: Session, job_id: UUID) -> None:
    """Fail a job the worker lost, including any run still waiting on this intake."""
    job = session.get(IngestJob, job_id)
    if job is None:
        return
    reason = exceeded_max_attempts_message(int(job.max_attempts))
    if job.source_material_version_id is not None:
        version = session.get(SourceMaterialVersion, job.source_material_version_id)
        if version is not None:
            _fail_version(session, job, version, reason)
            return
    if job.knowledge_document_version_id is not None:
        version_k = session.get(KnowledgeDocumentVersion, job.knowledge_document_version_id)
        if version_k is not None:
            _fail_version(session, job, version_k, reason)
            return
    job.status = IngestJobStatus.FAILED
    job.error = reason[:2000]
    _wake_ingest(session, job)
    commit_owned(session)


def _institution_for_material(session: Session, material: SourceMaterial) -> UUID | None:
    if material.topic_id is not None:
        return session.scalar(
            select(AcademicPeriod.institution_id)
            .select_from(Topic)
            .join(
                GradeSubjectOffering,
                GradeSubjectOffering.id == Topic.grade_subject_offering_id,
            )
            .join(PeriodGrade, PeriodGrade.id == GradeSubjectOffering.period_grade_id)
            .join(AcademicPeriod, AcademicPeriod.id == PeriodGrade.academic_period_id)
            .where(Topic.id == material.topic_id)
        )
    return session.scalar(
        select(AcademicPeriod.institution_id)
        .select_from(Subtopic)
        .join(Topic, Topic.id == Subtopic.topic_id)
        .join(GradeSubjectOffering, GradeSubjectOffering.id == Topic.grade_subject_offering_id)
        .join(PeriodGrade, PeriodGrade.id == GradeSubjectOffering.period_grade_id)
        .join(AcademicPeriod, AcademicPeriod.id == PeriodGrade.academic_period_id)
        .where(Subtopic.id == material.subtopic_id)
    )


def _persist_and_embed(
    *,
    session: Session,
    chunks: list[TextChunk],
    version_id: UUID,
    doc_id: UUID,
    doc_kind: str,
    institution_id: UUID,
    required_roles: list[str],
    doc_type: str | None,
    create_chunk: Callable[[TextChunk], Any],
    clear_existing: Callable[[], None],
    embeddings: list[list[float]],
) -> None:
    clear_existing()
    session.flush()
    # Keep embedding deletes/upserts on this session so a later rollback cannot
    # leave orphan vectors (or wipe vectors while restoring chunks).
    delete_by_version(version_id, session=session)

    orm_chunks: list[Any] = []
    for chunk in chunks:
        row = create_chunk(chunk)
        session.add(row)
        orm_chunks.append(row)
    session.flush()

    vector_rows = [
        VectorRow(
            chunk_id=orm.id,
            embedding=embedding,
            doc_id=doc_id,
            doc_kind=doc_kind,
            institution_id=institution_id,
            required_roles=required_roles,
            doc_type=doc_type,
            page_number=chunk.page_number,
            version_id=version_id,
        )
        for orm, embedding, chunk in zip(orm_chunks, embeddings, chunks, strict=True)
    ]
    upsert_rows(vector_rows, session=session)


def process_ingest_job_sync(
    ingest_job_id: str,
    *,
    parse_pdf: ParsePdfFn | None = None,
) -> None:
    parse = parse_pdf or parse_pdf_with_docling
    session = _sync_session()
    prepare_leased_writes(session)
    try:
        job = session.get(IngestJob, UUID(ingest_job_id))
        if job is None:
            logger.error("Ingest job %s not found", ingest_job_id)
            return
        # Captured once. A later reclaim bumps attempts; stage writes keep this value.
        stage_claim = StageClaim(attempts=int(job.attempts or 0))

        try:
            claim = IngestJobClaim.model_validate(job)
        except ValidationError as exc:
            logger.error("Ingest job %s failed claim validation: %s", ingest_job_id, exc)
            job.status = IngestJobStatus.FAILED
            job.error = f"Invalid ingest job claim: {exc}"[:2000]
            commit_owned(session)
            return

        job.status = IngestJobStatus.RUNNING
        commit_owned(session)

        try:
            if claim.source_material_version_id is not None:
                _process_source_material(session, job, parse, stage_claim)
            elif claim.knowledge_document_version_id is not None:
                _process_knowledge_document(session, job, parse, stage_claim)
            else:
                _fail_job(session, job, "Ingest job has no target set")
        except Exception as exc:
            logger.exception("Ingest job %s failed", ingest_job_id)
            session.rollback()
            job = session.get(IngestJob, UUID(ingest_job_id))
            if job is None:
                return
            reason = public_ingest_error(exc)
            if job.source_material_version_id is not None:
                version = session.get(SourceMaterialVersion, job.source_material_version_id)
                if version is not None:
                    _fail_version(session, job, version, reason)
                    return
            elif job.knowledge_document_version_id is not None:
                version_k = session.get(KnowledgeDocumentVersion, job.knowledge_document_version_id)
                if version_k is not None:
                    _fail_version(session, job, version_k, reason)
                    return
            job.status = IngestJobStatus.FAILED
            job.error = reason
            _wake_ingest(session, job)
            commit_owned(session)
    finally:
        close_session(session)


def _embed_texts(
    version: _VersionRow, job: IngestJob, texts: list[str], claim: StageClaim
) -> list[list[float]]:
    """Embed in batches so chunk counts can move without committing chunk rows.

    The first report is the first finished batch. A forced ``0 of N`` write
    would replace chunking a few milliseconds later, and a subscriber only
    reads the latest row.
    """
    total = len(texts)
    vectors: list[list[float]] = []
    step = EMBEDDING_PROGRESS_BATCH
    for start in range(0, total, step):
        batch = texts[start : start + step]
        vectors.extend(embed_texts(batch))
        done = start + len(batch)
        _note_stage(
            version,
            job,
            claim,
            stage="embedding",
            message=embedding_message(done, total),
            current=done,
            total=total,
        )
    return vectors


def _parsed_chunks(
    session: Session,
    job: IngestJob,
    version: _VersionRow,
    parse: ParsePdfFn,
    claim: StageClaim,
) -> tuple[list[TextChunk], list[list[float]]] | None:
    """Walk the upload stages through extracting_topics. None means the job was failed."""
    _note_stage(version, job, claim, stage="received", message=RECEIVED_MESSAGE, force=True)
    if not version.blob_object_key:
        _fail_version(session, job, version, "Missing blob object key")
        return None
    _note_stage(version, job, claim, stage="parsing", message=PARSING_MESSAGE, force=True)
    chunks = parse(storage.resolve_blob_path(version.blob_object_key))
    if not chunks:
        _fail_version(session, job, version, "No extractable text")
        return None
    count = len(chunks)
    _note_stage(
        version,
        job,
        claim,
        stage="chunking",
        message=chunking_message(count),
        current=count,
        total=count,
        force=True,
    )
    embeddings = _embed_texts(version, job, [chunk.text for chunk in chunks], claim)
    headed = sum(1 for chunk in chunks if chunk.section_heading)
    _note_stage(
        version,
        job,
        claim,
        stage="extracting_topics",
        message=EXTRACTING_MESSAGE,
        current=headed,
        total=count,
        force=True,
    )
    return chunks, embeddings


def _finish_ready(
    session: Session,
    job: IngestJob,
    version: _VersionRow,
    *,
    lifecycle: SourceMaterialVersionStatus | KnowledgeDocumentVersionStatus,
    notify_intake: bool,
) -> None:
    if notify_intake and isinstance(version, SourceMaterialVersion):
        on_intake_indexed(session, version.id)
    commit_upload_terminal(
        session,
        version,
        job,
        stage="done",
        lifecycle=lifecycle,
        failure_reason=None,
        job_status=IngestJobStatus.SUCCEEDED,
        job_error=None,
        message=DONE_MESSAGE,
    )
    commit_owned(session)


def _process_source_material(
    session: Session, job: IngestJob, parse: ParsePdfFn, claim: StageClaim
) -> None:
    version = session.get(SourceMaterialVersion, job.source_material_version_id)
    if version is None:
        _fail_job(session, job, "Source material version not found")
        return

    version.lifecycle_status = SourceMaterialVersionStatus.PROCESSING
    version.failure_reason = None
    _wake_ingest(session, job)
    commit_owned(session)

    prepared = _parsed_chunks(session, job, version, parse, claim)
    if prepared is None:
        return
    chunks, embeddings = prepared

    material = session.get(SourceMaterial, version.source_material_id)
    if material is None:
        _fail_version(session, job, version, "Source material missing")
        return

    institution_id = _institution_for_material(session, material)
    if institution_id is None:
        _fail_version(session, job, version, "Unable to resolve institution")
        return

    def clear_existing() -> None:
        session.execute(
            delete(SourceChunk).where(SourceChunk.source_material_version_id == version.id)
        )

    def create_chunk(chunk: TextChunk) -> SourceChunk:
        return SourceChunk(
            source_material_version_id=version.id,
            ordinal=chunk.ordinal,
            text=chunk.text,
            content_hash=chunk.content_hash,
            page_number=chunk.page_number,
            section_heading=chunk.section_heading,
            token_count=chunk.token_count,
        )

    required_roles = (
        ["teacher", "administrator"]
        if material.topic_id is not None
        else ["student", "teacher", "administrator"]
    )
    _persist_and_embed(
        session=session,
        chunks=chunks,
        version_id=version.id,
        doc_id=material.id,
        doc_kind="source_material_version",
        institution_id=institution_id,
        required_roles=required_roles,
        doc_type="curriculum",
        create_chunk=create_chunk,
        clear_existing=clear_existing,
        embeddings=embeddings,
    )
    _finish_ready(
        session,
        job,
        version,
        lifecycle=SourceMaterialVersionStatus.READY,
        notify_intake=True,
    )


def _process_knowledge_document(
    session: Session, job: IngestJob, parse: ParsePdfFn, claim: StageClaim
) -> None:
    version = session.get(KnowledgeDocumentVersion, job.knowledge_document_version_id)
    if version is None:
        _fail_job(session, job, "Knowledge document version not found")
        return

    version.lifecycle_status = KnowledgeDocumentVersionStatus.PROCESSING
    version.failure_reason = None
    _wake_ingest(session, job)
    commit_owned(session)

    prepared = _parsed_chunks(session, job, version, parse, claim)
    if prepared is None:
        return
    chunks, embeddings = prepared

    document = session.get(KnowledgeDocument, version.document_id)
    if document is None:
        _fail_version(session, job, version, "Knowledge document missing")
        return

    roles = [str(r) for r in (document.required_roles or ["administrator", "teacher"])]

    def clear_existing() -> None:
        session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.knowledge_document_version_id == version.id)
        )

    def create_chunk(chunk: TextChunk) -> KnowledgeChunk:
        return KnowledgeChunk(
            knowledge_document_version_id=version.id,
            ordinal=chunk.ordinal,
            text=chunk.text,
            content_hash=chunk.content_hash,
            page_number=chunk.page_number,
            section_heading=chunk.section_heading,
            token_count=chunk.token_count,
        )

    _persist_and_embed(
        session=session,
        chunks=chunks,
        version_id=version.id,
        doc_id=document.id,
        doc_kind="knowledge_document_version",
        institution_id=document.institution_id,
        required_roles=roles,
        doc_type=document.doc_type,
        create_chunk=create_chunk,
        clear_existing=clear_existing,
        embeddings=embeddings,
    )
    _finish_ready(
        session,
        job,
        version,
        lifecycle=KnowledgeDocumentVersionStatus.READY,
        notify_intake=False,
    )
