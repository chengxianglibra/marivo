# Semantic Layer Typed Binding Contract（草案）

本文定义 Marivo semantic layer 中 `typed binding contract` 的目标 schema。

本文是**语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/entity-schema-contract.zh.md`
- `docs/semantic/predicate-schema-contract.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/time-schema-contract.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`

本文重点回答：

- semantic object 如何稳定绑定到 carrier surfaces
- 哪些绑定信息属于 public semantic contract
- 哪些实现细节必须留在 compiler / adapter / execution 层
- `entity`、`process object`、`metric` 各自如何通过 binding contract 落到物理层

## Purpose

本文用于为 binding layer 提供一套更稳定、更受治理的 typed contract，使其能够：

- 把 `entity_ref`、`key_refs`、`primary_time_ref` 等语义引用映射到 carrier surfaces
- 把 `dimension_ref`、`stable_descriptors.dimension_ref`、`exported_dimension_refs` 等语义引用映射到 carrier surfaces
- 为 `entity`、`process object`、`metric` 提供可引用、可组合的 `binding_ref`
- 把 `process object`、`metric` 已声明的语义接口映射到 carrier bindings、carrier surfaces 与受治理消费策略
- 把 `metric` 依赖的 measure inputs、sample basis、scope basis 映射到底层事实来源
- 让 compiler 在进入 lowering 前完成 binding-aware validation

本文不定义：

- 最终数据库 DDL
- 最终 REST endpoint shape
- 最终 SQL 模板
- engine-specific rewrite 策略
- 具体 join 算法、CTE 形状、window frame 语法

## 背景

Binding 负责表达 semantic object 与 physical carrier 的关联，包括：

- 哪个 semantic target 消费哪个 carrier surface
- 不同 binding 之间通过哪些 relation 组合
- 哪些窗口策略、迟到数据策略、行过滤条件属于该对象的受治理绑定约束

**不需要独立的 asset 层（历史术语）：**

原设计引入独立的 `asset` contract 来表达”物理承载体”，但这带来问题：

- Asset 和 binding 职责重叠
- 需要两次查找（asset → binding）
- 对象无法直接看到其 carrier 信息

新设计将 carrier 信息直接合并到 binding 中：

- `carrier_kind`、`carrier_locator` 直接放在 `CarrierBinding` 结构中
- `field_surfaces`、`time_surfaces`、`relation_surfaces` 作为 binding 的子结构
- 消除了历史 `asset` 术语与 `binding` 职责之间的混淆

## 设计目标

typed binding contract 应同时满足：

- **语义稳定**：以 contract target + 受治理字段角色表达稳定语义，而不是暴露零散字段名
- **对象无关**：可以同时服务 entity、process object、metric
- **可治理**：支持版本、发布校验、兼容性检查
- **可组合**：binding 自身可被其他 binding 显式引用与导入
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

1. 这个 semantic object 主要依赖哪些 carriers / source objects
2. 这个 binding 自身的稳定引用是什么，以及它显式依赖哪些其他 bindings
3. 在这些 carriers 上，哪些 surfaces 落到哪些**契约目标路径（contract targets）**
4. 多个 carrier bindings 之间允许通过哪些 relation surfaces 连接，以及哪些消费策略属于稳定 binding 约束

它**不应**回答：

- 最终 SQL 怎么写
- join 顺序如何优化
- CTE 如何拆
- 哪个 adapter 具体选哪个 kernel
- 失败时如何 fallback

一句话总结：

> typed binding contract 应声明“这个语义对象依赖哪些受治理的物理绑定槽位”，而不是声明“最终 SQL 应长什么样”。

## 统一建模原则

### 1. binding 必须是可引用、可组合的一等对象

`entity` 已经要求 `identity_binding_ref`，因此 binding 自身不能只有 `name`，还必须有稳定 `binding_ref`。

同时，跨 binding 消费外部时间锚点、身份锚点或窗口契约时，不能依赖全局搜索或命名约定，必须通过显式 `imports` 声明依赖来源。

### 2. field binding 的核心是“绑定到哪个契约目标”

binding 不应只保存任意 column 名，也不应只靠一组宽泛 slot 枚举猜语义，而应明确：

- 这个字段服务哪个 contract target
- 这个字段承担哪类通用字段角色
- 它映射到哪个语义 ref

例如：

- `identity.key_refs[key.user_id]`
- `binding_target.population_subject[key.user_id]`
- `stable_descriptors[dimension.country]`
- `analysis_window.anchor_ref`
- `metric_input.converted_order_count`

物理字段名只是这些目标的实现映射。

若目标路径涉及 `primary_time_ref`、`anchor_time_ref`、`analysis_window.anchor_ref` 等时间槽位，则对应 `semantic_ref` 应解析到统一的 `time.*` contract，而不是在 binding 内再发明一套局部时间命名。即便 `anchor_time_ref` 与 `analysis_window.anchor_ref` 恰好指向同一个 `time.*`，binding 仍应把它们视为不同 contract target，因为它们分别服务 process 主锚点与窗口消费锚点。

### 3. public binding contract 只保留稳定语义，不暴露执行细节

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

### 4. binding 应区分 carrier binding 与 semantic field binding

建议把 binding 分为两层：

1. **carrier binding**
   - 这个语义对象主要绑定到哪些 carrier / source_object grounding
2. **semantic field binding**
   - 在这些 carriers 上，哪个 surface 承担哪个稳定语义角色

如果只保留“对象绑定表”，系统很快又会退回到松散 `mapping_json` 或裸 physical path。

### 5. source role 只保留结构角色，业务语义通过 ref 扩展

binding 需要表达 source 的职责，但核心枚举应保持结构化、对象无关，例如：

- `primary`
- `auxiliary`

其中：

- `primary`：当前 semantic object 的主承载 table / view
- `auxiliary`：为 join、过滤、窗口、补充指标输入等提供支撑的辅承载 table / view

像 `assignment_log`、`exposure_log`、`metric_fact` 这类场景语义，不应继续硬编码进核心枚举，而应通过 `semantic_role_ref`、`binding_key`、`join_relations` 等扩展字段表达。

### 6. join 关系应以受治理 relation 表达，而不是任意 SQL 片段

binding 需要表达 join，但表达方式应是：

- join subjects 是谁
- 使用哪些 semantic key refs
- 允许的基数关系是什么
- 是否存在时间顺序要求

而不是直接暴露完整 SQL `ON ... AND ...` 片段。

### 7. binding 只拥有窗口消费策略，不重新定义窗口本体

窗口本体仍属于 `process object` / `metric-process contract` 中的过程语义，例如：

- `analysis_window`
- `observation_window`
- attribution / cohort / retention 的窗口定义

binding 层只保留消费侧约束，例如：

- late arrival grace period = 2d
- incomplete window policy = exclude_open_subjects

它回答的是“既有窗口语义如何被物理消费”，而不是“窗口本身定义为什么”。

同理：

- `time.exposure_time`
- `time.conversion_time`
- `time.partition_time`
- `time.processing_time`

这些时间语义本身属于 `time-schema-contract`；binding 只负责把它们映射到底层字段、join 约束与 freshness / lateness 策略。

## binding 要回答什么

typed binding contract 主要回答：

- 这个对象绑定到哪些 carriers
- 这个 binding 的稳定引用是什么、依赖哪些其他 bindings
- 这些 carriers 各自扮演什么结构角色
- 哪些 fields 对应到哪些 contract targets / semantic refs
- 哪些 join relations 被允许
- 哪些 filter / freshness / lateness / incomplete-window 消费策略稳定生效

其中 filter 分层的稳定规则应为：

- carrier row filter 属于 binding consumption constraint
- metric business predicate 属于 metric identity
- request scope 属于请求级临时 population narrowing
- governance filter 属于更上层的强制策略

以上 filter 分层的 formal specification 与 effective scope 合成公式见 `predicate-schema-contract.zh.md` "Effective Scope 合成"。本文只引用其结论，不重复定义分层规则。

## binding 不要回答什么

typed binding contract 不回答：

- 查询 DAG 如何展开
- 哪一步先执行
- 哪个 join 用 hash / merge / broadcast
- 样本摘要如何物化
- engine-specific 时间截断怎么写

这些属于 compiler / lowering / execution。

同理，binding 不重新定义 filter AST，也不直接暴露 SQL predicate string；其职责是通过 `row_filter_refs` 与 surfaces 为上游 contract 提供可 lowering 的 grounding。

## 通用 Schema

### 公共头部

```python
from typing import Literal, NotRequired, TypedDict


