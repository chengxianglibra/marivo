# Compare Intent

状态：current。

`compare` 比较两个 observation artifact。调用方不得在 `compare` 中重复描述 metric、time scope、scope 或 filter；对齐行为只由 `compare_type` 控制。

## compare_type

| value | supported observation type | behavior |
| --- | --- | --- |
| `normal` | scalar / segmented / time_series | scalar/segmented 按现有值比较；time-series 使用 observed bucket 交集 |
| `yoy` | time_series | 当前窗口自然平移到上一年，按自然日期配对 |
| `mom` | time_series | 当前窗口前一个等长周期，按自然日期配对 |
| `wow` | time_series | 当前窗口前 7 天，按相同 weekday 配对 |
| `weekday_aligned_yoy` | time_series | 上一年窗口内相同 weekday 最近匹配，失败后自然日期 |
| `weekday_aligned_mom` | time_series | 上一周期窗口内相同 weekday 最近匹配，失败后自然日期 |
| `holiday_aligned_yoy` | time_series | 读取 calendar data，优先 holiday group / relative key，再 weekday，再自然日期 |

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
