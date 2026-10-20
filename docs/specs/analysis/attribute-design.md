# Attribute：显式来源调查与可选贡献分解

Status: frozen implementation contract，2026-10-20；P0 来源请求、父域交接及保存合同正文保持冻结。
当前实施、审查及验收状态见 [Attribute 实施进度](attribute-implementation-progress.md)。
当前 API 由 [Python Analysis Design](python-analysis-design.md) 和 [Lazy DSL](lazy-analysis-dsl-redesign.md) 拥有；
本文定义显式来源调查与精确贡献的合同，实际支持边界由当前 API 文档和实施证据说明。
P0 核对基线为 `6c2800bfa7`；[AnalysisFrame 实施方案](analysis-frame-implementation-plan.md)
及其 [最终数据合同](query-result-reuse.md) 是修订依据，基础切换的历史记录见
[AnalysisFrame 实施进度](analysis-frame-implementation-progress.md)。
当前公开对象已是 AnalysisFrame，rollup/drill 接口已退役，不重新引入。

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

唯一入口为 `Session.attribute`；不新增 `AnalysisFrame.attribute`、接收历史结果后恢复来源的
重载或兼容别名。新增不可变输入记录 `AttributeEndpoint` 只封装已有 observe 的四项端点输入，
构造时复制 metrics 的有序映射，后续调用方修改原映射不影响请求。它不是来源句柄或执行器。
两端独立范围无法由一个 Metric 映射无歧义表达，因此该记录是必要的最小新增请求类型；
父域直接复用 Population/AnalysisFrame，不新增父节点、专属坐标或运行时父域对象。

下面是本扩展的完整目标签名。`Deferred`、`Entity`、`TimeScope`、`CalendarAxis` 和分类类型
沿已有 observe/桥接定义；公开类型从各自 owning 模块取得，示例中的 `mv.AttributeEndpoint`
由 `marivo.analysis` 导出。下面的类型别名只缩写签名，不新增公开导出或顶层 Help 索引项。

```python
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

Classification: TypeAlias = Deferred | Grouping
ParentTarget: TypeAlias = Deferred | Entity

@dataclass(frozen=True)
class AttributeEndpoint:
    metrics: Mapping[str, Deferred]
    during: TimeScope | CalendarAxis | None = None
    where: Deferred | None = None
    memberships: tuple[Grouping, ...] = ()

class Session:
    def attribute(
        self,
        left: AttributeEndpoint,
        right: AttributeEndpoint,
        *,
        by: tuple[Classification, ...] | Population,
        parents: Population | AnalysisFrame | None = None,
        parent_on: tuple[tuple[FrameAxis, ParentTarget, ParentTarget], ...] = (),
        parent_how: Literal["exact", "outer"] = "exact",
        contribution: bool = False,
    ) -> AnalysisFrame: ...
```

| 唯一输入权威 | 责任 |
| --- | --- |
| left/right.metrics | 显式已解析 Metric；两端非空量名集合相同，左端声明顺序决定输出量顺序 |
| left/right.during、where、memberships | 各端时间、原生 Deferred 条件和显式 Grouping 分配规则；沿当前时间与 observe 绑定权威，没有重复的顶层同名参数 |
| parents | 唯一父选择域；None 是单一零轴总体 `{()}`，空 Population/Frame 是零个总体 |
| parent_on | 仅在 parents 为 Frame 时，将其中每个普通字段/完整身份轴显式绑定到两端新来源目标 |
| parent_how | 本次父域对应的 exact/outer 政策；不控制新增细组的 outer 并集 |
| by | 两端共同的非空新增联合分类，复用第 3 节 tuple 或单个 Population 形状 |
| contribution | 严格 bool，默认 False |

每对同名量均检查 Metric 种类、可比较数值类型、相同单位、时间角色及相容的根、粒度、
规约和 null/empty/有效样本政策。这里的根/配方检查验证两端消费的结构角色相容，
不要求原定义版本相同，不以同路径版本或 fingerprint 变化拒绝请求。
原量的非线性结构合法时默认调查仍合法；仅贡献路径额外检查第 6 节规则。
contribution=True 还要求同名量两端实际消费的贡献规则树逐项相容：对应的 additive/
ratio_component/linear 节点拓扑、线性加减/取负/缩放算子、子式顺序和固定系数必须相同。
比较已消费结构与常数的实际数值/数值类型；不根据定义路径、版本或 fingerprint 判定。
不交换子式、分配乘法或用代数等价重写来修复不相容规则；两端沿相同配方但来源事实变化合法。
因此 left=A−2B、right=A−3B 请求贡献必须在构造/准备时以 unsupported_expression 拒绝，
携带量名、规则树子式路径、两端实际算子/系数和预期的相同结构；修复为显式提供相同系数
的两端定义，或另发 contribution=False 调查原公式。默认调查不要求线性系数相同。
第 6 节的单一固定系数及第 7.3 节单一系数事实均建立在此准入条件上。
所有输入必须归当前 Session/Store，FrameAxis 必须来自 parents 的精确当前输入；
复合身份必须完整。绑定失败报告预期、实际输入及具体修复，不按名称猜测或静默重绑。
构造、待执行 show 和 n=0 不读取业务行、不创建 Run；动态选择只在统一求值时完成。

