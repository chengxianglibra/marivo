# attribute 派生意图 Schema

本文档定义 `attribute` 派生意图的拟议类型契约。

状态：draft design。本文是规划中的 `attribute` 派生意图 Schema 提案，不表示对应 HTTP endpoint 已经实现。

## 目的

`attribute` 用于把”已知变化的量化”和”变化由什么驱动”固化成一次稳定分析动作。

它回答两个固定问题：

- 这次变化有多大
- 这次变化主要由哪些维度驱动

典型场景：

- 环比 / 同比变化归因
- A/B 实验 uplift 的结构归因
- 预算变化、流量变化、供给变化导致的结果拆解

设计目标：

- 让调用方直接请求”解释这次变化”，而不是手工拼装 `observe -> observe -> compare -> decompose`
- 把变化解释从 ad hoc SQL 中抽离成稳定类型契约（typed contract）
- 复用 `observe`、`compare` 与 `decompose` 既有语义，不重定义标量观测（scalar observation）、差值（delta）或贡献（contribution）
- 让结果在有界输出（bounded output）下仍保留主要归因语义

## 核心设计决策

`attribute` 是派生意图（derived intent），不是开放式解释工作流（explain workflow）。

v1 明确约束：

- 只围绕单个 metric 的单个差值（delta）展开
- 左右两侧 scope 必须由调用方显式给定，不自动推导基线（baseline）
- 归因维度必须由调用方显式提供，不自动挑选
- 内部 compare 固定为标量（`scalar`）
- 每个归因维度都独立展开一个 `decompose`
- 不输出因果结论、建议或自由文本解释作为证据主体

## 请求形状（Request Shape）

```json
{
  "intent": "attribute",
  "metric": "gmv",
  "left": {
    "time_scope": {
      "kind": "range",
      "start": "2024-03-01T00:00:00",
      "end": "2024-04-01T00:00:00"
    },
    "scope": {
      "predicate": {
        "field": "experiment_group",
        "op": "eq",
        "value": "treatment"
      }
    }
  },
  "right": {
    "time_scope": {
      "kind": "range",
      "start": "2024-03-01T00:00:00",
      "end": "2024-04-01T00:00:00"
    },
    "scope": {
      "predicate": {
        "field": "experiment_group",
        "op": "eq",
        "value": "control"
      }
    }
  },
  "dimensions": ["channel", "region"],
  "decomposition_method": "delta_share",
  "decomposition_limit": 5
}
```

## Typed Schema

