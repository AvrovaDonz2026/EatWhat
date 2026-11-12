# 语义门禁（附录 B）

`packages/semantic_gate/` 是一个静态扫描器，作用于三类对象：

1. 静态 JSON 产物（结构化扫描）；
2. 网页源码 `.ts/.tsx/.js/.jsx/.html/.css/.json/.md`（纯文本扫描）；
3. 打包后的 `apps/web/dist/index.html`（纯文本扫描）。

它的唯一职责是**拦截**被禁止的表述，不修改内容、不自动改写。

## 运行

```bash
# 扫描默认目标：dist_data + apps/web/src + apps/web/index.html + apps/web/dist
python3 -m packages.semantic_gate.cli_scan --all

# 指定目标
python3 -m packages.semantic_gate.cli_scan dist_data apps/web/src apps/web/dist

# 机器可读输出
python3 -m packages.semantic_gate.cli_scan --all --json

# 通过 npm
npm run gate
```

| 退出码 | 含义 |
| --- | --- |
| `0` | 通过 |
| `1` | 命中禁用词或风险配色 |
| `2` | argparse 用法错误（未给目标且未用 `--all`） |

目标不存在时打印 `[skip] 目标不存在: <path>` 到 stderr 并继续，不计入退出码。

命中时输出：

```
[FAIL] apps/web/src/pages/Foo.tsx:42 「<被禁词>」(RISK_ASSESSMENT)
        …<上下文片段>…
```

JSON 模式输出 `{ targets, missing, finding_count, findings[] }`，每个 finding 含 `term` / `category` / `where` / `line` / `excerpt` / `node_path`。

## 禁用词表（附录 B）

以下为 `rules.py` 中 `BANNED_TERMS` 的**完整词表枚举**。此处列出仅用于声明「这些词属于被禁词汇」，不构成对任何商品、企业或行为的描述。

| 类别常量 | 类别含义 | 被禁词（逐项枚举） |
| --- | --- | --- |
| `RISK_ASSESSMENT` | 风险/安全评价 | `高风险`、`低风险`、`危险`、`值得警惕`、`可信度`、`安全等级` |
| `AUTHENTICITY` | 真伪评价 | `假货`、`真货` |
| `LEGALITY` | 合法/违法评价 | `非法`、`违法`、`未备案` |
| `RELATION_INFERENCE` | 关系推断 | `同源`、`换壳`、`关联企业`、`实际控制`、`背后是同一批人` |
| `PURCHASE_ADVICE` | 购买建议 | `推荐`、`不推荐`、`可以买`、`不要买`、`谨慎购买`、`推荐指数` |
| `MOTIVE_JUDGEMENT` | 动机/主观评价 | `欺骗`、`擦边`、`故意隐瞒`、`恶意` |
| `EFFICACY_CLAIM` | 学术功效评价 | `论文证明该商品有效`、`论文不支持该商品功效` |

共 **28** 个词条，分 7 个类别常量（`LEGALITY` 与 `AUTHENTICITY` 分开登记；`RISK_ASSESSMENT` 6 条、`PURCHASE_ADVICE` 6 条、`RELATION_INFERENCE` 5 条、`MOTIVE_JUDGEMENT` 4 条、`LEGALITY` 3 条、`AUTHENTICITY` 2 条、`EFFICACY_CLAIM` 2 条）。门禁 `test_banned_terms_cover_six_categories` 断言这 7 个类别常量全部存在，且测试名中的「six」是历史命名遗留 —— 实际代码已是 7 类。

匹配方式：`re.compile(re.escape(term))`，即**字面量不区分大小写的子串匹配**；中文词条天然按子串命中，英文缩写不在此表内。

## 风险配色禁令

`FORBIDDEN_COLOR_PATTERNS`（**5** 条正则）：

| 正则 | 说明 |
| --- | --- |
| `(风险\|安全\|评级)[^。\n]{0,12}(红灯\|绿灯\|黄灯)` | 文字层面用红绿灯编码风险 |
| `(红/黄/绿\|红黄绿)[^。\n]{0,10}(风险\|评级)` | 同上，另一种写法 |
| `risk[-_ ]?(light\|level\|score\|grade\|traffic)` | 英文风险等级/评分字段 |
| `(score\|rating)[-_ ]?(grade\|badge)[^。\n]{0,10}(danger\|safe)` | 评分徽标 |
| `bg-red-\d+[^"']{0,40}(风险\|不合格\|fail)` | 红色风险徽标样式 |

对应类别常量 `RISK_COLOR_CODE`。

