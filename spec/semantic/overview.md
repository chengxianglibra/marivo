# Semantic 文档总览

本文为 `spec/semantic/` 目录下文档的导航页，用于帮助读者快速理解这一组语义设计文档分别回答什么问题、彼此之间如何衔接，以及建议的阅读顺序。

这组文档整体关注的是 Marivo 的 **semantic layer / catalog object contracts / compiler-facing contracts**：它们定义稳定语义对象、对象之间的组合边界，以及这些对象如何被编译为内部 IR。它们**不是**当前实现说明，也**不是**外部 HTTP wire spec。外部接口以 `docs/api/` 为准，intent 设计以 `spec/analysis/intents/` 为准。

## 这一组文档在解决什么问题

`spec/semantic/` 试图把 Marivo 从“SQL 表达式注册表”推进到“类型化分析契约”：

- `metric` 负责 **measurement semantics**：量什么、统计语义是什么、有哪些比较/检验/分解边界。
- `process object` 负责 **process semantics**：总体、阶段、路径、session、cohort 等过程如何被稳定构造。
- `entity` 负责 **stable business identity**：业务实体是什么，以及语义层如何稳定引用它。
- `dimension` 负责 **analysis axis semantics**：共享维度轴是什么、值如何治理、层级如何组织。
- `typed binding contract` 负责 **physical grounding**：上述语义对象如何绑定到底层 source objects 与字段角色。
- compiler / IR 负责 **normalization、validation、expansion、IR assembly**：把这些对象编译成可校验、可追踪、可 lower 的内部计划。

一句话说，这一目录是在回答：

> Marivo 不把外部分析请求直接翻译成 SQL，而是先把类型化语义对象组合成受治理的中间语义计划。

## 文档关系图

```text
time schema -------\
entity schema -----+\
dimension schema --+--\
enum-set schema ---+---\
metric schema -----+--- > metric/process contract ----> compiler spec ----> IR schema contract
process schema ----/--/
       \
        -> typed binding contract
```

可以把它理解成四层：

1. **Semantic 层**：`time`、`entity`、`dimension`、`metric`、`process object` 等稳定语义对象
2. **Binding 层**：typed binding 如何把对象层语义落到物理 carriers / surfaces / relations
3. **Compilation 层**：compiler 如何做 normalize、compatibility resolution、derived expansion、capability derivation
4. **Plan 层**：IR 如何表达 engine-agnostic 的语义计划（使用引用而非复制）

## 本轮收敛原则

本目录中的 public schema 采用以下收敛原则：

- **只保留稳定且必须的 schema**：对象 contract 只回答”这个对象在语义上是什么”。
- **catalog metadata 单独承载**：`status`、`revision`、`lineage`、`quality gates`、搜索别名等 catalog/治理元数据，不再视为对象主 contract 的一部分。
- **stable ref / object id / revision 分层**：`metric.*` 等 semantic ref 表示稳定业务语义 identity；object id 是服务端内部实例定位符；revision 是某个 stable identity 下可审计、可回溯的冻结定义版本。
- **默认解析最新，历史回溯显式带版本**：runtime/catalog 默认把 stable ref 解析到 latest active revision；artifact、step metadata、审计回放必须记录并使用 resolved revision，不能只保存裸 ref。
- **compatibility profile 不等于对象本体**：`supported_intents`、`result_modes`、`capabilities`、`inference support` 这类组合/编译信息，不应默认塞入 public object schema；由 compiler 从核心字段推导。
- **binding 统一承载物理落地**：binding 直接表达 carriers / surfaces / relations，不再引入独立的 asset 层；底层物理快照继续由 `source_objects` 承载。
- **binding 使用类型化 target**：binding 用 `BindingTarget` TypedDict 替代 informal 字符串路径，不依赖 compiler 内部路径。
- **IR 使用引用而非复制**：IR 只保留对象引用和 resolved/derived 字段，不复制 catalog 对象的全部字段。
- **entity 定义独立于 binding**：entity contract 在没有 binding 的情况下语义完整，binding 提供物理落地。
- **deprecate 表示 identity 退出**：`deprecated` 不释放 semantic ref，也不是 spelling、description、unit label 等同一语义修订的常规路径；这类修订应通过同 ref revision 表达。
- **重要但尚未拍板的技术决策显式标注为待定**：本轮不把尚无共识的兼容矩阵或治理策略硬写进主 schema。

