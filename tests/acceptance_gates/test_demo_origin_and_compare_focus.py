"""门禁 8c：演示样例来源属性 + 06 区块并列来源焦点（真实浏览器）。

存在的理由：这两件事都只能通过「读者在页面上实际看到什么」来证明。

A. 演示样例标记
   `v1/demo/index.json`（以及打包进单文件的兜底常量）与 `v1/gtin|products|ingredients` 是两种
   不同来源。前者是合成演示数据，后者是静态档案库分片。若界面把两者画成同一种东西，读者会
   误以为「这个商品已被真实库收录」。标记必须由载荷里的 `origin` 字段驱动，不得在前端硬编码
   演示条码清单 —— 否则新增/删除演示条码时标记就会静默失准。

B. 06 区块来源焦点
   条码静态档案配料（`bundle.ingredients`）与 OCR / 手动修正文本（`ocrText`）各自独立匹配。
   两份命中必须始终可分辨、同时可见；切换只改变主视图焦点，不隐藏任何一方的数据，更不得把
   两份配料拼成一个列表当作结论。

两条不变量都配了反向对照：故意在**运行中的真实页面**里注入回归（删除标记 / 隐藏一栏 /
把一份命中塞进另一份），同一检查函数必须失败。只断言「通过」而没有「能失败」的证明视为无效门禁。
"""
from __future__ import annotations

import copy
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "apps" / "web" / "dist"
SRC = ROOT / "apps" / "web" / "src"

playwright = pytest.importorskip("playwright.sync_api", reason="需要 playwright 才能执行浏览器门禁")
from playwright.sync_api import sync_playwright  # noqa: E402

try:  # 兼容以 tests/ 为根、以及包内导入两种执行方式
    from ._browser_launch import launch_chromium
except ImportError:  # pragma: no cover
    from _browser_launch import launch_chromium  # type: ignore[no-redef]

# 演示条码：出现在 demo/index.json 里的样例。这里只用于**扫描**源码是否硬编码了它们，
# 不作为界面判定依据 —— 界面判定一律走载荷的 origin 字段。
DEMO_GTINS = ("6901234567892", "6905554443331")
STATIC_GTIN = "6901234567892"

# 演示数据会出现的展示位置（按 origin=DEMO_FALLBACK 时才必须出现）。
EXPECTED_DEMO_SCOPES = {
    "scan-demo-list",
    "scan-03-coverage",
    "scan-04-candidates",
    "scan-05-ingredients",
    "scan-06-static",
    "dossier-provenance",
}

HIT_NOTICE_STATIC_MARK = "配料表第"
HIT_NOTICE_OCR_MARK = "OCR 第"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
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
    yield f"http://127.0.0.1:{port}"
    server.terminate()
    server.wait(timeout=10)


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_playwright() as api:
        instance, _executable = launch_chromium(api, args=["--no-sandbox"])
        yield instance
        instance.close()


def _new_page(browser, url: str, *, demo_fallback: bool):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    if demo_fallback:
        # 阻断正式分片，令 lookupGtin 回落到 v1/demo/index.json（真实兜底路径）
        page.route("**/data/v1/gtin/*.json", lambda route: route.abort())
    page.goto(url, wait_until="load")
    page.wait_for_timeout(600)
    return context, page


def _add_rule(page, term: str = "乳糖") -> None:
    page.locator("input[placeholder='例如：乳糖']").fill(term)
    page.get_by_role("button", name="加入本地词条").click()
    page.wait_for_timeout(500)


def _lookup(page, gtin: str = STATIC_GTIN) -> None:
    page.locator("input[aria-label='条码']").fill(gtin)
    page.get_by_role("button", name="检索公开档案").click()
    page.wait_for_timeout(1200)


def _search_location() -> None:
    loc = ROOT / ".state"
    loc.mkdir(parents=True, exist_ok=True)


def _screenshot(page, name: str):
    _search_location()
    path = ROOT / ".state" / "gate_shots" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=False)
    return path


# ------------------------------------------------------------ 检查函数（正反共用）

