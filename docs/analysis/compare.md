# compare 原子意图 Schema

本文档定义 `compare` 原子意图的拟议类型契约。

状态：draft design。本文是规划中的原子 `compare` 意图 Schema 提案，不表示对应 HTTP endpoint 已经实现。

## 目的

`compare` 用于计算同一指标两个观测之间的类型化差异（delta）。

设计目标：

- 把对比（comparison）建模为一等分析意图，而不是原始算术
- 要求显式且可比较的输入，而不是任意 scope 字典
- 通过类型化差值（delta）契约保持确定性证据抽取
- 在校验和响应元数据中显式表达可比性（comparability）
- 保持工件（artifact）完整可引用，把压缩和截断放到投影层（projection layer）

## 核心设计决策

`compare` 消费类型化观测引用（typed observation references），而不是 `metric + ad hoc scopes`。

这样可以保持数据流清晰：

- `observe` 定义”观测了什么”
- `compare` 定义”这两个观测如何不同”

因此 v1 只保留引用式契约，而不再维护一套平行的直接对比（direct compare）契约。

`compare` 的工件（artifact）必须保持完整、可复现、可引用。任何 top-k、截断（truncation）、紧凑重述都属于从 artifact 确定性导出的投影（projection），而不是 `compare` artifact 自身的一部分。

## Request Shape

```json
{
  "step_type": "compare",
  "left_ref": {
    "session_id": "sess_123",
    "step_id": "step_obs_current",
    "step_type": "observe"
  },
  "right_ref": {
    "session_id": "sess_123",
    "step_id": "step_obs_baseline",
    "step_type": "observe"
  },
  "mode": "auto"
}
```

## Typed Schema

```ts
type ObservationRef = {
  session_id: string;
  step_id: string;
  step_type: "observe";
};

type CompareRequest = {
  step_type: "compare";
  left_ref: ObservationRef;
  right_ref: ObservationRef;
  mode?: "auto" | "scalar" | "segmented";
};
```

## 输入规则

`left_ref` 与 `right_ref` 必须指向先前 `observe` 步骤产出的观测工件（observation artifact）。`compare` 不接受重复描述 `metric`、`time_scope`、`scope` 或 filter 的平行输入。

v1 支持以下输入对：

### 标量 vs 标量（Scalar vs Scalar）

- 两个 ref 都解析到 `observation_type = "scalar"` 的 `observe` 输出
- 两边必须属于同一个 `metric`

输出类型：`scalar_delta`

### 分段 vs 分段（Segmented vs Segmented）

- 两个 ref 都解析到 `observation_type = "segmented"` 的 `observe` 输出
- 两边必须属于同一个 `metric`
- 两边必须拥有完全相同的 `dimensions`

输出类型：`segmented_delta`

## v1 不支持的输入对

- 时间序列（`time_series`）与任何类型对比
- 标量（`scalar`）与分段（`segmented`）
- 分段（`segmented`）与标量（`scalar`）
- 来自不同 metric 的观测
- `dimensions` 不一致的分段观测

未来可以扩展时间序列对比（time-series compare），但应使用独立结果契约，而不是给 v1 行为过载。

## 字段语义

### left_ref / right_ref

结构化观测引用（observation refs），指向先前 `observe` 步骤产出的观测工件（observation artifact）。

约定：

- `left_ref` 表示被考察的一侧，例如当前周期、treatment、target
- `right_ref` 表示基线（baseline）一侧，例如前一周期、control、reference
- `absolute_delta = left_value - right_value`

因此，正差值（delta）表示被考察侧高于基线侧。

### mode

- `auto`：根据引用到的观测类型（observation type）自动推断
- `scalar`：要求标量对比
- `segmented`：要求分段对比

显式 mode 可用作调用方的防护栏（guardrail），但不改变上游观测（observation）的语义，也不用于重建输入 scope。

## 可比性契约

只有在两个观测被视为可比（comparable）时，`compare` 才是合法的。

系统在计算差值（delta）前至少要检查：

- 相同 `metric`
- 相同 unit
- 相同观测类型（`observation_type`）
- 相同 aggregation semantics
- 分段对比（segmented compare）下相同 dimension schema
- 兼容的 temporal semantics
- 除目标比较轴之外，其余已解析 `scope` 定义兼容

系统应返回以下之一：

- `comparable`（可比）
- `needs_attention`（需要注意）
- `not_comparable`（不可比）

约束：

