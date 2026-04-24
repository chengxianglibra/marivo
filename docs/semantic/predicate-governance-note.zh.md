# Predicate 对象治理说明

本文是 `predicate.*` 的对象治理说明，回答四个治理问题：

1. 哪些过滤语义必须建模为 `predicate.*`，哪些必须不在
2. predicate 的生命周期约束
3. 如何发现、引用和命名 predicate
4. 何时需要新建 predicate vs 复用已有 predicate

正式 schema 定义见 [`predicate-schema-contract.zh.md`](./predicate-schema-contract.zh.md)。v1 产品边界与范围冻结见 [`predicate-v1-scope-note.zh.md`](./predicate-v1-scope-note.zh.md)。

本文不定义：

- predicate expression 的内部结构（见 `predicate-schema-contract.zh.md`）
- compiler 校验规则的实现细节（见 `compiler-spec.zh.md` 与 `app/analysis_core/predicate_validator.py`）
- API route shape（见 `docs/api/semantic.md`）

## 1. Authoring 边界

### 1.1 必须建模为 `predicate.*` 的过滤语义

以下过滤语义必须建模为 `predicate.*`，不得在对象中内联 SQL 或局部 filter DSL：

- 软删除排除（如 `is_deleted = false`）
- 测试数据隔离（如 `is_test_data = false`）
- 租户防护（如 `tenant_id = 'production'`）
- metric 业务口径（如 `order_status = 'completed'`、`event_type = 'conversion'`）
- 人口学收窄（如 `dimension.country in ["CN"]`）

判断标准：如果某个过滤语义满足以下任一条件，则它必须建模为 `predicate.*`：

- 需要跨对象复用（多个 metric 或 binding 共享同一过滤口径）
- 需要参与 lineage 冻结（observation artifact 必须能重放该过滤语义）
- 需要被 compiler 做冲突检测或 narrowing 校验

### 1.2 不得建模为 `predicate.*` 的过滤语义

以下过滤语义不属于 `predicate.*`，应通过各自的对象 contract 承载：

- 时间条件 → `time_scope` / `time.*`
- 窗口语义 → process object / metric-process contract
- join 条件 → `JoinRelation.key_ref_pairs`
- 聚合逻辑 → `MeasurementComponent.aggregation`
- 物理字段映射 → binding surfaces / lowering

### 1.3 Usage 与消费点强制匹配

| 消费点 | 必须声明的 usage | 典型 predicate |
|--------|-----------------|---------------|
| `MeasurementComponent.qualifier_refs` | `metric_qualifier` | `predicate.converted`, `predicate.effective_play` |
| `MetricHeader.default_predicate_refs` | `metric_qualifier` | `predicate.metric_active_users_only` |
| `CarrierBinding.row_filter_refs` | `carrier_row_filter` | `predicate.not_soft_deleted`, `predicate.exclude_test_data` |
| `scope.predicate_ref` | `request_scope` | `predicate.cn_platform_only` |
| governance context | `governance_policy` | `predicate.tenant_guardrail` |

单个 predicate 可以在 `allowed_usage` 中声明多个 usage，表示它可以被不同类型消费者安全复用。compiler 仍须校验每个消费者只使用声明了匹配 usage 的 predicate。

## 2. 生命周期约束

### 2.1 生命周期状态

```
draft → validated → active → deprecated
```

- **draft**：创建后初始状态；可自由修改；不参与 compiler 校验
- **validated**：通过 validate action 检查；所有依赖已存在
- **active**：通过 activate action 激活；可被 metric/binding/scope 引用；依赖必须同时 active
- **deprecated**：已废弃；不再允许新引用；已有引用应逐步迁移

### 2.2 Validate 前置检查

Predicate validate action 执行以下检查（对应 `app/semantic_readiness/evaluators.py` PredicateReadinessEvaluator）：

