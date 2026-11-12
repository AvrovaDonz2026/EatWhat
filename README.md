# EatWhat — 消费者商品公开信息展示器

一个**即拿即用**的商品公开信息展示系统：离线 Python 管线把多源公开记录编译成只读静态 JSON；纯静态网页读取这些 JSON，把全部个人数据留在浏览器 IndexedDB。

只做 **检索 · 取得 · 保存 · 结构化 · 并列 · 对照 · 时间线 · 来源追溯**。
不做风险评分、真伪判断、合法性定性、关系推断、购买建议、功效评价。

## 为什么存在

真实的商品信息散落在药监、市场监管、标准、条码、商标、海关等多个公开系统里，同一条商品在不同系统下的记录经常互相矛盾（例如某许可在一处有效、在另一处已注销）。

常见的处理方式有两种，本系统都不用：

- **挑一个"最可信"的写进单一字段** → 系统替用户做了判断；
- **按名称或外观自动归并** → 系统做了关系推断。

本系统只做第三件事：把每条记录连同它的来源、证据、采集时间原样并列出来，让读者自己看。所有判断留在读者那里。

这也是它同时是**合规演示**的原因：它能证明一套「只展示、不评价」的信息系统在工程上是可行的 —— 有只追加账本、有内容寻址、有语义门禁、有真实浏览器验收。

## 五条不可违反的工程红线

| # | 红线 | 具体约束 |
| --- | --- | --- |
| 1 | **零推断（ZERO_INFERENCE）** | 仅检索、取得、保存、结构化、并列、对照、时间线、来源追溯。无评分、无真伪判断、无合法性定性、无关系推断、无购买建议、无功效声称 |
| 2 | **无最终真相字段** | 冲突来源全部并列存于 `observations[]`，每条绑定 `source_id` / `evidence_id` / `retrieved_at` / `raw_value`。不存在 `final_truth` / `verdict` / `conclusion` 类字段 |
| 3 | **不自动合并** | `match_candidates[]` 仅在 `EXACT_IDENTIFIER_MATCH`（GTIN 精确相等、UDI_DI 去前导 0 后精确相等）时建立关联。同名、文本相似、同许可号一律并列并要求 `MANUAL_CONFIRM` |
| 4 | **端侧全私有、零 Cookie** | 照片、OCR 文本、忌口词条、饮食计划全部留在浏览器。无 HTTP Cookie、无上报、无日志、无云端上传 |
| 5 | **合规采集与凭证隔离** | 不绕验证码、不破限流。API token / appId 只以构建机环境变量形式存在，绝不写入静态 JSON 或网页源码 |

每条红线都有对应的自动化门禁守门，见「验收门禁」一节。

## 架构一览

```
        构建机（离线、可重复）                        浏览器（纯静态）
┌────────────────────────────────┐        ┌──────────────────────────────┐
│ source_policy_registry.json    │        │ pages/  三个页签              │
│        │ 策略门禁               │        │  扫码对照 / 忌口与计划 / 档案  │
│        ▼                        │        │                              │
│ SourceAdapter                   │        │ services/ 只读 GET /v1/*.json │
│  限流 · 退避 · 结构熔断 · 拒验证码│        │        │ 取不到→DEMO_FALLBACK │
│        │ 原始响应                │        │        ▼                     │
│        ▼                        │        │ 16 区块渲染 + 机械词条对照     │
│ Ledger (SQLite, 只追加)         │        │                              │
│  8 个 UPDATE/DELETE 触发器      │        │ ocr/  zxing-wasm  tesseract.js│
│  SHA-256 内容寻址               │        │       图片不出浏览器           │
│        │ 全部历史快照             │        │                              │
│        ▼                        │        │ local_store/ IndexedDB        │
│ dossier  16 区块装配            │        │  diet_rules / diet_plan /     │
│        │ 逐字段绑定证据           │        │  scan_history                 │
│        ▼                        │        │                              │
│ emit_static                     │        │ webgpu/ 织纹背景（失败即 CSS） │
│        │ 写盘前凭据检查           │        │                              │
│        ▼                        │        └──────────────┬───────────────┘
│ dist_data/v1/*.json             │                       │
└────────────────┬───────────────┘                       │
                 │ scripts/sync-data.mjs                 │
                 ▼                                       │
      apps/web/dist/data/v1/*.json ──── GET(同站, omit cookie) ┘
                 │
                 ▼
      apps/web/dist/index.html   ← 单文件，双击即用
```

