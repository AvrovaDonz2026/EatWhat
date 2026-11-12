# 验收门禁

`tests/acceptance_gates/` 是十项硬门禁的实现。Pytest 当前收集到 **131 个测试**，分布在 16 个测试模块 + 1 个 `conftest.py` + 1 个浏览器启动助手 `_browser_launch.py`（实测命令 `.venv/bin/python -m pytest tests/acceptance_gates --collect-only -q`）。

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

## 门禁 8 — `test_privacy_and_zero_cookie.py`（7 个测试，真实浏览器）+ 门禁 8b — `test_client_ocr.py`（4 个测试，真实浏览器）+ 门禁 8c — `test_layout_wrapping.py`（6 个测试，真实浏览器）+ 门禁 8d — `test_mobile_comprehension.py`（10 个测试，真实浏览器）

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

---

## 门禁 8d — `test_mobile_comprehension.py`（10 个测试，真实浏览器）

**为什么单独存在**：本项目的主使用场景是「在超市拿手机扫码看」，读者是普通消费者。但此前所有排版断言只覆盖「文字不被折断」这一件事，**从未断言过「手机上读得下去」**。修复前实测（真实 Chromium，390px）：三页面 `documentElement.scrollWidth = 450 > clientWidth = 390`（横向可滚、内容被裁切）；可见文本节点里 ≤13px 占 **扫码 95.7% / 档案 92.9% / 饮食 97.1%**（正文主力就是 12px）；档案页 **17/17** 个可点元素小于 44px；档案页默认可见 **75 处** `ev_...` 标识（追溯密度被铺平在消费者屏上）。这四件事都不报错、不发请求、不崩，旧门禁全绿而手机上完全看不懂。

**前置**：`apps/web/dist/index.html` 必须已构建，否则整个模块 `skip`。用 `_browser_launch.py` 的 `static_site_server(DIST)` 起真实静态站，`launch_chromium(api)` 起真实内核，走真实静态分片与真实 demo 数据（不注入假数据）。

**测量口径**（全部 `page.evaluate` 现场计算，不读源码猜）：

| 指标 | 口径 |
| --- | --- |
| 横向溢出 | `documentElement.scrollWidth > clientWidth + 1`，外加「任意可见元素的 `rect.right − viewportWidth > 1.5px`」 |
| 触屏目标 | `button / a[href] / input / select / textarea / [role=button] / summary` 的 `getBoundingClientRect()`，宽或高 < 44−1px 记违规 |
| 字号 | `TreeWalker` 遍历可见文本节点（`text.length ≥ 2`、父元素 `display`/`visibility` 正常），按 `getComputedStyle().fontSize` 分桶 |
| 追溯暴露 | 可见文本节点里匹配 `ev_[0-9a-f]{6,}` 或 40+ 位十六进制哈希的条数 |
| 折叠入口 | `[data-fold-toggle]` / `.trace__summary` 的几何高度与文案长度 |

| 测试 | 断言 |
| --- | --- |
| `test_no_horizontal_scroll_at_mobile_widths` | 4 宽度（320/360/390/430）× 3 页面，`scrollWidth ≤ clientWidth + 1`，且无任何元素右侧越出视口；先断言 `checked == 12`（组合数不够直接失败） |
| `test_touch_targets_at_least_44px` | 同一 12 个组合，非豁免可点元素全部 ≥ 44×44；豁免只允许 `data-tap-exempt ∈ {visually-hidden-radio, native-radio}`，且**被豁免控件的外层 `label` 自身必须 ≥ 44×44**（否则豁免无效）、每页豁免数 ≤ 2 |
| `test_mobile_body_font_size_is_readable` | 每页至少 60 个可见文本节点（否则「样本不足，门禁无法成立」）；任意可见文本 ≥ 12px；390px 下出现最多的字号 ≥ 14px；≤13px 占比 ≤ 55% |
| `test_evidence_is_layered_on_mobile` | 390px 下三页面默认可见的 `evidence_id` / 40+ 位哈希为 **0** |
| `test_fold_entries_are_tappable_and_explicit` | 档案页至少 5 个折叠入口；每个入口高度 ≥ 44px、文案 ≥ 6 字、不得是「更多 / 详情 / 展开」这类含糊词；展开前登记原文（`国食注字YP20240001`）在区块内不可见，点击后 `aria-expanded=true` 且该值可见，同时 `section[data-block-id]` 数量仍为 16 |
| `test_expanding_trace_reveals_evidence` | 默认视图 `body.innerText` 里 `ev_` 出现 0 次；点开任一 `.trace__summary` 后必须真的出现 `ev_...`（证明是折叠而不是删除） |

**反向对照（4 条，均只做运行中 DOM 注入，不修改 `apps/web/src`）**：

| 测试 | 注入内容 | 实测失败输出 |
| --- | --- | --- |
| `test_overflow_gate_detects_injected_500px_block` | 追加 `width:500px` 的空 `div` | `scrollWidth=500 > clientWidth=390`；`<DIV> 右侧越出视口 110px` |
| `test_font_gate_detects_injected_11px_body` | 追加一段 `font-size:11px` 的正文 | `存在小于 12.0px 的可见文本：[(11, '这一段是故意注入的 11px 正文…')]` |
| `test_layering_gate_detects_trace_moved_out_of_fold` | 追加 `ev_77dfe41a85bfec068604` 明文段落 | 默认可见追溯条数 `0 → 1`，判定报「应全部折叠」 |
| `test_tap_gate_detects_injected_small_button` | 追加 `24×20` 的 `button` | `<BUTTON> 24×20 < 44.0×44.0（'注入的小按钮'）` |

**怎样会失败**：
- 把 `.m-only` / `.m-desktop-only` 的可见性开关写反（手机上边注 + 追溯一起铺开）；
- 在手机上保留任何 `white-space: nowrap` 的长 ID / 哈希；
- 把 `.t-12` 的移动端字号改回 12px，或把 12px 用在正文段落上；
- 把长尾区块从 `MobileFold` 里移出来直接渲染（分层断言 + 折叠入口断言同时失败）；
- 为了「好点」而给 `data-tap-exempt` 加滥用白名单（豁免上限 + 外层 label 尺寸断言拦下）。

