# Semantic Layer Typed Binding Contract（草案）

本文定义 Factum semantic layer 中 `typed binding contract` 的目标 schema。

本文是**语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/entity-schema-contract.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`

本文重点回答：

- semantic object 如何稳定绑定到底层 source objects
- 哪些绑定信息属于 public semantic contract
- 哪些实现细节必须留在 compiler / adapter / execution 层
- `entity`、`process object`、`metric` 各自如何通过 binding contract 落到物理层

## Purpose

本文用于为 binding layer 提供一套更稳定、更受治理的 typed contract，使其能够：

- 把 `entity_ref`、`key_refs`、`primary_time_ref` 等语义引用映射到底层物理 schema
- 把 `process object` 依赖的主体、时间、上下文、join、窗口语义落到稳定 binding slots
- 把 `metric` 依赖的 measure inputs、sample basis、scope basis 映射到底层事实来源
- 让 compiler 在进入 lowering 前完成 binding-aware validation

本文不定义：

- 最终数据库 DDL
- 最终 REST endpoint shape
- 最终 SQL 模板
- engine-specific rewrite 策略
- 具体 join 算法、CTE 形状、window frame 语法

## 背景

当前实现中的 `semantic_mappings` 过于粗粒度，它主要表达：

- semantic object 与 source object 有关联

但它无法稳定表达：

- 哪个字段承担 `subject_key`
- 哪个字段承担 `experiment_id`
- 哪个字段承担 `primary_time_ref`
- 不同对象之间通过什么语义键连接
- 哪些窗口策略、迟到数据策略、行过滤条件属于该对象的受治理绑定约束

这会带来几个问题：

- `entity`、`metric`、`process object` 的公共契约无法真正落地
- 大量关键语义会退回到 `mapping_json`、SQL 片段或人工知识中
- compiler 无法在 binding 层做显式 validation
- experiment / cohort / session 等复杂场景很难原生编译

因此，Factum 需要把 binding 从“粗粒度对象映射”升级为“typed semantic binding contract”。

## 设计目标

typed binding contract 应同时满足：

- **语义稳定**：以 binding slots 表达稳定语义角色，而不是暴露零散字段名
- **对象无关**：可以同时服务 entity、process object、metric
- **可治理**：支持版本、发布校验、兼容性检查
- **可组合**：能表达 source object、field role、join graph、window policy 的组合
- **执行解耦**：允许 compiler / adapter 自主决定具体 SQL 落地方式

## 非目标

本文明确不追求：

- 把 binding contract 设计成 SQL DSL
- 暴露 engine-specific 函数、方言、hint
- 让 binding 直接表达完整查询计划
- 把所有去重、排序、聚合逻辑都塞进 binding
- 让 binding 取代 compiler / IR

## 核心设计结论

binding contract 的职责是回答四个问题：

1. 这个 semantic object 主要依赖哪些 source objects
2. 在这些 source objects 上，哪些字段承担哪些**稳定语义角色**
3. 多个 source bindings 之间允许通过哪些**受治理 join relations** 连接
4. 哪些窗口、时间、迟到数据、过滤策略属于该对象的稳定 binding 约束

它**不应**回答：

- 最终 SQL 怎么写
- join 顺序如何优化
- CTE 如何拆
- 哪个 adapter 具体选哪个 kernel
- 失败时如何 fallback

一句话总结：

> typed binding contract 应声明“这个语义对象依赖哪些受治理的物理绑定槽位”，而不是声明“最终 SQL 应长什么样”。

## 统一建模原则

### 1. binding 通过 binding slots 表达语义角色

binding 的核心不是保存任意 column 名，而是保存：

- `subject_key`
- `primary_time`
- `partition_time`
- `experiment_id`
- `variant_id`
- `measure_input`
- `state_key`
- `descriptor`

这类**稳定语义角色**。

物理字段名只是这些角色的实现映射。

### 2. public binding contract 只保留稳定语义，不暴露执行细节

以下内容不应进入 public binding contract：

- engine-specific SQL 表达式
- optimizer hint
- CTE 名称
- join reorder 策略
- adapter 私有 fallback
- 原始 SQL predicate string

可以保留的，是结构化后的稳定语义，例如：

- predicate ref
- time role
- cardinality expectation
- incomplete-window policy
- late-arrival policy

### 3. binding 应区分 source object binding 与 semantic field binding

建议把 binding 分为两层：

1. **source object binding**
   - 这个语义对象主要绑定到哪些 source objects
2. **semantic field binding**
   - 在这些 source objects 中，哪个字段承担哪个稳定语义角色

如果只保留“对象绑定表”，系统很快又会退回到松散 `mapping_json`。

### 4. join 关系应以受治理 relation 表达，而不是任意 SQL 片段

binding 需要表达 join，但表达方式应是：

- join subjects 是谁
- 使用哪些 semantic key refs
- 允许的基数关系是什么
- 是否存在时间顺序要求

而不是直接暴露完整 SQL `ON ... AND ...` 片段。

### 5. 窗口与迟到数据策略属于 binding 约束，不属于 entity / metric 本体

例如：

- conversion window = 7d
- late arrival grace period = 2d
- incomplete window policy = exclude_open_subjects

这些都属于 binding constraint，因为它们描述的是**底层事实如何被稳定消费**，而不是 entity 或 metric 的本体身份。

## binding 要回答什么

typed binding contract 主要回答：

- 这个对象绑定到哪些 source objects
- 这些 source objects 各自扮演什么 binding role
- 哪些 fields 对应到哪些 semantic refs / semantic roles
- 哪些 join relations 被允许
- 哪些 window / filter / freshness / lateness 约束稳定生效

## binding 不要回答什么

typed binding contract 不回答：

- 查询 DAG 如何展开
- 哪一步先执行
- 哪个 join 用 hash / merge / broadcast
- 样本摘要如何物化
- engine-specific 时间截断怎么写

这些属于 compiler / lowering / execution。

## 通用 Schema

### 公共头部

```python
from typing import Any, Literal, NotRequired, TypedDict


