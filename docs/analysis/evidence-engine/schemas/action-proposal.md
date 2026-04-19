# Action Proposal Schema

本文档定义 Factum 动作支持层中 `action proposal` 的拟议类型契约。

状态：draft design。本文是规划中的规范 `action proposal` Schema 提案，不表示对应 HTTP endpoint 或持久化模型已经实现。

## 目的

`action proposal` 是 Factum 证据引擎面向 agent 的类型化动作支持对象（typed action-support object），用于把当前评估状态（`assessment`）暴露出的判断状态、证据缺口（evidence gap）和策略上下文（policy context），转化为结构化、可排序、可执行的下一步动作候选。

设计目标：

- 明确分离评估状态（`assessment`）与动作候选（`action proposal`）
- 让 agent 直接读取”下一步做什么最有价值”，而不是从解释（explanation）文本中反推动作
- 保持 proposal 为投影层规范对象（projection-layer canonical object），而不是核心证据状态
- 保持 proposal typed、可引用、可排序、可局部读取
- 让分析型 proposal 直接落到类型化分析意图契约（typed analysis intent contract），不暴露 raw SQL

## 核心设计决策

### 1. `action proposal` 是投影层规范对象（projection-layer canonical object）

`action proposal` 位于规范抽象链路（canonical abstraction chain）的最外层：

`artifact -> finding -> proposition -> assessment -> action proposal`

其职责是表达：

- 当前哪一个 assessment 最值得被继续推进
- 建议采取哪类结构化动作
- 该动作预计带来多大信息增益（information gain）、成本（cost）、影响（impact）与紧急性（urgency）

它不负责表达：

- 新事实本体
- proposition 判断语义（judgment semantics）
- assessment 当前状态本身

因此：

- `action proposal` 不进入核心规范分析状态
- 但它也不是无标识（identity）的临时 DTO
- 它应被视为基于规范状态和显式策略上下文（policy context）生成的稳定投影对象

附加边界：

- `action proposal` 属于命题中心状态面（proposition-centered state surface）的规划快捷方式（planning shortcut），不属于主判断骨架（judgment spine）
- agent 不读取 proposal 时，仍必须能够仅依赖命题（`proposition`）+ 最新评估（latest assessment）+ 证据缺口（gaps）+ 事实单元（findings）完成下一步决策

### 2. `action proposal` 绑定单个主 assessment

v1 中每个 proposal 必须有且只有一个 `primary_assessment_ref`。

原因：

- proposal 的优先级、价值和执行目标都必须围绕一个明确的当前判断状态计算
- 多 assessment 聚合 proposal 会把 identity、排序和执行后回溯都显著复杂化
- agent 仍可通过 `related_assessment_refs` 读取辅助上下文，但不改变主目标唯一性

### 3. `action proposal` 必须是 typed next step

proposal 不只是“建议做什么”的文本标签，而必须提供结构化 next step contract。

v1 规则：

- `investigate` 与 `validate` proposal 必须落到 typed analysis intent request
- `monitor`、`mitigate`、`escalate` proposal 必须落到各自的 typed policy action payload
- 不允许把自由文本、自然语言计划或 raw SQL 当作规范执行契约

### 4. `action proposal` 是不可变快照

proposal 一旦生成即不可原地修改。

标识边界绑定：

- `session_id`
- `primary_assessment_ref`
- proposal payload 中决定动作语义的字段
- `proposal_context`
- `policy_version`

要求：

- 同一 assessment snapshot、相同 payload 语义、相同 policy context 重读时得到相同 `action_proposal_id`
- assessment snapshot 变化后，即使动作类别相同，也必须生成新的 proposal
- proposal 的采纳、执行、过期等 workflow 事件不得回写 proposal 本体

### 5. proposal 排序使用多轴，而不是单一黑盒分

v1 在 base schema 中标准化以下 planning axes：

- `information_gain`
- `execution_cost`
- `urgency`
- `expected_impact`

同时输出稳定的 `priority_rank`，用于默认排序和 top-k 截断。

这样做的原因是：

- agent 可以自己按不同任务目标重排
- policy 层可以给出默认排序，而不把排序语义压成不可解释总分
- consumer 不必反推“为什么这个 proposal 更靠前”

