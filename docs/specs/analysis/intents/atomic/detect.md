# detect 原子意图 Schema

本文档定义 `detect` 原子意图的拟议类型契约。

状态：draft design。本文是规划中的原子 `detect` 意图 Schema 提案，不表示对应 HTTP endpoint 已经实现。

## 目的

`detect` 用于在类型化时间范围内扫描单个指标，并返回排序后的异常候选（anomaly candidates）。

设计目标：

- 让 `detect` 聚焦候选发现（candidate discovery），不承担诊断（diagnosis）或解释（explanation）
- 复用 AOI 统一的 `time_scope` / `filter` 契约，而不是自定义平行过滤形状
- 暴露稳定的产品契约，而不是泄漏建模内部细节
- 显式表达可检测性（detectability）、截断（truncation）、扫描边界（scan boundary）与谱系（lineage）
- 区分候选标记（candidate flags）与已确认证据（confirmed evidence），维持确定性下游推理边界

## 核心设计决策

`detect` 是候选扫描步骤（candidate-scanning step），不是异常证明步骤（anomaly-proof step）。

v1 明确排除：

- 根因分析
- 因果解释
- 任意检测器（detector）配置
- 多指标筛查
- 无约束的多维爆炸
- “这一定是异常”的声明级（claim-level）语义

原子分析意图在这里的边界更窄：识别哪些 metric-time-slice 点值得进一步分析。

## Request Shape

```json
{
  "step_type": "detect",
  "metric": "dau",
  "time_scope": {
    "field": "event_date",
    "start": "2024-03-01T00:00:00Z",
    "end": "2024-04-01T00:00:00Z"
  },
  "granularity": "day",
  "strategy": "point_anomaly",
  "sensitivity": "aggressive"
}
```

## Typed Schema

```ts
type DetectRequest = {
  step_type: "detect";
  metric: string;
  time_scope: DetectTimeScope;
  granularity: TimeGranularity;
  filter?: Expression;
  dimension?: string;
  strategy: DetectStrategy;
  sensitivity?: DetectSensitivity;
  limit?: number;
};

type DetectTimeScope = {
  field: string;
  start: string;
  end: string;
};

type Expression = {
  dialects: Array<{ dialect: string; expression: string }>;
};

type TimeGranularity = "hour" | "day" | "week" | "month";

type DetectStrategy = "point_anomaly" | "period_shift";

type DetectSensitivity = "conservative" | "balanced" | "aggressive";
```

说明：

- AOI 协议的 `time_scope` 使用 `{field, start, end}` 形式，`field` 为必填时间字段名；Marivo runtime 内部可映射为 range 窗口。
- `granularity` 使用与 `observe.granularity` 相同命名。
- `filter` 使用 AOI/OSI 风格的结构化表达式；时间条件不得进入 `filter`。
- `dimension` 是可选的单维扫描轴，不是过滤条件；它定义“按哪个 semantic dimension 拆成独立序列扫描”。
- `strategy` 必填，控制候选形态：`point_anomaly` 为窗口内 z-score 点异常，`period_shift` 为当前窗口与 previous-adjacent baseline 的整体变化。
- `sensitivity` 是三档枚举，省略时默认 `aggressive`。

## 输入规则

v1 支持的输入形态如下：

- `metric` 必须解析到已发布的 semantic metric。
- `time_scope.field/start/end` 必须存在，`start/end` 必须是合法半开区间 `[start, end)`。
- `granularity` 必须来自受支持的时间粒度。
- `filter` 若提供，必须是结构化表达式。
- `dimension` 可选；若提供，则必须是单个 semantic dimension 字符串。
- `strategy` 必须是 `point_anomaly` 或 `period_shift`。
- `sensitivity` 若提供，必须是 `conservative`、`balanced`、`aggressive` 之一。
- `limit` 限制 artifact 中返回的候选数量。

输出类型：`anomaly_candidates`

## v1 不支持的输入

- legacy `time_scope.kind` / `mode` / `current` 形状。
- 快照式或相对式 time scope 别名；调用方必须传显式 range。
- 多个 `dimension`。
- 旧字段 `split_by`、`profile`、`patterns`。
- 内部/legacy `scope` 或请求级 `max_series` 参数；公开过滤条件只使用 AOI `filter`。
- 显式指定任意 baseline window。
- 直接暴露算法名，如 `zscore`、`iqr`、`stl`。
- 用户定义 seasonality、holiday、changepoint 等参数。
- 多指标请求。
- 需要 claim-level anomaly confirmation 的请求。

