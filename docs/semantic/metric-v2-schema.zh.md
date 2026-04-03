# Metric V2 Schema 设计草案

本文定义 Factum semantic layer 中 `metric` 的目标 v2 Schema 草案。

本文是语义设计文档，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/metric-process-contract.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`

本文重点回答：

- `metric` 在 v2 中应承载哪些 typed semantics
- 哪些内容应保留在 compiler / IR，而不应继续塞进 `metric`
- metric 应如何声明 comparability、inferential capability、additivity 与 process compatibility
- 常见 metric family 在 schema 上应如何表达

## Purpose

本文用于为 `metric` 提供一套更稳定、更受治理的 typed contract，使其能够：

- 替代当前以 `definition_sql` 为外部主语义的设计方式
- 为 `observe` / `compare` / `test` / `validate` / `attribute` 等 intent 提供稳定输入
- 与 `process object` 做受约束组合
- 让 compiler 在进入执行层前完成 measurement-aware validation

本文不定义：

- 最终数据库 DDL
- 最终 REST endpoint shape
- 最终 SQL 编译模板

## 背景

仅使用 `definition_sql` 定义 metric，会导致几个问题：

- 无法稳定表达 measurement identity
- 难以表达 numerator / denominator、观测实体、样本口径
- 难以表达 comparability 与适用边界
- 难以声明 inferential capability
- 难以明确 additive / non-additive 限制

因此，Factum 需要把 metric 从“SQL 表达式”提升为“typed measurement contract”。

## 设计目标

Metric v2 应满足以下目标：

- measurement identity 明确
- 可声明统计语义与运行边界
- 与 process object 的组合边界可校验
- 与 intent 的兼容性可校验
- 底层 execution payload 可替换，但不改变外部 semantic identity

## 非目标

本设计不追求：

- 复制 SQL 的全部表达力
- 把 cohort / funnel / session / path 过程规则继续塞进 metric
- 用 metric schema 直接表达 compiler 的 DAG 或 SQL AST
- 允许所有 metric family 在所有 intent 下无条件使用

## 核心概念

`metric` 回答的是：

- 最终量出的值是什么
- 它观测什么实体，以及以什么粒度稳定输出
- 它属于哪类 sample
- 是否可比较、可检验、可分解
- 它需要哪些 measurement refs，以及依赖哪些 process contract

它不负责回答：

- treatment / control 总体如何构造
- funnel 步骤如何匹配
- session 如何切分
- cohort 如何物化
- 底层字段、过滤谓词、去重策略、SQL kernel 如何实现

这些由 `process object` 或 compiler / IR 负责。

## 统一建模原则

### 1. metric 只表达 measurement contract，不表达 process 构造

metric 负责“量什么”，process object 负责“总体与过程如何形成”。

因此，像以下语义不应作为 metric 公共字段存在：

- `required_process_tags`
- `forbidden_process_tags`
- `attribution_basis = exposure / assignment`
- `attribution_window = 7 day`

更稳定的做法是让 metric 声明自己需要的结构化 process contract，例如：

- 需要的 `contract_mode`
- 需要的 `context_kind` 或 `entity_ref`
- 需要的 `emitted_grain_ref`（若 process 暴露实体流）
- 需要的 `provided_capabilities`
- 需要的 `comparison_contract`
- 需要的 `inference_support`

### 2. metric 公共 contract 不暴露执行 DSL

以下内容更适合放在 compiler normalization / IR / execution payload 中，而不是 public schema：

- 物理字段名
- 逻辑谓词树
- dedup key
- dedup strategy
- engine-specific percentile kernel
- approximate distinct / sketch 实现策略

public schema 应使用语义引用（semantic refs）表达“依赖哪个受治理概念”，而不是直接写“在哪个字段上执行什么逻辑”。

### 3. 观测实体与总体主体需要分层建模

`process-object-schema.zh.md` 已明确区分：

- `population_subject_ref`
- `context_kind` / `entity_ref`
- `emitted_grain_ref`（仅适用于 entity stream）

metric 也应使用与其兼容的结构，而不是只保留一个 `subject_key_semantics` 或单一 `observation_unit`。

metric 的公共 contract 应优先表达：

- `population_subject_ref`：若该 metric 对总体主体有稳定前提
- `observed_entity_ref`
- `observation_grain_ref`
- `primary_time_ref`

### 4. family-specific 语义必须收敛到 family payload

像 percentile、survival、score 来源、rate 的样本结构等语义，不应一部分放在公共组件里，一部分再放在 family payload 中。

否则会产生双重真相，给 compiler / validator 带来不必要歧义。

## 通用 Schema

### 公共头部

所有 metric 都应具备一组稳定的公共字段。

```python
from typing import Any, Literal, NotRequired, TypedDict


