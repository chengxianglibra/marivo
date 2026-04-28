# Calendar Annotation Failure Policy v1

状态：accepted design note。本文冻结 `calendar data` annotation 缺失时的处理规则，定义何时必须 fail、何时记录 warning、何时允许进入 policy 内声明的 fallback path。

配套文档：

- `spec/semantic/calendar-data-contract.zh.md`
- `spec/semantic/calendar-annotation-generation-policy.zh.md`
- `spec/semantic/calendar-version-freeze-policy.zh.md`
- `spec/semantic/calendar-alignment-policy-v1-scope-note.zh.md`
- `spec/semantic/calendar-alignment-policy.zh.md`

## 1. Purpose

本文回答的是：

- holiday / event annotation 缺失时，resolver 应如何处理
- 哪些缺失属于 blocking failure，哪些属于 warning
- 哪些场景允许进入 fixed policy 的 fallback path

本文不定义：

- fallback path 的具体 pairing 算法
- coverage 阈值的最终 gate 配置
- source 侧 annotation 如何生成

## 2. v1 总体结论

v1 固定采用“policy-owned failure semantics”：

- annotation 缺失不能由 runtime / LLM 临场猜测
- 是否允许 fallback，必须由固定 `calendar_policy_ref` 的内置语义决定
- source / version 不可用属于 blocking failure
- annotation 局部缺失是否可降级，取决于 policy 的 ordered matching strategy

换句话说：

- 缺 source/version：直接 fail
- 缺 cluster / relative key：按当前 policy 的顺序尝试下一层
- 若已触发 fallback：必须留下结构化 issue
- 若最终 coverage 不足：由 comparability gate 继续判定 warning 或 blocking

## 3. Failure Taxonomy

v1 在 calendar annotation 缺失相关场景只使用以下结构化 issue：

- `calendar_data_missing`
- `holiday_cluster_unmapped`
- `event_cluster_unmapped`
- `fallback_applied`
- `alignment_coverage_insufficient`

其中：

- `calendar_data_missing` 负责 source / version / 基础 annotation 不可消费
- `holiday_cluster_unmapped` 负责 holiday policy 需要的 cluster 或 relative key 无法映射
- `event_cluster_unmapped` 负责 event policy 需要的 cluster 或 relative key 无法映射
- `fallback_applied` 负责记录已进入 policy 内声明的 fallback path
- `alignment_coverage_insufficient` 由 comparability gate 基于最终 coverage 决定

## 4. Blocking Failures

以下情况必须立即 blocking fail，不允许继续 pairing：

### 4.1 Source / Version 不可用

触发条件：

- 无法解析 `resolved_calendar_source`
- 无法解析 `resolved_calendar_version`
- 只能拿到 dynamic latest / current 别名
- 已冻结 version 无法回放读取

输出：

- issue: `calendar_data_missing`

处理要求：

- resolver 直接失败
- 不进入 holiday / event / weekday fallback

### 4.2 基础日历字段缺失

触发条件：

- `calendar_date` 缺失
- `weekday` 缺失或值域非法
- `is_weekend` / `is_workday` 缺失

输出：

- issue: `calendar_data_missing`

处理要求：

- resolver 直接失败
- 不允许猜 weekday 或 workday 语义

### 4.3 逻辑 version 与 annotation 不一致

触发条件：

- 同一 `calendar_version` 下出现 `holiday_group_id + year_relative_holiday_key` 多重映射
- 同一 `calendar_version` 下出现 `event_group_id + year_relative_event_key` 多重映射

输出：

- issue: `calendar_data_missing`

处理要求：

- 视为 source snapshot 无效
- 必须在 source 发布流程修复，而不是 runtime 选择其中一条

## 5. Policy-Level Degradation Rules

### 5.1 `calendar_policy.natural_yoy` / `calendar_policy.natural_mom`

这两类 policy 不依赖 holiday / event annotation。

因此：

- holiday / event annotation 缺失不构成 failure
- 不产生 `holiday_cluster_unmapped` / `event_cluster_unmapped`
- 仍要求基础 calendar version 与 weekday/workday 可消费

### 5.2 `calendar_policy.weekday_yoy` / `calendar_policy.weekday_mom` / `calendar_policy.weekday_wow`

这三类 policy 主要依赖 weekday pairing。

因此：

- holiday / event annotation 缺失本身不构成 failure
- 若 weekday pairing 本身无法稳定决策，使用 `weekday_pairing_tie`
- 不得因为 holiday annotation 缺失而临场切换到 holiday policy

### 5.3 `calendar_policy.holiday_yoy`

这类 policy 的优先顺序是：

