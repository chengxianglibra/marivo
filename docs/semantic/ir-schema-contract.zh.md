# Semantic Compiler IR Schema 契约（重构草案）

本文定义 Factum semantic compiler 中 IR（intermediate representation）的目标 schema 契约。

若需要查看 `intent` / `metric` / `process object` 如何进入 compiler 并被转换为 IR，可配合阅读 `docs/semantic/compiler-spec.zh.md`。

本文是**内部语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/asset-schema-contract.zh.md`
- `docs/semantic/typed-binding-contract.zh.md`
- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/metric-process-contract.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/time-schema-contract.zh.md`
- `docs/analysis/intents/`

本文重点回答：

- IR 在 compiler 中的职责边界是什么
- IR 与 `metric` / `process object` / `intent` 的关系是什么
- IR 与 artifact、compile report、engine plan 的关系是什么
- IR 的最小公共 schema 应如何设计
- 如何保证 IR 抽象合理、跨引擎兼容且足以表达 Factum 的语义需求

## Purpose

Factum 已经将外部语义对象分离为：

- `metric`：measurement contract
- `process object`：process contract
- `dimension`：analysis axis contract
- `intent`：analysis action contract

compiler 需要一个稳定的内部目标，把上述三类输入编译为可校验、可追踪、可 lower 的计划结构。这个目标就是 IR。

本文的目标是为 IR 提供一套**typed semantic plan contract**，使其能够：

- 承接 `metric`、`process object`、`intent` 的统一组合
- 在进入 lowering 之前完成显式 compatibility validation
- 以 canonical artifact 和 typed bindings 表达组合关系
- 为不同 engine adapter 提供稳定的 lowering 输入
- 在不暴露 SQL AST 的前提下保留语义、lineage 与 materialization 边界

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

推荐将 Factum compiler 分为五层：

1. **public semantic contracts**
   - `metric`
   - `process object`
   - `dimension`
   - `intent`
2. **normalized compiler inputs**
   - typed metric snapshot
   - typed process snapshot
   - typed dimension snapshot(s)
   - typed intent request snapshot
   - typed upstream artifact refs
3. **semantic IR**
   - engine-agnostic semantic plan
   - artifact declarations
   - input / output bindings
4. **compile artifacts**
   - validation trace
   - semantic error report
   - lowerability precheck result
5. **execution-specific outputs**
   - lowering request / lowering report
   - engine plan
   - final SQL / native query / execution kernel

其中：

- public contracts 负责“声明什么”
- normalized inputs 负责“把外部对象解析成统一输入”
- semantic IR 负责“表达要做的语义计划”
- compile artifacts 负责“记录校验、拒绝和可降解性判断”
- engine layer 负责“在某个执行引擎上如何完成”

## 三个边界原则

### 1. Semantic IR 必须引擎无关

以下内容**不属于** Semantic IR 主契约：

- `target_engine`
- `required_engine_capabilities`
- `fallback_policy`
- `adapter_lowering`
- `materialize_or_rewrite` 之类的执行决策

这些属于 lowering request / lowering report 或 engine plan。

### 2. typed refs 必须有一等 wiring 模型

Factum 的组合边界不是自由 SQL，也不是松散字符串引用，而是：

- canonical artifacts
- typed upstream refs
- slot-aware input bindings

因此，IR 不应只保留 `depends_on`；还必须显式表达“哪个节点消费哪个 artifact、消费时扮演什么语义角色”。

### 3. validation 结果与执行计划必须解耦

IR 的职责是表达**成功编译后的可执行语义计划**。因此：

- 非法组合应产出 typed semantic error，而不是失败的 `IrPlan`
- validation trace 可以与 IR 同时返回，但不应把 `failed` / `skipped` 节点塞进核心 plan
- engine capability 检查属于 compile / lowering 辅助产物，而不是语义节点族

## IR 必须满足的契约条件

### 1. 语义完备

IR 必须能同时承接三类核心语义：

- measurement semantics
- process semantics
- analysis action semantics