class MetricHeader(TypedDict):
    name: str
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
    status: NotRequired[Literal["draft", "published", "deprecated"]]
    metric_family: Literal[
        "count_metric",
        "sum_metric",
        "rate_metric",
        "average_metric",
        "distribution_metric",
        "score_metric",
        "survival_metric",
    ]
    population_subject_ref: NotRequired[str | None]
    observed_entity_ref: str
    observation_grain_ref: str
    sample_kind: Literal["numeric", "rate", "binary", "survival"]
    value_semantics: Literal[
        "count",
        "sum",
        "ratio",
        "mean",
        "distribution_statistic",
        "score",
        "survival_probability",
    ]
    aggregation_scope: NotRequired[
        Literal["subject", "event", "session", "window"] | None
    ]
    primary_time_ref: NotRequired[str | None]
    additivity: Literal["additive", "semi_additive", "non_additive"]
    supported_intents: NotRequired[list[str] | None]
    supported_result_modes: NotRequired[list[str] | None]
    comparability_group: NotRequired[str | None]
    comparability_tags: NotRequired[list[str] | None]
    required_dimensions: NotRequired[list[str] | None]
    forbidden_dimensions: NotRequired[list[str] | None]
    quality_gate_refs: NotRequired[list[str] | None]
    freshness_tolerance: NotRequired[str | None]
    required_process_contract: NotRequired["RequiredProcessContract" | None]
    inferential_capabilities: NotRequired["InferentialCapabilities" | None]
    default_scope_constraints: NotRequired[
        dict[str, str | int | float | bool | None] | None
    ]
    lineage: NotRequired[list[str] | None]
    properties: NotRequired[dict[str, Any] | None]
    revision: NotRequired[int]
    metric_contract_version: str
```

### Process 兼容契约

```python
from typing import Literal, NotRequired, TypedDict


class ComparisonRequirements(TypedDict):
    comparable_with_same_contract: NotRequired[bool | None]
    requires_same_anchor_semantics: NotRequired[bool | None]
    requires_same_window_semantics: NotRequired[bool | None]
    requires_same_partition_semantics: NotRequired[bool | None]


class TimeProjectionRequirements(TypedDict):
    supported: NotRequired[bool | None]
    bucket_stability: NotRequired[
        Literal["required", "optional", "not_supported"] | None
    ]


class InferenceRequirements(TypedDict):
    supports_population_split: NotRequired[bool | None]
    supports_pairwise_comparison: NotRequired[bool | None]
    supports_sample_summary_prep: NotRequired[bool | None]


class RequiredProcessContract(TypedDict):
    contract_modes: NotRequired[list[Literal["context_provider", "entity_stream"]] | None]
    population_subject_refs: NotRequired[list[str] | None]
    context_kinds: NotRequired[
        list[Literal["cohort_membership", "experiment_split"]] | None
    ]
    entity_refs: NotRequired[list[str] | None]
    emitted_grain_refs: NotRequired[list[str] | None]
    required_capabilities: NotRequired[list[str] | None]
    comparison_requirements: NotRequired[ComparisonRequirements | None]
    time_projection_requirements: NotRequired[TimeProjectionRequirements | None]
    inference_requirements: NotRequired[InferenceRequirements | None]
