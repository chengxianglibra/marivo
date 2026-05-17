# Finding -> Proposition Seeding

本文档定义 Evidence Engine 中 `finding -> system-seeded proposition` 的完整运行时契约。

状态：draft design。本文是 `finding -> proposition` 的实现级规范文档，负责把 [`runtime-pipeline.md`](runtime-pipeline.md) 中的 seeding 主线、[`schemas/finding.md`](schemas/finding.md) 中的 finding contract、以及 [`schemas/proposition.md`](schemas/proposition.md) 中的 proposition schema 收束成可直接落地实现的统一设计。

## 目的

固定以下问题的统一答案：

- system-seeded proposition 何时触发、以什么输入触发
- seed template registry 的 shape、版本与 authority boundary 是什么
- single-finding / multi-finding template 如何稳定匹配
- proposition payload、identity、lineage、seed refs 如何确定性生成
- seeding run 的 dedupe key、recovery 与 assessment handoff 如何稳定定义
- seeding 如何做 registration、去重、replay 与 soft invalidation 协调
- 哪些 finding family 会 seed proposition，哪些不会

## 主题位置

Evidence Engine 主线仍然是：

`artifact -> finding -> proposition -> assessment -> action proposal`

其中：

- [`runtime-pipeline.md`](runtime-pipeline.md) 固定 seeding 在 committed finding 之后、assessment recompute 之前
- [`schemas/finding.md`](schemas/finding.md) 固定 seeding 的唯一 authority input 是 committed canonical finding layer
- [`schemas/proposition.md`](schemas/proposition.md) 固定 seeding 的输出必须是 session-local、typed、immutable 的 `system_seeded proposition`
- [`inference-and-gap-engine.md`](inference-and-gap-engine.md) 固定 proposition 注册后进入统一 assessment 轨道

本文不重写这些边界；它只补足 seeding 自身缺失的实现级 contract。

## 非目标

本文不定义：

- `agent_authored proposition` 的写入 surface
- assessment family 的 judgment policy
- action proposal 的刷新策略
- 对外 HTTP wire contract
- 当前代码实现的迁移步骤

## 固定设计决策

### 1. Seeding authority input 仍然是 committed findings

seeding 的 trigger input 固定为 committed findings。

但是，为了构造与 `proposition` schema 兼容的 payload，template 在运行时允许解引用以下 canonical handles：

- seed finding 自身
- seed finding payload 中显式携带的 `FindingRef`
- seed finding payload / provenance 中显式携带的 `ArtifactRef` / `ArtifactItemRefRef`
- seed finding 的 `artifact_id` 与 `step_ref`

限制：

- 只能解引用 committed canonical objects
- 只能沿 finding 已显式声明的 typed refs / lineage handles 解引用
- 不允许回读 projection、UI summary、自由文本 explanation
- 不允许在 session 内“再搜一圈相似结果”替代显式 slot / ref 匹配

因此，seeding 的 authority boundary 仍然是 finding layer；artifact dereference 只是 finding 所携 canonical lineage 的确定性展开，不是新的上游 authority。

### 2. `system_seeded` identity 必须与 `agent_authored` 分区

`system_seeded proposition` 与 `agent_authored proposition` 不共享 identity。

因此，system-seeded proposition 的 identity normalization 输入必须包含：

- `origin.kind = "system_seeded"`
- `lineage.derivation_version`
- proposition family 的 judgment semantics

而不包含：

- `template_version`
- `seed_finding_refs` 的顺序
- assessment state

这样可以同时满足：

- 同 family、同 judgment semantics 的 system-seeded proposition 稳定去重
- authored 与 seeded 不跨来源合并
- 当 breaking seeding logic 需要产生新 proposition 时，可通过 `derivation_version` 显式切分 identity boundary

### 3. `seed_finding_refs` 是首次注册写入的 creation-time seed

固定规则：

- proposition 首次注册时写入 `seed_finding_refs`
- 命中既有 proposition identity 时，不回写、不追加、不重排 `seed_finding_refs`
- 后续 finding 变化通过 assessment membership 表达，不通过 proposition 本体回写

### 4. Template registry 是 canonical contract

v1 中 system seeding 不允许把规则散落在临时 if/else 中。

