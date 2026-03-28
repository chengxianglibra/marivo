# Correlate Step Schema

本文档定义 `correlate` 分析步骤的拟议类型契约。

状态：draft design。本文是规划中的原子 `correlate` 意图 Schema 提案，不表示对应 HTTP endpoint 已经实现。

## 目的

`correlate` 用于估计两个先前定义的时间序列观测之间的统计关联。

设计目标：

- 让 `correlate` 只关注 pairwise association（两两关联），不承担数据抽取或因果解释
- 消费显式上游 observations，而不是 raw metric-plus-scope 输入
- 显式表达 alignment（对齐）与 data loss（数据损耗）语义
- 让输出对下游推理保持类型稳定

## 核心设计决策

`correlate` 消费两个 `observe(time_series)` 输出，而不是原始指标名、scope、列名或临时 join 规则。

这样可以保持数据流清晰：

- `observe` 定义每条时间序列
- `correlate` 定义这两条时间序列之间的关联

因此 v1 只保留 typed-reference contract，不再维护一套 direct correlate 契约。

## Artifact Identity 与 Lineage

`pairwise_time_series_association` 是 immutable canonical artifact。

identity boundary 绑定以下输入：

- `left_ref` 指向的 observe artifact lineage
- `right_ref` 指向的 observe artifact lineage
- `method`
- `artifact_schema_version`
- `derivation_version`

以下内容不得进入 artifact identity：

- projection 截断或展示参数
- explanation 文本
- execution timestamp
- engine 选择
- query hash

本契约必须显式区分：

- 重读同一 artifact：同一 identity，同一 lineage
- 重新执行同一请求：可产生新的 execution record；若左右 source lineage、artifact schema version 与 derivation version 未变，则不产生新的 canonical artifact identity
- 左右任一 source lineage、artifact schema version 或 derivation version 变化：必须产生新的 `pairwise_time_series_association` artifact

v1 默认：

- artifact 不允许跨 lineage 复用 identity
- artifact 为 immutable，不支持 session 内覆盖更新
- `correlate` 只能引用同 session 内已完成的 `observe` artifact

## Reference Contract

`correlate` 优先消费 typed artifact reference，而不是裸字符串 step id。

```ts
type ObservationArtifactRef = {
  step_type: "observe";
  session_id: string;
  step_id: string;
  artifact_id: string;
  observation_type: "time_series";
};
```

引用约束：

- `left_ref` 与 `right_ref` 都必须指向已完成步骤产出的 canonical `observe` artifact
- `observation_type` 在 v1 中必须是 `time_series`
- 不允许 projection ref 充当 canonical source ref
- v1 不允许跨 session ref
- v1 允许引用同 session 内的历史已完成 artifact
- 引用图必须保持 DAG；`correlate` 不允许直接或间接回指依赖自己的对象

## Request Shape

```json
{
  "step_type": "correlate",
  "left_ref": {
    "step_type": "observe",
    "session_id": "sess_123",
    "step_id": "step_obs_gmv_daily",
    "artifact_id": "obs_artifact_gmv_daily",
    "observation_type": "time_series"
  },
  "right_ref": {
    "step_type": "observe",
    "session_id": "sess_123",
    "step_id": "step_obs_ad_spend_daily",
    "artifact_id": "obs_artifact_ad_spend_daily",
    "observation_type": "time_series"
  },
  "method": "spearman",
  "min_pairs": 5
}
```

## Typed Schema

```ts
type CorrelateRequest = {
  step_type: "correlate";
  left_ref: ObservationArtifactRef;
  right_ref: ObservationArtifactRef;
  method?: "pearson" | "spearman";
  min_pairs?: number;
};
```

## 输入规则

v1 支持的输入形态如下：

- `left_ref` 必须解析到已完成的 `observe`，且 `observation_type = "time_series"`
- `right_ref` 也必须如此
- 两边都必须是完整 artifact，而不是 projection-only 结果
- 两边观测值都必须是数值
- 两边必须使用相同的 `granularity`
- 两边 time bucket 必须能在继承自 `observe` 的共享 bucket 语义下对齐
- 对齐后的 matched pair 数必须不少于 `min_pairs`

输出类型：`pairwise_time_series_association`

推荐默认值：

- `method = "spearman"`
- `min_pairs = 5`

`min_pairs` 为可选字段；省略表示使用系统默认值 `5`。

## v1 不支持的输入

- `scalar` 与任何类型
- `segmented` 与任何类型
- `matrix` 与任何类型
- 直接传 `metric + scope`
- 直接传 artifact ID、raw columns、`join_on`
- projection ref 作为输入
- 在一次请求中要求多个 methods
- 自动 lag search
- `control_for`
- 候选 metric 扫描
- 按维度批量关联

推荐错误码：`INVALID_ARGUMENT`。

## 字段语义

### left_ref / right_ref

