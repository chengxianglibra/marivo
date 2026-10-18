# Lazy Analysis DSL：多轮分析示例

Date: 2026-10-17

Status: v0.37 组合示例。基于 `405bd006f8`，R3–R8 已完成约定的本地实施、常规门禁和独立业务验收；
不表示本文每个片段、近似方法或输入形状均已执行。新数值 Query 仅准入 DuckDB，远端路线仍需代码
扩展；完整 slow、远端 Runtime 和 release 验收未完成。本文示例不代替实际路线验收。
本文演示 [DSL 定义](lazy-analysis-dsl-redesign.md) 的组合方式，不另行定义算子合同。
执行与导出布局见 [执行、保存与结果交接](lazy-analysis-dsl-execution.md)，
独立数值 oracle 与验收判据见 [场景验证](lazy-analysis-dsl-scenario-validation.md)。

## 1. 按问题选择入口

| 问题变化 | 表达 |
| --- | --- |
| 整周合计 / 每日收入 | observe 的 during=week / week.by(mv.grain("day")) |
| 固定名单计数或普通来源规约，包括已有时间角色的收入合计 | 可省略 during，表示不追加时间过滤；时间采样/时间样本规约或日历输出仍须显式绑定 |
| 按地区拆分 / 全部来源只看亚太 | by=(region,) / where=region=="APAC" |
| 促销分子 / 全量分母 | 在单一来源分子上形成新固有过滤定义，再用 / 或 divide 组合 |
| 本期与基期采用不同窗口 | 分别 observe，随后 compare |
| 对旧答案选重点后追问明细 | pandas 选键 → 脚本显式构造新域 → 端点 where/drill → compare → execute |
| 原指标合回总体 / 统计当前格子 | rollup / reduce |
| 保存名单 / 参照作为产物 | query(population) 或 values Query 明确 execute，之后只能阅读/导出 |
| 预定义 / 临时 Metric | 同一计算表达式；@ms.metric 只声明稳定名称，模型收录另查持久依赖 |
| 运行时名单限定某个组件 / 完整内部样本 | Metric.where(in_population(P)) / Metric.per(P) 后规约，保留域归属与读取 |

execute 返回终端 Artifact，保存输出、必要描述与实际 Finding/Evidence；不携带 logic 或内部组件；
需要细域、更早历史或其它窗口时，从来源 Query 重新构造并 execute。
各示例共用[单一执行路线](lazy-analysis-dsl-execution.md#15-单一执行路线与终端读写)：Ibis 完成源端计算并返回 pandas，
本次不能下推的阶段使用 pandas 本地内核，PyArrow 只负责产物存储和读取。来源本身可以是 DuckDB，
但示例不选择本地引擎，也不把 Artifact 或导出表自动注册回 DSL；后续 pandas 配方属于调用方处理。
v0.35 已同步完整期间配对与恢复边界；v0.36 增补 §6.4 的原生 Ibis 与自动绑定示例；v0.37 增补 §6.5 五项能力对照；原有零分母数值政策保持，默认 AOV 与零基期比较产生普通 NA。
普通 observe 的 Finding 为空，读取与实际检查摘要仍按执行合同保存为 Evidence；
示例中的多来源组合只承诺已准入路线，导出精度也不替代来源转换资格。
fanout 的语义表达对照见 §7.7；示例不固定物理 join/aggregate 顺序，也不构成新增执行策略的验收。

## 2. 完整多轮分析示例

代码使用 v0.37 API；完整链条须具备相应已准入的来源、绑定与 Query 实现。§2 各块按顺序构成会话；
命名可在模型或分析代码中构造，loader 才收录模型。§6 单独对照两种使用位置。
不变模拟输入用于手算，不要求真实来源跨读取不变。
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
    entity=Orders,
    column="customer_id",
)
Region = ms.dimension_column(name="region", entity=Orders, column="region")
Status = ms.dimension_column(name="status", entity=Orders, column="status")
PaidAt = ms.time_dimension_column(
    name="paid_at",
    entity=Orders,
    column="paid_at",
    granularity="second",
)
Amount = ms.measure_column(
    name="amount",
    entity=Orders,
    column="amount",
    unit="CNY",
    additivity=ms.additive_all(),
)

finalized = ms.finalize()
Orders = finalized.resolve(Orders)
CustomerId = finalized.resolve(CustomerId)
Region = finalized.resolve(Region)
Status = finalized.resolve(Status)
PaidAt = finalized.resolve(PaidAt)
Amount = finalized.resolve(Amount)

@ms.metric()
def revenue():
    return Amount.sum(time=PaidAt, empty="zero").where(Status == "paid")


@ms.metric()
def order_count():
    return Orders.count(time=PaidAt).where(Status == "paid")


@ms.metric()
def average_order_value():
    return revenue / order_count
```

已存在唯一默认 sales domain 与 warehouse datasource；装饰器捕获函数局部名，loader 才收录为 sales.revenue 等完整身份。
只声明Orders，CustomerId不是Customers身份；同ID不同订单仍不同成员。
模拟订单均paid、金额和分类非缺失、上海时间：

| order_id | customer_id | region | paid_at | amount |
| --- | --- | --- | --- | --- |
| A08 | A | East | 2026-08-10 12:00 | 50 |
| A09 | A | East | 2026-09-10 12:00 | 100 |
| B11…B19 | B | West | 2026-09-11…19 12:00 | 每笔10，共90 |

本模拟 ledger 是数值核验所用的完整数据集；这只是测试数据设定，不是完整性声明入口，也不由日期范围、执行成功或日历推断。
customer_id→region不作全局函数依赖；需要同时分类时保留完整二元组。

```python
import marivo.analysis as mv
import marivo.semantic as ms

catalog = ms.load()
s = mv.session.get_or_create("buyer-analysis", report_timezone="Asia/Shanghai")
OrdersRef = ms.ref.entity("sales.orders")
OrdersEntity = catalog.entities.get(OrdersRef)
customer_axis = catalog.fields.get(ms.ref.dimension("sales.orders.customer_id"))
region_axis = catalog.fields.get(ms.ref.dimension("sales.orders.region"))
status = catalog.fields.get(ms.ref.dimension("sales.orders.status"))
paid_at = catalog.fields.get(ms.ref.time_dimension("sales.orders.paid_at"))
amount = catalog.fields.get(ms.ref.measure("sales.orders.amount"))
revenue = catalog.metrics.get(ms.ref.metric("sales.revenue"))
orders = catalog.metrics.get(ms.ref.metric("sales.order_count"))
aov = revenue / orders
august = s.time_scope(start="2026-08-01", end="2026-09-01")
september = s.time_scope(start="2026-09-01", end="2026-10-01")


def orders_in(period: mv.TimeScope) -> mv.Population:
    return s.members(OrdersRef, where=paid_at.within(period) & (status == "paid"))


