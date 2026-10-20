# Session 与 observe：显式求值域重设计

Status: proposal，2026-10-20；仅设计，尚未实现。

后续修订：timezone 缺省按用户要求采用创建时的系统时区；计数入口进一步建议统一为
Population.count()。这两项修改发生在上一版两轮独立审查之后，不属于该冻结稿的审查结论。

核对基线：`622a930b7adba834744ad285789782e1d4d6e6b2`。本文定义目标合同，
不声明当前 Python 签名、Live Help、Store 或执行器已支持它。
当前实现仍以 [Python Analysis Design](python-analysis-design.md)、
[AnalysisFrame](query-result-reuse.md)、[Session State and Runtime](session-state-and-runtime.md)
及 [Timezones and Calendars](timezone-and-calendar-design.md) 为准。

## 1. 决策、范围与完成条件

### 1.1 observe 方向及计数入口建议

1. 删除 `observe.during` 和 `observe.members`，不保留别名、兼容参数或双重解释。
2. 时间观测域统一放入 `by`。TimeScope 表示整个范围的一个区间桶；
   `TimeScope.by(Grain)` 表示该范围的日历分桶。两者都成为结果坐标。
3. `by=Population` 表示只求值指定的完整坐标域，并保留域内无事实坐标；
   不再声明它必须覆盖来源产生的全部坐标。
4. 显式域计数保留；本轮 API 经济性重评建议使用 `population.count()`，
   删除独立顶层 member_count 入口。计数捕获完整 Entity 身份域，
   不从 Session、`by`、`where` 或其他 Metric 推测计数对象。
5. `where` 表达额外的事实贡献条件；输出域选择不等同于给所有底层读取追加全局过滤。

### 1.2 Session 其余公开面的目标取舍

采用严格创建、按 ID 恢复和只读发现；移除项目共享的 current 指针及其隐含激活。
移除无当前执行消费者的 `source_bindings` 和重复表达发布记录的 `graph`。
统一 Entity 输入、时区名称和身份选择方式。完整逐项决策见 §9。
这些是本文提出的配套设计，不是已经完成的 API 变更。

### 1.3 本轮文档工作记录

| 步骤 | 负责人 | 依赖 | 可核验完成条件 | 状态 |
| --- | --- | --- | --- | --- |
| D1 | coordinator | 当前工作树 | 核对 6 个模块入口、15 个实例方法、11 个属性及拥有者合同 | 完成 |
| D2 | coordinator | D1、已确定方向 | 明确所有输入、求值域、标量、空域、窗口、分配、计数和对齐 | 完成 |
| D3 | coordinator | D2 | 给出 Session 全公开面处置、迁移边界和独立数值验收项 | 完成 |
| D4 | coordinator | D3 | 文档自审、局部链接、示例语法和 diff 检查 | 完成 |

本轮仅新增本文和架构索引链接，不改运行代码、测试、当前合同正文、
`AGENTS.md`、打包技能或现有未跟踪计划。不提交、不发布。
验收表中的实现工作均未执行；历史测试通过不能替代新合同验收。
独立 agent 的原始结论、补充发现、修订及复核见
[独立审查记录](session-observe-redesign-review.md)。

## 2. 所有权与第一性原理

```text
Datasource: qualified physical access
    -> Semantic: roots, time roles, units, relationships, allocation, Metric formulas
    -> Population / TimeScope / Grouping: explicitly requested coordinate domains
    -> observe: bind each Metric to those cells and explicit conditions
    -> AnalysisFrame: complete immutable coordinates, values, presence and frozen time facts
    -> Artifact: submission and audit
```

Session 持有项目/Store 所有权、冻结的时区和语义域，提供来源构造及历史访问。
它不持有隐含“当前成员”、默认期间、结果缓存或上一轮计算参数。
每个 Metric 依赖及 Population 的固定/待执行性质必须显式进入计划。
完成后的 Frame 不恢复原 Metric、源图或隐含成员选择。

参数是否必要按独立语义判断，而不是按数量判断：

- `metrics`：算什么及输出名称。
- `by`：在哪些完整业务坐标求值。
- `where`：哪些额外事实贡献有资格参加。

同一 Population 可以有不同角色，但同一角色不需要重复声明。
例如逐客户观察只需 `by=customers`；这些客户按地区汇总仍需
`where=mv.in_population(customers), by=(region,)`。
把客户投影成地区后，身份选择已经丢失；不能隐含恢复“这些地区中的原客户”。

## 3. 目标签名与输入

以下签名和示例均为目标 API。类型名沿用现有拥有者，不新增通用请求对象或 options 字典。

```python
def observe(
    self,
    metrics: Mapping[str, Deferred],
    *,
    by: tuple[Deferred | Grouping | TimeScope, ...] | Population = (),
    where: Deferred | PopulationPredicate | None = None,
) -> AnalysisFrame: ...

class Population:
    def count(self) -> Deferred: ...
```

### 3.1 metrics

非空有序名称到 resolved Metric 的映射。名称继续遵守单段、非保留名称合同；
输出顺序与映射顺序一致。Ref、Artifact、字符串 ID、普通表均不是 Metric。
各规约保留自身贡献根、时间角色、单位、内在条件、空值政策与内部样本。
组合多个根时独立规约后按完整坐标组合，不能先连接事实表放大贡献。

### 3.2 by 的两种输入形状

| 形状 | 求值域 |
| --- | --- |
| `()` | 零轴单元域 `{()}`；无隐含时间范围 |
| 字段/Grouping 元组 | 按这些规则发现普通分组；同一元组中的普通轴保持真实联合坐标 |
| 含 TimeScope 的元组 | 有限区间单桶与普通联合分组组合 |
| 含 CalendarAxis 的元组 | 有限日历桶与普通联合分组组合 |
| Population | P 中全部、且仅有的完整联合坐标 |

