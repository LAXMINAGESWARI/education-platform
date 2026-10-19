from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.api.deps import Principal, get_scope, require_administrator
from education_platform.db.session import get_session
from education_platform.modules.assessments.schemas import QuizMaterial
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.materials import service
from education_platform.modules.materials.schemas import (
    LessonMaterial,
    MaterialProgressOut,
    MaterialProgressUpdate,
    TopicSummary,
)
from education_platform.modules.rag import service as rag_service
from education_platform.modules.rag.schemas import MaterialIngestAccepted, MaterialVersionStatusOut

router = APIRouter(tags=["materials"])


@router.post(
    "/admin/subtopics/{subtopic_id}/materials",
    response_model=MaterialIngestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_subtopic_material(
    subtopic_id: UUID,
    title: str = Form(...),
    file: UploadFile = File(...),
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> MaterialIngestAccepted:
    return await rag_service.upload_curriculum_material(
        session,
        principal,
        subtopic_id=subtopic_id,
        title=title,
        file=file,
    )


@router.get(
    "/admin/material-versions/{version_id}",
    response_model=MaterialVersionStatusOut,
)
async def get_material_version_status(
    version_id: UUID,
    principal: Principal = Depends(require_administrator),
    session: AsyncSession = Depends(get_session),
) -> MaterialVersionStatusOut:
    return await rag_service.get_material_version_status(session, principal, version_id)


@router.get("/materials", response_model=list[TopicSummary])
async def list_materials(
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> list[TopicSummary]:
    return await service.list_topics(session, scope)


@router.get("/subtopics/{subtopic_id}/material", response_model=LessonMaterial)
async def get_subtopic_material(
    subtopic_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> LessonMaterial:
    return await service.get_subtopic_lesson(session, scope, subtopic_id)


@router.get("/topics/{topic_id}/material", response_model=LessonMaterial)
async def get_topic_material(
    topic_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> LessonMaterial:
    return await service.get_topic_lesson(session, scope, topic_id)


@router.get("/subtopics/{subtopic_id}/quiz", response_model=QuizMaterial)
async def get_subtopic_quiz(
    subtopic_id: UUID,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> QuizMaterial:
    return await service.get_subtopic_quiz(session, scope, subtopic_id)


@router.put("/topics/{topic_id}/material-progress", response_model=MaterialProgressOut)
async def put_topic_material_progress(
    topic_id: UUID,
    payload: MaterialProgressUpdate,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> MaterialProgressOut:
    return await service.update_topic_material_progress(session, scope, topic_id, payload)


@router.put("/subtopics/{subtopic_id}/material-progress", response_model=MaterialProgressOut)
async def put_subtopic_material_progress(
    subtopic_id: UUID,
    payload: MaterialProgressUpdate,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> MaterialProgressOut:
    return await service.update_material_progress(session, scope, subtopic_id, payload)


@router.get("/materials/{topic_id}", response_model=LessonMaterial)
async def get_material(
    topic_id: str,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> LessonMaterial:
    return await service.get_lesson(session, scope, topic_id)


@router.get("/materials/{topic_id}/quiz", response_model=QuizMaterial)
async def get_material_quiz(
    topic_id: str,
    scope: Scope = Depends(get_scope),
    session: AsyncSession = Depends(get_session),
) -> QuizMaterial:
    return await service.get_quiz(session, scope, topic_id)
