# Calendar Alignment Policy

状态：current。

AOI 的日历对齐入口已经收敛为 `compare.compare_type`。`observe`、derived intent 内部输入、compiler request metadata 不再接受独立 calendar policy 用户输入。

## Public Control

`compare_type` 支持的值以 AOI schema enum 为准：

| compare_type | baseline window | bucket matching | calendar data |
| --- | --- | --- | --- |
| `normal` | 无额外 baseline 生成 | 现有 observed bucket 交集 | 不读取 |
| `yoy` | 当前窗口自然平移到上一年 | 自然日期 | 不读取 |
| `mom` | 当前窗口前一个等长周期 | 自然日期 | 不读取 |
| `wow` | 当前窗口前 7 天 | 相同 weekday | 不读取 |
| `weekday_aligned_yoy` | 当前窗口自然平移到上一年 | 相同 weekday 最近匹配，失败后自然日期 | 不读取 |
| `weekday_aligned_mom` | 当前窗口前一个等长周期 | 相同 weekday 最近匹配，失败后自然日期 | 不读取 |
| `holiday_aligned_yoy` | 当前窗口自然平移到上一年 | holiday group / relative key，失败后 weekday，再自然日期 | 读取 |

非 `normal` `compare_type` 仅支持 time-series compare。scalar/segmented compare 使用非 `normal` 时必须返回 invalid argument。

## Calendar Data

Calendar data 只为 `holiday_aligned_yoy` 提供 holiday annotation：

- `holiday_group_id`
- `year_relative_holiday_key`

公司活动 event annotation 不再是 calendar data 或 calendar policy 的一部分。

## Runtime Metadata

compare artifact 的 implementation metadata 记录所选 `compare_type`、对齐模式、bucket pairing，以及 holiday 模式下解析到的 calendar source/version。上游 observation 不再冻结可复用的 calendar policy summary。