class BindingHeader(TypedDict):
    binding_ref: str
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
    binding_scope: Literal["entity", "process_object", "metric"]
    bound_object_ref: str
    binding_contract_version: str
```

`BindingHeader` 只保留 public binding contract 所需的稳定字段。catalog 生命周期、版本序号、治理规则、搜索元数据等信息应单独保存，而不是进入 binding 主契约。

### 字段说明

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `binding_ref` | string | yes | binding 的稳定公共引用；必须使用 `binding.*` |
| `display_name` | string | no | 人类可读显示名 |
| `description` | string | no | binding 语义说明 |
| `binding_scope` | enum | yes | 绑定对象类型：entity / process_object / metric |
| `bound_object_ref` | string | yes | 被绑定的语义对象引用，如 `entity.*` / `process.*` / `metric.*` |
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
    source_object_ref: str
    object_type: NotRequired[
        Literal[
            "table",
            "view",
        ]
        | None
    ]
```

### BindingImport

```python
from typing import NotRequired, TypedDict


class BindingImport(TypedDict):
    import_key: str
    binding_ref: str
    required_ref_prefixes: NotRequired[list[str] | None]
```

字段含义：

- `import_key`：当前 binding 内部使用的导入别名
- `binding_ref`：被依赖的外部 binding；必须使用 `binding.*`
- `required_ref_prefixes`：当前 binding 依赖该 binding 提供的哪些 ref / target 前缀