**已知局限（如实记录）**：
- 字号断言按「文本节点数」加权，一个 200 字长段落与一个 3 字日期各算 1 票；因此它证明的是「小字不再是主力」，**不证明每一段正文都够长**。
- 「看不懂」的另一半是信息组织（先读什么、什么可以后读），本门禁只断言「默认可见的追溯内容为 0」「长尾区块默认收起」这两条可量化的代理指标，**不证明读者真的能按预期顺序理解**。真实理解度需要用户测试，不在门禁能力范围内。
- 触屏目标只测几何尺寸，不测「是否被相邻元素遮挡」或「滚动中是否误触」。
- 折叠入口的「文案 ≥ 6 字」只能拦住「更多」这类空话，拦不住「文案很长但语义含糊」。
- `test_fold_entries_are_tappable_and_explicit` 依赖 demo 数据里的具体字符串 `国食注字YP20240001`；demo 数据换掉后需要同步改这个样本值。

---

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

## 门禁 11 — `test_diet_scaling_and_backup.py`（33 个测试，真实浏览器 + 源码扫描 + 门禁反向对照）

**为什么单独存在**：这不只是「再加一个功能」的验收。之前没有任何门禁证明过饮食计划的数值**真的算对了**，也没有门禁证明过「导入失败时原数据还在」。两类问题都是**静默失败**：折算写错乘数、校验写漏字段、非法文件覆盖掉用户数据，页面都不崩、旧门禁依然全绿，用户只会看到一串错误的数字。

**前置**：`apps/web/dist/index.html` 必须已构建，否则整个模块 `skip`。测试自己起 `python -m http.server` 指向 `apps/web/dist`，每个测试用独立 context（IndexedDB 从空开始）。

**固定样本**（演示条码 `6901234567892` 的每 100 克原文，取自 `v1/ingredients/ent_0fdf238ca1c25b41.json`）：能量 2130 千焦、蛋白质 24.5 克、脂肪 26.8 克、碳水 39.6 克、钠 280 毫克。按 50 g 折算应为 1065 kJ / 12.3 g / 13.4 g / 19.8 g / 140 mg。

**参考值口径**（`apps/web/src/local_store/db.ts`）：`DEFAULT_TARGETS` 取自 GB 28050—2011《预包装食品营养标签通则》附录 A 表 A.1 的营养素参考值（NRV）：能量 8400 kJ、蛋白质 60 g、脂肪 **≤60 g**、碳水化合物 300 g、钠 2000 mg。脂肪在标准表里写作上限「≤60 g」，标准问答第（三十六）条明确计算 NRV% 时按 **60** 计算，因此 `DEFAULT_TARGETS.fatG` 存数字 `60`，上限语义只由只读展示常量 `NRV_IS_UPPER_BOUND`（仅 `fatG` 为 `true`）在文案层表达 `≤`。`DietTargets` 仍是 `Record<NutrientKey, number>`，索引库 schema 与 `backup.ts` 的 `NUMERIC_KEYS` / `validatePlan` 一字未改，旧备份仍可导入。

标准第 2.6 条把 NRV 定义为「专用于食品营养标签，用于比较食品营养成分含量的参考值」，**不是个人每日摄入目标**。因此界面按**逐项**比较 `plan.targets[key] === DEFAULT_TARGETS[key]` 判定归属（不存标记位、不进备份、不入静态产物）：相等的项挂 `参考值` + `NRV` + 区块级 `GB 28050—2011 附录 A` 出处；被改过的项退回 `自设值`，不出现 `≤` / `参考值` / `NRV` / `GB 28050`。区块标题随之在三种状态间切换（全为默认 → `营养素参考值（NRV · GB 28050—2011 附录 A）· 可自行修改`；部分改动 → `参考值 / 自设值（可自行修改）`；全改 → `自设值（可自行修改）`）。

**百分比口径**（`apps/web/src/engine/nutrition_scaling.ts` 的 `formatRatioPercent()`）：`ratio = 已计入 / 参考值（或自设值）× 100`，`Math.round(ratio)` 按 **修约间隔 1**（整数，GB 28050—2011 第 A.2 条）取整，不再有一位小数；`ratio ≥ 1e21` 时 `toFixed(0)` 会退回指数记法，改为输出不分组整数。`NaN` / `±Infinity` 返回 `null`，界面**不渲染**该段。对照值为 0 时前端根本不调用此函数（避免除零），文案保持「未设置参考值或自设值；仅记录已计入数值。」。称谓也随之切换：参考值态 `NRV <n>%`，自设值态 `占自设值 <n>%`。

**舍入的两层口径必须分清**：百分比用**落盘真值**算（`scaledNutrition` 保留到小数点后 2 位），「已计入」按营养素展示精度取整（kJ / mg 取整、g 取一位小数）。所以蛋白质这类真值 12.25 g 的项，界面写 `12.3 g`，百分比是 `12.25 / 60 × 100 = 20.4166… → 20%`（不是用 12.3 复算的 21%）。门禁从 IndexedDB 读落盘总量算预期值，并断言界面显示值与落盘值之差不超过一个展示步长。

