"""FastAPI boundary for the guideline chunking pipeline."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import __version__
from .config import PipelineConfig, load_pipeline_config
from .database import (
    PersistencePayload,
    PostgresResultRepository,
    ResultRepository,
)
from .deepseek import DeepSeekSemanticClient, SemanticClient
from .models import RunStatus
from .pipeline import run_inputs
from .storage import load_audit, load_chunks, load_manifest, load_processing_report
from .taxonomy import load_taxonomy


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "hich_v1.yaml"
DEFAULT_DATA_ROOT = PROJECT_ROOT / "api_data"
ARTIFACT_NAMES = {
    "chunks.jsonl",
    "manifest.json",
    "audit.jsonl",
    "processing_report.json",
}
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """Server-owned paths and resource limits."""

    config_path: Path = DEFAULT_CONFIG_PATH
    data_root: Path = DEFAULT_DATA_ROOT
    max_upload_bytes: int = 20 * 1024 * 1024
    max_concurrent_jobs: int = 1
    database_url: str | None = None
    database_auto_create_schema: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "config_path", Path(self.config_path).resolve())
        object.__setattr__(self, "data_root", Path(self.data_root).resolve())
        if self.max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be positive")
        if self.max_concurrent_jobs < 1:
            raise ValueError("max_concurrent_jobs must be positive")

    @classmethod
    def from_env(cls) -> "ApiSettings":
        """Build settings without accepting config paths from HTTP callers."""

        return cls(
            config_path=Path(
                os.getenv("GUIDELINE_CHUNKER_CONFIG", str(DEFAULT_CONFIG_PATH))
            ),
            data_root=Path(
                os.getenv("GUIDELINE_CHUNKER_DATA_ROOT", str(DEFAULT_DATA_ROOT))
            ),
            max_upload_bytes=_positive_env_int(
                "GUIDELINE_CHUNKER_MAX_UPLOAD_BYTES",
                20 * 1024 * 1024,
            ),
            max_concurrent_jobs=_positive_env_int(
                "GUIDELINE_CHUNKER_MAX_CONCURRENT_JOBS",
                1,
            ),
            database_url=(
                os.getenv("GUIDELINE_CHUNKER_DATABASE_URL") or None
            ),
            database_auto_create_schema=_env_bool(
                "GUIDELINE_CHUNKER_DATABASE_AUTO_CREATE_SCHEMA",
                True,
            ),
        )


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "not_ready"]
    version: str
    config_ready: bool
    taxonomy_ready: bool
    deepseek_api_key_configured: bool
    database_configured: bool
    max_upload_bytes: int = Field(ge=1)
    max_concurrent_jobs: int = Field(ge=1)


class DatabasePersistenceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    status: Literal["disabled", "persisted", "skipped_dry_run"]
    document_id: int | None = None
    chunks_count: int = Field(default=0, ge=0)


class ProcessResponse(BaseModel):
    """Stable payload consumed by the RAG application."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    source_doc: str
    status: RunStatus
    chunks: list[dict[str, Any]]
    manifest: dict[str, Any]
    processing_report: dict[str, Any]
    audit: list[dict[str, Any]] | None = None
    source_url: str
    artifact_urls: dict[str, str]
    database: DatabasePersistenceState


@dataclass(frozen=True, slots=True)
class ProcessedLocalRun:
    response: ProcessResponse
    persistence_payload: PersistencePayload


class ProcessingRequestError(ValueError):
    """A request-specific failure whose workspace is retained for diagnosis."""

    def __init__(self, request_id: str, message: str):
        super().__init__(message)
        self.request_id = request_id


SemanticClientFactory = Callable[[PipelineConfig], SemanticClient]


def _positive_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _default_semantic_client(config: PipelineConfig) -> SemanticClient:
    return DeepSeekSemanticClient(config, load_taxonomy(config.taxonomy.path))