元组中不混入 Population；联合固定域通过既有 `mv.product` 显式构造。
`mv.product` 在现有 Population/CalendarAxis 输入基础上接受 TimeScope；
TimeScope 被规范化为单桶时间域，不引入另一个公开时间构造器。
product 仍要求至少两个因子；单个时间域可与零轴单元域相乘：
`unit = mv.coordinates(by=(), keys=((),))`，再用 `mv.product(scope, unit)`
或 `mv.product(calendar, unit)`。unit 不增加轴，也不等于空零轴域。
Population 的 `.axes` 仍返回 Grouping 描述；区间时间轴使用闭合的 interval 变体，
不把 TimeScope 伪装成字段或内置日历粒度。

有且仅有时间轴的元组保留全部请求桶，即使没有业务活动。
时间加普通字段元组，普通联合分类域来自本次相关贡献及显式计数域的合法分类，
再以时间域补齐这些分类的桶；没有任何分类坐标时为零行。
这不是各普通轴取值的笛卡尔积。需要包含完全未出现的分类时，必须传完整 Population。
既有 membership 的分配关系不因使用元组而丢失。

每次 observe 最多有一个输出时间轴，包括 Population 内的时间轴。
重复/冲突时间轴在构造时拒绝，不任取一个作为窗口端点。
非时间多轴 Population 继续合法；多时间轴求值不在本提案内。
内部时间样本仍是 Metric 自身依赖，不能冒充第二个输出时间上下文。

本版不新增日历桶选择器。输入日历由显式 CalendarAxis 的完整有限目录构造；
与普通 Population 相乘可限定其他坐标，也可因空因子产生空域。
“只求值 P”适用于这些可公开构造的完整联合域，不承诺任意数学子集都有生产入口。
例如保留 Jan1–Jan4 原目录而仅选 Jan2/Jan4 的来源 Population，本版未提供构造路线。
已有 Frame.where/top 可以产生稀疏时间结果并保留原目录；该结果不能通过
population_from 把日历轴重新带入 observe。需要新的来源时间问题时显式构造新时间域。

### 3.3 where

保留字段布尔表达式及闭合 PopulationPredicate；不接收 TimeScope 作为特殊过滤模式。
条件按每个事实规约的根及明确关系绑定，保留完整键、路径、时点及所有权检查。
普通时间字段比较仍是硬事实条件，不提供输出桶或窗口端点，也不因窗口回看被取消。

`by=P` 不需要再次传 `where=mv.in_population(P)`。重复传入时，where 仍是普通
贡献条件，不做“重复条件自动忽略”：在窗口或分配场景，它可能真的改变含义。
构造器不从任意布尔表达式的 AND/OR/NOT 中提取隐藏的观测时间域。

## 4. 指定域求值：选择、补齐及检查

### 4.1 目标定义

对于给定输出域 D 和每个 `p in D`：

```text
value[p, metric] = evaluate the original metric in cell p
output_coordinates = D
```

每个单元格按原 Metric 定义确定所需贡献、样本与窗口。
P 是显式求值域，不是“观测全部来源之后必须满足的全域覆盖断言”。
删除当前 `observed_coordinates ⊆ P` 的默认要求，不另加 coverage 模式参数。
来源存在其他合法坐标不报错，也不扩大结果域。
本提案不增加“P 是否穷尽来源”的公开验证 API。

域内无贡献的坐标仍存在，值按每个规约自己的合同产生：
例如空/all-null sum 仍为 null，计数仍按其计数对象处理，不能统一填 0。
“有一行但值为 NA”与“没有该坐标行”保持独立，后续比较 presence 据此区分。
指定域不会证明来源完整性、日历认证或数据新鲜度。

### 4.2 完整元组与依赖

- `P={(East, Web), (West, Store)}` 只请求这两个联合键，不能扩大成四个组合。
- Field Null 是合法分类；Entity 身份的完整非空主键及业务版本锚点仍必须有效。
- Population 只携带当前完整坐标及保留的分配合同。投影删除的客户身份不能作为隐含筛选。
- 固定 Frame 桥接的 P 固定键；pending P 每次求值。新源值可变化，固定键不重新排名。
- P、Metric 及 where 中的 Frame 依赖进入同一个调度器；不通过隐藏 compute 或递归执行枚举 P。
- 所有权跨 Session/Store 仍拒绝；同路径定义版本变化本身不构成拒绝理由。

### 4.3 选择并不等于任意物理下推

普通身份/字段域可以在满足语义前提时转换成完整键半连接。
窗口依赖可能超出输出时间域，内部样本可能多于输出坐标，分配分母可能涉及未选择成员。
优化器必须保留这些依赖，不能机械注入全局 `in_population(P)`。
也不能先按细粒度得到最终均值/比率，再对这些结果重聚合来冒充所选粗粒度指标。

静态类型、根、时间角色、单位、合法路径及必需来源资格照常检查。
域外事实不再因“域外”本身产生错误，但域内所需分类缺失、非法分配、
采样缺口和来源故障不能被选择、结果 Top 或 where 隐藏。
P 的生产依赖若失败，不能当作空域。显式空字面域在静态准备合法后可跳过不需要的业务读取；
不要求为证明“空”扫描全域，也不吞掉已消费依赖自身的失败。

## 5. 时间、标量与空域

### 5.1 两种时间坐标

