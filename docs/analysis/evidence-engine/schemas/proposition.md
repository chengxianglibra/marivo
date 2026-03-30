# Proposition Schema

本文档定义判断层中 `proposition` 的拟议类型契约。

状态：draft design。本文是规划中的规范 `proposition` Schema 提案，不表示对应 HTTP endpoint 或持久化模型已经实现。

## 目的

`proposition` 是 Factum 证据引擎中的规范判断对象（canonical judgment object），用于把 `finding` 层已经确定知道的事实，组织成可被后续推断（inference）和评估（assessment）稳定评估的命题。

设计目标：

- 让 agent 直接读取”要判断什么”，而不是从自由文本 claim 中反推判断对象
- 明确分离事实单元（`finding`）、命题（`proposition`）、评估状态（`assessment`）与动作候选（`action proposal`）
- 保持 proposition 为会话内局部、typed、可引用、可局部读取的规范对象
- 支持系统种子（system-seeded）与 agent 创作（agent-authored）两种来源，但共享统一评估轨道
- 为后续推断（inference）/ 评估（assessment）/ 动作候选（action proposal）提供稳定判断锚点

## 核心设计决策

### 1. `proposition` 是判断层的规范对象

`proposition` 位于规范抽象链路的第三层：

`artifact -> finding -> proposition -> assessment -> action proposal`

其职责是表达：

- 当前系统或 agent 准备判断什么
- 这个判断围绕哪个 subject
- 这个判断应由哪类 findings 与 assessment 规则来评估

它不负责表达：

- 当前判断结果
- 证据是否足够
- 当前支持 / 反驳集合
- 下一步动作建议

### 2. `proposition` 是会话内局部（session-local）判断对象

v1 中 `proposition` 的标识边界绑定单个 session。

要求：

- 同一 session 中，同一 judgment semantics 重读时得到相同 `proposition_id`
- 不同 session 中，即使语义相同，也不复用 `proposition_id`
- 规范层不做跨 session proposition registry 或隐式合并

这使 proposition 可以直接服务 session 内的 agent planning，而不被跨 session 规范化复杂度拖累。

### 3. `proposition` 使用 typed union，而不是 generic predicate

v1 采用 `proposition_type` 判别联合。

原因：

- Factum 已按 typed intent / typed finding 设计，judgment layer 应延续这种类型化边界
- 不同命题家族的标识、种子事实家族、assessment 规则家族不同
- 用统一 predicate/object 抽象会削弱 agent 的稳定读取轴，也会提高校验歧义

因此：

- base schema 只保留跨 subtype 稳定存在的 judgment 轴
- 各 subtype 用 payload 表达其专属判断语义

### 4. `proposition` 的最小粒度是原子可评估单元（atomic evaluable unit）

一个 proposition 只表达一个可被单独支持、反驳、阻塞或升级的判断。

例如：

- “metric X 在 slice Y 上发生下降” 是一个 proposition
- “dimension item = US 是该 delta 的重要贡献项” 是一个 proposition
- “序列 A 与序列 B 存在统计关联” 是一个 proposition

不允许把多个可独立评估的判断捆成一个 topic bundle。

这样做的原因是：

- assessment 才能保持单义
- evidence gaps 才能精确绑定
- agent 才能围绕 proposition 做局部最小闭包读取

### 5. `proposition` 同时支持系统种子（system-seeded）与 agent 创作（agent-authored）

v1 支持两类来源：

- `system_seeded`：由确定性种子模板（seed template）根据 findings 创建
- `agent_authored`：由 agent 显式提出假设（hypothesis），再进入统一评估流程

两者共用同一套规范 Schema，但谱系（lineage）要显式区分来源与创建方式。

### 6. `proposition` 只保留创建时种子引用（creation-time seed refs）

`proposition` 可以记录创建时用于建模的种子事实引用（seed finding ref），但不得持有当前实时证据集合。

原因：

- proposition 回答的是”要判断什么”
- assessment 回答的是”当前判断到什么程度”

