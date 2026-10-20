# Attribute：显式来源调查与可选贡献分解

Status: proposal，2026-10-20；attribute 的来源请求 API 待定稿，尚未实现。
本次核对基线 `6c2800bfa7`。依据 [AnalysisFrame 实施方案](analysis-frame-implementation-plan.md)
及其 [最终数据合同](query-result-reuse.md) 修订；基础切换的完成记录见
[实施进度](analysis-frame-implementation-progress.md)，不将历史计划的 planned 状态当作当前状态。
当前公开对象已是 AnalysisFrame，rollup/drill 接口已退役，本提案不再保留或重新引入它们。
当前 API 由 [Python Analysis Design](python-analysis-design.md) 和 [Lazy DSL](lazy-analysis-dsl-redesign.md) 拥有；
本文只定义后续显式来源调查与精确贡献的扩展合同。

## 1. 用户问题与范围

用户发现两期指标变化后，希望在选定父域内按地区、渠道、品类或人群重新观察，
查看完整联合分组的两期值和变化；需要解释总体变化时，显式要求精确贡献分解。
默认调查和精确贡献是两项不同要求，不能因为 P95 无精确贡献规则就拒绝其合法来源调查。

本提案保留完整 joint 语义、contribution 显式开关、来源共同输入要求、可加/比率/线性
分解规则、NA 政策及数值反例。不自动选择最佳维度、近似分摊、剪枝或作因果判断。

AnalysisFrame 只提供其自包含数据结构上的方法。
已保存比较值没有原 Metric、完整贡献或新属性映射，不能仅凭接收者类型重新读取来源。
因此 attribute 不再是 Frame 方法，也不通过扫描比较图、直接列 rename 或一般 lineage 恢复资格。
调查请求必须显式携带来源定义和范围；待执行请求可以持有新的来源图，完成后的结果不持有它。

