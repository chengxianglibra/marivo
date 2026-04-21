# Enum Value Set Schema Contract（草案）

本文定义 Marivo semantic 文档体系中 `Enum Value Set` 的配套 schema contract。

本文是**语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/compiler-spec.zh.md`
- `docs/semantic/typed-binding-contract.zh.md`
- `docs/semantic/overview.md`

本文重点回答：

- `enum_set_ref` 指向的到底是什么
- `enum_version` 锚定的到底是哪一层
- 哪些内容属于受治理值域本体，哪些属于 `dimension` / compiler / governance / binding
- 为什么 `Enum Value Set` 需要独立文档边界，但不需要提升成新的顶层 semantic object

## Purpose

当前 `docs/semantic/` 中已经多次出现：

- `enum_set_ref`
- `enum_version`
- `domain_kind = "enumerated"`

但目录中还没有一份独立文档统一回答：

- `enum.iso_country_code` 这类 ref 到底引用什么
- `2026-01`、`v1` 这类版本号是“值集快照”还是“维度版本”
- 枚举值的稳定 identity、展示标签、别名、废弃状态各自应该落在哪层
- `dimension`、compiler、binding、governance 各自如何消费这个值域

本文的目标就是补上这层“受治理值域契约”，让 `dimension` 在引用枚举域时有稳定、清晰、可复用的被引用对象说明。

## 直接结论

### 1. 需要独立文档契约

`Enum Value Set` 需要一份独立文档来定义它的最小稳定 contract。

原因很直接：

- `dimension` 与 compiler 都已经直接引用 `enum_set_ref` / `enum_version`
- 值域本体、值项结构、发布版本、消费边界需要被单独说明
- 若只在 `dimension` 文档里零散补充，会让“维度轴语义”和“值域治理语义”重新混在一起

### 2. 不需要提升成新的顶层 semantic object

`Enum Value Set` 不应被写成与 `entity` / `dimension` / `time` 同级的核心 semantic object。

它更准确的定位是：

- `dimension` 的受治理值域配套 contract
- 可被多个 `dimension` 复用的值域定义
- 独立成文档，但不独立承担分析轴语义

换句话说：

> `dimension` 回答“按什么轴分析”，`enum set` 回答“这个轴在 enumerated domain 下允许哪些受治理值”。

## 设计目标

该 contract 应同时满足：

- **边界清晰**：值域本体、消费策略、物理映射、编译校验分层
- **引用稳定**：`enum_set_ref` 与 `enum_version` 能作为稳定引用面被 `dimension` / compiler 消费
- **版本明确**：版本锚定的是“已发布值集快照”，而不是单个值实例
- **易于复用**：同一值域可以被多个维度引用，而不需要复制值列表
- **容易理解**：读者能快速判断什么该放进 enum set，什么不该放

## 非目标

本文明确不追求：

- 把 `Enum Value Set` 设计成新的顶层分析对象
- 用它取代 `dimension`
- 在主 contract 中承载 null/tail、兼容级别、灰度策略、迁移策略
- 在主 contract 中表达物理列名、SQL case when、adapter 私有映射逻辑
- 把 UI 展示配置、国际化文案系统、搜索排序策略塞进主 schema

## 核心设计结论

### 1. `enum_set_ref` 指向的是“受治理值域”，不是 dimension 本体

`enum_set_ref` 应指向一个稳定命名的值域定义，例如：

- `enum.iso_country_code`
- `enum.experiment_variant`
- `enum.user_lifecycle_state`

它回答的是：

- 这个受治理值域的稳定标识是什么
- 它允许哪些值
- 每个值的稳定 identity 是什么
- 已发布的值集版本有哪些

它不回答：

- 哪个维度在使用它
- 哪个指标支持它
- 物理字段如何编码这个值

### 2. `enum_version` 锚定的是已发布值集快照

`enum_version` 不应理解为：

- dimension 自己的 schema version
- 某个单值的 revision
- request-time 的临时过滤版本

它应理解为：

- 某个 `enum_set_ref` 下的一次已发布值集快照
- 一个可被 `dimension` pin 住的稳定版本
- compiler 可以显式校验存在性的版本锚点

例如：

- `enum_set_ref = "enum.iso_country_code"`
- `enum_version = "2026-01"`

表示该维度消费的是 `enum.iso_country_code` 在 `2026-01` 发布快照中的值域。

### 3. enum set 只定义“允许值域”，不定义消费策略

主 contract 中应保留：

- 稳定值域标识
- 值类型
- 值项集合
- 值项稳定 identity
- 发布版本

主 contract 中不应默认保留：

- null 如何处理
- tail bucket 如何生成
- 旧值是否可向前兼容
- 废弃值在不同请求模式下是否允许继续消费
- 不同下游对象的兼容矩阵

这些内容若需要强约束，应进入 governance context、compiler policy 或 catalog metadata。

### 4. 值的稳定 identity 优先于展示文案

每个枚举值至少应有一个稳定 identity。

推荐区分：

- `value_key`：稳定语义键，面向治理与引用
- `raw_value`：真实枚举值或编码，面向数据绑定与编译校验
- `label`：默认展示文案，面向阅读体验

其中：

- `value_key` 不能被展示文案替代
- `label` 可以变，但不应改变值的 identity
- `alias` 若存在，只是辅助匹配，不应成为主引用面

## 推荐 ref taxonomy

建议使用 `enum.*` 作为稳定命名空间，例如：

- `enum.iso_country_code`
- `enum.experiment_variant`
- `enum.user_lifecycle_state`

该前缀表示：

- 这是一个受治理值域定义
- 它可以被 `dimension` 的 `enum_set_ref` 引用
- 它不是 `dimension.*` 本体，也不是 `binding.*` 映射锚点

## 核心 schema

### EnumSetHeader

```python
from typing import Literal, TypedDict


