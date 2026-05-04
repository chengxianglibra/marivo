# Session Schema

本文档定义 Marivo 中分析容器根对象 `session` 的拟议类型契约。

状态：draft design。本文是 `specs/analysis/` 下规划中的规范 `session` schema 提案，不表示对应 HTTP endpoint、持久化模型或运行时实现已经完成。

## 目的

`session` 是 Marivo 中的规范分析容器。它为一次分析任务提供描述性任务上下文、治理边界、生命周期边界，以及进入规范读取面的明确入口。

设计目标：

- 把 `session` 定义为分析容器，而不是工件 / 状态 / workflow / projection 的混合体
- 让所有 typed analysis intents 与规范证据对象都拥有明确的 session 归属
- 让 session root 与 agent 私有 planning state 显式分离
- 保持 `session` 本体轻量，不把命题中心主状态、answer readiness 或 workflow 协调逻辑直接内嵌进根对象
- 让 agent 能稳定判断何时继续写入当前 session、何时应因治理边界变化 rollover 到新 session
- 与 `finding / proposition / assessment / state surface / context surface` 的设计保持一致

## 核心设计决策

### 1. `session` 是分析容器，不是新的证据层

`session` 处于规范证据链之外，但为其提供根边界：

`session -> artifact -> finding -> proposition -> assessment -> action proposal`

`session` 的职责是承载：

- 当前任务要解决什么问题
- 该任务在什么描述性 `goal` 与稳定 `governance` 下进行
- 当前任务是否仍可继续产生新的规范对象
- 当前有哪些权威读取面可进入

它不负责承载：

- 实时 support / oppose / gap membership
- 命题中心主决策骨架
- session 级回答就绪判断
- step-level 执行约束
- agent 私有 planning state 或 step 调度顺序
- 完整 step 执行日志
- narrative report 或自由文本答案
- 内部 lease、锁或 job 调度细节
- runtime attempt、claim、retry、backpressure 或 publish checkpoint 细节

### 2. `session` 是会话内局部规范标识的根边界

v1 中规范对象的标识边界都绑定单个 session。

要求：

- 每个规范对象都必须显式归属某个 `session_id`
- 不做跨 session 规范 merge
- 不做跨 session proposition / assessment registry
- 不允许用 projection 或自由文本替代会话内局部 typed refs

### 3. `session` 本体只保留边界与入口

`session` 不直接内嵌 `SessionStateView` 或 `PropositionContextView`。

原因：

- 命题中心 state surface 与 context surface 已是独立读取契约
- 若把主状态直接塞回 `session`，根对象会重新混入读取层职责
- `session` 根对象首先要回答“任务边界是什么、还能不能继续写”，而不是“当前有哪些 live evidence members”

因此：

- `session` 只保留到 `SessionStateView` 的最小入口
- 全局判断一律通过 `SessionStateView`
- 单 proposition 局部最小闭包读取一律通过 `PropositionContextView`
- `session` 不再定义独立的 answer summary 入口

### 4. `session` root 不承载执行约束

执行约束属于 typed intent 的 step-level contract，而不是 `session` 根对象。

因此：

- session root 上不存在 `scope`、`time_scope`、`focus` 或 planning hint 字段
- 所有分析约束都应在具体 step 的 `scope` / `time_scope` 中显式表达
- agent 可以在 Marivo 交互面之外维护私有 focus、候选步骤与调度顺序
- agent 可以随着分析推进在后续 steps 中逐步收紧约束；这不改写 session root
- step-level scope 的变化本身不构成 session root 的变更语义

### 5. `goal` 只保留描述性任务表述，而不是把自由文本问题当作 identity

`goal` 同时包含：

- `question`：当前任务的描述性自然语言表述

其中：

- `question` 用于保留用户问题或 agent 当前采用的问题表述
- `question` 可以帮助 agent 理解任务，但不单独定义 canonical identity

因此：

- 不应把自由文本改写本身视为必须 rollover 的语义边界变化
- session continuity 不应由问题改写决定；v1 中只有治理边界变化要求 rollover
- `goal` 只提供描述性任务上下文，不参与 canonical evidence semantics，也不承担执行过滤职责

### 6. `session` 的治理边界与证据语义分离

`governance` 的职责是约束“允许如何执行与读取”，不是定义 evidence semantics。

它可以表达：

- policy refs（治理策略引用）
- budget（资源预算）
- warnings（治理警告）

它不得表达：

