# Evidence Engine Runtime Lifecycle

本文档定义 Factum 证据引擎在目标态规范模型（canonical model）下的运行时生命周期（runtime lifecycle）设计。

状态：draft design。本文是 `docs/analysis/` 下的运行时生命周期设计文档，不表示对应 HTTP endpoint、持久化表结构或当前实现已经完成。

## 目的

在规范证据链（canonical evidence chain）已经收敛为：

`artifact -> finding -> proposition -> assessment -> action proposal`

之后，系统仍需要一份独立的运行时生命周期规范，回答以下问题：

- 每一层规范对象（canonical object）何时创建、何时刷新、何时退出当前态
- 增量执行与重放（replay）时，哪些对象必须复用标识（identity），哪些必须产生新版本
- 当上游对象失效、不可解引用或被重新评估时，状态应如何演化
- 幂等（idempotent）、快照（snapshot）、最新态选择与读取面之间的责任边界如何划分

本文目标是把这些规则固定为目标态设计基线，使后续实现、评审与 API 绑定都共享同一套运行时语义（runtime semantics）。

## 非目标

本文不定义：

- 对外 HTTP wire contract、path、query string、分页与兼容参数
- 具体数据库表、索引、事务细节
- 当前 `observation / claim / recommendation` 模型到新 canonical model 的迁移策略
- session-level projection 的排序 policy 细节

state surface 与 context surface 在本文中只作为 canonical lifecycle 的消费方被引用，不展开为独立 schema 主线。

## 运行时总览

目标态下，Evidence Engine 的规范运行时流水线（canonical runtime pipeline）固定为：

1. 类型化意图（typed intent）执行并提交工件（artifact）
2. 基于 artifact 做确定性事实单元抽取（deterministic finding extraction）
3. 基于 findings 按种子模板（seed template）自动注册命题（proposition）
4. 对受影响 proposition 执行评估重算（assessment recompute）
5. 仅在规范评估（canonical assessment）输出发生变化时落新快照
6. 基于最新评估（latest assessment）刷新动作候选（action proposal）
7. 状态/上下文读取层只暴露最新态/活跃态规范状态（latest/live canonical state）

这条流水线体现以下边界：

- 动作面（action surface）负责执行类型化意图（typed intent），不直接定义判断状态（judgment state）
- 规范对象层（canonical object layer）负责事实抽取、命题注册、评估快照与动作候选刷新
- 读取面（read surface）负责读取最新态/活跃态对象（latest/live objects），不回写规范对象本体

## Lifecycle 原则

### 1. runtime lifecycle 围绕规范对象（canonical objects），而不是围绕步骤响应（step response）

步骤响应（step response）可以携带谱系（lineage）、工件引用（artifact ref）与有界投影（bounded projection），但它不是规范状态（canonical state）的权威来源。

规范状态的运行时更新必须以对象层为主语：

- 工件（`artifact`）先提交
- 事实单元（`finding`）再抽取
- 命题（`proposition`）再注册
- 评估状态（`assessment`）再评估
- 动作候选（`action proposal`）最后刷新

### 2. 标识边界（identity boundary）与版本边界（version boundary）必须分离

不同规范对象（canonical object）的标识稳定边界不同：

- `artifact_id` 绑定单次工件谱系（artifact lineage）
- `finding_id` 绑定单个 artifact 内的规范事实项（canonical fact item）
- `proposition_id` 绑定会话内局部判断语义（session-local judgment semantics）
- `assessment_id` 绑定 proposition 下的具体快照
- `action_proposal_id` 绑定 assessment snapshot 与 proposal payload semantics

这些对象的 schema/version 升级、提取器版本变化或读取投影变化，不得未经明确语义变化就改写对象标识（identity）。

### 3. runtime 默认采用 append-only + snapshot 语义

目标态不把判断层对象建模成可原地重写的 mutable blob。

原则如下：

- `artifact` 是 append-only lineage entry
- `finding` 由 artifact 决定，可重放，但不做跨 artifact 原地合并
- `proposition` 是 session-local registry object，不因评估推进而重建
- `assessment` 是 immutable snapshot
- `action proposal` 是 immutable projection snapshot

### 4. 失效采用 soft invalidation，而不是硬删除

