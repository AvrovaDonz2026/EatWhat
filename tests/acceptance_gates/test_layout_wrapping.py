"""门禁 8c：中文词语不被逐字折断（真实浏览器排版回归）。

此门禁存在的原因：`word-break: break-all` + flex 正文列 `flex-basis: 0`
会让「全脂乳粉、乳清蛋白粉」在窄容器里被折成「全脂乳 / 粉、乳清 / 蛋白粉」，
极端情况下甚至单字成行，读起来就是竖排折叠。这类问题不报错、不崩、不发请求，
旧门禁全部通过，只有人眼才看得出。这里把「词语完整性」变成断言。

断言：
1. 配料/致敏原区块里的中文词语，在任何测试宽度下都不得出现「单字成行」；
2. 不得把 `word-break: break-all` 用于这些语义文本（应使用 break-word）。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "apps" / "web" / "dist"

playwright = pytest.importorskip("playwright.sync_api", reason="需要 playwright 才能执行浏览器门禁")
from playwright.sync_api import sync_playwright  # noqa: E402

try:  # 兼容以 tests/ 为根、以及包内导入两种执行方式
    from ._browser_launch import launch_chromium, static_site_server
except ImportError:  # pragma: no cover
    from _browser_launch import launch_chromium, static_site_server  # type: ignore[no-redef]

# 只有在这些宽度下都稳定，才算「不逐字折行」成立。
WIDTHS = (390, 360, 320, 1280)

# 逐字符计算每行实际渲染内容：比量高度可靠，能量化到「哪几个字在哪一行」。
MEASURE_JS = """
() => {
  const out = [];
  const rg = document.createRange();
  const SELECTOR = '.item-row__primary, .item-row__secondary, .field-value, .term-cell';
  for (const el of document.querySelectorAll(SELECTOR)) {
    const tn = el.firstChild;
    if (!tn || tn.nodeType !== 3 || !tn.data) continue;
    const data = tn.data;
    if (!/全脂乳粉|乳清蛋白|磷脂|乳制品|配料/.test(data)) continue;
    const lines = [];
    let prevTop = null;
    let cur = '';
    for (let k = 0; k < data.length; k += 1) {
      rg.setStart(tn, k);
      rg.setEnd(tn, k + 1);
      const rc = rg.getBoundingClientRect();
      if (prevTop !== null && Math.abs(rc.top - prevTop) > 1) { lines.push(cur); cur = ''; }
      prevTop = rc.top;
      cur += data[k];
    }
    if (cur) lines.push(cur);
    out.push({ text: data.slice(0, 40), width: Math.round(el.getBoundingClientRect().width), lines });
  }
  return out;
}
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def layout_session():
    if not (DIST / "index.html").exists():
        pytest.skip("apps/web/dist 未构建；请先执行 npm run build:web")

    with static_site_server(DIST) as site:
        with sync_playwright() as api:
            browser, _ = launch_chromium(api, args=["--no-sandbox"])
            try:
                yield {"browser": browser, "site": site}
            finally:
                # 浏览器先关，站点后停：避免关站期间页面仍在请求
                browser.close()


def _open_dossier(page, site: str) -> None:
    page.goto(site, wait_until="load")
    page.wait_for_timeout(900)
    page.get_by_role("button", name="禾丰全脂乳粉 (6901234567892)").first.click()
    page.wait_for_timeout(400)
    page.get_by_role("button", name="看看是什么").first.click()
    page.wait_for_timeout(2200)
    page.get_by_role("button", name="看原始记录与出处").click(timeout=8000)
    page.wait_for_timeout(1500)


def test_chinese_terms_never_break_into_single_characters(layout_session):
    browser = layout_session["browser"]
    site = layout_session["site"]
    problems = []
    checked = 0

    for width in WIDTHS:
        page = browser.new_page(viewport={"width": width, "height": 1000})
        _open_dossier(page, site)
        samples = page.evaluate(MEASURE_JS)
        page.close()

        assert samples, f"宽度 {width} 下未找到任何配料相关文本，门禁无法成立"
        for sample in samples:
            checked += 1
            single = [line for line in sample["lines"] if len(line) <= 1]
            if single:
                problems.append((width, sample["text"], sample["lines"]))

    assert checked > 0, "没有捕捉到任何待检文本"
    assert not problems, f"出现单字成行（逐字竖排折叠）：{problems[:4]}"


