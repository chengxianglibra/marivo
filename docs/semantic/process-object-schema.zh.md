# Semantic Layer Process Object Schema（草案）

本文定义 Factum semantic layer 中 `process object` 的目标 schema 方向。

本文是**语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它关注的是：

- 哪些字段应作为稳定的 public semantic contract
- 哪些信息应下沉到 compiler / IR
- process object 如何与 `metric`、`intent` 做优雅组合

相关背景见：

- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/metric-process-contract.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/time-schema-contract.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`

## Purpose

本文回答四个问题：

- process object 的公共 schema 应长什么样
- 哪些信息属于 process object 的稳定语义边界
- 哪些信息不应泄漏到 public schema
- compiler / validator 应如何消费这些对象

## 设计目标

该 schema 设计应同时满足：

- **可实现**：不会要求 runtime 直接把 public schema 当作 SQL DSL 执行
- **可组合**：可以和 metric / intent 通过 typed contract 组合
- **可治理**：兼容性与校验规则可显式表达
- **可迁移**：允许 compiler 在内部吸收实现复杂度
- **可扩展**：新增 process subtype 时，不必重塑公共模型

## 非目标

本文明确不追求：

- 把所有执行细节暴露成 public schema 字段
- 让 process object 直接表达底层字段名、join plan、window SQL
- 用自由标签系统替代结构化兼容性校验
- 让 process object 自己枚举所有 intent 适配关系
- 让每个 subtype 都设计一套完全不同的下游输出接口

## 核心设计结论

`process object` 的公共 schema 应分为两层：

- **对象头部（header）**：对象身份、生命周期、治理信息
- **接口契约（interface_contract）**：该对象稳定向下游暴露什么类型的分析接口

各 subtype 再在其下表达自己的**过程语义 payload**。

设计上应避免以下反模式：

- 用 `subject_key_semantics` 同时表达总体主体和输出观测实体
- 用 `output_mode = retention_ready / completion_ready / session_ready` 这类面向下游消费的枚举充当主契约
- 用 `compatibility_tags` 充当 metric-process 主兼容机制
- 在 public schema 中暴露 `field`、`dedup_key`、`dedup_strategy`、物理 variant 字段名等执行细节

一句话总结：

> process object 应声明“它稳定提供什么分析接口”，而不是直接暴露“底层如何拼 SQL”。

## 统一建模原则

### 1. 区分总体主体、上下文接口与实体接口

许多过程对象都围绕某个主体定义，但并不都稳定产出同一类“观测实体”。

例如：

- `session_contract` 可能以 `user` 为总体主体
- 但它产出的观测实体是 `session`
- `experiment_context` 也围绕 `user` 定义
- 但它稳定提供的是 experiment split context，而不是一个应被当作独立观测实体消费的 synthetic membership row

因此 schema 中不应只有一个含混的 `subject_key_semantics`，也不应把所有 subtype 都硬塞进同一个 `emitted_entity` 模型，而应明确区分：

- `population_subject_ref`：过程围绕谁定义
- `context_kind`：若它提供的是总体 / 分流上下文
- `entity_ref`：若它产出的是稳定实体流
- `emitted_grain_ref`：若它产出实体流，该实体流对下游暴露的稳定样本粒度

其中命名空间应保持统一：

- `population_subject_ref` 使用 `subject.*`
- `entity_ref` 使用 `entity.*`
- `emitted_grain_ref` 使用 `grain.*`

### 2. 公共 contract 只表达稳定语义，不表达执行策略

像以下内容更适合放在 compiler normalization / IR / adapter 中，而不是 public schema：

- 物理字段名
- join path
- dedup key
- dedup strategy
- 具体 session close SQL
- 具体 sequence matcher kernel

public schema 应使用语义引用（semantic refs）表达“依赖哪个受治理概念”，而不是直接写“在哪个表、哪个字段上做什么”。

### 3. 兼容性以结构化接口为主，不以 tags 为主

process object 与 metric 的主兼容关系不应依赖：

- `compatibility_tags`
- `required_metric_tags`
- `forbidden_metric_tags`

更稳定的做法是：

- process object 先声明稳定 `interface_contract`
- metric / intent 先声明自己真正依赖的结构化前提
- validator 先基于结构化接口判断是否兼容
- 若未来仍需 capability 词表，应独立成 compiler compatibility profile，而不是直接混入 public schema

标签可以保留为 catalog 搜索辅助元数据，但不应是主校验机制。

### 4. “ready for X” 不应做成 public 主枚举

`retention_ready`、`completion_ready`、`session_ready` 这类名称，本质上是面向下游消费者的解释性标签。

更稳定的方式是把它拆成底层可组合能力，例如：

- 是否提供 membership
- 是否提供 anchor time
- 是否提供 variant split
- 是否提供 ordered steps
- 是否提供 session partition
- 是否提供 state assignment
- 是否支持 time projection
- 是否支持 sample summary preparation

然后由 intent / compiler 判断该对象是否“足以支撑 retention / validate / detect”，而不是在 public contract 里写死一组消费型枚举。

## 通用 Schema

### 公共头部

```python
from typing import Literal, NotRequired, TypedDict


