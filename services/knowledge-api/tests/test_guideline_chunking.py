import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from knowledge_api.services import guideline_chunking as engine
from knowledge_api.services.chunking import validate_chunking_config
from knowledge_api.vendor.guideline_chunker.models import SemanticBatchResponse

TEXT = (
    "# 治疗\r\n\r\n脑出血是脑实质内的出血。\r\n\r\n推荐意见：应平稳降压（Ⅰ级推荐，A级证据）。\r\n"
)


class FixtureClient:
    calls = 0
    partial = False
    last_exchange = None

    def __init__(self, *args, **kwargs):
        pass

    def analyze(self, batch, atoms, **kwargs):
        type(self).calls += 1
        by_id = {atom.atom_id: atom for atom in atoms}
        chunks, reviews = [], []
        for position, atom_id in enumerate(batch.core_atom_ids):
            atom = by_id[atom_id]
            if self.partial and position > 0:
                reviews.append(
                    {
                        "start_atom_id": atom_id,
                        "end_atom_id": atom_id,
                        "reason_code": "NEEDS_REVIEW",
                        "decision_reason": "fixture review",
                    }
                )
            else:
                chunks.append(
                    {
                        "start_atom_id": atom_id,
                        "end_atom_id": atom_id,
                        "type": "recommendation" if atom.recommendation_candidate else "fact",
                        "topic_tags": ["T05.03/收缩压目标"],
                        "summary": atom.text.strip(),
                        "decision_reason": "fixture decision",
                    }
                )
        return SemanticBatchResponse.model_validate({"chunks": chunks, "review_ranges": reviews})


@pytest.fixture
def configured_engine(monkeypatch, tmp_path):
    import openai

    settings = SimpleNamespace(
        workspace_root=tmp_path,
        deepseek_api_key="fixture-key",
        deepseek_base_url="https://api.deepseek.com",
    )
    monkeypatch.setattr(engine, "get_settings", lambda: settings)
    monkeypatch.setattr(engine, "DeepSeekSemanticClient", FixtureClient)
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(close=lambda: None))
    FixtureClient.calls = 0
    FixtureClient.partial = False
    return settings


