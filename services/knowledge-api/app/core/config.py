from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings for API, storage, MinerU, and local Worker."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8001, ge=1, le=65535)
    log_level: str = "INFO"
    database_url: PostgresDsn
    allowed_origins: list[str] = ["http://localhost:5173"]
    workspace_root: Path = Path("./runtime")
    max_upload_mb: int = Field(default=100, ge=1, le=1024)
    # External PDF parser configuration. The command may be an executable name
    # on PATH or the absolute path to the project-local MinerU environment.
    parser_provider: str = "mineru"
    mineru_command: str = "mineru-kit"
    mineru_tier: str = "standard"
    mineru_timeout_seconds: int = Field(default=1800, ge=30, le=7200)
    # Worker timing is intentionally configurable for slow local models and for
    # fast deterministic tests. stale_seconds must comfortably exceed heartbeat.
    task_worker_enabled: bool = True
    task_worker_poll_seconds: float = Field(default=1.0, ge=0.2, le=30)
    task_heartbeat_seconds: float = Field(default=5.0, ge=1, le=60)
    task_stale_seconds: int = Field(default=60, ge=15, le=3600)
    qdrant_url: AnyHttpUrl = "http://localhost:6333"
    qdrant_collection: str = "medical_evidence"
    qdrant_timeout_seconds: int = Field(default=30, ge=1, le=300)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    reranker_model: str = ""
    reranker_device: str = "auto"
    reranker_max_length: int = Field(default=512, ge=64, le=4096)
    embedding_model: str = ""
    embedding_api_key: str = ""
    embedding_timeout_seconds: int = Field(default=60, ge=5, le=600)
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    search_candidate_limit: int = Field(default=5000, ge=100, le=100000)
    medcpt_cache_dir: Path = Path("./runtime/models/medcpt")
    medcpt_query_model: str = "ncbi/MedCPT-Query-Encoder"
    medcpt_article_model: str = "ncbi/MedCPT-Article-Encoder"
    medcpt_device: str = "auto"
    medcpt_batch_size: int = Field(default=8, ge=1, le=128)
    # Optional static API-key authorization. API_KEYS_JSON maps each key to
    # {"user_id": "...", "role": "viewer|reviewer|editor|admin"}.
    auth_enabled: bool = False
    api_keys_json: str = "{}"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        """Accept comma-separated .env syntax as well as native JSON lists."""

        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Construct settings once so every service observes one configuration."""

    return Settings()
