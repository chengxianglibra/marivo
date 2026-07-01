# Python Analysis Design

状态：draft design。本文描述 `marivo.analysis` Python 库的目标态算子集合、类型边界、组合方式以及 Evidence Access Surface。它是设计侧说明，不表示所有目标态目标态能力已经实现。

本文只从未来合理性定义目标态，不以当前实现或既有设计文档为兼容约束。

## 设计目标

`marivo.analysis` 的 Python API 不应是一组 BI 功能菜单，也不应把 SQL、表、列或临时 workflow 暴露为主要契约。它应该是一组围绕 canonical artifact 的可组合算子，并且优先服务 Claude Code、Codex 这类通用 agent 在复杂互联网企业数据分析中的真实工作流。

目标态 API 满足以下要求：

- 让 agent 和 Python 调用方用少量稳定 core operator 表达常见 metric 分析。
- 高频复杂业务路径通过强契约 composite operator 暴露，而不是全部推给 SQL / pandas escape hatch。
- 每个公开 core operator 固定输出一个 canonical artifact family。
- 参数只能改变算法、粒度、scope、ranking policy 或策略，不能改变输出 family。
- 下游组合通过 artifact ref、selector ref、typed policy、typed follow-up 和 typed input 完成，不依赖自由文本解释。
- 探索式分析统一沉淀为 typed `candidate_set[...]`，不再为 anomaly、driver、window、outlier 分别暴露一等 core operator。
- 对 agent 侧只暴露 `composite operator` 这个统一概念；runtime 内部可用 `contract_level` 区分 canonical、domain、exploratory 契约。
- 默认 authoring model 是 step-wise analysis session：agent 可以读取中间 projection 后继续下一步，同时保持 continuous lineage。

核心判断标准是：如果一个能力会在不同参数下返回不同 artifact family，它不应作为单个公开 core operator 存在，必须拆成多个 core operator、提升为 typed composite operator，或降级为 projection / escape hatch。为了 agent ergonomics，允许在同一 family 内使用封闭 typed shape，例如 `MetricFrame[time_series]`、`CandidateSet[driver_axis]`、`AssociationResult[lag_sweep]`。

## 分层

Python Analysis API 分为五层。

### 1. Source-to-artifact 算子

这层从 semantic layer 读取 metric，产出分析链路的起点。

```text
observe -> metric_frame
```

### 2. Family-preserving transform 算子

这层只改写已有 artifact 的形状、范围、粒度或表达方式，不改变 artifact family。

```text
transform.<op>(metric_frame) -> metric_frame
transform.<op>(delta_frame) -> delta_frame
transform.<op>(attribution_frame) -> attribution_frame
```

`transform` 是泛型 family-preserving 算子。它的输出 family 由输入 family 决定，不能通过 `op` 改变。跨 family 派生必须使用命名算子：`metric_frame -> delta_frame` 只能通过 `compare`，`delta_frame -> attribution_frame` 只能通过 `decompose`。

### 3. Core cross-family analysis 算子

这层执行真正改变分析语义的操作，每个算子有固定输出 family。

```text
compare -> delta_frame
decompose -> attribution_frame
discover -> candidate_set
correlate -> association_result
hypothesis_test -> hypothesis_test_result
forecast -> forecast_frame
assess_quality -> quality_report
```

### 4. Composite operator 层

这层不属于 core operator 集合，但属于 agent 应该优先使用的 Python Analysis 契约面。它把多步 DAG、行业高频分析和 agent ergonomic shorthand 包成稳定入口。

```text
composite operator -> core operator DAG / domain DAG -> typed artifact or typed result
```

每个 composite operator 必须标注 `contract_level`：

| Contract level | Runtime 承诺                                                                     | 典型能力                                                                        |
| -------------- | ------------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| `canonical`    | 稳定 request / response schema、确定性 DAG、完整 lineage / evidence / failure semantics | `attribute` |
| `exploratory`  | 可辅助探索，不默认进入 canonical evidence 链路                                              | `auto_decompose`, `diagnose`, `driver_attribution_scan` |

对 agent 侧不再要求区分 derived intent 与 recipe；二者统一表现为 composite operator。runtime 内部保留 contract level，是为了明确版本化、lineage、evidence、失败语义和结果可信度。

### 5. Projection / escape hatch 层

Projection 是读取视图，例如 `artifact.summary()`、`artifact.profile()`、`artifact.preview()`。Escape hatch 是受控地进出 canonical 链路：Ibis / pandas 结果默认是 scratch，只有经过 promotion 才能成为 canonical `*_frame`。

```text
projection -> bounded read view
Ibis / pandas -> exploration_result -> promotion -> canonical frame
canonical frame -> toPandas() -> scratch dataframe
```

## Canonical family registry

Python Analysis 的 core operator 与 composite operator 必须引用同一份未来 canonical family registry。调用方、planner、selector resolver、UI 和 evidence extraction 不应各自发明 family 名称。

目标态 canonical families 如下：

| Family                       | 主要 producer                         | 主要语义                                           |
| ---------------------------- | ----------------------------------- | ---------------------------------------------- |
| `metric_frame`               | `observe`, `transform`              | 已观测的指标事实面                                      |
| `delta_frame`                | `compare`, `transform`              | 两个 `metric_frame` 的差异                          |
| `attribution_frame`          | `decompose`, `transform`            | delta 的贡献分配                                    |
| `candidate_set`              | `discover`                          | 可 follow-up 的候选集合                              |
| `association_result`         | `correlate`                         | 两个或多个 frame 的统计关联                              |
| `hypothesis_test_result`     | `hypothesis_test`                   | 明确统计假设的检验结果                                    |
| `forecast_frame`             | `forecast`                          | 未来 bucket 的模型投影                                |
| `quality_report`             | `assess_quality`                    | artifact 质量、覆盖率和前提可用性评估                        |
| `diagnosis_result`           | `diagnose` composite                | 针对候选异常或业务问题的多步骤诊断模板（exploratory，不进入 canonical evidence） |
| `driver_scan_result`         | `driver_attribution_scan` composite | 多 axis attribution scan 的固定结果                  |

以下对象不是公开核心算子的 output family：

- `sample_frame`：`hypothesis_test` 内部 materialized sampling node，可进入 lineage / manifest，但不要求 agent 手写。
- `artifact_summary` / `artifact_profile`：projection result，不进入 canonical artifact 链路。
- `exploration_result`：SQL / pandas escape hatch 的 non-canonical scratch result。

## Typed shape 与 typed policy

固定 output family 不等于所有 family 都只有一种内部形状。为避免 agent 在 runtime 才撞到 shape mismatch，目标态 API 必须把 shape 和 policy 做成封闭类型。

### Frame / result shape alias

| Alias | 底层 family | 合法 shape |
| --- | --- | --- |
| `MetricFrame[scalar]` | `metric_frame` | 单点指标 |
| `MetricFrame[time_series]` | `metric_frame` | 单指标时间序列 |
| `MetricFrame[segmented]` | `metric_frame` | 单时间或无时间的分段指标 |
| `MetricFrame[panel]` | `metric_frame` | 多 segment x time panel |
| `CandidateSet[point_anomaly]` | `candidate_set` | 点异常候选 |
| `CandidateSet[period_shift]` | `candidate_set` | 结构性 period shift 候选 |
| `CandidateSet[driver_axis]` | `candidate_set` | 可归因 semantic axis 候选 |
| `CandidateSet[slice]` | `candidate_set` | 值得下钻的 slice 候选 |
| `CandidateSet[window]` | `candidate_set` | 值得复看的时间窗口候选 |
| `CandidateSet[cross_sectional_outlier]` | `candidate_set` | 截面离群 segment 候选 |
| `AssociationResult[single_lag]` | `association_result` | 单个明确 lag 的关联结果 |
| `AssociationResult[lag_sweep]` | `association_result` | 封闭 lag policy 下的 lag scan 结果 |
| `QualityReport[metric]` | `quality_report` | metric frame 质量与覆盖率 |
| `QualityReport[delta]` | `quality_report` | delta 可比性与覆盖率 |
| `QualityReport[candidate]` | `quality_report` | candidate 生成前提可用性 |
| `QualityReport[forecast]` | `quality_report` | forecast 输入与模型前提可用性 |
| `QualityReport[attribution]` | `quality_report` | 归因覆盖率、残差与可解释性前提 |

shape 是封闭枚举。新增 shape 必须同时更新 family registry、producer、consumer compatibility、projection 和 evidence / follow-up 规则。

### Typed policy

以下策略对象必须是 typed object，不允许用裸 dict 或自由文本字符串表达：

| Policy | 用途 | 典型字段 |
| --- | --- | --- |
| `AlignmentPolicy` | compare / correlate / hypothesis_test 的跨输入对齐，以及 transform 的单 frame 时间对齐 | `kind`, `mode`, `strict_lengths`, `calendar`, `fiscal_calendar`, `campaign_window`, `forecast_origin`, `horizon_index`, `submission_time_policy`, `timezone` |
| `SamplingPolicy` | hypothesis_test 内部 sampling / pairing / null handling | `unit`, `method`, `pairing`, `null_handling`, `min_n` |
| `PromotionPolicy` | escape hatch promotion 的 anchors 回退和 fail-closed 校验 | `semantic_anchors`, `required_fields`, `on_missing` |

plan compile 阶段应尽量检查 family + shape + policy compatibility。只有依赖真实数据统计量的错误才应留到 runtime。

`AlignmentPolicy.kind` 是封闭枚举：

- `window_bucket`
- `dow_aligned`
- `holiday_aligned`
- `holiday_and_dow_aligned`
- `fiscal_period`
- `campaign_relative`
- `forecast_horizon`

`mv.window_bucket()` 默认使用 `mode="ordinal_bucket"`：按两个
window 内的 bucket 序号配对，不根据绝对日期是否重叠自动切换语义。若两侧 window
推导出的 bucket 数不同，默认按序号外连接，缺失侧填空并在 alignment coverage 中记录
`paired_buckets`、`current_unpaired_buckets` 和 `baseline_unpaired_buckets`。需要绝对
bucket identity 对齐时，显式使用 `mode="calendar_bucket"`，按规范化后的 bucket key
做 outer union。只有在调用方明确要求等长同期窗口时，才设置 `strict_lengths=True`；此时
ordinal bucket 数不等会在 runtime 报错。

`holiday_and_dow_aligned` 表示先做 holiday 对齐；无法按 holiday 匹配的非节假日 bucket，再按 day-of-week 对齐。

日历 holiday 条目用单字段 `holiday_id` 标识所属假期，对齐键为 `("holiday", holiday_id, 天序)`：

- 天序由系统在对齐周期内对同一 `holiday_id` 的假期日按日期升序派生（day-k 对 day-k），不要手工编号（不要写 `"wy-day1"`）。
- `holiday_id` 必须年份无关：同一假期跨年复用同一个 id（如 `"wy"`，不要写 `"2026-wy"`）。一个假期的所有天用同一个 `holiday_id` 即可。
- 因天序按周期派生，同周期内同 `holiday_id` 的不同天天序必不同，不再触发 `CalendarAlignKeyNotUnique`；该校验现在只在同一天出现重复数据行时触发。
- 不设 `holiday_id` 会回退到日期字符串（同周期内唯一，但跨年/跨周期无法匹配）；`holiday_id` 含年份也会因 id 不同而静默丢行。

所有 provider 字段都必须是 typed ref，而不是裸字符串：`CalendarRef`、`FiscalCalendarRef`、`CampaignWindowRef`、`ForecastOriginRef`。Forecast evaluation 是计划中的能力，不属于当前公共 Session API；若后续加入，必须支持 forecast-specific alignment：按 forecast origin、horizon index 和 submission timestamp policy 对齐滚动预测。

### Semantic catalog refs

所有 semantic 入口必须来自 catalog-resolved typed ref，不允许让 agent 猜字符串名称。

| Catalog ref | 用途 |
| --- | --- |
| `SemanticObject | SemanticRef` (`kind="metric"`) | 指标定义 |
| `EntityRef` | 事件表、事实表、entity table 等数据集 |
| `SemanticObject | SemanticRef` (`kind="dimension" | "time_dimension"`) | 可切分维度或 semantic axis |
| `CalendarRef` / `FiscalCalendarRef` | 日历和财务日历 |
| `CampaignWindowRef` | 活动窗口 |

PromotionPolicy 的 `semantic_anchors` 也应携带这些 typed refs。若 agent 只有字符串，应先通过 catalog lookup / disambiguation 拿到 ref，再提交 analysis step。

## 设计原则

### 固定输出 family

每个公开 core operator 必须有唯一 canonical output family。

合法示例：

```python
delta = analysis.compare(current, baseline)          # delta_frame
drivers = analysis.decompose(delta, axis=session.catalog.get("sales.orders.country")) # attribution_frame
candidates = analysis.discover.driver_axes(delta, search_space=[session.catalog.get("sales.orders.country")]) # candidate_set
```

### Shape-aware 签名

Core operator 的契约必须同时声明 family 和 shape。Shape alias 不是展示性注释，而是 compile-time / plan-time gate。

| Operator | Shape-aware signature |
| --- | --- |
| `observe` | `observe(SemanticObject | SemanticRef, ...) -> MetricFrame[scalar | time_series | segmented | panel]` |
| `transform.<op>` | `transform.topk(...)`, `transform.rollup(...)`, etc. preserve `Frame[T] -> Frame[T]` |
| `compare` | `compare(MetricFrame[T], MetricFrame[T], alignment=AlignmentPolicy) -> DeltaFrame[shape_for(T)]` |
| `decompose` | `decompose(DeltaFrame[T], axis=SemanticObject | SemanticRef) -> AttributionFrame` |
| `discover.point_anomalies` | `discover.point_anomalies(MetricFrame[time_series | panel]) -> CandidateSet[point_anomaly]` |
| `discover.period_shifts` | `discover.period_shifts(DeltaFrame[time_series_delta | panel_delta]) -> CandidateSet[period_shift]` |
| `discover.driver_axes` | `discover.driver_axes(DeltaFrame[*], search_space=[...]) -> CandidateSet[driver_axis]` |
| `discover.interesting_slices` | `discover.interesting_slices(MetricFrame[*] | DeltaFrame[*]) -> CandidateSet[slice]` |
| `discover.interesting_windows` | `discover.interesting_windows(MetricFrame[time_series | panel] | DeltaFrame[time_series_delta | panel_delta]) -> CandidateSet[window]` |
| `discover.cross_sectional_outliers` | `discover.cross_sectional_outliers(MetricFrame[segmented | panel]) -> CandidateSet[cross_sectional_outlier]` |
| `correlate` | `correlate(MetricFrame[T], MetricFrame[T], alignment=AlignmentPolicy) -> AssociationResult[single_lag]` |
| `hypothesis_test` | `hypothesis_test(MetricFrame[T], MetricFrame[T], hypothesis=..., sampling=SamplingPolicy) -> HypothesisTestResult` |
| `forecast` | `forecast(MetricFrame[time_series | panel], ...) -> ForecastFrame` |
| `assess_quality` | `assess_quality(Artifact[T]) -> QualityReport[shape_for(T)]` |

Session compile 阶段必须做 shape gate。比如 `forecast(MetricFrame[scalar])`、`discover.point_anomalies` over segmented-only frame、或 `correlate` 两侧 shape 不同，都应在提交前失败，而不是等 runtime 扫数据后才报错。

核心层不合法示例：

```python
analysis.decompose(delta, axis="auto")
```

如果 `axis="auto"` 有时返回候选维度、有时返回 attribution，它就混合了两个语义。核心层应拆成：

```python
axis_candidates = analysis.discover.driver_axes(delta, search_space=[session.catalog.get("sales.orders.country")])
selected_axis = analysis.select(axis_candidates, rank=1, attribute="axis")
drivers = analysis.decompose(delta, axis=selected_axis)
```

为了 agent ergonomics，Python API 可以额外提供 composite operator：

```python
drivers = analysis.composites.auto_decompose(
    delta,
    objective="largest_explainable_delta",
    search_space=[session.catalog.get("sales.orders.country"), session.catalog.get("sales.orders.platform"), session.catalog.get("sales.orders.channel")],
)
```

`auto_decompose` 固定输出 `attribution_frame`，候选 axis 排名进入 `attribution_frame.metadata.axis_candidates`。这不是放宽 core `decompose` 的输出原则，而是把常见多步路径提升为 typed composite。

### Transform 不做跨 family 派生

`transform` 负责 frame reshaping，不负责生成新的分析结论。

允许：

- `filter`
- `slice`
- `rollup`
- `topk` / `bottomk`
- `rank`
- `normalize`
- `window`
- `align_time`
- `dedupe`
- `impute_nulls`
- `winsorize` / `strip_outliers`

不允许：

- `transform.compare(metric_frame) -> delta_frame`
- `transform.decompose(delta_frame) -> attribution_frame`
- `transform.discover(frame) -> candidate_set`
- `transform.align(frame_collection) -> frame_collection`

