# 语义对象与分析表达式的统一设计

Date: 2026-10-14

Status: proposed v0.25；与 [Lazy Analysis DSL 提案](../analysis/lazy-analysis-dsl-redesign.md)
共同构成目标设计，尚未实施。当前契约仍由
[Semantic Object Model](semantic-object-model.md)、
[Loading, Validation and Introspection](loading-validation-introspection.md) 和
[Python Analysis Design](../analysis/python-analysis-design.md) 拥有。
本文只更新设计，不修改当前实现、Help、技能或已发布示例。

## 1. 定义、观察与答案各有一个 owner

Metric 是完整的业务计算定义；Query 将定义绑定为本次问题；Result 固定实际答案。
命名模型与临时计算使用同一种 Metric、同一套定义校验和 lowering。
统一的是业务计算合同，不要求所有表达式都是原生 Ibis Column。

| 层或对象 | 拥有的事实 |
| --- | --- |
| Entity / Relationship / Field | 来源、身份、版本、读取路径、字段意义、单位和数值权限 |
| Metric | 来源规约、固有过滤、时间角色、空间/时间次序、数值政策和完整依赖 |
| 运行时 Domain | 类型化坐标及读取依赖；单 Entity 身份域是其特例，不进入纯语义 Metric 定义 |
| Session.observe / Query | 本次期间或窗口、分组、共同条件、明确主体及歧义路由 |
| Result | 已提交的键、值、Cell、证据与实际保留信息 |
| compare | 两个端点、显式对应、presence 和变化公式 |
| Ibis | 已绑定来源关系上的计算表达式、类型与合格后端 lowering |

`time` 是观察使用的业务时间角色，不是日期范围。`fold` 是采样状态的时间规约，
不是观察窗口。Metric 不捕获 Session、Result、动态名单或运行时 Domain。
明确主体计数可以作为要求 members 的上下文程序；它不捕获某个具体身份域。

Dimension、TimeDimension、Measure 保留精确声明种类和 Ref kind。数值编码 Dimension
不因物理 dtype 为整数就获得金额求和许可。成员身份、贡献出现和结果坐标仍分别拥有权威。

## 2. 按独立计算含义保留精确构造器

不把构造器数量作为优化目标。来源聚合、完整行计数、比率和配对规约有不同输入意义，
分别使用 `ms.aggregate`、`ms.count`、`ms.ratio`、`ms.weighted_mean`。
受限来源函数体仍使用 `@ms.metric`。这些入口返回完整 Metric，共用定义、校验和加载；
不保留 runtime 副本、define_metric/register_metric 或转发别名。

| 入口 | 输入意义 |
| --- | --- |
| ms.aggregate | 已声明 Field 上的精确来源规约 |
| ms.count | Entity 的完整贡献行计数 |
| ms.ratio | 数值 Metric 的明确 numerator/denominator 与零分母政策 |
| ms.weighted_mean | 同贡献域 value/weight 的共同配对规约 |
| @ms.metric | 已声明来源作用域内的受限 Ibis 计算体 |
| Metric 算术 | 已定义指标的逐坐标组合，直接得到完整临时 Metric |
| Metric.over | 在明确的语义内部样本轴上先求子指标、再规约 |

这些是目标形状，不是当前构造器的返回类型声明。模型加载负责收录命名定义；
临时构造不创建 Session、不执行、不写模型、不改变 Catalog。
来源构造器的 name 可省略；模型归属和名称由明确声明及 loader 决定，
不从 Ibis 列名、结果别名或临时 Python 地址猜测身份。Metric 算术的结果无需再次定义。
模型中的派生指标用显式命名元数据声明收录，不另设登记流程。

### 2.1 来源规约的目标签名

以下精确别名仅服务签名，不扩展顶层 Help：
AggregateMethod 是 sum/count/count_distinct/min/max/mean/median
与 `("percentile", q)` 的封闭联合；q 为有限 float 且 0 < q < 1。
TimeFold 是 mean/min/max/first/last 与 `("percentile", q)` 的封闭联合。
不自动选择近似算法。FieldRef、EntityRef、MetricRef 仍是精确持久引用。

```python
def aggregate(
    *,
    measure: Field,
    agg: AggregateMethod,
    name: str | None = None,
    time: TimeField | None = None,
    fold: TimeFold | None = None,
    nulls: Literal["ignore", "error"] = "ignore",
    empty: Literal["null", "zero"] = "null",
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContext | None = None,
) -> Metric: ...


def count(
    *,
    entity: Entity,
    name: str | None = None,
    time: TimeField | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContext | None = None,
) -> Metric: ...
```

