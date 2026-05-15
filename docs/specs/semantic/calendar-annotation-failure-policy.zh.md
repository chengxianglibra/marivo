# Calendar Annotation Failure Policy

状态：current。

`compare.compare_type` 决定 calendar alignment 行为。只有 `holiday_aligned_yoy` 需要 calendar data。

Failure handling:

- calendar reader 未配置：`compare: INVALID_ARGUMENT`
- calendar data 不覆盖 current/baseline window：`compare: INVALID_ARGUMENT`
- holiday key 无法匹配：尝试 weekday fallback，再尝试自然日期 fallback
- fallback 被使用时，compare metadata 保留 pairing reason 与 warning

公司活动 event annotation、event matcher、event warning code 不再存在。
