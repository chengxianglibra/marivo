# test 原子意图 Schema

本文档定义 `test` 原子意图的拟议类型契约。

状态：draft design。本文是规划中的原子 `test` 意图 Schema 提案，不表示对应 HTTP endpoint 已经实现。

## 目的

`test` 用于在两个已定义、且推断就绪（inferential-ready）的观测之上评估结构化统计假设。

设计目标：

- 让 `test` 聚焦假设检验（hypothesis evaluation），不承担数据抽取或业务解释
- 消费显式观测工件（observation artifacts），而不是原始样本或 ad hoc scopes
- 显式表达方法选择（method selection）、假设状态（assumption status）与校验语义（validation semantics）
- 保持输出类型稳定，便于下游推理

## 核心设计决策

`test` 消费两个由 `observe` 产出的推断摘要工件（inferential summary artifact），不接受：

- 原始样本数组（raw sample arrays）
- 直接的 `metric + scope` 输入
- 把 `compare`、`decompose` 或 `detect` 输出当作样本

这样可以保持数据流清晰：

- `observe` 定义测量了什么总体摘要
- `test` 定义在这些摘要之上要评估什么统计假设

推断就绪性（inferential-readiness）必须由上游 `observe` 建立，而不能在 `test` 内部靠隐式自动转换。

因此 v1 只保留类型化引用契约（typed-reference contract），不再维护一套直接检验契约（direct test 契约）。

## 工件标识（Artifact Identity）与谱系（Lineage）

`hypothesis_test` 是不可变规范工件（immutable canonical artifact）。

标识边界（identity boundary）绑定以下输入：

- `left_ref` 指向的 observe artifact lineage
- `right_ref` 指向的 observe artifact lineage
- `hypothesis.family`
- `hypothesis.alternative`
- `hypothesis.alpha`
- `method`
- `schema_version`
- `derivation_version`

以下内容不得进入 artifact identity：

- `hypothesis.label`
- projection 截断或展示参数
- execution timestamp
- engine 选择
- query hash

本契约必须显式区分：

- 重读同一 artifact：同一 identity，同一 lineage
- 重新执行同一请求：可产生新的 execution record；若左右 source lineage、schema version 与 derivation version 未变，则不产生新的 canonical artifact identity
- 左右任一 source lineage、schema version 或 derivation version 变化：必须产生新的 `hypothesis_test` artifact

v1 默认：

- artifact 不允许跨 lineage 复用 identity
- artifact 为 immutable，不支持 session 内覆盖更新
- `test` 只能引用同 session 内已完成的 `observe` artifact

## Reference Contract

`test` 优先消费 typed artifact reference，而不是裸字符串 step id。

```ts
type ObservationArtifactRef = {
  step_type: "observe";
  session_id: string;
  step_id: string;
  artifact_id: string;
  observation_type:
    | "numeric_sample_summary"
    | "rate_sample_summary";
};
```

引用约束：

- `left_ref` 与 `right_ref` 都必须指向已完成步骤产出的 canonical `observe` artifact
- `observation_type` 在 v1 中只能是 `numeric_sample_summary` 或 `rate_sample_summary`
- 不允许 projection ref 充当 canonical source ref
- v1 不允许跨 session ref
- v1 允许引用同 session 内的历史已完成 artifact
- 引用图必须保持 DAG；`test` 不允许直接或间接回指依赖自己的对象

## Request Shape

```json
{
  "step_type": "test",
  "left_ref": {
    "step_type": "observe",
    "session_id": "sess_123",
    "step_id": "step_obs_variant_a_rate",
    "artifact_id": "obs_artifact_variant_a_rate",
    "observation_type": "rate_sample_summary"
  },
  "right_ref": {
    "step_type": "observe",
    "session_id": "sess_123",
    "step_id": "step_obs_variant_b_rate",
    "artifact_id": "obs_artifact_variant_b_rate",
    "observation_type": "rate_sample_summary"
  },
  "hypothesis": {
    "family": "difference",
    "alternative": "two_sided",
    "alpha": 0.05,
    "label": "conversion differs between variant A and variant B"
  },
  "method": "auto"
}
```

## Typed Schema

```ts
type TestRequest = {
  step_type: "test";
  left_ref: ObservationArtifactRef;
  right_ref: ObservationArtifactRef;
  hypothesis: HypothesisContract;
  method?: "auto" | "welch_t" | "two_proportion_z";
};

type HypothesisContract = {
  family: "difference";
  alternative?: "two_sided" | "greater" | "less";
  alpha?: number;
  label?: string | null;
};
```

## 输入规则

v1 支持的输入形态如下：

