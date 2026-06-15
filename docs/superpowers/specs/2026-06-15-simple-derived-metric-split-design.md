# 简单 / 派生指标拆分设计（Simple / Derived Metric Split）

日期：2026-06-15

状态：draft（2026-06-15 经五轮设计评审后重写，中文版）

关联文档：
`docs/specs/semantic/python-semantic-layer.md`、
`docs/specs/semantic/agent-semantic-layer-authoring-design.md`、
`docs/superpowers/specs/2026-06-02-incremental-semantic-authoring-design.md`、
`docs/superpowers/specs/2026-06-09-semantic-catalog-public-api-design.md`、
`docs/superpowers/specs/2026-06-12-semantic-analysis-interface-unification-design.md`

> 全文为中文设计说明，所有 API 名、字段名、类型名等代码工件保持英文。

## 修订说明

本版在初稿基础上吸收了五轮设计评审（对照 dbt MetricFlow / Cube），核心结论：

1. **不在简单指标上引入独立的 `aggregation=` 标签去“声明聚合再推可加性”。** 在“以 body 为执行真相”的模型里，再加一个聚合标签只会引入第二个真相、带来发散；dbt/Cube 能免费推导可加性，恰恰因为它们**没有 body**。
2. **“派生指标天生不可加”是错的。** 两个可加指标之和仍可加，可加性必须**按组合类型逐一推导**，而非钉死成一个值。
3. **可加性不是纯代数属性，它 = 代数天花板 × 业务本质。** 业务本质（流量 / 存量 / 强度量）无法从表达式推导，必须声明。
4. **业务本质是“列”的内在属性，应声明在 `measure` 维度上，而非在每个指标上重复。** marivo 已有 `DimensionKind.MEASURE`，且已用同一模式（`sample_interval` 在 `time_dimension`、`time_fold` 在 metric）。指标因此可继承本质、大幅简化。
5. **`additivity` 与 `fold` 不做扁平合并。** “合并”的目标（消除非法组合）对，但用单一 `fold` 标量就得用 `"sum"`/`"none"` 这类**操作/空值词冒充属性**，重蹈 `ms.sum()` 的“可加 vs 求和”歧义。正确做法：`additivity` 是**属性**（`additive`/`non_additive`/`semi_additive`），`fold` 是**操作**，结构性地装进 `semi_additive` 变体内——关系是包含，不是等同。

由此确定的取舍（在文末「待定项」一节列出便于否决）：

- `measure` 维度承载内在汇总本质：单一 `additivity`（`"additive"` / `"non_additive"` / `ms.semi_additive(over=, fold=)`）。
- 简单指标分两种**形态**（非两种 top-level 类型）：声明式 `ms.aggregate(...)` 与 ibis body `@ms.simple_metric(...)`。
- 派生指标本期落 `ratio` / `weighted_average` / `linear` 三种组合，预留 L2 ibis `expression`。
- `additivity` 为 kind 变体；半可加把 `fold` 装在变体内，**不再有 `fold="sum"`/`"none"` 与 `agg` 撞名的写法**。
- 本设计为**破坏性清理，不留任何兼容物**。

## 问题（Problem）

marivo 当前用一个 `MetricIR` + 一个 `DecompositionIR` 表达两类不同的指标：

- 实体支撑、带 Python/ibis body 的指标：`@ms.metric(..., decomposition=ms.sum())`
- 无 body、由其他指标组合的指标：`ms.derived_metric(..., decomposition=ms.ratio(...) | ms.weighted_average(...))`

这让 `DecompositionIR.kind` 背上了无关含义：

- `sum` 不是派生组合，它只是简单指标“加性归因形状”的一个标记。
- `ratio` / `weighted_average` 才是带组件引用的派生组合公式。

共用一个名字造成 agent 困惑：body 里已经写了 `orders.amount.sum()`，装饰器还要求 `decomposition=ms.sum()`，于是 agent 自然以为应有 `ms.count()` / `ms.max()` / `ms.mean()` 等对应 builder——尽管帮助文档反复说 decomposition 不是 SQL 聚合。

这不是文档问题：`decomposition.kind` 被写进 catalog 详情、就绪/证据决策、组件帧、指标帧元数据、delta 元数据、归因形状预测，把命名错误固化进了授权与分析两侧。

> 同一“`sum` 既像聚合又像属性”的歧义有强复发性：任何把 `"sum"` 当作“可加”标记的设计（包括一度考虑过的 `fold="sum"`）都会重蹈覆辙。本设计因此把**属性词**（`additive`/`non_additive`）与**操作词**（`sum`/`last`/`max`…）彻底分到不同字段、不同层。

评审中的两点观察让问题更清晰：

- `ms.sum()` 已经是**惰性标记**：分析层只对 `ratio` / `weighted_average` 分支，`"sum"` 是兜底基路。删除它不丢任何运行期信息。
- 现有 `MetricIR.additivity` 是 `... | None`（可选），其含糊的 `None` 态与冗余的 `decomposition=ms.sum()` 在重复表达同一件事且都没表达好。拆分正是把可加性显式化、并把这一类“业务本质”搬到正确归属（列）的时机。

