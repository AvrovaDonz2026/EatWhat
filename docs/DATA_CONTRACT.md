# 静态数据契约（`/v1/`）

构建管线把账本快照编译为一组只读 JSON 分片，默认输出到 `dist_data/v1/`，再由 `scripts/sync-data.mjs` 同步到 `apps/web/dist/data/v1/` 供静态站点托管。

前端只通过 `apps/web/src/services/static_dossier_client.ts` 读取这些分片，且**只发 GET**、`credentials: "omit"`、`referrerPolicy: "no-referrer"`。

## 分片总览

| 路径 | 作用 | 生成位置 |
| --- | --- | --- |
| `v1/manifest.json` | 全局清单：构建信息、源接入状态、适配器版本、条码索引 | `emit_static.py` |
| `v1/gtin/{gtin}.json` | 单条码的候选并列与覆盖清单 | `emit_static.py` |
| `v1/products/{entity_id}.json` | 16 区块多源并列档案、观测、时间线、可见性事件 | `emit_static.py` + `dossier.py` |
| `v1/ingredients/{entity_id}.json` | 供端侧机械对照消费的纯客观配料/营养/致敏原 | `emit_static.py` |
| `v1/demo/index.json` | 离线兜底样例（与正式契约同构，内联完整数据） | `emit_static.py` |

`entity_id` 由 GTIN 决定：`entity_id_for(gtin) = "ent_" + sha256({"gtin": gtin})[:16]`。同一 GTIN 永远得到同一 entity，不同 GTIN 必然不同（见门禁 7）。

## 1. `v1/manifest.json`

真实产物摘录（`dist_data/v1/manifest.json`）：

```json
{
  "schema_version": "1.0",
  "build_id": "build_20261112_053200Z",
  "built_at": "2026-11-12T05:32:00Z",
  "registry_version": "1.0.0",
  "tool_version": "1.0.0",
  "adapter_versions": {
    "udi": "1.0.0", "cnipa": "1.0.0", "samr_std": "1.0.0", "samr_gov": "1.0.0",
    "cifer": "1.0.0", "gs1": "1.0.0", "label_transcript": "1.0.0"
  },
  "sources": [ { "source_id": "gs1", "access_mode": "OPEN_BULK", "…": "…" } ],
  "coverage_summary": { "OPEN_BULK": 3, "OPEN_API": 2, "ACCESS_PENDING": 1, "SOURCE_UNAVAILABLE": 1 },
  "query_index": [
    {
      "gtin": "6901234567892",
      "entity_id": "ent_0fdf238ca1c25b41",
      "paths": {
        "gtin": "v1/gtin/6901234567892.json",
        "product": "v1/products/ent_0fdf238ca1c25b41.json",
        "ingredients": "v1/ingredients/ent_0fdf238ca1c25b41.json"
      },
      "candidate_count": 5
    }
  ],
  "no_final_truth_field": true,
  "no_auto_merge": true,
  "zero_inference": true
}
```

> 上例中 `adapter_versions` 的键为 `udi` / `cnipa` / `samr_std` / `samr_gov` / `cifer` / `gs1` / `label_transcript`，此处为压缩展示。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | 契约版本，当前 `"1.0"` |
| `build_id` | string | 构建标识，形如 `build_YYYYMMDD_HHMMSSZ` |
| `built_at` | string | 构建完成时间（UTC，ISO 8601） |
| `registry_version` | string | 使用的策略注册表版本 |
| `tool_version` | string | `emit_static.py` 的 `TOOL_VERSION` |
| `adapter_versions` | object | 每个源的适配器版本，用于复现 |
| `sources` | array | **全部**登记源的公开信息（由 `SourcePolicy.as_manifest_entry()` 生成） |
| `coverage_summary` | object | 按 `access_mode` 计数的源数量 |
| `query_index` | array | 每个已构建条码 → 三类产物的相对路径与候选数量 |
| `no_final_truth_field` / `no_auto_merge` / `zero_inference` | boolean | 恒为 `true`，作为产物自描述 |

