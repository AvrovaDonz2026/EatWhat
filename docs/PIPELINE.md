# 构建管线操作手册

本文是**构建机操作者**的指南：怎么跑、参数含义、账本行为、如何新增数据源、故障如何恢复。

## 前置

- Python 3.9+（仓库当前以 `.venv` 工作流运行，`python3 -m pipeline.build` 与 `.venv/bin/python -m pytest` 都在用）
- Node（用于构建前端与同步分片）
- 无需网络：默认通道是离线夹具 `LocalFixtureTransport`，线上通道 `StaticHttpTransport` 默认禁用且调用即抛错

## 命令一览

```bash
# 只用夹具编译静态分片到 dist_data/
python3 -m pipeline.build

# 清空 dist_data 后重编（账本不动，仍是只追加）
python3 -m pipeline.build --reset

# 追加第二版快照（模拟来源更新，历史保留）
python3 -m pipeline.build --rev2

# 只构建指定条码
python3 -m pipeline.build --gtins 6901234567892,6905554443331

# 指定账本位置（默认 .state/ledger.sqlite3）
python3 -m pipeline.build --ledger /path/to/ledger.sqlite3

# 指定夹具目录（默认 pipeline/fixtures/v1）
python3 -m pipeline.build --fixtures pipeline/fixtures/v1_rev2

# 自定义构建说明
python3 -m pipeline.build --note "baseline-2026-11"
```

通过 npm 脚本：

```bash
npm run build:data     # python3 -m pipeline.build
npm run build:web      # npm run build -w apps/web && sync-data
npm run sync:data      # 仅同步 dist_data → apps/web/dist/data
npm run gate           # 语义门禁
npm run build          # data → web → gate 全链路
npm test               # .venv/bin/python -m pytest tests/acceptance_gates -q
```

## CLI 参数（`pipeline/build.py`）

| 参数 | 默认值 | 行为 |
| --- | --- | --- |
| `--data-dir` | `dist_data`（仓库根） | 产物输出目录 |
| `--ledger` | `.state/ledger.sqlite3` | SQLite 账本路径，自动创建父目录 |
| `--fixtures` | `pipeline/fixtures/v1` | 夹具根目录 |
| `--gtins` | 空 → 取 `pipeline.fixtures_index.FIXTURE_GTINS` | 逗号分隔；非空则不查环境变量 |
| `--rev2` | 关闭 | 使用 `pipeline/fixtures/v1_rev2`，会**覆盖** `--fixtures` |
| `--note` | 空 | 写入 `build_runs.note`；空值时自动取 `rev2` 或 `baseline` |
| `--reset` | 关闭 | `shutil.rmtree(data_dir)`，**只清产物，不清账本** |
| `--reset-ledger` | 关闭 | `unlink(ledger)`，**丢弃全部历史**；仅用于从零重建演示基线 |

`--reset` 与 `--reset-ledger` 都可与其它参数组合。`--rev2` 的优先级高于 `--fixtures`（代码里 `if args.rev2: fixtures = DEFAULT_FIXTURES.parent / "v1_rev2"`）。

条码来源还有一条环境变量路径：未传 `--gtins` 时，`emit_static.default_gtins()` 会先读 `PPD_GTINS`（逗号分隔），再回落到夹具清单。`pipeline/build.py` 自己则是先看 `--gtins`，为空才调用 `default_gtins()`，因此 `PPD_GTINS` 对 CLI 同样生效。

输出为一行 JSON 摘要：

```json
{
  "build_id": "build_20261112_052321Z",
  "artifacts": 10,
  "gtins": 3,
  "failures": {},
  "manifest": "dist_data/v1/manifest.json"
}
```

`failures` 的键形如 `"6901234567892:samr_gov"`，值为 `"错误码: 说明"`。

## 描述提示（`description_hints.json`）

部分源的查询标识符不是条码（`samr_std` 用标准号、`cnipa` 用商标名、`cifer` 用备案号）。`pipeline/fixtures/v1/description_hints.json` 提供「条码 → 各源查询标识符」的静态映射：

```json
{
  "_note": "构建提示：仅用于把「非条码类」查询标识符映射到各数据源。…",
  "6901234567892": { "samr_std": "GB19644-2010" }
}
```

`build.py` 读取该文件（先试 `--fixtures` 下的，再试默认目录），剔除 `_note` 后传给 `run_build()`。映射值直接作为该源的 `identifier`；没有映射时该源对这条码就被跳过（`Builder._identifier_for` 返回 `None`）。

