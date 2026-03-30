# Assessment Schema

本文档定义 Factum 判断层中 `assessment` 的拟议类型契约，以及与其配套的证据缺口（`evidence_gap`）/ 推断记录（`inference_record`）规范对象。

状态：draft design。本文是规划中的规范 `assessment` Schema 提案，不表示对应 HTTP endpoint 或持久化模型已经实现。

## 目的

`assessment` 是 Factum 证据引擎中的规范评估状态（canonical evaluation state），用于表达系统当前对某个命题（`proposition`）已经判断到什么程度、还缺什么、以及这些判断是由哪些显式规则和事实支撑出来的。

设计目标：

- 明确分离命题（`proposition`）与评估状态（`assessment`）
- 让 agent 直接读取”当前判断状态”，而不是从 claim 文本中反推
- 把实时证据归属（live evidence membership）、证据缺口（evidence gap）、规则命中/未命中（rule hits/misses）放入判断状态，而不是回写 proposition
- 保持 assessment 可审计、可回溯、可版本化
- 为动作候选（action proposal）提供稳定输入，但不把动作建议混入 assessment 本体

## 核心设计决策

### 1. `assessment` 是版本化快照（versioned snapshot），不是可变 blob（mutable blob）

v1 中 `assessment` 采用版本化快照。

要求：

- 每次评估建立新的不可变快照（immutable snapshot）
- 同一 proposition 可有 `0..N` 个 assessment 快照
- 对外主读取接口通过 `latest_assessment` 暴露当前快照
- 历史快照不因新证据进入而被原地改写

这样可以同时满足：

- agent 读取当前状态的简单性
- 审计与回溯的稳定性
- 推断（inference）结果与证据缺口（gap）演化的可解释性

`latest_assessment` 是读取层 / 投影层（projection layer）选择出的”当前快照”，不是快照本体字段。规范 `Assessment` 不携带 `is_latest` 一类会随新快照出现而回写历史对象的状态位。

附加要求：

- proposition-centered 的主读取接口必须把 `latest_assessment` 视为读取层职责，而不是 assessment 本体字段
- 上下文（context）/ 焦点（focus）投影可以裁剪 assessment 列表，但不得改写最新态（latest）选择规则
- agent 不应被要求扫描同一 proposition 的全部历史快照，才能理解当前判断状态

### 2. `assessment` 回答“当前判断到什么程度”

`assessment` 的职责是表达：

- 当前是否已评估
- 当前支持 / 反驳 / 混合 / 证据不足的状态
- 当前 confidence grade
- 当前 live evidence set
- 当前阻塞与非阻塞 gaps
- 当前评估所依赖的 inference records

它不负责表达：

- proposition 本身的 judgment semantics
- 下一步动作建议
- 原始 artifact 明细

对 agent 来说，assessment 是 proposition-centered state surface 中的第一决策对象。

因此，一个 proposition 若尚未形成 `latest_assessment`，应由主读取接口显式表示“尚未进入评估流程”，而不是要求调用方从零散字段推断当前状态。

### 3. `status` 使用证据优先状态格（evidence-first lattice）

v1 采用证据优先（evidence-first），而不是工作流中心（workflow-centric）的状态机。

推荐通用状态：

- `insufficient`
- `supported`
- `contradicted`
- `mixed`

解释：

- `insufficient` 表示已进入评估流程，但证据不足以形成更强判断
- `supported` 表示当前证据主要支持 proposition
- `contradicted` 表示当前证据主要反驳 proposition
- `mixed` 表示同时存在实质支持与反驳，尚不能收敛为单向结论

如果某个评估家族（assessment family）需要更细语义，应进入 subtype payload，而不是扩张 base lattice。

未进入评估流程时，规范 Schema 统一用 `latest_assessment = null` 表示，不单独定义 `unassessed` status。

### 4. `confidence` 使用类型化等级（typed grade），而不是连续分数

v1 不使用 `0..1` 或 `0..100` 数值分。

推荐枚举：

- `very_low`
- `low`
- `medium`
- `high`
- `very_high`

原因：

- 避免伪精确
- 保持 agent 排序和阈值判断稳定
- 让置信度（confidence）依赖结构化理由，而不是单一黑盒分值

### 5. `assessment` 必须显式引用证据缺口（gaps）与推断记录（inference records）

assessment 不是只带几个 finding id 的薄壳对象。

v1 中：

- 证据缺口建模为独立规范对象：`EvidenceGap`
- 规则命中 / 未命中建模为独立规范对象：`InferenceRecord`
- assessment 通过 id 引用二者

这样可避免：

- 把证据缺口（gap）压成匿名字符串数组
- 把规则解释压成不可追踪的解释（explanation）文本
- 在 assessment 更新时丢失 gap / inference 的独立生命周期

