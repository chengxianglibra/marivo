# Lazy Analysis DSL：执行、保存与结果交接

Date: 2026-10-14

Status: proposed v0.35；尚未实施，不构成当前 API、后端能力或 Runtime 验收声明。
v0.35 明确分阶段检查、浮点优化准入、旧 Session 拒绝与历史产物边界；移除资源预算设计。
v0.34 明确来源类型准入、多 datasource 路线、普通观察 Evidence 和 Population.contract 的责任。
执行分工仍为 Ibis 源端计算、pandas 统一本地执行、PyArrow 存储和读取；Artifact 始终为终端产物。
本文承接 [DSL 定义](lazy-analysis-dsl-redesign.md)，拥有执行图、保存/恢复、Agent 披露和 pandas 导出布局。
参数与算子语义由 DSL 定义拥有；设计取舍与验收见 [场景验证](lazy-analysis-dsl-scenario-validation.md)。
当前实现仍由 [Python Analysis Design](python-analysis-design.md) 和
[Session State and Runtime](session-state-and-runtime.md) 约束。

实施进度：R2.4 已接通私有数值路线及计算字段、单值路径、精确版本锚点、
解析/民用日期/naive 时间、编码 Duration 的准备；不同 datasource 根先分别规约后组合。
普通原生字段 semantic Metric preview 复用数值内核；路径/锚点和 Population preview 在连接前拒绝。
R2.5 已接通公开 Query.execute、Store 10 原子终端发布与无图输出记录；返回已提交身份。
R2.6 接通公开元数据打开、有界预览、完整 pandas 导出和无图记录阅读。Finding 保留分页；
evidence_digest() 不接受参数，直接返回全部 Evidence（.items）及集合摘要，.show() 保持有界。
这些本地能力不构成后续阶段或远端 Runtime 验收声明。

## 1. 执行、保留与恢复

### 1.1 图与来源分类