| 测试 | 断言 |
| --- | --- |
| `test_diet_scaling_is_exact_arithmetic` | 载入档案后输入 50 g，界面必须逐项出现 `能量 1065kJ` / `钠 140mg` / `碳水化合物 19.8g` / `脂肪 13.4g` / `蛋白质 12.3g`；且不得出现「健康建议」「推荐摄入」「超标」等评价性措辞 |
| `test_diet_scaling_records_and_exposes_evidence` | 记录前必须看到「查看本次折算的营养来源与证据」；记录后今日累计必须变成 `已计入 1065 / 参考值 8400 kJ`，条目保留 `· 50 g`；展开溯源必须出现营养原文的 `evidence_id`；直接读原生 IndexedDB，`logs[0]` 的 `grams` / `entity_id` / `nutrition_basis` / `evidence[0].evidence_id` 全部非空 |
| `test_diet_progress_over_target_is_neutral_text` | 把能量对照值改成 1000 后必须出现中性句 `已超出目标 65 kJ`；必须并列「已计入 / 自设值」；页面 HTML 不得出现 `text-red-` / `bg-amber-` / `text-green-` / `text-danger` 等语义配色；必须有 `hairline-meter` |
| `test_diet_scaling_refuses_unknown_basis` | 载入按「每 100 毫升」标注的条码（`6901112223339`）后，必须说明计量基准、必须写「本次记录未计入数值」、「记录」按钮必须置灰，IndexedDB 里不得出现 0 值占位记录 |
| `test_backup_export_is_local_json` | 导出必须生成含 `app` / `schema_version` / `diet_rules` / `scan_history` / `diet_plan.logs` 的 JSON；条目必须保留 `grams`；导出期间请求列表增量为 **0** |
| `test_backup_import_restores_after_clearing` | 删除唯一记录后导出 → 以「覆盖」导入，IndexedDB 里记录按原值回来，界面回显写入方式与条数 |
| `test_backup_import_merge_keeps_local_entries` | 本地已有「乳糖」、备份含「花生」，以「合并」导入后两者必须同时存在（`put` 按 id 并入，不抹掉本地条目） |
| `test_backup_import_rejects_invalid_without_data_loss` | 三种非法输入（`diet_rules` 非数组、`schema_version` 不匹配、非 JSON）都必须给出精确报错；每次都逐项比对导入前后的 IndexedDB 快照，要求**完全相等** |
| `test_diet_progress_bar_never_exceeds_track_width` | 超出目标时逐条测量进度条几何：填充宽度必须 ≤ 轨道宽度、右侧溢出 ≤ 0.5px、比例 ≤ 100%。并先断言「至少有一条达到满格」，否则场景未构造出来、门禁视为空转 |
| `test_diet_progress_bar_clamp_gate_detects_overflow` | **反向对照**：把填充宽度注入为 160%（模拟 `barWidth` 未截断），断言确实测出溢出。注意 `.hairline-meter__fill` 有 240ms 过渡，必须等过渡结束再量几何，否则量到起始值 |
| `test_diet_meter_tick_marks_full_scale_without_semantic_color` | 100% 刻度线的正向判定。**未超出**场景（能量目标 8400 > 已计入 1065）先断言五条进度条的刻度线全部存在、`display`/`visibility` 正常且几何尺寸非零、`border-radius: 0px`、`box-shadow: none`、刻度线颜色为中性墨色且足够深（`rgb(r,g,b)` 的 max−min ≤ 32；相对亮度 ≤ 0.85，纸色 `#fbfaf6` ≈ 0.98，`--ink-faint` `#5e6d75` 实测色度差 23、亮度 0.417）、`fillRight <= tickRight + 0.5px`，并断言至少有一条填充比例 < 95%（证明刻度线是标尺而非满格专有）；**已超出**场景（能量目标 1000 < 已计入 1065）重复同一组判定，并要求至少一条填充比例 ≥ 99%（填充顶到刻度线）；同时页面 HTML 不得出现 `text-red-` / `bg-red` / `text-amber-` / `bg-amber` / `text-yellow-` / `bg-yellow` / `text-green-` / `bg-green` / `text-danger` |
| `test_meter_tick_gate_detects_hidden_tick` | **反向对照**：先确认基线通过，再 `page.evaluate` 把全部 `.hairline-meter__tick` 的 `display` 置为 `none`，同一判定必须报出「刻度线不可见」或「缺少 100% 刻度线」。不修改 `apps/web/src` |
| `test_meter_tick_gate_detects_fill_past_tick` | **反向对照**：把全部 `.hairline-meter__fill` 的宽度注入为 `160%`，等 `400ms`（240ms 过渡走完）后同一判定必须报出「填充越过刻度线」。不修改 `apps/web/src` |
| `test_meter_tick_gate_detects_semantic_color_injection` | **反向对照**：把刻度线改成 `rgb(200, 40, 46)`（红）必须被「不是中性墨色」命中；改成纸色 `rgb(251, 250, 246)`（色度差合格但亮度不合格）必须被「太浅」命中。证明中性色与可见性的两条判定都不是空转 |
| `test_backup_flow_makes_no_network_request` | 导出 + 导入全流程请求增量为 0，方法集合 ⊆ `{GET, HEAD}`，`document.cookie.length == 0` |
| `test_backup_modules_use_no_upload_primitive` | 源码级：`backup.ts` / `db.ts` / `nutrition_scaling.ts` / `DietPage.tsx` 不得出现 `navigator.sendBeacon` / `new FormData` / `multipart/form-data` / `XMLHttpRequest` / `WebSocket` |
| `test_upload_marker_gate_detects_injected_violation` | **反向对照**：把 `apps/web/src` 复制到 `tmp_path`，注入一行 `navigator.sendBeacon('/collect', json)`，同一扫描函数必须命中 `("local_store/leaky_backup.ts", "navigator.sendBeacon")` |
| `test_traffic_light_color_gate_detects_injected_violation` | **反向对照**：`scan_text` 对中性文案必须零 `RISK_COLOR_CODE`；对拼接出来的 `bg-red-500 风险` 必须命中；再断言真实 `apps/web/dist/index.html` 零命中 |
| `test_diet_percent_caption_is_arithmetic_below_target` | **正向（未超出也要有百分比）**。同一测试覆盖两个场景：① 新页面 `current == 0` 且有参考值，能量行文字层必须正好是 `未超出该参考值 · NRV 0%`；② 记录 50 g 后（已计入 1065 / 参考值 8400），五行文字层全部通过完整判定，且能量行百分比等于 `13`（`1065 / 8400 × 100 = 12.6785…` 按修约间隔 1 取整）。期望值从 IndexedDB 落盘总量 + 对照值现算，不读界面文本、不读源码常量 |
| `test_diet_percent_caption_coexists_with_neutral_overflow_sentence` | **正向（超出态两句话并存 + 刚好达标）**。把能量对照值改成 1000（已计入 1065）后，能量行文字层必须**同时**含 `已超出目标 65 kJ` 与 `占自设值 107%`（`1065 / 1000 × 100 = 106.5` 按修约间隔 1 四舍五入为 `107`，不是截断的 106），且 `已超出目标 65 kJ` 仍出现在正文里；再把对照值改成 1065（刚好达标，ratio = 100%）→ 文字层必须正好是 `未超出当前自设值 · 占自设值 100%`（该项已脱钩为自设值）。这一步直接证明「刚好达标 100%」与「超出 107%」在文字上可区分 |
| `test_diet_percent_caption_absent_when_target_is_zero` | **正向（除零防护）**。把能量对照值改成 0 后，能量行文字层必须正好是 `未设置参考值或自设值；仅记录已计入数值。`，不得出现任何百分比，行文本不得含 `Infinity` / `NaN` / `undefined`；同时断言其余四行**仍然**有百分比（证明不是把整层关掉来「通过」） |
| `test_diet_default_targets_are_gb28050_nrv_with_upper_bound_fat` | **正向（默认态出处可见 + 上限符）**。首次进入（IndexedDB 无 `diet_plan`）时，五行必须全部为 `nrv` 态：区块标题含 `GB 28050` 与 `NRV` 且**不含「每日目标」**；区块说明必须给出 `GB 28050—2011 附录 A` / `营养素参考值` 出处并写明「不是个人摄入量标准」；脂肪行必须显示 `已计入 0.0 / 参考值 ≤60.0 g`（有 `≤`），`data-diet-upper-bound="true"`；同时断言落盘（或默认计划）的 `targets` 仍是纯数字且 `fatG == 60`（schema 未因 `≤` 而变） |
| `test_diet_nrv_percent_rounding_interval_is_one` | **正向（修约间隔 1）**。未超出：`1065 / 8400 × 100 = 12.6785…` → 必须为 `13%`；超出：`1065 / 1000 × 100 = 106.5` → 必须为 `107%`，且页面正文不得出现 `106.5`。同一测试再跑完整文字层判定（含「百分比必须是整数」断言） |
| `test_diet_fat_percent_uses_60_not_65` | **正向（脂肪分母必须是 60）**。演示样品脂肪 26.8 g/100 g，输入克重 `111.9403` 使已计入正好 30.0 g：按 60 → `50%`，按旧的 65 → `46%`。断言脂肪行显示 `已计入 30.0` 且百分比为 `50%`，并断言说明句里不含 `46` |
| `test_diet_nrv_attribution_detaches_after_user_modification` | **正向（改过即脱钩，逐项粒度）**。把脂肪从 60 改成 70：落盘 `fatG == 70`；脂肪行转为 `self` 态（`data-diet-target-source="self"`、`data-diet-upper-bound="false"`），行内不得出现 `≤` / `参考值` / `NRV` / `GB 28050`，说明句必须是 `占自设值`；其余四项未改动，必须**仍**为 `nrv` 态（证明是逐项判定而不是整组一刀切） |
| `test_diet_percent_caption_is_neutral_typography` | **正向（排版中性）**。五行百分比都必须是独立文本节点、前景色 max−min ≤ 32（实测 `rgb(85, 100, 107)`，色度差 22）、`border-radius: 0px`、`box-shadow: none`；页面正文不得出现 `超标` / `警告` / `过量` / `注意` / `应当` / `健康` / `危险` / `正常` / `异常` / `良好` / `不足`（`建议` / `风险` 只在五行文字层内断言，因为现有免责句合法含「不构成任何营养建议」「不含风险评分」）；HTML 不得出现 `text-red-` / `bg-amber-` / `text-green-` / `text-danger` 等工具类 |
| `test_diet_percent_caption_stays_inside_container_at_extreme_ratio` | **正向（极端比例 + 零横向滚动）**。对照值改 1（已计入 1065）→ `已超出目标 1064 kJ · 占自设值 106500%`；再在 390px 视口下把对照值改成 0.01 → `占自设值 10650000%`，此时 `documentElement.scrollWidth == clientWidth`（实测 390 / 390）、文字层右缘 370 == 行容器右缘 370、文字层自身 `scrollWidth == clientWidth`（350 / 350），且百分比为整数 |
| `test_diet_nrv_gate_detects_decimal_percent_injection` | **反向对照（修约间隔）**：把能量行百分比文本改成 `NRV 12.7%`（模拟退回 0.1 精度），同一判定必须命中「百分比 12.7% ≠ 算术预期 13%」与「百分比不是整数（修约间隔应为 1）」 |
| `test_diet_nrv_gate_detects_missing_upper_bound_marker` | **反向对照（上限符）**：把脂肪行 `.mono` 文本里的 `≤` 去掉（变成 `参考值 60.0 g`），必须命中「脂肪行必须显示上限符「≤60 g」」 |
| `test_diet_nrv_gate_detects_modified_value_still_attributed_to_standard` | **反向对照（脱钩）**：先把脂肪改成 70（界面已脱钩），再手工把该行 `data-diet-target-source` 改回 `nrv`、文本改回 `参考值 ≤70.0 g` —— 必须命中「fatG 落盘对照值 70.0 应标 self，界面标为 'nrv'（把改过的值仍挂在标准出处上）」。判定读的是 IndexedDB 落盘值，不是界面自述 |
| `test_diet_percent_gate_detects_evaluative_word_injection` | **反向对照**：把第一行文字层 `textContent` 换成 `超标 107%`，同一判定必须命中「进度条文字层出现评价性词汇：「超标」」（实测同时命中三条：`有目标（1000）却给出 0 个百分比`、`文字层…「超标」`、`页面正文…「超标」`） |
| `test_diet_percent_gate_detects_fabricated_value_injection` | **反向对照**：把文字层换成 `占自设值 Infinity % NaN undefined`，必须命中 `Infinity` / `NaN` / `undefined`（实测命中 6 条：文字层 3 条 + 页面正文 3 条） |
| `test_diet_percent_gate_detects_percent_injected_at_zero_target` | **反向对照**：先把对照值改 0（基线判定为空），再手工 `appendChild` 一个 `占自设值 0%` 的 `.mono` span，必须命中「目标为 0（未设置）却出现百分比 0%」 |

