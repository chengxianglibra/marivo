# Evidence Engine Python DSL Proof Engine

本文档定义 Evidence Engine judgment/proof 层的 Python DSL 增强设计。

状态：draft design。本文是 `assessment` proof / judgment 层的 vNext 架构提案，用于增强现有 [`inference-and-gap-engine.md`](inference-and-gap-engine.md) 主线，而不是立即替代当前 v1 contract、对象 schema 或对外 HTTP surface。

## 目的

本文统一回答以下问题：

- 现有 Evidence Engine judgment layer 的优势和缺口分别是什么
- 为什么需要把 proposition proof 从隐式 rule flow 提升为显式 proof model
- Python DSL 方案要引入哪些新抽象，分别承担什么责任
- 新 proof layer 如何嵌入当前 `artifact -> finding -> proposition -> assessment -> action proposal` 主线
- 现有 assessment families 应如何逐步迁移到 Python DSL 方案
- 在不改写外部 API 的前提下，如何渐进替换现有 proof 逻辑

## 背景

Evidence Engine 当前 canonical chain 固定为：

`artifact -> finding -> proposition -> assessment -> action proposal`

围绕这条链路，现有设计已经建立了以下重要基线：

- `artifact -> finding` 的 deterministic extraction
- `finding -> proposition` 的 deterministic seeding / registration
- `assessment` 作为 immutable snapshot
- `EvidenceGap` 与 `InferenceRecord` 作为 judgment support objects
- state/context surfaces 只暴露 externally visible canonical state

这些基线是正确的，也是本提案明确保留的基础。

但在当前 v1 设计里，proof / judgment 的核心语义主要仍然分散在：

- `precondition_gate`
- `quality_gate`
- `comparability_gate`
- `support_evidence`
- `oppose_evidence`
- `status_resolution`

以及 family-specific rule code 中。

这导致系统可以稳定地产生 `supported` / `contradicted` / `mixed` / `insufficient`，但较难把以下问题显式化：

- 一个 proposition 到底需要满足哪些证明义务（proof obligations）
- 当前哪一条 finding 支撑了哪一个判断条件
- 当前“还差什么才能被证明”是 requirement-level truth，还是只是 rule miss 的副产物
- `blocking gap`、普通 caveat、方向性反证和未知状态之间的边界，是否已经作为一等对象存在

## 现有设计的优势

本提案不否定现有 Evidence Engine judgment layer。相反，Python DSL 方案成立的前提，正是当前设计已经做对了以下事情：

### 1. proposition / assessment 分层是正确的

- `proposition` 回答“要判断什么”
- `assessment` 回答“当前判断到什么程度”

这种分层使 proof layer 可以被独立增强，而不需要回退到 claim text 或 narrative reasoning。

### 2. canonical evidence chain 是稳定的

当前 Evidence Engine 已经把 canonical truth 固定为 typed objects，而不是把 SQL 结果或解释文本当作 judgment 输入。这使 proof layer 有明确、受限、可重放的 authority boundary。

### 3. immutable snapshot 是正确的 judgment persistence model

assessment 采用 immutable snapshot，而不是原地可变 blob。这使 proof layer 可以升级为更显式的结构，而不破坏 replay、audit 与 latest selection 语义。

### 4. gap / inference record 已经是独立 support objects

`EvidenceGap` 与 `InferenceRecord` 的存在，意味着系统已经接受“判断过程不应只坍缩为 status”。Python DSL 方案的工作，不是发明新的维度，而是把这些 support objects 的来源变得更显式。

### 5. runtime truth 与 canonical truth 已经分离

state/context surfaces 只暴露 externally visible canonical state，不暴露 queue / claim / retry / attempt truth。这使 proof layer 可以专注 canonical judgment，而不必兼顾 runtime orchestration。

## 当前设计的缺口

Python DSL 方案要解决的，不是“系统不会做判断”，而是“系统缺少显式 proof model”。

### 1. proof standard 不是一等对象

当前 `assessment_type` 确实隐含了 judgment policy，但系统没有一等对象来回答：

- 该 family 需要哪些 required proof obligations
- 哪些 obligations 是 strengthening，而哪些只是 caveat
- 哪些 requirement failure 应 materialize 为 gap

