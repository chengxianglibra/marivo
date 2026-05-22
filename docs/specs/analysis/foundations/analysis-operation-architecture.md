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
2. `analysis plan DSL`：把多个原子步骤和 transform step 组合成一次完整分析 DAG。
3. `outcome envelope`：一次 job 执行后返回给 agent 的完整闭包，包含 target artifact、session-level finding、proposition、assessment、action proposal、follow-up selector 等信息。

其中：

- 原子意图负责定义语义边界。
- Transform 操作负责重塑已有结果，使其适合后续 intent 消费。
- Plan DSL 负责定义 intent step 与 transform step 的编排。
- Outcome envelope 负责让 agent 少碰中间态。

这三层必须分开，否则系统会很快滑向两种坏形态：

- 每个 intent 都认识别人的结果类型，接口膨胀成网状依赖。
- Agent 被迫手工管理中间 artifact、引用关系和重试逻辑。

## 目标态概览

```text
business question
  -> analysis session
  -> job submitted with analysis plan DSL
  -> atomic intent / transform DAG
  -> committed artifacts
  -> findings
  -> propositions
  -> assessments
  -> outcome envelope
```

在这个链路里，Marivo 不只是“跑分析”，而是把分析变成可审计、可引用、可复现的结构化对象。

## Session / Job / Step 层级

对 agent 来说，一次 analysis plan DSL 不是“批处理脚本”，而是在一个分析会话中推进问题的一次原子分析动作。

目标态层级应固定为：

```text
analysis session
  -> job
    -> step
```

其中：

- `session` 是持续分析任务的容器，承载 business question、历史 jobs、已提交 artifacts、findings、propositions、assessments、action proposals 和 follow-up selectors。
- `job` 是 agent-facing 的原子分析动作。一次 job 由 agent 提交一份 analysis plan DSL，runtime 将其编译、执行、审计并返回 outcome envelope。一个 session 可以经过多轮 job 持续推进。
- `step` 是 job 内部 DAG 的执行节点，可以是 atomic intent step，也可以是 transform step。Step 是 runtime 编排和审计的最小执行单元，但不是 agent 主循环里的下一轮分析动作。

因此 agent 主循环应是：

```text
submit job
  -> receive outcome envelope
  -> decide next job
```

Agent 不应在正常路径上手工围绕裸 step 编排下一轮，也不应每轮 job 后再强制调用多个读取接口拼接 session 状态。

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

### Transform 操作层

Transform 操作不是新的 atomic intent。

它们不从 semantic metric 重新读取数据，也不直接表达新的分析判断。它们的职责是对已有 typed artifact、frame-like output 或 selector-resolved input 做结构重写、粒度调整、统计摘要或输入适配，让后续 intent 可以消费标准输入。

目标态里，transform 默认是一等 DAG step，可以被审计、引用和复用。Inline transform 只应作为语法糖存在，必须等价展开成显式 transform step；lineage、validation、materialization 和 audit 都以展开后的 step 为准。

v1 transform 集合应包括：

- `slice`：从 frame 中选择子空间，例如收窄时间窗、segment key 或 selector 指向的局部区域。它的语义是“取子集”，不是重新 `observe`，也不修改 semantic metric 定义。
- `rollup`：沿时间轴或维度轴聚合 frame，例如日到周、城市到国家、segment 到 overall。它的语义是“降低粒度 / 合并分组”，必须保留 lineage 和 rollup policy。这里的 rollup policy 是 transform 的聚合声明，不等同于 compare metadata 中的 calendar `rollup_safe` 判断。
- `sample_summary`：从 frame 或 selector-resolved population 生成 test-ready 样本摘要，例如 n、mean、stddev、rate numerator / denominator。它的语义是“统计检验输入摘要”，目标态下不再作为 `observe` 的特殊公开 result mode。
- `select_topk`：按 value、delta、contribution、share 等排序选择 top / bottom items。它的语义是“候选收窄”，不产生 evidence 判断。
- `normalize`：重表达 measure，例如 index、share、pct_change、per-unit、z-score。它用于让不同量级、不同曝光或不同基准的序列可比，不改变观察对象，也不伪装成新的 semantic metric。
- `align`：显式定义多个 frame 的 bucket、sample、segment key 或 window 如何配对。它用于 `compare`、`correlate`、`test` 前的输入配对，不改变值含义，也不重定义 `compare.compare_type` 的日历业务语义。

