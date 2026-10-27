from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "http://localhost:8080"


def main() -> None:
    output_dir = Path("test-results/chunk-markdown")
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        console_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)

        page.goto(f"{BASE_URL}/knowledge/")
        page.wait_for_load_state("networkidle")
        page.get_by_role("link", name="知识库管理", exact=True).click()
        page.locator(".library-card").first.wait_for(state="visible")
        page.locator(".library-card .text-action").first.click()
        page.locator(".knowledge-document").first.click()
        page.locator(".chunk-block").first.wait_for(state="visible")

        chunk = page.locator(".chunk-block").filter(has_text="院前与急诊室的急救管理").first
        chunk.wait_for(state="visible")
        assert chunk.locator("h2").inner_text() == "三、院前与急诊室的急救管理"
        assert chunk.locator("ol > li").count() == 3
        assert chunk.locator(".docvortex-page-footnote").count() == 2
        assert chunk.locator("script, iframe, style").count() == 0
        assert chunk.locator("[style]").count() == 0
        assert "字符 741–1892" in chunk.inner_text()
        assert not console_errors, console_errors

        chunk.screenshot(path=output_dir / "emergency-management-chunk.png")
        browser.close()

    print("PASS: chunk Markdown renders heading, ordered list, and sanitized footnotes")


if __name__ == "__main__":
    main()
