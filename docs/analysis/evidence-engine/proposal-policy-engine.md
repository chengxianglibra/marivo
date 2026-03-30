# Proposal Policy Engine

本文档定义 Evidence Engine 中 `latest_assessment -> action proposal[]` 的生成、排序与刷新契约。

状态：draft design。本文是 [`runtime-pipeline.md`](runtime-pipeline.md) 与 [`schemas/action-proposal.md`](schemas/action-proposal.md) 之间的实现级补充，负责把 proposal policy engine 的输入边界、候选生成、排序、identity 与 no-op 规则固定为可直接实现的 deterministic contract。

## 目的

固定以下问题的统一答案：

- action proposal refresh 的唯一 authority input 是什么
- proposal refresh 在 canonical pipeline 中何时运行
- assessment 特征如何确定性映射到 proposal family 与 typed payload
- `priority_axes` 与 `priority_rank` 如何由显式 policy 规则计算
- proposal identity、去重、排序、no-op 与 supersession 如何解释
- proposal engine 如何保持为纯 projection，而不回写 judgment semantics

## 主题位置

proposal policy engine 位于 canonical abstraction chain 的最后一段：

`artifact -> finding -> proposition -> assessment -> action proposal`

职责分工固定为：

- `assessment` 回答“当前判断到什么程度”
- proposal policy engine 回答“基于当前 latest assessment，下一步哪些 typed actions 最值得做”
- `action proposal` 是 engine 的 committed projection output，不是新的 judgment state

本文只定义 proposal refresh contract。

本文不定义：

- assessment recompute 的 rule family 执行过程
- typed analysis intent 的外部 HTTP wire shape
- action adoption / execution / stale tracking 的 workflow object
- UI narrative、summary text 或 top-k projection 文案

assessment recompute 仍以 [`inference-and-gap-engine.md`](inference-and-gap-engine.md) 为准；proposal 对象字段仍以 [`schemas/action-proposal.md`](schemas/action-proposal.md) 为准。

## Fixed Design Decisions

### 1. proposal refresh 只消费 committed latest assessment

proposal policy engine 的 authority inputs 只允许来自：

- target proposition 的 committed `latest_assessment`
- 该 assessment 对应 proposition
- 该 assessment closure 中可解引用的 findings / gaps / inference records
- 显式 `proposal_context`
- 显式 `policy_profile` 与 `policy_version`

不允许读取：

- UI projection、summary text、recommendation text
- 未提交 assessment / candidate outputs
- 其他 session 的 canonical objects
- 模型生成的自由文本动作建议
- workflow execution state

因此：

- proposition 尚未形成 committed `latest_assessment` 时，不得刷新 proposal
- proposal refresh 失败不得回写 assessment，也不得伪造 judgment output

### 2. proposal engine 是 assessment commit 之后的纯投影步骤

canonical pipeline 中固定顺序：

1. assessment recompute
2. assessment snapshot commit / no-op
3. latest assessment selection
4. proposal refresh
5. proposal read surface exposure

proposal engine 不能领先于 judgment layer 运行，也不能把自己的输出重新作为 assessment recompute 的 authority input。

### 3. candidate generation 必须 deterministic，且规则映射优先

v1 中 proposal candidate generation 必须由显式规则映射完成。

允许使用的稳定输入包括：

- `assessment_type`
- `status`
- `confidence_grade`
- `gap_memberships`
- `confidence_rationale`
- assessment closure 中的 committed findings / records
- proposition subject / origin / anchor
- `proposal_context`

不允许：

- 让模型自由决定 proposal family
- 用 explanation 文本反推动作
- 输出 raw SQL 或自由文本动作 DSL

模型若存在，只能用于 explanation 文案，不得进入 canonical proposal decision path。

### 4. 每个 proposal 必须绑定单个 primary assessment

proposal engine 对单个 proposition refresh 时，可以生成 `0..N` 个 proposals，但每个 proposal：

- 必须有且只有一个 `primary_assessment_ref`
- 必须与目标 proposition 一致
- 不得通过 `related_assessment_refs` 绕过主目标唯一性

若需要跨 assessment 辅助上下文，只能通过 `related_assessment_refs` 暴露，不改变 primary ownership。

### 5. proposal payload 必须是 typed next step

proposal family 到 payload 的最小 contract 固定为：

- `investigate` / `validate`：必须生成合法 typed analysis intent request
- `monitor`：必须生成 typed monitoring payload
- `mitigate`：必须生成 typed mitigation payload
- `escalate`：必须生成 typed escalation payload

