# Lazy Analysis DSL 定义

Date: 2026-10-14

Status: proposed v0.25；设计提案，尚未实施，不构成当前 API、后端能力或 Runtime 验收声明。
本文仅定义目标 DSL 的对象、签名、绑定规则与求值语义；v0.25 默认信任声明的数据源，
仅依据明确缺口传播窗口 Unknown，不要求调用方逐次声明完整性。
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

Metric 定义业务计算；Session.observe 一次绑定期间、分组、共同条件与可选身份约束；
Query 保持 lazy，显式 execute 返回固定 Result。Result 变换返回 Query，只消费实际保留信息，
不隐式回源。完整输入求值是指标定义，摘要只是合格的等价执行路线。

本版包含 Domain、观察、选择/Top-N、对齐/比较、参照/份额、原指标上卷与端点细化、
sum/mean/row_count 结果规约、原指标窗口和期间对应。普通分类、描述/配对统计和格子滚动统计交 pandas。
不包含 Event、journey、lifecycle、funnel、Anchor、retention、序列回放、预测、抽样或变化归因；
不提供自动比较下钻、原生 Metric Ibis 桥接或外部结果回灌。

v0.25 另暂不支持以下边缘能力；需要时分别重新 observe、调整算子顺序、在语义层声明唯一路径或交 pandas：

- execute 保留请求，以及依赖它的 Result 下钻、Result 重设窗口和固定离线细比较；
- Query/Result.window 重设窗口、显式参照份额 share(of=...)、rollup/drill 的显式 Domain 形态；
- observe 主体路由补全、期末左极限版本锚点；
- 绝对时长回看、不含当期回看窗口、命名期间映射与父期间内日序配对；
- 结果规约的 nulls/empty 选项与 Column.divide 的零分母选项。

### 1.1 普通分析的最短路径

```python
import marivo.analysis as mv
import marivo.semantic as ms

catalog = ms.load()
s = mv.session.get_or_create("apac-sales", report_timezone="Asia/Shanghai")
revenue = catalog.metrics.get(ms.ref.metric("sales.revenue"))
region = catalog.fields.get(ms.ref.dimension("sales.region"))
week = s.time_scope(start="2026-10-05", end="2026-10-12")
daily_sales = s.observe(
    {"revenue": revenue},
    where=region == "APAC",
    during=week.by(mv.grain("day")),
).execute()
daily_sales.show()
```

Metric 已拥有业务时间角色与政策，不要求调用方再指定 Entity 或身份域。
日历提供七个输出格；没有已知缺口时，空日按 Metric 的 empty 政策完成，不因有键就一律补零。
需要约束一批身份时传 members=identity_domain；完整输出由 by=domain、身份轴或日历明确请求。
Domain 统一身份、字段与日历坐标，普通分组不因有身份约束自动补齐全部分类。
普通观察默认信任声明的数据源；明确的来源缺口按 §4.9.3 影响相关结果。

## 2. 对象模型与 lazy 状态

Q = Observe({name_i: m_i}, context)，context 可选绑定身份域 P。
无身份约束不等于空 P 或默认 Entity 全集；各组件形成自己的合法贡献。
同一个 Domain 可作身份约束或完整输出域，但这两个使用位置独立。
R = (K, named Cells, retained information, evidence) 固定一次实际答案。

| 对象 | 唯一职责 |
| --- | --- |
| Metric | 完整业务计算定义，不捕获 Session、运行时名单或窗口默认 |
| Query | 有明确依赖与最终绑定的 logical 问题；execute 返回 Result |
| Result | 固定键、值、状态、证据和实际信息；恢复后同样续算 |
| Domain | 带完整类型化轴的有限键集；身份域是其单 Entity 身份轴特例，可作约束或请求域 |

Field 是语义字段，Column 是当前结果的量；两者不能混入另一阶段。
Session.members 从来源选择身份并返回 Domain；Session.query(domain) 返回仅键 Query。
统一以 query(domain).execute().domain() 固定域；Result.domain().project 提取已保存身份轴，
不另设成员结果、成员转换或隐藏成员关系。Session.values 返回类型化参照 Query。

### 2.1 不同身份不能互相替代

业务身份、贡献出现、完整坐标、计算定义、逻辑节点和已提交 Result ID 分别保存。
相同字段值不是同一 Entity，相同定义或共享节点不固定来源内容；
m+m 保留两次使用，同名角色或数值相等不能证明贡献包含。
动态字符串 schema 不由 Python 注解完全推导；具体 kind、绑定和实际数据条件分层检查。

### 2.2 状态与副作用

构造、字段定位、repr/contract 不读业务行、不创建 Run 或来源临时表。
物理类型在合法来源绑定时按 schema-only 边界解析，不扫描值猜类型；
模型加载保持 source-I/O free，不靠伪造原生 Column 提前取得 dtype。
execute 是业务执行入口，Result 不提供 execute；结果变换返回 Query。
show/to_pandas 只读已提交内容；后者返回有资源边界的隔离副本，固定导出布局与信息边界由 §4.6.1 拥有。
禁止迭代、bool/len/query 行索引或运算符隐式执行。展示默认按完整键规范排序；Top-N 的评分展示次序见 §4.6。
show(n) 不截断保存结果，不构造样本人群，四种 Cell 不压成无原因的 NaN。
项目本地 Session/Artifact 元数据访问不属于业务取数。

## 3. 语义交接与输入类型

### 3.1 保留语义层的权威

Entity 提供来源、完整身份与版本；Field 提供单位、读取要求和权限；
Metric 提供纯计算图、固有条件、时间角色与 fold；Relationship 提供方向、完整键与结构基数。
Grain/PeriodCalendar 提供认证日历，不证明业务来源完整性。
普通观察信任声明，不自动全表审计；声明、构造保证、调用前提和实际检查分别记录。

命名/临时构造器共用 Metric；不能覆盖既有聚合方法或政策来复用原权限。
数量×单价等新业务金额先在受限 Ibis Measure authoring 中明确声明，
再用精确来源聚合器。复杂来源 Ibis body 不自动拥有固定上卷状态。
原指标输入充分与具体执行资格分别判断，无合并摘要不否定完整输入直接求值。

### 3.2 Entity 身份与普通维度分组

Entity 的 datasource/source/primary_key/版本声明含义不变。
只有 Orders 时，customer_id 可以作为普通 Dimension 分组，不需要 Customers Entity。
订单身份域仍使用完整 order_id；客户 ID 字段坐标不能升级成客户身份。

| 问题 | 输入单位 |
| --- | --- |
| 选择订单 / 固定原订单 | 来源 Orders 身份域 / 已保存身份域 |
| 各 customer_id 收入与订单数 | 贡献按字段完整元组分组 |
| 固定 A/B 名单的新期间均额 | 明确固定字段域，observe 后 reduce，包含请求的无贡献格 |
| 计当期出现 ID | 来源 count_distinct，不等于固定域大小 |
| 计固定域大小 | s.query(domain).reduce({"ids": row_count()}) |
| 客户身份集合运算 | 明确 Customers 与 Relationship，取 image |

多个字段共同分类保持完整元组，不分别去重后求积。
固定域只固定值组合，不固定关联订单，也不继承提取窗口/状态条件。
需要客户身份时沿声明关系取像；关联键覆盖完整目标主键时只读来源侧关联键，
不为身份提取强制读取客户档案，不从同名字段猜 Relationship。

