# Calendar / Alignment Policy Contract（草案）

本文定义 Marivo 中“节假日 / 星期 / 业务活动日等可比期对齐规则”的目标设计。

状态：draft design with v1 scope freeze。本文保留较完整的设计空间，但 v1 实施边界以后续 scope note 为准：`docs/semantic/calendar-alignment-policy-v1-scope-note.zh.md`。

本文与以下文档配套：

- `docs/semantic/time-schema-contract.zh.md`
- `docs/semantic/calendar-data-contract.zh.md`
- `docs/semantic/calendar-data-v1-source-note.zh.md`
- `docs/semantic/compiler-spec.zh.md`
- `docs/analysis/intents/atomic/observe.md`
- `docs/analysis/intents/atomic/compare.md`
- `docs/analysis/evidence-engine/rules/comparability-gate-contract.md`

## Purpose

当前 Marivo 已有：

- `time.*`：表达稳定时间语义
- `time_scope`：表达本次请求的观察时间范围
- `compare` / `validate` / `attribute`：表达双边比较或比较驱动的分析动作

但对于互联网业务中的以下高频需求，还缺少一层稳定、可治理、可复用的抽象：

- 同比不是按自然日期平移，而是按节假日窗口对齐
- 同比需要“周一对周一、周日对周日”，而不是简单减去 365 天
- 大促、会员日、平台招商活动需要按活动窗口而不是自然月或自然周对齐
- 节假日前后存在预热、回落、补班、调休等非对称结构，需要显式规则
- 对齐失败时，需要产生结构化 comparability diagnostics，而不是留给大模型自由解释

本文的目标是补上这层“可比期生成与配对策略（calendar / alignment policy）”，让大模型只负责选择策略，不负责临场理解或计算对齐逻辑。

## 设计目标

该 contract 应满足：

- **显式治理**：节假日来源、版本、地区、偏移规则必须是可声明、可审计对象
- **稳定复用**：相同的同比 / 环比对齐逻辑可被多个 metric、process、intent 复用
- **与 `time.*` 分层**：`time.*` 继续回答“这是什么时间语义”，policy 回答“如何构造可比窗口”
- **与 `binding` 分层**：binding 继续负责物理时间字段映射，policy 不接管物理列与 SQL 表达式
- **确定性展开**：compiler / resolver 能把 policy 稳定展开为 baseline window、bucket pairing、comparability diagnostics
- **失败可解释**：对齐缺失、等距冲突、节假日未定义、窗口不完整等问题都能结构化暴露

## 非目标

本文明确不追求：

- 让 LLM 通过自然语言临场解释“今年春节比去年早几天”
- 在 `time_scope` 中内嵌大段一次性 holiday matching 逻辑
- 让 binding 或 SQL 模板负责业务节假日语义
- 在公共 contract 中暴露 engine-specific 日期函数、临时 SQL 片段或网页抓取逻辑
- 把所有 calendar data 都建成新的 top-level business object taxonomy

## 核心设计结论

### 1. `time.*` 不负责可比期对齐

`time.*` 回答的是：

- 这是什么时间语义
- 它可承担哪些分析角色
- 它如何被其他 semantic objects 稳定引用

`time.*` 不回答：

- 去年同期窗口应该如何生成
- 节假日前后应向前还是向后找最近周一
- 调休后的周末是否视为工作日

这些属于 calendar / alignment policy，而不是时间语义本体。

### 2. 可比期对齐需要独立的受治理语义，但 v1 先不开放独立对象面

长期看，calendar alignment 仍然是独立于 `time.*`、`time_scope` 与 `binding` 的一层受治理语义。理论上的对象命名可选：

- `calendar_policy.*`
- `alignment_policy.*`
- `comparison_policy.*`

本文暂用 `calendar_policy.*`。但 v1 已收敛为 compiler-owned fixed catalog refs，不开放独立 CRUD 或 object-family lifecycle。

它回答的是：

- 在给定 `time_scope.current` 与比较类型（如 `yoy` / `wow`）下，baseline 如何生成
- 节假日、weekday、活动窗口、工作日等优先级如何处理
- 窗口中的每个 bucket 应如何与 baseline bucket 配对
- 对齐失败、等距冲突或 coverage 不足时如何处理

### 3. 节假日事实应来自受治理 calendar data，而不是运行时临时抓取

