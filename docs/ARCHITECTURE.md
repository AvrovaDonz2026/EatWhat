# 架构

本文描述系统边界与数据流。所有描述对应仓库现有实现，不含未来规划。

## 一句话

左侧是**离线构建机**（Python，把公开来源的原始响应编译成只读 JSON），右侧是**纯静态浏览器端**（React，读 JSON、把所有个人数据留在 IndexedDB）。两者之间只有 HTTP GET 的只读 JSON。

## 全景图

```
                          构建机（离线，可重复执行）
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  source_policy_registry.json ──(load_registry / 门禁校验)──┐          │
│                                                            │          │
│   ┌──────────────┐                                         ▼          │
│   │ Transport    │   LocalFixtureTransport（离线夹具）          │          │
│   │ (取数通道)    │   StaticHttpTransport（默认禁用）            │          │
│   └──────┬───────┘                                         │          │
│          │  raw JSON                                       │          │
│          ▼                                                 │          │
│   ┌──────────────┐  policy gate + throttle + backoff +     │          │
│   │ SourceAdapter│  格式熔断 + 验证码拒绝                    │          │
│   └──────┬───────┘                                         │          │
│          │  {source_id, payload, request_url, …}            │          │
│          ▼                                                 │          │
│   ┌────────────────────────────────────────────────────┐   │          │
│   │ Ledger (SQLite)                                    │   │          │
│   │   snapshots       ← 只追加，UPDATE/DELETE 被触发器拒绝│   │          │
│   │   observations_log← 每次观测一条（含重复内容观测）     │   │          │
│   │   visibility_events ← 只追加                         │   │          │
│   │   build_runs / artifacts                            │   │          │
│   │   每条快照 content_hash = SHA-256(canonical_json)    │   │          │
│   └──────────────┬─────────────────────────────────────┘   │          │
│                  │  全部快照 + 可见性事件                    │          │
│                  ▼                                         │          │
│   ┌────────────────────────────────────────────────────┐   │          │
│   │ dossier.assemble_blocks  → 16 区块（逐字段绑定证据）  │   │          │
│   │ dossier.build_candidates → 候选并列（精确/手动确认）  │   │          │
│   │ dossier.coverage_manifest→ 每源覆盖状态（不隐藏缺口） │   │          │
│   └──────────────┬─────────────────────────────────────┘   │          │
│                  ▼                                         │          │
│   ┌────────────────────────────────────────────────────┐   │          │
│   │ emit_static → dist_data/v1/{manifest,gtin,products, │   │          │
│   │               ingredients,demo}                     │   │          │
│   │ 写盘前 assert_no_credentials；产物 SHA-256 记账本    │   │          │
│   └────────────────────────────────────────────────────┘   │          │
└──────────────────────────────────────────────────────────────────────┘
                             │
                             │  scripts/sync-data.mjs（构建后同步）
                             ▼
                    apps/web/dist/data/v1/*.json   ← 只读静态分片

                             ▲
                             │  HTTP GET（credentials: omit, referrer: no-referrer）
                             │
┌────────────────────────────┴─────────────────────────────────────────┐
│                          浏览器端（单文件或静态目录）                   │
│                                                                      │
│  pages/ScanPage     ← 条码检索 / 端侧 OCR / 机械词条对照               │
│  pages/DietPage     ← 忌口词条 + 饮食计划（纯本地）                    │
│  pages/DossierPage  ← 16 区块档案阅读                                  │
│                                                                      │
│  services/static_dossier_client.ts  只读 /v1/，取不到 → demo 兜底      │
│  engine/local_diet_matcher.ts       归一化 + 子串/别名匹配             │
│  ocr/barcode.ts   (zxing-wasm)      端侧条码识别                       │
│  ocr/local_ocr.ts (tesseract.js)    端侧 OCR，图片不出浏览器            │
│  local_store/db.ts  (idb)           IndexedDB：diet_rules /           │
│                                     diet_plan / scan_history          │
│  webgpu/weave_texture.ts            单 pass 织纹背景，失败即 CSS 兜底   │
└──────────────────────────────────────────────────────────────────────┘
```

## 溯源信息的呈现分层

同一份数据要同时服务两类读者：普通消费者只想知道「这条信息来自哪、什么时候取的」；审计者需要 `evidence_id` / `record_id` / `payload_sha256` 才能逐条核对。把两者混在一屏，结果是消费者被哈希值淹没、审计者也没得到结构化视图。因此 `components/ui.tsx` 提供三层构件：

