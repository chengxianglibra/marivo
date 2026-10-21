# AnalysisFrame 数据与运算结构

AnalysisFrame 是不可变分析数据对象，在待执行表达式、完整内存和精确持久引用上使用
同一数据合同。结构决定可表达的运算，载体决定如何取得数据。来源入口与固定数据的边界由
[Population 与观察](populations-and-observation.md)拥有；求值、发布、恢复和读取由
[Session 与 Runtime](session-state-and-runtime.md)拥有。

## 数据结构与载体

```text
AnalysisFrame
  owner + exact input binding
  structure: complete ordered axes / named values / independent presence
  required frozen coordinates and comparison descriptors
  backing:
    expression: explicit source/literal/fixed inputs and operations
    memory: complete immutable data and audit
    stored: exact committed reference and bounded descriptor
```

observe/read/values/frame/attribute、align 和 compare 均返回同一种 AnalysisFrame。
零轴 Frame 表示标量域；不通过公共子类区分计算阶段、比较、单位或日历形状。
物化根不保存生产图、原语义对象、来源连接、可执行 origin 或隐藏聚合组件。

| 载体 | compute | persist | artifact | to_pandas |
| --- | --- | --- | --- | --- |
| 表达式 | 求值并返回完整内存根 | 完整求值后发布 | 要求先 persist | 要求先 compute/persist |
| 内存 | 返回自身 | 发布已有完成结果 | 要求先 persist | 隔离副本 |
| 持久引用 | 完整验证并加载内存根 | 验证提交元数据后返回自身 | 同一提交的审计视图 | 验证并导出当前数据 |

只有使用返回的完成根才固定值与键；重用待执行来源表达式可看到变化后的数据。
派生运算保持惰性，其 artifact 不返回祖先产物，导出也不自动执行。
载体转换保留结果身份/求值审计；多次发布一个内存根可以产生不同提交。

## 值、轴与句柄

Column 表示某个精确 Frame 输入上的值表达式。frame[name] 按完整值或 presence 名查找；
frame.axes / axis(name) 返回完整轴句柄，coord(axis) 只将单物理分量轴作为标量列。
复合身份始终是一个完整轴，可用于 by/on，不能隐式拆成数值。axis(name) 仅在当前目录中
精确查找；缺失或重名列出实际候选，不任意选首项。

FrameAxis/Column/由列构造的统计量绑定精确输入和 Session。相同名称、布局、值或指纹
不能重绑句柄。载体转换后从返回对象重新取句柄；两次恢复同一存储根具有相同稳定输入身份。
mv.row_count() 是输入无关的未完成请求，消费时绑定；其它统计从所属 Column 获取。

Frame 轴保留完整物理键类型、identity/field/time 角色、显示名和时间解释，不保留原
Entity/Field 名义绑定。identity 角色与显示路径不能证明业务身份。显式 on 或
population_from 的目标由调用方声明；系统仍检查结构、单位、时间和所有权约束。

## 名称与 presence

用户在 observe/read/values/select/reduce 中声明的量名是单个非空名称段，不含点号、
反斜线或控制字符，也不得使用 left_present/right_present。
非法名在构造时给出重命名修复，不自动转义或静默改名。
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

## 数据操作边界

where/select/global top、已有完整轴 reduce/correlate、分区 over/Column.share、全轴 align/compare 在各载体上使用
同一合同。结构/参数在构造时检查；实际类型、唯一性、数值与映射在消费前验证。必要检查
不能被 Top-0、后续过滤或投影取消。按已有轴规约只统计当前行，不是日历粗化或原 Metric 上卷。
相关、算术、null/empty 和物理精度由[算子合同](operators-and-frames.md)拥有。

来源粒度、观察期间、新属性、原整体和原贡献份额要求显式来源输入。Frame 没有
原 Metric rollup/drill、分区 Top、任意粗 Frame 自动广播、任意关系对应或配对轴再次对齐接收者。
相容完整域的预算参照可显式 align 后做列算术；它不自动证明贡献包含或业务份额。

完整不可执行 method_facts 解释原 Attribute 结果；筛选和规约不改其原分母或认证新分解。
它与当前值表、Artifact 审计分别读取，详见[方法与审计事实](evidence-access-surface.md)。