```

### 统计能力结构

```python
from typing import Literal, NotRequired, TypedDict


class InferentialCapabilities(TypedDict):
    inferential_ready: bool
    supported_sample_summaries: NotRequired[
        list[Literal["numeric_sample_summary", "rate_sample_summary"]] | None
    ]
    supported_methods: NotRequired[
        list[Literal["welch_t", "student_t", "two_proportion_z", "logrank", "auto"]]
        | None
    ]
    minimum_sample_size: NotRequired[int | float | None]
    minimum_event_count: NotRequired[int | float | None]
```

### 字段说明

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `name` | string | yes | metric 唯一名称 |
| `display_name` | string | no | 人类可读显示名 |
| `description` | string | no | 业务口径说明 |
| `status` | enum | no | 生命周期状态 |
| `metric_family` | enum | yes | metric 类型族 |
| `population_subject_ref` | string | no | 若该 metric 对总体主体有稳定要求，则指向对应语义引用 |
| `observed_entity_ref` | string | yes | metric 直接消费或观测的实体引用；必须使用 `entity.*` |
| `observation_grain_ref` | string | yes | metric 输出样本的稳定粒度引用；必须使用 `grain.*` |
| `sample_kind` | enum | yes | 样本类型 |
| `value_semantics` | enum | yes | 值语义 |
| `aggregation_scope` | enum | no | 聚合作用的主要层次 |
| `primary_time_ref` | string | no | metric 主时间语义引用 |
| `additivity` | enum | yes | 加和性语义 |
| `supported_intents` | array[string] | no | 支持的 intent |
| `supported_result_modes` | array[string] | no | 支持的 observation 结果模式 |
| `comparability_group` | string | no | 用于组织同口径可比 metric |
| `comparability_tags` | array[string] | no | 更细粒度的可比性标签 |
| `required_dimensions` | array[string] | no | 请求中必须出现的维度 |
| `forbidden_dimensions` | array[string] | no | 请求中不允许出现的维度 |
| `quality_gate_refs` | array[string] | no | 执行前应检查的 gate |
| `freshness_tolerance` | string | no | 数据时效容忍度 |
| `required_process_contract` | object | no | 与 process object 组合时所需的结构化契约 |
| `inferential_capabilities` | object | no | 检验和样本摘要能力 |
| `default_scope_constraints` | object | no | 默认附加的非时间 scope |
| `lineage` | array[string] | no | 上游依赖对象 |
| `properties` | object | no | 预留元数据扩展 |
| `revision` | integer | no | 发布版本序号 |
| `metric_contract_version` | string | yes | 合同版本 |

### 设计说明

几个字段需要特别强调：

- `observed_entity_ref` 与 `observation_grain_ref` 是 metric identity 的一部分
- `sample_kind` 直接决定可支持的 inferential summary contract
- `additivity` 直接影响 `compare -> decompose -> attribute` 的合法性
- `required_process_contract` 是 process-aware validation 的主入口
- `properties` 不能承载核心 measurement semantics

其中命名空间应保持统一：

- `population_subject_ref` 使用 `subject.*`
- `observed_entity_ref` 使用 `entity.*`
- `observation_grain_ref` 使用 `grain.*`

## 公共子结构

### SemanticRef

metric 与 process object 应优先共用同一套语义引用体系。

```python
from typing import NotRequired, TypedDict


class SemanticRef(TypedDict):
    ref: str
    description: NotRequired[str | None]
```

### Measurement Component

适用于 rate、average、部分 score metric。它只表达 measurement identity，不表达执行细节。

```python
from typing import Literal, NotRequired, TypedDict


