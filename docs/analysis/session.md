# Session Schema

本文档定义 Factum 中分析容器根对象 `session` 的拟议类型契约。

状态：draft design。本文是 `docs/analysis/` 下规划中的规范 `session` schema 提案，不表示对应 HTTP endpoint、持久化模型或运行时实现已经完成。

## 目的

`session` 是 Factum 中的规范分析容器。它为一次分析任务提供描述性任务上下文、治理边界、生命周期边界，以及进入规范读取面的明确入口。

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
- agent 可以在 Factum 交互面之外维护私有 focus、候选步骤与调度顺序
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
    | "expired"
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
- `evidence-engine-runtime-lifecycle.md` 定义 canonical objects 的创建、重放、失效与 snapshot 语义

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

1. 先读 [`agent-interaction-contract-principles.md`](agent-interaction-contract-principles.md) 理解三层交互面与共享边界 / 私有 planning 的分离原则
2. 再读本文理解分析容器根边界
3. 再读 [`state-surface-schema.md`](state-surface-schema.md) 理解 session 级主读取面
4. 最后读 [`context-surface-schema.md`](context-surface-schema.md) 理解 proposition 级局部最小闭包