- 命题是否成立
- 哪个 finding 属于 support / oppose
- evidence gap 的判断语义

治理风险可以阻止某些执行，但不应被折叠成判断层的 canonical gap。

### 7. `session` 生命周期与 evidence lifecycle 分离

`session` 生命周期回答的是“容器是否仍可接受新的 canonical writes”，不是 assessment 的状态机。

因此：

- `session.lifecycle.status` 与 `assessment.status` 必须分离
- `session` 关闭后仍可读取 state/context
- “足够回答用户”属于 agent 基于 state/context 的消费结论，而不是 `session` 本体字段
- `latest` / `live` 属于读取层语义，不是 `session` 本体上的 mutable flag family

更细的 runtime stage ownership、publish boundary、retry / recovery、claim / backlog 语义由 [`../runtime-lifecycle.md`](../runtime-lifecycle.md) 定义，而不是由 `session` 根对象承载。

### 8. `SessionStateSummary` 只承载读取入口

`SessionStateSummary` 是 `session` 根对象上的最小状态入口对象。

它的职责只有：

- 指向当前 session 的权威 `SessionStateView` 入口

它不承载：

- blockers 摘要
- readiness 摘要
- top focus proposition 排名
- 任何需要 consumer 据此直接做判断的统计

在 v1 中，它只是 canonical entry handle，而不是轻量状态对象。

## Schema Position

`session` 在 agent interaction contract 中位于根容器层，而不是 state surface、context surface 或 projection surface 的替代物。

关系如下：

- action surface 负责执行 typed intents，并通过 step params 表达执行约束
- `session` 负责提供任务边界、治理边界、生命周期边界与读取入口
- state surface 负责暴露 proposition-centered 主状态
- context surface 负责暴露单 proposition 局部最小闭包
- projection surface 负责有界排序、截断与 focus view
- runtime lifecycle / status surface 负责承载 claim、attempt、failure visibility 与 backlog 等运行时状态

因此读取链路应理解为：

`session -> state surface / context surface -> consumer projection`

## Typed Schema

```ts
type AnalysisSession = {
  session_id: string;
  goal: SessionGoal;
  governance: SessionGovernance;
  lifecycle: SessionLifecycle;
  state_summary: SessionStateSummary;
  created_at: string;
  updated_at: string;
  schema_version: string;
};

type SessionGoal = {
  question: string;
};

type SessionGovernance = {
  policy_refs: GovernancePolicyRef[] | null;
  budget: SessionBudget | null;
  warnings: string[] | null;
};

type GovernancePolicyRef = {
  policy_id: string;
  policy_version: string | null;
};

type SessionBudget = {
  max_steps: number | null;
  max_scan_bytes: number | null;
  max_latency_sec: number | null;
};

type SessionLifecycle = {
  status: "open" | "closed" | "aborted";
  terminal_reason:
    | "answered"
    | "abandoned"
    | "rolled_over"
    | "governance_terminated"
    | "budget_exhausted"
    | "timed_out"
    | null;
  ended_at: string | null;
  rollover_from_session_id: string | null;
};

type SessionStateSummary = {
  state_view_ref: SessionStateViewRef;
};

type SessionStateViewRef = {
  session_id: string;
  view_type: "session_state_view";
};
```

## 字段语义

### AnalysisSession

#### session_id

`session_id` 是分析容器根标识。

要求：

- 对同一 session 生命周期稳定
- 所有 canonical objects 都必须通过它显式归属当前容器
- 不表达当前状态是否 latest / active / ready

#### goal

`goal` 表达当前分析任务要解决的问题。

规则：

- `goal.question` 是描述性表述，不是 canonical identity
- `goal` 只提供描述性任务上下文，不能反向定义 canonical evidence semantics
- `goal` 不承载执行过滤条件；具体分析约束必须进入 step-level contract

#### governance

`governance` 定义 session 的治理边界。

要求：

- `policy_refs` 或其等价快照只用于审计与执行限制
- `budget` 是执行容量限制，不是语义边界
- `warnings` 是 governance warning，不是 evidence gap

#### lifecycle

`lifecycle` 只表达分析容器自身的生命周期。

规则：

- `open` 表示可继续接受 canonical writes
- `closed` 表示任务以正常完成态结束，不再接受新的 canonical writes
- `aborted` 表示任务在未完成目标前终止，不再接受新的 canonical writes
- `terminal_reason` 解释容器为何结束，不解释 proposition 是否成立
- `answered` 与 `rolled_over` 只能映射到 `closed`
- `abandoned`、`governance_terminated`、`budget_exhausted`、`timed_out` 只能映射到 `aborted`

