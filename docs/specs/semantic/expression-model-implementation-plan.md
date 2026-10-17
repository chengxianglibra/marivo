# 语义层对象重构实施方案

Date: 2026-10-17

Status: implemented，基于语义表达式 v0.37 与实施基线 `188d3abc78`。
S0–S6 的实现与有界验收见第 8 节；本方案不将其它 Lazy Analysis DSL 阶段或未获准后端路线计为完成。

重构的主线是：**保留物理对象捕获、业务声明和现有执行链，将 Measure/Metric 的自建数值语言替换为受控的原生 Ibis Deferred，并贯通语义检查、模型收录和执行。**
在这条链上补齐内部样本、五项 v0.37 能力与相应披露；不重做已经完成的终端 Artifact 存储和读取。

## 1 依据与实施范围

目标 API 与语义以 [expression-model-redesign.md](expression-model-redesign.md) 为准。
[Lazy Analysis DSL](../analysis/lazy-analysis-dsl-redesign.md) 拥有 Population、membership、窗口和 Query 合同；
[执行与交接](../analysis/lazy-analysis-dsl-execution.md) 拥有读取、数值路线和终端产物；
[实现架构](../analysis/analysis-implementation-architecture.md) 提供跨层分工。
本方案将这些目标映射到当前代码，不另行定义 API。

当前行为仍需对照 [对象模型](semantic-object-model.md)、[加载与验证](loading-validation-introspection.md)、
[作者流程](authoring-workflow.md)、[Python Analysis Design](../analysis/python-analysis-design.md) 和 live Help。
其中部分历史阶段标记已经落后于代码：对象模型仍写 R1.4/R2 pending，实现架构仍写 R2.6 pending；
当前 loader 已有声明收录，Query 已接通 Store 10 终端发布与读取。实施时同步这些标记，不能据旧标记重复开发。

本次计划覆盖三类工作：

- 语义核心：受控叶子、数值配方、声明、权限、单位、时间、依赖、指纹、loader/Catalog。
- 必要分析衔接：观察绑定、自动 schema 准备、数值执行，以及 per、membership、window 所需的域和计划能力。
- 公共面收敛：类型、Help、错误、示例、当前文档和退役入口清理。

保留 Entity/Source、Dimension/TimeDimension 的受限 Ibis 作者合同，保留物理 capture → finalize → resolve。
不扩展任意关系程序、UDF、事件配对、分摊框架、预测或变化归因。
不借本次工作实现整个 R3–R8；Query 的其它代数操作仍由原计划拥有，涉及新语义的消费点必须更新准入规则。

## 2 当前实现与改造落点

以下是代码与 Help 的核对结果，不是本轮测试通过声明。

