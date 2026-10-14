# Lazy Analysis DSL：多轮分析示例

Date: 2026-10-14

Status: proposed v0.25 examples；使用目标 API，不能直接在当前 Marivo 版本运行。
本文演示 [DSL 定义](lazy-analysis-dsl-redesign.md) 的组合方式，不另行定义算子合同。
执行与导出布局见 [执行、保存与结果交接](lazy-analysis-dsl-execution.md)，
独立数值 oracle 与待实施验收见 [场景验证](lazy-analysis-dsl-scenario-validation.md)。

## 1. 按问题选择入口

| 问题变化 | 表达 |
| --- | --- |
| 整周合计 / 每日收入 | observe 的 during=week / week.by(mv.grain("day")) |
| 固定名单计数或普通来源规约，包括已有时间角色的收入合计 | 可省略 during，表示不追加时间过滤；时间采样/fold 或日历输出仍须显式绑定 |
| 按地区拆分 / 全部来源只看亚太 | by=(region,) / where=region=="APAC" |
| 促销分子 / 全量分母 | 在单一来源分子上形成新固有过滤定义，再用 ms.ratio 组合 |
| 本期与基期采用不同窗口 | 分别 observe，随后 compare |
| 对旧答案选重点后追问明细 | 固定异常域 → 各观察端点 where/drill → 新 compare |
| 原指标合回总体 / 统计当前格子 | rollup / reduce |
| 保存固定名单 / 参照 | query(domain) 或 values Query 明确 execute |

execute 只保存已承诺的低成本精确信息，不接受保留请求；
需要细域、更早历史或其它窗口时，从来源 Query 重新构造并 execute。

## 2. 完整多轮分析示例

代码使用目标 v0.25 API，不是当前版本可运行示例。各块按顺序构成会话；
命名声明在模型，分析在Session。不变模拟输入用于手算，不要求真实来源跨读取不变。
首先粗比较再选异常；第二轮分别细化端点，不提前预知客户细键。

### 2.1 只声明 Orders Entity，客户 ID 是普通维度

```python
import marivo.datasource as md
import marivo.semantic as ms

Orders = ms.entity(
    name="orders",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders"),
    primary_key=["order_id"],
)
CustomerId = ms.dimension_column(
    name="customer_id",
    entity=Orders.ref,
    column="customer_id",
)
Region = ms.dimension_column(name="region", entity=Orders.ref, column="region")
Status = ms.dimension_column(name="status", entity=Orders.ref, column="status")
PaidAt = ms.time_dimension_column(
    name="paid_at",
    entity=Orders.ref,
    column="paid_at",
    granularity="second",
)
Amount = ms.measure_column(
    name="amount",
    entity=Orders.ref,
    column="amount",
    unit="CNY",
    additivity=ms.additive_all(),
)
Revenue = ms.aggregate(
    name="revenue",
    measure=Amount,
    agg="sum",
    time=PaidAt,
    empty="zero",
).where(Status == "paid")
OrderCount = ms.count(
    name="order_count",
    entity=Orders,
    time=PaidAt,
).where(Status == "paid")
AverageOrderValue = ms.ratio(
    name="average_order_value",
    numerator=Revenue,
    denominator=OrderCount,
    zero_denominator="null",
)
```

已存在sales domain与warehouse datasource；最终命名定义由loader收录。
只声明Orders，CustomerId不是Customers身份；同ID不同订单仍不同成员。
模拟订单均paid、金额和分类Defined、上海时间：

| order_id | customer_id | region | paid_at | amount |
| --- | --- | --- | --- | --- |
| A08 | A | East | 2026-08-10 12:00 | 50 |
| A09 | A | East | 2026-09-10 12:00 | 100 |
| B11…B19 | B | West | 2026-09-11…19 12:00 | 每笔10，共90 |

本模拟ledger覆盖完整，来源证据作为测试输入，不由日期范围、执行成功或日历推断。
customer_id→region不作全局函数依赖；需要同时分类时保留完整二元组。

