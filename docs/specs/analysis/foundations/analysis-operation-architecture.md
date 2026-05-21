# Marivo 分析操作总设计

本文描述 Marivo 分析操作的目标态设计：从对外接口，到内部概念，再到证据类结果如何继续衔接后续分析。它回答三个问题：

1. 应该暴露什么样的分析接口。
2. 内部应该有哪些稳定概念。
3. 为什么要这样设计，而不是把每个 intent 做成孤立接口。

状态：draft design。本文是目标态设计说明，不表示当前所有能力都已实现。

## 设计结论

Marivo 的分析操作不应被设计成一组彼此孤立的 RPC。

更合理的目标态是三层：

1. `atomic intents`：最小分析语义原语，例如 `observe`、`compare`、`detect`。
2. `analysis plan DSL`：把多个原子步骤组合成一次完整分析 DAG。
3. `outcome envelope`：一次执行后返回给 agent 的完整闭包，包含 artifact、finding、assessment、follow-up selector 等信息。

其中：

- 原子意图负责定义语义边界。
- Plan DSL 负责定义步骤编排。
- Outcome envelope 负责让 agent 少碰中间态。

这三层必须分开，否则系统会很快滑向两种坏形态：

- 每个 intent 都认识别人的结果类型，接口膨胀成网状依赖。
- Agent 被迫手工管理中间 artifact、引用关系和重试逻辑。

## 目标态概览

```text
business question
  -> analysis plan DSL
  -> atomic intent DAG
  -> committed artifacts
  -> findings
  -> propositions
  -> assessments
  -> outcome envelope
```

在这个链路里，Marivo 不只是“跑分析”，而是把分析变成可审计、可引用、可复现的结构化对象。

## 核心对象

### 1. `metric_frame`

`metric_frame` 是所有分析的起点。

它表示某个 semantic metric 在某个 slice 上的观测载体。形态可以是：

- `scalar`
- `time_series`
- `segmented`
- `panel`

它的本质不是“一个表”，而是“一个带轴和标签的观测面”。

### 2. `delta_frame`

`delta_frame` 是两个可比、可对齐的 `metric_frame` 做 `compare` 后得到的差值载体。

它表达“变了多少”，不表达“为什么变”。

### 3. `attribution_frame`

`attribution_frame` 是对 `delta_frame` 做 `decompose` 后得到的解释载体。

它表达“这份变化主要由谁贡献”，核心字段是 contribution 和 share，而不是再造一个新的 delta。

### 4. `forecast_frame`

`forecast_frame` 是预测型结果，不是原始 `metric_frame`。

它和 `metric_frame` 共享时间轴形态，但不共享认识论地位：

- `metric_frame` 表达已观测到的事实。
- `forecast_frame` 表达模型投影出的未来值与不确定性区间。

所以它不应被归入观测面，但也不必伪装成一般证据结果。更准确地说，它是一个 sibling frame family。

### 5. Evidence results

以下结果不属于 frame，而属于证据类结果：

- `anomaly_candidate`
- `correlation_result`
- `test_result`

它们不是新的观测面，而是对 frame 的判断、估计或投影结果。

## 分层设计

### 原子意图层

原子意图是最小分析语义单元。

它们应该稳定回答一个问题：

- `observe`：在给定 slice 上读取一个 semantic metric，输出 `metric_frame`。
- `compare`：对两个可比、可对齐的 `metric_frame` 计算差值，输出 `delta_frame`。
- `decompose`：对 `delta_frame` 沿一个归因轴分配贡献，输出 `attribution_frame`。
- `correlate`：对两个可对齐样本轴上的数值 frame 计算关联，输出 `association_result`。
- `detect`：在含时间轴的 scan-ready frame 上找异常候选，输出 `anomaly_candidate` 集合。
- `test`：对两个可对齐 frame 在明确假设下做统计检验，输出 `test_result`。
- `forecast`：对可预测的时间序列 frame 向未来投影，输出 `forecast_frame`。

原子意图的要求是：

- 输入类型稳定
- 输出类型稳定
- 可确定性执行
- 可被下游引用
- 不吸收相邻职责

### 派生意图层