还必须能承接与它们直接相关的：

- comparability
- inferential readiness
- quality / freshness / sample sufficiency gates 的结果
- lineage 与 artifact 边界

### 2. 类型显式

IR 不应依赖自由文本、弱约束标签或运行时猜测。以下约束应是 typed 的：

- `contract_mode`
- `population_subject_ref`
- `context_kind` / `entity_ref`
- `observation_grain_ref`
- `sample_kind`
- `value_semantics`
- `additivity`
- `artifact_kind`
- `binding_slot`

### 3. 执行无关

IR 不应直接固化以下内容：

- 物理表名与字段名
- engine-specific SQL 函数
- CTE 拆分方式
- join reordering
- 具体 window frame 语法
- adapter 特有 hint

### 4. 可降解

Semantic IR 本身不承诺所有引擎都支持所有计划，但必须做到：

- 语义需求可被 lowering 明确读取
- lowerability precheck 可明确给出“存在 / 不存在落地路径”
- 若无落地路径，可被显式拒绝

### 5. 可校验

编译必须支持两类结果：

- 合法组合被统一编译为 `IrBundle`
- 非法组合被统一拒绝为 `SemanticCompileError`

系统不应把“语义不兼容”留到最后变成 SQL 失败。

### 6. 可追踪

IR 与 compile artifacts 至少应保留以下可追踪性信息：

- 来源的 `metric` / `process object` / `intent` 引用
- normalization 结果
- typed upstream refs
- artifact lineage
- lowerability precheck 结论
- 输出 artifact / result mode 对应关系

## Compiler / IR / Lowering 的关系

推荐的职责边界如下：

### Compiler

compiler 负责：

- 解析 public semantic contracts
- 执行组合期 validation
- 生成 Semantic IR
- 生成 compile report
- 向 adapter 发起 lowering request

### Semantic IR

Semantic IR 负责：

- 表达“要计算什么”“要产出什么”
- 表达稳定的语义节点和 artifact 关系
- 表达 typed ref / slot binding 的消费关系

Semantic IR 不负责：

- 记录失败校验
- 记录具体目标引擎与 fallback 决策
- 直接决定最终 SQL 长什么样

### Compile Report

Compile Report 负责：

- 记录 validation trace
- 记录 lowerability precheck
- 在失败时承载 typed semantic error

### Engine Plan

engine plan 负责：

- 把 Semantic IR 转译成某个执行引擎可接受的计算策略
- 做 capability matching
- 选择 kernel、materialization 和 adapter-specific rewrite
- 生成 final SQL / native execution

因此，更稳定的关系是：

> public semantic contracts -> normalized inputs -> semantic IR + compile report -> lowering request -> engine plan -> final SQL / native execution

## Compiler phase 建议

### Phase 0：对象发布校验（发布时）

这一步不属于单次编译本身，但应先于编译存在：

- `metric` 发布校验
- `process object` 发布校验
- `intent` schema 发布校验

目的不是生成 IR，而是保证 catalog 中对象本身合法。

### Phase 1：请求归一化

将一次 intent 请求解析为 typed normalized inputs，例如：

- intent kind
- root anchors / typed refs
- scope / time scope
- dimensions
- result mode
- request-time options

### Phase 2：对象解析与 typed snapshot 生成

compiler 将：

- `metric` 解析为 typed metric snapshot
- `process object` 解析为 typed process snapshot
- `intent request` 解析为 typed action snapshot
- upstream refs 解析为 typed artifact ref snapshot

### Phase 3：组合期校验链

建议依次校验：

1. 请求输入形状是否合法
2. intent 是否允许消费当前 slot 组合
3. metric 是否支持该 intent
4. 若存在 compiler compatibility profile，process interface 是否满足其中的 process requirements
5. grain / subject / entity 是否兼容
6. additivity 是否满足 `decompose` / `attribute`
7. inference / comparison / time projection 是否满足 `test` / `validate` / `detect`
8. quality / freshness / governance gate 是否通过
9. 是否存在可 lower 的路径