### 6. `assessment` 必须兼容 authored proposition 与 seeded proposition 共用轨道

v1 中 assessment 不区分 proposition 来源。

要求：

- `system_seeded proposition` 与 `agent_authored proposition` 都进入同一 assessment family
- authored proposition 不得拥有独立的“弱评估”旁路
- 若 authored proposition 尚无足够证据，应通过 `insufficient + gaps` 表达，而不是跳过 assessment 轨道

## Schema Position

规范抽象链路：

`artifact -> finding -> proposition -> assessment -> action proposal`

其中：

- `artifact`：完整步骤输出
- `finding`：确定性抽取的事实单元
- `proposition`：待评估命题
- `assessment`：当前评估快照
- `evidence_gap`：阻塞或限制 assessment 升级的缺失条件
- `inference_record`：规则过程的结构化记录
- `action proposal`：面向 agent 的动作候选

`evidence_gap` 与 `inference_record` 不是新的主层级，但属于判断层的规范支撑对象。

assessment 在 state lifecycle 中处于 `proposition registration` 之后、`action proposal refresh` 之前；它是 proposition-centered 主状态与 action proposal 之间的唯一规范桥梁。

assessment 中承载 relation semantics 的字段解释，以 [`evidence-engine/graph-and-reference-semantics.md`](../graph-and-reference-semantics.md) 为准：

- `proposition_id` 承载 `assessment -> proposition` 的 `assesses`
- `supporting_finding_ids` / `opposing_finding_ids` 承载 `assessment -> finding` 的 directional membership edges
- `gap_memberships` 承载 `assessment -> evidence_gap` 的 gap membership edges，并为当前 snapshot 提供 `blocking` / `severity` classification
- `applied_inference_record_ids` 承载 `assessment -> inference_record` 的 `applies_record`
- `supersedes_assessment_id` 承载 `assessment -> assessment` 的 `supersedes`

这些关系都绑定 assessment snapshot；其中 support / oppose / gap / inference record membership 是 runtime snapshot membership，不是 proposition-level 常驻关系。

## Typed Schema