若 `status = "open"`：

- `terminal_reason` 必须为空
- `ended_at` 必须为空

若 `status = "closed"` 或 `status = "aborted"`：

- `ended_at` 必须非空
- state/context 仍必须可读
- 历史 refs 不得失效为不可解释状态

#### state_summary

`state_summary` 是 session 根对象上的最小状态入口。

要求：

- 它必须指向稳定的 `state_view_ref`
- 它不替代完整 `SessionStateView`
- consumer 不得基于它直接判断 blockers、coverage 或 readiness
- v1 中它始终存在，不用 `null` 表示“尚未有状态”

### SessionStateSummary

`SessionStateSummary` 是 session 根对象上的最小状态入口对象。

#### state_view_ref

`state_view_ref` 是进入完整 `SessionStateView` 的权威入口。

要求：

- 不得退化为裸字符串 locator
- 不得把 projection variant 编入 canonical ref identity
- 在 v1 中它只是 canonical entry handle，不额外携带状态摘要语义

## 可变性与 Rollover 规则

### 原则

如果某个字段变化会改变 session 的稳定任务边界或治理边界，该字段必须要求 rollover；否则可以原地更新。

### 字段可变性矩阵

| 字段 | 可变性 | 变更后果 |
|------|--------|----------|
| `session_id` | 不可变 | 不允许修改 |
| `goal.question` | 可变 | 可原地更新 |
| `governance.policy_refs` | 不可变 | 值变化必须 rollover |
| `governance.budget` | 可变 | 可原地更新 |
| `governance.warnings` | 可变 | 可原地更新 |
| `lifecycle.status` | 单向转换 | 只允许 `open -> closed | aborted` |

### Rollover 触发字段

以下字段发生值变化时，必须创建新 session：

1. `governance.policy_refs`

附加规则：

- rollover 判断是值级别判断，不做“轻微修改”例外
- `rollover_from_session_id` 只表达容器级衔接，不表达 proposition / assessment 的跨 session 继承
- v1 中 rollover 不继承现有 evidence objects

## Session lifecycle transition details

### 目标

本节把 `SessionLifecycle` 从“字段约束”补足为“事件、authority、状态转换与终态归因”的 decision-complete 规则。

v1 采用以下基线：

- terminal transition 采用显式优先模型
- 系统可以检测并暴露 lifecycle-related signals，但除 rollover 流程外，不直接把 session 自动推进到 terminal state
- 不引入 `closing`、`terminating`、`pending_terminal` 一类中间态
- `SessionLifecycle` 只记录已提交的 terminal outcome，不承载未提交的 signal 集合

### 事件族与 authority

v1 用下列事件族解释 session lifecycle transition：

| 事件族 | 来源 | 是否直接改写 `SessionLifecycle` | 说明 |
|------|------|------------------------------|------|
| explicit completion | agent / 调用方显式结束 | 是 | 把 session 结束为 `closed + answered` |
| explicit abandonment | agent / 调用方显式结束 | 是 | 把 session 结束为 `aborted + abandoned` |
| rollover completion | rollover 流程 | 是 | 旧 session 结束为 `closed + rolled_over` |
| governance termination signal | 系统治理层 | 否 | 仅声明当前 session 已出现治理终止信号；后续可显式结束为 `aborted + governance_terminated` |
| budget exhaustion signal | 系统预算/执行层 | 否 | 仅声明当前 session 已出现预算耗尽信号；后续可显式结束为 `aborted + budget_exhausted` |
| timeout signal | 系统预算/执行层 | 否 | 仅声明当前 session 已出现超时信号；后续可显式结束为 `aborted + timed_out` |
| step execution failure | 执行面 | 否 | 普通 step 失败只作为执行结果返回，不直接进入 lifecycle terminal transition |

补充规则：

- terminal reason 的 authority 与事件族绑定，不允许自由伪报
- 系统派生的 signal 是 terminal reason 的 eligibility condition，不是自动状态转换器
- 若同一 session 同时出现多个 system-derived signals，v1 不定义全局优先级；显式结束时只允许提交一个最能解释终止原因的 `terminal_reason`

### 允许的状态转换

v1 只允许以下转换：

- `open -> closed`
- `open -> aborted`

不允许以下转换：

