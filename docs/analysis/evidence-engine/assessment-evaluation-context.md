# Assessment Evaluation Context

本文档定义 Evidence Engine 中 `proposition -> assessment` 重算前的 evaluation context 组装契约。

状态：draft design。本文是 [`inference-and-gap-engine.md`](inference-and-gap-engine.md) 的实现级补充，负责把 assessment recompute 的输入边界、finding 候选集合与触发映射固定为可直接实现的 deterministic contract。

## 目的

固定以下问题的统一答案：

- assessment recompute 的唯一 authority input 是什么
- `当前 proposition closure 中可解引用的 findings` 到底如何组装
- creation-time seed hydration 与 live evidence candidate set 如何区分
- `agent_authored proposition` 在无 seed refs 时如何形成合法 evaluation context
- proposition registration、related finding changes、replay 如何稳定触发 recompute
- recompute entry 的 dedupe key、recovery 与 downstream publish 边界是什么

## 主题位置

本文只定义 recompute 前的 context assembly。

它不定义：

- `support_evidence` / `oppose_evidence` / `status_resolution` 的判断算法
- gap open / keep / resolve 的最终 materialization
- snapshot commit / supersede 的最终提交规则
- 对外 HTTP 读取面或 projection 形状

assessment recompute 的固定顺序、rule families 与 snapshot policy 仍以 [`inference-and-gap-engine.md`](inference-and-gap-engine.md) 为准。

## Fixed Design Decisions

### 1. authority boundary 固定为 canonical objects

evaluation context 只能由以下 canonical inputs 组装：

- target `proposition`
- 同一 proposition 的 committed `assessment` 历史
- 同一 proposition 当前 `open` 的 `EvidenceGap`
- 同一 session 的 committed canonical `finding` layer
- 当前重算入口显式携带的 `trigger_finding_ids`

不允许读取：

- UI projection、top-k、summary text
- 未提交 finding
- 其他 proposition 的 assessment / gap
- 黑盒模型输出或临时评分对象

### 2. evaluation context 固定绑定单 proposition 与单 finding snapshot

单次 recompute 固定针对：

- 单个 `session_id`
- 单个 `proposition_id`
- 一个稳定的 committed finding snapshot

相同 proposition、相同 finding snapshot、相同 registry versions 下，evaluation context 必须稳定重放出同一组 canonical inputs。

### 3. recompute candidate set 不等于 read-surface `relevant_findings`

`candidate_finding_ids` 是 rule engine 的输入候选集。

`relevant_findings` 是读取面围绕 `latest_assessment` 暴露的 live evidence closure。

固定要求：

- `candidate_finding_ids` 可以是未来 `supporting_finding_ids` / `opposing_finding_ids` 的超集
- `relevant_findings` 只来自 committed `latest_assessment` 及其 `applied_inference_records`
- 读取层不得把 recompute candidate set 暴露成 proposition context 的 live evidence set

### 4. authored proposition 与 seeded proposition 共用同一 context contract

`system_seeded proposition` 与 `agent_authored proposition` 都使用同一 `AssessmentEvaluationContext`。

差异只体现在 candidate finding discovery：

- seeded proposition 可以从 `seed_finding_refs` 起步
- authored proposition 可以没有任何 resolved seed refs
- authored proposition 不因缺少 seed refs 而跳过 assessment 轨道；其首轮重算允许在 `candidate_finding_ids = []` 下形成 `insufficient` 或 gap-driven 输出

### 5. recompute 固定绑定单 proposition 与单 registry snapshot

单次 assessment recompute entry 固定针对：

- 单个 `session_id`
- 单个 `proposition_id`
- 一个稳定的 committed finding snapshot
- 一个稳定的 assessment rule / registry snapshot

推荐 recompute dedupe key：

- `(proposition_id, finding_snapshot_identity, assessment_registry_version)`

固定要求：

- 相同 dedupe key replay 时，`AssessmentEvaluationContext` 必须稳定
- context assembly 完成但 assessment snapshot 尚未 committed 时，可按相同输入整体重跑
- assessment snapshot 若已 committed 但尚未 externally visible，则下游 publish path 必须从 committed snapshot 继续，而不是重写 context truth

