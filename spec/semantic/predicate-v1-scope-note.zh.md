# Predicate v1 产品边界与范围冻结说明

本文是 `predicate.*` v1 的正式产品边界说明。它回答"predicate 表达什么、明确不表达什么"，作为后续所有实施、校验与治理决策的权威参考。

正式 schema 定义见 [`predicate-schema-contract.zh.md`](./predicate-schema-contract.zh.md)。

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

v1 支持四种受治理消费场景：

- `metric_qualifier`：metric 业务谓词，通过 `qualifier_refs` / `default_predicate_refs` 消费
- `carrier_row_filter`：carrier 消费不变量，通过 `row_filter_refs` 消费
- `request_scope`：请求级临时收窄，通过 `scope.predicate` 消费
- `governance_policy`：治理/隐私/合规策略，通过 policy engine 消费

### 支持的 target_ref 前缀

`PredicateAtom.target_ref` 必须指向受治理的语义引用，允许的前缀包括：

- `dimension.`、`entity.`、`key.`、`enum.`、`subject.`、`population.`、`event.`、`field.`

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

- `time.`：时间条件不属于 predicate，由 `time_scope` 承担
- `metric.`、`process.`、`binding.`：这些是对象引用，不是过滤目标
- `grain.`、`measure.`、`compiler_profile.`：这些是结构引用，不是过滤目标
- `predicate.`：不允许递归谓词引用

### 不支持的功能

- SQL AST 或通用 filter DSL：predicate 不是 SQL 替代品
- UDF、子查询、engine-specific predicate 语法
- cohort / funnel / session / attribution 规则：这些属于 process object
- 请求级覆盖或放宽上游过滤：request scope 只能收窄
- 时间窗口、归因窗口、late-arrival policy：这些属于 `time_scope` 和 process contract
- 值域推导或语义兼容性自动推断：v1 只做结构层校验

## v1 边界原理

一句话总结：

> predicate 应声明"什么过滤语义被稳定复用"，而不是声明"最终 WHERE 子句应长什么样"。

v1 约束集是故意最小化的。扩展到 `or`、`not`、动态变量或时间条件需要正式的契约版本升级（`predicate.v2`），而不是向后兼容扩展。

## 与其他对象的职责边界

| 职责 | 归属 | 说明 |
|---|---|---|
| 过滤语义的稳定标识与复用 | `predicate.*` | 受治理的第一等过滤语义对象 |
| 时间窗口与时间轴语义 | `time_scope` / `time.*` | 唯一的时间范围入口 |
| carrier 消费不变量 | `CarrierBinding.row_filter_refs` | 引用 `predicate.*`，只表达数据卫生 |
| metric 业务口径 | `MeasurementComponent.qualifier_refs` | 引用 `predicate.*`，是 measurement identity 的一部分 |
| metric 共享默认过滤 | `MetricHeader.default_predicate_refs` | 引用 `predicate.*`，不替代 component lineage |
| 请求级临时收窄 | `scope.predicate` | 引用同一表达式 contract，只能收窄 |
| 治理/合规策略 | governance context / policy engine | 不可被调用方覆盖 |
| 物理列名与 SQL lowering | binding surfaces / execution layer | 不属于 predicate public contract |