class ProcessObjectHeader(TypedDict):
    process_ref: str
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
    process_type: Literal[
        "experiment_context",
        "cohort_definition",
        "funnel_definition",
        "session_contract",
        "path_pattern",
        "lifecycle_state_machine",
    ]
    process_contract_version: str
```

`ProcessObjectHeader` 只保留 process public contract 必须稳定暴露的字段。`status`、`revision`、`lineage`、`quality gates`、搜索别名等 catalog 元数据，应独立于主 schema 存放。

### 字段说明

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `process_ref` | string | yes | process object 的稳定公共引用；必须使用 `process.*`；也是 public contract 主标识 |
| `display_name` | string | no | 面向人类的显示名称 |
| `description` | string | no | 对象语义说明 |
| `process_type` | enum | yes | 过程对象类别 |
| `process_contract_version` | string | yes | 契约版本 |

设计上，公共头部不再直接包含：

- `subject_key_semantics`
- `output_grain`
- `supported_intents`
- `compatibility_tags`
- `required_metric_tags`
- `forbidden_metric_tags`

这些信息要么进入更精确的 `interface_contract`，要么由更高层 catalog 元数据承载。

## 公共子结构

### SemanticRef

用于引用已受治理的语义对象，而不是直接暴露物理字段。

```python
from typing import NotRequired, TypedDict


class SemanticRef(TypedDict):
    ref: str
    description: NotRequired[str | None]
```

可用于引用：

- population rule
- event class
- dimension
- time anchor
- subject semantics
- state predicate

### WindowSpec

```python
from typing import Literal, NotRequired, TypedDict


class WindowOffset(TypedDict):
    value: int | float
    unit: Literal["minute", "hour", "day", "week"]


class WindowSpec(TypedDict):
    anchor_ref: NotRequired[str | None]
    start_offset: NotRequired[WindowOffset | None]
    end_offset: NotRequired[WindowOffset | None]
```

`WindowSpec` 只表达窗口语义，不表达底层窗口如何实现。

其中：

- `anchor_ref` 必须引用已受治理的 `time.*`
- 该 `time.*` 的统一定义见 `time-schema-contract.zh.md`
- 若窗口未显式给出 `anchor_ref`，则应默认继承 `interface_contract.anchor_time_ref`
- 若窗口显式给出不同的 `anchor_ref`，则它表示窗口级 override，而不是替换 process 主锚点

late arrival、open window exclusion、物理消费锚点等实现侧治理，不属于 `WindowSpec` 本体，而属于 typed binding contract 的消费策略。

### PopulationSpec

```python
from typing import Literal, NotRequired, TypedDict


class PopulationSpec(TypedDict):
    base_population_ref: str
    include_refs: NotRequired[list[str] | None]
    exclude_refs: NotRequired[list[str] | None]
    membership_mode: NotRequired[Literal["once", "repeatable", "rolling"] | None]
```

这里的 `*_ref` 应指向受治理的 population / predicate semantics，而不是直接写底层过滤表达式。

### StepSpec

```python
from typing import NotRequired, TypedDict


