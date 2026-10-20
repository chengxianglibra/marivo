# Lazy Analysis DSL：五个业务场景与时间分析的设计验证

Status: AnalysisFrame cutover scenario contract, 2026-10-19.
最终[数据合同](query-result-reuse.md)与[实施计划 A01-A24](analysis-frame-implementation-plan.md)
拥有当前边界。保留下列独立业务数据与数学 oracle；它们不冒充真实来源、保存/冷恢复、
provider Runtime 或发布验收。当前示例需要已经批准并加载、符合表中含义的语义模型。
来源建模细节由语义规格拥有，不从结果/列名猜含义或关系。

## 1. 结论与验证方法

每个来源问题显式 observe/read/frame/values，所有数据入口返回 AnalysisFrame。
需要同一份值/选择时使用返回 compute/persist 根；新来源事实明确构造，不能隐式恢复原图。
Frame 当前行 reduce、原整体 Metric 与外部统计分别核验；没有 rollup/drill/share/
broadcast/任意身份 corresponding/日历粗化/分区 top 的假等价接收者。

```python
from decimal import Decimal
import marivo.analysis as mv
import marivo.semantic as ms
```

## 2. 电商：销售上涨，净额却下降

### 2.1 业务问题与数据

区域负责人看到 9 月销售额上涨，但经营净额下降。需要比较 8 月和 9 月：
销售额、退款额、支付订单数、销售订单均额和净额如何变化？优先下钻净额下降最大的地区，
区分渠道；然后计算该地区占本期销售额的份额。
同时从订单数量、单价与成本计算销售额和销售毛利，并对照本轮明确输入的地区销售预算。

本题净额定义为“本月支付金额 − 本月实际退款金额”，不是会计利润，也不是“本月订单最终净收入”。
9 月对 8 月订单的退款必须进入 9 月退款额；同一订单可以有多笔退款。

| Entity / 字段 | 粒度和约束 |
| --- | --- |
| `commerce.stores` | `store_id` 唯一；region/channel 在此数据集中不变、单值 |
| `commerce.orders` | `order_id` 唯一；每单一个 store；所有列出的订单均已支付 |
| `commerce.refunds` | `refund_id` 唯一；每笔退款指向一个订单；金额为正数 |
| `order_amount` / `order_cost` / `refund_amount` | 各自事实上的可加 Measure，单位 CNY；订单金额/成本的逐行乘积由下面的语义声明授权 |
| `quantity` / `unit_price` / `unit_cost` | 同一订单上的 Measure；数量单位 1 且可加，单价/单件成本单位 CNY 且 non_additive；它们的临时乘积不自动获得求和许可 |
| `paid_at` / `posted_at` | 各自事实的观察时间，不能互换 |
| `orders_to_stores` / `refunds_to_orders` | 多对一、完整关联键；退款沿两跳路径归属门店 |

| store_id | region | channel |
| --- | --- | --- |
| S1 | East | Web |
| S2 | East | Store |
| S3 | West | Web |
| S4 | West | Store |

| order_id | store_id | paid_at（当地时间） | amount | quantity | unit_price | unit_cost |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| O1 | S1 | 2026-08-10 12:00 | 120 | 1 | 120 | 120 |
| O2 | S1 | 2026-08-11 12:00 | 80 | 1 | 80 | 80 |
| O3 | S2 | 2026-08-12 12:00 | 100 | 1 | 100 | 100 |
| O4 | S3 | 2026-08-13 12:00 | 60 | 1 | 60 | 60 |
| O5 | S1 | 2026-09-10 12:00 | 180 | 2 | 90 | 80 |
| O6 | S1 | 2026-09-11 12:00 | 20 | 1 | 20 | 20 |
| O7 | S2 | 2026-09-12 12:00 | 120 | 3 | 40 | 20 |
| O8 | S3 | 2026-09-13 12:00 | 100 | 1 | 100 | 100 |
| O9 | S3 | 2026-09-14 12:00 | 50 | 1 | 50 | 50 |

每单 amount 等于 quantity × unit_price，保留原金额列作为独立核对；本题销售毛利为
quantity × (unit_price − unit_cost)，未扣退款，不等于净额或完整经营利润。
9 月销售预算由分析师指定 East 300 CNY、West 200 CNY；达成率使用普通比值，不是构成份额。

| refund_id | order_id | posted_at（当地时间） | amount |
| --- | --- | --- | ---: |
| R1 | O1 | 2026-08-20 12:00 | 20 |
| R2 | O1 | 2026-09-15 12:00 | 70 |
| R3 | O1 | 2026-09-16 12:00 | 30 |
| R4 | O5 | 2026-09-17 12:00 | 10 |
| R5 | O3 | 2026-09-18 12:00 | 40 |

### 2.2 用新 DSL 表达

以下量与字段来自表中已批准模型；refund/order 的各自时间角色与关系保持独立。

```python
catalog = ms.load()
s = mv.session.create("scenario-commerce", timezone="Asia/Shanghai")
august = s.time_scope(start="2026-08-01", end="2026-09-01")
september = s.time_scope(start="2026-09-01", end="2026-10-01")
paid_at = catalog.fields.get("commerce.orders.paid_at")
posted_at = catalog.fields.get("commerce.refunds.posted_at")
region = catalog.fields.get("commerce.stores.region")
channel = catalog.fields.get("commerce.stores.channel")
sales = catalog.fields.get("commerce.orders.order_amount").sum(time=paid_at, empty="zero")
refunds = catalog.fields.get("commerce.refunds.refund_amount").sum(time=posted_at, empty="zero")
orders = catalog.entities.get("commerce.orders").count(time=paid_at)
metrics = {"sales": sales, "refunds": refunds, "orders": orders,
           "net": sales - refunds, "aov": sales / orders}
current = s.observe(metrics, by=(september, region)).compute()
baseline = s.observe(metrics, by=(august, region)).compute()
left = current.select({"net": current["net"]}).compute()
right = baseline.select({"net": baseline["net"]}).compute()
comparison = left.compare(right, on=tuple(zip(left.axes, right.axes)),
                          match=mv.ordinal()).compute()
negative = comparison.where(comparison["absolute.net"] < 0)
focus_frame = negative.top(1, by=negative["absolute.net"], descending=False).compute()
focus = s.population_from(focus_frame, on=((focus_frame.axes[1], region),))
focus.show()
current_detail = s.observe({'net': sales - refunds}, by=(september, region, channel), where=mv.in_population(focus)).compute()
baseline_detail = s.observe({'net': sales - refunds}, by=(august, region, channel), where=mv.in_population(focus)).compute()
detail = current_detail.compare(baseline_detail,
    on=tuple(zip(current_detail.axes, baseline_detail.axes)), match=mv.ordinal(), how="outer").compute()
budget = s.values(by=(region,), columns={"budget": "CNY"}, rows=(
    (("East",), {"budget": 300}), (("West",), {"budget": 200}),
)).compute()
regional_sales = current.reduce({"sales": current["sales"].sum()},
                                by=(current.axes[1],)).compute()
aligned = regional_sales.align(budget, on=((regional_sales.axes[0], budget.axes[0]),))
attainment = aligned.select({
    "attainment": aligned["left.sales"] / aligned["right.budget"],
}).compute()
detail.show()
attainment.show()
```

两根先各自按身份/时间/关系规约，再完整键组合，不能订单×多笔退款联接后求和。
focus 固定已选地区；后续渠道与金额是新来源读数，可能变化，不承诺与比较同一事务快照。
预算显式覆盖全部地区域，align/select 是普通同域数值运算，不是 broadcast 或贡献包含证明。

### 2.3 预期结果与反例

8 月销售 360、退款 20、净额 340；9 月销售 470、退款 150、净额 320。
销售增加 110，净额下降 20。East 净额从 280 到 170，下降 110；West 从 60 到 150，
增加 90。East 渠道 Web 净变 -110、Store 净变 0，不能重复分配父净变。
9 月 East/West 销售 320/150；预算达成率 320/300 与 150/200。
原贡献退款按 posted_at 而不是关联订单 paid_at 归期。

### 2.4 默认总体、分组占比与筛选顺序

同一固定销售表的 East/全部比值是 320/470=32/47；先只保留 East 再以所选表总额为分母则为 1。
这些固定数据算术的分母必须明确，不能据此声称恢复原贡献 share。
Frame 无该 receiver；需要新来源比值时显式声明受治理公式，外部固定数据计算保留输入与范围。

## 3. SaaS：固定客户群的扩张、收缩与流失

### 3.1 业务问题与数据

公司全体经常性月收入仅略降，客户成功团队怀疑新增收入掩盖了原客户流失。
要求固定“8 月经常性月收入大于零”的账户，计算 9 月 NRR、GRR、收入扩张/收缩/流失，
并取得本轮判为收入流失的账户身份，供下一次明确的使用量观察。

这里 MRR 是已经由语义层规范化的月度经常性金额：同账户同月的产品明细可以相加，
不包含一次性收费、按比例开票或未处理的币种差异。此题只选单个月，不跨月累加 MRR。
MRR 缺记录按数据契约代表该月无经常性贡献；若缺记录其实是未完成采集，不能套用下面的零值结论。

| Entity / 字段 | 粒度和约束 |
| --- | --- |
| `saas.accounts` | 完整主键 `(tenant_id, account_id)`；来源包括零收入账户 |
| `saas.recurring_lines` | 唯一 line_id，每行属于一个账户与一个月；金额非负 |
| `saas.recurring_lines.mrr_amount` | 可在单个月内按产品和账户相加的金额，单位 CNY |
| `saas.recurring_lines.month_at` | 本条月度事实所归属月份的时间角色 |
| `saas.lines_to_accounts` | 使用 tenant_id 和 account_id 两个分量关联，不能只连 account_id |

| tenant_id | account_id | 8 月 MRR | 9 月 MRR | 解释 |
| --- | --- | ---: | ---: | --- |
| t1 | A | 100 | 150 | 扩张 |
| t1 | B | 200 | 100 | 收缩 |
| t2 | A | 100 | 0 | 经常性收入流失 |
| t2 | C | 0 | 80 | 本期新增贡献，不足以区分新客与复活 |
| t1 | D | 0 | 50 | 本期新增贡献 |

正数格子各对应一条 recurring line，时间取对应月份 15 日 12:00；零格子没有 line。

### 3.2 固定主体与 pandas 后处理

```python
catalog = ms.load()
s = mv.session.create("scenario-saas", timezone="Asia/Shanghai")
august = s.time_scope(start="2026-08-01", end="2026-09-01")
september = s.time_scope(start="2026-09-01", end="2026-10-01")
Accounts = catalog.entities.get("saas.accounts")
mrr = catalog.fields.get("saas.recurring_lines.mrr_amount").sum(
    time=catalog.fields.get("saas.recurring_lines.month_at"), empty="zero",
)
cohort = mv.coordinates(
    by=(mv.identity(Accounts),),
    keys=((("t1", "A"),), (("t1", "B"),), (("t2", "A"),)),
)
baseline = s.observe({'mrr': mrr}, by=mv.product(august, cohort)).compute()
current = s.observe({'mrr': mrr}, by=mv.product(september, cohort)).compute()
change = current.compare(baseline, on=tuple(zip(current.axes, baseline.axes)),
                         match=mv.ordinal()).compute()
churned = change.where(change["left.mrr"] == 0).compute()
churned_accounts = s.population_from(churned, on=((churned.axes[1], Accounts),))
s.frame(churned_accounts).compute().show()
saas_export = change.to_pandas()
```

复合键不能按 account_id 单独去重。显式 empty="zero" 来自本题已批准的无月度贡献含义，
不是对未知缺失记录的通用补零。churned 来源于这份固定 change，不重新选择基期人群。
新使用量观察仍需显式 Metric、by 时间域与 Population 条件，不从结果恢复历史。

<!-- pandas-example:saas-readout -->
```python
import pandas as pd

pairs = saas_export["value"][["left.mrr", "right.mrr"]]
if pairs.isna().any().any():
    raise ValueError("The SaaS readout requires non-missing endpoint values.")
current_mrr, baseline_mrr = pairs["left.mrr"], pairs["right.mrr"]
retention_readout = {
    "nrr": current_mrr.sum() / baseline_mrr.sum(),
    "grr": current_mrr.clip(upper=baseline_mrr).sum() / baseline_mrr.sum(),
    "accounts": len(pairs),
}
movement = pd.Series("unchanged", index=pairs.index, dtype="string")
movement.loc[current_mrr > baseline_mrr] = "expansion"
movement.loc[current_mrr < baseline_mrr] = "contraction"
movement.loc[current_mrr == 0] = "churn"
```

### 3.3 预期结果与反例

固定三身份 {(t1,A),(t1,B),(t2,A)}，基期 400、本期 250，NRR 62.5%、GRR 50%。
扩张 50、收缩 100、流失 100，400+50-100-100=250。等权本期均值 250/3；
账户保留比例均值 2/3 不是 NRR。全体账户 380/400=95% 不能代替固定人群。
流失身份仅 (t2,A)，不能选仍扩张的 (t1,A)。收入零不证明 D7 事件流失或未来概率。

### 3.4 固定名单读取属性与精确版本选择

此补充场景使用独立的 `crm.customers`，不改变上面的非版本化 SaaS 账户模型。
业务完整主键只有 customer_id；物理版本行键为 (customer_id, valid_from)，不能把版本起点
加入业务主键冒充另一个客户。tier 是普通 Dimension，valid_from/valid_to 是同 Entity 的
时间字段。Entity 显式采用 Asia/Shanghai 的 closed_open validity，None 表示开放结束。
下表是本场景完整模拟数据，日期表示当地零点；它不是由执行成功推断的来源完整性。

| customer_id | valid_from | valid_to | tier |
| --- | --- | --- | --- |
| C1 | 2026-10-01 | 2026-11-01 | Silver |
| C1 | 2026-11-01 | None | Gold |
| C2 | 2026-10-01 | None | Bronze |
| C3 | 2026-11-01 | None | Gold |

使用已安装当前 Marivo 和 DuckDB 的同一环境，在独立项目目录运行下面的 seed_crm.py：

```python
from datetime import date
from pathlib import Path
import duckdb

Path("models/datasources").mkdir(parents=True, exist_ok=True)
Path("models/semantic/crm").mkdir(parents=True, exist_ok=True)
with duckdb.connect("crm.duckdb") as connection:
    connection.execute("""
        CREATE OR REPLACE TABLE customers (
            customer_id VARCHAR, valid_from DATE, valid_to DATE, tier VARCHAR
        )
    """)
    connection.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?)",
        [
            ("C1", date(2026, 10, 1), date(2026, 11, 1), "Silver"),
            ("C1", date(2026, 11, 1), None, "Gold"),
            ("C2", date(2026, 10, 1), None, "Bronze"),
            ("C3", date(2026, 11, 1), None, "Gold"),
        ],
    )
```

保存为 models/datasources/crm.py：

```python
import marivo.datasource as md

md.duckdb(name="crm", path="crm.duckdb", read_only=True)
```

保存为 models/semantic/crm/_domain.py；先捕获全部物理依赖，再 finalize/resolve：

```python
import marivo.datasource as md
import marivo.semantic as ms

ms.domain(name="crm", owner="Example owner", default=True)
customers = ms.entity(
    name="customers", datasource=ms.ref.datasource("crm"), source=md.table("customers"),
    primary_key=["customer_id"],
    versioning=ms.validity(
        valid_from=ms.ref.time_dimension("crm.customers.valid_from"),
        valid_to=ms.ref.time_dimension("crm.customers.valid_to"),
        interval="closed_open", open_end=(None,), timezone="Asia/Shanghai",
    ),
)
customer_id = ms.dimension_column(name="customer_id", entity=customers, column="customer_id")
tier = ms.dimension_column(name="tier", entity=customers, column="tier")
valid_from = ms.time_dimension_column(
    name="valid_from", entity=customers, column="valid_from", granularity="day",
)
valid_to = ms.time_dimension_column(
    name="valid_to", entity=customers, column="valid_to", granularity="day",
)
finalized = ms.finalize()
customers = finalized.resolve(customers)
customer_id = finalized.resolve(customer_id)
tier = finalized.resolve(tier)
valid_from = finalized.resolve(valid_from)
valid_to = finalized.resolve(valid_to)
```

模型文件保存后，从同一项目目录运行 analysis.py。固定 C1/C2，不从当前 Gold 人群重新选择：

