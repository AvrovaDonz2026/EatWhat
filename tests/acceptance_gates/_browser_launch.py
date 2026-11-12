"""浏览器门禁的公共启动助手。

背景：`chrome-headless-shell` 在 macOS arm64 上偶发 `SEGV_ACCERR` 启动崩溃
（pid 启动即 signal 11），会让整组浏览器门禁变成 setup ERROR 而非明确失败，
既污染 CI 又难以区分「真回归」与「环境抖动」。

这里提供两条路径：
1. 优先使用带合成器的完整 Chromium（稳定，且能验证 WebGPU）；
2. 回退时才用 headless shell，并做有限次重启重试。
"""
from __future__ import annotations

import os
import time
from pathlib import Path


def find_full_chromium() -> str | None:
    """定位完整 Chromium / Chrome for Testing（非 headless shell）。"""
    roots = [
        Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")).expanduser()
        if os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        else None,
        Path.home() / "Library" / "Caches" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    for root in roots:
        if not root or not root.exists():
            continue
        for pattern in (
            "chromium-*/chrome-mac-arm64/*.app/Contents/MacOS/*",
            "chromium-*/chrome-linux64/chrome",
            "chromium-*/chrome-mac/*.app/Contents/MacOS/*",
        ):
            for candidate in sorted(root.glob(pattern)):
                if (
                    candidate.is_file()
                    and os.access(candidate, os.X_OK)
                    and "headless_shell" not in candidate.name
                ):
                    return str(candidate)
    return None


def launch_chromium(api, *, attempts: int = 3, args: list[str] | None = None):
    """启动 Chromium：完整版优先，失败时有限重试；返回 (browser, executable_path)。"""
    launch_args = list(args or [])
    candidates: list[str | None] = [find_full_chromium(), None]
    last_error: Exception | None = None

    for executable in candidates:
        for attempt in range(attempts):
            kwargs: dict = {"args": launch_args}
            if executable:
                kwargs["executable_path"] = executable
            try:
                return api.chromium.launch(**kwargs), executable
            except Exception as error:  # noqa: BLE001 - 需要吞掉启动期 SEGV 后重试
                last_error = error
                time.sleep(0.4 * (attempt + 1))
        # 该候选连续失败，换下一个候选（完整版 → Playwright 默认）

    assert last_error is not None
    raise last_error