## 业界对照（Industry Alignment）

成熟语义层如何组织相同概念（架构层面）：

| 关注点 | dbt MetricFlow | Cube | 本设计 |
|---|---|---|---|
| 聚合 | 声明在 **measure** 上（`agg: sum/count/avg/...`），measure 即执行（无独立 body） | 声明在 **measure** 上（`type: sum/avg/number/...`） | **不声明**；ibis body / 声明式 `agg` 是执行真相 |
| 可加性 | 由 `agg` 推导 + `non_additive_dimension` 表达半可加 | 由 measure `type` 推导 | **本质声明在 measure 维度**（3 桶）× **agg 代数天花板** → 指标处推导 |
| 组合指标 | `ratio` 独立类型；`derived` 为对其他指标的通用 `expr` | `type: number` 计算度量，`sql` 引用其他 measure | **L1 识别型**（`ratio`/`weighted_average`/`linear`）+ **L2 预留** ibis `expression` |
| 半可加 | `non_additive_dimension`（单根维度，`window_choice` min/max） | 经 `rolling_window` | 单根 status-time 轴 + `fold`（更全的折叠算子，装在 `semi_additive` 变体内），设计成可升 per-axis |

两条推动本版的结论：

1. **dbt/Cube 能免费推可加性，仅因聚合是其唯一真相（无 body）。** marivo 刻意用 ibis body 作为 Python 表达式轨道，再加 `aggregation=` 只会制造第二真相。故 marivo 把 body 留作唯一执行真相，并把可加性的**业务本质**安放到它本就归属的地方——`measure` 维度。
2. **dbt 的 `ratio`（封闭、特判）+ `derived`（通用 `expr`）是一个两层模型，** Cube 的“可加 measure 类型 + `type: number`”同形。marivo 的 ibis 原生版即 L1 识别型组合 + L2 ibis 表达式。封闭层存在的理由：分析层必须**知道**一个组合的可加性以及如何把 delta 归因到组件，而通用表达式两者都不免费提供。

## 核心模型（Core Model）

**统一规则：有 body → `@装饰器`；无 body → 裸调用。**

| 形态 | 构造方式 |
|---|---|
| 装饰器（有 body） | `@ms.simple_metric` / `@ms.dimension` / `@ms.time_dimension` |
| 裸调用（无 body） | `ms.aggregate` / `ms.relationship` / `ms.ratio` / `ms.weighted_average` / `ms.linear` |

`_BaseRef.__call__` 在误用 `@` 时抛出教学错误。

### 心智模型：两个家族

- **简单指标（simple）**：读取行并聚合。
- **派生指标（derived）**：组合其他指标。

> 一句话给 agent：**“简单指标读行，派生指标组合指标。”**

### 关键认识一：可加性 = 代数天花板 × 业务本质

可加性有两个正交分量：

| 分量 | 是什么 | 可推导？ | 归属 |
|---|---|---|---|
| **业务本质** | 被度量量是 **flow / stock / intensive** | 否，必须声明 | **`measure` 维度** |
| **代数天花板** | 聚合函数本身允许的最高可加性 | 是（前提：agg 已知或 body 显式声明） | **指标**（tier-1 的 `agg`；tier-2 的 `additivity`） |

业务本质三类（声明在 measure 维度的 `additivity` 上）：

| 本质 | `additivity` | 含义 | 例 |
|---|---|---|---|
| 流量 | `"additive"` | 任意轴可加 | revenue、packets |
| 存量 | `ms.semi_additive(over=, fold=)` | 非时间轴可加，时间轴折叠 → 必带 `over`（时间轴）+ `fold` | inventory、balance、储备带宽 |
| 强度量 | `"non_additive"` | 任何轴都不可加 → `sum` 应被拒，只能 avg/min/max | unit_price、temperature、行级 rate |

把本质安放到 measure 维度后，指标处只需 `agg`（tier-1）或 `additivity`（tier-2），**有效可加性由两者推导**。例：`avg(revenue)`——revenue 本质 `additive`，但 `agg=avg` 把它压成 `non_additive`：measure 给上限，agg 下压，指标推导。

### 关键认识二：聚合（agg）与折叠（fold）正交

- **`agg`**：在**非时间**行集合上的聚合（sum/count/min/max/avg/...）。
- **`fold`**：沿 status-time 轴的折叠，仅半可加时有意义（装在 `semi_additive` 变体内）。

二者是两件事。以“峰值带宽”为例：跨链路应**求和**（agg=sum），沿时间应**取峰值**（fold=max）。旧模型用单个 body `samples.bw.max()` 把两者揉在一起，实际取的是“全时全链路单点最大”，并非“峰值总带宽”——新模型把二者分开后语义反而更准。

> `agg="max"` 与 `fold="max"` 同名但不冲突：两者都是诚实的 max 操作，只是作用轴不同（行集 vs 时间）、字段不同。歧义只发生在用 `sum` 这类操作词去冒充“可加”属性时——本设计不这么做。