### 6. policy / session context 必须显式进入 contract

proposal 可以依赖：

- session goal
- operator risk budget
- action policy profile

但这些上下文必须显式写入 `proposal_context`，不能由实现层隐式读取后静默影响输出。

## Schema Position

职责分工：

- `finding`：确定性事实单元
- `proposition`：待评估命题
- `assessment`：当前评估快照
- `action proposal`：面向 agent 的动作候选

其中：

- `assessment` 是 proposal 的主输入
- `action proposal` 是 canonical projection object，不回写 judgment layer
- agent 必须可以完全绕过 proposal，仅依赖 `proposition + assessment` 自主规划

proposal 因此只能排在 assessment 之后刷新，不能领先于判断层成为 agent 的首要状态接口。

action proposal 中承载 relation semantics 的字段解释，以 [`evidence-engine/graph-and-reference-semantics.md`](../graph-and-reference-semantics.md) 为准：

- `primary_assessment_ref` 承载 `action_proposal -> assessment` 的 `targets_primary_assessment`
- `related_assessment_refs` 承载 `action_proposal -> assessment` 的 `relates_assessment`
- `target_proposition_ref` 承载 `action_proposal -> proposition` 的 `targets_proposition`
- `rationale.served_gap_refs` 与各 subtype payload 中的 `closes_gap_refs` 承载 `action_proposal -> evidence_gap` 的 `serves_gap`

这些 edge 都属于 proposal projection 语义，不回写 judgment layer，也不把 proposal 提升为核心 evidence node。

proposal 的生成、排序、identity、refresh trigger 与 no-op contract，以 [`proposal-policy-engine.md`](../proposal-policy-engine.md) 为准；本文只固定 proposal object 自身的 schema 与边界。

## Typed Schema

```ts
type ActionProposal =
  | InvestigateProposal
  | ValidateProposal
  | MonitorProposal
  | MitigateProposal
  | EscalateProposal;

type ActionProposalBase = {
  action_proposal_id: string;
  action_kind:
    | "investigate"
    | "validate"
    | "monitor"
    | "mitigate"
    | "escalate";
  session_id: string;
  primary_assessment_ref: AssessmentRef;
  related_assessment_refs: AssessmentRef[];
  target_proposition_ref: PropositionRef;
  proposal_context: ProposalContext;
  priority_axes: ProposalPriorityAxes;
  priority_rank: number;
  rationale: ProposalRationale;
  created_at: string;
  policy_version: string;
  schema_version: string;
};

type AssessmentRef = {
  assessment_id: string;
  proposition_id: string;
  snapshot_seq: number;
};

type PropositionRef = {
  session_id: string;
  proposition_id: string;
};

type EvidenceGapRef = {
  gap_id: string;
  proposition_id: string;
};

type FindingRef = {
  session_id: string;
  finding_id: string;
};

type ProposalContext = {
  session_goal: SessionGoal | null;
  risk_budget: RiskBudget | null;
  policy_profile: string;
};

type SessionGoal =
  | "explain_change"
  | "validate_hypothesis"
  | "triage_anomaly"
  | "monitor_risk"
  | "prepare_escalation"
  | "other";

type RiskBudget = "minimal" | "low" | "medium" | "high";

type ProposalPriorityAxes = {
  information_gain: PriorityGrade;
  execution_cost: PriorityGrade;
  urgency: PriorityGrade;
  expected_impact: PriorityGrade;
};

type PriorityGrade =
  | "very_low"
  | "low"
  | "medium"
  | "high"
  | "very_high";

type ProposalRationale = {
  summary: string;
  driver_tokens: string[];
  served_gap_refs: EvidenceGapRef[];
  expected_assessment_outcomes: string[];
  notes: string[];
};

type IntentRequestRef =
  | ObserveRequest
  | CompareRequest
  | DecomposeRequest
  | CorrelateRequest
  | DetectRequest
  | TestRequest
  | ForecastRequest
  | AttributeRequest
  | DiagnoseRequest
  | ValidateRequest;

type PolicyActionBase = {
  action_channel: "agent" | "operator" | "system";
  requires_human_approval: boolean;
};

type InvestigateProposal = ActionProposalBase & {
  action_kind: "investigate";
  payload: {
    next_intent: IntentRequestRef;
    expected_output: "finding" | "finding_set" | "assessment_update";
    closes_gap_refs: EvidenceGapRef[];
  };
};

type ValidateProposal = ActionProposalBase & {
  action_kind: "validate";
  payload: {
    next_intent: IntentRequestRef;
    validation_target:
      | "supporting_evidence"
      | "opposing_evidence"
      | "data_quality_risk"
      | "comparability_risk"
      | "rule_precondition";
    closes_gap_refs: EvidenceGapRef[];
  };
};

type MonitorProposal = ActionProposalBase & {
  action_kind: "monitor";
  payload: PolicyActionBase & {
    monitoring_target: "metric" | "slice" | "proposition";
    monitor_window: {
      start: string;
      end: string;
      grain: "hour" | "day" | "week" | "month" | null;
    } | null;
    trigger_condition_tokens: string[];
  };
};

type MitigateProposal = ActionProposalBase & {
  action_kind: "mitigate";
  payload: PolicyActionBase & {
    mitigation_target:
      | "data_quality_risk"
      | "comparability_risk"
      | "business_risk"
      | "forecast_risk";
    mitigation_scope: "session" | "metric" | "slice" | "external";
    required_preconditions: string[];
  };
};

type EscalateProposal = ActionProposalBase & {
  action_kind: "escalate";
  payload: PolicyActionBase & {
    escalation_target: "operator" | "owner" | "incident_channel";
    escalation_reason:
      | "critical_risk"
      | "blocked_resolution"
      | "high_impact_change"
      | "policy_requirement"
      | "other";
    required_context_refs: ProposalContextRef[];
  };
};

type ProposalContextRef =
  | { kind: "proposition"; proposition_ref: PropositionRef }
  | { kind: "assessment"; assessment_ref: AssessmentRef }
  | { kind: "finding"; finding_ref: FindingRef }
  | { kind: "evidence_gap"; gap_ref: EvidenceGapRef };
```

