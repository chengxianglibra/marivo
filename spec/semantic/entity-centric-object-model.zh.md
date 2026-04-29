# Marivo Entity-Centric Semantic Object Model 设计

本文定义 Marivo semantic layer 对象模型的一次收敛方向：以 `entity` 作为唯一 physical grounding 单元，其他 semantic objects 只通过 `entity.field` 或其他 semantic refs 间接触达物理数据。

本文是对象模型设计说明，不是当前实现说明，也不是 HTTP wire spec。本文不讨论 session / workspace / official 分层，不讨论 promotion / approval 流程，也不定义最终 DDL 或 API payload。

## 背景

Marivo 现有 semantic 设计已经把 `metric`、`process object`、`dimension`、`predicate`、`time`、`typed binding` 拆成多个职责清晰的对象。但如果多个 semantic objects 都可以直接或间接承载 physical binding，建模和实现会出现几个问题：

- 业务专家或 Agent 需要在多个对象上反复声明物理字段映射。
- 同一个物理字段可能在 metric、dimension、time、process 等对象中形成重复 binding 真相。
- Agent 建模流程不够自然，容易先创建 metric/dimension，再回头补物理映射。
- 物理表字段变更时，影响面难以通过单一引用图稳定推导。
- 编译器需要同时处理 object contract 和多处 physical grounding，复杂度偏高。

因此，本设计将 physical grounding 收敛到 `entity` 层，让其他对象专注表达自己的语义 contract。

## 目标

- 让 `entity` 成为唯一拥有 physical binding 的 semantic object。
- 让 `entity.field` 成为薄的字段 surface，只描述字段本身、基础类型、治理标签和物理映射。
- 让 `dimension`、`time`、`predicate`、`metric`、`process object` 自己声明字段在本对象中的使用角色。
- 支持 metric / process 跨多个 entity 引用 fields，而不要求 metric / process 自己绑定物理表或字段。
- 通过 `entity relationship` / `compatibility profile` 表达跨 entity 的 key、grain、time、cardinality 对齐关系。
- 通过统一 catalog metadata 的 `domain_ref` 支持按业务域搜索、list 和 Agent discovery。
- 让 compiler 在执行前完成 ref resolution、field type、grain、time、relationship、governance 的确定性校验。
- 降低 semantic layer 构建成本，同时保留复杂 metric / process 的表达能力。

## 非目标

- 不引入 session / workspace / official 对象使用范围。
- 不定义 semantic object promotion、审批或发布流程。
- 不重构权限体系；本文只说明对象模型为治理校验提供哪些结构化上下文。
- 不把 `entity.field` 设计成 SQL DSL。
- 不让 relationship/profile 变成任意 join graph 或通用规则引擎。
- 不要求本轮一次性修改所有现有 schema、API、MCP tool 和 skill 文档。
- 不复制 SQL 的全部表达力；复杂逻辑应进入 typed metric/process contract，或由上游 view/model 作为 entity binding 的物理来源。

## 核心原则

```text
Entity owns physical grounding.
Entity field describes what the field is and where it lives.
Semantic objects own how fields are used.
Relationship/profile owns cross-entity compatibility.
Compiler owns validation and lowering.
```

换句话说：

```text
字段只负责“是什么和在哪里”。
对象负责“怎么被使用”。
compiler 负责“能不能组合执行”。
```

## 总体模型

```text
Entity
  owns physical binding
  owns thin fields

Dimension / Time / Predicate
  reference entity.field
  add governance and semantic meaning

Metric
  owns measurement contract
  references entity.field, time, predicate, process, dimension

Process Object
  owns population / sequence / state contract
  references entity.field, time, predicate, dimension

Entity Relationship / Compatibility Profile
  owns cross-entity key, grain, time, cardinality compatibility

Catalog Metadata
  owns domain, owner, lifecycle, readiness, revision, aliases

Compiler
  resolves refs through entity binding
  validates compatibility
  lowers semantic plan to IR / engine plan
```

## Catalog Metadata 与业务域

业务域标识应作为统一 catalog metadata，而不是写入每个 semantic object 的核心 contract。