class MeasurementComponent(TypedDict):
    name: str
    semantics: str
    entity_ref: NotRequired[str | None]
    grain: NotRequired[
        Literal["user", "device", "account", "order", "session", "event"] | None
    ]
    aggregation: Literal[
        "count",
        "count_distinct",
        "sum",
        "mean",
        "boolean_any",
        "boolean_all",
    ]
    measure_ref: NotRequired[str | None]
    qualifier_refs: NotRequired[list[str] | None]
    time_ref: NotRequired[str | None]
```

### Distribution Spec

```python
from typing import Literal, NotRequired, TypedDict


class DistributionSpec(TypedDict):
    kind: Literal["percentile", "quantile", "histogram_ready"]
    percentile: NotRequired[int | float | None]
    sketch_policy_ref: NotRequired[str | None]
```

### Survival Spec

```python
from typing import Literal, NotRequired, TypedDict


class HorizonSpec(TypedDict):
    value: int | float
    unit: Literal["day", "week", "month"]


class SurvivalSpec(TypedDict):
    origin_time_ref: str
    event_time_ref: str
    censor_time_ref: NotRequired[str | None]
    entry_time_ref: NotRequired[str | None]
    event_definition_ref: str
    censoring_policy: NotRequired[Literal["right_censor_only", "custom"] | None]
    horizon: NotRequired[HorizonSpec | None]
```

## Type 1: Count Metric

`count_metric` 用于表达以计数为主的指标，例如：

- `dau`
- `orders`
- `active_sessions`

### Schema

```python
from typing import Literal


class CountMetric(MetricHeader):
    metric_family: Literal["count_metric"]
    count_target: MeasurementComponent
```

### 示例

```json
{
  "name": "dau",
  "display_name": "Daily Active Users",
  "metric_family": "count_metric",
  "population_subject_ref": "subject.user",
  "observed_entity_ref": "entity.user",
  "observation_grain_ref": "grain.user",
  "sample_kind": "numeric",
  "value_semantics": "count",
  "aggregation_scope": "subject",
  "primary_time_ref": "time.activity_time",
  "additivity": "non_additive",
  "supported_intents": ["observe", "compare", "detect"],
  "supported_result_modes": ["standard", "numeric_sample_summary"],
  "comparability_group": "active_user_metrics",
  "quality_gate_refs": ["gate.activity_completeness"],
  "required_process_contract": {
    "emitted_grain_refs": ["grain.user"],
    "time_projection_requirements": {
      "supported": true,
      "bucket_stability": "required"
    }
  },
  "inferential_capabilities": {
    "inferential_ready": true,
    "supported_sample_summaries": ["numeric_sample_summary"],
    "supported_methods": ["welch_t", "auto"]
  },
  "metric_contract_version": "v1",
  "count_target": {
    "name": "active_users",
    "semantics": "distinct active users",
    "grain": "user",
    "aggregation": "count_distinct",
    "measure_ref": "measure.active_user"
  }
}
```

## Type 2: Sum Metric

`sum_metric` 用于表达可求和的业务量，例如：

- `gmv`
- `ad_revenue`
- `conversion_count`

### Schema

```python
from typing import Literal


class SumMetric(MetricHeader):
    metric_family: Literal["sum_metric"]
    measure: MeasurementComponent
```

### 示例

```json
{
  "name": "gmv",
  "metric_family": "sum_metric",
  "population_subject_ref": "subject.order",
  "observed_entity_ref": "entity.order",
  "observation_grain_ref": "grain.order",
  "sample_kind": "numeric",
  "value_semantics": "sum",
  "aggregation_scope": "event",
  "primary_time_ref": "time.payment_time",
  "additivity": "additive",
  "supported_intents": ["observe", "compare", "decompose", "attribute", "detect"],
  "supported_result_modes": ["standard", "numeric_sample_summary"],
  "comparability_group": "commerce_value_metrics",
  "metric_contract_version": "v1",
  "measure": {
    "name": "paid_amount",
    "semantics": "sum of paid amount",
    "grain": "order",
    "aggregation": "sum",
    "measure_ref": "measure.paid_amount"
  }
}
```

## Type 3: Rate Metric

`rate_metric` 用于表达分子/分母明确的比例类指标，例如：

- `conversion_rate`
- `retention_rate`
- `crash_rate`

### Schema

```python
from typing import Literal, NotRequired


