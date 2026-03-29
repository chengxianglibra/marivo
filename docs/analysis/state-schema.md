# State Schema Index

本文档是分析状态面与上下文面的索引页。

状态:draft design。本文是 `docs/analysis/` 下 state/context schema 的导航文档,不表示对应 HTTP endpoint 或持久化模型已经实现。

## 目的

随着命题中心读取契约细化,session 根容器契约、session 级主读取面与 proposition 级局部最小闭包读取面已经需要分开演进。

因此 v1 将其拆分为三个正式文档:

- [`session.md`](session.md):定义分析容器根对象 `AnalysisSession` 的规范契约
- [`state-surface-schema.md`](state-surface-schema.md):定义分析状态面的 session 级规范读取契约
- [`context-surface-schema.md`](context-surface-schema.md):定义上下文面的 proposition 级规范读取契约

## 文档分工

### Session Root

[`session.md`](session.md) 负责定义:

- `AnalysisSession`
- `SessionStateSummary`
- session 级目标、scope、focus、governance、lifecycle、coordination

它回答的问题是:

- 这次分析任务的容器边界是什么
- 当前 session 是否仍可继续写入
- 当前有哪些权威读取面可进入

其中 `SessionStateSummary` 只承载进入 `SessionStateView` 的最小入口,不承载 blockers、readiness 或 focus 排名。
其中 `scope` 只承载 session-level typed 非时间约束,`focus` 是 mutable planning hint；二者不得在执行契约中混用。

### State Surface

[`state-surface-schema.md`](state-surface-schema.md) 负责定义:

- `SessionStateView`
- `ActivePropositionEntry`
- `SessionStateQuery`
- `StateTruncation`
- session 级 `focus_subjects` / `active_propositions` / `backing_findings` / `blocking_gaps` / `artifact_refs`

它回答的问题是:

- 当前 session 整体最值得关注什么
- 当前有哪些活跃命题
- 当前有哪些阻塞性缺口阻塞推进

### Context Surface

[`context-surface-schema.md`](context-surface-schema.md) 负责定义:

- `PropositionContextView`
- `PropositionContextQuery`
- proposition 级 `seed_entries` / `relevant_findings`
- latest assessment、gaps、inference records 与 context artifact refs 的局部最小闭包规则

它回答的问题是:

- 围绕单个 proposition 当前正在判断什么
- 当前 latest assessment 为什么成立、为什么未成立或为什么卡住
- 为了解释和继续决策,最少需要读取哪些规范对象

## 共同边界

三个文档共享以下基线:

- `session` 是分析容器根,不替代 state surface 或 context surface
- 规范证据链保持 `artifact -> finding -> proposition -> assessment -> action proposal`
- typed refs 必须指向真实规范对象,不得退化为裸字符串 locator
- 投影的排序、截断、compact 视图不得改写规范标识
- state surface 若对 `active_propositions` 做顶层截断,其 supporting collections 必须只覆盖 returned propositions 的 closure
- HTTP path、query string 编码、分页、兼容与迁移策略不在本组文档内定义

## 阅读建议

推荐顺序:

1. 先读 [`agent-interaction-contract-principles.md`](agent-interaction-contract-principles.md) 理解三层交互面
2. 再读 [`session.md`](session.md) 理解分析容器根边界
3. 再读 [`state-surface-schema.md`](state-surface-schema.md) 理解 session 级主读取面
4. 最后读 [`context-surface-schema.md`](context-surface-schema.md) 理解 proposition 级局部最小闭包
