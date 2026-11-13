"""生成界面截图，用于交付验收与设计复核（不属于门禁断言）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.acceptance_gates._browser_launch import find_full_chromium  # noqa: E402
from tests.acceptance_gates.test_privacy_and_zero_cookie import (  # noqa: E402
    DIST,
    _free_port,
)

import subprocess  # noqa: E402
import socket  # noqa: E402
import time  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

OUT = Path("/tmp/ppd-shots")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    if not (DIST / "index.html").exists():
        print("请先构建 apps/web/dist")
        return 1

    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(DIST)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.1)

        with sync_playwright() as api:
            # 助手在 2026-11 从 test_privacy_and_zero_cookie 移到 _browser_launch，
            # 旧的 `_find_chromium_executable` 名字已不存在，这里跟随新名字。
            executable = find_full_chromium()
            browser = api.chromium.launch(executable_path=executable) if executable else api.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            def shot(name: str, *, full: bool = False) -> None:
                page.screenshot(path=str(OUT / name), full_page=full)

            page.goto(f"http://127.0.0.1:{port}", wait_until="load")
            page.wait_for_timeout(600)
            shot("01-scan-empty.png")

            page.get_by_role("button", name="禾丰全脂乳粉 (6901234567892)").click()
            page.wait_for_timeout(900)
            shot("02a-scan-hit-top.png")
            page.mouse.wheel(0, 700)
            page.wait_for_timeout(400)
            shot("02b-scan-coverage.png")
            page.mouse.wheel(0, 800)
            page.wait_for_timeout(400)
            shot("02c-scan-ingredients.png")

            page.get_by_role("button", name="看原始记录与出处").click()
            page.wait_for_timeout(900)
            shot("03a-dossier-top.png")
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(400)
            shot("03b-dossier-blocks.png")
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(400)
            shot("03c-dossier-blocks2.png")

            page.get_by_role("button", name="我的忌口与饮食计划").click()
            page.wait_for_timeout(500)
            shot("04-diet.png")

            page.get_by_placeholder("忌口词条，例如：乳糖 / 花生").fill("乳糖")
            page.get_by_role("button", name="添加本地词条").click()
            page.wait_for_timeout(600)
            shot("05-diet-with-rule.png")

            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
