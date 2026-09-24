# Marivo Analysis DSL MVP 实现验证

Date: 2026-09-24

Status: proposed；目标契约尚未接受为公共 API，尚未实施或通过新 DSL 的端到端验收。

本文选择首轮可实现的接口切片，以独立预期检验实现架构、组合正确性和后续扩展代价。
所有 Python 片段均为目标语法或验证草案，不是当前版本的可运行用法。

| 文档 | 唯一职责 |
| --- | --- |
| [语义层与 DSL 接口设计](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md) | 业务声明、公开对象、签名、方法语义及用户可见结果契约 |
| [DSL 实现架构设计](2026-09-24-marivo-analysis-dsl-architecture-design.md) | 基础能力、语义转换、执行放置、数据交换、物化及运行义务 |
| [MVP 实现验证](2026-09-24-marivo-analysis-dsl-mvp-validation-plan.md) | 首轮范围、接入位置、独立预期、验证矩阵、实施阶段和验收证据 |

理论依据：[分析组合的语义与代数契约](2026-09-23-analysis-algebra-theory.md)（v0.5）。
理论有限核心、完整目标接口与首轮实现切片范围不同；未纳入 MVP 不等于从目标语言删除。
本文档组重构不修改生产接口、AGENTS.md 或 packaged skills，也不授权公开接口切换与发布。

## 1. 目标与交付证据

交付有限语义内核、Ibis 来源适配、pandas 物化续算、一个真实 Python 数值方法、统一交换与
恢复实现，以及端到端验收记录。业务案例不能成为基础设施的特殊分支；DuckDB 是首个
真实来源适配器，不能成为 DSL 的语义定义或 Artifact 本地计算引擎。

实现原则与基础能力以架构文档为准，公开签名与方法含义以接口文档为准。验证必须回答：
算子是否复用基础能力、下推与本地阶段是否保持语义、交换与冷恢复是否保留 K，以及新增
方法和后端所需改动是否可定位。公开 API 切换和发布不属于本轮自动授权范围。

## 2. 首轮方法范围与后续扩展

### 2.1 首轮实现

基础能力面向受治理 Entity、关系和 Metric 定义实现；Customer/Order 仅是夹具，不是内核类型。
首轮准入非版本化单列整数/字符串身份、多对一路径、分类属性、固定半开事件窗口：

- session.members、分类 read；成员域自身 Dimension Ref 或 CategoryRelation 分组，
  结果上使用已保留坐标；显式 groups 保留合法空组。
- 装饰器 Metric 显式声明 unit/additivity/时间和值政策，不解析 body 推导规则；
  sum、身份 count 及显式组件 ratio 的注册方法与真实状态；显式 via/routes、多个单值贡献坐标。
- 严格数值/分类 where、is_defined、all_of/any_of/not_；Observed 端点绝对时间 compare，
  完整同键；Lazy Entity members 后新观察，显式物化成员的混合来源读取不准入。
- 当前行 summarize(sum/count/mean) 与原状态 rollup。DSL count 数当前行，不偷换为
  理论核心中消费 Defined 数值的 count；mean 的统计单位与输入域独立记录。
- 一个真实 Python 数值方法：两个 NumericRelation、同一 Entity 域、无 lag 的 Spearman；
  复用既有相关方法 owner，返回固定 AssociationResult 与 coefficient 视图。
- 上述方法的 Logical/Materialized 构造、执行、结果披露、pandas 续算和冷恢复。

源数值首轮准入 int64/float64；count 输出 int64，mean/ratio/coefficient 输出 float64。
整数 sum 输出 int64 并做越界检查，不允许 NumPy 静默回绕；不支持的 Decimal 明确拒绝，
不自动转 float。浮点非有限输入/输出按方法拒绝。扩展类型通过方法和适配器能力增加，
不能把首轮类型限制写成所有未来量的永久定义。

完整 Cell 协议保留四分支和原因。普通数值比较、sum/mean 与分类键严格消费；is_defined
全定义，复合条件不短路避检。合法空贡献 sum/count=0，零分母为 Undefined；空当前行
mean 为 Undefined(empty_mean)。全局统计保留 Singleton，默认分组使用实际像，显式
目标组保留空组。未知覆盖、缺状态或缺键不能冒充空输入。

Spearman 保留自己的封闭政策：同一完整域先精确配对，普通 Null 对按 owning 方法排除
并记录 matched/null/complete-pair 数量；Unknown/Undefined 不当普通 Null 删除。
平均秩后计算 Pearson，常量/不足完整对按现有状态规则处理；只有一个 lag=0 候选且无
有效候选时拒绝，不能输出 NaN。选中状态、pair 身份与方法版本必须保留，不新增显著性或因果解释。

### 2.2 后端和范围边界

DuckDB 是首个实现与真实集成验收的来源适配器；本地 Artifact 仍不经 DuckDB。另用独立
测试适配器/能力桩验证注册、放置、参数化和不支持诊断，不连接远端，也不把这项测试算作
第二后端资格。执行契约必须允许后续按同一准入规则接入其他后端，无需修改 DSL 语义内核。

首轮不纳入版本化成员/属性、时间网格/cohort、Event/Lifecycle、权重、count_distinct、分位数、归因、
预测、其他相关方法/lag、多对多、嵌套比较、UnionKeys、任意算术、用户回调、公开 join、
显式物化输入与现场查询的混合执行、来源型 Lazy 图的自动历史缓存复用、跨源资格、旧 Artifact 迁移或发布。
新增领域在接入时扩展有版本的方法与状态，不预埋空类、
万能 payload 或尚无契约的能力开关；也不删除当前生产中尚未迁入新切片的其他能力。

### 2.3 物化后的能力承诺

