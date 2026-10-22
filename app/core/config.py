from functools import lru_cache

from pydantic import Field, HttpUrl, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8003, ge=1, le=65535)
    database_url: PostgresDsn
    redis_url: str
    knowledge_api_url: HttpUrl
    agent_api_url: HttpUrl
    allowed_origins: list[str] = ["http://localhost:5175"]
    request_timeout_seconds: int = Field(default=120, ge=1, le=600)
    evaluation_user_id: str = Field(default="evaluation-workbench", min_length=1)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        return (
            [item.strip() for item in value.split(",") if item.strip()]
            if isinstance(value, str)
            else value
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
