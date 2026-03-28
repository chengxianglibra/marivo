# Diagnose Derived Intent Schema

本文档定义 `diagnose` 派生分析意图的拟议类型契约。

状态：draft design。本文是规划中的 `diagnose` 派生意图 Schema 提案，不表示对应 HTTP endpoint 已经实现。

## 目的

`diagnose` 用于把异常候选发现、异常量化和变化归因串成一次完整的诊断动作。

它回答三个固定问题：

- 有没有异常候选
- 异常有多大
- 主要由哪些维度驱动

设计目标：

- 让业务方直接请求“诊断”而不是手工拼装 `detect -> compare -> decompose`
- 保持内部展开完全确定，不在执行时依赖外部决策
- 复用原子意图既有语义，不重定义 anomaly、delta 或 contribution
- 让最终输出在 bounded output（有界输出）下仍保留主要诊断语义

## 核心设计决策

`diagnose` 是 derived intent，不是开放式 explain workflow。

v1 明确约束：

- 归因维度必须由调用方显式提供，不自动挑选
- 只跟进 `detect` 稳定排序后的前 `K` 个候选
- baseline window 使用固定推导策略，不允许自定义
- 每个候选 follow-up 只生成 `scalar` compare，以满足 `decompose` 的输入契约
- 不输出因果结论、建议或自由文本诊断作为证据主体

## Request Shape

```json
{
  "intent": "diagnose",
  "metric": "dau",
  "time_scope": {
    "mode": "single_window",
    "grain": "day",
    "current": {
      "start": "2024-03-01T00:00:00",
      "end": "2024-04-01T00:00:00"
    }
  },
  "scope": null,
  "detect_split_by": null,
  "candidate_dimensions": ["channel", "region"],
  "profile": "auto",
  "sensitivity": "balanced",
  "candidate_limit": 10,
  "followup_limit": 3,
  "decomposition_limit": 5
}
```

## Typed Schema

```ts
type DiagnoseRequest = {
  intent: "diagnose";
  metric: string;
  time_scope: DetectTimeScope;
  scope?: Scope | null;
  detect_split_by?: string | null;
  candidate_dimensions: string[];
  profile?: DetectProfile | null;
  sensitivity?: DetectSensitivity | null;
  candidate_limit?: number | null;
  followup_limit?: number | null;
  decomposition_limit?: number | null;
};

type DetectTimeScope = {
  mode: "single_window";
  grain: TimeGranularity;
  current: TimeWindow;
};

type TimeGranularity = "hour" | "day" | "week" | "month";

type TimeWindow = {
  start: string;
  end: string;
};

type Scope = {
  constraints?: Record<string, string | number | boolean | null> | null;
  predicate?: Predicate | null;
};

type Predicate =
  | { op: "and"; items: Predicate[] }
  | { op: "or"; items: Predicate[] }
  | {
      field: string;
      op:
        | "eq"
        | "neq"
        | "in"
        | "not_in"
        | "gt"
        | "gte"
        | "lt"
        | "lte"
        | "between"
        | "is_null"
        | "is_not_null";
      value?: string | number | boolean | string[] | number[];
    };

type DetectProfile =
  | "auto"
  | "spike_dip"
  | "level_shift"
  | "seasonal_residual";

type DetectSensitivity = "conservative" | "balanced" | "aggressive";
```

## 输入规则

v1 支持的输入形态如下：

- `metric` 必须解析到已发布的 semantic metric
- `time_scope` 必须复用 `detect` 的统一时间窗口契约，且 `mode = "single_window"`
- `scope` 若提供，必须遵守统一 non-time scope 契约
- `detect_split_by` 可选；若提供，只能是单个 semantic dimension
- `candidate_dimensions` 必须是非空的单维度名称列表，且去重后仍非空
- `profile`、`sensitivity` 继承 `detect` 的合法取值
- `candidate_limit` 控制内部 `detect` 返回的候选数
- `followup_limit` 控制实际进入 compare/decompose follow-up 的候选数
- `decomposition_limit` 控制每个维度分解返回的 contribution rows 数量

artifact 类型：`diagnosis_bundle`

projection 类型：`diagnose_projection`

## v1 不支持的输入

