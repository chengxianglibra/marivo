# Analysis DSL 精简方案

状态：已实施并完成本地与相关 PostgreSQL 验证。设计日期：2026-10-21；实施日期：2026-10-22。

本方案以 `a264c0f7edf7952af187e7e83bc0d7592d224a28` 的实现为基线，记录已确定的
设计方向及实施边界。目标 API 已落入当前工作区，完成证据与审查记录见
[实施进度](dsl-simplification-progress.md)。基线代码片段用于说明已移除的调用方式，
不提供兼容入口。打包技能未修改。

当前合同仍由 [Population 与来源观察](populations-and-observation.md)、
[AnalysisFrame](analysis-frame.md)、[数值与当前行算子](operators-and-frames.md)、
[Semantic 表达式](../semantic/expressions.md)和[时间语义](../temporal-semantics.md)拥有。
这些 owning documents 已随实现同步；最终门禁结果由实施进度记录。

## 1. 范围与决策

| 编号 | 决策 | 变更性质 |
| --- | --- | --- |
| D1 | membership 直接进入 `Session.observe.by` 和 `Metric.per`，移除这两个公共入口独立的 `memberships` 参数；交错坐标采用显式字段定位 | 规范化保持旧请求的轴顺序、完整分配规则和逐层采样；覆盖缺陷修复单列为 D7 |
| D2 | 显式 Population 仍唯一决定完整坐标与选中键域；membership 只提供分配规则 | 保持完整域语义 |
| D3 | Frame `align`、`compare` 移除 `how`，统一采用 outer | 有意改变原默认 exact 和显式 exact 的行为 |
| D4 | 通用除法、相对变化和加权均值移除 `zero_denominator`，统一在零分母时返回 NA | 有意取消对应的 error 政策 |
| D5 | 使用 `/` 作为唯一公共除法语法，同时移除 Row、Metric 和 Column 的 `divide` 入口 | 移除失去独立参数后的同义入口 |
| D6 | 不新增业务定义种类、策略对象、全局配置或公共包装类型 | 限制本次设计规模 |
| D7 | 外部 `by=P` 检查必要输出键覆盖；内部 `per(P)` 允许选择样本子集 | 按现有 owning contract 修复基线缺失的外部覆盖检查，明确承认运行行为变化 |

Attribute 算子整体不在本次范围内，后续单独重构。其 `AttributeEndpoint`、
`attribute_endpoint(..., memberships=...)`、`Session.attribute.by` 和 `parent_how`
保留现有合同，不移动分类声明，不要求两端改用新入口，也不将父域政策固定为 outer。
Attribute 内部的分解公式、贡献资格和 method facts 不在本次重新设计。

通用 Semantic 表达式的除法规则仍适用于作为 Attribute 输入的 Metric；这是共享表达式
合同变更，不新增 Attribute 专属政策。必要的模型调用示例更新与算子重构分别处理。

已可从定义推导的默认时间角色、唯一合法标量路径、唯一内部排序时间轴、认证期间和
直接 shift 对应继续沿用现有入口。本次不为它们新增语法，不新增命名比较、排名或关联分析定义，
也不修改 `unmatched`、`nulls`、`contribution` 等独立参数的含义。

## 2. membership 与坐标使用同一入口

### 2.1 普通观察域

保留 `mv.membership(fields, via=..., unmatched=...)`。关联字段、路径和 unmatched 政策
都由该描述捕获；它不是 Population，不自行选择或枚举完整键域。

```python
allocation = mv.membership(
    (tag, channel),
    via=paths,
    unmatched="exclude",
)

# Baseline API (retired).
pending = session.observe(
    metrics,
    by=(period, tag, channel),
    memberships=(allocation,),
)

# Implemented API.
pending = session.observe(metrics, by=(period, allocation))
```

普通 `by` 元组中，membership 默认在所在位置展开自己的有序字段元组，同时捕获完整分配
规则。例如 `(region, allocation, period)` 中 allocation 含 `(tag, channel)`，输出次序就是
`(region, tag, channel, period)`。字段顺序与关联分配是两件事；展开不得拆散规则本身。

**交错坐标使用显式字段定位。** 若 membership 的非首字段在同一层元组中另有显式位置，
该字段只在显式位置生成坐标，membership 在自身位置只展开未被定位的字段。首字段必须
由 membership 自身展开，作为规则在坐标序列中的起点；它不能再单独出现。