因此：

- `seed_finding_refs` 只用于溯源信息（provenance）、初始建模与局部闭包读取
- `supporting_finding_ids`、`opposing_finding_ids`、`missing_requirements` 必须留在 assessment 或推断记录（inference record）

这些字段对应的 canonical relation 语义由 [`evidence-engine/graph-and-reference-semantics.md`](../graph-and-reference-semantics.md) 统一定义：

- `seed_finding_refs` 承载 `proposition -> finding` 的 `seeded_by`
- `lineage.derived_from_proposition_ref` 承载 `proposition -> proposition` 的 `derived_from`

二者都属于谱系（lineage）/ 溯源信息（provenance）edge，不得被读取层或实现层误解释为实时证据归属（live evidence membership）。

## Schema Position

职责分工：

- `artifact`：完整、可复现、可审计的步骤输出
- `finding`：从 artifact 中确定性抽取出的原子事实单元
- `proposition`：待评估的结构化命题
- `assessment`：系统当前对 proposition 的评估状态
- `action proposal`：服务于 agent 的动作候选

## Typed Schema

```ts
type Proposition =
  | ChangeProposition
  | DecompositionProposition
  | AnomalyProposition
  | CorrelationProposition
  | TestHypothesisProposition
  | ForecastProposition;

type PropositionBase = {
  proposition_id: string;
  proposition_type:
    | "change"
    | "decomposition"
    | "anomaly"
    | "correlation"
    | "test_hypothesis"
    | "forecast";
  session_id: string;
  subject: PropositionSubject;
  origin: PropositionOrigin;
  assessment_anchor: PropositionAssessmentAnchor;
  lineage: PropositionLineage;
  seed_finding_refs: PropositionSeedRef[];
  created_at: string;
  schema_version: string;
};

type PropositionSubject = {
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

type PropositionRef = {
  session_id: string;
  proposition_id: string;
};

type PropositionOrigin =
  | {
      kind: "system_seeded";
      template_id: string;
      template_version: string;
    }
  | {
      kind: "agent_authored";
      author_type: "agent";
      authored_label: string | null;
      authored_input_ref: PropositionAuthoredInputRef | null;
    };

type PropositionAssessmentAnchor = {
  assessment_type:
    | "change_assessment"
    | "decomposition_assessment"
    | "anomaly_assessment"
    | "correlation_assessment"
    | "test_hypothesis_assessment"
    | "forecast_assessment";
};

type PropositionLineage = {
  creation_mode: "seeded" | "authored";
  source_artifact_lineages: ArtifactLineageRef[];
  source_step_refs: StepRef[];
  derived_from_proposition_ref: PropositionRef | null;
  derivation_version: string;
};

type ArtifactLineageRef = {
  artifact_id: string;
  artifact_schema_version: string | null;
  extractor_version: string | null;
};

type StepRef = {
  session_id: string;
  step_id: string;
  step_type: string;
};

type PropositionSeedRef = {
  finding_ref: FindingRef;
  role: "primary" | "secondary" | "context";
};

type FindingRef = {
  session_id: string;
  finding_id: string;
};

type ArtifactRef = {
  artifact_id: string;
};

type ArtifactItemRef = {
  collection:
    | "value"
    | "rows"
    | "buckets"
    | "candidates"
    | "points"
    | "result";
  index: number | null;
  key: string | null;
};

type ArtifactItemRefRef = {
  artifact_ref: ArtifactRef;
  item_ref: ArtifactItemRef;
};

type PropositionAuthoredInputRef =
  | {
      kind: "finding_ref";
      finding_ref: FindingRef;
    }
  | {
      kind: "proposition_ref";
      proposition_ref: PropositionRef;
    }
  | {
      kind: "artifact_item_ref";
      artifact_item_ref: ArtifactItemRefRef;
    };

type ResolvedTimeScope =
  | { kind: "range"; start: string; end: string }
  | { kind: "snapshot_now"; observed_at: string }
  | { kind: "latest_available"; data_as_of: string }
  | { kind: "as_of"; at: string };

type CorrelationJoinBasis =
  | {
      kind: "time_aligned";
      grain: "hour" | "day" | "week" | "month";
      key_fields: string[];
    }
  | {
      kind: "shared_key";
      key_fields: string[];
      grain: "hour" | "day" | "week" | "month" | null;
    };
```

