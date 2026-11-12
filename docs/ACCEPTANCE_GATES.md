# 验收门禁

`tests/acceptance_gates/` 是十项硬门禁的实现。Pytest 当前收集到 **102 个测试**，分布在 15 个测试模块 + 1 个 `conftest.py` + 1 个浏览器启动助手 `_browser_launch.py`。

> 注意：项目文档与计划书长期称「十项门禁」，而目录里有 **15** 个测试模块。8b（端侧 OCR）与 8c（中文折行）作为门禁 8 的两个补充子项存在；真正与「十项」并列的附加模块是 `test_demo_contract.py`（离线兜底样例契约）、`test_demo_origin_and_compare_focus.py`（演示样例来源属性 + 06 区块并列来源焦点）与 `test_diet_scaling_and_backup.py`（饮食按克重折算 + 本地备份）。本文按模块逐个记录，不为凑数做合并。

## 运行

```bash
# 全部门禁
.venv/bin/python -m pytest tests/acceptance_gates -q

# 单项
.venv/bin/python -m pytest tests/acceptance_gates/test_source_trace_rate.py -q

# 单个测试
.venv/bin/python -m pytest tests/acceptance_gates/test_integrity_check.py::test_tampered_snapshot_detected -q

# 通过 npm
npm test
```

## 公共夹具（`conftest.py`）

| 夹具 | 作用 |
| --- | --- |
| `registry` | `load_registry()`，真实读取 `source_policy_registry.json` |
| `ledger` | 独立临时 SQLite 账本（`tmp_path`），测试结束关闭 |
| `sleeper` | `SilentSleeper`：记录退避时长但不真睡，让限流相关测试瞬间完成 |
| `make_builder` | 工厂：注入 `registry` / `ledger` / `sleeper` / 固定种子 `random.Random(7)`；可按需替换 `transport`（用于注入 `scenarios`） |
| `built` | 基线构建产物：3 个夹具条码（`6901234567892`、`6905554443331`、`6901112223339`），`build_id = "build_test_baseline"` |
| `read_json` | 读 JSON 的小工具 |

`SilentSleeper` 是关键设计：它让「断言退避被调用了几次、每次多久」成为可行断言，而不必真的等待。

---

## 门禁 1 — `test_source_trace_rate.py`（3 个测试）

**断言什么**：任何出现在分片里的记录，都必须能追溯到账本中的一条具体快照。

| 测试 | 断言 |
| --- | --- |
| `test_all_display_records_bound_to_evidence` | 遍历 `/v1/products/*.json` 的 `blocks[].fields`、`blocks[].items`、`observations`、`source_visibility_events`，以及 `/v1/ingredients/*.json` 的 5 个数组、`/v1/gtin/*.json` 的 `match_candidates`。每条都必须有非空 `source_id` 与 `evidence_id`；`evidence_id` 必须存在于账本；`source_id` 必须与账本一致；`raw_value` 若能解析为结构，必须与 `snapshot.payload` 完全相等，否则其字符串必须出现在快照序列化结果中 |
| `test_field_trace_rate_is_full` | `traced / total == 1.0`，即 100%，不允许 99.9% |
| `test_no_final_truth_field` | 递归遍历所有分片，键名（小写）不得命中 `final_truth` / `final_value` / `verdict` / `conclusion` / `final_conclusion` / `verdict_value`；且 `observation_policy.no_final_truth_field` 必须为 `true` |

**怎么被执行**：由 `built` 夹具先跑一次完整构建，然后纯离线读取产物 + 账本比对。

**怎样会失败**：
- 新增区块时忘了给 `fields` / `items` 加 `source_id` / `evidence_id`；
- 字段值被加工过（例如把 `280毫克/100克` 规范成 `280mg/100g`），导致与 `raw_value` 或快照对不上；
- 引入了任何「最终值/结论」类字段；
- 某条 `evidence_id` 在产物里存在但账本里没有（例如手工改过分片）。

**例外**：`数据源覆盖状态` 与 `原始证据哈希` 两类 items 被显式跳过 —— 前者描述接入状态，后者本身是摘要。

---

## 门禁 2 — `test_field_extraction_accuracy.py`（3 个测试）

**断言什么**：从原始快照到分片字段的搬运**没有失真**。

| 测试 | 断言 |
| --- | --- |
| `test_extraction_accuracy_at_least_99_5` | 收集所有 (产物值, 原始快照) 对，准确率 `>= MIN_ACCURACY = 0.995`；且样本对必须 `> 30` 对（否则断言样本不足） |
| `test_golden_samples_cover_all_sources` | 每个商品的 `observations` 非空；账本中参与该商品的证据来源必须被 `observations` 完整覆盖；全部商品合计应覆盖账本中除 `samr_std` 外的所有来源（`samr_std` 按标准号查询，可能不属于任何条码的观测） |
| `test_ingredient_order_preserved` | 配料条目的 `order` 必须等于 `raw_name` 在原始快照 `ingredients` 数组中的下标 + 1 |

**怎么被执行**：`built` 产物 + 账本逐项比对。

**怎样会失败**：
- 把「毫克」改写成「mg」、把全角数字改半角，导致 `raw_value` 与快照不等；
- 对配料做排序（例如按长度、按拼音）而不是保持标签顺序；
- 把不同来源的字段混进同一条记录；
- 漏掉某个来源的观测（例如零记录跳过写错，把某个源整个吞掉）。

**关于 99.5% 阈值**：`MIN_ACCURACY = 0.995` 是测试文件中的硬编码常量，代码与注释未说明其历史依据。可以确认的事实是：比对对象是 `raw_value` 与原始快照，因此任何经过改写的字段值都会直接降低准确率；`canonical_nutrient()` 只影响 `nutrient` 字段（比对不用它），不会造成差异。

---

## 门禁 3 — `test_source_coverage.py`（5 个测试）

**断言什么**：覆盖缺口必须**显式披露**，不能被隐藏或伪装。