若 candidate 无法生成合法 typed payload：

- 该 candidate 必须被丢弃
- 不允许退化为文本 recommendation

### 6. 排序必须由显式 policy 规则解释

proposal engine 先计算四个 `priority_axes`：

- `information_gain`
- `execution_cost`
- `urgency`
- `expected_impact`

再由显式 policy 规则合成 `priority_rank`。

固定要求：

- 四个轴都必须 total
- `priority_rank` 必须可由 `priority_axes`、`proposal_context` 与 `driver_tokens` 解释
- consumer 允许重排，但 canonical default order 必须稳定

### 7. proposal refresh 允许 no-op，但不允许原地改写

proposal 是 immutable projection snapshot。

因此 refresh 后只允许三种结果：

- 生成新的 proposal snapshots
- 复用语义等价的既有 proposal snapshots，并把本轮视为 no-op
- 输出空 proposal 集

不允许：

- 原地覆盖既有 proposal payload
- 原地修改 `priority_rank`
- 用 workflow 字段标记 stale / adopted / executed

## Typed Design Sketch

```ts
type ProposalPolicyInput = {
  session_id: string;
  proposition: Proposition;
  primary_assessment: Assessment;
  related_assessments: Assessment[];
  relevant_findings: Finding[];
  open_gap_memberships: GapMembershipEntry[];
  proposal_context: ProposalContext;
  policy_profile: string;
  policy_version: string;
  schema_version: "proposal_policy_input.v1";
};

type CandidateProposal = {
  action_kind:
    | "investigate"
    | "validate"
    | "monitor"
    | "mitigate"
    | "escalate";
  served_gap_refs: EvidenceGapRef[];
  driver_tokens: string[];
  expected_assessment_outcomes: string[];
  payload: CandidatePayload;
};

type CandidatePayload =
  | {
      action_kind: "investigate";
      next_intent: IntentRequestRef;
      expected_output: "finding" | "finding_set" | "assessment_update";
      closes_gap_refs: EvidenceGapRef[];
    }
  | {
      action_kind: "validate";
      next_intent: IntentRequestRef;
      validation_target:
        | "supporting_evidence"
        | "opposing_evidence"
        | "data_quality_risk"
        | "comparability_risk"
        | "rule_precondition";
      closes_gap_refs: EvidenceGapRef[];
    }
  | {
      action_kind: "monitor";
      action_channel: "agent" | "operator" | "system";
      requires_human_approval: boolean;
      monitoring_target: "metric" | "slice" | "proposition";
      monitor_window: MonitorWindow | null;
      trigger_condition_tokens: string[];
    }
  | {
      action_kind: "mitigate";
      action_channel: "agent" | "operator" | "system";
      requires_human_approval: boolean;
      mitigation_target:
        | "data_quality_risk"
        | "comparability_risk"
        | "business_risk"
        | "forecast_risk";
      mitigation_scope: "session" | "metric" | "slice" | "external";
      required_preconditions: string[];
    }
  | {
      action_kind: "escalate";
      action_channel: "agent" | "operator" | "system";
      requires_human_approval: boolean;
      escalation_target: "operator" | "owner" | "incident_channel";
      escalation_reason:
        | "critical_risk"
        | "blocked_resolution"
        | "high_impact_change"
        | "policy_requirement"
        | "other";
      required_context_refs: ProposalContextRef[];
    };

type RankedProposalCandidate = CandidateProposal & {
  priority_axes: ProposalPriorityAxes;
  priority_rank: number;
};

type ProposalRefreshResult = {
  primary_assessment_id: string;
  proposal_ids: string[];
  materialized_count: number;
  noop: boolean;
  schema_version: "proposal_refresh_result.v1";
};
```

约束：

- `primary_assessment` 必须是 committed latest assessment，而不是任意历史 snapshot
- `related_assessments` 可为 `[]`
- `relevant_findings` 只允许来自 primary assessment closure，而不是 recompute candidate set
- `open_gap_memberships` 取自主 assessment 当前 gap memberships 中 `status = open` 的条目
- `policy_profile` 必须与 `proposal_context.policy_profile` 一致，不允许双重 authority

## Refresh Trigger Contract

proposal refresh 只允许由以下事件触发：

1. 新 assessment snapshot 提交并成为 latest
2. latest assessment selection 变化
3. 显式 `proposal_context` 变化
4. `policy_profile` 或 `policy_version` 变化，且该变化进入 proposal 语义边界

