# Lazy Analysis DSL：五个业务场景与时间分析的设计验证

Date: 2026-10-17

Status: design validation；模拟数据与独立数值校验，不是新 DSL 的运行时验收。

实施状态（`405bd006f8`）：R3–R8 已完成约定的本地实施、常规门禁与六条业务链的独立执行验收，
另含终端冷读、身份边界和审查问题探针；本文的历史 oracle 不因此变成执行日志。
新数值 Query 仅准入 DuckDB，远端后端仍需代码路线扩展；完整 slow、远端 Runtime 和 release 验收未完成。

验证对象是 [Lazy Analysis DSL 定义](lazy-analysis-dsl-redesign.md)
proposed v0.37，理论依据是 [核心分析代数](algebra-theory.md) proposed v0.25。
执行与交接合同见 [执行、保存与结果交接](lazy-analysis-dsl-execution.md)，
完整组合示例见 [多轮分析示例](lazy-analysis-dsl-examples.md)。
附录中的版本、HEAD 和 SHA-256 属于当时的历史核验记录，不用于定位拆分后的文档。
附录 A 的 189 项历史数值代码保持原样；C/H/J 按 nullable 规则校验，K 验证统一零分母政策的数值边界，其余历史数值标识保留；其中 Domain/domain 是历史集合术语，不是当前目标 API 名称。

本文与 v0.37 提案及 [统一语义表达式](../semantic/expression-model-redesign.md) 同步修订。
本轮补齐粗化组合、身份对应、属性版本读取和披露代码；首版移除外部业务缺口声明及消费承诺。
本次补齐声明改写、分阶段检查、元组谓词、浮点优化、完整期间配对和格式边界的关键反例；
移除资源预算要求，沿用 §9 的验收入口，不扩展新的验收矩阵。
继续采用 Ibis 源端计算、pandas 统一本地执行、PyArrow 存储/读取和终端 Artifact。
既有数值 oracle 保持不变；附录 K 的零分母见证仍有效，新增合同条款的真实 DSL 验收由各阶段实际测试记录承担。
2026-10-17 fanout 修订的表达边界与少量独立数值见证见 §9.6；不固定执行策略，不计入189项历史 oracle。
来源使用类型化行表达式与接收者规约，指标使用同一算术与显式 per 样本规约；观察上下文只绑定一次，Query.reduce 只保留 sum/mean/row_count；普通分类、描述统计与格子窗口交 pandas。
此前 175 项 oracle 与后续 6 项 P95/比率、8 项贡献/配对见证均保持原样，共 189 项独立数值检查；
这些代码逐字保留；pandas 片段与交接反例独立执行。v0.22 的附录 D 域见证保留，
v0.23 的附录 E 关系/稀疏域/时间见证保留；v0.24 在附录 F 补充份额总体、分组和筛选顺序见证。
v0.25 移除逐次观察的完整性声明，正常来源默认信任；当时的外部缺口设想已退出首版，历史集合见证不代表当前能力。
v0.25 同时收窄边缘能力，场景改用重新 observe、先 share 后筛选、在原 observe 请求 by 等替代路径；取舍集中记录于 §8.6。
v0.27 将 Measure/Metric 统一为分阶段计算表达式，装饰器只负责业务声明，政策归各算子；
显式不可变域继续合法，模型收录另查持久解析闭包。附录 G 保留域数值见证，附录 H 补齐五个核心场景。
v0.28 复审补充函数名声明、可推导事实继承、各外部格子内的 per 样本、非可加权重、
Metric 常数平移及嵌套规约的保存信息反例；数值见证在附录 I。字段 Ref 与 pandas 轴元数据同步为 Entity 限定路径。
v0.29 将分析总体及 DSL 示例统一为 Population、population() 和 in_population(...)；不改变数值预期或沿用历史检查作为新验收。
v0.30 采用 nullable value，sum 空/全 NA 默认均为 NA，来源/内部样本显式 empty="zero" 时为 0；非法运算及实际覆盖检查失败使执行失败。
删除逐格四态/原因与 empty 参数，pandas 示例只读取 value/coordinate/presence。
v0.32 将 execute 的产出统一为终端 Artifact；所有 DSL 组合在 Query 上完成，执行后只读、展示或 to_pandas。
原图和内部组件不附着于产物，实际 Finding/Evidence 是自包含输出；新产物和重开产物无能力差异。
附录历史记录中的 Artifact/fixed API 已退出目标合同；数学见证仍说明信息下界，不授权产物续算。
仍不构成 Marivo 导出器、构造器或 Runtime 验收。
本文不修改实现、公开导出或 packaged skills；独立数值见证与实际 API/Runtime 验收仍须分别记录。
全部指标观察统一使用 Session.observe。电商、制造和时间分析直接从 Metric 出发；
SaaS 固定账户与实验单位才显式绑定身份约束，并明确输出完整身份轴，以保留零活动主体。
所有域统一为 Population；执行前身份复用是 Query 已有身份轴投影，不保留隐藏成员关系。

## 1. 结论与验证方法

**以下问题采用 Marivo 语义计算与 pandas 后处理共同完成。Marivo 只保留有明确增量价值的操作；
不以所有自然追问都留在 DSL 内作为完备目标。** 原窗口在完整输入上直接求值；换窗口一律重新 observe。

| 方向 | 核心业务问题 | 设计层判断 | 主要边界 |
| --- | --- | --- | --- |
| 电商经营 | 销售增长为何伴随净额下降，毛利及预算达成如何？ | 已授权行金额、聚合前毛利计算、多根指标、临时预算 | 数量×单价的可加性须由 Measure 声明；退款期与订单期分开 |
| SaaS 客户运营 | 排除新客户后，原客户扩张、收缩、流失各有多少？ | Marivo 固定成员/比较/流失主体；pandas 计算 GRR 和分类汇总 | 不包含事件型留存和流失预测 |
| 制造质量 | 为什么每种产品都更好的工厂，总不良率反而更差？ | 分层率、来源权重与临时不等权重的直接标准化 | 缺少正权重层必须失败；标准化不是因果调整证明 |
| 物流服务 | 每个枢纽最差的是哪天，P95 是否恶化？ | 完整日网格、分组 Top-N、目标粒度重新观察精确分位数；滚动 P95 见 §7 | 需语义层先提供时长事实；SLA 截止时间和删失估计不在本版 |
| 产品实验 | 新方案是否提高单位收入，预设人群是否表现不同？ | Marivo 完整单位/同口径均值比较；pandas 分层、分位数与后续统计 | 没有完整的显著性检验、区间估计、聚类重抽样 DSL |
| 时间分析 | 七日收入、累计收入、同比环比及 P95 如何连续观察？ | 日历窗口（含小时粒度回看）、明确累计起点和重置、期间对应 | 缺桶不跳过；直接计算无需紧凑状态；不包含预测、工作日窗口或通用事件序列 |

三个问题分开判定：

1. **合理性**：身份、贡献、时间、坐标、统计单位和保留状态是否能给出唯一解释。
2. **便利性**：改变业务问题需要改哪些表达式；与同信息的 pandas 路径相比，Marivo 自动承担了哪些事实，交接是否明确。
3. **完备性**：核心问题能否通过已声明的 Marivo→pandas 路径完成；区分缺少语义输入、必要合同与有意外置的处理。

证据分为三层，不能互相替代：

- **提案核对**：逐项检查下文调用是否出现在 v0.37 的签名和规则中，检查绑定、单位和执行内组合前提。
- **独立数值 oracle**：附录使用 Python 标准库和 `Fraction`，从明确数据计算预期结果与反例。
  它不实现或调用 DSL，也不模拟 `AnalysisError`，只能验证算式和反例。
- **未来实现验收**：将同一批数据送入真正的新 DSL，对照数值、NA、必要错误、保留能力和读取边界。
  本次没有执行新 DSL，不能证明 API 类型绑定、编译、真实 to_pandas、后端、恢复或性能通过。
- **pandas 替代路径**：从符合目标导出布局的独立合成表执行本文原样 pandas 片段，验证算值和缺失值/日期检查；不模拟 Marivo 执行或声称导出实现已通过。