### 关键认识三：派生指标 = (compute, additivity, attribution) 三件事

封闭组合 builder 把三件打包；通用 ibis 表达式只给 compute。故派生分两层：

| 层 | 形态 | additivity | attribution | dbt/Cube 对应 |
|---|---|---|---|---|
| **L1 识别型** | 封闭 kind：`linear`(a±b…) / `ratio` / `weighted_average` | **逐 kind 推导** | kind 专属（`sum` / `ratio_mix` / `weighted_mix`） | dbt `ratio`；Cube 可加 measure |
| **L2 通用（预留）** | ibis body over 组件指标 | **声明** | 通用有限差分，或标注 decompose 受限 | dbt `derived(expr)`；Cube `type: number` |

L1 不会膨胀：识别集由“**独立且必要的归因语义**”界定，而非由 compute 能力界定——可归因形状就这几种（加性、比率、加权）。更复杂的（`a*b`、`a/(b+c)`、`case(...)`）走 L2，复用 ibis 而非扩 builder 菜单。

`weighted_average` 是“不能退化为对标量输出做逐点运算”的硬证据：它是 `Σ(value·weight)/Σ(weight)`，一个**跨分段行的 reduction**，需要对齐后的逐分段列，所以必须是识别型 kind。

**不从 ibis AST 反推 L1 形状当契约**：识别 ratio/weighted/linear 可作为**优化**（认出走闭式、认不出走通用归因），但**不可作为 decompose 正确性的契约**——那是 marivo 一贯回避的“AST 推断当契约”，且这里风险更高。

## 可加性与折叠（Additivity & Fold）

`additivity` 是**属性**（能否跨轴相加），`fold` 是**操作**（半可加时沿时间轴如何塌缩）。二者是不同种类的东西，关系是**包含**而非等同：把 `fold` 装进 `semi_additive` 变体里，既消除非法组合（`additive` 不可能带 fold），又不必用 `"sum"`/`"none"` 这类操作/空值词冒充属性。

```python
TimeFold = Literal["last", "first", "avg", "max", "min"] | tuple[Literal["quantile"], float]

@dataclass(frozen=True)
class SemiAdditive:
    over: str           # status_time_dimension —— 半可加所在时间轴
    fold: TimeFold      # 沿该轴的塌缩算子（绝不含 sum / none）

Additivity = Literal["additive", "non_additive"] | SemiAdditive
```

授权 builder：`ms.semi_additive(over=<time_dim>, fold="last")` 返回 `SemiAdditive`。因此 `additivity` 取 `"additive"` / `"non_additive"` / `ms.semi_additive(...)` 之一；`is_semi_additive = isinstance(additivity, SemiAdditive)`。这正是 agent-guide 偏好的“封闭 kind 变体优于可选字段大类”，且非法状态结构上不可表达（`fold` 不可能脱离 `semi_additive` 漂浮）。

`additivity` 的来源（按场景）：

| 场景 | `additivity` 怎么来 |
|---|---|
| measure 维度 | **声明**本质：`"additive"`(流量) / `"non_additive"`(强度量) / `ms.semi_additive(...)`(存量) |
| 简单指标 · tier-1 | 由 `downgrade(measure.additivity, agg)` **推导**；半可加时可用 `fold=` 覆盖塌缩算子 |
| 简单指标 · tier-2（body） | **声明** `additivity`（绕过 measure 抽象，无可继承） |
| 派生指标 | 由组件**传播**（见下） |

agg 对有效可加性的作用（tier-1 的 `downgrade`）：

| agg | 有效 additivity |
|---|---|
| `count` | `additive`（计行，measure 本质无关） |
| `sum` | = measure 本质（`additive`→`additive`；`semi_additive`→保留该变体；`non_additive`→**校验拒绝**） |
| `avg`、`median`、`percentile`、`count_distinct`、`min`、`max` | `non_additive` |

> 峰值/谷值（peak/trough）= 半可加 measure 上 `agg=sum`（跨空间）+ `fold=max/min`（沿时间覆盖），与 `min/max` agg 区分开。

派生指标的可加性传播：

- `ratio` → 恒 `non_additive`
- `weighted_average` → 恒 `non_additive`
- `linear` → 当且仅当所有组件 `additive` 时为 `additive`；任一组件为 semi/non 则保守降为 `non_additive`（派生指标自身没有 `over`/`fold`）

### 半可加的单轴模型（沿用，但留通式口）

当前定义“在指定 status-time 轴上半可加、其余维度可加”即 Kimball / dbt（`non_additive_dimension`）模型，是 95% 场景的标准做法，**保留**。需在文档写明它是**单根特殊轴 + 其余求和**的简化，覆盖不了三类：

1. 多根时间轴（as-of 日期 × 事件日期的快照）。
2. 非时间半可加（不应跨“场景/币种”求和）。
3. 均匀 min/max（空间也折叠而非求和，如全站最低温）。

