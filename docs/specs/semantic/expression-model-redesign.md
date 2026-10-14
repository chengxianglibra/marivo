# 语义对象与分析表达式的统一设计

Date: 2026-10-14

Status: proposed v0.35；与 [Lazy Analysis DSL 提案](../analysis/lazy-analysis-dsl-redesign.md)
共同构成目标设计，尚未实施。当前契约仍由
[Semantic Object Model](semantic-object-model.md)、
[Loading, Validation and Introspection](loading-validation-introspection.md) 和
[Python Analysis Design](../analysis/python-analysis-design.md) 拥有。
本文只更新设计，不修改当前实现、Help、技能或已发布示例。
v0.35 补齐声明改写、validator 分层、单位拒绝与精确分位数合同；不新增期末规约或资源预算。
v0.34 将时间样本规约与有序 fold 分名，并同步比较/份额、窗口、来源类型准入和执行披露的跨层边界。
v0.33 统一所有阶段的零分母政策：默认 null，显式 error；参数只控制当前算子。
拟替代的已提交合同、旧模型声明的拒绝边界和实施前置授权见附录 A。
v0.32 明确：execute 返回终端 Artifact；所有 DSL 组合在 Query 上完成，Artifact 不携带语义对象/图或内部组件。
模型加载合同独立；分析产物可保存实际 Finding/Evidence 输出，但不承载计算依赖。
执行分工继续为 Ibis 源端计算、pandas 统一本地执行、PyArrow 存储/读取。
v0.30 采用 nullable value；普通缺失统一为 NA，非法计算和实际覆盖检查失败按错误处理。
sum 忽略 NA，空输入与全 NA 都为 0；移除 empty 选项和逐格四态/原因传播。

## 1. 从两种使用场景确定最小模型

语义建模者需要保存一份有业务身份、可独立加载的定义；分析者需要在本次问题中组合一个临时量。
两者都在定义“如何计算”，差别在声明和收录，不应使用不同的计算语言。
Measure 是带业务声明的数值行表达式，不是行表达式之外的第二套计算对象；
NumericRowExpr 只描述类型约束。MetricSamples 也只是 per 与规约之间的参数绑定，不拥有身份、执行或保存生命周期。
Measure 与 Metric 也共享表达式原则，但它们的样本单位不同：
Measure 描述每条贡献记录上的业务量，Metric 描述在观察上下文中如何规约、组合这些量。

| 层或对象 | 唯一责任 |
| --- | --- |
| Entity / Relationship / Field | 贡献根、身份、字段种类、版本和合法读取路径 |
| 行表达式 / Measure | 同一贡献记录上的计算；Measure 声明单位和业务可加范围 |
| Metric | 来源规约、指标算术、内部样本规约及各算子的政策 |
| @ms.measure / @ms.metric | 声明业务意义或稳定名称；将受限表达式体捕获为不可变定义 |
| Population | 分析总体的字面量或来源/Query 程序，承载类型化业务对象或分析单元键 |
| 模型 loader / Catalog | 命名声明收录、依赖解析、可独立持久加载资格和发现 |
| Session.observe / Query | 本次期间、分组、共同条件、明确主体及实际执行绑定 |
| Artifact | 不可变分析输出及实际 Finding/Evidence；展示、阅读和 to_pandas，不是表达式输入 |
| Ibis | 已绑定来源关系上的物理计算与合格后端 lowering |

Population 是分析总体，与语义声明所属的业务 Domain 分开；业务命名空间仍使用 domain= 与 ms.ref.domain(...)。
v0.29 只同步此命名及 population()/in_population(...)，不改变依赖、绑定或求值语义。

普通分析只需 `表达式 → observe → execute`。声明后的对象仍能使用相同算子；
不需要 RuntimeMetric、临时注册、as_metric 或第二套表达式工厂。
计算表达式不可变，构造、声明、show/contract 都不读取业务来源。
装饰器体可在受限构造阶段展开为表达式图；这不是查询执行，不保存 Python callable。

不可变域可以成为 Metric 的显式依赖，包括明确字面量名单和 Query 的坐标程序。
必须捕获完整定义与键、版本、来源读取和已有 Session 归属；不捕获 Artifact 或活动 Session 对象。
活动连接、可变外部容器、任意 Python 回调不属于合法依赖。
“在 runtime 创建”不构成禁止理由；可持久化检查在模型收录边界进行。

## 2. 同一构造、装饰与命名方式

### 2.1 用接收者明确输入阶段

| 表达式 | 样本单位与结果 |
| --- | --- |
| Amount - Cost | 同一贡献根上的行表达式 |
| Amount.sum(time=PaidAt) | 每条贡献记录上的金额 → Metric |
| Orders.count(time=PaidAt) | 完整贡献行 → Metric |
| CustomerId.count_distinct(time=PaidAt) | 非 NA 客户字段值去重 → Metric |
| revenue / order_count | 两个完成的 Metric → Metric |
| revenue.per(CustomerId).mean() | 每个实际出现客户的收入 → Metric |
| revenue.per(cohort).mean() | 指定完整名单中每个样本的收入 → Metric |
| query["revenue"].mean() | 当前 Query 格子 → 结果规约请求，由 Query.reduce 消费 |

Field 保留精确种类；数值编码 Dimension 只能比较、分组或计数，不能因 dtype 是整数就求金额之和。
Measure 及数值行表达式提供受限算术和以下来源规约。下面是签名说明，支持类型不扩展顶层 Help；
NumericRowExpr 包含 Measure，NumericLiteral 是有限 int/float/Decimal，排除 bool、NaN 和无穷。
TimeField 是已解析的时间字段；对象依赖捕获精确版本，不按同名物理列猜测。
TimeField.via(path) 保持 TimeField kind，并捕获从贡献根到时间字段 Entity 的非空、有序、
按声明方向连续且单值的关系路径；将该对象传入 time= 保留跨 Entity 时间绑定能力，不另设 time_via。
原生时间字段直接传入 time=，不写空路径。绑定检查贡献根、完整键、角色、版本和路径端点，
不选择最短路径，也不把字段读取路径当作观察主体的归属路径。

```python
class NumericRowExpr:
    def sum(self, *, time: TimeField | None = None,
            nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def mean(self, *, time: TimeField | None = None,
             nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def min(self, *, time: TimeField | None = None,
            nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def max(self, *, time: TimeField | None = None,
            nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def median(self, *, time: TimeField | None = None,
               nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def quantile(self, q: float, *, time: TimeField | None = None,
                 nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def weighted_mean(self, *, weight: NumericRowExpr,
                      time: TimeField | None = None,
                      nulls: Literal["ignore", "error"] = "ignore",
                      zero_denominator: Literal["null", "error"] = "null") -> Metric: ...
    def divide(self, other: NumericRowExpr | NumericLiteral, *,
               zero_denominator: Literal["null", "error"] = "null") -> NumericRowExpr: ...


class Field:
    def count(self, *, time: TimeField | None = None) -> Metric: ...
    def count_distinct(self, *, time: TimeField | None = None) -> Metric: ...


class Entity:
    def count(self, *, time: TimeField | None = None) -> Metric: ...
```