### CarrierBinding

```python
from typing import Literal, NotRequired, TypedDict


class CarrierBinding(TypedDict):
    binding_key: str
    source_object_ref: NotRequired[str | None]
    # Carrier 信息直接声明在 binding 中
    carrier_kind: Literal[
        "table",
        "view",
    ]
    carrier_locator: str  # 内部定位符，不暴露在 public contract
    # Binding 角色
    binding_role: Literal[
        "primary",
        "auxiliary",
    ]
    semantic_role_ref: NotRequired[str | None]
    grain_ref: NotRequired[str | None]
    primary_entity_ref: NotRequired[str | None]
    row_filter_refs: NotRequired[list[str] | None]
    freshness_policy_ref: NotRequired[str | None]
    # Carrier surfaces（直接暴露，不再通过独立 asset ref 查找）
    field_surfaces: NotRequired[list["FieldSurfaceSpec"] | None]
    time_surfaces: NotRequired[list["TimeSurfaceSpec"] | None]
```

字段含义：

- `binding_key`：该 carrier binding 在当前 contract 中的局部唯一键
- `source_object_ref`：可选的底层 `source_object` 快照引用；若未显式提供，则 compiler 基于 `carrier_locator` 在已同步 `source_objects` 中解析
- `carrier_kind`：载体类型；当前仅支持 `table` / `view`
- `carrier_locator`：内部定位符（如 FQN），不暴露在 public contract
- `binding_role`：该 carrier 在当前对象中扮演的结构角色；当前仅支持 `primary` / `auxiliary`
- `semantic_role_ref`：对象专用语义角色引用，如 assignment / exposure / conversion fact
- `grain_ref`：该 carrier 产出或承载的稳定粒度引用；应使用 `grain.*`
- `primary_entity_ref`：该 carrier 主要围绕哪个实体组织；应使用 `entity.*`
- `row_filter_refs`：结构化的行级过滤语义引用
- `freshness_policy_ref`：时效治理规则引用
- `field_surfaces`：该 carrier 暴露的字段 surfaces
- `time_surfaces`：该 carrier 暴露的时间 surfaces

其中 `row_filter_refs` 的语义边界应保持严格：

- 它应引用受治理的 `predicate.*`
- 它表达 carrier consumption invariants，例如软删、测试数据排除、租户隔离
- 它不表达某个 metric 的 business predicates
- 它不应承载仅在 numerator / denominator 等 component 上成立的局部 measurement semantics
- 在 effective scope 合成中，`row_filter_refs` 属于 `shared_effective_scope`（与 governance 和 request scope 并列），不属于 component-level scope（formal specification 见 `predicate-schema-contract.zh.md` "Effective Scope 合成"）
- `row_filter_refs` 引用的 predicate 必须声明 `carrier_row_filter` usage（formal specification 见 `predicate-schema-contract.zh.md` "allowed_usage 分类"）

**合并独立 asset 层到 binding 的原因：**

- 消除 asset/binding 双层查找
- Carrier 信息在使用处直接可见
- 减少对象间的间接依赖
- 保留 `source_object` 作为底层 catalog 快照，而不是再引入第二套 public asset 对象

### FieldSurfaceSpec

```python
from typing import NotRequired, TypedDict


class FieldSurfaceSpec(TypedDict):
    surface_ref: str  # 如 "field.user_id"
    physical_name: str  # 实际字段名
    field_type: NotRequired[str | None]  # 字段类型
```

### TimeSurfaceSpec

```python
from typing import Literal, NotRequired, TypedDict


class TimeSurfaceSpec(TypedDict):
    surface_ref: str  # 如 "time_surface.event_time"
    physical_name: str  # 实际字段名
    time_granularity: NotRequired[Literal["second", "minute", "hour", "day"] | None]
```

### BindingTarget（类型化目标）