上游对象不可解引用、被清理或在 replay 后不再参与 latest state 时，系统默认：

- 保留历史 canonical objects 以供审计
- 不把依赖对象整体硬删或强制 tombstone 成不可读
- 通过 `missing_* refs`、新的 gaps、superseded snapshots 与 live membership 重算显式暴露影响

这保证 agent 能区分：

- 历史上曾经发生过什么
- 当前 latest state 还能被哪些 canonical 依据支撑

### 5. latest/live 是读取层语义，不是对象本体状态位

以下概念属于 runtime read semantics，而不是 canonical object 本体字段：

- `latest_assessment`
- active proposition membership
- latest/live finding membership for assessment support / oppose
- 当前有效 action proposal 集合

历史对象不应因为新对象出现而被回写 `is_latest` 或等价 mutable flag。

## Canonical Object Lifecycles

### Artifact Lifecycle

#### 创建

`artifact` 在 typed intent 成功执行且结果通过该 step 的 canonical artifact contract 后被提交。

创建要求：

- artifact 先于任何下游 canonical object 提交
- artifact 一旦提交，其 lineage、step ref、schema boundary 即固定
- 同一次 step 执行若产出多个 artifact，应各自拥有独立 `artifact_id`

#### 幂等

同一 `artifact_id` 的重复读取、重复抽取或重复 replay，不得生成新的 artifact identity。

若重新执行相同 step，即使 step type 与 params 完全相同，只要形成了新的 artifact lineage，就必须生成新的 `artifact_id`。

#### 重放

artifact replay 指对既有 artifact lineage 重新驱动下游 extraction / registration / assessment 流程，而不是复用旧 step response 直接拼装 state。

artifact replay 的最小保证：

- replay 必须以 artifact payload 为权威输入
- replay 不得要求保留旧 projection 才能恢复 finding
- replay 可以产生新的 assessment snapshots 与 action proposals，但不应改写 artifact identity

### Finding Lifecycle

#### 创建

`finding` 由单个 artifact 做 deterministic extraction 得到。

创建要求：

- 每个 finding 必须只指向一个权威 `artifact_id`
- finding 粒度必须是 artifact 中的最小可引用事实单元
- `finding_id` 必须由 artifact 内稳定 item boundary 导出

#### 幂等

同一 artifact replay 时：

- 相同 canonical fact item 必须得到相同 `finding_id`
- finding payload、subject、observed window 与 provenance 应保持可复现

系统不得因为：

- extractor 重跑
- projection 截断差异
- 读取顺序变化

而生成新的 `finding_id`。

#### 增量与退出

finding 不做跨 artifact 语义去重，也不在事实层合并“看起来相同”的事实。

因此：

- 新 artifact 到达时，可产生新的 findings
- 旧 findings 仍保留为历史事实单元
- 某 finding 是否仍参与 current assessment support/oppose，属于 judgment runtime 的 live membership 问题，不是 finding 自身生命周期问题

#### 失效

当其来源 artifact 当前不可解引用时：

- finding 历史 identity 不自动消失
- 依赖该 finding 的 proposition / assessment 在后续 recompute 中应显式暴露缺失影响
- 具体暴露方式包括 `seed_entries.finding = null`、新的 gaps、support/oppose membership 收缩

### Proposition Lifecycle

#### 注册策略

目标态采用 `Auto-register Seeds`：

- finding extraction 完成后，系统按适用的 deterministic seed templates 扫描 findings
- 任一 finding 或 finding 组合满足某个 proposition template 的 creation condition 时，立即注册 proposition
- proposition 注册与 assessment readiness 分离；命题可以先注册、后评估

这意味着：

- `latest_assessment = null` 是 proposition 的合法早期状态
- proposition-centered state surface 必须能暴露“已注册但尚未形成 assessment”的对象

#### Identity

`proposition_id` 绑定 session-local judgment semantics，而不是绑定某一次评估结果或某一组即时 support findings。

因此：

- 同一 session 中，同一 judgment semantics 重读时必须复用同一 `proposition_id`
- `seed_finding_refs` 的顺序、数量或后续缺失情况，不应单独导致 proposition identity 改变
- 新 finding 到达若只是为现有命题补充支持或反驳证据，应进入该 proposition 的后续 assessment 轨道，而不是重建 proposition