| 能力 | 当前实现 | 本次处置 |
| --- | --- | --- |
| 物理捕获和解析 | `semantic/expressions.py` 中已有 `FinalizedDeclarations`，解析冻结 scope；裸 Ref 不参与运算 | 保留生命周期；数值及条件 Field 的解析结果改为受控 Deferred，Entity/Relationship 的元数据对象保留 |
| Measure/Metric 作者值 | `semantic/expressions.py` 的 `NumericRowExpr`、`Metric`、`DeclaredMeasure`、`DeclaredMetric` 自行承载运算和披露 | 数值作者值统一为 Deferred；声明记录进入受控 resolver；定义披露移到 Catalog 条目和 Query.contract |
| 数值定义图 | `_definition/expressions.py` 有 Row/Metric Add、Subtract、Multiply、Divide、比较和布尔节点；`graph.py` 遍历并摘要 | 删除数值节点族，引用 Ibis 配方和绑定后的子图；保留规约、声明授权、贡献和样本边界 |
| 声明安全 | `validator.py` 检查语法；`_expression_binding.py` 的声明 builder 不调用原函数，按封闭接收者构图 | 保留先检查后构造；允许已核验 Ibis 符号，与匿名配方共用语义消费器 |
| 收录与发布 | `loader.py::_collect_module_declarations/_publish_declarations` 已处理声明、限定名称、重复名和 Session 依赖；`_compiled_state.py`、Catalog 已有发布表达式支持 | 改为读取根声明 resolver，复用现有闭包和身份检查，不新建注册系统 |
| 普通观察 | `analysis/dsl/query.py` 按 `isinstance(metric, Metric)` 接收；`binding/observe.py` 保留每次来源规约的 occurrence、过滤和时间 | 改为消费受控配方和语义使用记录，保留完整键及逐根绑定 |
| 准备与执行 | `analysis/materialization/query_preparation.py`、`analysis/compiler/graph_plan.py::plan_query` 已自动准备物理字段；`analysis/compiler/observation.py`、`semantic/metric_graph_lowering.py` 与 `analysis/methods/local.py::query_combine` 执行封闭节点 | 接入真实 Ibis 图，替换旧节点分派，复用 schema、数值政策、pandas 调度与类型交接 |
| 内部样本与域 | 私有 `PerReduce/PerWeightedMean`、`LiteralPopulation/PopulationProgram` 已存在；新 Query 的普通数值路线未完成这些能力，Population 条件仍有明确拒绝 | 复用定义基础，补齐公开域、绑定和实际执行，不能把节点存在当作完成 |
| 五项扩展 | 新表达式接收者尚无完整 first/last、approx_*、empty=null、membership、window 链路 | 分别接入同一配方、绑定和执行链，按下文阶段交付 |
| 终端产物 | R2.5/R2.6 已接通发布、重开、预览、导出及 Finding/Evidence 阅读 | 保持无图输出；只做受影响回归，不新增 codec、迁移或 Store 代际 |

代码路径均相对 `marivo/`。主要入口：
[表达式](../../../marivo/semantic/expressions.py)、
[定义图](../../../marivo/_definition/expressions.py)、
[声明构造](../../../marivo/semantic/_expression_binding.py)、
[loader](../../../marivo/semantic/loader.py)、
[观察绑定](../../../marivo/analysis/binding/observe.py)、
[计划](../../../marivo/analysis/compiler/graph_plan.py)、
[源端内核](../../../marivo/semantic/metric_graph_lowering.py)、
[本地内核](../../../marivo/analysis/methods/local.py)。

## 3 实现结构与关键取舍

### 3.1 采用一个作者载体和两次语义检查

Field/Measure/Metric 的计算输入均使用 Ibis Deferred；schema 是否已知不改变作者对象。
命名只是受控声明 resolver 中的元数据，Measure 新增单位或可加授权才构成新的语义边界。
不继承 final Deferred，不向它注入业务属性，不增加 `.expr`、`ms.ibis` 或 `ms.require`。
所有消费点通过统一的私有提取函数读取语义记录，不能再靠 Python 包装类区分阶段，
也不能对 Deferred 用普通属性探测来获取 `.ref` 或声明元数据。

消费器先遍历获准 Resolver 配方，提取完整依赖、语义操作、使用位置及已知约束。
execute 准备取得真实 schema 后，构造 Ibis operation DAG，完成物理类型与剩余语义检查。
普通数值调用交给 Ibis；sum/time/empty/divide/per/window 等语义方法先截获和归一化，
不把整份混合配方直接交给原生 `.resolve()`。

首次引入的模块控制在实际职责范围内：可在 `_definition/` 增加私有 Deferred 适配模块，
负责受控 resolver、配方遍历和摘要；在 `semantic/` 增加语义规则模块，
复用 `unit_algebra.py` 和现有权限合同。其余优先原位修改。
具体文件拆分随实现规模决定，不预建插件框架、空接口或逐算子文件树。

公共签名准确返回/接收 Deferred，行阶段和 Metric 阶段通过封闭语义检查确定；
内部叶子、声明记录和边界使用具体类型及 kind 分派，不用 Any 或大量可选字段隐藏差异。
Python AST 只负责安全捕获，不再构造一套与 Ibis 平行的数值 AST。

### 3.2 按语义使用位置保存政策