数值行表达式也可 count/count_distinct，规则与 Field 相同。Entity.count 数完整贡献行；
Field.count 数非 NA 值；count_distinct 数不同非 NA 值，空输入均为 0、单位为 1，
不接受 nulls/unit 覆盖。quantile 的 q 必须有限且 0 <= q <= 1；采用精确样本的线性插值规则，
端点分别等于同一有效样本的 min/max，不必为端点改换调用形状；不隐式降为近似算法。
对排序后的有效样本 x[0]…x[n−1]，令 h=(n−1)q、i=floor(h)、j=min(i+1,n−1)，
结果为 x[i]+(h−i)(x[j]−x[i])；n=1 返回唯一值，median 使用相同规则的 q=0.5。
这是来源和内部样本 quantile 的共同统计定义；物理累加、插值精度和最终舍入按已准入方法合同，
不以整数输入强制将插值结果取整，也不新增 interpolation 参数。
min/max/mean/median/quantile 的空或全 NA 输入固定为 NA，不提供补零参数。
sum 的可加许可由 §2.5 约束；空输入与全 NA 统一为 0，规则见 §3.4。
构造可保留尚未绑定的固定轴要求，例如状态金额必须固定时间轴；observe 按完整表达式检查
这些要求是否已由内部样本或合法输出绑定满足，不能在来源 sum 构造时提前丢弃或忽略它们。

```python
revenue = Amount.sum(time=PaidAt).where(Status == "paid")
order_count = Orders.count(time=PaidAt).where(Status == "paid")
aov = revenue / order_count
```

来源条件用规约后的 Metric.where 绑定明确的贡献作用域，不新增一套行表筛选 DSL。
数值行表达式的类型化比较产生 FieldPredicate，例如 margin > 0，沿同一贡献根在规约前求值；
谓词不改变被筛选业务量的单位或可加权限。
where 创建新定义，仅用于单一来源规约或同一共同配对作用域；对复合指标应先改具体子指标再组合。
不能把复合式的 where 猜成“过滤所有叶子”，也不提供 on 或表达式树位置参数。

表达式使用已构造对象或从 Catalog 解析的对象；裸 Ref 是加载/查找输入，不支持运算符或方法转发。
模型模块直接组合已声明对象，先声明被依赖对象；本版不增加隐式前向解析环境。
导入顺序不能替代循环检查；loader 冻结完整依赖版本，缺失或错误 kind 必须给出具体位置。

### 2.2 一种除法与明确配对

```python
class Metric:
    def divide(self, other: Metric | NumericLiteral, *,
               zero_denominator: Literal["null", "error"] = "null") -> Metric: ...
    def where(self, predicate: FieldPredicate | PopulationPredicate) -> Metric: ...
    def per(self, samples: Field | tuple[Field, ...] | Population) -> MetricSamples: ...
```

Metric 支持有限数值常量、正负号和 +、-、*、/；每次组合直接返回 Metric。
`a / b`、`a.divide(b)` 与 `a.divide(b, zero_denominator="null")` 规范化为相同 Divide 节点、
相同 numerator/denominator 依赖和政策。显式 zero_denominator="error" 只改变该次除法。
没有单独的 ratio 工厂或更高等级的比率类型。行表达式、Metric 和结果 Column 的写法及默认一致，
但保留各自阶段，不能隐式混算；唯一政策表见 §3.4。

```python
net_revenue = revenue - refunds - fees
adjusted_revenue = net_revenue * 0.9
strict_aov = revenue.divide(order_count, zero_denominator="error")
weighted_price = UnitPrice.weighted_mean(weight=Quantity, time=PaidAt)
```

weighted_mean 是必要的独立算子：它定义共同有效配对，不等价于两个独立规约相除。
value 与 weight 必须是同一贡献根上单值、数值的行表达式；两者都不要求其完成值具有业务可加性。
weight= 明确选择归一化权重，权重和是算子内部的数学组件，不自动成为可加业务量或份额支持证明。
不能把两个来源的逐行值自动 join 成共同样本。Metric 内部样本上的配对见 §3.5，结果配对统计交 pandas。

### 2.3 装饰器只负责声明

```python
def metric(*, domain: Ref[DomainKind] | None = None,
           ai_context: AiContextValue | None = None
           ) -> Callable[[Callable[[], Metric]], Metric]: ...


@ms.metric(ai_context=ms.ai_context(business_definition="Paid order revenue"))
def revenue():
    return Amount.sum(time=PaidAt).where(Status == "paid")


@ms.metric()
def order_count():
    return Orders.count(time=PaidAt).where(Status == "paid")


@ms.metric()
def average_order_value():
    return revenue / order_count
```

与 §2.1 的临时定义比较，函数体就是同一个计算表达式。装饰后的名字绑定 Metric 对象，
后续直接写 revenue / order_count，不调用 revenue()。类不是必要的业务容器，不增加类装饰器变体。
Metric 装饰器从函数名声明局部名称，可指定 domain 和 ai_context；不接收 name、entities、time、fold、additivity、unit、
nulls 或 zero_denominator；来源/单位/时间从表达式依赖取得，数值政策在发生运算处声明。
它也不按最终 float dtype 或公式形状推断新的可加权限。

声明体为零参数，可含首段 docstring、无重绑定的局部表达式和唯一最终 return。
只接受本提案的闭合表达式、已声明依赖及不可变字面量/域；不接受导入、循环、分支语句、
执行查询、状态修改、任意函数调用或活动外部对象。限制在静态验证和受控图构造中执行，
不先运行任意 Python 再验证返回类型。跨模块辅助声明使用已经构造的对象。
返回 Ibis scalar、结果 Column、Query 或 Python scalar 都是阶段错误。
普通 Python 可以在声明体外组合参数和表达式，再让声明体返回该不可变表达式；
收录仍检查同一依赖闭包，不保存辅助函数，也不因经辅助函数构造就扩大权限。
人类说明复用现有 ms.ai_context，不新增 description 或从函数 docstring 推导另一份业务说明。

同一装饰器可在模型模块或分析脚本中使用；不要求 loader、不登记、不读取来源。
无须稳定业务名的临时 Metric 直接使用表达式，observe 字典键已经能命名输出列。
声明不会保证依赖能被模型收录，也不保证后端或本次计划中的合并路线已经获准。

validator 复用现有 AST 与表达式绑定机制，按已解析符号和接收者阶段准入；
NumericRowExpr.sum、Metric.where/per 等不能仅因方法名在名单中就对任意对象放行。
decorator 参数中的 ms.ref.domain(...) 不属于声明函数体；声明体消费已构造依赖，不开放任意工厂或函数调用。
Entity/Source、Dimension/TimeDimension 保留原 owner 的受限 Ibis 作者能力；
ms.bind 仅在原合同允许的 Ibis 字段声明体内保留，继续验证字段与 Entity alias 的归属。
新 Measure/Metric 体不接受 Ibis Table 参数、Ibis 返回值或 ms.bind；不为统一这两层而复制完整 Ibis 行语言。