| 层 | 构件 | 默认可见内容 | 何时出现 |
| --- | --- | --- | --- |
| 1 消费者 | `SourceNotes` / `SourceLine` | 来源名称 + 取得日期（`2026-11-12`），小号无衬线 + 左侧发丝线 | 始终 |
| 2 按需溯源 | `TraceDisclosure` | 折叠标题「查看来源与证据 (N)」，N = 去重后的证据条数 | 有证据绑定时 |
| 3 审计视图 | 16 区块档案 | 完整绑定字段与时间线 | 用户打开档案页 |

两条实现约束：

- **来源名称一律来自数据**：`buildSourceLabeler()` 只读载荷里的 `coverage_manifest`（`authority` + `display_name`），代码里不硬编码任何来源名；查不到时回落成 `source_id` 或「来源未标注」，而不是猜一个机构名。
- **折叠时正文不进 DOM**：`TraceDisclosure` 用 `<details>`，但内部 `trace__body` 只在 `revealed === true` 时挂载。因此默认视图里 `evidence_id`、`payload_sha256` 在 DOM 中根本不存在，不是「被 CSS 藏起来」——这一点对「消费者视图不暴露内部标识」的承诺很重要。

`distinctTraceRecords()` 按 `(source_id, evidence_id)` 去重并合并首个非空 `record_id` / `payload_sha256` / `retrieved_at`，所以标题里的 N 是**证据条数**而不是展示条目数：同一证据被多个区块引用时不会重复计数。

## 数据来源属性的呈现（`origin`）

界面有**两种来源**，它们不是一回事，因此不能画成同一种东西：

| `origin` | 出处 | 性质 |
| --- | --- | --- |
| `STATIC_LIBRARY` | `v1/gtin/{gtin}.json` + `v1/products` + `v1/ingredients` | 构建机编译出的只读静态档案库分片 |
| `DEMO_FALLBACK` | `v1/demo/index.json`，取不到时用单文件内联的 `DEMO_FALLBACK` | 打包进前端的合成演示样例 |
| `LOCAL_OCR` | 端侧识别 / 手动粘贴文本 | 只在本次会话内存中存在 |

`DEMO_FALLBACK` 的位置一律渲染 `DemoMark`（`components/ui.tsx`）：发丝线框 + 左侧 2px 哑赭竖线 + 小号大写字距「演示样例」标签 + 一句「不等于已联网的真实商品库分片」。**不**使用彩色徽章、胶囊或投影。

渲染点分布（`data-demo-scope` 属性可供门禁断言）：

| 位置 | `data-demo-scope` |
| --- | --- |
| ScanPage 演示样例按钮区 | `scan-demo-list` |
| 03 数据源覆盖清单 | `scan-03-coverage` |
| 04 条码候选 | `scan-04-candidates` |
| 05 配料与营养原文 | `scan-05-ingredients` |
| 06 条码静态档案对照栏 | `scan-06-static` |
| DossierPage 封面 provenance 区 | `dossier-provenance` |

关键约束：**判定只读载荷的 `origin` 字段**，前端不硬编码演示条码清单。`demoGtins()` 返回的每个条目自带 `origin: "DEMO_FALLBACK"`，界面按该字段决定是否渲染标记。失败模式的对比很清楚——硬编码条码时，`v1/demo/index.json` 里加减一个样例条目，标记就会静默失准；按字段驱动则自动跟随。

06 区块的「来源 B（OCR / 手动修正文本）」不是商品库分片，用 `.demo-mark--inline` 变体（同一发丝线框风格，无标签色）说明「不是联网取得的商品库分片；原图与文本均留在本浏览器」。它复用同一个视觉语言，但不使用 `data-demo-mark` 属性——那个属性专指 `DEMO_FALLBACK`，门禁依赖这个区分。

## 组件边界

| 组件 | 职责 | 明确不做 |
| --- | --- | --- |
| `pipeline/config/policy.py` | 加载并校验策略注册表（字段、类型、区间、凭据形态） | 不做任何取数 |
| `pipeline/adapters/base.py` | 单源取数：策略门禁、令牌桶限流、指数退避、结构熔断、验证码拒绝 | 不解析业务字段 |
| `pipeline/adapters/v1/` | 声明各源 `source_id` / `identifier_type` / 版本 | 不做字段推断 |
| `pipeline/builder/ledger.py` | 只追加写入、内容寻址、脱敏与凭据检查、完整性校验 | 不决定「哪个值是对的」 |
| `pipeline/builder/dossier.py` | 把快照映射为 16 区块、候选并列、覆盖清单 | 不合并、不评分、不给结论 |
| `pipeline/builder/emit_static.py` | 编排构建、写分片、记录产物哈希、生成 demo 索引 | 不写网络可达的服务端 |
| `packages/semantic_gate/` | 扫描源码/产物/打包结果的禁用词与风险配色 | 不修改被扫描内容 |
| `apps/web/src/services/` | 读静态分片 + demo 兜底 | 不上传、不带 Cookie |
| `apps/web/src/engine/` | 归一化 + 子串匹配 | 不做语义推理 |
| `apps/web/src/ocr/` | 端侧识别 | 不把图像发出去 |
| `apps/web/src/local_store/` | IndexedDB 持久化 | 不写 Cookie / localStorage 中的个人数据 |
| `apps/web/src/pages/` | 三页签界面 | 不含评价性文案 |