为此 `SemiAdditive` 只含单根 `over`；将来可升级为多轴 `Mapping[axis, op]`（每轴一算子、默认 sum），单 `over`/`fold` 是其特例，扩展不破坏 IR。

## Measure 维度扩展

`@ms.dimension(kind="measure", ...)` 新增内在汇总本质字段 `additivity`（仅 `kind="measure"` 合法）：

```python
def dimension(
    *,
    name: str | None = None,
    entity: EntityRef | str,
    kind: Literal["categorical", "measure"] = "categorical",
    additivity: Additivity | None = None,                  # 仅 measure：additive / non_additive / ms.semi_additive(over, fold)
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], DimensionRef]: ...
```

校验规则：

- `additivity` 仅 `kind="measure"` 合法；categorical / time 维度不接受。
- `ms.semi_additive(over=, fold=)` 的 `over` / `fold` 由 builder 强制必填——半可加缺轴/缺折叠在 builder 层即被拦，无法构造出非法 `additivity`。
- 复用既有 `sample_interval` 机制：当 `semi_additive.over` 声明了 `sample_interval`，沿用既有“采样轴必须给折叠”的检查（[validator.py:795-817](marivo/semantic/validator.py:795) 迁移到此模型；现在“折叠”即 `SemiAdditive.fold`）。
- measure 维度的 body 仍走既有 base AST 白名单。

## 公开 API（Public API）

### 半可加 builder

```python
ms.semi_additive(over=<time_dim>, fold="last")     # -> SemiAdditive；over/fold 必填
```

`fold` 取 `TimeFold`（`"last"|"first"|"avg"|"max"|"min"|("quantile", q)`），**不含** `sum`/`none`。

### 简单指标 · 形态一：声明式聚合（tier-1，推荐主路）

对**已声明的 measure 维度**做聚合，继承其本质：

```python
def aggregate(
    *,
    measure: DimensionRef | str,                              # 必须是 kind="measure" 的维度
    agg: AggKind,                                             # sum/count/count_distinct/min/max/avg/median/percentile
    fold: TimeFold | None = None,                            # 仅半可加时作为塌缩算子覆盖；省略则继承 measure 默认
    name: str | None = None,
    unit: str | None = None,
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> MetricRef: ...
```

- 无 body、无需 AST 校验（measure 的 body 已校验）。
- `entities` 由 `measure` 所属实体推出。
- 有效 `additivity` 由 `downgrade(measure.additivity, agg)` 推导；半可加时可用 `fold=` 覆盖塌缩算子（`fold` 只接受时间算子，不会与 `agg` 的 `sum` 撞义）。
- `agg=sum` 作用于 `additivity="non_additive"` 的强度量 → **校验拒绝**（教 agent 改用 avg/min/max 或建模为 ratio）。

`AggKind`：`"sum" | "count" | "count_distinct" | "min" | "max" | "avg" | "median" | ("percentile", float)`。

### 简单指标 · 形态二：ibis body（tier-2，逃生舱）

需要行级/聚合前运算（`SUM(price*qty)`、条件聚合、`count(distinct …)` 复合）时用：

```python
def simple_metric(
    *,
    name: str | None = None,
    entities: list[EntityRef | str],
    additivity: Additivity,                                 # "additive" | "non_additive" | ms.semi_additive(over, fold)
    root_entity: EntityRef | str | None = None,
    fanout_policy: Literal["block", "aggregate_then_join"] = "block",
    unit: str | None = None,
    source_sql: str | None = None,
    source_dialect: str | None = None,
    domain: DomainRef | None = None,
    description: str | None = None,
    ai_context: AiContext | dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], MetricRef]: ...
```

- body 必须通过既有 base AST 白名单并在物化期返回 ibis 表达式；`ms.component()` 在 body 内非法。
- 绕过了 measure 抽象，故 `additivity` 必须显式声明（不继承）；半可加直接传 `ms.semi_additive(...)`，`over`/`fold` 随变体一并给出。
- `source_sql` / `source_dialect` 仅简单指标可用。

> 规则：**走 measure 维度则语义白送（tier-1）；走裸 body 则自负其责（tier-2）。** 这给了 tier-1 实打实的存在理由，并符合“一条主路 + 逃生舱”。

### 派生指标

```python
ms.ratio(name=, *, numerator=<metric>, denominator=<metric>, unit=None, domain=None, description=None, ai_context=None) -> MetricRef
ms.weighted_average(name=, *, value=<metric>, weight=<metric>, unit=None, ...) -> MetricRef
ms.linear(name=, *, add=[<metric>, ...], subtract=[<metric>, ...], unit=None, ...) -> MetricRef
# 未来 L2(有 body → 装饰器):@ms.expression(name=None, *, components=[...], additivity=..., ...)
```

规则：

- 派生指标无 `entities`、无 body、无 `source_sql`、无 `aggregation`/`measure`。
- 组件必须引用已声明指标。
- **无 `additivity`/`fold` 参数**：由组件传播。L2 落地时，`expression` 组合将携带必填 `additivity`（其 body 不可推导）。
- `weighted_average` 对外角色名即 `value` / `weight`，**不泄漏**内部别名（修正今天的 `value→numerator`）。
- `linear` 至少两个指标项，且至少一个 `add`。