其中：

- 仅 explanation 文案、排序实现细节或 UI projection 改动，不构成 canonical refresh trigger
- assessment recompute 若最终 no-op，且 latest selection 未变化，则 proposal refresh 默认不触发

## Input Closure Assembly

proposal engine 在单个 proposition 上运行时，必须按以下顺序组装输入：

### Phase 1. proposition / assessment anchor load

必须装载：

- target proposition
- committed latest assessment
- primary assessment 的 `assessment_type`
- primary assessment 的 `status`
- primary assessment 的 `confidence_grade`
- primary assessment 的 `gap_memberships`

若 proposition 与 assessment 无法互相解引用，应视为 canonical integrity error，而不是 policy miss。

### Phase 2. assessment closure load

必须装载 primary assessment closure 中的：

- `supporting_finding_ids`
- `opposing_finding_ids`
- `applied_inference_record_ids`
- `gap_memberships`

proposal engine 可以读取这些对象的 typed semantics，但不得重做 support / oppose / status resolution。

### Phase 3. context normalization

必须将以下外部上下文归一化到 `proposal_context`：

- session goal
- risk budget
- policy profile

任何会改变 candidate generation 或 ranking 的上下文，都必须显式进入 `proposal_context`；否则不得影响输出。

## Candidate Generation

proposal candidate generation 固定为“先判定 action family，再生成 typed payload”。

### Family selection baseline

v1 默认按以下优先思路建模：

- 当前 assessment 存在 blocking 或高严重度 gap，且最优下一步是补证据或补前提时，优先生成 `investigate` 或 `validate`
- 当前 assessment 已形成较明确 judgment，但需要持续观察风险暴露、窗口变化或回归情况时，生成 `monitor`
- 当前 assessment 暴露的是风险处置需求，而不是证据采集需求时，生成 `mitigate`
- 当前 assessment 已达到人工审批、事故沟通或策略上送阈值时，生成 `escalate`

同一 assessment 可产生多个 family 的 proposals，但每个候选都必须能解释其存在理由，且不能只是另一个 proposal 的文本变体。

### `investigate` generation

`investigate` 用于补充信息而不是直接验证既有判断。

候选输入通常包括：

- `missing_finding`
- `missing_slice`
- `missing_time_coverage`
- 需要进一步分解、观察、检测或相关性探索的不足判断

`next_intent` 选择原则：

- 优先选择能直接产出新 finding 的 intent family
- 必须与 `assessment_type`、proposition subject 与 served gaps 兼容
- 不得跨 subject 轴扩张到无关 metric/entity/slice

### `validate` generation

`validate` 用于验证支持证据、反驳证据或 gate 风险。

候选输入通常包括：

- `data_quality_risk`
- `comparability_risk`
- `missing_rule_precondition`
- 当前 latest assessment 中需要专门确认的 supporting / opposing evidence

`validation_target` 必须由 gap / record semantics 明确推导，不得由自由文本猜测。

### `monitor` generation

`monitor` 只在“当前不宜立刻补证据，但需要持续观察”的情况下生成。

典型触发包括：

- judgment 已基本收敛，但风险窗口仍持续
- 当前变化已被识别，需要后续回看是否继续恶化或恢复
- risk budget 或 policy profile 明确要求持续监控

### `mitigate` generation

`mitigate` 只在风险处置目标已经明确时生成。

要求：

- mitigation target 必须来自 canonical risk semantics
- 若仍需要额外分析来确认动作本身，则应优先生成 `investigate` / `validate`
- 不允许把“先看看数据”包装成 mitigation

### `escalate` generation

`escalate` 只在 policy threshold 明确命中时生成。

典型触发包括：

- critical severity
- blocked resolution
- high expected impact 且需要人工批准
- policy profile 明确规定的上送场景

`required_context_refs` 必须足以让 consumer 回到 proposition / assessment / finding / gap 闭包。

## Typed Payload Materialization

family selection 完成后，必须立即生成 subtype payload。

固定要求：

- payload 只允许引用 canonical typed refs
- `investigate` / `validate` 的 `next_intent` 必须满足当前 intent contract
- `closes_gap_refs` / `served_gap_refs` 可以为 `[]`，但不得退化成裸字符串 locator
- payload 生成失败时，该 candidate 必须丢弃，不进入 ranking

## Priority Axes Contract

### information_gain

表示执行后预计能消除多少关键不确定性。

主要看：

- 是否直接服务 blocking gaps
- 是否能减少当前 judgment 的主要不确定性
- 是否预计带来新的 canonical findings

