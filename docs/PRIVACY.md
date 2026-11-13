# 隐私与端侧边界

本文精确说明：**什么留在浏览器**、**什么从服务器取**、**如何自己验证**。

## 一句话

个人相关的一切（忌口词条、饮食计划、扫码历史、照片、OCR 文本）只存在于浏览器本地（IndexedDB / 内存；策略上亦允许 localStorage 与 Cookie）；从服务器拿到的只有公开的只读 JSON 分片。**任何个人数据都不外发**：不上传、不写日志、不向任何第三方发送。读公开分片的请求显式 `credentials: "omit"`，因此 Cookie 不会被自动携带到静态托管商。

## 数据流向表

| 数据 | 产生位置 | 存放位置 | 是否出网 |
| --- | --- | --- | --- |
| 忌口词条（`diet_rules`） | 用户输入 / GB 7718-2025 预设 | IndexedDB `product-public-dossier-local` | 否 |
| 饮食计划与目标值（`diet_plan`） | 用户输入 | 同上 | 否 |
| 扫码历史（`scan_history`） | 条码查询 / 本地 OCR | 同上 | 否 |
| 拍摄的照片原图 | 相机 / 文件选择 | **仅内存 `Blob`**，不落盘 | 否 |
| OCR 识别文本 | `tesseract.js` worker | 内存；用户主动查询时才写入 `scan_history` | 否 |
| 条码识别结果 | `zxing-wasm` | 内存 | 否 |
| 机械对照命中结果 | `local_diet_matcher` | 内存（不持久化） | 否 |
| 按克重的营养折算中间值 | `nutrition_scaling`（纯函数） | 内存；用户点击「记录」后才写入 `diet_plan.logs` | 否 |
| 本地备份 JSON 文件 | 用户主动导出 | 浏览器下载目录（用户自选位置） | 否 |
| 导入的备份文件 | 用户主动选择 | `FileReader` 读入内存 → 校验 → 写 IndexedDB | 否 |
| 06 区块的来源焦点偏好（`compareFocus`） | 用户切换 | **仅 React state**，刷新即复位；不写 IndexedDB | 否 |
| 静态 JSON 分片 | 构建机 | `apps/web/dist/data/v1/` | **是**（同站 GET） |
| Cookie | 应用当前未写入任何 Cookie（`document.cookie.length = 0`） | 浏览器本地 Cookie 罐 | 否（`credentials: "omit"` 使读分片请求不携带 Cookie） |
| localStorage / sessionStorage | — | 个人数据**不使用**（历史约定；策略上允许，见「为什么允许 Cookie」） | 否 |

`apps/web/src` 全目录检索不到 `localStorage.setItem` / `sessionStorage` / `navigator.sendBeacon` / `new FormData` / `multipart/form-data`。门禁 8 的 `test_no_upload_endpoint_in_source` 会扫描这三个上传语义标记。

## IndexedDB 结构

数据库名：`product-public-dossier-local`，版本 `1`。由 `apps/web/src/local_store/db.ts` 用 `idb` 的 `openDB` 建立。

| Object store | keyPath | 索引 | 值类型 |
| --- | --- | --- | --- |
| `diet_rules` | `id` | `by-term`（`term`） | `DietRule` |
| `diet_plan` | `id`（恒为 `"default"`） | 无 | `DietPlan`（单条记录） |
| `scan_history` | `id` | `by-ts`（`ts`） | `ScanHistoryEntry` |

### 值结构

```ts
interface DietRule {
  id: string;          // "rule_" + uuid
  term: string;
  aliases: string[];
  createdAt: number;   // Date.now()
}

interface DietTargets {
  energyKj: number;    // 默认 8400（GB 28050 附录 A NRV）
  proteinG: number;    // 60
  fatG: number;        // 60（标准写作「≤60 g」；上限语义只在展示层，存数字）
  carbohydrateG: number; // 300
  sodiumMg: number;    // 2000
}

interface DietLogEntry {
  id: string;
  label: string;
  energyKj: number; proteinG: number; fatG: number;
  carbohydrateG: number; sodiumMg: number;
  createdAt: number;
  origin: string;      // 手动录入 或 商品营养原文
  // 以下字段可选：旧版本记录（无克重折算）没有它们
  grams?: number;      // 本次输入的克重，例如 50
  entity_id?: string;  // 折算依据档案，例如 "ent_0fdf238ca1c25b41"
  gtin?: string;       // 折算依据条码
  nutrition_basis?: string; // "PER_100G" / "PER_100ML" / "NONE"
  evidence?: DietLogEvidenceRef[]; // 折算所依据的营养原文证据绑定
}

interface DietLogEvidenceRef {
  source_id?: string;
  source_name?: string;   // 记录时快照的可读来源名
  evidence_id?: string;
  retrieved_at?: string;
  payload_sha256?: string; // 营养条目本身不带哈希，按 evidence_id 从观测索引补齐
}

interface DietPlan {
  id: "default";
  targets: DietTargets;
  logs: DietLogEntry[];
  updatedAt: number;
}

interface ScanHistoryEntry {
  id: string;          // "scan_" + uuid
  ocrText: string;
  gtin: string;
  ts: number;
  origin: string;      // STATIC_LIBRARY / DEMO_FALLBACK / LOCAL_OCR
}
```

