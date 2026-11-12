"""浏览器门禁的公共启动助手。

背景：`chrome-headless-shell` 在 macOS arm64 上偶发 `SEGV_ACCERR` 启动崩溃
（pid 启动即 signal 11），会让整组浏览器门禁变成 setup ERROR 而非明确失败，
既污染 CI 又难以区分「真回归」与「环境抖动」。

这里提供两条路径：
1. 优先使用带合成器的完整 Chromium（稳定，且能验证 WebGPU）；
2. 回退时才用 headless shell，并做有限次重启重试。
"""
from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
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


def launch_chromium(api, *, attempts: int = 4, args: list[str] | None = None):
    """启动 Chromium：完整版优先，失败时有限重试；返回 (browser, executable_path)。

    重试存在的理由：整组浏览器门禁会在同一进程里反复启动内核，macOS 上偶发
    启动期 SEGV 会让所有依赖它的 fixture 一起变成 setup ERROR（曾实测一次
    跑出 29 errors，重跑即 100 passed）。这里用更长退避 + 更多次尝试，
    把「环境抖动」与「真回归」区分开。
    """
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
                time.sleep(0.5 * (attempt + 1))
        # 该候选连续失败，换下一个候选（完整版 → Playwright 默认）

    assert last_error is not None
    raise last_error


@contextlib.contextmanager
def static_site_server(dist: Path):
    """起一个一次性静态站点服务，并保证退出时回收。

    此前多处 fixture 只写 `server.terminate()`，当浏览器在 yield 之前崩溃时
    该清理会被跳过，导致残留 `http.server` 进程堆积（实测一次留下 7 个），
    进而拖垮后续整组门禁。这里用 contextmanager + wait 兜底。
    """
    if not (dist / "index.html").exists():
        raise FileNotFoundError(f"{dist}/index.html 不存在；请先执行 npm run build:web")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    process = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(dist)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - 兜底强杀
            process.kill()
            process.wait(timeout=5)
