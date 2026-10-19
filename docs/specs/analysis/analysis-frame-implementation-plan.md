# AnalysisFrame 完整实施方案

Date: 2026-10-19

Status: planned，尚未实施。代码核对基线 `acfc03617a`；目标合同为当前工作区中的
[AnalysisFrame 数据对象与执行架构设计](query-result-reuse.md)。该设计仍是未提交提案，
本文不把它或本文的阶段清单当作现有功能及验收证据。

本轮交付实施方案与索引，不修改运行代码、当前 API 合同或 packaged skills。
实施目标是一次完整公开切换：**以单一 AnalysisFrame 承载显式惰性计算和固定结果，
先完成数据、坐标、求值与保存闭环，再移除 Query/execute 和 Artifact 的数据入口。**

## 1. 依据、范围与完成定义

### 1.1 合同权威

| 文档 | 本方案中的用途 |
| --- | --- |
| [AnalysisFrame 设计](query-result-reuse.md) | 本次目标 API、结构、载体、日历对应、审计、行序及退役范围的唯一权威 |
| [Python Analysis Design](python-analysis-design.md) | 切换前实际公开对象与来源边界 |
| [Session State and Runtime](session-state-and-runtime.md) | 切换前 Store 11、调度、资源与原子提交基础 |
| [Implementation Architecture](analysis-implementation-architecture.md) | 当前模块分工与来源计算内核 |
| [Operators and Frames](operators-and-frames.md)、[Algebra Theory](algebra-theory.md) | 完整键、统计、NA、单位及信息充分性 |
| [Timezones and Calendars](timezone-and-calendar-design.md) | 来源绑定与时间解释；数据侧匹配按目标设计的冻结目录合同收敛 |
| [Attribute design](attribute-design.md) | 后续依赖边界，不能将尚未定稿的来源归因 API 并入本次 |

实施开始时记录目标设计的 Git blob 或文件摘要及代码基线；设计有实质变化时，先更新
对应任务和验收项，避免各阶段消费不同版本。当前合同与目标不一致的地方是切换任务，
不能在基础阶段提前把 current 文档标为已实现。

### 1.2 完整交付范围

- 一个公开 AnalysisFrame 类型、FrameAxis 句柄及 expression/memory/stored 三种私有载体。
- observe/read/values/frame 全部入口；compute、persist、artifact、show、to_pandas 完整生命周期。
- select/列算术、where(Column)、全局 top、已有完整轴 reduce、单分量 coord。
- 全轴 align/compare、exact/outer、明确 on 或两种唯一推导、独立与继承 presence。
- 冻结日历目录、ordinal、限定 builtin previous_year/period_offset、单层配对轴。
- session.population_from 的普通字段/完整身份轴桥接及与显式来源计划的组合。
- 新 Store generation、无损保存、冷恢复、来源隔离、内存生命周期、审计和发布故障处理。
- Query 及相关退役路线清理，Help/错误/动态指引/CLI/签名/快照/latest EN/ZH 文档同步。

不纳入自动历史匹配、结果缓存、TTL、spill、跨 Session 导入、固定数据上传远端、
配对轴再次对齐、广播、任意关系对应、日历粗化、分区 top、自定义 codec、
原量 rollup/drill/share 或 attribute。现有来源 Metric 计算需要的内部组件继续保留。

### 1.3 完成条件

只有以下条件全部满足，才可把整个方案标为 implemented：

1. 下文 S0–S8 的退出条件均有对应证据，所有首版结构均支持三种载体与冷恢复。
2. 对同一固定输入，值、完整键、presence、类型/单位、时间解释和行序一致；
   求值与发布失败不能产生不完整成功对象。
3. 纯固定数据路径在禁止 Catalog、模型和业务连接时可运行，物化对象不保留生产图。
4. 新公开面只有一套入口；退役实现和指引已按消费者核对收敛，无双读或执行别名。
5. 未缩小的 `make check-agent` 通过；实际受影响来源路线具有相应 Runtime 证据。
   未执行的 Runtime/slow/release 检查单独列明，不以本地证据替代。
6. packaged-skill 对齐如确有需要，已取得具名编辑授权并完成；未获授权时明确列为
   公开交付阻塞项，不能声称全套 agent 工作流已同步。

## 2. 当前实现核对与改造落点

以下是静态代码与 `marivo.help("analysis.query")` / `marivo.help("analysis.artifact")`
的核对结果，不是运行验收通过声明。下表给出完整路径；后续阶段清单按所在层缩写路径。

