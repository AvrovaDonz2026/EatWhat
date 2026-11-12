# 隐私与端侧边界

本文精确说明：**什么留在浏览器**、**什么从服务器取**、**如何自己验证**。

## 一句话

个人相关的一切（忌口词条、饮食计划、扫码历史、照片、OCR 文本）只存在于浏览器 IndexedDB 或内存；从服务器拿到的只有公开的只读 JSON 分片。没有 Cookie、没有上报、没有日志。

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
| 静态 JSON 分片 | 构建机 | `apps/web/dist/data/v1/` | **是**（同站 GET） |
| Cookie | — | **从不写入** | — |
| localStorage / sessionStorage | — | 个人数据**不使用** | — |

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
  energyKj: number;    // 默认 8400
  proteinG: number;    // 60
  fatG: number;        // 65
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
| `exportLocalData()` | 组装 JSON 字符串，由页面生成本地文件下载 —— **不经网络** |
| `localDataSummary()` | 仅返回三个计数（`rules` / `logs` / `history`），供页眉审计标识使用 |

浏览器清空站点数据（Clear site data）即彻底删除，没有服务端副本可以恢复。

## 为什么没有 Cookie

三层原因：

1. **架构上不需要**。没有会话、没有登录、没有个性化分流。每次请求都是匿名只读 GET。
2. **请求参数显式关闭**。`services/static_dossier_client.ts` 的 `fetchJson()`：

```ts
fetch(url, {
  method: "GET",
  credentials: "omit",
  cache: "force-cache",
  referrerPolicy: "no-referrer",
})
```

`credentials: "omit"` 让浏览器既不发也不存 Cookie；`referrerPolicy: "no-referrer"` 让请求不带 Referer，避免把页面 URL 泄露给 CDN。

3. **HTML 层面限制**。`apps/web/index.html` 声明 `<meta name="referrer" content="no-referrer" />`，且没有任何 `document.cookie` 写入代码。

界面的 `cookie.length` 审计标识每 2 秒重读一次 `document.cookie.length`（`App.tsx` 的 `setInterval`），恒为 `0`。

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

### 1. 界面的 Cookie 审计标识

页眉固定显示两个 chip：

```
cookie.length=0
IndexedDB  N 词条 / M 记录
```

`cookie.length` 由 `setInterval(2000)` 持续重读。门禁 8 的 `test_ui_shows_zero_cookie_badge` 断言该文本可见且含 `cookie.length=0`。

### 2. DevTools Network 面板

1. 打开页面，清空 Network，勾选 Preserve log。
2. 走一遍：输入条码查询 → 打开档案 → 添加忌口词条 → 上传一张标签照片做 OCR。
3. 观察请求列表：应只有对**当前站点**的 `GET`（`.html`、`.js`、`.css`、`data/v1/*.json`，静态目录模式下再加 `.gz`）。
4. 筛选 `XHR`/`Fetch` 看有没有非本站域名 —— 应为空。
5. 检查任一请求的 Request Headers：不应有 `Cookie`；不应有多余 `Referer`。

门禁 8 用 Playwright 自动做同一件事：`context.on("request", …)` 收集全部请求，断言非 `data:`/`blob:` 且 hostname 不在本机的记录为空，且方法集合 ⊆ `{GET, HEAD}`。

### 3. Application 面板

| 位置 | 期望 |
| --- | --- |
| Storage → Cookies | 空 |
| Storage → Local Storage | 空（个人数据不写这里） |
| Storage → Session Storage | 空 |
| Storage → IndexedDB → `product-public-dossier-local` | 有 `diet_rules` / `diet_plan` / `scan_history` |

门禁 8 的 `test_diet_terms_stay_in_indexeddb` 会断言：添加词条后 `localStorage` / `sessionStorage` / `document.cookie` 的序列化结果里**不包含**该词条文本，同时 IndexedDB 的 `diet_rules` 里**能查到**它。

### 4. 重载持久性

关闭页签再打开（或 F5），词条仍在 → 证明持久化只发生在浏览器端。门禁 8 的 `test_persistence_after_reload` 覆盖这一点。

### 5. 离线验证

断网后：已加载的页面仍可操作（词条、计划、机械对照全在本地）；需要新档案查询时会取不到，落到 `DEMO_FALLBACK` 或提示未命中 —— 不会出现「等待上报」之类的行为。

### 6. 源码检索

```bash
rg -n "cookie|sendBeacon|localStorage|sessionStorage|FormData|multipart" apps/web/src
```

预期：只匹配到注释、`document.cookie.length` 读取、以及 `fetchJson` 里的 `credentials: "omit"`。

## 边界与限制（如实记录）

| 项 | 现实情况 |
| --- | --- |
| 浏览器自身遥测 | 由浏览器厂商决定，本项目无法控制；本项目自身不发任何上报 |
| 静态托管方的访问日志 | GitHub Pages / 任意 CDN 会记录 HTTP 访问日志（IP、UA、路径）。本项目不发 Cookie，但**托管方层面的日志不可避免** |
| Service Worker | 本项目**未**注册 Service Worker，因此没有后台缓存或后台请求 |
| 多标签页 | IndexedDB 升级时若被其他标签页阻塞，会打印 `IndexedDB 升级被其他标签页阻塞` 警告；`terminated()` 时重置连接 |
| 数据导出 | `exportLocalData()` 生成本地 JSON 并触发文件下载，这是唯一的「数据离开浏览器」路径，且完全由用户主动触发 |
