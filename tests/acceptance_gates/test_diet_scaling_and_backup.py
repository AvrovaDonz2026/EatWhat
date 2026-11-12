"""门禁 11：饮食计划按克重折算 + 本地备份导出/导入（真实浏览器）。

门禁 8 只证明「个人数据不出网」，从未验证过「计划数值是否真的按营养表算对」，
也没有验证过「备份导入失败时原数据是否仍然完好」。这两个坑都是**静默失败**：
折算写成 0、导入校验写漏、非法文件覆盖掉用户数据，页面都不会崩、旧门禁依然全绿。

因此本模块把三件事变成断言：
1. 折算结果必须等于「每 100 克原文数值 × 克重 / 100」的纯算术值，并展示折算所依据的
   entity / evidence；
2. 进度超出目标时只能用中性文字，不得出现红/黄/绿语义配色；
3. 备份必须能导出为本地文件、能导入回来；非法文件必须报错且**不改变**已有数据。

另外每个「拦截型」断言都配一条反向对照（故意注入违规内容，断言门禁会命中），
避免门禁空转。
"""
from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "apps" / "web" / "dist"
STATE = ROOT / ".state"

playwright = pytest.importorskip("playwright.sync_api", reason="需要 playwright 才能执行浏览器门禁")
from playwright.sync_api import sync_playwright  # noqa: E402

try:  # 兼容以 tests/ 为根、以及包内导入两种执行方式
    from ._browser_launch import launch_chromium
except ImportError:  # pragma: no cover
    from _browser_launch import launch_chromium  # type: ignore[no-redef]

# 演示条码 6901234567892 的每 100 克原文：
# 能量 2130 千焦 / 蛋白质 24.5 克 / 脂肪 26.8 克 / 碳水 39.6 克 / 钠 280 毫克
# 50 g 折算：1065 kJ / 12.25 → 12.3 g / 13.4 g / 19.8 g / 140 mg
SAMPLE_GTIN = "6901234567892"
SAMPLE_ENTITY = "ent_0fdf238ca1c25b41"

# 上传语义标记：备份功能只能用 FileReader 读本地文件。
# 检查函数与反向对照共用同一份标记表，保证「能被命中」有实证。
UPLOAD_MARKERS = (
    "navigator.sendBeacon",
    "new FormData",
    "multipart/form-data",
    "XMLHttpRequest",
    "WebSocket",
)


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
def session(site: str) -> Iterator[dict]:
    with sync_playwright() as api:
        browser, _executable = launch_chromium(api, args=["--no-sandbox"])
        yield {"browser": browser, "site": site}
        browser.close()


@pytest.fixture()
def page(session: dict, tmp_path: Path) -> Iterator[dict]:
    """每个测试一个全新 context：IndexedDB 从空开始，互不污染。"""
    context = session["browser"].new_context(accept_downloads=True)
    requests: list[dict[str, str]] = []
    context.on("request", lambda request: requests.append({"url": request.url, "method": request.method}))
    page = context.new_page()
    page.goto(session["site"], wait_until="load")
    page.wait_for_timeout(400)
    page.get_by_role("button", name="我的忌口与饮食计划").click()
    page.wait_for_timeout(300)
    yield {"page": page, "requests": requests, "site": session["site"], "tmp": tmp_path}
    context.close()


def _load_sample_product(page) -> None:
    page.get_by_label("折算依据条码").fill(SAMPLE_GTIN)
    page.get_by_role("button", name="载入档案营养表").click()
    page.wait_for_timeout(700)


def _read_idb(page) -> dict:
    """直接读原生 IndexedDB，拿到真实落盘状态（不依赖界面文本）。"""
    return page.evaluate(
        """async () => {
            const open = () => new Promise((resolve, reject) => {
                const request = indexedDB.open('product-public-dossier-local');
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error);
            });
            const all = (db, store) => new Promise((resolve, reject) => {
                const tx = db.transaction(store, 'readonly');
                const req = tx.objectStore(store).getAll();
                req.onsuccess = () => resolve(req.result);
                req.onerror = () => reject(req.error);
            });
            const db = await open();
            const out = {
                diet_rules: await all(db, 'diet_rules'),
                diet_plan: await all(db, 'diet_plan'),
                scan_history: await all(db, 'scan_history'),
            };
            db.close();
            return out;
        }"""
    )


def _export_backup(page, target: Path) -> dict:
    with page.expect_download() as download_info:
        page.get_by_role("button", name="导出本地备份").click()
    download = download_info.value
    download.save_as(str(target))
    return json.loads(target.read_text(encoding="utf-8"))


# ------------------------------------------------- 任务 C：按克重折算 + 进度


def test_diet_scaling_is_exact_arithmetic(page):
    """50 g 的折算结果必须逐项等于「每 100 克原文 × 0.5」。"""
    p = page["page"]
    _load_sample_product(p)
    assert SAMPLE_ENTITY in p.inner_text("body"), "界面必须展示折算依据的 entity_id"

    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(300)
    body = p.inner_text("body")

    assert "能量 1065kJ" in body, "2130 kJ/100g × 50/100 应等于 1065 kJ"
    assert "钠 140mg" in body, "280 mg/100g × 50/100 应等于 140 mg"
    assert "碳水化合物 19.8g" in body, "39.6 g/100g × 50/100 应等于 19.8 g"
    assert "脂肪 13.4g" in body, "26.8 g/100g × 50/100 应等于 13.4 g"
    assert "蛋白质 12.3g" in body, "24.5 g/100g × 50/100 应等于 12.25 g（显示一位小数 → 12.3）"

    # 不得出现任何评价性措辞（门禁 10 已扫禁用词，这里固定住「建议/推荐/超标」类形态）
    for phrase in ("健康建议", "推荐摄入", "超标"):
        assert phrase not in body, f"折算界面出现评价性措辞：{phrase}"


def test_diet_scaling_records_and_exposes_evidence(page):
    """记录后必须保留克重与折算所依据的 source_id / evidence_id。"""
    p = page["page"]
    _load_sample_product(p)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(250)

    # 折算依据必须在记录前就可见（消费视图：来源名 + 取得日期）
    assert "查看本次折算的营养来源与证据" in p.inner_text("body"), "缺少折算来源披露"

    p.get_by_role("button", name="记录", exact=True).click()
    p.wait_for_timeout(500)

    body = p.inner_text("body")
    assert "已计入 1065 / 每日目标 8400 kJ" in body, "今日累计必须按折算值累加"
    assert "· 50 g" in body, "记录条目必须保留本次克重"
    assert SAMPLE_ENTITY in body and "NUTRITION_LABEL" in body, "记录必须保留 entity 与折算基准"

    # 展开溯源自证：evidence_id 必须来自营养表原文那一条
    p.get_by_text("查看本条折算的来源与证据").click()
    p.wait_for_timeout(200)
    trace = p.inner_text("body")
    assert "ev_77dfe41a85bfec068604" in trace, "折算记录必须绑定营养表的 evidence_id"

    stored = _read_idb(p)["diet_plan"][0]
    log = stored["logs"][0]
    assert log["grams"] == 50, "克重必须落盘"
    assert log["entity_id"] == SAMPLE_ENTITY, "entity_id 必须落盘"
    assert log["nutrition_basis"] == "PER_100G", "折算基准必须落盘"
    assert log["evidence"] and log["evidence"][0]["evidence_id"], "证据绑定必须随记录落盘"


