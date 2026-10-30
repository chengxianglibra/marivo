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
  optional process-local source declaration and output mapping
  backing:
    expression: explicit source/literal/fixed inputs and operations
    memory: complete immutable data and audit
    stored: exact committed reference and bounded descriptor
```

observe/read/values/frame/attribute、align 和 compare 均返回同一种 AnalysisFrame。
零轴 Frame 表示标量域；不通过公共子类区分计算阶段、比较、单位或日历形状。
完成数据本身不保存生产图、原语义对象、来源连接或隐藏聚合组件。
Frame 实例可另持有捕获时的不可变来源声明与输出映射，用于显式 attribute 新读。
compute 与同进程 persist 返回对象保留此引用；独立 reopen 不恢复该引用。
声明不写入 Store，不从同 artifact ID 的其它实例或全局缓存取回。

| 载体 | compute | persist | artifact_id / findings / evidence | to_pandas |
| --- | --- | --- | --- | --- |
| 表达式 | 求值并返回完整内存根 | 完整求值后发布 | 要求先 persist | 要求先 compute/persist |
| 内存 | 返回自身 | 发布已有完成结果 | 要求先 persist | 隔离副本 |
| 持久引用 | 完整验证并加载内存根 | 验证提交元数据后返回自身 | 同一提交的身份与审计读取 | 验证并导出当前数据 |

只有使用返回的完成根才固定值与键；重用待执行来源表达式可看到变化后的数据。
内存根只在其 Python 进程内可用。跨脚本继续须在生产进程退出前 persist 并输出或保存
Session/Artifact ID；仅导出 DataFrame、打印内存卡片或恢复 Session 不保留该内存根。
持续进程保留返回对象即可，一次性完成的问题无需保存；已有精确保存引用无需再次发布。
派生运算保持惰性，其 artifact_id 不返回祖先提交身份，导出也不自动执行。
载体转换保留结果身份/求值审计；多次发布一个内存根可以产生不同提交。
已完成的 where/top 选择由完整父键、旧值和冻结时间对应固定；后续 attribute 不重跑历史筛选。
保留声明不改变当前数据操作的读取基础，原值与本次新读父值分别通过 observed_total 与 total 展示。

session_id 在所有载体上返回捕获的所属 Session ID；artifact_id 仅对持久根返回提交 ID，
两个属性均不读取 Store。未保存根的 artifact_id、findings 和 evidence 给出 persist 修复。
持久根 compute 返回的内存根没有提交身份。show 展示有界输入、操作和来源事实/检查/Finding
数量，持久根另展示提交时间；show(n=0) 不读取结果行、坐标附件或完整方法/审计载荷。

## 稳定结构与有界读取

`frame.schema` 返回不可执行的 `FrameSchema`，不持有 Frame、Store 或回调。它在所有
载体上只读取捕获结构，不证明保存文件仍然可用。`input_id` 标识精确输入；`values`、
`presence`、`slots` 是同一组只读 `FrameField` 的索引，`axes` 保留完整轴和键分量顺序。
字段包括 slot_id、完整名称或键分量名、精确 dtype、nullable 和 unit。未声明单位
`None` 与无量纲 `"1"` 不同。待执行类型使用 `UnresolvedDataType(kind)`，只有待执行
字段允许 nullable 未知。具体类型保留 Decimal precision/scale 及时间 unit/timezone。

当前轴使用封闭变体：普通 field/identity 轴记录完整键；calendar 轴另含捕获时区和
builtin(unit, step) 或 named(ref, level) 粒度；interval 轴记录公共导出时区；paired
轴记录左右两个时间端点。named.ref 使用绑定时解析到的真实 `SemanticObjectIdentity`，
不从快照摘要、字段名或当前 Catalog 推测。轴的 `at` 是已捕获锚点，不代表新鲜度。

`FrameSchema` 还包含 `fixed_axes`、`value_contexts`、`comparisons` 和 `decompositions`。
fixed_axes 记录 `fix` 移除的普通轴、精确有序键与该轴的语义 origins；固定键不进入活动
`slots`。value_contexts 按当前完整值名索引，只存可准确绑定的直接观察主体、时间依据、
显式窗口，或当前行统计种类、父分组和 complete/restricted 行范围。无条目表示未提供
额外说明，不能解释为“无筛选”“无窗口”“全量”或数值无效。

comparisons 记录当前 Frame 中真实存在的比较量、当前值角色、两端上下文、当前 presence
和实际 calendar/interval 对应；不推断同比/环比，也不把显示在本期坐标上的基期值当作
本期观察。decompositions 记录当前 level/change 目标、父组、输出角色及 complete/
restricted 范围。所有字段名、轴 token、比较 ID、目标和输出引用必须属于同一精确输入。
固定事实不悬挂活动 slot，分解需要的比较量即使对应值列删除也可保留；这不会保存隐藏值、
补读历史或从贡献重新计算分母。无关关系随列裁剪而移除。

这些是当前结果的只读局部事实，不是业务定义或完整公式。语义身份仍只包含
`kind/path/definition_id`；需要定义详情时显式使用 `catalog.get(f"{ref.kind}:{ref.path}")`。
模型或 Catalog 当前不可用不妨碍读取固定结果，当前详情也不被当成历史定义的证明。

`schema.layout` 仅由结构派生。字段变体携带 name/kind="field"/slot_id，坐标变体携带
name/kind="coordinate"/axis_token/endpoint/part。endpoint 为 self/left/right；part 为
key/label/start/end/nominal_start/nominal_end，组合由轴角色决定。复合身份只导出
一个 tuple；paired 左右坐标属于同一完整轴。默认布局允许重名，实际导出在载荷读取前
检查唯一性；使用字符串 axis_names 或 select 修复。别名不修改 schema。

`read_page(*, columns=None, limit=1000, cursor=None, axis_names=None, time_details=False)` 只读取已完成数据。
columns 接受完整 value/presence 名，None 为全部，() 为仅坐标；重复或未知名在读载荷前
拒绝并给出真实候选。始终保留所有轴完整键和所选布局的可读坐标，所选字段按声明顺序排列；内部
可读取必要 presence 辅助列。limit 是 1..10000 的整数，不接受 bool。派生表达式仍需
显式 compute/persist；读取不执行分析、不创建 Run、不自动保存或访问来源/Catalog。

FramePage 携带 input_id、实际 layout、以全部键 slot 为索引的只读 coordinates、limit、
total_count、has_more、next_cursor 和 read_stats。len(page) 是本页行数；零轴空结果和
单行结果依靠行数区分。坐标标量保留 Decimal、Arrow Date32/Timestamp/Duration 精度和
NULL None。页内 RangeIndex 不承担业务键身份。页面不持有完整父数据、附件目录、审计、
方法事实、连接或 reader；小页数据脱离父 Arrow 缓冲。page.to_pandas() 不再读文件，
返回与 layout 一致且 attrs 为空的隔离副本。frame.to_pandas() 保留完整校验及既有
attrs 快照；二者使用同一布局和坐标解码规则，attrs 不构成稳定导入协议。

游标不可解析，绑定精确输入、读取种类、集合版本、limit、规范化投影和轴别名；续读
重复同一请求。持久输入在同版本 Runtime 独立 reopen 后可续读；内存游标限当前进程
同一固定输入。转换或派生取得新 input_id 后重新开始；返回自身的幂等操作不使游标
失效。空集无后续游标；总数来自已存行数，无全表扫描。

FrameReadStats 仅提供 data_rows_read、coordinate_rows_read、receipt_bytes_hashed。
行数统计实际解码或检查量，包括跳过前缀和重复坐标扫描；字节数只统计 receipt 哈希。
持久读取按列与范围分批解码，附件以固定批次扫描，只保留所需完整记录。收据哈希可能
扫描全文件，坐标查找可能扫描全附件，因此页大小不保证字节、I/O 或延迟上限。

`read_periods(target, limit=1000, cursor=None)` 在已完成的内存或持久 Frame 上读取一页
冻结期间或实际使用的端点对应。target 必须是同一 Frame 的 calendar/interval/paired
`FrameAxis`，或当前 calendar/interval `FrameComparison`；普通轴、普通比较和表达式拒绝。
它不执行、不持久化、不读取 Catalog、来源、method facts 或整个附件到内存。页面游标
绑定 input、target、存储结果版本及 limit，续读必须使用相同参数；limit 为 1..10000 的
整数且不接受 bool。

`FramePeriodPage` 提供同一 `input_id`、不可执行 target、items、分页状态和 `read_stats`。
单轴期间返回 `FrameCalendarPeriod` 或 `FrameIntervalPeriod`；paired 轴返回带完整 owning
key 和可缺侧的 `FramePairedPeriod`；时间比较返回 `FrameComparisonPeriod`，保留实际
current/baseline 端点及保存的缺失原因。日历标签保留标量类型和已保存民用边界；interval
不推测未保存的民用/nominal 边界或现行时区规则。paired 缺侧不臆造原因。期间 key 与
`read_page().coordinates` 中目标轴各 key slot 的有序、完整值连接；它不是当前行域，选中
期间不保证存在结果行。小页只保留本页期间和实际端点，不暴露通用附件浏览器或 shift 祖先链。

## 静态语义对象血缘

`frame.lineage` 在表达式、完整内存和持久载体上返回同一不可变目录，读取不执行分析，
不读取业务行、坐标附件、完整方法文件或当前 Catalog。`values` 按完整输出名查找，
`axes` 按当前轴 token 查找，`presence` 按完整 presence 名查找；复合身份仍为完整轴。
`lineage.input_id` 与同一 Frame 的 `schema.input_id` 相同；派生、fix、发布和独立恢复均
绑定各自当前输入身份，而不沿用祖先 Frame 的 ID。

每个 `ColumnLineage` 提供确定排序、去重的 `value_origins` 和 `context_origins`。
前者记录值表达式及命名声明来源，后者记录分组、时间、关系、筛选、排名、行域和对应
依赖。两组允许交叠。`SemanticObjectIdentity` 只保留 kind、path、definition_id，
不含定义正文、可执行 Ref、连接或凭证。同路径不同定义分别保留；相同计算的不同
命名声明也分别保留。标识解释实际消费定义，不要求原定义仍存在或拒绝同路径更新。

语义声明展开前捕获身份，Frame 构造时按槽位表达式传播。select 重命名保持来源；
where/top/fix 增加选择上下文，即使相关列或轴随后移除也保留。reduce/over 保留当前
输入与分组依赖，row_count 无值来源但保留行域上下文。align/compare 按端点映射，
attribute 分别承接旧观察与新读来源。字面量本身没有语义值来源。投影不会引入无关
输出的私有值依赖。条件分支保留静态依赖，空结果、乘零与物理下推不消除逻辑来源。

命名 Measure 在公共 where、Metric 内部 where 和 reduction 的 where 参数中都按
谓词上下文保留；同一 Measure 同时参与值与筛选时，两组都保留其身份。坐标目录也
保留筛选、排名与行域上下文。attribute 的加权平均分子承接值与权重，分母的值来源
仅承接权重，值与权重的非空配对条件进入上下文；有效配对数没有语义值来源。
普通平均的分母同样以非空值筛选作为上下文，直接比值则分别承接分子与分母表达式，
包括各自实际消费的命名 Metric。

compute/persist 和独立 reopen 保留结果血缘；每个保存结果自包含，不依赖原模型、
来源连接或祖先 Artifact。血缘属于独立结果元数据，不进入数据结构等价、计算权限、
来源声明或 method_facts。它不证明已执行读取、源数据一致性、业务身份等价或因果关系。
show 有界展示，typed fields 完整；没有逐行实际贡献或完整历史算子图查询。

```python
lineage = frame.lineage
lineage.show()
lineage.values["revenue"].show()
origins = lineage.values["revenue"].value_origins
axis_origins = lineage.axes[frame.axes[0].token]
```

## 完整 pandas 导出

`to_pandas()` 只接受完整内存或持久 Frame，返回隔离副本；表达式须先显式
`compute()` 或 `persist()` 并使用返回对象。固定数据上的派生运算同样保持惰性。

导出为可读平面表，坐标在前，随后是完整值名和独立布尔 presence 名。
普通轴默认取完整路径的末段；复合身份保持 tuple。全体列名必须唯一，冲突在读取载荷前报错。
`axis_names={"full.axis.name": "name"}` 通过完整轴名或唯一 token 设置别名；不接受 FrameAxis、
短名模糊匹配或重复指向同一轴的别名。重名轴使用 schema 中的唯一 token。别名只改变输出名称。

日历和区间统一导出 `.start`、`.end`，表示实际观察的半开区间 [start, end)。
两列均为带时区的 Arrow 时间戳，保留精度与缺失，使用轴捕获的时区；日、小时、分钟无需分支解析。
部分桶直接暴露实际裁剪边界。配对轴分别使用 `.left`、`.right` 前缀，缺失端点为 NA。
`time_details=True` 为日历增加 `.label`、`.nominal_start`、`.nominal_end`；命名日历标签
保留原有标量类型。时区由时间戳类型携带，不额外导出 timezone 列。
导出读取不会修改冻结数据或普通值列的类型；时间标签不授予跨 Frame 身份或观察范围等价性。

`read_page()` 使用相同 axis_names 和 time_details 规则；游标绑定详情选项，续读重复同一请求。
`schema.layout` 描述默认布局，`page.layout` 描述该次实际布局。page.to_pandas() 不再读取数据。
普通 TimeField 轴导出一个时间值坐标列；calendar/interval 的两列描述区间，不能互换。
axis_names 只重命名导出前缀，不改变轴结构。需要单列时选择 `.start` 并在 pandas 中重命名；
裁剪桶的 `.start` 是实际观察起点，原始桶起点使用 `time_details=True` 的 `.nominal_start`。

完整导出 attrs 保留隔离的 axes、units、comparisons、frozen_coordinates；分页导出 attrs 为空。
frozen_coordinates 每项的 rows 为不可变 pyarrow.Table，保留声明的列类型、精度与 NULL，
且相等比较返回布尔值，兼容 pandas 的 attrs 比较。需要 pandas 表时显式调用
`part["rows"].to_pandas(types_mapper=pd.ArrowDtype)`。
这些 attrs 不承诺稳定反序列化协议，导出的表不授予 typed 运算权限。
导出保留行序、物理数值、NULL 和合法零，返回隔离副本；show 的有界预览复用默认坐标解释。

区间轴描述捕获公共导出时区，分页不从当前页推断。混合时区的合并区间轴统一使用 UTC 时间戳；原始边界时区保留在冻结目录中。
固定偏移含非零秒数时也使用 UTC，以适配 Arrow/pandas 的时区解码，保持精确时间点；秒数为零时规范化为分钟偏移。此规则同样适用于详情列、分页和预览。
本次接口调整在持久化区间轴描述中新增必需的 `timezone` 字段；冻结目录的边界数据不变，旧区间描述格式不提供兼容读取或迁移。

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
observe/reduce 接受单个对象、非空 tuple 或显式命名 Mapping；不接受 list、任意
Iterable 或混合命名项。tuple 和 Mapping 的声明顺序决定输出顺序，先校验全部候选名
再构造命名输入。重复名（包括重复对象）和非法名在构造时拒绝，不去重、不覆盖、不编号。
名称在求值前确定，可立即从 output_names 读取；空数据、compute、persist 和独立恢复均不重新命名。

observe 简写仅继承冻结 scope 消费后根节点 DeclaredMetricRef 的结构化路径本地名。
命名 Metric 内部可包含复杂公式；直接引用仍使用声明名。引用外再加算术、filter、window、
per 或 rescale 则要求 Mapping；直接 source reducer、Entity.count、Population.count
和未命名表达式同样要求 Mapping。不读取 Python 变量名、repr、依赖叶子或 Catalog 反查。
不同域的同一本地名在同次调用中冲突；未参与本次调用的同名 Metric 不影响推导。

reduce 先验证精确 Frame/Session 统计绑定，再仅为直接 ValueSlot 且 schema 中恰好一个
名称段的 sum/mean 生成 `<name>_sum` / `<name>_mean`；row_count 固定为 row_count。
例如 revenue_sum 再 sum 得到 revenue_sum_sum。算术、条件、over、坐标、presence、
PairStatistic 和 left.revenue 等多段值路径没有默认名；合法统计可用 Mapping 指定单段名。
名称不赋予统计资格、原 Metric 可加性或跨输入对应权限，不能使原本非法的统计合法。
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
单个裸 Frame 基期固定使用 reference 别名，保留相同的 reference.* 完整路径和 reference.present。
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

`column.share(along=axes)` 将 along 指定的轴参与加总，其余轴
构成分区，等价于当前 Column 除以相应 over(sum)。along 必须是同输入完整轴的非空、
无重复 tuple；不改变行粒度，零轴 Frame 拒绝。需要先去掉其它轴时显式 reduce，再从
返回 Frame 重新获取句柄。份额是有符号比例，不乘 100，也不保证在 [0,1]。
零分母固定为 NA，未知单位保持 None；合法 Duration 组合沿用
sum/divide 合同。筛选前后的位置有意义：先筛选改变分母，后筛选保留已有分母。
两者不重读来源、不生成新 method_facts，不认证原 Metric 的可加性或来源贡献。

## Fixed coordinates

`frame.fix(axis, key)` selects one complete field/identity member and removes that
axis without aggregation. A single-component axis takes a scalar; a composite axis
takes a complete tuple in `axis.key_names` order. None matches a nullable NULL
coordinate; nonnullable identity components reject None. Calendar, interval and
paired axes cannot be fixed. Other axes retain their roles and frozen attachments.

Fix preserves selected physical values, units, NA, row presence, comparison value
bindings and relative row order. The remaining complete key is unique because the
removed coordinate is constant. Missing members return typed empty data. Removing
the final axis produces zero or one row, including Frames without value columns.
Acquire new handles from the result. Fix is lazy and retains required input checks.

Fix retains process-local source declarations with their fixed original coordinate
keys. Subsequent attribute uses those keys and the remaining selected parent domain;
source grouping distinguishes different fixed members. Membership allocation paths
remain owned by their original declarations. Independent reopen remains source-free.
Fix leaves the remaining axes subject to compare's default or explicit on mapping.

## Correspondence and comparison

align 接收另一 Frame；compare 接收单个裸 Frame（固定别名 reference），或非空有序
命名 ComparisonBaseline Mapping。每个声明通过 mv.baseline(frame, on=..., match=...) 获取。多个基期必须命名；单基期也可显式选择业务别名。
每端先验证同 Session、无 paired 轴、相等轴数与相容的完整键/角色/时间解释。
省略 on 时，普通轴按两端唯一相同的完整名称配对，角色和有序完整键名必须相同；
每端最多一个时间轴，且双方时间轴角色必须同为 interval 或同为 calendar。零轴也可推导。
配对不依赖轴顺序、token、数据值或类型猜测；名称重复、不匹配或部分映射时须显式完整双射。
多时间轴不能自动推导。compare 最多支持一对时间轴；align 可显式对齐多对时间轴，但不能为它们使用 match。
裸 Frame 的 on 是完整轴对 tuple，match 是单个 CalendarCorrespondence；命名基期的
Frame、on、match 集中在 mv.baseline(...) 中，外层 Mapping 只声明一次别名。
命名形态不接受顶层 on/match，也不接受裸 Frame Mapping 或裸 ComparisonBaseline。
ComparisonBaseline 是不可变声明，公开只读 frame/on/match，repr/show 只披露有限元数据；
工厂只检查输入形状，compare 再绑定当前 Frame 的所有权、完整轴双射和时间相容性。
部分 on 不自动补足。reference 路径冲突要求显式别名或先 select，不自动编号。
省略别名不推断同比、环比、预算或时间对应，也不按唯一值列的位置配对。
FrameAxis 的显示名、token、dtype 或相同值不证明跨输入业务身份。
省略 on 表示调用者接受同名轴默认对应；显式 on 可声明不同名轴的业务对应。检查只保证结构相容以及所选政策下的实际键域，
不验证原 Entity/Field 相同。Customer/Product 的同类型身份键可以显式配对；结构检查成功不证明业务身份等价。
compare 的同名量配对同样是调用方组织输入的方式，兼容类型与单位不证明相同 Metric 或业务可比性。

compare 的时间关系按每个已绑定基期独立选择：显式 match 优先表达意图，但须与存在的直接
shift 关系一致；省略时仅消费锚点与本期完整冻结域相符的直接关系，否则拒绝错误锚点。
没有直接关系也没有 match 时按完整键等值，不猜日期偏移或沿祖先追溯。
锚点比较 Grain、时区、规则版本、选定键与名义/实际边界，不要求同一内存对象或相同数据值。
align 仍使用显式 match 或等值，不引入自动 shift 推导。

每个基期的实际完整联合键先映到本期坐标，其它普通轴仍完整键等值。
align/compare 固定 outer，先保留本期行序，再按基期声明序/原行序加入新联合键。
额外目录桶无实际行时不制造输出行。实际基期时间行无法映到本期声明域时仍拒绝；
需要一般双端时间并集时使用 align。相对变化为 (current-baseline)/abs(baseline)，
零基期固定返回 NA；缺侧和值 NA 传播，不自动填零。
compare 不裁短本期、不填零、不为某个别名重求本期量或分类。显式贡献 Population 不重选；
显式 by=P 是该端完整输出域，基期对应时间×普通键域须由调用方独立构造。

align 保留左右值和独立 left_present/right_present，显式时间 match 可生成单层 paired 轴。
compare 始终保留本期普通时间轴、当前值及命名说明；current.present/alias.present 只表示行存在。
两者拒绝已有 paired 输入；select/where 不删除坐标，不能绕过该限制。

### Interval

TimeScope 在 by 中产生 interval 轴，保存精确 start/end 与边界时区，不自动变成零轴。
等值对应识别精确边界和时区。ordinal 要求每端完整声明目录恰有一条记录且边界时区相同，
不同期间可明确配对；过滤成一行不会把多区间目录改成单区间目录。
interval/calendar/零轴不互转或广播；time×普通分类可使用上述完整默认配对，或显式给出完整 on。
compare 保留当前 interval 及命名基期说明；align 的双端并集/paired 语义继续存在。
Store 23 / marivo.frame_output/v12 保存解释目录、实际键引用与关系，冷恢复不重建 TimeScope、
来源定义或祖先 Frame。所有新事实属于当前输入，不改变数值计算、过滤范围或方法文件合同。

### Calendar matching

匹配规则与 shift 支持范围由[时间语义](../temporal-semantics.md#period-shifts-and-fixed-data-matching)拥有。
显式规则、冻结 direct relation 与当前锚点必须一致，不能按别名或日期标签猜测。

冻结目录保存选定桶键、稳定名义 ordinal、名义/实际 UTC 与规范化民用边界、时区和规则版本。
新生成的日历桶键是轴身份下的名义起点（UTC 微秒时间戳）；首尾裁剪和稀疏选择不改变键。
ordinal 继续承担排序和对应关系职责，时间键相等不放宽身份、快照和实际边界校验。
父子日历及 direct relation 使用双方的时间键；区间轴仍保留原有单元键。
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
yearly = current_series.compare({"yoy": mv.baseline(previous_series)}).compute()
```