实际节假日信息应由 Marivo 消费“已同步、已版本化、已落库”的 calendar data。

运行时不应：

- 直接访问网页
- 临时调用外部 holiday API
- 依赖大模型从文本公告中抽取放假安排

推荐来源：

1. 平台内置 calendar table
2. 从权威公告源定期同步后固化到 Marivo source
3. 业务自定义 calendar table，例如大促、会员日、赛事、学期、财报季

### 4. “向前还是向后”必须是 policy 字段，不是实现细节

像“周一对周一，但日期偏差时优先向前还是向后”这种规则，不能藏在实现里。

它必须在 contract 中显式表达，例如：

- `anchor_mode = "same_weekday_nearest"`
- `tie_breaker = "prefer_backward"`
- `max_shift_days = 3`

这样不同业务才能稳定复用或差异化治理。
当前 v1 fixed policy catalog 中，所有使用 `same_weekday_nearest` 的内置 policy 都显式冻结为
`tie_breaker = "prefer_backward"`、`max_shift_days = 3`。其中 `max_shift_days = 3` 表示允许候选日与目标日相差最多 3 天，包含恰好 3 天的边界情况。

### 5. compiler / resolver 负责展开，intent 只负责引用

用户或 agent 在请求中不应再说：

> 帮我按节假日口径做同比，最好周一对周一。

而应引用一个稳定对象，例如：

- `calendar_policy.holiday_yoy`

然后由 compiler / resolver 稳定产出：

- baseline window
- bucket pairing map
- unresolved alignment gaps
- comparability issues

## 分层边界

| 层 | 主要对象 | 回答什么 | 不回答什么 |
| --- | --- | --- | --- |
| 时间语义层 | `time.*` | 这是什么时间语义 | 基线如何对齐 |
| 日历事实层 | `calendar data` | 某天是否节假日、工作日、属于哪个 holiday cluster | 本次分析应如何找 baseline |
| 对齐策略层 | `calendar_policy.*` | baseline 生成、bucket pairing、偏移规则、失败处理 | 物理时间列如何读取 |
| 请求层 | `time_scope` | 本次看哪段时间 | 节假日和 weekday 的通用对齐逻辑 |
| 绑定层 | `binding.*` | 时间语义如何映射到物理字段 | 去年同期该配哪一天 |
| 编译 / 解析层 | compiler / resolver | 把 policy 展开成确定性 comparison plan | 自发发明业务规则 |

## Calendar Data Contract

本文不要求 calendar data 本身一定成为新的 top-level semantic object family，但 compiler / resolver 需要一个稳定输入表或稳定 source contract。

建议 calendar data 至少具备以下字段：

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `calendar_date` | date | yes | 自然日 |
| `region_code` | string | yes | 地区，例如 `CN` |
| `calendar_version` | string | yes | 日历版本，例如 `cn_public_holiday_2026_v1` |
| `is_weekend` | bool | yes | 是否周末 |
| `is_workday` | bool | yes | 是否工作日，允许补班周末为 true |
| `holiday_name` | string \| null | no | 节假日名 |
| `holiday_group_id` | string \| null | no | 节假日簇 ID，例如 `spring_festival` |
| `weekday` | integer | yes | 固定为 1-7，其中 1=周一，7=周日 |
| `year_relative_holiday_key` | string \| null | no | 节假日相对位置，如 `spring_festival_d-1` |
| `event_group_id` | string \| null | no | 业务活动簇 ID，例如 `618_promo`、`member_day` |
| `year_relative_event_key` | string \| null | no | 活动相对位置，如 `618_promo_d-1` |

要求：

- 同一 `region_code + calendar_version + calendar_date` 必须唯一
- `weekday` 固定值域为 `1-7`，不得再使用 `0-6` 或其他本地约定
- “周末但补班”与“工作日但放假”都必须能表达
- 不要求所有业务都使用法定节假日；业务活动日历可并存

## `calendar_policy.*` v1 Minimal Contract

既然 v1 已明确采用固定 catalog、无独立 CRUD、`observe` 唯一暴露入口，那么 `calendar_policy` 不再需要完整的通用 authoring schema。v1 对外只暴露固定 `policy_ref` 集合。

### Public Input Shape