设计层面的落实（见 `apps/web/src/index.css` 头部注释与 `components/ui.tsx`）：状态一律用**文字标签 + 边框形态**表达，`StatusTag` 只有 `filled` / `pending` / `none` 三种色调，全部来自同一组墨色阶梯，不使用任何红/绿/黄语义色。

## `raw_value` 豁免

来源原文不得被改写，因此以下键名的字符串值**豁免**全部禁用词检查：

```python
RAW_VALUE_KEYS = frozenset({"raw_value", "raw_text", "raw_note", "raw_name", "raw_nutrient"})
```

理由：如果上游标签原文里写了某个被禁词，系统必须原样保存它 —— 改写来源原文同样是失真。豁免是**按字段名**生效的，不是按文件或按来源生效。

门禁 `test_raw_value_exemption_is_narrow` 验证豁免确实很窄：同样的文本放进 `raw_value` 不报错，放进 `note` 字段必须报错，且所有 finding 的 `node_path` 都以 `.note` 结尾。

注意：结构化扫描中对 `RAW_VALUE_KEYS` 先跳过禁用词检查，随后在 `if key in RAW_VALUE_KEYS: return` 处也跳过配色检查 —— 两级豁免一致。

## 扫描范围实现细节

| 项 | 值 |
| --- | --- |
| 扩展名 | `.ts .tsx .js .jsx .html .css .json .md` |
| 跳过路径片段 | `node_modules`、`.git`、`dist_data`、`__pycache__`、`.venv` || 目录遍历 | 递归、按路径排序（输出稳定） |
| JSON 文件 | 先 `json.loads`，成功则走 `scan_json`（结构化 + `node_path`）；解析失败回落纯文本扫描 |
| 非 JSON 文件 | 纯文本扫描（带行号） |

`_SCAN_SKIP_PARTS` 的跳过判断基于**相对 root 的路径分段**（`path.resolve().relative_to(root.resolve()).parts`），因此以 `dist_data` 为 root 时产物不会因根目录名而整体被跳过。

> 历史缺陷（已修复）：早期实现用 `path.parts`（绝对路径分段）判断，当 root 本身就是 `dist_data` 时第一个分段即命中跳过规则，`scan_tree(dist_data)` 返回 **0 个文件**，`test_static_artifacts_are_semantically_clean` 成为空断言（永远通过），`cli_scan --all` 打印 `[PASS]` 但产物实际未被扫描。修复后实测：

```
artifact files on disk: 11
iter_source_files(data_dir): 11
scan_tree findings: 0
```

并新增两条守门测试，防止同类「扫描范围为空导致假绿灯」复发：

| 测试 | 作用 |
| --- | --- |
| `test_static_artifacts_are_semantically_clean` | 先断言磁盘产物数与实际扫描数相等，再断言零命中 |
| `test_static_artifact_scan_actually_detects_violations` | 反向对照：向真实产物镜像注入禁用词后，扫描必须命中 |


## 白名单文件

`ALLOWLIST_FILES` 中的文件允许出现禁用词字面量（因为它们必须**列举**这些词）：

```
packages/semantic_gate/rules.py
packages/semantic_gate/cli_scan.py
packages/semantic_gate/adversarial_corpus.json
tests/acceptance_gates/test_zero_inference_gate.py
tests/acceptance_gates/conftest.py
README.md
```

`is_allowlisted()` 以相对仓库根路径比较，支持 `rel == entry` 或 `rel.endswith("/" + entry)`。

> `docs/` 不在白名单内，也**不在** `cli_scan --all` 的默认扫描目标里（默认只有 `dist_data` / `apps/web/src` / `apps/web/index.html` / `apps/web/dist`）。因此本文档中的词表不影响 `npm run gate`。
>
> 但若有人显式执行 `cli_scan docs`，本文件会被判为多处违规 —— 因为它必须逐项列举这些词。这是「枚举被禁词汇」与「使用被禁词汇」在纯子串扫描下无法区分的必然结果。若要覆盖 docs，应把 `docs/SEMANTIC_GATE.md` 加入 `rules.py` 的 `ALLOWLIST_FILES`。

### 已知误报：`同源` 与网页术语「同站」

禁用词 `同源` 的本意是拦截「同源产品 / 换壳」这类**关系推断**。但 Web 语境中 same-origin 也是常用技术术语，纯子串扫描无法区分语义，会一并命中。

处理方式：面向读者的文档与注释统一写作「同站」，避免与技术术语冲突；门禁词表保持原样（移除会削弱对关系推断的拦截）。这类「技术同形词」是子串匹配的固有代价，新增词条时应留意同类冲突。

