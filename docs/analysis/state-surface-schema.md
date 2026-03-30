# State Surface Schema

本文档定义 Factum 在 agent-first 读取路径中的分析状态面（`analysis state surface`）拟议类型契约。

状态：draft design。本文是 `docs/analysis/` 下 状态面 的 canonical schema 提案，不表示对应 HTTP endpoint 或持久化模型已经实现。

## 目的

状态面 的职责，是把已经存在的 canonical objects 组织成稳定、可决策的 session 级主读取视图，而不是让 agent 回读完整 artifact、claim graph 或 narrative summary 自己反推当前状态。

设计目标：

- 以 `proposition + latest_assessment` 作为 session 级主决策骨架
- 复用既有 canonical objects，而不是发明平行 state object
- 保持 projection 可排序、可截断，但不重写 规范语义
- 为未来替换 `reflection-context` 提供更规范的主读取基线

## 核心设计决策

### 1. analysis 状态面 是读取契约，不是新的核心证据层

规范证据链 仍保持：

`artifact -> finding -> proposition -> assessment -> action proposal`

状态面 不新增新的核心证据实体；它定义的是如何把已有 canonical objects 组织成 agent 默认读取的稳定 session view。

因此：

- `SessionStateView` 是面向 consumer 的规范视图
- view 中出现的对象身份仍来自 `finding / proposition / assessment / evidence_gap / inference_record`
- view 不得发明新事实、新判断或新 typed ref

### 2. v1 的主读取骨架围绕活跃命题组织

会话状态视图（`SessionStateView`）的主骨架是 `active_propositions`，而不是 artifact list、subject summary 或 recommendation list。

v1 中，`active_propositions` 默认包含所有活跃命题（`live propositions`）：

- 已有 `latest_assessment` 的 proposition
- 尚未形成 assessment snapshot，但仍处于当前 session judgment track 的 proposition

这意味着：

- `latest_assessment = null` 是合法状态
- `latest_assessment = null` 不等价于 assessment failure
- agent 不需要额外入口去发现“已注册但尚未评估”的 proposition

### 3. 主状态采用混合视图，而不是完全规范化或完全内嵌

v1 采用混合视图（mixed view）组织：

- `active_propositions` 条目内嵌 `proposition`
- 同一条目内嵌 `latest_assessment | null`
- 其余 实时证据 通过 typed refs 指向顶层集合或 proposition-level context closure

这样做的原因是：

- agent 默认决策最依赖 proposition 与 current assessment
- finding / gap / 推断记录 仍保留 规范对象 身份
- 避免完全内嵌导致的重复载荷与跨条目不一致

### 4. v1 不把 session 级 action proposal 纳入主状态

尽管 `action proposal` 属于整体 analysis 状态面 的外层 shortcut，但 v1 状态面 不把 `recommended_next_actions` 作为默认返回字段。

原因：

- 当前未承诺 session 级 proposal 默认排序 policy
- 主状态需要先把 命题中心 决策读取边界定稳
- agent 必须能够仅依赖 proposition、assessment、gaps 与 findings 做下一步判断

当后续补充 session 级 proposal policy 时，应新增独立扩展，而不是回写主状态骨架。

## Schema Position

状态读取链路：

`canonical objects -> state view -> consumer projection`

其中：

- `finding / proposition / assessment / evidence_gap / inference_record` 负责 规范语义
- `SessionStateView` 负责 session 级主读取组织
- top-k、compact focus、token-budget 压缩属于 projection metadata，不改写 view identity

## Typed Schema