def test_diet_progress_over_target_is_neutral_text(page):
    """超出目标只能用中性文字标注，且不得出现红/黄/绿语义配色。"""
    p = page["page"]
    _load_sample_product(p)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(250)
    p.get_by_role("button", name="记录", exact=True).click()
    p.wait_for_timeout(500)

    p.locator('input[type="number"]').first.fill("1000")  # 把能量目标调低，触发超出
    p.wait_for_timeout(400)

    body = p.inner_text("body")
    assert "已超出目标 65 kJ" in body, "超出时必须用中性文字标注具体超出量"
    assert "已计入" in body and "每日目标" in body, "必须并列显示「已计入 / 每日目标」"

    html = p.content()
    for token in (
        "text-red-",
        "text-amber-",
        "text-yellow-",
        "text-green-",
        "bg-red-",
        "bg-amber-",
        "bg-green-",
        "text-danger",
    ):
        assert token not in html, f"出现红/黄/绿语义配色：{token}"
    assert "hairline-meter" in html, "进度必须使用发丝线进度条"


def test_diet_progress_bar_never_exceeds_track_width(page):
    """超出目标时进度条必须封顶在轨道内，不得越界画出容器。

    这是实测发现的风险点：`barWidth` 若不截断（或截断写成 `Math.max`），
    斜纹填充块会以 113% 这类宽度溢出容器，在窄屏上直接横向撑破页面。
    只断言「文字中性」抓不到这个缺陷，因此单独测几何。
    """
    p = page["page"]
    _load_sample_product(p)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(250)
    p.get_by_role("button", name="记录", exact=True).click()
    p.wait_for_timeout(500)

    p.locator('input[type="number"]').first.fill("1000")  # 能量目标 1000 < 已计入 1065
    p.wait_for_timeout(400)

    geometry = p.evaluate(
        """() => {
          const meters = [...document.querySelectorAll('.hairline-meter')];
          const fills = [...document.querySelectorAll('.hairline-meter__fill')];
          return meters.map((meter, index) => {
            const fill = fills[index];
            if (!fill) return null;
            const trackWidth = meter.getBoundingClientRect().width;
            const fillWidth = fill.getBoundingClientRect().width;
            const trackRight = meter.getBoundingClientRect().right;
            const fillRight = fill.getBoundingClientRect().right;
            return { trackWidth, fillWidth, overflowPx: fillRight - trackRight,
                     ratio: fillWidth / trackWidth };
          });
        }"""
    )

    assert geometry, "未找到任何进度条，门禁无法成立"
    assert any(item and item["ratio"] >= 0.99 for item in geometry), (
        "没有一条进度条达到满格，超出场景未被构造出来，门禁无法成立"
    )
    for index, item in enumerate(geometry):
        assert item is not None, f"第 {index} 条进度条缺少填充元素"
        assert item["fillWidth"] <= item["trackWidth"] + 0.5, (
            f"第 {index} 条进度条越界：填充 {item['fillWidth']:.1f}px > 轨道 {item['trackWidth']:.1f}px"
        )
        assert item["overflowPx"] <= 0.5, (
            f"第 {index} 条进度条右侧溢出容器 {item['overflowPx']:.1f}px（窄屏会横向撑破页面）"
        )
        assert item["ratio"] <= 1.005, f"第 {index} 条进度条填充比例 {item['ratio']:.2%} 超过 100%"

    assert "已超出目标 65 kJ" in p.inner_text("body"), "超出场景的文字标注应仍然中性呈现"


def test_diet_progress_bar_clamp_gate_detects_overflow(page):
    """反向对照：去掉 `Math.min(100, …)` 截断后，上面的几何断言必须失败。

    直接在真实页面上把填充宽度拉到 160% 来模拟「未截断」的实现，
    证明 `test_diet_progress_bar_never_exceeds_track_width` 的判定是有效的。
    """
    p = page["page"]
    _load_sample_product(p)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(250)
    p.get_by_role("button", name="记录", exact=True).click()
    p.wait_for_timeout(500)

    p.evaluate(
        """() => {
          const fill = document.querySelector('.hairline-meter__fill');
          if (fill) fill.style.width = '160%';   // 模拟 barWidth 未截断（写成 160%）
        }"""
    )
    # `.hairline-meter__fill` 带 240ms 过渡，必须等过渡走完再量几何，否则量到起始值
    p.wait_for_timeout(400)

    injected = p.evaluate(
        """() => {
          const fill = document.querySelector('.hairline-meter__fill');
          if (!fill) return null;
          const meter = fill.parentElement;
          const trackWidth = meter.getBoundingClientRect().width;
          const fillWidth = fill.getBoundingClientRect().width;
          return { trackWidth, fillWidth, overflowPx: fillWidth - trackWidth };
        }"""
    )

    assert injected is not None, "页面上没有进度条填充元素，无法做反向对照"
    assert injected["fillWidth"] > injected["trackWidth"] + 0.5, (
        "反向对照失效：注入 160% 后填充仍未越界"
    )
    assert injected["overflowPx"] > 0.5, (
        f"反向对照失效：未测出溢出（{injected['overflowPx']:.1f}px）"
    )


# ------------------------------- 100% 刻度线：读得出「已达上限」而不靠小字

# 中性墨色允许的最大色度差（max(r,g,b) - min(r,g,b)）。中性灰的色度差很小（实测
# `--ink-faint` = #5e6d75 → 差 23），任何红/黄/绿都会有明显的色度差。
_NEUTRAL_CHROMA_LIMIT = 32

_METER_TICK_PROBE = """() => {
  const meters = [...document.querySelectorAll('.hairline-meter')];
  return meters.map((meter) => {
    const fill = meter.querySelector('.hairline-meter__fill');
    const tick = meter.querySelector('.hairline-meter__tick');
    const meterRect = meter.getBoundingClientRect();
    const fillRect = fill ? fill.getBoundingClientRect() : null;
    const tickRect = tick ? tick.getBoundingClientRect() : null;
    const tickStyle = tick ? getComputedStyle(tick) : null;
    return {
      hasFill: Boolean(fill),
      hasTick: Boolean(tick),
      tickVisible: Boolean(
        tickRect && tickRect.width > 0 && tickRect.height > 0 &&
        tickStyle && tickStyle.display !== 'none' && tickStyle.visibility !== 'hidden'
      ),
      tickRadius: tickStyle ? tickStyle.borderTopLeftRadius : '',
      tickBackground: tickStyle ? tickStyle.backgroundColor : '',
      tickShadow: tickStyle ? tickStyle.boxShadow : '',
      trackWidth: meterRect.width,
      fillRatio: fillRect && meterRect.width ? fillRect.width / meterRect.width : 0,
      fillRight: fillRect ? fillRect.right : null,
      tickRight: tickRect ? tickRect.right : null,
    };
  });
}"""


def _meter_tick_report(page) -> list[dict]:
    return page.evaluate(_METER_TICK_PROBE)


def _px(value: str) -> float:
    """`getComputedStyle` 的像素字符串 → 浮点；非 px 值（% 等）返回 0。"""
    text = (value or "").strip().lower()
    if not text.endswith("px"):
        return 0.0
    try:
        return float(text[: -len("px")].strip() or 0)
    except ValueError:
        return 0.0