**反向对照如何证明有效**（这三种注入都实跑过）：

| 注入 | 结果 |
| --- | --- |
| `nutrition_scaling.ts` 把 `grams / basisAmount` 改成 `grams * 2 / basisAmount` | `test_diet_scaling_is_exact_arithmetic` 立刻失败（找不到 `能量 1065kJ`），还原后通过 |
| `tmp_path` 副本里写下 `navigator.sendBeacon('/collect', json)` | `test_upload_marker_gate_detects_injected_violation` 命中该文件与标记 |
| `scan_text("bg-red-500 风险")` | 产生 `RISK_COLOR_CODE` finding；同函数对中性文案与真实产物均为 0 |
| 页面内把 `.hairline-meter__tick` 的 `display` 置为 `none` | `test_meter_tick_gate_detects_hidden_tick` 报「刻度线不可见」，证明可见性判定有效 |
| 页面内把 `.hairline-meter__fill` 宽度注入 `160%`（等 400ms 过渡） | `test_meter_tick_gate_detects_fill_past_tick` 报「填充越过刻度线」，证明几何判定有效 |
| 页面内把刻度线染成 `rgb(200, 40, 46)` / 调成纸色 `rgb(251, 250, 246)` | `test_meter_tick_gate_detects_semantic_color_injection` 分别报「不是中性墨色」与「太浅」，证明中性色与可见性判定有效 |
| 页面内把能量行百分比改成 `NRV 12.7%` | `test_diet_nrv_gate_detects_decimal_percent_injection` 报 `energyKj 百分比 12.7% ≠ 算术预期 13%（已计入 1065 / 目标 8400）` 与 `energyKj 百分比不是整数（修约间隔应为 1）：12.7%` |
| 页面内去掉脂肪行文本里的 `≤` | `test_diet_nrv_gate_detects_missing_upper_bound_marker` 报 `脂肪行必须显示上限符「≤60 g」，实际：脂肪 已计入 13.4 / 参考值 60.0 g 未超出该参考值 · NRV 22%` |
| 脂肪改成 70 后手工把该行标回 `nrv` + `参考值 ≤70.0 g` | `test_diet_nrv_gate_detects_modified_value_still_attributed_to_standard` 报 `fatG 落盘对照值 70.0 应标 self，界面标为 'nrv'（把改过的值仍挂在标准出处上）` 等 6 条 |
| 页面内把文字层换成 `超标 107%` | `test_diet_percent_gate_detects_evaluative_word_injection` 报 `进度条文字层出现评价性词汇：「超标」` + `页面正文出现评价性词汇：「超标」` + `energyKj 有目标（1000）却给出 0 个百分比：超标 107%`，共 3 条 |
| 页面内把文字层换成 `占自设值 Infinity % NaN undefined` | `test_diet_percent_gate_detects_fabricated_value_injection` 报 6 条：`伪造/非法值：Infinity`、`：NaN`、`：undefined`（文字层 3 条 + 页面正文 3 条） |
| 目标改 0 后手工 `appendChild` 一个 `占自设值 0%` | `test_diet_percent_gate_detects_percent_injected_at_zero_target` 报 `energyKj 目标为 0（未设置）却出现百分比 0%` |

