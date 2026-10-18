# Lazy Analysis DSL 定义

Date: 2026-10-17

Status: v0.37 DSL 与实施合同。基于 `405bd006f8`，原生 Deferred、Population、Query 组合与 Store 10
终端执行/读取已完成约定的本地实施、常规门禁和独立业务验收。新数值 Query 来源路线仅准入 DuckDB；
其它后端需要代码路线扩展及 Runtime 验证。完整 slow、远端 Runtime 和 release 验收未完成；
目标条目不授予未准入的方法/类型资格，具体边界见 [Python Analysis Design](python-analysis-design.md)。
v0.37 补齐 first/last、approx_*、membership 分桶、来源/内部样本 sum(empty=) 及命名窗口 Metric；规则与语义提案同步。
2026-10-17 fanout 修订约束贡献身份、出现次数和规约次序，不固定 join/aggregate 顺序；
各类表达与能力边界由[语义 §3.7.1–§3.7.2](../semantic/expression-model-redesign.md#371-fanout-的表达方式与能力边界)拥有，其物理路线以实际准入和验证为准。
v0.36 与语义提案 §2.6/§3.6 同步：命名和匿名 Measure/Metric 直接使用受控原生 Ibis 数值表达式，execute 自动绑定物理类型。
本文仅定义目标 DSL 的对象、签名、绑定规则与求值语义；v0.28 统一行/Metric 计算表达式，
装饰器只负责声明，政策归各算子；显式不可变域依赖的持久解析要求归模型收录。
v0.29 将分析总体统一命名为 Population，同步类型、取总体方法和成员测试名称，计算语义不变。
v0.32 将 execute 的产出统一为终端 Artifact；DSL 只组合 Query，执行后交 pandas（见 §2.3）。
执行分工继续为 Ibis 源端计算、pandas 统一本地执行、PyArrow 存储和读取。
v0.33 统一行/Metric/Column 除法及 share、compare、weighted_mean 的零分母政策，默认 null、显式 error；
完整政策由 [语义提案 §3.4](../semantic/expression-model-redesign.md#34-样本政策的归属) 唯一拥有。
v0.34 对齐一般比较、业务份额和数学累计边界，统一时间样本规约术语，并补齐类型、读取证据和 Population 披露。
v0.35 补齐时间输入、名义/实际桶、ordinal 与闰日对应合同，明确期末规约范围，并移除资源预算设计。
本轮补齐公开类型与日历导航引用；首版不提供外部业务缺口声明或消费，来源信任边界见 §4.9.3。
v0.30 用 nullable value 取代四态 Cell：sum 的空输入与全 NA 默认均为 NA，来源/内部样本显式 empty="zero" 时为 0；普通 NA 可被规约忽略，
非法计算和实际覆盖检查失败使本次 execute 失败。不要求调用方逐次声明完整性。
当前实现仍由 [Python Analysis Design](python-analysis-design.md)、
[Operators and Frames](operators-and-frames.md) 和 [Session State and Runtime](session-state-and-runtime.md) 约束。

| 内容 | 文档 |
| --- | --- |
| 来源定义、Metric 构造与 authoring | [语义表达式提案](../semantic/expression-model-redesign.md) |
| 数学模型与定律 | [核心分析代数](algebra-theory.md) |
| 执行图、保存/恢复、Agent 披露与导出布局 | [执行、保存与结果交接](lazy-analysis-dsl-execution.md) |
| 多轮组合与完整代码 | [多轮分析示例](lazy-analysis-dsl-examples.md) |
| 算子取舍、业务场景、数值 oracle 与验收判据 | [场景验证](lazy-analysis-dsl-scenario-validation.md) |

## 1. 范围

Metric 定义业务计算及显式不可变依赖；Session.observe 一次绑定期间、分组、共同条件与可选身份约束；
Query 保持 lazy，所有 DSL 组合发生在 execute 之前；显式 execute 返回终端 Artifact。
Artifact 只读、展示和导出，不进入新的 DSL 图。完整输入求值是指标定义，摘要只是本次执行的等价路线。
同一次 execute 可以包含多个 datasource 的读取和本地阶段，限于已准入的后端、类型和交接路线；
未具备路线时明确报告执行能力缺口。来源经 Ibis 返回 pandas，本地计算统一消费本次 pandas 数据。
PyArrow 不承担 DSL 求值，DuckDB 只作为来源后端；具体交接与资源边界见
[执行分工](lazy-analysis-dsl-execution.md#15-单一执行路线与终端读写)。

本版包含 Population、显式 membership 分桶、观察、选择/Top-N、对齐/比较、参照/份额、原指标上卷与端点细化、
sum/mean/row_count 结果规约、原指标窗口和期间对应。普通分类、描述/配对统计和格子滚动统计交 pandas。
不包含 Event、journey、lifecycle、funnel、Anchor、retention、序列回放、预测、抽样或变化归因；
不提供自动比较下钻或外部结果回灌。Measure/Metric 的原生 Ibis 数值语言由语义提案拥有，
不新增 ms.ibis 桥接、ms.require 或手工类型准备 API，也不因此扩展 Query Column 的原生 Ibis 接入。

以下能力不在本版合同内；后续问题通过新 Query、调整算子顺序、在语义层声明唯一路径或 pandas 处理。
Artifact 的终端边界是设计约束，不是待补齐的结果能力：

- execute 保留请求、Artifact 回灌、执行后 DSL 续算与固定离线细比较；
- Query.window 重设窗口、显式参照份额 share(of=...)、rollup/drill 的显式 Population 形态；
- observe 主体路由补全、期末左极限版本锚点及通用期末状态 closing 规约；
- 不含当期回看窗口、命名期间映射与父期间内日序配对；
- 结果 Column 规约的 nulls/empty 选项；来源/内部样本 sum 的 empty 政策见语义 §3.4。

### 语义定义更新原则（2026-10-15 批准）

分析不强制语义对象绑定构造时的精确定义版本，也不因同路径定义更新而单独拒绝输入；
分析过程中语义定义更新的后果由 agent 或用户承担。引用解析处使用消费者完整作用域的定义，
已构造表达式仍组合其不可变定义与事实，不新增 live refresh 或 module replay。
本原则不取消定义身份/依赖指纹、正确的缓存键和失效规则，也不自动删除持久化来源记录或其它记录；
实际消费的定义和绑定不同，不得复用为同一计算的缓存。Artifact 仍只保存终端输出，
不会因此新增定义图、Ref/指纹闭包或自动模型版本核对。
kind、贡献根、完整身份键、粒度、单位、权限、时间角色、null、实际覆盖等正确性继续由原 owner 校验。
业务 snapshot/validity 历史选择及轴角色不是被取消的“定义原版本强制绑定”。
共同图和缓存边界见[实现架构 §4.3](analysis-implementation-architecture.md#43-身份边和指纹)，
物理闭包阶段见[语义表达式设计](../semantic/expression-model-redesign.md#definition-changes-during-analysis-approved-2026-10-15)。

### 1.1 普通分析的最短路径

```python
import marivo.analysis as mv
import marivo.semantic as ms

catalog = ms.load()
s = mv.session.get_or_create("apac-sales", report_timezone="Asia/Shanghai")
revenue = catalog.metrics.get(ms.ref.metric("sales.revenue"))
region = catalog.fields.get(ms.ref.dimension("sales.orders.region"))
week = s.time_scope(start="2026-10-05", end="2026-10-12")
daily_sales = s.observe(
    {"revenue": revenue},
    where=region == "APAC",
    during=week.by(mv.grain("day")),
).execute()
daily_sales.show()
```

Metric 已拥有业务时间角色与政策，不要求调用方再指定 Entity 或身份域。
日历提供七个输出格；成功读取后的空日按 Metric 的空输入规则完成，不因有键就一律补零。
需要约束一批身份时传 members=identity_population；完整输出由 by=population、身份轴或日历明确请求。
Population 统一身份、字段与日历坐标，普通分组不因有身份约束自动补齐全部分类。
普通观察默认信任声明的数据源；来源完整性与实际覆盖检查的边界见 §4.9.3。

## 2. 对象模型与 lazy 状态

Q = Observe({name_i: m_i}, context)，context 可选绑定身份域 P。
无身份约束不等于空 P 或默认 Entity 全集；各组件形成自己的合法贡献。
同一个 Population 可作身份约束或完整输出域，但这两个使用位置独立。
Artifact 固定本次输出数据与实际产生的 Finding/Evidence；不携带计算上下文。

| 对象 | 唯一职责 |
| --- | --- |
| Metric | 不可变计算及显式域依赖；不捕获活动 Session、可变外部状态或观察窗口默认 |
| Query | 有明确依赖与最终绑定的 logical 问题；execute 返回 Artifact |
| Artifact | 不可变分析产物；读取、展示和 to_pandas，不是 DSL 输入 |
| Population | 分析总体：本次关注的业务对象或分析单元集合，以完整类型化轴和有限键集表示；单 Entity 身份轴是其特例，可作约束或请求域 |

Population 可表示客户/订单名单、地区 × 月份等分析坐标或指标内部样本；
“总体”不承诺来源全集、统计代表性或数据完整性。语义层的业务 Domain 仍表示声明所属命名空间，
保留 domain= 与 ms.ref.domain(...)。下文“身份域”“输出域”“样本域”描述数学集合或使用角色，不另设公开类型。
分析侧统一使用 Population、Query.population() 和 in_population(...)，不保留旧分析名称的兼容别名。

Field 是语义字段，Column 是当前 Query 的量表达式；两者不能混入另一阶段。
Session.members 从来源选择身份并返回 Population；Session.query(population) 返回仅键 Query。
Query.population().project 提取尚待执行的身份/坐标依赖；query(population).execute() 只产出键表 Artifact，
不能再取 Population。Session.values 返回显式字面量参照 Query，不是 Artifact 导入入口。

### 2.1 不同身份不能互相替代

业务身份、贡献出现、完整坐标、计算定义、逻辑节点和 Artifact ID 分别定位；
计算定义/节点只属于 Query 及执行计划；Artifact 只保存输出、描述与存储身份。
相同字段值不是同一 Entity，相同定义或共享节点不固定来源内容；
m+m 保留两次使用，同名角色或数值相等不能证明贡献包含。
动态字符串 schema 不由 Python 注解完全推导；具体 kind、绑定和实际数据条件分层检查。

### 2.2 状态与副作用

构造、字段定位、repr/contract 不读业务行、不创建 Run 或来源临时表。
物理类型在合法来源绑定时按 schema-only 边界解析，不扫描值猜类型；
模型加载保持 source-I/O free，不靠伪造原生 Column 提前取得 dtype。
execute 是业务执行入口；Query 变换返回 Query，Artifact 不提供 execute 或任何 DSL 变换。
show/to_pandas 只读已提交内容；后者返回有资源边界的隔离副本，固定导出布局与信息边界由 §4.6.1 拥有。
禁止迭代、bool/len/query 行索引或运算符隐式执行。展示默认按完整键规范排序；Top-N 的评分展示次序见 §4.6。
show(n) 不截断保存结果，不构造样本人群，量使用原 dtype 的缺失表示，不附带逐格状态或原因列。
项目本地 Session/Artifact 元数据访问不属于业务取数。
行/Metric 表达式与 @ms.measure / @ms.metric 在模型和分析代码中均无登记或业务执行副作用；
loader 收录才检查命名空间及依赖闭包的独立持久解析。命名不会解除域已有的 Session 归属。

### 2.3 终端 Artifact 与计算边界

`Query.execute() -> Artifact` 是唯一分析产出边界。Artifact 包含完整输出数据、必要数据描述、
存储身份，以及本次实际生成的 Finding/Evidence。普通 observe 不生成 Finding；读取范围和实际检查摘要
按[默认保存合同](lazy-analysis-dsl-execution.md#12-默认保存信息)作为 Evidence 保存，不自动生成业务结论。
刚执行的 Artifact 和 `Session.artifact(id)` 打开的 Artifact 使用同一接口与数据表示；
二者均引用已提交的 Parquet 和输出记录，不存在内存产物模式或 origin/fixed/cold 能力层级。

Artifact 没有 Column、population、select、where、top、reduce、rollup、share、align、compare、drill 或 contract；
它不能作为 Query、Metric、Population、broadcast 或其它 DSL 入口的参数。
不保留 Result 兼容别名、freeze/from_artifact/from_pandas 入口或执行后隐式转成 Query 的路径。
Artifact ID 只在 Session.artifact 中用于定位产物；普通字面量字符串不被解析为产物引用，也不触发 Store 查询。
Query 自己仍持有定义；Artifact 不反向引用 Query、语义对象、计算图、节点政策或内部规约组件。
sum/count 等中间信息仅服务本次计划，执行结束后释放；若需要外部计算，应显式列为输出量。

所有语义组合在 execute 前完成，包括 Query.population 的域依赖及端点比较。
执行后通过 to_pandas 进行筛选、统计和跨产物比较；业务解释和是否重用由用户/agent 根据脚本判断。
重新 execute 原 Query 是一次新求值，可能重新读来源、重新选名单，不会复用上次 Artifact 的值。
脚本可以把外部选定的完整键/数值显式写成 identities/coordinates/values 的普通输入，
并自行提供当前语义绑定；这是一份新定义，不传递 Artifact 的历史语义或续算资格。
完整保存、Finding/Evidence 及读取边界见[执行与交接](lazy-analysis-dsl-execution.md#13-跨轮和跨进程恢复)。

## 3. 语义交接与输入类型

### 3.1 保留语义层的权威

Entity 提供来源、完整身份与版本；Field 提供单位、读取要求和权限；
Metric 提供不可变计算图、显式域依赖、固有条件、时间角色与时间样本规约；Relationship 提供方向、完整键与结构基数。
Grain/PeriodCalendar 提供认证日历，不证明业务来源完整性。
普通观察信任声明，不自动全表审计；声明、构造保证、调用前提和实际检查分别记录。

预定义与临时定义共用行表达式、来源接收者规约和 Metric 组合；装饰器只声明，政策归各算子。
@ms.metric 的零参数体返回同一 Metric 表达式，局部名从函数名捕获，模型收录限定 domain；observe 的列名不形成模型身份。
@ms.measure 的零参数体返回行表达式，继承已有单位/许可并可声明新业务授权；不接聚合/除法政策。
数量×单价不自动获得可加权限；已有可加金额相减可继承权限而直接规约。
对象依赖、时间/采样、数值政策及收录边界由
[语义提案 §2](../semantic/expression-model-redesign.md#2-同一构造装饰与命名方式) 拥有。
模型收录才发布精确 Ref；裸 Ref 不支持算术/方法转发，不捕获活动 Catalog。
原指标输入充分与具体执行资格分别判断，无合并摘要不否定完整输入直接求值。

### 3.2 Entity 身份与普通维度分组

Entity 的 datasource/source/primary_key/版本声明含义不变。
只有 Orders 时，customer_id 可以作为普通 Dimension 分组，不需要 Customers Entity。
订单身份域仍使用完整 order_id；客户 ID 字段坐标不能升级成客户身份。

| 问题 | 输入单位 |
| --- | --- |
| 选择订单 / 固定原订单 | 来源 Orders 身份域 / 显式字面量身份域 |
| 各 customer_id 收入与订单数 | 贡献按字段完整元组分组 |
| 固定 A/B 名单的新期间均额 | Metric.per(名单).mean() 在所选完整样本上先求子指标再平均；已执行答案交 pandas |
| 计当期出现 ID | 来源 count_distinct，不等于固定域大小 |
| 计固定域大小 | s.query(population).reduce({"ids": row_count()}) |
| 客户身份集合运算 | 明确 Customers 与 Relationship，取 image |

多个字段共同分类保持完整元组，不分别去重后求积。
字面量域只固定值组合，不固定关联订单，也不继承提取窗口/状态条件。
需要客户身份时沿声明关系取像；关联键覆盖完整目标主键时只读来源侧关联键，
不为身份提取强制读取客户档案，不从同名字段猜 Relationship。

### 3.3 签名中使用的精确类型

TimeBoundary 为 ISO 日期/时间字符串、date、带时区 datetime 或已解析的 InstantAnchor；
日期与无偏移时间字符串按 Session 时间构造器解析出的时区解释，不接受 naive datetime。
Grain 沿用 [统一时间语义](../temporal-semantics.md#unified-grain-and-time-scopes) 的不可变闭合值；
内建粒度由 mv.grain(unit, count=...) 构造，认证日历粒度由 catalog 中的 calendar.grain(level) 取得。
LiteralValue 为 bool/int/有限float/Decimal/str/date/带时区datetime/timedelta 的封闭联合；
None 不作算术字面量，只可作为字段 Null 坐标。
MetricInput 是语义提案定义的受控延迟输入约束；命名 Metric 与匿名数值输入均为原生 Ibis Deferred，
声明信息由内部 Resolver 持有。observe 消费时检查指标阶段，不接受带关系的裸 Ibis Value，不增加转换步骤。
FieldPredicate 是有语义绑定的字段谓词；可消费受控 Ibis 布尔子图或其延迟构造，不能接收无绑定的外部 BooleanValue；
ColumnPredicate 绑定当前 Query；PopulationPredicate 由接收者确定唯一作用域。
Axis 为身份、Field、Calendar 或 CorrespondenceAxis 绑定，仅用于逻辑表达式；membership 描述展开为保留其分配规则的 Field 轴。
Population 是有限完整键的 logical 域；Artifact 中的数据轴描述不是 Axis，不构造 Population。
Population.axes 提供当前有序、已绑定的坐标轴；member() 只定位唯一相容身份轴。
CorrespondenceAxis 的 projections 只公开已有端点轴绑定，沿 current/baseline 或 left/right 路径定位；
它是坐标投影元数据，不是每个分组的成员名单。
身份轴携带 Entity 完整键及角色/版本绑定，不需要另一份主体角色映射。
CoordinateValue 与 ValueRow 是 coordinates/Session.values 手写输入共用的闭合类型别名：

```python
CoordinateValue = LiteralValue | tuple[LiteralValue, ...] | None
ValueRow = tuple[tuple[CoordinateValue, ...], Mapping[str, LiteralValue]]
```

CoordinateValue 只描述可直接提供的字段值、已绑定身份轴上的完整主键元组与字段 Null，
不是所有 Axis 内部键的通用编码。按对应轴检查 kind 与身份绑定；日历/对应轴不接受手写键，
其构造和复用边界见 §4.5.1。ValueRow 的量值只接受 LiteralValue，不接收主键元组、None 或表达式。
Session.values 的 columns 直接映射列名到单位 str | None；值 kind 从有限行中的字面量确定，
不要求调用方声明物理 dtype，不新增列定义包装、工厂或顶层 Help 目标。
_Statistic 为 Column 统计/无列row_count 的私有闭合变体，不公开独立执行对象。
TimeScope、ObservationWindow 和 Correspondence 见 §4.1、§4.7、§4.9。
TimeScope 与 Population 是目标 `marivo.analysis` 的公开导出，可在注解中使用 `mv.TimeScope`、`mv.Population`；
TimeScope 复用既有公开类型，Population 随目标 DSL 实施，不表示当前实现已提供 Population。
这些支持类型随 owner 渐进发现，不逐个扩展顶层 Help。

## 4. 核心 API 签名

签名属于目标设计。Field/Entity/Metric、Catalog 与定义构造归 ms；
观察、Query 组合与时间绑定归 mv。除明确读取外，调用只构造 logical 值。

### 4.1 Session、字段、时间与路径

```python
def get_or_create(
    name: str,
    *,
    question: str | None = None,
    report_timezone: str | None = None,
    domains: str | Sequence[str] | None = None,
) -> Session: ...


def resume(session_id: str) -> Session: ...


class Session:
    id: str

    def instant(
        self, value: TimeBoundary, *, timezone: str | None = None
    ) -> InstantAnchor: ...
    def time_scope(
        self,
        *,
        start: TimeBoundary,
        end: TimeBoundary,
        timezone: str | None = None,
    ) -> TimeScope: ...

    def observe(
        self,
        metrics: Mapping[str, MetricInput],
        *,
        during: TimeScope | CalendarAxis | None = None,
        by: tuple[Axis | MembershipAxis, ...] | Population = (),
        where: FieldPredicate | PopulationPredicate | None = None,
        members: Population | None = None,
    ) -> Query: ...

    def members(
        self,
        entity: EntityRef,
        *,
        at: InstantAnchor | None = None,
        where: FieldPredicate | PopulationPredicate | None = None,
    ) -> Population: ...
    def query(self, population: Population) -> Query: ...
    def read(self, population: Population, fields: Mapping[str, Field]) -> Query: ...
    def values(
        self,
        *,
        by: tuple[Axis, ...],
        columns: Mapping[str, str | None],
        rows: tuple[ValueRow, ...],
    ) -> Query: ...
    def artifact(self, artifact_id: str) -> Artifact: ...


def member() -> MemberAxis: ...


class TimeScope:
    start: InstantAnchor
    end: InstantAnchor

    def by(self, grain: Grain) -> CalendarAxis: ...


class CalendarAxis:
    def coarsen(self, grain: Grain) -> CalendarAxis: ...
```

observe 是唯一指标观察入口，metrics 为非空命名映射；名称只作输出别名。
省略 members 时直接按各组件来源/定义取得贡献，不构造隐式主体。
显式 P 必须是恰好一个已绑定 Entity 身份轴的 Population，要求同 Session；
身份×日期或多身份轴须先 project。空 P 与无约束不同，每个组件沿合法路径归属于 P。
显式身份引用不证明档案存在；贡献约束与请求输出域分别绑定，不从有无事实重选 P。
每个贡献根到 P 的归属路径由语义层的唯一声明决定，observe 不接受路由补全参数。
路径缺失或存在多条候选时，构造即拒绝：结构化错误列出实际贡献根、P 的 Entity 与候选关系，
修复指向在语义层声明唯一默认路径，或分别 observe。Field.via 是标量字段读取路径，不承担主体归属。

Field.via(path)、Population.image/preimage(via=...) 与 corresponding(via=...)
统一接受非空有序关系元组：单跳写 (relationship,)，多跳写 (first, second)。
单独的 RelationshipRef、list 和空元组均拒绝，不自动包装或把空路径解释为恒等关系。
路径按声明方向连续连接；完整键、角色、版本、端点和消费者所需基数仍分别检查。

where 在每个来源组件上追加条件，保留原定义；任一组件不能唯一解释就整体拒绝。
Metric 内部已捕获的域选择程序、字面量键及选择期间不会被共同 where/during 重写。
observe 检查整个 Metric 依赖闭包的 Session 归属；纯字面量域在消费时绑定，已有归属不得隐式转移。
member_count 在显式 P 上要求条件可单值解释，不重写已选身份域；
多值贡献条件不能被猜成“任意贡献满足”。常量不新增来源，固有分支过滤不被共同条件删除。
不同条件/范围的观察分别构造，再 align/select/compare，不嵌套默认上下文。

during=scope 为普通 Metric 提供范围合计，为窗口 Metric 提供 scope.end 评价端点；
scope.by(mv.grain("day")) 同时提供范围和完整日历，窗口规则仅来自 Metric.window。by 只列其它轴，
高级完整域须与 during 的同一日历绑定相容。冲突拒绝，不生成重复轴。
所有适用分支只接这一份上下文；期间不覆盖业务时间角色、属性锚点或固有固定范围。

省略 during 与显式 during=None 等价，表示本次观察不追加时间过滤。
业务时间角色只决定显式期间作用于哪个时间字段，本身不要求每次观察都提供期间。
仍按完整依赖图检查时间采样、时间样本规约和日历输出的期间要求，不只检查最外层 Metric：

| 指标或输出要求 | during=None |
| --- | --- |
| 不要求时间采样或时间样本规约的普通来源规约，以及仅由这些指标和常量组成的表达式 | 合法，有无业务时间角色均可；按定义及显式 where/members 取贡献，不新增时间约束 |
| member_count 及仅由它与上述普通规约组成的表达式 | 合法；仍须提供显式身份域，版本/属性等其它绑定要求不变 |
| 任一依赖要求按观察期间采样、包含时间样本规约或 window | 拒绝；必须显式提供期间/端点及所需日历；ObservationWindow 不作为 during 输入 |
| by 请求日历轴，包括含日历轴的显式 Population | 拒绝；必须显式提供与输出日历相容的 during，不从 by 反推观察期间 |

固有日期过滤和 where 中的 within(scope) 照常限制贡献，普通规约不必重复传入 during。
这些过滤不代替时间采样、时间样本规约或日历输出所需的期间；成员选择时点和 Field.at 仍只绑定各自的版本选择。
空 members、乘零或只消费输出键不能免除真正必需的期间绑定；无时间角色的指标不会因此获得隐式时间角色。
省略期间不选择最近期间、当前时刻或 Session 默认日期范围，也不证明来源包含完整历史。
构造时若缺少必需期间，结构化错误指出要求时间采样/时间样本规约的实际依赖或输出日历、收到的 None 及显式 during 的修复入口。
不读取来源猜测边界，执行与恢复也不把 None 改写为数据的最早/最晚时间。
期间准入不证明来源覆盖完整；默认信任与实际覆盖检查遵守 §4.9.3。

Session.time_scope 为非空半开范围；Session.instant 构造单个精确时刻，两者不保留模块级同名入口。
timezone 省略或为 None 时使用该 Session 已保存的 report_timezone，显式值只覆盖本次构造。
构造时冻结解析后的时区和绝对边界；后续消费、执行与冷恢复不重新选择 Session 或主机默认。
日期按解析时区午夜，无偏移时间字符串按当地时钟解释；DST 歧义/不存在时刻拒绝。
Python naive datetime 拒绝，不能按主机时区或报告时区猜测；需要时由调用方显式提供时区。
带偏移字符串、带时区 datetime 和已有 InstantAnchor 保持同一绝对时刻，不能按新时区重解释钟面值。
以上规则只解释分析时间构造器的输入；来源字段的 naive 时间继续由既有来源 parser/reader 时间合同解释，
缺少必要解释时按该合同拒绝，不借 Session.report_timezone 重解释来源业务时间。
scope 的时区用于日历分桶；认证日历仍拥有自己的边界时区，不相容时按 §4.9.1 拒绝。
认证日历期间沿用 [PeriodCalendarEntry 导航合同](../temporal-semantics.md#catalog-navigation-and-result-types)：
`catalog.period_calendars.get(ref).period(level, key)` 返回同一公开 TimeScope，
其中 level 与 key 是位置参数，精确定位当前认证快照中的期间，不解析键名来猜日期。
该 scope 冻结快照身份、半开边界和日历边界时区；Session 默认时区不重解释它。
未知层级/键或缺少当前认证快照按既有语义导航合同拒绝，不选择最近期间；本提案不新增 period 入口。
Session.instant/time_scope 的构造只读取本地 Session 时区元数据，不读业务来源；
时间值不捕获可变默认，也不提前把纯日历域归属某个 Session。
内建周从星期一开始，civil day 不等于24小时；截断桶保留名义与实际边界。
Field.at 显式绑定属性版本，非版本化字段不能假装有历史语义。

Field 支持类型化比较、is_null/within 和布尔组合，不提供原生 expr。
数值行表达式使用语义提案 §2.6/§3.6 准入的原生 Ibis 运算；新增业务可加性仍由 Measure 声明。
未知 dtype 自动留到 execute 准备，匿名与命名式不需要不同构造入口；不承诺全 Ibis API。
多值/多字段筛选统一使用 in_population(coordinates(...))。
字段轴的源 Null 默认归 None；缺映射、非法计算和实际覆盖检查失败按错误处理，不伪装为空分类。
None 展示 `<null>`，真实同名字符串转义；规范排序 None 在前。

Session.query 只物化域，无量列；实际身份轴仍保留身份类型，有来源的域仍保留显式依赖。
values 的非空 columns 直接映射每个独立参照列名到单位，例如 {"budget": "CNY"} 或 {"weight": "1"}；
不接受嵌套的列定义映射。有限非空 rows 提供完整键和逐列 LiteralValue，坐标准入与 coordinates 相同。
每行量名须与 columns 完全相同；同列 kind 一致，重复键、缺列/多列、类型冲突和非有限值拒绝。
columns 中的 "1" 明确无量纲，None 表示未声明单位；每列都须显式给出其单位值，不能省略 entry。
未知单位不等于无量纲，不能从列名或后续消费者猜出 CNY。
单位须与值 kind 相容，不承担物理 dtype 声明、换算或业务可加性授权；字面量值不携带另一份单位。
绑定后的列 kind/单位和值一起冻结；保存后不能把无量纲或未知单位的旧列重新解释成金额。
零轴仅一行；不增加 DataFrame/SQL/回调输入。显式身份键保留其类型，普通字段键不升级为 Entity 身份。
Session.artifact 用精确已提交 ID 打开终端产物，不选最新同定义结果，不加载来源补信息。

#### 4.1.1 精确时刻与版本选择

InstantAnchor 是唯一时刻值：由 Session.instant、scope.start/end 取得，可作范围边界、since 起点，
或作为 Session.members、Field.at、Population.image(via=..., at=...) 的版本选择时点。
`Field.at(anchor: InstantAnchor) -> Field` 返回保留字段身份的版本绑定；此调用不读取属性或提前验证版本存在性。
范围之间复用精确边界使用 scope.start/end，不用 end 减 tick。
对闭开有效期按 valid_from ≤ t < valid_to 选择；快照实体选择包含 t 的声明期间，不寻找最近可用快照。
v0.25 不提供边界左极限 t⁻ 的版本选择：at=scope.end 选择边界时刻生效的版本；
边界恰好换版时，期间内最后一个旧版本不可表达，也不能用 end 减 tick 近似。

#### 4.1.2 常量与独立参照列的单位

Column 比较、算术和 select 直接接收 LiteralValue，不需要公开字面量表达式或包装构造器。
bool 不当数值，非有限数值与 naive datetime 拒绝，None 仍按 §3.3 拒绝。
内部可以使用不可变常量节点；每个使用位置独立绑定，不给被复用的 Python 标量附加或修改单位：

| 使用位置 | 单位解释 |
| --- | --- |
| 已有数值 Column 与常量比较、加减 | 常量按该列已有单位及尺度解释；未知单位保持未知，原检查不被免除 |
| 数值 Column 与数值常量乘除 | 常量是无量纲系数；乘除保留或按方向推导量纲，100 / 金额为金额单位的倒数 |
| select 中没有量上下文的独立数值常量 | 无量纲；字符串、日期等保持自身 kind，不推断业务单位 |
| Session.values 的独立参照列 | 按 columns 中该列的单位声明解释全部输入字面量 |
| 两个已绑定的量运算 | 校验已有单位；不能把其中一个退回可随上下文改绑的字面量 |

例如收入列已知为 CNY 时，revenue >= 100、revenue + 100 中的 100 按 CNY 解释，
revenue * 1.1 则仍为 CNY；不按显示标签猜元/万元、不自动换汇。
独立预算是新的量，应在 values 的 columns 映射中声明一次；列单位不会由未来的 align/compare 反向补齐。
完整预算和权重示例见 [场景验证](lazy-analysis-dsl-scenario-validation.md)；支持字面量不授予来源 Metric 或 Field 任意逐行算术。

### 4.2 统一 Population 与明确身份读取

```python
class CorrespondenceAxis:
    projections: Mapping[tuple[str, ...], Axis]


class Population:
    axes: tuple[Axis, ...]

    def where(self, predicate: PopulationPredicate) -> Population: ...
    def project(self, *, by: tuple[Axis, ...]) -> Population: ...
    def union(self, other: Population) -> Population: ...
    def intersection(self, other: Population) -> Population: ...
    def difference(self, other: Population) -> Population: ...
    def classify(self, *, by: tuple[MemberAxis | Field | MembershipAxis, ...]) -> Population: ...
    def image(
        self,
        *,
        via: tuple[RelationshipRef, ...],
        at: InstantAnchor | None = None,
    ) -> Population: ...
    def preimage(
        self,
        targets: Population,
        *,
        via: tuple[RelationshipRef, ...],
    ) -> Population: ...
    def contract(self) -> AnalysisContract: ...
```

所有域共用集合、已有轴投影、纯坐标筛选和唯一保存路径；不按来源拆分公开域类型。
Session.read 的 population 参数，以及 classify、image、preimage 的接收者，只接受单个已绑定 Entity 身份轴；preimage 的 targets 也须为
路径目标 Entity 的单身份轴域。形状不符立即指出实际轴及所需投影，不引入可空 entity 或权限标志。
这些对象始终 lazy；Population 不提供 execute，使用 s.query(population).execute() 保存完整键。
Population.contract 只披露定义、已有绑定/归属和可判定操作，不枚举未知键集或触发业务/schema 读取；
具体披露归[执行 §2.1](lazy-analysis-dsl-execution.md#21-repr-与输入权威)，静态签名归 Help。

Session.members(entity, at=..., where=...) 明确从该 Entity 来源选择身份，where 在来源作用域
读取声明字段或测试名单；只返回实际命中的完整键。源中没有 O404 时，名单中的 O404 不生成源行。
identities 则保留明确请求的身份引用，包括未由来源确认的键；两种表达式的依赖和证据可区分，
返回类型相同。未执行的 query(population).population() 可作为 members 输入；执行所得 Artifact 不能作为身份输入。

Population.where 只接受 in_population 及其布尔组合，测试接收者已有坐标，不读取缺少的字段。
初始来源条件放在 Session.members(..., where=...)；对固定身份读取新属性时，先调用 session.read(population, fields)，
再按返回的 Column 筛选并取 population()。Session.read 返回 Query，筛选在同一逻辑输入上组合；已导出的旧属性筛选由 pandas 处理。

Session.read(population, fields) 是属性读取的唯一入口。它由显式 Session 绑定归属和执行服务，
不查询 ambient Session，也不保留 Population.read 别名。它保留完整输入身份键，字段成为命名量列；
同 customer_id 的两个订单仍两行。纯字面量身份域在该消费处绑定 Session，已有归属必须相容。
classify(by=...) 接收 Field、接收者原有的 MemberAxis，以及 owner 与接收者完整身份/角色/版本相容的 MembershipAxis：
它在构造目标桶域，直接读取当前成员身份的单值字段，不执行事实根到桶的分配路径；描述中的自关系路径也不在此展开。
membership 保留下游分桶描述，事实消费者再按显式路径优先规则绑定。
可以同时列出原身份轴；不接 CalendarAxis、CorrespondenceAxis、其它身份轴、任意回调或 owner 不相容的 membership。
缺少完整属性映射时失败，不静默删除引用或补 Null。
仅投影已有轴使用 project；classify 的新属性读取不会被解释成对旧坐标的投影。
image 只表示沿关系取目标身份，不与按字段分类共用方法名。

image(via=...) 沿声明关系取得目标身份，preimage 只在接收者有限域内选择关联到 targets 的身份。
完整目标键可由来源侧关系键取得时不读取目标档案；来源 Null 关联键无像，目标档案缺记录不删除引用。
读取必需属性/版本或补足关联键才引入相应来源依赖；所需绑定缺失与合法无关联分别处理。
路径按声明方向连续连接，空元组/反向/断端拒绝。关系映射不传递原指标贡献过滤，也不恢复原贡献出现。

字面量身份域只固定键。Session.read、classify、image 的属性或关系读取明确引入新依赖，版本锚点不能
静默替换成当前；来源不可用不能退回旧属性。实体、完整复合键、角色、属性版本和 Session 仍须检查。

#### 4.2.1 像、逆像与已有坐标投影

设接收者 P 为来源 Entity 的有限身份域，R 为声明方向上的关系，T 为目标 Entity 的身份域：

- image(via=(R,)) = {y | 存在 x∈P，x R y}，输出目标身份并去重；不保留贡献出现次数。
- preimage(T, via=(R,)) = {x∈P | 存在 y∈T，x R y}，输出接收者身份的子集；
  不搜索 P 之外的来源身份。多关联取存在匹配，一条来源身份只保留一次。

两个操作都沿同一声明方向解释 R；preimage 不是反向声明路径，也不是 image 的还原操作。
当两笔订单关联同一客户时，客户像会丢掉订单区别，不能再恢复最初选中了哪一笔。
关系与所需属性读取保持明确依赖，构造不执行；输入域的版本绑定必须相容，不能靠新操作覆盖它。

| 操作 | 结果与读取边界 |
| --- | --- |
| session.read(population, fields) | 保留输入身份，一身份一行，字段作为量列；显式读取属性 |
| classify(by=fields) | 读取单值分类并对完整元组去重；普通字段值不会升级为另一个 Entity 的身份 |
| image(via=(relationship,)) | 取得声明目标 Entity 的身份；可用来源关联键时不强制读目标档案 |
| preimage(targets, via=(relationship,)) | 在接收者身份域内筛出有匹配的身份；不传播 targets 的原贡献过滤 |
| project(by=axes) | 只提取已有坐标并去重；不读取新字段或关系 |

集合与版本示例见 [关系像与有界逆像](lazy-analysis-dsl-examples.md#4-关系像与有界逆像)。

### 4.3 Metric 表达式与明确贡献角色

定义签名由语义表达式提案拥有；来源用 Measure/Entity/Field 接收者规约，
指标用同一算术和 per(样本).规约；Query Column 不回到来源 Metric。
Metric 没有 expr/during/route，也没有 on 树位置参数。
where 仅形成单一来源规约或完整配对作用域的新固有过滤定义。
它接受 FieldPredicate 或 PopulationPredicate，显式捕获域的不可变字面量/逻辑定义；
与 FieldPredicate 组合后的域依赖也须保留，不能靠谓词类型包装绕过归属检查。
per 接受 Field、MembershipAxis、二者的非空元组或显式 Population，选定内部样本后规约；规则见
[语义提案 §3.5](../semantic/expression-model-redesign.md#35-内部域规约与-query-格子统计)。
@ms.metric 只声明函数局部名、可选 domain 与 ai_context，派生表达式不继承旧声明身份；不保留 named 或 RuntimeMetric。
/ 与 divide 规范化为同一节点，均保存分子分母角色；角色不自动授权观察路由。

```python
def member_count() -> Metric: ...
```

member_count 是要求显式身份域 P 的上下文定义，不捕获 P；
逐格身份分类须可解释，计声明域的完整身份，不证明每个引用有当前档案。
不能将贡献行数、身份×日期坐标数或普通 ID 字段数冒充它。
member_count 仍消费 observe.members 的本次身份约束；它不因 Metric 可引用域而隐式选择某个内部域。
Metric.per(固定名单) 后规约可显式定义新来源计算；只数名单键或统计当前 Query 格子时使用 Query.reduce；Artifact 交 pandas。
能否独立加载只在模型收录边界检查，不阻止带会话域依赖的合法临时 Metric。

#### 4.3.1 结果组合的有限边界

来源条件使用有语义绑定的字段谓词；行/Metric 组合以原生 Ibis 数值图及语义注解为准，
移除重复的 Marivo 数值 AST。未知操作、任意关系程序和回调仍拒绝；observe 检查阶段、贡献和已知事实，
保留待真实 schema 验证的义务，不为匿名表达式提前连接来源。
已知 schema 也不改变数值作者侧的 Deferred 载体；Catalog 条目及 Query.contract 披露语义事实。
声明仅命名不新增 eager 求值根。ifelse/cases/coalesce 的分支需求域由[语义提案 §3.6.5](../semantic/expression-model-redesign.md#365-条件分支的需求域与检查范围)拥有，
命名/内联等价；Query 后置筛选或丢列不反向取消已有严格检查。
语义使用位置、声明计算指纹及绑定执行指纹分别描述消费、逻辑定义和实际计划，不能用 Ibis op 相等性混同。
Query 只保留同域 Column 算术、比较、is_null 和 select/where。
这些操作为主体选择、参照与比较运输实际合同，不扩成另一套通用表格表达式语言。
结果条件赋值、封顶/截断、标签分类和一般配对统计交 pandas，见 §4.6.1。
需要复用选中的身份时，先对 Query.where，再取 population().project(by=身份轴)，最后执行消费它的 Query。
该轴必须已实际保留；只有分组字段的汇总不能恢复原名单，应在原 observe 的 by 中请求身份轴。

#### 4.3.2 字段表达式的规约授权

聚合检查声明单位/可加坐标/状态时间，dtype 不授予业务权限。
数量×单价先形成行表达式，再由 @ms.measure 为新业务金额声明授权；单价的原权限不变。
相容金额减成本继承许可交集，可以直接 .sum()，不强制新增业务声明。
Measure 仅增加名称时继承表达式的单位和可加事实，不要求重复断言；缺少的新业务权限仍需显式声明。
Metric 完成值加非零常数不继承可加性，不能与逐行加常数后 sum 混同；权重归一化不要求业务可加授权。
其规则及反例由[语义 §3.3](../semantic/expression-model-redesign.md#33-指标依赖与比率)拥有。
数值编码 Dimension、变量乘除、过滤、相消或乘零不能自动获得求和权限；不扫描 Catalog 猜等价声明。
来源 sum/count/mean 等精确方法逐项校验意义；未知单位不能自动换算。
approx_nunique/approx_median/approx_quantile 按定义保持近似意图、单位及单值结果要求，不授予合并完成值的权限。
声明、路径、规则版本和本次中间状态由执行计划管理，完成值不能替代充分信息。

#### 4.3.3 时间样本规约与派生依赖

时间角色、per(状态采样轴) 后规约的空间/时间次序由[语义提案§3.2](../semantic/expression-model-redesign.md#32-时间角色和时间样本规约)拥有。
本次观察只提供期间/目标空间，计划中的原指标粗化需要可对齐的时间规约前信息；
不能用完成P95或失去时点的分布补它，数值见证见 [场景验证 §7.8](lazy-analysis-dsl-scenario-validation.md#78-采样带宽-p95-与依赖指标)。
first/last 在同一内部时间样本上有序选择；排序唯一性、include_null、采样覆盖与 strict 域见语义 §3.2.1。

#### 4.3.4 显式成员关系分桶

```python
def membership(
    by: Field | tuple[Field, ...],
    *,
    via: tuple[tuple[RelationshipRef, ...], ...],
    unmatched: Literal["exclude", "null"],
) -> MembershipAxis: ...
```

这是多值分桶的唯一公开入口；返回不可变的坐标绑定描述，不是 Population、数值表达式或物理表。
by 的非空字段元组来自同一个成员 Entity；一个真实成员给出一个完整桶元组，不把各字段独立展开成笛卡尔积。
via 是有序路径集合，可以为 ()；其中每条路径必须非空，起点推导贡献根，终点均为 by 的 owner。
对每个被分桶的根独立绑定：先选该根唯一的显式路径，检查方向、终点、完整身份、角色与版本；
只有未提供该根路径时，才在根与 owner 身份相容的条件下推导恒等绑定，直接读取自身成员字段。
显式自关系或合法自返路径照常表达另一种成员分配，不与推导恒等关系竞争；多条显式路径仍拒绝。
显式路径无效时报告其错误，不退回恒等绑定；恒等绑定不需要伪关系或空路径项。
via=() 只适用于全部根都能恒等绑定的消费；描述可保留其它根的合法路径，当前只消费相关根的绑定。
缺路径、竞争路径、方向或完整键/版本不相容均拒绝，不自动选路。
字段及关系是已解析语义对象；schema 通过同一 execute 准备绑定。构造不读来源、不创建 Session。

```python
tag_buckets = mv.membership(TagId, via=((OrderTags,),), unmatched="exclude")
tag_query = s.observe({"revenue": Revenue}, during=period, by=(tag_buckets,))
tag_mean = Revenue.per(tag_buckets).mean()
full_tags = mv.coordinates(by=(tag_buckets,), keys=(("A",), ("B",)))
full_query = s.observe({"revenue": Revenue}, during=period, by=full_tags)
full_tag_mean = Revenue.per(full_tags).mean()
```

同一作用域可以显式列出多个 membership，并与普通单值轴组合：`by=(tags, channels)` 或 `per((tags, channels))`
表示对每条根贡献的两个成员集合取笛卡尔积；不推断相关性。要表达真实相关的标签/渠道对，使用同一成员 Entity
上的 `membership((TagId, ChannelId), ...)`，只保留实际联合元组，不补交叉组合。
每个描述先形成成员键集合，再对组合的完整桶元组与根贡献键去重；同桶一次、跨桶可重复。
重复/冲突的字段轴或不同路由冒充同一轴均拒绝，不自动覆盖、合并或重排；不跨窗口或指标位置全局去重。
unmatched 无默认值，逐描述决定空成员集变成空集或一个全 None 元组；因此 exclude 的空因子令组合为空，
null 因子可继续与其它成员集合组合。真实成员的字段 Null 同样按完整坐标相等性去重，不制造第二种 Null 哨兵。
成员版本/属性时点沿已有声明绑定，不推断当前版本；检查不会变成全来源健康扫描。
普通 Field.via 与 members 的归属约束保持单值，不接受此描述作为数值字段或替代主体路由。

MembershipAxis 在消费时展开为其有序字段轴，保留分桶组、路径、恒等规则和 unmatched 绑定；不是一个嵌套元组值列。
coordinates(by=(membership, ...), keys=...) 接收展开后的完整字段值元组，提供带绑定的手写域；
来源驱动的完整域使用 `s.members(TagEntity).classify(by=(tag_buckets,))`，按目标成员身份直接读取分类；
这与贡献根的选路分开，即使描述含 TagEntity 的显式自关系，构造桶域也不沿它映射成员。
多个完整成员域可用 product 组合，稀疏联合域直接用 coordinates；无需先运行事实分桶 Query，也不靠字段同名猜回绑定。
Query.population 仍只给实际逻辑输出键，不能补出未出现的桶。纯 coordinates 的键运算不读取成员路径，
消费事实分桶时才沿该路径取成员；classify 只读取当前成员身份的分类，不执行描述中的分配路径。
上述各域均保持 lazy，保留自身来源/属性时点和 Session 约束。
Population 的 project、集合和 product 保留所选轴所需分桶绑定；集合要求相同有序轴及绑定，投影不丢弃其成员路径与版本。

per(membership) 在每个外部格子内生成实际桶样本；per(带绑定 Population) 选择该完整内部样本域，包括空桶，
子指标按各自 empty/NA 政策完成后再规约。外部 by=Population 则要求覆盖全部被消费桶，不能用漏键过滤。
成员分配关系与请求输出域 K 分开：完整 K 可含没有贡献的桶，但不新增根事实；命名 Metric 与匿名体完全相同。
重叠桶的原指标总计按贡献并集重算；Query.reduce 的格子 sum 只是格子统计，二者不能冒充等价。
跨桶可加性与 share 仍检查完整不交分区；不提供自动均分、加权分配或“归一化修复重叠”。
多根指标逐根逐描述建立路径或恒等绑定，独立规约，再按完整桶键组合；缺少任一绑定即拒绝。
这里的独立规约是逻辑贡献约束，不指定物理 aggregate/join 顺序；合法计划可先构造成员键集合，再回取根值规约。
存在性筛选使用 preimage；真实关联次数使用独立关联 Entity 的 count；加权分摊使用显式分配根上的行量，
不能把这三种含义合并为 membership 去重或公开 fanout_policy。来源建模不能表达的通用事件匹配不属于 Metric 能力。
具体边界与执行等价条件由[语义 §3.7](../semantic/expression-model-redesign.md#37-显式重叠成员分桶)拥有。

### 4.4 最小规约与有效样本

来源规约由 Field/数值行表达式/Entity 拥有，内部样本规约由 Metric.per 拥有，
Query 格子统计由 Query.reduce 拥有；样本单位和方法资格仍分别声明。
来源与内部样本的 nulls，以及各阶段统一的 zero_denominator 政策归
[语义提案 §3.4](../semantic/expression-model-redesign.md#34-样本政策的归属)；普通 mean 不因内部实现有除法而增加零分母参数。

| 结果规约 | 固定政策 | 无有效样本与输出 |
| --- | --- | --- |
| Column.sum | 忽略 NA | 空输入与全 NA 都为 NA；保留单位；和摘要只供本次计划使用 |
| Column.mean | 忽略 NA | NA；保留有效值 sum/count 供本次计划使用，分母不是全部 Population 大小 |
| row_count | 不读取量值 | 空输入为 0；计全部当前坐标，包括值为 NA 的格子 |

Column 结果规约没有 empty 参数，也不保存用于区分空输入与全 NA 的隐藏计数。显式 row_count 与有效值数有各自的统计含义。
结果规约不新增 nulls 参数；需要严格的全非空样本，可显式检查或在原 Metric 上声明 nulls="error"。
多项规约消费同一输入，各量独立选取非 NA 样本；加权/配对运算才使用共同 mask。
NA 被跳过不等于对整个 Population 的估计，量定义与有效样本政策在本次计划内保留；Artifact 仅记录必要输出描述。

统计请求返回私有 _Statistic，只交给 reduce；普通终端描述统计可交 pandas。
统计量之间的比值先 reduce 再 select，不能对私有统计请求直接算术或 execute。
本版不增加其它结果统计；来源分位数和内部样本规约仍由原方法拥有。

### 4.5 坐标和目标域

```python
def product(*factors: Population | CalendarAxis) -> Population: ...


def identities(
    entity: Entity,
    *,
    keys: tuple[tuple[LiteralValue, ...], ...],
) -> Population: ...


def coordinates(
    *,
    by: tuple[Field | MembershipAxis, ...],
    keys: tuple[tuple[LiteralValue | None, ...], ...],
) -> Population: ...


def in_population(population: Population) -> PopulationPredicate: ...
```

#### 4.5.1 轴、键与域运算

Field 在 by 中是字段绑定轴，须有合法单值分类；按原类型形成完整元组。
源 Null 默认归独立的 None 键，参与分组、投影、集合和对齐，不删除贡献；
同字段绑定下按坐标相等性匹配，字段坐标值 None 是合法空分类，不是缺侧或缺映射。
复合轴逐字段分类；缺映射、非法计算和覆盖失败不能伪装成 None。
推导域仅在实际出现 Null 分类时产生此键；显式请求 None 时即使无贡献也按 空输入规则保留。

| 操作 | 定义与约束 |
| --- | --- |
| identities(entity, keys) | 唯一手写身份名单入口；只接已解析 Entity，不接裸 EntityRef，也不查找隐式 Session；每个键是按声明次序的完整主键元组，允许未经来源确认的身份引用；空 keys 仍是该 Entity 的类型化空域 |
| coordinates(by, keys) | 唯一手写字段坐标域入口，声明展开后的完整有限元组，允许稀疏域；显式 MembershipAxis 保留其分桶绑定 |
| product(*factors) | 至少两个不重复轴因子，按参数次序拼轴并作笛卡尔积；日历因子提供完整桶，任一空因子使结果为空 |
| Query.population() | 当前逻辑输出坐标；保留筛选、来源依赖及必要检查，不提前固定键 |
| Population.project(by) | 从已有轴或本次图的端点投影中选取/重排并去重完整元组，不读新属性；投影须全定义，outer 缺侧先按 presence 筛选 |
| union/intersection/difference | 按完整元组作并、交、左差；要求相同有序轴、身份类型、角色、版本/时间及相等性，不按列名合并或自动重排 |

identities 的 Entity 先从已完成的 finalize scope 或 catalog.entities.get(entity_ref) 解析。
构造身份名单不读取来源，也不由键值猜测 Entity。

空域保留轴类型；投影到零轴时，非空输入得 {()}，空输入仍空。
手写坐标按轴检查：

| 轴 | 允许的手写坐标 |
| --- | --- |
| 身份轴 | identities 的完整主键元组；Session.values 的 by 可用从 Population.axes 取得的已绑定身份轴，坐标同为完整主键元组 |
| Field（含 MembershipAxis 展开的字段） | 与字段 kind 相容的 LiteralValue，或表示源 Null 分类的 None；bool 不冒充整数 |
| CalendarAxis / CorrespondenceAxis | 不接受手写坐标，包括日期标签、桶序号或端点元组；不能用普通标量冒充内部键 |

日历域由 TimeScope.by(Grain) 取得，并通过 product 组合；对应域来自明确的 align/compare。
已有日历/对应坐标通过 population、project、where 和集合操作复用，保留实际轴、桶和端点绑定。
不新增日历键/对应键包装构造器，也不开放 DataFrame 回灌。
不将相关字段分别去重再求积。重复轴/键、元组长度不符或坐标 kind 不相容均拒绝。
跨 Entity 的同值主键不相等，字段坐标不升级为身份。None 只可作字段坐标，不能作主键分量或量值。

by=Population 同时声明轴和完整 K，保留无贡献坐标；K 必须覆盖所有被消费贡献，不能靠漏键过滤。
in_population(Population) 则只筛已有输入，不生成键；请求完整输出也不证明覆盖或保证补零。
by=轴元组的 K 依 §5.1 推导。Metric.per(Population) 则显式选择内部样本，
每个样本绑定子指标的贡献条件；域外贡献不属于该问题，不要求先 where 或覆盖未选择贡献。
这是内部选择合同，不改变 observe(by=Population) 的外部覆盖要求，也不增加外部输出轴。
域只传坐标和轴绑定（包括显式 membership 的分配规则），不传供给方指标、贡献过滤、期间或隐藏名单。

所有域保持 lazy；s.query(population) 可 select 坐标、where、row_count、取域和 align，
没有原指标可供 rollup/drill。执行后得到仅键 Artifact，没有占位量，也不能重新进入 DSL。
Artifact 导出完整键与必要描述；普通字段值不能自行证明 Entity 身份。
含 Population/Query 的依赖保留 Session；集合运算/product 混用不同 Session 时拒绝。
纯字面量/日历域在消费时绑定 Session，组合本身不创建或选择默认 Session；Session.query 检查已有归属。

字面量域运算只用显式键；含来源的域保留读取和原检查，即使集合等式可推空也不能省略必要失败。
相同来源定义不代表相同实际键集；共享读取规则见
[执行与恢复 §1.1.1](lazy-analysis-dsl-execution.md#111-共享逻辑节点与执行内输入)。

#### 4.5.2 身份名单与 in_population 绑定

identities(E, keys=(("O1",), ("O2",))) 中每个内部元组是一条完整复合主键，身份轴在构造时即绑定 E；
空名单同样是 E 的类型化空域，可直接保存或作为接收者筛选，不存在待绑定身份轴。已有绑定不能改绑。

in_population 只保存域，接收者在 where 中确定唯一测试，不预读字段或提取 Python 列表。
PopulationPredicate 支持 &、|、~；与 FieldPredicate 或 ColumnPredicate 组合后分别成为该类谓词，
所有测试绑定同一接收者。Field/Column 谓词不能混用，已绑定 Column 不能跨输入域混用。

| 接收者 | 允许的域与匹配方式 |
| --- | --- |
| Session.members(..., where=...) | 一个或多个字段轴按完整字段元组读取；或一个相同 Entity 身份轴按完整主键匹配 |
| 基础 Metric.where | 同上两种形状，作用于单一贡献根；保留/删除贡献出现，不按身份去重贡献 |
| Population.where | 只匹配已有坐标，只接受域谓词及其布尔组合；新属性须明确 read/classify |
| Query.where | 从当前完整 K 向名单轴建立唯一、全定义投影 p；保留 {k∈K : p(k)∈S}，不读字段或重选成员 |

来源选择/贡献筛选不接受零轴、身份与其它轴混合、多身份轴、日历或对应轴；先 project 到合法形状。
字段角色/版本须合法；完整复合元组不能拆成各列集合。来源成员只取实际命中交集，
未命中名单不生行、不报覆盖错误，多次匹配不复制身份；固定身份筛选不重查存在性。
源 Null 分类按 None 匹配，其它状态按 §5.4 处理；名单时点不隐式传给来源选择。

结果投影仅用已有轴的选取/重排、本次图的父子映射或合法 coarsen；
逐轴检查身份、角色、时间/版本和对应绑定，不按名字、行位置或部分维度猜测。
域 S 无须覆盖 p(K)，不匹配为 False，多余名单键不生行；这与 broadcast 的完整覆盖要求不同。
Query 保留原粒度、量、presence 和本次图内的必要信息。日历/对应轴须使用明确映射；
两侧不同日期不能当作同一时间轴，outer 缺侧投影须先限制到有键范围。
缺轴或歧义映射立即拒绝；member() 只能定位唯一相容身份轴，多角色须使用明确绑定。

合法空域恒 False；零轴 {()} 恒 True，零轴空集恒 False；域和原 Query 必要检查仍保留。
纯坐标测试不读取量列，不因收入为 NA 丢掉坐标；原 Query 必要检查仍保留；与值谓词组合时依 §5.4。
来源域随消费它的 execute 求值，字面量域使用显式键；二者都保留原检查和 Session。
Metric.where 也遵守这项规则：构造不执行或固定来源名单，声明名称不改域身份、归属或指纹。
域内的选择时间与本指标的观察时间分别保留；引用基期订单的 Query 程序仍按基期条件求值，不改用当期条件，但每次 execute 仍可重新读取。
固定客户 ID 字段域可选新订单；固定原订单须显式提供订单身份键。非持久化域重复消费可得到不同名单。

#### 4.5.3 日历粗化

coarsen 保留 scope、时区、截断区间与父子映射；相同 grain 保持原绑定。
改变 grain 仅构造候选粗化，实际区间须完整映到唯一目标桶，不能细化或扩大读取窗口。
日→月→年须与同 scope 下合法直接粗化相容；跨月周不能自动归某月，年桶仍只含原 scope 区间。
坐标映射不授予指标可加性或充分状态，能否原指标合并仍由 rollup 检查。
对依赖 window 的原量，候选 coarsen 不能通过 §4.9.4 的原时间绑定保留要求；应在粗轴上新 observe。

### 4.6 查询、结果与值表达式

```python
def row_count() -> _Statistic: ...


class Query:
    def __getitem__(self, name: str) -> Column: ...
    def coord(self, axis: Axis) -> Column: ...
    def population(self) -> Population: ...
    def select(self, values: Mapping[str, Column | LiteralValue]) -> Query: ...
    def share(
        self,
        name: str,
        *,
        partition_by: tuple[Axis, ...] = (),
        zero_denominator: Literal["null", "error"] = "null",
    ) -> Query: ...
    def where(self, predicate: ColumnPredicate | PopulationPredicate) -> Query: ...
    def top(
        self,
        n: int,
        *,
        by: Column,
        descending: bool = True,
        partition_by: tuple[Axis, ...] = (),
    ) -> Query: ...
    def reduce(
        self,
        values: Mapping[str, _Statistic],
        *,
        by: tuple[Axis, ...] = (),
    ) -> Query: ...
    def rollup(self, *, by: tuple[Axis, ...] = ()) -> Query: ...
    def align(
        self,
        other: Query,
        *,
        how: Literal["exact", "outer"] = "exact",
        match: Correspondence | None = None,
    ) -> Query: ...
    def compare(
        self,
        baseline: Query,
        *,
        how: Literal["exact", "outer"] = "exact",
        match: Correspondence | None = None,
        time_axis: Literal["paired", "current"] = "paired",
        zero_denominator: Literal["null", "error"] = "null",
    ) -> Query: ...
    def contract(self) -> AnalysisContract: ...

    def drill(self, *, add: tuple[Axis, ...]) -> Query: ...
    def execute(self) -> Artifact: ...


class Artifact:
    id: str
    session_id: str

    def show(self, *, n: int = 20) -> None: ...
    def to_pandas(self) -> pandas.DataFrame: ...
    def findings(self, limit: int = 20, cursor: str | None = None) -> FindingPage: ...
    def evidence_digest(self) -> ArtifactDigest: ...


class Column:
    def divide(
        self,
        other: Column | LiteralValue,
        *,
        zero_denominator: Literal["null", "error"] = "null",
    ) -> Column: ...
    def is_null(self) -> ColumnPredicate: ...
    # Arithmetic operators, including /, and statistical methods follow sections 4.1.2 and 4.4.
```

Query 独自拥有 DSL 方法；Column 只能由 Query 定位，Artifact 不继承 Query 的协议。
Artifact 的 show、findings 和 evidence_digest 读取已提交分析输出，to_pandas 是计算交接出口。
复用现有有界 FindingPage/ArtifactDigest 结果家族；记录改为自包含的输出事实，不能引用历史图才能解释。

Column 是统一的公开值表达式接口，内部封闭的数值、布尔、类别和时间 kind 限制相应运算；
不要求用户把 `query[name]` 再转换成另一个公开子类型。数值算术及 Query.share 选择非数值列时，
绑定检查立即报告期望 kind 和实际 kind；实际值、缺失和必要检查仍到 execute 才求值。没有任意 callback 的 map。
数值 Column 支持与 Metric 对应的逐点算术，返回绑定当前坐标的 Column；
`/`、divide(other) 和 divide(other, zero_denominator="null") 规范化为同一除法节点；
divide(other, zero_denominator="error") 只改变当前运算的政策，不覆盖输入定义。
规则与行/Metric 相同，按语义 §3.4 处理共同非 NA 零分母及普通 NA，不再要求将公式搬回 Metric。
LiteralValue 的 kind/单位绑定遵循 §4.1.2，已有列不能按常量规则重新赋单位。
谓词返回封闭的布尔表达式；普通配对统计按 §4.6.1 导出后显式选择共同有效样本。

Column.sum/mean 返回绑定列和当前样本域的私有 _Statistic；row_count 是尚未绑定输入的无列变体，
可复用于不同接收者，在 reduce 时绑定当前坐标域，不创建来源贡献。
reduce 的非空命名映射是唯一完成入口，分组在此统一绑定；同 schema、行数或 Session 不证明列属于接收者。
跨 Query 输入先 align/broadcast，不隐式对齐、拼行计数或重观察。各项消费同一实际输入，
有效样本按 §4.4 独立选择。统计请求不是 Column，不能直接算术或 execute；统计量之间的公式先 reduce 再 select。

reduce 与 rollup 的 by 只能引用当前成员/字段/日历绑定或其合法 coarsen，不能触发新属性读取。
省略全部轴时输出单例标量域；使用非空轴元组时输出所保留坐标的完整分组像。
需要保留没有子格子的父坐标时，在原 observe 上以 by=Population 请求完整域，rollup 不接受目标 Population。
空样本遵循该方法的空集政策，不因没有输入行而自动删除标量格子。
新统计量拥有自己的方法、单位和计划内中间状态；本次图内后续合并由这些信息决定，
不会继承输入列原业务指标的贡献单位或原观察的下钻能力。
`drill(add=...)` 只列新增细轴；目标细域固定为“当前轴 + 新增轴”，细坐标到父坐标的映射
就是丢弃新增轴的投影，因此不需要重复列出父轴。新增轴不能与当前轴重复，也不能是日历细化。
它不接受 share/contribution 开关，粒度变换与份额分开组合。
drill 只在可追踪到来源观察的 Query 上提供；Artifact 不提供 drill，需要细化时从原 Query 重新构造，准入见 §5.3.2。
compare 产生的 Query 不支持直接 drill；先在明确观察端点上分别细化，再 compare。

`select` 只改变量列，保留完整键和轴绑定；不能通过省略一列偷偷合并坐标。
`coord` 定位实际保留的轴绑定；相同语义字段的多个角色或版本须明确区分。
构造 Column 不读取来源；execute 准备期按完整键 schema 检查该轴只有一个标量分量，
复合身份坐标作为单量列在 Run 准入和业务 compile/read 之前明确拒绝，不引入 tuple 量值类型。
完整复合身份仍可作为输出坐标、Population 完整元组谓词及导出键；
需要单量值时选取已有单字段轴，身份筛选使用 population 与 in_population。
输入 Column 必须属于同一已建立的坐标域，或已通过 align/broadcast 取得对应。
同样的 schema 或恰好相同的行数不能建立这种关系。

轴统一用原 Field/Calendar 句柄或 population().axes 中的绑定引用，不另设按 Ref 查轴的方法；
同一字段存在多个角色/版本时拒绝并披露实际绑定。Query 使用真实绑定，不依赖原 Python 地址；Artifact 仅输出数据描述。
结果标签不成为新的业务轴。外部分箱/分类可使用 pandas，但这些标签不会自动获得
业务身份、原指标上卷、执行后下钻或跨期语义对应权限。

`top(n, by=score, partition_by=())` 在接收者的完整当前域上按 score 选择至多 n 个坐标；descending=False 选择最小值。
非空 partition_by 须为当前已有且不重复的轴绑定；不在 top 内读取新字段或隐式粗化。
按这些轴的完整元组划分输入，每组独立选择至多 n 个坐标；合法 None 分类属于一个分区，不凭空生成空分区。
n 必须为非负整数且不能为 bool；by 必须绑定当前输入域，非 NA 值具有声明的全序，数值须有限。
NA 无论升降序都排在非 NA 之后，再按完整键决胜；需要排除 NA 时先 where(~score.is_null())。
同分按完整坐标的规范顺序升序决胜，每组固定取 min(n, |K_group|) 个键；默认空分区轴时只有全局一组。
不提供另一种并列扩张政策。
排序值和被选行来自该次 top 实际消费的同一输入，不分别重跑来源后拼接；
分区键、排序值和被选行均来自该输入。这是选择操作的局部输入契约，不要求其他消费者共享读取，也不比较多次读取是否相等。
n=0 返回同类型空域，但仍保留输入与 score 的必要检查。输出保留原坐标、列、已有坐标投影和参照，
不增加排名列或顺序轴。全局 Top-N 按 score 和 descending 确定展示次序，同分按完整键升序；
分组 Top-N 先按分区完整键的规范顺序排列组，再按相同规则排列各组内的选中行。
show(n) 按这个次序读取前 n 行。执行保存本次选中完整键的展示次序，冷恢复不重算评分或读取来源；
评分表达式未作为输出列保存时，也须能够恢复相同次序。
where/select/share 保留已有键的相对展示次序，再次 top 使用新的评分次序；
reduce/rollup/drill/align/compare 及域物化不继承该次序，输出按规范键序展示。
展示次序不改变 Population、业务身份或对齐语义。top 是 lazy 结果选择，不是来源采样或物理 LIMIT 捷径。
先 where 再 top 与先 top 再 where 是不同问题；其后的 drill 按父级选中键细化，不在每个子组重新排名。
来源 Query 的新 execute 可重新选择重点；Artifact 只固定这次输出，不提供 top 域，读取保证见 [执行与恢复 §1.1.1](lazy-analysis-dsl-execution.md#111-共享逻辑节点与执行内输入)。

population() 只暴露实际完整坐标；身份复用与信息丢失见 §5.3.1。
已绑定轴可从原端点 population().axes，或对应轴的 projections 取得；
后者按 compare 的 current/baseline、align 的 left/right 路径记录投影；Artifact 只保存相应输出描述。
member() 有歧义时须提供明确轴，不能将同 Entity 的两个版本投影混成一个身份轴。
引用的来源及读取证据不等于当前档案存在证明。

#### 4.6.1 to_pandas 的固定交接合同

Artifact.to_pandas() 只读精确已提交结果，返回完整隔离副本；不执行 Query、不刷新来源或 Catalog。
导出保留完整键、nullable 量、两侧 presence 和纯数据描述；不导出语义对象、图、政策闭包或内部组件。
值和时间精度不能降级，空表仍保留类型；读取/转换/实际分配失败时沿现有错误边界失败，不抽样或截断。
固定宽表使用 RangeIndex 和 kind/name 两层列名；列布局、dtype、元数据与隔离要求由
[导出布局](lazy-analysis-dsl-execution.md#3-pandas-导出合同) 拥有。

外部程序自行选择 NA/共同有效样本、完整业务键及缺侧政策；RangeIndex 不是身份。
pandas 变换后的值、标签和 attrs 不授予 DSL 权限，也不接受回灌成 Artifact 或 Query。
继续语义分析须重新构造 Query；外部完整键/数值由脚本作为普通显式输入，见 §2.3。

### 4.7 对齐、比较与参照

```python
Correspondence = CalendarCorrespondence | DeclaredCorrespondence


def ordinal() -> CalendarCorrespondence: ...


def corresponding(
    *,
    via: tuple[RelationshipRef, ...],
) -> DeclaredCorrespondence: ...


def broadcast(value: Column, *, to: Query) -> Column: ...
```

align/compare 的 how 选择完整配对（exact）或键像之并（outer）；省略 match 只允许相同绑定下的键对应。
所有 Correspondence 都是不带轴的规格：左右轴在 align/compare 绑定时分别从两个操作数取得，
不重复传入两侧轴。日历对应要求每侧恰好一个日历轴，身份对应要求每侧恰好一个成员轴；
缺少或有歧义时拒绝并披露两侧实际轴。其余轴仍按声明的完整坐标规则对应。
`ordinal()` 按完整有序日历桶的位置配对；首期要求两侧相同的已解析 Grain 和边界时区：
内建粒度须 unit/count 相同，认证日历须同一日历定义快照和 level，不能混用两种日历权威。
两侧期间范围可以不同，但每个实际桶必须等于其名义桶；不自动重采样、补桶或把截断桶当作完整期。
位置以两侧绑定的日历桶序为准，结果过滤不压缩日历序号；完整结果键的缺侧仍按 how 处理。
exact 要求两侧桶数相同且完整键全部配对；桶数不同时 outer 保留较长日历尾部，但不放宽粒度或完整桶要求。
这只建立位置对应，不自行计算窗口或证明同比意义；时间扩展见 §4.9。
`corresponding(via=...)` 只解释身份轴之间的对应，
via 按左身份 → 右身份的声明方向连接，例如 `a.align(b, match=mv.corresponding(via=(CustomerMap,)))`。
普通字段轴不能因值像主键就进入该入口。路径须具有所需的一对一结构，角色和版本解释必须明确；
版本绑定来自各侧成员轴，不能推断为当前时刻，缺少所需锚点时应先修订对应侧的身份域定义。
结构一对一不证明本次键域完整配对，实际覆盖仍由 exact/outer 检查。
路径能直接解释完整键时不增加来源读取；需要查值时，Query 必须显式形成所需对应依赖。
Artifact 的两侧投影仅供读取和 pandas 使用，不提供对应算子。
不同期间配对默认产生对应域并保留两侧日期投影，不伪装成真实观察日历。
显式 compare(time_axis="current") 的受约束当前轴投影见 §4.9.5。

`align` 输出 `left.<name>`、`right.<name>` 以及 `left_present`、`right_present`。
exact 检查完整键唯一和键像相等；outer 使用已声明共同域上的键像之并。
相同轴绑定的恒等对应保留这些共享坐标轴；非恒等对应保存自己的左右投影，不将其重标成任一侧原轴。
outer 的缺侧值为 NA，独立 presence 为 False；已有端点坐标投影明确缺侧。
不生成逐值 missing_left/missing_right 原因，也不据缺侧证明业务空贡献。

`compare` 要求两侧具有相同且非空的量名集合，各同名数值量逐一可比较，输出
`<name>.current`、`<name>.baseline`、`<name>.absolute`、`<name>.relative`
及 current_present、baseline_present；outer 缺侧由相同业务侧的 presence/坐标投影表示，
错误位置沿 current/baseline 角色，align 使用 left/right。绝对变化为 `current - baseline`，相对变化为
`(current - baseline) / abs(baseline)`，保存两个端点及对应关系。zero_denominator 只控制新生成的 relative：
共同非 NA 的零基期默认得到 NA，absolute 仍按原式完成；显式 error 才使 execute 失败。
输入指标内部的政策不被 compare 覆盖，严格子式失败仍阻止整次发布；
只请求差额使用 align/select，不计算 relative。
量名、数值类型、单位、完整键、本次对应及两侧已知定义约束均检查；
业务可比性仍由脚本/调用方判断，不能从同名列推断。历史 Artifact 的比较交 pandas。
两侧不要求相同 Metric 定义；一般比较对应[代数 §5.2](algebra-theory.md#52-对齐比较与输入口径)的
对齐后逐点组合，同一指标在两个上下文中的比较是其特例，不限制合法字面量或 Query 变换端点。
两侧各自有无成员绑定记录在比较定义中；“固定同一批人”要求共同固定成员输入或实际身份相等证据，
标量坐标相同不能证明人群相同。
比较输出两端值、presence 与实际坐标投影；量定义和输入语义归两侧 Query，不进入 Artifact。
不保存历史端点图或专用细状态，不提供直接 drill，relative 也不可直接上卷。

`broadcast` 沿目标键到参照键的完整投影取值。
参照列已经携带完整坐标域，不再重复传 by；
由已绑定轴的完整投影、已保留父子映射或合法 coarsen 确定唯一 `h: K_target → K_reference`。
零轴参照必须恰有一个键；分组参照须匹配它的全部轴。参照键须唯一且覆盖所有目标键。
不自动少匹配某个维度，不因同名字段猜对应，也不因去重后只剩一行就当作零轴标量。
缺少或存在歧义映射时拒绝；先显式调整参照的粒度或建立对齐，再广播，不能用 by 掩盖未匹配的参照轴。
它复制取值出现而不复制原事实，也不自动赋予求和或原指标上卷能力。
broadcast 接受任意相容值 kind，纯粹建立取值对应；share 另有数值、单位和贡献包含要求。

execute 不接受保留请求；Artifact 保存最终输出，见 [保存信息](lazy-analysis-dsl-execution.md#12-默认保存信息)。
需要细域、更早历史或其它窗口时，构造相应 Query 再 execute；已产出数据的计算交 pandas。
名单使用 query(population).execute() 保存；需要身份细项时，在 observe 的 by 中直接请求该身份轴。

### 4.8 份额：当前总体与分组总体

Query 的 `share(name, ...)` 是唯一公开份额入口。`name` 精确选择接收者已有的一个数值量；
它根据 Query 定义和本次实际输入检查贡献/组件依据；Artifact 不提供 share。
返回新的 Query，保留原键、轴、列及其实际信息，并新增无量纲列 `<name>.share`。
例如 `current.share("sales")` 保留 sales、refunds、aov 等原列，只为 sales 构造分母。
原接收者不变；空接收者仍为空输出，不因内部需要标量参照而增加总计行。
未知列名、非数值量或新增列名冲突时明确拒绝，披露实际可选列；不覆盖、自动加后缀或猜列名。
需要自定名称或只保留份额时，继续使用 select。Column 不提供 share，也不保留兼容入口。

分母总是从接收者当前输入自动构造：

| 调用 | 分母与输出 |
| --- | --- |
| `current.share("sales")` | 当前接收者范围内 sales 的合法总体；所有行共用该分母，新增 sales.share |
| `current.share("sales", partition_by=(region,))` | 每个地区内部的 sales 总体；每行使用自己地区的分母，原细坐标不变 |

partition_by 默认为空元组，表示跨接收者全部当前坐标构造一个总体。
它消费调用处的数据范围，包括之前的 where/top；不恢复已筛掉的贡献，不推断整个数据库或筛选前总体。
地区×月份数据默认仍是当前全部地区和月份的总体；每月占比必须显式传 `partition_by=(month,)`。
partition_by 只使用已绑定轴或合法 coarsen，沿完整轴元组建立分组；不能重新读属性或丢掉不匹配行。
它只决定分母分组，不折叠输出坐标；未指定的轴均在各组内合并，不按字段名猜应保留哪些轴。
每组总体从所选原量的实际贡献或充分信息合法求得，原量的单位、政策、检查和必要输入保持；
其余保留列不参与总体构造，不能因为表中还含 aov 就尝试对 aov 求份额。
分母能上卷仍不充分，分子贡献包含及可加性须同时满足 §5.3.3；不以任意当前列的 sum 代替。
上述业务份额与一般参照比值的数学区分见[代数 §5.3](algebra-theory.md#53-参照与共同有效样本)。
不接受外部参照列或裸数值作为分母；对筛选前总体的份额通过先 share 再 where/top 表达，
预算达成等普通比值继续使用 `/`。

份额在调用处绑定分母依赖。以固定 East=320、West=150 为例：

| 顺序 | East 份额 | 分母范围 |
| --- | --- | --- |
| 先 share，再 where/top 选择 East | 320/470 | share 输入的全部地区 |
| 先 where 只留 East，再 share | 320/320 | 筛选后的 East |

后续筛选不改变已经绑定的分母；优化不能把筛选越过 share 后缩小参照。
构造、repr、contract 不读数值或计算分母；同一来源 Query 的每次 execute 仍重新求值，
绑定分母依赖不等于提前固定其值。分子与分母消费同一实际输入，
不能分别执行两条来源分支后仅凭共同逻辑节点认定支持；执行与保存见
[执行合同](lazy-analysis-dsl-execution.md#111-共享逻辑节点与执行内输入)。
新增份额列不继承原指标 drill/rollup；原列保留自身信息，
需要继续原量时从原接收者或仅投影原列的 Query 出发。含份额列的 Query 不能整体按原指标上卷。
zero_denominator 只控制 share 新生成的份额，按语义 §3.4 处理，不覆盖分子/参照的内部政策；
符号与支持依据由 §5.3.3 拥有。

### 4.9 滚动窗口、累计与期间比较

所有时间动作仍 lazy。ObservationWindow 只表示无轴的不可变窗口规则，没有绑定轴的第二形态或独立执行生命周期。
Metric.window 是应用窗口的唯一入口；observe(during=...) 只接 TimeScope、CalendarAxis 或 None。
期间、评价端点与日历在 observe 形成一次逻辑绑定：固定输出轴、端点及 W(t) 规则，不提前读取业务数据或冻结快照。
后续 Query 操作运输该绑定，不能重选窗口或把“期”重新解释为另一粒度；实际 schema/日历覆盖与数据检查仍在各自阶段完成。
Metric 可以拥有显式窗口变换，但没有隐藏的 during 默认。CalendarCorrespondence 仍按原合同绑定。

```python
def all_history() -> ObservationWindow: ...
def trailing_duration(*, duration: timedelta) -> ObservationWindow: ...
def trailing_periods(*, count: int) -> ObservationWindow: ...
def since(*, start: datetime | InstantAnchor) -> ObservationWindow: ...
def to_date(*, grain: Grain) -> ObservationWindow: ...
def period_offset(by: int) -> CalendarCorrespondence: ...
def previous_year() -> CalendarCorrespondence: ...
```

构造器仍由 mv 提供一个 canonical 入口，不再复制一套 ms.cumulative/ms.window 工厂。
无轴规则只捕获常量和可独立解析的日历定义，可在受限声明体中调用或捕获；其内部属于依赖中立时间合同，不加载 Session 执行器。
since 的 datetime 必须带明确时区，归一到精确时点；不能从声明加载环境猜时区。
InstantAnchor 保留原有归属；捕获 Session anchor 的定义只在相容会话内可用，不能冒充独立可加载模型。
duration 是有限正 timedelta，以固定实际时长计，不接月/年等可变单位；count 是非 bool 正整数。
持续时长先在唯一 instant 轴减去 Δ，再呈现本地边界；不能直接做跨 DST 的本地墙钟减法。
显式 grain/时区约束与消费方日历冲突即拒绝；无轴规则不冻结一个未来 Session 的实际日历或“现在”。

`Revenue.window(mv.to_date(grain=month))` 可直接匿名观察或由 @metric 返回，均配 `during=days`。
Metric.window 返回同一种受控 Deferred；命名只追加声明记录，不改变规则、需求域或计算指纹。
命名窗口的 `ms.calendar_grain(calendar=ref, level=...)` 保留日历查找依赖，发布时校验 kind/path，
并进入编译依赖闭包。observe 使用 Session 已冻结的认证日历作用域解析它，不读取日历后端或重新认证。
绑定指纹包含实际 snapshot digest；已有 Query 不随当前认证清单变化。缺失认证、未知层级、时区冲突和覆盖不足在业务读取前拒绝。
无快照的 Grain 按该作用域解析；显式携带认证快照的 Grain 必须与 Session 的认证快照一致，冲突即拒绝，不能按同路径静默覆盖。
readiness 仍显式检查日历权威，不为命名表达式新增物理检查入口。
不保留构造器的 axis 参数或 during=window 别名；旧提案示例统一改写为 Metric.window(rule) 加显式观察期间。

期间对应与 §4.7 的 ordinal 一样不带轴，在 align/compare 绑定时从两侧操作数取得唯一日历轴。
同比写法为 `current.compare(baseline, match=mv.previous_year())`。

#### 4.9.1 一份日历权威，分别绑定输出与贡献

TimeScope.by/coarsen/to_date 的 grain 统一只接受 Grain，不接裸字符串；by/coarsen 不再另设 count。
内建粒度用 mv.grain("day") 或 mv.grain("minute", count=5) 构造；count 只属于该构造器，
day/week/month/quarter/year 要求 count=1，sub-day 宽度须平铺 civil day。
认证日历粒度由 catalog.period_calendars.get(ref).grain(level) 取得，没有另一个 count 参数。
两者遵循 [统一时间语义](../temporal-semantics.md#unified-grain-and-time-scopes) 的同一 Grain 合同。
消费者仍分别校验合法分桶、coarsen 的唯一粗化映射或 to_date 的唯一包含层级，不因类型相同免除约束。
scope 时区须与认证日历的边界时区相同。日历认证不等于业务来源完整性。

CalendarAxis 保存完整名义桶、截断实际区间、时区、次序、Grain/快照身份。
名义桶 N(t)、实际桶 A(t)=N(t)∩S 与贡献窗口 W(t) 的区别进入[代数模型](algebra-theory.md#54-固定域上的线性与时间边界)；
普通桶观察使用 A(t)，窗口按自己的定义求 W(t)，不能从输出截断范围反推或缩短全部贡献。
每个非时间分组有明确适用域中的所有预期桶；有没有订单不能决定开业日期。
x(g,t)=Evaluate(m, Bind(m,g,W(t),context;B)) 是完整窗口基准语义，
不要求先日预聚合或实现 merge。贡献范围由 W(t) 的并决定，不是显示范围。
显式 P 的筛选和固有日期条件保留；十月订单主体不能自动扩大成七日完整来源。
重叠窗口保留(output_key,component,contribution_occurrence)，不全局去重。

#### 4.9.2 窗口的边界

| 窗口 | 对输出桶t的贡献范围 |
| --- | --- |
| all_history() | 无显式下界、时间早于 actual_end(t) 的全部合格来源贡献；显示起点不截断历史 |
| trailing_duration(Δ) | [actual_end(t)−Δ,actual_end(t))，按实际经过时长 |
| trailing_periods(n) | 含当前桶的 n 期：从首前驱期间起点到 actual_end(t) |
| since(a) | [a,actual_end(t))，a须早于所有评价端点 |
| to_date(p) | [reset_period(actual_end(t),p).start,actual_end(t))；输出日历桶另须满足唯一包含约束 |

期间回看要求完整的day/week/month/quarter/year或语义桶，部分桶不能冒充完整期。
所需前驱来自完整日历；认证不足报日历覆盖错误，不制造期间。
count为非bool正整数，有限窗口半开，all_history 只有严格上界；不扫描最小事件时间来伪造历史起点。
to_date 的重置期间由精确评价端点 e 确定：选择唯一满足 left < e <= right 的期间，故 e 恰在边界时使用刚结束的期间，
不靠减一微秒/一个精度 tick 近似。标量 TimeScope 的起点不参与该选择；例如 [09-01,10-08) 与 [10-01,10-08)
的标量 MTD 都求 [10-01,10-08)，不能因第一个显示范围跨月而拒绝。
有 CalendarAxis 时还要求每个名义输出桶唯一包含于一个重置期间，跨月周不能归某个月；截断桶用 actual_end 评价。
标量端点合同不放宽这一日历层级约束；所需重置边界/前驱仍由相同日历权威给出并检查认证覆盖。
trailing_periods 按日历期间计，七自然日不等于168小时；固定时长用 trailing_duration，可在日输出端点评价，
不要求为了得到168小时窗口而改变成小时输出。切穿预聚合日值时须重新取原贡献，不能按比例切分日总额。
all_history 是显式选择全部可用历史，不保证业务历史完整；固有 where、members 和来源保留范围仍有效。
已声明有有限采样覆盖要求的状态指标不能由无下界窗口自动满足，应显式 since 或其它有限窗口。
不含当期的回看、期初零、min_periods、插值、前值填充或任意累计seed均不提供。
这里的“期初零”指业务输出或初始状态；[代数 Cum 的零延拓](algebra-theory.md#54-固定域上的线性与时间边界)
只用于固定格子算子的恒等式，不生成额外日期，也不把缺测变成零。
不同窗口可分别 observe 后按完整输出轴 align/select，也可用各分支显式 Metric.window 后按共同输出键组合。

#### 4.9.3 覆盖、缺桶与执行检查

普通观察默认信任声明的数据源，按指标和本次绑定范围内实际读取的数据求值。
缺少完整性证明本身不拒绝计算，也不要求调用方逐次声明完整性；这不是已通过来源完整性审计。
首版不提供外部业务缺口的声明、注入或消费能力，也不接收上游漏数通知来阻塞、传播或恢复窗口。
查询成功但上游静默漏数时仍按已读数据计算，不承诺检测漏数；某天无记录、最大事件时间较早都不能证明漏数。
调用方掌握的来源完整性信息用于判断是否采用结果，不会自动成为本次 Query 的执行事实。

执行仍检查自己能够验证的条件：认证日历能否覆盖请求期间、完整键与映射是否有效、
显式输出域是否覆盖被消费贡献、broadcast/exact 对应是否满足各自的覆盖要求，以及来源读取和计算是否成功。
这些失败均保留具体作用范围并阻止整次产物发布，不转为普通 NA；通过这些检查不证明业务数据完整。

| 事实 | 行为 |
| --- | --- |
| 成功读取且无贡献 | sum 按 empty 政策为 0 或 NA，count 为 0；mean 等无有效值为 NA |
| 已读普通 NA | 按算子忽略或报错；sum 无有效值按 empty，不追踪其与空输入的区别 |
| 日历认证范围、完整键映射或请求域覆盖检查失败 | 结构化错误，不发布部分 Artifact |
| 本次计算缺少必需字段或充分信息 | 信息不足错误，不当作普通 NA；导出后缺日期由外部统计自行检查 |
| 重复键、非法值、来源读取失败 | 结构化错误 |

原 Query 的必要检查不能被后置筛选、丢列或乘零消除。
来源修复后可重新观察，已有成功 Artifact 不被改写。
读取范围与当时实际检查摘要按[默认保存合同](lazy-analysis-dsl-execution.md#12-默认保存信息)作为 Evidence
随成功结果保存；普通 observe 的 Finding 为空，不记录虚构的完整性认证或外部业务缺口状态。

#### 4.9.4 原指标窗口与外部统计

窗口在 observe 取得本次端点后，直接对 W(t) 的完整输入求原指标；规则只来自 Metric.window。
Query 不提供重设窗口；换窗口或评价粒度时构造新的 observe，不能借窗口扩大 where/top 已选范围。
本版对待上卷原量的整个依赖闭包检查 window（包括 per 或条件表达式内的 window）：
若 observe 绑定了日历轴，rollup 必须原样保留该时间轴及其每个端点/W(t)，只允许按其它轴做合格的空间上卷。
粗化、替换或删除该时间轴均拒绝，即使只有一个桶、count=1、规则为 all_history，或碰巧能算出相同值也不例外。
该拒绝在规划时给出 window 依赖位置和原/目标轴；Query.contract 的上卷指引只列保留该时间绑定的候选。
标量 TimeScope 观察可保持原端点/W 做空间上卷；没有日历轴不意味着可以改端点。普通无窗口量仍按原 rollup 合同处理。
where/top 只选择既有坐标，select/空间上卷保留原时间绑定及必要检查，不把选中端点重组成新窗口。
时间格子的 sum/mean 属于 Query.reduce；粗粒度原窗口值须新 observe，不能在 rollup 中选择“并集”“末端点”或“完成值相加”。
同一依赖路径只允许一次窗口；已有子窗口时再次 window 拒绝，不以相同规则为由重复应用。
不同算术分支各自带窗口合法；普通 during=CalendarAxis 只提供输出坐标，不覆盖这些规则。
普通窗口分支沿用[执行 §1.1.1](lazy-analysis-dsl-execution.md#111-共享逻辑节点与执行内输入)的读取合同，
不因基础表达式相同而强制共同输入、快照或共享扫描。旧 first-seen 的 H/W 配方只在一致输入前提下等价；
这不是普通 window 的准入条件，也不由名称、哈希或 Python 变量复用自动证明。具体条件见
[语义附录 A.1.1](../semantic/expression-model-redesign.md#a11-等价迁移的判定条件)。
无轴规则不能直接传 during；窗口 Metric 不能省略 during，不能隐式取当前时间。
TimeScope 可为 all_history/since/trailing_duration/to_date 提供唯一 end 评价端点；标量 to_date 按 §4.9.2 选重置期间，不用 scope.start 截断 W；
trailing_periods 必须有 CalendarAxis 才能确定“期”。状态采样/内部域的额外期间要求继续按各自合同检查。
来源叶子须有合法时间角色；不同根保留各自显式角色，不用一个字段覆盖所有根。
过滤、版本、Population 选择与固有固定范围不被窗口扩大；约束冲突仍拒绝。

当前格子滚动统计交 pandas，状态与日历检查见 [终端窗口示例](lazy-analysis-dsl-examples.md#214-终端-artifact-与-pandas-日格子统计)；
它不替代原窗口指标的组件重算。
精确 distinct 需精确身份集合，精确 quantile 需精确 bag/分布或完整输入；近似规约从窗口贡献重新求值，或使用另证合格的兼容 sketch，不能合并完成估计值；
来源 mean/weighted_mean 的合格路线保留 sum/count 或共同配对的乘积和/权重和，不能合并完成均值。
内部 per 规约还须保留足以按内部键重新完成子指标的信息，不能直接套用来源规约摘要。
状态采样规约仍检查时间规约前的采样时点对齐；重叠窗口和累计端点不因显示成多行就可直接相加。

#### 4.9.5 期间映射、同比环比与当前时间轴

对应是当前完整期间键到基期完整期间键的部分单射，保留两侧原边界。
period_offset按真实日历全局次序移动；by=-1在日轴上是昨日，不是上月同日。
previous_year支持内建day/month/quarter/year同名期间，闰日无对应不夹日；
week/sub-day 与 53 周零售日历不提供同比对应，不能用-365替代同比。
完整名义桶、时区/日历身份与部分单射均检查；缺侧不等于日历认证缺覆盖。
日粒度 current 含 2 月 29 日且 baseline 上年无对应日时，exact 失败；outer 保留该当前日并将基期标为缺侧。
反向出现仅基期存在的闰日也按缺侧处理；默认 paired 保留它，time_axis="current" 仍不能伪造当前日期。
修复是显式选择 outer，或重新构造期间/粒度；不自动删除闰日、夹到 2 月 28 日或重采样。
完整月/季/年桶可按同名期间对应，包含闰日并不单独导致失败；窗口贡献跨越闰日也不等同于日键无法配对。

两侧先各自求窗口，再compare。2024-03-01七日逐日映去年不等于2023-03-01自己七日，
窗口与期间对应不能无证明交换。累计分别从各自起点计算，不对增长率求和。
compare默认paired保留双方日期投影；显式time_axis="current"要求每行都有当前日期，
outer只有基期的行不能丢弃或伪造当前坐标。
增长序列可交 pandas 做普通滚动统计，但不能作为原收入窗口或时间 rollup。
相对变化(current−baseline)/abs(baseline)，共同非 NA 端点的零基期按 compare 的 zero_denominator 处理；
默认 relative 为 NA、absolute 仍有效，显式 error 才报错。只需差额也可用 align/select。

## 5. 关键求值约束

### 5.1 身份约束、贡献与输出域

省略 members 时按各组件定义直接形成贡献；显式身份域 P 再沿合法路径限制贡献归属。
多个根逻辑上按各自贡献规约后组合，同根不同使用保留各自出现；有 per 时保持完整 (k,j) 而非先压到最终 k。
物理关系展开不得改变各根的贡献次数；合法关系读取、键集合去重及预聚合计划按
[语义 §3.7.2](../semantic/expression-model-redesign.md#372-逻辑贡献约束与物理执行策略)准入，不以是否先 join 原始表作一刀切判定。
每个组件有值读取 v、贡献出现 C 及完整分类 a:C→K；membership 先将根事实展开为完整桶上的集合出现，窗口出现还携带输出目标。

| 有效输出轴 | K |
| --- | --- |
| 零轴 | 标量单例，包括无贡献或空 P |
| member()，可附加 P 上单值字段 | 显式 P 的完整身份及其分类元组，含无贡献身份；缺 P 时不能仅靠标记猜 Entity |
| 普通字段或显式 membership，未请求身份轴 | 各组件有效贡献的完整分类/成员桶像并集；多 membership 为每根贡献的集合乘积；有无 members 都遵守此规则 |
| 仅日历 | 全部请求桶，包括空桶 |
| 上述非日历轴加日历 | 已确定的完整非日历元组域 × 请求日历桶 |
| 显式 Population | 该 Population 的完整坐标，包括无贡献坐标 |

`members=P` 只限制贡献，不自动补齐 P 的全部地区或类别；需要这些完整分类时，
显式 `by=P.classify(by=fields)`。需要完整身份域时写 `members=P, by=(member(),)`，
不另以 `by=P` 重复请求同一身份域；一般乘积域只用作 by，身份约束先投影到单一 Entity 轴。
by 中的 Population 只指定输出，不隐式设置 members；消费贡献仍须合法分类且被 K 覆盖。

规约忽略 Null 值不删除分类键，源 Null 分类保留 None。
显式目标域须覆盖全部被消费贡献，遗漏键报错；限制贡献要 where/members，不借缺键静默删除。
每个组件的键/值使用同一分类输入；另一个组件的键不证明该组件空贡献完整。
纯字段分组全部无键时为空域，标量仍单例，显式空域不是空规约格。
member_count 须有 P 且每格身份分类明确；其贡献 C=M 的分类像参与共同 K 的并集，
因此明确输出这个指标可以增加其他组件的空格子。仅传 members 不会产生这种扩张。
观察窗口不把身份数变成订单数或乘以天数。
完整输出、来源覆盖、物理信息充分、类型/执行资格分别判断。

### 5.2 观察条件、分支过滤与内部域统计

| 位置 | 含义 |
| --- | --- |
| observe.where | 全部来源组件的共同范围，不改写显式身份约束域 |
| Session.members(..., where=...) | Entity 来源身份选择，可显式读取声明字段 |
| Population.where | 既有坐标上的域测试，不新增属性读取 |
| 基础Metric.where | 新定义的固有来源条件，作用于一个完整贡献/配对作用域 |
| Query.where/top | 当前结果坐标选择，保留原必要检查 |
| Metric.per 后规约 | 每个外部格子 k 内由 Field 推导 J_k 或使用 Population 的相容样本；逐 (k,j) 完成子指标，再规约 |
| 字面量域observe→reduce | 本次固定名单上的格子统计 |

促销订单的客户像不让下一次收入自动只计促销订单。
内部展开键 (k,j) 到外部 k 的投影明确；不要求裸内部键 j 唯一决定 k。
同一客户可以分别进入多个地区的客户均额，同一时点也可分别进入多个设备的 P95；
仍须验证各贡献到完整 (k,j) 的合法绑定，不可默认为正值/非 Null 主体。
只在模型收录时要求依赖闭包可独立持久解析，临时构造保留已有 Session 归属与所需绑定。
运行时域计数用query(population).reduce(row_count)，不经Metric伪装来源或计算时间角色。
结果统计保留实际输入检查，不能通过只取键或成功计数掩盖另一必需操作失败。

### 5.3 执行前组合与粒度变换

| 动作 | 权威 |
| --- | --- |
| 新observe / 固定身份域read/classify/image | 新明确来源依赖，固定身份不扩大 |
| Query.rollup | Query 定义 + 本次实际输入或充分中间信息，按原公式完成 |
| 观察Query.drill | 原观察定义和新增细绑定，保留父选择/检查 |
| compare | 两个端点和显式对应，不细化任何来源 |
| Query.share | 本次输入的总体、可加贡献包含与完整映射 |
| reduce | 当前格子上的 sum/mean/row_count，创建有明确样本的新量 |

rollup只消费选中坐标的原信息，不恢复未选贡献。依赖 window 的原量先通过 §4.9.4 的时间绑定保留检查，
不能因有原始输入或充分摘要就重绑窗口。均值不能合并完成均值，
比率须重求组件再组合；tag重叠分组不是不交分区。
per(...).mean() 的地区 sum/count 不能区分“两个地区是同一客户”与“两个不同客户”，
总体值可能为 400 或 200；先按内部身份合并子指标才有充分状态。执行器必须规划必要的内部键/子状态，
没有合格路线则拒绝；不能先丢弃它们再对完成值求平均，也不为将来执行默认保存这些状态。
按已有轴或合法coarsen建立唯一完整K→J，J为输入坐标的分组像；
需要保留空父坐标时在原 observe 请求完整 by=Population。被粗化丢弃的身份轴不另存隐藏名单。
来源Query上rollup也保留细网格完成检查，不退化为绕过失败的粗observe。
已丢父属性不能隐式回源补轴；应构造新观察。
比较量/任意派生输出不继承原指标rollup；需要粗比较时分别上卷端点后compare。

#### 5.3.1 复用身份坐标与信息丢失

设实际保留的身份坐标为 π_E:K→U_E。选中 S⊆K 后，population().project(by=身份轴)
取得 π_E(S)，按完整身份去重；身份×日期域的格子数与身份投影的大小可以不同。
普通 customer_id 字段仍是字段轴，只有明确的 Entity 身份轴才可直接作为 members 输入。

where/top/select 保留所选坐标及现有投影；align/compare 只保留显式两侧坐标对应。
outer 缺侧必须先按该侧 presence 限制到投影定义域，不能生成虚构身份。
rollup/reduce 若丢弃身份轴，结果就失去该名单；不会为每组保存或合并隐藏的 K×U_E 关系。
保留全局输入身份域及每组总额/人数，也不足以恢复每组当时的身份分配。

需要在本次 DSL 图中复用身份时，应在 Query 的 by 中请求身份轴，并在 execute 前投影。
需要在执行后追问旧分组，应显式输出身份及分类坐标供 pandas 使用；汇总 Artifact 不恢复隐藏名单。
独立保存的分类域与另一收入读取不因此共享快照；不能把当前分类附会到旧汇总。
要读取当前属性或重新关联身份，显式 read/classify/image/preimage 或新 observe，并披露新依赖。

#### 5.3.2 下钻的完整契约

下钻只细化可追踪到来源观察的 Query，保持P/无P、组件、固有过滤、范围、属性版本和参照。
细域L为当前轴加新增轴，r:L→K 为丢弃新增轴的投影，且贡献细分类a_L满足r∘a_L=a_K。
新增细轴必须覆盖被选父纤维的全部实际贡献，源Null归None；
没有细信息不等于空纤维，不把粗均值/差额按人数均摊。

来源Query先在父域完成where/top，取得S后才细化r⁻¹(S)，保留父必要检查。
未选父域的新增细分类不属于本次细化检查，但原父错误仍失败。
先细化再筛选声明了更大细化范围，与先筛选再细化不一般等价。
观察Query的新execute可以重选来源P，
固定成员输入则保持身份，Artifact 从来不是来源 Query 的输入。

| 接收者 | 准入 |
| --- | --- |
| 可追踪的Observe Query及合法选择/投影/上卷 | 明确来源细化 |
| Artifact | 终端产物，不提供任何 DSL 变换 |
| compare、任意align、当前格子reduce、含份额派生列的输出 | 直接drill拒绝；在明确原端点上细化再组合 |

compare不自动细化两侧，也不为新增细轴运输原对应。
分别细化端点后，调用方明确选择 how 及新的 match；
父exact不证明细exact，父月ordinal不自动授权日配对，缺侧不补零。
直接compare.drill在构造时拒绝，修复指向原端点 Query 或明确新observe，不回放比较lineage。
比较可保留端点坐标投影；端点原指标 rollup 在原端点对象上调用，drill 在原端点 Query 上调用。

完整细域与合法合并下 q.drill(add=a).rollup(by=q 的原轴) 可恢复 q 的原量；
这些规律不能套用于任意比较变化率或重叠贡献。

#### 5.3.3 份额、普通比值与贡献证明

对 `r.share(name, ...)`，新增列 `name.share(l)=r[name](l)/reference(h(l))`，
reference 按 §4.8 从当前输入及 partition_by 构造。h 必须唯一、完整，分母保留符号。
数值/同单位不够，须有可加性及分子贡献包含于参照的实际支持依据。
本次执行中同一实际输入的选择/合法上卷、合格共同输入路线与真实过滤祖先链可以证明；
同Metric、同Query、同Run、相同fingerprint/身份或恰好相等值都不能证明共同输入。
Query 的 share 须在算子内部使用已准入的共同输入路线，否则报告执行能力缺口；不能提示先 execute 再 share。
共同支持证明不能用两次读取对账替代（[执行与恢复 §1.1.1](lazy-analysis-dsl-execution.md#111-共享逻辑节点与执行内输入)）；普通比值仍可跨独立读取。

基础可加m.where(p)只在同实际输入、原值/出现/单位/政策和所需字段保持时证明子集；
不证明任意独立谓词之间的蕴含。distinct需精确集合包含，不以最终计数代替。
mean/AOV/转化率或 revenue + 10 的完成值不是可加贡献。部分城市对完整地区总额的份额先 share 再 where 选择城市，
合计1还需要完整不交分区；重叠标签不能靠归一化修复。
共同非 NA 零分母按 share 的 zero_denominator 处理：默认份额为 NA，显式 error 报错。
允许有符号120/−20对100的1.2/−0.2；
只有非负、正总额和完整不交分区才保证[0,1]及合计1。
NA 分子仍为 NA；分母按原量已声明政策合法完成，允许忽略普通 NA，但不能绕过原输入的必要检查。
份额只解释声明贡献及可用值；NA 行不支持完整非空分区的合计保证。
后置 where/top 不自动换分母，前置 where/top 改变总体；新份额列不继承原指标 drill/rollup。

### 5.4 Nullable 值、部分运算与谓词

量只有类型化值或 NA，不暴露四态、原因码或 is_defined/is_undefined/is_unknown。
Column.is_null() 返回非缺失布尔，~column.is_null() 选择非 NA；缺坐标另有独立 presence。
普通算术任一输入 NA 则输出 NA；共同非 NA 数值对的零分母统一按
[语义 §3.4](../semantic/expression-model-redesign.md#34-样本政策的归属) 的 null 默认或显式 error 处理。
Column.divide、share、compare 使用同名参数；参数只控制本算子，不覆盖已有子式或定义。
非有限计算、溢出、结构错误和实际覆盖检查失败均使 execute 失败；不以 NA 代替失败结果。
没有逐格失败后的继续传播，也不发布部分成功结果。

比较产生 nullable boolean，逻辑组合使用强 Kleene 规则。where 只保留 True；False 和 NA 均不入选，
与 pandas nullable boolean 索引一致；必要子表达式/来源检查仍不得消失。
因此 where(~p) 不等于 population − where(p)：后者可包含 p 为 NA 的坐标。
字段 NA 分类仍是合法 None 键，分组须保留该组，不能采用 pandas 默认 dropna=True 改变贡献。

优化必须保持有效样本、政策和检查。sum(x+y) 与 sum(x)+sum(y) 在缺失 mask 不同时不等价；
固定共同有效域上的线性规律仍成立。NA 可以忽略不授权丢掉完整坐标、改用有效值数代替 row_count，
或把必要检查失败变成普通缺失。Query 与本次执行计划使用原节点政策；Artifact 只保留完成值及必要输出描述，后续计算由脚本在 pandas 中显式定义。
