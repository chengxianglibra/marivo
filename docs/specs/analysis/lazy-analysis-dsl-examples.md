# Lazy Analysis DSL：多轮分析示例

Status: AnalysisFrame cutover examples, 2026-10-19.
[最终合同](query-result-reuse.md) 拥有结构/载体规则；本页给出显式来源与固定数据的工作流。
示例的模型/数据前提必须成立，静态代码与独立数值表不表示 Runtime 或发布验收。
完整可创建本地数据的 EN/ZH 教程位于 latest Python quick start。

## 1. 按问题选择入口

observe 绑定原 Metric，read 绑定单 Entity 非空属性，values 声明字面量，frame 绑定完整键域。
compute 返回完整内存 Frame，persist 发布已有完成结果。继续用返回对象才固定值与键。
持久 frame.artifact 仅审计；persisted_frame(str ID) 恢复完整数据且无需旧来源或模型。
原整体、新属性、新窗口需要显式来源，不是 reduce 或已退役 rollup/drill/share 的机械替换。

## 2. 完整多轮分析示例

保留下面同一份 ledger 与明确业务定义；用户原始定义不因执行成功变为完整性证明。

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

### 2.2 第一轮：固定两个地区观察，再比较

```python
current = s.observe({"revenue": revenue, "orders": orders, "aov": aov},
                    during=september, by=(region_axis,)).compute()
baseline = s.observe({"revenue": revenue, "orders": orders, "aov": aov},
                     during=august, by=(region_axis,)).compute()
current_revenue = current.select({"revenue": current["revenue"]}).compute()
baseline_revenue = baseline.select({"revenue": baseline["revenue"]}).compute()
change = current_revenue.compare(
    baseline_revenue, on=((current_revenue.axes[0], baseline_revenue.axes[0]),),
    how="outer",
).persist()
change.show(n=10)
change.artifact.show()
```

生成值名为 left.revenue/right.revenue/absolute.revenue/relative.revenue，
presence 独立于 NA。outer 不把缺失端点当零贡献；业务需要空地区贡献时要明确完整源域与 empty 政策。
每次 source compute 固定自己的读数，不承诺跨读事务快照；比较消费这两份精确数据。

### 2.3 第二轮：固定父键，显式声明新的细域观察

```python
chosen = current.top(1, by=current["revenue"]).compute()
parents = s.population_from(chosen, on=((chosen.axes[0], region_axis),))
parents.show()
current_detail = s.observe(
    {"revenue": revenue}, during=september, by=(region_axis, customer_axis),
    where=mv.in_population(parents),
).compute()
baseline_detail = s.observe(
    {"revenue": revenue}, during=august, by=(region_axis, customer_axis),
    where=mv.in_population(parents),
).compute()
detail = current_detail.compare(
    baseline_detail,
    on=tuple(zip(current_detail.axes, baseline_detail.axes)), how="outer",
).compute()
detail.show()
```

region/customer 字段由调用方明确提供。桥接投影完整联合普通键，固定父地区不重新排名；
新 customer 分类与金额是新的来源事实，不恢复选择时的历史城市/客户名单。
新贡献或删除贡献可改变新读数。on 必填，轴必须属于精确 Frame；不能从点号值名推断来源。

### 2.4 第三轮：普通 ID 字段与订单身份分别固定

```python
customer_source = s.observe({"revenue": revenue}, during=september, by=(customer_axis,)).compute()
customer_keys = s.population_from(customer_source, on=((customer_source.axes[0], customer_axis),))
selected_orders = orders_in(september)
order_keys = s.frame(selected_orders).compute()
order_population = s.population_from(order_keys, on=((order_keys.axes[0], OrdersEntity),))
order_attributes = s.read(order_population, {"region": region_axis}).compute()
order_attributes.show()
```

客户 ID 仍是普通字段，不升级为客户身份。完整订单身份包含全部主键；显式键存在不证明新来源有事实。
frame 支持一般 Population 和零轴空/单例域，read 需要一个身份域及非空属性。

### 2.5 第四轮：区分当前行统计与原整体