class EnumSetHeader(TypedDict):
    enum_set_ref: str
    value_type: Literal["string", "integer", "number", "boolean"]
```

字段含义：

- `enum_set_ref`：受治理值域的稳定标识
- `value_type`：该值域中 `raw_value` 的类型

说明：

- 这里保持最小化，只定义受治理值域本体
- 不在 header 中直接引入发布、兼容、物理映射等异质信息

### EnumValueSpec

```python
from typing import NotRequired, TypedDict


class EnumValueSpec(TypedDict):
    value_key: str
    raw_value: str | int | float | bool
    label: str
    aliases: NotRequired[list[str]]
```

字段含义：

- `value_key`：值的稳定语义键
- `raw_value`：该值在物理数据或规范值域中的真实值
- `label`：默认展示名称
- `aliases`：可选别名，用于辅助迁移或检索

说明：

- `aliases` 是辅助信息，不应成为主 identity
- `value_key` 与 `raw_value` 应在一个已发布版本内保持唯一

### EnumSetVersionSpec

```python
from typing import TypedDict


class EnumSetVersionSpec(TypedDict):
    enum_version: str
    values: list[EnumValueSpec]
```

字段含义：

- `enum_version`：值集发布快照的稳定版本号
- `values`：该版本下允许的值集合

说明：

- 版本是值集快照，而不是单值 revision
- 某个版本中的值项集合应是可发布、可校验、可复用的稳定视图

### EnumSetSchemaContract

```python
from typing import TypedDict


class EnumSetSchemaContract(TypedDict):
    header: EnumSetHeader
    versions: list[EnumSetVersionSpec]
