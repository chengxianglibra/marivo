# Calendar Annotation Generation Policy

状态：current。

Calendar annotation 只生成 holiday 相关字段：

- `holiday_group_id`
- `year_relative_holiday_key`

生成器不得输出公司活动 event annotation。业务活动窗口不参与 calendar alignment。

`holiday_group_id` 表达稳定节假日窗口；`year_relative_holiday_key` 表达该窗口内的相对日期。相同 `region_code + calendar_version + holiday_group_id + year_relative_holiday_key` 应唯一映射到一个自然日。
