# Inference Rule Engine Contract

本文档定义 Factum 证据引擎中 inference rule engine 的拟议 contract。

状态：draft design。本文是 `docs/analysis/` 下的 canonical inference 设计提案，不表示对应实现、持久化模型或 HTTP endpoint 已经存在。

## 目的

`assessment.md` 已定义 `Assessment`、`EvidenceGap`、`InferenceRecord` 的 canonical schema；`evidence-engine-runtime-lifecycle.md` 已定义 assessment recompute 的生命周期边界。

本文补足的是 inference rule engine 本身的 contract，回答以下问题：

- 规则引擎按什么 evaluation unit 运行
- rule family 如何组织、排序和协作
- 哪些情况会升级、降级或保持 assessment
- `hit / miss / partial` 如何稳定写入 `InferenceRecord`
- candidate assessment 与 inference records 如何形成无循环依赖的提交闭环
- state / context surface 如何稳定读取“命中了哪些规则族”

本文不重复定义 `Assessment`、`EvidenceGap`、`InferenceRecord` 的字段 shape；如有冲突，以 [`assessment.md`](assessment.md) 为准。

本文也不单独定义 canonical relation taxonomy；对象间允许的 edge family、方向与 authority 以 [`evidence-graph-edge-semantics.md`](evidence-graph-edge-semantics.md) 为准。

## 核心设计决策

### 1. inference 是显式规则过程，不是自由文本解释

inference engine 的输入固定为：

- target `proposition`
- 当前 proposition closure 中可解引用的 `findings`
- 同一 proposition 下更早的 `assessment snapshots`
- 同一 proposition 当前仍为 `open` 的 `EvidenceGap`

inference engine 的输出固定为：

- candidate `Assessment` snapshot payload
- 该 candidate snapshot 采用的 `InferenceRecord` 集合
- 该 candidate snapshot 打开、保持或解决的 `EvidenceGap`

引擎不输出：

- narrative summary
- 独立 execution telemetry log
- 脱离 canonical schema 的临时评分对象
- 对其他 proposition 的隐式读取结论

### 2. evaluation unit 固定为单个 proposition 的单次 recompute

一次 inference evaluation 只针对单个 `proposition_id` 运行。

要求：

- 所有规则结果都绑定同一个 target proposition
- 单次 recompute 内产出的 `InferenceRecord` 都属于同一个 candidate assessment snapshot
- 不允许跨 proposition 组装一个 assessment
- engine 不直接读取其他 proposition 或其他 proposition 的 assessment 作为推断输入

若未来需要 cross-proposition 判断，必须先通过独立 canonical relation object 或 finding-like canonical input 进入 judgment track，而不是让 v1 engine 直接跨 proposition 读取状态。

换言之，engine 只能消费 v1 已允许的 canonical edges：同一 proposition 下的 seed closure、latest/prior assessment lineage、gap membership 与 rule direct inputs；不得在实现层额外发明 cross-proposition relation。

### 3. 命名层级固定为 `assessment_type -> rule_family -> rule_id`

v1 固定三层：

- `assessment_type`：由 proposition 的 `assessment_anchor` 决定
- `rule_family`：稳定执行阶段与判断目标分组
- `rule_id`：单条规则的稳定标识，写入 `InferenceRecord.rule_id`

设计要求：

- `rule family` 是 engine contract 与读取面的稳定分组单位
- `rule_id` 是 audit、replay 与兼容检查的最小可引用单位
- 任何更细的业务分类只可作为 rule metadata 或 rule cluster，不得冒充新的 `rule family`

`rule_id -> rule_family -> assessment_type` 的稳定解引用，由独立的 [`rule-registry-contract.md`](rule-registry-contract.md) 定义。

### 4. rule engine 采用 fixed-order, change-only contract

assessment recompute 可以反复执行，但只有 canonical judgment output 变化时才写入新的 assessment snapshot。

固定顺序：

