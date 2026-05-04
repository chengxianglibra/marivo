# Predicate v1 产品边界与范围冻结说明

> **过渡说明**：Predicate 作为独立类型已删除。过滤语义现在通过 `Metric.filters`（MARIVO 扩展）表达。本文保留作为范围参考，但所有 v1 范围定义现在适用于 Metric.filters 而非独立的 Predicate 对象。主要变更如下：
> - `predicate.*` 独立类型 → 删除；过滤语义由 `Metric.filters` 承载
> - `carrier_row_filter` usage → 删除；由 Dataset-level row filter 或 `dataset_row_filter` 替代
> - `metric_qualifier` usage → `metric_filter`（适用于 `Metric.filters`）
> - `qualifier_refs` / `default_predicate_refs` → Metric.filters
> - `process.` / `binding.` target_ref 前缀 → 不再支持；改用 `dataset.` / `field.` 前缀

本文是过滤语义 v1 的正式产品边界说明。它回答"过滤语义表达什么、明确不表达什么"，作为后续所有实施、校验与治理决策的权威参考。

正式 schema 定义见 [`predicate-schema-contract.zh.md`](./predicate-schema-contract.zh.md)（注意：Predicate 独立类型已删除，该文档仅保留作为参考）。

## v1 范围内

### 支持的表达式结构

- **原子谓词**（`PredicateAtom`）：`target_ref` + `op` + `value` 构成的单个过滤条件
- **合取表达式**（`PredicateConjunction`）：`op = "and"` 连接的一个或多个 `PredicateExpression`
- 顶层 `expression` 可以是 `PredicateAtom` 或 `PredicateConjunction`

### 支持的操作符

v1 白名单包含 11 个操作符：

- 比较类：`eq`、`neq`、`gt`、`gte`、`lt`、`lte`
- 范围/集合类：`between`、`in`、`not_in`
- 空值类：`is_null`、`is_not_null`

### 支持的 usage 类别

v1 支持三种受治理消费场景：

- `metric_filter`：metric 过滤语义，通过 `Metric.filters` 消费（替代原 `metric_qualifier`）
- `request_scope`：请求级临时收窄，通过 `scope.predicate` 消费
- `governance_policy`：治理/隐私/合规策略，通过 policy engine 消费

> **注意**：原 `carrier_row_filter` usage 已删除。Dataset 不变量的过滤由 Dataset-level row filter 承载，不再通过独立的 `CarrierBinding.row_filter_refs` 消费。原 `metric_qualifier` 已重命名为 `metric_filter`，统一通过 `Metric.filters` 消费。

### 支持的 target_ref 前缀

`PredicateAtom.target_ref` 必须指向受治理的语义引用，允许的前缀包括：

- `dimension.`、`dataset.`、`field.`、`key.`、`enum.`、`subject.`、`population.`、`event.`

### 支持的 time policy

v1 只支持 `non_time_only`：predicate 不表达任何时间条件，时间过滤由 `time_scope` 独立承担。

## v1 范围外

以下内容在 v1 中明确不支持：

### 不支持的表达式结构

- `or`（析取）：v1 不允许在任意位置使用析取操作
- `not`（否定）：v1 不允许在任意位置使用否定操作
- 动态变量：不允许引用运行时参数、会话变量或上下文注入
- 非确定性输入：不允许 `now()`、`today()`、随机函数、环境依赖表达式
- 嵌套谓词引用：`target_ref` 不允许指向 `predicate.*`

### 不支持的 target_ref 前缀

- `time.`：时间条件不属于 filter，由 `time_scope` 承担
- `metric.`、`process.`、`binding.`：这些是对象引用，不是过滤目标（`process.` / `binding.` 已废弃）
- `grain.`、`measure.`、`compiler_profile.`：这些是结构引用，不是过滤目标
- `predicate.`：不允许递归谓词引用
- `entity.`：已替换为 `dataset.`；原 `entity.*` 引用应迁移到 `dataset.*`

### 不支持的功能

- SQL AST 或通用 filter DSL：predicate 不是 SQL 替代品
- UDF、子查询、engine-specific predicate 语法
- cohort / funnel / session / attribution 规则：这些由 compiler / IR 或独立分析构造机制处理
- 请求级覆盖或放宽上游过滤：request scope 只能收窄
- 时间窗口、归因窗口、late-arrival policy：这些属于 `time_scope` 和 Dataset 属性
- 值域推导或语义兼容性自动推断：v1 只做结构层校验

## v1 边界原理

一句话总结：

> filter 应声明"什么过滤语义被稳定复用"，而不是声明"最终 WHERE 子句应长什么样"。

v1 约束集是故意最小化的。扩展到 `or`、`not`、动态变量或时间条件需要正式的契约版本升级，而不是向后兼容扩展。

## 与其他对象的职责边界

| 职责 | 归属 | 说明 |
|---|---|---|
| 过滤语义的稳定标识与复用 | `Metric.filters` | 受治理的过滤语义（MARIVO 扩展） |
| 时间窗口与时间轴语义 | `time_scope` / `time.*` | 唯一的时间范围入口 |
| Dataset 消费不变量 | Dataset-level row filter | Dataset 层行级过滤，表达数据卫生 |
| metric 过滤口径 | `Metric.filters` | 过滤语义是 measurement identity 的一部分 |
| 请求级临时收窄 | `scope.predicate` | 引用同一表达式 contract，只能收窄 |
| 治理/合规策略 | governance context / policy engine | 不可被调用方覆盖 |
| 物理列名与 SQL lowering | Dataset Field / execution layer | 不属于 filter public contract |