### 2.4 命名与模型收录是不同边界

| 名称 | 作用 |
| --- | --- |
| 声明函数名，如 gross_margin | 装饰时捕获局部声明名；Measure/Metric 采用同一规则 |
| 模型业务身份，如 sales.gross_margin | 收录时用 Metric 的 domain 与局部声明名限定；Measure 用所属 Entity 与局部声明名限定 |
| 后续 Python 变量/导出别名 | 只引用对象；赋值或导入别名不重命名声明 |
| observe 的键，如 trial_margin | 此次结果列名；不注册或重命名指标 |

```python
@ms.metric()
def gross_margin():
    return (Amount - Cost).sum(time=PaidAt)


margin_alias = gross_margin
trial = (Amount - Cost).sum(time=PaidAt)
```

在 sales 为唯一默认 domain 的模型中收录 gross_margin，得到 sales.gross_margin；
margin_alias 不产生第二个名称。runtime 中同一声明也能直接 observe，不需要先设置 domain 或加载模型。
trial 是匿名表达式；为它选 observe 输出标签仍足够。改声明函数名会改变声明局部身份，
但不会改变同一表达式的计算指纹；改后续别名不会改变两者。

函数名直接采用 __name__，不从 __qualname__、Python 模块路径、文件夹或依赖根推导。
局部名复用现有语义路径段规则 `[a-z][a-z0-9_]*`，如 gross_margin；不做自动小写、snake_case 转换、
字符替换或截断。GrossMargin、_gross_margin、含非 ASCII 字符的函数名在装饰阶段给结构化错误，
列明实际名称、约束、声明位置及改名修复；禁止使用 name 字符串覆盖或静默产生另一个身份。
只校验名称与表达式，不因函数声明处于分析脚本而要求模型命名空间。

Metric 的 domain= 复用现有 Ref[DomainKind]，例如 domain=ms.ref.domain("sales")；
它只声明所属命名空间，不绑定 Session 或执行环境。收录时优先使用显式 domain，
否则使用模型唯一已声明默认 domain；缺失/歧义或显式域不存在则拒绝收录，列出真实可选域。
不从多来源依赖中挑选一个 domain，也不从文件所在目录猜域。装饰器不依赖活动 loader，
未收录声明仅披露局部名及可选 domain，不冒充已发布的 Catalog ref。
该限定过程生成收录版本，不能回写或改名原表达式；模型内一个完整名称只能对应一个声明。

不保留 Metric.named。声明身份、声明元数据版本、计算内容指纹分别记录；
Metric 声明只附加元数据，不改变计算指纹、政策、依赖和已有 Session 归属。
算术、where、per 规约产生匿名新表达式；旧声明及版本仅作为依赖来源，不能冒充原声明。
若要发布为另一个业务指标，显式写新声明；原对象保持不变。
Measure 仅命名时也不改变计算指纹；显式新增或改变的单位/可加合同进入语义指纹，
可推导且相同的重复断言规范化后不产生第二计算定义。稳定名称/ai_context 仍属独立元数据，
声明断言与推导事实的来源可以独立披露，不把说明文字当作计算权限。

模型 loader 只收录导出的 Measure/Metric 声明，先限定声明名称，再解析并检查完整依赖闭包，
成功后在 Catalog 发布带精确 Ref 的版本；原构造值不被改写。
匿名中间式可以是依赖，不自动成为公共模型对象。多个 Python 别名指向同一声明只收录一次；
不同声明争用同一完整名称必须失败，不能按导入顺序覆盖。

| 显式依赖 | 构造/合法观察 | 模型收录 |
| --- | --- | --- |
| 已声明 Field/Entity/Measure/Metric | 捕获精确种类、角色和定义版本 | 完整声明闭包可解析；不保存活动 Catalog |
| 不可变字面量域 | 保存类型化轴和完整键，包括合法空域 | 键可编码、轴可独立解析即可 |
| 来源域程序 | 保存 lazy 程序、选择期间/属性时点和已有归属 | 闭包可独立解析且无执行 Session 绑定；收录不能执行或解除归属 |
| 以 Artifact / Artifact ID 充当计算依赖 | 构造即拒绝；普通字面量字符串不解析为产物引用 | 不提供模型提升或反向恢复入口 |
| 活动 Session/连接、可变容器或任意回调 | 不是合法表达式依赖 | 不能 pickle 绕过依赖检查 |

模型加载保持 source-I/O free；可以检查声明及本地引用元数据，不能执行域来证明完整性。
本版没有模型 Artifact 提升/复制入口；失败应列出依赖路径、已有归属和缺失的持久解析条件，
保留临时表达式与用户数据，不自动复制名单、重选动态域或写回模型。
模型可加载与 Artifact 可保存/读取是两个独立合同。

### 2.5 Measure 采用同样的表达式与声明分离

Measure 的新设计也需要统一，但不能把“逐行算得出”误认为“业务允许求和”。
已有 measure_column 是物理叶子的来源、单位和权限声明；保留该职责与状态时间角色/采样间隔。
status_time_fold 不再属于叶子默认，改由 §3.2 的显式 per 规约表达；旧参数按附录 A.1 拒绝。
派生 Measure 改用同一套类型化行表达式，装饰器声明新业务量及其授权，不再接收 Ibis Table 参数。

```python
def measure(*, unit: str | None = None,
            additivity: AdditivityPolicy | None = None, ai_context: AiContextValue | None = None
            ) -> Callable[[Callable[[], NumericRowExpr]], Measure]: ...


@ms.measure(additivity=ms.additive_all())
def order_amount():
    return Quantity * UnitPrice


@ms.measure(additivity=ms.additive_all())
def order_cost():
    return Quantity * UnitCost


margin = order_amount - order_cost
margin_total = margin.sum(time=PaidAt)


@ms.measure()
def order_margin():
    return order_amount - order_cost
```

Measure 声明体与 Metric 声明体遵守同一纯表达式限制，返回阶段分别是行表达式与 Metric。
省略 unit/additivity（或 None）表示继承表达式已有事实，不表示清空单位或授予 additive_all。
已知单位自动按封闭代数推导，已有许可按行表达式规则继承；无许可的乘积在仅命名后仍无求和许可。
因此 order_margin 只加稳定名称，无须重复 CNY 和可加声明。order_amount 的新可加授权仍须显式给出。
未知单位保持未知；只有显式 unit 才承担新业务断言，不把 None 当作无量纲或一个清除旧合同的入口。
Quantity、UnitPrice、UnitCost 必须有同一个明确贡献根；单值路径、单位关系和行粒度可验证。
来源根从表达式唯一确定，不重复接收 entity 参数，也不选择多根中的第一项。
多值路径、跨事实行算术和 Metric 混入行表达式必须拒绝；跨事实先各自规约，再组合 Metric。

| 行计算 | 求和许可 |
| --- | --- |
| 已声明 Measure | 使用该定义的业务可加范围 |
| 同单位加减、无量纲常数缩放 | 继承输入许可交集；不能扩大到新的轴或时间 |
| 两个行量相乘或相除 | 可以形成数值表达式，但不自动取得可加许可 |
| 数值编码 Dimension、改标签、过滤、相消、乘零 | 不获得新数值/可加权限 |
| 显式新 Measure 声明 | 为新业务量声明可加合同，并验证来源、行粒度、单位和时间约束 |

