# Evidence Engine 主题总览

本文档是 `docs/analysis/` 下 Evidence Engine 主题的总入口。

状态：draft design。本文负责组织 Evidence Engine 主线文档，不替代各对象 schema 或 rule-family 子专题。

## 目的

Evidence Engine 的目标态规范链路为：

`artifact -> finding -> proposition -> assessment -> action proposal`

围绕这条链路，当前文档体系按以下层次组织：

- 主题主线：解释整体模型、运行时、推断、图语义、读取面
- 对象 schema：定义 `session`、`finding`、`proposition`、`assessment`、`action proposal` 及读取面 schema
- Rule-family 子专题：定义 gate families 与 rule-family 设计约束

## 阅读顺序

建议按以下顺序阅读：

1. [`../agent-interaction-contract-principles.md`](../foundations/agent-interaction-contract-principles.md)
2. 本文
3. [`runtime-lifecycle.md`](runtime-lifecycle.md)
4. [`runtime-pipeline.md`](runtime-pipeline.md)
5. [`runtime-status-surface.md`](runtime-status-surface.md)
6. [`artifact-finding-generation-rules.md`](artifact-finding-generation-rules.md)
7. [`finding-proposition-seeding.md`](finding-proposition-seeding.md)
8. [`inference-and-gap-engine.md`](inference-and-gap-engine.md)
9. [`assessment-evaluation-context.md`](assessment-evaluation-context.md)
10. [`support-oppose-and-status-resolution.md`](support-oppose-and-status-resolution.md)
11. [`gap-confidence-and-transition-materialization.md`](gap-confidence-and-transition-materialization.md)
12. [`proposal-policy-engine.md`](proposal-policy-engine.md)
13. [`graph-and-reference-semantics.md`](graph-and-reference-semantics.md)
14. [`migration-and-invalidation.md`](migration-and-invalidation.md)
15. [`read-surfaces.md`](read-surfaces.md)
16. [`../finding.md`](schemas/finding.md)
17. [`../proposition.md`](schemas/proposition.md)
18. [`../assessment.md`](schemas/assessment.md)
19. [`../action-proposal.md`](schemas/action-proposal.md)

## 主题边界

Evidence Engine 主题覆盖：

- canonical evidence chain 的对象分层
- `artifact -> finding -> proposition -> assessment -> action proposal` 的运行时更新规则
- rule engine、gap lifecycle、judgment policy 与 rule registry
- canonical edge taxonomy、typed refs、closure integrity
- session root、state surface、context surface 作为 evidence 消费面

Evidence Engine 主题不覆盖：

- typed intent 的请求/响应 schema
- 派生 intent 的 DAG 展开
- 对外 HTTP wire contract
- narrative explanation 或 UI projection 的文案层设计

## 对象分层

### Artifact Layer

- `artifact` 是 typed intent 的完整执行结果
- 它是 replay、审计、provenance 与下游 extraction 的权威输入
- 它不是主决策接口

### Fact Layer

- `finding` 是从 artifact 确定性抽取的原子事实单元
- finding 只表达事实，不表达判断状态
- `artifact -> finding` 的提交边界属于 runtime contract，而不是 projection 语义

### Judgment Layer

- `proposition` 表达“要判断什么”
- `assessment` 表达“当前判断到什么程度”
- `evidence_gap` 与 `inference_record` 是 judgment layer support objects

### Action-Support Layer

- `action proposal` 是基于最新 assessment 产生的 planning shortcut
- proposal 不能回写 judgment semantics
- agent 必须能绕过 proposal，仅依赖 proposition + assessment + gaps 做决策

## 文档职责

### 主题主线

- [`runtime-lifecycle.md`](runtime-lifecycle.md)：运行时 ownership、串行化点、发布边界、恢复与 backpressure 基线
- [`runtime-pipeline.md`](runtime-pipeline.md)：运行时主链、commit boundary、对象级 suppression 与主题导航
- [`runtime-status-surface.md`](runtime-status-surface.md)：operator-facing stage status、attempt、failure reason 与 backlog 可见性
- [`artifact-finding-generation-rules.md`](artifact-finding-generation-rules.md)：artifact -> finding 的统一生成协议、identity 规则与 family-level 抽取提案
- [`finding-proposition-seeding.md`](finding-proposition-seeding.md)：finding -> system-seeded proposition 的模板、registration、identity 与 replay contract
- [`inference-and-gap-engine.md`](inference-and-gap-engine.md)：assessment recompute、rule families、gap management、judgment policy、rule registry
- [`assessment-evaluation-context.md`](assessment-evaluation-context.md)：assessment recompute 的 evaluation context、candidate finding set 与触发映射
- [`support-oppose-and-status-resolution.md`](support-oppose-and-status-resolution.md)：support / oppose aggregation 与最终 status 决议
- [`gap-confidence-and-transition-materialization.md`](gap-confidence-and-transition-materialization.md)：gap、confidence、transition 与 candidate output materialization
- [`proposal-policy-engine.md`](proposal-policy-engine.md)：`latest_assessment -> action proposal[]` 的输入边界、候选生成、排序、identity 与 refresh/no-op 规则
- [`graph-and-reference-semantics.md`](graph-and-reference-semantics.md)：edge taxonomy、typed refs、hard/soft refs、closure integrity
- [`migration-and-invalidation.md`](migration-and-invalidation.md)：version bump 分类、mixed-version 边界、tombstone-first 与受控删除治理
- [`read-surfaces.md`](read-surfaces.md)：session root、state surface、context surface 的主题级关系

### 对象 schema

- [`../session.md`](schemas/session.md)
- [`../finding.md`](schemas/finding.md)
- [`../proposition.md`](schemas/proposition.md)
- [`../assessment.md`](schemas/assessment.md)
- [`../action-proposal.md`](schemas/action-proposal.md)
- [`../state-surface-schema.md`](schemas/state-surface-schema.md)
- [`../context-surface-schema.md`](schemas/context-surface-schema.md)

### Rule-family 子专题

- [`../precondition-gate-contract.md`](rules/precondition-gate-contract.md)
- [`../quality-gate-contract.md`](rules/quality-gate-contract.md)
- [`../comparability-gate-contract.md`](rules/comparability-gate-contract.md)
- [`../rule-family-design-checklist.md`](rules/rule-family-design-checklist.md)

## 设计基线

Evidence Engine 主线当前固定以下 v1 基线：

- facts extracted deterministically by code
- canonical refs 默认 session-local
- `latest/live` 是读取层语义，不是对象本体 mutable flag
- assessment snapshot 按需创建，采用严格 supersede 链选主
- resolved gap 再次出现时创建新 gap instance
- 读取面主骨架围绕 `proposition + latest_assessment`

更细的字段语义、family-level rule contract 与读取载荷形状，分别以下游主线文档和对象 schema 为准。
