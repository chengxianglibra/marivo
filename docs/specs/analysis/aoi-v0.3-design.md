# AOI v0.3 Artifact Algebra（工件代数）目标架构设计

状态：设计草案。

本文定义 AOI v0.3 的目标架构：AOI 从 v0.2 的意图请求目录，演进为以工件代数
（artifact algebra）为核心的公共分析契约。Marivo 会以该公共 AOI 契约为目标，
重建生成合同层、运行时降级、工件存储、传输面与测试体系。

AOI v0.3 是破坏性更新，不承担 v0.2 线缆格式兼容。本文不描述迁移兼容层，也不保留旧的
`Artifact { artifact_id, result | failure }` 外壳语义。

## 1. 定位与核心命题

AOI v0.3 不再把 artifact 视为各 intent 私有的 `result` 变体，而是把 artifact 定义为
公共分析代数的基本值。

核心命题：

- operation 名称不扩张：继续保留 7 个原子操作
  `observe`、`compare`、`decompose`、`correlate`、`detect`、`test`、`forecast`，
  以及 3 个派生操作 `validate`、`attribute`、`diagnose`。
- artifact 模型破坏性升级：公共 AOI 直接采用统一 `ArtifactEnvelope`，不保留
  v0.2 的薄 `result | failure` wrapper。
- 组合能力来自 transform DSL：`slice`、`filter`、`rollup`、
  `summarize_samples` 是请求中的带类型内联转换，不作为同级公共
  operation namespace，也不产生公共 artifact。
- source-style request 是便利用法；artifact DAG 是规范语义。
- Marivo 落地是目标架构的一部分：`aoi-spec`、生成模型、运行时降级、
  工件存储、传输面、文档与测试都需要以 v0.3 工件代数为目标重建。

本文的目标不是给 AOI 再增加更多 intent 名称，而是让 AOI 成为一套可组合、可校验、
可审计的带类型工件代数。

## 2. 公共 Artifact Envelope

AOI v0.3 的公共 artifact 采用统一 envelope。该 envelope 是公共契约，因此字段必须克制：
只表达可移植的分析语义，不承载 Marivo 私有执行细节。

```ts
type ArtifactEnvelope<TPayload> =
  | SuccessfulArtifactEnvelope<TPayload>
  | ResolvedFailedArtifactEnvelope
  | UnresolvedFailedArtifactEnvelope;

type SuccessfulArtifactEnvelope<TPayload> = {
  artifact_id: string;
  artifact_family: ArtifactFamily;
  shape: string;
  subject: ArtifactSubject;
  axes: AxisRef[];
  measures: MeasureRef[];
  capabilities: ArtifactCapability[];
  lineage: ArtifactLineage;
  payload: TPayload;
};

type ResolvedFailedArtifactEnvelope = {
  artifact_id: string;
  failure_stage: "resolved";
  artifact_family: ArtifactFamily;
  shape: string;
  subject: ArtifactSubject;
  axes: AxisRef[];
  measures: MeasureRef[];
  capabilities: [];
  lineage: ArtifactLineage;
  failure: AnalysisFailure;
};

type UnresolvedFailedArtifactEnvelope = {
  artifact_id: string;
  failure_stage: "unresolved";
  operation: AoiOperation;
  subject?: ArtifactSubject;
  lineage: ArtifactLineage;
  failure: AnalysisFailure;
};
```

`ArtifactSubject` 必须是 discriminated union。公共规范至少定义以下最小分支；实现可以在
payload 或私有 trace 中保留更细粒度字段，但不得把 `subject` 退化成无约束 map：

```ts
type ArtifactSubject =
  | { kind: "metric"; metric_ref: string; scope?: SubjectScopeRef }
  | { kind: "comparison"; metric_ref: string; current: SubjectScopeRef; baseline: SubjectScopeRef }
  | { kind: "candidate"; metric_ref: string; candidate_selector: CandidateSelector }
  | { kind: "hypothesis"; hypothesis_ref?: string; metric_ref: string; groups: SampleGroupSelector[] }
  | { kind: "diagnosis"; metric_ref: string; candidate_selector?: CandidateSelector };
```

`unresolved` failure 的 `subject` 是可选字段。只有当解析阶段已经得到合法的完整 subject
分支时才能填充；不得把部分解析字段拼成不完整 subject。

字段语义：

- `artifact_id` 是可引用身份。下游 operation 和 manifest 都通过它引用完整
  artifact。
- `artifact_family` 是最高层语义分类，例如 `metric_frame`、`delta_frame`、
  `candidate_set`。
- `shape` 是 family 内部形态。它不是全局枚举；每个 family 必须列出自己的合法
  shapes。
- `subject` 是带类型联合，不得是无约束 map。推荐至少覆盖 metric subject、
  comparison subject、candidate subject、hypothesis subject、diagnosis subject。
- `axes` 描述 artifact 当前实际携带的结构轴，例如 `time`、`dimension`、
  `candidate`、`comparison_side`、`sample_group`。
- `measures` 描述 payload 中可被 transform 或 consumer 稳定识别的数值字段，例如
  `value`、`delta_abs`、`delta_pct`、`contribution_abs`、`p_value`、
  `forecast_value`。
- `capabilities` 是兼容性守卫，不是状态标签。合法例子包括
  `sliceable`、`filterable`、`rollupable`、`comparable`、`decomposable`、
  `testable`、`forecastable`。
- `lineage` 是公共、结构化、最小谱系，记录产出 operation、来源 artifact refs
  与规范化后的内联转换。
- `payload` 是 family 专属数据体。
- `failure` 是 envelope 级终止失败。

成功与失败互斥：

