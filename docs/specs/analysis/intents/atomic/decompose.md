# decompose 原子意图 Schema

本文档定义 `decompose` 原子意图的拟议类型契约。

状态：draft design。本文是规划中的原子 `decompose` 意图 Schema 提案，不表示对应 HTTP endpoint 已经实现。

> **V1 Scope**：当前版本只支持**变化归因（delta attribution）**，不支持总量构成分解（total composition）。
> 如果要看当前值由哪些部分构成，请使用 `observe(segmented)`。

## 目的

`decompose` 用于将一个已经明确定义的指标差值（metric delta）分配到排序后的维度贡献项。

设计目标：

- 让 `decompose` 只关注变化归因，而不是通用分解（breakdown）
- 消费显式上游差值定义（delta 定义），不重新引入 ad hoc scopes
- 返回可排序、可下游消费的类型化贡献工件（typed contribution artifact）
- 当 metric 或方法无法支撑可辩护归因时拒绝请求

## 核心设计决策

`decompose` 是变化归因步骤（delta-attribution step），不是通用构成步骤（generic composition step）。

v1 明确不支持：

- 当前值总量分解
- 多维交互项分析
- 带不同证据语义的多种归因方法（attribution methods）

这些场景要么属于 `observe(segmented)`，要么需要独立的未来契约。

## 工件标识（Artifact Identity）与谱系（Lineage）

`delta_decomposition` 是不可变规范工件（immutable canonical artifact）。

标识边界（identity boundary）绑定以下输入：

- `compare_artifact_id` 指向的 compare artifact lineage
- `dimension`
- `artifact_schema_version`
- `derivation_version`

以下内容不得进入 artifact identity：

- projection 截断参数
- 排序位置
- explanation 文本
- execution timestamp
- engine 选择

本契约必须显式区分：

- 重读同一 artifact：同一 identity，同一 lineage
- 重新执行同一请求：可产生新的 execution record，但若 compare lineage 与 derivation version 未变，则不产生新的 canonical artifact identity
- compare source lineage、artifact schema version 或 derivation version 变化：必须产生新的 `delta_decomposition` artifact

v1 默认：

- artifact 不允许跨 lineage 复用 identity
- artifact 为 immutable，不支持 session 内覆盖更新
- `decompose` 只能引用同 session 内已完成的 compare artifact

## Reference Contract

`decompose` 消费 `compare_artifact_id`，而不是引用对象或裸字符串 step id。
运行时可在输出 artifact 中从 compare artifact 元数据重建 `compare_ref` 谱系。

```ts
type CompareArtifactRef = {
  step_type: "compare";
  session_id: string;
  step_id: string;
  artifact_id: string;
  comparison_type: "scalar_delta" | "time_series_delta";
};

type ObservationArtifactRef = {
  step_type: "observe";
  session_id: string;
  step_id: string;
  artifact_id: string;
  observation_type: "scalar" | "time_series";
};
```

引用约束：

- `compare_artifact_id` 必须指向已完成步骤产出的 canonical compare artifact
- 被引用 artifact 的 `comparison_type` 在 v1 中必须是 `scalar_delta` 或 `time_series_delta`
- 不允许 projection ref 充当 canonical source ref
- v1 不允许跨 session artifact ref
- 引用图必须保持 DAG；`decompose` 不允许直接或间接回指依赖自己的对象

## Request Shape

```json
{
  "step_type": "decompose",
  "compare_artifact_id": "cmp_artifact_123",
  "dimension": "country",
  "limit": 5
}
```

## Typed Schema

```ts
type DecomposeRequest = {
  step_type: "decompose";
  compare_artifact_id: string;
  dimension: string;
  limit?: number;
};
```

## 输入规则

v1 支持的输入形态如下：

- `compare_artifact_id` 必须解析到已完成的 `compare` artifact
- 被引用的 compare 输出必须是 `scalar_delta` 或 `time_series_delta`
- 被比较的 metric 的 `additive_dimensions` 必须非空（即 metric 支持至少一个维度的分解）
- 该 metric 必须声明自己可按所请求的 `dimension` 做分解（`dimension` 必须在 `additive_dimensions` 列表内）
- `limit` 省略时返回实现默认的完整排序结果；提供时必须为正整数

输出类型：`delta_decomposition`

## v1 不支持的输入

- 直接传 `scope`
- 直接传 `metric + left_scope + right_scope`
- 引用对象或 `method`
- 以 `segmented_delta` 作为主输入契约
- 多个 dimensions
- interaction-effect decomposition
- `additive_dimensions` 为空的 metrics（不支持分解）
- `shapley`、`anova` 等替代方法
- projection 参数驱动的执行请求