#### 来源

v1 中 proposition 可来自两类入口：

- `system_seeded`
- `agent_authored`

二者共享同一 proposition registry 与 assessment family 轨道。

运行时要求：

- `agent_authored` proposition 注册后不走旁路
- 若暂时没有足够 findings，仍以 proposition 存在、`latest_assessment = null` 或后续 `insufficient` assessment 表达

#### 活跃与退出

proposition 是否属于当前 session judgment track，由读取层根据 runtime registry 与 latest assessment pointer 决定。

目标态下，proposition 退出 active track 的典型原因应是：

- session 生命周期结束或被显式关闭
- proposition 被更高层 runtime policy 标记为不再继续评估
- proposition 在 replay / invalidation 后被判定不再属于当前 target state set

退出 active track 不等价于 proposition 历史对象被删除。

#### 失效

若 proposition 的部分 seed 当前不可解引用：

- proposition 仍必须可读取
- `seed_finding_refs` 不被静默改写
- 读取层通过 `seed_entries.finding = null` 暴露当前缺失

seed provenance 缺失表示当前读取不完整，不自动宣告 proposition 无效。

### Assessment Lifecycle

#### 触发

assessment recompute 在以下事件后可被触发：

- 新 proposition 注册
- proposition 所依赖的相关 findings 新增、缺失或被 replay 重建
- 上游 assessment family 需要重算其 status、confidence、gaps 或 rules

assessment recompute 的调度策略不在本文固定，但 canonical 写入规则固定。

关于 rule family、固定 evaluation order、升级/降级与冲突处理，见 [`inference-rule-engine-contract.md`](inference-rule-engine-contract.md)；本文只定义 runtime lifecycle 与 snapshot 写入边界。

#### Snapshot 策略

目标态采用 `Change-only` snapshot policy。

规则：

- 每次 recompute 都可以执行
- 只有当 canonical assessment 输出发生变化时，才写入新的 immutable snapshot

canonical assessment 输出至少包括：

- `status`
- `confidence_grade`
- `confidence_rationale`
- `supporting_finding_ids`
- `opposing_finding_ids`
- `blocking_gap_ids`
- `non_blocking_gap_ids`
- `applied_inference_record_ids`
- subtype payload 中影响 judgment semantics 的字段

若上述输出完全不变，则：

- 不产生新的 `snapshot_seq`
- 继续复用上一条 snapshot 作为 `latest_assessment`

#### 创建

一旦生成新的 assessment snapshot：

- 分配新的 `snapshot_seq`
- 分配新的 `assessment_id`
- 通过 `supersedes_assessment_id` 指向上一条 snapshot 或 `null`

assessment snapshot 一旦写入即不可原地修改。

#### 最新态选择

`latest_assessment` 是读取层为每个 proposition 选择的当前快照。

要求：

- 同一 proposition 任意时刻最多只有一个 latest assessment
- state/context surface 只围绕 latest assessment 组织 live support/oppose/gap/inference membership
- superseded snapshots 不得把其成员继续混入 latest state 读取

#### 降级与升级

assessment 允许因新证据、缺失证据或规则重算而：

- 从 `insufficient` 升级为 `supported` / `contradicted` / `mixed`
- 从较强结论降级为 `insufficient`
- 在 `supported`、`contradicted`、`mixed` 之间迁移

runtime 不要求状态单调增强；真正单调的是 snapshot 序号，不是结论强度。

#### 失效

assessment 采用 soft invalidation。

因此当上游 finding/artifact 当前不可解引用时：

- 历史 snapshot 保留，供审计与回溯
- 受影响 proposition 应触发新的 recompute
- recompute 后 latest assessment 可因 membership 收缩、gap 打开或 confidence 降级而产生新 snapshot

系统不通过硬删旧 assessment 来表达当前态失效。

### EvidenceGap Lifecycle

#### 打开

`EvidenceGap` 由 inference process 在 assessment recompute 中打开。

打开条件包括但不限于：

- 缺少 required finding family
- 缺少 subject coverage 或 time coverage
- rule precondition 未满足
- data quality / comparability 风险阻塞当前判断

#### 身份与状态

