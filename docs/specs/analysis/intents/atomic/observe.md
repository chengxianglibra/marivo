# Observe Intent

状态：current。

`observe` 只负责产出 observation artifact：scalar、time_series 或 segmented。

scalar observe 表达整个 `time_scope` 半开区间上的单个 metric 值，不带公开
`granularity`。runtime 必须保留 scalar 请求中的精确 date/datetime 边界；sub-day
datetime 窗口只能在所选 time field 支持小时级或 timestamp 语义时执行。调用方只有在
需要 time_series buckets 时才提供 `granularity`。

time_series 和 segmented observe 的执行查询使用固定内部行数上限 1000；该 intent 不暴露公开 `limit` 参数。

`observe` 不接受 calendar alignment 控制参数。日期对齐由下游 `compare.compare_type` 决定。

如果请求包含旧 calendar policy 字段，runtime 返回 invalid argument，并提示使用 `compare.compare_type`。

Observation artifact 不冻结 calendar pairing summary；需要审计对齐结果时查看 compare artifact metadata。
