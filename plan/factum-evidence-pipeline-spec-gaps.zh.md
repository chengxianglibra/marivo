# Factum Evidence Pipeline 规格缺口补齐计划

## 1. 背景

基于 `docs/analysis/` 下当前 Evidence Engine 设计，`artifact -> finding -> proposition -> assessment -> action proposal` 主链已经定义了较强的 canonical 语义边界，包括：

- typed intent 产出 artifact
- deterministic finding extraction
- deterministic proposition seeding / registration
- immutable assessment snapshot 与 strict-chain latest 选择
- state/context read surface 只暴露 latest/live canonical state

这些设计已经较完整地覆盖了“对象模型”和“结果语义”，但在真正进入实现时，仍存在一批关键的**运行时规格空白**。这些空白的共同特点是：

- 不直接表现为 schema 缺字段
- 但会在调度、恢复、版本升级、读写一致性和运维阶段立即出现
- 如果不先补齐，工程实现将被迫临时发明隐式规则，最终导致设计和实现分叉

本文目标不是再讨论总体架构方向，而是把当前 evidence pipeline 的主要规格缺口整理成一份可执行的补齐顺序与文档更新清单。

## 2. 判断摘要

当前最需要补齐的，不是新的 analysis intent，也不是新的 canonical object schema，而是以下 6 类运行时规格：

1. 调度、claim、backpressure 与 ownership 模型
2. 分阶段 replay / idempotency / crash recovery 模型
3. version migration / re-execution policy
4. publish / read visibility 的原子性语义
5. invalidation / redaction / deletion lifecycle
6. operator-facing observability / audit contract

建议补齐原则：

- **对外 canonical read surface 保持严格**
- **对内 runtime lifecycle 允许 staging、重试和恢复**
- **将 correctness 与 execution 解耦**
- **优先定义 authority boundary、去重键、状态迁移与 failure visibility**

## 3. 缺口一：调度 / claim / backpressure / ownership 模型

### 3.1 当前缺失

文档已经定义：

- pipeline 的阶段顺序
- `finding-proposition-seeding` 输出 `affected_proposition_ids`
- assessment recompute 以单 proposition 为 evaluation unit

但没有定义：

- 谁拥有 downstream work 的调度权
- `affected_proposition_ids` 如何被 claim / batch / retry
- fan-out 很大时如何做 backpressure
- proposal refresh 是同步跟随还是异步队列
- session 级、artifact 级、proposition 级分别由谁串行化

### 3.2 为什么重要

如果没有这层规格，实现会在两种极端之间摇摆：

- 把全链路做成同步大事务，导致吞吐和恢复性很差
- 用后台 job 异步推进，但没有 claim/lease 规范，导致重复执行和竞争提交

### 3.3 优先补齐的文档

- `docs/analysis/evidence-engine/runtime-pipeline.md`
- `docs/analysis/evidence-engine/finding-proposition-seeding.md`
- `docs/analysis/evidence-engine/schemas/session.md`

### 3.4 建议新增的文档内容

- pipeline stage owner 定义
- `affected_proposition_ids` 的 enqueue / dequeue / claim 语义
- artifact publish、assessment commit、proposal refresh 的串行化点
- bounded fan-out 与 backlog/backpressure 行为

### 3.5 示例故障

一个 `detect` artifact 最终命中 5000 个 proposition。实现 A 在请求线程里同步重算；实现 B 把 proposition 扔给多个 worker 并发处理。由于文档没有定义 claim 和串行化，多个 worker 同时争抢同一 proposition 的 latest assessment 提交权。

## 4. 缺口二：分阶段 replay / idempotency / crash recovery

### 4.1 当前缺失

现有文档强调 deterministic 和 replay，但还没有把 replay 拆到足够可实现的阶段：

- ingestion replay
- extraction replay
- seeding replay
- assessment replay
- proposal replay

也没有定义：

- 每一层的 authority input
- 每一层的 dedupe key
- crash 后从哪个 stage 恢复
- partial progress 如何判断为已完成 / 待重跑 / 待丢弃

### 4.2 为什么重要

只要系统不是单进程内存态，就一定会遇到：

- process crash
- worker restart
- duplicate delivery
- timeout after side effects

如果没有分阶段幂等语义，系统会在“重跑导致重复对象”和“跳过导致下游缺失”之间来回摆动。

### 4.3 优先补齐的文档

- `docs/analysis/evidence-engine/runtime-pipeline.md`
- `docs/analysis/evidence-engine/artifact-finding-generation-rules.md`
- `docs/analysis/evidence-engine/finding-proposition-seeding.md`
- `docs/analysis/evidence-engine/proposal-policy-engine.md`