def _open_scan_result(page, site: str) -> None:
    """打开扫码页并检索演示条码，让第 05 区块（配料与营养原文）渲染出来。"""
    page.goto(site, wait_until="load")
    page.wait_for_timeout(900)
    page.get_by_role("button", name="禾丰全脂乳粉 (6901234567892)").first.click()
    page.wait_for_timeout(400)
    page.get_by_role("button", name="看看是什么").first.click()
    page.wait_for_timeout(2200)


def test_scan_ingredient_terms_never_break_into_single_characters(layout_session):
    """扫码页第 05 区块的配料/致敏原术语列。

    这套结构是 `<ol><li><span class="t-13 flex">`：术语是 flex 子项，
    父级带 `min-w-0`。CJK 的 min-content 只有 1 个字宽，所以只要允许收缩
    就会被压成逐字竖排。此前的门禁只覆盖了档案页的 `ItemRow` / `.field-row`，
    漏掉了这里——这正是本节要补的盲区。
    """
    browser = layout_session["browser"]
    site = layout_session["site"]
    problems = []
    checked = 0

    for width in WIDTHS:
        page = browser.new_page(viewport={"width": width, "height": 1200})
        _open_scan_result(page, site)
        samples = page.evaluate(MEASURE_JS)
        page.close()

        assert samples, f"宽度 {width} 下未找到任何术语列文本，门禁无法成立"
        for sample in samples:
            checked += 1
            single = [line for line in sample["lines"] if len(line) <= 1]
            if single:
                problems.append((width, sample["text"], sample["lines"]))

    assert checked >= len(WIDTHS), f"覆盖不足：仅测到 {checked} 个样本"
    assert not problems, f"扫码页术语列出现单字成行：{problems[:4]}"


def test_term_cell_does_not_shrink():
    """术语列不得参与 flex 收缩，否则窄容器里会被压成单字宽。"""
    source = (ROOT / "apps" / "web" / "src" / "index.css").read_text(encoding="utf-8")
    assert ".term-cell" in source, "缺少 `.term-cell` 规则，术语列无法抵御收缩"
    block_start = source.index(".term-cell")
    block = source[block_start : block_start + 400]
    assert "flex: 0 0 auto" in block, f"`.term-cell` 必须禁止收缩，实际：{block[:120]!r}"
    assert "word-break: normal" in block, "`.term-cell` 不得使用 break-all"


def test_layout_gate_detects_squeezed_body_column(layout_session):
    """反向对照：证明排版门禁的测量与判定逻辑真的会判定「被压塌」。

    当前 demo 数据的字段值较短、证据标签也不够长，真实页面上压不到单字成行。
    若只做真实页面断言，这条门禁就是空转。因此这里用最小 HTML 复刻
    `ItemRow` 的结构，在极窄正文列 + 长证据标签下确认两点：
    - 错误写法（`flex: 1 1 0` + `break-all`）确实产生单字成行；
    - 修复写法（14ch 下限 + `break-word`）不会。
    这样后续有人改坏 CSS，判定逻辑本身是可靠的。
    """
    broken = """<html><body style="margin:0">
      <div style="width:300px;display:flex;flex-wrap:nowrap;column-gap:16px">
        <div style="min-width:0;flex:1 1 0">
          <p id="t" style="margin:0;font-size:13px;word-break:break-all">全脂乳粉、乳清蛋白粉</p>
        </div>
        <span style="white-space:nowrap;font-family:monospace">RFCY6871:F905A95C4FF7E422D4C7EAB9B1</span>
      </div></body></html>"""

    fixed = """<html><body style="margin:0">
      <div style="width:300px;display:flex;flex-wrap:wrap;column-gap:16px;row-gap:4px">
        <div style="flex:1 1 14ch;min-width:min(100%,14ch)">
          <p id="t" style="margin:0;font-size:13px;overflow-wrap:break-word;word-break:normal">全脂乳粉、乳清蛋白粉</p>
        </div>
        <span style="font-family:monospace;overflow-wrap:anywhere;max-width:100%">RFCY6871:F905A95C4FF7E422D4C7EAB9B1</span>
      </div></body></html>"""

    measure = """
    () => {
      const el = document.querySelector('#t');
      const tn = el.firstChild;
      const rg = document.createRange();
      const lines = [];
      let prevTop = null;
      let cur = '';
      for (let k = 0; k < tn.length; k += 1) {
        rg.setStart(tn, k); rg.setEnd(tn, k + 1);
        const rc = rg.getBoundingClientRect();
        if (prevTop !== null && Math.abs(rc.top - prevTop) > 1) { lines.push(cur); cur = ''; }
        prevTop = rc.top; cur += tn.data[k];
      }
      if (cur) lines.push(cur);
      return lines;
    }
    """

    if not (DIST / "index.html").exists():
        pytest.skip("apps/web/dist 未构建；请先执行 npm run build:web")

    browser = layout_session["browser"]
    page = browser.new_page(viewport={"width": 390, "height": 300})
    try:
        page.set_content(broken)
        broken_lines = page.evaluate(measure)
        page.set_content(fixed)
        fixed_lines = page.evaluate(measure)
    finally:
        page.close()

    broken_single = [line for line in broken_lines if len(line) <= 1]
    fixed_single = [line for line in fixed_lines if len(line) <= 1]

    assert broken_single, f"反向对照失效：错误写法竟未产生单字成行（{broken_lines}）"
    assert not fixed_single, f"修复写法仍出现单字成行：{fixed_lines}"
    assert len(fixed_lines) == 1, f"修复写法应为单行，实际 {fixed_lines}"