```ts
type SessionStateView = {
  session_id: string;
  focus_subjects: FindingSubject[];
  active_propositions: ActivePropositionEntry[];
  backing_findings: Finding[];
  blocking_gaps: EvidenceGap[];
  artifact_refs: StateArtifactRef[];
  truncation: StateTruncation;
  schema_version: string;
};

type ActivePropositionEntry = {
  proposition: Proposition;
  latest_assessment: Assessment | null;
  supporting_finding_refs: FindingRef[] | null;
  opposing_finding_refs: FindingRef[] | null;
  blocking_gap_refs: EvidenceGapRef[] | null;
  non_blocking_gap_refs: EvidenceGapRef[] | null;
  applied_inference_record_refs: InferenceRecordRef[] | null;
  artifact_refs: StateArtifactRef[];
};

type SessionStateQuery = {
  metric?: string | null;
  entity?: string | null;
  slice?: Record<string, string | number | boolean | null> | null;
  proposition_types?: Proposition["proposition_type"][] | null;
  origin_kinds?: Array<"system_seeded" | "agent_authored"> | null;
  assessment_presence?: "assessed" | "unassessed" | null;
  assessment_statuses?: Assessment["status"][] | null;
  has_blocking_gaps?: boolean | null;
  limit?: number | null;
};

type StateArtifactRef = {
  artifact_id: string;
  step_ref: StepRef;
};

type StepRef = {
  session_id: string;
  step_id: string;
  step_type: string;
};

type FindingRef = {
  session_id: string;
  finding_id: string;
};

type EvidenceGapRef = {
  gap_id: string;
  proposition_id: string;
  // Reuses the canonical ref shape from assessment.md.
};

type InferenceRecordRef = {
  inference_record_id: string;
  proposition_id: string;
  assessment_id: string;
  // Reuses the canonical ref shape from assessment.md.
};

type StateTruncation = {
  is_truncated: boolean;
  returned_count: number;
  total_count: number | null;
  sort_key: string;
  applies_to: "active_propositions";
};
```

## 字段语义

### SessionStateView

#### focus_subjects

`focus_subjects` 是当前状态视图中焦点主语的稳定去重投影。

要求：

- 只能由 `backing_findings.subject` 稳定去重得到
- 不能基于评估结果、推荐动作或自由文本额外推断
- 排序必须稳定且与 `backing_findings` 的 canonical 排序兼容

若 `backing_findings` 受截断影响，则：

- `focus_subjects` 只反映当前返回的 `backing_findings`
- consumer 不得把 `focus_subjects` 视为完整 session subject coverage
- 若需要全量 subject 索引，应通过独立 projection 定义，而不是扩张本字段语义

#### active_propositions

`active_propositions` 是 session state 的主决策骨架。

每个条目必须满足：

- `proposition` 为 canonical proposition object
- `latest_assessment` 是读取层选出的当前 assessment snapshot 或 `null`
- 若 `latest_assessment = null`，则所有 assessment-derived ref 字段必须同时为 `null`
- 若 `latest_assessment` 存在但某类 live membership 为空，则对应 ref 字段返回 `[]`
- `latest_assessment = null` 的 活跃命题 仍必须作为 judgment-track object 可见，不得因默认排序或顶层截断被语义性降格为 inactive

`supporting_finding_refs` 与 `opposing_finding_refs` 只允许引用当前 `latest_assessment` 的 实时证据 membership，不允许混入历史 snapshot 成员。

#### applied_inference_record_refs

`applied_inference_record_refs` 是会话主视图中的轻量规则过程索引。

要求：

- 它只索引当前 `latest_assessment` 采用的 inference records
- 它的职责是让 agent 知道“当前判断经过了哪些显式规则过程”
- session 主视图默认不内嵌完整 推断记录 payload，以避免跨 proposition 重复载荷

agent 若需要审计当前判断，应基于这些 refs 进入 proposition context，读取完整 `applied_inference_records`，并查看：

- 命中的规则族（规则族）
- `hit` / `miss` / `partial` 等 evaluation result
- `input_finding_ids` / `input_assessment_ids`
- 未满足的 rule preconditions 或等价线索

`rule family` 的汇总来源必须是 [`rule-registry-contract.md`](rule-registry-contract.md) 中定义的稳定 registry，不得由 `rule_id` 字符串前缀、目录名或实现类名推断。

#### backing_findings

`backing_findings` 是会话主状态中的支撑事实单元载荷。

在未截断时，它是 `active_propositions` 当前 实时证据 所需 finding 的去重并集；当顶层截断作用于 `active_propositions` 时，它只允许覆盖 returned propositions 的 实时证据 closure。

它允许包含：

- support / oppose findings
- proposition seed findings
- assessment / 推断记录 直接引用的 relevant findings

它不得包含：

- 仅用于展示的 summary item
- 与当前 state view 无关的 session 全量 finding
- 无 规范标识 的 projection fragment
- 已被顶层截断排除的 proposition 所需 findings

#### blocking_gaps

`blocking_gaps` 是 `active_propositions` 当前阻塞缺口的去重并集。

要求：

- 仅包含来自当前 latest assessments 的 `gap_memberships` 且 `blocking = true` 的 gaps
- 不包含 non-blocking gaps
- 成员集合必须可完全由 `blocking_gap_refs` 解引用得到
- 当顶层截断作用于 `active_propositions` 时，只允许覆盖 returned propositions 的 阻塞性缺口 closure

#### artifact_refs