1. 装载 evaluation context
2. 预分配 candidate `assessment_id`
3. 运行 gate / evidence / resolution / confidence 规则
4. 生成绑定 candidate `assessment_id` 的 `InferenceRecord` 集合
5. 组装 candidate assessment payload
6. 决定是否提交新的 `Assessment`

实现可并行执行同阶段内互不依赖的规则，但对外可观察结果必须与上述固定顺序一致。

### 5. v1 不追求状态单调增强

rule engine 必须允许：

- `insufficient -> supported | contradicted | mixed`
- `supported | contradicted -> mixed`
- `supported | contradicted | mixed -> insufficient`

触发原因可以包括：

- 新 finding 到来
- live evidence 当前不可读导致 membership 收缩
- quality / comparability gate 失败
- 已解决的 gap 重新打开
- prior assessment comparison 发现当前证据不足以延续旧结论

单调的只有 `snapshot_seq`，不是 conclusion strength。

## Schema Position

canonical judgment 链路保持：

`finding -> proposition -> inference rule engine -> assessment / evidence_gap / inference_record`

其中：

- `finding` 提供确定性事实输入
- `proposition` 提供判断锚点与 `assessment_type`
- inference rule engine 提供显式规则过程
- `assessment` 表达当前判断状态
- `evidence_gap` 表达当前缺失条件
- `inference_record` 表达当前 snapshot 的直接规则依据

## Typed Design Sketch

以下类型仅用于说明 engine contract，不替代 canonical schema。

```ts
type InferenceRuleFamily =
  | "precondition_gate"
  | "quality_gate"
  | "comparability_gate"
  | "support_evidence"
  | "oppose_evidence"
  | "status_resolution"
  | "gap_management"
  | "confidence_shaping"
  | "assessment_transition";

type InferenceEvaluationContext = {
  session_id: string;
  proposition: Proposition;
  available_findings: Finding[];
  prior_assessments: Assessment[];
  open_gaps_from_latest: EvidenceGap[];
  assessment_type: Assessment["assessment_type"];
  evaluation_reason:
    | "proposition_registered"
    | "finding_arrived"
    | "finding_invalidated"
    | "gap_recheck"
    | "assessment_replay";
};

type CandidateAssessmentIdentity = {
  proposition_id: string;
  assessment_type: Assessment["assessment_type"];
  supersedes_assessment_id: string | null;
  snapshot_seq: number;
  assessment_id: string;
};

type InferenceRuleDefinition = {
  rule_id: string;
  rule_family: InferenceRuleFamily;
  applies_to: Assessment["assessment_type"][];
  stage_order: number;
  reads: Array<"findings" | "prior_assessments" | "open_gaps">;
  may_emit: {
    status_transition: boolean;
    open_gap: boolean;
    resolve_gap: boolean;
    confidence_contribution: boolean;
  };
};

type InferenceEvaluationOutcome = {
  candidate_identity: CandidateAssessmentIdentity;
  candidate_status: Assessment["status"];
  supporting_finding_ids: string[];
  opposing_finding_ids: string[];
  blocking_gap_ids: string[];
  non_blocking_gap_ids: string[];
  confidence_grade: Assessment["confidence_grade"];
  confidence_rationale: Assessment["confidence_rationale"];
  inference_records: InferenceRecord[];
};
```

## Engine Input Contract

### proposition 输入

每次 evaluation 必须从单个 proposition 开始，并显式读取：

- `proposition_id`
- `assessment_anchor`
- proposition subject 信息
- creation-time `seed_finding_refs`

rule engine 不得自行改写 proposition identity 或 assessment anchor。

### finding 输入

可供规则消费的 finding 必须满足：

- 已提交到 canonical finding layer
- 能通过稳定 canonical id 解引用
- 与 target proposition 的 subject / family / seed / prior inference dependency 有明确关系

引擎不得直接读取 projection summary 或自由文本 evidence description 作为判断输入。

