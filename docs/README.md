# 文档索引

本目录是 `EatWhat`（消费者商品公开信息展示器）的工程文档。所有描述均以仓库中的实际代码与产物为依据，不含规划性表述。

## 建议阅读顺序

| 顺序 | 文档 | 一句话说明 |
| --- | --- | --- |
| 1 | [`../README.md`](../README.md) | 项目定位、五条工程红线、快速上手与产物一览 |
| 2 | [`ARCHITECTURE.md`](ARCHITECTURE.md) | 组件边界与数据流：适配器 → 账本 → 编译器 → 静态分片 → 浏览器 |
| 3 | [`DATA_CONTRACT.md`](DATA_CONTRACT.md) | `/v1/` 四类分片的字段契约、16 区块、覆盖状态与可见性事件 |
| 4 | [`PIPELINE.md`](PIPELINE.md) | 离线构建操作手册：CLI 参数、账本语义、新增数据源流程 |
| 5 | [`PRIVACY.md`](PRIVACY.md) | 端侧边界：IndexedDB 结构、OCR 路径、零 Cookie 的可验证方式 |
| 6 | [`SEMANTIC_GATE.md`](SEMANTIC_GATE.md) | 附录 B 语义门禁：禁用词类别、扫描范围、豁免规则 |
| 7 | [`ACCEPTANCE_GATES.md`](ACCEPTANCE_GATES.md) | 十项硬门禁逐项说明：断言、执行方式、失败条件 |
| 8 | [`DEVELOPMENT.md`](DEVELOPMENT.md) | 本地开发、构建、测试与常见故障处理 |
| 9 | [`../AGENTS.md`](../AGENTS.md) | 协作铁律：每一步修改必须同步更新哪些文档（含映射表） |

## 角色索引

| 你的角色 | 直接读这些 |
| --- | --- |
| 只想跑起来看效果 | `README.md` → `PRIVACY.md` |
| 要接新的公开数据源 | `PIPELINE.md` → `DATA_CONTRACT.md` → `SEMANTIC_GATE.md` |
| 要改前端界面 / 交互 | `ARCHITECTURE.md` → `PRIVACY.md` → `ACCEPTANCE_GATES.md`（门禁 8 会跑真实浏览器） |
| 要审合规性 | `SEMANTIC_GATE.md` → `ACCEPTANCE_GATES.md` → `PIPELINE.md` 的「凭证隔离」一节 |
| 要排查构建失败 | `DEVELOPMENT.md` → `PIPELINE.md` |

## 术语约定

| 术语 | 含义 |
| --- | --- |
| 静态分片 / shard | `dist_data/v1/` 下由构建管线生成、只读的 JSON 文件 |
| 快照 / snapshot | 账本中对某数据源某查询键的一次完整原始响应 |
| 证据 / evidence | 快照的内容寻址标识，形如 `ev_xxxxxxxxxxxxxxxxxxxx`，由 SHA-256 派生 |
| 观测 / observation | 某条分片中并列展示的一条来源记录，绑定 `source_id` + `evidence_id` + `retrieved_at` + `raw_value` |
| entity | 一个商品实体，`entity_id` 由 GTIN 哈希派生，形如 `ent_xxxxxxxxxxxxxxxx` |
| 覆盖状态 | 某数据源对本次查询的处置结果，见 `DATA_CONTRACT.md` |