普通父域可先通过 `session.population_from` 显式桥接，再把返回的 Population 作为 parents。
该情形 `parent_on` 必须为空；Population 的绑定同时用于两端，不接受第二份重复映射。
需要两端不同语义目标时，直接传一个 Frame，并逐普通轴提供 `(axis, left_target, right_target)`。
parent_on 必须恰好覆盖所有普通轴，各轴一次，不包含日历/配对轴；Population/None 下非空映射拒绝。
已有桥接的普通字段/完整身份资格不变，日历/配对转交仅在本入口内按第 2.2 节消费冻结数据。

父域数据与来源定义分别拥有事实：历史值、差值、presence 和生产图均不进入新来源绑定。
已 compute/persist 的选择只复用固定完整键；来源变化可改变新值，不能重跑旧筛选/Top。
待执行选择在每次新求值时重新执行，并在本次端点观察前固定其完整键。

### 2.1 显式贡献请求与规则判断

contribution 只接受 bool，不接受 0/1/None；没有 mode、topk、method 或自动回退。
这是定稿的目标合同，不表示实现或公开披露已经完成。

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

当前集成保留普通轴、日历及配对日历父域的显式绑定合同。main 新增的 interval
和 paired-interval Frame 父轴在读取前拒绝；选择普通父轴或受支持的显式日历父域。
端点 during=TimeScope 仍由现有时间权威私有降级，不因此新增 interval 父域投影或传输架构。

请求显式提供或绑定两端父坐标和对应，不继承一个 compare 节点中的隐藏定义。
数据侧日历匹配遵守 Frame 的自包含规则；新的来源范围、关系和分类仍必须显式提供。
普通轴对应须完整显式绑定；零轴或双方唯一日历轴才使用 Frame 已允许的推导。
基础桥接不接收interval/日历/配对轴，也不允许配对轴再次 compare；本提案不绕过这些限制。
固定父域的每个完整键是独立总体，启用贡献时其本次重算变化拥有该父域的占比分母。
选择时的 presence 不是本次来源的缺侧掩码；实际重算按各端本次输入及原输出域政策决定。
原请求明确排除的范围仍不消费，不能与历史读取暂未出现的组混淆。
父域选择先完成自身全部检查，再将完整键投影到两个显式端点。字段 Null 是类型化合法键，
身份复合键不能逐分量拆开；投影后的重复完整键去重，保留联合相关性。
零父轴时，None 的 `{()}` 与显式空选择不同；空 Frame 的零轴投影仍为空。
父域只给出请求选择范围，不把历史行有无当成本次读取有无。

| parents 的结构 | 新来源绑定及输出父轴 | 必须拒绝的形状 |
| --- | --- | --- |
| None 或纯普通轴 Population | None 为单总体；Population 使用自身显式绑定和完整键，两端同一域 | 非空 parent_on；无父日历时任一端 during=CalendarAxis |
| 普通轴 Frame | 每个普通轴由 parent_on 双端绑定；父键等值对应，输出保留选中父轴 | 漏轴、重复轴、部分身份、错输入句柄或不相容类型/时间锚点 |
| 含一个非配对日历轴的 Population/Frame | 两端 during 均为 CalendarAxis，与保存的同一有限目录完全相容；按同一桶键等值投影，输出普通日历父轴 | 缺少一端 CalendarAxis、只给 TimeScope、目录/名义或实际边界不符、把等值暗改 ordinal |
| 含一个配对日历轴的 Frame | 两端 during 各自匹配保存的左/右端完整有限目录；只消费原配对键→双端桶键附件，输出保留该配对轴 | 新 match、再次 align/compare、缺失/损坏附件、按标签重配对、用当前规则补造缺桶 |
| 普通轴加一个日历/配对轴的 Frame | 普通轴仍全部显式 parent_on；日历轴按上一行对应规则投影 | 多于一个日历承载轴，或新增 by 再引入日历细化 |

“完全相容”比较 builtin grain/step 或 named identity/snapshot/level、timezone、规则版本、
完整有限桶目录、唯一名义标签/原 ordinal 和逐桶名义/实际边界；不比较不同输入的局部 token、
附件名或显示名。新的 CalendarAxis 是调用方显式来源权限，不从旧 Frame 重建；
冻结目录仅验证和定位新范围。配对父域保留当初 full-bucket 准入，不因 where/top 只保留
完整桶而放宽原目录中的部分桶。普通等值日历父域沿原合同，不额外要求 full-bucket。
首版一个端点只有一个 during，所以拒绝多个日历承载父轴；需要新日历配对应先显式 observe
并按 Frame 合同形成父选择，再发起新请求。没有日历父域时只接受 TimeScope/None，
不另外提供 standalone CalendarAxis 输出、match 参数或日历细化。

