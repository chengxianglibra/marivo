# Semantic Layer Asset Schema Contract（草案）

本文定义 Factum semantic layer 中 `asset` 的目标 schema contract。

本文是**语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/entity-schema-contract.zh.md`
- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/time-schema-contract.zh.md`
- `docs/semantic/typed-binding-contract.zh.md`
- `docs/semantic/compiler-spec.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`

本文重点回答：

- semantic layer 是否需要独立表达“物理承载体是什么”
- 表 / 视图 / 物化视图 / stream / snapshot / external dataset 应如何进入统一 contract
- `asset` 与 `binding`、semantic objects、compiler、IR 的职责边界如何划分
- 如何在不把 IR 退化为 SQL AST 的前提下，稳定标识真实物理承载体

## Purpose

本文用于为 Factum 提供一套**physical carrier contract**，使其能够：

- 稳定标识真实物理承载体，而不是继续只靠裸 FQN、表名或 adapter 私有 locator
- 为 typed binding 提供可引用的 `asset_ref` 与 `asset_surface_ref`
- 让 compiler 在 lowering 前显式校验 asset kind、surface existence、time support、relation availability
- 让 IR 保留物理承载锚点，而不直接把 physical names、SQL 片段、adapter 决策塞进主 plan

本文不定义：

- 最终数据库 DDL
- 最终 REST endpoint shape
- engine-specific capability matrix 的实现细节
- SQL / native query lowering 模板
- source sync、catalog crawling 或 metadata ingestion 的实现细节

## 背景

现有 semantic 文档已经把职责大致拆成：

- object contract：回答“语义对象是什么”
- binding contract：回答“语义对象如何落到物理层”
- compiler / IR：回答“如何把它们组合成可执行语义计划”

但其中仍缺少一个独立 contract，专门回答：

- 真实物理承载体是什么
- 它具备哪些稳定 surface
- 它属于哪类 carrier
- 它以何种刷新 / 可变性 / 时间承载能力被消费

如果没有这层 contract，`binding` 往往会同时承担两类事实：

1. 物理承载体自身的身份与能力
2. semantic target 到物理 surface 的映射规则

这会让 “carrier truth” 与 “binding truth” 混在一起，不利于语义层分层。

因此，Factum 需要把 `asset` 从 typed binding 中分离出来，形成独立 contract。

## 设计目标

新的 asset contract 应同时满足：

- **carrier-first**：表达“这个物理承载体是什么”
- **binding-friendly**：为 binding 提供稳定 surface，而不是 forcing binding 回退到裸字段
- **compiler-friendly**：支持 normalization、validation、lowerability precheck
- **IR-safe**：让 IR 能引用 asset，但不暴露 engine-specific query shape
- **catalog-separable**：把 asset 主 contract 与 catalog / governance / engine metadata 分层

## 非目标

本文明确不追求：

- 让 asset 直接表达 semantic object 的业务语义
- 让 asset 直接承担 semantic target mapping
- 把 asset 做成 adapter DSL
- 在 asset 主 contract 中写 join 优化、物化策略选择或 fallback 决策
- 让外部请求直接引用物理表名而绕过 semantic layer

## 核心设计结论

`asset` 的公共 contract 应回答四个问题：

1. 这个物理承载体在 semantic layer 中的稳定标识是什么
2. 它属于哪类 carrier，以及自身有哪些稳定承载能力
3. 它稳定暴露哪些 field / time / relation surfaces
4. 它与 binding / compiler / IR 如何配合，而不泄漏 engine-specific 实现

它**不应**回答：

- 哪个 metric / entity / process 在消费它
- 哪个 semantic ref 映射到它的哪个 surface
- 最终 SQL 如何写
- join 顺序、materialization strategy、adapter fallback 如何选择

一句话总结：

> asset 应声明“物理承载体是什么、暴露哪些可绑定 surface”，而 binding 声明“语义槽位如何绑定到这些 surface”。

## 统一 ref taxonomy

### Asset 自身 taxonomy

建议引入以下稳定前缀：

- `asset.*`：物理承载体
- `asset-surface.*`：asset 对外暴露的可绑定 surface
- `asset-relation.*`：asset 对外暴露的 relation surface

命名规则统一为：

- **TypedDict 字段名**使用 `snake_case`
- **ref 值前缀**使用 taxonomy 约定的短横线形式，如 `asset-surface.*`

这意味着：

