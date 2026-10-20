# Period Comparison：统一 compare 与期间变换

Status: implemented，2026-10-21；S0–S5 已完成实现、阶段审查与本地统一验收，实际证据及验收范围见实施记录。

本文拥有统一期间比较的增量合同。当前已实现合同同时由
[AnalysisFrame](query-result-reuse.md)、
[Python Analysis Design](python-analysis-design.md)、
[Timezones and Calendars](timezone-and-calendar-design.md) 和
[Operators and Frames](operators-and-frames.md) 拥有。
本文列出的变化与现行 owning 文档一起切换。阶段、代码范围、依赖、旧接口清理、
实际审查和验收证据见[实施计划](period-comparison-implementation-plan.md)；
实施记录分别列明本地、冷恢复、Runtime 和发布验收的范围。

## 1. 目标、职责与范围

用一条组合路线表达周同比、周/月/年同进度比较，以及同表的多个基期：

```text
explicit current calendar domain
    -> periods.shift(mv.offset(...)): explicit baseline domain + frozen direct map
    -> session.observe(...): evaluate supplied Metrics independently at each endpoint
    -> current.compare({"yoy": baseline, "mom": another}): one ordinary Frame
    -> select / where / top / reduce / eligible align / compute / persist
```

| 公开入口 | 唯一职责 |
| --- | --- |
| `mv.offset(years=...)` / `mv.offset(periods=...)` | 构造不绑定时间轴的期间对应规则 |
| `CalendarAxis.shift(rule)` | 生成精确基期时间域，冻结直接对应；不计算 Metric |
| `session.observe(metrics, by=...)` | 在显式时间域上求原 Metric；不内置比较参数 |
| `AnalysisFrame.compare(baselines, ...)` | 比较显式 Frame，检查完整键、值和缺失，生成统一列 |
| `AnalysisFrame.align(other, ...)` | 保留一般双端对应和时间并集；不承担差值计算 |

不增加 `observe(compare=...)`、`Session.compare`、ComparisonFrame、结果回源或
按列名恢复来源的入口。单基期与多基期使用同一个命名映射、输出布局和生命周期。
pending、memory、stored Frame 都是显式数据输入；完成结果不获得原 Metric 或生产图。

新增 shift 首版支持内建 week/month/year、单位步长，以及完整桶或从桶首开始的前缀。
不扩大到命名财务日历、工作日进度、任意中段窗口、任意关系映射或子日偏移。
既有完整 day/quarter 的数据侧对应能力按 §3 保留；不将新来源构造的范围与旧数据能力混淆。

## 2. 统一用法

下面假定 `s` 已创建且时区为 Asia/Shanghai，Metric 均已解析。观察区间为半开区间。
本例 1–9 月为完整月，10 月为截至 20 日 12 点的月内前缀。

```python
import marivo.analysis as mv

periods = s.time_scope(
    start="2026-01-01",
    end="2026-10-20T12:00:00+08:00",
).by(mv.grain("month"))

metrics = {
    "revenue": revenue,
    "aov": revenue / order_count,
}
current = s.observe(metrics, by=(periods,))
yoy = s.observe(metrics, by=(periods.shift(mv.offset(years=-1)),))
mom = s.observe(metrics, by=(periods.shift(mv.offset(periods=-1)),))

report = current.compare({"yoy": yoy, "mom": mom}, how="outer")
fixed = report.compute()
table = fixed.select({
    "revenue": fixed["revenue"],
    "yoy": fixed["yoy.relative.revenue"],
    "mom": fixed["mom.relative.revenue"],
}).compute()
```

单基期也是 `current.compare({"yoy": yoy})`。名称只拥有输出命名，不推断业务规则；
把 `"yoy"` 换成 `"reference"` 不改变求值。周/月/年只改变本期域和 Grain，
不新增周比较、月比较、年比较算子。周范围必须从周一起点开始；
用自然年 1 月 1 日裁出半个首周不自动取得同进度资格。

