# 推断规则引擎契约

本文档定义 Factum 证据引擎中推断规则引擎（inference rule engine）的拟议契约。

状态：draft design。本文是 `docs/analysis/` 下的规范推断设计提案，不表示对应实现、持久化模型或 HTTP endpoint 已经存在。

## 目的

`assessment.md` 已定义 `Assessment`、`EvidenceGap`、`InferenceRecord` 的规范 schema；`evidence-engine-runtime-lifecycle.md` 已定义评估状态重算的生命周期边界。

本文补足的是推断规则引擎本身的契约，回答以下问题：

- 规则引擎按什么评估单元运行
- 规则族（rule family）如何组织、排序和协作
- 哪些情况会升级、降级或保持评估状态
- `hit / miss / partial` 如何稳定写入 `InferenceRecord`
- 候选评估快照与推断记录如何形成无循环依赖的提交闭环
- 状态面 / 上下文面如何稳定读取”命中了哪些规则族”

本文不重复定义 `Assessment`、`EvidenceGap`、`InferenceRecord` 的字段 shape；如有冲突，以 [`assessment.md`](assessment.md) 为准。

本文也不单独定义规范关系分类；对象间允许的 edge family、方向与 authority 以 [`evidence-graph-edge-semantics.md`](evidence-graph-edge-semantics.md) 为准。

对于某个具体 `rule_family` 的 family-level schema 契约，若已有独立文档，则以对应 family 文档补充本文的通用规则；当前 `precondition_gate`、`quality_gate` 与 `comparability_gate` 的细化设计分别见 [`precondition-gate-contract.md`](precondition-gate-contract.md)、[`quality-gate-contract.md`](quality-gate-contract.md) 与 [`comparability-gate-contract.md`](comparability-gate-contract.md)。

## 核心设计决策

### 1. 推断是显式规则过程，不是自由文本解释

推断引擎的输入固定为：

- target `proposition`
- 当前 proposition closure 中可解引用的 `findings`
- 同一 proposition 下更早的 `assessment snapshots`
- 同一 proposition 当前仍为 `open` 的 `EvidenceGap`

推断引擎的输出固定为：

- 候选 `Assessment` 快照载荷
- 该候选快照采用的 `InferenceRecord` 集合
- 该候选快照打开、保持或解决的 `EvidenceGap`

引擎不输出：

- narrative summary
- 独立 execution telemetry log
- 脱离规范 schema 的临时评分对象
- 对其他 proposition 的隐式读取结论

### 2. 评估单元固定为单个命题的单次重算

一次推断评估只针对单个 `proposition_id` 运行。

要求：

- 所有规则结果都绑定同一个 target proposition
- 单次重算内产出的 `InferenceRecord` 都属于同一个候选评估快照
- 不允许跨命题组装一个评估状态
- 引擎不直接读取其他命题或其他命题的评估状态作为推断输入

若未来需要跨命题判断，必须先通过独立规范关系对象或 finding-like 规范输入进入判断层，而不是让 v1 引擎直接跨命题读取状态。

换言之，引擎只能消费 v1 已允许的规范边：同一 proposition 下的种子闭包、latest/prior assessment lineage、gap membership 与 rule direct inputs；不得在实现层额外发明跨命题关系。

### 3. 命名层级固定为 `assessment_type -> rule_family -> rule_id`

v1 固定三层：

- `assessment_type`：由 proposition 的 `assessment_anchor` 决定
- `rule_family`：稳定执行阶段与判断目标分组
- `rule_id`：单条规则的稳定标识，写入 `InferenceRecord.rule_id`

设计要求：

- 规则族是引擎契约与读取面的稳定分组单位
- `rule_id` 是审计、重放与兼容检查的最小可引用单位
- 任何更细的业务分类只可作为 rule metadata 或规则簇（rule cluster），不得冒充新的规则族

`rule_id -> rule_family -> assessment_type` 的稳定解引用，由独立的 [`rule-registry-contract.md`](rule-registry-contract.md) 定义。

### 4. 规则引擎采用固定顺序、仅变化提交契约

评估状态重算可以反复执行，但只有规范判断输出变化时才写入新的评估快照。

固定顺序：