这里 revenue 为显式已解析来源 Metric。每个端点独立完整求值；同图可以共享合格依赖，
但不承诺跨来源事务快照。保存端点恢复后，锚点一致时仍可省略 match，不恢复 Metric。
手动准备的历史 Frame 使用 `{"yoy": mv.baseline(previous_series, match=mv.offset(years=-1))}`；别名不推断规则。

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

`frame.select({})` 是合法的空值投影：`output_names` 为空，保留当前行域、行序、完整坐标、
全部 presence 和必要的冻结映射；比较值绑定清空，保留有限 presence 说明。
零轴且无物理列的结果仍区分零行与单例行，求值、导出和保存恢复均保留该行数。
空投影不跳过上游必须执行的计算或错误检查。

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
- align/compare 先按左侧/当前输入行序输出匹配/独有键，再按右侧/基准行序追加独有键；日历映射不改此行序合同。
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
retains its native int64 carrier, Decimal SUM uses Decimal(38,input_scale), and mean keeps
its declared result type. [Numeric carrier rules](operators-and-frames.md) own
Decimal convergence, float approximation and source/local overflow differences.
Persistence and cold reads preserve the committed values and types without new rounding.
Native backend floating results, including infinity and valid NaN, remain valid Frame
value cells through completion, persistence, reopening and pandas export. Arrow validity
keeps those values distinct from NULL. Native arithmetic overflow follows the backend's
result or error; exact carrier, complete-key and local Frame method rules still apply.
Coordinate keys retain their finite typed-key contract.
Selecting an existing value Column preserves its captured values and null validity;
computed local expressions use the actual pandas/Arrow or Decimal scalar kernel,
without extra overflow/finite scans, protective widening or retries. Intrinsic
kernel errors and lossless storage-boundary checks still fail completion.