- semantic objects 不直接引用裸表名
- binding 不直接绑定裸物理路径，而优先绑定 `asset_ref + asset_surface_ref`
- compiler / IR 不直接把 physical name 当作主语义锚点

### 外部 taxonomy 依赖

asset contract 允许引用外部 taxonomy，但这些 taxonomy **不由 asset contract 自身定义**。当前至少依赖：

- `platform.*`：平台 / catalog / source system
- `namespace.*`：schema / dataset / topic group 等逻辑命名空间
- `type.*`：物理字段类型
- `timezone.*`：时区引用

约束：

- 上述外部 ref 必须能被 compiler 在 normalization 阶段解析
- 未解析的外部 ref 应视为 contract 错误，而不是静默回退
- asset contract 只引用这些 taxonomy，不在此文中重复定义其完整 schema

## 统一建模原则

### 1. asset 是 physical carrier contract，不是 semantic contract

`asset` 负责“物理上承载什么”，不负责“业务上代表什么”。

因此 asset 不应直接拥有：

- `entity_ref`
- `metric_ref`
- `dimension_ref`
- `process_ref`

它最多只表达自身 carrier identity、carrier capabilities、carrier surfaces。

### 2. asset 必须独立于 binding 存在

如果 asset 只是 binding 里的内嵌子结构，binding 仍然会同时承担：

- carrier identity
- carrier surfaces
- semantic mapping

这会导致 binding 继续过胖。

更稳定的方式是：

- `asset` 负责 carrier truth
- `binding` 负责 semantic mapping truth

### 3. `asset_surface_ref` 是 binding 的唯一字段级物理锚点

在目标态中，binding 不再优先绑定裸 `physical_field_path`，而是绑定：

- `asset_ref`
- `asset_surface_ref`

必要时 asset surface 自身可再映射到底层 physical path，但这属于 asset contract 的 carrier-facing 部分，不属于 binding 的主职责。

### 4. asset contract 只暴露稳定 carrier 语义，不暴露 adapter 决策

以下内容不应进入 asset 主 contract：

- target engine
- optimizer hint
- execution kernel
- SQL fragment
- fallback policy
- rewrite strategy

这些属于 lowering / engine plan。

### 5. asset 可以表达时间承载能力，但不表达业务时间语义

asset 可以声明：

- 哪些 surface 是 timestamp-like / partition-like
- 哪些 relation surface 可以被消费
- 哪些 carrier refresh / mutability 语义稳定存在

但 asset 不直接定义：

- `time.user_created_at`
- `time.assignment_time`
- `time.exposure_time`

这些依然是 semantic time contract；binding 再把它们映射到 asset time surfaces。

## asset 要回答什么

新的 asset contract 主要回答：

- 它的稳定标识是什么（`asset_ref`）
- 它是什么 carrier kind
- 它的 identity / locator 如何被稳定表达
- 它有哪些 field surfaces / time surfaces / relation surfaces
- 它的 mutability、refresh mode、materialization mode 是什么

## asset 不要回答什么

新的 asset contract 不回答：

- 这个 asset 对哪个 entity 生效
- 哪个 dimension 绑定到哪个字段
- 哪个 metric 使用哪个字段作为 numerator / denominator
- 哪个 process 通过哪条关系消费它
- 最终执行计划怎样拼接

## 通用 Schema

### 公共头部

```python
from typing import Literal, NotRequired, TypedDict


class AssetHeader(TypedDict):
    asset_ref: str
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
    asset_kind: Literal[
        "table",
        "view",
        "materialized_view",
        "stream",
        "snapshot",
        "external_dataset",
    ]
    asset_contract_version: str
```

`AssetHeader` 只保留 carrier identity 所需的稳定字段。catalog 生命周期、revision、搜索别名、owner、ACL、governance tags、adapter runtime stats 不应进入 asset 主 contract。

### AssetIdentitySpec

```python
from typing import Literal, NotRequired, TypedDict


class AssetIdentitySpec(TypedDict):
    locator_kind: Literal[
        "catalog_object",
        "stream_handle",
        "snapshot_handle",
        "external_handle",
    ]
    platform_ref: NotRequired[str]
    namespace_ref: NotRequired[str]
    native_locator: str
    lifecycle_mode: Literal["managed", "external", "derived_materialization"]
```

字段含义：

