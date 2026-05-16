# forecast 原子意图 Schema

本文档定义 `forecast` 原子意图的拟议类型契约。

状态：draft design。本文是规划中的原子 `forecast` 意图 Schema 提案，不表示对应 HTTP endpoint 已经实现。

## 目的

`forecast` 用于把一个已定义的单时间序列观测投影到有界未来范围，并返回带不确定性的类型化预测值。

设计目标：

- 让 `forecast` 聚焦前向投影（forward projection），不承担解释（explanation）或规划（planning）
- 消费显式上游观测（observations），而不是原始 metric-plus-scope 输入
- 显式表达可预测性（forecastability）、预测期长度（horizon）语义与不确定性（uncertainty）
- 保持输出类型稳定，便于下游推理

## 核心设计决策

`forecast` 消费一个 `observe(time_series)` 输出，而不是原始 metric 名、time scope、SQL 片段或任意模型配置（model config）。

数据流因此保持清晰：

- `observe` 定义历史序列
- `forecast` 定义该序列上的未来投影

v1 明确排除：

- 因果预测（causal forecasting）
- 情景规划（scenario planning）
- 干预模拟（intervention simulation）
- 多序列联合预测（multi-series joint forecasting）
- 任意模型超参数（model hyperparameters）
- 预算分配（budget allocation）或目标求解（target-solving）
- 节假日（holidays）/ 支出（spend）/ 活动标记（campaign flags）等外生回归变量（exogenous regressors）

原子分析意图在这里的目标，是对单条有界历史序列提供稳定的前向投影（forward projection）。

## 工件标识（Artifact Identity）与谱系（Lineage）

`forecast_series` 是不可变规范工件（immutable canonical artifact）。

标识边界（identity boundary）绑定以下输入：

- `source_ref` 指向的 observe artifact lineage
- `horizon`
- runtime 自动选择的 `profile`
- 归一化后的 `interval_level`
- `artifact_schema_version`
- `derivation_version`

以下内容不得进入 artifact identity：

- projection 截断或展示参数
- explanation 文本
- execution timestamp
- engine 选择
- `execution_metadata.model_family`

本契约必须显式区分：

- 重读同一 artifact：同一 identity，同一 lineage
- 重新执行同一请求：可产生新的 execution record；若 source lineage、artifact schema version 与 derivation version 未变，则不产生新的 canonical artifact identity
- source lineage、artifact schema version 或 derivation version 变化：必须产生新的 `forecast_series` artifact

v1 默认：

- artifact 不允许跨 lineage 复用 identity
- artifact 为 immutable，不支持 session 内覆盖更新
- `forecast` 只能引用同 session 内已完成的 `observe` artifact

## Reference Contract

`forecast` 优先消费 typed artifact reference，而不是裸字符串 step id。

AOI 协议支持通过 `source_artifact_id` 直接引用 observe artifact，此时 `source_ref` 可从 artifact 元数据自动解析。

```ts
type ObservationArtifactRef = {
  step_type: "observe";
  session_id: string;
  step_id: string;
  artifact_id: string;
  observation_type: "time_series";
};
```

引用约束：

- `source_ref` 必须指向已完成步骤产出的 canonical `observe` artifact
- `observation_type` 在 v1 中必须是 `time_series`
- 不允许 projection ref 充当 canonical source ref
- v1 不允许跨 session ref
- v1 允许引用同 session 内的历史已完成 artifact
- 引用图必须保持 DAG；`forecast` 不允许直接或间接回指依赖自己的对象

## Request Shape

```json
{
  "step_type": "forecast",
  "source_ref": {
    "step_type": "observe",
    "session_id": "sess_123",
    "step_id": "step_obs_dau_daily",
    "artifact_id": "obs_artifact_dau_daily",
    "observation_type": "time_series"
  },
  "horizon": 14,
  "interval_level": 0.95
}
```

## Typed Schema

```ts
type ForecastRequest = {
  step_type: "forecast";
  source_ref: ObservationArtifactRef;
  source_artifact_id?: string;
  horizon: number;
  interval_level?: number | null;
};

type ObservationArtifactRef = {
  step_type: "observe";
  session_id: string;
  step_id: string;
  artifact_id: string;
  observation_type: "time_series";
};

```