| 形式 | 时间含义 | 周期能力 |
| --- | --- | --- |
| `by=(scope,)` | `[start, end)` 整个范围的一个区间坐标 | 只有精确边界、边界时区和求值终点；没有隐含周期前驱 |
| `by=(scope.by(grain),)` | 有限、有序的日历桶 | 保留名义桶、实际裁剪边界、原序号、粒度及必要日历快照 |

TimeScope 继续要求非空半开区间；`start == end` 不用来表示空 Population。
日期/无偏移字符串按显式或 Session 时区解释；aware 值保留瞬间；
naive datetime、DST 歧义和不存在的本地时刻仍拒绝。
命名日历保持认证、覆盖和冻结快照要求；不从实际业务记录最早/最晚时间生成时间域。

新增时间坐标的闭合种类为 interval，与 calendar 并列。
`FrameAxis.role` 及 Grouping 的相应种类增加 interval；不把任意范围伪装成
`grain="all"`、某个超长 month 或带缺省字段的 CalendarAxis。
interval 保存有限的冻结区间目录；单次 scope 观测只声明一条记录。
calendar 保存既有完整日历目录。目录是时间解释数据，不等于当前实际结果行。
时间范围相同的 interval 和单个 calendar 桶仍是不同种类，不自动互换。

### 5.2 结构变化是明确的破坏性变更

| 当前请求 | 目标请求 | 结构变化 |
| --- | --- | --- |
| `observe(M)` | `observe(M)` | 仍为零轴整体结果 |
| `observe(M, during=scope)` | `observe(M, by=(scope,))` | 零轴变为单 interval 轴 |
| `observe(M, during=scope, by=(region,))` | `observe(M, by=(scope, region))` | 增加 interval 轴 |
| `observe(M, during=calendar)` | `observe(M, by=(calendar,))` | 保留 calendar 轴，端点权威改由 by 取得 |
| `observe(M, members=P, by=P)` | `observe(M, by=P)` | 保留指定身份域，取消重复成员选择 |

单桶不等于零轴，一行不等于标量。一个月的日历查询和两个月的同类查询都保留时间轴。
普通聚合数值可以不变，但 pandas 坐标、指纹、结果匹配及持久描述会变。
多事实根仍使用各自声明的业务时间角色映射到同一个输出时间域，不要求物理时间字段同名。
需要时间角色却没有声明的规约仍拒绝，不能从 where 中猜测。

`by=()` 没有时间域，因此窗口和必须具备有界时间的采样不能借用系统时间或上一轮端点。
显式时间谓词下的零轴普通聚合仍可表达，但不等价于带时间坐标的观测，
也不能靠时间谓词启用窗口。

本提案不引入自动 squeeze 或通用去轴方法。现有 `reduce(by=())` 是明确的结果统计，
不是对任意 Metric 的原样去轴；不能用 sum 替代均值、比率、去重或窗口重算。
需继续组合的期间总量保留时间坐标。真正要求零轴的调用方须显式选择合法统计或重新构造请求。

### 5.3 空域、零轴与 presence

| 域 | 行数及意义 |
| --- | --- |
| 默认 `by=()` | 单元域 `{()}`；没有事实仍得到一个零轴结果单元，值依规约政策 |
| 显式零轴 Population `{()}` | 一个单元；不携带已投影删除的身份筛选 |
| 显式零轴 Population `{}` | 零行零轴；不是“值为 null 的标量” |
| 非零轴空 Population | 零行，保留完整轴/值 schema 和必要冻结目录 |
| 一个无事实的 interval 桶 | 一行，保留区间键，sum 为 null；不是零行 |
| 非空 P 中缺少事实的身份 | 保留身份行；不把不存在的事实伪造成存在或把未知值填 0 |

空域不隐式转成默认 `()`，不扩展到全域，不改变存储/内存的空 schema。
通过显式域产生的行在比较时属于“端点存在”；其值 NA 与另一端没有该行分别处理。
已固定的日历目录序号不会因为空结果或过滤被压缩。

## 6. 窗口与分配

### 6.1 统一端点绑定

绑定器从 by 的唯一时间轴一次取得有限输出坐标与端点。interval 的端点为 end；
calendar 每个被请求桶的端点为其实际 end。输入 Population 的 calendar 仍来自
完整声明 CalendarAxis；窗口前驱按捕获的 Grain/日历权威查找，不能以结果筛选后的行
替代目录或压缩原序号。结果 where/top 不会重新执行或改变已完成的窗口。
没有输出时间轴的 `.window(...)` 在构造时拒绝，不增加另一个隐含端点参数。

| 规则 | interval | calendar |
| --- | --- | --- |
| 无窗口 | 对 `[start,end)` 普通聚合 | 对各实际桶区间普通聚合 |
| `all_history()` | 所有合格贡献中 `< end` 的部分 | 对每个端点独立计算 |
| `trailing_duration(Δ)` | `[end-Δ,end)`，真实经过时间 | 同左，按各端点 |
| `since(a)` | `[a,end)`，要求 a 早于端点 | 对所有请求端点成立 |
| `to_date(grain)` | 按终点找到刚结束或正在结束的 reset 期间；不要求显示区间包含于该期间 | 保留名义输出桶必须包含于一个 reset 期间的规则 |
| `trailing_periods(n)` | 拒绝：任意区间没有可推导的周期前驱 | 要求完整桶及已获资格的周期/日历前驱 |

interval 的 to_date 选择继承旧标量端点语义：选唯一满足 `left < end <= right`
的 reset 期间，实际贡献范围为 `[left,end)`。end 恰为期间边界时归刚结束的期间，
在期间内部时归该期间；不通过任意减一微秒确定归属。
即使显示区间跨数月，也只按此端点规则求值，不声称数值覆盖整个显示区间；
`.show()`/审计同时披露输出区间和实际窗口。
calendar 的 to_date 则仍有桶结构约束，不能用一个跨月周桶冒充月内桶。
同端点不自动意味着两种请求的窗口准入完全相同。