`sources[]` 的元素**不含** `credentials` 字段。`as_manifest_entry()` 只输出 `source_id`、`display_name`、`authority`、`jurisdiction`、`access_mode`、`policy_url`、`legal_basis`、`query_identifier_types`、`rate_limit`、`notes`。门禁 3 会断言 `"credentials" not in entry`。

## 2. `v1/gtin/{gtin}.json`

真实产物摘录（`dist_data/v1/gtin/6901234567892.json`，节选）：

```json
{
  "schema_version": "1.0",
  "build_id": "build_20261112_053200Z",
  "built_at": "2026-11-12T05:32:00Z",
  "gtin": "6901234567892",
  "entity_id": "ent_0fdf238ca1c25b41",
  "query_status": "QUERIED_MATCHED",
  "query_status_note": "当前已接入静态档案库已匹配到记录",
  "match_candidates": [
    {
      "candidate_id": "cand_fd4dda7dba",
      "source_id": "label_transcript",
      "evidence_id": "ev_77dfe41a85bfec068604",
      "retrieved_at": "2026-11-12T05:32:00Z",
      "identifier_type": "GTIN",
      "identifier_value": "6901234567892",
      "record_id": "LBL-6901234567892-V1",
      "display_name": "禾丰全脂乳粉",
      "net_content": "400g",
      "pack_type": "",
      "link_mode": "EXACT_IDENTIFIER_MATCH",
      "link_reason": "GTIN 精确等于查询条码 6901234567892"
    },
    {
      "candidate_id": "cand_005161c2a6",
      "source_id": "samr_gov",
      "evidence_id": "ev_d3ac72f8423e5e015ef8",
      "retrieved_at": "2026-11-12T05:32:00Z",
      "identifier_type": "SC_NO",
      "identifier_value": "SC10111000012345",
      "record_id": "SC-6901234567892-0001",
      "display_name": "",
      "net_content": "",
      "pack_type": "",
      "link_mode": "MANUAL_CONFIRM",
      "link_reason": "SC_NO 与查询条码 6901234567892 不构成精确标识符相等，需用户手动确认"
    }
  ],
  "coverage_manifest": [ "…7 条，每条对应一个登记源，字段见下表…" ],
  "exact_match_count": 3,
  "manual_confirm_count": 2,
  "no_auto_merge_notice": "候选仅并列展示；仅 EXACT_IDENTIFIER_MATCH 建立关联，其余需 MANUAL_CONFIRM"
}
```

### `match_candidates[]`

| 字段 | 说明 |
| --- | --- |
| `candidate_id` | `cand_` + `sha256(source_id|record_id|payload_sha256)[:10]`，稳定且唯一 |
| `source_id` | 来源标识 |
| `evidence_id` | 该候选所属快照的证据 ID |
| `retrieved_at` | 快照采集时间 |
| `identifier_type` | `GTIN` / `UDI_DI` / `SC_NO` / `STANDARD_NO` / `MARK_NAME` / `CIFER_NO` |
| `identifier_value` | 该记录自身的标识符原文 |
| `record_id` | 来源记录 ID（同一快照内可有多条） |
| `display_name` | `product_name` 或 `mark_name`，取不到则为空串 |
| `net_content` / `pack_type` | 净含量与包装形式（`net_content` 在无该字段时回落到 `specification`） |
| `link_mode` | `EXACT_IDENTIFIER_MATCH` 或 `MANUAL_CONFIRM` |
| `link_reason` | 关联判定的一句话机械说明 |

### 关联判定规则（`build_candidates`）

| 来源 | identifier_type | 何时 `EXACT_IDENTIFIER_MATCH` |
| --- | --- | --- |
| `gs1` | `GTIN` | 记录的 `gtin` 去非数字字符后与查询条码完全相等 |
| `label_transcript` | `GTIN` | 同上 |
| `udi` | `UDI_DI` | `udi_di` 去非数字并去前导 `0` 后与查询条码相等（GTIN 常被 0 补齐到 14 位） |
| `samr_gov` | `SC_NO` | **从不**（许可号与条码是不同命名空间） |
| `samr_std` | `STANDARD_NO` | **从不** |
| `cnipa` | `MARK_NAME` | **从不** |
| `cifer` | `CIFER_NO` | **从不** |

