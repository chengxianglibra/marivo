# 语义对象与分析表达式的统一设计

Date: 2026-10-17

Status: v0.37 目标设计与实现来源。原生 Deferred 表达式及关联执行能力按
[S0–S6 实施计划](expression-model-implementation-plan.md)实施；该计划的验收记录界定本次范围。
当前可调用契约由 [Semantic Object Model](semantic-object-model.md)、
[Loading, Validation and Introspection](loading-validation-introspection.md)、
[Python Analysis Design](../analysis/python-analysis-design.md) 和原生 Help 拥有。
本文保留完整目标设计；目标条目本身不证明所有分析 DSL 或物理后端路径已实现。
v0.36 采用受控语义输入上的原生 Ibis 数值表达式，命名与匿名共用延迟构造和语义检查；
execute 准备自动绑定真实物理类型，不引入 ms.ibis、ms.require 或另一套用户类型准备流程。
原生数值语言不撤销 Marivo 的规约、时间、空值和贡献合同；完整机制与边界见 §2.6、§3.6。
2026-10-16 审查修订明确作者侧始终延迟、语义使用位置、两层指纹、分支检查域及保留执行分派。
v0.37 补齐有序 first/last、近似规约、显式重叠成员分桶、sum(empty="null") 和命名窗口 Metric。
五项能力共用上述载体；新增的贡献/时间作用域不是自定义数值 AST。旧能力的等价改写条件见附录 A.1。
2026-10-17 审查修订补齐成员恒等绑定、独立集合组合和完整输出域；窗口仅保留无轴规则加 Metric.window，
明确标量 to-date 端点、旧 first-seen 归属及其条件等价，并统一 first/last 采样验收。
后续审查收敛为显式成员路径优先、无路径时推导恒等；普通窗口不强制共同输入。
窗口在 observe 固定逻辑绑定，本版拒绝窗口原量的时间轴粗化、替换和删除。
2026-10-17 fanout 修订以贡献身份、出现次数、分桶与规约次序约束求值，不固定 aggregate-then-join 执行策略；
各类表达方式、能力边界和等价计划条件由 §3.7.1–§3.7.2 拥有。
v0.35 补齐声明改写、validator 分层、单位拒绝与精确分位数合同；不新增期末规约或资源预算。
v0.34 将时间样本规约与有序 fold 分名，并同步比较/份额、窗口、来源类型准入和执行披露的跨层边界。
v0.33 统一所有阶段的零分母政策：默认 null，显式 error；参数只控制当前算子。
拟替代的已提交合同、旧模型声明的拒绝边界和实施前置授权见附录 A。
v0.32 明确：execute 返回终端 Artifact；所有 DSL 组合在 Query 上完成，Artifact 不携带语义对象/图或内部组件。
模型加载合同独立；分析产物可保存实际 Finding/Evidence 输出，但不承载计算依赖。
执行分工继续为 Ibis 源端计算、pandas 统一本地执行、PyArrow 存储/读取。
v0.30 采用 nullable value；普通缺失统一为 NA，非法计算和实际覆盖检查失败按错误处理。
sum 默认忽略 NA，空输入与全 NA 默认都为 0；可在来源/内部样本 sum 上显式选择 empty="null"。
政策不区分空输入与全 NA；仍无逐格四态/原因传播。

Implementation boundary (2026-10-17): native Deferred ingestion, semantic preparation,
and the execution routes in the S0–S6 plan replace the custom numeric AST. The broader
Lazy Analysis DSL proposal remains a separate scope. Current Metric catalog preview is
qualified for ordinary DuckDB expressions; sample, Population, window and routed-field
preview routes are rejected explicitly. Historical R1.3 checkpoints are not current
acceptance claims; use the implementation plan and owning current contracts.

## Definition changes during analysis (approved 2026-10-15)

Marivo does not require semantic objects to remain bound to the exact definition
versions from which they were constructed, and does not reject an input solely
because a same-path definition has changed. If semantic-layer definitions change
during analysis, the agent or user accepts the consequences. This does not relax
type, kind, contribution-root, grain, unit, time-role, null or other semantic
correctness checks. Definition fingerprints must still describe the computation
and dependencies actually consumed; cache reuse must use correct keys and
invalidation, and persisted provenance and records remain governed by their
owning contracts.

Marivo 不要求语义对象始终绑定其构造时的精确定义版本，也不因同路径定义已改变而单独拒绝输入。
分析期间语义层定义发生更新，其后果由 agent 或用户承担。这不放宽类型、kind、贡献根、粒度、单位、
时间角色、空值或其它语义正确性检查。定义指纹仍须描述实际消费的计算及依赖；缓存复用必须使用正确的键
和失效规则，持久化来源记录与其它记录仍遵循各自拥有的合同。

本决定取代先前要求保留原定义版本并拒绝同路径改版本输入的部分，不删除定义身份、计算指纹或记录。
在实际进行引用解析的消费者处，constructed 输入按 kind/path 在该消费者的完整作用域解析，
不额外核对输入原先记录的定义版本。已经构造的纯表达式仍不可变，组合其存储的定义与事实；
不新增 live refresh、自动重跑模块或 import effects，也不把裸 Ref 变成表达式。
既有 snapshot/validity 业务历史选择、时间路径、角色及完整键约束与“定义版本更新”不同，继续保留。

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
| Ibis | 原生数值表达式、Deferred 构造与绑定后的 operation DAG/物理类型；合格来源 lowering |

Population 是分析总体，与语义声明所属的业务 Domain 分开；业务命名空间仍使用 domain= 与 ms.ref.domain(...)。
v0.29 只同步此命名及 population()/in_population(...)，不改变依赖、绑定或求值语义。

普通分析只需 `表达式 → observe → execute`。声明后的对象仍能使用相同算子；
不需要 RuntimeMetric、临时注册、as_metric 或第二套表达式工厂。
计算表达式不可变，构造、声明及其消费者的 show/contract 都不读取业务来源。
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
Measure 及数值行表达式支持 §2.6/§3.6 准入的原生 Ibis 数值计算，以及以下 Marivo 来源规约。
下面的 time/nulls/zero_denominator 等是语义接收者合同，不是原生 Ibis 方法签名；
内部适配与原生同名调用的归一化见 §2.6。下列 class 块是语义方法协议示意，不要求新建对应数值类；
返回 Metric 表示完成指标阶段，原生调用链中间仍可能是 Deferred。支持类型不扩展顶层 Help；
NumericRowExpr 表示符合行阶段的受控语义延迟输入（含 Measure）；MetricInput 表示符合指标阶段的
受控语义延迟输入（含 Metric）。命名与匿名数值作者值均为 §2.6.1 所述受控原生 Ibis Deferred，
不接收带关系的裸 Ibis Value 作为第二作者入口。二者是签名中的封闭输入约束，不新增顶层导出；
数值/阶段资格须由语义检查确认，不能从载体的 Python 类型推断。NumericLiteral 是有限 int/float/Decimal，排除 bool、NaN 和无穷。
TimeField 是已解析的时间字段；依赖保留实际使用的定义身份，不按同名物理列猜测，也不强制绑定构造时的定义版本。
TimeField.via(path) 保持 TimeField kind，并捕获从贡献根到时间字段 Entity 的非空、有序、
按声明方向连续且单值的关系路径；将该对象传入 time= 保留跨 Entity 时间绑定能力，不另设 time_via。
原生时间字段直接传入 time=，不写空路径。绑定检查贡献根、完整键、角色、版本和路径端点，
不选择最短路径，也不把字段读取路径当作观察主体的归属路径。

```python
class NumericRowExpr:
    def sum(self, *, time: TimeField | None = None,
            nulls: Literal["ignore", "error"] = "ignore",
            empty: Literal["zero", "null"] = "zero") -> Metric: ...
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
    def approx_median(self, *, time: TimeField | None = None,
                      nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def approx_quantile(self, q: float, *, time: TimeField | None = None,
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
    def approx_nunique(self, *, time: TimeField | None = None) -> Metric: ...


class Entity:
    def count(self, *, time: TimeField | None = None) -> Metric: ...
```

数值行表达式也可 count/count_distinct/approx_nunique，规则与 Field 相同。Entity.count 数完整贡献行；
Field.count 数非 NA 值；count_distinct 数不同非 NA 值，空输入均为 0、单位为 1，
不接受 nulls/unit 覆盖。quantile 的 q 必须有限且 0 <= q <= 1；采用精确样本的线性插值规则，
端点分别等于同一有效样本的 min/max，不必为端点改换调用形状；不隐式降为近似算法。
对排序后的有效样本 x[0]…x[n−1]，令 h=(n−1)q、i=floor(h)、j=min(i+1,n−1)，
结果为 x[i]+(h−i)(x[j]−x[i])；n=1 返回唯一值，median 使用相同规则的 q=0.5。
这是来源和内部样本 quantile 的共同统计定义；物理累加、插值精度和最终舍入按已准入方法合同，
不以整数输入强制将插值结果取整，也不新增 interpolation 参数。
min/max/mean/median/quantile 的空或全 NA 输入固定为 NA，不提供补零参数。
sum 的可加许可由 §2.5 约束；empty 的两个取值及检查顺序见 §3.4。
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
物理作者阶段先捕获 PendingDefinition 并返回类型化 Ref，尚不产生闭包完整的表达式值。依赖完整后显式
`finalized = ms.finalize()`，复用 registry/assembly 校验并冻结闭包，再以
`amount = finalized.resolve(amount_ref)` 取得不可变 Entity/Field/Relationship 值用于组合。
finalize 后同一物理 scope/root 不再接受新增声明；缺失依赖的失败保持捕获阶段开放供修复。
受限 Ibis 字段仍可用显式 lookup Ref 捕获前向绑定，必须在 finalize 前完成依赖。
Ibis bind 及物理 owner、关系端点/键、versioning、status time、parse prefix、
calendar/temporal-set/work-schedule、additivity helper 接收 constructed 输入时，
按消费者实际闭包解析 kind/path，不再携带“必须等于输入原版本”的隐藏约束；显式 Ref 仍是查找键并支持前向依赖。
pure Measure 的显式 additivity 在行表达式的冻结坐标中检查轴存在性、kind、native time role 和权限，
不因原定义版本不同而拒绝，也不借活动 loader refresh 已返回值。
保留 finalize 是为了完整默认时间/坐标事实和 Ibis 前向依赖，而不是为旧版本强制绑定提供生命周期。
不允许 live mutable 返回值、按活动上下文 refresh 或重跑模块/import effects；纯 Measure/Metric
仍只消费已构造值，本版不增加隐式前向表达式解析环境。
导入顺序不能替代循环检查；loader 冻结完整依赖版本，缺失或错误 kind 必须给出具体位置。

### 2.2 一种除法与明确配对

```python
class Metric:
    def divide(self, other: MetricInput | NumericLiteral, *,
               zero_denominator: Literal["null", "error"] = "null") -> Metric: ...
    def where(self, predicate: FieldPredicate | PopulationPredicate) -> Metric: ...
    def per(self, samples: Field | MembershipAxis | tuple[Field | MembershipAxis, ...] | Population) -> MetricSamples: ...
```