- 自动选择归因维度
- 自定义 baseline window 或 baseline policy
- 跟进全部候选且不设上界
- 多个 detect dimensions
- 在候选层直接做 segmented compare 作为 `decompose` 输入
- 通过自由文本指定“主要异常”或“最值得看”的筛选逻辑
- 任何因果、建议、动作优先级类输出契约

推荐错误码：`INVALID_ARGUMENT`。

## 字段语义

### metric

要被诊断的单个 semantic metric。

`diagnose` 的作用是围绕一个指标展开完整诊断，而不是做多指标筛查。

### time_scope

供内部 `detect` 扫描的统一时间窗口。

`diagnose` 先在该范围内寻找异常候选，再围绕被选中的 candidate window 做后续量化与归因。

### scope

供内部 `detect` 和后续 follow-up `observe` 共享的统一 non-time scope。

### detect_split_by

控制 `detect` 是否在整体时间序列之上扫描，还是先按一个 semantic dimension 拆成独立子序列再扫描。

- `null`：扫描整体序列
- 非空：扫描该维度下的独立序列

若 detect candidate 自带 `slice`，该 slice 必须与请求 `scope` 组合，并原样继承到 follow-up 的 current/baseline scope 中。

### candidate_dimensions

诊断时要逐个执行归因的维度列表。

这是刻意的显式契约：

- 维度选择属于产品输入，而不是运行时探索决策
- 给定同一请求，系统始终对同一组维度展开
- 每个维度都形成独立的 `decompose` 结果，避免混合多维交互语义

### candidate_limit

传递给内部 `detect.limit`，用于限制 detect artifact 返回的候选数量。

### followup_limit

控制 `diagnose` 实际跟进多少个异常候选。

规则：

- 候选必须沿用 `detect` 的稳定排序
- 只跟进前 `followup_limit` 个候选
- 未跟进候选必须通过顶层 metadata 披露

### decomposition_limit

传递给每个内部 `decompose.limit`，用于限制每个归因维度返回的 top drivers 数量。

## 展开契约

给定同一请求与同一系统状态，`diagnose` 必须展开成同一条逻辑 DAG。

固定展开如下：

1. `detect(metric, time_scope, scope, detect_split_by, profile, sensitivity, candidate_limit)`
2. 读取 detect artifact 中按既定排序返回的 candidates
3. 对前 `followup_limit` 个 candidate 逐个展开：
   - 令 `current_window = candidate.window`
   - 用固定策略推导 `baseline_window`
   - 将请求 `scope` 与 `candidate.slice` 组合成 current/baseline 的共享 non-time scope
   - `observe(current_window, combined_scope)`
   - `observe(baseline_window, combined_scope)`
   - `compare(left_ref=current_ref, right_ref=baseline_ref, mode="scalar")`
   - 对 `candidate_dimensions` 中每个 dimension：
     - `decompose(compare_ref, dimension, method="delta_share", limit=decomposition_limit)`

### Baseline Derivation

v1 baseline policy 固定为 `previous_adjacent_equal_length`：

- baseline window 与 candidate window 粒度相同
- baseline window 与 candidate window 长度相同
- baseline window 紧邻 candidate window 之前

示例：

- candidate 为 `[2024-03-20, 2024-03-21)`，则 baseline 为 `[2024-03-19, 2024-03-20)`
- candidate 为 `[2024-03-18, 2024-03-25)`，则 baseline 为 `[2024-03-11, 2024-03-18)`

该推导属于派生意图的固定规则，不是调用方可调参数。

## 校验规则

`diagnose` 校验分为三层。

### 1. 请求校验

以下情况应直接失败：

- `metric` 不存在
- `time_scope.mode != "single_window"`
- `candidate_dimensions` 为空
- `candidate_dimensions` 含空字符串或重复后为空
- `candidate_limit != null && candidate_limit <= 0`
- `followup_limit != null && followup_limit <= 0`
- `decomposition_limit != null && decomposition_limit <= 0`
- `detect_split_by` 或任一 `candidate_dimensions` 不是合法 semantic dimension

### 2. 展开校验

以下情况应直接失败，而不是退化为 planner 行为：

