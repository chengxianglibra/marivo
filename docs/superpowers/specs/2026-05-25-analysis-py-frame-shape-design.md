# analysis_py Frame Shape Coverage Design

Date: 2026-05-25
Status: approved design for implementation planning

## Goal

补齐 `marivo.analysis_py` 中 `observe` / `compare` / `decompose` 在 `segmented`
与 `panel` 两个 frame shape 上的能力缺口，使 Python 分析 API 在四种核心
`MetricFrame` shape (`scalar`, `time_series`, `segmented`, `panel`) 与对应
`DeltaFrame` / `AttributionFrame` shape 上保持一致的契约面。

本设计依据 `docs/specs/analysis/python-analysis-operator-design.md` 中
Python Analysis 算子的目标态，且建立在
`docs/superpowers/specs/2026-05-25-analysis-py-core-operator-alignment-design.md`
落地之后。

## Scope

In scope:

- `observe`：新增 `dimensions: list[DimensionRef] | None` 参数，支持
  `segmented` 与 `panel` 两种新 shape，shape 由 `(window.grain, dimensions)`
  组合推断。
- `compare`：接入 `segmented` 与 `panel`，segment 维度做 outer join，
  `AlignmentPolicy` 仅控制时间轴，`panel` 要求两端时间 grain 一致。
- `decompose`：解除当前对 `panel` 的硬拒绝，对 `panel_delta` 走
  per-bucket attribution，保留 time 信息；`flat` 路径
  (`scalar` / `time_series` / `segmented`) 行为不变。

Out of scope（留后续单独 spec）:

- `discover` 新 objective：`period_shifts`, `driver_axes`,
  `interesting_slices`, `interesting_windows`, `cross_sectional_outliers`。
- `correlate` 非零 single lag、`lag_sweep` 模式、`spearman` method。
- `transform` 算子层（目标态 spec 第 2 层，当前未实现）。
- composite operator 层（`attribute` / `auto_decompose` / `diagnose` 等）。

约束:

- 不新增 canonical artifact family。
- `MetricFrameMeta` / `DeltaFrameMeta` / `AttributionFrameMeta` 不做 schema
  字段增减；`semantic_kind` 字段当前已允许 `"panel"`，无需 migration。
- 不破坏 `scalar` / `time_series` 现有调用路径。

## `observe`: dimensions 与 shape 推断

### API

```python
analysis.observe(
    metric=MetricRef("sales.revenue"),
    window=...,
    dimensions: list[DimensionRef] | None = None,   # 新增
    slice: dict[str, Any] | None = None,
    session=...,
) -> MetricFrame
```

`dimensions` 中每一项必须是 `DimensionRef`；不允许裸字符串；不允许空 list
（传入空 list 抛 `SemanticKindMismatchError`，提示使用 `None` 表示
"无 dimension"）。

### Shape 推断

| `window.grain` | `dimensions` | 输出 `semantic_kind` |
| -------------- | ------------ | --------------------- |
| `None`         | `None`       | `scalar`              |
| 已设置          | `None`       | `time_series`         |
| `None`         | 非空          | `segmented`（新）     |
| 已设置          | 非空          | `panel`（新）         |

### `DimensionRef` 解析

- `DimensionRef(id="field_name")` 在 metric 引用的所有 dataset
  (`metric_ir.references.datasets`) 的 `dataset.fields` 中按 `name == id`
  查找。
- 必须在所有引用 dataset 中**唯一**出现：
  - 0 个命中 → `DimensionFieldNotFoundError`。
  - ≥2 个命中 → `AmbiguousDimensionError`，details 列出候选
    `<dataset>.<field>`。
- 命中后通过 `FieldIR.fn(dataset_table)` 生成 ibis 表达式，作为 group-by key。

### 执行模型 — segmented 分支

1. 与 time_series 一致地把 slice / window predicate 下推到 dataset。
2. 解析每个 dimension 到具体的 `(dataset_name, FieldIR)`。
3. 多 dataset metric：v1 仍要求所有 dimensions 落在**同一**主 dataset；
   分布到不同 dataset 抛 `DimensionAcrossDatasetsError`。
4. `group_by(<dimension columns>).aggregate(metric_expr).order_by(<dimension columns>)`。
5. 输出列：`<dim1>, <dim2>, ..., <metric_name>`。

