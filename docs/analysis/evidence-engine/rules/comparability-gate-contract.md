# 可比性门槛契约

本文档定义 Factum 推断规则引擎中 `comparability_gate` 规则族的拟议契约。

状态：draft design。本文是 `docs/analysis/` 下的规范 family-level 设计提案，不表示对应实现、持久化模型或 HTTP endpoint 已经存在。

## 目的

[`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 已定义 `comparability_gate` 属于固定评估顺序中的门槛族，并约束其不能越权写最终判断。

本文补足的是 `comparability_gate` 自身的 family-level 契约，回答以下问题：

- `comparability_gate` 允许读取哪些规范输入
- 哪些可比性前提属于它的判断范围
- 它如何把结果稳定映射到 `InferenceRecord` 与 `EvidenceGap`
- 哪些条件 token 必须结构化写入 `matched_conditions` / `unmatched_conditions`
- 它如何与 `status_resolution`、`confidence_shaping`、注册表、缺口生命周期与仅变化快照策略对齐

其中 gap identity convergence、`open / keep / resolve / reopen` 生命周期，以及 snapshot-owned `blocking` / `severity` classification 的总规则，以 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 为准；本文只定义 `comparability_gate` 如何贡献 requirement-level candidates、gap ids 与结构化 comparability impact。

本文不负责：

- 新增或改写规则族
- 定义评估特定的判断门槛
- 枚举每个 `assessment_type` 的完整可比性要求目录
- 定义规则实现代码组织方式

## Non-goals

本文不定义：

- 最终 `Assessment.status` 的判断策略
- 完整 compare / test 工件 schema
- 评估特定可比性配置的完整枚举
- 规则加载方式、执行器实现、存储表结构或对外 HTTP 契约

## 核心设计决策

### 1. `comparability_gate` 是既有 gate family，不是新增阶段

`comparability_gate` 已是 v1 固定 `rule_family` 之一。

本文只细化该 family 的 schema contract，不新增新的 family、cluster 层级或执行顺序。

### 2. 输入边界固定为单 proposition canonical evaluation context

`comparability_gate` 只能读取当前 target proposition 的 canonical evaluation context：

- target `proposition`
- 当前 proposition closure 中可解引用的 `findings`
- 同一 proposition 的 `prior_assessments`
- 同一 proposition 当前仍为 `open` 的 `EvidenceGap`

不允许读取：

- 其他 proposition 或其他 proposition 的 assessment / gap
- projection summary、自由文本 evidence description
- 临时评分对象、黑盒模型输出或 out-of-band telemetry

### 3. 职责是判断双边可比性，不是决定最终结论或最终置信度

`comparability_gate` 负责回答的问题只有：

- 当前可用 findings 是否满足进入后续 judgment 的最低可比性前提
- 哪些可比性 requirement 已满足
- 哪些 requirement 失败或仅部分满足
- 哪些 comparability failure 需要被 materialize 成可追溯 gap
- 哪些结构化 comparability impact 需要交给后续 family 消费

`comparability_gate` 不负责：

- 决定最终 `Assessment.status`
- 决定 support / oppose membership
- 直接写最终 `confidence_grade`
- 通过未结构化说明隐式解决 gap

### 4. family 关注五类 comparability 维度

v1 中 `comparability_gate` 允许判断的 comparability 维度固定为：

1. `window_alignment`
2. `subject_alignment`
3. `slice_alignment`
4. `grain_alignment`
5. `method_precondition`

其中：

- `window_alignment` 用于回答左右窗口、时间语义与 baseline/current 定义是否可稳定比较
- `subject_alignment` 用于回答左右输入的 metric、unit、aggregation semantics 或显式 cross-group comparable 关系是否兼容
- `slice_alignment` 用于回答除目标比较轴之外，其余 scope / segment 定义是否兼容
- `grain_alignment` 用于回答左右输入的时间粒度、segment schema 或 pairing granularity 是否兼容
- `method_precondition` 用于回答具体 compare / correlate / test 方法的结构化前提是否成立

若某项条件本质上是 required finding family、subject coverage、time coverage 前提，应进入 `precondition_gate`；若本质上是数据完整性、样本量、quality status 或 null risk，应进入 `quality_gate`。

边界说明：

- 单边 finding 的可消费性属于 `quality_gate`
- 双边输入之间的关系兼容性属于 `comparability_gate`
- 若单边质量缺陷先导致输入不可安全消费，应先由 `quality_gate` 暴露该限制；`comparability_gate` 只表达剩余仍需判断的双边 comparability risk

### 5. 服务范围固定为需要双边比较语义的 assessment type

v1 中 `comparability_gate` 只服务以下 `assessment_type`：

- `change_assessment`
- `decomposition_assessment`
- `correlation_assessment`
- `test_hypothesis_assessment`

`anomaly_assessment` 与 `forecast_assessment` 不默认复用本 family；若未来需要，应先更新 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 中的映射，再扩展本 family contract。

原因：

- v1 先把 `comparability_gate` 收敛在显式双边比较语义稳定的 judgment track
- 若后续 anomaly / forecast 引入稳定的 baseline-vs-target 或 horizon-vs-reference comparability semantics，应通过更新固定映射显式扩展，而不是在实现层隐式复用

## Schema Position

`comparability_gate` 位于固定 evaluation order 的 gate 阶段，并满足：

- 在 `precondition_gate`、`quality_gate` 之后运行
- 在 `support_evidence`、`oppose_evidence`、`status_resolution` 之前运行
- 只向后续 family 暴露结构化 comparability 状态，不形成逆序依赖

## Typed Design Sketch

以下类型仅用于说明 family-level contract，不替代 canonical schema。

```ts
type ComparabilityDimension =
  | "window_alignment"
  | "subject_alignment"
  | "slice_alignment"
  | "grain_alignment"
  | "method_precondition";

type ComparabilityRequirementRef = {
  requirement_type: "comparability_requirement";
  requirement_key: string;
  comparability_dimension: ComparabilityDimension;
};

type ComparabilityImpactLevel =
  | "none"
  | "limited"
  | "material"
  | "severe";

type ComparabilityEvaluationResult = {
  rule_id: string;
  result: "hit" | "miss" | "partial";
  matched_condition_tokens: string[];
  unmatched_condition_tokens: string[];
  opened_gap_refs: ComparabilityRequirementRef[];
  resolved_gap_refs: ComparabilityRequirementRef[];
  emitted_comparability_impacts: ComparabilityImpactLevel[];
};
```

## Input Contract

### proposition 输入

`comparability_gate` 必须从 target proposition 读取：

- `proposition_id`
- `assessment_anchor`
- canonical subject 信息
- seed finding refs

它不得改写 proposition identity，也不得发明额外的判断锚点。

### finding 输入

`comparability_gate` 只能基于 canonical finding identity 与结构化 finding payload 判断 comparability requirement 是否满足。

要求：

- 输入 finding 必须已提交到 canonical finding layer
- 读取必须依赖稳定 `finding_id`
- 双边 comparability 只消费结构化字段，不以自由文本 warning 作为唯一主语义
- compare / test / correlate 等原子或派生 artifact 中既有的 comparability 语义，只能被读取和复用，不得在本 family 中被临时重定义

来源约束：

- `comparability_gate` 可以读取上游 artifact 已经产出的结构化 comparability signals
- `comparability_gate` 也可以基于 canonical finding payload 独立判断当前 requirement 是否满足
- 无论采用哪种来源，都不得重定义 [`compare.md`](../../intents/atomic/compare.md) 或 [`test.md`](../../intents/atomic/test.md) 中已声明的 canonical comparability schema

### prior assessment 与 open gap 输入

`prior_assessments` 与 `open_gaps` 仅用于：

- 判断某项 comparability gap 是否继续保持
- 判断某项旧 comparability gap 是否在本次被显式解决
- 为后续 family 提供稳定的 comparability risk 状态

它们不能被用来继承旧结论，也不能把历史上曾经满足的 comparability 前提当作当前输入仍然可比的替代品。

## Requirement Mapping Contract

### 1. gap 与 requirement 类型固定复用现有 canonical schema

当 `comparability_gate` 需要 materialize 风险时：

- gap 必须映射到 `gap_kind = "comparability_risk"`
- `missing_requirement.requirement_type` 必须为 `"comparability_requirement"`
- `requirement_key` 必须稳定标识该 comparability requirement semantics

本文不新增新的 `gap_kind`。

### 2. `comparability_dimension` 只能复用既有五项维度

`requirement_params.comparability_dimension` 只能是：

- `window_alignment`
- `subject_alignment`
- `slice_alignment`
- `grain_alignment`
- `method_precondition`

要求：

- 不得把 quality failure 伪装成 comparability requirement
- 不得把 coverage 缺口伪装成 comparability requirement
- `expected_relation` 与 `comparison_scope` 必须能稳定重放，不依赖实现层临时解释

### 3. gap identity 必须绑定 proposition 与 requirement semantics

comparability gap identity 必须绑定：

- proposition
- `requirement_key`
- `comparability_dimension`
- 稳定 relation semantics

不允许把一次运行实例、临时 notes 或展示文案作为 gap identity 的组成部分。

### 4. `requirement_key` 采用稳定语义命名，不默认内嵌实现细节

`requirement_key` 应优先表达“这是哪一个稳定 comparability requirement”，而不是直接复制实现参数。

推荐约束：

- 使用小写 ASCII `snake_case`
- 禁止包含 `:`
- 优先表达 requirement 语义或消费场景，而不是目录名、类名或临时文案
- 具体 relation 细节默认放在 `expected_relation` / `comparison_scope` 中，而不是重复塞进 `requirement_key`
- 只有当同一 proposition 下需要并存多个同维度 requirement，且仅靠维度名无法区分语义时，才允许把稳定限定词并入 key

示例：

- 推荐：`baseline_window_aligned`
- 推荐：`primary_metric_semantics_compatible`
- 推荐：`test_method_input_compatible`
- 不推荐：`compare-check-01`
- 不推荐：`grain-equals-day`

对 calendar alignment comparability，v1 固定 requirement keys 为：

- `baseline_calendar_policy_resolved`
- `holiday_cluster_alignment_complete`
- `event_cluster_alignment_complete`
- `weekday_pairing_compatible`
- `calendar_coverage_sufficient`
- `metric_data_coverage_sufficient`
- `alignment_tie_breaker_resolved`

这些 key 在 v1 中都映射到 `comparability_dimension = "window_alignment"`，并消费 compare / test
finding payload 中已冻结的 `calendar_alignment` 与 `comparability.issues` 字段。

其中：

- `baseline_calendar_policy_resolved` 以 task 1.4 冻结的最小 resolved alignment 字段集为准，不得只校验其中的字符串子集
- `calendar_coverage_sufficient` 在 v1 采用严格满覆盖语义：`aligned_ratio = 1.0` 且 `unpaired_bucket_count = 0`
- `metric_data_coverage_sufficient` 只消费 `effective_data_coverage_summary.coverage_ratio` 或 `metric_data_coverage_incomplete`；它表达业务 bucket 是否有值，不得与 calendar pairing coverage 混用
- `weekday_pairing_tie` 可同时导致 `weekday_pairing_compatible` 与 `alignment_tie_breaker_resolved` 失败；这是同一歧义在两个 requirement 维度上的显式映射，不视为重复报错
- compare/test 复用 frozen alignment metadata 时，产出的 calendar issues 必须显式标注 `gate_family = "comparability_gate"`；同一 code 不得再被 `quality_gate` 以平行 issue 重复报出
- v1 compare-like 默认分层为：`weekday_pairing_tie` 与 frozen metadata mismatch 为 blocking comparability issues；`holiday_cluster_unmapped`、`event_cluster_unmapped`、`fallback_applied`、`alignment_coverage_insufficient` 为 non-blocking comparability warnings，除非后续 requirement contract 明确升级

### 5. requirement token 必须显式输出

`comparability_gate` 的 `InferenceRecord.justification_json` 必须显式记录 requirement-level tokens：

- 满足：`comparability_requirement:<requirement_key>:met`
- 不满足：`comparability_requirement:<requirement_key>:failed`

当上游存在 calendar alignment frozen summary 时，还应补充窗口级摘要 signal：

- `comparability_signal:window_alignment:comparable`
- `comparability_signal:window_alignment:needs_attention`

该摘要 signal 只用于审计和后续 family 消费，不能替代 requirement-level token。

## Record Mapping Contract

### `result` 判定

`comparability_gate` 的 `InferenceRecord.result` 约束如下：

- `hit`：当前 rule 所要求的 comparability requirement 全部满足，且该通过对当前 snapshot 的 gap resolve、rule coverage 或 comparability impact 解释有价值
- `miss`：核心 comparability requirement 失败，足以解释保守降级、gap 保持或不得把当前 evidence 当作无保留比较输入
- `partial`：comparability requirement 部分满足，当前比较仍可被有限消费，但必须保留结构化 comparability caveat

更细规则：

- 当核心 comparability requirement 已失败，导致当前 evidence 不应继续作为该 judgment track 的安全比较输入时，应产出 `miss`
- 当当前比较仍可消费，但存在 `needs_attention`、边界对齐、局部 slice 漂移或次级方法 caveat，且这些 caveat 需要被后续 family 消费时，应产出 `partial`
- `partial` 不表示“几乎等于通过”；它表示“可以有限消费，但必须保留结构化 comparability 约束”
- `partial` 与 `miss` 的分界，不取决于 impact level，而取决于当前输入是否仍是该 judgment track 的合法比较输入
- `partial` 可以伴随 non-blocking gap，也可以完全不打开 gap；是否 materialize gap 取决于该 caveat 是否需要被稳定追踪为 requirement-level risk
- 同一 `comparability_gate` family 下，多条 rule 可以在一次 recompute 中分别产出 `partial`；canonical 读取面应保留逐条 `rule_id` 审计
- downstream family 不直接消费自由文本；它们只消费 `result`、opened / resolved gaps、condition tokens 与结构化 comparability impact

示例边界：

- `partial`：compare artifact 的 `comparability.status = needs_attention`，但尚未达到 `not_comparable`
- `partial`：左右窗口的主时间边界可对齐，但存在非核心 holiday / lag caveat，需要保留结构化约束
- `miss`：左右输入的 metric / unit / aggregation semantics 不兼容
- `miss`：test / correlate 方法要求的核心 inferential-ready precondition 不成立，当前结果不应继续作为可比输入

要求：

- 不允许只在 `hit` 时写 record，而忽略有解释价值的 `miss` / `partial`
- 仅凭 `comparability_gate` 的 `hit` 不得直接推出高 confidence 的单向强结论

### gap 字段

`comparability_gate` 打开或解决 gap 时，必须显式通过 record 驱动：

- 新打开的 gap 进入 `opened_gap_ids`
- 已满足原 requirement 的旧 gap 进入 `resolved_gap_ids`

解决归属规则：

- gap 是否解决，由当前 recompute 中命中该 requirement semantics 的 rule 决定；它不要求必须是“当初打开该 gap 的同一条 rule”，但必须属于同一 proposition 的 `comparability_gate` family，并能稳定指向同一个 `requirement_key`
- 若某个 comparability gap 被解决，但其他 comparability requirement 仍失败，则只解决已满足的那一个 gap；当前 assessment 仍可因剩余 comparability risk 保持保守输出
- 若同一 recompute 中有多条 rule 指向同一个 comparability requirement semantics，它们可以分别产出 record，但 candidate gap membership 必须按 gap identity 做集合归并；不得因 rule 顺序产生不同结果
- 若多条 rule 对同一 gap identity 给出相互冲突的 open / resolve 候选，实现必须先按同一 `requirement_key` 的 canonical requirement semantics 收敛后再 materialize，不能采用“最后一条规则覆盖前一条”的隐式优先级

最小收敛规则：

- 多条 rule 可以共同贡献同一 gap identity 的 open 或 resolve 证据
- open / resolve 候选先按 gap identity 归并，再决定最终 materialization
- 同一 gap identity 只要仍存在未满足的 canonical requirement，就不得在本次 snapshot 中 resolve
- 只有当该 gap identity 对应的 canonical requirement semantics 已被满足时，才允许 resolve

不允许：

- 仅因“这次没再提旧 gap”就视为已解决
- 同一 gap 在同一 snapshot 内同时出现在 open 与 resolve 两个集合

### comparability impact 字段

`comparability_gate` 可以产出结构化 comparability impact，但不直接写最终 `confidence_grade`。

family-level 要求：

- comparability impact 只作为 downstream 输入，供 `status_resolution` 与 `confidence_shaping` 消费
- 推荐 impact 语义与 `Assessment.confidence_rationale` 中的结构化风险维度对齐为 `none | limited | material | severe`
- `comparability_gate` 可声明“当前强结论不得无保留维持”的 guardrail 语义，但最终 status 仍由 `status_resolution` 决议

建议的定性边界：

- `none`：未观察到会影响当前比较消费方式的 comparability caveat
- `limited`：存在轻微 caveat，需要暴露但通常不改变当前 evidence 的主消费路径
- `material`：comparability 问题已明显限制 evidence 的可解释性或稳定性，后续 family 应保守降级或显著压低 confidence
- `severe`：comparability 问题已阻断当前 evidence 的安全消费，或足以触发强结论撤回 / gap blocking

示例：

- `limited`：`window_alignment` 存在轻微 lag caveat，但主窗口边界仍可对齐
- `limited`：`slice_alignment` 存在少量非核心 segment 漂移，当前主比较仍可继续
- `material`：`window_alignment` 的基线与当前窗口可对齐，但存在会明显影响解释稳定性的周期错位
- `material`：`method_precondition` 仅部分满足，当前结果可作为弱比较线索，但不应无保留支持强结论
- `severe`：`subject_alignment` 中 metric / unit / aggregation semantics 不兼容
- `severe`：`method_precondition` 的核心 inferential-ready 输入前提失败，当前结果不应继续作为比较输入

本文只固定这些 level 的语义边界，不在通用 contract 中定义跨 assessment type 的统一数值阈值。

### status transition 与 confidence grade 字段

`comparability_gate` 不是最终状态决议 family，也不是最终 confidence 塑形 family。

因此：

- 默认不应单独写 `produced_status_transition`
- 不应直接写最终 `confidence_grade`
- `comparability_gate` 的贡献应通过 `result`、gap 字段、condition tokens 与结构化 comparability impact 被后续 family 消费

## Condition Token Contract

### 目标

`matched_conditions` 与 `unmatched_conditions` 必须承载 `comparability_gate` 的主语义，做到：

- 稳定可比较
- 可被审计
- 可被 replay / compatibility 检查复用
- 可追溯回具体 requirement semantics

`notes` 只用于少量补充说明，不承载唯一主语义。

### token 命名规范

v1 先固定 token 组织原则，不在本文枚举所有 assessment-specific token。

token 应满足：

- 表达 comparability requirement 语义，而不是实现细节
- 可稳定对应到 `requirement_key`
- 不依赖目录名、类名、临时字符串拼接策略

推荐形态：

- comparability requirement：`comparability_requirement:<requirement_key>:met|degraded|failed`
- comparability signal：`comparability_signal:<dimension>:comparable|needs_attention|not_comparable`

示例：

```yaml
matched_conditions:
  - "comparability_requirement:primary_metric_semantics_compatible:met"
  - "comparability_signal:window_alignment:needs_attention"

unmatched_conditions:
  - "comparability_requirement:test_method_input_compatible:failed"
```

要求：

- `matched_conditions` 只写满足的稳定 token
- `unmatched_conditions` 只写失败、缺失或降级的稳定 token
- 若某项 comparability requirement 被 materialize 为 gap，相关 token 必须能回指同一 `requirement_key`

assessment-specific profile 后续若要新增 comparability requirement catalog，必须复用这套 token 语义，而不是为每条 rule 自发明自由文本。

## Downstream Consumption Contract

### `status_resolution`

`status_resolution` 只能消费 `comparability_gate` 的结构化输出，不消费自由文本解释。

它可使用的输入包括：

- `result`
- `opened_gap_ids` / `resolved_gap_ids`
- `matched_conditions` / `unmatched_conditions`
- 结构化 comparability impact

用途包括：

- 判断当前 comparability failure 是否要求保守回到 `insufficient`
- 判断 blocking 与 non-blocking comparability gap 是否影响最终 status 收敛

### `confidence_shaping`

`confidence_shaping` 负责把 `comparability_gate` 的结构化 comparability impact materialize 到：

- `confidence_rationale`
- 最终 `confidence_grade`

要求：

- `comparability_gate` 不直接写最终 `confidence_grade`
- `confidence_shaping` 必须复用 [`assessment.md`](../schemas/assessment.md) 与 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 已声明的全局 guardrails
- 当 `comparability_gate` 输出 `severe` impact 时，最终 `confidence_grade` 不得高于 `low`

## Registry Contract

`comparability_gate` 的 family 归属仍完全依赖 [`rule-registry-contract.md`](rule-registry-contract.md)：

- `InferenceRecord` 不新增 `rule_family` 字段
- `rule_id -> rule_family -> assessment_type` 必须由 registry 显式解引用
- 读取面不得通过字符串前缀、目录路径或类名推断 family

当某条 rule 注册到 `comparability_gate` 时，至少应满足：

- `rule_family = "comparability_gate"`
- `assessment_type` 明确，且属于本 family 当前允许的四个 assessment type 之一
- `rule_cluster` 仅用于更细业务分组，不改变 family 语义
- `rule_version` 变更时，不得破坏已持久化 token / gap 语义的可解释性

安全演进要求：

- 若仅新增 token 或 requirement key，且不改变既有 token 的既有语义，可视为 backward-compatible 变更
- 若某个 token 将废弃，应至少保留一个兼容窗口：旧 token 仍可被 replay / 审计解释，新增实现可同时产出旧新映射，或在 registry / 兼容文档中声明等价关系
- 若必须改变既有 token 的语义或 requirement semantics，应视为 breaking change，并伴随新的 `rule_version`；breaking 变更不得复用旧 token 字面值去表达新语义
- 对已持久化 records，不要求原地迁移 token 文本；但 replay、compatibility 检查与读取面必须仍能通过对应版本边界解释旧 token

## Snapshot And Lifecycle Constraints

`comparability_gate` 必须兼容 change-only snapshot policy：

- 相同 canonical inputs 重算，不得仅因 token 顺序、说明文本或实现细节变化制造新 snapshot
- canonical outcome 未变时，本轮 candidate records 必须整体丢弃
- 若 gap membership 改变，即使 status 不变，也必须允许形成新 snapshot
- 若新的 comparability failure 进入、旧 gap 重开或 finding 当前不可再安全比较，必须允许 downgrade

`comparability_gate` 不得假设状态只会单调增强。

对 `comparability_gate` 而言，canonical outcome 至少包括：

- 每条 rule 的 `result`
- `matched_conditions` / `unmatched_conditions` 的成员集合
- `opened_gap_ids` / `resolved_gap_ids` 的成员集合
- emitted comparability impacts 的成员集合
- 是否因此改变了当前 candidate assessment 的 blocking / non-blocking gap membership

不应进入 canonical equality 判断的内容包括：

- `notes` 的措辞
- record / gap 的展示顺序
- 时间戳或其他非 judgment 语义的运行时元数据

## Acceptance Scenarios

1. 左右输入的核心 metric / unit / aggregation semantics 不兼容时，`comparability_gate` 写入 `miss` record，并打开 blocking `comparability_risk` gap。
   示例 token：
   `unmatched_conditions = ["comparability_requirement:primary_metric_semantics_compatible:failed"]`
2. compare artifact 的 `comparability.status = needs_attention`，且当前比较仍是合法输入时，`comparability_gate` 写入 `partial` record，并保留结构化 comparability token 与 impact；该场景可以不打开 gap，也可以只打开 non-blocking gap。
   示例 token：
   `matched_conditions = ["comparability_signal:window_alignment:needs_attention"]`
3. 某条旧 `comparability_risk` gap 在新输入满足同一 requirement semantics 后，本轮必须通过 `resolved_gap_ids` 显式解决该 gap；若同次 recompute 中另一个 comparability requirement 新失败，则允许“resolve 一个 gap，同时 open 另一个 gap”。
4. 当多条 rule 同时指向同一 `requirement_key` 时，只要仍有未满足的 canonical requirement semantics，该 gap 就不得 resolve；最终 candidate gap membership 必须与 rule 执行顺序无关。
5. 相同 canonical inputs 重算时，condition token、gap identity、record result 与结构化 comparability impact 保持稳定，不制造额外 snapshot。
6. `comparability_gate` 全部命中通过时，系统仍需经过后续 evidence 与 `status_resolution` family 才能形成最终 judgment。

## Out-Of-Scope Failures

以下情形属于上游 contract violation 或 engine / registry 问题，不应被 `comparability_gate` 降格成普通 `miss` / `partial`：

- required finding payload malformed，无法按 canonical schema 解读
- 上游 artifact 产出了违反其自身 contract 的非法 comparability 状态
- `rule_id` 无法通过 registry 稳定解引用

这些问题应由上游 schema、runtime lifecycle、engine 或 registry contract 处理，而不是由本 family 在 judgment 语义层兜底。

## 与其他文档的关系

- [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 定义 rule family 的固定 evaluation order、通用职责边界与 `InferenceRecord` 写入总规则
- [`assessment.md`](../schemas/assessment.md) 定义 `EvidenceGap` 与 `InferenceRecord` 的 canonical schema
- [`compare.md`](../../intents/atomic/compare.md) 与 [`test.md`](../../intents/atomic/test.md) 定义 compare / test 上游 artifact 的 comparability 语义入口
- [`rule-registry-contract.md`](rule-registry-contract.md) 定义 `rule_id -> rule_family -> assessment_type` 的稳定解引用
- [`assessment-judgment-policy.md`](assessment-judgment-policy.md) 定义不同 `assessment_type` 的最终 judgment policy
- [`rule-family-design-checklist.md`](rule-family-design-checklist.md) 提供 family-level 设计评审 checklist