示例：

```json
{
  "object": {
    "metric_ref": "metric.gmv"
  },
  "catalog_metadata": {
    "domain_ref": "domain.commerce",
    "related_domain_refs": ["domain.shared"],
    "aliases": ["gmv", "gross merchandise value"]
  }
}
```

原因是业务域主要服务 catalog、owner、治理和发现，不是对象语义本体。组织结构或业务域归属调整不应迫使 metric、dimension、time、process 等对象的核心语义 contract 产生 revision。

建议所有顶层 semantic objects 都具备统一 metadata envelope：

```text
entity
dimension
time
predicate
metric
process
relationship
compatibility_profile
```

其中：

- `domain_ref` 建议作为 catalog metadata 必填字段，用于确定对象主归属业务域。
- `related_domain_refs` 可选，用于表达跨域共享或消费关系。
- `entity.field` 默认继承所属 entity 的 domain，不建议每个 field 强制声明 domain。
- 跨域共享对象可归属到 `domain.shared` 或组织定义的共享域。
- `domain_ref` 不应作为权限来源；权限仍由 governance policy、数据访问授权和底层执行引擎 ACL 判断。
- 不建议把 domain 编进 stable ref，例如不强制使用 `metric.commerce.gmv`；业务域调整不应污染 stable semantic ref。

### Domain Catalog

Marivo 应提供 domain catalog discovery 能力，方便 Agent 在搜索 semantic objects 前先缩小业务域范围。

最小能力：

```text
list domains
get domain detail
list semantic objects by domain_ref
search semantic objects within domain_ref
```

Domain 对象本身应保持轻量：

```text
domain_ref
display_name
description
status
aliases
```

字段语义：

| Field | 语义 |
| --- | --- |
| `domain_ref` | 业务域的稳定引用，例如 `domain.commerce`；用于 semantic object catalog metadata 中的主归属引用 |
| `display_name` | 面向用户展示的业务域名称 |
| `description` | 业务域覆盖范围说明，帮助 Agent 判断用户问题是否属于该域 |
| `status` | domain catalog entry 的可发现状态，建议最小取值为 `active`、`deprecated`；用于隐藏废弃域或提示迁移，不等同 semantic object lifecycle |
| `aliases` | 搜索别名和常用业务叫法，例如 `["ecommerce", "交易"]`；只服务 search/discovery，不作为稳定 identity |

这些字段属于 domain catalog metadata，不应被 compiler 当作 semantic compatibility 真相，也不应被 data-plane authorization 当作最终授权依据。

Agent 推荐流程：

```text
1. list domains
2. 根据用户问题选择候选 domain
3. 在 domain 内搜索 entity / metric / dimension / time / predicate / process
4. 如果结果不足，再扩展到 related domains 或 shared domain
```

这里的 list domain 是 catalog 发现接口，不改变本文的对象 contract 边界，也不引入 session / workspace / official 分层。

## Entity

`entity` 是唯一 physical grounding 单元，但它不是物理表或 view 的一对一别名。

允许：

- 一个 entity 只映射某张物理表 / view 的部分字段。
- 多个 entity 映射到同一张物理表 / view 的不同字段子集。
- 一个 entity 表达业务实体、事件实体、事实实体、快照实体或派生实体。

示例：

```text
entity.order
  binding:
    source_object: iceberg.dwd.order_wide
  fields:
    field.order_id -> column order_id
    field.user_id -> column mid
    field.order_status -> column status
    field.pay_amount -> column pay_amt
    field.pay_time -> column pay_time
```

`entity_kind` 可作为轻量分类：

```text
business_entity
event_entity
fact_entity
snapshot_entity
derived_entity
```

但 `entity_kind` 不应成为编译真相。它可以用于：

- Agent 建模建议。
- catalog 过滤和导航。
- 默认 readiness hint。
- 字段识别模板选择。

它不应单独决定：

- 这个 entity 能否参与某个 metric / process。
- 某个字段能否作为 numerator、dimension、time anchor 或 process step。
- 最终 SQL lowering 方式。
- 权限判断结果。

