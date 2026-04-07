# Semantic Layer Time Schema Contract（草案）

本文定义 Factum semantic layer 中 `time.*` 语义引用的目标 contract。

本文是**语义契约设计文档**，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/entity-schema-contract.zh.md`
- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/typed-binding-contract.zh.md`
- `docs/semantic/compiler-spec.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`

本文重点回答：

- `time.*` 如何成为 entity / metric / process / dimension / binding 的共享一等语义引用
- 哪些时间语义属于 catalog 对象，哪些属于 request-time `time_scope`
- `anchor_time_ref`、`analysis_window.anchor_ref`、`observation_window.anchor_ref` 应如何协同
- compiler / IR 应如何保留多时间组合下的解析结果

## Purpose

当前 `docs/semantic/` 中已经广泛使用：

- `entity.primary_time_ref`
- `metric.primary_time_ref`
- `process.interface_contract.anchor_time_ref`
- `dimension.required_time_anchor_ref`
- binding 中的 `event_time` / `partition_time` / `anchor_time`

但目录中还没有一份独立文档统一回答：

- `time.exposure_time` 与 `time.partition_time` 是否属于同一命名空间
- `time_scope` 与 catalog 中稳定 `time.*` 的边界是什么
- process anchor、window anchor、metric 主时间之间的关系是什么
- compiler / IR 在多时间场景下该保留哪些解析结果

本文的目标就是补上这层“共享时间语义 contract”，避免时间语义在各对象中各写一套。

## 设计目标

该 contract 应同时满足：

- **唯一标识**：`time_ref` 是唯一稳定标识，不再引入平行命名字段
- **严格分层**：时间语义、窗口本体、请求时间范围、物理消费策略、编译解析结果必须分层
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

### 1. `time.*` 必须是共享的一等语义引用

`time.*` 应像 `entity.*`、`dimension.*`、`subject.*` 一样，被当作稳定 semantic refs 对待。

它回答的是：

- 这是什么时间语义
- 它是否是业务分析时间、measurement 时间或运营支撑时间
- 它能否作为 catalog 对象之间共享的稳定时间引用

它不回答：

- 它落在哪个物理字段
- 它最终如何被某个引擎执行
- 它在某个 binding 中采用了什么 lateness / freshness / incomplete-window 策略

### 2. 时间语义必须拆成五层

应严格区分以下五类概念：

1. **时间语义引用**
   - 例如 `time.assignment_time`、`time.exposure_time`、`time.conversion_time`
2. **窗口本体**
   - 例如 `analysis_window`、`observation_window`
3. **请求时间范围**
   - 即 request-time `time_scope`
4. **物理消费策略**
   - 例如 `late_arrival_policy`、`incomplete_window_policy`、`freshness_policy_ref`
5. **编译后的解析结果**
   - 例如 `metric_primary_time_ref`、`process_anchor_time_ref`、`window_anchor_refs`、`resolved_filter_time_ref`

这五层若不拆开，时间语义很容易在 metric / process / binding / compiler 之间重复定义。

### 3. process 拥有窗口本体，metric 只拥有 measurement 主时间

稳定规则应为：

- **entity**：只声明默认业务时间语义
- **metric**：只声明 measurement 的 `primary_time_ref`
- **process**：声明 `anchor_time_ref`，并拥有 `analysis_window` / `observation_window`
- **binding**：声明时间字段如何落地，以及窗口如何被物理消费
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

### 5. 物理时间消费策略属于 binding，不属于 `time.*`

以下内容属于 binding：

- `processing_time`
- `partition_time`
- `freshness_policy_ref`
- `late_arrival_policy`
- `incomplete_window_policy`

它们回答的是“底层如何消费既有时间语义”，而不是“分析窗口本身是什么”。

`time.partition_time`、`time.processing_time` 可以作为 `time.*` 命名空间中的稳定 ref 存在，但它们的物理消费能力不在时间 contract 中枚举，而在 binding contract 中声明。

例如 `time.partition_time` 可以被 binding 映射为：

- 单个物理字段，如 `partition_ts`
- 复合分区键，如 `log_date` + `log_hour`

但无论物理形态如何，`time.*` contract 只负责声明“这是一种受治理的分区时间语义”；具体如何由多个字段还原出稳定 bucket 边界，应由 binding contract 显式表达。

## 时间语义分层总表