active_customer_ids = customer_axis.count_distinct(time=paid_at).where(status == "paid")
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
regional_pair = current_region_query.align(baseline_region_query)
current_region_query.show()
regional_comparison_artifact = regional_pair.select({
    "revenue.absolute": regional_pair["left.revenue"] - regional_pair["right.revenue"],
}).execute()
regional_frame = regional_comparison_artifact.to_pandas()
(region_column,) = [
    axis["column"] for axis in regional_frame.attrs["marivo"]["axes"]
    if axis["kind"] == "field"
]
selected = regional_frame.loc[regional_frame[("value", "revenue.absolute")] >= 80]
selected_regions = tuple((value,) for value in selected[("coordinate", region_column)].tolist())
anomalous_regions = mv.coordinates(by=(region_axis,), keys=selected_regions)
```

East 为 100/50、增量 50；West 为 90/0、增量 90。这里明确只求差额；
默认 compare 会同时得到 West 的 absolute=90、relative=NA；显式 zero_denominator="error" 才因零基期使整次执行失败。
West 基期零来自显式目标格的 sum 完成规则；outer 缺侧仍为 NA，不自动补零。
pandas 从精确已见输出选中 West，脚本将其作为当前 region 的显式字面量域；此时未预知客户细键。
本例输出只有一个字段轴，因此按 field kind 单元素解构，再用稳定 column 标识读取；不依赖描述 label。
这不授予 Artifact DSL 能力。若使用 Query.where/top/population 在执行前组合，重新 execute 可能重选地区。

### 2.3 第二轮：分别细化端点，再明确细域对应

```python
current_detail_query = current_region_query.where(mv.in_population(anomalous_regions)).drill(
    add=(customer_axis,)
)
baseline_detail_query = baseline_region_query.where(mv.in_population(anomalous_regions)).drill(
    add=(customer_axis,)
)
anomaly_details = current_detail_query.compare(
    baseline_detail_query,
    how="outer",
).execute()
anomaly_details.show()
```

在本例实际细域中，本期有West/B，基期没有该坐标：
current=90、baseline 为 NA，presence 为 True/False；absolute/relative 为 NA。
缺侧信息由 presence 保存，不由 NA 的原因码表达。
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
    where=mv.in_population(anomalous_regions),
    by=known_detail_keys,
)
grid_baseline = s.observe(
    {"revenue": revenue},
    during=august,
    where=mv.in_population(anomalous_regions),
    by=known_detail_keys,
)
complete_grid_pair = grid_current.align(grid_baseline)
complete_grid_details = complete_grid_pair.select({
    "revenue.absolute": complete_grid_pair["left.revenue"] - complete_grid_pair["right.revenue"],
}).execute()
```

完整细域由原 observe 的 by=Population 请求，drill 只列新增轴，不接受目标 Population。
此明确网格两侧 presence 均 True，收入为 90/0、差额为 90；默认相对变化为 NA。
这与缺侧导致端点及差额均为 NA 不同；显式 error 的 compare 仍因零基期失败。
它不替代前面的未知客户发现路径。目标域遗漏新增实际客户时按缺覆盖失败，不静默删除。
两条路径都以旧答案的West域限定新读取，不重判旧阈值。
West后来收入变70仍追问West，旧结果仍90；不回写、不对账，也不把新East加入。
直接对比较Query调用drill始终拒绝；Artifact 不提供任何 DSL 方法。

### 2.4 第三轮：固定客户 ID 值与订单身份分别保存

```python
cohort_population = orders_in(september).classify(by=(customer_axis,))
cohort_population.show()
cohort_artifact = s.query(cohort_population).execute()
cohort_frame = cohort_artifact.to_pandas()
```

<!-- pandas-example:cohort-keys -->
```python
(customer_column,) = [
    axis["column"] for axis in cohort_frame.attrs["marivo"]["axes"]
    if axis["kind"] == "field"
]
customer_keys = tuple((value,) for value in cohort_frame[("coordinate", customer_column)].tolist())
```

```python
cohort_keys = mv.coordinates(
    by=(customer_axis,),
    keys=customer_keys,
)
cohort_orders = s.members(OrdersRef, where=mv.in_population(cohort_keys))
fixed_september_orders = s.query(orders_in(september)).execute()
saved_order_classification = s.query(
    cohort_orders.classify(by=(mv.member(), customer_axis))
).execute()

requested_order_keys = mv.identities(
    OrdersEntity,
    keys=(("A09",), ("B11",), ("O404",)),
)
requested_order_result = s.query(requested_order_keys).execute()
selected_order_result = s.query(
    s.members(OrdersRef, where=mv.in_population(requested_order_keys))
).execute()
```

cohort_keys 固定普通 customer_id 字段值 A/B；cohort_orders 是订单身份域，可从新来源选到 11 个身份，
含 A08，不继承九月期间。fixed_september_orders 保存 10 个九月订单，只能阅读/导出其中的 10 个键，不提供 population。
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
customer_artifact = customer_query.execute()
customer_export = customer_artifact.to_pandas()
```

下段只处理已经明确得到的同一张客户表，不需要创建新的 DSL 统计图。
量列位于 value 名字空间；本段均值比较要求全非空，先检查再计算。

<!-- pandas-example:customer-means -->
```python
names = ["revenue", "orders", "aov"]
if customer_export["value"][names].isna().any().any():
    raise ValueError("The customer summary requires non-missing inputs.")
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
若输入含 NA，配对统计使用共同非 NA 样本，不能分别删值后拼接；必要计算错误已在 execute 阻止提交。
Metric 内部统计可用 aov.per(customer_axis).mean()；
它的贡献推导内域不自动含未出现类别。上面的固定答案路径由 observe 请求 A/B 完整输出，不能让 pandas 猜名单。
需要在新来源计算中使用完整内部域时，可显式 revenue.per(cohort_keys).mean()，规则与差异见 §7.3。
member_count若绑定cohort_orders则计11个订单身份，不变成两个客户。

### 2.6 第五轮：原指标上卷与普通描述统计

```python
original_total = customer_query.rollup().execute()
```

上卷在 Query 上构造，本次计划计算必要组件并按原公式完成；本例得到190元/10单/AOV19。
这是新的 execute，不消费 customer_artifact；可变来源下不承诺重复上次答案。要使用那份精确输出，沿用 §2.5 的 pandas 组件计算。
普通当前格子分位数和有效值计数则直接用导出表：

<!-- pandas-example:customer-statistics -->
```python
aov_values = customer_export[("value", "aov")]
cell_statistics = {
    "mean_aov": aov_values.mean(),
    "p90_aov": aov_values.quantile(0.9),
    "valid": int(aov_values.notna().sum()),
    "rows": len(customer_export),
}
```

得到mean55、P90=91、有效值数2、行数2。这些是外部描述统计，不是原AOV的上卷。
只保存完成均值/比率或distinct数字仍不构成一般原指标上卷依据；
该信息下界对Marivo与pandas都成立。

### 2.7 第六轮：选中高消费 ID，再明确观察上期

```python
(high_customer_column,) = [
    axis["column"] for axis in customer_export.attrs["marivo"]["axes"]
    if axis["kind"] == "field"
]
high_rows = customer_export.loc[customer_export[("value", "revenue")] >= 100]
high_keys = tuple((value,) for value in high_rows[("coordinate", high_customer_column)].tolist())
high_ids = mv.coordinates(by=(customer_axis,), keys=high_keys)
previous_high = s.observe(
    {"revenue": revenue},
    where=mv.in_population(high_ids),
    during=august,
    by=high_ids,
).execute()
classification_export = saved_order_classification.to_pandas()
(order_column,) = [
    axis["column"] for axis in classification_export.attrs["marivo"]["axes"]
    if axis["kind"] == "member"
]
(class_customer_column,) = [
    axis["column"] for axis in classification_export.attrs["marivo"]["axes"]
    if axis["kind"] == "field"
]
selected_orders = classification_export.loc[
    classification_export[("coordinate", class_customer_column)].isin([key[0] for key in high_keys])
]
saved_high_order_keys = tuple(selected_orders[("coordinate", order_column)].tolist())
```