- 成功 artifact 必须有 `payload` 且不得有 `failure`。
- 失败 artifact 必须有 `failure` 且不得有 `payload`。
- `resolved` failure 表示 operation 已经解析出目标 family / shape / axes / measures，
  但执行或守卫失败。它不得暴露任何可被下游消费的 capability。
- 失败 artifact 不携带 `partial_payload`。部分成功必须表达为已经成功落库的独立
  artifacts，并通过 `ExecutionManifest.nodes[].output_artifact_id` 回溯。
- `unresolved` failure 表示 failure 发生在请求解析、artifact ref 解析、
  subject 解析或 transform 校验期间。它不得伪造 `artifact_family`、
  `shape`、`axes`、`measures` 或 `capabilities`。

公共 AOI 的 `lineage` 不包含 engine id、query hash、wall time、debug plan、
storage locator 等实现细节。这些属于 Marivo 实现元数据。

## 3. Artifact Families（工件族）、Shapes（形态）与 Registry（注册表）

AOI v0.3 的 artifact family 应少而稳定，shape 负责表达同一 family 内的数据形态。
兼容性由 `family + shape + capabilities` 判定，不由 producing operation 名称判定。

核心 families：

| 工件族                      | 形态                                                                    | 主要语义                             |
| ------------------------ | --------------------------------------------------------------------- | -------------------------------- |
| `metric_frame`           | `scalar`, `time_series`, `segmented`, `panel`, `sample_summary`       | 指标观测与可检验样本摘要                     |
| `delta_frame`            | `scalar_delta`, `time_series_delta`, `segmented_delta`, `panel_delta` | 两个 `metric_frame` 的差异            |
| `candidate_set`          | `ranked_candidates`                                                   | `detect` 产生的候选窗口或候选 slice        |
| `attribution_frame`      | `ranked_contributions`                                                | `decompose` / `attribute` 产生的贡献项 |
| `association_result`     | `pairwise_association`                                                | `correlate` 产生的统计关联              |
| `forecast_frame`         | `forecast_series`                                                     | `forecast` 产生的未来 bucket          |
| `hypothesis_test_result` | `two_sample_mean`                                                     | `test` / `validate` 产生的检验结果      |
| `diagnosis_result`       | `candidate_diagnoses`                                                 | `diagnose` 的派生诊断闭包               |

`sample_summary` 不作为独立 family。它是 `metric_frame` 的 shape，由
`summarize_samples` transform 在 consuming operation 内部产生，并通过 `testable`
capability 被 `test` 消费。这样可以避免新增一个只服务 `test` 的孤立 artifact family。

AOI v0.3 公共规范必须包含 artifact registry。该 registry 是 family / shape /
capability / transform / consumer 兼容性的单一规范入口。
Registry 必须是机器可读 source，而不是只存在于 Markdown 表格中。公共发布包新增
`aoi-spec/registry/artifact-registry.yaml`，文档表格、runtime guard、一致性测试和生成合同层
都必须从这份 source 校验或派生。

Registry source 至少表达：

- 每个 `artifact_family` 的合法 `shape`。
- 每个 `shape` 的必需 axes、measures 与可出现 capabilities。
- 每个 measure id 的 family / shape 作用域与语义说明。
- 每个 `shape` 允许的 inline transforms。
- 每个 operation 可消费的 family / shape / capability 组合。
- 每个 transform 的 input requirement、effective-shape rule 与 failure code family。

| 工件族 | 必需 axes | 典型 measures | capabilities | 合法内联 transforms | 合法 consumers |
|---|---|---|---|---|---|
| `metric_frame(scalar)` | 无 | `value` | `comparable`, `filterable` | `filter`；当 source grain 可用时可用 `summarize_samples` | `compare`, `attribute`, `validate` |
| `metric_frame(time_series)` | `time` | `value` | `sliceable`, `filterable`, `rollupable`, `comparable`, `forecastable`, `testable` | `slice`, `filter`, `rollup`, `summarize_samples` | `compare`, `correlate`, `detect`, `forecast`, `test` |
| `metric_frame(segmented)` | `dimension` | `value` | `sliceable`, `filterable`, `rollupable`, `comparable` | `slice`, `filter`, `rollup` | `compare`, `attribute` |
| `metric_frame(panel)` | `time`, `dimension` | `value` | `sliceable`, `filterable`, `rollupable`, `comparable`；单序列化后可 `forecastable` | `slice`, `filter`, `rollup`, `summarize_samples` | `compare`, `detect`；单序列化后可 `forecast` |
| `metric_frame(sample_summary)` | `sample_group` | `n`, `mean`, `stddev` | `testable` | 无 | `test`, `validate` |
| `delta_frame(*)` | 由 shape 决定 | `delta_abs`, `delta_pct` | `sliceable`, `filterable`, `decomposable` | `slice`, `filter` | `decompose`, `attribute`, `diagnose` |
| `candidate_set(ranked_candidates)` | `candidate` | `score`, `value` | `sliceable` | `slice` | `diagnose` |
| `attribution_frame(ranked_contributions)` | `dimension` | `contribution_abs`, `contribution_pct` | `filterable` | `filter` | projection/read surfaces |
| `association_result(pairwise_association)` | 无 | `coefficient`, `p_value` | 无 | 无 | projection/read surfaces |
| `forecast_frame(forecast_series)` | `time` | `forecast_value`, `ci_low`, `ci_high` | `sliceable` | `slice` | projection/read surfaces |
| `hypothesis_test_result(two_sample_mean)` | 无 | `statistic`, `p_value` | 无 | 无 | `validate` readback / projection |
| `diagnosis_result(candidate_diagnoses)` | `candidate` | candidate-specific | `sliceable` | `slice` | projection/read surfaces |

