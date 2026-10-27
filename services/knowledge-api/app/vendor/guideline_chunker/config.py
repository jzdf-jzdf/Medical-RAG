"""医学指南切块流水线的运行时配置。"""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    PrivateAttr,
    model_validator,
)


class StrictModel(BaseModel):
    """防止配置拼写错误被静默忽略的基础模型。"""

    model_config = ConfigDict(extra="forbid")


class InputConfig(StrictModel):
    encoding: Literal["utf-8"] = "utf-8"


class TaxonomyConfig(StrictModel):
    path: Path


class DeepSeekConfig(StrictModel):
    model: Literal["deepseek-v4-flash"] = "deepseek-v4-flash"
    api_key_env: Literal["DEEPSEEK_API_KEY"] = "DEEPSEEK_API_KEY"
    timeout_seconds: PositiveInt = 180
    max_attempts: PositiveInt = 3
    concurrent_requests: PositiveInt = 1
    json_output: bool = True


class BatchingConfig(StrictModel):
    target_chars: PositiveInt = 2600
    max_chars: PositiveInt = 4200
    target_atoms: PositiveInt = 12
    max_atoms: PositiveInt = 18
    context_before_atoms: Annotated[int, Field(ge=0, le=2)] = 2
    context_after_atoms: Annotated[int, Field(ge=0, le=2)] = 2

    @model_validator(mode="after")
    def check_targets(self) -> "BatchingConfig":
        if self.target_chars > self.max_chars or self.target_atoms > self.max_atoms:
            raise ValueError("batch targets must not exceed maxima")
        return self


class ChunkingConfig(StrictModel):
    review_max_chars: PositiveInt = 500


class SummaryConfig(StrictModel):
    enabled: bool = True
    max_chars: PositiveInt = 300


class CacheConfig(StrictModel):
    enabled: bool = True
    directory: Path = Path(".cache/guideline-chunker")


class PipelineConfig(StrictModel):
    input: InputConfig = Field(default_factory=InputConfig)
    taxonomy: TaxonomyConfig
    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    batching: BatchingConfig = Field(default_factory=BatchingConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    _config_path: Path | None = PrivateAttr(default=None)

    @property
    def config_path(self) -> Path | None:
        """由加载器而非 YAML 输入推导出的绝对源路径。"""

        return self._config_path


def load_pipeline_config(path: Path) -> PipelineConfig:
    """加载严格的流水线配置，并解析其分类体系路径。"""

    config_path = path.resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cfg = PipelineConfig.model_validate(raw)
    cfg._config_path = config_path
    if not cfg.taxonomy.path.is_absolute():
        cfg.taxonomy.path = (config_path.parent / cfg.taxonomy.path).resolve()
    return cfg
