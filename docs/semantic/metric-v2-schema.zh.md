# Metric V2 Schema 设计草案

本文定义 Marivo semantic layer 中 `metric` 的目标 v2 Schema 草案。

本文是语义设计文档，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/metric-process-contract.zh.md`
- `docs/semantic/predicate-schema-contract.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/time-schema-contract.zh.md`
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
- 通过 `dimension.*` 引用受治理的分析轴，而不是继续使用自由字符串
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

因此，Marivo 需要把 metric 从“SQL 表达式”提升为“typed measurement contract”。

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

底层事实的 late arrival、incomplete-window 行为、外部锚点导入等物理消费细节，属于 typed binding contract，而不属于 metric 本体。
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

其中 `primary_time_ref` 的统一语义应由 `time-schema-contract.zh.md` 定义；它表达的是 measurement 主时间，而不是 attribution basis、process window 或 late-arrival policy。

### 4. family-specific 语义必须收敛到 family payload

像 percentile、survival、score 来源、rate 的样本结构等语义，不应一部分放在公共组件里，一部分再放在 family payload 中。

否则会产生双重真相，给 compiler / validator 带来不必要歧义。

## 通用 Schema

### 公共头部

所有 metric 都应具备一组稳定的公共字段。

```python
from typing import Literal, NotRequired, TypedDict


class MetricHeader(TypedDict):
    metric_ref: str
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
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
    additivity_constraints: AdditivityConstraints
    metric_contract_version: str


class AdditivityConstraints(TypedDict):
    dimension_policy: Literal["all", "subset", "none"]
    additive_dimensions: NotRequired[list[str]]
    time_axis_policy: Literal["additive", "non_additive"]
    notes: NotRequired[str]
```

`MetricHeader` 现在只保留 measurement 主 contract 中稳定且必须的字段。

**以下字段不应进入 public metric schema，由 compiler 从核心字段推导：**

- `supported_intents`：由 `sample_kind` + `additivity_constraints` 推导
- `inferential_capabilities`：由 `sample_kind` 推导

**以下字段属于 compiler compatibility profile，不是 metric 主 schema：**

- `result_modes`、`comparability_group`、`required_process_contract`、`freshness_tolerance`

### Capability 推导规则（compiler 负责）

| 推导能力 | 推导规则 |
|---------|---------|
| `supports_observe` | 始终为 true |
| `supports_compare` | `additivity_constraints` 存在 AND `primary_time_ref` exists |
| `supports_test` | `sample_kind` in ["numeric", "rate", "binary"] |
| `supports_decompose` | `additivity_constraints.dimension_policy` in ["all", "subset"] |
| `supports_detect` | `process.anchor_time_ref` exists OR `metric.primary_time_ref` exists |
| `supports_validate` | `sample_kind` == "rate" AND `process.anchor_time_ref` exists |

### Process 兼容 profile（非 public metric schema）

若 metric 对 process 有编译期结构要求，应通过独立的 compiler compatibility profile 声明，而不是作为 metric public contract 字段。

其 schema 由 `docs/semantic/compiler-compatibility-profile.zh.md` 唯一定义，主要包含：

- `contract_modes`：metric 对 process 接口类型的要求
- `context_kinds`：metric 对 process context kind 的要求
- `entity_refs`：metric 对 process 实体流类型的要求
- `population_subject_refs`：metric 对 process 总体主体的要求

本文不重复定义这些结构。

### 字段说明

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `metric_ref` | string | yes | metric 的稳定公共引用；必须使用 `metric.*`；也是 public contract 主标识 |
| `display_name` | string | no | 人类可读显示名 |
| `description` | string | no | 业务口径说明 |
| `metric_family` | enum | yes | metric 类型族 |
| `population_subject_ref` | string | no | 若该 metric 对总体主体有稳定要求，则指向对应语义引用 |
| `observed_entity_ref` | string | yes | metric 直接消费或观测的实体引用；必须使用 `entity.*` |
| `observation_grain_ref` | string | yes | metric 输出样本的稳定粒度引用；必须使用 `grain.*` |
| `sample_kind` | enum | yes | 样本类型 |
| `value_semantics` | enum | yes | 值语义 |
| `aggregation_scope` | enum | no | 聚合作用的主要层次 |
| `primary_time_ref` | string | no | metric 主时间语义引用 |
| `additivity_constraints` | AdditivityConstraints | yes | 结构化加和性约束：维度可分解策略与时间轴策略 |
| `metric_contract_version` | string | yes | 合同版本 |

### 设计说明

几个字段需要特别强调：

- `observed_entity_ref` 与 `observation_grain_ref` 是 metric identity 的一部分
- `sample_kind` 直接决定可支持的 inferential summary contract
- `additivity_constraints` 直接影响 `compare -> decompose -> attribute` 的合法性：`dimension_policy` 决定维度可分解性，`time_axis_policy` 决定时间轴可加性
- 维度兼容、process 兼容、统计能力与 freshness 等规则若需要存在，应由 compiler compatibility profile / governance context 承担，而不是回流到 public metric header
- 上述 `RequiredProcessContract` / `InferentialCapabilities` 的地位是 **profile 子结构**，不是 metric public schema，也不是 engine dispatch plan

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
```