必须存在稳定 registry，至少声明：

- `template_id`
- `template_version`
- `derivation_version`
- template match mode
- allowed seed finding families / slot schema
- field resolution rules
- creation condition
- output proposition family

registry 版本变化必须可审计、可 replay、可解释。

## Runtime Contract

### Trigger

seeding 在以下场景触发：

1. 新 committed finding set 写入完成
2. finding replay 完成并形成新的 committed finding set
3. 显式 seeding replay / reseed job 被请求

seeding 不在以下场景触发：

- staged artifact
- extraction pending / failure
- projection refresh
- 仅 assessment / action proposal 变化

### Seeding transaction boundary

单次 seeding run 固定针对：

- 单个 `session_id`
- 一个稳定的 committed finding snapshot
- 一个稳定的 template registry snapshot

写入产物固定为：

- 0..N 个新注册 proposition
- 0..N 个命中既有 proposition identity 的 registration hit
- 一个稳定的 `affected_proposition_ids` 集合，供 assessment recompute 消费

seeding run 必须可重放，且相同输入不得制造不同 proposition 集合。

### Run dedupe key and recovery

推荐 seeding run dedupe key：

- `session_id`
- committed finding snapshot identity
- `registry_version`

固定要求：

- 相同 dedupe key replay 时，`created_proposition_ids`、`existing_proposition_ids` 与 `affected_proposition_ids` 必须稳定
- 若 seeding crash 发生在 registration commit 之前，则整轮 seeding 允许按相同输入整体重跑
- 若 proposition registration 已 committed，但 assessment handoff 尚未完成，则必须能够从 committed seeding 结果重新派发 `affected_proposition_ids`
- runtime 不得依赖“本轮 worker 内存里还记得哪些 proposition 被影响”来驱动下游

### Input / Output Types

```ts
type SeedingRunInput = {
  session_id: string;
  trigger_finding_ids: string[];
  registry_version: string;
};

type SeedingRunResult = {
  created_proposition_ids: string[];
  existing_proposition_ids: string[];
  affected_proposition_ids: string[];
  schema_version: "finding_proposition_seeding_run.v1";
};
```

`trigger_finding_ids` 只是本轮受影响 finding 的入口，不意味着 template 只能读取这些 finding；对于 composite template，它仍可在同 session 的 committed canonical finding layer 内，通过显式 slot contract 解引用其他 required findings。

## Template Registry

### Canonical shape

```ts
type SeedTemplateSpec =
  | SingleFindingSeedTemplateSpec
  | CompositeSeedTemplateSpec;

type SeedTemplateBase = {
  template_id: string;
  template_version: string;
  derivation_version: string;
  proposition_type:
    | "change"
    | "decomposition"
    | "anomaly"
    | "correlation"
    | "test_hypothesis"
    | "forecast";
  assessment_type:
    | "change_assessment"
    | "decomposition_assessment"
    | "anomaly_assessment"
    | "correlation_assessment"
    | "test_hypothesis_assessment"
    | "forecast_assessment";
  schema_version: string;
};

type SingleFindingSeedTemplateSpec = SeedTemplateBase & {
  match_mode: "single_finding";
  trigger_finding_type:
    | "delta"
    | "decomposition_item"
    | "anomaly_candidate"
    | "correlation_result"
    | "test_result"
    | "forecast_point";
};

type CompositeSeedTemplateSpec = SeedTemplateBase & {
  match_mode: "composite";
  trigger_slot: string;
  slots: SeedSlotSpec[];
  group_key: string;
};

type SeedSlotSpec = {
  slot_name: string;
  finding_type:
    | "observation"
    | "delta"
    | "decomposition_item"
    | "anomaly_candidate"
    | "correlation_result"
    | "test_result"
    | "forecast_point";
  required: boolean;
  cardinality: "one" | "many";
  role: "primary" | "secondary" | "context";
  match_predicates: string[];
  sort_key: string;
};
```

### Registry invariants

- `template_id` 在 registry 内稳定唯一
- `template_version` 表达模板内容版本
- `derivation_version` 表达 proposition identity boundary 版本
- non-breaking template 升级允许仅 bump `template_version`
- breaking template 升级必须 bump `derivation_version`
- 同一 template version 下，field resolution / creation condition / output normalization 不得依赖实现层默认分支