### 3.3 签名中使用的精确类型

TimeBoundary 为 ISO 日期/时间字符串、date、带时区 datetime 或已解析的 InstantAnchor；
日期与无偏移时间字符串按 Session 时间构造器解析出的时区解释，不接受 naive datetime。
Grain 沿用 [统一时间语义](../temporal-semantics.md#unified-grain-and-time-scopes) 的不可变闭合值；
内建粒度由 mv.grain(unit, count=...) 构造，认证日历粒度由 catalog 中的 calendar.grain(level) 取得。
LiteralValue 为 bool/int/有限float/Decimal/str/date/带时区datetime/timedelta 的封闭联合；
None 不作算术字面量，只可作为字段 Null 坐标。
FieldPredicate 是有限类型化字段谓词，不是未绑定 Ibis BooleanValue；
ColumnPredicate 绑定当前结果；DomainPredicate 由接收者确定唯一作用域。
Axis 为身份、Field、Calendar 或已对齐的 CorrespondenceAxis 绑定；Domain 是有限完整键的 logical 域。
Domain.axes 提供当前有序、已绑定的坐标轴；member() 只定位唯一相容身份轴。
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
这些支持类型随 owner 渐进发现，不逐个扩展顶层 Help。

## 4. 核心 API 签名

签名属于目标设计。Field/Entity/Metric、Catalog 与定义构造归 ms；
观察、结果与时间绑定归 mv。除明确读取外，调用只构造 logical 值。

### 4.1 Session、字段、时间与路径

```python
def get_or_create(
    name: str,
    *,
    question: str | None = None,
    report_timezone: str = "UTC",
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
        metrics: Mapping[str, Metric],
        *,
        during: TimeScope | CalendarAxis | ObservationWindow | None = None,
        by: tuple[Axis, ...] | Domain = (),
        where: FieldPredicate | DomainPredicate | None = None,
        members: Domain | None = None,
    ) -> Query: ...

    def members(
        self,
        entity: EntityRef,
        *,
        at: InstantAnchor | None = None,
        where: FieldPredicate | DomainPredicate | None = None,
    ) -> Domain: ...
    def query(self, domain: Domain) -> Query: ...
    def values(
        self,
        *,
        by: tuple[Axis, ...],
        columns: Mapping[str, str | None],
        rows: tuple[ValueRow, ...],
    ) -> Query: ...
    def result(self, result_id: str) -> Result: ...


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
显式 P 必须是恰好一个已绑定 Entity 身份轴的 Domain，要求同 Session；
身份×日期或多身份轴须先 project。空 P 与无约束不同，每个组件沿合法路径归属于 P。
显式身份引用不证明档案存在；贡献约束与请求输出域分别绑定，不从有无事实重选 P。
每个贡献根到 P 的归属路径由语义层的唯一声明决定，observe 不接受路由补全参数。
路径缺失或存在多条候选时，构造即拒绝：结构化错误列出实际贡献根、P 的 Entity 与候选关系，
修复指向在语义层声明唯一默认路径，或分别 observe。Field.via 是标量字段读取路径，不承担主体归属。

Field.via(path)、Domain.image/preimage(via=...) 与 corresponding(via=...)
统一接受非空有序关系元组：单跳写 (relationship,)，多跳写 (first, second)。
单独的 RelationshipRef、list 和空元组均拒绝，不自动包装或把空路径解释为恒等关系。
路径按声明方向连续连接；完整键、角色、版本、端点和消费者所需基数仍分别检查。

where 在每个来源组件上追加条件，保留原定义；任一组件不能唯一解释就整体拒绝。
member_count 在显式 P 上要求条件可单值解释，不重写已选身份域；
多值贡献条件不能被猜成“任意贡献满足”。常量不新增来源，固有分支过滤不被共同条件删除。
不同条件/范围的观察分别构造，再 align/select/compare，不嵌套默认上下文。

during=scope 为范围合计，scope.by(mv.grain("day")) 同时提供范围和完整日历；
during=window 使用 window.axis 和每点 W(t)。by 只列其它轴，
高级完整域须与 during 的同一日历绑定相容。冲突拒绝，不生成重复轴。
所有适用分支只接这一份上下文；期间不覆盖业务时间角色、属性锚点或固有固定范围。

省略 during 与显式 during=None 等价，表示本次观察不追加时间过滤。
业务时间角色只决定显式期间作用于哪个时间字段，本身不要求每次观察都提供期间。
仍按完整依赖图检查时间采样、时间 fold 和日历输出的期间要求，不只检查最外层 Metric：

| 指标或输出要求 | during=None |
| --- | --- |
| 不要求时间采样或时间 fold 的普通来源规约，以及仅由这些指标和常量组成的表达式 | 合法，有无业务时间角色均可；按定义及显式 where/members 取贡献，不新增时间约束 |
| member_count 及仅由它与上述普通规约组成的表达式 | 合法；仍须提供显式身份域，版本/属性等其它绑定要求不变 |
| 任一依赖要求按观察期间采样，或定义包含时间 fold | 拒绝；必须显式传入 TimeScope、CalendarAxis 或 ObservationWindow |
| by 请求日历轴，包括含日历轴的显式 Domain | 拒绝；必须显式提供与输出日历相容的 during，不从 by 反推观察期间 |

固有日期过滤和 where 中的 within(scope) 照常限制贡献，普通规约不必重复传入 during。
这些过滤不代替时间采样、时间 fold 或日历输出所需的期间；成员选择时点和 Field.at 仍只绑定各自的版本选择。
空 members、乘零或只消费输出键不能免除真正必需的期间绑定；无时间角色的指标不会因此获得隐式时间角色。
省略期间不选择最近期间、当前时刻或 Session 默认日期范围，也不证明来源包含完整历史。
构造时若缺少必需期间，结构化错误指出要求时间采样/fold 的实际依赖或输出日历、收到的 None 及显式 during 的修复入口。
不读取来源猜测边界，执行与恢复也不把 None 改写为数据的最早/最晚时间。
期间准入不证明来源覆盖完整；默认信任及已知缺口遵守 §4.9.3。

Session.time_scope 为非空半开范围；Session.instant 构造单个精确时刻，两者不保留模块级同名入口。
timezone 省略或为 None 时使用该 Session 已保存的 report_timezone，显式值只覆盖本次构造。
构造时冻结解析后的时区和绝对边界；后续消费、执行与冷恢复不重新选择 Session 或主机默认。
日期按解析时区午夜，无偏移时间字符串按当地时钟解释；DST 歧义/不存在时刻拒绝。
带偏移字符串、带时区 datetime 和已有 InstantAnchor 保持同一绝对时刻，不能按新时区重解释钟面值。
scope 的时区用于日历分桶；认证日历仍拥有自己的边界时区，不相容时按 §4.9.1 拒绝。
构造只读取本地 Session 时区元数据，不读业务来源；时间值不捕获可变默认，也不提前把纯日历域归属某个 Session。
内建周从星期一开始，civil day 不等于24小时；截断桶保留名义与实际边界。
Field.at 显式绑定属性版本，非版本化字段不能假装有历史语义。

Field 支持类型化比较、is_null/within 和布尔组合，不提供原生 expr。
复杂逐行计算进入受限 Ibis authoring。多值/多字段筛选统一使用 in_domain(coordinates(...))。
字段轴的源 Null 默认归 None；缺映射、Unknown/Undefined 不伪装为 Null。
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
Session.result 用精确已提交ID恢复，不选最新同定义结果，不加载来源补信息。

#### 4.1.1 精确时刻与版本选择

InstantAnchor 是唯一时刻值：由 Session.instant、scope.start/end 取得，可作范围边界、since 起点，
或作为 Session.members、Field.at、Domain.image(via=..., at=...) 的版本选择时点。
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

### 4.2 统一 Domain 与明确身份读取

```python
class CorrespondenceAxis:
    projections: Mapping[tuple[str, ...], Axis]


class Domain:
    axes: tuple[Axis, ...]

    def where(self, predicate: DomainPredicate) -> Domain: ...
    def project(self, *, by: tuple[Axis, ...]) -> Domain: ...
    def union(self, other: Domain) -> Domain: ...
    def intersection(self, other: Domain) -> Domain: ...
    def difference(self, other: Domain) -> Domain: ...
    def read(self, fields: Mapping[str, Field]) -> Query: ...
    def classify(self, *, by: tuple[MemberAxis | Field, ...]) -> Domain: ...
    def image(
        self,
        *,
        via: tuple[RelationshipRef, ...],
        at: InstantAnchor | None = None,
    ) -> Domain: ...
    def preimage(
        self,
        targets: Domain,
        *,
        via: tuple[RelationshipRef, ...],
    ) -> Domain: ...
    def contract(self) -> AnalysisContract: ...
```

所有域共用集合、已有轴投影、纯坐标筛选和唯一保存路径；不按来源拆分公开域类型。
read、classify、image、preimage 只接受单个已绑定 Entity 身份轴的接收者；preimage 的 targets 也须为
路径目标 Entity 的单身份轴域。形状不符立即指出实际轴及所需投影，不引入可空 entity 或权限标志。
这些对象始终 lazy；Domain 不提供 execute，使用 s.query(domain).execute() 保存完整键。

Session.members(entity, at=..., where=...) 明确从该 Entity 来源选择身份，where 在来源作用域
读取声明字段或测试名单；只返回实际命中的完整键。源中没有 O404 时，名单中的 O404 不生成源行。
identities 则保留明确请求的身份引用，包括未由来源确认的键；两种表达式的依赖和证据可区分，
返回类型相同。query(domain) 保存身份轴后仍可作为 members 输入，不重新检查当前档案存在性。

Domain.where 只接受 in_domain 及其布尔组合，测试接收者已有坐标，不读取缺少的字段。
初始来源条件放在 Session.members(..., where=...)；对固定身份读取新属性时，先 read，
再按返回的 Column 筛选并取 domain。旧属性筛选只消费保存的 Column，不能退回当前来源。

read 保留完整输入身份键，字段成为量列；同 customer_id 的两个订单仍两行。
classify(by=...) 只接收 Field 与接收者原有的 MemberAxis：明确读取身份上的单值字段并取完整分类元组的集合像，
可以同时列出原身份轴；不接 CalendarAxis、CorrespondenceAxis、其它身份轴、任意回调或多值分类。
缺少完整属性映射时失败，不静默删除引用或补 Null。
仅投影已有轴使用 project；classify 的新属性读取不会被解释成对旧坐标的投影。
image 只表示沿关系取目标身份，不与按字段分类共用方法名。

image(via=...) 沿声明关系取得目标身份，preimage 只在接收者有限域内选择关联到 targets 的身份。
完整目标键可由来源侧关系键取得时不读取目标档案；来源 Null 关联键无像，目标档案缺记录不删除引用。
读取必需属性/版本或补足关联键才引入相应来源依赖；所需绑定缺失与合法无关联分别处理。
路径按声明方向连续连接，空元组/反向/断端拒绝。关系映射不传递原指标贡献过滤，也不恢复原贡献出现。

固定身份域只固定键。read、classify、image 的属性或关系读取明确引入新依赖，版本锚点不能
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
| read(fields) | 保留输入身份，一身份一行，字段作为量列；显式读取属性 |
| classify(by=fields) | 读取单值分类并对完整元组去重；普通字段值不会升级为另一个 Entity 的身份 |
| image(via=(relationship,)) | 取得声明目标 Entity 的身份；可用来源关联键时不强制读目标档案 |
| preimage(targets, via=(relationship,)) | 在接收者身份域内筛出有匹配的身份；不传播 targets 的原贡献过滤 |
| project(by=axes) | 只提取已有坐标并去重；不读取新字段或关系 |

集合与版本示例见 [关系像与有界逆像](lazy-analysis-dsl-examples.md#4-关系像与有界逆像)。

### 4.3 Metric 表达式与明确贡献角色

定义签名由语义表达式提案拥有；来源用精确构造器，
指标用小型 Metric 算术/over；结果 Column 不回到来源 Metric。
Metric 没有 expr/during/route，也没有 on 树位置参数。
where 仅形成单一来源规约或完整配对作用域的新固有过滤定义。
公开角色只服务业务声明与显式 ratio 等构造器；内部依赖出现仍完整保存。

```python
def member_count() -> Metric: ...
```

member_count 是要求显式身份域 P 的上下文定义，不捕获 P；
逐格身份分类须可解释，计声明域的完整身份，不证明每个引用有当前档案。
不能将贡献行数、身份×日期坐标数或普通 ID 字段数冒充它。
语义内部样本必须可独立加载；固定名单统计使用 observe/query 后 reduce。

#### 4.3.1 结果组合的有限边界

来源复杂条件使用受限 Ibis authoring；Metric 组合只支持已准入小型节点。
结果只保留同域 Column 算术、比较、状态谓词和 select/where。
这些操作为主体选择、参照与比较运输实际合同，不扩成另一套通用表格表达式语言。
结果条件赋值、封顶/截断、标签分类和一般配对统计交 pandas，见 §4.6.1。
需要复用选中的身份时，先对原 Result.where，再取 domain().project(by=身份轴)。
该轴必须已实际保留；只有分组字段的汇总不能恢复原名单，应在原 observe 的 by 中请求身份轴。

#### 4.3.2 字段表达式的规约授权

聚合检查声明单位/可加坐标/状态时间，dtype 不授予业务权限。
数量×单价、逐单金额减成本等复杂业务量先用受限 Ibis Measure 定义，
再 aggregate；不扫描 Catalog 猜等价 authoring，不扩大单价的 additivity。
来源 sum/count/mean 等精确方法逐项校验意义；未知单位不能自动换算。
声明、路径、规则版本与实际续算状态保存，完成值不能替代充分信息。

#### 4.3.3 时间 fold 与派生依赖

时间角色、空间/时间次序及精确fold的定义由[语义提案§3.2](../semantic/expression-model-redesign.md#32-时间角色和时间-fold)拥有。
本次观察只提供期间/目标空间，固定续算检查实际可对齐的折叠前信息；
不能用完成P95或失去时点的分布补它，数值见证见 [场景验证 §7.8](lazy-analysis-dsl-scenario-validation.md#78-采样带宽-p95-与依赖指标)。

### 4.4 最小规约与有效样本

来源统计由 ms.aggregate/count/weighted_mean 与受限 authoring 拥有；
纯语义内域由 Metric.over 拥有；已固定名单/格子的必要语义续算由 Query/Result.reduce 拥有。
不以一个 ms.metric(expression=..., by=...) 混合这些阶段。
来源 null/empty 政策由声明与构造器保存，命名/临时一致；
来源默认与新/已有指标政策由[语义提案§3.4](../semantic/expression-model-redesign.md#34-样本政策的归属)拥有。
Unknown/Undefined 不 lower 成可忽略 SQL NULL。

| 结果规约 | 固定政策 | 空有效样本与输出 |
| --- | --- | --- |
| Column.sum | Null 输入报错 | 0；保留单位与四态检查 |
| Column.mean | Null 输入报错 | Undefined；等权均值保留 sum/count 状态 |
| row_count | 无 | 0；计全部当前坐标，包含零活动/非 Defined 格 |

结果规约不接受 nulls/empty 选项；需要忽略 Null 或其它空集政策时，先显式 where 或交 pandas。
统计方法返回私有 _Statistic，只交给 reduce，不公开 Reducer。
多项规约消费同一输入，各自独立选择有效样本；不能先求所有量 Defined 的交集。
Unknown/Undefined 不被规约吞掉。需要统计量之间的比值，先 reduce 再 select；
不能对私有统计请求直接算术或 execute。普通终端均值/求和也可直接交 pandas。
结果侧其它统计不在本版闭合集合，不能因来源方法或底层 pandas 支持就自动进入公开面。
来源精确分位数及内部样本政策仍由原方法拥有，不能将结果统计的退出解释为来源指标能力删除。

### 4.5 坐标和目标域

```python
def product(*factors: Domain | CalendarAxis) -> Domain: ...


def identities(
    entity: EntityRef,
    *,
    keys: tuple[tuple[LiteralValue, ...], ...],
) -> Domain: ...


def coordinates(
    *,
    by: tuple[Field, ...],
    keys: tuple[tuple[LiteralValue | None, ...], ...],
) -> Domain: ...


def in_domain(domain: Domain) -> DomainPredicate: ...
```

#### 4.5.1 轴、键与域运算

Field 在 by 中是字段绑定轴，须有合法单值分类；按原类型形成完整元组。
源 Null 默认归独立的 None 键，参与分组、投影、集合和对齐，不删除贡献；
同字段绑定下按坐标相等性匹配，coord(None) 是 Defined 坐标，不是量 Null。
复合轴逐字段分类；缺映射、Unknown/Undefined 不能伪装成 None。
推导域仅在实际出现 Null 分类时产生此键；显式请求 None 时即使无贡献也按 empty 政策保留。

| 操作 | 定义与约束 |
| --- | --- |
| identities(entity, keys) | 唯一手写身份名单入口；Entity 只给一次，每个键是按声明次序的完整主键元组，允许未经来源确认的身份引用；空 keys 仍是该 Entity 的类型化空域 |
| coordinates(by, keys) | 唯一手写字段坐标域入口，声明字段轴的完整有限元组，允许稀疏域 |
| product(*factors) | 至少两个不重复轴因子，按参数次序拼轴并作笛卡尔积；日历因子提供完整桶，任一空因子使结果为空 |
| query/result.domain() | 当前完整输出坐标；Query 保留筛选及必要检查，Result 只用固定键 |
| Domain.project(by) | 从已有轴或已保存端点投影中选取/重排并去重完整元组，不读新属性；投影须全定义，outer 缺侧先按 presence 筛选 |
| union/intersection/difference | 按完整元组作并、交、左差；要求相同有序轴、身份类型、角色、版本/时间及相等性，不按列名合并或自动重排 |

空域保留轴类型；投影到零轴时，非空输入得 {()}，空输入仍空。
手写坐标按轴检查：

| 轴 | 允许的手写坐标 |
| --- | --- |
| 身份轴 | identities 的完整主键元组；Session.values 的 by 可用从 Domain.axes 取得的已绑定身份轴，坐标同为完整主键元组 |
| Field | 与字段 kind 相容的 LiteralValue，或表示源 Null 分类的 None；bool 不冒充整数 |
| CalendarAxis / CorrespondenceAxis | 不接受手写坐标，包括日期标签、桶序号或端点元组；不能用普通标量冒充内部键 |

日历域由 TimeScope.by(Grain) 取得，并通过 product 组合；对应域来自明确的 align/compare。
已有日历/对应坐标通过 domain、project、where 和集合操作复用，保留实际轴、桶和端点绑定。
不新增日历键/对应键包装构造器，也不开放 DataFrame 回灌。
不将相关字段分别去重再求积。重复轴/键、元组长度不符或坐标 kind 不相容均拒绝。
跨 Entity 的同值主键不相等，字段坐标不升级为身份。None 只可作字段坐标，不能作主键分量或量值。

by=Domain 同时声明轴和完整 K，保留无贡献坐标；K 必须覆盖所有被消费贡献，不能靠漏键过滤。
in_domain(Domain) 则只筛已有输入，不生成键；请求完整输出也不证明覆盖或保证补零。
by=轴元组的 K 依 §5.1 推导。纯语义 Metric.over 不消费运行时域，固定域统计使用 query/observe 后 reduce。
域只传坐标和轴绑定，不传供给方指标、贡献过滤、期间或隐藏名单。

所有域保持 lazy，保存统一使用 s.query(domain).execute()，恢复后取 domain()。
仅键 Result 没有占位量，可 select 坐标、where、row_count、取域和 align；没有原指标可供 rollup/drill。
已绑定 Entity 身份轴随键保存，投影后可作 members；普通字段不能反推身份。
含 Domain/Query/Result 的依赖保留 Session；集合运算/product 混用不同 Session 时拒绝。
纯字面量/日历域在消费时绑定 Session，组合本身不创建或选择默认 Session；Session.query 检查已有归属。

固定域运算只用保存键；含来源的域保留读取和原检查，即使集合等式可推空也不能省略必要失败。
相同来源定义不代表相同固定键集；共享读取规则见
[执行与恢复 §1.1.1](lazy-analysis-dsl-execution.md#111-共享逻辑节点与固定输入)。

#### 4.5.2 身份名单与 in_domain 绑定

identities(E, keys=(("O1",), ("O2",))) 中每个内部元组是一条完整复合主键，身份轴在构造时即绑定 E；
空名单同样是 E 的类型化空域，可直接保存或作为接收者筛选，不存在待绑定身份轴。已有绑定不能改绑。

in_domain 只保存域，接收者在 where 中确定唯一测试，不预读字段或提取 Python 列表。
DomainPredicate 支持 &、|、~；与 FieldPredicate 或 ColumnPredicate 组合后分别成为该类谓词，
所有测试绑定同一接收者。Field/Column 谓词不能混用，已绑定 Column 不能跨输入域混用。

| 接收者 | 允许的域与匹配方式 |
| --- | --- |
| Session.members(..., where=...) | 一个或多个字段轴按完整字段元组读取；或一个相同 Entity 身份轴按完整主键匹配 |
| 基础 Metric.where | 同上两种形状，作用于单一贡献根；保留/删除贡献出现，不按身份去重贡献 |
| Domain.where | 只匹配已有坐标，只接受域谓词及其布尔组合；新属性须明确 read/classify |
| Query/Result.where | 从当前完整 K 向名单轴建立唯一、全定义投影 p；保留 {k∈K : p(k)∈S}，不读字段或重选成员 |

来源选择/贡献筛选不接受零轴、身份与其它轴混合、多身份轴、日历或对应轴；先 project 到合法形状。
字段角色/版本须合法；完整复合元组不能拆成各列集合。来源成员只取实际命中交集，
未命中名单不生行、不报覆盖错误，多次匹配不复制身份；固定身份筛选不重查存在性。
源 Null 分类按 None 匹配，其它状态按 §5.4 处理；名单时点不隐式传给来源选择。

结果投影仅用已有轴的选取/重排、已保存父子映射或合法 coarsen；
逐轴检查身份、角色、时间/版本和对应绑定，不按名字、行位置或部分维度猜测。
域 S 无须覆盖 p(K)，不匹配为 False，多余名单键不生行；这与 broadcast 的完整覆盖要求不同。
结果保留原粒度、量、presence 和仍充分的保存信息。日历/对应轴须使用明确映射；
两侧不同日期不能当作同一时间轴，outer 缺侧投影须先限制到有键范围。
缺轴或歧义映射立即拒绝；member() 只能定位唯一相容身份轴，多角色须使用明确绑定。

合法空域恒 False；零轴 {()} 恒 True，零轴空集恒 False；域和原 Query 必要检查仍保留。
纯坐标测试不消费量列状态，不因收入 Undefined 丢掉坐标；与值谓词组合时依 §5.4。
来源域随消费它的 execute 求值，固定域用保存键；二者都保留原检查和 Session。
固定接收者加来源筛选域仅执行该域依赖，不能重新观察接收者指标或成员。
固定客户 ID 字段域可选新订单；固定原订单须保存订单身份域。非持久化域重复消费可得到不同名单。

#### 4.5.3 日历粗化

coarsen 保留 scope、时区、截断区间与父子映射；相同 grain 保持原绑定。
改变 grain 仅构造候选粗化，实际区间须完整映到唯一目标桶，不能细化或扩大读取窗口。
日→月→年须与同 scope 下合法直接粗化相容；跨月周不能自动归某月，年桶仍只含原 scope 区间。
坐标映射不授予指标可加性或充分状态，能否原指标合并仍由 rollup 检查。

### 4.6 查询、结果与值表达式

```python
from typing import Protocol


def row_count() -> _Statistic: ...


class _ResultOps(Protocol):
    def __getitem__(self, name: str) -> Column: ...
    def coord(self, axis: Axis) -> Column: ...
    def domain(self) -> Domain: ...
    def select(self, values: Mapping[str, Column | LiteralValue]) -> Query: ...
    def share(self, name: str, *, partition_by: tuple[Axis, ...] = ()) -> Query: ...
    def where(self, predicate: ColumnPredicate | DomainPredicate) -> Query: ...
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
        other: Query | Result,
        *,
        how: Literal["exact", "outer"] = "exact",
        match: Correspondence | None = None,
    ) -> Query: ...
    def compare(
        self,
        baseline: Query | Result,
        *,
        how: Literal["exact", "outer"] = "exact",
        match: Correspondence | None = None,
        time_axis: Literal["paired", "current"] = "paired",
    ) -> Query: ...
    def contract(self) -> AnalysisContract: ...