推荐错误码：`INVALID_ARGUMENT`。

## 非法组合

- `time_scope.start >= time_scope.end`。
- `filter` 包含时间轴条件。
- `dimension` 以数组形式提供。
- `limit != null && limit <= 0`。
- 指标不支持按请求的 `dimension` 拆分。
- `strategy = "period_shift"` 但无法构造 previous-adjacent baseline。

推荐错误码：`INVALID_ARGUMENT`。

## 归一化规则

- 省略 `sensitivity` 归一化为 `"aggressive"`。
- 省略 `filter` 表示 no-extra non-time filter。
- 省略 `dimension` 表示 overall scan。
- 省略 `limit` 表示由实现决定默认返回数量。
- 显式 `null` optional fields 不属于 AOI detect 请求契约。
- `dimension != null` 时，runtime 可使用内部默认 `max_series` 控制有界扇出，但 `max_series` 不属于公开 AOI/MCP detect contract。

## 字段语义

### metric

已发布的 semantic metric 名称。每次请求只扫描一个指标。

这是刻意设计：

- 多指标异常筛查属于 workflow concern，不属于原子步骤。
- 单指标范围让校验、排序、provenance 与 candidate lineage 更稳定。

### time_scope

AOI request 使用 `{field, start, end}`，表示用于扫描的时间字段和绝对时间范围。

`detect` 回答的问题是：

- 在这个请求窗口内，哪些点或子窗口值得关注？

它不回答：

- 为什么会发生？
- 是否已形成 confirmed anomaly fact？

顶层 `granularity` 定义扫描粒度。粒度是异常定义的一部分。

同一个点在 `day` 粒度下可能异常，在 `week` 粒度下可能正常。

### filter

可选的结构化 non-time filter。它只负责约束要扫描的总体，例如 region、平台或实体子集。

它不负责表达“按哪个维度拆成独立序列”。该职责属于 `dimension`。

### dimension

可选的单个 semantic dimension，用来把 metric 在请求 filter 内拆成独立子序列。

v1 只支持一个维度，因为：

- 单维拆分仍具有稳定的 truncation 与 provenance 语义。
- 多维拆分会引入组合爆炸与交互语义，应另立契约。

### strategy

必填的检测策略。

- `point_anomaly`：在请求窗口内按时间桶扫描点异常。
- `period_shift`：把整个请求窗口和 previous-adjacent 等长基线窗口比较，定位整体水平变化。

`strategy` 是面向 agent 的语义选择，不暴露底层算法参数。

### sensitivity

面向用户的告警严格度。

取值：

- `conservative`
- `balanced`
- `aggressive`

默认值：`aggressive`。

契约要求：

- 在固定 `metric + time_scope + filter + dimension + strategy` 下，灵敏度越高，candidate set 只能变大不能变小。
- `conservative` 必须是 `balanced` 的子集。
- `balanced` 必须是 `aggressive` 的子集。

### limit

用于限制 artifact 中返回的候选行数量。被截断候选必须在响应中显式交代。

## 校验语义

`detect` 同时需要语法校验和语义校验。

### 硬校验

以下情况应直接失败：

- metric 不存在
- metric 不支持所请求的 `granularity`
- metric 不支持所请求的 `dimension`
- `time_scope` range 对目标 strategy 来说过短
- 请求无法被 bounded fan-out（有界扇出）
- strategy 与指标时间语义不兼容

推荐错误码：

- `INVALID_ARGUMENT`
- `UNSUPPORTED_OPERATION`
- `INSUFFICIENT_HISTORY`

### 软校验

以下情况可以带 warning 成功返回：

- `max_series` 导致部分合格序列被排除
- `limit` 导致候选截断
- 某些候选序列因为点数不足被跳过
- 扫描基于部分不完整数据
- `strategy` 触发了内部回退策略

这些 warning 必须进入 `detectability.issues`。

## 检测契约

只有当请求的 metric 与 scan shape 能支撑可辩护的 candidate scan 时，`detect` 才成立。

系统至少要检查：

- metric 支持所请求的 `granularity`
- metric 支持所请求的 `dimension`
- 解析出的序列对目标 strategy 拥有足够点数
- 数据完整性足以支撑扫描
- 请求没有突破 bounded fan-out 规则
- strategy 与序列形态兼容

系统应返回：

- `detectable`
- `needs_attention`

Marivo 推荐默认行为是对不可检测请求直接报错，而不是返回 best-effort payload。