```ts
type CalendarPolicyRef =
  | "calendar_policy.natural_yoy"
  | "calendar_policy.weekday_yoy"
  | "calendar_policy.holiday_yoy"
  | "calendar_policy.event_yoy"
  | "calendar_policy.natural_mom"
  | "calendar_policy.weekday_mom"
  | "calendar_policy.event_mom"
  | "calendar_policy.weekday_wow";
```

对调用方来说，v1 唯一需要显式提供的字段是：

- `calendar_policy_ref`

其余诸如：

- `comparison_basis`
- `alignment_mode`
- `baseline_generation_rule`
- `anchor_priority`
- `offset_rule`
- `bucket_pairing_rule`
- `coverage_rule`
- `fallback_rule`

都视为 compiler-owned catalog entry 的内部属性，而不是 v1 的对外 authoring contract。

### 为什么简化

这样收敛有三个好处：

- 输入更简单：agent 和调用方只需要在固定候选集中选一个 `policy_ref`
- 边界更清楚：v1 不假装支持自定义 policy authoring
- 审计仍完整：复杂策略仍可通过 resolved plan 暴露，而不是丢失解释能力

### v1 Registry 语义

虽然输入只暴露 `policy_ref`，但每个内置 policy 在 compiler 内部仍然绑定一套稳定策略语义。至少包括：

- `comparison_basis`
- `resolved_calendar_source`
- `resolved_alignment_mode`
- `resolved_baseline_generation_rule`
- ordered conditional matching strategy
- fallback / coverage 行为

这些定义由 compiler 侧固定 catalog 维护，并随 catalog 版本冻结，而不是由请求方在 runtime 自由拼装。

### Ordered Matching Strategy

v1 仍然保留“按顺序尝试一组匹配线索”的消费语义，但它已不再是请求方可写字段，而是内置 policy 的内部策略。

它的语义仍然是：

1. resolver 按固定顺序逐项尝试
2. 当前匹配线索只有在 annotation / 前提成立时才可用
3. 当前线索不适用时，顺序回退到下一项
4. 不允许在该顺序之外再引入第二套并行覆盖逻辑

例如，`calendar_policy.holiday_yoy` 可在内部表达为：

- 优先 `holiday_cluster`
- 再尝试 `year_relative_holiday_key`
- 再退回 `same_weekday`
- 最后退回 `natural_date`

因此，“4 月全月同比，但清明节相关日期优先按节假日相对位置对齐，非节假日日期按 weekday 对齐”在 v1 中应通过选择 `calendar_policy.holiday_yoy` 表达，而不是在请求里再拼装一套自定义字段。

## 请求层如何消费

本文不直接定义最终 HTTP wire spec，但 intent 边界需要明确：

- v1 中 `calendar_policy_ref` 只由 `observe` 暴露
- v1 统一采用 `observation-first` 编译入口
- `compare` 继续保持“typed observation refs in, delta artifact out”的纯引用式边界
- 下游 compare-like intent 若需要节假日对齐语义，应读取上游 observation artifact 中已解析的 comparability metadata，而不是再次接收一份平行 policy 输入

推荐消费方式：

1. 请求继续保留 `time_scope` 作为当前窗口入口
2. `observe` 可显式提供 `calendar_policy_ref`
3. resolver 基于 `observe.time_scope + calendar_policy_ref` 生成 baseline window 与 bucket pairing plan
4. `observe` artifact 必须保留 resolved policy summary，供 `compare` / `validate` / `attribute` 等下游 typed-ref intent 复用

也就是说，v1 中不存在多个并列的 baseline-generating intent 入口；所有 calendar alignment 都先在 `observe` 阶段冻结，再由下游步骤复用。

上游请求示意：

```json
{
  "metric": "metric.gmv",
  "time_scope": {
    "kind": "range",
    "start": "2026-10-01",
    "end": "2026-10-08"
  },
  "calendar_policy_ref": "calendar_policy.holiday_yoy"
}
```

`compare` 请求保持不变：

```json
{
  "left_ref": {
    "session_id": "sess_current",
    "step_id": "step_obs_current",
    "step_type": "observe"
  },
  "right_ref": {
    "session_id": "sess_current",
    "step_id": "step_obs_baseline",
    "step_type": "observe"
  }
}
```

## Policy Resolution vs Policy Binding

`calendar_policy` 的选择过程与执行绑定过程必须显式分层。