### prior assessment 输入

prior assessments 只允许来自同一 proposition 的历史 snapshots。

典型用途：

- 检查是否存在已解决 gap 的重开
- 检查是否发生 status downgrade
- 检查 confidence 是否应保持、下降或重算

## Trigger Contract

assessment recompute 至少在以下事件后触发：

- 新 proposition 注册
- target proposition 直接依赖的 finding 到达
- target proposition 直接依赖的 finding 当前不可读或被 replay 替换
- 当前 open gap 可能被新 finding 或质量修复闭合
- prior latest assessment 被 supersede，需要重算 transition-sensitive rules

调度策略可以不同，但 canonical 结果必须满足：

- 相同 canonical inputs 重算，不额外生成新 snapshot
- 输入变化导致 judgment output 变化时，必须生成新 snapshot

## Rule Family Contract

### 通用 families

所有 assessment family 至少复用以下 rule family：

#### 1. `precondition_gate`

职责：

- 判断是否具备进入该 assessment family 的最低输入条件
- 在缺少 required finding family、subject coverage、time coverage 时打开 gap

输出约束：

- 通常产出 `miss` 或 `partial`
- 可打开 blocking gap
- 不直接给出高置信单向结论

#### 2. `quality_gate`

职责：

- 处理 `data_complete`、`sample_size`、`quality_status`、`null_rate` 等质量门槛

输出约束：

- 可打开 `data_quality_risk`
- 可压低 confidence
- 可触发从强结论降级为 `insufficient`

#### 3. `comparability_gate`

职责：

- 判断左右窗口、切片、grain、方法前提是否可比

输出约束：

- comparability failure 优先表现为 gap 或保守降级
- 不允许在 comparability 未通过时产出高 confidence 的 `supported` / `contradicted`

#### 4. `support_evidence`

职责：

- 累积直接支持 proposition 的 findings

输出约束：

- 只能把 finding 纳入 `supporting_finding_ids`
- 不负责决定最终 status

#### 5. `oppose_evidence`

职责：

- 累积直接反驳 proposition 的 findings

输出约束：

- 只能把 finding 纳入 `opposing_finding_ids`
- 不负责决定最终 status

#### 6. `status_resolution`

职责：

- 根据 support / oppose / blocking gaps / precondition 状态决定最终 `Assessment.status`

输出约束：

- 若最终状态变化，必须写 `produced_status_transition`
- 只允许写入与最终 candidate snapshot 一致的 transition

#### 7. `gap_management`

职责：

- 把本次规则判断映射为 gap open / keep / resolve

输出约束：

- `opened_gap_ids` / `resolved_gap_ids` 必须由具体 record 驱动
- gap 的打开、解决不得 out-of-band 写入

#### 8. `confidence_shaping`

职责：

- 形成 `confidence_rationale`
- 在 guardrails 内推出 `confidence_grade`

输出约束：

- 只能根据结构化理由塑形 confidence
- 不得用黑盒数值覆盖 rationale 维度

#### 9. `assessment_transition`

职责：

- 读取 prior latest assessment，判断是否发生 upgrade、downgrade 或 keep-state 但 changed-support/gap

输出约束：

- 用于解释为什么会 supersede 旧 snapshot
- 不单独决定 support / oppose membership

### assessment-type 到 rule family 的映射

v1 先固定 `assessment_type -> rule_family` 的映射，不在本文展开到每条业务规则。

| assessment_type | required rule families | optional rule clusters |
| --- | --- | --- |
| `change_assessment` | `precondition_gate`, `quality_gate`, `comparability_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `change_magnitude`, `direction_consistency` |
| `decomposition_assessment` | `precondition_gate`, `quality_gate`, `comparability_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `coverage_balance`, `residual_explained` |
| `anomaly_assessment` | `precondition_gate`, `quality_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `baseline_stability`, `repeat_occurrence` |
| `correlation_assessment` | `precondition_gate`, `quality_gate`, `comparability_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `lag_alignment`, `confounder_exposure` |
| `test_hypothesis_assessment` | `precondition_gate`, `quality_gate`, `comparability_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `significance`, `effect_size` |
| `forecast_assessment` | `precondition_gate`, `quality_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `forecast_stability`, `interval_reliability` |

