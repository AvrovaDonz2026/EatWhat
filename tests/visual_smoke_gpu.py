"""用真实 Chrome（WebGPU 可用）截图，复核设计改版效果。"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.acceptance_gates.test_privacy_and_zero_cookie import (  # noqa: E402
    DIST,
    _find_full_chromium,
    _free_port,
)

from playwright.sync_api import sync_playwright  # noqa: E402

FULL = _find_full_chromium()
OUT = Path("/tmp/ppd-shots2")


def main() -> int:
    OUT.mkdir(exist_ok=True)
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
            browser = api.chromium.launch(
                executable_path=FULL,
                args=["--enable-unsafe-webgpu", "--use-angle=metal"],
            )
            # 2x DPR 用来验证 devicePixelRatio 上限 1.5
            page = browser.new_page(viewport={"width": 1440, "height": 980}, device_scale_factor=2)
            page.goto(f"http://127.0.0.1:{port}", wait_until="load")
            page.wait_for_timeout(1500)

            print("dpr:", page.evaluate("window.devicePixelRatio"))
            print(
                "canvas px:",
                page.evaluate(
                    "[document.querySelector('.weave-canvas').width, document.querySelector('.weave-canvas').height]"
                ),
            )
            print("weave mode:", page.evaluate("document.querySelector('.weave-host').dataset.weaveMode"))

            page.screenshot(path=str(OUT / "01-scan.png"))
            page.get_by_role("button", name="禾丰全脂乳粉 (6901234567892)").click()
            page.wait_for_timeout(1000)
            page.screenshot(path=str(OUT / "02-scan-hit.png"))
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / "03-scan-ingredients.png"))

            page.get_by_role("button", name="看原始记录与出处").click()
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / "04-dossier-top.png"))
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / "05-dossier-blocks.png"))
            page.mouse.wheel(0, 2200)
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / "06-dossier-blocks2.png"))

            page.get_by_role("button", name="我的忌口与饮食计划").click()
            page.wait_for_timeout(700)
            page.screenshot(path=str(OUT / "07-diet.png"))

            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
