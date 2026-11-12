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
