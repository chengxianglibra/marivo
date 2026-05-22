# Test Intent

状态：current。

`test` 的 canonical 输入是两个 `sample_frame` artifact 引用：

- `current_sample_artifact_id`
- `baseline_sample_artifact_id`
- `hypothesis`

`test` 只消费 test-ready 样本摘要，不直接读取 semantic metric，也不接收 `grain`。
`test` 不在 intent 内部生成 sample summary；上游应通过 `sample_summary` transform
先把 `metric_frame` 准备为 `sample_frame`，再交由 `test` 执行假设检验。

`test` 复用 compare/test artifact 中已经形成的结论，不重新解释日历对齐规则。

Calendar alignment 的控制面是 `compare.compare_type`；公司活动 event alignment 不再受支持。
