# Semantic Layer Entity Schema Contract（草案）

本文定义 Factum semantic layer 中 `entity` 的目标 schema contract。

本文是**语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/metric-process-contract.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`

本文重点回答：

- `entity` 在新的 semantic layer 中还应该承载什么
- 哪些内容应从当前 `entity` 中移出
- `entity` 应如何为 `metric`、`process object`、compiler 提供稳定引用
- `entity` 与 typed binding contract 的职责边界应如何划分

## Purpose

本文用于为 `entity` 提供一套更窄、更稳定的 typed contract，使其能够：

- 表达业务实体的稳定身份语义
- 为 `metric` 和 `process object` 提供可引用的 `entity_ref`
- 为 `stable_descriptors[*].dimension_ref` 提供稳定的实体归属语义
- 为 compiler 提供明确的实体边界，而不是混合的 metadata bucket
- 与 typed binding contract 解耦，使底层字段、join、窗口策略可以独立演进

本文不定义：

- 最终数据库 DDL
- 最终 REST endpoint shape
- 最终 source binding 的物理字段结构
- 最终 SQL / engine lowering 模板

## 背景

当前实现中的 `entity` 同时承载了多种职责：

- 身份键
- 层级关系
- join 约束
- 上游依赖
- 时间能力
- 杂项扩展属性

这会带来几个问题：

- `entity` 很难回答“它到底是业务实体，还是执行辅助对象”
- experiment / cohort / sessionization / funnel / lifecycle 等过程语义容易被错误塞入 `entity`
- `join_constraints`、`properties`、`mapping_json` 之间边界不清
- compiler 很难把 `entity` 当作稳定的 public semantic contract 消费

因此，Factum 需要把 `entity` 从“混合元数据对象”收缩为“核心实体身份契约（core entity contract）”。

## 设计目标

新的 entity contract 应同时满足：

- **职责单一**：只表达实体身份与稳定接口，不表达过程与执行策略
- **可引用**：能被 `metric`、`process object`、binding contract 直接引用
- **可治理**：支持独立版本、发布校验与兼容性治理
- **可扩展**：新增 process subtype、metric family、binding 细节时无需重塑 entity
- **可组合**：能与 `subject.*`、`grain.*`、`time.*` 等统一语义引用体系稳定组合

## 非目标

本文明确不追求：

- 让 entity 直接表达底层字段名
- 让 entity 直接定义 join graph、row predicate、dedup strategy
- 让 entity 继续承载 experiment / cohort / funnel / lifecycle 过程语义
- 让 entity 自己枚举所有 metric / intent 兼容关系
- 用 `properties` 继续兜底主语义

## 核心设计结论

`entity` 的公共 contract 应回答四个问题：

1. 这个业务对象在语义层中的稳定标识是什么
2. 它的稳定身份由哪些 semantic key refs 定义
3. 它是否有稳定父实体，以及这种归属是否单值
4. 它天然暴露哪些**实体自身即可成立**的稳定接口

它**不应**回答：

- 如何 join 到别的对象
- 底层键列叫什么
- 用哪张表实现
- 窗口如何裁剪
- attribution / funnel / state transition 如何计算
- 实验分组如何构造

一句话总结：

> entity 应声明“什么是这个业务实体”，而不是声明“怎样把这个实体拼成查询计划”。

## 统一 ref taxonomy

semantic layer 中不同语义轴必须使用不同命名空间，不能继续复用同一批裸值。

建议采用以下稳定前缀：

- `entity.*`：稳定业务实体，如 `entity.user`、`entity.session`
- `subject.*`：总体主体，如 `subject.user`、`subject.order`
- `grain.*`：样本 / 输出粒度，如 `grain.user`、`grain.session`
- `key.*`：实体身份键，如 `key.user_id`
- `time.*`：时间语义，如 `time.user_created_at`
- `dimension.*`：维度语义，如 `dimension.signup_channel`
- `gate.*`：治理 gate，如 `gate.user_identity_complete`
- `binding.*`：typed binding 中的受治理绑定锚点，如 `binding.user_identity`

这意味着：

- `entity_ref` 只能指向 `entity.*`
- `population_subject_ref` 只能指向 `subject.*`
- `observation_grain_ref` / `emitted_grain_ref` 只能指向 `grain.*`

`"user"`、`"session"` 这类裸字符串只能作为示意名称存在，不应再作为跨对象 public ref。

## 统一建模原则

### 1. entity 只表达业务实体身份，不表达 process 语义

`entity` 与 `process object` 的分工必须严格区分：

- `entity` 负责“谁是稳定业务实体”
- `process object` 负责“总体、上下文、过程如何形成”

因此以下语义不应再作为 entity 公共字段存在：

- assignment / exposure / cohort basis
- attribution window
- funnel step matching
- session close rule
- lifecycle transition rule
- 任何“仅在窗口内唯一”才成立的身份定义

这些属于 `process object` 或 compiler / IR。

### 2. entity 公共 contract 不暴露物理实现

以下内容不应出现在 entity 的 public schema 中：

- 物理字段名
- join path
- source table
- dedup key
- partition column
- engine-specific 时间列实现

这些应由 typed binding contract 表达，entity 只保留语义 ref。

### 3. identity 应以语义键引用表达，并锚定到单一 identity binding

当前实现中的 `keys = ["user_id"]` 更接近物理列列表，而不是稳定的语义身份。

更合理的做法是让 entity 声明：

- 哪些 **semantic key refs** 构成它的稳定身份
- 这些 key 的唯一性作用域是什么
- 这些 key 落在同一个 `identity_binding_ref` 上

这样 compiler / validator 才能判断它是真实体，而不是被多个 binding 临时拼装出来的 synthetic bundle。

### 4. entity 不保存 capability 标签，改为暴露结构化接口

以下字段不应成为 entity 主契约：

- `provided_capabilities`
- `supported_intents`
- `compatibility_tags`
- `required_metric_tags`

因为这些字段要么可从结构推导，要么会制造“结构真相”和“标签真相”两套来源。

更稳定的方式是只保留结构化接口，例如：

- `entity_ref`
- `identity`
- `hierarchy`
- `primary_time_ref`
- `population_subject_bridge`
- `stable_descriptors`

### 5. entity 只暴露稳定属性，不暴露时态不清的描述维度清单

原来的 `descriptor_refs` 过于宽泛，容易把 `country`、`platform`、`app_version` 这类时态和快照语义不清的字段包装成“天然稳定属性”。

新的 contract 应只允许暴露**稳定描述属性**：

- 天然 1:1，或
- 明确标注为 slowly changing，并给出 as-of 语义

其他 event-scoped / window-scoped / export-only 属性应进入 process object 或单独的 attribute contract。

## entity 要回答什么

新的 entity contract 主要回答：

- 它的稳定语义标识是什么（`entity_ref`）
- 它由哪些 semantic key refs 唯一确定
- 这些 key 落在什么 identity binding 上
- 它是否有稳定父实体
- 它默认关联哪个时间语义 ref
- 它是否与某个 `subject.*` 存在明确桥接关系
- 它有哪些稳定描述属性

## entity 不要回答什么

新的 entity contract 不回答：

- 这个实体在哪张表
- 用哪几个物理列 join
- 哪些过滤条件定义 cohort
- assignment 与 exposure 的关系是什么
- 哪个窗口定义转化归因
- 哪个 SQL kernel 负责 sessionization
- 输出样本的 grain 是什么

`grain.*` 属于 metric / process object 的样本接口，不应重新塞回 entity。

## 通用 Schema

### 公共头部

```python
from typing import Any, Literal, NotRequired, TypedDict


