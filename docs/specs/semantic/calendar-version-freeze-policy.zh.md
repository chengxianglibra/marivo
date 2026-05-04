# Calendar Version Freeze Policy v1

> **Superseded** by `docs/superpowers/specs/2026-04-29-calendar-data-policy-redesign-design.md`.
> This document describes the pre-redesign architecture and is kept for historical reference.

状态：accepted design note。本文冻结 `calendar alignment policy` 在 v1 所依赖的 `calendar version` 锚定规则、resolver 消费约束与数据发布流程，目标是保证同一请求可重放出同一 baseline window 与 bucket pairing plan。

配套文档：

- `specs/semantic/calendar-data-contract.zh.md`
- `specs/semantic/calendar-data-v1-source-note.zh.md`
- `specs/semantic/calendar-alignment-policy-v1-scope-note.zh.md`
- `specs/semantic/calendar-alignment-policy.zh.md`
- `specs/semantic/calendar-annotation-failure-policy.zh.md`

## 1. Purpose

本文回答的是：

- 哪些 `calendar version` 才允许被 resolver 消费
- `resolved_calendar_version` 应如何冻结到 observation lineage
- calendar source owner 应按什么发布流程提供可回放版本

本文不定义：

- holiday / event annotation 的生成细节
- policy registry 白名单
- bucket pairing 算法本身

## 2. v1 Freeze 结论

v1 固定采用“published snapshot only”语义：

- resolver 只能消费已发布的 calendar snapshot
- 每次执行都必须冻结明确的 `resolved_calendar_source`
- 每次执行都必须冻结明确的 `resolved_calendar_version`
- 不允许使用“latest as of now”或其他随时间漂移的动态版本语义

对调用方来说，`observe` 仍不直接暴露 `calendar_version`。版本选择是 compiler-owned runtime decision，但一旦执行，冻结结果必须进入 observation artifact / lineage / comparability metadata。

## 3. Version Object Semantics

v1 把可消费的 calendar version 视为“已发布、不可变、可回放”的 snapshot。

每个 version 至少需要满足：

- 对应单一 `resolved_calendar_source`
- 有稳定 `calendar_version` 标识
- 覆盖明确 `region_code`
- 有完整发布日期和生效说明
- 发布后不可原位覆写

允许的版本示例：

- `cn_public_holiday_2026_v1`
- `campaign_calendar_2026_q2_v3`

不允许的版本示例：

- `latest`
- `current`
- `cn_public_holiday_current`
- `campaign_calendar_latest`

原因：

- 这些名称本身不构成版本锚点
- 同一请求在不同时间回放时会漂移到不同底层数据

## 4. Resolver Consumption Rules

resolver 在消费 calendar data 时必须遵守以下规则：

1. 必须在生成 baseline window 前确定 `resolved_calendar_source`
2. 必须在进行 bucket pairing 前确定 `resolved_calendar_version`
3. 若 source 只有动态“当前版本”别名、没有稳定 snapshot version，则必须拒绝执行
4. 若 holiday source 与 event source 采用装配视图，必须记录每个底层 source version，或记录可反查到底层 version 的逻辑装配 version
5. 一旦 resolved plan 生成，后续 `compare` / `validate` / `attribute` 等 compare-like intent 只能复用已冻结 version，不得二次重选

最低验收口径：

- 给定相同请求输入、相同 `resolved_calendar_source`、相同 `resolved_calendar_version`，必须得到同一 baseline window 与同一 bucket pairing plan

## 5. Logical Versioning Rules

v1 允许公共节假日 source 与业务活动 source 分别发布版本，但 resolver 对外暴露的冻结结果必须满足单值可引用。

推荐两种合法模式：

### 模式 A：单源直读

适用于只消费一种底层 source 的 policy：

- `resolved_calendar_source = cn_public_holiday`
- `resolved_calendar_version = cn_public_holiday_2026_v1`

### 模式 B：逻辑装配视图

适用于 holiday 与 event 联合装配：

- `resolved_calendar_source = calendar_data_cn_assembled`
- `resolved_calendar_version = calendar_data_cn_2026q2_v1`

同时要求：

- artifact lineage 或内部 metadata 能反查到底层版本，例如 holiday=`cn_public_holiday_2026_v1`、event=`campaign_calendar_2026_q2_v3`
- 逻辑装配 version 只要底层任一输入变化，就必须产生新的 logical version

v1 不允许：

- 输出只有 source family、没有 version
- 输出只写“assembled latest”
- 底层 source 变化后沿用旧的 logical version 名称

## 6. Publish Workflow

calendar source owner 的最小发布流程固定如下：

1. 生成候选 snapshot
2. 执行 source 侧校验
3. 分配新的不可变 `calendar_version`
4. 发布为可消费 snapshot
5. 更新 resolver 可见的 version registry / source catalog

其中 source 侧校验至少应覆盖：

- `region_code + calendar_version + calendar_date` 唯一
- `weekday` 已归一化到 `1-7`
- `is_weekend` / `is_workday` 字段齐备
- holiday / event stable ID 与 relative key 满足 source 自身约束

发布后约束：

- 已发布 snapshot 不可直接覆写
- 如需修正数据，必须发布新版本，例如 `..._v2`
- resolver 默认只能看到“ready for runtime”的已发布版本集合，而不是草稿或半成品

## 7. Roll Forward / Roll Back Rules

v1 的回滚与重放边界固定如下：

- 新发布 version 只影响新发起的 resolver 执行
- 已完成 observation 不因新版本发布而被动漂移
- 若新版本发现质量问题，应停止新执行继续选用该版本，并切换回上一个可用 published snapshot
- 已经冻结到 artifact lineage 的旧版本仍必须可读取、可审计、可重放

这意味着回滚是“切换默认可选 snapshot”，不是“重写历史 artifact 指向的 version”。

## 8. Failure Policy

以下情况必须结构化失败，而不是 runtime 猜测：

- source 无法提供稳定 `calendar_version`
- resolver 只能拿到动态 latest 别名
- 逻辑装配 version 不能反查底层 holiday / event version
- artifact lineage 缺失已使用的 `resolved_calendar_version`

推荐 issue 归类：

- `calendar_data_missing`
- `calendar_policy_missing`

更细的 annotation 缺失、holiday/event unmapped 与 coverage 问题，见 `specs/semantic/calendar-annotation-failure-policy.zh.md`。

## 9. 与后续实现的接口

本文为以下任务提供冻结前提：

- 任务 4.2：`observe.time_scope + calendar_policy_ref` 到 resolved plan 的编译入口
- 任务 5.1 / 5.2：observation artifact 与 lineage 中冻结 `resolved_calendar_version`
- 任务 6.1：comparability gate 复用已冻结 alignment plan

如果后续实现无法满足本文规则，应优先修正 source publish / version registry / lineage 设计，而不是引入 runtime “latest”兜底。