真正的编译判断来自 field 基础性质、对象 contract、relationship/profile、governance policy 和 compiler validation。

## Entity Field

`entity.field` 是薄 surface。它只描述字段本身、基础数据性质、治理标签和物理映射，不提前声明字段在 metric、dimension、time、predicate 或 process 中的业务角色。

推荐保留：

```text
field_ref
display_name
description
value_type
nullable
unit
enum_hint
sample_values / profile summary
sensitivity_tags
physical_column / physical expression locator
```

可选保留 discovery hints：

```text
usage_hints:
  likely_identifier
  likely_time
  likely_category
  likely_measure
```

`usage_hints` 只供 Agent 和 UI 推荐使用，不作为 compiler 真相。

`entity.field` 不应提前保存：

```text
field_kind = dimension / metric_input / time / process_step
semantic_role = numerator_input / grouping_axis / time_anchor
allowed_usages = metric_component / process_step / predicate_target
```

原因是同一个字段在不同对象中可能承担不同角色：

```text
entity.order.field.pay_amount
  在 metric.gmv 中是 value input
  在 predicate.high_value_order 中是 filter target
  在 dimension.order_value_bucket 中是 bucket source

entity.order.field.pay_time
  在 time.order_paid_at 中是 time source
  在 metric.gmv 中通过 time.order_paid_at 成为 primary time
  在 process.first_purchase_cohort 中成为 cohort anchor
```

字段用途属于消费对象，而不是字段本体。

## Dimension / Time / Predicate

`dimension`、`time`、`predicate` 不拥有 physical binding。它们引用 `entity.field`，并补充可治理的语义。

示例：

```text
dimension.order_status
  source_field_ref: entity.order.field.order_status
  value_domain: enumerated
  grouping_policy: ...

time.order_paid_at
  source_field_ref: entity.order.field.pay_time
  semantic_roles: [business_anchor, measurement]

predicate.successful_order
  atoms:
    - target_field_ref: entity.order.field.order_status
      op: in
      values: ["paid", "completed"]
```

对象职责：

- `dimension` 负责分析轴语义、值域、层级、分组治理。
- `time` 负责时间语义角色、calendar / alignment 相关语义锚点。
- `predicate` 负责可复用过滤语义、allowed usage 和 filter lineage。

它们不负责：

- 物理字段名。
- source table / view。
- join path。
- SQL expression。

## Metric

`metric` 保留 measurement contract，但不绑定物理字段。

Metric 负责声明：

- measurement identity。
- component role。
- aggregation。
- sample basis。
- observed entity / grain。
- primary time。
- additivity / comparability constraints。
- numerator / denominator / derived expression 等 family-specific contract。

Metric 中涉及字段时，只引用 `entity.field`、`time.*`、`predicate.*`、`process.*` 等 semantic refs。

示例：

```text
metric.gmv
  observed_entity_ref: entity.order
  primary_time_ref: time.order_paid_at
  components:
    - role: value
      input_field_ref: entity.order.field.pay_amount
      aggregation: sum
  default_predicate_refs:
    - predicate.successful_order
```

跨 entity ratio：

```text
metric.conversion_rate
  numerator:
    input_field_ref: entity.conversion_event.field.converted_users
    aggregation: sum
  denominator:
    input_field_ref: entity.exposure_event.field.exposed_users
    aggregation: sum
  expression: numerator / denominator
```

Metric 不拥有：

- physical table / view。
- physical column binding。
- join SQL。
- process window 实现。

跨 entity component 能否组合，由 relationship/profile 与 compiler 校验。

## Process Object

`process object` 保留过程契约，但不绑定物理字段。

Process 负责声明：

- cohort、funnel、session、experiment、path、lifecycle 等过程语义。
- population / sequence / state / window 规则。
- 下游稳定暴露的分析接口。

Process 中涉及字段时，只引用 `entity.field`、`time.*`、`predicate.*`、`dimension.*` 等 semantic refs。

示例：

```text
process.checkout_funnel
  subject_entity_ref: entity.user
  steps:
    - event_ref: entity.behavior_event.field.view_cart
    - event_ref: entity.behavior_event.field.submit_order
    - event_ref: entity.order_event.field.pay_success
  matching_window: 7d
```

