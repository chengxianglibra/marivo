# Semantic 文档总览

本文为 `specs/semantic/` 目录下文档的导航页，用于帮助读者快速理解这一组语义设计文档分别回答什么问题、彼此之间如何衔接，以及建议的阅读顺序。

这组文档整体关注的是 Marivo 的 **semantic layer / OSI-aligned object contracts / compiler-facing contracts**：它们定义 OSI 语义对象、对象之间的组合边界，以及这些对象如何被编译为内部 IR。它们**不是**当前实现说明，也**不是**外部 HTTP wire spec。外部接口以 `docs/api/` 为准，intent 设计以 `specs/analysis/intents/` 为准。

## 这一组文档在解决什么问题

`specs/semantic/` 定义 Marivo 的 dataset-native 语义层架构，对齐 OSI (Open Semantic Interchange) Core Metadata Spec v0.1.1：

- `SemanticModel` 负责 **模型容器与可见性**：语义模型的顶层分组，定义 public/private 可见性与业务域。
- `Dataset` 负责 **stable business identity + direct physical grounding**：业务数据集是什么，直接通过 `source` + `datasource_id` 落到物理表，无需独立 binding 层。
- `Field` 负责 **字段语义**：物理列/表达式的稳定语义声明，包含 dimension 和 time 属性。
- `Relationship` 负责 **跨数据集连接**：数据集之间的 key 对齐与基数声明。
- `Metric` 负责 **measurement semantics**：量什么、统计语义是什么，通过扁平表达式 + MARIVO 安全扩展表达。
- Compiler / IR 负责 **normalization、validation、IR assembly**：把这些对象编译成可校验、可追踪、可 lower 的内部计划。

一句话说，这一目录是在回答：

> Marivo 不把外部分析请求直接翻译成 SQL，而是先把 OSI 语义对象组合成受治理的中间语义计划。

## 文档关系图

```text
dataset schema -------\
field schema ---------+\
relationship schema --+--\
metric schema --------+--- > compiler spec ----> IR schema contract
calendar schemas -----/--/
```

可以把它理解成三层：

1. **OSI External Contract 层**：`SemanticModel`、`Dataset`、`Field`、`Relationship`、`Metric` — 对齐 OSI 规范的稳定线格式对象
2. **MARIVO Extension 层**：`datasource_id`、`data_type`、`observed_dataset`、`observation_grain`、`additivity`、`filters` 等 — 无法从 SQL 表达式安全推导的安全关键元数据
3. **Compilation / Execution 层**：compiler 如何做 reference resolution、validation、lowering，IR 如何表达 engine-agnostic 的语义计划

## 核心收敛原则

本目录中的 public schema 采用以下收敛原则：

- **dataset-native physical grounding**：`Dataset.source` + `Field.expression` 是唯一的持久化物理接地契约。不存在独立的 binding 层、carrier 对象或 source_object 快照。
- **OSI 对齐优先**：API 输入输出必须是合法的 OSI 文档。所有 MARIVO 特定数据通过 `custom_extensions` 承载，OSI 兼容工具无需了解 Marivo 即可消费。
- **扩展字段仅保留安全关键元数据**：MARIVO 扩展字段只在"推导错误会导致静默错误结果"或"无法从 SQL 表达式安全推导"时添加。可从表达式推断的字段、无运行时代码消费的字段应删除。
- **只保留稳定且必须的 schema**：对象 contract 只回答"这个对象在语义上是什么"。
- **lifecycle ceremony 删除**：对象直接创建/更新/删除，不再有 draft→validate→activate→publish→deprecate 状态机。写时校验在 create/update 内联执行，readiness 是 agent 面向的 harness 操作。
- **IR 使用引用而非复制**：IR 只保留对象引用和 resolved/derived 字段，不复制 catalog 对象的全部字段。
- **Dimension / Time 折叠为 Field 属性**：维度和时间不再是独立对象类型，而是 `Field.dimension` 和 `Field.dimension.is_time` 属性。
- **过滤语义内联到 Metric**：Predicate 不再是独立对象类型，默认过滤条件通过 `Metric.filters` MARIVO 扩展表达。

## 已被取代的文档

以下文档描述的对象类型或概念已被 OSI 对齐重写删除，文档已标记为 SUPERSEDED：

