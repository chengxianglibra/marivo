# Attribute 实施计划

Status: implementation started，2026-10-20；当前阶段及证据见
[实施进度](attribute-implementation-progress.md)。本文件安排实施，不表示各项能力已完成。
实施使用独立 worktree；提交、push、发布及 protected 文件修改仍遵守明确授权边界。

## 1. 依据、范围与完成条件

本计划以 [Attribute 设计](attribute-design.md) 为目标合同，重点落实其第 2、6、7、8、10 节。
[架构 §6.4](analysis-implementation-architecture.md#64-compareattribute基础能力组合的高级-api)
拥有分层和复用边界；[AnalysisFrame](query-result-reuse.md) 与
[Python Analysis Design](python-analysis-design.md) 拥有现有数据、坐标和生命周期合同。
若实施需要改变这些合同，先在 owning 文档中明确变更，再同步实现和消费者。

起草时的代码核对基线为 `82c1415120`；三份目标设计文档的修订随后落为文档提交
`ab9199acfc`，本计划依据该设计版本。正式开始实施时重新记录实际 HEAD 与工作区差异，
不能用上述快照替代实施候选。[AnalysisFrame 进度记录](analysis-frame-implementation-progress.md)
已确认基础切换完成；其历史验收不代替 Attribute 的新增验收。

交付范围：

- 一个 Session 拥有的显式来源入口，完整声明两端 Metric、范围、父域对应、新分类和贡献开关。
- 默认关闭贡献，按原公式返回完整联合分组的两期值、差值和 presence。
- 开启贡献时，只派发有证明的 additive、ratio_component、linear；每端总体与全部组共享
  实际贡献输入，每个父域独立归一化，任一必要条件失败即整个请求失败。
- 返回普通 AnalysisFrame，完成规范排序；解释组件和方法摘要可保存、冷恢复，结果可继续 DSL。
- 同步签名、错误、Help、导出、动态指引、CLI、当前规范及 latest 英中文档。

完成要求：第 5 节 AT01–AT14 均有对应证据，最终源码通过未缩小的 `make check-agent`，
受影响的 Runtime 路线有单列结论，公开披露与保存合同闭合。接口、日历/配对父域交接或
方法摘要仍未定稿时，不能宣称完整实现完成。

实施保持以下取舍：

- 复用统一 compiler/scheduler、数值政策与 compute/persist；高级入口只组织依赖、规则和输出。
- 父域就是已有完整分组键；只补充所需的聚合值映射，不建立通用 join/broadcast 框架。
- 不为手写等价式新增公开 abs、sort、组件导出或方法注册表，也不借此重构 compare。
- 不引入 AttributionFrame、独立 runner/Store/预算系统、树层级、Others、近似分摊或自动方法回退。
- 保持 rollup/drill/share 退役；完成结果不保留原 Metric、执行组件或隐式回源资格。

## 2. 当前实现与主要改造落点

以下路径相对仓库根目录；是职责落点，不要求每个文件都修改。实施时先追踪实际消费者，
再确定最小文件集合，不按表机械扩建模块。

| 职责 | 已有落点 | 本次需要补齐 |
| --- | --- | --- |
| Session 与来源构造 | `marivo/analysis/session/core.py`、`marivo/analysis/dsl/{frame_sources,frame_population}.py` | 显式请求、所有权检查、父域双端交接；普通桥接资格保持原合同 |
| 来源语义与分类绑定 | `marivo/_definition/{expressions,observations}.py`、`marivo/analysis/binding/{observe,coordinates,sampling,windows}.py` | 组件角色、分区及样本证明；两端范围与父键、新分类的绑定 |
| 组件准备与共同输入 | `marivo/analysis/compiler/{observation,original}.py`、`marivo/analysis/materialization/{query_preparation,query_components}.py` | 每根共享实际输入的合格路线；保留原规约次序和有效样本 |
| 数据计划及调度 | `marivo/analysis/core/frame_expression.py`、`marivo/analysis/compiler/{frame_plan,graph_plan,query_program}.py`、`marivo/analysis/materialization/{frame_evaluation,graph_execution}.py` | 将调查、检查、父域聚合/映射和贡献算术纳入现有计划 |
| 对齐、算术与输出 | `marivo/analysis/methods/{frame_comparison,frame_values,numeric_state}.py`、`marivo/analysis/core/frame_structure.py` | 复用完整键/presence/nullable 内核；增加必要映射与规范输出整形 |
| 结果事实与保存 | `marivo/analysis/core/{frame_data,frame_audit}.py`、`marivo/analysis/materialization/{terminal_protocol,terminal_storage,terminal_publication}.py` | 不可执行方法摘要、传播和版本化 codec；沿用同一发布路径 |
| 披露与消费者 | `marivo/analysis/{__init__,_public}.py`、`marivo/analysis/session/*disclosure.py`、`marivo/introspection/`、`tests/surface/` | 唯一入口的 Help、签名、错误、预算、导出和文档一致性 |

当前 Frame 表达式已有 source/fixed 输入及数据操作，但没有 Attribute 请求与完整贡献节点。
`query_components.py` 的单次读取组件传输可作为复用依据，不能直接推定它已证明任意指标、
分区和 provider 上的总体/细组共同输入。`FrameResultMetadata` 也尚无本提案的方法摘要合同。

`tests/analysis/numeric/attribution_workflows.py` 及相关 attribution 测试使用终态导出后的
显式分配配方，其中还有 hierarchy/top 等不同语义。它们可提供部分独立算式见证，不能
作为新入口、共同输入证明或目标输出协议的现成验收，也不能直接包装为生产执行器。

## 3. 实施顺序与职责

| 阶段 | 负责的工作 | 依赖 | 主要验收 |
| --- | --- | --- | --- |
| P0 | 请求、父域交接及方法摘要合同定稿 | 当前设计与实现核对 | AT01–AT04、AT13 的合同明确 |
| P1 | 显式来源绑定与默认联合调查 | P0 | AT01–AT04 |
| P2 | 共同输入、组件证明和父域映射 | P1 | AT05、AT06、AT09 的输入证明 |
| P3 | 三类贡献规则、NA 与整体失败 | P2 | AT07–AT11 |
| P4 | 方法摘要、持久化、冷恢复与 DSL 续算 | P3，使用 P0 的保存合同 | AT12、AT13 |
| P5 | 公开入口与披露同步 | P1–P4 | AT14 |
| P6 | 最终候选的统一验收与交接 | P5 | AT01–AT14 |

这些是职责和依赖划分，不预设多 agent、指定模型或分支策略。默认路径可在 P1 内部验收，
完整公开交付仍以 P6 为准；阶段中间状态不得披露尚未完成的贡献能力。

### P0：定稿必须先解决的合同

目标：把设计稿明确留下的接口与保存缺口收敛到可直接实现的合同，不在代码中隐式作决定。

工作与交付：

1. 在 `attribute-design.md` 定稿唯一入口及具体类型签名，复用 observe 的已解析 Metric、
   during/where/memberships 和 by 绑定方式。明确两端量名、类型、单位、根、样本政策的相容条件，
   同路径版本变化本身不构成拒绝理由。所有公开参数和返回值使用具体类型。
2. 定稿父域输入的唯一权威：两端完整键、来源绑定、对应、exact/outer 与选择域分别由哪里声明。
   分别给出单一总体、普通字段/身份父域、日历父域、配对父域的合法例子与拒绝边界。
   零父轴总体与空选择域必须区分；历史 presence 不充当新来源缺侧掩码。
3. 普通父域复用 population_from；日历/配对父域另行明确如何读取冻结双端坐标并绑定新的显式
   来源范围。保留日历完整覆盖与原对应规则，不把配对轴再次 align/compare，不放宽普通桥接资格。
   新增日历细化仍排除在首版之外。
4. 定稿每个量的输出列、顺序、单位及 presence；明确 ratio_component 的解释列、linear 子式
   事实与零变化标记的表达。方法摘要按完整父键定位，覆盖零细组父域，不保存可执行定义。
5. 定稿方法摘要在载体转换及 select/where/top/算术/reduce/align/compare 中的传播规则：
   区分“解释原结果的事实”和“当前派生值的结论”，明确可读取位置、关联身份及失效条件。
   不为这些普通数据操作增加来源依赖或新的业务求和禁令。
6. 对照 Store 12 / `marivo.frame_output/v1` 的闭合 codec，确定实际格式变更及版本处理。
   若需不兼容变更，按现有 Store 合同明确拒读边界并同步版本；不另建 Attribute Store，
   不用旧版本号写入无法被旧 codec 理解的新结构，不预建迁移或双读。

退出：以上决定写入 owning 文档，最小调用示例和类型/输出/传播表相互一致；记录真实
改造文件和 AT 验收归属。若完整父域交接仍无法闭合，先明确合同或范围调整，不能静默
把其延期后继续按完整设计发布。本计划本身不提前发明公开参数类或函数签名。

### P1：显式来源绑定与默认调查

主要落点：Session/Frame 来源构造、观察绑定、现有配对和输出结构构造。

- 构造时校验 contribution 是严格 bool、Session 所有权、当前输入句柄、两端定义与范围、
  父域映射和非空新增 by；构造与待执行 show 均不读取业务行或创建 Run。
- 元组分类和单个 Population 分别走现有绑定合同。拒绝 Population 混合元组、FrameAxis、
  重复/父域重复轴和零新增轴；完整候选域保留相关元组，覆盖检查不变成过滤。
- 每次求值先完成选择域及其检查，再固定本次完整父键用于两端新观察。固定 Frame 不重跑
  历史筛选/Top；待执行选择每次重新求值。空选择、Null 字段、复合身份保持原义。
- 复用端点对齐、差值和 presence。父域对应按请求，细组按两端完整键并集；父 exact 不要求
  细组 exact。原量按原公式求值，包括 max/P95、重叠分类和合法 NA。
- 输出 `left.q`、`right.q`、`absolute.q` 及两端 presence，不计算 relative 或贡献专用组件。
  在完整结果提取前按父键和有序 by 的规范键次序同步重排行值与 presence。

退出：默认调查与同一固定细组输入上的 observe/align/select 按完整键对应的值、差值和
presence 一致，显式处理名称及排序差异；max 等合法原量不被贡献支持表拒绝。
左 B/A、右 C/A 的调查结果为 A/B/C，裸 outer align 仍为 B/A/C；无根行、小计或 Others。

### P2：共同实际输入、组件证明与父域总量映射

主要落点：语义准备、observation/original 编译、组件读取、Frame 计划与统一调度。

- 从本次定义派生贡献需求，分别保留可加性、完整覆盖、不交性、组件角色和有效样本证明。
  count/distinct 的被计身份、window 的原时间端点、per 的内部样本及规约次序都参与判定。
  不能只按最外层 reducer 名称放行。
- 每个端点、每个贡献根采用一份实际输入产生总体与全部联合组所需数据，或从已证明充分、
  完整不交且可加的组件规约总体。复用合格的来源共享读取/执行内交接；多根分别证明后
  按完整坐标组合，不通过事实 join 放大贡献。
- 不把相同参数、同一 DAG、独立 SQL 读后对账或自动重试当作共同输入证明。物理路线不能
  保证时报告 unqualified_contribution_route；两端之间不要求额外的全局快照。
- mean 取得有效值和/有效计数，weighted_mean 取得共同有效配对的乘积和/权重和及支持；
  原数值与原 error 政策继续执行，不能独立扫描两个 sum 后假定样本一致。
- 以完整父键将已计算总量映射到细组；校验父键唯一、覆盖及类型，Null 分类保留类型化键。
  零父键为单总体特例，映射不新增细组、不改变顺序、不混用其他 Frame 的 Column。
- 没有细组的父域仍完成总体与必要检查；已证明空集、未知输入、原量 NA 分别处理。
  检查作为计划义务保留，不能被后置筛选、top(0)、select 剪列或预览限行删除。

退出：计划依赖与实际来源读取记录共同证明同输入；可变来源反例不能产生跨读伪成功。
父域重复/缺失、复合键、多根、空父域/空细组和配对 NA 均有定点证据。没有新增嵌套
compute/persist、导出后重建执行流程或独立归因 runner。

### P3：贡献公式、规则派发和失败边界

主要落点：请求规则判断、现有 nullable/精确数值内核及输出结构。

- additive：只对相关轴、期间和定义上合格的量采用组差值；保留合法常数缩放与多根线性组合。
- ratio_component：每组贡献为 `N1(group)/D1(parent) - N0(group)/D0(parent)`，
  父域分母从 P2 的相同输入取得。mean/weighted_mean 使用原生样本组件；组自身值、
  组 change 与 contribution 分别输出，不能以组比率差替代贡献。
- linear：递归组合已支持子式的加减、取负、固定系数缩放，保留单位与子式检查；
  不做变量乘除、常数偏移、条件分支或代数相消的推测性改写。
- contribution_share 使用本次父域变化，零变化只令占比 null 并记录 zero_total_change；
  不裁剪负数或大于 100% 的占比。原 Metric 根分母零遵守另一套原量/贡献错误判断。
- 静态不支持在构造/准备阶段拒绝，动态未知、重叠、组件缺失、根分母零等在求值阶段拒绝。
  新错误继承 AnalysisError，通过共享模板披露预期、实际、指标/父键、reason 和具体修复。
  原绑定、来源、溢出等错误保留其 owner，不统一改写为分解不支持。
- 一次请求的所有量、父域和组原子成功；任一必要条件失败，不返回部分 memory 或发布部分
  Artifact。默认关闭贡献是用户显式提交的新请求，不自动降级或补全未知贡献为零。

退出：AT07–AT11 的独立算式、完整键/NA/presence 和错误断言通过；浮点按既有精度政策比较，
Decimal/整数/Duration 按原类型政策执行，不使用强转 float、尾差分摊或“最终和正确”代替证明。

### P4：解释事实、保存与普通 DSL 续算

主要落点：Frame 数据/元数据、terminal codec、提取/发布与现有数据操作。

- 按 P0 合同保存完整父键、本次总体两期值/change、根分母、规则/参数、子式及配对支持事实；
  零行结果仍保留必要父域摘要。ratio_component 的解释列有显式类型和单位。
- 在同一 Frame 保存协议内实现闭合编码/解码、必要附件与收据验证；拒绝坏版本、坏类型、
  矛盾父键关联或不完整摘要。临时组件、图、连接、Deferred 和执行配方不进入完成结果。
- 载体转换保留原结果事实；派生计算按已定传播规则解释原结果，不把旧摘要变成新统计的
  精确分解认证。where/top 不改原分母；select/算术/reduce 使用普通规则和精确输入句柄。
- 完成 memory/stored/冷恢复上的读取、合法 align/compare、规约及再次保存；有配对轴时
  保持原有再次对齐限制。固定结果续算不加载原模型、Catalog、来源或重建日历。
- 沿用 compute 无持久 Run、persist 发布已有结果、Artifact 仅审计的生命周期；规范排序
  由计算产生，writer/reader 保持行序，不添加 display_order 或存储时补排序。

退出：AT12/AT13 通过。完整单总体贡献 +60/−20 规约为 40，筛选后小计为 60，占比仍 150%；
多父域分别规约，reduce 删除行级 presence。关闭原来源的新进程可恢复数据与解释事实，
并执行合法数据操作；空结果、摘要和行序均可往返。

### P5：唯一公开入口与披露同步

- 发布 P0 已定入口，不给 AnalysisFrame 增加 attribute，不提供旧接收者或兼容别名。
- 同步具体类型签名、完整 docstring、`__all__` 快照、native Help 及可达性/预算测试。
  Help 目标沿 analysis 的小写 snake_case 路由；属性查询不执行 getter。
- 动态 show/错误/继续分析指引区分来源请求、普通贡献值计算和终态审计，不声称结果可回源。
  待执行 show、n=0、预览及 Artifact.show 保持已有读取边界。
- 更新 current Python/DSL/执行/架构/证据等受影响文档、最小可运行示例，以及
  `site/src/content/docs/docs/latest/` 和 `site/src/content/docs/zh-cn/docs/latest/`。
  默认调查、贡献成功/失败、固定父域与新读取、保存后续算都应有清晰的对应入口。
- 核对两份 packaged skills 的工作流描述。实施时若确需修改，取得具名明确授权后再编辑；
  未授权则记录具体待对齐项，不把完整公开交付标为完成。AGENTS.md 同样不在本轮编辑范围。

退出：AT14 通过；无新旧入口冲突、虚假能力或被错误迁移的历史配方示例。

### P6：统一验收与交接

修复阶段失败后，记录最终候选 HEAD、未提交差异及实际检查范围，在该候选运行最终验收。
若其后修改执行代码或格式，重跑受影响验证并使最终 broad gate 对应最终源码。

退出：AT01–AT14 有可定位结论，已发现的合同偏差均关闭；未配置的 Runtime 或未完成的
公开对齐明确列为缺口，不以本地数学测试、历史 Frame 验收或计划状态代替完成证据。

## 4. 检查组织与证据范围

优先扩展现有 `tests/analysis/{session,graph,numeric,materialization}/`、`tests/surface/`
的 owning 测试。只有新增独立职责才建新测试文件。修改测试前读取项目的
`marivo-test-fixtures` skill，复用 `tests/conftest.py`、`tests/shared_fixtures.py` 及领域 fixtures。
不新增独立 fixture 框架、自动化验收平台或无关 provider 全排列。

阶段先运行改动对应的 focused tests、typing、lint；命令中的测试/模块范围按实际文件确定：

```sh
make test TESTS='tests/analysis/session/test_analysis_frame.py tests/analysis/materialization/test_frame_correspondence.py'
make typecheck TYPECHECK_TARGETS='marivo/analysis'
make lint-agent LINT_TARGETS='marivo/analysis tests/analysis'
```

这些是现有入口示例，不能以它们通过替代新增 Attribute 断言。若修改 `marivo/_definition`
或 semantic 模块，typing/lint 同时覆盖这些实际修改模块。共享绑定/执行/存储行为变化后运行
`make test`；最终在没有 narrowing 环境变量的条件下运行完整 `make check-agent`，包括 API docs。

新增或受影响的真实来源/传键/共享读取路线，使用
`make runtime-test TESTS='实际受影响测试路径'` 验证，记录 provider、路线、源码候选及原始输出。
贡献不合格路线的拒绝同样需要断言，不能为了让矩阵变绿改成独立读取再对账。
必要的冷恢复/进程测试若属于 slow，单独运行并记录，默认测试不含 slow/Runtime/release。
本任务不要求 full release acceptance，不启动无关 MinIO，不发布包。

数值 oracle 使用手工小表、Fraction/Decimal 或独立 SQL，不调用被测贡献内核生成期望值。
共享输入同时查计划和实际读取，冷恢复用禁用模型/来源的独立进程证明。
复用现有发布故障测试，增加本次摘要与整体失败边界所需断言，不重复建立完整故障矩阵。

## 5. 合同验收清单

以下将设计第 10 节的场景按 owning 行为合并；验收 ID 不要求逐项新建测试文件或运行器。
每项同时检查适用的有序完整键、类型/单位、值、NA、presence 和错误，不只对账总和。

| ID | 必须证明的行为 | 阶段 |
| --- | --- | --- |
| AT01 | 唯一显式来源请求、非空量名与严格 bool；0/1/None 拒绝；类型/单位/根/粒度/样本与所有权检查；同路径版本变化本身不拒绝；无 mode/topk/自动回退；构造无业务读取或 Run | P0/P1 |
| AT02 | 父域双端完整对应、exact/outer 与日历/配对合法边界；普通 compare 的 absolute.q 筛选后显式桥接；固定后来源排名变化不重选，待执行选择会重求值，新值可变化；历史 presence/差值不作本次掩码/分母；空选择、Null、多轴联合投影及错误句柄 | P0/P1 |
| AT03 | 非空分类元组或单个有新增轴 Population；混合元组、重复/父域重复轴拒绝；product 四候选、相关域仅两元组、轴序改变仅影响布局/排序；域外贡献报覆盖错误，空完整域不取消检查；默认合法重叠保留、贡献要求不交 | P1/P2 |
| AT04 | 默认无贡献/解释组件/relative；原公式 max 的组变化 100/2/2 不冒充总体 95；联合抵消 +100/−100/+10 仍三行；单侧细组 outer/presence、NA 与 empty 政策；规范 A/B/C 排序且所有列同步，裸 align 次序不变 | P1 |
| AT05 | 每端每根总体/组共享实际输入，不是两次 observe；多根无事实放大；改变读取内容的反例、读取计数及计划依赖一致；关闭贡献不收集专用证明；无合格路线明确拒绝；后置筛选/剪列/预览不消除检查 | P2 |
| AT06 | 完整父键聚合值映射，复合键/Null 不拆，重复/缺父拒绝，零父键单总体；不增细组或改序，不允许跨输入 Column；多个父域独立归一化，零细组父域仍验证并保留必要事实 | P2 |
| AT07 | additive 的 +60/−20 合计 40、占比 150%/−50%；count/distinct 身份是否跨组、库存/窗口的轴和端点资格；重叠 {1,2}/{2,3} 默认合法但贡献拒绝；不能以可合并状态或最终守恒替代证明 | P3 |
| AT08 | ratio 的根 1/10→1/20、组贡献 0/−1/20；B 的全局子组各 −1/40，局部总体贡献 −49/1710、−5/171，占比 49/99、50/99；显式 N/D 解释列、单位及父域分母；组 change 与贡献区分 | P3 |
| AT09 | mean 有效和/计数及 weighted_mean 共同有效配对，value=[10,NA]、weight=[1,9] 得 10；复杂 per 保持原样本/规约顺序，证明不足拒绝；不以完成均值或两个独立 sum 反推组件 | P2/P3 |
| AT10 | linear 的 A−2B 组贡献 8/10、合计 18，含已支持比率子式；单位/子式政策不变；非零常数偏移、变量乘除、一般非线性、近似量及不支持 P95 请求贡献明确失败，默认保持原公式 | P3 |
| AT11 | 零总体变化保贡献、占比 null；原根分母零与原 error 政策、局部 N=D=0 可有有效贡献；已证空集与未知输入区分，缺侧不普遍补零；多量/父域任一必要失败即整体失败；Decimal/Duration/精度、溢出和原来源错误不被改写 | P3 |
| AT12 | 返回普通 Frame；同一固定输入 memory/stored/冷恢复继续 select/算术/where/top/reduce/合法 align/compare，受原轴与句柄限制；完整和 40、筛选和 60、占比仍 150%；多父域分别规约、reduce 清除 presence，结果没有来源方法或新归因资格 | P4 |
| AT13 | 方法摘要类型/父键关联/传播与 codec 闭合，空候选零行仍保存事实；坏版本/结构明确失败；规范顺序往返；固定续算不加载模型/来源；compute 无持久 Run、persist 不重算固定结果且失败不部分发布；无隐藏执行组件或新 Store | P0/P4 |
| AT14 | 唯一入口签名/导出/Help/CLI/错误/show/latest EN/ZH/必要技能描述一致；待执行 show/n=0/Artifact.show 的读取边界不变；rollup/drill/share、Frame.attribute 和来源恢复别名不出现；基础 compare 负基准 relative=2.6 等原合同不回归 | P5/P6 |

## 6. 实施记录与最终交接

正式实施时维护一份轻量进度记录，每阶段注明负责人、依赖、实际文件、AT ID、候选版本、
命令与结果、原始失败/修复、剩余阻塞。可在本目录新增 `attribute-implementation-progress.md`，
本轮不预建空证据目录或复制 AnalysisFrame 的历史验收记录。

合同审查、实现自测和独立数值见证分别记录，避免同一内核输出互相证明。若安排正式审查，
每任务最多两轮；修复 P2 及以上问题，第二轮仍有未解决项则保留阻塞，不以继续轮数代替收敛。
这不预先授权外部审查、agent 团队、提交、push 或发布。

最终交接至少包含：

1. 实际公开签名、父域合法范围、规则支持/拒绝边界及最小可运行示例。
2. AT01–AT14 的实际证据、最终 broad gate 与 Runtime/slow 的具体范围和缺口。
3. 方法摘要传播、保存协议版本、冷恢复及普通 DSL 续算的结论。
4. Help、错误、动态指引、英中文档和 packaged-skill 的对齐及授权状态。
5. 最终候选差异和工作区状态；未获后续明确指令不创建 Git commit、push 或发布。

实施从 P0 开始；计划文档检查不等同于上述实现或 Runtime 验收。