1. 装载评估上下文
2. 预分配候选 `assessment_id`
3. 运行 gate / evidence / resolution / confidence 规则
4. 生成绑定候选 `assessment_id` 的 `InferenceRecord` 集合
5. 组装候选评估载荷
6. 决定是否提交新的 `Assessment`

实现可并行执行同阶段内互不依赖的规则，但对外可观察结果必须与上述固定顺序一致。

### 5. v1 不追求状态单调增强

规则引擎必须允许：

- `insufficient -> supported | contradicted | mixed`
- `supported | contradicted -> mixed`
- `supported | contradicted | mixed -> insufficient`

触发原因可以包括：

- 新事实单元到来
- 实时证据当前不可读导致归属收缩
- 质量 / 可比性门槛失败
- 已解决的缺口重新打开
- 与先前评估比较发现当前证据不足以延续旧结论

单调的只有 `snapshot_seq`，不是结论强度。

## Schema Position

规范判断链路保持：

`finding -> proposition -> 推断规则引擎 -> assessment / evidence_gap / inference_record`

其中：

- `finding` 提供确定性事实输入
- `proposition` 提供判断锚点与 `assessment_type`
- 推断规则引擎提供显式规则过程
- `assessment` 表达当前判断状态
- `evidence_gap` 表达当前缺失条件
- `inference_record` 表达当前快照的直接规则依据

## Typed Design Sketch

以下类型仅用于说明引擎契约，不替代规范 schema。

```ts
type InferenceRuleFamily =
  | "precondition_gate"
  | "quality_gate"
  | "comparability_gate"
  | "support_evidence"
  | "oppose_evidence"
  | "status_resolution"
  | "gap_management"
  | "confidence_shaping"
  | "assessment_transition";

type InferenceEvaluationContext = {
  session_id: string;
  proposition: Proposition;
  available_findings: Finding[];
  prior_assessments: Assessment[];
  open_gaps_from_latest: EvidenceGap[];
  assessment_type: Assessment["assessment_type"];
  evaluation_reason:
    | "proposition_registered"
    | "finding_arrived"
    | "finding_invalidated"
    | "gap_recheck"
    | "assessment_replay";
};

type CandidateAssessmentIdentity = {
  proposition_id: string;
  assessment_type: Assessment["assessment_type"];
  supersedes_assessment_id: string | null;
  snapshot_seq: number;
  assessment_id: string;
};

type InferenceRuleDefinition = {
  rule_id: string;
  rule_family: InferenceRuleFamily;
  applies_to: Assessment["assessment_type"][];
  stage_order: number;
  reads: Array<"findings" | "prior_assessments" | "open_gaps">;
  may_emit: {
    status_transition: boolean;
    open_gap: boolean;
    resolve_gap: boolean;
    confidence_contribution: boolean;
  };
};

type InferenceEvaluationOutcome = {
  candidate_identity: CandidateAssessmentIdentity;
  candidate_status: Assessment["status"];
  supporting_finding_ids: string[];
  opposing_finding_ids: string[];
  blocking_gap_ids: string[];
  non_blocking_gap_ids: string[];
  confidence_grade: Assessment["confidence_grade"];
  confidence_rationale: Assessment["confidence_rationale"];
  inference_records: InferenceRecord[];
};
```

## Engine Input Contract

### proposition 输入

每次评估必须从单个 proposition 开始，并显式读取：

- `proposition_id`
- `assessment_anchor`
- proposition subject 信息
- creation-time `seed_finding_refs`

规则引擎不得自行改写 proposition identity 或评估锚点。

### finding 输入

可供规则消费的事实单元必须满足：

- 已提交到规范事实层
- 能通过稳定规范 id 解引用
- 与 target proposition 的 subject / family / seed / prior inference dependency 有明确关系

引擎不得直接读取投影摘要或自由文本证据描述作为判断输入。

### prior assessment 输入

先前评估状态只允许来自同一 proposition 的历史快照。

典型用途：

- 检查是否存在已解决缺口的重开
- 检查是否发生状态降级
- 检查置信度是否应保持、下降或重算

## Trigger Contract

评估状态重算至少在以下事件后触发：

- 新命题注册
- target proposition 直接依赖的事实单元到达
- target proposition 直接依赖的事实单元当前不可读或被重放替换
- 当前开放缺口可能被新事实单元或质量修复闭合
- 先前最新评估被取代，需要重算 transition-sensitive 规则