class EntityHeader(TypedDict):
    name: str
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
    status: NotRequired[Literal["draft", "published", "deprecated"]]
    entity_ref: str
    quality_gate_refs: NotRequired[list[str] | None]
    lineage: NotRequired[list[str] | None]
    properties: NotRequired[dict[str, Any] | None]
    revision: NotRequired[int]
    entity_contract_version: str
```

### 字段说明

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `name` | string | yes | 语义层唯一名称 |
| `display_name` | string | no | 人类可读显示名 |
| `description` | string | no | 业务语义说明 |
| `status` | enum | no | 生命周期状态 |
| `entity_ref` | string | yes | 可被 metric / process / compiler 引用的稳定实体标识；必须使用 `entity.*` |
| `quality_gate_refs` | array[string] | no | 发布或执行前应满足的治理 gate |
| `lineage` | array[string] | no | 上游依赖语义对象引用 |
| `properties` | object | no | 辅助元数据，不承载主语义 |
| `revision` | integer | no | 发布版本序号 |
| `entity_contract_version` | string | yes | entity 契约版本 |

### 公共子结构

#### SemanticRef

与其他 semantic 文档一致，entity 通过 `SemanticRef` 引用稳定语义对象，而不是直接引用物理字段。

```python
from typing import NotRequired, TypedDict


