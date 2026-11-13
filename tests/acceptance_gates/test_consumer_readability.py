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

import json
import re
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

  // ---------- 6. 「大致成分」概览 ----------
  // 概览只允许放可数的原文事实。这里量的是「页面上真的写了什么」，
  // 判定（项数对不对、命中项有没有逐字列出、有没有阈值句）放在 Python 谓词里。
  const sec = document.querySelector('[data-section-key="consumer-ingredients"]');
  const overview = sec ? sec.querySelector('[data-ingredients-overview]') : null;
  const countEl = overview ? overview.querySelector('[data-ingredient-count]') : null;
  const additiveEl = overview ? overview.querySelector('[data-additive-label]') : null;
  const allergenHead = sec
    ? Array.from(sec.querySelectorAll('.consumer-subhead')).find((el) =>
        (el.textContent || '').includes('致敏原')) || null
    : null;
  const allergenBlock = allergenHead ? allergenHead.nextElementSibling : null;
  const ingOl = sec ? sec.querySelector('ol') : null;
  const topOf = (el) => (el ? Math.round(el.getBoundingClientRect().top * 10) / 10 : null);

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
    // 配料原文（术语列）：用来交叉核对概览里的字面检测，不读 data 属性、不自证预期。
    ingredientNames: Array.from(sec ? sec.querySelectorAll('ol .term-cell') : []).map((el) =>
      (el.textContent || '').trim(),
    ),
    titleFamily: (() => {
      const h1 = document.querySelector('h1');
      return h1 ? firstFamily(h1) : null;
    })(),
    productNameFamily: (() => {
      const el = document.querySelector('[data-product-name]');
      return el ? firstFamily(el) : null;
    })(),
    // ---------- 6. 概览区（值为「页面上真实的字符串」，不是预期值） ----------
    overview: {
      present: Boolean(overview),
      top: topOf(overview),
      countText: countEl ? (countEl.textContent || '').trim() : null,
      additiveText: additiveEl ? (additiveEl.textContent || '').trim() : null,
      // 概览区整体文本，用于扫阈值句 / 评价词 / 分类词
      text: overview ? (overview.innerText || '').replace(/\s+/g, ' ').trim() : '',
    },
    allergen: {
      headText: allergenHead ? (allergenHead.textContent || '').trim() : null,
      text: allergenBlock ? (allergenBlock.innerText || '').replace(/\s+/g, ' ').trim() : '',
      top: topOf(allergenBlock),
    },
    ingredientListTop: topOf(ingOl),
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


# ------------------------------------------------ 概览区（大致成分）

#: 页面上不允许出现的「阈值 / 比例 / 分类」句式。
#:
#: 每一项都必须能被「注入一句话」触发，否则就是空转。
#: 注意这里只匹配**新的判断句式**，不拦截原文里本来就有的词
#: （例如配料原文里真写着「食品添加剂」时，那是来源原话，不算违规）。
OVERVIEW_BANNED_PATTERNS = (
    # 阈值：以任意数字作为切分点（「超过 3 项」「不到 5 种」「≥ 2 项」）
    (r"(超过|低于|不到|多于|少于|至少|高达)\s*[0-9一二三四五六七八九十百]\s*(项|种|个|类)",
     "阈值判断"),
    (r"[0-9一二三四五六七八九十百]\s*(项|种)\s*(以上|以下|及以上|及以下)",
     "阈值判断"),
    # 占比：用比例暗示程度
    (r"[0-9.]+\s*%", "占比判断"),
    (r"(占|比例|占比)[^。，]{0,8}([0-9一二三四五六七八九十百]|半|多数|少数)", "占比判断"),
    # 评价 / 建议 / 风险 / 功效
    (r"(风险|安全|可靠|真伪|合格|优质|健康|营养好|高钠|低卡|低脂|高糖|高油|无害|有害)",
     "评价判断"),
    (r"(建议|推荐|值得买|不要买|可以买|谨慎购买|踩雷)", "购买建议"),
)

