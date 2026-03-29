# Session Schema

本文档定义 Factum 中分析容器根对象 `session` 的拟议类型契约。

状态：draft design。本文是 `docs/analysis/` 下规划中的规范 `session` schema 提案，不表示对应 HTTP endpoint、持久化模型或运行时实现已经完成。

## 目的

`session` 是 Factum 中的规范分析容器。它为一次分析任务提供稳定的约束边界、治理边界、生命周期边界，以及进入规范读取面的明确入口。

设计目标：

- 把 `session` 定义为分析容器，而不是工件 / 状态 / workflow / projection 的混合体
- 让所有 typed analysis intents 与规范证据对象都拥有明确的 session 归属
- 让 session-level hard constraints 与 mutable planning hints 显式分离
- 保持 `session` 本体轻量，不把命题中心主状态、answer readiness 或 workflow 协调逻辑直接内嵌进根对象
- 让 agent 能稳定判断何时继续写入当前 session、何时应 rollover 到新 session
- 与 `finding / proposition / assessment / state surface / context surface` 的设计保持一致

## 核心设计决策

### 1. `session` 是分析容器，不是新的证据层

`session` 处于规范证据链之外，但为其提供根边界：

`session -> artifact -> finding -> proposition -> assessment -> action proposal`

`session` 的职责是承载：

- 当前任务要解决什么问题
- 该任务在什么稳定 `scope / governance` 下进行
- 当前任务当前优先关注什么
- 当前任务是否仍可继续产生新的规范对象
- 当前有哪些权威读取面可进入

它不负责承载：

- 实时 support / oppose / gap membership
- 命题中心主决策骨架
- session 级回答就绪判断
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

### 4. `scope` 是 hard constraint，`focus` 是 mutable planning hint

这两个概念必须显式分离。

`scope` 回答：

- 这次分析任务稳定附加哪些非时间执行约束
- 哪些约束应自动注入支持的 query steps

`focus` 回答：

- 当前最优先看哪些 subject / proposition / question
- 在不改变 hard constraints 的前提下，当前 planning 该先推进什么

因此：

- `scope` 可以约束执行边界
- `focus` 只能影响 planning / projection 优先级
- `focus` 不得自动注入 step filter，不得与 enforced execution boundary 混写

### 5. `session` 的治理边界与证据语义分离

`governance` 的职责是约束“允许如何执行与读取”，不是定义 evidence semantics。

它可以表达：

- policy snapshot 或 policy refs
- budget
- approval mode
- data access constraints
- governance warnings

它不得表达：

- 命题是否成立
- 哪个 finding 属于 support / oppose
- evidence gap 的判断语义

治理风险可以阻止某些执行，但不应被折叠成判断层的 canonical gap。

### 6. `session` 生命周期与 evidence lifecycle 分离

`session` 生命周期回答的是“容器是否仍可接受新的 canonical writes”，不是 assessment 的状态机。

因此：

- `session.lifecycle.status` 与 `assessment.status` 必须分离
- `session` 关闭后仍可读取 state/context
- “足够回答用户”属于 agent 基于 state/context 的消费结论，而不是 `session` 本体字段
- `latest` / `live` 属于读取层语义，不是 `session` 本体上的 mutable flag family

### 7. `SessionStateSummary` 只承载读取入口，不承载摘要判断

`SessionStateSummary` 是 `session` 根对象上的最小状态入口，不是轻量决策对象。

它的职责是表达：

- 当前 session 的权威 state surface 入口在哪里

它不承载：

- blockers 摘要
- readiness 摘要
- top focus proposition 排名
- 任何需要 consumer 据此直接做判断的统计

## Schema Position

`session` 在 agent interaction contract 中位于根容器层，而不是 state surface、context surface 或 projection surface 的替代物。

关系如下：

- action surface 负责执行 typed intents
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
  scope: SessionScope;
  focus: SessionFocus | null;
  governance: SessionGovernance;
  lifecycle: SessionLifecycle;
  coordination: SessionCoordination;
  state_summary: SessionStateSummary | null;
  created_at: string;
  updated_at: string;
  schema_version: string;
};

type SessionGoal = {
  question: string;
  success_criteria: string[] | null;
};

type SessionScope = {
  constraints: SessionConstraintSet;
};

type SessionConstraintSet = {
  items: SessionConstraint[];
};

type SessionConstraint = {
  field_ref: SemanticFieldRef;
  operator: "eq" | "in" | "is_null";
  value:
    | string
    | number
    | boolean
    | null
    | Array<string | number | boolean>;
};