gap 是判断层独立 canonical support object。

运行时要求：

- gap identity 绑定 proposition 与 requirement semantics
- gap 可跨多个 assessment snapshots 持续存在
- `status = open | resolved` 由后续 inference record 显式推进

#### 解决

gap 被解决时：

- 原 gap object 不必删除
- 解决事件通过新的 inference record 与后续 assessment snapshot 体现
- latest assessment 不再把已解决 gap 纳入当前 blocking/non-blocking membership

### InferenceRecord Lifecycle

#### 创建

`InferenceRecord` 表示一次显式规则过程。

目标态下，assessment recompute 应把当前命中的、未命中的或部分命中的规则过程结构化写成 inference record。

哪些规则必须写 record、`hit / miss / partial` 的判定，以及 gap open/resolve 如何由规则过程驱动，统一由 [`inference-rule-engine-contract.md`](inference-rule-engine-contract.md) 定义。

#### 绑定关系

InferenceRecord 运行时必须满足：

- 绑定单个 proposition
- 绑定生成它的 assessment snapshot
- 引用直接输入 findings 与输入 assessments
- 显式记录 opened / resolved gaps 与 status transition

#### 幂等

若某次 recompute 未产生新 assessment snapshot，则不应仅为了记录“又跑了一次同样规则”而额外生成一批无语义变化的新 inference records。

换言之：

- inference record 是 canonical judgment process 的一部分
- 它服务于解释 latest snapshot 为什么成立
- 它不是独立的 execution telemetry log

#### 最新态读取

context surface 中返回的 `applied_inference_records` 只允许来自 latest assessment。

历史 inference records 保留，但不应混入当前解释闭包。

### ActionProposal Lifecycle

#### 触发

`action proposal` 的刷新必须发生在 latest assessment 确定之后。

proposal refresh 的输入至少包括：

- `primary_assessment_ref`
- target proposition
- 当前 gaps
- proposal policy context

#### 创建与幂等

proposal 是 immutable projection snapshot。

要求：

- 同一 assessment snapshot、相同 action semantics、相同 policy context 重读时，proposal identity 稳定
- 若 latest assessment 未变且 policy context 未变，则不应生成新的 proposal identity
- assessment snapshot 一旦变化，即使 action kind 相同，也必须生成新的 proposal snapshot

#### 退出当前态

proposal 是否仍属于“当前有效 proposal 集合”，由读取层根据 latest assessment 与 policy context 判定。

当 assessment 被 supersede 时：

- 历史 proposal 保留
- 新 latest assessment 驱动新的 proposal refresh
- 旧 proposal 不再作为当前 shortcut 被主读取面消费

## Runtime Operations

### 增量更新

增量更新是默认运行模式。

每次上游变化后，系统应只重算受影响的 proposition closure，而不是无条件重建整 session。

受影响范围至少包括：

- 新增 findings 可能 seed 的 propositions
- 直接引用该 finding 的 propositions
- assessment family 规则依赖该 finding / gap / prior assessment 的 propositions

本文不固定具体 dependency index 的实现方式，但要求结果与全量 replay 一致。

### Replay

replay 是从已提交 canonical inputs 重建下游 state 的过程。

推荐 replay 边界：

1. 选择 artifact lineage 或 proposition closure 作为 replay root
2. 重建 findings
3. 重跑 proposition registration 去重
4. 重算受影响 assessments
5. 刷新 proposals 与读取面

replay 目标不是“复制旧 response”，而是“在当前 canonical rules 下重建一致状态”。

#### Replay guarantees

- replay 不改写 artifact identity
- replay 同一 artifact 时 finding identity 稳定
- replay 同一 judgment semantics 时 proposition identity 稳定
- replay 若 latest assessment canonical 输出不变，不产生新 snapshot
- replay 若 latest assessment canonical 输出变化，则产生新的 superseding snapshot

### Invalidation

目标态采用 soft invalidation。

当 runtime 检测到上游对象不可解引用、被清理或与当前闭包不一致时，应：

1. 保留历史 canonical objects
2. 标记读取缺失，而不是静默跳过
3. 触发受影响 proposition 的 recompute
4. 由新的 latest assessment 显式吸收 invalidation 影响

允许的 canonical 后果包括：