调度策略可以不同，但规范结果必须满足：

- 相同规范输入重算，不额外生成新快照
- 输入变化导致判断输出变化时，必须生成新快照

## Rule Family Contract

### 通用规则族

所有评估类型至少复用以下规则族：

#### 1. `precondition_gate`

职责：

- 判断是否具备进入该评估类型的最低输入条件
- 在缺少必需事实单元类型、主语覆盖、时间覆盖时打开缺口

输出约束：

- 通常产出 `miss` 或 `partial`
- 可打开阻塞性缺口
- 不直接给出高置信单向结论

family-level 补充：

- 最低输入前提、缺口映射、条件 token 与记录写法由 [`precondition-gate-contract.md`](precondition-gate-contract.md) 进一步定义

#### 2. `quality_gate`

职责：

- 处理 `data_complete`、`sample_size`、`quality_status`、`null_rate` 等质量门槛

输出约束：

- 可打开 `data_quality_risk`
- 可压低置信度
- 可触发从强结论降级为 `insufficient`

family-level 补充：

- 质量要求、`data_quality_risk` 缺口映射、条件 token 与结构化质量影响写法由 [`quality-gate-contract.md`](quality-gate-contract.md) 进一步定义

#### 3. `comparability_gate`

职责：

- 判断左右窗口、切片、粒度、方法前提是否可比

输出约束：

- 可比性失败优先表现为缺口或保守降级
- 不允许在可比性未通过时产出高置信度的 `supported` / `contradicted`

family-level 补充：

- 可比性要求、`comparability_risk` 缺口映射、条件 token 与结构化可比性影响写法由 [`comparability-gate-contract.md`](comparability-gate-contract.md) 进一步定义

#### 4. `support_evidence`

职责：

- 累积直接支持 proposition 的事实单元

输出约束：

- 只能把事实单元纳入 `supporting_finding_ids`
- 不负责决定最终状态

#### 5. `oppose_evidence`

职责：

- 累积直接反驳 proposition 的事实单元

输出约束：

- 只能把事实单元纳入 `opposing_finding_ids`
- 不负责决定最终状态

#### 6. `status_resolution`

职责：

- 根据 support / oppose / blocking gaps / precondition 状态决定最终 `Assessment.status`

输出约束：

- 若最终状态变化，必须写 `produced_status_transition`
- 只允许写入与最终候选快照一致的 transition

#### 7. `gap_management`

职责：

- 把本次规则判断映射为缺口打开 / 保持 / 解决

输出约束：

- `opened_gap_ids` / `resolved_gap_ids` 必须由具体记录驱动
- 缺口的打开、解决不得 out-of-band 写入

#### 8. `confidence_shaping`

职责：

- 形成 `confidence_rationale`
- 在护栏内推出 `confidence_grade`

输出约束：

- 只能根据结构化理由塑形置信度
- 不得用黑盒数值覆盖依据维度

#### 9. `assessment_transition`

职责：

- 读取先前最新评估，判断是否发生升级、降级或保持状态但改变支持/缺口

输出约束：

- 用于解释为什么会取代旧快照
- 不单独决定 support / oppose 归属

### assessment-type 到规则族的映射

v1 先固定 `assessment_type -> rule_family` 的映射，不在本文展开到每条业务规则。