| 测试 | 断言 |
| --- | --- |
| `test_manifest_discloses_every_source` | `manifest.sources` 的 `source_id` 集合必须恰好等于注册表全部源；每个源的 `access_mode` 合法、`policy_url` 以 `https://` 开头、**不得含 `credentials` 键**；`coverage_summary` 各项之和等于源数量；`no_final_truth_field` / `no_auto_merge` 为 `true` |
| `test_each_gtin_discloses_full_coverage` | 每个 `gtin` 分片的 `coverage_manifest` 必须包含全部 7 个源；`coverage_state` 必须在白名单内；`ACCESS_PENDING` / `SOURCE_UNAVAILABLE` 的源，其 `coverage_state` 必须**等于** `access_mode`（不能伪装成「查过没有」） |
| `test_product_blocks_mark_unavailable_sources` | `blocks` 的 `block_id` 集合必须正好等于 `block_order`，且 **= 16**；`trademark_records.state == "ACCESS_PENDING"`；`import_export_records.state == "SOURCE_UNAVAILABLE"`；每个区块都必须有非空 `note` 与 `state` |
| `test_manifest_query_index_matches_artifacts` | `query_index` 非空；每个 `paths.*` 指向的文件必须存在；`candidate_count` 必须等于实际 `match_candidates` 长度 |
| `test_coverage_gap_is_never_silent` | 每个覆盖条目的 `note` 非空；`QUERIED_NO_MATCH` 状态的说明不被静默；未命中时 `query_status_note` 必须以 `QUERIED_NO_MATCH` 开头 |

**怎样会失败**：
- 新增源但没进注册表 / 注册了但没进 manifest；
- 把 `ACCESS_PENDING` 记成 `QUERIED_NO_MATCH`（这是最严重的失败：读者会误以为「查过、确实没有」）；
- 区块数量变成 15 或 17；
- 某区块 `note` 留空；
- `query_index` 的路径拼错或漏文件。

---

## 门禁 4 — `test_history_retention.py`（4 个测试）

**断言什么**：来源更新、状态变更、来源下线，都不得删除历史。

| 测试 | 断言 |
| --- | --- |
| `test_old_snapshot_retained_after_source_update` | 先 `v1` 再 `v1_rev2` 构建同一 GTIN；UDI 快照数 `>= 2`；两版 payload 不同；事件类型同时含 `CURRENTLY_VISIBLE` 与 `HISTORICALLY_OBSERVED`；历史事件引用的 `evidence_id` 必须仍在账本；同一来源推导出的「当前生效」**只有 1 条**且为 `CURRENTLY_VISIBLE`；`history_timeline` 至少 2 条 |
| `test_status_change_is_recorded_as_raw_value` | rev2 中 `samr_gov` 状态由「有效」变「注销」；`license_records` 只展示当前最新记录的状态原文，`raw_value == "注销"`；并且旧快照里仍能查到「有效」、新快照里能查到「注销」 |
| `test_append_only_triggers_block_history_rewrite` | 在事务内执行 `UPDATE snapshots SET payload_sha256 = 'deadbeef'` 必须抛 `LedgerError`；绕过 ORM 直接开 sqlite3 连接，对 `snapshots` / `visibility_events` / `observations_log` 的 `UPDATE` 与 `DELETE` 全部必须被触发器阻断（`append_only_guard` 会静默返回表示受保护，抛 `LedgerAppendOnlyError` 表示**未受保护**） |
| `test_removal_event_supported` | 可追加 `REMOVED` 事件；追加后 `snapshot_by_id()` 仍能取到该证据 |

**怎样会失败**：
- 用「新快照覆盖旧快照」的方式更新字段区块；
- 漏加 `HISTORICALLY_OBSERVED` 事件（读者会以为旧版本从未存在）；
- 触发器被误删（例如某次迁移重写了 schema）；
- 把 `REMOVED` 实现成 `DELETE`。

---

## 门禁 5 — `test_query_reproducibility.py`（4 个测试）

**断言什么**：相同账本 + 相同输入 → 相同产物；产物哈希可核验。

| 测试 | 断言 |
| --- | --- |
| `test_build_record_is_complete` | `build_runs` 至少有 1 条；`registry_version` 非空、`tool_version == TOOL_VERSION`、`adapter_versions` 解析后等于 `adapter_registry_map()`、`finished_at` 非空；每条快照的 `source_id` / `source_key` / `retrieved_at` / `adapter_version` / `payload_sha256` / `request_url` 全部非空 |
| `test_products_are_byte_reproducible` | 两次独立构建（不同目录、不同 `build_id`）的 JSON 文件**集合**相同；除 `manifest.json` 外，去掉易变字段后逐文件内容相等 |
| `test_manifest_records_reproducibility_inputs` | `build_id`、`tool_version`、`registry_version`、`adapter_versions`、`query_index` 全部存在；每个 `query_index[].gtin` 是纯数字 |
| `test_artifact_hashes_match_files` | `artifacts` 表非空；每条记录的 `path` 存在；文件实际 SHA-256 等于记录值；`bytes` 等于文件大小 |

**怎样会失败**：
- 产物里嵌入了 `built_at` 之外的随机/时间内容（例如把当前时间写进区块 note）；
- 把产物自身的哈希写进产物（自指循环，会导致每次构建都不同）；
- 忘记给某条快照记录 `request_url`；
- 改了解析逻辑却没改 `adapter_version`（这会破坏「同 evidence_id 同语义」的承诺）；
- `artifacts` 记录了路径但文件后来被删。

**注意 `manifest.json` 被排除**：它含 `build_id` / `built_at`，天然不可字节复现。其余分片必须逐字段可复现。

---

## 门禁 6 — `test_policy_compliance.py`（12 个测试）

**断言什么**：合规采集的每一条红线都有可执行证据。

| 测试 | 断言 |
| --- | --- |
| `test_unregistered_source_cannot_construct` | 用一个 `source_id` 不在注册表的假适配器，构造必须抛 `PolicyGateError` |
| `test_access_pending_source_is_blocked` | 对每个 `ACCESS_PENDING` / `SOURCE_UNAVAILABLE` 源，构造适配器必须抛 `PolicyBlocked` |
| `test_runnable_set_excludes_blocked_sources` | `cnipa` 与 `cifer` 不在 runnable 集合；`udi` / `gs1` / `samr_gov` / `samr_std` / `label_transcript` 都在 |
| `test_rate_limit_cannot_be_bypassed_by_retries` | `max_retries_override` 高于注册表上限时构造必须抛 `PolicyGateError` |
| `test_online_transport_disabled_by_default` | `StaticHttpTransport` 默认 `enabled=False`，任何 `fetch` 必须抛 `AdapterError` |
| `test_captcha_triggers_stop_not_bypass` | 注入 `scenarios={"udi:…": "captcha"}` 必须抛 `CaptchaOrLoginRequired`，不重试 |
| `test_429_exhaustion_raises_without_breaking_limit` | 注入 `rate_limited` 场景，重试耗尽后必须抛 `RateLimitedError` |
| `test_schema_change_circuit_breaker` | 注入 `format_changed` 场景，必须抛 `SourceFormatChanged`，`code == "SOURCE_FORMAT_CHANGED"`，`adapter.broken is True`，且 `requests_made == 1`（熔断后不再发第二次请求） |
| `test_registry_rejects_secret_literal` | 把真实凭据字面值写入 `credentials.token_env` 后 `load_registry()` 必须抛 `PolicyError` |
| `test_artifacts_contain_no_credentials` | 设置 `SAMR_GOV_TOKEN` / `CNIPA_APP_ID` 环境变量后构建，所有产物文本不得包含这两个值，也不得包含 `Authorization` 字样 |
| `test_redaction_removes_credentials` | `redact()` 把 `token` / `Authorization` 替换为 `<redacted>`；普通字段（`全脂乳粉`）保持不变；64 位十六进制的 `payload_sha256` **不被**误判 |
| `test_forbidden_actions_declared` | 注册表的 `global_forbidden_actions` 必须包含 `captcha_bypass` / `rate_limit_override` / `credential_in_artifact` |