- `left_ref` 必须解析到已完成的 `observe`，且 `observation_type` 为 `numeric_sample_summary` 或 `rate_sample_summary`
- `right_ref` 也必须如此，且 observation type 必须与左侧一致
- 两边应属于同一 semantic metric，或属于被显式标记为 cross-group comparable 的 metric family
- 两边必须是完整 artifact，而不是 projection-only 结果
- `hypothesis.family` 必须为 `"difference"`
- `hypothesis.alpha` 必须在 `(0, 1)` 内
- 选择的方法必须与 observation type 兼容
- 若任一输入 observation 冻结了 `resolved_policy_summary`，另一侧也必须冻结兼容的同一份 calendar alignment metadata；`test` 只能复用该 frozen summary，不得重建第二套 holiday / weekday / event pairing 逻辑

输出类型：`hypothesis_test`

推荐默认值：

- `hypothesis.alternative = "two_sided"`
- `hypothesis.alpha = 0.05`
- `method = "auto"`

## v1 不支持的输入

- 直接传 `metric + scope`
- raw sample arrays
- 把 `compare` / `decompose` / `detect` 输出当作统计样本
- `scalar` / `time_series` / `segmented`
- projection ref 作为输入
- 直接传裸 `artifact_id`
- 左右 observation types 不一致
- equivalence、non-inferiority 等其他 hypothesis families
- paired tests
- repeated-measures tests
- multi-arm tests
- covariate-adjusted tests

推荐错误码：`INVALID_ARGUMENT`。

## 非法组合

- `hypothesis.alpha <= 0` 或 `hypothesis.alpha >= 1`
- `method = "welch_t"`，但任一输入不是 `numeric_sample_summary`
- `method = "two_proportion_z"`，但任一输入不是 `rate_sample_summary`
- `left_ref.session_id != right_ref.session_id`
- `left_ref` 或 `right_ref` 解析到 projection-only、incomplete 或未完成 artifact
- 两边 metric family 未显式声明可比较

推荐错误码：`INVALID_ARGUMENT` 或 `NOT_COMPARABLE`。

## 归一化规则

- `hypothesis.alternative = null` 或缺失时，归一化为 `"two_sided"`
- `hypothesis.alpha = null` 或缺失时，归一化为 `0.05`
- `method = null` 或缺失时，归一化为 `"auto"`

## 字段语义

### left_ref / right_ref

指向先前 `observe` 产出的 inferential-ready 输入观测。

约定：

- `left_ref` 是主要被考察总体
- `right_ref` 是比较总体

左右区分对 one-sided hypotheses 与 estimate sign 都有影响。

### hypothesis

v1 仅支持：

- `family = "difference"`

即在请求的 `alternative` 下，评估左右总体是否存在差异。

`alternative` 语义：

- `two_sided`：左右不同
- `greater`：左侧大于右侧
- `less`：左侧小于右侧

`label` 仅是人类可读标签，不参与 identity 计算。

`label = null` 的唯一语义是 `not_applicable_or_omitted`：调用方未提供额外标签，不能同时表示 unknown 或 unresolved。

### method

v1 每次请求只支持一种方法：

- `welch_t`：数值样本均值差
- `two_proportion_z`：二项 rate 差异
- `auto`：由 observation type 决定

确定性的 `auto` 规则：

- `numeric_sample_summary -> welch_t`
- `rate_sample_summary -> two_proportion_z`

`numeric_sample_summary` 的 v1 契约只包含支撑 `welch_t` 所需的摘要统计；`test` 不对当前 artifact 额外隐式补充 rank-based 输入。

## 兼容性契约

只有当两条输入观测在语义和统计上都可比较时，`test` 才合法。

系统至少要检查：

- 相同 observation type
- 相同 metric，或显式允许 cross-group compare 的 metric family
- 若存在 calendar alignment freeze，则两侧 `policy_ref`、`comparison_basis`、`resolved_calendar_source`、`resolved_calendar_version` 必须兼容
- 样本量足够
- 所需 summary statistics 存在
- 两边都是完整 artifact
- 没有阻断性 completeness 问题
- metric capability 仍允许该 inferential method 与 comparison contract

系统应返回：

- `valid`
- `needs_attention`
- `invalid`

`validation.status` 的推导规则必须确定性：

- 任一 `issues[].severity = "error"` 时为 `invalid`
- 无 `error` 且至少一个 `warning` 时为 `needs_attention`
- `issues = []` 时为 `valid`

Marivo 推荐默认行为是拒绝 `invalid`。

## 检验语义

结果必须始终包含：

- 选定 method
- target estimand
- 在可定义时的 estimate value
- 在可定义时的 test statistic
- 在可定义时的 `p_value`
- 相对于 `alpha` 的 decision
- assumption status
- validation metadata