## 输入规则

v1 支持的输入形态如下：

- `source_ref` 必须解析到已完成的 `observe`，且 `observation_type = "time_series"`
- source observation 不得是 segmented
- source series 必须使用规则、受支持的 `granularity`
- v1 支持 `{"hour", "day", "week", "month"}`
- source 必须是完整 artifact，而不是 projection
- `horizon` 必须是正整数
- `interval_level` 必须在 `(0, 1)` 内

输出类型：`forecast_series`

推荐默认值：

- runtime 自动选择 `profile`
- `interval_level = 0.95`

## v1 不支持的输入

- 直接传 `metric + time_scope`
- `scalar`、`segmented`、`numeric_sample_summary`、`rate_sample_summary`
- 多个 source refs
- 直接传裸 `artifact_id`
- projection ref 作为输入
- 显式 exogenous regressors
- 直接暴露模型名，如 `arima`、`prophet`、`lstm`
- 自定义 season length、holiday、regularization 等参数
- `best_case / worst_case` 这类情景分支
- 概率路径样本而非 bounded interval
- 跨层级序列对账
- 按维度批量预测

推荐错误码：`INVALID_ARGUMENT`。

## 非法组合

- `horizon <= 0`
- `interval_level != null && (interval_level <= 0 || interval_level >= 1)`
- `source_ref.session_id` 与当前 step 所在 session 不一致
- `source_ref` 解析到 projection-only、incomplete 或未完成 artifact

推荐错误码：`INVALID_ARGUMENT`。

## 归一化规则

- `interval_level = null` 或缺失时，归一化为 `0.95`

## 字段语义

### source_ref

指向先前 `observe(time_series)` 步骤的完整 artifact，定义历史序列。

每次请求只消费一条历史序列。这是刻意设计：

- 多序列预测属于 workflow concern
- 单序列范围让 validation、horizon semantics 与 provenance 更稳定

### horizon

表示从最后一个已观测 bucket 之后开始，要向未来投影多少个 bucket。

例如：

- `day` 粒度且 `horizon = 14`，返回 14 个未来日 bucket
- `week` 粒度且 `horizon = 8`，返回 8 个未来周 bucket

系统必须强制执行最大 horizon 上限；v1 上限为 90。过长请求应失败，接近上限的请求可成功但返回 `long_horizon_warning`。

### runtime-selected profile

`profile` 是 runtime 为 artifact 记录的投影策略，不是 public request 参数。

v1 支持：

- `level`
- `trend`

runtime 会根据可用历史点数自动选择当前 artifact 的 `profile`。不同执行引擎可以选择不同内部方法，只要响应语义一致。

### interval_level

表示预测区间的置信水平。

契约要求：

- point forecast 不应依赖 `interval_level`
- 改变 `interval_level` 只改变 interval 宽度，不改变历史拟合语义
- `interval_level` 越高，预测区间应弱单调变宽
- v1 不保证每个 runtime-selected profile / implementation 都一定有 interval

## 校验语义

### 硬校验

以下情况应直接失败：

- source step 不存在
- source 不是 `time_series`
- granularity 不受支持
- 可用历史点数不足以支撑 runtime-selected profile
- source series 存在阻断性 completeness 问题
- runtime-selected profile 与序列形态不兼容

推荐错误码：

- `INVALID_ARGUMENT`
- `STEP_NOT_FOUND`
- `UNSUPPORTED_OPERATION`
- `INSUFFICIENT_HISTORY`

### 软校验

以下情况可以带 warning 成功返回：

- source series 有部分不完整，但仍可预测
- 前导或尾部缺失值被确定性丢弃
- runtime 自动选择回退 profile
- 所选方法无 interval，故返回 `prediction_interval = null`
- 请求的 horizon 相对历史过长

这些 warning 必须进入 `forecastability.issues`。

## 可预测性契约

只有当输入序列能在清晰契约下支撑可辩护的 forward projection 时，`forecast` 才合法。