**怎样会失败**：
- 把某个源从 `ACCESS_PENDING` 改成 `OPEN_API` 而未取得授权；
- 给 `StaticHttpTransport` 加个默认 `enabled=True`；
- 在验证码分支里加「重试三次」的逻辑；
- 熔断后仍继续发请求（`requests_made` 会变成 2）；
- 把凭据写进 `notes` 或 `legal_basis`；
- 哈希白名单被删，导致所有 `payload_sha256` 被脱敏成 `<redacted>`。

---

## 门禁 7 — `test_entity_separation.py`（6 个测试）

**断言什么**：不合并。多候选、多 SKU、同名不同码，都必须并列且互不串档。

| 测试 | 断言 |
| --- | --- |
| `test_same_gtin_multiple_sources_are_parallel` | 同条码候选 `>= 3`，且包含 `label_transcript` / `gs1` / `udi`；每个候选都有 `candidate_id` / `evidence_id` / `record_id`；`exact_match_count` 与 `manual_confirm_count` 等于实际计数之和；`no_auto_merge_notice` 非空；SC 许可号候选必须是 `MANUAL_CONFIRM` |
| `test_same_brand_multiple_sku_not_merged` | `6901234567892`（400g）与 `6905554443331`（800g）的 `entity_id` 不同；两个 product 分片的 `entity_id` 不同；各自 `identity_basic` 里分别是 `400g` / `800g`；`observation_policy.no_auto_merge` 为 `true` |
| `test_multi_record_same_gtin_in_one_source_not_merged` | 同一来源同一条码下的两条记录必须保持 2 个候选、2 个独立 `record_id`；净含量原样并列（`{"250mL", "1L"}`）；两条记录**共享同一 `evidence_id`**（同一次抓取）；`candidate_id` 不重复 |
| `test_candidate_ids_are_stable_and_unique` | 全量 `candidate_id` 无重复 |
| `test_entity_id_deterministic_from_gtin` | 同 GTIN → 同 `entity_id`；不同 GTIN → 不同 `entity_id` |
| `test_similar_name_does_not_create_link` | 构建两个条码后，两者的候选 `evidence_id` 集合**互不相交**；A 的所有 GTIN 类标识符值都等于 A 的条码 |

**怎样会失败**：
- 按 `display_name` 或 `brand` 做归并；
- 把同一次抓取的多条记录合成一条（丢 `record_id`）；
- 让 `candidate_id` 依赖遍历顺序而非内容哈希；
- 把 800g 的记录塞进 400g 的档案。

---

## 门禁 8 — `test_privacy_and_zero_cookie.py`（7 个测试，真实浏览器）+ 门禁 8b — `test_client_ocr.py`（4 个测试，真实浏览器）+ 门禁 8c — `test_layout_wrapping.py`（6 个测试，真实浏览器）

**断言什么**：在真实 Chromium 里跑真实 UI，证明零 Cookie、零外发、图片不出浏览器、词条只在 IndexedDB。

**前置**：`apps/web/dist/index.html` 必须已构建，否则整个模块 `skip`。测试自己起一个 `python -m http.server` 指向 `apps/web/dist`，端口随机。

| 测试 | 断言 |
| --- | --- |
| `test_no_outbound_network_requests` | 收集全部请求：`data:` / `blob:` 之外，hostname 必须属于本机（`127.0.0.1` / `localhost`）；方法集合 ⊆ `{GET, HEAD}` |
| `test_zero_cookies` | `context.cookies() == []` 且 `document.cookie.length == 0` |
| `test_ui_shows_zero_cookie_badge` | 页面上必须可见含 `cookie.length=0` 的文本 |
| `test_diet_terms_stay_in_indexeddb` | 点「我的忌口与饮食计划」→ 填「乳糖」→ 点「添加本地词条」：词条可见；**网络请求数不变**；`localStorage` / `sessionStorage` / `document.cookie` 序列化后不含「乳糖」；直接开原生 `indexedDB` 读 `diet_rules`，必须能找到 `term === "乳糖"` |
| `test_scanned_image_never_leaves_browser` | 不得出现非 `GET/HEAD` 请求，不得出现 URL 含 `upload` 或 `ocr` 的请求；方法集合 ⊆ `{GET, HEAD}` |
| `test_persistence_after_reload` | `page.reload()` 后词条仍在；Cookie 仍为空 |
| `test_no_upload_endpoint_in_source` | `apps/web/src/**/*.ts*` 中不得出现 `navigator.sendBeacon` / `new FormData` / `multipart/form-data` |

**怎么被执行**：Playwright + 真实 Chromium。`_find_chromium_executable()` 会在 `PLAYWRIGHT_BROWSERS_PATH`、`~/Library/Caches/ms-playwright`、`~/.cache/ms-playwright` 里按 `chromium_headless_shell-*` / `chromium-*` 模式找可执行文件，找到就显式传 `executable_path` —— 这是为了避免「Playwright Python 包版本与本机浏览器 build 号不一致」导致的启动失败。启动参数含 `--no-sandbox`（容器/沙箱环境必需）。

**怎样会失败**：
- 引入任何第三方 CDN 资源（字体、图标、分析脚本）；
- `fetchJson` 的 `credentials` 从 `"omit"` 改成 `"include"`；
- 把词条写进 `localStorage`（简单，但违反红线）；
- OCR 走服务端；
- UI 移除 cookie 审计标识；
- 明文出现 `new FormData`（即使只是注释也会被扫到）。