parent_how 默认为 exact，错误值拒绝，不回退 outer。对应按完整父键进行，不能只检查
日历桶而遗漏地区等其他键。求值先确认请求键的双端结构投影，再按各端原父域输出政策
确定本次父行域：exact 要求两个本次父行域完整一一对应；outer 取其完整并集。
两个端点都无实际父行的请求键不虚构输出细行，但仍完成该请求父域的 empty/NA 与贡献检查，
贡献开启时保留其摘要。完整 Population 的输出域政策可能为无事实父键保留空行；
该已知空行按原 empty/NA 政策求值，不能仅因没有事实或没有细组令 exact 失败。

三种缺侧必须区分：

- 历史父行 presence=False，但冻结映射具有合法端点桶键：该端仍按本次新范围读取，
  新数据可以出现；历史 presence 不作 mask，历史差值不作本次占比分母。
- 本次有合法完整键/范围、已读完且无贡献事实：这是已知空输入，沿原域和 empty/NA 政策，
  不等于结构缺侧；符合第 6.6 节证明时内部可用空集单位元。
- 已保存 outer 配对中的端点桶键本就不存在于该端冻结目录：结构上无可投影范围。
  exact 拒绝；outer 默认调查保留另一端，缺端 presence=False、原量/change 为 NA。
  不读取另一期间、扩充目录或把缺少范围说成已知空输入；贡献请求报 undefined_input。

plain 日历目录的完整相容条件不允许结构缺桶；需要不同有限目录或单侧桶时，先按普通
Frame 规则构造显式 paired 父域。一个结构存在但当前没有父行的端点也不能从旧 presence
推定为空或改用别的桶，必须由本次原量域/empty 政策决定。原请求明确排除的范围仍不消费。
父 exact 不要求细组 exact：新增完整细键始终取两端并集，保留新增/消失组及独立 presence。
分类使用各端显式角色及属性时点，角色不相容拒绝，不能按标签拼接。

每个选中完整父键是独立总体，其本次重算变化拥有该父域的贡献占比分母。
本次贡献解释新读取的两期指标，不承诺复现过去异常值，也不要求跨读相等或自动重试。
两个端点可独立读取；每端内部总体和全部联合组必须使用第 8 节同一实际贡献输入。

以下是目标入口的最小调用。revenue、region、channel 和期间均为已解析的同 Session 输入：

```python
# One overall parent; left is current and right is baseline.
overall = session.attribute(
    mv.AttributeEndpoint({"revenue": revenue}, during=current_period),
    mv.AttributeEndpoint({"revenue": revenue}, during=previous_period),
    by=(region, channel),
    contribution=True,
)

# selected is a fixed ordinary-axis comparison selection.
selected_parents = session.population_from(
    selected, on=((selected.axis("region"), region),),
)
regional = session.attribute(
    mv.AttributeEndpoint({"revenue": revenue}, during=current_period),
    mv.AttributeEndpoint({"revenue": revenue}, during=previous_period),
    parents=selected_parents,
    by=(channel,),
)

# selected_months has one plain frozen calendar axis and no ordinary axes.
# shared_months is an explicit CalendarAxis matching that complete directory.
monthly = session.attribute(
    mv.AttributeEndpoint({"revenue": revenue}, during=shared_months),
    mv.AttributeEndpoint({"revenue": revenue}, during=shared_months),
    parents=selected_months,
    by=(channel,),
)

# selected_pairs has one paired calendar axis plus the ordinary region axis.
# Each explicit CalendarAxis matches its stored endpoint directory in full.
paired = session.attribute(
    mv.AttributeEndpoint({"revenue": revenue}, during=current_months),
    mv.AttributeEndpoint({"revenue": revenue}, during=previous_months),
    parents=selected_pairs,
    parent_on=((selected_pairs.axis("region"), region, region),),
    parent_how="outer",
    by=(channel,),
)
```

paired 例子的 outer 允许结构单侧桶作默认调查，不承诺其可贡献分解；若启用贡献，任一
结构缺侧或必要总体变化未定义都使整个请求失败。plain 日历例子通过同一坐标在两端重新观察，
可使用不同显式 `where` 谓词和 `memberships` Grouping 分配规则；它不隐含两期间的偏移匹配。

### 2.3 当前可复用的基础流程

以下使用现有 Frame API 表达“发现异常 → 固定普通父键 → 显式新来源调查”。
假定同一 Session 中已取得 revenue Metric、region/channel Field 和两个 TimeScope，
轴显示名分别为 region/channel。来源结果带 interval 轴；命名 compare 保留本期 interval
及有限基期说明，桥接仍只选普通 region 轴。
这不是 attribute 的调用签名：