```python
import marivo.analysis as mv
import marivo.semantic as ms

catalog = ms.load(workspace_dir=".")
attribute_s = mv.session.create("scenario-customer-versions", timezone="Asia/Shanghai")
customers_entity = catalog.entities.get("crm.customers")
tier = catalog.fields.get("crm.customers.tier")
october = attribute_s.time_scope(start="2026-10-01", end="2026-11-01")
fixed_customers = mv.coordinates(
    by=(mv.identity(customers_entity),), keys=((("C1",),), (("C2",),)),
)
attributes = attribute_s.read(fixed_customers, {
    "tier_at_start": tier.at(october.start),
    "tier_at_end": tier.at(october.end),
}).compute()
gold_values = attributes.where(attributes["tier_at_end"] == "Gold").compute()
gold_customers = attribute_s.population_from(
    gold_values, on=((gold_values.axes[0], customers_entity),),
)
gold_frame = attribute_s.frame(gold_customers).compute()
attributes.show()
gold_frame.show()
```

属性表保留 C1/C2，分别为 (Silver, Gold)、(Bronze, Bronze)；Gold 身份只有 C1，不含新增 C3。
at(october.end) 精确命中 11 月 1 日的新版本，按 valid_from ≤ t < valid_to 读取，不是期间
结束前的最后版本；不能用 end 减 tick 代替左极限。非版本化字段不能接受伪历史选择。
这些属性是明确锚点的新来源事实，不从旧结果恢复；固定名单不会因属性 where 重新选择。
若加入 C404，必需属性映射缺失应失败，不能删键或补 NA；来源不可用不能回退旧 Artifact。
Field Null 与缺失映射不同，原语义定义版本变化本身也不拒绝输入；业务版本身份与 at 锚点
仍保持各自检查。独立 pandas 连接也必须按完整身份和闭开区间验证每个锚点唯一匹配。

### 3.5 不同 Entity 的声明身份对应

任意身份关系 corresponding 与旧 endpoint 投影不属于 Frame 首版。需要关联新来源时显式
提供当前受治理 Population 关系/源输入。不能假装全轴 align 与该关系语义等价。

## 4. 制造：总体不良率与分层质量的反转

### 4.1 业务问题与数据

采购团队按总体不良率认为 B 厂不如 A 厂。但两家工厂承接的产品难度不同。
需要同时比较原始不良率、各型号不良率，以及统一产品结构后的不良率，避免结构差异误导决策。

`quality.inspections` 每行是一个被检件的本次检验，完整身份为 inspection_id；
plant/model/outcome 为单值 Dimension，tested_at 为检验时间。数据没有重复复检计数，
不把“检查次数的不良率”误称为“唯一产品的不良率”。下表是原始记录的无损压缩：
每格生成 inspected 条记录，其中 failed 条 outcome 为 `fail`，其余为 `pass`；
时间统一为 2026-09-15 12:00，身份互不重复。

| plant | model | inspected | failed | 不良率 |
| --- | --- | ---: | ---: | ---: |
| A | Easy | 900 | 90 | 10% |
| A | Hard | 100 | 30 | 30% |
| B | Easy | 100 | 5 | 5% |
| B | Hard | 900 | 180 | 20% |

标准结构选择这次已观察样本合并后的型号占比，作为本次计算的结构参照：Easy/Hard 各 50%。
这是一个明确的分析参照，不声称是行业或未来真实产品结构，也不由引擎自动选择。
另用业务预先指定的 Easy/Hard 20%/80% 做参照敏感性分析；两套权重分别命名，不覆盖原权重。

### 4.2 用新 DSL 表达

```python
catalog = ms.load()
s = mv.session.create("scenario-quality", timezone="Asia/Shanghai")
september = s.time_scope(start="2026-09-01", end="2026-10-01")
plant = catalog.fields.get("quality.inspections.plant")
model = catalog.fields.get("quality.inspections.model")
outcome = catalog.fields.get("quality.inspections.outcome")
tested_at = catalog.fields.get("quality.inspections.tested_at")
inspected = catalog.entities.get("quality.inspections").count(time=tested_at)
failed = inspected.where(outcome == "fail")
rate = failed.divide(inspected, zero_denominator="error")
plant_keys = mv.coordinates(by=(plant,), keys=(("A",), ("B",)))
model_keys = mv.coordinates(by=(model,), keys=(("Easy",), ("Hard",)))
cells = s.observe({'rate': rate}, by=mv.product(september, mv.product(plant_keys, model_keys))).compute()
pooled = s.observe({'rate': rate}, by=mv.product(september, plant_keys)).compute()
weights = s.values(by=(plant, model), columns={"weight": "1"}, rows=(
    (("A", "Easy"), {"weight": Decimal("0.5")}),
    (("A", "Hard"), {"weight": Decimal("0.5")}),
    (("B", "Easy"), {"weight": Decimal("0.5")}),
    (("B", "Hard"), {"weight": Decimal("0.5")}),
)).compute()
cell_values = cells.reduce({"rate": cells["rate"].mean()}, by=cells.axes[1:]).compute()
joined = cell_values.align(weights, on=tuple(zip(cell_values.axes, weights.axes)))
weighted = joined.select({"weighted_rate": joined["left.rate"] * joined["right.weight"]})
standardized = weighted.reduce({"standardized_rate": weighted["weighted_rate"].sum()},
                               by=(weighted.axes[0],)).compute()
pooled.show()
standardized.show()
```

单期间每个 (plant,model) 只有一个 rate；先以当前行 mean 移除 interval 轴，再对齐参照。
这不把多个期间的率均值当成原来源率。参照明确覆盖全部 (plant,model) 完整键；不是把 model-only 表自动广播。
pooled 是新的原来源公式，standardized 是显式固定参照上的当前格子加权和。
正权重层缺样本必须失败/明确改口径；NA 不可静默丢弃后重新归一化。

### 4.3 预期结果与反例

A 原始不良率 120/1000=12%，B 为 185/1000=18.5%；统一 50%/50% 后为 20%/12.5%。
原始 B 高 6.5 个百分点，标准结构 B 低 7.5 个百分点；不证明工厂造成质量差异。
20%/80% 参照给 26%/17%。实际字段 Null 与缺关系映射不同；指定域省略 Hard 时只返回所选键；精确总体分解仍须独立证明相关样本完整。
B/Hard 无样本时严格 rate 0/0 失败，不能跳过/补零后仍称原标准化率。

## 5. 物流：完整日网格、未送达件与 P95 上卷

### 5.1 业务问题与数据

运营团队要看 10 月 1—3 日各枢纽派发批次的时效，定位最差的枢纽日，再汇总整个窗口的 P95。
同时需要报告没有时长的件数，避免“只看已送达包裹”让结果显得过好。

`logistics.shipments` 每行一个包裹，shipment_id 唯一，hub 为单值字段，dispatched_at 为派发时间。
`delivery_hours` 是语义层提供的非负数值 Measure，单位 hour；已送达件记录实际经过小时数，
未得到有效送达时长的件为源 Null。允许均值和分位数，不允许把时长总和冒充平均时效。
本版尚未给来源时间差完成业务单位与执行资格，不因 Ibis 支持日期运算就虚构该时长的语义授权。
本题基于固定取样时点的数据，Null 不直接等同于超时、丢件或未送达的某一种原因。

| hub | dispatched_at（当地日期，均为 12:00） | 每件 delivery_hours |
| --- | --- | --- |
| H1 | 2026-10-01 | 1, 1, 1, 100 |
| H1 | 2026-10-02 | 2, 2 |
| H1 | 2026-10-03 | 无包裹 |
| H2 | 2026-10-01 | 3, 3 |
| H2 | 2026-10-02 | Null |
| H2 | 2026-10-03 | 无包裹 |

共 9 件、8 个有效时长；包裹按表格顺序赋予不重复身份。
统计含义是“按派发日分组，截至本次读取已获得的时长分布”，不是对全部包裹最终送达时间的无偏估计。

### 5.2 用新 DSL 表达

```python
catalog = ms.load()
s = mv.session.create("scenario-logistics", timezone="Asia/Shanghai")
hub = catalog.fields.get("logistics.shipments.hub")
dispatched_at = catalog.fields.get("logistics.shipments.dispatched_at")
duration = catalog.fields.get("logistics.shipments.delivery_hours")
window = s.time_scope(start="2026-10-01", end="2026-10-04")
days = window.by(mv.grain("day"))
hubs = mv.coordinates(by=(hub,), keys=(("H1",), ("H2",)))
p95 = duration.quantile(0.95, time=dispatched_at)
daily = s.observe({'shipments': catalog.entities.get('logistics.shipments').count(time=dispatched_at), 'timed': duration.count(time=dispatched_at), 'mean_hours': duration.mean(time=dispatched_at), 'p95_hours': p95}, by=mv.product(hubs, days)).compute()
known = daily.where(~daily["p95_hours"].is_null())
worst_days = known.top(2, by=known["p95_hours"]).compute()
by_hub = s.observe({'p95_hours': p95}, by=mv.product(window, hubs)).compute()
network = s.observe({'p95_hours': p95}, by=(window,)).compute()
logistics_export = daily.to_pandas()
worst_days.show()
```

<!-- pandas-example:logistics-quality -->
```python
if logistics_export["value"][["shipments", "timed"]].isna().any().any():
    raise ValueError("Quality totals require non-missing counts.")
counts = logistics_export["value"][["shipments", "timed"]].sum()
quality = {
    "grid_cells": len(logistics_export),
    "valid_p95_cells": int(logistics_export[("value", "p95_hours")].notna().sum()),
    "missing_durations": counts["shipments"] - counts["timed"],
    "missing_fraction": (counts["shipments"] - counts["timed"]) / counts["shipments"],
}
```

### 5.3 预期结果与反例

六格中三个有效 P95：H1/10-01=85.15、H1/10-02=2、H2/10-01=3；其它为 NA。
空日 shipments/timed=0/0，H2/10-02=1/0，不能只读 P95 区分。
缺时长 1 件占 1/9；Top-2 的精确顺序是 H1/10-01、H2/10-01。
Frame 无每枢纽 partitioned top；该问题可用明确外部数据分组处理，不能声称是通用 Top-2 等价。
H1 全窗 P95=75.5，全网=66.05（位置 (n-1)*0.95 插值）；日 P95 平均 30.05，
按有效件数加权 43.825 都不是整体。原均值=113/8=14.125。
目标粒度用显式新 observe，完成日 P95 不保存原样本，也没有原量上卷 receiver。

## 6. 产品实验：正确选择随机化单位和分析分母

### 6.1 业务问题与数据

某功能按 workspace 随机分配 A/B 版本。看板显示 B 的每会话收入显著高于 A，团队想判断是否值得推广。
核心分析先回答：按全部入组 workspace 等权的收入是否提升？零活动 workspace 是否保留？
总收入/总会话比和 workspace 平均收入为何给出不同印象？随后再要求不确定性评估。

| Entity / 字段 | 必要契约 |
| --- | --- |
| `experiment.workspaces` | workspace_id 唯一；experiment_id/arm 是固定分配属性；包含零活动成员 |
| `experiment.purchases` | 每笔收入一个 purchase_id，经 declared relationship 归属 workspace |
| `experiment.sessions` | 每次会话一个 session_id，经另一 relationship 归属 workspace |
| `purchase_amount` / `purchased_at` | 可加收入 CNY / 购买时间 |
| `started_at` | 会话时间；不能据同名字段混用为入组时间 |
| `preperiod_sessions` | workspace 上已固定的实验前会话数 Dimension；非负整数，可按大小比较，不能用实验后会话数替代 |

全部六个 workspace 在 9 月开始前已入组 `E1`；分配在本数据集保持不变。
收入正数各对应一笔 purchase；会话数展开为对应数量的不重复 session；所有事实时间为 9 月中旬。

| arm | workspace_id | revenue | sessions | preperiod_sessions |
| --- | --- | ---: | ---: | ---: |
| A | a1 | 100 | 100 | 10 |
| A | a2 | 0 | 1 | 0 |
| A | a3 | 0 | 0 | 0 |
| B | b1 | 80 | 2 | 10 |
| B | b2 | 20 | 2 | 0 |
| B | b3 | 0 | 0 | 0 |

补充分析预先规定“实验前会话数 > 0”为 active，否则 inactive；每个 workspace 只属于一个类别。
分类不改变整体收入指标的入组分母，也不依据看到的效果选择阈值。

这些数据用于检验分析单位；不声称六个模拟 workspace 足以支持真实推广决策或随机化质量验收。

### 6.2 用新 DSL 表达

```python
catalog = ms.load()
s = mv.session.create("scenario-experiment", timezone="Asia/Shanghai")
september = s.time_scope(start="2026-09-01", end="2026-10-01")
arm = catalog.fields.get("experiment.workspaces.arm")
experiment_id = catalog.fields.get("experiment.workspaces.experiment_id")
preperiod = catalog.fields.get("experiment.workspaces.preperiod_sessions")
Workspaces = catalog.entities.get("experiment.workspaces")
cohort = s.members(Workspaces, where=experiment_id == "E1")
revenue = catalog.fields.get("experiment.purchases.purchase_amount").sum(
    time=catalog.fields.get("experiment.purchases.purchased_at"), empty="zero",
)
sessions = catalog.entities.get("experiment.sessions").count(
    time=catalog.fields.get("experiment.sessions.started_at"),
)
unit_domain = cohort.classify(by=(arm, preperiod, cohort.axes[0]))
units = s.observe({'revenue': revenue, 'sessions': sessions},
                  by=mv.product(september, unit_domain)).compute()
a_units = units.where(units.coord(units.axes[1]) == "A")
b_units = units.where(units.coord(units.axes[1]) == "B")
a_mean = a_units.reduce({"mean_workspace_revenue": a_units["revenue"].mean()})
b_mean = b_units.reduce({"mean_workspace_revenue": b_units["revenue"].mean()})
descriptive_lift = b_mean.compare(a_mean).compute()
experiment_export = units.to_pandas()
```

零轴均值比较无需重复 on=()；固定 units 的分支不重新选择 cohort 或重读来源。

<!-- pandas-example:experiment-readout -->
```python
import pandas as pd

if experiment_export["value"][["revenue", "sessions"]].isna().any().any():
    raise ValueError("The experiment summary requires non-missing unit-level outcomes.")
units_frame = experiment_export["value"][["revenue", "sessions"]].copy()
units_frame["arm"] = experiment_export[("coordinate", units.axes[1].token)]
activity = experiment_export[("coordinate", units.axes[2].token)]
if activity.isna().any():
    raise ValueError("This example requires a known preperiod activity class.")
units_frame["baseline_activity"] = pd.Series(
    "inactive", index=units_frame.index, dtype="string",
).mask(activity > 0, "active")
group_stats = units_frame.groupby("arm", dropna=False).agg(
    workspaces=("revenue", "size"), revenue=("revenue", "sum"),
    sessions=("sessions", "sum"), mean_workspace_revenue=("revenue", "mean"),
    p90_workspace_revenue=("revenue", lambda values: values.quantile(0.9)),
)
group_stats["revenue_per_session"] = group_stats["revenue"] / group_stats["sessions"]
```

### 6.3 预期结果与反例

A/B 各 3 个 workspace、收入各 100、等权均值各 100/3，提升为 0。
会话数 101/4，每会话收入 100/101 与 25，比值 25.25 不是随机化单位提升。
P90 为 80/68；active 层均值 100/80，inactive 层 0/10，不可将层均值等权替代原人数权重。
置信区间、p 值、cluster bootstrap 需要显式外部统计方法与不确定性假设。
Population 按完整身份去重，不能把重复抽样的 workspace 合并后伪装 bootstrap。

## 7. 时间分析：滚动、累计与期间比较

### 7.1 语义输入与可复算数据

业务要按天看七日收入、七日客单价、累计收入、上月同进度变化和七日同比；
另以物流 P95 验证不具备小型可合并摘要的指标。预测不在本次需求中。

