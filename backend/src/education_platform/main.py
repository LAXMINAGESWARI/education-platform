import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from education_platform.core.config import get_settings
from education_platform.core.errors import DomainError
from education_platform.core.health import readiness_response
from education_platform.db import models as _models
from education_platform.db.url import to_sync_url
from education_platform.modules.academics.router import router as academics_router
from education_platform.modules.assessments.router import router as assessments_router
from education_platform.modules.assistant.router import router as assistant_router
from education_platform.modules.at_risk.router import router as at_risk_router
from education_platform.modules.audit.router import router as audit_router
from education_platform.modules.auth.router import router as auth_router
from education_platform.modules.authoring.router import router as authoring_router
from education_platform.modules.engagement.router import router as engagement_router
from education_platform.modules.generation.router import router as generation_router
from education_platform.modules.insights.router import router as insights_router
from education_platform.modules.judge.policy import warn_judge_mode_at_startup
from education_platform.modules.materials.router import router as materials_router
from education_platform.modules.materials.seed import seed_approved_materials
from education_platform.modules.progress.router import router as progress_router
from education_platform.modules.rag.router import router as rag_router
from education_platform.modules.self_evaluation.router import router as self_evaluation_router
from education_platform.modules.text_to_sql.router import router as text_to_sql_router

_ = _models

logger = logging.getLogger(__name__)


def _seed_if_empty() -> None:
    settings = get_settings()
    engine = create_engine(to_sync_url(settings.database_url), pool_pre_ping=True)
    try:
        with Session(engine) as session:
            seed_approved_materials(session, replace=False)
    finally:
        engine.dispose()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    warn_judge_mode_at_startup()
    try:
        _seed_if_empty()
    except Exception:
        logger.exception("Startup seeding failed; continuing")
    yield


def cors_origin_regex(origin_regex: str) -> str | None:
    """Return a Starlette origin regex, or None when the setting is blank."""
    stripped = origin_regex.strip()
    if not stripped:
        return None
    return stripped


settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=cors_origin_regex(settings.cors_origin_regex),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(academics_router, prefix=settings.api_v1_prefix)
app.include_router(materials_router, prefix=settings.api_v1_prefix)
app.include_router(rag_router, prefix=settings.api_v1_prefix)
app.include_router(assistant_router, prefix=settings.api_v1_prefix)
app.include_router(assessments_router, prefix=settings.api_v1_prefix)
app.include_router(audit_router, prefix=settings.api_v1_prefix)
app.include_router(insights_router, prefix=settings.api_v1_prefix)
app.include_router(authoring_router, prefix=settings.api_v1_prefix)
app.include_router(text_to_sql_router, prefix=settings.api_v1_prefix)
app.include_router(generation_router, prefix=settings.api_v1_prefix)
app.include_router(progress_router, prefix=settings.api_v1_prefix)
app.include_router(at_risk_router, prefix=settings.api_v1_prefix)

app.include_router(engagement_router, prefix=settings.api_v1_prefix)
app.include_router(self_evaluation_router, prefix=settings.api_v1_prefix)


@app.exception_handler(DomainError)
async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health/live", tags=["system"])
async def health_live() -> dict[str, str]:
    """Process is up. No database call, so a DB blip does not fail liveness."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
async def health_ready() -> JSONResponse:
    """Postgres answers SELECT 1 within the configured timeout."""
    return await readiness_response()


@app.get("/health", tags=["system"])
async def health() -> JSONResponse:
    """Same check as /health/ready. Compose and autodeploy call this path."""
    return await readiness_response()
