# Additivity Constraints 建模指导

本文指导如何在 Marivo 中为 metric 正确声明 `additivity_constraints`。

## 核心概念

`additivity_constraints` 替代了旧的三态 `additivity` 枚举，由两个独立策略轴组成：

- **`dimension_policy`**：控制维度上的可分解性（decompose / attribute 是否允许、在哪些维度上允许）
- **`time_axis_policy`**：控制时间轴上的可加性（是否允许跨 time bucket / period 直接聚合）

**这两个轴独立，不可互推。**

## 何时使用 `dimension_policy = "all"`

适用于确认为普通 sum / count 且不存在 distinct / window / 非线性 aggregation 的 metric。

特征：
- 聚合方式为 `sum` 或 `count`（不含 `count_distinct`）
- 按任意已声明维度分组后，分组值之和等于整体值
- 时间轴上也可直接相加

示例：
- `gmv`（sum of order amount）
- `event_count`（count of events）
- `order_count`（count of orders）

声明：

```json
{
  "dimension_policy": "all",
  "time_axis_policy": "additive"
}
```

## 何时使用 `dimension_policy = "subset"`

适用于某些维度上可分解但非全部的 metric。

特征：
- 存在一组已知稳定、互斥、可分组的维度，按这些维度分组后，分组值之和等于整体值
- 其他维度上不满足此条件
- 典型场景：半加性指标、按特定维度可分解的 count_distinct 指标

必须同时提供 `additive_dimensions` 列表。

示例：
- `inventory_balance`（按仓库可加，但按产品不可加）
- `dau_by_platform`（按平台可加的 DAU 变体，但按国家不可加）

声明：

```json
{
  "dimension_policy": "subset",
  "additive_dimensions": ["dimension.warehouse"],
  "time_axis_policy": "non_additive"
}
```

## 何时使用 `dimension_policy = "none"`

适用于不可在任意维度上做归因分解的 metric。

特征：
- rate / ratio / average / percentile / score / survival 类指标
- 未确认稳定互斥维度的 distinct / UV / DAU 类指标
- 任何分组值之和不等于整体值的指标

示例：
- `conversion_rate`
- `latency_p95`
- `dau`（作为独立指标，未确认可分解维度）
- `avg_watch_time`
- `retention_rate`

声明：

```json
{
  "dimension_policy": "none",
  "time_axis_policy": "non_additive"
}
```

## 维度可分解性 ≠ 时间轴可加性

这是最容易误解的点：

- **维度可分解**（`dimension_policy` 为 `all` 或 `subset`）只表示"可以按维度做归因"，不表示"可以跨天/周/月再累计"
- **时间轴可加**（`time_axis_policy` 为 `additive`）只表示"可以沿时间轴直接聚合"，不表示"对任意维度都可归因"

反例：
- 一个 metric 可能 `dimension_policy = "all"` 但 `time_axis_policy = "non_additive"`（如某个按维度可分解但时间窗口不均匀的指标）
- 一个 metric 可能 `time_axis_policy = "additive"` 但 `dimension_policy = "none"`（如某个时间轴可累加但维度不可分解的指标）

**不得从 `supports_decompose = true` 或 `supports_attribute = true` 推导出时间轴可加性。**

## DAU / UV 建模模式

### 模式 1：独立 DAU（最常见）

当无法确认存在一组稳定互斥且可分组的维度时：

```json
{
  "dimension_policy": "none",
  "time_axis_policy": "non_additive"
}
```

此 metric 不支持 decompose / attribute。若业务需要解释 DAU 变化，应通过 observe + compare + 手动分析完成。

### 模式 2：按特定维度可分解的 DAU

当业务已确认存在一组稳定互斥且可分组的维度（如 platform、region 且这些维度之间无交叉）：

```json
{
  "dimension_policy": "subset",
  "additive_dimensions": ["dimension.platform", "dimension.region"],
  "time_axis_policy": "non_additive"
}
```

此 metric 仅在 `dimension.platform` 和 `dimension.region` 上支持 decompose / attribute。

### 反模式：DAU 使用 `dimension_policy = "all"`

**绝不允许。** `count_distinct` 聚合不满足任意维度分组后分组值之和等于整体值。创建请求会被 Pydantic cross-validator 拒绝。

## count_distinct 规则

1. 任何 payload 中包含 `aggregation = "count_distinct"` 的 metric，不得使用 `dimension_policy = "all"`
2. 创建时 Pydantic validator 会拒绝此类组合
3. readiness evaluator 会产生 `ADDITIVITY_CONSTRAINTS_AGGREGATION_CONFLICT` blocker
4. 允许的组合：`dimension_policy = "none"` 或 `dimension_policy = "subset"`（需提供 `additive_dimensions`）

## 建模决策流程

1. metric 的聚合方式是否为 `sum` 或 `count`（不含 `count_distinct`）？
   - 是 → 考虑 `dimension_policy = "all"`
   - 否 → `dimension_policy = "none"` 或 `"subset"`

2. metric 是否存在一组已知稳定互斥且可分组的维度？
   - 是 → `dimension_policy = "subset"`，列出这些维度
   - 否 → `dimension_policy = "none"`

3. metric 在时间轴上是否可直接相加（如日值之和等于周值）？
   - 是 → `time_axis_policy = "additive"`
   - 否 → `time_axis_policy = "non_additive"`

4. 对于 `count_distinct` / DAU / UV 类指标，默认 `time_axis_policy = "non_additive"`