```python
from typing import Literal, NotRequired, TypedDict


class BindingTarget(TypedDict):
    target_kind: Literal[
        "identity_key",           # 原: identity.key_refs[...]
        "primary_time",           # 原: primary_time_ref
        "stable_descriptor",      # 原: stable_descriptors[...]
        "population_subject",     # 原: binding_target.population_subject[...]
        "analysis_window_anchor", # 原: analysis_window.anchor_ref
        "process_context",        # 原: binding_target.process_context[...]
        "metric_input",           # 原: numerator.measure_ref 等
    ]
    target_key: str  # 语义 ref，如 "key.user_id", "time.exposure_time"
    context_ref: NotRequired[str | None]  # 多维度目标的上下文
```

**为什么要类型化？**

原 `target_path` 使用字符串路径如 `"identity.key_refs[key.user_id]"`：
- 非正式的小语言，难以校验
- 解析规则隐式，容易出错
- 无法在 IDE 中提供补全

类型化的 `BindingTarget`：
- 结构化、可校验
- 明确每个 target kind 的语义
- 便于 compiler 做静态检查

### FieldBinding

```python
from typing import Literal, NotRequired, TypedDict


class FieldBinding(TypedDict):
    carrier_binding_key: str
    target: BindingTarget  # 类型化目标，替代 target_path
    semantic_ref: str
    surface_ref: str  # 对应 carrier 的 field_surface
    field_type_ref: NotRequired[str | None]
    nullability_policy: NotRequired[Literal["reject", "allow", "impute"] | None]
    repeated_value_policy: NotRequired[
        Literal["take_first", "take_last", "aggregate", "explode"] | None
    ]
```

字段说明：

- `target`：字段实际服务的**类型化契约目标**
- `semantic_ref`：该字段服务的语义引用，如 `key.user_id`、`time.session_started_at`
- `surface_ref`：该 target 绑定到哪个 carrier surface
- `field_type_ref`：受治理类型语义引用
- `nullability_policy`：空值治理策略
- `repeated_value_policy`：重复值治理策略

建议约束：

- binding 不直接绑定裸 physical path，而是绑定 `surface_ref`
- carrier 的 field_surfaces 提供可用字段列表

### TimeBindingSpec

`FieldBinding` 适合表达“单个物理字段服务哪个 contract target”，但不足以稳定表达：

- 单列 timestamp 时间
- 单列 date 时间
- `date + hour` 复合时间
- 明确的 date/hour 编码格式

因此 binding contract 需要单独的 `TimeBindingSpec`：

```python
from typing import Literal, NotRequired, TypedDict


class TimeBindingSpec(TypedDict):
    carrier_binding_key: str
    target: BindingTarget
    semantic_ref: str  # 必须是 time.*
    resolution_kind: Literal[
        "timestamp_column",
        "date_column",
        "date_hour_columns",
    ]
    timestamp_surface_ref: NotRequired[str | None]
    timestamp_format: NotRequired[str | None]
    date_surface_ref: NotRequired[str | None]
    date_format: NotRequired[str | None]
    hour_surface_ref: NotRequired[str | None]
    hour_format: NotRequired[str | None]
    timezone_strategy: NotRequired[str | None]
```

约束：

- `target.target_kind` 仅允许 `primary_time` 或 `analysis_window_anchor`
- `timestamp_column` 只能提供 `timestamp_surface_ref`
- `timestamp_column` 可额外提供 `timestamp_format`
  - `native`：物理列本身就是 timestamp-like
  - `iso8601_t_naive`：物理列是无时区字符串，形如 `YYYY-MM-DDTHH:MM:SS`
  - 自定义 strftime 风格格式串：例如 `%Y%m%d %H:%M:%S`
- `date_column` 只能提供 `date_surface_ref`
- `date_hour_columns` 必须同时提供 `date_surface_ref` 与 `hour_surface_ref`
- `*_surface_ref` 必须引用当前 carrier 已声明的 `field_surfaces`
- `semantic_ref` 必须是 `time.*`
- `timezone_strategy` 在 Phase 1 仅支持 `session_consistent_naive`

运行时约定：

- windowed query 的 analysis time 与 partition pruning 都从 published `time_bindings` 推导
- 旧的单列时间 `field_bindings` 仅作为兼容读取路径，不支持复合列或格式声明

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

### ConsumptionPolicySpec

```python
from typing import Literal, NotRequired, TypedDict


class ConsumptionPolicySpec(TypedDict):
    policy_key: str
    policy_type: Literal[
        "late_arrival_policy",
        "incomplete_window_policy",
    ]
    policy_target_path: str
    anchor_ref: NotRequired[str | None]
    grace_period_ref: NotRequired[str | None]
    behavior: NotRequired[
        Literal["exclude_open_subjects", "clip_to_window", "keep_partial"] | None
    ]
```

### BindingInterfaceContract

```python
from typing import TypedDict


class BindingInterfaceContract(TypedDict):
    imports: list[BindingImport]
    carrier_bindings: list[CarrierBinding]
    field_bindings: list[FieldBinding]
    time_bindings: list[TimeBindingSpec]
    join_relations: list[JoinRelation]
    consumption_policies: list[ConsumptionPolicySpec]
```