推荐错误码：`INVALID_ARGUMENT`。

不允许 direct scope input 是刻意设计：v1 中 `compare` 是唯一 delta-definition contract，这样 left / right 语义、comparability 检查和 provenance 都能保持显式且不重复。

## 字段语义

### compare_artifact_id

指向先前 `compare` 步骤产出的 canonical artifact，用于定义要被解释的 delta。

沿用 `compare` 的约定：

- `left_ref` 是被考察的一侧
- `right_ref` 是基线一侧
- `absolute_delta = left_value - right_value`

`decompose` 不会重定义这个 delta，只负责解释它。

v1 要求 `compare_artifact_id` 解析到 `scalar_delta` 或 `time_series_delta`，而不是 `segmented_delta`。

当上游是 `time_series_delta` 时，`decompose` 解释的是 compare 已对齐 bucket 之后的 summary delta，而不是为每个时间 bucket 单独生成一组 contribution rows。若 compare analytical metadata 提供 `matched_left_time_scope` / `matched_right_time_scope`，`decompose` 的 grouped 重算必须分别复用左右两侧的 matched 范围；若只有兼容字段 `matched_time_scope`，则可继续将其同时视作两侧范围，以保持与 summary delta 同一对账边界。

原因：

- 当前请求的分解维度可能与上游 compare 的 segmentation 不同
- 上游 segmented compare 可能已被排序或截断，不适合作为 attribution 基底
- `decompose` 必须自行控制完整重算、完整性检查和 canonical 排序

### dimension

用于归因的单个 semantic dimension。

`dimension` 必须是 semantic dimension id，而不是任意物理列名。

v1 只支持一个维度，因为：

- 单维度归因拥有稳定的排序和截断语义
- 多维归因会引入 interaction semantics，需要单独契约

### limit

可选的返回行数上限。runtime 先按 contribution share / contribution magnitude / key
形成稳定排序，再返回前 `limit` 行。

其含义为：

- 在 `compare_ref` 继承的 left / right scopes 下，按请求 dimension 的每个取值重算 metric
- 计算每个 segment 的 delta：`segment_left_value - segment_right_value`
- 当数学和语义上都成立时，计算该 segment 对 scope delta 的带符号 share

v1 将 `delta_share` 限制在 `additive_dimensions` 非空的 metrics 上；`additive_dimensions` 为空的 metrics 在 v1 中必须拒绝。

## Presence 语义

`presence` 表示某个维度值出现在两边还是只出现在一边。

取值：

- `both`
- `left_only`
- `right_only`

计算规则：

- `both`：`absolute_contribution = left_value - right_value`
- `left_only`：返回 `right_value = null`，但归因计算中视作 `0`
- `right_only`：返回 `left_value = null`，但归因计算中视作 `0`

解释语义：

- `both`：持续存在的维度值发生了变化
- `left_only`：新出现的维度值对总变化产生贡献
- `right_only`：消失的维度值对总变化产生贡献

`presence` 是解释性元数据，不是单独的数学模式。对 additive metric 而言，单边行仍参与整体对账。

## Signed Share 解释

`contribution_share` 是带符号的 attribution ratio，不是单纯 magnitude share。

- 正值表示该 segment 与 scope delta 同方向，强化整体变化
- 负值表示该 segment 与 scope delta 反方向，抵消整体变化的一部分

例如：

- `scope_absolute_delta = -2000000`
- 某行 `absolute_contribution = 1000000`
- 则 `contribution_share = -0.5`

解释：该行抵消了整体下跌的 50%。如果没有该 segment，总体 delta 会从 `-2000000` 扩大到 `-3000000`。

## Response Shape