## Matching Algorithm

### Phase 1. Trigger routing

对 `trigger_finding_ids` 按稳定顺序排序：

1. `finding_type` lexical order
2. `artifact_id`
3. `finding_id`

然后按 `finding_type` 路由到可用 template 集合。

v1 registry 固定：

- `observation`：无 system-seeded template
- `delta`：`seed.change_from_delta.v1`
- `decomposition_item`：`seed.decomposition_from_item.v1`
- `anomaly_candidate`：`seed.anomaly_from_candidate.v1`
- `correlation_result`：`seed.correlation_from_result.v1`
- `test_result`：`seed.test_hypothesis_from_result.v1`
- `forecast_point`：`seed.forecast_from_point.v1`

### Phase 2. Candidate binding

### Single-finding template

single-finding template 的 binding 固定为：

- `primary finding = trigger finding`
- 不允许再额外搜索未声明 slot 的 finding

### Composite template

composite template 必须通过显式 `group_key + slots` 匹配，不允许 ad-hoc graph walk。

固定规则：

- 必须先根据 template 的 `group_key` 为 trigger finding 计算稳定 group
- 其他 slot 只能从同 group 的 committed findings 中匹配
- slot 匹配谓词只能使用 machine-readable typed fields、typed refs 与 normalized subject/window keys
- 若 required slot 缺失，则该 candidate 不成立
- `cardinality = "one"` 且命中多个 finding 时，必须按 `sort_key` 稳定选主；若 template 未定义可选主规则，则视为 template contract violation
- `cardinality = "many"` 时，成员按 `sort_key` 稳定排序，并整体进入 field resolution

v1 支持 composite engine，但不要求存在默认启用的 composite template。

### Phase 3. Resolution context

一旦 candidate binding 成立，template 可读取：

- bound seed finding(s)
- seed finding typed refs 指向的 committed findings
- seed finding typed refs / lineage handles 指向的 committed artifacts
- seed finding `step_ref` 指向的 committed step/request metadata

template 不允许读取：

- 不在 binding 或显式 ref 内的任意 finding
- superseded assessment members 反推 proposition payload
- narrative text
- projection order / rank 除非 rank 是 finding payload 内的 canonical field

### Phase 4. Creation condition

template 必须先判断 creation condition。

固定规则：

- creation condition 只回答“当前是否有足够结构化语义创建 proposition”
- finding 质量差、样本不足、comparability 不足，不应在 seeding 阶段过滤掉 proposition；这些问题由 assessment 表达
- 若 proposition schema required field 无法从 resolution context 确定性解析，则 creation condition = false
- creation condition = false 时，本轮对该 candidate 不产生 proposition，也不写错误对象

### Phase 5. Proposition materialization

一旦 creation condition 成立，template 必须一次性产出完整 proposition payload：

- `proposition_type`
- `subject`
- `origin`
- `assessment_anchor`
- `lineage`
- `seed_finding_refs`
- subtype `payload`
- `schema_version`

### Subject rules

- 单侧 proposition 直接继承或确定性变换自 trigger finding subject
- 双侧 proposition 的 `left_subject` / `right_subject` 必须来自 source artifact / step metadata，而不是由 finding.subject 猜测
- 双侧 proposition 的 base `subject` 必须按稳定 focus-anchor 算法派生：
  - 先对 `left_subject` 与 `right_subject` 计算 canonical subject key
  - 取 lexical order 更小者作为 base `subject`
  - 相等时固定取 `left_subject`

### Origin rules

system-seeded proposition 固定写入：

```ts
origin = {
  kind: "system_seeded",
  template_id,
  template_version
}
```

### Assessment anchor rules

`assessment_anchor.assessment_type` 必须与 template 声明一致，不允许实现层按 proposition payload 重新猜。

### Lineage rules

固定写入：

```ts
lineage = {
  creation_mode: "seeded",
  source_artifact_lineages,
  source_step_refs,
  derived_from_proposition_ref: null,
  derivation_version
}
```

其中：

- `source_artifact_lineages` 必须包含所有参与 materialization 的 source artifacts
- `source_step_refs` 必须包含所有参与 materialization 的 source steps
- 两者都做稳定去重并按 lexical order 排序