class StepSpec(TypedDict):
    step_key: str
    event_ref: str
    qualifier_refs: NotRequired[list[str] | None]
```

### StateSpec

```python
from typing import NotRequired, TypedDict


class StateSpec(TypedDict):
    state_key: str
    entry_ref: str
    exit_ref: NotRequired[str | None]
    priority: NotRequired[int | float | None]
```

## 统一接口契约

这是 process object 最关键的公共部分。metric / intent / validator 应优先消费它，而不是直接依赖 subtype 私有字段。

### Compiler Compatibility Profile（待决策）

`provided_capabilities`、`comparison_contract`、`time_projection_support`、`inference_support` 这类字段主要服务 compiler 组合判断。本轮不再把它们视为 process public contract 的默认组成部分；若要长期保留，建议单独定义 compiler compatibility profile，而不是继续混在 `ProcessInterfaceContract` 中。

### ProcessInterfaceContract

```python
from typing import Literal, NotRequired, TypedDict, TypeAlias


class ContextProcessContract(TypedDict):
    contract_mode: Literal["context_provider"]
    context_kind: Literal["cohort_membership", "experiment_split"]
    population_subject_ref: str
    membership_cardinality: Literal["exclusive_one", "repeatable_many"]
    anchor_time_ref: NotRequired[str | None]
    exported_dimension_refs: NotRequired[list[str] | None]


class EntityProcessContract(TypedDict):
    contract_mode: Literal["entity_stream"]
    entity_ref: str
    emitted_grain_ref: str
    population_subject_ref: str
    subject_cardinality: Literal["one", "many"]
    anchor_time_ref: NotRequired[str | None]
    exported_dimension_refs: NotRequired[list[str] | None]


ProcessInterfaceContract: TypeAlias = ContextProcessContract | EntityProcessContract
```

### 字段含义

- `contract_mode`：该对象对下游暴露的是上下文接口还是实体接口
- `population_subject_ref`：过程围绕谁定义，使用稳定 semantic ref
- `context_kind`：适用于 cohort / experiment 这类上下文提供者
- `membership_cardinality`：上下文提供者对同一主体提供的是排他 membership 还是可重复 membership
- `entity_ref`：适用于 session / funnel / path / lifecycle 这类实体流过程；必须使用 `entity.*`
- `emitted_grain_ref`：实体流接口的稳定粒度；必须使用 `grain.*`
- `subject_cardinality`：每个主体最多对应一个还是多个实体
- `anchor_time_ref`：process 的主时间锚点语义；也是窗口未显式指定 `anchor_ref` 时的默认继承来源
- `exported_dimension_refs`：该过程稳定导出的下游可用维度；必须使用 `dimension.*`，例如 `dimension.variant`、`dimension.step`、`dimension.state`

### 设计说明

`interface_contract` 的作用，是把各 subtype 的“下游接口”统一起来，但不要求所有对象都伪装成“实体发射器”。

其中 `exported_dimension_refs` 只声明“这个过程会稳定导出哪些维度轴”，维度值域、层级与取值治理仍由独立的 dimension contract 定义；若导出维度是 `time_derived`，则 process 的 `anchor_time_ref` 或窗口显式 override 后实际暴露的窗口锚点，还必须满足该 dimension 的 `required_time_anchor_ref`。

例如：

- cohort 主要提供 `cohort_membership + anchor_time`
- experiment 主要提供 `experiment_split + anchor_time`
- funnel 主要提供 `ordered_steps + completion_state`
- session 主要提供 `session_partition`
- lifecycle 主要提供 `state_assignment`，有时再提供 `state_transition`

这样，validator 可以先基于统一 contract 做大部分结构判断，只在必要时读取 subtype payload。若未来仍需要更细粒度 capability / inference / comparison 词表，建议独立成 compiler profile，并由你再决定最终发布形态。

## 顶层对象类型

```python
from typing import TypeAlias


class ProcessObjectBase(ProcessObjectHeader):
    interface_contract: ProcessInterfaceContract