| 当前事实 | 主要落点 | 必须完成的改造 |
| --- | --- | --- |
| Query 保存 BoundGraph 和绑定到 Session 的 `_execute` 回调 | `marivo/analysis/dsl/query.py`、`marivo/analysis/session/core.py` | 数据结构、表达式和本地服务分离；物化根不能继续保活 Session/语义 scope |
| BoundSignature 混合输出量与 declared_metrics、observation | `marivo/analysis/core/model.py`、`marivo/analysis/core/graph.py`、`marivo/_definition/relations.py` | 来源绑定仅属显式来源叶子；所有载体共用独立数据结构 |
| Column/Statistic 以 graph fingerprint 校验输入，Column 捕获 QueryKeys | `marivo/analysis/dsl/column.py`、`marivo/analysis/dsl/query.py` | 精确 input_id 与当前依赖引用；计算指纹只供编译优化 |
| `Query.__getitem__` 递归生产图发现 presence | `marivo/analysis/dsl/query.py` | presence 与路径顺序进入当前结构，访问不扫描旧操作 |
| reduce 可走日历粗化，where 接受 PopulationPredicate，top 支持 partition_by | `marivo/analysis/dsl/query.py`、`marivo/analysis/methods/query_values.py` | 新 Frame 按首版签名和结构准入；不把旧扩展机械搬入 |
| ResultQuery 被直接视为需要来源准备 | `marivo/analysis/session/core.py::_execute_query`、`marivo/analysis/materialization/terminal_publication.py::execute` | 从实际叶子确定依赖；纯数据图不能触发 Catalog 或连接 |
| 一条 execute 同时准备、admit Run、求值和发布 | `marivo/analysis/materialization/terminal_publication.py`、`marivo/analysis/materialization/admission.py` | 拆成求值与发布；只有完整结果进入发布 Run |
| StageOutput 还含 layout/components/population_frames | `marivo/analysis/materialization/graph_exchange.py` | 完成边界提取独立完整数据，剥离执行状态及来源对象 |
| DataAxis/CalendarDataAxis 和 DataProjection 已存在，但不足以描述目标冻结目录 | `marivo/analysis/core/artifact_data.py` | 扩展闭合数据变体、显示名/完整键、日历/配对附件及版本 |
| writer 先按键排序，publication/export 另存并消费 display_order | `marivo/analysis/materialization/terminal_storage.py`、`marivo/analysis/materialization/terminal_publication.py`、`marivo/analysis/materialization/pandas_export.py` | 行序由算子产生，存储原样保存；删除排序补偿和顺序附件 |
| 日历目录级 exact 会拒绝桶数不同或任一目录桶无对应 | `marivo/analysis/binding/correspondence.py::_calendar_pairs` | 目录生成部分对应；exact 在实际完整行域上执行 |
| ComparisonEndpoint 的投影与行缺侧耦合 | `marivo/analysis/binding/correspondence.py`、`marivo/analysis/methods/query_comparison.py` | 名义桶映射与行级 presence 分开，不能以整行缺侧擦除桶映射 |
| TerminalReader 集中预览、导出和审计；Finding/Evidence 读取无须数据表 | `marivo/analysis/materialization/graph_reading.py` | 数据读取移交 Frame，Artifact 保留提交与 Finding/Evidence 阅读 |
| Store generation 为 11，InvocationInput 只有 query marker | `marivo/analysis/materialization/layout.py`、`marivo/analysis/materialization/store.py`、`marivo/analysis/materialization/terminal_protocol.py`、`marivo/analysis/materialization/terminal_store.py` | 升级 generation 与输出协议，Run 输入只记录待发布 result_id |

复用现有 Ibis 来源执行、pandas 本地内核、StageOutput 规范化、PyArrow、收据、writer guard、
资源 journal、事务和卡片渲染。不新建第二执行器、全局轴注册表或动态方法资格系统。
内部 QueryPlan 等名称不作无关机械替换；是否删除按实际职责与消费者判断。

## 3. 实现结构与关键取舍

### 3.1 数据结构与绑定

优先演进 `core/artifact_data.py` 的 DataSchema/DataAxis/DataProjection 和现有类型协议。
新增文件只承接实际分离出的职责，例如 `dsl/frame.py`、轴句柄和私有完整结果数据模块；
不预建通用注册表或逐算子框架。以下是职责划分，不冻结内部类名。

| 内部责任 | 内容与禁止事项 |
| --- | --- |
| 当前结构 | 有序完整轴、值槽与名称段路径、独立 presence、单位/类型、必要附件描述；不含 Metric 配方或 declared_metrics |
| 来源表达式绑定 | 显式 Metric/Field/Population、完成的语义 scope 与读取参数；只能由本次来源叶子持有 |
| 待执行结构 | 可表达尚待来源准备的物理类型约束，不能用 Any 或假定 dtype 冒充已知；show 不为具体化类型而准备来源 |
| 完整结果数据 | 具体 schema、完整行数据、所需日历/配对数据及审计摘要；不含图、语义对象、连接和内部聚合状态 |
| stored 描述 | 精确 Store/Session/Artifact/result_id、小型结构和附件收据；打开时不解码全部行和目录 |