构造这些表达式不读取业务数据；一次最终 compute 调度所有显式依赖。
可以共享合格的读取和子计算，但同图不自动证明跨源事务快照。
重用 pending 表达式仍可重读变化的来源，须使用 compute 返回值才固定数据。

## 3. offset：期间规则

复用 `CalendarCorrespondence` 的闭合规则家族，不新增平行规则类型。
签名通过两个互斥调用形状表达，不要求用户传 unit 字符串或占位参数：

```python
from typing import overload

@overload
def offset(*, years: int) -> CalendarCorrespondence: ...

@overload
def offset(*, periods: int) -> CalendarCorrespondence: ...
```

- 必须且只能指定一个参数；只接受非 bool 整数，支持负数、零和正数。
- `periods=n` 按接收时间轴的名义粒度偏移 n 期，不按结果行位置移动。
- `years=n` 按相应年份中的同名期间对应。内建周使用 ISO week-year；
  其它支持粒度使用民用年，不按 365 天或固定 52 周偏移。
- `offset(...=0)` 是显式恒等对应；通过相同粒度/前缀准入后，直接复制已确定的
  名义/实际 UTC 与民用边界，不重新解析当地钟点。仍不绕过 ownership、完整键或数值检查。
- 名称 `yoy`/`mom` 不是规则；offset 不计算增长率。

| 粒度 | `offset(periods=-1)` | `offset(years=-1)` |
| --- | --- | --- |
| week | 上一个自然周 | 上一 ISO 周年的相同 ISO 周号 |
| month | 上个月 | 去年同月 |
| year | 上一年 | 上一年 |

完整 day/month/quarter/year 的 `match` 保留原有民用对应范围；
完整 week 新增 ISO 年对应。完整 day 的 years 偏移仍让闰日无对应，
quarter 的 years 偏移保留季度号。shift 的来源侧准入仍限 week/month/year。
`ordinal()` 继续用于显式双端的已有有限域，不能交给 shift 生成基期。

当前候选已用 offset 替代并移除 `previous_year()` 和 `period_offset()`，不保留公开别名。
align 的显式 match 采用同一规则家族；旧名称仅保留在退役说明、历史记录和拒绝测试中。

## 4. shift：有限时间域与同进度

```python
def shift(self: CalendarAxis, rule: CalendarCorrespondence) -> CalendarAxis: ...
```

### 4.1 名义桶、实际区间与直接映射

对每个选定本期桶 t，保留名义区间 N(t) 与实际观察区间 A(t)。
shift 先根据 offset 找到目标名义桶，再映射实际区间，返回不可变的同家族时间域。
输入不改变；构造只使用已冻结时间权威，不读取业务行。
合法输入须满足 A(t)=N(t)，或 A(t)=[N(t).start,e) 且 start<e<end。
中段截取、改变时区、不同 Grain、重复或重叠目标桶不静默修复。

结果保存选定目标桶及直接本期→目标关系。每个本期桶的目标是：

1. 唯一目标桶和精确实际区间；或
2. 明确无对应，区分目标期间不存在与目标民用截止日期不存在。

这是一份有限、不可执行的数据关系，不是捕获原 CalendarAxis 对象的回调。
它保留本期端完整目录及目标端说明，以供结果解释和校验。
结构损坏、精度不足、年份超出可表示范围、所需日历权威缺失或目标时刻不唯一等非法情形
都是错误，不能包装成合法无对应；合法无对应不构造假桶。

### 4.2 同进度的固定含义

| 本期状态 | 目标范围 |
| --- | --- |
| 完整周/月/年 | 完整目标周/月/年 |
| 周内前缀 | 目标周起点至相同星期、当地钟点 |
| 月内前缀 | 目标月起点至相同日号、当地钟点 |
| 年内前缀 | 目标年起点至相同月日、当地钟点 |

期间起点、完整期结束是独立的边界角色。完整 2024-02 对应完整 2023-02，
允许 29 天与 28 天；不能把完整期变成等天数截断。前缀只映射精确民用截止边界，
不引入 `partial`、`progress` 或“自动取较短期间”开关。