def _rgb_chroma(color: str) -> int:
    """`rgb(r, g, b)`/`rgba(...)` → max-min；解析失败返回 0（不误判）。"""
    numbers = re.findall(r"\d+", color or "")
    if len(numbers) < 3:
        return 0
    parts = [int(token) for token in numbers[:3]]
    return max(parts) - min(parts)


def _rgb_luma(color: str) -> float:
    """相对亮度（0=黑，1=白）；解析失败返回 1.0（按「等同纸色」处理，宁可报错不放过）。"""
    numbers = re.findall(r"\d+", color or "")
    if len(numbers) < 3:
        return 1.0
    red, green, blue = (int(token) for token in numbers[:3])
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0


# 纸色 `--paper` = #fbfaf6 → 相对亮度 ≈ 0.98。刻度线必须明显比纸色深，
# 否则「1px 视觉可见」只是名义上的。
_TICK_LUMA_LIMIT = 0.85


def _tick_failures(report: list[dict], expected_meters: int = 5) -> list[str]:
    """把「刻度线存在且中性、填充不越过刻度线」写成可复用的判定，正向与反向对照共用。"""
    failures: list[str] = []
    if len(report) < expected_meters:
        failures.append(f"进度条数量 {len(report)} < {expected_meters}，场景不完整")
    for index, item in enumerate(report):
        if not item["hasFill"]:
            failures.append(f"第 {index} 条进度条缺少填充元素")
            continue
        if not item["hasTick"]:
            failures.append(f"第 {index} 条进度条缺少 100% 刻度线")
            continue
        if not item["tickVisible"]:
            failures.append(f"第 {index} 条进度条刻度线不可见（display/visibility/尺寸为零）")
        if _px(item["tickRadius"]) > 0:
            failures.append(f"第 {index} 条刻度线不是方角：border-radius={item['tickRadius']}")
        if item["tickShadow"] not in ("none", ""):
            failures.append(f"第 {index} 条刻度线带投影：{item['tickShadow']}")
        if _rgb_chroma(item["tickBackground"]) > _NEUTRAL_CHROMA_LIMIT:
            failures.append(
                f"第 {index} 条刻度线不是中性墨色：{item['tickBackground']}"
                f"（色度差 {_rgb_chroma(item['tickBackground'])} > {_NEUTRAL_CHROMA_LIMIT}）"
            )
        if _rgb_luma(item["tickBackground"]) > _TICK_LUMA_LIMIT:
            failures.append(
                f"第 {index} 条刻度线太浅，1px 竖线在纸色上看不见：{item['tickBackground']}"
                f"（相对亮度 {_rgb_luma(item['tickBackground']):.3f} > {_TICK_LUMA_LIMIT}）"
            )
        if item["fillRight"] is None or item["tickRight"] is None:
            failures.append(f"第 {index} 条进度条几何不可测")
        elif item["fillRight"] > item["tickRight"] + 0.5:
            failures.append(
                f"第 {index} 条填充右缘 {item['fillRight']:.1f}px 越过刻度线 "
                f"{item['tickRight']:.1f}px"
            )
    return failures


def _meter_setup_over(page) -> None:
    """构造「已超出」场景：50 g 折算 1065 kJ > 自设目标 1000 kJ。"""
    p = page["page"]
    _load_sample_product(p)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(250)
    p.get_by_role("button", name="记录", exact=True).click()
    p.wait_for_timeout(500)
    p.locator('input[type="number"]').first.fill("1000")
    p.wait_for_timeout(400)  # `.hairline-meter__fill` 240ms 过渡


def test_diet_meter_tick_marks_full_scale_without_semantic_color(page):
    """100% 刻度线是中性标尺：未超出与已超出两种状态都可见，且填充不越线。

    修复前的缺陷：「刚好达标」与「超出 113%」都画成满格填充条，视觉完全一样，
    唯一区分信号是下方小字。加上 100% 刻度线后，超出时填充恰好顶到刻度线，
    读者不看小字也能看出「已到达自设上限」。
    """
    p = page["page"]
    _load_sample_product(p)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(250)
    p.get_by_role("button", name="记录", exact=True).click()
    p.wait_for_timeout(500)

    # 1) 未超出（能量目标 8400 > 已计入 1065）：刻度线仍在，它是标尺不是报警灯
    below = _meter_tick_report(p)
    assert _tick_failures(below) == [], f"未超出场景刻度线判定失败：{_tick_failures(below)}"
    assert any(item["fillRatio"] < 0.95 for item in below), (
        "未超出场景没有明显未满格的进度条，刻度线存在性未被真正验证"
    )

    # 2) 已超出（能量目标 1000 < 已计入 1065）：填充顶到刻度线但不越过
    p.locator('input[type="number"]').first.fill("1000")
    p.wait_for_timeout(400)
    over = _meter_tick_report(p)
    assert _tick_failures(over) == [], f"已超出场景刻度线判定失败：{_tick_failures(over)}"
    assert any(item["fillRatio"] >= 0.99 for item in over), (
        "已超出场景没有满格进度条，场景未被构造出来"
    )
    assert "已超出目标 65 kJ" in p.inner_text("body"), "超出场景的中性算术口径必须保持"

    # 判定只允许中性墨色：页面 HTML 也不得出现红/黄/绿语义配色工具类
    html = p.content()
    for token in ("text-red-", "bg-red-", "text-amber-", "bg-amber-", "text-yellow-",
                  "bg-yellow-", "text-green-", "bg-green-", "text-danger"):
        assert token not in html, f"刻度线场景出现红/黄/绿语义配色：{token}"


def test_meter_tick_gate_detects_hidden_tick(page):
    """反向对照：把刻度线 `display: none` 后，上面的正向判定必须失败。"""
    _meter_setup_over(page)
    p = page["page"]
    assert _tick_failures(_meter_tick_report(p)) == [], "反向对照前置条件不成立：基线已失败"

    p.evaluate(
        """() => {
          for (const tick of document.querySelectorAll('.hairline-meter__tick')) {
            tick.style.display = 'none';
          }
        }"""
    )
    p.wait_for_timeout(200)

    failures = _tick_failures(_meter_tick_report(p))
    assert failures, "反向对照失效：隐藏刻度线后判定仍然通过"
    assert any("刻度线不可见" in item or "缺少 100% 刻度线" in item for item in failures), (
        f"反向对照命中的不是刻度线可见性断言：{failures}"
    )


def test_meter_tick_gate_detects_semantic_color_injection(page):
    """反向对照：把刻度线染成红/黄/绿或调淡到纸色，中性色判定必须抓到。"""
    _meter_setup_over(page)
    p = page["page"]
    assert _tick_failures(_meter_tick_report(p)) == [], "反向对照前置条件不成立：基线已失败"

    # 1) 红：色度差 160（200−40），远超 32 的中性上限，必须被中性色断言命中
    p.evaluate(
        """() => {
          for (const tick of document.querySelectorAll('.hairline-meter__tick')) {
            tick.style.background = 'rgb(200, 40, 46)';
          }
        }"""
    )
    p.wait_for_timeout(150)
    color_failures = _tick_failures(_meter_tick_report(p))
    assert any("不是中性墨色" in item for item in color_failures), (
        f"反向对照失效：红色刻度线未被中性色断言命中：{color_failures}"
    )

    # 2) 纸色：色度差合格但亮度不合格，1px 竖线实际看不见
    p.evaluate(
        """() => {
          for (const tick of document.querySelectorAll('.hairline-meter__tick')) {
            tick.style.background = 'rgb(251, 250, 246)';
          }
        }"""
    )
    p.wait_for_timeout(150)
    luma_failures = _tick_failures(_meter_tick_report(p))
    assert any("太浅" in item for item in luma_failures), (
        f"反向对照失效：纸色刻度线未被亮度断言命中：{luma_failures}"
    )