`diet_plan` 使用固定主键 `"default"`，因此整个计划只有一条记录，`savePlan()` 用 `put` 覆盖它 —— 这是本系统唯一「覆盖写」的本地数据，因为它属于用户自己的可编辑草稿，不是来源证据。

### 按克重折算（`engine/nutrition_scaling.ts`）

记录食物时输入克重（例如 `50`），折算严格按该商品营养成分表的「每 100 克」原文数值做**纯算术**：

```
每一项 = 原文数值 × 克重 / 100            （能量 kJ、蛋白质 g、脂肪 g、碳水 g、钠 mg）
```

三条自我限制（都会在界面上如实说明，绝不静默补 0）：

| 情况 | 行为 |
| --- | --- |
| 计量基准是「每 100 毫升」或与克混用 | 拒绝折算，说明「本次记录未计入数值」，且「记录」按钮置灰 |
| 同一营养素在该档案出现多条不一致原文 | 该项按并列处理不折算，列出并列项名称 |
| 营养表缺少某项（如该档案无「钠」） | 只计入存在项，列出缺失项名称 |

`diet_plan` 与 `diet_rules` 都不存营养原文副本：记录里只保存 `entity_id` / `gtin` / `nutrition_basis` 与 `evidence[]` 引用，数值来源始终回到公开只读分片。界面用 `SourceNotes` + `TraceDisclosure` 展示折算用的是哪份营养数据（来源名、取得日期、`evidence_id`、`payload_sha256`）。

### 本地备份（`local_store/backup.ts`）

导出与导入全程在浏览器本地完成，**不发起任何网络请求**：

| 环节 | 实现 | 出网 |
| --- | --- | --- |
| 导出 | `exportLocalData()` 组装 JSON → 页面 `new Blob([...])` → `URL.createObjectURL` → `a[download]` 触发下载 | 否 |
| 导入 | `input[type=file]` → `FileReader.readAsText()` → `validateBackup()` → `applyBackup()` | 否 |

备份文件的顶层字段是校验的第一道断言：

```json
{
  "schema_version": "1",
  "app": "eatwhat-local-backup",
  "exported_at": "2026-11-12T07:31:00.000Z",
  "diet_rules": [],
  "diet_plan": {},
  "scan_history": []
}
```

导入的顺序是先**完整解析并校验全部字段类型**，通过后才在单个 IndexedDB 事务里写入；任何一步不通过，一个字节都不写，现有数据保持不变。写入方式由用户在界面上显式选择：

| 方式 | 行为 |
| --- | --- |
| 合并（默认） | 按 `id` 用 `put` 并入；同 id 以备份内容为准，本地其它条目保留 |
| 覆盖 | 先 `clear()` `diet_rules` 与 `scan_history`，再写入备份内容 |

`diet_plan` 恒为单条记录，两种方式都以备份内容为准。上限（`BACKUP_MAX_*`）：单文件 8 MB、词条 2000 条、饮食记录 5000 条、扫码历史 5000 条。

<!-- 分段 -->
### 容量与清理

