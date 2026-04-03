# Semantic Compiler IR Schema 契约（草案）

本文定义 Factum semantic compiler 中 IR（intermediate representation）的目标 schema 契约。

本文是**内部语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/metric-process-contract.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/analysis/intents/`

本文重点回答：

- IR 在 compiler 中的职责边界是什么
- IR 与 `metric` / `process object` / `intent` 的关系是什么
- IR 与 engine plan / final SQL 的关系是什么
- IR 的最小公共 schema 应如何设计
- 如何保证 IR 抽象合理、跨引擎兼容且足以表达 Factum 的语义需求

## Purpose

Factum 已经将外部语义对象分离为：

- `metric`：measurement contract
- `process object`：process contract
- `intent`：analysis action contract

接下来 compiler 需要一个稳定的内部目标，把上述三类输入编译为可校验、可降解、可追踪的计划结构。这个目标就是 IR。

本文的目标是为 IR 提供一套**typed semantic plan contract**，使其能够：

- 承接 `metric`、`process object`、`intent` 的统一组合
- 在进入执行层前完成显式 compatibility validation
- 为不同 engine adapter 提供稳定的 lowering target
- 在不暴露 SQL AST 的前提下保留语义、lineage 与 materialization boundary

## 非目标

本文明确不追求：

- 把 IR 做成外部 API contract
- 把 IR 做成 SQL AST 的别名
- 把 IR 做成 engine-specific execution DSL
- 让任意 `metric`、任意 `process object`、任意 `intent` 都能无条件组合
- 在 IR 主契约中暴露物理字段名、join graph、CTE 结构或方言 SQL 片段
- 让 Agent 或外部调用方直接操纵 IR 节点

一句话说，IR 不是面向用户的查询语言，而是 compiler 的稳定内部语义目标。

## 核心设计结论

推荐将 Factum compiler 分为四层：

1. **public semantic contracts**
   - `metric`
   - `process object`
   - `intent`
2. **normalized compiler inputs**
   - resolved metric contract
   - normalized process contract
   - normalized intent request
3. **IR**
   - typed semantic plan
   - validation-aware
   - engine-agnostic
4. **execution-specific outputs**
   - engine plan
   - final SQL / native query / execution kernel

其中：

- public contracts 负责“声明什么”
- normalized inputs 负责“把外部对象解析成统一输入”
- IR 负责“表达要做的语义计划”
- engine layer 负责“在某个执行引擎上如何完成”

## 为什么需要单独的 IR 契约

如果没有稳定 IR，系统很容易退化为两种不理想方式：

- 方式一：让 `metric` / `process object` 直接携带大量执行细节
- 方式二：让 compiler 直接把 public schema 临时拼成 SQL

这两种方式都会导致：

- 语义边界和执行边界混淆
- 兼容性规则分散在 ad hoc 编译逻辑中
- engine 切换或优化时难以保证行为一致
- lineage、materialization、error reporting 难以稳定表达

IR 的价值就在于把这些复杂度收敛到一个**稳定、受类型约束、可解释**的内部层。

## IR 必须满足的契约条件

IR 至少应满足以下条件。

### 1. 语义完备

IR 必须能同时承接三类核心语义：

- measurement semantics
- process semantics
- analysis action semantics

还必须能承接与它们直接相关的：

- comparability
- inferential readiness
- quality / freshness / sample sufficiency gates
- lineage 与 execution boundary

如果某类信息直接影响“是否可比较、是否可检验、是否可执行”，那它要么在 IR 中有显式位置，要么在 IR 生成前被显式校验并沉淀为判定结果。

### 2. 类型显式

IR 不应依赖自由文本、弱约束标签或运行时猜测。以下约束应是 typed 的：

- `population_subject`
- `emitted_entity`
- `observation_grain`
- `sample_kind`
- `value_semantics`
- `additivity`
- `provided_capabilities`
- `required_capabilities`
- `comparison_contract`
- `time_projection_support`
- `inference_support`

### 3. 执行无关

IR 不应直接固化以下内容：

- 物理表名与字段名
- engine-specific SQL 函数
- CTE 拆分方式
- join reordering
- 具体 window frame 语法
- adapter 特有 hint

这些属于 lowering / engine plan 层，不属于 IR 主契约。

### 4. 可降解

IR 中每个节点都必须满足以下之一：

- 可被某类 engine adapter 直接 lower
- 可通过 materialization / rewrite / kernel selection 间接 lower
- 若目标引擎不支持，可被**显式拒绝**

IR 不应引入“理论上有语义，但没有任何可落地路径”的悬空节点。

### 5. 可校验

IR 必须支持两类结果：

- 合法组合被统一编译
- 非法组合被统一拒绝

系统不应把“语义不兼容”留到最后变成 SQL 失败。

### 6. 可追踪

IR 至少应保留以下可追踪性信息：

- 来源的 `metric` / `process object` / `intent` 引用
- normalization 结果
- validation 决策
- execution boundary
- materialization boundary
- 输出 artifact / result mode 对应关系

## IR 与 compiler / engine / SQL 的关系

推荐的职责边界如下：

### Compiler

compiler 负责：

- 解析 public semantic contracts
- 执行组合期 validation
- 生成 IR
- 根据目标引擎把 IR lower 为 engine plan

### IR

IR 负责：

- 表达“要计算什么”“要验证什么”“要产出什么”
- 表达稳定的语义节点和节点关系
- 表达 lowering 所需的边界与要求

IR 不负责：

- 直接决定最终 SQL 长什么样
- 决定某引擎的最优 join / CTE / UDF 写法

### Engine Plan

engine plan 负责：

- 把 IR 转译成某个执行引擎可接受的计算策略
- 选择 kernel、materialization 和 adapter-specific rewrite
- 确认某些节点由 SQL、UDF、原生算子还是多阶段执行承接

### Final SQL

final SQL 只是 engine plan 的一种产物。对某些引擎，也可能是：

- 原生 query object
- pipeline stage graph
- engine-specific operator tree
- SQL + temporary materialization steps 的组合

因此，更稳定的关系是：

> public semantic contracts -> normalized inputs -> IR -> engine plan -> final SQL / native execution

## Compiler phase 建议

建议 compiler 至少显式划分以下阶段。

### Phase 0：对象发布校验（发布时）

这一步不属于单次编译本身，但应先于编译存在：

- `metric` 发布校验
- `process object` 发布校验
- `intent` schema 发布校验

目的不是生成 IR，而是保证 catalog 中对象本身合法。

### Phase 1：请求归一化

将一次 intent 请求解析为 normalized inputs，例如：

- intent kind
- metric ref
- process ref / left_process / right_process
- dimensions
- result mode
- scope / time scope / comparison side bindings

这里需要强调：

- `process input` 不应是新的 public semantic object
- 更合适的做法是：`process_ref + request-time bindings / overrides`

也就是说，request 层可以有 process input，但它只是实例化某个 process object 的输入绑定，不应与 `metric`、`process object`、`intent` 并列为新的语义主对象。

### Phase 2：对象解析与 normalized contract 生成

compiler 将：

- `metric` 解析为 normalized measurement contract
- `process object` 解析为 normalized process contract
- `intent request` 解析为 normalized action contract

其中，`process object` 至少建议经历：

1. subtype payload -> normalized output contract
2. normalized output contract + request bindings -> normalized process contract

### Phase 3：组合期校验链

这是 compiler 中最关键的语义 gate。建议依次校验：

1. 请求输入形状是否合法
2. intent 是否允许消费当前 slot 组合
3. metric 是否支持该 intent
4. process output 是否满足 metric `required_process_contract`
5. grain / subject / entity 是否兼容
6. additivity 是否满足 `decompose` / `attribute`
7. inference / comparison / time projection 是否满足 `test` / `validate` / `detect`
8. quality / freshness / governance gate 是否通过

若失败，应返回 typed semantic error，而不是 engine error。

### Phase 4：IR 构建

在前述校验通过后，compiler 构建 IR plan：

- measurement nodes
- process nodes
- intent nodes
- validation nodes
- execution boundary nodes

### Phase 5：Lowering 与 engine capability matching

engine adapter 读取 IR，并执行：

- capability matching
- rewrite / materialization decision
- kernel selection
- final SQL / native execution generation

## IR 的最小公共 Schema

下面给出一个建议性的最小公共结构。

### 1. Plan Header

```python
from typing import Literal, NotRequired, TypedDict