Metric 支持有限数值常量、正负号、算术及已准入的原生 Ibis 数值函数；组合保持 Metric 阶段，
延迟载体不保证此时已有具体物理 dtype。
`a / b`、`a.divide(b)` 与 `a.divide(b, zero_denominator="null")` 规范化为相同 Ibis 除法运算及语义政策注解、
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
           ) -> Callable[[Callable[[], MetricInput]], Metric]: ...


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

与 §2.1 的临时定义比较，函数体就是同一个计算表达式。装饰后的名字绑定具有 Metric 声明记录的 Deferred，
后续直接写 revenue / order_count，不调用 revenue()。类不是必要的业务容器，不增加类装饰器变体。
Metric 装饰器从函数名声明局部名称，可指定 domain 和 ai_context；不接收 name、entities、time、fold、additivity、unit、
nulls 或 zero_denominator；来源/单位/时间从表达式依赖取得，数值政策在发生运算处声明。
它也不按最终 float dtype 或公式形状推断新的可加权限。

声明体为零参数，可含首段 docstring、无重绑定的局部表达式和唯一最终 return。
只接受受控语义输入上的 Ibis/Marivo 表达式、已声明依赖及不可变字面量/域；不接受导入、循环、分支语句、
执行查询、状态修改、任意函数调用或活动外部对象。限制在静态验证和受控图构造中执行，
不先运行任意 Python 再验证返回类型。跨模块辅助声明使用已经构造的对象。
返回满足 Metric 阶段的受控 Ibis 延迟配方或命名 Metric 是合法输入；
带关系的裸 Ibis Scalar/Column 不作为作者返回值接收。自由行值、外部关系表达式、结果 Column、Query 或纯 Python scalar 仍拒绝。
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
新 Measure/Metric 体不接受 Ibis Table 参数或 ms.bind；接受受控叶子上原生 Ibis 数值延迟构造，
并与匿名式共用 §2.6 的捕获/检查。Python AST 负责声明安全，Ibis operation DAG 负责数值结构检查，不能互相替代。

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
收录从导出 Deferred 的根声明 Resolver 读取声明记录，不依赖另一个 Metric 包装类或 Ibis `.name()` 标签；
只有依赖中含命名节点的匿名派生式不会自动被收录为该声明。
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
派生 Measure 使用同一套原生 Ibis 行表达式，装饰器声明新业务量及其授权，不接收 Ibis Table 参数。

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

### 2.6 原生 Ibis 与自动延迟绑定

本节是 v0.36 的目标设计，不是当前可运行 API。作者直接写 Ibis 数值表达式，不写 ms.ibis、
ms.require、手工 dtype/schema 或表达式转换调用。输入来自已完成语义捕获的 Field/Measure/Metric；
业务字段身份与物理类型是两种事实，取得前者不要求提前连接来源。

```python
import ibis
import marivo.semantic as ms

# Inputs below are resolved semantic values, not bare Refs or backend columns.
positive = ibis.ifelse(Amount > Cost, Amount - Cost, 0)

@ms.measure(additivity=ms.additive_all())
def positive_margin():
    return positive

index = (Revenue / ReferenceRevenue).sqrt()

@ms.metric()
def revenue_index():
    return (Revenue / ReferenceRevenue).sqrt()

# The same expression can be consumed without a declaration or registration.
query = s.observe({"named": revenue_index, "anonymous": index})
```

Amount/Cost 同根同单位；Revenue/ReferenceRevenue 是单位相同的 Metric，商无量纲。
positive 的条件截断不自动继承求和许可；positive_margin 显式承担新业务量授权。
index 与 revenue_index 的计算、依赖、政策、检查和执行资格相同，仅后者有声明元数据。
这个例子不通过把逐行 sqrt 移到聚合前来模拟聚合后的 sqrt。

#### 2.6.1 同一个输入和捕获机制

作者侧采用一种受控延迟协议，schema 已知与未知时均如此；物理类型证据不改变作者侧对象的组合行为。
命名和匿名的差别是声明记录，不能成为“命名返回包装对象、匿名返回裸 Value”的分界。

1. Measure/Metric 及作为表达式输入的 Field 在作者侧统一提供原生 Ibis Deferred，其内部使用受控叶子/声明 Resolver。
   这也覆盖条件中的 Dimension/TimeField；字段 kind 仍在叶子中保留，不能把数值编码 Dimension 当作 Measure。
   time/via 等参数消费语义字段记录，不能先把时间字段当成普通数值 Value resolve。
   叶子持有不可变语义身份及配方引用，声明 Resolver 持有命名记录或 Measure 断言；不是把另一个公开包装对象塞进 Deferred。
   普通算术、比较和顶层函数全部复用 Ibis Deferred 的运算符和捕获机制，不创建 Marivo Add/Divide 等数值节点。
   装饰器返回同一种 Deferred，只在内部增加声明记录；不得向作者返回另一个仅有 `.expr` 的数值容器。
   这样不依赖 Ibis 识别任意包装对象，也不继承 final Deferred、不全局 monkey-patch Ibis。
   业务 Measure/Metric kind 由语义记录与检查确定，不由一个不同的 Python 数值包装类确定。
2. 原生 Deferred 的后续属性、方法和算术继续捕获配方；因此 `ibis.ifelse(...).mean(time=PaidAt)`
   在进入消费边界前不会先落到裸 NumericColumn.mean。已取得 schema 的输入也不提前 resolve 为 Value。
   来源对象自身的语义操作（如 Entity.count）也返回这种 Deferred；后续方法链由同一解析器解释。
   不给 Deferred 注入业务属性或 show/contract 方法；定义披露由 Catalog 条目、运行中事实由 Query.contract 等已有消费者提供。
3. @measure/@metric 先验证声明语法，再按相同协议捕获；仅命名附加独立声明记录，新增业务断言则形成语义边界。
   引用命名 Metric 在计算上展开同一配方，既不提前求值，也不改检查范围。模型收录沿同一闭包追加身份与加载检查。
4. 声明返回、observe 等 Marivo 消费边界先遍历受控 Resolver 配方，识别语义叶子、规约、per/window、membership 参数、政策参数、
   原始依赖及每个使用位置；完成已知阶段/根/单位检查，保留待 schema 验证的义务。
   这一来源无关的步骤先于 schema preparation，提供准备所需闭包；不能等真实 Ibis DAG 出现后才发现要绑定哪些来源。
5. execute 准备取得真实 schema 后，由同一受控解析器递归处理配方：普通数值调用委托给获准 Ibis 构造器，
   语义方法先归一到语义边界，规约输出以带身份的值槽位连接到后续数值子图。
   不直接对整份混合配方调用原生 `.resolve()`，否则 time/per/window/empty/divide 等会误传给裸 Ibis 对象。
   解析只调用已验证构造器，不重跑模型、imports、任意作者函数或活动上下文。

带关系的原生 Ibis Value 只在内部类型绑定/lowering 中出现，不作为 Measure/Metric 作者输入、装饰器返回值或
observe 的另一入口；后端表、memtable 及来源相同但未经本协议捕获的表达式均拒绝。
获准的原生 Ibis Literal 可作为不可变字面量子项接收，但不能单独建立 Measure/Metric，不能承载来源关系或业务授权。
已知 schema 不构成开放裸 Value 入口的理由，现有 Entity/Dimension 受限 Ibis 作者合同不受此限制替代。
不伪造 float64/null dtype 或空 schema；“原生”指作者数值语法、Ibis 延迟配方和绑定后的 operation DAG，
不承诺在作者阶段已经存在一个真实 NumericColumn。

这个解析器不是第二套数值 AST：它遍历 Ibis 已有 Resolver 结构，并附加必要的语义边界/使用记录。
只接收明确获准的 Ibis Resolver 种类及 Marivo 自有叶子/声明 resolver，不能仅凭 `isinstance(Resolver)`
执行外来 resolver。语义方法的识别先于普通 Ibis 方法求值；未知调用在调用前拒绝。

Ibis Deferred 能延迟调用不等于调用获准。内部适配须检查真实符号身份和接收者，拒绝任意 Python
回调、pipe、UDF、执行/编译/导出、backend 查询、可变闭包与未知 resolver。普通脚本由用户运行，Marivo
不是 Python 沙箱；它承诺不执行藏在所接收表达式里的未知调用，不承诺阻止用户在调用 Marivo 前自行做 I/O。

物理定义的 capture/finalize/resolve 仍按 §2.1 关闭语义依赖和默认时间事实；本修订不重设计该生命周期。
它不绑定物理 dtype，也不要求在此之后增加一次 require/finalize/resolve。命名与匿名均无额外类型准备步骤。

#### 2.6.2 原生数值方法与语义方法的分工

Ibis 拥有 abs、sqrt、round、cast、ifelse 等数值计算语义；Marivo 不逐个发布同名 ms 函数。
来源规约、Metric.where/per/window、weighted_mean、divide 的政策参数及 time=/empty= 继续属于 Marivo。
first/last 的排序与 NA 选择、近似规约的精确性意图、membership 分桶与窗口规则同样在语义边界检查。
例如 Amount.sum(time=PaidAt) 是语义接收者的扩展合同，不能将 time= 直接传给真实 Ibis Column.sum。
本方案不声称整套语义 API 与裸 Ibis 对象的方法签名完全相同。

延迟适配先识别这些有限的语义方法，记录语义边界与参数，再把普通数值部分交给 Ibis。
它必须同样处理 `(Amount - Cost).sum(time=PaidAt)` 与 `ibis.ifelse(...).sum(time=PaidAt)`，
不能依赖中间值恰好仍是某个 Marivo wrapper。普通 Ibis 同名 reduction（如无参数 sum/mean）
按接收者的行阶段归一化到同一语义规约，默认时间仍从冻结事实继承；对完成 Metric 再 sum 不隐式建立内部样本，
必须使用 per。原生 reduction 的 where= 若被使用，只能表示该次规约的合法同行谓词，并归一化到同一贡献过滤；
不允许它改变根或绑定其它规约。未知参数和无合同的规约拒绝，不静默丢弃。

原生 Ibis 的 / 与同一数值阶段的 divide 共用 §3.4 政策；裸 sum 使用 empty="zero"，显式 empty="null" 先由适配器消费。
这是对 Ibis 计算图的语义准入和政策 lowering，不是逐字照搬各后端默认结果。
Ibis 原有 nullif/coalesce/条件表达式保留显式计算意义，不能用它们把未获准的规则包装成已获准算子。
别名 name 只改变 Ibis 标签，不建立业务声明、单位、贡献根或权限。

内部适配的工程实现必须验证所支持 Ibis 版本的 Deferred、顶层函数和方法链协议；其私有结构不能成为公开 API。
新增数值函数通常补语义规则和执行资格，不新增用户包装函数。若不能安全捕获某个原生调用，给出具体能力缺口，
不得要求用户提供虚假物理类型或悄悄读取来源。数值表达式能力是明确准入集合，不承诺任意 Ibis API 都可用。

#### 2.6.3 类型何时成立

