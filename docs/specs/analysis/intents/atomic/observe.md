# observe 原子意图 Schema

本文档定义 `observe` 原子意图的拟议类型契约。

状态：draft design。本文是规划中的原子 `observe` 意图 Schema 提案，不表示对应 HTTP endpoint 已经实现。

## 目的

`observe` 是 Marivo 中稳定的证据入口步骤，用于在类型化观测契约下读取一个语义指标（semantic metric）。

设计目标：

- 用单一步骤承载核心观测意图
- 通过类型参数区分输出模式，而不是拆成多个步骤
- 让输出对下游步骤和 agent 都是自描述的
- 把 SQL 保持为实现细节，而不是外部契约

## Request Shape

```json
{
  "step_type": "observe",
  "metric": "dau",
  "result_mode": "standard",
  "time_scope": {
    "kind": "range",
    "start": "2024-03-01T00:00:00",
    "end": "2024-03-08T00:00:00"
  },
  "calendar_policy_ref": "calendar_policy.holiday_yoy",
  "scope": {
    "predicate": {
      "op": "and",
      "items": [
        { "field": "country", "op": "eq", "value": "CN" },
        { "field": "platform", "op": "in", "value": ["iOS", "Android"] }
      ]
    }
  },
  "granularity": null,
  "dimensions": null
}
```

**注：上述示例覆盖 2024-03-01 至 2024-03-07（7 天），`end` 为排他边界。若需覆盖 3 月 1-7 日 inclusive，请使用 `end = "2024-03-08"`。**

## Typed Schema

```ts
type ObserveRequest = {
  step_type: "observe";
  metric: string;
  result_mode?: "standard";
  time_scope: TimeScope;
  calendar_policy_ref?: string | null;
  scope?: Scope | null;
  granularity?: TimeGranularity | null;
  dimensions?: string[] | null;
};

type TimeScope =
  | { kind: "range"; start: string; end: string; field?: string }
  | { kind: "snapshot_now"; field?: string }
  | { kind: "latest_available"; field?: string }
  | { kind: "as_of"; at: string; field?: string };

type Predicate =
  | { op: "and"; items: Predicate[] }
  | { op: "or"; items: Predicate[] }
  | {
      field: string;
      op:
        | "eq"
        | "neq"
        | "in"
        | "not_in"
        | "gt"
        | "gte"
        | "lt"
        | "lte"
        | "between"
        | "is_null"
        | "is_not_null";
      value?: string | number | boolean | string[] | number[];
    };

type Scope = {
  constraints?: Record<string, string | number | boolean | null> | null;
  predicate?: Predicate | null;
};

type TimeGranularity = "hour" | "day" | "week" | "month";
```

## 模式规则

`observe` 在 v1 中共有三种合法输出契约，均由 `result_mode = "standard"` 产出。

### Standard

- `result_mode = "standard"` 或省略

`standard` 是唯一支持的观测家族，沿用标量（scalar）/ 时间序列（time-series）/ 分段（segmented）三种输出分支；最终输出类型由 `granularity` 与 `dimensions` 决定。

### Scalar

- `result_mode = "standard"`
- `granularity = null`
- `dimensions = null`

返回在解析后 scope 内的单个指标值。

### Time Series

- `result_mode = "standard"`
- `granularity != null`
- `dimensions = null`

按时间桶（time bucket）返回序列。

### Segmented

- `result_mode = "standard"`
- `granularity = null`
- `dimensions.length >= 1`

按维度键返回分段结果。

归一化规则：

- `dimensions = []` 归一化为 `null`，因此回退到 `scalar`

> **注**：`numeric_sample_summary` 和 `rate_sample_summary` result_mode 曾在早期设计中规划，但当前实现只支持 `result_mode = "standard"`。推断摘要功能（inferential summary）将在未来版本中按独立契约引入。

## 非法组合