| API | 行为 |
| --- | --- |
| `listRules()` | 按 `createdAt` 升序 |
| `addRule(term, aliases)` | 去空白后 `put` |
| `deleteRule(id)` / `clearRules()` | 单项 / 全清 |
| `loadPlan()` | 无记录时返回 `emptyPlan()`（默认目标值 + 空 logs） |
| `savePlan(plan)` | 强制 `id = "default"` 并更新 `updatedAt` |
| `listScanHistory(limit = 50)` | 按 `ts` 升序取末尾 `limit` 条后倒序 |
| `clearScanHistory()` | 全清 |
| `exportLocalData()` | 组装 JSON 字符串（含 `schema_version` / `app` 标记），由页面生成本地文件下载 —— **不经网络** |
| `validateBackup(text)` | 纯函数：只解析与校验，不触碰 IndexedDB；返回 `{ ok, payload }` 或 `{ ok: false, issues[] }` |
| `applyBackup(payload, mode)` | 单个事务写入三个 store；`mode` 为 `merge` 或 `replace` |
| `readFileAsText(file)` | `FileReader` 读用户选中的文件；超过 8 MB 直接拒绝 |
| `localDataSummary()` | 仅返回三个计数（`rules` / `logs` / `history`），供页眉审计标识使用 |

浏览器清空站点数据（Clear site data）即彻底删除，没有服务端副本可以恢复。

## Cookie 策略：允许本地持久化，禁止一切外发

**策略现状（2026-11-13 起）**：Cookie 与 localStorage 一样，属于浏览器本地存储，**允许**用来保存个人数据。本应用当前仍未写入任何 Cookie，但这只是现状，不是隐私要求 —— 真正的红线是「个人数据不出浏览器」。

**真正重要的那条防线**：

1. **架构上不需要会话**。没有登录、没有个性化分流、没有后端。每次请求都是匿名的只读 GET，因此没有任何理由让浏览器自动带上 Cookie。
2. **请求参数显式关闭**。`services/static_dossier_client.ts` 的 `fetchJson()`、`ocr/local_ocr.ts` 与 `ocr/barcode.ts` 的三处 `fetch` 全部写死 `credentials: "omit"`：

```ts
fetch(url, {
  method: "GET",
  credentials: "omit",
  cache: "force-cache",
  referrerPolicy: "no-referrer",
})
```

`credentials: "omit"` 让请求**即使本地存在 Cookie 也不会携带**。这正是「放开本地存储」之后仍然安全的唯一理由：静态托管商收到的请求头里没有 `Cookie`，也没有 `Referer`。全仓只允许 `"omit"`，改成 `"same-origin"` / `"include"` 视为违规（门禁 8 的 `test_private_data_requests_carry_no_cookie_header` 与源码级断言双重守门）。
3. **HTML 层面收紧 Referer**。`apps/web/index.html` 声明 `<meta name="referrer" content="no-referrer" />`。
4. **不得引入统计 / 追踪 / 广告类 Cookie**。任何第三方 Cookie、`_ga` 之类的标识、埋点脚本一律禁止。

页脚的 `cookie.length` 运行状态读数每 2 秒重读一次 `document.cookie.length`。它现在**只是现状快照**（当前为 `0`），不代表隐私口径；隐私口径以「请求头无 Cookie」为准。页眉的审计标识已改为中性的「本地存储 · 不外发」。

## OCR 路径

### 引擎

| 环节 | 库 | 数据注入方式 |
| --- | --- | --- |
| 条码 | `zxing-wasm/reader` | `prepareZXingModule({ overrides: { wasmBinary: bytes, locateFile } })`，WASM 字节来自打包内联 |
| 文字 | `tesseract.js` | core 源码拼进 worker 引导脚本（`self.TesseractCore` 预先定义）；语言数据以 `createWorker([{ code: "chi_sim", data: bytes }], 1, { workerPath: 引导脚本, workerBlobURL: false, gzip: true })` 直传 |
| 预处理 | 原生 Canvas | 灰度（0.299/0.587/0.114）+ 对比拉伸 `(gray - 96) * 1.65` + 最长边缩放到 1600px |

### 图片去向

```
用户选择/拍摄 → Blob（内存）
   → preprocess(): createImageBitmap → Canvas → toBlob("image/png")
   → worker.recognize(preparedBlob)
   → 文本结果返回主线程
```

`preprocess()` 的中间产物是 Canvas 与 `Blob`，都在内存。`local_ocr.ts` 从未调用 `URL.createObjectURL` 保存原图到可下载地址，也从未 `fetch` 上传。`disposeOcr()` 会 terminate worker 并 revoke 内部 blob URL。

### 打包方式差异（重要）