### execution_cost

表示执行成本与依赖成本，值越高表示越贵。

主要看：

- 是否需要外部协作或人工审批
- 是否需要更重的分析步骤或更长等待窗口
- 是否涉及更广 scope 的数据或流程

### urgency

表示若不执行，风险或机会窗口流失的紧迫程度。

主要看：

- gap severity / blocking pressure
- 监控或处置时效要求
- policy deadline / SLA / escalation threshold

### expected_impact

表示动作成功后对 assessment 收敛或风险处置的潜在影响。

主要看：

- 能否推动 `insufficient -> supported/contradicted/mixed`
- 能否降低高严重度风险
- 能否改变 operator 决策质量

## Ranking Policy Contract

`priority_rank` 必须由显式 policy 合成，至少回答以下问题：

- 四个 `priority_axes` 如何映射到排序
- 是否存在 veto / cap / floor 规则
- 在特定 `risk_budget` 下哪些 family 会被提级或降级
- 哪些 `driver_tokens` 负责解释排序结果

v1 中固定约束：

- default order 为 `priority_rank ASC, created_at ASC, action_proposal_id ASC`
- `driver_tokens` 必须包含主要提级、降级或 veto 原因
- 排序语义变化若改变结果，必须形成新的 proposal snapshot；不得原地改写

## Identity, Dedup, And No-op

### action_proposal_id generation boundary

proposal identity 推荐由以下输入生成：

- `session_id`
- `action_kind`
- `primary_assessment_ref`
- `target_proposition_ref`
- `proposal_context`
- payload 中决定动作语义的字段

默认不进入 identity 的字段包括：

- `created_at`
- `priority_rank`
- explanation 文案
- top-k 截断位置

`policy_version` 只有在改变 payload 语义边界、served refs 闭包或可执行动作边界时才进入 identity。

### dedup rule

同一 refresh 批次中，若两个 candidates 在以下维度完全相同，则必须去重：

- `action_kind`
- `primary_assessment_ref`
- payload semantic fields
- `proposal_context`

去重后只保留一个 canonical proposal candidate，不允许保留多个文案不同但语义等价的 proposal。

### no-op rule

若 refresh 后的 canonical proposal 集在 identity、排序和 ref membership 上与当前 committed proposal 集完全一致，则本轮 refresh 记为 no-op：

- 不提交新的 proposal snapshots
- 不改写既有 proposal objects
- 读取面继续暴露当前 committed proposal 集

## Output Guarantees

- 同一 latest assessment、相同 context、相同 policy 重放时，proposal 集、identity 与排序稳定
- proposal engine 可输出空 proposal 集；空集是合法 canonical result
- consumer 可以完全绕过 proposal，仅依赖 proposition + latest assessment + gaps 做决策
- proposal engine 不得创造新的 facts、propositions 或 assessments
- proposal focus view 中的 relevant findings 必须继续来自 assessment closure

## Test Cases

至少应覆盖以下实现级验收场景：

1. 同一 `latest_assessment`、相同 `proposal_context`、相同 `policy_profile` 重跑时，proposal 集与 `action_proposal_id` 稳定。
2. assessment snapshot 更新但 proposal 语义未变时，refresh 可 no-op；若 `primary_assessment_ref` 或 payload semantic fields 变化，则必须形成新 proposal snapshot。
3. 无 committed `latest_assessment` 的 proposition 不允许进入 proposal refresh。
4. `investigate` / `validate` candidate 无法生成合法 typed intent request 时必须丢弃。
5. `risk_budget` / `session_goal` / `policy_profile` 变化只有在显式进入 `proposal_context` 时才允许影响 proposal 结果。
6. `priority_axes` 四个轴都必须存在，且 `priority_rank` 必须可由 policy 规则和 `driver_tokens` 解释。
7. top-k canonical proposal view 必须先建立稳定全序，再做前缀截断。
8. proposal refresh 不得回写 assessment status、confidence、gap state 或 evidence membership。
9. proposal engine 输出空集时，读取面仍应保持 canonical 可读，而不是报错。

## Related Documents

- [`overview.md`](overview.md)
- [`runtime-pipeline.md`](runtime-pipeline.md)
- [`inference-and-gap-engine.md`](inference-and-gap-engine.md)
- [`read-surfaces.md`](read-surfaces.md)
- [`schemas/assessment.md`](schemas/assessment.md)
- [`schemas/action-proposal.md`](schemas/action-proposal.md)
