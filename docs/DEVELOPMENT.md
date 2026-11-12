# 开发指南

## 环境

| 依赖 | 版本要求 | 说明 |
| --- | --- | --- |
| Node.js | 20.x（CI 锁定 20） | npm workspaces，只有一个 workspace `apps/web` |
| Python | 3.9+（仓库当前 `.venv` 为 3.9.6，CI 为 3.12） | 构建管线 + 门禁 |
| Chromium | 由 Playwright 安装 | 仅门禁 8 需要 |

仓库使用 **`.venv` 工作流**：Python 侧命令一律走 `.venv/bin/python`（`package.json` 的 `test` 脚本已经这样写死）。若你不想用 venv，把 `npm test` 换成 `python3 -m pytest tests/acceptance_gates -q`，但需自行保证 `pytest` / `playwright` 装在当前解释器里。

## 安装

```bash
# 1) Node 依赖（会一并安装 apps/web）
npm install

# 2) Python 依赖（仅测试需要；构建管线只用标准库）
python3 -m venv .venv
.venv/bin/pip install pytest playwright
.venv/bin/python -m playwright install chromium

# 3) 校验注册表可加载
python3 -c "from pipeline.config.policy import load_registry; r=load_registry(); print(r.registry_version, len(r))"
```

构建管线（`pipeline/`）只用标准库（`sqlite3`、`hashlib`、`json`、`argparse`、`pathlib`、`urllib.parse`），**不需要**安装任何 PyPI 包。`pytest` 只被验收门禁需要。

## 开发服务器

```bash
npm run dev            # = npm run dev -w apps/web → vite，端口 5173
```

`vite.config.ts` 的 `server.fs.strict = false` 允许 Vite 读取 workspace 之外的文件（`?raw` 导入 `tesseract.js` 包内文件需要）。

开发服务器**不会**自动拥有静态分片。要联调真实档案，先在另一个终端：

```bash
python3 -m pipeline.build
npm run sync:data
```

`apps/web/dist` 是构建产物目录，`npm run build:web` 会先 `vite build`（`emptyOutDir: true` 清空）再 `sync:data`，顺序不能颠倒。

## 构建

```bash
npm run build:data     # 编译静态分片 → dist_data/
npm run build:web      # 单文件 HTML + 同步分片 → apps/web/dist/
npm run gate           # 语义门禁
npm run build          # 三者串联
```

`npm run build:web` 内部：

```bash
npm run build -w apps/web    # tsc -b && vite build
node scripts/sync-data.mjs   # dist_data/ → apps/web/dist/data/
```

`apps/web` 的 `build` 脚本是 `tsc -b && vite build`，即**先类型检查再打包**。`tsconfig.json` 开了 `strict` / `noUnusedLocals` / `noUnusedParameters`，未使用的导入会直接构建失败。

## 测试

```bash
npm test                                                    # 全部门禁
.venv/bin/python -m pytest tests/acceptance_gates -q        # 同上
.venv/bin/python -m pytest tests/acceptance_gates -q -k privacy   # 只跑浏览器门禁
.venv/bin/python -m pytest tests/acceptance_gates --collect-only -q  # 列测试（当前 102 个）
```

## 浏览器门禁的沙箱注意事项

门禁 8 需要启动真实的 Chromium。在受限沙箱（含本仓库开发时使用的沙箱环境）里 Chromium 常因系统调用限制而崩溃（段错误或 `--no-sandbox` 之外的其他失败）。处理方式：

1. **优先在非沙箱环境运行**该模块，或单独跑：

```bash
.venv/bin/python -m pytest tests/acceptance_gates/test_privacy_and_zero_cookie.py \
                                       tests/acceptance_gates/test_client_ocr.py -q
```

2. 若 Chromium 无法启动，测试会**整体 skip** 而不是假通过 —— `pytest.importorskip("playwright.sync_api")` + `apps/web/dist/index.html` 存在性检查双重把关。若你看到 `skipped`，先确认 `dist/index.html` 已构建。

