# AnalysisFrame 数据对象与执行架构设计

Status: current AnalysisFrame data contract，2026-10-21；澄清轴绑定与当前行规约，不改变 DSL 行为。
本文替代此前 Query/origin、自动历史复用及按物化状态限制续算的提案。
当前 API 与执行边界由[分析架构](README.md)和
[Session State and Runtime](session-state-and-runtime.md) 共同维护；后文实施步骤与验收场景
保留原始设计记录，不作为新执行或发布证据。
[Attribute 合同](../../design/analysis/attribute-design.md) 的显式来源入口返回同一种 AnalysisFrame，增加不可执行
方法事实而不恢复原来源。旧 Query.attribute/execute/drill 组合方案不重新引入。

## 1. 核心决策：结构决定运算，载体决定求值

AnalysisFrame 是具有明确结构和运算规则的不可变分析数据对象，可以尚待计算，也可以已物化。
它不是生产计算图的公开包装，也不是没有业务坐标约束的任意 pandas 表。
LogicalPlan 只负责表达显式依赖与惰性求值；对象的方法、字段和类型由当前结构决定。

依据[分析代数](algebra-theory.md#23-求值与定义域)，普通结果具有有限完整坐标域 K，
以及定义在 K 上的命名值。完整键、单位、时间解释、null 与 presence 都属于其数据合同。
组合结果含独立端点 presence；非等值日历对应还需配对坐标投影，均须独立于生产过程解释。

| 维度 | 权威与职责 |
| --- | --- |
| 当前结构 | 描述完整坐标、值和 presence；公共方法统一，具体操作检查其所需信息 |
| 数据合同 | 检查完整键、类型、单位、时间、所有权、映射和操作参数 |
| 载体 | 待执行表达式、完整内存数据、精确持久引用；决定如何取得数据 |

同一种结构来自 observe、字面量、本地变换或冷恢复时，数据操作合同相同。
具体参数或值可以不合法，但不因“已经物化”而撤销 AnalysisFrame 的方法。
未实现的路线必须明确限定为版本范围，不能伪装为类型天然缺少能力。

- 公开 Query 和此前提议的 Analysis 统一由 AnalysisFrame 取代，不保留别名。
- compute 返回绑定完整内存数据的 AnalysisFrame 对象；persist 返回绑定持久数据的 AnalysisFrame 对象。
- 只有 compute/persist 触发求值；persist 是唯一计算并持久发布入口，移除 execute。
- 持久 frame.artifact 返回同一保存结果的终态只读 Artifact，不求值、不重复发布。
- 原对象不变；必须使用返回的物化对象才固定输入，不自动匹配历史计算结果。
- 完整物化不保存生产图、语义对象、可执行 origin、连接或内部聚合组件，不隐式回源。
- 持久恢复保留完整数据结构，不能丢弃必要信息后继续执行。

这里 persist 指项目内持久保存。Artifact 是提交和审计视图，不提供数据预览、导出或运算，
只能从持久 frame.artifact 获取，不能直接作为 DSL 操作数。

## 2. 一个数据类型，统一列与坐标规则

### 2.1 AnalysisFrame 的结构

首版只有一个公开数据类型 AnalysisFrame，包含所有权、载体、有序完整坐标轴、
命名值、presence、必要附件及不可执行方法事实。observe/read/values/frame/attribute、align 和 compare 都返回它。
标量是零轴 frame；不设 ComparisonFrame、CoordinateFrame、ScalarFrame 或抽象基类。
内存/持久状态、单位、列类型和日历轴不产生公开子类，也不开放用户 codec 注册。
对象由 Session/算子构造，不开放原始载体构造器。

compare 消费非空命名基期映射，保留本期轴与一份本期值，再为各基期生成 baseline、
absolute、relative 三组。相对变化为 (current - baseline) / abs(baseline)，零分母政策显式捕获。
缺行或任一值为 NA 时差值为 NA，之后才对有效数值对应用零分母政策。
Duration 的 baseline/absolute 保留原 tick 单位，相对变化无量纲。
结果保存有类型的有限端点、映射、值绑定与 presence 说明，不保存 Metric、来源图或执行回调。
这些事实解释已经完成的值，不授权原贡献重算；不增加公共比较结果子类型或 endpoint 方法。

### 2.2 运算不创建结果子类型

compute/persist 只转换载体；where/top/select/reduce/correlate/align/compare 均返回 AnalysisFrame，
按操作更新坐标、命名值和 presence。第 5.5 节统一规定结构传播，不额外维护子类型保持规则。

列算术产生 Column，由 select 等接收者消费；不提供原地修改列或 attrs 的入口。
例如 select({"revenue": x["left.revenue"]}) 可取左侧值，并保留当前坐标和 presence。
它不恢复原左输入域，不按端点键重索引，也不自动丢弃右侧独有行；需要时显式 where。
没有额外 endpoint 入口，也不按投影后的列名猜测结果类型。

### 2.3 名称与 presence

用户在 observe/read/values/select/reduce 中声明的量名是单个非空名称段，不含点号、
反斜线或控制字符，也不得使用 left_present/right_present。
这是命名合同切换，非法名在构造时给出重命名修复，不自动转义或静默改名。
生成列采用不可变名称段路径，点号仅作公开分隔符；__getitem__ 按完整名称精确查找，
不按字符串后缀推断角色。内部槽 ID、名称路径及值/presence 分类是权威。

| 操作 | 值名称与 presence |
| --- | --- |
| 普通声明 | 用户名称，例如 revenue |
| align | left.q、right.q；新增 left_present/right_present |
| compare | 当前 q 原样保留；每个 alias 添加 alias.baseline.q、alias.absolute.q、alias.relative.q |
| compare 行存在性 | current.present、alias.present；继承路径进入 current.presence.*、alias.presence.* |
| align 继承 presence | 加 left/right 前缀，例如 left.right_present |
| select | 新值使用用户名称段，不能覆盖已有完整值或 presence 路径 |

output_names 仅含值，presence_names 仅含独立 presence，均按定义次序列出完整路径。
compare 先列出全部当前值，再按命名映射声明顺序逐个输出该基期的三组值；各组依当前值次序。
每端非空值路径集合须相同，按精确路径验证数值类型和单位，不因同名认定业务公式相同。
生成路径冲突时拒绝并要求先 select 重命名，不覆盖或自动编号。别名为单段名称，current 保留。

presence 通过 __getitem__ 取得不可空 boolean Column，独立于值 NA 和事实贡献是否存在。
缺基期行可显式用 x.where(~x["reference.present"]).reduce({"missing": mv.row_count()})。
继承的历史 presence 与本次端点 presence 不混合；布尔身份不赋予数值 sum/mean 权限。
再次 compare 按全部现存 output_names 逐列检查；需要仅比较某个值时先 select 成一致名称。
普通当前轴允许后续合格组合，paired 输入的拒绝合同继续保留。

### 2.4 完整方法事实与当前数据分离

`method_facts: tuple[FrameMethodFacts, ...]` 是完整不可变解释记录，不是来源定义或执行资格。
成功的 `contribution=True` 请求生成本次 Attribute 事实；默认调查不生成新事实或贡献证明。
显式消费的 Frame 历史仍按实际表达式输入顺序继承：每个 Attribute 节点先本次事实再继承，
外层 binary 完整保留左侧再右侧，按 origin_result_id 稳定去重。例如 A(B) 与 C(D) 组合为
A,B,C,D，而非把所有本次 producer 提前。固定输入是自包含叶，不展开一般 lineage。

事实保留原结果的 output_schema、完整父记录和独立冻结坐标，原 origin 不随 where/top/select/
reduce 改写。它只解释原总体；筛选不改变原贡献分母，派生算术不自动获得新分解认证。
只读完整字段使用 `frame.method_facts`；pending 访问要求先 compute/persist。具体记录与嵌套字段
由 `marivo.help("analysis.frame_method_facts")` 静态披露，不导出内部别名或保存描述符。

内存根保存完整记录；存储根与 manifest 只保存计数、schema、origin、digest 和 owned payload
描述符。repr/show/open/idempotent persist 不读取完整方法文件，卡片只给数量、有界 origin 和
getter 指针。getter 单独校验完整方法载荷，不读当前主表、Catalog、模型或来源；调用记录的
show(n=0) 发生在该显式读取之后。to_pandas 只读当前数据及必要坐标附件，stored compute 与
派生求值消费当前数据和完整事实。坏方法文件可使后者失败而不阻断元数据/当前行导出。
保存派生结果复制自己的 owned 方法载荷，原产物删除后仍能读取，不按 origin 查找原结果。

## 3. 公共 API 与使用方式

以下列出核心当前签名；现有数值表达式、Statistic、单位和错误类型沿用其所属合同。
FrameAxis 是从 frame.axes 或 frame.axis(name) 获得的只读完整轴句柄，绑定精确输入和局部轴 token，
不能用字符串、列值或语义 Ref 脱离所属 frame 直接构造。它使冷恢复后的坐标操作不依赖语义轴对象。
数据轴保存角色、完整键类型与时间解释，不保存原 Entity/Field 的名义绑定；显示名即使是语义路径也不证明该绑定。
待执行、内存和持久 Frame 均使用此边界；identity 角色表示完整身份键形状，不证明来自某个特定 Entity。

```python
from __future__ import annotations
from typing import Literal

class AnalysisFrame:
    def compute(self) -> AnalysisFrame: ...
    def persist(self) -> AnalysisFrame: ...
    def show(self, *, n: int = 20) -> None: ...
    def to_pandas(self) -> pandas.DataFrame: ...

    @property
    def artifact(self) -> Artifact: ...

    @property
    def output_names(self) -> tuple[str, ...]: ...

    @property
    def axes(self) -> tuple[FrameAxis, ...]: ...

    @property
    def presence_names(self) -> tuple[str, ...]: ...

    @property
    def method_facts(self) -> tuple[FrameMethodFacts, ...]: ...

    def __getitem__(self, name: str) -> Column: ...
    def axis(self, name: str) -> FrameAxis: ...
    def coord(self, axis: FrameAxis) -> Column: ...
    def where(self, predicate: Column) -> AnalysisFrame: ...
    def select(self, values: Mapping[str, Column | LiteralValue]) -> AnalysisFrame: ...
    def top(self, n: int, *, by: Column, descending: bool = True) -> AnalysisFrame: ...
    def reduce(
        self, values: Mapping[str, Statistic], *, by: tuple[FrameAxis, ...] = ()
    ) -> AnalysisFrame: ...
    def correlate(
        self, x: Column, y: Column, *,
        method: Literal["pearson", "spearman"], by: tuple[FrameAxis, ...] = ()
    ) -> AnalysisFrame: ...
    def align(
        self,
        other: AnalysisFrame,
        *,
        on: tuple[tuple[FrameAxis, FrameAxis], ...] | None = None,
        match: CalendarCorrespondence | None = None,
        how: Literal["exact", "outer"] = "exact",
    ) -> AnalysisFrame: ...
    def compare(
        self,
        baselines: Mapping[str, AnalysisFrame],
        *,
        on: Mapping[str, tuple[tuple[FrameAxis, FrameAxis], ...]] | None = None,
        match: Mapping[str, CalendarCorrespondence] | None = None,
        how: Literal["exact", "outer"] = "exact",
        zero_denominator: Literal["null", "error"] = "null",
    ) -> AnalysisFrame: ...

class Session:
    def frame(self, population: Population) -> AnalysisFrame: ...
    def persisted_frame(self, artifact_id: str) -> AnalysisFrame: ...
    def population_from(
        self, frame: AnalysisFrame, *, on: tuple[tuple[FrameAxis, Deferred | Entity], ...]
    ) -> Population: ...
```

本段只列本次闭合的核心签名；Column 的算术、比较、NA 检查和 Statistic 构造沿用现有语法。
Statistic 指既有 unfinished statistic 协议，不要求新增同名公共导出。
FrameAxis 只增加当前数据结构的选择能力；不是新的 Entity、Field 或日历规则对象。
axis(name) 在接收者的当前轴目录中按完整显示名精确查找；不存在或重名时给出结构化错误，
列出实际候选，要求从 axes 选择明确句柄，不任意取首项。该查找不构成跨输入匹配。
select 保留轴名称，reduce 保留所选轴名称；组合后的轴名称来自左侧对应轴，右侧信息保存在必要描述中。
coord(axis) 取坐标列；__getitem__(name) 只取值列与 presence，不能用值名代替坐标句柄。
CalendarCorrespondence 复用 ordinal、offset(years=...) 与 offset(periods=...) 的闭合值协议，
它描述匹配规则，不携带 CalendarAxis 或执行回调；第 5.3 节限定数据侧支持范围。
完整复合轴保持一个句柄；coord 首版只接受单物理分量轴，不拆开复合身份来伪装标量。
by 和 on 可以选择完整复合轴，内部展开其完整键分量；空 by 表示合法的零轴标量规约。
align/compare 的 on 省略时按第 5.2 节推导；显式给出时必须完整，不与自动补齐混用。
population_from 的 on 仍必填，因为它声明到调用方语义对象的来源绑定，不属于 Frame 间轴对应推导。

session.observe/read/values 名称保持，返回 AnalysisFrame；session.query(population) 改为
session.frame(population)。恢复入口统一为 session.persisted_frame(id)，不保留此前提案的
session.analysis/persisted_analysis，也不增加载体专用构造器或通用公开 result ID。
每个 analysis Artifact 均保存 Frame 数据；唯一 ID 恢复入口为 persisted_frame，
移除 session.artifact(id)，审计通过 session.persisted_frame(id).artifact 获取。
session.frame(population) 保留为键域到数据对象的唯一入口：支持一般完整 Population，
不要求一个 Entity 或新属性；read 则要求单 Entity 身份域及非空属性，values 声明字面量行，
observe 声明指标求值。允许 read 的 fields 为空仍不能覆盖一般多轴 Population，反而引入双入口。
这些入口不通过空参数互相代用。frame 的 output_names 可为空，但完整键域及空域检查仍适用。
派生对象的结构和输入绑定均不可变；不开放直接替换载体或修改结构的公共入口。

未完成统计量由 `mv.row_count()` 与 Column 的 `sum/mean/corr/pair_count` 获取，
只读公开字段为 `unit`，由 `frame.reduce(...)` 消费。row_count 请求未绑定，可复用于
不同 Frame，消费不修改原请求；Column 统计量绑定精确 Frame/Session，不能以名称、
结构或值相同的另一输入替换。它们不公开 AST 或重绑定入口。

端点描述使用 `mv.attribute_endpoint(metrics, during=None, where=None, memberships=())`；
它复制有序映射且不读取来源，`session.attribute` 继续拥有归属、绑定与贡献准入校验。
`AttributeEndpoint` 仅作为返回类型公开。

### 3.1 普通结果与比较结果

```python
import marivo.analysis as mv

session = mv.session.create("revenue-review", timezone="UTC")
revenue = session.catalog.metrics.get("sales.revenue")
region = session.catalog.fields.get("sales.orders.region")
current_period = session.time_scope(start="2026-08-01", end="2026-09-01")
baseline_period = session.time_scope(start="2026-07-01", end="2026-08-01")

current = session.observe({'revenue': revenue}, by=(current_period, region)).compute()
baseline = session.observe({'revenue': revenue}, by=(baseline_period, region)).compute()
current.show(n=10)

comparison = current.compare(
    {"reference": baseline},
    on={"reference": tuple(zip(current.axes, baseline.axes))},
    match={"reference": mv.ordinal()},
)
comparison.show()
saved = comparison.persist()
saved.show(n=10)
saved.artifact.show()

selected = saved.where(saved["reference.absolute.revenue"] > 0)
selected.show()
plain = selected.select({"change": selected["reference.absolute.revenue"]}).compute()
values = plain.to_pandas()
mean = plain.reduce({"mean_change": plain["change"].mean()}).compute()
```

两侧均有 interval 与 region 轴；on 覆盖全部轴，ordinal 显式配对两期，region 按完整键等值对应；
不能仅因为显示名相同就自动匹配。两次来源求值时间可以不同，不宣称共同事务快照。
comparison/selected 是待执行对象，其 show 不执行比较或筛选；saved 才拥有自身产物。

全轴对齐不要求量名或单位相等，先保留双方各自的类型与单位，再由算术合同检查表达式。
例如预算 frame 可由 session.values 显式声明地区坐标与 budget，直接组合为：

```python
aligned = actual.align(budget, on=((actual.axis("region"), budget.axis("region")),))
attainment = aligned.select({
    "attainment": aligned["left.actual"] / aligned["right.budget"],
}).compute()
```

这里 actual/budget 是调用方已构造的同 Session frame；不要求用 compare 凑出同名量。
标量预算到多轴结果的自动广播仍不包含在全轴 align 中，必须显式提供相同目标域的预算。

### 3.1.1 可推导的比较不重复声明轴

沿用上述 revenue、current_period（8 月）和 baseline_period（7 月），比较两个月的每日销售额：

```python
jul_daily = session.observe({'revenue': revenue}, by=(baseline_period.by(mv.grain('day')),))
aug_daily = session.observe({'revenue': revenue}, by=(current_period.by(mv.grain('day')),))
daily_change = aug_daily.compare({'reference': jul_daily}, match={'reference': mv.ordinal()}).compute()
daily_change.show(n=31)
```

双方都只有一个日历轴，on 自动确定；ordinal 明确选择月内第 1 天对第 1 天，依次对应。
7 月和 8 月都有 31 天，默认 exact 检查实际行完整配对。日粒度的 offset(periods=-1)
表示前一天，不能根据月份范围自动替换 ordinal；两种比较意图须由调用方选择。

比较 8 月上海与北京的整月销售额时，城市作为来源条件，两侧只有同一个 interval 轴：

```python
city = session.catalog.fields.get("sales.orders.city")
shanghai = session.observe({'revenue': revenue}, by=(current_period,), where=city == '上海')
beijing = session.observe({'revenue': revenue}, by=(current_period,), where=city == '北京')
city_change = shanghai.compare({'reference': beijing}).compute()
city_change.show()
```

无需 on=()；revenue 为上海，reference.baseline.revenue 为北京，reference.absolute.revenue 为上海减北京。
若两侧都改为 by=(current_period.by(mv.grain("day")),)，同样省略 on 和 match，
即可按同一天等值对应。改用返回的内存/持久 frame 或冷恢复对象时，推导规则不变。

### 3.2 生命周期入口

| 输入载体 | compute | persist | artifact | to_pandas |
| --- | --- | --- | --- | --- |
| 待执行表达式 | 求值，返回 AnalysisFrame 内存根 | 求值并提交，返回 AnalysisFrame 存储根 | 拒绝，要求先 persist | 拒绝，要求先 compute/persist |
| 完整内存根 | 返回自身 | 保存现有输出，返回 AnalysisFrame 存储根 | 拒绝，要求先 persist | 返回隔离副本 |
| 精确存储根 | 完整验证并加载，返回 AnalysisFrame 内存根 | 检查提交元数据后返回自身 | 打开同一产物视图 | 验证、读取并返回隔离副本 |

compute/persist 同步完成后返回；派生运算仍然惰性。纯载体转换保留结果身份和求值时间。
存储根 persist/artifact/恢复的元数据检查不承诺验证全部载荷文件；实际读取才验证所消费数据。
来源表达式重复求值可能读取变化后的来源；相同定义重复 persist 可以产生不同结果与产物。
内存根多次保存可产生多个提交，但不重新求值。存储根的幂等返回不新增 Run 或时间。

派生对象的 artifact 不得返回祖先产物，to_pandas 不自动执行派生计划。
导出值及 attrs 与内部数据隔离；公开 pandas attrs 不接受反向导入为计算合同。

### 3.3 Frame 的 show(n=20) 与 Artifact 元数据卡片

| 接收者状态 | 展示内容与读取边界 |
| --- | --- |
| 待执行 frame | 当前结构、已捕获范围、输入类型及待执行操作；未知行数/类型明确标注；不读父结果行、不做 schema 准备、不为 show 额外加载 Catalog |
| 内存 frame | 结构元数据、总行数和已有结果前 n 行，不读旧文件 |
| 持久 frame | 提交元数据、总行数和保存结果前 n 行，不加载模型或访问数据源 |

AnalysisFrame.show(*, n=20) -> None 统一各载体的预览。
n 必须是非负整数且不能为 bool，所有状态在读取前校验。n=0 只展示元数据，
不读取结果行、数据附件或校验载荷文件；持久对象仍可验证提交元数据。
元数据可用不保证数据文件完整；空结果与用户要求省略预览分别标明。

内存/持久 frame 共用限行表格渲染，各载体适配读取；内存预览不构造临时 Artifact。
Artifact.show() 没有 n 参数，只展示提交身份、时间和审计摘要及返回 frame 读取的指引，
不读结果表或坐标附件。Artifact 保留 id、session_id、findings 和 evidence_digest，移除 to_pandas。
预览取结果行序前 n 行，不重排、不抽样；超出卡片行、列、文本预算时明确省略。
禁止以 to_pandas().head(n) 完整导出再预览。直接/命名比较需校验所消费的完整有限端点/映射
权威，主表仍限 n 行；普通/paired-only 预览保留原选中坐标边界，不泛化为全载荷校验。
收据校验可能读取整个被消费文件，因此有限行解码不保证有限字节 I/O。
预览成功不证明未消费部分已验证；读取错误不静默降级为元数据卡片。
show 不求值、不创建 Run/Artifact，不缓存预览为完整结果，也不展示祖先行冒充派生输出。

## 4. 运算闭合性与来源边界

### 4.1 数据操作只消费显式输入

对象的方法按第 2 节结构静态定义；构造方法检查结构和参数、推导输出类型，并产生新的表达式。
依赖实际值的唯一性、映射覆盖和数值检查在消费前完成，不能靠类名或旧 Evidence 略过。
方法不扫描生产图寻找 presence、原 Metric 或额外资格。

首版数据操作包含值选择/算术、where(Column)、全局 top、按已有完整轴 reduce/correlate、
全轴 align/compare 及有限日历对应。它们在各载体上使用同一合同。
按已有轴规约不是日历粗化；不能用天桶句柄请求月桶，也不能隐式读取新属性。
每个 Statistic 使用自己的有效值掩码，保留既有单位、空输入、全 NA、零分母与精度政策。
标量 reduce 对空输入仍按既有统计合同产生单行标量，不与零行零轴域混淆。

一次计划可以显式组合来源表达式和固定输入；只执行其中明确存在的来源叶子。
固定叶子不因与来源混合而恢复旧生产图或刷新其值；跨 Session 输入仍拒绝。
首版采用现有来源阶段加本地数据内核，不新增把持久数据上传远端或跨载体融合的优化路线。
消费者要求更多信息时应拒绝，不能为满足需求而生成隐式来源叶子。

Top-0、空结果或列裁剪不能消除必要的上游检查。逻辑优化既要保值、域、类型和顺序，
也要保留错误与检查边界；是否能下推是编译器问题，不改变对象的公开方法集合。

### 4.2 原量问题重构归显式来源计算

已退役的 Query.rollup/drill 依赖原 Metric、贡献和语义分类，可重新读取来源。
它们不搬到 AnalysisFrame 上；物化前也不通过特殊来源子类保留这些方法。
更改观察粒度、期间、贡献筛选或读取新属性，应显式通过 observe/read 等来源入口构造新分析，
由调用方提供 Metric、Field、范围和必要的完整选择域，不能从 frame 找回原定义。

“选中地区后再看城市”需要把选中的地区域作为显式来源约束，再提供所需指标与城市绑定。
新的来源计算可能看到新数据；固定选择键与重跑原 Top 排名是不同语义。
只有地区输出不能恢复曾经选中过的城市名单，当前细分不能声称复现历史中间结果。

普通 reduce 的对象是当前行值，不等于原量重聚合：
客单价 `100/1`、`900/90` 的行均值为 55，原整体均值为 `1000/91`；
distinct 桶 `{1,2}`、`{2,3}` 的计数和为 4，合并 distinct 为 3。
保存均值或计数不自动取得原量能力。首版不保存隐藏 sum/count、贡献明细或原公式。
sum/mean 按当前值表达式保留单位；单位描述量纲，不认证原 Metric 的可加性或允许沿被移除轴上卷。
每日库存之和仍可标为件，比率之和仍可无量纲；这不使它们成为月库存或整体比率。

`frame.correlate(x, y, method="pearson" | "spearman", by=())` 是当前行成对相关的
常规入口：x、y 及完整 by 轴均绑定该精确 Frame/Session 输入，返回惰性普通 Frame，
`output_names == ("coefficient", "n_pairs")`。系数为 nullable float64、unit="1"；
配对数为 nonnullable int64、无单位。两端可以有不同数值类型/单位，但不接受 Duration。
每组先检查两个表达式，再取共同非 NA 样本；零和重复值保留，presence 不隐式筛样本。
不足两对或常量端时系数为 NA，配对数仍保留；零配对组不删除，空标量为一行 NA/0。
Spearman 在各组有效配对内使用平均秩。`Column.corr` / `Column.pair_count` 可由 reduce
显式命名组合其它统计；每项仅一列，名称不会推断配对关系，其它统计仍用独立掩码。
保存的分组系数/计数不能恢复总体相关；从原样本重新规约。详见
[Correlation Statistics](../../design/analysis/correlation-statistics-design.md)。

当前 share 所要求的贡献可加性和共同实际输入不能改成普通数值列的隐含权限。
显式算术只能产生调用方定义的当前值比例，不自动带有原贡献份额保证。
首版不提供原 share 方法；若后续需要，须单独定义充分输入及合同，不按来源历史猜测。

### 4.3 Population 和关系操作的边界

Population 仍拥有有限完整键域及集合语义，不能通过统一 Frame 名称抹掉它与值表的区别。
frame 的坐标投影只产生当前域，不包含丢失的身份、贡献出现或原选择条件。
沿语义关系找像/原像、分类和属性读取需要显式来源依赖，不作为恢复对象的隐式能力。

首版增加唯一桥接入口 session.population_from(frame, on=...)，将选定结果轴显式绑定到
调用方提供的已解析 Field Deferred 或 Entity，生成可用于来源约束的 Population。
这是 Population 构造族的键域投影和显式来源绑定，与来源枚举 members、字面构造 coordinates 分工明确；
由 Session 校验目标 Catalog 绑定，不是 frame 恢复原语义对象；原 frame 不变。
on 只能选普通字段轴或完整身份轴，轴属于该 frame、目标角色与完整键类型必须相容，
目标绑定按现有语义规则验证，不靠同名查找。首版不绑定interval 轴、日历轴、配对轴或任意值列。
该入口是坐标投影后建立新绑定，不是恢复原 Entity/Field。目标与原轴的业务对应由调用方声明；
系统不检查二者语义身份相同。结构相容的 Customer 轴可以绑定 Product，收货城市键也可以绑定开票城市，
但不能据此声称身份或维度等价。目标 Catalog、角色、完整键类型、单位及时间约束仍须通过检查。

桥接按所选轴的完整联合键投影并去重，不能逐轴拆开再做笛卡尔积。
字段 Null 是合法类别；Entity 身份要求完整非空主键，不能把普通字段升级成身份。
空输入得到空域，不表示没有筛选；键存在不证明来源有事实。
构造不读取行；求值时先完成输入检查及选择，再固定本次键，最后执行显式新来源依赖。
来自物化根的桥接持有固定数据依赖，后续来源计算不重跑其原 Top。
来自待执行 frame 的桥接每次调用会先求值该选择计划；想固定历史选择需先 compute/persist。

用于贡献过滤时走 observe 的 where=mv.in_population(...)；by=Population 只选择其完整输出键，
保留无事实坐标，不要求覆盖域外贡献。P.count() 则独立捕获完整去重元组域，
分组只匹配域内已有坐标，不隐式读取新属性；成员属性分组先显式 classify 并保留身份。
不增加 frame.population 或 where(Deferred) 重载；复杂关系导航仍需显式来源参数。

```python
regional = session.observe({'revenue': revenue}, by=(current_period, region))
chosen = regional.top(5, by=regional["revenue"]).compute()
parents = session.population_from(chosen, on=((chosen.axis("region"), region),))
cities = session.observe({'revenue': revenue}, by=(current_period, region, city), where=mv.in_population(parents)).compute()
```

这里 city 是调用方显式取得的新分类。来源变化时仍只调查已选地区，但城市值是本次新读取。
不用导出 pandas 或抄写字面量；这一桥接不承诺复现选择时的数值或恢复丢失的城市选择。

## 5. 坐标、对应与结构保存

### 5.1 结果内坐标与输入身份

局部 axis.0 等 token 只在所属 frame 的结构目录内解析，不是全局坐标身份。
FrameAxis/Column/_Statistic 绑定精确 input_id；来自其他输入的句柄不能直接参与当前输入的运算。
同一对象的列算术无需对应协议，跨对象先用显式组合入口绑定双方。
_Statistic 从 Column 继承 input_id；只有 mv.row_count() 保留输入无关形式，消费时绑定接收者。
载体转换后不能把旧 Column 生成的 Statistic 混入新根的 reduce；同样适用跨 Session 检查。

| 标识 | 生成和用途 |
| --- | --- |
| input_id | 精确接收者绑定；内存根为本次绑定 token，存储根为 Store/Session/Artifact 组合，派生表达式获得独立绑定 |
| result_id | 每次完成新结果的身份；载体转换保留，不能据此检索历史数据或证明来源新鲜 |
| 局部轴 token | 当前结构内的引用，不建立跨结果等价或自动对齐 |

两次恢复同一存储根具有相同稳定输入身份；载体转换后需从返回对象重新取得列和轴句柄。
计算指纹用于计划优化，不代替结果身份或跨输入绑定。相同数值不意味着相同结果。
Artifact ID 表示提交；同一内存结果可以有不同提交，不按 result_id 自动去重。

### 5.2 跨结果对应：唯一推导或显式绑定

align 接收另一 Frame；compare 始终接收非空有序命名 Frame 映射，单基期也必须命名。
每端先验证同 Session、无 paired 轴、相等轴数与相容的完整键/角色/时间解释。
零轴或双方唯一同种 interval/calendar 轴可唯一推导；含普通轴时必须显式完整双射。
compare 的 on/match 按基期别名分别提供；未知别名拒绝，部分 on 不自动补足。
FrameAxis 的显示名、token、dtype 或相同值不证明跨输入身份，不依此猜映射。
普通轴显式 on 声明本次业务对应；检查只保证结构相容以及所选政策下的实际键域，
不验证原 Entity/Field 相同。Customer/Product 的同类型身份键可以显式配对；exact 成功不证明业务身份等价。
compare 的同名量配对同样是调用方组织输入的方式，兼容类型与单位不证明相同 Metric 或业务可比性。

compare 的时间关系按每个已绑定基期独立选择：显式 match 优先表达意图，但须与存在的直接
shift 关系一致；省略时仅消费锚点与本期完整冻结域相符的直接关系，否则拒绝错误锚点。
没有直接关系也没有 match 时按完整键等值，不猜日期偏移或沿祖先追溯。
锚点比较 Grain、时区、规则版本、选定键与名义/实际边界，不要求同一内存对象或相同数据值。
align 仍使用显式 match 或等值，不引入自动 shift 推导。

每个基期的实际完整联合键先映到本期坐标，其它普通轴仍完整键等值。
exact 要求各端实际键域相等；outer 先保留本期行序，再按基期声明序/原行序加入新联合键。
额外目录桶无实际行时不制造输出行，也不单独使 exact 失败。实际基期时间行无法映到本期
声明域时，两种政策均拒绝；需要一般双端时间并集时使用 align。
compare 不裁短本期、不填零、不为某个别名重求本期量或分类。显式贡献 Population 不重选；
显式 by=P 是该端完整输出域，基期对应时间×普通键域须由调用方独立构造。

align 保留左右值和独立 left_present/right_present，显式时间 match 可生成单层 paired 轴。
compare 始终保留本期普通时间轴、当前值及命名说明；current.present/alias.present 只表示行存在。
两者拒绝已有 paired 输入；select/where 不删除坐标，不能绕过该限制。

### 5.2.1 interval 的冻结目录与对应

TimeScope 在 by 中产生 interval 轴，保存精确 start/end 与边界时区，不自动变成零轴。
等值对应识别精确边界和时区。ordinal 要求每端完整声明目录恰有一条记录且边界时区相同，
不同期间可明确配对；过滤成一行不会把多区间目录改成单区间目录。
interval/calendar/零轴不互转或广播；time×普通分类仍须完整显式 on。
compare 保留当前 interval 及命名基期说明；align 的双端并集/paired 语义继续存在。
Store 15 / marivo.frame_output/v4 保存目录、实际键引用与关系，冷恢复不重建 TimeScope。

### 5.3 日历偏移、精确选择与自包含信息

规则唯一入口为 mv.ordinal()、mv.offset(years=n) / mv.offset(periods=n)。offset 必须且只能
指定一个非 bool 整数关键字；允许负、零、正值。periods 按名义粒度偏移，不按现存行位置；
years 按民用年中的同名期间，week 使用 ISO week-year，不按固定 365 天或 52 周。

| 规则/接收者 | 准入与含义 |
| --- | --- |
| CalendarAxis.shift(offset) | 单位步长内建 week/month/year；完整桶或从桶首开始的前缀；构造精确目标域及直接部分一一映射 |
| 数据侧 offset match | 完整 builtin day/week/month/quarter/year；合法 week/month/year 前缀须验证双方已保存实际进度相同；部分 day/quarter 拒绝 |
| ordinal | 相容连续非重叠完整名义分区和完整实际边界，保留原始序号；或同边界时区的单 interval；不能用于 shift |

完整桶对应完整目标桶，闰月允许 29 天对 28 天；前缀按目标周的相同星期、月的相同日号、
年的相同月日及当地钟点确定截止。缺目标 ISO W53 与缺民用截止日期是不同合法无对应；
不夹日期、不缩短本期。MTD/YTD 的日历位置不是相同 elapsed seconds 或年内天数。
非零源 shift 的目标 DST gap/fold 拒绝，需显式无歧义区间；零偏移复制原始精确 instant。

shift 可产生稀疏或全空选定域，保留 Grain、时区与无对应说明；不补中间桶、不造哨兵行。
每次 shift 只保存直接输入端，链式 shift 不累计规则复活已丢失的期间。
observe 按精确实际选定区间过滤贡献；窗口按名义日历距离完整求原 Metric，不合并旧聚合值。

冻结目录保存选定桶键、稳定名义 ordinal、名义/实际 UTC 与规范化民用边界、时区和规则版本。
shift 另保存直接来源的完整端点目录和明确匹配/无对应映射；compare 保存每个命名基期的
端点目录、已消费映射、值绑定及端点 presence 所有权。这些是有限不可执行数据，不是来源图。
数据侧规则只查询已保存目标桶及进度，不能把已聚合的完整基期裁成前缀或用当前 TZ/Catalog 补齐。
缺失/损坏附件、键或边界是完整性错误，不归为合法缺期。命名日历仅支持既有 ordinal；
命名 offset、子日 offset、多日历匹配和任意关系映射不在本范围。

```python
periods = session.time_scope(start="2026-01-01", end="2026-10-20T12:00:00Z").by(mv.grain("month"))
metrics = {"revenue": revenue}
current_series = session.observe(metrics, by=(periods,))
previous_series = session.observe(metrics, by=(periods.shift(mv.offset(years=-1)),))
yearly = current_series.compare({"yoy": previous_series}, how="outer").compute()
```

这里 revenue 为显式已解析来源 Metric。每个端点独立完整求值；同图可以共享合格依赖，
但不承诺跨来源事务快照。保存端点恢复后，锚点一致时仍可省略 match，不恢复 Metric。
手动准备的历史 Frame 使用显式 match={"yoy": mv.offset(years=-1)}；别名不推断规则。

### 5.4 闭合数据目录

物化输出包含确定的值 schema、完整键分组、行数据、独立 presence 和必要附件。
优先扩展现有 DataSchema/DataAxis/DataProjection；下面是编码变体，不另建公开坐标继承树。

| 变体 | 必需数据 |
| --- | --- |
| 普通坐标 | 局部 token、identity/field 角色、显示名、有序完整键分量及类型/nullability、已捕获时间锚点等不可执行解释 |
| 日历坐标 | 局部 token、桶键类型、timezone、grain/快照标识、第 5.3 节规则版本及完整名义域、桶键到名义/实际边界的有限映射 |
| 配对坐标 | 局部 token、完整配对键布局、左右日历轴的局部 token、配对键到左右桶键的有限映射；每侧桶键可缺省，但不能同时缺省 |

角色、名称、时间锚点和日历标识解释数据，不是可解析的语义 Ref 或模型加载指令。
配对轴仅引用左右两个日历轴描述，引用须闭合且路径唯一；首版不允许递归配对轴。
配对键及其桶映射由第 5.3 节的名义对应决定，不因某个地区等完整行键缺侧而改变。
桶键缺省只表示冻结目录中没有对应桶，不表示某个完整结果行缺失；这里不另存坐标 presence。
left_present/right_present 及继承路径是单独的非 nullable boolean 行列，不能从桶映射推断。
复合身份始终保持一个完整轴；桶投影按配对轴键定位，不要求整行端点存在，值 NA 不替代行 presence。
日历按保存桶键定位，不依赖当前 CalendarAxis、来源行号或当前日历规则。
元数据只保留小型描述及附件引用，不能为打开句柄而解码全部坐标值。

配对坐标的数据投影只用于解释 align 的完整行键，不附带原输入值。
compare 使用同一 Frame 保存协议，并保存有限命名值绑定、端点目录、已消费映射与 presence 说明；
不靠列名前缀或 pandas attrs 恢复它们。
零分母政策在构造时捕获并在本次求值完成，不进入后续运算资格或专用结果布局。

### 5.5 结构传播与完整性

| 变换 | 必须保留或更新的结构 |
| --- | --- |
| 载体转换/冷恢复 | 完整数据结构、坐标目录、数据附件与行序 |
| align | 保留左右值和 left/right presence；显式时间对应可生成 paired 轴 |
| compare | 保留本期轴与值，按别名添加值组与端点 presence；已有说明按精确路径与时域运输，缺外层行时继承 presence 为 false |
| where/top | 当前行域、值和所有行级 presence 同步筛选；配对桶映射可按保留轴键裁剪 |
| select | 保留完整坐标、所有行级 presence 及必要映射；直接 Column 可复制/重命名比较值绑定，表达式不获得认证；无直接值时保留有限 presence-only 说明 |
| 按已有轴 reduce | 生成分组域，保留所选当前轴及其目录；删除全部 presence 和比较列认证/逐桶基期关系，不自动 any/all 或取首值 |
| 标量 reduce | 同样删除全部行级 presence，另移除全部坐标及其附件 |

同完整时域的等值 align/再次 compare 可运输旧值绑定与端点说明；新的时间 match 置换旧时域时，
旧列仅为保存值，不自动移动来源窗口。旧逐桶/值认证删除，只留命名的惰性历史 presence 说明。
日历映射在筛选后保留原声明有限域及原始序号；额外桶不增加输出行或扩大当前域。
reduce 后 presence_names 为空，即使分组保留全部轴也遵守同一规则；桶映射不进入 presence_names。
若需统计缺侧，先用 presence 谓词筛选再 row_count；这些是显式统计值，不重新命名为隐含 presence。
例如同一月份配对下，A 地区两端有行、B 地区只有左行：规约掉地区后仍是一行月份配对，
保留原两桶映射，删除两行不同的 right_present，不因此拆成两个配对键或报告映射冲突。
配对键确实对应多个不同桶映射才属于结构矛盾，必须拒绝，不能取首值修补。

完整读取验证收据、摘要、闭合类型、完整键唯一性、投影覆盖及 presence/日历一致性。
物化前的必要检查必须完成，不能将未履行义务藏在保存结果中。
所有公开结构必须可完整保存；普通、复合、日历及单层配对轴都属于 codec 验收范围。

## 6. 结果行序由计算决定

AnalysisFrame 的计算逻辑决定结果行序；物化、存储、恢复、show 和 to_pandas 原样保持。
不定义独立展示顺序、排列向量或顺序附件，不在持久边界额外按键重排。
轴的顺序和键分量顺序属于 schema，不等于结果行排序。

- where 保留被选中行的相对顺序；select 保持当前行序。
- top 保留 descending: bool = True；升降序均 NA 最后，完整键按既有规范次序裁决并列，不能用局部 token 或随机 ID 排名。
- reduce 的输出按完整分组键的既有规范次序产生；这是算子输出合同，存储不再排序。
- exact align/compare 保持左侧/当前输入行序；outer 先按该行序输出匹配/独有键，再按右侧/基准行序追加独有键；日历映射不改此行序合同。
- 文件分片与批次读取按写入序列拼接，不以文件枚举或并发完成顺序替代。

索引或唯一性检查不得改变主数据行序；按键定位的附件不承担显示排列。
行位置不是业务身份，不能用于自动匹配两个 frame。
未规定确定排序的来源计算只固定本次序列，不承诺独立重新求值仍产生同一行序。

行序验收覆盖整个执行链，不只检查 codec。来源下推只有在最外层 SQL ORDER BY
及 NA/并列键语义可证明时才可承担有序输出；否则保留本地排序阶段。
逐项审查 top/where/select/align/compare/reduce 的本地内核、SQL finish、merge/concat 和分片读取。
pandas outer merge 的默认次序不能作为合同证明；必要时使用执行内临时位置恢复算子规定的顺序，
这些位置在阶段结束前丢弃，不成为保存附件或业务坐标。

## 7. 实现架构：保留执行基础，拆清职责

### 7.1 一套结构描述，多种载体

内部采用闭合载体变体，不用大量可选字段组合：

```text
AnalysisFrame
  structure: typed coordinates / values / presence / required attachments
  owner + input binding
  backing:
    expression: explicit source and data operations
    memory: complete immutable result data
    stored: exact commit reference + small descriptor
```

表达式叶子区分显式来源、字面量、完整内存数据和持久数据。
物化根不保留原表达式；后续算子构造只引用该固定根的新表达式。
Source binding 可以在待执行阶段持有已完成语义 scope，但结果结构不携带语义对象。

方法先验证当前结构与参数，推导输出类型，再构造执行节点。
当前结构描述必须直接列出 presence/配对坐标；禁止通过扫描 CombineValues/SelectValues 等生产节点发现字段。
Column/FrameAxis 持有当前输入与表达式引用以维持必要生命周期，不捕获已经被替换的旧生产图。

### 7.2 当前实现的改造位置

| 现有责任 | 目标变化 |
| --- | --- |
| dsl/query.py | 拆成公共 Frame 生命周期、结构方法与显式来源构造；删除按原 Metric 历史授予数据方法的分支 |
| BoundSignature / BoundQuantity | 结果结构与 Metric 配方/来源绑定分离；物化结果不继续携带 declared_metrics |
| Column、FrameAxis 与 _Statistic.input_id | 绑定当前输入、值槽或轴句柄；不以生产图 fingerprint 代替所有权合同 |
| ComparisonEndpoint / correspondence binding | 分离名义配对桶映射与完整行 presence；数据运算使用闭合描述，来源绑定单独负责语义解析 |
| 编译器、QueryPlan、阶段调度 | 增加固定数据叶子；保留现有阶段交接、检查期限、共享和消费者释放机制 |
| StageOutput | 保持执行内交接；完成时提取独立完整数据，layout 转为闭合数据描述，剥离 components、population_frames 与来源资源 |
| Session._execute_query | 按实际叶子准备；不能仅因是 ResultQuery 就加载 Catalog |
| terminal_publication.execute | 分离求值与提交，compute/persist 共用求值路径，persist 独占持久发布 |
| Frame reader / writer | 承接现有结构 codec、限行预览与完整导出；删除按键重排及 display_order 生产/读取路径 |
| Artifact / Session 读入口 | 只保留提交与审计视图，ID 恢复统一走 persisted_frame；移除重复数据 reader 入口 |

复用 Ibis 来源执行、pandas 本地数值内核和 PyArrow 存储读取，不新增第二执行器。
结构推导、数据验证和物理优化各有单一 owner，不建立独立的动态方法资格注册系统。
有实际依赖才准备来源；纯固定数据图在禁用模型加载与业务连接时仍须运行。
日历绑定沿用完整桶检查，但将现有 _calendar_pairs 中的目录级 exact 拒绝移到实际完整行域检查。
现有按整行缺侧写空的端点投影不能直接充当配对轴桶映射；后者独立于地区等轴上的行存在性。

### 7.3 载体无关的语义边界

同一固定输入上的操作，在内存或持久载体中应保持值、键、presence、单位、时间和行序一致。
来源表达式每次求值可能面对不同数据，因此不能以两次来源执行的数值相等作为一般定律。
物理 dtype 可在来源准备后具体化；后续检查按实际类型运行，不伪造提前已知的类型。
物化完成时需验证输出确实满足所声明数据结构，不能返回结构不完整的成功对象。

## 8. 生命周期与审计

### 8.1 内存、持久引用及提交

内存根强持有完整不可变数据；派生 frame、Column、FrameAxis 和在途执行按依赖保持引用。
删除最初变量不影响剩余消费者，最后依赖释放后可回收，不保证具体 GC 时刻。
Session 不维护强引用结果缓存；执行回调只持必要运行上下文，不间接保活含 Catalog 的生产 Session。

frame 强持有与 Session 共享的内部本地 Store/runtime 服务，所有权为原 Store/Session 精确身份。
利用现有 Session._runtime 与 Catalog 的职责分离，保留读写/发布所需部分；该服务不捕获
Catalog、生产 Session、来源连接或引用它们的回调。来源准备服务只属于显式来源表达式。
不新增公开 runtime 类型，也不在 Session GC 后按 project_root 再打开 Session。
Session 被 GC 后，内存 frame 继续使用原服务读、计算和发布；同身份写入仍使用现有
Store writer guard，不引入第二套单写者协议。GC 不会主动关闭其他 frame 正在持有的服务。
强引用不保证外部存储永远可用；实际发布沿用现有 Store 错误和事务检查，不增加“重新接入”错误。
纯内存读取与本地计算不要求持久目标可写。该服务不反向缓存 frame/内存结果。

saved.compute() 完整加载后，内存根的读取、派生和再次 persist 不再访问旧文件或生产记录。
旧文件丢失不使该内存结果失效；新 persist 仍需满足目标 Store/Session 的发布条件。
返回的持久根不反向强持旧内存数据；原内存变量继续有效。
存储句柄不保证文件仍存在，缺失或损坏直接报错，不作为缓存 miss。

一次执行内共享读取并按消费者释放，不增加跨调用数据缓存。
内存不足就失败，不自动落盘、截断或回源；不新增 close/unpersist/eviction/spill。
首版不引入删除、压缩或跨产物文件共享协议。

compute 不创建持久 Run、Artifact、临时保存结果或失败 Run；失败清理资源且不返回部分结果。
persist 复用既有 writer guard、资源 journal、成功事务和未知提交协调，
数据、结构、审计与 Run 成功一起可见；不能通过重新求值解决提交结果未知。

### 8.2 persist 是 compute 后发布，Run 只表示发布

待执行 frame.persist() 的语义严格等于 frame.compute().persist()：先完整求值，
再发布得到的内存根，所有数值、检查、求值事实及发布行为相同。不增加可观察的融合分支。
其余载体行为以第 3.2 节为准；上述等价只针对待执行表达式，不能将存储根的幂等 persist
展开成 compute().persist() 再复制一份提交。

Run 只有“发布”一种语义，输入标识已有 result_id；求值时间和事实由该结果的摘要拥有，
不在 InvocationInput 再保存一份。
删除 evaluate_and_publish/publish_existing 分支；不因调用者省略 compute 就改变 Run 类型。
求值在 Run admission 前完成，求值失败不创建 Run；发布失败和未知提交按现有事务协议处理。
新 Artifact 的内部 Run 身份只用于原子提交与校验，不公开，也不声称此 Run 计算了值。
求值时间可早于 admission；提交时间位于该 Run 内，不能再用生产 Run admission 限制原求值时间。

保留 Session ID 与 Artifact ID 后精确打开；返回引用丢失时显式重新提交，接受重复 Artifact 和可能变化的来源数据。Session 不提供 Run 历史、结果目录或 graph/roots/heads 接口。
显式派生计算直接消费存储根 A 时，在新结果的求值摘要记录 A，不在发布 Run 中伪造输入读取。
A.compute() 只是载体转换；得到 m 后再 persist 为 B 不新建求值事实，也不额外保存 A→B 边。
这意味着摘要不保证追踪载体复制链，这是删除边界注记的明确取舍。
旧 A 缺失不阻断完整内存 m 发布；需要完整 lineage 的需求不在本版范围。

### 8.3 一份审计摘要，两个部分

| 部分 | 更新规则 |
| --- | --- |
| 来源事实 | 原读取的非秘密来源标识、范围、时间、必要来源检查及适用范围；按值继承，不累积中间事件 |
| 本次求值记录 | 本结果完成时间、输入种类与关联身份、直接消费的持久引用、操作类别和检查适用输入；每次新求值重新生成 |

组合输入保留各自来源事实，只对完全相同记录按值去重，不合并不同时间或范围。
来源范围不冒充当前筛选域，当前域由结果键数据拥有。
从 m1 计算 m2 时继承来源事实并写入 m2 的求值记录，不累积 m1 的操作历史。
记录中的输入身份仅作不可执行审计，不要求旧输入继续存在，也不据此补取数据。
compute 的纯载体加载、persist 和冷恢复均原样保留摘要，不新增持久边界注记。
读取完整性校验不改写结果事实；本次提交时间和写出的产物由发布 Run/提交记录拥有。

复用既有 Evidence 适用字段，不保存生产公式、图、语义对象或 Python 引用，不授予方法资格。
不增加内存事件 ID、审计 DAG 或完整 lineage。展示可限长，必要底层事实不能截断；
历史审计不替代新算子的检查。

## 9. 保存格式与跨进程恢复

所有 persist 结果使用 Store15 的 `marivo.frame_output/v4`、`frame.v4` 和 `frame-output-v4`，
目录为 `.marivo/analysis/generations/v15/`；没有方法事实的 Frame 也使用同一当前协议。
Artifact 审计视图与 Frame 共享同一提交权威。
描述包含协议/消费语义版本、Store/Session/Artifact/result_id、值与坐标目录、附件引用、
收据、一份审计摘要和有界方法描述符；完整事实保存为自己的闭合方法表附件。
数据及所有方法文件在同一既有事务中发布并回读收据，未知提交沿原事务协调，不重复求值。
提交时间、发布 Run 和文件位置复用现有字段。

没有 coordinate/comparison 两种 frame kind，不保存可导入 Python 类路径。
坐标目录自身仍使用普通/日历/配对闭合变体及其版本；恢复统一构造 AnalysisFrame，
验证版本、所有权、提交状态及目录闭合。实际读取验证所消费数据，元数据打开不扫描全部行。
旧格式按 Store generation 拒绝，不迁移、不双读、不从 Evidence 猜测缺失合同。
仅有旧 Store 的项目不能初始化旁路；保留旧字节，在新项目创建新命名 Session。
用原兼容版本导出是独立工作流。

persisted_frame 只接受本 Session 内有效的 analysis Artifact ID 字符串。
首版所有 analysis Artifact 都是 Frame，不增加不存在的非 Frame kind 或相应错误分支。
非法协议/坐标编码按格式错误拒绝；ID 不存在沿用 ArtifactNotFoundError，
传入 Artifact/RawSqlResult 等对象属于参数类型错误。
md.raw_sql 的 RawSqlResult 不进入 analysis Store，继续使用自己的 reader，不新增导入路线。

```python
import marivo.analysis as mv

session = mv.session.resume(saved_session_id)
saved = session.persisted_frame(saved_artifact_id)
saved.show(n=0)
positive = saved.where(saved["reference.absolute.revenue"] > 0).compute()
positive.show(n=10)
copied = positive.persist()
report = copied.artifact
report.show()
```

调用方保存 Session ID 和 frame.artifact.id。恢复、预览、固定数据运算和再次保存
不导入模型、不加载 Catalog、不访问来源 schema，不恢复旧图或原语义对象。
类型未知、必需结构缺失或版本不兼容就拒绝，不将结果降级为普通表继续执行。

结果是固定的历史读取，不自动刷新，不宣称跨来源事务快照。
原读取时间、本地求值完成时间和持久提交时间分别保存，不能以最近提交时间代表来源新鲜。
来源权限变化不自动撤销保存数据；访问遵守项目 Store/Artifact 权限。
需要当前数据时显式构造来源分析，不请求保存根隐式 refresh。

## 10. 错误与披露合同

错误继承 AnalysisError，沿用共享模板，带操作、精确输入、预期、实际、缺失前提和具体修复。

| 情况 | 修复方向 |
| --- | --- |
| 待执行 frame 直接 to_pandas | compute/persist 后读取返回对象 |
| 非持久根访问 artifact | 先 persist，不返回祖先产物 |
| show 的 n 为负数、非整数或 bool | 读取前拒绝，使用 show(n=20) 或 show(n=0) |
| 列/轴句柄属于其他输入 | 从当前 frame 获取句柄；合法跨输入组合通过 align/compare 绑定双方 |
| align/compare 的输入跨 Session | 使用同一 Session 所属的输入；显式 on 不能改变所有权 |
| align/compare 的双方轴数不同 | 列出双方真实轴目录，要求显式规约或重新构造轴结构兼容的输入；补充 on 或改为 outer 均不能修复 |
| 通过所有权、配对轴和轴数检查，但省略 on 的结构不属于首版自动推导形状 | 列出双方真实轴目录及原因，要求完整显式 on 并继续校验；不按同名、同类型或行值猜测 |
| axis(name) 不存在或重名 | 展示实际轴目录，使用精确名称或从 axes 明确选择句柄 |
| align/compare 的任一输入含配对轴 | 首版拒绝再次对齐；显式规约去掉该轴或构造新的来源分析，不隐式拆轴 |
| on 缺轴、重复、类型/时间不兼容或 exact 域不一致 | 指明具体轴与差异，修正完整对应或显式选择合法 outer 政策 |
| 不合法部分桶或保存进度不符 | 给出具体桶/边界；offset 支持 week/month/year 桶首前缀，ordinal 仍须完整桶；重做精确观察，不能裁切旧聚合值 |
| 请求来源重计算或未实现路线 | 指明来源依赖/版本范围及显式来源构造方向，不建议隐式恢复图 |
| 数据结构、附件、保存数据缺失或损坏 | 恢复完整备份或显式重新构造；不降级、不回源 |
| 版本不兼容 | 使用兼容环境或显式重新计算，不改写旧字节 |
| 求值/发布失败、未知提交 | 本次资源清理或提交协调，不重新求值兜底 |

Frame 类型 Help 拥有静态方法及返回类型；show 负责对象结构、状态和已有结果。
不提供通用 capability/action 矩阵，也不按生产图生成方法列表。
repr 有界，显示结构类型、身份、载体状态并指向 show；output_names 提供完整名称。
Artifact 只有元数据卡片与 Finding/Evidence；预览、导出与数据运算统一归 AnalysisFrame。
退役 API 的结构化指引可以解释新入口，不能保留可执行别名。

## 11. 最小实施范围与切换

### 11.1 同一类型在所有载体上统一的范围

| 首版纳入 | 明确延期或移出 Frame |
| --- | --- |
| 单一 AnalysisFrame 及完整 codec | 用户自定义子类型、通用扩展注册表 |
| compute/persist/artifact/show/to_pandas | 异步执行、历史自动复用、TTL、缓存管理 |
| 值选择/算术、where(Column)、全局 top | top 分区、Population 谓词重载 |
| 既有完整轴的 reduce、单分量 coord | 日历粗化、新属性、原量 rollup/drill/share |
| 全轴 align/compare、exact/outer；零轴及唯一日历轴的 on 推导 | 普通轴自动对应、再次对齐配对轴、自动广播、关系/任意映射、time_axis=current 投影 |
| ordinal、builtin offset(years=...)、day/week/month/quarter/year offset(periods=...) | 命名日历 offset、子日/DST offset、多日历轴匹配 |
| 显式来源叶子与固定输入的本地组合 | 固定数据上传远端、跨载体融合优化 |
| 现有 Population 输入及 session.population_from 普通轴桥接 | frame.population、配对/日历轴到来源的复杂交接 |

这是首个版本的明确能力范围，不延续旧提案“来源对象保留所有旧方法、物化对象再裁剪”的规则。
旧 rollup/drill/share 不能机械迁移；保留的同比/环比与对齐路线按新自包含合同实现，
其余关系匹配、广播等不伪装为新接口已覆盖。
切换时必须列明这些退役/延期项及可用的显式来源重建路径；没有等价替代的路线直接说明范围，
不能将部分闭环称为当前全部 DSL 能力的无损替换。

### 11.2 内部交付顺序

1. 结构合同、公共类型与返回类型、列/轴绑定：确立数据对象不变量和来源边界。
2. 普通坐标闭环：显式来源/字面量求值、内存/持久载体、show/导出、冷恢复及首批数据算子。
3. 组合闭环：align/compare、有限日历映射、单层配对轴/presence、各载体往返及普通轴 Population 桥接。
4. 完整公开切换：移除 Query/execute，发布新的 Session 入口，同步披露和验收。

中间阶段不发布双接口或载体相关的能力缩水对象；公共切换前需完成上述数据结构的承诺。
复用现有执行器、内核、收据、事务和渲染，只改必要职责边界；不做无关内部 Query 名称机械替换。
不增加第二数据引擎、origin、全局坐标注册表、图持久化、隐藏聚合组件或事件累积系统。

Query.rollup/drill/share 的接收者准入、依赖原图的资格传播和专用后继执行路线纳入退役清单。
StageOutput 的公开物化提取需要逐项处理 layout/components/population_frames，而不只是更换返回类。
先追踪消费者再删除旧路线：原始 Metric、mean/per 等来源执行仍可能需要内部组件/检查，
不能把所有 components 相关实现一并删除。它们可在单次来源调用内存在，不能进入长期 Frame 数据。
行序下推/SQL finish/本地 merge/codec 审计同属交付范围，不能仅在 writer 去掉 sort 后宣称完成。

核对当前消费者后，Artifact.findings/evidence_digest 经 TerminalReader 只读取提交、Finding/Evidence
记录，不调用表格预览或导出；Session Run 记录保留发布引用。因此保留这些审计路线，
将 TerminalReader 的数据读取实现移交 Frame reader。移除 session.artifact/Artifact.to_pandas、
修改 Artifact.show 时同步更新 ArtifactRef、FindingPage 等动态引导及相关示例，
不能留下“获得 ID 后恢复 Artifact 读表”的旧指引。RawSqlResult 不进入这次保存协议。

本次需要提升 Store generation。切换后旧 generation 的全部 Session、Artifact 和所属
Evidence/Findings 均不能由新版本 resume/read，不只是不能 persisted_frame；旧文件不删除或改写。
需要保留旧结果时，切换前用原版本导出，或保留兼容版本环境读取；新版本不提供双读/迁移。
这项破坏性后果与 Query/execute 退役、量名规则变化及不支持路线必须进入发布说明和切换验收。

### 11.3 公共导出与 Help 清单

| 变化 | __all__ / Help 合同 |
| --- | --- |
| 新增 | AnalysisFrame → analysis.analysis_frame；FrameAxis → analysis.frame_axis |
| 保留 | Artifact、Column、Population、Session、CalendarCorrespondence 及本版仍可用的工厂；原有 canonical Help 不改名 |
| 移除/不引入 | Query、Session.artifact、Artifact.to_pandas；不引入 ComparisonFrame/endpoint 或 Analysis/CoordinateFrame 别名；无受支持消费者的旧 corresponding/broadcast 入口及类型随路线退役，不留空壳 |
| 新 Session 方法 | analysis.session.frame、analysis.session.persisted_frame、analysis.session.population_from |
| 完整名称访问 | output_names、presence_names、axes 及 FrameAxis 元数据有独立 attribute Help，不执行 getter |

既有成员的行为变更不等于新增导出；具体内部协议和未完成 Statistic 包装不因本文记法自动成为公共导出。
固定 __all__ 快照覆盖新增、保留及删除项，FrameAxis 同样提供有界 repr/show 和类型 Help。
方法签名、返回类型及字段目标均通过 native registry 独立解析；CLI 与 Python 共享目标。
show 签名切换明确覆盖原 Query.show()→AnalysisFrame.show(*, n=20)，
Artifact.show(*, n=20)→Artifact.show() 仅元数据，数据预览/导出转至 frame；
axis(name)、coord(axis)、__getitem__ 的分工单独披露；top 保留 descending/NA 政策，延期 partition_by。
align/compare 的 Help 明确 on 可省略的两种形状、显式映射的完整性，以及 match 不自动选择；
population_from 的必填 on 单独说明，不将两个入口的参数规则混用。

实施时同步 current owning design/runtime、代数、签名与导出快照、native Help、错误修复、
CLI、latest EN/ZH 示例；对象 Help 为 analysis.analysis_frame，轴句柄与新增 Session 方法按现有 registry 规则提供独立目标。
若涉及 AGENTS 或 packaged skills，遵守其明确编辑授权要求。
本轮同步本提案、attribute 提案的依赖/来源边界及 README 提案索引；不改当前合同、示例站点、技能或实现。

## 12. 验收依据

- 同一固定输入在惰性、内存、持久路线保持值、完整键、presence、单位、时间和行序。
- 所有入口返回 AnalysisFrame；compare 与 align 共用坐标/presence 合同，只有生成值不同。
- 命名路径加前缀无冲突，select 后 presence 可访问但不混入 output_names；坐标通过 coord 读取。
- axis(name) 只做当前目录精确查找，缺失/重名明确失败，不导致跨输入自动匹配。
- align/compare 对零轴及双方唯一日历轴省略 on 与显式完整绑定产生相同结果和错误；惰性、内存、持久及冷恢复规则一致，无 Catalog/旧图依赖。
- 同名普通轴、日历加分类轴及不完整显式 on 均不自动补齐；跨 Session、日历不相容和配对轴限制不能被省略 on 绕过。
- 标量与日历序列等轴数不同的输入先报告结构限制，不提示补充 on；跨 Session、含配对轴分别给出专门修复。仅通过这些检查但不能自动推导的结构提示显式绑定，惰性、内存及冷恢复一致。
- 7/8 月每日比较显式 ordinal；同一 8 月上海/北京每日比较默认等值；两个城市整月标量比较不需 on=()。不按日期范围猜 match，不因失败改为 outer。
- actual/budget 不同名全轴 align 可完成比例；compare 检查全部量名与单位，遵守缺端点/NA、Duration 和零分母政策。
- 等值对应产生共享轴且可再组合；日历规则产生单层配对轴，含配对轴再次 align/compare 在构造时拒绝。
- builtin 同比闰日、月环比、ordinal 稀疏筛选保留原桶序号；完整目录存附件，冷恢复不加载模型或当前时区规则。
- 完整目录含未使用闰日或 ordinal 尾部时，实际行完全配对的 exact 成功；仍有未匹配实际行则失败，outer 保留该行且不生成目录中的空行。
- 月桶实际范围为 8 月 15 日至 9 月 1 日时，三种日历 match 在 exact/outer 下均拒绝；删掉该桶的结果行也不改变冻结目录的完整桶准入。
- Top 地区通过 population_from 转为显式来源过滤，无手抄键、无重排名；空域、Null 分类、复合身份和错误语义映射都覆盖。
- session.frame 支持一般 Population 的键域求值，不退化为单 Entity 属性读取；output_names 可为空。
- Column、FrameAxis、_Statistic 按精确 input_id 校验；row_count 在消费时绑定，不以 fingerprint 接受错输入。
- exact/outer、标量/空域、复合键和 presence 与数值 NA 的区别完整覆盖。
- select 不在附件保留已删除值，不恢复原输入域；reduce 删除全部行级 presence、保留分组轴所需桶映射，标量规约移除坐标附件。
- 同一月份配对下 A 两端有行、B 仅左端有行，规约掉地区只生成一个配对键；桶映射保留，presence_names 为空，物化/冷恢复后仍相同。
- 行均值 55 与原整体均值 1000/91、distinct 4 与 3、丢失城市 Top 等反例不能取得原量能力。
- 禁用模型与来源连接，冷恢复、预览、固定数据比较/规约和再 persist 均可执行；显式混合计划只读显式来源叶子。
- memory.to_pandas/show 零落盘，导出副本及 attrs 修改不影响内部数据。
- 待执行 show 只看元数据；frame.show(n=0) 不读行/附件或校验载荷，非法 n 在读取前拒绝。
- 内存/持久 frame 共用限行预览，不完整导出再截断；Artifact.show/findings/evidence_digest 不读取数据表。
- 仅 persisted_frame 按 ID 恢复；旧 session.artifact 与 Artifact 数据方法的 Help/动态指引全部切换。
- 非键排序结果的分片、物化、恢复与导出保持顺序；验证 SQL ORDER BY、NA/并列、finish 和 merge/concat，无顺序附件。
- 普通/复合/日历/单层配对 codec 无损，目录超集不增加事实行，缺目录和矛盾映射拒绝。
- 释放生产 Session/图/语义 scope 不影响物化数据；frame 通过原本地 runtime 发布，无重开 Session 路线或反向强缓存。
- saved.compute 后丢失旧文件，完整内存数据读取/派生/再保存有效，原存储读取报错。
- 审计只含继承来源事实和当前求值记录，载体转换原样保留，不记录复制边界、不累积 lineage。
- 待执行 persist 与 compute().persist 的求值和发布语义相同；纯存储根 persist 幂等，不复制提交。
- 求值失败无 Run，Run 只有发布语义；原求值时间可早于 admission；发布原子性和未知提交协调不依靠重算。
- 旧 generation Session/Artifact/Evidence 新版本拒读；非法目录/版本和参数类型明确报错，无假设的非 Frame kind 分支，旧字节不变。

本次仅做文档结构、链接、示例语法、数值反例和差异检查，不宣称实现验收通过。
实施从定向测试与 typing 开始，完整公开切换运行未缩小的 make check-agent；
相关 Runtime 验证按实际受影响来源路线执行，不把本地检查冒充 release 证据。
