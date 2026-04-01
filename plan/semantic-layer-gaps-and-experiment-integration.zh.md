# Semantic Layer 现状问题、互联网场景缺口与实验分析集成方案

## 1. 文档目的

本文整理当前 Factum semantic layer 设计在复杂互联网企业数据分析场景中的主要不足，并给出面向演进的改造方向。

文档重点覆盖三部分内容：

- “现状问题 → 互联网场景例子 → 建议改造方向”的系统清单
- 以实验分析（A/B Testing / Experiment Analysis）为例，说明实验相关原生语义对象应如何绑定到底层物理表列 schema
- 说明这些实验相关对象如何与现有 intent 体系集成，而不是为每个业务对象额外发明一套新的原子 intent

本文是设计提案，不表示对应 API 或 runtime 已经实现。

---

## 2. 结论摘要

当前 semantic layer 已经具备最基础的 `entity / metric / mapping` 三类对象，以及围绕 typed intents 的分析主路径，但它仍更接近“指标注册表 + 物理映射层”，还不是一个足以承载复杂互联网分析任务的“分析语义系统”。

在复杂企业场景下，语义层通常需要原生承载：

- 业务对象语义：experiment、variant、assignment、exposure、cohort、segment、funnel、lifecycle_state
- 分析规则语义：可比性、归因窗口、去重主体、统计假设、样本约束、freshness、口径适用边界
- 执行绑定语义：键列、时间列、分区列、事件条件、join 路径、迟到数据规则、快照版本
- 治理语义：权限、脱敏、预算、quality gate、发布版本、变更兼容性

如果这些能力缺失，系统就会退化为：

- metric 只是一段 `definition_sql`
- 业务场景只能靠 scope 过滤拼出来
- 分析口径分散在 step schema、调用方约定、SQL 片段和人工知识里

这会直接导致互联网企业最常见的一批分析任务难以原生支持：实验分析、归因漏斗、留存、路径、会话、生命周期分析、指标拆解。

---

## 3. 当前 semantic layer 的主要设计特征

基于当前仓库实现，semantic layer 目前的主要模型特征是：

- 语义对象主要为 `semantic_entities`、`semantic_metrics`、`semantic_mappings`
- metric 的核心定义方式是 `definition_sql`
- 维度能力主要体现在 `dimensions` 与 `allowed_dimensions`
- 运行时主要通过“已发布 metric + object mapping”解析 `observe` 等 intents
- intent 层以通用 typed analysis intents 为主：`observe / compare / decompose / correlate / detect / test / forecast / attribute / diagnose / validate`

这个方向对于“把 SQL 从外部契约中隐藏起来”是对的，但在业务语义承载层面仍然不够。

---

## 4. 系统清单：现状问题 → 互联网场景例子 → 建议改造方向

### 4.1 语义对象类型过少

#### 现状问题

当前一等对象主要只有：

- entity
- metric
- mapping

缺少独立建模的：

- dimension
- segment / cohort
- experiment family objects
- funnel / path / lifecycle
- policy / quality rule / comparability contract

这意味着很多关键业务概念只能被“挤压”进：

- metric 的属性字段
- entity 的 properties
- mapping_json
- step-level scope predicate

#### 互联网场景例子

- 实验平台里需要稳定表达 `experiment / variant / assignment / exposure / cohort / guardrail metric`
- 增长分析里需要表达“新用户 7 日留存 cohort”
- 推荐分析里需要表达“曝光 → 点击 → 下单”的有序 funnel
- 用户行为分析里需要表达 path、session、生命周期状态机

如果没有这些对象，最终只能退回到“给某个指标加上若干过滤条件”。

#### 建议改造方向

在 semantic layer 中增加一层“场景原生语义对象（domain-native semantic objects）”，并允许其：

- 有自己的 schema
- 有自己的物理绑定规则
- 有自己的校验逻辑
- 被 intent 编译器作为 typed context 消费

建议至少补充：

