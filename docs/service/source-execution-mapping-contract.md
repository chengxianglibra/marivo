# Source / Execution Engine / Mapping Contract

状态：draft design。

本文档定义 Marivo 数据平面中 `source`、`execution engine` 与 `mapping` 的目标 schema 契约。

本文是服务/运行时设计说明，不是当前 HTTP API 契约，也不是对现有 `/sources`、`/engines`、`/bindings` 路由的逐字段解释。它描述的是下一阶段应收敛到的领域模型，用于支撑：

- metadata authority 与 execution authority 分离
- source sync 仍以 metadata authority 为准
- execution routing 仍以 execution engine 为准
- authority namespace 到 execution namespace 的显式、受治理投影

## V1 冻结决策

### 1.1 三对象边界

v1 明确冻结以下边界：

- `source` 只负责 metadata authority
- `execution engine` 只负责 runtime execution authority
- `mapping` 是唯一 authority-to-execution projection contract

实现约束：

- 不把 execution catalog/schema projection 重新塞回 `source`
- 不把 authority identity 重新塞回 `engine`
- 不再把 `binding.namespace` 扩展为目标态内部权威字段

### 1.2 当前支持矩阵

v1 当前只支持以下运行时类型：

- `source`
  - `trino`
  - `duckdb`
- `execution engine`
  - `trino`
  - `duckdb`

未实现类型不进入当前 contract；后续若要支持，再连同 schema、adapter、runtime、API 一起增补，而不是先做预留枚举。

### 1.3 兼容策略

v1 保留现有 `/sources`、`/engines`、`/bindings` 读写面，但兼容语义固定为：

- `binding.namespace` 仅是 legacy compatibility field
- 当前 routing 仍可读取它，但它不再代表目标态内部权威语义
- 后续 authority-to-execution projection 必须收敛到独立 `mapping` 对象

### 1.4 Authority Locator 最小字段集

v1 authority locator 的最小字段集固定为：

- `catalog`
- `schema`
- `table`

source object identity、typed binding grounding、routing compile 后续都必须共享这一语义。

### 1.5 Mapping Resolution 顺序与失败面

v1 mapping resolution 顺序固定为：

1. 先按 `authority_catalog` 精确命中 mapping 项
2. 命中后显式产出 `execution_catalog`
3. 默认保留 authority `schema`
4. 只有 authority `schema` 缺失时，才允许使用 `default_schema`

v1 失败面固定为：

- `mapping_missing`
- `mapping_incomplete`
- `mapping_invalid_namespace`

同时，active mapping 采用 fail-closed 策略：必须覆盖该 source 当前 authority catalog 全集。

## 背景

当前实现中：

- `source` 同时承担“catalog metadata authority”与“可用于执行的 catalog endpoint”两种含义
- `execution engine` 表达执行端能力与连接
- `source_engine_binding.namespace` 仅能做轻量 catalog/schema 前缀修正

这在简单场景下可用，但不足以稳定表达以下事实：

- 元数据权威系统与执行系统不是同一个 endpoint
- source object identity 应锚定在 metadata authority，而不是执行端可见名字
- execution engine 对 authority object 的可见 namespace 需要显式 contract，而不是运行时猜测

因此，Marivo 需要把数据平面收敛为三个一等对象：

1. `source`
2. `execution engine`
3. `mapping`

其中 `mapping` 不再只是 namespace 补丁，而是 authority-to-execution projection contract。

## 目标

- 保留 `source` 与 `execution engine` 的分离建模
- 明确 source object identity 锚定在 metadata authority
- 仅支持两层 mapping：
  - source-level mapping object
  - authority catalog-level mapping entries
- 本轮 `source_type` 只支持 `trino` 与 `duckdb`
- 本轮 `engine_type` 只支持 `trino` 与 `duckdb`
- 让 routing、sync、semantic grounding 都能依赖同一份 mapping contract

## 非目标

- 定义 object-level table override
- 定义 schema-level 或 table-level rewrite
- 让 typed binding 直接绑定 execution-side locator
- 在本轮引入新的 federation planner contract
- 规定当前 HTTP API 必须立刻按本文变更

## 核心原则

### 1. Metadata identity 归属于 source

`source` 是 metadata authority。Marivo sync 得到的 object identity、层级、schema 与后续 semantic grounding，默认都以 source namespace 为准。