所有基期比较共用同一个本期实际范围。本期截至 3 月 30 日中午，月环比目标
2 月没有同日同刻时，基期无对应；不把本期偷偷缩短到 3 月 28 日。
YTD 截至 2024-03-01 映射到 2023-03-01，不按年内天序落到 3 月 2 日。
`2024-02-29T00:00` 虽表示不包含 2 月 29 日，但其同名截止边界在 2023 年不存在。
“相同已完成天数”是另一问题，调用方使用显式 TimeScope 与既有 interval 对应表达。

时区来自原域；非零偏移的目标当地钟点转换为 UTC 时，DST gap/fold 仍按既有时间合同拒绝歧义，
要求显式确定两端区间。不同期间不保证相同 elapsed seconds。
零偏移保留原始精确 instant，包括已用 aware 时间明确选定的 fold 分支；
例如 New York 的 `2026-11-01T01:30-04:00` 仍为 `05:30Z`，不能因重新解析 `01:30` 而失败。
目标名义和实际 UTC 边界、规范化民用边界及其角色在构造时冻结，
固定数据比较和冷恢复不使用当前时区库重新求边界。

### 4.3 ISO 周同比、稀疏域与空域

内建周从周一开始。`2021-W01` 的上一年同周是 `2020-W01`；
提前 52 周会落到 `2020-W02`，二者不能互换。
若目标 ISO 年没有相同 W53，则该本期桶无对应。需要固定前 52 周时明确使用
`offset(periods=-52)`。

shift 结果允许稀疏或空的选定时间域：

- `{2021-W52, 2022-W01}` 向前一年得到 `{2020-W52, 2021-W01}`，
  中间的 `2020-W53` 不是选定输出桶。
- 单独 `2020-W53` 向前一年没有目标，得到保留 Grain、时区、空目录和无对应映射的空域。
- 不用包围目标的连续 TimeScope 代替精确选定域，不补中间桶，也不制造空 interval 或零轴行。

这是对 CalendarAxis 连续非空构造能力的明确扩展。普通 time_scope.by 的构造规则不变；
shift 的输出选择与日历本身的名义顺序分开保存。原始名义标签/序号不因稀疏选择重新编号。
`trailing_periods` 等来源窗口按完整名义日历找前驱，不按选定目标列表倒数。
输出未选择的期间仍可成为某个 Metric 窗口的显式贡献依赖。
`trailing_periods` 既有的完整桶准入继续成立，shift 的前缀输出不会使该窗口获得部分桶资格；
空时间域没有求值端点，不能退回单个 scalar endpoint 或制造来源聚合。

允许对 shifted 域继续 shift，但只保存相对于直接输入域的新映射，不累加 offset、
不传递可执行链、不推断祖先对应。中途丢失的桶不会复活；
要比较原始域与 n 年前域，应直接从原始域使用 `offset(years=-n)`。

## 5. observe：每端完整求值

observe 的公开职责与签名不增加 compare 参数。它接收原域或 shifted CalendarAxis，
只在实际选定时间键上生成输出，空时间域产生保留类型和时间说明的零行 Frame。
同一份 metrics 映射可在各端复用；这表示使用同一已解析定义，不代表读取历史定义版本。

每端独立按自己的 A(t) 和 Metric 时间角色求值。有 `Metric.window(...)` 时，窗口 W(t)
继续拥有贡献范围，shift 不隐式覆盖窗口、不把 all_history 截断成期间前缀。
比率重新求区间内分子/分母，distinct、均值和分位数使用原贡献及原策略；
不裁切完成月值、不平均日客单价、不相加每日去重数。

where、Metric 内在条件、固定属性时点和贡献选择原样传递，不自动移动硬日期过滤，
不按每个基期重新挑选 Top 或活跃人群。指定范围不证明来源数据已经完整到达截止点。
来源空活动按 Metric 的 empty/null 规则处理，不等于时间对应不存在。