```ts
type AttributeRequest = {
  intent: "attribute";
  metric: string;
  left: AttributeObservationInput;
  right: AttributeObservationInput;
  dimensions: string[];
  decomposition_method?: "delta_share";
  decomposition_limit?: number;
};

type AttributeObservationInput = ObserveScalarInput;

type ObserveScalarInput = {
  // Reuses the canonical observe request contract.
  // This profile only permits the subset that deterministically
  // normalizes to scalar observe(metric, time_scope, scope).
  time_scope: CanonicalTimeScope;
  scope?: CanonicalScope | null;
};

type ObservationArtifactRef = {
  step_type: "observe";
  session_id: string;
  step_id: string;
  artifact_id: string;
  observation_type: "scalar";
};

type CompareArtifactRef = {
  step_type: "compare";
  session_id: string;
  step_id: string;
  artifact_id: string;
  comparison_type: "scalar_delta";
};

type DecomposeArtifactRef = {
  step_type: "decompose";
  session_id: string;
  step_id: string;
  artifact_id: string;
  decomposition_type: "delta_decomposition";
};

type AttributeBundleLineage = {
  source_compare_ref: CompareArtifactRef;
  source_observation_refs: [ObservationArtifactRef, ObservationArtifactRef];
  source_decompose_refs: DecomposeArtifactRef[];
};

type AttributeBundleVersion = {
  intent_contract_version: "attribute.v1";
  projection_version: "attribute_bundle.v1";
  derived_logic_version: string;
};

type AttributeResponse = {
  result_type: "attribute_bundle";
  artifact_id: string;
  lineage: AttributeBundleLineage;
  version: AttributeBundleVersion;
  metric: string;
  left: AttributeResolvedSide;
  right: AttributeResolvedSide;
  dimensions: string[];
  validation: AttributeValidation;
  observation_refs: {
    left_ref: ObservationArtifactRef;
    right_ref: ObservationArtifactRef;
  };
  compare_ref: CompareArtifactRef;
  comparison: ScalarDeltaSummary;
  drivers: AttributeDriverSet[];
  projection_metadata: AttributeProjectionMetadata;
};

type AttributeResolvedSide = {
  time_scope: ResolvedCanonicalTimeScope;
  scope: CanonicalScope | null;
};

type AttributeIssue = {
  code:
    | "observe_failed"
    | "compare_needs_attention"
    | "compare_not_comparable"
    | "decompose_needs_attention"
    | "decompose_not_attributable"
    | "driver_truncated";
  severity: "error" | "warning";
  message: string;
  dimension?: string | null;
};

type AttributeValidation = {
  status: "attributable" | "needs_attention";
  issues: AttributeIssue[];
};

type ScalarDeltaSummary = {
  comparison_type: "scalar_delta";
  left_value: number | null;
  right_value: number | null;
  absolute_delta: number | null;
  relative_delta: number | null;
  direction: "increase" | "decrease" | "flat" | "undefined";
  comparability_status: "comparable" | "needs_attention";
};

type AttributeDriverSet = {
  dimension: string;
  decompose_ref: DecomposeArtifactRef;
  attribution_status: "attributable" | "needs_attention";
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
  total_row_count: number;
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
  issues: AttributeIssue[];
};

type AttributeProjectionMetadata = {
  decomposition_limit: number;
  driver_row_order: "inherits_decompose_order";
  dimension_order: "request_order";
};
```

## Artifact Identity And Lineage

`attribute` 的外部读取对象是 `attribute_bundle`。

`attribute_bundle` 是 immutable derived artifact，不是 session-local 可演化状态。给定同一组上游原子 artifact refs 与同一 derived logic version，重读返回同一个 bundle；若任一上游 artifact lineage 改变，或 derived logic version 变化，必须产生新 bundle。

其 identity boundary 由以下输入共同决定：

- `metric`
- resolved `left` / `right` scalar observation request
- `dimensions` 的去重后有序列表
- `decomposition_method`
- resolved `decomposition_limit`
- compare artifact lineage
- per-dimension decompose artifact lineage
- `version.intent_contract_version`
- `version.derived_logic_version`

以下字段不得进入 `attribute_bundle` identity：

- `projection_version`
- `validation.issues`
- driver 当前排序位置
- explanation 文本

lineage 绑定规则：

- `attribute_bundle` 绑定其内部 `compare` artifact lineage
- 同时显式保留左右 `observation` 与各维度 `decompose` artifact refs
- 不允许跨 lineage 复用同一个 `attribute_bundle` identity

## Reference And Provenance Rules

`attribute` 响应中的 ref 必须是 machine-readable typed ref，不得使用自由文本 locator，也不得退化为仅有 `artifact_id` 的弱引用。

v1 ref 约束如下：

- 所有 ref 只能指向 canonical atomic artifacts，不得指向 projection
- 所有 ref 仅允许在同一 session 内引用
- 不允许跨 session rewiring
- 不允许跨 lineage 将旧 `decompose` 或 `compare` artifact 重新挂接到新 bundle
- 引用图必须保持 DAG，不允许 `attribute_bundle -> attribute_bundle` 形成循环
- `attribute_bundle` 可与其内部原子 artifacts 在同一派生执行中原子创建

v1 provenance 至少必须显式覆盖：

- 上游 compare artifact ref
- 上游 observation artifact refs
- 上游 decompose artifact refs
- `intent_contract_version`
- `derived_logic_version`
- `projection_version`

## 输入规则

v1 支持的输入形态如下：