## 公共字段语义

### action_proposal_id

`action_proposal_id` 是单个 proposal snapshot 的 canonical identifier。

必须使用以下输入生成：

- `session_id`
- `action_kind`
- `primary_assessment_ref`
- `target_proposition_ref`
- `proposal_context`
- payload 中决定动作语义的字段

注：`target_proposition_ref` 与 `primary_assessment_ref.proposition_id` 语义冗余，但两者均须显式纳入，以保证 identity 自描述。

禁止把以下字段作为 identity 输入：

- `schema_version`
- `policy_version`（若只改变排序实现或解释文本，而不改变动作语义边界）
- `priority_rank`
- explanation 文案排序位置
- consumer projection 截断结果
- workflow 执行状态

目标是让 proposal identity 由动作语义和输入状态决定，而不是由显示位置决定。

补充规则：

- `policy_version` 只有在其变化明确改变 proposal payload 语义、引用闭包或可执行动作边界时，才应进入 `action_proposal_id` 生成输入
- 若 `policy_version` 只改变排序实现、阈值细节或 explanation 文案，而不改变动作语义，应保留为独立 version boundary，不应导致既有 proposal identity 抖动

### primary_assessment_ref

`primary_assessment_ref` 是 proposal 的主规划锚点。

要求：

- 必填
- 必须指向同一 `session_id` 下的 assessment snapshot
- `target_proposition_ref` 必须与该 assessment 对应的 proposition 一致
- proposal 不允许脱离 assessment 独立存在

### related_assessment_refs

`related_assessment_refs` 用于暴露与当前 proposal 强相关、但不是主目标的 assessment。

规则：

- 可为 `[]`
- 不允许重复包含 `primary_assessment_ref`
- 仅允许引用同一 `session_id` 下的 assessment snapshots
- 允许引用同一 proposition 的历史 assessment，或与主 assessment 强相关的其他 proposition assessment；但不得跨 session、不得引用未来 snapshot
- 不得用来绕过“主目标唯一”的设计约束

