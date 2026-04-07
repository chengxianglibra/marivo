# Semantic Layer Dimension Schema Contract（草案）

本文定义 Factum semantic layer 中 `dimension` 的目标 schema contract。

本文是**语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/entity-schema-contract.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/time-schema-contract.zh.md`
- `docs/semantic/typed-binding-contract.zh.md`
- `docs/semantic/compiler-spec.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`

本文重点回答：

- `dimension` 在新的 semantic layer 中应该承载什么
- 哪些内容应从当前 `metric.dimensions / allowed_dimensions` 中迁出
- `dimension` 与 `entity`、`metric`、`process object`、typed binding、compiler 的职责边界如何划分
- 如何表达维度值治理、层级关系与时间锚点依赖，而不泄漏物理实现

## Purpose

本文用于为 `dimension` 提供一套更稳定、更受治理的 typed contract，使其能够：

- 表达共享分析轴的稳定语义身份
- 为 `entity.stable_descriptors`、`process.exported_dimension_refs` 与请求维度解析提供可引用的 `dimension_ref`
- 为 compiler 提供显式的维度合法性、层级性与值治理信息
- 与 typed binding contract 解耦，使底层列、lookup、join、枚举表与派生逻辑可以独立演进

本文不定义：

- 最终数据库 DDL
- 最终 REST endpoint shape
- 最终 source binding 的物理字段结构
- 最终 SQL / engine lowering 模板

## 背景

当前实现中的维度能力主要体现为：

- `metric.dimensions`
- `metric.allowed_dimensions`

这对于“该指标允许按哪些字符串分组”足够，但不足以稳定表达：

- 维度本身的语义身份
- 维度值类型与取值治理
- 层级 / drill down / roll up 关系
- 某个维度是实体稳定属性、过程导出属性，还是请求时分组轴
- compare / decompose / detect / experiment split 等动作下的结构化使用边界

因此，Factum 需要把 `dimension` 从“metric 上的字符串清单”提升为“独立的语义分析轴契约”。

## 设计目标

新的 dimension contract 应同时满足：

- **职责单一**：表达“这个分析轴是什么”，不表达“怎么把它 join 出来”
- **可引用**：可被 `entity`、`metric`、`process object`、compiler 稳定引用
- **可治理**：支持枚举值集合、层级关系与必要的发布版本信息
- **可组合**：可与 `entity.*`、`subject.*`、`grain.*`、`time.*` 组成统一 ref taxonomy
- **可迁移**：允许 runtime 先从字符串数组迁移到 `dimension.*` 引用，而不一次重写全部执行逻辑

## 非目标

本文明确不追求：

- 让 dimension 直接表达底层字段名、join path、source table
- 让 dimension 自己枚举所有兼容 metric / process 对象
- 让 cohort / segment / experiment membership 直接退化为 dimension 对象
- 把 compiler 的组合合法性全部前置到单个 dimension 标签里
- 把维度契约做成 SQL DSL 或 lookup DDL

## 核心设计结论

`dimension` 的公共 contract 应回答四个问题：

1. 这个维度轴在语义层中的稳定标识是什么
2. 它的值语义是什么，是否有受治理的枚举域或层级关系
3. 它依赖哪些时间锚点或层级语义
4. 它与其他对象如何组合，而不泄漏物理实现

它**不应**回答：

- 这个维度来自哪张表
- 具体走哪条 join path
- 哪个 metric family 可以直接使用它
- 哪个 adapter 用什么函数把它派生出来

一句话总结：

> dimension 应声明“这个分析轴是什么、值如何被治理、语义层级如何组织”，而不是声明“怎样把它拼成查询计划”。

## 统一 ref taxonomy

semantic layer 中不同语义轴必须使用不同命名空间，不能继续复用裸字符串。

建议采用以下稳定前缀：

- `entity.*`：稳定业务实体
- `subject.*`：总体主体
- `grain.*`：样本 / 输出粒度
- `key.*`：实体身份键
- `time.*`：时间语义
- `dimension.*`：分析维度语义
- `enum.*`：受治理的维度值集合（若后续引入独立 value-set catalog）
- `gate.*`：治理 gate
- `binding.*`：typed binding 中的受治理绑定锚点

这意味着：

- `dimension_ref` 只能指向 `dimension.*`
- `process.exported_dimension_refs` 应只保存 `dimension.*`
- `entity.stable_descriptors[*].dimension_ref` 应只保存 `dimension.*`

`"country"`、`"platform"`、`"variant"` 这类裸字符串可以保留为兼容层输入，但不应再作为跨对象 public ref。

## 统一建模原则

### 1. dimension 是一等分析轴，不是 metric 的附属字段

`metric` 负责 measurement semantics，`process object` 负责 process semantics，`dimension` 则负责 **analysis axis semantics**。

因此：

- `metric` 不在最小 public contract 中直接维护整套维度兼容矩阵
- `process object` 只声明“哪些维度会被稳定导出”
- `entity` 只声明“哪些维度是稳定描述属性”
- `dimension` 本身负责“这个维度轴是什么意思”

### 2. dimension 公共 contract 不暴露物理实现

以下内容不应出现在 dimension public schema 中：

- 物理字段名
- source object
- join path
- lookup 表名
- engine-specific 派生表达式

这些应由 typed binding contract 表达，dimension 只保留语义 ref 与结构化治理信息。

### 3. dimension 不直接枚举兼容对象，组合合法性留给 compiler

像以下字段不应成为 dimension 主契约：

- `compatible_metrics`
- `compatible_processes`
- 宽泛的 `allowed_for_intents`

因为这会制造“对象结构真相”和“兼容性标签真相”两套来源。

更稳定的做法是：

- dimension 只表达自身轴能力
- metric / process / entity 表达各自的消费或导出关系
- compiler 根据四者交叉校验请求是否合法

### 4. dimension 不定义 population/process 语义

下列语义不应被吸收到 dimension 中：

- cohort membership
- experiment assignment / exposure
- segment population rule
- funnel step matching
- lifecycle transition

这些属于 `process object` 或更高阶语义对象。dimension 可以表达 `dimension.variant`、`dimension.step`、`dimension.state` 这类下游消费轴，但不定义这些轴是如何被过程构造出来的。

### 5. hierarchy 与 value governance 属于 dimension，自身可被复用

dimension 应允许表达：

- flat / hierarchical / ordinal / time-derived 这类轴类型
- 受治理枚举值集合
- null / tail 治理策略
- roll up 的主语义邻接关系

但它不负责选择某次请求究竟要不要展开这些层级，展开是否合法仍由 compiler 结合 metric/process 上下文判断。

## dimension 要回答什么

新的 dimension contract 主要回答：

- 它的稳定语义标识是什么（`dimension_ref`）
- 它的值类型是什么（string、number、date、boolean 等）
- 它属于哪类轴语义（categorical、hierarchical、time_derived、label、state、variant）
- 它是开放域还是受治理枚举域
- 它是否存在层级上的 roll up 关系
- 它在需要时依赖哪个时间语义锚点
- 它的分组治理、枚举兼容与长尾处理规则是什么

## dimension 不要回答什么

新的 dimension contract 不回答：

- 在哪张表取值
- 通过什么字段或 join relation 获取
- 哪个 metric 已经导入了它
- 哪个 process subtype 具体生成了它
- 哪个 adapter 用什么派生 SQL 生成它

## 通用 Schema

### 公共头部

```python
from typing import NotRequired, TypedDict