```ts
type Assessment =
  | ChangeAssessment
  | DecompositionAssessment
  | AnomalyAssessment
  | CorrelationAssessment
  | TestHypothesisAssessment
  | ForecastAssessment;

type AssessmentBase = {
  assessment_id: string;
  assessment_type:
    | "change_assessment"
    | "decomposition_assessment"
    | "anomaly_assessment"
    | "correlation_assessment"
    | "test_hypothesis_assessment"
    | "forecast_assessment";
  session_id: string;
  proposition_id: string;
  snapshot_seq: number;
  status: AssessmentStatus;
  confidence_grade: ConfidenceGrade;
  confidence_rationale: ConfidenceRationale;
  supporting_finding_ids: string[];
  opposing_finding_ids: string[];
  gap_memberships: GapMembershipEntry[];
  applied_inference_record_ids: string[];
  supersedes_assessment_id: string | null;
  created_at: string;
  schema_version: string;
};

type AssessmentStatus =
  | "insufficient"
  | "supported"
  | "contradicted"
  | "mixed";

type ConfidenceGrade =
  | "very_low"
  | "low"
  | "medium"
  | "high"
  | "very_high";

type ConfidenceRationale = {
  evidence_sufficiency:
    | "very_weak"
    | "weak"
    | "adequate"
    | "strong"
    | "very_strong";
  evidence_consistency:
    | "conflicting"
    | "mostly_conflicting"
    | "mixed"
    | "mostly_consistent"
    | "consistent";
  rule_coverage:
    | "minimal"
    | "partial"
    | "substantial"
    | "comprehensive";
  data_quality_impact:
    | "severe"
    | "material"
    | "limited"
    | "none";
  rationale_notes: string[];
};

type AssessmentSubject = {
  metric: string | null;
  entity: string | null;
  slice: Record<string, string | number | boolean | null>;
  grain: "hour" | "day" | "week" | "month" | null;
  analysis_axis:
    | "change"
    | "decomposition"
    | "anomaly"
    | "correlation"
    | "test"
    | "forecast";
};

type EvidenceGap = {
  gap_id: string;
  session_id: string;
  proposition_id: string;
  gap_kind:
    | "missing_finding"
    | "missing_slice"
    | "missing_time_coverage"
    | "missing_rule_precondition"
    | "data_quality_risk"
    | "comparability_risk"
    | "resolution_conflict";
  title: string;
  description: string;
  status: "open" | "resolved";
  missing_requirement: GapRequirement;
  satisfiable_by: GapSatisfiableBy[];
  related_finding_ids: string[];
  opened_by_inference_record_id: string;
  resolved_by_inference_record_id: string | null;
  created_at: string;
  resolved_at: string | null;
  schema_version: string;
};

type GapMembershipEntry = {
  gap_ref: EvidenceGapRef;
  blocking: boolean;
  severity: "low" | "medium" | "high" | "critical";
};

type GapRequirement =
  | {
      requirement_type: "finding_family";
      requirement_key: string;
      requirement_params: {
        finding_type: string;
        minimum_count: number | null;
      };
    }
  | {
      requirement_type: "subject_coverage";
      requirement_key: string;
      requirement_params: {
        required_subject: AssessmentSubject;
        missing_axis: "metric" | "entity" | "slice" | "grain";
      };
    }
  | {
      requirement_type: "time_coverage";
      requirement_key: string;
      requirement_params: {
        required_window_start: string;
        required_window_end: string;
        required_grain: "hour" | "day" | "week" | "month" | null;
      };
    }
  | {
      requirement_type: "quality_threshold";
      requirement_key: string;
      requirement_params: {
        quality_dimension:
          | "data_complete"
          | "sample_size"
          | "quality_status"
          | "null_rate";
        threshold_operator: ">=" | "<=" | "=";
        threshold_value: string | number | boolean;
      };
    }
  | {
      requirement_type: "comparability_requirement";
      requirement_key: string;
      requirement_params: {
        comparability_dimension:
          | "window_alignment"
          | "subject_alignment"
          | "slice_alignment"
          | "grain_alignment"
          | "method_precondition";
        expected_relation: string;
        comparison_scope: string;
      };
    }
  | {
      requirement_type: "rule_precondition";
      requirement_key: string;
      requirement_params: {
        rule_id: string;
        missing_condition: string;
      };
    };

type GapSatisfiableBy =
  | {
      kind: "analysis_step";
      step_family:
        | "observe"
        | "compare"
        | "decompose"
        | "correlate"
        | "detect"
        | "test"
        | "forecast";
      suggested_subject: AssessmentSubject | null;
    }
  | {
      kind: "finding_arrival";
      finding_type: string;
      subject: AssessmentSubject | null;
    }
  | {
      kind: "quality_resolution";
      resolution_key: string;
    };

type InferenceRecord = {
  inference_record_id: string;
  session_id: string;
  proposition_id: string;
  assessment_id: string;
  rule_id: string;
  rule_version: string;
  result: "hit" | "miss" | "partial";
  input_finding_ids: string[];
  input_assessment_ids: string[];
  opened_gap_ids: string[];
  resolved_gap_ids: string[];
  produced_status_transition: StatusTransition | null;
  confidence_contribution: ConfidenceContribution;
  justification: InferenceJustification;
  created_at: string;
  schema_version: string;
};

type AssessmentRef = {
  assessment_id: string;
  proposition_id: string;
  snapshot_seq: number;
};

type EvidenceGapRef = {
  gap_id: string;
  proposition_id: string;
};

type InferenceRecordRef = {
  inference_record_id: string;
  proposition_id: string;
  assessment_id: string;
};

type StatusTransition = {
  from_status: AssessmentStatus | null;
  to_status: AssessmentStatus;
};

type ConfidenceContribution = {
  direction: "increase" | "decrease" | "neutral";
  magnitude: "small" | "medium" | "large";
};

type InferenceJustification = {
  matched_conditions: string[];
  unmatched_conditions: string[];
  notes: string[];
};
```

## 公共字段语义

### assessment_id

`assessment_id` 是单个 assessment snapshot 的 canonical identifier。

推荐生成输入：

- `session_id`
- `proposition_id`
- `snapshot_seq`

要求：

- 同一 snapshot 重读时稳定
- 新 snapshot 必须生成新 `assessment_id`
- `assessment_id` 不因 projection 截断或排序变化而改变
- `schema_version` 是独立 version boundary，不参与 `assessment_id` 生成
- `assessment_type` 应与 proposition 的 `assessment_anchor` 一致；单个 proposition 下不应出现并行多种 assessment type

推荐区分规则：

- 重读同一 persisted snapshot：复用同一 `assessment_id`
- 在相同输入上重复执行但没有产生任何规范状态变化：可复用旧 snapshot，不必新建
- 只要产生新的 canonical assessment snapshot，就必须分配新的 `snapshot_seq` 与 `assessment_id`

### snapshot_seq

`snapshot_seq` 表示某个 proposition 的 assessment 版本序号。

要求：

- 在同一 `proposition_id` 下单调递增
- 不做跨 proposition 比较语义
- 不得被复用来表达规则优先级或结论强弱

读取约束：

- “哪个 snapshot 是当前态”由读取层通过 `latest_assessment` 或等价 pointer 决定
- canonical `Assessment` 本体不携带 `is_latest` 一类可变状态位