- 候选窗口无法应用固定 baseline 推导策略
- `followup_limit` 超出系统允许的 bounded output 上限
- 请求的 detect / decomposition fan-out 不能维持有界执行

### 3. 原子兼容性校验

`diagnose` 不得绕过原子意图的校验规则。

至少要保证：

- 内部 `detect` 请求是 detectable
- follow-up `observe` 能在 current/baseline scope 下产生可比的 scalar observations
- 内部 `compare` status 不为 `not_comparable`
- 每个内部 `decompose` 在所请求维度下是 attributable

若某个候选或某个维度只达到 `needs_attention`，可带 issue 成功返回，但不得静默替换候选、维度或 baseline。

## Artifact / Projection Boundary

`diagnose` 必须先定义完整的 derived artifact，再定义 bounded projection。

- `diagnosis_bundle` 是 canonical derived artifact
- `diagnose_projection` 是 `diagnosis_bundle` 的确定性压缩视图
- projection 只能压缩 artifact，不得替代 artifact 的 source refs 或 identity

因此：

- 下游步骤、审计和复现应引用 `diagnosis_bundle`
- agent / UI 的短读场景可消费 `diagnose_projection`
- `followup_limit` 与 `decomposition_limit` 的截断语义属于 projection policy，不进入 artifact identity

## Artifact Shape

