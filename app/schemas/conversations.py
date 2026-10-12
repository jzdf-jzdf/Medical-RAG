import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=500)
    user_id: str = Field(min_length=1, max_length=255)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    user_id: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead]


class ConversationList(BaseModel):
    items: list[ConversationRead]
    total: int
    page: int
    page_size: int
