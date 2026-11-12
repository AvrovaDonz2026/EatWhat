# EatWhat — 消费者商品公开信息展示器（即拿即用纯静态网页版）

只做 **检索 · 取得 · 保存 · 结构化 · 并列 · 对照 · 时间线 · 来源追溯** 的商品公开信息展示器。
不含任何风险评分、真伪判断、合法性定性、关系推断、购买建议或功效评价。

## 即拿即用（30 秒）

```bash
# 1) 打开单文件产物（已内联全部脚本、样式、OCR 模型）
open apps/web/dist/index.html          # 或直接双击该文件

# 2) 若需随静态 JSON 一起交付，请用任意静态服务器（推荐，避免 file:// 的 fetch 限制）
python3 -m http.server 8080 --directory apps/web/dist
```

`apps/web/dist/index.html` 是一个 **7.6MB 单文件**，双击即可离线运行：

- 所有 JS / CSS 内联；
- OCR 内核（内嵌 WASM 的 `tesseract-core-simd-lstm.wasm.js`）、worker 脚本、中文简体语言数据全部内联为 `blob:` / `data:`；
- 运行时 **零外发请求**（已由 Playwright 门禁在真实 Chromium 中验证）。

`apps/web/dist/data/v1/` 是配套的静态 JSON 分片。若通过 `http.server` 托管，界面会自动读取真实档案；
若单独分发 `index.html`（无 `data/` 目录），会自动退回**内置离线样例**并在界面标注 `DEMO_FALLBACK`。

## 目录结构

```
EatWhat/
├── apps/web/                    # 纯静态 SPA（React 19 + Vite 6 + Tailwind v4）
│   ├── src/
│   │   ├── ocr/barcode.ts          # 端侧条码识别（zxing-wasm，WASM 内联）
│   │   ├── ocr/local_ocr.ts        # 端侧 OCR（tesseract.js，图片不出浏览器）
│   │   ├── local_store/db.ts       # IndexedDB（idb）：词条 / 计划 / 扫码历史
│   │   ├── local_store/allergen_presets.ts   # GB 7718-2025 致敏原预设词条
│   │   ├── engine/local_diet_matcher.ts      # 机械子串/别名对照引擎
│   │   ├── services/static_dossier_client.ts # 只读 /v1/ 静态 JSON
│   │   ├── services/demo_fallback.ts         # 内置离线兜底样例
│   │   └── pages/                  # 三个页签
│   └── dist/index.html             # 单文件交付产物
├── pipeline/                     # 离线增量编译管线（Python 3.9+）
│   ├── config/source_policy_registry.json   # 数据源策略注册表（唯一门禁）
│   ├── config/policy.py                       # 注册表加载与红线校验
│   ├── builder/ledger.py                     # SQLite 只追加账本 + SHA-256
│   ├── builder/dossier.py                    # 16 区块装配
│   ├── builder/emit_static.py                # 静态分片编译
│   ├── adapters/v1/                          # 7 个数据源适配器
│   └── fixtures/                             # 离线夹具（v1 / v1_rev2）
├── packages/semantic_gate/       # 附录 B 语义门禁（禁用词 + 风险配色）
├── tests/acceptance_gates/       # 10 项自动化验收门禁
├── legacy/                       # 已取代的设计原型（不参与构建）
├── scripts/sync-data.mjs         # 把 dist_data 同步进 apps/web/dist/data
└── dist_data/v1/                 # 编译产物（manifest / gtin / products / ingredients / demo）
```

## 构建与验收

```bash
# 依赖
npm install
python3 -m venv .venv && .venv/bin/pip install pytest playwright
.venv/bin/python -m playwright install chromium

# 1) 编译静态数据（账本只追加；--reset 只清空 dist_data）
python3 -m pipeline.build --reset

# 2) 构建单文件网页
npm run build -w apps/web

# 3) 语义门禁（附录 B）
python3 -m packages.semantic_gate.cli_scan --all

# 4) 全部 10 项验收门禁
.venv/bin/python -m pytest tests/acceptance_gates -q
```

> 说明：`--reset-ledger` 仅用于从零重建演示基线。正常运行请勿使用——账本的设计前提是**永不丢弃历史**。

### 产物契约

| 路径 | 内容 |
| --- | --- |
| `v1/manifest.json` | 构件版本、源清单、适配器版本、覆盖汇总、查询索引 |
| `v1/gtin/{code}.json` | 该条码的候选并列（`EXACT_IDENTIFIER_MATCH` / `MANUAL_CONFIRM`）+ 覆盖清单 |
| `v1/products/{entity_id}.json` | 16 区块档案、原始观测并列、历史时间线、可见性事件 |
| `v1/ingredients/{entity_id}.json` | 配料原文顺序、营养原文、致敏原原文、检索键 |
| `v1/demo/index.json` | 离线兜底样例（与正式契约同构） |

## 五条工程红线与实现

### 1. 零推断