```python
customer_values = s.observe(
    {"revenue": revenue, "orders": orders, "aov": aov},
    during=september, by=(customer_axis,),
).compute()
row_summary = customer_values.reduce({
    "mean_customer_revenue": customer_values["revenue"].mean(),
    "mean_customer_aov": customer_values["aov"].mean(),
    "rows": mv.row_count(),
}).compute()
source_overall = s.observe({"revenue": revenue, "orders": orders, "aov": aov},
                           during=september).compute()
row_summary.show()
source_overall.show()
```

本 ledger 客户收入为 100/90，订单数为 1/9：客户收入均值 95，客户 AOV 均值 55，
原整体收入 190、订单数 10、AOV 19。另一个充分性反例的来源整体是 1000/91，
它同样不能由完成行的 55 推回。distinct 分组和 4 也可能对应并集 3。
每项 reduce 统计独立忽略 NA，row_count 数全部行。来源整体必须从明确 Metric/期间请求，
没有把 reduce 当作原量 rollup 的等价路线。

### 2.6 第五轮：固定选择与重新选择是不同问题

```python
chosen_customers = customer_values.top(1, by=customer_values["revenue"]).compute()
fixed_cohort = s.population_from(
    chosen_customers, on=((chosen_customers.axes[0], customer_axis),),
)
prior_for_fixed = s.observe(
    {"revenue": revenue}, during=august, by=fixed_cohort,
    where=mv.in_population(fixed_cohort),
).compute()
new_selection = s.observe({"revenue": revenue}, during=august, by=(customer_axis,)).compute()
prior_for_fixed.show()
new_selection.show()
```

by=fixed_cohort 声明完整输出域，where 明确过滤贡献；不能用漏键 by 代替过滤。
重新求当期选择可能选中另一位客户，固定 cohort 不重跑旧 Top。空结果桥接仍是空筛选，
required on=() 对零轴也是合法绑定，空零轴域与单例 {()} 不混同。

### 2.7 第六轮：跨进程恢复后继续当前数据操作

```python
saved = customer_values.persist()
saved_session_id = s.id
saved_artifact_id = saved.artifact.id
resumed = mv.session.resume(saved_session_id, by="id")
restored = resumed.persisted_frame(saved_artifact_id)
restored.show(n=0)
positive = restored.where(restored["revenue"] > 0).compute()
positive.show(n=10)
positive.persist().artifact.show()
```

恢复、固定数据运算与再次保存不读取原模型/来源 schema 或旧生产记录。
完整读取验证必要 payload/附件；元数据成功不保证数据完整。source-read、evaluation 与 commit
时间分开，最近保存不证明来源新鲜；缺失文件不授权回源修复。

### 2.8 第七轮：显式完整域参照与延期能力

```python
budget = s.values(by=(region_axis,), columns={"budget": "CNY"}, rows=(
    (("East",), {"budget": 120}), (("West",), {"budget": 80}),
)).compute()
joined = current.align(budget, on=((current.axes[0], budget.axes[0]),))
variance = joined.select({"variance": joined["left.revenue"] - joined["right.budget"]}).compute()
variance.show()
```

这是完整同域对齐，不是 scalar broadcast，也不证明贡献包含。原贡献份额、任意关系对应、
partitioned top 和日历粗化本版无等价接收者。外部固定数据比例要明确分母与证据限度。

### 2.9 时间分析：新来源窗口与冻结对应

```python
from datetime import timedelta

days = september.by(mv.grain("day"))
rolling = s.observe(
    {"revenue": revenue.window(mv.trailing_duration(duration=timedelta(days=7)))},
    during=days,
).compute()
prior_days = august.by(mv.grain("day"))
prior = s.observe(
    {"revenue": revenue.window(mv.trailing_duration(duration=timedelta(days=7)))},
    during=prior_days,
).compute()
paired = rolling.compare(prior, match=mv.ordinal(), how="outer").compute()
paired.show(n=5)
```