这些语义目前更多存在于 rule family 代码和文档分层中，而不是一个统一的、可实例化的 proof contract 中。

### 2. support / oppose membership 仍然过于粗粒度

当前 assessment 主要暴露：

- `supporting_finding_ids`
- `opposing_finding_ids`

这足以表达方向性 membership，但不足以表达：

- 某个 finding 究竟在 support 哪个 obligation
- 某个 finding 是反驳 proposition 本身，还是只 block 某个证明前提
- 某个 finding 是 blocker、downgrade factor，还是 substantive contradiction

### 3. `insufficient` 仍然主要是结果，而不是显式证明状态

当前系统能够稳定产出 `insufficient`，但缺少一个中间层去显式建模：

- 哪个 required obligation 未满足
- 是证据缺失、前提不成立、质量不足，还是 comparability 失败
- 这些未满足项如何映射到 gap identity

### 4. judgment trace 仍然偏流程导向

当前 `InferenceRecord` 能记录 rule hits/misses，但对 operator 和 agent 最有价值的问题是：

- proof case 中有哪些 obligations
- 每个 obligation 当前是什么状态
- 哪些 findings 导致 obligation 被满足、反驳、阻断或降级

这类 obligation-level trace 目前并不是主模型。

## 设计目标

Python DSL proof engine 的目标如下：

- 把 proposition proof 提升为显式的 proof case / obligation / evaluation 模型
- 保持 assessment 作为 externally visible canonical judgment snapshot
- 保持 deterministic、replay-safe、snapshot-safe
- 保持 Python-hosted，不引入外部逻辑运行时
- 保持与现有 `AssessmentEvaluationContext` 的 authority boundary 一致
- 让 gap / confidence / transition materialization 仍然发生在 canonical pipeline 内
- 让 family-specific proof rules 可以声明式表达，而不是继续散在 ad hoc 函数中

## 非目标

本提案明确不做以下事情：

- 不替换 `artifact`、`finding`、`proposition` 的对象分层
- 不修改对外 HTTP action/state/context wire contract
- 不要求在本提案中重写 `ActionProposal` policy
- 不要求一次性替换全部 assessment families 的实现
- 不引入 Prolog、外部 theorem prover 或独立 logic runtime
- 不把 proof layer 变成自由文本 explanation engine

## 设计概览

本提案在现有 canonical chain 上引入一层显式 proof model：

`artifact -> finding -> proposition -> proof_case -> assessment -> action proposal`

其中：

- `artifact -> finding -> proposition` 保持不变
- `proof_case` 是 proposition-local、family-specific 的显式证明结构
- `assessment` 仍然是 externally visible 的 canonical judgment snapshot

这里新增的不是新的外部读取对象，而是 assessment recompute 的内部结构化中间层。

## 核心抽象

### `ProofCase`

`ProofCase` 是单个 proposition 在当前 family template 下的 proof 实例。

它回答：

- 这个 proposition 应按哪一组 proof obligations 被评估
- 当前使用的 proof family / template version 是什么
- 当前 proof case 的 subject / proposition anchor 是什么

它不回答：

- 当前最终 status 是什么
- 哪些 evidence 已经成立
- 该 proof case 是否已经 externally visible

建议最小形状：

```ts
type ProofCase = {
  proof_case_id: string;
  proposition_id: string;
  assessment_type: string;
  proof_family: string;
  proof_template_version: string;
  obligation_ids: string[];
};
```

### `ProofObligation`

`ProofObligation` 是最小的显式证明义务单元。

它回答：

- 当前 proposition 要想形成更强结论，必须满足哪条离散条件
- 该条件属于 required / strengthening / caveat / blocker 哪一类

建议最小语义：

- `required`：未满足时无法形成目标方向的强结论
- `strengthening`：未满足时仍可形成保守结论，但不应升级
- `blocker`：未满足时应 materialize 为 blocking gap 或阻断方向结论
- `caveat`：只影响 confidence 或 rationale，不直接阻断 status

建议状态集合：

- `satisfied`
- `refuted`
- `unresolved`
- `blocked`

### `EvidenceEvaluation`

`EvidenceEvaluation` 是 finding 与 obligation 之间的显式关系评估。

它回答：

- 哪个 finding 作用于哪个 obligation
- 作用类型是：
  - `support`
  - `oppose`
  - `block`
  - `downgrade`
  - `context_only`