```python
import marivo.analysis as mv
import marivo.semantic as ms

catalog = ms.load()
s = mv.session.get_or_create("buyer-analysis", report_timezone="Asia/Shanghai")
OrdersRef = ms.ref.entity("sales.orders")
customer_axis = catalog.fields.get(ms.ref.dimension("sales.customer_id"))
region_axis = catalog.fields.get(ms.ref.dimension("sales.region"))
status = catalog.fields.get(ms.ref.dimension("sales.status"))
paid_at = catalog.fields.get(ms.ref.time_dimension("sales.paid_at"))
amount = catalog.fields.get(ms.ref.measure("sales.amount"))
revenue = catalog.metrics.get(ms.ref.metric("sales.revenue"))
orders = catalog.metrics.get(ms.ref.metric("sales.order_count"))
aov = ms.ratio(numerator=revenue, denominator=orders)
august = s.time_scope(start="2026-08-01", end="2026-09-01")
september = s.time_scope(start="2026-09-01", end="2026-10-01")


def orders_in(period: mv.TimeScope) -> mv.Domain:
    return s.members(OrdersRef, where=paid_at.within(period) & (status == "paid"))


active_customer_ids = ms.aggregate(
    measure=customer_axis,
    agg="count_distinct",
    time=paid_at,
).where(status == "paid")
```

### 2.2 第一轮：先比较地区收入，选出异常地区

```python
region_keys = mv.coordinates(by=(region_axis,), keys=(("East",), ("West",)))
current_region_query = s.observe(
    {"revenue": revenue},
    during=september,
    by=region_keys,
)
baseline_region_query = s.observe(
    {"revenue": revenue},
    during=august,
    by=region_keys,
)
regional_comparison_result = current_region_query.compare(baseline_region_query).execute()
candidates = regional_comparison_result.where(
    regional_comparison_result["revenue.absolute"] >= 80
)
anomalous_regions = candidates.top(10, by=candidates["revenue.absolute"]).domain()
```

East为100/50、增量50；West为90/0、增量90，relative因零基期Undefined。
West基期零是完整观察中显式目标格的empty完成，不是outer补零。
固定异常域选择West，show(n)不能替代Top-N；此时未预知明细客户域。

### 2.3 第二轮：分别细化端点，再明确细域对应

```python
current_detail_query = current_region_query.where(mv.in_domain(anomalous_regions)).drill(
    add=(customer_axis,)
)
baseline_detail_query = baseline_region_query.where(mv.in_domain(anomalous_regions)).drill(
    add=(customer_axis,)
)
anomaly_details = current_detail_query.compare(
    baseline_detail_query,
    how="outer",
).execute()
anomaly_details.show()
```

在本例实际细域中，本期有West/B，基期没有该坐标：
current=90、baseline为missing_baseline Null，presence为True/False，
absolute/relative传播缺侧Null；不能将它改成空贡献零。
若选择exact会因细域缺侧失败，即使父级exact通过。
这条发现路径不需要硬编码B，新增维度在各端点检查，比较不运输来源图。

需要完整共同网格的另一个问题可以明确请求已知业务坐标：

```python
known_detail_keys = mv.coordinates(
    by=(region_axis, customer_axis),
    keys=(("West", "B"),),
)
grid_current = s.observe(
    {"revenue": revenue},
    during=september,
    where=mv.in_domain(anomalous_regions),
    by=known_detail_keys,
)
grid_baseline = s.observe(
    {"revenue": revenue},
    during=august,
    where=mv.in_domain(anomalous_regions),
    by=known_detail_keys,
)
complete_grid_details = grid_current.compare(grid_baseline).execute()
```