指向先前 `observe` 步骤的时间序列 artifact。

约定：

- `left_ref` 是主要被考察的序列
- `right_ref` 是比较序列

与 `compare` 不同，`correlate` 不给左右两侧赋予 baseline 含义；但稳定的左右标签仍有助于 provenance 和下游引用。

### method

v1 每次请求只支持一个关联方法：

- `pearson`：数值线性相关
- `spearman`：秩相关，对单调但非线性关系和离群点更稳健

v1 故意不支持 `both`。如果调用方需要两个方法，应分别调用两次 `correlate`。

### min_pairs

bucket matching 之后所需的最小对齐数据点数。

`min_pairs` 是 validation threshold（校验阈值），不是统计调参项。如果对齐后的样本点少于该阈值，应该直接失败。

## 对齐契约

只有当两条输入序列能在清晰且确定的 bucket 契约下对齐时，关联估计才是合法的。

v1 只使用一条对齐规则：

- `pairing_rule = "intersection_by_time_bucket"`

这意味着：

- 只保留两边都存在的 bucket
- 不对缺失 bucket 做插值
- 不做 interpolation、forward-fill、back-fill 或 resampling
- bucket 边界继承 `observe` 的半开区间语义

系统至少要检查：

- 相同 `granularity`
- 兼容的 bucket 定义
- 足够的 matched pairs
- 两边数据完整性是否可接受

系统应返回：

- `aligned`
- `needs_attention`
- `not_aligned`

Factum 的推荐默认行为是拒绝 `not_aligned`。

## 关联语义

结果必须始终包含：

- 关联系数
- 在数学上可定义时的 `p_value`
- matched pair 数
- sign
- significance 状态
- 显式 alignment metadata

定义：

- `coefficient` 是所选方法的 correlation coefficient，范围 `[-1, 1]`
- `sign` 仅由 `coefficient` 派生
- `significance` 仅由 `p_value` 与固定 `significance_level` 派生

v1 固定：

- `significance_level = 0.05`
- `significance_level` 当前不作为 request 字段开放配置

规则：

- 若系数在当前对齐结果上无法数学定义，则 `coefficient = null`
- 若 `p_value` 在当前对齐结果上无法数学定义，则 `p_value = null`
- `coefficient > 0` 时 `sign = "positive"`
- `coefficient < 0` 时 `sign = "negative"`
- `coefficient = 0` 时 `sign = "zero"`
- `coefficient = null` 时 `sign = "undefined"`
- `p_value <= significance_level` 时 `significance = "significant"`
- `p_value > significance_level` 时 `significance = "not_significant"`
- `p_value = null` 时 `significance = "undefined"`

重要边界：

- association 不等于 causation
- significant 结果不代表存在机制解释或方向性
- `correlate` 不能替代 `test`，也不能替代未来可能存在的 causal step

## Response Shape

```ts
type CorrelateResponse = AssociationResult;

type CorrelationIssue = {
  code:
    | "granularity_mismatch"
    | "bucket_mismatch"
    | "insufficient_pairs"
    | "sparse_overlap"
    | "data_incomplete"
    | "constant_series"
    | "numeric_domain_invalid";
  severity: "error" | "warning";
  message: string;
};

type AlignmentMetadata = {
  status: "aligned" | "needs_attention" | "not_aligned";
  issues: CorrelationIssue[];
};

type CorrelationStatistic = {
  method: "pearson" | "spearman";
  coefficient: number | null;
  p_value: number | null;
  n_pairs: number;
};

type CorrelateAnalyticalMetadata = {
  pairing_rule: "intersection_by_time_bucket";
  left_granularity: TimeGranularity;
  right_granularity: TimeGranularity;
  matched_time_scope: ResolvedTimeScope | null;
  significance_level: 0.05;
  left_point_count: number;
  right_point_count: number;
  matched_pair_count: number;
  dropped_left_points: number;
  dropped_right_points: number;
};

type CanonicalVersionMetadata = {
  artifact_schema_version: string;
  source_contract_version: string;
  derivation_version: string;
};

type SourceLineageMetadata = {
  left_artifact: ObservationArtifactRef;
  right_artifact: ObservationArtifactRef;
};

type ExecutionMetadata = {
  query_hash: string;
  engine: string;
  executed_at: string;
};

type AssociationResult = {
  association_type: "pairwise_time_series_association";
  artifact_id: string;
  left_ref: ObservationArtifactRef;
  right_ref: ObservationArtifactRef;
  left_metric: string;
  right_metric: string;
  left_time_scope: ResolvedTimeScope;
  right_time_scope: ResolvedTimeScope;
  left_filters: Predicate | null;
  right_filters: Predicate | null;
  unit_pair: {
    left: string | null;
    right: string | null;
  };
  alignment: AlignmentMetadata;
  statistic: CorrelationStatistic;
  sign: "positive" | "negative" | "zero" | "undefined";
  significance: "significant" | "not_significant" | "undefined";
  analytical_metadata: CorrelateAnalyticalMetadata;
  version_metadata: CanonicalVersionMetadata;
  source_lineage: SourceLineageMetadata;
  execution_metadata: ExecutionMetadata;
};
```