### TypedBindingObject

```python
from typing import TypedDict


class TypedBindingObject(TypedDict):
    header: BindingHeader
    interface_contract: BindingInterfaceContract
```

## 设计说明

### 1. `binding_ref` 与 `imports` 让 binding 成为可组合对象

同一套 binding schema 可以同时服务：

- entity
- process object
- metric

但每个 binding 都必须有稳定 `binding_ref`，并且在消费外部 binding 提供的 anchor / identity / window contract 时，通过 `imports` 显式声明依赖来源。

这使 `identity_binding_ref`、跨对象 anchor 引用与组合校验都不再依赖隐式全局约定。

第一阶段中，`imports` 还承担一项受限的 capability bridge 语义：

- 仅适用于 `metric` binding 显式 import 的 `entity` binding
- 仅桥接 imported entity binding 中 `target.target_kind = "stable_descriptor"` 且 `semantic_ref` 为 `dimension.*` 的 public contract target
- 该 bridge 只支持一跳，不递归合并 imported binding 的能力
- 该 bridge 不桥接 join relations、consumption policies、carrier internals，也不桥接任意 field-level physical details

这项 bridge 语义属于 compiler/runtime 对既有 binding contract 的受限解释，不引入新的
binding payload 字段，也不把 imported binding 的 carrier 实现细节暴露为 metric 自身的
public contract。

### 2. `target_path` 比宽泛 `semantic_slot` 更适合作为主绑定目标

同样是 `key.user_id`，它可能服务于：

- `identity.key_refs[key.user_id]`
- `binding_target.population_subject[key.user_id]`
- `hierarchy.parent_entity_ref` 对应的父键匹配

因此 binding 的主键不应是“这个字段像不像 key”，而应是“它到底绑定到哪个 contract target”。

为了避免 binding 泄漏 compiler 内部实现，本轮将 `target_path` 收敛为**只允许 public contract target**。它不应再引用 compiler-visible normalized target path。

### 3. `binding_scope` 决定 binding 服务的对象种类

三者关注重点不同：

- entity 更关注 identity / time / descriptors 的落地
- process object 更关注多 source、多 join、多窗口的约束
- metric 更关注 measure inputs、sample basis、numerator / denominator inputs

### 4. binding 的物理锚点是 carrier surfaces

binding 直接包含 carrier 信息：

- `carrier_kind`：载体类型（当前仅支持 `table` / `view`）
- `carrier_locator`：内部定位符
- `field_surfaces` / `time_surfaces`：暴露的字段和时间表面

不需要独立的 asset ref 或 asset lookup。更稳定的主锚点是：

- `surface_ref`：carrier 的字段表面
- `carrier_binding_key`：当前对象内部的 carrier grounding 键
- `source_object_ref` 或可解析的 `carrier_locator`：落到底层已同步 `source_objects`

而不是：

- 裸表名
- 裸列名
- adapter-specific expression
- compiler internal path

### 5. join relation 要求结构化，不接受自由 SQL

例如可以表达：

- `assignment.binding_target.population_subject[key.user_id]` 对 `exposure.binding_target.population_subject[key.user_id]`
- `assignment.binding_target.process_context[process.experiment_id]` 对 `exposure.binding_target.process_context[process.experiment_id]`
- `exposure_time >= assignment_time`

但这类信息应通过：

- `key_ref_pairs`
- `temporal_constraint_refs`

表达，而不是写成自由 SQL。

### 6. consumption policy 只描述物理消费策略，不重写 process / metric 语义

process object / metric-process contract 可以声明自己依赖某种窗口语义，但 binding 层不再重新定义：

- first exposure 后 7d
- signup 后 14d
- return within 30d

这类窗口本体。

binding 层只补充消费侧治理，例如：

- late arrival 2d
- incomplete window exclude
- 某个窗口在物理层使用哪个时间锚点消费

## 与其他对象的关系

### 与 dimension 的关系

dimension 声明：

- `dimension_ref`
- `value_domain`
- `hierarchy`
- `required_time_anchor_ref`
- `grouping`

binding contract 负责把这些 refs 与语义接口落到：

- 哪个 carrier 或 relation surface
- 哪个 carrier surface
- 哪个 contract target
- 哪些 as-of / join / derivation 约束

dimension 本体不直接暴露这些物理细节。

对 `time_derived` dimension 而言，binding 也不应为 dimension 本体额外创造一套独立时间列契约；它的职责是把 entity / process / metric 暴露的 `primary_time_ref`、`anchor_time_ref` 或窗口级 `anchor_ref` 映射到物理字段，使 compiler 能验证并满足该 dimension 的 `required_time_anchor_ref`。

