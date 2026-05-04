# Support, Oppose, And Status Resolution

本文档定义 Evidence Engine 中 `support_evidence`、`oppose_evidence` 与 `status_resolution` 的实现级契约。

状态：draft design。本文是 [`inference-and-gap-engine.md`](inference-and-gap-engine.md) 的实现级补充，负责把 family-specific judgment policy 收束成稳定、可重放的 directional evidence aggregation 与 status resolution contract。

## 目的

固定以下问题的统一答案：

- 什么是 direction evidence 的最小评估单元
- finding 何时进入 `supporting_finding_ids`、`opposing_finding_ids` 或 neither
- gate family 输出如何约束强结论但不越权直接写最终 status
- `supported` / `contradicted` / `mixed` / `insufficient` 如何从结构化结果稳定决议
- family-specific judgment threshold 如何转成可执行的 resolution input

## 主题边界

本文只定义：

- `support_evidence`
- `oppose_evidence`
- `status_resolution`

本文不定义：

- `precondition_gate`、`quality_gate`、`comparability_gate` 自身的 requirement mapping
- gap 的 open / keep / resolve materialization
- `confidence_grade` 与 `confidence_rationale` 的最终塑形
- snapshot commit / supersede 规则

## Fixed Design Decisions

### 1. directional evidence 的最小单元是 `finding_id`

v1 中 support / oppose membership 固定以 `finding_id` 为原子单位。

这意味着：

- `supporting_finding_ids` / `opposing_finding_ids` 的最小成员是 finding
- rule 可以消费 artifact lineage 或 prior assessments，但最终方向性归属仍需落回 finding ids
- 只提供背景、质量、comparability 或 transition 作用的 finding，不得混入 directional membership

### 2. directional family 只负责 membership，不直接写最终 status

`support_evidence` 只决定 candidate support membership。

`oppose_evidence` 只决定 candidate oppose membership。

二者都不能直接写最终 `Assessment.status`。

最终状态只能由 `status_resolution` 依据：

- directional evidence 结果
- gate family 的结构化 guardrails
- family-specific threshold tokens

统一决议。

### 3. 同一 finding 在单个 snapshot 中不得同时作为 support 与 oppose 成员提交

若同一 `finding_id` 在本轮同时命中 support 与 oppose 候选：

- `status_resolution` 不得把该 finding 同时写入两个 membership 数组
- 若缺少 family-specific disambiguation 规则，默认把该 finding 从两个方向集合都移除
- 并生成结构化 `resolution_conflict` 候选，供 `gap_management` 决定是否 materialize 为 gap

### 4. threshold 判断采用稳定 token，而不是自由文本

每个 `assessment_type` 的 judgment policy 必须下沉为稳定 token 集合。

v1 固定四类 token 入口：

- `support_requirement_tokens`
- `oppose_requirement_tokens`
- `mixed_resolution_policy`
- `insufficient_fallback_policy`

`assessment-judgment-policy.md` 负责声明 family-specific token 语义；本文负责定义这些 token 如何进入 aggregation 与 resolution。

## Typed Design Sketch

```ts
type DirectionalEvidenceResult = {
  rule_id: string;
  direction: "support" | "oppose";
  result: "hit" | "miss" | "partial";
  candidate_finding_ids: string[];
  satisfied_requirement_tokens: string[];
  unsatisfied_requirement_tokens: string[];
  rationale_notes: string[];
};

type StatusResolutionInput = {
  support_results: DirectionalEvidenceResult[];
  oppose_results: DirectionalEvidenceResult[];
  gate_guardrail_tokens: string[];
  support_requirement_tokens: string[];
  oppose_requirement_tokens: string[];
  mixed_resolution_policy:
    | "prefer_mixed_on_structured_conflict"
    | "prefer_insufficient_when_conflict_not_substantive";
  insufficient_fallback_policy:
    | "fallback_when_no_direction_meets_threshold"
    | "fallback_when_gate_blocks_and_threshold_not_met";
};
```

## Directional Evidence Contract

### support_evidence

`support_evidence` 只能做两件事：

- 判断哪些 finding 对 proposition 当前判断形成直接支持
- 写出 support side 已满足 / 未满足的 threshold tokens

固定要求：

- finding 必须来自 `AssessmentEvaluationContext.candidate_finding_ids`
- 只因 creation-time seed 身份，不足以直接成为 support
- 仅提供 subject coverage、quality、comparability 上下文的 finding 不进入 support membership
- 允许多个 rule 贡献同一 support finding，但最终 membership 必须去重

### oppose_evidence

`oppose_evidence` 与 `support_evidence` 对称：

- 只判断直接反驳 proposition 的 findings
- 只写 oppose side 的 threshold tokens
- 不直接决定最终 status