ProcessObject: TypeAlias = (
    ExperimentContext
    | CohortDefinition
    | FunnelDefinition
    | SessionContract
    | PathPattern
    | LifecycleStateMachine
)
```

## Type 1: Experiment Context

`experiment_context` 用于稳定定义实验总体、variant 归属以及实验比较边界。

### Schema

```python
from typing import Literal, NotRequired, TypedDict


class ExperimentVariant(TypedDict):
    variant_key: str
    population_ref: str


class ExperimentSplitBasis(TypedDict):
    kind: Literal["assignment", "exposure"]
    basis_ref: str
    resolution: Literal["first", "last"]


class ExperimentContext(ProcessObjectBase):
    process_type: Literal["experiment_context"]
    experiment_key: str
    variants: list[ExperimentVariant]
    split_basis: ExperimentSplitBasis
    analysis_window: NotRequired[WindowSpec | None]
    contamination_policy: NotRequired[
        Literal["allow", "exclude_mixed_subjects", "strict"] | None
    ]
    expected_split: NotRequired[dict[str, int | float] | None]
```

### 设计说明

- 不再暴露 `variant_dimension`
- 不再把 treatment/control 写成底层字段过滤表达式
- 不再内嵌 `default_metric_roles`
- 不再把 experiment split 伪装成 `variant_membership` 这类独立观测实体
- `split_basis.resolution = "all"` 不再属于 `experiment_context`；多次 exposure timeline 应由其他 process subtype 或 lower 层表达
- `analysis_window` 继续属于 process 语义本体；binding 只负责声明该窗口在物理层如何被消费
- attribution basis / attribution window / baseline alignment 这类过程型时间语义，也应优先放在 process object 或 compiler policy，而不是回写进 metric 本体

`default_metric_roles` 属于更高层的 analysis package / orchestration 配置，而不属于实验过程对象的本体语义。

下面的 subtype 示例为**过渡期合并视图**：其中若出现 capability / comparison / inference 一类字段，应理解为可选 compiler compatibility profile，而不是 process public contract 的必需部分。

### 示例

```json
{
  "name": "experiment.exp_123",
  "process_type": "experiment_context",
  "process_contract_version": "v2",
  "quality_gate_refs": ["gate.srm", "gate.experiment_contamination"],
  "interface_contract": {
    "contract_mode": "context_provider",
    "context_kind": "experiment_split",
    "population_subject_ref": "subject.user",
    "membership_cardinality": "exclusive_one",
    "anchor_time_ref": "time.exposure_time",
    "exported_dimension_refs": ["dimension.variant"],
    "provided_capabilities": [
      "population_membership",
      "anchor_time",
      "variant_split",
      "sample_summary_prep"
    ],
    "comparison_contract": {
      "comparable_with_same_contract": true,
      "requires_same_anchor_semantics": true,
      "requires_same_window_semantics": true
    },
    "time_projection_support": {
      "supported": true,
      "default_time_ref": "time.exposure_time",
      "bucket_stability": "required"
    },
    "inference_support": {
      "supports_population_split": true,
      "supports_pairwise_comparison": true,
      "supports_sample_summary_prep": true
    }
  },
  "experiment_key": "exp_123",
  "variants": [
    { "variant_key": "treatment", "population_ref": "population.exp_123_treatment" },
    { "variant_key": "control", "population_ref": "population.exp_123_control" }
  ],
  "split_basis": {
    "kind": "exposure",
    "basis_ref": "event.experiment_exposure",
    "resolution": "first"
  },
  "analysis_window": {
    "anchor_ref": "time.exposure_time",
    "start_offset": { "value": 0, "unit": "day" },
    "end_offset": { "value": 7, "unit": "day" }
  },
  "contamination_policy": "exclude_mixed_subjects",
  "expected_split": {
    "treatment": 0.5,
    "control": 0.5
  }
}
```

## Type 2: Cohort Definition

`cohort_definition` 用于稳定定义 cohort membership、anchor time 与观察窗口。

### Schema

```python
from typing import Literal, NotRequired


class CohortDefinition(ProcessObjectBase):
    process_type: Literal["cohort_definition"]
    cohort_key: str
    entry_population: PopulationSpec
    cohort_anchor_ref: str
    observation_window: NotRequired[WindowSpec | None]
    return_population_ref: NotRequired[str | None]
    return_anchor_ref: NotRequired[str | None]
