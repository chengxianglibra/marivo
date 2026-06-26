# Agent-Facing Analysis DSL Incremental Hardening (v2)

状态：proposal / RFC，供讨论，**不表示已被接受**。

本文是 `decision-centric-analysis-redesign.md` 经 code-grounded review 与后续讨论后的收敛版。
核心前提已经调整为：

> Marivo 是纯 Python 分析计算库，不集成 LLM、不做推理规划、不替 agent 选择下一步。
> Agent 通过 skill 和 Marivo API 学会分析流程；Marivo 只提供 deterministic computation、
> typed artifacts、contract metadata、quality / blocking issues、可审计 lineage，以及适合
> agent 多轮脚本循环使用的持久 artifact 协议。

因此，v2 不再把目标表述为“runtime 决策器”。更准确的目标是：

- 用少量稳定 API 表达常见 metric 分析计算。
- 只有在明显降低 agent 认知成本时，才用 composite operator 封装确定性多步 DAG；否则直接
  提升清晰 primitive 为 agent-facing core。
- 用 primitive algebra 保留精确控制和可调试性。
- 所有 operator 返回同一层级的 typed artifact/result；composite 如存在，其“复合性”只体现在
  lineage、source refs、sidecar artifacts 和 deterministic execution metadata 中。
- 将 artifact 设计成可持久、可重算、可冷启动恢复、可渐进读取的 analysis DAG 节点，而不只是
  一次函数调用的临时返回值。
- 通过 `schema()` / `contract()` / `quality_summary` / `blocking_issues` 帮助 agent 自检和恢复，
  但不提供 planner-style next action。

## 1. 设计边界

Marivo 应该做：

- materialize semantic metrics；
- 比较窗口、人群、版本或实验组；
- 计算归因、候选、关联、检验、预测和质量评估；
- 记录 lineage、source refs、resolved windows、quality_summary 和 blocking issues；
- 暴露 deterministic affordances：当前 artifact 在机械上能接哪些 operator、缺哪些输入、
  哪些 precondition pass/fail；
- 支持跨脚本恢复、低成本读取、可审计 cache/freshness metadata；
- 对 shape / kind / anchor / policy 错误 fail closed，并返回结构化教学错误。

Marivo 不应该做：

- 根据自然语言问题自动规划分析 DAG；
- 自动选择“最佳下一步”；
- 对合法下一步做 rank、recommend、headline、why-next 或 conclusion；
- 自动判断业务上是否应该继续分析；
- 把 candidate / correlation / low-quality attribution 包装成业务结论；
- 在语义不明确时替 agent 猜 metric、axis、window、cohort、threshold；
- 把 agent 的 working conclusion、排除理由、业务判断写入 core artifact truth。

Agent 的 planning 责任由 skill 承担。Marivo 的责任是让每一步计算可靠、可复现、可审计、
可恢复。

### 1.1 计算 vs 判断边界

| Marivo 能确定性暴露 | 只能由 agent 判断 |
| --- | --- |
| 类型合法的可接 operator / capability | 选择下一步做哪个 operator |
| required inputs 和 preconditions pass/fail | 判断哪个候选“有意义” |
| 固定算法产出的 score / candidate / contribution | 选择 objective、阈值、axis、cohort |
| 机械可预填参数，例如当前 artifact ref、已解析 timescope | 填业务判断参数 |
| 事实摘要、质量状态、blocking issues、lineage | 写结论、headline、叙事和停止条件 |

直接后果：

- 不新增 `recap()` 作为 core API。去掉 headline / working conclusion / next step 后，
  它会退化为 `summary()` + `contract()` + `blocking_issues` 的重复投影。
- `repr` / `show()` 只能放 deterministic descriptor，例如 ref、kind、materialization state、
  row count、固定规则计算的 total/pct；不要叫 `headline`，不要暗示这是业务结论。