### Seed ref rules

- `seed_finding_refs` 只包含 template slots 明确声明为 seed 的 finding
- 顺序固定为 slot declaration order；同 slot 多 finding 时按 slot `sort_key`
- single-finding template 至少包含 1 个 `primary` seed

### Phase 6. Identity normalization

system-seeded proposition 的 normalized identity object 固定包含：

- `session_id`
- `origin.kind`
- `proposition_type`
- `lineage.derivation_version`
- proposition payload 中定义为 judgment semantics 的字段
- proposition base / payload 中显式声明参与 judgment identity 的 stable typed refs

固定不包含：

- `template_id`
- `template_version`
- `schema_version`
- `created_at`
- `seed_finding_refs` 的顺序
- assessment status / confidence / supporting / opposing evidence

推荐计算方式：

1. 构造 normalized identity object
2. 进行 canonical JSON serialization
3. 生成 `proposition_id`

### Phase 7. Registration

registration 是 proposition registry 的唯一写入 owner。

固定规则：

- 若当前 identity 不存在，则新建 proposition
- 若当前 identity 已存在，则返回 registration hit
- 命中既有 proposition 时，不更新 proposition payload、本体 lineage 或 seed refs
- 无论 create 还是 hit，都必须把该 proposition 放入 `affected_proposition_ids`

推荐唯一键：

- `(session_id, proposition_id)`

运行时补充要求：

- registration commit 是 proposition registry truth 的唯一落点
- seeding stage 的下游 handoff truth 是稳定的 `affected_proposition_ids`，而不是“本轮新建了多少 proposition”
- create 与 hit 都必须进入 `affected_proposition_ids`，避免已有 proposition 因 registration hit 而跳过 recompute

### created_at semantics

- create：使用当前 proposition registration commit time
- hit：保留原 `created_at`

### Phase 8. Downstream handoff

seeding 完成后，必须把 `affected_proposition_ids` 交给 assessment recompute。

assessment 是否产生新 snapshot，由 inference/gap engine 决定；seeding 不提前裁剪。

## Replay, Upgrade, And Invalidation

### Replay

相同 committed finding snapshot + 相同 registry snapshot replay 时：

- candidate binding 保持稳定
- creation condition 结果保持稳定
- proposition_id 保持稳定
- create / hit 结果保持稳定
- `affected_proposition_ids` 保持稳定

### Template upgrade

#### Non-breaking upgrade

当 template upgrade 不改变 proposition identity boundary 时：

- bump `template_version`
- 保持 `derivation_version`
- replay 命中既有 proposition identity
- 不回写已存在 proposition 的 `origin.template_version`

#### Breaking upgrade

当 template upgrade 改变 proposition identity boundary 或 judgment semantics 时：

- 必须 bump `derivation_version`
- 可同时 bump `template_version`
- 旧 proposition 保留
- reseed / replay 产生新的 proposition identity

### Soft invalidation

当 seed finding 或其 lineage 当前不可解引用时：

- 历史 proposition 保留
- `seed_entries[*].finding = null` 在 context surface 暴露缺失
- assessment 通过 missing refs、gap reopen、membership 收缩表达影响
- seeding 不做硬删除，不回收旧 proposition

## v1 Template Catalog

### T0. `observation` 不直接 seed proposition

固定规则：

- `observation` finding 默认不注册 system-seeded proposition
- 若未来需要 observation-based proposition，必须新增独立 template，不得在现有 template 中隐式旁路

### T1. `seed.change_from_delta.v1`

### 输入

- trigger finding type：`delta`

### 输出

- proposition type：`change`
- assessment type：`change_assessment`
- `derivation_version = "seed.change_from_delta.identity.v1"`

### Resolution

- `subject <- finding.subject`，并把 `analysis_axis` 规范化为 `"change"`
- `change_kind`：
  - `scalar_delta -> scalar_change`
  - `segmented_delta -> segment_change`
  - `time_series_delta ->` 不创建 change proposition（v1 change proposition 限定为 scalar/segment 变化；时序桶级 delta 不 seed proposition）