### 与 entity 的关系

entity 声明：

- `entity_ref`
- `key_refs`
- `primary_time_ref`
- `stable_descriptors`

binding contract 负责把这些 refs 映射到：

- 哪个 carrier
- 哪个 carrier surface
- 哪个 contract target

其中 `identity.identity_binding_ref` 应直接指向某个 binding 的 `binding_ref`，而不是依赖名称猜测。

### 与 process object 的关系

process object 声明：

- `population_subject_ref`
- `context_kind`
- `entity_ref`
- 各类窗口 / anchor / context 本体语义

binding contract 负责把这些语义要求落到：

- assignment/exposure/event/fact 等语义化的 table/view bindings
- join relations
- consumption policies

若 process 同时暴露 `anchor_time_ref` 与窗口级 `anchor_ref`，binding 应能分别覆盖这些 target path，而不是假定所有时间槽位都折叠为一个物理时间字段。

### 与 metric 的关系

metric 声明：

- `observed_entity_ref`
- `sample_kind`
- `value_semantics`
- 顶层 measurement identity

binding contract 负责提供：

- `measure_input`
- `numerator_input`
- `denominator_input`
- `weight_input`
- 必要的 sample basis 与时间锚点映射

`metric` scope 的 public target vocabulary 仍保持现状，不因 imported dimension bridge 引入
新的 `dimension` target kind。若 metric 需要消费 imported entity 的稳定维度，应通过
`imports` 显式依赖对应 entity binding；可消费的 `dimension.*` 由 compiler/runtime 基于
受限 bridge 规则解析，而不是在 metric binding payload 中新增独立声明。

若 metric 依赖 attribution / experiment / retention 之类 process 窗口，窗口本体应继续来自 process object 或 metric-process contract，而不是在 metric binding 中重新声明一个新窗口。

### 与 compiler / IR 的关系

compiler 读取 binding 的目的应是：

- 确认 required refs 是否有落地 binding
- 确认外部依赖是否通过 imports 显式声明
- 确认 join relations 与 consumption policies 是否完整
- 生成 normalized compiler inputs
- 产出 typed bindings 进入 IR

binding 不直接等于 IR，也不直接等于 engine plan。

## 示例

以下示例按“public binding contract”理解：`field_role`、`revision`、catalog 名称等旧字段不再是主 schema 的必需部分。示例统一采用 `carrier_binding_key + surface_ref` 作为物理锚点；必要时可再补充 `source_object_ref` 以直接锚定到底层 catalog snapshot；最终 public target vocabulary 建议再单独拍板，不在本轮写死。

### 示例 1：`user` entity binding

以下示例使用类型化 `BindingTarget`，carrier 信息直接在 binding 中表达。

```json
{
  "header": {
    "binding_ref": "binding.user_identity",
    "binding_scope": "entity",
    "bound_object_ref": "entity.user",
    "binding_contract_version": "binding.v2"
  },
  "interface_contract": {
    "imports": [],
    "carrier_bindings": [
      {
        "binding_key": "user_base",
        "source_object_ref": "source_object.dim_user",
        "carrier_kind": "table",
        "carrier_locator": "warehouse.dim_user",
        "binding_role": "primary",
        "grain_ref": "grain.user",
        "primary_entity_ref": "entity.user",
        "row_filter_refs": ["predicate.not_soft_deleted"],
        "field_surfaces": [
          {"surface_ref": "field.user_id", "physical_name": "user_id"},
          {"surface_ref": "field.created_at", "physical_name": "created_at"},
          {"surface_ref": "field.country", "physical_name": "country"}
        ]
      }
    ],
    "field_bindings": [
      {
        "carrier_binding_key": "user_base",
        "target": {"target_kind": "identity_key", "target_key": "key.user_id"},
        "semantic_ref": "key.user_id",
        "surface_ref": "field.user_id",
        "nullability_policy": "reject"
      },
      {
        "carrier_binding_key": "user_base",
        "target": {"target_kind": "primary_time"},
        "semantic_ref": "time.user_created_at",
        "surface_ref": "field.created_at"
      },
      {
        "carrier_binding_key": "user_base",
        "target": {"target_kind": "stable_descriptor", "target_key": "dimension.country"},
        "semantic_ref": "dimension.country",
        "surface_ref": "field.country"
      }
    ],
    "join_relations": [],
    "consumption_policies": []
  }
}
```

### 示例 2：`experiment_context` process binding