3. 浏览器启动统一走 `tests/acceptance_gates/_browser_launch.py` 的 `launch_chromium()`：优先使用**带合成器的完整 Chromium**（在 `PLAYWRIGHT_BROWSERS_PATH`、`~/Library/Caches/ms-playwright`、`~/.cache/ms-playwright` 里按 `chromium-*/...` 模式定位），失败才回退 headless shell，并对启动期 `SEGV_ACCERR` 做有限次重试（每档 3 次，指数退避）。这消除了「headless shell 启动崩溃 → 整组测试 setup ERROR」的环境抖动；实测连续 5 轮全绿。若你的浏览器装在其他位置：

```bash
export PLAYWRIGHT_BROWSERS_PATH=/your/path/ms-playwright
```

4. 平台相关：Linux 下路径模式是 `chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell` 与 `chromium-*/chrome-linux64/chrome`；macOS 另有 `chromium-*/chrome-mac-arm64/*.app/Contents/MacOS/*`。

## 单文件约束

`apps/web/dist/index.html` 必须是**自包含**的：所有 JS、CSS、WASM、语言数据、图片都在文件内部，双击即可运行。这由 `vite.config.ts` 的 `viteSingleFile({ removeViteModuleLoader: true })` + `assetsInlineLimit: 100000000` 实现。

### 加依赖时的检查清单

| 检查 | 为什么 |
| --- | --- |
| 是否引入**运行时网络请求**（CDN、字体、图标、远程 wasm） | 会被门禁 8 的 `test_no_outbound_network_requests` 拦下 |
| 是否把 WASM 作为独立文件输出 | 单文件下会变成 `dist/assets/*.wasm`，双击打开时 `fetch` 失败。需要 `?url` + 内联，或 `?raw` + `Blob` |
| 是否自带默认 `locateFile` 指向 CDN | `zxing-wasm` 就有此默认行为，必须用 `prepareZXingModule({ overrides: { locateFile } })` 覆盖 |
| 是否把 worker 脚本作为独立文件 | `tesseract.js` 需要 `workerPath` 指向 blob URL 且 `workerBlobURL: false` |
| 是否显著增大体积 | 当前约 7.6 MB（主要来自 OCR core + 1.6 MB gzip 语言数据） |
| 是否使用 `localStorage` | 个人数据禁止；只用 IndexedDB |
| 是否包含被禁词 | 门禁 10 会扫源码与打包产物 |
| 是否使用红/绿/黄语义色 | 门禁 10 的风险配色正则 |

### 已验证的内联手法（可复用）

```ts
// 1) WASM / JS 以字符串导入，运行时转 blob URL
import workerSource from "tesseract.js/dist/worker.min.js?raw";
const url = URL.createObjectURL(new Blob([workerSource], { type: "application/javascript" }));

// 2) WASM 以 URL 导入，打包时被内联为 data: URL，再读成字节
import zxingWasmUrl from "zxing-wasm/reader/zxing_reader.wasm?url";
const bytes = new Uint8Array(await (await fetch(zxingWasmUrl, { credentials: "omit" })).arrayBuffer());

// 3) 语言数据以 URL 导入，同样被内联
import chiSimUrl from "@tesseract.js-data/chi_sim/4.0.0_best_int/chi_sim.traineddata.gz?url";
```

关键点：`?url` 导入在 `assetsInlineLimit` 足够大时会变成 `data:` URL，`fetch(dataUrl)` 不是网络请求。

### 分离构建（对照用）

```bash
PPD_SPLIT=1 npm run build -w apps/web
```

此时 `viteSingleFile` 不启用，`assetsInlineLimit` 降到 4096，产物变成多文件 `dist/`。它**不满足**「双击即用」承诺，但可用于对比或调试打包问题。注意 `sync:data` 仍需执行。

## 常见故障

### `@tesseract.js-data` 安装失败

症状：`npm install` 时报某个 `@tesseract.js-data/<lang>` 的 postinstall 校验目录失败。

原因：该包在 install 阶段检查目标目录是否存在，某些 npm 版本/缓存状态下会误判。

