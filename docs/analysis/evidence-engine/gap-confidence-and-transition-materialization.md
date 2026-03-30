# Gap, Confidence, And Transition Materialization

本文档定义 Evidence Engine 中 `gap_management`、`confidence_shaping`、`assessment_transition` 与 candidate inference outputs 的实现级 materialization 契约。

状态：draft design。本文是 [`inference-and-gap-engine.md`](inference-and-gap-engine.md) 的实现级补充，负责把后半段 family 的 ownership、candidate object 生命周期与 commit 前 canonical diff 组装规则固定下来。

## 目的

固定以下问题的统一答案：

- 哪些 family 有权打开、保持、解决 gap
- `blocking` / `severity` 如何从结构化结果收敛
- `confidence_grade` 与 `confidence_rationale` 如何从 gate / evidence / resolution 输出塑形
- `assessment_transition` 如何只解释变化，不越权决定 support / oppose / status
- candidate `InferenceRecord` / `EvidenceGap` 何时进入 canonical state，何时整体丢弃

## 主题边界

本文只定义 materialization 责任链：

- `gap_management`
- `confidence_shaping`
- `assessment_transition`
- candidate object finalization

本文不重写：

- evaluation context assembly
- directional evidence aggregation
- family-specific judgment threshold 语义

## Fixed Design Decisions

### 1. `gap_management` 是唯一 canonical materialization owner

其他 family 可以产出：

- missing requirement candidates
- quality / comparability risk candidates
- resolution conflict candidates

但只有 `gap_management` 能把这些 candidates 收敛为：

- `open`
- `keep`
- `resolve`

并最终写入 `EvidenceGap` / `gap_memberships`。

### 2. gap identity 与 snapshot classification 分离

固定边界：

- `gap_id` 绑定 proposition + gap semantics
- `blocking` / `severity` 只属于 snapshot-owned `gap_memberships`
- 同一 open gap 在不同 snapshots 中可改变 `blocking` / `severity`
- resolved gap 再次出现时必须创建新的 `gap_id`

### 3. confidence shaping 只消费结构化维度

`confidence_shaping` 的输入只允许来自：

- gate family 的结构化 impacts
- support / oppose threshold coverage
- final `status`
- transition family 暴露的 prior-vs-current 比较结果

不得直接消费 narrative explanation 生成 confidence。

### 4. `assessment_transition` 只解释变化，不决定 judgment ownership

`assessment_transition` 只能：

- 读取 prior latest assessment
- 对比 candidate canonical output 与 prior latest
- 生成 transition-oriented `InferenceRecord`
- 决定是 supersede 还是 no-op discard

它不能重新决定：

- `supporting_finding_ids`
- `opposing_finding_ids`
- `status`
- gap identity

## Typed Design Sketch

```ts
type GapMaterializationResult = {
  opened_gap_ids: string[];
  kept_gap_ids: string[];
  resolved_gap_ids: string[];
  gap_memberships: GapMembershipEntry[];
};

type ConfidenceShapingResult = {
  confidence_grade: ConfidenceGrade;
  confidence_rationale: ConfidenceRationale;
};

type AssessmentTransitionResult = {
  supersedes_assessment_id: string | null;
  canonical_diff_detected: boolean;
  input_assessment_ids: string[];
};
```

## Gap Management Contract

### candidate sources

`gap_management` 可以消费的 candidates 固定来自：

- `precondition_gate`
- `quality_gate`
- `comparability_gate`
- `status_resolution` 暴露的 `resolution_conflict`

`support_evidence` / `oppose_evidence` 不直接创建 gap identity。

### open / keep / resolve rules

固定收敛规则：

- candidate requirement 仍然缺失时：对同语义 open gap 执行 `keep`，无旧 gap 时执行 `open`
- candidate requirement 已被满足且存在同语义 open gap 时：执行 `resolve`
- 本轮未出现、且也无结构化解决依据的 gap，不得通过沉默缺席自动关闭

### blocking classification

`blocking = true` 的固定语义是：

- 当前 gap 会阻止 proposition 在下一步判断中稳定形成或升级为更强结论

v1 默认判定：

- core precondition 缺失：`blocking = true`
- severe quality / comparability risk 且当前强结论无法无保留维持：`blocking = true`
- 已有可成立的强结论，但 gap 只作为 caveat：`blocking = false`
- `resolution_conflict` 是否 blocking，取决于它是否阻止当前 direction 收敛

