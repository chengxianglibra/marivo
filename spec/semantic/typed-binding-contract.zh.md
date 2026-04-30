# Semantic Layer Typed Binding Contract（草案）

本文定义 Marivo semantic layer 中 `typed binding contract` 的目标 schema。

本文是**语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `spec/semantic/dimension-schema-contract.zh.md`
- `spec/semantic/entity-schema-contract.zh.md`
- `spec/semantic/predicate-schema-contract.zh.md`
- `spec/semantic/process-object-schema.zh.md`
- `spec/semantic/metric-v2-schema.zh.md`
- `spec/semantic/time-schema-contract.zh.md`
- `spec/semantic/ir-schema-contract.zh.md`

本文重点回答：

- entity 如何稳定绑定到 carrier surfaces
- 哪些绑定信息属于 public semantic contract
- 哪些实现细节必须留在 compiler / adapter / execution 层
- `entity` 如何通过 binding contract 落到物理层，以及 `process object`、`metric` 如何通过 entity fields 间接消费物理字段

## Purpose

本文用于为 binding layer 提供一套更稳定、更受治理的 typed contract，使其能够：

- 把 `entity_ref`、`key_refs`、`primary_time_ref` 等语义引用映射到 carrier surfaces
- 把 entity stable descriptors、primary time、identity keys 与 entity fields 映射到 carrier surfaces
- 为 `entity` 提供可引用、可组合的 `binding_ref`
- 让 `process object`、`metric` 已声明的语义接口通过 `entity.<entity>.field.<field>` 间接解析到 carrier surfaces
- 把 `metric` 依赖的 measure inputs、sample basis、scope basis 通过 entity fields 映射到底层事实来源
- 让 compiler 在进入 lowering 前完成 binding-aware validation

本文不定义：

- 最终数据库 DDL
- 最终 REST endpoint shape
- 最终 SQL 模板
- engine-specific rewrite 策略
- 具体 join 算法、CTE 形状、window frame 语法

## 背景

Binding 负责表达 entity 与 physical carrier 的关联，包括：

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
- **entity-only**：active authoring 只服务 entity；process object、metric 不再拥有对象级 physical binding
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

entity binding contract 的职责是回答四个问题：

1. 这个 entity 主要依赖哪些 carriers / source objects
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

> typed binding contract 应声明“这个 entity 的字段依赖哪些受治理的物理绑定槽位”，而不是声明“最终 SQL 应长什么样”。

## 统一建模原则

### 1. binding 必须是可引用、可组合的一等对象

entity grounding 需要被独立审计，因此 binding 自身不能只有 `name`，还必须有稳定 `binding_ref`。

同时，跨 binding 消费外部时间锚点、身份锚点或窗口契约时，不能依赖全局搜索或命名约定，必须通过显式 `imports` 声明依赖来源。

### 2. field binding 的核心是“绑定到哪个契约目标”

binding 不应只保存任意 column 名，也不应只靠一组宽泛 slot 枚举猜语义，而应明确：

- 这个字段服务哪个 contract target
- 这个字段承担哪类通用字段角色
- 它映射到哪个语义 ref

例如：

- `identity.key_refs[key.user_id]`
- `entity.fields[key.user_id]`
- `stable_descriptors[dimension.country]`
- `primary_time_ref`
- `entity.fields[pay_amount]`

物理字段名只是这些目标的实现映射。

若目标路径涉及 `primary_time_ref` 或 entity time field，则对应 `semantic_ref` 应解析到统一的
`time.*` contract，而不是在 binding 内再发明一套局部时间命名。process window anchor、
metric input、population subject、process context 等目标不属于 active entity binding
target；历史 metadata 中若出现，只能按 legacy/read-only 语义读取。

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
   - 这个 entity 主要绑定到哪些 carrier / source_object grounding
2. **semantic field binding**
   - 在这些 carriers 上，哪个 surface 承担哪个稳定语义角色

如果只保留“对象绑定表”，系统很快又会退回到松散 `mapping_json` 或裸 physical path。

### 5. source role 只保留结构角色，业务语义通过 ref 扩展

binding 需要表达 source 的职责，但核心枚举应保持结构化、对象无关，例如：

- `primary`
- `auxiliary`

其中：

- `primary`：当前 entity 的主承载 table / view
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

- carrier row filter 属于 entity binding consumption constraint
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

Entity-centric cutover 后，typed binding 的新建、更新、校验和发布只允许
`binding_scope="entity"`。`process_object` / `metric` scope 只用于识别历史
metadata，不再作为 active physical grounding authoring path。