> `ms.linear` 不是被删的 `ms.sum()` 的替代：`ms.sum()` 是单指标的简单指标标记；`ms.linear` 是多指标的**派生**组合。

### 完整示例

```python
# ---------- 1. measure 维度声明本质（一次） ----------
@ms.time_dimension(entity=inventory)
def snapshot_date(inventory):
    return inventory.as_of

@ms.dimension(kind="measure", entity=inventory,                       # 存量
              additivity=ms.semi_additive(over=snapshot_date, fold="last"))
def quantity(inventory):
    return inventory.qty

@ms.dimension(kind="measure", entity=orders, additivity="additive")   # 流量
def amount(orders):
    return orders.amount

@ms.dimension(kind="measure", entity=orders, additivity="non_additive")  # 强度量
def unit_price(orders):
    return orders.unit_price

# ---------- 2. 简单指标 · tier-1（继承本质，近乎零声明） ----------
revenue           = ms.aggregate(measure=amount, agg="sum")                 # additive
order_count       = ms.aggregate(measure=amount, agg="count")              # additive（计行）
ending_inventory  = ms.aggregate(measure=quantity, agg="sum")              # semi（继承 fold="last"）
average_inventory = ms.aggregate(measure=quantity, agg="sum", fold="avg")  # agg=跨仓求和, fold=沿时间取平均
avg_unit_price    = ms.aggregate(measure=unit_price, agg="avg")            # non_additive
# ms.aggregate(measure=unit_price, agg="sum")  -> 校验拒绝：强度量不可求和

# ---------- 3. 简单指标 · tier-2（逃生舱） ----------
@ms.simple_metric(entities=[orders], additivity="additive")   # 行级乘法后求和
def gmv(orders):
    return (orders.price * orders.qty).sum()

# agg×fold 正交：峰值总带宽 = 跨链路求和 × 沿时间取 max
@ms.dimension(kind="measure", entity=samples,
              additivity=ms.semi_additive(over=sample_time, fold="last"))
def bw(samples):
    return samples.bw
peak_bandwidth = ms.aggregate(measure=bw, agg="sum", fold="max")

# ---------- 4. 派生指标 ----------
aov           = ms.derived_metric(name="aov",
                  composition=ms.ratio(numerator=revenue, denominator=order_count))
total_revenue = ms.derived_metric(name="total_revenue",
                  composition=ms.linear(add=[product_revenue, service_revenue]))   # additive
net_revenue   = ms.derived_metric(name="net_revenue",
                  composition=ms.linear(add=[gross_revenue], subtract=[refunds]))  # additive
```

## IR 形态（IR Shape）

### 可加性、折叠与组合

```python
TimeFold = Literal["last", "first", "avg", "max", "min"] | tuple[Literal["quantile"], float]

@dataclass(frozen=True)
class SemiAdditive:
    over: str           # status_time_dimension
    fold: TimeFold

Additivity = Literal["additive", "non_additive"] | SemiAdditive

@dataclass(frozen=True)
class RatioComposition:
    numerator: str
    denominator: str
    kind: Literal["ratio"] = "ratio"

@dataclass(frozen=True)
class WeightedAverageComposition:
    value: str
    weight: str
    kind: Literal["weighted_average"] = "weighted_average"

@dataclass(frozen=True)
class LinearTerm:
    sign: Literal["+", "-"]
    metric: str

@dataclass(frozen=True)
class LinearComposition:
    terms: tuple[LinearTerm, ...]
    kind: Literal["linear"] = "linear"

# L2 预留，本设计不实现：
#   class ExpressionComposition:
#       components: tuple[str, ...]
#       body_ast_hash: str
#       kind: Literal["expression"] = "expression"

Composition = RatioComposition | WeightedAverageComposition | LinearComposition
```

### Measure 维度

```python
@dataclass(frozen=True)
class DimensionIR:
    ...
    kind: DimensionKind                       # categorical | measure | time
    additivity: Additivity | None = None      # 仅 measure：additive / non_additive / SemiAdditive(over, fold)
```

### 指标