- `locator_kind`：该 asset 在底层系统中的身份类型
- `platform_ref`：所属平台 / catalog / source system 的稳定引用；应使用 `platform.*`
- `namespace_ref`：逻辑命名空间引用；应使用 `namespace.*`
- `native_locator`：底层 carrier 的稳定 locator，不等于最终执行 SQL
- `lifecycle_mode`：carrier 是受系统管理、外部只读，还是派生物化

建议约束：

- `catalog_object` 的 `native_locator` 应是稳定 FQN，如 `analytics.events.user_events_daily`
- `stream_handle` 的 `native_locator` 应是稳定 stream/topic 标识
- `external_handle` 的 `native_locator` 应是稳定外部资源 locator
- `view` / `materialized_view` 通常应使用 `derived_materialization`
- `external_dataset` 不应声明为 `managed`

### AssetCarrierCapabilitySpec

```python
from typing import Literal, TypedDict


class AssetCarrierCapabilitySpec(TypedDict):
    mutability: Literal[
        "immutable_snapshot",
        "append_only",
        "replaceable_snapshot",
        "fully_mutable",
    ]
    refresh_mode: Literal["batch", "incremental", "streaming", "on_demand"]
    materialization_mode: Literal["physical", "logical", "derived"]
```

这里表达的是**carrier 物理事实**，不是 binding 的消费策略。

### AssetFieldSurfaceSpec

```python
from typing import Literal, NotRequired, TypedDict


class AssetFieldSurfaceSpec(TypedDict):
    asset_surface_ref: str
    surface_kind: Literal[
        "scalar_field",
        "struct_field",
        "timestamp_field",
        "date_field",
        "partition_field",
    ]
    physical_path: str
    physical_type_ref: NotRequired[str]
    carrier_nullable: bool
```

字段含义：

- `asset_surface_ref`：该 field surface 的稳定公开引用；应使用 `asset-surface.*`
- `surface_kind`：该 surface 的物理结构种类
- `physical_path`：底层字段路径
- `physical_type_ref`：底层类型引用；应使用 `type.*`
- `carrier_nullable`：底层 carrier 是否允许该 surface 为空

### AssetTimeSurfaceSpec

```python
from typing import Literal, NotRequired, TypedDict


class AssetTimeSurfaceSpec(TypedDict):
    asset_surface_ref: str
    time_carrier_role: Literal[
        "event_time",
        "valid_time",
        "partition_time",
        "processing_time",
    ]
    backing_field_surface_refs: list[str]
    physical_granularity: NotRequired[
        Literal["hour", "day", "week", "month"] | None
    ]
    timezone_ref: NotRequired[str]
```

设计约束：

- time surface 仍使用 `asset_surface_ref`，因为它同样是可被 binding 消费的公开 surface
- `backing_field_surface_refs` 必须只引用**同一 asset** 的 `field_surfaces`
- `time_carrier_role` 只表达物理承载角色，不直接表达业务时间语义
- `physical_granularity` 是 carrier 事实，不是 semantic 解释
- `timezone_ref` 应使用 `timezone.*`

### AssetRelationSurfaceSpec

```python
from typing import Literal, NotRequired, TypedDict


class AssetRelationSurfaceSpec(TypedDict):
    asset_relation_ref: str
    relation_kind: Literal[
        "lookup",
        "bridge",
        "parent_child",
        "snapshot_lineage",
        "change_feed",
    ]
    target_asset_ref: str
    local_field_surface_refs: list[str]
    target_field_surface_refs: list[str]
    cardinality: Literal[
        "one_to_one",
        "many_to_one",
        "one_to_many",
        "many_to_many",
    ]
    temporal_order_owner: NotRequired[Literal["local", "target"]]
    temporal_order_surface_ref: NotRequired[str]
```

设计约束：

- `asset_relation_ref` 应使用 `asset-relation.*`
- `local_field_surface_refs` 必须来自当前 asset 的 `field_surfaces`
- `target_field_surface_refs` 必须来自 `target_asset_ref` 对应 asset 的 `field_surfaces`
- `temporal_order_surface_ref` 若存在，必须属于 `temporal_order_owner` 指向的一侧
- relation surface 只声明“哪些 carrier 之间稳定可对齐”，不替代 binding 中的 `JoinRelation`

### AssetInterfaceContract

```python
from typing import TypedDict


class AssetInterfaceContract(TypedDict):
    identity: AssetIdentitySpec
    carrier_capabilities: AssetCarrierCapabilitySpec
    field_surfaces: list[AssetFieldSurfaceSpec]
    time_surfaces: list[AssetTimeSurfaceSpec]
    relation_surfaces: list[AssetRelationSurfaceSpec]
```