1 月每日 trailing 7 天在月初需要 12 月事实；P 不裁掉这些依赖。
显式 where/Metric 内在时间条件仍会裁剪贡献，窗口不得取消这些条件。
期间总量、日级值相加和逐日窗口相加是不同问题，不作隐含互换。
DST 的民用日与 24 小时仍不同，认证日历覆盖不足仍失败。
多 Metric 可以拥有不同窗口规则，但共享本次唯一输出端点域。

### 6.2 membership 与分配

先确定每个原始贡献在原关系合同下到桶的映射及分配因子，再选择 P 中的输出桶。
选择桶不重算分配分母，不把未选择桶的份额重新分给所选桶。
完整关联元组及独立分配因子保留；一笔事实对同一桶的重复链接只计一次，
数值相同的不同事实仍是不同贡献。

独立数值反例：一笔 100 按既定权重给 A/B 各 50%，`by={A}` 的值为 50，
不是 100。全域 A+B=100；局部域只输出 A=50，不再因缺 B 报覆盖错误。
多分配路径/复合分类仍必须验证共同根与明确关系，选择不能绕过这些合同。
结果当前行的份额分母与来源分配权重是不同概念，本提案不改变前者的既有算子定义。

## 7. Population.count()：闭合的身份域计数

### 7.1 必要的是计数对象，不是独立名称

当前仓库的 member_count 调用集中在 7 个测试/类型验收 Python 文件及 DSL 示例；
库内其余引用负责导出、Help 和表达式绑定，未发现业务模块主动调用它。
这不证明外部用户无人使用，也不以既有调用数量作为保留独立入口的依据。
典型用途是计已选名单，或作为收入/名单人数的分母：
`test_query_results.py::test_explicit_members_and_member_count` 使用 P={1,3,999}，
按 P 筛选后的实际贡献只有 1、3 时，收入 30、名单人数 3、每人收入 10。
`lazy-analysis-dsl-examples.md` §5 则说明无事实身份也计入名单，且不要求观察期间。

| 表达式 | 计数对象 |
| --- | --- |
| `entity.count()` | 来源中实际存在的完整贡献行，受该事实规约的条件和时间约束 |
| `field.count()` | 实际贡献中的非 NA 字段值 |
| `field.count_distinct()` | 实际贡献中的不同非 NA 字段值 |
| `population.count()` | 显式身份域中的不同完整身份，不以当前事实或档案存在为条件 |
| `frame.reduce({"n": mv.row_count()})` | 当前结果坐标行，属于结果统计，不是 observe 的 Metric 输入 |

因此不能用 `entity.count().where(mv.in_population(P))` 替代名单计数：
上述例子只得到 2，分母会从 3 变成 2。field.count 还受字段空值影响；
结果 row_count 虽可单独取得 P 的基数，但返回的是 Frame，不能作为 Metric
直接与收入在同一 observe 公式中组合。

计数对象需要独立表达，方法名称可以统一。推荐采用与 Entity/Field 一致的
接收者 `.count()` 语法；由 P 自身捕获被计数域，不再导出 mv.member_count，
也不新增通用 mv.count 分派入口或兼容别名。这里只调整表达入口，
不改变以下身份域、分组、空值、条件和窗口规则。

### 7.2 捕获与计数对象

P 必须是单个 Entity 的完整身份 Population，保留全部主键、版本锚点及必要 Frame 依赖。
Field 类别集合、calendar、任意多轴域均不能当成员计数输入；坐标行统计仍属于 row_count。
捕获不读行。无所属的字面 P 可由 observe 绑定；有所属的 P 必须与接收 Session 一致。
P.count() 与 Q.count() 各捕获自己的域；不以“第一个计数”设置全请求隐含成员。
只含域计数的指标仍具备真正的 P 求值依赖，无需虚构事实表来驱动执行。
返回值为可在 observe 中组合的惰性 Metric Deferred，不立即枚举 P 或返回 Python int。

单元域整体计数为 P 中不同完整身份数。字面声明但无事实的身份仍计数；
空 P 计数为 0。是否存在于当前来源不是已声明身份计数的额外筛选。
它不是有订单客户数、事实行数、输出坐标数，也不声称每个声明身份已被来源证实。

### 7.3 分组、筛选与时间

| 输出形状 | 成员计数 |
| --- | --- |
| 零轴 | P 中不同完整身份的个数 |
| 同 Entity 身份轴 | 每个输出身份在 P 中为 1，否则为 0 |
| 该 Entity 的合法单值分类 | 数 P 中落入各完整分类键的不同身份 |
| 时间轴 | P 在各请求时间桶重复使用；不因该桶没有事实而减少 |
| 时间与分类组合 | 先按身份/分类定位 P，再在相应时间桶计算该成员数 |

分类继续只接受可证明的直接同 Entity 单值字段/身份；不新增任意多跳或多值分类支持。
字面身份不存在导致必需分类无法读取时仍失败，不能发明分类或悄悄删除该身份。
多对多 allocation 不自动把 P.count() 变成小数加权人数；需要另一种指标定义时显式建模，
本提案不增加权重计数策略参数。

where 只作用于事实贡献规约，不重新选择 P.count() 已捕获的 P。
需要换计数人群，先构造新的 P。只有域计数、没有可承接条件的事实规约时，
传 observe.where 或只对该计数施加 Metric.where 应拒绝，并建议修改 P；
不接受一个完全无效果的过滤。混合公式中的条件只绑定实际事实规约，
不再因为计数 P 无法读取订单渠道等事实属性而拒绝合法的事实过滤。

