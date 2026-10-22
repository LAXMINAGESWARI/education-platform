"""retrieve_chunks tool — pgvector similarity search with authz filters."""

from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.db.session import get_sync_engine
from education_platform.modules.assistant.contracts import (
    RetrieveChunksArgs,
    RetrieveChunksResult,
    RetrievedChunk,
)
from education_platform.modules.assistant.tools.registry import ToolSpec, register_tool
from education_platform.modules.authorization.principal import Principal
from education_platform.modules.materials.models import SourceChunk, SourceMaterialVersion
from education_platform.modules.rag.embeddings import embed_texts
from education_platform.modules.rag.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
)
from education_platform.modules.rag.vector_store import SimilarChunk, search_similar

RETRIEVE_CHUNK_EXCERPT_MAX = 4000


def _load_chunk_payloads(hits: list[SimilarChunk]) -> list[RetrievedChunk]:
    """Load chunk text through the shared sync engine.

    That engine uses the application database URL and role. ``reset_engine()``
    drops it for tests, and the app lifespan disposes it on shutdown.
    """
    if not hits:
        return []
    engine = get_sync_engine()
    with Session(engine) as session:
        results: list[RetrievedChunk] = []
        for hit in hits:
            label = "Source"
            excerpt = ""
            page_number: int | None = None
            if hit.doc_kind == "knowledge_document_version":
                knowledge_chunk = session.get(KnowledgeChunk, hit.chunk_id)
                if knowledge_chunk is None:
                    continue
                excerpt = knowledge_chunk.text
                page_number = knowledge_chunk.page_number
                knowledge_version = session.get(KnowledgeDocumentVersion, hit.version_id)
                if knowledge_version is not None:
                    doc = session.get(KnowledgeDocument, knowledge_version.document_id)
                    if doc is not None:
                        label = doc.title
            elif hit.doc_kind == "source_material_version":
                source_chunk = session.get(SourceChunk, hit.chunk_id)
                if source_chunk is None:
                    continue
                excerpt = source_chunk.text
                page_number = source_chunk.page_number
                source_version = session.get(SourceMaterialVersion, hit.version_id)
                label = "Curriculum material" if source_version is not None else "Curriculum"
            else:
                continue
            if not excerpt.strip():
                continue
            results.append(
                RetrievedChunk(
                    id=str(hit.chunk_id),
                    label=label,
                    excerpt=excerpt[:RETRIEVE_CHUNK_EXCERPT_MAX],
                    page_number=page_number,
                    doc_kind=hit.doc_kind,
                    doc_id=str(hit.doc_id),
                    distance=float(hit.distance),
                )
            )
        return results


async def retrieve_chunks_handler(
    *,
    principal: Principal,
    query: str,
    limit: int | None = None,
    doc_kind: str | None = None,
) -> dict[str, object]:
    """Embed `query` and return institution-scoped similar chunks for the principal."""
    settings = get_settings()
    top_k = limit or settings.chat_retrieval_limit
    role = "administrator" if principal.is_administrator else next(iter(principal.roles), None)

    def _run() -> list[RetrievedChunk]:
        vectors = embed_texts([query])
        hits = search_similar(
            vectors[0],
            institution_id=principal.institution_id,
            limit=top_k,
            required_role=role,
            doc_kind=doc_kind,
        )
        return _load_chunk_payloads(hits)

    chunks = await asyncio.to_thread(_run)
    return RetrieveChunksResult(chunks=chunks, count=len(chunks)).model_dump(mode="python")


register_tool(
    ToolSpec(
        name="retrieve_chunks",
        description=(
            "Retrieve relevant policy or curriculum text chunks from the institution "
            "vector index. Always call this before answering factual policy questions."
        ),
        handler=retrieve_chunks_handler,
        args_model=RetrieveChunksArgs,
        result_model=RetrieveChunksResult,
    )
)