- 不暴露 `recommended_followups` 公共语义；目标态只允许 non-ranked affordance，见
  [5.2](#52-contract-affordances)。

## 2. Agent script-loop constraints

Agent 使用 Marivo 的真实模式通常是：

```text
write analysis script -> run -> read result -> revise script -> run again
```

一个 analysis 会跨多个 loop 轮次，甚至跨压缩后的 context 或新脚本文件。因此 frame/result
必须满足以下约束：

| 约束 | loop 真实情形 | 设计要求 |
| --- | --- | --- |
| 重算安全 | 每轮可能重跑累积脚本 | operator 尽量纯计算；artifact 支持 fingerprint / cache metadata；重复执行不制造语义漂移。 |
| 冷启动重建 | 轮次 N+1 可能丢失内存对象 | `get_frame(ref)` / persisted metadata 能恢复 artifact 的 kind、schema、lineage、quality、blocking。 |
| 读经济学 | 每次读 frame 消耗 context token | 提供 `repr -> summary() -> preview() -> to_pandas()` 分层读取，不迫使 agent 读大 frame。 |
| 失败可续 | 多步脚本第 k 步失败，前 k-1 步已物化 | 默认 operator fail-loud；session/job 层保留已完成上游 refs 和结构化错误，下一轮可复用上游。 |
| 跨轮状态可读 | 一个 analysis 是多 frame DAG，没有单个对象“就是 analysis” | artifact lineage、frame summaries、recent jobs 和 optional factual snapshot 能解释当前计算状态。 |

重构判断：frame/result 不只是“一次函数调用返回值 + 元数据”，而是持久、可重算、可冷启动恢复、
可渐进披露的 analysis DAG 节点。但这个 DAG 节点仍然只承载事实和计算契约，不承载 agent
判断。

## 3. API surface

v2 不再把默认面理解成“composite convenience layer”。默认面应是 **agent-facing core
operator surface**：agent 在 quickstart 和 skill 主路径中应该学习的唯一分析 API 面。

一个 operator 只有在满足以下至少一条时才进入 agent-facing core：

- 明显减少 agent 要写的步骤，并且不隐藏业务判断；
- 固定 output family，减少参数歧义；
- 名称直接表达计算任务，而不是 primitive 的别名；
- 失败时比底层实现更具教学性；
- 它本身就是自然、清晰、不可再简化的 primitive。

否则应删除 default alias，把 primitive 优化后提升为 agent-facing core，或降级为
internal / expert implementation surface。

### 3.1 Agent-facing core operator surface

默认对 agent 展示这组入口：

| Operator | 输出 | 说明 |
| --- | --- | --- |
| `observe` | `MetricFrame` | 从 semantic metric 物化观测结果。不要另造 `measure`。 |
| `compare` | `DeltaFrame` | 只接受两个已观测 `MetricFrame`；不支持 metric + windows shorthand。 |
| `attribute` | `AttributionFrame` | 对 `DeltaFrame` 做 deterministic attribution；不写业务解释叙事。 |
| `discover.<objective>` | `CandidateSet` | objective-specific candidate discovery；不提供 generic `scan(objective=...)`。 |
| `correlate` | `AssociationResult` | frame-to-frame statistical association；不承担 causality / explain。 |
| `hypothesis_test` | `HypothesisTestResult` | 对已准备好的 frame / delta / sample 执行统计检验；不另造 `test`。 |
| `forecast` | `ForecastFrame` | 对已观测 history frame 预测；若需要 history，agent 先显式 `observe`。 |
| `assess_quality` | `QualityReport` | 评估 artifact 质量、覆盖率、可比性、可归因性等。 |
| `derive_metric_frame` | `MetricFrame` | 通过 governed Ibis query escape hatch 产出并验证 canonical metric frame。 |

`compare` 遵守一个功能单一入口原则：它就是 frame-to-frame delta operator。若 agent 要比较两个
时间窗口，必须先显式 `observe` 当前窗口和基准窗口，再调用 `compare(current_frame,
baseline_frame)`。这牺牲一个 convenience shortcut，但换来更清晰的 lineage、更少 overload、
更少“函数替 agent 猜窗口/粒度/维度”的风险。

### 3.2 Internal / expert implementation surface

Internal / expert surface 用于调试、实现分解和少量专家场景。它不应在 quickstart 中与
agent-facing core operators 并排教学。

| Internal / expert API | 输出 | 说明 |
| --- | --- | --- |
| `decompose` | `AttributionFrame` | 单 axis、frame-local attribution；不自动 materialize missing dimensions。 |
| `transform.<op>` | same family as input | family-preserving reshape/filter/rank/window/normalize。 |
| `sample_summary` / `sample_frame` | sample artifact | 为 `hypothesis_test` 准备样本或摘要；不是默认 agent 主路径。 |
| internal scratch / promotion | internal artifact or canonical frame | `derive_metric_frame` 的内部实现阶段，或 expert/debug namespace。 |

命名原则：

- Agent-facing core 不暴露仅换名的 convenience alias。
- `explain` 删除，避免暗示业务解释；使用 `attribute` 表达 deterministic attribution。
- `scan` 删除，避免把认知成本藏进 `objective` 字符串；使用 `discover.<objective>` namespace。
- `test` 删除；统一为更明确的 `hypothesis_test`。
- `assess` 改为 `assess_quality`，避免和一般业务评估混淆。
- `compare_frames`、`correlate_frames`、`forecast_frame` 不作为默认或高级同义词教学；
  frame 输入语义分别合并到 `compare`、`correlate`、`forecast`。
- 不新增 `session.run(intent)` 作为默认执行路径。Affordance 可以帮助 agent 生成下一段
  Python 调用，但执行仍应回到明确的 public operator。
- `scratch.from_ibis/from_pandas` 和 `promote.*` 不进入 agent-facing surface。它们可以作为
  `derive_metric_frame` 的内部实现阶段，或放在 expert/internal namespace 中用于调试和审计。

## 4. 时间窗口输入

默认推荐显式 absolute `timescope`，不要把自然语言短语作为核心 API：

```python
timescope = {"start": "2026-06-18", "end": "2026-06-25"}
```

不推荐：

```python
mv.window("last_7d")
```

原因：

- `last_7d` 依赖运行时日期、时区、是否包含今天、闭开区间和业务日历；
- agent 可以自己基于用户问题计算 start/end；
- explicit `timescope` 更容易复现和审计；
- resolved window 必须进入 artifact metadata 和 lineage。

可以提供 typed relative constructors，但它们必须显式、可解析、可固化：

```python
mv.relative_window(days=7, end="2026-06-25", timezone="Asia/Shanghai")
mv.previous_window(current_window)
mv.trailing_window(days=7, anchor="2026-06-25")
```

Relative constructor 一旦执行，必须解析成 absolute `[start, end)` 并写入 artifact metadata。
Skill 可以教 agent 如何把“last 7 days”翻译成 absolute `timescope`，但 Marivo API 不应把
自由字符串作为主要入口。

## 5. Loop-friendly unified artifact/result abstraction

所有 operator 返回同一层级的 artifact/result。不存在“L1 返回 CompositeResult，L2 返回
Frame”的两套抽象。

公共协议：

```text
artifact.ref
artifact.kind
artifact.summary()
artifact.schema()
artifact.contract()
artifact.quality_summary
artifact.blocking_issues
artifact.lineage
artifact.state
artifact.show()

# only for tabular public artifacts
artifact.preview(limit=...)
artifact.to_pandas()
```

其中：

- `ref` 是可跨脚本恢复的 artifact identity。目标态下 `ref` 应尽量等于 deterministic
  artifact id，而不是随机 job 派生 id。
- `summary()` 是低 token 的结构化事实摘要，适合跨轮恢复和导航；不能包含 headline、业务结论、
  推荐动作或 salience 排序。
- `schema()` 描述列、role、semantic shape、time axis、nesting 等结构。
- `contract()` 描述 mechanical compatibility：canonical/internal、可被哪些 operator 消费、
  缺哪些 anchors/policies、是否存在 blocking issues，以及 non-ranked affordances。
- `quality_summary` 是 cheap、bounded、deterministic、persisted projection，只暴露已物化的轻量质量事实。
- `assess_quality(artifact)` 是唯一 public quality assessment operator，返回独立
  `QualityReport` artifact。
- `blocking_issues` 解释为何当前计算不可用或不可信；它应是 `contract().blocking_issues` 的
  便捷投影，不应有第二套来源。
- `lineage` 记录 primitive/composite 展开的步骤、source refs、resolved windows 和 policy。
- `state` 的 baseline 字段只聚合 materialization 和 content hash。cache、freshness、
  superseded relationship 是后续扩展，避免在 datasource snapshot / cache policy 语义稳定前过度承诺。
  失败状态属于 job / step recovery metadata，不混入 terminal artifact family。
- `preview(limit=...)` 是 bounded row projection。
- `show()` 是 bounded result card；不要默认暴露 dataclass repr。
- `to_pandas()` 只对 tabular public artifacts 暴露，返回 copy；非 tabular result 不应伪装成
  DataFrame。

`state` 不是业务判断对象。它只承载库可以确定性产出的事实，例如：

```text
ArtifactState:
  materialization: materialized | recomputed | partial
  content_hash: str | None

  # optional extensions, not required for the baseline public protocol
  cache: {policy, last_event} | None
  freshness: {status, as_of, source_snapshot} | None
  superseded_by: ref | None
```

### 5.1 分层 read 协议

Agent 读取 artifact 时应默认沿这个顺序逐层下钻：

```text
repr  ->  summary()  ->  preview(limit=...)  ->  to_pandas()
```

- `repr`：单行 descriptor，只包含 ref、kind、materialization state、row count 等确定性字段。
- `summary()`：结构化事实摘要，适合跨轮恢复和导航。
- `preview()`：有限行数和有限列数的行级预览。
- `to_pandas()`：只在 agent 明确需要完整数据时调用；tabular frames 返回 copy，
  non-tabular result 不应伪装成 frame。

### 5.2 Contract affordances

`contract()` 可以包含 `affordances`，但 affordance 不是 recommendation。

```text
Affordance:
  operator: public API path, e.g. "compare" or "discover.segments"
  required_inputs: typed refs or missing input descriptors
  preconditions: [(check, pass|fail, reason)]
  param_template:
    deterministic_slots: already-filled values
    judgment_slots: blank fields agent must decide
  expected_output_family: artifact family
```

Marivo 只说“这些门在机械上存在、每扇门要什么、哪些现在打不开”。Agent 决定开哪扇门、
填哪些 judgment slots、是否停止分析。

约束：

- `affordances` 不排序、不 rank、不叫 recommended。
- `affordances` 不包含 headline、why-next、business conclusion。
- `affordances` 不应促成 `session.run(intent)` 作为默认主路径；它服务 discovery、
  troubleshooting 和代码生成。
- `contract()` 只能声明机械兼容性。是否继续、如何选择 axis、是否转人工，是 agent 基于 skill
  和 artifact facts 做的事情。

不要提供 planner-style `decision_descriptor()` / `DecisionAction` / `next_actions`。

## 6. Core operator output rules

Agent-facing core operator 必须遵守：

1. 输出必须是 existing or approved typed artifact family。
2. 不创建额外 envelope 来包 primary result。
3. 若内部展开多个实现步骤，这些步骤必须进入 `lineage.steps` 或 internal sidecar lineage。
4. 中间 artifacts 必须以 stable role/source ref 暴露。
5. 不能隐藏关键选择，例如 axis、window、hypothesis、budget、anchor。
6. 若输入不足，抛结构化错误，而不是猜默认。
7. 若中间步骤已物化但后续失败，默认 public operator 仍 fail-loud；已完成上游通过
   persisted refs / job record / structured error 暴露，不把 `FailedStep` 混入 terminal
   artifact family。

示例：显式窗口比较

```python
current = session.observe(
    metric=session.catalog.get("analytics.dau"),
    timescope={"start": "2026-06-18", "end": "2026-06-25"},
    grain="day",
)
baseline = session.observe(
    metric=session.catalog.get("analytics.dau"),
    timescope={"start": "2026-06-11", "end": "2026-06-18"},
    grain="day",
)
delta = session.compare(current, baseline, alignment=mv.window_bucket())
```

`compare` 本身只接受 frames。当前/基准窗口选择由 agent 在 `observe` 中显式表达。

示例：`attribute(delta, axes=[...])`

```text
optional discover.axes -> CandidateSet
decompose(delta, axis=...)
optional nested composition
```

返回值是 `AttributionFrame`。nested / recursive attribution 仍压平到同一个 artifact family，
通过 `path`、`depth`、`parent_path`、`axis`、`segment_key`、`contribution`、`residual`、
`method`、`coverage_status` 等列表达层级。候选 axis、coverage warning、budget stop 等进入
metadata / blocking issues / lineage 或 internal refs，但不是下一步推荐器，也不是业务解释叙事。

## 7. Default operator details

### 7.1 `observe`

```python
series = session.observe(
    metric=session.catalog.get("analytics.dau"),
    timescope={"start": "2026-06-18", "end": "2026-06-25"},
    grain="day",
    dimensions=[session.catalog.get("analytics.events.platform")],
)
```

`observe` 是 source-to-artifact primitive，也是默认入口。它返回 `MetricFrame`。

### 7.2 `compare`

`compare` 只支持 frame 输入：

```python
current = session.observe(
    metric=session.catalog.get("analytics.dau"),
    timescope={"start": "2026-06-18", "end": "2026-06-25"},
    grain="day",
    dimensions=[session.catalog.get("analytics.events.platform")],
)
baseline = session.observe(
    metric=session.catalog.get("analytics.dau"),
    timescope={"start": "2026-06-11", "end": "2026-06-18"},
    grain="day",
    dimensions=[session.catalog.get("analytics.events.platform")],
)
delta = session.compare(current, baseline, alignment=mv.window_bucket())
```

删除 metric + windows shorthand。原因：

- 让窗口、粒度、维度选择显式进入两个 `observe` step；
- 避免 `compare` 同时承担 materialization 和 delta 计算；
- 保持一个功能单一入口：`MetricFrame + MetricFrame -> DeltaFrame`。

### 7.3 `attribute`

```python
drivers = session.attribute(
    delta,
    axes=[
        session.catalog.get("analytics.events.country"),
        session.catalog.get("analytics.events.platform"),
    ],
    mode="nested",
    budget=mv.cost_budget(max_axes=2, max_cardinality=200),
)
```

`attribute` 不是 planner。它只在显式 axes 或显式 search policy 下做 deterministic attribution。
缺 axis/search policy 时必须 fail closed。无论 flat、nested 还是 recursive mode，public result
都保持 `AttributionFrame`，不新增 experimental sidecar artifact family。

### 7.4 `discover.<objective>`

```python
candidates = session.discover.driver_axes(
    delta,
    search_space=[
        session.catalog.get("analytics.events.country"),
        session.catalog.get("analytics.events.platform"),
    ],
)
```

`discover.<objective>` 返回 `CandidateSet`。Candidate 是候选，不是事实结论。

默认 namespace 应包含少量 objective-specific 函数，例如：

- `discover.driver_axes(delta, search_space=...)`
- `discover.point_anomalies(metric_frame, ...)`
- `discover.period_shifts(delta, ...)`
- `discover.interesting_windows(metric_frame_or_delta, ...)`
- `discover.interesting_slices(metric_frame_or_delta, ...)`
- `discover.cross_sectional_outliers(metric_frame, ...)`

不要提供 `scan(objective="...")` 作为默认入口。它把 agent 的认知成本从函数名转移到字符串
参数，且更难做 typed help / typed error。

### 7.5 `correlate`

```python
association = session.correlate(
    dau_frame,
    revenue_frame,
    method="pearson",
    alignment=mv.window_bucket(),
)
```

`correlate` 返回 `AssociationResult`。它表达统计关联，不表达 causality，也不替 agent 写
解释。

### 7.6 `hypothesis_test`

```python
verdict = session.hypothesis_test(
    delta,
    hypothesis="mean_changed",
    alpha=0.05,
    sampling=mv.SamplingPolicy(unit="day", method="paired_numeric_summary"),
)
```

`hypothesis_test` 接受已准备好的 `DeltaFrame`、`MetricFrame` 或 sample artifact，并返回
`HypothesisTestResult`。它不接受 metric + windows shorthand；如需比较窗口，agent 先显式
`observe` 和 `compare`。

### 7.7 `forecast`

```python
projection = session.forecast(
    history_frame,
    horizon=mv.forecast_horizon(periods=14, grain="day"),
)
```

`forecast` 接受已观测 history `MetricFrame`，返回 `ForecastFrame`。它不隐式 materialize
history；agent 应通过 `observe` 显式声明历史窗口、粒度和维度。

### 7.8 `assess_quality`

```python
quality = session.assess_quality(delta)
```

`assess_quality` 返回 `QualityReport`。它只评估 artifact 的数据质量、覆盖率、可比性、
可归因性等机械质量，不做业务好坏判断。

`artifact.quality_summary` 不等价于 `assess_quality(artifact)`。前者只是读取 artifact 上已有的
轻量质量摘要；后者是唯一质量评估动作入口，会执行显式 checks 并产出 `QualityReport`。源
artifact 最多记录 `latest_quality_report_ref` 或在 `contract()` 中暴露是否已有详细质量报告，
不能复制完整 `QualityReport` 形成第二套事实源。

### 7.9 `derive_metric_frame`

```python
retention = session.derive_metric_frame(
    metric=session.catalog.get("analytics.retention_7d"),
    query=mv.ibis_query(
        datasource="warehouse",
        build=lambda db, ctx: ...,
    ),
    columns=mv.metric_columns(
        value="retention_rate",
        time=mv.time_column(
            column="cohort_date",
            ref=session.catalog.get("analytics.cohort_date"),
        ),
        dimensions=[
            mv.dimension_column(
                column="platform",
                ref=session.catalog.get("analytics.events.platform"),
            ),
        ],
    ),
    timescope={"start": "2026-06-18", "end": "2026-06-25"},
    grain="day",
    label="ios_7d_retention",
)
```

`derive_metric_frame` 是唯一默认公开的 governed escape hatch。它的目标不是替代
`observe`，而是在 semantic metric 已存在、但标准 `observe` 不能表达某个后端计算时，让
agent 用一段受约束的 Ibis query 产出 canonical `MetricFrame`。

接口原则：

- `metric` 必填，且必须是 catalog metric ref / object。没有 semantic metric，就不能产出
  canonical `MetricFrame`；只能停留在 internal / expert scratch 结果。
- `query` 是 Ibis/backend query spec。默认 agent-facing 文档不教学 pandas compute 回灌；
  `to_pandas()` 是读取/检查出口，不是默认 canonical frame 生产入口。
- `columns` 只声明 query 输出表的列绑定：`value`、可选 `time`、可选 `dimensions`。
  其中 `column` 字段永远是输出列名 string；`ref` 字段永远是 catalog semantic ref。
- 参数类型边界必须稳定：top-level semantic identity 用 semantic ref，query output schema 用
  column string，二者只在 `mv.time_column(...)` / `mv.dimension_column(...)` binding 中相遇。
  禁止 `time_axis: str | SemanticRef` 这类 union 槽位。
- `semantic_kind` 不作为参数暴露。库根据 columns 机械推断：value only -> `scalar`；
  value + time -> `time_series`；value + dimensions -> `segmented`；value + time +
  dimensions -> `panel`。
- `semantic_model` 不作为参数暴露。库从 `metric` 的 catalog 所属模型推断，避免 agent 重复填写
  并引入不一致。
- `timescope` / `grain` 表示本次 derivation 的观测范围与时间粒度；如果 query 已经编码过滤，
  也必须在参数中显式声明，用于 lineage、summary、freshness 和 cache correctness。
- `label` 是 session-local artifact label，不是 semantic metric id。
- output family 由函数名固定，不通过 `output=...`、`family=...` 或 `anchors=...` 切换。
- 实现可以内部展开为 `ibis_query -> internal scratch -> internal promotion -> MetricFrame`，
  但 public lineage 对 agent 展示为一个 governed `derive_metric_frame` step，并通过
  sidecar/internal refs 保留审计细节。

`version` 不进入默认接口。它的真实用途是 cache / lineage 失效键，不是业务版本。默认实现应从
operator id、compiled SQL / Ibis expression、query params、metric ref、semantic definition
version、columns binding、timescope、grain、datasource freshness marker 和 Marivo artifact schema
version 自动生成 fingerprint。只有 query 不能稳定编译或 fingerprint 时，expert namespace 才可
提供显式 `recipe_id` / `fingerprint` 兜底；quickstart 和 skill 主路径不教学这个参数。

## 8. L2 frame/result refactor

L2 不需要统一成一个 `AnalysisResult`，但需要统一 artifact protocol。

Artifact families：

| Family | 用途 |
| --- | --- |
| `MetricFrame` | observed metric facts |
| `DeltaFrame` | current vs baseline difference |
| `AttributionFrame` | contribution attribution |
| `CandidateSet` | candidates only |
| `AssociationResult` | statistical association |
| `HypothesisTestResult` | statistical test result |
| `ForecastFrame` | model projection |
| `QualityReport` | explicit quality assessment |

Internal / expert-only artifact families may include `ScratchResult` / `ExplorationResult` for
intermediate persistence, debugging, and audit. They are not part of the default
agent-facing artifact family table and must not be accepted by core primitives.

重构要求：

- 保留 closed, kind-dispatched variants；不要合并成 kind + optional fields 的 mega-class。
- 所有 family 实现同一 loop-friendly artifact protocol。
- Agent 默认只需要学习 base protocol：`ref`、`kind`、`summary()`、`schema()`、`contract()`、
  `quality_summary`、`blocking_issues`、`lineage`、`state`、`show()` 的边界。
- 精确 nominal result 类型继续服务库内部 correctness、preflight、help、typing 和 fail-loud。
- tabular public artifacts 支持 `preview()` / `to_pandas()`；非 tabular result 不默认伪装成 frame。
- `CandidateSet` 不 seed confirmed facts，不承载推荐动作；候选行最多携带机械可解析的 source refs
  和 candidate attributes。
- `ScratchResult` / `ExplorationResult` 如存在，只能作为 internal / expert artifact；进入 canonical
  metric-frame 链路必须通过 `derive_metric_frame` 的 validation path，而不是 agent-facing
  `promote.*` API。
- `schema()` / `contract()` / `state` 应覆盖所有 artifact family。
- `show()` 输出 bounded result card，避免默认 dataclass repr。
- `quality_summary` 和 `QualityReport` 明确分层：前者是 metadata projection，后者是
  `assess_quality()` 的 terminal artifact。

## 9. Runtime persistence, cache, and recovery requirements

这一节约束 runtime 行为，但不新增 planner 能力。

### 9.1 Content-addressed artifact identity

Frame persistence 应填充 content/fingerprint metadata，避免同一确定性计算在多轮脚本中反复触发
backend 查询。Fingerprint 至少包含：

- operator / capability id；
- input artifact fingerprints；
- resolved params，包括 absolute timescope、grain、dimensions、policies；
- semantic definition version；
- datasource snapshot、freshness marker 或显式 cache policy；
- Marivo artifact schema version。

不能只 hash operator 和 params。对外部 datasource 来说，cache hit 的正确性依赖数据快照、
freshness 约束或用户显式允许的 cache policy。

### 9.2 Cold-start rehydration

`get_frame(ref)` 和 `frame_summaries()` 必须能帮助 agent 在新脚本或压缩后 context 中恢复：

- artifact kind / schema / semantic shape；
- source refs 和 lineage；
- resolved windows；
- quality_summary 和 blocking issues；
- deterministic `state` metadata，包括 materialization、cache、freshness、superseded relationship；
- bounded summary fields。

Rehydration 不应重新查询 datasource，除非用户显式要求 refresh / recompute。

### 9.3 Failure recovery

默认 public operator 仍然 fail-loud：如果 `compare()` 不能产出 `DeltaFrame`，就抛结构化错误，
不要返回 `DeltaFrame | FailedStep` 这样的 widened result。

但 session/job 层应保留可续信息：

- 已成功物化的上游 artifact refs；
- 失败 step 的 operator、expected/received、repair hints；
- resolved params 和缺失 judgment slots；
- 下一轮可以复用的 cache hits。

若未来需要非抛错批处理，可另设 advanced `StepOutcome` / `try_*` API。它不属于默认 agent-facing
operator surface，也不改变 terminal artifact family。

### 9.4 Session-level factual navigation

本设计不新增 public `AnalysisSnapshot` artifact。跨轮恢复先依赖已有 session-level facts：

- `frame_summaries()`：refs、kind、semantic shape、row count、created_at；
- `recent_jobs()`：steps / jobs / status / output refs；
- 单个 artifact 的 `summary()`、`schema()`、`contract()`、`state`。

如果后续读成本仍然过高，可以增加 `session.snapshot()`，但它必须只是 bounded factual
projection，不是 artifact family，不叫 `recap()`，不生成 conclusion、headline、working notes
或下一步推荐。

## 10. 与现有设计原则的关系

- **每个 public core operator 固定 output family**：保持。
- **Composite operator 不是默认层级**：保留 composite 能力，但只有在明显降低 agent 认知成本
  且不隐藏关键判断时，才进入 agent-facing core；返回 terminal typed artifact，而不是 wrapper
  result。
- **Projection / governed escape hatch 分层**：保持。公开 escape hatch 收敛为单一
  `derive_metric_frame`；scratch/promotion 是 internal / expert implementation detail。
- **Typed policy / typed refs**：保持。
- **No lazy-plan authoring path**：保持。Marivo 不做 planner，只做 deterministic computation。
- **Agent-facing surface**：quickstart 只展示 agent-facing core operator；advanced help 再展示
  internal / expert implementation surface。
- **Loop-friendly artifact protocol**：新增。它解决 agent 跨轮读、恢复、缓存和机械衔接问题，
  不解决 agent 的判断问题。

## 11. Breaking cutover phases

本设计是破坏性目标态，不提供兼容层、旧名 alias、自动 fallback 或双轨文档。实现任务切分为三个
独立 Phase：每个 Phase 都有自己的 public contract、验证面和明确非目标；可以分别实现和评审，
但最终发布必须同时满足三组目标态约束。

### Phase 1: Artifact Protocol And Runtime Facts

目标：先收敛所有 artifact/result 的公共协议和跨 loop 事实基础，不改默认 operator 语义。

包含任务：

1. 公共 artifact protocol 采用 §5 目标态：`ref`、`kind`、`summary()`、`schema()`、`contract()`、
   `quality_summary`、`blocking_issues`、`lineage`、`state`、`show()`。
2. `preview(limit=...)` / `to_pandas()` 只属于 tabular public artifacts；非 tabular result 不伪装成
   frame。
3. 删除 `decision_descriptor()` / `DecisionAction` / `next_actions` 表述；Marivo 不产出 planner-style
   next action。
4. 删除 `recommended_followups` 公共语义；目标态只保留 `contract().affordances`，字段语义为
   non-ranked、non-recommended、mechanical compatibility。
5. `contract().affordances[].operator` 使用 public API path，例如 `compare`、`attribute`、
   `discover.segments`；内部 capability id 不进入 agent-facing contract。
6. `ArtifactState` baseline 只包含 `materialization` 和 `content_hash`。`cache` / `freshness` /
   `superseded_by` 是后续扩展；它们不作为顶层 artifact 字段暴露。
7. 激活 artifact fingerprint / content hash 设计，但先定义 cache correctness，再实现 memoize。
8. 不新增 public `AnalysisSnapshot` artifact。跨轮恢复依赖 `frame_summaries()`、`recent_jobs()`、
   以及单个 artifact 的 bounded read protocol。
9. `quality_summary` 和 `QualityReport` 分层：前者是 metadata projection；`assess_quality()` 是唯一
   public quality assessment operator，产出 terminal `QualityReport`。

非目标：

- 不重命名或删除 operator；
- 不引入 `session.run(intent)` 默认执行路径；
- 不把 cache/freshness/superseded 做成 baseline 承诺。

验收口径：

- 所有 public artifact family 实现同一 base protocol；
- `summary()` / `schema()` / `contract()` / `state` 不包含 conclusion、headline、recommendation；
- 旧 planner 字段和 recommended followup 语义在 public API、help、docs 中不可见。

Implementation note: Phase 1 may keep internal evidence tables or private helpers
with historical names while the runtime is being cut over, but public artifact
metadata, help, API docs, and skills must expose only `quality_summary` and
`contract().affordances`. There is no compatibility promise for old public
`meta.quality` or `meta.recommended_followups` access.

### Phase 2: Core Operator Surface Cutover

目标：一次性切换 agent-facing core operator surface，删除重复入口和过宽 alias。

包含任务：

1. 不新增 `measure`；`observe` 是唯一默认 metric observation 入口。
2. 删除 `compare_frames` 和 metric + windows shorthand；`compare` 只支持
   `MetricFrame + MetricFrame -> DeltaFrame`。
3. 删除 `correlate_frames` / `forecast_frame` 公共入口；`correlate` / `forecast` 直接以 frame 输入作为
   目标态契约。
4. 删除 `explain`、`scan`、`test`、`assess` 公共入口；目标态名称分别是 `attribute`、
   `discover.<objective>`、`hypothesis_test`、`assess_quality`。
5. composite 不返回 `CompositeResult` envelope；所有 public operator 返回 terminal typed artifact。
6. `attribute` 的 flat / nested / recursive mode 都返回 `AttributionFrame`；层级结构用 flattened
   hierarchy rows 表达，不新增 public sidecar artifact family。
7. 默认时间窗口使用 explicit absolute `timescope`；relative window 只能是 typed constructor，
   不接受自由字符串主路径。
8. `derive_metric_frame` 是唯一默认公开 governed escape hatch；`derive_delta_frame` /
   `derive_attribution_frame` 不进入 agent-facing surface。
9. `derive_metric_frame` 采用 `metric + query + columns + timescope + grain + label` 契约；
   不暴露 `semantic_kind`、`semantic_model`、`version`、`measure_column`、`time_axis` union。
10. `scratch.from_ibis/from_pandas` 与 `promote.*` 不进入 agent-facing surface；如保留，只属于
    internal / expert namespace，用于实现分解、调试和审计。

非目标：

- 不实现 Phase 1 的 full artifact persistence / memoize 细节；
- 不新增 agent planner 或 operator recommendation；
- 不为了旧脚本提供 shim。

验收口径：

- quickstart public operator 只剩 §3.1 列表；
- 每个 public operator 固定 output family；
- 旧入口名、旧 shorthand、旧 promotion 主路径在 public API/help/docs/examples 中不可见。

### Phase 3: Agent-Facing Docs, Skills, And Discovery

目标：把 Phase 1 / Phase 2 的目标态教给 agent，保证 agent 主路径只学习一个清晰 surface。

包含任务：

1. 更新 analysis skill 主路径，只教 `observe / compare / attribute / discover.<objective> /
   correlate / hypothesis_test / forecast / derive_metric_frame / assess_quality`。
2. troubleshooting / advanced help 再展开 internal / expert APIs、contract affordances 和 recovery
   workflow；它们不得与 core operator 并排教学。
3. 所有示例使用 explicit absolute `timescope`，避免 `mv.window("last_7d")` 这类自由字符串主路径。
4. 文档和 help 中解释 `quality_summary` vs `assess_quality()`：读取 metadata projection 不是执行质量评估。
5. 文档和 help 中解释 `derive_metric_frame` 的参数边界：semantic ref 只做语义锚点，string 只做 query
   输出列名。
6. Discovery / affordance 文档只说 mechanical compatibility，不出现 rank、recommend、why-next、
   headline、working conclusion。
7. 对 broken / blocked / partial 情形，文档只教学 structured errors、job refs、frame refs 和 recovery
   workflow，不引入 widened terminal artifact family。

非目标：

- 不扩展 runtime 语义；
- 不新增 public artifact family；
- 不把 internal / expert APIs 重新包装成 convenience composites。

验收口径：

- docs、skill、examples、help 中没有旧入口和旧字段；
- agent 主路径能用一个 base artifact protocol 串联 operator；
- advanced 内容只作为 troubleshooting/reference 出现。

## 12. Resolved design decisions

1. `contract().affordances[].operator` 使用 public API path，例如 `compare`、`attribute`、
   `discover.segments`。内部可以维护 stable capability id，但 agent-facing contract 不暴露。
2. `ArtifactState` baseline 只包含 `materialization` 和 `content_hash`。`cache` / `freshness` /
   `superseded_by` 是后续扩展；它们不作为顶层 artifact 字段暴露。
3. 不新增 public `AnalysisSnapshot` artifact。agent 通过 `frame_summaries()`、`recent_jobs()`
   和单个 artifact 的 bounded read protocol 恢复状态。若后续需要 `session.snapshot()`，它只能是
   factual projection。
4. nested / recursive `attribute` 仍返回 `AttributionFrame`。层级结构用 flattened hierarchy rows
   表达；不新增 experimental public sidecar artifact family。
