"""Opt-in live integration check using only synthetic literature, with cleanup."""

import json
import time
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


BASE = "http://localhost:8080"
API = f"{BASE}/api/knowledge/api/v1"
TEXT = "# 脑出血概述\n\n脑出血是脑实质内出血。\n\n## 推荐意见\n\n推荐意见：应监测患者血压（Ⅰ级推荐，A级证据）。\n"


def successful(response):
    assert response.ok, (response.status, response.text()[:1500])
    return response.json() if response.status != 204 else None


def wait_task(request, task_id, timeout=240):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        task = successful(request.get(f"{API}/processing-tasks/{task_id}"))
        state = (task["status"], task["phase"])
        if state != last:
            print(f"Task {task_id}: {state}", flush=True)
            last = state
        if task["status"] not in {"queued", "running"}:
            assert task["status"] == "completed", task.get("error_message")
            return task
        time.sleep(1)
    raise AssertionError(f"Task {task_id} did not finish")


def main():
    output = Path("test-results/guideline-chunker-e2e")
    output.mkdir(parents=True, exist_ok=True)
    name = f"指南切块集成验证-{uuid.uuid4().hex[:8]}"
    ids = {"document": None, "method": None, "knowledge_base": None, "tasks": []}
    report = {"name": name}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        request = context.request
        try:
            status = successful(request.get(f"{API}/configurations/guideline-chunker"))
            assert status["api_key_configured"] and status["topic_count"] == 102
            uploaded = successful(request.post(f"{API}/documents/from-file", multipart={
                "file": {"name": f"{name}.md", "mimeType": "text/markdown", "buffer": TEXT.encode("utf-8")},
            }))
            ids["document"] = uploaded["id"]
            print("Synthetic document uploaded", flush=True)

            page.goto(f"{BASE}/knowledge/")
            page.wait_for_load_state("networkidle")
            page.get_by_role("link", name="模型注册", exact=True).click()
            expect(page.locator(".registry-row").filter(has_text="医学指南原子知识分块")).to_be_visible()
            page.get_by_role("button", name="注册分块方法", exact=True).click()
            dialog = page.get_by_role("dialog", name="注册分块方法")
            dialog.get_by_placeholder("如：指南标题递归分块").fill(name)
            dialog.get_by_role("button", name="医学指南原子知识", exact=False).click()
            dialog.get_by_role("button", name="下一步", exact=True).click()
            expect(dialog.locator("label").filter(has_text="批次目标字符").locator("input")).to_have_value("2600")
            expect(dialog.locator("label").filter(has_text="发布索引前必须通过质量门").locator("input")).to_be_checked()
            page.screenshot(path=str(output / "parameters-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 375, "height": 850})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(output / "parameters-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})
            dialog.get_by_role("button", name="下一步", exact=True).click()
            dialog.locator(".preview-controls select").select_option(ids["document"])
            with page.expect_response(lambda response: response.url.endswith("/configurations/chunking-methods/preview") and response.request.method == "POST") as queued:
                dialog.get_by_role("button", name="生成预览", exact=True).click()
            assert queued.value.status == 202
            task_id = queued.value.json()["id"]
            ids["tasks"].append(task_id)
            print("Semantic preview queued", flush=True)
            wait_task(request, task_id)
            expect(dialog.locator(".preview-stat-grid")).to_be_visible(timeout=30000)
            result = successful(request.get(f"{API}/processing-tasks/{task_id}/result"))
            assert result["processing_report"]["quality_gate_passed"]
            assert {chunk["metadata"]["type"] for chunk in result["chunks"]} == {"fact", "recommendation"}
            assert all(TEXT[chunk["char_start"]:chunk["char_end"]] == chunk["content"] for chunk in result["chunks"])
            expect(dialog.locator(".chunk-semantic-meta")).to_have_count(2)
            expect(dialog.get_by_text("质量门已通过", exact=True)).to_be_visible()
            for relative in result["artifact_urls"].values():
                assert request.get(f"{BASE}/api/knowledge{relative}").ok
            page.screenshot(path=str(output / "semantic-preview.png"), full_page=True)
            report["preview"] = {"chunks": len(result["chunks"]), "quality_gate_passed": True, "run_id": result["run_id"]}
            with page.expect_response(lambda response: response.url.endswith("/configurations/chunking-methods") and response.request.method == "POST") as registered:
                dialog.get_by_role("button", name="保存分块方法", exact=True).click()
            method = successful(registered.value)
            ids["method"] = method["id"]
            assert method["strategy"] == "guideline_semantic"
            expect(dialog).not_to_be_visible()

            models = successful(request.get(f"{API}/configurations/embedding-models"))
            medcpt = next(model for model in models if model["provider"] == "medcpt")
            knowledge = successful(request.post(f"{API}/knowledge-bases", data={
                "name": name, "document_ids": [ids["document"]],
                "chunking_method_id": ids["method"], "embedding_model_id": medcpt["id"],
            }))
            ids["knowledge_base"] = knowledge["id"]
            assert knowledge["status"] == "configured"
            page.get_by_role("link", name="知识库管理", exact=True).click()
            # Refresh the dataset after creating this fixture through the API.
            page.reload()
            page.wait_for_load_state("networkidle")
            page.locator(".library-card").filter(has_text=name).get_by_role("button", name="打开知识库", exact=False).click()
            with page.expect_response(lambda response: response.url.endswith(f"/knowledge-bases/{knowledge['id']}/build")) as built:
                page.get_by_role("button", name="构建向量索引", exact=True).click()
            assert built.value.status == 202
            build_id = built.value.json()["id"]
            ids["tasks"].append(build_id)
            print("MedCPT index build queued", flush=True)
            wait_task(request, build_id)
            chunks = successful(request.get(f"{API}/knowledge-bases/{knowledge['id']}/documents/{ids['document']}/chunks"))["chunks"]
            assert len(chunks) == 2
            assert all(chunk["metadata"]["quality_gate_passed"] for chunk in chunks)
            search = successful(request.post(f"{API}/knowledge-bases/{knowledge['id']}/search", data={
                "query": "脑出血患者血压监测", "strategy": "hybrid", "top_k": 2, "use_reranker": False,
            }, timeout=120000))
            assert search["items"]
            assert all(hit["metadata"]["chunk_id"] for hit in search["items"])
            report["index"] = {"chunks": len(chunks), "search_hits": len(search["items"]), "metadata_preserved": True}
            page.locator(".knowledge-document").first.click()
            expect(page.locator(".chunk-block .chunk-semantic-meta")).to_have_count(2)
            page.screenshot(path=str(output / "knowledge-chunks.png"), full_page=True)
            page.get_by_role("link", name="解析任务", exact=True).click()
            task_row = page.locator(".task-list article").filter(has_text=name).filter(has_text="医学指南语义切块")
            task_row.get_by_role("button", name="查看切块结果", exact=True).click()
            result_dialog = page.get_by_role("dialog").filter(has=page.locator("#guideline-result-title"))
            expect(result_dialog.locator(".chunk-semantic-meta")).to_have_count(2)
            page.screenshot(path=str(output / "task-results.png"), full_page=True)
            assert not errors, errors
            report["page_errors"] = errors
            print("PASS: UI registration, live DeepSeek, durable task, MedCPT, Qdrant search and artifacts", flush=True)
        finally:
            cleanup = {}
            if ids["knowledge_base"]:
                cleanup["knowledge_base"] = request.delete(f"{API}/knowledge-bases/{ids['knowledge_base']}").status
            if ids["method"]:
                cleanup["method"] = request.delete(f"{API}/configurations/chunking-methods/{ids['method']}").status
            if ids["document"]:
                cleanup["document"] = request.delete(f"{API}/documents/{ids['document']}").status
            cleanup["tasks"] = [request.delete(f"{API}/processing-tasks/{task_id}").status for task_id in ids["tasks"]]
            report["cleanup"] = cleanup
            report["fixture_ids"] = ids
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
            print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