系统至少要检查：

- 继承自 `observe` 的规则 bucket 语义
- runtime-selected profile 所需的足够历史长度
- 历史序列完整性是否可接受
- granularity 与 runtime-selected profile 是否语义兼容
- horizon 是否在系统上限内
- source 是否是完整工件而非 projection

系统应返回：

- `forecastable`
- `needs_attention`
- `not_forecastable`

推导规则：

- 只要存在阻断性 issue，`status` 必须为 `not_forecastable`
- 不存在阻断性 issue、但存在至少一个 warning issue 时，`status` 必须为 `needs_attention`
- 仅当 `issues = []` 时，`status` 才能为 `forecastable`
- 成功响应不得包含 `severity = "error"` 的 issue

Marivo 推荐默认行为是拒绝 `not_forecastable`。

## 预测语义

结果必须始终包含：

- 选定 profile
- 用于投影的 source history summary
- 每个未来 bucket 一条预测记录
- 每条记录的 `point_forecast`
- 在可定义时的 interval bounds
- `forecastability` 元数据
- 明确的 provenance 与执行元数据

定义：

- `point_forecast` 是该 bucket 的中心预测值
- `prediction_interval.lower / upper` 表示在 `interval_level` 下未来值的不确定性区间
- `bucket_index = 1` 表示紧接观测序列后的第一个未来 bucket

规则：

- 预测 bucket 必须从最后一个观测 bucket 之后立即开始
- 未来 bucket 边界继承 `observe` 的半开区间语义
- 预测 bucket 继承 source 的 bucket 与 timezone 语义，不得另立新时区契约
- 如果 interval 不可用，必须显式返回 `prediction_interval = null`
- 如果 point forecast 本身都无法可辩护地产生，请求应失败

## Response Shape

```ts
type ForecastResponse = ForecastSeriesObservation;

type StepRef = {
  session_id: string;
  step_id: string;
  step_type: "forecast";
};

type ForecastSourceLineage = {
  source_artifact_ref: ObservationArtifactRef;
  source_schema_version: string;
  source_metric_contract_version: string | null;
};

type ForecastProfile = "level" | "trend";

type ForecastSeriesObservation = {
  step_ref: StepRef;
  artifact_id: string;
  artifact_schema_version: string;
  derivation_version: string;
  observation_type: "forecast_series";
  metric: string;
  source_ref: ObservationArtifactRef;
  source_granularity: TimeGranularity;
  source_time_scope: ResolvedRangeTimeScope;
  profile: ForecastProfile;
  interval_level: number;
  forecastability: ForecastabilityMetadata;
  history_summary: ForecastHistorySummary;
  forecast: ForecastBucket[];
  source_lineage: ForecastSourceLineage;
  analytical_metadata: ForecastAnalyticalMetadata;
  execution_metadata: ForecastExecutionMetadata;
};

type ResolvedRangeTimeScope = {
  kind: "range";
  start: string;
  end: string;
};

type TimeGranularity = "hour" | "day" | "week" | "month";

type ForecastabilityIssue = {
  code:
    | "step_not_found"
    | "unsupported_observation_type"
    | "granularity_unsupported"
    | "insufficient_history"
    | "data_incomplete"
    | "missing_bucket"
    | "irregular_series"
    | "profile_incompatible"
    | "projection_not_allowed"
    | "cross_session_ref"
    | "interval_unavailable"
    | "long_horizon_warning";
  severity: "error" | "warning";
  message: string;
};

type ForecastabilityMetadata = {
  status: "forecastable" | "needs_attention" | "not_forecastable";
  issues: ForecastabilityIssue[];
};

type ForecastHistorySummary = {
  observed_points: number;
  usable_points: number;
  dropped_points: number;
  last_observed_window: {
    start: string;
    end: string;
  };
};

type ForecastBucket = {
  bucket_index: number;
  window: {
    start: string;
    end: string;
  };
  point_forecast: number;
  prediction_interval: {
    level: number;
    lower: number | null;
    upper: number | null;
  } | null;
};

type ForecastAnalyticalMetadata = {
  timezone: string | null;
  data_complete: boolean | null;
  trend_assumption: "none" | "included" | "auto";
  seasonality_assumption:
    | "none"
    | "included"
    | "auto"
    | "not_applicable";
};

type ForecastExecutionMetadata = {
  engine: string;
  executed_at: string;
  model_family: string;
};
```