采用闭合变体及具体类型，避免把 expression/memory/stored 塞入大量 optional 字段。
memory 内部数据不向用户暴露可变 pandas 视图；从 StageOutput 移交所有权或复制时必须
证明执行消费者不能再修改它。`to_pandas()` 的值和 attrs 均为隔离副本。

用户声明量名统一校验单个非空名称段，禁止点号、反斜线、控制字符和两个保留 presence 名。
生成列以不可变段路径加前缀，公开用点连接，slot ID 与路径分类为权威。一个 owner
负责所有 observe/read/values/select/reduce 的命名检查，错误给出显式重命名修复。

### 3.2 三种身份与载体转换

| 身份 | 使用规则 |
| --- | --- |
| input_id | 当前精确接收者；新表达式/新内存绑定使用独立 token，stored 为 Store/Session/Artifact 的稳定组合 |
| result_id | 每次成功新求值产生；纯载体转换保留，不提供公开历史查询或去重入口 |
| 局部轴 token | 当前结构目录内引用，不能证明两个输入的轴等价 |

两个等定义表达式仍不能交换句柄。两次恢复同一 stored 根可共享其稳定输入身份。
memory.compute 和 stored.persist 返回自身；stored.compute 与 memory.persist 产生新绑定，
须从返回对象重取 Column/FrameAxis/Statistic。新派生对象总有自身绑定。
row_count 是唯一输入无关统计形式，在接收者消费时绑定。

FrameAxis 的公共只读字段在 S1 列成精确清单：提供目标合同要求的局部身份、显示名、
角色与完整键解释；不暴露可执行 Ref/载体构造器。字段、repr/show 与 attribute Help 同步。
`axis(name)` 只在当前目录精确查找，缺失/重名失败；`coord` 首版只接受单物理分量轴。

### 3.3 服务和执行边界

从现有 Session._runtime 提取可共享的本地 Store/读取/发布服务，保留同一 writer guard
权威。来源配置、凭据 resolver、Catalog 和连接创建属于显式来源准备服务，不让物化
frame 通过 `_execute` 闭包、事件回调或 runtime 字段反向持有这些对象。

固定数据图可持有本地服务，但内存读取和本地求值不检查目标可写性，不申请发布锁、
创建 Run、写 journal 或临时保存结果。求值中的来源连接用执行期上下文关闭，
取消和失败完成本次资源清理；不为 compute 引入持久求值 Run 或第二套日志协议。
已有未知发布义务仍由既有 reconciliation 处理，不用重新求值解决；新发布前必须检查。

编译器在同一计划中接收来源、字面量、memory 和 stored 叶子。固定叶子经类型、键、
所有权和完整性检查后进入既有调度；同一次调用共享读取并按最后消费者释放。
纯固定图不加载来源服务；混合图仅准备明确的来源叶子，不上传固定数据或恢复旧图。

### 3.4 求值、审计与发布

统一流水线：准备实际叶子 → 编译固定 schedule → 求值与必要检查 → 完整结果提取 →
返回 memory；persist 再对该 memory 进行 writer guard/reconciliation → admission →
写文件及附件 → 同一成功事务提交数据、审计、Findings 和 Run。

结果提取先把 StageOutput.layout 转为闭合目录/附件，验证完整输出，再去除 components、
population_frames、来源资源与图引用。任何消费/最终输出检查未完成都不能返回成功 memory。
旧 publish deadline 的检查须在完整结果提取前履行，compute 不能绕过它。

审计摘要仅有两部分：继承并按完全相同记录去重的来源事实、当前新求值记录。
当前记录包含完成时间、操作类别、输入种类/身份、直接消费的持久引用和检查适用输入。
不累积父结果的本地操作历史，不沿引用追读旧结果，不保存公式或生产图 fingerprint。
纯加载、保存和恢复保留摘要；来源读取时间、求值时间和提交时间各有一个 owner。
来源事实的底层范围不能沿用当前 declaration/Evidence 中的文本截断作为权威编码。

Run 只表示发布，InvocationInput 保存已有 result_id，不复制求值事实。先求值失败则无 Run；
发布失败沿用失败 Run 和资源清理；提交结果未知只查原 Run/事务，不重算。
SessionGraph 保留 Run→Artifact，不添加 consumes/lineage 边。复制内存结果不额外记录 A→B。

### 3.5 行序与优化

where/select 稳定保序；top 双向均 NA 最后并按规范完整键破同分；reduce 按完整分组键规范序；
exact 以左输入序输出，outer 以左输入序加右侧独有行序输出。索引/唯一性检查不得改主序列。
必要执行内位置仅用于恢复这些顺序，阶段结束即丢弃。