**注意**：门禁 8 与门禁 8b 的浏览器启动统一走 `_browser_launch.py` 的 `launch_chromium()`：完整 Chromium 优先（带合成器，也能验证 WebGPU），失败才回退 headless shell，并对启动期 SEGV 做有限次重试。`_find_full_chromium()` 仍被 `tests/visual_smoke_gpu.py` 与 `tests/a11y_probe.py` 两个探针脚本调用，但**不属于 pytest 收集范围**，不计入门禁测试数。WebGPU 在门禁层面只被间接覆盖（「页面能正常渲染 + 零外发请求」）；headless shell 下 `getCurrentTexture()` 不可用，代码会正确降级到 CSS 兜底，真实 GPU 路径需用探针脚本在完整 Chrome 中验证。

---

---

## 门禁 8c — `test_layout_wrapping.py`（6 个测试，真实浏览器）

**为什么单独存在**：排版缺陷不报错、不崩、不发请求。实测发现档案页第 05 区块（配料表原文 / 致敏原原文）在窄容器下会把「全脂乳粉、乳清蛋白粉」折成逐字竖排。两个原因叠加：

1. `ItemRow` 正文列用 `flex: 1 1 0`（`min-w-0 flex-1`），flex-basis 为 0，会被长 `evidence_id` 标签压到极窄；
2. 正文用 `word-break: break-all`，允许在任意两字之间断开。

实测复现（300px 容器 + 长证据标签）：正文列被压到 **53px**，渲染成「全脂乳 / 粉、乳清 / 蛋白粉」。

| 测试 | 断言 |
| --- | --- |
| `test_chinese_terms_never_break_into_single_characters` | 在 390 / 360 / 320 / 1280 四个宽度打开真实档案页，用 `Range` 逐字符计算每行实际内容；配料相关文本（`.item-row__primary` / `.field-value`）不得出现单字成行 |
| `test_layout_gate_detects_squeezed_body_column` | **反向对照**：用最小 HTML 复刻 `ItemRow` 结构，确认 `flex:1 1 0` + `break-all` 确实产生单字成行，而 `14ch` 下限 + `break-word` 产生单行。证明判定逻辑不是空转 |
| `test_scan_ingredient_terms_never_break_into_single_characters` | 扫码页第 05 区块（`<ol><li><span class="t-13 flex">` 结构）在同样 4 个宽度下逐字测量术语列，不得单字成行。**这是此前漏掉的盲区**：旧断言只覆盖档案页的 `ItemRow` / `.field-row` |
| `test_term_cell_does_not_shrink` | 静态断言 `.term-cell` 必须是 `flex: 0 0 auto` 且 `word-break: normal`；不许参与 flex 收缩 |
| `test_layout_gate_detects_squeezed_flex_term` | **反向对照**：术语列作为 `flex: 0 1 auto` 时被压到 26px（不到两个字宽）并逐字成行；改为 `flex: 0 0 auto` 后保持 ≥100px 单行。并断言两者宽度差 ≥3 倍 |
| `test_semantic_text_does_not_use_break_all` | 静态扫描 `apps/web/src/**/*.tsx`，`break-all` 不得用于语义文本；豁免仅限哈希/标识符（`.trace__value`、`payload_sha256`、`evidence_id`、`sha256`）与 `raw-value` 原文块 |

**修复做法**（两处独立缺陷，均已实测复现并修复）：

1. **档案页 `ItemRow`**：正文列改为 `flex: 1 1 14ch` + `min-width: min(100%, 14ch)`；正文用 `overflow-wrap: break-word` + `word-break: normal`；证据标识在 ≤720px 时整块换到下一行（`flex-basis: 100%`），不去挤正文。
2. **扫码页第 05 区块术语列**：术语 span 原先是可收缩 flex 子项（`flex-shrink: 1`、`min-width: auto`），CJK 的 min-content 只有 1 个字宽，父级 `min-w-0` 便允许它被压塌。改为 `.term-cell { flex: 0 0 auto }`，术语不参与收缩。

**实测数字**（280 / 320 / 360 / 390 / 480 / 768 / 1280 七个宽度）：配料术语 `全脂乳粉` 恒为 52px 单行、`乳清蛋白粉` 恒为 65px 单行，单字成行计数 **0**；反向对照中允许收缩时术语列被压到 **26px** 并逐字成行。

**已知局限（如实记录）**：档案页的 `test_chinese_terms_never_break_into_single_characters` 在**当前 demo 数据**下即使把 CSS 改回错误写法也仍然通过——demo 字段值短、证据标签不够长，压不到单字成行。该测试防的是「未来数据变长后复发」，不是「立刻能抓到本次改坏」。真正保证判定逻辑有效的是两条反向对照测试；其中术语列那条已验证：改回可收缩后 `test_term_cell_does_not_shrink` 与 `test_layout_gate_detects_squeezed_flex_term` 均会失败。

## 门禁 8b — `test_client_ocr.py`（4 个测试，真实浏览器）

**为什么单独存在**：门禁 8 只检查「有没有外发请求」，**从未真的上传过一张图片**。于是当端侧 OCR 彻底失效时——tesseract worker 因 `importScripts(blob:)` 被浏览器拒绝、core 与语言数据双双加载失败、异常被 `try/catch` 吞掉——页面不崩、不发请求、旧门禁全绿，而 OCR 实际不可用。门禁 8b 把「能识别出正确文字」本身变成断言。

**前置**：`apps/web/dist/index.html` 必须已构建，否则整个模块 `skip`。图片由浏览器自己渲染一张中文标签生成（`LABEL_HTML` 截图），不依赖任何二进制测试素材。

| 测试 | 断言 |
| --- | --- |
| `test_ocr_actually_recognizes_chinese_label` | 上传图片后，OCR 必须产出含「配料」锚点**且**含真实配料词（乳粉 / 乳清 / 磷脂）的文本；只出字但无配料词也算失败 |
| `test_ocr_ingredients_are_segmented` | 界面必须把配料切成短词条（每条 ≤ 40 字符）且含真实配料，证明机械切分链路通 |
| `test_ocr_run_makes_no_cross_site_requests` | 识别全程零跨站请求（worker / core / 语言数据全部内联），且 `document.cookie.length == 0` |
| `test_ocr_text_not_persisted_to_web_storage` | 识别原文只进 IndexedDB，不得出现在 `localStorage` / `sessionStorage` |

**怎么被执行**：真实 Chromium 上传真实 PNG 并等待真实识别，最长 60 秒。识别成功通常在 1–5 秒内完成。

**怎样会失败**：
- 把 core 做成 `blob:` URL 交给 worker（浏览器禁止 `importScripts` 读 `blob:`）；
- 语言数据未内联，回退到 jsdelivr CDN（会同时触发跨站请求断言失败）；
- 段落抽取把「致敏原 / 营养」段误当终止标记之前就截断，导致切不出配料；
- 把识别原文缓存进 `localStorage`。

