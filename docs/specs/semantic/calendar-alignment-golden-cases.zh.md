# Calendar Alignment Golden Cases

状态：current。

| case_id | compare_type | expected behavior |
| --- | --- | --- |
| `normal_intersection` | `normal` | 使用左右 observation 已有 bucket 交集，不读取 calendar data |
| `natural_yoy` | `yoy` | current window 平移上一年，按自然日期配对 |
| `natural_mom` | `mom` | current window 前一个等长周期，按自然日期配对 |
| `weekday_wow` | `wow` | current window 前 7 天，按相同 weekday 配对 |
| `weekday_yoy` | `weekday_aligned_yoy` | 上一年窗口内相同 weekday 最近匹配，失败后自然日期 |
| `weekday_mom` | `weekday_aligned_mom` | 上一周期窗口内相同 weekday 最近匹配，失败后自然日期 |
| `holiday_yoy` | `holiday_aligned_yoy` | 读取 calendar data，优先 holiday group / relative key，再 weekday，再自然日期 |

所有非 `normal` case 都只适用于 time-series compare。