逐项核对本地 kernel、SQL 最外层 ORDER BY、native finish、merge/concat 和文件批次拼接。
现有来源优化仅在完整键、NA、检查与行序均等价时保留；无法证明有序输出的路线保留本地
排序/finish，不以旧 SQL 已获数值资格证明新顺序。固定叶子不进入远端融合。
writer 不再排序，reader 不恢复 display_order；附件不得隐藏已 select 掉的值。

## 4. 分阶段任务与退出门槛

各阶段是同一未发布开发线上的内部交付，不形成可发布的双公开 API。
阶段可以分提交审阅，但提交需另获用户指令。S0–S6 不提前宣称当前 API 已切换；
新实现可通过私有构造测试，完整切换时移除临时适配和测试入口。

```text
S0 合同与消费者基线
  → S1 结构、身份与本地服务
  → S2 求值、内存与普通数据算子
  → S3 保存、读取、审计和事务
  → S4 普通对齐与比较
  → S5 日历与配对结构
  → S6 Population 显式桥接
  → S7 公开切换与退役清理
  → S8 冻结源码后的统一验收
```

S4 的纯数据内核可在 S2 后开发，S6 的普通键投影可在 S2 后开发；其退出验收必须等待
S3/S5 的持久与结构边界就绪。每个阶段交付代码、相应测试、受影响的披露及证据，
不把所有错误处理和文档推迟到最后一天。

### S0：冻结合同与消费者清单

工作：

1. 固定目标设计版本，将第 12 节逐条映射到本文验收 ID；记录实现基线和原工作区改动。
2. 追踪 Query、QueryKeys、ResultQuery、BoundSignature、ComparisonEndpoint、StageOutput、
   ArtifactReader、InvocationInput 及 display_order 的实际生产者/消费者。
3. 对 rollup/drill/share、corresponding/broadcast、日历粗化、partition_by、population()
   建立保留来源内部功能/移除 Frame 接收者/无替代路线清单，包括 tests/Help/示例和动态字符串。
4. 固定公开签名、FrameAxis 字段及错误检查优先级，避免实现者各自补接口。

退出：所有目标条款都有任务与测试 owner；无未解决的目标矛盾。当前未提交设计与无关
工作区改动保持可辨，不以历史提案恢复自动复用或双类型路线。

### S1：结构、精确绑定和本地服务

主要落点：`analysis/core/{artifact_data,model,graph}.py`、`dsl/{frame,column}.py`、
`session/core.py`、`materialization/admission.py` 及实际所需私有模块。

工作：

- 实现闭合结构和载体；明确普通/日历/配对描述，未求值类型约束与完整 schema 的边界。
- 实现路径命名、presence 目录、轴目录、input_id/result_id；列/轴/统计精确绑定。
- 分离来源绑定与数据描述；引入本地共享服务，移除物化对象对 Session 的绑定方法回调。
- 准备来源完成时冻结不可执行时间/轴解释；结构推导不依赖业务行或 Catalog。

退出：同定义错输入、跨 Session、旧根句柄、复合轴 coord、名称冲突和 axis 重名均正确失败；
metadata-only 操作无来源读取。类型检查通过，无新增公共 Any/宽泛 cast。

### S2：无持久副作用的求值与普通数据闭环

主要落点：`compiler/{graph_plan,graph_lowering,query_program}.py`、
`materialization/{graph_execution,graph_exchange,terminal_publication}.py`、
`methods/query_values.py` 和来源构造入口。

工作：

- 从 terminal_publication 拆出共用求值路径，按真实叶子准备，接入 memory/stored 固定输入节点；
  stored 节点的完整读取接入 S3 reader，未就绪前只验收 memory/字面量/显式来源路线。
- 接通 observe/read/values/frame 的私有新返回构造；一般 Population 的 keys-only frame 不走空 read。
- 实现 select/算术、where、全局 top、已有轴 reduce、coord，按目标规则传播结构。
- 提取完整 memory 结果及新求值摘要；compute 不执行 writer admission、输出文件写入或失败 Run。
- 统一内存预览和导出，待执行 show 只展示当前结构，所有状态在读取前校验 n。

退出：来源/字面量/一般多轴 Population 可 compute；source counter 证明重新求值可读新数据，
固定 memory 后派生不重读。Top-0、空域与 select 剪列不消除必要检查；标量空输入统计、
独立有效掩码、单位/Duration/NA 和非键行序正确。compute 失败前后 Run/Artifact/journal 不变。

### S3：统一保存协议、冷读取和发布事务

主要落点：`core/artifact_data.py`、`materialization/{terminal_protocol,terminal_storage,
terminal_store,terminal_publication,graph_reading,pandas_export,layout,store}.py`、
`dsl/artifact.py`、`session/_lazy_read_model.py`。

