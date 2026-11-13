"""门禁 12：消费者主线可读性（真实 Chromium，390px / 1280px）。

为什么单独存在：门禁 8d 已经把「手机点得中、字够大、没有横向滚动」变成断言，
但它允许一个**技术上完全合格、读者依然看不懂**的页面 —— 首屏挂着 `evidence_id`、
段落以 `01`/`02` 编号、标题是衬线公文腔、正文全是 12px 小字。这些都不报错、
不崩、不发请求，旧门禁可以全绿而普通消费者在货架前依然不知道先看哪。

本门禁断言五件事，并为每件事提供一条「注入违规内容必然失败」的反向对照：

1. 首屏零审计术语：消费者主线首屏（viewport 内）不得出现
   `evidence_id` / `payload_sha256` / `visible_at` / 分片 / 哈希 / 可见性 / 追溯 /
   并列 / 观测 / 证据 / 区块 / 检索；
2. 三问可答：不展开任何折叠时，页面同时有「这是什么」「里面有什么」「谁做的」三个段落
   且各自有实体内容（先断言段落数与非空文本节点数，防止空转）；
3. 无编号仪式感：消费者视图里不得出现 `01`/`02` 形式的区块编号元素（审计视图允许）；
4. 字体规则：衬线体只出现在页面主标题与商品名上，消费者段落的标题与正文必须无衬线；
5. 步数上限：从打开页面到看见配料表原文，点击次数 ≤ 2（实测为 2）。

反向对照全部用 `page.evaluate` 在运行中的 DOM 上注入，不改 `apps/web/src`，并且
复用**同一批判定谓词**：谓词能同时说明「真实页面合格」和「注入后必然失败」。

实测基线（修复前 → 修复后，真实 Chromium，同一台机器）：

| 指标 | scan@390 | scan@1280 |
| --- | --- | --- |
| 首屏审计术语种类 | 6 → 0 | 6 → 0 |
| 首屏编号小标题 | 1 → 0 | 2 → 0 |
| 到配料表的点击 / 滚动 | 2 点击 + 13 滚 → 2 点击 + 4 滚 | 2 点击 + 6 滚 → 2 点击 + 2 滚 |
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

WIDTHS = (390, 1280)

#: 审计层术语。消费者主线首屏出现任意一个即失败。
AUDIT_TERMS = (
    "evidence_id",
    "payload_sha256",
    "visible_at",
    "分片",
    "哈希",
    "可见性",
    "追溯",
    "并列",
    "观测",
    "证据",
    "区块",
    "检索",
)

#: 三问的段落锚点（`data-section-key`，见 components/ui.tsx 的 DocSection plain 形态）。
QUESTIONS = {
    "consumer-identity": "这是什么",
    "consumer-ingredients": "里面有什么",
    "consumer-maker": "谁做的",
}

MAX_CLICKS_TO_INGREDIENTS = 2
MAX_SCROLLS_TO_INGREDIENTS = 8

#: 衬线字体族标识。注意不能用 `"serif" in family` 判定 ——
#: `sans-serif` 本身就含子串 `serif`，那样会让每条断言都命中。
SERIF_MARKERS = ("Songti", "Georgia", "Times", "Noto Serif", "Source Han Serif", "serif")


MEASURE_JS = r"""
() => {
  const vh = window.innerHeight;
  const doc = document.documentElement;

  const visible = (el) => {
    if (!el) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const firstFamily = (el) => {
    const raw = getComputedStyle(el).fontFamily || '';
    return raw.split(',')[0].replace(/["']/g, '').trim();
  };

  // ---------- 1. 首屏可见文本节点（viewport 内） ----------
  const nodes = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const text = node.data.trim();
    if (text.length < 2) continue;
    const el = node.parentElement;
    if (!el || !visible(el)) continue;
    if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE' || el.tagName === 'TEXTAREA') continue;
    const rect = el.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > vh) continue;
    nodes.push({
      text,
      tag: el.tagName,
      cls: String(el.className || '').slice(0, 60),
      family: firstFamily(el),
      inTitle: Boolean(el.closest('h1')),
      inProductName: Boolean(el.closest('[data-product-name]')),
    });
  }

  // ---------- 2. 三问段落 ----------
  const sections = Array.from(document.querySelectorAll('[data-section-key]')).map((el) => {
    const bodyText = (el.innerText || '').replace(/\s+/g, ' ').trim();
    return {
      key: el.getAttribute('data-section-key'),
      variant: el.getAttribute('data-section-variant'),
      heading: (el.querySelector('h2') || {}).textContent || '',
      textLength: bodyText.length,
      text: bodyText.slice(0, 120),
    };
  });

  // ---------- 3. 消费者视图里的编号元素 ----------
  const consumerSections = Array.from(document.querySelectorAll('[data-section-variant="consumer"]'));
  const numbers = [];
  for (const section of consumerSections) {
    for (const el of section.querySelectorAll('.section-num, .doc-block__num, [data-block-number]')) {
      numbers.push({ where: 'section-num', text: (el.textContent || '').trim(), cls: el.className });
    }
    // 只剩两位数字的独立节点也算「编号仪式感」：它唯一的语义就是序号。
    const w = document.createTreeWalker(section, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) {
      const t = w.currentNode.data.trim();
      if (!/^\d{2}$/.test(t)) continue;
      const el = w.currentNode.parentElement;
      if (!el) continue;
      const rect = el.getBoundingClientRect();
      // 数字也可能出现在配料序号 / 条码里，所以要求它是独立短节点且父级没有长文本
      const parentText = (el.textContent || '').replace(/\s+/g, '');
      if (parentText === t) numbers.push({ where: 'numeric-node', text: t, cls: String(el.className || '') });
    }
  }

  // ---------- 4. 衬线体分布 ----------
  const serifNodes = nodes.filter((n) => /Songti|Georgia|Times|Noto Serif|Source Han Serif/.test(n.family));
  const serifOutsideAllowed = serifNodes.filter((n) => !n.inTitle && !n.inProductName);

  // ---------- 5. 消费段落标题的字体 ----------
  const headings = Array.from(document.querySelectorAll('[data-section-variant="consumer"] h2')).map((el) => ({
    text: (el.textContent || '').trim(),
    family: firstFamily(el),
  }));

  const ingList = document.querySelectorAll('[data-section-key="consumer-ingredients"] ol li');

  return {
    viewport: { w: doc.clientWidth, h: vh },
    textNodes: nodes.length,
    texts: nodes.map((n) => n.text).join(' \n '),
    termHits: (() => {
      const terms = %TERMS%;
      const out = [];
      for (const n of nodes) for (const term of terms) {
        if (n.text.includes(term)) out.push({ term, text: n.text.slice(0, 80) });
      }
      return out;
    })(),
    sections,
    numbers,
    serifNodes: serifNodes.map((n) => ({ text: n.text.slice(0, 40), family: n.family, allowed: n.inTitle || n.inProductName })),
    serifOutsideAllowed,
    headings,
    openDetails: document.querySelectorAll('details[open]').length,
    ingredientItems: ingList.length,
    ingredientText: Array.from(ingList).map((li) => (li.textContent || '').trim()).join(' | '),
    titleFamily: (() => {
      const h1 = document.querySelector('h1');
      return h1 ? firstFamily(h1) : null;
    })(),
    productNameFamily: (() => {
      const el = document.querySelector('[data-product-name]');
      return el ? firstFamily(el) : null;
    })(),
  };
}
""".replace("%TERMS%", repr(list(AUDIT_TERMS)))


# --------------------------------------------------------------------------- 判定
# 这些谓词是门禁的真实逻辑。反向对照把「注入后的测量结果」喂给同一批谓词，
# 因此能证明判定不是空转。


def term_violations(metrics: dict) -> list[str]:
    """首屏（viewport 内）不得出现任何审计术语。"""
    return [
        f"首屏出现审计术语「{hit['term']}」：{hit['text']!r}"
        for hit in metrics["termHits"]
    ]


def question_violations(metrics: dict) -> list[str]:
    """三问必须各自有段落、有标题、有实体内容，且都不靠折叠展开。"""
    out: list[str] = []
    if metrics["openDetails"] != 0:
        out.append(f"页面存在 {metrics['openDetails']} 个默认展开的 <details>，三问必须不展开就成立")

    by_key = {item["key"]: item for item in metrics["sections"]}
    if len(metrics["sections"]) < len(QUESTIONS):
        out.append(
            f"只找到 {len(metrics['sections'])} 个消费者段落，预期至少 {len(QUESTIONS)} 个"
            f"（实际：{[s['key'] for s in metrics['sections']]}）"
        )
    for key, label in QUESTIONS.items():
        item = by_key.get(key)
        if item is None:
            out.append(f"缺少「{label}」段落（data-section-key={key}）")
            continue
        if label not in item["heading"]:
            out.append(f"「{label}」段落的标题不是「{label}」：{item['heading']!r}")
        if item["textLength"] < 20:
            out.append(f"「{label}」段落几乎没有可观内容（{item['textLength']} 字符）：{item['text']!r}")
    return out


def numbering_violations(metrics: dict) -> list[str]:
    """消费者视图不得出现 01 / 02 形式的编号元素。"""
    return [
        f"消费者视图出现区块编号「{item['text']}」（{item['where']}，class={item['cls']!r}）"
        for item in metrics["numbers"]
    ]


def font_violations(metrics: dict) -> list[str]:
    """衬线体只允许出现在主标题与商品名；段落标题与正文必须无衬线。"""
    out: list[str] = []
    title = metrics["titleFamily"] or ""
    if not any(marker in title for marker in SERIF_MARKERS):
        out.append(f"页面主标题必须保留衬线体，实际字族：{title!r}")
    if metrics["headings"] and metrics["productNameFamily"] is None:
        # 没有商品名（例如未查询状态）时不强求，但一旦有商品名就必须是衬线
        pass
    for item in metrics["headings"]:
        if any(marker in item["family"] for marker in SERIF_MARKERS):
            out.append(f"消费者段落标题「{item['text']}」用了衬线体（{item['family']}）")
    for item in metrics["serifOutsideAllowed"]:
        out.append(f"衬线体出现在主标题与商品名之外：「{item['text']}」（{item['family']}）")
    return out


def step_violations(steps: dict) -> list[str]:
    """从打开页面到看见配料表原文的点击数上限。"""
    out: list[str] = []
    if not steps.get("found"):
        out.append("没有找到配料表原文，步数断言无法成立")
        return out
    if steps["clicks"] > MAX_CLICKS_TO_INGREDIENTS:
        out.append(
            f"到配料表原文需要 {steps['clicks']} 次点击，超过上限 {MAX_CLICKS_TO_INGREDIENTS}"
        )
    if steps["scrolls"] > MAX_SCROLLS_TO_INGREDIENTS:
        out.append(
            f"到配料表原文需要 {steps['scrolls']} 次滚动，超过上限 {MAX_SCROLLS_TO_INGREDIENTS}"
        )
    return out


# --------------------------------------------------------------------------- 夹具


@pytest.fixture(scope="module")
def consumer_session():
    if not (DIST / "index.html").exists():
        pytest.skip("apps/web/dist 未构建；请先执行 npm run build:web")

    with static_site_server(DIST) as site:
        with playwright.sync_playwright() as api:
            browser, _ = launch_chromium(api, args=["--no-sandbox"])
            try:
                yield {"browser": browser, "site": site}
            finally:
                browser.close()


def _lookup(page, gtin: str = "6901234567892") -> None:
    """走真实静态分片：点演示样例条码 → 点主按钮。"""
    page.locator("input[aria-label='条码']").fill(gtin)
    page.get_by_role("button", name="看看是什么").click()
    page.wait_for_timeout(1800)


def _open_result(browser, site: str, width: int):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.goto(site, wait_until="load")
    page.wait_for_timeout(700)
    _lookup(page)
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    return page


@pytest.fixture(scope="module")
def consumer_metrics(consumer_session):
    out: dict[int, dict] = {}
    for width in WIDTHS:
        page = _open_result(consumer_session["browser"], consumer_session["site"], width)
        try:
            out[width] = page.evaluate(MEASURE_JS)
        finally:
            page.close()
    return out


# --------------------------------------------------------------------------- 正向断言


def test_first_screen_has_no_audit_terms(consumer_metrics):
    """首屏零术语：390px 与 1280px 都不许出现审计层词汇。

    修复前实测：扫码页首屏 390px 有 6 种审计术语（`分片` / `区块` / `并列` / `档案` /
    `检索` / `追溯`），1280px 有 6 种（再多一条静态分片 GET 路径）。
    """
    problems: list[str] = []
    checked = 0
    for width, metrics in consumer_metrics.items():
        checked += 1
        # 先自证样本非空，避免「首屏啥也没有所以零命中」的假绿灯
        assert metrics["textNodes"] >= 20, f"{width}px 首屏只有 {metrics['textNodes']} 个文本节点，门禁无法成立"
        problems.extend(f"{width}px: {p}" for p in term_violations(metrics))
    assert checked == len(WIDTHS)
    assert not problems, "消费者首屏出现审计术语：\n" + "\n".join(problems[:8])


def test_three_questions_answered_without_opening_folds(consumer_metrics):
    """三问可答：不展开任何折叠，三段各有标题与实体内容。"""
    problems: list[str] = []
    checked = 0
    for width, metrics in consumer_metrics.items():
        checked += 1
        # 先自证样本非空：空页面也能「三问都不缺」，所以节点数必须够。
        assert metrics["textNodes"] >= 18, (
            f"{width}px 首屏只有 {metrics['textNodes']} 个可见文本节点，样本不足以证明「可答」"
        )
        problems.extend(f"{width}px: {p}" for p in question_violations(metrics))
    assert checked == len(WIDTHS)

    # 先自证：三个段落真的存在，且配料表原文已渲染（不是空页面）
    for width, metrics in consumer_metrics.items():
        keys = {item["key"] for item in metrics["sections"]}
        assert set(QUESTIONS) <= keys, f"{width}px 缺少三问段落：{sorted(set(QUESTIONS) - keys)}"
        assert metrics["ingredientItems"] >= 3, (
            f"{width}px 配料表原文只有 {metrics['ingredientItems']} 项，三问里的「里面有什么」是空的"
        )
        assert "全脂乳粉" in metrics["ingredientText"], (
            f"{width}px 配料表里没有出现夹具里的原文：{metrics['ingredientText'][:80]!r}"
        )
    assert not problems, "三问无法在不展开折叠的情况下回答：\n" + "\n".join(problems[:8])


def test_consumer_view_has_no_block_numbering(consumer_metrics):
    """无编号仪式感：消费者视图里没有 01 / 02 这类序号元素。

    修复前实测：扫码页有 6 个编号小标题（01–06），390px 首屏能看到 1 个、1280px 能看到 2 个。
    """
    problems: list[str] = []
    checked = 0
    for width, metrics in consumer_metrics.items():
        checked += 1
        problems.extend(f"{width}px: {p}" for p in numbering_violations(metrics))
    assert checked == len(WIDTHS)
    assert not problems, "消费者视图仍有区块编号：\n" + "\n".join(problems[:8])


def test_serif_is_confined_to_title_and_product_name(consumer_metrics):
    """衬线体只给主标题与商品名；段落标题与正文必须无衬线。"""
    problems: list[str] = []
    checked = 0
    for width, metrics in consumer_metrics.items():
        checked += 1
        # 自证：主标题与商品名确实用了衬线，否则「只在两处」可以被「全都不用衬线」蒙混
        assert metrics["titleFamily"] and any(
            marker in metrics["titleFamily"] for marker in SERIF_MARKERS
        ), f"{width}px 主标题丢了衬线体：{metrics['titleFamily']!r}"
        assert metrics["productNameFamily"] and any(
            marker in metrics["productNameFamily"] for marker in SERIF_MARKERS
        ), f"{width}px 商品名丢了衬线体：{metrics['productNameFamily']!r}"
        problems.extend(f"{width}px: {p}" for p in font_violations(metrics))
    assert checked == len(WIDTHS)
    assert not problems, "衬线体越界或段落标题不合格：\n" + "\n".join(problems[:8])


def _walk_to_ingredients(page, site: str) -> dict:
    """真实操作路径：点样例条码 → 点主按钮 → 滚到配料表原文。逐步计数。"""
    steps = {"clicks": 0, "scrolls": 0, "notes": [], "found": False}
    page.goto(site, wait_until="load")
    page.wait_for_timeout(700)
    page.get_by_role("button", name="禾丰全脂乳粉 (6901234567892)").first.click()
    steps["clicks"] += 1
    steps["notes"].append("点击演示样例条码")
    page.wait_for_timeout(250)
    page.get_by_role("button", name="看看是什么").click()
    steps["clicks"] += 1
    steps["notes"].append("点击主按钮「看看是什么」")
    page.wait_for_timeout(1800)

    target = page.locator("[data-section-key='consumer-ingredients'] ol li").filter(has_text="全脂乳粉").first
    try:
        target.wait_for(state="attached", timeout=5000)
    except Exception:
        return steps

    for index in range(200):
        box = target.bounding_box()
        if box and -1 <= box["y"] < 700:
            break
        page.mouse.wheel(0, 300)
        steps["scrolls"] += 1
        page.wait_for_timeout(60)
    steps["found"] = True
    box = target.bounding_box()
    steps["targetY"] = round(box["y"], 1) if box else None
    return steps


def test_steps_to_ingredient_list_within_limit(consumer_session):
    """步数上限：两次点击之内就能看到配料表原文（手机与桌面都测）。

    修复前实测：390px 需要 2 次点击 + 13 次滚动（目标停在 640.8px），
    1280px 需要 2 次点击 + 6 次滚动。
    """
    problems: list[str] = []
    results: dict[int, dict] = {}
    for width in WIDTHS:
        page = consumer_session["browser"].new_page(viewport={"width": width, "height": 900})
        try:
            results[width] = _walk_to_ingredients(page, consumer_session["site"])
        finally:
            page.close()
    for width, steps in results.items():
        problems.extend(f"{width}px: {p}" for p in step_violations(steps))
    assert not problems, "到配料表原文的步数超限：\n" + "\n".join(problems[:8])


# --------------------------------------------------------------------------- 反向对照


def _inject(page, script: str) -> dict:
    page.evaluate(script)
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(150)
    return page.evaluate(MEASURE_JS)


def test_term_gate_detects_injected_evidence_id_on_first_screen(consumer_session):
    """反向对照 1：把 evidence_id 注入首屏 → 零术语断言必须失败。"""
    page = _open_result(consumer_session["browser"], consumer_session["site"], 390)
    try:
        assert not term_violations(page.evaluate(MEASURE_JS)), "注入前首屏已有审计术语，无法做反向对照"
        metrics = _inject(
            page,
            """() => {
              const p = document.createElement('p');
              p.setAttribute('data-injected-violation', 'term');
              p.textContent = '这块数据带 evidence_id：ev_77dfe41a85bfec068604';
              p.style.cssText = 'font-size:14px;line-height:1.6;color:#17242b;padding:6px 0';
              const host = document.querySelector('[data-section-key="consumer-identity"]');
              // 必须注入到首屏可见处：追加到段落末尾会落到折叠线以下，反向对照会变成空转。
              host.insertBefore(p, host.firstChild);
            }""",
        )
    finally:
        page.close()

    problems = term_violations(metrics)
    assert problems, "反向对照失效：注入 evidence_id 后，零术语谓词依然判为通过"
    assert any("evidence_id" in p for p in problems), f"谓词没有指出注入的术语：{problems}"


def test_numbering_gate_detects_injected_block_number(consumer_session):
    """反向对照 2：把 01 编号注入消费者段落 → 编号断言必须失败。"""
    page = _open_result(consumer_session["browser"], consumer_session["site"], 390)
    try:
        assert not numbering_violations(page.evaluate(MEASURE_JS)), "注入前已有编号，无法做反向对照"
        metrics = _inject(
            page,
            """() => {
              const span = document.createElement('span');
              span.setAttribute('data-injected-violation', 'number');
              span.setAttribute('data-block-number', '01');
              span.className = 'section-num';
              span.textContent = '01';
              document.querySelector('[data-section-key="consumer-identity"]').prepend(span);
            }""",
        )
    finally:
        page.close()

    problems = numbering_violations(metrics)
    assert problems, "反向对照失效：注入 01 编号后，编号谓词依然判为通过"
    assert any("01" in p for p in problems), f"谓词没有指出注入的编号：{problems}"


def test_font_gate_detects_injected_serif_body(consumer_session):
    """反向对照 3：把正文与段落标题注入衬线体 → 字体断言必须失败。"""
    page = _open_result(consumer_session["browser"], consumer_session["site"], 390)
    try:
        assert not font_violations(page.evaluate(MEASURE_JS)), "注入前字体已越界，无法做反向对照"
        metrics = _inject(
            page,
            """() => {
              const host = document.querySelector('[data-section-key="consumer-identity"]');
              const h = host.querySelector('h2');
              h.style.fontFamily = 'Songti SC, serif';
              const p = document.createElement('p');
              p.setAttribute('data-injected-violation', 'serif');
              p.textContent = '这一段被注入成衬线体，用来证明字体门禁不是空转。';
              p.style.cssText = "font-family:'Songti SC', serif;font-size:15px;line-height:1.6;padding:6px 0";
              // 追加到段落末尾会落到折叠线以下、viewport 外的正文收集不到，
              // 反向对照就只剩标题那一条命中，正文规则等于没被验证；这里插到标题后面。
              h.insertAdjacentElement('afterend', p);
            }""",
        )
    finally:
        page.close()

    problems = font_violations(metrics)
    assert problems, "反向对照失效：注入衬线体后，字体谓词依然判为通过"
    assert any("衬线" in p for p in problems), f"字体谓词没有指出注入的衬线体：{problems}"


def test_step_gate_detects_extra_click(consumer_session):
    """反向对照 4：多走两步（多一次查询）→ 步数上限断言必须失败。

    这不是伪造计数：第二次查询按钮是真的点下去的，只是那一步对
    「看到配料表原文」没有贡献，正是这类多余步骤把新用户挡在门外。
    """
    page = consumer_session["browser"].new_page(viewport={"width": 390, "height": 900})
    try:
        steps = _walk_to_ingredients(page, consumer_session["site"])
        assert all(not p for p in step_violations(steps)), f"基线路径已超限，无法做反向对照：{step_violations(steps)}"

        # 多走一次「查同一件东西」：真实点击，不伪造计数
        page.evaluate("() => window.scrollTo(0, 0)")
        page.get_by_role("button", name="看看是什么").click()
        page.wait_for_timeout(600)
        steps["clicks"] += 1
        steps["notes"].append("（注入）多余的第二次查询")
        extra = {
            "found": True,
            "clicks": steps["clicks"],
            "scrolls": 0,
        }
    finally:
        page.close()

    problems = step_violations(extra)
    assert problems, "反向对照失效：多一次点击后，步数谓词依然判为通过"
    assert any("超过上限" in p for p in problems), f"步数谓词没有报出超限：{problems}"