### status

`status` 表达当前 evidence-first judgment state。

规则：

- `supported` / `contradicted` 不等价于“没有 gap”
- `mixed` 不等价于“低 confidence”；它表示存在实质对立证据
- `insufficient` 表示已评估但尚不具备更强判断

### confidence_grade

`confidence_grade` 表达系统对当前 status 的把握程度。

它必须由结构化理由支撑，不得成为黑盒分数替代品。

排序建议：

`very_high > high > medium > low > very_low`

### confidence_rationale

`confidence_rationale` 用于解释 confidence 的结构化组成。

该字段的首要目标是 machine-readable，而不是生成人类报告文案。

规则：

- `rationale_notes` 可为 `[]`
- 不允许用长自由文本替代结构化维度
- 若某维度暂不可判定，应使用最接近的保守枚举，而不是 `null`

### Confidence Grade 推导规则

v1 不定义跨所有 assessment family 通用的固定打分公式。

原因：

- 不同 proposition family 的 rule family 不同
- 同样的 evidence 组合在 `change`、`correlation`、`forecast` 中不应被强行套入同一数学映射

因此：

- `confidence_grade` 由适用的 inference rules 决定
- `confidence_rationale` 是该结论的结构化解释，不是独立计分器
- 任何 inference rule 在写入 `confidence_grade` 时，都必须同时写入可解释的 rationale 维度

v1 的全局 guardrails：

- `data_quality_impact = severe` 时，`confidence_grade` 不得高于 `low`
- `evidence_sufficiency = very_weak` 时，`confidence_grade` 不得高于 `low`
- `rule_coverage = minimal` 且 `evidence_consistency` 不是 `consistent` 时，`confidence_grade` 不得高于 `medium`
- 若 `evidence_consistency = conflicting`，应优先产生 `mixed` 或保守的 `insufficient`，而不是高 confidence 的单向结论

### supporting_finding_ids / opposing_finding_ids

两组 finding refs 用于定义当前方向性的证据归属。

语义区别：

- `supporting_finding_ids`：直接支持 proposition 当前判断的 findings
- `opposing_finding_ids`：直接反驳 proposition 当前判断的 findings

要求：

- 不得与 proposition `seed_finding_refs` 混淆
- 同一 finding 可是种子事实，但仍需在 assessment 中显式重新列入实时证据归属
- 数组为空表示当前集合为空，不表示未知

若某个 finding 只提供背景、范围、质量或 comparability 上下文，而不直接构成支持或反驳，应挂到相应的 `EvidenceGap` 或 `InferenceRecord`，而不是塞入 assessment base 的兜底字段。

### gap_memberships

`gap_memberships` 是 assessment snapshot 对当前 live gaps 的 canonical membership 载荷。

每个 member 必须引用一个 `EvidenceGapRef`，并携带当前 snapshot-owned 的：

- `blocking`
- `severity`

规则：

- 同一个 `gap_ref` 在单个 snapshot 中最多出现一次
- `blocking = true` 表示该 gap 当前阻止 proposition 升级或稳定收敛
- `blocking = false` 表示该 gap 当前只是 caveat，不阻塞当前可用判断
- `severity` 表示当前 snapshot 对该 gap pressure 的分类，不参与 gap identity
- 已评估但无 gap 时返回 `[]`
- 尚未评估时，不返回 assessment object；由上层 view 返回 `latest_assessment = null`

`gap_memberships` 的 lifecycle、identity convergence 与 reopen 规则统一由 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 定义；本节只定义 assessment snapshot 上该字段自身的 schema 语义。

### applied_inference_record_ids

`applied_inference_record_ids` 是当前 snapshot 的直接推理依据。

rule family 的组织、固定 evaluation order、以及哪些规则结果必须进入该字段，统一由 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 定义；本节只定义 snapshot 上该字段自身的 schema 语义。

`rule_id -> rule_family -> assessment_type` 的稳定解引用来源统一由 [`rule-registry-contract.md`](../rules/rule-registry-contract.md) 定义，不得由读取面或实现层通过字符串模式临时推断。

要求：

- 至少覆盖对当前 status / confidence / gap state 有贡献的 inference records
- 不要求把历史上所有无关 records 全量回挂到当前 snapshot
- 默认排序建议为 `created_at ASC`，再按 `inference_record_id ASC`

### supersedes_assessment_id

指向被当前 snapshot 取代的上一版本 assessment。

规则：

- 首个 snapshot 为 `null`
- 后续 snapshot 必须指向同一 proposition 的上一 latest snapshot
- 不允许跨 proposition supersede
- v1 的 supersede 链必须线性，不允许跳跃式 supersede
- 规范历史不应删除中间 snapshot；若底层存储不可读，应把链视为损坏状态显式暴露，而不是静默重连
- v1 不支持并发分叉 assessment 历史；若评估并发发生，写入层必须先线性化，再生成新的 `snapshot_seq`

