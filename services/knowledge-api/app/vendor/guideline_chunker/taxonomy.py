"""受控主题分类体系的加载与验证。"""

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


SECONDARY_CODE = re.compile(r"^T\d{2}\.\d{2}$")
PRIMARY_CODE = re.compile(r"^T\d{2}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecondaryTopic(StrictModel):
    id: str
    name: str


class PrimaryTopic(StrictModel):
    id: str
    name: str
    children: list[SecondaryTopic]


class TopicTaxonomy(StrictModel):
    version: str = Field(validation_alias="taxonomy_version")
    primary_topics: list[PrimaryTopic] = Field(validation_alias="topics")
    _allowed_tags: tuple[str, ...] = PrivateAttr(default=())

    @property
    def allowed_tags(self) -> tuple[str, ...]:
        """派生的受控标签；绝不直接接受分类体系 YAML 中的该值。"""

        return self._allowed_tags


def load_taxonomy(path: Path) -> TopicTaxonomy:
    """加载并严格验证 HICH V1 受控主题分类体系。"""

    taxonomy = TopicTaxonomy.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
    parent_ids = [parent.id for parent in taxonomy.primary_topics]
    if any(not PRIMARY_CODE.fullmatch(parent_id) for parent_id in parent_ids):
        raise ValueError("invalid primary topic code")
    if len(parent_ids) != len(set(parent_ids)):
        raise ValueError("duplicate primary topic code")

    tags: list[str] = []
    child_ids: list[str] = []
    for parent in taxonomy.primary_topics:
        for child in parent.children:
            if not SECONDARY_CODE.fullmatch(child.id):
                raise ValueError(f"invalid secondary topic code: {child.id}")
            if child.id[:3] != parent.id:
                raise ValueError(f"topic parent mismatch: {child.id}")
            child_ids.append(child.id)
            tags.append(f"{child.id}/{child.name}")
    if len(child_ids) != len(set(child_ids)):
        raise ValueError("duplicate controlled topic code")
    if len(tags) != len(set(tags)):
        raise ValueError("duplicate controlled topic tag")
    if taxonomy.version != "hich_topics_v1":
        raise ValueError("taxonomy version must be hich_topics_v1")
    if len(taxonomy.primary_topics) != 14 or len(tags) != 102:
        raise ValueError("hich_topics_v1 requires 14 primary and 102 secondary topics")
    taxonomy._allowed_tags = tuple(tags)
    return taxonomy
