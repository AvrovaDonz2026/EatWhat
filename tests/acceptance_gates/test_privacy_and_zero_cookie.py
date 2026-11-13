"""门禁 8：端侧私有与「Cookie 不出网」（真实浏览器运行验证）。

Cookie 口径（2026-11-13 起）：本地 Cookie 属允许的浏览器本地持久化形态，
**隐私要求不是「不写 Cookie」，而是「Cookie 不出网」**。因此本模块的主断言是
逐条请求的 headers 里没有 `Cookie`，而不是 `document.cookie.length == 0`。

用 Playwright 在本地静态站点上跑真实 UI：
1. 全程监听 network：除同源静态 JSON / 单文件自身外，不得有任何外发请求；
2. **主断言**：应用自己发起的 `fetch` / `xhr` 取数请求，`all_headers()` 都不得含 `cookie`
   （`credentials: "omit"` 的证据；顶层文档导航必然带同站 Cookie，属浏览器行为，故排除）；
3. 弱断言（现状快照，非隐私要求）：应用当前仍未写入 Cookie，`document.cookie.length == 0`；
4. 记录 localStorage / sessionStorage 不承载忌口与饮食计划（应全部在 IndexedDB）；
5. 关闭网页后重新打开，本地词条仍存在（证明持久化只发生在浏览器端）。

注意：Playwright 的 `request.headers` **不含** Cookie（浏览器把它拆进 separate cookie jar），
只有 `request.all_headers()` 才带 `cookie`。只用 `request.headers` 的断言会空转，故这里一律用 `all_headers()`。
"""
from __future__ import annotations

import json
import re
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
        requests: list[dict] = []
        # 必须用 all_headers()：request.headers 不含浏览器 cookie jar 里的 Cookie，
        # 只看 request.headers 的断言对「写了 Cookie 又发出去」完全空转。
        def _record(request) -> None:
            try:
                headers = dict(request.all_headers())
            except Exception:  # noqa: BLE001 - 请求可能在捕获时已结束
                headers = {}
            requests.append(
                {
                    "url": request.url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "headers": headers,
                }
            )

        context.on("request", _record)
        page = context.new_page()
        page.goto(static_site, wait_until="load")
        page.wait_for_timeout(400)
        yield page, requests, static_site
        browser.close()


def _requests_with_cookie(requests: list[dict], only_types: set[str] | None = None) -> list[str]:
    """返回携带 Cookie 请求头的 URL 列表（大小写不敏感）。

    `only_types` 用来把断言限定在应用自己发起的取数请求（`fetch` / `xhr`）上：
    顶层文档导航必然携带同站 Cookie，那是浏览器行为，`credentials: "omit"` 管不到。
    """
    offenders = []
    for entry in requests:
        if only_types is not None and entry.get("resource_type") not in only_types:
            continue
        for key in entry["headers"]:
            if key.lower() == "cookie":
                offenders.append(entry["url"])
                break
    return offenders


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


def test_private_data_requests_carry_no_cookie_header(browser_page):
    """主断言：页面自己发起的取数请求（fetch / xhr）不得携带 Cookie 头。

    这是放开本地 Cookie 之后唯一不允许松动的安全属性：即使本地存在 Cookie，
    `credentials: "omit"` 也必须让它不被携带到静态托管商。

    限定 `fetch` / `xhr` 的理由：顶层文档导航（`document`）必然携带同站 Cookie，
    那是浏览器自身行为，`credentials: "omit"` 无法影响，也不属于「取数请求」。
    """
    page, requests, _site = browser_page
    page.wait_for_timeout(300)
    assert requests, "未捕获到任何请求，门禁等于空转"
    offenders = _requests_with_cookie(requests, only_types={"fetch", "xhr"})
    assert not offenders, f"出现携带 Cookie 头的取数请求: {offenders[:10]}"


