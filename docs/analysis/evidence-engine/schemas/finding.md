# Finding Schema

本文档定义事实层中 `finding` 的拟议类型契约。

状态：draft design。本文是规划中的规范 `finding` Schema 提案，不表示对应 HTTP endpoint 或持久化模型已经实现。

## 目的

`finding` 是 Factum 证据引擎中的规范事实单元（canonical fact unit），用于承接类型化分析意图（typed analysis intent）产出的工件（artifact），并把其中可确定性抽取、可单独引用、可被 agent 消费的事实以稳定类型表达出来。

设计目标：

- 用统一上位抽象承接不同类型化意图（typed intent）的原生结果
- 保持 `artifact -> finding` 抽取完全确定性
- 让 agent 可以直接围绕 `finding` 读取分析状态，而不是回读完整 artifact
- 明确区分事实单元、命题、评估状态和动作候选
- 为后续命题（`proposition`）/ 评估状态（`assessment`）/ 动作候选（`action proposal`）提供稳定输入

`artifact -> finding` 的提交边界、artifact class 与 successful empty result 规则以 [`evidence-engine/runtime-pipeline.md`](../runtime-pipeline.md) 为准；本文只定义 `finding` 本体的 schema、subtype 与 schema-level 约束。

## 核心设计决策

### 1. `finding` 是事实层的规范对象

`finding` 是事实层的总类。

- `observation` 不再作为“所有事实结果”的总称
- `observation` 只是 `finding` 的一个 subtype
- `delta`、`decomposition_item`、`anomaly_candidate`、`test_result` 等同样是 `finding`

这样可以在统一事实契约下保留不同意图家族的原生语义。

### 2. `finding` 使用原子事实粒度

`finding` 必须是从单个 artifact 中抽取出的最小可引用事实单元。

因此：

- 一个标量（scalar）结果通常对应一个 finding
- 一个分段行（segmented row）对应一个 finding
- 一个分解行（decomposition row）对应一个 finding
- 一个异常候选（anomaly candidate）对应一个 finding
- 一个预测桶（forecast bucket）对应一个 finding

聚合摘要、主题概览、UI 文案不应直接成为规范 `finding`；它们应属于投影层（projection layer）。

### 3. `finding_id` 只要求在 artifact 内稳定

`finding_id` 的稳定性边界绑定单个 artifact。

要求：

- 同一 artifact 重放时，抽出的同一事实项得到相同 `finding_id`
- 跨 artifact 即使语义相似，也不要求复用 `finding_id`

这里的“同一 artifact”指同一个 `artifact_id` 对应的 artifact 内容被重读、重放或重复抽取，而不是“重新执行了一个语义上相同的 step”。

因此：

- 若系统只是重新读取同一个 `artifact_id`，则 finding identity 必须稳定
- 若重新执行同一个 step，即使 `step_type` 与 params 完全相同，只要产生了新的 `artifact_id`，就视为新的 artifact 谱系，并产出新的 finding 标识
- 事实层不负责跨 artifact 的语义等价识别或标识合并

这样可以避免在事实层过早引入跨 artifact 语义去重逻辑。

推荐生成公式：

- `stable_hash(artifact_id, finding_type, canonical_item_key)`

其中：

- `canonical_item_key` 优先使用 artifact contract 显式提供的稳定 item key
- 只有在 artifact contract 显式固定 canonical order 时，才允许退回 `collection + index`
- `rank`、projection order、summary text 不得进入 fact identity

### 4. `finding` 只表达事实，不表达判断

`finding` 不得承载以下语义：

- `confidence`
- `status`
- `inference_level`
- `supported / tentative / confirmed / insufficient`
- `recommended_action`
- 因果结论

这些语义必须由后续 inference 过程产出的 `proposition / assessment / action proposal` 承载。

### 5. `finding` 的主要消费者是 agent

`finding` 的设计目标不是“方便存储一份中间结果”，而是“让 agent 能稳定消费事实”。