- `seed_entries` 中 `finding = null` 的成员增加
- `supporting_finding_ids` / `opposing_finding_ids` 缩减
- 新 `EvidenceGap` 打开
- `confidence_grade` 降级
- `status` 从单向结论回退到 `insufficient` 或 `mixed`

不允许的行为包括：

- 因上游缺失而静默隐藏 proposition
- 直接把历史 assessment 原地重写为空壳
- 用 projection warning 替代 canonical gap / missing ref 暴露

## Read Surface Binding Rules

虽然本文不定义 state/context surface 的完整 contract，但 runtime lifecycle 必须保证其读取基础稳定。

### State Surface

`SessionStateView` 的运行时绑定规则：

- `active_propositions` 读取 proposition registry 中当前属于 judgment track 的 objects
- `latest_assessment` 只读取每个 proposition 的最新 snapshot
- assessment-derived finding/gap/inference refs 只来自 latest assessment
- `backing_findings` 是 current live evidence closure 的去重并集，不扫描历史 snapshots 拼接
- 若 `active_propositions` 被顶层截断，`backing_findings`、`blocking_gaps`、`artifact_refs` 与 `focus_subjects` 都只覆盖 returned propositions 的自洽 closure
- 顶层默认排序不得把 unassessed live propositions 系统性排除出 returned state view

### Context Surface

`PropositionContextView` 的运行时绑定规则：

- `seed_entries` 按 `seed_finding_refs` 顺序 hydration，并保留 seed role
- `relevant_findings` 只覆盖 latest assessment 及其 inference records 的直接输入
- `blocking_gaps`、`non_blocking_gaps`、`applied_inference_records` 只来自 latest assessment
- `assessment_dependencies` 只覆盖 `applied_inference_records.input_assessment_ids` 的直接 assessment 输入

### Proposal Shortcut

action proposal 仍属于 shortcut：

- 它必须晚于 latest assessment 刷新
- 不得成为 proposition/context 主语义的唯一入口
- 读取面可以选择不返回 proposal，而不影响 canonical state 的完整性

## Acceptance Scenarios

下列场景构成本文的目标态验收基线：

1. 同一 `artifact_id` replay 两次，finding identity 与 finding 集合保持稳定。
2. 两个语义相同但不同 `artifact_id` 的 artifacts，不共享 finding identity。
3. finding 抽取完成后，匹配的 seed template 立即注册 proposition；同一 judgment semantics 不重复注册。
4. proposition 已存在时，新 finding 只影响后续 assessment，不改写 proposition identity。
5. assessment recompute 输出完全相同，不产生新的 snapshot。
6. assessment recompute 仅 gap 集合变化，会产生新的 snapshot。
7. assessment recompute 仅 confidence grade 或 rationale 变化，会产生新的 snapshot。
8. latest assessment 更新后，context surface 只反映新的 live support / oppose / gap / inference membership。
9. seed finding 当前不可解引用时，proposition 仍可读取，且对应 `seed_entries.finding = null`。
10. 上游对象失效后，历史 assessment 仍可审计，但 latest assessment 可因 gaps 或 membership 变化而降级。
11. action proposal 只由 latest assessment 刷新，不从历史 assessment 混合拼装。
12. `agent_authored` proposition 与 `system_seeded` proposition 进入同一 assessment lifecycle。

## 与其他文档的关系

- [`evidence-engine-design.md`](evidence-engine-design.md) 负责定义 canonical abstraction 与分层边界
- [`finding.md`](finding.md) 负责定义事实层对象边界
- [`proposition.md`](proposition.md) 负责定义命题对象、seed refs 与 assessment anchor
- [`assessment.md`](assessment.md) 负责定义 snapshot、gap 与 inference record 的 canonical schema
- [`action-proposal.md`](action-proposal.md) 负责定义 proposal 的 canonical projection schema
- [`state-surface-schema.md`](state-surface-schema.md) 与 [`context-surface-schema.md`](context-surface-schema.md) 负责定义读取面如何消费 latest/live lifecycle 结果

若后续需要定义 HTTP path、分页、state version pointer 或事务边界，应在专门的 API / persistence 文档中继续细化，而不是回写本文的 canonical lifecycle 边界。
