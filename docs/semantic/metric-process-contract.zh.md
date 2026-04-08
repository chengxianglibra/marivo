# Semantic Layer Metric / Process Object 设计契约

本文整理 Factum semantic layer 中 `metric` 与 `process object` 的目标分工、组合方式与设计边界。

本文是语义设计文档，不是当前实现说明，也不是 HTTP wire spec。外部 API 仍以 `docs/api/` 下文档为准；intent 设计以 `docs/analysis/intents/` 为准。配套 schema 细化见：

- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`
- `docs/semantic/compiler-spec.zh.md`

## Purpose

本文用于回答以下问题：

- 为什么 `metric` 不应继续承载全部过程语义
- 哪些过程型分析对象应提升为与 `metric` 同级的原生语义对象
- `metric`、`process object`、`intent` 三者分别负责什么
- compiler / IR 应如何承接三者组合
- 哪些组合应被允许，哪些组合应被拒绝

本文不试图定义完整数据库 DDL，也不定义最终 HTTP endpoint 形状。

## 背景与问题

当前 semantic layer 已经在从 “SQL expression registry” 向 “typed analysis contract” 演进，但仅靠 `metric` 仍难稳定表达一类常见分析需求：

- 实验分析
- 留存分析
- 漏斗分析
- session 分析
- path / sequence 分析
- 生命周期迁移分析

这些场景的共同点是：除了“量什么”，还需要稳定表达“总体如何构造”“过程如何判定”“事件如何归并”“阶段如何识别”。这类信息如果继续塞进单个 `metric`，会带来几个问题：

- `metric` schema 膨胀，既承载 measurement identity，又承载 process logic
- 很多字段并不适用于普通指标，导致契约既庞大又不清晰
- intent runtime 难以判断某个分析请求是在请求一个指标，还是在请求一套过程构造规则
- SQL 实现细节、业务语义、统计前提容易混在一起，难以治理

因此，Factum 需要把“过程型分析对象（process-oriented analysis objects）”提升为与 `metric` 同级的原生语义对象。

## 核心设计结论

推荐采用三层分工：

- `metric`：定义“量什么（what to measure）”
- `process object`：定义“总体/阶段/路径如何构造（how to construct the analytical population or process state）”
- `intent`：定义“做什么分析动作（what analysis action to perform）”

对应地，semantic compiler / IR 负责：

- 将 `metric`、`process object`、`intent` 编译为可执行的中间表示
- 对组合做显式兼容性校验
- 将下层执行细节降解为 engine-specific plan，而不把 SQL 暴露为外部契约

独立的 `dimension` contract 则负责提供共享分析轴，使 `process.exported_dimension_refs`、`entity.stable_descriptors` 与请求维度解析都能围绕同一套 `dimension.*` refs 组合。

一句话总结：

> `metric` 负责 measurement semantics，`process object` 负责 process semantics，`dimension` 负责 analysis axis semantics，`intent` 负责 analysis action semantics，IR 负责组合与降解。

typed binding contract 只负责把这些既有语义对象落到物理层，并补充 late arrival / incomplete-window / imports 等消费侧治理；它不重新定义 process window 本体。

## 非目标

本设计明确不追求：

- 把所有 ad hoc SQL 模式都提升为 typed object
- 为每个业务域都发明一个新的 intent
- 让任意 `metric` 与任意 `process object` 无条件自由组合
- 让外部调用方直接操纵 join graph、CTE 结构、window SQL 细节

目标是建立一个**可校验、可治理、可扩展的组合子集**，而不是复制 SQL 的全部自由度。

## 语义对象分层

### 1. Metric

`metric` 是受治理的业务度量对象。它回答的是：

- 最终读出的值是什么
- 它的 measurement identity 是什么
- 它有哪些统计语义与运行边界

`metric` 应承载的典型信息包括：

- `metric_ref`
- `description`
- `subject_key_semantics`
- `observation_unit`
- `sample_kind`
- `value_semantics`
- `numerator / denominator contract`（适用于 rate 类指标）
- `additivity`
- `metric_contract_version`

`metric` 不应直接承载以下内容：

- 某次分析所需的临时总体构造逻辑
- 具体实验 treatment / control 的样本解析方式
- funnel step matching 的完整过程规则
- sessionization 的具体 idle gap / close policy 实现细节
- engine-specific SQL 结构本身作为外部主语义
- 全局 `supported_intents`
- 全局 `supported_result_modes`
- compiler 才消费的 capability / inference / freshness profiles

### 2. Process Object

`process object` 是过程型原生分析对象。它回答的是：

- 观测总体如何构造
- 阶段、路径、session、cohort 如何被确定
- 哪些事件或状态属于同一个分析过程

它不是一个值，而是一个**稳定的分析过程契约**。

推荐的 process object 候选类型包括：

- `experiment_context`
- `cohort_definition`
- `funnel_definition`
- `session_contract`
- `path_pattern`
- `lifecycle_state_machine`

这些对象与 `metric` 同级，而不是 `metric` 的子字段集合。

### Dimension Axis

`dimension` 是共享分析轴对象。它回答的是：

- 分组 / 分解 / split 所使用的维度轴是什么
- 它的值域、层级与治理语义是什么

它不回答：

- 底层通过什么字段取值
- 某个 metric 是否已经允许它
- 某个 process 如何生成它

因此：

- `metric` 在最小 public contract 中不直接枚举维度兼容矩阵；请求维度是否合法由 compiler 结合 dimension contract 与组合上下文判断
- `process object` 通过 `exported_dimension_refs` 导出 dimension
- `entity` 通过 `stable_descriptors` 暴露稳定 dimension
- compiler 负责校验请求中的 `dimensions` 是否与上述契约兼容

### 3. Intent

`intent` 表示稳定的 typed analysis action，例如：

- `observe`
- `compare`
- `decompose`
- `detect`
- `test`
- `validate`
- `attribute`
- `diagnose`

`intent` 不应重定义 metric 语义或 process 语义。它只负责声明分析动作及其输出契约。

例如：

- `observe` 负责读取某个 measurement over a resolved process
- `compare` 负责比较两个 typed observations
- `validate` 负责把两个总体上的 observation preparation + hypothesis test 固化成稳定动作

## 为什么要把 process object 提升为与 metric 同级

这样分工有几个核心收益。

### 1. 降低 metric schema 膨胀

如果所有过程语义都塞进 `metric`，则每个普通指标也要携带大量并不适用的 funnel / cohort / session 字段。结果会让 `metric` 既像 measurement contract，又像 workflow spec。

把 process object 独立出来后：

- 普通指标保持简洁
- 过程型分析场景获得一等表达能力
- 文档、校验和 UI 都更容易理解

### 2. 提高复用性

同一个 process object 可以服务多个 metric：

- `funnel.checkout` 可搭配 `completion_rate`、`dropoff_rate`、`median_step_latency`
- `experiment.exp_123` 可搭配 `conversion_rate`、`crash_rate`、`latency_p95`
- `cohort.signup_week` 可搭配 `retention_rate`、`avg_watch_time`、`payer_rate`

同一个 metric 也可以运行在多个 process object 上：

- `user_count` 可以观察不同 lifecycle state
- `completion_rate` 可以作用于不同 funnel
- `avg_watch_time` 可以作用于不同 session contract

### 3. 让 intent 保持稳定

如果不引入 process object，系统很容易继续沿着“每个业务对象一个 intent”的方向膨胀，例如：

- `analyze_experiment`
- `analyze_funnel`
- `analyze_retention`
- `analyze_session`

这种做法会让 runtime 变成业务接口拼盘，而不是稳定的 typed analysis system。

将过程语义上升为原生对象后，intent 可以继续保持较小而稳定的集合。

### 4. 让治理边界更清晰

三层分离后，系统可以分别回答：

- metric 是否可做统计检验
- process object 是否能稳定构造总体
- 当前 intent 是否允许消费该组合

例如：

- `conversion_rate` 声明自己是 `rate`
- `experiment_context` 声明自己提供 experiment split context
- `validate` 再校验该组合是否满足 `two_proportion_z` 的要求

### 5. 让 compiler 能承担复杂性，而不是把复杂性泄漏给外部契约

真正复杂的地方通常不在外部 API，而在：

- join path
- 去重策略
- windowing
- cohort materialization
- session boundary resolution
- sequence matching

这些更适合在 compiler / IR 层处理，而不是暴露成外部 schema 字段让 agent 或用户直接填写。

## Metric Contract 边界

### Metric 必须表达的语义

凡是影响以下问题的内容，应属于 `metric` contract：

- “这个 metric 到底是什么”
- “两个 observation 是否可以比较”
- “该 metric 是否可以做某类检验”
- “该 metric 是否允许某类分解”

推荐最小字段族如下：

```ts
type MetricContract = {
  metric_ref: string;
  description?: string | null;
  population_subject_ref?: string | null;
  observed_entity_ref: string;
  observation_grain_ref: string;
  sample_kind: "numeric" | "rate" | "binary" | "survival";
  value_semantics: "count" | "sum" | "ratio" | "mean" | "percentile" | "score";
  numerator?: MeasurementComponent | null;
  denominator?: MeasurementComponent | null;
  additivity: "additive" | "semi_additive" | "non_additive";
  metric_contract_version: string;
};
```

### Metric 不应直接表达的语义

以下内容原则上不应成为 `metric` 的外部主契约：

- 临时样本构造 DAG
- assignment / exposure join plan
- event sequence matching plan
- session close implementation
- path search strategy
- 底层 SQL 文本本身

必要时可保留受控的 execution payload 或 legacy SQL fragment，但它只应是**实现细节**，不是外部 semantic identity。

## Process Object Contract 边界

### Process Object 的设计原则

`process object` 的本质不是“另一个指标”，而是“分析过程语义对象”。

它应满足：

- 过程语义稳定
- 可被多个 intent 复用
- 可被多个 metric 复用
- 具有独立版本
- 能声明自己的输入前提与输出形状

推荐最小公共头部如下：

```ts
type ProcessObjectHeader = {
  process_ref: string;
  process_type:
    | "experiment_context"
    | "cohort_definition"
    | "funnel_definition"
    | "session_contract"
    | "path_pattern"
    | "lifecycle_state_machine";
  description?: string | null;
  process_contract_version: string;
};