#: 分类词：配料条目没有 category / section 字段，贴类别标签必然是推断。
#: 只扫描「概览区」，不扫描配料原文本身（原文里写着什么就显示什么）。
CATEGORY_TERMS = (
    "乳化剂",
    "增稠剂",
    "防腐剂",
    "甜味剂",
    "着色剂",
    "抗氧化剂",
    "膨松剂",
    "稳定剂",
    "抗结剂",
    "漂白剂",
    "护色剂",
    "酸度调节剂",
    "加工助剂",
    "添加剂项数",
    "添加剂占比",
)


def overview_violations(metrics: dict) -> list[str]:
    """概览区只许出现可数的原文事实：项数、字面命中、原文数值。

    断言三件事：
    1. 概览区在（`data-ingredients-overview`），且项数文案里的 N 等于页面上
       真实的配料条目数（N 取自 `ol li` 的条数，不读任何 data 属性）；
    2. 「食品添加剂」这条：无命中时明确写「未出现」，有命中时逐字列出命中项原文；
       命中的每一条都必须能在配料术语列里找到同一条原文（证明是抄来的，不是编的）；
    3. 概览区不得出现阈值句、占比句、评价词、分类词。
    """
    out: list[str] = []
    overview = metrics.get("overview") or {}
    if not overview.get("present"):
        return ["「里面有什么」缺少概览区（data-ingredients-overview），概览断言无法成立"]

    # ---- 1. 项数：N 必须等于页面上真的渲染出来的配料条目数
    count_text = overview.get("countText") or ""
    match = re.search(r"共列出\s*(\d+)\s*项配料", count_text)
    if not match:
        out.append(f"项数文案不符合「包装上共列出 N 项配料」：{count_text!r}")
    else:
        n_claimed = int(match.group(1))
        n_rendered = metrics["ingredientItems"]
        if n_claimed != n_rendered:
            out.append(f"项数文案写 {n_claimed} 项，但页面上渲染了 {n_rendered} 项配料")
    if "按原文顺序" not in count_text:
        out.append(f"项数文案没有说明「按原文顺序」：{count_text!r}")

    # ---- 2. 「食品添加剂」字面检测
    additive_text = overview.get("additiveText") or ""
    names = metrics.get("ingredientNames") or []
    literal_hits = [name for name in names if "食品添加剂" in name]
    if literal_hits:
        if "未出现" in additive_text:
            out.append(
                "配料原文里有「食品添加剂」，概览却说未出现："
                f"命中 {literal_hits} / 文案 {additive_text!r}"
            )
        for hit in literal_hits:
            if hit not in additive_text:
                out.append(f"命中项没有逐字列出原文：{hit!r} 不在 {additive_text!r}")
        if "食品添加剂" not in additive_text:
            out.append(f"有命中却没写「食品添加剂」字样：{additive_text!r}")
    else:
        if "未出现「食品添加剂」标注" not in additive_text:
            out.append(f"无命中时没有写「未出现「食品添加剂」标注」：{additive_text!r}")

    # ---- 3. 阈值 / 占比 / 评价 / 分类
    text = overview.get("text") or ""
    for pattern, kind in OVERVIEW_BANNED_PATTERNS:
        for hit in re.finditer(pattern, text):
            out.append(f"概览区出现{kind}：{hit.group(0)!r}（原文：{text[:90]!r}）")
    for term in CATEGORY_TERMS:
        if term in text:
            out.append(f"概览区出现分类词「{term}」：{text[:90]!r}")
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


def test_overview_shows_countable_facts_only(consumer_metrics):
    """概览区（大致成分）只放可数的原文事实，且 N 等于真实条目数。

    正向三条：
    - 项数文案「包装上共列出 N 项配料，按原文顺序」存在，N == `ol li` 条数；
    - 夹具这条包装原文里没有「食品添加剂」字样 → 概览必须写「未出现」；
    - 过敏原在概览区之后紧跟可见，不被长配料表压到后面。
    """
    problems: list[str] = []
    checked = 0
    for width, metrics in consumer_metrics.items():
        checked += 1
        assert metrics["ingredientItems"] >= 3, f"{width}px 配料条目不足，概览断言无法成立"
        problems.extend(f"{width}px: {p}" for p in overview_violations(metrics))
    assert checked == len(WIDTHS)
    assert not problems, "概览区不合格：\n" + "\n".join(problems[:8])


