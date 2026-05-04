# Metric V2 Schema 设计草案

> **过渡说明**：本文档已更新为反映 OSI Metric + MARIVO 扩展模型。原有 `metric_family`、`MeasurementComponent`、`qualifier_refs`、`default_predicate_refs`、process dependency 字段及 lifecycle state machine 已移除或替换为 OSI Dataset-native 概念。具体映射如下：
> - `metric_family` → 删除；Metric 类型通过 `sample_kind` / `value_semantics` / `additivity` 推导
> - `MeasurementComponent` / `qualifier_refs` / `default_predicate_refs` → `Metric.filters`（MARIVO 扩展）
> - `observed_entity_ref` → `observed_dataset`（OSI Dataset 引用）
> - `observation_grain_ref` → `observation_grain`（OSI grain 概念）
> - `primary_time_ref` → `primary_time_field`（OSI Field 引用）
> - `process_dependency` / `process.*` 字段 → 删除；过程语义不再由 Metric 承载
> - Entity binding / carrier → 删除；物理接地由 Dataset/Field 行内表达
> - Lifecycle state machine → 删除；对象直接 CRUD

本文定义 Marivo semantic layer 中 `metric` 的目标 v2 Schema 草案。

本文是语义设计文档，不是当前实现说明，也不是最终 HTTP wire spec。它与以下文档配套：

- `specs/semantic/dimension-schema-contract.zh.md`
- `specs/semantic/metric-process-contract.zh.md`
- `specs/semantic/time-schema-contract.zh.md`
- `specs/semantic/ir-schema-contract.zh.md`

以下文档已废弃（SUPERSEDED），其内容已整合到 OSI Dataset-native 模型中：

- ~~`specs/semantic/predicate-schema-contract.zh.md`~~ → 过滤语义由 `Metric.filters` 承载
- ~~`specs/semantic/process-object-schema.zh.md`~~ → 过程语义不再作为独立类型
- ~~`specs/semantic/compiler-compatibility-profile.zh.md`~~ → 兼容性由 Dataset schema 直接校验

本文重点回答：

- `metric` 在 v2 中应承载哪些 typed semantics
- 哪些内容应保留在 compiler / IR，而不应继续塞进 `metric`
- metric 应如何声明 comparability、inferential capability、additivity 与 dataset compatibility
- metric 如何通过 MARIVO 扩展（`observed_dataset`、`observation_grain`、`primary_time_field`、`additivity`、`filters`）表达完整度量语义

## Purpose

本文用于为 `metric` 提供一套更稳定、更受治理的 typed contract，使其能够：

- 替代当前以 `definition_sql` 为外部主语义的设计方式
- 为 `observe` / `compare` / `test` / `validate` / `attribute` 等 intent 提供稳定输入
- 通过 `observed_dataset` / `observation_grain` / `primary_time_field` 声明数据源与粒度
- 通过 `dimension.*` 引用受治理的分析轴，而不是继续使用自由字符串
- 通过 `filters`（MARIVO 扩展）声明过滤语义
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
- 与 dataset 的组合边界可校验
- 与 intent 的兼容性可校验
- 底层 execution payload 可替换，但不改变外部 semantic identity

## 非目标

本设计不追求：

- 复制 SQL 的全部表达力
- 把 cohort / funnel / session / path 过程规则塞进 metric
- 用 metric schema 直接表达 compiler 的 DAG 或 SQL AST
- 允许所有 metric 在所有 intent 下无条件使用

## 核心概念

`metric` 回答的是：

- 最终量出的值是什么
- 它观测什么 Dataset（`observed_dataset`），以及以什么粒度稳定输出（`observation_grain`）
- 它属于哪类 sample
- 是否可比较、可检验、可分解
- 它需要哪些 measurement refs，以及通过 `filters`（MARIVO 扩展）声明哪些过滤语义

它不负责回答：

- treatment / control 总体如何构造
- funnel 步骤如何匹配
- session 如何切分
- cohort 如何物化
- 底层字段、过滤谓词、去重策略、SQL kernel 如何实现

这些由 compiler / IR 或独立的分析构造机制负责。

## Metric revision contract

Metric revision contract 用于解决同一业务语义的受治理修订问题。它不改变 metric public schema 的字段职责，而是定义 metric identity、revision lifecycle、默认解析和历史回溯的边界。

### Stable identity