## Typed Design Sketch

```ts
type AssessmentEvaluationContext = {
  session_id: string;
  proposition: Proposition;
  assessment_type: PropositionAssessmentAnchor["assessment_type"];
  candidate_assessment_id: string;
  current_latest_assessment_id: string | null;
  prior_assessment_ids: string[];
  open_gap_ids: string[];
  resolved_seed_finding_ids: string[];
  trigger_finding_ids: string[];
  candidate_finding_ids: string[];
  schema_version: "assessment_evaluation_context.v1";
};
```

约束：

- `candidate_assessment_id` 在 family 执行前预分配，供 candidate `InferenceRecord` / `EvidenceGap` 绑定
- 若最终没有 committed snapshot，所有绑定该 candidate id 的 candidate outputs 一并丢弃
- `prior_assessment_ids` 按 `snapshot_seq ASC` 稳定排序
- `candidate_finding_ids` 采用 stable de-duplicated order

## Context Assembly Algorithm

### Phase 1. proposition anchor load

必须先装载：

- target `proposition`
- `proposition.assessment_anchor.assessment_type`
- `proposition.origin`
- `proposition.subject`
- `proposition.seed_finding_refs`

若 proposition 不存在或跨 session，不进入 rule engine；这属于上游 canonical lookup error，而不是族级 `miss`。

### Phase 2. prior assessment / open gap load

必须加载同 proposition 的：

- 全部 committed `assessment` snapshots，按 `snapshot_seq ASC`
- 当前 `status = open` 的 `EvidenceGap`

派生字段：

- `current_latest_assessment_id = latest_assessment.assessment_id | null`
- `prior_assessment_ids = all committed assessment ids`
- `open_gap_ids = all current open gap ids`

`open gaps` 只提供当前仍需延续或待解决的 requirement semantics；resolved gaps 不进入本轮 live context。

### Phase 3. seed hydration set

`resolved_seed_finding_ids` 由 `proposition.seed_finding_refs` 稳定解引用得到：

- 能解引用到 committed finding 的，按 `seed_finding_refs` 顺序加入
- 当前不可解引用的，不进入 `resolved_seed_finding_ids`
- 不可解引用信息只在 read surface 的 `seed_entries.finding = null` 暴露，不直接伪造 finding candidate

### Phase 4. trigger normalization

`trigger_finding_ids` 的 authority 来源只允许是：

1. proposition registration 传入的 resolved seed finding ids
2. finding replay / reseed 输出的 affected finding ids
3. related finding change detector 输出的 finding ids

稳定排序：

1. canonical `Finding` 默认稳定排序
2. `finding_id ASC`

### Phase 5. carry-forward closure replay

若当前已有 `latest_assessment`，则必须把上一 latest closure 中的直接 finding inputs 回放到本轮候选集中。

固定回放来源：

- `latest_assessment.supporting_finding_ids`
- `latest_assessment.opposing_finding_ids`
- `latest_assessment.applied_inference_record_ids` 对应 records 的 `input_finding_ids`
- 当前 open gaps 的 `related_finding_ids`

目的：

- 保证降级、gap reopen、membership 收缩时仍能看到上轮直接使用过的事实输入
- 避免 rule engine 只看新 trigger 而丢失当前 canonical judgment closure

### Phase 6. proposition-compatible trigger expansion

把 `trigger_finding_ids` 扩张到当前 proposition 的 live candidate set 时，必须通过 `proposition compatibility` 判断。

v1 固定兼容条件：

- 同一 `session_id`
- finding 已提交到 canonical finding layer
- finding family 与当前 `assessment_type` 的 judgment track 兼容
- finding subject 不得与 proposition subject 的非空锚点冲突
- 若 proposition subject 指定了 metric / entity / grain / slice，finding 不能在这些已声明轴上给出相反值

兼容判断只做 typed subject / family 边界校验；它不在此阶段决定 finding 是否最终 support 或 oppose。

### Phase 7. authored proposition discovery fallback