因此 `finding` 必须：

- 自描述
- 可单独引用
- 可围绕 subject 做局部闭包读取
- 带最小必要 provenance
- 不依赖自由文本解释才能理解

## Schema Position

`finding` 位于规范抽象链路的第二层：

`artifact -> finding -> proposition -> assessment -> action proposal`

职责分工：

- `artifact`：完整、可复现、可审计的步骤输出
- `finding`：从 artifact 中确定性抽出的原子事实单元
- `proposition`：待评估的结构化命题
- `assessment`：系统当前对 proposition 的评估状态
- `action proposal`：服务于 agent 的动作候选

## Typed Schema

```ts
type Finding =
  | ObservationFinding
  | DeltaFinding
  | DecompositionItemFinding
  | AnomalyCandidateFinding
  | CorrelationResultFinding
  | TestResultFinding
  | ForecastPointFinding;

type FindingBase = {
  finding_id: string;
  finding_type:
    | "observation"
    | "delta"
    | "decomposition_item"
    | "anomaly_candidate"
    | "correlation_result"
    | "test_result"
    | "forecast_point";
  artifact_id: string;
  step_ref: StepRef;
  subject: FindingSubject;
  observed_window: ResolvedTimeScope | null;
  quality: FindingQuality;
  provenance: FindingProvenance;
};

type StepRef = {
  session_id: string;
  step_id: string;
  step_type: string;
};

type FindingSubject = {
  metric: string | null;
  entity: string | null;
  slice: Record<string, string | number | boolean | null>;
  grain: "hour" | "day" | "week" | "month" | null;
  analysis_axis:
    | "scalar"
    | "time"
    | "segment"
    | "decomposition"
    | "correlation"
    | "test"
    | "forecast";
};

type FindingQuality = {
  data_complete: boolean | null;
  sample_size: number | null;
  row_count: number | null;
  null_rate: number | null;
  quality_status: "ready" | "needs_attention" | "not_ready" | null;
  quality_warnings: string[];
};

type FindingProvenance = {
  source_step_type: string;
  extractor_name: string;
  extractor_version: string;
  artifact_schema_version: string | null;
  canonical_item_key: string;   // stable key used as input to finding_id; also stored as dedicated column
  artifact_item_ref: ArtifactItemRef;
  projection_ref: string | null;
};

type ArtifactItemRef = {
  collection:
    | "value"
    | "rows"
    | "buckets"
    | "candidates"
    | "points"
    | "result";
  index: number | null;
  key: string | null;
};

type FindingRef = {
  session_id: string;
  finding_id: string;
};

type ArtifactItemRefRef = {
  artifact_id: string;
  item_ref: ArtifactItemRef;
};

type ResolvedTimeScope =
  | { kind: "range"; start: string; end: string }
  | { kind: "snapshot_now"; observed_at: string }
  | { kind: "latest_available"; data_as_of: string }
  | { kind: "as_of"; at: string };
```

## 公共字段语义

### finding_id

`finding_id` 是规范事实标识符。

推荐生成输入：

- `artifact_id`
- `finding_type`
- artifact 内的稳定 item key；仅当 contract 显式固定 canonical order 时，才退回 item path 或 row index

不应进入 `finding_id` 生成输入：

- `extractor_version`
- `artifact_schema_version`
- `projection_ref`
- 任何 explanation / summary / 排序位置字段

推荐属性：

- 同一 artifact 内稳定
- 不依赖执行时随机数
- 不依赖自由文本摘要

额外规则：

- 若 extractor 升级但 artifact item 的 canonical fact boundary 未变化，则 `finding_id` 不得变化
- extractor version 与 artifact schema version 只进入 provenance / version boundary，不进入 fact identity
- 只有当新的 artifact lineage 改变了 source item 或其 canonical identity boundary 时，才允许出现不同 `finding_id`

### finding_type

用于表达该 finding 的原生语义类别，而不是展示标签。

