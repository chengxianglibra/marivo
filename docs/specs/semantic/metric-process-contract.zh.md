# Semantic Layer Metric 设计契约

> **过渡说明**：本文档已更新为反映 OSI Metric + MARIVO 扩展模型。原有的三层分工（metric / process object / intent）已简化为二层：metric 直接声明 measurement semantics（通过 `observed_dataset`、`observation_grain`、`additive_dimensions`、`aggregation_semantics`、`filters`），过程构造语义不再作为独立的 process object 类型存在。具体映射如下：
> - `process object` → 删除为独立类型；过程语义由 compiler / IR / 独立分析构造机制处理
> - `metric + process + intent` 三层 → `metric + intent` 二层；metric 通过 MARIVO 扩展直接声明完整度量语义
> - `binding` / `CarrierBinding` → 删除；物理接地由 Dataset/Field 行内表达
> - `process_ref` / `process.*` → 删除
> - `entity_ref` / `observed_entity_ref` → `observed_dataset`（OSI Dataset 引用）
> - `source_object_ref` → 删除；Dataset.source 替代
> - `primary_time_ref` / `primary_time_field` → 删除；时间语义由 AOI `time_scope.field` 解析
> - `additivity_constraints` → `additive_dimensions`（扁平列表）+ `aggregation_semantics`

本文整理 Marivo semantic layer 中 `metric` 的设计契约与度量语义边界。

本文是语义设计文档，不是当前实现说明，也不是 HTTP wire spec。外部 API 仍以 `docs/api/` 下文档为准；intent 设计以 `specs/analysis/intents/` 为准。配套 schema 细化见：

- `specs/semantic/dimension-schema-contract.zh.md`
- `specs/semantic/metric-v2-schema.zh.md`
- `specs/semantic/ir-schema-contract.zh.md`
- `specs/semantic/compiler-spec.zh.md`

以下文档已废弃（SUPERSEDED），其内容已整合到 OSI Dataset-native 模型中：

- ~~`specs/semantic/process-object-schema.zh.md`~~ → 过程语义不再作为独立类型
- ~~`specs/semantic/typed-binding-contract.zh.md`~~ → 物理接地由 Dataset/Field 行内表达
- ~~`specs/semantic/compiler-compatibility-profile.zh.md`~~ → 兼容性由 Dataset schema 直接校验

## Purpose

本文用于回答以下问题：

- 为什么 `metric` 应通过 MARIVO 扩展直接声明完整度量语义
- `metric` 与 `intent` 的分工与组合边界
- compiler / IR 应如何承接 metric + intent 的组合
- 哪些组合应被允许，哪些组合应被拒绝

本文不试图定义完整数据库 DDL，也不定义最终 HTTP endpoint 形状。

## 背景与问题

当前 semantic layer 已经在从 “SQL expression registry” 向 “typed analysis contract” 演进。在 OSI 模型下，metric 通过 MARIVO 扩展直接声明完整度量语义：

- `observed_dataset`：观测的 Dataset
- `observation_grain`：输出粒度
- `additive_dimensions`：可加维度列表
- `aggregation_semantics`：聚合语义
- `filters`：过滤语义

过程型分析（实验、留存、漏斗、session 等）的构造逻辑不再作为独立的 process object 类型，而是由 compiler / IR / 独立分析构造机制处理。metric 只负责”量什么”的 measurement semantics，不承担”总体如何构造”的过程逻辑。

这种设计避免了以下问题：

- `metric` schema 膨胀，既承载 measurement identity，又承载 process logic
- 很多字段并不适用于普通指标，导致契约既庞大又不清晰
- SQL 实现细节、业务语义、统计前提容易混在一起，难以治理

## 核心设计结论

推荐采用二层分工：

- `metric`：定义”量什么（what to measure）”，通过 OSI Dataset 引用 + MARIVO 扩展声明完整度量语义
- `intent`：定义”做什么分析动作（what analysis action to perform）”

对应地，semantic compiler / IR 负责：

- 将 `metric`、`intent` 编译为可执行的中间表示
- 对组合做显式兼容性校验
- 将下层执行细节降解为 engine-specific plan，而不把 SQL 暴露为外部契约

独立的 `dimension` contract 则负责提供共享分析轴，使 metric 的维度引用与请求维度解析都能围绕同一套 `dimension.*` refs 组合。