measure 参数沿用既有聚合词汇；具体方法仍校验字段 kind 和权限。
count/count_distinct 可以消费合法 Field；数值规约不能用编码 Dimension 绕过 Measure 授权。
来源 Field count 是非 Null 值数，count_distinct 是不同非 Null 值数；
ms.count 是贡献行数，三者不能互换。两个计数方法固定空输入为零，
不接受改变其政策的 empty/nulls 覆盖；其它方法的 zero 只在有明确单位与数学意义时准入。
sum 默认空/全 Null 为 Null，需要空收入为零时显式 empty="zero"；不把 Unknown 补零。
严谨建模可以选择 nulls="error"，命名与临时定义遵守相同失败条件。

```python
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

where 构造带新增固有过滤的新定义，保存原定义的来源说明并形成新的指纹。
修改已加载指标时，新临时定义的 ref 为空，原声明引用只作为来源元数据；
不能将改变后的定义冒充已加载声明版本。模型中上述最终命名定义由 loader 收录为完整声明。
仅单一来源规约或同一完整配对作用域可调用 Metric.where；复合公式必须修改明确子指标再组合。
禁止 on 参数和通用表达式位置路径。

### 2.2 显式比率与配对规约

```python
def ratio(
    *,
    numerator: Metric,
    denominator: Metric,
    name: str | None = None,
    zero_denominator: Literal["undefined", "null", "error"] = "undefined",
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContext | None = None,
) -> Metric: ...


def weighted_mean(
    *,
    value: NumericField,
    weight: NumericField,
    zero_denominator: Literal["undefined", "null", "error"] = "undefined",
    name: str | None = None,
    time: TimeField | None = None,
    fold: TimeFold | None = None,
    unit: str | None = None,
    domain: DomainRef | None = None,
    ai_context: AiContext | None = None,
) -> Metric: ...
```

ratio 的两项须为数值 Metric；完成各组件后采用 §3.4 的明确分母政策。
普通 Metric `/` 共享数值默认但不获得专用角色，保留通用依赖和充分状态上的原式重算。
上节命名 AOV 显式选择 Null，以保持该业务定义的空日口径。

来源 weighted_mean 只接受同贡献域的两个数值 Measure。value 可以不可加，
weight 必须在所求域合法可加；不能混用来源字段与 Metric 格子。
Metric 内部域上的 value/weight 配对使用 §3.5 的显式内部域规约，
固定结果的普通配对统计经to_pandas交给pandas。来源与内部域两种定义不以 optional by 参数混在同一签名中。

### 2.3 保留有输入作用域的 Ibis authoring

删除 v0.18 新增的零参数 `@ms.metric` 与直接 `ms.metric(expression=...)` 双入口。
保留具有明确来源参数的受限 Ibis authoring；函数体仍只返回 Ibis 表达式。
其 Entity 参数、依赖捕获、语法限制和物理类型解析沿用既有 authoring owner。
不为这次收敛重写现有完整声明签名；具体声明能力按当前 owner 逐项核对后实施。

Ibis 只在来源 schema 和合法关系绑定后构造实际表达式。装饰时捕获受限计算定义，
不为模拟未知类型而访问来源；加载保持 source-I/O free。
字段读取使用当前明确的 `ms.bind(field_ref, rows)`，不是同名物理列替代。

```python
OrdersRef = ms.ref.entity("commerce.orders")
AmountRef = ms.ref.measure("commerce.orders.order_amount")
CostRef = ms.ref.measure("commerce.orders.order_cost")


@ms.measure(entity=OrdersRef, additivity=ms.additive_all(), unit="CNY")
def order_margin(rows):
    return ms.bind(AmountRef, rows) - ms.bind(CostRef, rows)