```

### 设计说明

- 不再使用 `output_mode = retention_ready`
- “是否可做留存”由 `interface_contract` + `return_population_ref` + `observation_window` 共同决定
- cohort 不再通过 `subject_membership` 这种 synthetic entity 暴露自己，而是直接声明自己是 `cohort_membership` context provider
- `observation_window` 的长度与锚点属于 process 本体；late arrival / incomplete-window 行为应在 binding 层补充

### 示例

```json
{
  "name": "cohort.signup_week",
  "process_type": "cohort_definition",
  "process_contract_version": "v2",
  "interface_contract": {
    "contract_mode": "context_provider",
    "context_kind": "cohort_membership",
    "population_subject_ref": "subject.user",
    "membership_cardinality": "exclusive_one",
    "anchor_time_ref": "time.signup_time",
    "provided_capabilities": [
      "population_membership",
      "anchor_time",
      "windowed_observation",
      "sample_summary_prep"
    ],
    "comparison_contract": {
      "comparable_with_same_contract": true,
      "requires_same_anchor_semantics": true,
      "requires_same_window_semantics": true
    },
    "time_projection_support": {
      "supported": true,
      "default_time_ref": "time.signup_time",
      "bucket_stability": "required"
    },
    "inference_support": {
      "supports_pairwise_comparison": true,
      "supports_sample_summary_prep": true
    }
  },
  "cohort_key": "signup_week",
  "entry_population": {
    "base_population_ref": "population.signed_up_users",
    "membership_mode": "once"
  },
  "cohort_anchor_ref": "time.signup_time",
  "observation_window": {
    "anchor_ref": "time.signup_time",
    "start_offset": { "value": 0, "unit": "day" },
    "end_offset": { "value": 7, "unit": "day" }
  },
  "return_population_ref": "population.active_return_users",
  "return_anchor_ref": "time.activity_time"
}
```

## Type 3: Funnel Definition

`funnel_definition` 用于定义步骤序列、完成条件与过程约束。

### Schema

```python
from typing import Literal, NotRequired, TypedDict


class StepGapSpec(TypedDict):
    value: int | float
    unit: Literal["minute", "hour", "day"]


class FunnelDefinition(ProcessObjectBase):
    process_type: Literal["funnel_definition"]
    funnel_key: str
    steps: list[StepSpec]
    ordering_rule: NotRequired[Literal["strict", "weak"] | None]
    counting_rule: NotRequired[
        Literal["first_pass", "all_passes", "first_success"] | None
    ]
    max_step_gap: NotRequired[StepGapSpec | None]
    conversion_step_key: str
    partition_scope: NotRequired[
        Literal["same_process_instance", "same_session", "cross_session"] | None
    ]
```

### 设计说明

- `funnel_definition` 输出的是 funnel progress 语义，而不是某个最终指标值
- completion / latency 等更细粒度下游能力若需要保留，建议放入 compiler compatibility profile

### 示例

```json
{
  "name": "funnel.checkout",
  "process_type": "funnel_definition",
  "process_contract_version": "v2",
  "interface_contract": {
    "contract_mode": "entity_stream",
    "entity_ref": "entity.path_match",
    "emitted_grain_ref": "grain.path_match",
    "population_subject_ref": "subject.user",
    "subject_cardinality": "many",
    "anchor_time_ref": "time.event_time",
    "exported_dimension_refs": ["dimension.step"],
    "provided_capabilities": [
      "ordered_steps",
      "completion_state",
      "step_latency"
    ],
    "comparison_contract": {
      "comparable_with_same_contract": true,
      "requires_same_partition_semantics": true
    },
    "time_projection_support": {
      "supported": true,
      "default_time_ref": "time.event_time",
      "bucket_stability": "optional"
    }
  },
  "funnel_key": "checkout",
  "steps": [
    { "step_key": "view_product", "event_ref": "event.product_view" },
    { "step_key": "start_checkout", "event_ref": "event.checkout_start" },
    { "step_key": "pay_success", "event_ref": "event.payment_success" }
  ],
  "ordering_rule": "strict",
  "counting_rule": "first_success",
  "max_step_gap": {
    "value": 24,
    "unit": "hour"
  },
  "conversion_step_key": "pay_success",
  "partition_scope": "same_session"
}
```

## Type 4: Session Contract

`session_contract` 用于定义事件流如何被解释为 session 级过程实体。

### Schema

```python
from typing import Literal, NotRequired, TypedDict