一句话总结：

> `metric` 负责 measurement semantics，`dimension` 负责 analysis axis semantics，`intent` 负责 analysis action semantics，IR 负责组合与降解。

## 非目标

本设计明确不追求：

- 把所有 ad hoc SQL 模式都提升为 typed object
- 为每个业务域都发明一个新的 intent
- 让任意 `metric` 在所有 intent 下无条件自由组合
- 让外部调用方直接操纵 join graph、CTE 结构、window SQL 细节

目标是建立一个**可校验、可治理、可扩展的组合子集**，而不是复制 SQL 的全部自由度。

## 语义对象分层

### 1. Metric

`metric` 是受治理的业务度量对象。它回答的是：

- 最终读出的值是什么
- 它的 measurement identity 是什么
- 它有哪些统计语义与运行边界

在 OSI + MARIVO 模型下，`metric` 应承载的典型信息包括：

- `metric_ref`
- `description`
- `observed_dataset`
- `observation_grain`
- `sample_kind`
- `value_semantics`
- `numerator / denominator contract`（适用于 rate 类指标）
- `additive_dimensions`
- `aggregation_semantics`
- `filters`（MARIVO 扩展）
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

### 2. ~~Process Object~~（已删除）

> **注意**：`process object` 作为独立类型已删除。过程语义（实验分析、留存分析、漏斗分析、session 分析等）的构造逻辑由 compiler / IR / 独立分析构造机制处理，不再作为与 metric 同级的独立语义对象。

### Dimension Axis

`dimension` 是共享分析轴对象。它回答的是：

- 分组 / 分解 / split 所使用的维度轴是什么
- 它的值域、层级与治理语义是什么

它不回答：

- 底层通过什么字段取值
- 某个 metric 是否已经允许它

因此：

- `metric` 在最小 public contract 中不直接枚举维度兼容矩阵；请求维度是否合法由 compiler 结合 dimension contract 与组合上下文判断
- Dataset 通过 Field.dimension 暴露稳定 dimension
- compiler 负责校验请求中的 `dimensions` 是否与上述契约兼容

### 2. Intent

`intent` 表示稳定的 typed analysis action，例如：

- `observe`
- `compare`
- `decompose`
- `detect`
- `test`
- `validate`
- `attribute`
- `diagnose`

`intent` 不应重定义 metric 语义。它只负责声明分析动作及其输出契约。

例如：

- `observe` 负责读取某个 measurement over a resolved process
- `compare` 负责比较两个 typed observations
- `validate` 负责把两个总体上的 observation preparation + hypothesis test 固化成稳定动作

## 为什么采用 OSI Metric + MARIVO 扩展模型

从三层分工（metric / process object / intent）简化为二层（metric + intent）有几个核心收益。

### 1. 降低 metric schema 膨胀

如果所有过程语义都塞进 `metric`，则每个普通指标也要携带大量并不适用的 funnel / cohort / session 字段。在 OSI 模型下，metric 只承载 measurement semantics，过程构造逻辑由 compiler / IR 处理：

- 普通指标保持简洁
- 过程型分析场景的构造逻辑不混入 metric 契约
- 文档、校验和 UI 都更容易理解

### 2. 让 intent 保持稳定

如果不采用简化的二层模型，系统很容易继续沿着”每个业务对象一个 intent”的方向膨胀，例如：

- `analyze_experiment`
- `analyze_funnel`
- `analyze_retention`
- `analyze_session`

这种做法会让 runtime 变成业务接口拼盘，而不是稳定的 typed analysis system。

在二层模型下，intent 可以继续保持较小而稳定的集合，过程语义由 compiler / IR 内部处理。

### 3. 让治理边界更清晰

二层分离后，系统可以分别回答：

- metric 是否可做统计检验
- 当前 intent 是否允许消费该 metric

例如：

- `conversion_rate` 声明自己是 `rate`
- `validate` 再校验该 metric 是否满足 `two_proportion_z` 的要求

### 4. 让 compiler 能承担复杂性，而不是把复杂性泄漏给外部契约

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

在 OSI + MARIVO 模型下，推荐最小字段族如下：