**反向对照已验证**：把 core 改回 `blob:` URL 传递后，本门禁 3 项失败（识别不出字），证明它不是空转门禁。WebGPU 在门禁层面只被间接覆盖（「页面能正常渲染 + 零外发请求」）；`chrome-headless-shell` 下 `getCurrentTexture()` 不可用，代码会正确降级到 CSS 兜底，真实 GPU 路径需用探针脚本在完整 Chrome 中验证。

---

## 门禁 9 — `test_integrity_check.py`（9 个测试）

**断言什么**：账本与产物都可用密码学方式核验。

| 测试 | 断言 |
| --- | --- |
| `test_snapshot_hash_is_content_addressed` | 每条快照的 `payload_sha256` 等于重算值、长度 64、全为小写十六进制字符 |
| `test_same_content_reuses_evidence_id` | 相同内容两次写入 → 同一 `evidence_id`；第二次 `is_new is False`；账本只有 1 条快照 |
| `test_different_content_creates_new_evidence` | 不同内容 → 不同 `evidence_id`；新快照 `seq` 更大；账本 2 条 |
| `test_ledger_triggers_survive_reopen` | 关闭再重开账本后，触发器仍然存在（`executescript` 用 `CREATE TRIGGER IF NOT EXISTS` 幂等重建） |
| `test_tampered_snapshot_detected` | 直接开 sqlite3 连接、`DROP TRIGGER`、改 `payload_json`，重开账本调 `verify_integrity()` 必须抛 `LedgerError` |
| `test_builder_verifies_artifacts_on_write` | `artifacts` 表每条记录的路径存在，文件 SHA-256 与记录一致 |
| `test_manifest_declares_integrity_fields` | `build_id` / `registry_version` / `adapter_versions` 非空；`query_index` 的每个路径存在；每个 observation 的 `payload_sha256` 长度为 64 |
| `test_evidence_id_is_deterministic` | `evidence_id` 由内容决定（同内容同 ID），长度 64 的哈希本身可稳定重算 |
| `test_append_only_error_type` | 事务内 `UPDATE` 抛的是 `LedgerAppendOnlyError`（`LedgerError` 的子类），而不是裸 `IntegrityError` |

**怎样会失败**：
- 哈希计算用了非规范化序列化（`sort_keys` 关掉、缩进变化），导致同内容不同哈希；
- `evidence_id` 里掺了时间戳或随机数；
- 触发器在 schema 迁移中丢失；
- 产物写完后又被后续步骤改写（`artifacts` 记录的哈希就对不上了）。

---

## 门禁 10 — `test_zero_inference_gate.py`（10 个测试）

**断言什么**：语义门禁本身有效，且产物/源码/单文件全部干净。详见 `SEMANTIC_GATE.md`。

| 测试 | 断言 |
| --- | --- |
| `test_gate_blocks_every_adversarial_category` | 对抗语料至少 6 个类别；每类至少 1 条样本；每条样本至少产生 1 个 finding |
| `test_gate_does_not_flag_benign_copy` | 合规文案零 finding |
| `test_gate_blocks_risk_color_coding` | 风险配色样本必须被拦；`FORBIDDEN_COLOR_PATTERNS` 非空 |
| `test_static_artifacts_are_semantically_clean` | `scan_tree(dist_data)` 零 finding |
| `test_raw_value_exemption_is_narrow` | 同样文本在 `raw_value` 里不报，在 `note` 里必须报；finding 的 `node_path` 全部以 `.note` 结尾 |
| `test_web_source_has_no_banned_terms` | `scan_tree(apps/web/src)` 零 finding |
| `test_single_file_build_is_clean` | `apps/web/dist/index.html` 零 finding（未构建则 skip） |
| `test_banned_terms_cover_six_categories` | 7 个类别常量齐全 |
| `test_no_health_efficacy_claims_in_artifacts` | 产物中 `RAW_VALUE_KEYS` 之外不得出现 `治疗` / `治愈` / `预防疾病` / `增强免疫` / `降血糖` / `降血压` |
| `test_dev_field_does_not_leak_into_ui_copy` | 三个覆盖状态的中文说明不得触发门禁 |

**怎样会失败**：
- 写文案时用了被禁词（哪怕在注释里）；
- 用 Tailwind 的红色背景工具类给「未命中」状态上色（配色正则会把这种写法判为风险编码）；
- 把禁用词放进 `note` 或 `title` 字段；
- 规则被削弱（例如把 `RAW_VALUE_KEYS` 扩大到包含 `note`）；
- 新增类别却忘了更新 `expected` 集合。

---

## 门禁 11 — `test_diet_scaling_and_backup.py`（14 个测试，真实浏览器 + 源码扫描 + 门禁反向对照）

**为什么单独存在**：这不只是「再加一个功能」的验收。之前没有任何门禁证明过饮食计划的数值**真的算对了**，也没有门禁证明过「导入失败时原数据还在」。两类问题都是**静默失败**：折算写错乘数、校验写漏字段、非法文件覆盖掉用户数据，页面都不崩、旧门禁依然全绿，用户只会看到一串错误的数字。

**前置**：`apps/web/dist/index.html` 必须已构建，否则整个模块 `skip`。测试自己起 `python -m http.server` 指向 `apps/web/dist`，每个测试用独立 context（IndexedDB 从空开始）。

**固定样本**（演示条码 `6901234567892` 的每 100 克原文，取自 `v1/ingredients/ent_0fdf238ca1c25b41.json`）：能量 2130 千焦、蛋白质 24.5 克、脂肪 26.8 克、碳水 39.6 克、钠 280 毫克。按 50 g 折算应为 1065 kJ / 12.3 g / 13.4 g / 19.8 g / 140 mg。