```ts
type DiagnoseArtifact = {
  result_type: "diagnosis_bundle";
  artifact_schema_version: string;
  metric: string;
  time_scope: ResolvedDetectTimeScope;
  scope: Scope | null;
  detect_split_by: string | null;
  candidate_dimensions: string[];
  profile: DetectProfile;
  sensitivity: DetectSensitivity;
  validation: DiagnoseValidation;
  provenance: DiagnoseArtifactProvenance;
  detect_summary: DiagnoseDetectSummary;
  diagnoses: DiagnoseCandidateResult[];
};

type DiagnoseProjection = {
  result_type: "diagnose_projection";
  artifact_ref: DiagnoseArtifactRef;
  projection_version: string;
  metric: string;
  time_scope: ResolvedDetectTimeScope;
  scope: Scope | null;
  detect_split_by: string | null;
  candidate_dimensions: string[];
  profile: DetectProfile;
  sensitivity: DetectSensitivity;
  validation: DiagnoseValidation;
  detect_summary: DiagnoseDetectSummary;
  diagnoses: DiagnoseCandidateProjection[];
  projection_metadata: DiagnoseProjectionMetadata;
};

type ResolvedDetectTimeScope = {
  mode: "single_window";
  grain: TimeGranularity;
  current: TimeWindow;
};

type DiagnoseIssue = {
  code:
    | "detect_needs_attention"
    | "baseline_derivation_failed"
    | "observe_failed"
    | "compare_needs_attention"
    | "compare_not_comparable"
    | "decompose_needs_attention"
    | "decompose_not_attributable"
    | "candidate_followup_truncated";
  severity: "error" | "warning";
  message: string;
  candidate_ref?: DetectCandidateRef | null;
  dimension?: string | null;
};

type DiagnoseValidation = {
  status: "diagnosable" | "needs_attention";
  issues: DiagnoseIssue[];
};

type DiagnoseArtifactRef = {
  session_id: string;
  step_id: string;
  step_type: "diagnose";
  artifact_id: string;
};

type DiagnoseArtifactProvenance = {
  artifact_ref: DiagnoseArtifactRef;
  source_detect_ref: DetectArtifactRef;
  artifact_schema_version: string;
  derivation_version: string;
  projection_ref: string | null;
};

type DiagnoseDetectSummary = {
  detect_ref: DetectArtifactRef;
  returned_candidate_count: number;
  total_candidate_count: number;
  followed_candidate_count: number;
  truncated: boolean;
};

type DiagnoseProjectionMetadata = {
  followup_limit: number;
  decomposition_limit: number;
};

type DiagnoseCandidateResult = {
  candidate_ref: DetectCandidateRef;
  candidate: DetectCandidateSummary;
  baseline_derivation: BaselineDerivationMetadata;
  current_ref: ObservationArtifactRef | null;
  baseline_ref: ObservationArtifactRef | null;
  compare_ref: CompareArtifactRef | null;
  comparison: ScalarDeltaSummary | null;
  drivers: DiagnoseDriverSet[];
  status: "diagnosed" | "needs_attention";
  issues: DiagnoseIssue[];
};

type DiagnoseCandidateProjection = {
  candidate_ref: DetectCandidateRef;
  candidate: DetectCandidateSummary;
  baseline_derivation: BaselineDerivationMetadata;
  compare_ref: CompareArtifactRef | null;
  comparison: ScalarDeltaSummary | null;
  drivers: DiagnoseDriverProjection[];
  status: "diagnosed" | "needs_attention";
  issues: DiagnoseIssue[];
};

type DetectArtifactRef = {
  session_id: string;
  step_id: string;
  step_type: "detect";
  artifact_id: string;
};

type ObservationArtifactRef = {
  session_id: string;
  step_id: string;
  step_type: "observe";
  artifact_id: string;
};

type CompareArtifactRef = {
  session_id: string;
  step_id: string;
  step_type: "compare";
  artifact_id: string;
};

type DecomposeArtifactRef = {
  session_id: string;
  step_id: string;
  step_type: "decompose";
  artifact_id: string;
};

type ArtifactItemRef = {
  collection: "candidates";
  index: number;
  key: string | null;
};

type DetectCandidateRef = {
  artifact_ref: DetectArtifactRef;
  item_ref: ArtifactItemRef;
};

type DetectCandidateSummary = {
  window: {
    start: string;
    end: string;
  };
  slice: Record<string, string> | null;
  observed_value: number | null;
  expected_value: number | null;
  deviation_abs: number | null;
  deviation_pct: number | null;
  candidate_score: number;
  flag_level: "low" | "medium" | "high";
  direction: "up" | "down" | "flat" | "undefined";
};

type BaselineDerivationMetadata = {
  policy: "previous_adjacent_equal_length";
  current_window: {
    start: string;
    end: string;
  };
  baseline_window: {
    start: string;
    end: string;
  } | null;
};

type ScalarDeltaSummary = {
  comparison_type: "scalar_delta";
  left_value: number | null;
  right_value: number | null;
  absolute_delta: number | null;
  relative_delta: number | null;
  direction: "increase" | "decrease" | "flat" | "undefined";
  comparability_status: "comparable" | "needs_attention" | "not_comparable";
};

type DiagnoseDriverSet = {
  dimension: string;
  decompose_ref: DecomposeArtifactRef | null;
  attribution_status: "attributable" | "needs_attention" | "not_attributable";
  rows: Array<{
    key: string | number | boolean | null;
    left_value: number | null;
    right_value: number | null;
    absolute_contribution: number | null;
    contribution_share: number | null;
    direction: "increase" | "decrease" | "flat" | "undefined";
    presence: "both" | "left_only" | "right_only";
  }>;
  returned_row_count: number;
  total_row_count: number | null;
  is_truncated: boolean;
  others_absolute_contribution: number | null;
  others_contribution_share: number | null;
  unexplained_absolute_delta: number | null;
  unexplained_share: number | null;
  unexplained_reason:
    | "method_limit"
    | "data_incomplete"
    | "scope_recomputation_failed"
    | "rounding"
    | null;
  issues: DiagnoseIssue[];
};

type DiagnoseDriverProjection = {
  dimension: string;
  decompose_ref: DecomposeArtifactRef | null;
  attribution_status: "attributable" | "needs_attention" | "not_attributable";
  rows: Array<{
    key: string | number | boolean | null;
    left_value: number | null;
    right_value: number | null;
    absolute_contribution: number | null;
    contribution_share: number | null;
    direction: "increase" | "decrease" | "flat" | "undefined";
    presence: "both" | "left_only" | "right_only";
  }>;
  returned_row_count: number;
  total_row_count: number | null;
  is_truncated: boolean;
  others_absolute_contribution: number | null;
  others_contribution_share: number | null;
  unexplained_absolute_delta: number | null;
  unexplained_share: number | null;
  unexplained_reason:
    | "method_limit"
    | "data_incomplete"
    | "scope_recomputation_failed"
    | "rounding"
    | null;
  issues: DiagnoseIssue[];
};
```

## Artifact 响应语义