Measure 规则：

- measure id 必须按 family / shape 解释，codegen 和 runtime guard 不得只按字符串名推断语义。
- `delta_frame.delta_abs` / `delta_frame.delta_pct` 表示两个 `metric_frame` 整体之间的差异。
- `attribution_frame.contribution_abs` / `attribution_frame.contribution_pct` 表示单个维度成员
  对整体差异的贡献，不得与 `delta_frame` measures 混用。
- `association_result`、`forecast_frame`、`hypothesis_test_result` 默认是终端读取
  artifacts。除 registry 显式授予 capability 与 consumer 外，它们不参与下游 operation DAG。

Registry（注册表）规则：

- registry 是规范性的。运行时守卫逻辑、生成合同校验、文档与一致性测试夹具必须与它一致。
- 只有当 family / shape 拥有所需 axes 与 measures 时，capability 才能出现。
- capabilities 不能表达结果就绪状态、有效性或产品状态。
- 空 transform 列表合法。未知 transform、不支持的 axis、不支持的 consumer 都是带类型失败，
  不是空操作。

Family 规则：

- `metric_frame(panel)` 是一等 `time x dimension` shape，不得折叠成 `segmented`。
- `metric_frame(segmented)` 是 dimension-only shape；`metric_frame(panel)` 是新增的
  `time x dimension` shape。本文不定义 v0.2 线缆迁移，但术语上 v0.2 segmented
  observation 对应 v0.3 的 dimension-only `segmented`，不是 `panel`。
- `candidate_set` 中的 candidates 必须携带稳定 selector，供 inline `slice(candidate=...)` 使用。
- `validate` 输出 `hypothesis_test_result`，不产生仅供 derived 使用的 family。
- `attribute` 输出 `attribution_frame`，不产生仅供 derived 使用的 family。
- `diagnosis_result` 是唯一派生专用 family，因为 `diagnose` 无法同构为单个
  原子 artifact。

## 4. Transform DSL

AOI v0.3 不把 `slice`、`filter`、`rollup` 或 `summarize_samples` 增加为公共 operation
命名空间。它们只作为 artifact input 上的带类型内联 transforms 出现。

```ts
type ArtifactInput = {
  artifact_id: string;
  transforms?: Transform[];
};

type Transform =
  | SliceTransform
  | FilterTransform
  | RollupTransform
  | SummarizeSamplesTransform;
```

Transform 语义：

- Transform 是 artifact input 的修饰器，不是 intent。
- Transform 链按声明顺序运行，并记录到 `lineage.applied_transforms`。
- Transform 兼容性按输入 artifact 的 `capabilities`、`axes` 与 `measures` 检查。
- Transform 不产生公共 artifact，也不能被 `artifact_id` 引用。
- Transform 不能创造新的分析结论。它只能在消费它的 operation 执行前调整结构、粒度
  或 test-input 形态。
- AOI 执行中只有 operation output 会创建公共 artifact。
- 如果调用方需要复用已转换的 `metric_frame`，必须用目标 scope / shape 调用
  `observe`，或消费使用该 transform 的 operation output。
- Marivo 可以在私有层缓存已转换的中间数据，但这些 cache 不是 AOI artifacts，
  不获得公共 `artifact_id`，也不得作为下游输入暴露。

Transform 形状：

```ts
type SliceTransform = {
  kind: "slice";
  axis: "time" | "dimension" | "candidate" | "sample_group";
  selector: TimeSelector | DimensionSelector | CandidateSelector | SampleGroupSelector;
};

type FilterTransform = {
  kind: "filter";
  predicate: Expression;
};

type RollupTransform = {
  kind: "rollup";
  target_axes: AxisRef[];
  aggregation_policy?: "metric_default";
};

type SummarizeSamplesTransform = {
  kind: "summarize_samples";
  grain: TimeGranularity;
  groups?: SampleGroupSelector[];
};
```

Transform 规则：

- `slice` 收窄 axes、window 或 selector，并可能改变 consuming operation 看到的有效输入
  shape。
- `filter` 通过结构化 predicate 收窄 rows，不改变 axes 定义。
- `rollup` 必须尊重 OSI 指标聚合语义；ratio / rate 指标不能退化为
  简单平均。
- `summarize_samples` 只对 numeric `metric_frame` 输入合法，会在 consuming operation
  内部创建有效的 `metric_frame(shape = "sample_summary")`，并为该 operation 添加
  `testable` 语义。
- `summarize_samples` 与 `rollup` 使用同一套指标聚合语义。ratio / rate 指标必须基于
  OSI 注册表中的 numerator / denominator 或等价组成字段重新计算 sample summary，
  不得直接对已计算的 `value` 做平均或标准差。
- `project` 不属于 transform DSL。它是读取 / 展示投影，永远不是
  规范 artifact input。

### 4.1 Transform 校验边界

Transform 校验是公共 AOI 契约边界，必须在运行时降级触达查询编译
或 artifact 消费之前以关闭失败方式失败。

校验顺序：

1. Schema 校验：校验 transform `kind`、必填字段、selector shape、枚举值与
   expression 外壳形态。
2. 语义引用授权：metric、dimension、time field、sample group、
   candidate selector 与 referenced artifact 必须对当前 session / actor 可见。
3. 编译器安全：在执行前校验 `Expression` 方言、字段引用、支持的函数、join 可达性与
   SQL 注入边界。
4. Capability 守卫：输入 family / shape / capability / axis / measure 组合必须允许该
   transform。
5. Consumer 守卫：transform 后的有效输入必须满足 consuming operation。

任何校验失败都会产生 `AnalysisFailure`；不得静默忽略，也不得降级成空结果。