`transform` 是一个 API，而不是 `transform_metric`、`transform_delta`、`transform_attribution` 三套 API。类型安全应由输入 artifact family、op compatibility matrix 和 runtime validation 保障，不应把同一个逻辑操作暴露成三种名字让 agent 学习。

跨 frame alignment 不属于 `transform`。`compare`、`correlate`、`hypothesis_test` 需要对齐多个输入时，应在各自请求中声明 `AlignmentPolicy`，并把对齐结果写入该算子的 lineage / metadata。`transform.align_time(...)` 只处理单个 frame artifact 的时间轴规范化，例如 fiscal week、campaign-relative time、cohort-relative period 或 calendar bucket 重写；它不能接受 frame collection，也不能输出多个 frame。

数据清理也属于 family-preserving transform，但必须显式记录 cleaning policy 和影响范围。`dedupe`、`impute_nulls`、`winsorize`、`strip_outliers` 不能静默修改事实，应在 lineage 中记录行数、点数、受影响 measure、策略和 residual quality warning。

### Candidate discovery 统一到 `discover.<objective>`

未来目标态不再保留独立 `detect` core operator。用于选择下一步分析对象的候选发现结果，统一沉淀为 `candidate_set`。异常检测、变点检测、driver axis 搜索、窗口发现、跨段离群都统一为：

```text
discover.<objective>(source_artifact, ...) -> candidate_set
```

helper 名表达 agent 要找什么；strategy 由 helper 的封闭默认策略决定，不能是自然语言字符串。

| Objective | 合法输入 | Output shape | Item 必填字段 | 推荐下一步 |
| --- | --- | --- | --- | --- |
| `point_anomalies` | `MetricFrame[time_series | panel]` | `CandidateSet[point_anomaly]` | `window`, `source_refs`, `score`, `direction` | `recommended_followups` |
| `period_shifts` | `DeltaFrame[time_series_delta | panel_delta]` | `CandidateSet[period_shift]` | `window`, `baseline_window`, `source_refs`, `score`, `direction` | `recommended_followups` |
| `driver_axes` | `DeltaFrame[*]` | `CandidateSet[driver_axis]` | `axis`, `score`, `reason_codes` | `recommended_followups` |
| `interesting_slices` | `MetricFrame[*]` / `DeltaFrame[*]` | `CandidateSet[slice]` | `selector`, `keys`, `score`, `reason_codes` | `recommended_followups` |
| `interesting_windows` | `MetricFrame[time_series | panel]` / `DeltaFrame[time_series_delta | panel_delta]` | `CandidateSet[window]` | `window`, `source_refs`, `score`, `reason_codes` | `recommended_followups` |
| `cross_sectional_outliers` | `MetricFrame[segmented | panel]` | `CandidateSet[cross_sectional_outlier]` | `keys`, `peer_scope`, `score`, `direction` | `recommended_followups` |

`discover` 只输出候选，不输出 attribution、diagnosis、hypothesis test result 或新的事实 frame。`candidate_set` 的职责是表达“下一步值得看哪里”，不是表达“看完以后得出的结论”。候选生成前提是否可靠由 `assess_quality(candidate_set)` 判断；候选是否为真异常或真驱动因素，需要后续 `hypothesis_test`、composite validation workflow 或 agent judgment。

适合进入 `candidate_set` 的能力：

- 找异常点、异常窗口、结构性变化。
- 找值得下钻的 slice、segment、axis。
- 找跨段离群或值得复看的 peer group。

不适合进入 `candidate_set` 的能力：

- 新事实面：应输出 `metric_frame`、`delta_frame` 或其它 frame family。
- 模型结果：应输出 `forecast_frame` 或未来单独 family。
- 检验结论：应输出 `hypothesis_test_result`。
- 归因结果：应输出 `attribution_frame`。
- 分群标签或新语义维度：应设计独立 artifact family 或 composite operator。

新增 `objective` 必须同时满足以下条件：

1. 输出仍然能表示为 `candidate_set`，而不是新的事实 frame、判断结果或模型结果。
2. Candidate item 能由通用字段和少量 shape-specific 字段表达。
3. 下游消费路径能通过 `select`、`transform`、`decompose`、`hypothesis_test`、`forecast` 或 composite operator 完成。
4. 不需要新增一类 canonical artifact family。

如果某个能力需要新的 artifact family、新的下游算子，或 item schema 无法自然落到 candidate 模型，应优先设计独立 core operator 或 composite operator，而不是继续扩张 `discover.objective`。

### Judgment 与事实分离

算子输出应区分“事实载体”“候选发现”和“判断结果”。

- `metric_frame`、`delta_frame`、`attribution_frame`、`forecast_frame` 是 frame artifact。
- `candidate_set` 是候选集合，不表示候选已经被证明。
- `hypothesis_test_result` 是统计假设检验结果。
- `quality_report` 是对已有 artifact 的质量、覆盖率和前提可用性评估。
- `diagnosis_result` 属于 `diagnose` composite operator，不属于 `assess_quality`。
- `artifact_summary` / `artifact_profile` 是 projection，不是分析证据。

## 算子目录

### `observe`

职责：读取 semantic metric，产出标准观测帧。

固定输出：

```text
metric_frame
```

支持形态：

- `scalar`
- `time_series`
- `segmented`
- `panel`

`observe` 只负责“当前观测是什么”。它不负责比较、不负责 profile、不负责异常检测，也不做跨 frame calendar pairing。

当 `observe` 产出 `time_series` 或 `panel` 时，公开 frame 的 `bucket_start`
是 report timezone 下的 bucket label，并以 timezone-naive timestamp/date
输出。后端返回的 datetime 解码差异必须在 executor 后处理阶段归一化；下游
`compare`、`decompose` 等 operator 不应重新猜测 `bucket_start` 的时区语义。

Phase 1 cross-dataset observe supports base metrics whose non-root datasets are
reachable through key-derived many-to-one or one-to-one relationships. Joins are
root-preserving left joins. Cross-dataset `dimensions=` and `slice_by=` are allowed
for base metrics. Root predicates are pushed before widening; joined predicates
apply after widening. `session.explain(...)` is not part of this phase.

### Derived Observe

Phase 2 derived metrics share the same planner as base metrics. Each
component metric is planned independently as a `BaseObservePlan` with the
same root-only-measure, key-derived join safety, and root-preserving
left-join rules. Component metrics may declare more than one dataset, and
different components may use different datasources; per-component plans
must each be single-datasource.

Derived dispatch enforces three fail-closed comparability checks:

- `component-axis-unreachable` / `component-axis-field-mismatch` — every
  parent dimension must resolve to the same semantic field id in every
  component.
- `component-filter-unreachable` / `component-filter-field-mismatch` —
  every parent `slice_by` predicate must apply to every component, to the
  same semantic field id.
- `component-version-mismatch` — versioned datasets accessed by multiple
  components must share derived version mode + anchor + resolved partition
  or interval predicate + mapping digest.

## Versioned Joins

`ms.snapshot()` and `ms.validity()` declare dataset versioning. The
planner auto-selects `as_of_root_time` when the root dataset has a
day-level time field; otherwise it falls back to `latest` anchored on
`time_scope.end` or plan time. There is no per-relationship override and
no metric-level kwarg.

Snapshot `as_of_root_time` runs two narrow discovery queries before the
join (distinct root anchor dates, distinct available partitions) to build
a Python-side anchor-to-partition mapping that is then injected as an
`ibis.memtable` and equi-joined against the snapshot table. Validity
`as_of_root_time` evaluates per-row interval predicates inline; overlap is
recorded as a single `validity_overlap_unverified` lineage warning per
join and not validated in Phase 2.

如果 agent 需要复用某种业务时间表达，例如 fiscal week、campaign-relative day 或 cohort-relative period，应先 `observe` 原始 metric，再用 `transform.align_time(policy=AlignmentPolicy(...))` 生成单 frame 的 aligned view。两个 frame 之间的 pairwise alignment 仍属于 `compare`、`correlate` 或 `hypothesis_test` 的职责。

示例：

```python
dau = analysis.observe(
    metric=session.catalog.get("analytics.dau"),
    time="last_30d",
    grain="day",
)
```

#### Grain parameter

`observe` and `compare` accept `grain` to specify time-series bucket
resolution. Public agent-facing usage passes grain as a token string:

| Form | Example | Semantics |
| --- | --- | --- |
| string token | `"day"` or `"5minute"` | Single-unit calendar grains or sub-day multi-bucket grains |

Dynamic sub-day grains (`count > 1` with unit `minute` or `hour`) produce
time-series buckets at a finer resolution than day.  The requested grain
must satisfy two constraints relative to the metric's time field:

1. **Base granularity rule**: the time field must declare `granularity`
   at least as fine as the requested grain.  For example, requesting
   `grain=(5, "minute")` requires the time field to have
   `granularity="minute"` or `granularity="second"`; a day-level time
   field cannot serve minute-level buckets.  Violations raise
   `GrainUnsupportedError`.

2. **Day divisibility rule**: sub-day grains must divide a day evenly.
   `5minute` (288 buckets/day), `15minute`, `30minute`, `1hour`, and
   `4hour` are valid; `7minute` is not (86400 % 420 != 0).

Calendar grains (`day`, `week`, `month`, `quarter`, `year`) only support
`count == 1` and do not require sub-day time fields.

### `transform`

职责：对 frame artifact 做 family-preserving 改写，使其适合下游消费。

固定输出：

```text
same family as input
```

`transform` 只消费以下 canonical frame families：

- `metric_frame`
- `delta_frame`
- `attribution_frame`

其他 families 不能作为 `transform` 输入。`candidate_set` 通过 `select` expression 消费，`association_result`、`hypothesis_test_result`、`forecast_frame`、`quality_report` 只能通过 projection、typed follow-up 或专门 composite operator 消费。

典型操作：

| Op | 合法输入 family | 输出 | 语义 |
| --- | --- | --- | --- |
| `filter` | `metric_frame`, `delta_frame`, `attribution_frame` | same family | 按 predicate 保留子集 |
| `slice` | `metric_frame`, `delta_frame`, `attribution_frame` | same family | 按 selector / keys 收窄子空间 |
| `rollup` | `metric_frame`, `delta_frame`, `attribution_frame` | same family | 沿已声明层级降低粒度或合并分组 |
| `topk` / `bottomk` | `metric_frame`, `delta_frame`, `attribution_frame` | same family | 保留排序后的前 N 项 |
| `rank` | `metric_frame`, `delta_frame`, `attribution_frame` | same family | 添加或更新排序 |
| `normalize` | `metric_frame` | same family | index、share、pct_change、per-unit、z-score 等重表达；`delta_frame` 在 v1 中显式拒绝，直到能同时维护 current/baseline/delta/pct_change 不变量 |
| `window` | time-axis `metric_frame`, time-axis `delta_frame` | same family | 收窄到指定时间窗口 |
| `align_time` | time-axis `metric_frame`, time-axis `delta_frame` | same family | 按 `AlignmentPolicy` 重写单 frame 时间轴 |
| `dedupe` | `metric_frame` | same family | 按 typed key policy 去重并记录影响 |
| `impute_nulls` | `metric_frame`, `delta_frame` | same family | 显式填补缺失并记录策略 |
| `winsorize` / `strip_outliers` | `metric_frame`, `delta_frame` | same family | 显式限制或剔除离群点并记录策略 |

Sampled folded MetricFrames set `reaggregatable=False`. `transform.rollup(...)` must fail closed and instruct callers to re-run `session.observe(...)` at the target grain or dimension set.

示例：

```python
mobile_dau = analysis.transform.slice(
    dau,
    slice_by={session.catalog.get("sales.orders.platform"): "mobile"},
)

top_declines = analysis.transform.topk(
    delta,
    by="delta_pct",
    direction="decrease",
    limit=10,
)
```

### `compare`

职责：比较两个可比 `metric_frame`，产出 delta。

固定输出：

```text
delta_frame
```

`compare` 回答“变了多少”，不回答“为什么变”。它消费已提交或已解析的 `metric_frame`，不在请求中重复描述 metric、scope 或 filter。

示例：

```python
current = analysis.observe(metric=session.catalog.get("sales.gmv"), time="this_week", grain="day")
baseline = analysis.observe(metric=session.catalog.get("sales.gmv"), time="previous_week", grain="day")
delta = analysis.compare(
    current,
    baseline,
    alignment=analysis.dow_aligned(calendar=analysis.CalendarRef("company")),
)
```

### `decompose`

职责：把已定义的 `delta_frame` 沿显式 semantic axes 分配到贡献项。

固定输出：

```text
attribution_frame
```

`decompose` 回答“这个 delta 由谁贡献”。核心 `decompose` 不负责自动找轴；自动找轴属于 `discover.driver_axes(...)` 或 `analysis.composites.auto_decompose(...)`。

核心 `decompose` 接受有序 semantic axes。单轴是 level-1 贡献分解；多轴表示有序分层 drilldown：`country -> platform` 回答“某个 country 内 platform 如何贡献”。这不是无序多轴联合归因；`country x platform` 联合归因回答“哪个 country-platform 组合贡献最大”。后者应作为 domain composite operator，待 semantic layer 支持相关数据模型后设计。

示例：

```python
country_drivers = analysis.decompose(delta, axes=[session.catalog.get("sales.orders.country")])
country_platform_drivers = analysis.decompose(
    delta,
    axes=[
        session.catalog.get("sales.orders.country"),
        session.catalog.get("sales.orders.platform"),
    ],
)
```

### `discover`

职责：在已提交 artifact 上做候选发现。

固定输出：

```text
candidate_set
```

示例：

```python
anomalies = analysis.discover.point_anomalies(
    dau,
    threshold=1.0,
)

axis_candidates = analysis.discover.driver_axes(
    delta,
    search_space=[session.catalog.get("sales.orders.country"), session.catalog.get("sales.orders.platform"), session.catalog.get("sales.orders.channel")],
)
```

### `correlate`

职责：估计两个或多个可对齐 frame 之间的统计关联。

固定输出：

```text
association_result
```

`correlate` 输出 association，不输出 causation。它不做 metric scan、因果控制或 lag sweep。当前 public runtime 固定 zero-lag 行为，产出 `AssociationResult[single_lag]`。

`association_result` 是描述性关联结果。核心 DAG 不支持 `association_result -> hypothesis_test` 或 `association_result -> decompose`：如果 agent 要验证关联是否显著，应回到产生该关联的 source `metric_frame`，用明确 hypothesis 调用 `hypothesis_test` 或 composite validation workflow；如果要分析哪些 segment 驱动了关联，应使用 composite operator 回到源 frames 分段计算，而不是把 association 本身当作可分解 delta。

示例：

```python
relationship = analysis.correlate(
    signup_rate,
    activation_rate,
    method="spearman",
)
```

### `hypothesis_test`

职责：对明确统计假设做检验。

固定输出：

```text
hypothesis_test_result
```

`hypothesis_test` 直接接受 `metric_frame` 或已解析的 test-ready input。Agent 不需要手写 `sample_summary`。如果检验需要样本摘要，runtime 应在 `hypothesis_test` 的 execution manifest / lineage 中 materialize 内部 sampling node，记录 sampling policy、null handling、rate numerator / denominator、pairing 和 sample size。

示例：

```python
result = analysis.hypothesis_test(
    current,
    baseline,
    hypothesis="mean_changed",
    sampling=SamplingPolicy(
        unit="day",
        method="paired_numeric_summary",
        pairing="window_bucket",
        null_handling="drop_pair",
    ),
)
```

### `forecast`

职责：把历史 time-series `metric_frame` 投影到未来。

固定输出：

```text
forecast_frame
```

`forecast` 输出预测值、区间、模型元数据和 forecast 自身可计算的风险信号。它不等价于 `metric_frame`，因为 forecast 是模型投影，不是已观测事实。

示例：

```python
next_30d = analysis.forecast(dau, horizon="30d")
```

### Forecast evaluation

当前公共 Session API 不提供 forecast evaluation operator。Forecast 结果可通过 `forecast_frame` metadata / projection 暴露区间宽度、漂移风险和模型可用性；actual-vs-forecast evaluation 是计划中的能力。

示例：

```python
history = analysis.observe(metric=session.catalog.get("sales.gmv"), time="last_365d", grain="day")
forecast = analysis.forecast(history, horizon="30d")
actual = analysis.observe(metric=session.catalog.get("sales.gmv"), time="next_30d", grain="day")
```

### `assess_quality`

职责：评估一个 artifact 是否满足后续消费的质量与前提条件。

固定输出：

```text
quality_report
```

`assess_quality` 不重新生成 source artifact，不做统计显著性检验，不解释根因。它只回答覆盖率、缺失、样本量、可比较性、可检测性、可归因性这类质量与前提问题。