## 主状态字段与推导规则

`alignment.status` 是 agent-facing 主状态字段。

取值：

- `aligned`
- `needs_attention`
- `not_aligned`

推导规则：

- 任一 blocking issue 必须令 `status = "not_aligned"`
- v1 blocking issues 为：`granularity_mismatch`、`bucket_mismatch`、`insufficient_pairs`
- 无 blocking issue，但存在任一 `severity = "warning"` 的 issue 时，`status = "needs_attention"`
- `issues = []` 时，`status = "aligned"`
- `constant_series` 与 `numeric_domain_invalid` 在 v1 中属于 `warning`；它们允许 artifact 产出，但会使 `coefficient` 或 `p_value` 为 `null`

empty semantics：

- `issues = []` 表示 `no_known_alignment_issues`

非法状态：

- `status = "aligned"` 且 `issues` 非空
- `status = "not_aligned"` 但不存在 blocking issue
- `status = "needs_attention"` 且 `issues = []`

## Nullability 与 Empty Semantics

nullable 字段的唯一语义如下：

- `statistic.coefficient = null`
  `not_applicable`；表示当前 method 在对齐后的有效 pair 上无法数学定义系数
- `statistic.p_value = null`
  `not_applicable`；表示在当前 method 与有效 pair 条件下显著性检验统计量不可定义
- `analytical_metadata.matched_time_scope = null`
  `not_yet_resolved`；仅允许出现在请求在形成 matched intersection 之前被拒绝
- `left_filters = null`
  `not_applicable`；表示左侧 source artifact 不含额外的非时间谓词
- `right_filters = null`
  `not_applicable`；表示右侧 source artifact 不含额外的非时间谓词
- `unit_pair.left = null`
  `unknown`；表示左侧 metric 无 canonical unit 或当前系统未知
- `unit_pair.right = null`
  `unknown`；表示右侧 metric 无 canonical unit 或当前系统未知

空集合语义：

- `alignment.issues = []` 表示 `no_known_alignment_issues`

## 校验规则

- `left_ref` 与 `right_ref` 都必须解析到已完成的 `observe`
- 两边都必须是 `observation_type = "time_series"`
- 两边都必须是完整 artifact，而不是 projection-only 结果
- 两边必须使用相同的 `granularity`
- 两边都必须包含数值型时间序列值
- 对齐后的 bucket 数必须不少于 `min_pairs`
- 若 `alignment.status = "not_aligned"`，默认应拒绝请求
- 若对齐已成立，但因常数序列或 numeric domain 限制导致统计量不可定义，系统应产出 artifact，并返回 `alignment.status = "needs_attention"`

## 错误语义

- `INVALID_ARGUMENT`
  请求形状非法、参数不支持、projection ref 输入或上游类型不兼容
- `STEP_NOT_FOUND`
  `left_ref` 或 `right_ref` 无法解析
- `ALIGNMENT_FAILED`
  两条序列无法在当前契约下对齐
- `INSUFFICIENT_DATA`
  对齐后的有效点数不足
- `UNSUPPORTED_METHOD`
  方法不受支持

## 下游兼容性说明

- `synthesize` 可以把 `correlate` 作为 L0 association evidence，但不能直接转写成因果语言
- `detect + correlate` 可用于异常共振分析，但二者仍应保持独立契约
- planner 可以 fan out 多个 `correlate` 去探索多个 metric pairs，但 candidate generation 不属于原子步骤契约

## Artifact 与 Projection

本文档定义的是 `correlate` 的完整 artifact semantics。

`correlate` 是单结果 artifact。下游步骤若依赖它，应引用整个 artifact，而不是某个 row-level projection。

agent 默认最小闭包读取字段为：

- `association_type`
- `left_ref`
- `right_ref`
- `alignment.status`
- `alignment.issues`
- `statistic.method`
- `statistic.coefficient`
- `statistic.p_value`
- `sign`
- `significance`
- `analytical_metadata.matched_pair_count`

projection 可以：

- 用紧凑格式重述 coefficient、sign 与 significance
- 摘要说明 alignment 质量与 dropped-point 数
- 在上下文紧张时省略非最小闭包 metadata

projection 不需要定义 top-k、排序或 row selection；这些对单结果 artifact 为 `not_applicable`。

但不得：

- 改写 correlation method
- 重定义 alignment 状态
- 改写 coefficient、p-value、sign 或 significance
- 把 association 翻译成因果语言
- 创造新的 claims
- 用 projection ref 替代 canonical source ref