```python
@dataclass(frozen=True)
class MetricIR:
    semantic_id: str
    domain: str
    name: str
    metric_type: Literal["simple", "derived"]

    # 授权输入（按形态互斥）：
    aggregation: AggKind | None               # tier-1 有值；tier-2/derived 为 None
    measure: str | None                       # tier-1 聚合的 measure 维度 ref
    composition: Composition | None           # derived 专属

    # 解析后的有效汇总语义（所有指标都有）：
    entities: tuple[str, ...]                 # simple 非空；derived 为空
    additivity: Additivity | None             # tier-1/derived 在 load 时解析(此前为 None);tier-2 直接声明。load 后恒为具体值。

    provenance: ProvenanceIR
    description: str | None
    ai_context: AiContextIR
    body_ast_hash: str                        # tier-2 为 body hash；derived 为组合结构 hash
    python_symbol: str
    location: SourceLocation
    root_entity: str | None = None
    fanout_policy: Literal["block", "aggregate_then_join"] = "block"
    unit: str | None = None

    def __post_init__(self) -> None:
        if self.metric_type == "simple":
            if not self.entities:
                raise ValueError(...)                      # 简单指标需要 entities
            if self.composition is not None:
                raise ValueError(...)                      # 简单指标无 composition
            # tier-1：aggregation+measure 二者皆有、无 body；tier-2：二者皆 None、有 body
            tier1 = self.aggregation is not None
            if tier1 != (self.measure is not None):
                raise ValueError(...)                      # aggregation 与 measure 同进同出
        else:  # derived
            if self.entities:
                raise ValueError(...)
            if self.composition is None:
                raise ValueError(...)
            if self.aggregation is not None or self.measure is not None:
                raise ValueError(...)
```

> `additivity` 单字段即承载三态：`"additive"` / `"non_additive"` / `SemiAdditive(over, fold)`。原先漂浮的 `time_fold` / `status_time_dimension` / 合并版 `fold` 字段全部**消除**——半可加的 `over`/`fold` 只存在于变体内。

不变式（`__post_init__` 强制，非仅文档）：

| 指标 | 形态 | `entities` | `aggregation`/`measure` | `composition` | body | `additivity` |
|---|---|---:|---|---|---|---|
| simple · tier-1 | 声明式 | 非空 | 皆有 | 无 | 无 | None → load 时推导 |
| simple · tier-2 | ibis body | 非空 | 皆无 | 无 | 有 | 声明 |
| derived · L1 | 组合 | 空 | 皆无 | 有 | 无 | None → load 时传播 |
| derived · L2（预留） | expression | 空 | 皆无 | `expression` | ibis body | 声明 |

- `metric_type` 是稳定判别符；**`is_derived` 从 IR/公开面删除**（连内部 compat property 也不留）。
- `metric_type` 当前是二值封闭枚举，但家族是开放概念；将来窗口/累计类指标会扩展它，枚举与 `Composition` union 均按可生长设计。
- 更彻底的 `SimpleMetricIR | DerivedMetricIR` tagged union 是更优形态，可后续采用；本设计用单类 + `__post_init__` 控制迁移面。

## Catalog 与 DTO 面

```python
class MetricDetails:
    metric_type: Literal["simple", "derived"]
    additivity: Additivity                                 # additive / non_additive / SemiAdditive(over, fold)
    aggregation: AggKind | None                            # tier-1 展示用
    measure: SemanticRef | None
    composition: Literal["ratio", "weighted_average", "linear"] | None
    components: tuple[tuple[str, SemanticRef], ...]         # role-keyed，仅公开名
    ...
```

规则：

- 简单指标 `composition=None`、`components=()`；tier-1 展示 `aggregation`/`measure`，tier-2 不展示。
- 派生指标展示 `composition` 与 role-keyed `components`；`weighted_average` 角色即 `value`/`weight`（无内部别名）；`linear` 按声明顺序展示带符号项。
- `additivity` 恒有值并展示；半可加时展示其 `over` / `fold`。
- **删除 `component_metrics` 第二入口**，role-keyed `components` 为唯一权威组件契约（一条主路）。
- catalog 列表/渲染显示 `type=simple|derived`、恒显示 `additivity`，仅派生显示 `composition`。

`DerivedMetricBrief.decomposition_kind` → `composition_kind`；`ComponentFact.decomposition_kind` 同理，若只是兼容事实则直接删除以免重引旧轴。

## 运行时与分析（Runtime & Analysis）

### 物化（Materialization）

`Materializer.metric(...)` 按 `metric_type` 分派：

- simple · tier-1：取 `measure` 维度表达式，套用 `agg` 在声明实体上的聚合。
- simple · tier-2：调用 body sidecar，要求返回 ibis 表达式。
- derived：按 `composition.kind` 递归物化组件并套用组合（`ratio`/`weighted_average` 沿用既有逻辑；`linear` 对齐组件输出后按带符号项加减）。

`metric_on(...)` 仍仅简单指标。

### 观测（Observe）

组件感知路径触发于 `metric_type == "derived"`。各 L1 kind 产出组件帧供 compare/decompose 归因：`ratio`→`numerator`/`denominator`；`weighted_average`→`value`/`weight`；`linear`→每个带符号项一个组件。简单指标恒走基路、不产组件帧。

帧元数据改用 composition 词汇：

```python
MetricFrameMeta.composition: dict[str, Any] | None
DeltaFrameMeta.composition: dict[str, Any] | None
ComponentFrameMeta.composition_kind: Literal["ratio", "weighted_average", "linear"]
```

### 对比与分解（Compare & Decompose）

- `ratio` → 以 denominator 为 mix 基 → 归因形状 `ratio_mix`
- `weighted_average` → 以 weight 为 mix 基 → `weighted_mix`
- `linear` → **加性归因** → 形状 `sum`：各组件贡献为其自身 delta（含符号），精确求和、无交互项（复用既有加性基路，但带显式组件引用）