窗口作用于整条公式中的事实贡献；显式 P 不随回看长度重新选择。
例如窗口收入除以 P.count() 的分母仍是显式 P。
只有域计数的表达式不需要窗口；附加没有事实时间意义的窗口应拒绝。
按每个时点重选“当时活跃客户”不是静态 P 的含义，不从时间桶推导有效成员。

## 8. 结果对齐、持久化与恢复

### 8.1 对齐规则

| 两侧结构 | 默认与显式行为 |
| --- | --- |
| 零轴与零轴 | 保持标量对应 |
| 各自只有一个 interval 轴 | 可唯一推导轴对；默认按精确区间身份等值对应 |
| 各自只有一个 calendar 轴 | 保留现有唯一推导及日历匹配规则 |
| 时间加分类轴 | 显式 on 覆盖全部轴；非时间轴按完整键等值 |
| 零轴与任意时间轴 | 轴数不符，拒绝；不广播、不自动删轴 |
| interval 与 calendar | 种类不符，拒绝；即使范围和行数相同也不自动互换 |

interval 等值要求精确 start/end 和规范化边界时区一致，不按局部 token、名称或行号相等。
不同期间总量默认不是相同坐标；exact 拒绝无对应实际行，outer 保留两端独立行及 presence。
显式 `match=mv.ordinal()` 扩展为支持 interval 对 interval：
双方各有一个声明区间，按原唯一位置一一对应。允许不同日期和时长，规范化边界时区须一致；
这是调用方明确的期间比较意图，不证明期间等长或具有相同业务覆盖。
interval 不支持 previous_year/period_offset；需要周期语义时用完整 calendar 桶。
calendar 的 ordinal/offset/year 仍要求各自现行完整名义桶及快照相容性。

interval ordinal 和 calendar match 都产生保留左右端点描述的配对轴；
不自动把配对结果投影为当期轴，不放宽已配对轴再次对齐限制。
不带 match 的 interval 等值对应保留 interval 轴，不生成配对轴。
值路径、单位、类型、NA、零分母及完整实际行域 exact/outer 检查保持独立。

interval 目录中每条记录以精确 start/end 和规范化边界时区标识；局部目录编号不是
跨结果身份。无 match 的等值对应合并双方完整目录并按该身份去重；
outer 的实际行仍只取两侧实际键并集，不把目录中无实际行的区间补成结果行。
不同区间可以互相重叠，也可以携带不同边界时区，均作为不同坐标保留；
它们不构成日历分区，不合并贡献，也不声称跨行相加得到去重后的原 Metric。

where/top/select 保留完整 interval 目录；按既有轴 reduce 保留仍在结果中的
interval 轴及目录，删除该轴才移除对应目录。上述操作不按剩余实际行重编号或缩减声明域。
interval ordinal 的“一个声明区间”严格指双方冻结目录各有一条记录；
outer 合成多区间后，即使 where/top 只剩一行，也不取得 ordinal 准入。
无 match 的等值对齐不要求单记录目录，因此这类结果仍可继续等值对齐。
空实际域仍保留所需目录，exact/outer 只检查实际完整键域；双方实际域均为空可通过 exact，
空零轴域与单元零轴域在 exact 下不匹配，outer 保留单元端点及缺侧 presence。

增加 interval 轴后，原来的“同地区两期零轴/分类结果直接 compare”需要显式期间对应。
只有一个桶不能作为自动 ordinal 的理由，普通字段同名也不能新增自动轴匹配。
零轴预算与期间结果不再结构相同；本提案不新增隐式广播或任意数据导入来掩盖此变化。
已有 reduce 仍按其公开统计含义工作，不承诺任意 Frame 的无损单例轴消除。

### 8.2 数据与状态边界

所有上述输出仍为 AnalysisFrame，保持 expression/memory/stored 三种载体。
compute 不创建发布 Run；persist 发布已完成数据；结果阅读不回源。
新增 interval 的轴目录、精确边界、时区及配对映射必须是有类型、可验证、冷恢复的数据，
而不是审计字符串、生产者回调或重新加载的 TimeScope。
interval 完整声明目录及实际键到目录的引用随载体转换、持久化和冷恢复保持不变；
不能只保存剩余行的区间，再从这些行重建声明目录或 ordinal 准入。
持久 calendar 选择保留完整冻结目录和原序号；输出行只包含选择的实际键。

计划/计算指纹必须包含新域语义、时间变体、显式计数域和消费的分配合同；
不要求保留旧 observe 指纹。物化结果仍不保存可执行 Metric/Population 源图。
新格式须使用新 Store generation 和相应输出协议版本；不把 interval 偷写进旧 calendar 协议。
具体版本号在实施切换时从届时基线分配，不在设计文档预占。
本次选择整体 breaking cutover，不设计旧 Store 双读、自动迁移或旧参数回放。
保留旧版本环境读取旧结果；新版本不修改旧字节，也不将旧期间标量自动解释为 interval。

## 9. Session 其他公开 API 的完整优化方案

### 9.1 生命周期模块

目标模块公开入口为 create、resume、recent、inspect、reconcile_run。

```python
def create(
    name: str,
    *,
    question: str | None = None,
    timezone: str | None = None,
    domains: str | Sequence[str] | None = None,
) -> Session: ...

def resume(session_id: str) -> Session: ...
def recent(*, limit: int = 20, cursor: str | None = None) -> SessionSummaryPage: ...
def inspect(
    session_id: str, *, run_limit: int = 5, run_cursor: str | None = None
) -> SessionInspection: ...
def reconcile_run(*, session_id: str, run_id: str) -> None: ...
```