- semantic_dimensions
- semantic_segments
- semantic_experiments
- semantic_variants
- semantic_assignments
- semantic_exposures
- semantic_cohorts
- semantic_metric_roles（如 primary metric、guardrail metric）

---

### 4.2 指标定义过度依赖 `definition_sql`

#### 现状问题

当前 metric 的核心是：

- 名称
- 说明
- `definition_sql`
- 默认维度
- 允许维度

这会让 metric 更像“聚合 SQL 模板”，而不是“受治理的业务度量对象”。

主要问题：

- 不利于跨引擎编译
- 不利于解释其统计含义
- 难以表达分子/分母、主体粒度、样本口径、归因窗口
- 难以表达指标可比性与适用边界
- 难以表达 additive / semi-additive / non-additive 的运行限制

#### 互联网场景例子

- `conversion_rate` 不能只是一句 `SUM(converted) / COUNT(*)`
- 实验转化率需要明确：
  - 分析主体是 user 还是 session
  - 归因依据是 assignment 还是 exposure
  - 首曝后多少天内算转化
  - 是否去除污染样本
- `retention_7d` 不能只是一句聚合 SQL，还需要 cohort day-0 定义与回访事件定义

#### 建议改造方向

把 metric 从“SQL 表达式”提升为“typed measurement contract”。

建议增加如下语义字段：

- subject_key_semantics：分析主体键
- observation_unit：user / device / session / order
- numerator / denominator contract
- sample_kind：numeric / rate / binary / survival
- additivity：additive / semi_additive / non_additive
- attribution_basis：assignment / exposure / event-time / session-time
- attribution_window
- comparability_group / metric_family
- required_dimensions / forbidden_dimensions
- freshness_tolerance / quality gate refs

`definition_sql` 可以保留为底层执行表达，但不应再是外部主语义。

---

### 4.3 mapping 过粗，无法支撑复杂 join 与多事实口径

#### 现状问题

当前 mapping 更接近：

- 某个 semantic object 绑定某个 source object

但企业分析真实需要的绑定通常远不止于“对象 → 表”：

- 需要哪个主键
- 用哪个时间列
- 用哪个分区列
- 过滤什么事件名
- 如何 join assignment / exposure / orders
- 是明细表、快照表、汇总表还是宽表
- 若有多个候选事实表，哪张是 canonical source

#### 互联网场景例子

- 实验转化分析通常至少涉及：
  - assignment 表
  - exposure 事件表
  - conversion 事件表或订单表
  - 用户维表
- 归因分析可能还要引入渠道表、设备表、实验配置表
- 多业务线里同一个 metric 可能在实时明细表与离线汇总表上都有实现

#### 建议改造方向

把 mapping 从“对象到表”的绑定，升级为“语义对象到可执行绑定图（binding graph）”。

建议 binding schema 至少包含：

- source_object_ref
- role（primary_fact / dimension_lookup / assignment_log / exposure_log / snapshot_source）
- key_columns
- time_columns
- partition_columns
- row_predicate
- join_edges
- late_arrival_policy
- snapshot_policy
- preferred_engine

这样编译器拿到的就不只是 `object_id`，而是一套可验证、可解释、可选择的执行绑定。

---

### 4.4 维度能力过弱，难以承载共享口径与 drill path

#### 现状问题

当前维度主要体现为字符串数组：

- `dimensions`
- `allowed_dimensions`

这对于简单分组足够，但不够表达：

- 维度类型（枚举、层级、时间衍生、标签、人群）
- 维度来源列与 join 路径
- 可钻取路径
- 维度取值治理与枚举版本
- 是否能用于 compare、decompose、detect、experiment split

#### 互联网场景例子

- “渠道”可能存在：
  - 一级渠道
  - 二级渠道
  - 投放账户
  - campaign
- “地区”可能存在：
  - country
  - province
  - city
- “用户分群”可能存在：
  - 新老用户
  - 高价值用户
  - 实验前高活跃用户