### AssetObject

```python
from typing import TypedDict


class AssetObject(TypedDict):
    header: AssetHeader
    interface_contract: AssetInterfaceContract
```

## 设计说明

### 1. `asset_ref` 是物理承载体的稳定锚点

`asset_ref` 不是表名别名，也不是 adapter 私有 object id。它的职责是让：

- binding 稳定引用 carrier
- compiler 稳定生成 asset snapshots
- IR 稳定表达 carrier wiring

### 2. `native_locator` 是 carrier identity，不是 lowering payload

`native_locator` 允许 asset 指向真实物理承载体，但它不等于：

- SQL FROM 子句
- connector 运行时参数
- adapter 私有执行计划

它只是 public asset contract 中的 carrier locator。

### 3. `asset_surface_ref` 是公开 surface id，不是任意路径别名

`asset_surface_ref` 的作用是给 binding、compiler、IR 提供稳定的 surface 锚点，而不是把 `physical_path` 简单重命名一次。

约束：

- 同一 `asset` 内，`field_surfaces` 与 `time_surfaces` 的 `asset_surface_ref` 必须全局唯一
- `asset_surface_ref` 必须稳定，不应随底层列重命名策略或 adapter 细节抖动
- compiler 应构建 `asset_ref + asset_surface_ref -> surface spec` 的索引，供 binding 校验和 IR wiring 使用

### 4. time surface 属于 asset，但时间语义属于 `time.*`

例如一个 asset 可以暴露：

- `asset-surface.user_events.event_ts`
- `asset-surface.user_events.log_date`
- `asset-surface.user_events.partition_time`

但 `time.user_created_at`、`time.event_occurred_at` 之类语义 ref 仍属于时间 contract；binding 再把这些 `time.*` 映射到上述 time surfaces。

换言之：

- asset time surface 只回答“这个 carrier 暴露了哪种时间承载能力”
- binding 才回答“这个业务时间语义绑定到哪个 carrier surface”

### 5. relation surface 只表达 carrier-to-carrier 的稳定可连接性

asset relation surface 只声明：

- 哪两个 asset 之间存在稳定关系
- 用哪些 field surfaces 对齐
- 基数大致如何
- 是否存在额外时间顺序约束

它不直接声明：

- 哪个 semantic object 需要这条关系
- 哪个 process / metric 应该消费这条关系
- 具体 join_kind 是 `inner` 还是 `left`

这些属于 binding 与 compiler。

## 编译期校验规则

compiler 在 normalization / validation 阶段至少应完成以下检查。

### 1. ref 解析与存在性校验

- `asset_ref` 必须唯一且可解析
- `platform_ref`、`namespace_ref`、`physical_type_ref`、`timezone_ref` 必须可解析到对应外部 taxonomy
- `target_asset_ref` 必须指向已声明的 asset

### 2. surface 唯一性与归属校验

- 同一 asset 内所有 `asset_surface_ref` 必须唯一
- 同一 asset 内所有 `asset_relation_ref` 必须唯一
- `backing_field_surface_refs` 只能引用当前 asset 的 field surfaces
- `local_field_surface_refs` 只能引用当前 asset 的 field surfaces
- `target_field_surface_refs` 只能引用 target asset 的 field surfaces

### 3. relation 可行性校验

- `local_field_surface_refs` 与 `target_field_surface_refs` 不应为空
- 两侧 surface 数量必须一致，以便形成稳定对齐对
- `temporal_order_surface_ref` 若存在，必须与 `temporal_order_owner` 一致
- relation graph 不应形成未受控的循环依赖；若允许环，必须由 compiler 显式标记并处理

### 4. asset kind 与 capability 兼容性校验

建议至少遵守以下组合约束：

- `table`：`mutability` 可为 `append_only` / `fully_mutable` / `replaceable_snapshot`
- `view`：`materialization_mode` 应为 `logical`
- `materialized_view`：`materialization_mode` 应为 `derived` 或 `physical`
- `snapshot`：`mutability` 应为 `immutable_snapshot` 或 `replaceable_snapshot`
- `stream`：`mutability` 应为 `append_only`，`refresh_mode` 应为 `streaming`
- `external_dataset`：`lifecycle_mode` 不应为 `managed`

### 5. 与 binding 的兼容性校验