```python
# Current API: correlated member fields need not be adjacent.
pending = session.observe(
    metrics, by=(tag, customer, channel), memberships=(allocation,)
)
sampled = revenue.per(
    (tag, customer, channel), memberships=(allocation,)
).mean()

# Proposed API: channel supplies its position, not another allocation rule.
pending = session.observe(metrics, by=(allocation, customer, channel))
sampled = revenue.per((allocation, customer, channel)).mean()
```

上述两种新调用都规范化为 `(tag, customer, channel)`，allocation 仍完整捕获 `(tag, channel)`
及其路径/unmatched。不能把它拆成两个单字段 membership：这会把关联元组变成独立因子。
显式定位是次序信息，不是重复定义路径或分配政策；连续字段仍优先使用简写 `(allocation,)`。

解析必须分两步完成，不能边扫描边去重：

1. 收集本层全部 membership 与显式普通坐标。字段匹配复用现有坐标身份规则，包括所属
   实体、字段、路径、时间锚点等；不能按显示名或 dtype 匹配。普通 Grouping 只有与该字段
   的完整坐标匹配时才可定位，identity/time-domain 等不同角色不能代替字段位置。
2. 按输入顺序输出显式坐标，并在各 membership 位置展开未被显式定位的字段；随后验证
   全部坐标唯一、每条规则字段的相对顺序与捕获顺序一致。规则仍只绑定一次，完整保存。

通用保序迁移为：在旧坐标元组中，把每条规则的首字段替换为该 membership，删除
`memberships=`，保留其余字段原位。多条不重叠规则也适用。例如规则 R 捕获 `(a, b)`、
S 捕获 `(c, d)`，旧坐标 `(a, c, b, d)` 迁移为 `(R, S, b, d)`；展开结果仍为
`(a, c, b, d)`，R/S 的相关性各自保留。无需枚举 Population、执行查询或事后重排 Frame。

同一层以下输入均拒绝，不静默去重、重排或选择优先规则：

- 显式普通坐标重复，或 membership 首字段另有显式位置。
- 两个 membership 覆盖同一坐标，即使参数完全相同。
- 一个关联描述内部包含重复坐标。
- 定位后规则字段错序，例如 allocation 为 `(tag, channel)` 时的 `(channel, allocation)`。

### 2.2 显式完整 Population 域

```python
# Baseline API (retired).
pending = session.observe(metrics, by=P, memberships=(allocation,))

# Implemented API.
pending = session.observe(metrics, by=(P, allocation))

# A complete domain without allocation remains unchanged.
pending = session.observe(metrics, by=P)
```

入口只接受两种结构，不混合猜测：

| 结构 | 坐标与键域 | 分配规则 |
| --- | --- | --- |
| 字段、普通 Grouping、时间坐标和 membership 的有序元组 | 沿用现有观察分组域规则；按 2.1 的展开和显式字段定位确定顺序 | 来自元组中的完整 membership |
| 单个 P，或 `(P, allocation, ...)` | P 唯一决定完整坐标、顺序和选中联合键 | 只绑定 P 已有坐标 |

显式域元组要求 P 位于第一项，其后至少一个 membership。`(P,)` 使用 `by=P` 表达。
拒绝多个 Population、P 后追加普通字段/时间坐标、P 不在首项，或规则字段不属于 P。
membership 的字段相对次序必须与 P 坐标次序一致；不为适配规则而重排 P。
P 已有完整顺序，不接受额外字段定位。例如 P 的轴为 `(tag, customer, channel)` 时，
`(P, allocation)` 直接保留这一交错次序；无需把 channel 再写一遍。

需要时间×分类或其它笛卡尔域时，先显式构造 `mv.product(...)`，再将其作为 P。
两个已相关的完整域不能被消费方自动分解成独立轴或隐式相乘。

### 2.3 内部采样

`Metric.per` 接受单个 membership 或由字段、现有合法采样 Grouping 和 membership
组成的非空元组；显式 Population 使用相同的 `(P, allocation, ...)` 结构。
普通元组采用 2.1 的同一保序算法；各层分别解析，不能用外层的显式字段位置改变内层次序。

```python
# Baseline API (retired).
sampled = revenue.per((tag, channel), memberships=(allocation,)).mean()
complete_sampled = revenue.per(P, memberships=(allocation,)).mean()

# Implemented API.
sampled = revenue.per(allocation).mean()
complete_sampled = revenue.per((P, allocation)).mean()
```

语法展开不放宽现有采样准入：普通字段采样仍要求现有支持的 native Dimension/TimeField；
不能借 membership 引入 routed scalar、Measure 或原来不支持的采样形状。
显式 Population 继续遵循其既有采样准入与空/零轴域合同。

