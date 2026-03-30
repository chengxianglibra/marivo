# Graph And Reference Semantics

本文档定义 Evidence Engine 的 canonical edge taxonomy 与 typed reference integrity 基线。

状态：draft design。本文吸收原 `evidence-graph-edge-semantics` 与 `reference-integrity-contract` 的主线职责。

## 目的

本文统一回答：

- 哪些对象间关系进入 canonical edge semantics
- 哪些关系只是 lineage/provenance，哪些属于 runtime membership
- canonical refs 如何分类
- 写入与读取时如何处理 dangling refs 与 closure integrity

## Canonical Node Set

本文覆盖的 canonical node types 为：

- `artifact`
- `finding`
- `proposition`
- `assessment`
- `evidence_gap`
- `inference_record`
- `action_proposal`

## Edge Taxonomy

v1 的 edge semantics 由对象 schema 中的 typed refs 承载，不新增独立 `Edge` canonical object。

核心 edge families：

- `finding -> artifact`：`extracted_from`
- `proposition -> finding`：`seeded_by`
- `proposition -> proposition`：`derived_from`
- `assessment -> proposition`：`assesses`
- `assessment -> assessment`：`supersedes`
- `assessment -> finding`：`supports` / `opposes`
- `assessment -> evidence_gap`：`blocks_on` / `has_non_blocking_gap`
- `assessment -> inference_record`：`applies_record`
- `action_proposal -> assessment`：targets primary/related assessments
- `action_proposal -> proposition`：targets proposition
- `action_proposal -> evidence_gap`：serves gap

## Lineage vs Runtime Membership

必须严格区分两类语义：

- lineage / provenance edges：表达来源、派生、creation-time seed
- runtime membership edges：表达某个 assessment snapshot 下的 support / oppose / gap / record membership

固定要求：

- `seeded_by` 不等价于 `supports`
- `derived_from` 不等价于 cross-proposition judgment dependency
- support/oppose/gap/record membership 只绑定 assessment snapshot
- latest/live 是读取层解释，不是 edge 本体 mutable state

## Reference Taxonomy

### Hard refs

hard refs 会影响 identity anchor、latest/live closure 或当前判断归属完整性。

要求：

- 写入时必须可解引用
- 读取 latest/live closure 时不允许悬空
- 悬空应视为 canonical inconsistency，而不是普通 warning

### Soft refs

soft refs 表达 lineage/provenance。

要求：

- 写入时仍要满足 shape 与边界校验
- 读取时允许当前不可解引用
- 不得静默丢弃，必须显式暴露缺失

### Projection handles

projection refs、top-k handles、UI row keys 不是 canonical refs。

它们不得：

- 出现在 canonical ref 字段位置
- 替代 source artifact / source finding / source proposition refs
- 被读取面提升成新的写入 authority

## Core Integrity Rules

v1 固定以下完整性基线：

- canonical refs 必须是 typed refs
- 默认禁止跨 session canonical refs
- canonical ref 图必须保持 DAG
- 新 ref shape 优先最小自包含，而不是依赖外层上下文猜测

## Write-Time Validation

所有 canonical refs 写入时至少校验：

- ref shape 合法
- target object type 与字段语义一致
- 未使用裸字符串 locator
- 未使用 projection handle 伪装成 canonical ref
- 未越过 session-local 边界
- 不引入 DAG violation

assessment-derived refs 一律按 hard ref 处理。

## Read-Time Semantics

### Hard refs

- 不得静默忽略
- 不得把缺失 target 降格成 narrative warning
- state/context surface 一旦返回 latest assessment，其 direct live membership 必须可稳定解引用

### Soft refs

- 上层 canonical object 仍可读取
- 缺失必须显式暴露
- 不得通过删除 ref 语义伪装完整 closure

## Read Surface Invariants

state/context surface 只复用 canonical objects 上已经成立的 refs，不定义新的写入 authority。

固定不变量：

- `SessionStateView` 的 assessment-derived closure 必须自洽
- `PropositionContextView` 的 seed hydration 与 latest assessment closure 必须自洽
- 读取层不得补写、修正或降格 canonical refs

## Related Documents

- [`runtime-pipeline.md`](runtime-pipeline.md)：object lifecycle 与 replay
- [`read-surfaces.md`](read-surfaces.md)：session/state/context 的读取组织
- [`../finding.md`](schemas/finding.md)
- [`../proposition.md`](schemas/proposition.md)
- [`../assessment.md`](schemas/assessment.md)
- [`../action-proposal.md`](schemas/action-proposal.md)
