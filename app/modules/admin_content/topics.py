import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.content.importer import slugify
from app.core import audit
from app.models import ContentStatus, DebateTopic
from app.modules.admin_auth.deps import CurrentAdmin, DbSession, client_ip, require_admin_header
from app.modules.admin_content.common import ContentEditor, apply_changes, conflict, not_found

router = APIRouter(
    prefix="/admin/api/topics", tags=["admin topics"], dependencies=[Depends(require_admin_header)]
)

Category = Literal["education", "technology", "environment", "society", "politics", "economy"]
Points = Annotated[list[Annotated[str, Field(min_length=3, max_length=240)]], Field(max_length=6)]


class TopicFields(BaseModel):
    title: str = Field(min_length=8, max_length=300)
    category: Category
    context: str = Field("", max_length=400)
    for_points: Points = Field(default_factory=list)
    against_points: Points = Field(default_factory=list)
    status: ContentStatus = ContentStatus.DRAFT


class TopicPatch(BaseModel):
    title: str | None = Field(None, min_length=8, max_length=300)
    category: Category | None = None
    context: str | None = Field(None, max_length=400)
    for_points: Points | None = None
    against_points: Points | None = None
    status: ContentStatus | None = None


class TopicOut(TopicFields):
    id: uuid.UUID
    slug: str
    sort_order: int
    updated_at: datetime


class BulkStatusIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    status: ContentStatus


def _out(topic: DebateTopic) -> TopicOut:
    return TopicOut.model_validate({name: getattr(topic, name) for name in TopicOut.model_fields})


def _publishable(topic: DebateTopic) -> None:
    """The AI opponent needs points to argue from on both sides."""
    if topic.status == ContentStatus.PUBLISHED and (
        len(topic.for_points) < 2 or len(topic.against_points) < 2
    ):
        raise conflict(
            f"“{topic.title}” needs at least two For and two Against points before it can be published."
        )


async def _get(session: DbSession, topic_id: uuid.UUID) -> DebateTopic:
    topic = await session.get(DebateTopic, topic_id)
    if topic is None:
        raise not_found("Topic")
    return topic


@router.get("")
async def list_topics(
    _: CurrentAdmin,
    session: DbSession,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Category | None = None,
    status_filter: Annotated[ContentStatus | None, Query(alias="status")] = None,
) -> list[TopicOut]:
    stmt = select(DebateTopic).order_by(DebateTopic.sort_order, DebateTopic.title)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(DebateTopic.title.ilike(like), DebateTopic.context.ilike(like)))
    if category:
        stmt = stmt.where(DebateTopic.category == category)
    if status_filter:
        stmt = stmt.where(DebateTopic.status == status_filter)
    return [_out(topic) for topic in await session.scalars(stmt)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_topic(
    body: TopicFields, admin: ContentEditor, request: Request, session: DbSession
) -> TopicOut:
    base = slugify(body.title) or "topic"
    slug, suffix = base, 2
    while await session.scalar(select(DebateTopic.id).where(DebateTopic.slug == slug)):
        slug, suffix = f"{base[:56]}-{suffix}", suffix + 1
    last = await session.scalar(select(func.max(DebateTopic.sort_order))) or 0
    topic = DebateTopic(slug=slug, sort_order=last + 1, **body.model_dump())
    _publishable(topic)
    session.add(topic)
    await session.flush()
    audit.record(
        session,
        "topic.created",
        admin_id=admin.id,
        target_type="topic",
        target_id=str(topic.id),
        ip=client_ip(request),
        title=topic.title,
    )
    await session.commit()
    return _out(topic)


@router.patch("/{topic_id}")
async def update_topic(
    topic_id: uuid.UUID, body: TopicPatch, admin: ContentEditor, request: Request, session: DbSession
) -> TopicOut:
    topic = await _get(session, topic_id)
    changes = apply_changes(topic, body.model_dump(exclude_unset=True))
    _publishable(topic)
    if changes:
        audit.record(
            session,
            "topic.updated",
            admin_id=admin.id,
            target_type="topic",
            target_id=str(topic.id),
            ip=client_ip(request),
            title=topic.title,
            changes=changes,
        )
        await session.commit()
    return _out(topic)


@router.delete("/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic(
    topic_id: uuid.UUID, admin: ContentEditor, request: Request, session: DbSession
) -> None:
    topic = await _get(session, topic_id)
    if topic.status == ContentStatus.PUBLISHED:
        raise conflict("Published topics can’t be deleted. Archive it first so students stop seeing it.")
    await session.delete(topic)
    audit.record(
        session,
        "topic.deleted",
        admin_id=admin.id,
        target_type="topic",
        target_id=str(topic_id),
        ip=client_ip(request),
        title=topic.title,
    )
    await session.commit()


@router.post("/bulk-status")
async def bulk_status(
    body: BulkStatusIn, admin: ContentEditor, request: Request, session: DbSession
) -> dict[str, int]:
    topics = list(
        await session.scalars(
            select(DebateTopic).where(DebateTopic.id.in_(body.ids), DebateTopic.status != body.status)
        )
    )
    for topic in topics:
        topic.status = body.status
        _publishable(topic)
    if topics:
        audit.record(
            session,
            "topic.bulk_status",
            admin_id=admin.id,
            target_type="topic",
            ip=client_ip(request),
            status=body.status,
            count=len(topics),
        )
        await session.commit()
    return {"updated": len(topics)}