@pytest.mark.asyncio
async def test_full_pipeline_maps_metadata_preserves_crlf_offsets_and_reuses_cache(
    configured_engine,
):
    document_id = uuid.uuid4()
    progress = []

    async def on_progress(phase, percent, message):
        progress.append((phase, percent))

    _, chunks = await engine.build_chunks_async(
        TEXT,
        "guideline_semantic",
        {},
        document_id=document_id,
        document_title="测试指南",
        on_progress=on_progress,
    )
    assert chunks
    assert all(TEXT[item.char_start : item.char_end] == item.content for item in chunks)
    assert {chunk.metadata["type"] for chunk in chunks} == {"fact", "recommendation"}
    recommendation = next(chunk for chunk in chunks if chunk.metadata["type"] == "recommendation")
    assert recommendation.metadata["recommendation_levels"]
    assert recommendation.metadata["topic_tags"] == ["T05.03/收缩压目标"]
    assert recommendation.metadata["quality_gate_passed"] is True
    assert recommendation.metadata["upstream_revision"] == engine.UPSTREAM_REVISION
    assert recommendation.metadata["document_id"] == str(document_id)
    assert progress[-1][1] == 100
    run_id = recommendation.metadata["run_id"]
    for artifact in engine.artifact_urls(run_id):
        artifact_path = engine.run_artifact(run_id, artifact)
        assert artifact_path.is_file()
        assert "fixture-key" not in artifact_path.read_text(encoding="utf-8")
    calls = FixtureClient.calls
    _, repeated = await engine.build_chunks_async(
        TEXT, "guideline_semantic", {}, document_id=document_id, document_title="测试指南"
    )
    assert FixtureClient.calls == calls
    assert [chunk.metadata["chunk_id"] for chunk in repeated] == [
        chunk.metadata["chunk_id"] for chunk in chunks
    ]
    report = json.loads(
        engine.run_artifact(repeated[0].metadata["run_id"], "processing_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["deepseek"]["cache_hits"] > 0


@pytest.mark.asyncio
async def test_preview_exposes_quality_failures_and_build_blocks_publication(configured_engine):
    FixtureClient.partial = True
    text = "# 诊疗\n\n患者需要测量血压。\n\n患者需要测量体温。\n"
    document_id = uuid.uuid4()
    _, chunks = await engine.build_chunks_async(
        text, "guideline_semantic", {}, document_id=document_id, document_title="测试", preview=True
    )
    assert chunks and chunks[0].metadata["quality_gate_passed"] is False
    with pytest.raises(ValueError, match="未通过质量门"):
        await engine.build_chunks_async(
            text, "guideline_semantic", {}, document_id=document_id, document_title="测试"
        )
    _, allowed = await engine.build_chunks_async(
        text,
        "guideline_semantic",
        {"require_quality_gate": False},
        document_id=document_id,
        document_title="测试",
    )
    assert allowed and allowed[0].metadata["quality_gate_passed"] is False


@pytest.mark.asyncio
async def test_missing_key_does_not_fallback_to_character_chunking(configured_engine):
    configured_engine.deepseek_api_key = ""
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        await engine.build_chunks_async(
            TEXT, "guideline_semantic", {}, document_id=uuid.uuid4(), document_title="测试"
        )


def test_config_keeps_upstream_contract_and_rejects_arbitrary_paths_or_secrets():
    config = validate_chunking_config("guideline_semantic", {})
    assert config.batching.max_chars == 4200
    assert config.deepseek.model == "deepseek-v4-flash"
    assert engine.engine_status()["topic_count"] == 102
    for invalid in (
        {"api_key": "bad"},
        {"taxonomy": {"path": "elsewhere"}},
        {"batching": {"target_atoms": 20, "max_atoms": 10}},
    ):
        with pytest.raises(ValueError):
            validate_chunking_config("guideline_semantic", invalid)


@pytest.mark.asyncio
async def test_semantic_preview_enqueues_durable_task_without_running_model(monkeypatch):
    from knowledge_api.api import knowledge_bases as api

    task = SimpleNamespace(id=uuid.uuid4())
    document = SimpleNamespace(id=uuid.uuid4())

    class FakeDB:
        async def scalar(self, statement):
            return document

    async def enqueue(document, config, db):
        return task

    monkeypatch.setattr(api, "_enqueue_guideline_preview", enqueue)
    response = Response()
    result = await api.preview_chunking_recipe(
        api.ChunkPreviewRequest(document_id=document.id, strategy="guideline_semantic"),
        response,
        FakeDB(),
    )
    assert result is task
    assert response.status_code == 202


def test_artifact_whitelist_and_run_id_validation(configured_engine):
    with pytest.raises(ValueError):
        engine.run_artifact(str(uuid.uuid4()), "../../.env")
    with pytest.raises(ValueError):
        engine.run_directory("../../runtime")


@pytest.mark.asyncio
async def test_result_endpoint_rejects_pending_and_unrelated_tasks(monkeypatch):
    from knowledge_api.api import processing_tasks as api

    task = SimpleNamespace(task_type="guideline_chunking", status="running", error_message=None)

    class Repository:
        def __init__(self, db):
            pass

        async def get(self, task_id):
            return task

    monkeypatch.setattr(api, "ProcessingTaskRepository", Repository)
    with pytest.raises(HTTPException) as pending:
        await api.get_processing_task_result(uuid.uuid4(), None)
    assert pending.value.status_code == 409
    task.task_type = "pdf_to_markdown"
    with pytest.raises(HTTPException) as unrelated:
        await api.get_processing_task_result(uuid.uuid4(), None)
    assert unrelated.value.status_code == 404


@pytest.mark.asyncio
async def test_index_build_persists_semantic_metadata_and_qdrant_payload(
    configured_engine, monkeypatch
):
    from knowledge_api.models import IndexVersion
    from knowledge_api.services import indexing

    collection = SimpleNamespace(
        id=uuid.uuid4(),
        chunking_method=SimpleNamespace(id=uuid.uuid4(), strategy="guideline_semantic", config={}),
        embedding_model=SimpleNamespace(provider="openai", model_name="fixture", dimension=3),
        documents=[
            SimpleNamespace(
                id=uuid.uuid4(),
                title="指南",
                files=[SimpleNamespace(id=uuid.uuid4(), kind="markdown")],
            )
        ],
    )

    class FakeDB:
        def __init__(self):
            self.objects = []
            self.committed = False

        async def scalar(self, statement):
            return collection

        def add(self, obj):
            self.objects.append(obj)

        def add_all(self, objects):
            self.objects.extend(objects)

        async def flush(self):
            for obj in self.objects:
                if obj.id is None:
                    obj.id = uuid.uuid4()

        async def execute(self, statement):
            pass

        async def commit(self):
            self.committed = True

    async def read_text(document, **kwargs):
        return TEXT

    async def embed(model, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    qdrant_points = []

    async def create_collection(name, dimension):
        assert dimension == 3

    async def upsert(name, points):
        qdrant_points.extend(points)

    monkeypatch.setattr(indexing, "_document_text", read_text)
    monkeypatch.setattr(indexing, "embed_texts", embed)
    monkeypatch.setattr(indexing, "create_qdrant_collection", create_collection)
    monkeypatch.setattr(indexing, "upsert_points", upsert)
    db = FakeDB()
    result = await indexing.build_collection_index(db, collection.id)
    index = next(obj for obj in db.objects if isinstance(obj, IndexVersion))
    rows = [obj for obj in db.objects if not isinstance(obj, IndexVersion)]
    assert db.committed and collection.status == "indexed"
    assert result["chunk_count"] == len(rows) == 2
    assert {row.metadata_json["type"] for row in rows} == {"fact", "recommendation"}
    assert str(collection.documents[0].id) in index.config["guideline_runs"]
    assert all(point["payload"]["atomic_chunk_id"] for point in qdrant_points)
    assert all(point["payload"]["topic_tags"] for point in qdrant_points)
