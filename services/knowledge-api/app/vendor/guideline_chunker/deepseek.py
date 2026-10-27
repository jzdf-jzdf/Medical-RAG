"""DeepSeek JSON 适配器与有界重试策略。"""

from __future__ import annotations

import inspect
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from openai import APIConnectionError, APITimeoutError, OpenAI
from pydantic import ValidationError

from .config import DeepSeekConfig, PipelineConfig
from .models import SemanticBatch, SemanticBatchResponse, SourceAtom
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .taxonomy import TopicTaxonomy


MAX_TOTAL_ATTEMPTS = 3
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MAX_TOKENS = 8192
REQUEST_HEARTBEAT_SECONDS = 30.0

logger = logging.getLogger(__name__)


class RetryableSemanticError(Exception):
    """可通过再次请求纠正的模型输出失败。"""


class EmptyModelResponse(RetryableSemanticError):
    """供应商完成请求但未返回 JSON 内容。"""


class InvalidSemanticResponse(RetryableSemanticError):
    """供应商返回了无效 JSON，或不符合模式的 JSON 对象。"""


class SemanticValidationError(RetryableSemanticError):
    def __init__(self, errors: Sequence[Any]):
        self.errors = [str(error) for error in errors]
        super().__init__("; ".join(self.errors))


class RetryExhaustedError(RuntimeError):
    def __init__(
        self,
        attempts: int,
        errors: list[str],
        *,
        last_response: SemanticBatchResponse | None = None,
        validation_errors: Sequence[str] = (),
    ):
        self.attempts = attempts
        self.errors = errors
        self.last_response = last_response
        self.validation_errors = list(validation_errors)
        super().__init__(f"semantic analysis failed after {attempts} attempts")


class SemanticClient(Protocol):
    def analyze(
        self,
        batch: SemanticBatch,
        *args: Any,
        **kwargs: Any,
    ) -> SemanticBatchResponse: ...


def _usage_dict(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    names = ("prompt_tokens", "completion_tokens", "total_tokens")
    result = {name: getattr(usage, name, None) for name in names}
    if all(value is None for value in result.values()):
        if hasattr(usage, "model_dump"):
            dumped = usage.model_dump()
            result = {name: dumped.get(name) for name in names}
    return {name: value for name, value in result.items() if isinstance(value, int)} or None


def _validation_error_summary(error: ValidationError) -> str:
    """仅保留字段位置、错误类型和消息，不记录模型返回值。"""

    details: list[str] = []
    for issue in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:4]:
        location = ".".join(str(part) for part in issue.get("loc", ())) or "<root>"
        details.append(
            f"{location} [{issue.get('type', 'validation_error')}]: "
            f"{issue.get('msg', 'invalid value')}"
        )
    remaining = error.error_count() - len(details)
    if remaining > 0:
        details.append(f"... and {remaining} more validation errors")
    return "; ".join(details)


class DeepSeekSemanticClient:
    """传输层可注入、兼容 OpenAI 接口的 DeepSeek 客户端。"""

    def __init__(
        self,
        config: DeepSeekConfig | PipelineConfig,
        taxonomy: TopicTaxonomy,
        *,
        client: Any | None = None,
    ):
        self.config = config.deepseek if isinstance(config, PipelineConfig) else config
        self.taxonomy = taxonomy
        self.last_exchange: dict[str, Any] | None = None
        if client is None:
            api_key = os.environ[self.config.api_key_env]
            client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        self.client = client

    def analyze(
        self,
        batch: SemanticBatch,
        atoms: list[SourceAtom],
        *,
        retry_feedback: str | None = None,
    ) -> SemanticBatchResponse:
        if len(self.taxonomy.allowed_tags) != 102:
            raise ValueError("DeepSeek requests require the 102-tag HICH taxonomy")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_prompt(batch, atoms, self.taxonomy.allowed_tags),
            },
        ]
        if retry_feedback:
            messages.append(
                {
                    "role": "user",
                    "content": "上次响应未通过校验。请仅修正JSON结果：" + retry_feedback,
                }
            )

        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=DEEPSEEK_MAX_TOKENS,
            extra_body={"thinking": {"type": "disabled"}},
            timeout=self.config.timeout_seconds,
        )
        try:
            choice = response.choices[0]
            content = choice.message.content
            finish_reason = getattr(choice, "finish_reason", None)
        except (AttributeError, IndexError, TypeError) as error:
            raise EmptyModelResponse("DeepSeek returned no message content") from error
        if not isinstance(content, str) or not content.strip():
            raise EmptyModelResponse("DeepSeek returned empty content")

        self.last_exchange = {
            "raw_response": content,
            "token_usage": _usage_dict(getattr(response, "usage", None)),
        }
        if finish_reason == "length":
            raise InvalidSemanticResponse(
                "DeepSeek JSON was truncated because max_tokens was reached"
            )
        try:
            return SemanticBatchResponse.model_validate_json(content)
        except ValidationError as error:
            detail = _validation_error_summary(error)
            raise InvalidSemanticResponse(
                "DeepSeek returned JSON that does not match the response schema "
                f"(chars={len(content)}, finish_reason={finish_reason or 'unknown'}): "
                f"{detail}"
            ) from error


