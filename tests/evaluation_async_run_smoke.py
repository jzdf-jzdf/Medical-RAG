"""Browser acceptance test for queued evaluation progress without a live model call."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8080").rstrip("/")
BANK_ID = "11111111-1111-4111-8111-111111111111"
COLLECTION_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"


def run_payload(status: str, completed: int):
    return {
        "id": RUN_ID,
        "bank_id": BANK_ID,
        "bank_name": "异步验收题库",
        "status": status,
        "total_questions": 2,
        "completed_questions": completed,
        "attempt_count": 1 if status != "queued" else 0,
        "cancel_requested": False,
        "error": None,
        "started_at": "2026-10-29T00:00:00Z",
        "finished_at": "2026-10-29T00:01:00Z" if status == "completed" else None,
        "results": [],
    }


def main() -> None:
    output = Path(os.environ.get("E2E_OUTPUT_DIR", "test-results")) / "evaluation-async-run"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        channel = os.environ.get("E2E_BROWSER_CHANNEL")
        browser = playwright.chromium.launch(headless=True, channel=channel)
        context = browser.new_context(viewport={"width": 1440, "height": 960})
        session = json.dumps({
            "account": "e2e@example.test",
            "displayName": "E2E",
            "signedInAt": "2026-10-29T00:00:00.000Z",
        })
        context.add_init_script(
            f"localStorage.setItem('medical-rag-session-v1', {json.dumps(session)})"
        )
        polls = {"count": 0}

        def api(route):
            request = route.request
            path = request.url.split("/api/evaluation/api/v1", 1)[-1].split("?", 1)[0]
            if path == "/question-banks":
                route.fulfill(json=[{
                    "id": BANK_ID, "name": "异步验收题库", "description": None,
                    "version": "1.0", "question_count": 2, "qa_count": 2,
                    "choice_count": 0, "created_at": "2026-10-29T00:00:00Z",
                    "updated_at": "2026-10-29T00:00:00Z",
                }])
            elif path == "/questions":
                route.fulfill(json=[])
            elif path == "/knowledge-bases":
                route.fulfill(json=[{
                    "id": COLLECTION_ID, "name": "验收知识库", "description": None,
                    "status": "indexed", "documents": [],
                }])
            elif path == "/test-runs" and request.method == "POST":
                route.fulfill(status=202, json=run_payload("queued", 0))
            elif path == "/test-runs":
                route.fulfill(json=[])
            elif path == f"/test-runs/{RUN_ID}":
                polls["count"] += 1
                route.fulfill(json=run_payload(
                    "running" if polls["count"] == 1 else "completed",
                    1 if polls["count"] == 1 else 2,
                ))
            else:
                route.fulfill(status=404, json={"detail": f"Unhandled E2E route: {path}"})

        context.route("**/api/evaluation/api/v1/**", api)
        page = context.new_page()
        page.goto(f"{BASE_URL}/evaluation/#test")
        page.wait_for_load_state("networkidle")
        page.get_by_role("heading", name="题库测试", exact=True).wait_for(state="visible")
        page.get_by_role("button", name="开始测试题库", exact=True).click()
        expect(page.get_by_text("状态：排队中", exact=True)).to_be_visible()
        expect(page.locator("progress.test-run-progress")).to_be_visible()
        expect(page.get_by_text("状态：已完成", exact=True)).to_be_visible(timeout=7000)
        expect(page.get_by_text("2 / 2 道已完成", exact=True)).to_be_visible()
        page.screenshot(path=output / "completed.png", full_page=True)
        context.close()
        browser.close()

    print("PASS: evaluation run queues immediately, polls progress, and completes")


if __name__ == "__main__":
    main()