- `granularity != null && dimensions != null`
- `time_scope.kind in {"snapshot_now", "latest_available", "as_of"}` 且 `granularity != null`
- `result_mode != "standard"`（当前实现只支持 `standard`）
- `scope.predicate` 包含时间条件
- `scope.constraints` 或 `scope.predicate` 引用非该 metric semantic scope 内的字段

推荐错误码：`INVALID_ARGUMENT`。

## 字段语义

### metric

已发布的语义指标（semantic metric）名称。该 metric 必须存在于语义层，并声明自己支持哪些：

- `time_scope.kind`
- observation modes（观测模式）

### result_mode

用于指定 `observe` 应输出哪种类型化观测契约。

v1 只支持 `standard`。`result_mode` 改变的是 artifact semantics（工件语义），而不是简单展示样式。

### time_scope

`time_scope` 是必填项，用于定义观测的时间语义。

**重要：`time_scope` 必须是结构化对象，不接受简写字符串。**

**AOI 对齐：** 当通过 AOI 协议调用时，`time_scope` 使用 `{field, start, end}` 形式（`field` 为必填的时间字段名），不含 `kind` 字段。运行时自动将其映射为 `kind = "range"` 的内部语义。

```ts
// CORRECT: 结构化对象
{ "kind": "range", "start": "2024-03-01", "end": "2024-04-01" }

// WRONG: 简写字符串（会被拒绝）
"2024-03-01~2024-03-31"
```

**时间范围采用半开区间语义：`[start, end)`，`end` 是排他边界。**

- 若需覆盖 2024 年 3 月整月（即 3 月 1 日至 3 月 31 日 inclusive），应使用：
  - `start = "2024-03-01"`
  - `end = "2024-04-01"`（下月首日）
- 若误用 `end = "2024-03-31"`，则只会覆盖 3 月 1 日至 3 月 30 日。

支持的 `kind` 类型：

- `range`：半开区间 `[start, end)`
- `snapshot_now`：查询时刻的即时快照
- `latest_available`：当前数据源中最新且稳定的点
- `as_of`：截至指定时间点的快照

响应必须返回 resolved time scope，而不是仅返回请求原文。

### calendar_policy_ref

`calendar_policy_ref` 是可选字段，用于在 `observe` 阶段显式选择固定的 calendar alignment policy。

v1 边界：

- 只接受 compiler-owned fixed catalog refs
- registry / resolution 顺序固定为：显式 request > 上游注入 binding > planner/agent 候选
- 若 planner/agent 候选同时存在多个同样合法的 policy，必须返回结构化歧义，而不是静默猜测
- 不接受开放式 policy payload、自定义 authoring schema 或临时 holiday matching 规则
- 下游 `compare`、`attribute`、`validate` 等 typed-ref intent 不重复接收该字段，而是复用 `observe` 已冻结的 resolved alignment metadata
- `hour` 粒度不支持 `calendar_policy_ref`
- `week` / `month` 观察可使用 `calendar_policy_ref`，但 compiler 内部仍按 `day` 生成 alignment pairing；请求粒度只决定 observation 的展示聚合形状
- 当 `time_series` 请求带 `calendar_policy_ref` 且成功完成对齐时，响应与 artifact 中的 `resolved_policy_summary` 必须为非空
- `day` 粒度的 aligned `time_series` 额外返回 `aligned_baseline_series` 与 `yoy_series`，供调用方直接绘制对齐 baseline 与 YoY 趋势
- `week` / `month` 粒度当前仅冻结 `resolved_policy_summary`，不直接返回 `aligned_baseline_series` / `yoy_series`
- `calendar_policy.weekday_wow` 表示“周内逐日 weekday 对齐后再聚合”，不是“整周黑盒对整周”

v1 固定 ref 集：

- `calendar_policy.natural_yoy`
- `calendar_policy.weekday_yoy`
- `calendar_policy.holiday_yoy`
- `calendar_policy.event_yoy`
- `calendar_policy.natural_mom`
- `calendar_policy.weekday_mom`
- `calendar_policy.event_mom`
- `calendar_policy.weekday_wow`

### scope

`scope` 用于定义被观测的总体（population），属于“观测对象是什么”的契约，而非展示选项。

