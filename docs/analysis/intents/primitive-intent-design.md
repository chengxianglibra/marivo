# 原子分析意图设计

本文档定义 Factum 原子分析意图（primitive analysis intents）的跨意图设计规则。

状态：draft design。本文是面向规划中原子意图家族及其 typed API contract（类型化 API 契约）的设计指南，不表示文中提到的每个意图都已经实现。

## 目的

Factum 的 step layer（步骤层）不是 SQL 的薄封装，而是面向 primitive analysis intents（原子分析意图）的 typed API。

本文的目标，是在新增或调整意图 Schema 时，保证原子意图家族始终保持语义一致、边界清晰。

设计目标：

- 让步骤层始终围绕分析意图，而不是关系型语法
- 让意图输出足够稳定，便于下游步骤引用
- 让意图输出足够可压缩，适合 AI agent 消费
- 让证据抽取保持确定性，隐藏实现细节

## 原子意图集合

Factum 当前将以下能力视为原子分析意图集合：

- `observe`：在类型化观测契约下读取语义指标
- `compare`：计算两个观测之间的类型化差异
- `decompose`：将已知 delta 分配到排序后的贡献项
- `correlate`：估计两个对齐序列之间的统计关联
- `detect`：扫描指标时间范围并返回排序后的异常候选
- `test`：评估一个类型化统计假设
- `forecast`：把有界历史序列投影到未来 bucket

这些意图应当具备以下属性：

- 正交
- 可组合
- 语义类型明确
- 在有界输出下保持稳定

## 什么样的意图可以称为原子意图

一个能力只有同时满足以下条件，才应被建模为原子分析意图：

1. 它回答的是一个独立的分析问题，而不是另一个意图的展示变体。
2. 它有稳定的输入和输出类型，并且这些类型与其他原子意图有实质区别。
3. 它能映射到一个可识别的统计或分析方法族。
4. 它围绕语义指标、语义维度和观测对象建模，而不是把表、列或 SQL 形状暴露为外部契约。
5. 它的输出可以在不明显损失语义的前提下被有界压缩。

如果一个能力不满足上述条件，通常更适合建模为：

- 派生意图
- planner / template 模式
- agent 侧工作流
- 现有工件之上的投影视图

## 全局设计规则

每个原子意图契约都应遵守以下规则。

### 1. 以语义层为原生契约

公共契约应当以以下概念表达：

- semantic metrics（语义指标）
- semantic dimensions（语义维度）
- typed observations（类型化观测）
- typed step references（类型化步骤引用）

公共契约不应主要以以下内容表达：

- raw SQL
- 任意列
- 任意 join
- 用户自定义执行计划

### 2. 输入输出必须类型化

每个意图应具备：

- 一个稳定的请求类型
- 少量合法模式
- 明确的非法组合
- 明确的响应类型契约

当一个意图依赖上游结果时，应优先消费 typed references（类型化引用），而不是重新引入临时性的原始 scope 输入。

推荐示例：

- `compare(left_ref, right_ref)`，而不是 `compare(metric, scope_a, scope_b)`
- `decompose(compare_ref, dimension)`，而不是 `decompose(metric, left_scope, right_scope, ...)`
- `correlate(left_ref, right_ref)`，而不是 `correlate(metric_a, metric_b, scope, ...)`

### 3. 单一分析职责

每个原子意图必须只做一件分析工作。

示例：

- `observe` 负责读取指标
- `compare` 负责计算 delta
- `decompose` 负责解释 delta 如何被分配
- `detect` 负责找出值得进一步分析的候选点

原子意图不得悄悄吸收相邻职责，例如：

- diagnosis（诊断）
- root-cause explanation（根因解释）
- recommendation generation（建议生成）
- 宽泛的工作流编排

### 4. 证据边界必须保持确定性

Factum 的 evidence（证据）必须由代码以确定性方式抽取。

原子意图输出可以包含：

- observations（观测）
- candidates（候选）
- structured statistics（结构化统计量）
- validation metadata（校验元数据）
- provenance（溯源信息）