当输入是 `candidate_set` 时，`assess_quality` 只能检查生成候选所需的前提是否可靠，例如源数据覆盖率是否足够、扫描窗口是否过短、eligible series 数是否不足、检测阈值是否可解释、是否存在明显 truncation。它不能判断“候选是否为真异常”，也不能通过 bootstrap、resampling 或重复扫描来做结果验证；这类验证必须回到 `hypothesis_test`、composite validation workflow 或 human/agent decision。

显著性验证统一走 `hypothesis_test`。Forecast 风险若能由 forecast 自身计算，应进入 `forecast_frame` metadata；当前公共 API 不提供 actual-vs-forecast evaluation operator；若需要更长的多步诊断，应作为 composite operator。

示例：

```python
quality = analysis.assess_quality(delta)
candidate_quality = analysis.assess_quality(anomalies)
```

## Projection / read methods

`inspect` 不作为一等分析算子。Profile、summary、describe、preview 都属于 projection/read surface。

推荐写法：

```python
summary = metric.summary()
profile = metric.profile()
preview = delta.preview(limit=20)
```

Projection 不产生 canonical artifact family，不直接 seed evidence proposition，也不能作为 `compare`、`decompose`、`discover`、`hypothesis_test` 的输入。下游核心算子必须引用 canonical artifact、selector ref 或 typed input。

## Result Contract

Analysis operators do not write stdout. Every returned result object supports:

- `result.show()` — print bounded result card, return None
- `result.render()` — return bounded text without writing stdout
- `repr(result)` — one-line cold-start hint pointing to `.show()`
- `result.summary()` — typed bounded projection (tabular results only)
- `result.preview(limit=10)` — bounded row projection (tabular results only)
- `result.to_pandas()` — isolated DataFrame copy (analysis frames)

## Candidate consumption protocol

`candidate_set` 的 item schema 由 `shape` 决定，但所有 item 共享以下通用字段：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `item_id` | string | candidate set 内稳定 item id |
| `score` | number | candidate set 内部排序分数，不跨 artifact 比较 |
| `reason_codes` | string[] | 机器可读的候选原因 |
| `source_refs` | source ref[] | 指向源 artifact item / point / row 的 provenance refs |
| `selector` | selector ref, optional | 用于下游 `transform` 或 `observe` 的 typed selector |
| `window` | time window, optional | 候选时间窗口 |
| `baseline_window` | time window, optional | 基线窗口，主要用于 period shift |
| `keys` | object, optional | segment / dimension key map |
| `axis` | semantic dimension id, optional | semantic axis，主要用于 driver axis candidates |
| `direction` | enum, optional | `increase`, `decrease`, `mixed`, `unknown` |
| `recommended_followups` | `FollowupAction[]` | 对该 candidate 的 typed 下一步建议 |

Shape-specific item schema 是封闭枚举：

| Shape | Required fields | Optional fields |
| --- | --- | --- |
| `point_anomaly_candidates` | `item_id`, `score`, `reason_codes`, `source_refs`, `window`, `direction`, `recommended_followups` | `keys`, `selector` |
| `period_shift_candidates` | `item_id`, `score`, `reason_codes`, `source_refs`, `window`, `baseline_window`, `direction`, `recommended_followups` | `keys`, `selector` |
| `driver_axis_candidates` | `item_id`, `score`, `reason_codes`, `source_refs`, `axis`, `recommended_followups` | `selector` |
| `slice_candidates` | `item_id`, `score`, `reason_codes`, `source_refs`, `selector`, `keys`, `recommended_followups` | `window`, `direction` |
| `window_candidates` | `item_id`, `score`, `reason_codes`, `source_refs`, `window`, `recommended_followups` | `selector`, `direction` |
| `cross_sectional_outlier_candidates` | `item_id`, `score`, `reason_codes`, `source_refs`, `keys`, `direction`, `recommended_followups` | `selector` |

新增 candidate shape 必须经过 registry 审批，并满足 discover objective 扩张规则。不能仅因为 UI 需要新的展示样式就新增 shape；展示差异应落在 projection。

Selector expressions 不是新的分析算子，也不是 artifact-producing step。它们是 plan IR 里的 typed expression，输入是已排序 artifact，输出是 `SelectorRef` 或 scalar field ref；consumer step 的 lineage 记录该 selector expression。Selector expression 不走 step executor，不产生 artifact_id，也不能 seed findings。

Selector API 只保留一个概念：`select`。

- plan 内：`plan.select(...)`，返回 selector expression。
- statement shorthand：`analysis.select(...)`，语义上仍然是 selector expression。
- materialized artifact：`artifact.projection().select(...)`，返回本地读取值。

不再同时暴露 `select_item`、`select_candidate`、`projection.top()` 三套名字。

```python
selected_axis = analysis.select(axis_candidates, rank=1, attribute="axis")
selected_window = analysis.select(anomalies, rank=1, attribute="window")
selected_slice = analysis.select(slice_candidates, rank=1, attribute="selector")
```

Typed shape narrowing 通过 accessor 完成：

```python
axis_candidates = candidates.as_driver_axis()
quality = report.as_candidate()
```

Accessor 不产生新 artifact，只在本地或 compile 阶段断言 shape。shape 不匹配时必须抛出明确错误，避免 agent 在自由文本里猜 schema。

## Result artifact follow-up contract

不是所有 result artifact 都应该变成 frame。`hypothesis_test_result`、`association_result`、`quality_report` 默认仍然是 judgment / evaluation result，不直接作为 `compare`、`decompose`、`discover` 的输入。

为了保持 agent workflow 闭包，它们必须携带：

- `source_refs`：指向产生该结果的 metric、delta、forecast、candidate 或 intermediate frame。
- `recommended_followups: FollowupAction[]`：机器可读的下一步候选动作。
- `blocking_issues: BlockingIssue[]`：阻止继续分析的质量、样本量、可比性或定义漂移问题。
- `confidence_scope`：结果适用的 metric、segment、time window 和 assumptions。

`FollowupAction` schema：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `action_id` | string | result 内稳定 action id |
| `kind` | enum | `submit_step`, `open_projection`, `adjust_policy`, `request_semantic_input` |
| `operator` | operator id, optional | 建议调用的 core / composite operator |
| `input_refs` | typed ref[] | 需要传入的 artifact、candidate、source 或 semantic refs |
| `params` | typed params object | 已解析参数，不能是自由文本 |
| `preconditions` | condition[] | 执行该 action 前必须满足的条件 |
| `expected_output_family` | family id, optional | 预期输出 family |

`BlockingIssue` schema：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `issue_id` | string | result 内稳定 issue id |
| `kind` | enum | `quality`, `sample_size`, `comparability`, `definition_drift`, `missing_semantic_ref`, `cost`, `permission` |
| `severity` | enum | `warning`, `blocking` |
| `source_refs` | typed ref[] | 问题来源 |
| `message` | string | 面向 agent 的短说明 |
| `remediation_followups` | `FollowupAction[]` | 可自动尝试或建议的修复动作 |

典型 follow-up：

| Result | Follow-up 示例 |
| --- | --- |
| `association_result` | 对 source frames 做 segment-level correlation scan；用 `hypothesis_test` 验证明确假设 |
| `hypothesis_test_result` | 若验证的是 delta，可回到 source `delta_frame` 做 `decompose` |
| `quality_report` | 缩短窗口、切换 source、调整 sampling、先做 `transform.impute_nulls(...)` |

这不是把 result artifact 变成任意 downstream input，而是给 agent 一个 typed branching surface，避免只能读 projection 再用自由文本记忆下一步。

## Cross-cutting metadata

每个 plan step、artifact 和 composite result 都应暴露以下横切 metadata，供 agent 做 cost-aware 和 provenance-aware 决策：

| Metadata | 主要字段 | 用途 |
| --- | --- | --- |
| `cost_estimate` | `scanned_rows`, `estimated_bytes`, `latency_class`, `approx_cost`, `cacheability` | 让 agent 在提交重查询前判断代价 |
| `data_size_profile` | `row_count`, `bucket_count`, `axis_cardinality`, `segment_fanout`, `null_rate` | 判断是否需要 sampling、rollup、topk 或分步执行 |
| `metric_definition_ref` | `metric_id`, `semantic_version`, `valid_from`, `valid_to`, `definition_change_warnings` | 避免跨定义版本比较导致隐性错误 |
| `lineage_summary` | `source_artifacts`, `source_queries`, `promotion_refs`, `cleaning_steps` | 给 projection / evidence / audit 使用 |

跨长时间窗 `compare`、`forecast`、`hypothesis_test` 必须检查 `metric_definition_ref`。如果 metric 定义在窗口内发生版本变化，runtime 应返回 warning 或 blocking issue，不能把定义漂移静默当作业务变化。

### Pre-submit estimate

Cost-aware planning 不能只依赖执行后的 artifact metadata。Session 必须支持提交前估算：

```python
estimate = session.estimate(step_request)
estimates = session.estimate_many([step_request_1, step_request_2])
```

`CostEstimate` 至少包含 `scanned_rows`、`estimated_bytes`、`latency_class`、`approx_cost`、`cacheability`、`fanout_risk` 和 `suggested_limits`。Agent 在提交 365d panel、multi-axis attribution、large cohort retention 这类高成本任务前，应先调用 estimate 或使用 session 自动返回的 estimate warning。

### Metric definition compatibility

跨 frame 算子必须计算 `MetricDefinitionCompatibility`，并写入 `delta_frame.metadata`、`forecast_frame.metadata` 或 result metadata。

| Compatibility | 规则 | 行为 |
| --- | --- | --- |
| `exact` | metric id 和 semantic version 完全一致 | 正常执行 |
| `compatible` | 定义变化被 semantic catalog 标注为 backward-compatible | warning 继续，写入 cross-version provenance |
| `incompatible` | 聚合语义、过滤条件、subject、unit、分子/分母或业务口径变化 | 默认 fail closed，返回 `BlockingIssue[kind="definition_drift"]` |
| `unknown` | 缺少版本信息或无法证明兼容 | 默认 blocking；只有 exploratory policy 显式允许时才能继续 |

Rename、描述文案、owner 变更不构成 incompatible；aggregation、unit、subject、filter、denominator、event definition 的变化默认 incompatible，除非 catalog 显式声明兼容映射。

## Python 使用模型

Python API 只有一个语义模型：所有 core operator 和 composite operator 都编译为 session step DAG。区别只在于调用方是逐步提交、固定子图提交，还是读取 projection。

对通用 agent，默认 authoring model 是 `analysis.session.get_or_create(...)`。复杂互联网分析几乎每一步都可能需要 agent 看中间结果后决定下一步；因此 session 必须支持 step-wise execution，并保持 continuous lineage。

### 决策树

| 场景 | 写法 |
| --- | --- |
| 复杂业务分析，需要 agent 看中间结果再决定下一步 | 使用 `analysis.session.get_or_create(...)`，逐 step 提交并读取 projection |
| 要提交一个多步分析，且中间选择规则已固定 | 仍用 session 连续提交；runtime 可内部 batch optimize |
| 只是查看 summary / profile / preview | 使用 projection/read method，不创建 analysis step |
| 需要 pandas 自由分析 | 使用 `toPandas()`，结果若要回链路必须 promotion |

### Step-wise session 写法

Session 是 agent-driven analysis 的主路径。每一步都产出 artifact 或 result，agent 可以读取 projection 后继续提交下一步；session 负责记录跨 step lineage、source refs、promotion refs 和 recommended follow-ups。

```python
session = analysis.session.get_or_create(name="dau_driver_analysis")

current = session.observe(
    metric=session.catalog.get("analytics.dau"),
    time_scope={"start": "2026-05-01", "end": "2026-05-07"}, grain="day",
)
baseline = session.observe(
    metric=session.catalog.get("analytics.dau"),
    time_scope={"start": "2026-04-24", "end": "2026-04-30"}, grain="day",
)
delta = session.compare(current, baseline, alignment=analysis.window_bucket())

axis_candidates = session.discover.driver_axes(
    delta,
    search_space=[session.catalog.get("sales.orders.country"), session.catalog.get("sales.orders.platform"), session.catalog.get("sales.orders.channel")],
)

selected_axis = axis_candidates.select(rank=1, attribute="axis")
drivers = session.decompose(delta, axis=selected_axis)
```

### Batch optimization

Agent 不需要学习公开 lazy plan API。若连续 step 没有 materialized projection decision，runtime 可以在 session 内部合并查询、批量执行或共享中间结果。这是 execution optimization，不是第二套 authoring model。

### Materialized artifact 写法

Materialized artifact 是已执行 step / job 的读取结果，可以通过 projection 读取 item，再提交下一步。只要下一步依赖 agent 看到中间结果后再判断，就应该使用 session step，而不是把逻辑塞进隐藏批处理子图。

```python
axis_candidates = session.artifact("axis_candidates")
selected_axis = axis_candidates.projection().select(rank=1, attribute="axis")
drivers = session.decompose(delta, axis=selected_axis)
```

经验规则：

- 固定选择规则用 selector expression。
- 需要看中间结果后再判断时，使用 materialized projection 表达 agent 决策。
- 下游 canonical 算子只能消费 artifact ref、selector ref 或 typed input，不能消费自由文本解释。

Materialized projection 中的 `select(...)` 是读取方法，不是 plan expression，也不进入 lineage。若读取结果被用于新一轮分析，它会作为新 session step 的 literal parameter 或 selector-derived value 进入该 step lineage。

## 组合方式

### 1. 第一次看一个指标

```python
session = analysis.session.get_or_create(name="revenue_windows")

metric = session.observe(
    metric=session.catalog.get("sales.revenue"),
    time_scope={"start": "2026-01-01", "end": "2026-03-31"}, grain="day",
)
summary = metric.summary()
candidates = session.discover.interesting_windows(metric)
```

用途：

- `observe` 固定事实载体。
- projection 帮 agent 理解 shape、覆盖率、缺失和分布。
- `discover` 给下一步 follow-up 候选。

### 2. 解释一次变化

```python
current = session.observe(metric=session.catalog.get("analytics.dau"), time="this_week", grain="day")
baseline = session.observe(metric=session.catalog.get("analytics.dau"), time="previous_week", grain="day")

delta = session.compare(current, baseline)
drivers = session.decompose(delta, axis=session.catalog.get("sales.orders.channel"))
quality = session.assess_quality(drivers)
```

用途：

- `compare` 只定义 delta。
- `decompose` 只做贡献分配。
- `assess_quality` 判断归因结果的前提和覆盖质量。

### 3. 不知道该沿哪个轴看

```python
delta = session.compare(current, baseline)

axis_candidates = session.discover.driver_axes(
    delta,
    search_space=[
        session.catalog.get("sales.orders.country"),
        session.catalog.get("sales.orders.platform"),
        session.catalog.get("sales.orders.channel"),
        session.catalog.get("sales.orders.app_version"),
    ],
)

selected_axis = axis_candidates.projection().select(rank=1, attribute="axis")
drivers = session.decompose(delta, axis=selected_axis)
```

用途：

- `discover` 产出 candidate axis。
- `decompose` 仍然保持固定输出 `attribution_frame`。
- 核心层不允许用 `decompose(axis="auto")` 混合输出类型。

若 agent 不需要看候选轴，只想要常见的“找轴 + 归因”一步式路径，可以用 composite：

```python
drivers = session.composites.auto_decompose(
    delta,
    objective="largest_explainable_delta",
    search_space=[
        session.catalog.get("sales.orders.country"),
        session.catalog.get("sales.orders.platform"),
        session.catalog.get("sales.orders.channel"),
        session.catalog.get("sales.orders.app_version"),
    ],
)
```

### 4. 找异常并复查

```python
series = session.observe(metric=session.catalog.get("growth.conversion_rate"), time="last_180d", grain="day")
anomalies = session.discover.point_anomalies(
    series,
    threshold=1.0,
)
quality = session.assess_quality(anomalies)

window = anomalies.projection().select(rank=1, attribute="window")
local_series = session.transform.window(series, window=window)
```

用途：

- `discover` 输出异常候选。
- `assess_quality` 判断候选集合的质量和前提。
- `transform` 用 candidate window 做局部复看。

### 5. 分层 drilldown

```python
delta = session.compare(current, baseline)

country_attr = session.decompose(delta, axis=session.catalog.get("sales.orders.country"))
top_country = country_attr.projection().select(rank=1, attribute="keys.country")

country_delta = session.transform.slice(
    delta,
    slice_by={session.catalog.get("sales.orders.country"): top_country},
)

city_attr = session.decompose(country_delta, axis=session.catalog.get("sales.orders.city"))
```

用途：

- 上一层 attribution 的 item key 通过 selector / keys 指向下一层 delta slice。
- 每一步仍然产出固定 artifact family。

### 6. 检验一个明确假设