`pipeline/fixtures/v1_rev2/` 下没有 `description_hints.json`，因此 `--rev2` 构建会回落到默认目录的提示文件。

## 账本行为

### 表结构

| 表 | 内容 | 只追加 |
| --- | --- | --- |
| `ledger_meta` | `schema_version` | — |
| `snapshots` | 原始快照：`evidence_id`、`source_id`、`source_key`、`adapter_version`、`retrieved_at`、`request_url`、`request_params`、`payload_json`、`payload_sha256` | 是 |
| `observations_log` | 每次观测一行：`evidence_id`、`observed_at`、`reason`（`first_observation` / `re_observation_same_content`） | 是 |
| `visibility_events` | 可见性事件：`event_id`、`entity_id`、`source_id`、`evidence_id`、`event_type`、`recorded_at`、`note` | 是 |
| `build_runs` | 构建记录：`build_id`、`registry_version`、`adapter_versions`、`started_at`、`finished_at`、`tool_version`、`note` | 否（`finish_build` 会 UPDATE `finished_at`，条件为 `finished_at IS NULL`） |
| `artifacts` | 每个产物：`build_id`、`path`、`sha256`、`bytes`、`written_at` | 是 |

触发器：`snapshots` / `visibility_events` / `artifacts` / `observations_log` 各有 `BEFORE UPDATE` 与 `BEFORE DELETE` 两条，共 8 条，动作都是 `RAISE(ABORT, '<table> is append-only')`。sqlite3 把 `ABORT` 以 `IntegrityError` 冒泡，`Ledger.transaction()` 捕获并映射为 `LedgerAppendOnlyError`。

> 注意：`build_runs` 是唯一允许更新的表（`finish_build` 写完成时间）。触发器不覆盖它，这是有意的例外，因为「构建是否跑完」本身是构建元数据而非来源证据。

### 唯一索引

| 索引 | 作用 |
| --- | --- |
| `ux_snapshots_evidence (evidence_id)` | 内容寻址去重 |
| `ux_snapshots_identity (source_id, source_key, adapter_version, payload_sha256)` | 同一来源、同一查询键、同一适配器版本、同一内容，不得二次插入 |
| `ux_visibility_event (event_id)` | 事件幂等 |
| `ix_visibility_entity (entity_id, seq)` | 按实体按序取事件 |

`event_id = "vis_" + SHA-256({entity_id, source_id, evidence_id, event_type, recorded_at})[:20]`，配合 `INSERT OR IGNORE` 保证同一次构建内重复调用不产生重复事件。

### 为什么重复构建会产生 `HISTORICALLY_OBSERVED`

这是设计正确行为，不是 bug：

1. 第一次构建：gs1 快照 A 落账本，`_visibility_events` 追加 `CURRENTLY_VISIBLE(A)`。
2. 第二次构建（同一夹具、内容未变）：内容寻址使 `evidence_id` 相同，`append_snapshot` 不插入新快照，只追加一条 `re_observation_same_content` 到 `observations_log`。`_visibility_events` 发现 `(source_id, evidence_id, CURRENTLY_VISIBLE)` 已在 `existing_keys` 中，**不重复追加**。所以「原样重跑」不会制造历史事件。
3. 若来源内容**变了**（如 `--rev2`）：产生新 `evidence_id`，新快照成为该源最新 → 追加 `CURRENTLY_VISIBLE(新)`；同时遍历既有事件，把旧快照的 `CURRENTLY_VISIBLE` 追加一条 `HISTORICALLY_OBSERVED`。旧快照与旧事件都保留。

因此「重复构建产生 `HISTORICALLY_OBSERVED`」只在来源真的变更时发生；重复跑同一夹具是幂等的。

### 幂等性与产物可复现

| 层 | 幂等 | 说明 |
| --- | --- | --- |
| 快照 | 是 | 内容寻址 + 唯一索引 |
| 可见性事件 | 是 | `existing_keys` 集合 + `INSERT OR IGNORE` + `event_id` 含 `recorded_at` |
| 分片内容 | 是（除 `build_id` / `built_at`） | 门禁 5 断言两次独立构建的分片逐字段相等 |
| 分片字节 | 否 | `build_id` / `built_at` 每次不同，因此文件 SHA-256 每次不同 |

产物内的 `observations[].payload_sha256` 取自**快照**内容哈希，不是产物自身哈希 —— 这条设计避免了「产物内容依赖自身书写时间」的自指循环。

