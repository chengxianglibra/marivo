# Source / Execution Engine / Mapping Contract

状态：draft design。

本文档定义 Factum 数据平面中 `source`、`execution engine` 与 `mapping` 的目标 schema 契约。

本文是服务/运行时设计说明，不是当前 HTTP API 契约，也不是对现有 `/sources`、`/engines`、`/bindings` 路由的逐字段解释。它描述的是下一阶段应收敛到的领域模型，用于支撑：

- metadata authority 与 execution authority 分离
- source sync 仍以 metadata authority 为准
- execution routing 仍以 execution engine 为准
- authority namespace 到 execution namespace 的显式、受治理投影

## 实现状态说明

本文中的类型集合描述的是目标 schema 设计需要覆盖的对象种类，不等同于当前实现已经支持这些类型。

当前实现层的支持矩阵仍然收敛为：

- `source`
  - `trino`
  - `duckdb`
- `execution engine`
  - `trino`
  - `duckdb`

因此，本文中出现的：

- `mysql`
- `hive_metastore`
- `iceberg_rest`
- `spark`

都应理解为 schema contract 预留覆盖面，而不是当前运行时、adapter、sync、routing 或 HTTP API 已经具备的实现能力。

## 背景

当前实现中：

- `source` 同时承担“catalog metadata authority”与“可用于执行的 catalog endpoint”两种含义
- `execution engine` 表达执行端能力与连接
- `source_engine_binding.namespace` 仅能做轻量 catalog/schema 前缀修正

这在简单场景下可用，但不足以稳定表达以下事实：

- 元数据权威系统与执行系统不是同一个 endpoint
- source object identity 应锚定在 metadata authority，而不是执行端可见名字
- execution engine 对 authority object 的可见 namespace 需要显式 contract，而不是运行时猜测

因此，Factum 需要把数据平面收敛为三个一等对象：

1. `source`
2. `execution engine`
3. `mapping`

其中 `mapping` 不再只是 namespace 补丁，而是 authority-to-execution projection contract。

## 目标

- 保留 `source` 与 `execution engine` 的分离建模
- 明确 source object identity 锚定在 metadata authority
- 仅支持两层 mapping：
  - source-level default mapping
  - authority catalog-level mapping
- 支持以下 source 类型：
  - MySQL
  - Hive Metastore
  - Iceberg REST catalog
  - Trino
  - DuckDB
- 支持以下 execution engine 类型：
  - Trino
  - Spark
  - DuckDB
- 让 routing、sync、semantic grounding 都能依赖同一份 mapping contract

## 非目标

- 定义 object-level table override
- 定义 schema-level 或 table-level rewrite
- 让 typed binding 直接绑定 execution-side locator
- 在本轮引入新的 federation planner contract
- 规定当前 HTTP API 必须立刻按本文变更

## 核心原则

### 1. Metadata identity 归属于 source

`source` 是 metadata authority。Factum sync 得到的 object identity、层级、schema 与后续 semantic grounding，默认都以 source namespace 为准。

### 2. Execution namespace 归属于 execution engine

`execution engine` 表达“哪些执行端可以运行查询，以及它们支持哪些能力”。执行侧 locator 是 projection，不是 object identity 源。

### 3. Mapping 是 authority-to-execution contract

`mapping` 负责回答：

- source 上的 object 在某个 execution engine 看起来属于哪个 catalog
- 若 execution engine 需要默认 schema，默认值是什么
- 哪些 authority catalog 可以被该 execution engine 消费

### 4. 没有 mapping，就没有稳定 routing

当 source 与 execution engine 分离时，Factum 不应通过连接参数、默认 catalog 或短名称推断 execution-side locator。若没有可用 mapping，应作为 readiness / routing blocker 明确失败。

### 5. Typed binding 继续锚定 source object

typed semantic binding 的 carrier grounding 应继续依赖 sync 后的 `source object`。编译与执行阶段再通过 `mapping` 将 authority locator 投影到 execution locator。

## 支持对象

### Source 类型

在 schema 设计上，本轮 source 类型收敛为：

- `mysql`
- `hive_metastore`
- `iceberg_rest`
- `trino`
- `duckdb`

它们的共同职责是：

- 提供 metadata authority 入口
- 支持 schema / table / column 级 sync
- 生成稳定的 authority-side object identity

注意：

- `trino` 作为 source 时，表示“Trino 暴露出来的 catalog 视图”是 metadata authority
- `duckdb` 作为 source 时，表示 DuckDB 文件自身既是 metadata authority，也是本地 catalog authority
- `mysql` / `hive_metastore` / `iceberg_rest` 不自动推断执行能力

### Execution engine 类型

在 schema 设计上，本轮 execution engine 类型收敛为：

- `trino`
- `spark`
- `duckdb`

它们的共同职责是：