### Resolution：谁来决定候选 policy

用户往往只会表达业务意图，例如：

- “看今年春节和去年春节同比”
- “看这次 `618` 和去年 `618` 的活动期对比”
- “看会员日当天及前后预热期的同比”

在这一步，agent 或其他上游 planner 可以参与做 **policy resolution**，即把自然语言问题解析成候选 `calendar_policy_ref`。

但 resolution 只是“选择建议”阶段，不是正式执行契约。系统不应把以下内容当成可执行输入：

- prompt 中的口头描述
- 未冻结的临场判断
- 没有写入 request / compile plan / artifact lineage 的隐式选择

### Binding：谁来冻结最终 policy

真正进入执行时，必须把最终选择绑定成显式输入：

- 写入上游请求中的 `calendar_policy_ref`
- 或写入 compile plan 中的 resolved policy binding
- 并在下游 artifact lineage / comparability metadata 中保留该绑定结果

也就是说：

- agent 可以参与选 policy
- 但不能只“口头决定” policy
- 一旦执行，必须把结果落成明确的 `calendar_policy_ref`

因此，本文反对的不是 “agent 参与 policy 选择”，而是“把未结构化的 agent 判断直接当成执行契约”。

### 选择优先级

推荐按以下顺序解析最终 policy：

1. 用户或调用方显式提供的 `calendar_policy_ref`
2. 上游系统显式注入的固定 policy binding
3. agent / planner 在允许集合内解析得到的候选 policy

若第 3 步存在多个同样合法的候选项，系统不应静默猜测，应：

- 要么要求用户澄清
- 要么返回结构化歧义错误

### 与 metric 的关系

`calendar_policy` 不应成为 metric public contract 的固定字段。

原因：

- 同一个 metric 往往支持多种合法比较口径
- policy 属于“分析问题的比较语义”，不是 measurement identity
- Marivo 现有设计已明确：编译期组合约束应优先通过独立 compiler compatibility profile 表达，而不是回流到 metric 主 schema

因此，若需要声明：

- 某 metric 允许哪些 `calendar_policy.*`
- 某 metric 在哪些 comparison basis 下允许活动窗口对齐
- 某些 policy 是否只对特定 process / subject / grain 合法

更合适的承载位置是 compiler compatibility profile / governance context，而不是 `MetricHeader`。

## Compiler / Resolver Responsibilities

compiler / resolver 需要把 `calendar_policy.*` 确定性展开为 comparison plan。

最少输出应包括：

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

### Resolved Plan Sketch

```ts
type ResolvedCalendarAlignmentPlan = {
  policy_ref: string;
  comparison_basis: "wow" | "mom" | "yoy";
  resolved_calendar_source: string;
  resolved_calendar_version: string;
  resolved_baseline_generation_rule: {
    strategy:
      | "previous_year"
      | "previous_period";
    offset_value: number | null;
    offset_unit: "day" | "week" | "month" | "quarter" | "year" | null;
    fixed_start: string | null;
    fixed_end: string | null;
    named_window_ref: string | null;
  };
  current_window: { start: string; end: string };
  baseline_window: { start: string; end: string } | null;
  bucket_pairing: Array<{
    current_bucket_start: string;
    baseline_bucket_start: string | null;
    pairing_reason:
      | "holiday_cluster"
      | "year_relative_holiday_key"
      | "event_cluster"
      | "year_relative_event_key"
      | "same_weekday_nearest"
      | "natural_date_shift"
      | "fallback";
    shift_days: number | null;
    issues: string[];
  }>;
  coverage_summary: {
    aligned_bucket_count: number;
    unpaired_bucket_count: number;
    aligned_ratio: number;
  };
  comparability_warnings: string[];
};
```

要求：

- resolver 不得发明未在固定 catalog entry 中声明的 pairing / fallback 逻辑
- resolver 必须按 policy 声明顺序逐 bucket 执行 ordered matching，而不是为整窗只选一个全局 matcher
- resolver 不得在 `calendar_version` 未固定时生成“latest as of now”式 plan
- 若 calendar annotations 缺失，必须按 `fallback_rule` 行为处理
- 所有 bucket pairing 都应可重放、可解释

## Comparability Integration

`comparability_gate` 不应重新推导一套平行 holiday 逻辑，而应消费 resolver 已产出的结构化结果。

