"""HTTP-only integration with the Knowledge and Agent services.

The evaluation service deliberately never opens either service's database.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.schemas.workbench import GeneratedQuestion


class PlatformServiceError(RuntimeError):
    def __init__(self, detail: str, *, status_code: int = 502) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class PlatformClient:
    def __init__(self, settings: Settings) -> None:
        self.knowledge_url = str(settings.knowledge_api_url).rstrip("/")
        self.agent_url = str(settings.agent_api_url).rstrip("/")
        self.user_id = settings.evaluation_user_id
        self.timeout = httpx.Timeout(settings.request_timeout_seconds)

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise PlatformServiceError("上游服务响应超时，请稍后重试", status_code=504) from exc
        except httpx.RequestError as exc:
            raise PlatformServiceError("无法连接上游服务，请确认 Knowledge/Agent 已启动") from exc

        if response.is_error:
            try:
                body = response.json()
                detail = body.get("detail") if isinstance(body, dict) else None
            except ValueError:
                detail = None
            status_code = 404 if response.status_code == 404 else 502
            raise PlatformServiceError(
                str(detail or f"上游服务返回 HTTP {response.status_code}"),
                status_code=status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise PlatformServiceError("上游服务返回了无法解析的数据") from exc

    async def list_knowledge_bases(self) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET", f"{self.knowledge_url}/api/v1/knowledge-bases"
        )
        if not isinstance(payload, list):
            raise PlatformServiceError("Knowledge 服务的知识库列表格式不正确")
        return payload

    async def get_document_chunks(self, collection_id: str, document_id: str) -> dict:
        payload = await self._request(
            "GET",
            (
                f"{self.knowledge_url}/api/v1/knowledge-bases/{collection_id}"
                f"/documents/{document_id}/chunks"
            ),
        )
        if not isinstance(payload, dict):
            raise PlatformServiceError("Knowledge 服务的分块数据格式不正确")
        return payload

    async def ask_agent(
        self,
        *,
        content: str,
        title: str,
        model_config_id: str | None = None,
    ) -> dict[str, Any]:
        conversation = await self._request(
            "POST",
            f"{self.agent_url}/api/v1/conversations",
            json={"title": title[:500], "user_id": self.user_id},
        )
        conversation_id = conversation.get("id") if isinstance(conversation, dict) else None
        if not conversation_id:
            raise PlatformServiceError("Agent 服务未返回会话标识")
        payload: dict[str, Any] = {
            "conversation_id": conversation_id,
            "user_id": self.user_id,
            "content": content,
        }
        if model_config_id:
            payload["model_config_id"] = model_config_id
        result = await self._request(
            "POST", f"{self.agent_url}/api/v1/chat", json=payload
        )
        if not isinstance(result, dict):
            raise PlatformServiceError("Agent 服务返回的数据格式不正确")
        return result


def get_platform_client() -> PlatformClient:
    return PlatformClient(get_settings())


def build_question_prompt(
    *, document_title: str, chunks: list[dict], instructions: str | None
) -> str:
    source = "\n\n".join(
        (
            f"[分块 {item['index']}]\n"
            f"章节：{' > '.join(item.get('heading_path') or []) or '未标注'}\n"
            f"原文：{item['content']}"
        )
        for item in chunks
    )
    extra = instructions.strip() if instructions and instructions.strip() else "无"
    return f"""你是医学指南题库编辑。仅依据给出的知识分块生成一道可用于测试医学 Agent 的问答题。

要求：
1. 题目应能由分块内容充分回答，不引入分块之外的事实。
2. 标准答案要准确、完整、措辞克制；如存在推荐强度或适用条件必须保留。
3. evidence.quote 必须逐字摘录原文，chunk_index 必须使用给定序号。
4. 只输出一个 JSON 对象，不要 Markdown 代码块，不要解释。
5. JSON 键固定为 question、standard_answer、difficulty、topic、rationale、evidence。
6. evidence 是对象数组，每项包含 chunk_index、heading、quote。

文档：{document_title}
补充出题要求：{extra}

知识分块：
{source}
"""


def parse_generated_question(
    content: str,
    *,
    source_chunks: list[dict],
    agent_result: dict[str, Any],
) -> GeneratedQuestion:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise TypeError("not an object")
        payload.update(
            source_chunks=source_chunks,
            agent_run_id=str(agent_result.get("run_id") or ""),
            model=str(agent_result.get("model") or "unknown"),
            provider=str(agent_result.get("provider") or "unknown"),
        )
        return GeneratedQuestion.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise PlatformServiceError(
            "Agent 未按约定返回题目 JSON，请调整出题要求后重试"
        ) from exc