`finding_type` 决定：

- payload schema
- agent 的默认读取方式
- proposition seed 模板的可适用集合
- 局部视图中的分组方式

### artifact_id

指向抽取来源 artifact。

规则：

- 每个 finding 只能有一个权威来源 artifact
- finding 可被单独消费，但必须可回溯到其 artifact
- projection 不是 artifact 的替代品

并非所有 artifact 都直接进入 canonical finding layer。

artifact class 由 [`evidence-engine/runtime-pipeline.md`](../runtime-pipeline.md) 固定：

- `mandatory extraction artifact`：成功 committed 时必须完成 finding extraction，并形成 committed finding set；是否允许 empty finding set 由对应 artifact family contract 决定
- `non-fact artifact`：不直接进入 finding layer，因此 canonical finding count 为 `0`

典型情况：

- scalar artifact：通常产出 1 个 finding
- segmented / decomposition / detect / forecast artifact：通常产出多个 findings
- 纯审计或纯摘要 artifact：不作为 finding source

规范事实层不在 Schema 层限制单个 artifact 的 finding 数量上限；面向 agent 的有界消费通过 projection 和 focus view 控制。

### step_ref

表示哪个 typed analysis step 产出了该 artifact。

`step_ref` 的作用是帮助 agent 快速理解：

- 这是由哪类意图产出的结果
- 后续若要补证据，应回到哪类 step family

`step_ref` 与 `artifact_id` 在信息上是有意冗余的：

- `artifact_id` 提供权威溯源入口
- `step_ref` 提供 agent 侧的快速可读谱系，避免消费端为理解来源再去解引用 artifact

因此 v1 保留二者并存。

### subject

`subject` 是 `finding` 面向 agent 和 inference 的主语义锚点。

它回答的是：

- 这个事实是关于哪个 metric
- 关于哪个 slice
- 在哪个分析轴上表达

约束：

- `slice` 默认为 `{}`，不允许用 `null`
- 一个 finding 只对应一个主 subject
- 若结果天然包含多个并列主题，应拆成多个 finding

其中：

- `slice = {}` 表示该 finding 在其主 subject 上没有额外切片约束，即 overall / unsliced subject
- `slice = {}` 不等价于显式业务值，例如 `{"country": "ALL"}`
- `"ALL"` 这类值若存在，只能表示业务语义中的一个真实枚举值或显式编码，而不是规范全局标记

### observed_window

表示该 finding 对应的已解析时间语义。

规则：

- 能确定时必须显式返回
- 无法确定时返回 `null`
- `observed_window` 是事实字段，不是建议字段
- 不得为迁就下游而伪造窗口

典型 `null` 场景包括：

- 该 finding 对应的是无时间窗口语义的快照型或结构型结果
- 该 finding 只表达跨窗口关系，而不绑定单一观测窗口
- 上游 artifact 只提供了比较/检验/关联语义，无法把该 finding 精确映射到一个单独的 resolved window

相反，以下情况通常不应为 `null`：

- 单个 observation bucket
- 单个 anomaly candidate 对应的被观测时间桶
- 单个 forecast point 对应的预测桶

### quality

`quality` 只表达数据质量和结果可消费性相关的确定性元数据，不表达系统对该 finding 的“信任程度”。

因此：

- 可以包含 `sample_size`
- 可以包含 `quality_status`
- 可以包含 `quality_warnings`
- 不应包含 `confidence_score`

其中 `quality_status` 只表达单个 finding 的数据质量与可消费性，不表达跨 finding 或跨 artifact 的双边 comparability 结论。

v1 中各字段的 `null` 语义固定如下：

- `data_complete = null`：unknown
- `sample_size = null`：not_applicable
- `row_count = null`：not_applicable
- `null_rate = null`：not_applicable
- `quality_status = null`：not_applicable

### provenance

`provenance` 用于支持 agent 的快速溯源和审计读取。