处置：

```bash
npm install @tesseract.js-data/chi_sim@1.0.0
```

仓库当前只依赖 `chi_sim`（`package.json` 的 `dependencies` 与 `apps/web/src/ocr/local_ocr.ts` 的 import 都只涉及中文简体）。历史上曾错误地加入过 `eng` 包，它没有代码引用，删掉即可。

### Playwright 浏览器找不到 / 版本不匹配

症状：`playwright._impl._errors.Error: Executable doesn't exist at …`。

处置：

```bash
.venv/bin/python -m playwright install chromium
# 或
.venv/bin/python -m playwright install --with-deps chromium
```

若 Python 包与已装浏览器 build 号不一致，门禁 8 的自动定位会兜底；仍失败则显式设 `PLAYWRIGHT_BROWSERS_PATH`。

### `LedgerError: … 检测到疑似凭据字面值，拒绝落盘`

原因：某个字段值是 32+ 位十六进制串，且字段名不在 `_HASH_KEY_ALLOWLIST` 中。最常见的是新增了一个哈希字段但没登记。

处置：把字段名加入 `pipeline/builder/ledger.py` 的 `_HASH_KEY_ALLOWLIST`。**不要**为此放宽 `_HEX_LIKE_SECRET` 正则 —— 那会让真正的凭据也通过。

### 构建产出 `dist_data` 但页面还是旧数据

原因：`sync:data` 未执行，或执行顺序错了（`vite build` 会清空 `dist`，必须在其之后同步）。

```bash
npm run build:web
```

### 页面显示 `DEMO_FALLBACK`

正常行为，表示取不到静态分片。检查：

1. 是否用 `file://` 双击打开 —— 此时 `fetch` 被浏览器限制，必然兜底；
2. 是否用静态服务器打开但 `dist/data/v1/` 不存在 —— 跑 `npm run build:web`；
3. `VITE_DATA_BASE` 是否被设成了错误路径。

### 语义门禁在文档上失败

`docs/` 不在 `ALLOWLIST_FILES` 中。若你在文档里以叙述方式写入了被禁词（而不是在表格/代码里枚举），门禁会报错。见 `SEMANTIC_GATE.md` 的「白名单文件」一节。

### TypeScript 报未使用变量

`tsconfig.json` 开了 `noUnusedLocals` / `noUnusedParameters`。删掉未用的导入/参数，或用下划线前缀（`_event`）表达「有意未用」。

### `apps/web/dist/index.html` 体积异常增大

检查是否新增了以 `?raw` 导入的大文件（它们会整体进入 HTML），或把语言数据换成更大的版本。当前 `chi_sim.traineddata.gz` 约 1.6 MB。

## 目录职责速查

| 路径 | 修改时的注意事项 |
| --- | --- |
| `apps/web/src/pages/` | 文案受门禁 10 约束；状态只能用文字 + 边框表达 |
| `apps/web/src/services/static_dossier_client.ts` | 必须保持 `credentials: "omit"` 与只 READ |
| `apps/web/src/ocr/` | 必须保持零网络；`locateFile` 覆盖不能删；core 必须拼进 worker 引导脚本（禁止 `blob:` URL 交给 `importScripts`）；`initialize` 语言的 `{code,data}` 还原补丁不能删 |
| `apps/web/src/local_store/` | 禁止新增 `localStorage` 用法 |
| `apps/web/src/webgpu/` | 必须保持单 pass、无新资源、失败即 CSS 兜底 |
| `pipeline/builder/ledger.py` | 不得删除触发器；改 schema 需同步 `SCHEMA_VERSION` |
| `pipeline/builder/dossier.py` | 新增区块要同步 `BLOCK_IDS`、门禁 3 的 16 区块断言 |
| `pipeline/adapters/` | 改解析逻辑必须提升 `adapter_version` |
| `packages/semantic_gate/rules.py` | 新增规则要同步对抗语料与类别集合断言 |
| `tests/acceptance_gates/` | 不要为了让测试通过而放宽断言 |
