# Semantic Layer Time Schema Contract（草案）

> **概念迁移说明**：本节描述的 Time 概念已演进为 OSI Field 的 `dimension.is_time` 属性，不再作为独立对象存在。
> 在 dataset-native 模型中，时间语义由 Field.dimension.is_time 直接表达；物理时间列映射由 Field.expression 内联完成。
> 原独立 `time.*` 命名空间对应为 Field 的 dimension.is_time 属性；原 entity binding 中的时间落地（time_surfaces / TimeBindingSpec）已删除。
> 原 typed-binding-contract.zh.md 已废弃。

本文定义 Marivo semantic layer 中 `time.*` 语义引用的目标 contract。

本文是**语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `specs/semantic/entity-schema-contract.zh.md`
- `specs/semantic/dimension-schema-contract.zh.md`
- `specs/semantic/metric-v2-schema.zh.md`
- `specs/semantic/process-object-schema.zh.md`
- ~~`specs/semantic/typed-binding-contract.zh.md`~~（已废弃）
- `specs/semantic/compiler-spec.zh.md`
- `specs/semantic/ir-schema-contract.zh.md`

本文重点回答：

- ~~`time.*` 如何成为 entity / metric / process / dimension 的共享一等语义引用，并由 entity binding 落地到物理时间字段~~（时间语义现由 Field.dimension.is_time 属性表达；物理时间列映射由 Field.expression 内联完成）
- 哪些时间语义属于 catalog 对象，哪些属于 request-time `time_scope`
- `anchor_time_ref`、`analysis_window.anchor_ref`、`observation_window.anchor_ref` 应如何协同
- compiler / IR 应如何保留多时间组合下的解析结果

## Purpose

当前 `specs/semantic/` 中已经广泛使用：

- ~~`entity.primary_time_ref`~~（已移除；时间语义现由 Field.dimension.is_time 属性表达）
- ~~`metric.primary_time_ref`~~（已移除；metric 不再声明 `primary_time_ref`，时间语义由 AOI `time_scope.field` 在请求时解析）
- `process.interface_contract.anchor_time_ref`
- `dimension.required_time_anchor_ref`
- ~~entity binding 中的 `event_time` / `partition_time` / `anchor_time`~~（binding 概念已删除；时间落地由 Field.expression 内联完成）

但目录中还没有一份独立文档统一回答：

- `time.exposure_time` 与 `time.partition_time` 是否属于同一命名空间
- `time_scope` 与 catalog 中稳定 `time.*` 的边界是什么
- process anchor、window anchor、metric 主时间之间的关系是什么
- compiler / IR 在多时间场景下该保留哪些解析结果

本文的目标就是补上这层“共享时间语义 contract”，避免时间语义在各对象中各写一套。

## 设计目标

该 contract 应同时满足：

- **唯一标识**：`time_ref` 是唯一稳定标识，不再引入平行命名字段（现由 Field.dimension.is_time 属性表达）
- **严格分层**：时间语义、窗口本体、请求时间范围、~~entity field grounding~~Field 物理映射、编译解析结果必须分层
- **多角色表达**：同一个时间语义可以同时承担多个分析角色，而不是被迫落入互斥 taxonomy
- **可校验**：compiler 能显式判断对象组合中的时间语义是否闭合
- **执行解耦**：不退化为 SQL 时间表达式或 engine-specific 时间函数集合

## 非目标

本文明确不追求：

- 把 `time.*` 设计成外部查询语言
- 把所有窗口表达都塞进 `metric`
- 让 binding 重新定义窗口本体
- 在公共 contract 中暴露时间列 SQL、时间截断函数或 adapter 私有逻辑
- 用一组对象专属 capability 矩阵替代引用方 schema 自身的约束

## 核心设计结论

### 1. `time.*` 必须是共享的一等语义引用（现由 Field.dimension.is_time 属性表达）

`time.*` 应像 `entity.*`、`dimension.*`、`subject.*` 一样，被当作稳定 semantic refs 对待。