边界说明：

- 语义引用授权是 session-scoped 静态检查：确认 metric、dimension、time field、
  sample group 与 candidate selector 在当前 semantic model / session 中可见，不调用
  query engine。
- `Expression` 沿用 AOI 公共表达式外壳，但 transform guard 只接受 allowlisted
  dialect、字段、函数与结构化引用；compiler safety 在这里是 AST / schema / allowlist
  校验，不生成 SQL，也不执行查询。
- 真正的查询编译发生在 operation execution 阶段。到达该阶段前，transform guard
  必须已经 fail closed。

示例：

```ts
forecast({
  source: {
    artifact_id: "obs_panel",
    transforms: [
      {
        kind: "slice",
        axis: "dimension",
        selector: { dimension: "country", value: "US" }
      }
    ]
  },
  horizon: { buckets: 14, granularity: "day" }
})
```

规范语义：

```text
metric_frame(panel) --inline slice(country = "US")--> effective metric_frame(time_series)
  -> forecast
  -> forecast_frame(forecast_series)
```

内联 transform 不产生中间 `metric_frame(time_series)` artifact。

## 5. Operation 兼容矩阵

AOI v0.3 保留 v0.2 的 operation 集合，但 request / response 语义改为 envelope +
transform DSL。

| 操作 | 输入 | 输出 |
|---|---|---|
| `observe` | semantic metric + time scope / filter / granularity / dimensions | `metric_frame` |
| `compare` | 两个 `metric_frame` 输入，每个都可带 inline transforms | `delta_frame` |
| `decompose` | 一个 `delta_frame`，可带 inline transforms + dimension | `attribution_frame` |
| `correlate` | 两个同形状有效 `metric_frame` 输入 | `association_result` |
| `detect` | scan-ready 的有效 `metric_frame(time_series | panel)`，或 semantic metric + scan scope | `candidate_set` |
| `test` | 两个有效 `metric_frame(sample_summary)` 输入 | `hypothesis_test_result` |
| `forecast` | 一个有效 `metric_frame(time_series | panel)` 输入 | `forecast_frame` |
| `validate` | 语义 metric / source slices，或 sample-summary artifact 输入加 hypothesis | `hypothesis_test_result` |
| `attribute` | 两个可比较的 `metric_frame` 输入，或语义 source slices + dimensions | `attribution_frame` |
| `diagnose` | scan-ready `metric_frame`，或 semantic metric + scan scope + dimensions | `diagnosis_result` |

兼容性规则：

- `observe`、`detect`、`validate`、`attribute`、`diagnose` 可以接受 source-style
  便利用法输入，但规范降级必须仍然产出逻辑 artifact DAG。
- `compare` 检查 `comparable` capability 与语义对齐，不检查 producer
  operation 名称。
- `forecast` 接受单个有效 `metric_frame(time_series | panel)`。`panel` 输入按 series
  独立预测并保留 series keys，不在 `forecast` 内做跨 segment 聚合。
- `decompose` 消费 `delta_frame`，不得重新 observe 原始 metric。
- `test` 消费有效 `metric_frame(sample_summary)` 输入。Sample summary 计算不再是藏在
  `test` 内部的私有逻辑，而是 request 语义中的显式 transform。
- `validate` 是 `summarize_samples + test` 的派生便利用法，输出
  `hypothesis_test_result`。
- `attribute` 是 `observe / compare / decompose` 的派生便利用法，输出
  `attribution_frame`。
- `diagnose` 是 `detect + candidate slice + compare + decompose` 的派生便利用法，
  输出 `diagnosis_result`。

## 6. Failure Code 分类

AOI v0.3 保留 `AnalysisFailure` 作为可移植的阻断失败形状，但 failure codes
必须归入稳定 family，方便 agent 与一致性测试推理。

推荐 code families：

| 分类 | 示例 | 含义 |
|---|---|---|
| `envelope.*` | `envelope.schema_invalid`, `envelope.failure_shape_invalid` | request 或 artifact envelope 无法解析 |
| `artifact_ref.*` | `artifact_ref.not_found`, `artifact_ref.wrong_family`, `artifact_ref.cross_session` | referenced artifact 无法被消费 |
| `transform.*` | `transform.unsupported_kind`, `transform.invalid_selector`, `transform.expression_invalid` | inline transform 形状错误或不安全 |
| `capability.*` | `capability.missing`, `capability.axis_missing`, `capability.measure_missing` | 输入缺少所需 capability / axis / measure |
| `operation.*` | `operation.unsupported_shape`, `operation.semantic_mismatch`, `operation.insufficient_data` | operation 专属执行无法继续 |
| `manifest.*` | `manifest.node_failed`, `manifest.primary_artifact_missing` | 派生 operation 展开无法产生成功的 primary artifact |

规则：

- Failure code 是单数且阻断性的。非阻断提示应进入 payload fields 或
  Marivo 执行追踪 warnings，而不是 `AnalysisFailure`。
- 未知 code family 对公共 AOI v0.3 artifacts 非法。
- 一致性测试夹具必须覆盖每个 code family 的代表性负例。
- 实现可以在公共 AOI 之外添加产品专属 diagnostics，但这些 diagnostics 不得替代
  可移植的 `AnalysisFailure.code`。

## 7. Derived Intent 与 Execution Manifest（执行清单）

AOI v0.3 保留派生操作，但它们必须是确定性组合契约，而不是私有 bundle。

派生 operation 规则：