class IdleGapSpec(TypedDict):
    value: int | float
    unit: Literal["minute", "hour"]


class SessionContract(ProcessObjectBase):
    process_type: Literal["session_contract"]
    session_key: str
    event_stream_ref: str
    included_event_refs: NotRequired[list[str] | None]
    excluded_event_refs: NotRequired[list[str] | None]
    start_ref: NotRequired[str | None]
    continuation_ref: NotRequired[str | None]
    close_ref: NotRequired[str | None]
    idle_gap: NotRequired[IdleGapSpec | None]
    canonical_session_ref: NotRequired[str | None]
```

### 设计说明

- 不再使用 `event_inclusion` / `event_exclusion` 直接写 predicate
- 不再用 `output_mode = session_ready`
- 若底层已有可复用 session 语义，可通过 `canonical_session_ref` 引用

### 示例

```json
{
  "name": "session.video_session",
  "process_type": "session_contract",
  "process_contract_version": "v2",
  "interface_contract": {
    "contract_mode": "entity_stream",
    "entity_ref": "entity.session",
    "emitted_grain_ref": "grain.session",
    "population_subject_ref": "subject.user",
    "subject_cardinality": "many",
    "anchor_time_ref": "time.session_start_time",
    "provided_capabilities": [
      "session_partition",
      "anchor_time",
      "time_projection"
    ],
    "comparison_contract": {
      "comparable_with_same_contract": true,
      "requires_same_partition_semantics": true
    },
    "time_projection_support": {
      "supported": true,
      "default_time_ref": "time.session_start_time",
      "bucket_stability": "required"
    }
  },
  "session_key": "video_session",
  "event_stream_ref": "stream.video_events",
  "included_event_refs": ["event.play", "event.pause", "event.seek"],
  "excluded_event_refs": ["event.bot_activity"],
  "idle_gap": {
    "value": 30,
    "unit": "minute"
  }
}
```

## Type 5: Path Pattern

`path_pattern` 用于定义可复用的路径匹配语义。

### Schema

```python
from typing import Literal, NotRequired, TypedDict


class PathLagSpec(TypedDict):
    value: int | float
    unit: Literal["minute", "hour", "day"]


class PathPattern(ProcessObjectBase):
    process_type: Literal["path_pattern"]
    path_key: str
    nodes: list[StepSpec]
    match_mode: NotRequired[
        Literal["ordered", "unordered", "contains_subsequence"] | None
    ]
    revisit_policy: NotRequired[Literal["allow", "forbid", "compress"] | None]
    max_path_length: NotRequired[int | float | None]
    max_lag: NotRequired[PathLagSpec | None]
    partition_scope: NotRequired[Literal["same_session", "cross_session"] | None]
```

### 示例

```json
{
  "name": "path.home_to_pay",
  "process_type": "path_pattern",
  "process_contract_version": "v2",
  "interface_contract": {
    "contract_mode": "entity_stream",
    "entity_ref": "entity.path_match",
    "emitted_grain_ref": "grain.path_match",
    "population_subject_ref": "subject.user",
    "subject_cardinality": "many",
    "anchor_time_ref": "time.event_time",
    "provided_capabilities": [
      "path_match",
      "ordered_steps"
    ],
    "comparison_contract": {
      "comparable_with_same_contract": true,
      "requires_same_partition_semantics": true
    }
  },
  "path_key": "home_to_pay",
  "nodes": [
    { "step_key": "home", "event_ref": "event.home_view" },
    { "step_key": "detail", "event_ref": "event.detail_view" },
    { "step_key": "pay", "event_ref": "event.payment_success" }
  ],
  "match_mode": "contains_subsequence",
  "revisit_policy": "allow",
  "max_path_length": 10,
  "max_lag": {
    "value": 1,
    "unit": "day"
  },
  "partition_scope": "same_session"
}
```

## Type 6: Lifecycle State Machine

`lifecycle_state_machine` 用于定义状态赋值与状态迁移语义。

### Schema

```python
from typing import Literal, NotRequired