时间关系跟随对应日历轴进入 Frame 的冻结说明，不证明其它轴或指标的身份。
已解析公式、连接与计算图只属于 pending 求值；完成结果只保留值、坐标、presence、
冻结时间关系与审计。

## 6. compare：唯一比较入口

```python
def compare(
    self: AnalysisFrame,
    baselines: Mapping[str, AnalysisFrame],
    *,
    on: Mapping[str, tuple[tuple[FrameAxis, FrameAxis], ...]] | None = None,
    match: Mapping[str, CalendarCorrespondence] | None = None,
    how: Literal["exact", "outer"] = "exact",
    zero_denominator: Literal["null", "error"] = "null",
) -> AnalysisFrame: ...
```

baselines 必须非空，保持映射声明顺序；每个别名是合法单段名称，`current` 为保留别名。
只接受同 Session 的 AnalysisFrame；规则、Metric、Artifact、ID 不充当数据输入。
单基期也是一项映射，不保留单 Frame 参数及 left/right 结果布局的长期兼容分支。
返回惰性普通 Frame；不隐式调用子输入 compute/persist。

### 6.1 轴绑定与期间对应是两层检查

每个基期先检查 ownership、轴数、角色、完整键和无配对轴输入。
零轴或唯一同种 interval/calendar 轴沿用结构唯一推导；
其余形状必须在 on 对应别名中给出覆盖全部轴的完整双射，包括时间轴。
给出部分轴映射后自动补足、按显示名推断普通轴、跨种类转换或自动广播均不允许。
on/match 中未知别名拒绝；每个未显式提供 on 的基期都须单独满足唯一推导条件。

对已确定的唯一时间轴对，按以下规则选择对应：

1. 有显式 `match[name]`：该规则是比较意图。若还存在直接指向本期的冻结关系，
   两者必须一致；冲突拒绝，不选择一个悄悄覆盖另一个。
2. 未给 match，且基期带直接 shift 关系：其本期端必须与接收者的完整冻结时间域相符，
   才消费保存的对应。相符比较 Grain、时区、规则版本、选定键及名义/实际边界；
   不要求数据读数或内存对象相同。锚点不符就拒绝，不转成等值或追溯祖先。
3. 没有直接关系，也未给 match：沿用完整键等值；不从相近日期猜测偏移。

普通轴始终按完整联合键等值；时间映射不免除它们的显式绑定。
where/top/select 保留完整时间目录，过滤后的实际行域另行检查，不能靠删行伪造直接关系。

手动准备的历史 Frame 可以显式表达对应：

```python
report = current.compare(
    {"yoy": saved_baseline},
    match={"yoy": mv.offset(years=-1)},
    how="outer",
)
```

数据侧 offset 只查找保存的目标桶。前缀匹配同样只支持 week/month/year；
部分 day/quarter 明确拒绝，完整 day/quarter 保持 §3 的支持范围。
对合法前缀，必须验证双方保存的进度边界精确相符；
不能裁切一个已经聚合的完整基期来满足本期前缀。相关冻结字段缺失时拒绝并要求重新观察，
不读取当前 Catalog/TZ 补齐。显式 ordinal 保留现有完整日历分区或单 interval 准入，
不把前缀桶当作可任意按位置配对的期间。

带地区的例子仍明确绑定全部轴：

```python
current = s.observe(metrics, by=(periods, region))
yoy = s.observe(metrics, by=(periods.shift(mv.offset(years=-1)), region))
report = current.compare(
    {"yoy": yoy},
    on={"yoy": tuple(zip(current.axes, yoy.axes, strict=True))},
    how="outer",
)
```

这里 zip 的轴顺序由例子中相同的 by 结构明确建立，不是 API 按位置自动匹配。

### 6.2 本期时间域、完整行域与 outer

比较具有方向：self 是本期，基期是参照。返回本期的普通 calendar/interval 轴及其完整目录，
不构造 PairedDataAxis。零轴和普通轴输入仍按各自完整键比较，不获得隐含时间轴。