class BindingHeader(TypedDict):
    name: str
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
    binding_scope: Literal["entity", "process_object", "metric"]
    bound_object_ref: str
    status: NotRequired[Literal["draft", "published", "deprecated"]]
    quality_gate_refs: NotRequired[list[str] | None]
    lineage: NotRequired[list[str] | None]
    properties: NotRequired[dict[str, Any] | None]
    revision: NotRequired[int]
    binding_contract_version: str
```

### 字段说明

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `name` | string | yes | binding 唯一名称 |
| `display_name` | string | no | 人类可读显示名 |
| `description` | string | no | binding 语义说明 |
| `binding_scope` | enum | yes | 绑定对象类型：entity / process_object / metric |
| `bound_object_ref` | string | yes | 被绑定的语义对象引用 |
| `status` | enum | no | 生命周期状态 |
| `quality_gate_refs` | array[string] | no | 发布或执行前需满足的治理 gate |
| `lineage` | array[string] | no | 上游依赖对象引用 |
| `properties` | object | no | 辅助元数据，不承载主语义 |
| `revision` | integer | no | 发布版本序号 |
| `binding_contract_version` | string | yes | binding 契约版本 |

## 公共子结构

### SemanticRef

```python
from typing import NotRequired, TypedDict


class SemanticRef(TypedDict):
    ref: str
    description: NotRequired[str | None]
```

### SourceObjectRef

```python
from typing import Literal, NotRequired, TypedDict


class SourceObjectRef(TypedDict):
    source_object: str
    object_type: NotRequired[Literal["table", "view", "materialized_view"] | None]
    source_namespace_ref: NotRequired[str | None]
```

### SourceObjectBinding

```python
from typing import Literal, NotRequired, TypedDict


class SourceObjectBinding(TypedDict):
    binding_key: str
    source: SourceObjectRef
    binding_role: Literal[
        "entity_base",
        "process_base",
        "event_log",
        "fact_table",
        "dimension_table",
        "assignment_log",
        "exposure_log",
        "metric_fact",
        "snapshot_source",
    ]
    grain_ref: NotRequired[str | None]
    primary_entity_ref: NotRequired[str | None]
    row_filter_refs: NotRequired[list[str] | None]
    freshness_policy_ref: NotRequired[str | None]
```

字段含义：

- `binding_key`：该 source binding 在当前 contract 中的局部唯一键
- `source`：绑定到哪个 source object
- `binding_role`：该 source 在当前对象中扮演的稳定角色
- `grain_ref`：该 source 产出或承载的稳定粒度引用；应使用 `grain.*`
- `primary_entity_ref`：该 source 主要围绕哪个实体组织；应使用 `entity.*`
- `row_filter_refs`：结构化的行级过滤语义引用
- `freshness_policy_ref`：时效治理规则引用

### FieldBinding

```python
from typing import Literal, NotRequired, TypedDict