要求：

- `optional rule clusters` 只是更细的业务分类，不是新的 `rule_family`
- 不允许某个 assessment family 绕过通用 `status_resolution` 直接写 assessment
- v1 新增 assessment family 时，必须先声明其 `rule_family` 映射，再进入 canonical contract

rule 的稳定定义、family 归属与版本边界由 [`rule-registry-contract.md`](rule-registry-contract.md) 统一定义。

## Candidate Assessment And Record Materialization

### candidate identity 预分配

为避免 `InferenceRecord.assessment_id` 与 `Assessment.applied_inference_record_ids` 的循环依赖，单次 recompute 必须先预分配 candidate assessment identity。

固定顺序：

1. 读取 prior latest assessment
2. 决定 `supersedes_assessment_id`
3. 计算 candidate `snapshot_seq`
4. 生成 candidate `assessment_id`
5. 用该 candidate `assessment_id` 生成本次 inference records
6. 用生成好的 inference record ids 回填 candidate assessment payload
7. 若 canonical outcome 与 prior latest assessment 完全一致，则丢弃 candidate，不提交 snapshot 与 records

candidate identity 只用于本次 recompute 的 canonical materialization，不是额外暴露给 consumer 的新对象类型。

### change-only 提交规则

只有以下 canonical output 任一变化时，才提交新 snapshot：

- `status`
- `confidence_grade`
- `confidence_rationale`
- `supporting_finding_ids`
- `opposing_finding_ids`
- `blocking_gap_ids`
- `non_blocking_gap_ids`
- `applied_inference_record_ids`
- subtype payload 中影响 judgment semantics 的字段

若上述输出完全不变：

- 不提交新的 assessment snapshot
- 不提交新的 inference records
- 继续复用 prior latest assessment

## Fixed Evaluation Order

### 1. context assembly

收集：

- target proposition
- 可用 findings
- prior latest assessment 及必要历史 snapshots
- 当前 open gaps

若 proposition 的 `assessment_anchor` 与期望 `assessment_type` 不一致，应视为上游 contract 错误，而不是产出 `miss`。

### 2. candidate identity allocation

先分配 candidate `assessment_id`，再执行 rule outcome materialization。

这是 v1 唯一允许 `InferenceRecord` 绑定“尚未提交 snapshot”的方式；一旦本次 recompute 被放弃，candidate id 不进入 consumer 可见 state。

### 3. gate evaluation

依次运行：

- `precondition_gate`
- `quality_gate`
- `comparability_gate`

gate 阶段的职责是决定：

- 是否缺少必要输入
- 是否存在质量或可比性阻塞
- 是否需要直接打开 blocking gap

gate 阶段不得直接把状态提升为高 confidence `supported` / `contradicted`。

### 4. evidence aggregation

分别运行：

- `support_evidence`
- `oppose_evidence`

该阶段只决定 live evidence membership：

- 哪些 findings 被计入 support
- 哪些 findings 被计入 oppose
- 哪些 findings 只作为 gap / rule context，而不进入方向性 membership

### 5. status resolution

最终 status 的判定口径不在本文内枚举，而统一由 [`assessment-judgment-policy.md`](assessment-judgment-policy.md) 定义。

本文只固定：

- `status_resolution` 必须以 gate、support、oppose 的结构化结果为输入
- 输出只能是 `insufficient | supported | contradicted | mixed`
- 结果必须与最终 candidate snapshot 一致

### 6. gap management

gap resolution 在最终 status 候选确定后执行，以决定：

- 哪些 gap 继续保持 open
- 哪些 gap 被新的 evidence 显式解决
- 哪些 gap 从 blocking 转成 non-blocking，或反之