def test_meter_tick_gate_detects_fill_past_tick(page):
    """反向对照：把填充宽度注入 160%（模拟未截断），几何判定必须抓到越线。

    注入后必须等 240ms 过渡走完（`wait_for_timeout(400)`）再量，否则量到起始值。
    """
    _meter_setup_over(page)
    p = page["page"]
    assert _tick_failures(_meter_tick_report(p)) == [], "反向对照前置条件不成立：基线已失败"

    p.evaluate(
        """() => {
          for (const fill of document.querySelectorAll('.hairline-meter__fill')) {
            fill.style.width = '160%';
          }
        }"""
    )
    p.wait_for_timeout(400)

    failures = _tick_failures(_meter_tick_report(p))
    assert failures, "反向对照失效：注入 160% 后几何判定仍然通过"
    assert any("越过刻度线" in item for item in failures), (
        f"反向对照命中的不是几何越线断言：{failures}"
    )


# ------------------------------- 百分比文字层：只补算术比例，不新增图形符号

# 口径：`ratio = 已计入 / 自设目标 × 100`，四舍五入到 0.1（`Math.round(ratio * 10) / 10`），
# 整数省略 `.0`。界面文案固定为「占自设目标 <n>%」，与「已超出目标 X」中性句并列。
_PERCENT_TEXT = re.compile(r"占自设目标\s*(\d+(?:\.\d+)?)\s*%")
_ACCUMULATED_TEXT = re.compile(r"已计入\s*([\d.]+)\s*/\s*每日目标\s*([\d.]+)")

# 进度条五行内不得出现的评价性词汇（用户清单全量）。
_PROGRESS_BANNED = (
    "超标",
    "警告",
    "过量",
    "注意",
    "建议",
    "应当",
    "健康",
    "风险",
    "危险",
    "正常",
    "异常",
    "良好",
    "不足",
)
# 全页范围断言的子集：现有免责句里合法包含「建议」「风险」
# （「不构成任何营养建议」「不含风险评分、真伪判断…」），因此这两词只在进度条五行内断言。
_PAGE_BANNED = tuple(term for term in _PROGRESS_BANNED if term not in ("建议", "风险"))

_FABRICATED_TOKENS = ("Infinity", "-Infinity", "NaN", "undefined")

_PERCENT_PROBE = """() => {
  const px = (value) => {
    const parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const clean = (text) => (text || '').replace(/\\s+/g, ' ').trim();
  const rows = [...document.querySelectorAll('[data-diet-meter-row]')];
  return rows.map((row) => {
    const caption = row.querySelector('[data-diet-meter-caption]');
    const percentSpan = caption ? caption.querySelector('.mono') : null;
    const percentStyle = percentSpan ? getComputedStyle(percentSpan) : null;
    const captionStyle = caption ? getComputedStyle(caption) : null;
    const rowRect = row.getBoundingClientRect();
    const captionRect = caption ? caption.getBoundingClientRect() : null;
    return {
      key: row.getAttribute('data-diet-meter-row') || '',
      text: clean(row.innerText),
      captionText: caption ? clean(caption.innerText) : null,
      hasPercentSpan: Boolean(percentSpan),
      percentColor: percentStyle ? percentStyle.color : '',
      percentBackground: percentStyle ? percentStyle.backgroundColor : '',
      percentRadius: percentStyle ? px(percentStyle.borderTopLeftRadius) : null,
      percentShadow: percentStyle ? percentStyle.boxShadow : '',
      captionColor: captionStyle ? captionStyle.color : '',
      captionBackground: captionStyle ? captionStyle.backgroundColor : '',
      captionRadius: captionStyle ? px(captionStyle.borderTopLeftRadius) : null,
      captionShadow: captionStyle ? captionStyle.boxShadow : '',
      rowRight: rowRect.right,
      captionRight: captionRect ? captionRect.right : null,
      captionScrollWidth: caption ? caption.scrollWidth : null,
      captionClientWidth: caption ? caption.clientWidth : null,
      docScrollWidth: document.documentElement.scrollWidth,
      docClientWidth: document.documentElement.clientWidth,
      viewportWidth: window.innerWidth,
    };
  });
}"""


def _percent_rows(page) -> list[dict]:
    return page.evaluate(_PERCENT_PROBE)


def _row_by_key(report: list[dict], key: str) -> dict:
    for item in report:
        if item["key"] == key:
            return item
    raise AssertionError(f"未找到 {key} 这一行进度条；实际行：{[item['key'] for item in report]}")


