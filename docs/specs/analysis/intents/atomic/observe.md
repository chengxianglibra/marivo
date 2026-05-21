# Observe Intent

状态：current。

`observe` 产出 observation artifact，支持 4 种 observation_type：
`scalar`、`time_series`、`segmented`、`panel`。

## Observation 类型

| 类型 | granularity | dimensions | 产出格式 |
|------|-------------|------------|----------|
| scalar | 未提供 | 未提供 | 单个聚合值 |
| time_series | 提供 | 未提供 | 按 granularity 分桶的时间序列 |
| segmented | 未提供 | 提供 | 按维度拆分的分段值 |
| panel | 提供 | 提供 | 按 granularity × dimensions 交叉的矩阵 |

panel 模式允许同时提供 granularity 和 dimensions，产出维度拆分下的时间序列面板。

## Artifact 格式

所有 observation artifact 使用 schema_version `"2.0"` 的统一 axes+series 格式：

- `axes`：描述 series 的结构维度（时间轴 + 维度轴），空列表表示 scalar
- `series`：统一的数据容器，每个元素包含 `keys`（维度键值映射）和 `points`（数据点列表）
- scalar 值通过 `series[0]["points"][0]["value"]` 访问（不再是顶层 `value`）
- time_series 数据通过 `series[0]["points"]` 访问（不再是顶层平铺 `series` 列表）
- segmented 数据通过 `series` 各元素的 `keys` + `points` 访问（不再有顶层 `segments`）

旧顶层字段 `granularity`、`dimensions`、`scope_value` 已移除；这些信息现由 `axes` 和 `series` 结构表达。

## 通用约束

scalar observe 表达整个 `time_scope` 半开区间上的单个 metric 值。runtime 必须保留 scalar 请求中的精确 date/datetime 边界；sub-day datetime 窗口只能在所选 time field 支持小时级或 timestamp 语义时执行。

time_series、segmented 和 panel observe 的执行查询使用固定内部行数上限 1000；该 intent 不暴露公开 `limit` 参数。

`observe` 不接受 calendar alignment 控制参数。日期对齐由下游 `compare.compare_type` 决定。

如果请求包含旧 calendar policy 字段，runtime 返回 invalid argument，并提示使用 `compare.compare_type`。

Observation artifact 不冻结 calendar pairing summary；需要审计对齐结果时查看 compare artifact metadata。