```python
import marivo.analysis as mv

current_regions = session.observe({'revenue': revenue}, by=(current_period, region))
previous_regions = session.observe({'revenue': revenue}, by=(previous_period, region))
regional_change = current_regions.compare(
    {'reference': previous_regions},
    on={'reference': tuple(zip(current_regions.axes, previous_regions.axes))},
    match={'reference': mv.ordinal()},
    how="outer",
).compute()
selected = regional_change.where(regional_change["reference.absolute.revenue"] < 0).compute()
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
相对变化。这里用普通单段量名，attribute 按第 7 节生成规范路径。该流程不构成精确贡献分解。
align + select 保留对齐行序；attribute 还需执行第 7 节的规范排序输出整形，不能据此假定两者行序相同。
不能把上一步 regional_change 的历史差值当作新组贡献的分母，也不能单独再 observe
总体后假定与组数据共享实际输入。贡献请求仍需第 6、8 节的共同输入路线。
配对/日历父域的调查使用第 2.2 节专门转交；不能剥除时间轴后声称保留了原范围。

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
完整 compare 还检查非空且相同的值路径、类型及单位；命名入口保留当前 q，生成
reference.baseline.q/reference.absolute.q/reference.relative.q，
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
可使用现有 API 构造域；下面仍是基础来源示例，不替代第 2 节已定稿的 attribute 调用签名：

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

attribute 请求返回普通 AnalysisFrame，轴为“显式父坐标 + by 的有序业务轴”，
每行一个完整联合组合，无根行、节点轴、depth、父子树结构或合成 Others。
Frame 生命周期、类型化键、名称段、presence_names、show 和物化合同由前置设计拥有。
这里的 left/right 分别代表当前/基准端点；沿用前置设计的名称段路径和 presence 规则，
不增加结果子类型或第二套端点命名。

量按 left.metrics 声明顺序排列，每个 q 的值列顺序如下；presence 是独立非空 bool 目录，
固定为 left_present/right_present，不混入 output_names。

| 请求/规则 | q 的有序值路径 | 类型与单位 |
| --- | --- | --- |
| 默认 | left.q、right.q、absolute.q | 两端按原可比较数值类型输出，absolute 用原减法提升政策；均为 q 单位 |
| additive/linear 贡献 | 以上三列，随后 contribution.q、contribution_share.q | contribution 用合法组件算术的结果类型及 q 单位；share 用既有除法类型、无量纲 |
| 直接 ratio_component 贡献 | 上述五列，随后 numerator_left.q、numerator_right.q、denominator_left.q、denominator_right.q | 组件各保留原始 N/D 类型和单位，符合 N/D=q；这些是该组的 N_t(S)/D_t(S)，不是广播的父分母 |

所有原值/差值及解释组件遵守各自 nullable/empty 政策；缺侧为 NA，不进行强制 float 转换。
父分母 D_t(parent) 只在第 7.3 节父域事实中保存，避免把重复总体值伪装成组组件。
linear 的 ratio 子式用第 7.3 节递归事实解释，不新增不稳定的临时子式值路径。
这些是操作生成的名称段路径，不能作为用户自定义单段量名绕过命名合同；不会生成 relative.q。

开启贡献时保存不可执行方法事实，不是额外结果行；关闭贡献时不生成本次 Attribute 方法事实或贡献专用解释组件/认证；
显式 Frame 输入已有的历史方法事实仍按第 7.3 节继承。零细组父域的必要事实仍保留。typed schema、身份关联、传播及
保存版本由第 7.3–7.4 节闭合，不存 Metric、Deferred、来源图、连接或可执行配方。

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
遵守第 7.3–7.4 节，不成为执行依赖或来源配方。

### 7.2 求值、保存与冷恢复

show(n=20) 在待执行时只展示已捕获信息，物化后预览已有结果；n=0 只展示元数据。
终态 Artifact 仅通过 frame.artifact 访问提交和审计，数据读取归 Frame；其 show() 不预览数据。
compute 不创建持久 Run，persist 发布并返回持久 AnalysisFrame，frame.artifact 才是终态视图。
已保存结果和审计事实均不恢复来源定义。冷恢复、命名冲突与原始数值错误遵守 Frame 合同。

### 7.3 不可执行方法事实及传播

唯一完整读取位置为 `AnalysisFrame.method_facts -> tuple[FrameMethodFacts, ...]`。
内存 Frame 的属性返回已拥有的完整不可变事实；持久 Frame 的属性是显式完整事实读取，
可以读取并验证第 7.4 节保存的方法载荷，不读当前主结果行或原业务来源、不加载 Catalog/模型。
待执行 Frame 读取该属性报“尚未求值”，修复为先 compute/persist，不隐式求值。
来源请求 show 只展示已捕获的请求形状；固定 Frame.show 只展示方法事实数量、origin_result_id
和指向该属性的继续读取提示，不把事实内容当成第二套数据预览。Artifact 仍只拥有终态审计，
不新增 Artifact→Frame 导入或方法事实专用 Artifact 查询入口。

`FrameMethodFacts` 属于既有 Frame* 不可执行结果事实家族；下面的嵌套记录也使用该家族命名，
均为 frozen/slots 只读记录，不能由记录重新构造来源操作。公开导出这些具体结果类型及
AttributeEndpoint；缩写的 union/type alias 不导出，也不新增通用方法注册表。
各具体类型、method_facts 属性和嵌套字段有独立小写 snake_case Help 目标；
`analysis.analysis_frame` 仅导航到 `analysis.analysis_frame.method_facts`，
其类型页导航到 `analysis.frame_method_facts` 及必要子类型，不扩展顶层 Help 索引。
每种记录使用有界单行 repr（kind、origin/局部身份、`.show()` 指针），不使用默认 dataclass repr。
`.show(n=20)` 仅渲染记录中已保存的事实，按父键/量/子式规范顺序限行；n=0 只显示字段目录和计数，
合法 n 的规则与 Frame.show 一致。完整字段始终通过只读 typed 属性取得，show 不裁剪其载体。

以下是闭合 schema；类型 `DataSchema`、`DataColumn`、`DataType` 复用现有纯数据目录的
具体类型，不引入语义 Ref。为记录 typed 值，定义内部缩写
`MethodScalar = bool | int | float | str | date | Decimal | None`；这里没有 Any/object/dict。
时间戳和 Duration 使用其 DataType 单位的整数 ticks，避免通过 datetime/timedelta 损失 ns，
date 保留 date，Decimal 保留原精度/scale，不转为 float。每个 FrameMethodValue 按 DataColumn
校验精确 scalar 类型、nullable、单位和有限值；bool 不作为 int，NA 规范为 None。

| 具体记录 | 全部字段（除此之外无自由扩展字段） |
| --- | --- |
| FrameMethodValue | column: DataColumn；value: MethodScalar |
| FrameMethodCoordinate | name: str；schema: DataSchema；rows: tuple[tuple[MethodScalar, ...], ...] |
| FrameMethodSupport | root_id: str；policy: Literal["rows", "non_null_value", "co_valid_value_weight", "identity", "preserved_sample"]；left_input_count: int；right_input_count: int；left_effective_count: int；right_effective_count: int |
| FrameAdditiveMethod | kind: Literal["additive"]；left: FrameMethodValue；right: FrameMethodValue；change: FrameMethodValue；support: tuple[FrameMethodSupport, ...] |
| FrameRatioMethod | kind: Literal["ratio_component"]；left: FrameMethodValue；right: FrameMethodValue；change: FrameMethodValue；numerator_left: FrameMethodValue；numerator_right: FrameMethodValue；denominator_left: FrameMethodValue；denominator_right: FrameMethodValue；support: tuple[FrameMethodSupport, ...] |
| FrameLinearTerm | coefficient: int \| float \| Decimal；method: FrameAdditiveMethod \| FrameRatioMethod \| FrameLinearMethod |
| FrameLinearMethod | kind: Literal["linear"]；left: FrameMethodValue；right: FrameMethodValue；change: FrameMethodValue；terms: tuple[FrameLinearTerm, ...] |
| FrameMethodMetric | name: str；method: FrameAdditiveMethod \| FrameRatioMethod \| FrameLinearMethod；share_state: Literal["defined", "zero_total_change"] |
| FrameMethodParent | key: tuple[FrameMethodValue, ...]；metrics: tuple[FrameMethodMetric, ...] |
| FrameMethodFacts | kind: Literal["attribute"]；origin_result_id: str；output_schema: DataSchema；coordinates: tuple[FrameMethodCoordinate, ...]；parent_axis_count: int；parents: tuple[FrameMethodParent, ...] |

output_schema 为原 Attribute 完成结果的完整纯数据目录，记录原值路径、类型、单位和轴；
parent_axis_count 指其前 N 个完整父轴，不能负数或超过原轴数。parent.key 恰好按这 N 个轴的
完整列次序存值，column 必须与原目录一致；不是显示标签、散列或单个复合键分量。
零父轴单总体用唯一 key=()；零个请求父域为 parents=()，无细组父域仍有父记录。
父记录按规范完整父键排序且唯一，metrics 按左端声明顺序恰好覆盖原 q 集合。
父事实中的 left/right/change 均是本次同输入重算总体；贡献成功时非 NA、change 按原数值政策
等于 left−right。share_state 由该量的 change 是否为零确定，不以显示舍入判断。
FrameRatioMethod 的 N/D 均是该父总体组件，D 不得为零，与第 7 节组组件列有明确区别。

support 记录实际有效样本数和样本政策，不存原始行或成员身份。input_count 是端点/父范围
限定后、该样本政策剔除无效值前的样本数，effective_count 是剔除后的样本数；
计数为非负精确 int，
每端 effective_count 不超过 input_count。root_id 是本次规则树中稳定的局部组件编号，
只标识事实的共享/区分关系，不是可解引用的来源地址、执行 token 或授权。
直接 weighted_mean 必须为 co_valid_value_weight，直接 mean 为 non_null_value；复杂 per 使用
preserved_sample 并保持原样本单位，不能仅填普通行数声称证明成立。
证明本身在执行阶段完成；这些事实说明成功采用的样本，不以计数相同充当共同输入证明。
additive 保存必要根支持；ratio 保存 N/D 各根支持，共同有效配对的同根只存一次；
linear 按已消费子式次序递归保存实际系数和全部子式总体/规则事实，不保存表达式字符串或树执行指令。
每个 term 的 coefficient 是第 2 节已验证双端相同的有限、无量纲、非 bool 原数值常数；
负号用 −1，减法保存带符号系数，不做常数偏移、代数相消或其它改写。

coordinates 恰好保存 output_schema 所需的原冻结日历目录/配对附件，名称唯一、结构和行均
通过已有 typed 目录/对应验证；没有这些轴时为空。它们是有限惰性数据的独立只读快照，
不是原结果行、CalendarAxis 或连接。这样 reduce 去掉日历轴后，历史方法事实仍能解释其
原父键，不能悬空指向已被普通操作删除的当前坐标附件。完整公开记录只存在于完成/内存载体
或显式事实读取的结果中；持久载体的父记录及坐标行进入第 7.4 节独立的 owned 方法载荷，
manifest 只存小型描述及精确收据引用。方法载荷沿用同一 Artifact 的附件发布，不混入
CompletedFrameData 仅允许当前坐标所需附件的清单，不需重新加载原 Artifact。

origin_result_id 为生成此精确分解的完成结果身份；只有元数据的当前 result_id 与它相同，
该记录才解释当前原 Attribute 结果。此比较只是显示原结果关联，不赋予来源资格。
compute/persist/冷恢复转换同一固定结果保持原身份和全部事实；任意 DSL 数据操作生成新
result_id，继承的事实仍只解释 origin。即使恒等 select、top(0) 或算术偶然得到相同值，
也不会生成新的精确分解认证。原输出目录用于解释旧路径，不要求派生结果继续保留这些路径。

| 操作/边界 | method_facts 的确切传播与关联 |
| --- | --- |
| Attribute contribution=False、普通新 source/literal | 本次不生成方法事实；无显式 Frame 依赖时为空 |
| Attribute contribution=True 成功 | 生成一条当前结果事实；若父选择本身消费有事实的 Frame，再保留该输入的历史事实 |
| compute/persist 固定结果、memory→stored、冷恢复、再次保存 | 保留相同 origin、完整结构/父事实/坐标快照和顺序；persist 不重算固定结果 |
| where/top，包括零行、top(0) | 完整继承原事实，不按保留行裁剪父摘要、不改总体分母；仅作 origin 的历史解释 |
| select、重命名、剪列、Column 算术 | 完整继承输入事实；原路径仅归 origin，不能移接到新列或声明新值可分解 |
| reduce，含删除全部父轴/日历轴 | 完整继承原事实和其独立坐标快照；无当前行级关联，不认证新聚合值；普通 presence 仍删除 |
| align/compare | align 按左再右，命名 compare 按本期再基期映射声明次序继承历史事实；不配对/合并父摘要，不认证新变化 |
| population_from/其他显式 Frame 选择依赖→新来源结果 | 按实际 Frame 输入顺序继承其历史事实，不从事实恢复 Metric/范围；新贡献结果另有自己的 origin |

单输入保持原顺序；多输入按确定的输入遍历顺序合并，以 origin_result_id 去重。
新的 Attribute 事实先于继承事实；继承部分按上述输入顺序。
同 origin 的载荷必须完全相同，否则报完整性错误，不“取第一条”；不能依 origin 查询旧结果。
不做一般 lineage 展开，只携带已消费 Frame 自包含事实。纯数据派生无需原定义，
不增加新的业务求和禁令，后置筛选、剪列及预览不得剪掉当前请求必须完成的检查。

```python
fixed = overall.compute()
facts = fixed.method_facts
facts[0].show(n=20)
first_parent = facts[0].parents[0]
first_metric = first_parent.metrics[0]
assert first_metric.name == "revenue"

