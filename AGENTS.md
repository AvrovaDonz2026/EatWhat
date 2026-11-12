# AGENTS.md — 仓库协作规则

本文件约束所有在本仓库工作的代理与人类贡献者。优先级高于任何单次任务的临时说明。

## 铁律：每一步修改必须同步更新文档

任何改动，只要影响下述任一项，就必须在**同一个提交内**更新对应文档。文档与代码在同一次提交里不一致，视为该提交不合格。

### 文档同步映射表

| 改动内容 | 必须同步更新的文档 |
| --- | --- |
| 新增/修改静态产物字段、分片结构、`block_id` | `docs/DATA_CONTRACT.md`（含真实 JSON 片段） |
| 修改覆盖状态、可见性事件枚举、`query_status` 文案 | `docs/DATA_CONTRACT.md` |
| 修改账本 schema、`retrieved_at` 语义、哈希算法 | `docs/DATA_CONTRACT.md` + `docs/ARCHITECTURE.md` |
| 新增数据源、修改 `source_policy_registry.json` 字段 | `docs/PIPELINE.md`（含凭证环境变量说明） |
| 修改限流、退避、熔断行为 | `docs/PIPELINE.md` |
| 修改 `pipeline/build.py` 的 CLI flag | `docs/PIPELINE.md` |
| 新增/删除禁用词、修改豁免规则、修改门禁扫描范围 | `docs/SEMANTIC_GATE.md` + `README.md` 的禁用词/门禁表 |
| 新增/修改验收门禁测试 | `docs/ACCEPTANCE_GATES.md`（写明断言什么、如何触发、什么情况失败） |
| 修改测试总数、模块数 | `README.md` + `docs/ACCEPTANCE_GATES.md` + `docs/DEVELOPMENT.md` |
| 修改 IndexedDB 表结构、Cookie 策略、OCR 打包方式 | `docs/PRIVACY.md` |
| 修改构建/开发/测试命令、依赖、环境要求 | `docs/DEVELOPMENT.md` + `README.md` 快速开始 |
| 修改架构边界、数据流、可信链 | `docs/ARCHITECTURE.md` |
| 修改 CI 工作流、部署方式、Pages 地址 | `README.md` 部署章节 |
| 修复缺陷 | 对应文档中的「历史缺陷（已修复）」小节，附修复前后实测数字 |

### 文档写作要求

1. **先读代码，再写文档**。不得臆造 flag、字段名、命令或默认值。不确定就写「代码未定义」，不要填空。
2. **数字必须实测**。测试数量、文件数量、产物体积、扫描文件数等，一律用实际命令跑出来的值，不得沿用旧值。
3. **JSON 示例取真实产物**。从 `dist_data/v1/` 里引用，不要手写示例。
4. **缺陷如实记录**。修过的缺陷写进「历史缺陷（已修复）」，带修复前后数字，不能静默删除。
5. **禁用词只能被列举**。在 `docs/SEMANTIC_GATE.md` 里枚举附录 B 词表时，必须以表格或行内代码呈现，框定为「被禁词条」，不得写成陈述句。

## 硬性工程红线（不可违背）

1. **ZERO_INFERENCE** — 只做检索、取得、保存、结构化、并列、对照、时间线、来源追溯。不得输出风险评分、真伪判断、合法/违法定性、同源/换壳推断、购买建议、功效评价。
2. **无最终真相字段** — 冲突来源并列保存在 `observations[]`，每条绑定 `source_id`、`evidence_id`、`retrieved_at`、`raw_value`。
3. **不自动合并候选** — 只有权威标识符精确相等才标记 `EXACT_IDENTIFIER_MATCH`；同名、同条码多候选、文本相似一律并列，需 `MANUAL_CONFIRM`。
4. **端侧全私有、零 Cookie** — 拍照原图、OCR 结果、忌口词条、饮食计划全部留在 IndexedDB；不得使用 HTTP Cookie；不得上传或写日志。
5. **合规采集** — 不绕验证码、不突破限流；`AUTH_API` 的 Token/appId 只存于构建机环境变量，禁止写入静态 JSON 或前端源码。

## 验证责任

提交前必须自己跑通，不得只依赖 CI：

```bash
npm run build:data
npm run build:web
npm run gate
.venv/bin/python -m pytest tests/acceptance_gates -q   # 浏览器门禁需在沙箱外运行
```

并把实际数字（测试数、扫描文件数、单文件体积）用于文档更新。

## 门禁不得空转

新增或修改任何门禁时，必须同时提供一个**反向对照**：故意注入违规内容，断言门禁会命中。只断言「通过」而没有「能失败」的证明，视为该门禁无效。

## 证据展示分层（UI 约束）

- **消费者视图**：来源中文名 + 采集日期 + 并列关系。
- **追溯视图**：`evidence_id`、`retrieved_at`、`payload_sha256`（全值，可复制，不截断），默认折叠。
- **审计视图**（16 区块档案页）：完整证据密度，哈希置于各区块的折叠区内。
- 无证据的区块不渲染空折叠入口。
