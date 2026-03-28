# State Schema Index

本文档是分析状态面与上下文面的索引页。

状态：draft design。本文是 `docs/analysis/` 下 state/context schema 的导航文档，不表示对应 HTTP endpoint 或持久化模型已经实现。

## 目的

随着 proposition-centered 读取契约细化，原先单个 `state-schema.md` 同时承载 session 级主读取面与 proposition 级局部闭包读取面，已经不利于独立演进。

因此 v1 将其拆分为两个正式文档：

- [`state-surface-schema.md`](state-surface-schema.md)：定义分析状态面（`analysis state surface`）的 session 级 canonical 读取契约
- [`context-surface-schema.md`](context-surface-schema.md)：定义上下文面（`context surface`）的 proposition 级 canonical 读取契约

## 文档分工

### State Surface

[`state-surface-schema.md`](state-surface-schema.md) 负责定义：

- `SessionStateView`
- `ActivePropositionEntry`
- `SessionStateQuery`
- `StateTruncation`
- session 级 `focus_subjects` / `active_propositions` / `backing_findings` / `blocking_gaps` / `artifact_refs`

它回答的问题是：

- 当前 session 整体最值得关注什么
- 当前有哪些 active propositions
- 当前有哪些 blocking gaps 阻塞推进

### Context Surface

[`context-surface-schema.md`](context-surface-schema.md) 负责定义：

- `PropositionContextView`
- `PropositionContextQuery`
- proposition 级 `seed_findings` / `relevant_findings` / `missing_seed_finding_refs`
- latest assessment、gaps、inference records 与 context artifact refs 的局部最小闭包规则

它回答的问题是：

- 围绕单个 proposition 当前正在判断什么
- 当前 latest assessment 为什么成立、为什么未成立或为什么卡住
- 为了解释和继续决策，最少需要读取哪些 canonical objects

## 共同边界

两个文档共享以下基线：

- canonical evidence chain 保持 `artifact -> finding -> proposition -> assessment -> action proposal`
- typed refs 必须指向真实 canonical objects，不得退化为裸字符串 locator
- projection 的排序、截断、compact 视图不得改写 canonical identity
- state surface 若对 `active_propositions` 做顶层截断，其 supporting collections 必须只覆盖 returned propositions 的 closure
- HTTP path、query string 编码、分页、兼容与迁移策略不在本组文档内定义

## 阅读建议

推荐顺序：

1. 先读 [`agent-interaction-contract-principles.md`](agent-interaction-contract-principles.md) 理解三层交互面
2. 再读 [`state-surface-schema.md`](state-surface-schema.md) 理解 session 级主读取面
3. 最后读 [`context-surface-schema.md`](context-surface-schema.md) 理解 proposition 级局部最小闭包