def _safe_markdown_filename(filename: str | None) -> str:
    original = Path(filename or "").name
    if not original:
        raise ValueError("上传文件缺少文件名")
    if Path(original).suffix.lower() != ".md":
        raise ValueError("仅支持 UTF-8 Markdown 文件（.md）")

    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(original).stem).strip(" .")
    stem = stem[:120] or "guide"
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    return f"{stem}.md"


async def _read_upload(upload: UploadFile, limit: int) -> bytes:
    payload = bytearray()
    try:
        while block := await upload.read(1024 * 1024):
            payload.extend(block)
            if len(payload) > limit:
                raise ValueError(f"上传文件超过大小限制（最大 {limit} 字节）")
    finally:
        await upload.close()

    if not payload:
        raise ValueError("上传文件为空")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValueError("Markdown 必须使用 UTF-8 且不能包含 BOM")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Markdown 必须使用有效的 UTF-8 编码") from error
    return bytes(payload)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class GuidelineChunkingService:
    """Own request workspaces and delegate all chunking to the existing pipeline."""

    def __init__(
        self,
        settings: ApiSettings,
        *,
        semantic_client_factory: SemanticClientFactory = _default_semantic_client,
    ):
        self.settings = settings
        self.semantic_client_factory = semantic_client_factory

    def health(self) -> HealthResponse:
        config_ready = self.settings.config_path.is_file()
        taxonomy_ready = False
        api_key_name = "DEEPSEEK_API_KEY"
        if config_ready:
            try:
                config = load_pipeline_config(self.settings.config_path)
                load_taxonomy(config.taxonomy.path)
                taxonomy_ready = True
                api_key_name = config.deepseek.api_key_env
            except Exception:
                logger.exception("API readiness configuration validation failed")
        return HealthResponse(
            status="ok" if config_ready and taxonomy_ready else "not_ready",
            version=__version__,
            config_ready=config_ready,
            taxonomy_ready=taxonomy_ready,
            deepseek_api_key_configured=bool(os.getenv(api_key_name)),
            database_configured=bool(self.settings.database_url),
            max_upload_bytes=self.settings.max_upload_bytes,
            max_concurrent_jobs=self.settings.max_concurrent_jobs,
        )

    def process(
        self,
        *,
        filename: str,
        payload: bytes,
        dry_run: bool,
        force: bool,
        no_cache: bool,
        workers: int | None,
        include_audit: bool,
    ) -> ProcessedLocalRun:
        request_id = uuid4().hex
        request_root = self.settings.data_root / "runs" / request_id
        input_path = request_root / "input" / filename
        output_root = request_root / "output"
        try:
            _atomic_write_bytes(input_path, payload)
        except Exception as error:
            raise ProcessingRequestError(request_id, "无法保存上传文件") from error

        try:
            config = load_pipeline_config(self.settings.config_path)
            config = config.model_copy(deep=True)
            if no_cache:
                config.cache.enabled = False
            elif config.cache.enabled:
                config.cache.directory = self.settings.data_root / ".cache"
            if workers is not None:
                if workers < 1:
                    raise ValueError("workers must be positive")
                config.deepseek.concurrent_requests = workers

            semantic_client = None
            if not dry_run:
                semantic_client = self.semantic_client_factory(config)
            batch_result = run_inputs(
                input_path,
                output_root,
                config,
                semantic_client,
                dry_run=dry_run,
                force=force,
            )
            result = batch_result.documents[0]
            if result.status is RunStatus.FAILED or result.output_paths is None:
                raise ProcessingRequestError(
                    request_id,
                    result.error or "指南处理失败",
                )

            paths = result.output_paths
            chunks = [
                chunk.model_dump(mode="json", exclude_none=True)
                for chunk in load_chunks(paths.chunks)
            ]
            manifest = load_manifest(paths.manifest).model_dump(
                mode="json",
                exclude_none=True,
            )
            report = load_processing_report(paths.processing_report).model_dump(
                mode="json",
                exclude_none=True,
            )
            audit = None
            if include_audit:
                audit = [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in load_audit(paths.audit)
                ]
            base_url = f"/api/v1/runs/{request_id}/artifacts"
            response = ProcessResponse(
                request_id=request_id,
                source_doc=result.source_doc,
                status=result.status,
                chunks=chunks,
                manifest=manifest,
                processing_report=report,
                audit=audit,
                source_url=f"/api/v1/runs/{request_id}/source",
                artifact_urls={
                    name: f"{base_url}/{name}" for name in sorted(ARTIFACT_NAMES)
                },
                database=DatabasePersistenceState(
                    enabled=False,
                    status="disabled",
                ),
            )
            return ProcessedLocalRun(
                response=response,
                persistence_payload=PersistencePayload(
                    request_id=UUID(hex=request_id),
                    source_path=input_path,
                    output_dir=result.output_dir,
                    manifest=manifest,
                    processing_report=report,
                    chunks=chunks,
                ),
            )
        except ProcessingRequestError:
            raise
        except Exception as error:
            raise ProcessingRequestError(request_id, str(error)) from error

    def artifact_path(self, request_id: UUID, artifact_name: str) -> Path:
        if artifact_name not in ARTIFACT_NAMES:
            raise FileNotFoundError(artifact_name)
        output_root = self.settings.data_root / "runs" / request_id.hex / "output"
        matches = list(output_root.glob(f"*/{artifact_name}"))
        if len(matches) != 1 or not matches[0].is_file():
            raise FileNotFoundError(artifact_name)
        return matches[0]

    def source_path(self, request_id: UUID) -> Path:
        input_root = self.settings.data_root / "runs" / request_id.hex / "input"
        matches = list(input_root.glob("*.md"))
        if len(matches) != 1 or not matches[0].is_file():
            raise FileNotFoundError(request_id.hex)
        return matches[0]

    def persistence_payload(self, request_id: UUID) -> PersistencePayload:
        source_path = self.source_path(request_id)
        output_root = self.settings.data_root / "runs" / request_id.hex / "output"
        manifests = list(output_root.glob("*/manifest.json"))
        if len(manifests) != 1:
            raise FileNotFoundError(request_id.hex)
        output_dir = manifests[0].parent
        manifest = load_manifest(manifests[0]).model_dump(
            mode="json",
            exclude_none=True,
        )
        report = load_processing_report(
            output_dir / "processing_report.json"
        ).model_dump(mode="json", exclude_none=True)
        chunks = [
            chunk.model_dump(mode="json", exclude_none=True)
            for chunk in load_chunks(output_dir / "chunks.jsonl")
        ]
        return PersistencePayload(
            request_id=request_id,
            source_path=source_path,
            output_dir=output_dir,
            manifest=manifest,
            processing_report=report,
            chunks=chunks,
        )