对每个基期，将其实际完整行键沿已验证的一一时间关系映射到本期坐标，
再与其它非时间坐标组成完整键。记本期实际键为 D0，第 i 个映射后的实际键为 Di：

- exact 要求每个 Di 与 D0 相等；实际本期行缺少任何基期时失败。
- outer 返回 D0 与所有 Di 的并集。先保留本期行序，再按基期声明顺序和各自原行序
  添加尚未出现的键；不按日期或显示名重排。
- 行值是否 NA 不影响键存在性；无实际行的额外目录记录不创建输出行，也不单独使 exact 失败。
- 任一基期实际时间行无法映射进本期声明时间域时，两种 how 均拒绝，
  要求显式筛选该基期或使用 align 查看一般双端时间并集；不得静默丢弃或虚构本期坐标。

outer 保留能映射到本期时间桶的基期独有地区/商品等联合分类，所以消失分类不会被隐藏。
各端先遵循自身 observe 的域发现和时间补齐，再合并实际行；不把分类各列分别取笛卡尔积，
也不提前将并集强制注入各端，以免把原本缺行改写为存在的空值行。
对消失组需要业务零值时，调用方明确共同输出域及 Metric empty 策略；compare 不补零。

显式 Population 的角色保持独立：`where=mv.in_population(P)` 固定贡献选择，不重选、不换期；
`by=P` 固定该端完整输出键，必须由调用方为基期显式构造对应时间×普通联合键域。
shift 只变换其接收时间域，不隐式改写 P。若要保持选定本期域，双方须使用对应的有限域并用 exact，
不能先构造不受限基期再声称 outer 仍严格等于 P。

对于 interval 的 ordinal，基期单区间映射到本期单区间，结果保留本期 interval 与基期说明；
任意双端 interval 并集仍由 align 承担。现有 align 的 paired 结果准入不在本次放宽。

## 7. 输出、presence 与数值

输出的当前值路径原样保留；每个基期按本期值顺序生成 baseline、absolute、relative 三组：

```text
revenue
aov
yoy.baseline.revenue
yoy.baseline.aov
yoy.absolute.revenue
yoy.absolute.aov
yoy.relative.revenue
yoy.relative.aov
mom.baseline.revenue
...
```

每端必须有相同的非空值路径集合，并按路径验证兼容数值类型和单位。
同一路径下可以使用不同显式公式或来源，但不会因同名被认定业务定义相同。
不同量名必须先 select 成相同路径；需要任意量的双端组合时使用 align，不自动猜量名对应。
多基期复用同一当前读数；不为某个基期修改当前范围、公式或分组。

`absolute = current - baseline`，`relative = absolute / abs(baseline)`。
缺行或任一值为 NA 时差值为 NA，之后才处理有效对的零分母。
zero_denominator 只作用于本次 relative；不会覆盖子公式的严格策略。
Duration 的 baseline/absolute 保持原 tick 单位，relative 无量纲；
整数、Decimal、溢出和非有限数仍用现有数值核。

独立、不可空的行级 presence 为 `current.present` 与 `<name>.present`。
继承输入已有 presence 时，分别放入 `current.presence.<path>` 和
`<name>.presence.<path>`，不与新的端点行存在性混合。
output_names 只包含值，presence_names 只包含 presence，均使用已有精确路径取值协议。
生成的值/presence 完整路径若与保留路径冲突，在构造时拒绝，要求先 select 重命名；
不能覆盖旧列或自动加序号。

下表中的值描述通过域检查后的结果。缺期、缺行两类只能出现在 outer 的成功结果中；
exact 遇到不能完整配对的实际键会失败，不先产生 presence=false/NA 的部分结果。
目录中的无对应桶若双方都没有相关实际行，仍按 §6.2 检查实际域。