`metric_ref` 是 metric 的 stable semantic identity，例如 `metric.avg_blocked_time`。下游 typed intent、compiler profile、artifact lineage 应继续引用这个 stable ref；若需要物理字段，应引用 Dataset Field，而不是依赖 metric 自己的 binding 或服务端内部 object id。

三层身份必须分开理解：

| 层级 | 示例 | 语义 |
| --- | --- | --- |
| Stable identity | `metric.avg_blocked_time` | 业务语义身份；同一语义的小修订保持不变 |
| Revision | `revision=2` | 该 identity 在某个时间点的冻结定义 |
| Object id | `metric_contract_id` | 服务端内部实例定位符；用于写入和生命周期操作 |

默认 resolution 使用 latest active revision。历史回溯必须显式携带 `metric_ref + revision`，不能用“当时的 latest”重新解释旧 artifact。

### Revision lifecycle

> **注意**：lifecycle state machine（draft → validated → active → deprecated）已删除。Metric 对象直接通过 CRUD 操作管理，不再需要显式状态机。

同一 `metric_ref` 可以拥有多个 revision。对象的创建、更新、删除直接通过标准 CRUD API 操作，无需经过显式的 validate / activate 生命周期阶段。

### Revision API 形态

Metric revision 的最小 HTTP API 形态如下。最终 wire spec 以 `docs/api/semantic.md` 为准，本文只冻结语义边界。

| Method | Path | 用途 |
| --- | --- | --- |
| `POST` | `/semantic/metrics` | 创建 metric |
| `GET` | `/semantic/metrics/{metric_ref}` | 读取 metric（返回最新定义） |
| `PATCH` | `/semantic/metrics/{metric_ref}` | 更新 metric（直接 CRUD，无需 lifecycle 状态机） |
| `DELETE` | `/semantic/metrics/{metric_ref}` | 删除 metric |
| `GET` | `/semantic/metrics/{metric_ref}/revisions` | 列出该 stable identity 的 revision history |
| `GET` | `/semantic/metrics/{metric_ref}/revisions/{revision}` | 显式读取历史 revision |

`GET /semantic/metrics/{metric_ref}` 默认返回 latest active revision。需要审计、回放、对比旧定义时，调用方必须使用显式 revision 读取接口。

`metric_id_or_ref` 用于写入入口，允许客户端从 detail read 返回的 object id 或 stable ref 进入 revision 流程；`metric_ref + revision` 是历史回溯和 artifact 解释的稳定定位方式。

### Revision create payload

v1 revision create 采用 full replacement payload，避免先引入 patch merge 语义。服务端可以在后续版本增加 patch 输入，但不得改变 full replacement 的确定性含义。

最小 payload：

```json
{
  "base_revision": 1,
  "change_summary": "Fix unit label in description from seconds to milliseconds.",
  "compatibility": "compatible",
  "replacement": {
    "header": {
      "metric_ref": "metric.avg_blocked_time",
      "display_name": "Average blocked time",
      "description": "Average blocked time in milliseconds.",
      "metric_contract_version": "v1"
    },
    "payload": {}
  }
}
```

字段约束：

- `base_revision` 必填，用于乐观并发校验；默认要求它等于当前 latest active revision。
- `change_summary` 必填，用于审计说明，不参与 runtime 编译。
- `compatibility` 必填，取值为 `compatible` 或 `breaking`。
- `replacement` 必填，表示完整 metric typed contract；其中 `metric_ref` 必须与被修订 identity 一致。

`compatible` 适用于 display name、description、unit label、文档化 semantics 文案等不改变 dataset grounding 和 compiler contract 的修订。`breaking` 适用于 observed dataset、observation grain、primary time field、sample kind、value semantics、additivity constraints 等会影响 dataset resolution 或编译结果的修订。

普通 spelling、description、unit label 修正不应通过 deprecate + recreate 或 `metric.*_v2` 表达。只有确实产生新的业务语义 identity 时，才创建新的 `metric_ref`。

### Artifact / step metadata 冻结

typed intent 执行时，compiler/runtime 必须把解析结果写入 semantic snapshot，而不是只保存输入中的 `metric_ref`。

最小冻结内容：

```json
{
  "metric_ref": "metric.avg_blocked_time",
  "resolved_metric_revision": 1,
  "resolved_metric_object_id": "metric_contract_...",
  "resolution_mode": "latest_active"
}
```

