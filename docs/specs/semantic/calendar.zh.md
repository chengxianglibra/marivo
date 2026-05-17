# Calendar Alignment 与 Calendar Data

状态：current。

AOI 的日历对齐入口已收敛为 `compare.compare_type`。`observe`、derived intent 内部输入、compiler request metadata 不再接受独立 calendar policy 用户输入。

## Compare Type 与对齐策略

`compare_type` 支持的值以 AOI schema enum 为准：

| compare_type | baseline window | bucket matching | calendar data |
| --- | --- | --- | --- |
| `normal` | 使用右侧 observe artifact 的窗口 | 左右窗口内 bucket 相对位置 | 不读取 |
| `weekday_aligned` | 使用右侧 observe artifact 的窗口 | 相同 weekday 最近匹配，失败后相对位置 | 不读取 |
| `holiday_aligned` | 使用右侧 observe artifact 的窗口 | holiday group / relative key，失败后相对位置 | 读取 |
| `holiday_and_weekday_aligned` | 使用右侧 observe artifact 的窗口 | holiday group / relative key，失败后 weekday，再相对位置 | 读取 |

`compare_type` 只表达对齐策略，不表达同比、环比或周环比；这些时间关系由左右 observe artifact 的 `time_scope` 决定。

非 `normal` `compare_type` 仅支持 time-series compare。scalar/segmented compare 使用非 `normal` 时必须返回 invalid argument。

### Golden Cases

| case_id | compare_type | expected behavior |
| --- | --- | --- |
| `normal_relative_position` | `normal` | 左第 N 个 bucket 对右第 N 个 bucket，不读取 calendar data |
| `weekday_alignment` | `weekday_aligned` | 右侧窗口内相同 weekday 最近匹配，失败后相对位置 |
| `holiday_alignment` | `holiday_aligned` | 读取 calendar data，优先 holiday group / relative key，失败后相对位置 |
| `holiday_weekday_alignment` | `holiday_and_weekday_aligned` | 读取 calendar data，优先 holiday group / relative key，再 weekday，再相对位置 |

所有非 `normal` case 都只适用于 time-series compare。

## Calendar Data 契约

Calendar data 只服务包含 holiday 的 compare_type：`holiday_aligned` 和 `holiday_and_weekday_aligned`。`normal` 和 weekday 对齐不要求外部 calendar rows。

Calendar data 是稀疏 holiday/exception 数据，不存每天一行。周几和周末标记由 `calendar_date` 在运行时程序化推断。

### Required Columns

| field | type | required | note |
| --- | --- | --- | --- |
| `calendar_date` | date/string | yes | 自然日期 |
| `day_kind` | string | yes | `holiday` 或 `adjusted_workday` |
| `holiday_group_id` | string | yes | 稳定节假日窗口 ID；调休工作日可为空字符串 |
| `holiday_name` | string/null | no | 节假日名称 |
| `year_relative_holiday_key` | string/null | no | 节假日窗口内相对位置 |

公司活动 event 字段不属于 calendar data contract。

### Resolution

Holiday 对齐读取 current window 和右侧 artifact baseline window 内的稀疏 holiday rows。缺失的普通日期会在运行时补齐为 weekday-only annotation row。

### 数据加载

Calendar data 通过 HTTP `PUT /calendar/data` 加载。该入口使用替换语义：
先删除现有 calendar 行，再批量写入请求中的 rows。

加载时校验 `day_kind` 只能是 `holiday` 或 `adjusted_workday`。仓库不再维护内置 CN calendar 生成脚本；调用方负责提供 holiday/exception rows。

## Calendar Annotation 生成

Calendar annotation 只生成 holiday 相关字段：

- `holiday_group_id`：稳定节假日窗口 ID
- `year_relative_holiday_key`：该窗口内的相对日期

生成器不得输出公司活动 event annotation。业务活动窗口不参与 calendar alignment。

`holiday_group_id` 表达稳定节假日窗口；`year_relative_holiday_key` 表达该窗口内的相对日期。相同 `holiday_group_id + year_relative_holiday_key` 应唯一映射到一个自然日。

## 故障处理

只有 `holiday_aligned` 和 `holiday_and_weekday_aligned` 需要 calendar data。

| 场景 | 处理 |
| --- | --- |
| calendar reader 未配置 | `compare: INVALID_ARGUMENT` |
| holiday key 无法匹配 | `holiday_aligned` 尝试相对位置 fallback；`holiday_and_weekday_aligned` 先尝试 weekday fallback，再尝试相对位置 fallback |
| fallback 被使用 | compare metadata 保留 pairing reason 与 warning |

公司活动 event annotation、event matcher、event warning code 不再存在。

## Bucket Pairing

Bucket pairing 不作为独立 artifact 暴露，也不再冻结在 observation artifact 中。

Runtime 在执行 `compare` 时根据 `compare_type` 即时生成 pairing，并写入 compare artifact metadata。调用方通过 compare artifact 审计最终对齐结果。

## Runtime Metadata

compare artifact 的 implementation metadata 记录所选 `compare_type`、对齐模式、bucket pairing，以及 holiday 模式下读取到的 calendar source lineage。上游 observation 不再冻结可复用的 calendar policy summary。