## EvidenceGap 语义

### 标识边界

`gap_id` 绑定单个 session 内、单个 proposition 下、单个缺口语义。

要求：

- 同一 gap 在多个 assessment snapshots 中持续存在时应复用 `gap_id`
- gap 被解决后不应删除；通过 `status = resolved` 和 `resolved_at` 表达生命周期
- 不同 proposition 即使缺口描述相同，也不复用 `gap_id`
- `schema_version` 不参与 `gap_id` 生成

`title` 与 `description` 是 explanation / presentation 辅助字段：

- 不参与 identity
- 不得替代 `gap_kind`、`missing_requirement` 或 `satisfiable_by`
- 不得承载唯一的 canonical planning 语义

### GapMembershipEntry.blocking / severity

`blocking` 与 `severity` 属于 snapshot-owned classification，而不是 `EvidenceGap` 本体字段。

因此：

- 同一个 open gap 可以在不同 snapshots 中改变 `blocking` 或 `severity`
- classification 变化会 supersede assessment snapshot，但不会改变 gap identity
- gap object 只负责 requirement semantics 与 lifecycle

具体收敛规则以 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 为准。

### missing_requirement

`missing_requirement` 是 gap 的主结构化语义入口。

规则：

- `requirement_key` 应稳定、可比较、可用于去重同类 requirement
- `subject_coverage.requirement_params.required_subject` 必须是结构化对象，不允许使用裸字符串 locator
- `comparability_requirement.requirement_params.expected_relation` 与 `comparison_scope` 必须承载稳定可重放的关系语义，不得退化成只靠 title / description 解释
- v1 不提供 `other` 逃生口；若某种 requirement 尚未稳定建模，则不应进入 canonical schema

### opened_by_inference_record_id / resolved_by_inference_record_id

gap 的打开与解决都必须能回溯到规则过程。

规则：

- `opened_by_inference_record_id` 必填
- 首次发现 gap 的过程本身就是一个 inference outcome，因此必须建立对应的 `InferenceRecord`
- gap 与 opening inference record 属于同一次评估事件，可以原子创建；这不构成循环依赖
- `resolved_by_inference_record_id = null` 表示该 gap 尚未被显式解决

### satisfiable_by

`satisfiable_by` 用于让 agent 把 gap 转化为下一步计划。

它应表达：

- 需要哪类 typed step
- 或等待哪类 finding 到来
- 或需要解决哪类质量问题

规则：

- `analysis_step.suggested_subject` 与 `finding_arrival.subject` 必须是结构化对象，而不是自由文本 locator
- `satisfiable_by = []` 表示当前没有规范化的闭合路径，不表示未知

该字段是结构化 planning hint，不是 action proposal。

## InferenceRecord 语义

关于 rule family、规则顺序、升级/降级条件、冲突处理，以及 `hit / miss / partial` 如何从 rule engine 稳定映射到 `InferenceRecord`，见 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md)。本节只定义 `InferenceRecord` 作为 canonical support object 的 schema 与 identity 约束。

`InferenceRecord.assessment_id` 可在单次 recompute 中先绑定 candidate / preallocated snapshot identity，再在 snapshot 提交时一并进入 canonical state；具体 materialization 顺序由 [`evidence-engine/inference-and-gap-engine.md`](../inference-and-gap-engine.md) 定义。

### 标识边界

`inference_record_id` 是单个规则过程结果的 canonical identifier。

推荐生成输入：

- `session_id`
- `proposition_id`
- `assessment_id`
- `rule_id`
- 同一 assessment 内该规则结果的稳定事件键或稳定序号

要求：

- 同一 persisted inference outcome 重读时复用同一 `inference_record_id`
- 若重复执行规则但没有产生新的 canonical inference outcome，可复用旧 record
- 只要规则过程产出了新的规范状态结果，就必须创建新的 `InferenceRecord`
- `rule_version` 与 `schema_version` 是版本边界，不参与 `inference_record_id` 生成
- 不允许跨 session 或跨 proposition 复用 `inference_record_id`

lineage 规则：

- `InferenceRecord` 绑定所属 `proposition_id`
- `InferenceRecord` 绑定所属 `assessment_id`
- 通过 `input_finding_ids` / `input_assessment_ids` 显式暴露其直接依赖
- `InferenceRecord` 是 immutable support object，不原地修改

### result

`result` 至少支持：

- `hit`
- `miss`
- `partial`

原因：

- 只记录 hit 会丢失为什么仍然 insufficient
- `partial` 能表达“部分条件满足，但不足以升级”的规则过程