## 3. 规范化与语义不变量

### 3.1 规范化输出

每一次 observe 或 per 消费独立生成：

```text
(ordered complete axes, optional explicit Population domain, allocation rules)
```

复用现有 `CoordinateBinding`、`SampleDomain`、`MembershipBinding` 及其校验。
共享两步字段展开、显式定位、重复检测与域绑定规则，保留 observe 和 per 各自的准入检查；
不为消除几段解析代码建立新的公共抽象。该规范化不得触发 schema/业务行读取或创建 Run。
显式定位只决定最终轴顺序，不成为额外分配因子或持久化政策。等价简写和保序展开应产生
相同的规范化定义及指纹；不同的轴顺序、域或逐层规则仍必须区别。

分配语法的合并仅作用于上述消费入口。`mv.coordinates`、`Population.project/classify`、
`Session.values` 不因此开始接受分配规则。`Population.axes` 仍仅披露坐标，
`Population.count()` 仍统计自身完整不同元组。

### 3.2 完整输出域

- P 的完整联合键、轴顺序、Null 分类、完整身份组件及 Session 所有权不变。
- 空 P 的选中键仍为空；通过外部覆盖检查后输出零行。零轴空域与 `{()}` 继续有区别，
  不能用空域绕过必要输出键覆盖检查。
- 无事实键保持可见，并按原 Metric 的空输入政策得到值；不能只保留实际有事实的键。
- 非空全 NULL 贡献与没有贡献不同，合并声明不能改变 sum/mean 的空值结果。
- by 选择输出键，where 限制贡献，两者不自动互相代替。

#### 3.2.1 覆盖语义的 owner 与基线分歧

共享规范化只保存域和规则，**不共享一套覆盖政策**：

| 消费位置 | P 的意义 | 域外键的处理 |
| --- | --- | --- |
| `observe(..., by=(P, allocation))` | 完整外部输出域 | 必要输出键必须属于 P，否则报覆盖错误 |
| `metric.per((P, allocation))` | 本层选中的完整内部样本域 | 允许选择子集；域外样本不参加本层 reducer，不报外部域覆盖错误 |

当前 owning contract 已要求外部覆盖并允许内部子集，但指定基线的局部运行并未执行前者：
关联桶 A/web 的 revenue 为 40，B/store 为 10，P 只含 A/web 时，旧 observe 返回 40，
未报覆盖错误；旧 per(P).mean() 也返回 40。前者是本方案明确要修复的合同/实现分歧，
后者是需要保留的采样行为。不能以这两个相同的旧数值认定两种域有相同含义。

D7 独立于入口合并验收：新 observe 在该例必须报错，新 per 仍返回 40。D1 的语法等价
承诺不把旧 observe 静默丢弃域外桶视为正确 oracle；修复不能被描述成“已有检查不变”。

#### 3.2.2 外部输出域：覆盖完整必要键

对普通事实 Metric，先按既有来源与分配语义，在明确的事实条件、时间范围及 unmatched
作用后确定完整外部坐标集合 K，再在每个外部坐标下选择和补齐内部样本。K 的确定不能
先把事实过滤到内部 P 再投影外部键，也不能从已经被外部 P 裁剪的结果反推。
多事实根各自贡献必要键，不能先内连接结果而消除某根独有的键。

外部覆盖要求 K 是外部 P 的子集，检查发生在按外部 P 裁剪/补齐结果之前。内部样本选择
只改变每个外部坐标下的样本和值，不反向删除 K 中的坐标。即使选中样本完全无事实、
内部 P 为空，或者嵌套 reducer 最终得到 0/NA，这些外部坐标仍是覆盖检查的必要键。
这是一项语义执行顺序；优化可以改变物理执行方式，但不得把内部样本过滤下推成外部键过滤。

- 每个必要键都须在 P 中；按完整元组及现有 Null/身份相等语义检查，不能分别检查各轴。
- P 中无事实的键仍保留并按子 Metric 空输入政策补齐；P 是必要键的超集时合法。
- `unmatched="exclude"` 排除的出现不生成必要桶；`unmatched="null"` 生成的未知桶若是
  必要键，则 P 必须包含它。不能自动补桶、丢桶或重新归一化。
- 空 P 仅在 K 为空时合法；内部样本为空不能作为 K 为空的依据。K 含零轴键 `()` 时，
  外部域必须包含它，不能被零轴空 P 吞掉。