class RateMetric(MetricHeader):
    metric_family: Literal["rate_metric"]
    numerator: MeasurementComponent
    denominator: MeasurementComponent
    default_test_method: NotRequired[Literal["two_proportion_z", "auto"] | None]
```

### 字段说明

- `numerator` / `denominator` 是 rate metric identity 的重要组成部分
- rate metric 不应在公共 contract 中固化 assignment / exposure / attribution window 这类 process 语义
- `default_test_method` 仅声明默认检验建议，不替代 runtime validation

### 示例

```json
{
  "name": "conversion_rate",
  "display_name": "Conversion Rate",
  "metric_family": "rate_metric",
  "population_subject_ref": "subject.user",
  "observed_entity_ref": "entity.user",
  "observation_grain_ref": "grain.user",
  "sample_kind": "rate",
  "value_semantics": "ratio",
  "aggregation_scope": "subject",
  "primary_time_ref": "time.conversion_time",
  "additivity": "non_additive",
  "supported_intents": ["observe", "compare", "test", "validate"],
  "supported_result_modes": ["standard", "rate_sample_summary"],
  "comparability_group": "experiment_primary_metrics",
  "quality_gate_refs": ["gate.experiment_population_ready"],
  "required_process_contract": {
    "contract_modes": ["context_provider"],
    "population_subject_refs": ["subject.user"],
    "context_kinds": ["experiment_split"],
    "required_capabilities": [
      "population_membership",
      "variant_split"
    ],
    "inference_requirements": {
      "supports_population_split": true,
      "supports_pairwise_comparison": true,
      "supports_sample_summary_prep": true
    }
  },
  "inferential_capabilities": {
    "inferential_ready": true,
    "supported_sample_summaries": ["rate_sample_summary"],
    "supported_methods": ["two_proportion_z", "auto"],
    "minimum_sample_size": 1000
  },
  "metric_contract_version": "v1",
  "numerator": {
    "name": "converted_users",
    "semantics": "users who converted",
    "grain": "user",
    "aggregation": "count_distinct",
    "measure_ref": "measure.converted_user",
    "qualifier_refs": ["predicate.converted"]
  },
  "denominator": {
    "name": "eligible_users",
    "semantics": "eligible users in analysis population",
    "grain": "user",
    "aggregation": "count_distinct",
    "measure_ref": "measure.eligible_user"
  },
  "default_test_method": "two_proportion_z"
}
```

## Type 4: Average Metric

`average_metric` 用于表达均值类指标，例如：

- `avg_watch_time`
- `avg_order_value`

### Schema

```python
from typing import Literal


class AverageMetric(MetricHeader):
    metric_family: Literal["average_metric"]
    numerator: MeasurementComponent
    denominator: MeasurementComponent