## 对抗语料

`packages/semantic_gate/adversarial_corpus.json` 是门禁自身的回归测试输入，结构为「类别 → 样本数组」：

| 键 | 样本数 | 用途 |
| --- | --- | --- |
| `risk_assessment` | 4 | 必须全部被拦截 |
| `authenticity` | 5 | 同上 |
| `relation_inference` | 5 | 同上 |
| `purchase_advice` | 6 | 同上 |
| `motive` | 4 | 同上 |
| `efficacy` | 2 | 同上 |
| `risk_color` | 2 | 配色正则必须命中 |
| `benign_control` | 3 | **必须零命中**（防过度拦截） |

`benign_control` 的三条是合规文案样本，例如：

- `本提示仅作配料词条机械对照，不构成医疗、营养或购买建议，请以商品实物标签为准。`
- `QUERIED_NO_MATCH：当前已接入静态档案库未匹配到记录`

它们随时可能出现在界面或产物里，必须能通过门禁。若某次规则调整把它们也拦了，`test_gate_does_not_flag_benign_copy` 会失败 —— 这防止「为了过门禁把文案改得无法阅读」。

## 如何新增一条规则

1. 在 `BANNED_TERMS` 中加一条：

```python
{"term": "<新禁用词>", "category": "<既有类别常量>", "note": "<类别说明>"}
```

`category` 应尽量复用已有常量；新增常量则需同步更新 `test_banned_terms_cover_six_categories` 的 `expected` 集合。

2. 在 `adversarial_corpus.json` 对应类别数组中加样本（至少一条），确保 `test_gate_blocks_every_adversarial_category` 覆盖到。

3. 如果新词可能误伤合规文案，先在 `benign_control` 里确认现有合规样本是否包含它。若合规文案**必然**含该词（例如某个专业术语），应改为更精确的正则表达式或缩小扫描范围，而不是无脑禁用 —— 否则会造成过度拦截，反而降低文档可用性。

4. 跑 `npm run gate` 与 `pytest tests/acceptance_gates/test_zero_inference_gate.py -q`。

5. 若禁用词必须出现在本文档或测试中，把文件加入 `ALLOWLIST_FILES`。

## 与验收门禁的关系

`test_zero_inference_gate.py` 共 9 个测试，把门禁自身也当作被测对象：

| 测试 | 断言 |
| --- | --- |
| `test_gate_blocks_every_adversarial_category` | 每个类别至少 6 个类别常量被覆盖，每条对抗样本至少产生 1 个 finding |
| `test_gate_does_not_flag_benign_copy` | 合规文案零 finding |
| `test_gate_blocks_risk_color_coding` | 风险配色样本必须有 finding，且 `FORBIDDEN_COLOR_PATTERNS` 非空 |
| `test_static_artifacts_are_semantically_clean` | `scan_tree(dist_data)` 零 finding —— **注意：当前该扫描实际为 0 个文件，见上文「扫描范围实现细节」中的缺陷说明** |
| `test_raw_value_exemption_is_narrow` | 豁免只对 `RAW_VALUE_KEYS` 生效 |
| `test_web_source_has_no_banned_terms` | `scan_tree(apps/web/src)` 零 finding |
| `test_single_file_build_is_clean` | `apps/web/dist/index.html` 零 finding（未构建则 skip） |
| `test_banned_terms_cover_six_categories` | 7 个类别常量齐全 |
| `test_no_health_efficacy_claims_in_artifacts` | 产物中（`RAW_VALUE_KEYS` 之外）不得出现 `治疗` / `治愈` / `预防疾病` / `增强免疫` / `降血糖` / `降血压` |
| `test_dev_field_does_not_leak_into_ui_copy` | 三个覆盖状态的中文说明不得触发门禁 |

最后两个测试的补充说明了门禁的边界：`test_no_health_efficacy_claims_in_artifacts` 使用了一份**独立**的功效词表（不在 `BANNED_TERMS` 中），因为功效声称的形态远比 26 个词条丰富，需要专表校验。

## 局限（如实记录）

| 局限 | 影响 |
| --- | --- |
| 纯子串匹配，无分词 | 变形表述（如「高 风险」中间插入空格）不会被拦 |
| 无同义词/近义扩展 | 未列入 `BANNED_TERMS` 的等价说法不会被拦 |
| 不检查语义意图 | 只是词汇黑名单，不判断上下文是否构成断言 |
| 不检查图片内文字 | OCR 产物不进门禁（端侧结果本就只留在本地） |

因此门禁是**下限保障**，不是完整的合规证明。新增文案时仍需人工审阅。