`MeasurementComponent` 不再重复声明 `entity_ref`、`grain`、`time_ref`。这些 measurement identity 已由顶层 `MetricHeader` 统一给出，family payload 不应制造第二套真相。因此以下字段已从 `MeasurementComponent` 中移除：

- `grain`：已由 `observation_grain_ref` 统一表达
- `field`：执行细节，不属于 public contract
- `predicate`：执行细节，不属于 public contract
- `dedup_key`：执行细节，不属于 public contract
- `dedup_strategy`：执行细节，不属于 public contract

其中 `qualifier_refs` 的语义边界应保持明确：

- 它引用受治理的 `predicate.*` contract，而不是内联 SQL 或局部 filter DSL
- 它表达 component-specific business predicates，是 measurement identity 的一部分
- 对 `rate_metric` / `average_metric`，numerator 与 denominator 的 `qualifier_refs` 必须分别保留 lineage，不能在编译期被压平成单个全局 predicate
- 若所有 component 共享同一组默认过滤，应通过 metric-level `default_predicate_refs` 一类的 ref list 表达，而不是吞并 component qualifier lineage

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

以下示例只展示 metric public contract。若需要声明对 process 的编译期要求，应通过独立 compiler profile 发布，见 `docs/semantic/compiler-compatibility-profile.zh.md`。