- 该 finding 的作用强度或适用性是什么

它不是 canonical finding 本体，也不替代 `InferenceRecord`；它是 proof 层的结构化中间结果。

### `JudgmentAggregation`

`JudgmentAggregation` 负责把 obligation-level truth 聚合为 proposition-level judgment。

它回答：

- 哪些 required obligations 已满足
- 哪些 contradictions 是 substantive
- 哪些 blockers 阻断了单向强结论
- 最终应形成 `supported` / `contradicted` / `mixed` / `insufficient` 中的哪一种

### `Assessment`

`Assessment` 在新设计中仍保持现有角色：

- externally visible canonical judgment snapshot
- live support / oppose / gaps / inference record memberships 的 stable owner

Python DSL 方案不把 `Assessment` 替换为 proof graph；它只改变 assessment 是如何被计算出来的。

## 为什么选择 Python DSL

本提案默认采用 Python-hosted declarative DSL，而不是外部 logic engine。

原因如下：

### 1. 与现有 execution boundary 一致

当前 Evidence Engine 的 authority boundary、repositories、snapshot lifecycle、context assembly 都已经在 Python 中实现。proof layer 保持 Python-hosted，可以最小化对象编译/反编译成本。

### 2. 更容易显式表达 unknown / blocked

Factum 最关键的 judgment status 之一是 `insufficient`。Python DSL 方案可以把：

- `unresolved`
- `blocked`
- `refuted`

显式建模为 obligation states，而不是让未知状态退化成求值失败。

### 3. 更适合 deterministic product-grade orchestration

Evidence Engine 不是 theorem prover；它是一个 canonical judgment system。相比外部逻辑运行时，Python DSL 更容易与：

- repositories
- schema typing
- snapshot commit
- read surfaces
- migration / invalidation

保持单一工程边界。

### 4. trace 更容易产品化

Python DSL 可以把 obligation evaluation trace 直接输出为结构化对象，并稳定映射到 future `InferenceRecord` metadata、gaps、confidence rationale 和 context surface explanation。

## Python DSL 设计

### DSL 定位

Python DSL 不是通用规则引擎，也不是自由逻辑语言。它是一个 family-scoped proof declaration layer，用于：

- 声明 proof template
- 声明 obligations
- 声明 finding-to-obligation evaluators
- 声明 judgment aggregation

它不负责：

- 查询数据库
- 读取未提交对象
- 改写 canonical objects

### 基本组成

Python DSL 由四类接口组成：

1. proof family registration
2. proof case instantiation
3. obligation evaluation
4. judgment aggregation

建议最小接口形状：

```python
class ProofFamilyTemplate(Protocol):
    assessment_type: str
    proof_family: str
    template_version: str

    def build_case(self, ctx: AssessmentEvaluationContext) -> ProofCase: ...
    def define_obligations(self, case: ProofCase) -> list[ProofObligation]: ...
    def evaluate(
        self,
        case: ProofCase,
        obligations: list[ProofObligation],
        findings: list[Finding],
    ) -> list[EvidenceEvaluation]: ...
    def aggregate(
        self,
        case: ProofCase,
        obligations: list[ProofObligation],
        evaluations: list[EvidenceEvaluation],
    ) -> JudgmentAggregationResult: ...
```

### registration

proof family registration 以 `assessment_type` 为主锚点：

```python
register_proof_family(template: ProofFamilyTemplate) -> None
```

固定要求：

- 同一 `assessment_type` 同一 `template_version` 只允许一个 active template
- registration metadata 必须可用于 future `InferenceRecord` / registry 解引用

### case instantiation

`build_case(...)` 只允许消费 `AssessmentEvaluationContext` 和 family-owned static template metadata。

固定要求：

- 不得读取其他 proposition 的 assessment / gap
- 不得绕过 context boundary 再做自由发现
- 相同 `AssessmentEvaluationContext + template_version` 必须稳定生成同一 proof case 语义

### obligation definition

obligation definition 必须是显式的。

每个 `ProofObligation` 至少需要声明：

- `obligation_id`
- `obligation_kind`
- `severity_kind`
- `directional_role`
- `failure_gap_semantics`

其中：

- `directional_role` 用于区分其对 support / oppose / neutral track 的作用
- `failure_gap_semantics` 用于 future gap identity materialization

