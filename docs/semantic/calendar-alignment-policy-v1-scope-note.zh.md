# Calendar Alignment Policy v1 Scope Note

状态：accepted scope note。本文用于冻结 `calendar alignment policy` 的 v1 产品边界与最小 contract；它优先于更早的开放式 design 草案。

配套文档：

- `docs/semantic/calendar-alignment-policy.zh.md`
- `docs/analysis/intents/atomic/observe.md`
- `docs/analysis/intents/atomic/compare.md`
- `docs/analysis/intents/derived/attribute.md`
- `docs/analysis/intents/derived/validate.md`

## 1. v1 范围结论

v1 只做以下收敛后的能力：

- fixed catalog policy refs
- no public CRUD / validate / activate / publish lifecycle
- `observe`-only input
- observation-first resolution and freeze

这意味着：

- 调用方只能引用白名单 `calendar_policy_ref`
- 调用方不能提交自定义 policy authoring payload
- `compare`、`validate`、`attribute` 等下游 intent 不再重复接收 policy 输入
- compiler / resolver 必须在 `observe` 阶段把 policy 解析结果冻结到上游 observation lineage / metadata

v1 明确不做：

- `calendar_policy.*` 独立 object family 的公开治理面
- 运行时临时 holiday API / 网页抓取
- 将 policy 写回 `metric`、`binding` 或 `time_scope` 主 contract
- compare-like intent 的第二套 baseline generation 入口

## 2. v1 固定 Policy 集

v1 固定白名单如下：

- `calendar_policy.natural_yoy`
- `calendar_policy.weekday_yoy`
- `calendar_policy.holiday_yoy`
- `calendar_policy.event_yoy`
- `calendar_policy.natural_mom`
- `calendar_policy.weekday_mom`
- `calendar_policy.event_mom`
- `calendar_policy.weekday_wow`

每个 policy 都是 compiler-owned registry entry，至少冻结以下内部语义：

- `comparison_basis`
- `use_when`
- `avoid_when`
- `resolved_baseline_generation_rule`
- ordered matching strategy
- fallback behavior
- coverage behavior

初版摘要：

| policy_ref | comparison_basis | use_when | avoid_when | matching strategy summary |
| --- | --- | --- | --- | --- |
| `calendar_policy.natural_yoy` | `yoy` | 自然同比即可成立，业务不要求 weekday / holiday 对齐 | 节假日、活动期、错位 weekday 对可比性有实质影响 | `previous_year` + `natural_date_shift` |
| `calendar_policy.weekday_yoy` | `yoy` | 同比需“周几对周几” | 节假日簇或活动窗比 weekday 更重要 | `previous_year` -> `same_weekday_nearest` -> bounded fallback |
| `calendar_policy.holiday_yoy` | `yoy` | 法定节假日、调休、节前节后窗口是主对齐语义 | 无稳定 holiday annotation 或业务重点不是 holiday 窗口 | `holiday_cluster` -> `year_relative_holiday_key` -> `same_weekday_nearest` -> fallback |
| `calendar_policy.event_yoy` | `yoy` | 业务活动窗口是主比较对象，如 `618`、会员日 | 无稳定 event calendar 或活动期不是核心语义 | `event_cluster` -> `year_relative_event_key` -> `same_weekday_nearest` -> fallback |
| `calendar_policy.natural_mom` | `mom` | 关注自然上期 | 月份错位 weekday / 活动期会破坏解释 | `previous_period` + `natural_date_shift` |
| `calendar_policy.weekday_mom` | `mom` | 环比要控制 weekday 结构差异 | 节假日或活动窗需要优先对齐 | `previous_period` -> `same_weekday_nearest` -> bounded fallback |
| `calendar_policy.event_mom` | `mom` | 同类活动期做环比，如本月会员日对上月会员日 | 无稳定 event annotation | `previous_period` -> `event_cluster` -> `year_relative_event_key` -> fallback |
| `calendar_policy.weekday_wow` | `wow` | 周比且要求 weekday 精确配对 | 业务需要 holiday/event 对齐语义 | `previous_period` + exact weekday pairing，必要时结构化报冲突 |

## 3. Request Boundary

`calendar_policy_ref` 的 v1 request boundary 固定如下：