```ts
type MetricContract = {
  metric_ref: string;
  description?: string | null;
  observed_dataset: string;  // OSI Dataset 引用
  observation_grain: string;  // OSI grain 概念
  sample_kind: “numeric” | “rate”;
  value_semantics: “count” | “sum” | “ratio” | “mean” | “percentile” | “score”;
  numerator?: MeasurementComponent | null;
  denominator?: MeasurementComponent | null;
  additive_dimensions: string[];  // 扁平列表，替代旧 additivity_constraints
  aggregation_semantics: “sum” | “ratio” | “weighted_average”;  // default “sum”
  filters?: string[] | null;  // MARIVO 扩展：过滤语义引用
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

## ~~Process Object Contract 边界~~（已删除）

> **注意**：`process object` 作为独立类型已删除。以下内容保留作为历史参考，但不再代表当前设计。
>
> 过程型分析的构造逻辑（实验分析、留存分析、漏斗分析、session 分析、path / sequence 分析、生命周期迁移分析等）由 compiler / IR / 独立分析构造机制处理，不再作为独立的 process object 类型存在。
>
> 原有的 `ProcessObjectHeader`、`ProcessInterfaceContract` 等结构已不再使用。metric 通过 `observed_dataset`、`observation_grain`、`additive_dimensions`、`aggregation_semantics`、`filters` 等 MARIVO 扩展字段直接声明度量语义。

## Intent 如何消费 Metric

在 OSI 模型下，intent 直接消费 metric，无需通过 process object 中间层：

- `metric` 是 measurement anchor
- `intent` 只消费已解析的 typed inputs，不消费任意 SQL shape

示意：

```json
{
  "intent": "observe",
  "metric": "completion_rate"
}
```

或：

```json
{
  "intent": "validate",
  "metric": "conversion_rate"
}
```

对于 `compare` intent，可以通过 dataset-level filter 表达左右总体：

```json
{
  "intent": "compare",
  "metric": "retention_rate",
  "left_filter": "filter.cohort_signup_week_2026_03_01",
  "right_filter": "filter.cohort_signup_week_2026_02_22"
}
```

## IR 的职责

IR（intermediate representation）不是外部 contract，而是 compiler 的稳定内部目标。

IR 需要能承接以下两类语义：

- measurement semantics
- analysis action semantics

推荐把 IR 视为受类型约束的计划图，而不是 SQL AST 的直接别名。

### IR 至少需要表达的节点族

```ts
type IrNode =
  | MeasurementNode
  | IntentNode;
