# Calendar Data Contract

状态：current。

Calendar data 只服务 `compare.compare_type = "holiday_aligned_yoy"`。普通同比、环比、周环比和 weekday 对齐不要求外部 calendar rows。

## Required Columns

| field | type | required | note |
| --- | --- | --- | --- |
| `calendar_date` | date/string | yes | 自然日期 |
| `region_code` | string | yes | calendar region |
| `calendar_version` | string | yes | frozen calendar version |
| `weekday` | integer | yes | 1-7 |
| `is_weekend` | boolean | yes | weekend marker |
| `is_workday` | boolean | yes | workday marker |
| `holiday_group_id` | string/null | no | 稳定节假日窗口 ID |
| `year_relative_holiday_key` | string/null | no | 节假日窗口内相对位置 |

公司活动 event 字段不属于 calendar data contract。

## Resolution

`holiday_aligned_yoy` 读取覆盖 current window 和 expected baseline window 的 dense calendar rows。若配置缺失或数据不完整，compare 返回 blocking failure，而不是回退到隐式 policy。