- 后续 select/where/Top-0、outer 对齐或固定 null 都不能绕过此检查。检查失败不发布结果。
- 修复提示应提供有界的缺失完整键，指导扩大 P 或显式限制事实贡献；不能建议隐式裁剪。
  `where=mv.in_population(P)` 是事实存在性条件：若同一事实同时分配到 A/B，按 A 选中它
  后仍可能产生 B，因此这条条件不保证能修复覆盖失败，也不能偷偷截断分配结果。

这里的域外必要键检查针对实际消费的来源输出，不通过重读数据重选已完成 Population。
Population.count 的独立计数、零轴和分组准入仍按其既有合同，不把 P 当成新的事实来源。

#### 3.2.3 内部采样域：允许子集，完整计算选中样本

先固定本层收到的完整外部坐标，再逐个外部坐标选择本层 P 的完整样本键，补齐其中无事实
的样本，完成整个子 Metric，最后执行 reducer。P 外样本不参加本层规约；P 内无事实样本
仍按子 Metric 的空输入政策参与，不能退化成仅对有事实样本取平均。
内部 P 为空时，本层 reducer 处理空样本集合，并为已有外部坐标保留结果行，不能因
外部坐标与空样本域的笛卡尔积为空就删除该外部行。非空全 NA 与空输入、零轴空/单例域
继续分别处理；值为 0 或 NA 均不等于行不存在。

内部子集选择不修改任何 membership 的原始出现身份、路径或权重，不重新归一化；
也不授权丢掉既有消费范围内的数值、关系及完整性检查。外部覆盖仅检查外部输出键，
不能把所有内外采样轴拼接后要求外部 P 覆盖它们。

嵌套 per 的每一层独立使用自己的 P 和规则。内层子集不成为外层的输出域，外层子集
不反向改写内层 P；权重 Metric 和值 Metric 均按该层完整样本完成后再配对规约。
父层已经选中的完整坐标是子层的外部坐标，子层选择或补齐样本不能将其删除；同一规则
逐层适用直至最外层输出 K。父层自身仍可按自己的 P 选择样本，这不授权子层反向筛选父层。
外部覆盖检查只针对最外层输出 K，不要求外部 P 包含各层内部样本坐标。

例如已有 customer 坐标 `{1, 2}`，内部 P 仅含无事实的 order `999`，则
`revenue.per(P).mean()` 在两个 customer 下均为 0；内部 P 为空时两个值均为 NA。
两种情况下，外部输出域只含 customer `1` 都必须报缺失 `2` 的覆盖错误。再嵌套一层
per 也不能消除这项要求。只有在确定 K 之前显式过滤事实，使 customer `2` 不再属于
实际外部坐标，才可能让仅含 `1` 的外部域通过；内部 P 的样本选择不是这样的事实过滤。

### 3.3 分配与贡献身份

- 同一个 membership 内的字段保持关联元组，不成为独立成员因子的乘积。
- 不同 descriptor 保持原有独立因子含义。
- `(完整贡献身份, 完整桶键)` 重复链路按集合出现处理；等值的不同贡献不合并。
- 多根 Metric 的各来源贡献独立完成再组合，不引入跨事实根 fanout。
- 分配不因选定 P、后续筛选或 Top 而重新归一化；加权分配继续要求显式权重来源。
- `via` 与 `unmatched` 仍表达独立意义。本次不推断多值业务分配，不替调用方选择政策。
- 同路径定义版本变化沿用既有消费规则，不增加原始版本相等要求。

### 3.4 嵌套采样

```python
# Baseline API (retired).
nested = revenue.per(fields, memberships=(inner_rule,)).mean().per(
    fields,
    memberships=(outer_rule,),
).mean()

# Implemented API.
nested = revenue.per(inner_rule).mean().per(outer_rule).mean()
```

每层的轴、显式域及规则作为完整绑定保存到来源路径解析完成。不得按字段名或 Ref
建立一个全局规则映射，不能先找到一个同字段规则后供所有层共享。
即使内外层使用相同字段，也分别执行显式字段定位及内部子集选择，保留各自顺序和域。

每个外部/内部坐标组合先完成整个子 Metric，再执行本层 reducer。per-ratio、
per-weighted-mean、零事实样本和未知样本继续有各自语义；不进行新的交换、融合或重排。
Decimal 每层的实际类型、收敛与规约顺序也保持不变。

## 4. Frame 对应固定为 outer

目标公共形状：

```python
aligned = left.align(right, on=pairs, match=rule)
compared = current.compare(baselines, on=bindings, match=rules)
```