- `metric` 必须解析到已发布的 semantic metric
- `left` 与 `right` 复用 canonical `observe` 请求契约，且都必须能确定性展开为 `observe(..., dimensions = null, granularity = null)` 的 scalar observation
- `left` / `right` 不得引入 `observe` scalar profile 之外的新字段
- `left` / `right` 不重复接收 `calendar_policy_ref`；若需要 calendar alignment 语义，应复用上游 observation 已冻结的 resolved policy summary
- `dimensions` 必须是非空的单维度名称列表，且去重后仍非空
- `decomposition_method` 省略时默认为 `delta_share`
- `decomposition_limit` 省略时使用系统默认上限；归一化后必须是正整数，且不得超过系统定义的最大有界输出阈值

输出类型：`attribute_bundle`

## v1 不支持的输入

- 自动选择归因维度
- 自动推导 left / right 其中一侧
- 多指标联合归因
- 内部使用 `segmented` compare 作为主语义
- 多维交互归因
- 开放式“继续下钻直到找到原因”的执行模式
- 因果、建议、动作优先级类输出契约

推荐错误码：`INVALID_ARGUMENT`。

## 字段语义

### metric

要被解释变化的单个 semantic metric。

`attribute` 围绕一个已经被业务方认为“值得解释”的变化展开，不负责发现目标变化。

### left / right

`left` 与 `right` 定义这次归因所比较的两侧 observation scope。

它们沿用 `compare` 的语义约定：

- `left` 表示被考察的一侧，例如当前周期、treatment、调整后方案
- `right` 表示基线一侧，例如上周期、control、调整前方案
- 最终 `absolute_delta = left_value - right_value`

`attribute` 不要求变化一定是时间上的；只要两侧 scope 可比较，就可以是：

- 两个时间窗口
- 同一时间窗口下的两个实验组
- 同一时间窗口下的两个预算 / 流量 / 供给状态

其中 `time_scope` 是唯一时间窗口契约；非时间总体约束统一通过 `scope` 表达。`left.scope = null` 或 `right.scope = null` 的唯一语义是“该侧没有额外非时间约束”。

### dimensions

用于归因的维度列表。

这是刻意的显式契约：

- 维度选择属于产品输入，而不是运行时探索决策
- 给定同一请求，系统始终按同一组维度展开
- 每个维度形成独立的 `decompose` 结果，避免混合多维交互语义

### decomposition_method

v1 只支持 `delta_share`，语义完全继承 `decompose(method = "delta_share")`。

`attribute` 不会发明新的归因算法；它只是把 compare 与 decompose 稳定串接起来。

### decomposition_limit

`decomposition_limit` 是每个归因维度返回 driver rows 的上限控制项。

- 省略表示“使用系统默认上限”
- 归一化后必须是正整数
- `attribute_bundle.projection_metadata.decomposition_limit` 回显的是归一化后的最终值，而不是原始输入

## 展开契约

给定同一请求与同一系统状态，`attribute` 必须展开成同一条逻辑 DAG。

固定展开如下：

1. `observe(metric, left.time_scope, left.scope, dimensions = null, granularity = null)`
2. `observe(metric, right.time_scope, right.scope, dimensions = null, granularity = null)`
3. `compare(left_ref, right_ref, mode = "scalar")`
4. 对 `dimensions` 中每个 dimension：
   - `decompose(compare_ref, dimension, method = decomposition_method, limit = resolved_decomposition_limit)`

其中：

- `observe` 的请求契约、时间窗口契约和非时间 `scope` 契约完全继承 canonical atomic `observe`
- `observe` 的 `result_mode` 固定为 `standard`
- `compare.mode` 固定为 `scalar`
- 所有 `decompose` 都共享同一个 `compare_ref`
- `dimensions` 的展开顺序必须继承请求顺序
- 每个 `decompose` 的 row 排序完全继承其 canonical atomic `decompose` artifact，不得由 `attribute` 重排

## 校验规则

`attribute` 校验分为三层。

### 1. 请求校验

以下情况应直接失败：

- `metric` 不存在
- `dimensions` 为空
- `dimensions` 含空字符串或去重后为空
- `decomposition_limit` 显式给定且 `<= 0`
- `decomposition_limit` 显式给定且超过系统允许上限
- `left` 或 `right` 缺少合法 `time_scope`
- `left.scope` 或 `right.scope` 含非法字段或非法时间条件
- `left` 或 `right` 包含不属于 canonical `observe` scalar profile 的字段
- 任一 `dimensions` 不是合法 semantic dimension