## Candidate Identity 与 Lineage

`detect` artifact 是 immutable typed artifact。

规则：

- 同一 artifact 被重读时，`candidate_ref` 必须稳定
- 同一请求重新执行并产生新 artifact 时，必须产生新 artifact lineage；新旧 candidate 不得复用 canonical identity
- canonical candidate identity 绑定 `artifact_ref + item_ref`，而不是绑定排序位置含义、projection 版本或 explanation 文本
- canonical layer 默认不做跨 artifact lineage 的 anomaly candidate 合并

`candidate_score`、`flag_level`、当前排序位置都不得进入 lineage 级 identity 边界。

## 候选语义

结果必须始终包含：

- 被扫描的 metric、`time_scope`、`filter` 与 `dimension`
- scan summary
- 每个候选的 machine-readable `candidate_ref`
- 每个候选的 `window`
- 每个候选的 `slice`
- 每个候选的 observed / expected value
- 在可定义时的绝对与相对偏差
- 用于排序的 `candidate_score`
- `flag_level`
- truncation metadata
- excluded-series metadata

重要边界：

- `detect` 返回的是 anomaly candidates，不是 confirmed anomaly facts
- `candidate_score` 是排序分数，不是概率
- `flag_level` 是分诊优先级，不是跨方法统一的严重程度定理

定义：

- `observed_value`：候选 bucket 或窗口中的实际指标值
- `expected_value`：与当前 strategy 一致的基线估计
- `deviation_abs = observed_value - expected_value`
- `deviation_pct = deviation_abs / expected_value`

若 `expected_value` 为 `0` 或 `null`，`deviation_pct` 必须为 `null`。

### Direction

根据 `deviation_abs` 推导：

- 正偏差 -> `up`
- 负偏差 -> `down`
- 零偏差 -> `flat`
- 无法定义 -> `undefined`

direction 属于 candidate payload 的一部分，而不是请求时过滤条件。

### Flag Level

推荐值：

- `low`
- `medium`
- `high`

`high` 仅表示在当前 strategy 与 sensitivity 下优先级高，不表示统一统计强度。

### Candidate Ranking Contract

排序必须稳定且确定。

默认排序：

1. `candidate_score desc`
2. `abs(deviation_abs) desc`
3. `window.start asc`
4. `slice` lexical order

### Excluded Series

在 `dimension != null` 时，系统不一定能扫描所有可能序列。

响应必须区分：

- `scanned_series_count`
- `eligible_series_count`
- `excluded_series_count`

## Response Shape

```ts
type DetectResponse = DetectCandidatesArtifact;

type DetectCandidatesArtifact = {
  artifact_type: "anomaly_candidates";
  artifact_schema_version: "v1";
  metric: string;
  time_scope: ResolvedDetectTimeScope;
  granularity: TimeGranularity;
  filter: Expression | null;
  dimension: string | null;
  strategy: DetectStrategy;
  sensitivity: DetectSensitivity;
  detectability: DetectabilityMetadata;
  scan_summary: DetectScanSummary;
  candidates: DetectCandidateItem[];
  truncation: DetectTruncation;
  analytical_metadata: DetectAnalyticalMetadata;
  provenance: DetectArtifactProvenance;
  execution_metadata: ExecutionMetadata;
};

type ResolvedDetectTimeScope = {
  kind: "range";
  start: string;
  end: string;
  field?: string;
};

type DetectabilityIssue = {
  code:
    | "metric_not_found"
    | "granularity_unsupported"
    | "split_dimension_unsupported"
    | "insufficient_history"
    | "insufficient_points"
    | "data_incomplete"
    | "strategy_incompatible"
    | "series_limit_applied";
  severity: "error" | "warning";
  message: string;
};

type DetectabilityMetadata = {
  status: "detectable" | "needs_attention";
  issues: DetectabilityIssue[];
};

type DetectScanSummary = {
  scanned_series_count: number;
  eligible_series_count: number;
  excluded_series_count: number;
  total_candidate_count: number;
};

type DetectArtifactRef = {
  session_id: string;
  step_id: string;
  step_type: "detect";
  artifact_id: string;
};

type ArtifactItemRef = {
  collection: "candidates";
  index: number;
  key: string | null;
};

type DetectCandidateRef = {
  artifact_ref: DetectArtifactRef;
  item_ref: ArtifactItemRef;
};

type DetectCandidateItem = {
  candidate_ref: DetectCandidateRef;
  candidate_type: "point_anomaly" | "period_shift";
  window: {
    start: string;
    end: string;
  };
  baseline_window?: { start: string; end: string } | null;
  slice: Record<string, string> | null;
  observed_value: number | null;
  expected_value: number | null;
  deviation_abs: number | null;
  deviation_pct: number | null;
  candidate_score: number;
  flag_level: "low" | "medium" | "high";
  direction: "up" | "down" | "flat" | "undefined";
};

type DetectTruncation = {
  returned_candidate_count: number;
  total_candidate_count: number;
  truncated: boolean;
};

type DetectAnalyticalMetadata = {
  timezone: string | null;
  data_complete: boolean | null;
  baseline_method: {
    strategy: DetectStrategy;
    methods: Record<DetectStrategy, string>;
  };
};

type DetectArtifactProvenance = {
  artifact_ref: DetectArtifactRef;
  source_metric_ref: string;
  detector_version: string;
  projection_ref: string | null;
};

type ExecutionMetadata = {
  query_hash: string;
  engine: string;
  executed_at: string;
};
```

