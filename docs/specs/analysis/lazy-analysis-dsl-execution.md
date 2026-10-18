# Lazy Analysis DSL：执行、保存与结果交接

Date: 2026-10-17

Status: v0.37 执行合同；基于 `405bd006f8`，原生 Deferred、R3–R8 组合与 Store 10 终端执行/读取
已完成约定的本地实施、常规门禁和独立业务验收。远端数值 Query 按实际后端、表达式、类型及方法
准入；代码路线与真实 Runtime 证据分开记录，见 [Runtime 覆盖说明](../../testing/runtime-coverage.md#remote-numerical-query-extension)。完整 slow 和 release 验收未完成。
2026-10-17 来源交接修订：接受 Ibis/provider 原生返回的数值精度损失，后端返回类型差异在 pandas 边界处理，并向本地消费者和最终输出 schema 传递有效类型（§1.5.1）。
v0.37 增补有序/近似/nullable sum、membership 与窗口的执行边界；具体资格由实现和执行前准入界定。
2026-10-17 fanout 修订采用贡献语义约束，允许已证明等价且已准入的不同物理策略；新增边界见 §1.1.4。
v0.36 复用 schema preparation 自动绑定原生 Ibis 数值图；详细语义由语义提案 §2.6/§3.6 拥有。
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
2026-10-18 格式切换至 Store 11，删除旧 Artifact 消费关系；终端输出 v1 编码保持不变。
S0–S6 已实现原生 Deferred 摄入、自动类型准备、有序/近似规约、nullable sum、membership 与基础窗口。
Population、Session.read、Query 值操作、对应/广播及原指标组合已完成 R3–R8 约定的本地验收。
这不授予未准入的方法或 preview 扩展资格，也不构成全部远端 Runtime 或 release 验收声明。

## 1. 执行、保留与恢复

### 1.1 图与来源分类

Session.read(population, fields) 显式捕获属性读取所属 Session、完整身份域和命名字段。
纯字面量域在此消费处绑定；已有 Session 归属不得转移。没有 Population.read 别名或 ambient Session 查找。
读取与版本/路径依赖进入本次 Query 计划，构造不读业务行；必需映射缺失阻止发布。
identities 只捕获已解析 Entity 及完整键，不接受裸 EntityRef 或隐式 Session 查找。
Query.coord 仍无源读取地构造 Column；准备期按完整键 schema 拒绝复合坐标作单量，
早于 Run 准入及业务 compile/read；完整键、Population 元组谓词和导出保持合法。

复用当前定义图、compiler、物理内核、物化、原子发布和 Store，不建第二 Runtime。
私有节点包含 Population 的来源身份选择/坐标运算、Session.read 的显式属性读取、Metric 域依赖/贡献过滤、
原生 Ibis 数值子图及语义注解/Measure 声明、来源规约、Metric 组合/PerReduce、membership/window 绑定、Observe、Query 变换与 Values。
自定义 Measure/Metric 数值 AST 由 Ibis 替代；语义边界仍归共同定义图，不建镜像数值 AST。
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
普通窗口分支遵循同一读取合同；即使基础贡献作用域相同，也不强制共享扫描、共同输入或快照，
不因缺少这些保证而拒绝一般窗口算术。命名/内联不改变此规则。
first-seen 的 H/W 改写只在两者实际消费同一合格历史 U 时具有迁移等价性；稳定输入、合格共同输入路线
或已有后端一致性保证可满足前提，定义/变量/哈希相同不能证明。未证明时不宣称 first-seen 等价，
但不为普通表达式增加执行门禁，也不通过识别公式或名称偷偷启用特殊读取模式。
可在已有路线允许时共享输入；不新增一致性探测、全局快照或固定结果 API。
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

#### 1.1.3 五项扩展的执行边界

有序/近似规约与 sum(empty=) 仍在原 SourceAggregate/PerReduce 边界检查；membership 和 window
是贡献/坐标绑定描述，不复制 Ibis 数值 AST，也不增加第二 Runtime。模型导入和声明只捕获配方。

| 能力 | 准备与执行必须保持 |
| --- | --- |
| first/last | 识别唯一时间排序、include_null 和 nulls；先按同时点完成子指标再选择。不能用未排序的后端 first/last；严格 NA 检查覆盖完整被需求样本域，不限于最后输出的一条 |
| approx_* | 检查真实 dtype、定义精确性、q 及具体后端/本地路线；实际算法/版本进入绑定指纹与必要结果描述。未知算法误差不能写成有界保证；无路线在业务计算前拒绝，不隐式改成精确或反之 |
| nullable sum | empty 在真实 Ibis lowering 前由语义适配消费；需要时共同取得 sum 与有效值支持，再按政策完成。空输入/全 NA 与有效零区分，严格 NA 先检查，缺坐标不被造出 |
| membership | 每根逐描述优先采用唯一合法显式路径（含自关系），无路径时才推导相容恒等绑定；描述内保留相关元组，描述间按集合乘积组合，逐因子处理 unmatched，再按完整贡献键/桶键对去重。请求 K 与分配关系独立，coordinates/classify 的显式绑定保留到 per/observe；完整键跨源传递需合法路线，各根独立规约 |
| window | 唯一输入为 Metric.window 的无轴规则，加 during 的期间/日历；绑定到 W(t) 后保留实际出现位置。observe 固定逻辑时间绑定，后续只运输，不重绑；窗口原量的日历轴粗化/替换/删除在规划时拒绝。普通分支不新增共同输入要求。标量 to_date 以 left < end <= right 选重置期间，不检查显示起点属于该期间；日历桶唯一包含另检。固定 duration 不换成 civil period |

只能在证明输入、政策、身份、顺序和数值路线等价时使用预聚合、键集合去重、共享扫描或 sketch 合并；
aggregate_then_join 是候选优化，不是必须采用的执行骨架或公开 policy。具体要求见 §1.1.4。
共享数值结构不共享不相容的桶/窗口/NA 政策；必要检查保留原语义使用位置。
条件分支需求先选输出端点/桶，再展开其完整贡献；first/last 和近似计算不能偷偷缩小所选样本上的 strict 检查域。
声明中未选中的分支仍做静态与 schema 检查。对被需求数据的映射、采样和严格错误阻止整次发布；
不做未被请求贡献的全表健康扫描，也不声称消除来源连接/解码错误。
直接从贡献求值是基准语义，不要求先提供优化摘要。中间支持数、成员键和 sketch 只在本次计划按需保留；
Artifact 保存实际输出及必要算法/规则说明，不保存可执行窗口、成员程序或合并状态，重开不能继续 DSL。

#### 1.1.4 Fanout 的执行准入

[语义 §3.7.1–§3.7.2](../semantic/expression-model-redesign.md#371-fanout-的表达方式与能力边界)
拥有贡献单位、表达方式和等价条件；执行计划消费这些事实，不按 SQL 中 join/aggregate 的文本顺序决定合法性。
逻辑上的多根独立规约允许物理阶段融合，也允许先沿合法关系取键再按原根身份回取数值，
但不能用关系乘积的行数代替某个根的贡献次数。存在性选择、membership 集合出现和真实关联事件须分别保留。

规划器只选择已有具体资格的路线；无需建立通用策略枚举器、公开 fanout_policy 或成本优化框架。
预聚合必须保留完整目标键、内部样本/采样键、成员归属、原过滤/时间/窗口、必要检查与充分状态。
不能先压到最终展示键再尝试恢复逐客户子式，也不能合并完成 P95、独立去重数或重叠桶完成值冒充原量。
count_distinct、mean、quantile 等不因非可加就一律拒绝：直接消费合法贡献仍可获准，摘要合并单独判断。

计划可以使用单值关联、存在性半连接、完整贡献键/桶键集合去重、预聚合后关联或分支规约后对齐；
它们均须满足原 NA/empty/未匹配/配对政策、外域覆盖、检查需求域和方法精度合同。
去重不能跨使用位置、输出窗口或根，也不能抹去独立关联事件键。分配权重必须来自明确贡献模型，
执行器不自动均分、归一化或引入匹配程序。无法构造等价计划时保留细粒度直接求值路线；
若该路线也无资格，则报告具体缺失绑定、信息或后端/交接能力，不替换业务问题。
等价计划的比较以相同实际输入为前提，不追加跨源快照、共同扫描或全表数据健康验证要求。

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
本地整数/Decimal 在各自运算类型、溢出及最终舍入合同内保持精确语义；来源计算与返回接受 Ibis/provider 的原生数值精度（§1.5.1），不承诺跨后端逐位一致。
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

#### 1.5.1 Ibis 原生返回与 pandas 类型交接

来源 schema、表达式编译和执行由 datasource/Ibis 拥有。相同 Ibis 表达式在不同后端可以返回不同
数值类型和精度；接受后端、驱动及 Ibis 原生转换造成的数值精度损失，不以无损传输作为路线准入条件。
Marivo 不在 Ibis 层为统一 dtype 或保真主动添加与业务语义无关的 cast，不另建 cursor transport。
DuckDB 读取使用 Ibis 的 Arrow 结果，将整数列直接转换为 pandas Arrow extension dtype，
保留原生整数值及 null，避免 pandas 经 float64 推断而丢失精度；本地计算框架仍为 pandas。
其他后端继续使用各自 Ibis pandas 返回路径，不由 DuckDB 的验证推导其精度保证。
单位换算、时间角色/时区、明确的运算规则、空值及错误政策所必需的转换仍由对应算子拥有；
精确/近似算法不能相互替换。数值精度容忍不改变请求时间边界、Duration 单位或完整键的业务含义。

后端返回类型差异在 pandas → StageOutput 的已有规范化边界处理，覆盖直接字段、规约值及内部组件。
结合逻辑类型与实际返回 dtype/标量，衔接 nullable 整数、浮点、Decimal 和时间表示；
不能只因返回 dtype 不同于来源声明而拒绝，也不能盲目 cast 回声明类型、截断小数或把无效值转成 NA。
保留列序、完整键、nullability、缺失 mask、时区和单位；无法满足这些业务约束时沿现有结构化错误失败。
本地消费者和输出 schema 使用交接后的有效类型，不伪称已恢复来源精度，不允许各算子重复猜测后端类型。

schema-only 准备检查表达式、类型族及后端方法能力，不扫描业务值，也不证明数值无损。
来源模板在 Run 准入前使用实际后端编译，时间准备和 temporal rewrite 也使用实际 engine；
动态依赖实例化后仍校验其具体输入。后端不能实现请求算法时报告具体方法缺口，
不能因编译器提供近似实现就接纳精确方法。远端来源块保留未融合路线，直到具有自身等价证据。
当前 PostgreSQL、MySQL、Trino 和 ClickHouse 的普通数值路线按实际类型准入 count、count_distinct、
sum、min、max、mean 和 weighted_mean。精确来源 median/quantile 只在 DuckDB 和 PostgreSQL
准入；MySQL、Trino 和 ClickHouse 在 Run 前按具体方法拒绝。ClickHouse 的 Ibis Quantile
lowering 使用近似算法，不能据此执行精确请求。这些代码准入不替代逐路线 Runtime 证据。
实际返回布局和值约束在交接时校验；DuckDB 大整数搭配 NA 必须保留 Arrow 返回的整数，
其余原生数值转换按各后端实际精度验证可交接性，不要求跨后端逐位相等。
完整键唯一性、NA/空输入、配对、分组和失败政策仍须成立。
这些工作复用来源绑定、物理准入和 StageOutput，不增加类型证明平台、全局精度开关或失败后换引擎。

精度容忍属于来源计算及其 pandas 交接；交接完成的值进入本地算子后仍服从该算子的数值合同。
产物按最终输出类型保存，已提交值的重开/导出保持保真，不把来源原始类型当作恢复精度的依据。
Artifact 往返成功只证明保存值可读，不能反向证明来源转换无损或跨后端答案一致。

## 2. Agent 读取与错误契约

Help 拥有静态语法；Catalog 定义条目和 Query.contract 披露指标定义、规范政策、显式依赖、归属及待检查要求，
Population.contract 拥有域定义和已有绑定，Query.contract 拥有本次逻辑上下文；
Artifact.show 展示已提交答案与有界 Finding/Evidence 摘要。
Artifact 没有 DSL contract；有 Top-N 展示次序时沿用，否则按完整键规范排序。
默认政策由 [语义提案 §3.4](../semantic/expression-model-redesign.md#34-样本政策的归属) 和
[DSL §4.4](lazy-analysis-dsl-redesign.md#44-最小规约与有效样本) 拥有，实例只显示其已解析值。
执行内输入合同集中在§1.1.1，各算子说明自身需要的局部依据。
Population/Query contract 与新/重开产物阅读的完整调用见[场景 §8.7](lazy-analysis-dsl-scenario-validation.md#87-构造披露执行后阅读与重开)。

### 2.1 repr 与输入权威

Marivo 公开结果和定义条目提供 bounded 单行 repr，不触发业务或 schema 读取；原生 Deferred 的 repr 不承担该结果披露合同。
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
| Ibis cases/least 等混合行/Metric/结果阶段或使用未知规则 | 同阶段且单位/贡献相容的行或 Metric 数值式可以直接使用；混合阶段与未知操作拒绝，Query 结果普通处理交 pandas |
| 来源Metric由裸后端Ibis表/memtable拼入 | 指向合法Entity与声明字段，说明缺少的来源/单位/权限，不能转型绕过 |
| @ms.metric 收到来源表参数、自由行值、外部 Ibis 图或全局计算政策 | 零参数体返回合法 Metric 阶段的受控原生 Deferred；带关系的裸 Value 不作为作者入口；政策归实际算子，不能仅凭 Scalar shape 放行；不创建 Session 或执行查询 |
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
| 算子政策不适用或 lowering 无法保持 | 指出实际节点和政策；例如 sum 的 empty 只能是 zero/null，mean 不接 empty；无合格路线明确失败，不退回后端默认 |
| Column属于另一输入域 | 先按已有合法映射align/broadcast，再构造结果表达式 |
| 已知单位不相容，例如 CNY + USD | 行/Metric 最早可检查的构造/消费边界报 SemanticError，Query Column 构造报 AnalysisError；列明双方单位与位置，要求显式合法换算后重新组合，不隐式换汇 |
| 独立参照列单位缺失或不相容 | 列明参照列及双方实际单位；用 values 的 columns={"budget": "CNY"} 等映射创建新参照，裸常量不携带单位，旧结果不补写单位 |
| values 的 columns 收到嵌套列定义或缺少行中量名 | 直接提供完整的列名到 str / None 单位映射；"1" 为无量纲，None 为明确未声明，量名与每行完全一致 |
| TimeScope.by/coarsen/to_date 的 grain 收到字符串，或 by/coarsen 另收到 count | 使用 mv.grain(unit, count=...) 或已加载认证日历的 grain(level)；粒度消费者仍校验合法分桶、粗化或包含关系 |
| identities/coordinates/values 手写键与轴不相容，或请求 CalendarAxis/CorrespondenceAxis | 列明轴及所需字段 kind 或完整主键元组；身份名单用 identities，coordinates 接 Field 或显式 MembershipAxis 展开的字段轴并保留分桶绑定；字段 Null 用 None，量值不接 None；日历用 TimeScope.by/product，已有日历与对应域用 population/project/where 复用 |
| Population.classify 的 by 收到日历、对应或其它身份轴 | 读取单值 Field、保留原 MemberAxis，或对 owner 身份相容的 MembershipAxis 直接读取目标成员分类；不在 classify 执行事实分配路径。已有坐标用 project，关系像用 image 的 via 元组 |
| 本地时刻存在 DST 歧义或不存在 | 提供明确偏移或带时区值；范围/since/at 复用精确 InstantAnchor，不用 end 减 tick 近似边界前版本 |
| 时间构造器收到 naive datetime | 提供带时区 datetime；需按报告时区解释的日历边界使用该构造器支持的日期或无偏移字符串，来源字段仍遵守来源时间合同 |
| ordinal 两端粒度/日历不相容或含截断桶 | 重新构造同粒度、相容日历且边界完整的观察；不自动重采样、补桶或把 outer 当作修复截断桶的办法 |
| previous_year 的日粒度 exact 配对包含无对应的 2 月 29 日 | 明确改用 outer 保留缺侧，或重新选择期间/粒度；不夹到 2 月 28 日，也不减 365 天 |
| 要求按观察期间采样/时间样本规约的依赖，或日历输出收到 during=None | 指出真正需要期间的依赖或输出日历，要求显式 during；普通来源规约仅有时间角色不触发此错误。日期过滤或属性时点不替代采样/时间样本规约/日历绑定，不读取来源猜范围 |
| Field.via、image/preimage 或 corresponding 的 via 收到单个 RelationshipRef、list、空元组或断开的路径 | 单跳用 (relationship,)，多跳用声明方向连续的非空元组；列实际断点与所需端点，不自动包装、反向或用空路径表达恒等 |
| preimage 的 targets 是字段域或另一 Entity 身份域 | 指出声明关系的目标 Entity；使用该 Entity 的身份域，不能按字段同名自动转换 |
| FieldPredicate与ColumnPredicate混用 | 指出当前作用域及各输入归属，分别在观察/成员或结果上构造条件 |
| 未绑定域或域轴不相容 | 列实际轴/角色及缺失映射；由合法接收者绑定，不能凭标签或改名修复 |
| membership 的某根有多条显式路径、显式路径无效，或无路径且无法恒等绑定 | 列明根、目标 owner 和问题路径；提供唯一合法路径，只有未提供路径且身份相容时才推导恒等。合法自关系不是歧义，错误路径不能靠恒等回退掩盖 |
| 请求隐式关联次数、自动分摊或通用跨根事件匹配 | 按语义 §3.7.1 指明当前贡献单位；真实关联/分配使用明确来源及完整键，无法由受支持来源构造的配对由上游提供；不建议修改 fanout_policy、按量值去重或放宽仅限条件/坐标的路径 |
| fanout 候选预聚合丢失内部键、贡献身份或充分状态 | 保留所需细度并选择已有资格的等价计划；无路线时列明缺失信息或执行资格，不先压到展示键、不合并完成 P95/去重数，也不新增跨源快照要求 |
| window 原量的 rollup 请求粗化、替换或删除已绑定时间轴 | 规划时指出原时间绑定、目标轴与 window 依赖位置；保留原轴做合格空间上卷，或用新 observe 明确绑定目标粒度。格子统计用 reduce；不能靠追加摘要、单桶/count=1 或换后端绕过 |
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

原生 Ibis 的命名/匿名输入共用[语义提案 §2.6](../semantic/expression-model-redesign.md#26-原生-ibis-与自动延迟绑定)。
作者侧命名与匿名值始终为受控原生 Deferred，已知 schema 不提前切换成裸 Value。
消费时先遍历获准 Resolver 配方，发现完整闭包、语义方法、政策和使用位置，再进入 schema preparation；
内部解析器拦截语义方法，其余调用委托获准 Ibis 构造器，不对混合配方直接原生 resolve。
execute 准备自动获取完整必要闭包的 schema，解析受控 Deferred，取得真实 operation DAG 后完成物理 numeric dtype、
单位/阶段/根剩余义务、政策 lowering 和路线资格检查，再进入依赖这些事实的业务计算。
语义检查不能只检查 DAG 的返回类型；条件、所有分支、规约参数及原始依赖义务都要保留。
不增加 ms.require、手工 schema、假 float64、来源全表预检或公开准备生命周期。
schema 获取可能有网络/文件 I/O；contract/observe/load 保持无来源 I/O，显式显示 dtype 未绑定。

源端可编译不证明跨源 Metric 数值函数有合格本地内核。先逐根规约，再以完整坐标键交付 pandas；
聚合后的 sqrt/ifelse 等仍须在该位置执行，不能为了下推改成逐行运算或隐藏 DuckDB 本地求值。
本地能力缺失在准备阶段给出具体操作/类型缺口；Artifact 不保存 Ibis 图或延迟配方。
物理类型变化重新绑定并参与缓存键；不重跑作者函数。值域、NA、除零、溢出等检查在实际数据阶段执行。
按 Ibis operation 到合格 pandas 内核的分派继续存在；删除的是旧 Marivo 数值 AST 及其重复构图/翻译入口。

语义使用位置按逻辑作用域和操作数角色路径关联；结构相等的 Ibis op 不合并政策或权限。
相同计算多处消费保留各处需求域，完整语义相容时才允许按域并集共享执行，不改变读取保证。
source-free 声明计算指纹来自受控配方与语义事实；绑定执行指纹再纳入真实 schema/图、观察与需求域程序、
实际消费依赖及规则/路线版本。只用声明指纹不能复用已绑定计划，动态域和业务数据仍每次执行求值。

#### 2.4.1 条件分支与严格检查的执行域

完整数值语义由[语义提案 §3.6.5](../semantic/expression-model-redesign.md#365-条件分支的需求域与检查范围)拥有。
所有分支的符号、类型、单位、贡献根和权限都检查，包括未选分支；schema 准备保持静态完整闭包。
执行时 ifelse 的条件在当前需求域求值，True 进入真分支，False/NA 进入假分支；cases 按首次匹配，
coalesce 按左侧已求值结果的 NA 域继续，不捕获严格错误。普通算术、nullif、least/greatest、and/or 无隐含短路。

行条件的需求域是贡献行；Metric 条件的需求域是完整输出坐标，再映射到各子规约的完整贡献。
准备阶段验证条件到动态需求的 stage 路线，执行时计算域；不把格子条件下推成原始行条件。
本地必须先选择输入域再算 sqrt/divide 等；源端须避免无需求分支的非法计算，不能先全域求值再掩码结果。
无法保证同义时准备阶段拒绝。声明和匿名变量都不形成 eager 根；仅命名、内联和提取公共子式的结果及错误一致。
额外观察一个严格子式会增加独立根需求；后置筛选、Top-N、丢列或乘零不能取消该需求。
连接/读取/解码错误仍由其 owner 处理；此规则不保证未选分支零来源 I/O。

#### 2.4.2 检查与披露汇总

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

配对统计使用一份共同非 NA mask；sum 默认忽略 NA，空/全 NA 默认 NA，来源/内部样本显式 empty="zero" 时为 0，mean 以有效值数作分母。
数据集合法执行时已通过必要失败检查，不要求外部示例再拒绝已删除的 Unknown/Undefined 状态。
presence False 与值为 NA 仍是不同事实。跨表计算明确完整业务键、唯一性与缺侧，
不用 RangeIndex 对齐；包含空分类时 groupby(dropna=False)，不能借 pandas 默认行为改变 Population。

pandas 可以继续保留或复制 attrs，但任意变换后的 attrs 均不能证明单位、覆盖、
贡献、充分状态或身份绑定仍有效。外部输出是普通 pandas 数据，不接受回灌成可信 Artifact，
也不新增 from_pandas/回调/自动 Metric 转换。需要后续主体观察时，
由脚本显式提供完整键及当前语义绑定，作为普通新输入；不能传入 Artifact 或仅凭 attrs 恢复权威。
此边界不是扩张 DSL 统计清单的理由；终端分析正常结束于 pandas。

## 4. 实施衔接

本合同已沿现有 Runtime 接通公开表面、执行内 stage 组合和终端 Artifact 保存，本地首版验收完成。
实施计划 `docs/superpowers/plans/2026-10-13-lazy-analysis-dsl-redesign-implementation.md` 的 §8–9
记录历史证据，§10 记录本轮远端增量；本文件独立说明执行合同。远端路线仍按实际后端、
类型与方法分别准入，Runtime 证据由 [Runtime 覆盖说明](../../testing/runtime-coverage.md#remote-numerical-query-extension) 记录；
完整 slow 和 release 仍未完成。R3–R8 的授权技能同步已完成，后续技能变更仍需明确授权。