**怎样会失败**：
- 乘数写错（`×克重` 而不是 `×克重/100`）、单位换算漏掉（千卡未折 kJ、克未折 mg）；
- 计量基准是每 100 毫升仍照 100 克算（凭空假设 1 mL = 1 g）；
- 营养表缺项时补 0 或补「推荐值」；
- 折算记录不落 `entity_id` / `evidence[]`，或溯源里取不到 `evidence_id`；
- 超出目标时改用红色、或写「超标警告」类措辞；
- 删除 100% 刻度线、把它改成圆角/投影/红黄绿/纸色过浅（正向判定直接抓），或让填充越过刻度线（注入对照抓）；
- 百分比只在超出时出现（正向断言要求未超出时也有，`current == 0` 时必须是 `0%`）；
- 百分比用「已计入」的展示取整值复算（会把蛋白质算成 21% 而不是落盘真值的 20%），或带小数位（修约间隔必须是 1）；
- 对照值为 0 时仍渲染百分比、或渲染 `Infinity` / `NaN` / `undefined`；
- 百分比文字带红/黄/绿、圆角、投影、感叹号，或附近出现「超标」类评价性词汇；
- 把用户改过的数值仍标成 `参考值` / `NRV` / `GB 28050`（伪造出处，比原来的硬编码更糟）；
- 脂肪行漏掉上限符 `≤`（默认态），或在用户改过脂肪后仍显示 `≤`；
- 脂肪百分比用旧值 65 而不是标准要求的 60 计算（30 g 会算成 46% 而不是 50%）；
- 对照值极小（1 / 0.01）时百分比把容器撑出横向滚动；
- 导入先 `clear()` 再校验（校验失败就永久丢了原数据）；
- 导入用 `fetch` / `sendBeacon` / `FormData` 把文件送出去；
- 合并模式用 `clear()` 实现，抹掉本地独有条目。

**已知局限（如实记录）**：
- 折算只覆盖「每 100 克」基准；按份（每份 X 克）、每 100 毫升、每 100 千焦等形态一律拒绝折算，而不是猜；
- 「同一营养素多条不一致原文」按并列处理不折算，因此这种档案的该项恒为未计入——这是刻意的（避免替读者选一条作准）；
- 反向对照注入 `sendBeacon` 的那次是**手工改动 + 立即还原**的临时验证，不是仓库里常驻的测试；常驻的 `test_upload_marker_gate_detects_injected_violation` 用 `tmp_path` 副本注入，不触碰 `apps/web/src`；
- 百分比按落盘真值算，而上方数值行按展示精度舍入。两者在「真值与展示值差超过半个步长」时会出现肉眼可察的错位：例如蛋白质真值 12.25 g 显示 `12.3 g`，百分比是 `20%`（按 12.25 算）而不是按 12.3 算的 `21%`。门禁断言的是「与落盘真值一致」，不是「与展示值一致」；
- 对照值小于该营养素展示精度时（例如能量对照值 `0.01`），数值行会显示 `自设值 0 kJ`（既有 `formatNutrient` 取整行为，本轮未改动），而同一条的百分比仍是 `10650000%`。两处口径不一致，属既有展示精度限制，未在本次修复；
- 百分比不参与任何等级判定，也不改变 `barWidth` 的封顶逻辑；图形层依然无法区分 100% 与 >100%；
- 归属判定是**逐项严格相等**：用户即使把某项改成恰好等于该表数值后又改回，仍会重新标为参考值——这是刻意的（比较的是当前值，不记录「是否曾被编辑」的历史）；`DietTargets` 不存额外标记，因此无法区分「从未改过」与「改成同一个值」；
- `≤` 只用于脂肪（`NRV_IS_UPPER_BOUND` 中唯一为 `true` 的项）；标准 A.1 表里其余四项是等式参考值。若将来标准修订引入其它上限项，必须同时改该常量与本节说明。

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

### 4. 进度条封顶后「刚好达标」与「超出」视觉不可区分

上一节把填充封顶在 100% 后，留下一个**可读性缺陷**：连续超出时宽度锁死在 100%，「刚好达标」与「超出 113%」在轨道上画出来完全一样，唯一区分信号是下方 `已超出目标 X kJ` 那行小字。这属于**静默的可读性回归**——几何门禁全绿，缺陷依然存在。

修复：在 `.hairline-meter` 轨道上新增 1px `100%` 刻度线 `.hairline-meter__tick`（`inset: 0 0 0 auto; width: 1px; background: var(--ink-faint); border-radius: 0; box-shadow: none; z-index: 1`），两种状态都渲染（它是标尺刻度，不是报警灯）。填充仍封顶在 100%（`barWidth = Math.min(100, Math.max(0, ratio))` 未改动），满格时恰好顶到刻度线，读者不必读小字也能看出「已到达自设上限」。刻度线用中性墨色 `#5e6d75`（色度差 23、相对亮度 0.417），未引入红/黄/绿语义色，也未新增任何评价性文案（`已超出目标 65 kJ` 保持原样）。