声明新的 order_amount 是业务授权，并非系统证明“所有乘积都可加”；原 UnitPrice 仍不可加。
新声明的授权来源、表达式依赖和既有事实都要披露。不能修改原 Measure 的权限，也不能用命名
伪造旧声明版本。权限政策只引用存在的语义轴；未知轴、与已知单位不相容或改变物理时间角色均拒绝。
新权限不能抹去状态采样轴或必要时间绑定；同根、单位相容只证明表达式可绑定，不证明新增业务授权正确。
若表达式单位可推导，unit 必须相容；未知单位可由声明承担明确业务断言并保留来源，不能冒称已验证推导。
unit 标签不换算金额、汇率或比例。

Measure 从函数名捕获局部声明名，规则与 Metric 相同；所属 Entity 从行表达式唯一取得。
完整字段路径为 Entity 路径加局部声明名，例如 `sales.orders.order_amount`；不接收 name/domain/entity
重复指定或更换所属关系。Metric 的命名空间限定由 §2.4 拥有。物理叶子构造器继续在所属 Entity 下解析字段名。
runtime 首次引入同样的新业务量也使用同样的继承与显式授权规则：

```python
@ms.measure(additivity=ms.additive_all())
def trial_order_amount():
    return quantity * unit_price


trial_revenue = trial_order_amount.sum(time=paid_at)
```

trial_order_amount 也是一个局部命名声明；在分析脚本中使用它不登记，也不自动发布到模型。
私有声明可以作为命名指标的可持久依赖，只有模型导出的声明才作为公共对象收录。
无需新授权或业务声明的 order_amount - order_cost 直接规约即可；匿名 Metric 也直接使用表达式。
不增加 measure(expression=...) 工厂或另一套匿名声明模式。
两种装饰器均不接受聚合/除法政策；例如行级 divide 的政策归该行运算，sum 的政策归 sum。

## 3. 保留阶段，统一计算规则

### 3.1 行、指标和结果的边界

统一表达式不等于把所有东西变成原生 Ibis Column。行表达式、Metric 和 Query Column
各自携带样本单位、身份、数值许可和阶段，不能隐式转换或混算。
Field 谓词仍是有限的比较、状态/集合测试及布尔组合；本版数值行表达式仅覆盖 §2 的算术和规约，
不复制完整 Ibis API，也不增加任意 UDF、条件/窗口逃生入口。
现有受限 Ibis authoring 是当前实现合同，不能继续作为目标 Metric 的第二计算语言；
目标不支持的复杂行计算应先成为来源声明中的合法字段，不能塞入装饰器绕过图验证。