在 dataset-native 模型中，时间语义由 Field.dimension.is_time 属性直接表达，不再需要独立 `time.*` 命名空间。

它回答的是：

- 这是什么时间语义
- 它是否是业务分析时间、measurement 时间或运营支撑时间
- 它能否作为 catalog 对象之间共享的稳定时间引用

它不回答：

- 它落在哪个物理字段
- 它最终如何被某个引擎执行
- ~~它在某个 binding 中采用了什么 lateness / freshness / incomplete-window 策略~~（binding 概念已删除；物理时间消费策略由 Dataset 与 compiler 表达）

### 2. 时间语义必须拆成三层

应严格区分以下三类概念：

1. **时间语义引用**
   - 例如 `time.assignment_time`、`time.exposure_time`、`time.conversion_time`
   - 在 dataset-native 模型中，由 Field.dimension.is_time 属性表达
2. **窗口本体**
   - 例如 `analysis_window`、`observation_window`
   - 由 process / metric 对象定义，包括 request `time_scope`
3. ~~**entity field grounding / 物理消费策略**~~（binding 概念已删除）
   - ~~例如 `late_arrival_policy`、`incomplete_window_policy`、`freshness_policy_ref`~~
   - ~~由 entity binding 与 compiler 表达~~
   - 在 dataset-native 模型中，物理时间列映射由 Field.expression 内联完成；消费策略由 Dataset 与 compiler 表达

这三层若不拆开，时间语义很容易在 metric / process / Dataset 之间重复定义。

**删除了原设计的"编译后的解析结果"层。**

原因：
- IR 不需要创建新的时间解析结果对象
- IR 直接引用 `time.*` refs
- Compiler 负责校验和组合，不输出额外的时间解析快照

### 3. process 拥有窗口本体，metric 不再声明 `primary_time_ref`

稳定规则应为：

- **entity**：~~只声明默认业务时间语义~~（时间语义现由 Field.dimension.is_time 属性表达）
- **metric**：~~声明 measurement 的 `primary_time_ref`~~（已移除；时间语义由 AOI `time_scope.field` 在请求时解析）
- **process**：声明 `anchor_time_ref`，并拥有 `analysis_window` / `observation_window`
- ~~**entity binding**：声明 entity 时间字段如何落地；compiler 基于这些字段解析窗口如何被物理消费~~（binding 概念已删除；时间字段落地由 Field.expression 内联完成；compiler 基于 Field 解析窗口如何被物理消费）
- **compiler / IR**：声明本次请求最终解析出的时间关系

因此：

- `attribution_basis = assignment | exposure`
- `attribution_window = 7d`
- `baseline_alignment`

这类过程型时间语义，不应回流到 metric 本体。

### 4. `time_scope` 是请求入口，不是时间 taxonomy

`time_scope` 仍然是请求层唯一时间入口，但它回答的是：

- 本次分析希望观察哪个时间范围
- 是否需要 time series / baseline shift / rolling window

它不负责重新定义：

- 哪个 `time.*` 是本次对象组合的合法锚点
- assignment / exposure / conversion 之间的语义差别

这些应先由 catalog 对象声明，再由 compiler 解析。

### 5. 物理时间消费策略属于 Dataset / compiler，不属于 `time.*`

~~以下内容属于 entity binding / compiler：~~

以下内容属于 Dataset / compiler：

- `processing_time`
- `partition_time`
- `freshness_policy_ref`
- `late_arrival_policy`
- `incomplete_window_policy`

它们回答的是“底层如何消费既有时间语义”，而不是“分析窗口本身是什么”。

`time.partition_time`、`time.processing_time` 可以作为稳定语义存在（现由 Field.dimension.is_time 属性表达），但它们的物理消费能力不在时间 contract 中枚举，而通过 Field.expression 与 compiler policy 声明。

例如 `time.partition_time` 可以经 Field.expression 映射为：

- 单个物理字段，如 `partition_ts`
- 复合分区键，如 `log_date` + `log_hour`