def create_app(
    settings: ApiSettings | None = None,
    *,
    service: GuidelineChunkingService | None = None,
    repository: ResultRepository | None = None,
) -> FastAPI:
    settings = settings or ApiSettings.from_env()
    service = service or GuidelineChunkingService(settings)
    if repository is None and settings.database_url:
        repository = PostgresResultRepository(
            settings.database_url,
            auto_create_schema=settings.database_auto_create_schema,
        )

    application = FastAPI(
        title="Guideline Chunker API",
        version=__version__,
        description="上传规范化 Markdown，并返回可审计的医学指南知识块。",
    )
    application.state.chunking_service = service
    application.state.result_repository = repository
    application.state.processing_slots = asyncio.Semaphore(
        settings.max_concurrent_jobs
    )

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return service.health().model_copy(
            update={"database_configured": repository is not None}
        )

    @application.post("/api/v1/process", response_model=ProcessResponse)
    async def process_markdown(
        request: Request,
        file: Annotated[UploadFile, File(description="UTF-8、无 BOM 的 Markdown")],
        dry_run: Annotated[bool, Form()] = False,
        force: Annotated[bool, Form()] = False,
        no_cache: Annotated[bool, Form()] = False,
        workers: Annotated[int | None, Form(ge=1)] = None,
        include_audit: Annotated[bool, Form()] = False,
    ) -> ProcessResponse:
        try:
            filename = _safe_markdown_filename(file.filename)
            payload = await _read_upload(file, settings.max_upload_bytes)
            async with request.app.state.processing_slots:
                local_run = await run_in_threadpool(
                    service.process,
                    filename=filename,
                    payload=payload,
                    dry_run=dry_run,
                    force=force,
                    no_cache=no_cache,
                    workers=workers,
                    include_audit=include_audit,
                )
        except ProcessingRequestError as error:
            logger.warning(
                "Guideline processing request failed: request_id=%s",
                error.request_id,
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(error),
                    "request_id": error.request_id,
                },
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        if repository is None:
            return local_run.response
        if local_run.response.status is RunStatus.DRY_RUN_COMPLETED:
            return local_run.response.model_copy(
                update={
                    "database": DatabasePersistenceState(
                        enabled=True,
                        status="skipped_dry_run",
                    )
                }
            )

        try:
            persisted = await repository.persist(local_run.persistence_payload)
        except Exception as error:
            logger.exception(
                "PostgreSQL persistence failed after local save: request_id=%s",
                local_run.response.request_id,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "PostgreSQL 保存失败，本地文件已保留",
                    "request_id": local_run.response.request_id,
                    "local_saved": True,
                    "source_url": local_run.response.source_url,
                    "artifact_urls": local_run.response.artifact_urls,
                    "retry_url": (
                        f"/api/v1/runs/{local_run.response.request_id}/persist"
                    ),
                },
            ) from error

        return local_run.response.model_copy(
            update={
                "database": DatabasePersistenceState(
                    enabled=True,
                    status="persisted",
                    document_id=persisted.document_id,
                    chunks_count=persisted.chunks_count,
                )
            }
        )

    @application.post(
        "/api/v1/runs/{request_id}/persist",
        response_model=DatabasePersistenceState,
    )
    async def persist_existing_run(
        request_id: UUID,
    ) -> DatabasePersistenceState:
        if repository is None:
            raise HTTPException(status_code=409, detail="PostgreSQL 尚未配置")
        try:
            persistence_payload = service.persistence_payload(request_id)
        except (FileNotFoundError, OSError, ValueError) as error:
            raise HTTPException(status_code=404, detail="本地处理结果不存在") from error
        try:
            persisted = await repository.persist(persistence_payload)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:
            logger.exception(
                "PostgreSQL retry persistence failed: request_id=%s",
                request_id.hex,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "PostgreSQL 保存失败，本地文件仍然保留",
                    "request_id": request_id.hex,
                },
            ) from error
        return DatabasePersistenceState(
            enabled=True,
            status="persisted",
            document_id=persisted.document_id,
            chunks_count=persisted.chunks_count,
        )

    @application.get("/api/v1/runs/{request_id}/source")
    async def download_source(request_id: UUID) -> FileResponse:
        try:
            path = service.source_path(request_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="源 Markdown 不存在") from error
        return FileResponse(
            path,
            filename=path.name,
            media_type="text/markdown; charset=utf-8",
        )

    @application.get("/api/v1/runs/{request_id}/artifacts/{artifact_name}")
    async def download_artifact(
        request_id: UUID,
        artifact_name: str,
    ) -> FileResponse:
        try:
            path = service.artifact_path(request_id, artifact_name)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="处理产物不存在") from error
        media_type = (
            "application/x-ndjson"
            if artifact_name.endswith(".jsonl")
            else "application/json"
        )
        return FileResponse(path, filename=artifact_name, media_type=media_type)

    return application


app = create_app()


def main() -> None:
    """Run the production API using environment-controlled bind settings."""

    import uvicorn

    uvicorn.run(
        "guideline_chunker.api:app",
        host=os.getenv("GUIDELINE_CHUNKER_HOST", "127.0.0.1"),
        port=_positive_env_int("GUIDELINE_CHUNKER_PORT", 8770),
        log_level=os.getenv("GUIDELINE_CHUNKER_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