## 新增一个数据源

### 1. 注册表条目（`pipeline/config/source_policy_registry.json`）

顶层结构固定，未登记字段会被拒：

```json
{
  "registry_version": "1.0.0",
  "generated_at": "2026-01-15T00:00:00Z",
  "description": "…",
  "allowed_access_modes": ["OPEN_BULK", "OPEN_API", "ACCESS_PENDING", "SOURCE_UNAVAILABLE"],
  "global_forbidden_actions": ["captcha_bypass", "rate_limit_override", "credential_in_artifact", "cookie_replay", "login_required_scrape"],
  "sources": [ … ]
}
```

`allowed_access_modes` 必须与 `policy.ACCESS_MODES` 逐字相等。`global_forbidden_actions` 必须至少包含 `captcha_bypass`、`rate_limit_override`、`credential_in_artifact`。

单源条目**必填 14 个键**（`REQUIRED_SOURCE_KEYS`），可选仅 `notes`：

| 键 | 校验 |
| --- | --- |
| `source_id` | `^[a-z][a-z0-9_]{2,31}$`，全局唯一 |
| `display_name` / `authority` / `jurisdiction` | 自由字符串 |
| `access_mode` | `OPEN_BULK` / `OPEN_API` / `ACCESS_PENDING` / `SOURCE_UNAVAILABLE` |
| `policy_url` | 字符串（门禁 3 要求 `https://` 前缀） |
| `legal_basis` | 自由字符串 |
| `query_identifier_types` | 非空数组 |
| `endpoint_template` | 必须以 `https://` 或 `local://` 开头；支持 `{q}` / `{identifier}` / `{gtin}` / `{standard_no}` / `{di}` 占位 |
| `rate_limit` | 必填 `requests_per_minute`（正整数）、`min_interval_ms`（**≥ 1000**）、`max_concurrency`（≥ 1） |
| `backoff` | 必填 `base_ms`（≥ 100）、`factor`（≥ 1.0）、`jitter_ms`（0–2000）、`max_retries`（0–5） |
| `schema_keys` | 非空数组；响应顶层必须包含这些键 |
| `expected_fields` | 非空数组；声明期望字段（用于文档与对账，不强制校验响应） |
| `credentials` | 必填 `token_env`、`app_id_env`，各为 `null` 或 `^[A-Z][A-Z0-9_]{2,63}$` 的环境变量名 |

`credentials` 的值**只能是环境变量名或 null**。写任何形似真实凭据的字面值（`sk_live`、`AKIA`、`ghp_`、`Bearer …`、JWT 片段、≥32 位十六进制串）都会被 `PolicyError` 拒收。门禁 6 的 `test_registry_rejects_secret_literal` 专门验证这一点。

### 2. 适配器类（`pipeline/adapters/v1/__init__.py`）

```python
class YourSourceAdapter(_RecordsAdapter):
    source_id = "your_source"        # 必须与注册表一致
    identifier_type = "GTIN"
    adapter_version = "1.0.0"        # 变更解析逻辑时必须改版本号
```

并加入 `ADAPTERS` 字典的构造元组。`_RecordsAdapter.normalize_records()` 只是把 `payload["records"]` 过滤为 dict 列表，不做字段改写。

若新源需要完全不同的解析，继承 `SourceAdapter` 并覆写 `fetch()`；但**不要**在适配器里做字段推断、候选合并或真伪判断 —— 这些一律在 `Builder` / `dossier` 层以机械规则处理。

### 3. `emit_static.py` 的映射表

| 位置 | 需要补什么 |
| --- | --- |
| `IDENTIFIER_TYPE_BY_SOURCE` | `"your_source": "GTIN"` 等；决定该源用条码还是提示值作为查询键 |
| `dossier.STATUS_TIER` | 排序优先级（缺省落 99，排在最后） |
| `dossier.build_candidates()` | 若新源有权威标识符可比对，在此加分支并给出 `exact` 判定；否则落到 `MANUAL_CONFIRM` |
| `dossier.assemble_blocks()` | 若需要独立区块，加到 `BLOCK_IDS` 并实现填充逻辑 |

### 4. 夹具

`pipeline/fixtures/v1/{source_id}/{identifier}.json`，内容形如：

```json
{ "records": [ { "record_id": "…", "gtin": "…", "…": "…" } ] }
```

`LocalFixtureTransport._path_for()` 按 `root / source_id / f"{identifier}.json"` 定位；文件不存在时返回 `{"records": []}`（零记录，不产证据）。

