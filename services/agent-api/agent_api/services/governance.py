"""Durable daily quotas and append-only audit recording."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.models import AuditEvent, UsageQuota


async def consume_quota(
    db: AsyncSession, user_id: str, daily_limit: int
) -> UsageQuota:
    today = datetime.now(UTC).date().isoformat()
    item = await db.scalar(
        select(UsageQuota).where(UsageQuota.user_id == user_id).with_for_update()
    )
    if item is None:
        item = UsageQuota(
            user_id=user_id,
            daily_limit=daily_limit,
            used_count=0,
            usage_date=today,
        )
        db.add(item)
        await db.flush()
    if item.usage_date != today:
        item.usage_date = today
        item.used_count = 0
    item.daily_limit = daily_limit
    if item.used_count >= item.daily_limit:
        raise HTTPException(status_code=429, detail="今日 Agent 调用配额已用完")
    item.used_count += 1
    return item


def record_audit(
    db: AsyncSession,
    *,
    user_id: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    detail: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail or {},
        )
    )