| 文档 | 被取代原因 |
| --- | --- |
| [`typed-binding-contract.zh.md`](./typed-binding-contract.zh.md) | 独立物理绑定层已删除；物理接地上内联到 Dataset/Field |
| [`entity-centric-object-model.zh.md`](./entity-centric-object-model.zh.md) | Entity 被 Dataset 取代；binding 层已删除 |
| [`compiler-compatibility-profile.zh.md`](./compiler-compatibility-profile.zh.md) | Compatibility Profile 已删除；能力推导直接使用对象字段 |
| [`process-object-schema.zh.md`](./process-object-schema.zh.md) | Process Object 已删除；过程语义另行设计 |
| [`enum-set-schema-contract.zh.md`](./enum-set-schema-contract.zh.md) | EnumSet 已删除；枚举域作为 Field.dimension 属性 |
| [`predicate-schema-contract.zh.md`](./predicate-schema-contract.zh.md) | Predicate 已删除；过滤语义通过 Metric.filters 承载 |

## 各文档说明

| 文档 | 主要主题 | 解决的核心问题 |
| --- | --- | --- |
| [`metric-v2-schema.zh.md`](./metric-v2-schema.zh.md) | OSI Metric + MARIVO 扩展 | Metric 作为扁平表达式模型，MARIVO 扩展承载 observed_dataset、observation_grain、additive_dimensions、aggregation_semantics、filters |
| [`metric-process-contract.zh.md`](./metric-process-contract.zh.md) | Metric 与过程语义的分工 | Metric 直接承担度量语义，过程语义另行设计 |
| [`entity-schema-contract.zh.md`](./entity-schema-contract.zh.md) | OSI Dataset + Field 模型 | Dataset 直接包含 source 和 fields，物理接地内联，无需独立 binding |
| [`dimension-schema-contract.zh.md`](./dimension-schema-contract.zh.md) | Dimension 作为 Field 属性 | 维度如何作为 Field.dimension 属性表达，而非独立对象 |
| [`predicate-v1-scope-note.zh.md`](./predicate-v1-scope-note.zh.md) | 过滤语义的范围说明 | v1 过滤语义如何通过 Metric.filters 和数据集级约束表达 |
| [`predicate-governance-note.zh.md`](./predicate-governance-note.zh.md) | 过滤语义治理 | 过滤条件的命名、复用和治理边界 |
| [`time-schema-contract.zh.md`](./time-schema-contract.zh.md) | Time 作为 Field 属性 | 时间语义如何通过 Field.dimension.is_time 表达，而非独立对象 |
| [`calendar.zh.md`](./calendar.zh.md) | Calendar alignment 与 calendar data | compare_type 对齐策略、calendar data 契约、annotation 生成、故障处理、版本冻结与 bucket pairing |
| [`additivity-modeling-guide.zh.md`](./additivity-modeling-guide.zh.md) | 加法性建模指南 | 加法性如何建模、dimension policy 与 time axis policy 如何选择 |
| [`evidence-integration.zh.md`](./evidence-integration.zh.md) | Evidence 与 Semantic 的集成边界 | canonical refs 与 semantic refs 如何分层、关联与禁止互相替代 |
| [`compiler-spec.zh.md`](./compiler-spec.zh.md) | semantic compiler 规范 | OSI 对象如何被归一化、校验、编译成 IR |
| [`ir-schema-contract.zh.md`](./ir-schema-contract.zh.md) | IR 的目标 schema 契约 | IR 使用引用而非复制，职责边界与 compile report / lowering / engine plan 的分层 |

## 推荐阅读顺序

### 1. 先理解 OSI 对象模型

阅读 OSI Alignment V2 设计文档（`docs/superpowers/specs/2026-04-30-osi-alignment-v2-design.md`）了解三层边界和五种 OSI 对象。

然后读 [`entity-schema-contract.zh.md`](./entity-schema-contract.zh.md) 理解 Dataset + Field 模型：
- Dataset 直接包含 `source` 和 `fields[]`，物理接地是内联的
- Field 承载列/表达式、dimension 和 time 属性
- 不存在独立的 binding 层

### 2. 再看 Metric 与相关属性

1. [`metric-v2-schema.zh.md`](./metric-v2-schema.zh.md) — OSI Metric + MARIVO 安全扩展
2. [`metric-process-contract.zh.md`](./metric-process-contract.zh.md) — Metric 与过程语义的分工
3. [`dimension-schema-contract.zh.md`](./dimension-schema-contract.zh.md) — Dimension 作为 Field 属性
4. [`time-schema-contract.zh.md`](./time-schema-contract.zh.md) — Time 作为 Field 属性
5. [`additivity-modeling-guide.zh.md`](./additivity-modeling-guide.zh.md) — 加法性建模