双方唯一日历轴可推导 on，match 仍显式。完整名义目录不等于实际输出行；过滤不压缩序号。
保存 paired map/两端冻结描述而不重建 TZ/model。部分桶拒绝 match；named offset、子日/DST
对应和再次对齐配对轴延期。新月粒度需显式新的 TimeScope.by(month)，不是 days.coarsen。

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
fixed_customers = mv.identities(VersionCustomers, keys=(("C1",), ("C2",)))
attributes = anchor_s.read(fixed_customers, {"tier": tier.at(october.end)}).compute()
gold_values = attributes.where(attributes["tier"] == "Gold").compute()
gold_customers = anchor_s.population_from(gold_values, on=((gold_values.axes[0], VersionCustomers),))
gold_frame = anchor_s.frame(gold_customers).compute()
attributes.show()
gold_frame.show()
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
fixed_orders = relation_s.frame(matched_orders).compute()
fixed_orders.show()
retained_orders = relation_s.population_from(
    fixed_orders, on=((fixed_orders.axes[0], order_entity),),
)
```

buyers 为客户身份 {A,B}，matched_orders 为订单身份 {O1,O2}；customer_values 只是字段值 {A,B}，
不能作为 preimage 的客户身份 targets。first_order 的客户像为 {A}，expanded_orders 却为 {O1,O2}。
若接收者换成九月订单域，preimage 只返回其中的匹配订单；若换成全量订单域，可包含这些客户的新订单。
模拟键值仅说明集合含义，不承诺共享来源表达式的多次读取相同。需要沿用已见键时，保留返回的
compute/persist Frame，再用 population_from 显式绑定其完整身份轴；上面的 retained_orders
固定 O1/O2，不需要导出或抄写字面量。后续显式关系或属性读取仍是新的来源事实，不恢复旧历史；
持久 Frame 的 Artifact 只提供审计。

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
).compute()
customer_count_explicit = relation_s.observe(
    {"customers": mv.member_count()},
    members=customers_a,
    during=None,
).compute()
```

这两个请求均计名单中的一个身份，不读取客户档案确认存在性，不代表当期活跃客户数。
普通来源规约有无业务时间角色均可省略期间；已有字段/版本绑定仍须合法。

上节 order_revenue 有业务时间角色，但统计已选订单的总收入不需要再追加时间过滤：

```python
selected_revenue = relation_s.observe(
    {"revenue": order_revenue},
    members=matched_orders,
).compute()
selected_revenue_explicit = relation_s.observe(
    {"revenue": order_revenue},
    members=matched_orders,
    during=None,
).compute()

relation_paid_at = catalog.fields.get(ms.ref.time_dimension("sales.orders.paid_at"))
october_revenue = relation_s.observe(
    {"revenue": order_revenue},
    where=relation_paid_at.within(relation_october),
).compute()
october_definition = order_revenue.where(relation_paid_at.within(relation_october))
intrinsic_october_revenue = relation_s.observe({"revenue": october_definition}).compute()
```

前两个请求的期间绑定等价；每次来源求值 仍读取本次来源，不承诺独立读取的值相同。
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
answer = s.observe({"trial_revenue": revenue, "trial_aov": aov}, during=september).compute()
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
cohort_frame = s.observe({"revenue": revenue}, during=september, by=(customer_id,))
source_cohort = s.population_from(cohort_frame, on=((cohort_frame.axes[0], customer_id),))
selected_cohort_revenue = revenue.where(mv.in_population(source_cohort))


@ms.metric()
def selected_revenue():
    return selected_cohort_revenue