class Query(_ResultOps):
    def drill(self, *, add: tuple[Axis, ...]) -> Query: ...
    def execute(self) -> Result: ...


class Result(_ResultOps):
    id: str
    session_id: str

    def show(self, *, n: int = 20) -> None: ...
    def to_pandas(self) -> pandas.DataFrame: ...


class Column:
    def is_defined(self) -> ColumnPredicate: ...
    def is_null(self) -> ColumnPredicate: ...
    def is_undefined(self) -> ColumnPredicate: ...
    def is_unknown(self) -> ColumnPredicate: ...
    # Arithmetic operators, including /, and statistical methods follow sections 4.1.2 and 4.4.
```

`_ResultOps` 只用于复用本文的方法签名，不是可构造的分析对象或顶层公开符号，
也不开放任意实现该协议的外部对象作为分析输入。实际入口仍使用具体的 Query / Result。
共同方法只列一次：变换返回 Logical 值；Query 独有 drill 与 execute，Result 独有已提交结果的读取接口。
execute 返回和按精确 ID 恢复的 Result 使用同一接口及保留信息约束（[恢复合同](lazy-analysis-dsl-execution.md#13-跨轮和跨进程恢复)）。

Column 是统一的公开值表达式接口，内部封闭的数值、布尔、类别和时间 kind 限制相应运算；
不要求用户把 `result[name]` 再转换成另一个公开子类型。数值算术及 Query/Result.share 选择非数值列时，
绑定检查立即报告期望 kind 和实际 kind；实际 Cell 状态仍到 execute 才检查。没有任意 callback 的 map。
数值 Column 支持与 Metric 对应的逐点算术，返回绑定当前坐标的 Column；
`/` 的 Defined 零分母固定为 Undefined，不提供零分母选项或另一个除法方法。
LiteralValue 的 kind/单位绑定遵循 §4.1.2，已保存列不能按常量规则重新赋单位。
谓词返回封闭的布尔表达式；普通配对统计按 §4.6.1 导出后显式检查状态和共同样本。

Column.sum/mean 返回绑定列和当前样本域的私有 _Statistic；row_count 是尚未绑定输入的无列变体，
可复用于不同接收者，在 reduce 时绑定当前坐标域，不创建来源贡献。
reduce 的非空命名映射是唯一完成入口，分组在此统一绑定；同 schema、行数或 Session 不证明列属于接收者。
跨结果输入先 align/broadcast，不隐式对齐、拼行计数或重观察。各项消费同一实际输入，
有效样本按 §4.4 独立选择。统计请求不是 Column，不能直接算术或 execute；统计量之间的公式先 reduce 再 select。

reduce 与 rollup 的 by 只能引用当前成员/字段/日历绑定或其合法 coarsen，不能触发新属性读取。
省略全部轴时输出单例标量域；使用非空轴元组时输出所保留坐标的完整分组像。
需要保留没有子格子的父坐标时，在原 observe 上以 by=Domain 请求完整域，rollup 不接受目标 Domain。
空样本遵循该方法的空集政策，不因没有输入行而自动删除标量格子。
新统计量拥有自己的方法、单位和实际保留的状态；后续可否合并由这些信息决定，
不会继承输入列原业务指标的贡献单位或原观察的下钻能力。
`drill(add=...)` 只列新增细轴；目标细域固定为“当前轴 + 新增轴”，细坐标到父坐标的映射
就是丢弃新增轴的投影，因此不需要重复列出父轴。新增轴不能与当前轴重复，也不能是日历细化。
它不接受 share/contribution 开关，粒度变换与份额分开组合。
drill 只在可追踪到来源观察的 Query 上提供；Result 不提供 drill，需要细化时从原 Query 重新构造，准入见 §5.3.2。
compare 产生的 Query 不支持直接 drill；先在明确观察端点上分别细化，再 compare。

`select` 只改变量列，保留完整键和轴绑定；不能通过省略一列偷偷合并坐标。
`coord` 定位实际保留的轴绑定；相同语义字段的多个角色或版本须明确区分。
输入 Column 必须属于同一已建立的坐标域，或已通过 align/broadcast 取得对应。
同样的 schema 或恰好相同的行数不能建立这种关系。

轴统一用原 Field/Calendar 句柄或 domain().axes 中的绑定引用，不另设按 Ref 查轴的方法；
同一字段存在多个角色/版本时拒绝并披露实际绑定。执行及恢复保存真实绑定，不依赖原 Python 地址。
结果标签不成为新的业务轴。外部分箱/分类可使用 pandas，但这些标签不会自动获得
业务身份、原指标上卷、固定下钻或跨期语义对应权限。

`top(n, by=score, partition_by=())` 在接收者的完整当前域上按 score 选择至多 n 个坐标；descending=False 选择最小值。
非空 partition_by 须为当前已有且不重复的轴绑定；不在 top 内读取新字段或隐式粗化。
按这些轴的完整元组划分输入，每组独立选择至多 n 个坐标；合法 None 分类属于一个分区，不凭空生成空分区。
n 必须为非负整数且不能为 bool；by 必须绑定当前输入域，具有声明的全序，所有被比较的 Cell 均须 Defined，
数值还须有限。需要排除其他状态时先显式 where；不默认将 Null/Undefined/Unknown 排到末尾。
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
展示次序不改变 Domain、业务身份或对齐语义。top 是 lazy 结果选择，不是来源采样或物理 LIMIT 捷径。
先 where 再 top 与先 top 再 where 是不同问题；其后的 drill 按父级选中键细化，不在每个子组重新排名。
来源 Query 的新 execute 可重新选择重点；从固定 Result 取 top 域则固定这次选择，读取保证见 [执行与恢复 §1.1.1](lazy-analysis-dsl-execution.md#111-共享逻辑节点与固定输入)。

domain() 只暴露实际完整坐标；身份复用与信息丢失见 §5.3.1。
已绑定轴可从原端点 domain().axes，或对应轴的 projections 取得；
后者按 compare 的 current/baseline、align 的 left/right 路径保存端点绑定，冷恢复后仍可用。
member() 有歧义时须提供明确轴，不能将同 Entity 的两个版本投影混成一个身份轴。
引用的来源及读取证据不等于当前档案存在证明。

#### 4.6.1 to_pandas 的固定交接合同

Result.to_pandas() 只读精确已提交结果，返回完整隔离副本；不执行 Query、不刷新来源或 Catalog。
导出保留完整键、量、四态及原因、两侧 presence、单位/政策和已保存证据，不展开未输出的内部组件。
值和时间精度不能降级，空表仍保留类型；超预算或不能无损转换时明确失败，不抽样或截断。
固定宽表使用 RangeIndex 和 kind/name 两层列名；列布局、dtype、元数据与隔离要求由
[导出布局](lazy-analysis-dsl-execution.md#3-pandas-导出合同) 拥有。

外部程序自行选择状态/共同有效样本、完整业务键及缺侧政策；RangeIndex 不是身份。
pandas 变换后的值、标签和 attrs 不授予原指标续算或身份权限，也不接受回灌成 Result。
需要继续观察主体时，保留原 Result，从 where/domain/project 提取实际保存的身份轴。

### 4.7 对齐、比较与参照

```python
Correspondence = CalendarCorrespondence | DeclaredCorrespondence