```

函数体保持可选首段 docstring、全新顺序局部绑定与唯一最终 return；
不开放导入、控制流、状态写入、查询执行或任意回调。装饰器是有作用域的 authoring，
不是对临时 Metric 再包一层的替代写法。函数体本身不能以声明 additivity
替代实际状态捕获或授予固定续算能力。

## 3. 来源计算与指标组合保持两个明确阶段

### 3.1 Ibis 的边界

Field/Entity/Metric 不承诺脱离来源绑定的原生 `.expr`。
Field 公共谓词只包括类型化比较、状态测试、集合测试及布尔组合；
复杂逐行计算进入受限 Ibis authoring，不向消费者复制完整 Ibis 方法集。
Entity 通过精确语义构造器计行，不伪造无 schema 的 Ibis Table。

Metric 组合图是小型、版本化、依赖中立的语义图，首期节点与资格边界见 §6.1。
它保存运算阶段、依赖出现和政策，
不是 Ibis AST 的复制品，不需要合成 Metric Column、虚拟 relation 或 monkey-patch。
Field 谓词与结果 Column 使用各自类型边界，不能混入错误阶段。
阶段误用的具体修复由 [执行与披露 §2](../analysis/lazy-analysis-dsl-execution.md#2-agent-读取与错误契约) 拥有；错误说明当前输入和合法接收者，
不提供 Field/Metric/Column/Ibis 之间绕过身份或状态校验的转换。

```python
net_revenue = revenue - refunds - fees
adjusted_revenue = net_revenue * 0.9 - fixed_cost
error_rate_percent = error_rate * 100
ordinary_ratio = revenue / orders
```

全部返回完整 Metric，不需要 as_metric、登记或再次 ms.metric 包装。
普通除法不提升成显式 ratio；乘积和不从公式模式提升成 weighted_mean。
来源根分别求值后按完整目标键组合，禁止把原始事实 join 成宽表再聚合。
尚无合格 graph/lowering 的运算明确拒绝；不因 Ibis 可以表达就获得资格。

### 3.2 时间角色和时间 fold

来源 Metric 的 time 明确消费哪一个业务时间角色；省略时只继承唯一已有声明。
没有声明则无时间角色，多个角色要求选择；不扫描日期列或按名称猜测。
派生 Metric 各依赖保留自己的时间角色，不能以一个参数覆盖全部依赖。

状态指标先在每个精确采样时点按目标空间规约，再在观察范围中 fold。
采样轴、间隔与状态约束来自语义声明；缺测不自动补零。
fold 使用精确 TimeFold 值，不使用未绑定 Ibis Deferred。

```python
bandwidth_p95 = ms.aggregate(
    name="blink_device_p95_bandwidth",
    measure=bandwidth,
    agg="sum",
    fold=("percentile", 0.95),
    unit="bit/s",
)
```

两个设备分别为 [10,0]、[0,10] 时，各设备 P95 为 9.5；
同时点空间求和后 [10,10] 的 P95 为 10，不是 19。
固定空间上卷需要可按时点对齐的折叠前输入；只有完成 P95 或失去时点的分布不够。

观察期间/window 只由 observe 拥有。Metric 没有 during/route，
没有根默认、分支默认或最近绑定优先。固有固定范围与观察冲突时拒绝，
不偷偷覆盖或取交集；原定义仍可在合法上下文直接观察。
不同期间的分支分别 observe，再显式 align/select/compare。
observe 省略期间的准入由 [DSL §4.1](../analysis/lazy-analysis-dsl-redesign.md#41-session字段时间与路径) 拥有：
普通来源规约可省略 during，表示不追加时间过滤；已有业务时间角色及固有日期条件保持，不因有时间角色就要求期间。
只有按观察期间采样、时间 fold 或日历输出等真正依赖期间的情形才必须显式传 during；固有日期过滤不能替代这些绑定。
没有时间角色的定义不因此获得隐式时间角色，省略期间也不证明来源包含完整历史。

### 3.3 指标依赖与比率

```python
error_rate = ms.ratio(
    name="vod_playurl_error_rate",
    numerator=vod_playurl_error_count,
    denominator=vod_playurl_log_count,
)
```

Metric 引用捕获完整定义、版本、固有过滤、单位、时间/fold 与依赖。
同一指标出现两次仍是两次逻辑使用；内部出现身份用于追踪，不成为公开 left/right 配置路径。
显式比率可发现 numerator/denominator 的定义角色；它们不授权独立改变原式权限。
复杂组合的分支条件在完整子指标上明确形成，再重组；不通过通用树位置修改模型。

#### 3.3.1 显式角色与通用依赖图

通用算术从每个依赖的合法充分状态恢复目标值，再按原式组合。
缺状态时不能因最终值相等、单位相容或 additive 标签获得重算能力。
普通除法可以在依赖充分时重算，仍没有专用 ratio 角色。
相容加减及无量纲常数缩放只继承各依赖合法可加范围的交集；
条件、变量乘除、相消或乘零不自动扩大权限。

单位由字段与明确代数规则推导：相同且已知的单位才能约去为 1；
未知单位不是无量纲。来源/Metric 构造器的 unit 参数校验相容性，不执行换单位或新增授权。
比率和加权均值的完成值不可加，组件是否合法合并另行判断。
裸数值常量在与 Metric 加减时采用该量已有单位，在乘除时作为无量纲系数，绑定只发生在该使用位置；
不因此扩大可加范围或更改已有定义。语义图仍不接收运行时 Column 或 values 参照。
结果常量、比较阈值及独立参照列的规则由 [DSL §4.1.2](../analysis/lazy-analysis-dsl-redesign.md#412-常量与独立参照列的单位) 拥有：
结果常量直接使用 LiteralValue，不带 unit；独立参照量在 Session.values 的 columns 列名到单位映射中声明单位，
已保存量不得按裸常量重新赋单位。

#### 3.3.2 weighted mean 的配对与状态

value/weight 从显式输入确定，不从乘法次序、列名或数值猜测。
节点形成共同非 Null 配对条件，连同业务过滤同时作用于乘积和、权重和及支持计数。
空/全 Null 配对与有效配对的 Defined 零权重和分别按 §3.4 处理；
Unknown/Undefined 不当成可忽略 Null。
保留乘积和、权重和、共同样本及必要支持事实，不能只保存完成均值。

value=[10,Null]、weight=[1,9] 得 10；单独用全部权重作分母得到 1，是另一计算。
分组值 10/30、权重 1/9，合并配对状态得 28，不是组均值平均的 20。
非负或正总权重前提由需要它们的消费者检查；单位消去不证明这些前提。
带状态 fold 时保留先空间配对再时间 fold 的次序和折叠前信息。

#### 3.3.3 drill、贡献和归因的边界

观察下钻按细坐标分别完成组件。两组 ERROR/总日志为 1/1、9/99，总体率为 0.1。
子率 1、1/11 不可直接相加；在完整不交分区和非零分母前提下，
N_i/总D 为 0.01、0.09，才解释总体率构成。
share 要求可加贡献及共同实际输入依据，不对比率完成值自动做份额。
变化归因需要消费者选择分解方法与验证前提，仍不纳入本轮交付。

### 3.4 样本政策的归属

这一节拥有新 Metric 构造与普通算术的数值政策；结果统计的完整默认表由
[DSL §4.4](../analysis/lazy-analysis-dsl-redesign.md#44-最小规约与有效样本) 拥有。
实际 Cell 传播、结果谓词与 typed error 由
[DSL §5.4](../analysis/lazy-analysis-dsl-redesign.md#54-cell部分运算与谓词) 拥有。
采用的是明确 Marivo 合同，Ibis lowering 必须保持它，不由后端默认反向决定。

**新定义的 Defined 零分母默认统一为 Undefined。**
普通 Metric `/`、结果 Column `/`/divide、显式 ratio、有效配对上的 weighted_mean、
share 和 compare.relative 使用这一规则。ratio/weighted_mean 的
zero_denominator="null" 与 Column.divide(zero="null") 是显式的另一种口径；
构造器的 error 选项为结构化失败。专用角色不决定默认分母政策。
受限来源体中的受支持除法也保存运算政策；新定义中未保护的Defined零除由Cell边界给出Undefined，
显式nullif(0)则先生成Null分母，沿Null传播。lowering按语义处理，不能依赖后端inf/NaN或隐式SQL NULL猜测状态。
已加载来源体仍使用保存的政策/规则版本。

| 输入 | 结果意义 |
| --- | --- |
| Defined 分母为零，包括 0/0 | 新构造默认 Undefined(zero_denominator) |
| 明确选择 zero_denominator="null" 或 divide(zero="null") | Null，保存这项政策与原因 |
| 分母已是 Null、Undefined 或 Unknown | 沿原输入/Cell 合同，不将其当作 Defined 零 |
| 来源 weighted_mean 没有有效配对或全部配对为源 Null | 原空样本合同的 Null；尚无 Defined 权重和 |
| 来源/纯语义内部 weighted_mean 有有效配对，但权重和为 Defined 零 | 默认 Undefined；Null 必须显式选择 |

结果 reduce 仅保留 DSL §4.4 的 sum/mean/row_count，不能由来源 empty 默认覆盖。
普通结果配对统计经 DSL §4.6.1 交 pandas，调用方明确共同有效样本及零权重和政策；不保留结果 record 家族。
share 与 compare 的固定公式沿统一规则，不新增零分母覆盖参数。
需要另一业务口径时显式构造并命名普通派生量，不把改过的公式冒充原份额或原 relative。

**已有指标政策保持原样。** Catalog 交付已解析、版本化的完整定义，
新默认只参与新定义构造，不作用于已有定义、其组合引用或固定 Result 的恢复。
新定义指纹保存已解析政策与运算规则版本；恢复使用保存事实，
不能因默认改变、Catalog 更新或名称相同重新填政策。
旧记录没有必要政策事实时按信息/格式不足拒绝，不用新默认猜测，不增加迁移/双读。
若要改变已加载指标的政策，显式构造新定义并给出新的身份/指纹，不就地覆盖原指标。

来源聚合沿显式 nulls/empty 定义，通常忽略源 Null、空有效输入为 Null，
来源计数固定空输入为零；原 Ibis authoring 的失败条件也保持。
结果格子统计有不同样本单位与默认拒绝合同，完整规则见上述 DSL owner。
同问题换写法时须核对输入单位及政策，不宣称所有同名统计天然等价。

```python
default_aov = ms.ratio(numerator=revenue, denominator=orders)
empty_as_null_aov = ms.ratio(
    numerator=revenue,
    denominator=orders,
    zero_denominator="null",
)
```

这两项均是完整新 Metric，区别明确进入定义；后一项不会改写前一项或 Catalog 的旧指标。
命名/临时的同定义、同输入仍有相同值、Cell 和失败条件。
内部格子的 Unknown/Undefined 不能 lower 成 SQL NULL 再忽略。

### 3.5 内部域规约与已固定域统计

Metric.over 构造纯语义内部域节点；by 必填，只接受可独立加载的语义样本轴，
不接受 Query、Result、外部分类或运行时 Domain，包括其中的单 Entity 身份域。
这些轴定义内部样本单位；不是外部输出分组，也不捕获本次人群/期间。
内外域须有合法明确映射；同名轴、不完整分类或内部轴重复外部绑定时拒绝。

```python
mean_customer_revenue = revenue.over(by=(customer,), agg="mean")
mean_customer_aov = aov.over(by=(customer,), agg="mean")
```

这里 customer 是明确语义字段；贡献推导域不包含从未出现的类别。
若要包括无贡献客户，需要在观察中明确完整身份输出或完整业务域；
仅传 members 约束贡献不补齐普通字段分组，不能将上述式子解释为客户全集平均。
需要对固定 A/B 名单统计时，先 observe(by=fixed_ids)，再 reduce，
域大小用 s.query(fixed_ids).reduce({"ids": mv.row_count()})。
不再使用 ms.metric(expression=_.count(), by=runtime_domain)。

内部域配对使用 Metric.over 的 weighted_mean 闭合变体：接收者为 value，
weight 必须为 Metric，by 为纯语义样本轴；两子指标先按完整内部键求值再共同配对。
该变体与单量规约使用精确重载，不接受来源 NumericField 或运行时域混入。
共同掩码与实际充分状态按 §3.3.2，空配对/Defined零权重政策按 §3.4；
固定名单的普通配对统计按 [DSL 交接合同](../analysis/lazy-analysis-dsl-redesign.md#461-to_pandas-的固定交接合同) 导出后处理；
来源配对与内部域配对仍保留共同样本、单位及可恢复充分信息，不把运行时名单捕获成模型。

## 4. 声明与发现直接交付可用对象

Field、Entity、Metric 声明及 Catalog 类型化集合返回对应不可变对象。
精确 Ref 用于定位声明、跨文件前向引用和持久引用；不允许 Ref 算术。
已构造定义捕获依赖版本，Catalog 更新不改变旧 Metric 或固定 Result。

```python
class Field:
    ref: FieldRef

    def via(self, path: tuple[RelationshipRef, ...]) -> Self: ...
    def at(self, anchor: TimeAnchor) -> Self: ...
    def is_null(self) -> FieldPredicate: ...
    def show(self) -> None: ...


