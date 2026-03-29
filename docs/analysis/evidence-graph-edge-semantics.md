# Evidence Graph Edge Semantics

本文档定义 Factum 在 v1 规范证据模型（canonical evidence model）下的图边语义（graph edge semantics）。

状态：draft design。本文是 `docs/analysis/` 下的规范关系（canonical relation）设计提案，不表示对应持久化表、图存储（graph storage）、HTTP endpoint 或通用图查询（graph query）已经实现。

## 目的

事实单元（`finding.md`）、命题（`proposition.md`）、评估状态（`assessment.md`）、动作候选（`action-proposal.md`）已分别定义规范对象（canonical objects）的字段与分层边界；分析状态面（`state-surface-schema.md`）与上下文面（`context-surface-schema.md`）已定义读取面如何解引用这些对象。

本文补足的是对象之间”哪些关系进入规范模型（canonical model）、这些关系如何解释”的统一基线，回答以下问题：

- 哪些对象间关系属于规范边（canonical edge），而不是实现层或读取层的临时拼接
- 每类边（edge）的方向、source/target 约束、创建权威（authority）与运行时含义是什么
- 哪些边只表达谱系（lineage）/ 溯源信息（provenance），哪些边表达当前判断归属（judgment membership）
- 重放（replay）、软失效（soft invalidation）、最新态/活跃态选择（latest/live selection）出现时，边应如何解释

## 非目标

本文不定义：

- 图存储 schema（graph storage schema）、表结构、索引或事务细节
- 对外 HTTP graph contract、path、query、分页与兼容参数
- 通用图查询语言（graph query language）
- 跨命题推断（cross-proposition inference）、跨命题支持（cross-proposition support）/ 矛盾（contradiction）/ 依赖（dependency）
- 叙述摘要（narrative summary）、反思上下文（reflection-context）或 UI 文案中隐式拼装出的关系

若未来需要跨命题关系（cross-proposition relation），必须在单独设计中把它先引入规范模型（canonical model）；v1 不得通过推断引擎（inference engine）、状态/上下文面（state/context surface）或实现层临时约定隐式开放。

## Core Rules

### 1. canonical edge 只覆盖会影响语义边界的关系

只有会影响以下任一方面的对象间关系，才进入 canonical edge semantics：

- object identity 或 lineage
- runtime lifecycle / replay / invalidation
- assessment recompute 的直接输入
- state/context surface 的 canonical closure
- audit / provenance 的稳定解释

读取层为方便消费而构造的反向索引、聚合视图或 compact summary，不进入 canonical edge taxonomy。

### 2. v1 用现有 typed refs 承载 edge semantics

v1 不新增独立 `Edge` canonical object。

edge semantics 由现有 object schema 中的 typed refs、id fields 与 lifecycle 规则承载。实现可以内部维护 relation index，但不得让 index 成为高于 canonical object fields 的权威来源。

### 3. canonical direction 固定为写入方向

每类 edge 都有一个 canonical direction，用于表达“哪个对象声明该关系”。

读取层可以按需要提供反向遍历或反向索引，但：

- 不得改变 edge 的 canonical source/target 定义
- 不得把反向读取结果误解释为一个新的 edge family

### 4. 历史（history）与实时归属（live membership）必须区分

并非所有边都表达当前实时归属（live membership）。

v1 固定两类语义：

- 谱系（lineage）/ 溯源信息（provenance）边：表达来源、派生、直接输入；历史可保留，不因最新态（latest）切换而失真
- 运行时归属边（runtime membership edge）：表达某个评估快照（assessment snapshot）下的支持（support）/ 反驳（oppose）/ 证据缺口（gap）/ 推断（inference）归属；其实时性（live）由快照选择与读取层解释决定

### 5. 失效（invalidation）采用软解释（soft interpretation）

当边指向的上游对象当前不可解引用时：

- 历史规范边（canonical edge）语义不自动消失
- 不静默硬删边语义
- 通过缺失引用（missing refs）、证据缺口（gap）重开、归属收缩（membership 收缩）、最新态选择变化等方式暴露影响

## Canonical Node Set

本文覆盖的 node types 固定为：

- `artifact`
- `finding`
- `proposition`
- `assessment`
- `evidence_gap`
- `inference_record`
- `action_proposal`

其中：

- `artifact` 是工件层权威来源
- `finding`、`proposition`、`assessment` 构成核心 judgment chain
- `evidence_gap` 与 `inference_record` 是判断层支撑对象
- `action_proposal` 是外层 projection object

## Edge Taxonomy

### 1. `finding -> artifact`

- Edge type: `extracted_from`
- Source/target: `finding` 指向单个 `artifact`
- Canonical carrier: `Finding.artifact_id`
- Authority: deterministic finding extractor
- Semantics: 该 finding 的唯一权威来源 artifact
- Lifecycle: provenance edge；同一 finding 只能有一个，不允许多源合并