工作：

- 将求值数据交给独立发布路径；Run admission 移至成功求值之后，持有原 memory 不重算。
- 基于 Store 11 升至下一 generation（若实施基线未变化即 12）；同步所有格式常量、
  错误、codec 白名单、manifest 与 Run/Evidence 时间/归属验证，不在 v11 写新字节。
- 一种 Frame 输出协议保存 result_id、具体目录、数据收据和摘要。日历/配对类型和附件形状
  此阶段定义并以手工数据 codec 验证，实际算子生成在 S5 接通；不发布不完整 codec。
- 移除 writer 键排序、CanonicalOrder/display_order 生产/读取路线；按文件写入序列恢复行序。
- 数据 reader 服务 Frame 的 full read/bounded preview；ArtifactReader 缩为审计/提交视图。
- 实现私有 persisted_frame 读取和 stored 生命周期；n=0/Artifact.show 只查元数据，
  正 n 不完整导出再截断，必要附件按实际消费加载，完整读取执行全部数据验证。
- 保留 journal、文件 fsync、单成功事务及 unknown-commit 协调。发布失败不得污染原 memory。

退出：普通/复合/日历/配对描述能无损往返；坏收据、坏版本、缺附件、闭合引用矛盾明确失败。
saved.compute 后删除旧数据文件，memory 的读取/派生/再保存仍成功，stored 完整读失败。
求值无 Run、发布失败有失败 Run、未知提交不会二次求值；stored.persist 幂等且不读载荷。
旧 generation 拒读全部 Session/Artifact/Evidence/Findings 且旧字节不变。

### S4：全轴 align/compare 与 presence

主要落点：新 Frame 结构方法、`binding/correspondence.py`、`methods/query_comparison.py`、
`compiler/query_program.py`，保留既有数值政策内核。

工作：

- 构造检查顺序固定为：参数/Session 所有权 → 配对轴限制 → 双方轴数 → 推导或完整显式 on
  → 轴角色/完整类型/时间相容性；错误不提示无法奏效的补 on 或 outer。
- on=None 只推导双方零轴或各自唯一日历轴；普通轴同名/同类型不自动对应。
- 等值对齐输出共享轴；按实际完整键实现 exact/outer 和规定行序，不按行号或单轴集合匹配。
- align 保留双方所有值；compare 要求同一非空名称路径集合、兼容数值类型与相同单位，
  输出 left/right/absolute/relative 四组；缺端点或 NA 优先于零分母政策。
- 新 presence 独立生成，继承 presence 加前缀，外层缺侧置 false；select 保留全部 presence，
  reduce 一律删除全部行级 presence。boolean 列不能获得 sum/mean 权限。

退出：actual/budget 异名 align、同名不同单位拒绝、复合键/Null 分类、零轴空域、Duration、
继承 presence 和重复组合均通过三载体测试；value NA 不等于缺行。无配对轴的等值结果可再组合。

### S5：冻结日历与单层配对轴

主要落点：来源 calendar 绑定、`binding/correspondence.py`、`methods/query_comparison.py`、
S3 目录/附件 codec 及来源/本地输出提取边界。

工作：

- 来源绑定冻结完整名义域、原始 ordinal、唯一标签、实际/名义边界、timezone、grain/快照
  与算法版本；大目录走有类型/收据附件，不塞入 metadata 或重建规则 codec。
- 目录匹配纯函数不接收 how，先建立稳定部分一一对应，再对实际完整行域做 exact/outer。
- ordinal、previous_year 与 period_offset 按设计限定粒度和标签查找；不读取当前模型、
  CalendarAxis 或时区数据库来重建已保存物理边界。
- 三种 match 都先验证完整名义分区及逐桶实际完整覆盖；过滤行不能绕过部分桶限制。
- 配对键到左右桶键的映射独立于地区等完整行的 presence；缺任一目录桶可合法，双缺不合法。
- where/top 可裁剪配对映射，但保留日历原有限域/序号；reduce 保留所选轴必要映射，
  标量 reduce 移除全部坐标附件；拒绝配对轴再次 align/compare。

退出：目标设计第 12 节的闰日、月偏移、稀疏 ordinal、目录超集、半月桶、named ordinal、
等值目录冲突及 A/B 地区不同 presence 的规约反例通过惰性/内存/持久/冷恢复路线。
paired codec 不依赖整行存在性，恢复时禁止模型/来源/当前日历重建仍能完成计算。

### S6：Population 显式来源桥接

主要落点：`session/core.py`、`dsl/population.py`、`_definition/relations.py`、
`compiler/populations.py`、`materialization/query_population.py` 与现有来源键参数路线。

工作：

- 新增 session.population_from(frame, on=...)，on 必填，只选普通字段/完整身份轴，
  校验 frame 所有权、精确句柄、目标 Catalog 绑定、完整键类型与角色。