### input_assessment_ids

`input_assessment_ids` 用于表示 assessment-on-assessment 的推理输入。

v1 约束：

- 仅允许引用同一 `proposition_id` 下更早的 assessment snapshots
- 不允许跨 proposition 引用其他 assessment
- 不允许引用当前 `assessment_id` 或任何未来 snapshot
- 引用图必须保持有向无环

典型用途：

- 比较当前评估与上一快照的状态变化
- 判断某个 gap 是否已由新证据解决
- 基于历史 assessment 的演化轨迹决定 confidence 调整

### produced_status_transition

该字段用于表达本次 inference 是否推动了 assessment 状态变化。

规则：

- 若只是补充 justification 或 gap 细节，可为 `null`
- 首次建立 assessment 时，`from_status = null`
- 不允许生成与 assessment snapshot 最终状态不一致的 transition

### justification

`justification` 记录机器可读的规则命中说明。

要求：

- `matched_conditions` 与 `unmatched_conditions` 都允许为空数组
- 不得退化成只有一段 explanation 文本
- condition token 应稳定、可引用、可比较

## Agent Consumption Contract

### 查询轴

assessment 至少应支持按以下轴查询：

- `session_id`
- `proposition_id`
- `assessment_type`
- `snapshot_seq`
- `status`
- `confidence_grade`

gap 至少应支持按以下轴查询：

- `session_id`
- `proposition_id`
- `status`
- `gap_kind`

gap membership 至少应支持按以下轴查询：

- `assessment_id`
- `blocking`
- `severity`

inference record 至少应支持按以下轴查询：

- `session_id`
- `proposition_id`
- `assessment_id`
- `rule_id`
- `result`

### 默认排序

建议默认排序：

- assessment list：`snapshot_seq DESC`, `created_at DESC`, `assessment_id ASC`
- gap list：`created_at ASC`, `gap_id ASC`
- gap membership list：`blocking DESC`, `severity DESC`, `gap_ref.gap_id ASC`
- inference record list：`created_at ASC`, `inference_record_id ASC`

### 稳定引用格式

推荐固定使用以下 canonical refs：

- `AssessmentRef = { assessment_id, proposition_id, snapshot_seq }`
- `EvidenceGapRef = { gap_id, proposition_id }`
- `InferenceRecordRef = { inference_record_id, proposition_id, assessment_id }`

通用的 hard / soft ref 分类、跨 session 禁止边界、dangling read semantics 与 closure integrity 以 [`evidence-engine/graph-and-reference-semantics.md`](../graph-and-reference-semantics.md) 为准；本节只补充 assessment snapshot、gap 与 inference record 的本地 ref 形状和读取要求。

引用约束：

- 默认在同一 session 上下文中解释；v1 不允许跨 session canonical ref
- assessment / gap / inference record 都允许引用同 session 内历史对象
- projection ref 不得替代 canonical source ref
- 整体引用图必须保持 DAG

### 局部最小闭包读取

围绕 proposition 的最小闭包推荐扩展为：

```ts
type PropositionSeedEntry = {
  seed_ref: PropositionSeedRef;
  finding: Finding | null;
};

type PropositionFocusView = {
  proposition: Proposition;
  seed_entries: PropositionSeedEntry[];
  relevant_findings: Finding[];
  latest_assessment: Assessment | null;
  blocking_gaps: EvidenceGap[] | null;
  applied_inference_records: InferenceRecord[] | null;
  assessment_dependencies: Assessment[] | null;
};
```

约束：

- `latest_assessment` 是读取层选出的当前 snapshot，不要求 `Assessment` 本体携带 latest 标志
- `latest_assessment = null` 表示尚未进入 assessment 流程
- 若 `latest_assessment = null`，则 `blocking_gaps`、`applied_inference_records` 与 `assessment_dependencies` 必须同时为 `null`
- `blocking_gaps = []` 表示已评估且当前无阻塞缺口
- 若 assessment 存在但尚无 inference records，`applied_inference_records` 返回 `[]`
- `seed_entries` 必须保留 `PropositionSeedRef.role`，不得在 hydration 时退化成只剩 `Finding[]`
- `assessment_dependencies` 只覆盖 `applied_inference_records.input_assessment_ids` 的直接 assessment 输入，不递归展开全历史链

## Assessment Snapshot Transition Details

本节补足 `assessment` 从 `latest_assessment = null` 进入首个 snapshot、以及后续 snapshot 如何 supersede 的规范 transition 语义。

### `latest_assessment = null` 与首个 snapshot 的边界

`latest_assessment = null` 是 proposition 的合法早期状态，不等价于 assessment failure，也不强制要求系统先写一个占位 `insufficient` snapshot。

v1 采用按需创建（on-demand creation）：