复用当前定义图、compiler、方法注册、物化、原子发布和 Store，不建第二 Runtime。
私有节点包含 Population 的来源身份选择/显式读取/坐标运算、Metric 域依赖/贡献过滤、
类型化行表达式/Measure 声明、来源规约、Metric 组合/PerReduce、Observe、Query 变换与 Values。
图不包含 ArtifactRead、FixedLeaf 或从 Artifact 派生的数据叶子；执行结果不是后继图的输入。
来源 Ibis 在合法 schema/关系绑定后 lower，并沿 SourceSession.execute 返回 pandas；
本地 stage 统一用 pandas/现有数值内核完成本次计划内的组合、检查和中间关系消费。
Query 依赖可含来源、字面量或两者，仍需明确多来源/本地 stage；不再维护 Artifact 输入的 source/fixed/mixed 矩阵。
同一次 execute 可读取多个 datasource，范围限于已准入的后端、类型和交接路线；
至少一条两个不同 datasource ID 的真实组合路线属于目标交付，具体门禁调整见
[架构 §9.2](analysis-implementation-architecture.md#92-名单和多来源的执行内交接)。
字面量名单以类型化 Ibis 完整元组谓词传入来源；来源依赖的名单须有合格的执行内传递路线。
不支持就报告具体执行能力缺口，不隐式全源扫描、来源写入或把 Artifact 接回图。

#### 1.1.1 共享逻辑节点与执行内输入

共享 Population/Metric/Query 只共享定义，不承诺同一次读取或相同值。
`artifact = q.execute()` 不替换 q、不缓存其业务答案；以后执行 q 是新求值。
同次 execute 也不自动提供跨源快照；独立 Metric 根无需共同读取时点，不追加对账或重试来制造保证。
执行器可以在本次 invocation 内暂存/复用中间输入，其生存期由计划消费关系管理。
这不是公开固定结果对象，也不产生跨 execute 的固定输入保证。

reduce、top 和 share 各自消费同一份实际输入。share 的分子/分母须使用合格共同输入路线，
不能展开成两条独立读取后仅凭相同逻辑节点证明支持。
后置 where/top 不能越过 share 改变分母和必要检查；前置筛选按调用顺序进入总体。
无共同输入路线时报告执行能力缺口，不能建议先 execute 再调用 Artifact.share。
多轮中需要已见数据的精确值时继续 pandas；重新 Query 求值不承诺重复那份输入。

#### 1.1.2 Metric 显式域依赖

行/Metric 表达式只形成不可变定义；共同私有图传递依赖中立描述，不导入 Session 执行器，
不捕获 Python callable、活动 Catalog、连接或 Artifact。域依赖只含两类叶子及其合法逻辑组合：

| 域依赖 | 定义事实 | 何时得到键 |
| --- | --- | --- |
| 字面量域 | 类型化有序轴、完整键、相等性与版本 | 构造时已有，不据此证明来源存在 |
| 来源/Query 域程序 | 完整逻辑依赖、Ref 解析版本、固有条件/时间与已有 Session 归属 | 消费它的 execute；构造/声明/收录不执行 |

没有项目/Session Artifact ID 键域、固定结果引用或按保存图恢复的域。
Metric.where 保持完整贡献作用域；Metric.per 按 (外部格子 k, 内部样本 j) 绑定子指标后规约。
不能要求裸 j→k，也不要求内部选择覆盖域外未选择贡献；observe(by=Population) 的外部覆盖要求仍独立。
域的定义、类型/角色、政策及已有 Session 归属进入本进程指纹，表达式使用已解析对象，不按名称刷新。
2026-10-15 的定义更新决定取消“必须匹配构造时原版本”的输入约束，不取消实际消费内容的身份。
同路径定义更新本身不拒绝输入，后果由 agent/用户承担；解析消费者使用其完整作用域，已闭合表达式仍不可变。
计划/缓存键须覆盖实际消费的定义、依赖、政策、默认时间/坐标和绑定；解析为不同内容后不得复用旧键的答案。
依赖scoped fingerprint、认证snapshot的失效及已有持久化记录的完整性要求各自保留；
这不是新增Artifact Ref/图存储、来源新鲜度保证或自动模型版本核对。所有非定义版本的类型/根/粒度/单位/时间/null检查保持。
字面量域在消费时绑定 Session；来源/Query 域原有的选择期间、属性时点、检查和归属必须保留。
域自身选择不继承外层 observe 的共同过滤或期间；复用 Python 名称不等于固定一次名单。
模型收录的独立解析条件由[语义 §2.4](../semantic/expression-model-redesign.md#24-命名与模型收录是不同边界)拥有。
模型加载和分析 Artifact 的保存互不替代；不能通过执行名单、剥离 Session 或提升 Artifact 使模型收录通过。
脚本显式提供的外部键/数值作为新的字面量输入处理，不消费旧 Artifact 的绑定、检查或历史图。

### 1.2 默认保存信息

execute 将最终 pandas 输出交给 writer，由 PyArrow 编码为 Parquet；数据与输出记录提交成功后才返回 Artifact。
Artifact 通过 `ArtifactData` 引用这些已提交输出：完整输出键、命名 nullable 值、presence、实际坐标投影、
必要数据描述与 Top-N 展示次序。schema 包含名称/轴 token、dtype/nullability、键形状、单位文本、
必要时区和实际边界；标签只帮助定位数据，不授予业务可加性或语义绑定。
另保存精确 Artifact/Run/Session ID、格式和文件 receipt，以及本次实际产生的 Finding/Evidence 输出记录。

Finding 是方法明确产出的结构化发现；Evidence 是支持产出的实际数值、坐标、已执行检查的结果或声明来源摘要。
二者保持事实来源及检查范围，不能把默认来源信任写成已验证完整性，也不自动生成因果或业务判断。
普通 observe 的 Finding 集合为空；执行器仍为实际来源读取和实际执行的检查生成有界、自包含的 Evidence：

| 普通观察记录 | 必须保存的事实 | 不能推导的结论 |
| --- | --- | --- |
| 来源读取摘要 | 来源标识、贡献角色、本次实际读取范围及事实来源；during=None 明确为未追加时间约束 | 已读取全部现实历史、来源完整或跨源快照一致 |
| 实际检查摘要 | 检查种类/版本、所检查范围和实际结果；只记录本次真正执行的检查 | 未执行的审计已经通过，或未来执行仍然满足条件 |

这些 Evidence 以可独立阅读的范围摘要保存，不携带可执行过滤程序、SQL、成员名单副本或证明依赖图。
没有来源读取或未执行某项检查就不生成相应记录；必要检查失败仍阻止整个产物发布。
新产物与冷恢复保存、读取相同摘要；本节唯一拥有普通观察的保存要求，
不新增分析方法、LLM 总结流程或通用证据推理系统。
复用现有 FindingPage/ArtifactDigest 家族；Finding 有界分页，evidence_digest() 无参返回全部已保存 Evidence，
结果 .show() 保持有界。记录必须自包含，只引用当前产物的输出位置/记录身份。
不保存需解释计算图才能读取的 derivation，不把执行中的 Fact/obligation/证明依赖链直接序列化。
必要数值若被明确选为 Evidence 输出，就按输出保存；不能以 evidence 名义默认保留全部隐藏 sum/count。

不保存 Metric/Entity/Field 等语义对象、逻辑/物理图、公式、来源程序、节点政策闭包、
计算指纹、可执行授权或内部规约组件；Run、sidecar、attrs 不能成为另一条恢复路径。
Artifact 对象本身也不持有 producer logic 或内部组件。mean 的 sum/count、配对状态及临时细网格
仅在本次执行计划需要时存在，完成或失败后释放。跨执行需要分子/分母等数据时须显式列为输出量。
没有 execute 保留请求或保存模式；必要检查失败不发布成功 Artifact、Finding 或 Evidence 的部分组合。

### 1.3 跨轮和跨进程恢复

`Session.artifact(artifact_id)` 只按精确 ID 打开已提交产物。新产物和重开产物具有相同的
show、to_pandas、findings、evidence_digest 行为；没有 origin/fixed/cold、context 或额外续算权限。
二者统一读取已提交 Parquet 和输出记录，不保留内存数据句柄或 producer 缓存；持有原 Query 不增加产物能力。
列、dtype、NA、完整键、presence、投影、次序与 Finding/Evidence 的内容往返一致。

Artifact 不是 Query/Population/Metric 输入，不提供 Column、population 或任何 DSL 变换，
也没有 contract 续算矩阵、freeze、from_artifact 或隐式回灌入口。
多轮处理通过 to_pandas；新语义计算须构造新 Query。显式字面量输入不继承旧产物语义，
重新运行原 Query 可能改变来源值和选中名单，不承诺复用上次答案。

项目 `.marivo/` 继续保存 Session 和产物；重开不选最新、不读来源/Catalog、不反序列化图。
旧脚本/语义声明变化不影响旧值与 Finding/Evidence 读取；是否重用或重算由用户/agent 根据脚本判断。
Artifact 不保存 Ref 或计算指纹，也不判断当前模型或来源是否已变化；其格式版本只说明能否读取，
不能用来断言语义仍然有效或数据仍然新鲜。不新增模型版本对照或自动失效机制。
旧格式不迁移、不双读、不自动重建；拒绝旧格式时保留文件。
旧 Store 代际中的 Session 不能 resume；错误给出实际与期望格式，并指向保留旧目录、使用新格式 Store
创建新 Session 后重跑脚本。在旧 Store 内只换 Session 名称不能绕过格式拒绝。

### 1.4 数学定律的实现边界

优化须保持身份、贡献出现、参照、完整坐标、政策和定义域；
m/m不能约成1，乘零/空集不能消除必要错误；缺失 mask 不同的 sum(x+y) 不能拆成 sum(x)+sum(y)。
where(~p) 不能优化成 Population 补集，分组不得丢弃合法 None 键；所有改写保持有效样本。
整数/Decimal 在各自声明类型、溢出及最终舍入合同内保持精确语义；浮点遵守方法精度，不承诺逐位重排等价。
方法准入须固定累加类型/算法、输出精度与最终舍入；来源原生实现和本地实现分别按该合同获得资格。
首期沿用现有数值内核：普通来源 sum/mean 等使用 Ibis 原生方法及 native_numeric 合同，
接受该后端的原生舍入，不设跨后端统一误差界；本地 float sum 使用现有 math.fsum，mean 以相同有效样本的和/数完成。
首期优化不改变浮点求和的分组及完成顺序；投影、同级计算合并等仍须保持所选方法与样本。
不引入全局误差开关、跨后端逐位一致保证或新的确定性求和框架。
精度合同不是任意重排许可：例如 fsum([1e16, 1, -1e16]) 为 1，
而 fsum([fsum([1e16, 1]), fsum([-1e16])]) 为 0；换成 fsum 仍不能证明分组后合并等价。
无法证明规约拆分/融合符合方法合同，就保留原计算路线。任何准入的数值误差都不能放宽完整键、NA mask、
零分母/error 政策、where 判定、排名和 Top-N 身份的要求；可能改变这些离散结果的改写不准入。
定律中的固定实例与实际读取保证采用 §1.1.1，等式只对满足前提的同实际输入成立。

### 1.5 单一执行路线与终端读写

| 环节 | 唯一路线与责任 |
| --- | --- |
| 来源计算 | Ibis 编译到已绑定来源后端并返回 pandas；不依赖 to_pyarrow/to_pyarrow_batches 作为来源交接协议 |
| 执行内字面量与中间数据 | 按已知 schema 规范化为 pandas DataFrame，保留完整键、列序、dtype、缺失表示与必要组件 |
| 本地计算 | 同一组 pandas/已准入数值内核消费本次 stage 输出；无 Arrow compute 或 DuckDB 本地引擎分派 |
| 产物发布 | 完成必要检查后，PyArrow 编码输出数据为 Parquet；Store 原子提交数据描述与实际 Finding/Evidence |
| 产物读取 | PyArrow 按需读取已提交数据；show 有界预览，to_pandas 导出完整隔离副本；Finding/Evidence 由 Store 有界读取 |

DuckDB 只作为 Ibis 的一种已声明 datasource 后端，包括其已有文件来源能力；不隐式接管本地计算或扫描 Artifact 做 DSL 续算。
这约束 Marivo 的阶段交接与直接调用，不要求修改 Ibis 后端或 pandas dtype 的内部实现。
完整图仍由 Marivo 定义和调度。来源和字面量按值依赖进入 SourceRead、LiteralInput、LocalCompute，
共享输入不得被消费者原地修改，组件在最后一次消费后释放。没有本地计算的计划可直接发布来源输出。
不能下推的操作须在规划时具有合格 pandas 路线；没有资格就报告具体缺口，不在失败后切换引擎、近似算法或扩大来源读取。

这是一条 eager 执行路线，本地表、关联/分组状态和中间副本受内存限制；来源尽量投影、过滤和合法规约，
不假定输出或中间结果天然很小。Parquet 分批编码和预览扫描只优化 I/O，不提供端到端流式或超内存计算保证。
新产物与重开产物使用相同读取合同；读取不加载 Query、调用来源或重建执行上下文。
具体实现分层与内存管理由[架构方案 §9](analysis-implementation-architecture.md#9-统一执行调度与执行内依赖)拥有；
本节不增加公开引擎选项、保存模式或输入转换入口。

#### 1.5.1 来源类型准入与无损交接

来源读取与产物往返各自承担类型责任；Artifact 导出无损不反向证明来源转换无损。
datasource 拥有来源 schema、后端及实际驱动/Ibis 转换路线的类型合同，物理准入消费这些资格。
完整键、大整数、Decimal、带时区 instant 和 duration 等所需的精确表示必须有合格路线；
已知有损或无法保证必需精度的路线拒绝执行，不隐式降为 float、去时区或降低时间精度。

schema-only 准备只解析声明类型、精度、时区及对应路线资格，不扫描业务值，也不能证明具体值从未损失精度。
执行入口按已绑定合同检查实际返回布局及必要值约束，再规范化为 StageOutput；
失去精度后再 cast 为原 dtype 不构成修复，不能通过重新标注类型取得精确类型资格。
这些检查复用现有来源绑定、物理准入和结构化错误，不增加另一套类型证明平台。

已接收的类型化值在 pandas 布局交接、产物写入、重开和导出之间不得因表示转换再次降精度。
算子计算仍遵守自己的数值合同；浮点重排、近似算法或溢出不由“无损交接”获得额外许可。
原来源数据自身的准确性不由这项传输合同证明；存储/导出也不能恢复来源已发生的损失。

## 2. Agent 读取与错误契约

Help拥有静态语法，Metric.contract拥有定义、规范政策、显式依赖、归属、收录阻碍及绑定要求，
Population.contract 拥有域定义和已有绑定，Query.contract 拥有本次逻辑上下文；
Artifact.show 展示已提交答案与有界 Finding/Evidence 摘要。
Artifact 没有 DSL contract；有 Top-N 展示次序时沿用，否则按完整键规范排序。
默认政策由 [语义提案 §3.4](../semantic/expression-model-redesign.md#34-样本政策的归属) 和
[DSL §4.4](lazy-analysis-dsl-redesign.md#44-最小规约与有效样本) 拥有，实例只显示其已解析值。
执行内输入合同集中在§1.1.1，各算子说明自身需要的局部依据。
三个 contract 与新/重开产物阅读的完整调用见[场景 §8.7](lazy-analysis-dsl-scenario-validation.md#87-构造披露执行后阅读与重开)。

### 2.1 repr 与输入权威

所有公开对象提供 bounded 单行 repr，不触发业务或 schema 读取。
Query repr 包含 kind、逻辑身份、来源/字面量依赖摘要及 contract 定位；Population 不提前枚举未知键集。
Metric 显示匿名/命名身份及已知域依赖，不把声明名称显示成已发布 Catalog Ref。
Artifact repr 包含 kind、Session/Artifact ID 并指向 show；不显示虚构的执行状态或续算资格。

Population.contract 从自身定义、已有绑定及与构造共用的纯准入规则披露：有序轴/值类型/角色、
已有 Session 归属、字面量或来源/Query 依赖、自己的选择期间与属性锚点，
以及可判定的操作、尚缺的绑定和待执行检查。未知键集保持未求值，不枚举键或读取业务/schema 来补齐信息。
Help 拥有静态签名和例子；Population 实例不重复能力全集，也不把逻辑合法性显示为已执行检查通过。
Query.contract 披露主输入、参照/域依赖、版本、必要检查和声明的读取范围；
动态范围尚未求出时保留依赖，实际读取及检查结果只由成功执行后的 §1.2 Evidence 披露。
drill 指向有来源的观察端点，compare 不显示自动 drill，share 披露所选量和分母范围而不计算分母。
Artifact.show 从已提交输出读取数据预览及事实摘要，不加载 Catalog，不重算或推断新 Finding。
细节通过 Finding 分页与完整 evidence_digest() 读取；只为实际存在的输出提供阅读提示。
展示被字节预算截断时，提示通过 to_pandas() 和 evidence_digest() 读取完整数据与事实，
不提示 Artifact.show 未接受的参数。Evidence/Coordinate 作为嵌套结果接入 native Help，
支持实例、类型及其阅读方法查询，不增加顶层导出。

### 2.2 域测试的未绑定与已绑定披露

in_population构造时repr显示population的有序轴、身份/字段角色、已知依赖及binding=unbound；
未提供接收者时只陈述可检查的域形状，不能预称将读取字段或过滤结果。
where绑定后，接收者contract披露实际测试：
member_identity、contribution_identity、field_tuple或result_coordinate，
并显示完整匹配轴、实际投影/父子映射、域依赖和新增字段/来源读取。
同一字段域用于 Session.members 的来源条件时可新增字段读取，用于 Population/Query.where 时只测试已有坐标。
Population.where 不接受 FieldPredicate；需要当前字段条件时用来源选择的 where，
或先 read 再在返回的 Query 上以列筛选。接收者决定读取边界，不能静默升级为属性查询。
不拆出第二套in_population入口，不按列名猜轴，不提前求键或Python列表。

### 2.3 跨阶段错误给出有效修复

错误由所属层 SemanticError/AnalysisError/DatasourceError 拥有 expected、received、location 和 typed repair。
修复候选来自真实语义声明/当前 Query 定义，只构造或描述合法下一步，不自动执行。

| 错误输入或位置 | 具体修复方向 |
| --- | --- |
| 来源构造器收到结果Column/统计请求 | Query 格子统计的 sum/mean/row_count 使用 reduce；其它结果处理按 §3 交 pandas；新来源指标明确来源定义 |
| 结果select/reduce收到Field/Metric | 新指标先observe，语义字段先合法read/分组；Query 已有轴使用coord，随后显式对齐 |
| Ibis cases/least 等收到行/Metric/结果表达式 | 只使用当前阶段获准的算子；结果普通处理交 pandas，复杂来源值先建合法来源字段，不自动转换阶段 |
| 来源Metric由裸后端Ibis表/memtable拼入 | 指向合法Entity与声明字段，说明缺少的来源/单位/权限，不能转型绕过 |
| @ms.metric 收到来源表参数、Ibis scalar 或全局计算政策 | 使用零参数声明体返回 Metric；政策移到实际 sum/divide 等节点，名称从函数名捕获，domain 在收录时限定，不创建 Session 或执行查询 |
| 旧 ms.aggregate/ratio、entities=、fanout_policy、empty= 声明 | 按[语义附录 A.1](../semantic/expression-model-redesign.md#a1-拟替代的已提交合同)指出当前声明的替代写法及非等价项；不静默迁移或累积成新的批量诊断框架 |
| @ms.measure 返回 Metric 或逐行乘积直接 sum 缺授权 | Measure 体返回同根行表达式；已有单位/许可可继承，仅对缺少的新业务权限作显式声明，不修改原叶子的权限 |
| Metric 依赖活动 Session、可变外部值或未受支持回调 | 指出捕获位置和预期的不可变定义/精确引用；不 pickle 对象或自动执行域 |
| 声明无法被模型收录 | 列出依赖路径、Session 归属和缺失的持久解析条件；保留临时使用，或显式重写为可加载依赖，不复制名单 |
| 声明函数名不符合语义路径段规则 | 装饰时列明函数名、位置、约束和改名修复；不静默规范化或接受 name 覆盖 |
| Metric 无法确定模型 domain | 收录时要求显式 domain Ref 或唯一已声明默认域；展示真实候选，不从文件路径、依赖或 Session 猜测 |
| 同一模型完整名称对应不同声明 | 指出两个声明位置及限定后的名称，要求修改声明函数名或明确不同业务 domain；Python 别名不能消除冲突，不覆盖 Catalog |
| Metric 域来自另一 Session | 列明原归属与消费方，要求使用合法同归属逻辑输入；不自动剥离归属 |
| DSL 收到 Artifact 或 Artifact 数据轴 | 使用原 Query 在执行前组合，或 to_pandas 后处理；不得把产物转成隐式数据叶子 |
| 需要 Query/Population 的位置收到 Artifact ID 字符串 | 报告所需类型；精确旧答案用 Session.artifact(id) 阅读，不自动解析为计算输入；合法字面量位置的字符串仍按普通值处理 |
| Metric.per 缺贡献到 (k,j) 的绑定，或输入阶段错误 | 列明实际轴、根和缺失绑定；使用合法字段/域，Query 格子统计用 reduce；Artifact 交 pandas；不要求裸 j→k 或补齐域外未选择贡献 |
| 算子政策不适用或 lowering 无法保持 | 指出实际节点和政策；例如 sum 不接 empty，mean 不能补零；无合格路线明确失败，不退回后端默认 |
| Column属于另一输入域 | 先按已有合法映射align/broadcast，再构造结果表达式 |
| 已知单位不相容，例如 CNY + USD | 行/Metric 构造报 SemanticError，Query Column 构造报 AnalysisError；列明双方单位与位置，要求显式合法换算后重新组合，不隐式换汇 |
| 独立参照列单位缺失或不相容 | 列明参照列及双方实际单位；用 values 的 columns={"budget": "CNY"} 等映射创建新参照，裸常量不携带单位，旧结果不补写单位 |
| values 的 columns 收到嵌套列定义或缺少行中量名 | 直接提供完整的列名到 str / None 单位映射；"1" 为无量纲，None 为明确未声明，量名与每行完全一致 |
| TimeScope.by/coarsen/to_date 的 grain 收到字符串，或 by/coarsen 另收到 count | 使用 mv.grain(unit, count=...) 或已加载认证日历的 grain(level)；粒度消费者仍校验合法分桶、粗化或包含关系 |
| identities/coordinates/values 手写键与轴不相容，或请求 CalendarAxis/CorrespondenceAxis | 列明轴及所需字段 kind 或完整主键元组；身份名单用 identities，coordinates 只接 Field 轴；字段 Null 用 None，量值不接 None；日历用 TimeScope.by/product，已有日历与对应域用 population/project/where 复用 |
| Population.classify 的 by 收到日历、对应或其它身份轴 | 只读取单值 Field，并可保留接收者的原 MemberAxis；已有坐标用 project，关系像用 image 的 via 元组 |
| 本地时刻存在 DST 歧义或不存在 | 提供明确偏移或带时区值；范围/since/at 复用精确 InstantAnchor，不用 end 减 tick 近似边界前版本 |
| 时间构造器收到 naive datetime | 提供带时区 datetime；需按报告时区解释的日历边界使用该构造器支持的日期或无偏移字符串，来源字段仍遵守来源时间合同 |
| ordinal 两端粒度/日历不相容或含截断桶 | 重新构造同粒度、相容日历且边界完整的观察；不自动重采样、补桶或把 outer 当作修复截断桶的办法 |
| previous_year 的日粒度 exact 配对包含无对应的 2 月 29 日 | 明确改用 outer 保留缺侧，或重新选择期间/粒度；不夹到 2 月 28 日，也不减 365 天 |
| 要求按观察期间采样/时间样本规约的依赖，或日历输出收到 during=None | 指出真正需要期间的依赖或输出日历，要求显式 during；普通来源规约仅有时间角色不触发此错误。日期过滤或属性时点不替代采样/时间样本规约/日历绑定，不读取来源猜范围 |
| Field.via、image/preimage 或 corresponding 的 via 收到单个 RelationshipRef、list、空元组或断开的路径 | 单跳用 (relationship,)，多跳用声明方向连续的非空元组；列实际断点与所需端点，不自动包装、反向或用空路径表达恒等 |
| preimage 的 targets 是字段域或另一 Entity 身份域 | 指出声明关系的目标 Entity；使用该 Entity 的身份域，不能按字段同名自动转换 |
| FieldPredicate与ColumnPredicate混用 | 指出当前作用域及各输入归属，分别在观察/成员或结果上构造条件 |
| 未绑定域或域轴不相容 | 列实际轴/角色及缺失映射；由合法接收者绑定，不能凭标签或改名修复 |
| Query 上卷缺合格的充分信息执行路线 | 指明所需内部键/组件及未支持的 stage，重新构造目标观察；不能以完成均值替代或建议 Artifact 续算 |
| 名单谓词无合格后端类型/表达式路线，或超过已知后端原生限制 | 复用 DomainPreparationError，字段约束见[架构 §9.2](analysis-implementation-architecture.md#92-名单和多来源的执行内交接)；由调用方重建范围或改用已有合格来源路线，不转成各列 IN、临时表或隐式全源读取 |
| 显式 error 政策下的零分母 | 报告实际行/Metric/Column 或 share/compare/weighted_mean 节点、政策和有界坐标；期望非零分母、实际为零；修复输入或由用户明确选择本算子的 null 政策，不要求搬动表达式阶段，不自动降级；失败不发布部分结果 |
| 显式严格 NA 检查或实际日历/映射/请求域覆盖检查失败 | 报告实际算子、政策和有界坐标/作用范围；修复输入或明确重建观察范围；zero_denominator="null" 不覆盖这些错误，失败不发布部分结果 |

裸Ibis错误若发生在进入Marivo前，Marivo不能保证拦截；
静态Help应提供阶段选择示例，进入Marivo后的typed repair才有上述义务。
不承诺让 Ibis 直接接受 Column，也不提供 Field/Metric/Column 之间的自动转换。
已退出的结果处理入口不作为错误修复候选，不用回调或隐式 pandas 执行保留兼容路径。
聚合前业务量的新意义由Measure authoring拥有，阶段修复不授予新权限。

实例只披露自己拥有的事实：Query 负责定义和执行要求，Artifact 负责输出数据与已提交事实。
compare 公共侧名统一 current/baseline，align 使用 left/right；输出坐标、presence 和错误位置一致。
默认来源信任不标为已检查完整性；首版不提供外部业务缺口声明或消费，实际检查的边界见 [DSL §4.9.3](lazy-analysis-dsl-redesign.md#493-覆盖缺桶与执行检查)。
Artifact 中的 Evidence 是阅读材料，不参与后续 DSL 授权；外部处理不回放 lineage。

### 2.4 检查时机与能力披露

在信息足够的最早边界检查；不能把已知非法输入一律延迟到 execute，也不能为提前判断而读取业务数据。

| 边界 | 可判定内容与行为 |
| --- | --- |
| 构造/声明 | 已解析符号、接收者阶段、已知类型/单位、轴形状、Session 归属及政策；已知非法立即拒绝 |
| per/observe/align 等绑定 | 补齐外部格子、内部样本、时间及对应关系后检查其要求；绑定前缺少的上下文作为明确要求保留，不宣称已经支持 |
| execute 准备 | 解析必要 schema，按后端、方法和输入形状核对类型、share 共同输入、rollup 充分信息及名单传递路线；无合格路线在依赖它的业务读取前失败 |
| stage 实际输入就绪 | 实际键集/规模、配对、覆盖、NA、零分母等数据条件；动态名单求出后检查原生限制并实例化既定模板，必要检查完成前不发布产物 |

contract 与构造/绑定共用纯规则，显示已知可用操作、已知缺口及尚需绑定/schema/数据的条件；
它不读取 schema 或业务值，也不把“逻辑合法”或“等待检查”表述为后端已支持。
已有信息足以确定 share/rollup 无合格路线时直接拒绝；仅因未选择后端或实际输入未到位而未知时，
保留具体要求，到上述所属边界检查。来源错误保留 datasource 原因，由所在层提供修复；
复用现有结构化错误家族，不新增通用能力状态机或资源预算错误类型。

## 3. pandas 导出合同

`Artifact.to_pandas()` 是普通后处理的正式出口。只读这一精确 Artifact，
无新来源读取、不刷新 Catalog、不创建 Run，也不要求先构造 display/select/reduce。
返回完整隔离副本；修改 DataFrame、其对象值或 attrs 均不改变 Artifact 或另一份导出。
读取、转换或实际内存分配失败时沿现有错误边界失败，
不抽样、截断、转成 float 蒙混过关或返回部分表。Query 不能隐式执行后导出。

采用一种固定的宽表布局，不另设 values-only/flatten 模式：
行使用 RangeIndex，按 Artifact 完整键的规范顺序排列；它只表示导出行位置，不是业务身份。
列为两层 pandas MultiIndex，层名为 `kind`、`name`。各类按下表顺序出现；
轴按保存的轴顺序，量按保存的命名顺序，presence 按规范业务角色路径序排列。

| kind | name 与内容 | 约束 |
| --- | --- | --- |
| coordinate | 每个实际输出轴的稳定绑定标识 → 完整业务坐标值 | 不用展示标签、Python 地址或行号作键；轴标签、键形状和投影从下述 axes 元数据发现 |
| value | 原输出量名 → 类型化 nullable 值 | 保留精确非缺失值与 dtype 相容的 NA；不导出 state/reason，也不补零或无穷 |
| presence | 当前输出实际拥有的业务侧路径 → 是否存在 | 非缺失布尔；保留 align 的 left/right、compare 的 current/baseline 及嵌套归属，不从 value 缺失猜 presence |

名字空间彼此分开；量名 `state`、`coordinate` 或 `revenue.current` 不会覆盖坐标或 presence 列。
coordinate 的 name 是稳定字符串标识；value 使用原量名字符串；presence 使用完整角色路径元组，
例如 `("current",)`，嵌套时保留全部分量。结构性 current_present 等在 presence 导出，不重复作为业务量。
完整复合成员身份作为同一轴中的有序键元组导出，所有分量保留原类型，
主键分量名称与类型在 axes 元数据中记录；业务含义由分析脚本说明，不用拼接字符串或某一个分量替代。
字段 None 坐标作为可识别的空分类键保留；外部 groupby 如需该组，显式使用 dropna=False。
日历坐标使用其真实桶起点；实际区间、时区与数据轴标识随元数据保存，不能只凭起点猜覆盖。
非恒等对齐保留对应坐标、两侧实际投影与 presence，不伪造单一真实日历。

`frame.attrs["marivo"]` 是新产物与重开产物相同的纯数据描述，固定记录：

- `session_id`、`artifact_id` 与 `layout_version=1`：只定位输出，不是图入口。
- `axes`：按输出轴顺序包含 `column`、数据轴 `kind`、`label`、dtype、键分量、
  必要时区/实际边界及现有两侧坐标投影。`column` 就是该轴的稳定数据 token，对应 `("coordinate", column)`；
  不另设一份匹配 token。`label` 是脚本提供/生成的描述文本，不是可自动解析的 Field/Entity Ref。
- `quantities`：输出量名、kind、dtype/nullability 和单位文本，不含公式、Metric 定义或算子政策闭包。

`axes[].kind` 是闭合集合，其值和数据含义如下：

| kind | 数据轴 |
| --- | --- |
| `member` | 完整 Entity 身份键；复合主键仍占一个轴 |
| `field` | 字段分类值，包括合法的 None 分类 |
| `calendar` | 真实日历桶坐标及其实际边界 |
| `correspondence` | 非恒等对应坐标及已输出的端点投影 |

轴顺序沿用 Query 的已绑定输出轴次序；`by`、`classify` 和 `project` 显式列出的轴保留其声明顺序。
脚本已知唯一 kind 时用单元素解构定位；多个同 kind 轴按该脚本声明的输出顺序解构，随后用 `column` 取列。
不能从 label 解析 Ref 或用首个同名轴猜测；轴数量不符合预期时应失败，而不是选择另一个轴。
复杂对应使用实际输出 token 与端点投影路径定位，不把对应轴当作普通成员或日历轴。

导出没有语义对象、计算图、内部组件或 evidence 证明链。Finding/Evidence 通过产物阅读接口读取，
不重复塞进每行或 pandas attrs，也不使 DataFrame 成为带权威的 DSL 输入。
同 label 多角色时按实际数据轴 token/投影路径选择，不取第一个同名项。
只有输出坐标和值进入行表；需要分子/分母等后处理组件，应在原观察中明确输出。
任何 Artifact 均为终端产物；导出后继续使用 pandas。

int64/较大整数、Decimal、布尔、字符串、date、带时区 instant 和 duration 按其保存类型
使用能无损表达的 pandas nullable/Arrow dtype 或精确 Python 标量；
不因出现缺失将大整数/Decimal 强制转 float，也不去时区或降低时间精度。
这里保证已提交产物到导出副本的无损往返；来源类型准入由 §1.5.1 拥有，不能以导出成功替代来源路线资格。
空结果仍保留全部列、dtype、轴与量元数据；零轴单例与零行空域保持区别。
仅坐标 Artifact 没有 value 列，身份轴仍保留完整键；不插占位 count。

调用方直接取出普通值表，无需解码 Cell。下例的全非空检查仅是该项外部统计的样本要求，
不是导出限制；普通缺失可以按统计方法显式忽略。

```python
exported = result.to_pandas()
values = exported["value"].copy()
if values.isna().any().any():
    raise ValueError("This statistic requires non-missing input values.")
```

配对统计使用一份共同非 NA mask；sum 默认忽略 NA 且空/全 NA 为 0，mean 以有效值数作分母。
数据集合法执行时已通过必要失败检查，不要求外部示例再拒绝已删除的 Unknown/Undefined 状态。
presence False 与值为 NA 仍是不同事实。跨表计算明确完整业务键、唯一性与缺侧，
不用 RangeIndex 对齐；包含空分类时 groupby(dropna=False)，不能借 pandas 默认行为改变 Population。

pandas 可以继续保留或复制 attrs，但任意变换后的 attrs 均不能证明单位、覆盖、
贡献、充分状态或身份绑定仍有效。外部输出是普通 pandas 数据，不接受回灌成可信 Artifact，
也不新增 from_pandas/回调/自动 Metric 转换。需要后续主体观察时，
由脚本显式提供完整键及当前语义绑定，作为普通新输入；不能传入 Artifact 或仅凭 attrs 恢复权威。
此边界不是扩张 DSL 统计清单的理由；终端分析正常结束于 pandas。

## 4. 实施衔接

本提案复用现有 Runtime；目标表面、执行内 stage 组合和终端 Artifact 保存合同仍待实施。
本地忽略计划 `docs/superpowers/plans/2026-10-13-lazy-analysis-dsl-redesign-implementation.md`
映射 R1–R8 模块与验收；它不是分发文档依赖。实施前刷新当前 owner；技能变更仍需独立明确授权。
