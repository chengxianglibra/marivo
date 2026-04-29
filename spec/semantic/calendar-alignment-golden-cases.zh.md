# Calendar Alignment Golden Cases

> **Superseded** by `docs/superpowers/specs/2026-04-29-calendar-data-policy-redesign-design.md`.
> This document describes the pre-redesign architecture and is kept for historical reference.

状态：accepted acceptance note。本文冻结 `calendar alignment policy` 的最小验收样例集，用作人工验收、回归核对与后续自动化扩展的统一基线。

## 目的

本样例集只覆盖 v1 最小验收面，不尝试替代完整单元测试或集成测试。

它回答三个问题：

- 给定一个典型业务场景，应该选哪个 `calendar_policy_ref`
- resolver 至少应产出什么关键 pairing 行为
- 仓库里哪条现有自动化测试是这个样例的真值锚点

## 使用规则

- 样例只冻结关键行为级预期，不枚举全量 `bucket_pairing`
- 每个样例都必须绑定明确的 `calendar_policy_ref`
- 每个样例都必须说明预期 baseline window、主要 `pairing_reason`、coverage / warnings
- 每个样例都必须能追溯到现有测试锚点，避免形成第二套脱节真值
- 若未来 runtime 行为变更，必须同步更新本文档与对应测试

## 最小样例集

| case_id | 场景 | request shape | calendar_policy_ref | expected baseline_window | expected primary pairing behavior | expected coverage / warnings | test anchors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `natural_yoy_basic` | 普通同比；业务只要求自然同比，不要求 weekday / holiday / event 对齐 | `day` 粒度，`2026-04-01` 到 `2026-04-04` | `calendar_policy.natural_yoy` | `2025-04-01` 到 `2025-04-04` | 每个 bucket 主行为都是 `natural_date_shift` | `aligned_ratio = 1.0`，无 warning | `tests/test_compiler_typed_resolution.py::test_compile_step_resolves_natural_yoy_alignment_successfully` |
| `holiday_yoy_qingming` | 清明同比；节假日窗口是主对齐语义 | `day` 粒度，`2026-04-04` 到 `2026-04-06` | `calendar_policy.holiday_yoy` | `2025-04-04` 到 `2025-04-06` | 清明当天优先命中 `holiday_cluster`；跨节窗口可继续按 `year_relative_holiday_key` 或降级 matcher 对齐 | 期望 coverage 完整；happy path 无 warning | `tests/test_calendar_alignment_pairing.py::test_holiday_policy_matches_unique_cluster_before_fallback` |
| `event_yoy_618` | `618` / 活动期同比；活动窗是主比较对象 | `day` 粒度，`2026-06-15` 到 `2026-06-17` | `calendar_policy.event_yoy` | `2025-06-15` 到 `2025-06-17` | 活动窗口优先按 `event_cluster` / `year_relative_event_key` 对齐，而不是自然日直接平移 | `aligned_ratio = 1.0`，无 warning | `tests/test_compiler_typed_resolution.py::test_compile_step_resolves_event_yoy_alignment_successfully` |
| `weekday_yoy_effect` | 工作日效应同比；目标是控制 weekday 结构差异 | `day` 粒度，工作日窗口同比 | `calendar_policy.weekday_yoy` | 对应上一年窗口 | 主行为以 `same_weekday_nearest` 为主；若个别 bucket 超出 `max_shift_days`，允许按 policy 降级到 `natural_date_shift` | coverage 完整；该最小 happy path 不要求 comparability warning | `tests/test_compiler_typed_resolution.py::test_compile_step_resolves_weekday_yoy_alignment_successfully` |
| `natural_mom_basic` | 月环比；关注自然上期，不额外控制 weekday / event 结构 | `day` 粒度，`2026-04-08` 到 `2026-04-15` 的自然上期对比 | `calendar_policy.natural_mom` | 按 `previous_period` 生成上一期等长窗口 | 主行为应为 `natural_date_shift` | coverage 完整；happy path 无 warning | `tests/test_compiler_typed_resolution.py::test_compile_step_resolves_natural_mom_alignment_successfully` |
| `weekday_wow_week_window` | 周环比；要求按周内 weekday 精确对齐后再聚合 | `week` 粒度，`2026-04-06` 到 `2026-04-13` | `calendar_policy.weekday_wow` | `2026-03-30` 到 `2026-04-06` | compiler 必须先按日级 `same_weekday_nearest` 完成 7 个 bucket pairing，再服务于周级观察；不是“整周黑盒对整周” | `aligned_bucket_count = 7`，无 warning | `tests/test_compiler_typed_resolution.py::test_compile_step_records_day_aligned_weekday_wow_for_week_window` |

## 样例解释

### `natural_yoy_basic`

- 用于验收最基础的自然同比路径
- 若该样例都不能稳定产出 `natural_date_shift`，说明 baseline 生成或自然日 pairing 已回归

### `holiday_yoy_qingming`

- 用于验收“节假日优先于 weekday”的核心边界
- 验收时关注点是：节假日 bucket 不应直接退化为自然日平移
- 本样例不要求列出全窗口每一天的 pairing 明细，只要求确认 holiday matcher 是首要命中路径

### `event_yoy_618`

- `618` 在本文中作为活动期同比的代表性命名
- 现有测试锚点使用稳定 event annotation 验证 `event_yoy` happy path；若后续活动样例替换为其他 event cluster，仍应保留“活动窗优先于自然日”的验收语义

### `weekday_yoy_effect`

- 用于验收 weekday 结构控制是主比较语义
- 验收时关注点是：大多数 bucket 以 `same_weekday_nearest` 对齐；若少量 bucket 因 `max_shift_days` 约束降级，仍应保持 coverage 完整

### `natural_mom_basic`

- 用于验收 `mom` 的 baseline 生成规则是 `previous_period`
- 不要求冻结每个 bucket 的明细，只要求确认不是 weekday / event 优先路径

### `weekday_wow_week_window`

- 用于验收 `week` 粒度 observe 的特殊语义
- 关键点不是返回 shape，而是内部 pairing 仍按 `day` 处理并冻结到 `resolved_policy_summary`

## 维护要求

- 新增 policy 或调整 v1 默认 matcher 顺序时，必须评估本文档是否需要增补或改写
- 若测试锚点重命名，必须同步更新本文档中的 `test anchors`
- 若某个样例需要靠 warning / fallback 才能表达其核心语义，应单列为扩展样例，不覆盖本文的 happy-path 最小集