其余一律 `MANUAL_CONFIRM`。系统不存在「同名即关联」「相似即关联」的分支；门禁 7 的 `test_similar_name_does_not_create_link` 会断言两个不同条码的候选证据集合互不相交。

### `coverage_manifest[]`

| 字段 | 说明 |
| --- | --- |
| `source_id` | 数据源标识 |
| `display_name` / `authority` | 展示名与机构 |
| `access_mode` | 注册表中的接入模式 |
| `coverage_state` | 本次查询的处置结果（见下） |
| `policy_url` | 必须为 `https://` 开头的可核验策略地址 |
| `note` | 中文说明，永远非空 |

## 覆盖状态

`coverage_state` 由 `coverage_manifest()` 依优先级判定，**每个登记源都会出现在清单中**，不允许隐藏。

| 状态 | 判定条件 | 说明文案（原文） |
| --- | --- | --- |
| `ACCESS_PENDING` | 注册表 `access_mode = ACCESS_PENDING` | 该数据源接入待授权，本档案不含其记录 |
| `SOURCE_UNAVAILABLE` | 注册表 `access_mode = SOURCE_UNAVAILABLE` | 该数据源当前不可稳定访问，本档案不含其记录 |
| `QUERY_FAILED` | 本次查询抛出适配器异常 | `本次查询失败：{错误码}: {说明}` |
| `QUERIED_MATCHED` | 本次查询产出了至少一条候选 | 本次查询取得记录 |
| `QUERIED_NO_MATCH` | 已查询但零记录 | 本次查询未取得记录 |
| `NOT_QUERIED` | 该源的查询标识符类型不适用本条码 | 该源不适用于本条码的查询标识符类型 |

`ACCESS_PENDING` / `SOURCE_UNAVAILABLE` 优先于本次查询结果，目的是让「没接入」不能被伪装成「查过但没有」。

### `QUERIED_NO_MATCH` 的文案红线

条码分片顶层另有 `query_status`，取值 `QUERIED_MATCHED` / `QUERIED_NO_MATCH`。未命中时 `query_status_note` 固定为：

```
QUERIED_NO_MATCH：当前已接入静态档案库未匹配到记录
```

该文案只描述「本静态库现有分片没有该条码」，**不得**改写为任何关于备案、真伪或合法性的说法。前端 `ScanPage.tsx` 的消费者主线用一句人话承载同一语义：「我们接入的公开记录里暂时没有这一条。这不代表任何结论——只是这次没查到。」（2026-11-13 起；原句为「仅表示本静态库现有分片无该条码」，替换后仍是同一断言，只是不再使用「分片」这类构建机词汇。见 `docs/VOICE.md`。）门禁 3 的 `test_coverage_gap_is_never_silent` 断言 `query_status_note` 以 `QUERIED_NO_MATCH` 开头。

## 3. `v1/products/{entity_id}.json`

顶层字段：

| 字段 | 说明 |
| --- | --- |
| `schema_version` / `build_id` / `built_at` / `entity_id` / `gtin` | 同上 |
| `observation_policy` | 自描述对象：`no_final_truth_field`、`no_auto_merge`、`observations_bound_to` 四个绑定键 |
| `coverage_manifest` | 与 `gtin` 分片同构的覆盖清单 |
| `blocks` | 16 个区块对象，顺序即 `block_order` |
| `block_order` | 区块 ID 数组，供前端稳定渲染 |
| `observations` | 全部历史快照的并列观测 |
| `history_timeline` | 按账本 `seq` 排序的快照时间线 |
| `source_visibility_events` | 只追加的可见性事件序列 |

### 16 区块（按 `BLOCK_IDS` 实际顺序）