class LifecycleStateMachine(ProcessObjectBase):
    process_type: Literal["lifecycle_state_machine"]
    machine_key: str
    states: list[StateSpec]
    conflict_resolution: NotRequired[
        Literal["highest_priority", "first_match"] | None
    ]
    evaluation_anchor_ref: NotRequired[str | None]
    transition_anchor_ref: NotRequired[str | None]
```

### 设计说明

- `current_state` / `state_transition_ready` 不再做成 `output_mode`
- 是否支持迁移分析，应优先由结构化 interface 与 subtype payload 判断；若仍需能力词表，交给 compiler profile

### 示例

```json
{
  "name": "lifecycle.user_states",
  "process_type": "lifecycle_state_machine",
  "process_contract_version": "v2",
  "interface_contract": {
    "contract_mode": "entity_stream",
    "entity_ref": "entity.state_assignment",
    "emitted_grain_ref": "grain.state_assignment",
    "population_subject_ref": "subject.user",
    "subject_cardinality": "many",
    "anchor_time_ref": "time.state_eval_time",
    "exported_dimension_refs": ["dimension.state"],
    "provided_capabilities": [
      "state_assignment",
      "state_transition",
      "time_projection"
    ],
    "comparison_contract": {
      "comparable_with_same_contract": true,
      "requires_same_anchor_semantics": true
    },
    "time_projection_support": {
      "supported": true,
      "default_time_ref": "time.state_eval_time",
      "bucket_stability": "required"
    }
  },
  "machine_key": "user_states",
  "states": [
    {
      "state_key": "new",
      "entry_ref": "predicate.state_new",
      "priority": 100
    },
    {
      "state_key": "active",
      "entry_ref": "predicate.state_active",
      "priority": 80
    },
    {
      "state_key": "churn_risk",
      "entry_ref": "predicate.state_churn_risk",
      "priority": 60
    }
  ],
  "conflict_resolution": "highest_priority",
  "evaluation_anchor_ref": "time.state_eval_time",
  "transition_anchor_ref": "time.state_transition_time"
}
```

## 与 Metric / Intent 的兼容方式

### 主原则

下游兼容性应优先基于：

- `interface_contract.contract_mode`
- `interface_contract.population_subject_ref`
- `interface_contract.context_kind` 或 `interface_contract.entity_ref`
- `interface_contract.emitted_grain_ref`（若 `contract_mode = entity_stream`）
- `anchor_time_ref`
- `exported_dimension_refs`

而不是优先基于：

- `process_type`
- 任意自由标签
- ad hoc subtype 特判

### 例子

#### 1. `retention_rate + cohort_definition + compare`

要求 process 至少具备：

- `contract_mode = context_provider`
- `context_kind = cohort_membership`
- 稳定 `population_subject_ref`
- 稳定 `anchor_time_ref`

#### 2. `conversion_rate + experiment_context + validate`

要求 process 至少具备：

- `contract_mode = context_provider`
- `context_kind = experiment_split`
- 稳定 `population_subject_ref`
- 稳定 `anchor_time_ref`

#### 3. `avg_watch_time + session_contract + detect`

要求 process 至少具备：

- `contract_mode = entity_stream`
- `entity_ref = session`
- `emitted_grain_ref = grain.session`
- 稳定 `anchor_time_ref`

#### 4. `user_count + lifecycle_state_machine + observe`

要求 process 至少具备：

- `contract_mode = entity_stream`
- `entity_ref = state_assignment`
- 稳定 `exported_dimension_refs = ["dimension.state"]`

## Validator 建议

### 对单个 process object 的发布校验

建议至少校验：

- `process_type` 与 subtype payload 一致
- `interface_contract` 必填字段齐全
- `interface_contract.contract_mode` 与 subtype 家族一致
- `interface_contract.population_subject_ref` 不应再被 subtype payload 重复声明
- `anchor_time_ref`、`population_ref`、`event_ref` 等引用均可解析
- `steps` / `states` / `variants` 的 key 唯一且非空
- 所有 window 值为正数
- `experiment_context.split_basis` 与 `membership_cardinality` 自洽
- `cohort_definition.entry_population.membership_mode` 与 `membership_cardinality` 自洽

### 组合期校验

建议优先基于统一 contract 做校验：

- process 的 `contract_mode`、`context_kind` / `entity_ref` 是否满足 metric 所需的结构化前提
- process 的 `population_subject_ref` 是否与 metric 对齐
- process 的 `emitted_grain_ref` 是否与 metric `observation_grain_ref` 相容（若该 process 暴露实体流）
- 更细粒度 capability / inference / comparison 规则若存在，应由单独的 compiler profile 判定
- 若请求维度为 `time_derived`，process 的 `anchor_time_ref` 是否满足其 `required_time_anchor_ref`

必要时，再读取 subtype payload 做更细粒度检查，例如：

- funnel 的 `conversion_step_key` 是否存在
- experiment 的 `variants` 是否满足对照结构
- lifecycle 的状态优先级是否能稳定消歧

## 与 IR 的关系

`process object` schema 不应等价于 IR。

更合理的分层是：

- process object schema：外部稳定 contract
- normalized process contract：compiler 解析后的统一中间表示
- IR nodes：面向执行与 lineage 的内部计划图

建议 compiler 至少做两步转换：

1. subtype payload -> normalized interface contract
2. normalized interface contract + metric + intent -> IR

例如：

- `session_contract.idle_gap` 不等于底层 window SQL
- `path_pattern.nodes` 不等于最终 sequence matcher node 图
- `experiment_context.contamination_policy` 不等于具体排除 join plan

## 与 SQL / execution payload 的关系

某些 process object 的实际执行可能非常复杂，例如：

- 实验污染消解
- 漏斗匹配
- session 化
- 路径搜索
- 生命周期状态评估

这些复杂性应由 compiler / execution layer 承接，而不是向 public schema 泄漏为：

- 物理字段名
- dedup 策略
- join graph
- adapter-specific SQL 片段

换句话说：

- process object 负责“是什么过程语义”
- normalized contract 负责“向下游暴露什么接口语义”
- IR / execution 负责“如何执行”

## 迁移建议

若从旧版 schema 迁移，建议按以下方向收敛：

### 1. 删除或降级以下字段

- `subject_key_semantics`
- `output_grain`
- `supported_intents`
- `compatibility_tags`
- `required_metric_tags`
- `forbidden_metric_tags`
- 各 subtype 的 `output_mode`
- `default_metric_roles`

### 2. 将执行型字段替换为 semantic refs

例如：

- `Predicate.field` -> `predicate_ref`
- `TimeAnchor.field` -> `anchor_ref`
- `variant_dimension` -> `dimension.variant` 或 split basis ref
- `event_inclusion` / `event_exclusion` -> `included_event_refs` / `excluded_event_refs`

### 3. 为每个对象补齐统一 `interface_contract`

这是与 metric / intent 优雅适配的关键步骤。

迁移时还应额外处理：

- `subject_ref` -> `interface_contract.population_subject_ref`
- `emitted_entity` -> `context_kind` 或 `entity_ref`
- `entity_grain` / 输出粒度字面量 -> `emitted_grain_ref`
- `cardinality_per_subject` -> `membership_cardinality` 或 `subject_cardinality`

## 总结

更合理的 process object schema，不应把自己做成“可执行 DSL”，也不应把自己做成“面向下游消费模式的枚举集合”。

它应当是：

- 一个稳定的对象头部
- 一个统一的接口契约
- 一个 subtype-specific 的过程语义 payload

这样做的收益是：

- 更容易实现
- 更容易校验
- 更容易和 metric / intent 组合
- 更容易把复杂度收敛到 compiler / IR

一句话总结：

> process object 的公共 contract 应以 `interface_contract + capabilities` 为中心，而不是以 tags、output_mode 或执行细节字段为中心。