### 2. 展开校验

以下情况应直接失败，而不是退化为 planner 行为：

- `left` 或 `right` 无法被确定性归一化为 scalar observe 请求
- 任一 `dimensions` 不支持 attribution
- 请求的归因 fan-out 不能维持有界执行

### 3. 原子兼容性校验

`attribute` 不得绕过原子意图的校验规则。

至少要保证：

- 两个内部 `observe` 都能成功产生 scalar observation
- 内部 `compare` status 不是 `not_comparable`
- 每个内部 `decompose` status 不是 `not_attributable`

若某个维度只达到 `needs_attention`，可带 issue 成功返回，但不得静默替换 metric、scope 或维度。

## Response Shape

见上文 Typed Schema。

## 响应语义

`attribute` 的最终语义分两层承接：

- “变化有多大”来自内部 `compare`
- “变化主要由哪些维度驱动”来自每个 `decompose` 的 top contribution rows

因此：

- `comparison.absolute_delta` / `relative_delta` 继承 `compare` 语义
- `drivers.rows[*].contribution_share` 继承 `decompose` 的 signed share 语义
- `attribute` 本身不把 driver 自动提升为 causal conclusion（因果结论）

### 状态推导规则

`attribute` 中所有聚合判断字段都必须由固定规则推导，不允许实现层自由解释。

`validation.status` 的推导规则：

- 若 `comparison.comparability_status = "comparable"`，且所有 `drivers[*].attribution_status = "attributable"`，则为 `attributable`
- 其他成功返回场景一律为 `needs_attention`

`comparison.comparability_status` 的推导规则：

- 直接继承内部 atomic `compare` 的 comparability 状态
- v1 成功响应中只允许 `comparable` 或 `needs_attention`
- 若内部 `compare` 为 `not_comparable`，整个 `attribute` 请求必须失败，不得返回成功 bundle

`drivers[*].attribution_status` 的推导规则：

- 直接继承对应 atomic `decompose` 的 attribution 状态
- v1 成功响应中只允许 `attributable` 或 `needs_attention`
- 若内部 `decompose` 为 `not_attributable`，整个 `attribute` 请求必须失败，不得返回伪成功的 driver set

### Null And Empty Semantics

所有 nullable 字段都必须满足单义语义。

`comparison.left_value` / `right_value` / `absolute_delta` / `relative_delta` 为 `null` 的唯一语义：

- 内部 atomic `compare` 在成功返回中将该数值标记为 `unknown`

`drivers.rows[*].key = null` 的唯一语义：

- 对应 contribution row 的维度 key 为 SQL null

`drivers.rows[*].left_value` / `right_value` / `absolute_contribution` / `contribution_share` 为 `null` 的唯一语义：

- 对应 atomic `decompose` row 将该数值标记为 `unknown`

`others_absolute_contribution` / `others_contribution_share` 为 `null` 的唯一语义：

- `is_truncated = false`，因此 “others” 聚合项 `not_applicable`

`unexplained_absolute_delta` / `unexplained_share` 为 `null` 的唯一语义：

- 当前 decompose artifact 未产生 unexplained remainder，因此该字段 `not_applicable`

`unexplained_reason = null` 的唯一语义：

- 当前不存在 unexplained remainder，因此原因 `not_applicable`

`issues = []` 的唯一语义：

- 没有已知 warning 或 error issue

`rows = []` 在 v1 成功响应中非法：

- v1 不允许成功 `attribute_bundle` 出现该状态；若某个维度无法形成任何 driver rows，对应内部 `decompose` 应失败，整个 `attribute` 请求也应失败

## Agent Consumption Contract

`attribute_bundle` 面向 agent 的最小读取规则如下：