修复前后实测（真实 Chromium，1280 × 900，轨道宽 720.0px、轨道右缘 880.0px）：
- 未超出场景（能量目标 8400、已计入 1065）：填充右缘 **251.3px**，刻度线右缘 **880.0px**（1px 宽、6px 高、`border-radius: 0px`、`box-shadow: none`、`background: rgb(94, 109, 117)`）；
- 刚好达标场景（目标 1065）：填充右缘 **880.0px**，恰好顶到刻度线；
- 超出场景（目标 1000）：填充右缘仍是 **880.0px**，`ratio = 1.0`（`Math.min(100, …)` 未改动）。
- 修复前这三种状态的填充都是满格或按比值铺满轨道，没有任何「100% 位置」的参照物：未超出时也看不到「上限在哪」。

刻度线只区分「未达上限」与「已达/越过上限」；**「刚好达标」与「超出」在轨道上仍然都顶线**。这条盲区当时记入「已知的覆盖盲区」，后续在缺陷 6 里由文字层的纯算术百分比补齐。

门禁：`test_diet_meter_tick_marks_full_scale_without_semantic_color`（未超出 + 已超出双场景），外加三条注入式反向对照（隐藏刻度线 / 注入 160% 填充 / 把刻度线染红或调成纸色）。反向验证实测：`display: none` 后判定报「刻度线不可见」；注入 `160%` 并在 `400ms` 后测量，判定报「填充越过刻度线」；染料红报「不是中性墨色」、染纸色报「太浅」；恢复后全绿。测试数 102 → 106。

### 6. 「刚好达标」与「超出」只剩一行小字可区分——文字层缺算术比例

缺陷 4 的刻度线把「未达上限」与「已达上限」分开了，但 **ratio = 100% 与 ratio = 106.5% 在轨道上完全相同**（填充都封顶在 100%）。剩余唯一区别是下方小字 `未超出当前自设目标` vs `已超出目标 65 kJ`——读者必须逐字读才看得出差多少，而小字本身**不给出比例**，无法回答「超了多少」。这是纯可读性缺口：几何门禁全绿（填充确实封顶、确实不越线），缺陷依然存在。

修复：在**文字说明层**（不碰轨道、不新增任何图形元素）补一个纯算术百分比，文案 `占自设目标 <n>%`：

- 口径 `ratio = 已计入 / 自设目标 × 100`，`Math.round(ratio * 10) / 10` 四舍五入到 **0.1**，整数省略 `.0`；
- **所有有目标的场景都显示**（`current == 0` 时是 `0%`），不是只在超出时才出现的符号；
- 原有中性句一字未改：`未超出当前自设目标` / `已超出目标 X kJ` 保留，百分比用 ` · ` 并列在后面；
- 目标为 0 时不计算、不渲染（保留「未设置每日目标；仅记录已计入数值。」），不出现 `Infinity` / `NaN` / 伪造的 `0%`；
- 样式只用既有小字与等宽体，颜色 `rgb(85, 100, 107)`（`--ink-muted`，色度差 22）、`border-radius: 0px`、`box-shadow: none`。

修复前后实测（真实 Chromium，1280 × 900，演示条码 6901234567892 记录 50 g）：

| 场景 | 修复前文字层 | 修复后文字层 |
| --- | --- | --- |
| 刚打开（已计入 0 / 目标 8400） | `未超出当前自设目标` | `未超出当前自设目标 · 占自设目标 0%` |
| 未超出（已计入 1065 / 目标 8400） | `未超出当前自设目标` | `未超出当前自设目标 · 占自设目标 12.7%` |
| 刚好达标（已计入 1065 / 目标 1065） | `未超出当前自设目标` | `未超出当前自设目标 · 占自设目标 100%` |
| 超出（已计入 1065 / 目标 1000） | `已超出目标 65 kJ` | `已超出目标 65 kJ · 占自设目标 106.5%` |
| 目标为 0 | `未设置每日目标；仅记录已计入数值。` | 同左（不显示百分比） |
| 目标为 1 | `已超出目标 1064 kJ` | `已超出目标 1064 kJ · 占自设目标 106500%` |
| 目标为 0.01（390px 视口） | 无百分比可溢出 | `已超出目标 1065 kJ · 占自设目标 10650000%`；`scrollWidth == clientWidth == 390`、文字层右缘 370 == 行容器右缘 370、文字层 `scrollWidth == clientWidth == 350` |

门禁：门禁 11 从 18 项扩到 **26 项**，新增 5 条正向（未超出也要有百分比且含 `current == 0` 边界 / 超出态两句话并存 / 目标 0 不显示 / 中性排版 / 极端比例不溢出）与 3 条注入式反向对照（评价性词汇 / 伪造值 / 目标 0 手插百分比）。反向验证实测见门禁 11 章节的表格：注入 `超标 107%` 命中 3 条失败、注入 `Infinity % NaN undefined` 命中 6 条、目标 0 手插 `0%` 命中 1 条。测试数 116 → 124。

> 本节的 0.1 修约与「占自设目标」措辞已被缺陷 7 取代：修约间隔改为 1（GB 28050—2011 A.2），称谓改为 `NRV` / `占自设值`，`12.7%` → `13%`、`106.5%` → `107%`。

### 7. 饮食目标硬编码 65 g 脂肪，与 GB 28050 不符且无出处

`DEFAULT_TARGETS` 一直是代码内写死的常量，界面把它称作「每日目标 · 自设数值」。其中脂肪写的是 **65 g**，而 GB 28050—2011 附录 A 表 A.1 的 NRV 是 **≤60 g**（标准问答第（三十六）条另明确：计算 NRV% 时按 **60** 计算）。两个后果同时存在：

1. 数值本身与标准不符（多 5 g）；
2. 分母偏大 → 所有脂肪百分比系统性偏小：脂肪已计入 30 g 时，按 60 应为 **50%**，按 65 只有 **46.15% → 46%**。这是**静默的错误**：界面不报错，几何门禁全绿，读者看到的是一个算错的数字。

更根本的问题是口径：标准第 2.6 条把 NRV 定义为「专用于食品营养标签，用于比较食品营养成分含量的参考值」，它**不是个人每日摄入目标**。旧文案把它当「每日目标」，等于给一个比较用的参考值加上了个人摄入处方的意味。

修复：