- 构造不读行；求值先完成输入选择与检查，再按所选轴的完整联合键投影去重，固定本次键。
  字段 Null 保留，身份主键完整非空；空域继续表示空筛选。
- memory/stored 桥接持固定叶子；expression 桥接每次先求值选择。复用现有显式传键路线，
  不用它恢复旧来源定义、重新 Top、逐轴笛卡尔化或把 by=Population 偷换成过滤。
- 新来源使用调用方明确提供的 Metric/Field/期间/关系；贡献过滤用 in_population，
  单 Entity 成员约束保持既有资格；日历/配对/值列桥接明确拒绝。

退出：Top 地区固定后来源变化，只调查原地区但读取新城市数值；未固定选择会重选。
空域不变无筛选、Null 类别、复合身份、类型/角色错配、多轴联合键和外侧缺行都覆盖；
本地与实际受影响远端键传输路线分别给证据，不新增未经资格验证的 provider 路线。

### S7：一次公开切换与消费者退役

工作：

1. `analysis/__init__.py` / `_public.py` 只导出 AnalysisFrame、FrameAxis 和保留类型；
   Session.observe/read/values/frame 返回 AnalysisFrame，公开 persisted_frame/population_from。
2. 移除 Query、execute、session.query、session.artifact、Artifact.to_pandas 及旧 show(n)；
   移除 frame.population/rollup/drill/share、旧对应/广播和目标延期签名。
   无受支持消费者的类型/分支/文件一并删除，不留下可执行兼容别名。
3. 保留原 Metric/per/mean 等来源所需的 components、数值检查及内部来源执行。
   删除的是退役接收者资格和后继路线，按 S0 清单逐个验明消费者，不做 components 全局清空。
4. ArtifactRef、SucceededRun、SessionGraph、FindingPage、Evidence/错误卡片的 ID 指引统一
   改为 persisted_frame(id) 及 frame.artifact。Artifact.show 只给提交/审计与返回 frame 指引。
5. 同步 native Help registry、独立 drift/reachability/budget 测试、签名、`__all__` 快照、
   CLI 和 API docstring；属性目标按字符串解析，不执行 getter。
6. 更新 current design/runtime、lazy DSL/执行/架构/例子/场景、相关代数和 Evidence 文档，
   `site/src/content/docs/docs/latest/` 与 `zh-cn/docs/latest/` 对齐。
   历史版本和归档计划保留历史含义，不全仓机械替换。
7. 编写切换说明：破坏性 API/量名/能力变化、Store 全面拒读、原版本导出/兼容环境读取路径、
   显式来源重建示例和无等价替代的延期项。RawSqlResult 保持独立 reader，不进入 Frame Store。
8. 检查 packaged `marivo-analysis` / `marivo-semantic` 是否存在受影响描述；只列出需改内容，
   未获得对具名文件的明确授权前不编辑。AGENTS.md 同样不在本次授权范围。

退出：公开面、实例指引和文档无互相矛盾的入口；所有旧引用已归类为历史、结构化退役修复
或应删除消费者。结构化退役说明不实现兼容执行。S0–S6 临时构造和适配从生产面清除。

### S8：冻结源码后的统一验收

先修复阶段测试和审查发现，再记录最终候选源码版本与未提交差异摘要，运行一次统一验收。
验收期间如修改执行代码，更新候选并重跑受影响验证；最终 broad gate 必须对应最终源码。
开发自测、合同审查和业务 oracle 验收分别记录结论，不能以同一批实现输出互相证明。
本方案不预先要求开启多 agent 或外部发布。

退出：第 5 节全部条款可追踪到证据；没有待修复的合同偏差，完整公开切换门槛满足。
本地检查、Runtime provider、slow、release 的完成范围分别记载。

## 5. 验收矩阵与独立见证

### 5.1 测试组织

优先扩展现有 `tests/analysis/session/`、`materialization/`、`numeric/`、`graph/`、
`tests/surface/` 和 `tests/packaging/` 的 owning 测试。只有独立的新职责才新增测试文件，
例如 Frame 生命周期、FrameAxis、固定数据执行和冻结日历 codec；具体文件名随实现确定。
共享数据沿用 `tests/conftest.py`、`tests/shared_fixtures.py` 及已有领域 fixtures。
修改测试前读取项目的 marivo-test-fixtures skill，不为本方案创建另一套独立 fixture 框架。

载体一致性使用同一份固定输入，对同一算子分别走表达式、memory、stored 和新进程恢复，
再比较完整有序键和值、精确 schema/单位、presence、目录/映射和审计转换规则。
不要分别重跑变化来源后强求值相等。来源行为另用可变 fixture 与读取计数证明。