定义：

- `estimate.value` 表示 left minus right
- 若 `p_value` 可定义，则 `decision.reject_null` 由 `p_value <= alpha` 导出
- 若存在 `confidence_interval`，它应与 `alpha` 对齐

重要边界：

- `test` 评估的是统计假设，不是业务结论
- `reject_null = true` 不代表因果性或业务重要性
- `reject_null = false` 不等于证明两者相等

## Response Shape

```ts
type TestResponse = HypothesisTestArtifact;

type StepRef = {
  session_id: string;
  step_id: string;
  step_type: "test";
};

type TestIssue = {
  code:
    | "step_not_found"
    | "unsupported_observation_type"
    | "observation_type_mismatch"
    | "metric_mismatch"
    | "projection_not_allowed"
    | "cross_session_not_allowed"
    | "insufficient_sample_size"
    | "summary_stat_missing"
    | "data_incomplete"
    | "method_incompatible"
    | "alpha_invalid"
    | "assumption_warning"
    | "calendar_alignment_metadata_mismatch"
    | "calendar_policy_mismatch"
    | "calendar_comparison_basis_mismatch"
    | "calendar_source_mismatch"
    | "calendar_version_mismatch"
    | "holiday_cluster_unmapped"
    | "event_cluster_unmapped"
    | "fallback_applied"
    | "alignment_coverage_insufficient"
    | "weekday_pairing_tie";
  severity: "error" | "warning";
  gate_family?: "comparability_gate";
  blocking?: boolean;
  message: string;
};

type TestValidation = {
  status: "valid" | "needs_attention" | "invalid";
  issues: TestIssue[];
};

calendar alignment 分层补充：

- 单边 sample summary 缺失、样本量不足、artifact 不完整属于 `quality_gate` 或更早阶段失败，不应在成功的 `test` artifact 中重报为 calendar comparability issue。
- 双边 frozen alignment metadata 不兼容、coverage 不足、weekday pairing tie 未解决属于 `comparability_gate`。
- `source_lineage.calendar_alignment.comparability_warnings` 保留 observation 上游冻结的原始 warning；`validation.issues` 表达 test 阶段的最终 blocking / non-blocking 判定。
- `weekday_pairing_tie` 在 v1 直接导致 `test: NOT_COMPARABLE`；`fallback_applied`、`holiday_cluster_unmapped`、`event_cluster_unmapped`、`alignment_coverage_insufficient` 默认保留为 `needs_attention` warning。

calendar alignment failure surface 与 `compare` 保持同一套用户文案：

| code | blocking | 用户可读 message | 下一步 |
| --- | --- | --- | --- |
| `calendar_alignment_metadata_mismatch` | 是 | 一侧 observation 冻结了 `resolved_policy_summary`，另一侧缺失兼容的 calendar alignment metadata。 | 用同一条 calendar-aligned `observe` 链路重跑缺失的一侧。 |
| `calendar_policy_mismatch` | 是 | 左右 observation 冻结了不同 `calendar_policy_ref`。 | 用同一 policy 重跑两侧 observation。 |
| `calendar_comparison_basis_mismatch` | 是 | 左右 observation 冻结了不同 comparison basis。 | 保证两侧来自同一 comparison basis。 |
| `calendar_source_mismatch` | 是 | 左右 observation 绑定了不同 calendar source。 | 用同一 resolved calendar source 重跑。 |
| `calendar_version_mismatch` | 是 | 左右 observation 冻结了不同 calendar version。 | 用同一冻结 version 重跑。 |
| `weekday_pairing_tie` | 是 | weekday 对齐出现未解决的候选 tie，当前 pairing 不稳定。 | 调整 tie-breaker / max-shift 或缩小窗口后重跑。 |
| `holiday_cluster_unmapped` | 否 | 节假日 cluster 无法完整映射到 baseline。 | 补齐 holiday annotation，或改用 `natural_*` / `weekday_*` policy。 |
| `event_cluster_unmapped` | 否 | 活动 cluster 无法完整映射到 baseline。 | 补齐 event annotation，或改用非 `event_*` policy。 |
| `fallback_applied` | 否 | 对齐过程已退化到 fallback matcher。 | 复核 fallback 是否可接受；不可接受时补齐 annotation 或改用更合适的 policy。 |
| `alignment_coverage_insufficient` | 否 | bucket pairing coverage 不完整，部分 bucket 未配对。 | 查看 coverage summary，补齐映射或缩小 comparison window。 |

对于 blocking issue，`test` 的 `NOT_COMPARABLE` 错误正文必须直接复用对应稳定 message。对于 warning issue，`validation.issues[*].message` 与 `compare.comparability.issues[*].message` 必须完全一致，不得在 `test` 链路再造一套不同措辞。

type TestAssumptions = {
  independence: "assumed";
  distribution_check: "unchecked" | "acceptable" | "violated" | null;
  variance_model: "welch" | null;
};

type TestSourceLineage = {
  left_source: {
    step_ref: ObservationArtifactRef;
    source_schema_version: string;
  };
  right_source: {
    step_ref: ObservationArtifactRef;
    source_schema_version: string;
  };
};

type HypothesisTestArtifact = {
  step_ref: StepRef;
  artifact_id: string;
  schema_version: string;
  derivation_version: string;
  result_type: "hypothesis_test";
  left_ref: ObservationArtifactRef;
  right_ref: ObservationArtifactRef;
  source_lineage: TestSourceLineage;
  sample_kind: "numeric" | "rate";
  hypothesis: {
    family: "difference";
    alternative: "two_sided" | "greater" | "less";
    alpha: number;
    label: string | null;
  };
  method: "welch_t" | "two_proportion_z";
  estimate: {
    estimand: "mean_diff" | "rate_diff";
    value: number | null;
    confidence_interval: {
      level: number;
      lower: number | null;
      upper: number | null;
    } | null;
  };
  statistic: {
    name: "t" | "z";
    value: number | null;
  };
  p_value: number | null;
  decision: {
    reject_null: boolean | null;
  };
  assumptions: TestAssumptions;
  validation: TestValidation;
  execution_metadata: {
    query_hash: string;
    engine: string;
    executed_at: string;
  };
};
```