| 当前入口/参数 | 决策与原因 |
| --- | --- |
| `get_or_create(name, ...)` | 删除，替换为严格 create；同名已存在报错并给出真实 ID，调用者显式 resume。不把可能创建、恢复和改写元数据合成一个入口 |
| `name` | 创建时必填的项目内唯一可读名称；拒绝空白及两端空格，保留大小写，不靠隐式规范化消歧 |
| `question` | 创建时可选关键字描述；None 表示无描述。保持描述性，不成为计算约束；本提案不新增元数据编辑 API |
| `report_timezone` | 替换为 `timezone`；None/省略时采用创建时的系统时区，显式值支持 IANA/规范化固定偏移。解析结果创建后冻结 |
| `domains` | 保留单名称/非空序列/None；规范化为排序去重 tuple，None 为全域。创建后冻结；不提供“空列表等于全域”的隐式规则 |
| `resume(identity, by=None)` | 改成精确 session_id；取消名称/ID 猜测及 by 选择器。名称通过 recent 的真实结果定位 |
| `current()` | 删除；无项目共享的隐含当前 Session，调用方保存显式返回值和 ID |
| `recent(limit, cursor)` | 保留有界只读发现；limit 1..100，cursor 为返回的 continuation |
| `inspect(name, run_limit, run_cursor)` | 按精确 session_id 只读检查；保留 Run 页参数，不因检查触发恢复 |
| `abandon_run(session_id, run_id)` | 重命名 reconcile_run；它核对发布结果及清理遗留资源，不是取消活跃查询 |

create 验证项目配置，原子建立身份与冻结上下文；不得覆盖同名 Session。
timezone=None 只在创建时解析系统环境，优先使用可识别的 TZ/IANA 系统配置；
无法识别 IANA 时，沿用现有本地固定偏移回退及可见诊断。
将规范化名称和解析方式保存到 Session；不把 None 或动态“system”写成运行时权威。
显式非法 timezone 仍报错，不回退系统值。instant/time_scope 未指定覆盖时区时，
使用 Session 的已冻结值；resume 读取保存值，不因 TZ、系统时区或执行宿主变化而重新解析。
resume 在现有 writer guard 下执行必要恢复，只打开已有 Store，不创建缺失身份，
不需要当前模型/来源；无遗留工作时不改写 Session 元数据。
reconcile_run 保留归属校验及 writer guard：活动 writer 冲突报错；成功 Run 不可当失败放弃；
失败/未完成 Run 的真实状态与原事务一起核对，绝不重算来源来确定结果。
session_id 与 run_id 在模块级修复入口都有作用，不能省掉所有权检查。
recent/inspect 不创建 Store、不激活、不恢复；分页仍确定、有界，不用全量列表取代。

移除 current 指针后没有隐含 activation；updated_at 只反映已提交的 Session 状态变化，
例如新发布、失败状态或恢复状态变更。单纯 resume/inspect/compute 不刷新它，
更不能把它解释为数据新鲜度。

### 9.2 全部实例方法

| 当前方法 | 目标及参数结论 |
| --- | --- |
| observe | 使用 §3 签名；删除 during/members |
| members(entity, at, where) | 保留方法，entity 改为 resolved Entity；at 仍为显式版本锚点，where 仍筛选来源身份。它与已删除的 observe.members 参数不是同一入口 |
| source_bindings(bindings) | 删除；当前 Frame 路径未消费绑定，且参数化 JSON 未获执行资格。不保留未来功能空壳 |
| instant(value, timezone) | 保留；value 必需，timezone=None 使用 Session 冻结值；返回精确 InstantAnchor |
| time_scope(start, end, timezone) | 保留；返回拥有精确边界的 TimeScope，可作为 by 中的 interval，或通过 .by(grain) 构造 calendar |
| frame(population) | 保留；一般多轴/零轴 Population 的无命名值 Frame，不能由 read(fields={}) 替代 |
| read(population, fields) | 保留；一个完整 Entity 身份域及非空命名标量字段，缺少必要属性映射仍失败 |
| values(by, columns, rows) | 保留；by 缺省改为 ()，columns 声明名称/单位，rows 为完整键和值。仍仅支持既有字段/身份字面坐标；不手写日历/interval，不引入任意表导入 |
| persisted_frame(artifact_id) | 保留精确字符串 ID；元数据打开不访问模型/来源/完整数据载荷，不增加 Ref/Artifact 双入口 |
| population_from(frame, on) | 保留显式 frame 和必填 on，包括合法 on=()；仍只桥接普通字段/完整身份轴，不扩大到 interval/calendar/paired |
| runs(status, limit, cursor) | 保留；按发布生命周期筛选，有界分页；不伪称记录每次 compute |
| get_run(run_id) | 保留同 Session 精确读取，不要求扫描 runs |
| graph(artifact_ref, direction, max_nodes) | 删除整项；Run→Artifact 由 runs/get_run 及输出引用提供，descendants 不能表达结果衍生。无独立图语义时不保留图参数及专属公开结果类型 |
| render(max_output_bytes) | 保留文本返回及 8192 默认输出预算，None 沿用渲染协议 |
| show(max_output_bytes) | 保留打印协议与 render 的明确差异；元数据阅读不恢复/执行 |

members 输入 Entity 后，依赖由传入已解析定义显式捕获，并按接收 Session 的实际
kind/root/key/version/time 合同绑定；不能以原始版本必须相等为门槛，也不通过 Ref
重新猜测用户选择。参数修复不再引导用户把 Entity 降回路径再解析。
Frame/Population 的精确所有权检查保持不变。