| ID | 覆盖范围与独立断言 | 阶段 |
| --- | --- | --- |
| A01 | 所有构造返回一个数据类型；禁止原始构造；名称段、output/presence 顺序与 axis 缺失/重名 | S1/S7 |
| A02 | 等 fingerprint 不授权错列/错轴/Statistic；载体转换重新取句柄；同 stored 恢复稳定绑定；row_count 例外 | S1/S3 |
| A03 | 三载体生命周期表逐格验证，含同对象返回、result_id/求值时间保持、存储 persist 幂等和派生 artifact 拒绝 | S2/S3 |
| A04 | 一般多轴 Population 键表、零轴 singleton/零行、空 output_names 与 read 非空属性边界 | S2/S6 |
| A05 | 算术/筛选/top/reduce 独立掩码、空/all-NA、Duration、完整复合键、NA 排序、并列和 ascending | S2 |
| A06 | align 异名值；compare 全量名称/单位、缺端点/NA/零分母和四组顺序；presence 嵌套且非 nullable | S4 |
| A07 | on 的零轴/唯一日历自动与显式等价；普通同名轴不猜；错误优先级与完整映射检查，跨 Session/配对/轴数不被绕过 | S4/S5 |
| A08 | exact 实际完整行覆盖与 outer 有序并集；省略 match 不猜期间，7/8 月 ordinal 与同月等值、整月城市标量 | S4/S5 |
| A09 | frozen 日历完整分区、部分桶删行仍拒绝、原始 ordinal、不用 365 天同比、标签 offset 与 named 支持限制 | S5 |
| A10 | 目录多余闰日/尾桶不使实际行 exact 失败或生成空行；坏目录不能当无对应；等值日历目录合并冲突 | S5 |
| A11 | select 保域/presence、不藏删除值；reduce 删除全部 presence，保留所选轴映射；A/B 地区配对规约不拆键 | S4/S5 |
| A12 | Top 地区到城市桥接的固定/惰性选择区别、联合键去重、Null/空域/复合身份和错误来源映射 | S6 |
| A13 | 行均值 55 与整体 1000/91、distinct 4 与 3；结果不取得原量/属性/历史城市选择能力 | S2/S7 |
| A14 | 完整 memory、persist、冷恢复无生产图/语义对象/连接；固定数据与显式来源混合只读明确叶子 | S2–S6 |
| A15 | 禁用模型导入/Catalog/schema/连接后，冷读取、对齐/规约/日历匹配和再次发布成功 | S3–S6 |
| A16 | 待执行 show、n=0、非法 n、Artifact show/findings/evidence_digest 的读取边界；正 n 只做限行解码 | S2/S3/S7 |
| A17 | memory 读取零落盘，导出值/attrs 隔离；GC 后 frame/Column/Axis 保活必要数据，无 Session 反向强缓存 | S1–S3 |
| A18 | saved.compute 后旧文件丢失的独立性；坏收据/闭合类型/唯一键/缺附件/presence/矛盾映射全读拒绝 | S3/S5 |
| A19 | top/where/select/align/compare/reduce→SQL finish/local merge→分片/多 batch→恢复保持非键顺序，无 display_order | S2–S5 |
| A20 | 来源事实精确继承去重、只保留当前求值记录、直接 stored 引用；纯转换不增事件/复制边、时间各有权威 | S2/S3 |
| A21 | 待执行 persist 与 compute().persist 共用求值/发布；来源次数相同、失败无 Run；同 memory 多提交不重算 | S3 |
| A22 | 发布故障、并发 writer、丢失确认、unknown outcome 和 reconciliation；只清理自身资源、成功原子可见且不重算 | S3/S8 |
| A23 | 新协议参数/版本/归属检查；旧 generation 的全部读入口拒绝、旧字节不变，无迁移/双读/降级 | S3/S7 |
| A24 | __all__/签名/Help/字段/CLI/动态修复/latest EN/ZH 一致，退役路线无可执行别名，RawSqlResult 不受牵连 | S7/S8 |

### 5.2 必须保留的业务见证

- 用固定地区销售与异名预算验证 align 后显式比例，单独验证 compare 量名要求。
- 用月内日序、闰日、非完整月桶和稀疏地区时间序列验证目录域、实际行域与 presence。
- 用 `100/1`、`900/90` 的两行验证行均值 55，独立分子/分母算出整体 `1000/91`；
  用 `{1,2}`、`{2,3}` 验证桶计数和 4 不等于合并 distinct 3。
- 先固定 Top 地区，再修改来源排名及城市数据，验证固定选择域与新来源值分别拥有事实。
- 保存非键排序的 outer 比较与 top 结果，在新进程且模型不可用时读取、筛选、规约和再保存。

