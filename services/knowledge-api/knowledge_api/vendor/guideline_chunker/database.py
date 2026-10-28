"""Transactional PostgreSQL persistence for locally completed API runs."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import asyncpg


POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS guideline_chunker_documents (
    id BIGSERIAL PRIMARY KEY,
    input_sha256 CHAR(64) NOT NULL UNIQUE,
    request_id UUID NOT NULL,
    pipeline_run_id TEXT NOT NULL,
    source_doc TEXT NOT NULL,
    input_filename TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('completed', 'completed_with_review')
    ),
    content TEXT NOT NULL,
    source_path TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    manifest JSONB NOT NULL,
    processing_report JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS guideline_chunker_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL
        REFERENCES guideline_chunker_documents(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL,
    source_doc TEXT NOT NULL,
    section_id TEXT NOT NULL,
    section_path JSONB NOT NULL,
    topic_tags JSONB NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    chunk_type TEXT NOT NULL CHECK (
        chunk_type IN ('fact', 'recommendation')
    ),
    recommendation_levels JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_guideline_chunker_chunks_document
    ON guideline_chunker_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_guideline_chunker_chunks_type
    ON guideline_chunker_chunks(chunk_type);
CREATE INDEX IF NOT EXISTS idx_guideline_chunker_chunks_topics
    ON guideline_chunker_chunks USING GIN(topic_tags);
"""


DOCUMENT_UPSERT_SQL = """
INSERT INTO guideline_chunker_documents (
    input_sha256,
    request_id,
    pipeline_run_id,
    source_doc,
    input_filename,
    status,
    content,
    source_path,
    output_dir,
    manifest,
    processing_report
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb
)
ON CONFLICT (input_sha256) DO UPDATE SET
    request_id = EXCLUDED.request_id,
    pipeline_run_id = EXCLUDED.pipeline_run_id,
    source_doc = EXCLUDED.source_doc,
    input_filename = EXCLUDED.input_filename,
    status = EXCLUDED.status,
    content = EXCLUDED.content,
    source_path = EXCLUDED.source_path,
    output_dir = EXCLUDED.output_dir,
    manifest = EXCLUDED.manifest,
    processing_report = EXCLUDED.processing_report,
    updated_at = NOW()
RETURNING id
"""


CHUNK_INSERT_SQL = """
INSERT INTO guideline_chunker_chunks (
    document_id,
    chunk_id,
    source_doc,
    section_id,
    section_path,
    topic_tags,
    summary,
    content,
    chunk_type,
    recommendation_levels,
    payload
)
VALUES (
    $1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9,
    $10::jsonb, $11::jsonb
)
"""


@dataclass(frozen=True, slots=True)
class PersistencePayload:
    request_id: UUID
    source_path: Path
    output_dir: Path
    manifest: dict[str, Any]
    processing_report: dict[str, Any]
    chunks: Sequence[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    document_id: int
    chunks_count: int


class ResultRepository(Protocol):
    async def persist(self, payload: PersistencePayload) -> PersistenceResult: ...


ConnectionFactory = Callable[[str], Awaitable[Any]]


def normalize_postgres_dsn(value: str) -> str:
    """Accept plain PostgreSQL URLs and common SQLAlchemy driver-qualified URLs."""

    dsn = value.strip()
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn.removeprefix(prefix)
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise ValueError(
            "GUIDELINE_CHUNKER_DATABASE_URL must use a PostgreSQL URL"
        )
    return dsn


class PostgresResultRepository:
    """Replace one source document and its full chunk set atomically."""

    def __init__(
        self,
        database_url: str,
        *,
        auto_create_schema: bool = True,
        connection_factory: ConnectionFactory = asyncpg.connect,
    ):
        self.database_url = normalize_postgres_dsn(database_url)
        self.auto_create_schema = auto_create_schema
        self.connection_factory = connection_factory

    async def persist(self, payload: PersistencePayload) -> PersistenceResult:
        manifest = payload.manifest
        status = str(manifest["status"])
        if status not in {"completed", "completed_with_review"}:
            raise ValueError("only formal completed runs can be persisted")

        source_content = payload.source_path.read_text(encoding="utf-8")
        connection = await self.connection_factory(self.database_url)
        try:
            async with connection.transaction():
                if self.auto_create_schema:
                    await connection.execute(POSTGRES_SCHEMA_SQL)

                row = await connection.fetchrow(
                    DOCUMENT_UPSERT_SQL,
                    manifest["input_sha256"],
                    payload.request_id,
                    manifest["run_id"],
                    manifest["source_doc"],
                    manifest["input_filename"],
                    status,
                    source_content,
                    str(payload.source_path),
                    str(payload.output_dir),
                    _json_text(manifest),
                    _json_text(payload.processing_report),
                )
                if row is None:
                    raise RuntimeError("PostgreSQL did not return a document id")
                document_id = int(row["id"])

                await connection.execute(
                    "DELETE FROM guideline_chunker_chunks WHERE document_id = $1",
                    document_id,
                )
                chunk_rows = [
                    (
                        document_id,
                        chunk["chunk_id"],
                        chunk["source_doc"],
                        chunk["section_id"],
                        _json_text(chunk.get("section_path", [])),
                        _json_text(chunk.get("topic_tags", [])),
                        chunk["summary"],
                        chunk["content"],
                        chunk["type"],
                        _json_text(chunk.get("recommendation_levels", [])),
                        _json_text(chunk),
                    )
                    for chunk in payload.chunks
                ]
                if chunk_rows:
                    await connection.executemany(CHUNK_INSERT_SQL, chunk_rows)
        finally:
            await connection.close()

        return PersistenceResult(
            document_id=document_id,
            chunks_count=len(payload.chunks),
        )


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
