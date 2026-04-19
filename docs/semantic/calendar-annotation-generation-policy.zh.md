# Calendar Annotation Generation Policy v1

状态：accepted design note。本文冻结 `calendar data` 在 v1 的 holiday / event annotation 生成规则，覆盖 `holiday_group_id`、`year_relative_holiday_key`、`event_group_id`、`year_relative_event_key` 的编码、边界与最小样例。

配套文档：

- `docs/semantic/calendar-data-contract.zh.md`
- `docs/semantic/calendar-data-v1-source-note.zh.md`
- `docs/semantic/calendar-version-freeze-policy.zh.md`
- `docs/semantic/calendar-alignment-policy-v1-scope-note.zh.md`
- `docs/semantic/calendar-annotation-failure-policy.zh.md`

## 1. Purpose

本文回答的是：

- holiday / event annotation 应如何从治理 source 生成稳定字段
- 相对位置 key 应如何编码，才能跨年份、跨活动批次稳定配对
- 哪些节假日 / 活动窗口在 v1 必须被稳定标注

本文不定义：

- resolver 的 pairing 顺序
- annotation 缺失时的 failure / fallback 语义
- source 发布与 version 冻结流程

## 2. v1 生成原则

v1 固定采用“cluster ID + relative day key”双层标注：

- `holiday_group_id` / `event_group_id` 负责表达“这是哪个稳定窗口”
- `year_relative_holiday_key` / `year_relative_event_key` 负责表达“这是该窗口中的第几天”

生成规则必须满足：

- 同一窗口内每个自然日只有一个稳定 relative key
- relative key 不依赖自然年内绝对日期
- relative key 不使用面向人读的文案作为 join key
- annotation 生成必须是离线、确定性、可重放的

## 3. Holiday Annotation Rules

### 3.1 `holiday_group_id`

`holiday_group_id` 表示稳定节假日簇 ID。

v1 命名规则：

- 使用稳定 snake_case 英文 ID
- 不包含年份
- 不包含具体日期
- 不把“调休”“补班”编码进 group ID 本身

v1 最小支持集合：

- `spring_festival`
- `qingming`
- `national_day`

可接受的后续扩展示例：

- `labor_day`
- `dragon_boat`
- `mid_autumn`

不接受的示例：

- `2026_spring_festival`
- `qingming_0404`
- `国庆节`

### 3.2 `year_relative_holiday_key`

`year_relative_holiday_key` 表示相对节假日簇 anchor day 的稳定位置。

v1 编码规则：

- 格式固定为 `<holiday_group_id>_d[+|-]<offset>`
- anchor day 使用 `d+0`
- anchor 前一天使用 `d-1`
- anchor 后一天使用 `d+1`

示例：

- `spring_festival_d-1`
- `spring_festival_d+0`
- `spring_festival_d+6`
- `qingming_d+0`
- `national_day_d+4`

### 3.3 Holiday Anchor 选择规则

holiday relative key 的 offset 必须围绕单一 anchor day 生成。

v1 anchor 规则：

- 对法定节假日簇，anchor 取该 holiday cluster 的首个放假自然日
- 调休补班日不进入 holiday relative key 序列
- 节前 / 节后扩展窗口若被 source 显式纳入 holiday cluster，则继续沿同一 anchor 计算 offset

这意味着：

- 春节假期首日为 `spring_festival_d+0`
- 假期第二天为 `spring_festival_d+1`
- 若治理 source 还标注节前一天属于春节窗口，则该日为 `spring_festival_d-1`

### 3.4 Holiday Window Membership

某自然日只有在被治理 source 明确纳入 holiday cluster 时，才能写入：

- `holiday_group_id`
- `year_relative_holiday_key`

否则这两个字段必须为 `null`。

v1 不允许：

- 根据“离清明很近”这类启发式临场猜一个 holiday key
- 仅凭 `holiday_name` 文案推导 cluster membership
- 将补班周末写成 holiday relative key 的一部分

## 4. Event Annotation Rules

### 4.1 `event_group_id`

`event_group_id` 表示稳定业务活动窗口 ID。

v1 命名规则：

- 使用治理过的 stable ID
- 不使用一次性活动标题
- 是否包含周期粒度由 source owner 决定，但同一 ID 的语义必须稳定