class DimensionHeader(TypedDict):
    dimension_ref: str
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
    dimension_contract_version: str
```

与其他 object contract 一样，dimension 的 catalog metadata（如 `status`、`revision`、`lineage`、`quality gates`、搜索辅助属性）应放在单独的 metadata envelope 中，而不是 dimension 主契约中。

### 字段说明

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `dimension_ref` | string | yes | 可被 entity / metric / process / compiler 引用的稳定维度标识；必须使用 `dimension.*`；也是 public contract 主标识 |
| `display_name` | string | no | 人类可读显示名 |
| `description` | string | no | 业务语义说明 |
| `dimension_contract_version` | string | yes | dimension 契约版本 |

### 公共子结构

#### DimensionValueDomainSpec

```python
from typing import Literal, NotRequired, TypedDict


class DimensionValueDomainSpec(TypedDict):
    structure_kind: Literal[
        "flat",
        "hierarchical",
        "ordinal",
        "time_derived",
    ]
    semantic_role: NotRequired[
        Literal[
            "category",
            "label",
            "state",
            "variant",
            "metric",
        ]
        | None
    ]
    value_type: Literal["string", "integer", "number", "boolean", "date", "datetime"]
    domain_kind: Literal["open", "enumerated"]
    enum_set_ref: NotRequired[str | None]
    enum_version: NotRequired[str | None]