### evaluation

evaluation 阶段负责把 findings 作用到 obligations 上。

固定要求：

- 输入 finding 集只来自 `AssessmentEvaluationContext.candidate_finding_ids`
- evaluation 必须保持 deterministic
- 同一个 finding 可以作用于多个 obligations，但每一条作用都必须显式 materialize 为 evaluation record
- evaluation 不得直接写 final status

### aggregation

aggregation 阶段负责：

- 计算 obligation status
- 判定 substantive contradictions
- 汇总 blockers / downgrades
- 生成 proposition-level judgment candidate

aggregation 输出建议最小包括：

- `status`
- `supporting_finding_ids`
- `opposing_finding_ids`
- `unsatisfied_obligations`
- `blocking_obligations`
- `confidence_inputs`
- `trace_notes`

## 与现有 runtime pipeline 的关系

Python DSL 方案不改变当前 runtime 主线：

1. typed intent 执行并形成 artifact
2. artifact extraction 形成 committed findings
3. findings seeding / registration propositions
4. proposition 触发 assessment recompute
5. assessment snapshot commit
6. proposal refresh
7. externally visible state/context reads

变化只发生在步骤 4 内部。

## 新的 assessment recompute 结构

当前 `assessment_recompute` 的逻辑在目标态上应拆成：

1. evaluation context assembly
2. proof case instantiation
3. obligation definition
4. evidence evaluation
5. obligation status derivation
6. judgment aggregation
7. gap materialization
8. confidence shaping
9. transition finalization
10. snapshot commit / no-op discard

对应关系如下：

- 现有 `AssessmentEvaluationContext` 继续保留
- 现有 `gap_management`、`confidence_shaping`、`assessment_transition` 主题责任保留
- `precondition_gate` / `quality_gate` / `comparability_gate` / `support_evidence` / `oppose_evidence` / `status_resolution` 的现有职责，逐步下沉为 obligation-level evaluators 与 aggregation policy

## 与 `AssessmentEvaluationContext` 的关系

`AssessmentEvaluationContext` 仍然是 proof engine 的唯一 authority input boundary。

本提案明确保留以下原则：

- canonical inputs only
- single proposition scope
- committed finding layer only
- no UI projection
- no uncommitted objects
- no cross-proposition hidden reads

Python DSL 不改变 context assembly contract，只改变 context 被消费的方式。

## 与 `InferenceRecord` 的关系

在目标态中，`InferenceRecord` 不再只是“某个 rule family hit/miss 的记录”。

它更适合承担以下角色：

- 记录 proof template / evaluator version
- 记录 obligation-level evaluation result
- 记录 aggregation decision 使用了哪些 obligation states

建议演进方向：

- 保留 `rule_id -> rule_family -> assessment_type` 的现有 registry 解释能力
- 允许 future `InferenceRecord` 新增 proof-oriented metadata：
  - `proof_family`
  - `proof_template_version`
  - `obligation_id`
  - `evaluation_role`
  - `obligation_status_after_evaluation`

本提案不要求马上修改 canonical `InferenceRecord` schema，但要把这个方向固定下来。

## 与 `EvidenceGap` 的关系

目标态中 gap 不应再主要由分散的 family miss 直接产生。

更合理的收敛路径是：

- obligation failure semantics 先被显式建模
- gap identity 从 unsatisfied / blocked obligations 派生
- `gap_management` 仍然是 canonical materialization owner

这意味着：

- gap 的 authority owner 不变
- gap 的输入语义会变得更显式、可解释
- 同一 family 的多个 rule miss 不再需要在 materialization 末端重新猜测它们指向的是哪个 proof requirement

## 与 read surfaces 的关系

本提案不引入新的 externally visible read surface。

State/context surfaces 仍然保持：

- proposition-centered read model
- latest externally visible assessment bundle
- no runtime staging visibility

但本提案预期未来可以让 context surface 更清晰地解释：

- 哪些 obligations 已满足
- 当前还缺哪些 proof requirements
- 某个 gap 对应的是哪条 proof requirement

这些增强应通过现有 context semantics 的扩展完成，而不是新增平行 surface。

## family mapping

Python DSL 方案需要覆盖当前已有的 6 个 assessment families：