```json
{
  "header": {
    "binding_ref": "binding.process.checkout_redesign",
    "binding_scope": "process_object",
    "bound_object_ref": "process.experiment.checkout_redesign",
    "binding_contract_version": "binding.v2"
  },
  "interface_contract": {
    "imports": [
      {
        "import_key": "user_identity",
        "binding_ref": "binding.user_identity",
        "required_ref_prefixes": ["identity_key"]
      }
    ],
    "carrier_bindings": [
      {
        "binding_key": "assignment",
        "source_object_ref": "source_object.exp_user_assignments",
        "carrier_kind": "table",
        "carrier_locator": "warehouse.exp_user_assignments",
        "binding_role": "primary",
        "semantic_role_ref": "process.assignment_basis",
        "grain_ref": "grain.user",
        "primary_entity_ref": "entity.user",
        "field_surfaces": [
          {"surface_ref": "field.user_id", "physical_name": "user_id"},
          {"surface_ref": "field.experiment_id", "physical_name": "experiment_id"},
          {"surface_ref": "field.variant_id", "physical_name": "variant_id"},
          {"surface_ref": "field.assigned_at", "physical_name": "assigned_at"}
        ]
      },
      {
        "binding_key": "exposure",
        "source_object_ref": "source_object.app_event_log",
        "carrier_kind": "table",
        "carrier_locator": "warehouse.app_event_log",
        "binding_role": "auxiliary",
        "semantic_role_ref": "process.exposure_basis",
        "grain_ref": "grain.event",
        "primary_entity_ref": "entity.user",
        "field_surfaces": [
          {"surface_ref": "field.user_id", "physical_name": "user_id"},
          {"surface_ref": "field.event_time", "physical_name": "event_time"}
        ]
      }
    ],
    "field_bindings": [
      {
        "carrier_binding_key": "assignment",
        "target": {"target_kind": "population_subject", "target_key": "key.user_id"},
        "semantic_ref": "key.user_id",
        "surface_ref": "field.user_id",
        "nullability_policy": "reject"
      },
      {
        "carrier_binding_key": "assignment",
        "target": {"target_kind": "process_context", "target_key": "process.experiment_id"},
        "semantic_ref": "process.experiment_id",
        "surface_ref": "field.experiment_id"
      },
      {
        "carrier_binding_key": "assignment",
        "target": {"target_kind": "process_context", "target_key": "process.variant_id"},
        "semantic_ref": "process.variant_id",
        "surface_ref": "field.variant_id"
      },
      {
        "carrier_binding_key": "exposure",
        "target": {"target_kind": "population_subject", "target_key": "key.user_id"},
        "semantic_ref": "key.user_id",
        "surface_ref": "field.user_id"
      },
      {
        "carrier_binding_key": "exposure",
        "target": {"target_kind": "analysis_window_anchor"},
        "semantic_ref": "time.exposure_time",
        "surface_ref": "field.event_time"
      }
    ],
    "join_relations": [
      {
        "relation_key": "assignment_to_exposure",
        "left_binding_key": "assignment",
        "right_binding_key": "exposure",
        "join_kind": "inner",
        "key_ref_pairs": [
          ["key.user_id", "key.user_id"]
        ],
        "cardinality": "one_to_many",
        "temporal_constraint_refs": ["rule.exposure_after_assignment"]
      }
    ],
    "consumption_policies": [
      {
        "policy_key": "late_arrival",
        "policy_type": "late_arrival_policy",
        "policy_target_path": "analysis_window",
        "anchor_ref": "time.exposure_time",
        "grace_period_ref": "window.2d"
      }
    ]
  }
}
```

### 示例 3：`conversion_rate` metric binding

该示例展示 metric binding 继续通过既有 `imports` 与 `metric_input` target 组合工作。若该
metric 还要消费 imported entity binding 暴露的 `stable_descriptor -> dimension.*`，做法是
继续通过 `imports` 显式依赖该 entity binding；imported dimension 的可用性属于
compiler/runtime bridge 能力，不要求在本 payload 中新增 `dimension` target 或额外字段。