若夹具内容命中 `CAPTCHA_HINT` 正则（`captcha|验证码|请先登录|login required|signin`），会抛出 `CaptchaOrLoginRequired`。这是**故意**的：测试需要能模拟验证码场景。

### 5. 策略状态

按可获得性选择 `access_mode`：

| 状态 | 何时用 |
| --- | --- |
| `OPEN_BULK` | 公开批量数据，无需凭据 |
| `OPEN_API` | 公开查询接口，可能需要构建机环境变量 |
| `ACCESS_PENDING` | 尚未取得接入许可/确认；**适配器禁止运行** |
| `SOURCE_UNAVAILABLE` | 入口不稳定或已下线；**适配器禁止运行** |

后两者仍会完整出现在 `manifest.sources` 与每个条码的 `coverage_manifest`，状态原样披露，对应区块的 `state` 也取该 `access_mode`。

### 6. 补测试

`tests/acceptance_gates/` 中至少需同步更新：门禁 3（源数量与会话覆盖清单自动跟随注册表，通常无需改）、门禁 7（若新增可比对标识符）、门禁 2 的黄金样本覆盖断言（`ledger_sources - {"samr_std"} <= seen_sources`）。

## 凭据处理

### 读取路径

`SourceAdapter._headers()`：

```python
token_env = (self.policy.credentials or {}).get("token_env")
app_env   = (self.policy.credentials or {}).get("app_id_env")
if token_env and os.environ.get(token_env):
    headers["Authorization"] = f"Bearer {os.environ[token_env]}"
if app_env and os.environ.get(app_env):
    headers["X-App-Id"] = os.environ[app_env]
```

当前注册表里的环境变量名：`CNIPA_TOKEN` / `CNIPA_APP_ID` / `SAMR_GOV_TOKEN` / `CIFER_TOKEN`。其余源为 `null`。

### 三道防线

| 时机 | 机制 | 位置 |
| --- | --- | --- |
| 注册表加载 | 值必须是环境变量名形态；形似凭据即拒收 | `policy._validate_source` |
| 落账本前 | `assert_no_credentials(payload)` → 敏感键拒写、敏感值拒写 | `ledger.append_snapshot` |
| 写产物前 | 再次 `assert_no_credentials`（JSON 文本先解析再按字段名判定） | `emit_static._write_json` |

脱敏规则（`ledger.redact`）：

| 规则 | 说明 |
| --- | --- |
| 敏感键名 | 匹配 `token|secret|password|passwd|authorization|api_key|app_id|appid|bearer|credential|cookie|session` 的键，值替换为 `"<redacted>"`；`_SAFE_KEY_ALLOWLIST` 中的键（`source_id`、`payload`、`records` 等）豁免 |
| 硬凭据特征 | `sk_live`、`AKIA…`、`ghp_…`、`eyJ…`、`Bearer …` 一律 `***` |
| URL 查询串 | `token=|key=|secret=|appid=|app_id=|signature=|sig=|credential=` 的值替换为 `***`（`mask_url` 用于 `request_url`） |
| 高熵十六进制 | `[A-Fa-f0-9]{32,}`；**仅当字段名在 `_HASH_KEY_ALLOWLIST`（`sha256`、`payload_sha256`、`content_hash`、`artifact_sha256`、`digest`、`hash`、`checksum`、`zxing_wasm_sha256`、`dataset_sha256`）时才放行** |

最后一条是必要的：SHA-256 摘要本身就是 32+ 位十六进制，若无字段名区分会把所有哈希误判为凭据（历史实现确实踩过这个坑）。

## 限流与退避语义

### 令牌桶（`ThrottleState.acquire`）

双重约束，两者都满足才放行：

1. **最小间隔**：距上次调用不足 `min_interval_ms` 则 `sleeper(need - gap)`。
2. **分钟配额**：滚动 60s 窗口，`window_count >= requests_per_minute` 时睡到窗口结束并重置。

`sleeper` 默认 `time.sleep`，测试注入 `SilentSleeper` 只记录不睡。两个计数器 `last_call_at` / `window_start` 用 `time.monotonic()`，不受系统时钟调整影响。

### 指数退避（`Backoff`）

```
delay(attempt) = (base_ms * factor^attempt + uniform(0, jitter_ms)) / 1000  （秒）
```

`rng` 默认 `random.Random(0)`（固定种子，保证可复现）。重试上限取注册表 `max_retries`；构造时传 `max_retries_override` **只能调低**，调高直接抛 `PolicyGateError("禁止提升重试次数超出策略注册表上限")`。