```text
语义叶子 + 原生 Ibis 延迟构造
  → 声明/匿名消费：已知语义检查，记录未绑定 dtype 和义务
  → observe：观察期间、输出键、各贡献根和时间绑定
  → execute 准备：自动访问所需 schema，解析真实 Ibis operation DAG
  → 完成物理类型、语义义务、政策 lowering 和执行路线准入
  → 业务数据计算与值域检查 → 终端 Artifact
```

已有 schema 可复用有明确来源和适用范围的证据；不能把过期缓存当作当前来源事实。
每次执行按实际 schema 和消费定义生成绑定/计划指纹，不能仅按未绑定表达式指纹复用旧 dtype 的计划。
无 schema 时，Measure 的 numeric 是要求而非已观察的物理事实；int/Decimal 精度、scale、nullable、
cast 合法性与具体函数签名在真实绑定后判定。表达式中的 cast 是明确的计算，不能反过来成为输入 schema 声明，
也不能把数值编码 Dimension 转成获准 Measure。

声明、load、show/contract、observe 不为补 dtype 连接来源。execute 复用现有 schema preparation，
覆盖完整所需闭包，包括条件、未选分支、键、路径和时间字段；它可能发生网络或文件访问，不能称为零 I/O。
不增加用户 preflight，不扫描整表证明 Entity/Relationship 声明，也不访问与请求闭包无关的字段。
真实类型不合法时在业务计算前失败；schema 无法证明的数据域、除零、严格 NA、溢出和非有限值在计算阶段检查。
无效 sqrt/log 等按非法计算失败，普通输入 NA 按 §3.4 传播；不把后端生成的 NaN/inf 当作成功结果。
条件表达式不能因后端提前求值无效的未选分支而改变语义；无法安全 lowering 时拒绝该路线。

## 3. 保留阶段，统一计算规则

### 3.1 行、指标和结果的边界

行表达式、Metric 和 Query Column 各自携带样本单位、身份、数值许可和阶段，不能隐式转换或混算。
原生 Ibis Scalar/Column 的物理 shape 不能替代这些阶段。数值条件表达式和函数按 §3.6 检查；
Field 谓词消费其中合法的同根布尔子图，保留条件和所有分支的依赖。
本次只扩展 Measure/Metric 数值表达式，Query Column 与 Population 的计算语言仍由各自 owner 管理。
任意关系程序、窗口、子查询和 UDF 不因数值返回类型而自动准入。

Metric 多来源根在逻辑上各自按声明贡献规约，再按完整目标键组合；有内部样本时先保留完整 (k,j) 键。
不得把关系展开产生的重复行当作原根的新贡献。此规则不规定物理 join 与 aggregate 的先后；
合法关系读取、成员集合构造及等价执行计划按 §3.7.2 准入，不能从原始表是否出现在 join 中判断是否合法。
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

本次期间和评价端点由 observe 绑定；Metric 可以显式保存 §3.8 的相对窗口规则，没有 during/route 默认。
普通来源规约可以省略 during，表示不追加时间过滤，保留业务时间角色和固有条件；
时间样本规约或日历输出等真正依赖期间的情形必须显式传入。日期谓词不能替代该绑定。
域自己的选择期间、属性时点不被外层 during/where 重写；冲突应失败，不暗取交集或最近覆盖。

#### 3.2.1 有序 first/last 与期初期末采样

`Inventory.sum(time=SnapshotAt).per(SnapshotAt).last(include_null=True)` 先按每个时点完成空间总量，
再选最后一个时点的值；first 同理。它不是每个实体各取最后记录再求和，也不是任意行 first/last。
唯一内部 TimeField 可提供升序；多个内部轴必须显式 order_by 指定其中的 TimeField，
并证明每个外部格子内该时间键唯一。不能用行顺序、后端任意 tie-break 或偷偷删除重复键修复歧义。
无时间轴、非内部排序字段、无法唯一排序的样本均拒绝；不在本轮开放任意序列或行级窗口。

沿用 Ibis 的 include_null 语义与默认 False：False 选择首/末非 NA 样本，True 选择首/末样本并保留其 NA。
没有可选样本返回 NA；nulls="error" 先检查整个被需求的内部样本域，再执行选择，不能只检查被选中的一条。
采样覆盖检查先于 NA 跳过；缺少声明要求的时点是错误，不等于有时点但值为 NA。
首末样本只来自所绑定期间，不补值、不读取期间外的最后已知状态，也不承诺恰好存在边界快照。
因此“期间内首末采样”与“期初/期末边界的 as-of 状态”须分开，后者沿已有精确时点合同处理。

结果保留数值 dtype 的合法可空形式和单位，不授予时间可加性。空间合并仍须恢复同一时点的子量，
或证明样本域、排序、实际选择锚点和 NA 行为兼容；不能仅因 first/last 保值就给完成值通用 sum 权限。

#### 3.2.2 定义拥有近似规约

使用 Ibis 既有名称 approx_nunique、approx_median、approx_quantile；不新增 ms.approx_* 包装函数。
前者可消费 Field/数值行值，后两者消费数值行值；内部样本提供同名规约。
近似分位数首版只接有限标量 q∈[0,1]，不接数组或动态 q；否则不满足单一数值 Metric 合同。
approx_nunique 忽略 NA、空/全 NA 为 0、单位为 1；approx_median/approx_quantile 沿用数值规约的
nulls 检查，空/全 NA 为 NA、保留输入单位，输出 dtype 由真实 Ibis 绑定及执行路线确认。

exact/approximate 属于定义；近似允许估计误差，不保证结果必然不精确，也不由后端选择反向改变定义。
精确 quantile 的插值合同不能仅凭 Ibis 方法名认定后端满足；精确与近似均须独立验证路线。
基础近似定义不承诺固定算法或误差上界，不新增无法跨后端落实的精度参数。实际算法/版本归绑定执行身份和披露；
后端未公开算法细节时记录实现标识/后端版本并披露未知，不杜撰算法名或误差界。
若旧定义另有明确算法/误差承诺，只有路线证明满足才可声称等价，否则报告未覆盖的承诺。
不支持时在执行准备报告具体算子、类型和后端能力缺口，不静默切换 exact/approximate 或隐藏本地执行。

近似去重完成值不能相加；近似分位数不能合并为总体分位数。默认重新从目标贡献求值。
优化若合并 sketch，必须另证算法/参数/版本兼容及合并资格；本轮不引入公开 sketch 对象或 Artifact 状态。

### 3.3 指标依赖与比率

```python
error_rate = error_count / log_count
```

两项完成后再执行带统一政策注解的 Ibis 除法；依赖保留定义版本、固有过滤、单位、时间和域依赖。
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
| 来源 / 内部样本 sum | 默认 ignore，可 error | empty="zero"（默认）为 0；empty="null" 为 NA | 不适用 |
| mean/min/max/median/quantile/approx_median/approx_quantile | 默认 ignore，可 error | NA | 不适用 |
| 内部时间样本 first/last | include_null 控制选择；nulls="error" 先检查完整样本域 | NA | 不适用 |
| Entity count | 计完整贡献行，不依赖量值 | 空输入为 0 | 不适用 |
| Field / 数值样本 count、count_distinct、approx_nunique | 不计 NA | 0 | 不适用 |
| 来源 / 内部样本 weighted_mean | 默认共同非 NA 配对，可 error | 两种零分母政策下均为 NA | 有有效配对但权重和为零：默认 null，显式 error 报错 |
| 行、Metric、Column 的 /、divide | 任一输入 NA 则结果 NA | 无独立空集政策 | 共同非 NA 输入中分母为零：默认 null，显式 error 报错 |
| Query.share | 份额的任一输入 NA 则结果 NA | 参照按原量规则完成 | 参数只控制新生成的份额；默认 null，显式 error 报错 |
| Query.compare 的 relative | 任一端点 NA 则结果 NA | 缺侧由独立 presence 表示 | 参数只控制新生成的相对变化；默认 null，显式 error 报错 |

sum 的 empty 只归实际规约，装饰器/Measure/模型不覆盖它；旧 ms.empty 工厂不恢复。
空输入与全 NA 都表示没有有效值，但 [-10,10] 是有效零，不能用 nullif(sum,0) 替代 empty="null"。
直接求值或计划内合并须保留合格的和及必要的有效值存在性/计数，最后应用 empty 政策；
不能对已完成的 nullable 分区值做普通加法并冒称等价。empty="zero" 不必保留无用支持状态。
政策不区分“没有记录”和“记录值全缺失”，不保存缺失原因；mean 仍需有效值数，Entity count 是独立请求的量。
它也不创造缺失的输出坐标；生成哪些空桶仍由显式输出域决定。nulls="error" 与 empty 可组合：
真正空输入按 empty 完成，全 NA 输入先因严格 NA 检查失败。来源/内部样本共用规则，Query Column 的政策由 DSL §4.4 单独约束。
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