```json
{
  "header": {
    "binding_ref": "binding.metric.conversion_rate",
    "binding_scope": "metric",
    "bound_object_ref": "metric.conversion_rate",
    "binding_contract_version": "binding.v2"
  },
  "interface_contract": {
    "imports": [
      {
        "import_key": "checkout_experiment",
        "binding_ref": "binding.process.checkout_redesign",
        "required_ref_prefixes": ["analysis_window_anchor", "time.exposure_time"]
      }
    ],
    "carrier_bindings": [
      {
        "binding_key": "conversion_fact",
        "source_object_ref": "source_object.order_fact",
        "carrier_kind": "table",
        "carrier_locator": "warehouse.order_fact",
        "binding_role": "primary",
        "semantic_role_ref": "metric.conversion_fact",
        "grain_ref": "grain.order",
        "primary_entity_ref": "entity.user",
        "field_surfaces": [
          {"surface_ref": "field.user_id", "physical_name": "user_id"},
          {"surface_ref": "field.paid_at", "physical_name": "paid_at"},
          {"surface_ref": "field.order_id", "physical_name": "order_id"}
        ]
      }
    ],
    "field_bindings": [
      {
        "carrier_binding_key": "conversion_fact",
        "target": {"target_kind": "population_subject", "target_key": "key.user_id"},
        "semantic_ref": "key.user_id",
        "surface_ref": "field.user_id"
      },
      {
        "carrier_binding_key": "conversion_fact",
        "target": {"target_kind": "primary_time"},
        "semantic_ref": "time.conversion_time",
        "surface_ref": "field.paid_at"
      },
      {
        "carrier_binding_key": "conversion_fact",
        "target": {"target_kind": "metric_input", "target_key": "numerator"},
        "semantic_ref": "metric_input.converted_order_count",
        "surface_ref": "field.order_id"
      }
    ],
    "join_relations": [],
    "consumption_policies": [
      {
        "policy_key": "exclude_open_subjects",
        "policy_type": "incomplete_window_policy",
        "policy_target_path": "checkout_experiment.analysis_window",
        "behavior": "exclude_open_subjects"
      }
    ]
  }
}
```

### 示例 4：分区时间绑定

```json
{
  "header": {
    "binding_ref": "binding.process.app_event_partitioning",
    "binding_scope": "process_object",
    "bound_object_ref": "process.event_ingestion.app_event_log",
    "binding_contract_version": "binding.v2"
  },
  "interface_contract": {
    "imports": [],
    "carrier_bindings": [
      {
        "binding_key": "event_log",
        "source_object_ref": "source_object.app_event_log",
        "carrier_kind": "table",
        "carrier_locator": "warehouse.app_event_log",
        "binding_role": "primary",
        "grain_ref": "grain.event",
        "primary_entity_ref": "entity.user",
        "field_surfaces": [
          {"surface_ref": "field.partition_time", "physical_name": "partition_ts"},
          {"surface_ref": "field.event_time", "physical_name": "event_time"}
        ],
        "time_surfaces": [
          {"surface_ref": "time_surface.partition", "physical_name": "partition_ts", "time_granularity": "hour"}
        ]
      }
    ],
    "field_bindings": [
      {
        "carrier_binding_key": "event_log",
        "target": {"target_kind": "primary_time", "target_key": "time.partition_time"},
        "semantic_ref": "time.partition_time",
        "surface_ref": "field.partition_time"
      },
      {
        "carrier_binding_key": "event_log",
        "target": {"target_kind": "analysis_window_anchor"},
        "semantic_ref": "time.exposure_time",
        "surface_ref": "field.event_time"
      }
    ],
    "join_relations": [],
    "consumption_policies": [
      {
        "policy_key": "partition_freshness",
        "policy_type": "late_arrival_policy",
        "policy_target_path": "primary_time",
        "anchor_ref": "time.partition_time",
        "grace_period_ref": "window.1h"
      }
    ]
  }
}
```

该示例表达的是：

- `time.partition_time` 绑定到 carrier 的 partition time surface
- binding 直接暴露 carrier 的 field/time surfaces
- 不再需要独立的 asset lookup；binding 可直接引用或解析到底层 `source_objects`

## 设计上的直接收益

采用该 typed binding contract 后，会有几个直接收益：

1. **binding 成为一等治理对象**
   - 不再只是粗粒度 `semantic_mappings`
2. **entity / process / metric 都能真正落地**
   - 不再停留在抽象 schema
3. **compiler 可以做 binding-aware validation**
   - 缺 target、缺 import、缺 join relation、缺消费策略都能显式报错
4. **experiment 场景能够原生表达**
   - assignment / exposure / conversion 的连接关系可治理、可校验
5. **底层物理 schema 变化不会直接污染 public semantic contract**
   - 只要 semantic refs、target paths 与 imports 稳定，上层契约可以保持稳定

## 后续建议

typed binding contract 定稿后，建议按以下顺序推进：

1. 对齐 `entity schema contract`
   - 统一 `entity_ref`、`key_refs`、`primary_time_ref`、`identity_binding_ref`
2. 对齐 `process object schema`
   - 明确各 subtype 需要哪些 contract targets、join relations 与消费策略
3. 对齐 `metric v2`
   - 明确 measure inputs、sample basis 与 process dependency 的绑定需求
4. 对齐 `IR schema contract`
   - 让 compiler 输出 typed bindings，而不是回退到松散 mapping

一句话总结：

> typed binding contract 是 semantic objects 与物理 schema 之间的受治理桥梁：它负责声明“哪些契约目标如何稳定落地、依赖哪些 bindings、如何被物理消费”，但不负责替代 compiler 去决定最终执行计划。