典型用途：

- 某个 mitigation proposal 同时受多个相关风险 assessment 影响
- 某个 validate proposal 需要查看上游 change 与 decomposition 两个 assessment 的上下文

### proposal_context

`proposal_context` 记录影响 proposal 生成的显式策略输入。

规则：

- `session_goal = null` 表示当前 proposal 未绑定显式 session goal，不表示 unknown
- `risk_budget = null` 表示当前 proposal 不适用 risk budget 约束，不表示系统尚未计算
- `policy_profile` 必填；即使使用默认策略也必须显式写出
- 任何会改变 proposal 生成或排序结果的上下文都必须进入该字段，而不是作为隐式运行时参数

### priority_axes

`priority_axes` 是面向 agent 的 planning dimensions。

v1 解释：

- `information_gain`：执行后预计能消除多少关键不确定性
- `execution_cost`：执行成本与资源占用；值越高表示成本越高
- `urgency`：如果当前不执行，风险或机会窗口流失的紧迫程度
- `expected_impact`：若该 proposal 成功执行，对判断质量或风险处置的潜在影响

要求：

- 四个轴都必须 total，不允许 `null`
- 若某轴难以精确判断，应使用保守枚举，而不是缺省
- consumer 可以自定义排序，但默认排序必须可由这些轴和 `priority_rank` 解释

推导边界：

- `priority_axes` 只能由 canonical inputs 推导：`primary_assessment_ref`、可解引用的 gaps / findings / proposition 闭包、`proposal_context`、`policy_profile`
- 不得由自由文本 summary、展示位置或 consumer-side top-k 结果反向决定
- `information_gain` 主要反映 proposal 对 closing gaps、减少判断不确定性的预期能力
- `execution_cost` 主要反映动作资源消耗、等待成本与需要的外部协作成本；值越高表示越贵
- `urgency` 主要反映风险暴露、机会窗口流失、SLA 或 policy deadline 压力
- `expected_impact` 主要反映动作成功后对 assessment 收敛、风险处置或 operator 决策的潜在影响

### priority_rank

`priority_rank` 是当前 policy 计算出的稳定综合排序键。

规则：

- 同一批 proposal 中按 `priority_rank ASC` 排序，数值越小表示优先级越高
- 若综合排序相同，推荐再按 `created_at ASC`、`action_proposal_id ASC`
- `priority_rank` 可因 policy version 或 assessment 变化而变化，但这会形成新的 proposal snapshot，而不是原地改写

推导约束：

- `priority_rank` 必须由显式 policy 规则合成，而不是人工黑盒打分
- policy 文档或实现至少应回答：四个 `priority_axes` 如何进入排序、是否存在 veto / cap / floor 规则、哪些 `driver_tokens` 对排序结果负责
- 若 policy 规定某些 proposal family 在特定 `risk_budget` 或 gap severity 下必须提级或降级，该覆盖规则必须显式写入 policy，而不是隐含在实现分支中
- `rationale.driver_tokens` 必须能解释 `priority_rank` 的主要驱动项；`summary` 只作阅读摘要，不承担排序主语义

### rationale

`rationale` 是 proposal 的结构化理由。

要求：

- `summary` 保持短文本摘要，不承载主语义
- `driver_tokens` 应为稳定、可比较的 machine-readable tokens
- `served_gap_refs` 表示该 proposal 直接服务的 gaps；`[]` 表示该 proposal 当前不直接以 gap closure 为主要目标
- `expected_assessment_outcomes` 表示预期能推动的 assessment 变化；可为 `[]`
- `notes = []` 表示当前无额外补充说明

`driver_tokens` 至少应能覆盖以下任一类可审计驱动：

- primary assessment gap memberships 中的 severity / blocking pressure
- policy overrides
- expected information gain
- execution dependency or human approval cost

## Subtype 语义

### Investigate Proposal

`investigate` 用于围绕当前 assessment 继续获取信息。

要求：

- `payload.next_intent` 必须是合法 typed analysis intent request
- 该 intent 应优先服务于发现新 findings，而不是直接生成 narrative
- `expected_output` 只描述结构化结果形态，不描述人类报告文案
- `closes_gap_refs = []` 表示该动作不直接声明要关闭某个现有 gap，但仍可用于探索新证据

