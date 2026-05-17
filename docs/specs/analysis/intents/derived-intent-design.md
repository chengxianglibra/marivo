# 派生分析意图设计

本文档定义 Marivo 派生分析意图（derived analysis intents）的设计规则。

状态：draft design。本文是面向规划中派生意图家族及其 typed API contracts（类型化 API 契约）的设计指南，不表示文中所有意图都已实现。

## 目的

Marivo 的 derived intent layer（派生意图层）用于暴露更高层的分析动作，同时避免退回到 ad hoc workflow（临时工作流）或 SQL-shaped API（SQL 形状 API）。

一个派生意图，是一个可以被用户直接调用的、类型化的分析动作；它会被系统展开为由 atomic intents（原子意图）组成的确定性 DAG。

本文的目标，是在新增便捷型高层意图时，保证整个派生意图家族边界清楚、语义稳定、可审计。

设计目标：

- 保留原子意图层作为语义基础
- 将常见的多步骤分析动作暴露为稳定的类型契约
- 保持展开过程确定且可检查
- 保证结果在确定性投影后仍然有界且有意义
- 防止 workflow-style template（工作流式模板）渗入可执行意图层

## 在分析栈中的位置

Marivo 的分析栈包含三个层次：

1. atomic intents（原子意图）
2. derived intents（派生意图）
3. templates（模板）

三者必须严格区分。

### 原子意图

原子意图是不可再约简的分析语义单元，例如 `observe`、`compare`、`decompose`。

它们定义了系统的语义积木。

### 派生意图

派生意图是完全由原子意图组成的可执行便捷意图。

当用户稳定地希望“一次完成一个完整分析动作”，并且这个动作仍足够确定、可以被表达为一个稳定契约时，就适合建模为派生意图。

示例：

- `attribute` = 量化一个 delta 并对其做分解
- `diagnose` = 检测异常候选、量化异常并做归因
- `validate` = 准备可检验观测并执行统计检验

### 模板

模板是声明式分析模式，需要在执行中依赖外部决策。

它们不是 Marivo 可执行意图，而是给外部 orchestrator（例如 agent）的引导。

示例：

- `describe`：因为维度选择和下钻路径需要判断
- 开放式 `explain`：因为后续步骤依赖中间发现的解释与分支

## 定义

一个能力只有在满足以下全部条件时，才应建模为派生分析意图：

1. 用户自然地把它描述为一个完整分析动作。
2. 内部多步骤展开可以由请求和系统状态完全确定。
3. 展开过程只依赖已支持的原子意图和确定性系统变换。
4. 内部步骤之间不需要人类或外部 agent 做决策。
5. 最终结果拥有稳定的类型契约。
6. 结果可以被确定性投影为有界输出，且仍保留主要语义。

只要其中任一条件不成立，这个能力通常就更适合做模板，而不是派生意图。

## 派生意图为何存在

派生意图不是原子意图的替代品。

它存在的原因是：某些分析请求从用户视角看已经是语义完整的动作，但内部仍需要多个原子步骤组合完成。

如果没有派生意图，调用方就必须反复手工拼装同样的 DAG，这会：

- 增加 planner 复杂度
- 降低契约一致性
- 弱化共享校验与治理逻辑

有了派生意图，Marivo 可以提供：

- 面向用户的稳定高层契约
- 确定性的内部展开
- 共享的校验与治理规则
- 对反复出现的多步骤分析，提供标准工件形状

## 核心规则

### 1. 派生意图本质上是原子意图 DAG 的宏

派生意图的外部契约应保持语义化和类型化。

其内部实现是对原子意图和确定性系统变换的 DAG 展开，例如：

- step reference wiring（步骤引用连线）
- baseline window derivation（基线窗口推导）
- ranking / truncation policy（排序 / 截断策略）
- semantic default resolution（语义默认值推导）

展开过程不得引入一套与原子层无关的第二执行模型。

### 2. 原子意图语义始终是权威来源

派生意图必须复用原子意图的语义，而不是重定义它们。

例如：

- 若派生意图内部使用 `compare`，则 delta 语义必须继承自 `compare`
- 若内部使用 `decompose`，则 contribution 语义必须继承自 `decompose`
- 若内部使用 `test`，则 inferential semantics（推断语义）必须继承自 `test`

