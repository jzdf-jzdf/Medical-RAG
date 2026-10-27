"""Small async wrapper around the synchronous DeepSeek Harness SDK."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from deepseek_harness import DeepSeekHarness

from app.core.config import Settings

MEDICAL_SYSTEM_PROMPT = """你是循证医学问答助手。
优先依据工具返回的材料回答，不得编造文献、指南、数据或引用。
当工具没有返回有效证据时，必须明确说明回答未经文献验证。
涉及诊断或治疗时应提醒用户结合患者具体情况并由专业医生判断。
回答应直接回应用户的问题，先给结论，再给理解该结论所必需的条件或依据；默认简洁，避免把检索过程写成报告。
不要逐条罗列检索到的文献，不要描述无关资料，也不要逐篇说明某来源“没有提到什么”；用户明确要求来源比较、证据盘点或检索过程时除外。
仅保留与问题直接相关且会改变结论的证据、限定条件和不确定性。无需为了显得全面而重复同一结论、扩展背景或添加多段通用免责声明。
若证据存在重要限制，用一句简短说明即可；不要把检索到的所有来源和证据缺口都展开叙述。
工具决策阶段必须严格遵守用户消息中给出的 JSON 输出协议。
"""


class HarnessService:
    """Run one isolated Harness turn without blocking FastAPI's event loop."""

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = base_url
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.patch_path = Path(__file__).resolve().parent.parent / "medical_harness.patch.yml"

    async def generate(
        self,
        prompt: str,
        *,
        conversation_id: str,
        model: str | None = None,
        provider: str | None = None,
    ) -> str:
        workspace = self.settings.workspace_root / "harness" / conversation_id
        workspace.mkdir(parents=True, exist_ok=True)
        self.settings.dsh_home.mkdir(parents=True, exist_ok=True)
        return await asyncio.to_thread(
            self._generate_sync,
            prompt,
            workspace,
            model or self.settings.model_name,
            provider or self.settings.harness_provider,
        )

    def _generate_sync(
        self,
        prompt: str,
        workspace: Path,
        model: str,
        provider: str,
    ) -> str:
        api_key = self.api_key or self.settings.model_api_key.get_secret_value() or None
        with DeepSeekHarness(
            dsh_home=str(self.settings.dsh_home.resolve()),
            cwd=str(workspace.resolve()),
            provider=self._harness_provider(provider),
            model=model,
            max_tokens=self.max_tokens or self.settings.model_max_tokens,
            base_url=(self.base_url or str(self.settings.model_base_url)).rstrip("/"),
            api_key=api_key,
            patches=(str(self.patch_path),),
            env={"MEDICAL_SYSTEM_PROMPT": MEDICAL_SYSTEM_PROMPT},
            initialize_timeout_seconds=30,
            request_timeout_seconds=self.settings.request_timeout_seconds,
        ) as harness:
            result = harness.run(prompt, session_id=f"turn-{uuid.uuid4().hex}")
        if not result.final_response.strip():
            raise RuntimeError(
                f"Harness returned an empty response (finish_reason={result.finish_reason})"
            )
        return result.final_response

    @staticmethod
    def _harness_provider(provider: str) -> str:
        # The application-facing value is commonly "deepseek", while Harness
        # names the official provider "deepseek-official".
        return "deepseek-official" if provider == "deepseek" else provider