## 公共字段语义

### proposition_id

`proposition_id` 是规范判断标识符。

推荐生成输入：

- `session_id`
- `proposition_type`
- subtype payload 中决定 judgment identity 的字段
- base / payload 中显式声明为 judgment semantics 的稳定 typed refs

禁止把以下字段作为 identity 输入：

- `schema_version`
- assessment status
- confidence
- 当前 supporting / opposing evidence sets
- 创建时 seed refs 的顺序

目标是让 proposition identity 由 judgment semantics 决定，而不是由某次评估快照决定。

### 标识归一化

标识计算必须先对输入字段做规范化，再做稳定序列化。

v1 规则：

- 对象字段按 key lexical order 排序后再序列化
- `subject.slice` 与所有 payload 中的 key-value map 都使用相同规则
- number 不做 string coercion；`1` 与 `"1"` 必须视为不同值
- decimal number 参与 identity 时，使用 canonical decimal string：禁止科学计数法，去除无意义尾随零，因此 `0.05` 与 `0.050` 等价
- boolean 与 `null` 保持 JSON 原生字面值，不做额外编码
- timestamp 不由 proposition 层重新解释精度；必须沿用上游 canonical `ResolvedTimeScope` 的已解析字符串
- 若 timestamp 含小数秒，序列化时去除无意义尾随零；若上游 contract 未提供小数秒，则 proposition 层不得补齐
- proposition 层不做 timezone rewriting；时间字符串必须已经符合 analysis layer 的 canonical time contract

推荐实现方式：

- 先构造仅包含 identity 字段的 normalized object
- 再对该 object 做 canonical JSON serialization
- 最后基于该 serialization 派生 `proposition_id`

### proposition_type

`proposition_type` 用于表达命题家族，而不是展示标签。

它决定：

- payload schema
- 合法的种子事实家族
- 默认 assessment family
- agent 的默认 focus grouping

### session_id

显式声明 proposition 的会话内局部边界。

`session_id` 是必须字段，不允许 `null`。

### subject

`subject` 是 proposition 面向 agent 与 assessment 的主语义锚点。

设计原则：

- 尽量与 `finding.subject` 共享轴
- `slice` 默认 `{}`，不允许 `null`
- base `subject` 只承载稳定的 focus anchor，不替代 subtype payload 中更具体的 subject 结构
- 若某个 subtype 天然需要并列主语义对象，base `subject` 必须是可确定性派生的 focus anchor，而真正的判断边界仍由 payload 中的 typed subjects 决定

`analysis_axis` 表达判断主要发生在哪类分析轴上，而不是来源 step 名。

`slice = {}` 的固定语义是 overall / unsliced subject，即：

- 该 proposition 针对其主 subject 没有额外 slice 约束
- 不表示 unknown
- 不表示 “调用方还没指定”

若调用方尚未明确 subject slice，系统不应创建规范 proposition。

在 `correlation`、`test_hypothesis` 这类双侧 payload 中：

- `left_subject.slice = {}`
- `right_subject.slice = {}`

表示左右两侧都以各自 overall subject 参与判断，即 “overall vs overall”，而不是 “未指定”。

双侧命题的 base `subject` 额外约束：

- `subject` 是供 agent 列表过滤、focus grouping、局部读取使用的 focus anchor
- `subject` 不单独决定 `correlation` / `test_hypothesis` 的 judgment identity
- identity 与评估语义仍由 `left_subject`、`right_subject` 及其 payload 字段共同决定
- 实现层必须为同一 payload 产生稳定、可重放的 base `subject`，不得依赖展示偏好或当前排序位置