- 只在 `POST /sessions/{session_id}/intents/observe` 暴露为可选字段
- 只接受 fixed catalog refs，不接受开放式 policy payload
- `compare`、`validate`、`attribute`、`decompose`、`test` 等下游步骤不重复接收 policy 输入

下游复用方式固定为：

1. `observe` 接收 `time_scope + calendar_policy_ref`
2. compiler / resolver 生成 resolved alignment plan
3. observation artifact / lineage / comparability metadata 冻结结果
4. compare-like intent 只读取上游冻结结果，不重建第二套 holiday / weekday / event 对齐逻辑

## 4. Resolved Alignment Plan 最小字段

v1 最小 resolved plan 字段集固定为：

- `policy_ref`
- `comparison_basis`
- `resolved_calendar_source`
- `resolved_calendar_version`
- `resolved_baseline_generation_rule`
- `current_window`
- `baseline_window`
- `bucket_pairing`
- `coverage_summary`
- `comparability_warnings`

字段语义：

| field | 说明 |
| --- | --- |
| `policy_ref` | 本次执行最终采用的固定 policy ref |
| `comparison_basis` | `wow` / `mom` / `yoy` 中的比较基准 |
| `resolved_calendar_source` | 实际使用的 calendar data 来源标识 |
| `resolved_calendar_version` | 实际冻结的 calendar version；不得是“latest as of now” |
| `resolved_baseline_generation_rule` | baseline window 的确定性生成规则 |
| `current_window` | 当前观察窗口 |
| `baseline_window` | 解析得到的基线窗口；若无法生成必须结构化失败或置空并带 issue |
| `bucket_pairing` | 当前 bucket 与 baseline bucket 的配对明细，包括 pairing reason、shift days、issues |
| `coverage_summary` | 至少包含 `aligned_bucket_count`、`unpaired_bucket_count`、`aligned_ratio` |
| `comparability_warnings` | 供 comparability gate 与用户 surface 复用的 warning 集合 |

## 5. Comparability Issue Taxonomy

v1 起步 issue taxonomy 固定如下：

| code | trigger | owner layer | suggested action |
| --- | --- | --- | --- |
| `calendar_policy_missing` | 请求或上游 freeze 需要 policy，但最终未解析出合法 `calendar_policy_ref` | request / compiler | 补充显式 policy，或在上游路由阶段返回歧义错误 |
| `calendar_data_missing` | policy 所需 calendar annotation / source / version 缺失 | calendar data / resolver | 修复 calendar source 或阻断该 policy 执行 |
| `holiday_cluster_unmapped` | holiday policy 需要的 holiday cluster / relative key 无法映射 | resolver / comparability | 记录 warning 或按固定 policy 失败，不允许 runtime 猜测 |
| `event_cluster_unmapped` | event policy 需要的 event cluster / relative key 无法映射 | resolver / comparability | 补齐活动日历或回退到显式 fallback |
| `weekday_pairing_tie` | `same_weekday_nearest` 出现等距候选且 policy 未显式提供 tie-breaker | resolver / comparability | 输出结构化歧义，必要时 fail；v1 默认 catalog 已固定 tie-breaker，因此正常不触发 |
| `alignment_coverage_insufficient` | `aligned_ratio` 低于 policy / gate 要求 | comparability gate | 标为 warning 或 blocking issue，阻止不可靠 compare |
| `fallback_applied` | resolver 触发了 policy 内声明的 fallback path | resolver / diagnostics | 显式记录 fallback reason，供下游继续判断是否可比 |

分层约束：

- 单边窗口数据缺失或原始值质量问题归 `quality_gate`
- 双边 bucket 配对、calendar 覆盖率与对齐稳定性归 `comparability_gate`

## 6. 验收口径

当以下条件同时成立时，M1 的 “Scope 与 Contract 冻结” 可视为完成：

- 文档明确声明 v1 不支持自定义 policy authoring
- `ObserveRequest` 已暴露可选 `calendar_policy_ref`
- compare-like intent 文档明确自己不再重复接收 policy 输入
- resolved plan 与 issue taxonomy 已形成可直接落到 runtime / evidence / diagnostics 的最小字段表
