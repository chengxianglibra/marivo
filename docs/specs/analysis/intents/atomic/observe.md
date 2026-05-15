# Observe Intent

状态：current。

`observe` 只负责产出 observation artifact：scalar、time_series 或 segmented。

`observe` 不接受 calendar alignment 控制参数。日期对齐由下游 `compare.compare_type` 决定。

如果请求包含旧 calendar policy 字段，runtime 返回 invalid argument，并提示使用 `compare.compare_type`。

Observation artifact 不冻结 calendar pairing summary；需要审计对齐结果时查看 compare artifact metadata。