- `closed -> open`
- `aborted -> open`
- `closed -> aborted`
- `aborted -> closed`
- 任何 terminal -> terminal 的二次改写

附加规则：

- 一旦提交 terminal transition，`ended_at` 必须同步固定
- terminal transition 提交后，`terminal_reason` 不得再被覆写
- system-derived signal 本身不改变 `ended_at`

### `terminal_reason` 与状态映射

| `terminal_reason` | 目标状态 | 允许写入者 | 前置条件 |
|------|----------|------------|----------|
| `answered` | `closed` | 显式结束调用方 | 无额外 signal 要求 |
| `abandoned` | `aborted` | 显式结束调用方 | 无额外 signal 要求 |
| `rolled_over` | `closed` | rollover 流程 | 必须已经创建后继 session，并把 `rollover_from_session_id` 指回当前 session |
| `governance_terminated` | `aborted` | 显式结束调用方 | 当前 session 已存在 governance termination signal |
| `budget_exhausted` | `aborted` | 显式结束调用方 | 当前 session 已存在 budget exhaustion signal |
| `timed_out` | `aborted` | 显式结束调用方 | 当前 session 已存在 timeout signal |

补充规则：

- `answered` / `abandoned` 始终可作为显式终止原因，不受 system-derived signal 约束
- system-derived signal 已存在时，agent 仍可选择 `answered` 或 `abandoned`；`terminal_reason` 记录的是“最终为何结束”，不是“出现过哪些 signal”
- `rolled_over` 只用于容器衔接，不应用来表达人工放弃后重开新任务

### signal 语义

#### governance termination signal

`governance_terminated` 只用于 session 级治理边界已不允许继续维持当前容器的情况，例如：

- 当前治理策略被撤销或失效
- 会话级访问授权被收回
- 平台治理层明确判定当前 session 必须终止

以下情况默认不构成 governance termination signal：

- 某个 step request 因字段级/查询级 policy 被拒绝，但 session 的治理边界本身仍有效
- 普通执行失败中顺带返回的 policy warning

#### budget exhaustion signal

`budget_exhausted` 只表示当前 session 已达到或超过既定 budget 边界，例如：

- 达到 `max_steps`
- 达到 `max_scan_bytes`

v1 规则：

- signal 出现后 session 仍保持 `open`
- signal 本身不自动封禁后续 canonical writes
- 是否继续尝试新的 step 由 agent / 调用方自行决定
- 若最终决定结束，可显式提交 `aborted + budget_exhausted`

#### timeout signal

`timed_out` 只表示当前 session 已达到或超过既定时间边界，例如：

- 达到 `max_latency_sec`
- 外层运行时对 session 施加的 wall-clock 超时已命中

v1 规则：

- signal 出现后 session 仍保持 `open`
- signal 本身不自动封禁后续 canonical writes
- 是否继续尝试新的 step 由 agent / 调用方自行决定
- 若最终决定结束，可显式提交 `aborted + timed_out`

### 执行失败与 lifecycle 的关系

普通 step execution failure 不构成独立 `terminal_reason`。

规则：

- 单次 step 失败只由执行面返回，不直接改写 `SessionLifecycle`
- `SessionLifecycle` 不记录失败中的中间协调态
- 若失败根因只是一般查询错误、规划错误或外部依赖错误，session 仍保持 `open`
- 若失败根因触发了 governance / budget / timeout signal，可额外产生对应 signal；但 terminal transition 仍需显式提交
- 若 agent 因累计失败决定不再继续，终止原因统一记为 `abandoned`

因此，v1 不引入 `execution_failed` 一类独立 terminal reason。

### rollover 的 lifecycle 语义

rollover 是唯一允许直接提交 terminal transition 的系统流程。

规则：

- 当不可变治理边界变化时，必须创建新 session，而不是改写旧 session
- 旧 session 以 `closed + rolled_over` 结束
- 新 session 的 `rollover_from_session_id` 指向旧 session
- rollover 不继承旧 session 的 canonical evidence objects
- rollover 是容器切换，不是 evidence merge 或 cross-session continuation registry

### 提交与读取约束

在 session 保持 `open` 时：

- 可以继续追加 canonical writes
- 可以继续读取 `SessionStateView` 与 `PropositionContextView`
- 可以出现 lifecycle-related signals，但这些 signals 不直接改写 root lifecycle

一旦 session 进入 terminal state：