type SemanticFieldRef = {
  semantic_ref_type: "entity_key" | "metric_dimension";
  entity_id: string;
  field_name: string;
};

type SessionFocus = {
  focus_subjects: FocusSubject[];
  focus_proposition_refs: PropositionRef[] | null;
  focus_questions: string[] | null;
  priority_hints: string[] | null;
  last_reasoned_at: string | null;
};

type FocusSubject = {
  metric: string | null;
  entity: string | null;
  slice: Record<string, string | number | boolean | null>;
  grain: "hour" | "day" | "week" | "month" | null;
};

type SessionGovernance = {
  policy_refs: GovernancePolicyRef[] | null;
  approval_mode: "auto" | "explicit_approval";
  budget: SessionBudget | null;
  data_access_constraints: string[] | null;
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
  status: "open" | "closing" | "closed";
  closed_reason:
    | "answered"
    | "abandoned"
    | "rolled_over"
    | "governance_terminated"
    | "expired"
    | null;
  closed_at: string | null;
  rollover_from_session_id: string | null;
};

type SessionCoordination = {
  write_status: "idle" | "write_in_progress" | "refresh_in_progress" | "blocked";
  last_write_at: string | null;
};

type SessionStateSummary = {
  state_view_ref: SessionStateViewRef;
};

type SessionStateViewRef = {
  session_id: string;
  view_type: "session_state_view";
};