v1 已冻结并实现以下 requirement 语义；`comparability_gate` 应直接把它们映射到
`resolved_policy_summary` / compare-like finding payload 中的结构化字段，而不是再发明一套并行规则：

- `baseline_calendar_policy_resolved`
- `holiday_cluster_alignment_complete`
- `event_cluster_alignment_complete`
- `weekday_pairing_compatible`
- `calendar_coverage_sufficient`
- `alignment_tie_breaker_resolved`

推荐的 token 形态固定为：

- 满足：`comparability_requirement:<requirement_key>:met`
- 不满足：`comparability_requirement:<requirement_key>:failed`
- 摘要信号：`comparability_signal:window_alignment:comparable|needs_attention`

其中 v1 对 `calendar_coverage_sufficient` 采用严格满覆盖语义：

- `effective_coverage_summary.aligned_ratio = 1.0`
- `effective_coverage_summary.unpaired_bucket_count = 0`

若 coverage 字段缺失、非数值或显式出现 `alignment_coverage_insufficient`，都应视为该 requirement 不满足。

典型 comparability issue：

- `calendar_policy_missing`
- `calendar_data_missing`
- `holiday_cluster_unmapped`
- `event_cluster_unmapped`
- `weekday_pairing_tie`
- `alignment_coverage_insufficient`
- `fallback_applied`

当前 v1 默认 policy 已通过显式 `tie_breaker` 消除 `same_weekday_nearest` 的等距歧义，因此
`weekday_pairing_tie` 作为 taxonomy 预留项保留，但默认 catalog 不应触发它。

若上游冻结 summary 中仍出现 `weekday_pairing_tie`，`comparability_gate` 应同时把它映射为：

- `weekday_pairing_compatible:failed`
- `alignment_tie_breaker_resolved:failed`

每个 issue 在 v1 都应至少有：

- 明确触发条件
- 归属层（request / compiler / resolver / comparability gate）
- 建议处理动作

语义边界：

- 单边窗口数据不完整仍属于 `quality_gate`
- 双边窗口配对关系不稳定属于 `comparability_gate`
- compare / test 成功 artifact 中出现的 calendar alignment issues 必须全部带 `comparability_gate` 语义，不得与 `quality_gate` 重复报同一 code
- v1 默认 severity 分层：`weekday_pairing_tie` 与 frozen metadata mismatch 为 blocking comparability issue；`holiday_cluster_unmapped`、`event_cluster_unmapped`、`fallback_applied`、`alignment_coverage_insufficient` 为 non-blocking comparability warning
- `resolved_policy_summary.bucket_pairing[*].strictness_level` / `is_reused_baseline_bucket` 是对 bucket pairing 可解释性的显式补充；出现 fallback 或 baseline bucket 被多个 current bucket 复用时，不得再把该 pairing 表述为“严格对齐”
- `resolved_policy_summary.rollup_safe=false` 表示该次对齐结果不适合按严格 1:1 bucket pairing 语义做 rollup 解释；它是 metadata 澄清位，不在 v1 自动升级为 compare/test 的 blocking 规则

## 典型策略示例

在 v1 中，典型用法不再是提交完整 policy payload，而是选择固定 `policy_ref`。

### 1. 中国法定节假日同比

```json
{
  "calendar_policy_ref": "calendar_policy.holiday_yoy"
}
```

语义：

- `comparison_basis = yoy`
- 优先公共节假日 cluster
- 未命中节假日 annotation 时，顺序回退 weekday / natural date

### 2. 大促活动窗口同比

```json
{
  "calendar_policy_ref": "calendar_policy.event_yoy"
}
```

适用场景：

- `618`、`双11`、黑五等大促窗口同比
- 月度会员日、品类日、平台主题活动的活动期对齐
- 需要按活动窗口整体做同比对齐

### 3. 常规工作日同比

```json
{
  "calendar_policy_ref": "calendar_policy.weekday_yoy"
}
```

## 为什么不把它直接放进 `time_scope`

如果把全部对齐逻辑塞进 `time_scope`：

- 请求层会变得过重
- 相同逻辑难以治理和复用
- compare / observe 上游下游难以共享同一份解析结果
- 很容易让模型重新生成局部规则，破坏确定性

更稳定的边界是：