selected = s.observe({"revenue": selected_revenue}, during=september).compute()
```

字面量域和来源/Frame 选择程序是显式依赖，不包含 Artifact 键引用。selected_revenue 仍依赖原选择程序，
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
artifact = query.compute()
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
不接收裸后端 Ibis Value 来替换作者值，不调用作者表达式自身的 `.resolve()`/`.show()`；通过 Catalog 条目或 AnalysisFrame.show 披露。
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
无轴规则及 tag_buckets 不依赖 s，命名声明可独立收录；具体 Frame/days 不进入模型闭包。
全部窗口表达式消费时须提供评价端点，calendar_week 还须 CalendarAxis；窗口不能补造基础指标的时间角色。

closing_inventory 的 last 保留末次 NA；改成 include_null=False 是选择末次非 NA，不是格式选项。
第一步 sum(empty="null") 同样重要：若显式改成 empty="zero"，末次全 NA 会成为有效零，后续跳 NA 的含义随之改变。
tag_mean 是标签桶统计；一笔收入可进入多个标签，不能把它解释成去重客户均额或总体收入。
elapsed_week 的七天恒为168小时；calendar_week 是七个输出日历期间，两者在 DST 周可能不同。
原 first-seen、固定时长、空集和成员集合的迁移条件见[语义附录 A.1.1](../semantic/expression-model-redesign.md#a11-等价迁移的判定条件)。


完整域例：仅 A 有100收入时，子指标为 sum(empty="zero") 时，full_tag_query 为 A=100/B=0，full_tag_mean 为50；
采用默认 sum() 时，B 为 NA、忽略 NA 的 mean 为100。按实际成员桶 per(tag_buckets) 则只有 A，mean 为100。
full_tags 的绑定明确且不依赖事实观察；来源域可写 `s.members(TagEntity).classify(by=(tag_buckets,))`，
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
regional_week = s.observe({"value": Revenue.window(week_rule)}, during=days, by=(Region,)).compute()
all_regions_week = s.observe({"value": Revenue.window(week_rule)}, during=days).compute()
cell_sum = regional_week.reduce({"sum_of_cells": regional_week["value"].sum()}).compute()
monthly_scope = s.time_scope(start="2026-09-01", end="2026-10-01")
coarse_week = s.observe(
    {"value": Revenue.window(week_rule)},
    during=monthly_scope.by(mv.grain("month")),
).compute()
```

all_regions_week 是明确新来源请求，保留相同端点/W；coarse_week 使用新月端点评价七日窗口。
cell_sum 仅统计当前格子。两个七日完成值都为 7 时，格子和 14、贡献并集 8、末端点值 7
是不同问题。没有 Frame rollup 或 CalendarAxis.coarsen 等价迁移。

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

Frame Column 使用同一写法，不要求回到 Metric 阶段修改公式：

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
).compute()
```

完整 A/B 名单的均额为 East50/West200，实际客户均额为 East100/West200；总体客户均额为250。
样本是 (地区, 客户)，不要求客户只属于一个地区；共享客户轴的内外坐标则必须一致。
若仅保存地区客户均额及客户数，不能保证总体重算：East:A100/West:A300 与
East:A100/West:B300 的地区摘要相同，总体却为400/200。原来源上卷路线须计算这些信息；Artifact 不提供 rollup。

### 7.4 带宽 P95：先空间求和，再对时间样本规约

```python
runtime_bandwidth_p95 = Bandwidth.sum(time=SampleAt).per(SampleAt).quantile(0.95)


@ms.metric(domain=ms.ref.domain("network"))
def total_bandwidth_p95():
    return Bandwidth.sum(time=SampleAt).per(SampleAt).quantile(0.95)


bandwidth_answer = s.observe(
    {"bandwidth_p95": runtime_bandwidth_p95}, during=september,
).compute()
```

SampleAt 已被声明为五分钟状态采样轴，Bandwidth 仅在固定时点内可空间相加。
A=[10,0]、B=[0,10]，总体先得 [10,10]，P95=10；设备 P95 各为 9.5，相加 19 是错误计算。
必须提供观察期间并保持应有样本/缺测事实；普通时间字段不能凭 per 获得采样状态意义。
只有设备 P95 的固定答案不能完成总体上卷，新来源观察必须明确计算按时点信息。
DeviceId 是同一来源上的设备维度；各设备共享相同采样时点也可以分别求 P95：

```python
device_p95 = s.observe(
    {"bandwidth_p95": runtime_bandwidth_p95},
    during=september, by=(DeviceId,),
).compute()
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
显式新来源观察的原式为 410；当前行相加是 420，不能据此声称原量上卷或贡献份额。
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