## 响应说明

- `step_ref` 与 `artifact_id` 是下游引用完整 artifact 的稳定身份字段；其中 `artifact_id` 是权威 lineage 入口，`step_ref` 提供 typed step lineage
- `source_lineage` 用于 machine-readable provenance，记录左右输入 artifact 及其 source contract version
- `execution_metadata` 只表达执行记录，不替代 provenance 主体语义
- `issues = []` 的唯一语义是当前无已知 validation issue，不得混用为 “未执行校验”

各 nullable 字段必须保持单义：

- `hypothesis.label = null`：调用方未提供标签
- `assumptions.distribution_check = null`：当前 method 下 `distribution_check` 不适用
- `assumptions.variance_model = null`：当前 method 下不存在可报告的 variance model
- `estimate.value = null`：当前检验因 validation 阻断而无法定义估计值
- `estimate.confidence_interval = null`：当前实现或当前 validation 状态下无法返回区间
- `estimate.confidence_interval.lower = null` / `upper = null`：区间对象已存在，但边界在当前状态下不可定义
- `statistic.value = null`：统计量在当前状态下不可定义
- `p_value = null`：p 值在当前状态下不可定义
- `decision.reject_null = null`：结果因 validation 阻断而不可得出决策

`decision.reject_null = null` 只能在 `validation.status = "invalid"` 时出现；`valid` 与 `needs_attention` 结果必须返回非空 decision。

## 错误语义

- `INVALID_ARGUMENT`
  请求形状、alpha、method、typed ref 形状或 hypothesis 非法
- `STEP_NOT_FOUND`
  某个 ref 无法解析
- `NOT_COMPARABLE`
  两个观测单独有效，但在当前检验契约下不可辩护地联用
- `INSUFFICIENT_DATA`
  所需摘要统计或样本条件不足

## 下游兼容性说明

- `validate` 可以展开为 `observe -> observe -> test`
- `test` 只能消费 inferential-ready 的 `observe` 工件
- canonical evidence pipeline 可以引用 `test` 结果作为结构化证据，但不能把它当作因果证明

## Artifact 与 Projection

本文档定义的是 `test` 的完整 artifact semantics。

`test` 是单结果 artifact。下游步骤若依赖它，应引用整个 artifact，而不是某个 projection。

projection 可以：

- 概括是否 reject null
- 摘要展示 estimate 与 confidence interval
- 提炼最关键的 validation warnings

projection 不需要定义 top-k、排序或 row selection；这些对单结果 artifact 为 `not_applicable`。

但不得：

- 改写 method
- 改写 hypothesis direction
- 在保留确定性解读的同时隐藏阻断性 validation issues
- 替代 artifact 自身成为下游 typed reference 的目标
- 创造业务结论或建议

## 负向契约

以下状态在 canonical schema 层面应视为非法：

- 用 projection ref 替代 canonical source ref
- 让 `hypothesis.label`、projection 参数或 execution metadata 影响 artifact identity
- 在 `validation.status != "invalid"` 时返回 `decision.reject_null = null`
- 允许 cross-session ref
- 在 source lineage 或 version boundary 已变化时复用旧 artifact identity