## 构建期数据流（逐步）

1. **加载注册表** — `load_registry()` 读取 `source_policy_registry.json`，逐项做字段全集校验（缺失/多余字段都报错）、`source_id` 正则、`access_mode` 枚举、`min_interval_ms >= 1000`、`max_retries <= 5`、凭据必须是 `^[A-Z][A-Z0-9_]{2,63}$` 形式的环境变量名或 `null`。
2. **确定可运行集合** — `policy.runnable` 仅当 `access_mode ∈ {OPEN_BULK, OPEN_API}`。`Builder` 只遍历这个集合，所以 `ACCESS_PENDING` / `SOURCE_UNAVAILABLE` 的源**不会进入构建序列**。
3. **构造适配器** — `SourceAdapter.__init__` 再次执行 `registry.require_runnable()`。未登记 → `PolicyGateError`；未放行 → `PolicyBlocked`。任何绕过构建序列的调用都会在构造阶段失败。
4. **取数** — 每次 `fetch()` 前先 `throttle.acquire()`（`min_interval_ms` 最小间隔 + 每分钟配额双约束），失败按 `base_ms * factor^attempt + jitter` 退避，重试次数不得超过注册表上限。响应必须是 JSON 对象且含全部 `schema_keys`，否则立即熔断抛 `SourceFormatChanged` 并置 `_broken = True`。
5. **落账本** — `append_snapshot()` 做凭据检查 → 计算 `payload_sha256 = SHA-256(canonical_json(payload))` → 派生 `evidence_id = "ev_" + SHA-256({source_id, source_key, adapter_version, payload_sha256})[:20]`。完全等价的快照不重复插入，只追加一条 `observations_log`。
6. **零记录不产证据** — `records` 为空数组时 `Builder` 直接跳过（不落快照）。否则空记录会成为该源「最新快照」，把真实历史记录遮蔽在字段区块之外。
7. **装配区块** — `_latest_by_source()` 取每源最新快照供字段区块使用；`_historic_snapshots()` 取全部历史供观测/时间线使用；`effective_visibility()` 由事件序列按 `seq` 推导当前状态。
8. **写分片** — `_write_json()` 先 `assert_no_credentials()`（按字段名区分哈希与凭据），再序列化落盘，返回内容 SHA-256 记入 `artifacts` 表。
9. **写清单与索引** — manifest 内联 `adapter_versions`、`query_index`、覆盖汇总，以及三个恒为 `true` 的自描述布尔值。

## 为什么只追加

若允许改写，任何一次「来源改口」都会抹掉「它曾经这么说过」这个事实。本系统的核心价值是并列与追溯，因此：

| 设计 | 效果 |
| --- | --- |
| `snapshots` / `visibility_events` / `artifacts` / `observations_log` 上的 8 个 `BEFORE UPDATE` / `BEFORE DELETE` 触发器 | 即使在事务内也直接 `RAISE(ABORT)`，映射为 `LedgerAppendOnlyError` |
| 内容寻址 `evidence_id` | 相同内容复用同一 ID，不同内容必然产生新 ID，不会「覆盖」 |
| 每源最新由 `seq` 而非时间戳判定 | 同一秒内的两次构建也能确定先后 |
| 当前态由事件推导而非存储单行 | 历史事件完整保留，当前态可随时重算 |

代价：同一来源会出现多条 `CURRENTLY_VISIBLE` 事件。这是**正确**的历史记录，不是冗余；界面用「原始事件序列」+「推导的当前生效」两层同时呈现（见 `DATA_CONTRACT.md` 第 4 节）。

## 为什么没有最终真相字段

真实世界里，不同公开来源对同一商品/成分/企业的记录会互相矛盾（例如许可状态在一处有效、在另一处已注销）。如果系统挑一个「最可信」的写进单一字段，它就**做出了判断**，而这正是本系统禁止的。