### 执行模型 — panel 分支

1. 复用 time_series 的 `apply_time_series_bucket`，得到 `bucket_start`。
2. `group_by("bucket_start", <dimension columns>)
   .aggregate(<metric_name>=metric_expr)
   .order_by("bucket_start", <dimension columns>)`。
3. 输出列：`bucket_start, <dim1>, <dim2>, ..., <metric_name>`。
4. 与 time_series 一致：v1 不支持 multi-dataset metric 的 panel
   observe，命中即抛 `MetricShapeUnsupportedError(kind="WindowedTimeSeriesUnsupported")`
   或其 panel 对等错误（沿用现有错误码）。

### `MetricFrameMeta.axes`

- 现有 time axis 条目格式保持不变：
  `{"time": {"role": "time", "column": "bucket_start", "grain": ..., "time_field": ...}}`。
- 每个 dimension 额外加入一个条目：
  `{"<field_name>": {"role": "dimension", "column": "<field_name>"}}`。
- `semantic_kind` 反映最终推断结果。

### `observe` 错误

| Error | 触发 |
| ----- | ---- |
| `SemanticKindMismatchError`（空 list）| `dimensions=[]` 传入 |
| `DimensionFieldNotFoundError` | DimensionRef 在所有 dataset 中均未找到 |
| `AmbiguousDimensionError` | DimensionRef 在多个 dataset 中重名 |
| `DimensionAcrossDatasetsError` | 多个 dimension 落在不同 dataset |
| `MetricShapeUnsupportedError` | panel + multi-dataset metric |

## `compare`: segmented / panel

### 前置 type check

- 已有：两端必须是 `MetricFrame`、同一 `metric_id`、同 `semantic_kind`、同
  session。
- 新增：当 `semantic_kind == "panel"`，两端 `axes.time.grain` 必须相同，否则
  `PanelGrainMismatchError`。
- 新增：当 `semantic_kind in {"segmented", "panel"}`，两端 `axes` 中
  `role == "dimension"` 的列名集合必须相同，否则
  `SegmentDimensionMismatchError`，details 列出双方维度集。

### Segmented 路径

- `AlignmentPolicy` 必须为 `kind="window_bucket"`，其它 kind 抛
  `AlignmentPolicyNotApplicableError`，hint 提示
  "segmented compare 仅接受 AlignmentPolicy(kind='window_bucket')"。
- Join：以 segment 维度列作为 join key，做 **outer join**，suffix
  `_a` / `_b`。
- 输出列：`<dim1>, <dim2>, ..., current, baseline, delta, pct_change`。
- 任一边缺失的 segment：current/baseline 一侧为 NaN，`delta`、`pct_change`
  也为 NaN（不假设 0）。
- 排序：按 segment 维度列稳定排序。
- Lineage / metadata：在 `alignment_dump` 之外的 meta 中记录
  `segment_count`, `a_only_segments_count`, `b_only_segments_count`，便于
  follow-up 分析。

### Panel 路径

- 完整支持现有 4 种 `AlignmentPolicy.kind`：`window_bucket`,
  `dow_aligned`, `holiday_aligned`, `holiday_and_dow_aligned`。
- 时间对齐复用现有 `align_calendar_frames` 与 `_align_and_compute`。
- v1 实现路径（简单版）：
  1. 把两端 dataframe 各自压成 "panel = 时间桶 × segment key tuple" 的长表。
  2. 时间对齐：复用 time_series 已有的 calendar align 逻辑，**按 segment
     key tuple 分组**调用 — 每个 segment group 内部独立做 time alignment。
  3. 再按 `(bucket_start, <dim columns>)` outer join 两端结果，得到
     panel delta。
- 输出列：`<dim1>, ..., bucket_start, current, baseline, delta, pct_change`。
- Metadata：保留现有 `calendar_info` 行为（与 time_series 一致），新增
  `segment_count`, `a_only_segments_count`, `b_only_segments_count`。

### `DeltaFrameMeta.axes`

- `axes` 中复制 input frame 的 time axis 与 dimension axes 条目（保持与
  observe 输出一致），不引入新字段。
- `semantic_kind` 反映 input shape (`segmented` 或 `panel`)。