但无论物理形态如何，时间 contract 只负责声明”这是一种受治理的分区时间语义”；具体如何由 Field 还原出稳定 bucket 边界，应由 Dataset / compiler 显式表达。

## 时间语义分层总表

| 层 | 主要对象 | 应回答什么 | 不应回答什么 |
| --- | --- | --- | --- |
| 时间语义引用 | `time.*` contract（现由 Field.dimension.is_time 承接） | 这个时间语义是什么、属于哪些分析角色 | 物理列名、SQL 表达式 |
| 窗口本体 | process / metric | 窗口如何定义、时间范围如何确定 | 具体时间列如何实现 |
| 消费策略 | Dataset / compiler | Field 如何映射到物理字段、迟到/新鲜度/不完整窗口策略 | 窗口本体定义 |

**简化说明：**

原设计的5层模型过于复杂：
1. ~~时间语义引用~~ → **保留**
2. ~~窗口本体~~ → **保留**（合并到 process/metric 对象中）
3. ~~请求时间范围~~ → 合并到窗口本体（request `time_scope` 是窗口参数）
4. ~~物理消费策略~~ → **保留**（在 Dataset / compiler 中）
5. ~~编译后的解析结果~~ → 删除（IR 直接引用 time ref，不创建新的解析层）

Compiler 不再输出 `TimeResolutionSnapshot`，而是直接引用 `time.*` refs。

## 命名空间与角色模型

`time.*` 应保留统一命名空间，但不再强行要求每个 ref 落入单一互斥类别。

更稳定的做法是使用**可组合语义角色**：

- `business_anchor`
  - 该时间可作为业务分析锚点，例如注册、分配、曝光、转化、会话开始
- `measurement`
  - 该时间可作为 measurement 主时间，例如 activity、payment、score、state evaluation
- `operational_support`
  - 该时间只服务 freshness、partition、processing、snapshot 等运维消费

同一个 `time.*` 可以同时拥有多个角色。例如：

- `time.exposure_time`：通常是 `business_anchor`
- `time.conversion_time`：通常同时是 `business_anchor` 与 `measurement`
- `time.partition_time`：通常是 `operational_support`

这里的重点不是把名字穷举完，而是明确：

- **角色可以组合**
- **运营支撑时间不应自动升级为分析锚点**
- **对象能否消费某个 `time.*`，由引用方 contract 与 compiler 共同判断，而不是由时间对象额外暴露一套 capability 矩阵**

## 通用 Schema

### 公共头部

```python
from typing import Literal, NotRequired, TypedDict


class TimeSemanticHeader(TypedDict):
    time_ref: str  # 现由 Field.dimension.is_time 属性承接
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
    # source_field_ref: 已删除 — Field 已内联于 Dataset，无需跨对象字段引用
    semantic_roles: list[
        Literal[
            "business_anchor",
            "measurement",
            "operational_support",
        ]
    ]
    time_contract_version: str
```

`time.*` 的 public contract 只保留稳定语义字段。catalog 生命周期、版本序号、搜索别名等信息若需要保留，应进入单独的 metadata envelope，而不是时间语义主 schema。

### 字段说明

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `time_ref` | string | yes | 稳定时间语义引用；必须使用 `time.*`；现由 Field.dimension.is_time 属性承接 |
| `display_name` | string | no | 面向人类的显示名称 |
| `description` | string | no | 对该时间语义的解释 |
| ~~`source_field_ref`~~ | ~~string~~ | ~~no~~ | ~~可选的语义字段来源引用；必须指向 `entity.<entity>.field.<field>`；不是 physical column、carrier locator 或 binding target，不表达物理绑定~~（已删除；Field 已内联于 Dataset，无需跨对象字段引用） |
| `semantic_roles` | array[string] | yes | 时间语义扮演的角色；允许多值，不再强行单选 |
| `time_contract_version` | string | yes | 契约版本 |

### 约束