这些都不能只用字符串名字表示。

#### 建议改造方向

增加独立的 semantic dimension schema：

- dimension_id / name / display_name
- value_type / hierarchy_type
- source binding
- allowed_for_intents
- compatible_metrics
- drill_path
- null / other bucket policy
- cardinality band
- enumeration governance

---

### 4.5 时间语义不足

#### 现状问题

当前已开始区分 `time_scope` 与 `scope`，这是对的，但仍不足以覆盖企业现实中的复杂时间问题：

- event time vs processing time vs partition time
- assignment time vs exposure time vs conversion time
- late-arriving data
- backfill 与 correction
- real-time / batch mixed semantics

#### 互联网场景例子

- 实验分析里常见要求是：
  - 以 assignment 时刻为分组锚点
  - 以 first exposure 时刻为处理开始点
  - 以转化事件时刻做归因判断
  - 只统计 exposure 后 7 天内的转化

这不是简单的一个 `time_scope` 能说清楚的。

#### 建议改造方向

补充时间绑定与时间语义 contract：

- canonical analysis time
- assignment time
- exposure time
- conversion time
- partition freshness metadata
- attribution windows
- baseline alignment rules
- incomplete window policy

---

### 4.6 治理与可比性语义不是一等对象

#### 现状问题

当前设计文档已经意识到治理、quality、comparability 很重要，但 semantic layer 本身还没有把这些规则做成强绑定对象。

#### 互联网场景例子

- 实验主指标可以比较，但 guardrail metric 不一定允许按某些细粒度维度切分
- 某指标只有在样本量超过阈值、数据完整度足够时才允许做显著性检验
- 某些指标只允许在特定业务域和特定国家范围内使用

#### 建议改造方向

增加规则对象与规则引用：

- comparability contract
- inferential readiness gate
- sample sufficiency gate
- quality gate
- privacy / policy gate

让 metric、cohort、experiment、dimension 都能显式引用这些规则。

---

### 4.7 版本与发布机制不够支撑复杂治理

#### 现状问题

当前对象有 `draft / published / deprecated` 与 `revision`，适合 MVP，但不够表达：

- 多版本并存
- 历史结果复盘所需的版本冻结
- 与 session / step / artifact 的强版本关联
- 兼容性破坏告警

#### 互联网场景例子

- 实验复盘往往要求严格回答：“当时那次结论是基于哪版 assignment 逻辑、哪版 metric 定义、哪版 exposure 规则得出的？”

#### 建议改造方向

把发布升级为可快照化的 semantic release：

- semantic package version
- dependency graph version
- session-bound semantic snapshot
- migration / compatibility metadata

---

## 5. 为什么实验分析是最典型的缺口场景

实验分析几乎把互联网企业分析的难点都叠在了一起：

- 既有业务对象（experiment / variant）
- 又有执行事实（assignment / exposure）
- 又有统计约束（sample ratio mismatch、显著性检验）
- 又有归因窗口
- 又有主指标与 guardrail 的角色差异
- 又有污染样本、首曝归因、跨天窗口、冻结复盘

如果 semantic layer 无法原生表达实验分析，说明它还不足以承载复杂互联网分析。

---

## 6. 实验分析应新增的原生语义对象

### 6.1 experiment

表示实验本身的业务与运行边界。

建议字段：

- experiment_id
- name
- display_name
- status
- owner
- objective
- randomization_unit
- randomization_scope
- mutual_exclusion_group
- start_time / end_time
- assignment_policy_ref
- exposure_policy_ref
- default_cohort_ref
- primary_metric_refs
- guardrail_metric_refs
- quality_rule_refs
- properties

### 6.2 variant

表示实验下的各 treatment arms。

建议字段：

- variant_id
- experiment_id
- name
- role（control / treatment / holdout / shadow）
- traffic_allocation
- config_ref
- effective_time_range
- properties

### 6.3 assignment

表示“理论分组事实”。

它回答：某个分析主体在某个时间点被分到哪个实验组。

