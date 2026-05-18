# Additivity 建模指导

本文指导如何在 Marivo 中为 metric 正确声明 `additive_dimensions` 与 `aggregation_semantics`。

## 核心概念

Marivo 使用扁平化的加法性模型替代了旧的 `additivity_constraints` 三态结构（`dimension_policy` + `time_axis_policy`）。当前模型由两个字段组成：

- **`additive_dimensions`**：`list[str]`，列出 metric 可加的维度字段名。空列表表示 metric 不在任何维度上可加；普通非空列表表示 metric 仅在列出的维度上可加；单元素列表 `["__all"]` 表示 metric 对 semantic model 中所有声明了 `dimension` 的字段可加，包括时间维度。`"__all"` 不得与显式维度名混用。
- **`aggregation_semantics`**：`"sum" | "ratio" | "weighted_average"`，声明 metric 的聚合语义。默认值为 `"sum"`。

**关键变化**：旧的 `dimension_policy`（`"all"` / `"subset"` / `"none"`）和 `time_axis_policy`（`"additive"` / `"non_additive"`）已被删除。能力推导直接基于 `additive_dimensions` 是否为空以及 `aggregation_semantics` 的值。

## 能力推导规则

`additive_dimensions` 和 `aggregation_semantics` 直接决定以下分析能力：

| 推导能力 | 推导规则 |
|---------|---------|
| `supports_observe` | 始终为 true |
| `supports_compare` | 始终为 true |
| `supports_decompose` | `additive_dimensions` 非空 |
| `supports_attribute` | `supports_compare` AND `supports_decompose` |
| `supports_detect` | process 的 `anchor_time_ref` 存在 |

当 `additive_dimensions` 为空时，`supports_decompose = false`，blocker 为 `ADDITIVITY_NONE`。

当 `additive_dimensions` 非空时，`supports_decompose = true`，但运行时需校验请求维度是否被 `additive_dimensions` 允许（`capability_condition = "dimension_must_be_allowed"`）。普通列表按成员关系校验；`["__all"]` 允许所有已声明维度，未知维度仍会被维度解析拒绝。

时间轴可加性由运行时通过 `time_scope.field` 是否被 `additive_dimensions` 允许来检查；`["__all"]` 表示已声明时间维度也可加。

## 何时 `additive_dimensions` 为空列表

适用于不可在任意维度上做归因分解的 metric。

特征：
- rate / ratio / average / percentile / score 类指标
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
  "additive_dimensions": [],
  "aggregation_semantics": "ratio"
}
```

## 何时 `additive_dimensions` 为非空列表

适用于某些或全部维度上可分解的 metric。

特征：
- 存在一组已知稳定、互斥、可分组的维度，按这些维度分组后，分组值之和等于整体值
- 典型场景：全加性指标、半加性指标、按特定维度可分解的指标

示例：
- `gmv`（全加性，可按任意维度分解）
- `order_count`（全加性）
- `inventory_balance`（按仓库可加，但按产品不可加）
- `dau_by_platform`（按平台可加的 DAU 变体，但按国家不可加）

声明（全加性）：

```json
{
  "additive_dimensions": ["__all"],
  "aggregation_semantics": "sum"
}
```

声明（半加性）：

```json
{
  "additive_dimensions": ["warehouse"],
  "aggregation_semantics": "sum"
}
```

## 维度可分解性 ≠ 时间轴可加性

这是最容易误解的点：

- **维度可分解**（`additive_dimensions` 非空）只表示"可以按列出的维度做归因"，不表示"可以跨天/周/月再累计"
- **时间轴可加**由运行时通过检查 `time_scope.field` 是否被 `additive_dimensions` 允许来判断，不是独立的 schema 声明

反例：
- 一个 metric 可能在某些维度上可分解，但时间字段不在 `additive_dimensions` 中（时间轴不可加）
- 一个 metric 可能时间字段在 `additive_dimensions` 中，但其他维度不在（仅时间轴可加，其他维度不可分解）

**不得从 `supports_decompose = true` 或 `supports_attribute = true` 推导出时间轴可加性。**

## DAU / UV 建模模式

### 模式 1：独立 DAU（最常见）

当无法确认存在一组稳定互斥且可分组的维度时：

```json
{
  "additive_dimensions": [],
  "aggregation_semantics": "sum"
}
```

此 metric 不支持 decompose / attribute。若业务需要解释 DAU 变化，应通过 observe + compare + 手动分析完成。

### 模式 2：按特定维度可分解的 DAU

当业务已确认存在一组稳定互斥且可分组的维度（如 platform、region 且这些维度之间无交叉）：

```json
{
  "additive_dimensions": ["platform", "region"],
  "aggregation_semantics": "sum"
}
```

此 metric 仅在 `platform` 和 `region` 上支持 decompose / attribute。

### 反模式：DAU 列出所有维度

**需谨慎。** `count_distinct` 聚合不满足任意维度分组后分组值之和等于整体值。仅当业务已确认特定维度上的可分解性时才应将其列入 `additive_dimensions`。

## count_distinct 规则

1. 对于 `count_distinct` 类聚合的 metric，`additive_dimensions` 应为空或仅包含已确认可分解的维度
2. 不得在未确认维度可分解性的情况下将维度列入 `additive_dimensions`

## `aggregation_semantics` 选择指南

- `"sum"`：可加连续值指标（如 revenue、latency、duration）— 使用 Welch's t-test
- `"ratio"`：比例/比率指标（如 conversion rate、click-through rate）— 使用 two-proportion z-test
- `"weighted_average"`：比率之和指标（如 AOV = SUM(revenue)/COUNT(orders)）— 使用 delta method 或 weighted-average decomposition

`aggregation_semantics` 决定 inferential summary mode 和支持的分析 intent。

## 建模决策流程

1. metric 的聚合语义是什么？
   - 可加连续值 → `aggregation_semantics = "sum"`
   - 比例/比率 → `aggregation_semantics = "ratio"`
   - 比率之和 → `aggregation_semantics = "weighted_average"`

2. metric 是否存在一组已知稳定互斥且可分组的维度？
   - 是 → 将这些维度列入 `additive_dimensions`
   - 否 → `additive_dimensions = []`

3. 时间字段是否应该可加？
   - 是 → 将时间字段名加入 `additive_dimensions`
   - 否 → 不加入

4. 对于 `count_distinct` / DAU / UV 类指标，除非已确认特定维度可分解，否则 `additive_dimensions` 应为空