规则：

- `time_scope` 是唯一时间窗口契约；时间条件不得写入 `scope`
- `scope.constraints` 用于标量实体或行级约束
- `scope.predicate` 只允许承载非时间条件
- `scope.constraints` 应优先使用 semantic ref key，例如 `dimension.cluster`；执行层会通过已发布 binding 自动映射到物理列名
- bare physical column key 仅作为兼容路径保留；新调用方不应依赖它表达 semantic scope
- 被引用字段必须属于该 metric 的 semantic scope，而不是未声明的物理列名
- 非法或语义上不支持的字段应抛出 `INVALID_FILTER`

响应中的 `scope` 必须返回归一化后的结构，而不是 `null`：

- `{}` 表示 overall / unsliced / 无额外非时间约束
- 不得用 `null` 同时表示 “overall” 和 “未知”
- 若请求省略 `scope`，响应必须归一化为 `{}` 或其等价 total 结构

### granularity

仅在时间序列（time_series）模式中合法，表示返回序列的 bucket 大小。

### dimensions

仅在分段（segmented）模式中合法，表示按哪些语义维度（semantic dimensions）切分。空数组会归一化为 `null`。

segment 排序由工件契约（artifact contract）固定为：

1. `value desc`
2. dimension-key lexical order

artifact 必须返回完整 segment 集合；消费端若有 top-k、截断或其他排序偏好，应放在投影层（projection layer）。

## 工件标识（Artifact Identity）与版本控制（Versioning）

本文档定义的是 `observe` 的完整工件语义（artifact semantics），而不是面向 agent 的压缩视图。

`observe` artifact 的标识边界（identity boundary）应至少绑定：

- 本次执行产生的 `artifact_id`
- 本次执行的类型化请求语义（typed request）
- 本次执行所绑定的源谱系（source lineage）

规则：

- `observe` artifact 绑定本次执行谱系（lineage）
- `observe` artifact 默认视为不可变（immutable）
- 重读同一 `artifact_id` 是读取同一谱系下的同一对象
- 重新执行 `observe` 即使请求语义相同，也应产生新 artifact，而不是与旧 artifact 隐式复用标识（identity）
- 若存在 calendar alignment，artifact payload 中的 `resolved_policy_summary` 是下游复用面；权威的 lineage / metadata 追溯面应写入 `step_metadata.typed_semantic_snapshot.compile_context.calendar_policy_binding`
- `resolved_policy_summary.bucket_pairing` 在 v1 仅作为该 observation artifact 的 frozen metadata 明细存在，不生成独立 artifact 或 typed ref；如需治理结论，见 `specs/semantic/calendar-bucket-pairing-artifact-decision.zh.md`
- 面向人工验收与回归核对的最小样例基线见 `specs/semantic/calendar-alignment-golden-cases.zh.md`

artifact contract 应显式区分：

- `schema_version`：`observe` artifact 自身的契约版本（contract version）
- `metric_contract_version`：该 metric 被解释时的语义契约版本
- `derivation_version`：抽取与归一化逻辑版本

## 响应形状（Response Shape）

所有响应都是观测对象（observation object），包含共享头部和类型特定载荷（payload）。

