# Marivo 语义层与 Analysis DSL 接口设计

Date: 2026-09-24

Status: proposed；目标契约尚未接受为公共 API，尚未实施或通过新 DSL 的端到端验收。

本文回答需要哪些业务语义对象、如何声明它们，以及分析者如何用 DSL 组合问题。
所有 Python 片段均为目标语法或验证草案，不是当前版本的可运行用法。

| 文档 | 唯一职责 |
| --- | --- |
| [语义层与 DSL 接口设计](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md) | 业务声明、公开对象、签名、方法语义及用户可见结果契约 |
| [DSL 实现架构设计](2026-09-24-marivo-analysis-dsl-architecture-design.md) | 基础能力、语义转换、执行放置、数据交换、物化及运行义务 |
| [MVP 实现验证](2026-09-24-marivo-analysis-dsl-mvp-validation-plan.md) | 首轮范围、接入位置、独立预期、验证矩阵、实施阶段和验收证据 |

理论依据：[分析组合的语义与代数契约](2026-09-23-analysis-algebra-theory.md)（v0.5）。
理论有限核心、完整目标接口与首轮实现切片范围不同；未纳入 MVP 不等于从目标语言删除。
本文档组重构不修改生产接口、AGENTS.md 或 packaged skills，也不授权公开接口切换与发布。