| 情形 | 冻结期间关系 | 基期行 presence | 数值 |
| --- | --- | --- | --- |
| 目标 ISO 周或截止日期不存在 | 明确无对应 | false | baseline/absolute/relative 为 NA |
| 合法目标范围，但该联合分类没有观察行 | 有对应 | false | 同上 |
| 合法目标、观察行存在但 Metric 值为空 | 有对应 | true | baseline 为 NA，差值为 NA |
| 合法范围内无事实，显式域/标量观察生成行 | 有对应 | true | 按 Metric empty 策略，可为 0 或 NA |
| 基期值为 0 且当前值非空 | 有对应 | true | absolute 正常；relative 为 NA 或按参数报错 |
| 目录、收据或对应损坏 | 非法数据 | 不产生结果 | 完整性错误，不归为合法无对应 |

一个观察行存在不证明它有事实贡献，也不证明来源覆盖完整。
缺失原因在冻结关系和结构化错误中解释，不增加每个值的状态类或 reason 列。

## 8. 冻结数据、后续组合与恢复

时间域构造冻结名义/实际 UTC 边界、民用进度、时区权威及规则版本。
shift 的输入端、精确目标域、直接部分一一映射与无对应原因随对应时间轴保存。
compare 的输出另保存每个命名基期的端点目录、已消费映射及列组绑定。
这些是有类型、可校验的说明附件，不是再次求值的许可或独立公共结果类型。

读取时验证端点键唯一、映射引用完整、精确边界一致和 primary keys 可解析。
数据损坏不能当作缺期。当前候选统一使用 Store 15、`marivo.frame_output/v4`、
`frame.v4` 和 `frame-output-v4`，保存闭合的直接关系、命名比较和端点 presence 说明。
Store14 及更旧数据原样拒绝，不迁移、不双读、不在旧项目初始化旁路；缺失冻结信息不能
用当前时区或模型重建。保留兼容旧环境读取旧数据，或在新项目显式重建来源问题。

| 后续操作 | 合同 |
| --- | --- |
| select / where / top | 保持轴和冻结目录；选择值时维护相应列组说明，数值表达式不冒充新的比较认证 |
| reduce | 沿用当前行统计；丢弃 presence。保留时间轴时可保留其区间说明，但新聚合列不继承原比较列组结论 |
| reduce 删除时间轴 | 删除该轴的逐桶对应；仅审计可保留发生过比较的历史，不声称总量具有一个新的同期范围 |
| 同域等值 align / 再次 compare | 普通轴输入可用；仍验证完整轴、值路径与新规则。保留已有说明的命名绑定，不重新生成旧值 |
| 显式新的时间 match | 只解释当前时间轴及新的显式输入；旧 baseline 列是普通保存值，不自动移动其原来源窗口 |
| compute / persist / 冷恢复 | 使用一套 Frame 生命周期；保存和读取有限数据，不回源、不重新匹配已完成结果、不恢复 Metric |

新 compare 结果的轴不是 paired，因此不触发现有 paired 输入拒绝。
但这不等于任意二次比较都合法：值名/单位、Session、轴对应、目标域与严格检查仍需成立。
对已保存 report 的 baseline 值做 reduce 只是这些行的统计，不是原始总体收入、
总体客单价或去重指标的重算。

## 9. 架构与披露边界

复用当前 compiler、stage scheduler、Ibis/pandas 数值路径、PyArrow 保存与事务。
shift 处理有限时间数据；observe 处理显式来源；compare 组合完整键映射、nullable 算术和输出整形。
不新建执行器、持久缓存、全局轴身份服务，或从完成结果扫描生产图。

当前候选的公开披露使用同一原生 registry，已接入以下入口和说明；S5 的最终审查与
修复后统一验收状态由[实施记录](period-comparison-implementation-plan.md)拥有：

- `mv.offset`、`CalendarAxis.shift`、命名 compare 的公开签名、exports 与 snapshot。
- `analysis.offset`、`analysis.calendar_axis.shift`、`analysis.analysis_frame.compare` 等
  Live Help、CLI、类型导航和错误修复；这些 target 已存在并使用新合同。
- `.show()` 中的捕获范围、稀疏/空域、基期关系及 bounded 预览，和独立 reachability/budget 检查。
- owning specs、API 文档、当前示例及 latest EN/ZH 站点说明。
- packaged skills 未修改；当前搜索未发现必须同步的期间比较工作流冲突。若出现具体调整，
  仍须取得该文件的明确授权。