若失败，应返回 typed semantic error，而不是 engine error。

### Phase 4：IR 构建

在前述校验通过后，compiler 构建：

- semantic nodes
- artifact declarations
- node input bindings
- node output bindings

### Phase 5：Lowering

engine adapter 读取 IR，并执行：

- capability matching
- rewrite / materialization decision
- kernel selection
- final SQL / native execution generation

## IR 的最小公共 Schema

下面给出建议性的最小公共结构。

### 1. Plan Header

```python
from typing import Literal, NotRequired, TypedDict


class IrPlanHeader(TypedDict):
    ir_version: str
    plan_id: str
    plan_kind: Literal["atomic", "derived"]
    root_intent_kind: str
    result_mode: NotRequired[str | None]
```

说明：

- header 只保留 plan 元信息
- `metric_ref` / `process_ref` / `left_process_ref` / `right_process_ref` 不再放在 header 中
- 语义锚点统一进入 typed input snapshots 和 artifact bindings

### 2. Typed Input References（使用引用而非复制）

**设计原则：IR 引用 catalog 对象，不复制全部字段。**

```python
from typing import Literal, NotRequired, TypedDict


class MetricRefSnapshot(TypedDict):
    """只保留 metric 引用和编译时 resolved 的字段"""
    metric_ref: str
    # 只有编译时 resolved/override 的字段才在这里
    resolved_primary_time_ref: NotRequired[str | None]
    resolved_observation_grain_ref: NotRequired[str | None]


class ProcessRefSnapshot(TypedDict):
    """只保留 process 引用和编译时 resolved 的字段"""
    process_ref: str
    resolved_anchor_time_ref: NotRequired[str | None]


class BindingRefSnapshot(TypedDict):
    """只保留 binding 引用"""
    binding_ref: str
    bound_object_ref: str


class IntentRequestSnapshot(TypedDict):
    intent_kind: str
    request_class: Literal["root_metric_process", "typed_ref", "derived_macro"]
    requested_dimensions: NotRequired[list[str] | None]
    requested_result_mode: NotRequired[str | None]
    request_time_scope_ref: NotRequired[str | None]
    request_options: NotRequired[dict[str, str | int | float | bool | None] | None]


class IrInputSnapshot(TypedDict):
    """IR 输入只保留引用，不复制 catalog 对象的全部字段"""
    metric_ref: NotRequired[str | None]  # 只有 ref
    process_refs: NotRequired[list[str] | None]  # 只有 refs
    binding_refs: NotRequired[list[str] | None]  # 只有 refs
    # 编译时 resolved 的字段（可选）
    resolved_metric: NotRequired[MetricRefSnapshot | None]
    resolved_processes: NotRequired[list[ProcessRefSnapshot] | None]
    resolved_bindings: NotRequired[list[BindingRefSnapshot] | None]
    intent_request: IntentRequestSnapshot
```

**删除的 Snapshot 类型：**

- `MetricSemanticSnapshot` → 用 `MetricRefSnapshot` 替代
- `ProcessSemanticSnapshot` → 用 `ProcessRefSnapshot` 替代
- `TimeResolutionSnapshot` → 删除，IR 直接引用 `time.*` refs
- `AssetCarrierSnapshot` → carrier 信息在 binding 中，不需要独立 snapshot
- `ArtifactRefSnapshot` → 用 binding ref 替代

**为什么使用引用而非复制？**

- 避免"双真相"问题（snapshot 与 catalog 对象不一致）
- IR 更轻量，只包含编译时必要的信息
- Catalog 对象修改不需要同步更新 IR
    processes: NotRequired[list[ProcessSemanticSnapshot] | None]
    bindings: NotRequired[list[BindingResolutionSnapshot] | None]
    assets: NotRequired[list[AssetCarrierSnapshot] | None]
    upstream_refs: NotRequired[list[ArtifactRefSnapshot] | None]
    intent_request: IntentRequestSnapshot
    time_resolution: NotRequired[TimeResolutionSnapshot | None]