1. `predicate_ref` 以 `predicate.` 开头
2. `subject_ref` 可解析
3. `expression` 非空
4. `allowed_usage` 非空
5. `time_policy` 在 v1 为 `non_time_only`
6. expression deterministic（无 `or`、`not`、`time.*` target、动态值）

### 2.3 Activate 前置检查

Predicate activate action 在 validate 检查基础上额外要求：

1. 当前状态为 `validated`
2. 所有依赖（`subject_ref`、expression 中的 `target_ref` 指向的对象）均为 `active` 状态

### 2.4 Deprecate 约束

- Deprecated predicate 不允许被新创建的 metric/binding/scope 引用
- 已引用 deprecated predicate 的对象应标记为 readiness 降级
- Deprecate 操作不可逆

## 3. Catalog 使用约定

### 3.1 命名约定

| predicate 类别 | 命名模式 | 示例 |
|---------------|---------|------|
| carrier 不变量 | `predicate.not_<exclusion>` / `predicate.exclude_<exclusion>` | `predicate.not_soft_deleted`, `predicate.exclude_test_data` |
| metric 业务口径 | `predicate.<business_concept>` | `predicate.converted`, `predicate.effective_play` |
| 请求级收窄 | `predicate.<scope_concept>_only` | `predicate.cn_platform_only` |
| 治理策略 | `predicate.<policy_concept>` | `predicate.tenant_guardrail` |

### 3.2 引用模式

- `qualifier_refs`：`["predicate.converted"]` — 组件级业务口径
- `default_predicate_refs`：`["predicate.metric_active_users_only"]` — 跨组件共享口径
- `row_filter_refs`：`["predicate.not_soft_deleted"]` — carrier 不变量
- `scope.predicate_ref`：`"predicate.cn_platform_only"` — 请求级临时收窄

### 3.3 发现与复用

新建 predicate 前应：

1. 搜索 catalog 中是否已有相同 `subject_ref` + 相同 expression 的 predicate
2. 若已有，检查其 `allowed_usage` 是否包含所需消费场景
3. 若已有且 usage 匹配，直接引用，不新建
4. 若已有但 usage 不包含所需场景，评估是否可以扩展 `allowed_usage`（扩展需 re-validate）

## 4. 决策标准：新建 vs 复用

### 4.1 必须复用已有 predicate 的场景

- 完全相同的过滤语义已在另一个 predicate 中表达
- 仅 `allowed_usage` 不包含所需场景，但 expression 和 subject 完全匹配

### 4.2 应新建 predicate 的场景

- 过滤语义不同（不同的 target_ref、op 或 value）
- subject_ref 不同（约束不同语义主体）
- 复用会导致 usage 语义混淆（如 carrier invariant 和 metric business 口径共用同一 predicate）

### 4.3 禁止行为

- 不得创建"万能 predicate"（一个 predicate 声明所有四种 usage 且 expression 过于宽泛）
- 不得在 metric/binding 中内联 SQL 或局部 filter DSL 替代 predicate 引用
- 不得创建 `allowed_usage` 为空的 predicate
- 不得让 request scope 引用声明 `governance_policy` 或 `carrier_row_filter` usage 的 predicate

## 5. 与实现的对应

| 治理规则 | 实现层 |
|---------|--------|
| validate 检查 | `app/semantic_readiness/evaluators.py` PredicateReadinessEvaluator |
| usage 强制匹配 | `app/analysis_core/predicate_validator.py` validate_predicate_contracts |
| narrowing 校验 | `app/analysis_core/predicate_validator.py` validate_request_scope |
| 冲突检测 | `app/analysis_core/predicate_validator.py` validate_predicate_conflicts |
| lowering 前置检查 | `app/analysis_core/predicate_validator.py` run_lowering_precheck |
| lineage 构建 | `app/analysis_core/predicate_validator.py` build_predicate_filter_lineage |
| API 路由 | `app/api/semantic.py` /semantic/predicates/* |
| catalog 搜索 | `app/semantic_runtime/catalog.py` _SEARCH_CONFIG |