| # | `block_id` | 标题 | 主要承载 |
| --- | --- | --- | --- |
| 01 | `identity_basic` | 基本信息（条码/品牌/商品名/净含量/包装） | `fields`：商品名称、品牌、净含量、包装形式、条码、厂商前缀 |
| 02 | `match_candidates` | 标识符候选与关联状态 | `items`：候选并列，另含 `exact_match_count` / `manual_confirm_count` |
| 03 | `label_ingredients` | 配料表原文 | `items`：按标签顺序逐项，含 `order`、`search_keys` |
| 04 | `nutrition_facts` | 营养成分表原文 | `items`：`nutrient`（规范名）、`raw_nutrient`、`raw_value` |
| 05 | `explicit_allergens` | 致敏原原文提示 | `items`：`raw_text`、`standard_ref`、`search_keys` |
| 06 | `explicit_unsuitable_groups` | 标签标注的不适宜人群原文 | `items`：`raw_text` |
| 07 | `storage_and_shelf_life` | 贮存条件与保质期 | `fields` |
| 08 | `registration_records` | 注册/备案公开记录 | `fields`：仅取 `udi` 源 |
| 09 | `license_records` | 生产许可公开记录 | `fields`：仅取 `samr_gov` 源 |
| 10 | `company_prefix` | 条码厂商前缀登记 | `fields`：仅取 `gs1` 源 |
| 11 | `trademark_records` | 商标公开记录 | `fields`：仅取 `cnipa` 源 |
| 12 | `referenced_standards` | 引用的标准文本元数据 | `items`：`samr_std` 记录 + 标签标注的标准号 |
| 13 | `import_export_records` | 进出口商备案记录 | `items`：仅取 `cifer` 源 |
| 14 | `observations` | 多源观测并列 | `items`：全部历史快照 |
| 15 | `history_timeline` | 历史快照时间线 | `items`：按 `seq` 排序 |
| 16 | `source_visibility` | 来源可见性与证据哈希 | `items`：事件 + 推导的当前生效状态 + 覆盖状态 + 证据哈希 |

### 区块结构

每个区块恒有五个基础键，外加可选计数：

```json
{
  "block_id": "identity_basic",
  "title": "基本信息（条码/品牌/商品名/净含量/包装）",
  "state": "FILLED",
  "note": "以下基本信息来自各数据源原始记录，按来源并列列出",
  "fields": [
    {
      "label": "商品名称",
      "value": "全脂乳粉",
      "raw_value": "全脂乳粉",
      "source_id": "gs1",
      "evidence_id": "ev_6e0d34e5dccf0c307528",
      "retrieved_at": "2026-11-12T04:44:37Z"
    }
  ],
  "items": []
}
```

`fields[]` 元素恒有 `label` / `value` / `raw_value` / `source_id` / `evidence_id` / `retrieved_at`。
`items[]` 的元素随区块而异，但凡是记录性条目都带 `source_id` + `evidence_id`。

`state` 取值：

| 值 | 含义 |
| --- | --- |
| `FILLED` | 本次构建取到了记录 |
| `EMPTY` | 无已采集记录（该源本次无数据或该源不适用） |
| `ACCESS_PENDING` | 该区块对应的源接入待授权 |
| `SOURCE_UNAVAILABLE` | 该区块对应的源不可访问 |

`note` 恒为非空字符串。门禁 3 会断言每个区块都同时具备 `state` 与 `note`。

### `observations[]`

```json
{
  "source_id": "gs1",
  "evidence_id": "ev_6e0d34e5dccf0c307528",
  "retrieved_at": "2026-11-12T04:44:37Z",
  "raw_value": "{\"records\":[{\"brand\":\"禾丰\",\"company_prefix\":\"6901234\",\"gtin\":\"6901234567892\",\"net_content\":\"400g\",\"pack_type\":\"马口铁罐装\",\"product_name\":\"全脂乳粉\",\"record_id\":\"GS1-CN-6901234567892-0001\"}]}",
  "payload_sha256": "b5c1d0e13c5172377915a7d2e4c5637950eb42a6a0eb48a3c6fc438188815aa6",
  "adapter_version": "1.0.0"
}
```

`raw_value` 是**规范化 JSON 字符串**（`canonical_json`：`sort_keys=True`、`separators=(",", ":")`、不转义中文）。它就是原始响应的完整内容，一字不改。门禁 1 会把它解析回结构并与账本快照逐字段比对。

同一来源的多个版本不会互相覆盖：每个版本一条 observation，各自带自己的 `evidence_id` 与 `payload_sha256`。