其中：

- 如果你关心"为什么 Metric 不再用多组件 typed contract"，优先看 `metric-v2-schema`
- 如果你关心"维度为什么不再是独立对象"，优先看 `dimension-schema-contract`
- 如果你关心"时间语义如何通过 Field.is_time 表达"，优先看 `time-schema-contract`

### 3. 再看 Calendar 与过滤

1. [`calendar.zh.md`](./calendar.zh.md)
2. [`predicate-v1-scope-note.zh.md`](./predicate-v1-scope-note.zh.md)
8. [`predicate-governance-note.zh.md`](./predicate-governance-note.zh.md)

### 4. 最后看编译与 IR

1. [`compiler-spec.zh.md`](./compiler-spec.zh.md)
2. [`ir-schema-contract.zh.md`](./ir-schema-contract.zh.md)
3. [`evidence-integration.zh.md`](./evidence-integration.zh.md)

## 如果按角色阅读

| 读者角色 | 建议优先阅读 |
| --- | --- |
| 想理解语义层总体方向的人 | OSI v2 设计文档、`entity-schema-contract` |
| 设计 Metric schema / catalog 的人 | `metric-v2-schema`、`entity-schema-contract`、`dimension-schema-contract` |
| 设计 Dataset / Field 模型的人 | `entity-schema-contract` |
| 设计 dimension / time 属性的人 | `dimension-schema-contract`、`time-schema-contract` |
| 设计 compiler、IR、lowering 边界的人 | `compiler-spec`、`ir-schema-contract` |
| 设计 calendar 对齐策略的人 | `calendar` |

## 核心共识

虽然每篇文档各自聚焦不同层面，但它们共享几条稳定原则：

- **OSI 对齐优先**：API 输入输出必须是合法 OSI 文档；MARIVO 特定数据仅通过 `custom_extensions` 承载。
- **dataset-native physical grounding**：Dataset 和 Field 是唯一的持久化物理接地契约，不存在独立 binding 层。
- **扩展字段仅保留安全关键元数据**：推导错误会静默产生错误结果的字段保留为 MARIVO 扩展；可安全推断的删除。
- **typed contracts over raw SQL**：外部和中间层都应优先表达类型化语义，而不是 SQL 形状。
- **semantic / compilation 分层**：对象层表达"是什么"；compiler/IR 表达"如何组合与编译"。
- **validation 是编译职责的一部分**：语义不兼容应在 compiler 阶段显式拒绝，而不是拖到 SQL 执行时报错。
- **IR 保持引擎无关**：IR 是稳定的内部语义计划，不应退化为 SQL AST 或 engine-specific DSL。
- **Dimension / Time 是 Field 属性**：维度和时间不再是独立对象，而是 Field 的属性。
- **过滤语义内联到 Metric**：默认过滤条件通过 Metric.filters 承载，不是独立 Predicate 对象。
- **Lifecycle ceremony 删除**：对象直接 CRUD，写时校验内联，readiness 是 harness 操作。

## 与其他目录的边界

- `specs/semantic/`：描述 OSI 对象、compiler、IR 的设计边界
- `specs/analysis/intents/`：描述 intent 体系、atomic / derived intent 设计
- `docs/api/`：描述外部 HTTP 接口与 wire contract

因此，在阅读或修改本目录时，建议始终保持以下边界：

- 不把 `specs/semantic/` 当成 HTTP 接口文档
- 不把 compiler / IR 设计直接写成 engine-specific 实现说明
- 不把对象 schema 退回成物理字段或 SQL 片段集合

## 一个最简心智模型

如果只记住一个模型，可以记住下面这句：

> `Dataset` 定义"谁/什么"并通过 source 直接落到物理表，`Field` 定义"有哪些列"并承载 dimension/time 属性，`Metric` 定义"量什么"作为扁平表达式，`Relationship` 定义"数据集如何连接"，而 Compiler / IR 定义"它们如何被安全地组合成分析计划"。

补充：

> 物理接地是 dataset-native 的：Dataset.source 选择关系，Field.expression 选择列或计算表达式。不存在独立的 binding 层。

> 如果某个字段主要服务 catalog 治理或执行引擎决策，推导错误不会静默产生错误结果，它不应成为 MARIVO 扩展字段；安全关键元数据才需要显式声明。
