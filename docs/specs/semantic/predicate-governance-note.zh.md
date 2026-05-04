# Predicate 对象治理说明

> **过渡说明**：Predicate 作为独立类型已删除。过滤语义现在通过 `Metric.filters`（MARIVO 扩展）表达。本文保留作为治理参考，但过滤治理规则现在适用于 Metric.filters 而非独立的 Predicate 对象。主要变更如下：
> - `predicate.*` 独立类型 → 删除；过滤语义由 `Metric.filters` 承载
> - `carrier_row_filter` → Dataset-level row filter 或 Metric.filters
> - `CarrierBinding.row_filter_refs` → Dataset-level row filter（物理接地由 Dataset 行内表达）
> - Lifecycle state machine（draft → validated → active → deprecated）→ 删除；对象直接 CRUD
> - `qualifier_refs` / `default_predicate_refs` → Metric.filters
>
> 核心的过滤治理原则（必须建模 vs 不应建模、复用 vs 新建）仍然有效，但应用场景从 Predicate 对象转移到 Metric.filters。

本文是过滤语义的对象治理说明，回答四个治理问题：

1. 哪些过滤语义必须建模为受治理的 filter，哪些必须不在
2. 过滤语义的治理约束
3. 如何发现、引用和命名 filter
4. 何时需要新建 filter 语义 vs 复用已有 filter

正式 schema 定义见 [`predicate-schema-contract.zh.md`](./predicate-schema-contract.zh.md)（注意：Predicate 独立类型已删除，该文档仅保留作为参考）。v1 产品边界与范围冻结见 [`predicate-v1-scope-note.zh.md`](./predicate-v1-scope-note.zh.md)。

本文不定义：

- filter expression 的内部结构（见 `predicate-schema-contract.zh.md`）
- compiler 校验规则的实现细节（见 `compiler-spec.zh.md` 与 `app/analysis_core/predicate_validator.py`）
- API route shape（见 `docs/api/semantic.md`）

## 1. Authoring 边界

### 1.1 必须建模为受治理 filter 的过滤语义

以下过滤语义必须建模为受治理的 filter（通过 `Metric.filters`），不得在对象中内联 SQL 或局部 filter DSL：

- 软删除排除（如 `is_deleted = false`）
- 测试数据隔离（如 `is_test_data = false`）
- 租户防护（如 `tenant_id = 'production'`）
- metric 业务口径（如 `order_status = 'completed'`、`event_type = 'conversion'`）
- 人口学收窄（如 `dimension.country in ["CN"]`）

判断标准：如果某个过滤语义满足以下任一条件，则它必须建模为受治理的 filter：

- 需要跨对象复用（多个 metric 共享同一过滤口径）
- 需要参与 lineage 冻结（observation artifact 必须能重放该过滤语义）
- 需要被 compiler 做冲突检测或 narrowing 校验

### 1.2 不得建模为受治理 filter 的过滤语义

以下过滤语义不属于 Metric.filters，应通过各自的对象 contract 承载：

- 时间条件 → `time_scope` / `time.*`
- 窗口语义 → compiler / IR 或独立分析构造机制
- join 条件 → `JoinRelation.key_ref_pairs`
- 聚合逻辑 → `MeasurementComponent.aggregation`
- 物理字段映射 → Dataset Field / lowering

### 1.3 Usage 与消费点强制匹配

| 消费点 | 必须声明的 usage | 典型 filter |
|--------|-----------------|---------------|
| `Metric.filters` | `metric_filter` | `filter.converted`, `filter.effective_play` |
| Dataset-level row filter | `dataset_row_filter` | `filter.not_soft_deleted`, `filter.exclude_test_data` |
| `scope.predicate_ref` | `request_scope` | `filter.cn_platform_only` |
| governance context | `governance_policy` | `filter.tenant_guardrail` |

> **注意**：原 `MeasurementComponent.qualifier_refs`、`MetricHeader.default_predicate_refs`、`CarrierBinding.row_filter_refs` 已删除，统一由 `Metric.filters` 承载。原 `metric_qualifier` 和 `carrier_row_filter` usage 合并为 `metric_filter` 和 `dataset_row_filter`。