class IrPlanHeader(TypedDict):
    ir_version: str
    intent_kind: str
    result_mode: NotRequired[str | None]
    target_engine: NotRequired[str | None]
    metric_ref: NotRequired[str | None]
    process_ref: NotRequired[str | None]
    left_process_ref: NotRequired[str | None]
    right_process_ref: NotRequired[str | None]
```

说明：

- `metric_ref` / `process_ref` 保留 lineage 入口
- `target_engine` 不是语义主键，只是 lowering 上下文
- 双侧比较类 intent 可以使用 `left_process_ref` / `right_process_ref`

### 2. Normalized Input Snapshot

```python
from typing import Any, NotRequired, TypedDict


class IrInputSnapshot(TypedDict):
    normalized_metric: NotRequired[dict[str, Any] | None]
    normalized_process: NotRequired[dict[str, Any] | None]
    normalized_left_process: NotRequired[dict[str, Any] | None]
    normalized_right_process: NotRequired[dict[str, Any] | None]
    normalized_intent: dict[str, Any]
```

说明：

- 这里的目标是保留“编译时解释后的统一输入”
- 它不是 public schema 的简单复制
- 也不应直接嵌入 engine-specific plan

### 3. Node Header

```python
from typing import Literal, NotRequired, TypedDict


class IrNodeHeader(TypedDict):
    node_id: str
    node_type: Literal[
        "measurement",
        "process",
        "intent",
        "validation",
        "execution_boundary",
    ]
    depends_on: NotRequired[list[str] | None]
    lineage_refs: NotRequired[list[str] | None]