建议字段：

- assignment_semantic_id
- experiment_id
- subject_key_type
- subject_key_binding
- assigned_at_binding
- variant_binding
- sticky_bucket_policy
- reassignment_policy
- contamination_flags
- source_binding_ref

### 6.4 exposure

表示“实际接触处理”的事实。

它回答：分析主体是否真正看到了实验处理，以及首次看到的时间。

建议字段：

- exposure_semantic_id
- experiment_id
- subject_key_binding
- exposed_at_binding
- exposure_event_binding
- variant_binding
- first_exposure_rule
- exposure_count_rule
- valid_exposure_predicate
- source_binding_ref

### 6.5 cohort

表示实验分析中的样本集定义。

建议字段：

- cohort_id
- experiment_id
- entry_basis（assignment / first_exposure / custom）
- inclusion_rules
- exclusion_rules
- freezing_rule
- subject_key_binding
- time_anchor_binding
- contamination_policy

### 6.6 metric role

并不是新增一个完全独立的 metric 类型，而是给 metric 在 experiment context 下赋予角色。

建议角色：

- primary_metric
- secondary_metric
- guardrail_metric
- diagnostic_metric

guardrail metric 需要额外字段：

- threshold_type
- threshold_value
- comparison_direction
- blocking_level
- alert_policy

---

## 7. 实验分析如何绑定表列 schema

这一节回答：这些实验相关原生语义对象，如何与底层表列对应起来。

关键原则不是“一个语义对象对应一张表”，而是：

> 一个语义对象对应一套受约束的物理绑定 contract。

该 contract 至少包括：

- 来源表 / 视图
- 主体键列
- 时间列
- 组别列
- 行级过滤条件
- 与其他对象的 join 规则
- 时间窗口与迟到数据策略

### 7.1 推荐的绑定层次

建议把物理绑定拆成两层：

#### 第一层：source object binding

用于说明某类事实主要来自哪张表。

例如：

- `exp_assignments_daily`
- `user_event_log`
- `orders_fact`
- `user_dim`

#### 第二层：semantic field binding

用于说明在该来源表中，哪个列承担什么语义角色。

例如：

- `subject_key` → `user_id`
- `experiment_id` → `exp_id`
- `variant_id` → `bucket`
- `assigned_at` → `assigned_ts`
- `exposed_at` → `event_time`
- `partition_date` → `ds`

### 7.2 一个建议的实验绑定 schema

下面给出一个建议形态。它不是当前实现，而是建议增加的 typed binding contract。

```json
{
  "experiment_id": "exp_checkout_redesign",
  "randomization_unit": "user",
  "bindings": {
    "assignment": {
      "source_object": "warehouse.exp_user_assignments",
      "role": "assignment_log",
      "subject_key": {
        "column": "user_id",
        "type": "user_id"
      },
      "experiment_id_column": "experiment_id",
      "variant_id_column": "variant_id",
      "assigned_at": {
        "column": "assigned_at",
        "semantic_time_role": "assignment_time"
      },
      "row_predicate": {
        "op": "eq",
        "field": "is_valid_assignment",
        "value": true
      },
      "partition_columns": ["ds"]
    },
    "exposure": {
      "source_object": "warehouse.app_event_log",
      "role": "exposure_log",
      "subject_key": {
        "column": "user_id",
        "type": "user_id"
      },
      "event_time": {
        "column": "event_time",
        "semantic_time_role": "exposure_time"
      },
      "experiment_id_column": "event_properties.experiment_id",
      "variant_id_column": "event_properties.variant_id",
      "event_name_predicate": {
        "op": "eq",
        "field": "event_name",
        "value": "experiment_exposed"
      },
      "first_exposure_rule": "min(event_time)",
      "partition_columns": ["event_date"]
    },
    "conversion_metric_base": {
      "source_object": "warehouse.order_fact",
      "role": "metric_fact",
      "subject_key": {
        "column": "user_id",
        "type": "user_id"
      },
      "event_time": {
        "column": "paid_at",
        "semantic_time_role": "conversion_time"
      },
      "measure_columns": {
        "order_cnt": "order_id",
        "gmv": "pay_amount"
      },
      "row_predicate": {
        "op": "eq",
        "field": "is_paid",
        "value": true
      },
      "partition_columns": ["pay_date"]
    }
  },
  "join_graph": {
    "assignment_to_exposure": {
      "keys": ["user_id", "experiment_id", "variant_id"],
      "time_constraint": "exposure.event_time >= assignment.assigned_at"
    },
    "exposure_to_conversion": {
      "keys": ["user_id"],
      "time_constraint": "conversion.paid_at between exposure.first_exposed_at and exposure.first_exposed_at + interval '7 day'"
    }
  },
  "window_policies": {
    "conversion_window": "7d",
    "late_arrival_grace_period": "2d",
    "incomplete_window_policy": "exclude_open_subjects"
  }
}
```

