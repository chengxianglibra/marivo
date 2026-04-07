# Semantic 文档总览

本文为 `docs/semantic/` 目录下文档的导航页，用于帮助读者快速理解这一组语义设计文档分别回答什么问题、彼此之间如何衔接，以及建议的阅读顺序。

这组文档整体关注的是 Factum 的 **semantic layer / catalog object contracts / compiler-facing contracts**：它们定义稳定语义对象、对象之间的组合边界，以及这些对象如何被编译为内部 IR。它们**不是**当前实现说明，也**不是**外部 HTTP wire spec。外部接口以 `docs/api/` 为准，intent 设计以 `docs/analysis/intents/` 为准。

## 这一组文档在解决什么问题

`docs/semantic/` 试图把 Factum 从“SQL 表达式注册表”推进到“类型化分析契约”：

- `metric` 负责 **measurement semantics**：量什么、统计语义是什么、有哪些比较/检验/分解边界。
- `process object` 负责 **process semantics**：总体、阶段、路径、session、cohort 等过程如何被稳定构造。
- `entity` 负责 **stable business identity**：业务实体是什么，以及语义层如何稳定引用它。
- `dimension` 负责 **analysis axis semantics**：共享维度轴是什么、值如何治理、层级如何组织。
- `typed binding contract` 负责 **physical grounding**：上述语义对象如何绑定到底层 source objects 与字段角色。
- compiler / IR 负责 **normalization、validation、expansion、IR assembly**：把这些对象编译成可校验、可追踪、可 lower 的内部计划。

一句话说，这一目录是在回答：

> Factum 不把外部分析请求直接翻译成 SQL，而是先把类型化语义对象组合成受治理的中间语义计划。

## 文档关系图

```text
time schema -------\
entity schema -----+\
dimension schema --+--\
metric schema -----+--- > metric/process contract ----> compiler spec ----> IR schema contract
process schema ----/--/
       \
        -> asset schema -> typed binding contract
```

可以把它理解成六层：

1. **Foundation 层**：稳定 ref taxonomy 与基础语义命名空间（如 `time.*`、`entity.*`、`dimension.*`）
2. **对象层**：`entity`、`dimension`、`metric`、`process object`
3. **承载层**：`asset` 如何表达真实物理承载体与稳定 surface
4. **绑定层**：typed binding 如何把对象层语义落到 asset surfaces / relations
5. **编译层**：compiler 如何做 normalize、compatibility resolution、derived expansion
6. **计划层**：IR 如何表达 engine-agnostic 的语义计划

## 本轮收敛原则

本目录中的 public schema 采用以下收敛原则：

- **只保留稳定且必须的 schema**：对象 contract 只回答“这个对象在语义上是什么”。
- **catalog metadata 单独承载**：`status`、`revision`、`lineage`、`quality gates`、搜索别名等 catalog/治理元数据，不再视为对象主 contract 的一部分。
- **compatibility profile 不等于对象本体**：`supported_intents`、`result_modes`、`capabilities`、`inference support` 这类组合/编译信息，不应默认塞入 public object schema。
- **asset 与 binding 分层**：`asset` 只回答“物理承载体是什么”，`binding` 只回答“语义槽位如何绑定到 asset surface”。
- **binding 不依赖 compiler 内部路径**：binding 只绑定 public contract targets，不绑定 compiler-visible internal target paths。
- **IR 不重复定义对象语义**：IR 只保留 normalized snapshots 与 plan wiring，不再重新发明 metric/process 的公共字段体系。
- **重要但尚未拍板的技术决策显式标注为待定**：本轮不把尚无共识的兼容矩阵或治理策略硬写进主 schema。

## 各文档说明