规则：

- replay 同一 `artifact_id` 时，不得把该 edge 改写到其他 artifact
- 若 artifact 当前不可解引用，finding 历史来源语义仍存在；读取层通过 provenance 缺失暴露当前不完整性

### 2. `proposition -> finding`

- Edge type: `seeded_by`
- Source/target: `proposition` 指向 `finding`
- Canonical carrier: `Proposition.seed_finding_refs`
- Authority: seed template 或 agent-authored proposition 注册逻辑
- Semantics: proposition 在 creation-time 的建模种子事实
- Lifecycle: lineage / provenance edge；不是 live support set

规则：

- `seeded_by` 不等价于 `supports`
- seed finding 可与后续 support finding 重叠，但语义仍分离
- seed 当前不可解引用时，proposition 仍可读取；由 `seed_entries.finding = null` 暴露缺失

### 3. `proposition -> proposition`

- Edge type: `derived_from`
- Source/target: `proposition` 指向同 session 的更早 proposition
- Canonical carrier: `Proposition.lineage.derived_from_proposition_ref`
- Authority: proposition derivation / refinement logic
- Semantics: proposition 谱系或 refinement 来源
- Lifecycle: lineage edge；不表示 support、contradiction 或 inference dependency

规则：

- v1 仅允许同一 session 内建立该 edge
- `derived_from` 不进入 inference engine 的 cross-proposition judgment 输入
- 该 edge 仅用于 lineage、audit 与 UI/context 中的派生来源解释

### 4. `assessment -> proposition`

- Edge type: `assesses`
- Source/target: `assessment` 指向单个 `proposition`
- Canonical carrier: `Assessment.proposition_id`
- Authority: inference engine / assessment recompute pipeline
- Semantics: 该 assessment snapshot 评估的唯一命题
- Lifecycle: judgment anchor edge；每个 assessment 必须且只能指向一个 proposition

规则：

- 不允许一个 assessment 同时评估多个 propositions
- 读取层的 `latest_assessment` 选择不改变该 edge 本身

### 5. `assessment -> assessment`

- Edge type: `supersedes`
- Source/target: 新 assessment 指向被其 supersede 的旧 assessment
- Canonical carrier: `Assessment.supersedes_assessment_id`
- Authority: assessment recompute pipeline
- Semantics: 同一 proposition 下的 snapshot lineage
- Lifecycle: lineage edge；不表达 workflow state machine 或 monotonic upgrade

规则：

- v1 中该 edge 只允许连接同一 proposition 的 assessment snapshots
- `supersedes` 不自动表示“更强”或“更正确”，只表示 latest lineage 的后继

### 6. `assessment -> finding`

此 family 分成两个定向 membership edges：

- `supports`
- `opposes`

通用定义：

- Source/target: `assessment` 指向 `finding`
- Canonical carrier: `Assessment.supporting_finding_ids`、`Assessment.opposing_finding_ids`
- Authority: inference engine
- Semantics: 该 assessment snapshot 下被纳入方向性判断 membership 的 live evidence
- Lifecycle: runtime membership edge；由 snapshot 绑定，而不是 proposition-level 常驻关系

规则：

- 同一 finding 在同一 assessment snapshot 下不得同时进入 `supports` 与 `opposes`
- `supports` / `opposes` 只表示当前 snapshot 采用的方向性 evidence membership
- 这些 edge 不得回写到 `proposition`
- state/context surface 中的 live support/oppose 只来源于 `latest_assessment`

### 7. `assessment -> evidence_gap`

此 family 分成两类：

- `blocks_on`
- `has_non_blocking_gap`

通用定义：

- Source/target: `assessment` 指向 `evidence_gap`
- Canonical carrier: `Assessment.blocking_gap_ids`、`Assessment.non_blocking_gap_ids`
- Authority: inference engine / gap management logic
- Semantics: 当前 assessment snapshot 的 gap membership
- Lifecycle: runtime membership edge；由 snapshot 绑定

规则：

- `blocks_on` 只对应 blocking gaps
- `has_non_blocking_gap` 只对应 non-blocking gaps
- gap 是否仍为 open，由 gap object 与 latest snapshot 共同解释；不得仅靠字符串文本推断

### 8. `assessment -> inference_record`

- Edge type: `applies_record`
- Source/target: `assessment` 指向 `inference_record`
- Canonical carrier: `Assessment.applied_inference_record_ids`
- Authority: inference engine
- Semantics: 该 assessment snapshot 采用的显式规则过程记录
- Lifecycle: runtime membership edge；由 snapshot 绑定

规则：

- 每个 `InferenceRecord` 必须属于一个明确 candidate/committed assessment snapshot
- state/context surface 只读取 latest assessment 对应的 records

### 9. `inference_record -> finding`