本文数据是可手工复核的小型反例核；每行是一个真实业务粒度，而非为了凑结果拼出的宽表。
各数据集在数值核验期间保持不变。这是测试输入条件，不是给 DSL 增加跨读取一致性要求。
对于实际可变来源，`execute()` 得到的结果仍服从[执行与恢复 §1.1.1](lazy-analysis-dsl-execution.md#111-共享逻辑节点与执行内输入)；固定名单也不冻结后续属性或指标。
小数据验证不代表百万客户、亿级事件或完整精确分布的资源验收。
分位数预期值按十进制参数的精确有理数计算；真实浮点结果应按方法声明的精度核对，
不据此要求任意执行次序下二进制逐位相等。

以下 Python 代码是**提案语法示例，不能直接在当前 Marivo 版本运行**。
各场景独立使用已加载的模拟 Catalog；表中 Entity、字段、单位、Relationship 是必要输入契约，
不是让 Analysis 根据名称猜出业务含义。所有金额均为 CNY，无汇率换算。
共同导入如下；每个场景先创建自己的 Session，再用其 time_scope/instant 构造时间值。
省略 timezone 时使用该 Session 的报告时区，场景内全部已归属对象属于同一 Session。

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

先补全可实际声明的语义输入。在已有 commerce domain 与 Orders Entity 的语义模块内，
用下面的声明定义相关字段；同名旧声明应替换，不能重复注册。装饰器体返回受限的行表达式，
单位和可加性由新 Measure 声明授权；模型与 runtime 都遵守同一合同，loader 才收录稳定名称。
目标声明直接返回 Measure，函数名 order_amount 决定局部字段名，所属 Entity 来自行表达式；
loader 收录为 `commerce.orders.order_amount`。模型与 runtime 都无需再填写名称字符串。

```python
Orders = ms.ref.entity("commerce.orders")
quantity = ms.measure_column(
    name="quantity", entity=Orders, column="quantity", additivity=ms.additive_all(), unit="1"
)
unit_price = ms.measure_column(
    name="unit_price", entity=Orders, column="unit_price", additivity=ms.non_additive(), unit="CNY"
)
unit_cost = ms.measure_column(
    name="unit_cost", entity=Orders, column="unit_cost", additivity=ms.non_additive(), unit="CNY"
)


@ms.measure(additivity=ms.additive_all())
def order_amount():
    return quantity * unit_price


@ms.measure(additivity=ms.additive_all())
def order_cost():
    return quantity * unit_cost


@ms.measure()
def order_margin():
    return order_amount - order_cost
```

这里明确声明的是订单金额/成本的业务意义，没有将单价改为可加，也没有让 Analysis 猜测两个乘积与某声明相等。
这里为毛利增加可发现的业务名称，单位/权限直接继承，省略参数不会清空已有事实；临时写法无需额外声明：
`(order_amount - order_cost).sum(time=paid_at)`。Analysis 与模型使用同一行表达式：

```python
catalog = ms.load()
s = mv.session.get_or_create("scenario-commerce", report_timezone="Asia/Shanghai")
august = s.time_scope(start="2026-08-01", end="2026-09-01")
september = s.time_scope(start="2026-09-01", end="2026-10-01")
Orders = ms.ref.entity("commerce.orders")
order_amount = catalog.fields.get(ms.ref.measure("commerce.orders.order_amount"))
order_cost = catalog.fields.get(ms.ref.measure("commerce.orders.order_cost"))
RefundAmount = ms.ref.measure("commerce.refunds.refund_amount")
PaidAt = ms.ref.time_dimension("commerce.orders.paid_at")
PostedAt = ms.ref.time_dimension("commerce.refunds.posted_at")
region = catalog.fields.get(ms.ref.dimension("commerce.stores.region"))
channel = catalog.fields.get(ms.ref.dimension("commerce.stores.channel"))
sales = order_amount.sum(time=catalog.fields.get(PaidAt))
gross_margin = catalog.fields.get(ms.ref.measure('commerce.orders.order_margin')).sum(time=catalog.fields.get(PaidAt))
refunds = catalog.fields.get(RefundAmount).sum(time=catalog.fields.get(PostedAt))
order_count = catalog.entities.get(Orders).count(time=catalog.fields.get(PaidAt))
metrics = {
    "sales": sales,
    "refunds": refunds,
    "orders": order_count,
    "net": sales - refunds,
    "aov": sales / order_count,
    "gross_margin": gross_margin,
}
current_q = s.observe(metrics, during=september, by=(region,))
baseline_q = s.observe(metrics, during=august, by=(region,))
comparison_q = current_q.select({"net": current_q["net"]}).compare(
    baseline_q.select({"net": baseline_q["net"]})
)
comparison = comparison_q.execute()
comparison_frame = comparison.to_pandas()
(region_column,) = [
    axis["column"] for axis in comparison_frame.attrs["marivo"]["axes"]
    if axis["kind"] == "field"
]
declining = comparison_frame.loc[comparison_frame[("value", "net.absolute")] < 0]
focus_rows = declining.sort_values(
    by=[("value", "net.absolute"), ("coordinate", region_column)]
).head(1)
focus = mv.coordinates(
    by=(region,),
    keys=tuple((value,) for value in focus_rows[("coordinate", region_column)].tolist()),
)
current_detail = current_q.where(mv.in_population(focus)).drill(add=(channel,))
baseline_detail = baseline_q.where(mv.in_population(focus)).drill(add=(channel,))
detail = current_detail.select({"net": current_detail["net"]}).compare(
    baseline_detail.select({"net": baseline_detail["net"]}), how="outer"
).execute()
east_net = s.observe({"net": sales - refunds}, where=region == "East", during=september).execute()
sales_shares = current_q.share("sales")
focus_share = sales_shares.where(mv.in_population(focus)).execute()
budget = s.values(
    by=(region,),
    columns={"budget": "CNY"},
    rows=(
        (("East",), {"budget": 300}),
        (("West",), {"budget": 200}),
    ),
)
budget_joined = current_q.align(budget)
budget_readout = budget_joined.select(
    {
        "sales": budget_joined["left.sales"],
        "budget": budget_joined["right.budget"],
        "attainment": budget_joined["left.sales"] / budget_joined["right.budget"],
    }
).execute()
```

预算的 CNY 在 columns={"budget": "CNY"} 中声明一次；每行直接提供数值。
净额阈值 0 在比较位置采用已有净额列的单位，不把预算旧列当作可重新赋单位的常量。
单位冲突、未知单位和已绑定参照沿 [DSL §4.1.2](lazy-analysis-dsl-redesign.md#412-常量与独立参照列的单位) 检查。

east_net 的公共条件同时约束订单和退款，保留各自时间角色；任一根无法解释 region 就拒绝整个绑定。
单独退款条件在基础退款 Metric 上形成新的固有过滤定义，再组合。
上述观察没有门店主体限制。订单与退款组件各自沿已声明的唯一关系路径读取 region/channel，
再分别规约到共同坐标；各组件的时间角色保持 paid_at/posted_at。by 的普通字段域来自完整贡献分类元组的并集。
这不把四家门店的全集当作输出域；本例两期均有 East/West 贡献，因而地区 exact 可配对。
若要求无贡献门店也出现，须显式请求完整门店身份域或 member() 输出轴；
仅绑定 members 不自动补齐门店的全部地区。完整地区域用 by=population 明确给出，并参与 空输入/覆盖判定。

固有时间角色由基础定义拥有，唯一声明路径由观察解析；路径缺失或歧义时构造即报结构化错误，修复指向在语义层声明唯一路径。没有把订单与多笔退款联接成一张待求和宽表。
share 只为本次 Query 输入中的 sales 构造当前总体，保留原列并新增 sales.share；其余指标不参与分母。
先算份额再选 focus，分母仍为全部地区的470。具体默认、分组和筛选顺序见 §2.4。
`focus` 是脚本从 pandas 输出显式选出的字面量地区，`detail` 是新来源观察，不承诺与上一份比较对账。
在本次不变数据上才能逐项得到下面的固定答案。

### 2.3 预期结果与反例

| 地区 | 8 月销售/退款/净额 | 9 月销售/退款/净额 | 净额变化 |
| --- | --- | --- | ---: |
| East | 300 / 20 / 280 | 320 / 150 / 170 | -110，约 -39.29% |
| West | 60 / 0 / 60 | 150 / 0 / 150 | +90，+150% |
| 全部 | 360 / 20 / 340 | 470 / 150 / 320 | -20，约 -5.88% |

East 下钻后 Web 为 90、Store 为 80，分别比基期变化 -90、-20。
East 的本期销售份额为 `320/470 = 32/47 ≈ 68.09%`。
全体销售订单均额从 `360/4=90` 变为 `470/5=94`；不能取地区 AOV 的简单平均。
本期销售毛利为 80，来自 O5 的 20 和 O7 的 60；这两单的数量和乘单件毛利和却为 `(2+3)×(10+20)=150`。
地区销售预算达成率为 East `320/300=16/15`、West `150/200=3/4`，超过 100% 是合法结果。

需要拒绝或区分的情况：

- 若把所有订单直接 left join 全部退款再求订单金额，O1 被复制三次，累计销售由 830 错成 1070。
  v0.12 §5.1 的多根独立贡献规约正好避免这种 fanout。
- 若用 9 月支付订单限制所有退款，会漏掉 R2/R3/R5 的 140 元；这改变了问题定义。
- S4 是合法零业务门店，收入为 0；本例 AOV 默认 null 政策返回 NA。
  若显式改为 zero_denominator="error"，该次观察须因 0/0 失败；Top-N 的 NA 分数排末或显式排除。
- 比较 Query 与任何 Artifact 都不提供 drill。要看渠道细项，在上面的两个端点来源 Query 上分别
  `drill(add=(channel,))`，再明确选择细比较的 how。
- 地区净额差额可以描述增减来源，但不证明退款增长的因果原因；完整因果或分解方法不由 `share` 提供。
- 重复地区预算、错误币种或将金额列混成 bool/int 不应自动修复；缺少 West 的预算不能 exact 对齐。
- 直接对未经声明的数量×单价乘积请求来源 sum 仍须拒绝；同根、单位正确不等于该业务量已授权。
  修复是显式引用上面的订单金额 Measure，不是把原单价的 non_additive 改掉，也不是增加许可绕过参数。
- 非可加单价乘零、相消或过滤后仍不能直接进入 sum；两个可加 Measure 相乘也不自动获得许可。
  对半可加金额相减不能取消其固定轴约束；这些是待实施的权限反例，不是数值 oracle 的通过项。
- 用另一个最小输入 `(order_amount, order_cost)=(180,200),(120,60)` 区分逐行筛选和聚合后处理：
  先形成 `margin = order_amount - order_cost`，再用 `margin.sum().where(margin > 0)` 得 60；
  先求和再取正值只有 40。来源条件不能被下推/上提成另一统计，不需要增加通用条件表达式入口。

**评价**：多根贡献和多时间角色合理；异常域接回来源比较的写法可用。
本场景以新 Measure 声明授权乘积，字段相减和来源筛选仍在规约前完成；政策留在具体规约上。
需要为新的可加业务量增加语义声明是明确的使用成本，不能宣称任意临时字段乘积都能直接求和。
主要使用成本是同时理解“已展示比较”和“新下钻观察”的输入权威。

### 2.4 默认总体、分组占比与筛选顺序

`current_q.share("sales")` 的业务意思是“各地区销售额占当前全部地区销售总额的比例”。
调用者只选指标；API 自动求总体、对应分母并新增 sales.share，不必先 select、rollup 再引用列。
原 current_q 及其 sales/refunds/aov 等列不变；新查询保留这些列，只为 sales 计算份额。

```python
focus_only = current_q.where(mv.in_population(focus))
focus_local_share = focus_only.share("sales").execute()

channel_sales = s.observe(
    {"sales": sales}, during=september, by=(region, channel)
)
channel_global_shares = channel_sales.share("sales").execute()
channel_region_shares = channel_sales.share("sales", partition_by=(region,)).execute()
```

在本题不变模拟输入上，focus 选中 East：

| 结果 | East 的 sales.share | 总体范围 |
| --- | --- | --- |
| §2.2 的 focus_share：先 share 后筛选 | 320/470 = 32/47 | 原全部地区 |
| focus_local_share：先筛选后 share | 320/320 = 1 | 当前所选 East |

channel_sales 有三个贡献分组；West/Store 没有贡献且本例未请求完整渠道网格。
自动分组总体只使用当前范围，输出仍保留地区×渠道细坐标：

| 地区/渠道 | sales | 默认占全部地区渠道 | partition_by=(region,) 的组内份额 |
| --- | ---: | ---: | ---: |
| East/Web | 200 | 200/470 = 20/47 | 200/320 = 5/8 |
| East/Store | 120 | 120/470 = 12/47 | 120/320 = 3/8 |
| West/Web | 150 | 150/470 = 15/47 | 150/150 = 1 |

partition_by 只划分分母，不合并输出行。地区×月份数据默认跨全部当前地区和月份；
每月占比须明确选择月份轴，不自动按时间字段猜分母。先算组内份额再只选 East/Web 仍为5/8，
先只选该格再算自动份额则为1。where/top 后置时不得改变已经绑定的总体。
要对筛选前总体求份额，先 share 再 where/top；share 不接受外部参照列或裸数值分母，仅同指标、同单位或数值相等不构成支持依据。
列名冲突、非数值列、AOV 等非贡献量、零分母、NA 与覆盖错误和缺共同输入路线的真实处理仍待实现验收。
附录 F 只独立验证算式与范围区别，不模拟这些 DSL 行为。

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
s = mv.session.get_or_create("scenario-saas", report_timezone="Asia/Shanghai")
august = s.time_scope(start="2026-08-01", end="2026-09-01")
september = s.time_scope(start="2026-09-01", end="2026-10-01")
Accounts = catalog.entities.get(ms.ref.entity("saas.accounts"))
mrr = catalog.fields.get(ms.ref.measure('saas.recurring_lines.mrr_amount')).sum(time=catalog.fields.get(ms.ref.time_dimension('saas.recurring_lines.month_at')))
cohort = mv.identities(Accounts, keys=(("t1", "A"), ("t1", "B"), ("t2", "A")))
baseline = s.observe({"mrr": mrr}, members=cohort, during=august, by=(mv.member(),))
current = s.observe({"mrr": mrr}, members=cohort, during=september, by=(mv.member(),))
change_query = current.compare(baseline)
change = change_query.execute()
churned = change_query.where(change_query["mrr.current"] == 0)
churned_accounts = s.query(churned.population().project(by=(mv.member(),))).execute()
mean_account_mrr = current.reduce({"mean_mrr": current["mrr"].mean()})
baseline_mean = baseline.reduce({"mean_mrr": baseline["mrr"].mean()})
mean_change = mean_account_mrr.compare(baseline_mean).execute()
saas_export = change.to_pandas()
```

三个完整身份由脚本根据基期记录明确选定；如果来自旧 Artifact，应先 to_pandas 读取再显式提供这些键。
它们固定名单，不冻结两期 MRR；本例 baseline/current 都是新的来源 Query。
两端使用相同身份绑定，恒等比较保留共享账户身份轴；Query 内可在执行前投影流失身份。
这里 change、churned_accounts、mean_change 是独立 execute，可变来源下可能重新取值。
若要严格保留已见基期值或从这份精确 change 提取流失账户，应在 pandas 处理其导出表。
复合键 (tenant_id, account_id) 是完整身份，不从普通字段或组汇总反推名单。

GRR、收入比率和分类汇总在本题是终端处理，直接使用同一张已对齐的导出表：

<!-- pandas-example:saas-readout -->
```python
import pandas as pd

names = ["mrr.current", "mrr.baseline", "mrr.absolute"]
if saas_export["value"][names].isna().any().any():
    raise ValueError("The SaaS readout requires non-missing endpoint values.")
account_values = saas_export["value"][names].copy()
current_mrr = account_values["mrr.current"]
baseline_mrr = account_values["mrr.baseline"]
retention_readout = {
    "nrr": current_mrr.sum() / baseline_mrr.sum(),
    "grr": current_mrr.clip(upper=baseline_mrr).sum() / baseline_mrr.sum(),
    "accounts": len(account_values),
    "expansion_gain": (current_mrr - baseline_mrr).clip(lower=0).sum(),
}
movement = pd.Series("unchanged", index=account_values.index, dtype="string")
movement.loc[current_mrr > baseline_mrr] = "expansion"
movement.loc[current_mrr < baseline_mrr] = "contraction"
movement.loc[current_mrr == 0] = "churn"
account_values["movement"] = movement
account_values["movement_amount"] = account_values["mrr.absolute"].abs()
movement_summary = account_values.groupby("movement", dropna=False).agg(
    accounts=("mrr.current", "size"),
    net_change=("mrr.absolute", "sum"),
    movement_amount=("movement_amount", "sum"),
    current=("mrr.current", "sum"),
    baseline=("mrr.baseline", "sum"),
)
```

两端列来自同一份已对齐输入，不跨表按RangeIndex猜对应。本例基期为正且MRR非负；
外部换公式、忽略 NA 或换人群需自行明确，不会回写原Artifact。

### 3.3 预期结果与反例

- 固定客户群：`{(t1,A), (t1,B), (t2,A)}`；完整身份数为 3，而非 account_id 去重后的 2。
- 基期 400，本期 250，`NRR = 250/400 = 62.5%`。
- 扩张 50、收缩 100、流失 100；`400 + 50 − 100 − 100 = 250`。
- `GRR = (100 + 100 + 0)/400 = 50%`；公式里的 capped 为逐账户 `min(current, baseline)`。
- 等权账户收入均值为 `250/3`；平均账户收入保留比例为 `(1.5 + 0.5 + 0)/3 = 2/3`，不能替代 NRR。
- 全体账户从 400 到 380，只下降 5%；不能拿全体 `380/400=95%` 冒充固定客户群 NRR。
- `churned_accounts` 只有完整身份 `(t2,A)`；不能误选仍在扩张的 `(t1,A)`。

本题的 NRR/GRR 是明确的收入比率，使用普通 `/`；扩张后 NRR 可以超过 100%，
它不是需要共同贡献包含证明的构成份额。

**价值判定**：Marivo 保留完整复合身份、固定人群、零活动坐标、端点对齐和Query 身份坐标的投影与完整键导出。
pandas 已足够计算 GRR 和三类账户的汇总：各一户，净变50/−100/−100，变动金额50/100/100；
对应本期合计150/100/0、基期100/200/100。无 unchanged 样本时不虚构这一组。
标签没有新增主体权威，也不需要建立不能跨输入复用的 DSL 分类绑定。
要追问流失账户，在 execute 前沿 change_query.where→population→project 组合，或直接从 saas_export 提取完整键；Artifact 没有 DSL 方法。
D7 事件留存、连续不活跃或流失概率仍需要另有明确语义，不能从本月收入为零推断。

### 3.4 固定名单读取属性与精确版本选择

此补充场景使用独立的 `crm.customers`，不改变上面的非版本化账户模型。
Catalog 已声明 customer_id 为完整主键，tier 为普通 Dimension，valid_from/valid_to 为同 Entity 的时间字段；
Entity 采用 Asia/Shanghai 的 closed_open validity，None 表示开放结束。数据如下，时间均为当地零点：

| customer_id | valid_from | valid_to | tier |
| --- | --- | --- | --- |
| C1 | 2026-10-01 | 2026-11-01 | Silver |
| C1 | 2026-11-01 | None | Gold |
| C2 | 2026-10-01 | None | Bronze |
| C3 | 2026-11-01 | None | Gold |

固定 C1/C2，只读取两个精确时点的属性；这份名单不从当前 Gold 客户重新选择。

```python
catalog = ms.load()
attribute_s = mv.session.get_or_create("scenario-customer-versions", report_timezone="Asia/Shanghai")
customers_ref = ms.ref.entity("crm.customers")
customers_entity = catalog.entities.get(customers_ref)
tier = catalog.fields.get(ms.ref.dimension("crm.customers.tier"))
october = attribute_s.time_scope(start="2026-10-01", end="2026-11-01")
fixed_customers = attribute_s.query(
    mv.identities(customers_entity, keys=(("C1",), ("C2",)))
).population()
attributes = attribute_s.read(fixed_customers, {
    "tier_at_start": tier.at(october.start),
    "tier_at_end": tier.at(october.end),
})
selected_gold = attributes.where(attributes["tier_at_end"] == "Gold").population()
gold_query = attribute_s.query(selected_gold)
attributes.contract().show()
attributes_artifact = attributes.execute()
gold_artifact = gold_query.execute()
attributes_artifact.show()
gold_artifact.show()
```

属性表仍有 C1/C2 两个完整身份，值分别为 `(Silver, Gold)`、`(Bronze, Bronze)`；Gold 身份结果只有 C1，不含新增 C3。
Session.query 在读取属性前绑定字面量名单的 Session，population() 复用其逻辑键域；这一步不执行或查询身份存在性。
`read` 的新属性读取和两个锚点在 Query.contract 中可见；构造与 contract 不读取来源，两个 execute 独立求值。
`at=october.end` 精确命中 11 月 1 日的新版本；显式 start 可以读取当时的 Silver，但不代表取得期间结束前的最后版本。
首版没有左极限选择，不能用 end 减 tick 代替。非版本化字段不能接受伪历史选择；
将名单增加 C404 后，必需属性映射缺失应在执行检查中失败，不删该键或补普通 NA。
来源不可用时也必须失败，不能退回前一次 Artifact 的属性。
同信息的 pandas 路径须由调用方自行按完整身份和闭开区间连接、检查每个时点恰好有一个属性版本；
本场景检验的是由声明版本规则承担这些绑定，不是 pandas 无法完成该连接。

### 3.5 不同 Entity 的声明身份对应

另有非版本化的 `saas.crm_accounts` 与 `saas.billing_accounts`，两者完整主键均为 `(tenant_id, account_id)`，
各主键分量都是直接 Dimension。相同键值仍属于不同 Entity；在已有 saas 语义模块中声明以下关系并加载：

```python
AccountMatch = ms.relationship(
    name="crm_to_billing",
    from_entity=ms.ref.entity("saas.crm_accounts"),
    to_entity=ms.ref.entity("saas.billing_accounts"),
    keys=[
        ms.join_on(
            ms.ref.dimension("saas.crm_accounts.tenant_id"),
            ms.ref.dimension("saas.billing_accounts.tenant_id"),
        ),
        ms.join_on(
            ms.ref.dimension("saas.crm_accounts.account_id"),
            ms.ref.dimension("saas.billing_accounts.account_id"),
        ),
    ],
)
```

两个端点的完整主键均被关系键覆盖，所以结构基数是一对一；只连 account_id 不满足这项要求。
以下用两个字面量金额表隔离验证对应，不依赖额外 Metric 或来源金额读取：

```python
catalog = ms.load()
mapping_s = mv.session.get_or_create("scenario-account-correspondence", report_timezone="Asia/Shanghai")
crm_ref = ms.ref.entity("saas.crm_accounts")
billing_ref = ms.ref.entity("saas.billing_accounts")
crm_entity = catalog.entities.get(crm_ref)
billing_entity = catalog.entities.get(billing_ref)
crm_ids = mv.identities(crm_entity, keys=(("t1", "A"), ("t2", "A")))
billing_ids = mv.identities(billing_entity, keys=(("t1", "A"), ("t2", "A")))
crm_amounts = mapping_s.values(
    by=crm_ids.axes,
    columns={"amount": "CNY"},
    rows=(((("t1", "A"),), {"amount": 100}), ((("t2", "A"),), {"amount": 200})),
)
billing_amounts = mapping_s.values(
    by=billing_ids.axes,
    columns={"amount": "CNY"},
    rows=(((("t1", "A"),), {"amount": 80}), ((("t2", "A"),), {"amount": 250})),
)
match = mv.corresponding(via=(catalog.relationships.get(ms.ref.relationship("saas.crm_to_billing")),))
aligned = crm_amounts.align(billing_amounts, match=match)
compared = crm_amounts.compare(billing_amounts, match=match)
partial_billing = billing_amounts.where(mv.in_population(
    mv.identities(billing_entity, keys=(("t1", "A"),))
))
outer = crm_amounts.compare(partial_billing, how="outer", match=match)
aligned.execute().show()
compared.execute().show()
outer.execute().show()
```

完整配对保留两个不同的复合身份：t1/A 为 100 对 80，absolute=20、relative=1/4；
t2/A 为 200 对 250，absolute=−50、relative=−1/5。
align 使用 left/right，compare 使用 current/baseline；非恒等对应保留对应轴及两侧身份投影。
对 partial_billing 使用默认 exact 必须失败；outer 的 t2/A 行保留 current=200、baseline=NA，presence 为 True/False，差额与比例均为 NA。
不能仅因两端键文本相同而省略 match，也不能将两个 account_id 字段分类轴传给 corresponding 冒充身份轴。
via 方向为 CRM → Billing，单跳仍是非空元组；反向或不具备一对一结构的路径拒绝。
此关系的完整键可直接对应，不增加档案读取；其它需要查值的关系必须在 Query.contract 中披露实际依赖。
同信息的 pandas merge 须显式使用两个键分量并检查 one_to_one 和缺侧；只按 account_id 会制造错误配对。

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
s = mv.session.get_or_create("scenario-quality", report_timezone="Asia/Shanghai")
september = s.time_scope(start="2026-09-01", end="2026-10-01")
Inspections = ms.ref.entity("quality.inspections")
plant = catalog.fields.get(ms.ref.dimension("quality.inspections.plant"))
model = catalog.fields.get(ms.ref.dimension("quality.inspections.model"))
outcome = catalog.fields.get(ms.ref.dimension("quality.inspections.outcome"))
tested_at = catalog.fields.get(ms.ref.time_dimension("quality.inspections.tested_at"))
inspected = catalog.entities.get(Inspections).count(time=tested_at)
failed = inspected.where(outcome == "fail")
rate = failed.divide(inspected, zero_denominator="error")
plant_keys = mv.coordinates(by=(plant,), keys=(("A",), ("B",)))
model_keys = mv.coordinates(by=(model,), keys=(("Easy",), ("Hard",)))
cells = s.observe(
    {"inspected": inspected, "failed": failed, "rate": rate},
    during=september,
    by=mv.product(plant_keys, model_keys),
)
pooled = cells.select({"rate": cells["rate"]}).rollup(by=(plant,))
model_sizes = cells.select({"inspected": cells["inspected"]}).rollup(by=(model,))
reference_weights = model_sizes.share("inspected")
weighted_cells = cells.select(
    {"weighted_rate": cells["rate"] * mv.broadcast(reference_weights["inspected.share"], to=cells)}
)
standardized = weighted_cells.reduce(
    {"standardized_rate": weighted_cells["weighted_rate"].sum()}, by=(plant,)
)
readout = pooled.align(standardized).execute()
planning_weights = s.values(
    by=(model,),
    columns={"weight": "1"},
    rows=(
        (("Easy",), {"weight": Decimal("0.2")}),
        (("Hard",), {"weight": Decimal("0.8")}),
    ),
)
planning_cells = cells.select(
    {"weighted_rate": cells["rate"] * mv.broadcast(planning_weights["weight"], to=cells)}
)
planning_standardized = planning_cells.reduce(
    {"standardized_rate": planning_cells["weighted_rate"].sum()}, by=(plant,)
).execute()
```

独立权重列用 columns={"weight": "1"} 明确无量纲；Decimal(...) 只提供值，不携带单位。
预算列的 CNY 与权重列的 "1" 是不同单位声明，不能从小数值、列名或下游除法猜测。

`pooled` 合并原 rate 的分子和分母；`standardized` 对本次结构参照乘以分层率后的格子求和。
reference_weights 是 Query，每次 execute 可重新求值；需要跨执行固定 50%/50% 时，像 planning_weights 一样显式提供两项 0.5。
这两个操作有不同的统计定义，不能互换。本题明确要求每个正权重层有可定义的不良率，
因此 rate 显式选择 error；通用默认 null 不替代这项业务要求。
权重的完整域只有 model，广播沿 `(plant, model) → model` 投影，不能按行号配对。
来源结构权重位于 reference_weights 的 inspected.share 列，默认分母是当前全部型号的检验总数。
`align` 用于组合含不同量名的两张工厂表；不能用要求同名量集合的 `compare` 拼表。

### 4.3 预期结果与反例

| 工厂 | 原始不良率 | 标准化不良率 |
| --- | ---: | ---: |
| A | 120/1000 = 12% | 0.5×10% + 0.5×30% = 20% |
| B | 185/1000 = 18.5% | 0.5×5% + 0.5×20% = 12.5% |

B 在每个型号的不良率都较低，但承接的 Hard 比例更高。
原始比较是 B 高 6.5 个百分点；统一结构后是 B 低 7.5 个百分点。
这些是指定样本和参照下的描述性结论，不证明工厂本身造成了质量差异。
20%/80% 权重下，A 为 `0.2×10%+0.8×30%=26%`，B 为 `0.2×5%+0.8×20%=17%`。
这些权重非负且总和为 1；该事实由本题输入及独立 oracle 核对，Session.values 不自动归一化或猜测权重含义。

对抗输入：

- 以字面量 values 明确固定 50%/50% 参照，但把 B/Hard 的实际样本改成 0：本例严格 rate 因 0/0 使观察失败，不生成可跳过的格子。
  若另选 null 政策则得到普通 NA，后续 sum 会忽略；要保持固定标准化口径须先保证正权重层非缺失。
- 显式目标域漏掉 Hard 时，实际 Hard 贡献无法进入输出域，应该报覆盖错误，不应计算只含 Easy 的“总体”。
- 新增 model 为源 Null 的检验：默认形成 None 分类；显式域需要包含该键，或先明确过滤此类成员。
  关系映射缺失不是源 Null，不能并入同一个分组蒙混过去。
- 不同工厂都用各自的产量比例加权，只会还原各自原始率，未做到固定标准结构。
- 临时参照漏 Hard 时 broadcast 覆盖失败；重复 Easy、类型或单位冲突在 values 构造时拒绝。
  正权重 Hard 缺样本必须报错或明确改写口径，不能补零后仍称为原标准化率。

**评价**：ratio 上卷与当前格子 reduce 的区分很有价值，固定参照可以完整表达直接标准化。
普通 `weighted_mean` 会按自己的有效样本政策处理输入；本题依赖原 rate 显式 error 的除零检查及正权重层完整输入；乘权再 sum 本身不会检测已经产生的普通 NA。
v0.12 的 values 使不等权重可直接按完整型号键输入，复用 broadcast 和 reduce，
不必为了这次分析注册新的业务来源；来源派生权重的原路径仍然适用。

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
s = mv.session.get_or_create("scenario-logistics", report_timezone="Asia/Shanghai")
Shipments = ms.ref.entity("logistics.shipments")
hub = catalog.fields.get(ms.ref.dimension("logistics.shipments.hub"))
dispatched_at = catalog.fields.get(ms.ref.time_dimension("logistics.shipments.dispatched_at"))
duration = catalog.fields.get(ms.ref.measure("logistics.shipments.delivery_hours"))
window = s.time_scope(start="2026-10-01", end="2026-10-04")
days = window.by(mv.grain("day"))
hubs = mv.coordinates(by=(hub,), keys=(("H1",), ("H2",)))
p95 = duration.quantile(0.95, time=dispatched_at)
daily_query = s.observe(
    {
        "shipments": catalog.entities.get(Shipments).count(time=dispatched_at),
        "timed": duration.count(time=dispatched_at),
        "mean_hours": duration.mean(time=dispatched_at),
        "p95_hours": p95,
    },
    during=days,
    by=mv.product(hubs, days),
)
daily = daily_query.execute()
logistics_export = daily.to_pandas()
known = daily_query.where(~daily_query["p95_hours"].is_null())
worst_days = known.top(2, by=known["p95_hours"]).execute()
worst_by_hub = known.top(1, by=known["p95_hours"], partition_by=(hub,)).execute()
by_hub = s.observe({"p95_hours": p95}, during=window, by=hubs).execute()
network = s.observe({"p95_hours": p95}, during=window).execute()
```

网格行数、有效值格数及缺时长比例是终端质量读数，无需扩大 DSL 规约方法：

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

Artifact 不保存精确分位数所需的内部时长样本，因此枢纽与全网络 P95 不从 daily 上卷，
而是用同一 Metric 在目标粒度上重新 observe，直接对窗口内全部有效时长求值；不能退化为把日 P95 当原始样本。
`worst_days.show()` 按 P95 从高到低展示；同分按完整键升序，不附加排名列或顺序轴。
`worst_by_hub.show()` 先按枢纽键排列组，再按组内评分展示；冷恢复保持同一展示次序。

### 5.3 预期结果与反例

| hub / day | shipments | timed | P95 |
| --- | ---: | ---: | --- |
| H1 / 10-01 | 4 | 4 | 85.15 |
| H1 / 10-02 | 2 | 2 | 2 |
| H1 / 10-03 | 0 | 0 | Null，空样本 |
| H2 / 10-01 | 2 | 2 | 3 |
| H2 / 10-02 | 1 | 0 | Null，无有效时长 |
| H2 / 10-03 | 0 | 0 | Null，空样本 |

- 网格固定为 6 格，P95 非 NA 的格子为 3；缺时长为 1 件，占 `1/9`。
- Top-2 按评分展示 H1/10-01（85.15）、H2/10-01（3），不能把 Null 默默排到后面。
- 每枢纽 Top-1 也恰好选中这两格，但这不是全局 Top-2 的一般等价变换；分区反例见 §9。
- H1 全窗口 P95 为 75.5；全网络 P95 为 66.05，均使用位置 `(n−1)×0.95` 连续插值。
- 三个有值的日格子 P95 简单平均为 30.05；按有效件数加权也只有 43.825；两者都不是 66.05。
- 原时长的全网络均值为 `113/8=14.125`，日均值简单平均不具备同样含义。
- 空日与“有包裹但全缺时长”均可产生 Null P95，必须结合 shipments/timed 判断，不能只读一个统计值。

仅保存每日最终 P95 时，上卷应拒绝：不同原始分布可以有相同的每日 P95，却有不同总体 P95。
P95 无有效值为 NA；非法时长、分类错误和实际日历/映射/请求域覆盖检查失败仍须报错，不能转成普通 NA。
滚动 7 日 P95 的直接求值见 §7.5；每票 SLA 截止时间、未完成件的删失校正仍需独立契约。

**评价**：完整域和 nullable 与检查政策能防止静默删格；P95 的粗粒度答案来自目标粒度的新 observe，
不要求中间摘要；Query 上卷需要合格充分输入路线，Artifact 无 rollup。
不方便的地方在于：调用方必须知道完成分位数不足以恢复总体分位数，以及先排除 NA 再排序的步骤。
当前只能评估这些步骤的认知负担，尚无真实 agent 完成时间或错误率数据。

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
s = mv.session.get_or_create("scenario-experiment", report_timezone="Asia/Shanghai")
september = s.time_scope(start="2026-09-01", end="2026-10-01")
Workspaces = ms.ref.entity("experiment.workspaces")
Sessions = ms.ref.entity("experiment.sessions")
arm = catalog.fields.get(ms.ref.dimension("experiment.workspaces.arm"))
experiment_id = catalog.fields.get(ms.ref.dimension("experiment.workspaces.experiment_id"))
preperiod_sessions = catalog.fields.get(ms.ref.dimension("experiment.workspaces.preperiod_sessions"))
cohort = s.members(Workspaces, where=experiment_id == "E1")
revenue = catalog.fields.get(ms.ref.measure('experiment.purchases.purchase_amount')).sum(time=catalog.fields.get(ms.ref.time_dimension('experiment.purchases.purchased_at')))
sessions = catalog.entities.get(Sessions).count(time=catalog.fields.get(ms.ref.time_dimension('experiment.sessions.started_at')))
units_query = s.observe(
    {"revenue": revenue, "sessions": sessions},
    members=cohort,
    during=september,
    by=(arm, preperiod_sessions, mv.member()),
)
units = units_query.execute()
a_units = units_query.where(mv.in_population(mv.coordinates(by=(arm,), keys=(("A",),))))
b_units = units_query.where(mv.in_population(mv.coordinates(by=(arm,), keys=(("B",),))))
a_mean = a_units.reduce({"mean_workspace_revenue": a_units["revenue"].mean()})
b_mean = b_units.reduce({"mean_workspace_revenue": b_units["revenue"].mean()})
descriptive_lift = b_mean.compare(a_mean).execute()
experiment_export = units.to_pandas()
```

Marivo 在随机化单位上聚合两个事实根，保留零活动成员；两个等权 Query 可在 execute 前 compare。
units 与 descriptive_lift 各自执行；cohort 是来源程序，重新执行可重选。若要固定已见单位/数值，使用字面量键或 pandas。
分位数、描述汇总及预设分层后的普通报表交 pandas。字段轴按保存元数据定位：
本例按 by 中的顺序恰有 arm、preperiod_sessions 两个 field 轴，随后是一个 member 轴；不按 label 匹配 Ref。

<!-- pandas-example:experiment-readout -->
```python
import pandas as pd

axes = experiment_export.attrs["marivo"]["axes"]
(arm_column, activity_column) = [
    axis["column"] for axis in axes if axis["kind"] == "field"
]
if experiment_export["value"][["revenue", "sessions"]].isna().any().any():
    raise ValueError("The experiment summary requires non-missing unit-level outcomes.")
units_frame = experiment_export["value"][["revenue", "sessions"]].copy()
units_frame["arm"] = experiment_export[("coordinate", arm_column)]
activity = experiment_export[("coordinate", activity_column)]
if activity.isna().any():
    raise ValueError("This example requires a known preperiod activity class.")
units_frame["baseline_activity"] = pd.Series(
    "inactive", index=units_frame.index, dtype="string"
).mask(activity > 0, "active")
group_stats = units_frame.groupby("arm", dropna=False).agg(
    workspaces=("revenue", "size"),
    revenue=("revenue", "sum"),
    sessions=("sessions", "sum"),
    mean_workspace_revenue=("revenue", "mean"),
    p90_workspace_revenue=("revenue", lambda values: values.quantile(0.9)),
)
group_stats["revenue_per_session"] = group_stats["revenue"] / group_stats["sessions"]
stratum_readout = units_frame.groupby(["arm", "baseline_activity"], dropna=False).agg(
    workspaces=("revenue", "size"), mean_workspace_revenue=("revenue", "mean")
)
```

两种路径都以 workspace 为单位，不能把会话当成独立样本或只保留活跃购买者。
外部分类/统计是明确的最终读数，不能自动进入 Metric 或取得 Artifact 合同。

### 6.3 预期结果与反例

| 指标 | A | B |
| --- | ---: | ---: |
| 入组 workspace 数 | 3 | 3 |
| 总收入 | 100 | 100 |
| 每 workspace 平均收入 | 100/3 | 100/3 |
| 总会话数 | 101 | 4 |
| 每会话收入 | 100/101 | 25 |
| workspace 收入 P90 | 80 | 68 |

workspace 等权收入的绝对提升为 0，相对提升为 0。
每会话收入的比值却是 25.25，即相对提升 2425%；它回答的是另一个分母定义下的问题，
不能据此断言每个随机化单位的收入得到提升。
如果按会话次数对 workspace 收入加权，A 会得到 `10000/101`，B 得到 50，
同样不是所请求的 workspace 等权均值。
预设 active 层 A/B 各一个 workspace，均值 100/80；inactive 层各两个，均值 0/10。
按各组原始人数合回仍各为 100/3；不能将层均值等权平均，或把这一描述性差异直接称为显著交互效果。

后续“给出实验效果的置信区间、p 值，以及 cluster bootstrap 稳健性”不能用本版 DSL 闭合，使用外部统计工具是正常路径：

- 原始 workspace 结果已可交接；样本方差、分位数和推断交 pandas/专用统计库，不为能在 DSL 中拼出算式而增加结果方法。
- 当前没有检验方法、自由度/分布、区间语义、多重比较、停止规则或重抽样出现身份的完整公开 DSL。
  pandas quantile 是当前样本的分位数，不能自动变成置信区间。
- Population 按完整坐标去重；若坐标只有 workspace 身份，重复抽中的同一 workspace 会合并并改变 bootstrap。
  需要 [模型方法](algebra-methods.md) 中的抽取位置和原贡献的组合语义，不能拼一个伪 API 冒充已经支持。
- `to_pandas()` 后交给外部统计工具属于明确退出 typed DSL；外部结果不能自动恢复成拥有原分析合同的 Artifact。

**评价**：描述性实验分析可以正确表达，且能保留零活动单位；完整实验决策工作流尚不完备。
这属于 v0.12 明确排除的模型范围，不应作为“现有实现产生错误 p 值”的缺陷报告。

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
from datetime import timedelta

s = mv.session.get_or_create("scenario-temporal-sales", report_timezone="Asia/Shanghai")
income = catalog.metrics.get(ms.ref.metric("sales.revenue"))
order_count = catalog.metrics.get(ms.ref.metric("sales.order_count"))
aov = catalog.metrics.get(ms.ref.metric("sales.average_order_value"))
display = s.time_scope(start="2026-10-07", end="2026-10-09")
days = display.by(mv.grain("day"))
seven = mv.trailing_periods(count=7)

rolling_query = s.observe(
    {"income": income.window(seven), "orders": order_count.window(seven), "aov": aov.window(seven)},
    during=days,
)
rolling = rolling_query.execute()
elapsed = s.observe(
    {"income": income.window(mv.trailing_duration(duration=timedelta(hours=168)))},
    during=days,
).execute()
mtd = s.observe(
    {"income": income.window(mv.to_date(grain=mv.grain("month")))},
    during=days,
)
ytd = s.observe(
    {"income": income.window(mv.to_date(grain=mv.grain("year")))},
    during=days,
)
since_january = s.observe(
    {"income": income.window(mv.since(start=s.instant("2026-01-01")))},
    during=days,
)
joined = mtd.align(ytd)
cumulative = joined.select({"mtd": joined["left.income"], "ytd": joined["right.income"]}).execute()
rolling.show()
cumulative.show()
```

每种窗口分别 observe，默认信任声明的数据源，无需额外完整性调用。
MTD/YTD 共用同一真实输出日历，以默认 exact 组合端点；Metric 不保存观察期间默认。
v0.37 用 trailing_duration 在原日输出端点评价固定168小时；它与日历七日、小时输出是独立选择。
since_january独立保留起点，跨年不随YTD重置。

| 当前日 | 七日收入 | 七日订单数 | 七日客单价 | MTD | YTD / since_january |
| --- | ---: | ---: | --- | ---: | ---: |
| 10-07 | 1220 | 104 | 305/26 | 1220 | 1920 |
| 10-08 | 1200 | 104 | 150/13 | 1300 | 2000 |

七日包含当前完整日；10-07范围[10-01,10-08)，不按已出现的七行取值。
显示10-01也须读取9月历史，收入150，不是100。
无主体观察从各完整窗口贡献求值；明确只含十月订单的P会缩小贡献，不能自动扩大。
直接求值无需中间摘要；显示起点不是累计起点，七自然日不一般等于168小时。

### 7.3 同比环比：先计算两个端点，再按期间对应

“上月同进度”明确定义为所属月份的同一个日历日序号；不是上一个显示格子，也不按月份长度缩放。
v0.25 不提供父期间内日序配对，调用方显式构造同日号的基期日轴，再用 ordinal 按位置对应：

```python
prior_days = s.time_scope(start="2026-09-07", end="2026-09-09").by(mv.grain("day"))
current_mtd = s.observe({"income": income.window(mv.to_date(grain=mv.grain("month")))}, during=days)
prior_mtd = s.observe({"income": income.window(mv.to_date(grain=mv.grain("month")))}, during=prior_days)
mom = current_mtd.compare(prior_mtd, match=mv.ordinal(), time_axis="current").execute()
last_year_days = s.time_scope(start="2025-10-07", end="2025-10-09").by(
    mv.grain("day")
)
prior_rolling = s.observe({"income": income.window(mv.trailing_periods(count=7))}, during=last_year_days)
yoy = (
    rolling_query.select({"income": rolling_query["income"]})
    .compare(prior_rolling, match=mv.previous_year(), time_axis="current")
    .execute()
)
mom.show()
yoy.show()
```

| 当前日 | MTD 本期 / 上月 | 环比 absolute / relative | 七日本期 / 上年 | 同比 absolute / relative |
| --- | --- | --- | --- | --- |
| 10-07 | 1220 / 610 | 610 / 1 | 1220 / 305 | 915 / 3 |
| 10-08 | 1300 / 650 | 650 / 1 | 1200 / 300 | 900 / 3 |

relative 的 1 和 3 分别表示 100% 和 300%，公式仍是 `(current-baseline)/abs(baseline)`。
输出真实轴为当前日，基期日期、实际边界、presence 和坐标投影随数据保存；双方窗口定义仅在 logic；
默认 paired 仍可用于保留完整双方坐标。这里没有把同比变成每日增长率之和，
也没有把 2026 年每个贡献日映到 2025 年后才聚合。
ordinal 只按两条已构造日轴的位置配对，日号相同由 prior_days 的构造保证；两轴长度不同时 exact 拒绝。
对完整月收入做环比，可在两个完整月轴分别观察后用 `match=mv.period_offset(by=-1)`；
本例十月只覆盖到 10-09，不能把它冒充完整十月收入。

### 7.4 终端窗口产物与日格子统计

```python
history = s.time_scope(start="2026-09-25", end="2026-10-09")
history_days = history.by(mv.grain("day"))
daily = s.observe(
    {"income": income, "orders": order_count, "aov": aov}, during=history_days
).execute()
history_export = daily.to_pandas()
```

daily 是终端日结果，不提供任何 DSL 操作；七日原指标只来自 §7.2 在 `during=seven` 上的新 observe。

日格子平均使用[固定窗口示例](lazy-analysis-dsl-examples.md#214-终端-artifact-与-pandas-日格子统计) 的相同外部算法：接受本例默认除法产生的空日 NA、
来源读取与必要计算检查由 execute 负责；此处另验证完整上海日历，再统计两个七日窗口，日期齐全不证明来源业务完整。
转换为浮点是这段外部统计的明确选择，不是导出行为。

<!-- pandas-example:temporal-daily-aov -->
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

mean_daily_aov 得 `1810/33`、`1700/33`，它把每个有定义日作为一个等权样本。
原七日客单价得 `305/26`、`150/13`，它保留各天订单数的差异。
Null 忽略是这段外部程序的明确政策，不补缺日，也不把 NA 的可忽略性当作来源完整性证明；默认来源信任不生成完整性检查证据。

daily 不持有内部组件或图；需要原指标粗化时在 Query 上组合后执行。
仅每日最终 AOV 不能恢复合并后的 AOV；显式输出分子/分母后可由 pandas 定义相应计算。
Metric Ref、SQL 或 lineage 不构成可消费的输入，Artifact 不进入 DSL 或隐式回源；换窗口、换粒度或需要更早历史时重新 observe。

### 7.5 七日 P95：直接样本与固定充分信息

复用 §5 的九件包裹；观察范围为 `[2026-09-25,2026-10-04)`，本例数据集的 9 月各日确实无包裹。
这里直接按 Metric 的声明根读取窗口贡献，没有 Shipment 主体筛选；不能先按显示期选择包裹再声称计算完整历史窗口。

```python
catalog = ms.load()
logistics_s = mv.session.get_or_create("scenario-temporal-p95", report_timezone="Asia/Shanghai")
hub = catalog.fields.get(ms.ref.dimension("logistics.shipments.hub"))
dispatched_at = catalog.fields.get(ms.ref.time_dimension("logistics.shipments.dispatched_at"))
duration = catalog.fields.get(ms.ref.measure("logistics.shipments.delivery_hours"))
ship_days = logistics_s.time_scope(
    start="2026-10-01",
    end="2026-10-04",
).by(mv.grain("day"))
hubs = mv.coordinates(by=(hub,), keys=(("H1",), ("H2",)))
ship_seven = mv.trailing_periods(count=7)
p95 = duration.quantile(0.95, time=dispatched_at)

direct_p95 = logistics_s.observe(
    {"p95_hours": p95.window(ship_seven)},
    during=ship_days,
    by=mv.product(hubs, ship_days),
).execute()
network_p95 = logistics_s.observe(
    {"p95_hours": p95.window(ship_seven)},
    during=ship_days,
).execute()
direct_p95.show()
network_p95.show()
```

没有完整性声明时，direct 仍正常求值：

| hub | 10-01 七日 P95 | 10-02 七日 P95 | 10-03 七日 P95 |
| --- | ---: | ---: | ---: |
| H1 | 85.15 | 75.5 | 75.5 |
| H2 | 3 | 3 | 3 |

全网络 10-03 为 66.05。10-03 无新包裹不表示七日窗口为空；H2 在 10-02 的源 Null
按该来源指标声明的 Null 忽略政策处理，但并不证明这件包裹最终送达时间可忽略。
直接求值读取窗口内实际时长样本；Artifact 不保存内部 quantile bag，换窗口须重新 observe。日 P95 加 count 仍不足够：
两组二元素样本 `[0,20]`、`[19,19]` 都有 P95=19、count=2，
分别与 `[0]` 合并后 P95 为 18、19。优化不能从相同摘要制造不同正确答案。
有足够原输入就直接重算；不存在紧凑摘要不是不允许声明这个 Metric 的理由。

首版不接收外部业务缺口声明或上游漏数通知。查询成功但记录未采集齐全时，以上结果仍按实际读入样本计算；
分析者须据来源背景判断其适用性，不能把本例九件包裹的设定当作引擎已证明的完整性。
来源读取失败、认证日历不能覆盖所需期间或请求域遗漏被消费贡献仍必须报错。

### 7.6 同一语义日历用于财务累计与业务期间对应

独立的 `sales.retail_445` 已按现有 PeriodCalendar 契约声明并认证：
日期 spine 为 `[2025-01-27,2027-01-25)` 的每个当地 civil date，
每年 52 周，月份宽度按 `(4,4,5)` 周重复四次，年起点分别为 2025-01-27、2026-01-26。
month 键为 `FY2025-P01` 等完整键，year 键为 FY2025/FY2026；保留 day 层级由系统推导。
既有语义声明的 calendar 部分为：

```python
from datetime import date

Retail445 = ms.period_calendar(
    name="retail_445",
    date=ms.ref.time_dimension("sales.retail_calendar.calendar_date"),
    boundary_timezone="Asia/Shanghai",
    coverage=(date(2025, 1, 27), date(2027, 1, 25)),
    levels={
        "month": ms.ref.dimension("sales.retail_calendar.fiscal_month"),
        "year": ms.ref.dimension("sales.retail_calendar.fiscal_year"),
    },
)
```

此片段属于已有 sales 语义模块，所有 Ref 均在已声明的 sales.retail_calendar Entity 上，date 是真正的 date 字段；
不是在 Analysis Session 中临时注册。加载与认证先完成，Analysis 只消费已认证快照。
若实际业务采用 53 周，期间键与边界必须由业务声明，不能套用本例 52 周假定。
v0.25 不提供命名期间映射，previous_year 也不为零售日历提供同比；跨年财务期间对比由调用方明确选出两个期间。
下面的 `retail.period(level, key)` 沿用既有 [PeriodCalendarEntry 导航合同](../temporal-semantics.md#catalog-navigation-and-result-types)，
返回绑定当前认证快照、精确半开边界和 Asia/Shanghai 日历时区的 TimeScope，不按 FY 键名推算日期。
未知层级/键或缺少当前认证快照按语义导航合同拒绝；Session 默认时区不重解释该 scope。

```python
catalog = ms.load()
retail = catalog.period_calendars.get(ms.ref.period_calendar("sales.retail_445"))
fiscal_days = display.by(retail.grain("day"))
fiscal_mtd = s.observe(
    {"income": income.window(mv.to_date(grain=retail.grain("month")))}, during=fiscal_days
).execute()
fiscal_current_scope = retail.period("month", "FY2026-P08")
fiscal_prior_scope = retail.period("month", "FY2025-P08")
fiscal_pair = s.observe({"income": income}, during=fiscal_current_scope).align(
    s.observe({"income": income}, during=fiscal_prior_scope)
)
fiscal_comparison = fiscal_pair.select({
    "income.absolute": fiscal_pair["left.income"] - fiscal_pair["right.income"],
}).execute()
fiscal_mtd.show()
fiscal_comparison.show()
```

FY2026-P09 为 `[2026-09-21,2026-10-26)`，因此 10-07/08 的财务月累计为 1270/1350，
比 Gregorian MTD 多了 9 月末的 50 元。
FY2026-P08 为 `[2026-08-24,2026-09-21)`，收入 650；
FY2025-P08 为 `[2025-08-25,2025-09-22)`，收入 0，且完整覆盖。
两端都是范围合计的零轴结果，默认 exact 直接配对。
上例只求 absolute=650；使用默认 compare 则同时得到 absolute=650、relative=NA。
显式 zero_denominator="error" 才因零基期使整次执行失败，不能后置丢掉 relative 绕过检查。
这不是缺基期，也不产生有限增长百分比。累计和比较共同消费认证日历边界，不按键名推算日期。

### 7.7 边界反例与信息下界

| 反例 | 精确预期 |
| --- | --- |
| 纽约七自然日 `[2026-03-05,03-12)` / `[2026-10-29,11-05)` | 实际为 167/169 小时；同端点 168 小时分别从 03-04 23:00 / 10-29 01:00 当地时刻开始 |
| 请求期间超出认证日历快照的范围 | 按实际所需期间拒绝，不把未认证的日期当作合法空桶；成功读取其它来源不补日历认证 |
| 2024-03-01 七日按日逐个映到 2023 年 | 闰日无对应，得到六日；2023-03-01 自己的七日另含 02-23，不能交换窗口与期间映射 |
| 2024-02-29 的同比 | previous_year 无对应；exact 拒绝，outer 缺基期；不夹到 02-28 |
| 完整 2024-02 月桶与 2023-02 月桶同比 | 可按同名月份对应；29/28 天不同不单独导致失败，窗口含闰日也不自动失败 |
| ordinal 的日桶对月桶，或某端只有半日实际桶 | 粒度不相同或 A(t)≠N(t) 时拒绝；outer 不能修复这些输入，需重建同粒度完整桶 |
| ordinal 两侧为同粒度完整桶但分别有 2/3 桶 | exact 拒绝；outer 保留较长端最后一个未配对桶，不补成第三个虚构桶 |
| 2026-03-31 与上月同日号 | 二月没有第 31 日，无同日号基期；显式构造的基期轴不能夹到 02-28，长度不符时 ordinal 的 exact 拒绝 |
| 2026-03-01 上期日 | period_offset(by=-1) 为 02-28；不是“当前已有行的前一行” |
| 相同日比率 0.1/0.9，分母分别为 1/9 与 9/1 | 合并比率分别为 0.82 与 0.18；单独最终比率不是充分信息 |
| 10-07 与 10-08 七日收入直接相加 | 得 2420，不是 10-01—08 的 1300；重叠窗口不能当贡献分区 |
| compare current 轴遇到 outer 的仅基期行 | 拒绝伪造当前日期；可以显式使用默认 paired 保留双方 |

本节 oracle 验证日期集合、金额、比率及分位数的信息下界，不模拟 API 的异常或 Cell 实现。
附录中的 coverage_days 只保留为历史集合包含/相交见证，不再对应首版的缺口声明、传播或恢复能力。
真实 typed error、实际日历/映射/请求域检查、断源恢复及后端执行仍属于 §9 的待验收集。

### 7.8 采样带宽 P95 与依赖指标

设备带宽与物流时长的 P95 使用不同的样本：物流直接规约每个合法时长贡献；
带宽必须先在同一采样时点按目标空间求和，再对五分钟聚合值序列求 P95。
假设两个设备的相邻五分钟采样如下，单位均为 bit/s，状态时间和间隔已由语义声明确定：

| 设备 | t0 | t0 + 5 分钟 | 设备 P95 |
| --- | ---: | ---: | ---: |
| A | 10 | 0 | 9.5 |
| B | 0 | 10 | 9.5 |
| 同时点合计 | 10 | 10 | 10 |

目标写法为 `bandwidth.sum(time=sample_at).per(sample_at).quantile(0.95)`，
设备分组由 observe 的 by 绑定。先算设备 P95 后求和得到 19，不能代替总体的 10；
只有设备完成值或失去时点的分布不足以完成这个空间上卷。
模型中命名的完整例子见[时间样本规约](../semantic/expression-model-redesign.md#32-时间角色和时间样本规约)。

依赖比率同样先组件后组合：两组 ERROR 数/总日志数为 1/1、9/99，总体应为 10/100=0.1，
两组比率的等权均值是 6/11。目标通过 / 或 divide 的同一进程内节点持有分子/分母，省略参数的新定义采用统一零分母政策，
保留来源、过滤、时间/时间样本规约和重复依赖出现；完整定义见[指标依赖](../semantic/expression-model-redesign.md#33-指标依赖与比率)。
这些反例只证明计算顺序不同；来源绑定、小型组合图和本次执行合并资格仍待真实实现验证。

### 7.9 比率贡献与加权均值状态

同一 ERROR/日志分组的分子为 [1, 9]，分母为 [1, 99]。在各组件具有同一完整不交分区的前提下，
各组对总体率的贡献为 Nᵢ/100，即 0.01、0.09，合计 0.1；各组自身的比率 1、1/11 不能直接相加。
下钻须保留组件，贡献或变化归因则须有明确方法及相应支持依据；显式角色本身不自动满足这些前提。
普通 / 与 divide 具有相同分子/分母角色和状态要求；依赖充分时按原式重算，不另分“显式比率”类型。

要在进程内按原公式合并加权均值，也不能只有完成值。若 value=[10, Null]、weight=[1, 9]，共同非 Null 配对只保留第一项，
结果为 10；若分母单独使用全部权重，会得到 1。两个组的均值为 10、30、权重为 1、9 时，
合并乘积和/权重和为 28，组均值的等权平均为 20。
value.weighted_mean(weight=weight) 从明确输入建立共同配对并 lower 为 Ibis，不要求用户重复写掩码，
也不从乘积和/权重和公式识别角色；完整规则见[配对与状态](../semantic/expression-model-redesign.md#332-weighted-mean-的配对与状态)。
来源 weighted_mean 消费同贡献域的数值 Measure；固定结果的普通 value/weight 统计导出后在 pandas 使用共同配对。Metric 内部配对使用 metric.per(samples).weighted_mean(weight=weight_metric)，内部样本可来自字段或显式 Population，不混用来源字段与指标格子。
这些数值见证不证明 Marivo 已实现显式节点、单位/许可、装饰器等价或状态恢复；相应验收见 §9。

移除 ms.linear 后，§2 的销售减退款继续用小型 Metric 算术表达，其分支时间与依赖状态仍需保留。
常量系数和嵌套公式也沿通用依赖图重算；一般组合仍在 Query 内使用完整输入或充分组件，Artifact 不续算。

### 7.10 Session 时间默认、独立起点与版本边界

继续使用 §7.2 的 s、days 和同一模拟来源；活动从 9 月 30 日中午启动，而非月初或年初：

```python
launch = s.instant("2026-09-30T12:00:00")
same_launch = s.instant("2026-09-30T04:00:00+00:00")
since_launch = s.observe({"income": income.window(mv.since(start=launch))}, during=days).execute()
october_scope = s.time_scope(start="2026-10-01", end="2026-11-01")
new_york_october = s.time_scope(
    start="2026-10-01", end="2026-11-01", timezone="America/New_York"
)
```

launch 与 same_launch 是同一绝对时刻。该起点包含 9 月 30 日的 10 元，
10 月 7/8 日累计为 1230/1310，不等于 MTD 的 1220/1300；days 的显示起点不会重置累计。
october_scope 使用上海午夜，new_york_october 使用纽约午夜，后者不修改 Session 的报告时区。
同一钟面值若作为 naive datetime 传入构造器则拒绝；日期/无偏移字符串按冻结报告时区解释。
来源字段的 naive 时间按其既有 parser/reader 合同处理，不因本次 scope 使用纽约而重解释来源值。

独立版本样本在 11 月 1 日上海零点由 Silver 换成 Gold：
at=october_scope.end 选择 Gold，即 11 月 1 日零点生效的版本，而不是十月内最后的 Silver。
InstantAnchor 及其合法消费者由 [DSL §4.1.1](lazy-analysis-dsl-redesign.md#411-精确时刻与版本选择) 拥有；
本版不提供边界左极限，十月内最后版本 Silver 不可表达，也不能用 end 减一微秒取得。附录 E 的 time.left.limit 只记录两种区间谓词的数值区别。
附录 E 只检验时区换算、区间谓词与独立算式；Session 默认解析、DST 拒绝及冷恢复仍待真实实现验收。

### 7.11 观察期间省略与关系元组

输入规则由 [DSL §4.1](lazy-analysis-dsl-redesign.md#41-session字段时间与路径) 拥有，
完整调用见 [关系路径示例](lazy-analysis-dsl-examples.md#4-关系像与有界逆像) 和
[省略期间示例](lazy-analysis-dsl-examples.md#5-省略观察期间的合法边界)。
以下只列未来实现必须区分的准入场景，不用独立数值算式冒充参数校验：

| 场景 | 预期准入或修复 |
| --- | --- |
| 显式身份域上的 member_count，无日历输出 | 省略 during 与传 None 等价，计原域身份，不重选当期活跃成员 |
| 普通来源规约及其常量组合，无期间采样/时间样本规约或日历输出 | 有无业务时间角色均可省略期间；保留来源条件、身份约束与属性版本要求，不追加时间过滤 |
| 已选订单收入、固有日期过滤或 where.within 已限定期间的普通规约 | 省略与 None 均合法，不要求重复传 during；保留原范围，不据此证明完整历史 |
| 任一依赖要求按观察期间采样或时间样本规约，包括与普通规约混合 | 省略/None 均拒绝，指出该依赖并要求显式期间；日期过滤不能替代采样/时间样本规约绑定 |
| 无时间要求指标，但 by 含日历轴或含日历轴的 Population | 省略期间仍拒绝，不从目标域猜观察上下文 |
| Field.via、image/preimage 与 corresponding 的单跳/多跳输入 | 统一为非空有序关系元组；消费者各自的端点、基数和身份要求不变 |
| 贡献根到 members 的归属路径缺失或有多条候选 | observe 构造时拒绝，列出贡献根、P 的 Entity 与候选关系；修复为在语义层声明唯一路径或分别 observe |
| 单关系引用、list、空元组、反向或断端路径 | 构造时拒绝并给出元组/合法路径修复，不执行自动包装或补路 |

### 7.12 截断月桶的原指标上卷与月内份额

复用 §7.1 的来源和 §7.2 的 s、income、order_count、aov，观察范围只含 9 月 29/30 日及 10 月 1/2 日。
日轴粗化后仍保留同一 scope；九月和十月的实际区间分别是 `[09-29,10-01)`、`[10-01,10-03)`，不能扩成完整月。

```python
coarsen_scope = s.time_scope(start="2026-09-29", end="2026-10-03")
coarsen_days = coarsen_scope.by(mv.grain("day"))
coarsen_months = coarsen_days.coarsen(mv.grain("month"))
daily_components = s.observe(
    {"income": income, "orders": order_count, "aov": aov},
    during=coarsen_days,
)
monthly_query = daily_components.rollup(by=(coarsen_months,))
shares_query = daily_components.share("income", partition_by=(coarsen_months,))
monthly_query.execute().show()
shares_query.execute().show()
```

| 输出 | 九月实际范围 | 十月实际范围 |
| --- | --- | --- |
| 原收入上卷 | 20 | 1000 |
| 原订单数上卷 | 2 | 100 |
| 原 AOV 上卷 | 10 | 10 |
| 各日 income.share | 1/2、1/2 | 1/10、9/10 |

份额仍有四个日坐标，partition_by 只改变分母分组，不折叠输出；每个实际月内份额合计为 1。
上卷从收入与订单组件重算 AOV；十月两个完成日 AOV 的等权均值为 600/11，不等于原 AOV 的 10。
两个分支都在原 daily_components 上构造，不能把含份额列的 Query 整体 rollup，也不在 Artifact 上续算。
跨月周 `[2026-09-28,2026-10-05)` 无法完整映入唯一月份，因此其周轴 coarsen 到月必须拒绝；
省略其它天的记录不改变这一日历结构。反向细化同样拒绝，coarsen 不增加读取范围或授予可加性。
同信息的 pandas 路径可按已知实际月映射汇总收入与订单，再算 AOV 和份额；不能只重采样完成的日 AOV。
独立数值验证见附录 L；真实 coarsen 绑定、充分组件路线和拒绝行为仍待 §9 验收。

## 8. 对设计的综合判断与改进顺序

### 8.1 已得到支持的设计选择

| 设计选择 | 本文提供的检验 |
| --- | --- |
| 成员身份与贡献出现分开 | 电商一单多退款；SaaS 复合账户键；实验一个 workspace 多会话 |
| 每个基础组件独立绑定贡献和时间 | 本月销售减本月退款；收入和会话跨事实根观察 |
| 公共观察条件 / 成员选择 / 组件过滤 / 结果选择分开 | 固定基期账户；只过滤失败检验贡献；在比较完成后选异常地区 |
| Metric、Column 与 pandas 各有边界 | 来源指标组合；必要的固定参照标准化；普通统计导出后完成 |
| rollup 与 reduce 分开 | 总体 AOV/不良率用组件上卷；等权客户均值/实验均值用格子统计 |
| Population 统一表示，身份约束与输出坐标角色分开 | 固定 East 字段域不等于客户身份域；同一身份域可显式作为 members 或 by |
| 参照与普通比值分开 | 电商份额和来源结构权重使用 share；NRR 与每会话收入在 pandas 按明确分母计算 |
| nullable 值与缺坐标分开 | 零业务门店、空物流日、全缺时长日、正权重层的 0/0 |
| Query 和终端 Artifact 分开 | 旧异常键经 pandas/脚本成为新字面量；精确旧基期值的后处理留在 pandas |
| 信息充分性限制后续计算 | 粗比较不能无中生有地下钻；最终 P95 不能恢复精确总体分位数 |
| 中间摘要是可选优化 | 无 compact state 时仍定义完整输入上的指标；输出信息不足时重新 observe |
| 输出日历、贡献窗口及期间对应分开 | 七日与 168 小时不同；累计起点不等于显示起点；闰年映射不能与窗口交换 |
| 实际覆盖检查与空值分开 | 成功读取后的空日按规约完成，日历/映射/请求域覆盖检查失败仍报错，Artifact 不恢复未输出输入 |

在这五个业务场景与时间分析的核心路径上，**未确认会必然导致错误结果的提案语义矛盾**。
这是有边界的设计评审结果，不是所有合法程序的完备性或优化正确性证明。
尤其没有用独立 oracle 的成功，替代 DSL 的拒绝行为、绑定正确性或物理执行证明。

### 8.2 API 表达力与 v0.28 收敛

| 场景需求 | 目标表达 | 保留边界 |
| --- | --- | --- |
| 电商净额与毛利 | 销售−退款组合；逐单金额由 Measure 授权，金额−成本可直接 sum | 多根不join原始事实；新业务量有单位/权限 |
| 销售与来源结构占比 | Query.share(name)，按需 partition_by | 默认总体是当前输入，后置筛选保留分母；不存在 Column.share 入口 |
| 粗比较后追问 | 固定异常域，各端点来源Query where/drill(add=...)，再新compare | 不重判旧阈值；细比较 how 独立选择 |
| SaaS固定账户均额 | 完整账户 observe 后 reduce；新来源计算也可 Metric.per(cohort).mean() | 显式完整域保留零活动成员；新来源计算与固定答案统计分别验证 |
| 时间窗口不同 | 每种窗口分别observe，再align/select | 没有Metric.during或最近默认继承 |
| 来源完整性 | observe 默认信任来源，按已读数据求值 | 首版不声明或消费外部业务缺口；默认信任不冒充已检查证据 |
| 行表达式 | 同贡献根上的原生 Ibis 数值表达式，Measure 声明新业务授权 | 移除自定义数值 AST；跨根逐行算术拒绝，schema 自动延迟绑定 |
| 分类与参照 | pandas 分类汇总；values/broadcast 仅保留类型化参照 | 外部分类不回灌；Query 可在执行前投影，产物完整键供 pandas 选择 |
| 配对规约 | 来源/Metric 内部 weighted_mean；普通固定格子配对交 pandas | 来源状态由 Marivo 维护，外部处理显式选择共同有效样本 |
| 模型与临时指标 | 同一计算表达式，@ms.metric 只声明稳定名称 | 命名不登记；模型收录要求独立可持久解析的依赖闭包 |
| 显式域依赖 | Metric.where(in_population(P)) 与 Metric.per(P) 后规约 | 前者筛当前分支贡献，后者逐个绑定所选完整样本；保留域选择程序、版本和原有 Session 归属 |

这些是目标API表达检查。189项独立数值见证不验证Marivo resolver、typed error、物理路线或冷恢复。
行/指标共享原生 Ibis 数值语言及受控 Deferred，阶段由语义检查区分；不保留第二套 Marivo 数值 AST。
不可变 runtime 名单可显式进入 Metric：where 只约束该分支，per 选择完整名单并逐样本求子值；
它们不接收 Artifact 或导出数值列。Query 格子统计用 reduce，已执行答案统计用 pandas；模型收录资格另行检查。

### 8.3 哪些“完备”已经检验，哪些没有

数值与设计覆盖多根规约、不同过滤作用域、固定人群、复合身份、明确时间角色、
完整网格、端点比较/细化、参照/份额、原指标上卷、格子统计、P95直接求值、状态与窗口边界。
本轮改写示例不改原始模拟数据及原 189 项数学代码；pandas 片段只验证替代路径，
附录 D 的新增集合见证只验证统一域下的身份、完整输出及信息丢失区别。
附录 G 独立检验 v0.28 的算式与样本范围，不模拟 Metric、loader 或域绑定器。

未验证真正Field谓词/Metric图绑定、来源schema解析、后端 lowering、
执行内跨 stage 依赖、终端输出/失败清理、typed repair、失败发布、断源冷恢复或真实agent效率。
结果分类/通用统计已明确交 pandas，不再留下待定公开入口；事件、模型、归因和预测仍范围外。
不能从原生Ibis能构造表达式，推导新DSL支持全API或已完成产品验收。

### 8.4 本轮收敛的验证责任与事实归属

数值政策由[语义提案§3.4](../semantic/expression-model-redesign.md#34-样本政策的归属)拥有；
来源配对与时间样本规约分别由其§3.3.2和§3.2拥有。本文只给场景输入和预期，不维护第二份默认值或节点清单。
结果统计、输入权威、谓词绑定与具体修复分别由DSL §4.4 与 [执行与披露 §2](lazy-analysis-dsl-execution.md#2-agent-读取与错误契约) 拥有；
共享逻辑节点的读取保证集中在[执行与恢复 §1.1.1](lazy-analysis-dsl-execution.md#111-共享逻辑节点与执行内输入)，各算子的同实际输入前提仍逐项保留。
版本变更与历史核验留在附录，规范正文保留当前正向规则与必要拒绝边界。

| 增量 | 最小场景输入与未来验收 | owner |
| --- | --- | --- |
| 数值默认与已有政策 | 行/Metric/Column 的 0/0、非零数/0 默认为 NA，显式 error 失败；NA/0 在两种政策下均为 NA。share/compare/weighted_mean 同规则；有效配对 value=[10,20]、weight=[1,−1] 的零权重和按政策处理，无有效配对两种政策均为 NA。Artifact 读取已输出值，不重新解释政策 | 语义§3.4；DSL§4.4；附录 K |
| 阶段错误与修复 | 误把Column送入来源构造器，或把Metric送入reduce，错误给出实收/预期及可用入口；Marivo接收前已发生的Ibis错误由静态Help说明。修复不自动observe、align或改统计政策 | [执行与披露 §2.3](lazy-analysis-dsl-execution.md#23-跨阶段错误给出有效修复) |
| Query依赖与主输入 | Query 披露来源/字面量依赖；Artifact 没有 contract，DSL 拒绝以 Artifact/ID/数据轴充当计算依赖；字面量字符串不解析为产物引用 | [执行与披露 §2.1](lazy-analysis-dsl-execution.md#21-repr-与输入权威)、DSL §5.3 |
| 域谓词绑定 | 固定West域的in_population尚未绑定时披露轴/依赖；绑定来源身份选择可读取所需属性，绑定 Population/Query 只测已有坐标，contract显示具体测试与新读 | [执行与披露 §2.2](lazy-analysis-dsl-execution.md#22-域测试的未绑定与已绑定披露) |
| 比较命名 | SaaS 从恒等比较保留的共享身份轴取得流失账户；outer缺基期时baseline_present=False、端点值为 NA。align预算参照仍使用left/right | DSL §4.6–4.7；本文 §9.1-3 |
| 小型图资格与成本 | 电商净额/带宽时间样本规约/配对规约只用准入 Ibis 数值图及语义边界；每类独立验证直接 lowering、Query rollup/执行内组件及输出数据往返。未知节点/规则版本给出位置，不以直接来源求值推导离线资格 | 语义§6.1；本文 §9.1-46 |
| 同一构造与命名 | 模型/runtime 写同一表达式，两种零参数声明都捕获函数局部名，模型收录限定业务身份；政策在算子上，派生式匿名。Measure 继承已有单位/权限，只补充新业务断言 | [语义 §2](../semantic/expression-model-redesign.md#2-同一构造装饰与命名方式)；本文 §9.1-63–64 |
| 不可变域与模型收录 | 字面量和来源/Query 程序可构造；保留选择范围与归属。仅会话逻辑闭包在收录时拒绝，Artifact 依赖在构造时即拒绝 | [执行与恢复 §1.1.2](lazy-analysis-dsl-execution.md#112-metric-显式域依赖)；本文 §9.1-65–66 |
| 分支与内部样本 | A=100、C=300、完整域 A/B：筛分子比率 1/4；per(A/B) 均值 50，全体实际出现均值 200，仅所选活跃客户均值 100；外部 by=A/B 才因未覆盖 C 拒绝 | 语义 §3.5；本文 §9.1-67；附录 G |
| 政策归一与恢复 | 政策归各节点，装饰器无政策；sum 默认空/全 NA 为 NA，显式 empty=zero 为 0，不区分二者，count 固定语义；不适用政策和无合法 lowering 拒绝，进程内不套新默认，冷存不恢复政策 | 语义 §3.4；本文 §9.1-68 |

以上是未实施的合同验收；189项数学见证保持原样，不能验证repr、typed error、默认政策编码或恢复行为。
首版最小规约的 nullable 政策由 DSL §4.4 拥有，导出布局由 [执行与交接 §3](lazy-analysis-dsl-execution.md#3-pandas-导出合同) 拥有。被移除的结果方法不再列为实现验收对象；
对应的数学反例仍有意义，不能据此声称该方法保留在DSL中。

### 8.5 与 pandas 的价值对照

完整算子准入理由见本文 §8.6；公开签名与语义仍由 [DSL 定义](lazy-analysis-dsl-redesign.md) 拥有。
本文用业务过程验证边界，不以“没有离开DSL”作为优点：

| 场景 | 必须由Marivo自动维护的事实 | 外部路径与预期 |
| --- | --- | --- |
| 客户平均 | 完整固定客户字段域、原 AOV 组件；订单身份/客户分类另行显式保存 | 同一客户导出表得到19/55/95/19、P90=91；不需要结果分位数/配对工厂 |
| SaaS | 固定复合账户、零活动坐标、两个端点及共享身份坐标 | clip和groupby得GRR50%及三类净变；执行前 Query 投影或 pandas 完整键选择得到 (t2,A) |
| 制造 | 原rate组件、固定参照覆盖/单位、显式 error 防止缺层 | 保留最小sum、broadcast、align；普通报表仍可导出，不能仅凭算式正确称独有能力 |
| 物流 | 源P95在目标粒度上的直接求值、最差日的固定选择 | 外部有效值计数得六格/三个有效 P95，缺时长1/9；不新增count_defined |
| 实验 | workspace分析单位、零活动主体、同口径均值比较 | pandas得P90 80/68和active/inactive均值；置信区间等继续由专用库完成 |
| 时间 | 原窗口贡献、认证日历范围与完整输出坐标 | 外部完整日格平均1810/33、1700/33；缺坐标须先检查，日期齐全不证明上游完整；普通 NA 可按政策忽略 |

附录C执行这些已标记pandas片段，输入是独立合成的目标布局表，未调用Marivo导出器。
真实交接还必须验收：大整数/Decimal/时区/空表、nullable 值与必要失败、outer缺侧、完整复合键、
修改副本不影响Artifact、读取或转换失败不返回部分表、冷恢复导出一致，以及外部attrs不构成回灌授权。
这些义务不由合成表的成功代替，也没有新增更快/更省内存的未验证主张。

### 8.6 逐算子的价值准入

比较基线是已取得相同信息、正确使用 pandas 的程序，不是遗漏键校验或错误平均比率的程序。
保留算子必须自动承担具体的业务事实维护、执行前组合或已验证的资源收益。
少写几行、统一语法、lazy 名称、可序列化或“pandas 结果不能回灌”均不能单独证明价值。
本提案只论证下面的语义价值；没有证明所有来源操作可下推或Query 组合比 pandas 更快。

| 算子 / 入口 | pandas 基线与用户原本需手工维护的事实 | 决定及必要用途 |
| --- | --- | --- |
| Session.observe | 取数后 groupby 可计算；指标来源、过滤、时间角色、单位和多根粒度需逐次组织 | 保留，统一应用已有业务定义 |
| 行表达式/Field/Entity 的 sum/count/count_distinct/min/max/mean/median/quantile/weighted_mean | 数学方法已有；贡献单位、配对和政策需自行携带 | 保留在语义层，定义可复用指标及其充分状态；不因此公开同名结果方法 |
| Metric.where、算术、divide | 过滤与公式可计算；派生口径及分子分母依赖需人工保存 | 保留，在本次 Query 按原公式计算，过滤不污染其它组件 |
| Metric.per 后规约，包括时间样本规约 | 嵌套 groupby/时间统计可表达；内部样本和空间/时间次序需维护 | 显式不可变 runtime 名单合法；依赖持久化在模型收录检查 |
| Session.members、member、identities | 普通索引不自带 Entity 完整身份 | 保留来源身份选择与类型化身份名单（完整主键元组，空名单仍有类型）；请求完整身份输出时保留零活动主体 |
| Session.read 与 Population.classify/image/preimage；Field 比较、布尔组合、is_null/within/via/at | 筛选/join 可实现；声明路径、属性版本及新增读取需手工约束 | 保留显式属性读取、分类与关系像/逆像；不传播原贡献过滤 |
| member_count | len/nunique 可计数但不能自动决定业务单位 | 保留，明确计本次主体，不计贡献或格子 |
| Query.population、Population.project/where | 去重、选列及集合筛选可实现；完整轴元组及读取依赖需维护 | 保留，复用 Query 逻辑坐标；只有明确的身份轴能作为身份约束 |
| Population.union/intersection/difference | Index/集合运算可实现 | 统一组合身份域和一般坐标域，检查轴/角色/版本 |
| coordinates、product、in_population | MultiIndex/笛卡尔积/isin 可实现 | 保留请求域与过滤的区别，完整网格参与 空输入/覆盖判断 |
| Session.query | DataFrame 可保存键 | 保留域到 Query 的唯一入口；execute 为仅键 Artifact，不提供反向取域 |
| Session.values | DataFrame 可建参照表 | 保留 columns 映射上的独立单位、完整键和覆盖检查；普通外部结果不回灌 |
| query[name]、coord、select | 列访问/投影已有 | 保留组合所需的最小连接能力，运输原键和实际状态；轴用原 Field/Calendar 句柄或 population().axes 定位 |
| Column 算术（含 `/` 与 divide）、LiteralValue | 逐点计算已有 | 常量按运算位置局部绑定，保留同域/单位及必要检查；divide 与来源阶段共享零分母参数和默认，不借后端原生除零结果决定语义 |
| is_null、Column 比较和布尔组合 | 原生缺失谓词与三值布尔 | 只保留 is_null；where 取 True，NA 不入选；不使用状态码 |
| Query.where、top（含 partition_by） | 筛选、排序、分组取前 N 已有 | 保留 Query 坐标选择和本次必要信息，用于身份轴投影或端点细化；无物理 LIMIT 性能承诺 |
| reduce、Column.sum | groupby.sum 已有 | 保留固定参照乘分层率后求和等组合，固定层完整性由前置检查保证；运输单位/样本/充分状态，再参与语义对齐 |
| Column.mean | groupby.mean 已有 | 保留明确样本域上的等权量及充分状态，随后做同口径 compare；普通描述均值交 pandas |
| row_count | len/groupby.size 已有 | 保留显式完整域的分母，不借有定义值数删除零活动成员 |
| align（how="exact"/"outer"，可选 match） | merge(validate=..., indicator=True) 已检查唯一性/缺侧 | 保留轴角色、版本、业务量及两侧坐标投影，不扩成通用 join |
| compare | 减法/除法已有 | 保留端点量定义、单位、主体、期间和缺侧检查；四列包装本身不是价值 |
| corresponding、broadcast | 映射/join 可实现 | 保留声明身份对应和完整参照投影，拒绝缺覆盖及错误贡献复制 |
| Query.share | 除法及组内总额可计算；总体范围、筛选顺序和贡献依据需手工维护 | 自动构造当前/分组总体，保留实际贡献包含证明 |
| rollup | 若手工导出各组件并知道原公式，pandas 能算对 | 保留自动维护原公式、可加性、支持和充分信息，拒绝合并完成均值/P95 |
| Query.drill(add=...) | 重新取细数据可实现 | 保留父级选择/口径，只在可追踪到来源观察的 Query 上细化 |
| Session.instant/time_scope、TimeScope.by、start/end | pandas 有时间/时区/期间类型 | 使用报告时区默认，保留精确时刻，不让用户猜 tick |
| CalendarAxis.coarsen | 重采样/期间映射已有 | 保留完整父子映射及原读取范围，服务合法原上卷 |
| trailing_periods、since/to_date | rolling/expanding/期间分组已有 | 保留原指标贡献窗口、认证日历和覆盖；不承诺通用序列处理 |
| ordinal/period_offset/previous_year | 位置/日期映射可实现 | 不带轴，在 align/compare 绑定时取两侧日历轴；保留完整期间和端点对应，不猜闰日/53 周 |
| get_or_create/resume/artifact、Query.execute/contract、Artifact.show/to_pandas/findings | 非统计算子 | 保留会话/固定答案/执行/交接基础，不据此为其它算子自动授权 |
| 结果 min/max/count_defined/count_distinct/quantile | 普通描述统计及有效值计数可直接完成，当前无必要语义续算案例 | 移出首版；来源指标同名方法不受影响 |
| record、结果 weighted_mean/correlation | pandas 可对同一有效配对求统计，现有示例只读取最终数字 | 移出首版，不保留 RecordColumn；来源/Metric 内部配对仍由 ms 拥有 |
| case、minimum/maximum、with_axes、axis(name) | 条件赋值、clip、分类分组已有；当前 GRR/分类汇总为终端处理 | 移出首版；主体选择用已有 where，普通标签留在 pandas |
| window_reduce | 已固定日格子的 rolling 统计已有 | 移出首版；用明确有效样本及完整日期检查后交 pandas，不影响 observe 的原指标窗口 |

v0.25 另将以下边缘能力移出首版。表中每项都有替代路径，场景已改写；这些条目只记录取舍，不是兼容入口：

| 移出的能力 | 替代路径 |
| --- | --- |
| execute(retain=...)、retain_input、retain_detail、Artifact.drill、固定离线细比较 | Artifact 无任何 DSL 方法；细项在端点来源 Query 上 drill(add=...) 后再 compare，或在原 observe 的 by 中直接请求细轴/身份轴 |
| Query.window | 用 `base.window(rule)` 构造新 Metric，再以 `during=scope/axis` observe（§7.2、§7.5） |
| share(of=...) | 对筛选前总体先 share 再 where/top（§2.4）；预算达成等普通比值用 `/` |
| rollup(by=Population)、drill 的 Population 形态 | 在原 observe 上以 by=Population 请求完整域 |
| observe(routes=...)、ContributionRole | 贡献归属路径缺失或歧义为结构化错误，修复是在语义层声明唯一路径或分别 observe（§7.11） |
| BeforeEndAnchor、scope.before_end、TimeAnchor | 只用 InstantAnchor；at=end 选择边界时刻生效的版本，边界换版时期间内最后旧版本不可表达（§7.10） |
| trailing_periods(include_current=...) | 不含当期的回看不提供；trailing_duration 已在 v0.37 纳入，输出粒度不决定固定持续时长 |
| calendar_correspondence、within_period | 同日号基期由调用方构造基期日轴后用 ordinal（§7.3）；财务期间对比显式选出两个期间（§7.6） |
| Column.sum/mean(nulls=, empty=) | 固定政策：sum/mean 忽略 NA，sum/mean 无有效值为 NA；不再提供 empty。Column.divide 已纳入统一除法合同，不属于移除项 |

最小 reduce 闭合集合为 sum、mean、row_count。新方法必须给出相同信息的 pandas 路径、
Marivo 自动承担的事实和必要后续操作；否则不进入 API、Help、进程内图或实施计划。
现有数值反例仍能说明数学问题，但不自动证明应在 DSL 增加一个同名算子。
一般分析可直接交接，不必先经过 reduce、align 或重新保存一份 Artifact。

pandas 基线参见官方 [分组](https://pandas.pydata.org/docs/user_guide/groupby.html)、
[连接](https://pandas.pydata.org/docs/user_guide/merging.html)、
[窗口](https://pandas.pydata.org/docs/user_guide/window.html) 和
[缺失值](https://pandas.pydata.org/docs/user_guide/missing_data.html) 文档。

### 8.7 构造披露、执行后阅读与重开

本例复用 sales 的声明，只检查各阅读入口的责任，不假定当前来源一定含有某个订单。
定义和 contract 阶段不读业务或 schema；属性读取的实际例子见 §3.4，份额分母的上下文见 §7.12。

```python
catalog = ms.load()
disclosure_s = mv.session.get_or_create("scenario-disclosure", report_timezone="Asia/Shanghai")
revenue = catalog.metrics.get(ms.ref.metric("sales.revenue"))
paid_at = catalog.fields.get(ms.ref.time_dimension("sales.orders.paid_at"))
region = catalog.fields.get(ms.ref.dimension("sales.orders.region"))
scope = disclosure_s.time_scope(start="2026-09-01", end="2026-10-01")
literal_population = mv.identities(catalog.entities.get(ms.ref.entity("sales.orders")), keys=(("A09",), ("B11",)))
source_population = disclosure_s.members(ms.ref.entity("sales.orders"), where=paid_at.within(scope))
query = disclosure_s.observe({"revenue": revenue}, during=scope, by=(region,))
query_population = query.population()
for population in (literal_population, source_population, query_population):
    population.contract().show()
query.contract().show()
artifact = query.execute()
artifact.show(n=5)
page = artifact.findings(limit=20, cursor=None)
page.show()
assert page.items == () and page.next_cursor is None
artifact.evidence_digest().show()
reopened = mv.session.resume(disclosure_s.id).artifact(artifact.id)
reopened.show(n=5)
reopened_page = reopened.findings(limit=20, cursor=None)
reopened_page.show()
assert reopened_page.items == () and reopened_page.next_cursor is None
reopened.evidence_digest().show()
```

Query.contract 同时披露收入定义、时间角色、节点政策及待绑定要求，命名 Deferred 不另有 contract；三个 Population.contract 分别披露字面量身份、
来源选择及其期间、Query 字段坐标依赖。未知来源键集保持未求值，不能靠 contract 完成 schema 或业务检查。
Query.contract 披露本次期间、分组、来源依赖和必要检查，不把逻辑合法写成后端已验收。
普通 observe 不产生方法 Finding，所以两次 findings 都是有界空页、没有后续 cursor；不为展示分页捏造发现。
Evidence 摘要只描述本次实际读取范围和实际执行的检查，不认证业务完整性或生成业务/因果判断。
新产物和重开产物读取同一输出记录；真实实现还须在来源、Catalog 和图解码器不可用时验证这些读取。
Artifact 没有 contract，不能从 findings/evidence_digest 恢复 DSL 权限；这些行为没有 pandas 统计算法替代问题。

## 9. 后续实现的最小验收集

每个场景一个正向输入与主要反例核；复用同一owner，避免每项重复全后端/全生命周期。
表中标明Marivo与pandas各自负责的步骤；Marivo部分均为未来实现要求，不是数值脚本的通过项。

| 场景 | 独立数值预期 | 必要边界 |
| --- | --- | --- |
| 电商 | 净320、East变化−110、毛利80、预算16/15与3/4 | 毛利先声明；两端细化后compare；新增channel缺侧不补零 |
| SaaS | NRR62.5%、GRR50%、三类净变50/−100/−100、流失(t2,A) | Query 绑定/比较/投影身份；pandas算GRR与分类，外部标签不回灌 |
| 制造 | 原率12%/18.5%，50/50标准率20%/12.5%，20/80为26%/17% | 组件上卷与格子加权分开；缺正权重层拒绝 |
| 物流 | 六格九件八有效、总体P95=66.05、各hub最差10-01 | 目标粒度直接求值，Null显式排序选择，不平均日P95 |
| 实验 | 均值100/3，active100/80、inactive0/10，P90为80/68 | Marivo完整workspace与均值比较；pandas分层/描述，分位数不授予推断 |
| 时间 | 七日1220/1200、MTD1220/1300、YTD1920/2000、环比1/同比3 | 分别绑定窗口/前提，不删缺日/夹闰日/补历史 |

保留算子的价值门槛由 §8.6 拥有：每项验收先给出相同信息的 pandas 路径，
再观察实际维护的业务事实或执行前组合；不能仅验证数值相等，也不将尚未测试的下推/性能当收益。
以下是真实实现的行为验收要求，不是本轮文档检查或数值 oracle 的通过声明。
每个行为有明确输入、nullable 值、坐标和拒绝边界；检查不引入跨读一致性。
构造/repr/Population.contract/Query.contract 无业务或 schema I/O；新/重开 Artifact 的阅读接口与数据一致，无图或 DSL 续算；
未知执行路线明确拒绝，不偷偷换算法或发布部分Artifact。

补充代码复用下表的既有验收条目，不另增一套测试矩阵：

| 完整代码位置 | §9.1 条目 / 其它验收 | 场景接通与复用阶段 |
| --- | --- | --- |
| §3.4 Session.read 与 Field.at | 2、13、14 | R3 |
| §3.5 声明身份对应 | 3、20、59 | R5 |
| §7.12 日历粗化、原上卷和月内份额 | 9、12、56、60 | R6；R7 复用时间边界 |
| §8.7 contract 与产物阅读 | 14、24、30；§9.2 的 Finding/Evidence 阅读 | R2、R3、R4，随各算子扩展 |

本表说明组合场景何时接通，不重分配单条验收的 owner；各条主归属仍以实施计划 §5.2 为准。

### 9.1 最小反例与修复判据

1. **域组合。** 完整地区域×30日为60键；相关字段元组不拆开求积；空域投影仍空。
2. **时间角色。** 创建/支付时间是不同定义；during只绑定范围；不提供边界左极限，不能用 end 减 tick 冒充。
3. **比较坐标投影。** compare 全链使用 current/baseline，包括坐标投影、presence 与错误；
   align 保持 left/right。只投影该侧实际坐标，outer 缺侧先筛 presence，不隐含另一份成员关系。
   两侧可采用不同 Metric 或来源/字面量 Query；量名非空且相同、类型/单位与坐标约束满足时可比较，
   不额外要求相同定义。量名或单位不相容仍拒绝，数值成功不授予业务可比性。
4. **分组充分性。** 相同总额190不能恢复East100/West90与East50/West140的不同细状态。
5. **字段与身份。** 两订单同 customer_id=A，字段像只有 A，身份域仍有两个订单；
   两者都是 Population，普通字段不能作为 Entity 身份，显式 O404 引用与来源命中也不同。
6. **终端边界。** 两组细组件(100,1)/(90,9)和(50,5)/(140,5)都粗190/10；Artifact 无 DSL 方法，细项从观察 Query drill(add=...) 后执行。
7. **字段集合。** (T1,A)/(T2,B)按完整元组并交差，不生成交叉身份或主体。
8. **各统计独立样本。** x=[10,Null]、y=[Null,20]的来源规约各自按声明忽略Null，不能先交集删行；配对才整对处理。
   结果 Column.sum/mean 各自忽略 NA；空/全 NA 的 sum/mean 均为 NA。
9. **时间粗化。** 日→月→年保留原scope与实际区间，跨月周不能隐式归月或扩大范围。
   §7.12 的截断月桶收入20/1000、订单2/100、原AOV均10；月内日份额1/2、1/2、1/10、9/10仍保留四个日坐标。
10. **域计数。** 固定 A/B，订单 [1,0]、默认除法的 AOV [50,NA]，query(population).reduce(row_count)=2；
    值 mean 为 50，row_count 为 2；默认除法产生 NA；显式 error 的除法先失败，不能通过只取计数绕过原问题的检查。
    in_population未绑定时显示轴/身份/依赖，绑定后披露member_identity或result_coordinate及实际新读。
11. **共享节点。** 两次来源域可以为 A 与 A/B，同次 Run 也不自动固定内容。显式字面量键保持指定名单；
    重新 execute Query 不读取上次 Artifact。repr 披露来源/字面量依赖，不能出现固定产物输入。
12. **过滤份额。** 同实际paid100/unpaid50，先 share 后筛 paid 为2/3，先筛 paid 再自动 share 为1；
    share 不接受外部参照分母。后置 where/top 不重算分母，优化不得越界改变范围。
    独立读取、同定义或同observe不补共同支持证明，条件不污染其它分支。
    零轴和 partition_by 的分母都检查可加性及分子贡献包含；AOV/revenue+10 不能因可上卷取得份额资格。
    合计为1还要求完整不交分区，有符号贡献不承诺份额位于[0,1]。
13. **固定身份与属性。** 字面量 O1/O2 不纳新增 O3；read 在 Query 中声明新属性读取。
    旧产物的属性筛选只在 pandas 进行；Population.where 不隐式读取新属性。
    §3.4 的固定 C1/C2 在 at=end 读取 Gold/Bronze，不纳入 C3；缺少必需版本/属性映射时失败，不补 NA 或重选名单。
14. **身份名单。** 来源O1/O2与名单O2/O3得O2，不产生O3；复合键、跨Entity、空名单精确检查。
    Population.contract 对字面量/来源/Query 域均披露有序轴/角色、已有归属、选择期间及依赖；
    禁止业务与 schema 读取仍可调用，未知键集不枚举，待检查条件不能显示为已经验证。
15. **无列计数与父域。** 同row_count请求分别绑定两行/三行结果；显式空父纤维按原算子空输入规则完成。
16. **精确去重。** 日计数1/1无法区分A/A与A/B；Query 精确合并需本次实际集合或充分输入，Artifact 不保存内部集合。
17. **域过滤。** West域筛(East,A)/(West,B)/(West,C)保留完整West两键，不重读字段或改变粒度。
18. **端点细化。** 父增量50、细增量20/30；Query 先选父域再细化端点，不对细量重复父阈值；
    compare.drill 拒绝，不复制或均摊父变化率。
19. **多轮选择。** 旧 West90 被选后，脚本从输出显式构造 West 键，新来源 West70/East130 时仍追问 West；
    若改用原逻辑筛选 Query 则可以重新选中 East。两条路径不承诺共同快照或对账。
20. **细配对独立。** 父exact相容但细A/B与A不相等；细比较 how="exact" 拒绝，how="outer" 保留缺侧而非补零。
21. **选父后细化。** 只选West时新增細字段只检查West纤维；原父错误保留。各端点分别执行，
    原父月ordinal不能自动授权细日对应；需要新的显式映射。
22. **Top-N。** A/B/C评分90/90/50，top1规范键选择A；n=0仍保留score必要检查。
    A/B/C评分10/30/30时，top3默认展示B/C/A，show(n=1)只展示B；descending=False展示A/B/C。
23. **Null分类。** East70/None30的总额100，East份额0.7；None键不同于量Null或缺映射。
24. **重开。** 新产物和重开产物都只读输出数据/事实；没有 select/rollup/share/population/contract。
    粗比较 ID 不重建来源，细项从新 Query 构造；没有数据叶子适配或结果回灌。
25. **Nullable 交接。** 有效 0 与 NA 以原 dtype 导出，坐标/presence 独立；没有 state/reason 列。
    默认零分母产生可导出的普通 NA；显式 error 的零分母和实际覆盖检查失败阻止 execute 提交，不形成失败格子。
26. **来源业务量授权。** 逐单数量/单价的业务金额先Measure authoring再聚合；
    订单毛利80不是数量和×单位毛利和150；正毛利反例60不是40。
27. **分类外置。** 账户100→150、200→100、100→0在pandas分类后净变50/−100/−100；
    从导出完整键选出流失账户，或在 Query 执行前投影；标签/attrs 不授予 DSL 权限。
28. **参照表。** Easy/Hard权重0.2/0.8得标准率0.26/0.17，重复键、缺列、bool/int或单位混用拒绝。
29. **分组Top-N。** H1/A90、H1/B90、H2/A80、H2/B70，全局top2与每组top1不同，输入乱序不改结果。
    H1/A10、H1/B30、H2/A90、H2/B20时，每组top2按H1/B、H1/A、H2/A、H2/B展示：组按键序，组内按评分，同分按完整键。
30. **交接与重开。** values/Top-N 产物断源可读；to_pandas 保留完整键/精度/NA/presence 与产物 ID。
    修改值/对象/attrs 不改变原 Artifact；Query 内 where/select/share 保序，Artifact 重开保留最后输出次序，
    没有执行后数据算子；pandas 派生值不回灌。
31. **直接窗口。** 无 merge 但有完整输入路线仍可求 P95=66.05；Artifact 不保存 quantile bag 或提供 rollup，不否定 Metric 定义。
32. **日历窗口。** 上海10-07七日[10-01,10-08)，含当前完整日；不含当期的回看不提供。
    纽约167/169小时与168不同，168小时回看用 trailing_duration，保持所请求输出端点；缺桶不补第八行，切穿日值不能拆分。
33. **默认信任与实际覆盖检查。** 无完整性声明时照常按已读数据求值，空日收入0，本文时间示例默认 null 政策的 AOV 为 NA；
    首版不声明或消费外部业务缺口，成功读取不证明上游完整。认证日历范围不足、完整键映射/请求域覆盖检查失败及来源读取失败仍报错；
    需要未输出日期时构造新 Query，导出后的缺日期由外部统计检查；冷存不保存检查图，默认信任不生成完整性证据。
34. **累计起点。** MTD1220/1300，YTD1920/2000，财务月1270/1350；
    每种窗口只通过 Metric.window 声明，observe 提供期间/日历，不通过隐藏默认覆盖。显示起点不作累计起点。
    Cum 的数学零延拓不增加期初业务格子或缺测补值；两天都只有 A 的 distinct 窗口为1，不能把日计数累计成2。
35. **窗口充分性。** 七日 AOV 为305/26、150/13，日均为1810/33、1700/33；完成比率/P95+count不足。
    本次 Query 计划获取所需组件；Artifact 不保留组件，换窗口须新 observe。
36. **窗口与对应。** 七日1220/305、1200/300同比均3；闰日/月末无对应不夹日，
    不先搬每天贡献再替代基期自己的窗口。
    日粒度闰日 exact 失败、outer 保留缺侧；完整月/季/年桶含闰日仍可同名对应。
    ordinal 要求同粒度、相容日历和完整桶；长度不同只可用 outer 保留未配对尾部，不自动重采样。
37. **当前轴。** outer有仅基期行时current模式拒绝；非 NA 零基期默认 relative=NA、absolute 有效，显式 error 失败；不当缺侧或有限增率。
38. **时间重开。** 输出坐标、时区/实际边界与投影可断源读取/导出；不恢复日历对象、窗口图或新历史。
39. **可选身份约束。** 无 P 收入 100 与空 P 收入 0 不同；普通字段分组不因有 P 自动补齐，
    完整输出由 members=P, by=(mv.member(),)、显式分类域或日历请求。
40. **多根。** East 收入 100/West 退款 20 完整覆盖下净 East100/West−20，
    独立规约后合完整键，不为这些字段汇总附加隐藏身份名单。
41. **命名。** 同定义命名/临时同值、NA、政策和失败，构造不登记；
    声明不检查独立持久解析资格；loader 按依赖闭包拒绝仅会话内可解析的模型，不复制结果或重选名单修复。
    已加载的 null 除零政策在Query 节点保持；冷存只保存 NA 完成值，新定义使用本次政策。
42. **共同条件。** APAC收入100/退款10、EMEA200/20，APAC净90，不能漏过滤退款成70；
    member_count 可单值分类才适用，显式 P 的定义不改写。
43. **一次上下文。** scope.by(mv.grain("day"))与相容高级绑定等价；冲突拒绝；
    Metric不接受during/route。不同窗口可分别 Query 或显式 Metric.window 分支，不采用最近绑定优先。
44. **默认保存信息。** AOV 组件只服务本次计划，Artifact 保存显式输出与实际 Finding/Evidence。
    execute 无保留请求；证据不含图/内部组件闭包，data reader 不依赖 Catalog。
    普通 observe 的 Finding 为空，读取范围和实际检查摘要仍按执行 §1.2 保存为 Evidence，断源重开一致。
45. **时间样本规约顺序。** [10,0]/[0,10]先空间后P95为10，不是19；状态轴/间隔/时间规约前对齐不可丢。
46. **小型组合图。** ERROR/日志1/1、9/99总体0.1；引用捕获定义版本/重复出现/阶段，
    不要求原生Metric Column或原始多根join，schema在来源绑定阶段才解析。
    进程内仅接纳语义提案 §6.1 的 Ibis 数值图及语义边界；未知节点/规则版本定位拒绝，直接求值资格不自动授予本次摘要合并路线。
47. **声明体边界。** @metric 的零参数体返回 Metric，@measure 的零参数体返回同根行表达式；
    模型/runtime 同语言，可接收合法阶段的受控原生 Deferred；不接受表参数、外部无绑定图、任意回调、控制流或执行查询。
    装饰器只做受限图构造与声明；loader 不读来源。Metric 装饰器不接全局政策，Measure 的业务单位/权限显式保留。
    行/Metric/Column 混用必须给出预期阶段、实收定义与具体修复，不自动转换。
    validator 按已解析符号和接收者阶段检查 sum/where/per；ms.ref.domain 在 decorator 参数中解析，不扩大函数体调用白名单。
    Dimension/TimeDimension 等原有字段体保留受限 Ibis，ms.bind 只在原合同允许的该类体中保留，新 Measure/Metric 体拒绝。
48. **统一除法与配对。** / 与 divide 归一到同一 Ibis 除法及语义政策注解，持有 N/D 及同样默认政策；
    依赖充分时按原式重算，缺状态拒绝。共同 value=[10,Null]/weight=[1,9] 得10不是1；
    分组10/30、权重1/9合并28不是20。计算角色不自动成为公开路由，不开放树位置修改。
    共同非 NA 零分母/有效零权重和默认 null、显式 error 失败；NA/0 与无有效配对在两种政策下均为 NA。
    /、无参数 divide 和显式 null 归一到同一政策/指纹；error 属于不同定义，行/Metric/Column 同规则。

49. **统一域与引用。** 身份/字段/日历都是 Population，保留完整复合键与角色。
    Query.coord 仍无源读取地构造 Column；复合身份作为单量在 execute 准备期、Run 准入和业务 compile/read 前拒绝。
    完整复合键仍可输出、用 population/in_population 作元组筛选并导出；单量值改用已有单字段轴。
    query(population).execute 保存仅键 Artifact；没有恢复后 project。O404 可以作为显式身份引用，来源选择不命中，必需属性缺失仍失败。
50. **约束与完整输出。** M={A,B}、A 有 East 贡献、B 无贡献且分类 West；
    members=M 的普通地区分组只有 East，显式 by=M.classify(by=(region,)) 才请求 East/West。
    身份×日期四个格子投影成两个身份；空域保留轴类型，普通字段不可升级为身份。
51. **无隐藏名单。** 相同全局身份与各组金额/人数不能恢复不同的组身份分配；
    汇总丢弃身份轴后拒绝身份投影。Query 分类域在本次执行内使用当次分类；跨执行沿用已见分类须由脚本读取 Artifact 并显式构造字面量域，之后 read/classify 是新依赖；
    真正原指标身份明细须在原观察 by 中直接请求身份轴，不把独立分类读取当作收入快照。
52. **常量与列单位。** CNY 收入与 100 比较/加减时常量按 CNY 绑定，乘 1.1 仍为 CNY；
    同一 Python 标量可在不同位置独立绑定且不被修改。独立预算用 columns={"budget": "CNY"}，权重用 {"weight": "1"}；
    {"reference": None} 明确单位未知，缺少列 entry 或嵌套 {"unit": ...} 拒绝，columns 与每行量名须完全一致。
    两个已有量单位冲突仍拒绝，未知单位不当 "1"，恢复不改绑；常量直接使用 LiteralValue，不提供公开包装入口。
    纯 Metric 的裸数值加减/缩放遵守同一量纲规则，但不接收运行时 Column 或 values 参照。
53. **Session 时间默认与精确时刻。** 上海 Session 的日期默认解析为当地午夜，显式纽约只覆盖本次 scope；
    带偏移输入保持绝对时刻，DST 歧义/不存在时刻拒绝，恢复不取主机默认。
    十月 end 在 11 月零点选新版本；不提供左极限，十月内最后的旧版本不可表达，也不能用 end 减 tick 取得。
    naive datetime 构造拒绝，来源 naive 时间不套用报告时区；提供 §9.5 的有序内部采样 first/last；不把它扩成通用 as-of closing 或 forward fill。
54. **关系像与有界逆像。** O1→A、O2→A、O3→B，像为客户 {A,B}，对客户 {A} 的逆像为订单 {O1,O2}；
    接收者仅含 O1 时不能加入 O2。先取 O1 的客户像再对全域取逆像不还原 O1，字段像不能作客户身份 targets。
    Null 关联无像，存在多匹配不复制来源身份；固定来源身份后的关系读取仍是明确新依赖。
55. **统一份额入口。** Query.share("sales") 返回新 Query，保留原键/列/信息并新增 sales.share；
    不对同行 aov 等原列求总体，不修改输入。Column 无 share；未知/非数值列和输出名称冲突明确拒绝。
    空输入保留空输出；Query 新增份额列不继承原指标的上卷规则，投影保留原列仍按实际信息判断能力。
56. **分组总体。** East/Web200、East/Store120、West/Web150，默认分母470；
    partition_by=(region,) 分母为320/320/150，份额5/8、3/8、1，三个细坐标全部保留。
    地区×月份默认不猜按月分组；缺失/歧义轴或无合法合并时拒绝。
    共同非 NA 零分母默认份额为 NA、显式 error 失败，NA 分子仍为 NA；参照按原政策完成，原输入检查不被省略，重叠分组和非可加量仍检查真实支持。
57. **份额执行与产出。** Query.share 的分子/分母消费同一实际输入，无合格路线报缺口，不能提示先 execute 再 share。
    构造/contract 不读取分母。先 share 再筛 East，Artifact 保持320/470，阅读不重算；
    跨产物普通比值交 pandas，不获得贡献包含证明。
    已知非法构造立即拒绝；schema/后端才能判定的缺口在 execute 准备报出，实际数据检查在发布前完成。
    contract 披露具体待绑定/待检查要求，不执行读取，也不把 share/rollup 的待定资格显示成已支持。
58. **期间省略准入。** 省略 during 与显式 None 等价，普通规约有无时间角色均表示不追加时间过滤；
    已选名单、固有日期条件、where.within 保持原范围，member_count 仍需显式 P，其它字段/版本要求不变。
    任一依赖要求按期间采样/时间样本规约，或 by 的轴/Population 含日历轴时拒绝省略；混合普通规约、空 P 或乘零不免除要求。
    日期过滤和成员/字段时点不替代必需的采样/时间样本规约/日历绑定；不选择最近期间、当前时刻或 Session 日期范围，也不证明完整历史。
    进程内保持无附加期间的绑定，不读取来源最早/最晚时间推导观察边界；冷存不保存这个计算绑定。
59. **统一关系元组。** Field.via 的 path、Population.image/preimage 和 corresponding 的 via
    均只接非空有序 tuple；单跳 (R,)、多跳 (R1,R2) 保持相同的路径绑定与消费者约束。
    单 RelationshipRef、list、空元组、反向/断端或错误终点在构造时拒绝，不自动包装或把空路径当恒等；
    observe 不接受路由补全，贡献归属路径缺失/歧义按 §7.11 拒绝，Field.via 不替代贡献归属。
60. **统一粒度输入。** TimeScope.by、CalendarAxis.coarsen、to_date 只接 Grain；内建 mv.grain("day") 与认证 calendar.grain(level)
    共用入口，消费者仍分别检查分桶、粗化和包含层级。五分钟使用 mv.grain("minute", count=5)，count 不重复传给消费者；
    裸字符串、by/coarsen 的独立 count、非法内建宽度和不相容日历拒绝，不能把跨月周强行归入某个月。
61. **手写坐标类型。** coordinates 只接 Field 轴，键为 LiteralValue 或表示源 Null 分类的 None；
    手写身份名单只用 identities(entity, keys=...)，entity 必须是从完成 scope 或 catalog 解析的 Entity，裸 EntityRef 明确拒绝；
    每个键是完整主键元组，空名单仍是该 Entity 的类型化空域。
    ValueRow 的坐标按对应轴同样检查；字段 Null 可作键，不能作量值或身份键分量；普通字段值不能冒充 Entity 身份，完整复合键、有限值与 kind 检查保留。
    CalendarAxis/CorrespondenceAxis 拒绝手写键，包括空输入，不从日期标签、序号或端点元组猜绑定；
    日历域用 TimeScope.by/product，已有日历与对应域用 population/project/where 复用，保留真实轴和端点绑定。
62. **分类参数。** Population.classify(by=...) 接 Field、接收者原有 MemberAxis 及 owner 与接收者身份相容的 MembershipAxis；直接读目标成员字段，保留分配描述但不执行其中的自关系/多值路径。
    CalendarAxis、CorrespondenceAxis、外来身份轴及多值字段拒绝；关系像只用 image(via=..., at=...)，已有坐标投影继续用 project。
63. **同一 Metric 表达式。** 模型声明体与 runtime 使用同一接收者规约、算术和 per 后规约；
    同实际输入有同值、NA、政策及失败。无 loader/Session 的构造不登记、不读业务来源。
    裸 Ref 不支持表达式转发，先取得语义对象；Metric 声明体零参数，返回行值必须拒绝；Ibis Scalar shape 本身既不授予也不否定 Metric 资格。
64. **声明名与标签。** 两种装饰器在构造时捕获函数局部名；Metric 由显式或模型唯一默认 domain 限定，Measure 由所属 Entity 限定。
    后续 Python 别名与 observe 标签不重命名；不保留 named 或 name 覆盖。
    Metric 和只命名的 Measure 不改变计算指纹、政策、依赖或归属；新增业务单位/权限断言进入语义指纹。
    算术、where、per 规约派生后匿名；导出别名去重，不同声明全名冲突拒绝，loader 不改写原值。
65. **不可变域闭包。** 字面量和来源/Query 程序可被 where/per 依赖；Artifact、产物 ID 引用、活动 Session、连接、可变容器或回调作为依赖拒绝。
    保留轴/选择期间/版本/已有归属，外层条件不改写域；逻辑复用不冻结键，字面量键不冻结后续收入。
66. **模型收录边界。** 可独立解析的字面量和无执行 Session 绑定的来源依赖可以收录；
    仅会话逻辑域可临时观察但收录拒绝。Artifact 不是表达式输入，不能先声明后提升或剥离归属。
67. **内部样本选择。** A=100、C=300、P={A,B}：筛分子比率1/4，共同筛分子/分母为1。
    子 sum 显式 empty="zero" 时 per(P).mean()=50，全局 per(customer_id).mean()=200，先筛 P 再按出现键平均=100。
    per(P) 直接选择样本，不要求覆盖 C；observe(by=P) 未筛 C 时仍因输出覆盖失败。
    未获支持的轴能力、未完成 MetricSamples、缺贡献到 (k,j) 的绑定拒绝；不要求裸 j→k，Artifact 后处理只用 pandas，不能隐式变为重新观察。
68. **节点政策。** sum() 默认空输入与全 NA 均为 NA；sum(empty="zero") 两者均为 0，合并按需保存有效值支持但不区分二者；nulls="error" 检查所选原样本。
    mean 不接 empty/zero_denominator，count 固定规则；divide/weighted_mean/share/compare 的政策只作用该节点。
    外层 compare/share 的 null 不能吞掉输入严格 Metric 的 error；Session/observe/execute/装饰器不增加全局覆盖。
    来源/内部 quantile 的0/1端点合法，分别与同政策 min/max 相同；越界或非有限 q 拒绝。
    固定线性插值 h=(n−1)q；[0,10] 的 P95=9.5，单样本返回自身；先空间后 P95 的 10 对 19 反例不依赖后端默认。
    min/max/mean/median/quantile 忽略 NA 后为空则 NA，count_distinct 为空则 0；可用的 nulls="error" 在规约前检查原样本。
    装饰器政策参数拒绝，已有定义/引用使用原规则版本；冷存不恢复节点或政策，不能退回后端默认。
    [语义附录 A.1](../semantic/expression-model-redesign.md#a1-拟替代的已提交合同) 的旧参数/工厂在声明校验或模型加载拒绝，不静默忽略；
    修复依照作者改写表；sum(empty="null") 与显式 membership 可在附录 A.1.1 条件下等价，不提供 codemod 或兼容 loader。
    TimeField.via 保持明确的跨 Entity 时间路径，状态采样角色保留，fold 改为显式 per 规约；近似定义不自动替换。
69. **Measure 声明一致。** Quantity * UnitPrice 只形成行表达式，未声明新业务授权时 sum 拒绝；
    预定义/runtime Measure 使用同一零参数体、函数命名和事实继承；相同新授权后同值同权限，原单价保持不可加。
    name-only 声明及显式重复相同的已有事实规范化为同一计算定义；省略或 None 不清空单位或授予新权限。
    单位冲突、未知轴、跨事实根、Metric 混入行表达式拒绝；新声明的业务断言不冒充从数据验证的事实。
    已知 CNY+USD 在行/Metric 构造报 SemanticError，Query Column 报 AnalysisError，不等执行或隐式换汇。
70. **有限行权限。** 已授权金额减成本及常数缩放继承许可交集，可直接 sum；
    变量乘除、维度整数、改名、过滤、乘零和相消不能扩大可加范围。逐单毛利与聚合后乘积不是同一计算。
71. **显式采样顺序。** sum(time=SampleAt).per(SampleAt).quantile(0.95) 先空间后时间；
    time 角色/间隔和状态授权不能由 per 推断。同一时点可在各设备内分别取样；无期间、明确缺测或只有完成 P95 的固定状态分别验证，不自动补零。
72. **共同内部配对。** aov.per(CustomerId).weighted_mean(weight=order_count) 在同一内部域先完成两项再配对；
    来源 Field 不能作该 weight，推导样本考虑 value/weight 两者的相关键，缺合法贡献绑定失败；两者不要求业务可加，共同有效配对不可改变。
73. **最小表达式图与五场景。** 收入/AOV/固定人群均额/时点总量P95/共同配对均须有真实端到端路线；
    / 与 divide 规范图等价，无 SourceBody/Ratio/旧 Over 并行入口。静态示例和附录 H 只给预期，不是实现通过。

74. **各外部格子的内部样本。** East:A100、West:A300/B100，完整 A/B 均额50/200，实际客户均额100/200，总体客户均额250。
    不要求客户唯一属于地区；同轴内外绑定必须一致。外部 K 仍由 observe 决定，内部样本不凭空增加外部格子。
75. **嵌套规约信息。** East:A100/West:A300 与 East:A100/West:B300 的地区均额/人数相同，总体400/200。
    Query 计划必须取得内部身份/子状态或等价充分输入；只有地区 sum/count 的路线拒绝，不默存细域到 Artifact。
76. **常数所在阶段。** 地区100/300加指标常数10得110/310，总体重算410；直接求和420及把它视为可加份额均拒绝。
    每条记录先加10再求和是另一程序；新命名不改变许可。组件充分的原式 rollup 与完成值求和分别验收。
    加减字面量0保留原许可与必要检查；不因常数为0跳过未知或非法依赖。
77. **权重是运算角色。** 值10/30、不可加分数0.2/0.8作为明确权重得26；不为此授予分数 sum/share 权限。
    同根/样本与有限数值仍检查；有符号权重不承诺凸组合性质，有效零权重和及无有效配对保持不同政策。
78. **域按绑定准入。** 合法身份×字段、多身份元组保留完整联合键，不展开成笛卡尔积；单条贡献不能隐式重复。
    零轴{()}每外部格子求值一次，空域无内部样本；首期 CalendarAxis/CorrespondenceAxis 的窗口/端点绑定仍不支持。
79. **函数名校验与收录限定。** gross_margin 合法，GrossMargin、_gross_margin 及非 ASCII 函数名在装饰时失败，不静默改写。
    不接 name 字符串；声明无 domain 仍能临时观察。缺失/歧义默认域或不存在的显式域在收录失败，别名不规避重名。
    同一个未限定声明在收录后原对象不被改名，模型版本保存限定身份和声明位置；ai_context 复用现有类型，无独立 description。
80. **两种用法示例一致。** 默认 AOV 声明体/runtime 都用 /，与显式 null 规范化一致；严格 aov_strict 单独命名，0/0 分别验证 NA/失败。
    Field Ref 为 domain.entity.field，Metric Ref 为 domain.metric；pandas 轴 label 仅为说明文本，不自动解析 Ref 或恢复绑定。
数值oracle只检查数学反例；真正typed errors、lowering、保存捕获与冷恢复必须由将来实现观察。
不因原生Ibis编译成功授予Metric权限，也不把文档Python语法通过当成可运行DSL。

### 语义定义更新的验收修订（2026-10-15）

先前“constructed输入同路径改版本必须拒绝”和“copy必须保留该隐藏版本约束”的验收已被用户决定取代，
不是靠修复获得通过。新的最小验收区分以下事实，不改变上面的独立数值断言：

- 相同定义复用和同路径已更新定义均不因原版本差异单独拒绝；引用解析消费者使用其完整作用域。
  验证物理owner/helper、保留Ibis绑定和pure Measure的实际消费事实，显式Ref前向依赖仍有效。
- complete capture后，默认时间先后顺序、amount/cost交换顺序、晚到coordinate和Ibis前向闭包的结果一致；
  返回值不可变，不从活动scope刷新，不重跑module/import effects。仅“constructor版本等于后来Catalog版本”的旧断言须按实际闭包含义重写。
- 存储的计算、依赖或政策改变时，指纹/缓存键须随实际内容改变或使旧缓存失效；不许路径相同就错误命中。
  未改变的规范计算保持稳定；名称/ai_context不改变计算身份。旧表达式的定义及已有记录不得被回写成新含义。
- copy/deepcopy不再证明原版本强制绑定；如果仍提供值复制，只校验不可变计算/公开语义事实及当前消费者正确性。
  不保留仅服务废弃隐藏约束的测试矩阵。保留snapshot codec/记录完整性与认证依赖失效的独立回归。
- kind/贡献根/完整键/粒度/单位/权限/native与routed时间角色/null/业务历史时点及实际覆盖错误继续拒绝。
  SemanticExpressionError仍需保留原结构化repair，telemetry不能掩盖它，广告中的show必须有canonical Help。

旧515focused/53snapshot/311typing及broad710pass37fail3errors均是旧合同证据；
本修订的实现清理已在未提交R1.3工作树完成；独立评审、完整R1.3及新执行验收仍未完成。R1.4/R2旧声明fixture例外不覆盖新合同/缓存错误。

### 9.2 终端 Artifact 的验收

- execute 和 Session.artifact(id) 返回同一终端接口；没有 origin/fixed/cold、producer 引用或 DSL contract。
- 两者均从已提交 Parquet/记录读取；Artifact 不持内存 Arrow 句柄，提交失败无成功产物；释放 producer 后继续读取不变。
- 新产物与重开的键、nullable 值、dtype、presence、投影、次序和 Finding/Evidence 一致。
- writer 只接收输出；manifest/文件/Run/attrs 无图、语义对象、内部组件或可恢复 derivation。
- 独立进程禁用来源、Catalog 与图解码器仍可 show/to_pandas/findings/evidence_digest。
- 当前模型改名或删除不改变历史输出读取；Artifact 不保存 Ref/指纹，也不宣称检测当前模型或来源新鲜度。
- 旧 Store 代际的 Session.resume 明确拒绝并保留文件；更换 Session 名称不能绕过，修复指向新格式 Store 和新 Session。
- DSL 构造器和操作数拒绝以 Artifact/ID/数据轴充当计算依赖；无 FixedLeaf、ArtifactRead、ComputeRead 或隐式转 Query 路径。合法字符串字面量即使恰好等于某个 Artifact ID，也不查询 Store 或取得产物权限。
- 本次计划完成/失败后释放中间组件；下一次 execute 是新求值，不使用上次产物缓存。
- 普通 observe 的 Finding 为空，Evidence 保存本次实际读取范围及已执行检查摘要；新产物与断源重开一致。
  无来源读取或未执行检查不虚构记录，默认来源信任不写成完整性认证；不自动生成因果解释或业务结论。
- 数据与记录原子可见，失败不暴露部分成功产物；pandas 后处理修改不改变原产物。

### 9.3 简化执行路线的验收

以下是未来实现条件，不是本文独立数值脚本的通过项；执行合同由
[执行 §1.5](lazy-analysis-dsl-execution.md#15-单一执行路线与终端读写)拥有。

- 来源经 Ibis/SourceSession.execute 返回 pandas；字面量和本地阶段共用同一 schema 规范化入口，覆盖完整复合键、NA、整数/Decimal 和时区。
- 来源类型交接与产物往返分开验收：schema-only 检查后端表达式/类型族/方法能力，不扫描值；
  接受 Ibis/provider 原生数值精度损失，不为统一 dtype 添加业务无关 cast。大整数与 NA、Decimal 用例记录原生返回和 pandas 交接后的值及类型，不以无损为通过条件。
  校验完整键、NA/空输入、分组、配对和错误政策，产物以实际保存值验证往返；不能事后 cast 后宣称精度恢复。
  这是待实施的 2026-10-17 取舍，远端步骤与实服范围见[实施计划 §8.1](../../superpowers/plans/2026-10-13-lazy-analysis-dsl-redesign-implementation.md#81-远端数值-query-最小实施顺序)。
- 本地选择、规约、对齐和 share 复用 pandas/已准入数值内核；Marivo 调度/lowering 不直接调用 Ibis Arrow 导出或 Arrow compute，不创建隐式 DuckDB 本地连接。检查针对阶段边界，不禁止依赖内部使用 Arrow。
- DuckDB 用作测试来源时仍通过 datasource 的 Ibis 路线；不能让 DuckDB 代替 pandas 完成本地阶段，或将来源通过宣称为远端后端全部合格。
- 同一执行内“来源名单 → 完整元组谓词 → 新来源”和“多来源 → pandas 组合”保持实际依赖；share 的分子/分母消费同一实际输入，共享表不被原地修改。
  多来源须实际使用两个不同已声明 datasource ID，可以同后端；同源多表/多根不能替代，未验证组合明确报能力缺口，不承诺跨源快照。
- 名单 {(a,1),(b,2)} 不匹配 (a,2)，合法 None 字段坐标保持 null-safe 相等；同源可用完整键 semi-join。
  按[架构 §9.2](analysis-implementation-architecture.md#92-名单和多来源的执行内交接)核对 Ibis 类型/表达式资格与实际后端原生限制，
  动态规模在上游就绪后检查，不加 Marivo 大小配额；不使用 memtable、拆列 IN、业务临时表或拆批求和。
- PyArrow 只编码/读取输出数据；阶段交接不往返 Arrow，产物预览和完整导出不调用来源、compiler 或本地 DSL 内核。
- 发布请求只包含最终 pandas 输出和记录，writer 编码后提交存储引用；Top-N 展示行号绑定该主表 receipt，预览不重建完整键索引，导出仍按规范键序。
- 来源和本地的已准入同义操作保持 NA/空输入、配对、完整键和失败政策；类型归一化不被解释为修复来源已发生的精度损失。
- 优化遵守[方法精度](lazy-analysis-dsl-execution.md#14-数学定律的实现边界)；用 [1e16,1,-1e16] 验证不能把一次 fsum 当作任意分组 fsum 合并。
  无合格数值等价依据的改写不准入，浮点容差不放宽 where/Top-N、NA、零分母及错误结果。
- 本地输入与中间副本的内存成本显式记录；无合格路线或执行失败不换引擎、不静默截断、不扩大来源读取，不发布部分 Artifact。
- 阶段数据和隐藏组件在本次消费者结束后释放；只需来源计算的计划可直接发布，下一次 execute 不复用历史 Artifact。

### 9.4 原生 Ibis 表达式与自动延迟类型绑定

以下是 v0.36 的待实现验收条件；历史 oracle 和 Ibis 构造探针不证明这些已通过。
每个合法例都要覆盖命名声明与匿名直接消费；禁止通过增加 ms.ibis/ms.require 或手工 schema 满足测试。

| 最小场景 | 可核验结果 |
| --- | --- |
| 同根 `ibis.ifelse(amount > cost, amount - cost, 0)`；顶层 coalesce/least；方法 sqrt/cast/round | 受控延迟配方保留叶子/条件/分支；装饰器与普通脚本走同一路径；顶层函数结果可继续 `.mean()` 与 `.mean(time=paid_at)` |
| `(revenue / reference_revenue).sqrt()` 的命名与匿名输入 | 同值、NA、错误、政策和计算指纹；声明元数据独立；不把 sqrt 下沉到逐行计算 |
| 缺 schema 时构造、load、contract、observe | 无来源 I/O，dtype 明确 pending；execute 自动 schema 准备，无新用户生命周期 |
| 同配方绑定整数/Decimal/字符串 schema，及两次执行之间 schema 变化 | 前两者按真实类型推导和精度准入；不合法字符串输入拒绝；新 schema 不复用错误旧计划，不填假 float64 |
| CNY+USD、sqrt(CNY)、有单位的 log、条件分支单位矛盾 | 最早可判定边界拒绝；显式结果 unit 不能覆盖内部冲突；无单位不能默认 1 |
| abs/clip/ifelse 行式直接 sum，与显式新 Measure 授权 | 前者不自动获得求和许可，后者在根/单位/时间合法时可授权；状态固定轴不被授权擦除 |
| sqrt(sum(x)) 分区和 9/16 | 分区值 3/4，总体 5；不能相加得到 7；计划依据子指标状态重算 |
| amount.sum()+amount、多事实根行算术、跨根条件、外部同名 table、as_scalar 子查询、裸 Ibis window/UDF | 包括数值返回值在内都拒绝；Scalar/Column 广播不能跨语义阶段 |
| 独立两根规约后数值组合 | 保留各自过滤/时间/根，完整坐标键对齐；无合格本地内核在准备阶段拒绝，不隐藏 join 或 DuckDB |
| 原生 sum、/、reduction where= 与语义方法 | 归一到同一语义边界/政策；sum 空/全 NA 默认 NA、除零默认 NA、strict 失败；time= 不误传原生方法 |
| 恒真分支、乘零、相消、name 别名、优化 | 原始依赖及严格检查/贡献义务保留；未选分支仍接受静态检查，值域检查尊重实际选中值 |
| 未登记 operation、未知 Deferred resolver、pipe/回调、隐藏 execute/compile | 按真实符号和接收者拒绝且不执行；不能以结果断言或数值 dtype 放行 |
| 模型收录、缓存和终端产物 | 同一依赖闭包额外检查独立加载；声明指纹摘要配方，绑定指纹摘要真实图/schema/政策/作用域/路线；Artifact 不带 Ibis 图、配方或 callable |
| 数值语言清理 | 删除旧 Measure/Metric 数值 AST 及依赖旧节点的重复入口；保留基于 Ibis op 的检查、政策改写、源端适配和 pandas 内核分派，不建立第二数值 AST |
| schema 已知/未知、命名/匿名输入的完整组合 | 数值作者值均为原生 Deferred，命名记录位于受控 Resolver；包括 Dimension/TimeField 条件及 time 参数，kind 不丢失；顶层 ifelse 后仍可接 mean(time=)、per 和 divide；不继承 final Deferred、不向 Ibis 传普通数值包装对象 |
| 裸 Ibis Value 与外来 Resolver | 带关系的 Value 不进入作者入口；未知 Resolver 在调用前拒绝；获准 Literal 只作为子项；Entity/Dimension 原有受限 Ibis 体独立检查 |
| schema 之前的闭包发现 | 受控解析器先从配方找叶子/规约/per/政策/使用位置；schema 准备不需要先执行整份原生 resolve；无额外来源 I/O |
| 结构相等的 Divide 使用 null/error 政策，或相同行式有不同 Measure 授权 | Ibis 数值结构允许相等；语义使用位置和政策/权限不能覆盖，禁止以 dict[op, annotation] 单键实现语义身份；不同政策的声明指纹不同 |
| 同式命名、内联、别名和共享子式 | 声明计算指纹相同、检查域和结果一致；各使用记录仍独立。仅完整语义相容才共享执行，需求域取并集时不能漏掉另一消费者的严格失败 |
| 同一 x.cast("float64") 配方绑定 int64/float64 | 声明计算指纹相同；原生图可为 Cast/Field，绑定执行指纹不同；不按旧 schema 复用计划，不依赖 repr/内存地址/随机别名 |
| ifelse(d != 0, n.divide(d, zero_denominator="error"), 0) | d=0 或 NA 时选假分支返回 0；内联、匿名变量或仅命名均同义。独立观察严格除法时 d=0 必须失败；乘零和后置筛选不能消去失败 |
| cases、coalesce、普通运算 | cases 只在未匹配域继续；coalesce 只在前项 NA 域继续且不能吞严格失败；nullif、least/greatest、and/or 不提供隐含短路 |
| 分支静态检查和执行域 | 未选分支的未知操作、单位/根/类型冲突仍拒绝；结构/单位合法的未选 sqrt(-1) 不做值域计算。行域与 Metric 格子域分别传播，不能跨规约/per 改次序；先全域计算再掩码的内核不准入 |
| 条件依赖的 source/local 路线 | 准备阶段验证动态需求域的传播能力，执行时求域；无同义路线明确失败，不改成 eager 全分支检查。静态完整 schema 闭包和实际来源错误仍保留 |

Ibis 版本适配、原生构造、语义检查、来源数值路线与 pandas 本地路线须分别有证据；
类型检查通过不代表后端语义/精度通过，文档示例语法通过不代表当前 API 能运行。

### 9.5 五项能力补全与等价迁移

这是 v0.37 的待实现验收，不是已通过测试。每个合法表达式都要比较 @metric 命名、匿名内联与匿名复用；
对真实 Ibis/schema、来源和 pandas 路线分别验证。下表的确定数值只来自给定有限输入，不代表 Runtime 验收。
共同输入、窗口/成员绑定和方法资格须相同；不同 execute 不承诺同一读取快照，近似结果也不以逐位相等作为通用断言。

| 场景 | 必须观察到的结果或拒绝 |
| --- | --- |
| 两时点两实体 A=[10,NA]、B=[NA,20]，记录/时点齐全，空间 sum(empty="null") 后 last(include_null=False) | 每时点总量为10/20，结果20；不得取各实体末次非 NA 后相加成30；空间上卷须重算正确次序 |
| 已完成内部时间样本 [10,NA] 与 [NA,10] | 按 include_null=True/False 的顺序，前者 last=NA/10、first=10/10；后者 first=NA/10、last=10/10。nulls="error" 对两序列和两方法均失败，即使 NA 不会被选中 |
| 缺少声明要求的采样点，或多内部轴在排序时有同刻不同样本 | 缺测不当普通 NA 跳过；歧义排序拒绝，不使用来源行顺序；无样本且无缺测义务时返回 NA |
| first/last 与边界快照 | 期间最后样本不冒充精确期末状态；范围外最后已知值不自动进入，命名不改变评价锚点 |
| approx_nunique/approx_median/approx_quantile(0.95) | 保持非 NA 输入、计数/数值单位和标量结果，原生同名调用归一；q 越界/数组、错误字段 kind、无合格 dtype 或执行路线明确拒绝 |
| exact 与 approximate，或两种 sketch 不兼容 | 声明意图与绑定算法分开；不静默换算法种类、不相加去重估计、不合并完成 P95；没有承诺的误差界不出现在结果保证中 |
| sum 空输入、全 NA、[-10,10] | empty=zero 得0/0/0；empty=null 得NA/NA/0；nulls=error 对全 NA 失败、真正空输入按 empty 完成 |
| nullable sum 的分区合并与缺桶 | 有效零与全 NA 分区合并得有效零；全无有效值仍 NA；需保存必要支持信息；未请求/不存在的坐标不会由 empty 生成 |
| 订单 O1=100，关联 (O1,A) 两次、(O1,B) 一次 | A=100、B=100，并集原量100；同桶对去重，跨桶出现保留；格子 sum=200 只作为显式格子统计 |
| 未匹配订单 O2=50；两种 unmatched | exclude 不把 O2 放入标签桶；null 形成 NA 分类桶50；空成员集和数值 NA 不混同；实际成员版本/时点保留 |
| 订单根 Revenue=100 分到标签 A/B，标签根 Budget 为 A=50/B=25 | Revenue / Budget 在 A/B 为2/4：订单走显式路径，标签恒等绑定；不要求自环。缺路径、多条显式竞争路径、身份/角色/版本不相容或隐式贡献倍增拒绝；合法物理关联不因包含原始表而拒绝 |
| 单一联合成员元组与多个独立成员描述 | 单描述只保留真实相关元组；显式 (tags, channels) 按集合乘积。订单100、tags={A,B}、channels={web,store} 得四桶各100，并集100；重复/冲突轴拒绝，Field.via 不获得多值读取资格 |
| 多成员空集合 | 每个因子独立应用 unmatched；exclude 的空因子使乘积为空，null 因子产生一个全 None 元组再参与乘积；重复关联和真实 Null 分类按完整键集合去重 |
| 完整成员输出域与内部域 | coordinates(by=(tags,), keys=(("A",),("B",))) 携带绑定；仅A有100时，sum默认为A=100/B=NA、per该域mean=100；显式empty=zero 则B=0、mean=50。域不制造根贡献 |
| 来源完整域、实际域与覆盖 | TagEntity 身份域 classify(by=(tags,)) 直接读取目标分类保留A/B；描述含自关系也不在此执行。Query.population 不补未出现B；project/集合/product 保留绑定。外部漏掉有贡献桶报覆盖错误，内部 per 可选子域 |
| 显式自关系优先及无路径恒等 | 根E的A=100/B=20，自关系A→B、B→A；显式路径分桶为A=20/B=100，无路径恒等为A=100/B=20。自关系不被当作恒等竞争者；两条显式路径拒绝，显式无效路径报错而非回退 |
| 目标域分类与事实选路分开 | 上述E身份域用相同描述classify仍读A/B自身分类；字段值不沿自关系互换。事实observe/per才采用显式路径；命名/内联采用同一规则 |
| 多根与内部 membership 检查 | 每根逐描述优先用唯一显式路径，无路径时才推导相容恒等；per 的 sum 无不交及可加证明不能当总体求和；普通单值字段约束不变 |
| 同规则命名 window、匿名内联与匿名复用 | 相同观察绑定产生相同求值、需求域及计算指纹；构造器 axis 参数及 during=ObservationWindow 均拒绝，没有绑定/未绑定双形态或别名 |
| 显示十月，九月收入100、十月收入20；同一用户两月各出现一次 | 全历史截至十月端点收入120、去重人数1；显示起点不重置为20；MTD只消费十月；基础比率按窗口组件重算 |
| 标量 to_date 与精确边界 | end=10-08、start=09-01或10-01均求[10-01,10-08)；end=11-01零点选择刚结束十月。无精度 tick 技巧；日历跨月周仍因名义桶不唯一包含而拒绝 |
| 旧 first-seen / trailing 迁移 | A于09-30和10-02出现、B于10-03首次出现；[10-01,10-04)活跃去重2，first-seen新增1，全历史2。按语义附录的精确H/W组合得到1；旧首次桶、身份/过滤/时区和合法固定跨度保持 |
| first-seen 一致输入的迁移前提 | H/W确实为同一历史U的限制时精确组合等价；稳定输入、已有一致性保证或合格共同输入路线均可支持。独立变化读取、approx计数、历史被显示范围截断不能证明等价 |
| first-seen 未证实前提及普通窗口组合 | H读到1次、W随后读到2次时H==W为False，不能声称新增计数等价；合法普通配方不因此触发新增执行拒绝。命名new_users不启用特殊语法/读取模式，普通MTD+YTD不要求新增共同输入路线；不同窗口分支加外层per/count不是嵌套window |
| 窗口绑定与合格空间上卷 | observe固定逻辑轴/端点/W，未读取或冻结业务数据。按地区的窗口收入保留原日历轴做空间上卷时，依原量/充分信息合同检查；标量观察同样保留原端点，where/top不重绑W |
| 窗口时间轴上卷拒绝 | 依赖闭包含window时，粗化/替换/删除其日历轴均拒绝，包括经per/条件包装、只有一桶及count=1。无窗口量的合法日→月上卷保持原合同；重新评价月窗口使用新observe |
| 七日窗口时间合并反例 | 相邻端点各7个单位日贡献：完成值之和14、贡献并集8、末端点窗口7。rollup不任选其一；Query.reduce显式格子sum为14。trailing_periods(7)不能因日→月rollup被重解释为七个月 |
| DST 周的七自然日与固定 timedelta(days=7) | 前者按日历可能167/169小时，后者恒168小时；两者可在相同日输出端点评价；切穿日值须重取贡献，不改小时输出或按比例拆日总额 |
| 窗口缺端点、无时间角色、重复/嵌套、状态 all_history 无有限覆盖域 | 在最早可判定边界拒绝；不同分支各自窗口可组合；CalendarAxis 不覆盖 Metric 规则，固有过滤/主体选择不被扩大 |
| 分支、复用与两层指纹 | 未选分支仍检查语义/schema；动态桶/窗口只展开被需求输出；独立观察增加 strict 域。empty/include_null/q/成员政策/规则不同则定义指纹不同，真实 schema/算法/日历绑定改变执行指纹 |
| 不读来源构造与终端边界 | 无 ms.ibis/ms.require，无假 dtype、回放模型或隐藏连接；窗口/成员参数捕获不可变依赖；Artifact 不保留可执行图、sketch 或续算组件 |
| 旧声明修复 | 保留五项表达能力但拒绝旧工厂/默认参数；依据语义附录 A.1.1 逐声明说明等价条件，失败不以降级算法、默认零或半连接修复 |

源端 first/last 的 null/order 语义、精确与近似算法、nullable sum、成员集合和窗口边界分别需要路线证据。
静态文档检查不替代这些证据；不能用既有 R1/R2 或历史 oracle 的通过数量宣称本节完成。

### 9.6 Fanout 语义与执行策略边界

本节核对[语义 §3.7.1–§3.7.2](../semantic/expression-model-redesign.md#371-fanout-的表达方式与能力边界)，
不增加公开 policy、通用配对/分配程序或执行策略验收矩阵。固定模拟输入只用于比较算式，
不为普通多根查询增加共同快照或共享扫描要求。

| 场景 | 独立预期与未来实现检查 |
| --- | --- |
| 同一客户两笔订单100/200、三笔退款10/20/30 | 各根原量为300/60，净额240；原始行乘积直接sum为900/120、净额780，必须拒绝这种重解释或由等价计划消除倍增 |
| 单侧输出键与空输入 | 只有退款的地区仍进入共同K；成功读取后收入sum默认NA，显式empty=zero为0，不靠inner join删地区或对全部规约统一补零 |
| O1=100，桥关联A两次/B一次 | membership为A100/B100、并集100；存在性选根只选O1一次；以完整事件键建模的三条关联则count=3，三者不能混同 |
| 两笔不同订单均为100 | 合计200；sum(distinct amount)=100是错误修复，身份去重不能变成数值去重 |
| 订单100，分配记录A/B权重0.25/0.75 | 显式分配金额25/75，总计100；membership仍为100/100，不自动均分或推导权重守恒 |
| A/B收入100/0、预算10/90，按共同客户样本求比率均值 | (10+0)/2=5，不是100/100=1；各根保留完整(k,j)，不能提前压到最终k |
| 两部分均值0/100，有效样本数1/3；精确P95或跨根去重并集 | 合并均值为75而非50；P95和去重并集不能仅消费完成值。直接合法贡献规约可准入，摘要不足只拒绝对应合并路线 |
| 时间最近匹配或跨根条件配对 | 仅有两侧总量不能恢复匹配；缺少明确配对来源时报告表达能力边界，不启用隐藏join或改成内部同键weighted_mean |
| 候选物理计划与检查 | 在相同实际输入上，对已有资格的键集合去重/回取或预聚合计划，与直接贡献求值对照完整键、值、NA/empty和必要错误；不要求实现所有策略，不用语义合法宣称后端已支持 |

以下8项是独立算术见证，未调用 Marivo；未来真实路线仍按上一表检查，数值通过不证明编译或Runtime通过。

<!-- fanout-oracle:start -->
```python
from fractions import Fraction

orders = {"O1": 100, "O2": 200}
refunds = {"R1": 10, "R2": 20, "R3": 30}
assert sum(orders.values()) - sum(refunds.values()) == 240
assert sum(o - r for o in orders.values() for r in refunds.values()) == 780
links = [("O1", "A"), ("O1", "A"), ("O1", "B")]
memberships = set(links)
assert {tag: sum(orders[oid] for oid, bucket in memberships if bucket == tag)
        for tag in ("A", "B")} == {"A": 100, "B": 100}
assert (len({oid for oid, _ in memberships}), len(links)) == (1, 3)
assert (sum([100, 100]), sum(set([100, 100]))) == (200, 100)
assert [100 * w for w in (Fraction(1, 4), Fraction(3, 4))] == [25, 75]
assert (Fraction(100, 10) + Fraction(0, 90)) / 2 == 5 != Fraction(100, 100)
assert Fraction(0 * 1 + 100 * 3, 1 + 3) == 75 != Fraction(0 + 100, 2)
```
<!-- fanout-oracle:end -->

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
| DSL §4.9；[多轮示例 §2.13–2.14](lazy-analysis-dsl-examples.md#213-时间分析七日收入累计与期间比较)；本文 §9.1-31–38 | 完整输入上的窗口指标、覆盖、累计起点、不带轴的期间对应及完整示例 |
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