`artifact_refs` 是本次状态视图涉及 evidence 的权威溯源入口。

它是 `backing_findings` 的来源 artifact 去重集合，不额外引入新判断语义。

`StateArtifactRef` 只承担最小查找句柄（查找句柄）职责：

- `artifact_id` 负责定位权威 artifact
- `step_ref` 负责定位生成该 artifact 的 typed step

完整 provenance 不在 state view 中重复内嵌；consumer 应通过 artifact object 本身或已返回的 `Finding.provenance` 获取完整溯源信息。

附加约束：

- `artifact_refs` 必须可完全由 returned `backing_findings` 的来源 artifact 稳定去重得到
- 不得为了 proposition 局部审计、seed provenance 补全或 context convenience 额外引入 artifact
- 当顶层截断作用于 `active_propositions` 时，`artifact_refs` 只允许覆盖 returned propositions 的 实时证据 closure

## 默认排序与截断

### SessionStateView 默认排序

`active_propositions` 推荐默认排序：

1. 存在 blocking gaps 的已评估 proposition 优先，按 latest assessment `gap_memberships` 中阻塞性缺口 severity descending
2. 其余已评估 proposition 按 阻塞性缺口 count descending
3. `latest_assessment.created_at` descending，nulls last
4. `proposition.proposition_type` lexical order
5. `proposition.subject.metric` lexical order，nulls last
6. `proposition.subject.slice` canonicalized lexical order
7. `proposition.proposition_id` ascending

补充约束：

- 默认排序不得把 `latest_assessment != null` 作为单独第一主键，从而系统性饿死 unassessed live propositions
- 状态面 的主职责之一是暴露“已注册但尚未评估”的 judgment-track objects；convenience ranking 不得覆盖该目标
- 若实现层对 `active_propositions` 做 top-k，必须保证 returned 集合中允许出现 unassessed live propositions，而不是只返回已评估条目

`backing_findings`、`blocking_gaps` 与 `artifact_refs` 默认排序应分别复用各自 canonical schema 文档中的稳定规则。

### 截断规则

- top-k 截断只能发生在 view/projection 层
- 截断不得改变 规范对象 identity
- `truncation.total_count = null` 表示总量未知或未计算，不表示无限制
- `truncation.sort_key` 必须显式记录本次截断所依赖的默认排序键
- `truncation.applies_to` 必须显式声明该 metadata 作用于哪组集合
- 若 `latest_assessment = null`，不得因排序劣后而重写 proposition 语义为 inactive
- 当顶层截断作用于 `active_propositions` 时，`focus_subjects`、`backing_findings`、`blocking_gaps` 与 `artifact_refs` 必须一并收缩为 returned propositions 的自洽 closure
- 顶层 supporting collections 不得残留任何被截断 proposition 的 members

v1 中，顶层 `truncation` 默认描述 `active_propositions` 主集合。

因此：

- `truncation.applies_to = "active_propositions"` 是默认情形
- 若 consumer 看到 `is_truncated = true`，只能先推断 `active_propositions` 被截断
- 其他集合若未来发生独立截断，应通过各自局部 metadata 表达，而不是扩张顶层 `truncation` 的作用范围

## 查询语义

推荐最小查询轴：

- `metric`
- `entity`
- `slice`
- `proposition_types`
- `origin_kinds`
- `assessment_presence`
- `assessment_statuses`
- `has_blocking_gaps`
- `limit`

推荐匹配规则：

- `slice` 采用 proposition subject slice 的子集精确匹配
- `assessment_presence = "unassessed"` 仅匹配 `latest_assessment = null` 的 活跃命题
- `assessment_presence = "assessed"` 仅匹配 `latest_assessment != null` 的条目
- `assessment_statuses` 仅匹配 `latest_assessment.status`；若 `latest_assessment = null`，默认不命中任何 status filter
- `assessment_presence = "unassessed"` 与任意 `assessment_statuses` 同时出现时返回空集，不做隐式容错
- `has_blocking_gaps = true` 仅匹配 `latest_assessment` 存在且 `blocking_gap_refs` 非空的条目
- `has_blocking_gaps = false` 仅匹配 `latest_assessment` 存在且 `blocking_gap_refs = []` 的条目，不隐式包含 unassessed live propositions
- 若 consumer 需要“无 blocking gaps 或尚未评估”的宽松读取，应显式组合查询，而不是扩大 `has_blocking_gaps = false` 的 canonical 语义

## 与现有读取面的关系

### 与 proposition / assessment / finding 文档的关系