sales 使用[多轮示例 §2.1](lazy-analysis-dsl-examples.md#21-只声明-orders-entity客户-id-是普通维度) 已完整列出的同一组语义声明：
Orders 一行一单、order_id 唯一；Amount 单位 CNY 且可加；PaidAt 为带时区支付时刻；
revenue 是 paid 订单金额 sum(empty="zero")，忽略源 Null、空贡献为 0；order_count 计 paid 订单；
sales.average_order_value 始终声明为 revenue / order_count，统一默认 null；本场景不为同一全名另设定义。
需要拒绝空日时使用[示例 §7.2 的 aov_strict](lazy-analysis-dsl-examples.md#72-aov所有除法共享一个含义) 独立声明。
指标及 Query 节点保留各自政策，Artifact 只保存完成值；政策归属见[语义提案§3.4](../semantic/expression-model-redesign.md#34-样本政策的归属)。
Analysis 不从“收入”列名猜时间角色、paid 条件或空值规则，也不要求 Metric 作者提供 merge 状态。

| 每月日号 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-10 收入 | 100 | 900 | 0 | 40 | 50 | 60 | 70 | 80 |
| 订单数 | 1 | 99 | 0 | 1 | 1 | 1 | 1 | 1 |
| 2026-09 收入 | 50 | 450 | 0 | 20 | 25 | 30 | 35 | 40 |
| 2025-10 收入 | 25 | 225 | 0 | 10 | 12.5 | 15 | 17.5 | 20 |

三个月订单数均取表中同一行；每天第 1 单承载全部日收入，其余订单金额为 0。
另有 2026-09-26—30 每日一单、每单 10 元。所有订单 status=paid，时间为当地 12:00，
以 `(年月日, 日内序号)` 生成唯一 order_id，customer_id/region 可固定为 C1/East。
模拟数据在 `[2025-01-01,2026-10-09)` 内其他日期没有订单；这是本例数据集的设定。
观察默认信任声明的数据源，不需要逐次完整性承诺，也不生成审计结论。
10-03 是空日，收入按显式 sum(empty="zero") 规则为 0，不能从日网格删除。

### 7.2 直接滚动与累计求值

```python
catalog = ms.load()
s = mv.session.create("scenario-temporal-sales", timezone="Asia/Shanghai")
income = catalog.metrics.get("sales.revenue")
display = s.time_scope(start="2026-10-07", end="2026-10-09")
days = display.by(mv.grain("day"))
rolling = s.observe({'income': income.window(mv.trailing_periods(count=7))}, by=(days,)).compute()
mtd = s.observe({'income': income.window(mv.to_date(grain=mv.grain('month')))}, by=(days,)).compute()
ytd = s.observe({'income': income.window(mv.to_date(grain=mv.grain('year')))}, by=(days,)).compute()
joined = mtd.align(ytd)
cumulative = joined.select({"mtd": joined["left.income"], "ytd": joined["right.income"]}).compute()
rolling.show()
cumulative.show()
```

10-07/10-08 七日收入 1220/1200、订单数 104/104、AOV 305/26 与 150/13；
MTD 1220/1300、YTD/since-January 1920/2000。显示起点不截断回看；七民用日不一般等于 168 小时。

### 7.3 同比环比：先计算两个端点，再按期间对应

唯一日历轴可推导 on，match 仍显式。ordinal 用原完整名义序号；builtin period_offset
支持 day/week/month/quarter/year，previous_year 支持 day/month/quarter/year 且闰日无对应。
完整名义分区/实际边界准入先于 actual-row exact/outer。过滤不重新编号；未用目录桶不产生行。
partial 桶在 exact/outer 均拒绝；命名日历仅 ordinal，子日/DST 和 named offsets 延期。
保存双方冻结目录与配对 map，不加载模型/TZ，也不新增 endpoint 或 current-only 投影。

### 7.4 终端窗口产物与日格子统计

完成窗口值可做当前行 mean/sum；不是把旧贡献重新观察，也不自动变更窗口。
source-read、evaluation 与 commit 时点分别解释新鲜度；完整读验证数据，n=0/audit 不读载荷。

### 7.5 七日 P95：直接样本与固定充分信息

单次来源请求可直接取得全部合格样本并求 P95；保存的每日完成 P95 不能恢复整体分布。
无内部 merge 摘要不否定新来源直接求值。缺少原样本时必须新观察，不能用完成分位数假等价。

### 7.6 同一语义日历用于财务累计与业务期间对应

命名日历规则是明确语义依赖，snapshot-bound Grain 必须匹配 Session 冻结认证作用域。
保存目录保留原 identity/digest/level/timezone；ordinal 解释旧数据不重新取得当前认证。

### 7.7 边界反例与信息下界

两个七日端点各为 7，当前行和 14、贡献并集 8、末端点窗口 7 是不同问题。
完整标签不证明实际范围覆盖完整桶；删除行也不能隐藏 partial 结构。目录损坏是错误，不是无对应。

### 7.8 采样带宽 P95 与依赖指标

先逐时点空间总和再对时间样本求 P95，与每设备 P95 求和不同。新目标问题明确声明
Metric.per 的完整子公式/样本域；完成结果不保存隐含样本或授予原来源重算。

### 7.9 比率贡献与加权均值状态

来源加权值/权重使用一个共同有效 mask；Frame 多项 reduce 各自独立 mask。
没有完整原贡献时，不从已见 ratio 反推分子/分母或用列名恢复充分状态。

### 7.10 Session 时间默认、独立起点与版本边界

Session 报告时区、source parser 时区与日历时区分离。instant/time_scope 冻结明确 UTC 边界；
aware 值保留瞬时，naive datetime/歧义时刻拒绝。改变期间不覆盖已绑定属性 at。

### 7.11 观察期间省略与关系元组

by=() 表示没有输出时间轴，不证明完整历史；需要端点的时间样本/窗口必须显式提供 by 时间域。
联合元组桥接不能分列去重再笛卡尔积，普通 Field Null 保留，Entity 身份不空。

### 7.12 截断月桶的原指标上卷与月内份额

Frame 首版没有 calendar coarsen/原量 rollup/share。显式新的月来源观察与当前日行统计是
不同程序，不声称有无损机械迁移。附录的旧算式只作为独立数学范围反例。

## 8. 对设计的综合判断与改进顺序

### 8.7 构造披露、执行后阅读与重开

```python
saved = rolling.persist()
saved.show(n=0)
saved.artifact.show()
restored = s.persisted_frame(saved.artifact.id)
restored.show(n=2)
restored.reduce({"rows": mv.row_count()}).compute().show()
restored.artifact.evidence_digest().show()
```

Pending show 不执行；n=0/Artifact audit 不消费 payload；正 n 只解码前缀和所需冻结坐标，
hash/解压可能读更多字节。full compute/export 验证完整必要数据。导出 raw-key MultiIndex、
隔离 axes/units/frozen_coordinates attrs，不新增可见投影列、不接 attrs 导入。

## 9. 后续实现的最小验收集

### 9.1 最小反例与修复判据

独立 expected 值、完整键顺序、NA/presence 和来源读取边界分别核验。
必须保留 55 vs 1000/91、distinct 4 vs 3、固定 Top 源变化不重排、零轴空/单例、联合键投影、
paired/cold/TZ-independent 解释、元数据与 full read 区别、发布前求值失败无 Run，以及
旧 generation 全部数据拒读且旧字节不变。不得用实现自己的输出互证 oracle。

### 9.2 终端 Artifact 的验收

Artifact 只从 persisted Frame 取得审计，无 n/to_pandas/DSL。Frame 的新/重开/内存载体
数据运算一致；存储 persist metadata-idempotent，内存重复发布不重算。恢复用精确字符串 ID。

### 9.3 简化执行路线的验收

一套 schedule 处理 source/literal/fixed/local，严格义务先于 pruning；共享输入按消费者计数。
固定数据参数运输不继承历史 Query fusion 保证，完整 source/per 数值核保持各自 owner。

### 9.4 原生 Ibis 表达式与自动延迟类型绑定

语义构造/物理 readiness/来源实际 method 资格分离。原生 precision loss 按 backend 合同接受，
pandas 交接保留实际有效类型，键/时间/单位/NA 不放宽。未知 operation/回调 fail closed。

### 9.5 五项能力补全与等价迁移

Frame 数据运算与显式来源重建是边界调整，不宣称旧原量/关系/广播/粗化全部无损替代。
Native sum/mean/weighted/ratio 已有多 provider 代码路线；新 Frame Runtime 资格仍需实际运行。

### 9.6 Fanout 语义与执行策略边界

同贡献/同桶去重、不同身份等值不合并，多根先独立完成再组合，by coverage 不能用 inner join
偷偷丢贡献。source-only relation optimizer 与本地 Frame carrier 是独立验收边界。

以下附录保留既有独立数学、集合与外部 pandas oracle 及其历史核验记录。历史版本 API
名称/旧 handoff 模拟只解释当时的见证，不是当前 Frame 调用、真实 exporter 或新 Runtime 资格。
当前接口以本页正文、最终合同与 native Help 为准；不重标旧运行结果为本次验收。

## 附录 A：可复现的独立数值 oracle

此脚本只验证数据、算式与信息不足反例。`None` 在脚本中表示“没有可供本次数值运算的值”，
这些历史见证不实现当前 nullable/失败合同，也不验证真实异常类型。源码、注释、输出均使用英文。
其中 temporal.coverage 的五项只验证显式日期集合的包含关系，保留历史数值证据，不再对应首版的外部业务缺口能力或验收。

旧入口见证中的 `entry.explicit.member.keys` 只验证显式身份域的字段分类；
当前提案须以 by=M.classify(by=fields) 明确请求这个输出域，不解释为 members 参数的默认分组行为。

<!-- oracle:start -->
```python
from collections import Counter
from fractions import Fraction as F
from itertools import product

checks: list[str] = []


def check(name: str, actual: object, expected: object) -> None:
    assert actual == expected, (name, actual, expected)
    checks.append(name)


def quantile(values: list[F], q: F) -> F | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lo = position.numerator // position.denominator
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (position - lo) * (ordered[hi] - ordered[lo])


def mean(values: list[F]) -> F:
    return sum(values, F(0)) / len(values)


stores = {
    "S1": ("East", "Web"), "S2": ("East", "Store"),
    "S3": ("West", "Web"), "S4": ("West", "Store"),
}
orders = [
    ("O1", "S1", 8, 120), ("O2", "S1", 8, 80),
    ("O3", "S2", 8, 100), ("O4", "S3", 8, 60),
    ("O5", "S1", 9, 180), ("O6", "S1", 9, 20),
    ("O7", "S2", 9, 120), ("O8", "S3", 9, 100),
    ("O9", "S3", 9, 50),
]
refunds = [
    ("R1", "O1", 8, 20), ("R2", "O1", 9, 70),
    ("R3", "O1", 9, 30), ("R4", "O5", 9, 10),
    ("R5", "O3", 9, 40),
]
order_store = {oid: store for oid, store, _, _ in orders}


def commerce(month: int, selected_stores: set[str]) -> tuple[int, int, int, int]:
    paid = [a for _, store, m, a in orders if m == month and store in selected_stores]
    returned = [
        a for _, oid, m, a in refunds
        if m == month and order_store[oid] in selected_stores
    ]
    return sum(paid), sum(returned), len(paid), sum(paid) - sum(returned)


east = {"S1", "S2"}
west = {"S3", "S4"}
check("commerce.aug.east", commerce(8, east), (300, 20, 3, 280))
check("commerce.sep.east", commerce(9, east), (320, 150, 3, 170))
check("commerce.aug.west", commerce(8, west), (60, 0, 1, 60))
check("commerce.sep.west", commerce(9, west), (150, 0, 2, 150))
check("commerce.aug.total", commerce(8, set(stores)), (360, 20, 4, 340))
check("commerce.sep.total", commerce(9, set(stores)), (470, 150, 5, 320))
check("commerce.east.delta", commerce(9, east)[3] - commerce(8, east)[3], -110)
check("commerce.west.delta", commerce(9, west)[3] - commerce(8, west)[3], 90)
check("commerce.total.delta", commerce(9, set(stores))[3] - commerce(8, set(stores))[3], -20)
check("commerce.east.web", commerce(9, {"S1"})[3], 90)
check("commerce.east.store", commerce(9, {"S2"})[3], 80)
check("commerce.east.web.delta", commerce(9, {"S1"})[3] - commerce(8, {"S1"})[3], -90)
check("commerce.east.store.delta", commerce(9, {"S2"})[3] - commerce(8, {"S2"})[3], -20)
check("commerce.zero.store", commerce(9, {"S4"}), (0, 0, 0, 0))
check("commerce.sales.share", F(commerce(9, east)[0], commerce(9, set(stores))[0]), F(32, 47))
check("commerce.aov.aug", F(commerce(8, set(stores))[0], commerce(8, set(stores))[2]), F(90))
check("commerce.aov.sep", F(commerce(9, set(stores))[0], commerce(9, set(stores))[2]), F(94))
check("commerce.all.sales", sum(a for _, _, _, a in orders), 830)
refund_multiplicity = Counter(oid for _, oid, _, _ in refunds)
fanout_sales = sum(a * max(1, refund_multiplicity[oid]) for oid, _, _, a in orders)
check("commerce.fanout.wrong", fanout_sales, 1070)
sep_order_ids = {oid for oid, _, month, _ in orders if month == 9}
wrong_sep_refunds = sum(a for _, oid, month, a in refunds if month == 9 and oid in sep_order_ids)
check("commerce.refund.wrong.scope", wrong_sep_refunds, 10)

accounts = {
    ("t1", "A"): (100, 150), ("t1", "B"): (200, 100),
    ("t2", "A"): (100, 0), ("t2", "C"): (0, 80),
    ("t1", "D"): (0, 50),
}
cohort = {key: value for key, value in accounts.items() if value[0] > 0}
base = sum(a for a, _ in cohort.values())
cur = sum(b for _, b in cohort.values())
expansion = sum(max(b - a, 0) for a, b in cohort.values())
contraction = sum(a - b for a, b in cohort.values() if 0 < b < a)
churn = sum(a for a, b in cohort.values() if b == 0)
capped = sum(F(a + b - abs(a - b), 2) for a, b in cohort.values())
check("saas.cohort", set(cohort), {("t1", "A"), ("t1", "B"), ("t2", "A")})
check("saas.base", base, 400)
check("saas.current", cur, 250)
check("saas.nrr", F(cur, base), F(5, 8))
check("saas.grr", capped / base, F(1, 2))
check("saas.expansion", expansion, 50)
check("saas.contraction", contraction, 100)
check("saas.churn", churn, 100)
check("saas.bridge", base + expansion - contraction - churn, cur)
check("saas.all.accounts.ratio", F(sum(b for _, b in accounts.values()), base), F(19, 20))
check("saas.mean.mrr", F(cur, len(cohort)), F(250, 3))
check("saas.mean.account.ratio", mean([F(b, a) for a, b in cohort.values()]), F(2, 3))
check("saas.identity.count", len(cohort), 3)
check("saas.partial.key.wrong", len({account for _, account in cohort}), 2)
check("saas.churn.identities", {key for key, (_, b) in cohort.items() if b == 0}, {("t2", "A")})

inspection_spec = {
    ("A", "Easy"): (900, 90), ("A", "Hard"): (100, 30),
    ("B", "Easy"): (100, 5), ("B", "Hard"): (900, 180),
}
inspections = [
    (plant, model, i, i < failed)
    for (plant, model), (count, failed) in inspection_spec.items()
    for i in range(count)
]
sizes = Counter((plant, model) for plant, model, _, _ in inspections)
failures = Counter((plant, model) for plant, model, _, bad in inspections if bad)
rates = {key: F(failures[key], n) for key, n in sizes.items()}
model_sizes = Counter(model for _, model, _, _ in inspections)
weights = {model: F(n, len(inspections)) for model, n in model_sizes.items()}
pooled_rates = {
    plant: F(sum(failures[(plant, m)] for m in weights), sum(sizes[(plant, m)] for m in weights))
    for plant in ("A", "B")
}
standardized = {
    plant: sum((rates[(plant, m)] * w for m, w in weights.items()), F(0))
    for plant in ("A", "B")
}
check("quality.inspections", len(inspections), 2000)
check("quality.unique.identities", len({(p, m, i) for p, m, i, _ in inspections}), 2000)
check("quality.weights", weights, {"Easy": F(1, 2), "Hard": F(1, 2)})
check("quality.pooled.a", pooled_rates["A"], F(3, 25))
check("quality.pooled.b", pooled_rates["B"], F(37, 200))
check("quality.standard.a", standardized["A"], F(1, 5))
check("quality.standard.b", standardized["B"], F(1, 8))
check("quality.raw.difference", pooled_rates["B"] - pooled_rates["A"], F(13, 200))
check("quality.standard.difference", standardized["B"] - standardized["A"], -F(3, 40))
check("quality.each.stratum", all(rates[("B", m)] < rates[("A", m)] for m in weights), True)
check("quality.positive.missing.weight", weights["Hard"] > 0, True)
missing_case = [row for row in inspections if row[:2] != ("B", "Hard")]
missing_n = sum(row[:2] == ("B", "Hard") for row in missing_case)
missing_failed = sum(row[:2] == ("B", "Hard") and row[3] for row in missing_case)
missing_rate = F(missing_failed, missing_n) if missing_n else None
check("quality.missing.stratum", (missing_n, missing_failed, missing_rate), (0, 0, None))

duration_groups: dict[tuple[str, int], list[int | None]] = {
    ("H1", 1): [1, 1, 1, 100], ("H1", 2): [2, 2], ("H1", 3): [],
    ("H2", 1): [3, 3], ("H2", 2): [None], ("H2", 3): [],
}
grid = set(product(("H1", "H2"), (1, 2, 3)))
defined = {key: [F(x) for x in values if x is not None] for key, values in duration_groups.items()}
p95 = {key: quantile(values, F(95, 100)) for key, values in defined.items()}
network_values = [x for values in defined.values() for x in values]
h1_values = [x for (hub, _), values in defined.items() if hub == "H1" for x in values]
valid_daily_p95 = [v for v in p95.values() if v is not None]
check("logistics.grid", set(duration_groups), grid)
check("logistics.grid.count", len(grid), 6)
check("logistics.shipments", sum(map(len, duration_groups.values())), 9)
check("logistics.known", len(network_values), 8)
check("logistics.missing.fraction", F(9 - len(network_values), 9), F(1, 9))
check("logistics.defined.cells", len(valid_daily_p95), 3)
check("logistics.empty.cells", sum(not values for values in duration_groups.values()), 2)
check("logistics.all.null.count", (len(duration_groups[("H2", 2)]), len(defined[("H2", 2)])), (1, 0))
check("logistics.empty.count", (len(duration_groups[("H2", 3)]), len(defined[("H2", 3)])), (0, 0))
check("logistics.h1.day1.p95", p95[("H1", 1)], F(1703, 20))
check("logistics.h1.p95", quantile(h1_values, F(95, 100)), F(151, 2))
check("logistics.network.p95", quantile(network_values, F(95, 100)), F(1321, 20))
check("logistics.mean", mean(network_values), F(113, 8))
check("logistics.daily.p95.mean.wrong", mean(valid_daily_p95), F(601, 20))
weighted_p95 = sum(v * len(defined[key]) for key, v in p95.items() if v is not None) / len(network_values)
check("logistics.daily.p95.weighted.wrong", weighted_p95, F(1753, 40))
ranked_p95 = [(key, value) for key, value in p95.items() if value is not None]
top_days = [key for key, _ in sorted(ranked_p95, key=lambda pair: (-pair[1], pair[0]))]
check("logistics.top.two", set(top_days[:2]), {("H1", 1), ("H2", 1)})
left = [F(0), F(0), F(10)]
right = [F(0), F(9), F(9)]
check("logistics.same.daily.p95", (quantile(left, F(95, 100)), quantile(right, F(95, 100))), (F(9), F(9)))
check("logistics.pooled.witness.left", quantile(left + [F(100)], F(95, 100)), F(173, 2))
check("logistics.pooled.witness.right", quantile(right + [F(100)], F(95, 100)), F(1727, 20))

experiment = {
    "A": [("a1", 100, 100), ("a2", 0, 1), ("a3", 0, 0)],
    "B": [("b1", 80, 2), ("b2", 20, 2), ("b3", 0, 0)],
}
means = {arm: mean([F(r) for _, r, _ in rows]) for arm, rows in experiment.items()}
session_ratios = {arm: F(sum(r for _, r, _ in rows), sum(n for _, _, n in rows)) for arm, rows in experiment.items()}
check("experiment.unit.count", {arm: len(rows) for arm, rows in experiment.items()}, {"A": 3, "B": 3})
check("experiment.zero.units", {arm: sum(n == 0 for _, _, n in rows) for arm, rows in experiment.items()}, {"A": 1, "B": 1})
check("experiment.means", means, {"A": F(100, 3), "B": F(100, 3)})
check("experiment.absolute.lift", means["B"] - means["A"], F(0))
check("experiment.relative.lift", (means["B"] - means["A"]) / abs(means["A"]), F(0))
check("experiment.session.ratios", session_ratios, {"A": F(100, 101), "B": F(25)})
check("experiment.session.ratio.multiple", session_ratios["B"] / session_ratios["A"], F(101, 4))
check("experiment.session.relative", session_ratios["B"] / session_ratios["A"] - 1, F(97, 4))
check("experiment.p90.a", quantile([F(r) for _, r, _ in experiment["A"]], F(9, 10)), F(80))
check("experiment.p90.b", quantile([F(r) for _, r, _ in experiment["B"]], F(9, 10)), F(68))
weighted_workspace = {
    arm: F(sum(r * n for _, r, n in rows), sum(n for _, _, n in rows))
    for arm, rows in experiment.items()
}
check("experiment.session.weighted.wrong", weighted_workspace, {"A": F(10000, 101), "B": F(50)})
draws = ["a1", "a1", "a2"]
values_by_id = {wid: r for wid, r, _ in experiment["A"]}
check("experiment.bootstrap.appearances", len(draws), 3)
check("experiment.bootstrap.dedup.wrong", len(set(draws)), 2)
check("experiment.bootstrap.mean", mean([F(values_by_id[wid]) for wid in draws]), F(200, 3))
check("experiment.bootstrap.dedup.mean.wrong", mean([F(values_by_id[wid]) for wid in set(draws)]), F(50))

# v0.12 witnesses; the original 81 checks above remain unchanged.
order_inputs = {
    "O1": (1, 120, 120), "O2": (1, 80, 80), "O3": (1, 100, 100),
    "O4": (1, 60, 60), "O5": (2, 90, 80), "O6": (1, 20, 20),
    "O7": (3, 40, 20), "O8": (1, 100, 100), "O9": (1, 50, 50),
}
row_amounts = {oid: qty * price for oid, (qty, price, _) in order_inputs.items()}
row_margins = {oid: qty * (price - cost) for oid, (qty, price, cost) in order_inputs.items()}
check("commerce.row.amounts", row_amounts, {oid: amount for oid, _, _, amount in orders})
check("commerce.row.margins", {oid: v for oid, v in row_margins.items() if v}, {"O5": 20, "O7": 60})
check("commerce.row.margin.total", sum(row_margins[oid] for oid in sep_order_ids), 80)
margin_rows = [order_inputs[oid] for oid in ("O5", "O7")]
wrong_margin = sum(qty for qty, _, _ in margin_rows) * sum(price - cost for _, price, cost in margin_rows)
check("commerce.margin.product.of.sums.wrong", wrong_margin, 150)
budgets = {"East": 300, "West": 200}
check("commerce.budget.east", F(commerce(9, east)[0], budgets["East"]), F(16, 15))
check("commerce.budget.west", F(commerce(9, west)[0], budgets["West"]), F(3, 4))
check("commerce.budget.total", F(commerce(9, set(stores))[0], sum(budgets.values())), F(47, 50))

check("saas.pointwise.minimum", sum(min(a, b) for a, b in cohort.values()), capped)
check("saas.pointwise.maximum", sum(max(b - a, 0) for a, b in cohort.values()), 50)
labels = {
    key: "churn" if b == 0 else "expansion" if b > a else "contraction" if b < a else "unchanged"
    for key, (a, b) in cohort.items()
}
check("saas.movement.labels", labels, {("t1", "A"): "expansion", ("t1", "B"): "contraction", ("t2", "A"): "churn"})
check("saas.movement.counts", dict(Counter(labels.values())), {"expansion": 1, "contraction": 1, "churn": 1})
movement_net = {label: sum(b - a for key, (a, b) in cohort.items() if labels[key] == label) for label in set(labels.values())}
movement_amount = {label: sum(abs(b - a) for key, (a, b) in cohort.items() if labels[key] == label) for label in set(labels.values())}
check("saas.movement.net", movement_net, {"expansion": 50, "contraction": -100, "churn": -100})
check("saas.movement.amount", movement_amount, {"expansion": 50, "contraction": 100, "churn": 100})

planning_weights = {"Easy": F(1, 5), "Hard": F(4, 5)}
planning_rates = {plant: sum(rates[(plant, m)] * w for m, w in planning_weights.items()) for plant in ("A", "B")}
check("quality.planning.weights.sum", sum(planning_weights.values()), F(1))
check("quality.planning.weights.positive", all(w > 0 for w in planning_weights.values()), True)
check("quality.planning.rates", planning_rates, {"A": F(13, 50), "B": F(17, 100)})
check("quality.planning.difference", planning_rates["B"] - planning_rates["A"], -F(9, 100))

per_hub = {
    next(key for key in top_days if key[0] == hub)
    for hub in ("H1", "H2")
}
check("logistics.partition.actual", per_hub, {("H1", 1), ("H2", 1)})
tie_rows = [("H1", "A", 90), ("H1", "B", 90), ("H2", "A", 80), ("H2", "B", 70)]
global_keys = [(h, day) for h, day, _ in sorted(tie_rows, key=lambda row: (-row[2], row[:2]))[:2]]


def first_per_hub(rows: list[tuple[str, str, int]], *, descending: bool = True) -> set[tuple[str, str]]:
    factor = -1 if descending else 1
    return {
        min((row for row in rows if row[0] == hub), key=lambda row: (factor * row[2], row[:2]))[:2]
        for hub in {row[0] for row in rows}
    }


check("logistics.partition.global.witness", global_keys, [("H1", "A"), ("H1", "B")])
check("logistics.partition.group.witness", first_per_hub(tie_rows), {("H1", "A"), ("H2", "A")})
check("logistics.partition.stable.ties", first_per_hub(list(reversed(tie_rows))), {("H1", "A"), ("H2", "A")})
check("logistics.partition.ascending", first_per_hub(tie_rows, descending=False), {("H1", "A"), ("H2", "B")})

preperiod = {"a1": 10, "a2": 0, "a3": 0, "b1": 10, "b2": 0, "b3": 0}
strata = {
    (arm, label): [F(revenue) for wid, revenue, _ in rows if ("active" if preperiod[wid] > 0 else "inactive") == label]
    for arm, rows in experiment.items() for label in ("active", "inactive")
}
stratum_counts = {key: len(values) for key, values in strata.items()}
stratum_means = {key: mean(values) for key, values in strata.items()}
check("experiment.preperiod.counts", stratum_counts, {("A", "active"): 1, ("A", "inactive"): 2, ("B", "active"): 1, ("B", "inactive"): 2})
check("experiment.preperiod.means", stratum_means, {("A", "active"): F(100), ("A", "inactive"): F(0), ("B", "active"): F(80), ("B", "inactive"): F(10)})
recombined = {arm: sum(stratum_means[(arm, label)] * stratum_counts[(arm, label)] for label in ("active", "inactive")) / len(rows) for arm, rows in experiment.items()}
check("experiment.preperiod.recombined", recombined, means)
unweighted_strata = {arm: mean([stratum_means[(arm, label)] for label in ("active", "inactive")]) for arm in experiment}
check("experiment.preperiod.unweighted.wrong", unweighted_strata, {"A": F(50), "B": F(45)})

# Review repair witnesses; the preceding 107 checks remain unchanged.
row_costs = {oid: qty * cost for oid, (qty, _, cost) in order_inputs.items()}
check("commerce.authorized.cost.total", sum(row_costs[oid] for oid in sep_order_ids), 390)
check("commerce.authorized.margin.rows", {oid: row_amounts[oid] - row_costs[oid] for oid in order_inputs}, row_margins)
conditional_margins = [amount - cost for amount, cost in [(180, 200), (120, 60)]]
check("commerce.authorized.case.before.sum", sum(m if m > 0 else 0 for m in conditional_margins), 60)
check("commerce.authorized.case.after.sum.wrong", max(sum(conditional_margins), 0), 40)
group_current = {label: sum(b for key, (_, b) in cohort.items() if labels[key] == label) for label in set(labels.values())}
group_baseline = {label: sum(a for key, (a, _) in cohort.items() if labels[key] == label) for label in set(labels.values())}
check("saas.shared.classification.current", group_current, {"expansion": 150, "contraction": 100, "churn": 0})
check("saas.shared.classification.baseline", group_baseline, {"expansion": 100, "contraction": 200, "churn": 100})
check("saas.shared.classification.difference", {label: group_current[label] - group_baseline[label] for label in group_current}, movement_net)

# v0.13 temporal witnesses; the preceding 114 checks remain unchanged.
from datetime import date as D, datetime as DT, timedelta as TD, timezone as TZ
from zoneinfo import ZoneInfo

day_amounts = [100, 900, 0, 40, 50, 60, 70, 80]
day_counts = [1, 99, 0, 1, 1, 1, 1, 1]
time_orders: list[tuple[str, D, F]] = []
for year, month, factor in [(2026, 10, F(1)), (2026, 9, F(1, 2)), (2025, 10, F(1, 4))]:
    for day, (amount, count) in enumerate(zip(day_amounts, day_counts), 1):
        for index in range(count):
            when = D(year, month, day)
            time_orders.append((f"{when}:{index}", when, F(amount) * factor if index == 0 else F(0)))
for day in range(26, 31):
    when = D(2026, 9, day)
    time_orders.append((f"{when}:0", when, F(10)))


def dates(start: D, end: D) -> list[D]:
    return [start + TD(days=i) for i in range((end - start).days)]


def income_count(start: D, end: D) -> tuple[F, int]:
    amounts = [amount for _, day, amount in time_orders if start <= day < end]
    return sum(amounts, F(0)), len(amounts)


def seven_totals(day: D) -> tuple[F, int]:
    return income_count(day - TD(days=6), day + TD(days=1))


current_days = [D(2026, 10, 7), D(2026, 10, 8)]
seven_values = [seven_totals(day) for day in current_days]
mtd_values = [income_count(D(2026, 10, 1), day + TD(days=1))[0] for day in current_days]
ytd_values = [income_count(D(2026, 1, 1), day + TD(days=1))[0] for day in current_days]
check("temporal.unique.order.identity", len({key for key, _, _ in time_orders}), len(time_orders))
check("temporal.input.order.count", len(time_orders), 320)
check("temporal.rolling.seven.income.count", seven_values, [(F(1220), 104), (F(1200), 104)])
check("temporal.rolling.seven.aov", [amount / count for amount, count in seven_values], [F(305, 26), F(150, 13)])
check("temporal.complete.empty.day", income_count(D(2026, 10, 3), D(2026, 10, 4)), (F(0), 0))
check("temporal.warmup.history", seven_totals(D(2026, 10, 1)), (F(150), 6))
check("temporal.exclude.current", income_count(D(2026, 9, 30), D(2026, 10, 7))[0], F(1160))
check("temporal.mtd", mtd_values, [F(1220), F(1300)])
check("temporal.ytd.explicit.origin", ytd_values, [F(1920), F(2000)])
check("temporal.display.only.wrong.prefix", [income_count(current_days[0], day + TD(days=1))[0] for day in current_days], [F(70), F(150)])
check("temporal.overlap.sum.wrong", sum(amount for amount, _ in seven_values), F(2420))
check("temporal.period.unique.income", income_count(D(2026, 10, 1), D(2026, 10, 9))[0], F(1300))

daily_info = {day: income_count(day, day + TD(days=1)) for day in dates(D(2026, 9, 25), D(2026, 10, 9))}
pooled_from_days = [
    (sum(daily_info[d][0] for d in dates(day - TD(days=6), day + TD(days=1))),
     sum(daily_info[d][1] for d in dates(day - TD(days=6), day + TD(days=1))))
    for day in current_days
]
daily_means = [
    mean([daily_info[d][0] / daily_info[d][1]
          for d in dates(day - TD(days=6), day + TD(days=1)) if daily_info[d][1] != 0])
    for day in current_days
]
check("temporal.raw.and.sufficient.components", pooled_from_days, seven_values)
check("temporal.daily.aov.mean.different.estimand", daily_means, [F(1810, 33), F(1700, 33)])
observed_days = [day for day in dates(D(2026, 10, 1), D(2026, 10, 9)) if daily_info[day][1]]
check("temporal.seven.observed.rows.wrong", sum(daily_info[day][0] for day in observed_days[-7:]), F(1300))
rates = [F(1, 10), F(9, 10)]
check("temporal.ratio.same.finals.first", sum(r * w for r, w in zip(rates, [1, 9])) / 10, F(41, 50))
check("temporal.ratio.same.finals.second", sum(r * w for r, w in zip(rates, [9, 1])) / 10, F(9, 50))

prior_mtd_values = [income_count(D(2026, 9, 1), D(2026, 9, day.day) + TD(days=1))[0] for day in current_days]
prior_seven_values = [seven_totals(D(2025, 10, day.day))[0] for day in current_days]
check("temporal.mom.baselines", prior_mtd_values, [F(610), F(650)])
check("temporal.mom.absolute", [a - b for a, b in zip(mtd_values, prior_mtd_values)], [F(610), F(650)])
check("temporal.mom.relative", [(a - b) / abs(b) for a, b in zip(mtd_values, prior_mtd_values)], [F(1), F(1)])
check("temporal.yoy.baselines", prior_seven_values, [F(305), F(300)])
check("temporal.yoy.absolute", [a - b for (a, _), b in zip(seven_values, prior_seven_values)], [F(915), F(900)])
check("temporal.yoy.relative", [(a - b) / abs(b) for (a, _), b in zip(seven_values, prior_seven_values)], [F(3), F(3)])


def same_parent_day(day: D, current_start: D, prior_start: D, prior_end: D) -> D | None:
    candidate = prior_start + (day - current_start)
    return candidate if prior_start <= candidate < prior_end else None


check("temporal.parent.ordinal.not.display.ordinal", [same_parent_day(day, D(2026, 10, 1), D(2026, 9, 1), D(2026, 10, 1)) for day in current_days], [D(2026, 9, 7), D(2026, 9, 8)])
check("temporal.short.month.unmatched", same_parent_day(D(2026, 3, 31), D(2026, 3, 1), D(2026, 2, 1), D(2026, 3, 1)), None)
check("temporal.period.predecessor", D(2026, 3, 1) - TD(days=1), D(2026, 2, 28))


def year_before(day: D) -> D | None:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return None


leap_window = dates(D(2024, 2, 24), D(2024, 3, 2))
mapped_days = {mapped for day in leap_window if (mapped := year_before(day)) is not None}
baseline_window = set(dates(D(2023, 2, 23), D(2023, 3, 2)))
check("temporal.leap.day.unmatched", year_before(D(2024, 2, 29)), None)
check("temporal.window.then.map.cardinality", len(mapped_days), 6)
check("temporal.baseline.own.window.cardinality", len(baseline_window), 7)
check("temporal.window.mapping.noncommute", baseline_window - mapped_days, {D(2023, 2, 23)})
leap_weights = {day: F(100) if day == D(2023, 2, 23) else F(1) for day in baseline_window}
check("temporal.baseline.own.window.income", sum(leap_weights[d] for d in baseline_window), F(106))
check("temporal.mapped.window.wrong.income", sum(leap_weights[d] for d in mapped_days), F(6))
named_weeks = {"FY2026-W52": "FY2025-W52", "FY2026-W53": None}
check("temporal.authored.week53.unmatched", named_weeks["FY2026-W53"], None)

ny = ZoneInfo("America/New_York")
for season, start, end, hours, duration_start in [
    ("spring", DT(2026, 3, 5, tzinfo=ny), DT(2026, 3, 12, tzinfo=ny), 167, DT(2026, 3, 4, 23, tzinfo=ny)),
    ("autumn", DT(2026, 10, 29, tzinfo=ny), DT(2026, 11, 5, tzinfo=ny), 169, DT(2026, 10, 29, 1, tzinfo=ny)),
]:
    elapsed_hours = (end.astimezone(TZ.utc) - start.astimezone(TZ.utc)) / TD(hours=1)
    fixed_start = (end.astimezone(TZ.utc) - TD(hours=168)).astimezone(ny)
    check(f"temporal.dst.{season}.calendar.hours", elapsed_hours, hours)
    check(f"temporal.dst.{season}.duration.start", fixed_start, duration_start)

# These sets model coverage facts, not DSL Cells or runtime rejection.
coverage_days = set(dates(D(2026, 9, 25), D(2026, 11, 3))) - {D(2026, 10, 2)}
check("temporal.coverage.oct8.rolling", set(dates(D(2026, 10, 2), D(2026, 10, 9))) <= coverage_days, False)
check("temporal.coverage.oct9.rolling.recovers", set(dates(D(2026, 10, 3), D(2026, 10, 10))) <= coverage_days, True)
check("temporal.coverage.oct9.cumulative", set(dates(D(2026, 10, 1), D(2026, 10, 10))) <= coverage_days, False)
check("temporal.coverage.november.reset", set(dates(D(2026, 11, 1), D(2026, 11, 3))) <= coverage_days, True)
check("temporal.coverage.complete.empty.day", D(2026, 10, 3) in coverage_days, True)

time_durations = {
    ("H1", D(2026, 10, 1)): [F(1), F(1), F(1), F(100)],
    ("H1", D(2026, 10, 2)): [F(2), F(2)],
    ("H2", D(2026, 10, 1)): [F(3), F(3)],
    ("H2", D(2026, 10, 2)): [None],
}


def duration_window(hub: str | None, end_day: D) -> list[F]:
    return [v for (key, day), values in time_durations.items()
            if (hub is None or key == hub) and end_day - TD(days=6) <= day <= end_day
            for v in values if v is not None]


check("temporal.p95.direct.h1", [quantile(duration_window("H1", D(2026, 10, d)), F(95, 100)) for d in [1, 2, 3]], [F(1703, 20), F(151, 2), F(151, 2)])
check("temporal.p95.direct.h2", [quantile(duration_window("H2", D(2026, 10, d)), F(95, 100)) for d in [1, 2, 3]], [F(3), F(3), F(3)])
check("temporal.p95.raw.without.compact.state", quantile(duration_window(None, D(2026, 10, 3)), F(95, 100)), F(1321, 20))
distribution_a, distribution_b = [F(0), F(20)], [F(19), F(19)]
check("temporal.p95.same.final.and.count", (quantile(distribution_a, F(95, 100)), len(distribution_a)), (quantile(distribution_b, F(95, 100)), len(distribution_b)))
check("temporal.p95.merged.a", quantile(distribution_a + [F(0)], F(95, 100)), F(18))
check("temporal.p95.merged.b", quantile(distribution_b + [F(0)], F(95, 100)), F(19))

fiscal_periods: dict[str, tuple[D, D]] = {}
for year, start in [(2025, D(2025, 1, 27)), (2026, D(2026, 1, 26))]:
    cursor = start
    for index, weeks in enumerate([4, 4, 5] * 4, 1):
        stop = cursor + TD(weeks=weeks)
        fiscal_periods[f"FY{year}-P{index:02d}"] = (cursor, stop)
        cursor = stop
check("temporal.fiscal.p09.bounds", fiscal_periods["FY2026-P09"], (D(2026, 9, 21), D(2026, 10, 26)))
check("temporal.fiscal.current.p08.bounds", fiscal_periods["FY2026-P08"], (D(2026, 8, 24), D(2026, 9, 21)))
check("temporal.fiscal.prior.p08.bounds", fiscal_periods["FY2025-P08"], (D(2025, 8, 25), D(2025, 9, 22)))
check("temporal.fiscal.mtd", [income_count(fiscal_periods["FY2026-P09"][0], day + TD(days=1))[0] for day in current_days], [F(1270), F(1350)])
fiscal_now = income_count(*fiscal_periods["FY2026-P08"])[0]
fiscal_before = income_count(*fiscal_periods["FY2025-P08"])[0]
check("temporal.fiscal.period.income", (fiscal_now, fiscal_before), (F(650), F(0)))
check("temporal.fiscal.absolute", fiscal_now - fiscal_before, F(650))
check("temporal.fiscal.relative.zero.base", fiscal_before == 0, True)

entry_orders = [("O1", "East", 100, True), ("O2", "West", 50, False)]
entry_paid = [row for row in entry_orders if row[3]]
entry_observed_keys = {row[1] for row in entry_paid}
entry_member_keys = {row[1] for row in entry_orders}
check("entry.contribution.keys", entry_observed_keys, {"East"})
check("entry.explicit.member.keys", entry_member_keys, {"East", "West"})
entry_empty_members: set[str] = set()
check("entry.unbound.scalar", sum(row[2] for row in entry_paid), 100)
check("entry.empty.members.scalar", sum(row[2] for row in entry_paid if row[0] in entry_empty_members), 0)
entry_income = {"East": 100}
entry_refunds = {"West": 20}
entry_keys = entry_income.keys() | entry_refunds.keys()
check("entry.multi.root.keys", entry_keys, {"East", "West"})
check("entry.multi.root.net", {key: entry_income.get(key, 0) - entry_refunds.get(key, 0) for key in entry_keys}, {"East": 100, "West": -20})

for prefix in ("commerce", "saas", "quality", "logistics", "experiment", "temporal", "entry"):
    print(prefix, sum(name.startswith(prefix + ".") for name in checks), "checks passed")
print("oracle assertions:", len(checks))
```
<!-- oracle:end -->

从仓库根目录运行以下命令，解析提案示例的 Python 语法，执行附录中的三段原数值 oracle、附录 C 的 pandas 片段及附录 D–L 的集合、时间、份额、Metric、nullable 与粗化数值见证，
不会导入 Marivo、访问业务来源或创建 `.marivo/` 状态：

```bash
.venv/bin/python -B - <<'PY'
import ast
import re
from pathlib import Path

paths = [
    Path("docs/specs/analysis/lazy-analysis-dsl-redesign.md"),
    Path("docs/specs/analysis/lazy-analysis-dsl-scenario-validation.md"),
    Path("docs/specs/semantic/expression-model-redesign.md"),
    Path("docs/specs/analysis/lazy-analysis-dsl-execution.md"),
    Path("docs/specs/analysis/lazy-analysis-dsl-examples.md"),
]
for path in paths:
    text = path.read_text()
    blocks = re.findall(r"^```python\n(.*?)^```$", text, flags=re.M | re.S)
    for index, block in enumerate(blocks, 1):
        ast.parse(block, filename=f"{path}:python-block-{index}")
        assert not re.search(r"[\u3400-\u9fff]", block), (path, index)
    print(path.name, "Python blocks parsed:", len(blocks))
path = paths[1]
text = path.read_text()
appendix = text.split("## 附录 A：", 1)[1]
for index, source in enumerate(re.findall(r"^```python\n(.*?)^```$", appendix, flags=re.M | re.S), 1):
    exec(compile(source, str(path) + f":oracle-{index}", "exec"), {"__name__": "__main__"})
PY
```

v0.16 新增的数值见证独立运行；上面的 175 项 oracle 代码保持原样。

```python
from fractions import Fraction as F


def q95(values):
    ordered = sorted(map(F, values))
    pos = (len(ordered) - 1) * F(19, 20)
    left = pos.numerator // pos.denominator
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (pos - left)


def guarded_ratio(numerator, denominator):
    return None if denominator == 0 else F(numerator, denominator)


a, b = [10, 0], [0, 10]
checks = [
    q95(a) + q95(b) == F(19),
    q95([x + y for x, y in zip(a, b)]) == F(10),
    q95(a) + q95(b) != q95([x + y for x, y in zip(a, b)]),
    guarded_ratio(1 + 9, 1 + 99) == F(1, 10),
    (guarded_ratio(1, 1) + guarded_ratio(9, 99)) / 2 == F(6, 11),
    guarded_ratio(0, 0) is None,
]
assert all(checks)
print(f"v0.16 independent witnesses: {len(checks)} passed")
```

v0.17 新增的贡献与配对数值见证：

```python
from fractions import Fraction as F


numerators = [F(1), F(9)]
denominators = [F(1), F(99)]
parent_rate = sum(numerators) / sum(denominators)
contributions = [n / sum(denominators) for n in numerators]
child_rates = [n / d for n, d in zip(numerators, denominators)]
pairs = [(F(10), F(1)), (None, F(9))]
valid_pairs = [(v, w) for v, w in pairs if v is not None]
paired_mean = sum(v * w for v, w in valid_pairs) / sum(w for _, w in valid_pairs)
unpaired_mean = sum(v * w for v, w in valid_pairs) / sum(w for _, w in pairs)
group_means = [F(10), F(30)]
group_weights = [F(1), F(9)]
merged_mean = sum(v * w for v, w in zip(group_means, group_weights)) / sum(group_weights)
unweighted_mean = sum(group_means) / len(group_means)
checks = [
    parent_rate == F(1, 10),
    contributions == [F(1, 100), F(9, 100)],
    sum(contributions) == parent_rate,
    sum(child_rates) == F(12, 11) != parent_rate,
    paired_mean == F(10),
    unpaired_mean == F(1) != paired_mean,
    merged_mean == F(28),
    unweighted_mean == F(20) != merged_mean,
]
assert all(checks)
print(f"v0.17 independent witnesses: {len(checks)} passed")
```

v0.11 历史核验：81 项断言通过，电商 20、SaaS 15、制造 12、物流 19、实验 15。
v0.12 首次修订核验：原 81 项断言代码保持不变，新增 26 项，合计 107 项通过；
电商 27、SaaS 21、制造 16、物流 24、实验 19。
v0.12 复审修复后复核：此前 107 项断言代码保持不变，新增 7 项，合计 114 项通过；
电商 31、SaaS 24、制造 16、物流 24、实验 19。新增项只核对授权声明对应的行金额/成本、条件计算阶段和共同分类后的合计。
v0.13 历史核验：此前 114 项断言代码保持不变，新增时间分析 55 项，合计 169 项通过。
新增项涵盖交易粒度求值、窗口/累计数值、期间对应、DST、覆盖集合、分位数信息下界和财务日历。
这些数字只统计数据、公式和数值反例，不统计 §9 的待验收拒绝行为，也不是 DSL/后端验收数量。

v0.14 增加 6 项独立入口数值反例，覆盖贡献键、显式成员键、无主体/空主体和多根分类并集，合计 175 项均通过。
v0.16 历史文档核验通过：60 个 Python 围栏语法有效，53 处 observe 和 47 处指标构造符合当时目标签名，
示例无已退出的指标构建入口；58 个本地链接/锚点有效，46 个反例连续编号，R1–R8 八个实施阶段保持未勾选。
原 175 项 oracle 代码与修订前逐字一致并全部通过，新增 6 项 P95/比率数值见证通过，共 181 项。
另在本地 Ibis 12.0.0 上通过 8 项原生表达式构造检查，覆盖规约、Deferred、nullif、配对统计与条件表达式；
这只验证 Ibis API 形状。场景文档与实现计划中的提案 SHA-256 已同步核对。
v0.17 历史文档核验通过：63 个 Python 围栏语法有效，49 处直接 ms.metric 调用、2 处 metric 装饰器及 53 处 observe 符合当时目标输入形状；
61 个本地链接/锚点有效，48 个反例连续编号，R1–R8 保持未勾选。原 175 项及 v0.16 的 6 项数值见证保持原样并通过，
新增 8 项贡献/配对反例通过，共 189 项。在 Ibis 12.0.0 上另通过 12 项原生结构检查，覆盖 Divide/NullIf、别名、
缩放、外层 fill_null、行级/聚合级运算次序及共同/错配权重掩码；这未执行 Marivo 的规范化器或装饰器，
也不是 v0.18 已取消的公式识别任务的实施证据。

v0.18 历史文档核验通过：65 个 Python 围栏语法有效，43 处直接 ms.metric、2 处 metric 装饰器、
7 处 ms.ratio、3 处 ms.weighted_mean 及 53 处 observe 符合目标参数形状。
62 个本地链接/锚点有效，48 个反例连续编号，R1–R8 八个实施阶段保持未勾选；围栏、表格与空白检查通过。
原 175+6+8 项数值代码与本轮修订前逐字一致，189 项全部通过；提案 SHA-256 与实现计划同步。
另按条款检查构造器职责、显式角色/通用依赖、时间 fold、装饰器/图校验及切换清单的一致性。
不执行语义 authoring 片段、不加载模拟 Catalog、不调用尚未实现的新 DSL；
文档与数值核验不证明桥接类型、绑定、后端或恢复通过。四份文档的范围仍为目标设计，R1–R8 尚未实施。

v0.19 历史文档核验：52 个 Python 围栏语法有效且代码块保持英文，49 个本地链接/锚点有效；
48 个反例编号连续，R1–R8 全部未实施。原 175+6+8 项数值代码逐字保留，189 项全部通过；
附录复现命令已覆盖三段数值见证，主提案 SHA-256 与本地计划一致。
本轮写入仅限两份提案、场景文档和本地计划；工作区范围外并行变更保留，技能/AGENTS未编辑。
未导入 Marivo、加载 Catalog 或执行目标 DSL/Runtime，文档与数学检查不构成产品验收。

v0.20 历史文档核验：53个Python围栏语法有效且代码块保持英文，60个本地链接/锚点有效；
48个反例编号连续，R1–R8全部未实施。原三段数值代码逐字保留，175+6+8共189项独立检查通过，附录命令已复现。
主提案SHA-256与本地计划一致；新零分母、已有政策和六项披露/资格要求仍是待实施的合同验收。
本轮只写四份目标文档；技能/AGENTS指纹不变。期间并行工作将HEAD推进到`8add83cc65987facb77f68392d5780d94a6a06ed`，
范围外代码/测试变更及删除保留，不归属于本轮文档编辑；未执行目标DSL/Runtime。

v0.21 历史文档核验：61个Python围栏通过语法/英文检查，66个本地链接/锚点有效，表格列数一致；
48个反例编号连续，R1–R8仍全部未实施。原三段数值代码与v0.20逐字一致，189项检查全部通过。
附录C在pandas 2.3.3上原样执行正文七段pandas程序，21项独立配方检查通过，
包含Unknown/Undefined拒绝、缺日拒绝和共同配对样本；复现入口覆盖四段附录代码。
合成输入不经过Marivo导出器，因此未验证实际to_pandas、typed DSL、保存/恢复或Runtime资格。
本轮只修订两份提案、本文和本地计划；未修改代码、测试、技能或AGENTS，未创建提交或推送。


v0.22 历史文档核验：七份相关文档共 62 个 Python 围栏通过语法/英文检查，73 个本地链接/锚点有效，
表格列数与围栏一致；51 个反例连续编号，R1–R8 仍全部未实施。
原 175+6+8 项数值代码、附录 C 配方驱动及七段 pandas 示例逐字保留；189 项原数值检查、
21 项 pandas 配方检查通过。附录 D 新增 13 项有限集合见证通过，专门验证统一域的身份、完整输出及信息丢失边界。
主提案 SHA-256：`07e90dc2b0cf2f20cc0657ac87ec12533bae7c1d510cb2e35981b1a647206d08`。这些是文档与独立见证，未执行目标 DSL 或 Runtime。

v0.23 整理前的历史文档核验：四份目标文档的 66 个 Python 围栏通过语法/英文检查，70 个本地链接/锚点有效；
围栏、表格、空白及 54 个连续反例核对通过，R1–R8 仍全部未实施。
Session 时间方法签名、literal 无 unit、values 列定义与逐行量名一致及旧调用残留通过静态检查。
原五段附录程序和七段 pandas 示例逐字保留；189 项原数值、21 项 pandas 配方、13 项原域见证通过，
新增附录 E 的 12 项关系/稀疏域/时间边界见证通过。单位绑定、Session/DST/Anchor、typed error 及冷恢复仍待实际实现验证。
主提案 SHA-256：`bbb729db1e40b38aeaf98878ca2b3805a23395949c84d478607269ac46b01120`，与本地计划一致。
本轮仅修改两份提案、本文和本地计划；未编辑代码、测试、技能、AGENTS 或当前实现契约，未执行目标 DSL/Runtime。

v0.24 份额修订核验：Python 围栏语法/英文、Query/Result.share 两个重载与全部10处代码调用核对通过；
Column 已无份额方法声明或调用，默认总体、partition_by、显式 of 及筛选顺序已同步到定义、执行和示例。
原六段附录程序逐字保留；189项原数值、21项pandas配方、13项域及12项关系/时间见证均通过，
附录F新增16项份额范围数值见证通过。链接/锚点、表格、围栏与57个连续验收条目核对通过。
本轮修改DSL定义、执行说明、多轮示例、本文及提案索引；未修改实现、测试、技能、AGENTS或本地实施计划，未执行目标DSL/Runtime。

v0.25 完整性规则修订核验：移除观察完整性声明及示例包装函数，普通来源默认信任；
当时的物流缺口案例曾使用明确的来源不完整事实；该能力已退出首版，仅保留历史集合数学见证，不再作为缺口声明或执行验收依据。
六份目标文档的 70 个 Python 围栏语法/英文、127 个本地链接/锚点、65 张表格及空白检查通过；
既有 59 个验收条目连续，R1–R8 八个实施阶段仍未勾选。
七段附录程序及七段 pandas 示例逐字保留，189 项原数值、21 项 pandas、13 项域、12 项关系/时间及 16 项份额见证通过，共 251 项。
同步范围包括 DSL、语义、执行、示例、本文及本地实施计划；未修改实现、测试、技能或 AGENTS，未执行目标 DSL/Runtime。

v0.26 文档核验：六份目标文档的 81 个 Python 围栏语法/英文检查、139 个本地链接/锚点、
68 张表格及空白检查通过；41 处 Metric 构造/装饰调用、19 处 named 调用符合目标参数形状。
68 个未来验收条目连续，R1–R8 仍全部未勾选。原七段附录程序及原 pandas 示例逐字保留；
原 251 项独立检查及附录 G 新增 16 项均通过，共 267 项。未导入 Marivo 或执行目标 DSL。
本轮只改提案、执行/示例/场景文档及被忽略的本地实施计划，未修改实现、测试、技能或 AGENTS；
数值结果不证明装饰器、域绑定、模型收录、政策 lowering、后端或冷恢复已实现。

v0.27 文档核验：七份目标文档共 85 个 Python 围栏语法/英文、140 个本地链接/锚点、
73 张表格和空白检查通过；17 个 Metric 与 10 个 Measure 声明符合零参数体及声明参数形状。
73 个未来验收条目连续，R1–R8 仍全部未实施。原八段附录程序和七段 pandas 示例逐字保留，
原 267 项独立检查通过，附录 H 新增 22 项通过，共 289 项；按本文公布的复现入口执行。
只修改提案、关联 DSL 文档、核心代数澄清及被忽略的本地实施计划；未改实现、测试、技能或 AGENTS。
未导入 Marivo、运行新 DSL 或验证真实后端/保存恢复；声明、权限、绑定和政策行为仍需实施验收。

v0.28 复审核验：七份目标文档的91个Python围栏语法/英文、142个本地链接/锚点、73张表格及空白检查通过。
19个Metric与12个Measure声明符合函数命名、零参数体和声明参数规则；规范示例的字面量Ref符合种类与路径段规则。
10组模型/runtime表达式在声明符号替换后AST相同；这是示例结构核对，不是运行时等价证明。
原九段附录程序中的八段逐字保留，附录C仅修正两处字段路径元数据；七段pandas片段仅实验片段同步相同路径。
原289项独立数值/pandas检查及附录I新增26项通过，共315项。80个未来验收条目连续，R1–R8仍全部未实施。
修改仅限七份设计文档（含被忽略的本地计划），没有实现、测试、技能或AGENTS变更；未执行目标DSL/Runtime或创建提交。
收尾HEAD为`970a5df89f7cd62b809f7b948e920a1e8cff94f0`；期间的并行实现提交未触及本轮七份文档，工作树提案为本次核验对象。

## 附录 B：核对的提案条款

下表未加前缀的 §2–5 指 DSL 定义，§8–9 指本文；语义、执行与示例均显式标明文档。

| 条款 | 本次使用方式 |
| --- | --- |
| DSL §1 | 限定 proposed 状态，纳入声明式时间窗口，排除通用事件序列与预测/模型范围 |
| 本文 §8.6；DSL §4.1、§4.3.1–§4.3.2 | 逐算子准入、类型化参照与来源业务量授权；普通结果条件/分类交pandas |
| 统一表达式提案、DSL §1.1；本文 §9.1-41–48 | 接收者规约、小型组合图、一次观察绑定、端点细化、纯表达式声明及实际信息保留；新增拒绝行为尚待实现验收 |
| §4.2–4.4 | 完整成员身份、唯一声明的贡献归属路径、贡献时间、Metric 内部统计及固定政策的sum/mean/row_count |
| §4.1.1–4.1.2、§4.2.1、§9.1-52–54 | Session 时间默认、精确时刻与版本选择、局部常量/参照列单位、关系像与有限逆像；typed 行为仍待实现 |
| §1.1、§4.1、§5.1、§9.1-39–40 | Metric 入口、显式成员绑定、贡献分组域；字段汇总不伪造隐藏名单；成员约束不隐含完整输出 |
| §4.5–4.7 | 完整域、坐标筛选、reduce/rollup/Query.drill、align/compare 的 how/match、broadcast、默认保存信息 |
| §4.6–4.6.1、§9.1-25–30 | 分组 Top-N、nullable/完整键/精度交接、外部标签不创造主体 |
| DSL §4.8、§5.3.3；本文 §2.4、§9.1-12、55–57 | Query 唯一份额入口、默认/分组总体、筛选顺序与实际共同输入 |
| DSL §4.9；[多轮示例 §2.13–2.14](lazy-analysis-dsl-examples.md#29-时间分析新来源窗口与冻结对应)；本文 §9.1-31–38 | 完整输入上的窗口指标、覆盖、累计起点、不带轴的期间对应及完整示例 |
| §5.1–5.2 | 防 fanout、各阶段过滤、零贡献成员与完整日网格 |
| §5.3.1–5.3.3 | 投影已有身份坐标、Query 下钻与 Artifact 终端边界、贡献份额的支持依据 |
| DSL §5.4；[执行与恢复 §1.1.1](lazy-analysis-dsl-execution.md#111-共享逻辑节点与执行内输入) | 状态筛选；不把共享逻辑节点当成同一次来源读取 |
| [执行与交接 §1–2](lazy-analysis-dsl-execution.md#1-执行保留与恢复) | 终端输出、重开要求、Query 依赖与输入权威、域绑定及阶段错误修复 |
| 语义§3.4、§6.1；§9.1-3、10–11、41、46–48 | 新零分母默认、显式Null/已有政策、比较命名及有限图资格；具体增量验收见§8.4 |
| 语义§2、§3.3–3.5；执行§1.1.2、§1.2；§9.1-63–80 | 同一行/指标表达式、声明及算子政策；不可变域与收录边界；附录 G/H 只验证范围和算式 |

当前实现的权威仍是 [Python Analysis Design](python-analysis-design.md)；
本报告中的提案语法不能据此加入当前 Help 或示例可运行性声明。

## 附录 C：原样执行 pandas 交接示例

下面只构造普通 pandas 合成表，并原样执行本文与多轮示例中标记的八段 pandas 程序。
不定义假的 Result/Query，不调用 to_pandas，不验证真实导出器、DSL 异常、保存或恢复。
合成表仅按 [导出布局](lazy-analysis-dsl-execution.md#3-pandas-导出合同) 的名字空间提供算法所需输入；dtype/隔离/完整元数据仍是未来实现验收。
原189项数学见证独立保留，此处的检查单独计数。附录A的复现命令会同时运行此段。

```python
from copy import deepcopy
from math import isclose
from pathlib import Path
import re

import pandas as pd

examples_doc = Path("docs/specs/analysis/lazy-analysis-dsl-examples.md")
scenario_doc = Path("docs/specs/analysis/lazy-analysis-dsl-scenario-validation.md")
pandas_checks = []


def check_pandas(name, condition):
    assert condition, name
    pandas_checks.append(name)


def fixture(values, *, coordinates=None, axes=()):
    columns = {}
    for name, items in (coordinates or {}).items():
        columns["coordinate", name] = pd.Series(items)
    for name, items in values.items():
        columns["value", name] = pd.Series(items)
    frame = pd.DataFrame(columns)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["kind", "name"])
    frame.attrs["marivo"] = {"axes": deepcopy(axes), "artifact_id": "synthetic-fixture"}
    return frame


def run_example(path, name, inputs):
    marker = "<!-- pandas-example:" + name + " -->"
    section = path.read_text().split(marker, 1)[1]
    source = re.match(r"\s*```python\n(.*?)\n```", section, re.S).group(1)
    namespace = dict(inputs)
    exec(compile(source, str(path) + ":" + name, "exec"), namespace)
    return namespace


def rejects_example(path, name, inputs):
    try:
        run_example(path, name, inputs)
    except ValueError:
        return True
    return False


customers = fixture(
    {"revenue": [100, 90], "orders": [1, 9], "aov": [100.0, 10.0]},
    coordinates={"customer": ["A", "B"]},
    axes=[{"column": "customer", "kind": "field", "label": "Customer code"}],
)
customer_keys = run_example(examples_doc, "cohort-keys", {"cohort_frame": customers})["customer_keys"]
check_pandas("axis.unique.field", customer_keys == (("A",), ("B",)))
renamed_customers = customers.copy(deep=True)
renamed_customers.attrs = deepcopy(customers.attrs)
renamed_customers.attrs["marivo"]["axes"][0]["label"] = "A different description"
check_pandas("axis.label.is.description", run_example(
    examples_doc, "cohort-keys", {"cohort_frame": renamed_customers}
)["customer_keys"] == customer_keys)
ambiguous_customers = fixture(
    {},
    coordinates={"customer": ["A", "B"], "region": ["East", "West"]},
    axes=[
        {"column": "customer", "kind": "field", "label": "Repeated label"},
        {"column": "region", "kind": "field", "label": "Repeated label"},
    ],
)
check_pandas("axis.unexpected.count.rejects", rejects_example(
    examples_doc, "cohort-keys", {"cohort_frame": ambiguous_customers}
))
summary = run_example(examples_doc, "customer-means", {"customer_export": customers})["overview"]
check_pandas("customer.means", summary == {
    "order_mean": 19, "mean_customer_aov": 55, "spend_per_customer": 95, "weighted_customer_aov": 19,
})
stats = run_example(examples_doc, "customer-statistics", {"customer_export": customers})["cell_statistics"]
check_pandas("customer.statistics", stats == {"mean_aov": 55, "p90_aov": 91, "valid": 2, "rows": 2})
missing_customers = customers.copy(deep=True)
missing_customers.loc[0, ("value", "aov")] = None
check_pandas("customer.required.value.missing", rejects_example(
    examples_doc, "customer-means", {"customer_export": missing_customers}
))

saas = fixture(
    {
        "mrr.current": [150, 100, 0], "mrr.baseline": [100, 200, 100],
        "mrr.absolute": [50, -100, -100], "mrr.relative": [0.5, -0.5, -1.0],
    },
    coordinates={"account": [("t1", "A"), ("t1", "B"), ("t2", "A")]},
    axes=[{"column": "account", "kind": "member", "label": "Account identity"}],
)
saas_readout = run_example(scenario_doc, "saas-readout", {"saas_export": saas})
check_pandas("saas.retention", saas_readout["retention_readout"] == {
    "nrr": 0.625, "grr": 0.5, "accounts": 3, "expansion_gain": 50,
})
movement = saas_readout["movement_summary"]
check_pandas("saas.movement", movement["net_change"].to_dict() == {
    "churn": -100, "contraction": -100, "expansion": 50,
})
check_pandas("saas.endpoints", movement["current"].sum() == 250 and movement["baseline"].sum() == 400)

logistics = fixture({
    "shipments": [4, 2, 0, 2, 1, 0], "timed": [4, 2, 0, 2, 0, 0],
    "p95_hours": [85.15, 2.0, None, 3.0, None, None],
})
quality = run_example(scenario_doc, "logistics-quality", {"logistics_export": logistics})["quality"]
check_pandas("logistics.quality", quality == {
    "grid_cells": 6, "valid_p95_cells": 3, "missing_durations": 1, "missing_fraction": 1 / 9,
})

experiment = fixture(
    {"revenue": [100, 0, 0, 80, 20, 0], "sessions": [100, 1, 0, 2, 2, 0]},
    coordinates={"arm": ["A", "A", "A", "B", "B", "B"], "activity": [10, 0, 0, 10, 0, 0]},
    axes=[
        {"column": "arm", "kind": "field", "label": "Treatment arm"},
        {"column": "activity", "kind": "field", "label": "Preperiod activity"},
    ],
)
experiment_readout = run_example(scenario_doc, "experiment-readout", {"experiment_export": experiment})
groups = experiment_readout["group_stats"]
check_pandas("experiment.means", all(isclose(value, 100 / 3) for value in groups["mean_workspace_revenue"]))
check_pandas("experiment.quantiles", groups["p90_workspace_revenue"].to_dict() == {"A": 80, "B": 68})
check_pandas("experiment.denominators", groups["workspaces"].to_dict() == {"A": 3, "B": 3})
check_pandas("experiment.strata", experiment_readout["stratum_readout"]["mean_workspace_revenue"].to_dict() == {
    ("A", "active"): 100, ("A", "inactive"): 0, ("B", "active"): 80, ("B", "inactive"): 10,
})
relabeled_experiment = experiment.copy(deep=True)
relabeled_experiment.attrs = deepcopy(experiment.attrs)
for axis in relabeled_experiment.attrs["marivo"]["axes"]:
    axis["label"] = "Repeated description"
relabeled_readout = run_example(scenario_doc, "experiment-readout", {"experiment_export": relabeled_experiment})
check_pandas("axis.declared.order.ignores.labels", relabeled_readout["stratum_readout"].equals(
    experiment_readout["stratum_readout"]
))

dates = pd.date_range("2026-09-25", "2026-10-08", freq="D", tz="Asia/Shanghai")
history = fixture(
    {"aov": [None, 10, 10, 10, 10, 10, 100, 100 / 11, None, 40, 50, 60, 70, 80]},
    coordinates={"day": list(dates)}, axes=[{"column": "day", "kind": "calendar"}],
)
for path, name in [(examples_doc, "daily-aov"), (scenario_doc, "temporal-daily-aov")]:
    mean = run_example(path, name, {"history_export": history})["mean_daily_aov"]
    check_pandas(name + ".values", all(isclose(x, y) for x, y in zip(mean, [1810 / 33, 1700 / 33], strict=True)))
    missing_day = history.drop(index=8).reset_index(drop=True)
    check_pandas(name + ".reject.missing.day", rejects_example(path, name, {"history_export": missing_day}))

# Pairwise external statistics use one common non-missing mask.
pairs = fixture({"value": [10.0, None], "weight": [1.0, 9.0]})
valid = pairs["value"][["value", "weight"]].notna().all(axis=1)
paired = pairs["value"].loc[valid]
paired_mean = (paired["value"] * paired["weight"]).sum() / paired["weight"].sum()
check_pandas("pairs.common.mask", paired_mean == 10)
check_pandas("layout.no.cell.columns", set(pairs.columns.get_level_values("kind")) == {"value"})

print(f"pandas {pd.__version__}: {len(pandas_checks)} independent handoff recipe checks passed")
```


## 附录 D：统一 Population 的独立集合见证

此处只用有限集合与字典验证身份类型、完整域和信息下界，不模拟 Population 类或 DSL 执行。
有相同全局身份、分组金额和人数的两份输入仍可拥有不同组名单，说明隐藏关系不能从摘要恢复。
固定属性副本与当前属性也分别计算；该反例不赋予独立读取共同快照。

```python
checks = []


def check_domain(name, condition):
    assert condition, name
    checks.append(name)


account_a = ("Account", ("t1", "A"))
account_b = ("Account", ("t2", "A"))
order_a = ("Order", ("t1", "A"))
check_domain("entity.type", account_a != order_a)
check_domain("complete.identity", len({account_a, account_b}) == 2)
grid = {(identity, day) for identity in (account_a, account_b) for day in (1, 2)}
check_domain("identity.projection", len(grid) == 4 and len({identity for identity, _ in grid}) == 2)
check_domain("empty.projection", {row[:1] for row in set()} == set())

requested = {("Order", (key,)) for key in ("A09", "B11", "O404")}
source = {("Order", (key,)) for key in ("A08", "A09", "B11")}
check_domain("request.vs.source", len(requested) == 3 and len(requested & source) == 2)
identities = {"A", "B"}
regions = {"A": "East", "B": "West"}
contributions = [("A", 100)]
selected = [(identity, value) for identity, value in contributions if identity in identities]
observed = {regions[identity] for identity, _ in selected}
complete = {regions[identity] for identity in identities}
check_domain("constraint.vs.output", observed == {"East"} and complete == {"East", "West"})
amounts = {region: sum(value for identity, value in selected if regions[identity] == region) for region in complete}
check_domain("explicit.empty.cell", amounts == {"East": 100, "West": 0})
check_domain("absent.vs.empty.constraint", sum(value for _, value in contributions) == 100 and sum(value for identity, value in contributions if identity in set()) == 0)

first = {"A": ("East", 100), "B": ("West", 100)}
second = {"A": ("West", 100), "B": ("East", 100)}


def summarize(rows):
    return {region: (sum(value for group, value in rows.values() if group == region), sum(group == region for group, _ in rows.values())) for region in ("East", "West")}


check_domain("same.summary", first.keys() == second.keys() and summarize(first) == summarize(second))
check_domain("lost.membership", {key for key, (region, _) in first.items() if region == "East"} != {key for key, (region, _) in second.items() if region == "East"})
saved_keys = frozenset(first)
saved_classification = frozenset((key, region) for key, (region, _) in first.items())
current_classification = {(key, second[key][0]) for key in saved_keys}
check_domain("fixed.keys.vs.attributes", saved_keys == frozenset(second) and saved_classification != current_classification)
outer_rows = [(account_a, account_a), (account_b, None)]
check_domain("missing.side", {right for _, right in outer_rows if right is not None} == {account_a})
draws = list(enumerate([account_a, account_a]))
check_domain("draw.occurrences", len(set(draws)) == 2 and len({identity for _, identity in draws}) == 1)
print(f"unified Domain: {len(checks)} independent finite-set witnesses passed")
```

## 附录 E：关系像、稀疏域与时间边界的独立见证

以下仅使用标准库，验证主提案 §4.2.1 的有限集合、§4.5 的稀疏完整元组和本文 §7.10 的边界样本。
不实现假的 Population/Session/Anchor，不验证单位绑定、DST 拒绝、typed error 或持久化。
旧三段数值 oracle、附录 C/D 与七段 pandas 程序保持原样。

```python
from datetime import datetime, timezone
from itertools import product
from zoneinfo import ZoneInfo

checks = []


def check_input(name, condition):
    assert condition, name
    checks.append(name)


buyers = {"O1": "A", "O2": "A", "O3": "B", "O4": None}
source = set(buyers)
targets = {"A"}
image = {buyers[key] for key in source if buyers[key] is not None}
preimage = {key for key in source if buyers[key] in targets}
restricted = {"O1"}
restricted_image = {buyers[key] for key in restricted}
check_input("relation.image", image == {"A", "B"})
check_input("relation.preimage", preimage == {"O1", "O2"})
check_input("relation.bounded", {key for key in restricted if buyers[key] in targets} == {"O1"})
check_input("relation.not.inverse", {key for key in source if buyers[key] in restricted_image} != restricted)
check_input("relation.null", "O4" not in preimage and None not in image)
sparse = {("East", "Web"), ("West", "Store")}
check_input("coordinates.sparse", len(sparse) == 2 and len(set(product({"East", "West"}, {"Web", "Store"}))) == 4)

shanghai = ZoneInfo("Asia/Shanghai")
new_york = ZoneInfo("America/New_York")
midnight = datetime(2026, 10, 1, tzinfo=shanghai)
override = datetime(2026, 10, 1, tzinfo=new_york)
check_input("time.report.zone", midnight.astimezone(timezone.utc) == datetime(2026, 9, 30, 16, tzinfo=timezone.utc))
check_input("time.override", override.astimezone(timezone.utc) == datetime(2026, 10, 1, 4, tzinfo=timezone.utc))
launch = datetime(2026, 9, 30, 12, tzinfo=shanghai)
check_input("time.aware.instant", launch == datetime.fromisoformat("2026-09-30T04:00:00+00:00"))
boundary = datetime(2026, 11, 1, tzinfo=shanghai)
versions = [
    (datetime(2026, 10, 1, tzinfo=shanghai), boundary, "Silver"),
    (boundary, datetime(2026, 12, 1, tzinfo=shanghai), "Gold"),
]
check_input("time.exact.version", [label for start, end, label in versions if start <= boundary < end] == ["Gold"])
check_input("time.left.limit", [label for start, end, label in versions if start < boundary <= end] == ["Silver"])
ledger = [(launch, 10)] + [
    (datetime(2026, 10, day, 12, tzinfo=shanghai), amount)
    for day, amount in enumerate([100, 900, 0, 40, 50, 60, 70, 80], 1)
]
totals = [
    sum(amount for instant, amount in ledger if launch <= instant < datetime(2026, 10, end_day, tzinfo=shanghai))
    for end_day in (8, 9)
]
check_input("time.independent.origin", totals == [1230, 1310])
print(f"literal-domain and temporal inputs: {len(checks)} independent witnesses passed")
```

## 附录 F：份额总体与筛选顺序的独立见证

v0.24 用精确 Fraction 对照同一输入的默认总体、地区内总体与先后筛选。
`explicit_original_reference` 只检验对原全部地区总体的 32/47；v0.25 中该数值由先 share 后筛选取得。
月份例沿用电商8/9月地区销售额，说明跨月总体与每月总体是不同问题；有符号金额、Null分类键和新旧读取比值保留原边界。
这些检查只证明下面的数值和范围区别，不实现 Query/Result.share，不验证贡献证据、状态传播、错误、读取次数或恢复。
原附录程序保持原样；附录 A 的复现命令会执行本段。

```python
from fractions import Fraction as F


regional_sales = {"East": 320, "West": 150}
regional_total = sum(regional_sales.values())
regional_shares = {key: F(value, regional_total) for key, value in regional_sales.items()}
selected_sales = {key: value for key, value in regional_sales.items() if key == "East"}
selected_total = sum(selected_sales.values())
selected_shares = {key: F(value, selected_total) for key, value in selected_sales.items()}
selected_original = {key: F(value, regional_total) for key, value in selected_sales.items()}

channel_sales = {("East", "Web"): 200, ("East", "Store"): 120, ("West", "Web"): 150}
region_totals = {
    region: sum(value for (parent, _), value in channel_sales.items() if parent == region)
    for region in regional_sales
}
channel_local = {key: F(value, region_totals[key[0]]) for key, value in channel_sales.items()}
channel_global = {key: F(value, sum(channel_sales.values())) for key, value in channel_sales.items()}
selected_channel = {key: value for key, value in channel_sales.items() if key == ("East", "Web")}

monthly_sales = {("East", 8): 300, ("West", 8): 60, ("East", 9): 320, ("West", 9): 150}
month_totals = {
    month: sum(value for (_, period), value in monthly_sales.items() if period == month)
    for month in (8, 9)
}
signed_sales = [120, -20]
null_group_sales = {"East": 70, None: 30}
fresh_sales = [120, 90]
old_total = 190

share_checks = {
    "default_total": regional_shares == {"East": F(32, 47), "West": F(15, 47)},
    "share_then_filter": {key: value for key, value in regional_shares.items() if key == "East"} == {"East": F(32, 47)},
    "filter_then_share": selected_shares == {"East": F(1)},
    "explicit_original_reference": selected_original == {"East": F(32, 47)},
    "partition_totals": region_totals == {"East": 320, "West": 150},
    "partition_shares": channel_local == {("East", "Web"): F(5, 8), ("East", "Store"): F(3, 8), ("West", "Web"): F(1)},
    "global_channel_shares": channel_global == {("East", "Web"): F(20, 47), ("East", "Store"): F(12, 47), ("West", "Web"): F(15, 47)},
    "partition_sums": all(sum(value for (parent, _), value in channel_local.items() if parent == region) == 1 for region in regional_sales),
    "partition_then_filter": channel_local["East", "Web"] == F(5, 8),
    "filter_then_partition": F(selected_channel["East", "Web"], sum(selected_channel.values())) == 1,
    "months_default_total": F(monthly_sales["East", 9], sum(monthly_sales.values())) == F(32, 83),
    "months_partition_total": F(monthly_sales["East", 9], month_totals[9]) == F(32, 47),
    "signed_shares": [F(value, sum(signed_sales)) for value in signed_sales] == [F(6, 5), F(-1, 5)],
    "null_coordinate_in_total": {key: F(value, sum(null_group_sales.values())) for key, value in null_group_sales.items()} == {"East": F(7, 10), None: F(3, 10)},
    "independent_read_ratio": sum(F(value, old_total) for value in fresh_sales) == F(21, 19),
    "fresh_total_shares": [F(value, sum(fresh_sales)) for value in fresh_sales] == [F(4, 7), F(3, 7)],
}
for name, passed in share_checks.items():
    assert passed, name
print(f"v0.24 share scope witnesses: {len(share_checks)} passed")
```

## 附录 G：统一 Metric 与显式域依赖见证

v0.26 使用 A=100、C=300、完整域 A/B，区分分支过滤、共同过滤、完整内域和实际出现内域。
当前默认 sum 的缺活动 B 为 NA，均值只包含有效值；下方 complete_null 对应这一默认。
complete_zero 对应显式 sum(empty="zero")，B 为 0 并参与均值；有效值均值与完整 Population 大小仍不同。
第二次数据把来源名单从 A 改为 C，同时收入变为 A=120、C=360：固定旧键与重新选键得到不同结果，固定键不固定收入。
这些是独立有限数据上的算式，不实现 Metric、Cell、loader、域覆盖错误或数值政策解析器。
v0.27 保留下面代码逐字不变：unfiltered_domain_missing_key 只说明外部完整输出缺 C，
不再解释为 per(cohort) 拒绝；observed_mean 是筛选后活跃名单的均值。
声明一致、不可变编码、模型收录拒绝和恢复仍须由 §9.1-63–73 的真实实现验收。

```python
from fractions import Fraction as F


revenue = {"A": 100, "C": 300}
cohort = frozenset(("A", "B"))
selected = {key: value for key, value in revenue.items() if key in cohort}
complete_zero = {key: selected.get(key, 0) for key in cohort}
complete_null = {key: selected.get(key) for key in cohort}
defined_values = [value for value in complete_null.values() if value is not None]

old_eligible = frozenset(("A",))
saved_keys = old_eligible
new_eligible = frozenset(("C",))
new_revenue = {"A": 120, "C": 360}
old_answer = sum(revenue[key] for key in saved_keys)
new_fixed_key_answer = sum(new_revenue[key] for key in saved_keys)
new_selected_answer = sum(new_revenue[key] for key in new_eligible)

metric_checks = {
    "full_revenue": sum(revenue.values()) == 400,
    "branch_filter": selected == {"A": 100},
    "numerator_only": F(sum(selected.values()), sum(revenue.values())) == F(1, 4),
    "common_filter": F(sum(selected.values()), sum(selected.values())) == 1,
    "complete_zero_samples": complete_zero == {"A": 100, "B": 0},
    "complete_zero_mean": F(sum(complete_zero.values()), len(complete_zero)) == 50,
    "observed_mean": F(sum(selected.values()), len(selected)) == 100,
    "null_is_a_missing_sample": complete_null == {"A": 100, "B": None},
    "ignore_null_mean": F(sum(defined_values), len(defined_values)) == 100,
    "strict_null_has_missing_input": any(value is None for value in complete_null.values()),
    "unfiltered_domain_missing_key": set(revenue) - cohort == {"C"},
    "filtered_domain_covers_input": set(selected) <= cohort,
    "fixed_selection_stays_old": saved_keys == {"A"} and saved_keys != new_eligible,
    "fixed_keys_read_new_values": new_fixed_key_answer == 120 and old_answer == 100,
    "source_selection_changes_values": new_selected_answer == 360,
    "empty_count_is_zero": len([]) == 0,
}
for name, passed in metric_checks.items():
    assert passed, name
print(f"v0.26 metric/domain witnesses: {len(metric_checks)} passed")
```

## 附录 H：统一行指标表达式的五个核心见证

v0.27 对照 [五个核心场景](lazy-analysis-dsl-examples.md#7-五个核心-metric-场景)。
只计算有限输入上的金额、样本域、分位数和共同配对；当前默认 sum 将空输入与全 NA 都完成为 NA，显式 empty="zero" 才为 0，
但不实现装饰器、权限系统、政策解析或异常。本附录不证明模型/runtime 同图、收录及恢复；
这些能力以实际实现测试和独立执行验收为准。
附录 A 的历史数学程序保留；当前 pandas 示例和本节 sum 见证随目标合同更新。

```python
from fractions import Fraction as F


def exact_quantile(values, q):
    ordered = sorted(F(value) for value in values)
    position = (len(ordered) - 1) * q
    lower = position.numerator // position.denominator
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


order_amounts = [100] + [10] * 9
customer_amounts = {"A": [100], "B": [10] * 9}
customer_aovs = [F(sum(values), len(values)) for values in customer_amounts.values()]
selected_keys = frozenset(("A", "B"))
source_values = {"A": 100, "C": 300}
selected_values = [source_values.get(key, 0) for key in sorted(selected_keys)]  # Explicit empty="zero".
active_selected = [value for key, value in source_values.items() if key in selected_keys]
source_samples = {"A": [10, 0], "B": [0, 10]}
space_totals = [sum(row) for row in zip(*source_samples.values())]
paired_rows = [(10, 1), (None, 9)]
valid_pairs = [(F(v), F(w)) for v, w in paired_rows if v is not None and w is not None]
paired_numerator = sum(v * w for v, w in valid_pairs)
paired_denominator = sum(w for _, w in valid_pairs)
zero_weight_pairs = [(10, 1), (20, -1)]
line_items = [(2, 90, 80), (3, 40, 20)]
line_margins = [quantity * (price - cost) for quantity, price, cost in line_items]
nullable_sums = {
    name: sum(present) if (present := [value for value in values if value is not None]) else None
    for name, values in {"empty": [], "all_null": [None, None], "mixed": [100, None], "zero": [-10, 10]}.items()
}

expression_checks = {
    "revenue": sum(order_amounts) == 190,
    "entity_count": len(order_amounts) == 10,
    "aov": F(sum(order_amounts), len(order_amounts)) == 19,
    "customer_aov_mean": sum(customer_aovs) / len(customer_aovs) == 55,
    "aov_weighted_by_orders": sum(F(sum(v), len(v)) * len(v) for v in customer_amounts.values()) / len(order_amounts) == 19,
    "selected_complete_samples": selected_values == [100, 0],
    "selected_mean": F(sum(selected_values), len(selected_values)) == 50,
    "global_observed_mean": F(sum(source_values.values()), len(source_values)) == 200,
    "selected_active_mean": F(sum(active_selected), len(active_selected)) == 100,
    "selected_share_ratio": F(sum(selected_values), sum(source_values.values())) == F(1, 4),
    "external_output_has_uncovered_key": set(source_values) - selected_keys == {"C"},
    "spatial_totals": space_totals == [10, 10],
    "spatial_then_temporal_p95": exact_quantile(space_totals, F(95, 100)) == 10,
    "wrong_sum_of_device_p95": sum(exact_quantile(v, F(95, 100)) for v in source_samples.values()) == 19,
    "common_pair_count": len(valid_pairs) == 1,
    "common_pair_mean": paired_numerator / paired_denominator == 10,
    "wrong_independent_weight_sum": paired_numerator / sum(w for _, w in paired_rows) == 1,
    "weighted_groups": F(10 * 1 + 30 * 9, 1 + 9) == 28,
    "valid_zero_weight_is_not_empty": len(zero_weight_pairs) == 2 and sum(w for _, w in zero_weight_pairs) == 0,
    "row_measure_margin": line_margins == [20, 60] and sum(line_margins) == 80,
    "wrong_post_aggregate_product": sum(q for q, _, _ in line_items) * sum(p - c for _, p, c in line_items) == 150,
    "empty_and_all_null_sum_to_null": nullable_sums == {"empty": None, "all_null": None, "mixed": 100, "zero": 0},
}
for name, passed in expression_checks.items():
    assert passed, name
print(f"v0.30 expression witnesses: {len(expression_checks)} passed")
```


## 附录 I：第一性原理复审反例

v0.28 的独立数值见证。这里仅验证内外样本、计算阶段与信息不足反例；
函数名校验、权限继承、域绑定准入和实际保存行为仍是 §9 的未来实现验收，不用假实现证明。
附录 A 的命令会执行本段；原九段程序的数值算法保留，附录 C 仅同步两处 Field Ref 元数据。

```python
from collections import defaultdict
from fractions import Fraction as F

review_checks: list[str] = []


def check_review(name: str, actual: object, expected: object) -> None:
    assert actual == expected, (name, actual, expected)
    review_checks.append(name)


def customer_totals(rows: list[tuple[str, str, int]]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for _, customer, amount in rows:
        totals[customer] += amount
    return dict(totals)


def customer_mean(rows: list[tuple[str, str, int]]) -> F:
    totals = customer_totals(rows)
    return F(sum(totals.values()), len(totals))


def local_summaries(rows: list[tuple[str, str, int]]) -> dict[str, tuple[int, int]]:
    result = {}
    for region in sorted({region for region, _, _ in rows}):
        totals = customer_totals([row for row in rows if row[0] == region])
        result[region] = (sum(totals.values()), len(totals))
    return result


regional_revenue = {"East": 100, "West": 300}
shifted = {key: value + 10 for key, value in regional_revenue.items()}
check_review("metric.offset.cells", shifted, {"East": 110, "West": 310})
check_review("metric.offset.recompute", sum(regional_revenue.values()) + 10, 410)
check_review("metric.offset.sum.wrong", sum(shifted.values()), 420)
check_review("row.offset.one.row.each", sum(value + 10 for value in (100, 300)), 420)

rows = [("East", "A", 100), ("West", "A", 300), ("West", "B", 100)]
cohort = {"A", "B"}
actual_means = {}
cohort_means = {}
for region in ("East", "West"):
    local = [row for row in rows if row[0] == region]
    totals = customer_totals(local)
    actual_means[region] = customer_mean(local)
    cohort_means[region] = F(sum(totals.get(key, 0) for key in cohort), len(cohort))
check_review("per.actual.by.region", actual_means, {"East": F(100), "West": F(200)})
check_review("per.cohort.by.region", cohort_means, {"East": F(50), "West": F(200)})
check_review("per.overall.customer.mean", customer_mean(rows), F(250))
expanded = {(region, customer) for region in regional_revenue for customer in cohort}
check_review("per.expanded.keys", len(expanded), 4)
check_review("per.repeated.customer", {region for region, customer in expanded if customer == "A"}, {"East", "West"})

same_customer = [("East", "A", 100), ("West", "A", 300)]
different_customers = [("East", "A", 100), ("West", "B", 300)]
check_review("per.state.collision", local_summaries(same_customer), local_summaries(different_customers))
check_review("per.state.same.customer", customer_mean(same_customer), F(400))
check_review("per.state.different.customers", customer_mean(different_customers), F(200))
check_review("per.state.local.means", local_summaries(same_customer), {"East": (100, 1), "West": (300, 1)})


def linear_quantile(values: list[F], q: F) -> F:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lo = position.numerator // position.denominator
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (position - lo) * (ordered[hi] - ordered[lo])


devices = {"A": [F(10), F(0)], "B": [F(0), F(10)]}
p95 = {device: linear_quantile(values, F(95, 100)) for device, values in devices.items()}
check_review("per.device.p95", p95, {"A": F(19, 2), "B": F(19, 2)})
check_review("per.device.final.sum.wrong", sum(p95.values()), F(19))
time_totals = [sum(values[t] for values in devices.values()) for t in range(2)]
check_review("per.time.total.p95", linear_quantile(time_totals, F(95, 100)), F(10))
check_review("per.device.time.keys", len({(device, t) for device in devices for t in range(2)}), 4)
check_review("quantile.lower.endpoint", linear_quantile(devices["A"], F(0)), min(devices["A"]))
check_review("quantile.upper.endpoint", linear_quantile(devices["A"], F(1)), max(devices["A"]))

values = [F(10), F(30)]
scores = [F(1, 5), F(4, 5)]
check_review("weight.score.normalized", sum(v * w for v, w in zip(values, scores)) / sum(scores), F(26))
signed = [F(3), F(-1)]
check_review("weight.signed.outside.range", sum(v * w for v, w in zip(values, signed)) / sum(signed), F(0))
check_review("weight.zero.sum", sum((F(1), F(-1))), F(0))

joint_keys = {("A", "East"), ("B", "West")}
check_review("per.joint.keys", len(joint_keys), 2)
check_review("per.cartesian.wrong", len({(customer, region) for customer in cohort for region in regional_revenue}), 4)
scalar_domain = {()}
empty_domain: set[tuple[()]] = set()
check_review("per.zero.axis.once.per.outer", len({(region, key) for region in regional_revenue for key in scalar_domain}), 2)
check_review("per.empty.domain.no.samples", len({(region, key) for region in regional_revenue for key in empty_domain}), 0)

print(f"v0.28 review witnesses: {len(review_checks)} passed")
```

## 附录 J：Nullable 规约与优化边界

Nullable 规约沿用 v0.30；当前政策见语义 §3.4 和 DSL §4.4、§5.4。原生参考为
[pandas sum](https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Series.sum.html) 与
[nullable boolean 索引](https://pandas.pydata.org/pandas-docs/version/2.3/user_guide/boolean.html)。
借用原生缺失表示及忽略 NA 的规约规则；零分母按语义 §3.4 的统一政策处理，显式 error 与实际日历/映射/请求域检查失败仍由 Marivo 检查。

下面只验证 pandas 数值、样本和集合反例，不模拟 DSL、coverage 错误或 Store。
sum 不区分空输入与全 NA，mean 只保留和/有效数；没有为缺失原因建立另一套状态机。

```python
from itertools import product
from math import isnan
import pandas as pd

nullable_checks = []


def check_nullable(name, condition):
    assert condition, name
    nullable_checks.append(name)


def series(values):
    return pd.Series(values, dtype="float64")


empty, all_na = series([]), series([None, None])
check_nullable("sum.empty.and.all.na", empty.sum() == all_na.sum() == 0)
check_nullable("mean.empty.and.all.na", isnan(empty.mean()) and isnan(all_na.mean()))
x, y = series([1, None]), series([None, 2])
check_nullable("different.masks.not.linear", (x + y).sum() == 0 and x.sum() + y.sum() == 3)
value, weight = series([10, None]), series([1, 9])
valid = value.notna() & weight.notna()
check_nullable("paired.mean", (value[valid] * weight[valid]).sum() / weight[valid].sum() == 10)
check_nullable("independent.weights.change.sample", (value * weight).sum() / weight.sum() == 1)
members = pd.Series([100, pd.NA], index=["A", "B"], dtype="Int64")
predicate = members > 50
check_nullable("predicate.complement", members[~predicate].empty and members.index.difference(members[predicate].index).tolist() == ["B"])
groups = pd.DataFrame({"region": ["East", None], "amount": [100, 20]})
check_nullable("null.group.key", groups.groupby("region", dropna=False)["amount"].sum().sum() == 120)
check_nullable("default.group.drops.contribution", groups.groupby("region")["amount"].sum().sum() == 100)

partition_checks = 0
for length in range(5):
    for values in product([None, -1, 0, 2], repeat=length):
        full = series(values)
        for cut in range(length + 1):
            parts = [series(values[:cut]), series(values[cut:])]
            total = sum(part.sum() for part in parts)
            valid_count = sum(part.count() for part in parts)
            assert total == full.sum()
            assert valid_count == full.count()
            assert total / valid_count == full.mean() if valid_count else isnan(full.mean())
            partition_checks += 1
print(f"nullable witnesses: {len(nullable_checks)} cases and {partition_checks} partition checks passed")
```

未来真实实现另验收：默认除零生成 NA、显式 error 失败、必要检查失败不发布部分结果、
无状态列的冷恢复与按需读取、必要检查不被 where/select/row_count 删除、优化前后相同有效样本。
历史记录中四态或空/全 Null 区分的要求已由 v0.30 取代；不能把历史检查计作本次 Runtime 通过。

## 附录 K：统一零分母政策的独立数值见证

v0.33 的唯一政策定义见[语义 §3.4](../semantic/expression-model-redesign.md#34-样本政策的归属)。
下列参考计算只核对有效输入掩码、普通 NA 和后续样本选择；它不是 Marivo 的政策解析器、
异常实现或后端验收。严格模式应拒绝的输入由独立掩码列出，不用原生 pandas 的 inf 代表结果。

```python
from fractions import Fraction as F

import pandas as pd

division_checks = []


def check_division(name, condition):
    assert condition, name
    division_checks.append(name)


numerator = pd.Series([6, 0, 5, None, 0, None, 5, 5], dtype="Float64")
denominator = pd.Series([3, 0, 0, 0, None, None, None, -2], dtype="Float64")
valid = numerator.notna() & denominator.notna()
zero = denominator.eq(0).fillna(False)
strict_failures = valid & zero
nullable_ratio = numerator / denominator.mask(zero, pd.NA)
actual = [None if pd.isna(value) else F(float(value)) for value in nullable_ratio]
check_division("ratio.truth.table", actual == [F(2), None, None, None, None, None, None, F(-5, 2)])
check_division("strict.only.valid.zero", strict_failures[strict_failures].index.tolist() == [1, 2])
check_division("na.numerator.zero.denominator", pd.isna(nullable_ratio.iloc[3]) and not strict_failures.iloc[3])
check_division("na.changes.mean.sample", nullable_ratio.count() == 2 and nullable_ratio.mean() == -0.25)
check_division("zero.fill.changes.mean", nullable_ratio.fillna(0).mean() != nullable_ratio.mean())

current = pd.Series([90, 0, 100, None], dtype="Float64")
baseline = pd.Series([0, 0, 50, 20], dtype="Float64")
absolute = current - baseline
relative = absolute / baseline.abs().mask(baseline.eq(0), pd.NA)
check_division("compare.absolute.survives", absolute.iloc[:3].tolist() == [90, 0, 50])
check_division("compare.relative.zero", relative.isna().tolist() == [True, True, False, True] and relative.iloc[2] == 1)

signed = pd.Series([10, -10], dtype="Float64")
total = signed.sum()
share_denominator = pd.Series([total, total], dtype="Float64")
shares = signed / share_denominator.mask(share_denominator.eq(0), pd.NA)
check_division("share.cancelling.total", total == 0 and shares.isna().all())

value = pd.Series([10, 20], dtype="Float64")
weight = pd.Series([1, -1], dtype="Float64")
paired = value.notna() & weight.notna()
check_division("weighted.nonempty.zero.sum", paired.sum() == 2 and weight[paired].sum() == 0)
missing = pd.Series([None, None], dtype="Float64")
empty_pairs = missing.notna() & weight.notna()
check_division("weighted.no.valid.pairs", not empty_pairs.any())

print(f"division policy witnesses: {len(division_checks)} passed")
```

未来真实实现复用 §9.1 的 48、56、68、80 等条目：核对各阶段同默认、同参数、默认/显式 null
规范化一致，显式 error 保留到执行检查；compare/share 不覆盖严格子式；旧模型声明拒绝且修复可执行。
不把上述十项数值见证计为这些构造、绑定、错误、优化或加载合同已经通过。

## 附录 L：截断月桶上卷与份额的独立数值见证

本段复算 §7.12 的金额、订单分母、实际区间和日份额；只使用日期集合与 Fraction，
不实现 CalendarAxis、Query 或模拟 Runtime 拒绝。跨月周的两个月集合仅说明不存在唯一父月；
真实 coarsen 准入、读取范围和充分组件路线仍由 §9 的未来实现验收负责。

```python
from datetime import date, timedelta
from fractions import Fraction as F

rows = [
    (date(2026, 9, 28), F(10), 1),
    (date(2026, 9, 29), F(10), 1),
    (date(2026, 9, 30), F(10), 1),
    (date(2026, 10, 1), F(100), 1),
    (date(2026, 10, 2), F(900), 99),
    (date(2026, 10, 3), F(0), 0),
    (date(2026, 10, 4), F(40), 1),
]
start, end = date(2026, 9, 29), date(2026, 10, 3)
selected = [row for row in rows if start <= row[0] < end]
months = sorted({(day.year, day.month) for day, _, _ in selected})
groups = {month: [row for row in selected if (row[0].year, row[0].month) == month] for month in months}
totals = {month: sum((amount for _, amount, _ in group), F(0)) for month, group in groups.items()}
counts = {month: sum(count for _, _, count in group) for month, group in groups.items()}
aovs = {month: totals[month] / counts[month] for month in months}
shares = [amount / totals[day.year, day.month] for day, amount, _ in selected]
bounds = {month: (group[0][0], group[-1][0] + timedelta(days=1)) for month, group in groups.items()}
october_daily_mean = sum((amount / count for _, amount, count in groups[2026, 10]), F(0)) / 2
cross_month_week = [date(2026, 9, 28) + timedelta(days=index) for index in range(7)]
witnesses = {
    "half_open_scope": [day for day, _, _ in selected] == [start + timedelta(days=index) for index in range(4)],
    "monthly_income": list(totals.values()) == [F(20), F(1000)],
    "monthly_orders": list(counts.values()) == [2, 100],
    "original_aov": list(aovs.values()) == [F(10), F(10)],
    "daily_shares": shares == [F(1, 2), F(1, 2), F(1, 10), F(9, 10)],
    "partition_totals": sum(shares[:2]) == sum(shares[2:]) == 1,
    "completed_aov_is_not_sufficient": october_daily_mean == F(600, 11) != aovs[2026, 10],
    "actual_bounds": list(bounds.values()) == [(start, date(2026, 10, 1)), (date(2026, 10, 1), end)],
    "week_has_no_unique_month": len({(day.year, day.month) for day in cross_month_week}) == 2,
}
for name, passed in witnesses.items():
    assert passed, name
print(f"calendar rollup/share witnesses: {len(witnesses)} passed")
```
