from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "http://localhost:8080"
APPS = (
    ("知识空间", "/knowledge/"),
    ("智能问答", "/agent/"),
    ("评测中心", "/evaluation/"),
)


def assert_switcher(page, active_label: str) -> None:
    switcher = page.get_by_role("navigation", name="平台应用切换")
    switcher.wait_for(state="visible")
    links = switcher.get_by_role("link")
    assert links.count() == 3
    assert [links.nth(index).inner_text() for index in range(3)] == [item[0] for item in APPS]
    assert [links.nth(index).get_attribute("href") for index in range(3)] == [item[1] for item in APPS]
    assert switcher.locator('[aria-current="page"]').inner_text() == active_label
    viewport = page.evaluate(
        """(() => {
          const offenders = [...document.querySelectorAll('*')]
            .map((node) => ({
              tag: node.tagName,
              className: typeof node.className === 'string' ? node.className : '',
              right: Math.round(node.getBoundingClientRect().right),
              width: Math.round(node.getBoundingClientRect().width),
            }))
            .filter((item) => item.right > window.innerWidth + 1)
            .slice(0, 8)
          return { scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth, offenders }
        })()"""
    )
    assert viewport["scrollWidth"] <= viewport["innerWidth"] + 1, f"{active_label}: {viewport}"


def main() -> None:
    output_dir = Path("test-results/platform-navigation")
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        for label, path in APPS:
            page.goto(f"{BASE_URL}{path}")
            page.wait_for_load_state("networkidle")
            assert_switcher(page, label)
            page.screenshot(path=output_dir / f"{path.strip('/')}--desktop.png")

        page.goto(f"{BASE_URL}/knowledge/")
        page.wait_for_load_state("networkidle")
        for label, path in (APPS[1], APPS[2], APPS[0]):
            page.get_by_role("navigation", name="平台应用切换").get_by_role(
                "link", name=label, exact=True
            ).click()
            page.wait_for_url(f"**{path}")
            page.wait_for_load_state("networkidle")
            assert_switcher(page, label)

        mobile = browser.new_page(viewport={"width": 375, "height": 812})
        for label, path in APPS:
            mobile.goto(f"{BASE_URL}{path}")
            mobile.wait_for_load_state("networkidle")
            if label == "智能问答":
                mobile.get_by_role("button", name="打开会话列表").click()
                mobile.wait_for_timeout(350)
            assert_switcher(mobile, label)
            mobile.screenshot(path=output_dir / f"{path.strip('/')}--mobile.png")

        browser.close()

    print("PASS: three platform switchers render and cross-app navigation completes")


if __name__ == "__main__":
    main()