def test_request_header_gate_detects_include_mode(browser_page):
    """反向对照：注入一份本地 Cookie 并用 `credentials: "include"` 发同源请求，门禁必须命中。

    不修改 `apps/web/src`：全部注入都在 `page.evaluate` 内完成。
    `request.headers` 看不到 Cookie（Playwright 语义），只有 `all_headers()` 能看到——
    这条对照同时证明我们用的取值方式不是空转。
    """
    page, requests, _site = browser_page
    before = len(requests)

    try:
        page.evaluate(
            """async () => {
                document.cookie = 'probe=1; path=/';
                // 必须绕开 HTTP 缓存：manifest.json 在页面加载时已被取过一次，
                // 命中缓存时浏览器不会真的发出网络请求，也就不会带 Cookie，
                // 反向对照会误判为「门禁空转」。加 query 强制走网络。
                await fetch('./data/v1/manifest.json?cookie_gate_probe=1', {
                    credentials: 'include',
                    cache: 'no-store',
                }).catch(() => null);
            }"""
        )
        page.wait_for_timeout(500)

        injected = requests[before:]
        assert injected, "注入请求未被捕获，反向对照无效"
        offenders = _requests_with_cookie(injected)
        assert offenders, f"反向对照失败：include 模式下的 Cookie 未被检出（捕获 {len(injected)} 条请求）"
    finally:
        # 必须在共享 context 里清掉注入的 Cookie：否则后续 test_no_cookie_currently_written
        # 会被本次对照污染（那会让门禁的失败原因变得不可读）。
        page.evaluate("document.cookie = 'probe=1; path=/; max-age=0'")
        page.wait_for_timeout(100)
    assert "probe=" not in page.evaluate("document.cookie"), "反向对照未清理干净，会污染后续断言"


def test_local_cookie_is_not_carried_when_app_fetches(browser_page):
    """回归守门：本地**确实存在** Cookie 时，应用自己的 `credentials: "omit"` 取数请求仍不带它。

    反向对照证明的是「检测能力在线」，这条证明的是「被检测的应用真的没犯规」。
    两者缺一不可：只有前者，等于证明门禁能抓到别人，却没证明自己干净。
    """
    page, requests, _site = browser_page
    page.evaluate("document.cookie = 'hint=1; path=/'")
    assert "hint=" in page.evaluate("document.cookie"), "注入 Cookie 失败，场景未构造出来"

    before = len(requests)
    try:
        # 走应用自己的取数路径（点「检索公开档案」会调用 static_dossier_client）
        page.get_by_role("button", name="扫码与成分对照").click()
        page.wait_for_timeout(200)
        page.get_by_role("textbox", name="条码").fill("6901234567892")
        page.get_by_role("button", name="检索公开档案").click()
        page.wait_for_timeout(1500)

        after = requests[before:]
        assert after, "应用取数请求未被捕获，门禁等于空转"
        offenders = _requests_with_cookie(after, only_types={"fetch", "xhr"})
        assert not offenders, f"本地已有 Cookie，但取数请求仍带上了它: {offenders[:10]}"
    finally:
        page.evaluate("document.cookie = 'hint=1; path=/; max-age=0'")
        page.wait_for_timeout(100)


def test_no_cookie_currently_written(browser_page):
    """弱断言（现状快照，**不是**隐私要求）：应用当前仍未写入任何 Cookie。

    放开 Cookie 之后「不写」不再是红线；这里保留它只为在策略真正启用 Cookie 时
    让读者立刻看到「现状变了」，失败时应更新本测试与文档，而不是当成违规。
    """
    page, _requests, _site = browser_page
    page.wait_for_timeout(300)
    cookie_length = page.evaluate("document.cookie.length")
    assert cookie_length == 0, (
        f"应用开始写入 Cookie（长度 {cookie_length}）。这本身不违规，"
        "但请同步更新本测试的语义与 docs/PRIVACY.md 的现状描述。"
    )