细节见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 快速开始

### 最快路径：直接用现成的单文件

```bash
open apps/web/dist/index.html          # macOS；或直接双击
```

`apps/web/dist/index.html` 是一个约 **7.6 MB 的自包含单文件**：

- 所有 JS / CSS 内联；
- OCR 内核（`tesseract-core-simd-lstm.wasm.js`）、worker 脚本、中文简体语言数据全部内联（core 源码拼进 worker 引导脚本，语言数据以字节直传）；
- 条码 WASM（zxing）内联；
- 运行时**零外发请求**（已由 Playwright 在真实 Chromium 中验证）。

此时只分发 `index.html` 也能跑：取不到静态分片时自动退回**内置离线样例**，界面标注 `DEMO_FALLBACK`。

### 推荐路径：带上真实档案

```bash
python3 -m http.server 8080 --directory apps/web/dist
# 打开 http://127.0.0.1:8080
```

`apps/web/dist/data/v1/` 存在时，界面读取真实分片并标注 `STATIC_LIBRARY`。

### 从零构建

```bash
# 依赖
npm install
python3 -m venv .venv
.venv/bin/pip install pytest playwright
.venv/bin/python -m playwright install chromium

# 1) 编译静态数据（账本只追加）
npm run build:data

# 2) 单文件网页 + 同步分片
npm run build:web

# 3) 语义门禁
npm run gate

# 4) 全部验收门禁（当前 116 个测试）
npm test
```

或者一条命令走完 1–3：`npm run build`。

## 脚本

根 `package.json` 的脚本（实际定义，非推测）：

| 脚本 | 实际命令 | 作用 |
| --- | --- | --- |
| `npm run dev` | `npm run dev -w apps/web` | Vite 开发服务器（端口 5173） |
| `npm run build:data` | `python3 -m pipeline.build` | 编译静态分片到 `dist_data/` |
| `npm run build:web` | `npm run build -w apps/web && npm run sync:data` | 类型检查 + 单文件打包 + 同步分片 |
| `npm run sync:data` | `node scripts/sync-data.mjs` | `dist_data/` → `apps/web/dist/data/` |
| `npm run gate` | `python3 -m packages.semantic_gate.cli_scan --all` | 语义门禁 |
| `npm run build` | `build:data && build:web && gate` | 全链路 |
| `npm test` | `.venv/bin/python -m pytest tests/acceptance_gates -q` | 验收门禁 |

`apps/web/package.json` 的脚本：`dev`（`vite`）、`build`（`tsc -b && vite build`）、`preview`（`vite preview`）。

管线 CLI 参数（`python3 -m pipeline.build --help`）：

```bash
python3 -m pipeline.build                 # 夹具基线构建
python3 -m pipeline.build --reset         # 先清空 dist_data（账本不动）
python3 -m pipeline.build --rev2          # 用第二版夹具追加快照，演示历史保留
python3 -m pipeline.build --gtins 6901234567892,6905554443331
python3 -m pipeline.build --ledger /path/to/ledger.sqlite3
python3 -m pipeline.build --fixtures pipeline/fixtures/v1_rev2
python3 -m pipeline.build --note "baseline-2026-11"
```

> `--reset-ledger` 会丢弃整个账本，必须同时设置 `PPD_ALLOW_LEDGER_RESET=1` 才生效，**仅用于从零重建演示基线**。正常运行请勿使用 —— 账本的设计前提是永不丢弃历史。

完整操作说明见 [`docs/PIPELINE.md`](docs/PIPELINE.md)。