- `time_ref` 必须是唯一主键；不再引入平行 `name`
- `semantic_roles` 必须非空
- `time.*` contract 不声明 `freshness_anchor`、`partition_anchor` 之类 Dataset 侧能力
- ~~`source_field_ref` 只能引用 `entity.<entity>.field.<field>`，用于声明推荐字段来源；
  它不替代 entity binding，也不允许写入物理列名、source table 或 SQL 表达式~~（已删除；Field 已内联于 Dataset）
- `time.partition_time` 表示受治理 partition bucket 对应的时间语义，不保证等于原始事件发生时刻
- 同一个 `time.partition_time` 可以被不同 Dataset 通过 Field.expression 落地为单字段分区时间列，或 `log_date` / `log_hour` 这类复合分区键

**注意：角色可组合**

`semantic_roles` 允许多值组合，没有排斥约束。例如：

- `time.exposure_time` 可以同时是 `["business_anchor"]`
- `time.conversion_time` 可以同时是 `["business_anchor", "measurement"]`
- `time.partition_time` 可以是 `["operational_support"]`，也可以是 `["operational_support", "business_anchor"]`（用于运营监控场景）

删除了原设计中"operational_support 不能与其他角色混用"的限制。原因：

- 分区时间在某些场景下确实可以作为分析锚点（如运营监控）
- 角色组合提供了更大的灵活性
- 具体使用场景由 compiler 根据上下文判断

本文件是目录中关于时间分层的**唯一规范来源**；其他 semantic 文档只应引用这里的分层结论，而不重复发明局部时间 taxonomy。

**dataset-native 说明**：在 dataset-native 模型中，时间语义由 Field.dimension.is_time 属性直接表达，
不再需要独立 `time.*` 命名空间。本文描述的分层原则仍然适用，但承载方式已从独立对象演进为 Field 属性。

### `time.partition_time` 的语义边界

`time.partition_time` 需要覆盖常见的数据仓库分区形态，但仍然保持语义层与物理层分离。

它在语义上表示：

- 一个受治理的 partition bucket 对应的时间语义
- 可被 freshness、late-arrival、backfill、partition pruning 等消费侧策略引用

它不直接表示：

- 原始业务事件时间
- metric / process 可直接消费的分析锚点
- `log_date || log_hour` 之类物理拼接表达式

因此：

1. `time.partition_time` 可以由单个分区时间列承载，例如 `partition_ts`
2. 也可以由多个分区字段组合承载，例如 `log_date` + `log_hour`
3. 若 Field.expression 使用 `log_date` + `log_hour`，其语义应解释为某个**小时分区 bucket 的受治理时间边界**
4. 粒度、时区、边界解释等物理归一化信息属于 Dataset / compiler，而不是 `time.*` contract 自身

## 与其他对象的关系

### 与 entity 的关系

~~entity 只应通过 `primary_time_ref` 声明”这个实体默认围绕哪个时间语义组织”。~~

在 dataset-native 模型中，entity（即 Dataset）通过 Field.dimension.is_time 属性声明时间语义。

例如：

- ~~`entity.user.primary_time_ref = time.user_created_at`~~
- ~~`entity.session.primary_time_ref = time.session_start_time`~~
- Dataset 中某个 Field 的 `dimension.is_time = true` 声明该字段为时间字段

entity 不负责表达：

- attribution window
- baseline policy
- partition freshness

在语义上，entity 默认应引用**非 operational** 的时间语义。

### 与 metric 的关系

~~metric 通过 `primary_time_ref` 声明 measurement 默认使用哪个时间语义。~~

在当前模型中，metric 不再声明 `primary_time_ref`。时间语义由 AOI `time_scope.field` 在请求时解析，而非作为 metric 的固定属性。

metric 不应直接声明：

- `attribution_basis = exposure | assignment`
- `attribution_window = 7d`

这些属于 process object 或 compiler policy。

~~在语义上，metric 的 `primary_time_ref` 应引用带有 `measurement` 角色的 `time.*`。~~ 在当前模型中，`time_scope.field` 指向 Dataset 中带有 `dimension.is_time` 属性的 Field，由 compiler 在编译时校验其时间语义角色。

### 与 dimension 的关系