### 7.3 绑定 schema 的几个关键点

#### 7.3.1 assignment 与 exposure 必须区分

在实验分析里：

- assignment 回答“理论上应该属于哪组”
- exposure 回答“实际上是否接触到了处理”

二者经常来自不同表，也常常存在不一致。

如果 semantic layer 里不把它们拆开，系统就很难原生支持：

- ITT（intention-to-treat）
- ATT / exposure-based analysis
- 污染样本识别
- 首曝时间归因

#### 7.3.2 cohort 通常不是一张固定表

cohort 更常见的是“绑定规则”而不是“物理表”：

- 基于 assignment 入组
- 基于 first exposure 入组
- 排除某些污染用户
- 冻结在某个统计窗口

所以 cohort 更适合绑定为：

- 入口来源
- inclusion / exclusion predicates
- 时间锚点
- 去重主体

而不是简单绑定一张 `cohort_table`。

#### 7.3.3 metric 要绑定分析口径，而不只是 measure 列

例如实验主指标 `conversion_rate` 的 schema 不应只是：

- numerator = `converted`
- denominator = `exposed_user`

还应绑定：

- 分析主体是 `user`
- cohort basis 是 `first_exposure`
- attribution basis 是 `exposure`
- attribution window 是 `7d`
- sample kind 是 `rate`
- inferential method defaults 是 `two_proportion_ztest`

#### 7.3.4 guardrail metric 是 metric 在 experiment context 下的角色，不只是普通 metric

比如：

- crash_rate
- latency_p95
- complaint_rate

这些指标不仅需要数值定义，还需要实验约束定义：

- 允许波动范围
- 是否 blocking
- 是否按某些人群切分后再判断

---

## 8. 实验分析如何与 intent 集成

这一节回答：是否要给每个实验对象单独增加新的 intent？

结论是不建议。

### 8.1 不建议的方向：为每个业务对象发明一个 intent

例如：

- `get_assignment`
- `get_exposure`
- `analyze_variant`
- `analyze_guardrail`

这样做的问题是：

- intent 表面会快速膨胀
- 很多 intent 只是底层数据对象的不同叫法
- 复用性差
- 新业务场景会不断要求再加新 intent

最终会把 analysis runtime 变成“业务接口拼盘”，而不是稳定的 typed analysis system。

### 8.2 更合理的方向：稳定原子 intents + 场景原生语义 + 少量派生 intents

建议分三层：

#### 第一层：primitive intents 保持稳定

继续以通用分析动作作为原子能力：

- `observe`
- `compare`
- `decompose`
- `test`
- `detect`
- `attribute`
- `diagnose`

这些 intent 不关心“这是实验、漏斗还是留存”，它们只关心：

- 观测什么
- 比较什么
- 如何归因
- 如何检验

#### 第二层：实验对象作为 typed semantic context 注入

例如请求里不只是传：

- `metric = "conversion_rate"`

还可以传：