| 文档 | 主要主题 | 解决的核心问题 |
| --- | --- | --- |
| [`time-schema-contract.zh.md`](./time-schema-contract.zh.md) | `time.*` 的统一语义 contract | 时间语义引用、窗口本体、请求时间范围、绑定消费策略与 compiler/IR 解析结果如何分层 |
| [`metric-process-contract.zh.md`](./metric-process-contract.zh.md) | `metric` 与 `process object` 的总分工 | 为什么要把过程语义从 metric 中拆出来、三层对象如何分工、compiler/IR 应如何承接 |
| [`metric-v2-schema.zh.md`](./metric-v2-schema.zh.md) | `metric` 的目标 schema | metric 应承载哪些 measurement semantics，哪些兼容性/治理信息不应回流到主 contract |
| [`process-object-schema.zh.md`](./process-object-schema.zh.md) | `process object` 的目标 schema | 过程对象如何声明稳定接口、subtype 如何建模、哪些 compiler compatibility 信息不应泄漏到 public schema |
| [`entity-schema-contract.zh.md`](./entity-schema-contract.zh.md) | `entity` 的目标 schema | entity 应如何收缩为稳定业务身份契约，并为 metric/process/compiler 提供 `entity_ref` |
| [`dimension-schema-contract.zh.md`](./dimension-schema-contract.zh.md) | `dimension` 的目标 schema | 共享分析维度如何成为独立 contract，以及它与 entity / metric / process 的边界如何划分 |
| [`asset-schema-contract.zh.md`](./asset-schema-contract.zh.md) | 物理承载体 contract | 真实物理表 / 视图 / stream / snapshot 等 carrier 如何被稳定标识，并向 binding 暴露 field / time / relation surfaces |
| [`typed-binding-contract.zh.md`](./typed-binding-contract.zh.md) | 语义对象到物理层的绑定契约 | semantic refs 如何映射到 asset surfaces / relations，同时保持执行解耦 |
| [`compiler-spec.zh.md`](./compiler-spec.zh.md) | semantic compiler 规范 | typed intent、metric、process、typed refs 如何被归一化、校验、展开并编译成 IR |
| [`ir-schema-contract.zh.md`](./ir-schema-contract.zh.md) | IR 的目标 schema 契约 | IR 的职责边界是什么、它与 compile report / lowering / engine plan 如何分层 |

## 推荐阅读顺序

### 1. 先理解总分工

先读 [`metric-process-contract.zh.md`](./metric-process-contract.zh.md)。

这篇是整个目录的“总论”，先定义：

- `metric` 不再承担全部过程语义
- `process object` 是与 `metric` 同级的原生语义对象
- `intent` 只表达分析动作
- compiler / IR 承担组合复杂度，而不是把复杂性泄漏到外部契约

如果不先建立这层分工，后面的 schema 文档会显得像一组孤立字段设计。

### 2. 再看五个核心对象

随后阅读：

1. [`metric-v2-schema.zh.md`](./metric-v2-schema.zh.md)
2. [`process-object-schema.zh.md`](./process-object-schema.zh.md)
3. [`entity-schema-contract.zh.md`](./entity-schema-contract.zh.md)
4. [`dimension-schema-contract.zh.md`](./dimension-schema-contract.zh.md)
5. [`time-schema-contract.zh.md`](./time-schema-contract.zh.md)

这五篇分别定义 semantic layer 中最核心的五类对象：

- `metric`：measurement contract
- `process object`：process/interface contract
- `entity`：稳定业务身份与引用锚点
- `dimension`：共享分析轴与值治理 contract
- `time`：共享时间语义与时间锚点 contract

其中：

- 如果你关心“指标为什么不能继续用 `definition_sql` 作为主语义”，优先看 `metric-v2-schema`
- 如果你关心“漏斗 / cohort / experiment / lifecycle 这类对象该放在哪里”，优先看 `process-object-schema`
- 如果你关心“`entity.*` / `subject.*` / `grain.*` / `key.*` 这些 ref taxonomy 为什么要分开”，优先看 `entity-schema-contract`
- 如果你关心“`dimension.*` 为什么不能继续只是 metric 上的字符串数组”，优先看 `dimension-schema-contract`
- 如果你关心“`time_scope`、`primary_time_ref`、`anchor_time_ref`、late arrival / freshness 应分别落在哪层”，优先看 `time-schema-contract`

### 3. 再看对象如何落地

接着按顺序读：

1. [`asset-schema-contract.zh.md`](./asset-schema-contract.zh.md)
2. [`typed-binding-contract.zh.md`](./typed-binding-contract.zh.md)

前面的对象文档主要回答“语义上是什么”，而这篇回答“如何稳定绑定到底层物理数据”。它强调：