派生意图是把一段固定、多步、可确定展开的分析流程包装成一个稳定动作。

例如：

- `attribute = observe + observe + compare + decompose`
- `diagnose = detect + focused observe + compare + decompose`
- `validate = paired observe + summarize + test`

派生意图不应引入新语义，只是把高频套路封装起来。

### Plan DSL 层

Plan DSL 是 agent-facing 的主编排接口。

它允许一次提交多个步骤、多个依赖、多个返回目标，而不是让 agent 反复调用三次、五次 MCP 工具去拼 DAG。

DSL 负责：

- step 定义
- step 依赖
- typed reference
- selector 解析
- inline transform
- materialization 策略
- return policy

### Derived intents vs command shortcuts

派生意图应保留为 canonical semantic layer。

像 `attribute`、`diagnose`、`validate` 这类动作，如果其内部展开可确定、且高频稳定，就应该继续作为派生意图存在。

所谓 convenience command，不应成为另一套并行语义层；它最多只是：

- 派生意图的别名
- 或编译到 `run_plan` 的快捷入口

如果一个 shortcut 形成了稳定的语义和稳定的返回契约，它应当升级为派生意图，而不是长期停留在“方便调用但语义模糊”的中间态。

### Outcome envelope 层

一次执行完成后，agent 不应该只拿到一个 artifact_id。

它应该拿到一个闭包，至少包含：

- committed artifact
- extracted findings
- seeded propositions
- current assessments
- follow-up selectors
- warnings / truncation / quality signals

这层是面向 agent 的读取面，不是 artifact 本体。

## 对外接口设计

### 1. `run_plan`

主接口。

输入是一份 JSON DSL，输出是一次完整分析执行的 outcome envelope。

适用场景：

- 一次完成多步分析
- 需要复用中间结果
- 需要把 evidence 结果继续衔接到后续 intent
- 需要减少 agent 的中间态负担

### 2. `run_intent`

原子 intent 的快捷入口。

它可以视为 `run_plan` 的单步特例，用于：

- 调试
- 回放
- 简单单步分析
- 与已有系统的兼容

目标态里，`run_intent` 只是 `run_plan` 的单步特例，不应成为第二套语义系统。

### 3. Evidence selector resolver

这是证据结果衔接后续分析的关键胶水层。

不要给 `observe`、`compare`、`test` 直接加一堆 `candidate_id`、`finding_id` 专用重载。更规范的方式是：

```text
evidence item -> selector role -> resolved frame spec
```

也就是说，证据类结果先暴露可解析的 selector，再由 runtime 解析成标准 `metric_frame` 输入。

注意：`resolve_selector` 不是一个新的分析语义原语，也不是和 `observe` 并列的 intent。它是 runtime 的支持能力，用来把 evidence 结果稳态地转回可继续分析的 frame spec。

## 输入统一方式

原子 intent 和 DSL 不应该各自定义一套平行输入。

应该统一成 typed reference 模型：

```ts
type FrameInput =
  | { kind: "step_output"; step_id: string; output?: string }
  | { kind: "artifact"; artifact_id: string }
  | { kind: "selector"; selector_id: string }
  | { kind: "inline"; spec: MetricFrameSpec };
```

这样：

- DSL 里可以引用上游 step 输出。
- 旧的单步接口可以直接引用 artifact。
- 证据类结果可以先解析成 selector，再转成 frame spec。
- `compare`、`correlate`、`test` 不需要维护两套不同 contract。

## 证据类结果如何作为后续输入

证据类结果不应直接变成新的 intent 专属输入字段。

更好的方式是让每个证据项携带结构化 selector 信息。

例如 `detect` 的候选应该带：

- 发生在哪个时间窗
- 对应哪个 series 或 segment
- 可用于 follow-up 的 focal selector
- 可用于 baseline 或 context 的 selector

然后后续步骤这样消费：

```text
detect -> anomaly_candidate
anomaly_candidate -> selector resolver -> observe
anomaly_candidate -> selector resolver -> compare
anomaly_candidate -> selector resolver -> decompose
```

这样 `observe` 仍然只认识标准 `metric_frame spec`，不会被 `candidate` 的内部结构污染。