## Nullability 与 Empty Semantics

本契约中的 nullable / empty 字段必须遵守单义语义：

- `filter = null`：no-extra non-time filter
- `dimension = null`：整体序列扫描，不做按维度拆分
- `candidate.slice = null`：该 candidate 对应整体序列，而不是“未知 slice”
- `observed_value = null`：该 candidate 的 observed value 在 artifact contract 下不可定义或未能可靠解析；不得同时表示 “0”
- `expected_value = null`：当前 strategy 下无法形成可辩护 expected baseline；不得同时表示 “0”
- `deviation_abs = null`：由于 `observed_value` 或 `expected_value` 不可定义，绝对偏差不可定义
- `deviation_pct = null`：相对偏差不可定义；唯一允许原因是 `expected_value = null` 或 `expected_value = 0`
- `analytical_metadata.timezone = null`：timezone 对该 artifact 不适用或未被 canonical contract 定义；不得同时表示 consumer 尚未读取
- `analytical_metadata.data_complete = null`：完整性尚未能被确定性判定；不得同时表示 `false`
- `detectability.issues = []`：no-known detectability issues

## Provenance 与 Versioning

`detect` artifact 必须显式区分：

- artifact schema version：`artifact_schema_version`
- detector / derivation version：`provenance.detector_version`
- projection ref：`provenance.projection_ref`

规则：

- projection ref 只能指向 bounded consumer view，不得替代 canonical `candidate_ref`
- 当 artifact contract 发生 breaking change 时，必须提升 `artifact_schema_version`
- 当检测逻辑在不改变 artifact contract 的前提下发生语义变化时，必须提升 `detector_version`

## Agent Consumption Contract

agent 直接消费 `detect` artifact 时，最小读取闭包至少包括：

- 顶层 `metric`
- 顶层 `time_scope`
- 顶层 `filter`
- 顶层 `dimension`
- 顶层 `detectability`
- 顶层 `scan_summary`
- 顶层 `truncation`
- 目标 `candidate`
- `candidate.candidate_ref`

推荐可查询轴：

- `metric`
- `time_scope`
- `filter`
- `dimension`
- `candidate.window`
- `candidate.slice`
- `candidate.flag_level`

稳定引用格式：

- 下游步骤或 evidence extractor 应使用 `candidate_ref`
- 不应仅依赖投影视图中的排序序号或裸字符串候选名

稳定截断规则：

- top-k 截断必须基于 canonical 排序规则执行
- 若调用 projection 只返回部分候选，projection 必须保留原 `candidate_ref`
- projection 不得隐藏 `max_series` 或 `limit` 造成的排除与截断

## 错误语义

- `INVALID_ARGUMENT`
  请求形状非法或组合不支持
- `UNSUPPORTED_OPERATION`
  metric 存在，但不支持对应 strategy / grain / dimension
- `INSUFFICIENT_HISTORY`
  历史长度不足，无法进行可辩护检测

## Artifact 与 Projection

本文档定义的是 `detect` 的完整 artifact semantics。

projection 可以：

- 截断候选列表
- 紧凑展示最高优先级候选
- 压缩 scan summary 与警告信息

但不得：

- 把 anomaly candidate 说成 anomaly fact
- 改写 `candidate_ref`、`candidate_score`、`flag_level` 或 `direction` 语义
- 隐藏因 `max_series` 或 `limit` 导致的排除 / 截断