`diagnose` 的最终语义分三层承接：

- “有没有异常”来自 `detect` candidate
- “异常有多大”来自 follow-up `compare`
- “主要由哪些维度驱动”来自每个 `decompose` 的 contribution rows

因此：

- `detect` candidate 仍然是 candidate，不是 confirmed anomaly fact
- `comparison.absolute_delta` / `relative_delta` 继承 `compare` 语义
- `drivers.rows[*].contribution_share` 继承 `decompose` 的 signed share 语义
- `DiagnoseArtifact` 是可复现、可审计、可被下游引用的完整派生工件

## Nullability 与 Empty Semantics

本契约中的 nullable / empty 字段必须遵守单义语义：

- `scope = null`：no-extra non-time scope
- `detect_split_by = null`：整体序列扫描，不做 split
- `candidate.slice = null`：该 candidate 对应整体序列，而不是“未知 slice”
- `baseline_derivation.baseline_window = null`：固定 baseline policy 对该 candidate 无法定义合法 baseline；不得同时表示“尚未读取”
- `current_ref = null`：current-side observe artifact 未产生；唯一允许原因是上游校验或执行失败已在 `issues` 中披露
- `baseline_ref = null`：baseline-side observe artifact 未产生；唯一允许原因是 baseline 推导失败或 baseline observe 未产生
- `compare_ref = null`：compare artifact 未产生；唯一允许原因是任一 observe 未产生或 compare 未通过原子兼容性校验
- `comparison = null`：scalar delta summary 不可定义；唯一允许原因是 `compare_ref = null` 或 compare artifact 未能产生可读取 summary
- `drivers = []`：没有任何已请求维度的 driver result；唯一允许原因是该 candidate 未进入可执行 decompose 阶段
- `total_row_count = null`：该 decompose artifact 未能稳定披露 total row count，而不是“0 rows”
- `unexplained_reason = null`：不存在 unexplained remainder，或 remainder 已为 0；不得同时表示“未知为什么没解释完”
- `validation.issues = []`：no-known diagnose-level issues
- `DiagnoseCandidateResult.issues = []`：no-known candidate-level issues
- `DiagnoseDriverSet.issues = []`：no-known driver-level issues
- `diagnoses = []`：没有任何 candidate 被成功纳入 artifact；不得同时表示“系统还没跑”

复合视图的 nullable 语义由主状态字段支配：

- `DiagnoseValidation.status` 支配 diagnose-level readiness
- `DiagnoseCandidateResult.status` 支配 candidate-level follow-up completion
- `DiagnoseDriverSet.attribution_status` 支配每个维度的 attribution readiness

## Status Derivation

以下推导规则是 contract 的一部分，不得由实现层临时解释。

### DiagnoseValidation.status

- 仅当 detect 阶段可执行，且不存在 diagnose-level `error` issue 时，状态为 `diagnosable`
- 只要存在 `detect_needs_attention` 且 `severity = "error"`，状态降为 `needs_attention`
- `candidate_followup_truncated` 只影响 completeness，不单独把顶层状态降为 `needs_attention`

### DiagnoseCandidateResult.status

- 仅当 `baseline_derivation.baseline_window` 可定义、`current_ref` / `baseline_ref` / `compare_ref` 全部存在、且 `comparison.comparability_status = "comparable"` 时，状态为 `diagnosed`
- 出现 `baseline_derivation_failed`、`observe_failed` 或 `compare_not_comparable` 时，状态必须为 `needs_attention`
- `compare_needs_attention` 也会把 candidate 状态降为 `needs_attention`
- 任一维度 `decompose_not_attributable` 不会抹掉其他维度结果，但 candidate 状态仍降为 `needs_attention`

### DiagnoseDriverSet.attribution_status

- 仅当 `decompose_ref` 存在，且 decompose artifact 可读、可归因时，状态为 `attributable`
- 出现 `decompose_not_attributable` 时，状态必须为 `not_attributable`
- 出现 `decompose_needs_attention` 时，状态必须为 `needs_attention`
- truncation 只影响 completeness，不单独把 `attribution_status` 从 `attributable` 降级

## Provenance 与 Reference Rules

`diagnose` artifact 必须显式区分：