- 每个派生 operation 必须定义固定 expansion DAG。
- expansion DAG 只能包含 7 个原子 operations 和内联 transform 标注。
- 给定同一 request 与同一系统状态，逻辑 DAG 必须稳定。
- 派生 operation 的最终输出仍然是 `ArtifactEnvelope`。
- response 可以包含 `ExecutionManifest`，但 manifest 不是 artifact family，也不是
  下游分析输入。
- 下游引用必须指向 primary artifact 或 manifest 中列出的中间 operation 输出 artifacts，
  而不是 response bundle。

```ts
type ExecutionManifest = {
  manifest_id: string;
  root_operation: "validate" | "attribute" | "diagnose";
  primary_artifact_id: string;
  nodes: ExecutionNode[];
  edges: ExecutionEdge[];
};

type ExecutionNode = {
  node_id: string;
  operation: AoiOperation;
  inline_transforms?: Transform[];
  status: "succeeded" | "failed" | "skipped";
  output_artifact_id?: string;
  failure_code?: string;
};

type ExecutionEdge = {
  from_node_id: string;
  to_node_id: string;
  artifact_id?: string;
};
```

Manifest primary artifact 规则：

- `primary_artifact_id` 必须始终存在，不得为空字符串或 `null`。
- `primary_artifact_id` 总是指向本次派生请求返回的主 artifact。成功或 resolved failed
  时，该 artifact 属于规范输出 family：`validate` 指向 `hypothesis_test_result`，
  `attribute` 指向 `attribution_frame`，`diagnose` 指向 `diagnosis_result`。
- 如果请求在解析、artifact ref 解析或 transform 校验阶段失败，`primary_artifact_id`
  可以指向一个 `unresolved` failed artifact；该 artifact 只携带 root operation、
  optional complete subject、lineage 与 failure。
- primary artifact 可以是成功 envelope，也可以是失败 envelope。派生流程无法产生成功主结果时，
  必须创建一个失败 primary artifact，并把 `manifest.primary_artifact_missing` 或更具体的
  failure code 放入该 artifact 的 `failure.code`。
- 多候选 `diagnose` 中，某些 candidate 分支失败不等于 primary artifact 缺失。成功主结果可以在
  payload 中标明 candidate failed / skipped reason；具体失败分支通过 manifest nodes 的
  `failure_code` 与已成功节点的 `output_artifact_id` 回溯。

派生 output 规则：

- `validate` 展开为 `observe -> summarize_samples -> test`，或直接
  `sample_summary inputs -> test`。主 artifact 是 `hypothesis_test_result`。
- `attribute` 展开为
  `observe current -> observe baseline -> compare -> decompose`。主 artifact 是
  `attribution_frame`。
- `diagnose` 展开为
  `detect -> inline slice(candidate) -> observe / compare / decompose`。主 artifact 是
  `diagnosis_result`。

`diagnosis_result` 与 `ExecutionManifest` 的边界：

- `diagnosis_result` 是用户请求“诊断异常”的规范结果，包含 candidate 诊断状态、
  candidate selectors、delta refs、attribution refs，以及失败 / 跳过原因。
- `ExecutionManifest` 是执行展开记录。它说明哪些节点运行了、产出了哪些 artifacts、
  哪个 failure code 停止了某个分支。
- `diagnosis_result` 可以被 projection 和读取；manifest 用于审计和调试。
- 如果下游分析需要某个 delta 或 attribution，应引用 `diagnosis_result.payload` 或
  manifest nodes 中的 artifact ids，而不是把 manifest 当作 artifact。

## 8. 公共谱系与 Marivo 执行追踪

AOI v0.3 有两个可观测性平面。

### 8.1 公共 ArtifactLineage

`ArtifactLineage` 是公共 AOI 的一部分，承载可移植的语义谱系：

```ts
type ArtifactLineage = {
  producing_operation: AoiOperation;
  source_artifacts: ArtifactRef[];
  applied_transforms: NormalizedTransform[];
  manifest_id?: string;
};
```

它可以包含来源 artifact refs、产出 operation、规范化后的内联 transforms，以及
派生 operations 的 manifest id。它不得包含 query hash、物理表名、engine id、执行耗时、
重试次数或 debug SQL。

### 8.2 Marivo ExecutionTrace（Marivo 执行追踪）

`ExecutionTrace` 是 Marivo 实现元数据。它通过 `artifact_id` 与
`manifest_id` 连接公共 AOI 对象，但它不是公共 AOI artifact contract 的一部分。

每个 v0.3 operation trace 应记录：

- request id / session id / actor（如果可用）
- operation 名称与请求 schema 版本
- input artifact ids
- 规范化后的 inline transform 链
- validation 与 capability guard 决策
- output artifact id 或可移植 failure code
- derived operations 的 manifest id
- registry version 或 content hash
- query hash、engine id、timing、retry count、debug plan 等私有 runtime diagnostics

追踪不变量：

- 缺失 trace data 必须成为 trace warnings，不得伪造成公共 lineage。
- 公共 AOI consumers 在没有私有 trace access 时，也能理解 artifact semantics。
- Operators 可以通过 trace + artifact id / manifest id 复盘一次 failed operation。
- trace storage 可以独立于 AOI schema 演进，只要它与公共 artifacts 的链接保持稳定。

## 9. Marivo 落地路线图

AOI v0.3 是破坏性更新，因此 Marivo 实现不需要 v0.2 线缆格式兼容。路线图按架构层重建。
实现姿态采用分阶段 gate：每一层完成后必须有独立验证，再进入下一层。这样保留完整
v0.3 scope，同时避免 schema、生成模型、运行时、存储和传输面的失败混在一起。

推荐 gate 顺序：

```text
公共规范与 examples
  -> 一致性测试包
  -> 生成合同层
  -> artifact store 与 lineage
  -> runtime lowering 与 operation guards
  -> HTTP / MCP transport 与 projection
  -> E2E OSI+AOI reference scenarios
```

