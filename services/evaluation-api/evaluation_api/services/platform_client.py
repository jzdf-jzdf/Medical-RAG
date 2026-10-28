"""HTTP-only integration with the Knowledge and Agent services.

The evaluation service deliberately never opens either service's database.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from evaluation_api.core.config import Settings, get_settings
from evaluation_api.schemas.workbench import GeneratedQuestion, GeneratedQuestionBatch


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
        collection_id: str | None = None,
        execution_mode: str = "agent",
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
            "execution_mode": execution_mode,
        }
        if model_config_id:
            payload["model_config_id"] = model_config_id
        if collection_id:
            payload["collection_id"] = collection_id
        attempts = 2 if execution_mode == "direct" else 1
        for attempt in range(attempts):
            result = await self._request(
                "POST", f"{self.agent_url}/api/v1/chat", json=payload
            )
            if not isinstance(result, dict):
                raise PlatformServiceError("Agent 服务返回的数据格式不正确")
            if execution_mode != "direct" or str(result.get("content") or "").strip():
                return result
            if attempt + 1 == attempts:
                raise PlatformServiceError("Agent 连续返回空内容，请稍后重试")
        raise PlatformServiceError("Agent 服务未返回结果")


def get_platform_client() -> PlatformClient:
    return PlatformClient(get_settings())


def build_question_prompt(
    *,
    document_title: str,
    chunks: list[dict],
    question_type: str,
    question_style: str,
    instructions: str | None,
    variation_index: int = 1,
    variation_total: int = 1,
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
    style_instructions = {
        "patient": "患者或家属口吻：使用第一人称或贴近就诊者的自然语言提问，不使用分块之外的病情细节。",
        "clinician": "临床医生口吻：使用专业临床语言，突出判断条件、处置决策或指南推荐。",
        "professional": "相关从业者口吻：从急救、护理、影像、康复或医疗管理等执行视角提问；仅在分块有依据时指明具体岗位。",
        "public": "大众口吻：使用清晰易懂的健康知识表达，不假设提问者已经患病，不堆砌专业术语。",
    }
    style_instruction = style_instructions.get(question_style, style_instructions["clinician"])
    diversity_angles = [
        "核心概念与首要原则",
        "适用条件与临床判断",
        "处置步骤与执行顺序",
        "风险、禁忌或注意事项",
        "推荐强度、证据级别或目标值",
    ]
    diversity_angle = diversity_angles[(variation_index - 1) % len(diversity_angles)]
    if question_type == "single_choice":
        type_instruction = """生成一道单项选择题。提供且仅提供 A、B、C、D 四个选项，只有一个正确答案；干扰项必须合理但可由原文排除。
JSON 键固定为 question、standard_answer、options、correct_answer、difficulty、topic、rationale、evidence。
options 是对象数组，每项包含 key 和 text；correct_answer 只填写 A、B、C 或 D；standard_answer 简要解释正确选项。"""
    else:
        type_instruction = """生成一道问答题。
JSON 键固定为 question、standard_answer、difficulty、topic、rationale、evidence。"""
    return f"""你是医学指南题库编辑。仅依据给出的知识分块生成一道可用于医学 Agent 评测的题目。

要求：
1. 题目应能由分块内容充分回答，不引入分块之外的事实。
2. 标准答案要准确、完整、措辞克制；如存在推荐强度或适用条件必须保留。
3. evidence.quote 必须逐字摘录原文，chunk_index 必须使用给定序号。
4. 题干口吻：{style_instruction}
5. 只输出一个 JSON 对象，不要 Markdown 代码块，不要解释。
6. {type_instruction}
7. evidence 是对象数组，每项包含 chunk_index、heading、quote。
8. question 不超过 120 个汉字，standard_answer 不超过 320 个汉字，rationale 不超过 80 个汉字。
9. evidence 仅保留 1–2 条最关键依据，每条 quote 不超过 120 个汉字。
10. 不要添加 [1] 等引用标记、临床免责声明或分块外信息。
11. 请输出紧凑且完整的 JSON，务必以最后一个右花括号结束。

文档：{document_title}
问题口吻：{style_instruction}
批次位置：第 {variation_index}/{variation_total} 道，优先考查“{diversity_angle}”；若分块不支持该角度，选择最接近且有明确原文依据的角度。
补充出题要求：{extra}

知识分块：
{source}
"""


def parse_generated_questions(
    content: str,
    *,
    source_chunks: list[dict],
    agent_result: dict[str, Any],
    requested_count: int,
    question_type: str,
    question_style: str,
) -> GeneratedQuestionBatch:
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
        raw_questions = payload.get("questions")
        if requested_count == 1 and not isinstance(raw_questions, list):
            raw_questions = [payload]
        if not isinstance(raw_questions, list) or len(raw_questions) != requested_count:
            actual_count = len(raw_questions) if isinstance(raw_questions, list) else 0
            raise ValueError(
                f"expected {requested_count} questions, received {actual_count}"
            )
        metadata = {
            "question_type": question_type,
            "source_chunks": source_chunks,
            "agent_run_id": str(agent_result.get("run_id") or ""),
            "model": str(agent_result.get("model") or "unknown"),
            "provider": str(agent_result.get("provider") or "unknown"),
        }
        questions = [
            GeneratedQuestion.model_validate({**item, **metadata})
            for item in raw_questions
            if isinstance(item, dict)
        ]
        if len(questions) != requested_count:
            raise ValueError("question items must be objects")
        return GeneratedQuestionBatch(
            questions=questions,
            requested_count=requested_count,
            question_type=question_type,
            question_style=question_style,
        )
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise PlatformServiceError(
            "Agent 未按约定返回完整题目批次，请减少生成数量或调整出题要求后重试"
        ) from exc