对于显式历史回放，`resolution_mode` 应表达为 `explicit_revision` 或等价值，并记录调用方指定的 revision。这样 revision 2 激活后，新 intent 默认使用 revision 2；旧 artifact 仍能通过 `metric_ref + resolved_metric_revision` 回溯 revision 1 的定义。

## 统一建模原则

### 1. metric 只表达 measurement contract，不表达过程构造

metric 负责”量什么”，过程构造由独立的分析构造机制或 compiler / IR 负责。

因此，像以下语义不应作为 metric 公共字段存在：

- `required_process_tags`
- `forbidden_process_tags`
- `attribution_basis = exposure / assignment`
- `attribution_window = 7 day`

更稳定的做法是让 metric 通过 `observed_dataset`、`observation_grain`、`primary_time_field`、`additivity`、`filters` 声明自己的 measurement 语义，过程前提通过 compiler 兼容性校验保证。

底层事实的 late arrival、incomplete-window 行为、外部锚点导入等物理消费细节，属于 Dataset 属性 / compiler policy，而不属于 metric 本体。

### 2. metric 公共 contract 不暴露执行 DSL

以下内容更适合放在 compiler normalization / IR / execution payload 中，而不是 public schema：

- 物理字段名
- 逻辑谓词树
- dedup key
- dedup strategy
- engine-specific percentile kernel
- approximate distinct / sketch 实现策略

public schema 应使用语义引用（semantic refs）表达“依赖哪个受治理概念”，而不是直接写“在哪个字段上执行什么逻辑”。

### 3. 观测 Dataset 与粒度需要分层建模

在 OSI 模型下，metric 应使用 Dataset-native 结构表达观测语义：

- `observed_dataset`：metric 直接消费或观测的 Dataset 引用
- `observation_grain`：metric 输出样本的稳定粒度
- `primary_time_field`：metric 主时间语义的 Field 引用

metric 的公共 contract 应优先表达：

- `observed_dataset`
- `observation_grain`
- `primary_time_field`

其中 `primary_time_field` 的统一语义应由 `time-schema-contract.zh.md` 定义；它表达的是 measurement 主时间，而不是 attribution basis 或 late-arrival policy。

### 4. 类型特定语义必须收敛到对应 payload

像 percentile、survival、score 来源、rate 的样本结构等语义，不应一部分放在公共组件里，一部分再放在特定 payload 中。

否则会产生双重真相，给 compiler / validator 带来不必要歧义。

> **注意**：`metric_family` 已删除。Metric 类型通过 `sample_kind` / `value_semantics` / component 结构推导，不再需要显式 family 枚举。

## 通用 Schema

### 公共头部

所有 metric 都应具备一组稳定的公共字段。

```python
from typing import Literal, NotRequired, TypedDict


class MetricHeader(TypedDict):
    metric_ref: str
    display_name: NotRequired[str | None]
    description: NotRequired[str | None]
    observed_dataset: str  # OSI Dataset 引用，替代 observed_entity_ref
    observation_grain: str  # OSI grain 概念，替代 observation_grain_ref
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
    primary_time_field: NotRequired[str | None]  # OSI Field 引用，替代 primary_time_ref
    additivity_constraints: AdditivityConstraints
    filters: NotRequired[list[str] | None]  # MARIVO 扩展：过滤语义引用，替代 default_predicate_refs / qualifier_refs
    metric_contract_version: str


class AdditivityConstraints(TypedDict):
    dimension_policy: Literal["all", "subset", "none"]
    additive_dimensions: NotRequired[list[str]]
    time_axis_policy: Literal["additive", "non_additive"]
    notes: NotRequired[str]
```

`MetricHeader` 现在只保留 measurement 主 contract 中稳定且必须的字段。

**以下字段已删除，不再属于 metric schema：**

- `metric_family`：删除；Metric 类型通过 `sample_kind` / `value_semantics` / component 结构推导
- `default_predicate_refs`：删除；过滤语义由 `filters`（MARIVO 扩展）统一承载
- `population_subject_ref`：删除；主体语义由 `observed_dataset` 表达
- `observed_entity_ref`：替换为 `observed_dataset`（OSI Dataset 引用）
- `observation_grain_ref`：替换为 `observation_grain`（OSI grain 概念）
- `primary_time_ref`：替换为 `primary_time_field`（OSI Field 引用）