固定要求：

- 反驳必须指向 proposition 当前 judgment semantics，而不是一般性 caveat
- 仅表示质量差、不可比、coverage 不足的 finding，不得伪装成 oppose evidence

### directional result normalization

单轮重算结束时，directional family 必须先完成以下规范化：

- `candidate_finding_ids` 去重
- `satisfied_requirement_tokens` / `unsatisfied_requirement_tokens` 去重
- 过滤同一 finding 的双向冲突
- 形成 `normalized_supporting_finding_ids`
- 形成 `normalized_opposing_finding_ids`

这一步仍不提交 assessment，只把结果交给 `status_resolution`。

## Gate Guardrail Consumption

`status_resolution` 只能消费 gate family 的结构化输出，不消费自由文本说明。

固定可消费输入：

- precondition missing requirement tokens
- quality impact tokens
- comparability impact tokens
- family-specific fallback policy tokens

固定不可消费输入：

- narrative warnings
- projection summaries
- 未结构化的 notes 文案

门槛族的作用是施加保守 guardrail，而不是直接写最终状态。

## Status Resolution Algorithm

### Phase 1. threshold satisfaction

先分别判断：

- `support_threshold_met = support_requirement_tokens` 是否全部被 support side 满足
- `oppose_threshold_met = oppose_requirement_tokens` 是否全部被 oppose side 满足

只要某侧 requirement tokens 未全部满足，该方向就不能进入强结论。

### Phase 2. guardrail evaluation

再判断 gate guardrails 是否要求 fallback。

guardrail 的固定解释为：

- 若 gate output 只表明 caveat，但当前方向阈值已成立，则允许与 `supported` / `contradicted` 并存
- 若 gate output 表明当前方向阈值所依赖的最低输入仍未满足，则该方向必须 fallback
- guardrail 可以压制强结论，但不能把纯 gate failure 直接改写为 oppose evidence

### Phase 3. final status resolution

v1 固定决议顺序：

1. 若 `support_threshold_met` 且 `oppose_threshold_met`，返回 `mixed`
2. 否则，若 `support_threshold_met` 且 support side 未被 guardrail fallback，返回 `supported`
3. 否则，若 `oppose_threshold_met` 且 oppose side 未被 guardrail fallback，返回 `contradicted`
4. 其余情况返回 `insufficient`

补充规则：

- `mixed` 表示存在结构化、实质性的双向证据冲突，不等价于低 confidence
- 若双向都有弱信号，但任一侧都未达到阈值，结果必须是 `insufficient`
- 阻塞性 gap 可以与 `supported` / `contradicted` 并存，但不允许掩盖当前仍缺的 requirement semantics

## Family-Specific Token Mapping

### `change_assessment`

- support tokens 至少覆盖：变化方向、变化量级、可比性成立
- oppose tokens 至少覆盖：反向变化成立 或 变化量不足以支撑命题

### `decomposition_assessment`

- support tokens 至少覆盖：主要贡献项覆盖、解释方向一致、残差可接受
- oppose tokens 至少覆盖：主要贡献项方向冲突 或 覆盖不足以支撑命题

### `anomaly_assessment`

- support tokens 至少覆盖：稳定偏离、非质量噪声
- oppose tokens 至少覆盖：基线波动 / 节律 / 质量问题足以解释候选

### `correlation_assessment`

- support tokens 至少覆盖：方向、强度、对齐方式
- oppose tokens 至少覆盖：弱相关、反向相关、时间对齐失败

### `test_hypothesis_assessment`

- support tokens 至少覆盖：显著性、效应量
- oppose tokens 至少覆盖：显著反方向 或 效应量明确不支持

### `forecast_assessment`

- support tokens 至少覆盖：预测方向稳定、区间可靠
- oppose tokens 至少覆盖：方向相反 或 不确定性过高

这些 token 的稳定命名与示例由 [`rules/assessment-judgment-policy.md`](rules/assessment-judgment-policy.md) 维护；本文只固定其消费位置。

## Output Contract

`status_resolution` 的 canonical 输出固定为：

- normalized `supporting_finding_ids`
- normalized `opposing_finding_ids`
- `status`
- 结构化 conflict / fallback tokens

它不得直接输出：

- `gap_memberships`
- `confidence_grade`
- `supersedes_assessment_id`

## Related Documents

- [`inference-and-gap-engine.md`](inference-and-gap-engine.md)
- [`assessment-evaluation-context.md`](assessment-evaluation-context.md)
- [`gap-confidence-and-transition-materialization.md`](gap-confidence-and-transition-materialization.md)
- [`rules/assessment-judgment-policy.md`](rules/assessment-judgment-policy.md)
- [`schemas/assessment.md`](schemas/assessment.md)