```python
current = session.observe(metric=session.catalog.get("video.avg_watch_time"), time="this_week", grain="day")
baseline = session.observe(metric=session.catalog.get("video.avg_watch_time"), time="previous_week", grain="day")

result = session.hypothesis_test(
    current,
    baseline,
    hypothesis="mean_changed",
    sampling=SamplingPolicy(unit="day", method="paired_numeric_summary"),
)
```

用途：

- `hypothesis_test` 直接消费 metric frames 和 sampling policy。
- runtime 在 lineage 中记录内部 sampling node。
- `hypothesis_test` 输出 `hypothesis_test_result`，不输出业务诊断。

### 7. 预测并识别风险

```python
history = session.observe(metric=session.catalog.get("sales.gmv"), time="last_365d", grain="day")
forecast = session.forecast(history, horizon="30d")
actual = session.observe(metric=session.catalog.get("sales.gmv"), time="next_30d", grain="day")
```

用途：

- `forecast` 产出 future projection。
- forecast 自身 metadata / projection 暴露区间宽度、漂移风险和模型可用性。
- 当前公共 Session API 不提供 actual-vs-forecast evaluation operator。

## Composite operator registry

Composite operator 是 agent 侧唯一需要理解的高层组合概念。它们不进入 core operator 表，但可以是一等 Python API。

### 准入原则

一个能力是否值得作为 composite operator 暴露，必须同时通过两道闸：

1. **不可平替**：不能由单个 core operator 加一个 typed policy（`AlignmentPolicy`、`SamplingPolicy`、`PromotionPolicy` 等）平替。
2. **隐含跨步约束**：DAG 中至少有一处约束（alignment 配对、provenance 保留、definition compatibility、跨步 evidence 绑定、scan bundle 一致性）是 agent 写顺手代码时会漏掉的。

只满足"高频"或"省键盘"不够。那种情况下应该使用 `analysis.session.get_or_create(...)` step-wise 写法，让 agent 在中间结果之上自己决定下一步。

### Internet analytics composite set

| 名称 | Contract level | 固定输出 | 展开方式 / 作用 |
| --- | --- | --- | --- |
| `attribute` | `canonical` | `attribution_frame` | `observe -> observe -> compare -> decompose`；面向 agent 不需要看中间 delta、轴已确定的快路径 |
| `driver_attribution_scan` | `exploratory` | `driver_scan_result` | 对多个候选轴批量执行 attribution，把 scan bundle 与 axis ranking 写入 result |
| `auto_decompose` | `exploratory` | `attribution_frame` | `discover.driver_axes -> select(rank=1) -> decompose`，axis candidates 写入 metadata；encodes 一个强选择决策，不默认 seed evidence |
| `diagnose` | `exploratory` | `diagnosis_result` | candidate slice、local compare、decompose、quality checks 的诊断模板；执行中需要 agent 看中间结果做语义决策，不承诺确定 DAG |

Composite operator 必须固定输出 family；不能写成多个可选输出，也不能返回裸数组作为 output family。多形态结果应拆成多个 composite，或定义新的稳定 result family。

### 不作为 composite 的能力

以下能力曾被考虑作为 composite operator，但因可由 core operator + typed policy 等价表达、属于 projection、或本身就是 core operator 而被排除。Agent 应直接使用对应的核心算子组合：

| 排除项 | 等价路径 | 排除原因 |
| --- | --- | --- |
| `validate` | `observe + observe + hypothesis_test(..., sampling=SamplingPolicy(...))` | 3 步纯胶水，没有 sampling policy 之外的隐藏约束。 |
| `lag_scan` | 不属于当前 public runtime | 当前 `correlate` 固定 zero-lag；lag sweep 需要后续独立设计。 |
| `driver_axis_scan` | `discover.driver_axes(search_space=[...])` | 与现有 `discover` driver-axis helper 字面等价。 |
| `metric_profile` | `metric.profile()` projection | projection 不产生 canonical artifact family，应使用 §Projection / read methods。 |

### Contract level 判定

- `canonical`：稳定 schema、确定 DAG、固定输出、可 version、可 evidence，失败语义明确。
- `exploratory`：排序、启发式、扫描导向，或执行中需要 agent 看中间结果做语义决策；主要产出候选、建议或探索性 result，不默认 seed evidence。

Composite operator 晋升到 `canonical` contract level 必须同时满足以下条件：

1. 输入 schema 稳定。
2. 展开 DAG 确定。
3. 执行中不需要 agent 看中间结果后再做语义决策。
4. 输出 artifact / result 稳定且有界。
5. evidence、lineage、failure semantics 可定义。
6. 足够高频，值得 runtime 承诺。

不满足这些条件的组合应保持 `domain`、`exploratory` 或 agent-side workflow。对 agent 侧仍通过 `analysis.composites.*` 访问，不暴露 derived intent / recipe 两套命名。

## Shape-aware DAG 邻接表

合法路径必须同时匹配 family 和 shape。下表是 compile-time gate 的目标态；projection/read method 不列为 analysis step。

| Source type | 合法下游 |
| --- | --- |
| `MetricFrame[scalar]` | `transform.<op>`, `compare` with same shape, `correlate` with same shape, `hypothesis_test`, `assess_quality`, composite operators |
| `MetricFrame[time_series]` | `transform.<op>`, `compare` with same shape, `discover.point_anomalies`, `discover.interesting_windows`, `correlate` with same shape, `hypothesis_test`, `forecast`, `assess_quality`, composite operators |
| `MetricFrame[segmented]` | `transform.<op>`, `compare` with same shape, `discover.interesting_slices`, `discover.cross_sectional_outliers`, `correlate` with same shape, `hypothesis_test`, `assess_quality`, composite operators |
| `MetricFrame[panel]` | `transform.<op>`, `compare` with same shape, `discover.point_anomalies`, `discover.interesting_windows`, `discover.cross_sectional_outliers`, `correlate` with same shape, `hypothesis_test`, `forecast`, `assess_quality`, composite operators |
| `DeltaFrame[scalar_delta]` | `transform.<op>`, `decompose`, `discover.driver_axes`, `discover.interesting_slices`, `assess_quality`, composite operators |
| `DeltaFrame[time_series_delta]` | `transform.<op>`, `decompose`, `discover.period_shifts`, `discover.driver_axes`, `discover.interesting_windows`, `discover.interesting_slices`, `assess_quality`, composite operators |
| `DeltaFrame[segmented_delta]` | `transform.<op>`, `decompose`, `discover.driver_axes`, `discover.interesting_slices`, `assess_quality`, composite operators |
| `DeltaFrame[panel_delta]` | `transform.<op>`, `decompose`, `discover.period_shifts`, `discover.driver_axes`, `discover.interesting_windows`, `discover.interesting_slices`, `assess_quality`, composite operators |
| `AttributionFrame` | `transform`, `assess_quality`, `select` |
| `CandidateSet[*]` | `assess_quality`, `select`, typed follow-up, composite operators |
| `AssociationResult[*]` | `assess_quality`, typed follow-up |
| `HypothesisTestResult` | typed follow-up / evidence assessment |
| `ForecastFrame` | `assess_quality` |
| `QualityReport[*]` | typed accessor, typed follow-up / evidence assessment |
| `DiagnosisResult` | typed follow-up / evidence assessment |
| `ExplorationResult` | promotion step only |

非法路径示例：

- `candidate_set -> decompose`：必须先通过 selector 得到 axis、window 或 slice，再喂给 `decompose` 或 `transform`。
- `artifact_summary -> compare`：summary 是 projection，不是 canonical input。
- `exploration_result -> compare`：必须先 promotion 成 canonical `metric_frame` 或 `delta_frame`。
- `forecast_frame -> compare`：forecast 与 observed metric 的认识论地位不同；当前公共 Session API 不提供 forecast-vs-actual evaluation step。

## 与 Session DAG 的关系

Python Analysis API 是 session step DAG 的友好入口，不是第二套执行模型。

每个 Python 调用应能编译为以下之一：

- 一个 atomic intent step。
- 一个 transform step。
- 一个 composite operator 展开的 step DAG。
- 一个 read projection。

因此，Python API 必须保留：

- step lineage
- artifact refs
- selector refs
- typed input resolution
- materialization policy
- outcome envelope 绑定

调用方可以用同步函数、builder 或 context manager 书写 Python 代码，但进入 runtime 后必须是同一套 canonical step DAG。

## Escape hatch

Ibis / pandas 不是核心算子层的一部分，但目标态产品必须提供受控 escape hatch。否则 agent 在算子覆盖不到的长尾分析里会倾向于发明不存在的 `op`、滥用 `discover`，或把自由文本解释伪装成 canonical artifact。

Escape hatch 必须同时支持“一进一出”两条边界。

### Ibis -> Marivo frame

当 Python Analysis 算子无法表达某个分析需求时，agent 可以使用 Ibis 直接构造查询和分析逻辑。Ibis 结果默认只产出 `exploration_result`，不自动进入 canonical 链路。

目标规则：

- `analysis.explore_ibis(...)` 产出 `exploration_result`，默认是 non-canonical artifact。
- `exploration_result` 不能直接喂给 `compare`、`decompose`、`discover`、`hypothesis_test` 等核心算子。
- 若探索结果需要衔接 Marivo analysis 链路，必须通过显式 promotion step，例如 `promote_metric_frame`、`promote_delta_frame`、`promote_attribution_frame`。
- Promotion 必须完成 schema、lineage、semantic subject、axes、measures、units、quality metadata 和 source query provenance 校验。
- Promotion 不做自动推断。元数据来自显式参数，或由 `PromotionPolicy.semantic_anchors` 提供回退值；缺失即 fail closed。
- 当 session 的 semantic 项目处于 ready 状态且定义了 metric 时，promotion 校验 metric id 必须存在于 semantic catalog；无 catalog 的 session 跳过该校验。
- Promotion 成功后，下游只看 promoted canonical `*_frame` artifact，不看原始 Ibis expression 或临时结果表。
- Promotion 失败时必须返回结构化缺口，例如 missing subject、ambiguous time axis、unknown unit、unlinked lineage；结果只能作为 scratch evidence 或 analyst note，不能 seed canonical findings。

Promotion step 是 escape-hatch bridge，不是核心分析算子。它的最小校验集合如下：

| Promotion | 最小必填元数据 | Runtime 校验内容 |
| --- | --- | --- |
| `promote_metric_frame` | `semantic_kind`、`measure_column`、`semantic_model` 显式必填；`metric`、`time_axis` 可由 `semantic_anchors` 回退 | dataframe schema、semantic catalog（条件式）、time axis、measure 类型、axis 唯一性、row/window provenance |
| `promote_delta_frame` | current/baseline anchor、`delta_column`、`current_column`、`baseline_column` | delta 公式一致性、左右 side provenance、comparability metadata、semantic catalog（条件式） |
| `promote_attribution_frame` | source delta ref（可由 `semantic_anchors` 回退）、`driver_field`、`contribution_column` | contribution 对账、source delta provenance |

Agent 可以显式提供完整元数据，也可以把部分字段（如 `metric`、`time_axis`、`current`、`baseline`、`source_delta`）放进 `PromotionPolicy.semantic_anchors` 作为回退值。promotion 必须 fail closed：缺少 subject、axis、measure 或 lineage 时，不得生成 canonical `*_frame`。

示例：

```python
scratch = analysis.explore_ibis(
    lambda t: t.filter(t.country == "US").group_by(t.device).aggregate(value=t.revenue.sum())
)

metric = analysis.promote_metric_frame(
    scratch,
    policy=PromotionPolicy(
        semantic_anchors={
            "metric": session.catalog.get("sales.revenue"),
            "time_axis": session.catalog.get("sales.orders.event_date"),
        },
        on_missing="fail_closed",
    ),
)
```

### Marivo frame -> pandas

当 canonical artifact 已经形成，但 agent 需要使用 pandas 做临时探索、可视化、建模或不适合沉淀为核心算子的长尾计算时，`*_frame` artifact 可以通过 `toPandas()` 转成 pandas dataframe。

目标规则：

- `metric_frame.toPandas()`、`delta_frame.toPandas()`、`attribution_frame.toPandas()`、`candidate_set.toPandas()` 等读取操作只产出本地 dataframe，不产生新的 canonical artifact。
- pandas dataframe 默认是 scratch result，不能直接喂给 `compare`、`decompose`、`discover`、`hypothesis_test` 等核心算子。
- pandas 分析得到的新结果若需要回到 Marivo 链路，可以直接传给 promotion step 生成 canonical `*_frame`。若需要保留 scratch artifact，可选地先调用 `analysis.from_pandas(...) -> exploration_result`，但这不是 promotion 必经步骤。
- `toPandas()` 必须保留足够的 provenance columns，例如 `artifact_id`、`item_id`、`source_refs`、`window`、`keys`，让 agent 能把 pandas 中发现的行回指到原 artifact。
- pandas 侧的自由分析可以辅助 agent 决策，但不能直接 seed canonical findings。

示例：

```python
df = delta.toPandas()
interesting = df[df["delta_pct"] < -0.2].sort_values("delta_pct").head(10)

focused_delta = analysis.promote_delta_frame(
    interesting,
    policy=PromotionPolicy(
        semantic_anchors={
            "source_delta": ArtifactRef(delta.meta.artifact_id),
        },
        on_missing="fail_closed",
    ),
)
```

这样 Ibis 保留“进入 Marivo 链路”的长尾表达力，pandas 保留“退出 Marivo artifact 做自由分析”的灵活性；两者都不能绕过 promotion 和 provenance 规则。

## 非目标

以下内容不属于本设计的核心算子层：

- 把任意 Ibis / SQL 查询伪装成核心算子。
- 把通用 pandas / sklearn wrapper 伪装成 canonical artifact producer。
- 因果推断或 what-if simulation。
- 自动业务结论生成。
- 以自由文本为主要输出的 explain / diagnose。
- 每个 BI 图表或产品分析模板对应一个 core operator。

## 最终算子表

| 算子 | 输入 | 固定输出 | 作用 |
| --- | --- | --- | --- |
| `observe` | `SemanticObject | SemanticRef` + scope | `MetricFrame[scalar | time_series | segmented | panel]` | 读取指标观测 |
| `transform` | `Frame[T]` | same family / shape as input | family-preserving 改写 |
| `compare` | `MetricFrame[T]`, `MetricFrame[T]` | `DeltaFrame[shape_for(T)]` | 计算差异 |
| `decompose` | `DeltaFrame[T]`, `SemanticObject | SemanticRef` | `attribution_frame` | 解释差异贡献 |
| `discover` | shape-compatible artifact + objective | `CandidateSet[objective_shape]` | 发现候选点、窗口、slice、axis |
| `correlate` | `MetricFrame[T]`, `MetricFrame[T]` | `AssociationResult[single_lag | lag_sweep]` | 估计关联 |
| `hypothesis_test` | `MetricFrame[T]` inputs + hypothesis + `SamplingPolicy` | `hypothesis_test_result` | 检验统计假设 |
| `forecast` | `MetricFrame[time_series | panel]` | `forecast_frame` | 预测未来走势 |
| `assess_quality` | `Artifact[T]` | `QualityReport[shape_for(T)]` | 评估质量、覆盖率和前提 |

这张表是 Python Analysis core operator 集合的目标态边界。新增 core operator 前应先回答：它是否有固定输出 family，是否不能由现有 core / composite operator 组合表达，是否需要进入 canonical artifact 链路。若任一答案是否定的，应优先放到 composite operator、projection、escape hatch 或 agent-side workflow。

## TODO: Domain frame 与相关算子

以下 domain frame、composite operator、semantic catalog ref 及示例依赖 semantic layer 提供事件、实体、cohort 等特定数据模型抽象（`EntityRef`、`EventRef`、`StageRef`、`CohortKeyRef`），当前 semantic layer 尚未定义这些概念。待后续单独设计并落地后，再纳入正文。

### 待纳入的 domain artifact families

| Family | 主要 producer | 主要语义 |
| --- | --- | --- |
| `entity_frame` | `enrich` composite | entity-level feature / enrich 结果 |
| `funnel_frame` | `funnel` composite | stage conversion / dropoff / segment breakdown |
| `retention_frame` | `retention` composite | cohort x period 留存矩阵 |
| `cohort_frame` | `cohort` composite | cohort membership 与 cohort-relative time |
| `segment_assignment_frame` | `cluster` composite | 个体或实体的 segment / cluster 标签 |

### 待纳入的 domain composite operators

| 名称 | Contract level | 固定输出 | 展开方式 / 作用 |
| --- | --- | --- | --- |
| `enrich` | `domain` | `entity_frame` | semantic-layer-aware row-level join / feature construction |
| `funnel` | `domain` | `funnel_frame` | event stage conversion、dropoff、segment breakdown |
| `cohort` | `domain` | `cohort_frame` | cohort membership 与 cohort-relative time |
| `retention` | `domain` | `retention_frame` | cohort x period 留存矩阵 |
| `cluster` | `domain` | `segment_assignment_frame` | 无监督分群，输出可作为后续 axis 的 segment label |
| `materialize_metric_frame` | `domain` | `metric_frame` | domain frame measure 到 core metric frame 的 typed bridge |
| `register_dynamic_axis` | `domain` | `DynamicAxisRef` | cluster / segment assignment 注册为可用 semantic axis |
| `cross_axis_attribution` | `domain` | `attribution_frame` | 多轴联合归因，不等同于 drilldown |