原子意图输出不得直接把以下内容作为核心契约：

- 不可验证的因果结论
- 以自由文本结论为主语义
- 动作建议

特别地：

- `detect` 返回的是异常候选，不是已确认异常事实
- `correlate` 返回的是 association（关联），不是 causation（因果）
- `test` 返回的是统计检验结果，不是业务结论

### 5. 校验是契约的一部分

每个原子意图 Schema 都应定义：

- 合法输入形态
- 不支持的输入形态
- 硬失败的校验条件
- 在需要时返回的软告警

校验不应只覆盖语法，还应覆盖语义是否可辩护。

示例：

- `compare` 必须拒绝不可比较的观测
- `decompose` 必须拒绝不可归因的指标或维度
- `correlate` 必须拒绝无法对齐的时间序列
- `detect` 必须拒绝过短或过于无界的扫描请求

### 6. 使用稳定默认值，而不是暴露任意调参面

原子意图契约应优先暴露稳定的语义控制项，而不是实现细节驱动的调参面。

推荐：

- `profile = "seasonal_residual"`
- `sensitivity = "balanced"`

不推荐：

- 任意 detector 算法名
- 任意 SQL 表达式
- 大量松散且缺乏约束的 knobs

这样即使实现发生演进，外部契约仍保持稳定。

## Schema 设计通用准则

原子意图的输出契约设计必须遵守 Factum 的 Canonical Schema 设计原则。

详见 [`canonical-schema-principles.md`](../foundations/canonical-schema-principles.md)。

这些原则与"全局设计规则"互补：全局规则关注意图语义，Schema 原则关注契约结构。

## 工件与投影

Factum 的意图输出有两类主要消费者：

- 下游步骤需要完整、稳定、可引用的结果
- agent 需要高信号、可压缩、可放入上下文预算的结果

因此应将 artifact（工件）与 projection（投影）建模为两层，而不是一个超载的响应形状。

### 工件层

工件是某个意图执行后的完整类型化输出。

特性：

- 对下游引用足够完整
- 对溯源与复现足够稳定
- 不以 token budget 优化为首要目标
- 可以包含 rows、diagnostics、quality metadata 等 agent 默认不需要的细节

下游步骤引用必须指向工件层，而不是压缩视图。

### 投影层

投影是从工件确定性派生出的压缩视图，服务于 agent 或 UI。

特性：

- 不重新分析，只从工件推导
- 输出有界，利于上下文效率
- 保留步骤的主语义结果
- 可以执行排序、截断、分组、摘要

投影不得：

- 重定义分析结果
- 以新语义重新计算意图
- 创造新的证据声明

### 对 API 设计的影响

原子意图契约应先定义完整工件语义。

面向 agent 的交付，再使用确定性的投影策略，例如：

- top-k 排名行
- 多序列覆盖
- 多时间窗覆盖
- 分组摘要
- 明确披露截断

这样既能保持契约清晰，也能保证系统对 agent 友好。

## 可压缩性规则

低失真的有界输出，是原子意图的一等设计要求。

如果某个意图的输出无法在压缩后仍基本保留原始含义，它就不适合作为面向 agent 的原子意图。

示例：

- `decompose` 可压缩，因为 top contribution rows 仍能表达谁在驱动变化
- `detect` 可压缩，因为 top anomaly candidates 仍能表达主要异常点在哪里
- 泛化的 “describe everything” 不适合做原子意图，因为没有单一稳定的压缩规则

### 压缩原则

压缩应当：

- 确定性
- 与步骤语义对齐
- 显式披露截断
- 在重复执行下保持稳定

压缩不应当：

- 退化成任意 UI 格式化
- 隐式丢行
- 因消费端展示逻辑导致分析语义漂移

### 执行边界与展示边界

并非所有“有界输出控制项”都属于同一层。

当一个参数会改变分析对象本身时，它属于执行边界，应进入意图契约。

示例：

- `detect` 中的 `max_series` 会改变实际扫描哪些序列
- `correlate` 中的 `min_pairs` 会改变关联估计是否合法