## 分区归约、广播与当前值份额

`frame.over(statistic, by=())` 接收同输入的 sum/mean/corr/pair_count 或未绑定的
mv.row_count()。by 是当前完整轴的无重复 tuple，按输入轴序规范化；空 tuple 表示整个
当前输入一个分区，frame.axes 表示每个完整行键一个分区。各分区统计映回原行，返回
与接收者同精确输入的 Column，行数与行序不变，可直接用于 select/where/top 或算术。
空输入仍为空，不沿用 reduce 零轴空输入生成一行的规则。

普通、interval、calendar 和 paired 轴均按当前冻结完整键分区；不拆复合身份、不重匹配
日历或更改原来源窗口。统计量沿用 reduce 的类型、NA、样本与单位规则；row_count 请求
消费时绑定且不被修改。允许同输入合法嵌套，内层广播值在外层仍按实际出现次数统计。
where/top 先计算其操作输入上的完整分区，再筛选或排序；下游选择不反向缩小分母。

`column.share(along=axes, zero_denominator="null")` 将 along 指定的轴参与加总，其余轴
构成分区，等价于当前 Column 除以相应 over(sum)。along 必须是同输入完整轴的非空、
无重复 tuple；不改变行粒度，零轴 Frame 拒绝。需要先去掉其它轴时显式 reduce，再从
返回 Frame 重新获取句柄。份额是有符号比例，不乘 100，也不保证在 [0,1]。
零分母使用本节点的 null/error 政策，未知单位保持 None；合法 Duration 组合沿用
sum/divide 合同。筛选前后的位置有意义：先筛选改变分母，后筛选保留已有分母。
两者不重读来源、不生成新 method_facts，不认证原 Metric 的可加性或来源贡献。

## Correspondence and comparison

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

### Interval

TimeScope 在 by 中产生 interval 轴，保存精确 start/end 与边界时区，不自动变成零轴。
等值对应识别精确边界和时区。ordinal 要求每端完整声明目录恰有一条记录且边界时区相同，
不同期间可明确配对；过滤成一行不会把多区间目录改成单区间目录。
interval/calendar/零轴不互转或广播；time×普通分类仍须完整显式 on。
compare 保留当前 interval 及命名基期说明；align 的双端并集/paired 语义继续存在。
Store 16 / marivo.frame_output/v5 保存目录、实际键引用与关系，冷恢复不重建 TimeScope。

### Calendar matching