- 不得新增新的 canonical evidence objects
- 仍必须允许读取 `state_summary`、`SessionStateView` 与 `PropositionContextView`
- 历史 refs 与 evidence objects 必须保持可解释、可审计
- terminal state 不得因后续 signal 再次改写

### 非目标

本节不定义以下内容：

- 触发显式终止的 HTTP path、payload 或错误码
- signal 的具体持久化位置或读取面
- signal 是否需要单独暴露为 API 资源
- runtime 如何调度 terminate / rollover 操作
- agent 是否“应该”在某个 signal 出现后立即结束 session

这些内容若后续需要对外收敛，应进入 `docs/api/` 或独立 runtime / state-surface 设计文档。

## 生命周期与写入协调

### 创建

创建 session 时，先建立 `AnalysisSession` 根对象，再允许后续 typed intents 写入属于该 session 的 canonical evidence。

创建后最小保证：

- `session_id` 已稳定
- `goal / governance / lifecycle` 已确定
- `state_summary` 已存在并可用作 state surface 入口

### 写入

typed intent 成功执行后产生的 `artifact / finding / proposition / assessment / action proposal` 都必须显式归属当前 `session_id`。

规则：

- 所有执行约束都必须在具体 step request 中显式给出，不从 session root 继承
- `governance.policy_refs` 的值变化必须通过 rollover
- `question`、`governance.budget`、`governance.warnings` 可原地更新
- `state_summary` 的刷新属于读取入口维护，不改写下游 evidence semantics
- v1 中单次 step 执行为同步语义；step 成功返回时，对应 canonical writes 应已完成提交
- v1 中 step 执行失败不在 `session` root 保留中间协调态；失败由执行面直接返回
- budget / timeout / governance signal 的出现本身不自动终止 session，也不自动封禁写入
- 显式终止时使用的 `terminal_reason` 必须满足本节前述 authority 与前置条件约束

### 关闭

当 session 进入 `closed` 或 `aborted`：

- 不得新增新的 canonical evidence objects
- 允许读取 `state_summary`、`SessionStateView` 与 `PropositionContextView`
- 历史 evidence objects 仍保留其可审计性

## 读取入口与默认消费方式

agent 默认消费顺序应为：

1. 先读取 `AnalysisSession`
2. 通过 `AnalysisSession` 获取 session-level goal、治理边界、生命周期与 state surface 入口
3. 需要全局判断时进入 `SessionStateView`
4. 需要解释单 proposition 时进入 `PropositionContextView`

其中：

- `session` 根对象不承担全局判断职责
- `session` 根对象不提供执行过滤条件
- 所有 blockers / coverage / readiness 判断都应在 state/context 读取后完成
- `state_summary` 只是 state surface 入口 shortcut，不是轻量决策对象

## 与其他 canonical schema 的关系

- `session.md` 只定义分析容器根对象与其最小读取入口
- `finding.md`、`proposition.md`、`assessment.md`、`action-proposal.md` 定义证据链对象
- `state-surface-schema.md` 定义 session 级主读取面
- `context-surface-schema.md` 定义 proposition 级局部最小闭包
- `evidence-engine/runtime-pipeline.md` 定义 canonical objects 的创建、重放、失效与 snapshot 语义

`session.md` 不复写下游对象自己的字段语义，只定义这些对象在容器根上的归属、入口与边界。

## 非目标与负向契约

本文明确拒绝以下设计回退：

- 在 `session` 根对象内嵌完整 `SessionStateView`
- 在 `session` 根对象内嵌 proposition context closure
- 在 `session` 根对象写入 step-level `scope` / `time_scope`
- 在 `session` 根对象写入 agent 私有 planning state、候选步骤或调度顺序
- 在 `session` 根对象重新引入 readiness summary、report summary 或 recommendation summary
- 把 governance warning 折叠成 `evidence_gap`
- 在 v1 `session` root 中预埋异步执行协调字段
- 让 `session` 充当 execution-scope carrier、projection 容器、report 容器或 workflow brain
- 把自由文本 `goal.question` 重新提升为 session canonical identity

## 阅读建议

推荐顺序：

1. 先读 [`agent-interaction-contract-principles.md`](../../foundations/agent-interaction-contract-principles.md) 理解三层交互面与共享边界 / 私有 planning 的分离原则
2. 再读本文理解分析容器根边界
3. 再读 [`state-surface-schema.md`](state-surface-schema.md) 理解 session 级主读取面
4. 最后读 [`context-surface-schema.md`](context-surface-schema.md) 理解 proposition 级局部最小闭包