def collect_demo_mark_scopes(page) -> list[str]:
    """读取页面上实际渲染的演示标记位置（不是源码推断）。"""
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('[data-demo-mark]'))
            .filter((el) => el.getClientRects().length > 0)
            .map((el) => el.getAttribute('data-demo-scope'))"""
    )


def assert_demo_marks(scopes: list[str], expected: set[str]) -> None:
    """断言：期望的每个展示位置都有可见标记，且标记位置集合不额外走漏。"""
    got = set(scopes or [])
    missing = expected - got
    unexpected = got - expected
    assert not missing, f"这些展示演示数据的位置缺少可见的「演示样例」标记: {sorted(missing)}（实际: {sorted(got)}）"
    assert not unexpected, f"出现了未预期的演示标记位置: {sorted(unexpected)}"


def collect_compare_state(page) -> dict:
    """读取 06 区块的真实 DOM 状态：焦点、两栏可见性、各命中行归属。"""
    return page.evaluate(
        """() => {
            const head = document.querySelector('[data-compare-focus]');
            if (!head) return { present: false };
            const panes = Array.from(document.querySelectorAll('[data-compare-pane]'));
            const state = {
                present: true,
                focus: head.getAttribute('data-compare-focus'),
                legend: (head.querySelector('legend') || {}).textContent || '',
                panes: panes.map((pane) => {
                    const rect = pane.getBoundingClientRect();
                    const hiddenByCss = getComputedStyle(pane).display === 'none';
                    const hits = Array.from(pane.querySelectorAll('[data-hit-source]'));
                    return {
                        source: pane.getAttribute('data-compare-pane'),
                        visible: rect.width > 0 && rect.height > 0 && !hiddenByCss,
                        focused: pane.classList.contains('is-focus'),
                        hitCount: hits.length,
                        hitSources: hits.map((h) => h.getAttribute('data-hit-source')),
                        text: pane.innerText,
                    };
                }),
                radios: Array.from(head.querySelectorAll('input[name="compare-source-focus"]')).map((r) => ({
                    value: r.value,
                    checked: r.checked,
                })),
                hitRowsOutsidePanes: Array.from(document.querySelectorAll('[data-hit-source]'))
                    .filter((el) => !el.closest('[data-compare-pane]')).length,
            };
            return state;
        }"""
    )


def assert_compare_state(state: dict, expected_focus: str) -> None:
    assert state.get("present"), "06 区块缺少来源焦点标注区（data-compare-focus）"
    assert state["focus"] == expected_focus, f"06 区块焦点标注未随切换更新: {state['focus']} != {expected_focus}"
    assert {p["source"] for p in state["panes"]} == {"static_archive", "ocr_text"}, (
        f"06 区块必须并列渲染两份来源，实际: {[p['source'] for p in state['panes']]}"
    )
    for pane in state["panes"]:
        assert pane["visible"], f"来源 {pane['source']} 在切换后被隐藏；切换只改变焦点，不隐藏数据"
        assert pane["hitSources"] == [pane["source"]] * pane["hitCount"], (
            f"来源 {pane['source']} 内混入了另一份来源的命中行: {pane['hitSources']}"
        )
    assert state["hitRowsOutsidePanes"] == 0, "存在不属于任何来源栏的命中行（疑似把两份来源合并成一个列表）"
    focused = [p["source"] for p in state["panes"] if p["focused"]]
    assert focused == [expected_focus], f"主视图焦点应唯一且等于 {expected_focus}，实际: {focused}"
    assert any(radio["checked"] for radio in state["radios"]), "来源切换控件必须处于已选中状态"


def assert_no_merged_presentation(state: dict) -> None:
    """两份来源的命中文案必须各自带来源语义，不能互相串味。"""
    for pane in state["panes"]:
        text = pane["text"]
        if pane["source"] == "static_archive":
            assert HIT_NOTICE_OCR_MARK not in text, "条码静态档案栏里出现了 OCR 命中文案"
        else:
            assert HIT_NOTICE_STATIC_MARK not in text, "OCR 栏里出现了条码静态档案命中文案"
        assert "命中" in text or "未出现" in text or "尚未" in text or "当前 OCR" in text


# ------------------------------------------------------------- 源码级检查（正反共用）

def scan_hardcoded_demo_gtins(sources: dict[str, str]) -> list[tuple[str, str]]:
    """扫描前端源码里是否把演示条码写进判定逻辑。返回 [(文件名, 条码)]。

    输入框的 `placeholder="…6901234567892…"` 只是给用户看的检索示例，不属于「硬编码演示条码清单」，
    因此在扫描前先剥掉 `placeholder` 属性。除此之外的任何出现（三元判定 / `includes` / 字面量数组）
    都算违规：那样一来新增或删除演示条码时，来源标记会静默失准。
    """
    placeholder = re.compile(r'placeholder=(?:"[^"]*"|\'[^\']*\'|\{[^}]*\})')
    offenders: list[tuple[str, str]] = []
    for name, text in sources.items():
        stripped = placeholder.sub('placeholder=""', text)
        for gtin in DEMO_GTINS:
            if gtin in stripped:
                offenders.append((name, gtin))
    return offenders


def scan_origin_guarded_marks(sources: dict[str, str]) -> list[str]:
    """每个 DemoMark 渲染点都必须带 origin 判定。返回未受 origin 守卫的渲染点描述。"""
    offenders: list[str] = []
    for name, text in sources.items():
        cursor = 0
        while True:
            index = text.find("<DemoMark", cursor)
            if index < -1:
                break
            cursor = index + 1
            if index < 0:
                break
            # 渲染点前 400 字符内必须出现 origin 判定
            window = text[max(0, index - 400) : index]
            if 'origin === "DEMO_FALLBACK"' not in window and "origin === \"DEMO_FALLBACK\"" not in window:
                offenders.append(f"{name}:{text[:index].count(chr(10)) + 1}")
    return offenders


def _load_sources() -> dict[str, str]:
    return {
        "pages/ScanPage.tsx": (SRC / "pages" / "ScanPage.tsx").read_text(encoding="utf-8"),
        "pages/DossierPage.tsx": (SRC / "pages" / "DossierPage.tsx").read_text(encoding="utf-8"),
        "components/ui.tsx": (SRC / "components" / "ui.tsx").read_text(encoding="utf-8"),
    }


# --------------------------------------------------------------------- A：演示样例标记

def test_demo_marks_visible_at_every_demo_display_location(site, browser):
    """demo 兜底路径：五处演示数据展示位置 + 档案页 provenance 区都必须有可见标记。"""
    context, page = _new_page(browser, site, demo_fallback=True)
    try:
        _add_rule(page)
        _lookup(page)
        scopes = collect_demo_mark_scopes(page)
        # 标记文案必须包含「演示样例」四个字
        mark = page.locator("[data-demo-mark]").first
        assert mark.is_visible(), "演示标记不可见（可能被 CSS 隐藏）"
        assert "演示样例" in mark.inner_text(), f"演示标记文案缺少「演示样例」: {mark.inner_text()!r}"
        assert "真实商品库" in mark.inner_text(), "演示标记必须说明它不等于已联网的真实商品库"
        _screenshot(page, "01-scan-demo-marks.png")

        page.get_by_role("button", name="查看 16 区块档案与来源追溯 →").first.click()
        page.wait_for_timeout(1500)
        scopes.extend(collect_demo_mark_scopes(page))
        assert_demo_marks(scopes, EXPECTED_DEMO_SCOPES)
    finally:
        context.close()


def test_static_library_results_carry_no_demo_mark(site, browser):
    """静态分片命中时，结果区不得出现「演示样例」标记（来源由 origin 字段判定，不是按条码）。"""
    context, page = _new_page(browser, site, demo_fallback=False)
    try:
        _add_rule(page)
        _lookup(page)
        # 演示样例按钮区本身就是 demo 数据，允许且必须有标记
        scopes = collect_demo_mark_scopes(page)
        assert_demo_marks(scopes, {"scan-demo-list"})
        assert page.locator("[data-demo-scope='scan-04-candidates']").count() == 0, (
            "静态档案命中结果不应被标为演示样例"
        )
    finally:
        context.close()


def test_demo_marks_fail_when_marking_is_removed_or_leaked(site, browser):
    """反向对照：运行中页面删掉标记 → 检查失败；静态结果里插入假标记 → 检查失败。"""
    context, page = _new_page(browser, site, demo_fallback=True)
    try:
        _add_rule(page)
        _lookup(page)
        page.evaluate(
            """() => document.querySelectorAll('[data-demo-scope="scan-04-candidates"]')
                .forEach((el) => el.remove())"""
        )
        with pytest.raises(AssertionError, match="缺少可见的「演示样例」标记"):
            assert_demo_marks(collect_demo_mark_scopes(page), EXPECTED_DEMO_SCOPES)
    finally:
        context.close()

    context, page = _new_page(browser, site, demo_fallback=False)
    try:
        _add_rule(page)
        _lookup(page)
        page.evaluate(
            """() => {
                const host = document.querySelector('[data-compare-pane="static_archive"]');
                const fake = document.createElement('div');
                fake.setAttribute('data-demo-mark', 'DEMO_FALLBACK');
                fake.setAttribute('data-demo-scope', 'scan-04-candidates');
                fake.textContent = '演示样例';
                host.prepend(fake);
            }"""
        )
        with pytest.raises(AssertionError, match="未预期的演示标记位置"):
            assert_demo_marks(collect_demo_mark_scopes(page), {"scan-demo-list"})
    finally:
        context.close()


def test_demo_marking_is_origin_driven_not_hardcoded():
    """源码级：不得硬编码演示条码清单；每个演示标记渲染点都由 origin 字段守卫。"""
    sources = _load_sources()
    assert scan_hardcoded_demo_gtins(sources) == [], (
        f"前端源码硬编码了演示条码: {scan_hardcoded_demo_gtins(sources)}"
    )
    assert scan_origin_guarded_marks(sources) == [], (
        f"存在未经 origin 守卫的演示标记渲染点: {scan_origin_guarded_marks(sources)}"
    )


def test_demo_marking_scanner_detects_injected_regressions():
    """反向对照：注入硬编码条码与被移除的 origin 守卫，扫描器必须命中。"""
    sources = _load_sources()
    injected = copy.deepcopy(sources)
    injected["pages/ScanPage.tsx"] += "\nconst leak = '6901234567892';\n"
    assert scan_hardcoded_demo_gtins(injected), "扫描器未能发现注入的硬编码演示条码"

    guarded = copy.deepcopy(sources)
    guarded["pages/ScanPage.tsx"] = guarded["pages/ScanPage.tsx"].replace(
        '<DemoMark scope="scan-demo-list" />',
        '<DemoMark scope="scan-demo-list" />\n{/* 无 origin 守卫 */}',
    ).replace(
        '{demos.some((item) => item.origin === "DEMO_FALLBACK") ? (',
        "{true ? (",
    )
    assert scan_origin_guarded_marks(guarded), "扫描器未能发现被移除的 origin 守卫"


# ----------------------------------------------------------------- B：06 区块来源焦点

def test_compare_focus_label_and_switch_work(site, browser):
    """06 顶部必须标注当前对照来源，且切换控件真的改变焦点而不隐藏数据。"""
    context, page = _new_page(browser, site, demo_fallback=False)
    try:
        _add_rule(page)
        _lookup(page)
        state = collect_compare_state(page)
        assert_compare_state(state, "static_archive")
        assert "当前对照来源" in state["legend"], f"来源焦点区缺少标注图例: {state['legend']!r}"

        page.locator("input[name='compare-source-focus'][value='ocr_text']").check(force=True)
        page.wait_for_timeout(300)
        state = collect_compare_state(page)
        assert_compare_state(state, "ocr_text")
        # 切回静态档案焦点，验证双向切换
        page.locator("input[name='compare-source-focus'][value='static_archive']").check(force=True)
        page.wait_for_timeout(300)
        assert_compare_state(collect_compare_state(page), "static_archive")
        _screenshot(page, "02-compare-focus.png")
    finally:
        context.close()


def test_compare_sources_keep_separate_hits_and_never_merge(site, browser):
    """两份来源的命中始终可分辨：各自带来源语义与归属，且不合成一个列表。"""
    context, page = _new_page(browser, site, demo_fallback=False)
    try:
        _add_rule(page)
        _lookup(page)
        page.locator("textarea").first.fill("配料：全脂乳粉、乳糖、磷脂")
        page.wait_for_timeout(400)
        state = collect_compare_state(page)
        assert_no_merged_presentation(state)
        panes = {p["source"]: p for p in state["panes"]}
        assert panes["static_archive"]["hitCount"] > 0, "静态档案侧应有配料命中（夹具含乳糖）"
        assert panes["ocr_text"]["hitCount"] > 0, "OCR 侧应有文本行命中"
        assert_compare_state(state, "static_archive")

        page.locator("input[name='compare-source-focus'][value='ocr_text']").check(force=True)
        page.wait_for_timeout(300)
        state_after = collect_compare_state(page)
        assert_compare_state(state_after, "ocr_text")
        # 切到 OCR 后，静态档案栏的命中数据必须仍在 DOM 中（不隐藏、不卸载）
        assert {p["source"]: p["hitCount"] for p in state_after["panes"]} == {
            p["source"]: p["hitCount"] for p in state["panes"]
        }, "切换焦点后两栏命中条数发生变化，说明切换隐藏或重算了数据"
        _screenshot(page, "03-compare-both-sources.png")
    finally:
        context.close()


def test_compare_focus_fails_when_sources_are_merged_or_hidden(site, browser):
    """反向对照：隐藏另一栏 / 把一份命中塞进另一份 → 同一检查必须失败。"""
    context, page = _new_page(browser, site, demo_fallback=False)
    try:
        _add_rule(page)
        _lookup(page)

        # 1) 模拟「切换时隐藏另一份来源」
        page.evaluate(
            """() => {
                const pane = document.querySelector('[data-compare-pane="ocr_text"]');
                pane.style.display = 'none';
            }"""
        )
        with pytest.raises(AssertionError, match="在切换后被隐藏"):
            assert_compare_state(collect_compare_state(page), "static_archive")

        # 2) 模拟「把 OCR 命中合并进静态档案列表」
        page.evaluate(
            """() => {
                const pane = document.querySelector('[data-compare-pane="ocr_text"]');
                pane.style.display = '';
                const host = document.querySelector('[data-compare-pane="static_archive"]');
                const merged = document.createElement('div');
                merged.setAttribute('data-hit-source', 'ocr_text');
                merged.textContent = 'OCR 第 1 行「乳糖」命中您的本地忌口词条「乳糖」';
                host.appendChild(merged);
            }"""
        )
        with pytest.raises(AssertionError, match="混入了另一份来源的命中行"):
            assert_compare_state(collect_compare_state(page), "static_archive")
    finally:
        context.close()