compare/attribute 均定位为基础能力组合的高级 API：compare 组合数据对齐、差值/相对变化
和规范输出；attribute 组合显式来源观察、联合分类、两端对齐及可选精确贡献。
高级 API 负责简化请求、选择有定义的规则并保留必要验证，不建立新的计算或执行体系。
基础能力、公开表达缺口和各层职责统一见 [架构 §6.4](analysis-implementation-architecture.md#64-compareattribute基础能力组合的高级-api)。
本文不要求所有内部能力公开，也不把仅在限定数据上成立的手写算式称为完整 API 等价实现。
入口保持薄封装：默认路径是显式观察加分组比较；贡献路径增加组件准备、分组聚合、
聚合值映射回组及算术。两条路径均返回普通 AnalysisFrame，可继续 Analysis DSL 计算。
“不支持连续 attribute”只限制隐式来源调查，不限制结果上的数据运算。

## 2. 显式来源请求边界

方向确定为 Session 拥有的显式来源操作，但本轮不冻结函数名、公开参数类或完整签名。
不能把旧 selected.attribute(...) 机械替换为 session.attribute(selected, ...)，
再从 selected 的内部表达式提取原 Metric；这仍违反前置合同。
最终 API 必须完整表达以下事实，并与既有 observe/read 的绑定方式统一后才能实施：

| 必需输入 | 责任 |
| --- | --- |
| 两端 Metric 定义及量名 | 调用方显式提供已解析定义；检查单位、贡献根、配方、规约及样本政策相容性 |
| 两端观察范围 | 各自期间、条件、成员与属性时点；不能从旧 frame 的审计文字猜测 |
| 父域及双端对应 | 明确每个调查总体的两侧完整键、对应和 exact/outer 政策；若来自筛选结果，作为固定数据输入 |
| 新增 by 分类 | 复用 observe 的分类元组或单个 Population 输入形状，见第 3 节；不作为隐藏过滤器 |
| contribution | 明确 bool 请求，默认关闭；不能把数值列存在当作分解证明 |

同路径定义版本变化不单独拒绝；仍需检查所消费定义的实际类型、单位、根、粒度与政策。
父域数据与来源定义分别拥有事实：复用历史键不等于复用历史来源值。
已物化父域的筛选与 Top 不在调查时重跑；新的来源观察可以看到变化后的事实。

现有 session.population_from 覆盖普通字段/身份轴到来源的显式桥接，
足以完成“选异常地区，再按渠道 observe”，包括从普通轴 compare 结果选择完整父键。
桥接接收待执行选择时每次会重新求值；只有消费 compute/persist 返回的 Frame 才固定选择。
同一组选定普通键可以显式用于两端来源，但不自动建立各父域的贡献总体或共同输入证明。
配对/日历父域双端投影、各父域的共同输入重算及精确贡献仍是后续扩展，
不能声称普通桥接已经覆盖，也不扩展基础桥接的轴资格。
两端来源定义、父域映射和异常筛选必须各有唯一权威，避免重复参数互相矛盾。

### 2.1 显式贡献请求与规则判断

contribution 只接受 bool，不接受 0/1/None；没有 mode、topk、method 或自动回退。
这是未来请求合同，不表示本轮已经发布可调用函数。

| 请求 | 计算与返回 | 无精确分解规则时 |
| --- | --- | --- |
| contribution=False | 原指标、change、presence 和完整业务坐标；不收集仅为归因所需的组件 | 原量路线合法即可调查 |
| contribution=True | 额外返回精确 contribution、contribution_share 及所需解释组件 | 整个请求失败，不降级、不返回部分贡献 |

来源绑定、依赖、类型、单位、样本与物理执行资格始终需要有效。
编译器只从本次显式提供的 Metric 定义及分类推导规则，不从结果列名或旧生产历史推导：

1. 合法可加结构采用 additive。
2. 有明确 N/D 角色，或 mean/weighted_mean 的原生共同有效配对摘要，采用 ratio_component。
3. 已可分解子式的固定系数线性组合递归采用 linear。
4. 其它公式没有本版本内精确规则，请求贡献时失败。

不做任意代数改写或数值拟合。来源原量所需组件和检查不会因关闭贡献而取消。
必要分区重叠、未知贡献、组件/共同输入证明不足与精确路线未获资格都会使贡献请求失败。
静态问题在构造/准备拒绝，动态问题在求值拒绝；结果 where/top 不能删除必要检查。

错误继承 AnalysisError，携带指标、父键、实际表达式/轴、预期条件、实际事实及修复。
原因包括 unsupported_expression、overlapping_groups、missing_component_proof、
undefined_input、root_denominator_zero、unqualified_contribution_route。
关闭 contribution 是用户可以显式作出的新请求，不是自动重试；原绑定/来源/数值错误保留原合同。

### 2.2 父域、对应与实际输入

请求显式提供或绑定两端父坐标和对应，不继承一个 compare 节点中的隐藏定义。
数据侧日历匹配遵守 Frame 的自包含规则；新的来源范围、关系和分类仍必须显式提供。
普通轴对应须完整显式绑定；零轴或双方唯一日历轴才使用 Frame 已允许的推导。
基础桥接不接收interval/日历/配对轴，也不允许配对轴再次 compare；本提案不绕过这些限制。
固定父域的每个完整键是独立总体，启用贡献时其本次重算变化拥有该父域的占比分母。
选择时的 presence 不是本次来源的缺侧掩码；实际重算按各端本次输入及原输出域政策决定。
原请求明确排除的范围仍不消费，不能与历史读取暂未出现的组混淆。

父域 exact 不意味着细组 exact。新增字段的完整细组按两端联合键并集处理，保留新增/消失组。
分类使用各端显式角色及属性时点，角色不相容就拒绝，不能根据标签拼接。
新增日历细化仍不在 attribute 首版范围；不丢弃必需双端投影来假装已经得到单一当期轴。

本次贡献解释本次新读取的两期指标，不承诺复现过去异常值，不要求跨读相等检查或自动重试。
两个端点可以独立读取；每个端点内部的总体和全部联合组必须使用同一实际贡献输入。

### 2.3 当前可复用的基础流程

以下使用现有 Frame API 表达“发现异常 → 固定普通父键 → 显式新来源调查”。
假定同一 Session 中已取得 revenue Metric、region/channel Field 和两个 TimeScope，
轴显示名分别为 region/channel。来源结果带 interval 轴；比较后产生配对时间轴，桥接只选普通 region 轴。
这不是 attribute 的调用签名：

```python
import marivo.analysis as mv

current_regions = session.observe({'revenue': revenue}, by=(current_period, region))
previous_regions = session.observe({'revenue': revenue}, by=(previous_period, region))
regional_change = current_regions.compare(
    previous_regions,
    on=tuple(zip(current_regions.axes, previous_regions.axes)),
    match=mv.ordinal(),
    how="outer",
).compute()
selected = regional_change.where(regional_change["absolute.revenue"] < 0).compute()
parents = session.population_from(selected, on=((selected.axis("region"), region),))
current_groups = session.observe({'revenue': revenue}, by=(current_period, region, channel), where=mv.in_population(parents))
previous_groups = session.observe({'revenue': revenue}, by=(previous_period, region, channel), where=mv.in_population(parents))
aligned = current_groups.align(
    previous_groups,
    on=tuple(zip(current_groups.axes, previous_groups.axes)),
    match=mv.ordinal(),
    how="outer",
)
comparison = aligned.select({
    "current": aligned["left.revenue"],
    "baseline": aligned["right.revenue"],
    "change": aligned["left.revenue"] - aligned["right.revenue"],
})
saved = comparison.persist()
saved.show(n=20)
artifact = saved.artifact
```

筛选使用固定比较数据，只选择差值已定义且为负的地区；缺侧/NA 不被解释为下降。
population_from 只传递选中地区的完整键，不传递历史比较值、presence 掩码或 Metric 配方。
两端显式复用该域并重新读取；空选择仍是空筛选，Null 字段键仍是合法类别。
若需要按 Top 地区选择，可在固定父域前使用普通 top；后续来源读取不重跑已固定的排名。
每次 compute/persist 或派生后从返回对象重取 Column/FrameAxis，不能复用旧接收者的句柄。

细组阶段使用 observe + align + select，展示两期值和变化，并保留独立 presence；不计算
相对变化。这里用普通单段量名，未来 attribute 按第 7 节生成规范路径。该流程不构成精确贡献分解。
align + select 保留对齐行序；attribute 还需执行第 7 节的规范排序输出整形，不能据此假定两者行序相同。
不能把上一步 regional_change 的历史差值当作新组贡献的分母，也不能单独再 observe
总体后假定与组数据共享实际输入。贡献请求仍需第 6、8 节的共同输入路线。
需要配对/日历父域的调查须等相应显式来源交接定稿，不能剥除时间轴后声称保留了原范围。

只改变来源观察粒度时，直接重新声明 by，不经过结果接收者接口：

```python
east_total = session.observe({'revenue': revenue}, by=(current_period,), where=region == 'East')
east_channels = session.observe({'revenue': revenue}, by=(current_period, channel), where=region == 'East')
```

两者分别是 East 整体与按渠道的原指标观察；不会从已有组值推算原整体，
也不承诺独立求值间的共同快照。归因时仍由单个显式来源请求负责总体和细组的一致输入。

### 2.4 compare 的基础组合示例

以下复用第 2.3 节的两个地区来源 Frame。假定 revenue 为普通数值且合法基准非负，
示例表达 compare 的数值核心；不覆盖任意数值类型、负基准或完整公开输出合同：

```python
aligned_regions = current_regions.align(
    previous_regions,
    on=tuple(zip(current_regions.axes, previous_regions.axes)),
    match=mv.ordinal(),
    how="outer",
)
left = aligned_regions["left.revenue"]
right = aligned_regions["right.revenue"]
comparison_values = aligned_regions.select({
    "current": left,
    "baseline": right,
    "change": left - right,
    "relative": (left - right).divide(right, zero_denominator="null"),
}).compute()
```

当期 160、基期 100 得到 change=60、relative=0.6；零基准返回 null，缺侧或值 NA
继续传播 NA。align 负责完整键、日历匹配和 presence；select 保留其坐标和 presence。
完整 compare 还检查非空且相同的量名集合、类型及单位，生成 left.q/right.q/absolute.q/relative.q，
并使用 abs(right)：当期 160、基期 −100 的 relative 是 2.6，不是 −2.6。
Column 当前没有公开 abs；用户 select 不能声明这些点分路径，因此此示例不冒充完整替代。
Decimal、Duration、负零、缺侧优先于零分母等行为仍由已有数值合同负责。
可继续保留 compare 作为短入口，不以“可组合”为由取消其验证或复制一个配对内核。

## 3. 字段、Population 与分区

`by` 复用 observe 的输入形状：分类绑定的有序元组，或单个完整 Population。
元组元素类型沿用 observe 的 `Deferred | Grouping`，
不包含 Population，也不接收结果 FrameAxis。attribute 仍要求至少一个新增业务轴：
空元组、零轴 Population、重复轴和与父坐标重复的轴拒绝；第 2.2 节的时间限制不变。

| 输入形状 | 含义 |
| --- | --- |
| `by=(region, channel)` | 合法分类绑定形成联合分类，普通字段只产生两端实际出现的联合键；成员分类等沿用 observe 的域政策 |
| `by=population` | 直接采用一个 Population 的有序轴和完整联合元组，声明完整候选输出域 |

Population 不是一个名为“人群”的值列。同一个多轴 Population 保留真实联合元组，
不把每个字段拆开再补笛卡尔积；其轴直接加入本次联合坐标，不代表一个树层级。
`by=(population,)`、`by=(population, field)`、`by=(population_a, population_b)`
均拒绝，不自动展开、合并或推断域。调用方先用已有 Population 操作构造一个完整域，再传给 by。
同名字段、相似值或现存键本身不能证明来源可以映射到这些轴，仍须满足原来源绑定资格。

Population 候选域选择指定完整输出键，不要求覆盖域外来源坐标。
来源事实过滤使用 where=mv.in_population(P)；指定输出域与贡献过滤各自明确。
传入完整 Population 时，合法的无事实候选按 observe 的输出域、empty/NA 和 presence 政策处理，
不能因没有事实就退回实际键域，也不能把无事实视为未知输入的零贡献。
空 Population 是显式空输出域；其生产依赖或必要类型/来源错误仍正常传播。
Attribute 的父域共同输入、精确贡献完整不交分区和守恒证明仍是本提案自己的要求，不能从 observe 的域选择推断。

例如调用方已取得 region/channel Field，并明确要求两地区与两渠道的全部交叉组合，
可使用现有 API 构造域；下面仍是基础来源示例，不是尚待定稿的 attribute 调用签名：

```python
regions = mv.coordinates(by=(region,), keys=(("East",), ("West",)))
channels = mv.coordinates(by=(channel,), keys=(("Online",), ("Store",)))
joint_domain = mv.product(regions, channels)
current_complete = session.observe({'revenue': revenue}, by=mv.product(current_period, joint_domain))
```

product 明确产生四个候选组合。即使来源只有 East/Online，其余组合也按声明完整域的
政策处理；这与 `by=(region, channel)` 只生成实际分类组合不同。此例只在指定期间与候选联合域中求值；需要独立限制事实贡献时另行声明来源过滤。
若业务域只有 East/Online 与 West/Store 两个相关组合，应直接传入保留相关元组的域：

```python
correlated_domain = mv.coordinates(
    by=(region, channel), keys=(("East", "Online"), ("West", "Store")),
)
```

多个完整域只有在业务明确要求跨域笛卡尔积、轴互不重复且所有权相容时使用 mv.product。
每个因子内部的相关元组保持不变；不按单列拆分求积。需要相关联合域时，由调用方通过
已有 Population 构造/投影等操作提供该域，attribute 不增加混合域合并规则。

若要比较“高价值客户”“活跃客户”等命名人群，需要先用现有语义分类/分桶能力
提供组归属；不能把一组任意 Population 当成自动互斥的命名分区。
精确贡献分解才要求同级分组在相关贡献根上形成完整不交分区；合法的重叠分组
在 contribution=False 时仍可下钻；启用贡献后必须证明不交，否则报错。
成员身份互斥不一定保证经多值关系展开后的事实贡献互斥。重叠 membership 继续遵守
[成员分桶](lazy-analysis-dsl-redesign.md#434-显式成员关系分桶)合同；
该入口不会自动均分、按调用顺序分配或归一化修复重叠。
有业务权重时，应先把显式分配记录建模为贡献根，并证明归属权重保持总体。

源 Null 分类是合法的独立键；不能因为名称未知而丢掉。分类路径缺失或非唯一是
绑定错误，不通过关闭 contribution 或结果筛选绕过。

## 4. 固定 joint 语义与输入范围

by 的全部业务轴在一次调用中共同形成联合分区。例如 by=(region, channel, category)
返回每个父域下全部合法的地区/渠道/品类组合；分类元组或 Population 的轴顺序决定坐标布局和规范排序，
不构成逐层展开顺序，也不改变同一联合分区的数学含义。
普通字段只生成两端实际出现的联合键，显式 Population 完整域遵守第 3 节。
不返回中间小计、树节点或合成分组。空候选不虚构全 Null 行，父域摘要仍可保存。

例如地区 A 下两个渠道变化 +100/−100，地区 B 下一个渠道 +10：by=(region, channel)
返回三个组合，A 的净变化为零不会隐藏其内部抵消。
只研究 East 时，在来源端点通过原有条件/Population 谓词 限定范围，或在已物化比较结果上选出父域，
再通过显式双端绑定作为新来源请求的范围。未保留在 compare 上的
字段不能通过 coord 凭空读取，应先在来源端点表达所需条件或分类。
by 的普通元组声明新增分类，Population 则选择指定完整输出键；贡献证明仍须满足自身完整分区与共同输入合同。

每个请求中显式绑定的父键是独立分析总体。按地区父域再调查渠道时，贡献解释
各地区自身变化；从全局父域请求 by=(region, channel) 则解释全局变化。
更改分析总体须提交新的显式来源请求，不从结果的生产图找回来源。
不同总体或独立调用的贡献不承诺相加守恒，不同次执行也不承诺数值复现。

贡献请求成功时，单个父域下完整联合组的贡献和等于该父域本次重算变化。
不同父域的占比分母独立。关闭贡献时不承诺组 change 可加，尤其不能用均值、
比率或 max 的组值之和代替总体重算。

## 5. 结果筛选与计算成本

重点展示使用现有 where/top，在普通业务坐标上按结果数量筛选和排序。
它们不改变已请求的联合分区、不重新计算贡献或占比分母；筛选后的占比之和
可以不为 1。attribute 不提供 direction 或其它排序策略。

结果上的 top 和 show 限行不保证减少原联合下钻的扫描、聚合或分组计算。
联合组合数可能随维度基数乘积增长；控制计算量应在 attribute 之前限定原始
端点范围或显式父域，或减少本次 by 维度。不能依赖后置结果筛选剪枝来源。
必要的读取、版本归属与数值检查仍须完成。

沿用现有 Runtime 资源控制；预算耗尽应失败，不静默截断组合或切换近似算法，
不引入独立预算系统。

## 6. 精确贡献的数学合同与可用性

本节是 contribution=True 的成功条件；contribution=False 不要求这些分解证明。
所有等式先在精确算术及有定义输入上成立。运行时按原数值类型、舍入、溢出和
精度合同求值，不宣称浮点恒等式逐位成立。定义约束不能靠最终和恰好相等来替代。

### 6.1 统一定义

对每个根父域，令两端根指标为 M_0、M_1，变化为 ΔM=M_1−M_0。
分解方法须为每期的分组集合 S 定义贡献质量 Q_t(S)，满足：

\[
Q_t(root)=M_t,\qquad
Q_t(A\mathbin{\dot\cup}B)=Q_t(A)+Q_t(B).
\]

于是联合分组的贡献和占比分别为：

\[
C_S=Q_1(S)-Q_0(S),\qquad
P_S=C_S/\Delta M.
\]

每个父域的完整联合分区满足 `sum(C_S)=ΔM`；对应 contribution_share 在
ΔM 非零时合计为 1。本次父域就是归一化总体，不再区分根占比与父占比。
新增贡献占比的零分母固定返回 NA（null），保留贡献值，不提供 error 开关。
这只处理本次父域 ΔM 为零；原 Metric 的分母和 compare 自己的除法政策仍归各自所有。
负数、超过 100% 和接近零总变化产生的大比例都不裁剪。

定义 `change_S=M_1(S)-M_0(S)` 为该组自身指标的变化。
它与 `contribution_S` 是不同字段：仅在相应直接可加方法下保证相等。
本分解解释数值构成，不把所有满足守恒的数学分配都视为同一种业务解释。

### 6.2 additive：可加指标

对两期完整不交贡献分区，指标沿全部下钻轴具有合法可加性时，取
`Q_t(S)=M_t(S)`，故 `C_S=M_1(S)-M_0(S)`。

适用 sum、合格 count，以及同一分区上的固定常数线性组合，例如收入减成本。
多根指标每个组件独立保证覆盖、归属和政策，再按完整键组合；不能先做放大事实的 join。
字面量缩放保留合法许可；按组增加非零常数、变量乘除、代数相消不自动获得许可。

可加性针对轴、期间和指标定义：时点库存可沿商品求和，不因此可沿日期求和；
窗口指标只有在保留原窗口端点、空间贡献不交时才能使用空间分解。
身份计数只有在被计身份确实不跨组重复时才可直接相加。

### 6.3 ratio_component：比率的根分母组件分解

要求指标具有已声明且可解释的 `N/D` 组件，根分母 D_0、D_1 非零，组件在
分类上的覆盖和充分信息完整。默认接纳已证明可加的分子/分母，以及 mean、
weighted_mean 的原生合法配对摘要；不能凭列名或最终数值推测组件角色。

令 `N_t(root)=sum_i N_t(i)`，定义：

\[
Q_t(S)=\frac{N_t(S)}{D_t(root)},\qquad
C_S=\frac{N_1(S)}{D_1(root)}-\frac{N_0(S)}{D_0(root)}.
\]

由分子可加性可直接推出 `sum_i C_i=R_1(root)-R_0(root)`。
分子分母可来自不同事实根，但每个根都必须沿合法路径覆盖相同分类语义。
weighted_mean 的乘积和、权重和与支持必须来自原算子的共同有效配对；
不能把独立求得的两个 sum 冒充配对摘要。

该方法解释“每组对整体比率的贡献份额如何变化”，同时包含组规模、结构和组内
表现变化。它不等于按组比率差求和，也不单独分离分子效应与分母效应。
其它分解方法可能同样精确但含义不同；本提案只按第 2.1 节派发这一个规范比率规则，
不在多个解释模型间启发式选择或混用。

此处只承诺规定的维度贡献分解；常数偏移、变量乘除及一般非线性复合式不自动
获得同一规则。第 6.4 节允许明确的线性组合，不允许任意公式改写。
不能仅由贡献和等于总变化证明分配唯一或解释成立；规则和根分母定义必须保存。

**同次调用中每个父域的全部联合组使用该父域的两期总体分母。**
在来源请求中限定地区或采用地区父域，会改变归一化总体；
须从该范围的原组件重算分母，不能沿用另一个全局分析的分母。具体差异见第 10 节。

组自身值仍为 `N_t(S)/D_t(S)`，其 change 可以与 contribution 完全不同。
组分母为零可能令自身值为 NA，而在根分母非零、组件已知时贡献仍可定义；
原 Metric 的 error 政策仍执行。不得为了完成贡献分解绕过已要求的原量错误检查。

### 6.4 已可分解子式的线性组合

若 A、B 在同一分析根和下钻分区上都有精确贡献质量 Q_A、Q_B，固定系数 a、b
形成的 M=aA+bB 可采用 `linear` 规则：

\[
Q_M(S)=aQ_A(S)+bQ_B(S),\qquad C_M(S)=aC_A(S)+bC_B(S).
\]

合法加减、取负、常数缩放递归保留分解，单位必须相容，子式错误和有效样本政策
继续执行。该规则也可组合两个已支持的比率，并不声称它们的完成值沿分组可加。
启用贡献后，任一必要子式缺少证明都使整个请求失败，不能只返回已支持项的部分贡献。
变量乘除、非零常数偏移、非线性函数和条件分支没有本版本内的一般规则，
关闭贡献时仍可按原公式下钻。行级表达式随后做合法 sum 与对完成 Metric 做非线性运算分开判断。

### 6.5 支持表

以下均以原 Metric 在目标轴上具有合法原量执行路线为前提。关闭贡献时按原公式
下钻；下表列出启用贡献后的额外条件，不满足时明确报错。

| Metric 类别 | contribution=True 的精确规则或限制 |
| --- | --- |
| 金额 sum、订单 count、固定可加线性组合 | additive；验证相关轴上的可加性和完整不交分区 |
| 时点库存、窗口 sum | 仅合格空间轴及原时间绑定下使用 additive |
| 客单价、转化率、利润率 | 组件、覆盖和分区均可证明时使用 ratio_component |
| mean、weighted_mean | 有效计数/共同配对组件完整时使用 ratio_component |
| 已可分解子式的加减、常数缩放 | linear；每个必要子式均须可精确分解 |
| 精确 distinct count | 被计身份在组间互斥时使用 additive；跨组重复则失败 |
| P95、min/max、一般非线性表达式 | 无本版本内的精确规则，报 unsupported_expression |
| approximate count/quantile | 原指标近似性不构成精确贡献资格，请求失败 |
| per 后的复杂规约 | 必须证明原内部样本/规约次序下的精确分解，不能只按外层 reducer 名称放行 |

可合并状态不等于可分解贡献。distinct 的集合、P95 的完整分布可以重算总量，
但不因此提供本版本认可的变化贡献规则。关闭贡献时，声明为近似的原指标仍按
自身定义求值并披露近似性；声明为精确的 P95 不得被替换成近似 P95。
不自动进行近似归因、分摊或抽样。

### 6.6 NA、单侧组与完整性

父坐标使用请求显式指定的对应、how 和固定选择域，新增细坐标按两期完整键并集处理，
均保留两端 presence。缺侧不自动补零；只有完整读取并证明该分类下没有贡献，
才可在贡献质量的加法摘要中使用空集单位元。对外原 Metric 值保持其 empty/NA 政策。
例如根变化已定义时，空子组 sum 可以展示 NA，其已证明为空的内部贡献摘要仍为零；
这不把未知输入解释为零，也不将未定义根变化改成已定义变化。

关闭贡献时，原量合法 NA 保留该行，change 按普通 NA 运算返回 NA，不因此删除分组。
启用贡献时，组自身值为 NA 不必然失败：例如组比率分母为零，但根分母非零且组件
已知时，根归一化贡献仍可定义。原 Metric 的 error 政策始终执行。

启用贡献后，任一根变化未定义、必要贡献未知、相关分区重叠、组件证明不足或
比率根分母为零，都使整个请求失败。不能删除未知候选或返回部分 null 贡献
以宣称成功；本次请求的多个父域遵守相同的原子成功边界。

| 情况 | contribution=False | contribution=True |
| --- | --- | --- |
| 完整精确分解可用 | 返回原量/change，不输出贡献列 | 返回精确贡献及占比 |
| 本次总体变化为零，贡献仍有定义 | 正常下钻 | 贡献保留，对应占比 null，注明 zero_total_change |
| 无精确规则、相关分区重叠、必要证明或精确路线不足 | 原量路线合法即可下钻 | 结构化报错，不自动降级 |
| 原指标合法 NA，但精确贡献仍有定义 | 保留原值及 NA change | 保留原值/NA change，并返回有效贡献 |
| 根变化或必要贡献未定义、比率根分母为零 | 原指标政策为 null 时正常保留 NA | 贡献请求失败；原指标政策为 error 时保持原错误 |
| 原绑定、来源执行或原数值计算失败 | 原错误合同 | 原错误合同，不伪装为分解不支持 |

占比分母为零只影响 contribution_share，不影响 contribution，也不触发错误。
这与原指标的比率分母为零不同：后者可能使指标或贡献本身未定义。

### 6.7 贡献分解的基础组合示例

以下是基础计算的数学组合，不是新增公开方法。执行计划中的处理顺序为：

```text
explicit endpoint source bindings
    -> shared actual contribution inputs and required partition checks
    -> complete joint-group values and proven components
    -> per-parent totals from those same inputs or sufficient additive components
    -> explicit complete-parent-key mapping to each group
    -> contribution and contribution_share arithmetic
    -> canonical output shaping -> AnalysisFrame and non-executable method facts
```

来源准备根据 Metric 定义取得组件并完成资格检查：sum/count 使用合格可加量，
mean 使用有效值和/有效计数，weighted_mean 使用同一有效配对的乘积和/权重和，
ratio 使用已确定角色的 N/D，线性式递归消费子式组件。只有完成均值等数值时无法反推这些信息。
组件角色和样本证明归现有语义绑定及编译层；后续差值、规约与归一化复用基础数值能力。

本文“父域”仅表示已有完整分组键所确定的分析总体，不引入父节点对象、专属坐标或新的绑定协议。
按这些键计算总量再映射回细组，就是分组聚合值映射；单一总体是零个父分组键的特例。
其数值作用类似分组 transform/窗口聚合，但不改变 Metric.window 的来源时间窗口含义，
也不在本提案中冻结新的公开方法签名。映射保持完整键和原行数，不执行任意关系 join。

只有证明分组完整、不交且组件可加，才可从分组组件规约父域；不能把所有 Metric 的组值
直接 sum，也不能使用忽略未知 NA 的普通 reduce.sum 冒充完整证明。多根分别检查输入与
分类，不通过事实 join 放大贡献。父域没有细组时仍按源 empty/NA 政策处理，不能丢掉父域检查。

可加例子：线上/线下基期 100/100、当期 160/80，组贡献 60/−20；总体变化 40，
占比 1.5/−0.5。相同固定输入上的分组差值、父域规约和除法已经给出全部数值步骤。
当前公开 Frame.reduce 可以求父域统计，但不能把所得父域列直接作为细组 Column 使用；
它们属于不同精确输入，全轴 align 也不提供所需的父域广播。

比率例子使用每端已验证的完整组件。下面的 Fraction 代码可独立执行，仅作为算式见证，
不替代来源证明、运行数值类型或 Marivo 的计划执行：

```python
from fractions import Fraction

# group, baseline numerator/denominator, current numerator/denominator
rows = [
    ("A", 1, 10, 2, 10),
    ("B", 9, 90, 8, 190),
]
d0 = sum(row[2] for row in rows)
d1 = sum(row[4] for row in rows)
delta = (
    Fraction(sum(row[3] for row in rows), d1)
    - Fraction(sum(row[1] for row in rows), d0)
)
result = []
for group, n0, _, n1, _ in rows:
    contribution = Fraction(n1, d1) - Fraction(n0, d0)
    share = contribution / delta if delta != 0 else None
    result.append((group, contribution, share))

assert result == [("A", Fraction(0), Fraction(0)), ("B", Fraction(-1, 20), Fraction(1))]
assert sum(row[1] for row in result) == delta
```

此例 d0=100、d1=200，根比率从 0.10 到 0.05；组贡献为 0/−0.05。
mean 换成有效值和/有效计数；weighted_mean 换成共同有效配对的乘积和/权重和，
不能重新独立扫描两个 sum 后假定样本一致。线性组合在各子式完成相同父域映射后
复用加减/缩放；无规则的复杂子式按第 6.5 节报错。

因此贡献开关增加的是组件需求、证明义务、父域归一化和解释输出；不需要新的 runner。
基础能力可在内部共用，公开组件或父域映射方法是否有独立用户价值另行判断，
不为了让示例能够全部手写而暴露临时聚合状态或放宽固定 Frame 的来源边界。

## 7. 输出、顺序与继续分析

未来 attribute 请求返回普通 AnalysisFrame，轴为“显式父坐标 + by 的有序业务轴”，
每行一个完整联合组合，无根行、节点轴、depth、父子树结构或合成 Others。
Frame 生命周期、类型化键、名称段、presence_names、show 和物化合同由前置设计拥有。
这里的 left/right 分别代表当前/基准端点；沿用前置设计的名称段路径和 presence 规则，
不增加结果子类型或第二套端点命名。

关闭贡献时，对声明量 q 输出 left.q、right.q、absolute.q 及独立 left_present/right_present；
absolute.q 是本文数学段落的 change，不自动附带 relative.q。
开启贡献成功时再输出 contribution.q、contribution_share.q；原值、差值和贡献具有指标单位，
占比无量纲。ratio_component 还显式输出 numerator_left.q/numerator_right.q、
denominator_left.q/denominator_right.q 及各自单位，不以隐藏内部组件提供续算资格。
这些是未来方法生成的路径，不能作为用户自定义量名绕过单段命名合同。

开启贡献时，按完整父键定位的方法摘要保存本次总体两期值、change、归一化根分母、分解规则及参数。
它是不可执行的方法事实，不是额外数据行；无联合候选仍保留必要父域摘要与原 empty/NA 语义。
linear 子式及 mean/weighted_mean 的配对支持作为可解释事实保存，不保存可执行原配方。
该方法摘要的 codec 和传播必须在扩展实施时单独闭合，不能假设基础 Frame 已经实现。

规范排序属于 attribute 的输出整形：计算及必要检查完成后、形成完整结果前，按完整父键
与联合组合键的既有规范次序统一排列所有行值和 presence。它不改变分组、贡献或分母。
高级 API 声明该输出要求，编译层复用已有完整键排序能力，在统一执行计划内完成；
不新增公开 sort API、独立执行器或 display_order 附件，物化和读取只保留已产生的行序。
align 的 outer 顺序仍是左行优先再右侧独有行，select 保持输入顺序；规范排序不是这两个
基础操作的隐含效果。例如左端键 B/A、右端键 C/A，裸组合得到 B/A/C，attribute 输出 A/B/C。
where/top 只选择已计算行，不重新分配贡献或改变占比分母；筛选后的占比和可以不为 1。
select/reduce 遵守普通 Frame 规则，不因仍保留 change 或 contribution 列获得来源重计算方法。
不会提供连续 frame.attribute；新问题必须显式构造新的来源请求。

### 7.1 作为普通 AnalysisFrame 继续计算

attribute 的值列、坐标和 presence 直接进入现有 DSL，无 AttributionFrame 子类型或
专属后续计算协议。贡献列是指标单位的普通数值列，占比列无量纲；select/算术、
where/top、reduce、align/compare 均按各自普通合同消费它们。
align/compare 仍要求相容的完整轴及键类型，并保留配对轴不能再次对齐的限制；
compare 另检查量名集合、数值类型和单位，align 允许双方保留各自的量名与单位。
后续操作产生普通派生 Frame，不因来自 attribute 而增加或减少数据运算权限。

下面假定 result 是已启用贡献、单一分析总体、量名为 revenue 的 attribute 返回值。
它可以是待执行、memory 或 stored Frame；例子只使用现有数据操作，不声明归因入口签名：

```python
result = result.compute()
positive = result.where(result["contribution.revenue"] > 0)
ranked = positive.top(10, by=positive["contribution.revenue"])

summary = result.reduce({
    "total_contribution": result["contribution.revenue"].sum(),
})
summary_values = summary.compute()

saved = ranked.persist()
reopened = session.persisted_frame(saved.artifact.id)
continued = reopened.select({
    "contribution": reopened["contribution.revenue"],
    "share": reopened["contribution_share.revenue"],
}).compute()
```

示例先固定 result，使多个计算分支消费同一批数据；若直接复用待执行来源表达式，
各分支独立求值仍可读取新数据，不能把表达式复用当作结果固定。
summary 规约完整 result，因此 +60/−20 的例子得到 40；positive/ranked 只保留 +60，
其占比仍为 150%。如果改为规约筛选后的行，得到的是已选贡献小计，不是原总体变化。
存在多个父域时，应保留各父分组键分别规约，例如 `by=(result.axis("region"),)`；
跨父域的占比和不能解释成一个总体占比。DSL 不为普通求和附加特殊禁令，也不认证这种业务解释。
reduce 仍删除行级 presence；null 占比及空/all-NA 统计保持普通算子政策。

算术、重命名或规约后的列不会自动取得新的精确归因证明。原方法摘要只解释原结果及其总体；
后续筛选不改写原分母，派生统计不把旧摘要冒充自己的分解结论。解释事实的保存与传播
沿第 7 节所述扩展 codec 定稿，不成为执行依赖或来源配方。

### 7.2 求值、保存与冷恢复

show(n=20) 在待执行时只展示已捕获信息，物化后预览已有结果；n=0 只展示元数据。
终态 Artifact 仅通过 frame.artifact 访问提交和审计，数据读取归 Frame；其 show() 不预览数据。
compute 不创建持久 Run，persist 发布并返回持久 AnalysisFrame，frame.artifact 才是终态视图。
已保存结果和审计事实均不恢复来源定义。冷恢复、命名冲突与原始数值错误遵守 Frame 合同。

## 8. 执行一致性与实施边界

复用现有绑定、编译、来源能力判定、数值内核和 Frame 的求值/持久发布路径，不创建归因执行器。
高级入口只构造显式依赖与检查需求，交给统一 compiler/scheduler；不能在方法内部逐个
compute 中间结果、再导出 pandas 重建执行流程。第 6.7 节独立算式只用于解释和验收。
编译可以融合共享步骤，但不以两次独立 observe 使用相同参数作为共同实际输入证明。
来源计划由显式请求构造，不从比较结果追溯。原量内部组件可在本次调用中存在，
调用结束释放；显式输出的解释值是普通数据，不能与隐藏续算组件混同。

contribution=False 不收集仅用于分解的证明；原 Metric 的覆盖、版本、样本与错误检查仍执行。
contribution=True 要求每端总体与全部联合组来自同一实际贡献输入，
不能用独立读取的总体分母配合组数据，再通过跨读对账/重试伪造同输入资格。
无法提供该路线时报告 unqualified_contribution_route，不自动降级。

先完成显式父域选择及必要检查，再固定完整键并在各端新范围内读取。
本次多个父域或组中任一必要条件失败时，不返回部分成功结果；compute 不返回部分内存根，
persist 不发布部分产物。后置 where/top/show 限行不能掩盖未展示分组中的必要错误。

## 9. 接口退役与后续实施边界

### 9.1 移除 rollup/drill，不提供兼容入口

| 分析需要 | 本方案的表达方式 |
| --- | --- |
| 原指标在更粗粒度或整体上重算 | 显式 observe 原 Metric、期间和筛选，以所需 by 重新观察 |
| 查看新维度或更细分组 | 显式 observe 新分类；已有选中父键通过 population_from 和来源 where 传入 |
| 对已完成行求统计 | 普通 Frame.reduce，只统计当前值，不恢复原 Metric |
| 两期联合分组及可选精确贡献 | 本文的显式来源扩展；不能从 compare 结果反推原定义 |

rollup/drill 不作为 AnalysisFrame 方法、Session 别名或 attribute 的前置步骤保留。
不保留按 compare 图追溯原量、select/rename 传播来源资格或物化前专用接收者。
连续来源下钻协议一并移除：新问题重新提供来源，结果只可作为显式选择域。
原整体 mean/distinct 等仍按原公式计算；不能将 reduce 当作上述接口的等价替换。

share 遵从 Frame 切换合同，已退役，不恢复此前“暂时保留”的决定。
普通数值除法只表达调用方指定的值比例；attribute 的 contribution_share
表示对本次总体变化的贡献比例，两者不冒充旧 share 的原贡献份额证明。

### 9.2 attribute 独立扩展

1. 复用已切换的 [AnalysisFrame 数据对象、来源边界及生命周期](query-result-reuse.md)，不重建 Query。
2. 定稿显式来源请求：两端 Metric/范围、父域双端绑定、by 分类以及共同实际输入。
3. 按架构 §6.4 复用基础能力、补齐贡献证明和父域映射缺口；不要求全部公开为新方法。
4. 确定完整结果 schema/方法摘要 codec、输出传播与冷恢复，之后才能发布归因入口。

第 6 节数学合同不依赖旧入口，继续作为后续实现的依据。
不得仅因本文有公式和示例，就声称基础 Frame 首版已包含双端来源调查或精确贡献。
配对/日历父域交接、显式来源请求签名和方法摘要传播仍是公开接口定稿前的剩余工作。
这些内容不通过 origin、历史自动匹配、Artifact→Frame 导入或隐式回源补齐。

## 10. 数学例子与可验证成功标准

以下是独立算式见证，不是后端或新 API 已通过验收的证明。贡献场景都需要显式请求，
不同总体/不同调用分别验证，不能把不同范围的贡献混合对账。

| 场景 | 输入与预期 |
| --- | --- |
| 默认调查 | contribution=False 不执行分解资格判断，返回两期值/change/presence；无贡献与解释组件 |
| 参数边界 | contribution=0/1/None 拒绝；无 mode/topk/自动方法回退 |
| compare 组合 | 非负普通数值场景下，align + select 的值/presence/键序与 compare 对应列相同；别名差异显式处理，不宣称规范路径相同 |
| compare 完整合同 | 负基准 160/−100 的 relative=2.6；缺侧/NA 优先于零分母；保持 Decimal/Duration 政策，不用简化示例替代这些检查 |
| 默认调查组合 | 同一固定细组输入上的 align + select 与贡献关闭结果按完整键对应的原值/差值/presence 相同；先处理规范排序差异，不读取归因专用组件 |
| 规范排序 | 左端 B/A、右端 C/A，裸 outer align + select 保持 B/A/C，attribute 输出 A/B/C；所有值/presence 随行一致重排，compute/persist/冷读保序 |
| 父域归一化 | 只按完整父键映射总量，复合键不拆分，缺父/重复父拒绝；不增加细组或改变其键序，不允许跨输入 Column 混用 |
| 共享执行 | 同一输入及检查义务进入统一调度；来源读取计数和计划证明确保总体/细组共享实际输入，而非两次 observe 后对账；无嵌套 compute/persist 或独立 runner |
| 完整联合结果 | A 下 +100/−100，B 下 +10；返回三个联合组，无地区小计或根行 |
| by 输入形状 | 接受非空分类元组或有新增轴的单个 Population；含 Population 的元组一律拒绝，不自动展开；空元组、零轴/重复/父域重复轴拒绝 |
| 完整域组合 | mv.product 的两个两元素单轴因子形成四个候选；传入 by=该 Population 后只保留指定完整键及合法空组合，不因域外来源坐标报覆盖错误；精确贡献请求另行证明完整分区 |
| 相关联合域 | 单个 Population 仅含 East/Online、West/Store 时不增加交叉组合；多轴因子参加显式 product 时保持因子内部相关性 |
| 域与顺序 | 调整分类轴顺序或显式 product 的因子顺序只改变布局及规范排序；同一联合分区按完整键对应的贡献不变 |
| 空完整域 | 空 Population 是显式空输出域，仍传播生产依赖与必要绑定错误；精确贡献继续独立检查共同输入及完整分区，不退化成无筛选或实际键域 |
| 输入端筛选 | 固定 East 父域后显式构造两端新来源请求；只调查 East，以其本次变化为总体，不重跑原选择 |
| 普通比较父域桥接 | 用 absolute.q 筛选普通地区轴 compare，compute 后以当前 FrameAxis 显式绑定到 region；两端 observe 只读取选中地区，无历史生产图恢复 |
| 固定与惰性选择 | 固定后来源排名变化不改变父键；直接桥接待执行选择则重新求值；新来源值允许变化，不以历史差值作为本次贡献分母 |
| 选择域边界 | 空选择保持空筛选，字段 Null 保留，多轴父键联合投影去重；interval/日历/配对/值列不进入普通桥接，错误句柄拒绝 |
| 多父域 | 相同渠道在不同父域独立分行，各父域占比分母独立 |
| 可加变化 | 基期线上/线下 100/100，当期 160/80；贡献 +60/−20，总体 +40，占比 150%/−50% |
| 零总体变化 | 子贡献 +100/−100，总体 0；贡献保留，占比 null，注明 zero_total_change |
| 比率分解 | 基期 A=1/10、B=9/90，当期 A=2/10、B=8/190；总体由 1/10 到 1/20，贡献 A=0、B=−1/20；A 自身 change=1/10 |
| 全局/局部差异 | B 细分基期 4/40、5/50，当期 3/90、5/100；相对原总体两个子组各 −1/40；以 B 为总体使用 90/190，贡献 −49/1710、−5/171，合计 −11/190，占比 49/99、50/99 |
| 线性规则 | A/B 总变化 10/−4，组贡献 (6,4)/(−1,−3)；A−2B 的组贡献为 (8,10)，合计 18 |
| 非线性调查 | max 基期组样本 [0]/[4]/[5]，当期 [100]/[6]/[7]；组 change=100/2/2，总体 change=95，不能求和替代总体 |
| 不支持贡献 | max、精确 P95、一般非线性开启贡献失败；默认保持原公式，不近似或返回伪成功的全 null |
| 重叠 | {1,2} 与 {2,3} 默认可各组计数 2；总体 3；贡献请求因非不交分区失败，不删除重叠组 |
| 来源边界 | 任意结果变换不产生 attribute 方法或来源资格；新请求必须显式提供定义与范围 |
| 退役接口 | 不提供 rollup/drill/share 及兼容别名；粗细粒度均显式 observe，reduce 的行均值 55 不替代原整体 1000/91 |
| 多父域失败 | 任一必要父域缺少分解证明就整体失败，不混合有效与 null 贡献 |
| 比率根分母为零 | 原政策为 null 时默认保留 NA，开启贡献报 root_denominator_zero；原 error 政策不被覆盖 |
| 局部比率 NA | 局部 N=D=0、总体分母非零且组件完整；原政策为 null 时组原值可 NA，贡献仍可有效 |
| 单侧细组 | 父 exact 不强制细组 exact；按本次两端细键并集保留 presence；旧缺侧不屏蔽本次新出现数据 |
| 单侧空贡献 | 已证明空集可用内部加法单位元，原 sum 仍可显示 NA；未知输入不能补零 |
| 配对 NA | value=[10,NA]、weight=[1,9]；使用共同有效配对，不能用全权重分母得到 1 |
| 筛选/预览 | top 留 +60 一组时占比仍 150%；show 限行不裁剪保存结果或重算分母 |
| DSL 继续计算 | attribute 返回普通 AnalysisFrame；where/top/select/算术可组合，完整单总体贡献求和为 40，筛选后的求和为 60，占比仍 150%，reduce 删除行级 presence |
| 多总体规约 | 分别按完整父键汇总贡献；跨父域占比相加仅是普通统计，不自动成为有效总体占比或产生新的归因证明 |
| 固定结果续算 | 相同固定输入在 memory、stored 和新进程冷恢复后继续 select/where/reduce/合法 align/compare，保持对应数据合同且不加载原 Metric 或读取原来源 |
| 空候选/冷读 | 无组时零行，保留必要父域摘要和 empty/NA 政策；冷读保留完整结构而不加载定义 |

后续验收需覆盖显式来源输入与所有权、父域双端映射、贡献默认关闭、规则派发、复杂 per、
合法空贡献/未知输入、共同输入证明以及结构 codec；先定稿接口，再同步 Help、签名、
导出快照、示例和测试。AGENTS/packaged skills 的修改遵守明确授权要求。
本轮只验证文档、算式、示例语法和链接，不改实现，不宣称本地/Runtime/release 验收通过。