### 路径分支决策表

| Inputs `semantic_kind` | AlignmentPolicy | 走哪条 |
| ---------------------- | --------------- | ------ |
| `scalar` / `time_series` | `window_bucket` | 现行 `_align_and_compute` |
| `time_series` | 其它 calendar kind | 现行 calendar align 分支 |
| `segmented` | `window_bucket` | 新 `_align_segmented` |
| `segmented` | 其它 | 抛 `AlignmentPolicyNotApplicableError` |
| `panel` | 任意 4 种 kind | 新 `_align_panel`（内部按 segment group 调用 calendar align） |

### `compare` 错误

| Error | 触发 |
| ----- | ---- |
| `PanelGrainMismatchError` | panel 两端 grain 不一致 |
| `SegmentDimensionMismatchError` | segmented/panel 两端 dimension 列集不同 |
| `AlignmentPolicyNotApplicableError` | segmented + 非 `window_bucket` |
| 现有 `AlignmentFailedError` | 对齐结果为空、calendar timezone 不匹配等（沿用） |

## `decompose`: panel per-bucket attribution

### 前置 type check（更新）

- 取消 "panel → 抛错" 这条硬拒绝。
- 当 `frame.meta.semantic_kind == "panel"`，`axis.id` 必须在
  `frame.meta.axes` 中以 `role="dimension"` 出现，否则
  `AxisNotInPanelDimensionsError`，details 列出可用 dimension 列。

### Flat 路径（不变）

`scalar` / `time_series` / `segmented` 走当前实现：

- `group_by(axis.id).sum(value)` → `contribution`。
- `pct_contribution = contribution / sum(contribution)`（分母为 0 时
  NaN）。
- `rank` 按 `|contribution|` 降序，全局 rank。

### Panel 路径

输入列（来自 panel `DeltaFrame`）：
`<dim1>, ..., bucket_start, current, baseline, delta, pct_change`。

1. 从 `frame.meta.axes` 中取 `role == "time"` 的 `column`（默认
   `bucket_start`）作为 bucket 列。
2. `group_by(bucket_column, axis.id).sum(value)` → `contribution`。
3. 在每个 `bucket_column` group 内：
   - `pct_contribution = contribution / sum(contribution within bucket)`
     （分母为 0 时 NaN）。
   - `rank` 按 `|contribution|` 降序，**bucket-local**（不跨桶比较）。
4. 输出排序：`bucket_column, rank`。

输出列：`<bucket_column>, <axis.id>, contribution, pct_contribution, rank`。

### `AttributionFrameMeta`

- `semantic_kind = "panel"`（schema 当前已允许）。
- `driver_field = axis.id`。
- `value_column = "delta"`（与 flat 一致）。
- `contribution_column = "contribution"`。
- `method = "sum"`。
- 不引入新的 meta 字段；`bucket_column` 写入 `params` 字段以保留
  provenance，避免 schema migration。

### 多维 panel 上的 axis 选择

若输入 panel frame 有 N≥2 个 dimensions，`decompose(axis=dim_k)` 在
`(bucket_column, dim_k)` 维度上聚合，其他 dimensions 被加总掉。这是
single-axis decompose 的标准语义，符合 spec
"核心 decompose 只接受单一 semantic axis"。多轴联合归因属于 domain
composite，不在本次范围。

### `decompose` 错误

| Error | 触发 |
| ----- | ---- |
| `AxisNotInPanelDimensionsError` | panel 输入 + axis.id 不是 frame dimension |
| 现有 `SemanticKindMismatchError` | 非 DeltaFrame / 非 DimensionRef 等（沿用） |

## 错误类型 summary

下列错误类新增到 `marivo/analysis_py/errors.py`，沿用现有 `AnalysisError`
模板（`kind / message / hint / details` 字段，并通过
`_template_fields()` 渲染）：

| Error | 父类 |
| ----- | ---- |
| `DimensionFieldNotFoundError` | `SemanticKindMismatchError` |
| `AmbiguousDimensionError` | `SemanticKindMismatchError` |
| `DimensionAcrossDatasetsError` | `SemanticKindMismatchError` |
| `PanelGrainMismatchError` | `AlignmentFailedError` |
| `SegmentDimensionMismatchError` | `AlignmentFailedError` |
| `AlignmentPolicyNotApplicableError` | `AlignmentFailedError` |
| `AxisNotInPanelDimensionsError` | `SemanticKindMismatchError` |