## 仓库布局

```
EatWhat/
├── apps/web/                        # 纯静态 SPA（React 19 + Vite 6 + Tailwind v4）
│   ├── src/
│   │   ├── ocr/barcode.ts               # 端侧条码识别（zxing-wasm，WASM 内联）
│   │   ├── ocr/local_ocr.ts             # 端侧 OCR（tesseract.js，图片不出浏览器）
│   │   ├── local_store/db.ts            # IndexedDB（idb）：词条 / 计划 / 扫码历史
│   │   ├── local_store/backup.ts        # 本地备份：校验 / 导出结构 / 合并或覆盖写入
│   │   ├── local_store/allergen_presets.ts  # GB 7718-2025 致敏原预设词条
│   │   ├── engine/local_diet_matcher.ts     # 机械子串/别名对照引擎
│   │   ├── engine/nutrition_scaling.ts       # 每 100 克原文 × 克重 / 100（纯算术）
│   │   ├── services/static_dossier_client.ts # 只读 /v1/ 静态 JSON + 兜底
│   │   ├── services/demo_fallback.ts        # 打包进单文件的离线样例
│   │   ├── webgpu/weave_texture.ts          # 单 pass 织纹背景，失败即 CSS
│   │   ├── components/{ui,WeaveTexture}.tsx # 档案文献风格构件
│   │   └── pages/{Scan,Dossier,Diet}Page.tsx # 三个页签
│   ├── index.html
│   ├── vite.config.ts               # 单文件产物配置（PPD_SPLIT=1 可分离）
│   └── dist/index.html              # 交付产物（构建生成）
├── pipeline/                        # 离线编译管线（Python 3.9+，仅标准库）
│   ├── build.py                     # CLI 入口
│   ├── config/source_policy_registry.json  # 数据源策略注册表（唯一门禁）
│   ├── config/policy.py                    # 注册表加载与红线校验
│   ├── adapters/base.py                    # 门禁 + 限流 + 退避 + 熔断
│   ├── adapters/v1/__init__.py              # 7 个适配器类
│   ├── builder/ledger.py                   # SQLite 只追加账本 + SHA-256
│   ├── builder/dossier.py                  # 16 区块装配 + 候选并列 + 覆盖清单
│   ├── builder/emit_static.py              # 静态分片编译
│   ├── builder/lexicon_terms.py            # 机械词条映射（配料/致敏原/营养）
│   ├── fixtures/v1/                        # 基线夹具
│   ├── fixtures/v1_rev2/                   # 第二版夹具（模拟来源更新）
│   └── fixtures_index.py                   # 夹具 GTIN 清单
├── packages/semantic_gate/          # 附录 B 语义门禁
│   ├── rules.py                            # 禁用词表 + 风险配色正则 + 豁免
│   ├── cli_scan.py                         # 扫描 CLI
│   └── adversarial_corpus.json             # 对抗语料（对抗样本 + 合规对照）
├── tests/acceptance_gates/          # 116 个自动化验收测试
├── scripts/sync-data.mjs            # dist_data → apps/web/dist/data
├── docs/                            # 工程文档（本目录）
├── legacy/                          # 已取代的设计原型（不参与构建）
├── .github/workflows/pages.yml      # CI：构建 + 门禁 + 部署 Pages
├── dist_data/v1/                    # 编译产物（构建生成，gitignore）
└── .state/ledger.sqlite3            # 账本（构建生成，gitignore）
```

## 数据契约

四类只读分片，写入位置由 `pipeline/builder/emit_static.py` 决定：

| 路径 | 内容 |
| --- | --- |
| `v1/manifest.json` | 构建版本、全部源的接入状态、适配器版本、覆盖汇总、条码索引 |
| `v1/gtin/{gtin}.json` | 该条码的候选并列（`EXACT_IDENTIFIER_MATCH` / `MANUAL_CONFIRM`）+ 7 源覆盖清单 |
| `v1/products/{entity_id}.json` | 16 区块档案、多源观测并列、历史时间线、可见性事件 |
| `v1/ingredients/{entity_id}.json` | 配料原文顺序、营养原文、致敏原原文、检索键 |
| `v1/demo/index.json` | 离线兜底样例（与正式契约同构） |