典型用途：

- 对某个 change proposition 做进一步 `decompose`
- 对 anomaly candidate 做局部 `observe` 或 `detect`
- 对相关指标做 `correlate`

### Validate Proposal

`validate` 用于验证当前支持、反驳、质量风险或规则前提。

要求：

- 必须带 `validation_target`
- `payload.next_intent` 必须能解释“验证什么”
- `closes_gap_refs` 只表示目标缺口，不承诺一定关闭

### Monitor Proposal

`monitor` 用于持续观察风险、命题或指标，而不是立即获取新证据。

要求：

- `trigger_condition_tokens` 应是可稳定比较的触发条件，而不是自由文本
- `monitor_window = null` 表示持续监控但当前没有固定窗口约束
- 若 `monitor_window` 非 `null`，其窗口语义遵循统一时间窗口规则；`grain = null` 表示窗口已给定但不额外要求消费粒度

### Mitigate Proposal

`mitigate` 用于降低当前 assessment 暴露出的业务、质量或可比性风险。

要求：

- mitigation 必须面向风险处置，而不是重做 assessment
- `required_preconditions = []` 表示当前无额外前置条件
- 不允许把需要进一步分析才能执行的动作伪装成 mitigation；那应建模为 `investigate` 或 `validate`

### Escalate Proposal

`escalate` 用于在当前 assessment 已达到特定风险或策略阈值时上送。

要求：

- `escalation_reason` 必须结构化
- `required_context_refs` 至少能让 consumer 回到相关 proposition / assessment / finding / gap 闭包
- `required_context_refs = []` 仅在 escalation payload 自身已可由 `primary_assessment_ref` 和 `target_proposition_ref` 构成完整上下文时允许；否则必须显式列出额外 typed refs

## Agent Consumption Contract

### 查询轴

proposal 至少应支持按以下轴查询：

- `session_id`
- `action_kind`
- `primary_assessment_ref.assessment_id`
- `target_proposition_ref.proposition_id`
- `proposal_context.policy_profile`
- `priority_rank`
- `priority_axes.information_gain`
- `priority_axes.execution_cost`
- `priority_axes.urgency`
- `priority_axes.expected_impact`

### 默认排序

建议默认排序：

- `priority_rank ASC`
- `created_at ASC`
- `action_proposal_id ASC`

### 稳定截断规则

- 任何 top-k proposal view 必须先按默认排序建立全序，再做前缀截断
- 截断只能压缩成员集合，不得回写 proposal identity、priority axes 或 ref membership
- 若 consumer 需要按自定义目标重排，可在 projection 层做二次排序；canonical default list 仍必须保持稳定顺序
- 并列 proposal 不得依赖运行时非确定性顺序

### 局部最小闭包读取

围绕 proposal 的最小闭包推荐为：

```ts
type ActionProposalFocusView = {
  proposal: ActionProposal;
  primary_assessment: Assessment;
  related_assessments: Assessment[];
  target_proposition: Proposition;
  relevant_findings: Finding[];
  served_gaps: EvidenceGap[];
};
```

约束：

- `primary_assessment` 必须可解引用；否则 proposal 应被视为不可读取
- `related_assessments` 可为 `[]`
- `served_gaps` 来自 `rationale.served_gap_refs` 的 typed 解引用；若 `served_gap_refs = []`，consumer 可回退到 `primary_assessment` 当前 gap 集作为背景，但不得把该回退结果回写 proposal
- 若 consumer 需要当前 gap 的 `blocking` / `severity`，必须从 `primary_assessment.gap_memberships` 读取，而不是假定这些字段属于 `EvidenceGap`
- `relevant_findings` 仍来自 assessment / inference record 的 live evidence closure，而不是由 proposal 发明
- `target_proposition`、`primary_assessment` 与 `served_gaps` 的 ref membership 必须彼此一致；若出现 cross-session 或 proposition mismatch，应视为非法 canonical read

## 与 Finding / Proposition / Assessment / Workflow 的边界

### Action Proposal 和 Finding 的边界