`AttributionShape` 维持 `Literal["sum", "ratio_mix", "weighted_mix"]`。形状预测读 `composition["kind"]`：`ratio→ratio_mix`、`weighted_average→weighted_mix`、`linear→sum`。

**composition 与 decompose 的层次边界**：`composition` 是指标如何**构建**（语义层属性）；`decompose` 是把 delta 归因的**分析操作**（产出 `AttributionFrame`）。`AttributionFrame.attribution_kind` 可继续叫 `"decomposition"`（分析产物名）；描述派生公式的元数据一律用 `composition`。`ms.help("composition")` 必须写明此边界，避免近义词重引困惑。

## 证据、就绪与约束（Evidence, Readiness, Constraints）

- 简单指标自动记录有效 `metric_additivity`（及必要时按 body/agg hash 的 `metric_expression`）；measure 维度记录其内在 `measure_additivity`（本质）。
- 派生指标自动记录 `metric_composition`。
- **任何指标都不记录** `metric_decomposition=sum`。
- 就绪依赖：tier-1 依赖其 `measure` 维度（及实体）；tier-2 依赖实体；派生依赖各组合组件。
- 环检测覆盖派生组合引用（含 `linear` 项）。`linear` 的可加性传播在解析期完成。
- 约束：`invalid_decomposition` → `invalid_composition`；新增 `invalid_measure_aggregation`（如 `sum` 作用于强度量 measure）；`missing_metric_additivity` 仅简单指标；`invalid_component_body` 仅简单指标 AST。
- 帮助主题：`ms.help("simple_metric")` / `ms.help("derived_metric")` / `ms.help("composition")`（含边界说明）/ `ms.help("additivity")`（含 measure 本质与 `semi_additive` 的 `over`/`fold`，原 `time_fold` 帮助并入此处）。

## 文档与 Skill 更新

更新所有面向 agent 的指引：停止“每个指标都要 decomposition”的说法；讲清两家族、measure 本质继承、agg×fold 正交、body-vs-composition 选择。需改：

- `docs/specs/semantic/python-semantic-layer.md`
- `docs/specs/semantic/agent-semantic-layer-authoring-design.md`
- `docs/specs/semantic/stepwise-authoring-design.md`
- `docs/superpowers/specs/2026-06-02-incremental-semantic-authoring-design.md`
- `docs/superpowers/specs/2026-06-09-semantic-catalog-public-api-design.md`
- `marivo/skills/marivo-analysis/references/examples/_fixtures/tiny_semantic.py`
- 语义 help 数据与 import/help 测试

规范措辞：

- 简单指标：“读行并聚合；走 measure 维度声明式聚合（继承本质），或走 ibis body（自声明 `additivity`）。”
- 派生指标：“无 body、由其他指标经组合（`ratio`/`weighted_average`/`linear`）而成。”
- measure 本质：“量的内在可加性（流量/存量/强度量），声明在 measure 维度的 `additivity` 上，指标继承。”
- additivity：“`additive` / `non_additive` / `semi_additive(over, fold)`；`fold` 是属性内部的时间塌缩算子，绝不写成 `sum`/`none`。”

## 测试策略（Test Strategy）

先窄后宽：

1. **measure 维度**：`additivity` 仅 measure 合法（categorical/time 拒绝）；`ms.semi_additive(...)` 缺 `over`/`fold` 在 builder 层报错；构造不出非法 `additivity`。
2. **授权 · tier-1**：`ms.aggregate` 产 `metric_type="simple"`、`aggregation`/`measure` 同在、无 body；继承 measure 的 `additivity`；半可加时 `fold=` 覆盖塌缩算子生效；`sum(强度量)` 拒绝；`avg` 推 `non_additive`；`count` 恒 `additive`。
3. **授权 · tier-2**：`@ms.simple_metric` body 产无 `aggregation`/`measure`、有 body；`additivity` 必填且为 `Additivity` 变体；半可加传 `ms.semi_additive(...)`；body 内 `ms.component()` 拒绝。
4. **授权 · derived**：`ratio`/`weighted_average`/`linear` 正确建 `composition`；`weighted_average` 对外角色 `value`/`weight`（无 `numerator` 泄漏）；`linear` 少于两项拒绝；可加性传播（linear 全 additive→additive，混入 semi/non→non_additive）。
5. **IR 不变式**：`__post_init__` 拒绝越界形态（simple 带 composition、derived 带 entities、tier-1/tier-2 字段混搭等）。
6. **物化**：tier-1 走 measure 表达式；tier-2 走 body；derived 三 kind 递归；`metric_on()` 拒派生。
7. **观测/分解**：简单指标无组件帧；各 L1 kind 产组件帧并带 `composition_kind`；`linear` 加性归因（各组件贡献含符号、求和等于总 delta）；形状预测读 `composition`。
8. **证据/就绪**：无 `metric_decomposition=sum`；派生记录 `metric_composition`；measure 记录本质；依赖与环检测含组合组件。
9. **文档/帮助**：无 happy-path 用 `decomposition=ms.sum()`；帮助不再叫 agent 用 `ms.sum()` 计数；`ms.help("composition")` 含边界说明；无任何 `fold="sum"`/`"none"` 写法。

