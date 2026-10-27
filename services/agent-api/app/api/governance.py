"""Self-service quota and audit endpoints."""

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import enforce_user
from app.db.session import get_db
from app.models import AuditEvent, UsageQuota

router = APIRouter(prefix="/governance", tags=["governance"])


@router.get("/usage")
async def usage(
    request: Request,
    user_id: str = Header(alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    enforce_user(request, user_id)
    item = await db.get(UsageQuota, user_id)
    if item is None:
        return {"user_id": user_id, "used_count": 0, "daily_limit": None, "usage_date": None}
    return {
        "user_id": item.user_id,
        "used_count": item.used_count,
        "daily_limit": item.daily_limit,
        "remaining": max(0, item.daily_limit - item.used_count),
        "usage_date": item.usage_date,
    }


@router.get("/audit-events")
async def audit_events(
    request: Request,
    user_id: str = Header(alias="X-User-ID"),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    principal = enforce_user(request, user_id)
    query = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
    if principal.role != "admin":
        query = query.where(AuditEvent.user_id == user_id)
    rows = list((await db.scalars(query)).all())
    return [
        {
            "id": str(item.id),
            "user_id": item.user_id,
            "action": item.action,
            "resource_type": item.resource_type,
            "resource_id": item.resource_id,
            "detail": item.detail,
            "created_at": item.created_at,
        }
        for item in rows
    ]
