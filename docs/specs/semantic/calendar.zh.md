# Calendar Alignment 与 Calendar Data

状态：current。

AOI 的日历对齐入口已收敛为 `compare.compare_type`。`observe`、derived intent 内部输入、compiler request metadata 不再接受独立 calendar policy 用户输入。

## Compare Type 与对齐策略

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

### Golden Cases

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

## Calendar Data 契约

Calendar data 只服务 `compare.compare_type = "holiday_aligned_yoy"`。普通同比、环比、周环比和 weekday 对齐不要求外部 calendar rows。

### Required Columns

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

### Resolution

`holiday_aligned_yoy` 读取覆盖 current window 和 expected baseline window 的 dense calendar rows。若配置缺失或数据不完整，compare 返回 blocking failure，而不是回退到隐式 policy。

### 数据加载

Calendar data 通过以下入口加载：

- HTTP `POST /calendar/data`：批量写入 calendar 行，同一 `calendar_version` 重复写入返回 409
- CLI `marivo calendar load <file.csv> --version <version>`：从 CSV 文件加载，校验必填列与取值范围

加载时校验 `weekday` 在 1-7 范围内，`is_weekend` / `is_workday` 为 0 或 1。

## Calendar Annotation 生成

Calendar annotation 只生成 holiday 相关字段：

- `holiday_group_id`：稳定节假日窗口 ID
- `year_relative_holiday_key`：该窗口内的相对日期

生成器不得输出公司活动 event annotation。业务活动窗口不参与 calendar alignment。

`holiday_group_id` 表达稳定节假日窗口；`year_relative_holiday_key` 表达该窗口内的相对日期。相同 `region_code + calendar_version + holiday_group_id + year_relative_holiday_key` 应唯一映射到一个自然日。

## 故障处理

只有 `holiday_aligned_yoy` 需要 calendar data。

| 场景 | 处理 |
| --- | --- |
| calendar reader 未配置 | `compare: INVALID_ARGUMENT` |
| calendar data 不覆盖 current/baseline window | `compare: INVALID_ARGUMENT` |
| holiday key 无法匹配 | 尝试 weekday fallback，再尝试自然日期 fallback |
| fallback 被使用 | compare metadata 保留 pairing reason 与 warning |

公司活动 event annotation、event matcher、event warning code 不再存在。

## 版本冻结规则

calendar version 采用 "published snapshot only" 语义：

- resolver 只能消费已发布的 calendar snapshot
- 每次执行都必须冻结明确的 `resolved_calendar_source` 和 `resolved_calendar_version`
- 不允许使用 "latest" / "current" 或其他随时间漂移的动态版本语义
- 配置校验会拒绝 `calendar_version = "latest"` 或 `"current"`
- 已发布 snapshot 不可覆写；修正数据必须发布新版本
- 已冻结到 artifact lineage 的旧版本必须可读取、可审计、可重放
- 给定相同请求输入、相同 source/version 组合，必须可重放出同一 baseline window 与 bucket pairing

版本解析规则：

- 若配置指定了 `calendar_version`，直接使用该版本
- 若未指定，使用 metadata store 中 `region_code` 匹配的最大版本
- resolved version 写入 compare artifact metadata

## Bucket Pairing

Bucket pairing 不作为独立 artifact 暴露，也不再冻结在 observation artifact 中。

Runtime 在执行 `compare` 时根据 `compare_type` 即时生成 pairing，并写入 compare artifact metadata。调用方通过 compare artifact 审计最终对齐结果。

## Runtime Metadata

compare artifact 的 implementation metadata 记录所选 `compare_type`、对齐模式、bucket pairing，以及 holiday 模式下解析到的 calendar source/version。上游 observation 不再冻结可复用的 calendar policy summary。