其中：

- `artifact_item_ref` 以结构化格式指向 artifact 内该 finding 的来源项，而不是自由文本
- `projection_ref` 可选，用于指出某个 bounded projection 中的对应位置
- `extractor_version` 是 finding 重放稳定性的组成部分
- `artifact_schema_version` 用于说明 extractor 所针对的 artifact contract 版本

版本边界规则：

- `artifact_schema_version` 回答“这个 finding 是按哪个 artifact contract 解释出来的”
- `extractor_version` 回答“由哪个确定性 extractor 版本抽取出来”
- 二者都用于可解释性与审计，不参与 canonical `finding_id`
- 若 artifact contract 发生 breaking change，应通过新的 artifact lineage 或新的 contract version 显式区分，而不是隐式覆盖旧 finding 语义

`artifact_item_ref` 的稳定性规则：

- 有稳定 key 时，`key` 必须非空
- 只有在 contract 不提供稳定 key 且 canonical order 已固定时，才允许 `index`
- `index` 不能来自 projection 截断后的局部顺序

这里的 projection 指规范状态之上的有界消费视图，例如：

- agent 侧 top-k focus view
- UI 摘要视图
- recommendation shortcut view

projection 不是事实来源，只是对 canonical findings 的有界消费视图。

运行时抽取事件信息，例如 `extracted_at`，不属于 canonical `finding` 本体。

若需要记录抽取发生时间，应放入独立的 extraction audit / run metadata，而不是写回 immutable fact。

## Finding Subtypes

### Observation Finding

用于表达单个观测事实。

来源：

- `observe`
- 其他会直接返回 typed observation 的步骤

```ts
type ObservationFinding = FindingBase & {
  finding_type: "observation";
  payload:
    | ScalarObservationPayload
    | TimeBucketObservationPayload
    | SegmentObservationPayload
    | SampleSummaryObservationPayload;
};

type ScalarObservationPayload = {
  observation_kind: "scalar";
  value: number | null;
  unit: string | null;
};

type TimeBucketObservationPayload = {
  observation_kind: "time_bucket";
  bucket_start: string;
  bucket_end: string;
  value: number | null;
  unit: string | null;
};

type SegmentObservationPayload = {
  observation_kind: "segment";
  keys: Record<string, string | number | boolean | null>;
  value: number | null;
  unit: string | null;
  rank: number | null;
};

type SampleSummaryObservationPayload = {
  observation_kind: "sample_summary";
  sample_kind: "numeric" | "rate";
  summary: Record<string, number | null>;
};
```

`observe` 参数到 `observation_kind` 的映射规则如下：

- `granularity = null` 且 `dimensions = null` -> `scalar`
- `granularity != null` 且 `dimensions = null` -> `time_bucket`
- `granularity = null` 且 `dimensions.length >= 1` -> `segment`
- `result_mode in {"numeric_sample_summary", "rate_sample_summary"}` -> `sample_summary`

`observe` 不允许通过单次请求同时产出多个 observation family；单个 artifact family 内的每个 item 映射为一个 observation finding。

### Delta Finding

用于表达两个可比较观测之间的差异事实。

来源：

- `compare`

```ts
type DeltaFinding = FindingBase & {
  finding_type: "delta";
  payload: {
    delta_kind: "scalar_delta" | "segmented_delta";
    left_ref: ArtifactItemRefRef;
    right_ref: ArtifactItemRefRef;
    left_value: number | null;
    right_value: number | null;
    absolute_delta: number | null;
    relative_delta: number | null;
    direction: "increase" | "decrease" | "flat" | "undefined";
    presence: "both" | "left_only" | "right_only" | null;
    unit: string | null;
  };
};
```

抽取规则：

- `compare.comparison_type = "scalar_delta"` 时创建 1 个 delta finding，并固定 `presence = null`
- `compare.comparison_type = "segmented_delta"` 时每个 row 创建 1 个 delta finding，并把 row `presence` 原样映射到 finding payload