沿用现有 occurrence 思路，扩充为“消费作用域 + 有序操作数角色路径”。
每次使用分别记录根、过滤、时间、内部样本、成员/窗口绑定、政策和需求域。
数值结构可以共享，不能以 `dict[IbisOperation, Annotation]` 覆盖不同使用处的政策。

分支始终进入静态闭包与 schema 检查；动态数值检查按实际需求域执行。
ifelse/cases/coalesce 的分支域与普通算术的全操作数需求分别处理。
命名和匿名提取不改变求值；独立 observe 一个严格子式会新增它自己的需求。
首版优先正确的独立求值，仅复用已有明确等价的共享路线，不做通用公共子式优化器。

### 3.3 保留语义边界和执行分派

共同私有定义图保留 SourceAggregate、共同配对、ContributionFilter、PerReduce、Population 依赖，
并加入实际需要的 membership/window 边界；数值部分只引用 Ibis 配方/子图及其绑定槽位。
完成规约的槽位连接后续 Metric 数值计算，不建立拼接原始事实的虚拟共同关系。

源端继续由 Ibis 执行，本地继续由 pandas/现有数值内核执行，PyArrow 用于终端读写。
删除旧数值节点分派时，保留并改造 Ibis operation 到本地内核的分派及 dtype、NA、检查域适配。
不下推的计算只能使用准备阶段已准入的本地路线，不在失败后临时换引擎或算法。

每个根保留完整贡献身份和出现次数，多根在完整输出键及必要内部键上完成各组件后组合。
这约束计算意义，不强制 aggregate-then-join。首版使用已有合格策略，
不为设计中列举的每种等价物理策略编写实现或测试矩阵。

### 3.4 复用身份与存储边界

声明计算指纹摘要规范配方、实际捕获依赖、权限断言和默认化政策；名称与 ai_context 独立。
绑定执行指纹再纳入实际消费定义、schema、Ibis 图、观察/需求域程序及路线版本。
别名、仅命名和省略/显式默认保持计算身份一致；不追求任意数学等价。
不使用 repr、Python hash、内存地址或实际数据结果作为内容摘要。

引用解析继续使用消费者完整 scope 的 kind/path；纯表达式保持不可变。
同路径定义变化不能单独成为拒绝理由，也不触发 live refresh 或模块重放。
本轮只调整实际存在的缓存键，不为两层指纹新建缓存服务。
配方、Ibis 图和临时组件只在进程内，Artifact 格式不因作者语言替换而升级。

## 4 分阶段实施

阶段编号 S0–S6 只跟踪本次重构，不重置已有 R1–R8。
每个任务连同受影响的测试、类型和 Help 一起改，不把正确性与披露全部留到最后。
S0 是有界技术确认；其后按能力提交可审阅的变更。公开切换只有一种数值作者协议，
不发布接受“旧包装类或新 Deferred”的长期双轨入口。

### S0 确认 Deferred 适配和类型边界

**工作。** 针对当前锁定的 Ibis 12.0.0，验证受控叶子/声明 resolver、顶层 ifelse/coalesce、
方法链、反向算术、time 参数和 per 后规约的捕获；验证未知 resolver/回调在调用前被拒绝。
用一个命名/匿名配方验证声明记录读取、规范摘要和严格返回类型，不启动来源服务。
适配集中在私有模块，不把 Ibis 私有结构暴露为 Marivo API。