- proposition 注册成功后，可以暂时保持 `latest_assessment = null`
- 只有在该 proposition 实际进入一次 assessment recompute，且形成 canonical assessment 输出时，才创建首个 snapshot
- 首个 snapshot 的 `status` 可以直接是 `insufficient`、`supported`、`contradicted` 或 `mixed`
- 不要求所有 proposition 都先经历 `null -> insufficient -> ...` 的固定路径

因此：

- “尚未评估” 与“已评估但证据不足”必须严格区分
- 读取层不得把 `latest_assessment = null` 自动投影成 `status = insufficient`

### 首个 snapshot 的合法转换

当 proposition 首次形成 assessment snapshot 时，允许的 transition 为：

- `null -> insufficient`
- `null -> supported`
- `null -> contradicted`
- `null -> mixed`

这里的 `null` 不是 `Assessment.status` 枚举成员，而是指 proposition 之前尚无任何 committed assessment snapshot。

### 已评估 proposition 的状态转换矩阵

对已有 latest snapshot 的 proposition，v1 允许以下 `status` 迁移：

- `insufficient -> supported | contradicted | mixed`
- `supported -> insufficient | contradicted | mixed`
- `contradicted -> insufficient | supported | mixed`
- `mixed -> insufficient | supported | contradicted`

同状态保持（例如 `supported -> supported`）本身不是非法；只要其他 canonical output 发生变化，仍必须通过新的 superseding snapshot 表达，而不是原地修改旧 snapshot。

### 必须形成 superseding snapshot 的变化

以下任一变化都必须创建新的 superseding snapshot：

- `status` 变化
- `confidence_grade` 变化
- `confidence_rationale` 变化
- `supporting_finding_ids` 变化
- `opposing_finding_ids` 变化
- `gap_memberships` 变化
- `applied_inference_record_ids` 变化
- subtype payload 中任何影响 judgment semantics 或 agent 决策的 canonical 字段变化

因此以下场景即使 `status` 不变，也必须 supersede：

- 结论仍为 `supported`，但 support / oppose membership 收缩或扩张
- 结论仍为 `insufficient`，但 blocking gaps 改变
- 结论仍为 `contradicted`，但 confidence rationale 改变

### 可以复用当前 latest snapshot 的情况

以下情况可以不创建新 snapshot：

- 重复执行 assessment recompute，但 canonical assessment 输出完全一致
- 仅 projection 排序、截断或展示文案变化
- 与当前 latest snapshot 无关的历史 inference record 补录
- 仅补充非规范性说明文字，且不改变任何 canonical 字段

### supersede 链规则

v1 的 assessment 历史必须形成单条线性 supersede 链。

要求：

- 首个 snapshot 的 `supersedes_assessment_id = null`
- 后续 snapshot 必须指向同一 proposition 的上一 latest snapshot
- 不允许跳过上一 latest snapshot 直接 supersede 更早历史
- 不允许并发分叉出多个 latest
- `snapshot_seq` 必须与线性 supersede 链一致地单调递增

### latest 选择规则

`latest_assessment` 采用严格链路（strict-chain）规则，而不是容错兜底规则。

要求：

- 正常情况下，latest 由单调 `snapshot_seq` 与线性 `supersedes_assessment_id` 链唯一确定
- 若 supersede 链断裂、跳链、形成分叉，或与 `snapshot_seq` 次序冲突，应视为 canonical integrity error
- 出现上述损坏时，读取层必须显式暴露链损坏状态，而不是退回“最大 `snapshot_seq`”或“最新 `created_at`”作为兜底 latest

### Gap reopen 规则

`EvidenceGap` 的 reopen 以 requirement semantics 为事件语义，而不是对旧 gap object 原地回写。

规则：

- gap 被解决后，该 gap object 保持 `status = resolved`
- 若后续 assessment 中相同 `missing_requirement` 再次缺失，应创建新的 `gap_id`
- 新 gap 的打开必须由新的 `InferenceRecord.opened_gap_ids` 显式驱动
- 旧 resolved gap 不得通过把 `status` 改回 `open` 来表示“重新打开”

这种建模保证：

- 单个 gap object 的生命周期是 `open -> resolved`
- requirement 级的多次缺失事件可以通过多个 gap 实例审计
- assessment snapshot 对 gap membership 的变化保持 append-only 语义

## Snapshot 创建触发规则

以下任一变化都必须创建新的 assessment snapshot：

- `status` 变化
- `confidence_grade` 变化
- `supporting_finding_ids` 或 `opposing_finding_ids` 变化
- `gap_memberships` 变化
- `applied_inference_record_ids` 变化
- subtype payload 中任何会改变 agent 决策的 canonical 字段变化

以下情况可以不创建新 snapshot：