| 测试 | 断言 |
| --- | --- |
| `test_diet_scaling_is_exact_arithmetic` | 载入档案后输入 50 g，界面必须逐项出现 `能量 1065kJ` / `钠 140mg` / `碳水化合物 19.8g` / `脂肪 13.4g` / `蛋白质 12.3g`；且不得出现「健康建议」「推荐摄入」「超标」等评价性措辞 |
| `test_diet_scaling_records_and_exposes_evidence` | 记录前必须看到「查看本次折算的营养来源与证据」；记录后今日累计必须变成 `已计入 1065 / 每日目标 8400 kJ`，条目保留 `· 50 g`；展开溯源必须出现营养原文的 `evidence_id`；直接读原生 IndexedDB，`logs[0]` 的 `grams` / `entity_id` / `nutrition_basis` / `evidence[0].evidence_id` 全部非空 |
| `test_diet_progress_over_target_is_neutral_text` | 把能量目标改成 1000 后必须出现中性句 `已超出目标 65 kJ`；必须并列「已计入 / 每日目标」；页面 HTML 不得出现 `text-red-` / `bg-amber-` / `text-green-` / `text-danger` 等语义配色；必须有 `hairline-meter` |
| `test_diet_scaling_refuses_unknown_basis` | 载入按「每 100 毫升」标注的条码（`6901112223339`）后，必须说明计量基准、必须写「本次记录未计入数值」、「记录」按钮必须置灰，IndexedDB 里不得出现 0 值占位记录 |
| `test_backup_export_is_local_json` | 导出必须生成含 `app` / `schema_version` / `diet_rules` / `scan_history` / `diet_plan.logs` 的 JSON；条目必须保留 `grams`；导出期间请求列表增量为 **0** |
| `test_backup_import_restores_after_clearing` | 删除唯一记录后导出 → 以「覆盖」导入，IndexedDB 里记录按原值回来，界面回显写入方式与条数 |
| `test_backup_import_merge_keeps_local_entries` | 本地已有「乳糖」、备份含「花生」，以「合并」导入后两者必须同时存在（`put` 按 id 并入，不抹掉本地条目） |
| `test_backup_import_rejects_invalid_without_data_loss` | 三种非法输入（`diet_rules` 非数组、`schema_version` 不匹配、非 JSON）都必须给出精确报错；每次都逐项比对导入前后的 IndexedDB 快照，要求**完全相等** |
| `test_diet_progress_bar_never_exceeds_track_width` | 超出目标时逐条测量进度条几何：填充宽度必须 ≤ 轨道宽度、右侧溢出 ≤ 0.5px、比例 ≤ 100%。并先断言「至少有一条达到满格」，否则场景未构造出来、门禁视为空转 |
| `test_diet_progress_bar_clamp_gate_detects_overflow` | **反向对照**：把填充宽度注入为 160%（模拟 `barWidth` 未截断），断言确实测出溢出。注意 `.hairline-meter__fill` 有 240ms 过渡，必须等过渡结束再量几何，否则量到起始值 |
| `test_backup_flow_makes_no_network_request` | 导出 + 导入全流程请求增量为 0，方法集合 ⊆ `{GET, HEAD}`，`document.cookie.length == 0` |
| `test_backup_modules_use_no_upload_primitive` | 源码级：`backup.ts` / `db.ts` / `nutrition_scaling.ts` / `DietPage.tsx` 不得出现 `navigator.sendBeacon` / `new FormData` / `multipart/form-data` / `XMLHttpRequest` / `WebSocket` |
| `test_upload_marker_gate_detects_injected_violation` | **反向对照**：把 `apps/web/src` 复制到 `tmp_path`，注入一行 `navigator.sendBeacon('/collect', json)`，同一扫描函数必须命中 `("local_store/leaky_backup.ts", "navigator.sendBeacon")` |
| `test_traffic_light_color_gate_detects_injected_violation` | **反向对照**：`scan_text` 对中性文案必须零 `RISK_COLOR_CODE`；对拼接出来的 `bg-red-500 风险` 必须命中；再断言真实 `apps/web/dist/index.html` 零命中 |

**反向对照如何证明有效**（这三种注入都实跑过）：

| 注入 | 结果 |
| --- | --- |
| `nutrition_scaling.ts` 把 `grams / basisAmount` 改成 `grams * 2 / basisAmount` | `test_diet_scaling_is_exact_arithmetic` 立刻失败（找不到 `能量 1065kJ`），还原后通过 |
| `tmp_path` 副本里写下 `navigator.sendBeacon('/collect', json)` | `test_upload_marker_gate_detects_injected_violation` 命中该文件与标记 |
| `scan_text("bg-red-500 风险")` | 产生 `RISK_COLOR_CODE` finding；同函数对中性文案与真实产物均为 0 |

**怎样会失败**：
- 乘数写错（`×克重` 而不是 `×克重/100`）、单位换算漏掉（千卡未折 kJ、克未折 mg）；
- 计量基准是每 100 毫升仍照 100 克算（凭空假设 1 mL = 1 g）；
- 营养表缺项时补 0 或补「推荐值」；
- 折算记录不落 `entity_id` / `evidence[]`，或溯源里取不到 `evidence_id`；
- 超出目标时改用红色、或写「超标警告」类措辞；
- 导入先 `clear()` 再校验（校验失败就永久丢了原数据）；
- 导入用 `fetch` / `sendBeacon` / `FormData` 把文件送出去；
- 合并模式用 `clear()` 实现，抹掉本地独有条目。

**已知局限（如实记录）**：
- 折算只覆盖「每 100 克」基准；按份（每份 X 克）、每 100 毫升、每 100 千焦等形态一律拒绝折算，而不是猜；
- 「同一营养素多条不一致原文」按并列处理不折算，因此这种档案的该项恒为未计入——这是刻意的（避免替读者选一条作准）；
- 反向对照注入 `sendBeacon` 的那次是**手工改动 + 立即还原**的临时验证，不是仓库里常驻的测试；常驻的 `test_upload_marker_gate_detects_injected_violation` 用 `tmp_path` 副本注入，不触碰 `apps/web/src`。

---

## 附加门禁 — `test_demo_origin_and_compare_focus.py`（8 个测试，真实浏览器 + 源码扫描）

**为什么单独存在**：这两件事都只能在「读者实际看到什么」的层面上证明。

1. **演示样例来源属性**：`v1/demo/index.json`（及打包进单文件的 `DEMO_FALLBACK` 常量）与 `v1/gtin|products|ingredients` 分片是**两种来源**。前者是合成演示数据，后者是静态档案库。若界面把两者画成同一种东西，读者会误以为「这个商品已被真实库收录」。标记必须由载荷里的 `origin` 字段驱动（`DEMO_FALLBACK` 才渲染），不得在前端硬编码演示条码清单——否则新增/删除演示条码时标记会静默失准。
2. **06 区块来源焦点**：条码静态档案配料（`bundle.ingredients`）与 OCR / 手动修正文本（`ocrText`）各自独立匹配。两份命中必须始终可分辨、同时可见；切换只改变主视图焦点，不隐藏任何一方，更不得把两份配料拼成一个列表当作结论。

**前置**：`apps/web/dist/index.html` 必须已构建。测试自己起 `python -m http.server` 指向 `apps/web/dist`。