每个 gate 的完成标准：

- 当前 gate 的 schema、生成模型、单元测试或一致性快照必须通过。
- 不允许在后续 gate 中补救前一 gate 的合同缺口。
- 如果某一层需要临时适配，只能留在 Marivo 私有实现层，不得进入公共 AOI contract。
- 每个 gate 都必须更新对应文档与测试，避免代码先行、规范滞后。

### 9.1 公共 AOI 规范

更新 `aoi-spec/`：

- 重写 `aoi-spec/spec.md`。
- 重写 `aoi-spec/schema/aoi.schema.yaml` 与 `aoi.schema.json`。
- 新增 `aoi-spec/registry/artifact-registry.yaml`，作为 family / shape / capability /
  transform / consumer 兼容性的机器可读 source。
- 更新示例与变更日志。
- 加入 `ArtifactEnvelope`、artifact registry、transform DSL、operation compatibility、
  failure code 分类、derived manifest 与公共 lineage contract。

验收标准：所有 v0.3 示例都能通过生成模型校验，且文档表格、runtime guard 与一致性测试
引用的 registry 内容来自同一份机器可读 source。

### 9.2 一致性测试包

新增 `aoi-spec/conformance/`，作为可执行契约包。

它应包含：

- 合法 request / artifact 示例
- 非法 request / artifact 示例，以及预期失败码 family
- source-style 与 artifact-input requests 的预期逻辑 DAG 快照
- 预期 primary artifact 的 family / shape
- 代表性派生 operations 的预期 manifest 节点状态

一致性 DAG 快照必须把 inline transforms 表达为节点标注，而不是
transform artifact ids。

验收标准：schema 校验不够；一致性测试必须验证代表性场景的降级语义。

### 9.3 生成合同层

重新生成 `marivo/contracts/generated/aoi.py`，并更新面向运行时的合同 helpers。
合同层职责拆分如下：

- `scripts/generate_contract_models.py` 只负责从 schema 生成 Pydantic 模型，以及补充
  codegen 无法表达的 schema 不变量；不得承载业务兼容逻辑。
- `marivo/contracts/aoi_runtime.py` 负责 request / envelope 类型别名、operation registry
  与公共 artifact validation。
- 新增 `marivo/contracts/aoi_registry.py`，读取 `aoi-spec/registry/artifact-registry.yaml`，
  暴露 family / shape / capability / consumer 兼容性查询。
- `aoi_registry.py` 启动或首次使用时加载 registry，并暴露不可变内存 lookup；不得在每个
  operation 热路径重复读取 YAML。加载结果必须包含 registry version 或 content hash。
- `marivo/contracts/aoi_projection.py` 只负责 read / presentation projection，不参与规范
  transform DSL 或 operation guard。

验收标准：

- runtime 不再依赖 v0.2 `Artifact1` / `Artifact2` 或 result union 形态。
- operation registry 基于 v0.3 request / envelope / family / shape。
- 面向合同的 DTO 不接受 untyped `dict` / `Any` 作为 AOI payload 替代品。

### 9.4 Artifact 存储与谱系

调整 artifact 持久化，只存储 operation 输出 artifacts：

- `content_json` 存完整 `ArtifactEnvelope`。
- 存储层列化最小 guard metadata：`artifact_family`、`shape`、`failure_stage`、
  `capabilities_json`、`axes_json`、`measures_json` 与 `manifest_id`。
- `subject`、公共 `lineage`、`payload` 或 `failure` 保留在 envelope JSON 中，除非后续
  有明确查询需求再提升为列。

Inline transforms 存储为规范化 lineage / manifest data。它们不创建 artifact store
records。私有性能缓存可以存在于 artifact store 之下，但它们不是 AOI artifacts，
也不能暴露公共 `artifact_id`。

验收标准：每个 operation output 都能通过 `artifact_id` 读取，且能在不依赖私有
execution metadata 的情况下恢复公共 lineage。Runtime guard 不需要解析 family-specific
payload 就能读取 family / shape / capability / failure stage。

### 9.5 运行时降级

重写 intent runner 输入解析：

- source-style request 降级为逻辑 artifact DAG。
- artifact inputs + inline transforms 降级为带规范化 transform chains 的 operation inputs。
- 新增 `marivo/runtime/aoi_transform_guard.py`，统一执行 transform 校验边界：
  schema-normalized input、语义引用授权、compiler safety、capability guard 与 consumer guard。
- `marivo/runtime/aoi_lowering.py` 保持结构降级职责，不承担语义授权或查询编译安全判断。
- transform guard 的语义引用授权只读取 semantic model、session / actor 可见性与 artifact
  metadata；compiler safety 只做表达式结构、安全方言和字段引用检查，不生成 SQL、不触发
  engine 调用。
- transform guard 先于 operation 兼容性守卫执行。
- operation compatibility 使用 family / shape / capability / axis / measure guard。
- `compare`、`decompose`、`correlate`、`test`、`forecast` 不再直接读取 source scope。
- `validate`、`attribute`、`diagnose` 不再返回旧 derived bundle。旧
  `bundle_type + aoi_artifacts + product_metadata` 路径必须被 `primary artifact +
  ExecutionManifest` 替换；公共 transport 不保留 bundle fallback。

验收标准：所有 ref-style 原子 operations 都通过工件代数输入执行；派生 operation response
只暴露 primary artifact 与 `ExecutionManifest`，不再接受旧 bundle envelope。

### 9.6 传输与投影面

更新 HTTP / MCP adapters 与文档：