### 4.4 建议新增的文档内容

- 每个 stage 的 replay authority
- `(artifact_id, extractor_version)`、`(finding_snapshot, seed_registry_version)` 等稳定 dedupe key
- stage-complete / stage-failed / publish-ready 的明确语义
- crash recovery 决策表

### 4.5 示例故障

seeding 已经注册了 proposition，但 assessment 只成功提交了一半。系统重启后，如果没有 stage checkpoint，某些 proposition 会被重复重算，另一些则永远停在 `latest_assessment = null`。

## 5. 缺口三：version migration / re-execution policy

### 5.1 当前缺失

文档已经有多个 version 轴：

- `artifact_schema_version`
- `extractor_version`
- `template_version`
- `derivation_version`
- `rule_version`
- `policy_version`

但没有定义升级策略：

- 何时只对新写入生效
- 何时要求旧 session 回放 extraction / seeding / assessment
- 旧 version 的 canonical state 是否允许长期共存
- migration 是 eager 还是 lazy

### 5.2 为什么重要

这是正式上线后最容易出现“设计正确、实现混乱”的地方。没有 migration policy，`latest_assessment` 可能是新 rule 算出来的，而 proposition identity 却来自旧 seeding 语义，最终形成版本拼接状态。

### 5.3 优先补齐的文档

- `docs/analysis/evidence-engine/artifact-finding-generation-rules.md`
- `docs/analysis/evidence-engine/finding-proposition-seeding.md`
- `docs/analysis/evidence-engine/inference-and-gap-engine.md`
- `docs/analysis/evidence-engine/schemas/assessment.md`

### 5.4 建议新增的文档内容

- version bump 分类（兼容 / 破坏式 / 需回放）
- session 级 migration marker
- re-execution scope 判定规则
- mixed-version canonical state 的允许边界与禁止边界

### 5.5 示例故障

新版 seeding template 改了 proposition identity normalization。新 artifact 进来后生成了一批新的 proposition，但旧 proposition 没有被迁移或失效，结果同一 judgment semantics 在 state surface 里出现两条活跃命题。

## 6. 缺口四：publish / read visibility 原子语义

### 6.1 当前缺失

设计已经规定：

- assessment snapshot 是 immutable
- latest 选择必须 strict-chain
- proposal refresh 依赖 committed latest assessment

但还没有定义：

- assessment commit 与 latest exposure 的关系
- proposal refresh 与 read surface 暴露的原子边界
- read-after-write 的一致性级别
- operator / agent 在刷新中的可见状态

### 6.2 为什么重要

如果 publish 边界不清楚，读取面就会出现“新 assessment + 旧 proposal”或“新 latest + 不完整 closure”这类半更新状态，直接破坏文档一直强调的 canonical clean read surface。

### 6.3 优先补齐的文档

- `docs/analysis/evidence-engine/runtime-pipeline.md`
- `docs/analysis/evidence-engine/proposal-policy-engine.md`
- `docs/analysis/evidence-engine/graph-and-reference-semantics.md`
- `docs/analysis/evidence-engine/schemas/assessment.md`
- `docs/analysis/evidence-engine/read-surfaces.md`

### 6.4 建议新增的文档内容

- publish-ready 与 externally visible 的区分
- latest assessment publish 和 proposal publish 的顺序规则
- state/context 读取面对 refresh-in-progress 的处理规则
- 对外是否允许 “assessment 已更新、proposal 仍是旧版本” 的短暂窗口

### 6.5 示例故障

用户读取 `/state` 时已经看到了新的 blocking gaps，但 `/context` 里看到的 proposal 仍基于旧 assessment 生成，造成 agent 对当前建议和当前判断的理解相互冲突。

## 7. 缺口五：invalidation / redaction / deletion lifecycle

### 7.1 当前缺失

文档提到了 soft invalidation、missing refs 显式暴露、不能静默修复，但没有完整定义：

- 谁可以触发 invalidation
- invalidation 作用于 artifact / finding / proposition / assessment 的哪一层
- redaction、compliance deletion、retention purge 是否用同一语义
- downstream repair 是 reopen gap、recompute 还是 tombstone

### 7.2 为什么重要

真正生产环境一定会碰到：

- 上游数据纠错
- artifact 重放修正
- 合规删除
- 数据保留期清理

如果没有 lifecycle ownership，不同实现会选择 hard delete、soft delete、tombstone 或 no-op，最终让 canonical graph 行为不一致。

### 7.3 优先补齐的文档