## 10. 先前合同与已实现候选的切换

| 先前合同 | 已实现候选合同 |
| --- | --- |
| previous_year / period_offset 两个工厂 | offset 的两个互斥参数形状；不保留别名 |
| CalendarAxis 常规连续非空范围 | shift 可生成精确稀疏/空选择，并保存直接关系 |
| 日历对应默认需要显式 match | 只有可验证的直接 shift 关系可省略；无关系仍等值或显式 match |
| 日历 match 一律完整桶 | offset 增加经过实际边界验证的同进度前缀；ordinal 不放宽 |
| compare 接收一个 Frame，输出 left/right/absolute/relative | 接收非空命名 Frame 映射，单/多基期同一布局 |
| 时间 match 的 compare 产生 paired 轴 | compare 使用本期时间轴与命名基期说明 |
| compare outer 可保留无对应的基期时间桶 | 新 compare 拒绝无法落入本期域的实际基期行；一般双端时间并集交给 align |

以上 API/数据合同已在候选中切换：被替代的公开形状和有效消费者使用新入口，
不通过双入口、双读或 renderer 影子清单维持两套规则。实现候选与阶段审查不等同于
S5 最终验收、安装包或发布完成；精确门禁、审查轮次和证据见实施记录。

## 11. 候选实施验收合同

以下是当前候选的最小行为验收合同，优先使用独立日期/数值 oracle，不把冻结重读等同
数值正确。PC01–PC16 的实际执行、阶段审查和未完成门禁由实施记录逐项列明；
S5 最终 R2 和修复后统一验收仍待完成。示例语法或文档检查不替代执行、Runtime 或发布证据。

| ID | 场景 | 必须验证的结果 |
| --- | --- | --- |
| PC01 | 单基期与多基期 | 同一命名入口、路径和当前轴；独立两次比较的相同分子/基期与同表结果一致 |
| PC02 | 周同比与跨 ISO 年 | 2021-W01→2020-W01，明确不同于前 52 周；完整年同比与年环比一致 |
| PC03 | 第 53 周与稀疏/空域 | 不补中间 W53，不造哨兵行；保留全空基期的类型和无对应说明 |
| PC04 | 月/年同进度 | 截至 10 月 20 日中午的 MTD/YTD 两端精确；2024-03-01 YTD→2023-03-01 |
| PC05 | 闰月、月末与截止边界 | 完整 29 天→完整 28 天；不存在的部分截止日期无对应；不缩短当前值 |
| PC06 | DST 与冻结解释 | 非零偏移的 gap/fold 明确拒绝，零偏移保留 aware fold 的精确 instant；不同秒数合法；断开当前 TZ/模型后按保存边界读取 |
| PC07 | 非可加 Metric 与窗口 | AOV、distinct、分位数直接原贡献 oracle；稀疏输出的 trailing_periods 不压缩名义距离 |
| PC08 | 缺期/缺行/NA/零 | 实际缺期/缺行在 exact 下失败、outer 下保留；分别核对映射、presence、数值和零分母政策；Metric 空域策略未被比较覆盖 |
| PC09 | 联合分组与显式域 | 新增/消失类别保留、不造分类笛卡尔积；贡献 P 不重选，双方显式输出域精确 |
| PC10 | exact/outer 和时间边界 | exact 检查实际联合域；outer 顺序确定；域外实际基期时间行拒绝而非丢弃 |
| PC11 | 自动关系与显式 on/match | 直接域相符才推导；多轴完整绑定；错误锚点、冲突规则、跨 Session、paired 输入和部分 day/quarter 拒绝 |
| PC12 | shift 链与输入不可变 | 只接受直接关系；中途缺失不复活；原域不变，禁止按 offset 求和恢复祖先 |
| PC13 | 后续 Frame 运算 | select/where/top/reduce/等值 align 的值、presence 与说明正确传播；不获得来源续算 |
| PC14 | 载体与损坏边界 | pending/memory/stored 相同输入得到相同结果；冷恢复不回源；附件损坏明确失败 |
| PC15 | 数值与严格依赖 | Decimal、负基期、Duration、NA、零、溢出符合既有 compare；后置删列/Top-0 不屏蔽必需错误 |
| PC16 | 披露和退役 | exports、Help、错误、例子、EN/ZH 与新合同一致；旧入口不残留为公开别名 |