- 公共 request / response 使用 v0.3 schema。
- derived response 返回 primary artifact 与 manifest。
- projection 保持读取面 capability，永不进入规范 transform DSL。
- OpenAPI、MCP tool schema、用户文档与 AOI contract 一致。

验收标准：transport tests 证明 projection refs 不能成为下游 operation inputs。

### 9.7 测试与参考场景

重建合同测试、运行时 intent 测试、传输测试、E2E OSI+AOI 测试与一致性测试。
测试必须按 gate 绑定到实现层，不能只保留大类描述。

Per-gate test matrix：

| Gate | 测试位置 | 必测内容 |
|---|---|---|
| 公共规范与 examples | `tests/contracts/test_generated_models.py` | 所有 v0.3 examples 通过生成模型；成功 envelope、resolved failure、unresolved failure 都有正负例 |
| Registry source | `tests/contracts/test_aoi_registry.py` | `artifact-registry.yaml` 覆盖文档列出的每个 family / shape；consumer matrix 与 operation 兼容矩阵一致；未知 transform / axis / consumer 返回 typed failure |
| 生成合同层 | `tests/contracts/test_aoi_runtime_contract.py` | `validate_aoi_artifact` 只接受 v0.3 envelope；旧 `Artifact1` / `Artifact2` result union 被拒绝；operation registry 使用 v0.3 request / envelope / family / shape |
| Artifact store | `tests/contracts/artifact_store_cases.py`、`tests/adapters/test_file_artifact_store.py` | `content_json` 与最小 guard columns 一致；failure artifact 保存 `failure_stage`；跨 session artifact ref 返回 `artifact_ref.*` |
| Transform guard | `tests/runtime/test_aoi_transform_guard.py` | invalid selector、越权 metric/dimension、unsafe expression、capability mismatch、consumer mismatch 都 fail closed，并返回稳定 failure code family |
| Runtime lowering | `tests/runtime/test_aoi_lowering.py` | source-style request 降级为 logical artifact DAG；artifact inputs + inline transforms 规范化顺序；ref-style atomic operations 不再读取 source scope |
| Derived operations | `tests/runtime/test_derived_aoi_envelopes.py` 或后继 manifest 测试 | `validate` / `attribute` / `diagnose` 产生 primary artifact + `ExecutionManifest`；branch failure 产生 failed / skipped nodes；旧 bundle envelope 不再出现 |
| HTTP / MCP transport | `tests/transports/http/test_http_aoi_intents.py`、`tests/transports/mcp/test_mcp_aoi_adapter.py` | public response 使用 v0.3 schema；projection refs 不能作为下游 input；MCP compact diagnose 不依赖旧 bundle path |
| E2E OSI+AOI | `tests/integration/test_e2e_osi_aoi.py` | 参考场景端到端通过：observe、compare、transform guard、derived manifest、artifact readback 与 projection boundary |

参考场景：

- `metric_frame(panel) --inline slice--> forecast`
- `metric_frame --inline rollup--> compare`
- `metric_frame --inline summarize_samples--> test`
- `detect -> inline slice(candidate) -> compare -> decompose -> diagnosis_result`
- projection 不能作为 downstream input
- capability guard 拒绝非法组合
- 非法 transform expression 返回带类型 `transform.*` failure
- missing artifact ref 返回 `artifact_ref.*` failure

## 10. 风险、非目标与验收标准

### 10.0 What Already Exists（可复用现有能力）

- `aoi-spec/` 已经是公共 AOI 发布包，v0.3 应重写现有 spec / schema / examples，而不是创建
  第二个公共规范目录。
- `scripts/generate_contract_models.py` 已经负责 JSON Schema 到
  `marivo/contracts/generated/aoi.py` 的生成，v0.3 应沿用这条生成链。
- `marivo/contracts/aoi_runtime.py` 已经是 runtime-facing AOI 合同入口，v0.3 应演进它的
  envelope validation 与 operation registry，而不是在 runtime 下复制一套合同模型。
- `marivo/runtime/aoi_lowering.py` 已经集中处理 generated request 到 runner params 的降级，
  v0.3 应保留它的结构降级职责，并把语义 guard 放到独立 transform guard。
- `ArtifactStore` port 与 `artifacts.content_json` 已经能存完整 artifact body，v0.3 只需要补充
  最小 guard columns，不需要把 payload 拆成关系模型。
- HTTP / MCP intent tests、runtime lowering tests、generated model tests 与 E2E OSI+AOI tests
  已经存在，v0.3 应重写/扩展这些测试，而不是另建孤立测试体系。

### 10.0.1 Failure Modes（失败模式）

| 新 codepath | 现实失败模式 | 测试要求 | 用户可见结果 |
|---|---|---|---|
| Registry loader | YAML 缺 shape、重复 capability、version/hash 缺失 | registry contract test fail closed | 启动或首次调用失败，错误指向 registry |
| Envelope validation | success 同时含 `payload` 与 `failure` | generated model / contract negative test | 返回 `envelope.failure_shape_invalid` |
| Artifact store guard columns | `content_json` family 与列化 family 不一致 | artifact store contract case | artifact commit 失败，不写入 committed artifact |
| Transform guard | 越权 dimension 或 unsafe expression 进入 compiler | transform guard negative tests | 返回 `transform.*` 或 `artifact_ref.*` failure |
| Runtime lowering | source-style request 未降级成 logical artifact DAG | lowering snapshot test | request 被拒绝或 trace 标出 lowering failure |
| Derived manifest | `diagnose` 某个 branch 失败却没有 failed/skipped node | derived manifest failure test | primary `diagnosis_result` 标明 candidate failed/skipped reason |
| Projection boundary | projection ref 被误用作 downstream input | transport negative test | 返回 typed `artifact_ref.*` failure |
| Registry cache | runtime 使用旧 registry 但 trace 不可复盘 | registry cache/version test | ExecutionTrace 记录 registry version/hash |