Process 不拥有：

- source table / view。
- physical column。
- join SQL。
- engine-specific sequence matcher。

## Entity Relationship

为了支持跨 entity metric / process，需要一类非物理 relationship 对象。

Relationship 不绑定物理字段，只表达 entity 之间的语义组合关系。

示例：

```text
relationship.exposure_to_conversion
  left_entity: entity.exposure_event
  right_entity: entity.conversion_event
  key_alignment:
    left_field: entity.exposure_event.field.user_id
    right_field: entity.conversion_event.field.user_id
  time_alignment:
    left_time_ref: time.exposure_at
    right_time_ref: time.conversion_at
    rule: conversion within 7d after exposure
  cardinality: many_to_many
```

Relationship 可表达：

- key equality / subject alignment。
- time alignment。
- cardinality。
- grain compatibility。
- snapshot effective window alignment。

Relationship 不应表达：

- physical join SQL。
- optimizer hint。
- CTE shape。
- 任意 boolean expression DSL。

## Compatibility Profile

Compatibility profile 负责表达更复杂组合的编译前置条件。

示例：

```text
profile.conversion_rate
  required_relationships:
    - relationship.exposure_to_conversion
  grain_policy:
    numerator and denominator must align by subject.user and day
  time_policy:
    denominator scoped by exposure time
    numerator scoped by conversion window
```

Profile 可以由 compiler 自动推导一部分，也可以在复杂场景中显式声明。它不替代 metric/process contract，也不拥有 physical binding。

Profile 的 v1 范围应保持克制：

- required relationships。
- key / grain / time compatibility。
- additivity / aggregation compatibility。
- field profile requirements。
- governance preflight requirements。

不应支持：

- 任意 SQL。
- 任意 join graph。
- 任意规则引擎。

## Compiler Resolution Flow

一次 typed intent 执行时，compiler 应按确定流程解析：

```text
1. resolve metric / dimension / time / predicate / process refs
2. collect entity.field refs from resolved objects
3. resolve entity refs and entity binding revisions
4. resolve field refs to physical source columns
5. validate field value_type against object usage
6. validate relationship/profile for cross-entity composition
7. validate time / grain / additivity / governance constraints
8. lower to IR / engine plan
9. snapshot resolved refs + revisions
```

失败应返回 semantic blocker，而不是让用户面对 SQL error。

示例 blocker：

```text
missing_time_object
invalid_metric_input_type
missing_entity_relationship
incompatible_grain
ambiguous_field_ref
permission_denied
```

## 建模用例

### 单 Entity 指标

```text
metric.gmv
entity.order.field.pay_amount
time.order_paid_at
dimension.order_status
predicate.successful_order
```

验证点：

- metric 不需要 physical binding。
- dimension/time/predicate 只引用 entity field。
- compiler 能从 entity binding 解析到物理字段。

### 跨 Entity Ratio

```text
metric.conversion_rate
entity.exposure_event.field.exposed_users
entity.conversion_event.field.converted_users
relationship.exposure_to_conversion
```

验证点：

- metric 可以跨 entity 引用 component input。
- relationship 表达 key/time/grain 对齐。
- 缺 relationship 时 compiler 返回 semantic blocker。

### Funnel Process

```text
process.checkout_funnel
entity.behavior_event.field.view_cart
entity.behavior_event.field.submit_order
entity.order_event.field.pay_success
```

验证点：

- process 不需要 physical binding。
- process step 可以引用多个 entity fields。
- sequence/window 规则留在 process contract。

### Snapshot Alignment

```text
entity.user_snapshot
entity.behavior_event
relationship.event_to_user_snapshot
```

验证点：

- `entity_kind=snapshot_entity` 不直接驱动编译。
- relationship/profile 表达 event time 与 snapshot effective window 的对齐。

### 字段复用

```text
entity.order.field.pay_time
  -> time.order_paid_at
  -> process.first_purchase_cohort anchor
  -> metric.gmv primary time
```

验证点：

- field 不保存 role。
- role 在各消费对象中声明。
- 没有重复 physical binding。