```json
{
  "metric_ref": "metric.dau",
  "display_name": "Daily Active Users",
  "metric_family": "count_metric",
  "population_subject_ref": "subject.user",
  "observed_entity_ref": "entity.user",
  "observation_grain_ref": "grain.user",
  "sample_kind": "numeric",
  "value_semantics": "count",
  "aggregation_scope": "subject",
  "primary_time_ref": "time.activity_time",
  "additivity_constraints": {
    "dimension_policy": "none",
    "time_axis_policy": "non_additive"
  },
  "metric_contract_version": "v1",
  "count_target": {
    "name": "active_users",
    "semantics": "distinct active users",
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
  "metric_ref": "metric.gmv",
  "display_name": "GMV",
  "metric_family": "sum_metric",
  "population_subject_ref": "subject.order",
  "observed_entity_ref": "entity.order",
  "observation_grain_ref": "grain.order",
  "sample_kind": "numeric",
  "value_semantics": "sum",
  "aggregation_scope": "event",
  "primary_time_ref": "time.payment_time",
  "additivity_constraints": {
    "dimension_policy": "all",
    "time_axis_policy": "additive"
  },
  "metric_contract_version": "v1",
  "measure": {
    "name": "paid_amount",
    "semantics": "sum of paid amount",
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
- 若 rate metric 依赖实验或归因窗口，窗口本体仍应来自 process object / metric-process contract；metric binding 只负责消费映射与治理策略
- `default_test_method` 仅声明默认检验建议，不替代 runtime validation

### 示例

```json
{
  "metric_ref": "metric.conversion_rate",
  "display_name": "Conversion Rate",
  "metric_family": "rate_metric",
  "population_subject_ref": "subject.user",
  "observed_entity_ref": "entity.user",
  "observation_grain_ref": "grain.user",
  "sample_kind": "rate",
  "value_semantics": "ratio",
  "aggregation_scope": "subject",
  "primary_time_ref": "time.conversion_time",
  "additivity_constraints": {
    "dimension_policy": "none",
    "time_axis_policy": "non_additive"
  },
  "metric_contract_version": "v1",
  "numerator": {
    "name": "converted_users",
    "semantics": "users who converted",
    "aggregation": "count_distinct",
    "measure_ref": "measure.converted_user",
    "qualifier_refs": ["predicate.converted"]
  },
  "denominator": {
    "name": "eligible_users",
    "semantics": "eligible users in analysis population",
    "aggregation": "count_distinct",
    "measure_ref": "measure.eligible_user"
  },
  "default_test_method": "two_proportion_z"
}
```

若需要声明对实验 process 的编译期要求，应额外发布 compiler profile：

```json
{
  "profile_ref": "compiler_profile.conversion_rate_requirement",
  "profile_kind": "requirement",
  "schema_version": "v1",
  "subject_kind": "metric",
  "subject_ref": "metric.conversion_rate",
  "requirement": {
    "contract_modes": ["context_provider"],
    "context_kinds": ["experiment_split"],
    "population_subject_refs": ["subject.user"]
  }
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
- 如果它依赖 session 粒度，应通过 `observed_entity_ref` / `observation_grain_ref` 明确表达；若还需要更细 process 前提，则交给 compiler compatibility profile

### 示例

```json
{
  "metric_ref": "metric.avg_watch_time",
  "display_name": "Average Watch Time",
  "metric_family": "average_metric",
  "population_subject_ref": "subject.user",
  "observed_entity_ref": "entity.session",
  "observation_grain_ref": "grain.session",
  "sample_kind": "numeric",
  "value_semantics": "mean",
  "aggregation_scope": "session",
  "primary_time_ref": "time.session_start_time",
  "additivity_constraints": {
    "dimension_policy": "none",
    "time_axis_policy": "non_additive"
  },
  "metric_contract_version": "v1",
  "numerator": {
    "name": "total_watch_seconds",
    "semantics": "sum of watch duration",
    "aggregation": "sum",
    "measure_ref": "measure.watch_seconds"
  },
  "denominator": {
    "name": "session_count",
    "semantics": "count of sessions",
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

- `additivity_constraints.dimension_policy = "none"`, `additivity_constraints.time_axis_policy = "non_additive"`
- 可比较，但不适合直接进入 additive attribution
- 分布特有语义应统一落在 `distribution_spec`，避免和通用 `MeasurementComponent` 重复表达 percentile

### 示例

```json
{
  "metric_ref": "metric.latency_p95",
  "display_name": "Latency P95",
  "metric_family": "distribution_metric",
  "population_subject_ref": "subject.request",
  "observed_entity_ref": "entity.event",
  "observation_grain_ref": "grain.event",
  "sample_kind": "numeric",
  "value_semantics": "distribution_statistic",
  "aggregation_scope": "event",
  "primary_time_ref": "time.request_time",
  "additivity_constraints": {
    "dimension_policy": "none",
    "time_axis_policy": "non_additive"
  },
  "metric_contract_version": "v1",
  "value_component": {
    "name": "latency_ms",
    "semantics": "request latency",
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
  "metric_ref": "metric.fraud_score",
  "display_name": "Fraud Score",
  "metric_family": "score_metric",
  "population_subject_ref": "subject.user",
  "observed_entity_ref": "entity.user",
  "observation_grain_ref": "grain.user",
  "sample_kind": "numeric",
  "value_semantics": "score",
  "aggregation_scope": "subject",
  "primary_time_ref": "time.score_time",
  "additivity_constraints": {
    "dimension_policy": "none",
    "time_axis_policy": "non_additive"
  },
  "metric_contract_version": "v1",
  "score_source": {
    "name": "fraud_score_value",
    "semantics": "precomputed fraud score",
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
  "metric_ref": "metric.time_to_churn",
  "display_name": "Time to Churn",
  "metric_family": "survival_metric",
  "population_subject_ref": "subject.user",
  "observed_entity_ref": "entity.user",
  "observation_grain_ref": "grain.user",
  "sample_kind": "survival",
  "value_semantics": "survival_probability",
  "aggregation_scope": "subject",
  "primary_time_ref": "time.churn_eval_time",
  "additivity_constraints": {
    "dimension_policy": "none",
    "time_axis_policy": "non_additive"
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

- metric 的 `sample_kind`
- metric 的 `observed_entity_ref` / `observation_grain_ref`
- process 的 `interface_contract`
- 若存在 compiler compatibility profile，则其中的 process requirements

### 例子

- `conversion_rate` 这类实验指标通常要求 process 提供 `experiment_split` 上下文与稳定 `anchor_time_ref`
- `avg_watch_time` 这类会话质量指标通常要求 process 暴露 `session` entity stream
- `time_to_churn` 这类 survival metric 通常要求 process 提供稳定 `population_subject_ref + anchor_time_ref`
- `gmv` 未必适合直接绑定只输出 `path_match` 的 `funnel_definition`

## Result Mode Compatibility 建议

若系统最终要持久化 result mode 兼容矩阵，建议把它放进 compiler compatibility profile，而不是回写到 `MetricHeader`。

例如：

- `rate_metric` 常支持 `standard` + `rate_sample_summary`
- `average_metric` 常支持 `standard` + `numeric_sample_summary`
- `distribution_metric` 往往只支持 `standard`

若请求的 result mode 不被 metric 支持，应在 validation 阶段拒绝，而不是运行后失败。

## Additivity Constraints 规则

`additivity_constraints` 是 metric v2 中非常关键的结构化字段，替代了旧的三态 `additivity` 枚举。

`additivity_constraints` 包含两个独立的策略轴：

- `dimension_policy`：控制维度上的可分解性
- `time_axis_policy`：控制时间轴上的可加性

二者独立，不可互推。

### dimension_policy

- `"all"`：允许对所有已声明且可 groupable 的 semantic dimensions 做归因。适用于普通 sum / count（不含 distinct / window / 非线性 aggregation）的 metric。
- `"subset"`：只允许对 `additive_dimensions` 中列出的 dimensions 做归因。适用于某些维度上可分解但非全部的 metric（如按仓库可加的库存余额）。
- `"none"`：不允许做维度归因。适用于 rate / ratio / avg / percentile / score / survival 类 metric，以及未确认稳定互斥维度的 distinct / UV / DAU 类 metric。

### time_axis_policy

- `"additive"`：允许沿声明的时间语义做时间轴聚合。
- `"non_additive"`：不允许跨 time bucket / period 直接 rollup。

### 两个策略轴独立

- 允许按某个 dimension 做 `decompose`，不代表该 metric 可以跨 time bucket 相加。
- 时间轴上可做某种聚合，也不代表该 metric 对任意 dimension 都可稳定归因。

例如：

- `gmv`：`dimension_policy = "all"`, `time_axis_policy = "additive"`
- `dau`（count_distinct）：`dimension_policy = "none"`, `time_axis_policy = "non_additive"`
- `inventory_balance`：`dimension_policy = "subset"`, `additive_dimensions = ["dimension.warehouse"]`, `time_axis_policy = "non_additive"`
- `conversion_rate`：`dimension_policy = "none"`, `time_axis_policy = "non_additive"`

### 约束规则

- `dimension_policy = "all"` 时，`additive_dimensions` 必须为 null 或缺失
- `dimension_policy = "subset"` 时，`additive_dimensions` 必须为非空列表
- `dimension_policy = "none"` 时，`additive_dimensions` 必须为 null 或缺失
- `MeasurementComponent.aggregation = "count_distinct"` 时，不得使用 `dimension_policy = "all"`
- 缺少完整 constraints 的 metric 不得暴露虚假的 decompose / attribute 能力

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
- 所有 `*_ref` 均可解析
- `qualifier_refs` 若存在，必须引用可解析的 `predicate.*`
- `qualifier_refs` 使用的 predicate 必须允许 `metric_qualifier` usage

组合期还应额外校验：

- metric 与 process object 是否兼容
- metric 的 `additivity_constraints` 是否满足下游分析动作
- 请求维度若为 `time_derived`，其 `required_time_anchor_ref` 是否被 metric / process 组合满足
- 若存在 compiler profile，则其 result mode / inference 规则是否满足 `test` / `validate`
- 多 component metric 的 predicate lineage 是否可分别解析、验证并保留

## 与 IR 的关系

metric schema 不是 IR。

例如：

- `numerator` / `denominator` 不是最终执行 DAG
- `additivity_constraints` 不是 compare artifact 本身
- compiler profile 不是最终 test 方法调度逻辑
- compiler profile 不是最终 process join / materialization plan；它只是对 `interface_contract` 的 typed 需求

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
- additivity_constraints
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

> Marivo 的 metric v2 应以 typed measurement contract 为中心，显式表达 observed entity、observation grain、sample kind、family-specific measurement payload、additivity_constraints、comparability 与 required process contract，并把过程构造与执行细节下沉到 process object 和 compiler / IR。