frame 对已携带所需轴和类型事实的无来源字面域，以及 population_from(..., on=())，
不应仅为构造而加载无关 Catalog；仍需解析字段类型、身份结构或来源语义的路径，
按实际依赖加载接收 Session 的语义范围。
这不是放松依赖校验，而是按真实依赖决定是否需要模型。
interval 等时间域通过 `mv.product(scope, unit)` 或 `mv.product(calendar, unit)`
形成 Population 后使用 frame；unit 按 §3.2 显式构造。

### 9.3 全部属性

| 当前属性 | 目标 |
| --- | --- |
| id | 保留不可变精确身份 |
| name | 保留可读名称 |
| question | 保留创建时描述，只读 |
| domains | 保留冻结 tuple 或 None |
| project_root | 保留项目/Store 归属 |
| created_at | 保留创建时间 |
| updated_at | 保留已提交状态更新时间，采用 §9.1 新语义 |
| report_tz_name | 替换为 timezone，返回规范化字符串，与 create 参数一致 |
| report_tz | 删除公开 tzinfo 派生表示 |
| report_tz_resolution | 内部化恢复表示；show 可披露 IANA/固定偏移事实 |
| catalog | 保留惰性语义目录；不声称每次属性读取均为纯内存操作 |

Session 仍不能直接构造，不增加 public close/unpersist、默认期间或默认成员。
底层连接按执行调用管理，固定结果独立保留数据。

## 10. 目标用法与迁移

以下变量如 revenue、region、customers 均须从显式来源定义或已构造 Population 获得；
示例不靠旧 Frame 恢复 Metric。新签名尚不可执行。

```python
import marivo.analysis as mv

session = mv.session.create("revenue-review", timezone="Asia/Shanghai")
scope = session.time_scope(start="2026-01-01", end="2026-02-01")

whole = session.observe({"revenue": revenue}, by=(scope,))
daily = session.observe({"revenue": revenue}, by=(scope.by(mv.grain("day")),))

# Explicit joint domain; no repeated predicate for the same domain.
customer_periods = mv.product(scope, customers)
per_customer = session.observe({"revenue": revenue}, by=customer_periods)

# The input cohort and output classification have different roles.
by_region = session.observe(
    {"revenue": revenue},
    by=(scope, region),
    where=mv.in_population(customers),
)

# Explicit denominator, including selected customers without activity.
per_person = session.observe(
    {
        "revenue": revenue,
        "people": customers.count(),
        "per_person": revenue / customers.count(),
    },
    by=(scope,),
    where=mv.in_population(customers),
)

saved = whole.persist()
reopened = mv.session.resume(session.id).persisted_frame(saved.artifact.id)

# A single time domain becomes a Population without introducing another axis.
unit = mv.coordinates(by=(), keys=((),))
time_domain = mv.product(scope, unit)
domain_frame = session.frame(time_domain)
```

两期整体比较明确选择期间对应：

```python
previous_scope = session.time_scope(start="2025-12-01", end="2026-01-01")
previous = session.observe({"revenue": revenue}, by=(previous_scope,))
change = whole.compare(previous, match=mv.ordinal(), how="exact")
```

旧 `members=P` 且不按成员输出的请求迁移为 `where=mv.in_population(P)`；
原 where 若存在，通过闭合谓词的合法组合显式保留，不能覆盖掉原条件。
旧 `member_count()` 配合 members=P 改为 `P.count()`，不保留 mv.member_count(P) 别名；
不要换成来源 Entity/Field 的 count，也不要把结果统计直接塞入 observe。
旧“身份分组匹配 members 自动补域”改为显式 by=P；分类补域则显式构造所需完整域。
旧 during=calendar 且 by=P 的请求迁移为包含同一 calendar 的联合 P；
若 P 已有时间轴则校验并使用它，不重复相乘。

## 11. 实施影响与披露

| 边界 | 必须同步的内容 |
| --- | --- |
| 公开入口/类型 | Session 方法、生命周期、Population.count、删除 member_count 导出、product、Grouping interval、FrameAxis interval、公开导出快照 |
| 捕获/IR | 删除隐含 members/独立 during 绑定，增加显式计数依赖、interval 及指定域语义版本 |
| 计划/来源/本地执行 | 域先明确；按请求单元格准备所需贡献；保留窗口/分配/内部样本；移除旧全域覆盖断言 |
| 结果/匹配 | interval 的数据目录、等值、显式 ordinal、配对映射、空域和完整导出 |
| 持久化 | 新协议与 Store generation、收据校验、冷恢复及旧格式拒读 |
| 生命周期/遥测 | 删除 current 激活，区分 create/resume/reconcile；同步能力 ID、question/身份采集及时间属性消费者 |
| Help/错误/展示 | 新签名、输入取得、效果、空域/窗口限制、真实修复、可达性和预算；旧入口不保留别名 |
| 文档/示例 | 当前拥有者合同、API 文档、CLI、latest EN/ZH、执行示例及对应漂移测试 |
| 相关提案 | attribute 的两端期间改为显式时间域；旧标量/分类比较示例增加期间对应；共同输入/贡献分解要求不变 |

实施顺序：先冻结域/时间/计数/对齐合同，再改绑定与执行，随后持久化及生命周期，
最后统一公开披露和验收；不能先改签名就声称语义完成。
复用现有编译器、调度器和发布事务，不建立第二套执行系统。
普通身份桥接的资格保持不变，不能为了新时间轴而暗中开放日历/配对轴回源。
非平凡日历 Population 子集选择不属于本版；结果筛选和冷恢复不增加来源选择 API。
归因提案的父域共同输入与贡献守恒需单独对齐，不能沿用“新增 by 不是选择域”的旧措辞。

