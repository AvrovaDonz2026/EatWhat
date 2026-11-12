# 验收门禁

`tests/acceptance_gates/` 是十项硬门禁的实现。Pytest 当前收集到 **70 个测试**，分布在 12 个测试模块 + 1 个 `conftest.py`。

> 注意：项目文档与计划书长期称「十项门禁」，而目录里有 **11** 个测试模块 —— 多出来的 `test_demo_contract.py` 是「离线兜底样例契约」门禁，与另外十项并列存在但不计入「十项」编号。本文按模块逐个记录，不为凑数做合并。

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

## 门禁 8 — `test_privacy_and_zero_cookie.py`（7 个测试，真实浏览器）

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

**注意**：模块里定义了 `_find_full_chromium()`（用于找带合成器的完整 Chromium，以验证 WebGPU 路径），但**当前没有任何测试调用它**。WebGPU 的实际验证只在「页面能正常渲染、无外发请求」这层间接覆盖；`chrome-headless-shell` 下 `getCurrentTexture()` 不可用，代码会正确降级到 CSS 兜底。

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
| 不自动合并 | 门禁 7 |
| 端侧私有与零 Cookie | 门禁 8 |
| 合规采集与凭证隔离 | 门禁 6 |
| 历史不丢 | 门禁 4 |
| 可复现 | 门禁 5 |
| 内容可核验 | 门禁 9 |
| 字段不失真 | 门禁 2 |
| 缺口不隐藏 | 门禁 3、附加门禁 |

## 已知的覆盖盲区

如实记录，便于后续补齐：

| 盲区 | 现状 |
| --- | --- |
| WebGPU 渲染路径 | `_find_full_chromium()` 已写好但无测试调用；headless shell 下只能间接验证「页面正常 + 零外发」 |
| `RIGHT` 分支覆盖率 | 未接入 `pytest-cov`，覆盖率数字不可得 || 端侧 `local_diet_matcher` 单测 | 无独立单元测试；仅通过门禁 8 的加载与门禁 10 的文案检查间接触及 |
| OCR 识别质量 | 无「识别准确率」门禁；OCR 结果只进 `scan_history`，不参与展示字段 |
| 多标签页并发写 IndexedDB | 仅代码里有 `blocked()` 警告，无测试 |
| `PPD_SPLIT=1` 静态目录模式 | 门禁 8 固定测 `apps/web/dist/index.html`；分离模式构建未单独验证 |