## 各文档说明

| 文档 | 主要主题 | 解决的核心问题 |
| --- | --- | --- |
| [`time-schema-contract.zh.md`](./time-schema-contract.zh.md) | `time.*` 的统一语义 contract | 时间语义引用、窗口本体与消费策略三层模型，角色组合而非排斥 |
| [`calendar-alignment-policy.zh.md`](./calendar-alignment-policy.zh.md) | 节假日 / weekday / 交易日对齐策略 contract | 可比期如何生成、bucket 如何配对、calendar data 与 policy 如何分层 |
| [`calendar-data-contract.zh.md`](./calendar-data-contract.zh.md) | `calendar data` 逻辑输入契约 | resolver 依赖哪些稳定字段、唯一性约束、weekday/workday/annotation 如何表达 |
| [`calendar-data-v1-source-note.zh.md`](./calendar-data-v1-source-note.zh.md) | `calendar data` v1 source 与 version 冻结 | `CN` 节假日和业务活动各自从哪里来、如何冻结版本、为何不能用 runtime latest |
| [`calendar-version-freeze-policy.zh.md`](./calendar-version-freeze-policy.zh.md) | `calendar version` 冻结与发布流程 | 哪些 snapshot 允许被 resolver 使用、如何发布、如何回滚且不漂移历史 artifact |
| [`calendar-annotation-generation-policy.zh.md`](./calendar-annotation-generation-policy.zh.md) | holiday / event 注释生成规则 | `group_id` 与 relative key 如何生成、哪些窗口必须稳定标注 |
| [`calendar-annotation-failure-policy.zh.md`](./calendar-annotation-failure-policy.zh.md) | annotation 缺失处理规则 | 何时 fail、何时 warning、何时允许 fallback |
| [`metric-process-contract.zh.md`](./metric-process-contract.zh.md) | `metric` 与 `process object` 的总分工 | 为什么要把过程语义从 metric 中拆出来、三层对象如何分工、compiler/IR 应如何承接 |
| [`entity-centric-object-model.zh.md`](./entity-centric-object-model.zh.md) | entity-centric 对象模型收敛 | entity-only physical grounding、thin entity fields、object-owned semantic roles、relationship/profile 组合校验 |
| [`metric-v2-schema.zh.md`](./metric-v2-schema.zh.md) | `metric` 的目标 schema | metric 应承载哪些 measurement semantics，哪些 capability 应由 compiler 推导 |
| [`process-object-schema.zh.md`](./process-object-schema.zh.md) | `process object` 的目标 schema | 过程对象如何声明稳定接口、subtype 如何建模、哪些 capability 应保留 vs 推导 |
| [`entity-schema-contract.zh.md`](./entity-schema-contract.zh.md) | `entity` 的目标 schema | entity 如何作为独立语义锚点，不依赖 binding，不暴露 process 语义 |
| [`dimension-schema-contract.zh.md`](./dimension-schema-contract.zh.md) | `dimension` 的目标 schema | 共享分析维度如何成为独立 contract，structure_kind 与 semantic_role 分离 |
| [`predicate-schema-contract.zh.md`](./predicate-schema-contract.zh.md) | `predicate.*` 的目标 schema | 过滤语义如何成为独立 contract，如何区分 metric qualifier、binding row filter 与 request scope |
| [`predicate-v1-scope-note.zh.md`](./predicate-v1-scope-note.zh.md) | `predicate.*` v1 产品边界 | v1 支持与不支持的表达式结构、操作符、usage 类别与 target_ref 前缀 |
| [`predicate-governance-note.zh.md`](./predicate-governance-note.zh.md) | `predicate.*` 对象治理说明 | authoring 边界、生命周期约束、catalog 使用约定、新建 vs 复用决策标准 |
| [`enum-set-schema-contract.zh.md`](./enum-set-schema-contract.zh.md) | `dimension` 的受治理值域 contract | `enum_set_ref` / `enum_version` 引用的值域本体是什么、版本锚定哪一层、与 governance / binding 如何分层 |
| [`typed-binding-contract.zh.md`](./typed-binding-contract.zh.md) | 语义对象到物理层的绑定契约 | semantic refs 如何映射到 carriers / surfaces / relations，使用类型化 BindingTarget |
| [`evidence-integration.zh.md`](./evidence-integration.zh.md) | Evidence 与 Semantic 的集成边界 | canonical refs / canonical artifact refs 与 `metric_ref`、`process_ref`、广义 `semantic_ref` 如何分层、关联与禁止互相替代 |
| [`compiler-compatibility-profile.zh.md`](./compiler-compatibility-profile.zh.md) | compiler compatibility profile 契约 | 哪些组合兼容性与前置能力应独立发布为 profile artifact，如何被 compiler 消费，如何与 object contract / governance context 分层 |
| [`compiler-spec.zh.md`](./compiler-spec.zh.md) | semantic compiler 规范 | typed intent、metric、process、typed refs 如何被归一化、校验、展开并编译成 IR，包含 capability 推导规则 |
| [`ir-schema-contract.zh.md`](./ir-schema-contract.zh.md) | IR 的目标 schema 契约 | IR 使用引用而非复制，职责边界是什么、它与 compile report / lowering / engine plan 如何分层 |