## 原设计任务与审查记录（历史）

以下原样保留 2026-10-20 设计任务的发现、修订和当时执行限制，仅描述该历史任务，
不表示当前实现、Help 或验收状态。当前候选合同由上文拥有，实际进度由实施记录拥有。

本轮只整理设计，不修改 Python、测试、Live Help、站点或 packaged skills，不提交。
主代理拥有本文及 analysis README 中的提案入口；审查代理只读。
开始时 README 已有未提交修改，工作区另有 attribute、predicate、observe 等设计文件；
本轮保留这些内容，不把它们作为实现证据。

| 阶段 | 负责人 | 依赖 | 成功标准 | 状态 |
| --- | --- | --- | --- | --- |
| D1 | 主代理 | 现行 owning contracts 与已确认方案 | 明确期间映射、来源求值、单/多基期输出与边界 | complete |
| R1 | 时间语义与 Frame 组合审查代理 | D1 | 独立检查语义缺口，记录 P2+ 问题 | complete，4 项 P2 |
| D2 | 主代理 | R1 | 修订全部 P2+ 问题并保留记录 | complete，R2 已确认 |
| R2 | 原审查代理 | D2 | 最多第二轮确认；剩余 P2+ 阻断收尾 | complete，两位审查者均无剩余 P2+ |
| V1 | 主代理 | 最终文稿 | Markdown 链接、Python 示例语法、diff/范围检查 | complete |

PC01–PC16 是未来实现的要求，不是本轮已执行的 Runtime 测试。

### R1 原始发现与修订

| ID | 级别 / owner | 原始反例与影响 | 修订 |
| --- | --- | --- | --- |
| T1 | P2 / 时间审查 | 零偏移若重新解析 New York 2026-11-01 01:30，会拒绝已明确为 -04:00 的合法 instant | §3/§4.2 明确零偏移精确复制；PC06 保留该反例 |
| T2 | P2 / 时间审查 | 数据侧未限定前缀粒度，季度前缀既无进度定义也无明确拒绝 | §6.1 限定前缀 week/month/year；PC11 拒绝部分 day/quarter |
| C1 | P2 / Frame 审查 | 相同值路径要求与“不同量名可比较”矛盾，revenue/sales 没有定义配对 | §7 要求先 select 统一量名，保留不同显式公式/来源 |
| C2 | P2 / Frame 审查 | 缺失表承诺 NA 结果却未说明默认 exact 应失败，如实际 W53 对空基期 | §7 限定缺期/缺行表项为 outer 成功结果；PC08 同时检查 exact |

附带闭合：§4.1 区分年份越界/权威缺失与合法无对应；
§4.3 保留 trailing_periods 的完整桶前提及空域零端点语义。

### R2 与文档验证

时间审查确认 T1/T2 修复及直接相关边界，无剩余 P2+；
Frame 审查确认 C1/C2 修复及键域/presence/组合规则，无剩余 P2+。
两轮均为只读设计审查，不代表实现、来源执行或 Runtime 资格。

最终文档检查通过：6 个 Python 代码块可被 AST 解析，4 个本地文档链接存在，
README 提案入口唯一，代码围栏完整，PC01–PC16 及 4 项原始发现均保留。
独立 stdlib 日期核验确认 ISO W01/W53、稀疏基期周、闰月长度及民用 YTD 反例。
tracked diff 与新文档的 whitespace 检查通过；本轮只增加本文和 README 中三行提案入口，
保留其余既有工作区内容。没有运行实现测试、Runtime 或 release gate，没有提交。