### origin

`origin` 回答 proposition 是如何进入规范状态的。

约束：

- `system_seeded` 必须带 `template_id` 与 `template_version`
- `agent_authored` 必须带 author 谱系；若没有人类可读标签，可令 `authored_label = null`

该字段只描述来源，不表达当前判断结果。

### assessment_anchor

`assessment_anchor` 是 proposition 对评估家族（assessment family）的静态挂接点（anchor）。

它的作用是帮助：

- inference 选择适用规则族（rule family）
- agent 在不读完整 payload 时快速理解后续评估对象

该字段不包含 assessment 实例状态。

该字段只决定当前 proposition 进入哪个 `assessment_type`，不授予推断引擎（inference engine）直接读取其他 proposition 或其他 proposition assessment 的权限。

### 谱系（lineage）

`lineage` 用于说明 proposition 的创建路径。

要求：

- 区分 `seeded` 与 `authored`
- 显式记录 source artifact / step 谱系
- 若 proposition 由旧 proposition 派生，则填 `derived_from_proposition_ref`
- `derivation_version` 用于标识命题构建逻辑版本

v1 不做跨谱系的隐式标识复用。

模板（template）/ 派生（derivation）版本演化规则：

- `template_version` 或 `derivation_version` 的 breaking change 不触发旧 proposition 原地迁移
- 旧 proposition 保留原谱系与原 schema/version 边界
- 新版本 template 重新种子（seed）时，应生成新 proposition
- 若 breaking change 改变了判断标识边界，新的规范化结果必须产生新的 `proposition_id`
- proposition 层不承担跨版本自动兼容；兼容或映射应由投影（projection）或迁移（migration）层显式处理

### seed_finding_refs

`seed_finding_refs` 只记录创建时的种子事实（seed finding）。

规则：

- 可为空数组，表示该 proposition 由 agent authored 且无直接种子事实
- 空数组不表示”当前没有证据”，只表示”创建时没有种子事实”
- 不允许将实时支持（support）/ 反驳（oppose）集合写回本字段

### Reference Rules

`proposition` 及其 payload 中的规范引用（canonical ref）必须使用结构化 typed ref。

通用的 hard / soft ref 分类、跨 session 禁止边界、dangling read semantics 与 closure integrity 以 [`evidence-engine/graph-and-reference-semantics.md`](../graph-and-reference-semantics.md) 为准；本节只补充 `proposition` 层的 seed / lineage / payload subtype 约束。

规则：

- 不允许使用裸字符串作为规范来源引用（canonical source ref）
- v1 proposition canonical ref 固定为 `PropositionRef = { session_id, proposition_id }`
- `seed_finding_refs[*].finding_ref` 必须是同一 session 内可解引用的 `FindingRef`
- `derived_from_proposition_ref` 仅允许指向同一 session 内更早创建的 proposition
- payload 中指向 artifact / finding 的 ref 不得改写成投影引用（projection ref）
- 默认不允许跨 session canonical ref；若未来引入跨 session 读取，必须在独立 schema 版本中显式放开
- 允许跨 artifact、跨 subject、跨谱系（lineage）引用，但 ref 必须显式、可审计，且不能绕过会话内局部（session-local）边界
- 整体引用图必须保持有向无环图（DAG）

v1 subtype ref 约束：

- `DecompositionProposition.payload.scope_delta_ref`：使用 `FindingRef`，指向被解释的 delta finding
- `AnomalyProposition.payload.candidate_ref`：使用 `ArtifactItemRefRef`，指向 detect artifact 中对应 candidate item
- `AnomalyProposition.payload.expected_behavior_ref`：使用 `FindingRef | ArtifactItemRefRef | null`
- `ForecastProposition.payload.forecast_basis_ref`：使用 `FindingRef | ArtifactRef | null`
- `agent_authored.origin.authored_input_ref`：仅允许 `PropositionAuthoredInputRef`

### created_at