```

### 设计说明

average metric 与 rate metric 类似，都有 numerator / denominator 结构，但：

- `sample_kind` 通常是 `numeric`
- `value_semantics` 通常是 `mean`
- inferential summary 多使用 `numeric_sample_summary`
- 如果它依赖 session 粒度，应通过 `observed_entity_ref` / `observation_grain_ref` 与 `required_process_contract` 显式表达

### 示例

```json
{
  "name": "avg_watch_time",
  "metric_family": "average_metric",
  "population_subject_ref": "subject.user",
  "observed_entity_ref": "entity.session",
  "observation_grain_ref": "grain.session",
  "sample_kind": "numeric",
  "value_semantics": "mean",
  "aggregation_scope": "session",
  "primary_time_ref": "time.session_start_time",
  "additivity": "non_additive",
  "supported_intents": ["observe", "compare", "detect", "test"],
  "supported_result_modes": ["standard", "numeric_sample_summary"],
  "comparability_group": "session_quality_metrics",
  "required_process_contract": {
    "contract_modes": ["entity_stream"],
    "population_subject_refs": ["subject.user"],
    "entity_refs": ["entity.session"],
    "emitted_grain_refs": ["grain.session"],
    "required_capabilities": [
      "session_partition",
      "time_projection"
    ],
    "time_projection_requirements": {
      "supported": true,
      "bucket_stability": "required"
    }
  },
  "inferential_capabilities": {
    "inferential_ready": true,
    "supported_sample_summaries": ["numeric_sample_summary"],
    "supported_methods": ["welch_t", "auto"]
  },
  "metric_contract_version": "v1",
  "numerator": {
    "name": "total_watch_seconds",
    "semantics": "sum of watch duration",
    "grain": "session",
    "aggregation": "sum",
    "measure_ref": "measure.watch_seconds"
  },
  "denominator": {
    "name": "session_count",
    "semantics": "count of sessions",
    "grain": "session",
    "aggregation": "count_distinct",
    "measure_ref": "measure.session"
  }
}
```

## Type 5: Distribution Metric

`distribution_metric` 用于表达分布或分位数类指标，例如：

- `latency_p95`
- `order_amount_p90`

### Schema

```python
from typing import Literal


class DistributionMetric(MetricHeader):
    metric_family: Literal["distribution_metric"]
    value_component: MeasurementComponent
    distribution_spec: DistributionSpec
```

### 设计说明

这类 metric 往往：

- `additivity = non_additive`
- 可比较，但不适合直接进入 additive attribution
- 分布特有语义应统一落在 `distribution_spec`，避免和通用 `MeasurementComponent` 重复表达 percentile

### 示例

```json
{
  "name": "latency_p95",
  "metric_family": "distribution_metric",
  "population_subject_ref": "subject.request",
  "observed_entity_ref": "entity.event",
  "observation_grain_ref": "grain.event",
  "sample_kind": "numeric",
  "value_semantics": "distribution_statistic",
  "aggregation_scope": "event",
  "primary_time_ref": "time.request_time",
  "additivity": "non_additive",
  "supported_intents": ["observe", "compare", "detect"],
  "supported_result_modes": ["standard"],
  "comparability_group": "latency_metrics",
  "metric_contract_version": "v1",
  "value_component": {
    "name": "latency_ms",
    "semantics": "request latency",
    "grain": "event",
    "aggregation": "mean",
    "measure_ref": "measure.latency_ms"
  },
  "distribution_spec": {
    "kind": "percentile",
    "percentile": 0.95,
    "sketch_policy_ref": "policy.latency_percentile"
  }
}
```

## Type 6: Score Metric

`score_metric` 用于表达预计算分数或模型分数，例如：

- `fraud_score`
- `ranking_quality_score`

### Schema

```python
from typing import Literal, NotRequired


class ScoreMetric(MetricHeader):
    metric_family: Literal["score_metric"]
    score_source: MeasurementComponent
    score_kind: NotRequired[
        Literal["precomputed", "model_output", "rule_score"] | None
    ]
```

### 设计说明

这类 metric 通常：

- measurement semantics 明确
- execution kernel 可能高度特化
- 可支持 observe / compare / detect
- 是否支持检验要看 sample summary 是否稳定

### 示例

```json
{
  "name": "fraud_score",
  "metric_family": "score_metric",
  "population_subject_ref": "subject.user",
  "observed_entity_ref": "entity.user",
  "observation_grain_ref": "grain.user",
  "sample_kind": "numeric",
  "value_semantics": "score",
  "aggregation_scope": "subject",
  "primary_time_ref": "time.score_time",
  "additivity": "non_additive",
  "supported_intents": ["observe", "compare", "detect"],
  "supported_result_modes": ["standard"],
  "comparability_group": "risk_score_metrics",
  "metric_contract_version": "v1",
  "score_source": {
    "name": "fraud_score_value",
    "semantics": "precomputed fraud score",
    "grain": "user",
    "aggregation": "mean",
    "measure_ref": "measure.fraud_score"
  },
  "score_kind": "model_output"
}
```

## Type 7: Survival Metric

`survival_metric` 用于表达 survival / time-to-event 类指标。

### Schema

```python
from typing import Literal