```ts
type DecomposeResponse = DeltaDecomposition;

type DeltaAttributionIssue = {
  code:
    | "unsupported_dimension"
    | "metric_not_decomposable"
    | "non_additive_not_supported"
    | "data_incomplete"
    | "scope_recomputation_failed"
    | "attribution_not_reconcilable";
  severity: "error" | "warning";
  message: string;
};

type AttributionMetadata = {
  status: "attributable" | "needs_attention";
  issues: DeltaAttributionIssue[];
};

type DecomposeAnalyticalMetadata = {
  method: "delta_share";
  aggregation_semantics: string;
  additive_dimensions: string[];
  capability_condition: "dimension_must_be_allowed" | null;
  reconciliation_expected: boolean;
  flat_tolerance_relative: number;
  left_row_count: number | null;
  right_row_count: number | null;
  returned_row_count: number;
};

type CanonicalVersionMetadata = {
  artifact_schema_version: string;
  source_contract_version: string;
  derivation_version: string;
};

type SourceLineageMetadata = {
  compare_artifact: CompareArtifactRef;
  left_artifact: ObservationArtifactRef;
  right_artifact: ObservationArtifactRef;
};

type ExecutionMetadata = {
  query_hash: string;
  engine: string;
  executed_at: string;
};

type DeltaDecompositionRow = {
  key: string | number | boolean | null;
  left_value: number | null;
  right_value: number | null;
  absolute_contribution: number | null;
  contribution_share: number | null;
  direction: "increase" | "decrease" | "flat" | "undefined";
  presence: "both" | "left_only" | "right_only";
};

type DeltaDecomposition = {
  decomposition_type: "delta_decomposition";
  artifact_id: string;
  metric: string;
  compare_ref: CompareArtifactRef;
  left_ref: ObservationArtifactRef;
  right_ref: ObservationArtifactRef;
  dimension: string;
  method: "delta_share";
  unit: string | null;
  left_time_scope: ResolvedTimeScope;
  right_time_scope: ResolvedTimeScope;
  resolved_scopes: {
    left: Scope;
    right: Scope;
  };
  scope_left_value: number | null;
  scope_right_value: number | null;
  scope_absolute_delta: number | null;
  scope_relative_delta: number | null;
  scope_direction: "increase" | "decrease" | "flat" | "undefined";
  attribution: AttributionMetadata;
  rows: DeltaDecompositionRow[];
  unexplained_absolute_delta: number | null;
  unexplained_share: number | null;
  unexplained_reason:
    | "data_incomplete"
    | "scope_recomputation_failed"
    | "rounding"
    | null;
  analytical_metadata: DecomposeAnalyticalMetadata;
  version_metadata: CanonicalVersionMetadata;
  source_lineage: SourceLineageMetadata;
  execution_metadata: ExecutionMetadata;
};
```

## 主状态字段与推导规则

`attribution.status` 是 agent-facing 主状态字段。

取值：

- `attributable`
- `needs_attention`

推导规则：

- 默认值为 `attributable`
- 当 `issues` 中存在任一 `severity = "error"` 的 issue 时，`status` 必须为 `needs_attention`
- `warning` 不自动降低 `status`，除非未来版本新增显式规则
- 当 `analytical_metadata.reconciliation_expected = true` 且结果无法与 `scope_absolute_delta` 对账时，必须生成 `attribution_not_reconcilable` 且 `severity = "error"`

empty semantics：

- `issues = []` 表示 `no_known_attribution_issues`

非法状态：

- `status = "attributable"` 且存在 `severity = "error"` 的 issue
- `status = "needs_attention"` 但 `issues = []`

## Nullability 与 Empty Semantics

本契约中的 nullable 字段必须保持单义：

- `unit = null`：metric 没有 canonical unit，语义为 `not_applicable`
- `resolved_scopes.left/right` 必须保留完整 canonical scope；若该侧没有额外 non-time filter，应返回 `{}` 或其等价 normalized total scope，而不是 `null`
- `scope_left_value` / `scope_right_value = null`：该侧 scope value 当前无法可靠解析，语义为 `not_yet_resolved`
- `scope_absolute_delta = null`：scope delta 当前无法可靠解析，语义为 `not_yet_resolved`
- `scope_relative_delta = null`：相对变化不可定义，例如 baseline 为 0，语义为 `not_applicable`
- `row.key = null`：source dimension value 本身为 null bucket，不表示 unknown
- `row.left_value = null`：仅在 `presence = right_only` 时合法，语义为该侧不存在该 segment，`not_applicable`
- `row.right_value = null`：仅在 `presence = left_only` 时合法，语义为该侧不存在该 segment，`not_applicable`
- `absolute_contribution = null`：当前行 contribution 无法可靠计算，语义为 `not_yet_resolved`
- `contribution_share = null`：share 不可定义，例如 `scope_absolute_delta = 0` 或 null，语义为 `not_applicable`
- `left_row_count` / `right_row_count = null`：该侧 canonical row count 当前不可得，语义为 `not_yet_resolved`
- `unexplained_absolute_delta = null`：剩余未归因量当前无法可靠解析，语义为 `not_yet_resolved`
- `unexplained_share = null`：未归因 share 不可定义或尚未解析完成
- `unexplained_reason = null`：仅当 `unexplained_absolute_delta = 0` 或该字段 `not_applicable` 时合法

empty semantics：

- `issues = []`：没有已知 attribution 问题

## 校验规则