```ts
type ObserveResponse =
  | ScalarObservation
  | TimeSeriesObservation
  | SegmentedObservation;

type StepRef = {
  session_id: string;
  step_id: string;
  step_type: "observe";
};

type ObservationBase = {
  step_ref: StepRef;
  artifact_id: string;
  schema_version: string;
  metric_contract_version: string | null;
  derivation_version: string;
  observation_type:
    | "scalar"
    | "time_series"
    | "segmented";
  metric: string;
  time_scope: ResolvedTimeScope;
  scope: Scope;
  unit: string | null;
  resolved_policy_summary: ResolvedPolicySummary | null;
  analytical_metadata: AnalyticalMetadata;
  execution_metadata: ExecutionMetadata;
};

type ResolvedTimeScope =
  | { kind: "range"; start: string; end: string }
  | { kind: "snapshot_now"; observed_at: string }
  | { kind: "latest_available"; data_as_of: string }
  | { kind: "as_of"; at: string };

type AnalyticalMetadata = {
  additive_dimensions: string[];
  aggregation_semantics: string;
  timezone: string | null;
  data_complete: boolean | null;
  quality_status: "ready" | "needs_attention" | "not_ready";
  row_count: number | null;
  sample_size: number | null;
  null_rate: number | null;
};

type ExecutionMetadata = {
  query_hash: string;
  engine: string;
  executed_at: string;
};

type ResolvedPolicySummary = {
  policy_ref: string;
  comparison_basis: "yoy" | "mom" | "wow";
  resolved_calendar_source: string;
  resolved_calendar_version: string;
  resolved_baseline_generation_rule: {
    strategy:
      | "previous_year"
      | "previous_period";
    offset_value: number | null;
    offset_unit: "day" | "week" | "month" | "quarter" | "year" | null;
    fixed_start: string | null;
    fixed_end: string | null;
    named_window_ref: string | null;
  };
  current_window: { start: string; end: string };
  baseline_window: { start: string; end: string };
  bucket_pairing: Array<{
    current_bucket_start: string;
    baseline_bucket_start: string | null;
    pairing_reason:
      | "holiday_cluster"
      | "year_relative_holiday_key"
      | "event_cluster"
      | "year_relative_event_key"
      | "same_weekday_nearest"
      | "natural_date_shift"
      | "fallback";
    shift_days: number | null;
    issues: string[];
    strictness_level:
      | "strict"
      | "fallback"
      | "reused_baseline"
      | "coverage_incomplete";
    is_reused_baseline_bucket: boolean;
  }>;
  rollup_safe: boolean;
  coverage_summary: {
    aligned_bucket_count: number;
    unpaired_bucket_count: number;
    aligned_ratio: number;
  };
  data_coverage_summary?: {
    expected_bucket_count: number;
    present_bucket_count: number;
    missing_bucket_count: number;
    coverage_ratio: number;
    aligned_expected_bucket_count?: number;
    aligned_present_current_bucket_count?: number;
    aligned_present_baseline_bucket_count?: number;
    aligned_present_both_bucket_count?: number;
  };
  comparability_warnings: string[];
};

type ScalarObservation = ObservationBase & {
  observation_type: "scalar";
  value: number | null;
};

type TimeSeriesObservation = ObservationBase & {
  observation_type: "time_series";
  granularity: TimeGranularity;
  series: Array<{
    window: {
      start: string;
      end: string;
    };
    value: number | null;
  }>;
};

type SegmentedObservation = ObservationBase & {
  observation_type: "segmented";
  dimensions: string[];
  segments: Array<{
    keys: Record<string, string | number | boolean | null>;
    value: number | null;
    share: number | null;
  }>;
  segmented_yoy?: Array<{
    keys: Record<string, string | number | boolean | null>;
    current_value: number | null;
    baseline_value: number | null;
    absolute_delta: number | null;
    relative_delta: number | null;
  }>;
  scope_value: number | null;
};
```

## 响应说明

