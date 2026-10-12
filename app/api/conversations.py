import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models import Conversation
from app.schemas.conversations import (
    ConversationCreate,
    ConversationDetail,
    ConversationList,
    ConversationRead,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate, db: AsyncSession = Depends(get_db)
) -> Conversation:
    conversation = Conversation(**payload.model_dump())
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.get("", response_model=ConversationList)
async def list_conversations(
    user_id: str = Header(alias="X-User-ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ConversationList:
    predicate = Conversation.user_id == user_id
    total = int(
        (await db.scalar(select(func.count()).select_from(Conversation).where(predicate))) or 0
    )
    items = list(
        (
            await db.scalars(
                select(Conversation)
                .where(predicate)
                .order_by(Conversation.updated_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return ConversationList(items=items, total=total, page=page, page_size=page_size)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    user_id: str = Header(alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.user_id != user_id:
        raise HTTPException(status_code=403, detail="Conversation access denied")
    return conversation
