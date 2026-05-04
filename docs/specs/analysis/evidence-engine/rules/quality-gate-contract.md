# 质量门槛契约

本文档定义 Marivo 推断规则引擎中 `quality_gate` 规则族的拟议契约。

状态：draft design。本文是 `specs/analysis/` 下的规范 family-level 设计提案，不表示对应实现、持久化模型或 HTTP endpoint 已经存在。

## 目的

[`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 已定义 `quality_gate` 属于固定评估顺序中的门槛族，并约束其不能越权写最终判断。

本文补足的是 `quality_gate` 自身的 family-level 契约，回答以下问题：

- `quality_gate` 允许读取哪些规范输入
- 哪些质量门槛属于它的判断范围
- 它如何把结果稳定映射到 `InferenceRecord` 与 `EvidenceGap`
- 哪些条件 token 必须结构化写入 `matched_conditions` / `unmatched_conditions`
- 它如何与 `status_resolution`、`confidence_shaping`、注册表、缺口生命周期与仅变化快照策略对齐

其中 gap identity convergence、`open / keep / resolve / reopen` 生命周期，以及 snapshot-owned `blocking` / `severity` classification 的总规则，以 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 为准；本文只定义 `quality_gate` 如何贡献 requirement-level candidates、gap ids 与结构化质量影响。

本文不负责：

- 新增或改写规则族
- 定义评估特定的判断门槛
- 枚举每个 `assessment_type` 的完整质量要求目录
- 定义规则实现代码组织方式

## Non-goals

本文不定义：

- 最终 `Assessment.status` 的判断策略
- `comparability_gate` 的门槛
- 评估特定质量阈值配置的完整枚举
- 规则加载方式、执行器实现、存储表结构或对外 HTTP 契约

## 核心设计决策

### 1. `quality_gate` 是既有 gate family，不是新增阶段

`quality_gate` 已是 v1 固定 `rule_family` 之一。

本文只细化该 family 的 schema contract，不新增新的 family、cluster 层级或执行顺序。

### 2. 输入边界固定为单 proposition canonical evaluation context

`quality_gate` 只能读取当前 target proposition 的 canonical evaluation context：

- target `proposition`
- 当前 proposition closure 中可解引用的 `findings`
- 同一 proposition 的 `prior_assessments`
- 同一 proposition 当前仍为 `open` 的 `EvidenceGap`

不允许读取：

- 其他 proposition 或其他 proposition 的 assessment / gap
- projection summary、自由文本 evidence description
- 临时评分对象、黑盒模型输出或 out-of-band telemetry

### 3. 职责是判断质量门槛，不是决定最终结论或最终置信度

`quality_gate` 负责回答的问题只有：

- 当前可用 findings 是否满足进入后续 judgment 的最低质量门槛
- 哪些质量门槛已经满足
- 哪些质量门槛失败或仅部分满足
- 哪些质量失败需要被 materialize 成可追溯 gap
- 哪些结构化质量影响需要交给后续 family 消费

`quality_gate` 不负责：

- 决定最终 `Assessment.status`
- 决定 support / oppose membership
- 直接写最终 `confidence_grade`
- 通过未结构化说明隐式解决 gap

### 4. family 关注四类质量维度

v1 中 `quality_gate` 允许判断的质量维度固定为 canonical schema 已声明的四类：

1. `data_complete`
2. `sample_size`
3. `quality_status`
4. `null_rate`

其中：

- `data_complete` 用于回答当前 finding 是否存在已知完整性缺口
- `sample_size` 用于回答样本量是否满足最低可消费门槛
- `quality_status` 用于回答 finding 是否处于 `ready | needs_attention | not_ready`
- `null_rate` 用于回答缺失率是否超过既定可消费阈值

若某项条件本质上是 required finding family、subject coverage、time coverage 前提，应进入 `precondition_gate`；若本质上是左右窗口、切片、grain、方法前提之间的双边可比性问题，应进入 `comparability_gate`。

对 calendar alignment 的固定边界：

- 单边窗口的 calendar snapshot 缺失、annotation 不完整、sample summary 不可消费，属于 `quality_gate` 或更早阶段失败。
- 不得把 `holiday_cluster_unmapped`、`event_cluster_unmapped`、`fallback_applied`、`alignment_coverage_insufficient`、`weekday_pairing_tie` 这类双边对齐问题降格为 quality issues。
- 成功的 compare/test artifact 若携带 calendar alignment issues，这些 issues 必须全部归属于 `comparability_gate`，而不是在 quality 与 comparability 两侧重复出现。

## Schema Position

`quality_gate` 位于固定 evaluation order 的 gate 阶段，并满足：

- 在 `precondition_gate` 之后运行
- 在 `comparability_gate`、`support_evidence`、`oppose_evidence`、`status_resolution` 之前运行
- 只向后续 family 暴露结构化质量状态，不形成逆序依赖

## Typed Design Sketch

以下类型仅用于说明 family-level contract，不替代 canonical schema。

```ts
type QualityDimension =
  | "data_complete"
  | "sample_size"
  | "quality_status"
  | "null_rate";

type QualityRequirementRef = {
  requirement_type: "quality_threshold";
  requirement_key: string;
  quality_dimension: QualityDimension;
};

type QualityImpactLevel =
  | "none"
  | "limited"
  | "material"
  | "severe";

type QualityEvaluationResult = {
  rule_id: string;
  result: "hit" | "miss" | "partial";
  matched_condition_tokens: string[];
  unmatched_condition_tokens: string[];
  opened_gap_refs: QualityRequirementRef[];
  resolved_gap_refs: QualityRequirementRef[];
  emitted_quality_impacts: QualityImpactLevel[];
};
```

## Input Contract

### proposition 输入

`quality_gate` 必须从 target proposition 读取：

- `proposition_id`
- `assessment_anchor`
- canonical subject 信息
- seed finding refs

它不得改写 proposition identity，也不得发明额外的判断锚点。

### finding 输入

`quality_gate` 只能基于 canonical finding identity 与结构化 finding payload 判断质量门槛是否满足。

要求：

- 输入 finding 必须已提交到 canonical finding layer
- 读取必须依赖稳定 `finding_id`
- 质量判断只消费结构化质量字段，不以自由文本 warning 作为唯一主语义
- finding 的 `null` 语义必须复用 [`finding.md`](../schemas/finding.md) 与 [`observe.md`](../../intents/atomic/observe.md) 中已声明的单义约束，不得由 `quality_gate` 重新解释

### prior assessment 与 open gap 输入

`prior_assessments` 与 `open_gaps` 仅用于：

- 判断某项 quality gap 是否继续保持
- 判断某项旧 quality gap 是否在本次被显式解决
- 为后续 family 提供稳定的质量风险状态

它们不能被用来继承旧结论，也不能把历史上曾经满足的质量门槛当作当前 finding 质量仍然满足的替代品。

## Requirement Mapping Contract

### 1. gap 与 requirement 类型固定复用现有 canonical schema

当 `quality_gate` 需要 materialize 风险时：

- gap 必须映射到 `gap_kind = "data_quality_risk"`
- `missing_requirement.requirement_type` 必须为 `"quality_threshold"`
- `requirement_key` 必须稳定标识该 quality requirement semantics

本文不新增新的 `gap_kind` 或新的 requirement type。

### 2. `quality_dimension` 只能复用既有四项维度

`requirement_params.quality_dimension` 只能是：

- `data_complete`
- `sample_size`
- `quality_status`
- `null_rate`

要求：

- 不得把 comparability failure 伪装成质量 requirement
- 不得把 coverage 缺口伪装成质量 requirement
- `threshold_operator` 与 `threshold_value` 必须能稳定重放，不依赖实现层临时解释

### 3. gap identity 必须绑定 proposition 与 requirement semantics

quality gap identity 必须绑定：

- proposition
- `requirement_key`
- `quality_dimension`
- 稳定 threshold semantics

不允许把一次运行实例、临时 notes 或展示文案作为 gap identity 的组成部分。

### 4. `requirement_key` 采用稳定语义命名，不默认内嵌阈值字面值

`requirement_key` 应优先表达“这是哪一个稳定质量 requirement”，而不是直接复制实现参数。

推荐约束：

- 使用小写 ASCII `snake_case`
- 优先表达 requirement 语义或消费场景，而不是目录名、类名或临时文案
- 阈值具体数值默认放在 `threshold_operator` / `threshold_value` 中，而不是重复塞进 `requirement_key`
- 只有当同一 proposition 下需要并存多个同维度 requirement，且仅靠维度名无法区分语义时，才允许把稳定限定词并入 key

示例：

- 推荐：`min_sample_size_support_eval`
- 推荐：`max_null_rate_primary_series`
- 推荐：`quality_status_ready_for_support`
- 不推荐：`min-sample-size-100`
- 不推荐：`ruleA_check_01`

## Record Mapping Contract

### `result` 判定

`quality_gate` 的 `InferenceRecord.result` 约束如下：

- `hit`：当前 rule 所要求的质量门槛全部满足，且该通过对当前 snapshot 的 gap resolve、rule coverage 或质量影响解释有价值
- `miss`：核心质量门槛失败，足以解释保守降级、gap 保持或不得把当前 finding 当作无保留硬证据
- `partial`：质量门槛部分满足，finding 仍可被有限消费，但必须保留结构化 quality caveat

更细规则：

- 当 finding `quality_status = not_ready`，或其他核心质量 requirement 失败且当前 finding 不应继续作为无保留判断输入时，应产出 `miss`
- 当 finding 仍可消费，但存在 `needs_attention`、边界样本量、边界 null risk 或部分完整性 caveat，且这些 caveat 需要被后续 family 消费时，应产出 `partial`
- `partial` 不表示“几乎等于通过”；它表示“可以有限消费，但必须保留结构化质量约束”
- 同一 `quality_gate` family 下，多条 rule 可以在一次 recompute 中分别产出 `partial`；canonical 读取面应保留逐条 `rule_id` 审计
- downstream family 不直接消费自由文本；它们只消费 `result`、opened / resolved gaps、condition tokens 与结构化质量影响

示例边界：

- `partial`：`quality_status = needs_attention`，但样本量与 null rate 均在可消费范围内；该 finding 仍可进入 evidence aggregation，但后续 family 必须保留质量 caveat
- `partial`：`data_complete = false` 仅表示某些非核心补充字段缺失，而 proposition 当前依赖的核心度量仍完整可读；此时允许有限消费，但应暴露质量限制
- `miss`：`quality_status = not_ready`，调用方不应把该 finding 当作无保留硬证据
- `miss`：当前 proposition 的核心判断依赖最小样本量，但 `sample_size` 明确低于 requirement，导致该 finding 不应继续作为该 judgment track 的有效输入

要求：

- 不允许只在 `hit` 时写 record，而忽略有解释价值的 `miss` / `partial`
- 仅凭 `quality_gate` 的 `hit` 不得直接推出高 confidence 的单向强结论

### gap 字段

`quality_gate` 打开或解决 gap 时，必须显式通过 record 驱动：

- 新打开的 gap 进入 `opened_gap_ids`
- 已满足原 requirement 的旧 gap 进入 `resolved_gap_ids`

解决归属规则：

- gap 是否解决，由当前 recompute 中命中该 requirement semantics 的 rule 决定；它不要求必须是“当初打开该 gap 的同一条 rule”，但必须属于同一 proposition 的 `quality_gate` family，并能稳定指向同一个 `requirement_key`
- 若某个 quality gap 被解决，但其他质量 requirement 仍失败，则只解决已满足的那一个 gap；当前 assessment 仍可因剩余质量风险保持保守输出
- 若同一 recompute 中有多条 rule 指向同一个 quality requirement semantics，它们可以分别产出 record，但 candidate gap membership 必须按 gap identity 做集合归并；不得因 rule 顺序产生不同结果
- 若多条 rule 对同一 gap identity 给出相互冲突的 open / resolve 候选，实现必须先按同一 `requirement_key` 的 canonical requirement semantics 收敛后再 materialize，不能采用“最后一条规则覆盖前一条”的隐式优先级

不允许：

- 仅因“这次没再提旧 gap”就视为已解决
- 同一 gap 在同一 snapshot 内同时出现在 open 与 resolve 两个集合

### quality impact 字段

`quality_gate` 可以产出结构化质量影响，但不直接写最终 `confidence_grade`。

family-level 要求：

- 质量影响只作为 downstream 输入，供 `status_resolution` 与 `confidence_shaping` 消费
- 推荐 impact 语义与 `Assessment.confidence_rationale.data_quality_impact` 对齐为 `none | limited | material | severe`
- `quality_gate` 可声明“当前强结论不得无保留维持”的 guardrail 语义，但最终 status 仍由 `status_resolution` 决议

建议的定性边界：

- `none`：未观察到会影响当前判断消费方式的质量 caveat
- `limited`：存在轻微 caveat，需要暴露但通常不改变当前 evidence 的主消费路径
- `material`：质量问题已明显限制 evidence 的可解释性或稳定性，后续 family 应保守降级或显著压低 confidence
- `severe`：质量问题已阻断当前 finding 的安全消费，或足以触发强结论撤回 / gap blocking

本文只固定这些 level 的语义边界，不在通用 contract 中定义跨 assessment type 的统一数值阈值。

### status transition 与 confidence grade 字段

`quality_gate` 不是最终状态决议 family，也不是最终 confidence 塑形 family。

因此：

- 默认不应单独写 `produced_status_transition`
- 不应直接写最终 `confidence_grade`
- `quality_gate` 的贡献应通过 `result`、gap 字段、condition tokens 与结构化质量影响被后续 family 消费

## Condition Token Contract

### 目标

`matched_conditions` 与 `unmatched_conditions` 必须承载 `quality_gate` 的主语义，做到：

- 稳定可比较
- 可被审计
- 可被 replay / compatibility 检查复用
- 可追溯回具体 requirement semantics

`notes` 只用于少量补充说明，不承载唯一主语义。

### token 命名规范

v1 先固定 token 组织原则，不在本文枚举所有 assessment-specific token。

token 应满足：

- 表达质量 requirement 语义，而不是实现细节
- 可稳定对应到 `requirement_key`
- 不依赖目录名、类名、临时字符串拼接策略

推荐形态：

- quality threshold：`quality_threshold:<requirement_key>:met|breached|degraded`
- quality signal：`quality_signal:<dimension>:ready|needs_attention|not_ready`

示例：

```yaml
matched_conditions:
  - "quality_threshold:min_sample_size:met"
  - "quality_signal:quality_status:needs_attention"

unmatched_conditions:
  - "quality_threshold:max_null_rate:breached"
```

要求：

- `matched_conditions` 只写满足的稳定 token
- `unmatched_conditions` 只写失败、缺失或降级的稳定 token
- 若某项 quality requirement 被 materialize 为 gap，相关 token 必须能回指同一 `requirement_key`

assessment-specific profile 后续若要新增 quality requirement catalog，必须复用这套 token 语义，而不是为每条 rule 自发明自由文本。

## Downstream Consumption Contract

### `status_resolution`

`status_resolution` 只能消费 `quality_gate` 的结构化输出，不消费自由文本解释。

它可使用的输入包括：

- `result`
- `opened_gap_ids` / `resolved_gap_ids`
- `matched_conditions` / `unmatched_conditions`
- 结构化质量影响

用途包括：

- 判断当前 quality failure 是否要求保守回到 `insufficient`
- 判断 blocking 与 non-blocking quality gap 是否影响最终 status 收敛

### `confidence_shaping`

`confidence_shaping` 负责把 `quality_gate` 的结构化质量影响 materialize 到：

- `confidence_rationale.data_quality_impact`
- 最终 `confidence_grade`

要求：

- `quality_gate` 不直接写最终 `confidence_grade`
- `confidence_shaping` 必须复用 [`assessment.md`](../schemas/assessment.md) 与 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 已声明的全局 guardrails
- 当 `quality_gate` 输出 `severe` impact 时，最终 `confidence_grade` 不得高于 `low`

### `gap_management`

`gap_management` 负责最终 open / keep / resolve 的稳定 materialization，但 quality gap 的打开与解决必须由 `quality_gate` record 驱动，不得 out-of-band 写入。

## Registry Contract

`quality_gate` 的 family 归属仍完全依赖 [`rule-registry-contract.md`](rule-registry-contract.md)：

- `InferenceRecord` 不新增 `rule_family` 字段
- `rule_id -> rule_family -> assessment_type` 必须由 registry 显式解引用
- 读取面不得通过字符串前缀、目录路径或类名推断 family

当某条 rule 注册到 `quality_gate` 时，至少应满足：

- `rule_family = "quality_gate"`
- `assessment_type` 明确
- `rule_cluster` 仅用于更细业务分组，不改变 family 语义
- `rule_version` 变更时，不得破坏已持久化 token / gap 语义的可解释性

安全演进要求：

- 若仅新增 requirement key 或 token，且不改变既有 token 的既有语义，可视为 backward-compatible 变更
- 若某个 token 将废弃，应至少保留一个兼容窗口，保证旧 token 仍可被 replay / 审计解释
- 若必须改变既有 token 的语义或 requirement semantics，应视为 breaking change，并伴随新的 `rule_version`

## Snapshot And Lifecycle Constraints

`quality_gate` 必须兼容 change-only snapshot policy：

- 相同 canonical inputs 重算，不得仅因 token 顺序、说明文本或实现细节变化制造新 snapshot
- canonical outcome 未变时，本轮 candidate records 必须整体丢弃
- 若 quality gap membership 改变，即使 status 不变，也必须允许形成新 snapshot
- 若 finding 当前退化为不满足既有质量门槛，必须允许 downgrade 或 gap reopen

`quality_gate` 不得假设状态只会单调增强。

对 `quality_gate` 而言，canonical outcome 至少包括：

- 每条 rule 的 `result`
- `matched_conditions` / `unmatched_conditions` 的成员集合
- `opened_gap_ids` / `resolved_gap_ids` 的成员集合
- 结构化质量影响语义集合
- 是否因此改变了当前 candidate assessment 的 blocking / non-blocking quality gap membership

不应进入 canonical equality 判断的内容包括：

- `notes` 的措辞
- record / gap 的展示顺序
- 时间戳或其他非 judgment 语义的运行时元数据

## Acceptance Scenarios

1. proposition 首次进入评估，某个 finding `quality_status = not_ready` 时，`quality_gate` 写入 `miss` record，并打开 blocking `data_quality_risk` gap。
2. finding 可消费但存在 `needs_attention` 或边界质量 caveat 时，`quality_gate` 写入 `partial` record，并保留结构化 quality token。
3. `sample_size` 或 `null_rate` 未达 requirement 时，gap 能稳定追溯到 `quality_threshold` requirement，而不是依赖自由文本描述。
4. 某条旧 `data_quality_risk` gap 在新 finding 满足同一 requirement semantics 后，本轮必须通过 `resolved_gap_ids` 显式解决该 gap。
5. 相同 canonical inputs 重算时，condition token、gap identity、record result 与结构化质量影响保持稳定，不制造额外 snapshot。
6. `quality_gate` 全部命中通过时，系统仍需经过 evidence aggregation、`status_resolution` 与 `confidence_shaping` 才能形成最终 judgment 与 confidence。
7. 新 quality failure 到达后，即使 prior assessment 曾为强结论，当前 recompute 仍必须允许 downgrade 到更保守状态，或在 status 不变时单独改变 gap / `confidence_shaping` 产出的 confidence。

## 与其他文档的关系

- [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 定义 rule family 的固定 evaluation order、通用职责边界与 `InferenceRecord` 写入总规则
- [`assessment.md`](../schemas/assessment.md) 定义 `EvidenceGap`、`InferenceRecord`、`confidence_rationale` 的 canonical schema
- [`finding.md`](../schemas/finding.md) 与 [`observe.md`](../../intents/atomic/observe.md) 定义 quality fields 的单义语义
- [`rule-registry-contract.md`](rule-registry-contract.md) 定义 `rule_id -> rule_family -> assessment_type` 的稳定解引用
- [`assessment-judgment-policy.md`](assessment-judgment-policy.md) 定义不同 `assessment_type` 的最终 judgment policy
- [`rule-family-design-checklist.md`](rule-family-design-checklist.md) 提供 family-level 设计评审 checklist