- `DEFAULT_TARGETS.fatG` **65 → 60**；其余四项复核后本就等于标准值（8400 kJ / 60 g 蛋白 / 300 g 碳水 / 2000 mg 钠），保持不变；
- 新增只读展示常量 `NRV_IS_UPPER_BOUND`（仅 `fatG` 为 `true`）与 `isNrvValue()`，脂肪行显示 `≤60 g`；**落盘仍是数字 `60`**，`DietTargets` 类型、IndexedDB schema、`backup.ts` 的 `NUMERIC_KEYS` / `validatePlan` 一字未改，旧备份仍可导入；
- 归属**逐项**判定（`plan.targets[key] === DEFAULT_TARGETS[key]`）：相等的项标 `参考值` / `NRV` 并在区块层标出 `GB 28050—2011 附录 A`；被改过的项退回 `自设值`，不出现 `≤` / `参考值` / `NRV` / `GB 28050`。不新增标记位、不进备份、不入静态产物；
- 百分比修约间隔 0.1 → **1**（`Math.round(ratio)`），称谓 `占自设目标 n%` → `NRV n%`（默认态）/ `占自设值 n%`（改过态）；
- 顶部说明改为如实交代出处，并写明 NRV「不是个人摄入量标准」。注意：仓库语义门禁把「推荐」列入附录 B 禁用词，因此说明句采用「不是个人摄入量标准」「不构成个人摄入量依据」这类表述，**没有**改动门禁词表。

修复前后实测（真实 Chromium，1280×900，演示条码 6901234567892）：

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| 首次进入 | `每日目标 · 自设数值（可自行修改）`；脂肪 `已计入 0.0 / 每日目标 65.0 g` | `营养素参考值（NRV · GB 28050—2011 附录 A）· 可自行修改`；脂肪 `已计入 0.0 / 参考值 ≤60.0 g` |
| 未超出（已计入 1065） | `未超出当前自设目标 · 占自设目标 12.7%` | `未超出该参考值 · NRV 13%` |
| 超出（对照值 1000） | `已超出目标 65 kJ · 占自设目标 106.5%` | `已超出目标 65 kJ · 占自设值 107%` |
| 脂肪 30.0 g | `占自设目标 46.2%`（分母 65） | `NRV 50%`（分母 60） |
| 脂肪改成 70 | `未超出当前自设目标 · 占自设目标 0%`（仍称自设，但无脱钩判定） | `未超出当前自设值 · 占自设值 0%`，行内为 `自设值 70.0 g`，不出现 `≤` / `参考值` / `NRV` / `GB 28050` |

门禁：门禁 11 从 26 项扩到 **33 项**，新增 4 条正向（默认态 NRV 出处 + `≤60 g` / 修约间隔 1 / 脂肪分母 60 / 改过即脱钩）与 3 条注入式反向对照（一位小数百分比 / 去掉 `≤` / 把改过的值仍标 GB 28050）。反向验证实测见门禁 11 章节的表格。测试数 124 → 131。

### 5. 移动端整屏「读不下去」——旧门禁全绿，手机上完全看不懂

这是本轮最严重的一类缺陷：它不报错、不崩、不发请求，`test_layout_wrapping.py` 也只断言「汉字不被折成单字竖排」，于是**所有门禁全绿而真实手机屏上是一整片挤成团的小字**。用户反馈原话「这个 ui 落在手机上其实很难被人类看懂」。

实测环境：真实 Chromium（`_browser_launch.py` 的 `launch_chromium`），`static_site_server` 指向 `apps/web/dist`，真实 demo 数据（禾丰全脂乳粉 6901234567892），`page.evaluate` 现场测量，不读源码推测。

修复前实测（390px，三页面）：

| 页面 | ≤13px 可见文本占比 | `scrollWidth` / `clientWidth` | 右侧越出视口的元素 | <44×44 可点元素 | 默认可见 `evidence_id` | 来源行 / 正文行 |
| --- | --- | --- | --- | --- | --- | --- |
| 扫码页 | 95.7%（265 / 277） | 450 / 390（+60px） | 10 | 13 / 14 | 0 | 45 / 28（1.61 倍） |
| 档案页 | 92.9%（643 / 692） | 450 / 390（+60px） | 10 | 17 / 17 | **75** | 44 / 89 |
| 饮食页 | 97.1%（134 / 138） | 450 / 390（+60px） | 10 | 32 / 32 | 0 | 0 / 3 |

四个成因，各自可证伪：

1. **横向溢出**：三页面在 320 / 360 / 390 全部 `scrollWidth = 450`（文档比视口宽 60px）。根因是页眉审计标记容器的 `shrink-0`（不参与收缩）与 `.evidence-chip` 的 `white-space: nowrap`。
2. **字号塌陷**：正文主力是 12px 的 `.t-12`——「正文」与「日期 / 哈希 / 编号」同字号，读者没有层级线索。
3. **点不中**：档案页 17 个可点元素**全部**不足 44px（折叠入口 18px 高、页签 40.4px、演示条码 29.8px、忌口预设 28px）。
4. **证据密度铺平**：档案页默认可见 75 处 `ev_...` 标识；扫码页第 05 区块「来源文字行 / 商品信息行」为 45 : 28（1.61 倍）——消费者看配料表时，来源文字比配料本身还多。

修复（全部只在 ≤721px 的媒体查询里生效，宽屏语汇不动；CSS 追加在 `index.css` 末尾 `/* 移动端可读性（m-*） */` 段落内，不改动任何既有规则的基础定义）：

| 成因 | 做法 |
| --- | --- |
| 横向溢出 | 页眉容器 `shrink-0` → `min-w-0`；`.evidence-chip` 在手机上允许换行（`white-space: normal; overflow-wrap: anywhere`）；页签 `wrap` 成两行而不是横向裁切；`.m-cand-body` 在 320px 先占满整行、标签下移 |
| 字号塌陷 | `.t-12` / `.t-13` 抬到 **14px** 作为正文基准；`.t-14` → 15px；`.t-20` → 19px；真正的次要信息（日期、来源名、标识符、哈希、区块编号、状态标签）显式钉回 12–13px。**只分级不全局放大**，密度问题靠下面的折叠解决 |
| 单栏阅读流 | `.m-stack > * + *` 加发丝线 + 18/16px 间距；`.m-subhead` 加下划线小标题；`.archive-grid` / `.compare-grid` 强制单列；边注整块 `display: none` 并改由「本区块附注」折叠入口进入，不再夹在正文中间 |
| 点不中 | `button` / `a[href]` / `select` / `summary` / `.trace__summary` / `.source-focus__item` 统一 `min-height: 44px`（只加 padding，不放大字号） |
| 证据密度 | 新增 `ConsumerSource`：每个区块末尾**只出现一处**「来源中文名 + 采集日期」，逐条 `SourceLine` 在手机上隐藏；`TraceDisclosure` 的 `evidence_id` / `payload_sha256` 全值保持默认折叠 |
| 信息减负 | 新增 `MobileFold`：档案页 7 个长尾区块（注册 / 许可 / 厂商前缀 / 商标 / 标准引用 / 进出口 / 可见性事件）在手机上默认收起，入口 ≥44px 且文案写明「本节 X 的 N 项登记原文与字段取值」；宽屏不渲染按钮、内容全展开，审计视图密度不变 |