每个错误的 `hint` 必须给出最小可粘贴 snippet（例如
"使用 `dimensions=[DimensionRef('country')]` 后再 observe"）。

## Typing 不变量

- 所有新参数为 typed：`dimensions: list[DimensionRef] | None`。
- 不允许传裸字符串作为 dimension。
- 不引入新的 `dict[str, Any]` / 隐式 `Any` / 广义 `cast`。
- 不修改任何 frame meta 的 pydantic 字段；新增的 axis / segment 信息全部
  通过现有 `axes` 字典 string key 或 `params` 字段表达。

## Testing

复用
[`.agents/skills/marivo-test-fixtures/SKILL.md`](../../.agents/skills/marivo-test-fixtures/SKILL.md)
中的 session-scoped 模板。如果当前 seeded fixture 不包含足够的非时间
field 用作 dimension，则同步扩展 fixture（不在本 spec 详尽枚举字段名）。

新增测试文件：

- `tests/test_py_observe_segmented_panel.py`
- `tests/test_py_compare_segmented_panel.py`
- `tests/test_py_decompose_panel.py`

测试矩阵概要：

`observe`:

- 4 种 shape 组合各 1 个 happy-path test（scalar / time_series 是 regression）。
- `DimensionFieldNotFoundError` / `AmbiguousDimensionError` /
  `DimensionAcrossDatasetsError` 各 1 个负例。
- `dimensions=[]` 触发 `SemanticKindMismatchError`。
- panel + multi-dataset metric → `MetricShapeUnsupportedError`。

`compare`:

- `segmented × segmented` happy path（outer join，包含一边缺失 segment）。
- `panel × panel` happy path（`window_bucket`）。
- `panel × panel` + `dow_aligned`、`holiday_and_dow_aligned`。
- `PanelGrainMismatchError`, `SegmentDimensionMismatchError`,
  `AlignmentPolicyNotApplicableError` 各 1 个负例。
- `scalar` / `time_series` 现有 happy path 与 `CrossSessionFrameError`
  保持通过（regression）。

`decompose`:

- panel happy path：bucket-local rank、pct 在 bucket 内归一。
- panel + 多 dimension：sum away 其他 dimension，结果维度只剩
  `(bucket_column, axis.id)`。
- `AxisNotInPanelDimensionsError`。
- 现有 flat 路径 happy path 保持通过（regression）。

## Skill examples 同步

按 [agent-guide.md](../../agent-guide.md) "Skill examples 是 SDK 契约"
规则，本 spec 落地时必须同步更新
`marivo-skill/marivo-py-analysis/references/examples/`：

新增 / 更新文件：

- `observe_segmented.py`
- `observe_panel.py`
- `compare_segmented.py`
- `compare_panel.py`
- `decompose_panel.py`

`make examples-check`（已挂在 `make check` 上）作为 gate。
`marivo-py-analysis/SKILL.md` 保持 ≤600 行；若新 example 名字需要进入
SKILL 索引，按现有风格添加一行链接即可。

## 与既有 spec 的关系

- 建立在
  `docs/superpowers/specs/2026-05-25-analysis-py-core-operator-alignment-design.md`
  之上，不改变 v1 alignment 的目标态契约。
- 不覆盖
  `docs/superpowers/specs/2026-05-21-compare-panel-delta-frame-design.md` 与
  `docs/superpowers/specs/2026-05-21-observe-panel-metric-frame-unification-design.md`
  中的 frame-level meta 设计；本 spec 只补齐运行时执行路径。
- 不涉及 `marivo.runtime` / `marivo.adapters`，遵守
  `analysis_py-independence` import-linter 契约。

## 非目标

- 不在本次 spec 引入 `transform` 算子层。
- 不在本次扩展 `AttributionFrame` 的 schema（不新增 `attribution_shape`
  字段；panel 信息通过 `semantic_kind` + `params.bucket_column` 表达）。
- 不在本次给 `discover` / `correlate` 新增能力；它们的 panel 兼容性单独
  spec 处理。
- 不引入 cross-axis attribution；多轴联合归因仍属 domain composite。