- Edge type: `reads_finding`
- Source/target: `inference_record` 指向 `finding`
- Canonical carrier: `InferenceRecord.input_finding_ids` 或等价直接输入字段
- Authority: inference engine
- Semantics: 该 rule record 直接读取或依赖的 finding 输入
- Lifecycle: audit / provenance edge；不是方向性 judgment membership

规则：

- `reads_finding` 不自动提升为 `assessment.supports` 或 `assessment.opposes`
- 某 finding 只作为 caveat、gate input 或 partial context 时，可以只出现在 `reads_finding`

### 10. `inference_record -> assessment`

- Edge type: `reads_assessment`
- Source/target: `inference_record` 指向 prior assessment
- Canonical carrier: `InferenceRecord.input_assessment_ids` 或等价直接输入字段
- Authority: inference engine
- Semantics: 该 rule record 直接读取的 prior assessment 输入
- Lifecycle: audit / provenance edge

规则：

- v1 只允许读取同一 proposition 的历史 assessments
- 不得借此引入 cross-proposition inference

### 11. `action_proposal -> assessment`

该 family 分成两类：

- `targets_primary_assessment`
- `relates_assessment`

通用定义：

- Source/target: `action_proposal` 指向 `assessment`
- Canonical carrier: `ActionProposal.primary_assessment_ref`、`ActionProposal.related_assessment_refs`
- Authority: proposal policy / proposal materialization logic
- Semantics: proposal 的主目标 assessment 与辅助 assessment 上下文
- Lifecycle: projection edge；proposal identity 的一部分

规则：

- 每个 proposal 必须且只能有一个 `targets_primary_assessment`
- `relates_assessment` 只补充上下文，不改变 primary target 的唯一性

### 12. `action_proposal -> proposition`

- Edge type: `targets_proposition`
- Source/target: `action_proposal` 指向 `proposition`
- Canonical carrier: `ActionProposal.target_proposition_ref`
- Authority: proposal policy / proposal materialization logic
- Semantics: proposal 服务的 judgment target proposition
- Lifecycle: projection edge

规则：

- 该 edge 必须与 `primary_assessment_ref.proposition_id` 一致
- 不允许 proposal 指向与 primary assessment 不一致的 proposition

### 13. `action_proposal -> evidence_gap`

- Edge type: `serves_gap`
- Source/target: `action_proposal` 指向 `evidence_gap`
- Canonical carrier: `ProposalRationale.served_gap_refs`、`payload.closes_gap_refs`
- Authority: proposal policy / proposal materialization logic
- Semantics: proposal 预期服务、缩小或闭合的 gap
- Lifecycle: projection edge

规则：

- 只有当 proposal payload 或 rationale 显式绑定 gap 时，才建立该 edge
- 未显式列出的 gap 不得由 consumer 自行推断为 served

## Creation Authority Matrix

v1 的 edge 创建 authority 固定如下：

- extractor 只能创建 `finding -> artifact`
- proposition registration 只能创建 `proposition -> finding`、`proposition -> proposition`
- inference engine 只能创建 assessment、gap、inference record 相关 edge
- proposal policy / materializer 只能创建 action proposal 相关 edge

任何层都不得越权创建其他层的 edge family。例如：

- extractor 不得直接创建 `supports`
- state/context surface 不得补写 `derived_from`
- proposal policy 不得回写 assessment support membership

## Read Surface Interpretation

`state-surface-schema.md` 与 `context-surface-schema.md` 必须按以下方式消费 edge semantics：

- `seed_entries` 只 hydration `seeded_by`，并保留 `PropositionSeedRef.role`
- `relevant_findings` 只覆盖 `supports`、`opposes` 与 `reads_finding`
- `latest_assessment`、live support/oppose、live gaps、applied inference records 都是读取层对 snapshot membership 的选择，不是对象本体状态位
- `assessment_dependencies` 只覆盖 `reads_assessment` 的直接 assessment 输入，不递归展开更早链路
- `artifact_refs` 是由 context/state 所涉及 edge 指向的 findings 再回溯 `extracted_from` 后得到的最小权威入口

读取层不得新增：

- proposition-level `supports`
- cross-proposition `depends_on`
- action proposal 到 finding 的默认 evidence edge

## Out of Scope for v1

以下关系不进入 v1 canonical edge taxonomy：

- proposition 与 proposition 之间的 support / contradiction / dependency
- assessment 直接指向 artifact 的 judgment edge
- action proposal 直接指向 finding 的 evidence-consumption edge
- reflection / readiness / compact summary 中拼装出的隐式关系
- 用于 HTTP graph API 的 generic `source_id + edge_type + target_id` contract

## Compatibility Notes

本文是 v1 canonical design 的关系语义基线。

后续若新增：

- cross-proposition relation
- 独立 edge object
- graph HTTP surface

必须先更新本文或以本文为基线新增后续设计，避免在 engine contract、read surface 或实现层中隐式扩张 relation 语义。