```

### 4. Measurement Node

```python
from typing import Literal, NotRequired, TypedDict


class MeasurementNode(IrNodeHeader):
    node_type: Literal["measurement"]
    metric_ref: str
    observed_entity: str
    observation_grain: str
    sample_kind: Literal["numeric", "rate", "binary", "survival"]
    value_semantics: str
    additivity: Literal["additive", "semi_additive", "non_additive"]
    aggregation_scope: NotRequired[str | None]
    measurement_components: NotRequired[list[str] | None]
    inferential_summary_mode: NotRequired[str | None]
```

说明：

- `measurement_components` 可引用 numerator / denominator / value source 的 normalized component
- `inferential_summary_mode` 表示本次计划是否要求 sample summary prep

### 5. Process Node

```python
from typing import Literal, NotRequired, TypedDict


class ProcessNode(IrNodeHeader):
    node_type: Literal["process"]
    process_ref: str
    process_type: str
    population_subject: str
    emitted_entity: str
    entity_grain: str
    cardinality_per_subject: Literal["one", "many"]
    anchor_time_ref: NotRequired[str | None]
    provided_capabilities: list[str]
    exported_dimension_refs: NotRequired[list[str] | None]
    comparison_contract_ref: NotRequired[str | None]
    time_projection_support_ref: NotRequired[str | None]
    inference_support_ref: NotRequired[str | None]
```

说明：

- 这里表达的是“统一过程结果接口”，而不是 subtype 原始 payload
- subtype 细节可在 compiler normalization 中解析，但不必全部直接暴露在 IR 公共头部中

### 6. Intent Node

```python
from typing import NotRequired, TypedDict


class IntentNode(IrNodeHeader):
    node_type: Literal["intent"]
    intent_kind: str
    requested_dimensions: NotRequired[list[str] | None]
    requested_result_mode: NotRequired[str | None]
    requested_scope_ref: NotRequired[str | None]
    requested_time_scope_ref: NotRequired[str | None]
    output_artifact_kind: NotRequired[str | None]
```

说明：

- `intent` 仍是动作语义主语
- IR 不替代 typed intent step，而是承接其编译后的内部动作计划

### 7. Validation Node

```python
from typing import Literal, NotRequired, TypedDict


class ValidationNode(IrNodeHeader):
    node_type: Literal["validation"]
    validation_kind: Literal[
        "intent_support",
        "process_compatibility",
        "comparability_gate",
        "inference_gate",
        "quality_gate",
        "freshness_gate",
        "sample_sufficiency_gate",
        "engine_capability_gate",
    ]
    status: Literal["passed", "failed", "skipped"]
    reason_code: NotRequired[str | None]
    details_ref: NotRequired[str | None]