| 构建模式 | 命令 | 语言数据形态 | 网络请求 |
| --- | --- | --- | --- |
| 单文件（默认） | `npm run build:web` | `@tesseract.js-data/chi_sim/4.0.0_best_int/chi_sim.traineddata.gz?url` 被 Vite 内联为 `data:application/gzip;base64,…` | 读取 data URL 不是网络请求 |
| 静态目录 | `PPD_SPLIT=1 npm run build -w apps/web` | 同上但 `assetsInlineLimit` 降为 4096，`.gz` 成为 `dist/assets/*.gz` 相对路径 | 同站 GET 一次 `chi_sim.traineddata.gz` |

两种模式都**没有跨站请求**。区别只在：单文件模式下语言数据在 HTML 内部（因此 `index.html` 约 7.6 MB），静态目录模式从同站加载一个独立文件（HTML 更小，可分别缓存）。

`vite.config.ts` 中的判定：`const singleFile = process.env.PPD_SPLIT !== "1"`，`assetsInlineLimit: singleFile ? 100000000 : 4096`。

### 实证

单文件产物 `apps/web/dist/index.html` 中可见的内联资源：

| 类型 | 形态 |
| --- | --- |
| OCR core | `tesseract-core-simd-lstm.wasm.js` 以 `?raw` 读入，**拼接进 worker 引导脚本**（不是 `blob:` URL） |
| OCR worker | `tesseract.js/dist/worker.min.js` 以 `?raw` 读入，与 core 同处一个引导 `blob:` URL |
| 语言数据 | `data:application/gzip;base64,…`（约 1.6 MB gzip 源） |
| 条码 WASM | `data:application/wasm;base64,…` |

`workerBlobURL: false` 明确禁止 tesseract 自己再生成 blob URL 去 fetch。

### 为什么 core 不能做成 `blob:` URL

浏览器禁止 worker 通过 `importScripts()` 读取 `blob:` URL。tesseract.js 的 worker 内部只用 `importScripts()` 加载 core，因此把 core 做成 blob URL 必然抛 `NetworkError`。该异常会被 `try/catch` 吞掉，表现为「页面正常、零请求、但 OCR 永远出不了字」，很容易被误判为可用。正确做法是把 core 源码拼进同一个 worker 引导脚本，让 `self.TesseractCore` 在 worker 启动时就已存在。

此外 tesseract.js 5.1.1 的 `initialize` 分支会把 `lang.data`（traineddata 字节）当成语言名拼接，报 `Failed loading language '31,139,8,8,...'`。引导脚本里因此拦截 `initialize` 任务，把 `{code,data}` 还原成 `code` 字符串；`loadLanguage` 阶段仍保留字节，保证不回退到 jsdelivr 拉取。

两条约束都由 `tests/acceptance_gates/test_client_ocr.py` 守门（真实浏览器、真实图片、真实识别）。

### 已知的第三方默认行为

`zxing-wasm` 的 UMD 包内含一个默认 `locateFile`，会拼出 `https://fastly.jsdelivr.net/npm/zxing-wasm@…/dist/…` 的 URL。本项目的 `ocr/barcode.ts` **用 `overrides.locateFile` 覆盖了它**，因此运行时不会走该 CDN 分支。这一覆盖是隐私保障的一部分，修改该文件时必须保留。

## 「同站」的准确含义

前端只向**自身所在源**发请求：

| 部署方式 | 请求目标 | 说明 |
| --- | --- | --- |
| `python3 -m http.server --directory apps/web/dist` | `http://127.0.0.1:PORT/data/v1/…` | 同站（同 scheme + host + port） |
| GitHub Pages | `https://avrovadonz2026.github.io/EatWhat/data/v1/…` | 同站 |
| 双击 `index.html`（`file://`） | `fetch` 被浏览器拦截 | 全部失败 → 走内联 `DEMO_FALLBACK` 常量 |

`DATA_BASE` 默认为 `"./data"`，可用 `VITE_DATA_BASE` 覆盖（构建期变量）。

## 如何自己验证

### 1. 界面的审计标识

页眉固定显示两个 chip：

```
本地存储 · 不外发
IndexedDB  N 词条 / M 记录
```

页脚运行状态区另有 `cookie.length=N`（当前为 `0`），由 `setInterval(2000)` 持续重读，是现状快照而非隐私口径。门禁 8 的 `test_ui_shows_local_only_no_egress_badge` 断言页眉的中性标识可见，`test_request_headers_carry_no_cookie` 断言请求头无 Cookie。