制定方案时只做了原生捕获探针：算术、顶层 ifelse、`.sum(time=..., empty=...)`、sqrt、per/last
均保持 Deferred；没有调用 resolve，也没有验证 Marivo 适配或数值执行。
官方 [Deferred 与 Value](https://ibis-project.org/reference/expression-generic#deferred)
及 [operations](https://ibis-project.org/reference/operations) 仅提供机制依据，支持范围仍以本地版本适配为准。

**完成判据。** 同一受控捕获器能够接住命名、匿名、顶层函数和链式语义方法；
不能只让装饰器源码改写成功。若关键链路无法捕获，先解决或收窄有明确依据的能力，
不以假 dtype、提前 schema I/O 或新的作者桥接 API 绕过。

### S1 替换语义作者值并接通声明收录

**S1.1 叶子与规则。** 修改 `expressions.py`、`_definition/expressions.py`、`graph.py`，
使 finalize/Catalog 的计算输入产生受控叶子；接入行/Metric 阶段、根、单位、可加权限、时间及待检查义务。
普通算术、比较、布尔和首期原生函数使用 Ibis 捕获；按真实符号及接收者检查调用。
数值编码 Dimension、跨根行算术、行与 Metric 混算、裸关系 Value 不能通过 dtype 或 cast 获得准入。

**S1.2 声明与模型。** 修改 `_authoring_decorators.py`、`_authoring_declarations.py`、
`_expression_binding.py`、`validator.py`、loader、`_compiled_state.py` 和 Catalog。
保留零参数体、首段 docstring、无重绑定局部表达式和最终 return；
允许获准原生符号及不可变语义参数，仍不执行任意作者函数。
装饰器只追加声明/授权；loader 只从导出值的根声明记录收录，
别名只收录一次，匿名派生不冒充其依赖的声明。私有声明可进入闭包但不自动成为公共条目。

复用名称限定、重复名、依赖循环、独立加载及 Session 归属检查。
原构造对象不被收录回写。规则按使用位置保存，声明和绑定指纹分别实现，
不把名称变化误判为计算变化，也不因 Ibis 结构相等混合 Measure 授权。

**完成判据。** 一个物理模型可 source-free 地 finalize、声明、load、解析；
命名和匿名具有相同计算合同。收录不连接来源，非法声明在其获准构造前失败。
公开声明签名、Catalog 读取和受影响 Help 已反映新载体；实际执行交付由 S2 完成。

### S2 贯通自动绑定与普通数值执行

**S2.1 准备。** 改造 `Query._from_metrics`、`binding/observe.py`、
`materialization/query_preparation.py`、`compiler/graph_plan.py`。
先消费完整配方，确定所需字段、条件、所有静态分支和使用位置；
observe 固定逻辑绑定，execute 自动获取所需 schema，生成真实 Ibis 图和绑定执行指纹。
复用当前 Decimal、Duration、时间字段、单值路径与精确版本锚点准备，不另设用户 preflight。

**S2.2 数值路线。** 改造 `semantic/metric_graph_lowering.py`、`_numeric_preview.py`、
`analysis/compiler/observation.py`、`materialization/graph_source_execution.py` 和 `methods/local.py`。
先迁移当前普通收入、计数、AOV、共同有效配对与分组对齐，再接入目标首期函数族和动态需求域。
保留零分母默认 NA、显式 error、严格 NA、溢出和非法值检查；
分支先选实际需求再做数据计算，不能先全域计算非法值后遮罩。

来源 sum 在此阶段默认 empty="null"：无有效值为 NA，有效零仍为零；显式 empty="zero" 保留零结果；
仅在合并需要时保留有效值存在性/计数。精确 quantile 保持线性插值，
近似来源规约复用 Ibis 名称并单独准入路线，定义精确性与实际实现披露分开。
没有合格内核时报具体缺口，不以 exact 替代 approximate，反之亦然。

本阶段结束时删除普通 Measure/Metric 路线消费的旧数值 AST 及重复构图入口。
仍有其它活跃消费者时，同步迁移相应消费者；不保留“先转回旧 AST”的适配层作为完成状态。
保留受限物理 Ibis 作者代码、必要语义边界和基于 Ibis op 的执行适配。

**完成判据。** 命名/匿名都可经真实 schema、源端规约和 pandas 组合返回终端 Artifact；
合法/非法类型在正确阶段处理；数值政策与分支需求域不被优化或后置操作抹去。
semantic 普通 preview 继续复用内核，其未实现的域/路径 preview 不因本次改造自动扩权。

### S3 完成内部样本与有序时间规约

**S3.1 域与 per。** 在 `_definition/relations.py`、analysis DSL、绑定和计划层，
交付 per 实际需要的 Field 元组、字面量 Population、来源/Query 坐标程序及已有归属保留。
来源域独立保存自己的选择期间/属性时点；Query 坐标域保留 Session 归属。
复用共同域实现，避免 semantic 专用名单或临时伪造的全局样本网格。
需要 `observe(by=Population)` 时一并接通显式输出域和覆盖检查，不能只增加参数类型。

执行键采用 `(外部 k, 内部 j)`。字段样本取每个 k 内各相关根的键像并集；
per(P) 使用指定完整样本集，保留零活动样本，先逐键完成整个子式再规约。
内部配对先完成 value/weight，再应用共同有效掩码；子指标状态按消费者需要保留，
不能用 SourceMean 的 sum/count 代替跨客户身份的 PerMean 重算。

**S3.2 规约。** 复用来源规约政策，补内部 sum(empty)、exact/approx 统计和 weighted_mean；
实现状态 TimeField 的先空间后时间、采样覆盖及 first/last。
唯一内部时间轴可给默认顺序；多轴时显式 order_by，并检查每格时间键唯一。
include_null 决定选择，strict 检查整个被需求样本域；缺测不等于 NA，期间末次样本不冒充边界 as-of。

**完成判据。** 收入、AOV、固定名单均额、先空间后时间 P95、共同有效配对五类核心场景可执行；
first/last、approx 和 nullable sum 在内部样本上具有独立路线资格。
per(P) 选择样本与外部 by=P 覆盖输出的差异有实际行为证据。
未完成的 MetricSamples 不能作为 Metric 观察或收录。

### S4 完成显式成员分桶

**工作。** 在共享定义/域中实现不可变 membership 描述，在 analysis 的公开构造、
coordinates、Population.classify/product/投影/集合操作、observe.by 和 Metric.per 中运输同一绑定。
这些是本阶段必要依赖；若已有 Population 阶段交付则复用，否则连同最小所需域操作完成。

逐根、逐描述优先选唯一显式路径；未提供路径时才推导身份/角色/版本相容的恒等。
显式自关系有效，无效路径不回退。单描述多字段是相关元组，多描述是独立集合乘积；
unmatched 逐因子应用，按 `(完整根贡献键, 完整桶键)` 集合去重。
成员目标的 classify 直接读取其身份分类，不执行事实分配路径。

执行计划保留根、使用位置、窗口和完整内部键；重复桥行不增贡献，
两个同金额事实或不同关联事件也不能被数值去重误删。
普通 via 仍单值；存在性选择、关联事件根、显式分配根与 membership 各自保持原语义。
并集原量必须从根贡献/充分信息重算，不能加总完成桶值。

**完成判据。** by/per 能消费同一成员描述，完整域能表示未出现桶；
多根路径加恒等、显式自关系、联合元组与独立集合乘积均符合目标合同。
新重叠事实进入已有 rollup/share 等消费者的准入检查；尚未实现的 Query 操作不能因此宣称可用。
不增加 fanout_policy、自动分摊或对来源权重的全表守恒审计。

### S5 完成可命名窗口 Metric

**工作。** 增加依赖中立的无轴 ObservationWindow 值，公开构造器由 mv 提供；
semantic 只依赖共享定义，不能导入 analysis 执行器。
覆盖 all_history、to_date、trailing_duration、trailing_periods 及 since 的目标规则。
`Metric.window(rule)` 与所有数值值共用 Deferred；装饰器体只允许获准的 source-free 规则构造。

observe 固定输出日历/标量端点和 W(t) 的逻辑绑定；执行在每个 W(t) 内重新求完整基础公式。
保留各叶子的时间角色、条件和域选择；per 的字段样本须包含窗口历史贡献中的身份。
标量 to-date 由 end 决定重置期间，不用 scope.start 截断历史，不用减一 tick 选边界。
同一路径只允许一次 window，不同算术分支可各自窗口；条件需求只展开需要的端点。

把窗口依赖事实运输到 Query 消费器：空间上卷需保留原时间绑定及充分信息，
窗口原量的日历轴粗化、替换或删除均拒绝，包括单桶情况。
改变评价粒度用新 observe，格子统计归 Query.reduce；不要据原始数据充分就猜测窗口时间上卷意义。

**完成判据。** 命名/匿名窗口能执行累计收入、窗口内重算 AOV 和 distinct，
固定时长与自然日历规则不混用，状态采样遵守有限覆盖域。
旧 first-seen 改写的 H/W 组合沿普通表达式执行；只有同一合格历史输入及旧桶边界等条件成立时才声明迁移等价，
不增加 first_seen 工厂、共享扫描强制要求或未证实一致性时的额外运行时拒绝。

### S6 收敛公开面和完成验收

**工作。** 核对全部入口只使用目标协议，清除被替换的数值类、旧节点翻译和无活跃 owner 的重复代码。
清理按实际依赖进行，不按文件名删除整个历史 Metric 模块，也不删除 Query/Population 自身代数。
旧工厂/参数只保留现有结构化拒绝与人工改写 guidance，不保留可执行别名、双读或自动写回。
特别核对 empty、status_time_fold、time_via、approx 和 cumulative 的修复不能悄悄改变统计含义。

同步 `semantic/__init__.py`、具体公开签名、`semantic/_capabilities/`、analysis Help、
Catalog/Query 披露、共享错误模板、导出快照、可达性/预算测试和示例。
同步 semantic overview/object-model/authoring/loading 当前契约及受影响的 analysis 当前文档，
对齐 `site/src/content/docs/docs/latest/` 与 `site/src/content/docs/zh-cn/docs/latest/`。
Deferred 数值值不再承诺自己的 Marivo show/contract，Agent 必须能从现有消费者获得已知事实、pending 和具体修复。

`AGENTS.md` 与两个 packaged skills 的修改需要显式授权。实施时先核对实际需要的差异；
如需修改，准备具体 diff 后请求针对这些文件的授权，不将一般实现授权扩大解释。
未获授权时可以完成其它已授权工作，但必须单列待同步内容，不能宣称公共披露全部完成。
本方案本身不修改这些文件。

**完成判据。** S1–S5 的能力都有实际消费和执行路线，数值语言已单轨；
新旧声明处置、Help、当前文档与实现一致，必要验证通过，剩余后端缺口按具体算子/类型披露。
已有终端 Artifact 阅读继续成立，不保存配方、Ibis 图、sketch 或内部状态。

## 5 依赖和提交组织

主依赖为 `S0 → S1 → S2 → S3 → S4 → S5 → S6`。
窗口的普通来源计算只依赖 S2，但其 per/成员/覆盖交互依赖 S3/S4，按上述顺序交付可减少返工。
first/last 依赖内部样本；近似规约与 empty=null 在 S2 做来源、S3 做样本，
不单独建立另一套算子框架。

S1.1/S1.2、S2.1/S2.2、S3.1/S3.2 可作为独立审阅任务；其余按完整能力提交。
核心载体切换期间以同一开发分支贯通 S1/S2，再提供可用切换点；
不为中间提交可运行而增加公开兼容模式。
后续能力只有定义、绑定、真实执行和披露都完成时才标记完成。
不为任务预设天数；S0 的适配结果和 S3 的域执行范围是主要工作量变量。

## 6 必要且有界的验证

测试以 [场景验证 §9.4–9.6](../analysis/lazy-analysis-dsl-scenario-validation.md#94-原生-ibis-表达式与自动延迟类型绑定)
为语义依据，按规则族选取正例与能区分错误实现的反例，不把参数、后端、命名方式全部做笛卡尔积。
命名、匿名内联和匿名复用由共享用例参数化覆盖；相同不变量只在其 owner 做一次详细断言。

| 验证组 | 最小有效证据 |
| --- | --- |
| 捕获与收录 | 一个完整模型加一个临时声明；顶层函数后接语义规约；I/O 哨兵覆盖构造/load/observe/contract；未知调用计数为零；别名、重复名、Session 依赖与原对象不变 |
| 阶段与授权 | CNY+USD、跨根行值、行/Metric 混算、数值 Dimension 拒绝；行乘积直接 sum 失败，显式 Measure 授权后成功；固定状态轴不被授权抹除 |
| 指纹与使用位置 | 命名/内联/默认参数规范等价；同结构 null/error 政策分别保留；一个 int→float schema 变更例确认绑定身份变化，旧计划不误用 |
| 实际数值 | 收入/AOV、配对 `[10,NA]` 与 `[1,9]` 得10；sum 空/全 NA/有效零；精确分位数插值；近似类型/政策和真实路线披露 |
| 分支需求 | guarded strict divide 在零分母处返回假分支值；独立请求 strict 或乘零仍失败；未选分支的静态冲突仍失败；源端和本地各有实际执行例 |
| 多根和内部样本 | 两订单100/200、三退款10/20/30得净额240；保留单侧键；完整 cohort 均额50而 observed 均额200；同一客户跨地区的总体重算不能丢内部身份 |
| 状态与有序规约 | A=[10,0]、B=[0,10] 的总体P95为10；完整时点上的10/20总量取last为20；NA选择、strict、缺测和同刻歧义用小型参数组 |
| 成员集合 | 重复桥行同桶只计一次；A/B各100而并集100；两独立集合得四桶各100；显式自关系优先；完整域空桶与内外域覆盖区别 |
| 窗口 | 历史100加当期20得120、历史重复身份只计1；标量月边界、DST固定时长差异；窗口时间轴变更拒绝；first-seen与活跃去重分别为1和2 |
| 终端与披露 | 复用新产物/重开读取与禁止DSL输入测试；确认图不进入产物；更新已有导出、Help可达性和预算测试 |

优先修改现有 `tests/semantic/test_semantic_expressions.py`、`test_definition_graph.py`、
`test_metric_authoring_surface.py`，以及 `tests/analysis/materialization/test_query_preparation.py`、
`test_query_schedule.py`、`test_terminal_publication.py` 和已有 surface 测试。
新增测试文件只用于新规则族；重复模型和数据复用 `tests/conftest.py`、`tests/shared_fixtures.py`。
实施测试变更时遵循项目 `marivo-test-fixtures` skill。

验证分三层：

1. 每个任务先跑最窄有用测试，改动 Python 模块必须通过针对性 typing 和 lint。
2. 核心载体/执行链切换及完整集成时运行默认测试；最终运行不带缩窄变量的 `make check-agent`。
   最终 gate 已含默认测试，通过后不再重复一套相同检查。
3. 数值路线用现有本地 datasource 集成夹具覆盖源端 Ibis 和 pandas 本地执行。
   新后端或特定物理路线才增加相关 Runtime 测试；没有运行的远端路线不标记已验收。

示例命令使用仓库入口，目标按实际修改范围替换：

```sh
make test TESTS='tests/semantic/test_semantic_expressions.py tests/semantic/test_definition_graph.py'
make typecheck TYPECHECK_TARGETS='marivo/_definition marivo/semantic/expressions.py'
make lint-agent LINT_TARGETS='marivo/_definition marivo/semantic/expressions.py tests/semantic/test_semantic_expressions.py'
make test TESTS='tests/analysis/materialization/test_query_preparation.py tests/analysis/materialization/test_query_schedule.py'
make check-agent
```

不新增全来源主键/关系扫描、跨读取结果核对、自动重试、完整后端矩阵、第二套数值 oracle 框架、
所有物理策略比较或超内存压力体系。失败只修复本次引入的问题；疑似基线失败做针对性基线对照，
不以扩大测试范围代替定位。实施后的检查与基线差异记录在第 8 节。

## 7 完成范围的报告方式

每阶段报告实现入口、可执行能力、实际检查与明确剩余缺口即可，不建立额外验收平台。
S0 的 Ibis 捕获证明、静态类型通过、独立算术预期、本地真实执行和远端 Runtime 证据分别陈述。

整个重构完成需要同时满足：统一原生作者载体；声明/匿名共用检查和执行；
收录保持 source-free；五项扩展及 per 的必要域能力完成；旧数值 AST 退出活跃路径；
已有终端边界保持；公共披露与当前契约同步。仅通过原有 R1/R2 测试不能替代这些条件。


## 8 实施记录（2026-10-17）

| 阶段 | 已交付入口与行为 |
| --- | --- |
| S0 | `_definition/deferred.py` 对 Ibis 12 原生 Resolver 做闭式捕获与规范编码；未获准回调在执行前拒绝 |
| S1 | `_deferred_semantics.py`、声明消费与 Catalog 共用 Deferred；保留根、单位、授权、时间和冻结作用域，旧数值 AST 退出活跃路径 |
| S2 | schema 准备生成真实 Ibis 数值图；来源与本地组合保留分支需求、strict/null/overflow/zero-denominator 检查；普通 preview 复用同一内核 |
| S3 | `(k,j)` 内部样本、固定及来源/Query Population、完整域与采样覆盖、first/last、配对规约；Query.population 保留原选择与检查 |
| S4 | membership 相关元组与独立集合乘积、完整根键去重、显式路径；project/set/classify/image/preimage 与 keys-only Query |
| S5 | 无轴窗口规则与命名/匿名 Metric；每个端点重算完整来源公式，支持累计收入、窗口 AOV/distinct、固定时长及自然日历边界 |
| S6 | 原生 Help、类型契约、导出快照、当前语义文档及中英文示例同步；已迁移相关表达式、规划、发布和恢复测试 |

开发、独立审查和独立验收分工执行。审查共两轮，发现的 P0/P1/P2 已关闭；第二轮修复只做定点复验，未启动第三轮。修复包括日历轴不在首位时的采样覆盖，以及来源 Population 零轴投影/乘积的空域语义。未修改 `AGENTS.md` 或两个 packaged skills。

明确保留的边界：来源 `approx_quantile` 和内部样本 `approx_nunique` 缺少合格物理路线时具体拒绝，不替换成 exact；Catalog 的 Population/sample/window/routed-field preview 未扩权；仅由 Population 提供日历而缺少显式窗口端点绑定的组合拒绝。未交付其它 Query rollup/reduce/compare、Population.read 或旧结果续算 API，也未运行远端后端矩阵和 release acceptance。

独立真实 DuckDB 验收覆盖命名/匿名、guarded strict、未选分支非法类型、固定名单、成员去重、状态 P95/first/last/缺测、窗口重算及 Population 三个原阻断。来源 approx_nunique/approx_median 路线实际执行通过。阶段中间检查与完整默认检查不混作远端 Runtime 证据。

验证结果：

- 冻结实现后的独立验收通过；原三个 Population 阻断、strict/状态/窗口链路已定点复验。第二轮审查的零轴回归 6 项及日历轴序缺测反例通过。
- lint、全部导入约束、仓库 typing（341 文件）、API 文档构建和站点内容验证（381 个必要文件）通过。
- 不带缩窄变量运行了 `make check-agent`。默认测试仍有已知基线失败，因此不声称 broad gate 全绿；API 文档阶段另行执行通过。
- 完整默认诊断采用 `.venv/bin/pytest -q --tb=short --maxfail=0 --junitxml=/tmp/marivo-expression-full.xml`，结果为 3952 passed、516 failed、10 errors。与已提交 `docs/testing/unit-test-triage.md` 的 520 个基线失败/错误逐项比较，发现 10 个本次漏迁测试；随后保持原断言目的迁移并定点验证。测试不新增 skip/xfail、不屏蔽默认选择，也不恢复退休 API。旧 cumulative 文件另在精确基线 `188d3abc78` 上复现 13 failed / 23 passed。

完整诊断日志位于 `/tmp/marivo-expression-full.log`，静态检查分别位于 `/tmp/marivo-expression-final-lint.log` 与 `/tmp/marivo-expression-final-types.log`。这些是本地开发证据，不是持续可用的仓库资产。

最终常规检查在静态阶段通过后，以 1799 passed / 27 failed 停止；27 个失败均已列在既有基线清单，没有新增失败。完整诊断发现的 10 个本次漏迁项均已定点通过；另外同组两个原基线数值边界案例也已迁移通过。