- 提供 query execution 入口
- 提供 capability profile
- 作为 routing 选择目标
- 接收 authority-to-execution projection 后的 locator

## 统一 locator 语义

为避免 source 与 execution 对象名混淆，本文统一使用以下概念：

- `authority catalog`
  source 视角下的 catalog 名称；对不具备 catalog 层的 source，可使用 source-defined synthetic catalog
- `schema`
  authority 或 execution 视角下的 schema / database namespace
- `table`
  authority 或 execution 视角下的 relation name

最小 authority locator 形态：

```json
{
  "catalog": "catalog_name",
  "schema": "schema_name",
  "table": "table_name"
}
```

最小 execution locator 形态：

```json
{
  "catalog": "catalog_name",
  "schema": "schema_name",
  "table": "table_name"
}
```

本文不要求两者名字相同；它们通过 `mapping` 建立关系。

## 1. Source Contract

`source` 是 metadata authority object。它必须能回答“从哪里 sync 元数据，以及 sync 出来的 object identity 属于哪个 authority namespace”。

建议 public schema：

```json
{
  "source_id": "src_...",
  "source_type": "mysql | hive_metastore | iceberg_rest | trino | duckdb",
  "display_name": "string",
  "status": "active | inactive | deprecated",
  "authority": {
    "authority_kind": "catalog_service",
    "catalog_system": "mysql | hive_metastore | iceberg_rest | trino | duckdb",
    "connection": {},
    "default_catalog": "string | null"
  },
  "sync": {
    "mode": "by_select | full | none",
    "selection_policy": "explicit_only | allow_all"
  },
  "capabilities": {
    "supports_live_browse": true,
    "supports_column_sync": true,
    "supports_table_properties": true
  },
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

### Source 字段语义

- `source_id`
  Factum 内部稳定标识。
- `source_type`
  authority adapter 类型。
- `authority.catalog_system`
  authority 的底层系统类型；通常与 `source_type` 一致，但保留单独字段以避免 future overloading。
- `authority.connection`
  metadata authority 连接参数。
- `authority.default_catalog`
  authority browse/sync 的默认 catalog。仅在 authority 自身具备 catalog 概念时出现。
- `sync.mode`
  metadata sync 策略，不涉及 execution。
- `capabilities`
  metadata-side 能力，不代表 execution 能力。

### Source 不回答什么

`source` 不应回答：

- 查询最终在哪个 engine 上执行
- authority object 在 execution 端如何命名
- engine 的 SQL dialect 或 policy support

这些都属于 `execution engine` 或 `mapping`。

## 2. Execution Engine Contract

`execution engine` 是 query execution object。它必须能回答“查询可以在哪里执行，以及该执行端具备哪些 runtime 能力”。

建议 public schema：

```json
{
  "engine_id": "eng_...",
  "engine_type": "trino | spark | duckdb",
  "display_name": "string",
  "status": "active | inactive | deprecated",
  "connection": {},
  "default_namespace": {
    "catalog": "string | null",
    "schema": "string | null"
  },
  "capability_profile": {
    "supported_sql_features": [],
    "supported_step_types": [],
    "materialization_support": "temporary_table | catalog_table | none | unknown",
    "policy_support": [],
    "performance_class": "embedded | distributed | general_purpose",
    "federation_support": "none | connector | engine_native"
  },
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

### Execution engine 字段语义

- `engine_type`
  执行端种类。
- `connection`
  execution 连接参数。
- `default_namespace`
  execution engine 自身的默认 catalog/schema；只能作为 mapping resolution 的 fallback，不能替代显式 mapping。
- `capability_profile`
  routing、planning、policy fit 的 authority source。

### Execution engine 不回答什么

`execution engine` 不应回答：

- sync 从哪里来
- source object 的 identity 是什么
- 哪个 authority catalog 应映射到哪个 execution catalog

这些都不属于 execution 本体。

## 3. Mapping Contract

`mapping` 是 `source -> execution engine` 的 authority-to-execution projection contract。

一个 mapping object 代表：

- 某个 source 的 authority namespace
- 在某个 execution engine 上如何被消费

本轮只支持两层 mapping：

1. source-level default mapping
2. authority catalog-level mapping

建议 public schema：

```json
{
  "mapping_id": "map_...",
  "source_id": "src_...",
  "engine_id": "eng_...",
  "status": "active | inactive | deprecated",
  "priority": 0,
  "default_mapping": {
    "execution_catalog": "string | null",
    "default_schema": "string | null"
  },
  "catalog_mappings": [
    {
      "authority_catalog": "string",
      "execution_catalog": "string",
      "default_schema": "string | null"
    }
  ],
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

### Mapping 字段语义

- `source_id`
  metadata authority 侧对象集合。
- `engine_id`
  execution 侧消费目标。
- `priority`
  多 execution engine 候选时的 routing tie-break 输入之一。
- `default_mapping`
  source 级默认投影规则。
- `catalog_mappings`
  authority catalog 级显式覆盖规则。

### Mapping resolution 顺序

对于 authority locator：

```json
{
  "catalog": "authority_catalog",
  "schema": "analytics",
  "table": "orders"
}
```

在给定 `mapping(source_id, engine_id)` 下，解析顺序固定为：

1. 若 `catalog_mappings` 中存在匹配 `authority_catalog` 的项，使用该项：
   - `execution_catalog = matched.execution_catalog`
   - `schema = authority.schema`
   - 若 authority-side schema 缺失且 `matched.default_schema` 存在，则使用 `matched.default_schema`
2. 否则若 `default_mapping.execution_catalog` 存在，使用 default mapping：
   - `execution_catalog = default_mapping.execution_catalog`
   - `schema = authority.schema`
   - 若 authority-side schema 缺失且 `default_mapping.default_schema` 存在，则使用 `default_mapping.default_schema`
3. 否则解析失败，报 `mapping_missing`

本轮不支持：

- 按 schema 做 rewrite
- 按 table 做 remap
- 一个 authority catalog 对应多个 execution catalog

### Mapping 不变量

- 同一 `source_id + engine_id` 下，`authority_catalog` 不能重复定义
- `catalog_mappings` 命中时必须覆盖 `default_mapping`
- mapping 解析只改 execution namespace，不改 authority object identity
- 若 source 类型天然不具备 catalog 层，也必须在 sync identity 中产生稳定 `authority_catalog`
- `default_schema` 只允许作为 schema 缺失时的 fallback；不能重写已存在 authority schema

## 两层 mapping 的设计原因

本轮只做两层，是因为目标是先消除当前 `namespace` 补丁模型的不足，而不是一步引入过强的 object remap 系统。

两层 mapping 可以覆盖的主流场景：

- Hive Metastore catalog `hms` 在 Trino 中映射为 `hive`
- Iceberg REST catalog `lakehouse` 在 Spark 中映射为 `prod_iceberg`
- MySQL source 的 authority catalog 统一投影到 Trino catalog `mysql_prod`
- DuckDB source 在 DuckDB engine 中直接 pass-through

它不能覆盖的场景：

- authority 中同 catalog 的部分表映射到 execution 侧不同 catalog
- execution 端只暴露 authority 对象的部分 schema
- authority object 被 engine-side view / MV 替代

这些后续若有需要，再引入更细粒度 contract。

## Source Object Identity Contract

sync 后的 `source object` 必须保留 authority-side locator。

建议最小 object identity 片段：

```json
{
  "object_id": "obj_...",
  "source_id": "src_...",
  "object_type": "catalog | schema | table | column",
  "authority_locator": {
    "catalog": "hms",
    "schema": "analytics",
    "table": "orders"
  }
}
```

执行阶段不应把 object identity 直接改写成 execution-side locator。若需要暴露 execution 解析结果，应作为 derived metadata，例如：

```json
{
  "resolved_execution_locator": {
    "engine_id": "eng_...",
    "catalog": "hive",
    "schema": "analytics",
    "table": "orders"
  }
}
```

这样可以保持：

- metadata authority 稳定
- semantic binding grounding 稳定
- execution projection 可替换

## Routing Contract

routing 在选择 engine 时，应至少同时考虑：

- mapping 是否可解析全部 authority locator
- engine capability profile 是否满足任务
- mapping priority

因此，`source -> engine` 可路由，不再等价于“存在一条 binding 记录”，而应等价于：

1. 存在 active mapping
2. mapping 能为涉及的 authority catalogs 提供完整 execution projection
3. engine capability profile 允许该类执行

当任一条件不满足时，应明确失败，而不是退回到短名称猜测。

## Readiness / Validation Contract

对于每条 mapping，最少应验证以下事项：

- `source_id` 与 `engine_id` 存在
- source 类型与 engine 类型的组合允许建立 mapping
- `catalog_mappings[*].authority_catalog` 在 source authority 中合法
- `catalog_mappings[*].execution_catalog` 在 engine 侧是合法 namespace
- `default_mapping` 与 `catalog_mappings` 组合后不会产生歧义

对于已 sync 的 typed binding grounding，还应能检查：

- carrier 对应的 authority locator 能否被至少一个 active mapping 解析
- 若某 semantic object 依赖特定 engine class，可否解析到满足 capability 的 engine

## 推荐的类型组合

### Source 侧

#### `mysql`

- authority 视角通常不具备强 catalog 层
- 需要 Factum 生成稳定 `authority_catalog`
- 推荐 `authority_catalog` 默认使用 source-local logical catalog 名称

#### `hive_metastore`

- authority catalog 通常明确存在
- 适合作为 lakehouse metadata authority

#### `iceberg_rest`

- authority catalog 明确存在
- 适合与 Trino / Spark execution 分离建模

#### `trino`

- 表示 Trino 暴露的 catalog 视图本身是 authority
- 适用于联邦视图直接作为 metadata authority 的场景

#### `duckdb`

- 通常是单机 authority
- authority 与 execution 很可能指向同一文件，但概念上仍分离

### Execution 侧

#### `trino`

- 适合联邦 execution
- `catalog_mappings` 是主配置面

#### `spark`

- 适合批式 / distributed execution
- 应允许 authority catalog 映射到 Spark catalog 名称

#### `duckdb`

- 适合单机本地 execution
- 常见为 pass-through 或极少量 default mapping

## 示例

### 示例 1：Hive Metastore source -> Trino engine

```json
{
  "source": {
    "source_id": "src_hms_prod",
    "source_type": "hive_metastore",
    "authority": {
      "catalog_system": "hive_metastore",
      "default_catalog": "hms"
    }
  },
  "engine": {
    "engine_id": "eng_trino_prod",
    "engine_type": "trino"
  },
  "mapping": {
    "mapping_id": "map_hms_to_trino",
    "source_id": "src_hms_prod",
    "engine_id": "eng_trino_prod",
    "priority": 10,
    "default_mapping": {
      "execution_catalog": "hive",
      "default_schema": null
    },
    "catalog_mappings": [
      {
        "authority_catalog": "hms",
        "execution_catalog": "hive",
        "default_schema": null
      }
    ]
  }
}
```

authority locator:

```json
{
  "catalog": "hms",
  "schema": "analytics",
  "table": "orders"
}
```

resolved execution locator:

```json
{
  "catalog": "hive",
  "schema": "analytics",
  "table": "orders"
}
```

### 示例 2：Iceberg REST source -> Spark engine

```json
{
  "mapping_id": "map_iceberg_to_spark",
  "source_id": "src_iceberg_rest",
  "engine_id": "eng_spark_prod",
  "priority": 20,
  "default_mapping": {
    "execution_catalog": "prod_iceberg",
    "default_schema": null
  },
  "catalog_mappings": [
    {
      "authority_catalog": "lakehouse",
      "execution_catalog": "prod_iceberg",
      "default_schema": null
    }
  ]
}
```

### 示例 3：DuckDB source -> DuckDB engine

```json
{
  "mapping_id": "map_duckdb_local",
  "source_id": "src_duckdb_local",
  "engine_id": "eng_duckdb_local",
  "priority": 100,
  "default_mapping": {
    "execution_catalog": null,
    "default_schema": "main"
  },
  "catalog_mappings": []
}
```

这表示：

- authority identity 仍来自 DuckDB source
- execution 仍在 DuckDB engine 上
- 当 authority schema 缺失时，execution fallback 到 `main`

## 与当前 `source_engine_binding.namespace` 的关系

当前 `namespace` 只适合表达：

- 一个 engine 的 catalog 前缀
- 一个粗粒度默认 schema

它不够表达：

- authority catalog 到 execution catalog 的显式映射
- source object identity 与 execution locator 的区分
- source-level default 与 catalog-level override 的固定优先级

因此，后续应将现有 `source_engine_binding.namespace` 逐步收敛为 `mapping`：

- `namespace.catalog` -> `default_mapping.execution_catalog`
- `namespace.schema` -> `default_mapping.default_schema`

但仅当 source 只含一个 authority catalog，或者产品显式选择“全部 catalog 共用一个 execution catalog”时，才能无损迁移。

## 与 typed semantic binding 的关系

typed semantic binding 不应直接存 execution-side locator。

推荐边界：

- semantic binding grounding：
  绑定 `source object`
- runtime compile：
  读取 `source object.authority_locator`
- routing / lowering：
  根据选中的 `mapping` 解析 `resolved_execution_locator`
- execution：
  只消费 execution locator

这样可以避免 semantic layer 因 execution 侧 catalog 变化而整体重写。

## 后续扩展方向

本文定稿后，后续可按需要扩展：

1. schema-level mapping
2. object-level remap
3. materialized projection / MV contract
4. execution-side availability / denylist
5. mapping-aware readiness diagnostics

但这些都应建立在本文的三对象模型之上，而不是回退到隐式 namespace 拼接。

## 结论

Factum 数据平面应收敛为：

- `source`：metadata authority
- `execution engine`：runtime execution authority
- `mapping`：authority-to-execution projection

并固定以下边界：

- source object identity 锚定在 authority
- execution locator 通过 mapping 派生
- mapping 先只支持两层：
  - source-level default
  - authority catalog-level override

这是当前从“source/engine 弱绑定”走向“authority / execution 显式分离且可路由”的最小充分 contract。