```

说明：

- 编译快照必须是 typed 的，而不是 `dict[str, Any]`
- 时间相关解析结果应显式进入 typed snapshot，而不是等 lowering 时再从自由字符串重建
- `resolved_filter_time_ref` 只表示 request `time_scope` 的最终过滤目标，不替代 metric / process 自身原有的时间引用
- `upstream_refs` 明确承接 typed ref 入口，而不是只留字符串列表
- `bindings` / `assets` 让 IR 输入快照显式保留 physical grounding，而不回退到裸 physical names
- compiler profile / governance decision 若需要保留，应作为单独的 compile artifacts 或 snapshot refs 存在，而不应在 IR 中重新定义一套 process/metric 语义结构

### 3. Artifact 与 Binding 模型

```python
from typing import Literal, NotRequired, TypedDict


class ArtifactLineageEntry(TypedDict):
    source_artifact_id: str
    relationship: Literal["consumes", "derives_from", "compares", "tests", "projects"]


class IrArtifact(TypedDict):
    artifact_id: str
    artifact_kind: str
    producer_node_id: str
    output_semantics_ref: NotRequired[str | None]
    result_mode: NotRequired[str | None]
    lineage: NotRequired[list[ArtifactLineageEntry] | None]


class InputBinding(TypedDict):
    slot_name: str
    artifact_kind: str
    artifact_id: str
    semantic_role: Literal[
        "source",
        "left",
        "right",
        "compare_source",
        "decompose_source",
        "test_left",
        "test_right",
        "forecast_source",
    ]


class OutputBinding(TypedDict):
    artifact_id: str
    artifact_kind: str


class CarrierBinding(TypedDict):
    binding_ref: str
    asset_ref: str
    consumed_surface_refs: NotRequired[list[str] | None]
```

说明：

- artifact 是 IR 中的一等对象，不再只是 `lineage_refs`
- binding 必须按 slot 和语义角色建模，不能只靠 `depends_on`
- typed-ref intent 的组合关系通过 binding 表达，而不是通过重建 root anchors
- physical grounding 通过 `CarrierBinding` 表达，而不是通过裸表名 / 裸列名表达

### 4. Node Header

```python
from typing import Literal, NotRequired, TypedDict


class IrNodeHeader(TypedDict):
    node_id: str
    node_type: Literal["measurement", "process", "intent"]
    depends_on: NotRequired[list[str] | None]
    input_bindings: NotRequired[list[InputBinding] | None]
    output_bindings: NotRequired[list[OutputBinding] | None]
    carrier_bindings: NotRequired[list[CarrierBinding] | None]
```

### 5. Measurement Node

```python
from typing import Literal, NotRequired, TypedDict


class MeasurementNode(IrNodeHeader):
    node_type: Literal["measurement"]
    metric_ref: str
    observed_entity_ref: str
    observation_grain_ref: str
    sample_kind: Literal["numeric", "rate", "binary", "survival"]
    value_semantics: str
    additivity: Literal["additive", "semi_additive", "non_additive"]
    inferential_summary_mode: NotRequired[str | None]
```

### 6. Process Node

```python
from typing import Literal, NotRequired, TypedDict


class ProcessNode(IrNodeHeader):
    node_type: Literal["process"]
    process_ref: str
    process_type: str
    contract_mode: Literal["context_provider", "entity_stream"]
    population_subject_ref: str
    context_kind: NotRequired[str | None]
    entity_ref: NotRequired[str | None]
    emitted_grain_ref: NotRequired[str | None]
    membership_cardinality: NotRequired[
        Literal["exclusive_one", "repeatable_many"] | None
    ]
    subject_cardinality: NotRequired[Literal["one", "many"] | None]
```

### 7. Intent Node

```python
from typing import Literal, NotRequired, TypedDict


class IntentNode(IrNodeHeader):
    node_type: Literal["intent"]
    intent_kind: str
    intent_level: Literal["root", "expanded_atomic"]
    requested_dimensions: NotRequired[list[str] | None]
    requested_result_mode: NotRequired[str | None]
