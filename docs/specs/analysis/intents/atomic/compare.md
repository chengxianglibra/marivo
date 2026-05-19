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

`dimensions: ["log_hour"]` 这类小时分区 observe 仍然是 segmented observation；
`compare_type = "normal"` 可以比较它并产出 `segmented_delta`。它不等价于 hour 级
time-series compare，也不能使用 calendar-aligned compare_type。

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

`time_series_delta` artifact 额外暴露左右两侧 source series 派生出的最小 coverage
事实：

```json
{
  "coverage": {
    "current": {
      "grain": "day",
      "requested_units": 7,
      "covered_units": 6,
      "missing_units": ["2026-05-18"]
    },
    "baseline": {
      "grain": "day",
      "requested_units": 7,
      "covered_units": 7,
      "missing_units": []
    }
  }
}
```

其中 `requested_units = len(series)`，`covered_units` 只统计 `value is not null`
的 bucket，`missing_units` 来自缺失 bucket 的 `window.start`。`value = 0` 是有效
观测值，不视为缺失。若 current/baseline 的相对 coverage 形态不一致（grain、
requested / covered unit 数或缺失 bucket 的相对位置不同），`comparability.status`
变为 `needs_attention` 并追加 `coverage_mismatch` warning；绝对缺失日期不同但相对
缺失位置一致时不单独降级。compare 仍产出结果，不自动改写时间窗口或判断完整周 /
当天是否应排除。