`normalize` 适合“值本身需要换一种表达方式”的场景。例如：

- 百万级 DAU 与百分比指标进入 `correlate` 前转成 z-score。
- 大小渠道进入 `detect` 前转成相对基准期 index。
- segment 值转成 share 后再 `compare`，分析结构变化而不是绝对规模。
- 投诉数除以订单数，转成每万订单投诉率后再分析。

`align` 适合“输入样本轴不天然一一对应”的场景。例如：

- 两个 time series 缺失 bucket 不同，`correlate` 前取交集或声明外连接。
- current / baseline segment key 不一致，`compare` 前声明 inner、outer 或 left-preserving pairing。
- anomaly candidate window 与 baseline window 需要配成同长度窗口。
- `test` 前按 cohort key、实验桶或日期位置配对样本。

Transform 输出默认不直接 seed proposition。只有下游 intent 或 artifact-finding extraction 明确支持时，它才进入 finding / proposition / assessment 链路。

### 派生意图层

派生意图是把一段固定、多步、可确定展开的分析流程包装成一个稳定动作。

例如：

- `attribute = observe + observe + compare + decompose`
- `diagnose = detect + selector resolution + slice + observe / compare / decompose`
- `validate = observe + sample_summary + test`

派生意图不应引入新语义，只是把高频套路封装起来。

派生意图可以在内部展开 transform。比如 `attribute` 在两侧 frame 粒度不一致时，可以在 `compare` 前插入 `rollup` 或 `align`；`validate` 可以把 `sample_summary` 作为 `test` 的确定性前置输入；`diagnose` 可以把异常候选解析成 selector 后，用 `slice` 做局部复看。

### Plan DSL 层

Plan DSL 是 agent-facing 的主编排接口。

它允许一次提交多个步骤、多个依赖、多个返回目标，而不是让 agent 反复调用三次、五次 MCP 工具去拼 DAG。

DSL 负责：

- step 定义
- step 依赖
- typed reference
- selector 解析
- transform step
- inline transform sugar 的展开
- materialization 策略
- return policy

Plan DSL 中的 intent step 和 transform step 共同组成 DAG：

- intent step 产出 canonical artifact 或 evidence result。
- transform step 引用上游 `FrameInput`、artifact ref、step output 或 selector-resolved input，产出 typed transform output。
- 下游 intent 只消费标准 typed input，不关心输入来自 intent 还是 transform。

这意味着 transform 不需要污染 atomic intent 的请求面。`compare`、`correlate`、`test` 仍然只看自己声明的输入类型；如果输入需要先切片、汇总、归一化或配对，应由 Plan DSL 在上游显式插入 transform step。

Plan DSL 的另一个职责是把 agent-facing 的多种引用形态规范化。API 层可以为了易用性接受 `step_id`、`artifact_id`、`selector_id` 或 inline spec；但进入 intent executor 前，runtime 必须先通过 reference resolver / selector resolver 把这些来源统一解析成 canonical typed input。Atomic intent 不应自己解析 step、artifact 或 selector。

### Derived intents vs command shortcuts

派生意图应保留为 canonical semantic layer。

像 `attribute`、`diagnose`、`validate` 这类动作，如果其内部展开可确定、且高频稳定，就应该继续作为派生意图存在。

所谓 convenience command，不应成为另一套并行语义层；它最多只是：

- 派生意图的别名
- 或编译到 `submit_job` 的快捷入口

如果一个 shortcut 形成了稳定的语义和稳定的返回契约，它应当升级为派生意图，而不是长期停留在“方便调用但语义模糊”的中间态。

### Outcome envelope 层

一次 job 执行完成后，agent 不应该只拿到 target step 的 artifact_id。

它应该拿到一个闭包，包含本次 job 的目标产物，以及当前 session 到目前为止支持下一轮判断所需的 evidence closure。至少包括：

- target artifacts
- newly committed artifacts
- session findings
- session propositions
- current assessments
- current action proposals
- follow-up selectors
- warnings / truncation / quality signals

这层是面向 agent 的读取面，不是 artifact 本体。