def ordinal() -> CalendarCorrespondence: ...


def corresponding(
    *,
    via: tuple[RelationshipRef, ...],
) -> DeclaredCorrespondence: ...


def broadcast(value: Column, *, to: Query | Result) -> Column: ...
```

align/compare 的 how 选择完整配对（exact）或键像之并（outer）；省略 match 只允许相同绑定下的键对应。
所有 Correspondence 都是不带轴的规格：左右轴在 align/compare 绑定时分别从两个操作数取得，
不重复传入两侧轴。日历对应要求每侧恰好一个日历轴，身份对应要求每侧恰好一个成员轴；
缺少或有歧义时拒绝并披露两侧实际轴。其余轴仍按声明的完整坐标规则对应。
`ordinal()` 对完整有序日历桶给出位置对应；exact 仍要求两侧完整配对，长度不同即拒绝。
这只建立位置对应，不自行计算窗口或证明同比意义；时间扩展见 §4.9。
`corresponding(via=...)` 只解释身份轴之间的对应，
via 按左身份 → 右身份的声明方向连接，例如 `a.align(b, match=mv.corresponding(via=(CustomerMap,)))`。
普通字段轴不能因值像主键就进入该入口。路径须具有所需的一对一结构，角色和版本解释必须明确；
版本绑定来自各侧成员轴，不能推断为当前时刻，缺少所需锚点时应先修订对应侧的身份域定义。
结构一对一不证明本次键域完整配对，实际覆盖仍由 exact/outer 检查。
路径能直接解释完整键时不增加来源读取；需要查值时，来源 Query 可显式形成所需对应依赖，
固定 Result 只能使用已保存映射，不能因声明了 via 就回读旧来源补关系。
不同期间配对默认产生对应域并保留两侧日期投影，不伪装成真实观察日历。
显式 compare(time_axis="current") 的受约束当前轴投影见 §4.9.5。

`align` 输出 `left.<name>`、`right.<name>` 以及 `left_present`、`right_present`。
exact 检查完整键唯一和键像相等；outer 使用已声明共同域上的键像之并。
相同轴绑定的恒等对应保留这些共享坐标轴；非恒等对应保存自己的左右投影，不将其重标成任一侧原轴。
outer 显式把缺侧投影扩展为带 `missing_left` / `missing_right` 原因的 Null，
同时保留 presence；这不是把原始“缺坐标”重新定义为 Cell，也不是空贡献证明。

`compare` 要求两侧具有相同且非空的量名集合，各同名数值量逐一可比较，输出
`<name>.current`、`<name>.baseline`、`<name>.absolute`、`<name>.relative`
及 current_present、baseline_present。outer缺侧原因分别为missing_current/missing_baseline，
错误位置沿相同业务侧名；align的left/right及missing_left/missing_right不变。绝对变化为 `current - baseline`，相对变化为
`(current - baseline) / abs(baseline)`，保存两个端点及对应关系。
量的定义、单位和各侧上下文都参与检查；不兼容量须先显式构造所需的新量。
两侧各自有无成员绑定记录在比较定义中；“固定同一批人”要求共同固定成员输入或实际身份相等证据，
标量坐标相同不能证明人群相同。
比较保存两端值、量定义、输入权威、对齐和存在时的精确已提交端点引用；
不保存可回放观察或专用细状态，不提供直接 drill，relative 也不可直接上卷。

`broadcast` 沿目标键到参照键的完整投影取值。
参照列已经携带完整坐标域，不再重复传 by；
由已绑定轴的完整投影、已保留父子映射或合法 coarsen 确定唯一 `h: K_target → K_reference`。
零轴参照必须恰有一个键；分组参照须匹配它的全部轴。参照键须唯一且覆盖所有目标键。
不自动少匹配某个维度，不因同名字段猜对应，也不因去重后只剩一行就当作零轴标量。
缺少或存在歧义映射时拒绝；先显式调整参照的粒度或建立对齐，再广播，不能用 by 掩盖未匹配的参照轴。
它复制取值出现而不复制原事实，也不自动赋予求和或原指标上卷能力。
broadcast 接受任意相容值 kind，纯粹建立取值对应；share 另有数值、单位和贡献包含要求。

execute 不接受保留请求；Result 只保存默认信息，见 [保存信息](lazy-analysis-dsl-execution.md#12-默认保存信息)。
默认信息足够时 Result 可 rollup 等合法续算；需要细域、更早历史或其它窗口时，从来源 Query 重新构造并 execute。
名单使用 query(domain).execute() 保存；需要身份细项时，在 observe 的 by 中直接请求该身份轴。

### 4.8 份额：当前总体与分组总体

Query/Result 的 `share(name, ...)` 是唯一公开份额入口。`name` 精确选择接收者已有的一个数值量；
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
[执行合同](lazy-analysis-dsl-execution.md#111-共享逻辑节点与固定输入)。
新增份额列不继承原指标 drill/rollup；原列保留自身信息，
需要继续原量时从原接收者或仅投影原列的 Query 出发。含份额列的整表不能整体按原指标续算。
零分母、状态、符号及支持依据由 §5.3.3 拥有。

### 4.9 滚动窗口、累计与期间比较

所有时间动作仍 lazy。ObservationWindow 与 CalendarCorrespondence 是绑定值，
没有独立执行生命周期；期间只在 observe 绑定，Metric 没有时间默认。

```python
def trailing_periods(axis: CalendarAxis, *, count: int) -> ObservationWindow: ...
def since(axis: CalendarAxis, *, start: InstantAnchor) -> ObservationWindow: ...
def to_date(axis: CalendarAxis, *, grain: Grain) -> ObservationWindow: ...
def period_offset(by: int) -> CalendarCorrespondence: ...
def previous_year() -> CalendarCorrespondence: ...
```

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
每个非时间分组有明确适用域中的所有预期桶；有没有订单不能决定开业日期。
x(g,t)=Evaluate(m, Bind(m,g,W(t),context;B)) 是完整窗口基准语义，
不要求先日预聚合或实现 merge。贡献范围由 W(t) 的并决定，不是显示范围。
显式 P 的筛选和固有日期条件保留；十月订单主体不能自动扩大成七日完整来源。
重叠窗口保留(output_key,component,contribution_occurrence)，不全局去重。

#### 4.9.2 三类窗口的边界

| 窗口 | 对输出桶t的贡献范围 |
| --- | --- |
| trailing_periods(n) | 含当前桶的 n 期：从首前驱期间起点到t终点 |
| since(a) | [a,actual_end(t))，a须早于所有评价端点 |
| to_date(p) | [containing_period(t,p).start,actual_end(t)) |

期间回看要求完整的day/week/month/quarter/year或语义桶，部分桶不能冒充完整期。
所需前驱来自完整日历；认证不足报日历覆盖错误，不制造期间。
count为非bool正整数，所有窗口半开。
to_date要求唯一包含层级，跨月周不能归某个月；可明确使用实际端点的部分期间累计。
回看按日历期间计，七自然日不等于168小时；绝对时长回看使用小时粒度的 trailing_periods。
不含当期的回看、隐式全历史起点、期初零、min_periods、插值、前值填充或任意累计seed均不提供。
不同窗口分支分别observe，再按真实完整输出轴align/select。

#### 4.9.3 覆盖、缺桶与 Cell

普通观察默认信任声明的数据源，按指标和本次绑定范围内实际读取的数据求值。
缺少完整性证明本身不产生 Unknown，也不要求调用方逐次声明完整性或选择信任模式。
默认信任是计算约定，不是已通过完整性检查；不证明字段非 Null、历史版本可读或已执行审计。
若上游静默漏数且未提供任何缺口信息，本次结果仍按已读数据计算，不承诺检测漏数。

只有明确的不完整信息影响本次所需贡献时，相关格子才为 Unknown(incomplete_window)。
例如来源明确报告某日分区未就绪、某段记录丢失，或所需历史已被清除；
事实须带实际来源/贡献角色和适用范围，不能仅因某天无记录、没有完整性声明或最大事件时间较早而推断缺口。
这里只规定已有缺口事实的消费与传播，不增加 Query 覆盖声明或来源审计入口。
窗口仍须读取其全部依赖范围；显示起点不截断回看或累计输入。

| 事实 | 行为 |
| --- | --- |
| 成功读取、无已知缺口且无贡献 | 原empty政策；计数零不代表sum必然零 |
| 已读源Null | 原null政策，保留空/全Null支持区别 |
| 明确缺口影响所需贡献 | 保留键，Unknown(incomplete_window)，披露角色/缺口 |
| 固定输入缺日期、字段或充分信息 | 信息不足错误，不伪装成业务无贡献 |
| 日历错误、重复键、非法值、来源失败 | 结构化错误，不吞成Unknown |

缺口按实际作用域影响相交的贡献窗口；滚动窗口移出缺口后可正常求值，累计窗口包含缺口时持续 Unknown。
累计重置后若不再包含缺口，可正常求值；新观察确认缺口已修复时可重新计算，已保存 Result 不被改写。
已知缺口及其作用域随结果保留，contract/导出将其与默认信任和已检查事实区分。
日历存在、最大事件时间或执行成功不生成已检查完整性证据；
重复 execute 不升级信任或提供共同快照，读取保证见 [执行与恢复 §1.1.1](lazy-analysis-dsl-execution.md#111-共享逻辑节点与固定输入)。

#### 4.9.4 原指标窗口与外部统计

窗口只在 observe(...,during=window) 绑定，直接对完整输入求原指标。
Query/Result 不提供重设窗口；换窗口时构造新的 observe，不能借窗口扩大 where/top 已选范围。
Metric没有隐藏时间默认，因此不存在更近分支覆盖；固有固定范围冲突仍拒绝。

当前格子滚动统计交 pandas，状态与日历检查见 [固定窗口示例](lazy-analysis-dsl-examples.md#214-固定结果原窗口续算与-pandas-日格子统计)；
它不替代原窗口指标的组件重算。
distinct需精确身份集合，quantile需精确bag/分布或完整输入；
mean/weighted mean保留sum/count或乘积和/权重和，不能合并完成均值。
状态fold仍检查折叠前时间对齐；重叠窗口和累计端点不因显示成多行就可直接相加。

#### 4.9.5 期间映射、同比环比与当前时间轴

对应是当前完整期间键到基期完整期间键的部分单射，保留两侧原边界。
period_offset按真实日历全局次序移动；by=-1在日轴上是昨日，不是上月同日。
previous_year支持内建day/month/quarter/year同名期间，闰日无对应不夹日；
week/sub-day 与 53 周零售日历不提供同比对应，不能用-365替代同比。
完整名义桶、时区/日历身份与部分单射均检查；缺侧不等于日历认证缺覆盖。

两侧先各自求窗口，再compare。2024-03-01七日逐日映去年不等于2023-03-01自己七日，
窗口与期间对应不能无证明交换。累计分别从各自起点计算，不对增长率求和。
compare默认paired保留双方日期投影；显式time_axis="current"要求每行都有当前日期，
outer只有基期的行不能丢弃或伪造当前坐标。
增长序列可交 pandas 做普通滚动统计，但不能作为原收入窗口或时间 rollup。
相对变化(current−baseline)/abs(baseline)，零基期Undefined，不伪造有限增长率。

## 5. 关键求值约束

### 5.1 身份约束、贡献与输出域

省略 members 时按各组件定义直接形成贡献；显式身份域 P 再沿合法路径限制贡献归属。
多个根独立规约后组合，不原始 join；同根不同使用保留各自出现。
每个组件有值读取 v、贡献 C 及完整分类 a:C→K；窗口出现还携带输出目标。

| 有效输出轴 | K |
| --- | --- |
| 零轴 | 标量单例，包括无贡献或空 P |
| member()，可附加 P 上单值字段 | 显式 P 的完整身份及其分类元组，含无贡献身份；缺 P 时不能仅靠标记猜 Entity |
| 普通字段，未请求身份轴 | 各组件有效贡献的完整分类像并集；有无 members 都遵守此规则 |
| 仅日历 | 全部请求桶，包括空桶 |
| 上述非日历轴加日历 | 已确定的完整非日历元组域 × 请求日历桶 |
| 显式 Domain | 该 Domain 的完整坐标，包括无贡献坐标 |

`members=P` 只限制贡献，不自动补齐 P 的全部地区或类别；需要这些完整分类时，
显式 `by=P.classify(by=fields)`。需要完整身份域时写 `members=P, by=(member(),)`，
不另以 `by=P` 重复请求同一身份域；一般乘积域只用作 by，身份约束先投影到单一 Entity 轴。
by 中的 Domain 只指定输出，不隐式设置 members；消费贡献仍须合法分类且被 K 覆盖。

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
| Domain.where | 既有坐标上的域测试，不新增属性读取 |
| 基础Metric.where | 新定义的固有来源条件，作用于一个完整贡献/配对作用域 |
| Query/Result.where/top | 当前结果坐标选择，保留原必要检查 |
| Metric.over | 纯语义内部样本单位，先子指标后规约 |
| 固定域observe→reduce | 本次固定名单上的格子统计 |

促销订单的客户像不让下一次收入自动只计促销订单。
内部语义样本轴必须能独立加载，内外域有明确映射；不可默认为正值/非Null主体。
运行时域计数用query(domain).reduce(row_count)，不经Metric伪装来源或计算时间角色。
结果统计保留实际输入检查，不能通过只取键或成功计数掩盖另一必需操作失败。

### 5.3 继续计算与粒度变换

| 动作 | 权威 |
| --- | --- |
| 新observe / 固定身份域read/classify/image | 新明确来源依赖，固定身份不扩大 |
| rollup | 原贡献或实际充分信息；原公式重新完成 |
| 观察Query.drill | 原观察定义和新增细绑定，保留父选择/检查 |
| compare | 两个端点和显式对应，不细化任何来源 |
| Query/Result.share | 当前输入上的自动/分组总体；可加贡献包含与完整映射 |
| reduce | 当前格子上的 sum/mean/row_count，创建有明确样本的新量 |

rollup只消费选中坐标的原信息，不恢复未选贡献。均值不能合并完成均值，
比率须重求组件再组合；tag重叠分组不是不交分区。
按已有轴或合法coarsen建立唯一完整K→J，J为输入坐标的分组像；
需要保留空父坐标时在原 observe 请求完整 by=Domain。被粗化丢弃的身份轴不另存隐藏名单。
来源Query上rollup也保留细网格完成检查，不退化为绕过失败的粗observe。
已丢父属性不能隐式回源补轴；应构造新观察。
比较量/任意派生输出不继承原指标rollup；需要粗比较时分别上卷端点后compare。

#### 5.3.1 复用身份坐标与信息丢失

设实际保留的身份坐标为 π_E:K→U_E。选中 S⊆K 后，domain().project(by=身份轴)
取得 π_E(S)，按完整身份去重；身份×日期域的格子数与身份投影的大小可以不同。
普通 customer_id 字段仍是字段轴，只有明确的 Entity 身份轴才可直接作为 members 输入。

where/top/select 保留所选坐标及现有投影；align/compare 只保留显式两侧坐标对应。
outer 缺侧必须先按该侧 presence 限制到投影定义域，不能生成虚构身份。
rollup/reduce 若丢弃身份轴，结果就失去该名单；不会为每组保存或合并隐藏的 K×U_E 关系。
保留全局输入身份域及每组总额/人数，也不足以恢复每组当时的身份分配。

需要追问旧分组里的身份时，应显式保存身份及分类坐标，或在原观察的 by 中直接请求身份轴。
前者固定那次域读取的分类，后者保存原指标在身份细域上的实际值。
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
固定成员输入则保持身份，旧Result从来不是新来源Query的隐式输入。

| 接收者 | 准入 |
| --- | --- |
| 可追踪的Observe Query及合法选择/投影/上卷 | 明确来源细化 |
| Result、以固定结果为主输入的 Query | 不提供 drill；从原来源 Query 重新构造 |
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
同固定Result的选择/合法上卷、合格共同输入路线与真实过滤祖先链可以证明；
同Metric、同Query、同Run、相同fingerprint/身份或恰好相等值都不能证明共同输入。
来源 Query 的 share 须在算子内部使用已准入的共同输入路线，否则拒绝并提示先 execute 固定结果再 share。
共同支持证明不能用两次读取对账替代（[执行与恢复 §1.1.1](lazy-analysis-dsl-execution.md#111-共享逻辑节点与固定输入)）；普通比值仍可跨独立读取。

基础可加m.where(p)只在同实际输入、原值/出现/单位/政策和所需字段保持时证明子集；
不证明任意独立谓词之间的蕴含。distinct需精确集合包含，不以最终计数代替。
mean/AOV/转化率完成值不是可加贡献。部分城市对完整地区总额的份额先 share 再 where 选择城市，
合计1还需要完整不交分区；重叠标签不能靠归一化修复。
Defined零分母Undefined，允许有符号120/−20对100的1.2/−0.2；
只有非负、正总额和完整不交分区才保证[0,1]及合计1。
Unknown/Null 等状态按原量政策和 §5.4 传播，不跳过未定义格子后把剩余格子归一化，也不补零。
后置 where/top 不自动换分母，前置 where/top 改变总体；新份额列不继承原指标 drill/rollup。

### 5.4 Cell、部分运算与谓词

保留Defined/Null/Undefined/Unknown；缺坐标另有presence，对outer缺侧Null保留原因。
局部算术Undefined优先，其次Unknown、Null；同优先级原因按稳定角色序收集。
来源 Metric 的零分母政策遵循[语义数值政策](../semantic/expression-model-redesign.md#34-样本政策的归属)；
结果 Column 的 `/` 固定 Defined 零分母为 Undefined。已保存政策不因新默认或新Catalog变化。
非有限输入/溢出是错误，Unknown不能被fill/忽略Null吞掉。

状态谓词返回 Defined 布尔；必要结构/执行检查仍保留。
where最终谓词要求Defined，不能静默删未知格子。
布尔使用固定强Kleene规则，Undefined优先；False&Unknown可False，
但不能消除子表达式的绑定/执行错误。
来源受限Ibis运算沿其原声明；Field公共筛选的源Null需明确处理，
不把结果Column的严格默认注入来源Ibis规约。
先选择Defined再构造值统计会改变范围，应记录。
row_count计全部坐标，不读任意量列模拟；多项reduce共享同实际输入，
每项有效样本独立，不能用成功计数掩盖原Query的必要失败。