**以下字段不应进入 public metric schema，由 compiler 从核心字段推导：**

- `supported_intents`：由 `sample_kind` + `additivity_constraints` 推导
- `inferential_capabilities`：由 `sample_kind` 推导

**以下字段属于 compiler compatibility profile，不是 metric 主 schema：**

- `result_modes`、`comparability_group`、`freshness_tolerance`

### Capability 推导规则（compiler 负责）

| 推导能力 | 推导规则 |
|---------|---------|
| `supports_observe` | 始终为 true |
| `supports_compare` | `additivity_constraints` 存在 AND `primary_time_field` exists |
| `supports_test` | `sample_kind` in ["numeric", "rate", "binary"] |
| `supports_decompose` | `additivity_constraints.dimension_policy` in ["all", "subset"] |
| `supports_detect` | `primary_time_field` exists |
| `supports_validate` | `sample_kind` == "rate" AND `primary_time_field` exists |

### Dataset 兼容 profile（非 public metric schema）

若 metric 对 Dataset 有编译期结构要求，应通过独立的 compiler compatibility profile 声明，而不是作为 metric public contract 字段。

### 字段说明

| Field | Type | Required | 说明 |
| --- | --- | --- | --- |
| `metric_ref` | string | yes | metric 的稳定公共引用；必须使用 `metric.*`；也是 public contract 主标识 |
| `display_name` | string | no | 人类可读显示名 |
| `description` | string | no | 业务口径说明 |
| `observed_dataset` | string | yes | metric 直接消费或观测的 Dataset 引用；替代原 `observed_entity_ref` |
| `observation_grain` | string | yes | metric 输出样本的稳定粒度；替代原 `observation_grain_ref` |
| `sample_kind` | enum | yes | 样本类型 |
| `value_semantics` | enum | yes | 值语义 |
| `aggregation_scope` | enum | no | 聚合作用的主要层次 |
| `primary_time_field` | string | no | metric 主时间语义的 Field 引用；替代原 `primary_time_ref` |
| `additivity_constraints` | AdditivityConstraints | yes | 结构化加和性约束：维度可分解策略与时间轴策略 |
| `filters` | list of string | no | MARIVO 扩展：过滤语义引用列表；替代原 `default_predicate_refs` 与 `qualifier_refs` |
| `metric_contract_version` | string | yes | 合同版本 |

### Component required input slots

Metric component 直接使用 `input_field_ref` 引用 Dataset Field。
不同 `value_semantics` + `sample_kind` 组合需要的 component slot 如下；这些 slot
由 metric contract 声明业务消费角色，通过 Dataset Field 解析物理来源。

| value_semantics / sample_kind | required component slot |
| --- | --- |
| count | `count_target` |
| sum | `measure` |
| ratio (rate) | `numerator`, `denominator` |
| mean (numeric) | `numerator`, `denominator` |
| distribution_statistic | `value_component` |
| score | `score_source` |
| survival_probability | none |

### 设计说明

几个字段需要特别强调：

- `observed_dataset` 与 `observation_grain` 是 metric identity 的一部分
- `sample_kind` 直接决定可支持的 inferential summary contract
- `additivity_constraints` 直接影响 `compare -> decompose -> attribute` 的合法性：`dimension_policy` 决定维度可分解性，`time_axis_policy` 决定时间轴可加性
- 维度兼容、统计能力与 freshness 等规则若需要存在，应由 compiler compatibility profile / governance context 承担，而不是回流到 public metric header
- `filters`（MARIVO 扩展）统一承载过滤语义，替代了已删除的 `default_predicate_refs` 与 `qualifier_refs`

其中命名空间应保持统一：

- `observed_dataset` 使用 Dataset 引用
- `observation_grain` 使用 grain 引用
- `primary_time_field` 使用 Field 引用

## 公共子结构

### SemanticRef