| 层 | 主要对象 | 应回答什么 | 不应回答什么 |
| --- | --- | --- | --- |
| 时间语义引用 | `time.*` contract | 这个时间语义是什么、属于哪些分析角色 | 物理列名、SQL 表达式 |
| 对象层 | entity / metric / process / dimension | 哪些对象消费哪些 `time.*` refs | 具体时间列如何实现 |
| 请求层 | `time_scope` | 本次请求观察哪个时间范围 | 重新发明 assignment / exposure / conversion 语义 |
| 绑定层 | typed binding | 时间语义如何映射到物理字段与消费策略 | 窗口本体定义 |
| 编译层 | compiler / IR | 解析后的时间锚点、窗口锚点、过滤目标 | engine-specific 时间实现 |

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
    time_ref: str
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
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
| `time_ref` | string | yes | 稳定时间语义引用；必须使用 `time.*`；也是唯一标识 |
| `display_name` | string | no | 面向人类的显示名称 |
| `description` | string | no | 对该时间语义的解释 |
| `semantic_roles` | array[string] | yes | 时间语义扮演的角色；允许多值，不再强行单选 |
| `time_contract_version` | string | yes | 契约版本 |

### 约束

- `time_ref` 必须是唯一主键；不再引入平行 `name`
- `semantic_roles` 必须非空
- `operational_support` 在 v1 中不能与 `business_anchor` / `measurement` 混用
- `time.*` contract 不声明 `freshness_anchor`、`partition_anchor` 之类 binding 侧能力
- `time.partition_time` 表示受治理 partition bucket 对应的时间语义，不保证等于原始事件发生时刻
- 同一个 `time.partition_time` 可以被不同 binding 落地为单字段分区时间列，或 `log_date` / `log_hour` 这类复合分区键

本文件是目录中关于时间分层的**唯一规范来源**；其他 semantic 文档只应引用这里的分层结论，而不重复发明局部时间 taxonomy。

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
3. 若 binding 使用 `log_date` + `log_hour`，其语义应解释为某个**小时分区 bucket 的受治理时间边界**
4. 粒度、时区、边界解释等物理归一化信息属于 binding contract，而不是 `time.*` contract 自身

## 与其他对象的关系

### 与 entity 的关系

entity 只应通过 `primary_time_ref` 声明“这个实体默认围绕哪个时间语义组织”。

例如：

- `entity.user.primary_time_ref = time.user_created_at`
- `entity.session.primary_time_ref = time.session_start_time`

entity 不负责表达：

- attribution window
- baseline policy
- partition freshness

在语义上，entity 默认应引用**非 operational** 的 `time.*`。

### 与 metric 的关系

metric 通过 `primary_time_ref` 声明 measurement 默认使用哪个时间语义。

它不应直接声明：

- `attribution_basis = exposure | assignment`
- `attribution_window = 7d`

这些属于 process object 或 compiler policy。

在语义上，metric 的 `primary_time_ref` 应引用带有 `measurement` 角色的 `time.*`。

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

### 与 typed binding contract 的关系

binding 负责把：

- `primary_time_ref`
- `anchor_time_ref`
- `analysis_window.anchor_ref`
- `observation_window.anchor_ref`
- `time.partition_time`
- `time.processing_time`

这些语义时间映射到底层字段。

同时它还负责：

- lateness / freshness / incomplete-window 的物理消费策略
- 结构化 temporal constraints

但 binding 不重新定义窗口本体。

即便 `anchor_time_ref` 与 `analysis_window.anchor_ref` 恰好指向同一个 `time.*`，binding 也应把它们视为不同 contract target，因为它们在语义上分别服务“主锚点”和“窗口消费锚点”。

### 与 compiler / IR 的关系

compiler 应至少完成以下事情：

1. 解析 metric / process 暴露的全部时间 refs
2. 判断 request `time_scope` 最终作用于哪个过滤目标时间
3. 校验 `time_derived` dimension 的 `required_time_anchor_ref`
4. 校验 process comparison / baseline projection 的时间前提
5. 将解析结果写入 normalized inputs 与 IR

IR 则应保留：

- metric 的 `primary_time_ref`
- process 的 `anchor_time_ref`
- 各窗口的 `window_anchor_refs`
- request 的 `resolved_filter_time_ref`
- normalized temporal constraints / time resolution 结果

## 多时间解析规则

### 1. compiler 必须保留多条时间线，而不是只保留一个“解析后锚点”