def test_allergen_sits_above_the_long_ingredient_list(consumer_metrics):
    """过敏原原文必须紧邻概览、位于配料长表之上。

    实测坐标：概览块的顶边 < 致敏原小标题的顶边 < 配料表 `ol` 的顶边。
    这三条顺序一旦被「把致敏原挪到配料表之后」破坏，断言就失败。
    """
    problems: list[str] = []
    for width, metrics in consumer_metrics.items():
        allergen = metrics["allergen"]
        if not allergen["headText"]:
            problems.append(f"{width}px: 没有找到致敏原小标题")
            continue
        if "含有乳及乳制品" not in allergen["text"]:
            problems.append(f"{width}px: 致敏原段落里没有夹具原文：{allergen['text'][:60]!r}")
        overview_top = metrics["overview"].get("top")
        if overview_top is None:
            problems.append(f"{width}px: 概览区没有测量到顶边坐标")
            continue
        if metrics["ingredientListTop"] is None:
            problems.append(f"{width}px: 没有找到配料表 ol，无法判断先后")
            continue
        if not (overview_top < allergen["top"] < metrics["ingredientListTop"]):
            problems.append(
                f"{width}px: 顺序不对——概览 top={overview_top}、致敏原 top={allergen['top']}、"
                f"配料表 top={metrics['ingredientListTop']}（要求概览 < 致敏原 < 配料表）"
            )
    assert not problems, "致敏原没有排在概览与长配料表之间：\n" + "\n".join(problems[:8])


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


def test_overview_gate_detects_injected_additive_label(consumer_session):
    """反向对照 5：让包装记录里真的多出一项含「食品添加剂」的配料。

    注入走 `page.route` 改**返回的配料原文**（不改 `apps/web/src`），再走真实点击，
    这样测的是「渲染层真的去读每项配料的 raw_name」：如果概览把「未出现」写死，
    这条断言就会失败。只在 DOM 上追加一个 `<li>` 是测不出来的——React 不会因此重算。
    """
    page = consumer_session["browser"].new_page(viewport={"width": 390, "height": 900})
    checked = {"routed": 0}
    try:

        def add_entry(route):
            payload = json.loads((DIST / "data" / "v1" / "ingredients" / "ent_0fdf238ca1c25b41.json").read_text(encoding="utf-8"))
            payload["ingredients"].append(
                {
                    "order": len(payload["ingredients"]) + 1,
                    "raw_name": "食品添加剂（山梨酸钾）",
                    "search_keys": [],
                    "source_id": "label_transcript",
                    "evidence_id": payload["ingredients"][0]["evidence_id"],
                    "retrieved_at": payload["ingredients"][0]["retrieved_at"],
                    "raw_value": "食品添加剂（山梨酸钾）",
                }
            )
            checked["routed"] += 1
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(payload, ensure_ascii=False),
            )

        page.route("**/data/v1/ingredients/*.json", add_entry)
        page.goto(consumer_session["site"], wait_until="load")
        page.wait_for_timeout(700)
        _lookup(page)
        metrics = page.evaluate(MEASURE_JS)
    finally:
        page.close()

    assert checked["routed"] == 1, f"注入的配料记录没有被请求到（route 命中 {checked['routed']} 次）"
    assert metrics["ingredientItems"] == 4, (
        f"注入后配料条目应为 4 项，实际 {metrics['ingredientItems']}"
    )

    additive_text = metrics["overview"]["additiveText"] or ""
    assert "食品添加剂" in additive_text, (
        f"配料原文里写着「食品添加剂」，概览仍没有出现该字样：{additive_text!r}"
    )
    assert "食品添加剂（山梨酸钾）" in additive_text, (
        f"概览没有逐字列出命中项原文（应原样抄，不得改写）：{additive_text!r}"
    )
    assert "未出现" not in additive_text, f"已有命中时仍写「未出现」：{additive_text!r}"

    # 项数文案必须自己跟着数据变成 4（不是写死的 3）
    count_text = metrics["overview"]["countText"] or ""
    assert "共列出 4 项配料" in count_text, f"项数文案没有跟随注入后的条目数：{count_text!r}"

    problems = overview_violations(metrics)
    assert not problems, f"注入一条原文含「食品添加剂」的配料后，概览仍被判不合格：{problems}"

    # 反证：把概览退回「未出现」的硬编码文案，同一批谓词必须失败
    tampered = json.loads(json.dumps(metrics))
    tampered["overview"]["additiveText"] = "包装原文未出现「食品添加剂」标注。"
    problems = overview_violations(tampered)
    assert problems, "反向对照失效：概览写死「未出现」而配料里明明有命中时，谓词依然判为通过"
    assert any("未出现" in p for p in problems), f"谓词没有指出文案与原文矛盾：{problems}"