记录 proposition 进入规范状态的时间。

### schema_version

用于区分 proposition 规范 Schema 的版本边界。

当 payload 语义发生 breaking change 时，必须显式变更该字段参与的版本边界。

## Proposition Families

### Change Proposition

用于表达“某个 metric 在给定 subject 上是否发生值得评估的变化”。

来源：

- `delta` finding
- agent-authored change hypothesis

```ts
type ChangeProposition = PropositionBase & {
  proposition_type: "change";
  payload: {
    change_kind: "scalar_change" | "segment_change";
    direction_of_interest:
      | "increase"
      | "decrease"
      | "any_non_flat"
      | "unexpected";
    comparison_window: {
      left: ResolvedTimeScope;
      right: ResolvedTimeScope;
    };
    comparison_basis: "current_vs_baseline" | "left_vs_right" | "peer_vs_peer";
    unit: string | null;
    dimension_keys: Record<string, string | number | boolean | null> | null;
  };
};
```

标识边界：

- metric / entity / slice
- change kind
- comparison window pair
- direction of interest
- dimension keys（若为 segment-level proposition）

不包括：

- 当前 delta 数值是否已被支持
- 当前 support/opposition 集合

窗口约束：

- `change` proposition 必须绑定 `left/right` 窗口
- 不存在 `not_applicable` 形式的 change proposition
- 若无法确定 comparison window，则不应创建 canonical `change` proposition

### Decomposition Proposition

用于表达“某个 dimension item 是否是 scope delta 的重要贡献项”。

来源：

- `decomposition_item` finding

```ts
type DecompositionProposition = PropositionBase & {
  proposition_type: "decomposition";
  payload: {
    dimension: string;
    dimension_keys: Record<string, string | number | boolean | null>;
    contribution_role:
      | "primary_driver"
      | "secondary_driver"
      | "offsetting_factor"
      | "material_component";
    scope_delta_ref: FindingRef;
    comparison_window: {
      left: ResolvedTimeScope;
      right: ResolvedTimeScope;
    };
  };
};
```

标识边界：

- target dimension
- dimension item keys
- contribution role
- referenced scope delta semantics

注意：

- 该 proposition 判断的是“该 item 是否构成重要贡献项”
- 不判断“整体变化是否存在”；那属于 `change` proposition
- `scope_delta_ref` 在 system-seeded decomposition proposition 中必须非空，并指向被解释的 scope-delta semantics
- 若未来支持 agent-authored decomposition hypothesis，应使用独立 authored contract，而不是把 `scope_delta_ref` 置空

### Anomaly Proposition

用于表达“某个 anomaly candidate 是否值得继续验证的异常判断对象”。

来源：

- `anomaly_candidate` finding

```ts
type AnomalyProposition = PropositionBase & {
  proposition_type: "anomaly";
  payload: {
    anomaly_kind: "point_anomaly" | "bucket_anomaly" | "candidate";
    candidate_ref: ArtifactItemRefRef | null;
    expected_behavior_ref: FindingRef | ArtifactItemRefRef | null;
    observed_window: ResolvedTimeScope;
    validation_goal:
      | "validate_candidate"
      | "rule_out_noise"
      | "confirm_operational_significance";
  };
};
```

标识边界：

- subject
- candidate ref 或稳定时间桶语义
- validation goal

该 proposition 不直接宣称“已经确认异常”。

窗口约束：

- `anomaly` proposition 必须绑定单个 `observed_window`
- 若候选没有稳定可解析的观测窗口，则不应创建 canonical `anomaly` proposition

### Correlation Proposition

用于表达“两个序列之间是否存在统计关联”。

来源：

- `correlation_result` finding
- agent-authored correlation hypothesis

```ts
type CorrelationProposition = PropositionBase & {
  proposition_type: "correlation";
  payload: {
    left_subject: PropositionSubject;
    right_subject: PropositionSubject;
    method_family: "pearson" | "spearman" | "auto";
    relationship_of_interest:
      | "any_association"
      | "positive_association"
      | "negative_association";
    join_basis: CorrelationJoinBasis;
    aligned_window: ResolvedTimeScope;
  };
};
```