16 个区块（`dossier.BLOCK_IDS` 实际顺序）：

```
01 identity_basic            09 license_records
02 match_candidates          10 company_prefix
03 label_ingredients         11 trademark_records
04 nutrition_facts           12 referenced_standards
05 explicit_allergens        13 import_export_records
06 explicit_unsuitable_groups 14 observations
07 storage_and_shelf_life    15 history_timeline
08 registration_records      16 source_visibility
```

`entity_id = "ent_" + sha256({"gtin": gtin})[:16]`，同一 GTIN 永远同 entity。

字段级契约、覆盖状态（`OPEN_BULK` / `OPEN_API` / `ACCESS_PENDING` / `SOURCE_UNAVAILABLE` / `QUERIED_MATCHED` / `QUERIED_NO_MATCH` / `QUERY_FAILED` / `NOT_QUERIED`）、可见性事件（`CURRENTLY_VISIBLE` / `HISTORICALLY_OBSERVED` / `REMOVED`）与「当前生效」推导规则，见 [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md)。

## 验收门禁

`tests/acceptance_gates/` 当前收集 **116 个测试**（`.venv/bin/python -m pytest tests/acceptance_gates --collect-only -q` 实测），分布在 16 个模块。前 10 项为计划书定义的硬门禁，另有离线样例契约门禁（`test_demo_contract.py`）、演示样例来源属性与 06 区块并列来源焦点门禁（`test_demo_origin_and_compare_focus.py`）、饮食按克重折算与本地备份门禁（`test_diet_scaling_and_backup.py`）、手机端可读性门禁（`test_mobile_comprehension.py`），以及针对门禁本身的守门测试（防止「扫描范围为空导致假绿灯」、「同一证据多时间戳」与「标记/切换空转」复发）。

| # | 模块 | 关键断言 |
| --- | --- | --- |
| 1 | `test_source_trace_rate.py` | 字段追溯率 = **100%**；`evidence_id` 必须存在于账本；无最终真相字段 |
| 2 | `test_field_extraction_accuracy.py` | 抽取准确率 ≥ **99.5%**；配料顺序不得重排 |
| 3 | `test_source_coverage.py` | 7 源全部披露；`ACCESS_PENDING`/`SOURCE_UNAVAILABLE` 不可伪装；16 区块齐全 |
| 4 | `test_history_retention.py` | 来源更新后旧快照保留；可见性事件只追加；触发器阻断改写 |
| 5 | `test_query_reproducibility.py` | 相同账本 + 相同输入 → 相同产物；产物哈希可校验 |
| 6 | `test_policy_compliance.py` | 待授权源无法运行；无凭据泄露；验证码即终止；重试上限不可提 |
| 7 | `test_entity_separation.py` | 多候选 / 多 SKU / 同名不同码一律并列不合并 |
| 8 | `test_privacy_and_zero_cookie.py` | 真实 Chromium：零 Cookie、零外发、词条只在 IndexedDB |
| 8b | `test_client_ocr.py` | 真实 Chromium + 真实图片：OCR 必须真的出中文配料文本、能切词、零跨站请求 |
| 8c | `test_layout_wrapping.py` | 真实 Chromium：中文词语不得在窄容器里逐字竖排折叠（覆盖档案页 + 扫码页两套结构） |
| 9 | `test_integrity_check.py` | 只追加触发器生效；SHA-256 内容寻址；篡改可检出 |
| 10 | `test_zero_inference_gate.py` | 对抗语料全拦截；源码与单文件产物零禁用词 |
| + | `test_demo_contract.py` | 离线样例与正式契约同构、不虚构商品、必须自我说明 |
| + | `test_demo_origin_and_compare_focus.py` | 真实 Chromium：演示样例标记必须按 `origin` 字段出现在每个展示位置；06 区块两份来源始终并列可分辨，切换只改焦点不隐藏数据 |
| + | `test_diet_scaling_and_backup.py` | 真实 Chromium：50 g 折算必须逐项等于「每 100 克原文 × 0.5」、折算依据可追溯到 entity/evidence；备份可导出为本地 JSON 并导回；非法文件必须报错且不破坏已有数据 |