在以下场景中，多条时间线是正常的：

- process 用 `time.exposure_time` 作为 `anchor_time_ref`
- `analysis_window` 仍围绕 `time.exposure_time`
- metric 用 `time.conversion_time` 作为 `primary_time_ref`

此时 compiler 不应把它们压扁成一个模糊的“单一解析后时间锚点”，而应至少保留：

- `metric_primary_time_ref`
- `process_anchor_time_ref`
- `window_anchor_refs`
- `resolved_filter_time_ref`

### 2. `time_scope` 只过滤一个目标时间 ref

request `time_scope` 是一个请求入口，但在一次已归一化的组合里，它必须落到**单一过滤目标**。

推荐的 v1 默认解析优先级：

1. 若 process 提供 `analysis_window.anchor_ref`，默认过滤它
2. 否则若 process 提供 `interface_contract.anchor_time_ref`，过滤它
3. 否则若 metric 提供 `primary_time_ref`，过滤它
4. 否则编译失败

这个结果应写入 `resolved_filter_time_ref`，而不是覆盖 metric 或 process 各自原有的时间引用。

### 3. operational support time 不能充当分析锚点

`time.partition_time`、`time.processing_time` 这类 `operational_support` 时间：

- 可以被 binding / freshness / lateness 策略引用
- 可以进入治理、时效或回填相关校验
- 可以由 coarse partition key 或 composite partition key（如 `log_date` / `log_hour`）落地
- 不能满足 `metric.primary_time_ref`
- 不能满足 `process.anchor_time_ref`
- 不能满足 `analysis_window.anchor_ref` / `observation_window.anchor_ref`

若需要例外，必须由未来版本显式引入新规则，而不是在 v1 中保留“无约束例外”。

## Compiler / Validator 建议

建议至少做以下校验：

1. **time ref existence**
   - 所有 `time.*` 引用都必须能在时间 contract 中解析
2. **role compatibility**
   - `metric.primary_time_ref` 必须引用带有 `measurement` 角色的 `time.*`
   - process 的主锚点与窗口锚点必须引用非 `operational_support` 的 `time.*`
3. **window inheritance**
   - 若 `analysis_window.anchor_ref` / `observation_window.anchor_ref` 省略，则必须显式继承 `anchor_time_ref`
   - 若显式 override，则 override ref 必须独立可解析
4. **dimension compatibility**
   - `time_derived` dimension 的 `required_time_anchor_ref` 必须被 metric / process 组合满足
5. **request-time resolution**
   - `time_scope` 必须作用于已解析出的 `resolved_filter_time_ref`
6. **binding coverage**
   - 所有被消费的时间语义都必须能由 binding 稳定映射到物理层
7. **partition-time normalization**
   - 若 binding 用复合分区键承载 `time.partition_time`，必须显式声明粒度、时区与 bucket 边界解释
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

在该场景下：

- experiment process 用 `time.exposure_time` 作为 `anchor_time_ref`
- `analysis_window` 默认也围绕它定义 `0d -> 7d`
- conversion metric 仍可使用自己的 `primary_time_ref = time.conversion_time`
- binding 再声明 `time.exposure_time` 在物理层如何落地

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
- conversion metric 的 `primary_time_ref`

### 示例 3：partition freshness

```json
{
  "time_ref": "time.partition_time",
  "semantic_roles": ["operational_support"],
  "time_contract_version": "time.v1"
}
```

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

在该场景下：

- source 物理上可能只有 `log_date` 与 `log_hour`
- binding 可把它们组合解释为 hour-level `time.partition_time`
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

- entity 只保留 `primary_time_ref`
- metric 只保留 measurement `primary_time_ref`
- process 统一承接窗口与过程锚点
- binding 统一承接 lateness / freshness / incomplete-window

### 第四阶段：补齐 compiler / IR 的时间解析结果

至少补齐：

- normalized metric `primary_time_ref`
- normalized process `anchor_time_ref`
- normalized `window_anchor_refs`
- `resolved_filter_time_ref`
- normalized temporal constraints

## 总结

时间语义在 Factum 中不应继续被当作零散字段或 capability 矩阵，而应收敛为：

> `time.*` 定义“这是哪一种稳定时间语义”，entity / metric / process / dimension 消费这些语义，binding 负责把它们落地并补充消费策略，而 compiler / IR 负责把一次请求解析成可校验、可执行的多时间关系。