def _status_code(error: BaseException) -> int | None:
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _is_retryable(error: BaseException) -> bool:
    status = _status_code(error)
    if status is not None:
        if status == 429 or 500 <= status <= 599:
            return True
        if 400 <= status <= 499:
            return False
    return isinstance(
        error,
        (
            APIConnectionError,
            APITimeoutError,
            ConnectionError,
            TimeoutError,
            RetryableSemanticError,
        ),
    )


_FEEDBACK_SECRET = re.compile(
    r"(?i)(?:authorization\s*[:=]\s*|bearer\s+)?sk-[A-Za-z0-9_-]+"
)


def _short_error(error: BaseException) -> str:
    message = _FEEDBACK_SECRET.sub("[REDACTED]", str(error)).replace("\n", " ")
    return f"{type(error).__name__}: {message}"[:280]


def _retry_feedback(error: BaseException) -> str:
    """向下一次模型请求提供覆盖多类问题、且不含敏感内容的反馈。"""

    if not isinstance(error, SemanticValidationError):
        return _short_error(error)

    grouped: dict[str, list[str]] = {}
    for raw in error.errors:
        message = _FEEDBACK_SECRET.sub("[REDACTED]", raw).replace("\n", " ")
        match = re.match(r"(?P<code>[A-Z][A-Z0-9_]+)", message)
        code = match.group("code") if match else "VALIDATION_ERROR"
        grouped.setdefault(code, []).append(message)

    parts: list[str] = []
    for code, messages in grouped.items():
        examples = " | ".join(messages[:3])
        suffix = f"（另有{len(messages) - 3}项）" if len(messages) > 3 else ""
        parts.append(f"{code}×{len(messages)}: {examples}{suffix}")
    return ("SemanticValidationError: " + "; ".join(parts))[:1200]


def _call_analyze(
    client: SemanticClient,
    batch: SemanticBatch,
    atoms: list[SourceAtom] | None,
    retry_feedback: str | None,
) -> SemanticBatchResponse:
    method = client.analyze
    parameters = inspect.signature(method).parameters
    args: list[Any] = [batch]
    kwargs: dict[str, Any] = {}
    if atoms is not None and "atoms" in parameters:
        args.append(atoms)
    if "retry_feedback" in parameters:
        kwargs["retry_feedback"] = retry_feedback
    return method(*args, **kwargs)


def _call_with_heartbeat(
    client: SemanticClient,
    batch: SemanticBatch,
    atoms: list[SourceAtom] | None,
    retry_feedback: str | None,
    *,
    attempt: int,
    heartbeat_interval_seconds: float,
) -> SemanticBatchResponse:
    """调用阻塞式客户端，并在 CLI 开启 INFO 日志时定期报告等待状态。"""

    if (
        heartbeat_interval_seconds <= 0
        or not logger.isEnabledFor(logging.INFO)
    ):
        return _call_analyze(client, batch, atoms, retry_feedback)

    stopped = threading.Event()
    started = time.monotonic()

    def report_wait() -> None:
        while not stopped.wait(heartbeat_interval_seconds):
            logger.info(
                "DeepSeek 仍在等待响应：批次=%s，尝试=%d，已等待=%.0f 秒",
                batch.batch_id,
                attempt,
                time.monotonic() - started,
            )

    thread = threading.Thread(
        target=report_wait,
        name=f"deepseek-heartbeat-{batch.batch_id}",
        daemon=True,
    )
    thread.start()
    try:
        return _call_analyze(client, batch, atoms, retry_feedback)
    finally:
        stopped.set()
        thread.join(timeout=min(heartbeat_interval_seconds, 0.1))


