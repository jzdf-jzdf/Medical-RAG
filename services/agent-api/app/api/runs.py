"""Auditable Agent run details for trace viewers and evaluation links."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import enforce_user
from app.db.session import get_db
from app.models import AgentRun, Conversation

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("/{run_id}/trace")
async def get_run_trace(
    run_id: uuid.UUID,
    request: Request,
    user_id: str = Header(alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    enforce_user(request, user_id)
    row = await db.execute(
        select(AgentRun, Conversation)
        .join(Conversation, Conversation.id == AgentRun.conversation_id)
        .where(AgentRun.id == run_id)
    )
    result = row.first()
    if result is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    run, conversation = result
    if conversation.user_id != user_id:
        raise HTTPException(status_code=403, detail="Agent run access denied")
    return {
        "run_id": str(run.id),
        "status": run.status,
        "model": run.model_name,
        "provider": run.model_provider,
        "degraded_without_evidence": run.degraded_without_evidence,
        "trace": run.trace,
        "verification": run.verification,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "links": {
            "agent": f"/agent/?run_id={run.id}",
            "evaluation": f"/evaluation/?run_id={run.id}",
        },
    }