```

说明：

- 并非所有 gate 都必须作为独立节点暴露给最终执行层
- 但它们应当在 IR 或与 IR 同级的编译产物中有稳定位置

### 8. Execution Boundary Node

```python
from typing import Literal, NotRequired, TypedDict


class ExecutionBoundaryNode(IrNodeHeader):
    node_type: Literal["execution_boundary"]
    boundary_kind: Literal[
        "materialization",
        "adapter_lowering",
        "engine_handoff",
        "lineage_checkpoint",
    ]
    required_engine_capabilities: NotRequired[list[str] | None]
    fallback_policy: NotRequired[Literal["rewrite", "materialize", "reject"] | None]
```

说明：

- 该节点用于显式表达“何处开始需要 engine-specific 决策”
- 这能避免把 lowering 逻辑隐式散落在各类语义节点里

### 9. IR Plan

```python
from typing import TypedDict


class IrPlan(TypedDict):
    header: IrPlanHeader
    inputs: IrInputSnapshot
    nodes: list[
        MeasurementNode
        | ProcessNode
        | IntentNode
        | ValidationNode
        | ExecutionBoundaryNode
    ]
```

## 为什么这种抽象是合理的

### 1. 以 Factum 的重复语义为抽象中心

IR 不应抽象 SQL 技巧，而应抽象 Factum 中反复出现的稳定语义：

- measurement
- process resolution
- analysis action
- validation / governance
- execution boundary

这正是 Factum 在 `metric` / `process object` / `intent` 三层分离后，真正稳定存在的内部问题空间。

### 2. 保留“最小充分抽象”

IR 需要避免两个极端：

- 过低：退化成 SQL AST 或 adapter DSL
- 过高：只有一些空泛概念，无法 lower

更好的原则是：

- 对经常复用的语义做一等建模
- 对容易随引擎变化的执行技巧不做主抽象
- 对复杂 subtype 的实现细节保留在 normalization / adapter 中

### 3. 把复杂性集中在可解释边界

复杂场景如：

- experiment contamination exclusion
- funnel matching
- sessionization
- path search
- lifecycle resolution

都应以 `ProcessNode + ExecutionBoundaryNode` 的方式进入 IR，而不是让外部 schema 直接背负这些实现细节。

## 如何保证跨引擎兼容性

IR 不需要承诺所有引擎都支持所有节点，但必须承诺：

- 每个节点的能力需求可描述
- 每个目标引擎的能力边界可匹配
- 不支持时可明确 reject

推荐引入 engine capability matrix。

### Engine Capability 示例

```python
from typing import Literal, TypedDict


EngineCapability = Literal[
    "basic_aggregate",
    "window_function",
    "distinct_count",
    "percentile_exact",
    "percentile_approx",
    "session_partition",
    "sequence_match",
    "state_resolution",
    "survival_estimation",
    "temporary_materialization",
]


class EngineCapabilityMatrix(TypedDict):
    engine_name: str
    supported_capabilities: list[EngineCapability]