type ProcessInterfaceContract =
  | {
      contract_mode: "context_provider";
      context_kind: "cohort_membership" | "experiment_split";
      population_subject_ref: string;
      membership_cardinality: "exclusive_one" | "repeatable_many";
      anchor_time_ref?: string | null;
      exported_dimension_refs?: string[] | null;
    }
  | {
      contract_mode: "entity_stream";
      entity_ref: string;
      emitted_grain_ref: string;
      population_subject_ref: string;
      subject_cardinality: "one" | "many";
      anchor_time_ref?: string | null;
      exported_dimension_refs?: string[] | null;
    };
```

若需要 `supported_intents`、`result_modes`、`capabilities`、`inference support` 等更细粒度组合规则，应作为 compiler compatibility profile 单独维护，而不是继续塞回 public object schema；其发布形态与边界见 `docs/semantic/compiler-compatibility-profile.zh.md`。

### 各类 Process Object 示例

#### 1. Experiment Context

用于稳定定义实验分析总体的构造语义，例如：

- assignment 还是 exposure 作为归因基础
- first exposure 还是 last exposure
- contamination exclusion
- attribution window
- variant split context

它不直接表示“转化率”或“崩溃率”，而是表示这些指标要作用在哪个实验语义上下文中。

assignment / exposure 的字段映射、跨 source join、迟到数据处理则属于 typed binding contract，而不是 experiment context 本体字段。

#### 2. Cohort Definition

用于稳定定义 cohort 的进入条件、锚点时间与回访判定，例如：

- signup day-0 cohort
- first purchase cohort
- first activation cohort

它可被 `observe`、`compare`、`validate` 等复用。

#### 3. Funnel Definition

用于定义步骤序列及其匹配约束，例如：

- step sequence
- ordering rule
- max step gap
- counting rule
- conversion anchor

它不直接定义指标值，而是定义漏斗过程。

#### 4. Session Contract

用于定义 session 的归并规则，例如：

- session start condition
- idle gap
- session close rule
- event inclusion / exclusion

这类对象适合支撑 watch-time、session depth、bounce rate 等分析。

#### 5. Path Pattern

用于定义序列型分析对象，例如：

- A -> B -> C 路径
- 允许/禁止重复 step
- same-session 或 cross-session 约束
- max lag

#### 6. Lifecycle State Machine

用于定义状态迁移，例如：

- `new -> active -> loyal -> churn_risk`
- 状态进入条件
- 状态退出条件
- 优先级与冲突消解

## Intent 如何消费 Metric 与 Process Object

推荐有两种稳定方式：

- `metric + process`
- `analysis_object_ref + metric`

其中：

- `metric` 仍是 measurement anchor
- `process` 是 population / process semantics anchor
- `intent` 只消费已解析的 typed inputs，不消费任意 SQL shape

示意：

```json
{
  "intent": "observe",
  "metric": "completion_rate",
  "process": "funnel.checkout"
}
```

或：

```json
{
  "intent": "validate",
  "metric": "conversion_rate",
  "process": "experiment.exp_123"
}
```

对于某些 intent，也可以允许显式 `left_process` / `right_process`：

```json
{
  "intent": "compare",
  "metric": "retention_rate",
  "left_process": "cohort.signup_week_2026_03_01",
  "right_process": "cohort.signup_week_2026_02_22"
}
```

## IR 的职责

IR（intermediate representation）不是外部 contract，而是 compiler 的稳定内部目标。

IR 需要能承接以下三类语义：

- measurement semantics
- process semantics
- analysis action semantics

推荐把 IR 视为受类型约束的计划图，而不是 SQL AST 的直接别名。

### IR 至少需要表达的节点族

```ts
type IrNode =
  | MeasurementNode
  | ProcessNode
  | IntentNode;