标识边界：

- left/right subjects
- relationship of interest
- method family
- join basis
- aligned window

该 proposition 只讨论 statistical association，不携带 causal semantics。

时间语义：

- v1 `correlation` proposition 只表达在单个对齐分析窗口内的关联判断
- `aligned_window` 表示两侧序列完成对齐后参与相关性估计的共同窗口
- `join_basis` 必须是 machine-readable 的结构化对齐语义，不接受自由文本

### Test Hypothesis Proposition

用于表达“某个显式统计假设是否获得支持”。

来源：

- `test_result` finding
- agent-authored hypothesis
- `validate` 派生意图内部显式 hypothesis

```ts
type TestHypothesisProposition = PropositionBase & {
  proposition_type: "test_hypothesis";
  payload: {
    hypothesis_family: "difference";
    alternative: "two_sided" | "greater" | "less";
    left_subject: PropositionSubject;
    right_subject: PropositionSubject;
    method_family: "welch_t" | "two_proportion_z" | "auto";
    alpha: number | null;
    hypothesis_label: string | null;
  };
};
```

标识边界：

- hypothesis family
- left/right subjects
- alternative
- alpha
- method family

该 proposition 只表达 hypothesis 本身，不表达是否 rejected / supported。

### Forecast Proposition

用于表达“某个 subject 在未来窗口上的预测判断对象”。

来源：

- `forecast_point` finding
- agent-authored forecast expectation

```ts
type ForecastProposition = PropositionBase & {
  proposition_type: "forecast";
  payload: {
    forecast_kind: "point_forecast" | "interval_forecast";
    forecast_window: ResolvedTimeScope;
    horizon_index: number | null;
    expectation_direction:
      | "increase"
      | "decrease"
      | "stable"
      | "open";
    forecast_basis_ref: FindingRef | ArtifactRef | null;
  };
};
```

标识边界：

- subject
- forecast target window
- horizon index
- expectation direction

该 proposition 不直接表达 forecast quality、coverage 或是否应采取行动。

窗口约束：

- `forecast` proposition 必须绑定单个 `forecast_window`
- 若未来值未解析到具体窗口，不应创建规范 `forecast` proposition

## Seed Rules

`finding -> proposition` 的种子规则（seed rule）必须是确定性的模板选择（template selection），而不是自由文本总结。

v1 原则：

- 一个 finding 可种子（seed）`0..N` 个 propositions
- 一个 proposition 可由 `1..N` 个种子事实创建
- `seed_finding_refs` 只表示创建输入，不表示当前评估证据归属（assessment evidence membership）
- proposition identity 不由种子引用（seed ref）的数量或顺序决定

推荐种子家族（seed family）映射：

| finding_type | 默认 proposition family |
| --- | --- |
| `delta` | `change` |
| `decomposition_item` | `decomposition` |
| `anomaly_candidate` | `anomaly` |
| `correlation_result` | `correlation` |
| `test_result` | `test_hypothesis` |
| `forecast_point` | `forecast` |

`observation` finding 默认不直接种子 proposition，除非未来单独定义 observation-to-proposition 模板。

## Immutability 与 Evolution

v1 中 proposition 视为不可变规范判断对象。

要求：

- proposition 创建后不应原地修改其判断语义（judgment semantics）
- 若判断对象发生变化，应创建新的 proposition，而不是复用旧 ID
- 新 findings 到来时，优先更新 assessment，而不是修改 proposition
- 若 proposition 被判定需要更具体或更细粒度的版本，可创建新 proposition，并通过 `derived_from_proposition_ref` 建立谱系

## Nullability 与 Empty Semantics

以下字段采用固定语义：