def test_layout_gate_detects_squeezed_flex_term(layout_session):
    """反向对照：术语列作为可收缩 flex 子项时确实会被压成单字成行。

    复刻扫码页 `词条 → <span class="t-13 flex min-w-0"><序号/><术语/></span>`
    的结构，用一个极长的序号列把容器占满，比较「允许收缩」与「禁止收缩」。
    """
    def build(term_flex: str) -> str:
        return f"""<html><body style="margin:0">
          <div style="width:180px;font-family:sans-serif">
            <span id="row" style="font-size:13px;display:flex;gap:8px;min-width:0">
              <span style="font-family:monospace;flex:0 0 auto;width:150px">1</span>
              <span id="t" style="flex:{term_flex};overflow-wrap:break-word;word-break:normal">全脂乳粉、乳清蛋白粉</span>
            </span>
          </div></body></html>"""

    measure = """
    () => {
      const el = document.querySelector('#t');
      const tn = el.firstChild;
      const rg = document.createRange();
      const lines = [];
      let prevTop = null;
      let cur = '';
      for (let k = 0; k < tn.length; k += 1) {
        rg.setStart(tn, k); rg.setEnd(tn, k + 1);
        const rc = rg.getBoundingClientRect();
        if (prevTop !== null && Math.abs(rc.top - prevTop) > 1) { lines.push(cur); cur = ''; }
        prevTop = rc.top; cur += tn.data[k];
      }
      if (cur) lines.push(cur);
      return { width: Math.round(el.getBoundingClientRect().width), lines };
    }
    """

    if not (DIST / "index.html").exists():
        pytest.skip("apps/web/dist 未构建；请先执行 npm run build:web")

    browser = layout_session["browser"]
    page = browser.new_page(viewport={"width": 390, "height": 300})
    try:
        page.set_content(build("0 1 auto"))
        shrinkable = page.evaluate(measure)
        page.set_content(build("0 0 auto"))
        rigid = page.evaluate(measure)
    finally:
        page.close()

    # 允许收缩：术语列被压到不到两个字宽，必然逐字成行
    assert shrinkable["width"] <= 30, f"反向对照未压塌：术语列宽 {shrinkable['width']}px"
    shrink_single = [line for line in shrinkable["lines"] if len(line) <= 1]
    assert shrink_single, f"反向对照失效：允许收缩却未产生单字成行（{shrinkable['lines']}）"

    # 禁止收缩：术语列保持完整宽度，整串单行
    assert rigid["width"] >= 100, f"禁止收缩后术语列仍被压窄：{rigid['width']}px"
    assert len(rigid["lines"]) == 1, f"禁止收缩后仍折行：{rigid['lines']}"
    assert shrinkable["width"] < rigid["width"] / 3, (
        f"收缩与不收缩宽度差不足：{shrinkable['width']}px vs {rigid['width']}px"
    )


def test_semantic_text_does_not_use_break_all():
    """`break-all` 不应用于中文语义文本；它允许在任意两字之间断开。"""
    offenders = []
    for path in sorted((ROOT / "apps" / "web" / "src").rglob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "break-all" not in line:
                continue
            # 允许：哈希/标识符等无空格长串（它们本来就需要硬断），
            # 以及 raw_value 原文块（任意原始文本，可能含 URL/序列号）
            allowed_contexts = (".trace__value", "payload_sha256", "evidence_id", "sha256", "raw-value")
            if any(marker in line for marker in allowed_contexts):
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{line_no}")
    assert not offenders, f"语义文本使用了 break-all（会逐字折行）：{offenders[:5]}"