- diagnose artifact schema version：`artifact_schema_version`
- derived intent derivation version：`provenance.derivation_version`
- source detect artifact ref：`provenance.source_detect_ref`
- diagnose projection version：`DiagnoseProjection.projection_version`

内部与上游 refs 必须遵守以下规则：

- 所有 `ObservationArtifactRef`、`CompareArtifactRef`、`DecomposeArtifactRef` 必须指向同一 session 内由本次 `diagnose` 展开创建的内部步骤
- `DetectCandidateRef` 可以指向上游 detect artifact，但必须与 `provenance.source_detect_ref` 同 lineage
- `DetectCandidateRef.item_ref.index` 绑定 detect artifact 原始 candidates 集合中的稳定位置，不绑定 projection 排序位置
- `projection_ref` 只能指向 bounded consumer view，不得替代 source artifact ref
- 引用图必须保持 DAG：`diagnosis_bundle -> detect candidate -> observe -> compare -> decompose`

## Negative Contract

以下状态或引用组合是非法的：

- `DiagnoseCandidateResult.status = "diagnosed"` 但 `compare_ref = null`
- `DiagnoseCandidateResult.status = "diagnosed"` 但 `comparison.comparability_status != "comparable"`
- `DiagnoseDriverSet.attribution_status = "attributable"` 但 `decompose_ref = null`
- `baseline_derivation.baseline_window = null` 且 `baseline_ref != null`
- `compare_ref != null` 但 `current_ref = null` 或 `baseline_ref = null`
- 任一内部 ref 指向不同 session 的 artifact
- `DetectCandidateRef` 指向不属于 `detect_summary.detect_ref` 的 candidate
- 用 projection-level ref 或排序位置替代 canonical source ref

## Projection Shape

`diagnose_projection` 是对 `DiagnoseArtifact` 的确定性压缩视图，而不是新的证据层。

- `diagnoses` 只包含按 detect 稳定排序后前 `followup_limit` 个被跟进候选
- 每个 `DiagnoseCandidateProjection.drivers` 只包含按 decompose 稳定排序后前 `decomposition_limit` 个 driver rows
- projection 必须保留解释截断所需的 metadata，不得让调用方从内部 artifact 自行重建 truncation 语义

## Projection Policy

`diagnose` 的 projection 必须保持确定性压缩。

允许：

- 只返回前 `followup_limit` 个被跟进候选
- 每个维度只返回前 `decomposition_limit` 个 drivers
- 压缩 detect 与 decompose 的元数据为 projection-level 摘要
- 附带 `artifact_ref` 以回指完整 `DiagnoseArtifact`

不得：

- 把 anomaly candidate 改写成 anomaly fact
- 根据中间结果临时换一组维度继续归因
- 隐藏 follow-up truncation、driver truncation 或 detect exclusions
- 发明工件中不存在的因果解释
- 用 projection 替代 artifact 作为下游 typed reference 的来源

## 例子

### 例 1：诊断整体 DAU 下跌

请求：

- `metric = "dau"`
- `detect_split_by = null`
- `candidate_dimensions = ["channel", "region"]`

含义：

- 先在整体 DAU 日序列中扫描异常候选
- 跟进前 `K` 个候选日期
- 对每个异常日，与前一个等长窗口比较
- 再分别按 `channel` 与 `region` 解释 delta

### 例 2：诊断分渠道 GMV 异常周

请求：

- `metric = "gmv"`
- `time_scope.grain = "week"`
- `detect_split_by = "channel"`
- `candidate_dimensions = ["category", "province"]`

含义：

- 先按渠道拆出独立周序列做异常检测
- 某个候选若来自 `channel = "paid_search"`，则 follow-up 的 current/baseline 两侧都固定带上该 slice
- 再按 `category` 与 `province` 分别对该渠道的周度 delta 做归因

## v1 Scope Limits

- 只支持单指标诊断
- 只支持单个 detect split dimension
- 只支持显式归因维度列表
- 只支持固定 baseline policy：`previous_adjacent_equal_length`
- 只支持以 `scalar_delta` 为核心的 follow-up compare
- 只支持 additive metric 的 `delta_share` 归因
- 不支持开放式多轮下钻、维度自动推荐或因果解释