type PropositionRef = {
  session_id: string;
  proposition_id: string;
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

- `goal.question` 是任务目标，不是原始聊天记录归档容器
- `goal.success_criteria` 是可选的完成判据提示，不替代 assessment 或 state surface 的规范依据
- `goal` 可以帮助 agent 判断何时停止，但不能反向定义 canonical evidence semantics

#### scope

`scope` 是 session 的 hard constraint。

它决定：

- 哪些非时间结构化约束属于稳定任务边界
- 哪些约束应自动注入支持的 query steps

要求：

- `scope` 变更必须被视为边界变更，而不是普通 planning update
- step 执行只能继承 `scope.constraints` 这类 hard constraints，不能继承 `focus`
- session root 不定义 `time_scope`；时间边界只能进入 step `time_scope`
- `scope.constraints.items[*].field_ref` 必须是可解引用的 typed ref，不得退化为自由字段名或物理列名
- session root 不定义 predicate AST；复杂布尔过滤只允许出现在 step `scope.predicate`
- `scope` 不得静默扩张为“任意补查都可以”

#### SessionConstraintSet

`SessionConstraintSet` 是 session root 上唯一允许的执行硬约束。

规则：

- `items` 中的每个约束都必须绑定到 `SemanticFieldRef`
- 这些约束最终都要能稳定映射到 Factum 语义层对象，而不是物理 source object
- v1 只支持 `eq`、`in`、`is_null` 三类原子约束，避免在 session root 引入复杂表达式树
- `items = []` 表示当前 session 没有额外 session-level 非时间约束

#### SemanticFieldRef

`SemanticFieldRef` 是 session 约束的权威目标引用。

要求：

- `semantic_ref_type = "entity_key"` 用于绑定实体键字段
- `semantic_ref_type = "metric_dimension"` 用于绑定语义指标允许的维度字段
- `entity_id` 必须引用已存在的 `semantic_entities.entity_id`
- `field_name` 必须是该语义对象下稳定可解析的字段名
- 不允许直接引用 `source_objects.object_id`、物理列名或自由文本 locator

#### focus

`focus` 是 mutable planning hint。

它可以表达：

- 当前优先关注哪些 subjects
- 当前优先查看哪些 propositions
- 当前想回答哪些子问题

要求：

- `focus` 可以更新、重排或清空
- `focus` 的变化不应单独触发 canonical evidence 重算
- `focus` 不得改变 `scope`
- `focus` 不得被执行层当作强制 filter 自动注入

#### governance

`governance` 定义 session 的治理边界。

要求：

- `policy_refs` 或其等价快照只用于审计与执行限制
- `approval_mode` 只表达是否需要额外批准，不表达 evidence strength
- `warnings` 是 governance warning，不是 evidence gap
- `data_access_constraints` 可以阻断某些读取，但不得重写判断层对象语义

#### lifecycle

`lifecycle` 只表达分析容器自身的生命周期。

规则：

- `open` 表示可继续接受 canonical writes
- `closing` 表示正在进入结束态，但不要求 state/context 不可读
- `closed` 表示不再接受新的 canonical writes
- `closed_reason` 解释容器为何关闭，不解释 proposition 是否成立

若 `status = "closed"`：

- `closed_at` 必须非空
- state/context 仍必须可读
- 历史 refs 不得失效为不可解释状态

#### coordination

`coordination` 是最小协调语义，不是实现细节泄漏。

它回答：

- 当前是否存在写入进行中
- 当前读取面是否可能暂时滞后于最新写入
- 当前 session 是否暂时无法继续 canonical write

它不得承载：

- lease owner
- 锁 token
- job executor 内部信息
- 并发实现协议
- blocker 的细分原因分类

`write_status` 的消费语义固定为：

- `idle`：当前无已知写入中的协调信号
- `write_in_progress`：canonical write 正在进行，读取仍可继续，但可能暂时看不到最新结果
- `refresh_in_progress`：读取面物化可能滞后于最新 canonical evidence
- `blocked`：当前 canonical write 无法继续；具体原因应通过执行面或治理面另查，不在本字段内编码

#### state_summary

`state_summary` 是 session 根对象上的最小状态入口。

要求：

- 它必须指向稳定的 `state_view_ref`
- 它不承载高信号摘要计数
- 它不替代完整 `SessionStateView`
- consumer 不得基于它直接判断 blockers、coverage 或 readiness

若为 `null`，只表示 state surface 入口尚未物化或暂不可得；不等价于 session 无状态。

### SessionStateSummary

`SessionStateSummary` 是 session 根对象上的最小状态入口对象。

#### state_view_ref

`state_view_ref` 是进入完整 `SessionStateView` 的权威入口。

要求：

- 不得退化为裸字符串 locator
- 不得把 projection variant 编入 canonical ref identity

## 生命周期与写入协调

### 创建

创建 session 时，先建立 `AnalysisSession` 根对象，再允许后续 typed intents 写入属于该 session 的 canonical evidence。

创建后最小保证：

- `session_id` 已稳定
- `scope / governance / lifecycle` 已确定
- `state_summary` 可以暂时为 `null`

### 写入

typed intent 成功执行后产生的 `artifact / finding / proposition / assessment / action proposal` 都必须显式归属当前 `session_id`。

规则：

- `focus` 更新不是 canonical evidence write
- `state_summary` 的刷新属于读取面或物化层更新，不改写下游 evidence semantics
- 任何会改变 `scope` hard constraints 的修改，都不应被视为普通增量写入
- session `constraints` 注入支持的 query steps 时，目标位置固定为 step `scope.constraints`
- 若 step 自身的 `scope.constraints` 与 session `constraints` 在同一 `field_ref` 上给出冲突值，必须报错而不是静默覆盖

### 关闭

当 session 进入 `closed`：

- 不得新增新的 canonical evidence objects
- 允许读取 `state_summary`、`SessionStateView` 与 `PropositionContextView`
- 历史 evidence objects 仍保留其可审计性

### rollover

若任务边界发生实质变化，例如：

- session-level 约束集合改变
- 治理边界改变到会重写解释边界

则应优先新建 session 或执行 rollover，而不是原地静默改写旧 `session`。

`rollover_from_session_id` 只表达容器级衔接，不表达 proposition / assessment 的跨 session 继承。

## 读取入口与默认消费方式

agent 默认消费顺序应为：

1. 先读取 `AnalysisSession`
2. 通过 `AnalysisSession` 获取 session-level constraints、治理边界、生命周期与 state surface 入口
3. 需要全局判断时进入 `SessionStateView`
4. 需要解释单 proposition 时进入 `PropositionContextView`

其中：

- `session` 根对象不承担全局判断职责
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
- 用 `focus` 充当 step 执行过滤器
- 在 `session` 根对象重新引入 readiness summary、report summary 或 recommendation summary
- 把 governance warning 折叠成 `evidence_gap`
- 在 `coordination` 中暴露内部锁、lease 或 job 实现
- 让 `session` 充当 projection 容器、report 容器或 workflow brain

## 阅读建议

推荐顺序：

1. 先读 [`agent-interaction-contract-principles.md`](agent-interaction-contract-principles.md) 理解三层交互面与 `scope / focus` 分离原则
2. 再读本文理解分析容器根边界
3. 再读 [`state-surface-schema.md`](state-surface-schema.md) 理解 session 级主读取面
4. 最后读 [`context-surface-schema.md`](context-surface-schema.md) 理解 proposition 级局部最小闭包