metric 应优先使用 OSI Dataset / Field 引用体系。

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
```

`MeasurementComponent` 不再重复声明 `entity_ref`、`grain`、`time_ref`。这些 measurement identity 已由顶层 `MetricHeader` 统一给出。因此以下字段已从 `MeasurementComponent` 中移除：

- `grain`：已由 `observation_grain` 统一表达
- `field`：执行细节，不属于 public contract
- `predicate`：执行细节，不属于 public contract
- `dedup_key`：执行细节，不属于 public contract
- `dedup_strategy`：执行细节，不属于 public contract
- `qualifier_refs`：已删除；过滤语义由 Metric-level `filters`（MARIVO 扩展）统一承载

> **注意**：原 `qualifier_refs` / `default_predicate_refs` 的扁平化禁止规则已不再适用。过滤语义统一由 `Metric.filters` 表达，compiler 负责在 lineage 中独立保留各 component 的 filter 来源。

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
    count_target: MeasurementComponent
```

### 示例

```json
{
  "metric_ref": "metric.dau",
  "display_name": "Daily Active Users",
  "observed_dataset": "dataset.user_activity",
  "observation_grain": "grain.user",
  "sample_kind": "numeric",
  "value_semantics": "count",
  "aggregation_scope": "subject",
  "primary_time_field": "dataset.user_activity.field.activity_time",
  "additivity_constraints": {
    "dimension_policy": "none",
    "time_axis_policy": "non_additive"
  },
  "filters": ["filter.is_active"],
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
    measure: MeasurementComponent
```

### 示例

```json
{
  "metric_ref": "metric.gmv",
  "display_name": "GMV",
  "observed_dataset": "dataset.order",
  "observation_grain": "grain.order",
  "sample_kind": "numeric",
  "value_semantics": "sum",
  "aggregation_scope": "event",
  "primary_time_field": "dataset.order.field.payment_time",
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
    numerator: MeasurementComponent
    denominator: MeasurementComponent
    default_test_method: NotRequired[Literal["two_proportion_z", "auto"] | None]
```

### 字段说明

- `numerator` / `denominator` 是 rate metric identity 的重要组成部分
- rate metric 不应在公共 contract 中固化 assignment / exposure / attribution window 这类过程语义
- 若 rate metric 依赖实验或归因窗口，窗口本体仍应来自独立的分析构造机制；metric contract 只声明 component 消费角色，具体字段通过 `input_field_ref` 指向 Dataset Field
- `default_test_method` 仅声明默认检验建议，不替代 runtime validation

### 示例

```json
{
  "metric_ref": "metric.conversion_rate",
  "display_name": "Conversion Rate",
  "observed_dataset": "dataset.user",
  "observation_grain": "grain.user",
  "sample_kind": "rate",
  "value_semantics": "ratio",
  "aggregation_scope": "subject",
  "primary_time_field": "dataset.user.field.conversion_time",
  "additivity_constraints": {
    "dimension_policy": "none",
    "time_axis_policy": "non_additive"
  },
  "filters": ["filter.converted"],
  "metric_contract_version": "v1",
  "numerator": {
    "name": "converted_users",
    "semantics": "users who converted",
    "aggregation": "count_distinct",
    "measure_ref": "measure.converted_user"
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

## Type 4: Average Metric

`average_metric` 用于表达均值类指标，例如：

- `avg_watch_time`
- `avg_order_value`

### Schema

```python
from typing import Literal


class AverageMetric(MetricHeader):
    numerator: MeasurementComponent
    denominator: MeasurementComponent
