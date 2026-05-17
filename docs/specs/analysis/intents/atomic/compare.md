# Compare Intent

状态：current。

`compare` 比较两个 observation artifact。调用方不得在 `compare` 中重复描述 metric、time scope、scope 或 filter；对齐行为只由 `compare_type` 控制。

## compare_type

| value | supported observation type | behavior |
| --- | --- | --- |
| `normal` | scalar / segmented / time_series | scalar/segmented 按现有值比较；time-series 按左右 artifact 窗口内的 bucket 相对位置配对 |
| `weekday_aligned` | time_series | 在右侧 artifact 窗口内找相同 weekday 最近匹配，失败后按相对位置配对 |
| `holiday_aligned` | time_series | 读取 calendar data，优先 holiday group / relative key，失败后按相对位置配对 |
| `holiday_and_weekday_aligned` | time_series | 读取 calendar data，优先 holiday group / relative key，再 weekday，最后按相对位置配对 |

`compare_type` 不表达同比、环比或周环比；这些时间关系由传入的左右 observe artifact 的 `time_scope` 决定。

非 `normal` 值用于 scalar 或 segmented observation 时，runtime 返回：

```text
compare: INVALID_ARGUMENT - compare_type '<value>' requires time_series observations
```

## Metadata

compare artifact metadata 记录：

- selected `compare_type`
- pairing basis/rule
- matched left/right time scope
- bucket pairing
- holiday 模式下解析到的 calendar source/version

右侧 observation 未覆盖的 baseline bucket 以 `right_value = null` 和 coverage issue 表达。