class FieldBinding(TypedDict):
    source_binding_key: str
    semantic_slot: Literal[
        "subject_key",
        "entity_key",
        "parent_key",
        "primary_time",
        "partition_time",
        "event_time",
        "experiment_id",
        "variant_id",
        "state_key",
        "descriptor",
        "measure_input",
        "numerator_input",
        "denominator_input",
        "weight_input",
        "anchor_time",
    ]
    semantic_ref: str
    physical_field_path: str
    field_type_ref: NotRequired[str | None]
    nullability_policy: NotRequired[Literal["reject", "allow", "impute"] | None]
    repeated_value_policy: NotRequired[
        Literal["take_first", "take_last", "aggregate", "explode"] | None
    ]
```

字段说明：

- `semantic_slot`：稳定语义角色
- `semantic_ref`：该字段服务的语义引用，如 `key.user_id`、`time.session_started_at`
- `physical_field_path`：物理字段路径，可是列名或结构化路径
- `field_type_ref`：受治理类型语义引用
- `nullability_policy`：空值治理策略
- `repeated_value_policy`：重复值治理策略

### JoinRelation

```python
from typing import Literal, NotRequired, TypedDict


class JoinRelation(TypedDict):
    relation_key: str
    left_binding_key: str
    right_binding_key: str
    join_kind: NotRequired[Literal["inner", "left", "semi", "anti"] | None]
    key_ref_pairs: list[tuple[str, str]]
    cardinality: NotRequired[
        Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"] | None
    ]
    temporal_constraint_refs: NotRequired[list[str] | None]
    compatibility_rule_refs: NotRequired[list[str] | None]
```

这里的重点是：

- join relation 使用 `key_ref_pairs`
- 时间约束通过 `temporal_constraint_refs`
- 兼容性通过 `compatibility_rule_refs`

而不是直接暴露原始 SQL join 条件。

### WindowPolicySpec

```python
from typing import Literal, NotRequired, TypedDict


class WindowPolicySpec(TypedDict):
    policy_key: str
    policy_type: Literal[
        "attribution_window",
        "observation_window",
        "late_arrival_policy",
        "incomplete_window_policy",
    ]
    anchor_ref: str
    window_ref: NotRequired[str | None]
    grace_period_ref: NotRequired[str | None]
    behavior: NotRequired[
        Literal["exclude_open_subjects", "clip_to_window", "keep_partial"] | None
    ]
```

### BindingInterfaceContract

```python
from typing import TypedDict


class BindingInterfaceContract(TypedDict):
    source_bindings: list[SourceObjectBinding]
    field_bindings: list[FieldBinding]
    join_relations: list[JoinRelation]
    window_policies: list[WindowPolicySpec]
```

### TypedBindingObject

```python
from typing import TypedDict


class TypedBindingObject(TypedDict):
    header: BindingHeader
    interface_contract: BindingInterfaceContract