### severity classification

`severity` 按当前 snapshot 对 gap pressure 的分类固定为：

- `critical`：缺核心输入，当前 judgment track 无法继续成立
- `high`：存在 severe quality/comparability/rule conflict，已实质影响当前判断稳定性
- `medium`：存在明确缺口，但当前仍可形成保守判断
- `low`：仅作为 caveat 或 follow-up hint，不影响当前主结论是否成立

## Confidence Shaping Contract

### rationale dimensions

`confidence_shaping` 必须同时写出：

- `evidence_sufficiency`
- `evidence_consistency`
- `rule_coverage`
- `data_quality_impact`

以及必要的 `rationale_notes`。

### dimension mapping

v1 固定映射：

- `evidence_sufficiency`：由 directional threshold coverage、membership 完整性、open blocking gaps 共同决定
- `evidence_consistency`：由 support/oppose 冲突与 `mixed` / `resolution_conflict` 决定
- `rule_coverage`：由 required families 是否产出结构化结果、threshold tokens 覆盖度决定
- `data_quality_impact`：只由 `quality_gate` 的结构化 impacts 决定

comparability impacts 不发明新的 base dimension；它们必须通过：

- 降低 `evidence_sufficiency`
- 降低 `rule_coverage`
- 或写入 `rationale_notes`

来体现。

### global guardrails

必须继续复用 [`schemas/assessment.md`](schemas/assessment.md) 中的全局 guardrails：

- `data_quality_impact = severe` 时，`confidence_grade` 不得高于 `low`
- `evidence_sufficiency = very_weak` 时，`confidence_grade` 不得高于 `low`
- `rule_coverage = minimal` 且 `evidence_consistency` 不是 `consistent` 时，`confidence_grade` 不得高于 `medium`
- `evidence_consistency = conflicting` 时，不得产出高 confidence 单向结论

## Assessment Transition Contract

### candidate assessment identity

在任何 family 运行前，必须先预分配 `candidate_assessment_id`。

用途固定为：

- 作为本轮 candidate `InferenceRecord.assessment_id`
- 作为本轮 candidate gap / membership / snapshot payload 的 identity anchor

若本轮最终 `canonical_diff_detected = false`：

- candidate assessment snapshot 不提交
- candidate inference records 一并丢弃
- candidate gap open / resolve 结果一并丢弃

### prior assessment input

`assessment_transition` 只允许读取：

- current latest assessment
- 本轮 candidate canonical output

若当前 proposition 尚无 latest assessment：

- `supersedes_assessment_id = null`
- 仍需执行 canonical diff 判断，以确认是否真的形成首个 snapshot

### canonical diff set

`assessment_transition` 必须以以下字段作为 canonical diff set：

- `status`
- `confidence_grade`
- `confidence_rationale`
- `supporting_finding_ids`
- `opposing_finding_ids`
- `gap_memberships`
- `applied_inference_record_ids`
- subtype payload 中影响 judgment semantics 或 agent 决策的 canonical 字段

只要上述任一字段变化，就必须认为 `canonical_diff_detected = true`。

## InferenceRecord Materialization

### must-materialize outputs

凡是对当前 committed snapshot 有贡献的结构化 rule outputs，都必须 materialize 为 `InferenceRecord`。

至少包括：

- gate family 的 `hit / miss / partial`
- support / oppose family 的 directional结果
- status resolution 的最终决议记录
- gap management 的 open / keep / resolve 记录
- confidence shaping 的 confidence contribution 记录
- assessment transition 的 supersede / no-op 比较记录

### discard behavior

若 candidate snapshot 最终不提交：

- 本轮 candidate inference records 不进入 canonical state
- 本轮 candidate gap lifecycle 不进入 canonical state
- 读取层不得暴露这些 candidate-only objects

## Related Documents

- [`inference-and-gap-engine.md`](inference-and-gap-engine.md)
- [`assessment-evaluation-context.md`](assessment-evaluation-context.md)
- [`support-oppose-and-status-resolution.md`](support-oppose-and-status-resolution.md)
- [`schemas/assessment.md`](schemas/assessment.md)
- [`rules/rule-registry-contract.md`](rules/rule-registry-contract.md)