显式 error 的检查在 §3.6.5 确定的执行域内不能被乘零、后置筛选或丢列消去。读取失败、溢出、非法值和实际日历/映射/请求域覆盖检查失败均报错，
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
    def sum(self, *, nulls: Literal["ignore", "error"] = "ignore",
            empty: Literal["zero", "null"] = "zero") -> Metric: ...
    def mean(self, *, nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def quantile(self, q: float, *,
                 nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def count(self) -> Metric: ...
    def count_distinct(self) -> Metric: ...
    def approx_nunique(self) -> Metric: ...
    def first(self, *, order_by: TimeField | None = None,
              include_null: bool = False,
              nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def last(self, *, order_by: TimeField | None = None,
             include_null: bool = False,
             nulls: Literal["ignore", "error"] = "ignore") -> Metric: ...
    def weighted_mean(self, *, weight: Metric,
                      nulls: Literal["ignore", "error"] = "ignore",
                      zero_denominator: Literal["null", "error"] = "null") -> Metric: ...
```

同组 min/max/median/approx_median 与 mean 同签名，approx_quantile 与 quantile 同签名。
first/last 只接 §3.2.1 的有序时间样本，不开放任意回调变体。
本版不提供从任意观察期间自动选择期末状态的 closing 规约，也不按主体取最后非空值或 forward fill。
显式时点的已有快照读取仍遵守其声明合同；它不授权从期间末端猜快照、补缺测或用 end 减 tick。
per(Field) 是 per((Field,)) 的单轴形态；非空元组也可包含显式 membership 描述，集合组合及完整域见 §3.7。
关键是先确定一个外部输出格子 k，再确定该格子内的样本 J_k；不是先构造一份全局客户值再分配给地区。
Field 样本从 k 内各相关贡献根的完整键像并集推导，不填未出现类别；
空值规约不删除这些键；窗口分支以各自 W(k) 的贡献键像参与并集，不先按显示期间丢弃历史身份。
各组件在每个内部键按自身规约规则完成，再执行原式。

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
普通 Field/Member 轴须有明确角色、版本和对各相关贡献根的合法单值绑定；显式 membership 及携带其绑定的域按 §3.7 处理。不能隐式 fanout、
按同名猜关系、从一个样本权重重复一条贡献，或把域键存在当作来源覆盖证明。
零轴非空域 {()} 表示每个 k 内一次子求值，零键域表示没有内部样本；二者不混同子指标本身的空贡献。
CalendarAxis 可通过 product(Population, CalendarAxis) 保留权威网格，并作为内部完整桶样本；
每个样本以已绑定的实际半开区间分组来源贡献，空桶遵循子指标的空贡献政策。
这不增加 CalendarAxis/CorrespondenceAxis 的内部窗口/端点绑定；窗口输出仍通过 during=CalendarAxis
绑定端点，不从 Population 日期/端点标签猜测。CorrespondenceAxis 统计先显式 observe/compare，
再在 Query 上 reduce；执行后交 pandas。状态 TimeField 的采样合同仍由 §3.2 拥有。
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

### 3.6 沿 Ibis 图推导与语义检查边界

绑定后的计算结构以 `expr.op()` 所得 operation DAG 为准，按依赖顺序分析，并按 §3.6.4 的语义使用位置输出注解。
Python AST 只约束声明体和安全捕获；不通过解析 Python 字符串猜测数值意义，也不重新实现 Ibis 类型系统。
绑定前遍历受控配方做安全性、依赖及可确定的语义检查；不能判定的规则记录为待检查，不能默认通过。
前后阶段共用单位/权限代数及语义合同，但不假称无 dtype 配方已有具体 operation 种类。
命名与匿名共用两个阶段的规则，不维护另一套仅由装饰器源码推导的数值意义。

注解至少包含阶段（字面量、行、完成 Metric）、贡献根/粒度、单位及来源、业务求和许可、
固定轴/时间角色、完整依赖、节点政策、待绑定义务。实际 dtype/shape 取自绑定后的 Ibis 节点。
规则按真实 operation 种类和参数分派；未登记 operation/参数组合给出具体失败位置。
“没有规则”与“单位未知”不同，不能通过 @measure(unit=..., additivity=...) 给未知操作免责。
语义规则只计算注解、约束及合并需求，数值运算继续由 Ibis/已准入本地内核承担。

#### 3.6.1 单位与可加性的传播

| 操作 | 单位与阶段要求 | 可加性/权限结果 |
| --- | --- | --- |
| 受控 Measure 叶子 | 继承单位、同根行粒度和时间事实 | 继承已声明的业务范围 |
| 加减、比较 | 输入单位相容；比较输出布尔；行/Metric 不混合 | 行加减及合法 Metric 加减按 §2.5/§3.3.1；比较不产生数值权限 |
| 乘除 | 单位相乘/相除；同单位相除为 1；保留除零政策 | 无量纲常数缩放保留已有许可；变量乘除不新增许可 |
| abs、round、clip、数值 cast | 保留单位；阈值与被截断值相容；cast 另验物理合法性 | 不默认继承业务求和许可或完成值可加性；仅已证明保值的操作可保留 |
| nullif、ifelse/cases、coalesce、least/greatest | 数值分支单位相容；条件须布尔，阶段/根须相容 | 首期保守不授予求和许可；新 Measure 可按 §2.5 显式授权 |
| sqrt | 无量纲，或现有单位整数指数代数可表达的平方根 | 非线性，不继承可加性；不扩展分数单位系统 |
| log/exp | 输入要求无量纲，结果无量纲 | 非线性，不继承可加性 |
| sum/count/mean/min/max/median/quantile、approx_* | 行到 Metric；数值统计保留输入单位，计数为 1 | sum 验业务授权/固定轴；精确性、空集政策及合并资格分别检查 |
| per(time).first/last | 内部时间样本到 Metric；保留单位，检查排序唯一性和 NA 选择 | 不授予时间可加性；空间合并另证选择锚点兼容 |
| Metric 上准入的数值函数 | 在目标坐标按 §3.6.5 的需求域求子 Metric，再应用函数 | 可直接计算不等于能相加上卷；必须恢复子指标充分状态后重算 |

这是首期规则族，非“全部 Ibis API 已支持”的清单。其它函数（包括方差/标准差、幂和 UDF）
须另补完整规则与路线资格，不能仅凭单位公式或后端可编译放行。
单位未知不会自动变为 1；加减、比较、条件分支、clip 的裸字面量按所在位置的已有量单位解释，
乘除裸字面量是无量纲系数；无类型 null 只表示缺失，不承担单位或权限。不能据此把不相容的两个业务量统一。
已知单位矛盾必须拒绝；@measure 的单位断言可补足允许声明的未知结果，但不能覆盖内部冲突或未满足的函数单位前提。

必须区分三个命题：逐行函数可计算、该行量获准业务求和、Metric 完成值可跨分区相加。
对于互不重叠的贡献集合，`sum(f(row))` 可按行和合并；f 是非线性并不破坏这个数学事实，
但业务是否允许求和仍由 Measure 授权。`f(sum(row))` 则通常不能相加合并。
例如平方和为 9/16 的两个分区：`sqrt(sum(x))` 的分区值为 3/4，总体为 5，不是 7；
只存 3/4 不授予任意上卷资格，计划必须保留/重算原规约所需状态。例中 x 的单位应为 1 或可开方单位。
不通过相消、乘零、别名、过滤或常量折叠删除严格检查、权限、根或依赖。

#### 3.6.2 Measure/Metric 的准入判定

| 边界 | 必须成立 | 拒绝示例 |
| --- | --- | --- |
| Measure/匿名数值行表达式 | 数值结果；唯一贡献根；每条贡献一个值；仅合法单值字段路径；无聚合阶段 | 两个事实根逐行相加、含 sum、窗口、标量子查询 |
| Metric/匿名观察输入 | 数值结果；所有行依赖已被合法规约消费；每个输出格子一个值；各根独立规约后对齐 | amount.sum() + amount、纯行值、单纯 literal、对 Metric 隐式再次 sum |
| 条件和非数值中间节点 | 被已准入数值操作消费，条件/分支的根和阶段完整可解释 | 用另一个事实根的谓词控制本根行量；用数值编码 Dimension 冒充金额 |
| 关系与外部叶子 | 所有叶子追溯到受控语义定义；路径按声明方向、完整键、版本及时点绑定 | 裸 table/memtable、任意 join/group_by、外部 ScalarSubquery、未知来源字段 |
| 模型收录 | 与匿名相同的计算检查，加名称、闭包、独立加载及归属检查 | 活动连接/Session、可变闭包、无可解析依赖 |

Ibis 允许 Scalar/Column 广播，不意味着 Marivo 允许行值和 Metric 混算；`.as_scalar()` 不改变语义阶段。
Metric 多根集合必须保留每个规约发生位置、各自过滤和时间角色，不能简化成一个“主根”。
普通字段读取保持单值；§3.7 的显式 membership 只在分桶边界引入多值成员关系。
不得因原生 Ibis 或 membership 就开放作者自定义 join、隐式贡献倍增或跨根逐行算术；
执行器内部的合法关系展开与去重仍按 §3.7.2 的等价条件检查。
已有限制某个 via/at 字段只能用于条件或坐标的合同仍有效；本次不自动扩大到数值行算术或来源规约。
条件中的布尔/类别/时间子表达式也须有规则，不能只遍历数值叶子。
捕获时保存原始依赖及检查义务，绑定图检查不能被 Ibis 优化删除分支、同名合并或代数消元绕过。
优化只在语义检查之后进行，并须证明不改变结果、NA/错误行为和必要的依赖义务。

#### 3.6.3 哪些可以证明，哪些不能

| 时机 | 可以证明或拒绝 | 不能据此宣称 |
| --- | --- | --- |
| 捕获/声明/匿名消费 | 安全调用、已知阶段/根/单位、声明权限、依赖闭包；不能确定的项明确 pending | 真实物理 dtype、函数后端资格、数据质量、业务授权本身正确 |
| load/收录 | 额外的名称与可独立解析性；不读取 schema | 来源可连接、所有物理类型已验证 |
| observe | 期间、坐标、贡献选择、固定轴和时间角色绑定；保留尚待物理检查的义务 | 已计算数据或已准入所有本地/源端运算 |
| execute 准备 | 真实 schema、完整 Ibis DAG、numeric dtype、剩余语义义务、政策与整条执行路线 | 实际值域、数据完整性或跨源快照 |
| execute 计算 | 所消费数据的除零/严格 NA/有限性等必要检查，实际结果与读取证据 | 未扫描数据的质量或业务关系声明的真实性 |

推导只证明“在这些声明前提下合法”。Entity 主键/Relationship 单值性和业务可加授权是来源语义事实，
普通执行不为证明它们增加全表预检；需要观察证据时沿既有显式 source-health 合同处理。
没有语义规则则语义拒绝；有规则但无后端/本地内核则执行资格拒绝；二者不能混成一个泛化的 Ibis 不支持错误。
每个错误定位表达式/节点、预期与实际事实、检查时机，并给真实可用修复；不会建议 ms.ibis/ms.require。

#### 3.6.4 语义使用位置与共享

数值结构、语义定义和语义使用位置是不同身份。Ibis operation 的结构相等只证明数值结构相同；
同一个 Python 对象或同一 operation 在不同父节点、贡献过滤、时间、内部样本或分支下使用，必须保留各自的语义使用记录。

每个使用位置由“消费方的逻辑作用域 + 从该作用域入口到本次使用的有序操作数角色路径”定位。
角色区分分子/分母、条件/真分支/假分支、规约值/过滤条件等；路径用于内部引用和诊断，不成为公共参数。
逻辑作用域保留贡献根、粒度、过滤、时间、内部样本、membership/window、观察绑定及已有归属；动态检查域另保存其逻辑定义，
不能拿实际求出的布尔数组当作定义身份。仅命名的包装是透明边，不改变计算角色；Measure 新业务断言是非透明语义边界。
原始闭包保留所有依赖，声明身份及来源位置另行用于追溯。

绑定关系为“语义使用位置 → Ibis 子图/运算使用 + 该处政策与语义事实”，而不是 `dict[IbisOperation, Annotation]`。
同一结构的运算可以关联多条使用记录；实际类型和数值结构可复用，政策、权限、检查域不能被相等性覆盖。
解析配方时先建立使用记录，再构造 Ibis 图；lowering/优化须保留这些映射，不能事后从合并后的裸 DAG 猜回差异。
政策改写可以生成不同物理子图，也可以产生独立检查，但必须保留各自语义身份及结果引用。

例如 `n / d` 与 `n.divide(d, zero_denominator="error")` 的裸 Ibis Divide 可以相等，
两处的默认 NA 与严格错误政策仍不同。相同数值行式在两个 Measure 声明下获得不同权限时也不能合并授权。
公共子表达式消除只能在类型、语义事实、节点政策、贡献/时间绑定及需求域均相容时进行；不以结构相等作为充分证明。
同一语义计算被多个使用位置消费时，可在证明等价后按需求域并集求值，且每个消费者只取得自己的域；
严格检查覆盖并集中的真实需求，不能由一个消费者的窄域掩盖另一个消费者所需的错误。

使用位置身份不直接成为内容指纹：同式内联、仅命名、别名和正常公共子式提取应有相同声明计算指纹，
但不同逻辑使用仍保留各自检查/路由记录。规范摘要按角色展开透明引用并保留真实语义边界；
不依赖内存地址、随机 ID、源码行号或输出标签，也不要求展开成重复的数值 AST。

#### 3.6.5 条件分支的需求域与检查范围

采用按表达式语义确定的需求域，不按 Python 赋值顺序或“是否已命名”决定求值。
所有分支始终检查符号、依赖、阶段、根、单位、权限及真实 dtype；恒假分支中的跨根表达式、未知操作、
不相容单位或非法类型仍拒绝。schema 准备仍覆盖静态完整闭包，不能用分支遮蔽来源无法绑定等错误。
这里只限定数值求值与数据检查域，不承诺未选分支完全不访问来源，也不把连接、读取或解码错误改成成功。

设某表达式使用位置的需求域为 D：行阶段 D 是合法贡献行，Metric 阶段 D 是需要该值的完整输出坐标。
数据检查（严格 NA、除零、sqrt/log 值域、溢出、有限性）在该运算自身的需求域上执行。

| 构造 | 求值及检查域 |
| --- | --- |
| ifelse(c, a, b) | c 在 D 求值；a 在 D 中 c 为 True 的部分求值；b 在 c 为 False 或 NA 的部分求值 |
| 有序 cases | 条件在尚未匹配的域依次求值；各分支只消费首次匹配域，剩余域进入默认分支 |
| coalesce(a, b, ...) | 从左到右求值，后项只消费前项结果为 NA 的剩余域；不是异常捕获，前项严格失败仍失败 |
| 普通算术、nullif、least/greatest、布尔 and/or | 所有操作数在 D 求值；不提供隐含短路保护，乘零和相消不能缩小检查域 |
| 来源规约及其 where | 先确定该使用位置的合法贡献域及显式规约过滤，再计算被选行的值并检查；筛选条件自身在其输入域检查 |
| Metric 阶段的分支 | 按被选输出坐标向子 Metric 传递需求；子规约在这些坐标对应的完整合法贡献上求值，不能把格子条件当作逐行过滤 |
| Query 后置筛选、丢列、Top-N | 不反向缩小已有观察/严格计算的需求域，继续遵守既有错误保留合同 |

per 的需求从外部格子传给其内部样本计算，内部样本上再应用各自条件；不能跨过 per 或规约改变计算次序。
判断条件依赖的 Metric 本身先按条件需求求值，其错误不能被后面的分支选择遮蔽。
命名声明只提供配方，不是 eager 求值根；observe 的每个显式指标请求才建立其所需坐标的根需求。

```python
strict_ratio = numerator.divide(denominator, zero_denominator="error")
guarded = ibis.ifelse(denominator != 0, strict_ratio, 0)
```

当 numerator/denominator 为同阶段相容输入、分母为 0 时，guarded 返回 0，未选除法不做除零检查。
分母为 NA 时条件为 NA，选择假分支，同样返回 0；若直接消费 strict_ratio，则仍按 §3.4 传播 NA。
内联该除法、提取匿名变量或仅命名为 Metric 的结果完全一致。若同时 observe strict_ratio 和 guarded，
前者在零分母坐标上的独立需求会使整个 execute 失败，后者不能取消它。`strict_ratio * 0` 仍须检查除法。

源端 lowering 和 pandas 本地内核均须遵守需求域，不能先在全域计算非法运算再遮罩输出。
本地执行应先选择域再调用内核；源端可用合格的条件/安全操作数改写和独立检查保持同义。
计划须描述分支条件到动态需求域的依赖；准备阶段验证路线能处理这些域，实际域在执行中求出。
无法对某后端/算子保证该语义时，在执行准备拒绝该路线；不退回 eager 全分支检查或吞掉选中分支的错误。

### 3.7 显式重叠成员分桶

多值成员关系是贡献分组合同，不是数值字段读取。使用 DSL 的 `mv.membership(...)` 构造不可变分桶描述，
在 observe 的 by 或 Metric.per 中消费；完整签名由 [DSL §4.3.4](../analysis/lazy-analysis-dsl-redesign.md#434-显式成员关系分桶)拥有。
例如标签成员关系把一条订单贡献映射到多个标签，但不改变订单贡献根、行粒度、量单位或求和授权。
描述只捕获声明字段、显式有序路径和未匹配政策，不捕获 Session、后端表、活动连接或 Python 回调，命名体和匿名脚本可同样使用。
每个事实根逐描述绑定：有唯一显式路径时优先使用并验证它；仅在未提供路径且根与成员 owner 的
完整身份/角色/版本相容时推导恒等绑定。显式自关系合法，多条显式竞争路径拒绝，无效路径不回退为恒等。
所以订单收入 / 标签预算可以分别用订单→标签路径和标签根恒等绑定，不要求伪造标签自环，也不以同值键猜实体相同。
同一描述的多字段是成员行内相关元组；多个显式描述是各自成员集合的笛卡尔积。重复/冲突轴拒绝。
例如一笔100同时属于标签{A,B}和渠道{web,store}，四个完整桶各100，并集原量仍100；真实相关二元成员关系则只产生实际配对。
未匹配政策逐集合应用，exclude 空因子令乘积为空，null 以一个全 None 元组参与；组合后按完整桶键去重。

对每个语义使用位置和目标输出窗口，先保留原过滤/身份选择，再形成 `(完整根贡献键, 完整桶键)` 的集合。
同一对重复只计一次；不同桶可以消费同一根贡献。去重不能跨 Metric 使用位置、输出窗口或不同事实根。
业务重复事件必须由各自完整贡献键保留，不能按量值、业务标签或不完整实体键去重。
每根分别建立成员集合，在逻辑上规约到完整目标键，再执行多根公式；
不能将多根关联后的行数当成各根的贡献次数。物理计划是否先 join 由 §3.7.2 的等价条件决定。
一笔 100 的订单属于 A/B 两标签时，两桶各为 100，A∪B 的原指标总额为 100；对格子做 sum 得到 200 是另一项统计。

未匹配成员按显式 unmatched="exclude" 或 "null" 处理；后者产生保留 NA 键的分类桶，不给数值补零。
Null 分类键沿现有坐标相等性处理。有效成员版本/时点来自已有声明和绑定，不从订单时间或当前时间猜测。
成员分配关系与输出域 K 分开：coordinates 显式携带 membership 绑定并声明完整字段键，
成员 Entity 的 Population.classify 直接读取目标身份分类，给出来源驱动的完整域，多个域可 product。
它不执行描述中的事实分配路径（含自关系）；描述被保留，直到消费事实时再按显式路径优先规则绑定。
Query.population 只给实际逻辑输出域，不能作为补齐从未出现标签的唯一入口；轴投影/集合操作须保留分桶绑定。
外部完整 K 须覆盖被消费的桶键，空桶仍按子指标的 empty/NA 政策完成；内部 per(Population) 选择完整样本域，
不要求覆盖域外贡献。完整域不制造根事实，成员筛选、存在性半连接和重叠分桶不能互相代替。
内部分桶 per(membership) 按同样集合语义生成样本；其 sum 必须证明桶间不交及量可加，mean 等只能表示桶统计，
不能把重叠桶统计冒称去重总体。外部 rollup/share 同样检查分区与分母支持，不自动分摊金额或归一化成总和 1。
并集总计须从本次计划的根贡献和成员集合重新求值，或证明充分信息；完成标签值不足以恢复它。

#### 3.7.1 Fanout 的表达方式与能力边界

fanout 描述关系展开后出现多行的现象，本身不是业务计数或分配规则。Relationship 只拥有方向、键、
角色、版本和映射，不授予重复贡献、金额分配或求和权限；公开表达选择下列业务含义，不选择 SQL 执行算法。

| 业务需求 | 表达方式 | 贡献单位与能力边界 |
| --- | --- | --- |
| 多根总量或比率 | 各来源接收者规约后做 Metric 算术 | 根从依赖取得，每根保留自己的贡献、过滤和时间；共同输出轴必须逐根合法绑定，不按某个主根删去单侧键 |
| 读取关联属性或绑定主体 | 普通 Field.via/time 路径；members 的唯一声明归属路径 | 每条根贡献在消费位置保持单值；多值关系不能用默认去重、首条记录或任意选路修复 |
| 只选有某类关联的事实 | 有限身份域的 Population.preimage，按需要以 in_population 约束组件 | 存在至少一个匹配即入选一次；关联次数不成为贡献次数，不把重叠分桶降为存在性筛选 |
| 同一事实属于多个桶 | mv.membership，在 observe.by 或 Metric.per 消费 | 每个使用位置/窗口内按 (完整根贡献键, 完整桶键) 去重；跨桶可复用，不自动分摊或取得跨桶可加性 |
| 确实统计每条关联或事件 | 将关联记录建模为有完整稳定键的独立 Entity，再 count 或规约其 Measure | 贡献单位是关联记录；两个同端点的真实事件若有不同完整键必须保留，不能用 membership 的集合去重替代 |
| 按业务权重分配金额 | 将分配记录建模为独立贡献根，显式提供金额与权重；为同根派生分配量声明单位/可加合同后规约 | 仅支持已获准的来源/行计算；使用位置已有准入的单值数值读取才可复用。权重、有效期和未分配部分由模型定义；无通用 allocation 参数、自动均分或归一化，weighted_mean 也不是分摊算子 |
| 先按客户/订单等样本组合再统计 | 复合 Metric.per(共同字段或 Population) 后规约 | 先在每个 (k,j) 完成整个子式，再做外层规约；不要求各根先压到最终展示 k，外层 sum 仍需合法可加性 |
| 逐事件配对、最近触点或条件匹配 | 先在受支持的来源建模中明确配对结果及贡献键，再消费该 Entity | 本版 Metric 不提供任意跨根 join、最近匹配、跨行配对谓词或通用归因程序；内部同键样本配对不能替代这些关系规则 |

关联事件与分配记录是普通语义来源，不新增表达式工厂。其来源构造仍受 Entity/Source 的现有合同约束；
无法在该合同中构造的关系程序须由上游提供，不能把裸 Ibis join、活动连接或 Python 回调塞进 Metric。
单值性本身不授予数值路径准入；仅可用于条件/坐标的 via/at 不能借分配计算扩大用途。
此时金额与权重须由分配根的已支持字段提供，必要时在上游形成；不隐式混入订单根的行值。
分配声明不证明来源权重实际守恒；本版不增加自动权重审计或全来源扫描来证明它。

去重对象是贡献身份，不是数值。两笔金额相同的订单仍有两条贡献；sum(distinct amount) 不能修复 join 放大。
多个来源各自的 count_distinct 也不能相加冒充跨根身份并集计数；需要明确共同身份集合及其集合运算，
或建模拥有该去重单位的来源。均值、P95 与去重数可直接在各自合法贡献上计算，
但完成值通常不是后续上卷、跨桶并集或跨源去重的充分信息。

#### 3.7.2 逻辑贡献约束与物理执行策略

基准语义是：对每个来源规约使用位置，按原过滤、身份约束、时间/窗口及成员绑定确定贡献；
在完整输出键及所需内部样本键上完成各组件，再按原式及外层规约次序求值。
这不是“所有来源先聚合到最终展示粒度，再 join”的固定算法。

执行器可在已准入路线内选择单值关系读取、存在性半连接、完整贡献键/桶键集合去重后回取数值、
预聚合后关联、分支独立聚合后对齐或共享扫描等计划，不要求逐个实现这些候选策略。
即使生成的关系计划包含原始来源表 join，也不能让关系乘积隐式替代原贡献；
作者侧任意关系程序仍不因此获得准入。采用具体计划须保持：

1. 每个根、语义使用位置和输出窗口的完整贡献身份、出现次数及桶归属；关联事件不能被集合去重抹去。
2. 全部目标/内部样本键及成员版本、角色和时点，原有来源条件、共同条件、身份选择及窗口范围。
3. 规约与 Metric 算术的逻辑次序；非线性子式不能提前跨样本规约，状态量不能交换空间/时间次序。
4. 原算子的 NA、empty、未匹配、配对和错误检查域，以及显式输出域的覆盖与空桶；不靠 inner join 丢键或全局补零制造等价。
5. 后续消费者所需的充分信息及方法精度合同；合并均值需合格 sum/count，精确分位数需合格样本/分布，去重/重叠并集需身份支持，近似方法只合并兼容状态。

旧 aggregate_then_join 只是其中一种可能的等价计划，不恢复公开 fanout_policy。
若过早预聚合丢失键、身份或充分状态，应保留所需细度或采用其它已准入计划；
无法保持语义或没有具体执行资格时报告该缺口，不回退为重复计数、自动分摊或另一种统计量。
这里的等价针对同一实际输入；不新增普通多根/窗口分支的共同快照、共享扫描或跨 execute 一致性要求。

### 3.8 可命名的窗口 Metric

`base.window(rule)` 是完成 Metric 到 Metric 的显式贡献作用域变换，保持数值类型/单位及原根集合。
语义协议为 `Metric.window(self, rule: ObservationWindow) -> Metric`；rule 为只有无轴规则一种形态的 ObservationWindow；日历与实际区间规则由
[DSL §4.9](../analysis/lazy-analysis-dsl-redesign.md#49-滚动窗口累计与期间比较)唯一拥有。
`@ms.metric` 返回它与直接 observe 匿名表达式完全相同；命名不新增求值根、Session 或隐式日期默认。
无轴规则的 mv 构造器是获准的 source-free 值构造器，定义中可使用；内部依赖中立的时间合同不导入执行器。
窗口构造器不接 CalendarAxis；observe 的 during 只接 TimeScope/CalendarAxis/None，没有临时绑定窗口或兼容别名。
标量 to-date 只由 scope.end 选择满足 left < end <= right 的重置期间，scope.start 不限制历史；
日历输出另检查名义桶唯一包含于重置期间。精确边界不使用减一 tick，规则由 DSL §4.9.2 统一定义。

```python
month_rule = mv.to_date(grain=mv.grain("month"))

@ms.metric()
def revenue_mtd():
    return Revenue.window(month_rule)

anonymous_mtd = Revenue.window(month_rule)
```

observe 一次固定窗口的逻辑绑定：输出日历/标量端点与 W(t) 规则；不冻结实际数据，也不新增读取一致性承诺。
窗口在每个输出坐标 t 上，按 W(t) 重新评价 base 的完整公式；不是对输出格子 cumsum。
该绑定随 Query 运输；待上卷原量的依赖闭包含 window 时，只允许保留原时间绑定的合格空间上卷，
本版拒绝粗化、替换或删除其日历轴，包括单桶和 count=1；标量观察保留原端点。
改评价粒度须新 observe，普通格子统计用 Query.reduce；不能仅凭充分原始输入推导出时间上卷意义。
收入/订单数在同一个窗口内分别重算后相除；不同日期的同一用户在全历史去重指标中只计一次。
所有叶子保留自己的显式业务时间角色、固有条件与根约束；窗口不能补造时间角色、扩大固有过滤或重选 Population。
构造检查 Metric 阶段、规则常量与嵌套约束；observe 绑定评价端点/日历/角色；schema 准备检查类型和路线；
execute 检查真实数据、采样和映射要求。期间未知不阻止捕获定义，但不能在没有端点的 observe 中假设“现在”。

同一依赖路径首版只允许一次窗口变换，已有子窗口的外层 window 拒绝，
即使规则看起来相同也不静默覆盖或再次展开。不同算术分支可以各有自己的窗口，按共同完整输出键组合。
窗口先限制子表达式的贡献，再在该范围完成 per/规约；不能无证明交换 window 与 per/first/last。
状态采样必须能确定所需有限样本域；没有有限起点的 all_history 不能免除采样覆盖义务，应改用明确 since。

窗口重叠不授予时间可加性，approx/empty/strict 等子政策原样保留。条件分支只对被需求的输出端点求窗口，
再将需求展开到该端点所需的完整贡献；窗口内严格检查不能用最终输出过滤取消。
all_history、to_date、trailing_duration、trailing_periods 及 since 的规则进入声明指纹，
实际日历、端点、读取范围程序和方法资格进入绑定指纹；构造与模型收录不读来源、不枚举历史。

### 3.9 五项能力共用的检查边界

五项扩展不增加作者载体。规约方法和 window 仍由 §2.6 的同一 Ibis Deferred 配方捕获；
membership/窗口规则作为不可变语义参数被捕获，不能先当普通 Ibis 数值值 resolve。
@metric 只增加发布记录，@measure 只在行阶段增加允许的业务断言；不能用命名将 window、成员集合或未完成 per 包装成 Measure。
近似算子、empty/include_null、排序、成员字段分组/路径/恒等规则/集合组合/未匹配政策、显式域和窗口规则
都属于非透明语义事实；仅声明名称仍透明。恒等绑定的实际角色/版本与消费根在绑定指纹中保留。
相同裸 Ibis 运算在不同桶/窗口/政策中的使用位置必须分开，不能用 op 相等性去重检查。

所有分支先进行静态依赖、kind、单位、根与政策检查，命名和匿名相同；schema 闭包包含所有静态分支。
动态需求按 §3.6.5 传播：first/last 的选择不隐藏其完整样本域中的 strict 错误；近似不免除输入检查；
membership 仅扩展被需求桶的合格贡献；window 仅扩展被需求输出端点的 W(t)。额外独立观察仍增加自己的检查域。
声明计算指纹记录上述规则和默认值；绑定指纹还记录真实 schema、成员/日历绑定和已选算法，
但动态成员键、选择锚点及数据结果每次执行重新计算，不作为跨次业务答案缓存。

## 4. 发现、类型与错误

Entity 等元数据对象及 Catalog 定义条目保留 bounded repr/show；
字段的独立元数据披露由定义条目提供，作为计算输入的 Field 与 Measure/Metric 使用同一延迟协议；
Measure/Metric 数值作者值统一为原生 Deferred，不再承诺自身有 Marivo show/contract。
Catalog 定义披露及 Query.contract 披露阶段、贡献根/角色、
单位与授权来源、时间、节点政策、域依赖/归属、所需绑定和模型收录阻碍，不执行或枚举域。
NumericRowExpr、MetricSamples、图节点和类型别名留在所属能力下，不扩张顶层导航。
Catalog/Query 的指标贡献披露只列出能定位具体根的语义角色；Divide 的 N/D 是计算依赖，不自动构成主体路由。

Field.via/at 的路径与精确时刻继续由
[DSL §4.1](../analysis/lazy-analysis-dsl-redesign.md#41-session字段时间与路径) 和
[§4.1.1](../analysis/lazy-analysis-dsl-redesign.md#411-精确时刻与版本选择) 拥有。
单值路径不凭同名字段建立；时间默认在构造时冻结，不捕获活动 Session。

错误说明预期阶段、收到的定义与具体位置，并从真实状态给出修复：
行乘积缺求和权限应明确声明新 Measure；@metric 返回行值应显式规约；
@measure 返回 Metric 不得自动下沉；per 缺贡献到 (外部格子, 内部样本) 的绑定应补合法轴；模型依赖不可持久解析
应保留临时使用或重写依赖。不给自动转换、隐藏 join、任意回调或回源补状态的修复。
已知不相容单位的行/Metric 运算（如 CNY + USD）在最早能检查的构造/消费边界以 SemanticError 的具体子类拒绝，
列出双方单位和表达式位置，要求显式完成合法换算后重新组合；不隐式换汇或修改单位标签。
Query Column 的同类失败由 AnalysisError 拥有。未知单位保持未知，不假定无量纲；
确需后续绑定的信息按[检查时机](../analysis/lazy-analysis-dsl-execution.md#24-检查时机与能力披露)处理。
完整披露由 [执行与披露 §2](../analysis/lazy-analysis-dsl-execution.md#2-agent-读取与错误契约) 拥有。
原生 Deferred 可能在进入 Marivo 消费边界前只捕获语法；不保证每次 Python 运算立刻有 Marivo 异常。
contract 分开显示已知语义事实、未绑定 physical dtype、待检查义务和已知能力缺口；不得把 pending 显示为通过。
命名和匿名 Deferred 均由其语义消费者（如 Query.contract）披露同一事实；Catalog 条目补充已收录声明身份。
不把 `.show()` 捕获成可执行数值方法，不为获取披露要求用户先声明名称或做转换。
Ibis 原生构造自身抛出的类型错误与 Marivo 消费时的结构化错误须区分，不伪称能拦截任意脚本中的 Ibis 调用。

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
| semantic 表达式与声明 | 受控语义叶子、统一延迟捕获、行/指标阶段、业务权限、稳定身份 |
| loader / Catalog | 独立持久解析、依赖与名称检查、实际定义身份和发现 |
| 共同私有定义图 | 语义边界、规约、内部样本、域依赖、membership/window；引用原生 Ibis 数值子图与语义注解 |
| Ibis 与来源 lowering | 原生数值 DAG/类型推导；合法 schema/关系上的计算、政策保持和后端资格；交付 pandas |
| analysis | 观察绑定、Query 组合、统一 pandas 本地执行与中间信息、终端 Artifact 产出 |
| Help / contract / errors | 静态导航 / 当前定义或状态 / 具体修复 |

复用现有图、compiler、发布和恢复链，不建第二 Runtime、虚拟共同关系或完整 Ibis AST 镜像。
共同定义图仅在进程中保留规范化计算、声明授权、实际依赖身份/指纹、节点政策与规则版本；这些记录不强制固定输入的原版本。缓存按实际消费的定义与绑定区分，不能因取消旧版本拒绝而复用错误缓存。不做分析结果图编码，
不 pickle callable/Ibis/Catalog。Population 采用依赖中立的字面量/来源或 Query 程序描述，
semantic 不导入 analysis 执行器，实际归属、读取边界和循环检查沿依赖保留。
来源无法下推的计算只在具有合格 pandas 内核时进入本地阶段；保留 Ibis operation 到这些内核的执行分派，
包括 dtype、NA、政策和检查域适配，语义政策不因执行位置改变。
PyArrow 只负责终端产物存储/读取，DuckDB 只作为来源后端，不增加另一套表达式语言或本地选路。
完整执行合同见[执行 §1.5](../analysis/lazy-analysis-dsl-execution.md#15-单一执行路线与终端读写)。

### 6.1 移除自定义数值 AST，保留语义边界

v0.36 替换当前 Measure/Metric 自建的数值表达式层，不保留它与 Ibis 的长期双轨。
自定义 Row/Metric 的 Negate/Add/Subtract/Multiply/Divide、比较、布尔与一般数值函数节点应移除，
连同旧节点的重复构图/翻译入口，由 Ibis 数值 DAG 承担。保留基于 Ibis operation 的语义检查、政策改写、
源端 lowering 适配及 pandas 内核执行分派；除法政策按语义使用位置关联，不能只按 Ibis 节点相等性存放。
延迟配方复用 Ibis 构造机制，仅有必要的语义方法截获，不再定义一套 Marivo 数值 opcode/AST。

| 现有内容 | 目标处置 |
| --- | --- |
| Measure/Metric 数值 AST、逐算子公开包装、第二套数值类型推导 | 以 Ibis 构造/operation DAG/类型推导替代，切换后删除旧 AST 及依赖旧 AST 的分派入口 |
| 基于 Ibis operation 的检查、政策改写和本地内核分派 | 保留；这是执行适配，不重新定义数值节点或另一套计算语言 |
| NumericRowExpr 等输入合同 | 可作为阶段约束保留，不再代表独立数值语言；公开类型须准确描述延迟输入，不能用 Any 隐藏 |
| Field/Measure/Metric 语义身份、业务声明、贡献与时间绑定 | 保留为受控叶子、语义边界和注解；不是重复实现 Ibis 运算 |
| SourceAggregate、共同配对、per/内部样本、贡献过滤、membership/window | 保留 Ibis 不拥有的语义合同及计划边界；原生 reduction 归一到这些边界，不再并存两个聚合意义 |
| Query/Population/Artifact 的自身结构 | 继续由原 owner 管理，本次不删除分析代数或终端边界 |

定义图仍需把来源规约、Metric 数值子图和内部样本按依赖连接；这不是将完整 Ibis AST 翻译成另一套图。
Ibis 叶子与语义输入/规约输出间用稳定绑定映射连接；多源子指标的已规约输出是完成值槽位，
不是拼接原始事实的虚拟共同关系。下表中的 Row/Metric Ibis numeric subgraph 是引用边界，不是新数值节点族。

#### 6.1.1 执行内信息需求

| 私有节点族 | 直接求值 | 本次计划内合并所需信息 |
| --- | --- | --- |
| Field/定义引用、有限数值常量 | 保留根、路径、单位和版本；常量不制造来源 | 保存精确绑定和规则 |
| Row Ibis numeric subgraph | 同一贡献根上的准入数值计算、局部政策与规则注解 | 保存行定义不等于保存原行；需要实际输入或规约充分状态 |
| MeasureDeclaration | 名称/ai_context 与语义事实分开；可推导事实继承，只记录新增单位/权限断言 | 记录授权来源和版本，不修改依赖原合同 |
| SourceAggregate / EntityCount | 合法贡献单位上的定义指定规约，保留 exact/approx 与 empty | sum/count/min/max/mean 等合格状态；nullable sum 需有效值支持；distinct/quantile 或 sketch 另验资格 |
| PopulationDependency / ContributionFilter | 显式域绑定一个完整贡献作用域 | 类型、字面量/程序、匹配和必要贡献状态 |
| Metric Ibis numeric subgraph | 完成各指标后逐坐标计算；保留非线性顺序与 Divide 角色 | 各依赖在目标坐标的合法充分状态 |
| SourceWeightedMean | 同贡献域共同有效配对 | 配对乘积和、权重和、支持/覆盖；不能用独立权重和 |
| PerReduce / PerWeightedMean | 各外部 k 内先按 J_k 完成子指标，再规约/配对 | 按需计算展开键 (k,j) 及必要子状态，或已证明等价的充分信息；不能套用来源 mean 的摘要 |
| Membership binding | 每根完整贡献键到桶键的集合，保留 unmatched 与版本 | 同桶对去重、跨桶重叠；并集重算需根身份/充分信息，不能相加完成桶值 |
| Window binding | observe 固定每个输出端点到 W(t)，在其中重新评价完整子式 | 后续操作保留根、过滤、时间绑定与出现位置；本版拒绝时间轴粗化/替换/删除，不把完成日值当充分信息 |

声明名称是独立元数据，不再引入 SourceBody、Ratio 或旧 Over 与新节点并行的计算入口。
时间样本规约是 PerReduce 的受约束用法；无任意序列节点。
直接可求值与可按摘要合并是不同资格，不因同名 sum/mean 就默认支持。
表中的定义属于 Query，组件只服务本次计划，最后一次消费后释放；Artifact 不持有它们。
只有实际消费者需要且路线获准时才计算细网格/精确分布，不为未来未知续算默认保留；输出边界由
[执行 §1.2](../analysis/lazy-analysis-dsl-execution.md#12-默认保存信息) 拥有。

规范化、类型/单位/权限检查、lowering、必要检查、进程内组件与输出数据往返均需独立验证；不新增结果图 codec。
两层指纹按 §6.2 分离；规范遍历/摘要不构成可执行的镜像 AST。
未知节点/规则版本必须定位失败，不降级为任意 Python、后端默认或旧 DSL 别名。

### 6.2 声明计算指纹与绑定执行指纹

物理类型未知时没有最终 Ibis operation 图，不能从尚不存在的图计算声明阶段的身份。
采用两层计算指纹；声明名称/ai_context 等元数据身份继续独立，语义使用位置也不等于内容指纹。

| 层 | 输入与时机 | 用途与限制 |
| --- | --- | --- |
| 声明计算指纹 | source-free 捕获/收录时，对规范 Ibis 延迟配方、实际捕获的语义依赖、单位/权限断言、操作数角色、语义方法及默认化政策作摘要；包含所用规范化规则版本 | 比较逻辑定义、依赖失效与声明计算身份；不证明物理类型或执行路线，不单独作为已绑定计划缓存键 |
| 绑定执行指纹 | execute 准备后，在前者基础上纳入实际消费定义、真实 schema、规范 Ibis operation 图、语义作用域/政策与需求域程序、观察绑定、规则和所选后端/本地内核资格版本 | 区分实际计划及其可复用条件；不缓存业务答案、不证明不同执行读取同一数据；动态域仍每次求值 |

延迟配方摘要只遍历获准 Resolver 结构及其不可变参数，不能执行 resolver 来求摘要，也不能用 repr、
Python hash、内存地址、随机关系别名或任意 callable 内容。获准构造函数以稳定的语义符号标识表示。
命名/内联/别名规范到同一计算；显式默认与省略默认也归一，语义断言或 error/null 政策不同则不同。
不追求任意数学等价：不同配方不因碰巧同值就共享声明身份。实际绑定依赖与声明捕获不同时，
绑定指纹描述实际消费的依赖，仍不因原定义版本变化单独拒绝输入。

例如 `x.cast("float64")` 的延迟配方不变，仅来源物理类型由 int64 改为 float64 时，
声明计算指纹不变；真实 Ibis 图可能由 Cast 变为 Field，schema 和绑定执行指纹必须变化。
不能因最终 cast 被消去而删除原始配方的静态义务或使用记录；schema 已知也不改变作者侧载体。
计算图摘要与执行图优化仍受语义等价约束，不能将相等 Ibis operation 的不同政策摘要合并。
没有稳定规范摘要规则的调用不进入可收录定义或绑定计划缓存；不能用不稳定摘要伪装可复用。
这些摘要只描述进程内定义/计划，不新增图持久化 codec，Artifact 仍不携带延迟配方、Ibis 图或内部组件。

## 7. 实施与可核验条件

v0.36 原生 Ibis 与 v0.37 能力补全独立于既有 R1–R8 实施进度；不重置已完成阶段，也不据旧阶段检查宣称本修订完成。
实施顺序为：统一 Deferred/接收者适配与来源无关探针 → 图语义规则与声明验证 → 自动 schema 绑定和执行路线 → 披露及发布面切换。
第一步必须同时证明 §2.6 的命名、匿名、顶层函数和链式规约；若某 Ibis API 不支持安全延迟，明确拒绝该能力，不能退回仅命名可用的源码改写或新增桥接 API。
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
五项能力的新增验收见[场景验证 §9.5](../analysis/lazy-analysis-dsl-scenario-validation.md#95-五项能力补全与等价迁移)。
fanout 的贡献语义与等价计划反例见[场景验证 §9.6](../analysis/lazy-analysis-dsl-scenario-validation.md#96-fanout-语义与执行策略边界)。
原生载体的针对性验收见[场景验证 §9.4](../analysis/lazy-analysis-dsl-scenario-validation.md#94-原生-ibis-表达式与自动延迟类型绑定)。
Ibis 技术依据：[Deferred 与 Value](https://ibis-project.org/reference/expression-generic)、
[operations](https://ibis-project.org/reference/operations)。这些能力说明不能代替 Marivo 适配和路线验收。

## 附录 A：被本版替代的设计

上文是 v0.37 的目标合同，不提供兼容 API；在真实实施切换前，已提交的 current 文档和 live Help 仍约束现行实现。

### A.1 拟替代的已提交合同

下表明确能力是否保留、由谁拥有以及旧声明如何处理；不能把未列出旧参数解释成静默忽略。

| 已提交合同与 owner | 目标合同与能力处置 | 旧声明的处置 |
| --- | --- | --- |
| [当前封闭表达式合同](semantic-object-model.md#common-contracts) | Measure/Metric 采用受控叶子上的原生 Ibis 数值表达式及自动延迟类型绑定；Entity/Source、Dimension/TimeDimension 保留其 owner 合同 | 接受合法 Metric 阶段的受控 Ibis 延迟输入；仍拒绝表参数、无语义来源的外部 Ibis 图及已退役工厂；不恢复旧任意表体 |
| [fanout_policy 与 aggregate_then_join](semantic-object-model.md#grain-root-entity-and-fan-out) | 按 §3.7.1 显式选择存在性、重叠桶或独立关联/分配贡献；物理 join/aggregate 次序按 §3.7.2 证明等价，不恢复策略参数 | 旧参数拒绝；保留贡献单位、完整身份、成员集合、时点、过滤、未匹配政策及组件顺序；不能把桶改成半连接、关联事件改成集合，或补上未声明的分配/匹配规则 |
| [empty 声明政策](semantic-object-model.md#fixed-null-and-empty-contracts) | 来源/内部样本 sum 接 empty="zero"（默认）或 "null"；保留忽略 NA 后无有效值的区别 | 旧声明参数和 ms.empty 工厂拒绝；把政策移到每个对应 sum 节点，不能只删除参数 |
| [status_time_fold 与 aggregate fold](semantic-object-model.md#sampled-semi-additive-metrics) | 默认 fold 改为 per(状态 TimeField).reducer，包含 first/last；保留先空间后时间、排序及覆盖 | 旧参数拒绝；展开实际生效的继承/覆盖政策，保持采样轴、范围、include_null 和锚点，不能改成逐实体最后记录 |
| [跨 Entity 的 time_via](semantic-object-model.md#metric) | 保留明确的跨 Entity 时间读取能力，统一为 time=TimeField.via(path)，按 §2.1 捕获单值路径；不增加观察时路由覆盖 | time_via 参数拒绝；错误给出原时间字段和有序关系路径的改写位置，不猜路径或改为根 Entity 的其它时间 |
| [approx_* 与定义拥有精确性](semantic-object-model.md#definition-owned-aggregate-exactness) | 原生 approx_nunique/approx_median/approx_quantile，来源和内部样本共用定义精确性合同 | 旧工厂拒绝；保留字段、q、过滤、NA 与误差承诺，路线另验；禁止精确/近似静默替换 |
| 旧 ms.cumulative 命名累计 | @metric 返回 base.window(rule)，与匿名窗口共用求值；all_history/to_date/trailing_duration/trailing_periods/since 见 DSL §4.9 | 旧工厂拒绝；保留基础指标、时间角色、评价端点、过滤与重置；固定时长不能换成日历期间 |

声明校验/模型加载必须在业务来源读取前拒绝已移除的参数、工厂和表达式体。
错误列出声明位置、收到的旧语法、期望的目标形状及真实可用修复；不双读、不迁移、不静默忽略或重解释。
这项规则适用于 Python 模型声明，独立于旧四态 Artifact/图快照格式的拒绝；正常新声明仍按 §2.4 检查依赖闭包。

用户模型采用人工改写对照与逐声明 typed repair，不提供 codemod、兼容加载或自动写回：

| 旧写法 | 目标改写与边界 |
| --- | --- |
| ms.aggregate / ms.count | 从已解析 Measure/Entity 选择原计算对应的接收者规约，如 Amount.sum(time=PaidAt) / Orders.count(time=PaidAt)；原过滤和时间路径分别保留，不统一替换为 sum |
| ms.ratio | 组合已构造 Metric，使用 numerator / denominator 或显式 divide(..., zero_denominator="error")；作者按 §3.4 明确新政策，不能猜测旧缺失状态的等价写法 |
| @ms.metric(entities=...) 与 Ibis 表参数体 | 改为零参数体，返回由已声明字段/Metric 构成的表达式；来源根从依赖取得，多根独立规约，不能将旧表 join 机械搬入新体 |
| fanout_policy | 按 §3.7.1 选择 preimage、membership 或明确的关联/分配 Entity；不机械替换成某个执行策略，不推导旧定义未给出的权重或配对 |
| empty / ms.empty | 旧 empty=null 改为对应 sum(empty="null")，zero 改为 sum(empty="zero")；保持 nulls、同一贡献集与空组输出，其他规约无此参数 |
| status_time_fold / fold | 展开实际生效政策为 base.per(StatusTime).first/last/mean/...；first/last 显式确认 include_null、排序与覆盖，不再靠叶子默认 |
| approx_count_distinct / approx_percentile | 分别改为 Field.approx_nunique() / NumericInput.approx_quantile(q)；approx_median 同名；不是精确规约的别名 |
| ms.cumulative | 用 @metric 命名 base.window(rule)；anchor=None 对应 all_history，grain_to_date 对应 to_date，旧固定时长 trailing 对应 trailing_duration |
| ms.bind | 从新 Measure/Metric 体移除并直接使用已构造字段；保留的 Ibis 字段声明体仍按 §2.3 的受限合同使用 |

#### A.1.1 等价迁移的判定条件

这里的迁移指作者改写定义，不是 loader 自动兼容。五项能力必须在相同输入、业务身份和观察绑定下比较，
名称相同、表达式可编译或某组样例数值相同均不足以证明等价。

| 能力 | 必须保持 | 不得宣称等价的情况 |
| --- | --- | --- |
| first/last | 先空间后时间；同一采样轴/时点域、升序、选择锚点和 NA 行为；旧覆盖要求 | 每实体不同末次时点求和；缺测被跳过；边界 as-of 被期间最后样本替代 |
| 近似规约 | 同一输入/过滤/去重身份、q、精确性意图及旧有误差承诺；类型/单位/NA 一致 | 改成精确算法、不同统计定义；无证据宣称同误差界或逐位数值相等 |
| 重叠分桶 | 同一根贡献键、完整桶键、路径/恒等绑定、联合关系或独立集合乘积、输出域、成员版本/时点、同桶去重、未匹配行为与组件顺序 | 半连接、分摊权重、原始 join 放大、加总标签行当并集；旧定义若确实计关联次数则须保留其独立关联事实根，不能偷偷改成集合 |
| empty=null | 忽略 NA 后无有效值返回 NA，有效零仍为零；同一 sum 节点和输出域 | 只删旧参数、nullif(sum,0)、把缺桶补成存在空桶、用 scalar NA 加法代替合并 |
| 命名累计 | 同一基础公式、时间角色、输出端点、历史下界/重置/持续时长、时区、过滤及空/NA 政策；distinct 保留下述 first-seen 归属 | 报表起点重置全历史、每日比率/去重数累加、固定 86400 秒改自然日、重复窗口覆盖 |

旧 count_distinct 累计合同把身份归到其首次出现桶，再对该归属计累计；迁移须保留这层归属，不能只在每个窗口重做 nunique。
固定同一合法贡献根、身份键、分组/成员绑定、过滤及时间角色，令 U 为不受显示起点和窗口下界截断的合格历史，
first(u)=min{time(c): c∈U 且 identity(c)=u}。在旧首次桶归属与评价边界相容的条件下，
全历史结果为 #{u:first(u)<e}，可写成精确 nunique.window(all_history())；
重置/滚动 first-seen 结果为 #{u:a(e)≤first(u)<e}，其中 a(e) 分别由 to_date/trailing_duration 给出。
它不同于 #{u:存在 a(e)≤time(c)<e} 的活跃去重；原生 nunique.window(rule) 表示后者，不作为前者的自动迁移。
这里规定的是旧文档 first-seen 合同的等价目标，不据此宣称旧 Runtime 已实现全部 anchor 组合。
如果具体旧模型通过固有条件缩小了 U，迁移保留该条件；不得把观察显示范围偷偷变成首次出现的历史范围。

无需增加 first_seen 工厂或自定义数值 AST。对每个非空身份 u，定义精确计数 H(u)=count(U,time<e)、
W(u)=count(U,a(e)≤time<e)，则 W(u)>0 且 H(u)=W(u) 当且仅当首次出现位于窗口。
如下为同一受控 Ibis 配方的组合（UserId/EventTime 已解析，规则为 to_date 或 trailing_duration）：

```python
visits = UserId.count(time=EventTime)
history_visits = visits.window(mv.all_history())
window_visits = visits.window(rule)
first_seen_in_window = ibis.ifelse(
    (window_visits > 0) & (history_visits == window_visits),
    1,
    ibis.null(),
).per(UserId).count()

@ms.metric()
def new_users():
    return first_seen_in_window
```

计数忽略空 UserId，外层 count 只数非 NA 命中身份，单位为1；不对数值编码身份授予求和权限。
该配方是普通表达式，first_seen_in_window/new_users 的名称不增加语义种类或执行要求。
迁移等价以 H/W 实际评价同一个合格历史 U 为前提；U 包含身份、过滤、成员映射与版本等相关输入，
可以由稳定输入、合格共同输入路线或已有后端一致性保证满足，不能仅凭相同定义/变量/哈希证明。
本版不为此新增 first_seen 工厂、特殊语法识别、快照 API、自动一致性探测或所有窗口分支的共享扫描要求。
若前提不成立或未获证实，该配方仍按普通读取合同构造和求值，但不得声称其结果等价于旧 first-seen；
这是一条迁移证明条件，不是新增的运行时拒绝政策。读取合同由
[执行 §1.1.1](../analysis/lazy-analysis-dsl-execution.md#111-共享逻辑节点与执行内输入)拥有。
缺少合法身份/字段绑定仍按现有语义检查拒绝；approx 计数不能支持上述精确等价证明。
两个窗口位于不同分支，外层 per/count 不是再次 window，符合单路径不嵌套规则；命名、内联和匿名复用一致。
first(u) 归属还须与旧首次桶边界相容；若新下界切穿旧首次桶，不能以精确时点过滤声称保留旧按桶归属。
例如 A 在09-30和10-02出现、B在10-03首次出现，窗口[10-01,10-04)的活跃去重为2，首次新增为1；全历史为2。
若历史被上游截断，只能相对于可用 U 判定首次，不新增业务历史完整性承诺。
旧 trailing 若限制窗口跨度为输出粒度整数倍，保持原合法参数和端点可迁移；新设计支持更多组合不改变旧输入的含义。
旧允许的部分历史与新实际可验证采样/日历检查分别评估，不能靠补零或扩大来源伪造覆盖。
明确近似定义的合同等价不承诺不同算法结果逐位相等；源类型、舍入、误差承诺和实际执行资格仍需验证。

错误至少定位当前被拒声明及对应旧语法；不为一次收集全部模块错误执行无效模型，
也不承诺自动生成业务等价的新定义。旧语法即使参数值恰好等于新默认也不接受。

切换实施时须同步上述 current owner、Help、示例、错误和英中发布文档。
[AGENTS.md](../../../AGENTS.md) 的 Ibis 表达式规则及两个 packaged skills 的编辑需要显式用户授权，
本次提案修订不修改这些受保护文件；CLAUDE.md 是 AGENTS.md 的符号链接，不形成第二份规则。

### A.2 提案演进

v0.37 恢复五项定义能力，但不恢复旧工厂/默认参数，也不开放任意关系与数值 AST。

| 早期方案 | 当前目标 |
| --- | --- |
| 分析 Domain、domain()、in_domain(...) | Population、population()、in_population(...)；语义业务 Domain 保留 |
| ms.aggregate/count + Ibis Metric 装饰器两条语言 | 来源接收者规约、Metric 组合；零参数声明体返回同一表达式 |
| ms.ratio 与普通 / 分开 | / 和 divide 是同一节点、角色与默认政策 |
| named 或装饰器显式完整名称 | 装饰器捕获函数局部名；模型收录限定命名空间，输出标签归 observe |
| 装饰器全局 nulls/zero_denominator | 政策归每个实际算子，声明不覆盖 |
| fold 参数隐含第二规约阶段 | per(采样轴).quantile 等显式表达次序 |
| over(Domain) 要求先 where 再覆盖全部贡献 | per(Population) 直接选择内部样本；外部输出覆盖合同不变 |
| 有限自定义行算术、显式 ms.ibis 桥接建议 | 原生 Ibis 数值语言，命名/匿名共用受控 Deferred；自动物理类型绑定；Measure 仍拥有业务授权 |
| 禁止所有 runtime 域或构造时检查模型持久化 | 显式不可变依赖合法；独立持久解析在模型收录检查 |

窗口、身份、默认来源信任和 pandas 交接继续由 DSL 及执行文档拥有；
早期 Result 续算、固定 Artifact 键域与 origin/fixed/cold 已由终端 Artifact 边界替代。
能力保留、退役与旧声明拒绝以 A.1 和正文为准；参数减少不作为计算等价证明。

v0.28 复审修正：两种装饰器从函数名推导局部声明名，模型收录限定完整身份；Measure 继承可推导事实、说明复用 ai_context；per 的样本在每个外部格子内建立，
按绑定能力校验域形状；权重归一化不要求业务可加性；Metric 常数平移不继承完成值可加性。
示例与验收同步，模型/runtime 的 AOV 政策及字段路径使用同一合同。以上为历史提案说明，当前实施以各 owner 为准。