- 可查询轴：`metric`、`dimensions[*]`、`validation.status`
- 默认 dimension 顺序：请求顺序
- 默认 driver row 顺序：继承对应 `decompose` artifact 的稳定排序
- 稳定截断规则：每个维度最多返回 `projection_metadata.decomposition_limit` 行
- 局部最小闭包：读取 `comparison`、目标 `drivers[*]`、对应 typed refs 与 `projection_metadata` 即可恢复面向 agent 的最小归因视图

## Projection Policy

`attribute` 的 projection 必须保持确定性压缩。

允许：

- 每个维度只返回前 `projection_metadata.decomposition_limit` 个 drivers
- 把内部 `observe` refs 压缩成顶层 `observation_refs`
- 以单个 `comparison` 摘要承接 compare 结果
- 显式披露 truncation 与 unexplained remainder

不得：

- 根据中间结果临时增加或替换归因维度
- 隐藏 driver truncation、unexplained share 或 comparability / attribution issues
- 重排 atomic `decompose` 已定义的 driver 顺序
- 把 contribution rows 改写成因果结论
- 发明工件中不存在的新解释性 claim

### Driver Ordering And Truncation

每个 `drivers[*].rows` 的排序与截断规则必须稳定：

- 排序主规则完全继承对应 atomic `decompose`
- 若 atomic `decompose` 已定义 tie-breaker，则 `attribute` 必须继承同一 tie-breaker
- `returned_row_count` 等于当前 projection 实际返回的 row 数
- `total_row_count` 等于对应 canonical `decompose` artifact 中可枚举 contribution rows 总数
- `is_truncated = true` 当且仅当 `total_row_count > returned_row_count`
- `others_*` 只聚合被 truncation 省略的 rows，不包含 unexplained remainder

## 非法状态

以下状态在 canonical `attribute_bundle` 中是非法的：

- 成功响应缺少 `observation_refs.left_ref`、`observation_refs.right_ref`、`compare_ref` 中任一必需 ref
- `drivers[*]` 缺少 `decompose_ref`
- `comparison.comparability_status = "comparable"`，但 `comparison` 的关键数值字段以“未解析”之外的理由缺失
- `drivers[*].attribution_status = "attributable"`，但 `rows`、`returned_row_count`、`total_row_count` 之间自相矛盾
- 成功响应中任一 `drivers[*].rows = []`
- `is_truncated = false`，但 `others_*` 非 `null`
- `unexplained_reason = null`，但 `unexplained_*` 非 `null`
- 任一 ref 指向 projection 或跨 session artifact
- 把旧 lineage 的 `compare` / `decompose` artifact 重新挂接到新 bundle

## 例子

### 例 1：解释 DAU 环比变化

请求：

- `metric = "dau"`
- `left.time_scope = 本周`
- `right.time_scope = 上周`
- `dimensions = ["channel", "region"]`

含义：

- 先量化本周相对上周的 DAU delta
- 再分别按 `channel` 与 `region` 解释这次 delta

### 例 2：解释实验 uplift 的结构来源

请求：

- `metric = "conversion_rate_numerator"` 或其他 additive 实验指标
- `left.scope.predicate = experiment_group = treatment`
- `right.scope.predicate = experiment_group = control`
- `dimensions = ["user_segment", "traffic_source"]`

含义：

- 先量化 treatment 相对 control 的整体变化
- 再看 uplift 主要由哪些用户段和流量来源贡献

说明：

- v1 的 `decompose` 只支持 additive metric；如果目标实验指标本身是 non-additive rate，应改为请求其可加和的底层业务量，或等待未来独立契约

### 例 3：解释预算调整后的 GMV 变化

请求：

- `metric = "gmv"`
- `left.scope.predicate = budget_plan = new`
- `right.scope.predicate = budget_plan = old`
- `dimensions = ["campaign", "category", "province"]`

含义：

- 先量化新旧预算方案之间的 GMV delta
- 再把这次变化分配到各 campaign、category、province 的贡献项

## v1 Scope Limits

- 只支持单个 metric 的单次变化归因
- 只支持调用方显式给定 left / right scope
- 只支持显式归因维度列表
- 只支持内部 `scalar` compare
- 只支持 additive metric 的 `delta_share` 归因
- 不支持多维交互项、自动维度推荐、开放式下钻或因果解释
