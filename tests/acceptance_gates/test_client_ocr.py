"""门禁 9：端侧 OCR 真实性（真实浏览器 + 真实图片 + 真实识别）。

此门禁存在的理由：先前的隐私门禁只检查「有没有外发请求」，从未真的跑过一次识别。
于是当 tesseract worker 因 `importScripts(blob:)` 被浏览器拒绝、core 与语言数据
双双加载失败时，页面不崩、不发请求、错误被 catch 吞掉，所有旧门禁依然全绿——
而端侧 OCR 实际完全不可用。这里把「能识别出正确文字」本身变成断言。

覆盖的三条不变量：
1. 能出字：上传一张中文配料表图片后，OCR 必须真的产出包含「配料」与具体配料的文本；
2. 不出网：识别全程零跨站请求（图片、worker、core、语言数据全部在本地）；
3. 不落地：零 Cookie，识别原文不写入 localStorage / sessionStorage。
"""
from __future__ import annotations

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
    from ._browser_launch import launch_chromium
except ImportError:  # pragma: no cover
    from _browser_launch import launch_chromium  # type: ignore[no-redef]

# 一张足够干净的中文包装标签：字号大、纯黑白、无背景干扰。
# 内容刻意包含「配料」锚点与致敏原段落，用来检验段落抽取是否会被营养/致敏段截断。
LABEL_HTML = """
<html><body style="margin:0;background:#fff">
<div id="label" style="width:700px;padding:28px;font-family:'Songti SC',serif;color:#111;background:#fff">
  <div style="font-size:26px;font-weight:700;margin-bottom:14px">禾丰全脂乳粉</div>
  <div style="font-size:19px;line-height:1.9">
    <b>配料：</b>全脂乳粉、乳清蛋白粉、磷脂<br>
    <b>致敏原提示：</b>含有乳及乳制品（包括乳糖）<br>
    <b>净含量：</b>400g　<b>保质期：</b>18个月<br>
    <b>贮存条件：</b>密封、置于阴凉干燥处
  </div>
</div></body></html>
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def ocr_session() -> Iterator[dict]:
    if not (DIST / "index.html").exists():
        pytest.skip("apps/web/dist 未构建；请先执行 npm run build:web")

    port = _free_port()
    server = subprocess.Popen(
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

    with sync_playwright() as api:
        # 完整 Chromium 优先并带重试：headless shell 偶发启动期 SEGV 会造成 setup ERROR
        browser, _executable = launch_chromium(api, args=["--no-sandbox"])
        context = browser.new_context()
        requests: list[str] = []
        context.on("request", lambda request: requests.append(request.url))
        page = context.new_page()

        # 1) 用浏览器自己渲染一张中文标签图（不引入任何二进制图片依赖）
        render = context.new_page()
        render.set_viewport_size({"width": 760, "height": 320})
        render.set_content(LABEL_HTML)
        render.wait_for_timeout(300)
        label_png = (ROOT / ".state" / "ocr_gate_label.png")
        label_png.parent.mkdir(parents=True, exist_ok=True)
        render.locator("#label").screenshot(path=str(label_png))
        render.close()

        page.goto(f"http://127.0.0.1:{port}/", wait_until="load")
        page.wait_for_timeout(800)

        yield {"page": page, "context": context, "requests": requests, "site": f"http://127.0.0.1:{port}", "image": label_png}

        browser.close()
    server.terminate()
    server.wait(timeout=10)


def _read_recognized_text(page) -> str:
    return page.evaluate(
        """() => {
            const areas = Array.from(document.querySelectorAll('textarea'));
            let best = '';
            for (const a of areas) if (a.value && a.value.length > best.length) best = a.value;
            return best;
        }"""
    )


def test_ocr_actually_recognizes_chinese_label(ocr_session):
    """OCR 必须真的出字：把「识别成功」本身作为断言，而不是只看有没有报错。"""
    page = ocr_session["page"]
    page.locator('input[type="file"]').first.set_input_files(str(ocr_session["image"]))

    recognized = ""
    for _ in range(60):
        page.wait_for_timeout(1000)
        recognized = _read_recognized_text(page)
        # 出现「配料」且正文里出现任一真实配料词，即为真识别成功
        if "配料" in recognized and any(word in recognized for word in ("乳粉", "乳清", "磷脂")):
            break
        if recognized and "配料" not in recognized and len(recognized) > 40:
            break  # 已产出大段文本但无配料锚点，交给下面的断言报错

    assert "配料" in recognized, f"端侧 OCR 未产出配料段落，识别结果：{recognized[:300]!r}"
    assert any(word in recognized for word in ("乳粉", "乳清", "磷脂")), (
        f"端侧 OCR 产出文本但未包含任何真实配料词：{recognized[:300]!r}"
    )


def test_ocr_ingredients_are_segmented(ocr_session):
    """识别出的配料必须被机械切分成词条列表，供本地忌口对照使用。"""
    page = ocr_session["page"]
    recognized = _read_recognized_text(page)
    assert recognized, "需要先有识别结果"

    items = page.evaluate(
        """() => {
            const nodes = Array.from(document.querySelectorAll('ol li'));
            return nodes.map((n) => n.textContent.trim()).filter(Boolean);
        }"""
    )
    assert items, f"界面未切分出任何配料词条：{recognized[:200]!r}"
    # 词条必须是「短词」而不是整段原文，否则对照会失效
    assert all(len(item) <= 40 for item in items), f"切分粒度过粗：{items[:5]}"
    assert any("乳粉" in item or "乳清" in item or "磷脂" in item for item in items), f"切分结果缺少配料: {items}"


def test_ocr_run_makes_no_cross_site_requests(ocr_session):
    """识别全程不得出现跨站请求（core / worker / 语言数据必须全部内联）。"""
    page = ocr_session["page"]
    site = ocr_session["site"]
    allowed_host = urlparse(site).hostname or "127.0.0.1"

    offenders = []
    for url in ocr_session["requests"]:
        parsed = urlparse(url)
        if parsed.scheme in ("data", "blob"):
            continue
        host = parsed.hostname
        if host not in {allowed_host, "localhost"}:
            offenders.append(url)
    assert not offenders, f"OCR 过程出现跨站请求：{offenders[:10]}"
    assert page.evaluate("document.cookie.length") == 0, "OCR 过程不得产生 Cookie"


def test_ocr_text_not_persisted_to_web_storage(ocr_session):
    """识别原文只能进 IndexedDB 的 scan_history，不得写入 localStorage / sessionStorage。"""
    page = ocr_session["page"]
    recognized = _read_recognized_text(page)
    assert recognized, "需要先有识别结果"

    storage_blob = page.evaluate(
        """() => {
            const dump = (store) => {
                let out = '';
                for (let i = 0; i < store.length; i += 1) {
                    const key = store.key(i);
                    out += String(key) + '=' + String(store.getItem(key)) + '\\n';
                }
                return out;
            };
            return dump(window.localStorage) + dump(window.sessionStorage);
        }"""
    )
    snippet = recognized.strip().splitlines()[0][:12]
    assert snippet not in storage_blob, f"识别原文被写入 Web Storage：{snippet!r}"
    assert page.context.cookies() == [], "全程不得出现 Cookie"