第 1 个场景走真实静态分片（`v1/gtin/*.json` 正常返回，`origin = STATIC_LIBRARY`）；第 2 个场景用 `page.route` 阻断 `**/data/v1/gtin/*.json`，令 `lookupGtin` 真实回落到 `v1/demo/index.json`（`origin = DEMO_FALLBACK`）——不是在测试里塞一个假 origin。

| 测试 | 断言 |
| --- | --- |
| `test_demo_marks_visible_at_every_demo_display_location` | demo 兜底路径下，6 个展示演示数据的位置都必须有**可见**的 `[data-demo-mark]`：`scan-demo-list` / `scan-03-coverage` / `scan-04-candidates` / `scan-05-ingredients` / `scan-06-static` / `dossier-provenance`；文案必须含「演示样例」与「真实商品库」。多出的标记位置也算失败 |
| `test_static_library_results_carry_no_demo_mark` | 静态分片命中时，结果区（03/04/05/06）与档案页**不得**出现演示标记；只有演示样例按钮区（`scan-demo-list`，本身就是 demo 数据）允许出现 |
| `test_demo_marks_fail_when_marking_is_removed_or_leaked` | **反向对照（运行中页面）**：删掉 `scan-04-candidates` 的标记 → 「缺少可见的演示样例标记」；在静态结果栏插入假标记 → 「未预期的演示标记位置」 |
| `test_demo_marking_is_origin_driven_not_hardcoded` | 源码级：`ScanPage.tsx` / `DossierPage.tsx` / `ui.tsx` 不得把演示条码写进判定逻辑（`placeholder` 检索示例被显式剥除，不算违规）；每个 `<DemoMark` 渲染点前 400 字符内必须出现 `origin === "DEMO_FALLBACK"` 守卫 |
| `test_demo_marking_scanner_detects_injected_regressions` | **反向对照（源码扫描器）**：注入 `const leak = '6901234567892'` → 硬编码扫描命中；把 `origin === "DEMO_FALLBACK"` 替换成 `true` → 守卫扫描命中 |
| `test_compare_focus_label_and_switch_work` | 06 顶部必须有 `[data-compare-focus]` 与图例「当前对照来源」；两栏 `static_archive` / `ocr_text` 始终同时可见；radio 切换后 `data-compare-focus` 与实际 `is-focus` 栏一致，且可双向切回 |
| `test_compare_sources_keep_separate_hits_and_never_merge` | 两栏命中行各带 `data-hit-source` 且必须等于自己所在栏；**没有任何命中行在栏外**；静态档案栏不得出现 OCR 命中文案，反之亦然；切焦点后两栏命中条数不变（证明切换没隐藏或重算数据） |
| `test_compare_focus_fails_when_sources_are_merged_or_hidden` | **反向对照（运行中页面）**：`display:none` 隐藏 OCR 栏 → 「在切换后被隐藏」；把一条 OCR 命中行 append 进静态档案栏 → 「混入了另一份来源的命中行」 |

**实测数字**（`127.0.0.1` 随机端口 + 完整 Chromium 151）：

- demo 兜底场景下可见标记 = **5**（ScanPage）+ **1**（DossierPage 封面 provenance 区）；
- 静态库场景下可见标记 = **1**（仅演示按钮区），结果区 = **0**；
- 06 区块 panes = **2**，切换前后两栏 `hitCount` 不变（静态档案 3 / OCR 1）；
- 来源切换控件实测 `border-radius: 0px`、`box-shadow: none`（无胶囊、无投影）。

**怎样会失败**：
- 把 `DEMO_FALLBACK` 的样例直接画成与静态分片同一种展示（读者无法区分）；
- 在 `ScanPage` 里写 `if (gtin === "6901234567892")` 决定是否显示标记（新增演示条码即失效）；
- 06 区块切换用 `display:none` / 条件渲染隐藏另一栏；
- 把 OCR 命中拼进静态档案命中列表（合并成「最终结论」）；
- 演示标记使用填充色胶囊或红/绿/黄色。

**已知局限（如实记录）**：源码扫描器以 `placeholder` 为正则剥离边界，属启发式；若把演示条码写进模板字符串（如 `` `条码${"6901234567892"}` ``）仍会被抓到，但写进计算型拼接（如 `["6901234567" + "892"]`）则抓不到。浏览器侧断言不受此限——它只看真实渲染结果与真实 `origin` 取值。

---

## 附加门禁 — `test_demo_contract.py`（5 个测试）

**断言什么**：离线兜底样例与正式契约同构、可离线演进、不虚构数据。

| 测试 | 断言 |
| --- | --- |
| `test_demo_index_exists_and_is_offline_ready` | `v1/demo/index.json` 存在；`entries` 非空；`note` 非空（必须自我说明）；每个 entry 含必需键；`query_status ∈ {QUERIED_MATCHED, QUERIED_NO_MATCH}`；含候选与配料 |
| `test_demo_entries_match_official_contract` | 同一 GTIN 的 demo entry 与正式产物身份字段一致；`ingredients` 至少含 `schema_version` / `gtin` / `ingredients` / `nutrition_facts` / `disclaimer`；候选来源集合必须一致 |
| `test_demo_covers_subset_of_official` | demo 的条码集合必须是正式产物的**子集**；至少 2 个条码 |
| `test_demo_blocks_shapes` | 每个区块只能出现允许的键；`block_id` 与 `state` 非空 |
| `test_ingredients_contract` | 必填键齐全；`disclaimer` 非空；`order` 必须是整数 |

**怎样会失败**：
- demo 里塞了一个正式产物没有的条码（相当于虚构商品）；
- demo 结构与正式契约漂移（前端两条渲染路径会不一致）；
- 忘记写 `note`（读者无法知道这是样例）。

---

## 门禁与红线的对应

| 红线 | 主要守门测试 |
| --- | --- |
| 零推断 | 门禁 10（全部）、门禁 1 `test_no_final_truth_field` |
| 无最终真相字段 | 门禁 1 |
| 不自动合并 | 门禁 7、附加门禁 `test_compare_sources_keep_separate_hits_and_never_merge` |
| 端侧私有与零 Cookie | 门禁 8、门禁 8b、门禁 11（备份与折算全程零外发） |
| 中文词语不被逐字折断 | 门禁 8c |
| 合规采集与凭证隔离 | 门禁 6 |
| 历史不丢 | 门禁 4 |
| 可复现 | 门禁 5 |
| 内容可核验 | 门禁 9 |
| 字段不失真 | 门禁 2 |
| 缺口不隐藏 | 门禁 3、附加门禁 |

## 历史缺陷（已修复）

### 1. 静态站点服务进程泄漏，污染后续整组门禁