```

### 设计说明

average metric 与 rate metric 类似，都有 numerator / denominator 结构，但：

- `sample_kind` 通常是 `numeric`
- `value_semantics` 通常是 `mean`
- inferential summary 多使用 `numeric_sample_summary`
- 如果它依赖 session 粒度，应通过 `observed_dataset` / `observation_grain` 明确表达

### 示例

```json
{
  "metric_ref": "metric.avg_watch_time",
  "display_name": "Average Watch Time",
  "observed_dataset": "dataset.session",
  "observation_grain": "grain.session",
  "sample_kind": "numeric",
  "value_semantics": "mean",
  "aggregation_scope": "session",
  "primary_time_field": "dataset.session.field.session_start_time",
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
  "observed_dataset": "dataset.request_event",
  "observation_grain": "grain.event",
  "sample_kind": "numeric",
  "value_semantics": "distribution_statistic",
  "aggregation_scope": "event",
  "primary_time_field": "dataset.request_event.field.request_time",
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
  "observed_dataset": "dataset.user",
  "observation_grain": "grain.user",
  "sample_kind": "numeric",
  "value_semantics": "score",
  "aggregation_scope": "subject",
  "primary_time_field": "dataset.user.field.score_time",
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
- Dataset 能稳定提供 cohort / population / time anchor 相关信息
- event time 与 censor time 必须显式表达，不能只用布尔组件替代

### 示例

```json
{
  "metric_ref": "metric.time_to_churn",
  "display_name": "Time to Churn",
  "observed_dataset": "dataset.user",
  "observation_grain": "grain.user",
  "sample_kind": "survival",
  "value_semantics": "survival_probability",
  "aggregation_scope": "subject",
  "primary_time_field": "dataset.user.field.churn_eval_time",
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

## Dataset Compatibility 建议

metric 与 Dataset 的兼容性不应只在运行期碰运气。

建议基于以下维度显式校验：

- metric 的 `sample_kind`
- metric 的 `observed_dataset` / `observation_grain`
- Dataset 的 schema 是否提供 metric 所需的 Field
- 若存在 compiler compatibility profile，则其中的 dataset requirements

### 例子

- `conversion_rate` 这类实验指标通常要求 Dataset 提供 experiment split 上下文与稳定 time anchor
- `avg_watch_time` 这类会话质量指标通常要求 Dataset 暴露 session 粒度数据
- `time_to_churn` 这类 survival metric 通常要求 Dataset 提供稳定 population 与 time anchor
- `gmv` 未必适合直接绑定只输出 path match 数据的 Dataset

## Result Mode Compatibility 建议

若系统最终要持久化 result mode 兼容矩阵，建议把它放进 compiler compatibility profile，而不是回写到 `MetricHeader`。

例如：

- `sample_kind = rate` 的 metric 常支持 `standard` + `rate_sample_summary`
- `sample_kind = numeric` + `value_semantics = mean` 的 metric 常支持 `standard` + `numeric_sample_summary`
- `value_semantics = distribution_statistic` 的 metric 往往只支持 `standard`

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

- `sample_kind`、`value_semantics` 与 component 结构不冲突
- `observed_dataset`、`observation_grain` 与 value semantics 不冲突
- `sample_kind = rate` / `value_semantics = mean` 的 metric `numerator`、`denominator` 必填
- `distribution_spec.percentile` 在 `0 < p < 1`
- 所有 `*_ref` 均可解析
- `filters` 若存在，必须引用可解析的过滤语义对象

组合期还应额外校验：

- metric 与 Dataset 是否兼容
- metric 的 `additivity_constraints` 是否满足下游分析动作
- 请求维度若为 `time_derived`，其 `required_time_anchor` 是否被 metric 的 `primary_time_field` 满足
- 若存在 compiler profile，则其 result mode / inference 规则是否满足 `test` / `validate`
- 多 component metric 的 filter lineage 是否可分别解析、验证并保留

## 与 IR 的关系

metric schema 不是 IR。

例如：

- `numerator` / `denominator` 不是最终执行 DAG
- `additivity_constraints` 不是 compare artifact 本身
- compiler profile 不是最终 test 方法调度逻辑
- compiler profile 不是最终 dataset join / materialization plan；它只是对 dataset 结构的 typed 需求

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
- `MeasurementComponent.field`
- `MeasurementComponent.predicate`
- `MeasurementComponent.dedup_key`
- `MeasurementComponent.dedup_strategy`
- `MeasurementComponent.qualifier_refs`
- `MetricHeader.default_predicate_refs`
- `MetricHeader.metric_family`

### 第三阶段

补齐 dataset-aware validation，并把：

- comparability
- inferential readiness
- additivity_constraints
- result mode support
- dataset / grain compatibility

做成显式校验层。

### 第四阶段

再逐步纳入更复杂类型：

- distribution metric
- score metric
- survival metric

并统一其类型特定 payload，避免跨层重复表达。

## 总结

Metric v2 的目标不是让 semantic layer 再次退回 SQL registry，而是把业务度量真正提升为可校验、可治理、可组合的 measurement contract。

一套好的 metric schema 应做到：

- identity 清晰
- 统计语义清晰
- 与 Dataset 的边界清晰
- 与 intent 的兼容性清晰
- 与 execution payload 解耦

一句话总结：

> Marivo 的 metric v2 应以 OSI typed measurement contract 为中心，显式表达 observed dataset、observation grain、sample kind、type-specific measurement payload、additivity_constraints、comparability 与 filters（MARIVO 扩展），并把执行细节下沉到 compiler / IR。