当 `dimension.semantic_kind = "time_derived"` 时，它应通过 `required_time_anchor_ref` 依赖某个 `time.*`。

例如：

- `dimension.signup_month.required_time_anchor_ref = time.user_created_at`

dimension 不负责决定这个锚点在物理层对应哪个列。

`required_time_anchor_ref` 的匹配依据是**显式 ref 相等**，而不是“某个时间对象暴露了 dimension 专用 capability”。

### 与 process object 的关系

process object 是时间窗口本体的主拥有者。

它通过以下字段消费 `time.*`：

- `interface_contract.anchor_time_ref`
- `analysis_window.anchor_ref`
- `observation_window.anchor_ref`

这三者的关系应明确为：

1. `interface_contract.anchor_time_ref` 是 process 的**主时间锚点**
2. `analysis_window.anchor_ref` / `observation_window.anchor_ref` 是**窗口级锚点**
3. 若窗口级锚点省略，则默认继承 `interface_contract.anchor_time_ref`
4. 若窗口级锚点显式给出且不同于 `interface_contract.anchor_time_ref`，则它是**窗口级 override**，不是新的 process 主锚点

例如实验分析中：

- `anchor_time_ref = time.exposure_time`
- `analysis_window.anchor_ref = time.exposure_time`

这是 process 语义，不是 metric 语义。

### ~~与 typed binding contract 的关系~~（binding 概念已删除）

~~time contract 不拥有自己的 physical binding。消费方需要具体字段时，应引用
`entity.<entity>.field.<field>` 或对象自身的 `time.*` ref；字段的物理落地由 entity
binding 负责。~~

在 dataset-native 模型中，时间字段的物理落地由 Field.expression 内联完成，不再需要独立 binding。

~~entity binding / compiler 负责把：~~

Dataset / compiler 负责把：

- ~~`primary_time_ref`~~（时间语义由 Field.dimension.is_time 属性表达）
- `anchor_time_ref`
- `analysis_window.anchor_ref`
- `observation_window.anchor_ref`
- `time.partition_time`
- `time.processing_time`

这些语义时间映射到底层字段。

同时它还负责：

- lateness / freshness / incomplete-window 的物理消费策略
- 结构化 temporal constraints

但 Dataset 不重新定义窗口本体。

即便 `anchor_time_ref` 与 `analysis_window.anchor_ref` 恰好指向同一个时间语义，
compiler 也应把它们视为不同消费 target，因为它们在语义上分别服务”主锚点”和”窗口消费锚点”。

### 与 compiler / IR 的关系

compiler 应至少完成以下事情：

1. 解析 metric / process 暴露的全部时间 refs
2. 判断 request `time_scope` 最终作用于哪个过滤目标时间
3. 校验 `time_derived` dimension 的 `required_time_anchor_ref`
4. 校验 process comparison / baseline projection 的时间前提
5. 将解析结果写入 normalized inputs 与 IR

IR 则应保留：

- ~~metric 的 `primary_time_ref`~~（已移除；时间语义由 AOI `time_scope.field` 解析）
- process 的 `anchor_time_ref`
- 各窗口的 `window_anchor_refs`
- request 的 `resolved_filter_time_ref`
- normalized temporal constraints / time resolution 结果

## 多时间解析规则

### 1. compiler 必须保留多条时间线，而不是只保留一个“解析后锚点”

在以下场景中，多条时间线是正常的：

- process 用 `time.exposure_time` 作为 `anchor_time_ref`
- `analysis_window` 仍围绕 `time.exposure_time`
- ~~metric 用 `time.conversion_time` 作为 `primary_time_ref`~~（metric 不再声明 `primary_time_ref`；时间语义由 AOI `time_scope.field` 解析）

此时 compiler 不应把它们压扁成一个模糊的”单一解析后时间锚点”，而应至少保留：

- ~~`metric_primary_time_ref`~~（已移除；由 `time_scope.field` 替代）
- `process_anchor_time_ref`
- `window_anchor_refs`
- `resolved_filter_time_ref`