而以下内容通常更适合放在投影层：

- 按非语义展示偏好排序
- 在候选已经生成之后，再按 direction 过滤
- 为时间线或卡片视图重排结构

## 共享响应结构

虽然每个原子意图都有自己的结果类型，但这一家族应共享一些重复出现的响应元素。

### 分析结果主体

按意图不同而变化，例如：

- scalar observation（标量观测）
- segmented delta（分段差值）
- contribution rows（贡献行）
- correlation result（关联结果）
- anomaly candidates（异常候选）
- hypothesis-test result（假设检验结果）
- forecast series（预测序列）

### 校验元数据

当结果需要注意时，应返回结构化状态和机器可读 issue 列表。

典型模式：

- comparability（可比性）
- detectability（可检测性）
- alignment（对齐状态）
- attributability（可归因性）

### 截断元数据

只要意图返回的是有界子集，就必须显式披露截断。

典型字段：

- 返回行数
- 过滤后总行数
- 是否发生截断

### 溯源与执行元数据

结果应可通过以下信息追踪：

- 稳定的行或候选标识
- 执行时间戳
- query / plan hash
- engine 标识

## 各原子意图的边界说明

### `observe`

- 主要职责：产出类型化观测
- 合法压缩面：有界 segmented 输出
- 不应把 time-series 与 segmented 混成一个过载模式

### `compare`

- 主要职责：在两个兼容观测之间生成类型化 delta
- 应消费上游 observation refs
- 不应吸收 diagnosis 或 attribution 语义

### `decompose`

- 主要职责：把已知 delta 分配到排序后的贡献项
- 应消费明确的上游 delta 定义
- 应拒绝不受支持的指标、维度或归因方法

### `correlate`

- 主要职责：估计两个对齐序列之间的关联
- 应消费上游 time-series 观测
- v1 不应吸收 lag 搜索、metric 扫描或因果判断

### `detect`

- 主要职责：在有界扫描面内生成排序后的异常候选
- 输出语义是 candidate / flag，而不是 confirmed fact
- `max_series` 这类执行边界参数应进入契约
- 展示导向的过滤和排序更适合放在投影层

### `test`

- 主要职责：评估结构化统计假设
- 应显式保留 hypothesis direction 和 null semantics
- 不应吸收综合解释或业务解读

### `forecast`

- 主要职责：将一条历史序列投影到有界未来 bucket
- 应消费上游 time-series 观测
- 应显式保留 horizon、profile 与 uncertainty 语义
- 不应吸收情景规划、目标求解或因果解释

## 派生意图与模板

当用户问题跨越多个原子意图时，Factum 应优先使用组合，而不是扩大原子意图的范围。

示例：

- `diagnose = detect -> compare -> decompose`
- `validate = observe -> test`

如果某个工作流在执行中需要依赖中间结果做额外判断，且该判断不能由输入和系统状态确定性推导，那么它就不应被提升为单个原子意图。

这类能力应属于：

- 可确定性展开的派生意图，或
- planner / template，或
- agent 侧工作流

## 评审检查清单

在新增或修改一个原子意图 Schema 前，请逐项确认：

1. 该意图是否回答了一个独立分析问题？
2. 请求契约是否以语义层为原生表达？
3. 非法组合和校验语义是否明确？
4. 输出是否类型稳定、可引用、可复现？
5. 输出是否可以压缩且不显著扭曲原意？
6. 执行边界控制项是否已与投影 / 展示控制项分离？
7. 该意图是否避免吸收相邻职责？
8. 该意图是否保持了 Factum 的确定性证据边界？
9. 步骤输出的 identity 边界是否明确？
10. 所有 nullable 字段的 null 语义是否单一？
11. provenance 是否结构化且 machine-readable？
12. agent consumption contract 是否完整定义？
13. schema version 演进策略是否明确？
14. 负向契约是否覆盖非法组合和状态转换？

如果其中任一项答案是否定的，这个设计大概率属于另一层，而不是原子意图层。