删除这两个入口的 `how`，不提供 exact 别名或替代策略对象。`on`、`match` 的独立含义
及已有唯一推导条件保留。Attribute 的 `parent_how` 不在删除范围内。

outer 的合同沿用当前合格 outer 路径：

- align 保留左右完整联合键并集和独立 `left_present/right_present`。
- compare 保留当前值与轴，并按基期声明顺序加入合格基期独有键；本期不为各基期重算。
- 保留既有确定行序，缺侧值是 NA，presence 是不可空 boolean，不填零。
- compare 仍在本期完整冻结时间域内匹配；无法映入的实际基期时间行仍拒绝。
- paired 输入限制、所有权、完整键唯一性、轴角色/类型、数值单位、映射完整性和来源覆盖
  检查继续执行。outer 只取消该操作要求两端实际键域相等的检查。
- 尚待计算的来源端点仍须通过 D7 的输出域覆盖；outer 只能合并已合格端点的键，不能
  把来源遗漏桶当成普通缺侧行。已完成的历史 Frame 按固定数据消费，不追溯重跑覆盖检查。
- 下游可明确使用 presence 选择交集或缺侧行，但这不恢复已移除的 exact 检查合同。

“outer”是 Marivo Frame 的对应政策，不是将底层 Ibis 的所有 join 都替换成 outer。
成员筛选、关系映射和来源编译仍使用它们各自所需的 join 语义。

## 5. 零分母固定为 null

| 公共入口 | 目标 |
| --- | --- |
| `Column / other`、Row/Metric `/` | 零分母返回 NA |
| `AnalysisFrame.compare` | 相对变化仍为 `(current - baseline) / abs(baseline)`，零基期返回 NA |
| 来源 `weighted_mean(weight=...)` | 共同有效样本的权重和为零时返回 NA |
| 内部采样 `weighted_mean(weight=...)` | 完成样本 Metric 后按同一固定规则处理零权重和 |
| Row/Metric/Column `.divide(...)` | 移除；使用 `/` |

所有相关入口移除 `zero_denominator`，不保留只能传 null 的参数或策略描述。
权重仍允许原合同支持的符号，正负权重抵消为零同样返回 NA。
缺侧和值 NA 继续先按其自身语义传播，不从 NA 推断零。

零分母在数值除法之前屏蔽，避免产生 Infinity 后再替换。`0/0` 与非零数除零都返回 NA。
合法输入的单位、Decimal 结果类型、Duration tick 和物理载体合同不变。

该修改仅取消分母为零的 error 政策。`nulls="error"`、来源实际非有限值、非法数值域、
溢出合同及必要完整性检查不被后续 select/where/Top-0、外层 NA 或零乘数隐藏。
不提供自动近似、换算法或放宽输入的修复路径。

## 6. 兼容边界、内部调用与持久化

- 新公共 observe/per 拒绝 `memberships=`；Frame align/compare 拒绝 `how=`；
  通用数值入口拒绝 `zero_denominator=`，包括显式传入旧默认值的调用。
- 移除 `.divide` 的消费语法、Help 目标和修复提示，统一引导 `/`。
- Attribute 的既有 `memberships` 与 `parent_how` 继续有效。这是明确的范围边界，
  不作为新 observe/per 的兼容入口。私有 capture/binding 可以继续显式运输规范化后的
  axes、Population 和 allocation，供 Attribute 及来源执行复用；私有字段无需跟随公共
  参数一起删除。不能因公共签名收敛而丢失分配元数据。
- 仅移除承载本次已固定政策的内部字段、分支与校验；Attribute 自己的 parent-how
  字段与实现保留，避免跨算子替换。
- 指纹覆盖实际消费的规范化定义及其 owning rule version。需要变更的结构由实际 owner
  更新版本，不因省略原始调用语法而丢失层级、域、路径或 unmatched 事实。
- D7 是独立行为修复：完成前不宣称外部域覆盖已实现，也不将其加入 Attribute 的私有
  端点捕获合同。共享来源路径须保留消费方边界，不能将 observe 的新检查全局注入。
- 已完成 Frame 仍作为固定数据读取，不恢复原来源图或旧的 exact/error 执行路径。
  历史审计记录按原事实保留，不能伪称历史结果已执行新政策。
- 实施时核查数据 codec 与 method-facts payload 的实际字段依赖；只有实际受影响的
  编码 owner 才需要版本调整，不添加旧可执行定义迁移或双读。

## 7. API、Help 和文档影响清单

### 7.1 公共接口