### Decomposition Item Finding

用于表达某个维度项对 scope delta 的分解结果。

来源：

- `decompose`
- `attribute`

```ts
type DecompositionItemFinding = FindingBase & {
  finding_type: "decomposition_item";
  payload: {
    dimension: string;
    keys: Record<string, string | number | boolean | null>;
    contribution_value: number | null;
    contribution_share: number | null;
    rank: number | null;
    direction: "increase" | "decrease" | "flat" | "undefined";
    scope_delta_ref: FindingRef;
  };
};
```

抽取规则：

- `direction` 与 `decompose.rows[].direction` 保持同一枚举，不再单独映射为 `positive/negative/neutral`
- `scope_delta_ref` 必须指向同一 compare lineage 下抽取得到的 canonical delta finding；不得通过自由文本或运行时模糊匹配补出
- `attribute` 不定义独立 canonical finding family；其 canonical fact 仍复用 `decomposition_item`

### Anomaly Candidate Finding

用于表达检测步骤返回的单个异常候选。

来源：

- `detect`

```ts
type AnomalyCandidateFinding = FindingBase & {
  finding_type: "anomaly_candidate";
  payload: {
    candidate_ref: ArtifactItemRefRef;
    score: number | null;
    flag_level: "high" | "medium" | "low" | null;
    actual_value: number | null;
    expected_value: number | null;
    deviation_absolute: number | null;
    deviation_relative: number | null;
  };
};
```

字段映射：

- `score <- candidate_score`
- `actual_value <- observed_value`
- `deviation_absolute <- deviation_abs`
- `deviation_relative <- deviation_pct`

### Correlation Result Finding

用于表达两个序列之间的统计关联结果。

来源：

- `correlate`

```ts
type CorrelationResultFinding = FindingBase & {
  finding_type: "correlation_result";
  payload: {
    left_ref: ArtifactItemRefRef;
    right_ref: ArtifactItemRefRef;
    method: "pearson" | "spearman";
    coefficient: number | null;
    p_value: number | null;
    n: number | null;
    join_basis: string | null;
  };
};
```

抽取规则：

- v1 中每个 `correlate` artifact 只生成 1 个 `correlation_result` finding

### Test Result Finding

用于表达单次统计检验的结果事实。

来源：

- `test`

```ts
type TestResultFinding = FindingBase & {
  finding_type: "test_result";
  payload: {
    left_ref: ArtifactItemRefRef;
    right_ref: ArtifactItemRefRef;
    method: "welch_t" | "two_proportion_z";
    estimate_value: number | null;
    statistic_name: "t" | "z";
    statistic_value: number | null;
    p_value: number | null;
    reject_null: boolean | null;
    alpha: number;
  };
};
```

抽取规则：

- v1 中每个 `test` artifact 只生成 1 个 `test_result` finding

### Forecast Point Finding

用于表达预测序列中的单个未来点。

来源：

- `forecast`

```ts
type ForecastPointFinding = FindingBase & {
  finding_type: "forecast_point";
  payload: {
    bucket_start: string;
    bucket_end: string;
    predicted_value: number | null;
    prediction_interval: {
      lower: number | null;
      upper: number | null;
      level: number | null;
    } | null;
    horizon_index: number;
  };
};
```

## Typed Intent 到 Finding 的映射

v1 推荐固定如下映射：

| Typed Intent | Artifact 核心结果 | Finding subtype |
| --- | --- | --- |
| `observe` | scalar observation | `observation` |
| `observe` | time-series bucket | `observation` |
| `observe` | segmented row | `observation` |
| `observe` | sample summary | `observation` |
| `compare` | scalar delta | `delta` |
| `compare` | segmented delta row | `delta` |
| `decompose` / `attribute` | contribution row | `decomposition_item` |
| `detect` | anomaly candidate | `anomaly_candidate` |
| `correlate` | correlation result | `correlation_result` |
| `test` | hypothesis test result | `test_result` |
| `forecast` | forecast bucket | `forecast_point` |