- `packages/semantic_gate/rules.py` 定义附录 B 六类禁用词 + 红绿灯配色的正则；
- 扫描范围覆盖静态 JSON、前端源码、打包后的单文件 HTML；
- 仅 `raw_value` / `raw_text` 等**来源原文字段**豁免（因为不得改写来源措辞）；
- 对抗语料 `packages/semantic_gate/adversarial_corpus.json` 中每个类别都必须被拦截，合规文案不得超过度拦截。

### 2. 无最终真相字段

- 每条展示记录都绑定 `source_id` / `evidence_id` / `retrieved_at` / `raw_value`；
- `observations[]` 并列保存全部来源的原始快照，不做合并、不做投票；
- 门禁 `test_source_trace_rate.py` 强制 **100%** 字段追溯率，并禁止出现 `final_truth` / `verdict` / `conclusion` 类字段。

### 3. 不自动合并候选

- 仅 `EXACT_IDENTIFIER_MATCH`（GTIN 精确相等、UDI_DI 去前导 0 后精确相等）建立关联；
- 名称相似、SC 许可号、同品牌不同净含量一律 `MANUAL_CONFIRM` 并列；
- 同一次抓取内的多条记录按 `record_id` 逐条并列，不因共享证据快照而合并。

### 4. 端侧全私有与零 Cookie

- 拍照原图仅驻留内存（`blob:` URL），不落盘、不上传；
- OCR / 条码识别全部在本地 WASM 中执行，语言数据内联；
- 忌口词条、饮食计划、扫码历史全部在 IndexedDB（`product-public-dossier-local`）；
- 不使用 `localStorage` 存储个人数据，不写 Cookie，不发送任何上报请求；
- 界面固定展示 `cookie.length=0` 与 IndexedDB 计数作为运行时审计标识。

### 5. 合规采集与凭证隔离

- `source_policy_registry.json` 是唯一门禁：`ACCESS_PENDING` / `SOURCE_UNAVAILABLE` 的源在**构造阶段**即被拒绝；
- 命中验证码 / 登录墙即抛 `CaptchaOrLoginRequired` 终止，绝不绕过；
- 限流按 `requests_per_minute` + `min_interval_ms` 执行，重试次数不得超过注册表上限；
- 结构变更立即熔断（`SOURCE_FORMAT_CHANGED`），不猜测解析；
- 凭据只允许以 `*_env` 环境变量名形式登记，写盘前逐字段做凭据扫描（哈希字段按名放行）。

## 验收门禁一览

| # | 门禁 | 关键断言 |
| --- | --- | --- |
| 1 | `test_source_trace_rate.py` | 字段追溯率 = 100%；无最终真相字段 |
| 2 | `test_field_extraction_accuracy.py` | 抽取准确率 ≥ 99.5%；配料顺序不得重排 |
| 3 | `test_source_coverage.py` | 7 个源全部披露；状态不可伪装；16 区块齐全 |
| 4 | `test_history_retention.py` | 来源更新后旧快照保留；可见性事件只追加 |
| 5 | `test_query_reproducibility.py` | 相同账本 + 相同输入 → 相同产物；产物哈希可校验 |
| 6 | `test_policy_compliance.py` | 待授权源无法运行；无凭据泄露；验证码即终止 |
| 7 | `test_entity_separation.py` | 多候选/多 SKU 并列不合并；不同条码证据不串档 |
| 8 | `test_privacy_and_zero_cookie.py` | 真实浏览器：零 Cookie、零外发、词条只在 IndexedDB |
| 9 | `test_integrity_check.py` | 只追加触发器生效；SHA-256 内容寻址；篡改可检出 |
| 10 | `test_zero_inference_gate.py` | 对抗语料全拦截；产物/源码/单文件产物零禁用词 |

## 数据源

| source_id | 机构 | 访问模式 | 当前状态 |
| --- | --- | --- | --- |
| `udi` | 国家药品监督管理局 | OPEN_API | 已接入（夹具） |
| `gs1` | GS1 China | OPEN_BULK | 已接入（夹具） |
| `label_transcript` | 原始包装标签转录 | OPEN_BULK | 已接入（构建机录入） |
| `samr_gov` | 国家市场监督管理总局 | OPEN_API | 已接入（夹具） |
| `samr_std` | 国家标准全文公开系统 | OPEN_BULK | 已接入（按标准号查询） |
| `cnipa` | 国家知识产权局 | ACCESS_PENDING | 待授权；档案中显式标注 |
| `cifer` | 海关总署 CIFER | SOURCE_UNAVAILABLE | 不可稳定访问；档案中显式标注 |

所有夹具均为**合成演示数据**，不对应任何真实商品。

## 免责声明

本工具仅展示已接入公开来源的原始记录与机械对照结果，不提供任何评价性结论。
饮食对照为纯词条子串匹配，不构成医疗、营养或购买建议，请以商品实物标签为准。