| API | 变更 |
| --- | --- |
| `Session.observe(metrics, *, by=(), where=None)` | by 接受 membership、显式字段定位和显式域绑定元组；移除 memberships；外部 P 执行 D7 |
| `Metric.per(samples)` | samples 接受同一保序语法；移除 memberships；内部 P 保留子集选择 |
| `mv.membership(fields, *, via, unmatched)` | 捕获合同保留；补充完整规则与字段位置分离的说明 |
| `AnalysisFrame.align(other, *, on=None, match=None)` | 移除 how，固定 outer |
| `AnalysisFrame.compare(baselines, *, on=None, match=None)` | 移除 how/zero_denominator，固定 outer/null |
| 来源及内部采样 `weighted_mean` | 保留 weight/nulls 等独立参数，移除 zero_denominator |
| Row/Metric/Column 数值表达式 | 以 `/` 取代 divide；固定零分母 null |

表中是目标调用形状，不是新增运行时 type alias。实现参数类型必须采用具体的闭合联合，
不引入 Any，不将内部载体加入公共 `__all__` 或顶层 Help 索引。

### 7.2 实现 owner

| Owner | 路径与责任 |
| --- | --- |
| 公共 Analysis 捕获 | `marivo/analysis/session/core.py`；`dsl/frame_sources.py`、`source_capture.py`、`population.py`、`frame.py`、`column.py` |
| Semantic 语法与捕获 | `marivo/semantic/_deferred_semantics.py`、`_numeric_recipe.py`、`expressions.py`；`marivo/_definition/deferred.py` |
| 规范化与来源绑定 | `_definition/relations.py`、`observations.py`；`analysis/binding/observe.py`、`membership.py`、`sampling.py` |
| Frame 对应与值执行 | `analysis/core/frame_expression.py`；`methods/frame_comparison.py`、`query_values.py`、`numeric_state.py`、`_sample_numeric.py` |
| 来源数值 lowering | `semantic/_numeric_lowering.py` 及实际依赖的 `_definition/expressions.py`、`query.py`、`types.py`、compiler/native formula |
| 调度与依赖 | 实际受影响的 frame-key 收集、population condition、query preparation/fusion、membership preparation |
| 外部域覆盖修复 | `analysis/methods/local.py` 的完整域合并、来源 preparation/fusion 及检查执行链；在外部域裁剪前保留必要完整键并执行覆盖检查，内部样本域不采用此政策 |

Attribute 的公开捕获与专属 kernels 不列入交付；共享实现变更只按上述边界保持其调用成立。

### 7.3 Disclosure

更新下列 native Help 及其输入族、示例、约束和跳转：

- `analysis.session.observe`、`semantic.metric.per`、`analysis.membership`、`analysis.grouping`。
- `analysis.analysis_frame.align`、`analysis.analysis_frame.compare`。
- 来源和内部采样的 weighted-mean 页面，以及数值表达式的 `/` 合同。
- 移除 `analysis.column.divide`、`semantic.row.divide`、`semantic.metric.divide`。

主要 owner 为 `analysis/session/_disclosure.py`、`dsl/_result_disclosure.py`、
`_population_disclosure.py` 和 `semantic/_capabilities/registry.py`。
错误 guidance、待执行 `.show()`、类型方法列表、CLI/Python 解析、独立可达性与预算测试
必须同步。Attribute Help 继续描述它自己的现有参数，不能做全局文本替换。
observe/per Help 分别说明外部覆盖与内部子集，不仅列出相同输入类型。membership Help
至少包含交错字段和多规则交错示例；旧“字段与规则同时出现一律拒绝”的修复提示要改为
区分合法非首字段定位、首字段重复及定位后错序。pending `.show()` 展示规范化后的完整
轴序与各层完整规则，不能把定位字段误报为第二条分配规则。

### 7.4 文档

本次同步以下当前合同，本文保留设计依据与基线差异：

- Analysis：`populations-and-observation.md`、`analysis-frame.md`、`operators-and-frames.md`、
  `examples.md`、`algebra-theory.md`，必要时同步 Runtime 的指纹/恢复说明。
- Semantic：`expressions.md`，以及受到实际示例影响的 authoring 文档。
- API：`docs/api/analysis.rst`、`docs/api/semantic.rst` 和生成的 API reference。
- latest 英中版：`concepts/analysis-workflow.mdx`、`guides/time-and-cohorts.mdx`、
  `guides/comparison-and-attribution.mdx`、`reference/troubleshooting.mdx`。