派生意图可以重新包装输出，但不得静默改变底层原子工件的语义。

### 3. 展开必须是确定性的

给定同一请求和同一系统状态，派生意图必须展开成同一个逻辑 DAG。

“确定性展开”包括：

- 创建哪些原子步骤
- 各步骤引用如何连接
- 缺省参数如何补全
- baseline / comparison windows 如何推导
- 最终行如何排序与截断

如果展开需要系统在中间做判断，例如“挑一个最有意思的维度”或“决定是否继续”，那它就不是派生意图。

### 4. 执行中不能依赖外部决策

执行期间，系统不得暂停并要求外部 orchestrator 补充语义决策。

不允许的例子：

- 看完中间结果后再选维度
- 决定哪些 anomaly candidates 值得做分解
- 根据开放式探索结果决定检查哪些关联指标

这些都属于模板行为，而不是派生意图行为。

### 5. 最终契约必须类型稳定

派生意图必须暴露：

- 一个稳定的请求类型
- 一个稳定的响应类型

即使内部会生成多个原子工件，最终响应也应围绕用户要完成的分析动作来设计，而不是原始内部输出的简单转储。

它可以嵌入部分原子工件或引用，但最终仍应呈现为一个连贯的派生结果类型。

### 6. 先定义工件，再定义投影

派生意图同样遵守 Marivo 的 artifact / projection 分层。

完整工件应足以支持：

- provenance（溯源）
- reproducibility（复现）
- downstream referencing（下游引用）
- 审计内部展开过程

agent / UI 侧看到的 projection（投影）应当只是对该工件的确定性压缩。

投影不得：

- 改变语义结果
- 用不同逻辑重跑内部分析
- 发明工件中不存在的新 claim

### 7. 有界输出是强制要求

任何面向用户可调用的 Marivo intent 都必须在 bounded output（有界输出）下仍然有意义。

对派生意图而言，这意味着：

- anomaly list 需要稳定排序规则
- driver list 需要稳定排序规则
- residual / truncation metadata 必须披露被省略内容

如果不存在稳定的压缩规则，这个能力就更适合做模板。

## 派生意图与模板的边界

### 何时应该建模为派生意图

当满足以下条件时，更适合做派生意图：

- 调用方想要的是一个完整分析动作
- 所有内部参数都能确定性推导
- 所有中间分支都由契约固定
- 最终结果可以形成有界、稳定的语义摘要

### 何时应该建模为模板

当存在以下情况时，更适合做模板：

- 执行必须在多个后续路径之间做选择
- 必须先解释中间结果，才能知道下一步做什么
- 维度或指标选择依赖开放式探索
- “最佳摘要”无法通过稳定排序或截断规则确定

### 经验法则

如果系统可以在不问“下一步该做什么”的情况下，完整展开并执行分析，它就可能适合做派生意图。

如果系统在执行中任何时刻都必须问出这个问题，它就属于模板。

## Schema 设计原则

派生意图的请求和响应契约设计必须遵守 Marivo 的 Canonical Schema 设计原则。

详见 [`canonical-schema-principles.md`](../foundations/canonical-schema-principles.md)。

这些原则确保派生意图的契约保持：
- Agent 可消费性
- 语义清晰性
- 可追溯性
- 可演进性

## 契约结构

每个派生意图设计文档都应包含以下契约部分。

### 请求契约

请求只应包含对用户有意义的语义输入。

典型字段：

- 目标指标或目标假设
- 类型化范围或时间窗口
- 语义维度
- 稳定的语义模式开关
- `limit` 这类有界输出控制项

请求不应暴露：

- 内部步骤 ID
- SQL 片段
- 任意 planner knobs
- 开放式执行分支

### 展开契约

派生意图设计文档应明确说明：

- 会展开为哪些原子意图
- 它们的顺序或 DAG 形状
- 哪些输入会被转发到各原子步骤
- 哪些内部参数由系统推导
- 展开前要做哪些校验

这部分展开契约属于设计的一部分，不应依赖“实现碰巧如此”。

### 响应契约

响应应包含：

- 稳定的派生结果类型
- 用户真正关心的主要语义输出
- 能解释截断与质量状态的元数据
- 在需要时提供 provenance 或内部 refs