Metric 多来源根分别规约后按完整目标键组合；不能将原始事实 join 成宽表再聚合。
同一依赖出现两次仍保留两次逻辑使用，不按 Python 对象去重运算语义。
定义相同不保证多次来源读取相同；读取保证由执行文档拥有。
多个 datasource 属于目标范围，实际执行仍按后端、类型和交接路线准入；语义可绑定不等于已有物理资格。
来源类型保真及存储/导出的责任边界由
[执行 §1.5.1](../analysis/lazy-analysis-dsl-execution.md#151-来源类型准入与无损交接)拥有，不从 Ibis 可编译推导精度保证。

### 3.2 时间角色和时间样本规约

time 在来源规约上选择业务时间角色；省略时只继承唯一已有声明，没有声明不产生时间角色，
多个候选必须显式选择。派生 Metric 保留每个子指标的时间角色，不提供全局覆盖参数。
采样状态的时间轴、间隔和时间可加限制来自语义声明，不由普通 timestamp 或 per 自动推断。

时间样本规约使用与其它内部样本相同的 per + reducer 计算语言：

```python
@ms.metric(domain=ms.ref.domain("network"))
def bandwidth_p95():
    return Bandwidth.sum(time=SampleAt).per(SampleAt).quantile(0.95)
```

SampleAt 是已声明的五分钟状态采样轴。per 将此轴绑定为内部键，使 sum 在每个时点先按空间求和；
quantile 再消费观察期间内的总量样本。时间轴的权限在内部键处固定，
外层 quantile 不授予跨时点求和权限。没有 per 消费状态轴的中间 sum，不能直接完成一个跨多个时点的观察。
这项计算统一称为“时间样本规约”；不再提供隐藏在 aggregate 或装饰器上的 fold 参数。
fold 仅用于[序列代数的有序状态折叠](../analysis/algebra-sequences.md#41-有序折叠)，
不能把 per/reducer 解释成依赖前序状态的转移或序列回放。

设备 A=[10,0]、B=[0,10]：各设备 P95 为 9.5，总体同时点 [10,10] 的 P95 为 10，不是 19。
Query 的空间上卷需要按采样时点对齐的时间规约前信息，只有完成 P95 或失去时点的分布不够。
采样轴按声明和本次期间确定应有样本，明确缺测按覆盖检查拒绝执行，不改成“只对出现时点取 P95”或补零。
只提供真实观测点、没有采样覆盖声明的事件时间，可以定义事件键上的统计，但不能冒充完整状态采样规约。

期间和窗口由 observe 拥有；Metric 没有 during/route 默认。
普通来源规约可以省略 during，表示不追加时间过滤，保留业务时间角色和固有条件；
时间样本规约或日历输出等真正依赖期间的情形必须显式传入。日期谓词不能替代该绑定。
域自己的选择期间、属性时点不被外层 during/where 重写；冲突应失败，不暗取交集或最近覆盖。

### 3.3 指标依赖与比率

```python
error_rate = error_count / log_count
```

两项完成后再执行统一 Divide；依赖保留定义版本、固有过滤、单位、时间和域依赖。
分子分母角色可供解释和合法重算，不能自动成为观察路由或通用树手术入口。
不同条件应显式形成子指标；不同观察窗口应分别 observe，再比较或对齐。

#### 3.3.1 显式角色与通用依赖图

通用算术从各依赖的合法充分状态恢复目标值，再按原式组合。
两个 Metric 的相容加减仅在共同合法贡献分区上继承许可交集；无量纲常数缩放保留原许可。
Metric 与非零常数相加/相减则不继承可加性：每个输出格子加一次常数不同于每条贡献加一次常数。
例如地区收入为 100/300，metric = revenue + 10 的地区完成值为 110/310，和为 420，
而总体原式是 400+10=410。组件充分时 rollup 可重算 410；不能直接对完成值求和或据此 share。
行级 (Amount + 10).sum() 则在每条记录加 10，可以按其合法行贡献合并；两种阶段不可互换。
加减字面量 0 保留原许可和必要检查；0-m 等价于取负，不扩大许可。
变量乘除、相消或乘零也不能扩大许可或消除必要错误。
Divide 完成值通常不可加，但组件充分时可以重算总体比率；缺状态时不能靠最终值相等放行。

相同且已知的单位可约去为 1，未知单位不是无量纲。加减的裸数值常量在该使用位置采用已有量单位，
乘除的裸常量为无量纲系数，不修改常量或原定义。Metric 不接收结果 Column/values 参照。
独立参照列由 [DSL §4.1.2](../analysis/lazy-analysis-dsl-redesign.md#412-常量与独立参照列的单位) 拥有。

#### 3.3.2 weighted mean 的配对与状态

共同非 NA 配对条件同时用于乘积和、权重和及支持计数；nulls="error" 必须在删行前检查所选配对。
value=[10,NA]、weight=[1,9] 得 10；分母独立使用全部权重会得 1，是另一计算。
分组均值 10/30、权重 1/9，合并配对状态得 28，不是等权均值 20。
无有效配对在两种政策下均返回 NA；有有效配对但权重和为零时，按 §3.4 默认返回 NA，显式 error 才报错。
weight 只需是该样本上的合法数值，不要求它已获业务 sum 授权。例如不可加的评分 0.2/0.8
可以明确用作值 10/30 的权重，结果为 26；这不授权发布“评分总额”。本算子允许有符号权重，
因此不承诺结果位于值域内；若消费者要求非负或正总权重，仍需检查。非有限值按数值合同拒绝。
本次计划中的配对合并需要乘积和、权重和、支持及必要状态，不能只保存完成均值；
内部样本配对还须满足 §3.5 的子指标重算条件，不能仅套用来源配对摘要。

#### 3.3.3 drill、贡献和归因的边界

ERROR/总日志两组为 1/1、9/99，总体率为 0.1；子率不能相加。
完整不交分区及非零总体分母成立时，N_i/总 D 为 0.01、0.09，才可解释总体率构成。
share 仍要求可加贡献和共同实际输入，不把 Divide 角色当成支持证明。
变化归因的分解方法不纳入本轮目标；Artifact 不拥有任何 DSL 能力。

### 3.4 样本政策的归属

数值只有类型化值与 NA；NA 是逻辑缺失，来源 SQL 用 NULL、本地 pandas 用相应 dtype 的缺失表示，Arrow validity 只属于存储/读取布局。
不保存或传播逐格 Defined/Null/Undefined/Unknown 与 reason；需要影响计算的事实归算子政策或必要检查。
政策只归实际算子，不归声明、名字或模型/临时代码位置；构造时解析默认并与规则版本一起保留在定义节点中。
本节是零分母政策的唯一规范性定义：所有支持该选择的算子使用
zero_denominator: Literal["null", "error"] = "null"，没有其它策略或阶段特例。

| 算子 | NA 输入 | 无有效值 | 零分母 |
| --- | --- | --- | --- |
| 来源 / 内部样本 sum | 默认 ignore，可 error | 空输入与全 NA 都为 0；没有 empty 参数 | 不适用 |
| mean/min/max/median/quantile | 默认 ignore，可 error | NA | 不适用 |
| Entity count | 计完整贡献行，不依赖量值 | 空输入为 0 | 不适用 |
| Field / 数值样本 count、count_distinct | 不计 NA | 0 | 不适用 |
| 来源 / 内部样本 weighted_mean | 默认共同非 NA 配对，可 error | 两种零分母政策下均为 NA | 有有效配对但权重和为零：默认 null，显式 error 报错 |
| 行、Metric、Column 的 /、divide | 任一输入 NA 则结果 NA | 无独立空集政策 | 共同非 NA 输入中分母为零：默认 null，显式 error 报错 |
| Query.share | 份额的任一输入 NA 则结果 NA | 参照按原量规则完成 | 参数只控制新生成的份额；默认 null，显式 error 报错 |
| Query.compare 的 relative | 任一端点 NA 则结果 NA | 缺侧由独立 presence 表示 | 参数只控制新生成的相对变化；默认 null，显式 error 报错 |

sum 不提供区分“没有记录”和“记录值全缺失”的结果或续算能力，不为该目的保存原样本数、
NA 数或原因；按原公式上卷只需合格的和。mean 仍需有效值数，Entity count 是独立请求的量。
nulls="error" 是对所选输入的显式检查，在忽略 NA 之前完成；不是新的 Cell 状态。
默认采用可用值统计，不自动解释为完整 Population 的总体估计；固定名单人数与有效值数分别由各自算子决定。

共同非 NA 输入中的 0/0 和非零数/0 默认均产生普通 NA；不产生 0、无穷或特殊缺失状态。
显式 zero_denominator="error" 才使本次 execute 以结构化计算错误失败。任一输入已为 NA 时，
包括 NA/0，两种政策都传播 NA；缺值不被重新解释为除零错误。
生成的普通 NA 可被后续 mean/sum 等按各自规则忽略，不保留零分母原因。
业务要求每个比率都可定义时，应在对应算子明确选择 error，不能事后从 NA 推回原因。

`/` 等价于省略参数的 divide；普通 mean 的空样本语义不增加 zero_denominator 参数。
share、compare 或 weighted_mean 的参数不覆盖输入 Metric 内部的政策；compare 的参数只作用于
relative，不改变 absolute 的公式。输入子式的 error 失败仍使整个 execute 失败，不发布半份比较结果。
Session、observe、execute 与装饰器均不接受零分母全局覆盖；声明/模型收录保留节点政策，
不同政策属于不同计算定义。源端与本地执行保持同一算子政策和失败边界；数值精度遵守
[已准入方法合同](../analysis/lazy-analysis-dsl-execution.md#14-数学定律的实现边界)，不承诺跨后端逐位相同。

显式 error 的检查不能被乘零、后置筛选或丢列消去。读取失败、溢出、非法值和实际日历/映射/请求域覆盖检查失败均报错，
不转成 NA；普通观察按实际读取的数据求值，首版不提供外部业务缺口声明、消费或完整性审计。
输入缺失表示按已绑定 dtype 规范化；数值计算不借 inf 或运算产生的 NaN 表示成功。

```python
revenue = Amount.sum(time=PaidAt)
strict_cost = Cost.sum(time=PaidAt, nulls="error")
margin_rate = (revenue - strict_cost) / revenue
strict_margin_rate = (revenue - strict_cost).divide(revenue, zero_denominator="error")
```

来源 SQL sum 必须显式完成空/全 NULL 为 0，与 pandas 的 skipna 求和一致；
共同配对、有限性和除零检查仍由 Marivo 的算子合同统一，不照搬各引擎不同的异常行为。
结果 reduce 的 sum/mean/row_count 采用 [DSL §4.4](../analysis/lazy-analysis-dsl-redesign.md#44-最小规约与有效样本)；
Column.divide、Query.share 和 Query.compare 暴露本节同名参数；结果层无需把公式搬回 Metric 才能选择政策。
默认 compare 可以同时输出有效 absolute 与 NA relative；只需差额仍可使用 align/select。
Query 组合沿用已解析政策；Artifact 只保存输出，不保存或恢复旧政策/定义。
旧四态/图快照格式在一次切换时拒绝，不迁移或按新默认猜测旧定义。

### 3.5 内部域规约与 Query 格子统计

per 明确“先为谁计算一遍”，随后 reducer 明确“怎样汇总这些样本”。
MetricSamples 只是尚未完成的表达式，不能直接 observe、execute、注册为 Metric 或返回给 @metric。
它复用来源数值规约的名称及 nulls/zero_denominator 值域，但不接受 time 参数：
子指标已经拥有时间角色。sum 仍检查样本轴上的合法可加性；mean/quantile/weighted_mean 等不以此授予业务求和权限。

```python
class MetricSamples:
    def sum(self, *, nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def mean(self, *, nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def quantile(self, q: float, *,
                 nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def count(self) -> Metric: ...
    def count_distinct(self) -> Metric: ...
    def weighted_mean(self, *, weight: Metric,
                      nulls: Literal["ignore", "error"] = "ignore",
                      zero_denominator: Literal["null", "error"] = "null") -> Metric: ...
```

同组 min/max/median 与 mean 同签名；不加 first/last 或任意回调变体。
本版不提供从任意观察期间自动选择期末状态的 closing 规约，也不按主体取最后非空值或 forward fill。
显式时点的已有快照读取仍遵守其声明合同；它不授权从期间末端猜快照、补缺测或用 end 减 tick。
per(Field) 是 per((Field,)) 的单轴形态；字段元组须非空。
关键是先确定一个外部输出格子 k，再确定该格子内的样本 J_k；不是先构造一份全局客户值再分配给地区。
Field 样本从 k 内各相关贡献根的完整键像并集推导，不填未出现类别；
空值规约不删除这些键。各组件在每个内部键按自身规约规则完成，再执行原式。

per(Population) **显式选择全部内部样本**：在每个 k 内，对域的每个相容键 j 绑定子指标。
域外贡献不属于请求；不要求域覆盖全局来源，也不要求重复 where(P)。
域自己的键选择程序/期间保持不变，外部上下文仅限制在这些键上计算哪些贡献。
这项语义取代 v0.26 over 的全局覆盖要求。

```python
cohort_mean = revenue.per(cohort).mean()
observed_mean = revenue.per(CustomerId).mean()
cohort_ratio = revenue.where(mv.in_population(cohort)) / revenue
```

cohort={A,B}，全来源 A=100、C=300，收入 sum 空/全 NA 为零：cohort_mean=(100+0)/2=50，
observed_mean=(100+300)/2=200；只在 cohort 中出现的客户均值为 100。cohort_ratio 为 1/4。
per 在每个样本完成整个子式，不会把其中分母意外留在全局 400；人群/全体比率应使用上面的分支公式。

**内部展开键是 (k,j)，其到外域的映射是 (k,j)→k；不要求裸 j→k。**
所以同一客户跨地区、同一采样时点跨设备都合法。对 East/West 的完整 cohort 各求一次客户值；
若只要各地区实际客户，使用 per(CustomerId)。对每台设备也分别形成时点样本，P95 无需时点唯一归属设备。
共享的同一轴绑定必须取相等键；不能制造同一客户轴同时为 A 和 B 的矛盾坐标。
per 不额外创建外部格子，K 仍按 observe 规则从相关贡献或显式输出域确定；内部零活动样本只在已有 k 内保留。

Population 的准入按轴能否完整绑定判断，不因是多字段、身份×字段或多身份元组而一律拒绝。
每个 Field/Member 轴须有明确角色、版本和对各相关贡献根的合法单值绑定；不能隐式 fanout、
按同名猜关系、从一个样本权重重复一条贡献，或把域键存在当作来源覆盖证明。
零轴非空域 {()} 表示每个 k 内一次子求值，零键域表示没有内部样本；二者不混同子指标本身的空贡献。
首期不增加 CalendarAxis/CorrespondenceAxis 的内部窗口/端点绑定；这属于能力范围，
不是不可变 Population 的一般类型禁令。此类统计先显式 observe/compare，再在 Query 上 reduce；执行后交 pandas，
不能拿日期/端点标签冒充 Field。状态 TimeField 的采样合同仍由 §3.2 拥有。
不能直接传 Query/Artifact/Column；Query 坐标先取合法 Population，保留来源与已有归属。
Artifact 没有 population；外部键由脚本以显式字面量和当前绑定构造新定义。

每个完整域样本先按子指标政策完成，再按外层规约政策处理；NA 不因样本键存在而补零，原输入的必要检查仍须完成。
Population 只给样本身份，不证明来源存在或完整。来源域保留原选择程序/时刻，字面量域保留明确完整键和归属。
配对变体先在同一内部样本域完成 value 与 weight，再用共同有效掩码；weight 必须是 Metric，
不能混入来源 Field。字段推导样本时，键域包含 value 和 weight 两者相关贡献键的并集；
显式 Population 则按该域逐键完成两者，不先删去某一侧无值的键再假称完整。
Query 格子统计用 reduce；Artifact 后处理用 pandas。前者随本次图求值，后者只消费导出数据。
Query 外部粗化时须先在共同内部键 j 上合法合并/恢复子指标，再完成外层规约。
不能对 PerMean 无条件套用 SourceMean 的 sum/count 摘要：East=A100、West=A300 与
East=A100、West=B300 有相同地区摘要 (100,1)/(300,1)，总体客户均额却为 400/200。
没有内部身份/子状态时无法区分；本次计划必须提供对该表达式充分的输入/中间信息，不能用完成值代替。
纯键数使用 s.query(population).reduce({"ids": mv.row_count()})，不把 per(...).count 当作名单基数。

外部 observe(by=Population) 仍是完整输出覆盖合同，缺少被消费贡献的输出键必须失败；
它不等于 per(Population) 的内部样本选择。数学区分见
[核心分析代数](../analysis/algebra-theory.md)。

## 4. 发现、类型与错误

Field/Entity/Measure/Metric 都有 bounded repr/show；定义 contract 披露阶段、贡献根/角色、
单位与授权来源、时间、节点政策、域依赖/归属、所需绑定和模型收录阻碍，不执行或枚举域。
NumericRowExpr、MetricSamples、图节点和类型别名留在所属能力下，不扩张顶层导航。
Metric.contributions 只披露能定位具体根的语义角色；Divide 的 N/D 是计算依赖，不自动构成主体路由。

Field.via/at 的路径与精确时刻继续由
[DSL §4.1](../analysis/lazy-analysis-dsl-redesign.md#41-session字段时间与路径) 和
[§4.1.1](../analysis/lazy-analysis-dsl-redesign.md#411-精确时刻与版本选择) 拥有。
单值路径不凭同名字段建立；时间默认在构造时冻结，不捕获活动 Session。

错误说明预期阶段、收到的定义与具体位置，并从真实状态给出修复：
行乘积缺求和权限应明确声明新 Measure；@metric 返回行值应显式规约；
@measure 返回 Metric 不得自动下沉；per 缺贡献到 (外部格子, 内部样本) 的绑定应补合法轴；模型依赖不可持久解析
应保留临时使用或重写依赖。不给自动转换、隐藏 join、任意回调或回源补状态的修复。
已知不相容单位的行/Metric 运算（如 CNY + USD）在构造时以 SemanticError 的具体子类拒绝，
列出双方单位和表达式位置，要求显式完成合法换算后重新组合；不隐式换汇或修改单位标签。
Query Column 的同类失败由 AnalysisError 拥有。未知单位保持未知，不假定无量纲；
确需后续绑定的信息按[检查时机](../analysis/lazy-analysis-dsl-execution.md#24-检查时机与能力披露)处理。
完整披露由 [执行与披露 §2](../analysis/lazy-analysis-dsl-execution.md#2-agent-读取与错误契约) 拥有。

## 5. 观察上下文是普通分析的唯一绑定入口

```python
daily = s.observe(
    {"revenue": revenue, "trial_aov": null_on_zero_aov},
    where=region == "APAC",
    during=week.by(mv.grain("day")),
).execute()
```

普通调用无需 Entity、身份域或注册。共同条件必须对全部相关来源组件完整绑定，
一项不能唯一解释就拒绝，不能只过滤部分组件。内部域自身的选择程序不被改写。
主体归属使用唯一声明路径，observe 不接 routes 补全、不选最短或最低成本路径。
普通观察默认信任来源，成功执行不证明业务数据完整；首版不声明或消费外部业务缺口，边界归 [DSL §4.9.3](../analysis/lazy-analysis-dsl-redesign.md#493-覆盖缺桶与执行检查)。
Query Column 不能进入 Metric；Artifact 不提供 Column，也不能通过 .expr 或其它导入入口回到 DSL。

## 6. 内部实现与一次切换

| owner | 负责 |
| --- | --- |
| semantic 表达式与声明 | 行/指标阶段、业务权限、稳定身份与闭合图构造 |
| loader / Catalog | 独立持久解析、依赖与名称检查、精确版本和发现 |
| 共同私有定义图 | 行计算、规约、Metric 组合、内部样本及规范化域依赖 |
| Ibis 来源 lowering | 合法 schema/关系上的计算、政策保持和实际后端资格；来源执行交付 pandas |
| analysis | 观察绑定、Query 组合、统一 pandas 本地执行与中间信息、终端 Artifact 产出 |
| Help / contract / errors | 静态导航 / 当前定义或状态 / 具体修复 |

复用现有图、compiler、发布和恢复链，不建第二 Runtime、虚拟共同关系或完整 Ibis AST 镜像。
共同定义图仅在进程中保留规范化计算、声明授权、依赖版本、节点政策与规则版本；不做分析结果图编码，
不 pickle callable/Ibis/Catalog。Population 采用依赖中立的字面量/来源或 Query 程序描述，
semantic 不导入 analysis 执行器，实际归属、读取边界和循环检查沿依赖保留。
来源无法下推的计算只在具有合格 pandas 内核时进入本地阶段；语义政策不因执行位置改变。
PyArrow 只负责终端产物存储/读取，DuckDB 只作为来源后端，不增加另一套表达式语言或本地选路。
完整执行合同见[执行 §1.5](../analysis/lazy-analysis-dsl-execution.md#15-单一执行路线与终端读写)。

### 6.1 首期节点集合与执行内信息需求

| 私有节点族 | 直接求值 | 本次计划内合并所需信息 |
| --- | --- | --- |
| Field/定义引用、有限数值常量 | 保留根、路径、单位和版本；常量不制造来源 | 保存精确绑定和规则 |
| Row Negate/Add/Subtract/Multiply/Divide | 同一贡献根上的逐行计算、局部除法政策 | 保存行定义不等于保存原行；需要实际输入或规约充分状态 |
| MeasureDeclaration | 名称/ai_context 与语义事实分开；可推导事实继承，只记录新增单位/权限断言 | 记录授权来源和版本，不修改依赖原合同 |
| SourceAggregate / EntityCount | 合法贡献单位上的精确规约 | sum/count/min/max/mean 等合格状态；distinct/quantile 另需精确信息 |
| PopulationDependency / ContributionFilter | 显式域绑定一个完整贡献作用域 | 类型、字面量/程序、匹配和必要贡献状态 |
| Metric Negate/Add/Subtract/Multiply/Divide | 完成各指标后逐坐标组合；统一 Divide 角色 | 各依赖在目标坐标的合法充分状态 |
| SourceWeightedMean | 同贡献域共同有效配对 | 配对乘积和、权重和、支持/覆盖；不能用独立权重和 |
| PerReduce / PerWeightedMean | 各外部 k 内先按 J_k 完成子指标，再规约/配对 | 按需计算展开键 (k,j) 及必要子状态，或已证明等价的充分信息；不能套用来源 mean 的摘要 |

声明名称是独立元数据，不再引入 SourceBody、Ratio 或旧 Over 与新节点并行的计算入口。
时间样本规约是 PerReduce 的受约束用法；无任意序列节点。
直接可求值与可按摘要合并是不同资格，不因同名 sum/mean 就默认支持。
表中的定义属于 Query，组件只服务本次计划，最后一次消费后释放；Artifact 不持有它们。
只有实际消费者需要且路线获准时才计算细网格/精确分布，不为未来未知续算默认保留；输出边界由
[执行 §1.2](../analysis/lazy-analysis-dsl-execution.md#12-默认保存信息) 拥有。

规范化、类型/单位/权限检查、lowering、必要检查、进程内组件与输出数据往返均需独立验证；不新增结果图 codec。
未知节点/规则版本必须定位失败，不降级为任意 Python、后端默认或旧 DSL 别名。

## 7. 实施与可核验条件

本地 R1–R8 计划与 DSL 提案同步为 v0.35；全部阶段仍未实施。
[示例 §6–7](../analysis/lazy-analysis-dsl-examples.md#6-预定义与-runtime-共用表达式与声明)
对照预定义/runtime，覆盖收入、AOV、固定人群均额、先空间后时间 P95、共同有效配对五个核心场景。
[场景验证 §9](../analysis/lazy-analysis-dsl-scenario-validation.md#9-后续实现的最小验收集)
拥有未来验收；文档与独立数值见证不是 Runtime 证据。

必须分别验证：同式同阶段的计算/政策等价、构造无业务 I/O、声明和收录分离、纯表达式体限制、
行权限不会由 dtype/乘法/命名自动扩大、跨根行算术拒绝、/ 与 divide 同节点、共同配对、
per(P) 的样本选择与 observe(by=P) 的输出覆盖不同、采样次序与覆盖、仅会话域收录拒绝、
本次计划的充分组件及无图 Artifact 读取；新产物与重开产物都拒绝 DSL 输入。
既有 Query 节点政策不得被新默认覆盖，Artifact 的 Finding/Evidence 不恢复节点或赋予计算权限。
实施时一次同步公开类型、Help、错误和中英文 API 示例；技能变更仍须另获明确授权。
本轮不把提案签名写入当前实现或发布文档。

## 附录 A：被本版替代的设计

上文是 v0.35 的目标合同，不提供兼容 API；在真实实施切换前，已提交的 current 文档和 live Help 仍约束现行实现。

### A.1 拟替代的已提交合同

下表明确能力是否保留、由谁拥有以及旧声明如何处理；不能把未列出旧参数解释成静默忽略。

| 已提交合同与 owner | 目标合同与能力处置 | 旧声明的处置 |
| --- | --- | --- |
| [Overview 的 Ibis 唯一作者语言](overview.md#design-goals)、[受限 Ibis 表达式体](semantic-object-model.md#common-contracts) | Measure/Metric 作者语言改为本文的受限行/指标表达式；Ibis 负责来源 lowering。Entity/Source、Dimension/TimeDimension 的合法来源构造与 Ibis 字段能力仍由原 owner 管理，不借此开放任意 Metric Ibis 桥接 | 新 Measure/Metric 声明拒绝表参数、Ibis scalar 及已退役工厂；未受本次替代影响的来源声明继续遵守原合同 |
| [fanout_policy 与 aggregate_then_join](semantic-object-model.md#grain-root-entity-and-fan-out) | 退役该参数及隐式多值分组路线。存在匹配筛选使用显式 Population.preimage，再将所选身份作为贡献约束；每个来源身份保留一次。重叠标签分组不等价于半连接，本版不提供旧的自动 fanout 分桶 | 参数拒绝，不能降为 block 或忽略；结构化修复区分存在匹配筛选与本版不支持的重叠分组，不自动改贡献根或分配方式 |
| [empty 声明政策](semantic-object-model.md#fixed-null-and-empty-contracts) | sum 空输入与全 NA 均为 0；其余算子按 §3.4。移除 empty 参数和 ms.empty 构造器 | 即使旧值为 zero 也拒绝旧语法；旧 empty=null 不能被默认为 0，须由作者明确重写定义 |
| [status_time_fold 与 aggregate fold](semantic-object-model.md#sampled-semi-additive-metrics) | 退役 Measure/Metric（包括 measure_column）上的默认 fold；统一为 per(状态 TimeField).reducer。状态时间角色、采样间隔、可加限制仍归来源声明，先空间后时间及覆盖要求按 §3.2 保留 | status_time_fold/fold 参数拒绝；修复指向显式内部样本表达式，不丢弃旧折叠次序 |
| [跨 Entity 的 time_via](semantic-object-model.md#metric) | 保留明确的跨 Entity 时间读取能力，统一为 time=TimeField.via(path)，按 §2.1 捕获单值路径；不增加观察时路由覆盖 | time_via 参数拒绝；错误给出原时间字段和有序关系路径的改写位置，不猜路径或改为根 Entity 的其它时间 |
| [approx_* 与定义拥有精确性](semantic-object-model.md#definition-owned-aggregate-exactness) | 本版仅纳入 §2.1 的非近似规约；近似聚合明确不在目标范围。保留定义拥有精确性、禁止后端静默替换的边界 | 旧 approx_count_distinct/approx_median/approx_percentile 声明拒绝；不自动改成精确算子，也不把不支持的精确请求降为近似 |

声明校验/模型加载必须在业务来源读取前拒绝已移除的参数、工厂和表达式体。
错误列出声明位置、收到的旧语法、期望的目标形状及真实可用修复；不双读、不迁移、不静默忽略或重解释。
这项规则适用于 Python 模型声明，独立于旧四态 Artifact/图快照格式的拒绝；正常新声明仍按 §2.4 检查依赖闭包。

用户模型采用人工改写对照与逐声明 typed repair，不提供 codemod、兼容加载或自动写回：

| 旧写法 | 目标改写与边界 |
| --- | --- |
| ms.aggregate / ms.count | 从已解析 Measure/Entity 选择原计算对应的接收者规约，如 Amount.sum(time=PaidAt) / Orders.count(time=PaidAt)；原过滤和时间路径分别保留，不统一替换为 sum |
| ms.ratio | 组合已构造 Metric，使用 numerator / denominator 或显式 divide(..., zero_denominator="error")；作者按 §3.4 明确新政策，不能猜测旧缺失状态的等价写法 |
| @ms.metric(entities=...) 与 Ibis 表参数体 | 改为零参数体，返回由已声明字段/Metric 构成的表达式；来源根从依赖取得，多根独立规约，不能将旧表 join 机械搬入新体 |
| fanout_policy | 存在匹配筛选显式使用 Population.preimage；重叠标签分桶本版无等价替代，错误明确说明，不能自动改为半连接 |
| empty / ms.empty | 移除旧语法前确认目标空值合同；旧 empty=null 的 sum 不能通过删除参数冒称等价，作者须接受新定义或重写业务问题 |
| ms.bind | 从新 Measure/Metric 体移除并直接使用已构造字段；保留的 Ibis 字段声明体仍按 §2.3 的受限合同使用 |

错误至少定位当前被拒声明及对应旧语法；不为一次收集全部模块错误执行无效模型，
也不承诺自动生成业务等价的新定义。旧语法即使参数值恰好等于新默认也不接受。

切换实施时须同步上述 current owner、Help、示例、错误和英中发布文档。
[AGENTS.md](../../../AGENTS.md) 的 Ibis 表达式规则及两个 packaged skills 的编辑需要显式用户授权，
本次提案修订不修改这些受保护文件；CLAUDE.md 是 AGENTS.md 的符号链接，不形成第二份规则。

### A.2 提案演进

| 早期方案 | 当前目标 |
| --- | --- |
| 分析 Domain、domain()、in_domain(...) | Population、population()、in_population(...)；语义业务 Domain 保留 |
| ms.aggregate/count + Ibis Metric 装饰器两条语言 | 来源接收者规约、Metric 组合；零参数声明体返回同一表达式 |
| ms.ratio 与普通 / 分开 | / 和 divide 是同一节点、角色与默认政策 |
| named 或装饰器显式完整名称 | 装饰器捕获函数局部名；模型收录限定命名空间，输出标签归 observe |
| 装饰器全局 nulls/zero_denominator | 政策归每个实际算子，声明不覆盖 |
| fold 参数隐含第二规约阶段 | per(采样轴).quantile 等显式表达次序 |
| over(Domain) 要求先 where 再覆盖全部贡献 | per(Population) 直接选择内部样本；外部输出覆盖合同不变 |
| 行表达式只能写 Ibis 函数体 | 有类型/权限的有限行表达式；Measure 声明新业务量 |
| 禁止所有 runtime 域或构造时检查模型持久化 | 显式不可变依赖合法；独立持久解析在模型收录检查 |

窗口、身份、默认来源信任和 pandas 交接继续由 DSL 及执行文档拥有；
早期 Result 续算、固定 Artifact 键域与 origin/fixed/cold 已由终端 Artifact 边界替代。
能力保留、退役与旧声明拒绝以 A.1 和正文为准；参数减少不作为计算等价证明。

v0.28 复审修正：两种装饰器从函数名推导局部声明名，模型收录限定完整身份；Measure 继承可推导事实、说明复用 ai_context；per 的样本在每个外部格子内建立，
按绑定能力校验域形状；权重归一化不要求业务可加性；Metric 常数平移不继承完成值可加性。
示例与验收同步，模型/runtime 的 AOV 政策及字段路径使用同一合同。全部仍为文档提案。