因此：

- 每个展示字段都来自某一条具体快照，并携带该快照的 `source_id` / `evidence_id` / `retrieved_at` / `raw_value`；
- 冲突不消解，全部进 `observations[]`；
- 字段区块只展示每源**最新**值，历史值仍可在区块 14/15/16 找到；
- 门禁 1 显式禁止 `final_truth` / `final_value` / `verdict` / `conclusion` 等键出现在任何分片。

## 为什么没有自动合并

合并需要判断「这两条记录是不是同一个东西」，而判断依据只可能是名称、地址、人名的相似性 —— 这正是关系推断。本系统只接受一种关联依据：**权威标识符精确相等**。

| 情形 | 处理 |
| --- | --- |
| 同 GTIN，多来源 | 候选并列，各自 `EXACT_IDENTIFIER_MATCH` |
| 同 GTIN，同来源多条记录（如 250mL 与 1L 共用条码） | 逐条并列，`record_id` 独立，共享同一 `evidence_id` |
| 同品牌不同 SKU（400g / 800g） | 不同 `entity_id`，不同分片，互不引用 |
| 名称相似但条码不同 | 不建立任何关联 |
| 许可号 / 标准号 / 商标名 | 永远 `MANUAL_CONFIRM`，只并列 |

`candidate_id` 由 `source_id|record_id|payload_sha256` 哈希决定，因此稳定、可去重、不会因构建顺序变化。

## 端侧架构

### 数据获取优先级

```
lookupGtin(gtin)
  ├─ GET ./data/v1/gtin/{gtin}.json  ── 成功 → origin = STATIC_LIBRARY
  └─ 失败 → loadDemoIndex()
        ├─ GET ./data/v1/demo/index.json ── 成功 → origin = DEMO_FALLBACK
        └─ 失败 → 打包在单文件里的 DEMO_FALLBACK 常量 → origin = DEMO_FALLBACK
```

`fetchJson` 使用 `method: "GET"`、`credentials: "omit"`、`cache: "force-cache"`、`referrerPolicy: "no-referrer"`，任何异常都返回 `null`，由上层走兜底。界面用 `origin` 明确标注数据来源。

### 机械对照

`engine/local_diet_matcher.ts`：

```
normalizeText = NFKC 归一化 → 去所有空白 → toLowerCase
haystack = [raw_name, ...search_keys] 全部归一化
needle   = [term, ...aliases] 全部归一化
命中条件 = haystack 中任一项包含 needle
```

只做子串包含，不做分词、不做同义扩展、不做否定判断。输出固定附带 `MATCH_DISCLAIMER`：

```
本提示仅作配料词条机械对照，不构成医疗、营养或购买建议，请以商品实物标签为准。
```

### 两份配料来源的并列对照（06 区块）

配料有两个来源，**各自独立匹配、互不合并**：

| 来源 | 输入 | 匹配入口 | 证据绑定 |
| --- | --- | --- | --- |
| A 条码静态档案 | `bundle.ingredients.ingredients` | `matchIngredients()` | 每条命中带 `source_id` / `evidence_id` / `retrieved_at` |
| B OCR / 手动修正文本 | `ocrText`（textarea） | `matchOcrText()` | 无（端侧文本不进静态档案，只在本次会话内存） |

06 区块**始终同时渲染两栏**，顶部用 `SourceFocusSwitch`（原生 radio + 文字 label，选中态只有墨青下划线，`border-radius: 0`、`box-shadow: none`）标注「当前对照来源」并支持一键切换：

- 切换只改变**主视图焦点**（`data-compare-focus` + 焦点栏左侧墨青竖线），不隐藏、不卸载任何一栏；
- 两栏命中行各自带 `data-hit-source`，归属不得串栏；
- 绝不把两份配料拼成一个列表——那等于制造「最终结论」。

状态只有一个 React state（`compareFocus`，默认 `static_archive`），不写入 IndexedDB，刷新即复位。这份数据是展示偏好，不是用户隐私数据。

### 端侧识别

| 能力 | 实现 | 数据去向 |
| --- | --- | --- |
| 条码 | `zxing-wasm/reader`，WASM 二进制内联，`prepareZXingModule({ overrides: { wasmBinary } })` | 内存 |
| 文字 | `tesseract.js`，worker 脚本 / core / `chi_sim` 语言数据全部内联（core 拼进 worker 引导脚本） | 内存 → worker |
| 预处理 | Canvas 灰度 + 放大（最长边 1600px 上限）+ 对比拉伸 | 内存 |