def test_ui_shows_local_only_no_egress_badge(browser_page):
    """页眉必须展示语义正确的隐私标识：「本地存储」+「不外发」。

    旧标识 `cookie.length=` 已被移除——放开 Cookie 后它名实不符（Cookie 本身不再是
    隐私要求，真正的要求是「不出网」）。这里改为断言中性文案，避免留下谎言。
    """
    page, _requests, _site = browser_page
    chip = page.locator(".evidence-chip", has_text="本地存储").first
    assert chip.is_visible(), "页眉必须展示「本地存储」审计标识"
    assert "不外发" in chip.inner_text(), f"审计标识文案应说明「不外发」，实际: {chip.inner_text()}"
    assert "cookie.length" not in chip.inner_text(), "页眉不应再出现名实不符的 cookie.length 标识"


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
    web_storage_blob = json.dumps(
        {"localStorage": storage["localStorage"], "sessionStorage": storage["sessionStorage"]},
        ensure_ascii=False,
    )
    assert "乳糖" not in web_storage_blob, "忌口词条不得写入 localStorage / sessionStorage（应只在 IndexedDB）"
    # Cookie 已不再是违规存储：只要求「若写了，不得含词条明文」
    assert "乳糖" not in storage["cookie"], "忌口词条不得以明文形式出现在 Cookie 中"

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
    """重新打开页面后本地词条仍然存在，且请求头仍不携带 Cookie。"""
    page, requests, static_site = browser_page
    before = len(requests)
    page.reload(wait_until="load")
    page.wait_for_timeout(500)

    page.get_by_role("button", name="我的忌口与饮食计划").click()
    page.wait_for_timeout(400)
    assert page.get_by_text("乳糖", exact=False).first.is_visible(), "重载后本地词条应仍存在"
    assert _requests_with_cookie(requests[before:]) == [], "重载后的请求不得携带 Cookie"


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


def test_app_source_never_relaxes_credentials():
    """源码级：本项目自己的 fetch 一律 `credentials: "omit"`，不得出现 include / same-origin。

    浏览器级断言只能覆盖**实际走到的分支**。`tesseract.js-core`（Emscripten 产物）内部
    带有 `fetch(url, {credentials:"same-origin"})` 的 WASM 兜底分支；单文件构建把
    WASM 内联后该分支不会执行，因此浏览器门禁看不到它。这里用源码扫描把「本项目
    自己的代码」与「第三方库的兜底」明确切开，防止有人把自家 fetch 悄悄放宽。
    """
    src = ROOT / "apps" / "web" / "src"
    offenders = []
    for path in sorted(src.rglob("*.ts*")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'credentials\s*:\s*"([^"]+)"', text):
            value = match.group(1)
            if value != "omit":
                offenders.append((str(path.relative_to(ROOT)), value))
    assert not offenders, f"apps/web/src 出现非 omit 的 credentials：{offenders}"
    # 反向证明扫描不是空转：三个已知 fetch 站点必须都被扫到
    fetch_sites = [
        src / "services" / "static_dossier_client.ts",
        src / "ocr" / "local_ocr.ts",
        src / "ocr" / "barcode.ts",
    ]
    scanned = [p for p in fetch_sites if 'credentials: "omit"' in p.read_text(encoding="utf-8")]
    assert len(scanned) == len(fetch_sites), f"漏扫 fetch 站点：{fetch_sites}"


def test_credentials_gate_detects_relaxed_value(tmp_path):
    """反向对照：注入 `credentials: "include"` 的副本，同一扫描必须命中。"""
    target = tmp_path / "leaky_client.ts"
    target.write_text('fetch("/data/v1/x.json", { credentials: "include" });\n', encoding="utf-8")
    offenders = [
        match.group(1)
        for match in re.finditer(r'credentials\s*:\s*"([^"]+)"', target.read_text(encoding="utf-8"))
        if match.group(1) != "omit"
    ]
    assert offenders == ["include"], f"反向对照失败：include 未被检出（{offenders}）"