```

字段含义：

- `header`：值域本体定义
- `versions`：该值域的已发布版本集合

## 必要约束

建议至少满足以下约束：

1. `enum_set_ref` 在 catalog 中全局稳定且唯一
2. `enum_version` 在同一 `enum_set_ref` 下唯一
3. `values[*].value_key` 在同一版本内唯一
4. `values[*].raw_value` 在同一版本内唯一
5. consuming dimension 的 `value_type` 应与 enum set 的 `value_type` 一致

## 与其他对象的边界

### 与 dimension 的边界

`dimension` 回答：

- 这个分析轴是什么
- 它的结构语义是什么
- 它的值类型是什么
- 它是否使用 open domain 还是 enumerated domain

`enum set` 回答：

- 当 `domain_kind = "enumerated"` 时，允许哪些受治理值
- 这些值在某个发布版本中是什么快照

因此：

- `dimension` 不应内嵌完整值列表作为主 contract 常态
- `enum set` 也不应反过来定义维度层级、grouping、semantic_role

### 与 compiler 的边界

compiler 至少应能利用该 contract 做：

- `enum_set_ref` 是否存在的校验
- `enum_version` 是否存在的校验
- consuming dimension 的 `value_type` 是否与值域一致的校验
- normalized dimension contract 中 `enum_set_ref` / `enum_version` 的稳定抽取

但 compiler policy 若要表达：

- 向前兼容
- 废弃值是否允许
- null / tail 行为
- 自动升级到最新版本

这些规则不应默认回写到 enum set 主 schema。

### 与 governance 的边界

governance / policy 更适合承载：

- 废弃策略
- 迁移策略
- 兼容级别
- 发布审批状态
- 下游消费限制

也就是说：

> enum set 负责定义“值域是什么”，governance 负责定义“这个值域在组织内如何演进和被消费”。

### 与 binding 的边界

binding 回答的是：

- 某个物理字段如何映射到枚举值
- 原始代码值是否需要清洗或归一化
- 多个源系统如何对齐到同一受治理值域

因此：

- 物理字段名
- SQL/adapter 私有转换
- 源系统代码表 join 规则

都不应进入 enum set 主 contract。

## 示例

### 示例 1：国家码值域

```json
{
  "header": {
    "enum_set_ref": "enum.iso_country_code",
    "value_type": "string"
  },
  "versions": [
    {
      "enum_version": "2026-01",
      "values": [
        {"value_key": "CN", "raw_value": "CN", "label": "China"},
        {"value_key": "US", "raw_value": "US", "label": "United States"},
        {"value_key": "JP", "raw_value": "JP", "label": "Japan"}
      ]
    }
  ]
}
```

对应 consuming dimension 只需要声明：

```json
{
  "value_type": "string",
  "domain_kind": "enumerated",
  "enum_set_ref": "enum.iso_country_code",
  "enum_version": "2026-01"
}
```

### 示例 2：实验变体值域

```json
{
  "header": {
    "enum_set_ref": "enum.experiment_variant",
    "value_type": "string"
  },
  "versions": [
    {
      "enum_version": "v1",
      "values": [
        {"value_key": "control", "raw_value": "control", "label": "Control"},
        {"value_key": "treatment_a", "raw_value": "treatment_a", "label": "Treatment A"},
        {"value_key": "treatment_b", "raw_value": "treatment_b", "label": "Treatment B"}
      ]
    }
  ]
}
```

### 示例 3：生命周期状态值域

```json
{
  "header": {
    "enum_set_ref": "enum.user_lifecycle_state",
    "value_type": "string"
  },
  "versions": [
    {
      "enum_version": "v2",
      "values": [
        {"value_key": "new", "raw_value": "new", "label": "New"},
        {"value_key": "active", "raw_value": "active", "label": "Active"},
        {"value_key": "churned", "raw_value": "churned", "label": "Churned"}
      ]
    }
  ]
}
```

## 反例

以下内容不建议写进 enum set 主 contract：

### 1. 把 null / tail 策略直接塞进值域定义

例如：

- `unknown_bucket_label`
- `tail_handling`
- `null_as_other`

这些更像消费策略，而不是值域本体。

### 2. 把物理映射细节写进值域 contract

例如：

- `source_column_name`
- `sql_expression`
- `join_sql`

这些属于 binding。

### 3. 把维度语义重新写进 enum set

例如：

- `semantic_role`
- `supports_grouping`
- `hierarchy_type`

这些属于 `dimension`，不属于值域定义。

## 最小心智模型

如果只记住一句话，可以记住：

> `dimension` 定义分析轴，`enum set` 定义该轴在 enumerated domain 下可引用的受治理值域；它需要独立文档边界，但不是新的顶层 semantic object。