```

## 设计说明

### 1. `binding_scope` 决定 binding 服务的对象种类

同一套 binding schema 可以同时服务：

- entity
- process object
- metric

但三者关注重点不同：

- entity 更关注 identity / time / descriptors 的落地
- process object 更关注多 source、多 join、多窗口的约束
- metric 更关注 measure inputs、sample basis、numerator / denominator inputs

### 2. `semantic_slot` 是 binding contract 的核心

binding 不应围绕“列名”组织，而应围绕：

- 这个字段在语义上承担什么角色

来组织。这样即使底层字段名变化，只要 slot 与 semantic ref 不变，公共契约就能保持稳定。

### 3. `physical_field_path` 可以出现，但只是实现锚点

这里允许出现物理字段路径，因为 binding 的职责就是把语义 ref 落到物理层。

但 `physical_field_path` 不应扩展成任意 SQL 表达式；它应局限在：

- 列名
- 嵌套字段路径
- 受约束的结构化路径

### 4. join relation 要求结构化，不接受自由 SQL

例如可以表达：

- `assignment_log.subject_key` 对 `exposure_log.subject_key`
- `assignment_log.experiment_id` 对 `exposure_log.experiment_id`
- `exposure_time >= assignment_time`

但这类信息应通过：

- `key_ref_pairs`
- `temporal_constraint_refs`

表达，而不是写成自由 SQL。

### 5. window policy 是 binding 约束，而不是 process 本体

process object 可以声明自己依赖某种窗口语义，但真正把：

- first exposure 后 7d
- late arrival 2d
- incomplete window exclude

这些约束固定下来的，是 binding contract。

## 与其他对象的关系

### 与 entity 的关系

entity 声明：

- `entity_ref`
- `key_refs`
- `primary_time_ref`
- `descriptor_refs`

binding contract 负责把这些 refs 映射到：

- 哪个 source object
- 哪个字段路径
- 哪个 binding slot

### 与 process object 的关系

process object 声明：

- `population_subject_ref`
- `context_kind`
- `entity_ref`
- `provided_capabilities`

binding contract 负责把这些语义要求落到：

- assignment/exposure/event/fact 等 source bindings
- join relations
- window policies

### 与 metric 的关系

metric 声明：

- `observed_entity_ref`
- `sample_kind`
- `value_semantics`
- `required_process_contract`

binding contract 负责提供：

- `measure_input`
- `numerator_input`
- `denominator_input`
- `weight_input`
- 必要的 sample basis 与时间锚点

### 与 compiler / IR 的关系

compiler 读取 binding 的目的应是：

- 确认 required refs 是否有落地 binding
- 确认 join relations 与 window policies 是否完整
- 生成 normalized compiler inputs
- 产出 typed bindings 进入 IR

binding 不直接等于 IR，也不直接等于 engine plan。

## 示例

### 示例 1：`user` entity binding

```json
{
  "header": {
    "name": "binding.user",
    "binding_scope": "entity",
    "bound_object_ref": "user",
    "revision": 1,
    "binding_contract_version": "binding.v1"
  },
  "interface_contract": {
    "source_bindings": [
      {
        "binding_key": "user_base",
        "source": {
          "source_object": "warehouse.user_dim",
          "object_type": "table"
        },
        "binding_role": "entity_base",
        "grain_ref": "grain.user",
        "primary_entity_ref": "entity.user"
      }
    ],
    "field_bindings": [
      {
        "source_binding_key": "user_base",
        "semantic_slot": "subject_key",
        "semantic_ref": "key.user_id",
        "physical_field_path": "user_id",
        "nullability_policy": "reject"
      },
      {
        "source_binding_key": "user_base",
        "semantic_slot": "primary_time",
        "semantic_ref": "time.user_created_at",
        "physical_field_path": "created_at"
      },
      {
        "source_binding_key": "user_base",
        "semantic_slot": "descriptor",
        "semantic_ref": "dimension.country",
        "physical_field_path": "country"
      }
    ],
    "join_relations": [],
    "window_policies": []
  }
}
```

### 示例 2：`experiment_context` process binding

```json
{
  "header": {
    "name": "binding.process.checkout_redesign",
    "binding_scope": "process_object",
    "bound_object_ref": "process.experiment.checkout_redesign",
    "revision": 2,
    "binding_contract_version": "binding.v1"
  },
  "interface_contract": {
    "source_bindings": [
      {
        "binding_key": "assignment",
        "source": {
          "source_object": "warehouse.exp_user_assignments",
          "object_type": "table"
        },
        "binding_role": "assignment_log",
        "grain_ref": "grain.user",
        "primary_entity_ref": "entity.user"
      },
      {
        "binding_key": "exposure",
        "source": {
          "source_object": "warehouse.app_event_log",
          "object_type": "table"
        },
        "binding_role": "exposure_log",
        "grain_ref": "grain.event",
        "primary_entity_ref": "entity.user"
      }
    ],
    "field_bindings": [
      {
        "source_binding_key": "assignment",
        "semantic_slot": "subject_key",
        "semantic_ref": "key.user_id",
        "physical_field_path": "user_id",
        "nullability_policy": "reject"
      },
      {
        "source_binding_key": "assignment",
        "semantic_slot": "experiment_id",
        "semantic_ref": "process.experiment_id",
        "physical_field_path": "experiment_id"
      },
      {
        "source_binding_key": "assignment",
        "semantic_slot": "variant_id",
        "semantic_ref": "process.variant_id",
        "physical_field_path": "variant_id"
      },
      {
        "source_binding_key": "assignment",
        "semantic_slot": "anchor_time",
        "semantic_ref": "time.assignment_time",
        "physical_field_path": "assigned_at"
      },
      {
        "source_binding_key": "exposure",
        "semantic_slot": "subject_key",
        "semantic_ref": "key.user_id",
        "physical_field_path": "user_id"
      },
      {
        "source_binding_key": "exposure",
        "semantic_slot": "experiment_id",
        "semantic_ref": "process.experiment_id",
        "physical_field_path": "event_properties.experiment_id"
      },
      {
        "source_binding_key": "exposure",
        "semantic_slot": "variant_id",
        "semantic_ref": "process.variant_id",
        "physical_field_path": "event_properties.variant_id"
      },
      {
        "source_binding_key": "exposure",
        "semantic_slot": "event_time",
        "semantic_ref": "time.exposure_time",
        "physical_field_path": "event_time"
      }
    ],
    "join_relations": [
      {
        "relation_key": "assignment_to_exposure",
        "left_binding_key": "assignment",
        "right_binding_key": "exposure",
        "join_kind": "inner",
        "key_ref_pairs": [
          ["key.user_id", "key.user_id"],
          ["process.experiment_id", "process.experiment_id"],
          ["process.variant_id", "process.variant_id"]
        ],
        "cardinality": "one_to_many",
        "temporal_constraint_refs": [
          "rule.exposure_after_assignment"
        ]
      }
    ],
    "window_policies": [
      {
        "policy_key": "late_arrival",
        "policy_type": "late_arrival_policy",
        "anchor_ref": "time.exposure_time",
        "grace_period_ref": "window.2d"
      }
    ]
  }
}
```

### 示例 3：`conversion_rate` metric binding

```json
{
  "header": {
    "name": "binding.metric.conversion_rate",
    "binding_scope": "metric",
    "bound_object_ref": "metric.conversion_rate",
    "revision": 1,
    "binding_contract_version": "binding.v1"
  },
  "interface_contract": {
    "source_bindings": [
      {
        "binding_key": "conversion_fact",
        "source": {
          "source_object": "warehouse.order_fact",
          "object_type": "table"
        },
        "binding_role": "metric_fact",
        "grain_ref": "grain.order",
        "primary_entity_ref": "entity.user"
      }
    ],
    "field_bindings": [
      {
        "source_binding_key": "conversion_fact",
        "semantic_slot": "subject_key",
        "semantic_ref": "key.user_id",
        "physical_field_path": "user_id"
      },
      {
        "source_binding_key": "conversion_fact",
        "semantic_slot": "event_time",
        "semantic_ref": "time.conversion_time",
        "physical_field_path": "paid_at"
      },
      {
        "source_binding_key": "conversion_fact",
        "semantic_slot": "numerator_input",
        "semantic_ref": "metric_input.converted_order_count",
        "physical_field_path": "order_id"
      }
    ],
    "join_relations": [],
    "window_policies": [
      {
        "policy_key": "conversion_window",
        "policy_type": "attribution_window",
        "anchor_ref": "time.exposure_time",
        "window_ref": "window.7d"
      }
    ]
  }
}
```

## 设计上的直接收益

采用该 typed binding contract 后，会有几个直接收益：

1. **binding 成为一等治理对象**
   - 不再只是粗粒度 `semantic_mappings`
2. **entity / process / metric 都能真正落地**
   - 不再停留在抽象 schema
3. **compiler 可以做 binding-aware validation**
   - 缺字段、缺 slot、缺 join relation、缺 window policy 都能显式报错
4. **experiment 场景能够原生表达**
   - assignment / exposure / conversion 的连接关系可治理、可校验
5. **底层物理 schema 变化不会直接污染 public semantic contract**
   - 只要 semantic refs 和 binding slots 稳定，上层契约可以保持稳定

## 后续建议

typed binding contract 定稿后，建议按以下顺序推进：

1. 对齐 `entity schema contract`
   - 统一 `entity_ref`、`key_refs`、`primary_time_ref`
2. 对齐 `process object schema`
   - 明确各 subtype 需要哪些 binding slots 与 join relations
3. 对齐 `metric v2`
   - 明确 measure inputs、sample basis、window dependencies 的绑定需求
4. 对齐 `IR schema contract`
   - 让 compiler 输出 typed bindings，而不是回退到松散 mapping

一句话总结：

> typed binding contract 是 semantic objects 与物理 schema 之间的受治理桥梁：它负责声明“哪些语义角色如何稳定落地”，但不负责替代 compiler 去决定最终执行计划。