- `not_comparable` 必须硬失败，不能成功产出 compare artifact
- `needs_attention` 可以成功返回 artifact，但必须通过结构化 issues 暴露告警
- artifact 成功返回时，`comparability.status` 只能是 `comparable` 或 `needs_attention`

## 差值语义（Delta Semantics）

结果必须始终包含：

- 绝对变化量（absolute delta）
- 在数学上可定义时的相对变化量（relative delta）
- 方向（direction）

定义：

- `absolute_delta = left_value - right_value`
- `relative_delta = absolute_delta / right_value`

若 `right_value` 为 `0` 或 `null`，则 `relative_delta` 必须为 `null`，不得伪造。

direction 根据指标感知的平稳阈值（metric-aware flat tolerance）导出：

- 任一侧为 null 且无法确定性计算 delta 时，方向为 `undefined`
- `absolute_delta = 0` 时，方向为 `flat`
- 若 `relative_delta != null` 且 `abs(relative_delta) <= flat_tolerance_relative`，方向为 `flat`
- 否则正值为 `increase`，负值为 `decrease`

推荐方向值：

- `increase`
- `decrease`
- `flat`
- `undefined`

## 工件标识（Artifact Identity）与版本控制（Versioning）

本文档定义的是 `compare` 的完整工件语义（artifact semantics），而不是面向 agent 的压缩视图。

`compare` artifact 的标识边界（identity boundary）应至少绑定：

- 本次 `compare` 执行所消费的 `left_ref` 与 `right_ref`
- compare 请求自身的语义参数，例如 `mode`
- 本次执行产生的工件谱系（artifact lineage）

规则：

- compare artifact 绑定本次执行谱系（lineage）
- compare artifact 默认视为不可变（immutable）
- 重读同一 artifact 是读取同一谱系下的同一对象
- 重新执行 compare 即使语义相同，也应产生新 artifact，而不是与旧 artifact 隐式复用标识（identity）

artifact contract 应显式区分：

- `schema_version`：compare artifact 自身的契约版本（contract version）
- `observation_schema_version`：上游观测工件契约版本（observation artifact contract version）
- `derivation_version`：compare 计算与抽取逻辑的版本

## 响应形状（Response Shape）

```ts
type CompareResponse =
  | ScalarDeltaArtifact
  | SegmentedDeltaArtifact;

type ObservationRef = {
  session_id: string;
  step_id: string;
  step_type: "observe";
};

type CompareArtifactLineage = {
  left_source_ref: ObservationRef;
  right_source_ref: ObservationRef;
  observation_schema_version: string | null;
  derivation_version: string;
};

type CompareResolvedInputSummary = {
  left_time_scope: ResolvedTimeScope | null;
  right_time_scope: ResolvedTimeScope | null;
  left_scope: Scope;
  right_scope: Scope;
};

type CompareBase = {
  artifact_type: "compare_artifact";
  schema_version: string;
  comparison_type: "scalar_delta" | "segmented_delta";
  metric: string;
  left_ref: ObservationRef;
  right_ref: ObservationRef;
  lineage: CompareArtifactLineage;
  resolved_input_summary: CompareResolvedInputSummary;
  unit: string | null;
  comparability: ComparabilityMetadata;
  analytical_metadata: CompareAnalyticalMetadata;
  execution_metadata: ExecutionMetadata;
};

type ComparabilityIssue = {
  code:
    | "metric_mismatch"
    | "unit_mismatch"
    | "observation_type_mismatch"
    | "dimension_mismatch"
    | "time_scope_mismatch"
    | "time_length_mismatch"
    | "scope_divergence"
    | "aggregation_mismatch"
    | "sample_size_disparity"
    | "data_incomplete";
  severity: "error" | "warning";
  message: string;
};

type ComparabilityMetadata = {
  status: "comparable" | "needs_attention" | "not_comparable";
  issues: ComparabilityIssue[];
};

type CompareAnalyticalMetadata = {
  aggregation_semantics: string;
  metric_additivity: "additive" | "semi_additive" | "non_additive";
  relative_delta_denominator: "right";
  flat_tolerance_relative: number;
  left_row_count: number | null;
  right_row_count: number | null;
};

type ExecutionMetadata = {
  query_hash: string;
  engine: string;
  executed_at: string;
};

type ScalarDeltaArtifact = CompareBase & {
  comparison_type: "scalar_delta";
  left_value: number | null;
  right_value: number | null;
  absolute_delta: number | null;
  relative_delta: number | null;
  direction: "increase" | "decrease" | "flat" | "undefined";
};

type SegmentedDeltaArtifact = CompareBase & {
  comparison_type: "segmented_delta";
  dimensions: string[];
  rows: Array<{
    keys: Record<string, string | number | boolean | null>;
    left_value: number | null;
    right_value: number | null;
    absolute_delta: number | null;
    relative_delta: number | null;
    direction: "increase" | "decrease" | "flat" | "undefined";
    presence: "both" | "left_only" | "right_only";
  }>;
  scope_left_value: number | null;
  scope_right_value: number | null;
  scope_absolute_delta: number | null;
  scope_relative_delta: number | null;
  scope_direction: "increase" | "decrease" | "flat" | "undefined";
};
```