```

字段含义：

- `structure_kind`：维度值的**结构组织方式**
  - `flat`：无层级关系
  - `hierarchical`：存在父子层级关系
  - `ordinal`：有序值（如评分等级）
  - `time_derived`：从时间语义派生
- `semantic_role`：维度的**行为语义角色**（可选）
  - `category`：一般分类维度
  - `label`：用户定义的标签
  - `state`：实体状态维度
  - `variant`：实验变体维度
  - `metric`：由 metric 派生的维度
- `value_type`：维度值类型
- `domain_kind`：开放域还是受治理枚举域
- `enum_set_ref` / `enum_version`：若使用受治理枚举，则引用对应枚举集与发布版本

**为什么要拆分？**

原 `semantic_kind` 混合了两种概念：
- **结构性**：`categorical`、`hierarchical`、`time_derived` 描述值的组织方式
- **行为性**：`label`、`state`、`variant` 描述维度的使用语义

拆分后：
- `structure_kind` 是必选的，描述维度如何组织
- `semantic_role` 是可选的，描述维度用于什么场景
- 两者可以独立组合，例如 `hierarchical` + `state`（有层级的状态维度）

`enum_compatibility_policy`、`null_handling`、`tail_handling` 这类消费/请求策略，不再视为 dimension 主 contract 的一部分；如需保留，应进入 compiler policy 或 catalog governance metadata。

#### DimensionHierarchySpec

```python
from typing import Literal, NotRequired, TypedDict


class DimensionHierarchySpec(TypedDict):
    hierarchy_type: Literal["flat", "parent_child", "ordinal", "calendar_rollup"]
    parent_dimension_ref: NotRequired[str | None]
```

字段含义：

- `hierarchy_type`：层级组织方式
- `parent_dimension_ref`：向上 roll up 时的父维度
- 子维度与 drill down 邻接关系由 catalog 遍历 `parent_dimension_ref` 反推，不再在主契约中重复存储

`parent_dimension_ref` 只表达语义 roll up 邻接，不表达物理执行路径。

#### DimensionGroupingContract

```python
from typing import TypedDict


class DimensionGroupingContract(TypedDict):
    supports_grouping: bool
```

字段含义：

- `supports_grouping`：是否可作为通用分组轴

`decompose`、`detect split`、`experiment export` 等组合合法性不再由 dimension 单独声明，而由 compiler 结合 metric / process / entity 契约推导。若需要额外治理提示，应进入 catalog metadata，而不是 dimension 主 schema。

#### TimeDerivedRequirementSpec

```python
from typing import TypedDict


class TimeDerivedRequirementSpec(TypedDict):
    required_time_anchor_ref: str
```

当 `semantic_kind = "time_derived"` 时，dimension 通过该子结构显式声明所需时间锚点，而不是在 header 顶层重复出现。

#### DimensionInterfaceContract

```python
from typing import NotRequired, TypedDict


class DimensionInterfaceContract(TypedDict):
    value_domain: DimensionValueDomainSpec
    hierarchy: NotRequired[DimensionHierarchySpec | None]
    grouping: NotRequired[DimensionGroupingContract | None]
    time_derived_requirement: NotRequired[TimeDerivedRequirementSpec | None]
```

#### CoreDimensionObject

```python
from typing import TypedDict


class CoreDimensionObject(TypedDict):
    header: DimensionHeader
    interface_contract: DimensionInterfaceContract
