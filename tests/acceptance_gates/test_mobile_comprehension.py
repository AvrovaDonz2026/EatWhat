"""门禁 8d：移动端可读性（真实 Chromium，320–430px）。

为什么单独存在：本项目的主使用场景是「在超市拿手机扫码看」，而现有门禁全部
在 `1280px` 或「数据契约」层面断言。移动端上真正会击穿可读性的四件事——
横向滚动、字小到读不动、点不中、证据密度铺平——**都不报错、不崩、不发请求**，
旧门禁可以全绿而手机上完全看不懂。

实测基线（修复前，390px，真实 Chromium）：

| 页面 | ≤13px 可见文本占比 | scrollWidth / clientWidth | <44px 可点元素 | 默认可见 evidence_id |
| --- | --- | --- | --- | --- |
| 扫码页 | 95.7% | 450 / 390 | 13 / 14 | 0 |
| 档案页 | 92.9% | 450 / 390 | 17 / 17 | 75 |
| 饮食页 | 97.1% | 450 / 390 | 32 / 32 | 0 |

本门禁断言四件事，并为每件事提供一条「故意注入违规内容必然失败」的反向对照：

1. 零横向滚动（4 宽度 × 3 页面）；
2. 触屏目标 ≥ 44×44（允许 `data-tap-exempt` 白名单，且白名单数量有上限）；
3. 正文可读性（390px 下 14px 必须是出现最多的字号，且 ≤13px 占比受阈值约束）；
4. 证据分层未退化（默认可见 `evidence_id` / 40+ 位哈希为 0，展开后必须出现）。

每条断言前先确认「确实找到了 N 个元素 / N 个文本节点」，N 不够直接失败并说明
门禁无法成立——不允许在空集合上做 `all([]) == True` 式的空转通过。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "apps" / "web" / "dist"

playwright = pytest.importorskip("playwright.sync_api", reason="需要 playwright 才能执行浏览器门禁")

try:  # 兼容以 tests/ 为根、以及包内导入两种执行方式
    from ._browser_launch import launch_chromium, static_site_server
except ImportError:  # pragma: no cover
    from _browser_launch import launch_chromium, static_site_server  # type: ignore[no-redef]

WIDTHS = (320, 360, 390, 430)
PAGES = ("scan", "dossier", "diet")

# 触屏最小可点尺寸（iOS HIG 44pt / Material 48dp 的下界）。
MIN_TAP = 44.0
# 裁切容差：subpixel 布局会让 43.9 之类出现，1px 内不算违规。
EPS = 1.0

# 字号阈值（实测得出，不是拍的）：
# 修复后 390px 实测 ≤13px 占比为 扫码 42.8% / 档案 48.2% / 饮食 47.5%。
# 阈值取 55%，留出数据增长余量；同时要求 14px 必须是出现最多的字号。
MAX_SMALL_SHARE = 0.55
# 12px 是 AGENTS.md「辅助信息」下限，任何可见文本不得更小。
MIN_FONT_PX = 12.0

# `data-tap-exempt` 白名单上限：只允许「视觉上 1×1 或完全透明、由外层 label 承接
# 点击」的原生 radio/checkbox。单页超过这个数，说明豁免被用来掩盖真实的小目标。
# 实测：扫码页 2 个（来源聚焦切换）、饮食页 2 个（备份导入方式），档案页 0 个。
MAX_TAP_EXEMPTIONS = 2
# 允许的豁免理由必须登记在案，并同时要求外层 label ≥ 44×44（见 tap_violations）。
ALLOWED_EXEMPT = frozenset({"visually-hidden-radio", "native-radio"})

MEASURE_JS = r"""
() => {
  const doc = document.documentElement;
  const vw = doc.clientWidth;

  const isVisible = (el) => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    if (parseFloat(s.opacity) === 0 && el.getBoundingClientRect().width <= 2) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  // ---------- 1. 横向溢出：只看「真的伸出视口」的元素 ----------
  const overflow = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!isVisible(el)) continue;
    const r = el.getBoundingClientRect();
    // right 相对视口；scrollX 恒为 0（不滚动页面横向）
    const overflowRight = r.right - vw;
    if (overflowRight > 1.5) {
      overflow.push({
        tag: el.tagName,
        cls: String(el.className || '').slice(0, 48),
        overflowRight: Math.round(overflowRight * 10) / 10,
        width: Math.round(r.width * 10) / 10,
        text: (el.textContent || '').trim().slice(0, 30),
      });
    }
  }

  // ---------- 2. 触屏目标 ----------
  const CANDIDATE = 'button, a[href], input, select, textarea, [role="button"], summary';
  const clickables = [];
  const exempt = [];
  for (const el of document.querySelectorAll(CANDIDATE)) {
    if (!isVisible(el)) continue;
    if (el.tagName === 'INPUT' && (el.type === 'hidden' || el.type === 'file')) continue;
    const r = el.getBoundingClientRect();
    const rec = {
      tag: el.tagName,
      type: el.type || '',
      exempt: el.getAttribute('data-tap-exempt') || '',
      w: Math.round(r.width * 10) / 10,
      h: Math.round(r.height * 10) / 10,
      text: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 26),
    };
    if (el.hasAttribute('data-tap-exempt')) {
      // 豁免只能是「视觉隐藏、由外层 label 承接点击」的原生控件；因此必须
      // 同时证明外层 label 本身够大，否则豁免就是在掩盖真实的小目标。
      const label = el.closest('label');
      const lr = label ? label.getBoundingClientRect() : null;
      rec.labelW = lr ? Math.round(lr.width * 10) / 10 : 0;
      rec.labelH = lr ? Math.round(lr.height * 10) / 10 : 0;
      exempt.push(rec);
      continue;
    }
    clickables.push(rec);
  }
  const small = clickables.filter((c) => c.w < 44 - 1 || c.h < 44 - 1);

  // ---------- 3. 字号分布（按可见文本节点，不按元素） ----------
  let totalNodes = 0;
  const tally = {};
  const belowMin = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const text = node.data.trim();
    if (text.length < 2) continue;
    const el = node.parentElement;
    if (!el || !isVisible(el)) continue;
    if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE' || el.tagName === 'TEXTAREA') continue;
    const fs = Math.round(parseFloat(getComputedStyle(el).fontSize) * 10) / 10;
    tally[fs] = (tally[fs] || 0) + 1;
    totalNodes += 1;
    if (fs < 12 - 0.01 && belowMin.length < 8) belowMin.push({ fs, text: text.slice(0, 30) });
  }
  const smallCount = Object.entries(tally)
    .filter(([k]) => parseFloat(k) <= 13)
    .reduce((sum, [, v]) => sum + v, 0);
  const modal = Object.entries(tally).sort((a, b) => b[1] - a[1])[0] || [0, 0];

  // ---------- 4. 默认可见的追溯内容 ----------
  const visibleTrace = [];
  const walker2 = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker2.nextNode()) {
    const node = walker2.currentNode;
    const text = node.data.trim();
    if (!/ev_[0-9a-f]{6,}|[0-9a-f]{40,}/.test(text)) continue;
    const el = node.parentElement;
    if (!el || !isVisible(el)) continue;
    visibleTrace.push({ text: text.slice(0, 48), cls: String(el.className || '').slice(0, 40) });
  }

  // ---------- 5. 折叠入口可用性 ----------
  const folds = [];
  for (const btn of document.querySelectorAll('[data-fold-toggle], .trace__summary')) {
    if (!isVisible(btn)) continue;
    const r = btn.getBoundingClientRect();
    folds.push({
      kind: btn.hasAttribute('data-fold-toggle') ? 'fold' : 'trace',
      label: btn.getAttribute('data-fold-toggle') || (btn.textContent || '').trim().slice(0, 30),
      w: Math.round(r.width * 10) / 10,
      h: Math.round(r.height * 10) / 10,
      h44: r.height >= 44,
    });
  }

  return {
    scrollWidth: doc.scrollWidth,
    clientWidth: vw,
    overflow,
    clickableCount: clickables.length,
    small,
    exemptCount: exempt.length,
    exempt,
    totalNodes,
    tally,
    modalSize: parseFloat(modal[0]),
    smallShare: totalNodes > 0 ? smallCount / totalNodes : 0,
    belowMin,
    visibleTraceCount: visibleTrace.length,
    visibleTrace: visibleTrace.slice(0, 6),
    folds,
  };
}
"""


# --------------------------------------------------------------------------- 判定
# 这些谓词就是门禁的真实断言逻辑。反向对照把「注入后的测量结果」喂给同一批
# 谓词，因此能证明判定本身不是空转。


def overflow_violations(metrics: dict, *, page: str, width: int) -> list[str]:
    out: list[str] = []
    if metrics["scrollWidth"] > metrics["clientWidth"] + 1:
        out.append(
            f"{page}@{width}px: documentElement scrollWidth={metrics['scrollWidth']} "
            f"> clientWidth={metrics['clientWidth']}"
        )
    for item in metrics["overflow"]:
        out.append(
            f"{page}@{width}px: <{item['tag']} class={item['cls']!r}> 右侧越出视口 "
            f"{item['overflowRight']}px（{item['text']!r}）"
        )
    return out


def tap_violations(metrics: dict, *, page: str, width: int) -> list[str]:
    out: list[str] = []
    if metrics["clickableCount"] < 8:
        out.append(
            f"{page}@{width}px: 只找到 {metrics['clickableCount']} 个可点元素，"
            "门禁无法成立（页面可能没有渲染出可交互区域）"
        )
    for item in metrics["small"]:
        out.append(
            f"{page}@{width}px: <{item['tag']}{('/' + item['type']) if item['type'] else ''}> "
            f"{item['w']}×{item['h']} < {MIN_TAP}×{MIN_TAP}（{item['text']!r}）"
        )
    if metrics["exemptCount"] > MAX_TAP_EXEMPTIONS:
        out.append(
            f"{page}@{width}px: data-tap-exempt 豁免了 {metrics['exemptCount']} 个元素，"
            f"超过上限 {MAX_TAP_EXEMPTIONS}（豁免不得用来掩盖真实的小目标）"
        )
    for item in metrics["exempt"]:
        # 豁免必须「可证明无害」：外层 label 本身 ≥44×44，否则视同真实小目标。
        if item["labelW"] < MIN_TAP - 1 or item["labelH"] < MIN_TAP - 1:
            out.append(
                f"{page}@{width}px: 被豁免的 <{item['tag']}/{item['type']}> "
                f"外层 label 只有 {item['labelW']}×{item['labelH']} < {MIN_TAP}，豁免无效"
            )
        if item["exempt"] not in ALLOWED_EXEMPT:
            out.append(
                f"{page}@{width}px: 未登记在案的豁免理由 {item['exempt']!r}（只允许 {sorted(ALLOWED_EXEMPT)}）"
            )
    return out


def font_violations(metrics: dict, *, page: str, width: int) -> list[str]:
    out: list[str] = []
    if metrics["totalNodes"] < 60:
        out.append(
            f"{page}@{width}px: 只统计到 {metrics['totalNodes']} 个可见文本节点，"
            "样本不足，字号断言无法成立"
        )
        return out
    if metrics["belowMin"]:
        out.append(
            f"{page}@{width}px: 存在小于 {MIN_FONT_PX}px 的可见文本："
            f"{[(b['fs'], b['text']) for b in metrics['belowMin']]}"
        )
    if width == 390 and metrics["smallShare"] > MAX_SMALL_SHARE:
        out.append(
            f"{page}@{width}px: ≤13px 文本节点占 {metrics['smallShare'] * 100:.1f}%，"
            f"超过阈值 {MAX_SMALL_SHARE * 100:.0f}%（正文主力必须是 14px 以上）"
        )
    if width == 390 and metrics["modalSize"] < 14:
        out.append(
            f"{page}@{width}px: 出现最多的字号只有 {metrics['modalSize']}px，"
            "正文主力字号必须 ≥ 14px"
        )
    return out


def layering_violations(metrics: dict, *, page: str) -> list[str]:
    out: list[str] = []
    if metrics["visibleTraceCount"] > 0:
        out.append(
            f"{page}: 默认可见 {metrics['visibleTraceCount']} 处追溯内容"
            f"（evidence_id / 哈希），应全部折叠：{metrics['visibleTrace']}"
        )
    return out


def fold_violations(metrics: dict, *, page: str, width: int) -> list[str]:
    """折叠入口必须点得中、且文案写明内容（不是「更多」）。"""
    out: list[str] = []
    if page == "dossier" and width <= 430:
        if len(metrics["folds"]) < 5:
            out.append(
                f"{page}@{width}px: 只找到 {len(metrics['folds'])} 个折叠入口，"
                "预期至少 5 个（16 区块里长尾区块 + 追溯披露），门禁无法成立"
            )
    for item in metrics["folds"]:
        if item["h"] < MIN_TAP - 1:
            out.append(f"{page}@{width}px: 折叠入口高度 {item['h']}px < {MIN_TAP}px（{item['label']!r}）")
        if len(item["label"]) < 6:
            out.append(f"{page}@{width}px: 折叠入口文案过短、未说明展开内容：{item['label']!r}")
        for vague in ("更多", "详情", "展开"):
            if item["label"].strip() == vague:
                out.append(f"{page}@{width}px: 折叠入口使用了含糊文案 {item['label']!r}")
    return out


# --------------------------------------------------------------------------- 夹具


@pytest.fixture(scope="module")
def mobile_session():
    if not (DIST / "index.html").exists():
        pytest.skip("apps/web/dist 未构建；请先执行 npm run build:web")

    with static_site_server(DIST) as site:
        with playwright.sync_playwright() as api:
            browser, _ = launch_chromium(api, args=["--no-sandbox"])
            try:
                yield {"browser": browser, "site": site}
            finally:
                browser.close()


def _open_paged(page, site: str, name: str) -> None:
    """打开指定页面并进入真实数据状态（走真实静态分片，不用假数据）。"""
    page.goto(site, wait_until="load")
    page.wait_for_timeout(700)

    if name == "diet":
        page.get_by_role("button", name="我的忌口与饮食计划").first.click()
        page.wait_for_timeout(1000)
        return

    page.get_by_role("button", name="禾丰全脂乳粉 (6901234567892)").first.click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name="检索公开档案").first.click()
    page.wait_for_timeout(2000)

    if name == "dossier":
        page.locator("button", has_text="档案").last.click(timeout=8000)
        page.wait_for_timeout(1600)


def _measure_all(browser, site: str) -> dict[tuple[str, int], dict]:
    results: dict[tuple[str, int], dict] = {}
    for name in PAGES:
        for width in WIDTHS:
            page = browser.new_page(viewport={"width": width, "height": 900})
            try:
                _open_paged(page, site, name)
                results[(name, width)] = page.evaluate(MEASURE_JS)
            finally:
                page.close()
    return results


@pytest.fixture(scope="module")
def mobile_metrics(mobile_session):
    return _measure_all(mobile_session["browser"], mobile_session["site"])


# --------------------------------------------------------------------------- 正向断言


def test_no_horizontal_scroll_at_mobile_widths(mobile_metrics):
    """320–430px 下三页面的 scrollWidth 必须等于 clientWidth。

    修复前实测：三页面在 320/360/390 全部 scrollWidth=450 > clientWidth
    （320/360/390），横向可滚、内容被裁切。
    """
    problems: list[str] = []
    checked = 0
    for (name, width), metrics in mobile_metrics.items():
        checked += 1
        problems.extend(overflow_violations(metrics, page=name, width=width))

    assert checked == len(PAGES) * len(WIDTHS), f"覆盖不足：只测了 {checked} 个页面×宽度组合"
    assert not problems, "存在横向溢出：\n" + "\n".join(problems[:8])


def test_touch_targets_at_least_44px(mobile_metrics):
    """所有可点元素 ≥ 44×44；`data-tap-exempt` 只允许极小范围的原生控件。

    修复前实测：档案页 390px 下 17/17 个可点元素不足 44px（折叠入口仅 18px 高、
    页签 40.4px、演示条码 29.8px、忌口预设 28px）。
    """
    problems: list[str] = []
    checked = 0
    exempt_at_390 = 0
    for (name, width), metrics in mobile_metrics.items():
        checked += 1
        if width == 390:
            exempt_at_390 += metrics["exemptCount"]
        problems.extend(tap_violations(metrics, page=name, width=width))

    assert checked == len(PAGES) * len(WIDTHS)
    # 豁免上限按「页面」计，不按「页面×宽度」累乘——同一个小控件在四个宽度下
    # 出现四次只说明它存在，不说明豁免被滥用。
    assert exempt_at_390 <= MAX_TAP_EXEMPTIONS * len(PAGES), (
        f"390px 下三页豁免合计 {exempt_at_390} 个，超过上限 "
        f"{MAX_TAP_EXEMPTIONS * len(PAGES)}，白名单被滥用"
    )
    assert not problems, "存在不足 44×44 的可点元素：\n" + "\n".join(problems[:8])


def test_mobile_body_font_size_is_readable(mobile_metrics):
    """390px 下正文主力字号必须是 14px，≤13px 占比受限，且无小于 12px 的文本。

    修复前实测：390px 下 ≤13px 占比 扫码 95.7% / 档案 92.9% / 饮食 97.1%，
    正文主力就是 12px 的 `.t-12`。
    """
    problems: list[str] = []
    checked = 0
    for (name, width), metrics in mobile_metrics.items():
        checked += 1
        problems.extend(font_violations(metrics, page=name, width=width))

    assert checked == len(PAGES) * len(WIDTHS)
    assert not problems, "移动端字号不合格：\n" + "\n".join(problems[:8])


def test_evidence_is_layered_on_mobile(mobile_metrics):
    """手机默认视图中不得出现任何 evidence_id / 40+ 位哈希（追溯默认折叠）。

    修复前实测：档案页 390px 下默认可见 75 处 `ev_...` 标识（每个字段行、
    每条 item 都挂一个 evidence chip），追溯密度被铺平在手机屏上。
    """
    problems: list[str] = []
    checked = 0
    for (name, width), metrics in mobile_metrics.items():
        if width != 390:
            continue
        checked += 1
        problems.extend(layering_violations(metrics, page=name))

    assert checked == len(PAGES), f"覆盖不足：只检查了 {checked} 个页面的分层"
    assert not problems, "证据分层退化：\n" + "\n".join(problems[:6])


def test_fold_entries_are_tappable_and_explicit(mobile_session):
    """折叠入口 ≥44px 高，且文案写明展开后是什么，不用「更多」这类含糊词。"""
    page = mobile_session["browser"].new_page(viewport={"width": 390, "height": 900})
    problems: list[str] = []
    try:
        _open_paged(page, mobile_session["site"], "dossier")
        metrics = page.evaluate(MEASURE_JS)
        problems.extend(fold_violations(metrics, page="dossier", width=390))

        # 自证：折叠入口确实存在，且未展开时内容不在可见流里。
        assert len(metrics["folds"]) >= 5, (
            f"只找到 {len(metrics['folds'])} 个折叠入口，长尾区块的渐进展开没有生效"
        )

        # 展开后必须真的出现更多内容（不是删掉内容冒充折叠）。
        # 注：同一字符串可能出现在其他区块的原始观测 JSON 里，所以只在「注册/备案」
        # 区块内部判断可见性，避免跨区块误判。
        before = page.evaluate("() => document.querySelectorAll('section[data-block-id]').length")
        hidden_value = "国食注字YP20240001"

        def block_has_visible(value: str) -> bool:
            return bool(
                page.evaluate(
                    """(value) => {
                      const block = document.querySelector("section[data-block-id='registration_records']");
                      if (!block) return false;
                      const w = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
                      while (w.nextNode()) {
                        if (!w.currentNode.data.includes(value)) continue;
                        const el = w.currentNode.parentElement;
                        if (!el) continue;
                        const s = getComputedStyle(el);
                        if (s.display === 'none' || s.visibility === 'hidden') continue;
                        if (el.closest('[aria-hidden="true"]')) continue;
                        if (el.getBoundingClientRect().height === 0) continue;
                        return true;
                      }
                      return false;
                    }""",
                    value,
                )
            )

        assert not block_has_visible(hidden_value), (
            f"折叠前就已可见登记原文 {hidden_value!r}，折叠没有起作用"
        )
        fold = page.locator("[data-fold-toggle]").filter(has_text="注册").first
        fold.click()
        page.wait_for_timeout(300)
        assert fold.get_attribute("aria-expanded") == "true", "折叠入口点击后 aria-expanded 未变为 true"
        expanded_text = page.inner_text("section[data-block-id='registration_records']")
        assert "注册/备案公开记录" in expanded_text, "展开后未出现注册记录区块的正文"
        assert block_has_visible(hidden_value), (
            f"展开后仍未出现登记原文 {hidden_value!r}（内容被删而非折叠）"
        )
        after = page.evaluate("() => document.querySelectorAll('section[data-block-id]').length")
        assert before == after == 16, f"区块数量在展开前后发生改变：{before} → {after}（16 区块契约不得改动）"
    finally:
        page.close()

    assert not problems, "折叠入口不合格：\n" + "\n".join(problems[:8])


def test_expanding_trace_reveals_evidence(mobile_session):
    """默认折叠的追溯内容必须能在点击后出现——分层不是「删掉证据」。"""
    page = mobile_session["browser"].new_page(viewport={"width": 390, "height": 900})
    try:
        _open_paged(page, mobile_session["site"], "dossier")
        assert page.evaluate("() => document.body.innerText").count("ev_") == 0, (
            "默认视图不应出现 evidence_id"
        )

        summaries = page.locator(".trace__summary")
        assert summaries.count() > 0, "没有任何追溯折叠入口，分层断言无法成立"
        summaries.first.click()
        page.wait_for_timeout(400)

        revealed = page.evaluate(
            """() => {
              const out = [];
              const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
              while (w.nextNode()) {
                const t = w.currentNode.data.trim();
                if (!/ev_[0-9a-f]{6,}/.test(t)) continue;
                const el = w.currentNode.parentElement;
                const s = getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') continue;
                if (el.getBoundingClientRect().width === 0) continue;
                out.push(t);
              }
              return out;
            }"""
        )
        assert revealed, "展开追溯后仍未出现 evidence_id，说明证据被删而不是被折叠"
    finally:
        page.close()


# --------------------------------------------------------------------------- 反向对照


def test_overflow_gate_detects_injected_500px_block(mobile_session):
    """反向对照：注入 500px 宽的 div，零横向滚动断言必须失败。

    不动 `apps/web/src`，只在运行中的 DOM 里注入。
    """
    browser = mobile_session["browser"]
    site = mobile_session["site"]
    page = browser.new_page(viewport={"width": 390, "height": 800})
    try:
        _open_paged(page, site, "scan")
        injected = page.evaluate(
            """() => {
              const d = document.createElement('div');
              d.setAttribute('data-injected-violation', 'overflow');
              d.style.width = '500px';
              d.style.height = '8px';
              d.style.background = 'transparent';
              document.body.appendChild(d);
              return { scrollWidth: document.documentElement.scrollWidth,
                       clientWidth: document.documentElement.clientWidth };
            }"""
        )
        metrics = page.evaluate(MEASURE_JS)
    finally:
        page.close()

    assert injected["scrollWidth"] > injected["clientWidth"] + 1, (
        f"注入 500px div 后仍未撑宽文档：{injected}"
    )
    problems = overflow_violations(metrics, page="scan", width=390)
    assert problems, "反向对照失效：注入 500px 元素后，零横向滚动谓词依然判为通过"
    assert any("500" in p or "越出视口" in p for p in problems), (
        f"溢出谓词没有指出注入元素：{problems}"
    )


def test_font_gate_detects_injected_11px_body(mobile_session):
    """反向对照：把一段正文注入为 11px，字号断言必须失败。"""
    browser = mobile_session["browser"]
    site = mobile_session["site"]
    page = browser.new_page(viewport={"width": 390, "height": 800})
    try:
        _open_paged(page, site, "scan")
        page.evaluate(
            """() => {
              const p = document.createElement('p');
              p.setAttribute('data-injected-violation', 'font');
              p.textContent = '这一段是故意注入的 11px 正文，用来证明字号门禁不是空转。';
              p.style.fontSize = '11px';
              document.querySelector('main').appendChild(p);
            }"""
        )
        metrics = page.evaluate(MEASURE_JS)
    finally:
        page.close()

    problems = font_violations(metrics, page="scan", width=390)
    assert problems, "反向对照失效：注入 11px 正文后，字号谓词依然判为通过"
    assert any("11.0" in p or "11px" in p or "小于 12" in p for p in problems), (
        f"字号谓词没有指出 11px 注入：{problems}"
    )


def test_layering_gate_detects_trace_moved_out_of_fold(mobile_session):
    """反向对照：把 evidence_id 挪到默认可见处，分层断言必须失败。"""
    browser = mobile_session["browser"]
    site = mobile_session["site"]
    page = browser.new_page(viewport={"width": 390, "height": 800})
    try:
        _open_paged(page, site, "dossier")
        metrics_before = page.evaluate(MEASURE_JS)
        assert metrics_before["visibleTraceCount"] == 0, (
            f"注入前默认视图已出现 {metrics_before['visibleTraceCount']} 处追溯内容，无法做反向对照"
        )

        page.evaluate(
            """() => {
              const p = document.createElement('p');
              p.setAttribute('data-injected-violation', 'trace');
              p.textContent = 'ev_77dfe41a85bfec068604';
              document.body.appendChild(p);
            }"""
        )
        metrics_after = page.evaluate(MEASURE_JS)
    finally:
        page.close()

    problems = layering_violations(metrics_after, page="dossier")
    assert problems, "反向对照失效：把 evidence_id 放到默认可见位置后，分层谓词依然判为通过"
    assert "ev_77dfe41a85bfec068604" in problems[0], f"分层谓词没有指出注入的 evidence_id：{problems}"


def test_tap_gate_detects_injected_small_button(mobile_session):
    """反向对照：注入一个 24×20 的按钮，触屏目标断言必须失败。"""
    browser = mobile_session["browser"]
    site = mobile_session["site"]
    page = browser.new_page(viewport={"width": 390, "height": 800})
    try:
        _open_paged(page, site, "diet")
        page.evaluate(
            """() => {
              const b = document.createElement('button');
              b.type = 'button';
              b.textContent = '注入的小按钮';
              b.style.width = '24px';
              b.style.height = '20px';
              b.style.padding = '0';
              b.style.minHeight = '20px';
              document.querySelector('main').appendChild(b);
            }"""
        )
        metrics = page.evaluate(MEASURE_JS)
    finally:
        page.close()

    problems = tap_violations(metrics, page="diet", width=390)
    assert problems, "反向对照失效：注入 24×20 按钮后，触屏目标谓词依然判为通过"
    assert any("注入的小按钮" in p for p in problems), f"触屏谓词没有指出注入按钮：{problems}"
