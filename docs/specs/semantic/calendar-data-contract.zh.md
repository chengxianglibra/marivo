# Calendar Data Logical Input Contract

> **Superseded** by `docs/superpowers/specs/2026-04-29-calendar-data-policy-redesign-design.md`.
> This document describes the pre-redesign architecture and is kept for historical reference.

状态：accepted design note。本文冻结 `calendar alignment policy` 在 v1 所依赖的 `calendar data` 逻辑输入契约；它定义 compiler / resolver 读取的稳定字段与约束，不定义新的 top-level semantic object family。

配套文档：

- `specs/semantic/calendar-alignment-policy.zh.md`
- `specs/semantic/calendar-alignment-policy-v1-scope-note.zh.md`
- `specs/semantic/calendar-data-v1-source-note.zh.md`
- `specs/semantic/calendar-version-freeze-policy.zh.md`
- `specs/semantic/calendar-annotation-generation-policy.zh.md`
- `specs/semantic/calendar-annotation-failure-policy.zh.md`

## Purpose

`calendar data` 回答的是“某个自然日具备哪些受治理日历注释”，而不是“本次分析应该选哪条 policy”。

它为 compiler / resolver 提供稳定输入，用于：

- holiday / weekday / event 对齐
- baseline window 生成后的 bucket pairing
- coverage 与 comparability diagnostics

它不负责：

- 选择 `calendar_policy_ref`
- 定义 baseline generation rule
- 代替 binding 读取物理时间列
- 在 runtime 临时抓取公告或 holiday API

## v1 契约结论

v1 的 `calendar data` 保持为 compiler-owned logical input contract，而不是新的公开 object family。

这意味着：

- compiler / resolver 只依赖稳定字段集与约束
- source 可以是平台内置表或同步后的治理表，但对 runtime 暴露为同一逻辑 contract
- 同一请求一旦冻结 `resolved_calendar_source + resolved_calendar_version`，就必须可重放出同一 pairing 结果

## Minimal Logical Schema

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `calendar_date` | date | yes | 自然日 |
| `region_code` | string | yes | 地区代码；v1 至少支持 `CN` |
| `calendar_version` | string | yes | 版本锚点；同一请求不得使用动态 latest |
| `is_weekend` | bool | yes | 是否周末 |
| `is_workday` | bool | yes | 是否工作日；允许补班周末为 `true` |
| `holiday_name` | string \| null | no | 节假日名称；无节假日时为 `null` |
| `holiday_group_id` | string \| null | no | 节假日簇 ID，如 `spring_festival`、`qingming` |
| `weekday` | integer | yes | 固定值域 `1-7`，其中 `1=周一`，`7=周日` |
| `year_relative_holiday_key` | string \| null | no | 节假日相对位置，如 `spring_festival_d-1` |
| `event_group_id` | string \| null | no | 业务活动簇 ID，如 `618_promo`、`member_day` |
| `year_relative_event_key` | string \| null | no | 活动相对位置，如 `618_promo_d-1` |

## Key Constraints

### Uniqueness

同一逻辑记录必须满足：

- `region_code + calendar_version + calendar_date` 唯一

若同一日期存在多套业务活动注释，v1 不通过复制多行表达；需要在 source 侧先规约为单一稳定活动窗口注释，或拆到不同 `calendar_version` / source family 再由 resolver 显式选择。

### Weekday Domain

`weekday` 的值域固定为：

- `1 = Monday`
- `2 = Tuesday`
- `3 = Wednesday`
- `4 = Thursday`
- `5 = Friday`
- `6 = Saturday`
- `7 = Sunday`

v1 不接受：

- `0-6`
- 本地化字符串如 `周一`
- engine-specific weekday 编码

所有 source 都必须在进入 resolver 前归一化到上述值域。

### Workday / Weekend Semantics

`is_weekend` 与 `is_workday` 必须独立表达，不能互相推导。

原因：

- 补班周末：`is_weekend = true` 且 `is_workday = true`
- 工作日放假：`is_weekend = false` 且 `is_workday = false`

这两个字段缺一不可；resolver 不得依赖 `weekday in {6,7}` 或 `!is_workday` 来猜补班 / 调休。

### Holiday Annotation

holiday 相关字段的语义如下：

- `holiday_name`：面向人读的标签，不作为稳定 join key
- `holiday_group_id`：稳定 cluster key，供 holiday policy 配对
- `year_relative_holiday_key`：稳定相对位置 key，供节前 / 节中 / 节后 bucket pairing

要求：

- 同一 `holiday_group_id` 在同一 `region_code + calendar_version` 下必须指向单个稳定节假日簇
- `year_relative_holiday_key` 必须能唯一落回对应 holiday cluster 内的一天
- 无 holiday annotation 时，这三个字段允许为 `null`

### Event Annotation

event 相关字段的语义如下：

- `event_group_id`：稳定业务活动窗口 ID
- `year_relative_event_key`：活动窗口内相对位置 key

要求：

- `event_group_id` 必须是治理过的稳定 ID，而不是一次性活动文案
- `year_relative_event_key` 的编码方式必须与 `event_group_id` 绑定，例如 `618_promo_d+2`
- 无活动注释时，这两个字段允许为 `null`

## v1 Resolver Consumption Rules

resolver 在消费 `calendar data` 时必须遵守以下规则：

- 不得读取未冻结版本的 dynamic latest 数据
- 不得在 annotation 缺失时临场猜测 holiday / event 相对位置
- 不得用 `holiday_name` 代替 `holiday_group_id`
- 不得跨 `region_code` 混用 pairing

policy 对缺失 annotation 的处理必须由 fixed policy 语义决定，后续 failure / warning 分层以 scope note 中的 issue taxonomy 为准。

## Non-goals

本文不定义：

- `calendar_policy_ref` 白名单
- resolved alignment plan 输出 schema
- annotation 生成任务的离线实现细节
- source catalog 的 HTTP lifecycle

这些内容分别由 policy 文档、scope note 与 source note 承接。