- `experiment_ref`
- `variant_refs`
- `cohort_ref`
- `analysis_basis`
- `window_policy_ref`

这类字段不一定都暴露在所有 primitive intents 顶层，也可以先在编译器做 semantic resolution，再转成标准化 observe / compare / test 输入。

#### 第三层：少量场景级 derived intents

对于非常常见、分析 DAG 固定的业务动作，可以增加少量“派生 intent”，例如：

- `analyze_experiment`
- `validate_experiment`
- `diagnose_experiment_regression`

但这类 derived intent 的职责应是：

- 把实验场景稳定展开为既定的内部分析 DAG
- 复用 primitive intents 与 evidence engine

而不是重新定义底层观测与比较语义。

---

## 9. 一个建议的实验分析 intent 分层方案

### 9.1 primitive intents 不变

现有 primitive intents 已经覆盖实验分析的大部分基础动作：

- `observe`：观察某组样本上的指标
- `compare`：比较 treatment 与 control
- `test`：做显著性检验
- `attribute`：解释 uplift 的结构来源
- `detect`：发现异常 guardrail 或细分异常
- `diagnose`：对显著异常再展开

### 9.2 在 semantic compiler 中增加 experiment-aware resolution

semantic compiler 新增一层实验解析逻辑，把：

- experiment
- variants
- assignment / exposure basis
- cohort
- metric roles

编译为具体的 typed step input。

例如外部请求：

```json
{
  "experiment": "checkout_redesign",
  "metric": "conversion_rate",
  "analysis_basis": "exposure",
  "cohort": "first_exposure_valid_users",
  "compare": {
    "left_variant": "treatment",
    "right_variant": "control"
  },
  "window": "7d"
}
```

编译后可规范化为：

- 左侧 observe：treatment exposure cohort 下的 rate sample summary
- 右侧 observe：control exposure cohort 下的 rate sample summary
- compare：两侧 rate summary 的 delta
- test：两侧 rate summary 的差异显著性检验

### 9.3 增加一个场景级 derived intent：`analyze_experiment`

如果要对实验分析提供更强的一键式入口，建议增加的是一个“场景级派生 intent”，而不是一堆对象级 intent。

建议其内部 DAG 大致为：

1. validate experiment context
2. check assignment completeness / SRM
3. observe primary metric for treatment
4. observe primary metric for control
5. compare primary metric
6. test primary metric
7. observe each guardrail metric for treatment / control
8. compare + test each guardrail
9. optional attribute on additive driver metrics
10. synthesize structured experiment finding bundle

### 9.4 `analyze_experiment` 的建议请求形状

```json
{
  "experiment": "checkout_redesign",
  "primary_metric": "conversion_rate",
  "guardrails": ["crash_rate", "latency_p95"],
  "analysis_basis": "exposure",
  "cohort": "first_exposure_valid_users",
  "left_variant": "treatment",
  "right_variant": "control",
  "time_scope": {
    "kind": "range",
    "start": "2026-03-01T00:00:00Z",
    "end": "2026-03-15T00:00:00Z"
  },
  "attribution_window": "7d",
  "include_segment_breakdown": ["platform", "country"],
  "include_attribute": {
    "enabled": true,
    "driver_metrics": ["conversion_numerator"]
  }
}
```

### 9.5 `analyze_experiment` 的建议执行语义

#### 输入阶段

- 解析 experiment 与 variant
- 解析 assignment / exposure / cohort 绑定
- 解析 metric role 与 guardrail role
- 校验指定 metric 是否允许在该 experiment context 下使用

#### 编译阶段

- 根据 `analysis_basis` 决定按 assignment 还是 exposure 取样
- 根据 `cohort` 构造标准化 scope / sample contract
- 根据 metric 的 sample kind 决定 `observe` 输出模式：
  - rate metric → `rate_sample_summary`
  - numeric metric → `numeric_sample_summary`
- 自动展开 compare / test

#### 证据阶段