验证门：

```bash
make typecheck
make lint
make test
make examples-check
```

收尾陈旧引用扫描：

```bash
rg "decomposition=ms\\.sum|metric_decomposition|decomposition_kind|DecompositionIR|DecompositionBuilder|is_derived|time_fold|fold=\"(sum|none)\"" marivo tests docs
```

剩余命中须为归档历史文档或有意保留项。

## 迁移范围（Migration）

破坏性清理，**不留任何兼容物**：

| 旧 | 新 |
|---|---|
| `@ms.metric(..., decomposition=ms.sum(), additivity=...)` | tier-1 `ms.aggregate(measure=..., agg="sum")` 或 tier-2 `@ms.simple_metric(..., additivity=...)` body |
| 半可加指标的 `additivity="semi_additive"`+`status_time_dimension=`+`time_fold=` | 本质迁至 measure 维度：`additivity=ms.semi_additive(over=, fold=)`；指标仅在 tier-1 用 `fold=` 覆盖或 tier-2 直接声明 |
| `additivity` 三态枚举 + 独立 `time_fold` | 单一 `Additivity` 变体（`semi_additive` 内含 `over`/`fold`） |
| `ms.derived_metric(..., decomposition=ms.ratio(...))` | `ms.derived_metric(..., composition=ms.ratio(...))` |
| `ms.derived_metric(..., additivity=...)` | 删除（按 kind 传播） |
| `DecompositionIR` / `DecompositionBuilder` | `Composition` union + builders |
| `MetricDetails.decomposition` / `.component_metrics` | `.composition` / role-keyed `.components` |
| `ComponentFrameMeta.decomposition_kind` 等帧元 `decomposition` | `composition_kind` / `composition` |
| `is_derived` | `metric_type` |

明确删除的“旧世界让步”：

- `session/_load.py` 的 `decomposition→composition` loader upgrade —— 删，旧 session 失效/重生成。
- `ms.metric` 别名、`component_metrics` 第二入口、`value→numerator` 内部别名 —— 删。
- 不在任何地方保留 `additivity`+`time_fold` 双字段或 `fold="sum"`/`"none"` 旧形状 —— 统一为 `Additivity` 变体。

## 验收标准（Acceptance Criteria）

- 简单指标恰好两种形态：`ms.aggregate(measure, agg, fold?)` 与 `@ms.simple_metric(body, additivity)`。
- 派生指标恰好一个构造器：`ms.derived_metric(composition=...)`，接受 `ratio`/`weighted_average`/`linear`。
- 量的可加性**本质声明在 measure 维度**（3 态 `additivity`）；指标的有效可加性 = 本质 × agg 推导。
- `additivity` 为 kind 变体（`additive` / `non_additive` / `semi_additive(over, fold)`），`time_fold` 并入半可加变体；非法组合结构上不可表达，且**属性词不与 `agg` 的 `sum` 撞名**。
- `sum` 作用于强度量 measure 被拒绝。
- 简单指标 IR 不带组件/组合；派生指标 IR 不带 entities/body/source_sql/aggregation/measure。
- `__post_init__` 强制全部不变式。
- `ms.sum()` 退出指标组合面；无 `fold="sum"`/`"none"` 写法；`weighted_average` 无内部别名泄漏。
- ratio/weighted_average 的 observe/compare/decompose 行为在 `composition` 命名下保持；`linear` 加性归因。
- `Composition` union 与 `metric_type` 结构上可纳入未来 L2 `expression` 与窗口类指标，无需再次破坏 IR。
- `make typecheck`、`make lint`、`make test`、`make examples-check` 通过。

## 待定项（Open Decisions）

| # | 项 | 状态 / 推荐 |
|---|---|---|
| 1 | tier-1 构造名 | 暂定 `ms.aggregate(measure=, agg=)`；备选 `ms.measure_metric` / `ms.agg` |
| 2 | measure 本质粒度 | **已采纳**三态 `additivity`（`additive`/`non_additive`/`semi_additive`），可表达强度量并拒 `sum(unit_price)` |
| 3 | tier-1 封装 | **已采纳** simple 两形态（mental model 仍是两句话），非三 top-level type |
| 4 | additivity 形态 | **已采纳** kind 变体 + `ms.semi_additive(over, fold)` builder；不做 `fold` 扁平标量、无 `"sum"`/`"none"` 哨兵 |
| 5 | per-axis fold | 本期单 `over` 轴；`SemiAdditive` 设计成可升 `Mapping[axis, op]`，非时间半可加/多时间轴留作下一步 |
| 6 | L2 ibis `expression` | 本期仅预留 IR 形状，不实现 derived-body 白名单/通用归因器 |
