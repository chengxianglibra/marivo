# Correlation Statistics：当前行上的成对相关统计

Status: implemented and locally validated，2026-10-20；correlate/corr/pair_count 已实现，
阶段、独立审查与最终验收结论见 [实施计划](correlation-statistics-implementation-plan.md)。

本提案扩展 [AnalysisFrame](query-result-reuse.md) 的当前行规约，遵守
[Python Analysis Design](python-analysis-design.md) 的输入绑定与生命周期，以及
[Operators and Frames](operators-and-frames.md) 的坐标、NA、presence 和数值政策。
本文只拥有相关统计的增量合同，不改变来源 Metric、Population、对齐或保存合同。
范围外的相关计算仍遵循既有 pandas 外部计算边界。

## 1. 目标与范围

在一个明确的 Frame 样本域内，对两个数值表达式计算 Pearson 或 Spearman，
可按已有完整轴分组，并显式取得与系数使用同一批样本的有效配对数。
AnalysisFrame.correlate 是常规入口，一次处理一个变量对和一种方法，固定返回
coefficient 与 n_pairs。多个指定变量对、多个方法或混合其它统计使用组合层；
两层结果均为普通 AnalysisFrame。

首版新增 AnalysisFrame.correlate、Column.corr 和 Column.pair_count。
correlate 组合基础统计并提供规范结果；corr/pair_count 保留独立统计能力。
保持 reduce 的签名、一项命名统计对应一列标量、独立有效掩码、
输出分组域和载体合同。
不增加 Session 统计入口、复合相关结果类型、自动展开列或公共样本描述对象。
方法实现复用现有本地数据计算路径，不新增执行器或默认来源侧下推。

首版不包含其它相关算法、矩阵专用入口、权重/样本门槛、自动预处理、时序/偏相关或统计推断。

## 2. 公开表达

### 2.1 常规入口：AnalysisFrame.correlate

```python
def correlate(
    self: AnalysisFrame,
    x: Column,
    y: Column,
    *,
    method: Literal["pearson", "spearman"],
    by: tuple[FrameAxis, ...] = (),
) -> AnalysisFrame: ...
```

- x、y 只接受绑定到接收者精确输入的 Column；允许同输入的数值表达式。
  不接受字符串列名、标量、数组、Deferred、Frame 或 Artifact。
- method 必填，只接受两个列明的字符串；不推断、不自动回退。
- by 沿用 reduce 的完整轴元组合同；省略时对所有当前行规约。
  样本筛选、变量选择、跨 Frame 对齐和分组选择均由调用方明确表达。
- 返回惰性的普通 AnalysisFrame，构造时不读取数据，也不隐式 compute/persist。
  接收者即使已完成，相关计算仍由返回 Frame 的 compute/persist 触发。
- 命名值及顺序固定为 `output_names == ("coefficient", "n_pairs")`；
  分组坐标不计入 output_names。两列是单段名，不加方法名或输入名的前缀，
  不自动改名，也不提供结果命名、可选计数或多方法参数。
- coefficient 与 n_pairs 必须使用同一次求值中、同一输入、同一组、同一变量对的
  共同有效集合。输出域、类型、NA、presence 和数值规则均沿用第 3–5 节。
  需要其它名称时，通过现有 select 显式命名返回结果。

假定 frame 的完整坐标是一行一个客户，包含 region 轴及 spend/revenue 数值。
以下为常规用法；先固定数据，便于后续核验复用同一份输入：

```python
fixed = frame.compute()
summary = fixed.correlate(
    fixed["spend"],
    fixed["revenue"],
    method="pearson",
    by=(fixed.axis("region"),),
)
result = summary.compute()
```

整体相关省略 by；Spearman 使用 `method="spearman"`。n_pairs 始终随系数返回，
即使系数因样本不足或常量端而为 NA，也保留实际计数。

### 2.2 组合入口：Column 统计与 reduce

_Statistic 沿用 sum/mean 的私有未完成统计表达类型，
不新增公开导出，也不要求用户导入它：

```python
def corr(
    self: Column,
    other: Column,
    *,
    method: Literal["pearson", "spearman"],
) -> _Statistic: ...

def pair_count(
    self: Column,
    other: Column,
) -> _Statistic: ...
```