### 2. DevTools Network 面板

1. 打开页面，清空 Network，勾选 Preserve log。
2. 走一遍：输入条码查询 → 打开档案 → 添加忌口词条 → 上传一张标签照片做 OCR。
3. 观察请求列表：应只有对**当前站点**的 `GET`（`.html`、`.js`、`.css`、`data/v1/*.json`，静态目录模式下再加 `.gz`）。
4. 筛选 `XHR`/`Fetch` 看有没有非本站域名 —— 应为空。
5. 检查任一请求的 Request Headers：**不应有 `Cookie`**（即使本地存在 Cookie）；不应有多余 `Referer`。

门禁 8 用 Playwright 自动做同一件事：`context.on("request", …)` 收集全部请求，断言非 `data:`/`blob:` 且 hostname 不在本机的记录为空，且方法集合 ⊆ `{GET, HEAD}`；并对每条请求取 `request.all_headers()` 断言没有 `cookie` 头。

### 3. Application 面板

| 位置 | 期望 |
| --- | --- |
| Storage → Cookies | 当前为空（应用未写 Cookie）；若将来写入，属允许的本地持久化，但仍不得出网 |
| Storage → Local Storage | 空（个人数据当前仍只写 IndexedDB） |
| Storage → Session Storage | 空 |
| Storage → IndexedDB → `product-public-dossier-local` | 有 `diet_rules` / `diet_plan` / `scan_history` |

门禁 8 的 `test_diet_terms_stay_in_indexeddb` 会断言：添加词条后 `localStorage` / `sessionStorage` 的序列化结果里**不包含**该词条文本，同时 IndexedDB 的 `diet_rules` 里**能查到**它。词条不得出现在 `document.cookie` 里的检查改为「若 Cookie 存在，其内容不得包含该词条」——因为 Cookie 本身已不再是违规。

### 4. 重载持久性

关闭页签再打开（或 F5），词条仍在 → 证明持久化只发生在浏览器端。门禁 8 的 `test_persistence_after_reload` 覆盖这一点。

### 5. 离线验证

断网后：已加载的页面仍可操作（词条、计划、机械对照全在本地）；需要新档案查询时会取不到，落到 `DEMO_FALLBACK` 或提示未命中 —— 不会出现「等待上报」之类的行为。

### 6. 源码检索

```bash
rg -n "cookie|sendBeacon|localStorage|sessionStorage|FormData|multipart" apps/web/src
```

预期：只匹配到注释、`document.cookie.length` 读取，以及三处 `fetch` 的 `credentials: "omit"`。**不应**匹配到任何 `localStorage.setItem` / `document.cookie =` 写入。

### 7. 两份配料来源的边界

06 区块同时显示两个来源的机械命中，它们的数据生命期完全不同：

| 来源 | 数据出处 | 生命期 | 是否会写盘 |
| --- | --- | --- | --- |
| 条码静态档案配料 | `v1/ingredients/{entity_id}.json`（公开只读分片） | 公开数据，随页面加载 | 服务器上本来就有 |
| OCR / 手动修正文本 | `textarea`（端侧识别结果或用户粘贴） | 仅本次会话内存；显式扫码/识别时才落 `scan_history` | 只进 IndexedDB，从不外发 |

两栏是**并列呈递**，不是合并：页面不生成「合并后的配料列表」，也不给任何单一结论。切换控件只改变阅读焦点（React state），不写入任何存储，因此不会额外产生可追踪的本地痕迹。

门禁 `test_demo_origin_and_compare_focus.py` 断言：两栏始终同时可见、每栏命中行只属于自己、没有任何命中行落在栏外（那正是「合并成一个列表」的形态）。

### 8. 备份文件的去向

导出与导入都只在本机发生，可用 DevTools Network 面板逐步核对：

1. 清空 Network 并勾选 Preserve log。
2. 点「导出本地备份」→ 观察：请求列表**不增加任何条目**（下载由 `URL.createObjectURL(blob)` 触发，不是 HTTP 请求）。
3. 点「导入本地备份」并选择一个 JSON 文件 → 观察：同样不增加任何请求；文件只在 `FileReader` 中读取。
4. 筛选 XHR/Fetch → 应为空。

门禁 `test_diet_scaling_and_backup.py` 用 Playwright 自动做同一件事：导出 + 导入前后比对 `context.on("request")` 收集到的请求条数，要求增量为 0，且方法集合 ⊆ `{GET, HEAD}`。