若 gap membership 改变，即使 status 不变，也必须产出新 snapshot。

### 7. confidence shaping

confidence 在最终 status 和 gap 状态确定后计算。

原因：

- confidence 依赖最终 evidence sufficiency / consistency / rule coverage / quality impact
- 若先算 confidence，再改 status/gap，会导致 rationale 与 snapshot 不一致

### 8. transition finalization

最后比较 prior latest assessment 与 candidate outcome，决定：

- 是否发生 upgrade / downgrade / lateral transition
- 是否只是同状态下的 evidence membership 或 gap 变化
- 是否完全无 canonical 变化从而复用旧 snapshot

## Status Resolution Policy Binding

### evidence-first lattice

最终状态只能是：

- `insufficient`
- `supported`
- `contradicted`
- `mixed`

### policy 来源

以下口径不在本文内展开，而统一由 [`assessment-judgment-policy.md`](assessment-judgment-policy.md) 定义：

- 什么算实质 support
- 什么算实质 oppose
- 何时进入 `mixed`
- 何时即使存在单向证据也必须保守回到 `insufficient`

本文只要求 engine 对这些判断口径做确定性执行，不允许实现层临时拍板。

## Gap Policy

### open gap

gap 打开条件包括：

- required finding family 缺失
- required subject coverage / time coverage 缺失
- 规则前提不满足
- data quality / comparability 风险阻塞当前判断

### resolve gap

gap 解决需要满足两个条件：

- 新 evidence 或新 quality state 满足了原 `missing_requirement`
- 某条 `InferenceRecord` 显式把该 gap 记入 `resolved_gap_ids`

仅仅“本次没有再提到旧 gap”不等价于已解决。

### blocking vs non-blocking

规则：

- 会阻止 status 升级或稳定收敛的 gap 必须进入 `blocking_gap_ids`
- 只提供 caveat、但不改变当前可用判断的 gap 进入 `non_blocking_gap_ids`

`supported` 或 `contradicted` assessment 允许同时携带 blocking gap。

## Confidence Policy

### rationale-first

每次 confidence shaping 都必须先形成：

- `evidence_sufficiency`
- `evidence_consistency`
- `rule_coverage`
- `data_quality_impact`

再由这些结构化维度推出 `confidence_grade`。

### 全局 guardrails

复用并强调 [`assessment.md`](assessment.md) 已定义的 guardrails：

- `data_quality_impact = severe` 时，`confidence_grade` 不得高于 `low`
- `evidence_sufficiency = very_weak` 时，`confidence_grade` 不得高于 `low`
- `rule_coverage = minimal` 且 `evidence_consistency` 不是 `consistent` 时，`confidence_grade` 不得高于 `medium`
- `evidence_consistency = conflicting` 时，应优先 `mixed` 或保守 `insufficient`

## InferenceRecord Mapping Contract

### record 何时必须产出

v1 至少要求：

- 所有对当前 snapshot 的 `status`、gap state、confidence 有贡献的 `rule_family`，都必须有对应 record
- 不允许只记录 `hit` 而静默丢弃 `miss` / `partial`
- 不要求把与当前 snapshot 无关的历史 records 回挂到 `applied_inference_record_ids`

### `result` 判定

- `hit`：该 rule 的核心条件满足，并对当前 snapshot 产生正向判断贡献
- `miss`：该 rule 所需条件未满足，且这一未命中对当前 snapshot 的 `insufficient`、gap 保持或保守降级有解释价值
- `partial`：部分条件满足，足以贡献 context / caveat / 局部证据，但不足以单独完成其目标判断

### justification 写法

要求：

- `matched_conditions` 记录稳定 condition token
- `unmatched_conditions` 记录稳定缺失或失败 token
- `notes` 只补充少量非主语义说明，不得承载唯一规则语义

### status transition 写法