#### `retrieved_at` 的唯一语义：首次取得该证据的时间

`retrieved_at` 在全部分片中**唯一取值**：它表示「首次取得该 `evidence_id` 对应内容的时间」。内容未变的重复查询不会让它漂移。

> 历史缺陷（已修复）：早期实现中，`append_snapshot()` 在内容未变时返回**本次调用**的时间，而历史读取走 `snapshots` 表的首次落盘时间，导致同一 `evidence_id` 在 `gtin` 分片与 `products` 分片出现两个不同的 `retrieved_at`（实测 `05:32:00Z` 与 `04:44:37Z`）。修复后同一证据在所有分片中的时间戳一致，并新增 `test_retrieved_at_is_single_valued_per_evidence` 守门。

「何时复核过」这一语义由 `observations_log` 承载，不在静态产物中暴露。若将来需要把它也导出，应新增独立字段（如 `last_observed_at`），而不是复用 `retrieved_at`。


### `history_timeline[]`

```json
{
  "evidence_id": "ev_6e0d34e5dccf0c307528",
  "source_id": "gs1",
  "retrieved_at": "2026-11-12T04:44:37Z",
  "payload_sha256": "b5c1d0e13c5172377915a7d2e4c5637950eb42a6a0eb48a3c6fc438188815aa6",
  "adapter_version": "1.0.0"
}
```

排序键是账本 `seq`（单调递增），不是时间戳。这样两次构建落在同一秒时仍能稳定区分先后（见 `emit_static.py` 的 `_latest_by_source`）。

## 4. 可见性事件与「当前生效」推导

`source_visibility_events[]` 元素：

```json
{
  "seq": 1,
  "event_id": "vis_e31c554b7df09942ff91",
  "entity_id": "ent_0fdf238ca1c25b41",
  "source_id": "gs1",
  "evidence_id": "ev_6e0d34e5dccf0c307528",
  "event_type": "CURRENTLY_VISIBLE",
  "recorded_at": "2026-11-12T04:44:37Z",
  "note": "本来源当前最新记录"
}
```

| `event_type` | 含义 |
| --- | --- |
| `CURRENTLY_VISIBLE` | 该快照在其来源内是当前最新 |
| `HISTORICALLY_OBSERVED` | 该快照曾被观测，但已被同一来源更新的快照取代；仍保留 |
| `REMOVED` | 来源下线/撤下该记录（只追加事件，不删除证据） |

### 为什么需要「推导」而不是「一张表」

账本严格只追加，所以同一来源经过多次构建后，事件流里**确实会存在多条 `CURRENTLY_VISIBLE`** —— 每条在它写入的那一刻都是当前最新，这是历史事实，不能改写。因此区块 16 同时给出两层：

| 层 | 内容 | 性质 |
| --- | --- | --- |
| 原始事件序列 | `label: 来源可见性事件`，每条带 `seq` | 只追加、不可变 |
| 推导的当前生效状态 | `label: 来源当前生效可见性（由事件序列推导）` | 每次构建按 `seq` 取每个来源最后一条事件算出 |

推导由 `dossier.effective_visibility()` 完成：按 `seq` 升序遍历，对每个 `source_id` 保留最后一条。`REMOVED` 之后若又有新事件，则以新事件为准。同理，区块 08/09 等「当前字段」只展示**每个来源的最新快照**（`_latest_by_source`），历史版本只出现在区块 14/15/16。

门禁 4 断言：同一来源的「当前生效」记录唯一，且旧版本证据仍在账本与 `history_timeline` 中。

## 5. `v1/ingredients/{entity_id}.json`

真实产物摘录（节选）：