- binding 中引用的 `asset_surface_ref` 必须来自对应 `asset_ref`
- binding 的 `nullability_policy` 不应违反 `carrier_nullable`
- binding 的时间绑定若指向 time surface，应确保该 surface 来自 `time_surfaces`
- binding 中的 `JoinRelation` 仍以语义键和治理规则为主；asset relation surface 只提供“哪些 physical pair 稳定可连”的约束背景

## 与其他对象的关系

### 与 semantic objects 的关系

entity / dimension / metric / process object 都不应直接引用 `asset.*`。它们只表达业务与分析语义。

### 与 typed binding 的关系

binding 应成为 asset 的直接消费者：

- `binding` 引用 `asset_ref`
- `binding` 把 semantic target path 映射到 `asset_surface_ref`
- `binding` 可以消费 asset 暴露的 time surfaces
- `binding` 中的 `JoinRelation` 仍使用 `key_ref_pairs`、时间约束和兼容性规则表达对象级连接意图

因此：

- `asset` 负责声明 carrier surfaces 与可连接性边界
- `binding` 负责声明 semantic grounding 与对象级连接规则

### 与 compiler 的关系

compiler 在组合期应读取：

- normalized object snapshots
- normalized binding snapshots
- normalized asset snapshots

并显式构建：

- `asset_ref -> asset object`
- `asset_ref + asset_surface_ref -> surface spec`
- `asset_relation_ref -> relation spec`

然后校验：

- asset 是否存在
- asset kind 是否兼容
- 所需 surface 是否存在
- time surface / relation surface 是否满足 binding 与 intent 需求

### 与 IR 的关系

IR 应保留：

- `asset_ref`
- `asset_surface_ref`
- `binding_ref`
- 节点对 asset surface 的 typed wiring

IR 不应直接保留：

- physical table names 作为主语义
- SQL join clause
- engine-specific materialization details

## 示例

### 示例：`asset.user_events_daily`

```json
{
  "header": {
    "asset_ref": "asset.user_events_daily",
    "display_name": "User Events Daily",
    "description": "按日分区的用户事件事实表",
    "asset_kind": "table",
    "asset_contract_version": "asset.v1"
  },
  "interface_contract": {
    "identity": {
      "locator_kind": "catalog_object",
      "platform_ref": "platform.analytics_lakehouse",
      "namespace_ref": "namespace.events",
      "native_locator": "analytics.events.user_events_daily",
      "lifecycle_mode": "managed"
    },
    "carrier_capabilities": {
      "mutability": "append_only",
      "refresh_mode": "incremental",
      "materialization_mode": "physical"
    },
    "field_surfaces": [
      {
        "asset_surface_ref": "asset-surface.user_events_daily.user_id",
        "surface_kind": "scalar_field",
        "physical_path": "user_id",
        "physical_type_ref": "type.string",
        "carrier_nullable": false
      },
      {
        "asset_surface_ref": "asset-surface.user_events_daily.event_ts",
        "surface_kind": "timestamp_field",
        "physical_path": "event_ts",
        "physical_type_ref": "type.timestamp",
        "carrier_nullable": false
      },
      {
        "asset_surface_ref": "asset-surface.user_events_daily.log_date",
        "surface_kind": "partition_field",
        "physical_path": "log_date",
        "physical_type_ref": "type.date",
        "carrier_nullable": false
      }
    ],
    "time_surfaces": [
      {
        "asset_surface_ref": "asset-surface.user_events_daily.partition_time",
        "time_carrier_role": "partition_time",
        "backing_field_surface_refs": [
          "asset-surface.user_events_daily.log_date"
        ],
        "physical_granularity": "day",
        "timezone_ref": "timezone.utc"
      }
    ],
    "relation_surfaces": [
      {
        "asset_relation_ref": "asset-relation.user_events_daily.user_profile",
        "relation_kind": "lookup",
        "target_asset_ref": "asset.user_profile_snapshot",
        "local_field_surface_refs": [
          "asset-surface.user_events_daily.user_id"
        ],
        "target_field_surface_refs": [
          "asset-surface.user_profile_snapshot.user_id"
        ],
        "cardinality": "many_to_one"
      }
    ]
  }
}
```

## 总结

一言以蔽之：

> object contract 负责“语义上是什么”，asset contract 负责“物理承载体是什么、稳定暴露哪些可绑定接口”，binding contract 负责“语义如何落到这些接口”，compiler 负责“这些层如何被安全组合”，IR 负责“保存组合后的 typed plan wiring”。