high_ids 是脚本从已见值明确选择的 A，上期收入50；后续来源仍可包含 A 的新订单。
pandas 从已输出订单身份/客户分类中选得 A08/A09 完整键，不从收入汇总恢复隐藏名单。
若要观察这些订单，脚本可明确构造 mv.identities(OrdersEntity, keys=saved_high_order_keys)；
这是新字面量定义，不是 Artifact 的自动转换或历史语义恢复。

### 2.8 第七轮：固定 ID 域与重新选择 ID 域

```python
august_fixed_cells = s.observe(
    {"revenue": revenue},
    during=august,
    where=mv.in_population(cohort_keys),
    by=cohort_keys,
)
august_fixed_stats = august_fixed_cells.reduce(
    {"revenue": august_fixed_cells["revenue"].sum(), "ids": mv.row_count()}
)
august_fixed = august_fixed_stats.select(
    {"spend_per_customer": august_fixed_stats["revenue"] / august_fixed_stats["ids"]}
).execute()

august_keys = orders_in(august).classify(by=(customer_axis,))
august_reselected_cells = s.observe(
    {"revenue": revenue},
    during=august,
    where=mv.in_population(august_keys),
    by=august_keys,
)
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
cohort_keys 是脚本明确的 A/B 字面量，august_keys 是重新选择程序；后者每次 execute 可能变化。
三个集合查询分别执行，不承诺跨调用共同快照；需要比较同一份已见键集时导出后用 pandas/集合计算。

### 2.9 第八轮：贡献份额与换分母

```python
regional = s.observe(
    {"revenue": revenue},
    during=september,
    by=(region_axis,),
)
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

### 2.10 第九轮：终端产物不恢复细状态

Artifact 不提供 DSL 方法、保留请求或离线细比较。
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
    where=mv.in_population(cohort_keys),
    by=daily_grid,
)
daily = daily_query.execute()
daily_total = daily_query.select({"active_ids": daily_query["active_ids"]}).rollup().execute()
saved_days = s.query(daily_query.population().project(by=(days,))).execute()
```

A/B×30日共60格，10个有贡献、50个已完整观察的空贡献格；
输出存在与来源覆盖不同。日去重计数和为10，月精确去重为2。
Artifact 不保存精确 distinct 集合；Query.rollup 在本次计划中取得充分输入，日计数本身不充分。
这里 daily、daily_total 和 saved_days 各自 execute，不能据共享变量推断共用同一次来源读取。
project日期得到30个完整日，不按量是否缺失删桶。
显式Null字段组、空域/标量空规约及成员复合键仍按 [DSL §4.5](lazy-analysis-dsl-redesign.md#45-坐标和目标域) 处理。

### 2.12 第十一轮：重开 Artifact，继续 pandas

读取旧答案不需要当前语义模型。刚 execute 的产物和重开产物都引用已提交 Parquet 与输出记录，使用同一 reader。
Artifact ID 只定位输出，不恢复此前 Metric、轴对象或观察图，也不依赖上一进程仍持有数据。
以下 resume 只适用于同一新格式代际的 Session；重构前的旧 Store/Session 明确拒绝，保留旧文件，不自动迁移。

```python
resumed = mv.session.resume(s.id)
restored_customers = resumed.artifact(customer_artifact.id)
restored_total = resumed.artifact(original_total.id)
frame = restored_customers.to_pandas()
total_frame = restored_total.to_pandas()
if len(total_frame) != 1:
    raise ValueError("Expected a single saved total.")
old_total = total_frame[("value", "revenue")].iloc[0]
if old_total == 0:
    raise ValueError("The saved reference total must be nonzero.")
ordinary_ratios = frame[("value", "revenue")] / old_total
restored_comparison = resumed.artifact(regional_comparison_artifact.id)
comparison_frame = restored_comparison.to_pandas()
restored_focus = comparison_frame.loc[comparison_frame[("value", "revenue.absolute")] >= 80]
restored_customers.show()
restored_findings = restored_customers.findings(limit=20, cursor=None)
restored_findings.show()
assert restored_findings.items == () and restored_findings.next_cursor is None
restored_customers.evidence_digest().show()
```

这里的比例只使用脚本选定的输出，不声称是经过 DSL 贡献包含检查的业务 share。
如果另要查询当前来源，应重新加载模型并构造 Query；需要旧名单时按 §2.4 显式提供完整键和当前绑定。
新收入为 A120/B90 时，新查询总额为210，旧产物仍为100/90与190；两份数据可在 pandas 按完整键比较。
业务口径相容性由脚本判断，不用标签或 attrs 自动重建语义。
模型变化不影响历史值读取；Artifact 不判断它是否仍符合当前模型或来源，也不提供新鲜度标记。

本例普通观察的 Finding 为空，findings 返回有界空页；evidence_digest 读取实际读取与检查记录的摘要。
它们只描述这份已提交产物，不使 restored_customers 获得 rollup/share 或任何其它 DSL 方法。
构造披露到新产物、重开阅读的完整链见[场景 §8.7](lazy-analysis-dsl-scenario-validation.md#87-构造披露执行后阅读与重开)。

### 2.13 时间分析：七日收入、累计与期间比较

沿用[场景验证 §7](lazy-analysis-dsl-scenario-validation.md#7-时间分析滚动累计与期间比较)的模拟ledger：
10-01…08为100/1、900/99、0/0、40/1、50/1、60/1、70/1、80/1，
9月26…30每日10/1；截至本年1月1日后的其它模拟历史另有650元。
完整数值输入以场景验证为准，所有示例只作独立数值见证。

```python
from datetime import timedelta

temporal_s = mv.session.get_or_create("temporal-sales", report_timezone="Asia/Shanghai")
income = catalog.metrics.get(ms.ref.metric("sales.revenue"))
order_count = catalog.metrics.get(ms.ref.metric("sales.order_count"))
temporal_aov = catalog.metrics.get(ms.ref.metric("sales.average_order_value"))
display = temporal_s.time_scope(start="2026-10-07", end="2026-10-09")
output_days = display.by(mv.grain("day"))
seven = mv.trailing_periods(count=7)