当 proposition 为 `agent_authored` 且同时满足以下条件时，允许启动 discovery fallback：

- `resolved_seed_finding_ids = []`
- 上一 latest assessment 不存在，或其 closure finding set 为空
- 本轮 `trigger_finding_ids = []`

fallback 行为固定为：

- 在同 session committed finding layer 中，按 `assessment_type` 兼容 family 扫描 finding
- 仅保留与 proposition subject 兼容的 findings
- 不得跨 subject 轴扩张到无关 metric/entity/slice
- 扫描结果进入 `candidate_finding_ids`

若 fallback 结果仍为空，本轮 recompute 仍然合法；后续 family 必须在空输入上给出保守输出，而不是跳过 assessment 轨道。

### Phase 8. candidate set finalization

`candidate_finding_ids` 是以下集合的稳定去重并集：

- `resolved_seed_finding_ids`
- `trigger_finding_ids`
- prior latest closure replay finding ids
- open gap `related_finding_ids`
- authored proposition discovery fallback finding ids

固定要求：

- 去重按 `finding_id` 完成
- 排序复用 canonical `Finding` 稳定排序
- 该集合是 rule engine 的唯一 finding input boundary

## Related Finding Change Mapping

runtime pipeline 中的 `related finding changes` 在 v1 固定为以下任一命中：

1. finding 被 `proposition.seed_finding_refs` 直接引用
2. finding 出现在当前 latest assessment 的 support / oppose membership 中
3. finding 出现在当前 latest assessment 的 applied inference record `input_finding_ids` 中
4. finding 出现在当前 open gaps 的 `related_finding_ids` 中
5. finding 与 proposition subject / assessment family 兼容，且能作为当前 authored proposition 的 discovery fallback 输入

命中任一条件，即可把该 proposition 放入 `affected_proposition_ids`，进入 assessment recompute。

## Trigger Contract By Entry Point

### proposition registration

- newly registered proposition 必须进入 recompute 队列
- seeded proposition 的初始 `trigger_finding_ids` 为 resolved seed finding ids
- authored proposition 允许以 `trigger_finding_ids = []` 启动首轮 recompute

### finding replay / reseed

- 以 replay 后的 committed finding set 为权威输入
- seeding run 输出的 `affected_proposition_ids` 是系统种子命题的首要触发源
- 对已存在 proposition，仍需通过本文件的 context assembly 重新组装 `candidate_finding_ids`

### direct related finding change

- 不重建 proposition identity
- 只把命中的 proposition 放入 recompute
- finding change 是否最终改变 assessment，由后续 family 与 snapshot diff 决定

## Downstream Handoff Boundary

本文件只定义 recompute 的 canonical input boundary，不直接定义 assessment judgment 过程。

但 handoff 基线固定为：

- `AssessmentEvaluationContext` 是 family rule engine 的唯一结构化输入入口
- `candidate_finding_ids` 是唯一 finding 输入边界；后续 family 不得临时扩张到未进入该集合的 finding
- rule engine 输出的新 snapshot 若未 committed，不得进入 publish path
- committed snapshot 一旦产生，proposal refresh 与 externally visible 切换应按 [`runtime-lifecycle.md`](runtime-lifecycle.md) 的 proposition-local publish bundle 规则继续推进

## Output Guarantees

- evaluation context 只使用 canonical objects，可 deterministic replay
- `candidate_finding_ids` 可以为空；空集仍是合法输入
- `candidate_finding_ids` 是 recompute 输入候选集，不自动等于未来 committed latest assessment membership
- read surfaces 必须继续以 committed latest assessment closure 暴露 `relevant_findings`

## Related Documents

- [`inference-and-gap-engine.md`](inference-and-gap-engine.md)
- [`runtime-lifecycle.md`](runtime-lifecycle.md)
- [`runtime-pipeline.md`](runtime-pipeline.md)
- [`finding-proposition-seeding.md`](finding-proposition-seeding.md)
- [`schemas/proposition.md`](schemas/proposition.md)
- [`schemas/assessment.md`](schemas/assessment.md)
- [`schemas/context-surface-schema.md`](schemas/context-surface-schema.md)