### 2. `time_scope` 只过滤一个目标时间 ref

request `time_scope` 是一个请求入口，但在一次已归一化的组合里，它必须落到**单一过滤目标**。

推荐的 v1 默认解析优先级：

1. 若 process 提供 `analysis_window.anchor_ref`，默认过滤它
2. 否则若 process 提供 `interface_contract.anchor_time_ref`，过滤它
3. 否则使用 AOI `time_scope.field` 指向的 Field 作为过滤目标
4. 否则编译失败

这个结果应写入 `resolved_filter_time_ref`，而不是覆盖 metric 或 process 各自原有的时间引用。

### 3. operational support time 的使用建议

`time.partition_time`、`time.processing_time` 这类 `operational_support` 时间：

- 可以被 binding / freshness / lateness 策略引用
- 可以进入治理、时效或回填相关校验
- 可以由 coarse partition key 或 composite partition key（如 `log_date` / `log_hour`）落地
- **在特定场景下也可以作为分析锚点**（如运营监控、分区时效分析）

Compiler 根据具体使用场景判断 `operational_support` 时间是否适合作为 metric/process 的主时间锚点。不再有硬性限制。

## Compiler / Validator 建议

建议至少做以下校验：

1. **time ref existence**
   - 所有 `time.*` 引用都必须能在时间 contract 中解析
2. **role compatibility**
   - ~~`metric.primary_time_ref` 必须引用带有 `measurement` 角色的 `time.*`~~（metric 不再声明 `primary_time_ref`；时间语义由 AOI `time_scope.field` 解析，compiler 校验 Field 的 `dimension.is_time` 属性与时间语义角色）
   - process 的主锚点与窗口锚点必须引用非 `operational_support` 的 `time.*`
3. **window inheritance**
   - 若 `analysis_window.anchor_ref` / `observation_window.anchor_ref` 省略，则必须显式继承 `anchor_time_ref`
   - 若显式 override，则 override ref 必须独立可解析
4. **dimension compatibility**
   - `time_derived` dimension 的 `required_time_anchor_ref` 必须被 metric / process 组合满足
5. **request-time resolution**
   - `time_scope` 必须作用于已解析出的 `resolved_filter_time_ref`
6. **binding coverage**（现由 Dataset 物理接地保证）
   - 所有被消费的时间语义都必须能由 Dataset 的 Field.expression 稳定映射到物理层
7. **partition-time normalization**
   - 若 Dataset 用复合分区键承载 `time.partition_time`，必须显式声明粒度、时区与 bucket 边界解释
   - `log_date` 可单独承载 day-level partition time；hour-level partition time 不能只给出 `log_hour`
   - `log_date` + `log_hour` 这类组合应被归一化为单一受治理 partition-time 语义，而不是暴露为两个独立分析时间

## 示例

### 示例 1：experiment process 的曝光锚点

```json
{
  "time_ref": "time.exposure_time",
  "semantic_roles": ["business_anchor"],
  "time_contract_version": "time.v1"
}
```

**注意**：原 `source_field_ref` 已移除（Field 已内联于 Dataset；物理时间列映射由 Field.expression 内联完成）。

在该场景下：

- experiment process 用 `time.exposure_time` 作为 `anchor_time_ref`
- `analysis_window` 默认也围绕它定义 `0d -> 7d`
- conversion metric 仍可使用自己的 `primary_time_ref = time.conversion_time`~~（metric 不再声明 `primary_time_ref`；时间语义由 AOI `time_scope.field` 解析）
- ~~entity binding 再声明 `time.exposure_time` 在物理层如何落地~~（Field.expression 内联完成时间列映射）

### 示例 2：既是业务锚点又是 measurement 主时间的转化时间

```json
{
  "time_ref": "time.conversion_time",
  "semantic_roles": ["business_anchor", "measurement"],
  "time_contract_version": "time.v1"
}
```

该语义可同时用于：

- attribution / funnel / lifecycle 过程中的业务锚点
- ~~conversion metric 的 `primary_time_ref`~~（metric 不再声明 `primary_time_ref`；时间语义由 AOI `time_scope.field` 解析）