> 产物（`dist_data/`）侧的语义扫描当前存在一处实现缺口，详见 [`docs/SEMANTIC_GATE.md`](docs/SEMANTIC_GATE.md#扫描范围实现细节) 的「扫描范围实现细节」一节。

```bash
npm test
# 或
.venv/bin/python -m pytest tests/acceptance_gates -q
```

逐项断言与失败条件见 [`docs/ACCEPTANCE_GATES.md`](docs/ACCEPTANCE_GATES.md)。

## 部署

**GitHub Pages**：<https://avrovadonz2026.github.io/EatWhat/>

CI 定义在 `.github/workflows/pages.yml`，触发条件为 `push: branches: [main]` 与 `workflow_dispatch`。流水线步骤：

1. Checkout → Python 3.12 → Node 20（启用 npm 缓存）
2. `npm ci` + `pip install pytest playwright` + `playwright install --with-deps chromium`
3. `npm run build:data`（离线构建静态分片）
4. `npm run build:web`（单文件打包 + 分片同步进 `apps/web/dist/data`）
5. `npm run gate`（语义门禁）
6. `python -m pytest tests/acceptance_gates -q`（全部门禁，含真实 Chromium）
7. 校验 `apps/web/dist/index.html` 与 `apps/web/dist/data/v1/manifest.json` 存在，`du -sh` 打印体积
8. `actions/upload-pages-artifact@v3` 上传 `apps/web/dist`
9. `actions/deploy-pages@v4` 部署

权限为 `contents: read` / `pages: write` / `id-token: write`，`concurrency.group = pages` 且 `cancel-in-progress: false`（避免部署被后续提交打断）。

任何一步失败都会阻止部署 —— 门禁失败即不可上线。

也可以部署到任意静态托管（S3、Cloudflare Pages、Netlify）：把 `apps/web/dist/` 整个目录上传即可，无需服务端逻辑。

## 隐私

| 留在浏览器 | 从服务器取 |
| --- | --- |
| 忌口词条、饮食计划、扫码历史（IndexedDB `product-public-dossier-local`） | 只读的公开静态 JSON 分片 |
| 按克重的营养折算值（每 100 克原文 × 克重 / 100，纯算术） | 请求显式 `credentials: "omit"`、`referrerPolicy: "no-referrer"` |
| 本地备份 JSON（用户主动导出 / 导入，Blob + FileReader） | 备份文件只写到本机下载目录；导入文件只经 `FileReader` 读入内存 |
| 照片原图（仅内存 `Blob`，不落盘） | 无 Cookie、无上报、无日志 |
| OCR 文本、条码结果、机械对照结果（内存） | 页面自身不发任何上报请求 |

界面页眉常驻两个运行时审计标识：`cookie.length=0`（每 2 秒重读一次 `document.cookie.length`）与 IndexedDB 计数。

自行验证方法（DevTools Network / Application 面板的具体步骤、OCR 内联手法、单文件 vs 静态目录模式的差异）见 [`docs/PRIVACY.md`](docs/PRIVACY.md)。

## 数据源

| `source_id` | 机构 | 访问模式 | 当前状态 |
| --- | --- | --- | --- |
| `udi` | 国家药品监督管理局 | `OPEN_API` | 已接入（夹具） |
| `gs1` | GS1 China | `OPEN_BULK` | 已接入（夹具） |
| `label_transcript` | 原始包装标签转录 | `OPEN_BULK` | 已接入（构建机录入，`local://` 端点） |
| `samr_gov` | 国家市场监督管理总局 | `OPEN_API` | 已接入（夹具） |
| `samr_std` | 国家标准全文公开系统 | `OPEN_BULK` | 已接入（按标准号查询） |
| `cnipa` | 国家知识产权局 | `ACCESS_PENDING` | 待授权；适配器禁止运行，档案中显式标注 |
| `cifer` | 海关总署 CIFER | `SOURCE_UNAVAILABLE` | 不可稳定访问；适配器禁止运行，档案中显式标注 |

### 关于夹具（重要）

`pipeline/fixtures/` 下的全部数据都是**合成演示数据**：

- 条码 `6901234567892` / `6905554443331` / `6901112223339` 及其品牌、商品名、配料、营养值、许可号、标准号均为构造的样例；
- 不对应任何真实商品、企业或记录；
- 端点域名使用 `*.example.gov.cn` / `*.example.cn` 形式，明确标注为占位。

它们的唯一用途是演示数据结构与验证门禁。真实接入需按 [`docs/PIPELINE.md`](docs/PIPELINE.md) 的新增数据源流程，完成授权与策略登记后才可运行。

## 修改代码

| 你想改什么 | 从哪里开始 | 必须同时注意 |
| --- | --- | --- |
| 界面 / 交互 | `apps/web/src/pages/`、`components/ui.tsx` | 文案受语义门禁约束；状态只能用文字 + 边框表达，不可用红/绿/黄语义色 |
| 16 区块内容 | `pipeline/builder/dossier.py` | 新增区块要同步 `BLOCK_IDS`；门禁 3 断言区块数 = 16 |
| 新增数据源 | `pipeline/config/source_policy_registry.json` + `adapters/v1/` | 必须同步 `emit_static.IDENTIFIER_TYPE_BY_SOURCE`、`dossier.STATUS_TIER`、夹具 |
| 新增禁用词 | `packages/semantic_gate/rules.py` | 同步 `adversarial_corpus.json` 与类别集合断言 |
| 加前端依赖 | `apps/web/package.json` | 必须保持零运行时网络请求与自包含单文件 |

新增依赖前请对照 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) 的「单文件约束」检查清单（尤其是 WASM / worker / CDN 默认 `locateFile` 三项）。