`finding` 回答的是“系统确定知道什么”。

`action proposal` 回答的是“下一步做什么最有价值”。

因此 proposal 中不得写入：

- 新 finding 事实本体
- 未在规范状态中存在的观测结论
- 以 proposal 反向确认某个事实

### Action Proposal 和 Proposition 的边界

`proposition` 回答的是“系统要判断什么”。

proposal 可以引用 proposition，但不得改写 proposition judgment semantics。

因此 proposal 中不得写入：

- proposition payload 的镜像副本
- 新的 proposition identity 规则
- 以动作优先级覆盖命题语义

### Action Proposal 和 Assessment 的边界

`assessment` 回答的是“当前判断到什么程度”。

proposal 只能基于 assessment 生成，不得把 assessment 字段回写为 proposal 的一部分。

因此 proposal 中不得承担：

- `status`
- `confidence_grade`
- 实时证据归属
- gap state 本体

### Action Proposal 和 Workflow Object 的边界

proposal 本体不承载：

- `adoption_status`
- `execution_status`
- `executed_at`
- `stale_reason`

若需要记录 agent 或 operator 是否采纳、何时执行、是否失败，应定义独立 workflow objects，例如：

- `action_decision`
- `action_execution`

这些对象不属于 v1 canonical proposal schema。

## Test Cases

后续实现至少应满足以下 schema-level 验收场景：

1. 同一 assessment snapshot、相同 payload 语义、相同 policy context 重读时，`action_proposal_id` 稳定。
2. assessment snapshot 更新后，即使 `action_kind` 相同，也生成新的 `action_proposal_id`。
3. proposal 必须有且只有一个 `primary_assessment_ref`。
4. proposal 可以 `related_assessment_refs = []`，但不得用其替代主目标。
5. `investigate` / `validate` proposal 必须能解析到合法 typed analysis intent request。
6. proposal 不得包含 raw SQL 或自由文本动作 DSL。
7. `priority_axes` 四个轴都必须存在，且不得为 `null`。
8. `priority_rank` 必须可由显式 policy 规则和 `driver_tokens` 解释；若排序语义变化导致结果变化，必须形成新的 proposal snapshot。
9. consumer 可以完全绕过 proposal，仅依赖 proposition + assessment 规划下一步。
10. proposal focus view 中的 `relevant_findings` 必须来自 assessment closure，而不是 proposal 自造。
11. proposal 不得内联 adopted/executed/stale 等 workflow 状态字段。
12. 不同 session 中语义相同的 proposal 不复用 ID。
13. `target_proposition_ref`、`served_gap_refs`、`required_context_refs` 的 ref 形状必须与相邻 canonical schema 一致。
14. top-k proposal view 必须按稳定全序截断，不得因 consumer 侧重排改变 canonical 默认顺序。
15. `policy_version` 若只改变 explanation 或实现细节，不得单独触发新的 `action_proposal_id`。

负向场景至少覆盖：

1. 不允许缺少 `primary_assessment_ref` 的 proposal 进入 canonical contract。
2. 不允许 `target_proposition_ref` 与 `primary_assessment_ref` 指向不同 proposition。
3. 不允许 `related_assessment_refs` 重复包含 `primary_assessment_ref`。
4. 不允许 `investigate` / `validate` proposal 的 `next_intent` 退化成未约束自由文本。
5. 不允许通过修改 `priority_rank` 原地覆盖既有 proposal。
6. 不允许未显式进入 `proposal_context` 的策略输入静默改变 proposal identity 或排序。
7. 不允许 `served_gap_refs` / `closes_gap_refs` / `required_context_refs` 退化成裸字符串 locator。
8. 不允许 `related_assessment_refs` 跨 session、引用未来 snapshot 或形成不可解释的闭包。

## 非目标

v1 不包含以下能力：

- 多 primary assessments 的组合 proposal
- 跨 session proposal registry
- 把 action adoption / execution lifecycle 混入 proposal 本体
- 用自由文本 recommendation 取代 typed next step
- 为现有 recommendation persistence 设计兼容层
- 在 proposal 层发明新的 facts、propositions 或 assessments