### 示例 3：partition freshness

```json
{
  "time_ref": "time.partition_time",
  "semantic_roles": ["operational_support"],
  "time_contract_version": "time.v1"
}
```

**注意**：原 `source_field_ref` 已移除。

该语义可用于：

- source freshness 评估
- late-arrival / backfill 消费

但不应直接替代 `time.exposure_time`、`time.activity_time` 这类业务分析锚点。

### 示例 4：`log_date` / `log_hour` 组成的分区时间

仍然可以使用同一个稳定语义引用：

```json
{
  "time_ref": "time.partition_time",
  "semantic_roles": ["operational_support"],
  "time_contract_version": "time.v1"
}
```

**注意**：原 `source_field_ref` 已移除。

在该场景下：

- source 物理上可能只有 `log_date` 与 `log_hour`
- Field.expression 可把它们组合解释为 hour-level `time.partition_time`
- 该语义表示每个小时 partition bucket 的受治理时间边界
- 它仍然服务 partition pruning / freshness / backfill，而不是 metric 或 process 的分析锚点

## 迁移建议

### 第一阶段：建立统一 `time.*` catalog

先把已在文档中出现的时间 ref 收拢到一份中心 contract 中，例如：

- `time.user_created_at`
- `time.assignment_time`
- `time.exposure_time`
- `time.conversion_time`
- `time.activity_time`
- `time.partition_time`

### 第二阶段：去掉冗余标识与 capability 矩阵

- 删除时间对象中的平行 `name`
- 删除 `anchor_capabilities`
- 用 `time_ref + semantic_roles` 作为唯一主真相

### 第三阶段：统一各对象的时间边界

- ~~entity 只保留 `primary_time_ref`~~（时间语义现由 Field.dimension.is_time 属性表达）
- ~~metric 只保留 measurement `primary_time_ref`~~（已移除；metric 不再声明 `primary_time_ref`，时间语义由 AOI `time_scope.field` 在请求时解析）
- process 统一承接窗口与过程锚点
- ~~time object 可用 `source_field_ref` 指向 `entity.<entity>.field.<field>` 作为自身字段来源~~（Field 已内联于 Dataset；时间语义由 Field.dimension.is_time 属性直接表达）
- ~~entity binding 统一承接 physical column / expression locator；lateness / freshness / incomplete-window 由 entity binding 与 compiler policy 消费~~（binding 概念已删除；Field.expression 内联完成列映射；lateness / freshness / incomplete-window 由 Dataset 与 compiler policy 消费）

~~`time.*` 不直接声明 `physical_column`、carrier、binding target 或 source table。validate/activate
阶段必须检查 `source_field_ref` 指向 date/datetime-compatible entity field；不兼容时返回
`invalid_field_type_for_semantic_object` 类 blocker。~~

在 dataset-native 模型中，时间语义由 Field.dimension.is_time 属性直接表达；Field.expression 内联完成列映射。compiler 校验 Field 的值类型是否适合时间语义；不兼容时返回 blocker。

### 第四阶段：补齐 compiler / IR 的时间解析结果

至少补齐：

- ~~normalized metric `primary_time_ref`~~（已移除；由 `time_scope.field` 解析结果替代）
- normalized process `anchor_time_ref`
- normalized `window_anchor_refs`
- `resolved_filter_time_ref`
- normalized temporal constraints

## 总结

时间语义在 Marivo 中不应继续被当作零散字段或 capability 矩阵，而应收敛为：

> `time.*` 定义”这是哪一种稳定时间语义”（现由 Field.dimension.is_time 属性承接），~~entity / metric / process / dimension 消费这些语义，entity field 负责连接物理字段~~Dataset 通过 Field 直接承载时间语义与物理映射，~~metric~~/process 消费这些语义（metric 不再声明 `primary_time_ref`，时间语义由 AOI `time_scope.field` 在请求时解析），而 compiler / IR 负责把一次请求解析成可校验、可执行的多时间关系。