- `change_assessment`
- `decomposition_assessment`
- `anomaly_assessment`
- `correlation_assessment`
- `test_hypothesis_assessment`
- `forecast_assessment`

以下只定义最小 proof obligations 草图。

### `change_assessment`

最小 obligations：

- `change_direction_supported`
- `change_magnitude_sufficient`
- `comparison_context_usable`

典型 evidence roles：

- `delta` finding:
  - support 方向和量级 obligation
  - 在反向变化时 oppose proposition
- comparability / quality findings 或 metadata:
  - block `comparison_context_usable`
  - downgrade confidence

聚合原则：

- 方向和量级 obligation 均满足，且 comparability 未阻断时，可形成 `supported`
- 方向被稳定反驳时，可形成 `contradicted`
- 存在 substantive 双向冲突时，可形成 `mixed`
- 其余为 `insufficient`

### `decomposition_assessment`

最小 obligations：

- `driver_identity_supported`
- `contribution_share_sufficient`
- `residual_acceptable`

### `anomaly_assessment`

最小 obligations：

- `baseline_deviation_substantive`
- `quality_noise_not_dominant`
- `seasonal_or_routine_explanation_not_sufficient`

### `correlation_assessment`

最小 obligations：

- `alignment_usable`
- `association_strength_sufficient`
- `association_direction_supported`

### `test_hypothesis_assessment`

最小 obligations：

- `sample_summary_usable`
- `method_preconditions_satisfied`
- `significance_threshold_met`
- `effect_size_semantically_supportive`

这是最适合试点 Python DSL 的 family，因为：

- proposition semantics 收敛
- `test_result` finding 边界稳定
- required obligations 相对清晰

### `forecast_assessment`

最小 obligations：

- `forecast_basis_usable`
- `prediction_direction_supported`
- `uncertainty_within_acceptable_range`

## Worked Example 1: `test_hypothesis_assessment`

### proposition

假设 proposition 表达：

- 命题：实验组与对照组存在实质差异，且当前检验结果支持该差异
- proposition type：`test_hypothesis`

### proof case

proof case 选择：

- `assessment_type = "test_hypothesis_assessment"`
- `proof_family = "hypothesis_difference_proof"`
- `template_version = "v1"`

### obligations

最小 obligations：

- `sample_summary_usable`
- `method_preconditions_satisfied`
- `significance_threshold_met`
- `effect_size_semantically_supportive`

### evidence evaluations

`test_result` finding 可以产生如下 evaluations：

- 对 `sample_summary_usable`：
  - 若样本摘要完整，则 `support`
  - 若摘要缺失或质量不足，则 `block`
- 对 `method_preconditions_satisfied`：
  - 若 method / assumptions 合法，则 `support`
  - 若 comparability 或 method 前提不满足，则 `block`
- 对 `significance_threshold_met`：
  - `p_value <= alpha` 时 `support`
  - `p_value > alpha` 时不一定是 `oppose`，通常是 `unresolved` 或 `downgrade`
- 对 `effect_size_semantically_supportive`：
  - effect size 支持 proposition direction 时 `support`
  - effect size 明显反向时 `oppose`

### aggregation

建议最小聚合逻辑：

- 若 `sample_summary_usable` 或 `method_preconditions_satisfied` 为 `blocked`，默认不能形成强单向结论
- 若显著性与效应量都支持 proposition，可形成 `supported`
- 若效应量明确反向且统计上支持反方向，可形成 `contradicted`
- 若不同 findings / slices / repeated tests 给出稳定冲突，可形成 `mixed`
- 其余为 `insufficient`

### assessment mapping

映射回现有 assessment：

- `status` 由 aggregation 决定
- `supporting_finding_ids` 来自 substantive support evaluations
- `opposing_finding_ids` 来自 substantive oppose evaluations
- 未满足的 required obligations 派生 `EvidenceGap`
- evaluation / aggregation trace 写入 future proof-oriented `InferenceRecord`

## Worked Example 2: `change_assessment`

### proposition

假设 proposition 表达：

- 命题：`watch_time` 在指定时间窗口内发生了下降
- proposition type：`change`

### proof case

- `assessment_type = "change_assessment"`
- `proof_family = "delta_change_proof"`

### obligations

- `change_direction_supported`
- `change_magnitude_sufficient`
- `comparison_context_usable`