## 推荐阅读顺序

### 1. 先理解总分工

先读 [`metric-process-contract.zh.md`](./metric-process-contract.zh.md)。

这篇是整个目录的“总论”，先定义：

- `metric` 不再承担全部过程语义
- `process object` 是与 `metric` 同级的原生语义对象
- `intent` 只表达分析动作
- compiler / IR 承担组合复杂度，而不是把复杂性泄漏到外部契约

如果不先建立这层分工，后面的 schema 文档会显得像一组孤立字段设计。

如果你关心 semantic layer 对象模型如何从“多个对象都可能触碰 physical grounding”收敛为“entity-only physical grounding + object-owned semantic roles”，以及 entity field、dimension/time/predicate、metric/process、relationship/profile 的新边界，随后阅读 [`entity-centric-object-model.zh.md`](./entity-centric-object-model.zh.md)。

### 2. 再看六个核心对象与一个配套值域契约

随后阅读：

1. [`metric-v2-schema.zh.md`](./metric-v2-schema.zh.md)
2. [`process-object-schema.zh.md`](./process-object-schema.zh.md)
3. [`entity-schema-contract.zh.md`](./entity-schema-contract.zh.md)
4. [`dimension-schema-contract.zh.md`](./dimension-schema-contract.zh.md)
5. [`predicate-schema-contract.zh.md`](./predicate-schema-contract.zh.md)
5a. [`predicate-v1-scope-note.zh.md`](./predicate-v1-scope-note.zh.md)
5b. [`predicate-governance-note.zh.md`](./predicate-governance-note.zh.md)
6. [`time-schema-contract.zh.md`](./time-schema-contract.zh.md)
7. [`calendar-alignment-policy.zh.md`](./calendar-alignment-policy.zh.md)
8. [`calendar-data-contract.zh.md`](./calendar-data-contract.zh.md)
9. [`calendar-data-v1-source-note.zh.md`](./calendar-data-v1-source-note.zh.md)
10. [`calendar-version-freeze-policy.zh.md`](./calendar-version-freeze-policy.zh.md)
11. [`calendar-annotation-generation-policy.zh.md`](./calendar-annotation-generation-policy.zh.md)
12. [`calendar-annotation-failure-policy.zh.md`](./calendar-annotation-failure-policy.zh.md)
13. [`enum-set-schema-contract.zh.md`](./enum-set-schema-contract.zh.md)