调用方不应被迫从原始内部工件中自行重建语义。

## 校验规则

派生意图校验分为三个层次。

### 1. 请求校验

在展开前，校验外部请求形状。

示例：

- 必需的 metric / dimension 是否存在
- `limit` 是否有界
- semantic scopes 是否合法

### 2. 展开校验

校验系统是否能确定性推导每个内部步骤参数。

示例：

- `diagnose` 是否存在可用的 baseline window policy
- `validate` 所需指标是否支持 inferential-ready observation
- `attribute` 请求的 dimension 是否支持 attribution

如果有内部参数无法确定性推导，就应直接失败，而不是退化为 ad hoc planner 行为。

### 3. 原子兼容性校验

在展开前或展开过程中，还要保证每个内部原子步骤都符合各自契约。

示例：

- 内部 `compare` 输入是否可比
- 内部 `decompose` 输入是否可归因
- 内部 `test` 输入是否 inferential-ready 且 method-compatible

派生意图不得绕过原子意图的校验规则。

## 治理与证据边界

派生意图继承 Marivo 的确定性证据边界。

它们可以包含：

- 内部 observations
- deltas
- decomposition rows
- test results
- anomaly candidates
- provenance 与 validation metadata

它们不得借由派生契约偷带入：

- 不可验证的因果结论
- 以自由文本诊断作为证据主体
- 建议作为一等证据载荷

如果需要 explanation、recommendation 或 synthesis，那应该属于后续解释层，而不是核心派生工件契约。

## 推荐设计模板

每个派生意图设计文档建议包含以下部分：

1. purpose
2. request shape
3. typed schema
4. expansion definition
5. field semantics
6. validation rules
7. response shape
8. projection policy
9. examples
10. v1 scope limits

## 候选派生意图

以下能力是较强的 v1 候选，因为它们满足确定性展开和有界输出要求。

### Attribute

意图：

- 回答“这个指标在两个 scope 之间的变化由什么解释？”

详见 [`attribute` 派生意图 Schema](derived/attribute.md)。

展开：

- `observe(left)`
- `observe(right)`
- `compare(current_artifact_id, baseline_artifact_id)`
- `decompose(compare_artifact_id, dimension, ...)`

为什么成立：

- 用户动作是单一且完整的
- 所有内部 refs 都可确定性推导
- 结果在保留 top drivers 和 residual share 后仍然有意义

### Diagnose

意图：

- 回答“主要异常候选在哪里，它们由什么驱动？”

详见 [`diagnose` 派生意图 Schema](derived/diagnose.md)。

展开：

- `detect(metric, time_scope, ...)`
- 确定性的候选排序与截断
- 对每个 candidate：
  - 用固定策略推导 baseline window
  - `observe(current_window, inherited_slice_scope)`
  - `observe(baseline_window, inherited_slice_scope)`
  - `compare(...)`
  - 对显式给定的归因维度逐个 `decompose(...)`

为什么成立：

- candidate ranking 可以确定化
- baseline derivation 可以确定化
- driver dimensions 由请求显式给定，因此不需要执行时挑维度
- 单个异常的 driver 输出可以做有界压缩

主要风险：

- 如果 baseline 推导或 candidate follow-up policy 变得过于依赖判断，`diagnose` 应降级为模板

## 不适合作为派生意图的模式

### Describe

原因：

- “全景描述”不存在单一稳定的压缩规则
- 为了得到有用结果，必须决定看哪些维度和切片

### Open-Ended Explain

原因：

- 解释通常需要在维度、异常、关联和后续检查之间反复分支
- 下一步依赖于对中间证据的解释

## 设计检查清单

在新增一个派生意图前，应回答清楚以下问题：

1. 用户主观上认为自己在请求哪个单一分析动作？
2. 它会展开成哪一条 atomic DAG？
3. 是否每个内部参数都能确定性推导？
4. 请求校验与展开校验的边界在哪里？
5. 完整派生工件长什么样？
6. 哪种确定性投影能让它在 token 限制下仍然有用？
7. 为什么它不是模板？
8. 为什么它不应该直接由调用方组合原子步骤来完成？

如果这些问题没有清晰答案，这个能力就还不适合加入派生意图层。