完整细域由原 observe 的 by=Domain 请求，drill 只列新增轴，不接受目标 Domain。
此明确网格两侧presence均True，90/0、差额90、relative Undefined由empty/zero政策产生。
它不替代前面的未知客户发现路径。目标域遗漏新增实际客户时按缺覆盖失败，不静默删除。
两条路径都以旧答案的West域限定新读取，不重判旧阈值。
West后来收入变70仍追问West，旧结果仍90；不回写、不对账，也不把新East加入。
直接对比较Query调用drill始终拒绝；Result不提供drill。

### 2.4 第三轮：固定客户 ID 值与订单身份分别保存

```python
cohort_result = s.query(orders_in(september).classify(by=(customer_axis,))).execute()
cohort_keys = cohort_result.domain()
cohort_orders = s.members(OrdersRef, where=mv.in_domain(cohort_keys))
fixed_september_orders = s.query(orders_in(september)).execute()
saved_order_classification = s.query(
    cohort_orders.classify(by=(mv.member(), customer_axis))
).execute()

requested_order_keys = mv.identities(
    OrdersRef,
    keys=(("A09",), ("B11",), ("O404",)),
)
requested_order_result = s.query(requested_order_keys).execute()
selected_order_result = s.query(
    s.members(OrdersRef, where=mv.in_domain(requested_order_keys))
).execute()
```

cohort_keys 固定普通 customer_id 字段值 A/B；cohort_orders 是订单身份域，可从新来源选到 11 个身份，
含 A08，不继承九月期间。fixed_september_orders 保存 10 个九月订单，恢复其 domain 不纳入新身份。
saved_order_classification 明确读取并保存订单身份与客户字段的对应坐标；独立读取不保证与收入同快照。
requested_order_result 保留 A09/B11/O404 三个类型化引用；来源选择 selected_order_result 只命中 A09/B11。
O404 不证明当前档案存在，后续必需属性缺失会失败；名单大小不证明真实客户/订单数。
复合键必须完整，customer_id 字段值不是 order_id 身份。固定域筛选只消费键，按新属性条件须明确读取。

### 2.5 第四轮：区分三个“平均”，终端统计交 pandas

```python
customer_query = s.observe(
    {"revenue": revenue, "orders": orders, "aov": aov},
    members=cohort_orders,
    during=september,
    by=cohort_keys,
)
customer_result = customer_query.execute()
customer_export = customer_result.to_pandas()
```

下段只处理已经明确得到的同一张客户表，不需要创建新的 DSL 统计图。
列名位于 value/state 名字空间；本例全部 Defined，先检查再计算。

<!-- pandas-example:customer-means -->
```python
names = ["revenue", "orders", "aov"]
if not customer_export["state"][names].eq("Defined").all().all():
    raise ValueError("The customer summary requires Defined input Cells.")
values = customer_export["value"][names]
overview = {
    "order_mean": values["revenue"].sum() / values["orders"].sum(),
    "mean_customer_aov": values["aov"].mean(),
    "spend_per_customer": values["revenue"].sum() / len(values),
    "weighted_customer_aov": (values["aov"] * values["orders"]).sum()
    / values["orders"].sum(),
}
```

依次为19、55、95、19；这四个终端数字本身不证明需要四个 DSL 算子。
输入单位分别是订单、客户 ID 格子或按订单数加权的格子，pandas 使用同一份已检查输入。
若输入含 Null，配对统计须显式选择共同有效样本，不能分别删值后拼接，零权重也不掩盖 Undefined。
纯语义可复用指标仍可用 aov.over(by=(customer_axis,), agg="mean")；
它的贡献推导内域不自动含未出现类别。固定A/B完整输出由本次observe负责，不能让pandas猜名单。
member_count若绑定cohort_orders则计11个订单身份，不变成两个客户。

### 2.6 第五轮：原指标上卷与普通描述统计

```python
original_total = customer_result.rollup().execute()
```

原指标上卷自动使用保存组件，得到190元/10单/AOV19，不让用户为每个指标重写合并规则。
普通当前格子分位数和状态计数则直接用导出表：