```

说明：

- derived intent 可以保留一个顶层 `IntentNode(intent_level="root")`
- 内部 atomic expansion 使用 `intent_level="expanded_atomic"`
- 不再通过 `ValidationNode` 或 `ExecutionBoundaryNode` 表达编译结果

### 8. Compile Report

```python
from typing import Literal, NotRequired, TypedDict


class ValidationRecord(TypedDict):
    validation_kind: Literal[
        "request_shape",
        "intent_support",
        "process_compatibility",
        "comparability_gate",
        "inference_gate",
        "quality_gate",
        "freshness_gate",
        "sample_sufficiency_gate",
        "lowerability_precheck",
    ]
    status: Literal["passed"]
    reason_code: NotRequired[str | None]


class SemanticCompileError(TypedDict):
    error_code: str
    failed_gate: str
    message: str


class LoweringRequirement(TypedDict):
    requirement_kind: str
    source_node_id: str


class CompileReport(TypedDict):
    validation_trace: list[ValidationRecord]
    lowering_requirements: NotRequired[list[LoweringRequirement] | None]
```

说明：

- 成功编译后的 `CompileReport` 只保留通过的 validation trace
- 失败时返回 `SemanticCompileError`，而不是带 `failed` 节点的 `IrPlan`
- lowering requirement 描述“需要什么能力”，不提前绑定某个 engine

### 9. Lowering Request / Report

```python
from typing import Literal, NotRequired, TypedDict


class LoweringRequest(TypedDict):
    target_engine: str
    ir_plan_id: str
    requirements: list[LoweringRequirement]


class LoweringDecision(TypedDict):
    source_node_id: str
    decision_kind: Literal["direct_lower", "rewrite", "materialize"]
    selected_engine_capability: NotRequired[str | None]


class LoweringReport(TypedDict):
    target_engine: str
    decisions: list[LoweringDecision]
```

说明：

- `target_engine` 与 fallback 决策属于 lowering，而不是 Semantic IR
- `materialize` / `rewrite` 是 lowering report 的决策，不再作为语义节点出现

### 10. IR Bundle

```python
from typing import TypedDict


class IrPlan(TypedDict):
    header: IrPlanHeader
    inputs: IrInputSnapshot
    artifacts: list[IrArtifact]
    nodes: list[MeasurementNode | ProcessNode | IntentNode]


class IrBundle(TypedDict):
    plan: IrPlan
    compile_report: CompileReport