### 429 的处理

```
if exc.detail.get("status") == 429 and attempt >= max_retries:
    raise RateLimitedError("…限流重试已耗尽，禁止突破限流")
```

即：429 可以按退避等，但**绝不提速**；耗尽即终止。

## `SOURCE_FORMAT_CHANGED` 熔断与恢复

### 触发条件

`_structural_error()` 在以下任一情况下返回描述：

| 情况 | 描述 |
| --- | --- |
| 响应为 `null` | `响应为空（null）` |
| 顶层既非 dict 也非 list | `响应顶层类型异常: Xxx类型名` |
| dict 缺少任一 `schema_keys` | `缺少 schema_keys: [...]` |

命中后：

```python
self._broken = True
raise SourceFormatChanged("… → 熔断，不猜测解析", detail={url, problem, schema_keys})
```

`_broken` 在适配器实例内生效，后续 `fetch()` 直接抛。"猜测解析"被明令禁止：宁可空结果，也不产出可能错误的字段。

### 恢复流程

1. 人工核对上游新响应的真实结构（抓一次原始响应，逐字段对照）。
2. 更新注册表的 `schema_keys`（若信封变了）与 `expected_fields`。
3. 更新适配器的 `adapter_version`（例如 `1.0.0` → `1.1.0`）。这一步不可省略：`evidence_id` 含版本号，版本变化会正确产生新证据而不是与旧证据混淆。
4. 若字段名变了，同步修改 `dossier.assemble_blocks()` 中读取该源记录的分支。
5. 重跑构建与全部门禁；覆盖清单里该源的 `QUERY_FAILED` 应回到 `QUERIED_MATCHED`。

熔断**只影响单次构建进程内的那个适配器实例**，不会污染账本历史：该源的旧快照与旧事件全部保留，`history_timeline` 仍能看到过去成功采集的版本。

## 可复现性要求

门禁 5（`test_query_reproducibility.py`）要求每次构建记录四个输入，缺一不可：

| 记录位置 | 字段 | 含义 |
| --- | --- | --- |
| `snapshots` | `source_id` | 哪条来源 |
| `snapshots` | `source_key` | 用什么查询键 |
| `snapshots` | `retrieved_at` | 何时采集 |
| `snapshots` | `adapter_version` | 用哪个版本的解析逻辑 |
| `snapshots` | `payload_sha256` | 内容指纹 |
| `snapshots` | `request_url` | 请求现场（已脱敏） |
| `build_runs` | `registry_version` / `adapter_versions` / `tool_version` / `started_at` / `finished_at` | 构建现场 |
| `manifest` | `registry_version` / `adapter_versions` / `tool_version` / `query_index` | 对读者公开的构建现场 |

任何一项缺失都会让「这份展示为什么是这样」无法回答。修改解析逻辑而不改 `adapter_version` 会导致同一 `evidence_id` 对应两种语义 —— 这是必须避免的。

## 完整性校验

```python
ledger.integrity_report()  # {"schema_version", "snapshots", "visibility_events", "artifacts", "hash_mismatches", "ok"}
ledger.verify_integrity()  # hash_mismatches 非空则抛 LedgerError
```

`integrity_report` 对每条快照重算 `SHA-256(canonical_json(payload))` 并与 `payload_sha256` 比对。即使外部直接改数据库并 `DROP TRIGGER`，重算也能发现（门禁 9 的 `test_tampered_snapshot_detected` 正是这样验证的）。

产物侧则由 `artifacts` 表的 `sha256` / `bytes` 对照磁盘文件。

## 常见操作

| 目标 | 命令 |
| --- | --- |
| 从零重建演示基线 | `rm -rf dist_data .state && npm run build` |
| 演示历史保留 | 先 `python3 -m pipeline.build`，再 `python3 -m pipeline.build --rev2` |
| 只重出分片不动账本 | `python3 -m pipeline.build --reset` |
| 单条码排查 | `python3 -m pipeline.build --gtins 6901234567892` |
| 校验账本 | `.venv/bin/python -c "from pipeline.builder.ledger import Ledger; print(Ledger('.state/ledger.sqlite3').integrity_report())"` |

`--reset` 只删 `dist_data/`，账本保留，因此重跑会用账本里已有的全部历史快照重新装配区块（这正是「不丢历史」的体现）。要真正从零开始必须删账本文件或使用 `--reset-ledger`。