<!-- pandas-example:customer-statistics -->
```python
states = customer_export[("state", "aov")]
if not states.eq("Defined").all():
    raise ValueError("This descriptive example requires Defined AOV Cells.")
aov_values = customer_export[("value", "aov")]
cell_statistics = {
    "mean_aov": aov_values.mean(),
    "p90_aov": aov_values.quantile(0.9),
    "defined": int(states.eq("Defined").sum()),
    "rows": len(customer_export),
}
```

得到mean55、P90=91、Defined数2、行数2。这些是外部描述统计，不是原AOV的上卷。
只保存完成均值/比率或distinct数字仍不构成一般原指标上卷依据；
该信息下界对Marivo与pandas都成立。

### 2.7 第六轮：选中高消费 ID，再明确观察上期

```python
high_spend = customer_result.where(customer_result["revenue"] >= 100)
high_ids = high_spend.domain()
previous_high = s.observe(
    {"revenue": revenue},
    where=mv.in_domain(high_ids),
    during=august,
    by=high_ids,
).execute()
saved_high_orders = saved_order_classification.where(
    mv.in_domain(high_ids)
).domain().project(by=(mv.member(),))
```

high_ids 为字段值 A，上期收入 50；它只固定 ID 值，新订单可进入。
saved_high_orders 从已显式保存的订单身份/客户分类域筛选并投影，在本例是 A08/A09。
这不是从 customer_result 汇总恢复名单，也不证明订单分类与收入读取同快照；
若未保存这个分类域，就必须明确新读取或先保存指标的合法身份细域。

### 2.8 第七轮：固定 ID 域与重新选择 ID 域

```python
august_fixed_cells = s.observe(
    {"revenue": revenue},
    during=august,
    where=mv.in_domain(cohort_keys),
    by=cohort_keys,
).execute()
august_fixed_stats = august_fixed_cells.reduce(
    {"revenue": august_fixed_cells["revenue"].sum(), "ids": mv.row_count()}
)
august_fixed = august_fixed_stats.select(
    {"spend_per_customer": august_fixed_stats["revenue"] / august_fixed_stats["ids"]}
).execute()

august_key_result = s.query(orders_in(august).classify(by=(customer_axis,))).execute()
august_keys = august_key_result.domain()
august_reselected_cells = s.observe(
    {"revenue": revenue},
    during=august,
    where=mv.in_domain(august_keys),
    by=august_keys,
).execute()
august_reselected_stats = august_reselected_cells.reduce(
    {"revenue": august_reselected_cells["revenue"].sum(), "ids": mv.row_count()}
)
august_reselected = august_reselected_stats.select(
    {"spend_per_customer": august_reselected_stats["revenue"] / august_reselected_stats["ids"]}
).execute()

both = s.query(cohort_keys.intersection(august_keys)).execute()
either = s.query(cohort_keys.union(august_keys)).execute()
september_only = s.query(cohort_keys.difference(august_keys)).execute()
```

固定A/B得50/2=25，重新选择A得50/1=50，集合分别A、A/B、B。
固定域中的B空收入仍为0且计入分母，不能先过滤无贡献格。
动态名单需本次固定时先execute；逻辑域多处消费不保证同一组键。
域、原身份与新观察独立，来源后续删除B不改变固定差集。

### 2.9 第八轮：贡献份额与换分母

```python
regional = s.observe(
    {"revenue": revenue},
    during=september,
    by=(region_axis,),
).execute()
shares = regional.share("revenue")
east = shares.where(shares.coord(region_axis) == "East").execute()
east_only = regional.where(regional.coord(region_axis) == "East")
east_renormalized = east_only.share("revenue").execute()
```