```

其中：

- `MeasurementNode`：分子/分母、聚合、sample summary、value semantics
- `ProcessNode`：cohort build、sessionize、funnelize、path match、state resolve
- `IntentNode`：observe、compare、test、decompose 等动作

同时，IR 还必须显式表达：

- `IrArtifact`：canonical artifact 声明与 lineage
- `InputBinding` / `OutputBinding`：typed ref 的 slot-aware wiring
- `CompileReport`：validation trace 与 lowerability precheck
- `LoweringReport`：engine capability / rewrite / materialization 决策

### IR 能否支持三者任意组合

原则上，IR 应能承接三者的**统一表示**；但系统不应承诺“任意组合都合法”。

更合理的目标是：

- 允许统一编译
- 允许显式校验
- 仅执行受支持的可编译子集

也就是说，IR 的职责不是“让所有组合都能跑”，而是“让所有组合都能被解释、被验证、被明确接受或拒绝”。

## 组合规则：允许什么，不允许什么

### 合法组合的判断标准

一个组合至少需要同时满足：

- `metric` 支持该 intent
- `process object` 支持该 intent
- 二者 grain / subject semantics 相容
- intent 所需的统计或比较前提可满足
- runtime 可为其生成受支持的 execution plan

### 典型拒绝场景

以下组合应被显式拒绝，而不是隐式退化：

- `decompose` 一个 `non_additive` 的 rate metric，但要求做 additive delta 对账
- `validate` 一个无法产出 inferential-ready sample summary 的 path metric
- `compare` 两个 comparability contract 不兼容的 process interfaces
- `detect` 一个 process interface 无法稳定构造成 time series 的指标

错误应反映**语义不兼容**，而不是暴露底层 SQL 失败。

## 常见分析场景示例

### 1. A/B 实验转化提升

组合：

- `metric = conversion_rate`
- `process = experiment.exp_123`
- `intent = validate`

示意：

```json
{
  "intent": "validate",
  "metric": "conversion_rate",
  "process": "experiment.exp_123"
}
```

语义分工：

- `metric` 定义转化率是一个 `rate`，并声明默认 inferential contract
- `process` 定义 experiment split context 如何解析，assignment 还是 exposure，窗口多大
- `intent` 固化“验证差异是否显著”的分析动作

IR 负责：

- 解析 experiment split context
- 构造 left / right population
- 做 dedup 与 contamination exclusion
- 生成 rate sample summary
- 下推到 `test`

### 2. 新用户 7 日留存

组合：

- `metric = retention_rate`
- `process = cohort.signup_week`
- `intent = compare`

示意：

```json
{
  "intent": "compare",
  "metric": "retention_rate",
  "left_process": "cohort.signup_week_2026_03_01",
  "right_process": "cohort.signup_week_2026_02_22"
}
```

语义分工：

- `metric` 负责 retention measurement semantics
- `cohort_definition` 负责谁是 cohort member、什么是 day-0、什么是回访
- `compare` 负责比较两个 cohort observation

IR 负责：

- 构造 cohort membership
- 解析 day-7 回访
- 生成两侧 observation
- 检查 comparability

### 3. 支付漏斗优化

组合：

- `metric = completion_rate`
- `process = funnel.checkout`
- `intent = attribute`

示意：

```json
{
  "intent": "attribute",
  "metric": "completion_rate",
  "process": "funnel.checkout",
  "dimensions": ["traffic_source", "device_type"]
}
```

语义分工：

- `metric` 定义要看 completion rate
- `funnel_definition` 定义步骤序列与匹配规则
- `attribute` 负责解释变化来源

这里尤其要注意：

- 若目标指标是 non-additive ratio，则 `decompose` / `attribute` 不能假定 additive 对账关系
- 必要时应要求调用方改用可加和底层业务量，或等待专门的 rate attribution contract

### 4. 视频会话质量监控

组合：

- `metric = avg_watch_time`
- `process = session.video_session`
- `intent = detect`

示意：

```json
{
  "intent": "detect",
  "metric": "avg_watch_time",
  "process": "session.video_session"
}
```

语义分工：

- `metric` 定义 watch time 的 measurement semantics
- `session_contract` 定义 session 如何切分
- `detect` 负责扫描异常候选

IR 负责：

- sessionize
- 计算 session-level observation
- 将其投影为可检测的 time series

### 5. 生命周期迁移分析

组合：

- `metric = user_count`
- `process = lifecycle.user_states`
- `intent = observe`

示意：

```json
{
  "intent": "observe",
  "metric": "user_count",
  "process": "lifecycle.user_states",
  "dimensions": ["state"]
}
```

语义分工：

- `metric` 负责计数
- `lifecycle_state_machine` 负责把用户映射到稳定状态
- `observe` 负责读取分段结果

## 兼容性与校验建议

为避免“表面可以组合、运行时才报 SQL 错误”，semantic layer 应在 compiler 前提供显式的 compatibility validation。

推荐至少校验以下维度：

- `population_subject_ref` 是否一致
- grain 是否兼容
- `sample_kind` 是否满足 intent 前提
- `additivity` 是否满足 `decompose` / `attribute` 前提
- process interface 是否支持时间序列化
- process interface 是否满足 comparability gate
- quality gate / freshness gate 是否通过

这类校验应优先给出 typed、可解释的错误，而不是底层 engine 异常。

## 与 SQL / Execution Payload 的关系

本文不主张完全移除底层 SQL 或执行模板。

更合理的关系是：

- typed contract 是外部主语义
- IR 是内部稳定编译目标
- SQL / engine plan 是最终执行产物

必要时可以保留以下受控能力：

- legacy `definition_sql`
- engine-specific execution kernel
- 特殊 UDF / sketch / percentile 实现

但这些能力不应决定对象的外部 semantic identity，也不应绕过 typed validation。

## 迁移建议

建议按以下顺序演进：

### 第一阶段：稳定 metric contract

先把 measurement identity、comparability、additivity、inferential capability 从 `definition_sql` 中剥离出来，形成更完整的 metric contract。

### 第二阶段：引入最小 process object 集合

优先引入高价值、复用度高的对象：

- `experiment_context`
- `cohort_definition`
- `funnel_definition`

### 第三阶段：让 primitive / derived intents 消费 process-aware inputs

优先打通：

- `observe`
- `compare`
- `test`
- `validate`
- `attribute`

### 第四阶段：收敛 compiler / IR 兼容性规则

将允许组合、拒绝组合、gate 执行点做成显式规则，而不是散落在各 intent 的 ad hoc 逻辑中。

## 总结

对于 Factum semantic layer，更合适的演进方向不是继续扩张 `metric`，也不是为每类业务对象发明新 intent，而是建立如下稳定分工：

- `metric`：受治理的 measurement contract
- `process object`：受治理的 process contract
- `intent`：稳定的 analysis action contract
- IR：统一组合、校验与降解的内部表示

这套分工的核心收益是：

- 减少 schema 膨胀
- 提高复用性
- 保持 intent 稳定
- 把复杂性收敛到 compiler / IR
- 让语义校验先于执行失败

一句话总结：

> Factum 应把“量什么”“过程如何构造”“做什么分析动作”分离为三类稳定契约，并由 IR 承接其受约束组合，而不是把它们重新混回 SQL 或单一 metric schema 中。