### 10.0.2 Worktree Parallelization Strategy（并行实施策略）

该计划适合分 worktree 并行，但必须按 gate 合并，不能让 runtime 先于公共合同落地。

| Step | Modules touched | Depends on |
|---|---|---|
| Public spec + registry | `aoi-spec/`, `docs/specs/analysis/` | — |
| Generated contracts | `scripts/`, `marivo/contracts/generated/`, `marivo/contracts/` | Public spec + registry |
| Artifact store metadata | `marivo/ports/`, `marivo/adapters/`, `tests/contracts/`, `tests/adapters/` | Generated contracts |
| Runtime lowering + transform guard | `marivo/runtime/`, `tests/runtime/` | Generated contracts, Artifact store metadata |
| Derived manifests | `marivo/runtime/intents/`, `tests/runtime/` | Runtime lowering + transform guard |
| Transport + projection | `marivo/transports/`, `marivo/contracts/aoi_projection.py`, `tests/transports/` | Runtime lowering, Derived manifests |
| E2E + conformance | `aoi-spec/conformance/`, `tests/integration/` | All prior gates |

Parallel lanes:

- Lane A：Public spec + registry → Generated contracts。
- Lane B：Artifact store metadata（等 Generated contracts 后启动）。
- Lane C：Runtime lowering + transform guard → Derived manifests（等 Generated contracts 与 store metadata 后启动）。
- Lane D：Transport + projection（等 Runtime / Derived 后启动）。
- Lane E：E2E + conformance（最后收口）。

执行顺序：先完成 Lane A；随后 B 与 C 可在不同 worktree 并行，但 C 不能合并早于 B 的
artifact guard contract；再做 D；最后 E。

合并策略：

- Lane A 的 `aoi-spec` schema 与 `artifact-registry.yaml` 是 canonical source。
- Lane B / C / D 不得各自发明接口定义；合并前必须 rebase 到最新 Lane A registry，
  并删除与 registry 不一致的本地 shape / capability 假设。
- 推荐合并顺序是 A -> B -> C -> D -> E。每个 lane 合并前必须运行对应 gate 的验收测试；
  失败时回到该 lane 修正，不在后续 lane 中补救合同缺口。

冲突提示：B 与 C 都会触及 artifact shape 假设，合并前必须跑合同测试和 runtime lowering
tests。C 与 D 都会碰 AOI response shape，transport worktree 必须基于 derived manifest
合并后的接口。

### 10.1 主要风险

- Envelope 过胖：如果公共 `lineage`、`subject`、`capabilities` 吸收实现细节，AOI
  会失去只依赖 schema 的可移植性。公共 envelope 只保留语义谱系；Marivo
  私有 diagnostics 留在 `ExecutionTrace`。
- Transform DSL 变成隐藏 intent 系统：transforms 不能执行分析推断，也不能产生
  公共 artifacts。
- Capability labels 变成 status labels：`capabilities` 只服务兼容性守卫，不能表达
  `ready`、`needs_attention` 或 `valid`。
- Manifest 被误当作 artifact：manifest 是执行展开记录，不是分析输出。
- `sample_summary` 边界不清：v0.3 将它定义为 `metric_frame` shape，而不是独立
  family。
- 破坏性更新范围大：v0.3 会重写 spec、生成模型、runtime、transport 与 tests。
  本文有意不定义兼容性迁移。
- Artifact 生命周期不在范围内：invalidation、supersession、stale reads 与 mixed-version
  lifecycle 语义留在 Evidence Engine 文档和后续设计中，不属于本文的 AOI v0.3
  目标设计。
- 发布姿态不在范围内：v0.3 被视为干净的 breaking target；本文不定义 feature
  flags、dual-mode operation 或 rollback steps。

### 10.2 非目标

- 不新增公共 operation namespace。
- 不保留 v0.2 artifact 线缆格式兼容。
- 不定义 transport、storage engine、execution scheduler 或发布实现细节。
- 不把 inline transforms 物化成公共 artifacts。
- 不把 projection 变成 canonical transform。
- 不把 open-ended `explain` / `describe` 变成 AOI operations。
- 不把 engine id、query hash、wall time、debug SQL 或物理存储定位符放进公共 AOI。
- 不在本文解决 artifact lifecycle、invalidation 或 supersession。

### 10.3 验收标准

- 所有公共 operations 都能通过 `ArtifactEnvelope + inline transform DSL` 表达输入输出。
- artifact registry 定义合法 shapes、axes、measures、capabilities、transforms 与 consumers。
- 每个 transform 都有输入要求、输出有效形态规则、校验边界与非法组合。
- `validate`、`attribute`、`diagnose` 定义 expansion DAGs、primary artifacts 与 manifest
  semantics。
- Failure envelope 语义能区分 resolved / unresolved failures，且不伪造 metadata。
- Failure code 分类可移植，并被代表性一致性测试夹具覆盖。
- 一致性测试包同时验证 schema 有效性与逻辑降级 DAG 快照。
- Marivo roadmap 覆盖 `aoi-spec`、conformance、generated contracts、artifact store、
  runtime lowering、transport / projection 与 tests。
- 文档清楚区分公共 AOI contract、Marivo 执行追踪与 projection / read surface。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | ISSUES_OPEN | 9 proposals, 7 accepted, 0 deferred |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAN | 8 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- UNRESOLVED：0
- VERDICT：ENG CLEARED；CEO Review 仍记录为 ISSUES_OPEN，因为本轮只运行工程评审。