- `compare_artifact_id` 必须解析到现有且已完成的 `compare`
- 被引用结果必须是 `scalar_delta` 或 `time_series_delta`
- `dimension` 必须是单个 semantic dimension 名称
- 请求不得包含引用对象或 `method`
- `limit` 若提供必须为正整数；runtime 在稳定排序后返回前 `limit` 行
- metric 的 `additive_dimensions` 必须非空，且被请求的 `dimension` 必须在该列表内；`additive_dimensions` 为空的 metric 在 v1 中必须拒绝
- one-sided rows 必须显式保留，并通过 `presence` 标记
- 成功 artifact 必须至少包含一条 contribution row；若当前请求无法形成任何 canonical contribution row，请求必须失败
- 当 `scope_absolute_delta` 为 `0` 或 `null` 时，`contribution_share` 必须为 `null`
- `unexplained_*` 只表示“未归因”的剩余部分，且非零时必须给出 `unexplained_reason`
- 若 `reconciliation_expected = true`（即 `aggregation_semantics = "sum"` 且 `additive_dimensions` 非空），则返回行与 `unexplained_*` 应能与 scope delta 对账
- projection 参数不得改变 canonical artifact identity

## 错误语义

- `INVALID_ARGUMENT`
  请求形状非法、v1 不支持的参数、或上游类型不兼容
- `ARTIFACT_NOT_FOUND`
  `compare_artifact_id` 无法解析
- `UNSUPPORTED_DIMENSION`
  metric 不支持按该 dimension 分解
- `NOT_ATTRIBUTABLE`
  delta 存在，但在当前契约下无法做可辩护归因，包括无法形成任何 canonical contribution row

成功响应中 `rows = []` 属于非法状态，不得被解释为 successful empty result。

## Agent Consumption Contract

agent 可稳定消费以下查询轴：

- `metric`
- `dimension`
- `attribution.status`
- `rows[].presence`
- `rows[].direction`

artifact 默认排序固定为：

1. `abs(contribution_share) desc`，nulls last
2. `abs(absolute_contribution) desc`，nulls last
3. `key` lexical order

最小闭包读取要求：

- agent 若读取 contribution rows，必须同时读取 `scope_absolute_delta`
- agent 若读取 contribution rows，必须同时读取 `attribution.status`
- agent 若读取压缩视图，必须同时读取截断元数据与 residual bucket

v1 不提供单行 contribution 的独立 typed ref。下游步骤若依赖 `decompose`，应引用整个 artifact，而不是引用单行投影。

## Artifact 与 Projection

本文档定义的是 `decompose` 的完整 artifact semantics。

artifact 层必须保持完整结果，不因调用方 token budget 或展示偏好而变化。

projection 是从 artifact 确定性压缩得到的 bounded view。

```ts
type DecomposeProjectionRequest = {
  artifact_ref: {
    artifact_type: "delta_decomposition";
    session_id: string;
    artifact_id: string;
  };
  row_limit?: number;
};

type DecomposeProjection = {
  artifact_ref: {
    artifact_type: "delta_decomposition";
    session_id: string;
    artifact_id: string;
  };
  returned_row_count: number;
  total_row_count: number;
  is_truncated: boolean;
  rows: DeltaDecompositionRow[];
  others_absolute_contribution: number | null;
  others_contribution_share: number | null;
};
```

projection 可以：

- 截断长尾 contribution rows
- 压缩为 top drivers + residual
- 重新组织为 agent / UI 友好的摘要

projection 不得：

- 修改 attribution status
- 改写 contribution 计算语义
- 隐式合并 one-sided rows
- 发明新的解释性 claim
- 充当下游步骤的 canonical source ref

projection nullability：

- `others_absolute_contribution = null`：未发生截断，语义为 `not_applicable`
- `others_contribution_share = null`：未发生截断，或 residual share 不可定义

## 负向契约

以下状态是非法的：

- 使用 projection ref 替代 canonical compare ref
- 使用跨 session ref
- 使用未完成 compare step 的 artifact ref
- 使用 `segmented_delta` compare artifact 作为 v1 输入
- 让 projection 参数影响 artifact identity
- 用 `null` 同时表达 one-sided、unknown 与 not_applicable
- `attribution.status = "attributable"` 且存在 error issue

## 下游兼容性说明

- `attribute` 可以展开为 `compare -> decompose`（`attribute` 同时支持 `scalar_delta` 和 rate metric 的归因）
- `diagnose` 可以展开为 `detect -> compare -> decompose`
- `synthesize` 应依赖 `attribution.status` 与 `issues`，不要默认所有返回 share 都完全可靠
- 更高级的 decomposition 未来应使用新契约，而不是过载 `delta_share`