`local_ocr.ts` 的三条实现约束（每条都对应一次真实故障，回退即 OCR 全废）：

| 约束 | 原因 |
| --- | --- |
| core 源码必须拼进 worker 引导脚本，让 `self.TesseractCore` 启动时已存在 | 浏览器禁止 worker 用 `importScripts` 读取 `blob:` URL；把 core 做成 blob URL 会抛 `NetworkError` 且被 catch 吞掉 |
| 引导脚本里拦截 `initialize` 任务，把 `{code,data}` 还原为 `code` 字符串 | tesseract.js 5.1.1 的 `initialize` 分支把 `lang.data`（traineddata 字节）当语言名拼接，报 `Failed loading language '31,139,8,8,...'` |
| 语言数据仍以 `{ code, data }` 字节传入 `createWorker`（`loadLanguage` 阶段保留字节） | 保住「零网络」：不让 worker 回退去 jsdelivr 拉取语言数据 |

图片只以 `Blob` 交给 worker，不落盘、不上传。`workerBlobURL: false`，worker 内部不再发起 fetch。

### WebGPU 织纹层

`webgpu/weave_texture.ts` 是唯一的 GPU 使用点，用途限于版面背景：

| 约束 | 实现 |
| --- | --- |
| 单 pass | 1 个顶点着色器 + 1 个片元着色器，1 个全屏三角形，1 个 uniform buffer |
| 无新资源 | WGSL 内联为模板字符串，不新增 `.wgsl` / wasm / 图片 |
| 无网络 | 不发起任何请求 |
| DPR 上限 | `MAX_DPR = 1.5` |
| 停帧条件 | `prefers-reduced-motion: reduce`、电池放电且 `< 20%`、标签页隐藏、滚出视口、`getCurrentTexture()` 校验失败、`device.lost` |
| 兜底 | 任一步失败返回 `{ mode: "css" }`，组件加 `.weave-fallback` 类走纯 CSS 细网格 |

试渲染一帧并用 `pushErrorScope("validation")` / `popErrorScope()` 校验：这在 `chrome-headless-shell` 里会失败（无合成器），从而正确降级 —— 门禁 8 的浏览器测试正是跑在这个环境里。

## 信任链

```
原始响应（HTTP body）
   │  canonical_json（sort_keys + 紧凑分隔符 + 不转义中文）
   ▼
payload_sha256  = SHA-256(bytes)
   │  + {source_id, source_key, adapter_version, payload_sha256}
   ▼
evidence_id     = "ev_" + SHA-256(前四项)[:20]
   │  写入 snapshots(append-only)，同时写 observations_log
   ▼
分片中的每条记录 ── source_id + evidence_id + retrieved_at + raw_value
   │  raw_value 即原始响应的完整规范化 JSON
   ▼
读者：把 raw_value 解析回结构 → 与账本 payload 比对（门禁 1）
      重算 SHA-256(payload) → 与 payload_sha256 比对（门禁 9）
```

任何一环被改动都会被发现：改 payload 则哈希不匹配；改 evidence_id 则失去内容寻址；改 raw_value 则与快照对不上；改分片文件则与账本 `artifacts` 表的 SHA-256 不符。

## 失败模式与处置

| 失败 | 抛出 | 处置 |
| --- | --- | --- |
| 源未登记 | `PolicyGateError` | 适配器拒绝构造 |
| 源未放行 | `PolicyBlocked`（`POLICY_BLOCKED`） | 构建序列跳过；覆盖清单记为对应 `access_mode` |
| 命中验证码/登录墙 | `CaptchaOrLoginRequired`（`CAPTCHA_OR_LOGIN_REQUIRED`） | 立即终止，不重试、不绕过 |
| 响应结构不符 | `SourceFormatChanged`（`SOURCE_FORMAT_CHANGED`） | 熔断该适配器实例，后续调用直接抛；覆盖清单记 `QUERY_FAILED` |
| 429 且重试耗尽 | `RateLimitedError`（`RATE_LIMITED_EXHAUSTED`） | 终止，不提升频率 |
| 传输异常（重试耗尽） | `AdapterError`（`TRANSPORT_ERROR`） | 该源本次无记录，其余源继续 |
| 零记录 | — | 不产生快照；覆盖状态 `QUERIED_NO_MATCH` |

所有适配器异常都在 `Builder.build()` 中被捕获并记入 `failures[f"{gtin}:{source_id}"]`，转换为该条码 `coverage_manifest` 的 `QUERY_FAILED` 条目 —— 缺口永远显式披露。
