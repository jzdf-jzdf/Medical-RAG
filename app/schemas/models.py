import uuid

from pydantic import BaseModel, ConfigDict, Field


class ModelConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(default="deepseek-official", min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=255)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = None
    max_tokens: int = Field(default=4096, ge=1, le=131072)
    temperature: float = Field(default=0.2, ge=0, le=2)
    is_default: bool = False


class ModelConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    provider: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    api_key: str | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=131072)
    temperature: float | None = Field(default=None, ge=0, le=2)
    is_default: bool | None = None
    is_active: bool | None = None


class ModelConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    provider: str
    model: str
    base_url: str
    max_tokens: int
    temperature: float
    is_default: bool
    is_active: bool
    has_api_key: bool