- `proposition.md` 继续定义判断对象 identity 与 proposition-level focus closure 的 canonical 基线
- `assessment.md` 继续定义 latest snapshot、gap 与 推断记录 的规范语义
- `finding.md` 继续定义事实载荷、focus subject 与 artifact lineage 的基线

本文只定义这些对象在 session 主状态读取中的组合方式。

### 与 reflection-context 的关系

本文定义的 状态面 目标上替换当前 `reflection-context` 作为 agent 默认主读取基线，但二者不等价：

- `reflection-context` 是 compact summary
- 状态面 是 命题中心 规范状态 view
- readiness、tentative claims 与 compact evidence-gap summary 不并入 v1 规范状态 object

HTTP 迁移、兼容期与废弃策略属于后续 API 设计，不在本文定义。

### 与 action proposal 的关系

`action proposal` 仍是可选 planning shortcut。

在更完整的分析状态面设计中，`recommended_next_actions` 仍可作为 session 级推荐下一步动作 shortcut 存在。

但本文的 v1 明确不把 session 级 `recommended_next_actions` 纳入 `SessionStateView` 默认字段，原因是：

- 当前尚未定义稳定的 session-level ranking policy
- 命题中心 主骨架需要先独立定稳
- agent 必须能够在完全绕过 proposal 的情况下完成下一步决策

这是一种阶段性收敛，不是否定 action proposal 在更完整 状态面 中的地位。后续若补入 proposal shortcut，应通过独立扩展恢复，而不是回写主状态骨架。

## Test Cases

后续实现至少应满足以下 schema-level 验收场景：

1. 活跃命题 尚未评估时，`latest_assessment = null`，且 assessment-derived refs 也为 `null`
2. 已评估 proposition 可返回 `blocking_gap_refs = []`
3. 同一 proposition 有多个 assessment snapshots 时，state view 只暴露一个 `latest_assessment`
4. `focus_subjects` 只能由 `backing_findings.subject` 去重得到
5. `blocking_gaps` 必须能由 `blocking_gap_refs` 完整解引用
6. top-k 截断不改变任何 canonical id、typed ref 或 latest 选择规则，且 supporting collections 只覆盖 returned propositions
7. `focus_subjects` 在 truncation 生效时只反映 returned findings，不得被解释为全量 subject coverage
8. `StateTruncation.total_count = null` 表示 unknown / not computed，而不是 unlimited
9. agent 可通过 `applied_inference_record_refs` 和 proposition context 中的 `applied_inference_records` 回溯 rule hit/miss 与直接输入依赖
10. `StateArtifactRef` 不重复承载完整 provenance，完整溯源应通过 artifact 或 finding provenance 获取
11. `SessionStateView` 不默认包含 session-level action proposal shortcut
12. 不允许在 规范引用 位置使用裸字符串 locator
13. `assessment_presence = "unassessed"` 只命中 `latest_assessment = null` 的 live propositions
14. `assessment_presence = "unassessed"` 与任意 `assessment_statuses` 组合时返回空集
15. `has_blocking_gaps = false` 不得混入 unassessed live propositions
16. `artifact_refs` 必须能完全由 returned `backing_findings` 的来源 artifact 去重推导
17. 顶层默认排序不得把 unassessed live propositions 系统性排除在 returned 集合之外

负向场景至少覆盖：

1. 不允许把全 session findings 全量塞入 `backing_findings`
2. 不允许把 non-blocking gaps 混入 `blocking_gaps`
3. 不允许 `latest_assessment = null` 时返回 `[]` 形式的 assessment-derived ref 字段
4. 不允许 projection 排序重写 proposition identity 或 assessment identity
5. 不允许把 readiness、narrative summary 或 recommendation text 混入 规范状态 object
6. 不允许把 `focus_subjects` 在截断状态下解释成全量 subject 索引
7. 不允许把 artifact 查找句柄 误写成 artifact provenance payload
8. 不允许把被顶层截断排除的 proposition 所需 findings / gaps / artifacts 残留在 supporting collections
9. 不允许 `has_blocking_gaps = false` 继续承担 “no 阻塞性缺口 or unassessed” 的混合语义
10. 不允许为了 context convenience 在 `artifact_refs` 中返回不属于 `backing_findings` 的 artifact

## 非目标

本文不定义：

- HTTP endpoint、分页 token、cache key 或并发语义
- assessment 历史快照检索
- state runtime lifecycle、回滚与重放
- session 级 action proposal 排序 policy
- readiness / planning / reflection 的统一调度算法