### 待纳入的 semantic catalog refs

| Ref | 用途 |
| --- | --- |
| `EntityRef` | 用户、订单、设备等 semantic subject |
| `EventRef` | 行为事件 |
| `StageRef` | funnel stage |
| `CohortKeyRef` | cohort membership key |
| `DynamicAxisRef` | runtime 注册的动态分群轴 |

### 待纳入的 AlignmentPolicy 扩展

- `AlignmentPolicy.kind` 值 `cohort_relative`
- `AlignmentPolicy` 典型字段 `cohort_time`
- typed ref `CohortTimeRef`

### 待纳入的 decompose 扩展

- `decompose` 签名扩展为 `axes=list[SemanticObject | SemanticRef | DynamicAxisRef]`，待 `register_dynamic_axis` 落地后启用
- 多轴联合归因 composite `cross_axis_attribution`

### Domain frame contract

Domain frame 是 canonical artifact family，不是 escape hatch scratch result。它们不能直接假装成 `metric_frame`，但可以通过 typed bridge 产生 core frame。

适用 families：

- `entity_frame`
- `funnel_frame`
- `retention_frame`
- `cohort_frame`
- `segment_assignment_frame`

所有 domain frame 必须支持：

- `projection()`：领域投影视图，例如 funnel stage table、retention heatmap、cluster profile。
- `toPandas()`：本地 scratch dataframe，保留 `artifact_id`、`item_id`、`source_refs`、`keys`、`window` 等 provenance columns。
- `assess_quality(...)`：返回对应 `QualityReport[...]`，检查覆盖率、样本量、entity/stage/cohort 完整性。
- limited `transform.<op>(...)`：仅允许兼容矩阵声明的 `filter`、`slice`、`window`、`topk` / `bottomk`。

Domain frame 到 core metric 的唯一 canonical bridge 是：

```text
materialize_metric_frame(domain_frame, measure_ref, grain?, dimensions?, policy?) -> MetricFrame[scalar | time_series | segmented | panel]
```

`measure_ref` 必须是 typed ref，例如 `FunnelMeasureRef`、`RetentionMeasureRef`、`EntityMeasureRef`。这不是 projection，也不是 escape hatch promotion；它是 domain artifact 内部已知 measure 到 core metric frame 的 typed materialization step。

Cluster 的主路径需要显式动态轴注册：

```text
register_dynamic_axis(segment_assignment_frame, name, scope, ttl, refresh_policy) -> DynamicAxisRef
```

`DynamicAxisRef` 可作为 `observe(..., dimensions=[...])`、`compare` scope 或 `decompose(axis=...)` 的 semantic axis。注册必须记录 lineage、适用 subject、有效窗口、ttl 和 refresh policy；过期或 subject 不匹配时必须 fail closed。

### 待纳入的 DAG 邻接条目

| Source type | 合法下游 |
| --- | --- |
| `EntityFrame` | `funnel`, `cohort`, `retention`, `cluster`, `materialize_metric_frame`, `assess_quality`, limited `transform` |
| `FunnelFrame` | `materialize_metric_frame`, `assess_quality`, limited `transform` |
| `CohortFrame` | `retention`, `materialize_metric_frame`, `assess_quality`, limited `transform` |
| `RetentionFrame` | `materialize_metric_frame`, `discover` through materialized metric frame, `assess_quality`, limited `transform` |
| `SegmentAssignmentFrame` | `register_dynamic_axis`, `enrich`, `assess_quality`, limited `transform` |
| `DynamicAxisRef` | `observe(..., dimensions=[...])`, `compare` scope, `decompose(axis=...)` |

### 待纳入的组合示例

#### Funnel 后做分段归因

```python
events = session.composites.enrich(
    base=EntityRef("user_events"),
    joins=[EntityRef("orders"), EntityRef("campaign_membership")],
    subject=EntityRef("user"),
    time="last_30d",
)

funnel = session.composites.funnel(
    events,
    stages=[
        StageRef("visit"),
        StageRef("signup"),
        StageRef("activate"),
        StageRef("purchase"),
    ],
    dimensions=[session.catalog.get("sales.orders.channel"), session.catalog.get("sales.orders.country")],
)

dropoff_metric = session.materialize_metric_frame(
    funnel,
    measure=FunnelMeasureRef.stage_dropoff_rate(stage=StageRef("purchase")),
)
dropoff = session.transform.topk(dropoff_metric, by="dropoff", limit=10)
```

用途：

- `enrich` 覆盖 row-level join / feature construction，不要求 agent 每次写 Ibis promotion。
- `funnel` 产出 typed `funnel_frame`，stage、dropoff、segment breakdown 可进入 projection 或转换为 metric frame 后继续分析。

#### 留存 / cohort 分析

```python
cohorts = session.composites.cohort(
    subject=EntityRef("user"),
    cohort_key=CohortKeyRef("signup_week"),
    time="last_180d",
)

retention = session.composites.retention(
    cohorts,
    active_event=EventRef("app_open"),
    period="week",
    horizon=12,
)
```

用途：

- `cohort_frame` 固化 cohort membership 与 cohort-relative time。
- `retention_frame` 固化 cohort x period matrix，后续可 compare cohort、discover 异常 cohort 或 projection 成 retention heatmap。

---

# Evidence Access Surface


状态：target design。本文定义 `marivo.analysis` 暴露给通用 agent（Claude Code、Codex 等）的 evidence 访问面、Python 轨道实现边界，以及它与 见上文「算子目录」等章节 的衔接方式。

Python 轨道是 **Python-native independent implementation**：实现代码落在 `marivo.analysis` 内，只依赖 Python 轨道 frame、meta、lineage、异常和存储边界。

本文不维护已删除服务链路的一致性，也不要求双写、兼容读取或数据迁移。

## 目的

固定以下问题的统一答案：

- agent 看到一个 result 时，evidence 信息以什么字段挂在 result 上
- agent 想知道"我在这个 session 中已经判断到什么程度"时，调什么、拿到什么 typed 对象
- agent 需要 audit、回放或跨 session 引用时，evidence 链路对象本体如何暴露
- evidence emission 是 step 提交的副作用，还是 agent 显式 trigger
- runtime 在什么规则下生成 followup，agent 收到 followup 时应给予多大信任
- judgment 存储落在 project 本地的什么位置

## 非目标

本文不定义：

- 已删除服务链路的数据迁移、双写兼容或 wire parity
- 业务领域 / 战略性 followup（agent 自身职责，runtime 不冒充）
- semantic axis enumeration 类启发式建议（不暴露）
- `agent_authored` proposition 写入面
- 对外 wire contract
- UI projection 文案

## 主题位置

| 章节 | 职责 |
| --- | --- |
| 上文算子设计章节（算子目录、Shape-aware DAG 邻接表等） | Python 轨道算子集、Frame 契约、`FollowupAction` / `BlockingIssue` / `ConfidenceScope` 基 schema |
| 下文 Surface 1 / 2 / 3 章节 | evidence 读写、抽取、seeding、assessment 与 followup 规则；扩展 `FollowupAction` 加 `category` |

## 核心设计原则

### 原则 P1：evidence 是 step 提交的副作用

agent 提交 `session.compare(...)` 时，runtime 自动完成：finding 抽取 → proposition seeding → assessment recompute → result 上 typed 字段填充。agent 不需要显式 trigger evidence。

agent 必须能仅通过算子和 read method 完成完整工作流，不强制学习 evidence 内部分层。

### 原则 P2：三层 surface 分别服务三类场景

| Surface | 默认使用频率 | agent 需要识别的新类型 | 场景 |
| --- | --- | --- | --- |
| Surface 1 result-bound | 高（每步） | 4：`ConfidenceScope`、`FollowupAction`、`BlockingIssue`、`QualitySummary` | 刚提交完一步，看下一步去哪 |
| Surface 2 session-bound | 中（关键节点） | 4 默认入口（`SessionKnowledge`、`Fact`、`OpenItem`、`BlockedFollowup`）+ 5 细分 typed fact（按需） | 综合判断、跨 step 推理、session 恢复 |
| Surface 3 object-bound | 低（审计） | 3：`Finding`、`Proposition`、`Assessment`（`ActionProposal` 不暴露，预留未来） | 回放、UI 绑定 |

Surface 2 的默认 agent surface 压缩为 `knowledge.facts() / open_items() / next_steps()`——agent 在常规分析中只需识别 4 类。

### 原则 P3：面向语义命名，不暴露引擎对象

Surface 2 对象命名按"agent 听得懂的事实形态"组织（`ChangeFact / AttributedDriver / TestedHypothesis / ForecastSummary / AssociationSummary`、`OpenAnomaly`），**不直接暴露** `Proposition[change]`、`Assessment[change_assessment]` 等引擎命名。引擎命名只在 Surface 3 出现。

### 原则 P4：evidence 失败不阻塞分析推进

artifact + findings + confidence_scope + quality_summary 在 SAVEPOINT 之前落 SQLite；savepoint 之内的 seeding / assessment / followup / blocking_issues 若失败，savepoint 回滚但前段保留，外层 tx 仍 COMMIT，`evidence_status='partial'`。agent 总能拿到 result 并继续下一步。Surface 1 通过 `evidence_status` 表达降级；Surface 2 通过 `evidence_completeness` 表达降级。

### 原则 P5：`recommended_followups` 严格限定 C1 + C2

runtime 只暴露两类 followup：

- **C1 (`dag_continuation`)**：由上文「Shape-aware DAG 邻接表」机械推导的合法下游 operator
- **C2 (`quality_remediation`)**：针对某个 `BlockingIssue` 的确定性补救动作

业务领域 / 战略性建议（C4）与 semantic axis enumeration 类启发式建议（C3）**不进入** `recommended_followups`，**由 agent 自身生成**。runtime 不冒充对业务知识或启发式裁剪的判断力。

宁可空，不要噪音：当无法按 C1 / C2 规则生成确定性 followup 时，字段保持空列表，让 agent 自行决策。

### 原则 P6：Python 轨道独立实现，旧代码只拷贝不依赖

`marivo.analysis.evidence` 是 Python 库唯一 evidence runtime。实现层不得绕过 Python-track isolation。

允许从旧 evidence 链路复制以下素材：

- canonical finding / item identity 规则
- family extractor 的 canonical item key 逻辑
- T1-T6 seed template registry 结构
- assessment precondition / quality / comparability gate 与 status resolution 的纯规则
- 测试 fixture 中表达的稳定 evidence 行为

复用这些素材时必须改成 `analysis` frame/meta/lineage 输入、`AnalysisError` exception taxonomy、session-local `judgment.db` repository，并纳入 import-linter 边界。服务端策略、wire projection 和旧 replay recovery 不进入 Python 轨道。

## Surface 1：Result-Bound

agent 提交 step 后拿到的 result 对象（`MetricFrame`、`DeltaFrame`、`AttributionFrame`、`CandidateSet`、`AssociationResult`、`HypothesisTestResult`、`ForecastFrame`、`ForecastEvaluationResult`、`QualityReport`、`DiagnosisResult`、`DriverScanResult`）必须**直接以扁平字段**携带 evidence 信息，不嵌套包装类，不要求 agent 再发起 read call。

### 扁平字段集

每个 result artifact 暴露以下字段（dataclass `frozen=True` 或等价 immutable）：