- `comparison_window`：从 source compare artifact 的 left/right resolved window 确定性解析
- `comparison_basis`：
  - 若 source compare contract 显式声明 baseline 语义，则 `current_vs_baseline`
  - 若显式声明 peer 语义，则 `peer_vs_peer`
  - 其他情况固定 `left_vs_right`
- `unit <- finding.payload.unit`
- `dimension_keys`：
  - `scalar_delta -> null`
  - `segmented_delta ->` 由 source row key / source segmented item keys 解析
- `direction_of_interest`：
  - `increase -> increase`
  - `decrease -> decrease`
  - `undefined + presence in {current_only, baseline_only} -> any_non_flat`

### Creation condition

仅当以下条件同时满足时创建 proposition：

- `comparison_window.left/right` 可解析
- `direction_of_interest` 可解析
- `segmented_delta` 时 `dimension_keys` 可解析

以下情况不创建 proposition：

- `direction = flat`
- `direction = undefined` 且 `presence ∉ {current_only, baseline_only}`
- required compare windows 无法解析

### Seed refs

```ts
[
  { finding_ref: trigger_finding_ref, role: "primary" }
]
```

### T2. `seed.decomposition_from_item.v1`

### 输入

- trigger finding type：`decomposition_item`

### 输出

- proposition type：`decomposition`
- assessment type：`decomposition_assessment`
- `derivation_version = "seed.decomposition_from_item.identity.v1"`

### Resolution

- `subject <- finding.subject`，并把 `analysis_axis` 规范化为 `"decomposition"`
- `dimension <- finding.payload.dimension`
- `dimension_keys <- finding.payload.keys`
- `scope_delta_ref <- finding.payload.scope_delta_ref`
- `comparison_window`：通过 `scope_delta_ref -> delta finding -> source compare artifact` 解析
- `contribution_role`：
  - 若 `scope_delta_ref.direction` 与 `finding.payload.direction` 方向相反，则 `offsetting_factor`
  - 若方向相同且 `rank = 1`，则 `primary_driver`
  - 若方向相同且 `rank > 1`，则 `secondary_driver`
  - 若方向不可比较但 `contribution_value != 0` 或 `contribution_share != 0`，则 `material_component`

### Creation condition

仅当以下条件同时满足时创建 proposition：

- `scope_delta_ref` 可解引用到 committed `delta` finding
- `comparison_window.left/right` 可解析
- `dimension_keys` 非空 map
- `contribution_role` 可解析

以下情况不创建 proposition：

- `contribution_value = 0` 且 `contribution_share = 0`
- `contribution_value = null` 且 `contribution_share = null`

### Seed refs

```ts
[
  { finding_ref: trigger_finding_ref, role: "primary" },
  { finding_ref: scope_delta_ref, role: "context" }
]
```

### T3. `seed.anomaly_from_candidate.v1`

### 输入

- trigger finding type：`anomaly_candidate`

### 输出

- proposition type：`anomaly`
- assessment type：`anomaly_assessment`
- `derivation_version = "seed.anomaly_from_candidate.identity.v1"`

### Resolution

- `subject <- finding.subject`，并把 `analysis_axis` 规范化为 `"anomaly"`
- `candidate_ref <- finding.payload.candidate_ref`
- `observed_window <- finding.observed_window`
- `anomaly_kind <- "candidate"`
- `expected_behavior_ref <- null`
- `validation_goal <- "validate_candidate"`

### Creation condition

仅当以下条件同时满足时创建 proposition：

- `candidate_ref` 形状合法且可解引用
- `observed_window != null`

### Seed refs

```ts
[
  { finding_ref: trigger_finding_ref, role: "primary" }
]
```

### T4. `seed.correlation_from_result.v1`

### 输入

- trigger finding type：`correlation_result`

### 输出

- proposition type：`correlation`
- assessment type：`correlation_assessment`
- `derivation_version = "seed.correlation_from_result.identity.v1"`

### Resolution

- `left_subject` / `right_subject`：从 source correlate artifact / step metadata 中解析
- `subject`：按本文定义的双侧 focus-anchor 算法稳定派生
- `method_family <- finding.payload.method`
- `relationship_of_interest`：
  - `coefficient > 0 -> positive_association`
  - `coefficient < 0 -> negative_association`
  - 其他情况 -> `any_association`