```json
{
  "schema_version": "1.0",
  "entity_id": "ent_0fdf238ca1c25b41",
  "gtin": "6901234567892",
  "ingredients": [
    {
      "order": 1,
      "raw_name": "全脂乳粉",
      "search_keys": ["全脂乳粉", "乳粉", "乳制品", "乳糖", "乳", "牛乳"],
      "source_id": "label_transcript",
      "evidence_id": "ev_77dfe41a85bfec068604",
      "retrieved_at": "2026-11-12T04:44:37Z",
      "raw_value": "全脂乳粉",
      "record_id": "LBL-6901234567892-V1"
    }
  ],
  "nutrition_facts": [
    {
      "nutrient": "钠",
      "raw_nutrient": "钠",
      "raw_value": "280毫克/100克",
      "evidence_id": "ev_77dfe41a85bfec068604",
      "source_id": "label_transcript",
      "retrieved_at": "2026-11-12T04:44:37Z"
    }
  ],
  "explicit_allergens": [
    {
      "raw_text": "含有乳及乳制品（包括乳糖）",
      "standard_ref": "GB 7718-2025",
      "search_keys": ["乳", "乳制品", "乳糖", "乳蛋白"],
      "source_id": "label_transcript",
      "evidence_id": "ev_77dfe41a85bfec068604",
      "retrieved_at": "2026-11-12T04:44:37Z"
    }
  ],
  "explicit_unsuitable_groups": [
    {
      "raw_text": "对乳蛋白过敏者不宜食用",
      "source_id": "label_transcript",
      "evidence_id": "ev_77dfe41a85bfec068604",
      "retrieved_at": "2026-11-12T04:44:37Z"
    }
  ],
  "label_standard_refs": [
    {
      "standard_no": "GB 7718-2025",
      "source_id": "label_transcript",
      "evidence_id": "ev_77dfe41a85bfec068604",
      "retrieved_at": "2026-11-12T04:44:37Z"
    }
  ],
  "disclaimer": "本文件为纯客观配料与营养原文数据，供端侧机械词条对照；不含任何评价或建议"
}
```

| 数组 | 消费方 | 说明 |
| --- | --- | --- |
| `ingredients` | `local_diet_matcher.matchIngredients` | `order` 为标签原文顺序（不重排，门禁 2 断言）；`search_keys` 由 `lexicon_terms.search_keys_for_ingredient()` 机械展开 |
| `nutrition_facts` | 饮食计划录入（用户可选读入） | `nutrient` 是规范名（如 `钠`），`raw_nutrient` 与 `raw_value` 保持原文 |
| `explicit_allergens` | 致敏原对照 | `search_keys` 由 `search_keys_for_allergen()` 生成 |
| `explicit_unsuitable_groups` | 纯展示 | 仅复述标签原文 |
| `label_standard_refs` | 展示 | 标签上标注的标准号 |

`search_keys` 生成规则见 `pipeline/builder/lexicon_terms.py` 的 `INGREDIENT_TERM_KEYS` / `ALLERGEN_TERM_KEYS` 字典。它是**静态词条映射**，不含任何商品判断；未命中词典时回落到去括号的原文本身。

## 6. `v1/demo/index.json`

```json
{
  "schema_version": "1.0",
  "build_id": "build_20261112_053200Z",
  "note": "端侧离线兜底样例：仅用于无静态库时的数据结构演示，全部字段与 /v1/ 契约一致",
  "manifest_sources": [ "…与 manifest.sources 同构…" ],
  "entries": [
    {
      "gtin": "6905554443331",
      "entity_id": "ent_f2591797e13fe7f4",
      "query_status": "QUERIED_MATCHED",
      "match_candidates": [ "…" ],
      "coverage_manifest": [ "…" ],
      "blocks": [ "…16 个区块…" ],
      "ingredients": { "…与 ingredients 分片同构…" }
    }
  ]
}
```

`entries[]` 把正式产物需要的四类信息内联到一处，便于单文件分发时直接读本地样例。每个 entry 与正式分片保持同构，因此前端可以在 `STATIC_LIBRARY` 与 `DEMO_FALLBACK` 两种来源之间走同一条渲染路径。

**两种 origin 的语义边界（读者必须能区分）**：