约束：

- mapping 必须由 typed artifact contract 决定
- 不允许 extractor 自行发明新的 claim-like finding
- 不允许把一个主题摘要直接映射为 canonical finding
- `attribute` 不引入独立 finding subtype；只复用 `delta` 与 `decomposition_item`

## Extraction 规则

`artifact -> finding` 抽取必须由确定性 extractor 完成。

artifact family 的适用范围、staged/commit 边界与 failure semantics 以 [`evidence-engine/runtime-pipeline.md`](../runtime-pipeline.md) 为主；本文只补充 `finding` schema 侧必须满足的规则。

### 通用规则

- 不使用模型
- 不依赖自由文本解释
- 不根据 session 其他状态改变 finding 内容
- 同一 artifact 的相同 item 必须得到相同 finding
- extractor 应由 `artifact_type + artifact_schema_version` 选择
- `mandatory extraction artifact` 成功提交时必须完成 finding extraction；empty finding set 是否允许由对应 artifact family contract 决定
- extractor 必须声明自己所兼容的 artifact schema version
- 当 artifact contract 发生 breaking change 时，应通过新的 `artifact_schema_version` 或新的 extractor version 显式区分

### Reference Rules

finding payload 中的 canonical 引用必须使用结构化 typed ref。

通用的 hard / soft ref 分类、跨 session 禁止边界、dangling read semantics 与 closure integrity 以 [`evidence-engine/graph-and-reference-semantics.md`](../graph-and-reference-semantics.md) 为准；本节只补充 `finding` payload 自身的 subtype 约束。

规则：

- 不允许使用裸字符串作为 canonical source ref
- payload ref 不允许跨 session 指向其他 finding
- payload ref 可以跨 artifact，但必须显式带上 `artifact_id` 或 `finding_id`
- payload ref 不得指向 projection
- 引用图必须保持有向无环

v1 固定如下：

- `left_ref` / `right_ref`：使用 `ArtifactItemRefRef`
- `scope_delta_ref`：使用 `FindingRef`，指向被解释的 delta finding
- `candidate_ref`：使用 `ArtifactItemRefRef`，指向 detect artifact 中对应 candidate item

### 行级规则

- scalar artifact item：1 item -> 1 finding
- row-based artifact item：1 row -> 1 finding
- bucket-based artifact item：1 bucket -> 1 finding
- candidate-based artifact item：1 candidate -> 1 finding
- 若 `rows` / `buckets` / `candidates` / `points` 为空，是否允许成功提交空 finding set 由对应 artifact family contract 决定；schema 层不再统一规定必须失败

### 非法抽取

以下情况不应产生 finding：

- 纯展示性文案
- UI summary
- recommendation text
- artifact 级 narration
- 无法稳定定位来源项的临时派生文本
- 仅因 projection 截断而保留下来的 top-k 局部结果

## 生命周期与去重

v1 中 `finding` 视为 immutable canonical fact。

规则：

- finding 创建后不应原地修改其事实内容
- 若上游 artifact 被重新执行并生成新 artifact，应生成新的 findings，而不是更新旧 findings
- 若 artifact 被逻辑删除，其 findings 应视为失效并随谱系一并移除

事实层默认不做跨 artifact 或跨 session 的规范去重。

因此：

- 同一 session 中重复执行语义相同的 step，可以产生重复语义但不同标识的 findings
- 是否折叠这些 findings，属于 projection、focus view 或更高层 state 的职责

## Agent-Facing Consumption

agent 不应被要求直接遍历全量 artifact。

更推荐围绕 `finding` 暴露局部最小闭包读取视图。

```ts
type FindingFocusView = {
  focus_subjects: FindingSubject[];
  backing_findings: Finding[];
  artifact_refs: Array<{
    artifact_id: string;
    step_ref: StepRef;
  }>;
  truncation: {
    is_truncated: boolean;
    returned_count: number;
    total_count: number | null;
  };
};
```