- 时间序列 bucket 采用半开区间（half-open interval）语义：`[window.start, window.end)`
- `resolved_policy_summary.coverage_summary` 只表示 calendar bucket pairing coverage，不表示业务数据已完整落库
- `resolved_policy_summary.data_coverage_summary` 表示实际 metric bucket coverage；当请求窗口内某些 bucket 尚无业务数据时，必须通过该字段显式暴露
- `resolved_policy_summary.bucket_pairing[*].strictness_level` 与 `is_reused_baseline_bucket` 用于显式说明该 bucket 是否仍可被解释为严格 1:1 对齐；`fallback` 或 baseline bucket 复用都不得再被表述为“严格对齐”
- `resolved_policy_summary.rollup_safe=false` 表示本次 bucket pairing 不应被解释为严格对齐后可安全 rollup 的窗口；常见触发条件包括 fallback、baseline bucket 复用或 coverage 不完整
- `time_series.series` 必须按请求窗口返回完整 bucket 序列；缺失 bucket 不得静默省略，应用 `value = null` 表达
- `segmented` 的 `share` 表示该 segment 在当前 scope 中的占比
- `segmented` artifact 必须返回完整 segment 集合，不得把 tail-folding 或 top-k 截断编码进 artifact
- 样本摘要模式的核心输出是 `sample_summary`，不是业务汇总值
- `step_ref` 与 `artifact_id` 是下游引用完整 artifact 的稳定身份字段；其中 `artifact_id` 是权威谱系（lineage）入口，`step_ref` 提供类型化步骤谱系（typed step lineage）
- `quality_status` 只表达单边质量与可消费性提示，不表达双边可比性（comparability）结论
- `value`、`share`、`unit`、`timezone`、`row_count`、`sample_size`、`null_rate` 的 `null` 必须保持单义：仅表示该字段对当前 observation `not_applicable`、`unknown` 或 `not_yet_resolved` 中被文档显式声明的一种，不能混用

## 分析元数据语义

### quality_status

`quality_status` 是规则推导字段，不是自由文本评语。

最低要求：

- `ready`：当前观测（observation）满足默认消费条件，无已知硬阻断
- `needs_attention`：当前观测可消费，但存在必须显式暴露的质量告警
- `not_ready`：当前观测不满足安全消费条件，调用方不应把它当作无保留硬证据

该字段的推导至少应综合：

- `data_complete`
- `sample_size`
- `row_count`
- `null_rate`
- metric 或 source 侧的已知质量告警

对于 `observe(time_series)`：

- 若响应为了覆盖请求窗口而回填了 `value = null` bucket，则 `analytical_metadata.data_complete` 必须为 `false`
- 同一场景下 `quality_status` 至少应为 `needs_attention`，不得继续返回 `ready`
- 若请求窗口 bucket 全部有值，则 `data_complete` 应为 `true`

若实现无法稳定计算某项辅助信号，可以令辅助字段为 `null`，但 `quality_status` 自身不得因此退化成未定义的自由裁量字段。

## 校验规则

- `metric` 必须存在，并支持所请求的 `time_scope` 与 `result_mode`
- `time_scope` 为必填，且响应中必须返回解析后的时间范围
- `granularity` 与 `dimensions` 不得同时出现
- `scope` 不能包含时间条件
- `result_mode` 只支持 `"standard"`；其他值将被拒绝
- `dimensions = []` 必须确定性归一化为 `null`

## 错误语义

- `INVALID_ARGUMENT`
  请求形状非法、模式组合非法或参数不受支持
- `INVALID_FILTER`
  `scope` 中包含无效字段、无效操作符或不受支持的语义条件
- `UNSUPPORTED_OPERATION`
  metric 存在，但不支持请求的 observation contract

## 下游兼容性说明

- `compare` 应消费 `observe` 产出的观测引用（observation refs）
- `correlate` 只应消费 `observe(time_series)`
- `test` 消费推断就绪（inferential-ready）的 `observe` 工件（当前实现尚未支持推断摘要输出模式）
- `forecast` 只应消费完整的 `observe(time_series)` artifact，而不是投影（projection）
- 投影不是下游类型化引用（typed reference）的合法目标；下游步骤必须引用完整 artifact

## 工件（Artifact）与投影（Projection）

本文档定义的是 `observe` 的完整工件语义（artifact semantics）。

未来面向 agent 的投影（agent-facing projection）可以：

- 截断 `segmented` 的长尾分段，并显式披露截断元数据
- 压缩长时间序列（time-series）的展示形式
- 用摘要形式重述样本统计

但投影不得：

- 改变观测类型（observation type）
- 改写 `time_scope` 或 bucket 语义
- 把普通观测伪装成推断摘要（inferential summary）（推断摘要在当前实现中尚未支持）
- 生成新的证据声明
- 替代 artifact 自身成为下游类型化引用（typed reference）的目标