前六篇定义 semantic layer 中最核心的六类对象，第七篇补充 `dimension` 在 enumerated domain 下依赖的值域契约：

- `metric`：measurement contract
- `process object`：process/interface contract
- `entity`：稳定业务身份与引用锚点
- `dimension`：共享分析轴与值治理 contract
- `predicate`：共享过滤语义与 lineage contract
- `time`：共享时间语义与时间锚点 contract
- `calendar alignment policy`：可比期生成、holiday/weekday 对齐与 bucket pairing contract
- `calendar data` logical contract：resolver 实际依赖的日粒度注释字段与 source/version 边界
- `calendar version freeze policy`：哪些 calendar snapshot 可被 runtime 消费、如何冻结到 lineage、如何发布与回滚
- `calendar annotation generation policy`：holiday/event stable ID 与 relative key 如何离线生成
- `calendar annotation failure policy`：annotation 缺失时如何 fail、warning 与 fallback 分层
- `enum set`：被 `dimension.enum_set_ref` 引用的受治理值域 contract（不是新的顶层对象层）

其中：

- 如果你关心“指标为什么不能继续用 `definition_sql` 作为主语义”，优先看 `metric-v2-schema`
- 如果你关心“漏斗 / cohort / experiment / lifecycle 这类对象该放在哪里”，优先看 `process-object-schema`
- 如果你关心“`entity.*` / `subject.*` / `grain.*` / `key.*` 这些 ref taxonomy 为什么要分开”，优先看 `entity-schema-contract`
- 如果你关心“`dimension.*` 为什么不能继续只是 metric 上的字符串数组”，优先看 `dimension-schema-contract`
- 如果你关心”metric qualifier、binding row filter、request scope 为什么不能继续各自维护一套 filter 语义”，优先看 `predicate-schema-contract`
- 如果你关心”predicate 该怎么创建、命名、发布，何时新建 vs 复用”，优先看 `predicate-governance-note`
- 如果你关心“`enum_set_ref` / `enum_version` 到底引用什么，以及为什么它需要独立文档但不是新的顶层对象”，优先看 `enum-set-schema-contract`
- 如果你关心“`time_scope`、`primary_time_ref`、`anchor_time_ref`、late arrival / freshness 应分别落在哪层”，优先看 `time-schema-contract`
- 如果你关心“同比不按自然日，而按节假日或周几对齐该落在哪层”，优先看 `calendar-alignment-policy`

### 3. 再看对象如何落地

接着按顺序读：

1. [`typed-binding-contract.zh.md`](./typed-binding-contract.zh.md)
2. [`evidence-integration.zh.md`](./evidence-integration.zh.md)
3. [`compiler-compatibility-profile.zh.md`](./compiler-compatibility-profile.zh.md)

前面的对象文档主要回答”语义上是什么”，这三篇回答”如何落地”、”如何与 canonical evidence outputs 对齐”以及”如何参与编译兼容性判断”。它们强调：

- binding 是一等、可引用、可组合对象
- binding 直接承载 carriers / surfaces / relations，不再依赖独立的 asset 层；解析时落到底层 `source_objects`
- field binding 的核心是 **类型化 BindingTarget**
- canonical refs / artifact refs 与 semantic refs 必须分层，允许关联但不得互相替代
- join、late arrival、incomplete-window 等属于 binding 的消费约束
- binding 不等于 SQL DSL，也不取代 compiler / IR
- compatibility profile 是独立 artifact，不回流到 object public schema
- profile 只承载编译兼容性与前置能力，不承载 object identity 或 runtime policy

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
| 设计 metric schema / catalog 的人 | `metric-v2-schema`、`entity-schema-contract`、`dimension-schema-contract`、`enum-set-schema-contract` |
| 设计 funnel / experiment / cohort / session 等过程对象的人 | `process-object-schema` |
| 设计 dimension catalog / value governance / drill path 的人 | `dimension-schema-contract`、`enum-set-schema-contract`、`typed-binding-contract` |
| 设计 semantic mapping / physical grounding 的人 | `typed-binding-contract` |
| 设计 compiler profile、validation、IR、lowering 边界的人 | `compiler-compatibility-profile`、`compiler-spec`、`ir-schema-contract` |