单个 filter 可以声明多个 usage，表示它可以被不同类型消费者安全复用。compiler 仍须校验每个消费者只使用声明了匹配 usage 的 filter。

## 2. 治理约束

> **注意**：Lifecycle state machine（draft → validated → active → deprecated）已删除。过滤语义对象直接通过 CRUD 操作管理，不再需要显式状态机。以下保留的治理约束直接适用于 Metric.filters。

### 2.1 创建前检查

Filter 创建时应执行以下检查：

1. `filter_ref` 以 `filter.` 开头
2. `subject_ref` 可解析
3. `expression` 非空
4. `allowed_usage` 非空
5. `time_policy` 在 v1 为 `non_time_only`
6. expression deterministic（无 `or`、`not`、`time.*` target、动态值）

### 2.2 引用约束

- Filter 引用的所有依赖必须可解析
- 已废弃的 filter 不允许被新创建的 metric 引用
- 已引用废弃 filter 的对象应标记为 readiness 降级

## 3. Catalog 使用约定

### 3.1 命名约定

| filter 类别 | 命名模式 | 示例 |
|---------------|---------|------|
| Dataset 不变量 | `filter.not_<exclusion>` / `filter.exclude_<exclusion>` | `filter.not_soft_deleted`, `filter.exclude_test_data` |
| metric 业务口径 | `filter.<business_concept>` | `filter.converted`, `filter.effective_play` |
| 请求级收窄 | `filter.<scope_concept>_only` | `filter.cn_platform_only` |
| 治理策略 | `filter.<policy_concept>` | `filter.tenant_guardrail` |

### 3.2 引用模式

- `Metric.filters`：`["filter.converted"]` — metric 级过滤语义
- Dataset-level row filter：`["filter.not_soft_deleted"]` — Dataset 不变量
- `scope.predicate_ref`：`"filter.cn_platform_only"` — 请求级临时收窄

### 3.3 发现与复用

新建 filter 前应：

1. 搜索 catalog 中是否已有相同 `subject_ref` + 相同 expression 的 filter
2. 若已有，检查其 `allowed_usage` 是否包含所需消费场景
3. 若已有且 usage 匹配，直接引用，不新建
4. 若已有但 usage 不包含所需场景，评估是否可以扩展 `allowed_usage`

## 4. 决策标准：新建 vs 复用

### 4.1 必须复用已有 filter 的场景

- 完全相同的过滤语义已在另一个 filter 中表达
- 仅 `allowed_usage` 不包含所需场景，但 expression 和 subject 完全匹配

### 4.2 应新建 filter 的场景

- 过滤语义不同（不同的 target_ref、op 或 value）
- subject_ref 不同（约束不同语义主体）
- 复用会导致 usage 语义混淆（如 Dataset invariant 和 metric business 口径共用同一 filter）

### 4.3 禁止行为

- 不得创建"万能 filter"（一个 filter 声明所有四种 usage 且 expression 过于宽泛）
- 不得在 metric 中内联 SQL 或局部 filter DSL 替代 filter 引用
- 不得创建 `allowed_usage` 为空的 filter
- 不得让 request scope 引用声明 `governance_policy` 或 `dataset_row_filter` usage 的 filter

## 5. 与实现的对应

| 治理规则 | 实现层 |
|---------|--------|
| filter 创建检查 | `app/semantic_readiness/evaluators.py` |
| usage 强制匹配 | `app/analysis_core/predicate_validator.py` validate_predicate_contracts |
| narrowing 校验 | `app/analysis_core/predicate_validator.py` validate_request_scope |
| 冲突检测 | `app/analysis_core/predicate_validator.py` validate_predicate_conflicts |
| lowering 前置检查 | `app/analysis_core/predicate_validator.py` run_lowering_precheck |
| lineage 构建 | `app/analysis_core/predicate_validator.py` build_predicate_filter_lineage |
| API 路由 | `app/api/semantic.py` /semantic/predicates/* |
| catalog 搜索 | `app/semantic_runtime/catalog.py` _SEARCH_CONFIG |
