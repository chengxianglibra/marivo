# Evidence Engine Runtime Lifecycle

本文档定义 Evidence Engine 在 canonical pipeline 之下的运行时生命周期契约。

状态：draft design。本文不重写 `artifact -> finding -> proposition -> assessment -> action proposal` 的对象语义；它只固定运行时推进、串行化、恢复、发布与可见性规则，使主题文档达到可实现的规格完备度。

## 目的

固定以下问题的统一答案：

- 哪些阶段拥有下游工作的调度权
- claim / retry / recovery 的最小责任边界是什么
- 哪些对象可以处于 internal staging，哪些状态可以对外暴露
- `publish_ready`、`externally_visible` 与 `refresh_in_progress` 如何区分
- proposition fan-out 很大时如何做 bounded backpressure
- crash / duplicate delivery / replay 后如何恢复而不制造半更新 canonical state

## 主题位置

Evidence Engine 的 canonical chain 仍然是：

`artifact -> finding -> proposition -> assessment -> action proposal`

其中：

- [`runtime-pipeline.md`](runtime-pipeline.md) 固定阶段顺序、对象边界与最小提交单元
- 本文固定运行时 stage ownership、串行化点、发布边界、恢复与 backpressure 规则
- [`runtime-status-surface.md`](runtime-status-surface.md) 固定 operator-facing status / attempt / backlog / failure visibility 边界
- stage-specific idempotency、authority input 与 dedupe key 由各子主题文档继续细化

本文不定义：

- session root 的外部 HTTP contract
- assessment judgment policy 或 proposal ranking policy
- narrative explanation、UI projection 或 workflow 编排 UI

## Fixed Design Decisions

### 1. internal staging 与 external canonical visibility 必须分离

v1 中 runtime 可以存在 internal staging objects、队列项、claim 状态与重试记录。

但对外 canonical read surface 只允许暴露 externally visible 的稳定结果，不允许暴露以下中间态：

- `artifact committed but findings pending`
- `proposition affected but assessment recompute pending`
- `latest_assessment` 已切换但 proposal 仍是旧集合
- refresh 中间产物导致的半更新 closure

因此：

- internal staging 是 runtime concern，不属于 canonical read model
- publish-ready 不等于 externally visible
- 对外读取面必须只看到旧整套状态或新整套状态

### 2. stage owner 固定由上游 committed output 决定

每个 stage 的下游调度权固定如下：

1. typed intent execution owner 负责产出 artifact candidate
2. artifact commit owner 负责把 artifact 推进到 finding extraction
3. extraction owner 负责产生 committed finding set，并派生 seeding work
4. seeding owner 负责产生稳定 `affected_proposition_ids`，并派生 assessment recompute work
5. assessment owner 负责为单 proposition 提交新的 assessment snapshot 或记为 no-op
6. publish owner 负责在 assessment / proposal 都达到 publish-ready 后切换 externally visible state

规则：

- 上游 stage 只拥有派生下游 work item 的权力，不拥有绕过下游 commit boundary 的权力
- stage owner 可以是同一进程、同一 job 或不同 worker，但逻辑 ownership 必须稳定
- canonical semantics 不依赖具体 worker 拓扑

### 3. proposition 是 assessment / proposal 刷新的串行化单元

v1 中 assessment recompute 与 proposal refresh 的最小串行化单元固定为单个 proposition。

因此：

- 同一 proposition 在任一时刻最多只能有一个 active assessment publish path
- 同一 proposition 的 proposal refresh 不得脱离该 proposition 的 assessment publish path 独立暴露
- 不要求 session 级全局串行化
- 不要求 artifact 级扇出在单线程内完成

这保证：

- 大 fan-out artifact 可以分批推进
- proposition-local latest selection 仍然有唯一提交权
- publish 可在 proposition 粒度保持严格原子可见

### 4. publish 以 proposition-local bundle 为最小可见切换单元

对外 canonical read surface 的最小可见切换单元不是单个 assessment snapshot，而是 proposition-local publish bundle：

- target proposition
- committed latest assessment
- 该 assessment 对应的 live closure
- 与该 latest assessment 匹配的 canonical action proposal set

固定要求：

- assessment snapshot 提交后，先进入 publish-ready，而不是立即 external visible
- proposal refresh 完成前，不得切换 proposition-local latest bundle
- 若 proposal refresh 结果为合法空集，则空 proposal set 也是 bundle 的一部分
- publish 切换必须是单次原子切换，不暴露新旧混合状态

### 5. queue / claim / retry 属于 runtime truth，不属于 canonical truth

runtime 允许为 stage work item 建立队列、claim、lease、attempt 与 retry 语义。

但这些对象：

- 不进入 `SessionStateView`
- 不进入 `PropositionContextView`
- 不改写 canonical object identity
- 只用于确保同一 committed input 不会被无限重复消费

因此，claim 失败、lease 过期、worker crash 等问题的恢复，应通过 runtime lifecycle 与 operator-facing status surface 解决，而不是把临时调度状态塞入 evidence objects。

### 6. fan-out 必须 bounded，backpressure 必须显式

单个 artifact 可以影响大量 proposition，但 runtime 不得把“大量受影响 proposition”解释为“必须立即同步跑完整链路”。