## 许可

仓库根目录的 `LICENSE` 为 **Mozilla Public License Version 2.0**（MPL-2.0）。

## 免责声明

本工具仅展示已接入公开来源的原始记录与机械对照结果，不提供任何评价性结论。
饮食对照为纯词条子串匹配，不构成医疗、营养或购买建议，请以商品实物标签为准。

## 文档

| 文档 | 内容 |
| --- | --- |
| [`docs/README.md`](docs/README.md) | 文档索引与建议阅读顺序 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 组件边界、数据流、为什么只追加 / 为什么无最终真相 / 信任链 |
| [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md) | `/v1/` 分片字段契约、16 区块、覆盖状态、可见性事件 |
| [`docs/PIPELINE.md`](docs/PIPELINE.md) | 构建操作手册：CLI、账本语义、新增数据源、凭据、限流、熔断恢复 |
| [`docs/SEMANTIC_GATE.md`](docs/SEMANTIC_GATE.md) | 附录 B 门禁：禁用词类别、扫描范围、豁免、对抗语料 |
| [`docs/PRIVACY.md`](docs/PRIVACY.md) | 端侧边界、IndexedDB 结构、OCR 路径、自行验证方法 |
| [`docs/ACCEPTANCE_GATES.md`](docs/ACCEPTANCE_GATES.md) | 12 个门禁模块逐项断言与失败条件 |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | 环境、构建、测试、常见故障 |
| [`AGENTS.md`](AGENTS.md) | 仓库协作铁律：每一步修改必须同步更新文档（含映射表） |

> 贡献规则：任何改动只要影响产物契约、门禁、构建命令或 UI 证据展示，必须在**同一提交内**同步更新对应文档，详见 [`AGENTS.md`](AGENTS.md)。