只修改这些文件中属于本次范围的部分，保留 Attribute 当前合同。打包技能当前未写死这些
调用参数，预期无需编辑；若实际发现工作流边界也需变化，再按仓库规定单独取得编辑授权。

## 8. 验收矩阵

membership 语法规范化的对照基准是旧合法输入；D7 的外部覆盖采用 owning contract 和
独立完整键 oracle，并记录旧基线的反例，不能把其静默截取结果用作正确预期。
内部子集保持旧合法行为。政策固定的基准是旧 `outer/null` 路径与独立 oracle，不能用旧默认
exact 或 error 的结果要求新默认保持不变。

| ID | 验收点 | 必须证明 |
| --- | --- | --- |
| A01 | 普通元组展开 | 字段/时间坐标/关联描述的完整轴次序、schema 和值与旧等价请求一致 |
| A02 | 显式联合域 | P 的相关联合键、Null 类别、复合身份及所有权保持；不制造投影乘积 |
| A03 | 空域与无事实键 | 分别检查外部/内部空 P、零轴空/单例、无事实成员和非空全 NA；外部空 P 不掩盖必要键覆盖失败 |
| A04 | 分配出现身份 | 重复链路不重复计入；不同等值贡献不合并；关联字段不拆散 |
| A05 | unmatched 与覆盖 | exclude 不产生被排除的桶；null 必要桶在外部 P 中缺失时报错，内部 P 可不选；不补桶或重新归一化；与 A16/A17 联合验证 |
| A06 | 嵌套规则 | 相同字段的不同内外路径、交换层序和多层组合分别符合逐层独立 oracle |
| A07 | 完成子 Metric | ratio、weighted mean、零/未知样本和 Decimal 每层类型及收敛保持 |
| A08 | 非法形状 | 显式坐标重复、首字段重复、重叠规则、多个 P、P 外字段、错序和非法采样形状给出 typed repair；非首字段定位合法 |
| A09 | 捕获与依赖 | 构造无业务读取；归属与闭包检查完整；named Metric 可捕获合法 per 描述 |
| A10 | 固定 outer | 双端/多基期独有键、空端点、presence、行序正确；结构和冻结时间错误仍拒绝 |
| A11 | 固定 null | `0/0`、非零/0、NA、零/抵消权重和各合法数值类型正确；其它必要错误不被掩盖 |
| A12 | 生命周期与来源 | pending/memory/stored 及冷恢复续接一致；完成选择不重选；无隐式回源或上传 |
| A13 | Surface | 旧参数/入口拒绝，Help 可达性、类型、exports、CLI 等价与预算同步 |
| A14 | 范围隔离 | Attribute 原公开签名、端点独立分配及 parent_how 仍由现有测试覆盖，本次不重构 |
| A15 | 交错保序 | observe/per 的单规则 `(tag, customer, channel)`、多规则 `(a, c, b, d)`、普通 Grouping 定位及嵌套层分别保留旧轴序/schema/完整规则；值符合关联元组 oracle；简写与等价定位式规范化一致，无额外读取 |
| A16 | 外部覆盖修复 | A=40/B=10、P 仅含 A 的基线反例改为覆盖错误；完整 P 返回 40/10，增加无事实 C 返回 0；联合键须整体匹配；无事实、空样本及嵌套下保留的外部坐标仍属于 K，遗漏时报错；Null 桶、多事实根、空域及零轴域按 3.2.2 检查 |
| A17 | 内部子集与嵌套 | 同一 A/B 例中 per(P={A}).mean() 仍为 40，P={A,C} 为 20；无事实样本产生 0、空样本产生 NA 时仍保留外部行；嵌套各层按不同 P/路径/次序独立选择及补齐，不反向删除父层坐标；缺事实/未知样本和配对权重语义保持 |
| A18 | 覆盖执行边界 | 覆盖失败不能被内部空样本、Top-0、空输出域、外层 NA 或 outer 隐藏；显式事实过滤可改变 K，内部选择不得改变 K；本地合并与实际启用的来源优化路径一致，不因样本过滤下推漏检；失败不发布，固定历史 Frame 不回源重查 |

A15 必须比较规范化轴序和分配结构，不能只比较最终标量。A16/A17 复用同一有限 fixture，
明确区分语法等价与行为修复；C 是选中但无事实的样本，其 revenue 为 0。再补一组联合键
反例：P 的各轴投影都包含某必要桶的分量，但 P 不包含该完整桶，外部仍须报错。
A17 的嵌套子集不能只测两层相同 P；至少包含不相等的内外域并交换层序，以检出域泄漏。