## 与当前模型的差异

当前设计倾向于让 typed binding 作为独立 binding 层，服务 metric、process、entity、dimension 等对象的 physical grounding。本设计将 binding 收敛到 entity。

差异总结：

| 主题 | 当前模型 | 新模型 |
| --- | --- | --- |
| Physical grounding | typed binding 是独立对象，可服务多个 semantic object | 只有 entity binding 拥有 physical grounding |
| Field role | binding target 可表达字段服务哪个 contract target | field 本身不保存 role，role 由消费对象声明 |
| Dimension/time/predicate | 独立对象，但与 binding 存在落地关系 | 独立对象，只引用 entity.field |
| Metric/process | 保留 typed contract，可能依赖 binding 解析 | 保留 typed contract，但不拥有 physical binding |
| Cross-entity composition | binding imports / relation surfaces 承载较多职责 | relationship/profile 专门表达跨 entity 兼容性 |
| Agent authoring | 多对象 + binding authoring | entity -> fields -> semantic objects |

## 风险与缓解

### Entity 变成过宽 carrier

风险：entity 可能被塞入过多过程逻辑或计算逻辑。

缓解：

- entity field 保持薄 surface。
- metric/process contract 仍是一等对象。
- process 规则、metric formula、predicate expression 不写入 entity field。

### Relationship/profile 复杂度上升

风险：relationship/profile 可能演变成通用 SQL / join DSL。

缓解：

- v1 只支持 key alignment、time alignment、cardinality、grain compatibility。
- 不允许写 physical SQL。
- 复杂过程逻辑进入 process contract。

### 字段用途分散在多个对象中

风险：同一个 field 被多个对象以不同角色引用，使用面变多。

缓解：

- 这是刻意取舍。重复的是对象用途，不是 physical mapping。
- 通过 reverse dependency graph 查看 field 被哪些对象消费。
- field 改动通过引用图分析影响面。

### 复杂 SQL 表达力下降

风险：无法直接把任意 SQL 片段塞进 semantic object。

缓解：

- Marivo 不以复制 SQL 全表达力为目标。
- 常见复杂分析进入 metric/process typed contract。
- 极复杂清洗、拼宽、预计算逻辑可上移到 source view/model，再通过 entity binding 暴露。

## 后续影响范围

本设计若被采纳，后续需要更新以下文档和工具面：

```text
spec/semantic/entity-schema-contract.zh.md
spec/semantic/typed-binding-contract.zh.md
spec/semantic/dimension-schema-contract.zh.md
spec/semantic/time-schema-contract.zh.md
spec/semantic/predicate-schema-contract.zh.md
spec/semantic/metric-v2-schema.zh.md
spec/semantic/process-object-schema.zh.md
spec/semantic/compiler-spec.zh.md
spec/semantic/overview.md

docs/api/semantic.md
agent-guide.md

marivo-skill/marivo/SKILL.md
marivo-skill/marivo/references/*
marivo-mcp related tool descriptions / schema docs / inventory docs
```

其中：

- `marivo-mcp` 的工具说明应从“为多个 semantic object 分别创建 binding”调整为“先创建 entity + fields + entity binding，再创建引用 fields 的 semantic objects”。
- `marivo-skill` 应更新为 entity-first 建模流程，避免 Agent 建议给 metric、dimension、time、process 单独绑定物理字段。
- `agent-guide.md` 只保留 coding/testing 规范，不应塞入 Marivo 使用说明或 repo-local 细节。
- `docs/api/semantic.md` 需要在后续 wire spec 阶段更新，不应由本文直接冻结 endpoint 形状。

## 设计结论

本设计建议将 Marivo semantic layer 的 canonical object model 收敛为：

```text
entity-centric authoring
entity-only physical grounding
thin entity fields
object-owned semantic roles
relationship/profile-based composition
compiler-enforced compatibility
```

该模型能显著降低 semantic layer 构建成本，同时保留复杂 metric / process 的表达能力。关键约束是：不要让 entity field 过早承载 dimension/metric/process 的使用角色，也不要让 relationship/profile 演变成 SQL DSL。