| 已保留对象 | 必须保持的 K | 明确不支持 |
| --- | --- | --- |
| Entity 行数值/分类关系 | 合法 where、Entity members；数值当前行统计 | 隐式读取未保留属性 |
| 完整数值观察与组件状态 | 与另一物化端点合法 compare、已有坐标 rollup；有效空状态合并 | 从标量恢复细节、改原方法、与现场来源端点混合 |
| Difference | where→members、当前行统计 | 当原 Metric 状态 rollup |
| RowStatistic 状态 | 所支持 sum/count/mean 的合法状态归约 | 从摘要恢复客户名单 |
| AnalysisDomain | 固定成员与已有本地映射，可作为获准纯本地操作的目标域 | 首版固定成员再 read/observe 新来源；重算名单替代固定输入 |
| AssociationResult | 检查状态/计数及 coefficient 视图，获准的数值筛选与当前行统计 | 从系数恢复主体，或对系数 rollup 为总体相关 |

纯保留输入 K 用断源并禁止 DuckDB 连接的测试验收。Lazy 成员上的新 read/observe
另行验证来源执行；物化成员上的调用按[架构 §5.1](2026-09-24-marivo-analysis-dsl-architecture-design.md#51-历史缓存显式物化输入与准入)验证拒绝。本 MVP 的 K 是完整目标设计的
明确子集，不能将尚未实现的混合操作列为已承诺续算。恢复沿用现有 Session/Artifact
定位入口，不增加公开 load 同义方法。

## 3. 当前接入点和改造责任

检查基线为 `bf14862fbf5ae3dd7038273e1cc3a95a75d49d34`；实施前更新 SHA、依赖和工作区记录。

| 现有位置（相对仓库根） | 复用/改造责任 |
| --- | --- |
| marivo/semantic | 真实 Ref、关系、装饰器 Metric 声明及显式组件图；通过正式 authoring 接口声明，不手改内部可合并标签 |
| marivo/analysis/session、datasets、observation | 有类型的公开构造、域和组件规则；不先全库改名 |
| marivo/analysis/operators/registry.py | 分离方法契约与已准入实现，复用当前后端能力和本地方法注册 |
| marivo/analysis/compiler/placement.py、lowering.py | 放置与 DSL→Ibis 表达式转换；SQL 编译交给 Ibis；新切片的 Artifact 不选择 ParquetBinding/DuckDB 扫描路径 |
| marivo/analysis/materialization/execution.py、ibis_batches.py、source_stage.py、storage.py | 复用 BatchStream 的 schema、批次迭代和 close 协议，将 Ibis/原生驱动来源及经 receipt 校验的 Parquet 读取接入同一受控流边界；保留空流 schema、类型与底层资源所有权 |
| marivo/analysis/materialization/reads.py、retained.py、local_execution.py | 复用 receipt/部件校验、pandas frame 转换和本地运行；补齐统一输入/输出契约及资源生命周期 |
| marivo/analysis/materialization/execution_key.py、dataset_execution.py、store.py | 分开定义身份与本次求值身份；修订来源型 key、历史命中、Run/Artifact 绑定及发布定位，使重复来源求值各自持久化且不覆盖旧结果；纯保留输入仅允许固定输入的精确命中 |
| marivo/analysis/materialization/contracts.py、publication.py 及 codecs | 现有 Store 的主表/部件发布和冷恢复；不另造存储、不增加旧格式迁移 |
| marivo/analysis/operators/association_values.py 等方法 owner | 复用确切算法和方法政策，拆出可适配的计算边界，避免重写第二套统计定义 |

现有代码已有 SourceStep、ArtifactReadStep、PandasStep 和本地数值计算，可作为基础。
同时存在保留数据进入 DuckDB 的路径，因此“本地只用 pandas”是本次候选必须实施和测试
的新放置约束，不是现状描述。本轮不顺带改写未接入新切片的全部旧生产方法；后续接入新
协议的物化续算都必须遵守该约束，不能留下同一新方法的隐含双路线。

当前 `execution_key.py` 仅由 `definition_fingerprint` 推导 key；`dataset_execution.py`
在求值前按该 key 查找历史 Artifact 并恢复命中；`store.py` 对 Session 内 execution key
施加 Artifact 唯一约束。因此仅取消命中分支不足以实现重复来源求值，还须同时闭合新求值
身份、发布 key、Run/Artifact/receipt 绑定及精确恢复。目标契约见
[架构 §5.2](2026-09-24-marivo-analysis-dsl-architecture-design.md#52-定义身份求值身份与持久化)；
不能把每次执行的随机量写进语义定义来避开冲突，也不覆盖或重定向已提交 Artifact。
来源型新切片采用每次获准顶层求值的新身份；纯保留输入的本地派生仍可按同一 Session 的
不可变 receipt、方法及协议版本精确命中，命中不建立新 Run，未命中才执行并发布。
混合输入在准入与数据读取前拒绝。失败或提交结果不明时协调原 Run/receipt 的状态，不能
自动再次求值；新求值身份也不替代 compare 原有的域、定义和输入绑定检查。
这里列出的是新切片必须改造的既有接缝，本次文档修订不修改 Runtime 代码；实施仍不另建
Runtime/Store，不迁移旧 Artifact；新切片接受前不改变现有生产路径，不切换尚未获准的生产接口。

## 4. 分析场景与独立预期

这些场景验证基础能力的组合，不定义实现架构。按需下钻属于用户分析策略。
以下均为目标语法。session 已加载声明；各场景使用独立夹具，场景内共享显式 customers 节点。

```python
import marivo.semantic as ms
import marivo.analysis as mv

Customer = ms.ref.entity("sales.customer")
Region = ms.ref.dimension("sales.customer.region")
Channel = ms.ref.dimension("sales.order.channel")
Revenue = ms.ref.metric("sales.revenue")
Buyer = ms.ref.relationship("sales.order_buyer")

july = mv.time_scope(start="2026-07-01", end="2026-08-01")
august = mv.time_scope(start="2026-08-01", end="2026-09-01")
september = mv.time_scope(start="2026-09-01", end="2026-10-01")
customers = session.members(Customer)
```

### J1：总数 → 地区 → 华东的渠道

```python
total = customers.observe(
    Revenue, during=august, via=Buyer,
).rollup().execute()

regional = customers.group_by(Region).observe(
    Revenue, during=august, via=Buyer,
).execute()

region = customers.read(Region)
east = region.where(region.value.eq("east")).members()
east_channels = east.observe(
    Revenue, during=august, via=Buyer, coordinates=(Channel,),
).group_by(Channel).rollup().execute()
```

夹具固定为 A/east 的 web 收入 450、B/east 的 mobile 收入 150、C/south 的 web 收入 400；
D/west 存在但没有订单。总收入必须为 1000；地区结果为 east=600、south=400、west=0；
华东渠道结果为 web=450、mobile=150。没有订单的客户不产生凭空的 Channel 类别。

第一步不读取 Channel，也不构建所有未来分组。第二步才引入 Region，第三步才引入 Channel。
Dimension Ref 分组必须与同一绑定上的显式 read→group_by 结果及检查政策一致。
`customers.group_by(Channel)` 必须拒绝：订单渠道不是客户的单值属性。

三次 execute 是三次观察，不保证来源不变。固定夹具用于核对；另在两步之间新增订单，
验证下一次观察反映新来源且保留新绑定，不强行核对到旧总数。物化客户名单不冻结订单来源。
本轮不发布“自动沿旧快照下钻”的承诺。

### J2：按消费下降选人，再观察下一月

```python
jul = customers.observe(Revenue, during=july, via=Buyer)
aug = customers.observe(Revenue, during=august, via=Buyer)
change = aug.compare(jul)
decliners = change.where(change.value.lt(0)).members()

result = decliners.observe(
    Revenue, during=september, via=Buyer,
).summarize(mv.mean()).execute()
```

| 客户 | 七月 | 八月 | 九月 |
| --- | ---: | ---: | ---: |
| A | 100 | 60 | 30 |
| B | 100 | 120 | 200 |
| C | 50 | 0 | 0 |
| D | 0 | 0 | 0 |

变化为 -40/20/-50/0，选出 {A,C}，九月人均收入 15；C 的九月零贡献仍占一票。
只有同一个明确客户域和相容量定义可以精确配对。故意在一个端点删掉 D 时必须拒绝，
不能 inner join 后恰好仍算出 15 就通过。跨期选人不新增业务 Metric。

### J3：明细收入 / 订单数，按渠道观察与正确上卷

```python
Order = ms.ref.entity("sales.order")
OrderLine = ms.ref.entity("sales.order_line")
LineOrder = ms.ref.relationship("sales.line_order")
AOVFromLines = ms.ref.metric("sales.aov_from_lines")

customer_channels = customers.observe(
    AOVFromLines,
    during=august,
    via=mv.routes(
        mv.route(OrderLine, through=(LineOrder, Buyer)),
        mv.route(Order, through=(Buyer,)),
    ),
    coordinates=(Channel,),
).execute()

by_channel = customer_channels.group_by(Channel).rollup().execute()
overall = customer_channels.rollup().execute()
customer_channel_mean = customer_channels.summarize(mv.mean()).execute()
```

完整夹具：A/web 有一笔订单，明细 40+60；A/mobile 有一笔订单、确实无明细；B/web
有两笔订单、明细总额 60。明细与订单分别归约，共同域保留 A/web、A/mobile、B/web。
局部值为 100、0、30；渠道值为 web=160/3、mobile=0；总体为 160/4=40。
最后一行按客户×渠道实例等权统计，结果为 130/3，必须如实标明统计单位。

另用不含 Channel 的客户 AOV 夹具验证经典区别：A 有 100 笔 1 元订单，B 有 1 笔
100 元订单；客户 AOV 均值为 50.5，总体客单价为 200/101。不能复用同一 sum/count
字段结构就把两种统计状态混为一体。

A/mobile 的零来自完整明细范围的合法空贡献，而非无条件补值。覆盖或坐标映射未知时拒绝。
多根原始事实不得先 join 成宽表；一笔订单多条明细不能把订单数放大。

### J4：来源聚合 → Python Spearman → 物化结果续算

```python
OrderCount = ms.ref.metric("sales.order_count")
revenue = customers.observe(Revenue, during=august, via=Buyer)
order_count = customers.observe(OrderCount, during=august, via=Buyer)
association = revenue.correlate(order_count, method="spearman").execute()

coefficient = association.coefficient
result = coefficient.where(coefficient.value.lt(0)).execute()
```

四位客户的收入为 [1,2,4,8]、订单数为 [4,1,3,2]；完整配对为 4，Spearman 系数为 -0.4。
源端完成成员收入和订单数准备，统一输入通过交换边界进入 Python，数值结果再经发布和
pandas 读取参与筛选。另覆盖有并列值的平均秩、打乱物理行序、普通 Null 对计数、常量、
不足完整对及 Unknown/Undefined；负例不得以丢行或 NaN 通过。

纯保留输入的相关另用正常受治理 codec/发布链生成的两份 Materialized 契约夹具，明确保留
共同且完整的 Customer 观察坐标及输入绑定；冷恢复后按 correlate 自己的配对与绑定条件
准入，验证相同系数和计数。不能仅因两表键值相同就宣称共同观察域，也不把 TimeChange 的
共享成员实现要求扩展为所有相关方法的统一前提。这项保留输入契约测试与上面的全 Lazy
公开端到端链分别记账，不声称已经提供一次捕获多个端点的新公共入口。关联结果没有主体
映射，不能因输入来自客户就由系数恢复名单。本例不要求 rank、时间 lag 或预测。

## 5. 验证目标：正确性、架构与代价

### 5.1 必须通过的验证矩阵

当前以下目标均未完成新 DSL 实现验收。

| ID | 验证目标 | 通过标准 |
| --- | --- | --- |
| V01 | 分析组合 | J1–J4 的独立数值、域、单位、状态与操作结果全部符合；直接 Ref 分组与同绑定 read→group_by 一致 |
| V02 | 完整域与多根 | 零订单客户保留；仅分母有坐标的行保留；完整元组并集不做笛卡尔积；明细不放大订单数 |
| V03 | 方法状态与空输入 | AOV 均值与总体 AOV、当前行状态与原量状态区分；显式空组、零分母、空统计和有效空状态正确 |
| V04 | 拒绝与消费范围 | Null 分类、缺键、错误单位/量、跨 Session、多对多、模糊路径、非有限和越界明确失败；不新增未选中事实全源预检 |
| V05 | 双实现契约一致 | 对实际支持双路线的基础能力，用独立向量比较 Ibis 来源执行与 pandas 执行的数值、域、Cell、部件和拒绝含义，不要求物理计划相同 |
| V06 | 交换与 codec | 来源与受控 Parquet 两类生产者遵守同一固定 schema，空流仍保留 schema；键乱序、四种 Cell、nullable int64、时间戳与精度、主表与部件换序往返正确；批次 schema 漂移、dtype 丢失、错误标签、错绑定、缺部件和损坏 receipt 拒绝 |
| V07 | pandas 物化续算 | Artifact→pandas→续算与新进程冷恢复保留 K；断源且 DuckDB connect/register/scan spy 禁止时仍通过，不重新计算旧逻辑 |
| V08 | 缓存、求值身份与混合准入 | 同一来源型 Lazy 对象在来源变化前后重复 execute，定义不变、求值 key 与 Artifact 各自独立，新结果使用当前来源，两个精确引用均可断源恢复；纯保留输入按同一 Session 的不可变 receipt、方法及协议版本精确命中，复用原 Artifact 且不新增 Run；单端物化 compare、物化成员再 read/observe 在准入及数据读取前拒绝，不重算、不上传；共享成员实现绑定的双 Artifact 契约夹具通过默认 compare，独立捕获仅键相同则拒绝 |
| V09 | 数值方法边界 | J4 实际走 Python 数值核；平均秩、配对计数与方法状态正确，改行序不改配对，跨批次不分开求秩 |
| V10 | 惰性、共享与原子性 | 构造/纯计划零业务来源 I/O，来源 reader 仅在获准执行中创建；一个执行图内共享节点单次实现，独立顶层来源求值不复用实现身份；第二次执行失败不覆盖首次已提交 Artifact，不发布失败运行的成功 Artifact；失败或提交结果不明时协调原 Run/receipt、不自动再次求值；各退出路径释放流和底层资源，未读完的校验不记为通过；输入未变且不泄露主体键 |
| V11 | 放置与后端隔离 | 能力注册决定路线；不支持后端/类型在最早可确定阶段拒绝；无静默回退；能力桩替换不改语义核且不误导为真实后端已验证 |
| V12 | 分批正确性与运行成本 | 批次切分不改结果；记录 Arrow Table 组装、pandas 转换与 NumPy 工作区的实际开销，输入批次大小不等于算法内存上界；无 DuckDB spill、隐式抽样或截断；查询次数不随客户数线性增长；不设置输入行数、字节或工作区预算准入门槛 |
| V13 | 扩展代价 | 新增算子与数值方法的实际改动可定位，基础能力被复用；更换为非电商 Entity/字段映射仍可执行同类构造；adapter 中重复的语义决策与待重构项明确登记 |
| V14 | 用户与类型契约 | 严格 typing 正反例、Help 发现、真实状态 contract 和修复通过；脚本与真实 Agent 验收分别记录 |
| V15 | SQL 委托与路线选择 | 产品分析/校验 SQL 来自 Ibis 编译，无手写片段或生成后补丁；分别验证 Ibis 可表达且语义获准、后端不支持但已注册 Python 路线可用、无合法路线三种情况；可编译但语义不符仍拒绝，运行失败不触发 pandas 重试 |

V14 另须验证 Metric 声明边界：缺失 unit/additivity 拒绝；同形函数体不触发单位或可加性
推断；改变 body 不自动改变已声明规则，改变声明版本则更新语义身份。已知矛盾声明拒绝，
不能履行值政策的 body 不准入。仅声明 non_additive 的 AOV 不获得组件上卷；显式 ratio
组件路径保留状态并正确上卷。记录声明依据，不把独立样例相等当作声明普遍正确的证明。


V06 对两类生产者使用相同的声明 schema 与往返向量，包括零批次空流、批次间类型漂移、
带 Null 且含大于 `2**53` 值的 int64，以及带时区和明确精度的 timestamp；不能先转 float
再恢复整数。Decimal 仅在交换/codec 层检验精度往返，不改变 §2.1 的 int64/float64 算法
准入，也不因此允许 Decimal 进入首轮统计或 NumPy 方法。

V10 覆盖 reader 创建即可能提交来源查询的适配器，确认构造和纯计划均不打开来源流；
Artifact 的 show/恢复仍可按原契约受控读取。正常耗尽、提前 close 和迭代异常
都须释放底层游标/reader 等资源。提前结束不能把尚未消费的完整性或覆盖校验记为通过。关闭计数等 mock 仅证明单元契约；
真实后端的游标、事务和取消行为仍须各自资格验收，不能由 mock 或另一后端通过代替。

V12 对完整输入算法记录 Table 组装、pandas 转换复制和 NumPy 工作区的峰值共存开销；
不能假定逐批反复 pandas concat 始终有界，也不作通用零拷贝承诺。分批读取只限定搬运
批次；算法能否分批仍由方法契约决定。

V08/V10 的重复求值必须使用同一个 Lazy Python 对象，不能重建表达式或改参数来回避历史
命中。先执行并保存精确 Artifact 引用，再修改受控来源后执行第二次，断言定义指纹不变、
两次运行的求值 key 和 Artifact 引用不同且值分别对应两次输入；随后断源并在新进程中
按两个引用恢复，检查各自域、Cell、状态与 K。另将第二次执行改为数据准入失败，确认首次
Artifact 仍可精确恢复且未被覆盖。运行内共享另用同一节点的多个消费者验证，不能把这种
共享扩大为两个顶层调用共享求值身份。

双物化默认 compare 的正例必须从明确共享的成员实现及其真实状态出发，通过正常受治理
codec/发布链生成两份保留该绑定的 Artifact；这是契约测试，不替代 J2 的全 Lazy 公开
端到端验收。负例中，两个独立 execute 即使引用同一个 Lazy members 节点且得到相同键
集合，也没有同一成员实现绑定，不能通过默认 TimeChange。此次修订不新增公开的多端点
共享捕获或多输出工厂，不能将这个未定义的生产入口作为已交付能力。

纯保留输入另验同一派生表达式的固定输入精确命中：复用原 Artifact、Run 数不增加、来源
访问为零；receipt、方法或协议版本变化时不能沿用该命中。模拟发布提交结果不明时，按原
Run/receipt 协调与读回；未确定结果不能作为自动重跑许可，也不能改变此前已提交的 Artifact。

显式 builder 的验证与装饰器声明分别记账：首轮 aggregate 的 sum/count 与 ratio 路径
验证输出单位、条件化合并规则、组件状态，以及输入声明依据的保留。至少覆盖单位冲突、
不相容时间/域、重叠分组、缺组件状态和零分母；不要求作者重复填写可推导的输出契约。
同一 AOV 的装饰器直接值与显式 ratio 在固定合法输入上可有相同数值，但只有具备组件状态
及规则的路径承诺总体上卷，不能按数值相同合并能力。输入为装饰器 Metric 的 builder
只使用声明，不穿透 body。上述归入 V03/V04/V14；源端及 pandas 实现一致性归 V05。
`ms.linear` 的完整推导属于目标接口，未因此扩大首轮端到端范围；后续接入须验证系数单位、
各项单位相容、共同条件而非许可并集，以及嵌套不可合并组件的拒绝。

### 5.2 独立 oracle 与扩展试验

业务 oracle 用人工表、整数/Fraction 和独立秩计算，不调用候选 reducer；原始 SQL 对照
直接由业务问题写出，不能复制候选 SQL。首轮小型 float64 fixture 使用 abs_tol=1e-9、
rel_tol=1e-9；数量级、整数边界与消去误差另测，不能拿此容差比较身份或宣称普遍精度保证。

有限规律检查覆盖已实现能力：L1 共同原域全定义谓词、L8 同目标域分层状态合并、L9
固定含空组目标子域、L6 的既定 K。分别记录状态、语义和续算保持；不能以恢复前后都
拒绝原定能力算通过，也不为验证性质新增通用证明系统。

在基础契约和 J1 完成后记录内核基线，再接入 J2 compare 与 J4 数值方法。逐项记录：
公开构造、语义定义、基础能力、Ibis/pandas 适配、codec、披露及测试的新增/修改位置。
若需要改内核，说明缺失的普适能力或错误抽象，修订版本并补回归；不把修改藏进适配器。
目标是评估新增方法的边际工作与合理复用，不要求为了“零内核修改”扭曲设计。

每项语义决策登记 requirement → owner → 实现位置 → 测试 → 适用方法；另记录总新增代码、
重复判断、方法/类型/状态数量和新增后端的接入清单。不实现另一套完整 A/B 系统，不预设
任意代码减少百分比，也不把少几个类当作成本下降。

### 5.3 运行成本与使用验证

对同一固定数据分别测试全来源、先物化后 pandas、来源→Python 数值三种执行布局；另测
显式 Artifact+新来源的零数据 I/O 拒绝，以及历史缓存存在时仍重新执行 Lazy 来源图。
记录 SQL/来源查询次数、Artifact 读取量、交换字节、转换复制、峰值内存、耗时和
物化字节。至少包含 1,000 与 100,000 条事实。选择较小的
前置样本验证正确性，再扩大；不以同数字、不同范围的查询比较速度。

成本结果用于记录实际开销与决定下一步优化，不设置数据量准入限制，也不反向改变语义。远端性能和资格未实测就
保持未验证。纯保留输入来源查询和 DuckDB 使用均为零；来源后的 Python 阶段允许消费
完整必要输入，但必须说明为何不能再安全缩减。

静态类型反例覆盖 Ref kind 混用、Category 数值方法、Logical.show、Materialized.execute
和未开放方法；运行反例覆盖单位、实际域和状态。真实 Agent 只依赖候选 Help/contract
完成 J1–J4，记录修复过程；预写脚本通过不能冒充真实 Agent 验证。

## 6. 实施阶段与完成标准

| 阶段 | 可持续交付 | 出口 |
| --- | --- | --- |
| S0 契约与接入 | 基础能力输入/输出、方法与实现注册、交换 schema 与流生命周期；定义/求值身份、execution key 与发布/恢复绑定；既有拥有者差距清单和独立 fixture | 无悬空输入语义；来源求值与固定输入命中已分开；批准的切片进入 owning specs 后实施，不以当前原型替代新契约 |
| S1 双执行基础 | 同一有限方法契约的源端 Ibis 与本地 pandas 适配、受控 Artifact 读写、基础状态归约，J1；V06/V10 双生产者流与关闭边界，V08/V10 重复求值、固定输入命中与精确恢复子项 | 数值/域/值状态一致，SQL 生成委托边界成立，Artifact 路径禁止 DuckDB；同对象重复来源执行成功及第二次失败均保持旧 Artifact，固定输入命中不新增 Run，基本完整性与原子性已成立 |
| S2 算子组合 | J2/J3 的 compare、Lazy 选择/成员/新观察和复合指标；缓存分类与混合拒绝；记录相对 S1 的实际内核改动 | 原子能力确实被复用，显式旧输入不被替换，共享成员绑定的双物化 compare 契约夹具正确，混合输入拒绝及独立组件/状态测试通过 |
| S3 数值扩展与边界 | J4 的来源准备→Python→发布→pandas 续算；完整冷恢复、分批正确性、能力桩与成本记录 | V01–V13 及 V15 的必要项通过，交换与放置契约可承接后续方法 |
| S4 使用与交付验收 | 静态类型、唯一 Help/contract、错误、真实 Agent 及总结 | V14 与完整矩阵通过；技术、Agent、后端资格分别给结论 |

恢复、原子性和错误不能全部拖到末期；每阶段先保持其已承诺能力，S3 完成全矩阵。
使用隔离 worktree，保留其他工作区修改。先跑窄测试，再按影响范围执行 make test、
make typecheck、make lint-agent，最终 make check-agent；Runtime/冷恢复用对应范围的
make runtime-test TESTS='...'。新增 fixture 遵守 marivo-test-fixtures 技能，不为本轮
启动远端服务或 MinIO，也不把完整 release-check 作为普通开发门槛。

若进入公共接口切换，同步 owning specs、实现、原生 Help、错误、导出快照、CLI 和 site
latest 中英文文档；packaged skills 按 AGENTS.md 单独授权要求处理。本次仅重构接口、架构与 MVP 文档，
不直接实施、提交、切换生产或发布。

实施时建立 composition-acceptance.md，保存 SHA、依赖、配置、输入、命令、实际结果、
失败复现和证据位置。分列文档/类型、Ibis 编译、来源实际执行、pandas/数值核、交换/恢复、
扩展成本、真实 Agent、各后端资格，各项状态为通过/失败/未验证/阻塞。

全部必要语义与架构矩阵通过才可称基础架构 MVP 完成；真实 Agent 通过另行记录。DuckDB
来源通过不意味着其他数据库获准，能力桩通过不意味着远端已执行，局部 Ibis 小样本和历史
签名原型也不意味着新 DSL 通过。本轮完成后的新增能力，应通过已有注册、基础能力组合、
执行适配及契约测试接入，而不是复制一条新的执行和存储链。

## 附录 A. 完整目标语言的验证案例

本附录保留完整目标语言的判别案例，不把 Event/Lifecycle、预测、归因等后续能力加入首轮。
首轮必做范围以第 2 节和 V01–V15 为准；未来扩展接入时选择对应案例并补齐独立方法验证。

验收首先检验问题与结果含义，其次检验执行；不能只看是否算出期望数字。

### A.1 场景与语义反例

| 判别例 | 必须成立 | 必须拒绝或明确区分 |
| --- | --- | --- |
| 两客户、仅一人有 100 元订单 | 完整域收入 100/0，客户均值 50 | 事实分组只产一人后报告均值 100 |
| A 有 100 笔 1 元订单，B 有 1 笔 100 元订单 | 客户 AOV 均值 50.5；总体 AOV 200/101 | 自动选权重；只有均值却继续原量上卷 |
| 多事实用户画像 | 各根独立聚合后在 Customer 上配对 | 明细 × 访问 join 导致贡献相乘 |
| 明细收入 / 订单数，web 有 100 元明细与一笔订单，mobile 仅一笔订单 | 完整覆盖下共同坐标域为 {web, mobile}，值为 100/1、0/1；分支状态独立 | 用分子像或交集丢掉 mobile；缺覆盖时伪造空明细 |
| 同一客户周，两订单组件的 (Channel, OrderStatus) 分别仅有 (web, paid)、(mobile, cancelled) | 共同域仅保留这两个完整元组；每组件按自己的空贡献政策求值 | 按 Channel 与 OrderStatus 分别并集后扩成四个组合 |
| 一组件已知只有 web，另一组件覆盖不足且可能还有 mobile | 无法确定完整共同坐标域时拒绝 | 只保留 web 并令其值为 Unknown，以数值未知掩盖潜在缺坐标 |
| A 收入 100 且 Region=Null，B 收入 50 且 Region=Defined(east) | 原域分组拒绝；显式选取已定义分类后，只对新域分组并披露筛选 | 隐含 NULL 组、自动“未知”类别或静默遗漏 A；以显式目标组掩盖缺映射 |
| 已支付订单的 Channel=Unknown；未支付订单的 Channel=Null | 前者使按 Channel 的已支付收入观察拒绝；后者不进入该组件的坐标检查 | 丢弃已支付缺键贡献；对未选中事实做全表分类预检 |
| 收入 Jun=80/100/0、Jul=100/100/0、Aug=60/120/0 | 二次差分 -60/20/0，均值 -40/3 | 一侧缺 u3 后 inner join；合并不同 July 实现 |
| 上例负变化客户，Sep=30/200/0 | cohort={u1}，后续均值 30 | 用 Sep 域重新选人；丢失零贡献客户 |
| u1 两旅程 10/30 秒，u2 一旅程 100 秒 | 旅程均值 140/3；主体像去重 | 偷换成等主体均值 60；将观察时长当完成耗时 |
| 两个不相交旅程分别属于同一人 | 旅程交集为空，两个主体像交集非空 | 交换主体像与交集 |
| 同均值/分布，不同用户到值的对应 | 摘要不能单独恢复高值成员 | 从 summary 隐式回源 |
| -100/+30/-30 去掉后两项 | 数值核对仍等于目标，但完整覆盖失效 | residual=0 签发完整分区 |
| Top-K 展示 | 固定总体分母不变 | 排名/limit 改写 reference |
| 留存 25 真、5 假、70 未知 | 固定 100 人目标界为 [25%,95%] | 当作置信区间；把未知当未回访 |
| 相同状态区间，不同自循环次数 | 迁移统计需要轨迹 | 从区间重建全部迁移 |
| 两仓库 t₁ 库存 10/20、t₂ 库存 12/18 | 各时点总体库存 30；同一时点的获准空间分区可求和 | 四个值相加为库存 60；将最后可见快照当精确期末 |
| 两设备在对齐时点分别为 [10,0]、[0,10] | 总带宽峰值 10；需要时间对齐状态 | 先算各设备峰值再求和得 20；传播 Measure 空间求和许可到折叠后值 |
| 同一 100 元订单进入两个标签 | 保留重叠事实；只有获准去重/分配方法可继续得到对应整体 | 仅因标签在可加名单中就将 100+100 当总体收入 |
| Materialized / 冷恢复 | 固定输入下保留既定 K 的语义续算 | 用重新读可变源的结果比较代数等价 |
| 全 Lazy 的观察→比较→选人→再观察→统计 | 最终 execute 前零业务来源读取、零结果发布；一次终端调用完成依赖图 | 要求用户先执行成员或中间量 |
| 同一 customers/aug 节点进入多个分支 | 一次 execute 内共享对应实现；分组与筛选沿同一域绑定推导 | 按分支重读，或把共享某节点解释为全源快照 |
| customers.group_by(Region) 与同一绑定上的 read→group_by | Region 属于 Customer 且版本确定时，两者有相同映射、检查与结果；构造均无业务 I/O | 直接以订单 Channel 给客户分组；从汇总 Relation 隐式读取 Region |
| 先看总数，再按用户选择观察地区与渠道 | 不预读未来维度；新观察保留实际来源绑定，固定输入时可核对 | 从标量凭空恢复细节；来源已变化仍承诺核对到旧总数 |
| Revenue 与 Profit 都为 CNY | Observe 取指标声明的实际变量与贡献归属角色 | 用同单位字段或另一关系角色替代原量 |
| 展示 mean=10，但保留状态为 (1,1) | 输入不满足状态实现其绑定量的有效性条件，拒绝按该状态上卷 | 只因状态字段名和版本齐全就宣称可恢复 |
| 空均值为 Undefined，保留状态 (0,0) | 当前行数值 mean 拒绝该单元格；获准状态上卷仍可合并 (0,0) | 将非 Defined 展示值等同于状态必然无效 |
| 未定义行先由 is_defined 排除，再作数值 where | 显式得到新的 Defined 子域 | 未经共同原域的 Defined 前提就合并成一个布尔条件 |
| 完整四周机会上的 gt(0)，前三周为真、第四周 Unknown 或 Undefined | at_least(3) 对 Unknown 可确定入选，对 Undefined 拒绝；两真一假一未知仍拒绝未决定资格 | 把 Undefined 混为 Unknown；用量词已有足够真值掩盖硬失败 |
| 同一机会的 any_of 含 True 与 Unknown，或 True 与 Undefined 比较 | cohort 中前者真值为 True，后者仍拒绝；is_defined 可显式判断 Cell 标签 | 把三值合成当作短路免检，或将 cohort 政策套到 where |
| L9 保留含空组的 G' | 相同固定状态、映射与目标组域上的状态相等 | 丢掉空组；重算参考分母/权重；仅凭状态相等替换 RowStatistic 定义 |
| L9 的 G' 来自组收入阈值 | 固定该 G' 后限制整组原状态 | 将组收入阈值改成单条订单金额阈值，或跳过求 G' |
| 归因 -100/+30/-30 筛成 -100 | 原 target 与未筛选核对仍有输入绑定，当前域已不完整 | 将原 reconciliation 当作当前完整分区证明 |

理论 L1–L9 约束选择、映射、状态归约和物化规律；C1–C4 约束摘要不可恢复性、主体像、
两种均值和未知界；T1 只在每个局部算子履行义务后支持条件组合。
它们不是公共 API 数量指标，也不是实现或统计方法已被证明正确的声明。
每条对照记录数值、状态、成员集合、语义或 K 续算中的实际比较层次。新增方法既需完整推导
样例，也需缺前提或缺部件的拒绝样例；不能用几个成功数字替代方法规则。

语言层的通过标准是：上述问题能用同一套 AnalysisDomain、量绑定、比较和归约结构表达；
领域 matcher/replayer 不重复实现 mean、主体去重或通用比较；Agent 能从 contract 理解
“为什么这个操作可行、为什么另一个不行”。规则经济性仍需包含适配、编码、披露的真实对照，
不能仅凭类更少或文档公式更统一宣布成功。

### A.2 接口与类型反例

静态验收不能只检查“例子能解析”，必须包含正例与故意写错的调用：

| 调用 | 应在何处处理 |
| --- | --- |
| `aug.where(region.value.eq("east"))` | 静态允许；构造期核对同域及显式依赖 |
| `mv.all_of(aug.value.gt(0), region.value.eq("east"))` | 静态允许；构造期核对共同机会域 |
| `aug.compare(region)` | 静态拒绝：CategoryRelation 不是数值比较基线 |
| `region.value.lt(1)` | 静态拒绝：普通分类字段没有数值顺序方法 |
| `aug.value.lt(region.value)` | 静态拒绝：本阈值方法接受 Number，不是任意字段 |
| `aug.group_by(aug)` | 静态拒绝：数值 Relation 不能冒充分组分类 |
| `customers.group_by(Region)` | 静态允许 Dimension Ref；构造期核对其 owner、成员版本与单值绑定，实际分类值在执行时检查 |
| `customers.group_by(Channel)` | Ref kind 虽相同，Channel owner 为 Order，不能绑定为 Customer 自身单值属性，构造期拒绝 |
| `customers.observe(Region, ...)` | 静态拒绝：observe 接收 Metric；属性使用 read |
| `session.members(Revenue)` | 静态拒绝：成员身份必须使用 Entity Ref |
| `customers.observe("sales.revenue", ...)` | 静态拒绝：字符串不是 Metric Ref |
| `customers.observe(metric_entry, ...)` | 静态拒绝：CatalogEntry 需显式取 `.ref` |
| `customers.observe(Revenue, via=Region, ...)` | 静态拒绝：路径需要 Relationship Ref |
| `declining_change.observe(Revenue, ...)` | 静态拒绝：先用 members 将 Relation 投影为 AnalysisDomain |
| `Revenue.numerator` | 静态拒绝：Ref 不暴露计算图属性 |
| `logical_relation.show()` | 静态拒绝：尚无物化结果可读 |
| `aug.value.lt("100")` | 静态拒绝：字符串不是数值阈值 |
| CNY 与 USD 比较、不同实际客户域配对 | 构造期拒绝已知不相容；可获准的数据条件检查进入执行义务 |
| 跨 Session 条件或分组、缺状态上卷、缺映射选人 | 构造期拒绝，不靠后端查询报错 |

2026-09-24 已用独立的临时 `.pyi` 签名原型验证域、Numeric/Category 和 Ref 接口切片：
Python 3.10、严格 mypy 下，包含全 Lazy 主线及同域跨量条件的三个正向检查文件通过，
20 个静态反例逐条按预期诊断拒绝。既验证了隔离的同形 Ref 签名，也验证了实际仓库的
Ref / ms.ref / CatalogEntry 导入。该原型采用当时的 InstanceSet / Series 命名，验证的是方法
和类型边界；本次将对应概念改为 AnalysisDomain / AnalysisRelation，不将旧原型结果记作
新名称公共 API 已实现或已完成发布验证。
原型采用 Number=int/float/Decimal，显式验证 Logical/Materialized 接口；没有实现数据计算。
函数体示例的等价 Ibis 表达式通过了构造和 DuckDB SQL 编译检查；新增 decorator 元数据
尚未实现，不将此编译检查记作完整 authoring、SQL 数值或真实来源验证。
这只证明签名可表达且该切片能拦截指定误用，不代表 AnalysisDomain/单位检查、Duration/领域扩展、
后端执行或物化恢复已经验证。实施时这些必须分别补齐，并由真实公共签名测试替代原型证据。
接口设计第 3.9 节新增的求和坐标元数据与精确时点观察重载尚未纳入该原型；本轮只检查了文档结构、
示例语法与规则的一致性，不把这些新提案写成已有类型或执行测试通过。
对齐 v0.5 新增的值状态政策、状态有效性与 L9 条件，以及本次补齐的共同贡献坐标域、分类键
准入、cohort 真值规则和成员域直接 Dimension Ref 分组，也不在上述历史原型证据内，
须按各自的定义和实现边界另行验证。

### A.3 现有表达能力的迁移判别例

下面是目标接口必须承接的验收问题，不是本次文档修改已经通过的执行测试。
每项都要验证输入身份、量定义、Cell、部件和续算，不能只比较展示数值。

| 问题 | 正向判别 | 负向判别 |
| --- | --- | --- |
| 分支过滤后的运行时比值 | 分子只含 web 订单，分母仍为全渠道订单；两者各自保留过滤与贡献范围 | 把结果 where 下推到两分支，或强制注册 Catalog Metric 才能观察 |
| 客户在两个渠道分别消费 40/60 | Customer × Channel 上为 40/60，去掉 Channel 合法上卷为 100 | 将客户总消费 100 复制成 100/100 |
| Entity × Region × Channel × Month | 只消去 Entity 或 Channel 时保留其余坐标；月到季只用获准包含映射 | 无参数全局 rollup 冒充部分轴上卷；粗桶切开细桶仍直接合并 |
| 成员时间与指标时间不同 | 先固定某时点成员版本，再研究后续收入 | 用消费窗口重新选择成员版本 |
| 认证财政期间与活动 occurrence | 期间身份及实际边界进入输入绑定，跨窗口比较保留两侧期间 | 用等长天数或字符串标签冒充日历对齐 |
| 累计值 100/150 | 保留累计范围与组件状态；末期值和期间新增量是不同量 | 把重叠的累计展示值相加为总体 250 |
| 比较一侧缺键 | 严格配对拒绝；显式缺侧方法保留成员存在状态 | 自动改用交集、把缺行当 NULL 或无条件补零 |
| 基线 -100、当前 -80 | 使用明确的绝对基线分母时相对变化为 0.2，定义记录分母政策 | 与有符号基线分母的 -0.2 混作同一量；零基线补成 0 |
| 分位数首次接入 | 可直接观察各组中位数 50/10；若要总体中位数 10，须对合并后的目标域重新观察 | 对组中位数调用原量 rollup 或求均值 30 冒充总体；近似结果冒充精确 |
| count_distinct 首次接入 | 可在目标粒度直接观察精确去重计数 | 将重叠组计数相加或对细粒度结果调用原量 rollup |
| 归因 Top-K 与展示裁剪 | top_k 先按 owning 方法共同映射 basis 的 Other，再计算完整分解；输出 where/limit 不改目标 | 把 top_k 当作事后删行，或把裁剪后的可见贡献和声称为完整目标；仅凭 residual=0 判完整 |
| 异常与驱动维度候选 | 保留搜索域、评分方法和筛选限制，选定候选后显式继续分析 | Candidate 分数被当成原因；将未搜索维度解释为没有影响 |
| 多量相关与时间 lag | 明确配对域与 lag 方向；每个结果保留实际有效配对数 | 每对量静默采用不同缺失删除政策，再当作同一总体比较 |
| 预测与区间 | 点预测和方法区间有独立定义、训练范围与方法假设 | 把方法区间称为代数证明的覆盖率，或与观测值合并为同一 Metric |
| 漏斗与任意步骤时长 | 完整旅程的指定步骤对定义耗时，漏斗使用其规定的主体/旅程单位 | 复用次数改变统计权重；未完成 observed duration 冒充完成耗时 |
| 月末状态选人再观察下月 | 从规范历史取得月末状态及主体映射，where 后 members 再 observe | 从只保存计数的状态分布恢复名单 |
| 同刻事件的顺序 | 顺序有业务依据，或所有允许次序保持所承诺的状态、轨迹及违规结果 | 仅凭 occurrence ID 稳定排序宣称业务顺序或完整汇合 |
| 排名、缺值与 limit | 平局政策固定；非定义值及排名状态保留；limit 不改原参照 | 未知值被解释为最差；全局 limit 冒充每组 Top-K |

语言验收还必须检查：同一能力只有一条规范入口；逻辑、物化和冷恢复分支在约定 K 内有对应
的完整正例；必要部件缺失时有同义的拒绝。领域方法需要自己的数值及方法对照，不以通用
Relation 的类型检查替代。新补充的签名尚未纳入附录 A.2的旧临时 typing 原型。
