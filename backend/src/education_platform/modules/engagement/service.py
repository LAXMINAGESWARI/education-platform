"""Business logic for slide engagement recording and summary."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from education_platform.core.errors import DomainError
from education_platform.modules.academics.models import (
    GradeSubjectOffering,
    Subject,
    Subtopic,
    Topic,
)
from education_platform.modules.authorization.scope import Scope
from education_platform.modules.engagement.models import SlideEngagement
from education_platform.modules.engagement.schemas import (
    EngagementRateOut,
    SlideEngagementIn,
    SlideEngagementOut,
    SlideEngagementSummary,
    SubtopicEngagementSummary,
)
from education_platform.modules.materials import service as materials_service
from education_platform.modules.materials.markdown_parser import parse_slides
from education_platform.modules.materials.models import (
    SourceMaterial,
    SourceMaterialVersion,
    SourceMaterialVersionStatus,
)

# A slide counts as "engaged" if the student spent at least this many seconds on it.
# Starting value (option A); task 2 layers a class-relative threshold on top.
ENGAGED_THRESHOLD_SECONDS = 5


async def record_engagement(
    session: AsyncSession,
    scope: Scope,
    subtopic_id: UUID,
    payload: SlideEngagementIn,
) -> SlideEngagementOut:
    """Insert one slide engagement row for the current student.

    Uses scope.self_student_id (student_profiles.id) — consistent with at_risk_flags.
    Returns early if the caller is not a student (e.g. admin browsing lessons).
    """
    if scope.self_student_id is None:
        return SlideEngagementOut(recorded=False)

    row = SlideEngagement(
        student_id=scope.self_student_id,
        subtopic_id=subtopic_id,
        slide_index=payload.slide_index,
        seconds_active=payload.seconds_active,
        recorded_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    return SlideEngagementOut(recorded=True)


async def get_subtopic_engagement_summary(
    session: AsyncSession,
    scope: Scope,
    subtopic_id: UUID,
) -> SubtopicEngagementSummary:
    """Return per-slide engagement totals for this student on a subtopic."""
    if scope.self_student_id is None:
        return SubtopicEngagementSummary(subtopic_id=subtopic_id, slides=[])

    rows = (
        await session.execute(
            select(
                SlideEngagement.slide_index,
                func.sum(SlideEngagement.seconds_active).label("total_seconds"),
            )
            .where(
                SlideEngagement.student_id == scope.self_student_id,
                SlideEngagement.subtopic_id == subtopic_id,
            )
            .group_by(SlideEngagement.slide_index)
            .order_by(SlideEngagement.slide_index)
        )
    ).all()

    return SubtopicEngagementSummary(
        subtopic_id=subtopic_id,
        slides=[
            SlideEngagementSummary(
                slide_index=row.slide_index,
                total_seconds=int(row.total_seconds),
            )
            for row in rows
        ],
    )


async def get_subtopic_engagement_rate(
    session: AsyncSession,
    scope: Scope,
    subtopic_id: UUID,
) -> EngagementRateOut:
    """Compute engagement rate for this student on a subtopic.

    engaged_slides = distinct slides where total active time >= threshold
    total_slides   = number of slides in the published lesson
    rate           = engaged_slides / total_slides  (None if no slides)
    """
    empty = EngagementRateOut(
        subtopic_id=subtopic_id, engaged_slides=0, total_slides=0, engagement_rate=None
    )
    if scope.self_student_id is None:
        return empty

    # Denominator: total slides in the lesson (reuse the materials service).
    lesson = await materials_service.get_subtopic_lesson(session, scope, subtopic_id)
    total_slides = len(lesson.slides)
    if total_slides == 0:
        return empty

    # Numerator: distinct slides the student spent >= threshold seconds on.
    engaged_rows = (
        await session.execute(
            select(SlideEngagement.slide_index)
            .where(
                SlideEngagement.student_id == scope.self_student_id,
                SlideEngagement.subtopic_id == subtopic_id,
            )
            .group_by(SlideEngagement.slide_index)
            .having(func.sum(SlideEngagement.seconds_active) >= ENGAGED_THRESHOLD_SECONDS)
        )
    ).all()
    engaged_slides = len(engaged_rows)

    return EngagementRateOut(
        subtopic_id=subtopic_id,
        engaged_slides=engaged_slides,
        total_slides=total_slides,
        engagement_rate=round(engaged_slides / total_slides, 3),
    )


async def get_subject_engagement_rates(
    session: AsyncSession,
    scope: Scope,
    subject_id: UUID,
) -> list[EngagementRateOut]:
    """Engagement rates for every published lesson in a subject, in a few queries.

    The feedback page used to request one rate per subtopic, and each of those
    loaded the full lesson. This returns the same per-subtopic shape together.
    """
    if scope.self_student_id is None:
        raise DomainError("Student enrollment required", status_code=403)

    subject = await session.get(Subject, subject_id)
    if subject is None:
        raise DomainError("Subject not found", status_code=404)
    offering_ids = list(
        await session.scalars(
            select(GradeSubjectOffering.id).where(GradeSubjectOffering.subject_id == subject_id)
        )
    )
    covered = [
        offering_id
        for offering_id in offering_ids
        if scope.covers_offering(offering_id, institution_id=scope.institution_id)
    ]
    if not covered:
        raise DomainError("Subject not found", status_code=404)

    subtopic_ids = list(
        await session.scalars(
            select(Subtopic.id)
            .join(Topic, Topic.id == Subtopic.topic_id)
            .where(Topic.grade_subject_offering_id.in_(covered))
        )
    )
    if not subtopic_ids:
        return []

    material_rows = (
        await session.execute(
            select(SourceMaterial.subtopic_id, SourceMaterialVersion.content_markdown)
            .join(SourceMaterial, SourceMaterial.id == SourceMaterialVersion.source_material_id)
            .where(
                SourceMaterial.subtopic_id.in_(subtopic_ids),
                SourceMaterialVersion.lifecycle_status == SourceMaterialVersionStatus.PUBLISHED,
                SourceMaterialVersion.content_markdown.is_not(None),
            )
            .order_by(SourceMaterialVersion.version_number.desc())
        )
    ).all()
    slide_counts: dict[UUID, int] = {}
    for subtopic_id, markdown in material_rows:
        if subtopic_id is None or subtopic_id in slide_counts or not markdown:
            continue
        slide_counts[subtopic_id] = len(parse_slides(markdown))

    engaged_counts: dict[UUID, int] = {}
    if slide_counts:
        engaged_rows = (
            await session.execute(
                select(SlideEngagement.subtopic_id, SlideEngagement.slide_index)
                .where(
                    SlideEngagement.student_id == scope.self_student_id,
                    SlideEngagement.subtopic_id.in_(list(slide_counts)),
                )
                .group_by(SlideEngagement.subtopic_id, SlideEngagement.slide_index)
                .having(func.sum(SlideEngagement.seconds_active) >= ENGAGED_THRESHOLD_SECONDS)
            )
        ).all()
        for subtopic_id, _slide_index in engaged_rows:
            engaged_counts[subtopic_id] = engaged_counts.get(subtopic_id, 0) + 1

    rates: list[EngagementRateOut] = []
    for subtopic_id, total_slides in slide_counts.items():
        engaged_slides = engaged_counts.get(subtopic_id, 0)
        rates.append(
            EngagementRateOut(
                subtopic_id=subtopic_id,
                engaged_slides=engaged_slides,
                total_slides=total_slides,
                engagement_rate=(round(engaged_slides / total_slides, 3) if total_slides else None),
            )
        )
    return rates