class SurvivalMetric(MetricHeader):
    metric_family: Literal["survival_metric"]
    survival_spec: SurvivalSpec
```

### 设计说明

这类 metric 常用于：

- 留存生存分析
- time-to-conversion
- time-to-churn

它通常要求：

- `sample_kind = survival`
- 专门 inferential capability
- process object 能稳定提供 cohort / population / time anchor 相关 contract
- event time 与 censor time 必须显式表达，不能只用布尔组件替代

### 示例

```json
{
  "name": "time_to_churn",
  "metric_family": "survival_metric",
  "population_subject_ref": "subject.user",
  "observed_entity_ref": "entity.user",
  "observation_grain_ref": "grain.user",
  "sample_kind": "survival",
  "value_semantics": "survival_probability",
  "aggregation_scope": "subject",
  "primary_time_ref": "time.churn_eval_time",
  "additivity": "non_additive",
  "supported_intents": ["observe", "compare", "test"],
  "supported_result_modes": ["standard"],
  "comparability_group": "retention_survival_metrics",
  "required_process_contract": {
    "contract_modes": ["context_provider"],
    "population_subject_refs": ["subject.user"],
    "context_kinds": ["cohort_membership"],
    "required_capabilities": [
      "population_membership",
      "anchor_time",
      "windowed_observation"
    ],
    "comparison_requirements": {
      "comparable_with_same_contract": true,
      "requires_same_anchor_semantics": true,
      "requires_same_window_semantics": true
    }
  },
  "inferential_capabilities": {
    "inferential_ready": true,
    "supported_methods": ["logrank", "auto"]
  },
  "metric_contract_version": "v1",
  "survival_spec": {
    "origin_time_ref": "time.signup_time",
    "event_time_ref": "time.churn_time",
    "censor_time_ref": "time.last_observed_time",
    "event_definition_ref": "event.churn",
    "censoring_policy": "right_censor_only",
    "horizon": {
      "value": 90,
      "unit": "day"
    }
  }
}
```

## Process Compatibility 建议

metric 与 process object 的兼容性不应只在运行期碰运气。

建议基于以下维度显式校验：

- metric `required_process_contract.contract_modes`
- metric `required_process_contract.population_subject_refs`
- metric `required_process_contract.context_kinds` / `entity_refs`
- metric `required_process_contract.emitted_grain_refs`
- metric `required_process_contract.required_capabilities`
- metric 的 `sample_kind`
- metric 的 `supported_intents`
- process 的 `interface_contract`

### 例子

- `conversion_rate` 这类实验指标通常要求 process 提供 `variant_split + sample_summary_prep`
- `avg_watch_time` 这类会话质量指标通常要求 process 暴露 `session` entity stream 且具备 `time_projection`
- `time_to_churn` 这类 survival metric 通常要求 process 提供 `population_membership + anchor_time + windowed_observation`
- `gmv` 未必适合直接绑定只输出 `path_match` 的 `funnel_definition`

## Result Mode Compatibility 建议

metric 还应显式声明自己支持哪些 observation result mode。

例如：

- `rate_metric` 常支持 `standard` + `rate_sample_summary`
- `average_metric` 常支持 `standard` + `numeric_sample_summary`
- `distribution_metric` 往往只支持 `standard`

若请求的 result mode 不被 metric 支持，应在 validation 阶段拒绝，而不是运行后失败。

## Additivity 规则

`additivity` 是 metric v2 中非常关键的字段。

建议语义如下：

- `additive`：允许在合法 slice 上对账与分解
- `semi_additive`：仅在部分轴上可加和
- `non_additive`：不能假定切片和整体天然可对账

对应地：

- `decompose` 应优先允许 `additive`
- `semi_additive` 需要额外 comparability / axis validation
- `non_additive` 不能直接套用 additive attribution contract

例如：

- `gmv` 通常是 `additive`
- `inventory_balance` 可能是 `semi_additive`
- `conversion_rate`、`latency_p95` 通常是 `non_additive`

## 与 SQL / execution payload 的关系

metric v2 并不要求完全移除底层 SQL 或 engine-specific kernel。

更合理的关系是：

- metric contract 负责外部 measurement semantics
- compiler / IR 负责把其翻译成内部计划
- execution layer 负责最终 SQL / adapter plan

必要时，可保留：

- legacy `definition_sql`
- 特定引擎 percentile kernel
- sketch / approximate distinct kernel
- 模型分数读取逻辑

但这些都不应成为 metric identity 的主来源。

## Validator 建议

metric 创建或发布前，建议至少校验：

- `metric_family` 与 `sample_kind`、`value_semantics` 不冲突
- `observed_entity_ref`、`observation_grain_ref` 与 family 语义不冲突
- `rate_metric` / `average_metric` 的 `numerator`、`denominator` 必填
- `distribution_spec.percentile` 在 `0 < p < 1`
- `supported_result_modes` 与 `inferential_capabilities` 一致
- `required_dimensions` 与 `forbidden_dimensions` 不重叠
- `quality_gate_refs`、`comparability_tags` 不重复
- 所有 `*_ref` 均可解析

组合期还应额外校验：

- metric 与 process object 是否兼容
- metric 是否支持请求 intent
- metric 是否支持请求 result mode
- metric 的 `additivity` 是否满足下游分析动作
- inferential capability 是否满足 `test` / `validate`

## 与 IR 的关系

metric schema 不是 IR。

例如：

- `numerator` / `denominator` 不是最终执行 DAG
- `additivity` 不是 compare artifact 本身
- `inferential_capabilities` 不是最终 test 方法调度逻辑
- `required_process_contract` 不是最终 process join / materialization plan；它只是对 `interface_contract` 的 typed 需求

这些都只是 compiler 的 typed inputs。IR 负责把它们转成：

- measurement nodes
- sample summary nodes
- validation nodes
- execution boundary nodes

## 迁移建议

建议按以下顺序演进：

### 第一阶段

让 metric 同时具备：

- 现有 execution payload
- 新增 typed contract 字段

并逐步让 planner / validator 优先使用 typed 字段，而不是直接依赖 `definition_sql`。

### 第二阶段

优先删除或降级以下字段的主契约地位：

- `subject_key_semantics`
- `observation_unit`
- `compatibility_tags`
- `required_process_tags`
- `forbidden_process_tags`
- `MeasurementComponent.field`
- `MeasurementComponent.predicate`
- `MeasurementComponent.dedup_key`
- `MeasurementComponent.dedup_strategy`

### 第三阶段

补齐 process-aware validation，并把：

- comparability
- inferential readiness
- additivity
- result mode support
- entity / grain compatibility

做成显式校验层。

### 第四阶段

再逐步纳入更复杂族：

- `distribution_metric`
- `score_metric`
- `survival_metric`

并统一其 family-specific payload，避免跨层重复表达。

## 总结

Metric v2 的目标不是让 semantic layer 再次退回 SQL registry，而是把业务度量真正提升为可校验、可治理、可组合的 measurement contract。

一套好的 metric schema 应做到：

- identity 清晰
- 统计语义清晰
- 与 process object 的边界清晰
- 与 intent 的兼容性清晰
- 与 execution payload 解耦

一句话总结：

> Factum 的 metric v2 应以 typed measurement contract 为中心，显式表达 observed entity、observation grain、sample kind、family-specific measurement payload、additivity、comparability 与 required process contract，并把过程构造与执行细节下沉到 process object 和 compiler / IR。