rolling = temporal_s.observe(
    {"income": income.window(seven), "orders": order_count.window(seven), "aov": temporal_aov.window(seven)},
    during=output_days,
).execute()
elapsed = temporal_s.observe(
    {"income": income.window(mv.trailing_duration(duration=timedelta(hours=168)))},
    during=output_days,
).execute()
mtd = temporal_s.observe(
    {"income": income.window(mv.to_date(grain=mv.grain("month")))},
    during=output_days,
)
ytd = temporal_s.observe(
    {"income": income.window(mv.to_date(grain=mv.grain("year")))},
    during=output_days,
)
since_january = temporal_s.observe(
    {"income": income.window(mv.since(start=temporal_s.instant("2026-01-01")))},
    during=output_days,
)
cumulative_joined = mtd.align(ytd)
cumulative = cumulative_joined.select(
    {"mtd": cumulative_joined["left.income"], "ytd": cumulative_joined["right.income"]}
).execute()
```

各窗口分别 observe，默认信任声明的数据源，不需要额外完整性声明；成功执行不证明上游数据完整。
temporal_aov 直接使用 §2.1/§6.1 的同一 sales.average_order_value 声明：revenue / order_count 默认在空日返回 NA。
场景不为同一全名重定义政策；需要拒绝空日时使用 §7.2 的独立 aov_strict 声明。
MTD/YTD用相同真实输出日历，所以exact可组合；since_january独立定义起点。
10-07/08七日收入1220/1200、订单104/104、AOV305/26与150/13，
MTD1220/1300、YTD1920/2000。显示仅两天但读取更早窗口。
elapsed 在相同日输出端点评价固定168小时窗口；输出粒度与贡献窗口长度分别声明。
v0.25 不提供父期间内日序配对；上月同进度改为分别对 10-07…08 与 09-07…08 求月累计，
再 compare(match=mv.ordinal()) 按位置配对，上月610/650，环比100%；
这里两侧都是同一时区的完整 day 桶，桶数相同；改为不同粒度或截断桶会拒绝，outer 也不放宽此条件。
上年七日用 compare(match=mv.previous_year())，305/300，同比300%。
若日粒度配对包含无上年对应的 2 月 29 日，须明确使用 outer 保留缺侧或重建期间/粒度，不能夹日。
期间对应的数值见证见 [场景验证 §7.3](lazy-analysis-dsl-scenario-validation.md#73-同比环比先计算两个端点再按期间对应)。
同比不逐日搬贡献，七自然日不等于168小时；固有过滤/P仍限制窗口。

### 2.14 终端 Artifact 与 pandas 日格子统计

```python
history = temporal_s.time_scope(start="2026-09-25", end="2026-10-09")
history_query = temporal_s.observe(
    {"income": income, "orders": order_count, "aov": temporal_aov},
    during=history.by(mv.grain("day")),
)
history_result = history_query.execute()
history_export = history_result.to_pandas()
```

Query 不提供重设窗口；Artifact 不提供 DSL 操作；原七日指标由 §2.13 的 rolling 以 during=seven 重新 observe，
按原指标重求组件。以下改问日AOV的等权平均，交pandas；
示例明确采用浮点统计，导出本身不将Decimal或大整数强制转float。
本例只有一个日历轴、上海完整日序列，Null仅表示按已声明政策完成的空贡献日。
日期完整检查只验证已保存的日历坐标；默认来源信任不因此升级为完整性审计。
原观察的来源读取、日历/映射/请求域覆盖或必要计算检查失败时，不会形成可导出的部分 Artifact；这不包括外部业务漏数通知。

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
values = history_export[("value", "aov")].astype("Float64")
daily_values = pd.Series(values.array, index=dates)
mean_daily_aov = daily_values.rolling(7, min_periods=1).mean().loc["2026-10-07":"2026-10-08"]
```

日AOV等权均值为1810/33、1700/33，不是原七日AOV的305/26、150/13。
完整网格保证本例七行对应七个自然日；min_periods只控制已明确允许的Null样本，
不证明七日来源完整；外部统计仍须检查所需日期是否齐全，不能把日期齐全当作来源完整性证明。
其它时区/桶宽须按其真实日历重新选择pandas窗口，不能把此段推广为168小时窗口。
固定的日结果不能换成七日原指标：日最终AOV、日P95或切穿桶的日收入不能制造缺失信息，
也不能回源补历史。普通pandas统计不因保存Artifact ID而取得原指标窗口权限。

### 2.15 日历粗化与原上卷、分组份额

沿用 §2.13 的时间场景来源，四天范围跨越月界；同一日 Query 分别构造上卷和份额后执行：

```python
coarsen_scope = temporal_s.time_scope(start="2026-09-29", end="2026-10-03")
coarsen_days = coarsen_scope.by(mv.grain("day"))
coarsen_months = coarsen_days.coarsen(mv.grain("month"))
coarsen_query = temporal_s.observe(
    {"income": income, "orders": order_count, "aov": temporal_aov},
    during=coarsen_days,
)
monthly_query = coarsen_query.rollup(by=(coarsen_months,))
shares_query = coarsen_query.share("income", partition_by=(coarsen_months,))
monthly_query.execute().show()
shares_query.execute().show()
```