`marivo-analysis`/`marivo-semantic` 可能需要后续同步，但本次授权仅限设计文档，
没有授权编辑这两份打包技能。正式实施若涉及它们，须按仓库规则取得明确授权；
未完成其对齐时不能宣布公开面完整切换。

## 12. 验收矩阵与设计核验

下表是未来实施的必要验收，不是本次通过记录。优先复用现有 fixtures、计划和原始失败证据，
每项对独立数值/坐标/presence oracle 验证；不通过复制实现表达式充当 oracle。

| ID | 场景 | 必须证明 |
| --- | --- | --- |
| O01 | 新签名/旧入口 | during、members、独立 member_count 和旧 Session 入口不再公开或静默兼容；Population.count 的 Help/导出/修复一致 |
| O02 | 范围总量 | scope 单桶数值与原范围普通聚合一致，结果明确多一个 interval 轴 |
| O03 | 一桶/多桶 | 结果轴数不因桶数或实际行数变化；无自动 squeeze |
| O04 | 指定身份子域 | 来源有额外身份时只返回 P；1→10、3→20、999→null，不再要求覆盖 ID 2 |
| O05 | 联合键 | East/Web、West/Store 不扩大成四组；Null 类别和复合身份保持区别 |
| O06 | 无事实/空域 | 单桶无事实、空 P、零轴 `{()}`/`{}`、all-null sum 和空 count 分别正确 |
| O07 | 选择上下文 | 固定 Top 键在来源变化后不重排；pending 选择重新求值；新值不承诺旧快照 |
| O08 | 字段投影 | 所选客户按地区汇总与只选择这些地区的所有客户得到不同 oracle，不能恢复已投影身份 |
| O09 | 多根 | 指标各根按自身时间角色及明确输出坐标规约，无 fanout 乘法 |
| O10 | 月初窗口 | 1 月输出的 trailing 7 天包含 12 月依赖；显式硬时间谓词仍能裁剪 |
| O11 | interval 窗口 | since/all_history/duration/端点 to_date 合法；trailing_periods 与无时间轴窗口拒绝 |
| O12 | calendar 窗口 | 完整桶、partial、reset containment、DST、认证前驱及覆盖边界保留正确行为 |
| O13 | 时间域与结果筛选 | calendar/interval × 零轴单元域可构造时间 Population；Frame where/top 保留完整目录与原序号，不因此开放日历轴回源或非平凡日历 Population 子集选择 |
| O14 | 分配 | 100 的 A/B 各半分配选 A 得 50；不重归一、不重复链接计数、不合并同值不同事实 |
| O15 | 显式身份域计数 | P={1,3,999} 的 P.count() 得 3、空 P 得 0；与按 P 筛选后的来源 Entity.count 得 2 区分，不从 by/where 推断，不立即执行 |
| O16 | 多个计数域 | 同公式 P/Q 各自捕获/调度/计数，无隐含全局成员域 |
| O17 | 计数分组/条件 | 合法单值分类、缺属性失败、时间重复、事实过滤不改分母；无效果条件拒绝 |
| O18 | 非可加指标 | mean/ratio/weighted/distinct 的指定域直接求值不替换成细组结果重聚合 |
| O19 | interval 对齐 | 同区间默认等值，不同区间 exact 拒绝、outer 缺侧、显式 ordinal 配对；无自动日期猜测 |
| O20 | 结构与 presence | 零轴/interval/calendar/paired、时间+分类的 on 规则、NA 与缺端点、零分母及单位正确 |
| O21 | 生命周期 | create 同名不覆盖、resume 精确 ID 无创建/current 副作用、inspect 只读、必要恢复原子且不重算 |
| O22 | 时区/域 | 省略时捕获创建时系统时区，显式偏移/IANA 优先，固定偏移回退可诊断；冻结 domains；修改 TZ/宿主后 resume 不漂移，无旧属性双入口 |
| O23 | 依赖错误 | 空域不吞掉 P 的生产失败；必要类型/来源/分配错误不变成空结果；无全域扫描证明空 |
| O24 | 完成与发布 | compute 无发布 Run、persist 不重读源、memory/stored 的值/轴/presence/时间目录一致 |
| O25 | 冷恢复 | 删除模型/来源后精确读取与 interval/calendar 匹配仍成立；损坏附件报错不重建 |
| O26 | 旧 Store | 旧字节保持不变；不绕过 generation 创建，不把旧标量重解释为期间坐标 |
| O27 | 所有权 | 跨 Session、foreign FrameAxis、FrameKeyInput 及计数 P 失败；同路径版本变化不单独拒绝 |
| O28 | 全披露 | 全部保留/替换/删除入口的 Help、CLI、EN/ZH、遥测、错误、快照和授权后的技能一致 |
| O29 | interval 目录闭合 | 两个重叠 interval outer 后保留两个声明坐标；筛选为一行并冷恢复后仍可按实际键等值对齐，ordinal 仍因声明目录多记录而拒绝；不得补出无实际行的目录记录 |

正式实施需先跑聚焦语义测试和修改模块 typing，再执行未缩窄的 `make check-agent`。
相关 Runtime 的参数传输、窗口和分配数值证据另行取得；不从静态/本地结果推定远端资格。
构建安装包与离线冷恢复用于验证实际交付边界；完整 release-check 仍归发布流程。
本次文档核验只检查内容、路径、示例语法和差异，不执行尚不存在的目标 API。
初稿完成了本地链接、Python 代码块语法、围栏、验收编号和差异空白核验；
独立审查后的修订证据见审查记录。本轮未修改运行代码或执行实现验收。