`session_state`、`proposition_context` 这类单独读取接口不应成为 agent 正常分析循环里的必要 plumbing。它们可以保留为辅助 projection / read API，用于 UI、调试、分页、恢复上下文或外部系统读取；但主路径应由 job outcome envelope 直接提供下一轮决策所需的当前 session closure。

## 对外接口设计

### 1. `submit_job`

主接口。

输入是一份 analysis plan DSL，输出是一次 job 的 outcome envelope。

适用场景：

- 一次完成多步分析
- 需要复用中间结果
- 需要把 evidence 结果继续衔接到后续 intent
- 需要把当前 session closure 直接返回给 agent
- 需要减少 agent 的中间态和读取接口 plumbing 负担

`run_plan` 可以作为 `submit_job` 的兼容命名或低层别名存在，但目标态里规范概念应是 `job`：agent 提交 job，runtime 执行 job，session 记录 job history。

### 2. `run_intent`

原子 intent 的快捷入口。

它可以视为单 step job 的快捷入口，用于：

- 调试
- 回放
- 简单单步分析
- 与已有系统的兼容

目标态里，`run_intent` 只是 `submit_job` 的单步特例，不应成为第二套语义系统。

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
  | { kind: "selector"; selector_id: string; role?: string }
  | { kind: "inline"; spec: MetricFrameSpec };
```

这样：

- DSL 里可以引用上游 step 输出。
- 旧的单步接口可以直接引用 artifact。
- 证据类结果可以先解析成 selector，再转成 frame spec。
- `compare`、`correlate`、`test` 不需要维护两套不同 contract。

关键原则是：

```text
public API refs
  -> reference resolver / selector resolver
  -> canonical resolved input
  -> intent executor
```

这里的“统一”不是把所有输入都强行变成 `metric_frame`，而是按 intent 的输入类型统一成固定 resolved envelope。例如：

- `compare`、`correlate`、`detect`、`forecast` 消费 `ResolvedMetricFrameInput`。
- `decompose` 消费 `ResolvedDeltaFrameInput`。
- `test` 消费 `ResolvedSampleSummaryInput` 或等价的 test-ready resolved input。
- transform step 的输出也必须先形成对应的 resolved input，才能被下游 intent 消费。

Resolved input 至少应保留：

- 已解析出的 artifact family / shape / axes / measures / payload 或等价 typed view。
- 原始引用来源，例如来自 `step_output`、`artifact`、`selector` 还是 `inline`。
- selector role、transform lineage、materialization 信息等审计所需字段。

这样 API 可以保持对 agent 友好，而 runtime 的 intent executor 仍然只面对稳定、类型化、已校验的输入。

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

一次 job 完成后，最友好的返回不是裸 artifact，也不是只包含本次 DAG target 节点的结果。

应该返回本次 job 结果加当前 session closure：

```text
target artifacts
newly committed artifacts
session findings
session propositions
current assessments
current action proposals
selectors for follow-up
quality / truncation / warnings
```

这样 agent 不需要自己去回读整份 artifact，也不需要每轮 job 后再通过 `session_state`、`proposition_context` 等接口拼接当前判断上下文。

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

`submit_job` 适合复杂分析和 agent 主路径。

两者共享同一套底层语义，不会分裂。

### 5. 便于演进

当未来新增更多结果类型时，只要继续遵守：

- 结果先进入 finding
- finding 再进入 proposition / assessment
- 下游通过 selector 或 typed ref 解析

就不会破坏已有接口。

## 推荐的目标态接口形状

```text
submit_job(session_id, analysis_plan_dsl) -> outcome_envelope
run_intent(atomic_request) -> outcome_envelope
resolve_selector(evidence_ref, role) -> metric_frame_spec
```

其中：

- `submit_job` 是 agent 主入口，代表 session 内的一次原子分析动作。
- `run_intent` 是兼容与调试入口，可视为单 step job。
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
- Plan DSL 负责把多步分析变成一次可提交的 job。
- 证据类结果不伪装成 frame，而是通过 selector 和 finding 进入后续分析。
- agent 拿到的不是裸 artifact，而是本次 job 结果加当前 session closure 的 outcome envelope。

这套设计的本质是：让分析动作可组合，让证据可继承，让 agent 少做 plumbing，多做判断。