- `docs/analysis/evidence-engine/runtime-pipeline.md`
- `docs/analysis/evidence-engine/graph-and-reference-semantics.md`
- `docs/analysis/evidence-engine/finding-proposition-seeding.md`
- 如有必要，新增专门的 invalidation lifecycle 文档

### 7.4 建议新增的文档内容

- invalidation trigger taxonomy
- tombstone / redaction / removal 的语义区分
- invalidated upstream object 的 read behavior
- downstream recompute / reopen / suppression 规则

### 7.5 示例故障

一个 artifact 因合规要求被删除。实现 A 直接 hard-delete finding；实现 B 只给 artifact 打 invalid 标记；实现 C 不回写任何状态。最后同一 proposition 在不同部署中出现不同的 latest/live closure。

## 8. 缺口六：operator-facing observability / audit contract

### 8.1 当前缺失

当前对象模型已经很强调 canonical correctness，但对于运维和排障还缺少统一的 runtime 观测面，例如：

- proposition 为什么仍然 `latest_assessment = null`
- extraction 是未触发、失败还是 no-op
- proposal refresh 是否因为 no-op 被抑制
- backlog 是积压在 extraction、publish 还是 recompute

### 8.2 为什么重要

如果没有明确 observability contract，系统一旦出问题，只能靠数据库手工排查。设计再严格，也很难被实际维护。

### 8.3 优先补齐的文档

- `docs/analysis/evidence-engine/runtime-pipeline.md`
- `docs/analysis/evidence-engine/schemas/session.md`
- `docs/analysis/evidence-engine/proposal-policy-engine.md`
- `docs/analysis/evidence-engine/schemas/finding.md`

### 8.4 建议新增的文档内容

- stage run / stage result / failure reason 的统一对象或 schema
- session、artifact、proposition 粒度的 runtime health 指标
- correlation id / attempt id / last successful stage
- 对 agent 可见与仅 operator 可见的状态边界

### 8.5 示例故障

某个 proposition 长时间没有 `latest_assessment`。如果没有统一运行时状态面，调用方无法区分它是尚未进入重算、重算失败、被 version mismatch 阻塞，还是只是 no-op 后没有形成首个 snapshot。

## 9. 文档补齐顺序建议

建议按下面顺序补文档，而不是并行散改：

### 第一优先级：先补运行时骨架

1. `runtime-pipeline.md`
2. `schemas/session.md`
3. 如有必要新增 `runtime-lifecycle.md` 或等价文档

优先把下面几件事写死：

- internal staging vs external canonical visibility
- stage ownership / serialization points
- replay / retry / failure visibility
- publish boundary

### 第二优先级：再补 stage-specific contract

1. `artifact-finding-generation-rules.md`
2. `finding-proposition-seeding.md`
3. `assessment-evaluation-context.md`
4. `proposal-policy-engine.md`

这一轮重点补：

- 每一层的 authority input
- dedupe key
- retry / replay 语义
- downstream trigger / suppression 规则

### 第三优先级：最后补版本与失效治理

1. `graph-and-reference-semantics.md`
2. `schemas/assessment.md`
3. 新增 migration / invalidation 专项文档（如拆分更清晰）

这一轮重点补：

- version upgrade policy
- mixed-version state 边界
- invalidation / redaction / deletion 行为
- read surface 对缺损状态的统一暴露方式

## 10. 建议的文档交付物

为了避免继续把关键语义散落在多个章节里，建议最终至少形成以下交付物：

1. **运行时生命周期主文档**
   - staging、publish、retry、recovery、ownership、serialization

2. **阶段级幂等与回放规则**
   - artifact、finding、proposition、assessment、proposal 各自的 authority input 与 dedupe key

3. **版本迁移与失效治理文档**
   - migration、redaction、deletion、tombstone、repair 规则

4. **运维观测面定义**
   - stage status、failure reason、health summary、operator view

## 11. 验收标准

当以下问题都能在文档中直接找到答案时，可认为这轮规格补齐基本完成：

- 一个 artifact 扇出到大量 proposition 时，系统如何调度与限流？
- worker 在 seeding 或 assessment 中途崩溃后，从哪里恢复？
- extractor / template / rule 升级后，旧 session 是否必须回放？
- 读取层是否可能看到新 assessment + 旧 proposal？
- 上游 artifact 被 redaction 或删除后，下游如何修复？
- `latest_assessment = null` 时，如何判断是未触发、失败还是合法未产出？

如果这些问题仍需要靠“实现默认行为”来猜，说明 evidence pipeline 还没有真正达到可落地实现的规格完备度。