可接受示例：

- `618_promo`
- `member_day`

不接受示例：

- `2026年618大促第一波`
- `本月会员日`
- `618_banner_campaign`

### 4.2 `year_relative_event_key`

`year_relative_event_key` 表示活动窗口内部的相对位置。

v1 编码规则：

- 格式固定为 `<event_group_id>_d[+|-]<offset>`
- anchor day 使用 `d+0`
- 同一活动窗口内连续自然日按日偏移递增

示例：

- `618_promo_d-3`
- `618_promo_d+0`
- `618_promo_d+5`
- `member_day_d+0`
- `member_day_d+1`

### 4.3 Event Anchor 选择规则

event relative key 的 offset 必须围绕活动窗口 anchor day 生成。

v1 anchor 规则：

- 默认取活动正式开始日为 `d+0`
- 若业务治理约定存在预热期且预热期属于同一稳定窗口，则预热期可落在 `d-1`、`d-2` 等负 offset
- 预热期是否并入同一窗口，必须由 source owner 在治理表中显式定义；resolver 不得临场扩窗

### 4.4 Event Window Membership

event annotation 只来自业务治理活动表的显式日粒度展开结果。

若某自然日未被显式纳入活动窗口，则：

- `event_group_id = null`
- `year_relative_event_key = null`

v1 不允许：

- 从营销文案或 prompt 里临时解析活动日期
- 把同一天同时写成多套 event annotation
- 同一 `event_group_id` 在同一 `calendar_version` 下指向多种互斥窗口定义

## 5. Collision And Uniqueness Rules

annotation 生成必须满足以下唯一性约束：

- 同一 `region_code + calendar_version + calendar_date` 只有一行逻辑记录
- 同一 `holiday_group_id + year_relative_holiday_key` 在同一 `region_code + calendar_version` 下只能映射到一个自然日
- 同一 `event_group_id + year_relative_event_key` 在同一 `region_code + calendar_version` 下只能映射到一个自然日

若 source 侧发现冲突，必须在发布前修复；v1 不允许通过 runtime 选择器临场决策。

## 6. Minimum v1 Coverage Examples

v1 至少需要能为以下窗口生成稳定 annotation：

### 春节

示例：

- `2026-02-17 -> holiday_group_id=spring_festival, year_relative_holiday_key=spring_festival_d+0`
- `2026-02-18 -> holiday_group_id=spring_festival, year_relative_holiday_key=spring_festival_d+1`

### 清明

示例：

- `2026-04-04 -> holiday_group_id=qingming, year_relative_holiday_key=qingming_d+0`

### 国庆

示例：

- `2026-10-01 -> holiday_group_id=national_day, year_relative_holiday_key=national_day_d+0`
- `2026-10-05 -> holiday_group_id=national_day, year_relative_holiday_key=national_day_d+4`

### 至少一个活动窗口

以 `618_promo` 为例：

- `2026-06-15 -> event_group_id=618_promo, year_relative_event_key=618_promo_d-3`
- `2026-06-18 -> event_group_id=618_promo, year_relative_event_key=618_promo_d+0`
- `2026-06-20 -> event_group_id=618_promo, year_relative_event_key=618_promo_d+2`

这些日期仅为编码示意；生产数据以治理 source 发布的实际窗口为准。

## 7. Offline Build Responsibilities

annotation 生成应由 source-side offline job 或等价的确定性构建流程负责。

最小流程：

1. 读取权威节假日 source 或治理活动 source
2. 将窗口展开到日粒度
3. 生成稳定 `group_id`
4. 基于 anchor 生成 relative key
5. 执行唯一性校验后写入 snapshot

resolver 的职责只是消费 annotation，不负责生成 annotation。

## 8. Interface To Later Tasks

本文为以下任务提供冻结前提：

- 任务 2.5：annotation 缺失时的失败策略
- 任务 4.4：ordered matching strategy 执行器
- 任务 7.2 / 7.3：holiday / event pairing 成功与失败路径测试

如果后续实现发现现有 source 无法按本文规则稳定生成 annotation，应优先修正 offline build 规则或 source 治理表，而不是在 runtime 引入猜测逻辑。