def test_overview_gate_detects_injected_threshold_sentence(consumer_session):
    """反向对照 6：往概览区注入阈值句「添加剂占比超过 3 项」→ 必须被拦住。

    阈值不是「数据里本来就有的数字」，而是**人为切分点**：配料条目里没有任何
    字段能支持「几项以上算多」。因此门禁把「数量 + 项/种 + 超过/以上」列为禁用句式，
    这条对照证明它真的拦得住，而不是只写在注释里。
    """
    page = _open_result(consumer_session["browser"], consumer_session["site"], 390)
    try:
        assert not overview_violations(page.evaluate(MEASURE_JS)), "注入前概览已不合格，无法做反向对照"
        page.evaluate(
            """() => {
              const host = document.querySelector('[data-ingredients-overview]');
              const p = document.createElement('p');
              p.setAttribute('data-injected-violation', 'threshold');
              p.textContent = '添加剂占比超过 3 项，需要留意。';
              host.appendChild(p);
            }"""
        )
        metrics = page.evaluate(MEASURE_JS)
    finally:
        page.close()

    problems = overview_violations(metrics)
    assert problems, "反向对照失效：注入阈值句后，概览谓词依然判为通过"
    assert any("阈值" in p for p in problems), f"谓词没有指出阈值句式：{problems}"
    assert any("超过 3 项" in p for p in problems), f"谓词没有抓到注入的原句：{problems}"

    # 同一句若只是「分类词」也必须在另一条规则上命中（阈值与分类两条各自都能失败）
    assert any("分类词" in p for p in problems), (
        f"「添加剂占比」属于分类式表述，分类词规则也应命中：{problems}"
    )


def test_overview_gate_detects_injected_rating_word(consumer_session):
    """反向对照 7：注入评价词 / 购买建议 → 概览谓词必须命中。

    这条补的是「阈值之外」的另一类越界：不加阈值、不分类，但直接给评价。
    """
    page = _open_result(consumer_session["browser"], consumer_session["site"], 390)
    try:
        assert not overview_violations(page.evaluate(MEASURE_JS)), "注入前概览已不合格，无法做反向对照"
        page.evaluate(
            """() => {
              const host = document.querySelector('[data-ingredients-overview]');
              const p = document.createElement('p');
              p.setAttribute('data-injected-violation', 'rating');
              p.textContent = '这款属于高钠，不太健康，不推荐买。';
              host.appendChild(p);
            }"""
        )
        metrics = page.evaluate(MEASURE_JS)
    finally:
        page.close()

    problems = overview_violations(metrics)
    assert problems, "反向对照失效：注入评价词后，概览谓词依然判为通过"
    assert any("评价判断" in p or "购买建议" in p for p in problems), (
        f"谓词没有指出评价 / 建议语气：{problems}"
    )