## 边界与限制（如实记录）

| 项 | 现实情况 |
| --- | --- |
| 浏览器自身遥测 | 由浏览器厂商决定，本项目无法控制；本项目自身不发任何上报 |
| 静态托管方的访问日志 | GitHub Pages / 任意 CDN 会记录 HTTP 访问日志（IP、UA、路径）。本项目请求头不带 Cookie 与 Referer，但**托管方层面的日志不可避免** |
| Service Worker | 本项目**未**注册 Service Worker，因此没有后台缓存或后台请求 |
| 多标签页 | IndexedDB 升级时若被其他标签页阻塞，会打印 `IndexedDB 升级被其他标签页阻塞` 警告；`terminated()` 时重置连接 |
| 数据导出 | `exportLocalData()` 生成本地 JSON 并触发文件下载，这是唯一的「数据离开浏览器」路径，且完全由用户主动触发 |
| 备份导入的信任边界 | 导入文件来自用户自选位置。校验只保证「结构与字段类型匹配」，不保证内容对用户有意义（例如备份里的饮食记录数值可以被手工改大）。系统不对此做任何推断或修正 |
| 备份不加密 | 导出的 JSON 是明文。若用户把文件放到同步盘或分享出去，保护责任在该文件的存储位置，不在本应用 |
| 备份的版本兼容 | 只接受 `schema_version` 与当前值（`"1"`）完全相等。旧版或未来版备份必须显式拒绝，不做静默转换 |

## 历史策略变更（如实记录）

| 日期 | 变更 | 修复前 | 修复后 |
| --- | --- | --- | --- |
| 2026-11-13 | 第 4 条硬红线的 Cookie 口径放开 | 「端侧全私有、**零 Cookie**」：`AGENTS.md` 写「不得使用 HTTP Cookie」；`docs/PRIVACY.md` 写「没有 Cookie」、表格写 Cookie「**从不写入**」、并有整节「为什么没有 Cookie」 | 「端侧全私有、**禁一切外发**」：个人数据只存浏览器本地（IndexedDB / localStorage / Cookie 均可），**不上传、不写日志、不向任何第三方发送**；读公开分片的请求必须保留 `credentials: "omit"`；不得引入统计 / 追踪 / 广告类 Cookie |

**本次变更的性质**：由仓库所有者决定放开本地存储形态的限制，**不是**隐私边界的松动。外发禁令不变，且被更强的断言守住。

**门禁口径同步改造**（旧断言「不写 Cookie」→ 新断言「Cookie 不出网」）：

| 旧断言 | 新断言 | 处理方式 |
| --- | --- | --- |
| `page.context.cookies() == []` | `test_private_data_requests_carry_no_cookie_header`：逐条请求的 `all_headers()` 不含 `cookie` | **换成等价但方向正确的断言**（原断言只能证明「没写」，新断言证明「没发」） |
| `document.cookie.length == 0` | 保留为 `test_no_cookie_currently_written` 的**弱断言**，文档内注明「当前未使用 Cookie，仅现状快照，不是隐私要求」 | **保留但降级并改语义注释**，不再作为隐私门禁 |
| `test_ui_shows_zero_cookie_badge` 断言 `cookie.length=0` | 改名为 `test_ui_shows_local_only_no_egress_badge`，断言页眉「本地存储 · 不外发」中性文案 | **改名 + 改断言**，不留名实不符的测试 |
| `test_client_ocr.py` / `test_diet_scaling_and_backup.py` 的 `document.cookie.length == 0` | 改为「全程请求头不含 Cookie」 | **换成更强的等价断言** |

**实测结果**：改造前，`page.context.cookies() == []` 在应用当前行为下仍成立（应用确实不写 Cookie），也就是说旧断言在「允许 Cookie」之后**仍然通过但已失去意义**——它只能证明「没写」，完全抓不到「写了但发出去」这种真正的违规。因此本轮不是「让测试重新变绿」，而是把断言从错误的方向掰回正确方向。

反向对照实测：在页面内注入 `document.cookie = 'probe=1; path=/'` 后用 `credentials: "include"` 发同源请求，服务端实际收到 `Cookie: probe=1`，Playwright `request.all_headers()` 命中 `cookie`，新断言报出违规（详见 `docs/ACCEPTANCE_GATES.md` 门禁 8 段落）。