- 重复执行评估但 canonical 输出完全一致
- 仅投影视图排序、截断或展示文案变化
- 与当前 snapshot 无关的历史 inference record 补录

对 `confidence_rationale` 的要求：

- 若 `confidence_rationale` 的 canonical 内容变化，必须新建 snapshot
- 若只是补充投影说明文字或其他非 canonical 文案，且不改变任何规范字段，不应新建 snapshot

## 与 Proposition / Finding / Action Proposal 的边界

### Assessment 和 Proposition 的边界

assessment 不得承载：

- proposition payload 本身
- proposition identity 规则
- creation-time seed semantics

proposition 不得承载：

- `status`
- `confidence_grade`
- 实时证据归属
- gap state
- applied rule state

### Assessment 和 Finding 的边界

assessment 可以引用 finding，但不能把 finding 事实回写成 judgment 字段。

例如：

- `delta_pct` 仍属于 finding / artifact 语义
- “当前支持 proposition” 属于 assessment 语义

### Assessment 和 Action Proposal 的边界

assessment 可以为动作规划提供输入，但不应包含：

- `priority`
- `recommended_action`
- `expected_information_gain`
- `estimated_cost`

这些字段属于 `action proposal`。

## Test Cases

后续实现至少应满足以下 schema-level 验收场景：

1. 同一 proposition 多次评估产生多个 assessment snapshots，读取层始终只能选出一个 `latest_assessment`。
2. 新 finding 到来时建立新 assessment snapshot，而不是原地修改旧 snapshot。
3. 新 snapshot 创建后，旧 snapshot 的字段值保持不变。
4. `latest_assessment = null` 与 `status = insufficient` 可明确区分。
5. proposition 注册后允许继续保持 `latest_assessment = null`，直到首次实际重算形成 canonical 输出。
6. proposition 的首个 snapshot 可以直接是 `supported`、`contradicted` 或 `mixed`，不要求先经过 `insufficient`。
7. `supported` assessment 仍可通过 `gap_memberships` 携带 blocking gap。
8. `mixed` assessment 可同时存在 `supporting_finding_ids` 与 `opposing_finding_ids`。
9. `confidence_grade` 或 `confidence_rationale` 变化会产生新 assessment snapshot，但不改变 proposition identity。
10. 同一 open gap 跨多个 assessment snapshots 持续存在时复用 `gap_id`。
11. gap 被解决后保留历史 object，并通过 `status = resolved` 标记。
12. 已 resolved 的 gap 若再次满足相同缺失条件，必须创建新的 `gap_id`，而不是回写旧 gap。
13. inference record 必须支持 `miss` 与 `partial`，不能只支持 `hit`。
14. assessment 可通过 `applied_inference_record_ids` 完整回溯当前状态来源。
15. projection 可以裁剪 gap / inference record / finding 列表，但不得改写规范标识。
16. 不同 session 中语义相同的 assessment / gap / inference record 不复用 ID。
17. `assessment_type` 若与 proposition `assessment_anchor` 不一致，必须校验失败。
18. `latest_assessment = null` 时，`blocking_gaps` 与 `applied_inference_records` 不得返回 `[]`。
19. assessment 链损坏时，读取层必须显式暴露 integrity error，而不是兜底选择 latest。
20. `required_subject` / `suggested_subject` / `subject` 必须是结构化对象，不能是裸字符串。
21. `AssessmentRef` / `EvidenceGapRef` / `InferenceRecordRef` 形状固定且可稳定引用。

负向场景至少覆盖：

1. 不允许在没有新 `InferenceRecord` 或其他规范状态变化的情况下空转生成新 snapshot。
2. 不允许通过修改旧 snapshot 来表达“latest”变化。
3. 不允许 `supersedes_assessment_id` 跳过上一 latest snapshot。
4. 不允许 assessment 链损坏时退回 `snapshot_seq` 或 `created_at` 兜底选择 latest。
5. 不允许把已 resolved 的 gap 原地改回 `open` 来表达 reopen。
6. 不允许 `input_assessment_ids` 跨 proposition 引用或形成循环。
7. 不允许 `opened_by_inference_record_id` 指向其他 proposition 或其他 session 的 inference record。
8. 不允许 `schema_version` 变化导致旧 snapshot 的 `assessment_id` 或 `inference_record_id` 被重新解释。
9. 不允许在 canonical ref 位置继续使用字符串 locator。
10. 不允许把核心 gap 语义只写在 `title` / `description` 等自由文本里。

## 非目标

v1 不包含以下能力：

- 跨 session assessment registry
- 数值型 black-box confidence score
- 把 recommendation / prioritization 混入 assessment 本体
- 在 proposition 层持有 live evidence state
- 为现有 claim persistence 设计兼容层