字段说明：

- `step_ref` 与 `artifact_id` 是下游引用完整 artifact 的稳定身份字段；其中 `artifact_id` 是权威 lineage 入口，`step_ref` 提供 typed step lineage
- `source_lineage` 用于 machine-readable provenance，记录输入 artifact 及其 source contract version
- `history_summary.dropped_points` 只统计为使序列可预测而被确定性排除的历史 bucket
- 不得把 hidden outlier removal 之类的再分析写进 `dropped_points`
- `history_summary.usable_points` 是 runtime-selected profile eligibility 与最小历史校验所依据的点数
- `forecast` artifact 必须按 `bucket_index ASC` 返回完整未来 bucket 序列；artifact 不得把 top-k 或 tail truncation 编码进 canonical 输出
- `execution_metadata.model_family` 仅是 provenance 信息，不是稳定的用户契约

## Null 与 Empty 语义

- `forecastability.issues = []` 表示 no known forecastability issues
- `prediction_interval = null` 的唯一语义是当前 runtime-selected profile / implementation 下 interval 不可定义或不可用；不得表示 point forecast 失败
- `prediction_interval.lower = null` 或 `upper = null` 的唯一语义是区间对象存在，但对应边界在当前 contract 下不可解析
- `analytical_metadata.timezone = null` 表示 source observation 未声明稳定 timezone 语义
- `analytical_metadata.data_complete = null` 表示 source completeness 当前 unknown，而不是 false
- `seasonality_assumption = "not_applicable"` 仅用于当前 runtime-selected profile 或 granularity 下 seasonality 语义不成立；不使用 `null` 表达该状态
- `last_observed_window` 为 total 字段；若无法确定最后观测窗口，则请求必须失败，而不是返回 `null`

## 错误语义

- `INVALID_ARGUMENT`
  请求形状非法、typed ref 非法或参数组合不合法
- `STEP_NOT_FOUND`
  `source_ref` 无法解析
- `UNSUPPORTED_OPERATION`
  source 合法，但 runtime-selected profile / granularity 组合不受支持
- `INSUFFICIENT_HISTORY`
  历史长度不足

## Agent Consumption Contract

- 下游步骤必须引用完整 artifact，不得引用 projection
- `forecast[]` 的默认读取顺序是 `bucket_index ASC`
- 长 horizon projection 可以做前缀截断或窗口摘要，但必须显式披露 truncation
- projection 至少应保留：`artifact_id`、`step_ref`、`metric`、`profile`、`interval_level`、`forecastability`、可见 forecast bucket 与 truncation disclosure
- projection 不得隐藏 blocking issue，不得改写 point forecast 或 interval 语义
- agent 读取单个 `forecast` artifact 的局部最小闭包应至少包含：身份字段、`source_ref`、`source_lineage`、`profile`、`interval_level`、`forecastability`、`history_summary` 与 `forecast`

## 负向契约

以下状态必须视为非法：

- 用 projection ref 替代 canonical source ref
- 使用跨 session ref
- 在 source lineage 或 version boundary 已变化时复用旧 artifact identity
- `forecastability.status = "forecastable"`，但 `issues` 非空
- 成功响应包含 `severity = "error"` 的 issue
- `point_forecast = null`
- `last_observed_window = null`
- artifact 级输出隐式省略部分 future buckets 却不披露 truncation

## Artifact 与 Projection

本文档定义的是 `forecast` 的完整 artifact semantics。

projection 可以：

- 压缩长 horizon 输出
- 摘要展示前几个未来 bucket
- 凝练 forecastability warnings

但不得：

- 改写 point forecast 或 interval 语义
- 省略阻断性 forecastability 问题却保留确定性结论
- 把预测结果表述为保证发生的未来事实
- 替代 artifact 自身成为下游 typed reference 的目标