### 2. Execution namespace 归属于 execution engine

`execution engine` 表达“哪些执行端可以运行查询，以及它们支持哪些能力”。执行侧 locator 是 projection，不是 object identity 源。

### 3. Mapping 是 authority-to-execution contract

`mapping` 负责回答：

- source 上的 object 在某个 execution engine 看起来属于哪个 catalog
- 若 execution engine 需要默认 schema，默认值是什么
- 哪些 authority catalog 可以被该 execution engine 消费

### 4. 没有 mapping，就没有稳定 routing

当 source 与 execution engine 分离时，Marivo 不应通过连接参数、默认 catalog 或短名称推断 execution-side locator。若没有可用 mapping，应作为 readiness / routing blocker 明确失败。

### 5. Typed binding 继续锚定 source object

typed semantic binding 的 carrier grounding 应继续依赖 sync 后的 `source object`。编译与执行阶段再通过 `mapping` 将 authority locator 投影到 execution locator。

## 支持对象

### Source 类型

在当前 contract 中，本轮 source 类型只收敛为：

- `trino`
- `duckdb`

它们的共同职责是：

- 提供 metadata authority 入口
- 支持 schema / table / column 级 sync
- 生成稳定的 authority-side object identity

注意：

- `trino` 作为 source 时，表示“Trino 暴露出来的 catalog 视图”是 metadata authority
- `duckdb` 作为 source 时，表示 DuckDB 文件自身是 metadata authority；由于其 authority 视角不具备原生 catalog 层，必须通过 `synthetic_catalog` 冻结 authority catalog

### Execution engine 类型

在当前 contract 中，本轮 execution engine 类型只收敛为：

- `trino`
- `duckdb`

它们的共同职责是：

- 提供 query execution 入口
- 提供 execution-side capability surfaces
- 作为 routing 选择目标
- 接收 authority-to-execution projection 后的 locator

## 统一 locator 语义

为避免 source 与 execution 对象名混淆，本文统一使用以下概念：

- `authority catalog`
  source 视角下的 catalog 名称；对不具备原生 catalog 层的 source，必须通过 source 配置提供稳定 `synthetic_catalog`
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
  "source_type": "trino | duckdb",
  "display_name": "string",
  "status": "active | inactive | deprecated",
  "authority": {
    "catalog_system": "trino | duckdb",
    "connection": {},
    "synthetic_catalog": "string | null"
  },
  "sync": {
    "mode": "selected | all | none"
  },
  "intrinsic_capabilities": {
    "supports_partitions": false
  },
  "policy": {
    "allow_live_browse": true,
    "allow_sync": true
  },
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

### Source 字段语义

- `source_id`
  Marivo 内部稳定标识。
- `source_type`
  authority adapter 类型。
- `authority.catalog_system`
  authority 的底层系统类型；通常与 `source_type` 一致，但保留单独字段以避免 future overloading。
- `authority.connection`
  metadata authority 连接参数。
- `authority.synthetic_catalog`
  source 级稳定逻辑 catalog 名称。对没有原生 catalog 层的 source 必填，用于冻结 authority-side object identity。
- `sync.mode`
  metadata sync 范围策略，不涉及 execution。它只回答：
  - 不同步
  - 仅同步显式选择集
  - 尝试同步 authority 下全部对象
- `intrinsic_capabilities`
  source implementation 固有且会影响 sync/runtime 行为的 metadata-side 能力。它是只读事实，不是 operator 可写配置。
- `policy`
  operator 控制面。它表达“允许 Marivo 暴露/使用什么”，而不是“source 天生支持什么”。

### Source 不回答什么

`source` 不应回答：

- 查询最终在哪个 engine 上执行
- authority object 在 execution 端如何命名
- engine 的 SQL dialect 或 policy support

这些都属于 `execution engine` 或 `mapping`。

### Source 能力分层原则

为避免把“真实能力”和“运营开关”混在一起，`source` 侧字段必须分层：

- `intrinsic_capabilities`
  只描述由 source type / adapter implementation 决定、且会影响 Marivo 核心运行路径的固有能力。本轮最小集合只保留 `supports_partitions`，因为 sync runtime 会根据它决定是否枚举 partition。
- `policy`
  只描述 Marivo 是否允许对该 source 暴露相应能力，例如是否允许 live browse、是否允许执行 sync、是否允许 preview。