修复后实测（390px，同一套测量口径）：

| 页面 | ≤13px 占比 | `scrollWidth` / `clientWidth` | 越出视口元素 | <44px 可点 | 默认可见 `evidence_id` | 来源行 / 正文行 | 出现最多的字号 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 扫码页 | **42.8%**（101 / 236） | **390 / 390** | **0** | **0**（另有 2 个豁免原生 radio，外层 label ≥44） | 0 | 18 / 48（0.38 倍） | **14px** |
| 档案页 | **48.2%**（183 / 380） | **390 / 390** | **0** | **0** | **0**（原 75） | 59 / 156（0.38 倍） | **14px** |
| 饮食页 | **47.5%**（58 / 122） | **390 / 390** | **0** | **0**（另有 2 个豁免原生 radio） | 0 | 0 / 4 | **14px** |

四宽度全部零横向滚动：320 / 360 / 390 / 430 下三页面 `scrollWidth == clientWidth`（320/320、360/360、390/390、430/430）。字号分布（390px，`{字号: 可见文本节点数}`）：扫码页 `{12:62, 13:39, 14:111, 15:17, 19:6, 24:1}`、档案页 `{12:109, 13:74, 14:167, 15:11, 19:18, 24:1}`、饮食页 `{12:36, 13:22, 14:60, 19:3, 24:1}`——三页面的众数字号都是 14px。

「来源行 / 正文行」的口径是「`.source-line` 按高度估算的行数 ÷ `.t-13 / .field-value` 按高度估算的行数」，**对字号变化敏感**（字号抬升后同一段文字的行数会变多），所以这一列只用于说明量级变化（扫码页来源文字从「比正文多 1.6 倍」变成「不到正文的四成」），不宜当作精确的行数统计。

门禁：新增 `test_mobile_comprehension.py`（4 宽度 × 3 页面 + 4 条注入式反向对照），详见「门禁 8d」章节。反向验证实测见该章节的表格（500px 注入后 `scrollWidth=500 > 390`；11px 注入被点名；`ev_` 明文注入使可见追溯条数 0 → 1；24×20 按钮被点名）。

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
| 饮食目标的默认值 | **已改为 GB 28050 附录 A 表 A.1 的营养素参考值（NRV）**：能量 8400 kJ、蛋白质 60 g、脂肪 **≤60 g**（标准问答第（三十六）条：计算 NRV% 时按 60 计）、碳水化合物 300 g、钠 2000 mg。默认态界面标出 `GB 28050—2011 附录 A` 出处并使用 `NRV` 称谓；用户改动某项后该项退回「自设值」口径，不再引用该标准（逐项判定，`plan.targets[key] === DEFAULT_TARGETS[key]`，不存标记位）。**已知局限**：判定是严格相等比较，`DietTargets` 不存「是否曾被编辑」的历史，因此「改成恰好等于 NRV 的值」仍会被视为参考值；`≤` 只由只读常量 `NRV_IS_UPPER_BOUND`（仅 `fatG`）在文案层表达，落盘仍是数字 `60`，schema 与备份格式未变 |
| 饮食进度条的「达标」与「超出」区分 | **图形层仍相同，文字层已用纯算术百分比区分**。100% 刻度线只把「未达上限」与「已达 / 已越过上限」分开；「刚好落在刻度线上（ratio = 100%）」与「超出后填充封顶」在轨道上依然都是顶线，轨道上不再有任何可区分信号（本轮按要求不新增图形元素）。区别改由下方文字层的纯算术百分比承载：`ratio = 已计入 / 参考值（或自设值）× 100`，按 GB 28050—2011 第 A.2 条修约间隔 **1** 取整；默认态显示 `未超出该参考值 · NRV 13%`，刚好达标显示 `100%`，超出显示 `已超出目标 65 kJ · 占自设值 107%`（该项已脱钩）。该百分比**在所有有对照值的场景都显示**（不是「越线才出现的符号」），对照值为 0 时不显示。仍未覆盖的是：图形层本身依旧无法区分 100% 与 >100% |
| 备份的跨浏览器迁移 | 只保证 JSON 结构与版本号；不保证跨浏览器版本的 IndexedDB 兼容，也不做备份内容迁移 |
| 多标签页并发写 IndexedDB | 仅代码里有 `blocked()` 警告，无测试 |
| 浏览器 fixture 的启动稳定性 | `launch_chromium` 最多尝试 4 次 + 两个候选内核，但内核级资源耗尽仍可能让整组浏览器门禁一起变成 setup ERROR。已知一次实测跑出 29 errors、原样重跑即 100 passed，因此**单次 errors 不可直接判定为回归**，需重跑确认 |
| `PPD_SPLIT=1` 静态目录模式 | 门禁 8 固定测 `apps/web/dist/index.html`；分离模式构建未单独验证 |
| 移动端的「信息减负」只覆盖长尾区块 | 门禁 8d 断言的是「默认可见追溯内容为 0」「长尾区块默认收起」；档案页里非长尾区块（身份、配料、营养、致敏原、贮存）仍然全量展开，靠字号与分段降低密度，没有进一步折叠 |
| 移动端的理解度 | 门禁 8d 用字号分布与追溯暴露量做代理指标，**不证明普通消费者真的能按预期顺序读懂**；真实理解度需要用户测试 |
| 字号断言的加权口径 | 按可见文本节点数计票，一个长段落与一个短日期各算一票；能证明「小字不再是主力」，不证明「每一段正文都够长」 |
| 触屏目标的可遮挡性 | 只测几何尺寸，不测相邻元素遮挡、滚动中误触、以及「视觉上够大但热区被覆盖」的情况 |
| 折叠入口文案的语义质量 | 只断言 ≥6 字且不是「更多 / 详情 / 展开」，拦不住「文案很长但语义含糊」 |
| 移动端断点固定 720px | 门禁 8d 只测 320–430px 且断点写死 720px；平板竖屏（600–720px）与横屏（≥721px 但高度很小）未覆盖 |