主规则：

- `backing_findings` 是 agent 的主事实读取入口
- `artifact_refs` 只负责溯源，不负责承载主语义
- focus view 应允许围绕 metric、slice、finding_type 做局部读取
- 默认排序必须确定性，且不依赖模型打分

推荐查询参数如下：

```ts
type FindingFocusQuery = {
  metric?: string | null;
  slice?: Record<string, string | number | boolean | null> | null;
  finding_types?: Finding["finding_type"][] | null;
  artifact_ids?: string[] | null;
  step_types?: string[] | null;
  observed_window_overlap?: ResolvedTimeScope | null;
  limit?: number | null;
};
```

推荐过滤语义：

- `metric`：只返回 `subject.metric` 匹配的 findings
- `slice`：查询条件必须是 `subject.slice` 的子集精确匹配；例如查询 `{country: "US"}` 可匹配 `{country: "US"}` 与 `{country: "US", "device": "iOS"}`，但不能匹配 `{country: "CA"}`
- `finding_types`：只返回指定 subtype
- `artifact_ids`：限制来源 artifact 范围
- `step_types`：限制来源意图家族
- `observed_window_overlap`：仅 `range` 与 `range` 使用区间相交；其他 `kind` 只在 kind 相同且 canonical timestamp 完全相等时匹配，不做跨 kind 隐式归一化

推荐默认排序规则：

1. `subject.metric` lexical order，nulls last
2. `subject.slice` canonicalized lexical order
3. `finding_type` lexical order
4. `observed_window.kind` lexical order，nulls last
5. 各 `observed_window.kind` 的 canonical time key ascending：
   - `range.start`
   - `snapshot_now.observed_at`
   - `latest_available.data_as_of`
   - `as_of.at`
6. `finding_id` ascending

若调用方请求 top-k，应在上述排序基础上做稳定截断，并返回 truncation metadata。

局部最小闭包规则：

- `backing_findings` 是主事实载荷
- `artifact_refs` 是 `backing_findings` 的权威来源 artifact 去重集合，不额外引入新语义
- `focus_subjects` 是 `backing_findings.subject` 的稳定去重投影，不是独立推断结果

## 与 Proposition / Assessment 的边界

### Finding 和 Proposition 的边界

`finding` 回答的是“系统确定知道什么”。

`proposition` 回答的是“系统接下来要判断什么”。

因此：

- `finding` 不写 “this suggests”
- `finding` 不写 “worth validating”
- `finding` 不写 “main driver”

这些都属于 proposition 或 assessment 层。

### Finding 和 Assessment 的边界

`assessment` 可以引用 finding，但不应把 assessment 字段回写到 finding。

禁止字段示例：

- `status`
- `confidence`
- `supporting_finding_ids`
- `missing_requirements`
- `blocking_gaps`

### Finding 和 Action Proposal 的边界

action proposal 是面向 agent 的动作候选，不是事实。

因此 finding 中不得出现：

- `suggested_validation`
- `next_step`
- `action_priority`
- `expected_impact`

## 非目标

`finding` 的设计不应退化为：

- claim 的别名
- recommendation 的证据附件
- artifact 的裁剪版全文
- 任意中间结果的杂项 JSON 容器
- 面向人类报告的 narrative block

## 非法状态

以下状态在 canonical `finding` 中非法：

- 同一 `artifact_id + finding_type + artifact_item_ref` 映射到多个 `finding_id`
- `mandatory extraction artifact` 在其 family contract 要求非空 finding set 时进入 committed state，但找不到任何对应 findings
- `subject.slice = null`
- `quality_warnings = null`
- 使用裸字符串作为 canonical payload ref
- 用 `projection_ref` 替代 source artifact / source finding ref
- 同一 payload ref 跨 session 指向其他 finding
- 在 focus view 中注入 `main_driver`、`worth_validating` 等判断性新字段