- `time_scope` 负责“当前窗口”
- `calendar_policy_ref` 负责“如何找 baseline 并配对 bucket”

## 为什么不把它放进 `binding`

如果放进 `binding`：

- 会把分析可比性语义和物理落地混在一起
- 同一指标在不同分析场景下可能需要不同 policy，binding 不适合作为唯一承载点
- holiday / weekday 对齐属于业务比较语义，不属于列级映射

binding 仍应只回答：

- 时间字段在哪里
- 如何解析
- freshness / lateness / incomplete-window 如何消费

## v1 Catalog Strategy

v1 不提供 `calendar_policy.*` 的独立 CRUD。

原因：

- 当前更需要稳定、可审计、低歧义的默认 policy 集，而不是开放式 policy authoring
- 若在 schema 仍快速迭代时开放 CRUD，容易把不稳定 contract 过早固化到用户面
- agent 分析阶段更适合从固定候选集中做 resolution / binding，而不是 runtime 发明新 policy

因此，v1 采用 **fixed catalog, no public CRUD** 的策略：

- policy 通过内部 catalog 预装
- 调用方只能引用已存在的 `calendar_policy_ref`
- 若现有集合无法覆盖需求，应视为语义治理动作，而不是一次普通分析请求中的 runtime 生成

同时，v1 中的 `calendar_policy.*` 先作为 **compiler-owned catalog object**，而不是新的 top-level semantic object family。

原因：

- 它当前更接近“编译期 baseline / pairing 策略”，不是稳定的业务语义本体
- 本文 contract 仍在快速收敛，过早对象化会放大后续演进成本
- 在不开放独立 CRUD 的前提下，没有必要立即引入完整的 semantic object 生命周期

因此，v1 只承诺：

- `calendar_policy_ref` 可被请求与 compile plan 稳定引用
- policy 条目由 compiler 侧 catalog 预装和版本化
- policy 条目可通过 discovery surface 发现：`GET /catalog/search?type=calendar_policy`、
  `GET /semantic/resolve/{ref}`，以及只读的 compatibility-profile list 投影视图
- 不承诺与 `metric.*` / `dimension.*` / `time.*` 同级的独立治理面
- `calendar_policy_ref` 只在 `observe` 暴露，整体采用 `observation-first` 编译入口

是否在后续版本升级为 top-level semantic object family，应等以下条件满足后再评估：

- contract 基本稳定
- 确实需要独立 CRUD / validate / activate / publish 生命周期
- 多团队存在持续新增或治理 policy 的需求
- 与 `time.*`、compiler profile、governance context 的分层边界已清晰

### Default Policy Set

v1 建议只内置一小组高频、边界清晰、彼此不重叠过多的默认 policy。

#### YoY

- `calendar_policy.natural_yoy`
  - 含义：按自然日期做同比
  - 适用：普通同比，未强调 weekday、节假日或活动窗口
- `calendar_policy.weekday_yoy`
  - 含义：优先同 weekday 对齐的同比
  - 适用：用户明确要求“周一对周一、周末对周末”或 weekday 效应明显
- `calendar_policy.holiday_yoy`
  - 含义：优先公共节假日 cluster，对不上再回退 `same_weekday` / `natural_date`
  - 适用：春节、清明、国庆等法定节假日口径
- `calendar_policy.event_yoy`
  - 含义：优先业务活动 cluster 的同比
  - 适用：`618`、双11、会员日、大促活动期

#### MoM

- `calendar_policy.natural_mom`
  - 含义：按自然日期做月环比
  - 适用：普通月环比
- `calendar_policy.weekday_mom`
  - 含义：优先同 weekday 对齐的月环比
  - 适用：日级月环比，且 weekday 分布影响明显
- `calendar_policy.event_mom`
  - 含义：按业务活动窗口做月环比
  - 适用：月度会员日、月度主题活动、稳定重复的活动档期

#### WoW

- `calendar_policy.weekday_wow`
  - 含义：按同 weekday 做周环比
  - 适用：用户明确要求上周同期，且 weekday 效应明显

### 为什么默认不提供 `holiday_mom`

`holiday_mom` 不建议作为通用默认 policy。

原因：

- 多数法定节假日并不是天然稳定的“月环比对象”
- 节假日常跨月或在自然月中的位置漂移明显
- “上个月对应节假日窗口”在很多月份并不存在

