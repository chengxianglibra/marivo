# Evidence Engine Runtime Pipeline

本文档定义 Evidence Engine 主线中的运行时流水线与提交边界。

状态：draft design。本文是主题级总线文档，吸收原 `evidence-engine-runtime-lifecycle`、`artifact-finding-extraction-contract` 与 `proposition-seeding-contract` 的主线职责。

## 目的

固定以下问题的统一答案：

- canonical pipeline 的阶段顺序是什么
- 哪些 artifact family 必须进入 finding layer
- committed canonical state 的最小可见单元是什么
- proposition seeding 何时运行、如何匹配、如何去重
- replay、idempotency、soft invalidation 如何解释

## Canonical Pipeline

Evidence Engine 的目标态运行时流水线固定为：

1. typed intent 执行并形成 artifact
2. 对 committed artifact 做 deterministic finding extraction
3. 对 committed findings 执行 deterministic proposition seeding / registration
4. 对受影响 proposition 执行 assessment recompute
5. 仅在 judgment output 发生变化时提交新的 assessment snapshot
6. 基于 latest assessment 刷新 action proposals
7. state/context 读取层暴露 latest/live canonical state

## Artifact -> Finding

### Mandatory extraction families

以下 artifact family 属于 `mandatory extraction`：

- `observe`
- `compare`
- `decompose`
- `detect`
- `correlate`
- `test`
- `forecast`

这些 artifact 一旦进入 committed canonical state，就必须同时完成 finding extraction，并满足 `finding_count >= 1`。

### Commit boundary

对 `mandatory extraction artifact`：

- 最小 committed 可见单元是 `artifact + extracted findings`
- `artifact committed but extraction pending` 非法
- `artifact committed but extraction failed` 非法
- successful empty result 非法

因此，proposition seeding、assessment recompute 与 action proposal refresh 都只能消费 committed findings。

### Extraction rules

`artifact -> finding` 抽取必须：

- 只依赖 artifact payload、artifact contract 与 extractor version
- 不使用模型
- 不依赖 UI projection、top-k 或 narrative text
- 对同一 `artifact_id + canonical item boundary` 产生稳定 `finding_id`

## Finding -> Proposition

### Seeding input boundary

proposition seeding 的唯一上游输入是 committed findings。

因此：

- extraction pending/failure 的 artifact 不参与 seeding
- successful empty result 不存在合法 committed 形态
- replay 必须以 committed finding set 为权威输入

### Deterministic matching

seed template 的选择、slot matching、creation condition、identity normalization 与 seed output 都必须 deterministic。

v1 支持：

- 单 finding template
- 多 finding 组合 template

组合 template 仍必须通过显式 typed slots 匹配，不允许“临时再找一些相关 findings”。

### Registration and identity

`proposition_id` 绑定 session-local judgment semantics，而不是 seed 批次。

固定规则：

- `system_seeded` 与 `system_seeded` 按 judgment semantics 去重
- `agent_authored` 与 `agent_authored` 按 judgment semantics 去重
- `system_seeded` 与 `agent_authored` 不跨来源共享 proposition identity
- `seed_finding_refs` 只在首次注册时写入，不做实时增量维护

## Assessment Refresh And Downstream Suppression

assessment recompute 只在 proposition registration 或相关 finding 变化后运行。

下游 suppression 规则：

- extraction 成功前不得注册 proposition
- proposition 注册成功前不得触发 assessment recompute
- assessment 未形成 committed latest state 前，不得刷新 action proposals
- 读取面不得暴露 artifact-only 中间态作为 canonical state

## Replay And Idempotency

### Artifact replay

artifact replay 的权威输入是 artifact payload，而不是旧 projection。

replay 允许：

- 重跑 extraction
- 重跑 seeding
- 重跑 assessment recompute
- 形成新的 assessment snapshots 与 action proposals

replay 不允许：

- 改写既有 artifact identity
- 在 source item boundary 未变化时漂移 finding identity
- 把既有 successful artifact 回放成 successful empty result

### Snapshot behavior

- proposition 是 registry object，不因 assessment 推进而重建
- assessment 是 immutable snapshot
- action proposal 是 immutable projection snapshot
- latest/live 由读取层解释，不回写历史对象

## Soft Invalidation

当上游对象当前不可解引用时，默认采用 soft invalidation：

- 历史 canonical objects 保留
- 通过 missing refs、gap reopen、membership 收缩与 latest selection 变化暴露影响
- 不通过硬删除伪装成“从未发生”

## Related Documents

- [`overview.md`](overview.md)：主题总览与阅读顺序
- [`inference-and-gap-engine.md`](inference-and-gap-engine.md)：assessment recompute 的规则过程
- [`graph-and-reference-semantics.md`](graph-and-reference-semantics.md)：refs、edges 与 closure integrity
- [`../finding.md`](schemas/finding.md)：finding schema
- [`../proposition.md`](schemas/proposition.md)：proposition schema