```

## 字段设计说明

### 1. `dimension_ref` 是维度命名空间中的稳定锚点

`dimension_ref` 不是显示名，也不是底层列名。它的职责是让：

- `entity.stable_descriptors[*].dimension_ref`
- `process.exported_dimension_refs`
- compiler `request_dimensions`

都能稳定引用同一个维度概念。

### 2. `structure_kind` 表达轴结构，`semantic_role` 表达使用语义

例如：

- `dimension.country` 可以是 `structure_kind: hierarchical` + `semantic_role: category`
- `dimension.step` 可以是 `structure_kind: flat` + `semantic_role: state`
- `dimension.variant` 可以是 `structure_kind: flat` + `semantic_role: variant`
- `dimension.signup_month` 可以是 `structure_kind: time_derived`

但它不需要在公共 schema 中声明这些维度究竟来自 entity、process 还是 request-time time projection。

### 3. time-derived dimension 必须显式声明所需时间锚点语义

`dimension.signup_month` 或 `dimension.calendar_week` 这类轴可以声明为 `structure_kind = “time_derived”`，但**不应**在 dimension contract 中直接写死物理时间列。

它们必须通过 `interface_contract.time_derived_requirement.required_time_anchor_ref` 声明”消费方需要提供哪个 `time.*` 语义锚点”，例如 `time.user_created_at`。

这里的 `time.*` 应引用统一的 `time-schema-contract.zh.md`，而不是由 dimension 自己定义一套局部时间命名。

其物理落地仍由消费它的对象或 binding 提供，例如：

- entity 的 `primary_time_ref`
- process 的 `anchor_time_ref`
- metric 的 `primary_time_ref`
- binding 的 contract target 映射

若 `structure_kind = “time_derived”`，则发布态 dimension 应显式提供 `required_time_anchor_ref`，否则 compiler 无法在组合期做稳定校验。

### 4. hierarchy 只表达语义 roll up path，不表达 join / lookup path

`dimension.country -> dimension.region` 这类关系可以表达为 roll up 邻接，但不能在 dimension 中附带：

- lookup table
- parent join key
- bridge path

这些属于 binding contract。

### 5. grouping contract 只定义维度自身是否可分组，不替代组合校验

例如 `supports_grouping = true` 的意思是“这个轴在维度本体上允许被请求为分组轴”，但最终是否合法仍取决于：

- metric 的 `additivity`
- process 是否导出该维度
- entity 是否把它暴露为稳定描述属性
- compiler 的请求时 gate

dimension 主契约不再维护 `supports_decomposition`、`supports_detection_split`、`supports_experiment_split_export` 这类跨对象 compatibility 标签，以避免出现两套真相来源。

### 6. 枚举兼容与 null / tail 治理必须是可执行语义

若 `domain_kind = "enumerated"`，则 published dimension 应尽量显式声明：

- `enum_set_ref`
- `enum_version`

更细粒度的枚举兼容、null/tail 行为若需要强约束，应由 governance context 或 compiler policy 承担，而不是默认进入 dimension 主 schema。

## 与其他对象的关系

### 与 entity 的关系

entity 通过 `stable_descriptors[*].dimension_ref` 引用 dimension。

其中：

- dimension 定义“这个维度轴是什么”
- entity 定义“这个维度是否稳定描述该实体”

因此“`country` 是一个受治理 categorical dimension”属于 dimension，而“`user.country` 是否是稳定属性”属于 entity。

### 与 metric 的关系

metric 在最小 public contract 中不直接维护维度兼容矩阵。若系统最终需要更细的维度限制或版本 pinning，建议交给 compiler compatibility profile，而不是回写到 dimension 主 contract。

### 与 process object 的关系

process object 通过 `exported_dimension_refs` 暴露下游可稳定消费的维度。

若 process 对导出维度的枚举版本或时间锚点有稳定前提，也应在 process contract 中显式声明，而不是回写进 dimension 本体。

其中：

- dimension 定义 `dimension.variant` / `dimension.step` / `dimension.state` 的语义轴
- process object 定义这些轴是如何由过程对象稳定导出的

### 与 typed binding contract 的关系

binding contract 负责把以下语义 ref 落到物理实现：

- `dimension_ref`
- `entity.stable_descriptors[*].dimension_ref`
- `process.exported_dimension_refs[*]`
- request-time `dimensions[*]`

它负责声明：

- 哪个 source object
- 哪个字段路径
- 哪个 contract target
- 哪些 join / as-of / lookup 约束受治理

而不是让 dimension 文档自己定义这些细节。

### 与 compiler / IR 的关系

compiler 读取 dimension 的目的应是：

- 归一化 `request_dimensions` 为 `dimension.*`
- 校验维度 ref 是否存在
- 校验分组、时间锚点、枚举兼容、null/tail 治理是否成立
- 结合 metric / process / entity 判断请求是否合法

IR 则只保留：

- `requested_dimensions`
- `exported_dimension_refs`

等 canonical dimension refs，而不复制维度的物理落地细节。

## 示例

### 示例 1：`country`

以下示例按收敛后的 public dimension contract 展示，不再把 catalog metadata、null/tail 策略或 compiler policy 直接写进主 schema。

```json
{
  "header": {
    "display_name": "Country",
    "description": "用户或事件所属国家维度",
    "dimension_ref": "dimension.country",
    "dimension_contract_version": "dimension.v1"
  },
  "interface_contract": {
    "value_domain": {
        "structure_kind": "hierarchical",
        "semantic_role": "category",
        "value_type": "string",
        "domain_kind": "enumerated",
        "enum_set_ref": "enum.iso_country_code",
        "enum_version": "2026-01"
      },
    "grouping": {
      "supports_grouping": true
    },
    "hierarchy": {
      "hierarchy_type": "parent_child",
      "parent_dimension_ref": "dimension.region"
    }
  }
}
```

### 示例 2：`variant`

```json
{
  "header": {
    "display_name": "Experiment Variant",
    "description": "实验 split 导出的 treatment / control 维度",
    "dimension_ref": "dimension.variant",
    "dimension_contract_version": "dimension.v1"
  },
  "interface_contract": {
    "value_domain": {
        "structure_kind": "flat",
        "semantic_role": "variant",
        "value_type": "string",
        "domain_kind": "enumerated",
        "enum_set_ref": "enum.experiment_variant",
        "enum_version": "v1"
      },
    "grouping": {
      "supports_grouping": true
    }
  }
}
```

### 示例 3：`signup_month`

```json
{
  "header": {
    "display_name": "Signup Month",
    "description": "基于用户注册时间派生的月粒度时间维度",
    "dimension_ref": "dimension.signup_month",
    "dimension_contract_version": "dimension.v1"
  },
  "interface_contract": {
    "value_domain": {
      "structure_kind": "time_derived",
      "value_type": "date",
      "domain_kind": "open"
    },
    "time_derived_requirement": {
      "required_time_anchor_ref": "time.user_created_at"
    },
    "hierarchy": {
      "hierarchy_type": "calendar_rollup",
      "parent_dimension_ref": "dimension.signup_quarter"
    },
    "grouping": {
      "supports_grouping": true
    }
  }
}
```

### 示例 4：`user_state`（有层级的状态维度）

```json
{
  "header": {
    "display_name": "User State",
    "description": "用户生命周期状态，具有层级关系",
    "dimension_ref": "dimension.user_state",
    "dimension_contract_version": "dimension.v1"
  },
  "interface_contract": {
    "value_domain": {
      "structure_kind": "hierarchical",
      "semantic_role": "state",
      "value_type": "string",
      "domain_kind": "enumerated",
      "enum_set_ref": "enum.user_lifecycle_state",
      "enum_version": "v2"
    },
    "grouping": {
      "supports_grouping": true
    },
    "hierarchy": {
      "hierarchy_type": "parent_child"
    }
  }
}
```

## 当前实现中建议移出的字段与能力

与 plan 中的候选字段相比，以下内容不应进入 dimension 主契约：

- `source binding`
- `join path`
- `compatible_metrics`
- 以对象枚举为主的 `allowed_for_intents`
- `supports_decomposition`
- `supports_detection_split`
- `supports_experiment_split_export`

它们应分别进入：

- `typed binding contract`
- compiler validation
- metric / process / entity 对象自己的消费边界

## 迁移建议

建议按以下顺序推进：

1. 引入独立 `dimension.*` 命名空间
   - 为现有 `platform`、`country`、`variant`、`state` 等字符串建立 canonical refs
2. 将 metric 上的 `dimensions / allowed_dimensions` 迁移为 dimension refs
   - 旧输入通过 translation layer 映射到 `dimension.*`
3. 对齐 entity / process object
   - `stable_descriptors`、`exported_dimension_refs` 全部引用 dimension catalog
4. 对齐 compiler
   - `request_dimensions` 先解析 dimension，再做 metric/process/entity 的时间锚点、版本兼容与治理校验
5. 最后再补维度 binding
   - 把 source object、field path、lookup/as-of 规则与时间锚点映射放到 typed binding

一言以蔽之：

> dimension 应是 semantic layer 中“共享分析轴”的稳定锚点；entity 负责稳定属性归属，metric 负责使用边界，process object 负责导出边界，binding 负责物理落地，compiler 负责组合校验。