def _expected_percent(current: float, target: float) -> str:
    """期望值 = 落盘总量 / 目标 × 100，四舍五入到 0.1，整数省略 `.0`。

    四舍五入用 `ROUND_HALF_UP` 模拟 JS 的 `Math.round(ratio * 10) / 10`
    （两者对正数都是四舍五入到一位小数）。

    注意口径：百分比按**未舍入**的折算总量计算（`scaledNutrition` 落盘到小数点后 2 位），
    而上方「已计入」按营养素展示精度四舍五入（kJ / mg 取整，g 取一位小数）。
    因此蛋白质这类真值是 12.25 g 的项，界面显示 `12.3 g`，但百分比是
    `12.25 / 60 × 100 = 20.4166… → 20.4%`，而不是用 12.3 复算的 20.5%。
    这条差异在文档里写明（不把展示舍入再叠加一次）。
    """
    from decimal import ROUND_HALF_UP, Decimal  # noqa: PLC0415

    ratio = current / target * 100
    rounded = Decimal(repr(ratio)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    text = format(rounded, "f")
    return text[:-2] if text.endswith(".0") else text


def _display_step(key: str) -> float:
    """该营养素的展示精度：kJ / mg 取整（±0.5），g 取一位小数（±0.05）。"""
    return 0.5 if key in ("energyKj", "sodiumMg") else 0.05


def _plan_snapshot(page) -> dict:
    """从 IndexedDB 的真实落盘状态取「已计入总量」与「自设目标」。

    不读界面文本、不读源码常量：期望值必须来自用户实际写入的数据。
    界面上的「已计入」按展示精度舍入（kJ / mg 取整，g 取一位小数），
    所以判定时会允许一个展示步长的差，并用落盘真值算百分比。
    """
    plan = _read_idb(page)["diet_plan"]
    keys = ("energyKj", "proteinG", "fatG", "carbohydrateG", "sodiumMg")
    totals = {key: 0.0 for key in keys}
    targets = {key: 0.0 for key in keys}
    if not plan:
        return {"totals": totals, "targets": targets}
    for key in keys:
        targets[key] = float(plan[0].get("targets", {}).get(key) or 0)
    for entry in plan[0].get("logs", []):
        for key in keys:
            totals[key] += float(entry.get(key) or 0)
    return {"totals": totals, "targets": targets}


def _caption_percents(caption: str | None) -> list[str]:
    return _PERCENT_TEXT.findall(caption or "")


def _evaluative_violations(text: str, terms=_PROGRESS_BANNED) -> list[str]:
    return [term for term in terms if term in (text or "")]


def _fabricated_violations(text: str) -> list[str]:
    return [token for token in _FABRICATED_TOKENS if token in (text or "")]


def _percent_layer_failures(report: list[dict], page_text: str, plan: dict | None = None) -> list[str]:
    """百分比文字层的完整判定；正向断言「返回空列表」，反向对照断言能命中具体条目。

    `plan` 可选：`_plan_snapshot(page)` 的结果（IndexedDB 真实落盘的总量与目标）。
    给出时用它作为算术预期值，并额外断言「界面显示的已计入 / 每日目标」与落盘值只差
    一个展示舍入步长 —— 否则说明取错了行、或界面在伪造数字。
    """
    failures: list[str] = []
    if len(report) != 5:
        failures.append(f"进度条行数 {len(report)} ≠ 5，场景不完整")

    captions: list[str] = []
    for index, item in enumerate(report):
        key = item["key"] or f"#{index}"
        caption = item["captionText"]
        if caption is None:
            failures.append(f"{key} 缺少文字说明层")
            continue
        captions.append(caption)

        accumulated = _ACCUMULATED_TEXT.search(item["text"])
        if not accumulated:
            failures.append(f"{key} 的「已计入 / 每日目标」数值无法从 DOM 解析：{item['text']}")
            continue
        shown_current = float(accumulated.group(1))
        target = float(accumulated.group(2))
        percents = _caption_percents(caption)

        true_current = shown_current
        if plan is not None and key in plan["totals"]:
            true_current = plan["totals"][key]
            stored_target = plan["targets"][key]
            step = _display_step(key)
            if abs(shown_current - true_current) > step + 1e-9:
                failures.append(
                    f"{key} 界面已计入 {shown_current:g} 与落盘值 {true_current:g} 相差超过展示精度"
                )
            if abs(target - stored_target) > 1e-9:
                failures.append(f"{key} 界面每日目标 {target:g} 与落盘值 {stored_target:g} 不一致")

        if target > 0:
            if len(percents) != 1:
                failures.append(f"{key} 有目标（{target:g}）却给出 {len(percents)} 个百分比：{caption}")
                continue
            expected = _expected_percent(true_current, target)
            if percents[0] != expected:
                failures.append(
                    f"{key} 百分比 {percents[0]}% ≠ 算术预期 {expected}%"
                    f"（已计入 {true_current:g} / 目标 {target:g}）"
                )
            if "." in percents[0] and len(percents[0].split(".", 1)[1]) > 1:
                failures.append(f"{key} 百分比小数位超过 1 位：{percents[0]}%")
            if not item["hasPercentSpan"]:
                failures.append(f"{key} 百分比不是独立文本节点，样式判定无从成立")
        else:
            if percents:
                failures.append(f"{key} 目标为 0（未设置）却出现百分比 {percents[0]}%")

        if "!" in caption or "！" in caption:
            failures.append(f"{key} 文字层出现感叹号：{caption}")

        if _rgb_chroma(item["percentColor"] or item["captionColor"]) > _NEUTRAL_CHROMA_LIMIT:
            failures.append(f"{key} 百分比文字不是中性墨色：{item['percentColor']}")
        if _rgb_chroma(item["percentBackground"]) > _NEUTRAL_CHROMA_LIMIT:
            failures.append(f"{key} 百分比文字带语义底色：{item['percentBackground']}")
        if (item["percentRadius"] or 0) > 0 or (item["captionRadius"] or 0) > 0:
            failures.append(
                f"{key} 文字层不是方角：percent={item['percentRadius']} caption={item['captionRadius']}"
            )
        if item["percentShadow"] not in ("none", "") or item["captionShadow"] not in ("none", ""):
            failures.append(f"{key} 文字层带投影：{item['percentShadow']}")
        if item["captionScrollWidth"] is not None and item["captionScrollWidth"] > item["captionClientWidth"] + 1:
            failures.append(
                f"{key} 文字层自身横向溢出：scrollWidth={item['captionScrollWidth']} > "
                f"clientWidth={item['captionClientWidth']}"
            )
        if item["captionRight"] is not None and item["captionRight"] > item["rowRight"] + 0.5:
            failures.append(f"{key} 文字层右缘越过行容器：{item['captionRight']:.1f}px")

    joined = " ".join(captions)
    for term in _evaluative_violations(joined):
        failures.append(f"进度条文字层出现评价性词汇：「{term}」")
    for token in _fabricated_violations(joined):
        failures.append(f"进度条文字层出现伪造/非法值：{token}")
    for term in _evaluative_violations(page_text, _PAGE_BANNED):
        failures.append(f"页面正文出现评价性词汇：「{term}」")
    for token in _fabricated_violations(page_text):
        failures.append(f"页面正文出现伪造/非法值：{token}")
    return failures


def _log_sample_serving(page) -> None:
    """记录 50 g 演示样品：能量已计入 1065 kJ，默认目标 8400 kJ。"""
    p = page["page"]
    _load_sample_product(p)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(250)
    p.get_by_role("button", name="记录", exact=True).click()
    p.wait_for_timeout(500)


def _set_energy_target(page, value: str) -> None:
    """能量目标是「每日目标」网格里的第一个数字输入（在记录区之前）。"""
    p = page["page"]
    p.locator('input[type="number"]').first.fill(value)
    p.wait_for_timeout(400)


def test_diet_percent_caption_is_arithmetic_below_target(page):
    """正向：未超出时也给出百分比（不是「越线才出现的符号」）。

    期望值来源：从 DOM 文本解析出「已计入 1065 / 每日目标 8400」，
    再按 `1065 / 8400 * 100 = 12.678571…` 四舍五入到 0.1 → `12.7`。
    另一条边界（current == 0 且有目标）在同一测试里覆盖：新页面未记录时应显示 `0%`。
    """
    p = page["page"]

    # 边界 2：current == 0 且有目标 → 0% 合法，且不是「无百分比」
    fresh = _percent_rows(p)
    energy = _row_by_key(fresh, "energyKj")
    assert energy["captionText"] == "未超出当前自设目标 · 占自设目标 0%", (
        f"未记录时应显示 0%，实际：{energy['captionText']!r}"
    )

    _log_sample_serving(page)
    p.wait_for_timeout(200)
    report = _percent_rows(p)
    failures = _percent_layer_failures(report, p.inner_text("body"), _plan_snapshot(p))
    assert failures == [], f"百分比文字层判定失败：{failures}"

    energy = _row_by_key(report, "energyKj")
    accumulated = _ACCUMULATED_TEXT.search(energy["text"])
    assert accumulated, f"无法从行文本解析数值：{energy['text']}"
    current, target = float(accumulated.group(1)), float(accumulated.group(2))
    assert (current, target) == (1065.0, 8400.0), (
        f"场景前提不成立：已计入 {current:g} / 目标 {target:g}（应为 1065 / 8400）"
    )
    assert _caption_percents(energy["captionText"]) == ["12.7"], (
        f"1065/8400×100 = 12.6785… 应显示 12.7%，实际：{energy['captionText']!r}"
    )
    # 未超出时中性句必须保留
    assert "未超出当前自设目标" in energy["captionText"]


def test_diet_percent_caption_coexists_with_neutral_overflow_sentence(page):
    """正向：超出态同时有算术百分比与中性超出句，两者不得互相替代。

    期望值来源：把能量目标改成 1000（已计入 1065）→ `1065 / 1000 × 100 = 106.5`，
    四舍五入到 0.1 仍是 `106.5`（一位小数，不省略）。
    """
    _log_sample_serving(page)
    p = page["page"]
    _set_energy_target(page, "1000")

    report = _percent_rows(p)
    failures = _percent_layer_failures(report, p.inner_text("body"), _plan_snapshot(p))
    assert failures == [], f"百分比文字层判定失败：{failures}"

    energy = _row_by_key(report, "energyKj")
    assert _caption_percents(energy["captionText"]) == ["106.5"], (
        f"1065/1000×100 应显示 106.5%，实际：{energy['captionText']!r}"
    )
    assert "已超出目标 65 kJ" in energy["captionText"], (
        f"中性超出句必须与百分比同时存在，实际：{energy['captionText']!r}"
    )
    assert "已超出目标 65 kJ" in p.inner_text("body"), "中性超出句必须仍然渲染在正文里"

    # 「刚好达标」（目标恰好等于已计入 1065）与「超出」（目标 1000）在轨道上都是顶线，
    # 现在文字层必须给出不同的数字：100% vs 106.5%。
    _set_energy_target(page, "1065")
    report = _percent_rows(p)
    failures = _percent_layer_failures(report, p.inner_text("body"), _plan_snapshot(p))
    assert failures == [], f"刚好达标场景判定失败：{failures}"
    energy = _row_by_key(report, "energyKj")
    assert energy["captionText"] == "未超出当前自设目标 · 占自设目标 100%", (
        f"目标 1065 / 已计入 1065 应显示 100%，实际：{energy['captionText']!r}"
    )


def test_diet_percent_caption_absent_when_target_is_zero(page):
    """正向：`target == 0` 时不得除零，也不得显示伪造的 0% / Infinity / NaN。"""
    _log_sample_serving(page)
    p = page["page"]
    _set_energy_target(page, "0")

    report = _percent_rows(p)
    energy = _row_by_key(report, "energyKj")
    assert energy["captionText"] == "未设置每日目标；仅记录已计入数值。", (
        f"目标为 0 时应保留原文案且不出现百分比，实际：{energy['captionText']!r}"
    )
    assert _caption_percents(energy["captionText"]) == [], "目标为 0 时不得出现百分比"
    assert "Infinity" not in energy["text"] and "NaN" not in energy["text"]
    assert _fabricated_violations(energy["text"]) == [], (
        f"目标为 0 时出现伪造值：{_fabricated_violations(energy['text'])}"
    )

    # 其它四条仍应有目标 → 仍有百分比（证明不是「整层都关了」）
    failures = _percent_layer_failures(report, p.inner_text("body"), _plan_snapshot(p))
    assert failures == [], f"目标为 0 场景触发其它判定失败：{failures}"
    assert all(
        _caption_percents(item["captionText"]) for item in report if item["key"] != "energyKj"
    ), "未改动的四条进度条应仍显示百分比"


def test_diet_percent_caption_is_neutral_typography(page):
    """正向：百分比不带红/黄/绿、不带圆角、投影、胶囊，也不带评价性词汇。"""
    _log_sample_serving(page)
    p = page["page"]
    _set_energy_target(page, "1000")

    report = _percent_rows(p)
    for item in report:
        assert item["hasPercentSpan"], f"{item['key']} 缺少独立百分比文本节点"
        assert _rgb_chroma(item["percentColor"]) <= _NEUTRAL_CHROMA_LIMIT, (
            f"{item['key']} 百分比不是中性墨色：{item['percentColor']}（色度差 "
            f"{_rgb_chroma(item['percentColor'])} > {_NEUTRAL_CHROMA_LIMIT}）"
        )
        assert (item["percentRadius"] or 0) == 0, f"{item['key']} 百分比带圆角：{item['percentRadius']}"
        assert item["percentShadow"] in ("none", ""), f"{item['key']} 百分比带投影：{item['percentShadow']}"

    body = p.inner_text("body")
    assert _evaluative_violations(" ".join(item["captionText"] or "" for item in report)) == []
    assert _evaluative_violations(body, _PAGE_BANNED) == [], (
        f"页面正文出现评价性词汇：{_evaluative_violations(body, _PAGE_BANNED)}"
    )

    html = p.content()
    for token in ("text-red-", "bg-red-", "text-amber-", "bg-amber-", "text-yellow-",
                  "bg-yellow-", "text-green-", "bg-green-", "text-danger"):
        assert token not in html, f"百分比场景出现红/黄/绿语义配色：{token}"


def test_diet_percent_caption_stays_inside_container_at_extreme_ratio(page):
    """正向：目标极小（1）时百分比不得溢出，也不得产生超长小数或横向滚动。

    边界 3 + 边界 4：已计入 1065、目标 1 → `1065 / 1 × 100 = 106500`，显示 `106500%`。
    再把目标改成 0.01 构造 `10650000%`，在 390px 视口下断言零横向滚动。
    """
    _log_sample_serving(page)
    p = page["page"]
    p.set_viewport_size({"width": 390, "height": 900})
    p.wait_for_timeout(300)

    _set_energy_target(page, "1")
    report = _percent_rows(p)
    failures = _percent_layer_failures(report, p.inner_text("body"), _plan_snapshot(p))
    assert failures == [], f"极端比例场景判定失败：{failures}"
    energy = _row_by_key(report, "energyKj")
    assert _caption_percents(energy["captionText"]) == ["106500"], (
        f"1065/1×100 应显示 106500%，实际：{energy['captionText']!r}"
    )

    _set_energy_target(page, "0.01")
    report = _percent_rows(p)
    energy = _row_by_key(report, "energyKj")
    assert _caption_percents(energy["captionText"]) == ["10650000"], (
        f"1065/0.01×100 应显示 10650000%，实际：{energy['captionText']!r}"
    )
    for item in report:
        if item["key"] != "energyKj":
            continue
        assert item["docScrollWidth"] <= item["docClientWidth"] + 1, (
            f"390px 下出现横向滚动：scrollWidth={item['docScrollWidth']} > "
            f"clientWidth={item['docClientWidth']}"
        )
        assert item["captionRight"] <= item["rowRight"] + 0.5, (
            f"百分比文字右缘越过容器：{item['captionRight']:.1f}px > {item['rowRight']:.1f}px"
        )
        assert item["captionScrollWidth"] <= item["captionClientWidth"] + 1, (
            f"百分比文字自身溢出：scrollWidth={item['captionScrollWidth']} > "
            f"clientWidth={item['captionClientWidth']}"
        )
    p.set_viewport_size({"width": 1280, "height": 900})
    p.wait_for_timeout(200)


def test_diet_percent_gate_detects_evaluative_word_injection(page):
    """反向对照：把某行文字层换成「超标 107%」，评价性词汇断言必须命中。"""
    _log_sample_serving(page)
    p = page["page"]
    _set_energy_target(page, "1000")
    assert _percent_layer_failures(_percent_rows(p), p.inner_text("body"), _plan_snapshot(p)) == [], (
        "反向对照前置条件不成立：基线已失败"
    )

    p.evaluate(
        """() => {
          const caption = document.querySelector('[data-diet-meter-caption]');
          if (caption) caption.textContent = '超标 107%';
        }"""
    )
    p.wait_for_timeout(200)

    failures = _percent_layer_failures(_percent_rows(p), p.inner_text("body"))
    assert any("评价性词汇" in item and "超标" in item for item in failures), (
        f"反向对照失效：注入「超标」后评价性词汇断言未命中：{failures}"
    )


def test_diet_percent_gate_detects_fabricated_value_injection(page):
    """反向对照：注入 `Infinity` / `NaN` 文字，伪造值断言必须命中。"""
    _log_sample_serving(page)
    p = page["page"]
    assert _percent_layer_failures(_percent_rows(p), p.inner_text("body"), _plan_snapshot(p)) == [], (
        "反向对照前置条件不成立：基线已失败"
    )

    p.evaluate(
        """() => {
          const caption = document.querySelector('[data-diet-meter-caption]');
          if (caption) caption.textContent = '占自设目标 Infinity % NaN undefined';
        }"""
    )
    p.wait_for_timeout(200)

    failures = _percent_layer_failures(_percent_rows(p), p.inner_text("body"))
    assert any("伪造/非法值" in item and "Infinity" in item for item in failures), (
        f"反向对照失效：注入 Infinity 后伪造值断言未命中：{failures}"
    )
    assert any("NaN" in item for item in failures), (
        f"反向对照失效：注入 NaN 后伪造值断言未命中：{failures}"
    )


def test_diet_percent_gate_detects_percent_injected_at_zero_target(page):
    """反向对照：目标为 0 后手动插入百分比，除零断言必须命中。"""
    _log_sample_serving(page)
    p = page["page"]
    _set_energy_target(page, "0")
    assert _percent_layer_failures(_percent_rows(p), p.inner_text("body"), _plan_snapshot(p)) == [], (
        "反向对照前置条件不成立：基线已失败"
    )

    p.evaluate(
        """() => {
          const caption = document.querySelector('[data-diet-meter-caption]');
          if (caption) {
            const span = document.createElement('span');
            span.className = 'mono';
            span.textContent = ' · 占自设目标 0%';
            caption.appendChild(span);
          }
        }"""
    )
    p.wait_for_timeout(200)

    failures = _percent_layer_failures(_percent_rows(p), p.inner_text("body"))
    assert any("目标为 0" in item and "百分比" in item for item in failures), (
        f"反向对照失效：目标为 0 时手插百分比未被命中：{failures}"
    )


def test_diet_scaling_refuses_unknown_basis(page):
    """营养表按每 100 毫升标注时不得假设 1 毫升 = 1 克：拒绝折算并明确说明。"""
    p = page["page"]
    p.get_by_label("折算依据条码").fill("6901112223339")
    p.get_by_role("button", name="载入档案营养表").click()
    p.wait_for_timeout(700)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(300)

    body = p.inner_text("body")
    assert "每 100 毫升" in body, "必须如实说明计量基准是每 100 毫升"
    assert "本次记录未计入数值" in body, "计量基准不同时必须拒绝折算"
    assert p.get_by_role("button", name="记录", exact=True).is_disabled(), "无法折算时不得允许记录"
    stored = _read_idb(p)
    logs = stored["diet_plan"][0]["logs"] if stored["diet_plan"] else []
    assert logs == [], "拒绝折算时不得写入任何 0 值占位记录"


# ------------------------------------------------- 任务 D：备份导出 / 导入


def test_backup_export_is_local_json(page):
    """导出必须生成结构完整的本地 JSON 文件，且不产生网络请求。"""
    p = page["page"]
    _load_sample_product(p)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(250)
    p.get_by_role("button", name="记录", exact=True).click()
    p.wait_for_timeout(500)
    before = len(page["requests"])

    payload = _export_backup(p, page["tmp"] / "backup.json")
    assert payload["app"] == "eatwhat-local-backup", "必须带可校验的 app 标识"
    assert payload["schema_version"] == "1", "必须带版本号"
    assert isinstance(payload["diet_rules"], list), "diet_rules 必须是数组"
    assert isinstance(payload["scan_history"], list), "scan_history 必须是数组"
    assert len(payload["diet_plan"]["logs"]) == 1, "备份必须包含刚记录的条目"
    assert payload["diet_plan"]["logs"][0]["grams"] == 50, "备份必须保留克重"

    after = page["requests"][before:]
    assert after == [], f"导出触发了网络请求：{after[:5]}"


def test_backup_import_restores_after_clearing(page):
    """覆盖模式：清空后导入备份，条目按原值回来。"""
    p = page["page"]
    _load_sample_product(p)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(250)
    p.get_by_role("button", name="记录", exact=True).click()
    p.wait_for_timeout(500)
    file_path = page["tmp"] / "backup.json"
    _export_backup(p, file_path)

    p.get_by_role("button", name="删除").first.click()  # 清掉唯一一条记录
    p.wait_for_timeout(400)
    assert _read_idb(p)["diet_plan"][0]["logs"] == [], "删除后记录应为空"

    p.get_by_role("radio", name="覆盖").check()
    p.get_by_label("本地备份文件").set_input_files(str(file_path))
    p.wait_for_timeout(900)

    body = p.inner_text("body")
    assert "已按「覆盖」写入" in body, "导入成功必须回显写入方式与条数"
    stored = _read_idb(p)["diet_plan"][0]["logs"]
    assert len(stored) == 1 and stored[0]["grams"] == 50, "导入必须恢复记录的克重"


def test_backup_import_merge_keeps_local_entries(page):
    """合并模式：本地已有条目不得被备份抹掉，同 id 以备份为准。"""
    p = page["page"]
    p.get_by_placeholder("忌口词条，例如：乳糖 / 花生").fill("乳糖")
    p.get_by_role("button", name="添加本地词条").click()
    p.wait_for_timeout(400)

    backup = {
        "schema_version": "1",
        "app": "eatwhat-local-backup",
        "exported_at": "2026-01-01T00:00:00Z",
        "diet_rules": [
            {"id": "rule_backup_1", "term": "花生", "aliases": ["花生仁"], "createdAt": 1700000000000}
        ],
        "diet_plan": {
            "id": "default",
            "targets": {
                "energyKj": 8400,
                "proteinG": 60,
                "fatG": 65,
                "carbohydrateG": 300,
                "sodiumMg": 2000,
            },
            "logs": [],
            "updatedAt": 1700000000000,
        },
        "scan_history": [],
    }
    file_path = page["tmp"] / "merge_backup.json"
    file_path.write_text(json.dumps(backup, ensure_ascii=False), encoding="utf-8")

    p.get_by_role("radio", name="合并").check()
    p.get_by_label("本地备份文件").set_input_files(str(file_path))
    p.wait_for_timeout(900)

    terms = {rule["term"] for rule in _read_idb(p)["diet_rules"]}
    assert {"乳糖", "花生"} <= terms, f"合并模式丢失本地或备份词条：{terms}"
    assert "合并" in p.inner_text("body"), "界面必须写清导入是覆盖还是合并"


def test_backup_import_rejects_invalid_without_data_loss(page):
    """非法文件：必须明确报错，且 IndexedDB 内容逐项不变。"""
    p = page["page"]
    _load_sample_product(p)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(250)
    p.get_by_role("button", name="记录", exact=True).click()
    p.wait_for_timeout(500)
    p.get_by_placeholder("忌口词条，例如：乳糖 / 花生").fill("乳糖")
    p.get_by_role("button", name="添加本地词条").click()
    p.wait_for_timeout(400)

    before = _read_idb(p)
    assert len(before["diet_rules"]) == 1 and len(before["diet_plan"][0]["logs"]) == 1

    # 1) 结构非法（diet_rules 不是数组）
    bad_structure = page["tmp"] / "bad_structure.json"
    bad_structure.write_text(
        json.dumps(
            {
                "app": "eatwhat-local-backup",
                "schema_version": "1",
                "diet_rules": "not-an-array",
                "diet_plan": before["diet_plan"][0],
                "scan_history": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    p.get_by_label("本地备份文件").set_input_files(str(bad_structure))
    p.wait_for_timeout(800)
    body = p.inner_text("body")
    assert "备份结构校验未通过" in body, "结构非法必须明确报错"
    assert "diet_rules 必须是数组" in body, "错误必须精确指出字段与原因"
    assert _read_idb(p) == before, "结构非法时不得改动任何一条已有数据"

    # 2) 版本不匹配
    bad_version = page["tmp"] / "bad_version.json"
    bad_version.write_text(
        json.dumps(
            {
                "app": "eatwhat-local-backup",
                "schema_version": "99",
                "diet_rules": [],
                "diet_plan": before["diet_plan"][0],
                "scan_history": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    p.get_by_label("本地备份文件").set_input_files(str(bad_version))
    p.wait_for_timeout(800)
    assert "schema_version" in p.inner_text("body"), "版本不匹配必须明确报错"
    assert _read_idb(p) == before, "版本不匹配时不得改动任何一条已有数据"

    # 3) 非 JSON
    broken = page["tmp"] / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    p.get_by_label("本地备份文件").set_input_files(str(broken))
    p.wait_for_timeout(700)
    assert "文件不是合法 JSON" in p.inner_text("body"), "非法 JSON 必须明确报错"
    assert _read_idb(p) == before, "非法 JSON 时不得改动任何一条已有数据"


def test_backup_flow_makes_no_network_request(page):
    """导出与导入全程零外发：无 POST/PUT，无跨站主机。"""
    p = page["page"]
    _load_sample_product(p)
    p.get_by_label("克重").fill("50")
    p.wait_for_timeout(250)
    p.get_by_role("button", name="记录", exact=True).click()
    p.wait_for_timeout(400)
    before = len(page["requests"])

    payload = _export_backup(p, page["tmp"] / "backup.json")
    assert payload["diet_plan"]["logs"], "导出前需要有记录"
    p.get_by_role("radio", name="覆盖").check()
    p.get_by_label("本地备份文件").set_input_files(str(page["tmp"] / "backup.json"))
    p.wait_for_timeout(900)

    new_requests = page["requests"][before:]
    assert new_requests == [], f"备份流程出现新的网络请求：{new_requests[:5]}"
    methods = {entry["method"] for entry in page["requests"]}
    assert methods <= {"GET", "HEAD"}, f"备份流程出现非只读请求：{methods}"
    assert p.evaluate("document.cookie.length") == 0, "备份流程不得产生 Cookie"


# --------------------------------------------- 源码级：备份只能用 FileReader


def _scan_upload_markers(root: Path) -> list[tuple[str, str]]:
    offenders: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.ts*")):
        text = path.read_text(encoding="utf-8")
        for marker in UPLOAD_MARKERS:
            if marker in text:
                offenders.append((str(path.relative_to(root)), marker))
    return offenders


def test_backup_modules_use_no_upload_primitive():
    """备份与折算模块只能本地读写：不得出现任何上传语义。"""
    targets = [
        ROOT / "apps" / "web" / "src" / "local_store" / "backup.ts",
        ROOT / "apps" / "web" / "src" / "local_store" / "db.ts",
        ROOT / "apps" / "web" / "src" / "engine" / "nutrition_scaling.ts",
        ROOT / "apps" / "web" / "src" / "pages" / "DietPage.tsx",
    ]
    offenders: list[tuple[str, str]] = []
    for path in targets:
        assert path.exists(), f"缺少被测文件：{path}"
        text = path.read_text(encoding="utf-8")
        for marker in UPLOAD_MARKERS:
            if marker in text:
                offenders.append((path.name, marker))
    assert not offenders, f"备份/折算模块出现上传能力：{offenders}"


def test_upload_marker_gate_detects_injected_violation(tmp_path: Path):
    """反向对照：向副本注入上传标记后，同一扫描函数必须命中。"""
    clean = _scan_upload_markers(ROOT / "apps" / "web" / "src")
    assert clean == [], f"基线源码已含上传标记：{clean}"

    copy = tmp_path / "src"
    shutil.copytree(ROOT / "apps" / "web" / "src", copy)
    injected = copy / "local_store" / "leaky_backup.ts"
    injected.write_text(
        "export function leak(json: string) { navigator.sendBeacon('/collect', json); }\n",
        encoding="utf-8",
    )
    found = _scan_upload_markers(copy)
    assert ("local_store/leaky_backup.ts", "navigator.sendBeacon") in found, (
        f"注入的上传标记未被命中，说明扫描是空转的：{found}"
    )


# ------------------------------- 反向对照：交通灯配色在构建产物里会被拦下

# 构造被禁词与配色片段：字面量拼接，避免本测试文件自身被词表扫描命中。
_RISK_WORD = "风" + "险"
_TRAFFIC_CLASS = "bg-red-500 " + _RISK_WORD


def test_traffic_light_color_gate_detects_injected_violation(tmp_path: Path):
    """反向对照：同一段语义门禁代码在注入配色后必须命中，在真实产物里必须干净。"""
    from packages.semantic_gate.rules import scan_text  # noqa: PLC0415

    clean = scan_text(_TRAFFIC_CLASS.replace(_TRAFFIC_CLASS, "已超出目标 65 kJ"), where="probe-clean")
    assert [f for f in clean if f.category == "RISK_COLOR_CODE"] == [], "中性文案不应命中配色门禁"

    dirty = scan_text(_TRAFFIC_CLASS, where="probe-dirty")
    codes = [f for f in dirty if f.category == "RISK_COLOR_CODE"]
    assert codes, "注入的交通灯配色未被命中，说明配色门禁是空转的"

    built = DIST / "index.html"
    if not built.exists():
        pytest.skip("apps/web/dist 未构建；请先执行 npm run build:web")
    real = [f for f in scan_text(built.read_text(encoding="utf-8"), where="apps/web/dist/index.html")
            if f.category == "RISK_COLOR_CODE"]
    assert real == [], f"真实单文件产物出现风险配色：{[f.term for f in real][:5]}"