因此：

- `intrinsic_capabilities` 不应出现在 create/update writable contract 中
- Marivo 不应接受 operator 手工声明“该 source 支持某能力”来覆盖实现事实
- 若 implementation 支持但运营上不希望暴露，应写入 `policy`，而不是伪造 `intrinsic_capabilities`
- 类似 `supports_table_preview`、`supports_table_properties`、`supports_column_comments`、`supports_column_stats` 这类“元数据丰富度”信号，可保留在 source detail/debug surface，但不进入本份核心 Source Contract

### Source catalog 不变量

为保证 authority identity 稳定，`source.authority` 必须满足以下约束：

- 若 source 具有原生 catalog 层，可不设置 `synthetic_catalog`
- 若 source 没有原生 catalog 层，必须设置 `synthetic_catalog`
- `synthetic_catalog` 一旦用于生成已 sync object 的 authority locator，就不应被静默改写；否则会破坏 grounding identity

## 2. Execution Engine Contract

`execution engine` 是 query execution object。它必须能回答“查询可以在哪里执行，以及该执行端具备哪些 runtime 能力”。

建议 public schema：

```json
{
  "engine_id": "eng_...",
  "engine_type": "trino | duckdb",
  "display_name": "string",
  "status": "active | inactive | deprecated",
  "connection": {},
  "default_namespace": {
    "catalog": "string | null",
    "schema": "string | null"
  },
  "intrinsic_capabilities": {
    "materialization_support": "temporary_table | catalog_table | none | unknown",
    "performance_class": "embedded | distributed | general_purpose",
    "federation_support": "none | connector | engine_native"
  },
  "deployment_capabilities": {
    "supported_step_types": [],
    "min_staleness_minutes": "integer | null"
  },
  "policy": {
    "allowed_step_types": [],
    "required_policy_support": []
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
- `intrinsic_capabilities`
  execution implementation 固有能力，例如 materialization 模式、federation 模式、performance class。它是只读事实，不是 operator 可写配置。
- `deployment_capabilities`
  具体 engine 实例在当前部署下的可用能力与约束，例如当前允许的 step types、staleness 边界、启用中的 runtime 特性。
- `policy`
  operator 对 engine 的使用控制面，例如允许 Marivo 把哪些 step type 路由到该 engine，或要求哪些策略前提才允许选中该 engine。

### Execution engine 不回答什么

`execution engine` 不应回答：

- sync 从哪里来
- source object 的 identity 是什么
- 哪个 authority catalog 应映射到哪个 execution catalog

这些都不属于 execution 本体。

### Execution 能力分层原则

为避免把“引擎做不到”和“引擎做得到但当前不让用”混为一谈，`execution engine` 侧字段必须分层：

- `intrinsic_capabilities`
  引擎类型和实现固有能力，不应通过 operator 配置伪造。本轮核心集合只保留真正参与 routing/planning 的字段：`materialization_support`、`performance_class`、`federation_support`。
- `deployment_capabilities`
  具体实例当前部署、连接器、运行时环境下的能力与限制。它可以由探测、实例元数据或受控声明得出，但不应冒充 intrinsic fact。像 `supported_step_types`、`min_staleness_minutes` 这类会影响 route fit、但明显依赖实例部署的属性，应归到这一层。
- `policy`
  Marivo 的路由/治理控制面。它表达“是否允许用”“允许用于什么任务”，而不是“引擎是否天生支持”。

因此：

- `intrinsic_capabilities` 不应出现在 create/update writable contract 中
- 若某能力是 engine type 固有事实，应由实现派生，而不是由配置写入
- 若某能力依赖实例部署或连接器，应落在 `deployment_capabilities`
- 若某能力是运营控制开关，应落在 `policy`
- 类似广义 `supported_sql_features` 这种未稳定参与 planner/routing 决策的标签集合，不进入本份核心 Engine Contract；若后续确有硬性消费方，再以收敛后的最小字段加入

## 3. Mapping Contract

`mapping` 是 `source -> execution engine` 的 authority-to-execution projection contract。

一个 mapping object 代表：

- 某个 source 的 authority namespace
- 在某个 execution engine 上如何被消费

本轮只支持两层 mapping：

1. source-level mapping object
2. authority catalog-level mapping entries

建议 public schema：

```json
{
  "mapping_id": "map_...",
  "source_id": "src_...",
  "engine_id": "eng_...",
  "status": "active | inactive | deprecated",
  "priority": 0,
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
- `catalog_mappings`
  authority catalog 级显式投影规则。它是 source/engine catalog 对齐的唯一 contract。

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

1. 要求 `catalog_mappings` 中存在匹配 `authority_catalog` 的项；若不存在则解析失败，报 `mapping_missing`
2. 对命中的 mapping 项：
   - `execution_catalog = matched.execution_catalog`
   - `schema = authority.schema`
   - 若 authority-side schema 缺失且 `matched.default_schema` 存在，则使用 `matched.default_schema`

若出现以下情况，应直接失败：

- authority catalog 缺少对应 mapping 项：`mapping_missing`
- mapping 未覆盖 source 当前 authority catalog 全集：`mapping_incomplete`
- `execution_catalog` 缺失，或试图用 `default_schema` 重写已有 authority schema：`mapping_invalid_namespace`

本轮不支持：

- 按 schema 做 rewrite
- 按 table 做 remap
- 一个 authority catalog 对应多个 execution catalog

### Mapping 不变量

- 同一 `source_id + engine_id` 下，`authority_catalog` 不能重复定义
- `catalog_mappings` 必须覆盖该 source 在该 mapping 下允许暴露的全部 authority catalogs
- mapping 解析只改 execution namespace，不改 authority object identity
- 若 source 类型天然不具备 catalog 层，也必须在 sync identity 中产生稳定 `authority_catalog`
- `default_schema` 只允许作为 schema 缺失时的 fallback；不能重写已存在 authority schema
- catalog 对齐必须由 `catalog_mappings` 显式声明；Marivo 不应依赖 source 默认值或 engine 默认值猜测 execution catalog

## 两层 mapping 的设计原因

本轮只做两层，是因为目标是先消除当前 `namespace` 补丁模型的不足，而不是一步引入过强的 object remap 系统。

两层 mapping 可以覆盖的主流场景：

- Trino source catalog `lakehouse` 在 Trino execution 中映射为 `iceberg_prod`
- DuckDB source 在 DuckDB engine 中直接 pass-through
- DuckDB source 在 Trino engine 中映射为一个显式 execution catalog

它不能覆盖的场景：

- authority 中同 catalog 的部分表映射到 execution 侧不同 catalog
- execution 端只暴露 authority 对象的部分 schema
- authority object 被 engine-side view / MV 替代

这些后续若有需要，再引入更细粒度 contract。

同时，本轮刻意不提供 source-level default catalog projection，因为 catalog 对齐必须是显式治理行为，而不是 source 或 engine 默认值推断结果。

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
- engine 的 intrinsic/deployment capabilities 是否满足任务
- engine policy 是否允许该类任务
- mapping priority

因此，`source -> engine` 可路由，不再等价于“存在一条 binding 记录”，而应等价于：

1. 存在 active mapping
2. mapping 能为涉及的 authority catalogs 提供完整 execution projection
3. engine 的 intrinsic/deployment capabilities 覆盖该类执行需求
4. engine policy 未禁止该类执行

当任一条件不满足时，应明确失败，而不是退回到短名称猜测。

## Readiness / Validation Contract

对于每条 mapping，最少应验证以下事项：

- `source_id` 与 `engine_id` 存在
- source 类型与 engine 类型的组合允许建立 mapping
- `catalog_mappings[*].authority_catalog` 在 source authority 中合法
- `catalog_mappings[*].execution_catalog` 在 engine 侧是合法 namespace
- `catalog_mappings` 覆盖范围与 source authority catalog 集合一致，或满足产品显式允许的子集策略

对于已 sync 的 typed binding grounding，还应能检查：

- carrier 对应的 authority locator 能否被至少一个 active mapping 解析
- 若某 semantic object 依赖特定 engine class，可否解析到同时满足 intrinsic/deployment capability 与 policy 的 engine

## 推荐的类型组合

### Source 侧

#### `trino`

- 表示 Trino 暴露的 catalog 视图本身是 authority
- 适用于联邦视图直接作为 metadata authority 的场景

#### `duckdb`

- 通常是单机 authority
- authority 视角通常不具备原生 catalog 层
- 必须配置稳定 `synthetic_catalog`
- authority 与 execution 很可能指向同一文件，但概念上仍分离

### Execution 侧

#### `trino`

- 适合联邦 execution
- `catalog_mappings` 是主配置面

#### `duckdb`

- 适合单机本地 execution
- 常见为 pass-through 或极少量 default mapping

## 示例

### 示例 1：Trino source -> Trino engine

```json
{
  "source": {
    "source_id": "src_trino_catalog",
    "source_type": "trino",
    "authority": {
      "catalog_system": "trino",
      "synthetic_catalog": null
    }
  },
  "engine": {
    "engine_id": "eng_trino_prod",
    "engine_type": "trino"
  },
  "mapping": {
    "mapping_id": "map_trino_catalog_to_trino",
    "source_id": "src_trino_catalog",
    "engine_id": "eng_trino_prod",
    "priority": 10,
    "catalog_mappings": [
      {
        "authority_catalog": "lakehouse",
        "execution_catalog": "iceberg_prod",
        "default_schema": null
      }
    ]
  }
}
```

authority locator：

```json
{
  "catalog": "lakehouse",
  "schema": "analytics",
  "table": "orders"
}
```

resolved execution locator：

```json
{
  "catalog": "iceberg_prod",
  "schema": "analytics",
  "table": "orders"
}
```

### 示例 2：DuckDB source -> Trino engine

```json
{
  "mapping_id": "map_duckdb_to_trino",
  "source_id": "src_duckdb_local",
  "engine_id": "eng_trino_prod",
  "priority": 20,
  "catalog_mappings": [
    {
      "authority_catalog": "duckdb_local",
      "execution_catalog": "duckdb_bridge",
      "default_schema": "main"
    }
  ]
}
```

### 示例 3：DuckDB source -> DuckDB engine

```json
{
  "source": {
    "source_id": "src_duckdb_local",
    "source_type": "duckdb",
    "authority": {
      "catalog_system": "duckdb",
      "synthetic_catalog": "duckdb_local"
    }
  },
  "mapping": {
    "mapping_id": "map_duckdb_local",
    "source_id": "src_duckdb_local",
    "engine_id": "eng_duckdb_local",
    "priority": 100,
    "catalog_mappings": [
      {
        "authority_catalog": "duckdb_local",
        "execution_catalog": "main",
        "default_schema": "main"
      }
    ]
  }
}
```

这表示：

- authority identity 仍来自 DuckDB source
- execution 仍在 DuckDB engine 上
- authority catalog `duckdb_local` 被显式对齐到 execution catalog `main`
- 当 authority schema 缺失时，execution schema fallback 到 `main`

## 与当前 `source_engine_binding.namespace` 的关系

当前 `namespace` 只适合表达：

- 一个 engine 的 catalog 前缀
- 一个粗粒度默认 schema

在 v1 中，它被明确降级为 legacy compatibility field：

- 可以继续保留在现有 `/bindings` 读写面中
- 可以继续被当前 routing 兼容读取
- 不能再被视为目标态 authority-to-execution 权威 contract

它不够表达：

- authority catalog 到 execution catalog 的显式映射
- source object identity 与 execution locator 的区分
- catalog 对齐必须显式治理，不能依赖默认 catalog 投影

因此，后续应将现有 `source_engine_binding.namespace` 逐步收敛为 `mapping`：

- `namespace.catalog` -> `catalog_mappings[*].execution_catalog`
- `namespace.schema` -> `catalog_mappings[*].default_schema`

但仅当 source 只含一个 authority catalog，或者产品显式为每个 authority catalog 生成对应 mapping 项时，才能无损迁移。

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
3. execution-side availability / denylist
4. mapping-aware readiness diagnostics

但这些都应建立在本文的三对象模型之上，而不是回退到隐式 namespace 拼接。

## 结论

Marivo 数据平面应收敛为：

- `source`：metadata authority
- `execution engine`：runtime execution authority
- `mapping`：authority-to-execution projection

并固定以下边界：

- source object identity 锚定在 authority
- execution locator 通过 mapping 派生
- source / engine 的 intrinsic capabilities 是只读派生事实，不是可写配置
- operator 控制面通过 `policy` 表达，而不是伪造 capability 事实
- catalog 对齐必须通过 `catalog_mappings` 显式声明，不依赖默认 catalog 猜测

这是当前从“source/engine 弱绑定”走向“authority / execution 显式分离且可路由”的最小充分 contract。
