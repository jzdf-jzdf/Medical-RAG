import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPServerSettings(BaseModel):
    name: str
    transport: Literal["stdio", "sse", "streamable-http"]
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = Field(default_factory=dict)
    url: HttpUrl | None = None
    enabled: bool = True
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    allowed_tools: list[str] = []
    source_type: str = "external"
    trust_level: Literal["reviewed", "trusted", "external"] = "external"

    @model_validator(mode="after")
    def validate_transport_target(self):
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP server requires command")
        if self.transport != "stdio" and not self.url:
            raise ValueError("remote MCP server requires url")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8002, ge=1, le=65535)
    log_level: str = "INFO"
    database_url: PostgresDsn
    allowed_origins: list[str] = ["http://localhost:5174"]
    model_provider: str
    model_name: str
    model_base_url: HttpUrl
    model_api_key: SecretStr = SecretStr("")
    model_max_tokens: int = Field(default=4096, ge=1)
    model_temperature: float = Field(default=0.2, ge=0, le=2)
    model_encryption_key: str = ""
    harness_provider: str = "deepseek-official"
    dsh_home: Path = Path("./runtime/dsh_home")
    model_profiles_json: dict = {}
    default_model_profile: str = "default"
    mcp_servers_json: list[MCPServerSettings] = []
    max_history_turns: int = Field(default=20, ge=1, le=200)
    conversation_ttl_hours: int = Field(default=24, ge=1)
    workspace_root: Path = Path("./runtime")
    request_timeout_seconds: int = Field(default=120, ge=1, le=600)
    max_tool_rounds: int = Field(default=8, ge=1, le=10)
    default_knowledge_base_id: str = ""
    retrieval_top_k: int = Field(default=8, ge=1, le=50)
    daily_request_quota: int = Field(default=100, ge=1, le=100000)
    auth_enabled: bool = False
    api_keys_json: dict = {}

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        return (
            [item.strip() for item in value.split(",") if item.strip()]
            if isinstance(value, str)
            else value
        )

    @field_validator("mcp_servers_json", "model_profiles_json", "api_keys_json", mode="before")
    @classmethod
    def parse_json(cls, value: object) -> object:
        return json.loads(value) if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