### evidence evaluations

`delta` finding 可以同时作用于多个 obligations：

- 若 delta < 0，则 support `change_direction_supported`
- 若绝对变化量超过 family threshold，则 support `change_magnitude_sufficient`
- 若 comparability metadata 指出对齐不稳，则 block `comparison_context_usable`

### aggregation

- 方向和量级 obligations 都满足，且 comparison context 未阻断时，可形成 `supported`
- 若 delta 明显为反向，可形成 `contradicted`
- 若存在跨切片稳定冲突或 prior carried-forward findings 给出实质反向 evidence，可形成 `mixed`
- 若变化存在但量级或 comparability 不足，则形成 `insufficient`

### assessment mapping

- current `change_assessment` 仍然是 externally visible object
- proof case 内部细节不直接进入 read surface
- gaps / confidence 继续通过现有 canonical owners materialize

## 渐进迁移策略

本提案采用渐进替换，而不是整层重写。

### Phase 1. 引入 proof model 术语与边界

先在设计与实现层引入：

- `ProofCase`
- `ProofObligation`
- `EvidenceEvaluation`
- `JudgmentAggregationResult`

这一阶段不要求立刻修改 externally visible schema。

### Phase 2. 在单一 family 上试点

首个试点 family 建议固定为：

- `test_hypothesis_assessment`

原因：

- proposition semantics 明确
- `test_result` finding 边界稳定
- proof obligations 最容易离散化

### Phase 3. 将 directional / gate 逻辑逐步下沉为 DSL evaluators

逐步把当前 family-specific ad hoc logic 收敛为：

- obligation definitions
- evidence evaluators
- family aggregators

此阶段应优先保持 externally visible `Assessment.status`、`confidence`、`gaps` 语义不变。

### Phase 4. 统一 gap / confidence / registry 对齐

待 proof model 在至少两个 families 上稳定后，再统一：

- gap identity derivation from obligations
- confidence shaping inputs from obligation coverage
- registry / inference metadata 对 proof template 的解引用

## 兼容性约束

本提案固定以下兼容边界：

- 对外 HTTP API 不变
- `Assessment.status` lattice 不变
- `latest_assessment = null` 的读取语义不变
- state/context surfaces 不暴露 proof internal staging
- `artifact -> finding -> proposition` pipeline 不变
- `ActionProposal` 仍然消费 latest assessment，而不是直接消费 proof case

## 与现有文档的关系

当前仍然以以下文档作为现行 contract 权威：

- [`inference-and-gap-engine.md`](inference-and-gap-engine.md)
- [`assessment-evaluation-context.md`](assessment-evaluation-context.md)
- [`support-oppose-and-status-resolution.md`](support-oppose-and-status-resolution.md)
- [`gap-confidence-and-transition-materialization.md`](gap-confidence-and-transition-materialization.md)
- [`schemas/assessment.md`](schemas/assessment.md)

本文的角色是：

- 给出 proof layer 的目标态增强设计
- 解释为什么要从 implicit rule flow 迁移到 explicit proof model
- 为后续 family-by-family implementation 提供统一方向

在实际代码和 schema 完成迁移前，本文不得被误解为 v1 的字段级权威来源。

## Non-goals

本文不定义以下内容：

- 未来 `ProofCase` 是否成为 externally visible canonical object
- 新增独立 proof read surface
- 完整的 `InferenceRecord` v2 schema
- 完整的 `EvidenceGap` v2 schema
- 每个 assessment family 的全部 rule threshold 数值
- `ActionProposal` 如何消费 obligation-level trace

这些都留待后续专项设计文档补充。

## Related Documents

- [`overview.md`](overview.md)
- [`runtime-pipeline.md`](runtime-pipeline.md)
- [`inference-and-gap-engine.md`](inference-and-gap-engine.md)
- [`assessment-evaluation-context.md`](assessment-evaluation-context.md)
- [`support-oppose-and-status-resolution.md`](support-oppose-and-status-resolution.md)
- [`gap-confidence-and-transition-materialization.md`](gap-confidence-and-transition-materialization.md)
- [`read-surfaces.md`](read-surfaces.md)
- [`schemas/proposition.md`](schemas/proposition.md)
- [`schemas/assessment.md`](schemas/assessment.md)
