"""采用私有原子写入的内容寻址 JSON 缓存。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .config import PipelineConfig
from .models import SemanticBatch, SemanticBatchResponse, SourceAtom
from .prompts import PROMPT_VERSION, RESPONSE_SCHEMA_VERSION
from .taxonomy import TopicTaxonomy


_CACHE_KEY = re.compile(r"^[0-9a-f]{64}$")
_BEARER_SECRET = re.compile(
    r"(?i)(bearer\s+)(?:\"[^\"]*\"|'[^']*'|[^\s,;}\"']+)"
)
_SK_SECRET = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]+\b")
_SENSITIVE_FIELD_MARKERS = (
    "apikey",
    "authorization",
    "accesstoken",
    "token",
    "secret",
    "password",
)
_REQUIRED_RESPONSE_KEYS = frozenset(
    {"chunks", "excluded_ranges", "review_ranges"}
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?ix)"
    r"(?P<prefix>"
    r"[\"']?"
    r"[a-z0-9_-]*"
    r"(?:api[_-]?key|authorization|access[_-]?token|token|secret|password)"
    r"[a-z0-9_-]*"
    r"[\"']?"
    r"(?:\s*[:=]\s*|\s+)"
    r")"
    r"(?:"
    r"(?P<quote>[\"'])(?P<quoted>.*?)(?P=quote)"
    r"|"
    r"(?:bearer\s+)?(?P<unquoted>[^\s,;}\"']+)"
    r")"
)


class TokenUsage(BaseModel):
    """可安全持久化到缓存元数据中的严格供应商令牌计数。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class CacheEntry(BaseModel):
    """完整且已验证的缓存产物；格式错误的条目绝不计为命中。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    raw_response: str | dict[str, Any] | list[Any] | None
    parsed_response: SemanticBatchResponse
    attempts: int = Field(ge=1, le=3)
    validation_errors: list[str]
    started_at: datetime
    completed_at: datetime
    elapsed_seconds: float = Field(ge=0)
    token_usage: TokenUsage | None

    @field_validator("parsed_response", mode="before")
    @classmethod
    def require_explicit_response_keys(cls, value: Any) -> Any:
        if isinstance(value, SemanticBatchResponse):
            return value
        if not isinstance(value, dict) or not _REQUIRED_RESPONSE_KEYS.issubset(value):
            raise ValueError(
                "cached parsed_response must explicitly contain chunks, "
                "excluded_ranges, and review_ranges"
            )
        return value

    @model_validator(mode="after")
    def validate_timing(self) -> "CacheEntry":
        if self.started_at.tzinfo is None or self.completed_at.tzinfo is None:
            raise ValueError("cache timestamps must include a timezone")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        return self


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_cache_key(
    source_hash: str,
    batch: SemanticBatch,
    atoms: list[SourceAtom],
    config: PipelineConfig,
    taxonomy: TopicTaxonomy,
    *,
    prompt_version: str = PROMPT_VERSION,
    response_schema_version: str = RESPONSE_SCHEMA_VERSION,
    model: str | None = None,
) -> str:
    """对所有可能改变语义决策的有效输入进行哈希。"""

    payload = {
        "source_hash": source_hash,
        "batch": batch.model_dump(mode="json", exclude={"cache_key"}),
        "batch_atoms": [atom.model_dump(mode="json") for atom in atoms],
        "effective_config": config.model_dump(mode="json"),
        "taxonomy": {
            "definition": taxonomy.model_dump(mode="json", by_alias=True),
            "allowed_tags": list(taxonomy.allowed_tags),
        },
        "prompt_version": prompt_version,
        "response_schema_version": response_schema_version,
        "model": model or config.deepseek.model,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


compute_cache_key = build_cache_key


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _replace_secret_assignment(match: re.Match[str]) -> str:
    quote = match.group("quote")
    replacement = f"{quote}[REDACTED]{quote}" if quote else "[REDACTED]"
    return match.group("prefix") + replacement


def _redact_string(value: str) -> str:
    redacted = _SECRET_ASSIGNMENT.sub(_replace_secret_assignment, value)
    redacted = _BEARER_SECRET.sub(r"\1[REDACTED]", redacted)
    redacted = _SK_SECRET.sub("[REDACTED]", redacted)
    return redacted


def _sanitize(value: Any) -> Any:
    value = _json_value(value)
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            field = str(key)
            normalized = re.sub(r"[^a-z0-9]", "", field.lower())
            sanitized[field] = (
                "[REDACTED]"
                if any(marker in normalized for marker in _SENSITIVE_FIELD_MARKERS)
                else _sanitize(item)
            )
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def _sanitize_raw_response(value: Any) -> Any:
    """按字段名清理结构化原始 JSON，按赋值内容清理纯文本。"""

    if not isinstance(value, str):
        return _sanitize(value)
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return _redact_string(value)
    return _canonical_json(_sanitize(decoded))


def _sanitize_record_strings(value: Any) -> Any:
    """递归脱敏缓存记录中的每个可写字符串值。"""

    value = _json_value(value)
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return {
            str(key): _sanitize_record_strings(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_record_strings(item) for item in value]
    return value


def build_cache_entry(
    *,
    raw_response: str | dict[str, Any] | list[Any] | None,
    parsed_response: SemanticBatchResponse | dict[str, Any],
    attempts: int,
    validation_errors: list[str],
    started_at: str | datetime,
    completed_at: str | datetime,
    elapsed_seconds: float,
    token_usage: dict[str, int] | None,
) -> dict[str, Any]:
    """构建完整且已清除敏感信息的磁盘缓存记录。"""

    def timestamp(value: str | datetime) -> str:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()
        return value

    payload = {
        "raw_response": _sanitize_raw_response(raw_response),
        "parsed_response": _json_value(parsed_response),
        "attempts": attempts,
        "validation_errors": validation_errors,
        "started_at": timestamp(started_at),
        "completed_at": timestamp(completed_at),
        "elapsed_seconds": elapsed_seconds,
        "token_usage": token_usage,
    }
    sanitized = _sanitize_record_strings(payload)
    entry = CacheEntry.model_validate_json(_canonical_json(sanitized))
    return entry.model_dump(mode="json")


def _is_complete_record(value: dict[str, Any]) -> bool:
    return set(CacheEntry.model_fields) == set(value)


def _validated_record(value: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(value)
    candidate["raw_response"] = _sanitize_raw_response(candidate["raw_response"])
    sanitized = _sanitize_record_strings(candidate)
    entry = CacheEntry.model_validate_json(_canonical_json(sanitized))
    return entry.model_dump(mode="json")


class CacheStore:
    def __init__(self, root: Path, enabled: bool = True):
        self.root = Path(root)
        self.enabled = enabled

    @staticmethod
    def _validate_key(key: str) -> None:
        if not _CACHE_KEY.fullmatch(key):
            raise ValueError("cache key must be a 64-character lowercase SHA-256 hex digest")

    def get(self, key: str) -> dict[str, Any] | None:
        self._validate_key(key)
        path = self.root / f"{key}.json"
        if not self.enabled or not path.is_file():
            return None
        try:
            entry = CacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError):
            return None
        return entry.model_dump(mode="json")

    def has_valid(self, key: str) -> bool:
        return self.get(key) is not None

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._validate_key(key)
        if not self.enabled:
            return

        if _is_complete_record(value):
            record = _validated_record(value)
        else:
            now = datetime.now(timezone.utc).isoformat()
            record = build_cache_entry(
                raw_response=None,
                parsed_response=value,
                attempts=1,
                validation_errors=[],
                started_at=now,
                completed_at=now,
                elapsed_seconds=0.0,
                token_usage=None,
            )

        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / f"{key}.json"
        # 暂存文件名应保持简短：Windows 测试/输出根路径本身可能已经很长，
        # 而目标文件名还包含完整的 64 字符键。
        temporary = self.root / f".{uuid4().hex}.tmp"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(_canonical_json(record))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
