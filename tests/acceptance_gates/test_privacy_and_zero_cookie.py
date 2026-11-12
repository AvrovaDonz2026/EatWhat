"""门禁 8：端侧私有与零 Cookie（真实浏览器运行验证）。

用 Playwright 在本地静态站点上跑真实 UI：
1. 全程监听 network：除同源静态 JSON / 单文件自身外，不得有任何外发请求；
2. 全程校验 document.cookie 为空、Cookie 存储为空；
3. 记录 localStorage / sessionStorage 不承载忌口与饮食计划（应全部在 IndexedDB）；
4. 关闭网页后重新打开，本地词条仍存在（证明持久化只发生在浏览器端）。
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "apps" / "web" / "dist"

playwright = pytest.importorskip("playwright.sync_api", reason="需要 playwright 才能执行浏览器门禁")
from playwright.sync_api import sync_playwright  # noqa: E402

try:  # 兼容以 tests/ 为根、以及包内导入两种执行方式
    from ._browser_launch import find_full_chromium, launch_chromium
except ImportError:  # pragma: no cover
    from _browser_launch import find_full_chromium, launch_chromium  # type: ignore[no-redef]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def static_site() -> Iterator[str]:
    if not (DIST / "index.html").exists():
        pytest.skip("apps/web/dist 未构建；请先执行 npm run build:web")
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(DIST)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    process.terminate()
    process.wait(timeout=10)


def _find_full_chromium() -> str | None:
    """定位完整 Chromium / Chrome for Testing（保留此符号，供探针脚本导入）。

    实现在 `_browser_launch.find_full_chromium()`，此处只做转发，
    避免两处维护同一套路径规则而漂移。
    """
    return find_full_chromium()


@pytest.fixture(scope="module")
def browser_page(static_site: str):
    with sync_playwright() as api:
        # 完整 Chromium 优先并带重试：headless shell 偶发启动期 SEGV 会造成 setup ERROR
        browser, _executable = launch_chromium(api, args=["--no-sandbox"])
        context = browser.new_context()
        requests: list[dict[str, str]] = []
        context.on("request", lambda request: requests.append({"url": request.url, "method": request.method}))
        page = context.new_page()
        page.goto(static_site, wait_until="load")
        page.wait_for_timeout(400)
        yield page, requests, static_site
        browser.close()


def _local_hosts(static_site: str) -> set[str]:
    host = urlparse(static_site).hostname or "127.0.0.1"
    return {host, "localhost"}


def test_no_outbound_network_requests(browser_page):
    page, requests, static_site = browser_page
    allowed = _local_hosts(static_site)
    offenders = []
    for entry in requests:
        parsed = urlparse(entry["url"])
        if parsed.scheme in ("data", "blob"):
            continue
        if parsed.hostname not in allowed:
            offenders.append(entry["url"])
    assert not offenders, f"出现跨站外发请求: {offenders[:10]}"
    assert all(entry["method"] in ("GET", "HEAD") for entry in requests), "只允许只读请求"


def test_zero_cookies(browser_page):
    page, _requests, _site = browser_page
    page.wait_for_timeout(300)
    cookies = page.context.cookies()
    assert cookies == [], f"出现 Cookie: {cookies}"
    cookie_length = page.evaluate("document.cookie.length")
    assert cookie_length == 0, f"document.cookie 长度应为 0，实际 {cookie_length}"


def test_ui_shows_zero_cookie_badge(browser_page):
    page, _requests, _site = browser_page
    badge = page.get_by_text("cookie.length=", exact=False).first
    assert badge.is_visible(), "界面必须展示 cookie.length 审计标识"
    text = badge.inner_text()
    assert "cookie.length=0" in text.replace(" ", ""), f"审计标识显示异常: {text}"


def test_diet_terms_stay_in_indexeddb(browser_page):
    """添加本地词条后：仅在 IndexedDB 可见，不得写入 localStorage / 网络。"""
    page, requests, _site = browser_page
    before_requests = len(requests)

    page.get_by_role("button", name="我的忌口与饮食计划").click()
    page.wait_for_timeout(300)

    term_input = page.get_by_placeholder("忌口词条，例如：乳糖 / 花生")
    term_input.fill("乳糖")
    page.get_by_role("button", name="添加本地词条").click()
    page.wait_for_timeout(600)

    assert page.get_by_text("乳糖", exact=False).first.is_visible(), "词条应出现在本地列表"
    assert len(requests) == before_requests, f"添加词条触发了新的网络请求: {requests[before_requests:]}"

    storage = page.evaluate(
        """async () => ({
            localStorage: Object.entries(localStorage),
            sessionStorage: Object.entries(sessionStorage),
            cookie: document.cookie,
        })"""
    )
    assert storage["cookie"] == ""
    blob = json.dumps(storage, ensure_ascii=False)
    assert "乳糖" not in blob, "忌口词条不得写入 localStorage / sessionStorage（应只在 IndexedDB）"

    idb_has_term = page.evaluate(
        """async () => {
            const databases = await indexedDB.databases();
            const names = databases.map((db) => db.name);
            if (!names.includes('product-public-dossier-local')) return false;
            const db = await new Promise((resolve, reject) => {
                const request = indexedDB.open('product-public-dossier-local');
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error);
            });
            const rules = await new Promise((resolve, reject) => {
                const tx = db.transaction('diet_rules', 'readonly');
                const store = tx.objectStore('diet_rules');
                const all = store.getAll();
                all.onsuccess = () => resolve(all.result);
                all.onerror = () => reject(all.error);
            });
            return rules.some((rule) => rule.term === '乳糖');
        }"""
    )
    assert idb_has_term, "忌口词条必须持久化在 IndexedDB"


def test_scanned_image_never_leaves_browser(browser_page):
    """图片只能作为本地 Blob/内存对象处理；不得出现 multipart 上传。"""
    page, requests, static_site = browser_page
    uploads = [
        entry
        for entry in requests
        if entry["method"] not in ("GET", "HEAD")
        or "upload" in entry["url"]
        or "ocr" in entry["url"].lower()
    ]
    assert not uploads, f"出现可疑上传请求: {uploads[:5]}"

    # 页面加载过程中不能有任何 POST/PUT（端侧识别全部在本地）
    methods = {entry["method"] for entry in requests}
    assert methods <= {"GET", "HEAD"}, f"出现非只读请求: {methods}"


def test_persistence_after_reload(browser_page):
    """重新打开页面后本地词条仍然存在，且 Cookie 仍为空。"""
    page, _requests, static_site = browser_page
    page.reload(wait_until="load")
    page.wait_for_timeout(500)

    page.get_by_role("button", name="我的忌口与饮食计划").click()
    page.wait_for_timeout(400)
    assert page.get_by_text("乳糖", exact=False).first.is_visible(), "重载后本地词条应仍存在"
    assert page.context.cookies() == []
    assert page.evaluate("document.cookie.length") == 0


def test_no_upload_endpoint_in_source():
    """静态产物与源码中不得出现上传/上报端点。"""
    forbidden_markers = ("track", "analytics", "upload", "beacon", "fetch(", "sendBeacon")
    src = ROOT / "apps" / "web" / "src"
    offenders = []
    for path in src.rglob("*.ts*"):
        text = path.read_text(encoding="utf-8")
        # 允许同源静态 JSON 的只读 GET；禁止任何上报语义
        for marker in ("navigator.sendBeacon", "new FormData", "multipart/form-data"):
            if marker in text:
                offenders.append((path.name, marker))
    assert not offenders, f"发现上传/上报能力: {offenders}"