## 示例

### Example 1: segmented compare row -> delta finding

```json
{
  "finding_id": "finding_art_cmp_01_delta_device_ios",
  "finding_type": "delta",
  "artifact_id": "art_cmp_01",
  "step_ref": {
    "session_id": "sess_01",
    "step_id": "step_cmp_01",
    "step_type": "compare"
  },
  "subject": {
    "metric": "avg_watch_time",
    "entity": null,
    "slice": { "device_type": "iOS" },
    "grain": null,
    "analysis_axis": "segment"
  },
  "observed_window": {
    "kind": "range",
    "start": "2024-03-01",
    "end": "2024-03-08"
  },
  "quality": {
    "data_complete": true,
    "sample_size": 18234,
    "row_count": 1,
    "null_rate": 0.0,
    "quality_status": "ready",
    "quality_warnings": []
  },
  "provenance": {
    "source_step_type": "compare",
    "extractor_name": "compare_delta_extractor",
    "extractor_version": "v1",
    "artifact_schema_version": "v1",
    "canonical_item_key": "rows:iOS",
    "artifact_item_ref": {
      "collection": "rows",
      "index": null,
      "key": "iOS"
    },
    "projection_ref": null
  },
  "payload": {
    "delta_kind": "segmented_delta",
    "left_ref": {
      "artifact_id": "art_obs_current",
      "item_ref": {
        "collection": "rows",
        "index": null,
        "key": "iOS"
      }
    },
    "right_ref": {
      "artifact_id": "art_obs_baseline",
      "item_ref": {
        "collection": "rows",
        "index": null,
        "key": "iOS"
      }
    },
    "left_value": 12.4,
    "right_value": 14.1,
    "absolute_delta": -1.7,
    "relative_delta": -0.1206,
    "direction": "decrease",
    "presence": "both",
    "unit": "minutes"
  }
}
```

### Example 2: anomaly candidate -> anomaly finding

```json
{
  "finding_id": "finding_art_det_01_candidate_3",
  "finding_type": "anomaly_candidate",
  "artifact_id": "art_det_01",
  "step_ref": {
    "session_id": "sess_01",
    "step_id": "step_det_01",
    "step_type": "detect"
  },
  "subject": {
    "metric": "checkout_conversion",
    "entity": null,
    "slice": { "country": "US" },
    "grain": "day",
    "analysis_axis": "time"
  },
  "observed_window": {
    "kind": "range",
    "start": "2024-03-07",
    "end": "2024-03-08"
  },
  "quality": {
    "data_complete": true,
    "sample_size": 4201,
    "row_count": 1,
    "null_rate": 0.0,
    "quality_status": null,
    "quality_warnings": []
  },
  "provenance": {
    "source_step_type": "detect",
    "extractor_name": "detect_candidate_extractor",
    "extractor_version": "v1",
    "artifact_schema_version": "v1",
    "artifact_item_ref": {
      "collection": "candidates",
      "index": 3,
      "key": null
    },
    "projection_ref": null
  },
  "payload": {
    "candidate_ref": {
      "artifact_id": "art_det_01",
      "item_ref": {
        "collection": "candidates",
        "index": 3,
        "key": null
      }
    },
    "score": 0.92,
    "flag_level": "high",
    "actual_value": 0.031,
    "expected_value": 0.052,
    "deviation_absolute": -0.021,
    "deviation_relative": -0.4038
  }
}
```

## 经验法则

在判断某个字段或对象是否属于 `finding` 时，可用以下问题自检：

1. 它表达的是事实，还是判断，还是动作建议？
2. 它能否由 artifact 确定性抽出？
3. 它是否对应单个可引用 item？
4. agent 是否可以不依赖自由文本就消费它？
5. 它是否会随着新证据进入而改变？

如果第 5 个问题答案是“会”，它通常不属于 finding，而更可能属于 assessment。