- `subject.slice = {}`：表示无额外 slice 约束，不表示 unknown
- `seed_finding_refs = []`：表示创建时无直接种子事实
- `lineage.derived_from_proposition_ref = null`：表示不是由旧 proposition 派生
- `origin.authored_label = null`：表示该 agent-authored proposition 没有人类可读标签，不表示来源未知
- `origin.authored_input_ref = null`：表示该 agent-authored proposition 没有显式上游 canonical 输入锚点
- `ArtifactLineageRef.artifact_schema_version = null`：表示来源 artifact contract 未提供独立 schema version
- `ArtifactLineageRef.extractor_version = null`：表示该 proposition 不是经由独立 extractor 版本抽取，而非版本未知
- `ChangeProposition.payload.unit = null`：表示该 change judgment 在当前 contract 下没有可定义的 canonical unit
- `ChangeProposition.payload.dimension_keys = null`：表示该 proposition 不是 segment-level change，因此不存在 dimension item keys
- `AnomalyProposition.payload.candidate_ref = null`：表示该 anomaly proposition 以稳定窗口语义建模，而不是绑定单个 candidate item
- `AnomalyProposition.payload.expected_behavior_ref = null`：表示当前命题未绑定单独的 canonical expected-behavior anchor
- `TestHypothesisProposition.payload.alpha = null`：表示该 hypothesis 未显式绑定 alpha，由 assessment family 或执行 contract 决定阈值
- `TestHypothesisProposition.payload.hypothesis_label = null`：表示未提供人类可读假设标签，不影响 canonical semantics
- `ForecastProposition.payload.horizon_index = null`：表示 forecast_window 已足以唯一确定目标 horizon，无需额外 ordinal
- `ForecastProposition.payload.forecast_basis_ref = null`：表示该 forecast proposition 未绑定单个 canonical basis ref

禁止在同一字段中混用：

- `unknown`
- `not_applicable`
- `not_yet_resolved`

## Agent Consumption Contract

`proposition` 被声明为规范 agent-facing object，因此 Schema 文档必须包含最小读取规则。

### 可查询轴

推荐至少支持：

- `session_id`
- `proposition_type`
- `subject.metric`
- `subject.entity`
- `subject.slice`
- `subject.analysis_axis`
- `origin.kind`
- `created_at`

### 默认排序规则

推荐默认排序：

1. `proposition_type` lexical order
2. `subject.metric` lexical order，nulls last
3. `subject.slice` canonicalized lexical order
4. subtype-specific stable key
5. `proposition_id` ascending

若调用方请求 top-k，应在上述排序基础上做稳定截断，并返回 truncation metadata。

### 局部最小闭包读取

围绕 proposition 的最小闭包应至少包含：

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

- `seed_entries` 必须按 `seed_finding_refs` 的 canonical 顺序返回，并保留每个 `PropositionSeedRef.role`
- `seed_entries.finding = null` 表示该种子（seed）当前不可解引用；读取层不得静默丢弃该 seed entry
- `relevant_findings` 来自 `latest_assessment` 或其引用的推断记录（inference records），是 agent 读取实时证据的主入口
- `latest_assessment = null` 表示该 proposition 尚未进入评估流程，不表示 assessment 失败
- 若 `latest_assessment = null`，则 `blocking_gaps`、`applied_inference_records` 与 `assessment_dependencies` 必须同时为 `null`
- `blocking_gaps = []` 表示已评估且当前无阻塞缺口（blocking gap）
- 若 assessment 存在但暂无推断记录（inference record），`applied_inference_records` 应返回 `[]`
- `assessment_dependencies` 只覆盖 `applied_inference_records.input_assessment_ids` 的直接 assessment 输入
- 种子当前不可解引用不等价于 proposition 无效，但必须通过 `seed_entries` 向 agent 显式暴露
- `blocking_gaps`、`applied_inference_records` 与 `relevant_findings` 必须从规范评估（canonical assessment）/ 推断记录（inference record）解引用；投影（projection）只能压缩展示，不得重定义成员集合

### 稳定引用格式

推荐固定使用 `PropositionRef = { session_id, proposition_id }` 作为规范引用。