- 只有参与最终状态决议的 record 可以写 `produced_status_transition`
- 若最终 status 未变，但 support/gap/confidence 变化，则该字段可为 `null`
- 首次 assessment 建立时，`from_status = null`

### gap fields 写法

- 打开 gap 的 record 必须把对应 id 写入 `opened_gap_ids`
- 解决 gap 的 record 必须把对应 id 写入 `resolved_gap_ids`
- 同一 gap 在同一 snapshot 内不能同时出现在 open 和 resolve 两个集合

### family 暴露

`InferenceRecord` schema 本身不新增 `rule_family` 字段。

v1 要求通过 [`rule-registry-contract.md`](rule-registry-contract.md) 中定义的稳定 registry，从 `rule_id -> rule_family -> assessment_type` 完全可解引用，并作为：

- session state 中“命中的规则族”的来源
- context 审计中 rule grouping 的来源
- replay / compatibility 检查的来源

实现不得把 family 归属写成隐式字符串约定。

## State / Context Consumption Contract

### state surface

[`state-surface-schema.md`](state-surface-schema.md) 中的 `applied_inference_record_refs` 应满足：

- 仅索引 `latest_assessment.applied_inference_record_ids`
- consumer 通过 rule registry 汇总命中的 `rule_family`
- state surface 不内嵌完整 `InferenceRecord` payload

### context surface

[`context-surface-schema.md`](context-surface-schema.md) 中的 `applied_inference_records` 应满足：

- 完整覆盖 `latest_assessment.applied_inference_record_ids`
- 足以解释当前 status、gap、confidence 为什么成立或未成立
- 不混入 superseded snapshots 的历史 record

## Non-goals

本文不定义：

- 对外 HTTP path、query 参数、分页与兼容策略
- 具体实现中的调度器、队列、事务边界或存储表结构
- 每个 assessment family 下完整的业务规则枚举表
- action proposal 的排序 policy
- cross-proposition inference
- 使用模型生成 explanation 的提示词设计

## Acceptance Scenarios

1. proposition 首次进入评估，但 required finding family 不足时，生成 `insufficient` assessment，并写入至少一条 `miss` 或 `partial` 的 precondition/gap record。
2. 新 finding 到来满足原 blocking gap 后，旧 gap 被显式 resolve，assessment 从 `insufficient` 升级为更强状态，具体状态口径由 judgment policy 文档决定。
3. 强结论依赖的关键 finding 当前不可解引用时，status 可回退到 `insufficient`，并重新打开 blocking gap。
4. status 不变但 gap 集合变化时，仍生成新的 assessment snapshot。
5. status 不变但 confidence grade 或 rationale 变化时，仍生成新的 assessment snapshot。
6. 同一次 recompute 若 canonical outcome 完全不变，不生成新的 assessment snapshot，也不额外生成 inference records。
7. state surface 能从 `applied_inference_record_refs` 稳定汇总 rule families；context surface 能审计单条 `rule_id` 的 hit/miss/partial 和直接输入。
8. 同一次 recompute 中，records 可绑定 candidate `assessment_id`，但只有 snapshot 被提交时这些 records 才进入 canonical state。

## 与其他文档的关系

- [`assessment.md`](assessment.md) 定义 canonical `Assessment` / `EvidenceGap` / `InferenceRecord` schema
- [`rule-registry-contract.md`](rule-registry-contract.md) 定义 rule registry 的稳定解引用 contract
- [`assessment-judgment-policy.md`](assessment-judgment-policy.md) 定义不同 `assessment_type` 的 judgment policy 与判断门槛
- [`evidence-engine-runtime-lifecycle.md`](evidence-engine-runtime-lifecycle.md) 定义 assessment recompute 的 runtime lifecycle 与 change-only snapshot policy
- [`state-surface-schema.md`](state-surface-schema.md) 与 [`context-surface-schema.md`](context-surface-schema.md) 定义 inference 结果如何被读取面消费