匹配规则与 shift 支持范围由[时间语义](../temporal-semantics.md#period-shifts-and-fixed-data-matching)拥有。
显式规则、冻结 direct relation 与当前锚点必须一致，不能按别名或日期标签猜测。

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

## 闭合坐标目录

物化输出包含确定的值 schema、完整键分组、行数据、独立 presence 和必要附件。
内部 DataSchema/DataAxis/DataProjection 使用闭合编码变体，不另建公开坐标继承树。

| 变体 | 必需数据 |
| --- | --- |
| 普通坐标 | 局部 token、identity/field 角色、显示名、有序完整键分量及类型/nullability、已捕获时间锚点等不可执行解释 |
| 日历坐标 | 局部 token、桶键类型、timezone、grain/快照标识、[时间语义](../temporal-semantics.md)规则版本及完整名义域、桶键到名义/实际边界的有限映射 |
| 区间坐标 | 局部 token、完整区间键、冻结 start/end、边界时区与有限区间目录 |
| 配对坐标 | 局部 token、完整配对键布局、左右同种日历或区间轴描述及其有限键映射；每侧键可缺省，但不能同时缺省 |

角色、名称、时间锚点和日历标识解释数据，不是可解析的语义 Ref 或模型加载指令。
配对轴仅引用左右两个同种时间轴描述，引用须闭合且路径唯一；不允许递归配对轴。
配对键及其桶映射由[时间语义](../temporal-semantics.md)的名义对应决定，不因某个地区等完整行键缺侧而改变。
桶键缺省只表示冻结目录中没有对应桶，不表示某个完整结果行缺失；这里不另存坐标 presence。
left_present/right_present 及继承路径是单独的非 nullable boolean 行列，不能从桶映射推断。
复合身份始终保持一个完整轴；桶投影按配对轴键定位，不要求整行端点存在，值 NA 不替代行 presence。
日历按保存桶键定位，不依赖当前 CalendarAxis、来源行号或当前日历规则。
元数据只保留小型描述及附件引用，不能为打开句柄而解码全部坐标值。

配对坐标的数据投影只用于解释 align 的完整行键，不附带原输入值。
compare 使用同一 Frame 保存协议，并保存有限命名值绑定、端点目录、已消费映射与 presence 说明；
不靠列名前缀或 pandas attrs 恢复它们。
零分母政策在构造时捕获并在本次求值完成，不进入后续运算资格或专用结果布局。

## 结构传播与完整性

| 变换 | 必须保留或更新的结构 |
| --- | --- |
| 载体转换/冷恢复 | 完整数据结构、坐标目录、数据附件与行序 |
| align | 保留左右值和 left/right presence；显式时间对应可生成 paired 轴 |
| compare | 保留本期轴与值，按别名添加值组与端点 presence；已有说明按精确路径与时域运输，缺外层行时继承 presence 为 false |
| where/top | 当前行域、值和所有行级 presence 同步筛选；配对桶映射可按保留轴键裁剪 |
| select | 保留完整坐标、所有行级 presence 及必要映射；直接 Column 可复制/重命名比较值绑定，表达式不获得认证；无直接值时保留有限 presence-only 说明 |
| over/Column.share | 返回同输入 Column，保持完整键域及行序；经 select 生成的计算值不获得比较列认证，presence 仍由 select 保留 |
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
所有公开结构必须可完整保存；普通、复合、区间、日历及单层配对轴都属于 codec 验收范围。

## 结果行序

AnalysisFrame 的计算逻辑决定结果行序；物化、存储、恢复、show 和 to_pandas 原样保持。
不定义独立展示顺序、排列向量或顺序附件，不在持久边界额外按键重排。
轴的顺序和键分量顺序属于 schema，不等于结果行排序。

- where 保留被选中行的相对顺序；select 保持当前行序。
- over/Column.share 返回同输入 Column，不重排原行；by/along 参数顺序不改变行序。
- top 保留 descending: bool = True；升降序均 NA 最后，完整键按既有规范次序裁决并列，不能用局部 token 或随机 ID 排名。
- reduce 的输出按完整分组键的既有规范次序产生；这是算子输出合同，存储不再排序。
- exact align/compare 保持左侧/当前输入行序；outer 先按该行序输出匹配/独有键，再按右侧/基准行序追加独有键；日历映射不改此行序合同。
- 文件分片与批次读取按写入序列拼接，不以文件枚举或并发完成顺序替代。

索引或唯一性检查不得改变主数据行序；按键定位的附件不承担显示排列。
行位置不是业务身份，不能用于自动匹配两个 frame。
未规定确定排序的来源计算只固定本次序列，不承诺独立重新求值仍产生同一行序。

来源下推只有在最外层排序、NA 与并列键语义可保持时才承担有序输出；否则保留本地阶段。
执行内临时位置不进入保存目录。分片读取按写入序列拼接，附件不承担另一套展示顺序。

## Implementation map

[frame.py](../../../marivo/analysis/dsl/frame.py)、[column.py](../../../marivo/analysis/dsl/column.py)
和 [frame_axis.py](../../../marivo/analysis/dsl/frame_axis.py) 拥有公共构造与精确输入校验。
[frame_data.py](../../../marivo/analysis/core/frame_data.py) 拥有闭合载体；
[frame_structure.py](../../../marivo/analysis/core/frame_structure.py) 和
[data_schema.py](../../../marivo/analysis/core/data_schema.py) 拥有结构推导/完整性。
[frame_comparison.py](../../../marivo/analysis/methods/frame_comparison.py) 拥有组合数值与行序。

Numeric operations bind their result types before execution. Integer Column SUM
returns Decimal(38,0), Decimal SUM widens to Decimal(38,input_scale), and mean keeps
its declared result type. [Numeric carrier rules](operators-and-frames.md) own
Decimal convergence, float approximation and source/local overflow differences.
Persistence and cold reads preserve the committed values and types without new rounding.