本文不以现有 Dataset 家族为设计起点，也不要求兼容其表面语法。现有能力的承接与冲突条件见第 10 节。
核心规则采用与扩展义务由[实现架构第 3 节](2026-09-24-marivo-analysis-dsl-architecture-design.md#3-基础能力与规则推导)统一说明。

## 1. 从什么问题推导设计

### 1.1 分析不是对列名执行运算

“八月客户收入下降了吗”至少包含六个决定：客户是谁，哪些客户进入问题，什么算收入，
收入发生在哪段时间，每行代表客户还是渠道，与什么范围配对比较。
只给出 `customer_id, month, revenue` 三列，无法恢复这些决定。

因此，一个可组合分析必须回答：

1. **谁**：业务身份是什么；本次目标成员是谁；一行是什么实例。
2. **量什么**：数值来自哪些贡献，按什么方法、单位和空值规则计算。
3. **如何对应**：事实、主体、分组、事件和参照之间如何映射；是否重复使用贡献。
4. **在哪个范围**：成员选择、观察、属性取值、事件随访分别使用什么时间与条件。
5. **接着做什么**：当前值、状态、主体映射和轨迹足以支持哪些下一步。
6. **凭什么成立**：哪些是业务声明，哪些由构造保证，哪些本次还需检查。

据此推导对象，而不是先增加一个通用 Relation 类，再尝试把业务放进去。

### 1.2 必须覆盖的分析场景

| 场景 | 必须表达的区别 | 对设计的要求 |
| --- | --- | --- |
| 收入、订单量、客单价及趋势 | 贡献单位与展示粒度不同；比率需要组件 | Metric 计算图；明确时间角色和目标域 |
| 多事实用户画像 | 订单、明细、访问各有粒度 | 各组件先归约到共同实例，避免明细相乘 |
| 用户平均客单价与总体客单价 | 等用户统计与按订单贡献计算不同 | 当前行统计与原量上卷分开 |
| 注册群体的后续消费 | 入群时间与消费时间不同 | 可复用、可固定的成员域 |
| 环比、同比、群组差异、变化的变化 | 有意改变的条件与必须保持的条件不同 | 显式比较设计与配对；差值可继续计算 |
| 按变化选人，再研究下一期 | 差值不是终点；选人会改变分析域 | 派生量、条件选择、主体量化、新观察可组合 |
| 渠道份额、渗透率、排名、Top-K | 计算域、参照域、展示域不同 | 固定参照输入；集合比例与数值比例分开 |
| 固定用户构成后的指标比较 | 实际总体与标准化总体不同 | 带单位和分层依据的参考权重 |
| 维度贡献与公式组件贡献 | 分项变化与分配方法不同 | target、basis、rule 各有身份 |
| 漏斗、完成耗时、相对留存 | 事件、旅程、锚点、主体不是同一单位 | 事件匹配、覆盖、量词与相对窗口 |
| 状态存量、积压、停留、迁移异常 | 区间、转移轨迹、完整生命周期不同 | StateModel 和有序执行；保留所需轨迹 |
| 异常、关联、预测、抽样不确定性 | 描述统计不自动成为推断或因果结论 | 方法扩展接入统一输入输出，独立声明假设 |

这些是长期表达目标，不是一次实现清单。前十一项仍各有方法准入条件；最后一项尤其需要
理论之外的统计方法规范，不能仅由“支持关系运算”推出已经支持。

## 2. 三层对象：业务定义、分析构造、分析结果

### 2.1 语义层定义稳定含义，分析层定义这一次的问题

| 层 | 拥有的内容 | 不属于这一层 |
| --- | --- | --- |
| Datasource | 连接、物理来源、schema、读取能力及来源证据 | 收入定义、用户身份、业务计量 |
| Semantic：定义环境 Γ | Entity、变量、Relationship、Metric、Event、StateModel | 本次选中的人、七月窗口、已执行的数值 |
| Analysis：表达式 | 成员域、量的观察绑定、配对、分组、归约、领域方法及显式依赖 | 临时改写业务口径、隐藏来源读取 |
| Analysis：结果值 | 实际成员、各量的值状态、保留部件、条件依据和输入绑定 | 凭 lineage 自动补读丢失信息 |

`Metric` 是可复用的贡献计算定义；`AnalysisRelation` 是某个分析域上的量及其完整续算契约。
“客户八月收入”“八月减七月”“下降客户的九月收入均值”是三个分析量，
不需要注册三个新的业务 Metric。

### 2.2 哪些概念值得成为公开对象

语义层保留六组业务对象：

- **Entity**：身份与表示。
- **Dimension / TimeDimension / Measure**：逐实例变量的三种角色。
- **Relationship**：具名的业务对应。
- **Metric**：贡献计算。
- **Event**：事件发生类型。
- **StateModel**：状态与转移规则。

另保留已有 PeriodCalendar / TemporalSet 等时间基础定义，用于认证期间、粒度和具名活动范围。
它们不替代上述业务对象，也不需要为同一日历再建立 Analysis 版本。

Analysis 的基础对象按代数中的职责命名：**AnalysisDomain（分析域）** 与
**AnalysisRelation（分析关系）**。名称区分“分析在哪些实例上成立”和“这些实例上承载什么量”，
不按结果最终存为集合、表格还是数组命名。

| 代数概念 | 设计名称 | 对象负责的含义 |
| --- | --- | --- |
| 实例类型 U、域定义 d；求值时得到 D | **AnalysisDomain** | 行单位、成员选择与输入绑定，以及构造所保留的坐标和映射 |
| 语义签名 `SRel[U,d,Σ;Π,Φ]`；求值时得到 `A=(D,x,P,E,α)` | **AnalysisRelation** | 域上的量定义和取值、保留部件、成立条件及实际依据、所有权与实现绑定 |
| 上述关系只有一个主量，`Σ={q}` | **NumericRelation / CategoryRelation / DurationRelation 等** | 同一 AnalysisRelation 家族的精确形状；主量通过固定 `.value` 操作 |

AnalysisDomain 不是裸集合 D。例如“某次选择的客户 × 四周”还包括实例身份、选择定义、
时间坐标与主体投影；尚未 execute 时，实际成员甚至还未求出。LogicalAnalysisDomain 持有
域定义与依赖，MaterializedAnalysisDomain 持有固定实现及必要映射，不能由一个 list of IDs 替代。

AnalysisRelation 也不是裸映射 `x_q:D→Cell[T]` 或一张数值表。相同的客户 AOV 显示值，
保留了订单 sum/count 的关系可能继续上卷；只保留值的关系不具有相同能力。
两者的区别属于关系契约，而不只是存储实现。Logical 关系承载具有签名的计算表达式；
Materialized 关系承载满足发布条件的实际 A。名称不暗示已经执行或全部条件已经实测。

因此取消 Dataset / Series 两层目标命名：观察、比较、筛选、统计直接返回相应的 Relation
具体类型。公共入口优先提供单量关系，用户不需要先建立多字段 schema，也无需先转成某个
通用容器才可继续计算。多量或领域输出仍可以有固定的具名视图，不要求共享同一组方法。
AnalysisRelation 是这一族对象的共同概念，不是一个接受任意字段和 join 的万能公共类。
下文在上下文明确时简称 Relation；这不是另一个导出类型或转换层。

### 2.3 域、量和关系不能互相代称

理论第 5 节区分量的定义 q、在域上的实际取值 x_q，以及完整分析关系 A；DSL 也保持这个区别：

- **域**回答“分析哪些实例、每行是谁”。
- **量定义**回答“值意味着什么”，包含单位、范围、方法与值政策；它属于 Σ。
- **分析关系**将域、量的定义和取值、保留部件与条件组织成可以继续运算的对象。

例如客户收入变化的定义是 Difference，当前取值可以是 `{u1:-40,u2:20}`；承载它们的
NumericRelation 还保留客户域、Cell 状态、必要映射和执行依赖；比较端点等额外部件由
§6.1 的 DSL 方法明确保留，不由理论 §6.7 的最小 Diff 规则自动提供。不能把整个关系
直接叫 Quantity，否则会重新混淆“量的含义”“量的取值”和“本次分析对象”三个层次。

| 名称 | 本设计中的处理 |
| --- | --- |
| AnalysisDomain | 保留数学上的 domain；Analysis 前缀区分语义层的业务命名空间 `ms.domain(...)` |
| AnalysisRelation | 对应理论的语义关系；区别于语义层声明业务对应的 Relationship |
| InstanceSet / Dataset | 不作为目标类型名；前者只强调实际成员，后者只强调数据容器 |
| Series | 不作为额外类型层；分析关系不天然有顺序，也不只有可见值 |
| Quantity / Variable | 可描述关系承载的量或变量，不代替完整关系对象 |
| Observation | 只描述一种量的产生方式；Difference、RowStatistic、Contribution 也需要同一关系体系 |
| Population / Cohort | 描述目标总体或资格群体等业务意图，不是所有分析域的名称 |

理论中的 `U / d / Ω / Σ / Π / Φ` 是共同推导语言，不逐个转成公共构造器。
窗口、分组、比较设计、权重和方法选择使用封闭参数值，不另建 Catalog 注册体系。
用户不提交任意 schema、metadata、proof 或 Python 映射回调来制造合法性。

## 3. 语义层应该怎样定义

### 定义方式：元数据声明，表达式写在装饰器函数体

沿用当前语义层的 Python authoring 方式。Entity、Relationship、状态集合等无计算体对象
使用声明构造器；Dimension、TimeDimension、Measure、Metric、Event 中的计算或过滤
使用对应装饰器的受限函数体，返回 Ibis 表达式。直接列 helper 仍可作为无计算的快捷声明。

装饰器参数只放对象身份、依赖、单位、时间角色、值政策等静态声明；不增加
`where=ms.eq(...)`、`expression=...`、`formula=...` 或分析谓词参数。
没有一套由作者维护的 `ms.sum/ms.ratio/ms.eq` 语义表达式语言。
Ibis 表达式由 loader 做受限语法、绑定和类型校验；装饰器 Metric 的业务契约来自显式声明，
不从函数体结构推导。显式 builder 的规范图仍由其构造参数形成。

函数体继续遵守当前 restricted-body 边界：单一 return，无 I/O、execute、循环、任意回调
或本地赋值；字段引用通过 `ms.bind(field_ref, entity_parameter)` 应用到直接 Entity 参数。
Ref 不可调用，`ms.bind` 不接受 Metric，也不接受任意派生表别名。
复杂表达式应通过合法字段定义复用，不为缩短示例偷偷开放 Python 执行能力。

下例均在 `sales` 业务域下 authoring；该域由项目的 `_domain.py` 统一声明。
装饰后的函数名绑定为对应 `Ref[K]`，不是留给 Analysis 调用的 Python 函数。

### 3.1 Entity：先定义身份，再绑定数据表示

Entity 必须回答“什么算同一个业务实例”。Customer、Order、Channel 都可以是 Entity；
Entity 不专指人，也不必等于一张事实表的每个物理行。

| 字段或契约 | 含义 |
| --- | --- |
| 稳定对象引用与定义版本 | 下游依赖业务定义，而不是 Python 文件路径 |
| `primary_key` | 业务身份 K；不同 Entity 的同值整数不是同一种身份 |
| source binding | 在受治理来源中怎样取得身份及属性 |
| versioning | 无版本、精确快照、有效期三个封闭变体 |
| representation key | 由 K 与版本坐标推导，不再要求作者给第二套业务键 |

客户每天一条快照仍是一个客户；`Customer × Snapshot` 是表示坐标。
分析的 `Customer × Week` 是另一个实例单位，不能把 Week 加进 Customer 身份来绕过建模。
事实存在有意义的重复时，应定义 OrderLine、PaymentOccurrence 等实例；来源违反键声明时，
不能自动添加行号使其“合法”。

```python
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource("warehouse")

Customer = ms.entity(
    name="customer",
    datasource=warehouse,
    source=md.table("customers"),
    primary_key=["customer_id"],
)
Order = ms.entity(
    name="order",
    datasource=warehouse,
    source=md.table("orders"),
    primary_key=["order_id"],
)
```

身份与版本唯一性是有所有者的来源声明，不意味着 Analysis 每次全表审计。
版本化来源必须明确属性取值与成员选择的时间；缺少精确快照不自动采用最新或最后已知值。

### 3.2 变量：值类型、业务角色和时间依赖都要明确

内部统一的变量契约是：`owner + value_type + unit + definition + temporal_dependency + value_policy`。
不新增一个要求业务作者同时注册的 `Variable` 对象。

| 角色 | 例子 | 合法用途及边界 |
| --- | --- | --- |
| Dimension | 客户地区、订单状态 | 分类、选择、分组；数字编码不会自动成为 Measure |
| TimeDimension | 支付时间、快照日期 | 窗口、顺序、时间坐标；声明解析、时区/日历及粒度 |
| Measure | 订单金额、访问时长、库存数量 | 有单位的逐实例数值；本身不指定所有聚合方式 |

```python
CustomerId = ms.dimension_column(
    name="customer_id", entity=Customer, column="customer_id"
)
OrderCustomerId = ms.dimension_column(
    name="order_customer_id", entity=Order, column="customer_id"
)
Region = ms.dimension_column(name="region", entity=Customer, column="region")
OrderStatus = ms.dimension_column(name="order_status", entity=Order, column="status")
OrderAmount = ms.measure_column(
    name="order_amount", entity=Order, column="amount", additivity=ms.additive_all(), unit="CNY"
)
PaidAt = ms.time_dimension_column(
    name="paid_at", entity=Order, column="paid_at", granularity="second"
)
```

复杂逐行定义仍使用受限制的 Ibis 表达式；SQL 只作为 provenance/parity 输入，不作为分析 DSL
或可执行语义定义的后门。直接列绑定和计算变量共享同一含义契约。

例如分类归一化和条件数值定义写成：

```python
@ms.dimension(entity=Customer)
def region_normalized(rows):
    return ms.bind(Region, rows).upper()


@ms.measure(entity=Order, additivity=ms.additive_all(), unit="CNY")
def paid_amount(rows):
    return (ms.bind(OrderStatus, rows) == "paid").ifelse(
        ms.bind(OrderAmount, rows), None
    )
```

paid_amount 在未支付行上为 NULL，并没有把该行从 Order 实例集删除；
需要排除贡献行的指标在自己的 Ibis 聚合中表达筛选。字段表达式与贡献选择不能混为一谈。

重新设计的关键变化是：**Measure 的单位和含义不足以授予任意聚合能力。** 金额可以按
获准贡献求和，库存不能因此跨任意时点相加。原量能否归约由计算方法、贡献关系、时间规则
和保留状态共同决定，不再让一个 `additive=True` 承担全部判断。

### 3.3 Relationship：定义对应，不定义“join 后都可以相加”

Relationship 声明端点、角色、键、预期基数、可缺失性与必要的时间选择。
基数声明必须与目标身份覆盖和版本选择相容；它不是调用者随时可注入的通过标志。

```python
Buyer = ms.relationship(
    name="buyer",
    from_entity=Order,
    to_entity=Customer,
    keys=[ms.join_on(OrderCustomerId, CustomerId)],
    cardinality="many_to_one",
    required=True,
)
```

`Buyer` 允许将订单映射给购买者，不代表 Customer 是 Order 的贡献单位。
一对多标签关系使用显式关系实例身份；经两个标签取到同一笔 100 元收入，不产生 200 元收入。
如需按标签分摊，计算必须选择已注册的分配方法，并绑定分配角色和权重。
平均分配、全额重复展示、精确去重是不同方法；不能以 `allow_fanout=True` 混在一起。

多条路径有不同业务角色时必须显式选择，例如 buyer 与 recipient。
单条路径的“可达”也不自动意味着历史属性能按所需时点解析。

### 3.4 Metric：带贡献语义的可复用计算图

Metric 定义的是“怎样从业务贡献得到量”，不是已经计算出的列，也不是绑定某个客户域的查询。

装饰器 Metric 的函数体只定义数值计算。Marivo 不解析其中的 sum、除法、过滤或字段组合，
来推导单位、可加性、贡献组件或上卷规则；这些语义通过装饰器接口由作者显式声明。
restricted-body 检查、依赖绑定、Ibis 表达式类型检查与编译继续存在，但不承担业务语义推断。

| 声明 | 含义与责任 |
| --- | --- |
| entities / contribution root | 依赖与原子贡献根；单根使用唯一 Entity，多根必须使用已有显式组件构造，不能把所有依赖都当贡献根 |
| time | 确切业务时间及其角色；精确状态时点不能当事件窗口 |
| unit | 必填的输出业务单位；不从函数体或 Measure 单位计算得出 |
| additivity | 必填的显示值求和规则；指定原生坐标、全部坐标及例外，或明确 non_additive |
| value policy | 作者按计算方法显式给出适用的输入 NULL、空贡献及零分母政策；必须有对应的已准入执行实现 |
| continuation | 由声明所选的已注册方法与实际保留部件确定，不凭函数体猜出隐藏组件 |

值政策通过小写构造器给出；三个参数分别接收不同的封闭类型，而不是可互换的字符串：

| 参数与类型 | 本文已定义的构造器 | 作用范围 |
| --- | --- | --- |
| `nulls: NullInputPolicy` | `ms.nulls.reject()`、`ms.nulls.ignore()` | 对实际进入计算的贡献值检查或忽略输入 NULL；被 `where` 排除的行不属于该次贡献 |
| `empty: EmptyContributionPolicy` | `ms.empty.zero()`、`ms.empty.null()` | 已确认完整且没有贡献的目标组；具体零或 Null 结果由方法与声明共同约束 |
| `zero_denominator: ZeroDenominatorPolicy` | `ms.zero_denominator.undefined()`、`ms.zero_denominator.error()` | 已准入的除法方法中分母为零时，分别产生 `Undefined(zero_denominator)` 或拒绝求值 |

这些构造器与 decorator 参数在 S1 W1 已可正式声明和加载，但不代表任意装饰器 body
都可进入 Analysis DSL 续算。构造器返回不可由作者直接构造的政策值；各方法只要求与其输入和计算有关的政策，
不存在跨方法的隐藏默认值；传入裸字符串或另一种政策类型在无 I/O 的声明解析阶段拒绝，
遗漏方法必需的政策在该方法的 DSL 准入阶段拒绝。未来增加取值须同时增加明确构造器和执行规则，不能放宽为任意 `str`。
持久化使用稳定规范代码，恢复时按所属政策类型解码并重新校验。

```python
@ms.metric(
    entities=[Order],
    time=PaidAt,
    unit="CNY",
    additivity=ms.additive_all(),
    nulls=ms.nulls.ignore(),
    empty=ms.empty.null(),
)
def revenue(rows):
    return ms.bind(OrderAmount, rows).sum(
        where=ms.bind(OrderStatus, rows) == "paid"
    )


@ms.metric(
    entities=[Order],
    time=PaidAt,
    unit="1",
    additivity=ms.additive_all(),
    empty=ms.empty.zero(),
)
def order_count(rows):
    return rows.count(where=ms.bind(OrderStatus, rows) == "paid")


@ms.metric(
    entities=[Order],
    time=PaidAt,
    unit="CNY",
    additivity=ms.non_additive(),
    nulls=ms.nulls.reject(),
    zero_denominator=ms.zero_denominator.undefined(),
)
def aov(rows):
    return (
        ms.bind(OrderAmount, rows).sum(
            where=ms.bind(OrderStatus, rows) == "paid"
        )
        / rows.count(where=ms.bind(OrderStatus, rows) == "paid")
    )
```

以上是待接受的接口草案，不代表当前 decorator 已支持这些参数。
作者对单位、可加性及表达式与声明的一致性负责；Marivo 信任获准的声明，不把它当成已验证的
数学定理，也不通过样本或函数体分析证明业务声明。缺失必填声明时拒绝，不自动猜测。
引用、坐标归属、类型和声明之间的已知矛盾仍须校验；实际归约还须满足路径、分区、时间、
Cell 和保留状态条件。错误声明可能导致业务结果错误，证据必须保留其作者声明来源。

`additive` 声明授权指定条件下的值求和；已注册的加法方法可将合法观察值作为状态保存并合并。
这不等于从函数体识别 sum，也不授权任意窗口或重叠贡献相加。`non_additive()` 只否定显示值
求和，不否定直接观察、比较或以当前行构造新的统计问题。
上例 aov 作为装饰器结果不自动保留分子、分母，因此不能仅凭除法表达式获得总体 AOV 上卷。
需要组件上卷时，使用已有显式 ratio/linear 等构造并引用组件 Metric Ref；该路径的组件与
计算关系由构造参数给出，继续按已注册方法生成规范图和状态，不反向解析装饰器函数体。
不新增任意 reducer 回调，也不允许将用户声明当作缺失组件状态的替代品。

构造器只封闭了政策的取值，不能证明作者选择了正确政策，也不能替代执行检查。
值政策必须可以独立履行。输出检查只能证明实际输出条件，不能证明函数体内部没有忽略 NULL。
如果直接执行的 body 不能在既有声明与受控执行协议下落实所选输入 NULL/零分母政策，
该组合拒绝准入；作者改用已支持的显式组件构造，而不是让解析器反推内部聚合或悄悄忽略政策。
上例装饰器 AOV 仅展示声明形状；若其直接除法不能可靠落实零分母政策，不得因为政策值有效
就接受该定义。零贡献与缺失坐标、来源不完整或被筛选掉的结果不同，不能统一补零。

订单计数的贡献单位是 Order，不因物理单位为 "1" 就能与客户计数互换。多根组件分别绑定
贡献、过滤、时间和路径，先归约再组合；不先 join 成宽表放大计数。组件构造保留真实状态，
Cell 由对应方法 finish 得到；同形状态或相同显示值不能证明组件、范围与方法身份相同。

#### 3.4.1 显式 builder 推导组合规则

`ms.ratio`、`ms.linear`、`ms.aggregate` 显式给出运算、输入及方法参数；Marivo 从这些参数、
输入契约和已注册方法推导输出，不要求作者重复声明可推导的 unit/additivity，也不允许
用输出声明覆盖推导结论。这与装饰器 Metric 的声明路径不同；不需要分析任何函数体。

| 构造 | 可推导的组合契约 | 仍需显式输入或本次检查 |
| --- | --- | --- |
| `ms.ratio` | 分子单位÷分母单位；两侧组件身份、状态需求及先分别合并再相除的上卷规则；通常不具备显示值可加性 | 零分母政策、组件域/时间相容性、贡献路径；转化率或份额的支持集包含关系不能从除法得到 |
| `ms.linear` | 系数与各项单位相容时的输出单位；组件状态及组合方法；在共同适用条件下的可加规则 | 系数及其单位语义、输入支持范围；本次分组须同时满足所有参与项的条件，不取许可并集 |
| `ms.aggregate` | 注册聚合方法决定输出单位、状态 schema、merge/finish 及可用续算；sum/mean 保留输入单位，count 为计数单位并保留贡献对象含义 | 贡献根、时间角色、输入缺值与空输入政策，以及分组的覆盖、重叠和数值条件 |

上述单位推导以受支持的单位运算为限：无量纲系数不改变单位，求和项须单位相容；未注册的
单位换算、币种转换或有量纲系数形式明确拒绝，不猜测转换规则。具体参数形状沿用获准的
builder 接口；本节不新增系数包装器或通用单位系统。

“可推导”包括得到“不支持此续算”的明确结论。某个 aggregate 方法若没有获准的合并状态，
就不能上卷；ratio 的某一组件若缺少对应粗化规则，也不能上卷整个 ratio。输入为装饰器
Metric 时，只消费其已声明契约与真实部件，不穿透函数体补出缺失状态。声明未知或方法
无推导规则时拒绝相应构造/能力，不能默认 additive，也不将比率值相加当成组件合并。

推导结果记录条件和依据，不是永久的 `is_additive=True`。输入业务事实仍由作者负责，
推导只证明在这些前提下组合规则成立；执行时由 Analysis 检查实际域、路径、分区、时间和
保留状态。此路径也不把作者声明升级为来源实测事实。

### 3.5 Event 与 StateModel：保留独立领域语义

Event 必须定义 occurrence 身份、发生时间、事件筛选和具名参与者角色。
同一用户发生三次支付就是三个 occurrence；参与者身份不能代替事件身份。
StateModel 定义主体、闭合状态集合、起源规则和合法转移；具体覆盖与重放窗口属于一次分析。

下面省略 Payment Entity 及其字段声明；PaymentId 是 occurrence 身份，PaymentTime 是
发生时间，PaymentStatus 是事件过滤使用的 Dimension。PaymentOrder / PaymentBuyer 分别为
到 Order / Customer 的获准单值路径。过滤表达式仍在 Event 装饰器函数体中：

```python
@ms.event(
    identity=(PaymentId,),
    occurred_at=PaymentTime,
    participants=(
        ms.participant(name="order", path=(PaymentOrder,), cardinality="one"),
        ms.participant(name="buyer", path=(PaymentBuyer,), cardinality="one"),
    ),
)
def paid(rows):
    return ms.bind(PaymentStatus, rows) == "succeeded"


Created = ms.ref.event("sales.created")
Cancelled = ms.ref.event("sales.cancelled")
PaidOrder = ms.participant_role(event=paid, name="order")
CreatedSubject = ms.participant_role(event=Created, name="order")
CancelledSubject = ms.participant_role(event=Cancelled, name="order")

created_state = ms.lifecycle_state(name="created", initial=True)
paid_state = ms.lifecycle_state(name="paid", terminal=True)
cancelled_state = ms.lifecycle_state(name="cancelled", terminal=True)

OrderLifecycle = ms.state_model(
    name="order_lifecycle",
    subject=Order,
    states=(created_state, paid_state, cancelled_state),
    transitions=(
        ms.inception(on=CreatedSubject),
        ms.transition(from_state=created_state, on=PaidOrder, to_state=paid_state),
        ms.transition(
            from_state=created_state, on=CancelledSubject, to_state=cancelled_state
        ),
    ),
)
```

Created / Cancelled 是已按相同方式声明的 Event；通过 Ref 复用，不导入其 authoring 函数。
Event 的 occurrence Entity 由身份、时间和字段 owner 一致解析，参与者身份不能替代它。
发生时间若需计算，应先定义带 Ibis 函数体的 TimeDimension，再交给 `occurred_at` 引用。
Event 函数体仍采用更窄的规则：`ms.all_rows()` 或通过 ms.bind 引用 Dimension 的比较谓词，
以 `& / | / ~` 组合；“使用 Ibis”不等于允许 Event 执行任意 Ibis 方法。
StateModel 的闭合状态/转移表是元数据，不因此改造成任意可执行回调。

同刻事件若影响终态，必须有业务顺序或注册的冲突规则。按事件 ID 稳定排序不证明业务先后。
participant 负责声明角色；participant_role 以 Event Ref 和声明名构造不可变角色句柄，
角色存在性、基数与主体一致性由加载后的定义验证。Ref 自身不加载这些事实。
StateModel 不声明 `complete=True`；“合法转移有哪些”和“本次历史是否足够”是两个问题。
事件匹配与状态重放输出规范旅程、区间和轨迹，再进入共同 DSL，不由通用关系运算猜测它们。

### 3.6 不新增哪些静态对象

Population、Sample、Comparison、Reference、ObservedMetric、Delta、Journey、Summary
均不因出现一次分析就进入 Catalog。稳定的业务定义可以被项目代码复用；一次分析的窗口、
群体和结果使用 Analysis 表达式或 Artifact 复用。

日历、工作时间、数值方法、分配方法仍由各自拥有者提供封闭定义，作为上述对象的组成参数。
不为每个数学名词增加一种顶层可注册对象，也不让用户实现任意 reducer/proof 插件绕过准入。

### 3.7 语义层与 Analysis 只通过 Ref 衔接

语义定义文件负责声明；分析脚本使用 `ms.ref.<kind>(path)` 引用已声明对象，不 import
项目里的 authoring 函数或定义模块。以下名称都属于同一个已加载项目的 Catalog：

| Analysis 需要的定义 | 唯一引用形状 | 接收位置 |
| --- | --- | --- |
| 客户等业务身份 | `ms.ref.entity("sales.customer")` → `Ref[EntityKind]` | session.members |
| 收入等可观察量 | `ms.ref.metric("sales.revenue")` → `Ref[MetricKind]` | AnalysisDomain.observe |
| 地区等分类属性 | `ms.ref.dimension("sales.customer.region")` → `Ref[DimensionKind]` | AnalysisDomain.read |
| 时间、数值属性 | ref.time_dimension / ref.measure → 对应 kind 的 Ref | AnalysisDomain.read 的精确重载 |
| 订单购买者关系 | `ms.ref.relationship("sales.buyer")` → `Ref[RelationshipKind]` | via / 路由 |
| 事件、状态规则 | ref.event / ref.state_model → 对应 kind 的 Ref | matcher / replay |

`Ref[K]` 是不可变的 kind + path 身份；构造 Ref 无 I/O，也不证明该对象存在、可执行或已就绪。
Session 在自己的已加载定义环境 Γ 中解析和固定定义绑定：不存在、kind 不符、字段 owner
或关系端点不符时给出结构化错误。物理类型与实际数据条件仍由执行层在获准范围内检查。
相同路径在不同项目里不自动是同一对象；分析结果继续遵守 Session 所有权。

CatalogEntry 是查看已加载定义的结果，不能直接作为 observe/read 参数；已有 entry 时显式
取 `.ref`。不接受裸字符串、定义对象或 `Ref | CatalogEntry | str` 联合入口。
也不让 `Revenue.numerator`、`Revenue(rows)` 或 `Revenue.resolve()` 给 Ref 增加解析行为。

Ref-only 限定对语义对象的引用，不限制 Analysis 表达式只能是 Ref。
observe 还接收已有 RuntimeMetricExpr；其中引用的语义叶子仍全部用 Ref。
二者属于分析期计算/方法绑定，不是另一种 Catalog 对象引用方式；完整接口见第 5.7 节。

本例将 Ref 别名写作 Customer / Revenue / Buyer，分析表达式写作 customers / aug / change。
这只是 Python 局部命名约定；API 依据 Ref 的 kind 和已解析定义工作，不依据变量名判断。

### 3.8 从代数反推：各语义对象需要补齐什么

需要补齐的是分析规则可消费的语义事实，而不是给所有对象加一个“支持组合”的开关。
已经存在的身份、Ref、版本和计算图继续由原对象拥有；下面列出目标职责及需要精化的部分：

| 对象 | 必须明确的契约 | 需要调整或扩展的重点 |
| --- | --- | --- |
| Entity | 业务身份、数据表示、版本坐标、来源声明 | 将稳定身份与表示粒度贯穿到贡献身份；快照坐标不能制造新的业务身份，也不自动授权重复累计同一存量。分析的 Customer × Week 不反向注册为 Entity |
| Dimension | owner、分类含义、取值表达式、时间依赖 | 为分组提供确定的取值映射；通过关系访问时保留路径和取值时点。已有层级映射可支持粗化，字段名或枚举值相同不能代替映射 |
| TimeDimension | 时间值解释、时区、日历、精度及采样事实 | 使用该时间轴的 Entity/Metric 明确其角色：事件发生、状态快照、有效区间、累计锚点等；角色不能由 date 类型或 default 时间字段猜出 |
| Measure | 逐实例数值的含义、单位、owner、表达式及时间依赖 | 用具体坐标和固定条件描述必要的计量约束，区分事件金额、时点存量、区间累计等支持含义；不再用三档标签代表所有归约许可 |
| Relationship | 端点、具名角色、键映射和版本解析 | 向分析提供有依据的单值/可缺失对应。贡献复制、分配与去重属于具体计量路径和方法，不能由 join 关系自动授予 |
| Metric | 贡献根、筛选、业务时间、计算图和数值政策 | 装饰器以作者声明提供单位、可加性和方法政策；显式组件构造提供规范图，函数体不参与语义推断 |
| Event | occurrence 身份、发生时间、受限谓词、参与者角色 | 保留领域定义，向匹配结果提供准确的实例与主体映射；事件声明不证明来源覆盖 |
| StateModel | 主体、状态、起源、合法转移 | 保留规则定义；由执行方法说明区间、轨迹、边界状态的输出与续算要求，不能从静态模型推断本次历史完整 |

来源身份、时区、单位等业务事实必须由作者或既有受治理定义提供；Ibis、物理 schema 和样本
不能独自推导这些事实。反过来，已经可以从 Entity 键、表达式和获准路径推导的内容不再让
作者重复填写。声明、推导事实和本次实际数据检查保留不同依据，不新增全源预检。
时间观测是否成为贡献由具体方法决定；合法的期间均值或时间加权计算可以消费快照观测，
不能因其不改变 Entity 身份就一概禁止这些计算。

Measure 与 Metric 不承担同一种责任：Measure 定义逐实例变量及其业务约束，Metric 选择
怎样消费贡献并得到量。同一个订单金额 Measure 可以用于收入之和、订单均额或最大订单金额；
这三个 Metric 不会因为来自同一 Measure 就拥有相同上卷规则。

### 3.9 可加性必须精确到坐标、贡献与方法

**应超越 additive / non_additive / semi_additive，但不能只改成可加维度白名单。**
在这个代数里，判断对象是“当前量沿某个映射怎样归约”，而不是 Metric 永久属于哪一档。
必须区分三件事：

| 问题 | 所需事实 |
| --- | --- |
| 显示值能否直接相加，并保持原量含义 | 获准的贡献分区、固定条件，以及该计量的值加法规律 |
| 原量能否用状态上卷 | 各组件合并方法、贡献覆盖/重叠许可、计算顺序及当前保留状态 |
| 能否对当前行另外求和或均值 | 明确当前行作为新统计单位、值政策与权重；输出是新的 RowStatistic |

对有充分状态的量，理论第 6.5 节和 L8 给出的上卷形态是：

$$
q_{\mathrm{coarse}}(k)
=\operatorname{finish}\!\left(\bigoplus_{u:g(u)=k}S(u)\right)
$$

这里 g 是本次的粗化映射，S 是绑定原贡献含义的状态。它成立仍需方法、贡献、时间、覆盖及
状态前提；不自动等于对 q(u) 求和。只有在允许状态上还满足
`finish(s₁ ⊕ s₂) = finish(s₁) + finish(s₂)` 等条件，才能直接相加显示值。
均值和比率通常需要先合并组件再 finish；精确去重在数学上可以合并身份集合，但不能相加
重叠集合的计数。目标接口首次接入 `count_distinct` 与分位数时不要求来源暴露这类可上卷状态，
因此两者的观察结果都不提供 `rollup()`；需要更粗粒度时重新对目标域发起观察。

#### 3.9.1 一个 additivity 参数，支持指定维度与全部维度

用熟悉的 `additivity` 表达计量可加性，取值改为有明确含义的封闭元数据对象。
可加声明使用两种构造，另用 `ms.non_additive()` 明确不可对显示值求和；不分别填写 `sum_across/hold_fixed`：

| 写法 | 业务含义 |
| --- | --- |
| `ms.additive(over=(WarehouseKey, SkuKey))` | 只允许合并指定的原生计量坐标，其余原生坐标在每个汇总组内保持一致 |
| `ms.additive_all()` | 允许合并此定义的全部原生计量坐标，仍须满足贡献、时间和方法条件 |
| `ms.additive_all(except_=(SnapshotAt,))` | 除指定例外外，允许合并全部原生计量坐标；例外坐标在每个汇总组内保持一致 |

`ms.additive` 的 over 必须为非空的 Dimension / TimeDimension Ref 元组；不以无参数调用
隐式表达全部。`ms.additive_all` 的 except_ 默认空元组。两种构造返回不同的封闭规则变体，
不提供同时可填 over/except_、可能彼此冲突的通用配置类。except_ 使用后缀是因为 except
是 Python 关键字。不再增加 additive_except 等同义入口。

这些规则**附着在 Measure 或装饰器 Metric 定义上**，不注册新的语义对象，也不承载过滤或计算表达式。
它们描述“扩展支持范围仍保持此计量含义”的业务许可和限制，不指定默认聚合，
也不直接授予任意结果 rollup 能力。装饰器 Metric 必须独立声明，不自动继承 Measure 的规则；
显式组件构造按其注册方法组合已声明的条件。

“全部”的边界必须明确：

- 范围是当前 Measure 或 Metric 定义版本所声明贡献支持的原生计量坐标，由所属 Entity 的身份/表示、量的支持结构及
  时间依赖解析；不是整个 Catalog，也不是所有通过 join 可见的字段，不要求作者再列一次全集。
- 全部包括其中的时间坐标，不偷偷解释为“全部非时间维度”。事件时间仍要求贡献分区合法；
  库存等必须固定的状态时点需要显式排除，矛盾的 additive_all 声明应拒绝，不能覆盖版本语义。
- 例外表示每个输出组内固定，允许结果中同时存在多个时点；禁止把不同时点合进同一个库存总量。
  隐藏列、先做另一层分组或物化，均不能移除这个约束。
- 规则解析绑定到当前定义版本；新增原生坐标需要重新验证，不能在执行或冷恢复时重新扫描
  Catalog 扩大旧结果的权限。引用不存在、非原生或 owner 不相容的坐标应拒绝。
- 维度是否形成互斥贡献分区仍由路径、支持和本次范围判断。all 不允许重复计算标签、人群或
  重叠时间窗口中的相同贡献，也不替代已经定义的去重/守恒分配方法。

未在显式列表中出现的展示维度不能仅凭名称判定：例如地区是仓库的属性，应解析其到原生
坐标及贡献的映射。`additive(over=...)` 对未列出的原生坐标采取固定约束；`additive_all`
则明确承诺全部当前原生坐标，except_ 中的例外除外。两者的承诺范围不同。
装饰器 Metric 必须显式填写 additivity，缺失时 authoring 拒绝；其他未获规则的定义也不能默认为 additive_all。
原生支持约束既用于检查 Ibis 聚合首次消费的输入，也用于后续归约，不能只在 rollup 时检查。
超出原量支持规则的当前行统计必须产生独立的统计量定义，不能借直接执行或 opaque 路径
继续声称保持原计量含义；这些约束也不是对一切新统计问题的全局禁令。

例如 StockCell 已声明业务身份为 warehouse_id × sku_id，使用 SnapshotAt 作快照版本坐标。
仓库和 SKU 都是该身份上的原生计量坐标，由已有定义解析，不需要在这里重列为第二套主键。
假定所有库存值都按同一种 item 单位计量，且库存单元代表互斥的物理持有：

```python
StockCell = ms.ref.entity("inventory.stock_cell")
SnapshotAt = ms.ref.time_dimension("inventory.stock_cell.snapshot_at")


@ms.measure(
    entity=StockCell,
    unit="{item}",
    additivity=ms.additive_all(except_=(SnapshotAt,)),
)
def on_hand(rows):
    return rows.on_hand


@ms.metric(
    entities=[StockCell],
    time=SnapshotAt,
    unit="{item}",
    additivity=ms.additive_all(except_=(SnapshotAt,)),
)
def inventory(rows):
    return ms.bind(on_hand, rows).sum()
```

`ms.additive` / `ms.additive_all` 已在 S1 W1 成为可加载的 additivity 参数值；
它们替代原 sum_across/hold_fixed 草案，不保留两套入口。
作者仍只在函数体中用 Ibis 定义数值和计算；元数据说明这项求和的业务适用范围。
Metric 的 SnapshotAt 绑定与 Measure、Entity 的状态时点一致，不能解释为事件发生时间。
这个 Metric 没有声明期间折叠；观察应绑定精确时点，不能把期间内所有快照都拿来相加。

本例原生计量坐标是仓库、SKU 和快照时间；声明表示同一 SnapshotAt 下，按获准分区
合并其他坐标上的库存单元。其他项目即使有更多空间计量坐标，也不必在调用处逐一列举。
例如两个仓库在 t₁ 为 10/20、在 t₂ 为 12/18，总库存分别是 30/30；四个值之和 60
不是任何一个时点的库存。按仓库所属地区汇总可沿已有单值映射推导，不必把每个地区层级
重复写进 over。按重叠商品标签展示则另需处理贡献重叠，不能继承仓库分区的许可。

期末库存是指定边界时点的状态；期间平均库存是另一种有时间权重和覆盖规则的计算。
`last` 也必须说明取值范围与边界：期间最后一条已观测记录不能自动冒充精确期末状态。
省去三档标签，不能同时省去原先 over/fold 所表达的真实时间轴和方法；这些内容应进入
明确的时间依赖及 Metric 计算契约。普通数值求和规则也不能代替有序折叠或时间加权方法。

#### 3.9.2 Metric 声明与组件方法形成带前提的规则

Metric 契约必须能回答以下内容：装饰器路径读取作者声明，显式组件路径读取构造参数与
注册方法。不会解析 body 来补齐缺失事实；也不要求作者手填 lift / merge / finish 或提供任意回调：

| 规则组成 | 需要记录什么 |
| --- | --- |
| 作用对象 | 哪个组件出现位置、哪个贡献根、哪个原量；值求和还是状态上卷 |
| 坐标变化 | 哪些原生坐标可以粗化，哪些条件必须固定；时间使用哪个确切 Ref 与角色 |
| 方法与顺序 | sum、sum/count、ratio 组件合并、有序折叠等；空间/时间运算的先后 |
| 成立前提 | 单值映射、互斥或守恒分配、覆盖、时间对齐、权重和单位条件 |
| 保留要求 | 组件状态、身份集合、分布、时间对齐状态或边界状态及其版本 |
| 输出意义 | 保持原计量，还是产生明确的新统计量；NULL、空贡献、零分母等政策 |

收入由订单金额之和产生：若每笔订单按规定时点唯一归属一个地区，地区组可构成订单分区；
绑定 PaidAt 后，互不重叠的月份窗口也可以形成贡献分区。重叠窗口、当前地区与历史地区的
不同映射不能因为使用了同一个 Ref 就视为同一分区。一笔 100 元订单进入两个标签，两个标签
各展示 100，并不允许上卷得到总体收入 200；需要已定义的去重或守恒分配方法及充分状态。

显式组件构造的 AOV 值不满足上述加法律，但其收入、订单数状态可以各自按获准规则合并再相除。
仅有上述装饰器 body 的 AOV 没有这些组件状态，不能承诺相同上卷。
两客户分别有 `(收入=100,订单数=100)` 与 `(100,1)`，总体 AOV 是 200/101，
不是 AOV 值之和 101，也不是等客户均值 50.5。最后一种仍可由 summarize 明确产生。

时间折叠还会改变空间归约能力。例如两台设备在两个对齐时点的带宽是 `[10,0]`、`[0,10]`：
各自峰值都是 10，总带宽峰值却是 10，不是 20。因而“设备维度可加、时间维度不可加”仍不准确；
设备轴可在时间折叠之前求和，折叠后的峰值不再拥有相同许可。
保留两台设备各自的分布也不够：还需要时间对齐信息来区分同步峰值与错峰。

所以规则必须依赖计算顺序和当前保留坐标/部件；不能把 Measure 的 additivity 规则原样传播给
所有由它计算出来的 Metric，更不能原样传播给 compare、summarize 等生成的量。

#### 3.9.3 Analysis 判断这一次变换能否成立

| 层次 | 唯一职责 |
| --- | --- |
| 作者声明 | 身份、单位、量的业务支持、确切时间角色、必要坐标约束与业务计算式 |
| 语义声明解析与组件构造 | 校验并绑定作者声明；显式组件按注册方法形成图与状态要求，不从装饰器 Ibis body 推断规则 |
| 本次 Analysis | 将规则绑定到实际分析域、路径、分组、时间和保留部件，履行对应条件 |

Analysis 消费 Metric Ref 及解析后的规则；不通过 `Revenue.additive` 访问裸 Ref 的内部信息。
Catalog 说明“此定义有哪些带前提的归约规则”，结果 contract 说明“当前关系为何能或不能
进行这个变换”。同一个 Metric 的两个结果可能因为保留状态、所选域或此前折叠不同而具有
不同续算能力。全部操作仍 Lazy；依赖实际数据的获准检查到 execute 时履行。

三档 additivity 如需继续展示，只能是附带适用范围的派生摘要，不能作为执行准入的依据。

### 3.10 与当前语义实现的实际增量

当前不是只有三个枚举而没有其他信息。SemiAdditive 已保存具体 over 时间轴和 fold；
规范 Metric 图已有 aggregate、weighted mean、ratio、linear、cumulative 等节点，以及
组件出现位置、贡献根、单位、值政策、计算顺序和 required state。Analysis 已有逐轴分区与
保留状态的归约检查。本轮代码阅读确认这些结构存在，不代表重新完成所有后端验收。

此次提案的主要增量是：

1. 将笼统 additivity authoring 改为明确的业务时间和必要坐标约束，保留已有 over/fold 的有效含义。
2. 装饰器 Metric 保持受控的 body 执行边界，单位、可加性等规则改由必填声明提供；
   不提取内部计算图。已有显式 builders 继续生成规范图，二者接入同一语义契约。
3. 用统一的、有前提的归约契约连接语义定义和 AnalysisRelation 的实际部件、域与坐标，
   同时分别披露显示值求和、状态上卷和当前行统计。
4. 实施时同步接受新的签名、值状态和错误边界。第 3.3 节 Relationship 的 cardinality/required
   也是拟议字段，当前构造器不接受；这些声明不得取代键覆盖、时间解析与本次映射的判断。

不新增一套平行计算图、全局维度白名单或手写能力库存；已有对象继续拥有各自事实。

## 4. DSL 的入口：先看一条完整分析

### 4.1 常用链只操作成员与一个量

接口采用**单量优先**：观察直接返回 NumericRelation 等具体分析关系，比较返回承载差值的
NumericRelation；筛选、分组、统计始终作用于这个明确的主量。它们都是第 2 节 AnalysisRelation
的精确形状，携带完整域、定义、必要部件与条件，不需要 Dataset 或 Series 中间层。

```python
import marivo.analysis as mv
import marivo.semantic as ms

Customer = ms.ref.entity("sales.customer")
Revenue = ms.ref.metric("sales.revenue")
Buyer = ms.ref.relationship("sales.buyer")

session = mv.session.get_or_create("customer-change", report_timezone="Asia/Shanghai")

july = mv.time_scope(start="2026-07-01", end="2026-08-01")
august = mv.time_scope(start="2026-08-01", end="2026-09-01")
september = mv.time_scope(start="2026-09-01", end="2026-10-01")

customers = session.members(Customer)
jul = customers.observe(Revenue, during=july, via=Buyer)
aug = customers.observe(Revenue, during=august, via=Buyer)

change = aug.compare(jul)
declining_change = change.where(change.value.lt(0))
decliners = declining_change.members()
sep = decliners.observe(Revenue, during=september, via=Buyer)
result = sep.summarize(mv.mean()).execute()
```

前面各行只定义目标客户、两期观察、变化、下降客户与九月统计的依赖图，不读取业务来源。
最后一次 `.execute()` 才启动整条依赖图的实际求值与结果发布；不要求用户先执行成员、
观察或筛选结果。复用同一个 customers 节点，使两期观察共享本次执行中的成员实现，
而不是要求提前生成客户 Artifact。运行内共享与跨次执行固定的区别见第 9.1 节。
这条主线不需要学习输出字段字典、`mv.value`、两侧字段配对、字符串主体角色或机会域参数。
必要语义由具体接收者及方法契约携带；不能确定时拒绝，不靠猜测使调用变短。
此处 session 已绑定项目与已加载定义；上面的 Ref 构造不触发加载、业务来源读取或执行。

### 4.2 一种意图只有一个常用入口

| 用户的问题 | 规范入口 | 返回什么 |
| --- | --- | --- |
| 研究哪些业务实例 | `session.members(entity_ref)` | Logical AnalysisDomain |
| 这些实例上的指标是多少 | `members.observe(metric, during=..., via=...)` | Logical NumericRelation；metric 为 Ref 或闭合分析期表达式 |
| 这些实例的属性是什么 | `members.read(field_ref, at=...)` | 对应值种类的 Logical Relation |
| 哪些当前实例满足条件 | `relation.where(relation.value.lt(...))` | 同量、子域上的 Logical Relation |
| 相对基线改变多少 | `current.compare(baseline)` | Difference 的 Logical NumericRelation |
| 每组当前值的统计 | `relation.group_by(category).summarize(mv.mean())` | 新统计量 Relation |
| 合并原指标的贡献 | `relation.group_by(category).rollup()` | 保持原计量含义的 Relation |
| 这些行对应哪些主体 | `relation.members(through=...)` | 去重后的 Logical AnalysisDomain |
| 执行完整分析并取得结果 | `logical.execute()` | 对应 Materialized 值 |

不分组的 summarize/rollup 都归约到 Singleton；结果仍是 Singleton 域上的分析关系，
不是脱离定义和状态的 Python 标量。分组后两者使用同一分组描述。
不提供并行的 `.mean()`、`.aggregate(agg="mean")`、`.values()` 等同义入口。
`.value` 是唯一主值字段，不是新的提取/解锁操作；展示名不参与字段寻址。

补齐表达能力遵循一个约束：**已有 API 或已确定的组合能表达，就补契约与例子，不增加重复入口。**
保留 runtime_metric、rank、discover、correlate、forecast、事件/状态方法中仍合适的操作名；
需要改变的参数只承载现有输入无法表达的语义。理论里的映射、basis、rule 和证明义务优先作为
内部推导，不要求用户重复提供可以从显式输入唯一确定的事实。一个旧入口被新的组合替代时，
在第 10 节说明对应关系；不同时发布两条同义路径。

### 4.3 公开形状分工明确

| 形状 | 负责什么 | 不能据此推断 |
| --- | --- | --- |
| AnalysisDomain | 实例身份、成员定义、获准映射；可表达成员、分组、时间乘积等域 | 同 Entity 就是同一批成员 |
| NumericRelation | 一个数值量及其域、定义和状态 | 单位相容、可比较、可上卷 |
| CategoryRelation | 一个分类量及其域 | 数字类别可以拿来求均值 |
| DurationRelation / TimeRelation / BooleanRelation | 时长、时间值、命题值各自的操作族 | 三者可以互换为普通数字 |
| GroupedRelation | 归约前的分组描述，只提供 summarize/rollup | 已完成计算或存在一份新的数据 |
| 领域具名结果 | 匹配、状态等方法的固定字段和角色，例如完成旅程的 duration | 任意字符串列都具有同样角色 |

用户通常无需手写这些类型或构造类；工厂和方法返回精确形状，IDE/Help 只展示对应接口。
量的 Metric / Difference / RowStatistic 等定义仍在内部 Σ 中，不为每种嵌套表达式增加结果类。
一个 Difference 可以再比较，一段 duration 可以统计；但数值量不能充当分类字段来分组。

AnalysisDomain 的集合操作只接受 AnalysisDomain；Relation 的 set union 不在公共语言中，避免隐式解决同键量冲突。
Entity 域的身份、分组域的成员到组绑定、时间乘积的坐标身份均不能丢失。
域的构造、身份和保留映射由 `.contract()` 披露；不另设与 `.members()` 含义重叠的域提取入口。

内部仍是 `SRel[U, d, Sigma; Parts, Conditions]`。单量形态没有删除 Cell 的
Defined / Null / Undefined / Unknown 区别；缺行仍是域问题，四分支也不是四值逻辑。
类型化字段表示计算表达式，不能把它当已经读取的 Python number/bool。

### 4.4 字段、时间轴与角色使用有类型的句柄

常用量只有固定的 `.value`。它返回绑定当前 Relation 的字段：

- NumericField 提供 `lt/lte/gt/gte/eq`，参数是明确的数值常量；CategoryField 提供分类相等/集合选择。
- DurationField 接收 Duration 值，不能接受无单位数冒充秒；TimeField 使用对应日期/时点类型。
- 各字段的判断都返回 BoundPredicate，携带来源字段、域和显式依赖；条件通过
  `mv.all_of/any_of/not_` 组合，不使用 Python `and/or` 或隐式布尔求值。
- `is_defined()` 显式选择当前有定义的值；它不消除来源完整性义务。

数值常量按接收量的声明单位解释，`revenue.value.lt(100)` 表示 100 个该量单位，不自动换汇。
拒绝把 bool 当作数字阈值。where 允许条件来自另一个同域 Relation：条件源作为显式输入依赖，
不要求与被筛选量拥有同一个字段 owner，也不能借字段同名建立对应。允许的域关系是同一个
显式 Logical 域节点（本次执行共享实现）、同一 Materialized 域，或已有包含映射能为接收者
每行取得唯一条件值；缺少对应的接收者行必须拒绝，不能自动 inner join。
all_of/any_of 的操作数必须绑定同一个机会域；不自动挑交集、并集或某一个较小域。
条件组合后的 where 可以沿已有子域映射取值。跨量运算同样接收明确输入并检查对应规则。
普通值比较与显式 Cell 状态判断使用各自的封闭值政策；where 的严格消费和复合条件规则见第 6.2 节。
分类、布尔、时间及领域状态谓词继续保留，不能因理论第 6.7 节仅选取数值谓词而删去这些方法。

时间网格使用 `weeks.window` 这样的绑定句柄，不使用 `period_window("week")` 查找字符串。
主体角色使用由领域结果产生的 SubjectBinding；多根路由显式引用贡献 Entity 和 Relationship。
字符串保留给展示标签、业务常量和静态声明名称；有限策略可采用 `Literal` 或类型化构造器，
不以“消除所有字符串”为目标，也不允许把任意字符串冒充字段、域或角色引用。
有限选项必须在目标签名中写出封闭类型，并在构造或声明解析时校验；Python 类型注解本身
不执行运行时检查。高风险且容易跨语义场景混用的值政策使用 §3.4 的独立政策类型，
简单选择沿用 `Literal` 加运行时校验，不为每个取值新增同义工厂。例如：

| 参数 | 目标封闭类型 | 除取值外仍须检查 |
| --- | --- | --- |
| `time_dimension_column(granularity=...)` | `Literal["year", "quarter", "month", "week", "day", "hour", "minute", "second"]` | 声明的时间类型及实际粒度能力 |
| `relationship(cardinality=...)` | `Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]` | 键、版本选择、来源基数和本次映射 |
| `participant(cardinality=...)` | `Literal["one", "optional_one"]` | exact 参与者身份与实际覆盖 |
| `aggregate(agg=...)` | 封闭 AggKind（包含显式近似变体） | 数据源 SQL 能否履行该定义，数值精度限制须披露 |
| `every_start(completion_assignment=...)` | `Literal["exclusive", "shared"]` | 最终 occurrence 的实际分配与覆盖 |

比较的 `value`、`UnionKeys.missing`、排名的 `order/ties`、归因的 `mode`、相关的 `method`
已在各自方法处给出封闭选项；`agg/fold` 继续使用既有封闭联合类型。无论何种类型，
都要拒绝拼错的动态输入，不能由默认值或静默回退吞掉。`unit`、时区、业务分类值、
声明名和局部步骤名不是全局有限策略：分别按单位、时区、值域或引用/唯一性规则校验。

### 4.5 类型安全有三道明确边界

| 层次 | 必须检查 | 不承诺证明 |
| --- | --- | --- |
| Python 静态类型 | Metric/Dimension 角色，Numeric/Category/Duration 方法族，参数封闭变体，Logical/Materialized 接口 | 动态 Entity 的精确身份、币种、实际域相等 |
| 无 I/O 构造期 | owner/session、量定义与单位、已知域绑定、比较意图、主体映射、RequiredParts；登记可履行义务 | 尚未读取的实际缺键、数值和来源条件 |
| 执行与发布 | 对获准范围检查实际配对、值状态、数值及本次强制义务，再原子发布 | 全部来源数据真实正确或统计/因果假设成立 |

不使用 `Any`、`__getattr__` 或动态字典假装获得静态字段类型；也不要求每个中间结果先写 schema class。
Python 不能从 `ms.entity(name="customer")` 为每次调用生成一个新 nominal type，
不能从字符串 `unit="CNY"` 自动得到币种类型。实际身份和单位由构造期检查承担，必须诚实披露。

生产签名采用不可直接构造的公共协议，以及由工厂产生的封闭 Logical/Materialized 变体。
NumericRelation 是两种状态共享的计算协议；`.execute()` 只在 Logical 上，读取结果的
`.show()`/`.to_pandas()` 只在 Materialized 上，`.contract()` 可检查两种状态。
用户不用填写生命周期泛型。Metric Ref 的 kind，以及 RuntimeMetricExpr 的
形状静态可知；此 observe 契约只接纳获准的数值计算，解析时仍须核对数值准入。
Ref 本身不携带数值子类型；具体 int/Decimal/float
表示、精度和物理 NULL 由声明/执行解析的类型事实确定。
DurationRelation 的 mean/rollup 保持 Duration，count 返回 NumericRelation；值种类改变必须体现在
具体方法返回类型中，不以一个万能 Relation[Any] 统一。数值分桶明确产生 CategoryRelation 才能分组。

以下是可由 Python 3.10 typing 表达的签名切片；省略的是其他封闭方法，不是隐藏的 `Any`：

```text
Session.members(Ref[EntityKind], *, at: Instant | BeforeEnd | None = None)
    -> LogicalAnalysisDomain
MetricInput = Ref[MetricKind] | RuntimeMetricExpr
AnalysisDomain.observe(MetricInput, *, during: TimeScope | PeriodWindow | None = None,
    via: Ref[RelationshipKind] | RootRoutes,
    coordinates: tuple[Ref[DimensionKind], ...] = (),
    time_dimension: Ref[TimeDimensionKind] | None = None)
    -> LogicalNumericRelation
AnalysisDomain.observe(MetricInput, *, at: Instant | PeriodEndpoint,
    via: Ref[RelationshipKind] | RootRoutes)
    -> LogicalNumericRelation
AnalysisDomain.read(Ref[DimensionKind]) -> LogicalCategoryRelation
AnalysisDomain.read(Ref[MeasureKind]) -> LogicalNumericRelation
AnalysisDomain.read(Ref[TimeDimensionKind]) -> LogicalTimeRelation
DomainGroupKey = CategoryRelation | Ref[DimensionKind]
AnalysisDomain.group_by(*keys: DomainGroupKey, groups: AnalysisDomain | None = None)
    -> GroupedAnalysisDomain
GroupedAnalysisDomain.observe(MetricInput, *, during: TimeScope,
    via: Ref[RelationshipKind] | RootRoutes)
    -> LogicalNumericRelation

NumericRelation.value -> NumericField
NumericField.lt(Number) -> BoundPredicate
CategoryField.eq(CategoryValue) -> BoundPredicate
NumericRelation.where(BoundPredicate) -> LogicalNumericRelation
CategoryRelation.where(BoundPredicate) -> LogicalCategoryRelation
NumericRelation.compare(NumericRelation, *, design: ComparisonDesign = TimeChange(),
    value: Literal["difference", "relative_change"] = "difference")
    -> LogicalNumericRelation
NumericRelation.summarize(Mean | Sum | Count) -> LogicalNumericRelation
GroupKey = CategoryRelation | Ref[EntityKind] | Ref[DimensionKind] | TimeGrid | Grain
NumericRelation.group_by(*keys: GroupKey, groups: AnalysisDomain | None = None)
    -> GroupedNumericRelation
GroupedNumericRelation.summarize(Mean | Sum | Count) -> LogicalNumericRelation
NumericRelation.rollup() -> LogicalNumericRelation
NumericRelation.members() -> LogicalAnalysisDomain

LogicalNumericRelation.execute() -> MaterializedNumericRelation
MaterializedNumericRelation.show() -> None
```

`Number` 是封闭的 int/float/Decimal 数值集合，不是任意 object；bool 在构造期排除。
MetricInput 是便于描述的内部类型别名，不增加一个用户必须构造的包装对象或顶层导出。
DomainGroupKey 与 GroupKey 同样是闭合签名别名。成员域的 Dimension Ref 绑定其自身属性，
规则见 §5.3；Relation 的 Ref/TimeGrid/Grain 仅选择已有坐标或粗化，不能隐式读属性。
RootRoutes 沿用第 5.2 节的路由形状；直接身份映射或定义已明确绑定唯一路径时有省略 via 的重载。
Ref 的泛型参数是已有对象 kind；不发明 NumericMetricRef / CategoryDimensionRef 包装层。
这里 Dimension 按分类角色返回 CategoryRelation，即使其物理值是整数；Measure 表达数值角色。
`during` 的 PeriodWindow 绑定时间网格中的逐格窗口；它与固定 TimeScope 不混同。
`at` 绑定状态时点或累计计算的明确端点，`during` 绑定区间；两种重载互斥。
解析 Metric 的时间角色后选择其唯一规则，不能将窗口自动解释为 last，也不能将时点当作事件窗口。
相对锚点窗口仍处于第 8.2 节的独立设计边界，不混入本节已明确的重载。
不能把它们放入一个所有字段可选的 Context 类。
上卷状态或主体映射可能在运行中裁剪，因此 `NumericRelation` 上存在 rollup/members 方法
不保证每个实例可调用成功；所需信息缺失必须在最早可确定的构造阶段拒绝。

## 5. 观察、分组与两种归约

### 5.1 成员、指标和属性分别表达

`session.members(Customer)` 构造惰性的成员域定义，尚未求出实际成员。
根成员域信任 Entity 声明的完整 `primary_key` 与版本规则，执行时投影完整身份；不默认
添加 `distinct`，也不自动扫描全源验证唯一性。复合主键必须保留完整元组，不能只取其中一列。
版本化 Entity 必须先按明确的 `at` 解析版本，再使用该版本范围内的身份；不能对全部历史
`distinct(K)` 来替代版本选择。已观察到违反本次消费契约的重复身份必须报错，不能用去重
或取第一行修补；既有成员、关系 fanout、对齐及输出完整性检查仍由各自所有者执行。

AnalysisDomain.observe 声明指标观察及其来源依赖；AnalysisDomain.read 声明逐实例属性读取及其来源依赖。
两者都不在调用时读取数据，而是在最终 execute 中求值。前者计算 Metric，后者要求单值变量映射。

```python
Region = ms.ref.dimension("sales.customer.region")
AOV = ms.ref.metric("sales.aov")

region = customers.read(Region)
aov = customers.observe(AOV, during=august, via=Buyer)
```

本例 Region 为无版本属性，不声称历史地区。历史变量必须显式 `at=...`，并有对应版本语义；
不取默认最新。成员、属性与指标作为明确依赖进入最终执行，不要求逐一物化。
只有需要跨次执行保留某个中间值时，才单独执行它并复用所得 Materialized 值。
`read(Measure)` 返回数值 Relation，但不会对分组里的多个 Measure 自动求和或取第一项。

每个 observe 只绑定一个量自己的时间和路径。不同窗口直接写两次观察，不再在单张结果中
创建 `july_revenue/august_revenue` 字符串列。比较和二元方法直接接收这两个有类型的量。
同一显式输入节点在一次 execute 内共享一个实现绑定；相同表达式分别构造两遍则是两个节点，
不自动保证同一次读取。共享节点也不代表所有独立来源读取处于共同快照。

对每个指标组件，实际贡献身份和变量必须来自该定义绑定的 Ref 与版本；贡献域恰好覆盖
该组件筛选、本次目标成员及时间范围所要求的事实。`via` 同时绑定获准的贡献归属角色和
时间选择，实际映射须符合对应关系事实。相同单位不能把利润替换成收入，相同端点也不能
把 recipient 归属替换成 buyer。量定义及其输入绑定保留这些角色、范围和实际来源实现，
不能只保存 Metric 名称和最终数值。完整性依据仍遵守来源及观察方法的准入政策。

理论第 6.7 节的 Observe 是单根、严格数值、无重叠贡献的一个完整实例；这里已有多组件、
加权、时间折叠等观察继续使用各自计算图和封闭政策，不因这一个实例而被压成单根求和。

假设 customers 为 u1/u2，只有 u1 有一笔 100 元订单。覆盖获准且 Revenue 空贡献为零时，
收入结果必须为 100/0，客户均值 50。AOV 则是 100/Undefined(zero_denominator)，不能补零。
成员选择时间、指标窗口、属性时点分别保存，不从其中一个推断另两个。

保留无时间范围的观察：`members.observe(metric, via=...)` 或显式 `during=None` 表示
该绑定来源中未加本次时间限制的全部获准贡献，不是“最近一段时间”。要求精确时点、累计端点
或有限窗口的 Metric 拒绝该形状；versioned members 仍须独立确定版本。没有源历史完整性的
依据时，不能把“未加过滤的来源”解释为业务全部历史。


成员定义中的版本选择、成员资格条件、指标观察范围是三个独立事实。无版本 Entity 可以直接调用 `session.members(Customer)`；有版本 Entity 必须明确 `at=`。时点选择和窗口结束前选择采用封闭的边界值：

```python
august = mv.time_scope(start="2026-08-01", end="2026-09-01")
customers = session.members(Customer, at=august.before_end)
revenue = customers.observe(Revenue, during=august, via=Buyer)
result = revenue.rollup().execute()
```

这里给 TimeScope 补充 `before_end` 类型化边界视图，与本设计时间网格的同名边界采用同一规则；
这是目标接口增量，不是对当前 TimeScope 已有属性的描述。它表示半开窗口结束前的版本边界，
不是用浮点 epsilon 减一个时点。snapshot Entity 按声明粒度和时区选择该边界所属的指定快照；
validity Entity 按声明的区间开闭性选取边界前有效的版本。不能改为“数据里能找到的最近一版”。
给出普通 `at=instant` 时则使用精确时点规则，两者不能互换。

成员资格的时间过滤继续复用 `read`、时间字段条件与 `members`，不再增加第二套人口筛选语言。例如，先明确九月前有效的客户版本，再选择八月注册者：

```python
RegisteredAt = ms.ref.time_dimension("sales.customer.registered_at")
registered = customers.read(RegisteredAt, at=august.before_end)
new_customers = registered.where(
    mv.all_of(
        registered.value.gte(august.start),
        registered.value.lt(august.end),
    )
).members()
new_customer_revenue = new_customers.observe(
    Revenue, during=august, via=Buyer,
).rollup().execute()
```

`at` 只适用于有版本选择意义的 Entity/属性；无版本声明不能据此制造历史事实。版本选择和成员过滤都成为域定义与输入绑定的一部分。同一个惰性成员节点在一次 execute 中固定一个实现；跨次执行固定成员仍需复用已执行的 Materialized Domain。此处不引入预先 execute 的要求。

### 5.2 路径在必要时显式，不反复配置内核细节

`via=Buyer` 指定订单到客户的业务角色。只有直接身份映射，或语义定义已明确绑定的唯一路径
才允许省略；存在多条候选路径时拒绝，不能挑最短路径或猜用户意图。
AOV 两组件共享 Order 根时，同一路径适用于两个组件；多根图通过各贡献根的 Entity Ref
分别选择路径。无需先访问 Metric Ref 上不存在的 numerator/denominator 属性：

```python
Order = ms.ref.entity("sales.order")
OrderLine = ms.ref.entity("sales.order_line")
AOVFromLines = ms.ref.metric("sales.aov_from_lines")
LineOrder = ms.ref.relationship("sales.line_order")

regional_aov = customers.group_by(region).observe(
    AOVFromLines,
    during=august,
    via=mv.routes(
        mv.route(OrderLine, through=(LineOrder, Buyer)),
        mv.route(Order, through=(Buyer,)),
    ),
)
```

本例 AOVFromLines 是已定义的 ratio：numerator 为已支付明细收入，denominator 为已支付订单数。
`mv.route(root_ref, through=relationship_refs)` 是封闭的路由参数：首端须为该贡献根，
相邻端点连续，终点须为目标成员身份。OrderLine 经 Order 到 Customer，Order 直接到 Customer；
再沿本次共享地区映射到组，各根分别归约后组合。
路由键来自显式声明或组件构造中的实际贡献根，不是 decorator `entities` 的全部依赖；
仅提供属性或筛选条件的 Entity 不因此成为一个要独立归约的贡献根。

这是一种明确受限的写法：同一根的各组件出现位置共用该路径，且规范图必须允许它们
使用相同业务角色与时间选择。解析时将路由绑定到每个相应出现位置，保留其各自贡献支持；
不把组件因根相同而合并。额外、重复、遗漏的根或不相容的路径均拒绝。
单根的 `via=Buyer` 是该同一路由规则的简写，不是自动找路。

同一 Entity 在不同组件必须走 buyer / recipient 等不同角色时，本节的按根路由不能表达，
应明确拒绝并指出冲突出现位置。未来若需要按出现位置路由，句柄必须由已解析的规范图提供并
绑定 Metric Ref / 定义版本，不能让纯 Ref 假装拥有图属性；本提案不提前发布一套未定的图浏览 DSL。
这里没有通用 join 或 `allow_fanout` 开关。

### 5.3 单值分组和贡献坐标分开声明，共用归约入口

`group_by` 定义从当前行域到组域的单值映射，可以接收多个有类型的分类输入。成员域可以
直接接收自身的 Dimension Ref，也可以接收已显式读取的 CategoryRelation：

```python
regional_revenue = customers.group_by(Region).observe(
    Revenue, during=august, via=Buyer,
)
```

这里 Region 的 owner 必须是当前成员的 Entity，且其属性版本由当前成员绑定唯一确定；
无版本属性可直接绑定，版本不明则拒绝。其含义与在同一成员绑定上先 read 再 group_by
一致，仍须履行类型、单值、覆盖及分类键检查。它声明一个惰性属性依赖，不立即读取来源，
也不按 Ref owner 自动搜索跨 Entity 路径。需要不同的明确属性时点或复用属性结果时，
继续显式 read，再将 CategoryRelation 传入分组：

```python
region = customers.read(Region)
segment = customers.read(Segment)
regional_segments = customers.group_by(region, segment)
regional_revenue = regional_segments.observe(
    Revenue, during=august, via=Buyer,
)
```

所有分类输入须覆盖接收者当前域，或有已保留的包含映射能对每行唯一取值；不是按行号拼接。组合键是这些单值映射的积，默认目标组域是组合映射的实际像。需要保留空组时，继续使用本文已经定义的 `groups=target_groups`，并检查目标组身份、覆盖和空贡献规则。执行并保存分组域时，成员集合、成员到组合键的映射及属性输入绑定一起固定。

分类键采用严格准入：当前域上每个实际消费的分类 Cell 都必须是 Defined，且值符合该分类的
类型和值域；任一 Null、Undefined 或 Unknown 均拒绝整次分组，不形成隐含的 SQL NULL 组，
不静默丢行，也不自动创建“未知”类别。业务上明确声明的“未知地区”可以是一个 Defined
类别，与 Cell 的 Unknown 不同。`groups=target_groups` 中的分类坐标也须有效，且不能
修补成员到组映射的缺失。确需排除缺值时，先显式选择已定义的分类及其成员，形成新的输入域；
不能把筛选后的结果声称为原完整总体。

一位客户可以在多个渠道下单。这时 Channel 不是 Customer 上的单值属性，不能写成 `customers.read(Channel)` 再 group_by。该能力通过已有 observe 入口新增一个必要的输入 `coordinates=` 表达：它指定本次观察保留的贡献坐标，坐标叶子仍是 Ref。

```python
Customer = ms.ref.entity("sales.customer")
Revenue = ms.ref.metric("sales.revenue")
Region = ms.ref.dimension("sales.customer.region")
Channel = ms.ref.dimension("sales.order.channel")
Buyer = ms.ref.relationship("sales.order_buyer")

august = mv.time_scope(start="2026-08-01", end="2026-09-01")
weeks = mv.time_grid(
    during=august, grain=mv.grain("week"), timezone="Asia/Shanghai",
)
customers = session.members(Customer)
customer_weeks = customers.each(weeks)
customer_channel_weeks = customer_weeks.observe(
    Revenue,
    during=weeks.window,
    via=Buyer,
    coordinates=(Region, Channel),
)
regional_weeks = customer_channel_weeks.group_by(Region, weeks).rollup()
regional_total = regional_weeks.group_by(Region).rollup()
result = regional_total.execute()
```

观察结果的实例身份为 Customer × Region × Channel × Week 中实际获准的组合。Region 对每位 Customer 单值；Channel 对订单贡献单值，对 Customer 可多值。同一客户在两个渠道有订单，就有两个渠道坐标，不把订单复制成一张未经治理的笛卡尔表。构造期为每个组件根解析到目标成员和各坐标的路径；不能唯一确定时要求现有 `via=routes(...)` 能明确表达的路径，否则拒绝。仅凭维度 owner、字段同名或关系可达，不足以授权路径。

贡献坐标域由明确的受治理贡献路径及其观察范围生成，不能仅从最终非零值推断。复合指标
必须先确定各组件共同使用的结果域，再独立归约并组合。具体规则为：

- 每个组件出现位置 j 在自己的贡献筛选、时间范围和路径绑定下，产生完整的有类型坐标
  元组集合 D_j；元组包含目标成员、全部请求的贡献坐标及已绑定的时间格。同根组件也
  分别形成自己的像，不把一个分支的筛选应用到其他分支。
- 本次贡献坐标结果域为 D = ∪_j D_j。并集比较的是完整元组及其身份绑定；不能取各坐标
  投影后做笛卡尔积，不能只用分子或某个根的像，也不能用交集丢弃仅一组件有贡献的元组。
- 每个组件都在 D 上独立求值。某元组不在 D_j 中，仅在该组件的范围与路径覆盖足以证明
  其贡献确实为空时，才按该组件的方法生成空状态和值；sum/count 可以为零，mean/ratio
  可能为 Undefined。已有非 Defined 值不因此改成空贡献。缺覆盖、未知映射或执行失败
  不能当作空输入；无法确定完整坐标像时拒绝建立该结果域。域已确定而数值为 Unknown 时，
  仍由该组件及组合方法的值政策处理，不能据此补零或删去坐标。

例如 §5.2 的 AOVFromLines 按 Channel 观察：同一客户同一周，web 有一笔已支付订单及
100 元明细，mobile 有一笔已支付订单但没有明细。在明细、订单和路径覆盖均成立时，
分子坐标像为 {web}，分母为 {web, mobile}；共同域保留两者，结果为 web=100/1、
mobile=0/1。mobile 的零来自明细 sum 的有效空状态，不来自缺侧补值。即使某渠道明细
金额正负抵消为零，该渠道仍在贡献像中。没有贡献坐标扩展的观察继续使用原明确目标域及其
空贡献规则；本规则也不扩大固定参照或改变分母的定义与范围。

贡献坐标中的分类键遵循本节相同的 Defined 政策，检查对象是各组件经过自身贡献筛选后
实际消费的路径与坐标输入；无关的未选中事实不因此接受全表分类检查。每个贡献必须能按
已获准路径确定完整坐标元组，不能通过丢弃缺键事实来使坐标像看起来完整。分类值、实际
坐标像和数据相关覆盖检查均登记为执行义务，不使 group_by 或 observe 提前读取来源。

坐标的像不宣称完整覆盖所有可能类别。`customers.each(weeks)` 原先保证每个客户周存在；
增加数据驱动 Channel 后，不会凭空知道无订单客户周应属于哪个渠道。各组件均无贡献时，
该客户周不会由并集规则凭空生成 Channel 坐标。此时结果域必须披露为受贡献坐标限制的域，
不能继续宣称完整 Customer × Week。
恢复到已知的完整客户周目标域可写
`customer_channel_weeks.group_by(Customer, weeks, groups=customer_weeks).rollup()`；
这沿用 group_by 的 groups，不给 observe 再加一套目标组参数。系统须验证目标域与保留坐标的
对应、贡献覆盖及空贡献规则；不能从任意缺失行推断零，也不能把未知渠道标签伪造为类别。

本例收入的订单贡献沿 Channel 分割互斥，可以从渠道合并回地区周，再合并各周。但多值标签会让同一订单进入多个标签组：这些行仍可观察、筛选、统计，不能因此对标签无条件 rollup。能否合并由规范图、贡献支持与保留状态决定；默认不提供 `allow_fanout` 逃生开关。不同独立的一对多分支也不能仅因同时出现在 coordinates 中而交叉展开。

归约时，只保留哪些坐标由同一个 `group_by` 表达，不另提供 `drop_dimensions`、`drop_time`、`aggregate` 的同义路径：

| 表达式 | 结果域及含义 |
| --- | --- |
| `r.group_by(Customer, Region, weeks).rollup()` | 合并 Channel，保留客户、地区、周 |
| `r.group_by(Region, weeks).rollup()` | 合并 Customer 和 Channel，保留地区、周 |
| `r.group_by(Region).rollup()` | 进一步合并时间，仍需时间可归约的状态 |
| `r.group_by(Region, mv.grain("month")).rollup()` | 合并到地区月，要求原时间格完整归属于目标月 |
| `r.rollup()` | 合并到 Singleton，所有被消去坐标都须满足方法前提 |
| `r.group_by(Region, weeks).summarize(mv.mean())` | 每个地区周对当前行均值统计，不是地区周原收入 |

表中的 Ref 只引用接收者**已经保留且唯一绑定的坐标**，不能触发新的属性读取。新的属性仍先
在拥有对应成员绑定的域上 `read` 成 CategoryRelation，再检查与接收者的对应；不能从汇总
Relation 隐式找回成员或读取新的维度。这与本节成员域直接绑定自身 Dimension Ref 的规则
由接收者类型明确区分。时间网格句柄表示保留该确切坐标，Grain 表示按接收者已有的唯一
时间轴做粗化；不增加 coarsen 同义入口。无法唯一定位的 Ref、重复键、多个候选时间轴均拒绝。
跨越目标格边界的旧时间格也拒绝：group_by 要求每个输入实例只映射到一个目标组，不能借有
细粒度状态就把一周拆成两个输出月份。需要这种月值时显式 observe 月网格。
GroupedRelation 仍只是待归约描述，不成为第二份数据。

正常探索从 `customers.observe(Revenue, ...).rollup().execute()` 看总数开始，再按用户
选择构造 `customers.group_by(Region).observe(...)`，随后限定地区成员并观察 Channel
贡献坐标。用户不必在第一次观察时预先列出全部下钻维度，也不新增 drill_down 同义入口。
这些步骤是沿明确成员、指标和窗口发起的新观察；标量汇总本身不恢复细节。已有细粒度
Artifact 时可显式复用其保留坐标，否则新观察允许读取来源，并披露新的输入绑定。
同一个 Logical 对象不固定跨次 execute 的来源；来源变化时下钻结果不保证核对到先前总数。
需要严格核对时必须使用共同固定输入；仅固定客户名单不能固定订单和属性来源。

### 5.4 summarize 统计当前行，rollup 合并原贡献

```python
defined_aov = aov.where(aov.value.is_defined())
mean_customer_aov = defined_aov.group_by(region).summarize(mv.mean())
regional_order_aov = aov.group_by(region).rollup()
overall_order_aov = aov.rollup()
```

mean 的唯一输入就是接收者当前的值，省去重复的字段引用；`mv.mean()` 是封闭统计方法描述，
不是回调。默认每行一票，要求值已定义且有限。显式 defined 筛选产生新的统计域，并保留该选择；
不能仍称未筛选总体。来源完整性义务不会因为筛选消失。

| 操作 | 被统计的贡献 | 两客户 AOV=1/100，订单数=100/1 时 |
| --- | --- | ---: |
| `aov.summarize(mv.mean())` | 当前 Customer 行 | 50.5 CNY |
| `aov.rollup()` | 原 AOV 的订单组件状态 | 200/101 CNY |

rollup 不接收 `agg=`；它不能改变原计量方法。局部 AOV 因零订单而 Undefined 时，合法的
零组件状态仍可参与上卷；合并后总分母为零则输出 Undefined。
mean 产生的新 sum/count 与订单 sum/count 不是相同统计状态。

默认 sum 与 mean 都要求所有参与值 Defined 且有限，不静默排除 Null、Undefined 或 Unknown。
空且有效的目标组上 count=0、sum=0、mean=Undefined(empty_mean)；显式目标组仍须保留。
空 mean 同时保留有效 `(0,0)` 状态，可以由原方法 rollup 与其他获准状态合并；
summarize 消费的是当前 Cell 值，不能把该 Undefined 当作零或自动取其状态。
覆盖不足、原贡献未知或状态缺失不属于合法空状态。

目标 DSL 的两种计数各有定义，不能直接共用理论第 6.7 节的严格数值 count：

| 方法 | 贡献及 Cell 政策 | 结果含义 |
| --- | --- | --- |
| `mv.count()` | 当前域中的每个实例贡献 1，不消费该实例的数值 | 当前实例数，包含其值为 Null、Undefined 或 Unknown 的行 |
| `mv.count_defined()` | 逐行判断 Cell 标签，Defined 贡献 1，其他分支贡献 0 | 当前量的已定义值数；其他分支不因此成为 false 业务事实或零数值 |
| 理论第 6.7 节 count | 每个参与数值须为 Defined，任一非 Defined 即拒绝 | 该有限子语言中的严格计数，当前 DSL 不为它增加第三个入口 |

上述计数的语义单位均绑定其贡献身份；对当前行统计时是当前行单位，合并原状态时仍是
原贡献单位。完整定义值域上三者的数值可以相同，但政策不同；不能用数值相同互换定义。
计数一个实际域也不证明它完整覆盖目标域，来源和成员覆盖义务不因此消失。
加权统计使用 `mv.weighted_mean(weight=StatisticalWeight)`；权重对象绑定独立 Relation、统计角色
与域，不能把订单数、分配或抽样权重自动代入。`count_distinct` 与分位数的目标首次接入
只允许直接观察，不提供原量 `rollup()`；`summarize()` 仍可对其已定义的当前行值提出新的
统计问题，但结果不能冒充原量的粗粒度值。时间折叠继续按自身的 RequiredParts 准入。

### 5.5 一个有限时间网格入口，复用现有粒度和认证窗口

以通用 `mv.time_grid(during=..., grain=..., timezone=...)` 取代原草案的专用 weeks 工厂；
不同时保留 days/weeks/months 多套同义入口。`mv.time_scope` 仍只定义范围，
`mv.grain` / `ms.calendar_grain` 仍只定义分格规则，time_grid 将两者绑定为有限的时间坐标域。
预测的既有 `mv.periods(n)` 返回 ForecastHorizon，职责不同，继续保留。
时间格带逐行窗口、端点及主体乘积映射，这些不能由 Grain 或 TimeScope 单独表达，
因此需要一个域构造；旧 with_time_axis 的计算能力由此构造加 observe 承接，不并存同义入口。

```python
window = mv.time_scope(start="2026-08-03", end="2026-08-31")
weeks = mv.time_grid(
    during=window, grain=mv.grain("week"), timezone="Asia/Shanghai",
)
customer_weeks = customers.each(weeks)
weekly = customer_weeks.observe(Revenue, during=weeks.window, via=Buyer)
result = weekly.group_by(weeks).rollup().execute()
```

每个格拥有稳定坐标、半开窗口、start/end/before_end 句柄和边界时区。内置 week 沿当前 Grain 的固定周规则，不在不同入口另设不同默认值。范围边缘不足一格时，必须保留实际裁剪边界和“不完整格”事实，不能当成完整周；需要完整格的方法拒绝该输入。网格的目标域在构造时明确，零贡献格仍保留，不能用实际有行的桶重新编号。

`each` 只构造已注册的有界时间乘积，不是通用笛卡尔积。`weeks.window` 指当前行窗口；传固定 window 则仍是固定窗口，不自动改为逐格计算。逐格范围、来源 TimeDimension、成员版本时点彼此独立。Metric 图已唯一绑定来源时间轴时无需重复配置；当前 `time_dimension=TimeDimensionRef` 继续用于不能从图中唯一确定来源时间轴的观察，不能将它与输出 Grain 当成同一个输入。

业务时间继续消费已有认证快照；Catalog 查询所返回的窗口是 Analysis 值输入，不是把 CatalogEntry 当作语义叶子传入计算图：

```python
Fiscal = ms.ref.period_calendar("sales.fiscal")
fiscal_calendar = session.catalog.period_calendars.get("sales.fiscal")
fiscal_quarter = fiscal_calendar.period("fiscal_quarter", "FY2026-Q3")
fiscal_weeks = mv.time_grid(
    during=fiscal_quarter,
    grain=ms.calendar_grain(calendar=Fiscal, level="fiscal_week"),
    timezone="Asia/Shanghai",
)
fiscal_revenue = customers.each(fiscal_weeks).observe(
    Revenue, during=fiscal_weeks.window, via=Buyer,
).group_by(fiscal_weeks).rollup()

campaigns = session.catalog.temporal_sets.get("sales.campaigns")
promotion = campaigns.occurrence("autumn_launch")
promotion_revenue = customers.observe(
    Revenue, during=promotion, via=Buyer,
).rollup()
result = mv.table(fiscal_total=fiscal_revenue.rollup(),
                  promotion_total=promotion_revenue).execute()
```

`period`、`period_on`、`occurrence` 及有界发现继续使用当前 Catalog 入口，不另加 analysis 同义函数。period/occurrence 返回的 TimeScope 保留日历或集合 Ref、认证快照摘要、期间键和确切边界；执行需检验使用的粒度、时区、认证范围及层级归属。Calendar Entry 只是发现/解析入口，进入 Analysis 的 Grain 与 TimeScope 才是依赖值。相同文字键在不同认证快照下不保证同一个期间。

TemporalSet occurrence 可以重叠，不能自动视为时间分区。上例每个 occurrence 独立观察成立；把多个活动的总额相加并称为全期间总额，还需不重叠或获准的分配方法。该边界不妨碍现有按单个 occurrence 观察的能力。

累计指标继续由已有 Metric 定义承载，不新增 `.cumulative()` 分析操作。累计图的 anchor 明确 all-history、grain-to-date 或 trailing，来源时间轴、重置粒度和窗口由该定义及本次绑定一起决定：

```python
CumulativeRevenue = ms.ref.metric("sales.revenue_to_date")
cumulative = customers.each(weeks).observe(
    CumulativeRevenue, at=weeks.end, via=Buyer,
)
result = cumulative.group_by(weeks).rollup().execute()
```

每个端点 e 的值使用定义要求的 `[anchor(e), e)` 贡献；grid 的展示起点不会截断需要的更早历史，也不自动成为累计重置点。`at=weeks.end` 在累计方法中是明确的累计结束边界，不是状态选择的 before_end。累计先聚合客户而保留端点可以成立；消去时间不能把多个累计值相加。只有图登记了相应带前提的时间归约规则且保留足够状态，才允许进一步 rollup；否则拒绝。all-history 也不等于已证明来源覆盖全部历史，覆盖仍是单独的准入事实。

### 5.6 多量分析复用 Relation，不恢复动态字段字典

多事实画像分别观察各量，它们以同一个明确成员域为共同坐标。比率、相关等二元方法接收
Relation 参数，并按其注册的精确对应规则检查；不把全部事实先 join 成一张表。
`left.ratio(right)` 只得到普通比值，不能自动成为份额或转化率。
NumericRelation 的二元方法还需核对各自定义、范围和支持；两个 NumericRelation 不等于可任意相除。

筛选也能组合多个有类型输入；这里复用同一个 customers 逻辑域节点，不增加未声明的业务来源：

```python
high_value_east = aug.where(
    mv.all_of(aug.value.gt(0), region.value.eq("east"))
)
```

若 region 仍为 Logical，执行依赖图时会读取它已经显式声明的来源；where 自身不会凭列名
增加来源。若两个输入都是 Materialized，则只消费其保留数据。

需要并排展示时才命名列：

```python
profile = mv.table(revenue=aug, aov=aov, region=region).execute()
profile.show()
```

table 只接受明确、完整同键的 Relation，列名是展示标签。它是只读展示/导出结果，不提供
字符串列回灌、通用算术或另一套分组 DSL；后续分析继续使用原 Relation 变量。
不宣称任意 `**kwargs` 可生成可静态检查的 `.revenue` 属性。
同一次执行必须共享显式共同节点的实现，不能因此承诺不同来源读取共享事务快照。

### 5.7 分析期指标复用已有工厂

“只通过 Ref 衔接”限定的是**对语义定义的引用方式**，不排除 Analysis 自己构造表达式。
observe 接受的闭合输入为 `Ref[MetricKind] | RuntimeMetricExpr`；两者都由
Session 解析绑定，不接受 CatalogEntry、裸字符串、作者函数、SQL 或任意 Python 回调。
RuntimeMetricExpr 不是新 Catalog 对象，也不改写原 Metric。它定义这次观察要计算的一个量；
执行、数据身份、状态和源依赖仍属于生成的 AnalysisRelation。

沿用现有 `mv.runtime_metric` 的**五个**工厂，不另加 analysis.linear、derive、formula 等同义入口：

| 工厂 | 闭合输入 | 输出含义 |
| --- | --- | --- |
| `aggregate(measure, agg=..., label=..., fold=..., slice_by=...)` | Measure Ref；注册聚合；可选时间 fold 和分支筛选 | 从一个受治理 Measure 定义聚合量 |
| `weighted_mean(value, weight, label=..., slice_by=...)` | 两个 Measure Ref | 保留加权和与权重和的量 |
| `slice(metric, by=..., label=...)` | Metric Ref 或 RuntimeMetricExpr；字段 Ref 到闭合 SliceValue 的映射 | 限制该计算分支的贡献来源 |
| `ratio(numerator, denominator, label=..., zero_division=...)` | 两个 Metric Ref 或 RuntimeMetricExpr | 同一观察绑定下的比值定义 |
| `linear(add=(...), subtract=(...), label=...)` | 有序 Metric Ref / RuntimeMetricExpr；固定 +1/-1 系数 | 同一观察绑定下的和差定义 |

`agg` 保留现有封闭选项：sum、count、count_distinct、min、max、mean、median、
`("percentile", q)`；`0 < q < 1`，不能把 bool 当作 q。是否可观察由方法与 Measure 契约
确定；目标首次接入的 count_distinct、median 和 percentile 一律不支持原量上卷或归因，
不因输入同为数字而放行。`fold` 沿用已有注册时间折叠参数；只有相应
Measure 的时间语义允许时才接受，不授权跨需要固定的轴求和。

SliceValue 沿用标量、标量集合和现有有限比较操作的闭合形状；实现需把现有 SlicePredicate
中宽泛的 `Any` 收紧为相应标量/集合联合类型，不增设第二套谓词工厂。字段 Ref 只标识语义字段，
其 owner、物理类型、值域和这条贡献分支是否可读取，仍在解析与执行时检查。

```python
Amount = ms.ref.measure("sales.order.amount")
Status = ms.ref.dimension("sales.order.status")
Cost = ms.ref.metric("sales.cost")

paid_revenue = mv.runtime_metric.slice(
    Revenue, by={Status: "paid"}, label="Paid revenue"
)
margin = mv.runtime_metric.linear(
    add=(paid_revenue,), subtract=(Cost,), label="Paid revenue less cost"
)
margin_rate = mv.runtime_metric.ratio(
    margin, paid_revenue, label="Margin rate", zero_division="null"
)

rates = customers.observe(margin_rate, during=august, via=Buyer)
defined_rates = rates.where(rates.value.is_defined())
result = defined_rates.where(defined_rates.value.gt(0)).summarize(mv.mean()).execute()
```

`slice` 在原始贡献上限制特定分支；`where` 在已经定义的输出量上选择实例。两者一般不交换。
上例先显式选取已定义比值，再筛选正值；最终均值属于这个新子域，不是全部目标客户的比值均值。
上例 paid_revenue 限制收入分支，不会悄悄把 Cost 分支也改成 paid；不同根的路由继续遵循 §5.2。
同理，`margin` 的差值是一个公式量，不是具有 TimeChange 设计的时期 Difference。单位相同
不自动代表可相加；解析后的定义还必须满足该公式方法的语义兼容规则。之后对两期 margin
调用 compare，才产生该公式量的时期变化。已有 `compare` 足以表达变化的变化时，不再加
第二套 relation.linear / derive。

既有 `zero_division: Literal["null", "error"]` 参数形状保留，并在构造时校验；
解析为与 §3.4 同一规范政策：`"null"` 对应 `ms.zero_denominator.undefined()` 的政策值，
`"error"` 对应 `ms.zero_denominator.error()` 的政策值。前者产生 `Undefined(zero_denominator)`，
不是“有定义但缺失”的 Null；后者拒绝求值。旧实现的 null 存储不能直接成为新理论中的
Null 含义。这是一项必要的值语义调整，不在新装饰器参数中复制 `"null"` 这个名称。

目标接口中，精确／近似由 Metric 定义决定，不接受观察侧 accuracy 或算法覆盖。
`median`、`count_distinct`、`("percentile", q)` 使用非近似操作；对应的近似定义为
`approx_median`、`approx_count_distinct`、`("approx_percentile", q)`。

```python
exact_p95 = mv.runtime_metric.aggregate(
    Amount, agg=("percentile", 0.95), label="P95 order amount"
)
approx_p95 = mv.runtime_metric.aggregate(
    Amount, agg=("approx_percentile", 0.95), label="Estimated P95 order amount"
)
exact_by_customer = customers.observe(exact_p95, during=august, via=Buyer)
approx_by_customer = customers.observe(approx_p95, during=august, via=Buyer)
```

q 和聚合种类属于定义、指纹、执行键及 receipt。聚合必须通过 Ibis 编译为数据源 SQL；
不拉取贡献行在 Python 中排序或插值。原生分位数的数值精度损失可以接受，但须披露
实际算法、输出类型和限制，不编造误差界。Ibis 编译成功不等于精确性资格。
不支持精确定义时，错误提示对应近似定义及其在该数据源上的支持情况，绝不自动替换。

直接 distinct/quantile 观察不保存完整分布或 sketch，不授予原量 rollup 或归因；
当前结果行上的已获资格统计仍可使用。装饰器函数体保持 opaque 边界：Ibis 操作表达
计算意图，但不据函数名或函数体推断贡献图、可加性或续算权利。

## 6. 比较与成员选择

### 6.1 比较方法、配对域与缺侧政策分别明确

`NumericRelation.compare(baseline)` 保留唯一默认：同一量定义与同一目标成员的绝对时间变化，完整身份精确配对，方向 current - baseline。它不自动退化为交集、并集或补零。其他已有能力仍通过 compare 的封闭参数表达：

```python
absolute_change = august_revenue.compare(july_revenue)
relative_change = august_revenue.compare(
    july_revenue, value="relative_change",
)
category_change = august_by_channel.compare(
    july_by_channel,
    design=mv.TimeChange(
        pairing=mv.UnionKeys(missing="keep"),
    ),
)
```

`value` 是 `"difference" | "relative_change"` 的封闭选择，不是任意输出字段名。每次仍返回一个量的 NumericRelation。relative_change 明确定义为 `(current - baseline) / abs(baseline)`，保持当前 Marivo 的分母约定；基线为零返回 Undefined(zero_baseline)，不产生 infinity 或伪造零。绝对差的单位与输入相同，相对差无量纲，定义、端点、数值策略和证据分别保留。不同时提供 `.relative_change()` 同义入口。

ComparisonDesign 继续是 TimeChange / CohortContrast / PeriodChange 的闭合联合。TimeChange 固定量定义模板及非时间条件，改变观察时间；CohortContrast 在共同组坐标或 Singleton 上比较不同成员群体；PeriodChange 显式建立两个时间轴之间的对应。共同 Entity 类型、相同行数和同样列名都不足以建立配对。

默认时间比较固定指标或派生量的定义模板、目标成员输入、贡献归属角色、分组、单位和
值政策，只改变明确的 current/baseline 时间角色。共享目标成员须有同一显式实现绑定，
不能把两次独立读取的相同成员定义当作相同输入；两期贡献来源本身不要求共同事务快照。
理论第 6.7 节只完整定义了 Observed 端点的严格时间差；本 DSL 的 RuntimeMetricExpr、
相对变化和嵌套 Difference 比较是范围更广的注册方法，须分别匹配其定义模板和数值条件，
不能仅以“源于同一 Metric”代替这些判断。

精确配对与并集配对是两个方法，不是成功和失败路径：

| 配对 | 实际配对域 K | 缺侧 |
| --- | --- | --- |
| 默认 ExactKeys | 两侧必须完整同键 | 任一缺键拒绝 |
| `UnionKeys(missing="keep")` | 两侧坐标键的并集 | 保留 MissingCoordinate；依赖该侧的差值 Undefined(missing_side) |
| `UnionKeys(missing="metric_empty")` | 两侧坐标键的并集 | 只有能证明是完整观察中的空贡献，才应用该 Metric 的空输入结果 |

ExactKeys 使用同一有类型坐标 T 上的键 `k_i:D_i→T`；两侧键都单射且键像相等。
T 可以是同一 Entity 身份、分组或时间乘积坐标，不能把不同角色的同值整数当作同一种键。
`K={(u,v):k_current(u)=k_baseline(v)}` 的两个投影因而分别完整覆盖两侧实际域，并形成双射。
两域同时为空合法；重复键、缺侧或不同键像均拒绝，不用行数相同或每个已有配对唯一替代完整性。
预先 where 后仍检查两个实际域；完整观察中的零贡献成员应保留其坐标及指标空输入结果，
不能与筛选造成的缺行混同。UnionKeys 同样要求两侧各自键唯一，不能以并集配对吞掉重复键。

missing 是 `Literal["keep", "metric_empty"]`，由 UnionKeys 持有；不额外创建同义的缺侧政策工厂。`MissingCoordinate` 是某一侧没有坐标；它与 `Present(Null)`、`Present(Undefined)`、`Present(Unknown)` 分开保存，不把缺行直接转换为 Cell 的 Null。MetricEmpty 也不是 fillna(0)：sum/count 的有效空贡献可能是零，mean/ratio 可能是 Undefined。由于 where、rank、缺覆盖、缺失版本或执行失败而没有的行，不能借该政策推断空贡献；也不能覆盖一侧已存在的 Null/Undefined/Unknown。缺侧证据不足时拒绝 MetricEmpty，而不是继续使用零。

完整配对后的普通数值差要求两端都是 Defined 且有限，单位和比较设计相容；任一参与端点
为 Null、Undefined 或 Unknown 则拒绝，不能仅因键齐全就执行减法。相对变化采用同样的
端点准入，再按已声明的零基线政策计算。UnionKeys 的 matched 行也遵守该规则；
missing="keep" 的单侧行不做差值算术，保留所有实际端点状态并产生 Undefined(missing_side)。
missing="metric_empty" 只对缺侧使用已获准的空输入结果：实际存在的一侧仍须满足数值准入；
空输入为 Defined 时按所选差值方法计算，为 Undefined 时保留其无定义原因而不伪造数值。
这些缺侧规则不把现存的非 Defined 值改成空贡献，也不由执行器自动选择另一种方法。

比较结果保留两侧身份映射、端点 Cell 和配对状态；Materialized 的 show/contract 披露 matched/current-only/baseline-only 及未定义原因，唯一分析主值仍是 `.value`。不会同时隐含生成一组可任意字符串寻址的 delta/relative_delta 列。
若为归因等续算保留端点组件，必须在 Π 中绑定到原端点量、贡献和范围；它们是该续算的依据，
不是 Difference 自身的可上卷状态。缺少所需端点或分解部件时仍按相应方法拒绝。

期间比较复用现有 `mv.window_bucket()`，不增设按序号的同义入口：

```python
july = mv.time_scope(start="2026-07-01", end="2026-08-01")
august = mv.time_scope(start="2026-08-01", end="2026-09-01")
july_days = mv.time_grid(during=july, grain=mv.grain("day"),
                         timezone="Asia/Shanghai")
august_days = mv.time_grid(during=august, grain=mv.grain("day"),
                           timezone="Asia/Shanghai")
july_revenue = customers.each(july_days).observe(
    Revenue, during=july_days.window, via=Buyer,
).group_by(july_days).rollup()
august_revenue = customers.each(august_days).observe(
    Revenue, during=august_days.window, via=Buyer,
).group_by(august_days).rollup()
result = august_revenue.compare(
    july_revenue,
    design=mv.PeriodChange(alignment=mv.window_bucket()),
    value="relative_change",
).execute()
```

window_bucket 在每个相同非时间坐标下，将两侧完整、有序的观察桶按位置配对。两侧桶数必须相同；示例两月各 31 日。其含义是“期间内第 n 个桶”，不是同一天身份，也不是自动同星期几或同财政周。两侧所有端点和对应关系保留；不能先丢掉零值/缺值桶再给剩余行重新编号。财政日历用同一规则时仍需确认该序号对比就是所选研究设计，不能因 bucket 数相同就推断业务期间可交换。

选择或投影后若完整桶绑定仍被保留，可按保留绑定检验；若不足以建立精确对应，则拒绝。不同桶数、不同业务对齐意图需要另行接受其明确的对应方法，不能猜测截断、填充或日历映射。这里不提前发布万能 Pairing API。

派生量仍可继续比较，不因已经是 Difference 就成为终点：

```python
acceleration = change.compare(previous_change)
group_gap = treatment_total.compare(control_total, design=mv.CohortContrast())
```

第一次比较的量定义、端点、输入实现与第二次的设计一起保留。
`(Aug-Jul1)-(Jul2-Jun)` 不因两次 July 的逻辑定义相同就化简为 `Aug-2*Jul+Jun`；
共享实现、量定义相容和数值重排许可分别检查。FunnelResult.compare 是第 8.1 节的领域重载，
其宽结果有独立的漏斗配对与目标选择规则，不套用本节单量调用的默认设计。

### 6.2 where 限制分析关系，members 提取成员域

`change.where(change.value.lt(0))` 表示**限定在下降客户子域上的收入变化关系**，类型仍是
LogicalNumericRelation。它保留这些客户的身份、负差值、CNY 单位、八月减七月的定义和输入绑定，
只是行实例集变小；它不是单独的客户名单，也不是已经执行后的布尔数组。

where 对普通值比较采取严格政策：在接收者的每个实例上，比较实际引用的各值都必须满足
其方法的 Defined、类型、单位及数值前提；任一失败则拒绝整次选择，不按 false 静默排除。
`all_of/any_of/not_` 不用短路绕过任一子条件的消费前提。`is_defined()` 则是对 Cell 标签
全定义的显式判断，允许检查 Null、Undefined 和 Unknown；它与分类、布尔及领域状态谓词
各有封闭规则，不套用“所有字段都必须 Defined”的一刀切检查。

因此 `all_of(value.is_defined(), value.gt(0))` 不会为非 Defined 值提供比较保护；
需要先单独用 is_defined 限制域，再在该子域比较。两次 where 各自成功也不自动允许合并：
理论 L1 还要求两个谓词在同一个原始域上均全定义。cohort 对未知机会的量化判断由第 6.3 节
独立定义；构造 BoundPredicate 本身不提前执行 where 的整域检查。

以三个客户的变化为 -40、+20、0 为例，以下为最终求值时的含义示意：

| 表达式 | 输出形状 | 承载内容 |
| --- | --- | --- |
| `change` | NumericRelation | u1 → -40，u2 → +20，u3 → 0；收入差值 |
| `declining_change = change.where(change.value.lt(0))` | NumericRelation | u1 → -40；仍是同一差值量 |
| `decliners = declining_change.members()` | AnalysisDomain | 实现成员为 {u1}；客户身份与负变化选择依据仍保留 |

`.members()` 是明确的身份投影：从“这些客户的变化是多少”转到“这些客户是谁”。
它不读取来源，不提前物化，不重新选人；选择依据仍作为依赖保留，-40 不再是输出上的量。
因此下一步要统计下降幅度，直接写 `declining_change.summarize(mv.mean())`；
要观察这些客户的九月收入，写 `decliners.observe(Revenue, during=september, via=Buyer)`。
两种问题的接收者不同，避免在 Relation 上增加会隐式丢弃当前量的 observe。

所以 `.members()` 不是 where 的必需后缀；只有从承载量的关系切换到成员域时才使用。
两步可以链写，但本文主例拆成两行，使类型变化可见。所有步骤仍保持 Lazy，最终 `.execute()`
才求值。Entity 行直接保留身份；它不是对原总体逐人判定真假，也不重新评估入群窗口。

对非 Entity 行，members 是沿明确主体角色取得直接像，而不是简单丢掉 value 列。

Journey、Interval 等非 Entity 行必须提供保留的 SubjectBinding，例如
`selected.members(through=buyer_binding)`；它包含来源实例域、目标主体身份/域和单值角色映射。
选择只沿已有子域包含关系限制该映射，不能按同名角色猜测，也不能回源取得缺少的映射。
一人多个选中实例只产生一个主体；机会重数和锚点不进入输出成员身份。

上述去重是主体集合像的语义：Journey 身份各自唯一，仍可有多条 Journey 指向同一客户。
Entity 身份声明不能证明这个映射为单射。来源路线通过 Ibis `distinct()` 或等价的集合像
实现取得唯一主体；固定输入路线保持同一语义。若接收者已是按 Entity 身份唯一的域，且
主体映射为恒等或已有依据的单射，则直接投影即可；`where` 保持该性质，无需额外去重。
所有实现都保留唯一性推导依赖的声明或证据，不把省略去重记成已验证全源唯一性。

输出的精确承诺是“在这个明确选中域中有至少一个实例”。它不宣称未选者已经被证明没有行为，
也不能把来源拥有者要求的完整性检查绕掉。筛选未知条件时默认拒绝；明确选择 known-true 的
领域结果只承诺这个子域，必须保留其范围。
分组统计后没有主体映射时，members 构造期拒绝，不能凭 lineage 恢复名单，也不能把组标签当客户。

### 6.3 cohort 专门表达全机会域的资格判定

“四周至少三周活跃”“没有发生”“每次都满足”使用 cohort，不能通过正向 members 偷换。
接收者指定目标主体域；绑定条件携带实例机会域；rule 指定量词。它们均可保持 Logical：

```python
regulars = customers.cohort(
    weekly.value.gt(0),
    rule=mv.at_least(3),
)
```

这里 weekly 来自完整 customers.each(weeks)，条件绑定的机会就是四周每个 Customer × Week。
主体投影由 each 构造明确给出；不重复填相同机会域，也不从“出现过数据的周”反推目标机会。
其他领域使用 `through=SubjectBinding`，显式选择角色；缺少资格域、映射或覆盖依据则拒绝。
构造器消费真实保留的机会域契约，不允许调用者用一份任意 AnalysisDomain 声称“这就是全部机会”。

cohort 消费 BoundPredicate 时，先在完整机会域上检查其实际引用的输入，再产生每个机会的
`true / false / unknown` 真值。缺机会、缺键或缺少覆盖依据是准入错误，不能制造一条 Unknown
机会补齐。机会存在但其值未知，与机会本身不存在仍是两回事。

| 逐机会谓词 | Cell 消费政策 |
| --- | --- |
| 普通数值比较，如 `value.gt(0)` | Defined 值满足方法的类型、单位和有限性等前提时产生 true 或 false；Unknown 产生 unknown；Null 或 Undefined 返回结构化错误，不统一转换为未知 |
| `value.is_defined()` | 对 Cell 标签全定义：Defined 为 true，Null、Undefined、Unknown 均为 false；不把其他分支转换为已知业务事实 |
| 分类、布尔及领域状态谓词 | 按各自封闭方法的值状态规则产生真值或错误；没有明确规则时拒绝，不能套用数值比较或隐式布尔转换 |

Unknown 只能保留该方法允许的值不确定性，不能豁免类型、单位、来源完整性或其他强制准入
义务。`is_defined()` 也不消除这些义务；构造 BoundPredicate 或 cohort 时不提前读取业务来源，
需要实际值的检查仍由最终 execute 履行。

`all_of` 在任一子条件为 false 时为 false，全部为 true 时为 true，其余为 unknown；
`any_of` 在任一子条件为 true 时为 true，全部为 false 时为 false，其余为 unknown；
`not_` 交换 true/false，保留 unknown。因此 `false AND unknown = false`、
`true OR unknown = true`，但合成前必须检查全部实际引用子条件的消费前提，不能借已知真值
掩盖子条件对 Null、Undefined 的消费错误或其他强制失败。
`all_of(value.is_defined(), value.gt(0))` 遇到 Undefined 仍拒绝。
这是 cohort 方法内的三值真值合成，不是 Cell 四分支的全局逻辑，
也不改变第 6.2 节 where 的严格政策。

对一个主体，记完整机会域内的 true、unknown、false 数分别为 t、u、f；完成上述检查后：

| 量词 | 资格真值 |
| --- | --- |
| `any_instance` | t > 0 为 true；t = 0 且 u = 0 为 false；其余为 unknown；完整空机会域为 false |
| `at_least(k)` | k 必须为正整数；t ≥ k 为 true，t + u < k 为 false，其余为 unknown；完整空机会域为 false |
| `all_instances` | 非空机会域中，f > 0 为 false；f = 0 且 u = 0 为 true；其余为 unknown。空机会域必须显式选择 `mv.empty_opportunity.true()`、`mv.empty_opportunity.false()` 或 `mv.empty_opportunity.undefined()`，没有隐藏默认 |

例如至少三周的规则下，三真一未知可入选；三真加一个 Undefined 输入在谓词消费时拒绝；
两真一假一未知的资格仍未决定。量词不能用已足够的真值跳过剩余机会的强制检查。
当前精确 AnalysisDomain 输出要求全部目标主体的入选资格可决定，否则返回结构化错误；
不能将 unknown 或空域政策产生的 undefined 静默排除为 false。
`all_instances(empty: EmptyOpportunityPolicy)` 只接收上述 `mv.empty_opportunity` 构造器的
政策值；它与 `ms.empty.zero()` 返回的 `EmptyContributionPolicy` 不同。前者决定空机会域
上的量词真值，后者决定完整目标组没有贡献时的数值。两类值不能互传，传入裸字符串也须拒绝。

## 7. 参照、分配与完整子分析

### 7.1 独立参照直接作为另一个量传入

```python
total_revenue = aug.rollup()
shares = aug.share_of(total_revenue)
top = shares.rank(order="descending", ties="ordinal").limit(10)
result = top.execute()
```

share_of 默认参照必须是同一计量的 Singleton Relation；标量参照映射由这个受限形状唯一确定，
无需用户再传 `singleton_reference()`。分组参照的未来变体必须有明确的对应输入，不能猜 join。
share 要求支持包含、相容可加计量或获准分配侧项、有效分母；若声称组成整体，还需完整分区。
非负贡献和正分母等条件成立时才承诺 [0,1]；分母为零时 Undefined，不补零。

“固定参照”指参照绑定不随展示筛选改变，不要求参照已经物化。
这里 aug 与 total_revenue 都保持 Logical；最终执行先满足共同依赖，再计算份额和 Top-K。
Top-K 只筛选展示域，不重算独立分母；若先选 Top-K 再建立参照，则是另一个问题。
members.penetration_in(reference_members) 消费身份相容的两个 AnalysisDomain，定义为
`|B ∩ Ω| / |Ω|`，不是普通数值相除；重叠类别之和可以超过一。
参照必须是成员含义完整的固定目标域；空 Ω 输出 Undefined(empty_reference)，不生成零渗透率。
排名同样需要明确参照与平局政策，不能因显示行数改变而改题。

排名复用当前量和显式分组输入，不再次指定字符串字段：

```text
NumericRelation.rank(*, order: Literal["ascending", "descending"],
    ties: Literal["ordinal", "dense", "min", "max"],
    partition_by: tuple[CategoryRelation, ...] = ()) -> LogicalRankingResult
RankingResult.values -> NumericRelation
RankingResult.ranks -> NumericRelation
RankingResult.where(BoundPredicate) -> LogicalRankingResult
RankingResult.limit(count: int) -> LogicalRankingResult
```

`values` 保留原量，`ranks` 是绑定原排名域、分区与平局政策的新量；两个固定视图共享行身份。
沿用现有 rank 的 order/ties/partition_by 参数，不另加 order_by/top 等同义排序入口。
单量接收者已经确定被排的值，因此省去重复的 by 字段。partition_by 的分类输入必须通过同域
或已有包含映射取得，且对应当前实例的分区坐标；空元组表示整体排名。
ordinal 对同值按规范实例键稳定打破平局，这只决定展示次序，不提供业务先后依据。
dense 使用连续名次，min/max 分别取平局占据的位置下界/上界。已定义的有限值从 1 开始排名；
Null/Undefined/Unknown 行保留原值状态，排名也保留相应非定义状态并排在末尾，不当作零参与。
这是已定义值子域中的排名，不宣称未知值已确定落后；排除这些行须显式 where，并保留域变化。
limit 沿用现有 1–100000 的整数范围并排除 bool；取已排好序的全局前缀，不是每组 Top-K，
也不是抽样；分区结果按规范分区键、名次、实例键
形成确定顺序。需要每组前 k 名时先以 `ranking.ranks.value.is_defined()` 显式限制已排名域，
再对该结果的 ranks 筛选 `value.lte(k)`，保留 ties 决定的全部并列行；不能把未知排名直接当作大于 k。
where/limit 同步限制两个视图，但不重算原排名或 share 的固定分母。
归因、候选、相关与预测结果通过各自具名数值视图复用这一入口。

### 7.2 标准化与归因各自保留方法语义

标准化使用 `stratum_values.standardize(reference=ReferenceWeights)`，返回 NumericRelation。
ReferenceWeights 绑定统计单位、分层身份和固定输入；`q_std = sum(w_ref[k] * q[k])` 要求
非负权重、和为一、正权重层有合法值及获准加权方法。缺层不自动重归一化，访问率不能使用
用户权重冒充同一目标。标准化量不等于实际总体值；这不为任意非线性量增加合并许可。


归因沿用现有入口，不额外公开能由输入推导的 basis/rule 参数：

```text
change.attribute(
    *, axes: tuple[Ref[DimensionKind], ...],
    mode: Literal["joint", "hierarchy"] = "joint", top_k: int | None = None
) -> LogicalAttributionResult
```

接收者给出 target，axes 与已保留/显式请求的分项给出 basis，量定义和充分部件决定唯一注册
rule。内核仍保存理论要求的 target/basis/rule 三元组；用户无需重复填写从输入可唯一推导的事实。
没有合法或唯一的方法时给出结构化错误，不由后端猜测或试另一种分配。

```python
change = current.compare(baseline)
attribution = change.attribute(axes=(Region, Channel), mode="joint", top_k=5)
negative = attribution.where(attribution.contribution.value.lt(0))
result = negative.contribution.rank(order="ascending", ties="ordinal").execute()
```

AttributionResult 是固定输出形状，拥有 `.contribution`、`.current`、`.baseline` 数值 Relation
视图，保留坐标、方法、原 target/basis scope 及其 coverage 和 reconciliation 输入绑定。
where 同步限制这些视图，当前子域不再自动拥有完整分区或贡献求和回到 target 的承诺。
原核对必须标明针对未筛选输入，不能充当筛选后结果的完整性证明，即使保留行的数值和仍等于 target。
视图的 rank 复用 §7.1，不另建归因排序入口。当前/基准视图表示**该方法分配后的侧项**，
尤其 component_mix 不能将其误标成未经分配的分组比率。

现有四个闭合方法的前提和公式如下，owner 为
[typed operators 的 Exact attribution arithmetic 与 Top-K and reconciliation](2026-09-01-lazy-analysis-typed-operators-design.md#exact-attribution-arithmetic)：

| 从量定义与部件选择的方法 | 必要依据与输出 |
| --- | --- |
| `additive_difference@v1` | 完整可加分区或明确的可加分配；分项 current-baseline |
| `component_mix@v1` | mean/weighted_mean/ratio 的每侧可加 N、W；侧项为 N_i / W_total，贡献为侧项之差 |
| `distinct_membership@v1` | 每侧精确去重的 key→partition 成员关系；一个 key 按其分区数等额分配；目标首次接入不准入 |
| `distribution_shapley@v1` | 分区分布与同一次执行内固定的分位数实现；完整枚举替换 current/baseline 分区分布的联盟；目标首次接入不准入 |

首次接入的 `count_distinct` 和分位数不携带可上卷的成员关系或完整分布；不能从展示值、
lineage 或后端函数名推导归因许可。未来若扩展，
须逐来源证明所需状态的提取、运输和续算后再开放对应方法。

Top-K 是归因计算前对两侧共同 basis 的确定性映射，剩余项进入真实 Other；不是输出 limit。
joint 输出完整轴元组，hierarchy 输出作者轴顺序的各个前缀；不同层级都是独立 resolution，
不能把同一目标的多层贡献混合相加。若未来准入 distinct 或 distribution 方法，前者在
每个前缀重新去重和分配，后者在每个 resolution 重新计算；不能通过对子贡献普通求和
生成父贡献。

未来的 distribution_shapley 仍至多 8 个映射后玩家，精确枚举最多 256 个联盟；不会成本过高就
抽样排列。若执行选择了近似分位数，每个联盟使用该固定实现，并继承其近似披露；
“精确枚举联盟”不把近似分位数变成精确分位数。`residual=0` 只证明数值核对，完整 scope
及部件依据另需成立。分支重叠、未知覆盖或非法时间 fold 不由核对通过而得到许可。

逻辑输入缺少所请求轴时，attribute 可以从仍保留的**观察表达式**显式构造轴展开依赖；
这是用户 axes 请求的一部分，须出现在执行依赖图中。Materialized 输入只消费 retained parts，
不足就拒绝，不能凭 lineage 回读来源。对没有可展开观察定义的派生量也不能假装有该能力。

漏斗保留已有 `FunnelResult.compare` → `FunnelComparisonResult.attribute`，通过闭合重载接入
同一个 attribute 能力：`.attribute(target=mv.funnel_loss_rate(step=...), axes=..., mode=..., top_k=...)`。
漏斗比较包含多个潜在目标，所以此处 target 有意义；普通单量 Difference 无需重复目标。
不新增 loss_rate / conversion_rate 快捷方法；需要单量视图时，用 read 接收已有的具名 selector。
具体漏斗规则由 Event 章节拥有，不将普通比率自动解释成漏斗。现有 component_mix 是按维度
分配比率变化，不是任意公式组件的贡献分解；通用 FormulaBasis 仍须另建完整方法后才开放。

### 7.3 完整子分析的重复有独立边界

“每地区内部的渠道贡献”需要每地区自己的目标与依据，不能仅对全国归因结果分组。
可以复用有类型的分析定义，在有限参数位上重复求值；固定全国参照、参考权重、输入实现
不能随切片悄悄改变。失败切片要保留失败状态。
本设计保留受控 evaluate_each 的理论位置，但不把任意 Python callback、自动规划或
通用程序语言加入核心 DSL。先验证真实重复分析场景，再接受对应封闭模板接口。

## 8. 事件、状态与统计扩展如何接入

下列方法接入理论的共同签名与规则模板；具体定义项、Cell 政策、部件变化与局部保持义务
仍由各方法拥有者负责。理论 §6.7 的有限核心证明不覆盖 matcher、replayer、分配或统计模型。

### 8.1 事件：先构造旅程，再组合领域量

Event matching 是代数 §7.3 的领域构造器，负责建立 occurrence 到 Journey 的匹配；普通
筛选、配对和归约不承担这一步。它的入口仍有单独的 Event 名字空间，但输入成员使用本设计的
AnalysisDomain，结果是具名 JourneyResult。构造、reducer 和后续组合全程 Lazy。

```python
from datetime import datetime, timezone

session = mv.session.get_or_create("event-state-review", report_timezone="UTC")
Customer = ms.ref.entity("sales.customer")
Visit = ms.ref.event("sales.visited")
Paid = ms.ref.event("sales.paid")
Channel = ms.ref.dimension("sales.customer.channel")

VisitBuyer = ms.participant_role(event=Visit, name="buyer")
PaidBuyer = ms.participant_role(event=Paid, name="buyer")
visit_step = mv.step(participant=VisitBuyer, key="visit")
paid_step = mv.step(participant=PaidBuyer, key="paid")
pattern = mv.sequence(visit_step, paid_step)

customers = session.members(Customer)
coverage = mv.BoundedCompletenessDeclarationV1(
    inputs=(Visit, Paid),
    complete_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
    complete_through=datetime(2026, 9, 8, tzinfo=timezone.utc),
    rationale="The retained event extract covers this interval.",
)
july = mv.time_scope(
    start=datetime(2026, 7, 1, tzinfo=timezone.utc),
    end=datetime(2026, 8, 1, tzinfo=timezone.utc),
)
august = mv.time_scope(
    start=datetime(2026, 8, 1, tzinfo=timezone.utc),
    end=datetime(2026, 9, 1, tzinfo=timezone.utc),
)
jul_journeys = session.events.match(
    pattern,
    population=customers,
    cohort_window=july,
    completion_through=datetime(2026, 8, 8, tzinfo=timezone.utc),
    matching=mv.first_per_subject(),
    completeness=(coverage,),
)
aug_journeys = session.events.match(
    pattern,
    population=customers,
    cohort_window=august,
    completion_through=datetime(2026, 9, 8, tzinfo=timezone.utc),
    matching=mv.first_per_subject(),
    completeness=(coverage,),
)
```

两个窗口的持续时间及窗口结束后的随访长度必须满足所选比较设计；以上两个自然月分别为
31 天，随访均为七天。`cohort_window` 选择第一步骤的开始 occurrence；
`completion_through` 是独立的排他随访上界，不是成员窗口，也不是每个开始 occurrence
自动拥有相同长度的相对窗口。相对窗口属于 §8.2，不能从这个绝对上界推导。

这里保留现有 step/sequence/matching 的封闭含义：

| 输入 | 精确含义与边界 |
| --- | --- |
| `mv.step(participant=..., key=...)` | exact Event participant role 加局部唯一步骤名；`key` 是声明，不是结果字段查找；各步骤主体必须是同一 Entity 身份 |
| `mv.sequence(*steps)` | 有序步骤，不接受裸 Event、字符串角色或整数位置代替 typed step |
| `mv.first_per_subject()` | 每主体选择开始窗口中的最早开始，产生至多一个 Journey；它是主体漏斗和完整流失选人的政策 |
| `mv.every_start(completion_assignment="exclusive" \| "shared")` | `completion_assignment: Literal["exclusive", "shared"]`；每个开始 occurrence 一个 Journey；exclusive 将最终完成分给最早合格未完成尝试，shared 允许多个尝试复用最终 occurrence；该选择仅约束最终步骤，中间 occurrence 按已登记算法复用 |

Journey 的实例身份包含开始 occurrence 与确切 pattern/matching 绑定。一人多个 Journey
不会在构造时被主体去重。JourneyResult 保留各步骤 assignment、开始域、参与者映射、顺序
依据、随访及覆盖条件；没有开始 occurrence 的客户不是一条自动补出的失败 Journey。
对全体客户判断“没有访问”须另有完整机会域与覆盖的 cohort，不能取 Journey 域的补集代替。

覆盖沿用已有两个封闭声明构造器，不再增加同义的 coverage helper：

```text
mv.BoundedCompletenessDeclarationV1(
    inputs, complete_from, complete_through, rationale
)
mv.SourceOriginCompletenessDeclarationV1(
    inputs, source_origin_ref, complete_through, rationale
)
```

所有参数均为 keyword-only；inputs 是非空 exact Event Ref 元组，source_origin_ref 是 Datasource Ref，
边界是有时区的 datetime。前者声明有限区间，后者声明从指定来源起源到上界；它们是可追溯的
**来源假设，不是用户签发的证明**。注册来源也可提供真实覆盖依据；解析器核对 exact Event
定义、来源绑定和边界，保留 observed/declared/mixed/unknown 的来源类别。不存在数据行、
最大事件时间或一句 rationale 都不会独立证明完整。缺覆盖时领域方法可输出 Unknown/censoring，
要求完整成员或完整比较的操作则拒绝。没有通用 `complete=True`。

#### 漏斗、期间比较与流失归因

```python
jul_funnel = jul_journeys.funnel()
aug_funnel = aug_journeys.funnel()
change = aug_funnel.compare(jul_funnel)
drivers = change.attribute(
    target=mv.funnel_loss_rate(step=paid_step),
    axes=(Channel,),
    mode="joint",
    top_k=10,
)
result = drivers.execute()
```

`funnel(axes=())` 返回 FunnelResult；可选 axes 是受治理的非时间 Dimension Ref 元组，
按各 Journey 的第一 occurrence 时点取值。只有 first_per_subject 获准主体漏斗；
every_start 的尝试转化是另一估计对象，本设计不偷偷复用主体漏斗的分母。

FunnelResult 保留现有固定计数、三种率及 exact 步骤坐标，不要求作者拿字符串列重新拼漏斗。
现有 cohort/resolved-cohort、entry/resolved-entry、reached/lost 和 coverage-censored 计数
继续以拥有明确含义的字段公开；三种率继续声明其 first/previous 角色和 resolved 分母。
这些 conditional 比率不自动声称覆盖全体，零分母为 Undefined，而非零；也不因显示值相同
就抹去未知/排除计数。此次不增加 counts、loss_rate、conversion_rate 快捷方法。

主要比较链沿用 `funnel.compare(baseline)`，返回固定 FunnelComparisonResult；它保留
现有两侧计数、率、差值、缺失侧状态及步骤坐标。封闭的 funnel-period 规则要求 pattern、matching、主体、
成员定义、Event 定义、分组定义一致，时间域、开始窗口长度和结束后随访长度相容，相关后续
观察完整。FunnelComparisonResult 是具有多个固定角色的领域结果，不是一套不可组合的
通用 Delta 家族。分组域采用完整 outer 配对；缺侧分组只可在已证明该侧完整无此分组时给计数零，
其零分母比率仍无定义。这个规则来自漏斗计数语义，不能运输成所有 NumericRelation 的补零比较。

`change.attribute(target=mv.funnel_loss_rate(step=...), axes=..., mode="joint" | "hierarchy", top_k=...)` 选择唯一注册的
`funnel_ratio_mix@v1`，方法由目标量和部件分派，不让作者手填公式或证明。它绑定 exact 步骤、
两侧 lost/resolved-entry 状态和目标端点，产生公共 AttributionResult；贡献核对与非因果解释
沿用 §7 的规则。维度必须完整分区，层级模式保留每一级的范围和 Other；筛选显示贡献行不改变目标。

逻辑目标尚无 axes 时，领域扩展在图中显式加入“同一已绑定 Journey assignment 的维度分解”
依赖，并核对原目标；不是重做另一套 matching。已物化漏斗或差值缺少必要分项时不得从 lineage
回源。作者可复用保留完整 assignment 的 Journey Artifact，重新构造所需 funnel，然后明确
继续计算。只有 compact 比率值不能获得归因或率上卷能力。

领域结果进入公共单量协议使用已经存在的 read 动词，不另开同义提取方法。例如复用现有
FunnelLossRate selector：

```python
payment_loss = aug_funnel.read(mv.funnel_loss_rate(step=paid_step))
loss_values = payment_loss.execute()
```

这个 read 的精确重载返回 NumericRelation，保留所选步骤、条件分母、窗口、覆盖及必要
计数部件；读取不重新计算或重新匹配。其它计数和率使用 `read` 对结果原有 owned 字段句柄
的精确重载，保留步骤坐标与量角色；不在本文虚构一份新的 selector 工厂清单。若从比较
结果读取某个差值，其定义必须保留有序两侧与原比较设计；拿到普通数值不能获得新的归因
权限。领域完整比较和公共单量 read 分别解决“比较整个漏斗”和“继续分析某一个已有量”，
对同一量不再同时提供 loss_rate()/values() 等重复入口。

#### 指定步骤耗时与选人

```python
pairs = aug_journeys.time_to_event(from_step=visit_step, to_step=paid_step)
completed = pairs.completed()
duration = completed.duration
buyer_binding = completed.subjects(PaidBuyer)
timing = duration.summarize(mv.mean())
slow = duration.where(duration.value.gt(mv.duration(seconds=15)))
slow_customers = slow.members(through=buyer_binding)

drop_truth = aug_journeys.read(mv.dropped_before(step=paid_step))
dropouts = drop_truth.where(drop_truth.value.eq(True)).members(
    through=aug_journeys.subjects(VisitBuyer)
)
```

`time_to_event` 接收保留 pattern 中两个 exact、按顺序排列的步骤；两者可以跨过中间步骤。
EventDurationResult 保留整个原 Journey 域，提供固定的 status、started_at、completed_at、
duration、observed_duration 和 followup_until 关系。status 的闭合变体由领域方法定义；
未进入所选起点、进入后未完成、覆盖不足与已完成不能用一个物理 NULL 混同。
completed() 是在这个结果上选择已知完成子域，输出 CompletedJourneys；duration 为绑定这对
步骤的 DurationRelation。observed_duration 仅表示已观测随访，不是未完成者的完成耗时。
当前方法采用 elapsed 时间；没有默认为营业时间、工作时间或本地日历日。

subjects(role) 返回绑定该具体 Journey/Pair 域的 SubjectBinding，只接受 pattern 已声明的
exact participant role。where 的子域包含关系运输该映射，members 才取其去重主体像。
作为独立反例，若另一个 every_start 输入中 u1 两旅程耗时 10/30 秒、u2 一旅程 100 秒，
其旅程均值为 140/3 秒；先按主体均值再平均为
60 秒，后者另需显式构造。耗时大于 15 秒的成员为 u1/u2，但丢失了三旅程的机会重数。

mv.dropped_before 是已有封闭 selector，只描述请求，不自行求值。
`journeys.read(mv.dropped_before(...))` 消费保留的前序进入、最终 assignment、随访截止和覆盖，
返回 first_per_subject 的 BooleanRelation。通用 where 不能从某一步空 occurrence 推导这些事实。
返回关系绑定“已进入该 pattern 开始域、
截至明确随访上界，在目标步骤前已确定流失”的机会域和主体映射。它不直接选人，也不把尚未
观察完的情况返回 False。普通 where 默认要求判定可决定，因此仍有 Unknown 时拒绝这条
完整成员路径。没有另一套 `select_subjects` 入口；领域负责真值，where 负责子域，members
负责身份像。每次尝试都失败/至少三次失败等问题须用 every_start 的完整机会域和 §6 cohort
量词，不能把 first_per_subject 的修饰词删除后宣称已经支持。

### 8.2 相对观察与留存保留 Anchor

相对窗口绑定 `Subject × Anchor` 的每个锚点，显式区分 elapsed 168 hours 与七个本地日历日。
多锚点窗口重叠时，定义贡献可共享还是独占；不能通过重复行自动做决定。

留存方法先固定 Ω，再形成已知真 K+、已知假 K-、未知 K? 的完整划分。
Ω 的实例单位必须明确为 Subject 或 Subject × Anchor；实例留存与主体留存分别定义，
不能通过 members 去重自动将前者改成后者。
非空 Ω 上给出 `[|K+|/|Ω|, (|K+|+|K?|)/|Ω|]`；这是确定性界，不是置信区间。
100 个目标成员中 25 真、5 假、70 未知，结果为 [25%,95%]；只取完整随访成员另算单点率
改变了目标域，必须显式命名为另一分析。
不能用一个 `drop_unknown=True` 将这两种问题伪装成同一 retention 方法。

### 8.3 状态：有序重放产生可组合的领域视图

StateModel replay 是代数 §7.1 的 ordered fold。它以规范事件历史、初始依据和受治理顺序
生成状态区间、转移轨迹、违规事实及主体状态；通用 summarize 不负责执行转移函数。

```python
Order = ms.ref.entity("sales.order")
OrderLifecycle = ms.ref.state_model("sales.order_lifecycle")
Created = ms.ref.event("sales.created")
Paid = ms.ref.event("sales.paid")
Cancelled = ms.ref.event("sales.cancelled")
Warehouse = ms.ref.datasource("warehouse")
PaidState = ms.model_state(model=OrderLifecycle, name="paid")

orders = session.members(Order)
origin = mv.SourceOriginCompletenessDeclarationV1(
    inputs=(Created, Paid, Cancelled),
    source_origin_ref=Warehouse,
    complete_through=datetime(2026, 9, 1, tzinfo=timezone.utc),
    rationale="The archive includes the modeled events from source inception.",
)
history = session.lifecycle.replay(
    OrderLifecycle,
    population=orders,
    window=august,
    seed=mv.from_inception(),
    completeness=(origin,),
)
checkpoint = datetime(2026, 8, 31, tzinfo=timezone.utc)
is_paid = history.read(mv.in_state(PaidState, at=checkpoint))
paid_orders = is_paid.where(is_paid.value.eq(True)).members()
```

StateHandle 来自 `ms.model_state(model=StateModelRef, name=...)`；名称只引用已声明状态，
由已加载定义验证，不通过 CatalogEntry 链或 authoring 模块取得。`population` 必须是模型的
exact 主体 Entity；read(in_state) 保持整个输入主体域，因此上述 members 无须另给 SubjectBinding。
in_state 复用当前封闭 selector：在确定状态等于所选 ModelStateHandle 时为 True，在确定其他
状态或 NotStarted 时为 False。NotStarted 仅在完整历史证明无模型起源时成立，不是把 initial
state 填在窗口起点。覆盖不足产生 Unknown；有后续 modeled trigger
却在完整历史中缺失必需 inception 仍是模型/历史错误。普通 where 对 Unknown 的拒绝保证
这条时点选人路径具有完整成员真值，不静默抛弃未知主体。

in_state 与 distribution 的检查点限于输出窗口的 `[start, end]`。内部时点按规范历史取得
相应状态；`at == end` 读取排他上界前的左极限，恰好发生于 end 的事件不进入本次历史。
它是这个有界 HistoryResult 的明确边界规则，不能冒充已经读取 end 之后事件的状态观察。

`history.read(mv.in_state(...))` 消费已保留的主体分类与区间边界，返回 BooleanRelation；
in_state 只描述状态与时点请求，不自行求值。原始区间的 where 无法为没有区间的主体产生
NotStarted，也不能推导覆盖未知或排他终点状态。领域负责这些判断，之后复用 where/members；
不另加 state_at、state_members 或另一套选人入口。

`from_inception()` 保持现有确定含义：从真实 inception 开始重放，哪怕它早于输出 window；
window 只裁剪报告范围。有限区间覆盖不足以证明没有更早 inception，来源起源依据不得由
bounded declaration 冒充。缺少初态依据不会自动假定 initial、取最近状态或重新读另一个来源。

HistoryResult 的公开视图与固定摘要如下。方法仅消费这次保留的部件，不再次匹配或回放：

| 入口 | 输出、行单位及当前能力映射 |
| --- | --- |
| `read(mv.in_state(state, at=instant))` | 主体域上的 BooleanRelation；复用现有 selector 并承接当前 select_subjects，统一使用 where→members |
| `distribution(at=(...), axes=())` | StateDistributionResult，行单位 Checkpoint × ModelState × AxisGroup；固定字段 known_state_count、seeded_subject_count、coverage_censored_count、share_among_seeded，分别为有精确支持定义的 NumericRelation |
| `transitions()` | TransitionSummary，行单位模型声明的 TransitionPair；count 与 share_of_modeled_transitions 为固定 NumericRelation；完整目标 pair 域保留合法零次迁移 |
| `violations()` | ViolationResult，行单位 TriggerOccurrence；保留 trigger、发生时点、state_at_event、kind 和实际主体映射，可 where 后 members |
| `intervals()` | StateIntervalResult，行单位 Subject × StateInterval；保留 state、start/end、observed_duration、left_clipped、终止/覆盖状态及主体映射 |
| `dwell()` | DwellSummary，行单位 ModelState；固定计数为 interval/completed/right_censored/coverage_censored/left_clipped_completed，时长字段为 completed-window-fragment 的 mean/median/p90 |

distribution 的 Dimension 在每个 checkpoint 取值，不沿用 Event funnel 的入口时点。
`share_among_seeded` 的分母是本时点已确定进入模型的主体，明确承接当前 conditional share；
它不声称是包含覆盖未知主体的全体状态份额。模型全部状态是目标 state 域，零成员状态仍产出
计数零；尚未入模的 NotStarted 与覆盖未知各自保留状态，不能算入任意已声明状态。
axes 的目标分组域与空组规则由该分布方法固定，不因图表需要自动生成不存在的地区。

transitions 的计数必须消费 canonical 迁移轨迹，包括自循环和零时长转移；当前结果的
share 分母是同一窗口全部 modeled transitions。统计得到的迁移占比是描述量，不能自动
解释为马尔可夫转移概率。只保留连续状态区间时，这个方法因缺部件拒绝；不能由状态变化
次数或重新回放填补消失的触发事件。

```python
distribution = history.distribution(at=(checkpoint,))
transition_counts = history.transitions().count
dwell = history.dwell()
state_fragment_mean = dwell.mean_duration

violations = history.violations()
illegal = violations.where(violations.kind.value.eq("illegal_transition"))
affected_orders = illegal.members(through=violations.subjects())
```

上述固定字段是该具名结果的类型成员，不是任意字符串列访问。它们可进入共同 where、
summarize、compare 等已获准方法，但定义相容和部件要求仍分别检查。TriggerOccurrence
不是 Order；violations.subjects() 返回这次违规域到唯一模型主体的实际映射，再由 members
去重。汇总后的 TransitionPair/ModelState 结果不默认保留原主体名单。

dwell 明确保持当前 `completed_window_fragment_duration@v1`：先按输出窗口裁剪状态区间，
再对在窗口中已结束的片段统计；left-clipped completed fragment 会参与，right-censored
与 coverage-censored 不进入三个完成时长统计。它既不是完整生命周期时长，也不是所有
区间的观测占用时长；摘要同时披露各类计数，不能只展示均值而改变估计对象。
若问题是“所有已观察区间占用多久”，使用 intervals().observed_duration 对当前区间做
明确统计；若问题是“从进入到离开状态的完整时长”，还须选择两端完整的区间并保留边界依据。
普通 DurationRelation 不能抹掉这些不同的定义。

时长均值/分位数与迁移计数的后续上卷各自需要状态：不能相加 state mean 或平均 state p90
恢复总体分布。若当前 DwellSummary 只保留统计值，允许基于当前状态行做新的统计，不允许
冒充 interval 层面的原量 rollup；要继续精确上卷必须保留对应 count/sum 或完整分布部件。

同刻事件的顺序不能仅由 occurrence ID 稳定排序取得。拥有者只接受有业务依据的顺序，或在
所有仍可能的顺序中证明请求结果及其保留轨迹/违规等相关部件不变；验证结论绑定 exact
输入与方法。其内部有序扫描可按合法顺序实现，但不能把实现顺序运输成业务先后证据。
分段续接另需扫描边界状态；把两个 HistoryResult 作为无序 bag 相加不构成 replay。

### 8.4 统计方法复用现有入口


这些方法接入共同域、量定义和 Cell 状态，不由共同代数证明统计有效性。保留已有方法名称、
阈值/范围参数和注册方法版本；不增加通用 statistics、search 或 model namespace。
方法都返回 Logical 结果，where、具名数值视图与 rank 可继续构造表达式，最后一次 execute
统一执行。固定结果对象将多个有不同含义的量分开，避免恢复任意字段字典。

#### 8.4.1 候选发现

保留非 callable 的 `.discover` 命名空间，以及现有五个方法：

| 调用 | 准入与注册方法 | 固定结果变体 |
| --- | --- | --- |
| `history.discover.point_anomalies(threshold=3.0, limit=50)` | 时间量；`point_zscore@v1` | 点候选，保留时间点、观察值、基准值、偏差 |
| `history.discover.interesting_windows(threshold=2.0, limit=50)` | 时间量；`global_zscore_runs@v1` | 窗口候选，保留 start/end、点数、峰值 |
| `change.discover.period_shifts(threshold=2.0, limit=50)` | 保留配对时间轴的 Difference；`delta_window_zscore@v1` | 变化窗口候选，保留两侧时间、窗口宽度 |
| `values.discover.entity_outliers(threshold=3.0, limit=50)` | Entity 单位的观察量；`entity_mad@v1` | 主体候选，保留原完整业务身份、观察值、基准值 |
| `change.discover.driver_axes(search_space=(Region, Channel), limit=50)` | 可加 Difference；`axis_concentration@v1` | 维度候选，保留实际 Dimension Ref、基数、集中度 |

CandidateResult 是一个固定家族，方法返回相应闭合变体，不创建可任意装字段的 mega-class。
各变体都有 `.score: NumericRelation`，以及固定的 reason_codes、search_summary；数值视图
仍保留候选类型和实例单位。score 是方法分数，不是概率、置信度或因果贡献。

```python
outliers = aug.discover.entity_outliers(threshold=3.0, limit=50)
selected = outliers.where(outliers.score.value.gt(4))
customers_to_follow = selected.score.members()
result = customers_to_follow.observe(
    Revenue, during=september, via=Buyer
).summarize(mv.mean()).execute()

drivers = change.discover.driver_axes(search_space=(Region, Channel), limit=10)
result = drivers.score.rank(order="descending", ties="ordinal").execute()
```

主体候选的行保留原 Entity 身份，所以这里可直接 members；点、窗口、维度候选不能用
members 冒充客户名单，也不能把分数当业务 Metric 的可上卷状态。选出维度只是线索；应将其
真实 Ref 作为明确的后续 attribute 输入，不自动执行搜索后的因果解释。

现有闭合算法保持：点异常以有限完整值的总体均值/标准差形成绝对 z 分数；窗口为超过阈值的
最大连续段，缺口与不可用点打断；period_shifts 使用 `max(7, floor(n/10))` 个连续配对点的
完整滑窗均值，再标准化并查找连续段。entity_mad 使用中位数，scale 为 `1.4826×MAD`；MAD
为零时用围绕同一中位数的平均绝对偏差，这是该注册方法的一部分，不是运行失败后的降级。
driver_axes 只搜索明确给出的合法完整分区，以达到绝对变化总量 50% 的最小前缀规模衡量集中度，
score=`1/(prefix_count + cardinality/1000)`；净变化为零不等于绝对变化总量为零。

threshold 须有限且大于零，limit 为 1..1000。方法保留 searched/evaluated/excluded 状态和数量，
无可评估点、零离散度等不能包装成“已经证明没有异常”。Null、Undefined、Unknown 的不可用
依据分别保留，禁止通过丢弃状态产生普通 Null。driver 的逻辑轴展开和物化边界与归因一致。
方法定义由现有 candidate_contracts、candidate_values、entity_candidate 与 driver_values 的
上述版本拥有；不开放自定义打分回调或统计显著性宣称。

#### 8.4.2 相关

```text
left.correlate(
    *others: NumericRelation,
    method: Literal["pearson", "spearman", "kendall"] = "pearson",
    lag_range: range | None = None
) -> LogicalAssociationResult
```

保留 correlate / method / lag_range；唯一必要的输入形状调整是显式传其他单量 Relation。
旧入口在一个多量 Dataset 中取 2–16 个量；目标取消该容器后，接收者加 others 提供同样的
2–16 个互异量，不增加 table.correlate 或另一组 correlation 工厂。方法计算完整两两组合。

```python
associations = weekly_revenue.correlate(
    weekly_orders, weekly_visits, method="spearman", lag_range=range(-2, 3)
)
selected = associations.where(associations.selected.value.eq(True))
result = selected.coefficient.rank(order="descending", ties="ordinal").execute()
```

每个输入必须有可验证的共同观察实例/坐标和相同完整域；没有隐式 inner join。Entity 或分类域
以其行作为统计单位，时间量按时间点配对，分类×时间分别在每个分类序列内计算。显式 lag 只
接受有完整时间坐标契约的输入；+k 表示左侧 t 与右侧 t+k。输入顺序确定有序 pair 与 lag 方向。

沿用现有 Pearson、平均秩后 Pearson 的 Spearman、Kendall tau-b。每个 pair/lag 保留 matched、
Null 和 complete-pair 数量；完整对不足或常量序列具有明确状态，不生成 NaN 冒充系数。
Unknown/Undefined 不能被当作普通可忽略 Null。每个 pair/序列至少一个有效 lag，否则按现有
owner 拒绝。所有候选 lag 和状态均保留；selected 选择最大绝对系数，依次用最小绝对 lag、
最小有符号 lag 破除并列，不把选择结果当显著性检验。

AssociationResult 固定 `.coefficient: NumericRelation`、`.selected: BooleanRelation`，并保留
输入 pair 身份、lag、配对计数、状态与 search_summary。筛选不重估系数或重选最优 lag；
系数的普通 summarize 只能形成“这些系数的描述统计”，不等于合并原观测后重新算相关。
准入版本和配对/选择公式沿用 association_contracts 与 association_values 的现行 owner；
不提供 p-value、总体显著性、因果方向或基于最优 lag 的效果解释。

#### 8.4.3 预测

沿用原签名和已有封闭模型值：

```python
forecast = history.forecast(
    horizon=mv.periods(4), model=mv.seasonal_naive(periods=12), interval_level=0.95
)
selected = forecast.where(forecast.prediction.value.gt(0))
result = mv.table(
    prediction=selected.prediction,
    lower=selected.lower,
    upper=selected.upper,
).execute()
```

模型仅为 `mv.naive()`、`mv.drift()`、`mv.seasonal_naive(periods=...)`，默认 naive。
horizon 是 `mv.periods(1..1000)`；level 为有限 `(0,1)`。输入是一个时间量或分类×时间量，
每个序列具有相同完整、连续历史坐标及获准的未来网格；Null、Undefined、Unknown、非有限值、重复、部分或缺失
桶都拒绝，不自动插补。最短历史分别为 2、3、seasonality+1。

返回 `LogicalForecastResult`，固定 `.prediction`、`.lower`、`.upper` 数值 Relation 视图，
在同一个未来时间域上；另保留训练行数、模型、区间定义、条件及输入绑定。where 同步限制
这些视图，prediction 可复用 rank。预测值属于 ModelPrediction，不是 Observed；区间边界
属于该注册方法的 PredictionIntervalBound，不借现有 NumericRelation 自动获得可加总性。
对 prediction 的合法行统计只是预测值的描述统计；不能相加逐点 lower/upper 得到总量区间。

沿用 [typed operators 的 Forecast contract](2026-09-01-lazy-analysis-typed-operators-design.md)
中现有 `normal_residual@v1` 的三种点预测、创新、自由度与随 horizon 变化的方差公式，
包括 naive/seasonal 不对创新去均值、缺失方差不能变成零宽区间的规定。该方法产生未来观测的
名义预测区间，不是均值置信区间；其独立假设为零均值、不相关、有限恒定方差的创新及正态
预测误差，drift 还传播平均增量估计的不确定性。有限且完整的历史不证明这些假设或实际覆盖率。

这是一项已有完整 owner 的模型扩展，符合理论 §1.3/§7 的接入边界；不能因为共同代数没有
证明预测覆盖率就删掉其入口，也不能因为可 execute 就宣称实际校准成立。没有自动模型选择、
bootstrap 区间或从历史评分推导未来保证。

## 9. 执行与结果的公开契约

### 9.1 惰性、固定输入与合法续算

组合操作消费 Logical 或 Materialized，均产生新的 Logical；只有 `.execute()` 发布固定结果。
members、read、observe、where、compare、group_by、summarize 和 rollup 的构造不读取业务数据。
字段句柄、域引用及纯契约检查不触发求值；需要实际数据的条件由执行时履行。

一次 execute 中，同一个显式 Logical 节点只有一份实现，所有消费者共享它；分别构造的
同形节点不自动合并。运行内共享不保证跨次冻结，也不保证独立来源处于同一事务快照。
含 Lazy 来源依赖的每次获准顶层 execute 都有独立的求值身份；分析定义不因执行次数而改变。
同一个 Lazy 对象在来源变化前后重复执行，产生各自固定且可精确恢复的 Artifact，后一次
不能命中或覆盖前一次结果。仅由显式 Materialized 输入派生的纯本地表达式，可以复用同一
Session 中相同固定输入及方法版本的精确已提交结果；命中不建立新 Run。这一区分不增加
公开执行参数，内部绑定与持久化义务见
[实现架构 §5.2](2026-09-24-marivo-analysis-dsl-architecture-design.md#52-定义身份求值身份与持久化)。

需要跨次固定输入时显式物化，例如：

```python
saved_revenue = customers.observe(Revenue, during=august, via=Buyer).execute()
positive = saved_revenue.where(saved_revenue.value.gt(0)).execute()
```

Materialized 固定已提交的数据、域绑定和部件；其值运算不能隐式回源补数据或重算旧输入。
对固定成员再次 observe/read 引入新的来源依赖，其可准入性由执行配置决定；首轮 MVP
拒绝显式物化与现场查询混合。保留 Lazy 选择并在一次 execute 内继续观察仍属于来源图。
历史 Artifact 的存在不会自动替换来源型 Lazy 输入。分别执行得到的 Materialized 端点仍按
所选方法的域、定义与实际输入绑定判断能否比较；默认 TimeChange 仍要求同一目标成员的
显式实现绑定。独立执行中重复引用同一个 Lazy 成员对象不构成这种共享，相同键集合也不
足以替代它；求值身份不同本身同样不是所有方法的统一拒绝条件。

结果只承诺当前定义、域与保留部件支持的续算集合 K；冷恢复必须保持该承诺，不能只有
展示值相同或恢复前后都拒绝就算通过。完整目标的具体公开方法见前文，首轮 K 见
[MVP 第 2.3 节](2026-09-24-marivo-analysis-dsl-mvp-validation-plan.md#23-物化后的能力承诺)。执行分类、共享和恢复义务见[实现架构](2026-09-24-marivo-analysis-dsl-architecture-design.md)。

### 9.2 为 Agent 暴露问题含义及下一步

公开结果提供有界单行 repr、确定性的 show、随当前状态生成的 contract。
一个客户均值结果至少应说明：

```text
Unit: Singleton
Value: mean of customer revenue
Input unit: Customer
Input domain: customers selected by negative August-vs-July revenue change
Observation: September
Weighting: one vote per selected customer
Validity: defined, with the stated source assumptions
Retained: row-statistic state; no customer membership map
Next actions: supported scalar comparisons and result inspection
```

静态 API、参数与导航归 live Help；当前值/有效性归 show；合法续算归 contract；
具体修复归结构化 AnalysisError/SemanticError。错误给出 expected、received、可执行修复，
例如“缺少原量组件状态；使用已保存的完整观察，或显式建立新观察”。
不建议静默删键、补零或自动回源。

新的分析域、分析关系和设计变体要有具体类型，不能以 `Any` 或大量可选字段容纳所有形态。
公共导出、Help、示例和能力预算统一维护；不把内部映射/证明类型全部加入顶层发现目录。
公开 contract、错误和日志不输出完整主体键或仅由主体键计算的摘要；必要成员检查在私有执行中完成。

### 9.3 保留运行与证据能力，不把它们误作代数算子

Session、Run、Artifact 是执行所有者；其身份、恢复与证据接口继续存在。
它们不改变量的数学含义，也不因新 Relation 名称而另建一套运行系统。

| 能力 | 保留入口 | 与新 DSL 的连接 |
| --- | --- | --- |
| 创建与恢复分析上下文 | `mv.session.get_or_create(...)`、`resume(...)`、`current()` | 继续使用精确 Session 身份；构造分析表达式不等于执行业务查询 |
| 有界历史与故障处理 | `mv.session.recent(...)`、`inspect(...)`、`abandon_run(...)`；`session.runs(...)`、`get_run(...)`、`graph(...)` | 保留现有明确副作用边界；读取历史不激活或重放分析 |
| 参数化来源绑定 | `session.source_bindings(bindings)` | 上下文内构造的来源节点捕获非秘密参数；退出后 execute 仍使用所捕获绑定，不能读取届时的环境作为新问题 |
| 精确恢复与校验 | `session.artifact(reference)`、`revalidate(reference)` | 恢复已提交的具体 Materialized 变体、定义与部件；不从显示表猜回可续算类型，不隐式回源补齐 |
| 结果与证据读取 | `show()`、`to_pandas()`、`evidence_digest`、`findings(...)`、`finding(...)` | 读取本次已提交结果和有界证据；DataFrame 是隔离导出，不回灌为受治理分析值 |
| 当前合法续算 | `contract()` 与结构化错误 | 按当前类型、定义与保留部件披露；不因属于 NumericRelation 就承诺任意归因或上卷 |

上述现有入口的参数、返回类型和副作用仍以其 owning contract 为准；这里只改变分析结果变体的
衔接，不设计别名或兼容读取。Artifact 恢复返回封闭的已物化结果联合；使用具体能力前按结果 kind
缩窄，不能将所有结果标为 NumericRelation。公共 Help 继续负责精确签名。
source_bindings 中的物理参数不是语义 Ref；它们仍由 datasource 的类型与秘密处理规则约束。
恢复时若某种 codec 不能保持预先承诺的 K，则拒绝恢复该可续算形态，不能悄悄降级为只有显示值的表。

## 10. 与现有 Marivo 的差异及接受路径

### 10.1 逐项覆盖：核心派生、领域方法与执行服务

对照基线为 2026-09-24 checkout `bf14862fbf5ae3dd7038273e1cc3a95a75d49d34` 的公共导出、
方法实现及 live Help。下表判断目标契约是否有代数依据，不声称新签名已经实现或各后端已经验收。
“符合”要求输入/输出、域与对应、量定义、Cell 政策、必需部件和信息变化明确；只有方法名称
或“理论上可以组合”不能算覆盖。

| 现有表达能力 | 代数依据（理论文档章节） | 目标中的唯一承接位置 |
| --- | --- | --- |
| 成员选择、历史选版、稳定/历史属性 | §4 实例域与输入绑定、§6.3 取值 | §5 的 members/read；选版与观察时间分开 |
| 单/多根观察与分析期指标 | §3.2 贡献图、§8.1 observe、§9.1 Expr | §5 的 observe 与已有 runtime_metric；Ref 是语义叶子，不要求临时公式持久注册 |
| 多维、贡献维度、Entity × Time、多量画像 | §4.3 贡献归属、§4.4 域、§6.3 取值 | §5 的坐标构造与已有 Relation 组合；不以多值属性冒充单值 read |
| 当前行统计、部分坐标消去与时间粗化 | §6.5 两种归约、§6.7 选定规则、L2/L8 | §5 的 summarize/rollup；分组或坐标参数明确输出域；超出有限核心的方法独立定义 |
| 日历、活动期间、累计观察 | §4 范围身份、§7.1 有序规则 | §5 的既有期间/粒度能力及累计规范图；不新增同义时间 helper |
| 绝对/相对变化、窗口对齐、缺侧比较 | §5.1 RelativeChange、§8.2 配对设计 | §6 的 compare 及封闭变体；不把严格配对当作唯一可定义的方法 |
| 排名、平局、分区排名、有序前缀 | §6.2 域限制及具名有序方法 | §7.1 的已有 rank/limit；删除原草案的 order_by 同义入口 |
| 可加、组件、去重、分布与漏斗归因 | §7.3、§8.4 注册分配 | §7.2 的 attribute；首次接入的去重与分位数缺少充分状态，拒绝对应归因；不另加 decompose |
| 异常、窗口变化与驱动维度发现 | §6/§9.3 加有限候选搜索方法 | §8.4 的已有 discover 方法；搜索空间、分数及候选单位明确 |
| 多量相关、lag 与预测 | §7.3 方法扩展 | §8.4 的已有 correlate/forecast；独立方法义务继续由拥有者承担 |
| 精确/近似分位数 | §5.3 状态、§6.5 方法、§9.4 数值边界 | runtime_metric 定义 q 及精确／近似聚合种类；观察不可覆盖；首次接入只支持直接观察，不承诺原量上卷 |
| 事件匹配、漏斗、步骤耗时、流失选人 | §7.3 领域构造、§8.1 主体像 | §8.1 的 match/funnel/time_to_event 与 where→members；保留匹配/覆盖规则 |
| 状态重放、分布、迁移、违规、停留、时点选人 | §7.1 ordered fold、§10 信息变化 | §8.3 的 replay 与领域视图；时点状态关系经 where→members 继续观察 |
| Lazy、物化、冷恢复与合法续算 | §9.1 显式依赖、L6/T1 | §9 的同一 Runtime/Artifact；不建设第二套存储或 AST |
| 来源参数、运行历史、证据读取与 Help | 执行服务，不是值代数算子 | §9.3 保留现有入口；不因未列入生成规则而删掉 |

其中相关、预测、归因、候选发现、事件匹配都属于**符合扩展边界、须履行独立方法契约**。
这与仅凭核心公理就证明该方法正确不同；但也不能因它不是核心生成规则而判为不符合。
目标类型换名、参数缩窄为 Ref 和调用链重组不要求旧表面语法继续存在。

### 10.2 不予原样沿用的行为

以下单独列出冲突条件，不把整项业务能力一起删除。现有实现事实和禁止的迁移解释分开记账；
本表不是对当前所有后端行为已经完成动态证伪的声明。

| 项目 | 与代数的冲突 | 目标处理 |
| --- | --- | --- |
| 仅凭 occurrence ID 建立同刻业务顺序 | §7.1 要求业务依据或相关结果对所有允许顺序不变；可重复排序本身不提供这两者 | 不运输无依据的 ID 顺序为业务证据；补业务顺序依据，或按真实未决顺序验证结果与保留部件不变 |
| 把运行时比值的零分母存储 NULL 当作理论 Null | §5.2 将来源缺值 Null 与计算无定义 Undefined 分开 | 保留已有 zero_division 参数，并在解析时映射到 §3.4 的零分母政策；null 输出 Undefined(zero_denominator)，error 拒绝 |
| 缺侧被自动改成交集或无条件补零 | §4.3/§8.2 把 exact、outer、intersection 定义为不同方法；缺行不是 Cell，也不自动是空贡献 | 保留显式缺侧方法；只在对应主体/组、覆盖与指标空贡献规则都成立时产生合法零值 |
| 将已解决/已入模子总体的比率称为全体比率 | §4.1 与 §8.5 要求明确目标域 Ω，不能排除未知后保持原问题名称 | 漏斗与状态份额保留真实条件分母及未知计数；不能把 resolved/seeded 统计升级为全体覆盖 |
| 以稳定排序、数值守恒或近似容差证明业务条件 | §9–11 分别约束顺序、数值核对、身份与证据 | 归因 residual=0 不代替完整分区；容差不判断身份；近似结果保留自己的方法版本与误差边界 |
| 只有展示值仍承诺原量上卷、主体恢复或完整迁移统计 | §5.3、§10、C1 规定 RequiredParts；区间不等于 canonical 轨迹 | 如实保留所需状态/映射/轨迹，否则拒绝；不从 lineage 隐式重新读源、匹配或回放 |
| 无条件将当前行 where 改写成贡献分支过滤 | §6.2 与 L1 保持原量范围；L9 只证明固定组与原状态上的限制规律 | slice 与 where 保留各自含义；只有另外证明定义、部件、参照及拒绝条件保持，才能采用对应替换 |
| 把相关、归因贡献、预测区间解释成因果性或普遍统计保证 | §1.3/§7.3 明确这些结论需要独立模型与假设 | 保留计算及其方法说明，不赋予未建立的统计或因果含义 |

第一项是**业务顺序前提的所有权需闭合**，不是已证实所有当前结果错误。
[现有 Event/Lifecycle 规范](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md)确实要求
governed occurrence identity order；[Event 排序实现](../../../marivo/analysis/compiler/event.py)与
[Lifecycle 同刻检查](../../../marivo/analysis/compiler/lifecycle.py)使用该前提。
只有唯一键声明、没有业务排序授权时，不能把这个实现顺序运输成理论 §7.1 要求的依据。
反例是同刻两个 X 均可触发 A→终态 B：换序可以保持终态和违规总数，却交换哪一个 exact
occurrence 是合法转移、哪一个触发终态违规。因此检查还要覆盖承诺的轨迹/违规身份，
不能只比较最终状态。这里未对后端运行该反例，不宣称已完成动态证伪。
其余各项列出的是不能携带进
目标设计的解释或条件缺失，并不表示现有代码在所有相应场景都会这样处理。
特别是受指标规则约束的空贡献零值、显式近似分位数和条件分母统计，满足前提时仍符合代数。

### 10.3 尚未建立完整方法，不判作代数冲突

通用 bootstrap、总体估计、因果识别、生存分析、马尔可夫推断，以及通用批量程序求值，
目前只有部分组合边界；它们不属于本轮已核实的现有公共 DSL 表达能力。
相对锚点留存及营业/工作时长也不能仅因语义层存在时间声明，就当作当前 Analysis 已有完整接口。
本轮不为它们发布占位工厂；§8.2 保留已有理论说明，后续须先补方法与领域定义。
这与第 10.2 节的已知冲突条件不同，也不影响本表中已具备明确方法的能力被纳入。

### 10.4 结构差异与接受路径

以下依据当前 owning docs 与实现观察，目的在于标出改动，不让现状决定目标语言。

| 当前结构 | 目标设计决策 | 必须另行完成的工作 |
| --- | --- | --- |
| Entity / Dimension / Measure / Metric / Relationship / Event / StateModel | 保留业务概念、声明和装饰器边界，重新明确贡献图与变量角色 | 逐条接受时间/值政策等元数据与具体函数体形状 |
| 现有 builders 的规范图与受限装饰器计算体 | 装饰器显式声明业务规则；已有组件 builders 从参数形成规范图 | 接受声明接口、受信依据、值政策执行及缺状态拒绝边界 |
| 已有纯 Ref 与 CatalogEntry 分工 | 继续 Ref-only 传递，不增加可调用 Ref 或定义对象参数 | Analysis 签名、错误、Help 和示例同步使用具体 Ref kind |
| Population 与多个 Dataset 家族 | AnalysisDomain 对应分析域，AnalysisRelation 对应完整语义关系；常用单量形状如 NumericRelation | 决定具体公开类型、封闭变体和一次切换边界 |
| observe 参数分别表达 population、scope、axes 等 | `members.observe(metric, during=..., via=..., coordinates=...)` | 接收者绑定域，每个量独立绑定窗口；分组/时间网格使用类型化句柄 |
| Metric / Delta 的续算方法按家族注册 | 按量定义、域、部件准入；差值自然可再比较 | 不靠任意 `.values()` 才能进入新的可组合世界 |
| 聚合/上卷契约与当前实例统计的需求并存 | summarize 与 rollup 为两个明确问题 | 统计量定义、状态 codec、量权重和拒绝边界 |
| Event / Lifecycle 自有 reducers 与选人 | 保留领域生产者，复用共同输出与量化规则 | 不以共享协议重写领域匹配、覆盖或顺序判断 |
| 逻辑/物化、Artifact、所有权、Help 已存在 | 保留执行边界，重做必要语义接缝 | 不建设平行 Runtime、第二个 store 或第二套 AST |

接口取舍必须能解释必要性：

| 当前或前稿入口 | 本文的唯一目标路径 | 是否增加重复入口 |
| --- | --- | --- |
| 当前 runtime_metric / discover / correlate / forecast | 保留操作名和已定义方法；调整单量 Relation 必需的输入与输出形状 | 否；不另加 derive、statistics 或 model 工厂 |
| 旧 quantile_metric | 删除工厂及输入类型，无兼容别名；精确／近似由 Metric 聚合定义决定 | 否；observe 不接受 accuracy 或 method |
| 当前 with_dimensions、aggregate、rollup 的轴参数 | observe(coordinates=...) 构造；group_by 指定保留坐标后 rollup | 替换后的同义入口不并存；已有量的当前行统计仍为 summarize |
| 前稿 window、weeks、order_by | 现有 time_scope、一个 time_grid、现有 rank | 删除同义便利入口；不发布多套时间格或排序工厂 |
| 当前粒度与范围值 | 新增 time_grid，把两者绑定为带身份与逐格窗口的域 | 只补原值无法表达的有限坐标域，不替代 grain/time_scope |
| 成员版本的窗口结束前选择 | members(at=...) 加 TimeScope.before_end 边界视图 | 只补版本选择含义；入群谓词继续 read→where→members |
| 当前 dropped_before / in_state / funnel_loss_rate selector | 领域结果 read(selector) 的精确重载 | 复用 selector 和 read，不再另设同义领域快捷方法 |
| 当前 funnel compare/attribute | 保留领域 compare 与含 target 的 attribute 重载 | 不另造 EventDelta 计算语言；归因结果复用公共形状 |

设计接受顺序：

1. 冻结三层对象边界、分析域/量定义/分析关系的区别、计量/统计两种归约，并用[验证文档附录 A 的判别例](2026-09-24-marivo-analysis-dsl-mvp-validation-plan.md#附录-a-完整目标语言的验证案例)评审。
2. 选择一条端到端语言切片，逐项区分理论 §6.7 的直接采用规则与扩展规则，明确各自完整契约和拒绝边界，以及哪些现有公开形状替换、哪些领域视图保留。
3. 在唯一 owning specs 中接受具体签名、字段、错误、保留部件和 Help；随后才能实施。
4. 以固定输入进行逻辑/物化/冷恢复验证，再按实际方法做后端准入；未验证的后端保持缺口。

若采用目标设计，应形成一次清楚的公开契约，而不是长期保留 Population/AnalysisDomain、
Dataset/AnalysisRelation、旧新 compare 两套同义入口。具体切换不在本文自动授权范围内。

依据与定位：

- [Semantic overview](../../specs/semantic/overview.md) 与 [Semantic object model](../../specs/semantic/semantic-object-model.md)：当前业务对象、身份、版本和计算所有权。
- [Analysis design](../../specs/analysis/python-analysis-design.md)：当前执行与结果边界。
- [Observation model](2026-09-01-lazy-analysis-observation-model-design.md)：多事实、成员/观察分离和贡献场景。
- [Subject / Event / Lifecycle](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md)：事件与状态领域事实。
- [Observe / Compare / Attribute 提案](2026-09-23-observe-compare-attribute-enhancement-design-and-plan.md)：分区完整分析、公式组件、单位统计和固定参照需求；仍属 proposed。

## 11. 接受与交付边界

本文给出完整目标接口与方法条件；公开契约接受按第 10.4 节推进。实现架构承担执行及
语义保持义务，MVP 文档选择可实施切片并记录验收证据。接口示例、历史签名原型或文档
检查均不能作为真实数值执行、物化恢复、后端资格、迁移或发布已完成的证据。