selected_values = fixed.where(fixed["contribution.revenue"] > 0).compute()
assert selected_values.method_facts == facts
```

上述单一总体示例没有历史父输入，故 facts[0] 是本次 origin；一般多输入通过
origin_result_id 选择记录，不能假定最新记录总在第一个位置。空结果同样可读取必要事实。

### 7.4 同一 Frame 协议的闭合版本

完整 `FrameMethodFacts` 是公开读取/内存表示，不是 ArtifactManifest 的内嵌元数据。
每个 origin 的父域数或日历目录行数可以任意增长；manifest 大小仅随原输出 schema 和
实际继承 origin 的描述数增长，不能随这些事实行数增长。

| 私有载体 | 确切责任 |
| --- | --- |
| CompletedFrameResult / MemoryFrameBacking | 在现有完成结果中另持有 method_facts: tuple[FrameMethodFacts, ...]，已完整验证、独立不可变；不把大 tuple 放入共享 FrameResultMetadata |
| ArtifactManifest / StoredFrameBacking | 新增 method_facts: tuple[SavedFrameMethodFacts, ...]，只持有下述小型描述；FrameResultMetadata 保持 result_id/audit/findings，没有完整父域或坐标值 |
| SavedFrameMethodFacts（私有闭合记录） | kind: Literal["attribute"]；origin_result_id: str；output_schema: DataSchema；parent_axis_count: int；parent_count: int；coordinate_count: int；coordinate_row_count: int；facts_digest: str；payload: SavedAttachment |

SavedFrameMethodFacts 的 output_schema/parent_axis_count 与公开记录相同；其余计数为非负
精确 int。payload 是同一 Artifact 拥有的现有 SavedAttachment，不是原 Artifact ID、来源地址
或 lineage 查找。每个 origin 恰有一份方法附件；无事实的普通结果使用空 tuple。
当前数据坐标附件仍由 ArtifactData.attachments 保存；方法附件由上述显式独立清单保存，
各自验证声明的精确清单，避免把历史坐标冒充当前 Frame 坐标。两者复用现有 write_output、
ParquetHandle、LocalReceipt、文件/集合收据、暂存资源和同一个原子 publish/readback，
方法附件不是新的 Store、发布事务或通用 metadata 框架。

每份方法附件使用固定 typed 表布局：一个非 nullable int64 普通键列 entry，两个非 nullable
string 值列 kind/record；slot 和路径固定为 entry/kind/record，无 presence 或坐标附件。
entry 从 0 连续递增；前 parent_count 行 kind="parent"，record 为对应 FrameMethodParent 的
严格 output/v3 canonical JSON 记录；随后 coordinate_count 行 kind="coordinate"，record 为
对应 FrameMethodCoordinate 的同一闭合编码。父行按规范完整父键排序，坐标行按附件名称排序。
record 只能解码为该 kind 的具体白名单记录，不接收自由 JSON、表达式或任意类型。
方法载荷的行数恰为 parent_count+coordinate_count，coordinate_row_count 是全部坐标记录
内部 rows 的总数；空父域且无坐标时保存合法零行附件，不虚构父行。facts_digest 是描述头
(kind/origin_result_id/output_schema/parent_axis_count) 加这些有序闭合记录的 canonical JSON SHA-256，
与文件收据共同验证；它用于同 origin 逻辑载荷比较，不能解析为执行或历史读取资格。
这样普通高基数父域和大日历目录都只增长载荷，不增长 manifest 中的值清单。

打开持久 Frame、构造待执行派生式、Frame.show(n=0) 和 Artifact 元数据卡片只读取/校验
manifest 中的描述、声明 schema、计数及收据结构，不读取方法附件行、校验载荷文件或解码
父域/坐标值。普通 Frame 预览同样只按原合同读取当前行/坐标，不隐式展开历史方法载荷。
显式读取 frame.method_facts 时，才按本 Frame 的精确 SavedAttachment 引用读取方法载荷，
验证实际文件/集合收据、物理 schema、entry/kind/计数、facts_digest 和完整闭合事实/坐标关联，
然后重建并返回公开 tuple；元数据可用不表示载荷完整，丢失/损坏附件必须在该消费边界报错。
已取得的 FrameMethodFacts.show(n=0) 仅展示内存事实目录；调用 getter 获取事实本身是完整读取，
不能用随后的 n=0 声称 getter 没有读载荷。manifest 打开不能提前执行这种完整读取。

真正 compute 派生结果时，为继承事实消费已保存输入的方法载荷并完成上述验证，生成独立
完整内存事实；不要求用户先调用 getter。persist 已完成的 memory 结果只编码这些已有事实，
不重算来源。若从持久输入直接发布新的派生 Artifact，同样验证并将其事实写成新 Artifact
自己的方法附件，引用新收据；不得悬空指向原 Artifact 的文件。直接复用同一已存固定结果
可沿既有合同返回原持久句柄，不为复制而新增发布。reduce 即使删除全部原轴，新产物仍拥有
其继承父域/坐标载荷；关闭/移除原 Artifact 或来源不影响新产物完整事实读取及后续数据运算。
相同 origin 的逻辑描述/facts_digest 不同即拒绝；相同时可描述去重，但每次实际消费仍验证
所用载荷，不以 digest 相同跳过收据或闭合结构检查。

这是实际格式变更，不是在 Store13/v2 下添加可被旧 reader 忽略的字段。
第 7.3 节公开事实和本节私有描述加入精确 record/field 白名单，codec 只允许公开事实在
方法载荷中出现，ArtifactManifest.method_facts 的类型严格限定为 SavedFrameMethodFacts；
直接把 FrameMethodFacts/parents/coordinate rows 放进 manifest 必须拒绝。
EvaluationRecord.operations 的闭合字面量集合新增 attribute。既有 ArtifactManifest、数据文件及
坐标保存路径继续使用同一 Frame publish/readback/receipt 体系，原子成功规则不变。

| owning 位置 | 本扩展实施时的精确版本变更及原因 |
| --- | --- |
| terminal_protocol.py FORMAT 与精确字段目录 | marivo.frame_output/v2 → marivo.frame_output/v3；新结果字段和记录不能由旧闭合 codec 表达 |
| ArtifactManifest.semantics_version | frame.v2 → frame.v3；方法事实的结果身份/传播成为当前结果语义的一部分 |
| layout.py/store.py 当前 Store generation/user_version | 13 → 14，目录 generations/v14；新 manifest 和集合协议不能混入 Store13 |
| evidence collection descriptor/digest 与 terminal_store | frame-output-v2 → frame-output-v3；收据和集合验证必须描述实际写入的 v3 字节 |

没有方法事实的普通结果也统一写 v3，method_facts=()；有显式事实输入则按第 7.3 节继承，
manifest 始终只写描述。不同时维护 v2/v3 写入或按结果种类选 Store。
Store14 opener 明确拒绝旧 user_version=13，Store13 reader 也不能接受 v3；保留旧目录原样，
不迁移、不重放、不双读、不从旧产物自动复制数据。用户可在独立新 Store 显式重建需要的分析，
错误必须说明实际/预期版本及此修复，不删除旧状态。其他不受本次新增记录影响的协议不任意 bump。

v3 manifest 解码只验证闭合描述与元数据关联；完整事实验证发生在求值完成、写出或实际
读取方法载荷时。相应消费边界拒绝未知/缺失/多余字段、非法记录/版本、错误 scalar
类型/单位/nullable、重复父键、错误父轴数、量集合不完整、错误零变化标记、N/D 或 linear
结构/类型不符，以及不完整或矛盾的冻结坐标快照。相同 origin 不同载荷也拒绝。
只验证有限 typed 结构与关联，不重新读取来源、重新证明分区或以浮点逐位守恒替代执行证明。
元数据和所有数据附件一同受原文件/集合收据验证；writer 不补排序或修复坏事实。
冷读保留既有规范行序、零行结果及完整父摘要。固定结果继续 DSL 时，所有事实已自包含，
没有原模型、Catalog、CalendarAxis、来源或原 Artifact 在线的要求。

本节描述集成后的 Store14/v3 目标协议；原 Attribute 验收中的 Store13/v2 记录
仅证明其当时源版本，不覆盖当前 main 的 Store13/v2 闭合 schema。集成验收必须同步
上述 owning 常量、codec、错误/文档及冷读边界后才能声称当前候选完成。

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
已有 original-input 路线对 provider、身份、日历/window/per/样本等形状的资格限制仍生效；
本合同不表示任意 provider/形状已支持，新增路线必须取得实际输入及原规约/check 的独立资格证据。

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
2. 实现第 2 节显式来源请求：两端 Metric/范围、父域双端绑定、by 分类以及共同实际输入。
3. 按架构 §6.4 复用基础能力、补齐贡献证明和父域映射缺口；不要求全部公开为新方法。
4. 实现第 7 节完整结果 schema/方法事实 codec、输出传播与冷恢复，之后才能发布归因入口。

第 6 节数学合同不依赖旧入口，继续作为后续实现的依据。
不得仅因本文有公式和示例，就声称基础 Frame 首版已包含双端来源调查或精确贡献。
配对/日历父域交接、显式来源请求签名和方法事实传播已在本文定稿，仍需实现与独立验收。
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
| 双端相同线性系数 | 两端实际均为 A−2B，使用上行输入，贡献为 (8,10)、总体 18；同路径版本/fingerprint 变化本身不拒绝，保存一个共同系数 −2 |
| 双端不同线性系数 | left=A−2B、right=A−3B，A/B 两期均为 10/4；默认调查仍返回总体变化 4；请求贡献以 unsupported_expression 指明系数 −2/−3 和子式位置，修复为相同显式系数或 contribution=False；不得套共同系数返回零贡献 |
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
| 方法事实读取边界 | 分别扩大普通父域数和冻结日历目录：打开 stored、show(n=0)、Artifact 卡片只解码小型描述，方法载荷零读取；显式 method_facts 验证全部消费载荷，损坏/缺失文件此时失败 |
| 方法事实自包含 | reduce 删除全部父轴/日历轴后保存新产物，关闭/移除原 Artifact 与来源，仍可完整读取父事实/坐标；新 manifest 不嵌入父键/坐标行、不引用原产物文件，普通 DSL 保持原义 |

后续验收需覆盖显式来源输入与所有权、父域双端映射、贡献默认关闭、规则派发、复杂 per、
合法空贡献/未知输入、共同输入证明以及结构 codec；按本文定稿接口同步 Help、签名、
导出快照、示例和测试。AGENTS/packaged skills 的修改遵守明确授权要求。
本轮只验证文档、算式、示例语法和链接，不改实现，不宣称本地/Runtime/release 验收通过。
