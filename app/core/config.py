from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8001, ge=1, le=65535)
    log_level: str = "INFO"
    database_url: PostgresDsn
    allowed_origins: list[str] = ["http://localhost:5173"]
    workspace_root: Path = Path("./runtime")
    max_upload_mb: int = Field(default=100, ge=1, le=1024)
    parser_provider: str = "mineru"
    qdrant_url: AnyHttpUrl = "http://localhost:6333"
    qdrant_collection: str = "medical_evidence"
    embedding_model: str = ""

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