- `join_basis`：
  - 若 source artifact 已提供结构化 join basis，则直接使用
  - 否则仅允许从 `finding.payload.join_basis` 解析为 `CorrelationJoinBasis`
  - 无法解析则 creation condition = false
- `aligned_window`：从 source correlate artifact 的对齐窗口元数据解析

### Creation condition

仅当以下条件同时满足时创建 proposition：

- `left_subject` / `right_subject` 可解析
- `join_basis` 可解析为结构化 `CorrelationJoinBasis`
- `aligned_window` 可解析

### Seed refs

```ts
[
  { finding_ref: trigger_finding_ref, role: "primary" }
]
```

### T5. `seed.test_hypothesis_from_result.v1`

### 输入

- trigger finding type：`test_result`

### 输出

- proposition type：`test_hypothesis`
- assessment type：`test_hypothesis_assessment`
- `derivation_version = "seed.test_hypothesis_from_result.identity.v1"`

### Resolution

- `left_subject` / `right_subject`：从 source test artifact / step metadata 中解析
- `subject`：按双侧 focus-anchor 算法稳定派生
- `hypothesis_family <- "difference"`
- `alternative`：
  - 若 source test contract 显式声明 alternative，则直接使用
  - 否则固定 `two_sided`
- `method_family <- finding.payload.method`
- `alpha <- finding.payload.alpha`
- `hypothesis_label <- null`

### Creation condition

仅当以下条件同时满足时创建 proposition：

- `left_subject` / `right_subject` 可解析
- `alpha` 形状合法

### Seed refs

```ts
[
  { finding_ref: trigger_finding_ref, role: "primary" }
]
```

### T6. `seed.forecast_from_point.v1`

### 输入

- trigger finding type：`forecast_point`

### 输出

- proposition type：`forecast`
- assessment type：`forecast_assessment`
- `derivation_version = "seed.forecast_from_point.identity.v1"`

### Resolution

- `subject <- finding.subject`，并把 `analysis_axis` 规范化为 `"forecast"`
- `forecast_kind`：
  - `prediction_interval != null -> interval_forecast`
  - `prediction_interval = null -> point_forecast`
- `forecast_window <- { kind: "range", start: bucket_start, end: bucket_end }`
- `horizon_index <- finding.payload.horizon_index`
- `expectation_direction <- "open"`
- `forecast_basis_ref <- trigger_finding_ref`

### Creation condition

仅当以下条件同时满足时创建 proposition：

- `bucket_start` / `bucket_end` 可解析为合法 window
- `horizon_index` 合法

### Seed refs

```ts
[
  { finding_ref: trigger_finding_ref, role: "primary" }
]
```

## Validation Checklist

实现至少必须满足以下验收场景：

1. 同一 finding replay、同一 template registry snapshot 下，产生同一 proposition identity。
2. `system_seeded` 与 `agent_authored` 对同一 payload 语义不共享 proposition identity。
3. single-finding template 命中既有 proposition 时，不回写 `seed_finding_refs`。
4. `delta(flat)` 不注册 `change proposition`。
5. `decomposition_item` 缺少可解引用 `scope_delta_ref` 时，不注册 proposition。
6. `correlation_result` 若只有字符串 `join_basis` 且无法解析为结构化 join basis，不注册 proposition。
7. `forecast_point` 对同一 future bucket replay 时命中既有 proposition。
8. breaking template upgrade bump `derivation_version` 后，reseed 产生新 proposition，而旧 proposition 保留。
9. soft invalidation 不硬删 proposition；context surface 通过 `seed_entries[*].finding = null` 暴露缺失。
10. 相同 committed finding snapshot 下，多次 seeding run 的 `affected_proposition_ids` 集合稳定。

## Related Documents

- [`runtime-pipeline.md`](runtime-pipeline.md)
- [`artifact-finding-generation-rules.md`](artifact-finding-generation-rules.md)
- [`inference-and-gap-engine.md`](inference-and-gap-engine.md)
- [`graph-and-reference-semantics.md`](graph-and-reference-semantics.md)
- [`schemas/finding.md`](schemas/finding.md)
- [`schemas/proposition.md`](schemas/proposition.md)