| `origin` | 真实来源 | 界面义务 |
| --- | --- | --- |
| `STATIC_LIBRARY` | 构建机编译的 `v1/gtin` / `v1/products` / `v1/ingredients` 分片 | 正常展示；不得标为演示样例 |
| `DEMO_FALLBACK` | 本文档的 `v1/demo/index.json`，或打包进单文件的 `DEMO_FALLBACK` 常量 | 每个展示位置必须出现「演示样例」标记，并说明不等于已联网的真实商品库记录 |
| `LOCAL_OCR` | 端侧识别 / 手动粘贴的文本（`scan_history.origin`） | 标注为端侧文本，说明不是商品库分片 |

`origin` 由 `lookupGtin()` 在查询时写入返回值，不是前端猜测：`fetchJson("v1/gtin/{gtin}.json")` 成功 → `STATIC_LIBRARY`；回落到 demo → `DEMO_FALLBACK`。演示条码 `6901234567892` / `6905554443331` 之所以是「演示样例」，是因为它们在**这份兜底索引里**，而不是因为条码本身有什么特殊——前端因此绝不能按条码字面量判断来源，只能读 `origin`。

前端还有一份**打包进单文件**的兜底样例 `apps/web/src/services/demo_fallback.ts`（常量 `DEMO_FALLBACK`），用于连 `v1/demo/index.json` 也取不到时（例如双击 `index.html` 打开、`fetch` 被 `file://` 限制）。此时界面标注 `DEMO_FALLBACK`。

门禁（`test_demo_contract.py`）断言：demo 索引必须存在、不得为空、必须自我说明、每个 entry 字段齐全、demo 覆盖的条码必须是正式产物的子集。

## 7. 字段追溯契约

四类分片中所有「记录性」条目都必须满足：

```
source_id + evidence_id + retrieved_at + raw_value  （四元绑定）
```

| 门禁 | 断言 |
| --- | --- |
| 1 `test_source_trace_rate` | 追溯率必须恰好 `1.0`；`evidence_id` 必须存在于账本；`raw_value` 必须能在原始快照中找到 |
| 2 `test_field_extraction_accuracy` | `raw_value` 与账本 payload 的一致率 ≥ `0.995`；配料 `order` 必须与原始数组下标一致 |
| 9 `test_integrity_check` | 每个 observation 的 `payload_sha256` 长度为 64 字符；账本记录与产物文件的 SHA-256 必须一致 |

例外：`数据源覆盖状态` 与 `原始证据哈希` 两类 `items` 不带 `evidence_id`（前者描述接入状态，后者本身就是哈希摘要），门禁 1 显式跳过它们。

## 8. 契约的稳定承诺

| 承诺 | 由什么保证 |
| --- | --- |
| 同一 GTIN 永远映射同一 `entity_id` | `entity_id_for()` 纯哈希 |
| 相同账本 + 相同输入 → 相同产物字节（除 `build_id` / `built_at`） | 门禁 5 `test_products_are_byte_reproducible` |
| 产物内容不依赖自身写入时间 | 区块 16 用快照的 `payload_sha256`，不嵌入产物自身哈希 |
| 内容相同的响应不重复落盘 | 账本 `ux_snapshots_identity` 唯一索引 + 内容寻址 `evidence_id` |
| 历史快照永不丢失 | 账本 UPDATE/DELETE 触发器 |

### 饮食页的「参考值 / 自设值」是展示层派生状态，**不入产物**

饮食页每行进度条的归属（`参考值` 挂 `GB 28050—2011 附录 A` 出处，还是退回 `自设值`）由前端逐项比较得出：

```ts
isNrv = plan.targets[key] === DEFAULT_TARGETS[key]   // 纯前端比较，不落盘
```

- **不进入** `v1/` 任何静态分片，也没有对应字段——它不是产物内容，构建期完全不参与；
- **不进入** IndexedDB 的 `DietTargets`（仍是 `Record<NutrientKey, number>`），也没有「是否已修改」标记位；
- **不进入**本地备份 JSON（`schema_version` 仍是 `"1"`，`NUMERIC_KEYS` 与 `validatePlan` 未变）；
- 脂肪的上限语义（显示 `≤60 g`）同样只在展示层由只读常量 `NRV_IS_UPPER_BOUND` 表达，存储值永远是数字 `60`。

因此本文件其余章节定义的字段契约不受该展示状态影响；改口径不会造成产物字节变化，也不会破坏已有备份。
