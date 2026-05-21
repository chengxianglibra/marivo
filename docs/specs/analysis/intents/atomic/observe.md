# Observe Intent

状态：current。

`observe` 成功时产出顶层 `metric_frame` artifact。artifact 使用
`artifact_family = "metric_frame"` 与 `shape` 区分 4 种形态：
`scalar`、`time_series`、`segmented`、`panel`。

## Metric Frame Shape

| shape | granularity | dimensions | 产出格式 |
|------|-------------|------------|----------|
| scalar | 未提供 | 未提供 | 单个聚合值 |
| time_series | 提供 | 未提供 | 按 granularity 分桶的时间序列 |
| segmented | 未提供 | 提供 | 按维度拆分的分段值 |
| panel | 提供 | 提供 | 按 granularity × dimensions 交叉的矩阵 |

panel 模式允许同时提供 granularity 和 dimensions，产出维度拆分下的时间序列面板。

## Artifact 格式

所有成功 observe artifact 使用顶层 `metric_frame` 合约：

- `artifact_id`：artifact 标识
- `artifact_family`：固定为 `"metric_frame"`
- `shape`：`scalar | time_series | segmented | panel`
- `subject`：`{ kind: "metric", metric_ref, time_scope, scope }`
- `axes`：描述 series 的结构维度（时间轴 + 维度轴），空列表表示 scalar
- `measures`：当前固定为单一数值 measure `value`
- `payload.series`：统一的数据容器，每个元素包含 `keys`（维度键值映射）和 `points`（数据点列表）

数据读取规则：

- scalar 值通过 `payload.series[0]["points"][0]["value"]` 访问
- time_series 数据通过 `payload.series[0]["points"]` 访问
- segmented 数据通过 `payload.series` 各元素的 `keys` + `points` 访问
- panel 数据通过 `payload.series` 的维度 `keys` 与每个 point 的 `window` 访问

旧顶层字段 `schema_version`、`observation_type`、`metric`、`time_scope`、`scope`、
`unit`、`series`、`granularity`、`dimensions`、`segments`、`analytical_metadata`
和 `execution_metadata` 不属于公开 `metric_frame` artifact。内部质量和执行信号保存在
execution envelope 的 `product_metadata.observe_metadata` 中。

## 通用约束

scalar observe 表达整个 `time_scope` 半开区间上的单个 metric 值。runtime 必须保留 scalar 请求中的精确 date/datetime 边界；sub-day datetime 窗口只能在所选 time field 支持小时级或 timestamp 语义时执行。

time_series、segmented 和 panel observe 的执行查询使用固定内部行数上限 1000；该 intent 不暴露公开 `limit` 参数。

`observe` 不接受 calendar alignment 控制参数。日期对齐由下游 `compare.compare_type` 决定。

如果请求包含旧 calendar policy 字段，runtime 返回 invalid argument，并提示使用 `compare.compare_type`。

Metric frame artifact 不冻结 calendar pairing summary；需要审计对齐结果时查看 compare artifact metadata。
