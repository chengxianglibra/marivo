# Artifact → Finding Extraction Contract

本文档定义 Factum 规范模型中 `artifact -> finding` 的独立契约。

状态：draft design。本文是 `docs/analysis/` 下的规范设计文档，不表示对应 HTTP endpoint、持久化结构或当前实现已经完成。

## 目的

在 canonical evidence chain 已固定为：

`artifact -> finding -> proposition -> assessment -> action proposal`

之后，系统还需要一份独立 contract 明确回答：

- 哪些 artifact family 必须进入 canonical finding layer
- 哪些 artifact 只是审计 / 摘要 / projection / bundle，因此不直接抽取 findings
- successful artifact 与 successful extraction 的提交边界如何绑定
- mandatory result artifact 出现 successful empty result 时应如何处理
- replay 时 finding set 与 finding identity 应如何保持稳定

本文目标是让 `finding.md`、runtime lifecycle、以及各 typed intent artifact 文档共享同一套 extraction 语义，而不是各自定义“空结果”“提交成功”“finding 缺失”的含义。

## 非目标

本文不定义：

- 对外 HTTP wire contract、状态码或错误响应 shape
- 具体持久化表、事务实现或任务调度器
- proposition seeding template 的细节
- projection / UI view 的截断与展示策略

## Artifact Classes

### Mandatory Extraction Artifacts

以下 canonical result artifact family 属于 `mandatory extraction`：

- `observe`
- `compare`
- `decompose`
- `detect`
- `correlate`
- `test`
- `forecast`

这些 artifact 的设计目标是向 canonical fact layer 提供新的、可单独引用的事实输入。

因此它们必须满足：

- 只要 artifact 成功进入 committed canonical state，就必须完成 deterministic finding extraction
- 成功 extraction 后必须得到 `1..N` 个 findings
- 不允许以“artifact 成功，但 finding 集为空”的形式进入 committed canonical state

### Non-Fact Artifacts

以下对象不直接作为 canonical finding source：

- 纯审计 artifact
- 纯摘要 artifact
- projection / UI bundle
- 聚合既有 canonical artifact 的派生 bundle，例如 `attribute_bundle`

这些对象可以包含空数组或紧凑视图，但那是 projection / bundle 语义，不是 canonical fact extraction 语义。

`non-fact artifact` 不进入 canonical finding layer，因此不受 `finding_count >= 1` 约束；但它们仍不得与其所复用的上游 canonical artifact contract 冲突。

## Commit Boundary

### Staged Then Commit

对 `mandatory extraction artifact`，canonical runtime 采用两段式内部流程：

1. typed intent 成功执行，artifact payload 通过自身 artifact contract 校验
2. 系统以 artifact payload 为唯一权威输入执行 deterministic finding extraction
3. 只有在 extraction 成功且得到非空 finding set 后，artifact 与 findings 才一起进入 committed canonical state

因此 committed canonical state 的最小可见单元是：

- `artifact + extracted findings`

而不是：

- `artifact` 先 committed，`finding` 后补写

### No Committed Intermediate State

下列状态在 committed canonical state 中非法：

- artifact 已 committed，但 finding extraction 尚未完成
- artifact 已 committed，但 extraction failed
- artifact 已 committed，但 finding set 为空

若 runtime 需要记录执行审计、失败原因或重试信息，应留在 pre-commit / audit 路径，而不是伪装成 canonical committed state。

## Extraction Rules

`artifact -> finding` 抽取必须满足以下规则：

- 只依赖 artifact payload、artifact schema boundary 与显式 extractor 版本
- 不使用模型
- 不依赖自由文本解释
- 不依赖 projection 排序、top-k 结果或 UI 截断
- 不根据 session 中其他对象的当前 live 状态改写 finding 内容
- 相同 artifact 的相同 canonical item boundary 必须映射到相同 `finding_id`

### Cardinality

v1 cardinality 规则如下：

- scalar / single-result artifact item：`1 item -> 1 finding`
- row-based artifact item：`1 row -> 1 finding`
- bucket-based artifact item：`1 bucket -> 1 finding`
- candidate-based artifact item：`1 candidate -> 1 finding`

`mandatory extraction artifact` 的成功条件不是“artifact payload 合法”，而是：

- artifact payload 合法
- extractor 可以稳定识别 canonical item boundary
- extraction 结果满足 `finding_count >= 1`

### Stable Source Boundaries

extractor 必须以 artifact contract 中已定义的 canonical item boundary 为准。

不允许：

- 用 narrative text、summary text 或 UI label 推导 canonical finding
- 用 projection 排名位置代替 source item boundary
- 把多个并列 item 合并成一个新的 claim-like finding
- 因 extractor 版本升级就在 source item boundary 未变化时改写 `finding_id`

## Failure Semantics

以下情况都属于 extraction failure / contract violation：

- artifact payload 未满足其 artifact contract
- extractor 无法稳定定位 canonical source item
- extractor 产生的 payload ref 非 typed ref、跨 session，或指向 projection
- extractor 对同一 `artifact_id + item boundary` 生成多个 `finding_id`
- `mandatory extraction artifact` 推导出 `0 findings`
- replay 同一 artifact 时 finding set 或 identity 在 source boundary 未变化的前提下漂移

这些失败都不应被解释成：

- successful empty result
- consumer 自行忽略的 warning
- “先记 artifact，稍后再补 findings”

## Replay And Idempotency

同一 `artifact_id` replay 时：

- finding set 必须稳定
- finding identity 必须稳定
- finding payload / subject / observed window / provenance 必须可复现

若 replay 结果与既有 committed finding set 不一致，应优先判断：

- artifact payload 是否发生了 breaking change
- canonical item boundary 是否改变
- extractor 是否违反了 deterministic contract

在 source item boundary 未变化时，replay 不得把既有 successful artifact 回放成 successful empty result。

## Downstream Contract

proposition seeding、assessment recompute 与 action proposal refresh 只能消费 committed findings。

因此：

- extraction 成功前不得进行 proposition registration
- extraction 失败不得触发 proposition seeding
- assessment / action proposal 不得引用未 committed 的 artifact-only 中间态

## Family-Level Consequences

本 contract 对各 family 的直接约束如下：

- `observe` / `compare` / `detect` / `correlate` / `test` / `forecast`：成功 artifact 必须至少产出一个 finding
- `decompose`：若当前请求无法形成任何 canonical contribution row，请求应失败，例如落为 `NOT_ATTRIBUTABLE`，而不是成功返回 `rows = []`
- `attribute_bundle`：虽然它不是 finding source，但成功 bundle 不得与其内部 `decompose` source contract 冲突；若任一维度无法形成 committed `decompose` artifact，则整个 `attribute` 请求应失败，而不是成功返回 `drivers[*].rows = []`

## 与其他文档的关系

- [`finding.md`](finding.md) 负责定义 finding schema、subtype、typed refs 与 schema-level illegal states
- [`evidence-engine-runtime-lifecycle.md`](evidence-engine-runtime-lifecycle.md) 负责定义 staged / commit / replay / downstream suppression 的 runtime 语义
- 各 typed intent artifact 文档负责定义各自 artifact contract，但不得重写本文已固定的 extraction boundary