```

## 为什么这种抽象更合理

### 1. graph 才是唯一语义事实来源

旧设计把 root refs 放在 header，同时又在 nodes 中重复表达，会形成双重真相。重构后：

- header 只保留 plan 元信息
- roots、refs、上游 artifact 全部进入 typed snapshots 与 bindings
- graph 和 artifact wiring 成为唯一语义事实来源

### 2. typed ref 组合不再悬空

像 `compare(left_ref, right_ref)`、`decompose(compare_ref, dimension)`、`forecast(source_ref)` 这类组合，都必须知道：

- 消费的是哪类 artifact
- 在当前节点里扮演哪个 slot
- 输出的新 artifact 是什么

artifact + binding 模型正是为了解决这个问题。

### 3. compile 失败与 lowering 决策不再污染 IR

把失败校验和引擎决策移出 IR 后：

- 成功计划的语义更稳定
- cache key / audit key 更稳定
- 引擎切换不会改写语义 plan 本身

## 如何保证跨引擎兼容性

Semantic IR 不需要承诺所有引擎都支持所有计划，但必须承诺：

- 每个节点的 lowering requirement 可描述
- lowering request 可以独立携带目标引擎
- adapter 可以把 capability matching 与 fallback 决策写入 lowering report

这样，IR 与引擎之间的关系就是：

- IR 表达语义和 artifact 关系
- compile report 表达 lowerability 前提
- lowering report 决定具体如何落地

## 典型编译样例

### 1. `conversion_rate + experiment_context + validate`

IR 至少应包括：

- 一个 `ProcessNode`：提供 experiment split context resolution
- 一个 `MeasurementNode`：表达 rate measurement
- 一个顶层 `IntentNode(intent_kind="validate")`
- 两个由内部 expansion 生成的 `observe` / `test` 相关 `IntentNode`
- 左右 observation artifact 与 test artifact 的显式 bindings
- `CompileReport.validation_trace` 中的 `inference_gate`

### 2. `retention_rate + cohort_definition + compare`

IR 至少应包括：

- 左右两个上游 observation artifact ref
- 一个 `IntentNode(intent_kind="compare")`
- `left` / `right` 两个 input bindings
- 一个 compare artifact
- `CompileReport.validation_trace` 中的 `comparability_gate`

### 3. `avg_watch_time + session_contract + detect`

IR 至少应包括：

- 一个 session `ProcessNode`
- 一个 numeric `MeasurementNode`
- 一个 `IntentNode(intent_kind="detect")`
- 输出 time-series artifact 的声明
- `CompileReport.validation_trace` 中的 `lowerability_precheck`

## 典型拒绝场景

以下情况应在 compiler / compile report 阶段明确拒绝：

- `non_additive` rate metric 请求 additive `decompose`
- 若存在 compiler profile，process 的 time projection 规则不满足却请求 `detect`
- 若存在 compiler profile，process 的 inference 规则不满足却请求 `validate`
- 若存在 compiler profile，左右 process 的 comparison 规则不兼容却请求 `compare`
- metric `observation_grain_ref` 与 process `emitted_grain_ref` 不兼容
- 不存在可 lower 的路径

这些都应产出 `SemanticCompileError`，而不是运行到 SQL 层才失败。

## Validator / Compiler / Adapter 的边界

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
- build semantic IR
- produce compile report

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
- artifact 是执行前后组合边界
- lowering report 是 engine-specific 执行决策记录

IR 不应替代：

- `docs/analysis/intents/` 中的 typed intent schema
- `docs/api/intent-steps.md` 中的外部 step submission surface

更准确的关系是：

> typed intent step 进入 semantic compiler 后，结合 `metric` 与 `process object` 被编译为 Semantic IR；IR 再通过 lowering request 进入 adapter，并最终产生 engine plan 与 artifact。

## 迁移建议

建议按以下顺序演进：

### 第一阶段：补齐 IR 文档与术语

- 统一 `typed input snapshot`、`artifact binding`、`compile report`、`lowering report` 的命名
- 删除 IR 中的 engine-specific 字段

### 第二阶段：将现有 compiler 逻辑按 phase 收敛

- resolve
- normalize
- validate
- build semantic IR
- emit compile report
- lower

### 第三阶段：建立 lowering requirement matrix

- 明确每类节点会提出哪些 lowering requirements
- 明确哪些 requirements 可 direct lower / rewrite / materialize

### 第四阶段：为典型 intent 建立 golden compilation cases

建议至少覆盖：

- `observe`
- `compare`
- `validate`
- `attribute`
- `detect`

验证目标包括：

- 是否能产出稳定 Semantic IR
- 是否能产出稳定拒绝错误
- 是否能在不同 engine 上共享同一语义 plan

## 总结

对 Factum 来说，IR 最合适的定位不是 SQL AST，也不是新的外部语义层，而是：

- compiler 的稳定内部目标
- 承接 `metric` / `process object` / `intent` 的 typed semantic plan
- 以 artifact 和 binding 表达 typed-ref 组合
- 通过 compile report 和 lowering report 与执行层解耦

一句话总结：

> Factum 的 IR 应当保持语义 plan 与引擎决策分层：核心 IR 只表达 typed semantic graph 与 artifact wiring，validation 失败进入 compile error，engine capability / fallback / materialization 进入 lowering report，再由 adapter 将其 lower 为 engine-specific execution plan 与最终 SQL。