- corr 的 method 必填，只接受两个列明的字符串，不推断、不自动回退。
- pair_count 无 method；Pearson 和 Spearman 使用相同的配对资格。
- other 只接受 Column，不接受字符串列名、标量、数组、Deferred、Frame 或 Artifact。
- 两者仅捕获表达式，由所属 Frame.reduce 消费，不能自行执行或参与算术。
- reduce 仍接收非空有序命名统计映射及 `by: tuple[FrameAxis, ...] = ()`。
  名称、顺序和冲突规则沿用现有合同；每项只输出一列，不因 corr 自动展开计数列。
- pair_count 可独立请求，corr 仍只表示系数。组合层需要报告样本量时，
  调用方显式请求两者并保证操作数对应；correlate 则统一承担这一配对责任。

需要同时比较两种方法并附带行数时，使用同一份固定输入组合统计：

```python
import marivo.analysis as mv

x = fixed["spend"]
y = fixed["revenue"]

summary = fixed.reduce(
    {
        "pearson": x.corr(y, method="pearson"),
        "spearman": x.corr(y, method="spearman"),
        "n_pairs": x.pair_count(y),
        "n_rows": mv.row_count(),
    },
    by=(fixed.axis("region"),),
)
result = summary.compute()
```

省略 by 即对所有当前行规约。也可直接对待执行 Frame 构造上述统计，
但再次求值该来源表达式可能读到变化后的数据。输入经 compute、select、where、
align 等形成另一 Frame 后，必须从实际接收者重新取得 Column/FrameAxis。
这些输入与生命周期规则同样适用于 correlate。
接口不通过回调或任意参数字典扩展上述固定范围。

### 2.3 两层职责与等价性

| 层 | 公开责任 |
| --- | --- |
| AnalysisFrame.correlate | 一个变量对和一种方法的常规入口；检查接收者绑定，固定结果名称、顺序及系数/计数配套关系 |
| Column.corr / Column.pair_count + reduce | 捕获可独立组合的标量统计；调用方选择统计项和名称，可组合多个变量对、方法及其它统计 |
| 共享规约与数值内核 | 第 3–5 节的配对、类型、分组域、排名、退化值、数值与错误合同；两层共用 |

对于绑定到同一 frame 的 x、y、by，以下两种写法语义等价：

```python
summary = frame.correlate(x, y, method=method, by=by)

summary = frame.reduce(
    {
        "coefficient": x.corr(y, method=method),
        "n_pairs": x.pair_count(y),
    },
    by=by,
)
```