A16/A17 还须共享以下边界用例：事实中的 customer 为 `{1, 2}`，`revenue` 是 sum Metric；
内部样本轴为 order id，`P_missing={999}` 无匹配事实，`P_empty={}` 为空，`Q={1, 2}` 是
另一层完整样本域。表中每个表达式均先按 customer 观察，不把内部样本轴当成外部输出轴。

| 表达式 | 外部完整域 `{1, 2}` 的预期 | 外部域只含 `{1}` 的预期 |
| --- | --- | --- |
| `revenue.per(P_missing).mean()` | 保留两行，各为 0 | 报缺失 customer `2` 的覆盖错误 |
| `revenue.per(P_empty).mean()` | 保留两行，各为 NA | 同上，内部空样本不能使检查通过 |
| `revenue.per(P_missing).mean().per(Q).mean()` | 保留两行，各为 0 | 同上，嵌套补齐不删除外部键 |
| `revenue.per(P_empty).mean().per(Q).mean()` | 保留两行，各为 NA | 同上，内层空样本不删除父层坐标 |
| `revenue.per(Q).mean().per(P_empty).mean()` | 保留两行，各为 NA | 同上，外层空样本不删除最终输出坐标 |

每例同时断言完整外部键、行存在性和值，不能只断言数值或最终行数。另加明确的事实过滤
`where=customer == 1` 对照：过滤在 K 的确定之前生效，外部 `{1}` 应通过并保留该行，
其值仍按对应样本/reducer 的空输入政策得到 0 或 NA。该对照区分事实过滤与内部样本选择。
范围内的覆盖检查沿现有检查执行链实现，不新增通用策略框架或重复的 provider 矩阵。

优先复用 `tests/conftest.py`、`tests/shared_fixtures.py` 和现有独立 oracle，集中扩展：

- `tests/analysis/graph/test_grouping_inputs.py`、公开组合与 typing 契约。
- `tests/semantic/test_deferred_authoring.py`、`test_semantic_expressions.py`。
- `tests/analysis/materialization/test_population_membership.py`，尤其是逐层 membership 回归。
- `test_frame_correspondence.py`、`test_frame_calendar_matching.py`、`test_frame_storage.py`、
  `test_source_population_fusion.py` 及实际受影响的数值检查。
- `tests/surface/` 的 Analysis Help、类型、exports、context budget，Semantic live Help/registry。

来源数值正确性使用独立 SQL/有限元组逐层计算；冷读只证明保存和重放，不单独证明数值正确。
不新增 Attribute 重构验收矩阵，也不建立重复的全 provider 笛卡尔测试框架。

## 9. 实施顺序与完成证据

| 阶段 | Owner / 交付 | 依赖 | 完成证据 | 当前状态 |
| --- | --- | --- | --- | --- |
| S0 | 本提案、范围与验收矩阵 | 无 | 文档路径、内容与 diff 检查 | 范围与台账已冻结 |
| S1 | 规范化与 observe/per 捕获；A01–A09 的语法/采样部分、A15/A17 | S0 | 交错迁移及逐层 oracle、typing、构造无读取证据；覆盖修复不在此阶段宣称完成 | 完成；证据见实施进度记录 |
| S1b | D7 外部域覆盖行为修复；A03/A05 的覆盖部分、A16/A18，与 A17 联验 | S1 | 保留基线反例，证明先确定 K、逐层选样本不删除外部坐标，以及覆盖检查不可绕过 | 完成；证据见实施进度记录 |
| S2 | Frame 固定 outer、通用固定 null 与 `/` 收敛；A10–A11 | S1 | 独立值 oracle、原 outer/null 对照、错误路径测试 | 完成；证据见实施进度记录 |
| S3 | 编译/物化、存储与 disclosure/docs；A12–A14 | S1、S1b、S2 | 冷恢复、相关 provider、Help/CLI/类型/文档检查；公开区分 D1 与 D7 | 完成；证据见实施进度记录 |
| S4 | 汇总验收与交付 | S3 | 不裁剪的 `make check-agent`、相关 Runtime 结果与遗留项记录 | 完成；证据见实施进度记录 |

每阶段运行最窄有效检查，改动的 Python 模块通过 typing。最终 broad gate 使用未裁剪的
`make check-agent`；Runtime 只覆盖实际受影响的来源路径，按运行事实分别报告。
实施中的 API 文档使用 `make docs-api` 验证，latest 英中内容使用现有站点检查入口验证。
本次不执行发布验收，不把 local、provider Runtime、installed-package 与 release 证据合并宣称。