1. `holiday_cluster`
2. `year_relative_holiday_key`
3. `same_weekday_nearest`
4. `fallback`

对应失败策略：

- 若窗口中的某日没有 holiday membership，允许继续尝试下一层
- 若已有 `holiday_group_id` 但 baseline 中无可映射 cluster，或缺失 / 无法映射 `year_relative_holiday_key`，记 `holiday_cluster_unmapped`
- 当 resolver 从 holiday path 降级到 `same_weekday_nearest` 或其他 fallback path 时，必须记 `fallback_applied`
- 不允许凭 `holiday_name` 或自然语言解释补造 holiday key

### 5.4 `calendar_policy.event_yoy` / `calendar_policy.event_mom`

这类 policy 的优先顺序是：

1. `event_cluster`
2. `year_relative_event_key`
3. `same_weekday_nearest`
4. `fallback`

对应失败策略：

- 若窗口中的某日没有 event membership，允许继续尝试下一层
- 若已有 `event_group_id` 但 baseline 中无可映射 cluster，或缺失 / 无法映射 `year_relative_event_key`，记 `event_cluster_unmapped`
- 当 resolver 从 event path 降级到 weekday / natural fallback 时，必须记 `fallback_applied`
- 不允许从 prompt、营销文案或活动标题临场恢复 event key

## 6. Warning vs Blocking 边界

v1 的分层边界固定如下：

### Resolver Blocking

以下情况在 resolver 直接 blocking：

- source / version 缺失
- 基础日历字段不可消费
- snapshot 内部唯一性冲突

### Resolver Warning

以下情况可继续生成 resolved plan，但必须留下 issue：

- `holiday_cluster_unmapped`
- `event_cluster_unmapped`
- `fallback_applied`

前提：

- 当前 policy 的下一层 fallback path 已在 registry 中声明
- resolver 最终仍能输出 bucket pairing

### Comparability Gate 决策

以下情况不由 resolver 单独决定最终 blocking 与否，而交给 comparability gate：

- 经过 fallback 后的 coverage 是否仍足够
- `aligned_ratio` 是否低于政策或系统门槛
- warning 是否已经实质破坏 compare 的可比性

对应 issue：

- `alignment_coverage_insufficient`

## 7. Decision Matrix

| 场景 | issue | resolver 行为 | 下游语义 |
| --- | --- | --- | --- |
| 无 stable calendar version | `calendar_data_missing` | 立即 fail | 不生成 resolved plan |
| `weekday` 缺失或非法 | `calendar_data_missing` | 立即 fail | 不生成 resolved plan |
| holiday policy 下该日无 holiday membership | 无或 `fallback_applied` | 尝试下一层 | 由 coverage 决定是否可比 |
| holiday policy 下已有 group，但 baseline 无可映射 cluster 或缺 relative key | `holiday_cluster_unmapped` | 尝试下一层 | warning，可继续 |
| event policy 下已有 group，但 baseline 无可映射 cluster 或缺 relative key | `event_cluster_unmapped` | 尝试下一层 | warning，可继续 |
| 已触发 registry 声明的 fallback | `fallback_applied` | 继续 pairing | warning，供 comparability 复用 |
| fallback 后 coverage 不足 | `alignment_coverage_insufficient` | plan 已生成 | comparability gate 决定 warning / blocking |

补充分层约束：

- `holiday_cluster_unmapped`、`event_cluster_unmapped`、`fallback_applied` 即使由 resolver 产生，也只作为 compare-like 下游 `comparability_gate` 的 warning 输入，不得在成功 artifact 中再被质量面重复报错。
- `weekday_pairing_tie` 若进入 frozen summary，视为 unresolved comparability ambiguity，compare/test 必须按 blocking comparability issue 处理。

## 8. Explicit Non-Goals

v1 明确不允许以下处理方式：

- LLM 根据节假日常识猜测 `holiday_group_id`
- 根据活动标题文本临时补造 `event_group_id`
- 用 `holiday_name` 替代 `holiday_group_id`
- 在未声明 fallback 的 policy 上私自降级到自然日对齐

## 9. Interface To Later Tasks

本文为以下任务提供冻结前提：

- 任务 3.2：为每个 policy 补齐 fallback / coverage 行为
- 任务 4.4：ordered matching strategy 执行器
- 任务 4.5：coverage summary 计算
- 任务 6.1 / 6.3：comparability gate 与 warning / blocking 分层
- 任务 7.3：calendar data 缺失、holiday/event unmapped、fallback 生效场景测试

如果后续实现无法满足本文规则，应优先修正 registry、source snapshot 或 comparability gate，而不是在 runtime 增加非结构化兜底。