v1 最小要求：

- `affected_proposition_ids` 可以分批 enqueue
- batch / queue 顺序必须稳定且可重放
- backlog 必须可观测，而不是静默堆积
- backpressure 可以延后 recompute，但不能破坏已发布 canonical state

因此：

- 请求线程可以在 artifact + findings committed 后结束
- 下游 recompute / publish 允许异步推进
- 异步推进期间，对外仍暴露上一版 externally visible bundle

## Runtime States

### Stage progression vocabulary

本文统一使用以下运行时状态词汇：

- `staged`：本阶段候选产物已生成，但尚未满足 canonical commit 条件
- `committed`：本阶段 canonical 对象已落入历史真相，可被下游 stage 消费
- `publish_ready`：对外切换所需的 proposition-local bundle 已完整生成
- `externally_visible`：canonical read surface 当前实际暴露的稳定结果
- `failed`：本阶段本次 attempt 失败，需要 runtime 决定重试或人工介入
- `noop`：本阶段执行成功，但 canonical output 与当前已发布结果语义等价

关系固定为：

`staged -> committed -> publish_ready -> externally_visible`

其中：

- 不是每个 stage 都需要单独的 `publish_ready`
- `failed` 与 `noop` 是 attempt outcome，不是 canonical object type
- `externally_visible` 永远滞后或等于 `publish_ready`，绝不领先

## Serialization Points

### Artifact commit

对 mandatory extraction artifact：

- artifact 不得先于 findings 单独 external visible
- extraction 完成前，artifact 只能处于 internal staging
- extraction failure 不得形成外部可见 artifact-only canonical state

### Seeding completion

单次 seeding run 完成时，必须同时固定：

- 所消费的 committed finding snapshot
- 所使用的 template registry snapshot
- 稳定的 `affected_proposition_ids`

这样 assessment recompute 才有稳定入口。

### Proposition publish

同一 proposition 的 publish path 固定为：

1. recompute assessment candidate
2. commit new assessment snapshot 或判断为 no-op
3. refresh proposal set
4. 形成 publish-ready bundle
5. 原子切换 externally visible bundle

任何步骤中断都不得跳过第 5 步直接暴露部分结果。

## Replay And Crash Recovery

### Recovery baseline

runtime 必须假定以下故障随时会发生：

- worker crash
- duplicate delivery
- timeout after side effects
- lease/claim 丢失
- process restart during fan-out

因此恢复基线固定为：

- replay 只以 committed upstream output 为 authority
- 未达到 committed 的 staged candidate 可以丢弃并重建
- 已 committed 但未 publish 的下游产物必须可重新驱动到 publish-ready
- 同一 proposition 的 publish path 必须可重复执行而不破坏 latest selection

### Stage recovery rules

- extraction crash：若 `artifact + findings` 尚未 committed，则整段 extraction 重新执行
- seeding crash：若 committed finding snapshot 已存在，则以该 snapshot 重新执行 seeding
- assessment crash：若新 snapshot 未 committed，则重新 recompute；若已 committed 但未 publish，则从 proposal refresh / publish 阶段继续
- publish crash：若 publish-ready bundle 已形成但切换未完成，则允许重试切换；切换必须保持幂等

## Atomic Read Visibility

对外 canonical read surface 固定采用严格原子可见：

- `SessionStateView` 不得暴露“新 assessment + 旧 proposal”
- `PropositionContextView` 不得暴露尚未完成 publish 的 candidate closure
- refresh-in-progress 只允许进入 operator-facing runtime status surface

因此：

- canonical read surface 优先 correctness，而不是最新 attempt 的即时可见性
- agent 若需要知道后台是否仍在刷新，应读取独立 runtime status surface，而不是从 state/context 推断

## Invalidation Baseline

v1 默认采用 tombstone-first 基线：

- invalidation / redaction / deletion 默认先表现为上游对象失效或缺损
- 下游通过 missing refs、gap reopen、membership 收缩、proposal suppression 或 latest bundle 回退暴露影响
- 物理删除只作为受控例外边界定义，不作为默认恢复路径

更细的 invalidation taxonomy 与 mixed-version 规则由后续专项文档补足。

## Relationship To Session And Read Surfaces

- `session` 仍然只是分析容器；它不承载 claim、lease、attempt 或 backlog
- `SessionStateView` / `PropositionContextView` 只承载 externally visible canonical evidence state
- operator-facing runtime status surface 承载 stage status、failure reason、attempt/correlation id 与 backlog/claim 可见性
- 更细的 operator-facing status object shape 由 [`runtime-status-surface.md`](runtime-status-surface.md) 定义

这三层必须严格分离，避免把运行时调度状态混入 canonical truth。

## Related Documents

- [`overview.md`](overview.md)
- [`runtime-pipeline.md`](runtime-pipeline.md)
- [`runtime-status-surface.md`](runtime-status-surface.md)
- [`finding-proposition-seeding.md`](finding-proposition-seeding.md)
- [`assessment-evaluation-context.md`](assessment-evaluation-context.md)
- [`proposal-policy-engine.md`](proposal-policy-engine.md)
- [`read-surfaces.md`](read-surfaces.md)
- [`schemas/session.md`](schemas/session.md)