- `asset` 是“物理承载体本体”的稳定 contract
- binding 是一等、可引用、可组合对象
- field binding 的核心是 **public contract target + asset surface**
- join、late arrival、incomplete-window 等属于 binding 的消费约束
- binding 不等于 SQL DSL，也不取代 compiler / IR

### 4. 最后看编译与 IR

最后按顺序读：

1. [`compiler-spec.zh.md`](./compiler-spec.zh.md)
2. [`ir-schema-contract.zh.md`](./ir-schema-contract.zh.md)

这两篇把前面的对象与绑定层收束到编译链路中：

- `compiler-spec` 说明请求输入槽位、typed refs、全局编译规则、编译阶段与 derived intent 的确定性展开
- `ir-schema-contract` 说明 IR 的内部职责边界，以及它为何不是 SQL AST、也不是外部 API contract

## 如果按角色阅读

| 读者角色 | 建议优先阅读 |
| --- | --- |
| 想理解语义层总体方向的人 | `metric-process-contract` |
| 设计 metric schema / catalog 的人 | `metric-v2-schema`、`entity-schema-contract`、`dimension-schema-contract` |
| 设计 funnel / experiment / cohort / session 等过程对象的人 | `process-object-schema` |
| 设计 dimension catalog / value governance / drill path 的人 | `dimension-schema-contract`、`asset-schema-contract`、`typed-binding-contract` |
| 设计 semantic mapping / physical grounding 的人 | `asset-schema-contract`、`typed-binding-contract` |
| 设计 compiler、validation、IR、lowering 边界的人 | `compiler-spec`、`ir-schema-contract` |

## 这一目录中的几个反复出现的共识

虽然每篇文档各自聚焦不同层面，但它们共享几条稳定原则：

- **typed contracts over raw SQL**：外部和中间层都应优先表达类型化语义，而不是 SQL 形状。
- **semantic / physical 分层**：对象层表达“是什么”，asset 表达“承载在哪里”，binding 表达“如何落地”，compiler/IR 表达“如何组合与编译”。
- **stable object contract / compiler profile 分层**：对象主 contract 只保留稳定且必须的语义字段；组合兼容、治理与执行前置条件优先进入 compiler profile / governance context。
- **dimensions are first-class semantic axes**：共享维度应是独立对象，而不是继续退回成 metric 上的字符串数组。
- **process semantics 从 metric 中拆出**：复杂总体构造、路径、阶段、session、实验上下文不应继续塞进 metric。
- **time semantics are layered**：时间锚点、窗口本体、请求时间范围、绑定消费策略、编译结果必须分层。
- **typed refs 是组合边界**：下游应优先消费 canonical artifact refs，而不是重建上游 scope/time_scope。
- **validation 是编译职责的一部分**：语义不兼容应在 compiler 阶段显式拒绝，而不是拖到 SQL 执行时报错。
- **IR 保持引擎无关**：IR 是稳定的内部语义计划，不应退化为 SQL AST 或 engine-specific DSL。

## 与其他目录的边界

- `docs/semantic/`：描述 semantic objects、binding、compiler、IR 的设计边界
- `docs/analysis/intents/`：描述 intent 体系、atomic / derived intent 设计
- `docs/api/`：描述外部 HTTP 接口与 wire contract

因此，在阅读或修改本目录时，建议始终保持以下边界：

- 不把 `docs/semantic/` 当成 HTTP 接口文档
- 不把 compiler / IR 设计直接写成 engine-specific 实现说明
- 不把对象 schema 退回成物理字段或 SQL 片段集合

## 一个最简心智模型

如果只记住一个模型，可以记住下面这句：

> `entity` 定义“谁”，`dimension` 定义“按什么轴观察”，`time` 定义“围绕哪种时间语义组织”，`metric` 定义“量什么”，`process object` 定义“总体/过程怎样形成”，`asset` 定义“真实物理承载体是什么”，`binding` 定义“这些语义对象如何落到 asset surface”，而 compiler / IR 定义“它们如何被安全地组合成分析计划”。

补充一句：

> 如果某个字段主要服务 catalog 治理、compiler 兼容性判断或执行引擎决策，而不是对象本体语义，它默认不应进入 public object schema。