def _validation_errors(
    validator: Any,
    batch: SemanticBatch,
    response: SemanticBatchResponse,
) -> list[Any]:
    target = getattr(validator, "validate", validator)
    parameters = inspect.signature(target).parameters
    result = target(batch, response) if len(parameters) >= 2 else target(response)
    if result is None or result is True:
        return []
    if result is False:
        return ["validator rejected response"]
    if isinstance(result, (str, bytes)):
        return [result]
    return list(result)


def analyze_with_retry(
    client: SemanticClient,
    batch: SemanticBatch,
    validator: Callable[..., Any] | Any,
    max_attempts: int = MAX_TOTAL_ATTEMPTS,
    *,
    atoms: list[SourceAtom] | None = None,
    base_delay_seconds: float = 0.25,
    max_delay_seconds: float = 2.0,
    sleeper: Callable[[float], None] = time.sleep,
    heartbeat_interval_seconds: float = REQUEST_HEARTBEAT_SECONDS,
) -> SemanticBatchResponse:
    """执行分析与验证，分类后的总尝试次数最多为三次。"""

    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if (
        base_delay_seconds < 0
        or max_delay_seconds < 0
        or heartbeat_interval_seconds < 0
    ):
        raise ValueError("retry delays must be non-negative")

    attempts_allowed = min(max_attempts, MAX_TOTAL_ATTEMPTS)
    timeout_seconds = getattr(getattr(client, "config", None), "timeout_seconds", None)
    errors: list[str] = []
    feedback: str | None = None
    for attempt in range(1, attempts_allowed + 1):
        response: SemanticBatchResponse | None = None
        attempt_started = time.monotonic()
        timeout_note = (
            f"，单次超时={timeout_seconds} 秒"
            if isinstance(timeout_seconds, (int, float))
            else ""
        )
        logger.info(
            "开始 DeepSeek 请求：批次=%s，尝试=%d/%d%s",
            batch.batch_id,
            attempt,
            attempts_allowed,
            timeout_note,
        )
        try:
            response = _call_with_heartbeat(
                client,
                batch,
                atoms,
                feedback,
                attempt=attempt,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
            )
            validation_errors = _validation_errors(validator, batch, response)
            if validation_errors:
                raise SemanticValidationError(validation_errors)
            logger.info(
                "DeepSeek 响应校验通过：批次=%s，尝试=%d，耗时=%.1f 秒",
                batch.batch_id,
                attempt,
                time.monotonic() - attempt_started,
            )
            return response
        except Exception as error:
            if not _is_retryable(error):
                logger.error(
                    "DeepSeek 请求不可重试：批次=%s，尝试=%d，错误=%s",
                    batch.batch_id,
                    attempt,
                    _short_error(error),
                )
                raise
            log_error = _short_error(error)
            feedback = _retry_feedback(error)
            errors.append(log_error)
            if attempt == attempts_allowed:
                logger.error(
                    "DeepSeek 请求重试耗尽：批次=%s，尝试=%d/%d，错误=%s",
                    batch.batch_id,
                    attempt,
                    attempts_allowed,
                    log_error,
                )
                raise RetryExhaustedError(
                    attempt,
                    errors,
                    last_response=(
                        response if isinstance(error, SemanticValidationError) else None
                    ),
                    validation_errors=(
                        error.errors
                        if isinstance(error, SemanticValidationError)
                        else ()
                    ),
                ) from error
            delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
            logger.warning(
                "DeepSeek 请求失败，准备重试：批次=%s，尝试=%d/%d，"
                "错误=%s，%.2f 秒后重试",
                batch.batch_id,
                attempt,
                attempts_allowed,
                log_error,
                delay,
            )
            sleeper(delay)

    raise AssertionError("unreachable")