若后续出现强需求，可在治理层单独新增特定 holiday 场景 policy，而不是把 `holiday_mom` 作为通用默认项。

### Policy Registry For Agent Resolution

agent 不应只凭 prompt 自由发挥，而应读取一个固定的 policy registry 摘要做判断。该摘要至少应包含：

- `policy_ref`
- `comparison_basis`
- `window_tags`
- `use_when`
- `avoid_when`

HTTP discovery surface 已暴露该固定 registry：优先用 `GET /catalog/search?q=holiday&type=calendar_policy`
或 `GET /catalog/search?q=weekday&type=calendar_policy` 查找候选，再用
`GET /semantic/resolve/calendar_policy.*` 读取详情。`GET /compiler/compatibility-profiles` 也会以
只读、`system_managed=true` 的投影视图列出这些内置 policy，便于只使用 compatibility profile
列表面的 agent 自洽发现。

示意：

```json
[
  {
    "policy_ref": "calendar_policy.natural_yoy",
    "comparison_basis": "yoy",
    "window_tags": ["natural_date"],
    "use_when": ["普通同比", "未提节假日", "未提活动窗口"],
    "avoid_when": ["明确要求周几对周几", "明确要求节假日口径", "明确要求活动口径"]
  },
  {
    "policy_ref": "calendar_policy.weekday_yoy",
    "comparison_basis": "yoy",
    "window_tags": ["same_weekday"],
    "use_when": ["工作日效应强", "周一对周一", "周末对周末"],
    "avoid_when": ["明确要求节假日窗口", "明确要求活动窗口"]
  },
  {
    "policy_ref": "calendar_policy.holiday_yoy",
    "comparison_basis": "yoy",
    "window_tags": ["holiday_cluster", "same_weekday_fallback"],
    "use_when": ["春节", "清明", "国庆", "法定节假日"],
    "avoid_when": ["业务活动窗口优先"]
  },
  {
    "policy_ref": "calendar_policy.event_yoy",
    "comparison_basis": "yoy",
    "window_tags": ["event_cluster"],
    "use_when": ["618", "双11", "会员日", "大促"],
    "avoid_when": ["普通自然月同比", "仅法定节假日口径"]
  }
]
```

### Agent Routing Guidance

agent 在 v1 中的 policy resolution 应尽量保守：

1. 先按 `comparison_basis` 缩小候选集
2. 再按窗口语义选择 `natural` / `weekday` / `holiday` / `event`
3. 若同时命中多个强语义窗口，应要求澄清，而不是静默猜测
4. 只有当唯一候选成立时，才把结果绑定为正式 `calendar_policy_ref`

推荐规则：

- “同比” 只在 `*_yoy` 中选择
- “月环比 / 上月 / MoM” 只在 `*_mom` 中选择
- “周环比 / 上周 / WoW” 只在 `*_wow` 中选择
- 命中法定节假日词时优先考虑 `holiday_*`
- 命中业务活动词时优先考虑 `event_*`
- 命中“周一对周一 / 周末对周末”时优先考虑 `weekday_*`
- 若都未命中，则回退 `natural_*`

歧义示例：

- 同时命中公共节假日与业务活动窗口
- 要求月环比，同时又要求按漂移节假日窗口严格对齐
- 同时存在多个同样合法的活动窗口候选

这些情况应返回结构化歧义，而不是让 agent 自行猜测。

## 开放问题

以下问题仍待后续文档或实现阶段拍板：

- holiday cluster 与 business campaign calendar 是否共用同一 source contract，还是拆成公共节假日与业务活动两类

已冻结结论：

- `bucket_pairing plan` 在 v1 只作为 observation artifact 的 `resolved_policy_summary` metadata 输出，不升格为可单独引用的一等 artifact；见 `docs/semantic/calendar-bucket-pairing-artifact-decision.zh.md`
- 面向人工验收与回归核对的最小样例基线见 `docs/semantic/calendar-alignment-golden-cases.zh.md`

## 总结

一句话总结：

> 节假日 / weekday 对齐不是 `time.*`、不是 `time_scope`、不是 `binding` 的职责；它需要成为紧邻时间语义、但独立受治理的 `calendar_policy.*` 抽象，由 compiler / resolver 确定性展开成 baseline 与 bucket pairing，并把对齐失败显式暴露给 comparability gate。