## 结果类型与下游衔接

### detect

`detect` 的结果不是 frame，而是异常候选集合。

它的下游通常是：

- `observe` 做局部复看
- `compare` 做窗口对比
- `decompose` 做归因
- `diagnose` 做组合式跟进

### correlate

`correlate` 的结果是关联结果，不是 frame。

它的下游通常是：

- 作为关联证据进入 proposition / assessment
- 触发进一步的分层或分组分析
- 作为业务解释的输入

### test

`test` 的结果是假设检验结果，不是 frame。

它的下游通常是：

- 进入 proposition / assessment
- 作为决策是否成立的证据
- 作为后续验证或监控动作的依据

### forecast

`forecast` 的结果是预测点序列，也不是 observed frame。

它的下游通常是：

- 展示未来趋势
- 作为预测偏差监控的基线
- 与后续实际观察做 compare

## 如何理解这套结果面

这里有两个不同层次：

### 1. 计算层

计算层关心的是：

- frame 是否可对齐
- delta 是否可定义
- attribution 是否可加性解释
- association / hypothesis / anomaly 是否可计算

### 2. 证据层

证据层关心的是：

- 哪个结果项可以被稳定引用
- 哪个 finding 可以 seed proposition
- 哪个 assessment 可以作为当前判断
- 哪个 selector 可以驱动下一步分析

所以，结果面不是“再做一个 frame”，而是“把 frame 计算后的事实结果变成可继续消费的证据对象”。

## Agent-facing 最终返回什么

一次分析完成后，最友好的返回不是裸 artifact。

应该至少返回：

```text
artifact
findings
seeded propositions
assessments
selectors for follow-up
quality / truncation / warnings
```

这样 agent 不需要自己去回读整份 artifact，也不需要自己猜下一步应该怎么接。

## 为什么这样设计

### 1. 减少 agent 的 plumbing 负担

如果 agent 必须手工调用多个 intent，它会把大量预算花在：

- 管中间 artifact
- 传引用
- 处理重复窗口
- 处理排序和截断

这些都不是分析本身。

### 2. 保持语义边界清楚

`observe` 不应知道 `candidate` 是什么。

`compare` 不应知道 `detect` 的内部策略。

`test` 不应知道 `correlate` 的实现细节。

统一通过 typed reference、selector 和 plan DSL 连接，比给每个 intent 加专用重载更稳。

### 3. 保留可审计性

原子 intent 仍然是最小审计单元。

Plan DSL 只是组合这些原语，不会创造第二套黑箱执行语义。

### 4. 既支持单步，也支持多步

`run_intent` 适合简单任务和调试。

`run_plan` 适合复杂分析和 agent 主路径。

两者共享同一套底层语义，不会分裂。

### 5. 便于演进

当未来新增更多结果类型时，只要继续遵守：

- 结果先进入 finding
- finding 再进入 proposition / assessment
- 下游通过 selector 或 typed ref 解析

就不会破坏已有接口。

## 推荐的目标态接口形状

```text
run_plan(json_dsl) -> outcome_envelope
run_intent(atomic_request) -> outcome_envelope
resolve_selector(evidence_ref, role) -> metric_frame_spec
```

其中：

- `run_plan` 是 agent 主入口。
- `run_intent` 是兼容与调试入口。
- `resolve_selector` 是证据到分析的标准桥梁。

## 非目标

本文不试图定义：

- 每个 HTTP route 的具体路径
- 每个 artifact schema 的字段级细节
- 每个 finding subtype 的完整字段列表
- 每个 rule family 的判断算法

这些内容应继续放在各自的 schema、runtime pipeline 和 rule 文档里。

## 总结

Marivo 的分析操作目标态，不是“更多 intent”，而是“更少的手工编排”。

正确方向是：

- 原子意图保持语义纯净。
- Plan DSL 负责把多步分析变成一次可提交的声明式计划。
- 证据类结果不伪装成 frame，而是通过 selector 和 finding 进入后续分析。
- agent 拿到的不是裸 artifact，而是一份可继续行动的 outcome envelope。

这套设计的本质是：让分析动作可组合，让证据可继承，让 agent 少做 plumbing，多做判断。
