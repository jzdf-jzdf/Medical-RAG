import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    doi: str | None = Field(default=None, max_length=255)
    authors: list[dict | str] = []
    abstract: str | None = None
    publication_year: int | None = Field(default=None, ge=1600, le=2200)
    metadata: dict = {}


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    doi: str | None = Field(default=None, max_length=255)
    authors: list[dict | str] | None = None
    abstract: str | None = None
    publication_year: int | None = Field(default=None, ge=1600, le=2200)
    metadata: dict | None = None


class DocumentFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_name: str
    media_type: str
    size_bytes: int
    created_at: datetime


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    doi: str | None
    authors: list
    abstract: str | None
    publication_year: int | None
    metadata_json: dict = Field(serialization_alias="metadata")
    files: list[DocumentFileRead] = []
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentRead]
    total: int
    page: int
    page_size: int


class AttachmentMove(BaseModel):
    target_document_id: uuid.UUID


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class CollectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