稳定截断 metadata 至少应包含：

- `is_truncated`
- `returned_count`
- `total_count`
- `sort_key`

projection 可以对 proposition 做排序、聚合与截断，但不得：

- 改写 `proposition_id`
- 改写 `proposition_type`
- 覆盖 payload 语义

异常场景读取规则：

- 若 `seed_finding_refs` 中部分 finding 不可解引用，focus view 仍返回 proposition，但对应 `seed_entries.finding` 必须为 `null`
- 若 `latest_assessment` 不存在，不得伪造空 assessment；返回 `null`
- 若 assessment 存在但没有 live findings，`relevant_findings` 返回 `[]`
- 若 assessment 存在但没有 inference records，`applied_inference_records` 返回 `[]`
- 若 proposition 所属谱系已整体失效，projection 层应显式将该 proposition 标为不可读取，而不是静默返回空闭包

## 与 Finding / Assessment / Action Proposal 的边界

### Proposition 和 Finding 的边界

`finding` 回答的是“系统确定知道什么”。

`proposition` 回答的是“系统要判断什么”。

因此 proposition 中不得直接写入：

- 原始观测样本或完整 artifact rows
- `delta_pct` 一类作为事实本体的字段
- “已观察到”“已证明”“数据显示”这类事实结论

### Proposition 和 Assessment 的边界

assessment 是 proposition 的动态评估状态。

因此 proposition 中不得出现：

- `status`
- `confidence`
- `inference_grade`
- `supporting_finding_ids`
- `opposing_finding_ids`
- `missing_requirements`
- `applied_rule_ids`

### Proposition 和 Action Proposal 的边界

action proposal 回答的是“下一步做什么可能最有价值”。

因此 proposition 中不得出现：

- `priority`
- `recommended_action`
- `expected_information_gain`
- `mitigation_owner`

## Test Cases

后续实现至少应满足以下 schema-level 验收场景：

1. 同一 session 中重读同一 judgment semantics，`proposition_id` 稳定。
2. 同一 session 中创建 proposition 时种子事实批次不同，但 judgment semantics 相同，不因 `seed_finding_refs[*].finding_ref` 顺序或数量变化而生成不同 ID。
3. 非 breaking 的 `schema_version` 变化不得单独触发新的 `proposition_id`。
4. 不同 session 中语义相同的 proposition 不复用 ID。
5. `agent_authored` proposition 可以 `seed_finding_refs = []`，但 `origin` 与 `lineage` 必须完整。
6. `change` proposition 不携带 confidence/status；相关字段只能出现在 assessment。
7. `decomposition` proposition 只判断 item 是否构成重要贡献项，不替代 `change` proposition。
8. `correlation` proposition 不能混入 causal semantics。
9. `test_hypothesis` proposition 可以表达 hypothesis direction 与 alpha，但不直接表达 hypothesis 是否 supported。
10. 新 findings 到来后，应更新 assessment，而不是原地修改 proposition payload。
11. projection 可以裁剪 proposition 列表，但不得改写 proposition identity 或 payload 语义。
12. 同一 subject 上的 `change` proposition 与 `decomposition` proposition 必须通过 `scope_delta_ref` / `lineage` 保持可追溯关联，但不得合并为同一个 proposition。
13. `correlation` proposition 与 `test_hypothesis` proposition 即使 subject 相同，也必须因 judgment semantics 不同而保持不同 identity。
14. `agent_authored` 与 `system_seeded` proposition 应进入同一 assessment family，但 `lineage` 与种子处理规则必须不同。
15. 任一 payload ref 若是裸字符串、跨 session canonical ref 或 projection ref，应判为非法 proposition 契约。

## 非目标

v1 不包含以下能力：

- 跨 session proposition registry
- composite proposition family
- recommendation / prioritization 混入 proposition 本体
- 在 proposition 层做实时证据聚合
- 为现有 `claim` / `recommendation` / persistence 设计兼容层