class TimeField(Field):
    def within(self, scope: TimeScope) -> FieldPredicate: ...


class Entity:
    ref: EntityRef

    def show(self) -> None: ...


class Metric:
    ref: MetricRef | None
    contributions: Mapping[str, ContributionRole]

    def where(self, predicate: FieldPredicate | DomainPredicate) -> Metric: ...
    @overload
    def over(self, *, by: tuple[Field, ...], agg: AggregateMethod) -> Metric: ...
    @overload
    def over(
        self,
        *,
        by: tuple[Field, ...],
        agg: Literal["weighted_mean"],
        weight: Metric,
        zero_denominator: Literal["undefined", "null", "error"] = "undefined",
    ) -> Metric: ...
    def show(self) -> None: ...
    def contract(self) -> MetricContract: ...
```

contributions 是只读具名语义贡献角色映射：基础规约唯一 contribution，
专用构造器或模型只暴露明确声明且能定位具体根的业务角色。
复合公式没有唯一或具名角色时不生成 left/right 槽位；应分别观察或在语义模型明确角色。
句柄捕获当前定义归属/版本，不能借同根或同名跨指标绑定。

MetricContract 是定义/所需绑定/充分信息要求的只读合同，不依赖 Session/Runtime。
它不复制 AnalysisContract 的当前 Result 生命周期。
Field 支持有限的类型化比较和布尔组合；上述签名不列重复运算符。
复杂逐行表达式由受限 Ibis authoring 拥有，Field 不扩成完整数值运算语言。

Field.via/at 定位字段读取角色和属性时点，与 observe 的主体归属路由不同。
Field.via 的 path 与 routes 各 value、其它 via 输入共用
[非空关系元组合同](../analysis/lazy-analysis-dsl-redesign.md#41-session字段时间与路径)；
单跳写 field.via((relationship,))，多跳按声明顺序排列，标量字段读取仍要求合法单值路径。
TimeAnchor 的闭合定义及构造由 [DSL §4.1.1](../analysis/lazy-analysis-dsl-redesign.md#411-精确时刻与版本选择锚点) 拥有：
Session.instant 或 scope.start/end 提供精确时刻，scope.before_end 提供左极限；Field.at 接受两者。
Session.time_scope/instant 默认使用报告时区，显式时区只覆盖本次构造；解析结果固定，不成为语义模型的运行时默认。
运行时锚点只进入本次绑定；不能把依赖 Session 的 Field 变体收录成可独立加载模型。
Field/Entity/Metric 都有 bounded repr/show，公共叶子随 owner Help 渐进发现，
不把内部图类型或字段绑定表加入顶层 exports。

## 5. 观察上下文是普通分析的唯一绑定入口

```python
daily = s.observe(
    {"revenue": revenue},
    where=region == "APAC",
    during=week.by(mv.grain("day")),
).execute()
```

普通调用无需 Entity、身份域或额外注册。共同条件对全部来源组件完整绑定；
有一项不能唯一解释条件就拒绝，不部分过滤。
每个组件的业务时间角色不变；本次期间/窗口只在该观察中绑定一次。
需要不同条件/窗口的分支分别 observe，再组合 Query。
固定 Result 上的 Column 不能放进 Metric 或通过某属性回到来源阶段。

歧义主体路由在 observe 的高级 routes 参数中绑定，key 是语义贡献角色的类型化句柄，
value 是非空有序关系元组；不是 left/right 字符串路径。唯一声明路径可机械解析，不按最短或最低成本选择。
普通观察默认信任声明的数据源，只有明确缺口影响所需贡献时才传播 Unknown。
完整性未检查不等于已知不完整；完整调用、缺口处理及保存合同由
[DSL §4.9.3](../analysis/lazy-analysis-dsl-redesign.md#493-覆盖缺桶与-cell) 拥有。

## 6. 内部实现与一次切换

| owner | 负责 |
| --- | --- |
| semantic authoring / loader / Catalog | 声明、名称/归属、依赖校验与可发现定义 |
| 共同私有定义图 | 小型 Metric 组合、定义引用、角色、单位/许可、时间/fold |
| Ibis 来源 lowering | 合法关系/schema 上的受限来源计算与后端操作 |
| analysis | 单次观察绑定、Query/Result、Cell、坐标与实际信息上的续算 |
| compare | 端点对应和变化计算；不细化来源 |
| Help / contract / errors | 静态导航 / 当前定义或信息 / 具体修复 |

复用当前 metric_graph、表达式 sidecar、compiler 和发布/恢复链；
不建第二 Runtime，不要求原生 Metric Column、虚拟共同关系、Ibis monkey-patch 或全 API 编码。
保存规范化定义、受限 authoring 计算描述、依赖版本、操作规则和必要状态，
不保存 Python callable、活动 Ibis/Catalog 或连接。共同图不导入 Session/Runtime。
来源函数体只有最终值时不自动拥有可合并状态；实际捕获仍是独立实现资格。

### 6.1 首期节点集合、保存成本与资格边界

下表是闭合的逻辑目标集合，不是已通过的后端矩阵。节点类型与字段保存在私有版本化图，
不逐个导出公共类。Metric 级通用条件节点不在首期集合；
来源条件继续由受限 authoring 拥有；一般结果条件/分类交 pandas，不新增 Column 条件节点。
新增节点须同时补类型/单位/权限、数值政策、依赖出现、编码、恢复和实际路线。

| 私有节点族 | 目标直接求值 | 原指标 rollup | 固定续算所需信息 |
| --- | --- | --- | --- |
| 定义引用、有限数值常量 | 引用完整定义，常量不制造来源 | 继承被引用定义的条件；常量不提供贡献证明 | 冻结定义/类型/单位与操作政策 |
| 来源 Aggregate / Entity Count | 合法贡献域上的精确方法 | sum/count/min/max/mean 等按方法状态合并；distinct/quantile 另需精确信息 | 输入或相应充分组件、支持/覆盖及坐标映射 |
| 有作用域的 SourceBody | 在已绑定 schema/关系上执行受限 Ibis 定义 | body 可直接求值不授予上卷；只接受已明确捕获的合格状态 | 冻结 body 描述供解释；实际续算还需已捕获输入/状态 |
| Negate / Add / Subtract | 各 Metric 完成后逐坐标计算 | 合法恢复各依赖到目标坐标后按原式计算 | 每项依赖的状态与完整键对应 |
| Multiply / Divide | 合法数值及单位，明确零分母政策 | 从依赖状态重算，不推导任意变量乘除的可加性 | 依赖状态、阶段、政策和原检查 |
| Ratio | 显式 N/D 角色，完成组件后组合 | 独立合法恢复 N/D，再按保存政策完成 | 两组件状态、定义/角色和对应 |
| SourceWeightedMean | 同一贡献域的共同配对 | 合法不交分区的配对充分状态合并；fold 次序另检查 | 共同样本、乘积和/权重和、支持和必要折叠前信息 |
| OverAggregate / OverWeightedMean | 纯语义内域先子指标再规约/共同配对 | 内外映射、完整样本及相关状态均保持才准入 | 内部完整坐标、子定义/值或充分状态、映射及政策 |

内部域不是“任意重复列”；坐标或样本不完整时不能仅因节点种类匹配而放行。
来源 state fold 是 Aggregate/配对定义的精确时间子合同，沿 §3.2；
它不新增未绑定 Ibis Deferred 或任意序列节点。
记录中的元数据都能保存，不代表该节点的固定执行器或 retained capture 已获资格；
Help/contract 分别披露可构造、待实际求值、可保留与可固定续算。

这份私有图仍有明确成本：规范化和类型/单位检查、规则版本、codec 与 schema 校验、
依赖发生位置、lowering/check 调度、充分信息捕获、冷恢复，以及合格物理组合。
来源 sidecar 只维护已获准的有作用域计算描述，不镜像整个 Ibis 操作集。
运算参数/默认政策先规范化后编码；图、body、方法和状态版本分别验证。
新增操作或不兼容编码先失败并披露实际位置，不能绕过白名单、降级算法或改用当前默认。
计划 R1 建立引用/常量与最小 Aggregate，R3 扩展上述闭合组合；
R6/R7 逐项验证 retained capture 和固定执行，避免先声称统一支持。

## 7. 实施与可核验条件

本地 R1–R8 计划与 DSL 提案同步为 v0.23；全部阶段仍未实施。
优先验证纯 Metric → observe → execute → Result → 断源恢复，
再验证端点比较、选择与独立细化，不把旧 Ibis 桥接或自动比较下钻作为后置承诺。

必须验证：

1. 同定义命名/临时 Metric 同输入同值、Cell、失败和依赖，构造无登记或取数副作用。
2. 模型加载 source-I/O free；物理 schema 到来源绑定才解析，不合成未知类型 Metric Column。
3. 多来源组件独立求值、完整键组合，重复依赖不去重、分支过滤不污染其他定义。
4. observe 上下文只绑定一次；Metric 不能设置期间/路由默认；冲突精确拒绝。
5. 来源规约、语义内部样本、最小结果规约和 pandas 统计不同；运行时固定域不能混入模型定义。
6. 五分钟采样先空间后时间 P95 为 10；完成设备 P95 的 19 不代替它。
7. ratio/weighted_mean 保留角色、政策、共同样本、单位/许可和充分状态。
8. 通用算术在实际依赖充分时原式重算，缺信息拒绝；没有专用角色不阻断合法一般重算。
9. 冷恢复不重载 Catalog/来源，恢复实际能力；只有 authoring 最终值不伪造状态。
10. 来源或固定端点先 drill 再 compare；比较直接 drill/retain_detail 均拒绝。
11. 普通观察无需完整性声明即可求值；明确缺口只影响实际相关贡献，默认信任不生成已检查证据。
12. 新 Defined 零分母默认 Undefined；显式 Null 与已加载/已保存旧政策独立验证，归属见 §3.4。
13. §6.1 节点逐项验证直接求值/合并/固定资格和未知编码拒绝，不以小型图为由省略成本。
14. 静态 Help、exports、错误、实例合同及未来中英文示例一次同步；技能仅在另获授权后更新。
15. 常量局部单位绑定不修改原定义，不将未知单位当作无量纲；独立参照列与来源单位各归其 owner。
16. 属性版本的精确时刻/左极限与 Session 时间构造默认一致，不能用 end 减 tick 或重新解析主机时区替代。

这些是未来实现验收条件；文档和独立数值见证不构成桥接、执行或恢复证明。

## 附录 A：设计变更记录

| 早期目标 | 当前收敛 |
| --- | --- |
| 全对象原生 expr 与通用 Ibis 图编码 | 来源绑定后使用 Ibis；Metric 使用小型语义组合图 |
| ms.metric 五种 expression 变体 | 精确来源构造器、Metric 算术、纯语义 over 和结果 reduce |
| 零参数装饰器等价入口 | 保留有来源作用域的受限 authoring，不新增零参副本 |
| Metric 期间/路由默认 | observe 一次绑定上下文；来源完整性遵守 DSL 的默认信任规则 |
| components 中的通用 left/right 路径 | 内部出现追踪；公开只发现明确语义贡献角色 |
| runtime 域直接进入 Metric | 固定域 observe/query 后以结果统计完成 |
| ms.linear 与 runtime 副本 | 小型 Metric 算术；模型/临时使用同一入口 |


v0.20增量：新Defined零分母统一Undefined，Null显式选择且已有政策冻结；
小型图闭合集合和维护成本由§6.1拥有。观察依赖、谓词绑定、compare命名和阶段修复由[执行与披露 §2](../analysis/lazy-analysis-dsl-execution.md#2-agent-读取与错误契约)及对应算子拥有。


v0.21 增量：按 [场景验证 §8.6](../analysis/lazy-analysis-dsl-scenario-validation.md#86-逐算子的价值准入) 逐算子判断增量价值。
来源 AggregateMethod、TimeFold、ms.weighted_mean 与 Metric.over 保持原意义；
结果 min/max/count_defined/count_distinct/quantile、record/weighted_mean/correlation、
case/minimum/maximum/with_axes、命名派生轴定位和 window_reduce 移出首版。
结果只保留必要的 sum/mean/row_count 和小型同域组合；普通后处理由 pandas 拥有。
导出按 DSL §4.6.1 明确状态、坐标、精度与信息边界，不把缺少内部信息算成 pandas 的计算缺陷。
本轮只修订目标文档，没有修改语义实现或当前公开 API。

v0.22 增量：运行时统一使用 Domain；Session.members 只保留为来源身份选择入口。
observe 的 members 只约束贡献，by=Domain 明确完整输出，两者互不隐含。
结果复用身份依赖已保存身份轴的投影，不引入 Members 类型、结果成员转换或隐藏名单运输。
Entity/Relationship 仍拥有身份类型和显式读取路径；固定域不冻结新属性读取，也不证明来源覆盖。
语义命名空间的 DomainRef 仍是模型组织引用，与此运行时 Domain 无关。

v0.23 增量：常量不重复声明业务单位，独立参照量的单位归 Session.values 列定义；
时间构造归 Session，TimeAnchor 闭合区分精确时刻与左极限。Domain 的关系像输出目标身份，
逆像只筛选接收者有限身份域；字段分类像不升级为目标 Entity 身份，规则由 DSL §4.2.1 拥有，示例见 [关系像与有界逆像](../analysis/lazy-analysis-dsl-examples.md#4-关系像与有界逆像)。

v0.25 增量：观察默认信任声明的数据源，移除逐次完整性声明及其运输规则；
明确缺口的作用域和状态传播由 DSL §4.9.3 拥有，默认信任不构成已检查证据。