数值期望使用手工小表或独立 SQL/直接数学计算，不能调用被测 compare/reduce 内核生成。
业务验收同时检查 NA、presence、精确行序、空域及错误，不只比较聚合总数或导出成功。
冷恢复 worker 不重建语义模型；禁止调用用断言 spy/实际不可用来源环境证明。

### 5.3 故障与资源验证

复用既有 publication/concurrency/recovery worker，在求值、源连接关闭、输出提取、
staging 写入、rename、事务提交和确认读取的边界注入实际异常。
求值失败不新增 Run；资源释放失败同样不允许返回成功 memory。
发布已成功但确认丢失时恢复原 Artifact，提交未知保留原义务；不能重算、重复提交或清理他人文件。
stored 的 metadata-only 成功不等于载荷健康；正 n 预览成功不等于全文件逻辑校验通过。
不扩展到通用灾备平台、内存配额或新故障恢复框架。

### 5.4 检查命令与证据等级

每个阶段先运行实际受影响测试和模块 typing/lint，命令中的路径替换为当阶段改动清单：

```sh
make test TESTS='tests/analysis/session/test_query_artifact_boundary.py'
make typecheck TYPECHECK_TARGETS='marivo/analysis'
make lint-agent LINT_TARGETS='marivo/analysis tests/analysis'
```

首条是当前已有测试的入口示例，切换时按 owning 职责改写/重命名；不能只保留旧 Query 断言。
共享执行/存储改动阶段运行 `make test`。S7/S8 的 broad gate 必须清空缩小范围的环境变量后
运行未缩小的 `make check-agent`，包括 API docs，不用数个 focused run 冒充 broad gate。

来源/SQL finish/传键路线受影响时，通过 `make runtime-test TESTS='实际受影响测试路径'`
在配置的真实 provider 上验证。记录 provider、具体路线、候选源码和原始结果；未配置即记录
缺失证据，不能标为 qualified。纯本地路径不要求无关 provider 全覆盖。
新进程、GC 和并发等必要测试若属于 slow，运行相应 focused slow 测试并单列证据。
本次实现不运行 full release acceptance、不启动无关 MinIO；`make release-check` 归正式发布准备。

## 6. 主要风险与应对

| 风险 | 处理方式及可验证结果 |
| --- | --- |
| 把 Query 换名后仍持有来源图/闭包 | S1 拆服务，S2 完整提取，A14/A17 检查 GC、禁用来源与对象保活边界 |
| unknown physical type 被错误当完整 schema | 待执行约束和具体 schema 分开；完成前具体化并严格验证，不靠 show 准备 |
| compute 复用旧 execute 导致写 Run/临时文件 | S2 独立无发布路径，A03/A16/A21 检查副作用；不增加求值 Run |
| 目录级对应与行级 presence 混淆 | S5 两步匹配；A10/A11 用目录超集与 A/B 地区反例验证 |
| 移除 writer sort 后 SQL/merge 仍改变顺序 | A19 全链路独立次序断言，不仅测试 codec；不合格优化保留本地 finish |
| 删掉旧原量方法时误删来源 mean/per 内核 | S0 追踪消费者，S7 逐项删除接收者分支，保留相关来源回归与实际 provider 验证 |
| 审计随每次派生膨胀或包含执行对象 | 两段摘要、来源事实按值去重、当前记录覆盖；codec 闭合检查和复制链反例 |
| 新 Store 导致历史项目完全不可读 | 明确切换说明与新项目重跑路径，旧字节不动；回退使用兼容版本和原状态，不能让旧版读新 Store |
| packaged skills 留有旧入口 | S7 明确具名差异和授权状态；未解决不通过完整公开切换门槛 |

不为降低切换风险加入长期双公开 API、自动迁移、历史图恢复、隐藏别名或弱化数据验证。
若实现中发现当前合同无法自包含，返回目标设计修订，不能静默丢弃附件后降为普通表。

## 7. 实施记录与最终交接

各阶段只需维护一条可审查记录：候选版本、目标条款/验收 ID、实际修改文件、执行命令及结果、
失败与修复、剩余阻塞项。保留原始失败，不把重放 fixture 或关闭路径当作产品修复。
无需另建进度服务或自动化系统。

最终交接包含：

1. 完整目标范围和退役/延期清单，实际公开签名及最小可运行示例。
2. A01–A24 对应证据、未缩小的 check-agent 结果及受影响 Runtime/slow 的明确范围。
3. Store generation 与破坏性切换说明、旧结果读取/导出安排、提交协调限制。
4. Help/错误/动态指引/CLI/latest EN/ZH 与 packaged-skill 对齐状态。
5. 最终差异和工作区状态；未获得指令不创建 Git commit、不 push、不发布包。

本方案的落盘完成不表示上述实现阶段完成。后续从 S0 开始执行，完整公开切换以 S8 的最终
源码证据为准，attribute 继续按其独立提案推进。