class SemanticRef(TypedDict):
    ref: str
    description: NotRequired[str | None]
```

#### EntityIdentitySpec

```python
from typing import Literal, NotRequired, TypedDict


class EntityIdentitySpec(TypedDict):
    key_refs: list[str]
    identity_binding_ref: str
    uniqueness_scope: Literal["global", "parent_scoped"]
    id_stability: Literal["stable", "reassignable", "ephemeral"]
    nullable_key_policy: NotRequired[Literal["reject", "allow_partial"] | None]
```

字段含义：

- `key_refs`：构成实体身份的语义键引用，如 `key.user_id`
- `identity_binding_ref`：该实体身份落地到哪个受治理 binding 锚点；它应直接指向某个 binding 的 `binding_ref`，且 `key_refs` 应在同一个 identity binding 中闭合
- `uniqueness_scope`：唯一性作用域；仅允许全局唯一或在稳定父实体下唯一
- `id_stability`：该实体 ID 是否可重分配、是否天然短暂
- `nullable_key_policy`：缺键时的治理策略

`window_scoped` 不再允许出现。任何只有依赖窗口才能成立的对象，都不应被直接建模为 core entity。

#### EntityHierarchySpec

```python
from typing import Literal, NotRequired, TypedDict


class EntityHierarchySpec(TypedDict):
    parent_entity_ref: NotRequired[str | None]
    cardinality_to_parent: NotRequired[Literal["one_to_one", "many_to_one"] | None]
    ownership_semantics: NotRequired[
        Literal["belongs_to", "contains", "derives_from"] | None
    ]
```

这部分只表达稳定层级语义，不表达 join plan。

`many_to_many` 不再允许出现在 hierarchy 中；若确实存在多对多关系，应进入单独的 relation / bridge contract。

#### PopulationSubjectBridge

```python
from typing import Literal, TypedDict


class PopulationSubjectBridge(TypedDict):
    subject_ref: str
    relation: Literal["same_identity", "compatible_subject"]
```

字段含义：

- `subject_ref`：与该实体存在明确桥接关系的总体主体引用；必须使用 `subject.*`
- `relation`：
  - `same_identity`：实体与主体共用同一身份基准，例如 `entity.user` ↔ `subject.user`
  - `compatible_subject`：实体可稳定 roll up / map 到该主体，但不是同一身份

#### StableDescriptorSpec

```python
from typing import Literal, NotRequired, TypedDict


class StableDescriptorSpec(TypedDict):
    dimension_ref: str
    value_stability: Literal["stable", "slowly_changing"]
    cardinality: NotRequired[Literal["one", "many"] | None]
    as_of_policy_ref: NotRequired[str | None]
```

字段含义：

- `dimension_ref`：稳定描述属性的维度引用
- `value_stability`：值是否天然稳定，或会缓慢变化
- `cardinality`：单值还是多值
- `as_of_policy_ref`：若为 slowly changing，需要给出 as-of 语义

#### EntityInterfaceContract

```python
from typing import NotRequired, TypedDict


class EntityInterfaceContract(TypedDict):
    identity: EntityIdentitySpec
    hierarchy: NotRequired[EntityHierarchySpec | None]
    primary_time_ref: NotRequired[str | None]
    population_subject_bridge: NotRequired[PopulationSubjectBridge | None]
    stable_descriptors: NotRequired[list[StableDescriptorSpec] | None]
```

字段含义：

- `identity`：实体身份契约
- `hierarchy`：父子层级关系
- `primary_time_ref`：实体主时间语义引用
- `population_subject_bridge`：与总体主体的显式桥接关系
- `stable_descriptors`：仅包含稳定或明确定义 slowly changing 语义的描述属性

#### CoreEntityObject

```python
from typing import TypedDict