等价范围包括输出 schema、完整分组键与行序、配对集合、NA、数值、presence
及必要检查；错误上下文应指向实际调用入口。它不要求两种写法具有相同表达式身份、
定义指纹或共享缓存。高级入口不能分别 compute 两项统计后再拼接结果。
实现复用同一规约计划与数值内核，不要求逐个实例化公开中间对象；遵循
[高级 API 架构](analysis-implementation-architecture.md#64-compareattribute基础能力组合的高级-api)。

## 3. 输入绑定、类型与单位

### 3.1 精确输入绑定

两个操作数与 correlate 接收者，或两个操作数与消费统计的 reduce，
必须绑定同一精确 Frame 输入；by 中的 FrameAxis 同样来自该输入。
所有权与存储句柄规则直接遵循 [AnalysisFrame 输入身份](query-result-reuse.md#51-结果内坐标与输入身份)。

跨 Frame 先通过现有 align 显式配对全部完整轴，再从返回的 Frame 取得
left/right 数值列。相关表达不新增 join/on/match 参数，不按位置、标签或来源历史配对。
现有同输入数值 Column 算术可以作为操作数；数值型单分量 coord 也遵循现有准入，
但其是否具有合适的业务含义仍由调用方判断。

构造与 pending show 不读取业务数据。已知类型/绑定错误在构造时拒绝，
未决物理类型在既有准备边界检查；依赖实际值的检查在求值时完成。

### 3.2 物理类型与结果

输入只接受既有 numeric 家族：int64、有限 float64 和受支持的 Decimal(p,s)。
两端可采用不同数值类型和单位，例如金额与数量；不要求单位相等，也不执行单位换算。
boolean、string、date、timestamp 和 Duration 不隐式转成数值。
需要其它表示时，由调用方通过有定义的来源表达或外部计算明确处理。
这是比 sum/mean 更窄的准入：unresolved 只允许延迟判断，准备阶段若解析为
Duration，同样拒绝。错误的 expected 是 numeric，received 标明实际端点与 Duration
物理单位；repair 必须提示先构造有明确数值单位的来源表达或使用外部计算边界，
不能复用 sum/mean 接受 Duration 的说明。

| 组合统计 / correlate 固定列 | 输出类型 | 可空 | 单位 |
| --- | --- | --- | --- |
| corr / coefficient | float64 | 是 | `"1"`，无量纲 |
| pair_count / n_pairs | int64 | 否 | 未声明，与当前 row_count 一致 |

计数保持整数，不因同次 reduce 包含浮点系数而提升为浮点。
输出不新增逐格状态、原因列或专用结果子类型。
pending schema 的输出 kind 均为 numeric；corr 固定 nullable=True、unit="1"，
pair_count 固定 nullable=False、unit=None，即使输入类型尚未解析也不变。
物理输出类型按上表确定，不能从 `value is not None` 或某一个操作数的单位推导。
correlate 的两个固定列在 pending 与完整结果中都遵循同一规则；
这些输出事实不免除两个输入的后续物理类型检查。

## 4. 有效配对与算法

### 4.1 每个变量对拥有自己的有效样本

先按当前行建立分组 g，再计算该组的共同有效集合：

```text
P_g(x, y) = {i in g | x[i] is not NA and y[i] is not NA}
n_pairs = len(P_g(x, y))
```

有效配对只排除值 NA，不去重相同数值，不排除零，不填补缺失，也不自动判断异常值。
非有限值和非法算术仍按数值错误处理，不能通过“有效性筛选”转成缺失。
每个保留坐标行贡献一个等权配对，不恢复原始事实权重、样本权重或成员分配权重。

同一输入、同一组、同一对操作数的 corr 和 pair_count 使用同一集合。
不同变量对各自取交集，不将它们扩大成所有输出共同的完整样本集合。
组合层不根据输出名称推断关系：把 `x.pair_count(z)` 命名为 n_pairs，
不会使它自动成为 `x.corr(y, ...)` 的样本量。
correlate 由入口固定使用同一对操作数，保证两列配套。

现有独立掩码规则保持不变：一个多输入统计内部取输入有效掩码的交集；
同次 reduce 中的 x.mean、y.mean、sum 和 row_count 仍使用自己的有效样本。

允许同一表达式作为两端：`x.pair_count(x)` 正是该数值表达式的非 NA 行数，
仍保留零有效值组；Help 应明确这一用法，不新增单列 count 别名。
`x.corr(x, ...)` 在 n>=2 且非常量时为 1，否则按第 5 节返回 NA。

交换操作数不改变数学结果：pair_count 精确相等，corr 遵循第 5.2 节浮点合同，
以第 8.1 节见证及其容差核验两种顺序；两端单位不同也不改变这条规则。
捕获表示、pending show 和定义指纹仍保留调用方给出的有序操作数，
不按对称性排序或合并表达式身份，不新增缓存复用约定。

### 4.2 Pearson

在 P_g 上计算中心化的标准 Pearson 系数：

```text
r = sum((x - mean(x)) * (y - mean(y))) /
    sqrt(sum((x - mean(x))**2) * sum((y - mean(y))**2))
```

该定义不增加 ddof 参数，也不把“可计算描述性系数”解释为满足检验假设。
定义参照 [SciPy Pearson](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.pearsonr.html)；
该库附带的 p 值与区间接口不属于本提案输出。

### 4.3 Spearman

先取得 P_g，再在该组的有效配对内分别对 x、y 升序排名，
最后对两个排名向量计算 Pearson。并列值固定用平均秩，排名从 1 开始；
例如 `[10, 10, 30]` 对应 `[1.5, 1.5, 3]`。
参照 [Spearman](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.spearmanr.html)
及 [平均秩](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.rankdata.html) 定义。

不能先对完整列排名再删缺失，也不能先全局排名再按组截取。
不同变量对可能拥有不同配对样本，因此不能直接复用某列的全局排名。
排名和系数不依赖输入行的排列；浮点结果仍服从数值误差边界。

### 4.4 Presence 与检查义务

配对掩码只消费两个值表达式，不扫描 presence、不从生产历史判断样本是否真实存在。
对齐后的缺端点通常使相应值为 NA，但 presence 与 NA 仍是独立事实。
来源显式 empty="zero" 产生的零是有效数值，不被改判为缺失。

若业务要求两端都存在，先用实际 presence 列显式 where，再从筛选后的 Frame
重新取得 Column。该筛选会改变输入域，可能删除整个组；这是调用方的显式选择。

共同掩码不短路操作数已有检查。某个 x 子式发生严格除零或其它必要错误时，
不能因为对应 y 为 NA 而忽略。Top-0、输出剪列及后置 where 也沿用既有检查边界。

## 5. 分组域、退化值与数值边界

### 5.1 输出域

以下规则同时约束 correlate 及组合层；表中的 corr/pair_count 分别对应
correlate 输出的 coefficient/n_pairs。

分组使用 reduce 的当前完整轴，保留 by 给出的轴顺序，不读取新分类或粗化日历。
组依据输入当前行形成，先于各项统计的 NA 筛选；Null 分组键仍是合法类别。
复合轴保留全部键分量；组行顺序沿用 reduce 现有的完整分组键规范次序，
见 [AnalysisFrame 行序合同](query-result-reuse.md#6-结果行序由计算决定)，不按输入首次出现排序。
所有输出 presence 均按现有 reduce 合同删除。

| 输入情况 | 输出 |
| --- | --- |
| by=()，输入为空 | 一行零轴标量，corr=NA，pair_count=0 |
| by 非空，输入为空 | 零行，保留输出 schema，不补造分组 |
| 某组有行但有效配对为零 | 保留该组，corr=NA，pair_count=0 |
| 某组只有一个有效配对 | corr=NA，pair_count=1 |
| 至少两个配对，但任一端为常量 | corr=NA，pair_count 为实际数量 |
| 恰好两个配对且两端均非常量 | 系数按定义为 ±1，pair_count=2 |
| 其它可计算组 | 系数在 [-1, 1] 内，pair_count 为实际数量 |

退化情况按普通 NA 表示；Help 解释其条件，不用 0 代表未定义。
pair_count 不因系数未定义而清零，也不因输出只需计数而计算相关系数。
首版不设置业务最小样本门槛；调用方可按结果中的 n_pairs 显式筛选。

pair_count 不能简单实现为先过滤整个 Frame 再 row_count：那会丢失零有效配对组，
并改变同次 reduce 中其它统计的样本域。

### 5.2 数值精度与失败

常量判断及 Spearman 排序/并列判定使用实际输入值，不能先整体转 float64；
常量是全部值精确相等，没有“近常量”容差或专用报错分支。
在共同有效配对内，n<2 或任一端为常量时按第 5.1 节返回 NA；
其余输入采用以下固定 float64 计算路径：

1. 对每个 int64/Decimal 向量 z，先精确计算 `z[i] - z[0]`，再转 float64。
   整数差使用足够宽的整数；Decimal 差可由同尺度整数计算，不受默认 Decimal
   context 舍入，也不要求临时差写入原 Decimal(p,s)。精确算术只负责这里的差值，
   不在 Decimal 中计算均值、平方或协方差；原操作数表达式已有的溢出检查仍生效。
2. 对 float64 向量，用 `e = frexp(max(abs(z)))[1]`、`a[i] = ldexp(z[i], -e)`
   按 2 的整数幂缩放，再计算 `a[i] - a[0]`。缩放后的最大绝对值位于 [0.5, 1)，
   避免有限极值相减溢出；不直接构造可能溢出/下溢的 `2**e`。
   使用二进制尺度，而非任意除数，避免平移前的舍入扭曲相邻浮点值之间的差异。
   第 1 步产生的差值向量也先除以自身最大绝对值，再进入第 3 步。
3. 两遍计算中心矩：第一遍求缩放向量的均值，第二遍取得中心化残差。
   每端残差再除以自身最大绝对值，得到 u、v；分别累计 `sum(u*u)`、
   `sum(v*v)` 和 `sum(u*v)`，用
   `r = sum(u*v) / (sqrt(sum(u*u)) * sqrt(sum(v*v)))` 计算系数。
   累计采用 float64 的补偿求和或等效稳定求和，不使用原始平方和相减。
   残差缩放避免极小差异在平方时下溢。
4. 明确检查：转换或中间结果非有限，或原值非常量而计算尺度/残差范数为零，
   均报结构化数值错误，不返回 NA 或合法系数。有限 r 固定执行
   `max(-1.0, min(1.0, r))`；不得用截断把 Infinity/NaN 变成 ±1。

Spearman 的平均秩向量使用同一中心矩计算，不重复排名。
这条路径不切换算法或随机抽样，也不承诺任意精度实数结果或跨平台逐位一致。

[代数理论 §5.3](algebra-theory.md#53-参照与共同有效样本) 的
`(n, Σx, Σy, Σx², Σy², Σxy)` 说明共同配对域上 Pearson 的信息充分性；
它不是浮点执行算法，也不授权用大数原始平方和直接相减。
来源下推仍需证明实际算法的数值行为，不能只凭摘要形式相同取得资格。

此保证针对 Frame 实际接收的数值，不恢复来源此前已经发生的精度损失。
键始终保留其既有精确类型，不进入相关数值转换。

## 6. 结果与保证边界

correlate 输出固定的两个命名值；组合层每个 Statistic 仍只输出调用方命名的一列。
两层结果与保存恢复直接遵循
[AnalysisFrame 生命周期](query-result-reuse.md#32-生命周期入口)；
不为相关统计增加隐藏样本、排名或可合并摘要。

可以继续筛选、对齐或计算这些结果列，但相关系数和配对数不是合并总体相关系数的
充分信息。对各组系数求 mean 只是“系数的均值”；按 n_pairs 加权也不能被解释成
原始样本合并后的相关性。需要总体相关时，从仍持有的原样本 Frame 重新规约，
或显式重建来源样本；不会根据汇总结果恢复原 Metric 贡献。

| 范围 | Marivo 保证 |
| --- | --- |
| 两层入口 | correlate 固定返回配套的系数/计数；组合层保持单项单列，两层共用统计语义 |
| 样本与配对 | 当前完整坐标行等权；精确输入绑定、完整键对齐及指定组内计算 |
| 缺失与零 | 共同非 NA 掩码、保留零及准确配对计数 |
| 算法 | 显式 Pearson/Spearman、固定平均秩与第 5.2 节数值路径 |
| 数量与解释 | n_pairs 仅为配对行数，不是独立样本量；系数仅描述当前样本，不证明因果或跨粒度关系 |
| 时间与证据 | 保留既有时间坐标及审计事实，不自动证明时序推断条件或产生显著性/因果 Evidence |

业务判断、调查流程和外部计算交接由
[marivo-analysis skill](../../../marivo/skills/marivo-analysis/SKILL.md) 拥有，本文不重复定义。

## 7. 实施与公开披露边界

通用实施、错误与披露要求遵循 [AGENTS.md](../../../AGENTS.md) 和
[AnalysisFrame 架构](query-result-reuse.md#7-实现架构保留执行基础拆清职责)；
这里只列新增入口与统计必须处理的差异：

- AnalysisFrame 增加 correlate，组合两项基础统计并组装第 2.1 节的固定结果；
  Column 增加两个方法，继续复用未完成统计封装；reduce 的公开签名不变。
  高级入口复用既有编译、调度和生命周期，不增加执行器、缓存或结果子类型。
- 私有定义图增加闭合的双输入统计变体，两个操作数均为必需字段；
  依赖遍历、类型准备、必要检查与本地求值同时消费两端，pending 与完整 schema
  按第 3.2 节显式推导输出，不沿用单输入统计的单位/可空性捷径。
- 旧的单输入 reduce 下推或合并规约规则不能误收新统计，首版使用现有本地执行路径。
- 导出集合不变；pending show 只展示捕获的方法、有序操作数、分组和输出事实，
  不计算样本量或系数。复用既有展示结构，不增加解释性结论。

### 7.1 Help 引导

新增三个独立可达的规范路径，CLI/Python 使用同一注册与合同：

| Help 路径 | 拥有的内容与导航 |
| --- | --- |
| analysis.analysis_frame.correlate | 常规入口；签名、固定 coefficient/n_pairs 及类型/单位、分组/空域/NA、同输入约束与最小例子；需要自定义统计组合时转到 reduce |
| analysis.column.corr | 只返回系数的未完成统计，须交由 reduce 消费；声明方法及数值合同；需要常规配套结果时指向 correlate |
| analysis.column.pair_count | 可独立规约的有效配对计数及自配对非 NA 计数；常量系数为 NA 时仍保留 n；使用 correlate 已包含计数 |

AnalysisFrame 类型 Help 的相关分析导航优先指向 correlate，让常规用法不必先理解
_Statistic 或 reduce。Column 类型 Help 保留两个组合方法的发现路径；
reduce Help 以自定义命名、多变量对/多方法或混合其它统计的例子说明组合能力，
明确不会从输出名推断配对关系，也不会把一个 corr 展开成多列。
三者是完整结果与独立统计的不同层次，不新增 Frame.corr、Session 入口或其它别名。

各相关页面披露同一输入、Duration 拒绝与 repair、自配对、单位、NA 及非推断边界，
通过聚焦链接复用说明，避免重复完整流程。业务方法选择与调查判断仍由 packaged
skill 拥有；本设计只定义静态 API 事实、示例和导航，不修改 skill。

## 8. 验收标准

下列 C01–C11 是验收义务；实际通过记录由实施计划拥有。使用少量有判别力的共享样例，
复用既有测试夹具；不扩成所有类型、载体、分组、后端的笛卡尔矩阵。

| ID | 范围 | 必须满足的判定 |
| --- | --- | --- |
| C01 | 公开表达与精确绑定 | correlate/corr 的 method 必填且仅两个值；拒绝错误参数/操作数；双端及 by 与 correlate/reduce 的 Frame/Session 绑定检查完整；构造与 pending show 无业务读取，correlate 不隐式 compute/persist |
| C02 | 配对、自配对与对称性 | 不对称 NA 的集合与 n 一致；不同变量对独立；自配对等于非 NA 计数；交换端点保值但捕获/展示/指纹保序；同次 sum/mean/row_count 的掩码不变 |
| C03 | 分组域与顺序 | 保留零配对组、Null 类别及完整复合键；按当前行确定组，沿用 reduce 的完整分组键规范次序；输出仅保留指定轴且移除 presence |
| C04 | Spearman 顺序与并列 | 先分组/选配对，再用平均秩；用有排名间隙及组间交错的样例排除全局预排名；重复数值不去重 |
| C05 | 退化与空域 | 空标量、空分组、n=0/1、常量端及 n=2 按第 5 节返回；n 不因常量而清零 |
| C06 | 类型与数值路径 | 数值混合类型和不同单位合法；已知/延迟解析的 Duration 均拒绝且 repair 准确；pending/完整 schema 的 kind、dtype、nullable、unit 符合第 3.2 节；第 8.1 节大数、相邻浮点和尺度见证通过 |
| C07 | 错误与优化边界 | 按第 5.2 节有限值与零尺度判定拒绝数值失败，clip 不掩盖 Infinity/NaN；严格子式错误不被掩码隐藏；检查既有 Top-0/剪列规则及未资格化的下推/合并 |
| C08 | 对齐与 presence | 按明确完整键先 align 后计算；缺端点 NA 被排除，零保留；presence 不隐式改变配对集合，显式筛选按新输入域生效 |
| C09 | Help 与结果披露 | 三个新路径独立可达；AnalysisFrame 优先导航 correlate，Column/reduce 保留组合路径及互链；常规与组合例子分别符合第 2 节；CLI/Python 合同一致，固定输出、自配对及非推断边界有说明；无新增公共类型/别名 |
| C10 | 普通 Frame 回归 | 现有 sum/mean/row_count 的值、组域、顺序、绑定与生命周期不变；以现有保存/冷读夹具覆盖新增 float64/int64/NA 列，不增加载体矩阵或隐藏样本状态；分组统计不取得总体相关资格；文档同步遵循既有披露要求 |
| C11 | 两层等价、固定结果与共享求值 | 对同一固定输入分别执行 correlate 与第 2.3 节组合，复用两种方法及 C02–C08 的代表样例；schema、键/行序、计数/NA/presence 精确一致，系数满足独立 oracle 与规定容差，必要错误在两层均触发；correlate 的 pending/完整 output_names 均严格为 ("coefficient", "n_pairs")，类型按第 3.2 节且无额外值列；返回普通惰性 Frame，可继续 select/where 及既有生命周期；补充下述 pending 输入见证，确认系数与计数消费同一次输入求值 |

C11 只补一个单来源、无分组的 Pearson pending 输入见证，复用现有
analysis_dsl_case_factory、monkeypatch 与
[capture_source](../../../tests/support/source_trace.py)；
共享读取的既有用法见
[test_shared_reference_source_reexecution_and_fixed_inputs](../../../tests/analysis/numeric/test_analysis_references.py)。
不预先 compute 输入；在此单例中让目标业务读取首次返回三对 `(1, 1)、(2, 2)、(3, 3)`，
若重复读取则返回两对 `(1, 3)、(2, 1)`，每对均具有不同的完整样本键。
构造 correlate 与 pending show 均不得触发业务读取；对返回 Frame 调用一次 compute，
必须只读取该来源一次，并得到 coefficient=1（按第 8.1 节容差）、n_pairs=3。
读取次数与结果同时断言，排除把第一次的系数和第二次的计数拼成 `(1, 2)` 的实现；
schema 准备不计作业务读取。本见证属于 C11，不新增验收编号，也不扩展为方法、
分组、载体或后端矩阵。

### 8.1 最小数值见证

`NA` 表示已有缺失值状态。每行有不同的完整样本键；表中只省略这些键。
以下三组均作为同一输入 Frame 的分组样本：

| group | x | y |
| --- | --- | --- |
| A | 1 | 1 |
| A | 2 | NA |
| A | 3 | 3 |
| A | NA | 4 |
| B | 7 | 10 |
| B | 7 | 20 |
| C | 1 | NA |
| C | NA | 4 |

| group | Pearson | Spearman | pair_count | row_count | mean(x) | mean(y) |
| --- | --- | --- | --- | --- | --- | --- |
| A | 1 | 1 | 2 | 4 | 2 | 8/3 |
| B | NA | NA | 2 | 2 | 7 | 15 |
| C | NA | NA | 0 | 2 | 1 | 4 |

同一输入调用 correlate，method 为 pearson 或 spearman 时，均应得到以下固定结果；
上表的其它统计仍由组合层请求：

| group | coefficient | n_pairs |
| --- | --- | --- |
| A | 1 | 2 |
| B | NA | 2 |
| C | NA | 0 |

第 1 个补充见证用于区分两种 method 的 coefficient，n_pairs 均为 3；
使用第 2.3 节的显式组合应得到相同结果。

补充四类小见证以区分算法和精度边界：

1. x=`[1, 2, 3, 4]`，y=`[1, NA, 3, 2]`：
   有效配对 x=`[1, 3, 4]`，y=`[1, 3, 2]`，n=3；
   Pearson=`3/sqrt(21)`，Spearman=`1/2`。先对 x 全列排名会产生错误结果。
2. x=`[10, 10, 30]`，y=`[1, 2, 3]`：
   x 的平均秩为 `[1.5, 1.5, 3]`，Spearman=`sqrt(3)/2`，n=3。
3. x 为 `2**53` 起的三个连续整数，y=`[1, 2, 3]`；
   另用 Decimal 的 `1000000000000000000000000.00`、末尾 `.01`、`.02` 替换 x。
   两种输入均应得到 n=3、Pearson=1、Spearman=1，不能在转浮点时丢失区别。
4. 对以下三种 x，分别取 y=`[1, 2, 3]`、`[1, -1, 0]`、`[1, 2, 3]`：
   x=`[1.0, 1.0 + 2**-52, 1.0 + 2**-51]`、
   `[1e308, -1e308, 0.0]`、
   `[0.0, float.fromhex("0x0.0000000000001p-1022"), float.fromhex("0x0.0000000000002p-1022")]`。
   各例均为 n=3、Pearson=1、Spearman=1；不得将相邻浮点误判为常量，
   或因未缩放的差值溢出/平方下溢产生 NA。
   另取 x=`[1.3, 1.3 + 2**-52, 1.3 + 3 * 2**-52]`、y=`[1, 2, 3]`，
   期望 n=3、Pearson=`sqrt(27/28)`、Spearman=1；此例区分二进制缩放与
   先除以原值最大绝对值而扭曲相对差异的实现。

键、顺序、掩码、NA、计数和类型应精确比较。上述小规模浮点见证采用绝对误差
不超过 1e-12 的独立手算/高精度 oracle；该验收容差不是任意数据的全局误差保证。
组间交错排名、错误边界和冷恢复使用对应专项见证，不以同一生产内核自证正确。

### 8.2 验收证据层级

验证与证据分层遵循 [AGENTS.md 的 Python and Validation](../../../AGENTS.md#python-and-validation)。
独立数值见证与两层等价比较用于实现验收；实际命令、结果和候选源码由实施计划记录，
设计检查不计为实现验收。