### 字段说明

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `binding_ref` | string | yes | binding 的稳定公共引用；必须使用 `binding.*` |
| `display_name` | string | no | 人类可读显示名 |
| `description` | string | no | binding 语义说明 |
| `binding_scope` | enum | yes | 新建 authoring 仅允许 `entity`；`process_object` / `metric` 为历史只读 scope |
| `bound_object_ref` | string | yes | 被绑定的语义对象引用；active authoring 必须是 `entity.*`，`process.*` / `metric.*` 仅用于历史 metadata |
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
        "entity_field",           # entity.<entity>.field.<field>
    ]
    target_key: str  # 语义 ref，如 "key.user_id", "time.user_created_at", "field.pay_amount"
    field_ref: NotRequired[str | None]  # 完整 entity field ref，如 entity.order.field.pay_amount
```

Active `BindingTarget` 只允许 entity binding target，即 entity identity、entity primary time、
entity stable descriptor 与 entity field 的 grounding。`target_kind` 不再包含
`population_subject`、`analysis_window_anchor`、`process_context`、`metric_input`。
这些 target kind 若出现在历史 metadata 中，只能用于 legacy/read-only 诊断，不允许新建、
更新、校验或发布。

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

- active authoring 中，`target.target_kind` 仅允许 `primary_time` 或 `entity_field`
- `analysis_window_anchor` 是 legacy/read-only target kind，不允许新建
- `timestamp_column` 只能提供 `timestamp_surface_ref`
- `timestamp_column` 可额外提供 `timestamp_format`
  - `native`：物理列本身就是 timestamp-like
  - `iso8601_t_naive`：物理列是无时区字符串，形如 `YYYY-MM-DDTHH:MM:SS`
  - 自定义 strftime 风格格式串：例如 `%Y%m%d %H:%M:%S`
- `date_column` 只能提供 `date_surface_ref`
- `date_hour_columns` 必须同时提供 `date_surface_ref` 与 `hour_surface_ref`
- `*_surface_ref` 必须引用当前 carrier 已声明的 `time_surfaces`，ref 前缀必须是
  `time_surface.*`
- `semantic_ref` 必须是 `time.*`
- `timezone_strategy` 在 Phase 1 仅支持 `session_consistent_naive`

### Active authoring target kind 矩阵

| binding_scope | allowed target_kind |
| --- | --- |
| `entity` | `identity_key`, `primary_time`, `stable_descriptor`, `entity_field` |

`metric` / `process_object` scope 只用于历史 metadata 读取和诊断，不再是 active
authoring scope。新建、更新、校验、发布 public typed binding 时，非 entity scope
应被拒绝。

### Legacy imported target coverage

以下能力属于 legacy metric/process binding 读取语义，不再作为 public authoring 指南：

- legacy target kind 包括 `population_subject`、`analysis_window_anchor`、
  `process_context`、`metric_input`。
- legacy `imports.required_ref_prefixes` 曾参与 metric binding 的 required target coverage
  propagation。
- legacy `metric_input.*` 曾用于 metric binding slot coverage。
- legacy `analysis_window.anchor_ref` 曾用于 process/window binding target coverage。
- legacy `binding_target.population_subject[...]` / `binding_target.process_context[...]`
  曾用于 process binding target coverage。
- entity-centric authoring 中，metric/process 需要字段时应在自身 contract 中引用
  `entity.field`，再经 entity binding 解析物理字段。

Binding detail/create readiness capabilities 暴露 `required_targets`、`covered_targets`、
`missing_required_targets`、`imported_covered_targets` 与 `covers_required_targets`。
这些字段若包含上述 legacy target kind，只能表示历史 metadata 的读取或诊断结果，不表示
public authoring 仍允许写入这些 target。

运行时约定：

- windowed query 的 analysis time 与 partition pruning 都从 published entity `time_bindings` 推导
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

当前 active authoring 中，binding schema 只服务 entity physical grounding。每个 active
binding 都必须有稳定 `binding_ref`，并且通过 `bound_object_ref` 指向被落地的 entity。

`imports`、metric bridge、process bridge 等语义只属于 legacy metadata 读取和诊断，不再是
新建 metric/process grounding 的入口。

### 2. `target_path` 比宽泛 `semantic_slot` 更适合作为主绑定目标

同样是 `key.user_id`，它可能服务于：

- `identity.key_refs[key.user_id]`
- `hierarchy.parent_entity_ref` 对应的父键匹配

因此 binding 的主键不应是“这个字段像不像 key”，而应是“它到底绑定到哪个 contract target”。
Active entity binding 的 contract target 只覆盖 entity identity、entity primary time、
entity stable descriptor 与 entity field。population subject、process context、
analysis window anchor、metric input 等历史 target path 不再是 public authoring target。

为了避免 binding 泄漏 compiler 内部实现，本轮将 `target_path` 收敛为**只允许 public contract target**。它不应再引用 compiler-visible normalized target path。

### 3. `binding_scope` 的目标态

目标态中，只有 entity binding 负责 physical grounding：

- entity binding 负责 identity / time / descriptors 及 `entity.field` 的物理落地。
- process object 不再提交 carrier binding；它通过自身 contract 引用 `entity.field`、time、predicate、dimension 等 semantic refs。
- metric 不再提交 carrier binding；measurement input、sample basis、numerator / denominator 等角色由 metric contract 声明，涉及字段时引用 `entity.field`。

### 4. binding 的物理锚点是 entity carrier surfaces

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

### 5. legacy join relation 要求结构化，不接受自由 SQL

Join relation 是 legacy multi-carrier binding 的结构化表达；entity-centric target state
会由 entity relationship / compatibility profile 表达跨 entity 组合能力。保留本节是为了
说明历史 metadata 的读取语义，不表示 public authoring 仍允许 process/metric carrier
binding。

例如可以表达：

- `assignment.binding_target.population_subject[key.user_id]` 对 `exposure.binding_target.population_subject[key.user_id]`
- `assignment.binding_target.process_context[process.experiment_id]` 对 `exposure.binding_target.process_context[process.experiment_id]`
- `exposure_time >= assignment_time`

这些示例只说明 legacy/read-only metadata 的解释方式，不是 active public authoring 示例。
新建 binding 不得提交 `population_subject` 或 `process_context` target。

但这类信息应通过：

- `key_ref_pairs`
- `temporal_constraint_refs`

表达，而不是写成自由 SQL。

### 6. legacy consumption policy 只描述物理消费策略，不重写 process / metric 语义

Consumption policy 在 legacy binding contract 中存在；entity-centric authoring 不应通过
metric/process binding 重新声明窗口或消费策略。

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

其中，entity 是唯一 active physical grounding owner。多个 entity 可以绑定同一个物理
table/view 的不同字段子集。

### 与 dimension / time / predicate 的关系

dimension、time、predicate 不再拥有自己的 physical binding。它们应引用
`entity.field`，并在各自对象 contract 中声明字段的语义角色、时间角色、过滤用途或治理含义。

### 与 metric / process object 的关系

metric 与 process object 不再提交 typed binding carrier payload：

- metric 自己声明 measurement semantics、component、aggregation、sample basis 等语义。
- process object 自己声明 population、context、sequence、window、state 等语义。
- 涉及具体字段时，metric/process 应引用 `entity.field` 或其他 semantic refs。
- compiler/readiness 后续通过 entity binding、relationship、compatibility profile 完成确定性解析。

### 与 legacy metric/process binding 的关系

历史 metadata 中可能存在 `binding_scope=metric` 或 `binding_scope=process_object`：

- 服务可以读取这类记录用于诊断或迁移分析。
- public authoring 不允许新建、更新、校验或发布这类记录。
- `/semantic/bindings/{id_or_ref}/revisions/derive` 这类 legacy metric binding revision path
  已禁用。
- 不能把 legacy metric/process binding 示例作为新 Agent 建模指南。

### 与 compiler / IR 的关系

compiler 读取 active binding 的目的应是：

- 从 entity binding 解析 `entity.field` 的物理来源。
- 确认 entity field locator、type、relationship/profile、time/grain/governance 兼容性。
- 生成 normalized compiler inputs。
- 不把 metric/process binding 当作新的 physical grounding 来源。

binding 不直接等于 IR，也不直接等于 engine plan。

## 示例

以下示例只展示 active public authoring path：entity typed binding。`field_role`、
`revision`、catalog 名称等旧字段不再是主 schema 的必需部分。

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

不再提供 `binding_scope=process_object` 或 `binding_scope=metric` 的 public 示例；这些
payload 在当前 authoring API 中会被拒绝。

## 设计上的直接收益

采用该 typed binding contract 后，会有几个直接收益：

1. **binding 成为一等治理对象**
   - 不再只是粗粒度 `semantic_mappings`
2. **entity 成为唯一 physical grounding owner**
   - metric / process 通过 `entity.field` 间接触达物理数据
3. **compiler 可以做 binding-aware validation**
   - 缺 entity binding、缺 field locator、缺 relationship/profile 都能显式报错
4. **跨对象组合能力从 binding 挪到 relationship/profile**
   - assignment / exposure / conversion 的连接关系由 semantic relationship/profile 治理
5. **底层物理 schema 变化不会直接污染 public semantic contract**
   - 只要 entity fields 与 semantic refs 稳定，上层契约可以保持稳定

## 后续建议

typed binding contract 定稿后，建议按以下顺序推进：

1. 对齐 `entity schema contract`
   - 统一 `entity_ref`、`fields`、`binding`、`physical locator`
2. 对齐 `process object schema`
   - 明确各 subtype 如何引用 `entity.field`、time、predicate、dimension
3. 对齐 `metric v2`
   - 明确 measure inputs、sample basis 与 process dependency 如何引用 `entity.field`
4. 对齐 `IR schema contract`
   - 让 compiler 从 entity binding resolution 输出 normalized physical plan

一句话总结：

> typed binding contract 在 entity-centric 模型中只承担 entity 与 physical carrier 之间的受治理桥梁：它负责声明 entity 字段如何稳定落地，但不替代 metric/process contract、relationship/profile 或 compiler。