class CoreEntityObject(TypedDict):
    header: EntityHeader
    interface_contract: EntityInterfaceContract
```

## 字段设计说明

### 1. `entity_ref` 是实体命名空间中的稳定锚点

`entity_ref` 不是 display label，也不是底层表名。它的职责是让：

- `process object.entity_ref`
- `metric.observed_entity_ref`
- compiler normalized inputs

都能稳定引用同一个实体概念。

它必须使用 `entity.*` 命名空间，例如：

- `entity.user`
- `entity.session`
- `entity.path_match`

### 2. `identity_binding_ref` 保证 entity 不是被任意拼装出来的

entity 不应只声明 `key_refs`，而把“这些 key 到底在哪个稳定实体基座上成立”留给实现层猜测。

引入 `identity_binding_ref` 的目的，是要求：

- 身份键在单一 binding 锚点上闭合
- compiler 能确认存在稳定实体边界
- binding 审计能区分“实体基座”与“跨对象投影”

### 3. `stable_descriptors` 是稳定属性，不是 join 权限清单

`stable_descriptors` 表达的是：

- 哪些维度在语义上稳定描述该实体

而不是：

- 哪些字段可以被拿来 join
- 哪些 source object 上存在同名列

后者属于 binding contract。

### 4. `primary_time_ref` 是语义时间引用，不是物理时间列

entity 可以声明自己默认围绕哪个时间语义组织，例如：

- `time.user_created_at`
- `time.session_started_at`
- `time.order_paid_at`

但不能在 entity contract 中直接写：

- `event_time`
- `created_at_column`
- `pay_date`

这些属于 binding 层。

### 5. `population_subject_bridge` 替代 capability 标签

entity 与总体主体的关系如果重要，应显式建模，而不是通过：

- `provided_capabilities = ["population_subject"]`

这类模糊标签来暗示。

这样更容易校验：

- `metric.population_subject_ref`
- `process.population_subject_ref`

是否与实体语义相容。

## 当前实现中应移出的字段

与当前 `semantic_entities` 相比，以下内容应从 entity 主契约中移出：

- `join_constraints`
- `upstream_dependencies`
- `properties.time_capabilities`
- `canonical_grain`
- `descriptor_refs`
- `provided_capabilities`
- 任何 process / cohort / experiment 相关规则
- 任何物理 join / filter / partition 细节

这些内容分别进入：

- `process object`
- `typed binding contract`
- `metric v2`
- compiler normalization / IR

## 与其他对象的关系

### 与 dimension contract 的关系

entity 通过 `stable_descriptors[*].dimension_ref` 消费 dimension contract。

其中：

- dimension 定义“这个维度轴本身是什么”
- entity 定义“这个维度是否稳定描述该实体”

因此 `dimension.country` 的值域、层级与分析轴语义属于 dimension，而 `user.country` 是否可作为 `entity.user` 的稳定描述属性属于 entity。

### 与 process object 的关系

`process object` 通过以下方式消费 entity：

- `population_subject_ref = "subject.user"`
- `entity_ref = "entity.session"`
- `emitted_grain_ref = "grain.session"`

其中：

- `subject.*` 表达总体主体
- `entity.*` 表达稳定业务实体
- `grain.*` 表达输出样本粒度

entity 只提供稳定实体锚点，不负责描述 process 规则。

### 与 metric 的关系

metric 通过 entity 获得：

- `observed_entity_ref`
- `population_subject_ref` 的桥接校验基础
- `primary_time_ref` 的语义一致性基础

metric 若需要样本粒度，应独立声明 `observation_grain_ref`，而不是回退到 entity 内部寻找 `canonical_grain`。

### 与 binding contract 的关系

binding contract 负责把以下语义 ref 落到物理实现：

- `entity_ref`
- `identity.key_refs`
- `identity.identity_binding_ref`
- `primary_time_ref`
- `stable_descriptors.dimension_ref`

若 entity 依赖外部 binding 提供的时间锚点或桥接能力，也应通过 binding contract 的显式 imports 组合，而不是靠全局命名约定推断。

因此 entity 文档不需要直接定义表列 schema。

### 与 compiler / IR 的关系

compiler 读取 entity 的目的应是：

- 确认稳定实体身份
- 确认父子归属是否单值且稳定
- 读取实体主时间与稳定属性接口
- 校验与 `subject.*`、`grain.*` 的兼容关系

而不是从 entity 直接提取 SQL 计划信息。

## 示例

### 示例 1：`user`

```json
{
  "header": {
    "name": "user",
    "display_name": "User",
    "description": "平台中的稳定注册用户实体",
    "entity_ref": "entity.user",
    "quality_gate_refs": ["gate.user_identity_complete"],
    "lineage": ["subject.user"],
    "revision": 3,
    "entity_contract_version": "entity.v3"
  },
  "interface_contract": {
    "identity": {
      "key_refs": ["key.user_id"],
      "identity_binding_ref": "binding.user_identity",
      "uniqueness_scope": "global",
      "id_stability": "stable",
      "nullable_key_policy": "reject"
    },
    "primary_time_ref": "time.user_created_at",
    "population_subject_bridge": {
      "subject_ref": "subject.user",
      "relation": "same_identity"
    },
    "stable_descriptors": [
      {
        "dimension_ref": "dimension.signup_channel",
        "value_stability": "stable",
        "cardinality": "one"
      }
    ]
  }
}
```

### 示例 2：`session`

```json
{
  "header": {
    "name": "session",
    "display_name": "Session",
    "description": "围绕用户行为形成的稳定会话实体",
    "entity_ref": "entity.session",
    "quality_gate_refs": ["gate.session_identity_complete"],
    "revision": 1,
    "entity_contract_version": "entity.v3"
  },
  "interface_contract": {
    "identity": {
      "key_refs": ["key.session_id"],
      "identity_binding_ref": "binding.session_identity",
      "uniqueness_scope": "global",
      "id_stability": "ephemeral",
      "nullable_key_policy": "reject"
    },
    "hierarchy": {
      "parent_entity_ref": "entity.user",
      "cardinality_to_parent": "many_to_one",
      "ownership_semantics": "belongs_to"
    },
    "primary_time_ref": "time.session_started_at",
    "stable_descriptors": [
      {
        "dimension_ref": "dimension.device_type",
        "value_stability": "stable",
        "cardinality": "one"
      },
      {
        "dimension_ref": "dimension.app_version",
        "value_stability": "stable",
        "cardinality": "one"
      }
    ]
  }
}
```

## 设计上的直接收益

采用该 entity contract 后，会有几个直接收益：

1. **entity 边界更清楚**
   - 不再与 process、metric、binding 混在一起
2. **subject / entity / grain 三条语义轴被显式分开**
   - compiler 与 validator 不再依赖裸字符串猜语义
3. **compiler 更容易做静态校验**
   - 实体身份、层级、主体桥接、时间语义都更明确
4. **binding 可以独立演进**
   - 不必为了底层字段变化而修改 entity 公共契约
5. **版本治理更自然**
   - entity 可独立拥有 `entity_contract_version`

## 迁移建议

建议按以下顺序推进：

1. 统一 ref taxonomy
   - 全面切换到 `entity.*` / `subject.*` / `grain.*` / `time.*` / `dimension.*`
2. 收缩 entity 主契约
   - 删除 `canonical_grain`、`provided_capabilities`、宽泛 `descriptor_refs`
3. 引入 identity binding 审计
   - 为每个 entity 找到单一 `identity_binding_ref`
4. 对齐 `metric v2`
   - 将 `observed_entity` / `observation_grain` 升级为 `observed_entity_ref` / `observation_grain_ref`
5. 对齐 `process object`
   - 将 `entity_grain` 升级为 `emitted_grain_ref`
6. 为旧 API 补 translation layer
   - 旧 `keys` / `level` / `join_constraints` / `time_capabilities` 不应直接等价为新 entity contract

一句话总结：

> entity 应是 semantic layer 中“业务实体身份”的稳定锚点；总体主体属于 `subject.*`，样本粒度属于 `grain.*`，过程属于 process object，物理落地属于 binding，组合与 lowering 属于 compiler。