```

建议 adapter 在 lowering 时执行：

- `ProcessNode` / `MeasurementNode` 的 capability 匹配
- `ExecutionBoundaryNode.required_engine_capabilities` 检查
- 必要时的 rewrite / materialize / reject

这样，IR 与引擎之间的关系就是：

- IR 表达语义所需能力
- adapter 判断目标引擎是否满足这些能力
- engine plan 决定具体如何落地

## 如何保证表达能力覆盖 Factum 需求

判断 IR 是否足够，不应看它能否复制任意 SQL，而应看它能否稳定承接 Factum 的主要组合。

至少应覆盖以下模式：

- `observe(metric + process)`
- `compare(metric + left_process + right_process)`
- `validate(metric + experiment_context)`
- `attribute(metric + funnel_definition + dimensions)`
- `detect(metric + session_contract)`
- `observe(metric + lifecycle_state_machine)`

也就是说，IR 至少要能表达：

- 单侧 observation
- 双侧 comparison
- inferential preparation
- decomposition / attribution 前置约束
- time projection
- process-derived dimensions

如果一个 semantic 组合能被 public contracts 合法表达，但 IR 无法表示，那说明 IR 抽象不完整。

## 典型编译样例

### 1. `conversion_rate + experiment_context + validate`

IR 至少应包括：

- 一个 `ProcessNode`：提供 `variant_split + population_membership + sample_summary_prep`
- 一个 `MeasurementNode`：表达 rate measurement
- 一个 `ValidationNode`：检查 inference gate
- 一个 `IntentNode`：表达 `validate`
- 一个 `ExecutionBoundaryNode`：交给实验分析 adapter / engine lowering

### 2. `retention_rate + cohort_definition + compare`

IR 至少应包括：

- 左右两个 `ProcessNode`
- 一个 `MeasurementNode`
- 一个 comparability `ValidationNode`
- 一个 `IntentNode(intent_kind="compare")`

### 3. `avg_watch_time + session_contract + detect`

IR 至少应包括：

- 一个 session `ProcessNode`
- 一个 numeric `MeasurementNode`
- 一个 `ValidationNode(validation_kind="engine_capability_gate")`
- 一个 `IntentNode(intent_kind="detect")`

## 典型拒绝场景

以下情况应在 compiler / IR 阶段明确拒绝：

- `non_additive` rate metric 请求 additive `decompose`
- process 无 `time_projection` 能力却请求 `detect`
- process 无 `sample_summary_prep` 却请求 `validate`
- 左右 process `comparison_contract` 不兼容却请求 `compare`
- metric `observation_grain` 与 process `entity_grain` 不兼容
- 目标 engine 不支持所需 capability，且无 `rewrite` / `materialize` 路径

这些都应产出 typed semantic error，而不是运行到 SQL 层才失败。

## Validator / Compiler / Adapter 的边界

推荐按以下方式分责：

### Validator

负责对象发布前的单体合法性：

- metric 自身是否完整
- process object 自身是否完整
- intent schema 是否完整

### Compiler

负责组合期解释与 IR 生成：

- resolve refs
- normalize inputs
- validate combination
- build IR

### Adapter

负责面向目标引擎的 lowering：

- capability matching
- rewrite
- materialization
- SQL / native execution generation

这个边界很重要，因为它避免了：

- validator 变成 ad hoc compiler
- adapter 变成语义决策中心
- SQL 失败替代 semantic error

## 与 typed intent step 的关系

Factum 对外仍应保持 typed analysis step 作为主要交互契约。

因此：

- intent step 是外部动作 contract
- IR 是 compiler 内部 contract
- artifact / projection 是执行后输出 contract

IR 不应替代：

- `docs/analysis/intents/` 中的 typed intent schema
- `docs/api/intent-steps.md` 中的外部 step submission surface

更准确的关系是：

> typed intent step 进入 semantic compiler 后，结合 `metric` 与 `process object` 被编译为 IR；IR 再被 lower 为 engine plan，并最终产生 artifact。

## 迁移建议

建议按以下顺序演进：

### 第一阶段：补齐 IR 文档与术语

- 统一 `normalized contract`、`IR node`、`engine plan`、`execution boundary` 的命名
- 明确 validator / compiler / adapter 的边界

### 第二阶段：将现有 compiler 逻辑按 phase 收敛

- resolve
- normalize
- validate
- build IR
- lower

### 第三阶段：建立 engine capability matrix

- 明确每个 adapter 支持哪些 capability
- 明确哪些节点可 rewrite / materialize / reject

### 第四阶段：为典型 intent 建立 golden compilation cases

建议至少覆盖：

- `observe`
- `compare`
- `validate`
- `attribute`
- `detect`

验证目标包括：

- 是否能产出稳定 IR
- 是否能产出稳定拒绝错误
- 是否能在不同 engine 上保持一致语义

## 总结

对 Factum 来说，IR 最合适的定位不是 SQL AST，也不是新的外部语义层，而是：

- compiler 的稳定内部目标
- 承接 `metric` / `process object` / `intent` 的 typed semantic plan
- 兼容 validation、lineage 与 execution boundary 的统一表示
- 供不同 engine adapter 降解为 execution plan 的中间契约

一句话总结：

> Factum 的 IR 应当以“语义完备、类型显式、执行无关、可降解、可校验、可追踪”为设计原则，把 `metric` / `process object` / `intent` 的受约束组合编译为稳定的 typed semantic plan，再由 adapter 将其 lower 为 engine-specific execution plan 与最终 SQL。
