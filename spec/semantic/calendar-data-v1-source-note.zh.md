# Calendar Data v1 Source Note

> **Superseded** by `docs/superpowers/specs/2026-04-29-calendar-data-policy-redesign-design.md`.
> This document describes the pre-redesign architecture and is kept for historical reference.

状态：accepted source note。本文冻结 `calendar data` 在 v1 的 source 选型、source 分层与 versioning 约束，用于支撑 `calendar alignment policy` 的可重放解析。

配套文档：

- `spec/semantic/calendar-data-contract.zh.md`
- `spec/semantic/calendar-alignment-policy-v1-scope-note.zh.md`
- `spec/semantic/calendar-alignment-policy.zh.md`
- `spec/semantic/calendar-version-freeze-policy.zh.md`
- `spec/semantic/calendar-annotation-generation-policy.zh.md`

## 1. v1 Source 结论

v1 采用“两类治理 source，单一逻辑 contract”的收敛方案：

- `CN` 公共节假日与补班/调休：来自同步后的权威节假日治理表
- 业务活动窗口：来自业务治理活动日历表

对 compiler / resolver 来说，两类 source 都必须先归一化为同一个 `calendar data` 逻辑输入契约；resolver 不直接消费原始外部公告或临时活动配置。

## 1.1 备选方案与取舍

任务 2.2 评估过的 v1 候选 source 如下：

| 候选 | 是否作为 v1 主方案 | 结论 |
| --- | --- | --- |
| 平台内置统一 calendar 表 | no | 可作为未来收敛形态，但不作为 v1 主方案 |
| 同步后的权威节假日治理表 | yes | 作为 `CN` 公共节假日与补班/调休的正式 source |
| 业务治理活动日历表 | yes | 作为业务活动窗口的正式 source |

选择理由：

- `CN` 公共节假日需要权威公告同步、补班/调休表达与可冻结版本；同步治理表最符合这些约束
- 业务活动窗口需要稳定 owner、stable ID 与版本；业务治理活动日历表比平台通用内置表更接近真实治理入口
- v1 先解决“权威节假日”和“治理活动窗口”各自可冻结，再由 resolver 前置装配层统一暴露逻辑 contract；不强行要求 source owner 先物理合表
- 平台内置统一表虽然表面更简洁，但会把不同 owner、不同发布节奏、不同 versioning 责任提前耦合到同一张底表，不利于 v1 快速落地

## 2. 为什么不选运行时 latest / 抓取式方案

v1 明确拒绝以下方案：

- runtime 临时 holiday API
- 运行时网页抓取国务院办公厅或其他公告
- “latest as of now” 式动态版本选择
- 让 agent / LLM 从活动文案临场抽取 event window

原因：

- 这会破坏 observation artifact 的可重放性
- 同一请求无法稳定生成同一 pairing plan
- comparability diagnostics 无法绑定到明确的数据版本

## 3. v1 Source 分层

### 3.1 CN 公共节假日 Source

`CN` 公共节假日 source 必须覆盖：

- 法定节假日名称
- holiday cluster
- 周末与工作日语义
- 补班 / 调休后的实际 `is_workday`
- 节假日相对位置 key

推荐来源：

- 同步后的权威节假日治理表

v1 不要求把该表暴露成新的公开 semantic object family，但要求它是：

- 已同步
- 已落库
- 可版本冻结
- 可被 resolver 以 `resolved_calendar_source + resolved_calendar_version` 引用

### 3.2 Business Event Source

业务活动窗口 source 必须覆盖：

- 活动窗口 stable ID
- 活动窗口内每日相对位置 key
- 与 `calendar_date` 对齐的日粒度展开结果

推荐来源：

- 业务治理活动日历表

不接受：

- ad hoc Excel 导入后未治理的临时表
- runtime prompt 里口头提供的活动日期范围
- 只保留活动名称、不保留 stable ID / version 的松散配置

## 4. 逻辑合并策略

v1 不强制要求“公共节假日表”和“业务活动表”物理上合并成一张底表，但逻辑上必须对 resolver 暴露为同一个输入 contract。

建议的最小实现策略：

1. 公共节假日 source 负责填充 holiday 相关字段与基础 weekday/workday 字段
2. 业务活动 source 负责填充 event 相关字段
3. resolver 前置装配层将两者归并为单一逻辑 `calendar data` 行视图

这样做的原因：

- 节假日与活动窗口的治理 owner 往往不同
- 允许公共节假日和业务活动各自独立发布版本
- 对 compiler / resolver 仍保持单一输入 contract

## 5. Version 冻结规则（摘要）

v1 的 versioning 规则固定如下：

- 每次 resolver 执行都必须绑定明确的 `resolved_calendar_source`
- 每次 resolver 执行都必须绑定明确的 `resolved_calendar_version`
- `resolved_calendar_version` 必须来自已发布、可回放的数据版本
- 不允许使用“当前最新版本”这种无锚点语义

建议的版本边界：

- 公共节假日：例如 `cn_public_holiday_2026_v1`
- 业务活动：例如 `campaign_calendar_2026_q2_v3`
- 若 resolver 使用逻辑装配视图，可在输出中同时记录组合后的 logical version 或 source lineage summary

最低要求是：给定同一请求、同一 source/version 组合，必须可重放出同一 baseline window 与 bucket pairing。

更完整的 version freeze 规则、publish workflow 与 rollback 边界，见 `spec/semantic/calendar-version-freeze-policy.zh.md`。

## 6. v1 选型决策

### 决策 A：CN 公共节假日走权威同步表

结论：

- 接受同步后的权威节假日治理表
- 不接受平台临时抓取或手工复制公告文本

理由：

- 可审计
- 可冻结版本
- 能稳定表达补班 / 调休

### 决策 B：业务活动走治理活动表

结论：

- 接受业务治理活动日历表
- 不接受在 request 中临时提交 event window payload

理由：

- 活动窗口需要 stable ID 和版本
- `event_yoy` / `event_mom` 依赖稳定相对位置注释，不能靠 runtime 猜

### 决策 C：resolver 只消费逻辑 contract，不直接感知原始物理表差异

结论：

- source 侧可以有两张表或多张表
- resolver 入口只能看到统一字段集和明确版本锚点

理由：

- 保持 compiler 侧 contract 简洁
- 让 source owner 的实现差异不泄漏到 policy / comparability contract

## 7. 与后续任务的接口

本文为后续任务提供冻结前提：

- 任务 2.4：holiday / event relative annotation 生成规则
- 任务 2.5：annotation 缺失时的失败策略
- 任务 4.2-4.5：resolver 生成 baseline 与 pairing plan

如果后续实现发现 source 不满足本文约束，应优先修正 source contract 或 versioning，而不是在 runtime 增加临时兜底猜测逻辑。