两个月的实际范围保持截断，收入20/1000、订单2/100、AOV均10；四日份额为1/2、1/2、1/10、9/10。
完整数据、反例及独立数值见证见[场景 §7.12](lazy-analysis-dsl-scenario-validation.md#712-截断月桶的原指标上卷与月内份额)。
coarsen 不能扩大观察范围，份额分组不折叠日坐标；Artifact 不承担任何后续 DSL 计算。

## 3. 时间锚点与版本选择

规则见 [DSL §4.1.1](lazy-analysis-dsl-redesign.md#411-精确时刻与版本选择)。

```python
anchor_s = mv.session.get_or_create("anchor-example", report_timezone="Asia/Shanghai")
october = anchor_s.time_scope(start="2026-10-01", end="2026-11-01")
launch = anchor_s.instant("2026-10-15T09:30:00")
absolute_launch = anchor_s.instant("2026-10-15T01:30:00+00:00")
since_launch = mv.since(start=launch)
new_york_scope = anchor_s.time_scope(
    start="2026-10-01", end="2026-11-01", timezone="America/New_York"
)
```

launch 与 absolute_launch 表示同一绝对时刻；since_launch 表达非日历起点，不能由 to_date 猜成月初。
使用[场景 §3.4](lazy-analysis-dsl-scenario-validation.md#34-固定名单读取属性与精确版本选择)的独立 crm.customers 声明和数据，
C1 在 11 月 1 日零点从 Silver 换成 Gold，C2 保持 Bronze，新增 C3 也是 Gold。

```python
version_catalog = ms.load()
tier = version_catalog.fields.get(ms.ref.dimension("crm.customers.tier"))
VersionCustomers = version_catalog.entities.get(ms.ref.entity("crm.customers"))
fixed_customers = anchor_s.query(
    mv.identities(VersionCustomers, keys=(("C1",), ("C2",)))
).population()
attributes = anchor_s.read(fixed_customers, {"tier": tier.at(october.end)})
gold_customers = attributes.where(attributes["tier"] == "Gold").population()
gold_query = anchor_s.query(gold_customers)
attributes.show()
attributes.execute().show()
gold_query.execute().show()
```

tier.at(october.end) 即 11 月 1 日零点，按 valid_from ≤ t < valid_to 选择 Gold；固定名单筛选后只有 C1，不加入 C3。
v0.25 不提供边界左极限版本选择，不能表达“十月结束前最后版本”，也不用 end 减 tick 近似。
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
order_entity = catalog.entities.get(orders_ref)
customer_entity = catalog.entities.get(customers_ref)
buyer = catalog.relationships.get(ms.ref.relationship("sales.buyer"))
customer_id = catalog.fields.get(ms.ref.dimension("sales.orders.customer_id"))
orders = relation_s.members(orders_ref)
customers_a = mv.identities(customer_entity, keys=(("A",),))
buyers = orders.image(via=(buyer,))
matched_orders = orders.preimage(customers_a, via=(buyer,))
customer_values = orders.classify(by=(customer_id,))
first_order = relation_s.members(
    orders_ref,
    where=mv.in_population(mv.identities(order_entity, keys=(("O1",),))),
)
expanded_orders = orders.preimage(first_order.image(via=(buyer,)), via=(buyer,))
relation_s.query(matched_orders).execute().show()
```

buyers 为客户身份 {A,B}，matched_orders 为订单身份 {O1,O2}；customer_values 只是字段值 {A,B}，
不能作为 preimage 的客户身份 targets。first_order 的客户像为 {A}，expanded_orders 却为 {O1,O2}。
若接收者换成九月订单域，preimage 只返回其中的匹配订单；若换成全量订单域，可包含这些客户的新订单。
模拟键值仅说明集合含义，不承诺共享来源表达式的多次读取相同；若要沿用已见键，由脚本读取产物后明确构造字面量域；execute 不返回可续算域。

所有关系输入都使用同一种元组形状。下面另假设 Customers 上声明 customer_region 字段，
Orders 除 buyer 外还有另一条通向 Customers 的付款客户关系，语义层已声明 buyer 为贡献归属的唯一默认路径。
sales.revenue 是 Orders 上有业务时间角色的基础收入规约：

```python
customer_region = catalog.fields.get(ms.ref.dimension("sales.customers.customer_region"))
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

关系像和身份对应的消费者不同：image 可以使用合法多对一关系；corresponding 需要两侧身份轴与一对一结构。
不同 Entity 的完整复合主键对应、align/compare 和 exact/outer 的完整代码见
[场景 §3.5](lazy-analysis-dsl-scenario-validation.md#35-不同-entity-的声明身份对应)，不能用上面的多对一 buyer 路径替代。

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

relation_paid_at = catalog.fields.get(ms.ref.time_dimension("sales.orders.paid_at"))
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
任何依赖要求按期间采样或时间样本规约时，仍须显式提供 during，日期条件和属性时点不能代替它。
即使只观察 member_count，若 by 请求日历轴，也须显式给出相容 during，不能从 by 隐式推导。

## 6. 预定义与 runtime 共用表达式与声明

以下片段使用目标 v0.37 API。Amount、Cost、Quantity、UnitPrice、UnitCost 是已声明的 Measure，
Orders 是 Entity，PaidAt、Status、CustomerId 是字段；模型模块直接引用这些对象，
分析代码从 Catalog 取得其作用域解析的对象。示例假设业务定义未改变；不要求强制匹配构造时原定义版本，变更后果由 agent/用户承担。实际消费定义仍进入计算指纹，代码位置不改变计算规则。

### 6.1 同一个 Metric，只有声明与收录不同

```python
@ms.metric(ai_context=ms.ai_context(business_definition="Paid order revenue"))
def revenue():
    return Amount.sum(time=PaidAt, empty="zero").where(Status == "paid")


@ms.metric()
def order_count():
    return Orders.count(time=PaidAt).where(Status == "paid")


@ms.metric()
def average_order_value():
    return revenue / order_count
```

分析代码使用完全相同的表达式，不需要声明或注册：

```python
catalog = ms.load()
s = mv.session.get_or_create("metric-expressions", report_timezone="Asia/Shanghai")
amount = catalog.fields.get(ms.ref.measure("sales.orders.amount"))
cost = catalog.fields.get(ms.ref.measure("sales.orders.cost"))
paid_at = catalog.fields.get(ms.ref.time_dimension("sales.orders.paid_at"))
status = catalog.fields.get(ms.ref.dimension("sales.orders.status"))
customer_id = catalog.fields.get(ms.ref.dimension("sales.orders.customer_id"))
orders = catalog.entities.get(ms.ref.entity("sales.orders"))
revenue = amount.sum(time=paid_at).where(status == "paid")
order_count = orders.count(time=paid_at).where(status == "paid")
aov = revenue / order_count
september = s.time_scope(start="2026-09-01", end="2026-10-01")
answer = s.observe({"trial_revenue": revenue, "trial_aov": aov}, during=september).execute()
```

| 关注点 | 模型预定义 | runtime 临时定义 |
| --- | --- | --- |
| 计算 | 装饰器体中的同一表达式 | 直接写相同表达式 |
| 声明身份 | 函数局部名；loader 以显式或唯一默认 domain 限定 | 直接表达式匿名；需要声明时也用相同装饰器，无须先有模型 domain |
| 输出列 | observe 字典键 | observe 字典键 |
| 政策 | 写在 sum、divide 等节点上 | 同样节点、同样默认和失败 |
| 使用 | 装饰后的 Metric 或 Catalog 返回对象 | 表达式直接交 observe |
| 持久化 | loader 收录时检查全部依赖 | 构造/声明不要求能独立成为模型 |

装饰器不接收 time、fold、unit、additivity 或数值政策。被装饰名字是 Metric，不能再调用 revenue()。
增加声明、改函数名或结果标签不改变计算指纹；函数名在装饰时决定局部声明身份，后续 Python 别名不改名。
模型使用唯一默认 sales domain；也可在装饰器明确 domain=ms.ref.domain("sales")，函数仍写 revenue。
不接受 name 字符串。非法函数名在装饰时拒绝，domain 缺失/歧义与全名冲突在收录时拒绝；
runtime 无模型 domain 仍可声明和 observe，输出标签不会被拿来补模型身份。

### 6.2 Measure 的同构设计与不同责任

Quantity 单位为 1，单价/单位成本为 CNY 且不可加，三个量属于同一订单行。
逐行乘积可计算，但新金额的业务可加性需要显式声明：

```python
@ms.measure(additivity=ms.additive_all())
def order_amount():
    return Quantity * UnitPrice


@ms.measure(additivity=ms.additive_all())
def order_cost():
    return Quantity * UnitCost


@ms.metric()
def gross_margin():
    return (order_amount - order_cost).sum(time=PaidAt)
```

临时业务量采用同一声明体和业务授权，函数局部名只作声明身份，不自动发布到模型：

```python
quantity = catalog.fields.get(ms.ref.measure("sales.orders.quantity"))
unit_price = catalog.fields.get(ms.ref.measure("sales.orders.unit_price"))
unit_cost = catalog.fields.get(ms.ref.measure("sales.orders.unit_cost"))


@ms.measure(additivity=ms.additive_all())
def trial_amount():
    return quantity * unit_price


@ms.measure(additivity=ms.additive_all())
def trial_cost():
    return quantity * unit_cost


trial_margin = (trial_amount - trial_cost).sum(time=paid_at)
```

unit 已由 1×CNY 推导，装饰器只补充乘积的新可加授权；省略已有事实不会清空它们。
Measure 声明的 unit/additivity 是业务合同，不是装饰器全局计算政策。
只为已授权毛利增加名称时，模型和 runtime 都能省略重复的单位/权限：

```python
@ms.measure()
def order_margin():
    return order_amount - order_cost


runtime_margin = (trial_amount - trial_cost).sum(time=paid_at)
```

命名的 order_margin 与未命名差值具有同一计算事实；所属 Entity 从表达式取得，函数名决定局部字段名。
原单价仍不可加；直接 `(quantity * unit_price).sum()` 缺少新业务授权，必须拒绝。
已授权的金额减成本则继承相容权限，可以直接 sum；不强制每个中间表达式都有名称。
跨事实行量相乘不能自动关联，须先各自规约为 Metric。这里只改变目标表达式设计，当前 Ibis authoring 未修改。

### 6.3 不可变域依赖与模型收录

```python
literal_cohort = mv.coordinates(by=(customer_id,), keys=(("A",), ("B",)))
literal_revenue = revenue.where(mv.in_population(literal_cohort))
source_cohort = s.observe({"revenue": revenue}, during=september, by=(customer_id,)).population()
selected_cohort_revenue = revenue.where(mv.in_population(source_cohort))


@ms.metric()
def selected_revenue():
    return selected_cohort_revenue


selected = s.observe({"revenue": selected_revenue}, during=september).execute()
```

字面量域和来源/Query 程序是显式依赖，不包含 Artifact 键引用。selected_revenue 仍依赖原选择程序，
不会把它变成已求值名单；可以在同 Session 观察，模型收录拒绝带执行 Session 的闭包。
字面量域若轴可独立解析则可收录；Artifact 在表达式构造时就拒绝，不等待模型收录。

### 6.4 原生 Ibis：命名、匿名与延迟类型绑定

以下为待实施示例。amount/cost 已完成语义解析，同根且单位为 CNY；revenue/reference_revenue
是单位相同的 Metric。此处不要求来源 schema，不提供 dtype、ms.ibis 或 ms.require。

```python
import ibis

positive = ibis.ifelse(amount > cost, amount - cost, 0)

@ms.measure(additivity=ms.additive_all())
def positive_margin():
    return positive

@ms.metric()
def revenue_index():
    return (revenue / reference_revenue).sqrt()

anonymous_index = (revenue / reference_revenue).sqrt()
query = s.observe({"named": revenue_index, "anonymous": anonymous_index})
artifact = query.execute()
```

positive 是原生条件表达式，但不自动取得业务求和许可；positive_margin 明确声明该授权。
revenue_index 与 anonymous_index 的延迟配方、语义检查、除零政策及结果相同，仅声明元数据不同。
execute 自动读取必要 schema，生成真实 dtype 的 Ibis 图并完成检查；上面的普通 Python 组合和装饰器
使用同一受控 Deferred 机制，不能只有装饰器示例能运行。原生顶层函数的匿名后续链也须成立：

```python
anonymous_mean = ibis.ifelse(amount > cost, amount - cost, 0).mean()
positive_total = positive_margin.sum()
```

mean 不授予求和权限；直接 positive.sum() 仍缺业务授权。这里 mean/sum 是归一后的语义规约，
继承已冻结的唯一默认时间；显式 time= 等参数按语义接收者合同处理，不能直接传给已绑定裸 Ibis 方法。
类型不合法（例如物理 amount 为字符串）、不相容单位、跨根或无本地内核分别在拥有该信息的边界失败。
具体机制由[语义提案 §2.6](../semantic/expression-model-redesign.md#26-原生-ibis-与自动延迟绑定)拥有。

即使已有可信 schema，amount/revenue 等数值作者值和这些中间结果也始终是原生 Deferred；命名信息存在内部声明 Resolver。
不接收裸后端 Ibis Value 来替换作者值，不调用作者表达式自身的 `.resolve()`/`.show()`；通过 Catalog 条目或 Query.show 披露。
命名不是提前执行。下例 numerator/denominator 为已解析的相容 Metric，分母为无量纲数值：

```python
@ms.metric()
def strict_ratio():
    return numerator.divide(denominator, zero_denominator="error")

safe_named = ibis.ifelse(denominator != 0, strict_ratio, 0)
safe_inline = ibis.ifelse(
    denominator != 0,
    numerator.divide(denominator, zero_denominator="error"),
    0,
)
safe_query = s.observe({"named": safe_named, "inline": safe_inline})
strict_query = s.observe({"safe": safe_named, "strict": strict_ratio})
```

分母为 0 或 NA 时，safe_query 的两项均选假分支返回 0；静态类型、单位和根检查仍覆盖真分支。
分母为 0 时，strict_query 的独立 strict 请求须检查除法并使 execute 失败；safe 不能取消另一处需求。
只有 safe_named 的请求不因严格子式已命名而失败。`strict_ratio * 0` 仍检查严格除法，普通乘法不提供短路。
`ibis.coalesce(strict_ratio, 0)` 也不能捕获严格除零：只有成功求值为 NA 才尝试后项。

默认 `numerator / denominator` 与 strict_ratio 即使对应相等的 Ibis Divide，也保留不同使用位置和政策。
这类差异进入声明计算指纹；仅命名不改变指纹。相同 cast 配方遇到不同实际 schema 时，声明计算指纹不变，
绑定执行指纹变化，不能复用旧物理计划。该表达式合同已接通；本文片段本身不构成运行证据。


### 6.5 五项能力：相同配方，命名与匿名共用

以下展示 v0.37 写法；有序规约、empty、membership 和 window 已有本地路线，近似方法仍须分别准入：
来源 approx_quantile、内部样本 approx_nunique 及原指标 approx_* 重算缺合格内核时拒绝，不以 exact 替代。
Inventory/Amount/Latency/CustomerId/TagId/SnapshotAt 和
OrderTags 是已 finalize/resolve 的语义依赖，Revenue 是完成 Metric；不使用裸 Ibis table、手写 schema 或 ms.require。
OrderTags 是根订单到标签 Entity 的声明成员关系，s/period/days 由调用方提供。

```python
from datetime import timedelta

@ms.metric()
def closing_inventory():
    return Inventory.sum(time=SnapshotAt, empty="null").per(SnapshotAt).last(include_null=True)

closing_inline = Inventory.sum(time=SnapshotAt, empty="null").per(SnapshotAt).last(include_null=True)

@ms.metric()
def latency_p95_approx():
    return Latency.approx_quantile(0.95)

p95_inline = Latency.approx_quantile(0.95)
users_approx = CustomerId.approx_nunique()

@ms.metric()
def revenue_nullable():
    return Amount.sum(empty="null")

nullable_inline = Amount.sum(empty="null")
tag_buckets = mv.membership(TagId, via=((OrderTags,),), unmatched="exclude")

@ms.metric()
def mean_tag_revenue():
    return Revenue.per(tag_buckets).mean()

tag_mean_inline = Revenue.per(tag_buckets).mean()
tag_query = s.observe({"revenue": Revenue}, during=period, by=(tag_buckets,))
full_tags = mv.coordinates(by=(tag_buckets,), keys=(("A",), ("B",)))
full_tag_query = s.observe({"revenue": Revenue}, during=period, by=full_tags)
full_tag_mean = Revenue.per(full_tags).mean()

month_rule = mv.to_date(grain=mv.grain("month"))

@ms.metric()
def revenue_mtd():
    return Revenue.window(month_rule)

mtd_inline = Revenue.window(month_rule)
q_named = s.observe({"value": revenue_mtd}, during=days)
q_inline = s.observe({"value": mtd_inline}, during=days)
history = Revenue.window(mv.all_history())
elapsed_week = Revenue.window(mv.trailing_duration(duration=timedelta(days=7)))
calendar_week = Revenue.window(mv.trailing_periods(count=7))
```

每对 named/inline 的配方、政策、检查域与声明计算指纹相同，声明身份不同；schema 已知/未知均保持 Deferred。
q_named 与 q_inline 使用同一规则和观察轴；没有 during=window 的第二入口。
这里展示配方等价，不承诺独立 execute 读取相同快照或近似计算产生逐位相同结果。
无轴规则及 tag_buckets 不依赖 s，命名声明可独立收录；具体 Query/days 不进入模型闭包。
全部窗口表达式消费时须提供评价端点，calendar_week 还须 CalendarAxis；窗口不能补造基础指标的时间角色。

closing_inventory 的 last 保留末次 NA；改成 include_null=False 是选择末次非 NA，不是格式选项。
第一步 sum(empty="null") 同样重要：若显式改成 empty="zero"，末次全 NA 会成为有效零，后续跳 NA 的含义随之改变。
tag_mean 是标签桶统计；一笔收入可进入多个标签，不能把它解释成去重客户均额或总体收入。
elapsed_week 的七天恒为168小时；calendar_week 是七个输出日历期间，两者在 DST 周可能不同。
原 first-seen、固定时长、空集和成员集合的迁移条件见[语义附录 A.1.1](../semantic/expression-model-redesign.md#a11-等价迁移的判定条件)。


完整域例：仅 A 有100收入时，子指标为 sum(empty="zero") 时，full_tag_query 为 A=100/B=0，full_tag_mean 为50；
采用默认 sum() 时，B 为 NA、忽略 NA 的 mean 为100。按实际成员桶 per(tag_buckets) 则只有 A，mean 为100。
full_tags 的绑定明确且不依赖事实 Query；来源域可写 `s.members(TagEntity).classify(by=(tag_buckets,))`，
此处直接读目标 TagEntity 的身份分类，不走 OrderTags，也不执行描述可能携带的 TagEntity 自关系。来源域会保留其 Session/时点，不能伪装成 source-free 模型参数。

若同时存在 OrderChannels，独立集合组合可写：

```python
channels = mv.membership(ChannelId, via=((OrderChannels,),), unmatched="exclude")
by_tag_channel = s.observe({"revenue": Revenue}, during=period, by=(tag_buckets, channels))
mean_tag_channel = Revenue.per((tag_buckets, channels)).mean()
```

一笔100属于标签 A/B、渠道 web/store 时，四桶各100，并集总额100。真实相关标签/渠道对须改用同一个成员 Entity
上的字段元组描述；不能从两个独立集合猜出相关性。若另有标签根 Budget，`Revenue / Budget` 复用 tag_buckets 时，
订单根走 OrderTags、标签根恒等绑定，先分别规约再相除，不能把 Budget join 到订单明细。

标量 MTD 在 `during=s.time_scope(start="2026-09-01", end="2026-10-08")` 与起点改为10-01时，
都取[10-01,10-08)；端点恰为11-01零点时取完整十月。不要求显示起点在同一月，也不通过减一微秒选择月份。
旧 first-seen 的重置/滚动迁移可在一致输入前提下使用精确 H/W 计数组合，完整命名/匿名配方见语义附录 A.1.1；
A在09-30及10-02出现、B在10-03出现，十月窗口活跃去重2、首次新增1，不能把两者作为等价例子。
这不要求所有窗口分支共享输入；前提未证实时配方仍是合法普通表达式，只是不再具有已证明的 first-seen 迁移等价性。


显式路径优先于推导恒等。以下 EmployeeId 属于 EmployeeEntity，ReportsTo 为 Employee→Employee 的合法自关系，
Payroll 为该根上的金额指标；其它上下文与本节相同：

```python
manager_buckets = mv.membership(EmployeeId, via=((ReportsTo,),), unmatched="exclude")
self_buckets = mv.membership(EmployeeId, via=(), unmatched="exclude")
by_manager = s.observe({"payroll": Payroll}, during=period, by=(manager_buckets,))
by_self = s.observe({"payroll": Payroll}, during=period, by=(self_buckets,))
all_manager_keys = s.members(EmployeeEntity).classify(by=(manager_buckets,))
```

by_manager 沿 ReportsTo 分配贡献；by_self 才读取根自身分类。all_manager_keys 构造完整目标桶域，
直接读取所选 Employee 身份分类，不沿 ReportsTo 展开。无效显式路径不会退回 self_buckets 的含义。

窗口逻辑绑定在 observe 固定；以下 Region 为已解析的单值分组字段，days 为输出日历轴：

```python
week_rule = mv.trailing_duration(duration=timedelta(days=7))
regional_week = s.observe({"value": Revenue.window(week_rule)}, during=days, by=(Region,))
all_regions_week = regional_week.rollup(by=(days,))
cell_sum = regional_week.reduce({"sum_of_cells": regional_week["value"].sum()})
coarse_week = s.observe(
    {"value": Revenue.window(week_rule)},
    during=days.coarsen(mv.grain("month")),
)
```

all_regions_week 保留每个原端点/W，仍须通过原指标的空间合并检查。`regional_week.rollup(by=(days.coarsen(mv.grain("month")),))` 与 `regional_week.rollup()` 均拒绝，
不能借保留了原始输入就改变或删除时间绑定。coarse_week 是显式的新观察，按粗轴实际端点重新绑定七日窗口；
cell_sum 只是当前格子的统计。相邻两个端点的七日值均为7时，格子和14、贡献并集8、末端点值7不是同一问题。

## 7. 五个核心 Metric 场景

每项都先明确输入与计算，再说明模型声明、临时用法和反例；不另立 factory 或装饰器计算语言。
以下五项的独立数值见证在 [场景验证附录 H](lazy-analysis-dsl-scenario-validation.md#附录-h统一行指标表达式的五个核心见证)。

### 7.1 收入：来源规约与局部政策

```python
runtime_revenue = Amount.sum(time=PaidAt).where(Status == "paid")


@ms.metric()
def paid_revenue():
    return Amount.sum(time=PaidAt).where(Status == "paid")
```

两个已支付订单金额 100、90，均为 190。没有贡献或金额全为 NA 时默认均为 NA；
需要返回 0 时在来源 sum 上显式传入 empty="zero"，有效值相消为零仍为 0。
若改成 nulls="error"，所选订单中存在 Null 就失败。政策不会因是否装饰而变化。

### 7.2 AOV：所有除法共享一个含义

```python
runtime_aov = revenue / order_count
explicit_default_aov = revenue.divide(order_count, zero_denominator="null")


@ms.metric()
def aov_strict():
    return revenue.divide(order_count, zero_denominator="error")
```

A 有 1 单金额 100，B 有 9 单共 90：总体 AOV=190/10=19，客户 AOV 的等权均值为 (100+10)/2=55。
默认 /、.divide(order_count) 与显式 null 同节点、同分子分母；0/0 和非零数/0 默认均为普通 NA。
aov_strict 是另一声明，显式 error 才拒绝共同非 NA 的零分母；同一全名不对应两种政策。
装饰器不能用一个政策覆盖 revenue 内部的其它运算。总体重算需组件，不能平均已完成 AOV。

Query Column 使用同一写法，不要求回到 Metric 阶段修改公式：

```python
measured = s.observe({"revenue": revenue, "orders": order_count}, during=september)
column_aov = measured.select({"aov": measured["revenue"] / measured["orders"]})
strict_column_aov = measured.select({
    "aov": measured["revenue"].divide(measured["orders"], zero_denominator="error"),
})
```

参数只控制新除法；已有严格子指标的错误不会被外层 null 政策消除。
默认 NA 可被后续 mean 忽略，业务若要求每个样本的 AOV 可定义，应选择严格表达式。

### 7.3 固定人群均额：内部样本就是所选名单

```python
cohort = mv.coordinates(by=(customer_id,), keys=(("A",), ("B",)))
zero_revenue = Amount.sum(time=PaidAt, empty="zero").where(Status == "paid")
cohort_mean = zero_revenue.per(cohort).mean()
observed_mean = revenue.per(customer_id).mean()
cohort_active_mean = revenue.where(mv.in_population(cohort)).per(customer_id).mean()
cohort_ratio = revenue.where(mv.in_population(cohort)) / revenue


@ms.metric()
def cohort_mean_revenue():
    return zero_revenue.per(cohort).mean()
```

来源 A=100、C=300，B 无贡献，收入显式 sum(empty="zero") 空/全 NA 为零：完整 cohort 均额 50，全体实际客户均额 200，
cohort 活跃客户均额 100，人群收入比率 1/4。per(cohort) 已选择内部样本，不要求再 where，也不因 C 在域外失败。
外部 observe(by=cohort) 的完整输出覆盖合同不同，不能用它隐式筛掉 C。
来源动态名单保留它自己的选择期间；固定名单不会在新期间重新选人。

同一个客户可以在各地区分别成为一个内部样本；此处改用独立数据 East:A100，West:A300/B100：

```python
region = catalog.fields.get(ms.ref.dimension("sales.orders.region"))
regional_means = s.observe(
    {"cohort_mean": cohort_mean, "active_mean": observed_mean},
    during=september, by=(region,),
).execute()
```

完整 A/B 名单的均额为 East50/West200，实际客户均额为 East100/West200；总体客户均额为250。
样本是 (地区, 客户)，不要求客户只属于一个地区；共享客户轴的内外坐标则必须一致。
若仅保存地区客户均额及客户数，不能保证总体重算：East:A100/West:A300 与
East:A100/West:B300 的地区摘要相同，总体却为400/200。Query 上卷路线须计算这些信息；Artifact 不提供 rollup。

### 7.4 带宽 P95：先空间求和，再对时间样本规约

```python
runtime_bandwidth_p95 = Bandwidth.sum(time=SampleAt).per(SampleAt).quantile(0.95)


@ms.metric(domain=ms.ref.domain("network"))
def total_bandwidth_p95():
    return Bandwidth.sum(time=SampleAt).per(SampleAt).quantile(0.95)


bandwidth_answer = s.observe(
    {"bandwidth_p95": runtime_bandwidth_p95}, during=september,
).execute()
```

SampleAt 已被声明为五分钟状态采样轴，Bandwidth 仅在固定时点内可空间相加。
A=[10,0]、B=[0,10]，总体先得 [10,10]，P95=10；设备 P95 各为 9.5，相加 19 是错误计算。
必须提供观察期间并保持应有样本/缺测事实；普通时间字段不能凭 per 获得采样状态意义。
只有设备 P95 的固定答案不能完成总体上卷，新 Query 必须计算按时点信息。
DeviceId 是同一来源上的设备维度；各设备共享相同采样时点也可以分别求 P95：

```python
device_p95 = s.observe(
    {"bandwidth_p95": runtime_bandwidth_p95},
    during=september, by=(DeviceId,),
).execute()
```

内部样本为 (设备, 时点)，输出 A/B 各9.5；裸时点无需唯一映射到设备。

### 7.5 加权均值：共同有效配对是算子的责任

```python
runtime_weighted_price = UnitPrice.weighted_mean(weight=Quantity, time=PaidAt)


@ms.metric()
def weighted_unit_price():
    return UnitPrice.weighted_mean(weight=Quantity, time=PaidAt)
```

value=[10,Null]、weight=[1,9] 只保留共同有效的第一对，得 10，不是用全部权重得到的 1。
有效配对为空时两种零分母政策均为 NA；非空有效配对权重和为 0 时默认为 NA，可在 weighted_mean 上显式选 error。
组值 10、30 配权重 1、9 得 28，不能把组均值等权平均成 20。

如果样本是每位客户的指标而非原始订单行，仍明确输入阶段：

```python
weighted_customer_aov = aov.per(customer_id).weighted_mean(weight=order_count)
```

value 和 weight 都在同一客户域完成再配对，不能把 Quantity 行值直接放入该 weight 参数。
相同定义的模型/临时版本有相同政策和依赖；模型名称本身不授予合并能力或份额支持。

权重可以是不可加的评分，不需要先声明为业务总量。假设 Value 与 Score 是同根数值 Measure，
Score 不可加，值为10/30、分数为0.2/0.8：

```python
runtime_scored_mean = Value.weighted_mean(weight=Score, time=PaidAt)


@ms.metric()
def scored_mean():
    return Value.weighted_mean(weight=Score, time=PaidAt)
```

两种定义都得26；weight= 明确其数学归一化角色，不授权 Score.sum() 或评分份额。
有符号权重遵守算子合同，零权重和按本次节点政策处理，不能默认套用非负权重的值域性质。

### 7.6 跨场景边界：行常数与指标常数

地区收入100/300时，`revenue + 10` 的地区值为110/310，而总体原式为410；
Query 内组件充分时 rollup 应重算410，不能把地区完成值相加成420，也不能据此 share。
`(Amount + 10).sum()` 则每条贡献加10；若各地区各一条，合计420，和前一表达式有不同含义。
给它们加装饰器或换函数名不会改变这一区别。复审数值反例见
[场景验证附录 I](lazy-analysis-dsl-scenario-validation.md#附录-i第一性原理复审反例)。

### 7.7 多根与 fanout：先声明贡献含义

以下是独立目标 API 片段。OrderAmount/RefundAmount 分属订单和退款根；OrderTags 是订单到标签的声明关系，
RefundOrder 是退款到订单的单值声明关系，TagId 属于标签 Entity；所有输入均已解析。
相同观察期间对两个组件分别使用支付和退款时间，不自动改成同一批订单的最终退款。

```python
order_revenue = OrderAmount.sum(time=OrderPaidAt)
refund_total = RefundAmount.sum(time=RefundedAt)
net_revenue = order_revenue - refund_total
tags = mv.membership(
    TagId,
    via=((OrderTags,), (RefundOrder, OrderTags)),
    unmatched="exclude",
)
tag_net = s.observe(
    {"net_revenue": net_revenue}, during=period, by=(tags,),
)
```

订单 O1=100、退款 R1=20 归属 O1，O1 属于 A/B 两标签；同一 (O1,A) 的桥记录出现两次。
membership 下收入 A/B 各100，退款各20，净额各80；原指标的贡献并集净额仍80，两个格子的 sum 则为160。
执行器可以沿关系取得不同贡献键/桶键后回取数值，也可采用保持相同语义的预聚合计划；
不能将桥记录或订单×退款的乘积当成新增订单/退款。缺少退款根的合法成员路径时拒绝绑定。

若问题改变，显式改变贡献单位：

| 问题 | 表达与边界 |
| --- | --- |
| 只统计关联到所选标签的订单 | 在有限订单身份域上 preimage(tag_population, via=(OrderTags,))，再用 in_population 约束订单组件；无论关联几次只选一次 |
| 统计两条真实关联事件 | 以 EventId 为完整键的关联 Entity.count；同端点 E1/E2 都计入，membership 不能替代事件计数 |
| 将订单100按 A/B 的0.25/0.75分配 | 以分配记录为贡献根，显式构造已授权分配金额25/75再规约；不是 membership 的100/100，也不是 weighted_mean |
| 各客户达成率的均值 | (Revenue / Budget).per(CustomerId).mean()；要求两个根都能绑定共同客户轴，先按客户完成比率再平均 |
| 每笔订单找此前最近点击 | 上游先给出有明确匹配与完整键的贡献来源；Metric 体不提供任意时间 join 或通用触点归因 |

达成率例中 A/B 的收入为100/0、预算为10/90，客户比率均值为5，总收入/总预算则为1。
不能先压到最终展示粒度再恢复客户比率；有 per 时保留完整 (外部格子, 客户) 样本。
分配记录的权重和等于1是该模型的业务前提，不由本片段自动验证。完整分类、充分状态和执行边界由
[语义 §3.7.1–§3.7.2](../semantic/expression-model-redesign.md#371-fanout-的表达方式与能力边界)拥有。