`resolved_input_summary` 是从上游观测工件（observation artifact）确定性派生出的只读溯源摘要，用于说明 compare 所比较的已解析上下文。它必须保留规范 `Scope` 的完整 shape（`constraints + predicate`），而不是只保留 predicate。它不是新的步骤级输入契约，也不替代 Factum 现有的 `time_scope` / `scope` 设计。

## 校验规则

- 两个 ref 都必须解析到已完成步骤
- 两个 ref 都必须显式声明 `step_type = “observe”`
- 两个 ref 都必须暴露兼容的观测载荷（observation payload）
- 两个观测必须属于同一个 `metric`
- 标量对比（scalar compare）要求 unit 与 aggregation semantics 完全一致
- 分段对比（segmented compare）额外要求 `dimensions` 完全一致
- 任一侧缺失可用数值时，delta 字段应变为 `null`，不能自动补零
- `relative_delta` 在分母为 `0` 或 `null` 时必须为 `null`
- 分段对比（segmented compare）必须按完整 dimension key tuple 对齐
- 仅出现在一侧的 segment 必须保留，并使用 `presence = “left_only”` 或 `presence = “right_only”` 标记

对于单侧行（one-sided rows）：

- `left_only`：`right_value = null`，`absolute_delta = left_value`，`relative_delta = null`
- `right_only`：`left_value = null`，`absolute_delta = -right_value`，`relative_delta = null`
- `direction` 应为 `undefined`，由消费方结合 `presence` 解释”新增 / 消失”

## 分段差值约束（Segmented Delta 约束）

分段差值行（segmented delta rows）是 compare artifact 的 delta rows，可供 `decompose` 等下游步骤消费，但本身不是归因（attribution）/ 解释（explanation）结论。

对于可加性指标（additive metrics）：

- `sum(rows.absolute_delta)` 应与 `scope_absolute_delta` 对账

对于不可加指标（non-additive metrics）：

- 行级差值（delta）只能视为切片内局部变化（slice-local changes）
- `sum(rows.absolute_delta)` 不应被期待等于 `scope_absolute_delta`
- 下游如 `decompose` 不能假定这种对账关系天然成立

## 校验失败类别

- `INVALID_ARGUMENT`
  请求形状、参数或输入类型不合法
- `STEP_NOT_FOUND`
  某个 ref 无法解析
- `UNSUPPORTED_COMPARISON`
  输入本身有效，但当前 v1 不支持该对比形态
- `NOT_COMPARABLE`
  观测未通过必需的可比性（comparability）检查

## 下游兼容性说明

- `decompose` 应优先消费 `compare` 产出的差值定义（delta 定义），而不是重复接收 raw scopes
- `attribute` 可以展开为 `observe -> observe -> compare -> decompose`
- `test` 不应把 compare 输出直接当作统计样本

## 工件（Artifact）与投影（Projection）

本文档定义的是 `compare` 的完整工件语义（artifact semantics）。

面向 agent 或 UI 的投影（projection）可以从 artifact 确定性导出，例如：

- 对 `segmented_delta.rows` 做固定排序后 top-k 截断
- 提炼最强的增长（increase）/ 下降（decrease）行
- 用紧凑形式重述 scope delta
- 生成明确披露截断（truncation）的 `others` bucket

若需要投影，推荐排序规则为：

1. `abs(relative_delta) desc`，nulls last
2. `abs(absolute_delta) desc`，nulls last
3. dimension-key lexical order

投影不得：

- 重定义可比性（comparability）状态
- 改写差值（delta）方向或分母语义
- 把单侧行（one-sided rows）合并成持续行（continuing rows）
- 创造新的声明（claims）
- 替代 artifact 自身成为下游类型化引用（typed reference）的目标