早期模具 fixture 只写 `server.terminate()`，当浏览器在 `yield` 之前崩溃时该清理被跳过。实测一次留下 **7 个** 残留 `http.server` 进程（端口 54338 / 54371 / 52572 / 52606 / 52636 / 53200 / 54918），随后的全量跑出现 **29 errors**（全部是浏览器 fixture 的 setup 失败，`pytest -q` 只打印 ERROR 行）。

修复：在 `_browser_launch.py` 增加 `static_site_server()` contextmanager，用随机空闲端口 + `wait(timeout=10)` + 超时 `kill()` 兜底；`test_layout_wrapping.py` 的 fixture 改为 `with static_site_server(...)` 并把 `browser.close()` 放进 `finally`。`launch_chromium` 重试次数 3 → 4、退避 0.4→0.5 秒。

修复后实测：连续两轮全量 **100 passed**（97.91s / 97.88s），跑完 `pgrep -f "http.server"` 计数 **0**。

另需说明的验证时序：提交 `79e38bd`（本应报 100 passed）的那一次跑，实际输出是 `71 passed, 29 errors`——29 个 error 全是上述浏览器 fixture setup 失败，而提交命令用 `&&` 串联，因此「测试未全绿」并未阻止提交发生。清理泄漏进程后重跑才得到 100 passed。该提交说明中的「全套门禁 97 → 100 passed」描述的是清理后的状态，不是提交当时的实测；此处如实补记，并把 CI 设为最终判据。

### 2. 扫码页术语列未被排版门禁覆盖

门禁 8c 原本只测档案页的 `ItemRow` / `.field-row`。扫码页第 05 区块是另一套结构（`<ol><li><span class="t-13 flex min-w-0">`），术语 span 作为可收缩 flex 子项（`flex-shrink: 1`、`min-width: auto`）在理论上可被压成单字宽，此前无任何断言。

实测（修复前）：在 280 / 320 / 360 / 390 / 480 / 768 / 1280 七个宽度下**当前 demo 数据未复现**单字成行（`全脂乳粉` 恒 52px、`乳清蛋白粉` 恒 65px），所以这是**潜在复发点**而非现存可见缺陷——如实记录，不夸大为「已修复的线上故障」。

修复：新增 `.term-cell { flex: 0 0 auto; overflow-wrap: break-word; word-break: normal }`；门禁 8c 从 3 项扩到 6 项，含静态断言与反向对照。反向验证：把 `.term-cell` 改回 `flex: 0 1 auto; min-width: 0`（保留 `word-break: normal`）后，`test_term_cell_does_not_shrink` 失败、反向对照测得术语列被压到 **26px** 并逐字成行；恢复后全绿。

### 3. 饮食进度条越界未被任何门禁覆盖

门禁 11 原有一条 `test_diet_progress_over_target_is_neutral_text`，只断言「超出时文字中性 + 无红黄绿配色」，**没有测几何**。实测把 `barWidth` 的截断去掉（改成 `Math.max(0, ratio)`）后，能量条以 106.5% 宽度渲染：填充 **766.8px** > 轨道 **720.0px**，右侧横向溢出 **46.8px**——窄屏下会直接把页面撑宽。这条缺陷此前完全静默。

修复：新增两条几何门禁。正向断言五条进度条全部满足 `fillWidth <= trackWidth + 0.5px`、`overflowPx <= 0.5px`、`ratio <= 100.5%`，且先确认至少一条达到满格（否则视为场景未构造、门禁空转）；反向对照把填充注入 160% 并断言测得出溢出。

反向验证实测：去掉截断后 `test_diet_progress_bar_never_exceeds_track_width` 失败（`填充 766.8px > 轨道 720.0px`），恢复后全绿。测试数 100 → 102。

## 已知的覆盖盲区

如实记录，便于后续补齐：

| 盲区 | 现状 |
| --- | --- |
| WebGPU 渲染路径 | `_find_full_chromium()` 被 `tests/visual_smoke_gpu.py` 与 `tests/a11y_probe.py` 探针脚本调用，但不属于 pytest 收集范围；headless shell 下只能间接验证「页面正常 + 零外发」 |
| `RIGHT` 分支覆盖率 | 未接入 `pytest-cov`，覆盖率数字不可得 || 端侧 `local_diet_matcher` 单测 | 无独立单元测试；仅通过门禁 8 的加载与门禁 10 的文案检查间接触及 |
| OCR 识别质量 | 门禁 8b 只证明「能出字、能切词、不出网」，**不证明识别准确率**；OCR 结果只进 `scan_history`，不参与展示字段 |
| 排版回归 | 门禁 8c 的档案页真实页断言依赖 demo 数据长度；数据不变长时压不出单字成行，需靠反向对照测试保证判定逻辑有效。扫码页术语列已补静态断言（`.term-cell` 必须 `flex: 0 0 auto`），改坏即可被抓到 |
| 演示标记的源码扫描 | 附加门禁只看 `<DemoMark>` 渲染点前 400 字符内的 `origin` 守卫；计算型拼接的演示条码抓不到。浏览器侧断言不受此限，它只看真实渲染与真实 `origin` |
| 06 区块切换的记忆 | 焦点处于组件状态（默认 `static_archive`），刷新页面即复位；切换不写入 IndexedDB，也不做跨页签同步 |
| 饮食折算的计量基准 | 只支持「每 100 克」。按份、每 100 毫升、每 100 千焦等其它基准一律拒绝折算（见门禁 11 `test_diet_scaling_refuses_unknown_basis`），因此这类档案无法记录 |
| 饮食目标的默认值 | `DEFAULT_TARGETS` 是代码内硬编码常量（8400 kJ / 60 g 蛋白 / 65 g 脂肪 / 300 g 碳水 / 2000 mg 钠），不是从任何来源取得的数值，用户可自行改成任意非负数 |
| 备份的跨浏览器迁移 | 只保证 JSON 结构与版本号；不保证跨浏览器版本的 IndexedDB 兼容，也不做备份内容迁移 |
| 多标签页并发写 IndexedDB | 仅代码里有 `blocked()` 警告，无测试 |
| 浏览器 fixture 的启动稳定性 | `launch_chromium` 最多尝试 4 次 + 两个候选内核，但内核级资源耗尽仍可能让整组浏览器门禁一起变成 setup ERROR。已知一次实测跑出 29 errors、原样重跑即 100 passed，因此**单次 errors 不可直接判定为回归**，需重跑确认 |
| `PPD_SPLIT=1` 静态目录模式 | 门禁 8 固定测 `apps/web/dist/index.html`；分离模式构建未单独验证 |
