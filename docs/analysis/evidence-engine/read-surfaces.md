# Evidence Engine Read Surfaces

本文档是 Evidence Engine 读取面的主题级总览。

状态：draft design。本文不替代 `session.md`、`state-surface-schema.md`、`context-surface-schema.md` 的字段级 schema。

## 目的

说明以下对象在 Evidence Engine 中的关系：

- `session` 作为分析容器根
- `SessionStateView` 作为 session 级主状态面
- `PropositionContextView` 作为 proposition 级局部最小闭包读取面

## 读取层位置

Evidence Engine 主线链路仍然是：

`artifact -> finding -> proposition -> assessment -> action proposal`

读取面不新增新的核心证据对象；它负责把既有 canonical objects 组织成 agent 可直接消费的稳定视图。

因此：

- session root 负责边界与入口
- state surface 负责全局决策读取
- context surface 负责单 proposition 深挖
- projection surface 负责排序、截断和 token-budget 压缩

## Session Root

[`../session.md`](schemas/session.md) 定义分析容器根对象。

它回答：

- 当前任务边界是什么
- 当前 session 是否仍可接受写入
- 当前有哪些权威读取面可进入

它不回答：

- 当前有哪些 live support / oppose / gap members
- 当前哪个 proposition 最重要
- 当前是否已经足够回答用户

## State Surface

[`../state-surface-schema.md`](schemas/state-surface-schema.md) 定义 `SessionStateView`。

主骨架固定围绕：

- `active_propositions`
- `latest_assessment`
- live support/oppose/gap/record refs
- `backing_findings`
- `artifact_refs`

state surface 回答：

- 当前 session 整体最值得关注什么
- 当前有哪些命题处于 judgment track
- 当前哪些 blocking gaps 阻塞推进

## Context Surface

[`../context-surface-schema.md`](schemas/context-surface-schema.md) 定义 `PropositionContextView`。

局部最小闭包固定围绕：

- target proposition
- creation-time seed hydration
- latest assessment
- relevant findings
- gaps
- applied inference records
- assessment dependencies
- artifact refs

context surface 回答：

- 围绕单个 proposition 当前正在判断什么
- 当前为什么是这个 assessment 状态
- 若要继续推进，还缺什么

补充边界：

- `relevant_findings` 是 committed latest assessment closure，不是 assessment recompute 的 candidate finding set
- recompute 输入组装以 [`assessment-evaluation-context.md`](assessment-evaluation-context.md) 为准；读取面不得把两者合并

## Shared Invariants

三个读取对象共享以下基线：

- proposition-centered read model
- `latest/live` 是读取层解释，不是对象本体 flag
- assessment-derived closure integrity 受 graph/reference 主题文档约束
- projection 可以压缩，不得重定义 evidence semantics

## Related Documents

- [`overview.md`](overview.md)
- [`assessment-evaluation-context.md`](assessment-evaluation-context.md)
- [`graph-and-reference-semantics.md`](graph-and-reference-semantics.md)
- [`../session.md`](schemas/session.md)
- [`../state-surface-schema.md`](schemas/state-surface-schema.md)
- [`../context-surface-schema.md`](schemas/context-surface-schema.md)