- 生成 primary metric uplift artifact
- 生成 guardrail evaluation artifacts
- 生成 SRM / quality / completeness checks
- 生成最终 experiment analysis bundle

### 9.6 为什么这比“给实验单独做一套 analysis engine”更好

因为它保留了 Factum 当前设计的核心优势：

- 原子语义稳定
- 外部契约 typed
- 内部可展开为 deterministic DAG
- 结果继续进入统一 evidence engine

这意味着实验分析不是一个平行系统，而是 semantic layer 和 intent runtime 的自然扩展。

---

## 10. 一个更具体的实验分析编译示例

目标问题：

> checkout_redesign 实验是否提升了转化率？是否恶化了 crash_rate 和 latency_p95？如果提升成立，主要由哪些人群贡献？

### 10.1 语义输入

- experiment = `checkout_redesign`
- variants = `treatment`, `control`
- basis = `exposure`
- cohort = `first_exposure_valid_users`
- primary_metric = `conversion_rate`
- guardrails = `crash_rate`, `latency_p95`
- attribute_driver_metric = `conversion_numerator`

### 10.2 可能展开出的内部 DAG

1. `observe(rate_sample_summary)` on `conversion_rate`, treatment
2. `observe(rate_sample_summary)` on `conversion_rate`, control
3. `compare` primary metric
4. `test` primary metric
5. `observe(rate_sample_summary)` on `crash_rate`, treatment
6. `observe(rate_sample_summary)` on `crash_rate`, control
7. `compare` crash_rate
8. `test` crash_rate
9. `observe(numeric_sample_summary)` on `latency_p95_proxy_or_summary_metric`, treatment
10. `observe(numeric_sample_summary)` on `latency_p95_proxy_or_summary_metric`, control
11. `compare` latency
12. `test` latency
13. `attribute` on `conversion_numerator`, dimensions = `platform`, `country`
14. `synthesize_findings`

### 10.3 这个例子说明了什么

说明实验相关原生语义对象真正起作用的地方，不是替代 intents，而是：

- 让 compiler 知道如何构造 treatment/control 样本
- 让 compiler 知道应该用 assignment 还是 exposure
- 让 compiler 知道主指标和 guardrail 指标的角色差异
- 让 runtime 知道应做哪些预检查与 gating

---

## 11. 对当前 Factum 演进的建议优先级

### 第一优先级：补 experiment-aware semantic bindings

先不要急着发明大量新 endpoint，而是先补足：

- experiment / variant / assignment / exposure / cohort 的 schema
- 与 source object 的绑定 contract
- metric role / guardrail role

这是让实验分析可落地的最小必要条件。

### 第二优先级：让现有 primitive intents 支持 experiment context resolution

尤其是：

- `observe`
- `compare`
- `test`
- `attribute`

需要能安全地消费“由 experiment context 归一化出来的输入”。

### 第三优先级：增加 `analyze_experiment` 这类少量 derived intents

当 experiment semantic context 已经稳定后，再增加少量常见场景的派生意图。

### 第四优先级：把 SRM、样本充分性、污染检查做成标准 gate

实验分析如果没有这些 gate，就很难成为可信的产品能力。

---

## 12. 总结

当前 Factum semantic layer 的方向是正确的：它已经在从“SQL 外露”向“typed analysis contract”演进。但就复杂互联网企业的分析需求而言，当前模型仍然缺少足够丰富的原生业务语义对象，尤其在实验分析场景中表现明显。

更合理的演进方式不是：

- 为每个实验对象增加一个 intent

而是：

- 在 semantic layer 中增加实验相关原生对象
- 用 typed binding contract 把这些对象绑定到底层表列与 join 规则
- 让 semantic compiler 把 experiment context 解析为标准化的 primitive intent 输入
- 只在少数高频场景上增加场景级 derived intents，例如 `analyze_experiment`

一句话总结：

> 实验对象应进入 semantic layer，实验流程应进入 derived intents，而基础分析动作应继续留在稳定的 primitive intents 中。