share 保留 revenue 并新增 revenue.share。默认总体是调用处的当前输入：
先算份额再选 East 保留原分母190，得10/19；先选 East 再算份额使用分母100，得1。
需要筛选前总体时先 share 再 where/top。分组总体通过 partition_by 声明，见
[电商组内份额示例](lazy-analysis-dsl-scenario-validation.md#24-默认总体分组占比与筛选顺序)。
固定参照不因后续筛选自动重算。独立新明细除旧总额只作普通比值，不获得贡献包含证明。

### 2.10 第九轮：v0.25 不保存端点细状态

v0.25 不提供 execute 保留请求、Result.drill 或固定离线细比较。
需要细值时，从来源 Query 重新构造：按 §2.3 在各端点 Query 上 where/drill 后 compare，
或在原 observe 的 by 中直接请求细轴。§2.6 的 original_total 只保存 190/10/AOV19，
不能从这三个数字区分不同细分配，也不能凭 lineage 回源恢复细值。

### 2.11 第十轮：完整客户 ID 日网格与坐标复用

```python
days = september.by(mv.grain("day"))
daily_grid = mv.product(cohort_keys, days)
daily_query = s.observe(
    {"revenue": revenue, "orders": orders, "active_ids": active_customer_ids},
    during=days,
    where=mv.in_domain(cohort_keys),
    by=daily_grid,
)
daily = daily_query.execute()
daily_total = daily_query.select({"active_ids": daily_query["active_ids"]}).rollup().execute()
saved_days = s.query(daily.domain().project(by=(days,))).execute()
```

A/B×30日共60格，10个有贡献、50个已完整观察的空贡献格；
输出存在与来源覆盖不同。日去重计数和为10，月精确去重为2。
Result 默认不保存精确 distinct 集合，因此在来源 Query 上 rollup 重新求值；日计数本身不充分。
project日期得到30个完整日，不按量Defined删桶。
显式Null字段组、空域/标量空规约及成员复合键仍按 [DSL §4.5](lazy-analysis-dsl-redesign.md#45-坐标和目标域) 处理。

### 2.12 第十一轮：恢复固定答案，再明确新观察

```python
resumed = mv.session.resume(s.id)
restored_customers = resumed.result(customer_result.id)
restored_total = resumed.result(original_total.id)
fixed_ids = restored_customers.domain()
fresh = resumed.observe(
    {"revenue": catalog.metrics.get(ms.ref.metric("sales.revenue"))},
    during=september,
    where=mv.in_domain(fixed_ids),
    by=fixed_ids,
).execute()
ordinary_ratio = fresh.select(
    {"ratio": fresh["revenue"] / mv.broadcast(restored_total["revenue"], to=fresh)}
).execute()
fresh_shares = fresh.share("revenue").execute()

restored_comparison = resumed.result(regional_comparison_result.id)
restored_focus = restored_comparison.where(
    restored_comparison["revenue.absolute"] >= 80
).domain()
focus_current = resumed.observe(
    {"revenue": revenue},
    during=september,
    where=mv.in_domain(restored_focus),
    by=(region_axis, customer_axis),
)
focus_baseline = resumed.observe(
    {"revenue": revenue},
    during=august,
    where=mv.in_domain(restored_focus),
    by=(region_axis, customer_axis),
)
refreshed_fine_comparison = focus_current.compare(focus_baseline, how="outer").execute()
```

恢复 Result 只使用保存的键、值和轴绑定，不加载当前Catalog或读取属性；
轴使用原 Field 句柄或 domain().axes，fresh仅在明确新观察时使用新解析的Metric。
若新A收入120、B90，旧固定值仍100/90，总190；
普通跨读比值合计21/19，新份额从新总210形成4/7、3/7。
旧总额只作普通比值分母，不作为份额分母。
恢复的比较 Result 不能细化；从固定异常域明确构造新的本期/基期observe，在 by 中请求细轴后compare。
重新execute来源Query读取新值，恢复Result始终固定。

### 2.13 时间分析：七日收入、累计与期间比较

沿用[场景验证 §7](lazy-analysis-dsl-scenario-validation.md#7-时间分析滚动累计与期间比较)的模拟ledger：
10-01…08为100/1、900/99、0/0、40/1、50/1、60/1、70/1、80/1，
9月26…30每日10/1；截至本年1月1日后的其它模拟历史另有650元。
完整数值输入以场景验证为准，所有示例只作独立数值见证。

```python
temporal_s = mv.session.get_or_create("temporal-sales", report_timezone="Asia/Shanghai")
income = catalog.metrics.get(ms.ref.metric("sales.revenue"))
order_count = catalog.metrics.get(ms.ref.metric("sales.order_count"))
temporal_aov = ms.ratio(numerator=income, denominator=order_count, zero_denominator="null")
display = temporal_s.time_scope(start="2026-10-07", end="2026-10-09")
output_days = display.by(mv.grain("day"))
seven = mv.trailing_periods(output_days, count=7)

rolling = temporal_s.observe(
    {"income": income, "orders": order_count, "aov": temporal_aov},
    during=seven,
).execute()
elapsed = temporal_s.observe(
    {"income": income},
    during=mv.trailing_periods(display.by(mv.grain("hour")), count=168),
).execute()
mtd = temporal_s.observe(
    {"income": income},
    during=mv.to_date(output_days, grain=mv.grain("month")),
)
ytd = temporal_s.observe(
    {"income": income},
    during=mv.to_date(output_days, grain=mv.grain("year")),
)
since_january = temporal_s.observe(
    {"income": income},
    during=mv.since(output_days, start=temporal_s.instant("2026-01-01")),
)
cumulative_joined = mtd.align(ytd)
cumulative = cumulative_joined.select(
    {"mtd": cumulative_joined["left.income"], "ytd": cumulative_joined["right.income"]}
).execute()
```

各窗口分别 observe，默认信任声明的数据源；本例没有已知来源缺口，不需要额外完整性声明。
MTD/YTD用相同真实输出日历，所以exact可组合；since_january独立定义起点。
10-07/08七日收入1220/1200、订单104/104、AOV305/26与150/13，
MTD1220/1300、YTD1920/2000。显示仅两天但读取更早窗口。
elapsed 是小时粒度的 168 期回看，输出为小时格；绝对时长回看不另设入口。
v0.25 不提供父期间内日序配对；上月同进度改为分别对 10-07…08 与 09-07…08 求月累计，
再 compare(match=mv.ordinal()) 按位置配对，上月610/650，环比100%；
上年七日用 compare(match=mv.previous_year())，305/300，同比300%。
期间对应的数值见证见 [场景验证 §7.3](lazy-analysis-dsl-scenario-validation.md#73-同比环比先计算两个端点再按期间对应)。
同比不逐日搬贡献，七自然日不等于168小时；固有过滤/P仍限制窗口。

### 2.14 固定结果：原窗口续算与 pandas 日格子统计

```python
history = temporal_s.time_scope(start="2026-09-25", end="2026-10-09")
history_query = temporal_s.observe(
    {"income": income, "orders": order_count, "aov": temporal_aov},
    during=history.by(mv.grain("day")),
)
history_result = history_query.execute()
history_export = history_result.to_pandas()
```

Query/Result 不提供重设窗口；原七日指标由 §2.13 的 rolling 以 during=seven 重新 observe，
按原指标重求组件。以下改问日AOV的等权平均，交pandas；
示例明确采用浮点统计，导出本身不将Decimal或大整数强制转float。
本例只有一个日历轴、上海完整日序列，Null仅表示按已声明政策完成的空贡献日。
日期完整检查只验证已保存的日历坐标；默认来源信任不因此升级为完整性审计。
若原观察存在已知缺口，保存的 Unknown 必须由下面的状态检查拒绝，不能被跳过。

<!-- pandas-example:daily-aov -->
```python
import pandas as pd

(time_column,) = [
    axis["column"] for axis in history_export.attrs["marivo"]["axes"]
    if axis["kind"] == "calendar"
]
dates = pd.DatetimeIndex(history_export[("coordinate", time_column)])
expected = pd.date_range("2026-09-25", "2026-10-08", freq="D", tz="Asia/Shanghai")
if not dates.equals(expected):
    raise ValueError("Expected every saved daily coordinate in chronological order.")
states = history_export[("state", "aov")]
if not states.isin(["Defined", "Null"]).all():
    raise ValueError("Unknown or Undefined days cannot be skipped by rolling statistics.")
values = history_export[("value", "aov")].astype("Float64")
daily_values = pd.Series(values.array, index=dates)
mean_daily_aov = daily_values.rolling(7, min_periods=1).mean().loc["2026-10-07":"2026-10-08"]
```

日AOV等权均值为1810/33、1700/33，不是原七日AOV的305/26、150/13。
完整网格保证本例七行对应七个自然日；min_periods只控制已明确允许的Null样本，
不证明七日来源完整，也不允许缺日、Unknown或Undefined静默缩窗。
其它时区/桶宽须按其真实日历重新选择pandas窗口，不能把此段推广为168小时窗口。
固定的日结果不能换成七日原指标：日最终AOV、日P95或切穿桶的日收入不能制造缺失信息，
也不能回源补历史。普通pandas统计不因保存原Result ID而取得原指标窗口权限。

## 3. 时间锚点与版本选择

规则见 [DSL §4.1.1](lazy-analysis-dsl-redesign.md#411-精确时刻与版本选择)。

```python
anchor_s = mv.session.get_or_create("anchor-example", report_timezone="Asia/Shanghai")
october = anchor_s.time_scope(start="2026-10-01", end="2026-11-01")
launch = anchor_s.instant("2026-10-15T09:30:00")
absolute_launch = anchor_s.instant("2026-10-15T01:30:00+00:00")
since_launch = mv.since(
    anchor_s.time_scope(start="2026-10-16", end="2026-10-18").by(mv.grain("day")),
    start=launch,
)
new_york_scope = anchor_s.time_scope(
    start="2026-10-01", end="2026-11-01", timezone="America/New_York"
)
```

launch 与 absolute_launch 表示同一绝对时刻；since_launch 表达非日历起点，不能由 to_date 猜成月初。
假设已声明的客户历史在 11 月 1 日零点从等级 Silver 换成 Gold：
Field.at(october.end) 即 11 月 1 日零点，按 valid_from ≤ t < valid_to 选择 Gold；
v0.25 不提供边界左极限版本选择，十月内最后的 Silver 不可表达，也不用 end 减 tick 近似。
Session.members 的 at 与关系像所需的版本选择遵守同一种边界解释。
普通用户通过 Session.instant 和 scope 属性取得 InstantAnchor，不必手工构造锚点类型。

## 4. 关系像与有界逆像

规则见 [DSL §4.2.1](lazy-analysis-dsl-redesign.md#421-像逆像与已有坐标投影)。

下面假设已声明非版本化的 Orders、Customers 和 Orders → Customers 的 buyer 关系，
固定模拟来源只有 O1→A、O2→A、O3→B；customer_id 是订单上的普通字段。

```python
relation_s = mv.session.get_or_create("relation-example", report_timezone="Asia/Shanghai")
orders_ref = ms.ref.entity("sales.orders")
customers_ref = ms.ref.entity("sales.customers")
buyer = ms.ref.relationship("sales.buyer")
customer_id = catalog.fields.get(ms.ref.dimension("sales.customer_id"))
orders = relation_s.members(orders_ref)
customers_a = mv.identities(customers_ref, keys=(("A",),))
buyers = orders.image(via=(buyer,))
matched_orders = orders.preimage(customers_a, via=(buyer,))
customer_values = orders.classify(by=(customer_id,))
first_order = relation_s.members(
    orders_ref,
    where=mv.in_domain(mv.identities(orders_ref, keys=(("O1",),))),
)
expanded_orders = orders.preimage(first_order.image(via=(buyer,)), via=(buyer,))
relation_s.query(matched_orders).execute().show()
```

buyers 为客户身份 {A,B}，matched_orders 为订单身份 {O1,O2}；customer_values 只是字段值 {A,B}，
不能作为 preimage 的客户身份 targets。first_order 的客户像为 {A}，expanded_orders 却为 {O1,O2}。
若接收者换成九月订单域，preimage 只返回其中的匹配订单；若换成全量订单域，可包含这些客户的新订单。
模拟键值仅说明集合含义，不承诺共享来源表达式的多次读取相同；需要固定域时仍显式 query(domain).execute。

所有关系输入都使用同一种元组形状。下面另假设 Customers 上声明 customer_region 字段，
Orders 除 buyer 外还有另一条通向 Customers 的付款客户关系，语义层已声明 buyer 为贡献归属的唯一默认路径。
sales.revenue 是 Orders 上有业务时间角色的基础收入规约：

```python
customer_region = catalog.fields.get(ms.ref.dimension("sales.customer_region"))
buyer_region = customer_region.via((buyer,))
order_revenue = catalog.metrics.get(ms.ref.metric("sales.revenue"))
relation_october = relation_s.time_scope(start="2026-10-01", end="2026-11-01")
buyer_revenue = relation_s.observe(
    {"revenue": order_revenue},
    members=customers_a,
    during=relation_october,
)
```

buyer_region 的路径定位单值字段读取，贡献对客户身份域的归属由语义层声明的唯一路径决定；两者不能互相替代。
若语义层未声明唯一默认路径，同一调用在构造时拒绝，结构化错误列出贡献根、客户 Entity 与两条候选关系，
修复指向在语义层声明唯一路径或分别 observe；observe 不接受路由补全参数。
若需要多跳，元组按声明方向写为 (first_relationship, second_relationship)，仍须端点连续且满足消费者要求；
空元组不能表达同 Entity 的恒等路径。

## 5. 省略观察期间的合法边界

规则由 [DSL §4.1](lazy-analysis-dsl-redesign.md#41-session字段时间与路径) 拥有。
继续使用上节显式客户身份域；计该名单的身份数不需要订单时间范围：

```python
customer_count = relation_s.observe(
    {"customers": mv.member_count()},
    members=customers_a,
).execute()
customer_count_explicit = relation_s.observe(
    {"customers": mv.member_count()},
    members=customers_a,
    during=None,
).execute()
```

这两个请求均计名单中的一个身份，不读取客户档案确认存在性，不代表当期活跃客户数。
普通来源规约有无业务时间角色均可省略期间；已有字段/版本绑定仍须合法。

上节 order_revenue 有业务时间角色，但统计已选订单的总收入不需要再追加时间过滤：

```python
selected_revenue = relation_s.observe(
    {"revenue": order_revenue},
    members=matched_orders,
).execute()
selected_revenue_explicit = relation_s.observe(
    {"revenue": order_revenue},
    members=matched_orders,
    during=None,
).execute()

relation_paid_at = catalog.fields.get(ms.ref.time_dimension("sales.paid_at"))
october_revenue = relation_s.observe(
    {"revenue": order_revenue},
    where=relation_paid_at.within(relation_october),
).execute()
october_definition = order_revenue.where(relation_paid_at.within(relation_october))
intrinsic_october_revenue = relation_s.observe({"revenue": october_definition}).execute()
```

前两个请求的期间绑定等价；每次 execute 仍读取本次来源，不承诺独立读取的值相同。
后两个请求分别通过观察条件和固有条件限定十月，不需要重复传入 during；省略期间不证明来源包含完整历史。
把 member_count 与普通 order_revenue 放进同一次 observe 也可省略期间，显式身份域等要求保持。
任何依赖要求按期间采样或时间 fold 时，仍须显式提供 during，日期条件和属性时点不能代替它。
即使只观察 member_count，若 by 请求日历轴，也须显式给出相容 during，不能从 by 隐式推导。