```

其中：

- `MeasurementNode`：分子/分母、聚合、sample summary、value semantics
- `IntentNode`：observe、compare、test、decompose 等动作

同时，IR 还必须显式表达：

- `IrArtifact`：canonical artifact 声明与 lineage
- `InputBinding` / `OutputBinding`：artifact 的 typed wiring
- `CompileReport`：validation trace 与 lowerability precheck
- `LoweringReport`：engine capability / rewrite / materialization 决策

### IR 能否支持任意组合

原则上，IR 应能承接二者的**统一表示**；但系统不应承诺”任意组合都合法”。

更合理的目标是：

- 允许统一编译
- 允许显式校验
- 仅执行受支持的可编译子集

也就是说，IR 的职责不是“让所有组合都能跑”，而是“让所有组合都能被解释、被验证、被明确接受或拒绝”。

## 组合规则：允许什么，不允许什么

### 合法组合的判断标准

一个组合至少需要同时满足：

- `metric` 支持该 intent
- 二者 grain / dataset semantics 相容
- intent 所需的统计或比较前提可满足
- runtime 可为其生成受支持的 execution plan

### 典型拒绝场景

以下组合应被显式拒绝，而不是隐式退化：

- `decompose` 一个 `non_additive` 的 rate metric，但要求做 additive delta 对账
- `validate` 一个无法产出 inferential-ready sample summary 的 metric
- `compare` 两个 comparability contract 不兼容的 metric
- `detect` 一个无法稳定构造成 time series 的指标

错误应反映**语义不兼容**，而不是暴露底层 SQL 失败。

## 常见分析场景示例

### 1. A/B 实验转化提升

组合：

- `metric = conversion_rate`
- `intent = validate`

示意：

```json
{
  “intent”: “validate”,
  “metric”: “conversion_rate”
}
```

语义分工：

- `metric` 定义转化率是一个 `rate`，并声明默认 inferential contract
- `intent` 固化”验证差异是否显著”的分析动作

IR 负责：

- 解析 experiment split context（通过 Dataset 属性）
- 构造 left / right population
- 做 dedup 与 contamination exclusion
- 生成 rate sample summary
- 下推到 `test`

### 2. 新用户 7 日留存

组合：

- `metric = retention_rate`
- `intent = compare`

示意：

```json
{
  “intent”: “compare”,
  “metric”: “retention_rate”,
  “left_filter”: “filter.cohort_signup_week_2026_03_01”,
  “right_filter”: “filter.cohort_signup_week_2026_02_22”
}
```

语义分工：

- `metric` 负责 retention measurement semantics
- `compare` 负责比较两个 cohort observation

IR 负责：

- 构造 cohort membership（通过 Dataset filter）
- 解析 day-7 回访
- 生成两侧 observation
- 检查 comparability

### 3. 支付漏斗优化

组合：

- `metric = completion_rate`
- `intent = attribute`

示意：

```json
{
  “intent”: “attribute”,
  “metric”: “completion_rate”,
  “dimensions”: [“traffic_source”, “device_type”]
}
```

语义分工：

- `metric` 定义要看 completion rate
- `attribute` 负责解释变化来源

这里尤其要注意：

- 若目标指标是 non-additive ratio，则 `decompose` / `attribute` 不能假定 additive 对账关系
- 必要时应要求调用方改用可加和底层业务量，或等待专门的 rate attribution contract

### 4. 视频会话质量监控

组合：

- `metric = avg_watch_time`
- `intent = detect`

示意：

```json
{
  “intent”: “detect”,
  “metric”: “avg_watch_time”
}
```

语义分工：

- `metric` 定义 watch time 的 measurement semantics
- `detect` 负责扫描异常候选

IR 负责：

- sessionize（通过 Dataset 结构推导）
- 计算 session-level observation
- 将其投影为可检测的 time series

### 5. 生命周期迁移分析

组合：

- `metric = user_count`
- `intent = observe`

示意：

```json
{
  “intent”: “observe”,
  “metric”: “user_count”,
  “dimensions”: [“state”]
}
```

语义分工：

- `metric` 负责计数
- `observe` 负责读取分段结果

## 兼容性与校验建议

为避免“表面可以组合、运行时才报 SQL 错误”，semantic layer 应在 compiler 前提供显式的 compatibility validation。

推荐至少校验以下维度：

- `observed_dataset` 是否一致
- grain 是否兼容
- `sample_kind` 是否满足 intent 前提
- `additive_dimensions` 是否满足 `decompose` / `attribute` 前提
- Dataset 是否支持时间序列化
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

先把 measurement identity、comparability、additive_dimensions、aggregation_semantics、inferential capability 从 `definition_sql` 中剥离出来，形成更完整的 metric contract。同时引入 `observed_dataset`、`observation_grain`、`filters` 等 MARIVO 扩展字段。

### 第二阶段：移除 process object 依赖

将原有依赖 `process_ref` 的逻辑迁移到 Dataset-native 模型：

- `process_ref` → Dataset-level filter 或 Metric.filters
- `CarrierBinding.row_filter_refs` → Dataset-level row filter
- `source_object_ref` → Dataset.source

### 第三阶段：让 intents 消费 dataset-aware inputs

优先打通：

- `observe`
- `compare`
- `test`
- `validate`
- `attribute`

### 第四阶段：收敛 compiler / IR 兼容性规则

将允许组合、拒绝组合、gate 执行点做成显式规则，而不是散落在各 intent 的 ad hoc 逻辑中。

## 总结

对于 Marivo semantic layer，更合适的演进方向不是继续扩张 `metric` 或为每类业务对象发明新 intent，而是建立如下稳定分工：

- `metric`：受治理的 measurement contract（OSI + MARIVO 扩展）
- `intent`：稳定的 analysis action contract
- IR：统一组合、校验与降解的内部表示

这套分工的核心收益是：

- 减少 schema 膨胀
- 保持 intent 稳定
- 把复杂性收敛到 compiler / IR
- 让语义校验先于执行失败

一句话总结：

> Marivo 应把”量什么”与”做什么分析动作”分离为二类稳定契约，由 metric 通过 MARIVO 扩展直接声明度量语义，IR 承接其受约束组合，而不是通过独立 process object 或把它们重新混回 SQL 中。