| 字段 | 类型 | 来源 |
| --- | --- | --- |
| `artifact_id` | `str` | step executor 在 commit 时生成 |
| `subject` | `Subject` | 由 artifact typed semantics 派生 |
| `source_refs` | `list[ArtifactRef]` | 上游 step artifact refs |
| `lineage` | `ResultLineage` | `AlignmentPolicy`、`MetricDefinitionCompatibility`、cleaning steps、promotion refs、`triggered_by_followup` |
| `confidence_scope` | `ConfidenceScope` | 见 [ConfidenceScope](#confidencescope-跨-step-兼容性) |
| `quality` | `QualitySummary \| None` | commit 时**直接由 artifact payload + lineage 元数据同步计算**的轻量摘要，**不是 `assess_quality` operator 的输出**，**不进 step DAG** |
| `blocking_issues` | `list[BlockingIssue]` | commit 时同步填充 |
| `recommended_followups` | `list[FollowupAction]` | commit 时同步填充；只含 C1 + C2 |
| `evidence_status` | `Literal["complete", "partial", "unavailable"]` | 见 [evidence_status per-field fallback](#evidence_status-per-field-fallback) |

字段挂在 result 的 `meta` 上：

```python
delta = session.compare(current, baseline)
delta.meta.recommended_followups   # 只含 C1 + C2
delta.meta.blocking_issues
delta.meta.quality
delta.meta.evidence_status
```

**不存在** `delta.evidence.*` 嵌套命名空间。所有 Surface 1 字段都通过 `frame.meta` 读取。

实现层字段声明位置：这些字段应收敛到 `BaseFrameMeta`，让 `MetricFrameMeta`、`DeltaFrameMeta`、`AttributionFrameMeta`、`CandidateSetMeta`、`AssociationResultMeta`、`HypothesisTestResultMeta`、`ForecastFrameMeta`、`ForecastEvaluationResultMeta`、`QualityReportMeta` 共享同一 result-bound evidence contract。

`artifact_id` 是 replay-stable canonical identity。现有 `ref` 仅作为 frame loading alias 保留；目标态中 `ref == artifact_id`，新算子不再生成随机 `frame_*` 作为规范身份。

`CandidateSet` 行级 `recommended_followups_json` 与 result-level `recommended_followups` 不同：

- `result.meta.recommended_followups` 是 artifact-level C1 / C2 followup，由 evidence pipeline 在 step commit 时生成。
- `candidate_item.recommended_followups` 是 candidate row payload，通过 `select(..., attribute="recommended_followups")` 读取。

两者复用同一个 `FollowupAction` 类型，但来源、去重和执行 lineage 语义不同。

### `FollowupAction` 扩展

在 见上文「算子目录」等章节 的 base schema 上增加：

```python
class FollowupAction:
    # base schema (见上文算子设计章节)
    action_id: str
    kind: Literal["submit_step", "open_projection", "adjust_policy", "request_semantic_input"]
    operator: OperatorId | None
    input_refs: list[TypedRef]
    params: TypedParams
    preconditions: list[Condition]
    expected_output_family: FamilyId | None

    # category extension
    category: Literal["dag_continuation", "quality_remediation"]   # required
    source_issue_id: str | None                                     # required when category="quality_remediation"
```

`category` 是必填字段，封闭枚举 `{dag_continuation, quality_remediation}`。新增 category 必须经过 spec 修订，不允许 runtime 私自扩展。

### `result.meta.quality` 与 `session.assess_quality(...)` 的边界

| 项 | `result.meta.quality` | `session.assess_quality(result)` |
| --- | --- | --- |
| 触发 | commit 时自动 | agent 显式调 |
| 是否进 step DAG | 否 | **是**（见上文「算子目录」等章节 core operator） |
| 是否产 artifact | 否（embedded summary） | 是（产 canonical `QualityReport[shape]` artifact，可被 evidence chain 抽取成 finding） |
| 复杂度 | 轻量：coverage、null_rate、sample_size、`MetricDefinitionCompatibility` | 完整：参照 见上文「算子目录」等章节 `QualityReport[shape]` 全字段 |
| 适用场景 | 默认随每步暴露 | 需要正式 quality artifact 进入 lineage / evidence chain |
| 是否会重复跑 | 否；存于 SQLite | 显式调用每次创建新 step |

### Sampled fold coverage sidecar

Sampled folds produce a linked `CoverageFrame` accessible through `frame.coverage()`. The main MetricFrame stays limited to axis columns plus measure columns; time-slot coverage rows live in the sidecar frame and summarize into `result.meta.quality.sample_coverage_*`.

### `evidence_status` per-field fallback

| `evidence_status` | 失败阶段 | 仍然填充 | 可能为 `null` / 空 |
| --- | --- | --- | --- |
| `complete` | 无 | 所有字段 | 无 |
| `partial` | savepoint 内 seeding / assessment / followup / blocking_issues 写入失败 | `artifact_id`、`subject`、`source_refs`、`lineage`、`quality`、`confidence_scope`、`blocking_issues`（含一条 `kind=evidence_partial` issue） | `recommended_followups`（可能不完整或空） |
| `unavailable` | judgment store 启动期不可用 | `artifact_id`、`subject`、`source_refs`、`lineage`、`quality`、`confidence_scope`（皆由本步执行计算，不依赖 store）；`blocking_issues` 强制含一条 `kind=evidence_store_unavailable` issue | `recommended_followups` 空（无 store 可去重 / 持久化） |

`unavailable` 的语义补充：

- result 是 **in-memory only**，**不写 SQLite**；本进程内下游 operator 仍可直接消费该 result 作为输入（`artifact_id` / lineage 完整）
- 进程重启后 in-memory result 丢失，无法通过 `session.evidence.findings()` / `session.knowledge()` 找回
- `session.knowledge().evidence_completeness` 在 store 恢复前持续 `unavailable`

`partial` 时 `recommended_followups` 可能仍然填充 C1 部分（C1 生成不依赖 seeding），但 agent 应优先信任 `blocking_issues` 中 `kind=evidence_partial` 的提示决定是否重试。

### Result-bound 不承载的语义

下列内容**不进入** Surface 1：

- judgment 状态（`validated` / `refuted` / `inconclusive`）
- `proposition_id` / `assessment_id`
- 跨 step / 跨 session 累积事实
- support / oppose finding 聚合

这些属于 Surface 2 / Surface 3。

## Followup 生成规则

`recommended_followups` 由 runtime 在 commit 阶段同步生成。生成规则是一等契约——任何无法溯源到下面 C1 / C2 规则的 followup 都是 spec violation。

### C1：`dag_continuation`

**判定**：从上文「Shape-aware DAG 邻接表」出发，对当前 result artifact 的 `(family, shape)` 查表，列出合法下游 operator。

**只在以下条件全部满足时**发出 C1 followup：

1. 下游 operator 可仅以当前 artifact + **默认参数** / **该 operator 自身的封闭枚举参数**执行——不需要 runtime 推断新的 ref（如 `decompose` 的 `axis: SemanticObject | SemanticRef`，runtime 不挑），不需要 runtime 推断 policy（如 `compare` 的另一侧 `metric_frame`，runtime 不配对）
2. 下游 operator 的所有 `input_refs` 在当前 session 中已可解析（要么是当前 artifact，要么是 session 内显式存在的另一 artifact）
3. 不引入未经裁剪的 enumeration（如"对所有 axis 各发一个 decompose followup"——禁止）

**C1 实际可发出的 followup（白名单）**：

| 源 artifact | 可发 C1 followup |
| --- | --- |
| `MetricFrame[scalar]` | `assess_quality` |
| `MetricFrame[time_series]` | `discover.point_anomalies`、`discover.interesting_windows`、`forecast(horizon=default)`、`assess_quality` |
| `MetricFrame[segmented]` | `discover.interesting_slices`、`discover.cross_sectional_outliers`、`assess_quality` |
| `MetricFrame[panel]` | `discover.point_anomalies`、`discover.cross_sectional_outliers`、`discover.interesting_windows`、`forecast(horizon=default)`、`assess_quality` |
| `DeltaFrame[*]` | `discover.driver_axes`、`discover.period_shifts`（time_series_delta / panel_delta）、`discover.interesting_slices`、`assess_quality` |
| `AttributionFrame` | `assess_quality` |
| `CandidateSet[*]` | `assess_quality` |
| `AssociationResult[*]` | `assess_quality` |
| `HypothesisTestResult` | `assess_quality` |
| `ForecastFrame` | `assess_quality` |
| `QualityReport` | （空——不再续接） |

**显式不发 C1 followup 的 operator**：

- `decompose`：需要 `axis: SemanticObject | SemanticRef`，属于 C3 enumeration
- `compare`：需要另一侧 `metric_frame`，runtime 不配对
- `correlate`：需要另一侧 frame，runtime 不挑；lag 固定 zero-lag
- `test`：需要 `hypothesis` + `SamplingPolicy`，runtime 不挑
- `transform`：除了 `assess_quality` 路径，其他 op 需要 predicate / window / 策略参数，属于 C3
- 任何 composite operator：不进入 C1

如果 agent 想做这些动作，agent 自己生成调用。runtime 不在 `recommended_followups` 中替 agent 做这个判断。

### C2：`quality_remediation`

**判定**：对当前 result 的每个 `BlockingIssue`，查 `BlockingIssue.kind → remediation` 映射表；如果存在确定性补救动作，发出 C2 followup 并设置 `source_issue_id`。

**只在以下条件全部满足时**发出 C2 followup：

1. `BlockingIssue.kind` 在 remediation 映射表中
2. 补救动作可仅以当前 artifact + BlockingIssue 提供的字段执行；不需要额外推断（不重叠 C3）
3. 补救动作是可执行的 typed operator call，不是自由文本

**Remediation 映射表**：

| `BlockingIssue.kind` | C2 followup |
| --- | --- |
| `null_rate_high` | `transform(op="impute_nulls", policy=default)`；`transform(op="filter", slice_by=<issue.payload.non_null_predicate>)` |
| `sample_size_low` | （不发——"扩窗口多少"属于 C3 启发式；agent 自决） |
| `comparability_incompatible` | `compare(..., alignment=<issue.payload.suggested_alignment>)`（仅当 `issue.payload.suggested_alignment` 已由 lineage 解出） |
| `definition_drift_detected` | `transform(op="window", window=<issue.payload.definition_valid_range>)`（仅当 valid_range 在 issue payload） |
| `evidence_partial` | meta-followup：`retry_evidence_pipeline`（kind=`adjust_policy`，runtime 内部重跑 seeding / assessment） |
| `cross_session_window_mismatch` | （不发——跨 session 不在 scope） |
| `outlier_winsorize_recommended` | `transform(op="winsorize", policy=<issue.payload.suggested_policy>)`（仅当 suggested_policy 已由 issue 提供） |

凡是需要 runtime 自行推断"什么策略合适"的 issue（如 sample_size_low），**不发** C2——agent 看到 BlockingIssue 后自行决定。**不发比乱发好**。

### 生成规则 conformance

实现层必须满足：

1. **可溯源**：每条发出的 FollowupAction 必须能精确指向 C1 白名单某一行或 C2 remediation 表某一行
2. **不混类**：`category` 与 `source_issue_id` 满足以下不变量：
   - `category="dag_continuation"` ⇒ `source_issue_id IS NULL`
   - `category="quality_remediation"` ⇒ `source_issue_id IS NOT NULL`
3. **不引入推断**：runtime 不自己挑 axis、不自己挑配对 frame、不自己挑 sampling unit。需要这些的算子不进入 followup
4. **可重放**：同一 result 二次生成 followup 集合必须字节相等
5. **不依赖运行时数据统计**：followup 生成必须基于 result shape + lineage + BlockingIssue + session artifact 索引，**不读 frame raw data**

`tests/test_followup_generation_rules.py` 必须覆盖：

- 每个 (family, shape) 组合下 C1 followup 集合完全等于 C1 白名单表的对应行
- 每种 BlockingIssue.kind 下 C2 followup 完全等于 remediation 表对应行（含 "不发" 的情形）
- 任意非白名单 / 非映射表的 followup 出现 → 测试失败

## Surface 2：Session-Bound

agent 在 session 中跑了多步后，需要横切问"我已经知道什么 / 还不确定什么 / 接下来该做什么"。

### 入口

```python
knowledge: SessionKnowledge = session.knowledge()
```

`session.knowledge()` 是 read method，**不创建 step、不进入 lineage**。返回值是 immutable snapshot。

### `SessionKnowledge` 契约（默认入口）

```python
class SessionKnowledge:
    session_id: str
    snapshot_id: str
    snapshot_at: datetime                                                    # timezone-aware UTC
    evidence_completeness: Literal["complete", "partial", "unavailable"]

    def facts(self, kind: FactKind | None = None) -> list[Fact]:
        """按 kind 过滤已建立事实。kind ∈ {"change","driver","tested_hypothesis","forecast","association"}。"""

    def open_items(self, kind: OpenItemKind | None = None) -> list[OpenItem]:
        """待判断 / 待复查项。kind ∈ {"anomaly","question"}。"""

    def blocked_followups(self) -> list[BlockedFollowup]:
        """因 BlockingIssue 而无法直接执行的 followup。"""

    def next_steps(self, top: int = 5) -> list[FollowupAction]:
        """跨所有 result 的 recommended_followups 去重保序，只返回未执行的。"""

    def for_subject(self, subject: Subject) -> SessionKnowledge:
        """按 subject canonical key 过滤的子视图。"""
```

### `evidence_completeness` 语义

| 值 | 含义 |
| --- | --- |
| `complete` | judgment store 健康；所有 step 的 finding/proposition/assessment 都已成功 |
| `partial` | judgment store 健康，但存在 ≥ 1 个 step 处于 `evidence_status=partial`；返回的 facts/open_items 可能少于真实情况 |
| `unavailable` | judgment store 不可用；**所有列表为空，但"空"含义是"未知"，不是"无"**。agent 必须先检查 `evidence_completeness` 再消费列表 |

### 细分 typed fact

`knowledge.facts(kind=...)` 返回的对象按 kind 是具体 typed fact，共享以下基字段：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `id` | str | 稳定 id（底层 `proposition_id` 的 Pythonic 包装） |
| `kind` | `FactKind` | discriminator |
| `subject` | Subject | semantic subject |
| `window` | TimeWindow \| None | observed window |
| `status` | enum | `validated` \| `refuted` \| `inconclusive` \| `pending` |
| `confidence` | float | scalar 0..1 |
| `confidence_basis` | str | 机器可读基础（如 `latest_test_p_lt_alpha`） |
| `source_refs` | list[ArtifactRef] | trace 回产生该 fact 的 step / result |
| `latest_assessment_id` | str | 直读 Surface 3 用 |

附加字段：

| 类 | 附加字段 |
| --- | --- |
| `ChangeFact` | `direction`、`magnitude`、`comparison_window`、`comparison_basis`、`dimension_keys: Mapping[str,str] \| None` |
| `AttributedDriver` | `dimension`、`dimension_keys`、`contribution_value`、`contribution_share`、`contribution_role`、`scope_change_id` |
| `TestedHypothesis` | `hypothesis_family`、`alternative`、`method_family`、`alpha`、`p_value`、`reject_null` |
| `ForecastSummary` | `forecast_window`、`horizon_index`、`forecast_kind`、`prediction_interval` |
| `AssociationSummary` | `left_subject`、`right_subject`、`method_family`、`coefficient`、`lag_mode: Literal["single","sweep"]`、`lag: TimeOffset \| None`、`lag_sweep: LagSweepSummary \| None`、`join_basis` |

### Fact projection rules

typed fact 的字段由 **proposition + latest assessment + seed finding** 三者投影合并。投影规则：

| Fact 字段 | 取自 |
| --- | --- |
| `id`、`subject`、`window`、定义性参数（如 `alpha`、`comparison_basis`） | proposition base / payload |
| `status`、`confidence`、`confidence_basis`、`reject_null`、`p_value` | latest assessment payload |
| `magnitude`、`coefficient`、`prediction_interval`、`contribution_value` 等量化字段 | seed finding payload（assessment recompute 时快照到 fact） |

实现层必须保证投影 deterministic：同一 `(proposition, latest_assessment, seed_findings)` 输入产出同一 fact 对象。

### 待判断对象（`OpenItem`）

仅两种 kind：

| `kind` | 类 | 含义 | 来源 |
| --- | --- | --- | --- |
| `"anomaly"` | `OpenAnomaly` | 异常候选已注册成 proposition，assessment 尚未到 `validated`/`refuted` | seeding 自 `anomaly_candidate` finding |
| `"question"` | `OpenQuestion` | 综合视图，`reason` 字段标明来源 | 见下 |

`OpenQuestion.reason` 封闭枚举：

- `"reopened_gap"`：assessment 触发的 gap reopened
- `"persistent_blocking_issue"`：跨 ≥ 2 个 step 重复出现的 `BlockingIssue`，未被 followup 解决

**不包含**：

- `open_candidates`（非 anomaly 的 `discover` objective）—— 阻塞于 `discover` 多 shape seeding 设计
- `forecast_evaluations` —— 阻塞于 `forecast_evaluation_result` finding family 设计

### `next_steps(top=5)` 语义

跨所有 result `recommended_followups` 去重、**按 emit 顺序（commit_at 升序）保序**、过滤已执行项、返回前 N。

**不做语义排序**。result 上发出的 followup 已经是 C1 / C2，结构性可靠；排序权交给 agent。

去重 key：`(operator, canonical(input_refs), canonical(params))`。

"已执行"判定靠 `followups.executed_step_id`；该字段由带 `triggered_by_followup` lineage 的已 commit step 回填（见 [Followup execution lineage](#followup-execution-lineage)）。

### `for_subject(subject)`

按 [Subject canonical key](#subject-canonical-key) 过滤的子视图，保持同一 schema。

### 同步语义

`session.knowledge()` 返回的 snapshot 必须包含本 session 内**所有已 commit step** 产生的 evidence。新 step commit 完成的瞬间，下一次 `session.knowledge()` 立即可见，无延迟。实现层：调用时从 SQLite 同步查询；不返回过期缓存。

### Followup 执行方式

```python
knowledge = session.knowledge()
for action in knowledge.next_steps(top=5):
    if action.operator == "assess_quality":
        frame = session.get_frame(action.input_refs[0])
        result = session.assess_quality(frame)
    elif action.operator == "decompose":
        delta = session.get_frame(action.input_refs[0])
        result = session.decompose(delta, axis=session.catalog.get(str(action.params["axis"])).ref)
    else:
        # agent 显式选择对应 typed operator，并负责补齐 operator 所需参数
        continue
```

runtime 不提供统一 followup dispatcher。agent 从 `knowledge.next_steps()` 读取 `FollowupAction` 后，按 `action.operator` 显式调用对应 typed operator（`session.observe / compare / decompose / discover / correlate / hypothesis_test / forecast / assess_quality`），并从 `action.input_refs / action.params` 中补齐该 operator 的参数。

所有 followup 已是 C1 / C2 可靠类，**不需要** `accept_class` 参数。

当前公共 `session.*` wrapper 示例只负责执行 typed step，不暴露 followup execution marker，也不会更新 `followups.executed_step_id`；agent 可能在下一次 `next_steps()` 中再次看到同一个 action。`triggered_by_followup` 是内部 / persistence lineage 字段，只有实现层在 step commit 时显式携带该 lineage，runtime 才会回填 `executed_step_id`。

## Surface 3：Object-Bound

为审计、回放提供的引擎对象级访问。**默认 agent 不需要碰**。

### 命名空间

```python
session.evidence.findings(
    artifact_id: ArtifactRef | None = None,
    finding_type: FindingType | None = None,
    subject: Subject | None = None,
) -> Iterable[Finding]

session.evidence.propositions(
    proposition_type: PropositionType | None = None,
    subject: Subject | None = None,
    status: AssessmentStatus | None = None,
) -> Iterable[Proposition]

session.evidence.assessments(
    proposition_id: str | None = None,
    latest_only: bool = True,
) -> Iterable[Assessment]

session.evidence.proposition(proposition_id: str) -> Proposition
session.evidence.latest_assessment(proposition_id: str) -> Assessment | None
session.evidence.trace(proposition_id: str) -> EvidenceTrace
```

`session.findings(...)` / `session.propositions(...)` / `session.assessments(...)`
仅作为 backward-compatibility alias 保留，不作为 agent-facing Surface 3 入口。

**不暴露** `session.action_proposals(...)`——policy engine 未实现，没有写入源；保留命名给未来。

### `EvidenceTrace`

```python
class EvidenceTrace:
    proposition: Proposition
    latest_assessment: Assessment | None
    seed_findings: list[Finding]
    support_findings: list[Finding]
    oppose_findings: list[Finding]
    source_artifacts: list[ArtifactRef]
    source_steps: list[StepRef]
```

`StepRef` 复用 见上文「算子目录」等章节 的 typed ref 定义。

### 对象 schema

Python 轨道暴露的 `Finding` / `Proposition` / `Assessment` 字段集遵循本文 Surface 3 规则，Python 表达层约束：

- 所有对象 `@dataclass(frozen=True)` 或等价 immutable
- 所有引用字段为 typed `*Ref`，禁止 raw string
- subtype payload 用 TypedDict union

### Delta finding payload fields

Delta finding payloads carry the following unit-related field:

- `unit` (`str | None`): The subject metric's declared UCUM unit, sourced from
  `MetricIR.unit`, threaded through frame meta; `null` when the metric has no
  declared unit. Change proposition payloads copy this value.

## 实现范围

Python 轨道主动收窄实现范围以控制规模。下表是目标能力 vs 实际落地：

实现 package 边界：

```text
marivo/analysis/evidence/
  types.py
  identity.py
  extraction/
  seeding.py
  assessment.py
  followups.py
  knowledge.py
  store.py
  pipeline.py
```

`pipeline.py` 提供统一 `commit_result(...)` helper。现有 operator 仍负责计算 DataFrame 与基础语义 metadata；`commit_result(...)` 负责 deterministic identity、Parquet / job / SQLite 写入、finding extraction、proposition seeding、assessment recompute、blocking issue、C1 / C2 followup 和 Surface 1 字段回填。这样避免每个 operator 分散实现 evidence side effect。

| spec 能力 | 落地 | 说明 |
| --- | --- | --- |
| finding 抽取（7 个 family） | ✅ 全实现 | 严格遵循本文 finding 抽取规则 |
| seed template registry（T1-T6） | ✅ 全实现 | 严格遵循本文 seeding 规则 |
| assessment recompute / status 决议 | ⚠️ 简化版 | 仅维护 latest snapshot，不维护完整 supersede 链 |
| gap engine / inference rules | ⚠️ 部分 | 仅 `precondition_gate` + `quality_gate`；`comparability_gate` 复用 lineage compatibility |
| action proposal policy engine | ❌ 不实现 | 用 `FollowupAction` (C1+C2) 合成视图（`next_steps()`）代替 |
| supersede 链全量保留 | ❌ 仅 latest | 老 snapshot 不可读 |
| migration / invalidation policy | ⚠️ 部分 | 仅 soft invalidation；不支持 mixed-version 边界 |
| 跨 session 引用 | ❌ session-local | 当前仅承诺 session 内读写 |
| `agent_authored` proposition | ❌ 仅 system-seeded | |
| `discover` 多 shape seeding | ❌ 仅 anomaly | 等 `discover` 多 shape finding subtype 设计 |
| `forecast_evaluation_result` finding family | ❌ 不实现 | 当前无公共 forecast evaluation operator；等 finding family 与 operator 设计 |
| C3 / C4 followup | ❌ 永不实现 | 见 [原则 P5](#原则-p5recommended_followups-严格限定-c1--c2) |

## 存储设计

### 落点

```text
<project_root>/.marivo/analysis/
  ├── analysis.db                       # SQLite: session index, artifacts, jobs, current_session_id
  └── sessions/<session_id>/
      ├── meta.json                    # session metadata
      ├── jobs/<job_id>.json           # existing analysis job records
      ├── frames/<artifact_id>/
      │   ├── data.parquet             # raw frame data only
      │   └── meta.json                # cached Surface 1 fields
      └── judgment.db                  # SQLite: artifact metadata + finding/proposition/assessment
```

**SQLite 是 source of truth**——artifact metadata、lineage、findings、propositions、assessments、blocking issues、followups 全部在 SQLite。Frame `meta.json` 只缓存 Surface 1 字段，服务 `load_frame()` ergonomics；Parquet 仅承载 raw tabular data，是数据副本，不承载身份或语义。

### 跨 store recovery 协议

step commit 序列：

1. 内存构造 frame → 写 `frames/<artifact_id>/data.parquet.tmp` + fsync
2. 原子 rename 为 `frames/<artifact_id>/data.parquet`
3. 计算 Parquet SHA-256
4. SQLite 开事务：插 artifact metadata（含 `frame_path`、`frame_sha`）+ 抽 finding + 校验 D4 + seed proposition + recompute assessment + 生成 C1 / C2 followup
5. SQLite commit

**失败模式与恢复**：

| 失败发生在 | SQLite 状态 | Parquet 状态 | 启动 recovery 行为 |
| --- | --- | --- | --- |
| 1 之前 | 无记录 | 无 | n/a |
| 1-2 之间 | 无记录 | `.tmp` 孤儿 | GC 扫描 `frames/*/*.tmp` 删除 |
| 2-5 之间 | 无记录 | 正式 frame dir 孤儿 | GC 扫描 `frames/<artifact_id>/` 与 SQLite 反 join，孤儿移到 `.gc/` 后删除 |
| SQLite commit 失败 | 回滚 | 正式 frame dir 孤儿 | 同上 |
| commit 成功 | 完整 | 完整 | n/a |

**SQLite 内** finding / proposition / assessment / followup 在同一事务内 commit，跨 store 一致性化归为单 store 事务一致性。

### Schema

```sql
PRAGMA user_version = 1;
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE artifacts (
  artifact_id              TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL,
  step_type                TEXT NOT NULL,
  artifact_type            TEXT NOT NULL,
  artifact_schema_version  TEXT NOT NULL,
  subject_payload          TEXT NOT NULL,       -- JSON
  lineage_payload          TEXT NOT NULL,       -- JSON
  confidence_scope         TEXT,                -- JSON
  quality_summary          TEXT,                -- JSON
  evidence_status          TEXT NOT NULL,       -- complete | partial | unavailable
  frame_path               TEXT,
  frame_sha                TEXT,
  triggered_by_followup    TEXT,                -- JSON {action_id, source_artifact_id} | NULL
  committed_at_us          INTEGER NOT NULL     -- microseconds since unix epoch UTC
);
CREATE INDEX idx_artifacts_session_type ON artifacts(session_id, step_type);

CREATE TABLE findings (
  finding_id               TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL,
  artifact_id              TEXT NOT NULL REFERENCES artifacts(artifact_id),
  finding_type             TEXT NOT NULL,
  canonical_item_key       TEXT NOT NULL,
  subject_axis             TEXT,
  subject_payload          TEXT NOT NULL,
  observed_window_start_us INTEGER,
  observed_window_end_us   INTEGER,
  quality_status           TEXT,
  payload                  TEXT NOT NULL,
  artifact_schema_version  TEXT,
  extractor_version        TEXT,
  committed_at_us          INTEGER NOT NULL,
  UNIQUE (artifact_id, finding_type, canonical_item_key)
);
CREATE INDEX idx_findings_session_type ON findings(session_id, finding_type);

CREATE TABLE propositions (
  proposition_id     TEXT PRIMARY KEY,
  session_id         TEXT NOT NULL,
  proposition_type   TEXT NOT NULL,
  origin_kind        TEXT NOT NULL,             -- 'system_seeded'
  derivation_version TEXT NOT NULL,
  subject_key        TEXT NOT NULL,
  payload            TEXT NOT NULL,
  seed_finding_refs  TEXT NOT NULL,
  created_at_us      INTEGER NOT NULL,
  UNIQUE (session_id, proposition_id)
);
CREATE INDEX idx_propositions_session_type ON propositions(session_id, proposition_type);
CREATE INDEX idx_propositions_subject ON propositions(session_id, subject_key);

CREATE TABLE assessment_snapshots (
  snapshot_id      TEXT PRIMARY KEY,
  proposition_id   TEXT NOT NULL REFERENCES propositions(proposition_id),
  session_id       TEXT NOT NULL,
  supersedes_id    TEXT,                        -- 当前总为 NULL；schema 预留
  status           TEXT NOT NULL,
  confidence       REAL,
  confidence_basis TEXT,
  payload          TEXT NOT NULL,
  created_at_us    INTEGER NOT NULL,
  is_latest        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_assess_latest ON assessment_snapshots(proposition_id, is_latest);

CREATE TABLE assessment_edges (
  snapshot_id  TEXT NOT NULL REFERENCES assessment_snapshots(snapshot_id),
  finding_id   TEXT NOT NULL REFERENCES findings(finding_id),
  role         TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, finding_id, role)
);

CREATE TABLE blocking_issues (
  issue_id           TEXT PRIMARY KEY,           -- stable hash of (artifact_id, kind, canonical(source_refs))
  session_id         TEXT NOT NULL,
  artifact_id        TEXT NOT NULL REFERENCES artifacts(artifact_id),
  kind               TEXT NOT NULL,              -- BlockingIssue.kind enum
  severity           TEXT NOT NULL,              -- 'warning' | 'blocking'
  payload            TEXT NOT NULL,              -- JSON: full BlockingIssue
  resolved_by_step_id TEXT,                      -- set when a later step removes this issue
  created_at_us      INTEGER NOT NULL
);
CREATE INDEX idx_blocking_issues_session_kind ON blocking_issues(session_id, kind);
CREATE INDEX idx_blocking_issues_artifact ON blocking_issues(artifact_id);
-- 索引支撑 OpenQuestion.reason='persistent_blocking_issue' 跨 artifact 按 kind 检索

CREATE TABLE followups (
  followup_id          TEXT PRIMARY KEY,         -- = action_id (stable hash)
  session_id           TEXT NOT NULL,
  source_artifact_id   TEXT NOT NULL REFERENCES artifacts(artifact_id),
  category             TEXT NOT NULL,            -- 'dag_continuation' | 'quality_remediation'
  source_issue_id      TEXT REFERENCES blocking_issues(issue_id),  -- NOT NULL iff category='quality_remediation'
  operator             TEXT,
  payload              TEXT NOT NULL,            -- JSON: full FollowupAction
  executed_step_id     TEXT,                     -- internal marker set only when commit receives triggered_by_followup
  created_at_us        INTEGER NOT NULL
);
CREATE INDEX idx_followups_session ON followups(session_id);
CREATE INDEX idx_followups_source ON followups(source_artifact_id);
```

Surface 1 字段在 SQLite 的落点对应关系：

| Surface 1 字段 | 落点 |
| --- | --- |
| `artifact_id` / `subject` / `source_refs` / `lineage` / `confidence_scope` / `quality` / `evidence_status` | `artifacts` 表（每 result 一行） |
| `blocking_issues` | `blocking_issues` 表（每 result 0..N 行） |
| `recommended_followups` | `followups` 表（每 result 0..N 行） |

### 时间纪律

- 所有时间列以 `INTEGER` 存 **microseconds since unix epoch UTC**
- 列名后缀 `_us` 显式标注单位
- Python 层 `datetime` 字段一律 **timezone-aware UTC**
- `observed_window` 的单位由 finding payload 自描述

### Schema migration

- 启动时 `PRAGMA user_version` 读出 → 与代码内 `EXPECTED_SCHEMA_VERSION` 比对
- 更小 → 顺序 apply `migrations/v{n}_to_v{n+1}.sql`
- 更大 → 抛 `SchemaVersionMismatch(AnalysisError)`，拒绝打开
- migration 失败 → 抛 `MigrationFailed(AnalysisError)`，保留原 db 不破坏

### 并发与多进程

- WAL 模式启用，允许多 reader + 1 writer
- 同一 session 同时被多进程持有 writer 锁会 `SQLITE_BUSY` → 抛 `SessionLockedByAnotherProcess(AnalysisError)`
- **不支持** 多 writer 协同写同一 session
- **不支持** 跨 session 查询；每 session 独立 db，跨 session 查询不在 scope

### 不写入 judgment.db 的内容

- secrets / credentials（per agent guide）
- 用户 prompt / agent 自由文本输出
- Frame raw data（在 `frames/` 下）

## Identity 与稳定性

### Artifact ID / Session ID 稳定性

| Id | 生成 | 稳定性 |
| --- | --- | --- |
| `session_id` | session 创建时；ULID（time-ordered + 随机） | 跨进程稳定；持久化在 `meta.json` |
| `artifact_id` | step commit 时；`stable_hash(step_type, normalized_inputs, normalized_params, semantic_anchors)` deterministic | **同 input replay 命中同 artifact_id** |
| `finding_id` | `stable_hash(artifact_id, finding_type, canonical_item_key)` | 跟随 `artifact_id` 稳定 |
| `proposition_id` | 按本文 seeding identity normalization | 跨 replay 稳定 |
| `followup.action_id` | `stable_hash(source_artifact_id, category, operator, canonical(input_refs), canonical(params))` deterministic | 同 result replay 命中同 action_id |

**replay 的实现要求**：

- `normalized_inputs` 把 source artifact refs 按 typed ref 标准化
- `normalized_params` 走 RFC 8785 JCS canonical
- `semantic_anchors` 锁定 metric / dimension / calendar 等 catalog id + version

### Subject fields

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `metric` | `str \| None` | metric semantic id |
| `entity` | `str \| None` | entity semantic id |
| `slice` | `dict[str, str | int | float | bool \| None]` | segment key map |
| `grain` | `str \| None` | grain token string (e.g. `"day"` for calendar grains, `"5minute"` for dynamic sub-day grains) |
| `analysis_axis` | `Literal[...]` | frame shape discriminator |

`grain` stores the normalized grain token.  For calendar grains the token
is the unit name (`"day"`, `"week"`, `"month"`, `"quarter"`, `"year"`).
For dynamic sub-day grains the token is `"{count}{unit}"` (e.g.
`"5minute"`).  This token is used both in `subject_key` computation and
in cross-step `ConfidenceScope` compatibility checks.

### Subject canonical key

`subject_key` 计算规则：

1. Subject payload 按 RFC 8785 JCS 规范化
2. nested object 的 key 按 lexical 序排
3. array 元素若有显式 sort_key，按 sort_key 排
4. SHA-256 取前 16 字节 hex

实现位置：`analysis/_subject.py::canonical_key(subject)`。

### Followup execution lineage

step lineage 新增字段 `triggered_by_followup: TriggeredByFollowup | None`：

```python
class TriggeredByFollowup:
    action_id: str
    source_artifact_id: str
    via: Literal["run_followup", "manual"]
```

`triggered_by_followup` 是内部 / persistence lineage 字段，当前公共 `session.*` wrapper 示例不暴露设置入口。实现层若显式记录 typed followup execution，新的 step lineage 使用 `via="manual"`；`via="run_followup"` 仅为历史 lineage / 兼容读取保留，类型字面量不删除，但不再对应现存执行入口。

`next_steps()` 通过 `followups.executed_step_id IS NULL` 过滤未执行项；`via` 只解释该 executed marker 来自哪种 lineage。

## Exception Taxonomy

per [agent-guide.md](../../../agent-guide.md)，所有新 exception subclass `AnalysisError`，携带 `kind / message / hint / details`。

| Exception | kind | 触发场景 |
| --- | --- | --- |
| `EvidenceStoreUnavailable` | `evidence_store_unavailable` | judgment.db 打不开 / IO 错误 |
| `FollowupGenerationRuleViolated` | `followup_rule_violated` | runtime 试图发出非 C1 / C2 的 followup（实现 bug guard） |
| `PropositionNotFound` | `proposition_not_found` | `session.evidence.proposition(id)` id 不存在 |
| `FindingExtractionFailed` | `finding_extraction_failed` | extractor 抛错或违反 D4 contract |
| `SchemaVersionMismatch` | `schema_version_mismatch` | db schema 版本与代码不兼容 |
| `MigrationFailed` | `migration_failed` | migration SQL 失败 |
| `SessionLockedByAnotherProcess` | `session_locked` | 多进程并发写同一 session |
| `EvidencePartial` | `evidence_partial` | seeding/assessment 失败但 artifact + findings 已 commit；作为 `BlockingIssue` 出现在 result |

## Analysis 流程衔接

### Step 提交副作用流

```text
agent: session.compare(current, baseline)
          │
          ▼
   ┌──────────────────────────────────────────────────┐
   │ analysis commit_result(...) pipeline          │
   │  1. build & run ibis expression                  │
   │  2. write frames/<artifact_id>/data.parquet.tmp  │
   │     + fsync                                      │
   │  3. rename to frames/<artifact_id>/data.parquet  │
   │  4. BEGIN SQLite transaction                     │
   │  4a. insert artifact metadata                    │
   │      (evidence_status='complete' 暂定)           │
   │  4b. extract canonical findings (D4 validated)   │
   │      → insert findings                           │
   │  4c. compute & insert confidence_scope,          │
   │      quality_summary                             │
   │  ── SAVEPOINT sp_evidence ──                     │
   │  4d. seed propositions (template registry)       │
   │  4e. recompute affected assessments (latest)     │
   │  4f. generate C1 + C2 followups; compute &       │
   │      insert blocking_issues                      │
   │  ── on 4d-4f success: RELEASE sp_evidence ──     │
   │  ── on 4d-4f failure:                            │
   │       ROLLBACK TO sp_evidence;                   │
   │       UPDATE artifact SET evidence_status='partial';│
   │       INSERT blocking_issue kind=evidence_partial──│
   │  5. COMMIT                                       │
   │  6. return result with all Surface 1 fields      │
   └──────────────────────────────────────────────────┘
          │
          ▼
   delta: DeltaFrame ← Surface 1 字段就位
```

savepoint 设计的关键性质：

- savepoint **之前**的写入（artifact / findings / confidence_scope / quality_summary）在最外层 COMMIT 时一定落盘
- savepoint **之内**的写入（seeding / assessment / followup / blocking_issues）可独立 rollback 而不影响 savepoint 之前的内容
- 失败时仍走最外层 COMMIT，把 `evidence_status='partial'` 与 `kind=evidence_partial` blocking issue 一并写入
- 整个流程仍是**单一外层事务一次 COMMIT**，跨 store 一致性化归为单 store 原子性

### 失败语义

| 阶段 | 失败处理 | `evidence_status` |
| --- | --- | --- |
| 1（ibis 执行） | abort；不返回 result；抛 typed exception | n/a |
| 2-3（Parquet 写） | abort；Parquet `.tmp` 在 GC 中清；抛 typed exception | n/a |
| 4a（artifact insert） | abort 外层 tx；Parquet GC | n/a |
| 4b（finding extraction / D4） | abort 外层 tx；Parquet GC；抛 `FindingExtractionFailed` | n/a |
| 4c（confidence_scope / quality_summary） | abort 外层 tx；Parquet GC | n/a |
| 4d-4f（seeding / assessment / followup / blocking_issues） | `ROLLBACK TO SAVEPOINT sp_evidence`；artifact + findings + confidence_scope + quality_summary 保留；`UPDATE artifact SET evidence_status='partial'`；插一条 `kind=evidence_partial` blocking_issue；继续到 COMMIT | `partial` |
| 5（外层 COMMIT） | 全部回滚；Parquet GC；抛 typed exception | n/a |
| judgment store 启动期不可用 | step 仍执行；result 仅 **in-memory**（不写 SQLite）；intrinsic 字段在；`blocking_issues` 含 `kind=evidence_store_unavailable`；本进程内下游可继续消费，进程重启后丢失 | `unavailable` |

Evidence 失败**不阻塞分析推进**。SAVEPOINT 模型保证 4d-4f 失败不影响前置 artifact / findings / quality 落盘——agent 仍可在 partial 状态下读取 quality 与 confidence_scope。

### Replay 语义

整 session replay 必须保证：

- 所有 `artifact_id` / `finding_id` / `proposition_id` / `followup.action_id` 一致
- `SessionKnowledge.snapshot_id` 同 input + 同 SQLite 状态下一致
- `next_steps()` 排序在同一 codebase version 下稳定

## ConfidenceScope 跨 step 兼容性

`result.meta.confidence_scope` 是**暴露字段**，不是 runtime 自动门控。agent 在跨 result 推理时**应**调 `confidence_scope.compatible_with(other)` 自查；runtime 不会自动拒绝 step。

```python
class ConfidenceScope:
    metrics: list[SemanticObject | SemanticRef]
    dimensions: list[SemanticObject | SemanticRef]
    time_window: TimeWindow
    alignment: AlignmentPolicy | None
    assumptions: list[str]
    definition_versions: Mapping[SemanticObject | SemanticRef, str]

    def compatible_with(self, other: ConfidenceScope) -> ScopeCompatibility:
        """返回 exact / compatible / incompatible / unknown，对齐 MetricDefinitionCompatibility。"""
```

未来可考虑把 `incompatible` 升级为 `BlockingIssue` 自动注入。

## 命名空间总览

```python
# === 公开（agent 默认 surface） ===
session.observe(...)
session.compare(...)
session.decompose(...)
session.discover.<objective>(...)
session.correlate(...)
session.hypothesis_test(...)
session.forecast(...)
session.assess_quality(...)

session.knowledge() -> SessionKnowledge

# === result meta 字段（自动填充） ===
result.meta.artifact_id
result.meta.lineage
result.meta.confidence_scope
result.meta.quality
result.meta.blocking_issues
result.meta.recommended_followups        # C1 + C2 only
result.meta.evidence_status

# === 半公开（audit / advanced） ===
session.evidence.findings(...)
session.evidence.propositions(...)
session.evidence.assessments(...)
session.evidence.proposition(id)
session.evidence.latest_assessment(prop_id)
session.evidence.trace(prop_id)

# === 内部 ===
session._judgment_store
session._extract_findings(...)
session._seed_propositions(...)
session._recompute_assessments(...)
session._generate_followups(...)
```

## SKILL.md 暴露范围

[`marivo/skills/marivo-analysis/SKILL.md`](../../../marivo/skills/marivo-analysis/) 应仅写入：

- 算子集
- `session.knowledge()` 与 `knowledge.facts/open_items/next_steps()` 默认入口（4 类）
- result meta 字段名 + walkthrough 示例
- `session.assess_quality(...)` 与 `result.meta.quality` 的区分
- `knowledge.next_steps()` + agent 显式 typed operator dispatch 的用法
- **`recommended_followups` 仅含 C1 + C2 的明确声明**——避免 agent 期待战略性建议

**不在 SKILL.md 暴露**：

- 5 类细分 typed fact 的完整字段集（放 `references/`）
- Surface 3 引擎对象
- judgment.db schema
- followup 生成规则白名单细节

SKILL.md 必须使用 见上文「算子目录」等章节 canonical 写法：`session = analysis.session.get_or_create(...)` + `session.observe(time_scope=..., dimensions=...)`。不容忍两套并行写法。

## 典型用法示例

### 单步分析 + 直接读 followup（Surface 1）

```python
session = analysis.session.get_or_create(name="dau_investigation")
current = session.observe(
    metric=session.catalog.get("analytics.dau"),
    time_scope={"start": "2026-05-01", "end": "2026-05-07"}, grain="day",
)
baseline = session.observe(
    metric=session.catalog.get("analytics.dau"),
    time_scope={"start": "2026-04-24", "end": "2026-04-30"}, grain="day",
)
delta = session.compare(current, baseline, alignment=analysis.window_bucket())

if delta.meta.blocking_issues:
    for issue in delta.meta.blocking_issues:
        print(issue.kind, issue.message)

for followup in delta.meta.recommended_followups:
    # category 必是 dag_continuation 或 quality_remediation
    if followup.category == "quality_remediation":
        print(f"remediation for issue {followup.source_issue_id}: {followup.operator}")
    else:
        print(f"valid next operator: {followup.operator}")
```

### 综合判断 + typed dispatch 执行 followup（Surface 2）

```python
knowledge = session.knowledge()
if knowledge.evidence_completeness == "unavailable":
    raise RuntimeError("judgment store unavailable; cannot reason about session state")

for change in knowledge.facts(kind="change"):
    print(change.subject, change.direction, change.magnitude, change.status)

for question in knowledge.open_items(kind="question"):
    print(question.reason, question.subject)

# next_steps 全是 C1 + C2，agent 自行评估业务相关性
next_steps = knowledge.next_steps(top=3)
for action in next_steps:
    if action.operator == "assess_quality":
        frame = session.get_frame(action.input_refs[0])
        result = session.assess_quality(frame)
    elif action.operator == "decompose":
        delta = session.get_frame(action.input_refs[0])
        result = session.decompose(delta, axis=session.catalog.get(str(action.params["axis"])).ref)
```

### 审计（Surface 3）

```python
for prop in session.evidence.propositions(proposition_type="change"):
    trace = session.evidence.trace(prop.id)
    print(trace.proposition.subject)
    print(trace.latest_assessment.status if trace.latest_assessment else None)
    print(len(trace.support_findings), len(trace.oppose_findings))
```

## 落地建议

实施切成 10 个任务，分 4 个 milestone。原则：先打通一条最薄的端到端竖切（compare → ChangeFact 全链路），再横向铺开其他 family 与 surface，最快暴露架构问题。

### Milestone A：基础设施（横切铺基础）

T1-T3 串行最小耗时；T2/T3 可部分并行。整体约 1 周。

#### T1. Contract types & exceptions

- 所有 dataclass / TypedDict / `*Ref`：`Subject`、`ResultLineage`、`ConfidenceScope`、`QualitySummary`、`BlockingIssue`、`FollowupAction`（含 `category` 扩展）、5 类 Fact 子类、2 类 `OpenItem` 子类、`EvidenceTrace`、`Finding`、`Proposition`、`Assessment`、`TriggeredByFollowup`
- Exception 表全部 8 类，subclass `AnalysisError`
- 不含任何 runtime 行为

**验收**：类型构造 + immutability + `to_dict()` 序列化 round-trip。

**规模**：1-2 天。**依赖**：无。

#### T2. Identity primitives

- `stable_hash()` 用于 `artifact_id` / `finding_id` / `proposition_id` / `followup.action_id`
- `canonical_key(subject)` JCS (RFC 8785) + lexical key sort + SHA-256 截 16 字节
- `normalize_inputs` / `normalize_params` JCS canonical
- 纯函数，无 IO

**验收**：固定 fixture 输入产固定字节输出；replay 等价测试。

**规模**：1-2 天。**依赖**：T1。

#### T3. judgment.db storage layer

- SQLite schema：`artifacts` / `findings` / `propositions` / `assessment_snapshots` / `assessment_edges` / `blocking_issues` / `followups` + 索引
- `PRAGMA user_version` + migration framework
- WAL 启用、connection lock 检测（`SessionLockedByAnotherProcess`）
- Repository 模式：每张表 CRUD
- 跨 store recovery：startup GC 扫描 `frames/*.tmp` 与孤儿 `.parquet`
- SAVEPOINT helper（包装 4d-4f 失败回滚 + `evidence_status='partial'` 写入逻辑）

**验收**：schema 完整性、migration round-trip、锁竞争、GC 情景；含 crash injection 测试覆盖跨 store recovery。

**规模**：3-4 天。**依赖**：T1。这是基础，所有后续任务都依赖它。

### Milestone B：第一条竖切（验证架构）

只覆盖 `observe` + `compare` → `DeltaFrame` → `ChangeFact` 一条链路。目的是用最小代码量验证 SAVEPOINT 流程、id 稳定性、Surface 1/2 数据流是否真的能跑通。整体约 1.5 周（端到端最容易踩问题，要留 buffer）。

#### T4. Extractor + seeding + assessment — change family only

- `metric_value` / `delta` finding extractor（仅这两种 family）
- T1 (change proposition) seed template
- `change_assessment` recompute（latest only）
- support / oppose edge 写入
- 不实现其他 6 个 family

**验收**：fixture-based 测试 compare → finding → proposition → assessment 链路 ids 与字段稳定。

**规模**：2-3 天。**依赖**：T2 + T3。

#### T5. Step executor + Surface 1 minimal

- `analysis.session.session()` + `observe` + `compare` 三个 operator hook 进 executor
- Pipeline：ibis → Parquet → SQLite tx → SAVEPOINT 流程图全跑通
- `confidence_scope` / `quality_summary` 同步计算
- `BlockingIssue` 注入逻辑（仅 `null_rate_high` / `comparability_incompatible` / `evidence_partial` 三种 kind）
- result 扁平字段填充
- `evidence_status` 三态实现

**验收**：端到端 `session.compare(...)` 返回带完整 Surface 1 字段的 result；partial / unavailable 路径覆盖。

**规模**：3-4 天。**依赖**：T3 + T4。

#### T6. Followup generator + Surface 2 minimal

- C1 白名单表实现（仅覆盖 `MetricFrame[time_series]` / `DeltaFrame[*]` 两行）
- C2 remediation 映射表实现（仅 `null_rate_high` / `comparability_incompatible` / `evidence_partial`）
- `FollowupGenerationRuleViolated` guard
- `SessionKnowledge` 最小实现：`facts(kind="change")` 投影、`next_steps()` 去重保序
- agent 显式 typed operator dispatch 示例（从 `knowledge.next_steps()` 读取 action，再调用对应 operator）
- `triggered_by_followup` 内部 lineage 写入（`via="manual"`；`via="run_followup"` 仅历史兼容；不通过当前公共 wrapper 示例暴露）

**验收**：完整脚本 `observe` → `compare` → `knowledge.facts(kind="change")` → `next_steps()` → agent 显式 typed operator dispatch 跑通；followup 生成规则 conformance test 通过。

**规模**：2-3 天。**依赖**：T5。

> 🎯 **第一个 demo-able milestone**：可以让 agent 完整跑一个 DAU WoW 比较 + 拿到 ChangeFact + 选 followup + 自动执行。如果架构有根本问题，这一步暴露。完成后必须做一次 agent 真实 walkthrough，验证 C1 / C2 白名单是否真的足够 agent 使用。

### Milestone C：横向铺开（按 family 并行）

T4-T6 验证了竖切可行后，剩下的 family / operator 可以多个工程师并行做。整体约 1.5 周（并行做能压缩）。

#### T7. 剩余 finding family extractors + seed templates

- 5 个剩余 family extractor：`decomposition_item`、`anomaly_candidate`、`correlation_result`、`test_result`、`forecast_point`、`observation`
- T2-T6 seed templates
- 各自的 `*_assessment` recompute 逻辑

**验收**：每 family 一组 fixture 测试；finding count + `canonical_item_key` + `subject_axis` 与 spec 一致。

**规模**：5-7 天（最大单任务；可考虑拆 2-3 个 sub-PR by family）。**依赖**：T6 验证完成。

#### T8. 剩余 operator step executor 接入

- `decompose` / `discover` / `correlate` / `hypothesis_test` / `forecast` / `assess_quality` / `transform` 全部接入 executor pipeline
- C1 白名单表补齐其余行（`MetricFrame[scalar/segmented/panel]`、`AttributionFrame`、`CandidateSet`、`AssociationResult`、`HypothesisTestResult`、`ForecastFrame`）
- C2 remediation 表补齐其余 `BlockingIssue.kind`

**验收**：每 operator 端到端 + conformance test 全覆盖。

**规模**：4-5 天。**依赖**：T6 + T7（部分）。可与 T7 并行：每接入一个 operator 配对它的 finding extractor。

#### T9. Surface 2 完整 + Surface 3

- `SessionKnowledge` 剩余 4 类 fact（`AttributedDriver` / `TestedHypothesis` / `ForecastSummary` / `AssociationSummary`）投影规则
- `OpenItem` 完整（`OpenAnomaly` + `OpenQuestion` 含两种 reason 来源）
- `BlockedFollowup` 计算
- `for_subject()` 过滤
- `session.evidence.findings` / `propositions` / `assessments` 查询 API
- `session.evidence.proposition` / `latest_assessment` / `trace`
- `EvidenceTrace` assembly

**验收**：跨 step 复杂场景测试；replay 后 `snapshot_id` + facts 集合不变。

**规模**：3-4 天。**依赖**：T7 + T8。Surface 2 完整与 Surface 3 可拆双 PR 并行。

### Milestone D：收尾

#### T10. SKILL.md 对齐 + walkthrough

- `marivo/skills/marivo-analysis/SKILL.md` 迁移到 `session.observe(time_scope=..., dimensions=...)` canonical 写法
- 默认 surface 文档（Surface 1 字段 + `knowledge.facts/open_items/next_steps`）
- 明示 `recommended_followups` 仅 C1 + C2
- 完整 walkthrough 示例
- `references/` 下放细分 Fact schema、Surface 3 文档

**验收**：SKILL 内每个 code block 实际可执行（doctest）。

**规模**：1-2 天。**依赖**：T9。

### 依赖图

```text
T1 ── T2 ── T3
            │
            ├── T4 ── T5 ── T6  (🎯 first end-to-end demo)
            │                │
            │                ├── T7 ─┐
            │                │      ├── T9 ── T10
            │                └── T8 ─┘
            │
            └─ (T3 lock contention test 可与 T4 并行)
```

**关键并行机会**：

- T7 与 T8 可以配对并行：每接一个新 operator (T8) 同时实现它的 extractor (T7)。两人协作能把 milestone C 压缩到 5-6 天总周期
- T9 内部 Surface 2 完整与 Surface 3 可拆双 PR 并行

### 风险点（决定先后顺序的真实原因）

1. **SAVEPOINT 模型在生产 SQLite 中是否真的工作如预期** — T3 + T5 是验证点。如果有 corner case（比如 SAVEPOINT 嵌套 + `foreign_keys = ON` 的交互），整套失败语义要重做。优先级最高，所以放在竖切早期暴露
2. **id 稳定性在真实 ibis expression 上的 normalize 是否 deterministic** — T2 单测可能覆盖不到 ibis 表达式细节，T5 端到端会暴露。所以竖切必须包含真实 operator，不能只用合成 fixture
3. **C1 / C2 白名单是否真的足够 agent 使用** — 只有 milestone B 跑完之后，让 Claude Code / Codex 真正试一遍才知道。如果跑完发现 followup 永远空、agent 不知所措，可能需要 spec 回滚——所以 T10 不要等到最后才让 agent 上手；T6 完成后就该做一次 agent 真实 walkthrough
4. **跨 store recovery 在 crash 注入测试下是否真的能收口** — T3 必须做 crash injection；否则线上偶发 orphan 不可恢复

### 时间估算

| Milestone | 单人耗时 | 备注 |
| --- | --- | --- |
| A | 约 1 周 | T1-T3 串行最小耗时；T2/T3 可部分并行 |
| B | 约 1.5 周 | 端到端最容易踩问题，要留 buffer |
| C | 约 1.5 周 | 并行做能压缩 |
| D | 3 天 | |

**总计**：单人约 5-6 周；双人并行（T7/T8 + Surface 2/3 双流）约 4 周。

每个任务必须以测试（含生成规则 conformance）+ spec 引用更新为 land 闸口。

## 相关文档

- 算子设计与 Evidence Access Surface 现已合并为本文档。
- `docs/specs/analysis/timezone-and-calendar-design.md` — 时区与日历对齐设计（已 superseded）。