| assessment_type | required rule families | optional rule clusters |
| --- | --- | --- |
| `change_assessment` | `precondition_gate`, `quality_gate`, `comparability_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `change_magnitude`, `direction_consistency` |
| `decomposition_assessment` | `precondition_gate`, `quality_gate`, `comparability_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `coverage_balance`, `residual_explained` |
| `anomaly_assessment` | `precondition_gate`, `quality_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `baseline_stability`, `repeat_occurrence` |
| `correlation_assessment` | `precondition_gate`, `quality_gate`, `comparability_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `lag_alignment`, `confounder_exposure` |
| `test_hypothesis_assessment` | `precondition_gate`, `quality_gate`, `comparability_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `significance`, `effect_size` |
| `forecast_assessment` | `precondition_gate`, `quality_gate`, `support_evidence`, `oppose_evidence`, `status_resolution`, `gap_management`, `confidence_shaping`, `assessment_transition` | `forecast_stability`, `interval_reliability` |

要求：

- 可选规则簇只是更细的业务分类，不是新的规则族
- 不允许某个评估类型绕过通用 `status_resolution` 直接写评估状态
- v1 新增评估类型时，必须先声明其规则族映射，再进入规范契约

规则的稳定定义、族归属与版本边界由 [`rule-registry-contract.md`](rule-registry-contract.md) 统一定义。

## Candidate Assessment And Record Materialization

### 候选标识预分配

为避免 `InferenceRecord.assessment_id` 与 `Assessment.applied_inference_record_ids` 的循环依赖，单次重算必须先预分配候选评估标识。

固定顺序：

1. 读取先前最新评估
2. 验证先前 latest assessment 链在线性 supersede 规则下可解
3. 决定 `supersedes_assessment_id`
4. 计算候选 `snapshot_seq`
5. 生成候选 `assessment_id`
6. 用该候选 `assessment_id` 生成本次推断记录
7. 用生成好的推断记录 ids 回填候选评估载荷
8. 若规范结果与先前最新评估完全一致，则丢弃候选，不提交快照与记录

候选标识只用于本次重算的规范实体化，不是额外暴露给消费者的新对象类型。

latest 链校验要求：

- 先前 latest assessment 必须能通过线性 `supersedes_assessment_id` 链与单调 `snapshot_seq` 自洽解释
- 若链断裂、跳链、分叉，或与 `snapshot_seq` 次序冲突，应视为 canonical state error
- 出现该错误时，引擎不得继续兜底选主并写入新的 assessment snapshot

### 仅变化提交规则

只有以下规范输出任一变化时，才提交新快照：

- `status`
- `confidence_grade`
- `confidence_rationale`
- `supporting_finding_ids`
- `opposing_finding_ids`
- `blocking_gap_ids`
- `non_blocking_gap_ids`
- `applied_inference_record_ids`
- 子类型载荷中影响判断语义的字段

若上述输出完全不变：

- 不提交新的评估快照
- 不提交新的推断记录
- 继续复用先前最新评估

## Fixed Evaluation Order

### 1. 上下文组装

收集：

- target proposition
- 可用事实单元
- 先前最新评估及必要历史快照
- 当前开放缺口

若 proposition 的 `assessment_anchor` 与期望 `assessment_type` 不一致，应视为上游契约错误，而不是产出 `miss`。

### 2. 候选标识分配

先分配候选 `assessment_id`，再执行规则结果实体化。

这是 v1 唯一允许 `InferenceRecord` 绑定”尚未提交快照”的方式；一旦本次重算被放弃，候选 id 不进入消费者可见状态。

如果 proposition 之前没有任何 committed assessment snapshot，则：

- `supersedes_assessment_id = null`
- `snapshot_seq` 从该 proposition 的首个 assessment 序号开始
- 首个候选 snapshot 不要求先固定为 `insufficient`

### 3. 门槛评估

依次运行：

- `precondition_gate`
- `quality_gate`
- `comparability_gate`

门槛阶段的职责是决定：

- 是否缺少必要输入
- 是否存在质量或可比性阻塞
- 是否需要直接打开阻塞性缺口

门槛阶段不得直接把状态提升为高置信度 `supported` / `contradicted`。

### 4. 证据聚合

分别运行：

- `support_evidence`
- `oppose_evidence`

该阶段只决定实时证据归属：

- 哪些事实单元被计入支持
- 哪些事实单元被计入反驳
- 哪些事实单元只作为缺口 / 规则上下文，而不进入方向性归属

### 5. 状态决议

最终状态的判定口径不在本文内枚举，而统一由 [`assessment-judgment-policy.md`](assessment-judgment-policy.md) 定义。

本文只固定：

- `status_resolution` 必须以门槛、支持、反驳的结构化结果为输入
- 输出只能是 `insufficient | supported | contradicted | mixed`
- 结果必须与最终候选快照一致

### 6. 缺口管理

缺口解决在最终状态候选确定后执行，以决定：

- 哪些缺口继续保持开放
- 哪些缺口被新的证据显式解决
- 哪些缺口从阻塞转成非阻塞，或反之

若缺口归属改变，即使状态不变，也必须产出新快照。

### 7. 置信度塑形

置信度在最终状态和缺口状态确定后计算。

原因：

- 置信度依赖最终证据充分性 / 一致性 / 规则覆盖 / 质量影响
- 若先算置信度，再改状态/缺口，会导致依据与快照不一致

### 8. 转换最终化

最后比较先前最新评估与候选结果，决定：

- 是否发生升级 / 降级 / 横向转换
- 是否只是同状态下的证据归属或缺口变化
- 是否完全无规范变化从而复用旧快照

若 target proposition 之前没有 latest assessment，则本阶段还必须决定：

- 是否从 `null` 首次进入 `insufficient | supported | contradicted | mixed`
- 是否当前重算仍不足以形成 committed assessment output；若不足，则保持 `latest_assessment = null`

## Status Resolution Policy Binding

### 证据优先状态格

最终状态只能是：

- `insufficient`
- `supported`
- `contradicted`
- `mixed`

### 策略来源

以下口径不在本文内展开，而统一由 [`assessment-judgment-policy.md`](assessment-judgment-policy.md) 定义：

- 什么算实质支持
- 什么算实质反驳
- 何时进入 `mixed`
- 何时即使存在单向证据也必须保守回到 `insufficient`

本文只要求引擎对这些判断口径做确定性执行，不允许实现层临时拍板。

## Gap Policy

### 打开缺口

缺口打开条件包括：

- 必需事实单元类型缺失
- 必需主语覆盖 / 时间覆盖缺失
- 规则前提不满足
- 数据质量 / 可比性风险阻塞当前判断

### 解决缺口

缺口解决需要满足两个条件：

- 新证据或新质量状态满足了原 `missing_requirement`
- 某条 `InferenceRecord` 显式把该缺口记入 `resolved_gap_ids`

仅仅”本次没有再提到旧缺口”不等价于已解决。

### Reopen 规则

v1 将 gap reopen 建模为新的缺失事件，而不是旧 gap object 的状态回退。

规则：

- 已 `resolved` 的 gap 不得被改回 `open`
- 若相同 `missing_requirement` 后续再次缺失，应创建新的 `gap_id`
- 新 gap 的打开必须由新的 `InferenceRecord.opened_gap_ids` 显式记录
- 旧 resolved gap 可以作为 `input_assessment_ids` 关联的历史依据被读取，但不重新进入当前 gap membership

### 阻塞 vs 非阻塞

规则：

- 会阻止状态升级或稳定收敛的缺口必须进入 `blocking_gap_ids`
- 只提供警示、但不改变当前可用判断的缺口进入 `non_blocking_gap_ids`

`supported` 或 `contradicted` 评估允许同时携带阻塞性缺口。

## Confidence Policy

### 依据优先

每次置信度塑形都必须先形成：

- `evidence_sufficiency`
- `evidence_consistency`
- `rule_coverage`
- `data_quality_impact`

再由这些结构化维度推出 `confidence_grade`。

### 全局护栏

复用并强调 [`assessment.md`](assessment.md) 已定义的护栏：

- `data_quality_impact = severe` 时，`confidence_grade` 不得高于 `low`
- `evidence_sufficiency = very_weak` 时，`confidence_grade` 不得高于 `low`
- `rule_coverage = minimal` 且 `evidence_consistency` 不是 `consistent` 时，`confidence_grade` 不得高于 `medium`
- `evidence_consistency = conflicting` 时，应优先 `mixed` 或保守 `insufficient`

## InferenceRecord Mapping Contract

### 记录何时必须产出

v1 至少要求：

- 所有对当前快照的 `status`、缺口状态、置信度有贡献的规则族，都必须有对应记录
- 不允许只记录 `hit` 而静默丢弃 `miss` / `partial`
- 不要求把与当前快照无关的历史记录回挂到 `applied_inference_record_ids`

### `result` 判定

- `hit`：该规则的核心条件满足，并对当前快照产生正向判断贡献
- `miss`：该规则所需条件未满足，且这一未命中对当前快照的 `insufficient`、缺口保持或保守降级有解释价值
- `partial`：部分条件满足，足以贡献上下文 / 警示 / 局部证据，但不足以单独完成其目标判断

### 理由写法

要求：

- `matched_conditions` 记录稳定条件 token
- `unmatched_conditions` 记录稳定缺失或失败 token
- `notes` 只补充少量非主语义说明，不得承载唯一规则语义

### 状态转换写法

- 只有参与最终状态决议的记录可以写 `produced_status_transition`
- 若最终状态未变，但支持/缺口/置信度变化，则该字段可为 `null`
- 首次评估建立时，`from_status = null`
- proposition 尚无 latest assessment 且本次未形成 committed assessment output 时，不生成 `produced_status_transition`

### 缺口字段写法

- 打开缺口的记录必须把对应 id 写入 `opened_gap_ids`
- 解决缺口的记录必须把对应 id 写入 `resolved_gap_ids`
- 同一缺口在同一快照内不能同时出现在打开和解决两个集合

### 族暴露

`InferenceRecord` schema 本身不新增 `rule_family` 字段。

v1 要求通过 [`rule-registry-contract.md`](rule-registry-contract.md) 中定义的稳定注册表，从 `rule_id -> rule_family -> assessment_type` 完全可解引用，并作为：

- 会话状态中”命中的规则族”的来源
- 上下文审计中规则分组的来源
- 重放 / 兼容检查的来源

实现不得把族归属写成隐式字符串约定。

## State / Context Consumption Contract

### 状态面

[`state-surface-schema.md`](state-surface-schema.md) 中的 `applied_inference_record_refs` 应满足：

- 仅索引 `latest_assessment.applied_inference_record_ids`
- 消费者通过规则注册表汇总命中的规则族
- 状态面不内嵌完整 `InferenceRecord` 载荷

### 上下文面

[`context-surface-schema.md`](context-surface-schema.md) 中的 `applied_inference_records` 应满足：

- 完整覆盖 `latest_assessment.applied_inference_record_ids`
- 足以解释当前状态、缺口、置信度为什么成立或未成立
- 不混入已取代快照的历史记录

## Non-goals

本文不定义：

- 对外 HTTP path、query 参数、分页与兼容策略
- 具体实现中的调度器、队列、事务边界或存储表结构
- 每个评估类型下完整的业务规则枚举表
- 动作候选的排序策略
- 跨命题推断
- 使用模型生成解释的提示词设计

## Acceptance Scenarios

1. 命题首次进入评估，但必需事实单元类型不足时，生成 `insufficient` 评估，并写入至少一条 `miss` 或 `partial` 的前提条件/缺口记录。
2. 新事实单元到来满足原阻塞性缺口后，旧缺口被显式解决，评估从 `insufficient` 升级为更强状态，具体状态口径由判断策略文档决定。
3. 强结论依赖的关键事实单元当前不可解引用时，状态可回退到 `insufficient`，并重新打开阻塞性缺口。
4. 状态不变但缺口集合变化时，仍生成新的评估快照。
5. 状态不变但置信等级或依据变化时，仍生成新的评估快照。
6. proposition 的首个 committed assessment 可以直接是 `supported`、`contradicted` 或 `mixed`，不要求先写占位 `insufficient`。
7. 已 resolved 的 gap 若同 requirement 再次缺失，应创建新的 gap，而不是把旧 gap 改回 `open`。
8. 同一次重算若规范结果完全不变，不生成新的评估快照，也不额外生成推断记录。
9. assessment 历史链损坏时，引擎报 canonical state error，不继续兜底选主或写新快照。
10. 状态面能从 `applied_inference_record_refs` 稳定汇总规则族；上下文面能审计单条 `rule_id` 的 hit/miss/partial 和直接输入。
11. 同一次重算中，记录可绑定候选 `assessment_id`，但只有快照被提交时这些记录才进入规范状态。

## 与其他文档的关系

- [`assessment.md`](assessment.md) 定义规范 `Assessment` / `EvidenceGap` / `InferenceRecord` schema
- [`rule-registry-contract.md`](rule-registry-contract.md) 定义规则注册表的稳定解引用契约
- [`assessment-judgment-policy.md`](assessment-judgment-policy.md) 定义不同 `assessment_type` 的判断策略与判断门槛
- [`evidence-engine-runtime-lifecycle.md`](evidence-engine-runtime-lifecycle.md) 定义评估重算的运行时生命周期与仅变化快照策略
- [`state-surface-schema.md`](state-surface-schema.md) 与 [`context-surface-schema.md`](context-surface-schema.md) 定义推断结果如何被读取面消费