## 这一目录中的几个反复出现的共识

虽然每篇文档各自聚焦不同层面，但它们共享几条稳定原则：

- **typed contracts over raw SQL**：外部和中间层都应优先表达类型化语义，而不是 SQL 形状。
- **semantic / physical 分层**：对象层表达”是什么”，binding 层表达”如何落地”，compiler/IR 表达”如何组合与编译”。
- **stable object contract / compiler profile 分层**：对象主 contract 只保留稳定且必须的语义字段；组合兼容、治理与执行前置条件优先进入 compiler profile / governance context；capability 从核心字段推导。
- **stable ref / revision 分层**：semantic ref 是下游 API、intent 和 binding 继续引用的业务 identity；revision 是该 identity 的冻结版本。默认 resolution 使用 latest active revision，historical resolution 必须显式携带 revision。
- **dimensions are first-class semantic axes**：共享维度应是独立对象，而不是继续退回成 metric 上的字符串数组。
- **predicates are first-class filter semantics**：共享过滤语义应通过 `predicate.*` 治理，而不是在 metric、binding、request scope 中各自发明局部 filter DSL。每个 predicate 通过 `allowed_usage` 声明合法消费场景（`metric_qualifier`、`carrier_row_filter`、`request_scope`、`governance_policy`），compiler 在校验阶段强制匹配。
- **process semantics 从 metric 中拆出**：复杂总体构造、路径、阶段、session、实验上下文不应继续塞进 metric。
- **time semantics are layered**：时间语义引用、窗口本体、消费策略三层清晰分离；角色可组合而非排斥。
- **filter semantics are layered by priority**：过滤语义按优先级分为四层：governance policy filter > carrier row filter > request scope > metric business predicate；多 component metric 的 `default_predicate_refs` 与 `qualifier_refs` 不得压平为单个全局 predicate。详细分层公式见 `predicate-schema-contract.zh.md` "Effective Scope 合成"。
- **typed refs 是组合边界**：下游应优先消费 canonical artifact refs，而不是重建上游 scope/time_scope。
- **validation 是编译职责的一部分**：语义不兼容应在 compiler 阶段显式拒绝，而不是拖到 SQL 执行时报错。
- **IR 保持引擎无关**：IR 是稳定的内部语义计划，不应退化为 SQL AST 或 engine-specific DSL。
- **entity 定义独立**：entity contract 在没有 binding 的情况下语义完整，不暴露 process 语义。

## 与其他目录的边界

- `spec/semantic/`：描述 semantic objects、binding、compiler、IR 的设计边界
- `spec/analysis/intents/`：描述 intent 体系、atomic / derived intent 设计
- `docs/api/`：描述外部 HTTP 接口与 wire contract

因此，在阅读或修改本目录时，建议始终保持以下边界：

- 不把 `spec/semantic/` 当成 HTTP 接口文档
- 不把 compiler / IR 设计直接写成 engine-specific 实现说明
- 不把对象 schema 退回成物理字段或 SQL 片段集合

## 一个最简心智模型

如果只记住一个模型，可以记住下面这句：

> `entity` 定义”谁”，`dimension` 定义”按什么轴观察”，`time` 定义”围绕哪种时间语义组织”，`metric` 定义”量什么”，`process object` 定义”总体/过程怎样形成”，`binding` 定义”这些语义对象如何落到物理 carriers”，而 compiler / IR 定义”它们如何被安全地组合成分析计划”。

补充两句：

> entity 定义独立于 binding：entity contract 在没有 binding 的情况下语义完整，binding 只提供物理落地。

> 如果某个字段主要服务 catalog 治理、compiler 兼容性判断或执行引擎决策，而不是对象本体语义，它默认不应进入 public object schema；capability 从核心字段推导。
