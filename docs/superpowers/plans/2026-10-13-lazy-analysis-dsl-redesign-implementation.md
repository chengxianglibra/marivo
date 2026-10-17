# 统一语义表达式与 Lazy Analysis DSL：实施与验收计划

Date: 2026-10-17

Status: 本地实施与约定验收已完成，状态核对基于 `405bd006f8`。R1/R2 与 [S0–S6](../../specs/semantic/expression-model-implementation-plan.md) 提供基础能力，R3–R8 已在 `7508aca650` 交付，外部依赖 typing 收尾在 `405bd006f8` 完成。新数值 Query 的远端后端路线仍有代码能力缺口；远端 Runtime、完整 slow 集合和 release 验收未完成，不能合并称为“只差验收”。当前状态与证据边界见 §8，日期化实施过程见 §9。

本次修订只同步计划及相关文档状态，不修改实现或重跑行为验收。R1/R2 的旧任务表和 §9 日期化交接保留为历史记录；其中旧包装类、原定义版本、未提交状态和开发期失败均不构成当前待办或当前合同。当前作者值、语义使用位置、分支需求及空值政策以 v0.37 和当前 owning specs 为准；历史审查不重启，文档同步不新增行为验收证据。
2026-10-17 用户取舍：允许 Ibis/provider 原生数值精度损失，不在 Ibis 层添加业务无关的类型转换；相同表达式跨后端的返回类型差异交给 pandas 入口。该取舍替代旧的来源无损准入要求，最小实施顺序见 §8.1；旧 DuckDB 实现与历史无损测试记录保持原样，不代表新路线已交付。
当前优先修订（2026-10-15，Slack `1792053438.175219` / `1792053465.849969`）：取消语义对象必须匹配构造时原定义版本及同路径改版拒绝；agent/用户承担分析期间语义定义变更的后果。实际计算/依赖身份、缓存正确性、持久化记录和其它语义检查保留。capture/finalize为默认时间/坐标/Ibis前向闭包保留。完整supersession、下一阶段清理与证据边界见下文user-priority checkpoint；历史评审/验收不回写成新合同通过。
v0.32 统一 Query.execute → 终端 Artifact；所有 DSL 组合在执行前完成，产物不承载图、内部组件或续算。
执行分工与架构 v0.12 同步：Ibis 负责源端计算，pandas 统一本地执行，PyArrow 只负责存储和读取。
延续 nullable value：来源/内部样本 sum 默认空/全 NA 为 0，可显式 empty="null"；Query Column.sum 仍固定忽略 NA、空/全 NA 为 0，不新增 empty 参数。四态 carrier 与逐格原因保持退役；
各阶段零分母统一默认 null，显式 error 才使 execute 失败；其它非法计算和实际日历/映射/请求域覆盖检查失败仍报错。
首版不提供外部业务缺口声明或消费；本轮删除相应工作包，不增加替代入口或占位任务。
政策只归当前算子，不增加 Session/observe/execute 覆盖；已提交 semantic 合同的处置见[语义附录 A.1](../../specs/semantic/expression-model-redesign.md#a1-拟替代的已提交合同)。
本次补齐声明改写、分阶段检查、元组谓词、数值与时间合同，移除资源预算设计；不把文档修订或历史 oracle 当作新实现证据。

本计划替代旧版 R1–R8 的任务切分和累积修订记录，保留文件路径与 milestone 编号，方便现有文档引用。
编号不代表沿用旧任务内容，也不继承旧文档检查的通过状态。
本计划与 v0.37 同步原生 Deferred、终端 Artifact、执行内中间状态与 pandas 交接边界；历史模块示意服从当前已实现 owner，不据此重建旧数值 AST。

## 1. 目标、依据与实施尺度

交付一条完整的 `行表达式 / Metric → Session.observe → Query.execute → Artifact` 路径。
预定义与临时指标共用计算语言；Population 明确身份、样本与输出坐标；
Query 组合管理本次输入及充分信息；Artifact 保存输出、必要描述与实际 Finding/Evidence。
执行后用 pandas，业务语义、重用与新 Query 求值由用户/agent 根据脚本判断。

分析侧的目标名称统一为 Population、PopulationPredicate、population() 和 in_population(...)；
语义业务 Domain、domain= 与 ms.ref.domain(...) 保留。下文模块映射保留实施职责，不将历史目标文件名视为当前缺失模块。

两份目标设计共同拥有 API 和语义，本文只规定实施顺序、工作包与验收出口，不复制第二份签名清单：

- [语义表达式 v0.37](../../specs/semantic/expression-model-redesign.md)：行/指标阶段、声明、单位与可加性、节点政策、内部样本和模型收录。
- [Lazy Analysis DSL v0.37](../../specs/analysis/lazy-analysis-dsl-redesign.md)：Population、观察绑定、Query/Artifact、结果算子与时间窗口。
- [执行、保存与交接](../../specs/analysis/lazy-analysis-dsl-execution.md)：实际读取、默认保存、冷恢复、动态披露和 pandas 布局。
- [实现架构 v0.12](../../specs/analysis/analysis-implementation-architecture.md)：共同定义、绑定、编译、执行、终端数据的分层与模块归属；重点对应 §8–9、§12–15。
- [场景验证 §9](../../specs/analysis/lazy-analysis-dsl-scenario-validation.md#9-后续实现的最小验收集) 与 [完整示例](../../specs/analysis/lazy-analysis-dsl-examples.md)：业务输入、独立预期和关键反例。
- [核心分析代数](../../specs/analysis/algebra-theory.md)：身份、贡献、坐标、规约和信息充分性的依据。

实施遵循以下尺度：

1. **按可用能力交付。** 每个任务是一份可以独立评审的行为变化，连同必要类型、错误、保存字段和测试一起完成；不拆成“先写所有接口、最后补执行和测试”。
2. **复用现有链路。** 沿现有 semantic、graph、compiler、Runtime、Store 和 Help 修改，不建第二套执行器、泛化插件框架、验证框架或证据平台。
3. **只检查合同真正需要的条件。** 保留阶段、身份、单位、贡献绑定、nullable 政策和执行内信息的必要检查；不防御任意内存篡改，不做跨读取对账、自动重试、快照一致性或额外来源审计。
4. **验收抓语义差异。** 每个能力使用一个主场景和必要的关键反例；共享场景、fixture 和恢复用例。场景文档的条目是覆盖索引，不机械拆成 80 个测试，更不逐条乘以后端、类型和生命周期组合。
   验收只断言合同的正面结果与必要拒绝；不靠扫描存储、拦截调用或逐算子重复来证明某种实现“不存在”，这类结构约束由类型合同、静态导入检查和代码评审承担。
5. **先有合格路线，再验证优化。** 每项先直接求完整输入，再按架构接通有限需求传播和有反例覆盖的融合；不靠优化补齐缺少的能力。按 §5.5 验证结果一致与检查屏障，不设性能门槛或性能平台。
6. **固定执行分工。** 来源沿 Ibis 返回 pandas，LiteralInput/LocalCompute 共用 pandas StageOutput 与已有数值内核；PyArrow 仅用于终端产物编解码/读取，DuckDB 仅作为 datasource 后端。不增加本地引擎或批次/全表执行矩阵。

不纳入本轮实现：Event/序列/预测/归因、通用表格表达式、外部结果回灌、任意 Python/Ibis 回调、
任何 Artifact DSL 变换、固定 Artifact 输入、窗口重设、execute 保留请求、显式分母 `share(of=...)`、逐次 observe 路由补全、通用期末 closing 规约、资源预算及旧 API 兼容层。
复杂结果分类、描述/配对统计和格子滚动统计按提案交给 pandas。
保留仍有消费者的内部算法、资源管理和 Session 历史，不按旧名称批量删除实现。

## 2. 当前基线与改动落点

本次核对基于 `405bd006f8`；`3a4e741cfa` 是 R3–R8 实施前的 S0–S6 基线。当前产品合同由 [Semantic Object Model](../../specs/semantic/semantic-object-model.md)、[Loading / Validation](../../specs/semantic/loading-validation-introspection.md)、[Python Analysis Design](../../specs/analysis/python-analysis-design.md) 与 native Help 拥有；完整设计由 v0.37 拥有，具体物理路线以实际准入和证据为限。

| 当前已实现能力 | 当前边界 |
| --- | --- |
| Measure/Metric 作者值为受控 Ibis Deferred；统一消费器与真实 schema 准备已接通 | 不恢复 NumericRowExpr/Metric 包装类或 Row/Metric 算术 AST；新消费者复用语义记录、规约边界和 Ibis 配方 |
| capture → finalize → resolve、声明收录及 Catalog 已接通 | 定义身份描述实际消费内容；不恢复同路径原版本强制匹配；披露归 Catalog 条目与 Query.contract |
| 普通观察、pandas 阶段、Store 10 发布与终端读取已接通 | 已含次序、presence、投影及多日历输出；Artifact 不承载图或续算 |
| Population、Session.read、observe.members、member_count 与依赖调度已接通 | 完整键与 Session 归属保留；两个 DuckDB datasource 的合格交接不等于任意跨后端路线 |
| per 的 (k,j) 完整子式、状态样本、first/last、membership 分桶已实现 | 保留样本身份、相关元组/独立集合区别和完整贡献键；不授予未准入的近似或合并路线 |
| 五种窗口规则、逐端点重算、期间对应与比较已接通 | 窗口空间上卷原样保留时间轴；日历覆盖与对应仍按实际输入检查 |
| Query select/top/reduce/align/compare/rollup/drill/share 与有限优化已接通 | R3–R8 约定本地验收已通过；新数值来源路线仅准入 DuckDB，远端扩展须补代码及 Runtime 证据 |

当前 S0–S6 边界仍有效：来源 approx_quantile、内部样本 approx_nunique 缺合格路线时拒绝；原指标 approx_* 重算缺合格本地内核时拒绝；Catalog 的 Population/sample/window/routed-field preview 未扩权。这些是明确保留的能力限制，不是本地完成声明所覆盖的路线。

### 2.1 现有入口 → 目标 owner → 交付阶段

下表将[架构 §13](../../specs/analysis/analysis-implementation-architecture.md#13-目标代码组织与旧模块迁移)
落实到阶段；路径均相对 `marivo/`。目标路径可能尚不存在，以代码格式标识，不把它们写成已实现文件链接。
新模块随真实消费者建立；已有模块满足职责时原位保留，不先创建完整目录骨架。下表保留原职责映射，旧迁移入口只用于追溯；实际增量优先落在 `_definition/deferred.py`、`semantic/_deferred_semantics.py`、`analysis/binding/{sampling,membership,windows}.py`、`analysis/materialization/population_*.py` 与 `query_population.py` 等现有 owner，不按旧目标文件名重复拆层。

| 职责 | 现有迁移入口 | 目标模块与边界 | 交付阶段 |
| --- | --- | --- | --- |
| 中立定义与依赖 | `semantic/metric_graph.py`、`semantic/metric_graph_canonical.py`；`analysis/core/graph.py` 的纯记录 | `_definition/{types,expressions,relations,graph}.py`；只含闭合定义、角色边、遍历和指纹，不依赖 Catalog/Runtime/registry | R1 建立；R2–R7 随节点族扩展 |
| 表达式、声明、收录 | `semantic/_authoring_decorators.py`、`semantic/_authoring_declarations.py`、`semantic/_expression_binding.py`、`semantic/validator.py`、`semantic/loader.py`、`semantic/catalog.py` | `semantic/expressions.py` 与原 authoring/loader owner；计算定义、声明信息和收录资格分开 | R1；R8 收齐旧入口退出 |
| 公开对象与 Session | `analysis/public_dsl.py`、`analysis/session/core.py` | `analysis/dsl/{population,query,artifact,column,temporal}.py`；`analysis/session/` 只管归属、服务接线和历史 | R2 Query/Artifact；R3 Population；R4 Column；R7 时间扩展 |
| 观察、域、坐标和样本绑定 | `analysis/observation/`、`analysis/core/domain_captures.py`、`analysis/materialization/graph_{relation,members,observation}.py` 的纯构造 | `analysis/binding/{observe,populations,coordinates,samples}.py`；产出精确绑定，不做 I/O | R2 observe/坐标；R3 域/样本；R5–R7 映射/窗口扩展 |
| 绑定图与语义规则 | `analysis/core/{model,graph,rules}.py` | `analysis/core/model.py` 多量签名；`analysis/core/graph.py` 引用共同定义；`analysis/core/rules/{population,observe,query,grain,temporal}.py` 按族拆纯规则 | R2 起随能力贯通；R3–R7 扩展 |
| 物理资格与数值 | `analysis/methods/{registry,physical,semantics,numeric_state,local,comparison,references,predicates}.py` | `analysis/methods/` 保持唯一注册入口；精确算法留在数值内核，SourceAggregate/PerReduce/QueryReduce 分别准入 | R2 基础；R3/R4 样本；R5/R6 组合 |
| 纯来源 lowering | `semantic/metric_graph_lowering.py`、`analysis/compiler/graph_lowering.py` | 前者为唯一 Row/Metric Ibis 内核；`analysis/compiler/observation.py` 将绑定转为其精确物理输入，`analysis/compiler/{populations,queries,windows}.py` 各管一族 | R2 基础；R3/R5/R7 扩展 |
| 规划与优化 | `analysis/compiler/graph_plan.py`；`analysis/materialization/graph_local_execution.py` 的 `_selection_groups`/`_reduction_groups`；`analysis/core/local_laws.py` | `analysis/compiler/optimization.py` 的有序 pass 写入 GraphPlan/LoweredPlan；保留原节点定位和检查屏障，Runtime 不重新选路 | R2 基本计划；R3/R4 RequiredSlots/选择；R6 原量融合 |
| 单一阶段执行 | `analysis/materialization/graph_{execution,source_execution,local_execution,exchange}.py`；`datasource/adapters.py` | 原调度器消费 SourceRead/LiteralInput/LocalCompute；ExchangeResult 演化为 pandas StageOutput；算法不移入调度器 | R2 来源/本地；R3 字面量/动态参数；后续复用 |
| 终端数据与发布 | `analysis/materialization/graph_{protocol,snapshot,storage,store,publication,value_tables}.py`、`analysis/materialization/store.py` | `analysis/core/artifact_data.py` 拥有纯数据 schema/句柄/ReadLayout；writer 只接 ArtifactPublication；保留 journal 与事务，移除结果图/continuation codec | R2 格式切换；R4/R5/R7 只补输出字段 |
| 读取和 pandas 交接 | `analysis/materialization/graph_storage.read_result`、`analysis/materialization/graph_dataset.py` 及现有显示/导出 | `analysis/materialization/graph_reading.py` 统一元数据/PreviewRead/ExportRead；`analysis/materialization/pandas_export.py` 管公开布局；PyArrow 仅在存储/读取边界 | R2 基础重开/预览/导出；R4 次序/完整布局；R5 投影 |
| 四态和固定输入退役 | `analysis/core/cell_encoding.py`、`analysis/compiler/cell_lowering.py`、`analysis/materialization/cell_arrow.py`、FixedLeaf 和历史计算 reader | 新路径仅 nullable value；必要 Arrow schema 帮助函数留在存储；无 FixedRead/ComputeRead、状态书表或兼容分派 | R2 新路径即隔离；R3/R4 消费者收敛；R8 清理无消费者残留 |
| 披露与发现 | `semantic/_capabilities/`、`analysis/_capabilities/`、`_help/` | Catalog 条目及 Population/Query 合同读定义/绑定；Artifact 只读已提交输出；静态 Help 不复制实例状态 | 各阶段同步；R8 全局核对 |

### 2.2 阶段之间交接什么

四种表示按[架构 §3.2](../../specs/analysis/analysis-implementation-architecture.md#32-四种内部表示)分工：

| 表示 | 建立阶段与后续消费者 | 验收边界 |
| --- | --- | --- |
| DefinitionGraph | R1 建立；semantic 与 R2–R7 的绑定共同引用 | 不含活动 Session/Catalog/连接/Artifact；保留受控 Deferred 配方和语义边界；真实 schema 绑定后的 Ibis 数值图归准备/编译产物，不复制数值 AST，均不进入终端产物 |
| BoundGraph | R2 建立；R3–R7 增加域、样本、映射和窗口绑定 | 拥有多量签名、完整依赖角色与 obligations；规则不导入 Runtime |
| GraphPlan / LoweredPlan | R2 建立；R3/R4/R6 增加参数、需求和融合 | compiler 消费 schema 绑定且不做 I/O；计划固定来源 ID、stage、slots、消费者和检查位置 |
| ArtifactData + ArtifactManifest | R2 建立；R4/R5/R7 完成各类输出布局 | 只引用已提交 Parquet 和实际 Finding/Evidence，不持有前三种表示、执行内组件或内存产物分支 |

StageOutput 是 GraphPlan 的 invocation 内交接值，统一 schema 约束的 pandas 表、必要组件和检查回执；
它不成为第五套 IR 或可保存的计算输入。发布将所需输出投影为 ArtifactPublication；中间状态按最后消费者释放，Artifact 不延长其生命周期。
ArtifactPublication 只含最终 pandas 输出和记录；writer 编码后由发布器提交，返回的 ArtifactData/manifest 才引用已提交文件。
`semantic/metric_graph_lowering.py` 只接其自身定义的物理输入，不接 analysis Signature/RuleDerivation；
调用入口准备 schema/资源，compiler 纯规划，Runtime 只执行计划，reader 只消费输出。

## 3. Milestone 总览与完成规则

本轮已按 R3 → R4 → R5 → R6 完成实施；R7.2 随 R5 对应/比较推进，R7.1 复用已交付窗口并补组合缺口，R8 完成公开切换与本地收尾。以下顺序和交接规则保留实施依据，不表示这些能力仍待开发。
下表列的是实际消费的前置产物；编号是交付顺序，不表示每个子任务必须等待全部较小编号能力。
每阶段先完成类型/规则和一条未优化真实路线，再接保存/重开、必要优化与披露；不靠未实现占位让接口先通过。
阶段之间可以在同一开发分支增量提交，不要求每阶段发布一个版本。

| Milestone | 可交付结果 | 必需的上游产物 | 状态 |
| --- | --- | --- | --- |
| R1 统一计算定义 | 模型和分析代码可构造、声明、收录同一表达式 | 现有语义 Ref/时间原语；本阶段无 Runtime 前置 | 已交付；原作者载体由 S0–S2 替换，历史记录仅供追溯 |
| R2 最小观察闭环 | 多量观察 → pandas stage → 终端发布 → 按需读取 | R1 定义、政策、loader；先分开 BoundGraph 与 ArtifactData，再接 lowering/执行/存储 | 已交付普通观察本地闭环；R2.6 已提交，S2 适配新数值内核 |
| R3 Population 与内部样本 | 字面量/逻辑名单与 (k,j) 求值可实际执行 | R1 域依赖记录；R2 观察绑定、stage、nullable 输出和 reader | 已完成本轮本地实施与验收；资格及未运行范围见 §9 |
| R4 Query 组合与交接 | Column、筛选/Top-N/reduce/values、需求裁剪与完整导出 | R2 多量/检查/终端格式；R3 Population 绑定、LiteralInput 和依赖键交接 | 已完成本轮本地实施与验收；资格及未运行范围见 §9 |
| R5 对齐、比较与参照 | 多端点组合，presence、投影与广播 | R2 端点输出；R3 完整键/关系绑定；R4 Column/values 与 ReadLayout | 已完成本轮本地实施与验收；资格及未运行范围见 §9 |
| R6 原指标粒度与份额 | 合法 rollup、父选择后 drill、局部同输入 share | R2 组件/方法资格；R3 样本与键依赖；R4 选择/RequiredSlots；R5 映射与参照运输 | 已完成本轮本地实施与验收；资格及未运行范围见 §9 |
| R7 时间窗口 | 原指标滚动、累计、同比环比及实际日历覆盖检查 | R2 时间/来源绑定；R3 per/时间样本规约；基础窗口不依赖 R6；期间比较依赖 R5，空间上卷组合复用 R6，禁止窗口时间轴粗化 | 已完成本轮本地实施与验收；资格及未运行范围见 §9 |
| R8 公开切换与整体收尾 | 一套公开入口、同步文档和完整业务路径 | R1–R7 已贯通的行为、读取/失败边界、披露与验收记录 | 已完成本轮本地实施与验收；资格及未运行范围见 §9 |

为避免阶段循环和职责后移，固定以下交接：

- R1 只定义 LiteralPopulation/PopulationProgram 的闭合记录与收录检查；域的实际求值由 R3 接入，不在 R1 建模拟执行器。
- R3 的 QueryKeys 先消费 R2 观察的键；R4 已将 where/top/reduce 后的合法键域接入同一路径，避免实施阶段循环依赖。
- R2 即交付可用的 metadata open、普通预览和基础完整导出；R4 扩展选择次序与已准入 dtype/空表边界，R5 补端点投影，R7 扩展窗口/期间对应的时间输出。各阶段不能把自己新增的保存/重开留给 R8。
- R2 计划先固定 slots、检查截止点与 stage；R3/R4 引入 RequiredSlots 传播和局部融合，R6 扩展按量组件与原量融合。未优化路线始终是对照，Runtime 不另做准入。
- R7 的完整窗口直接求值不以滑动缓存或 merge 实现为前置；同比先有两端完整窗口，再消费 R5 的对应与比较。

每个任务同时完成自己的类型、结构化错误和最小动态披露；涉及保存的能力同时更新数据 schema/codec 与冷读；不编码 logic。
每个 milestone 的验收直接运行真实新增路径，不能仅凭 mock、AST、独立数值脚本或旧接口用例通过。
R1 的成果是定义/加载能力，来源执行从 R2 起验收。
Help 与当前文档随对应公开行为更新，R8 只做全局切换和遗漏收尾，不接收积压的各阶段合同工作。

验收证据直接记在本计划相应 milestone 下：实际改动、运行命令、结果和未完成项即可。
不新增阶段审批、独立验收台账、机器状态流或重复报告模板。每阶段只运行受影响范围；最后运行一次完整工程 gate。

## 4. Milestone 工作包

实现模块路径相对 `marivo/`；`tests/`、`docs/`、`site/` 和 Makefile 相对仓库根。花括号仅用于合并列举同级文件。

### R1：统一表达式、声明与模型收录

**历史任务与证据。** 本节记录原 R1 的交付过程，不是当前开发清单；已由 S0–S2/S6 接续完成的表达式、声明和披露不再执行。旧状态和旧载体仅按记录日期解释，当前基线见 §2。

**出口：** 同一表达式可在模型或普通分析代码中构造；声明只增加业务身份/授权，加载只收录可独立解析的定义。
本阶段不宣称来源计算已通过。

**模块落点：** `_definition/` 与 `semantic/`；以下路径均相对 `marivo/`。遵守架构 §4 的依赖方向，semantic 不导入 analysis 执行层。
**内部顺序：** R1.1 → R1.2 → R1.3 → R1.4；域记录在本阶段闭合，域执行不提前进入 loader。

| 任务 | 状态 | 目标模块 | 实施内容与产物 |
| --- | --- | --- | --- |
| R1.1 统一类型化定义图 | 已完成：评审 2 无发现，提交 `8bbab54b81` | `_definition/{types,expressions,relations,graph}.py` | 从 metric_graph/canonical 和 analysis 图的纯记录抽取 Row/Metric、Population/Query 引用及角色边；保留阶段、精确版本、贡献使用位置、权限、政策与规则版本。LiteralPopulation/PopulationProgram 保存完整程序闭包，不捕获公共对象或活动服务；通用遍历发现跨阶段循环/归属冲突。声明命名独立于计算指纹，不新增结果图 codec 或 Ibis AST 镜像。 |
| R1.2 接通表达式对象 | 已完成：评审 3 无发现，提交 `c0ac430447` | `_definition/{types,expressions,graph}.py`、`semantic/expressions.py`、`semantic/errors.py`；`semantic/validator.py`、`semantic/_expression_binding.py` 的声明体准入随 R1.3 消费者改造 | 交付私有表达式层，以及从已加载 Catalog 解析 Entity/Field/TimeField/Relationship 并捕获精确版本（共用版本函数）；已声明 Entity/Field 构造器接入移交 R1.3，Catalog Metric 接入移交 R1.4。裸 Ref 只用于查找。实现有限行算术、来源接收者规约、Metric 算术、where/per 的合法构造；where 只作用单一来源规约或共同配对作用域。`/`、无参数 divide 与显式 null 规范化为同一节点；显式 error 是另一计算政策。TimeField.via 捕获跨 Entity 的明确单值时间路径；状态轴要求留待完整绑定检查。已知 CNY+USD 等不相容单位在行/Metric 构造报 SemanticError，不隐式换汇。 |
| R1.3 实现纯声明 | 进行中：新合同清理与有效review4 P2修复完成，624定向/311文件typing通过，broad实际失败已分类；待独立review5全diff审查/修复及后续提交授权；本轮不提交 | `semantic/_authoring_decorators.py`、`semantic/_authoring_declarations.py` | 两种零参数装饰器按已解析符号/接收者阶段检查受限函数体，再构造表达式；sum/where/per 不按方法名无条件放行，ms.ref.domain 在 decorator 参数解析。捕获函数局部名、位置与 ai_context；Measure 继承事实并记录显式授权，Metric 不接全局政策。派生式匿名、别名不改身份，不要求活动 loader/Session。按语义附录 A.1 拒绝旧 Ibis Measure/Metric 体、empty/fanout_policy/status_time_fold/fold/time_via 及 approx_* 旧声明；Entity/Source、Dimension/TimeDimension 保留合法受限 Ibis，ms.bind 仅在原 owner 允许的字段体保留。按语义附录 A.1 给出作者改写/当前声明错误；empty="null"、重叠 fanout 不机械改写，不建 codemod 或兼容 loader。承接 R1.2 移交并按批准的 Option 1 调整：entity/dimension_column/time_dimension_column/measure_column/relationship 等物理构造器先 capture 并返回 typed Ref；完整闭包经 ms.finalize() 校验冻结后，由 finalized.resolve(ref) 返回 R1.2 表达式对象，与 Catalog 共用精确版本函数；同批退役旧 Ref 消费者（ms.aggregate/ms.where/ms.bind 体/_require_entity_ref）的同一变更中切换；`semantic/validator.py`、`semantic/_expression_binding.py` 的声明体准入在此改造。 |
| R1.4 改造模型收录与发现 | 已完成：评审 1 三项发现修复，提交 `50bfce76f1`（AGENTS.md 的 R1.3 遗留修改未纳入） | `semantic/loader.py`、`semantic/catalog.py`、`semantic/_capabilities/` | 收集导出声明，检查命名空间、重复/循环/kind 与可独立解析闭包；发布精确版本且不改写原值。匿名中间式/私有声明可作依赖，会话逻辑域不能靠执行或复制名单获得收录资格；Artifact 构造即拒绝。repr/show/contract 披露阶段、政策、归属和收录限制，不读来源或枚举域。承接 R1.2 移交：loader 以新定义节点发布 Metric，Catalog Metric 经 DeclaredMetricRef 与精确版本 resolver 进入同一表达式协议，不翻译旧 metric_graph；公开 catalog.fields/entities 发现及其 Help/文档同步在此交付；DefinitionGraphError 的循环/未解析类转换同归此处。 |

**必要验收：**

- 用“金额减成本求和”和 AOV 对照声明体/临时表达式，确认同计算图、政策与版本引用；名称/别名的变化不改变计算。
- 用“数量×单价尚未授权”和跨阶段返回各一个反例验证必要拒绝；显式 Measure 授权后可构造。
- 复用最小模型核对旧声明的作者改写诊断、字段体受限 Ibis/ms.bind 与新 Measure/Metric 体的边界；已知 CNY+USD 在构造拒绝，不为每个旧参数复制加载流程。
- 加载一份含别名与依赖的最小模型；再用重名或不可独立解析依赖验证收录边界。
- 定义层导入不依赖 registry/Runtime，构造/加载在禁用业务读取时仍通过；`m+m` 保留两个有序使用位置，真实 `Metric → PopulationProgram → Query → Metric` 循环可被发现。
- 同路径定义更新不因输入原版本不同而拒绝；实际消费的定义/依赖/政策仍生成正确指纹与缓存键，旧对象不回写。默认时间/坐标闭包、Ibis forward refs与所有非版本语义检查保留。
- 运行受影响 semantic 测试及 typing/lint；具体来源值与域运行留在 R2/R3，避免在此搭建模拟 Runtime。

**验收归属（2026-10-15 按选项 1 重分配；其后定义更新决定取消原版本强制匹配，保留实际消费身份与下列独立语义要求）：**

- R1.2：临时表达式侧的金额减成本求和与 AOV（同图、同政策、精确版本）、三种除法写法同节点、数量×单价未授权求和拒绝、跨根行算术与行/Metric 混算拒绝、CNY+USD 构造拒绝、TimeField.via 路径检查、`m+m` 有序使用位置、Catalog 解析构造不读业务来源、定义层导入边界。
- R1.3（依赖 R1.2 表达式对象与版本函数）：声明体与临时表达式的同图对照、名称/别名不改计算、装饰器跨阶段返回拒绝、显式 Measure 授权后可构造、旧声明作者改写诊断与字段体受限 Ibis/ms.bind 边界；已声明构造器与同一实际闭包的 Catalog 解析值具有一致定义身份，不要求跨scope保持输入原版本。
- R1.4（依赖 R1.2/R1.3）：含别名与依赖的最小模型加载、重名/不可独立解析依赖拒绝、Catalog Metric 经 DeclaredMetricRef 进入表达式并与声明体同图、禁用业务读取时加载通过、加载路径上的循环发现与公开发现披露。


### R2：Metric-first 观察、执行与最小恢复闭环

**历史任务与证据。** 本节保留各次交接当时的状态和测试结果；R2.6 已提交 `ea189ec50a`，旧结果续算清理由 `1d02fccb7a` 接续，Deferred 适配由 S2 完成。下列“待实现/未提交”不再是当前阶段状态。

**出口：** `s.observe({"revenue": revenue, "aov": aov}, ...).execute()` 返回终端 Artifact，保存后新进程可按精确 ID 读取，均无 DSL 续算。
先完成普通来源规约与组合；显式 Population 和内部样本的执行由 R3 接入。

**模块落点：** `analysis/dsl/`、`analysis/binding/`、`analysis/core/`、`analysis/compiler/`、`analysis/materialization/` 与纯 semantic lowering；沿现有 Session/Runtime/Store 接通，不复制协调器。
**内部顺序：** R2.1 数据/逻辑分离 → R2.2 绑定 → R2.3 基本计划与调度 → R2.4 数值路线 → R2.5 发布 → R2.6 阅读；全部贯通才算 R2 完成。

| 任务 | 状态 | 目标模块 | 实施内容与产物 |
| --- | --- | --- | --- |
| R2.1 分开 Query 与终端 Artifact | 已实施（2026-10-16，按确认的合同/接线范围）：P1 组合 Metric 闭包已修复，152 项相关测试通过；lint/import contracts、307 文件 typing 通过，先前 API docs 通过；broad 基线失败仍在，未提交 | `analysis/dsl/{query,artifact}.py`、`analysis/session/core.py`、`analysis/core/{model,artifact_data,graph}.py` | 多量 BoundGraph/BoundSignature 与 DataSchema/ArtifactData/ArtifactManifest 分开；ArtifactPublication 接最终 pandas 输出和无图记录，ArtifactData 只引用已提交 Parquet receipt。Query 捕获 R1 定义闭包，Artifact 只持 ID/manifest/reader；新路径拒绝 Artifact/ID/FixedLeaf 计算输入，无 Result 别名。Session.observe 返回 Query；execute 在 Run/读取前报结构化阶段缺口，Session.artifact 仅收精确字符串并在解码前拒绝旧图产物；get_or_create/resume 沿用同代际 Store 9。按明确确认，新代际启用推迟至 R2.5，未以升版本号继续写旧图。 |
| R2.2 绑定普通观察上下文 | 已实施（2026-10-16）：普通 during/by/Field where、单值字段路径/属性锚点与基础日历；178 项主定向及 132 项补充表达式检查通过（含重叠）；工程基线失败见交接 | `analysis/binding/{observe,coordinates}.py`、`analysis/core/{model,graph}.py`、`analysis/dsl/temporal.py` 与共享时间/semantic 表达式 owner | 建立 ObservationBinding/ContributionBinding/CoordinateBinding；共同条件分别绑定每个来源出现。实现 Field 谓词、单值 via/at、基础时间/Grain/日历输出；冻结时区与边界。构造不读业务行、不建 Run，repr/contract 不读 schema；未知 dtype 保留明确状态，execute 准备时解析。during=None 不追加时间过滤；采样/时间样本规约/日历的必需期间检查完整闭包。 |
| R2.3 基本计划与统一调度 | 已实施（2026-10-16，未提交）：schema-only 准备、纯 QueryPlan、pandas StageOutput 与拓扑调度；真实 DuckDB 投影/过滤读取通过；数值路线待 R2.4 | `analysis/compiler/{graph_plan,graph_lowering}.py`、`analysis/materialization/graph_{execution,exchange,source_execution,local_execution}.py` | 调用入口准备 schema/来源连接后交纯 compiler；固定来源 ID、需求 slots、stage 输入/输出、消费者与 consume/publish 检查截止点。ExchangeResult 演化为统一 pandas StageOutput，来源/本地消费同一规范化入口，消费者不原地改共享表。Runtime 按拓扑执行，不打开历史 Artifact 或重新选路；纯来源计划直接发布，不插空本地阶段。按执行 §2.4 在信息足够的最早边界检查，后端缺口在所依赖的业务读取前报出，实际值检查在发布前完成。 |
| R2.4 完成来源与本地数值路线 | 已实施并修复，已提交 `6b4c25add7`（2026-10-16）：数值内核、pandas 组合、有界 preview 与完整普通观察准备路线；验收见修复交接 | `semantic/metric_graph_lowering.py`、`analysis/compiler/observation.py`、`analysis/methods/{registry,physical,numeric_state,local}.py` | observation 将绑定转成纯 Row/Metric 内核的物理输入，内核不接 Signature/Session。接通 sum/count/count_distinct/mean/min/max/median/quantile、共同配对 weighted_mean 与公式；多根先分别规约再按完整键组合。源端 Ibis 返回 pandas，本地用准入内核；sum 空/全 NA 为 0，除法先 mask 再计算；默认零分母为 NA，显式 error 才生成除零检查，其它非法计算失败。删除新路径的 Cell carrier lowering；合格 semantic preview 复用同一内核，缺 Population 准备路线时明确拒绝。 |
| R2.5 原子终端存储 | 已提交 `e14eec9c49`（2026-10-16）：Store 10、公开 execute、宽表/附件、无图输出记录及原子发布；200 项定向通过，验收边界见下文 | `analysis/materialization/terminal_{protocol,storage,store,publication}.py`、现有 Store/Runtime/journal 与数值调度 owner | writer 只接 ArtifactPublication 的最终 pandas 输出和记录，不要求预先构造 Arrow 句柄；宽 primary 与必要输出附件按冻结 schema 编码，稳定 slot ID 映射命名量，规范完整键序和零轴物理占位见 §6.3。Finding/Evidence 改为自包含输出并复用分页。沿 journal/staging/文件就位/SQLite 提交发布，成功后才返回 Artifact；旧 graph/continuation 格式拒绝，无状态书表、隐藏组件或可恢复 derivation。 |
| R2.6 元数据打开与按需阅读 | 已实施（2026-10-16，未提交）；验证见本轮交接 | `analysis/materialization/{graph_reading,pandas_export,terminal_store}.py`、`analysis/dsl/artifact.py` | Session.artifact/repr 只读 manifest/receipt，禁扫 Parquet；新/重开共用已提交 Parquet 的 PreviewRead/ExportRead 和基础 ReadLayout。show 有界解码、完整导出不截断；只验证消费的 parts，保留首次 digest 可能读完整 part 的事实。Finding 独立分页；按本轮用户简化，evidence_digest() 无参返回全部 Evidence（.items）及摘要，.show() 有界，无需主表/Catalog/图；Artifact 无内存 Arrow 句柄、contract 或 DSL 方法；不保存 Ref/指纹或判定当前模型/来源新鲜度。 |

**R2.1 本轮交接（2026-10-16）：** 当前只提供 `Session.observe(metrics)` 的命名 Metric 捕获；during/by/where/members 的完整上下文留给 R2.2，不铺未来方法占位。新 BoundSignature 和共同定义图是新 Query 的唯一计算合同；旧单量 Signature/FixedLeaf 仍服务未替换的旧执行消费者，不进入新路径，全局退役按 R8 收尾。ArtifactReader 已定义协议，但尚无生产 reader 或 writer；ArtifactPublication 的表布局检查不是数值或持久化验收。R2.5 实现写入/无图 Finding 与 Evidence codec、一次新代际启用，R2.6 完成实际 metadata open、PreviewRead/ExportRead 与分页；既有公开 FindingPage/ArtifactDigest 家族的最终无图记录适配在这两个 owner 完成。新/重开实际产物和跨进程读取尚未验收。

**本轮验证与基线：** 定向运行 `tests/analysis/session/test_query_artifact_boundary.py`、Session surface/discovery、exports、guidance、disclosure content，共 44 项通过，含真实 R1 Catalog Metric 闭包且禁止 datasource backend 打开的构造验证。完整 `make check-agent` 在默认测试阶段失败：23 failed / 824 passed / 3 errors；实施前同一 gate 为 23 failed / 772 passed / 3 errors。xdist 停止顺序使具体失败项不同，已核对 18 个共同失败及新增出现的 5 项，均在已退役 semantic 声明/共享旧模型加载处失败；3 个错误仍属旧语义导入/加载路径。更广 surface 运行发现的本轮 observe 可见性断言已更新，相关定向回归通过。`make docs-api-agent` 单独通过。未运行 Runtime/release、远端服务或真实计算闭环；未修改 AGENTS/packaged skills，未提交或推送。

**R2.1 P1 审查修复（2026-10-16）：** Metric 二元算术合并双方已捕获的声明体，取负保留原闭包；Query 无需重新查询当前 Catalog，也不要求把依赖量额外列为输出。回归沿用真实 Catalog 临时项目，新增仅输出乘常数、反向乘法、双 Metric 嵌套公式及取负的四种情况，保留禁止 datasource 打开与无 Run 断言。修复前四项均失败，修复后五项（含原直接输出对照）通过；Session/surface 与相关 semantic 表达式测试合计 152 passed。受影响模块 lint/typecheck 通过；完整 `make check-agent` 的 lint/import contracts 和 307 文件 typing 通过，默认测试为 23 failed / 798 passed / 3 errors，未进入 API docs 阶段。本轮失败/错误与上一轮有 23 项重合，另三项 `test_complete_keys_source_and_fixed[ratio]`、`test_score_select_members_followup_one_dag[zscore-parquet]`、`test_two_separate_axes_and_joint_keep_the_original_target` 已在精确 HEAD `50bfce76f11e500e404fe5e7206ac368664f9a68` 的临时归档副本中复现，均在旧 semantic 声明/夹具加载处失败。日志：`/tmp/marivo-r21-p1-check.log`、`/tmp/marivo-r21-p1-base-extra.log`。未修改 AGENTS.md 或 packaged skills，未提交或推送；此修复只证明组合定义捕获，不改变 R2.2–R2.6 的执行、发布和读取交接，不构成真实计算或 Runtime 验收。

**R2.2 本轮交接（2026-10-16）：** 从精确 HEAD `871d26ee468a9fef21117e5aedaa18fa95a175dc` 实施普通观察绑定。`Session.observe(metrics, *, during=None, by=(), where=None)` 仅接 TimeScope/CalendarAxis、字段元组和 Field Predicate；Population、members 与窗口入口随所属阶段加入，没有占位方法。ObservationBinding、ContributionBinding、CoordinateBinding 保存不可变观察上下文、每个规约使用位置、固有/共同条件、每根单值字段读取与有序输出轴。期间及路径/锚点进入 Query 身份；实际字段/关系定义取本次完成的 Catalog scope，保持同路径改版可用，不按原定义版本拒绝，仍检查实际 kind/root/版本/单值路径。原生来源规约、per 样本轴、可加性与 Ibis/来源声明绑定继续拒绝路径/属性读取，避免扩大 R1 的计算合同。

新增 Session.instant/time_scope、共享 TimeScope 的精确边界变体及 TimeScope.by(Grain)，冻结 UTC 锚点、日历边界时区、名义/实际桶及认证快照。日期/无偏移字符串按构造时区解释，拒绝 naive datetime 和 DST 歧义/不存在时刻；带偏移输入保持瞬间。期间的现有 contract/model_dump 往返保留冻结时区。日历纯算法从 core/time_grid 下沉到 `_temporal_grid.py`，时区解析下沉到 `_timezones.py`，原 analysis owner 保留错误转换，semantic/definition 无反向 analysis 或 engine 依赖。共享 TimeScope.by 沿共享时间值的 ValueError 校验合同给出具体期间/Grain/认证修复；观察/字段错误由 AnalysisError/SemanticError 拥有。普通 during=None 不追加时间过滤；完整表达式/声明闭包中的采样与时间样本规约仍要求显式期间，乘零和 within 条件不豁免。

构造、repr、contract 不读 schema/业务行、不建 Run；每个来源出现保留明确 `physical_dtype="unresolved"`。**R2.3 交接：** 在 execute 准备边界解析 dtype、来源转换资格、连接和物理输入，再交纯 compiler；本轮未实现 schema 准备、执行调度或数值路线。Query.execute 仍在读取/Run 前以结构化阶段缺口指出 R2.3–R2.5；未使用旧执行器。没有 Store 升代、发布、冷读或 Runtime 资格声明。

**R2.2 验证：** `make test` 定向覆盖 Query/绑定、时间解析、定义图、semantic binding/live Help、exports/discovery/disclosure/budgets 与 packaged-skill 布局，共 **178 passed**；补充真实 Catalog 表达式、Metric authoring、live registry 及新绑定场景共 **132 passed**，两组含重叠，新绑定模块为 12 项。覆盖多根共同条件、同根不同固有分支、隐式/显式路径、缺失/歧义/fanout、跨 Entity 时间角色、无时间角色与期间、属性精确端点、完整期间检查、同路径不同捕获版本、DST/截断桶/认证覆盖和时区冲突。typed example 验证 scope.start/end 为 InstantAnchor，可直接传给 field.at。完整 `make check-agent` 的 lint/import contracts 与 **313 文件 typing 通过**，默认测试 **21 failed / 810 passed / 5 errors**，未进入 API docs 阶段；`make docs-api-agent` 单独通过。最终出现的 21 个失败均在精确 HEAD 的临时归档副本复现；5 个错误同样在该副本复现（原 fixture 参数展开产生 7 个错误），属于旧退役声明/旧 Catalog 类型导入。曾发现的本轮导入分层、原生字段约束、Help 反射与文档断言问题已修复，相关定向回归和静态检查通过。

日志：`/tmp/marivo-r22-final-check.log`、`/tmp/marivo-r22-targeted-final.log`、`/tmp/marivo-r22-expressions.log`、`/tmp/marivo-r22-base.log`、`/tmp/marivo-r22-base-extra.log`、`/tmp/marivo-r22-docs.log`。已同步当前 API、分析/语义/时间规格、native Help 及中英文站点示例。保留开工前 AGENTS.md 修改，不编辑 packaged skills；未运行远端/Runtime/release，未提交或推送。R2.2 的绑定通过不代表 R2 数值、发布和读取闭环完成。

**R2.2 P2 审查修复（2026-10-16）：** 修复三项审查发现：Metric 声明解释器对 Dimension/Measure/TimeField 统一准入 `via`，允许声明内构造单值字段条件，原生规约仍拒绝绑定字段；`session.observe` 的 `by`/`during` Help 按所属操作披露有序字段元组、TimeScope/CalendarAxis 及 None 语义，保留 Session 身份查找的 name/id 指引；TimeScope 类型 Help 的生产者与获取指引改为 `session.time_scope`，明确旧 civil scope 调用 by 前必须重建冻结时区。同步 semantic object model。新增四个回归场景（Dimension/Measure 两种声明内 via、observe 参数 Help、TimeScope Help 到实际日历构造），修复前四项均失败，修复后相关定向 **69 passed**；真实 Catalog 测试验证路径进入贡献绑定、禁止 datasource 打开、无 Run 和原生规约拒绝。

完整 `make check-agent` 的 lint、导入契约及 **313 个源码文件 typing 通过**；默认测试 **23 failed / 804 passed / 3 errors**，受 maxfail/并行调度提前停止，未进入文档阶段。失败/错误均与此前精确 HEAD 基线复现相符；本次额外出现的 `test_later_consumption_reinterprets_original_measure_callable`、`test_new_load_relowers_changed_weighted_mean_dependency` 已在同一 `871d26ee468a9fef21117e5aedaa18fa95a175dc` 归档副本复现，分别因退役 measure 参数和 ms.aggregate 声明加载失败。`make docs-api-agent` 单独通过，`git diff --check` 通过。日志：`/tmp/marivo-r22-p2-check.log`、`/tmp/marivo-r22-p2-base-extra.log`、`/tmp/marivo-r22-p2-docs.log`。未修改原有 AGENTS.md diff 或 packaged skills，未提交或推送；本轮不增加数值执行或 Runtime 验收结论。

**R2.3 本轮交接（2026-10-16）：** 从 HEAD `3231c8fa93` 实施，按确认范围只交付计划与调度，不含数值规约。新类型与旧 GraphPlan/Stage/ExchangeResult 并存于原文件，新路径不导入旧阶段类型，execution key 不变；旧执行器全局退役仍归 R8。

- 准备边界（`graph_source_execution`）：`open_query_sources` 先用 registry 解析每个读取的直接列，路径/锚点、解析时间、计算字段与跨 datasource 在打开连接前报 R2.4/后续阶段缺口；随后每个贡献根 `bind` 一次并按 schema 做无损类型准入（有符号整数→int64、浮点、bool、string、date32、decimal128、带时区 timestamp、duration；uint64、无时区瞬间、Duration 元数据编码等拒绝）。非 DuckDB 来源在资格测试前拒绝。准备不提交业务读取。
- 纯 compiler（`graph_plan` / `graph_lowering`）：`SourceSchema` 承载准备事实；`plan_query` 固定 SourceRead（来源 ID、出现位置、稳定 slot、完整键、谓词与期间过滤）、多根时的 `query.combine@v1` LocalCompute、消费者表与 consume/publish 检查截止点，未实现的检查在读取前拒绝；单根计划直接发布来源阶段，不插空本地阶段。`lower_query` 只读 Ibis schema，按 slot 投影并保留来源 ID。`DataColumn`/`DataType` 移至 `analysis/core/data_types.py`，避免 compiler 依赖存储，`artifact_data` 原样再导出。
- StageOutput（`graph_exchange`）：`normalize_stage_frame` 是来源与本地输出的唯一入口，校验列序、完整键、dtype、时区、nullable，并以 safe Arrow cast 拒绝有损值，不事后回 cast；`StageOutput` 只能经 `from_frame` 构造。`run_local` 以 copy-on-write 视图交给 kernel，拒绝返回输入表；`run_query_plan` 按拓扑执行、在截止点调用检查、末位消费后释放输出，不打开 Store/Artifact、不重新选路。
- 经确认改变全部 DuckDB 调用者：`_execute_duckdb` 改为 Arrow 表 `to_pandas(types_mapper=pd.ArrowDtype)`，修复可空 int64 经 float 丢精度（9007199254740993→…992）。唯一回归为 `test_source_profiles` 以 `isnan` 断言 NA，改为 `pd.isna`。SQL 归属清单为 `read_stage` 的 compile/execute 增加条目；`relation_operations` 从 `bind_entity_output` 抽出复用。
- Session：`Query.execute` 做准备、规划与 lowering 后，仍以 `AnalysisStageUnavailableError(stage="query_execution")` 指向 R2.4 数值路线与 R2.5 发布，不读业务行、不建 Run。Query/Session docstring、contract pending、Help 披露、导航、三份规格与中英文站点同步。

**R2.3 验证：** 新增 `tests/analysis/materialization/test_stage_output.py`（8 项）与 `test_query_schedule.py`（5 项，真实 DuckDB：大整数与 NA 无损往返、单根一次读取直接发布、多根本地组合且共享输入不被改写、uint64 在准备时拒绝且无业务读取、执行在 path 缺口时不开连接、规划在会话关闭后仅依赖 schema 事实）。定向 Query/绑定/边界/来源 profile 51 passed，SQL 审计 8 passed。`make lint-agent`（含 import contracts）、`make typecheck`（314 文件）、`make docs-api-agent`、`git diff --check` 通过。完整默认测试 669 failed / 3753 passed / 72 errors；同一命令在 HEAD 归档副本为 672 failed / 3737 passed / 72 errors，失败 ID 对比无新增、3 项转为通过，剩余均属既有旧声明/旧 Catalog 基线。日志：`/tmp/marivo-r23-full-after.log`、`/tmp/marivo-r23-full-base.log`、`/tmp/marivo-r23-lint.log`、`/tmp/marivo-r23-typecheck.log`、`/tmp/marivo-r23-docs.log`。保留开工前 AGENTS.md 修改，不编辑 packaged skills；未运行远端/Runtime/release，未提交或推送。R2.3 不代表数值规约、发布或读取完成。


**R2.3 审查修复（2026-10-16）：** 修复三项已复现问题，保持准备/规划/调度范围。来源准入沿实际 Entity 投影追溯原始 datasource 字段元数据，在业务读取前拒绝编码 Duration 与会丢失原始精度的 Timestamp；覆盖直接列和重命名投影，微秒 Timestamp 及未消费的其它类型列不受影响。每次 FieldRead 都先验证 path/anchor，再合并物理列需求，避免原生读取掩盖同字段后续锚点。内部 SourceCheckRoute 显式携带完整 obligation 与贡献 occurrence，规划拒绝缺失或不属于本 Query 的路线；调度在对应来源输出就绪时执行检查，满足 consume/publish 截止点并阻止失败后的消费，不再把检查统一绑定到最终组合结果。未添加实际检查算法、数值规约、Artifact 发布或读取。

新增 16 个参数化回归用例；包含原有用例的定向验证 **67 passed**。`make check-agent` 的全量 lint/import contracts 与 **314 文件 typing** 通过；默认测试 **21 failed / 829 passed / 5 errors**，这 21 个失败和 5 个错误均在精确 HEAD `3231c8fa9387cc6b499d226b438a968d467e6f3b` 归档副本逐项复现，属于既有旧声明/旧 Catalog 基线。`make docs-api-agent` 和 `git diff --check` 通过。日志：`/tmp/marivo-r23-fix-focused.log`、`/tmp/marivo-r23-fix-check.log`、`/tmp/marivo-r23-fix-base.log`、`/tmp/marivo-r23-fix-docs.log`。保留已有 AGENTS.md 与其它本地变更，不修改 packaged skills；未运行远端 Runtime/release，未提交或推送。

**必要验收：**

- 用本地真实 DuckDB 跑 APAC 收入/AOV 的范围合计与日历输出；多根收入/退款证明共同条件作用完整且没有事实 fanout。无订单日 sum/count 为 0，其他量按各节点规约完成。
- 一份小数据同时覆盖空输入、全 NA、零分母和共同配对：`[10, NA]`、权重 `[1, 9]` 的加权均值为 10；来源 sum 的空/全 NA 均为 0，不需要额外支持计数，结果侧 sum 在 R4 验收。精确 quantile 固定线性插值 h=(n−1)q，[0,10] 的 P95=9.5；共用空/全 NA 输入核对 min/max/mean/median/quantile 为 NA、count_distinct 为 0，不隐式用近似算法。方法精度按执行 §1.4 准入。
- 新进程关闭业务来源和当前 Catalog，按 ID 读取同一输出数据；实际 Finding/Evidence 自包含且与数据一致。原子发布与崩溃恢复沿用既有 journal 回归，不复制一套故障框架。
- 同一来源/本地混合路线的 dtype、NA 与完整键与源端政策一致，输出保存后往返不变；零轴 0/1 行不泄露物理占位列；保留 Query 再 execute 会重新求值。
- 禁用 Parquet 数据读取时 metadata open/repr 仍成功；show(20) 返回有界预览，完整导出不抽样。
- 不把“本地 DuckDB 实际执行通过”写成所有远端后端或 Runtime 标记测试通过。


**R2.4 初版交接（2026-10-16；后续审查修复见下）：** 从精确 HEAD `884395a14dc20806833385dfe057062e81d772e3` 实施。交付普通原生字段的私有数值路线；未修改既有 AGENTS.md diff 或 packaged skills，未提交或推送。

- 共享纯内核：`semantic/metric_graph_lowering.py` 直接接 Row/Metric 节点、Ibis 关系及物理字段，返回 nullable 值与临时检查；不接 Signature、Session、Population 程序或 Cell。原 `native_numeric` 的 Decimal 加法/乘法适配归入同一 owner，旧消费者复用原算法。
- 编译与来源：新增 `compiler/observation.py`，由贡献绑定构造物理规约输入；支持 sum/count/count_distinct/mean/min/max/median/quantile、Entity count 与共同非 NA 配对 weighted_mean。来源规约先完成，再交本地组合；单个直接规约可直接返回来源 stage，不插空本地阶段。来源显式 cast 消除 DuckDB HUGEINT→Decimal carrier 漂移，保留 int64 精度并拒绝溢出。
- 本地：注册 `query.combine@v1`，只消费本次 StageOutput 的 pandas 表，按全部坐标列组合（含 Null 分类），缺侧 sum/count 为 0，其余规约为 NA；保留大整数和 Decimal，输出保留命名量单位。日历补齐有限桶，空桶按各规约政策完成；支持来源无关常量。输入共享表不被改写。
- 检查：sum 空/全 NA 为 0；除法在计算前 mask 零分母，只有显式 error 政策要求非零；严格 NA、非有限值与其它非法计算失败。Row 条件检查在过滤前保留，即使分组结果为空也不能隐藏严格除零失败。来源检查在本地消费前完成，临时检查列不进入 StageOutput；新路径不调用 Cell carrier lowering。
- semantic preview：普通已发布 DuckDB Metric 的 preview/preview_many 复用同一 Row/Metric 数值内核，沿现有显式 scope、最多 10,000 行输入及近似披露；不创建分析 Session/Run 或 Artifact。Population/内部样本、未经资格验证的后端在业务读取前拒绝。
- 初版边界：`Query.execute()` 完成 schema 准备、数值规划/lowering 后仍在业务读取和 Run 前报告 **R2.5 终端发布**缺口；实际数值通过私有 `execute_numeric_query` 与统一调度验证。R2.5/R2.6 尚未交付。路径/属性锚点、解析时间、计算物理字段、编码 Duration、跨 datasource 的准备路线仍明确拒绝；本轮通过不能作为这些路线、完整 R2、终端保存/冷读或远端 Runtime 的验收。已同步 native Help、API、当前规格及中英文站点；packaged skills 未编辑。

**R2.4 验证：** 核心定向 **179 passed**，含真实 DuckDB 八种规约、Entity count、行/Metric 公式、多根标量规约后组合、两列完整键、Null 分类、日历空桶、weighted_mean 配对、空/全 NA、严格 NA/除零、过滤不能隐藏错误、整数精度/溢出、Decimal 混合运算、source-free 计划、普通 Metric preview/preview_many 与 Population 预览拒绝。补充 Help/exports/disclosure/SQL audit/数值测试 **118 passed / 3 failed**，三个失败在精确 HEAD 归档副本复现，均为旧退役声明夹具。

全量 `make check-agent` 的 lint/import contracts 与 **316 文件 typing 通过**；默认测试 **24 failed / 861 passed / 2 errors**，未进入 API docs 阶段。所有失败/错误均在精确 HEAD 归档副本复现：第一轮 broad 的 23 failed / 3 errors 逐项复现，最终新增出现的 count 参数项也单独复现，其余重合。`make lint-agent`、`make typecheck`、`make docs-api-agent` 和 `git diff --check` 最终通过。日志：`/tmp/marivo-r24-final-focused.log`、`/tmp/marivo-r24-surface.log`、`/tmp/marivo-r24-final-check.log`、`/tmp/marivo-r24-base.log`、`/tmp/marivo-r24-base-extra.log`、`/tmp/marivo-r24-lint.log`、`/tmp/marivo-r24-final-types.log`、`/tmp/marivo-r24-docs.log`。未运行远端服务、Runtime/release 或终端恢复验收。


**R2.4 审查修复与准备路线补齐（2026-10-16）：** 保持原任务归属，不把准备路线移到后续阶段；基线仍为 `884395a14dc20806833385dfe057062e81d772e3`。

- 修复四项审查问题：weighted_mean 在除法前检查分子和权重和的非有限结果；preview/preview_many 对无法准备的路径/属性锚点在打开来源前拒绝；无来源常量保留完整日历桶；普通观察准备不再以路径、时间解析、计算字段或编码 Duration 为阶段缺口。
- 每个贡献出现有独立物理关系和 schema，字段 slot 保留路径/锚点身份。复用受限字段 body、时间解析、精确 snapshot/validity 选择；原生期间过滤先排除无关贡献，再构造单值关联。关联先按完整目标键规约并保留匹配数，缺失或重复匹配在数值消费前以 Analysis 错误说明修复；不把行扇出当作贡献。
- 准备覆盖计算 Dimension/TimeDimension、解析时间、带声明/reader 时区的 naive 时间、civil date 的报告时区边界、属性版本轴解析，以及 encoded Duration 原始 ticks/单位。保留来源元数据沿投影追踪；版本轴和关联键等非输出依赖也检查精度。Duration 均值/分位数使用来源规约的必要组件完成精确 ticks 舍入，本地公式避免浮点中转。
- 复审额外修复整型中间值溢出被后续乘零掩盖：来源先以精确宽整数表达式生成检查并 mask 非法结果，本地在每次整数运算后检查；preview 复用同一来源检查。
- 不同合格 DuckDB datasource 的贡献根各自规约，再按完整键在 pandas 组合；每次来源提交核对实际依赖的全部绑定。跨 datasource 的单值关联仍不冒充来源原生连接；独立来源应分别规约。同步 SQL 提交审计 owner、当前规格、API 和中英文站点。
- 资格边界不变：公开 `Query.execute()` 仍在业务读取/Run 前报告 R2.5 发布缺口；私有统一调度执行本轮数值验收。preview 只采用其已准入输入，不能继承分析准备资格。未编辑 packaged skills/既有 AGENTS.md，未运行远端 Runtime/release 或终端发布/冷读，未提交或推送。

**修复后验证与复审：** 最终定向 **205 passed**，覆盖四项原问题、全部上述准备路线、Duration 大整数 ticks、来源/本地/preview 的整型中间值溢出，以及 SQL 提交 owner。最终 lint/import contracts、**317 文件 typing**、API 文档、**381 个站点内容文件**与 `git diff --check` 通过。默认测试 **24 failed / 849 passed / 2 errors**；所有失败项及两项旧声明导入错误均在精确基线归档复现，未将默认测试记作通过。按 `marivo-review` 对工作区完整 R2.4 diff 复审，原四项关闭，额外整型中间值溢出已修复，当前范围 **No findings**；该结论不扩展到未运行的远端 Runtime、R2.5/2.6 或终端恢复。

验证日志：`/tmp/r24-final-focused.log`、`/tmp/r24-final-lint.log`、`/tmp/r24-final-types.log`、`/tmp/r24-fix-docs-final.log`、`/tmp/r24-fix-site-final.log`、`/tmp/r24-final-default.log`。精确基线证据：`/tmp/marivo-r24-base.log`、`/tmp/marivo-r24-base-extra.log`、`/tmp/r24-fix-baseverify.log`、`/tmp/r24-fix-baseverify-final.log`。所有改动保持未暂存、未提交。


**R2.5 实施交接（2026-10-16）：** 基于精确 HEAD `6b4c25add7`，按确认范围只交付 R2.5；R2.6 公开阅读保持未完成。

- `Query.execute()` 经现有 Runtime 做准备/规划/lowering，成功准备后准入 Run，复用 `execute_numeric_query` 与统一拓扑调度；来源关闭成功后发布。纯来源、pandas 组合与无来源常量共用终端路径，每次调用新求值，不复用历史答案。
- Store 10 使用 `marivo.terminal_output/v1` 的闭合数据记录。manifest 保存 DataSchema、primary/附件 receipt 与 payload digest，Run 只保存 invocation marker/身份/生命周期；无定义、Signature、图、Ref、计算指纹、内部组件或 continuation。旧图发布器在 Run/写入前拒绝；旧代际及旧文件名明确拒绝并保留字节，换 Session 名称不能绕过。
- 一个规范完整键序的宽 primary 保存多量 nullable 值与独立 presence；零轴无量以内部 `__unit: uint8` 保存 0/1 行。日历的 nominal/actual bounds 作为明确输出附件保存。Parquet 使用冻结 schema、2.6、zstd level 3、最大 128 Ki 行组；共享扫描 batch 上限为 64 Ki 行。整型、Decimal、时区时间和 Duration 无损转换，不能表达则失败。
- Finding/Evidence 使用既有 OutputFinding/OutputEvidence 的无图 codec；复用分页基础设施，通过精确 Artifact/集合游标读取私有记录。普通观察 Finding 为空，只保存实际读取范围及实际检查事实。SQL ownership 审计登记新 Store/协调调用；DuckDB native 提交验收改用公开 Query 路线，不再进入旧成员发布器。
- 沿原 writer guard/journal 完成 staging、关闭/fsync、receipt 检查、文件就位，再在一个 SQLite 事务提交 manifest、记录、Run 成功和资源归属。丢失确认核对原 manifest 与完整输出记录；状态未知保留原资源并报告 recovery pending。崩溃回收只处理原 Run 的未提交输出，不重算、不损坏已提交产物。
- 返回的 Artifact 仅持已提交 manifest 与无数据缓存的 reader；repr/身份可用，`Session.artifact()`、show、导出、公开 Finding/Evidence 阅读及包含 Artifact 摘要的 Session.graph 均明确指向 R2.6 缺口。私有完整读取用于存储验收，不作为公开 reader 或完整 R2 资格。

**R2.5 验证：** 最终定向 **200 passed / 4 deselected**，覆盖真实 DuckDB 纯来源/多量与本地组合、重复执行重读、NA/零分母失败、准备无 Run、来源关闭失败、宽表/空表/完整 Null 键、四种时间精度与 Duration、Decimal/大整数、零轴 0/1 行、分页/codec、独立进程私有读取、六个进程退出边界、writer 争用、提交确认丢失/未知及旧 Store 保留。4 项远端服务用例按默认标记排除，不计入通过。lint/import contracts、**321 文件 typing**、API 文档、**381 个站点内容文件**和 `git diff --check` 通过。

最终 `make check-agent` 在默认测试阶段为 **23 failed / 737 passed / 2 errors**，未记为通过。全部 25 个失败/错误节点已在精确基线 `6b4c25add7` 的临时归档中复现；额外 surface 扩展为 **476 passed / 6 failed**，6 项亦已在同一基线复现，均属旧语义声明/夹具。基线归档：`/tmp/marivo-r25-base-6b4c25add7`；日志：`/tmp/marivo-r25-core-final.log`、`/tmp/marivo-r25-check-final.log`、`/tmp/marivo-r25-base.log`、`/tmp/marivo-r25-extra-base.log`、`/tmp/marivo-r25-surface-base.log`、`/tmp/marivo-r25-docs.log`、`/tmp/marivo-r25-site.log`。

**R2.5 三项 P2 修复（2026-10-16）：** 日历主表及边界附件的 schema 使用闭合的 `CalendarDataAxis` 输出记录保存所选时区，边界值仍以 UTC 保存；该描述不包含日历规则或执行逻辑。这样同时保留 IANA/DST 与固定偏移语义，不依赖 Arrow 解析 `UTC+05:30` 等时区名称。读取范围摘要为嵌套 AND/OR 保留括号，并覆盖 NOT 组合，避免不同读取范围保存成相同文本。精确 `Query.execute` Help 明确新 Run、按需来源读取及原子发布；Artifact 四个阅读方法的 Help/docstring 明确 R2.6 不可用边界。

新增回归通过真实临时 DuckDB 项目与独立进程私有读取验证有来源/无来源日历、DST 的 23 小时桶、非默认固定偏移及三种嵌套过滤结果；字符串/callable Help 入口均验证实际执行效果与阅读缺口。修复后定向 **161 passed**，完整 lint/import contracts、**321 文件 typing**、API 文档及 **381 个站点内容文件**通过。Help 扩展 **56 passed / 3 failed**；3 项旧声明夹具失败均匹配精确基线复现记录。最终 `make check-agent` 默认测试 **23 failed / 734 passed / 2 errors**，没有记作通过；此前未记录的 4 个失败节点已在精确基线归档补充复现，其余 21 个失败/错误节点与既有基线记录匹配。

**修复后 marivo-review 复审：** 基于 `6b4c25add7` 对本轮完整 R2.5 工作区差异和新增文件复核，三项原 P2 均关闭，当前范围 **No findings**。原反例探针确认日历时区已保存，结果 20/35 的两种范围保留不同分组摘要；codec/私有冷读、发布失败/恢复与独立 Help 入口回归通过。该结论不扩展到 R2.6 公开读取或远端 Runtime。日志：`/tmp/marivo-r25-p2-targeted.log`、`/tmp/marivo-r25-p2-check.log`、`/tmp/marivo-r25-p2-help.log`、`/tmp/marivo-r25-p2-base.log`、`/tmp/marivo-r25-p2-probe.log`、`/tmp/marivo-r25-p2-docs.log`。

未编辑既有 AGENTS.md diff 或 packaged skills，未提交/推送，未启动远端服务或运行 Runtime/release。R2.6 的元数据打开、PreviewRead/ExportRead、公开结果家族适配和完整断源公开读取不在本轮完成声明中。

**R2.6 实施交接（2026-10-16）：** 开工时 HEAD 已从计划所述 `e14eec9c49` 前进到仅调整 AGENTS 指导的 `6a11e96253ce3ea3f518d84fb31e3a3fd333eb42`，以该精确 HEAD 作为验证基线；本轮不改 AGENTS 或 packaged skills。

- 新/重开 Artifact 共用无表缓存的 TerminalReader；Session.artifact/repr 只读 manifest、归属、成功发布与 receipt 元数据，不读 Parquet/来源/Catalog/图。Session.graph 只消费 Run/终端输出元数据，移除公开 ArtifactSummary 的 definition_fingerprint。
- PreviewRead/ExportRead 共用 ReadLayout、receipt 校验与无损解码；R2.5 私有完整读取也委托同一数据 reader。只校验实际消费的 parts；同调用复用 digest 结论，首次 hash 仍可能读整份文件；只对解码行检查键序、唯一性、nullable 类型与有限值，不额外全表排序/扫描。
- show(n) 有界解码，show(0) 不打开 Parquet；完整导出保持 RangeIndex、kind/name 两层列、完整复合坐标、精确 nullable 类型、presence 和独立纯数据 attrs。日历序号经已保存附件还原真实桶起点，保留 DST/固定偏移时区及名义/实际边界；预览只收集必要边界。
- Finding/FindingPage 为自包含输出值与坐标，无 subject/derivation；仍有消费者的 GraphFinding/GraphArtifactDigest 仅留私有旧路径待 R8。**按用户后续明确简化，Artifact.evidence_digest() 不接受 limit/cursor，直接返回全部 Evidence（.items）与集合摘要；结果 .show() 保持有界。** Evidence 从同一 SQLite snapshot 完整读取，校验序号、数量和实际 body/集合摘要；Finding 保持精确产物分页。
- 同步公开 docstring/native Help、导航、类型契约、API、当前规格和中英文站点示例。无新顶层 export，无迁移、双读或额外 Store 升代；R4/R5 扩展仍归所属阶段。

**验证：** 定向读取/发布/Query 边界/无图结果/native Help 与协议测试共 **156 passed / 1 failed**；唯一失败 `test_committed_runtime_projections_satisfy_terminal_protocol` 在精确基线归档复现，仍在旧 ms.aggregate 声明夹具加载处失败。最终独立核心定向为 **124 passed**，额外 exports/发现/Help reachability/预算/披露定向 **45 passed**（含重叠）；日历逻辑 dtype 元数据补充回归 **2 passed**。覆盖大于一个 row group 的 show(20) 仅解码 20 行与完整导出、未消费附件删除、已消费主表损坏而记录仍可读、复合键、零轴 0/1 行、空表、Decimal/大整数/四种时间精度/Duration、205 条 Evidence 完整返回与 Finding 分页/跨产物 cursor 拒绝。真实 DuckDB producer 与断源/删除模型后的 reader 为两个独立进程，后者禁止 Catalog/来源/图解码仍可公开 show/to_pandas/findings/evidence_digest 与 Session.graph。

完整 `make check-agent` 的 lint/import contracts、**323 文件 typing 通过**；默认测试 **23 failed / 711 passed / 2 errors**，受 maxfail/并行调度提前停止，未进入文档阶段。全部 23 个失败、两个收集错误及额外四个披露/协议旧夹具失败均已在精确 `6a11e96253` 归档副本复现（27 failed / 2 errors）；不将 broad gate 记作通过。单独 API 文档、**381 个站点内容文件**及 diff 检查通过；最终静态复核日志见下。

日志：`/tmp/marivo-r26-targeted.log`、`/tmp/marivo-r26-final-targeted.log`、`/tmp/marivo-r26-check.log`、`/tmp/marivo-r26-base.log`、`/tmp/marivo-r26-final-check.log`、`/tmp/marivo-r26-final-core.log`、`/tmp/marivo-r26-disclosure.log`、`/tmp/marivo-r26-calendar-final.log`、`/tmp/marivo-r26-final-type.log`、`/tmp/marivo-r26-final-lint.log`、`/tmp/marivo-r26-final-docs.log`、`/tmp/marivo-r26-final-site.log`。本轮未编辑 AGENTS/packaged skills，未提交/推送，未启动远端服务或运行 Runtime/release；受保护技能同步保持待办，不声明全套公开工作流已完成。

**R2.6 三项 P2 修复与复审（2026-10-16）：** 基线仍为 `6a11e96253ce3ea3f518d84fb31e3a3fd333eb42`，完整 R2.6 改动保持未提交。

- 预览在每次下一批解码前按剩余行数调整当前 Arrow reader 的 batch 大小，不再解码完整批后切片。回归覆盖 0、20、65,537、131,073 行请求与完整导出，实际解码行数等于请求行数，单批不超过 64 Ki 行；保留跨行组读取和未消费附件不校验。
- Card 由结果 owner 提供截断恢复提示，默认行为保持原有合同。Artifact 仅提示 `.to_pandas()` 与 `.evidence_digest()`，不再提示不存在的 `show(max_output_bytes=...)`。真实宽表产物回归验证截断后两个入口可调用；共享 renderer 覆盖不同字节预算、标记压缩、行数限制及过小预算错误。
- OutputEvidence/OutputCoordinate 注册为现有 runtime 结果家族的嵌套 Help 类型，ArtifactDigest/Finding 提供可发现路由。覆盖字符串、类型、实例和绑定 show/render 方法解析，保持无新增顶层导出；同步 API、当前执行规格和中英文站点示例。

**修复后验证：** 首轮定向 **23 passed / 77 deselected**；扩大到终端发布/读取、Query 边界、无图结果、共享 renderer、Help/发现/预算/export 的定向 **184 passed**（含首轮覆盖）。完整 lint/import contracts、**323 文件 typing**、API 文档及 **381 个站点文件**检查通过。`make check-agent` 默认测试 **23 failed / 711 passed / 2 errors**，因 maxfail/并行调度提前停止，未进入文档阶段，不记作通过。全部 25 个失败/错误节点已在精确基线归档重新运行并复现（**23 failed / 2 errors**），包括此前基线日志未覆盖的三个 semantic_materializer 节点；确认测试导入归档内的 marivo。API 文档另行运行通过。

**marivo-review 全量复审：** 对照实施计划与当前契约，复核相对上述 HEAD 的全部 R2.6 差异和新增文件，包括统一 reader、公开导出、无图记录、Session 历史、Help 与共享 renderer。原三项 P2 均关闭；实际公开产物重放宽表截断及 Evidence Help 反例成功，当前范围 **No findings**。复审不把基线失败、未运行的远端 Runtime/release 或受保护技能同步记作通过。未修改 AGENTS.md 或 packaged skills，未提交、推送或启动外部服务。

日志：`/tmp/marivo-r26-fixes-targeted.log`、`/tmp/marivo-r26-fixes-lint.log`、`/tmp/marivo-r26-fixes-type.log`、`/tmp/marivo-r26-fixes-docs.log`、`/tmp/marivo-r26-fixes-site.log`、`/tmp/marivo-r26-fixes-check.log`、`/tmp/marivo-r26-fixes-base.log`。

R2.2–R2.4 当时采用来源无损准入；上述代码与验证记录属于历史事实。
后续按[来源交接修订](../../specs/analysis/lazy-analysis-dsl-execution.md#151-ibis-原生返回与-pandas-类型交接)和 §8.1 实施：
schema-only 检查类型族、表达式和后端方法能力，接受原生数值精度损失；实际返回类型在 pandas 交接。
大整数与 NA、Decimal 用例记录真实返回及交接结果，产物按实际保存值验证往返，不以事后 cast 充当精度修复。
R2.5/2.6 复用[场景 §8.7](../../specs/analysis/lazy-analysis-dsl-scenario-validation.md#87-构造披露执行后阅读与重开)，
普通 observe 验收必须覆盖空 Finding 与实际读取/检查 Evidence，断源重开保留原摘要，不虚构完整性认证；
Catalog 条目/Query.contract 在构造期披露定义与依赖，Artifact 只提供终端阅读。
各阶段共用[执行 §2.4](../../specs/analysis/lazy-analysis-dsl-execution.md#24-检查时机与能力披露)的检查时机；
后续 R6 的 share/rollup 沿用同一规则，contract 只披露已知事实和待满足条件，不新增能力状态机。

### R3：统一 Population、明确身份与内部样本

**出口：** 字面量或逻辑名单可参与本次观察；不接受 Artifact 键引用；`Metric.per(...)` 在每个外部格子内形成正确样本。

**模块落点：** `analysis/dsl/population.py`、`analysis/binding/{observe,coordinates,sampling,membership}.py`、`analysis/materialization/population_*.py`、`query_population.py` 和既有 stage 调度；纯绑定留在 binding，执行职责留在 materialization。
**内部顺序：** 复用 S3/S4 的域与样本 → 补 R3.3 明确身份约束及 R3.4 依赖调度 → R3.2 的 read 随 R4.1 Column 消费接通；R3.5 只补新组合缺口。read 的值选择不作为 R4 起步前置，避免阶段循环；QueryKeys 先消费已有观察，R4 再扩展选择后的输入。

| 任务 | 状态 | 目标模块 | 实施内容与产物 |
| --- | --- | --- | --- |
| R3.1 域及唯一物化路径 | 已完成；本轮证据与资格见 §9 | `analysis/dsl/population.py`、`analysis/materialization/{population_preparation,population_relations,query_population}.py` | 复用 identities、coordinates、product、集合运算、project、Query.population 和 Session.query 的当前实现及空/零轴域证据；核对完整轴/复合身份/角色版本/None 键与依赖闭包。只为后续消费者补缺，不重建域执行器；Query.population 不得因仅取键免除上游必要检查。 |
| R3.2 明确来源域操作 | 已完成；本轮证据与资格见 §9 | `analysis/session/core.py`、`analysis/dsl/population.py`、`analysis/binding/coordinates.py`、`analysis/materialization/population_relations.py` | 复用 Session.members(where/at)、classify/image/preimage；按 2026-10-17 用户决策，唯一属性读取入口为 Session.read(population, fields)，接收单身份 Population，显式绑定无主字面量域并检查已有 Session 归属。保留身份与属性读取/版本锚点，返回新 Query；不提供 Population.read，不自动选择或恢复 Session。read 的 Column 选择与 R4.1 一起接通；投影已有轴不新增读取，显式关系路径仍为非空有序元组。 |
| R3.3 绑定身份约束和域谓词 | 已完成；本轮证据与资格见 §9 | `analysis/binding/{observe,membership,coordinates}.py`、`analysis/dsl/query.py`、`analysis/core/graph.py` | 补 Session.observe(members=...) 与 member_count 的显式身份域绑定；members 只限制贡献，membership 是可重叠成员分桶，两者不能互代。复用 by=Population 与 Population.where，保持无约束/空身份域/普通分组/完整输出的区别。完整闭合角色与 Session 归属进入依赖图，捕获域的选择期间不被外层观察覆盖；贡献约束遵循各自合法路径。 |
| R3.4 执行内名单交接 | 已完成；本轮证据与资格见 §9 | `analysis/compiler/{graph_plan,optimization,populations}.py`、`analysis/materialization/graph_{execution,exchange,source_execution,local_execution}.py` | 复用现有域准备与 QueryKeys 的完整 pandas 键交接；将新增依赖统一纳入 stage 调度，补齐 RequiredSlots 的键/值/检查需求传播与消费者计数。计划预先记录键 schema、Ibis 类型化完整元组谓词模板和路线，上游求值后仅实例化；元组内 AND、元组间 OR，合法 None 字段键用 null-safe 相等。同源合格域可 semi-join；DuckDB datasource 先贯通实际路线。不用 memtable、裸 SQL、拆列 IN、业务临时表或拆批聚合，不静默全源拉取。只检查后端原生类型/表达式/查询限制，不加 Marivo 名单配额或预估内存准入；能力/原生限制拒绝复用架构 §9.2 的 DomainPreparationError。逐 datasource/类型准入并退役持久计算 reader；中间表在最后消费后释放。 |
| R3.5 内部样本与状态采样规约 | 已完成；本轮证据与资格见 §9 | `analysis/binding/sampling.py`、`analysis/compiler/observation.py`、`analysis/methods/_sample_numeric.py` | 复用每个 (k,j) 完成子 Metric 后再规约/共同配对、零活动样本、状态采样与 first/last。Field 键像不因单量 NA 缩小；per(P) 排除域外贡献，by=P 仍检查覆盖。允许 product(Population, CalendarAxis) 提供权威内部完整桶样本；它不提供内部窗口端点，窗口仍由 during=CalendarAxis 绑定。Correspondence 先显式 observe/compare，再由 Query.reduce 消费。保留来源/样本方法资格，不把近似完成值视为可合并 sketch。 |

**必要验收：**

- 复用一份订单/客户数据覆盖来源成员、明确 O404 引用、复合键、关系像与有界逆像；显式字面量名单在来源新增订单后仍不扩大，新 read 明确读当前属性；逻辑名单重新 execute 可变化。
- 复用[场景 §3.4](../../specs/analysis/lazy-analysis-dsl-scenario-validation.md#34-固定名单读取属性与精确版本选择)的 C1/C2 固定名单与 Field.at：先由 Session.query 绑定名单，再 read、按 Column 筛选并取 Population；11 月 1 日边界读取 Gold/Bronze，新增 C3 不进入名单。缺少必需属性/版本映射失败，不能补 NA、退回旧版本或重新选人。
- A=100、C=300、P={A,B}：`per(P).mean()` 为 50，实际客户均额为 200，先筛 P 再按出现客户均额为 100；外部 by=P 未限制 C 时仍因缺输出键失败。
- 同一客户横跨地区合法；两设备 `[10,0]` / `[0,10]` 的同时点总量 P95 为 10，不是 19。共同内部配对使用同一 J_k，Field 不能混充 Metric 权重。
- 字面量/Query 名单加收入真实执行，Artifact 断源只读/导出；会话逻辑域可临时观察而模型收录拒绝。覆盖 Session 归属与依赖披露，不再验收固定引用恢复。
- 实测“来源 Query 键 → 完整元组谓词 → 后续来源”和两个不同已声明 datasource ID 经 pandas 组合的最小合格路线；可以同后端，同源多表/多根不能代替跨 datasource 交接证据。已知类型/后端缺口在执行准备失败。用 {(a,1),(b,2)} 不匹配 (a,2) 和合法 None 字段键验证完整键语义，复用同一名单场景。
- 只取 population 键仍保留上游严格 NA 或显式 error 零分母的必要失败。

R3.1 同步 Population.contract 的实例披露，规则归[执行 §2.1](../../specs/analysis/lazy-analysis-dsl-execution.md#21-repr-与输入权威)。
字面量/来源/Query 域均显示有序轴/类型/角色、已有归属、选择期间/属性锚点和依赖；
禁止业务与 schema 读取时仍能披露可判定操作及待检查条件，不枚举未知键集，也不将未执行检查显示为通过。

### R4：结果表达式、选择、最小统计与 pandas 交接

**新语义接入边界：** Column 仍是绑定当前 Query 的有限结果表达式；v0.37 不授权向 Column 注入任意 Ibis Deferred，也不允许 Column 回到 Metric。复用当前数值内核，政策和单位按结果算子拥有。R3.2 read 的值选择随 R4.1 接通，已有基础导出与 keys-only 证据直接复用。

**出口：** Agent 在 Query 上筛选重点、做最小统计，执行为 Artifact 后完整导出到 pandas；普通后处理不扩张 DSL。

**模块落点：** `analysis/dsl/{column,query}.py`、Query 规则/lowering、`analysis/compiler/optimization.py`、pandas 内核和终端 reader/export。
**内部顺序：** R4.1–R4.3 先走 R2/R3 的未融合路线 → R4.4 有限需求/融合 → R4.5 次序读取和完整导出验收。
R2 已切换的 nullable 物理布局在此扩展到全部结果表达式；删除其旧状态谓词/state/reason 消费者，不增兼容层。

| 任务 | 状态 | 目标模块 | 实施内容与产物 |
| --- | --- | --- | --- |
| R4.1 同域 Column 与选择 | 已完成；本轮证据与资格见 §9 | `analysis/dsl/{column,query}.py`、`analysis/core/rules/query.py`、`analysis/compiler/queries.py` | 实现有限算术、Column.divide、比较、is_null、coord、select、where，绑定精确输入域；divide 与来源阶段共用默认 null/显式 error 的节点政策；where 只选 True、NA 不入选，保留完整键/必要检查。标量单位按使用位置解释，已知单位冲突在 Column 构造报 AnalysisError；结果域谓词只投影已有坐标。where/top 等后继的 QueryKeys 接回 R3 域依赖路径，不执行后再导入 Artifact。 |
| R4.2 Top-N 与展示次序 | 已完成；本轮证据与资格见 §9 | `analysis/methods/local.py`、`analysis/core/artifact_data.py`、`analysis/materialization/graph_storage.py` | 全局/分组 Top-N 的评分、分区和选中行消费同一实际输入；NA 排末，同分按完整键决胜。order part 只保存 receipt 绑定的主表行号排列，完整键留在主表，读取不重建键索引；where/select 保序，重 top 重排，改坐标恢复规范序。 |
| R4.3 最小 reduce 与 values | 已完成；本轮证据与资格见 §9 | `analysis/core/rules/query.py`、`analysis/methods/{registry,numeric_state,local}.py`、`analysis/compiler/queries.py` | Column.sum/mean 与无列 row_count 由 reduce 绑定，各项独立 mask，不取全列非空交集；结果 ignore-NA 不继承来源 nulls 政策。values 通过 R3 LiteralInput 规范化，columns 映射唯一声明单位，不接 DataFrame 回灌。 |
| R4.4 有限需求传播与局部融合 | 已完成；本轮证据与资格见 §9 | `analysis/compiler/{optimization,graph_plan,graph_lowering}.py`、`analysis/core/local_laws.py`、`analysis/materialization/graph_local_execution.py` | 按架构 §8.4 顺序扩展 RequiredSlots → 精确选路 → 执行内投影/局部融合 → 同根来源合并 → nullable lowering；复用 local_laws，不另建优化 IR/registry。将选择/格子规约融合组、原节点/检查位置写入计划，Runtime 只执行；保留多消费者屏障。Deferred 分支完整进入静态/schema 检查，动态数值检查按实际分支需求域执行；裁列或只取键不能免除上游必要失败，也不能强迫未选分支执行动态检查。同根组件合并限相同贡献/期间/政策及已准入数值合同，不能由实数结合律或采用 fsum 授权浮点重排，缺少依据就不融合；原指标融合在 R6 扩展。按 §8.5 实现 invocation 内编译复用，registry/绑定变化即失效，不跨 execute 缓存结果。 |
| R4.5 次序阅读与完整 pandas 导出 | 已完成；本轮证据与资格见 §9 | `analysis/materialization/{graph_reading,graph_dataset,pandas_export}.py`、`analysis/core/artifact_data.py` | PreviewRead/ExportRead 共享 ReadLayout；Top-N 预览按 order part 定位必要行组并恢复展示序，导出沿规范完整键序。固定 RangeIndex、kind/name 两层列与纯数据 attrs，精确 dtype/空表/仅键和隔离副本完整保留；读取/转换/实际分配失败沿现有错误边界处理，不截断、不刷新来源/Catalog。 |

**必要验收：**

- 一个含同分、None 分类和 NA 值的小结果验证 where/top 顺序、分组选择和 n=0 必要检查；去掉评分输出列后冷恢复仍保留原 Top-N 展示次序，to_pandas 使用规范完整键序。
- 同一 Query 验证 row_count 计完整坐标，sum/mean 的 NA 忽略与空输入政策；不同 Query 的 Column 不能因行数相等混算。
- 一次真实 Artifact 导出往返读取覆盖 nullable 值、复合身份、大整数/Decimal、时区及空/仅键结果；修改导出的值或嵌套 attrs 不改变原答案。pandas 示例只验证交接后的配方，不替代导出器验收。
- axes.kind 只取 member/field/calendar/correspondence，column 稳定定位导出列，label 仅描述；保持声明的轴顺序。复用场景附录 C 的唯一 kind、同 kind 顺序解构和标签变化配方，补真实导出 metadata 断言，不增加 Ref 恢复入口。
- 优化前后比较完整键、NA、次序和失败，数值按方法精度比较；复用 [1e16,1,-1e16] 的 fsum 分组反例禁止未经准入的重排；显式 error 零分母在 select 丢列后仍失败，`sum(x+y)` 不跨不同 mask 分解，`where(~p)` 不变成域补集。
- 宽中间表只消费两个量时，计划去除无关 slots 并保留必要检查；`m+m` 共享子图编译不指数展开。

### R5：对齐、比较与参照运输

**接入调整：** 主体目标保留，消费当前 Deferred 准备后的量、单位、逐使用位置政策与完整键；不恢复旧 Metric 包装类。期间对应与 R7.2 共用此处映射/比较链，presence 和终端投影同时接 writer/reader。

**出口：** 两个 Query 可以按明确对应组合，缺坐标由 presence 区分；量值使用普通 NA，输入规约保留自己的 empty/strict 政策。

**模块落点：** `analysis/binding/coordinates.py`、`analysis/methods/{comparison,references}.py`、`analysis/compiler/queries.py` 和终端投影读取；沿 R3 参数依赖和 R4 本地计划执行。
**内部顺序：** R5.1 明确对应/完整映射 → R5.2 比较与 R5.3 广播；新增 presence/投影同时完成保存与读取。

| 任务 | 状态 | 目标模块 | 实施内容与产物 |
| --- | --- | --- | --- |
| R5.1 align 与完整对应 | 已完成；本轮证据与资格见 §9 | `analysis/binding/coordinates.py`、`analysis/core/rules/query.py`、`analysis/compiler/queries.py` | 同绑定 exact/outer、日历 ordinal 和声明关系 corresponding 在消费处绑定两侧实际轴；完整键唯一/覆盖检查属于计划。ordinal 要求相同 Grain、相容日历与完整名义桶；exact 桶数相同，桶数不同时 outer 保留日历尾部；结果过滤不压缩日历序号，完整键仍按 how 检查，不重采样。关系值按 R3 明确依赖读取；保存独立 boolean presence 与端点投影，NA 值不决定 presence，Artifact 不参与绑定。 |
| R5.2 compare 与端点权威 | 已完成；本轮证据与资格见 §9 | `analysis/methods/comparison.py`、`analysis/dsl/query.py`、`analysis/core/artifact_data.py`、`analysis/materialization/graph_{storage,reading}.py` | 同名量产出 current/baseline、absolute/relative；检查类型、单位、已知语义约束和完整对应，业务可比性由脚本判断。relative 为差值除以基期绝对值，zero_denominator 默认 null、显式 error；仅控制新 relative，不覆盖输入子式，默认零基期保留有效 absolute；align 保持 left/right。输出保存值、presence/投影，不存细状态或可回放来源，compare.drill 不准入。 |
| R5.3 broadcast 与显式参照 | 已完成；本轮证据与资格见 §9 | `analysis/methods/references.py`、`analysis/core/rules/query.py`、`analysis/compiler/{queries,optimization}.py` | 以完整且唯一的目标键到参照键投影运输值；零轴/分组参照共用 R4 values 或来源 Query 路线，不按行位置或同名字段猜映射。RequiredSlots 保留映射/presence 与检查支持；普通比值不获得 share 的贡献支持证明。 |

**必要验收：**

- 一对有缺侧及零基期的端点同时验证 exact 拒绝、outer 的 presence/NA、默认 relative=NA 与有效 absolute、显式 error 和端点投影；缺侧先限制 presence 才能提取该侧身份。外层 null 不覆盖输入严格子式的失败。
- 接通[场景 §3.5](../../specs/analysis/lazy-analysis-dsl-scenario-validation.md#35-不同-entity-的声明身份对应)的两个 Entity 及完整复合主键，用 corresponding(via=...) 完成 align/compare；exact 缺侧失败、outer 保留 presence，普通字段轴及非一对一路径拒绝，不把同名键文本当作身份对应。
- 用一份分组预算验证完整广播和缺参照键拒绝；两端先组合再 execute，保存比较 Artifact 后断源读取；两个已保存产物只在 pandas 比较，不能把对应关系解释成共同读取或共同人群证明。
- Artifact 作为 compare 输入在构造时拒绝（其它算子共用同一参数类型，不逐个复测）；重新执行端点 Query 是新求值。时间 period_offset/previous_year 的扩展在 R7 完成。
- 同一对照含“存在但值 NA”和“缺端点”两种行，保存/重开的 presence、端点投影和完整导出保持区别。

R5.2 的一般 compare 不要求两侧来自同一 Metric；复用来源与字面量预算端点，
验证量名非空且相同、类型/单位与坐标约束满足时可以比较，量名/单位不相容仍拒绝。
同一指标的期间比较只是特例，不因数值成功自动证明业务可比性。

### R6：原指标上卷、来源细化与份额

**新语义准入：** 运输充分信息以当前受控配方、规约/声明边界、贡献使用位置和样本身份为依据，不按旧 Add/Divide 数值节点枚举重建语言。empty、strict、分支需求、first/last 的排序/NA 政策与近似方法路线均随实际消费者保留；没有合格合并信息时不能用完成值替代。membership 的重叠桶不因原量可加便可直接相加，相关元组和独立集合乘积保持区别。

**出口：** Agent 能区分原指标 rollup、当前格子 reduce 和新来源 drill；份额有真实总体与共同支持。

**模块落点：** `analysis/core/rules/grain.py`、`analysis/compiler/optimization.py`、`analysis/methods/{numeric_state,references}.py`、观察绑定与 lowering；充分信息由本次计划拥有。
**内部顺序：** R6.1 需求/运输 → R6.2 rollup、R6.3 drill、R6.4 share 的未融合路线 → R6.5 有准入证据的融合。

| 任务 | 状态 | 目标模块 | 实施内容与产物 |
| --- | --- | --- | --- |
| R6.1 运输实际充分信息 | 已完成；本轮证据与资格见 §9 | `analysis/core/model.py`、`analysis/compiler/optimization.py`、`analysis/methods/numeric_state.py`、`analysis/materialization/graph_exchange.py` | 扩展反向需求到按量组件、政策、完整键与支持事实；来源 mean/weighted_mean 的组件、Ibis 配方中的除法、PerReduce 等语义边界分别准入。只将键、绑定与运输规则相同的组件放在同一块；共享物理组件取消费者需求并集，不按标签去重。必要内部身份/细状态仅为实际消费者保留，不能凭投影恢复丢失分组名单；最后消费释放，不进入 Artifact。 |
| R6.2 原指标 rollup | 已完成；本轮证据与资格见 §9 | `analysis/core/rules/grain.py`、`analysis/compiler/{observation,queries}.py`、`analysis/methods/numeric_state.py` | 合法 coarsen/现有轴建立完整唯一映射；合并选中坐标的实际贡献/充分组件再 finish 原公式，保留细网格完成检查，不替换为粗 observe。常数平移、ratio/per 按实际配方依赖重算；窗口量仅允许合格空间上卷，必须原样保留已绑定时间轴及每个端点/W(t)，粗化、替换或删除时间轴均拒绝；完整输入合格即可直接求值，不强制所有 reducer 实现 merge。 |
| R6.3 来源 Query drill | 已完成；本轮证据与资格见 §9 | `analysis/binding/{observe,coordinates}.py`、`analysis/core/rules/grain.py`、`analysis/compiler/{observation,populations}.py` | 父域 where/top 的选择先完成，再读取所选纤维；保留父检查、上下文和参照，新分类检查限所选父纤维。来源角色进入真实 stage 依赖，Artifact/compare/任意 align/reduce/份额派生输出不借 lineage 回源。 |
| R6.4 自动/分组 share | 已完成；本轮证据与资格见 §9 | `analysis/methods/references.py`、`analysis/core/rules/query.py`、`analysis/compiler/{graph_plan,queries}.py`、`analysis/methods/local.py` | 唯一 Query.share 保留原列并新增份额列；zero_denominator 默认 null、显式 error，仅作用新份额；先将接收者及必要组件形成一个实际 pandas 输入，再构造分组原量总体、完整分母映射，检查可加性、分子贡献包含及单位/政策。后置 where/top 不改分母；缺共同输入/组件路线明确拒绝。Artifact 只保存显式输出和实际事实，份额列不继承原量 rollup/drill 权。 |
| R6.5 扩展原量融合与同输入屏障 | 已完成；本轮证据与资格见 §9 | `analysis/compiler/optimization.py`、`analysis/core/local_laws.py`、`analysis/materialization/graph_local_execution.py` | 将原量规约融合准入从 Runtime 移到计划；复用 local_laws 的实现编号 L8 等现有检查（理论对应核心 L6，编号表由核心文档拥有），编码组件需求、mask/键运输、原检查定位和同输入物化边界。保留多消费者；share/filter、per、原量完成检查不可交换。解释采用/拒绝规则及屏障，不新增公开优化开关。 |

**必要验收：**

- 同一收入/AOV Query 验证合法原上卷；`revenue + 10` 的地区 110/310 总体为 410，而非 420。本次路线缺 distinct 精确集合、quantile 样本或内部身份时拒绝，不将最终值当充分信息。
- 复用[场景 §7.12](../../specs/analysis/lazy-analysis-dsl-scenario-validation.md#712-截断月桶的原指标上卷与月内份额)的四日跨月数据：coarsen 后 rollup 得收入20/1000、订单2/100、AOV均10，按月 share 保留四日份额1/2、1/2、1/10、9/10。范围不扩张，跨月周不能强行归月；附录 L 只作独立数值参照，R7 复用时间拒绝边界。
- PerMean 使用“两个地区同一客户/不同客户”的 400/200 反例，证明仅地区 sum/count 不足；信息足够的路线通过，缺合格内部信息路线时拒绝。
- 用“粗比较 Query→逻辑异常域→分别细化端点→比较→execute”验证执行前组合；另以外部显式 West 键验证多轮脚本选择、父 exact 不保证细 exact、缺侧不补零。只保存粗比较 ID 不能恢复细项。
- East=320/West=150：先 share 再选 East 保留 320/470，先选 East 再 share 得 1；分组 share 在组内构造分母。来源模式必须使用合格共同输入路线；无此路线时报具体执行能力缺口，不建议先 execute 再 share，不能用两次独立读取补证明。
- 覆盖有符号分母、NA 分子、默认零分母 NA 及显式 error 失败；原输入的必要检查不能省略。
- 计划中 share 的分子、分母消费同一 stage 输出；优化与基线在上述反例及失败上一致。
- 复用已有 membership 场景验证重叠桶不能当互斥贡献相加；用一个已绑定窗口 Query 验证保留时间轴的合格空间上卷和时间轴粗化/删除拒绝。复用分支与 empty 政策输入核对组件运输，不增加每算子×每政策矩阵。

### R7：原指标窗口、累计与期间比较

**出口：** 能从完整所需贡献计算滚动、累计和两期变化；输出范围与贡献范围分别保留，日历认证范围按实际请求检查。

**模块落点：** `_definition/windows.py`、`analysis/dsl/{windows,temporal}.py`、`analysis/binding/{windows,observe,sampling,coordinates}.py` 与现有 observation/compiler；期间对应随 R5 消费者扩展，基础时间权威继续归 `_temporal` 和语义日历 owner。
**内部顺序：** R7.1 复用 S5 已交付的 W(t) 绑定和完整公式重算；R7.2 在 R5 对应/比较可用后接通。基础窗口与期间比较不等待 R6；只有空间上卷组合消费 R6 的充分信息准入，时间轴仍不得改动。

| 任务 | 状态 | 目标模块 | 实施内容与产物 |
| --- | --- | --- | --- |
| R7.1 窗口与完整贡献范围 | 已完成；本轮证据与资格见 §9 | `_definition/windows.py`、`analysis/dsl/windows.py`、`analysis/binding/windows.py`、现有 observation/compiler | 复用 all_history、trailing_duration、trailing_periods、since、to_date 无轴规则与 Metric.window；observe 固定端点和 W(t)，每个端点从完整贡献重算公式。复用 Grain/PeriodCalendar/时区、重叠窗口 occurrence 与完整日历检查，只补新 Query 消费者需要的绑定/输出；不新增 Query.window、ObservationWindow during 输入或第二套窗口执行器，不以滑动摘要为前置。 |
| R7.2 期间对应与输出边界 | 已完成；本轮证据与资格见 §9 | `analysis/binding/coordinates.py`、`analysis/methods/comparison.py`、`analysis/compiler/{windows,queries}.py`、`analysis/core/artifact_data.py` | 接通 period_offset、previous_year 与受约束 current 时间轴；各端先求窗口再对应；日粒度闰日无对应时 exact 失败、outer 保留缺侧，完整月/季/年含闰日仍可同名配对，不减 365 天或夹日。非法包含/粗化失败；换窗口构造新 observe。输出保存绝对坐标、时区/实际边界与投影，不存窗口/日历对象；沿同一 reader 重开。 |

**必要验收：**

- 共用一份时间数据跑七日收入、MTD/YTD、AOV 和同比；验证显示起点不截断输入，AOV 从组件重算，P95 从完整样本求值，不能平均完成的日值。
- 选择一个 DST 期间和一个认证财务日历覆盖日历差异；用日粒度闰日的 exact/outer 与完整二月的合法同比验证差异，跨月周拒绝非法包含；复用 ordinal 场景检查不同粒度/截断桶拒绝。报告时间构造拒绝 naive datetime，来源 naive 时间仍按来源合同处理。不为每种 grain 重复整套场景。
- 无完整性声明时按已读数据求值，空日按规约完成；认证日历不能覆盖所需期间或来源读取失败时拒绝。成功执行不生成业务完整性认证，外部漏数通知不属于首版输入。
- 一次时间结果断源冷恢复验证输出坐标、时区/实际边界与数值；不对每个窗口变体复制恢复测试。

R7 的窗口按完整 W(t) 求原指标；用已有精确去重反例确认两天 A/A 的日计数不能累计成窗口人数2。
Cum 的数学零延拓不生成期初业务格子或补缺测；细桶值求和仅在[代数 §5.4](../../specs/analysis/algebra-theory.md#54-固定域上的线性与时间边界)前提成立时准入。

### R8：公开切换、文档同步与整体收尾

**已有成果：** S6 已完成本次语义载体切换的 Help、类型、导出和英中文档；复用其记录。R8 聚焦后续 Query 操作的增量与跨入口核对，不重新展开 S0–S6 或修改历史审查结论。

**出口：** 目标能力只有一条公开路径，当前文档和发现入口一致，代表性业务链完整通过；剩余限制准确披露。

**模块落点：** `semantic/`、`analysis/` 的 exports 与各自 `_capabilities/`、`_help/`、CLI、typing/surface 测试及当前规格/API/site 文档。
**内部顺序：** 核对 R1–R7 出口 → R8.1 清理残留 → R8.2/R8.3 全局披露同步 → R8.4 整体验收；不在此补各阶段尚未完成的执行、codec 或 reader。

| 任务 | 状态 | 目标模块 | 实施内容与产物 |
| --- | --- | --- | --- |
| R8.1 完成公开切换和旧入口退出 | 已完成；本轮证据与资格见 §9 | `semantic/__init__.py`、`analysis/{__init__,_public}.py`、旧 runtime_metric/public_dsl 及其实际消费者 | 收齐各阶段已替换的工厂、旧政策、包装与别名；确认 Query/Artifact/Population/Column 直接导向唯一实现。复核依赖后删除无用 fixed/Cell/图恢复路径，不批量删除仍有内部消费者的无关算法。无 Artifact DSL、MetricSamples 顶层导航或长期转发适配。 |
| R8.2 收齐披露与类型合同 | 已完成；本轮证据与资格见 §9 | `semantic/_capabilities/`、`analysis/_capabilities/`、`_help/`；`tests/typing`、`tests/surface` | 核对各阶段 docstring/Help/错误/CLI 和 exports、可达性、Help 输出预算；目标公开 mv.TimeScope、mv.Population 支持示例注解，TimeScope 复用既有导出，支持类型仍随所属能力发现。具体参数/返回类型与跨阶段负向 typing 一致。Catalog 条目与 Population/Query.contract 读所属纯规则；Deferred 不新增 .contract/.show 包装；Artifact 只读已提交输出，无 contract/续算提示。 |
| R8.3 同步当前文档与工作流 | 已完成；本轮证据与资格见 §9 | 当前 semantic/analysis owner、`docs/api`、英中 latest 示例；受保护文件见 §7 | 收齐随行为更新的文档，清除旧构造/保存/续算说明；核对在对应阶段准备的技能/AGENTS 同步稿，仅在明确授权范围内编辑。提案状态只随真实能力推进，未授权项保留待办。 |
| R8.4 完成集成验收 | 已完成；本轮证据与资格见 §9 | §5 的现有测试 owner、Makefile 入口及执行记录 | 六条业务链与 §5.5 架构条件收齐后执行 make check-agent；受影响远端特化路线单独补 Runtime。列明真实已验类型/输入/后端，汇总通过/失败/未运行/未完成；不追加发布或全后端矩阵。 |

**必要验收：**

- 收入、AOV、固定人群均额、先空间后时间 P95、共同配对五类 Metric，在声明体和临时表达式下共用相同计算合同；用同一组实际输入比较，不重复两套基础设施。
- 主业务链能从 Help 找到正确入口，经 Query 组合、实际 execute、产物阅读和 pandas 交接完成；旧入口不再出现在当前 exports/Help/示例或错误修复中。
- 静态导入检查确认定义层不反向导入执行层；compiler 无 I/O、Runtime 不重新选路等结构约束由代码评审确认，不另建运行时探针。
- make check-agent 通过；目标内未实现能力、必要用例失败和因授权尚未同步的工作流分别列出，不能用“其余测试通过”把它们结项。

## 5. 验收组织：覆盖关键行为，避免重复工作

### 5.1 六条业务链复用已有场景

S3/S4 的域、样本、membership 与 S5 的窗口已有行为证据；R3–R8 补齐 Query 组合后，六条业务链已通过独立本地验收。以下保留固定预期与覆盖组织，不将数值 oracle 本身当作实际执行证据。

按场景文档的数据和独立预期运行新 API；每条链只在一个主测试位置维护。
上游 milestone 已验证的步骤直接复用，不再次为每个场景复制格式、权限和全部异常测试。

| 业务链 | 必须观察到的结果与边界 | 收齐阶段 |
| --- | --- | --- |
| 电商多根与追问 | 净额 320、East 变化 −110、逐单毛利 80；先声明新业务量，比较后由 pandas/脚本选择异常键，构造新端点分别细化；新 channel 缺侧不补零 | R2、R3、R5、R6 |
| SaaS 固定客户群 | NRR 62.5%；Query 绑定显式身份并比较，pandas 提取流失身份，pandas 完成 GRR 50% 与分类；外部标签不回灌 | R3–R5 |
| 制造比率与参照 | 原率 12%/18.5%；组件上卷与 50/50、20/80 标准率分开；参照缺正权重层不能静默忽略 | R4–R6 |
| 物流完整网格 | 六格、九件、八有效，总体 P95=66.05；来源直接求值合法，仅有完成 P95 的 Artifact 不恢复总体分位数；Top-N 显式处理 Null | R2–R4、R6 |
| 实验分析单位 | 按完整 workspace 域保留零活动样本，均值 100/3；分层和 P90 交 pandas，不按事件行误充实验单位 | R3–R5 |
| 时间窗口与比较 | 七日 1220/1200、MTD 1220/1300、YTD 1920/2000；分别计算两端窗口，处理期间对应和日历认证范围 | R7 |

这些场景不要求外部真实生产数据或真实模型 Agent 参与，也不据此声称生产数据质量、业务结论或 Agent 端到端资格。
本地真实来源执行即可验证目标 DSL；独立数值 oracle 只提供预期值，不替代新 API 的执行。

### 5.2 与设计稿验收条目的对应

下表将 [场景 §9.1](../../specs/analysis/lazy-analysis-dsl-scenario-validation.md#91-最小反例与修复判据)
的 1–80 条分配给主验收 owner。跨阶段行为可引用上游证据，不重复建设测试。
这是防止漏项的索引，**不是 80 个任务、80 个测试或额外验收门禁**。表中 R 编号是主 owner，不表示尚未开发；已有 S3/S4/S5 证据按 §3 与任务行归入 R3/R7。v0.37 的分支需求、empty、membership 和窗口时间轴限制在本计划相应任务补组合断言，不另增一套编号或重复台账。

| 主归属 | 条目编号 |
| --- | --- |
| R1 | 26、41、46、47、63、64、65、66、69、70、79 |
| R2 | 2、24、40、42、43、44、48、53、58、68、77、80 |
| R3 | 1、5、7、11、13、14、39、45、49、50、54、59、61、62、67、71、72、74、78 |
| R4 | 8、10、15、17、22、25、27、28、29、30、52 |
| R5 | 3、20 |
| R6 | 4、6、9、12、16、18、19、21、23、51、55、56、57、75、76 |
| R7 | 31、32、33、34、35、36、37、38、60 |
| R8 | 73 |

例如 R1 的“同图同政策”在 R2 增加真实值，R3 增加域依赖；这是同一合同逐步接通，不要求每阶段重跑所有下游场景。
每条必要语义最终应能指向实际用例或该主场景中的断言；不能把“索引已分配”当作实现通过。
场景 §9 的补充代码索引复用这些条目：属性版本读取由 R3 接通，身份对应由 R5 接通，粗化上卷/份额由 R6 接通，披露阅读随 R2–R4 扩展；不重新分配各条 owner，也不新增外部缺口工作包。

场景 §9.2 的终端/无图合同由 R2 主交付，R3 仅键、R4 次序/导出、R5 投影、R7 时间输出复用并补其增量；
§9.3 的统一 pandas 路线由 R2 建立，R3 接名单/多来源，R4 接选择/规约，R5 接对齐，R6 接同输入 share。
§9.1 的 24/44 因而归 R2，声明授权和闭包收录的 26/65/66 归 R1；它们的下游使用不等于重新拥有合同。
架构专项条件按 §5.5 分配，不把它们塞进业务数值 oracle 或等到 R8 才建立。

### 5.3 测试落点与运行尺度

复用 [tests/conftest.py](../../../tests/conftest.py) 与
[tests/shared_fixtures.py](../../../tests/shared_fixtures.py)；修改 fixture 时遵循仓库的 marivo-test-fixtures 技能。
优先更新当前 owner 附近的测试，仅在没有合适位置时增加主题明确的模块：

| 范围 | 现有测试落点 |
| --- | --- |
| 表达式、声明、加载 | tests/semantic 中的 metric_authoring_surface、semantic_expression_binding、semantic_loader、metric_expression_graph、semantic_metric_graph_lowering |
| 观察、域、方法图、需求/融合 | tests/analysis/graph、tests/analysis/numeric、tests/analysis/session；优先沿 test_analysis_lowering、test_identity_observation_lowering、test_observation_source_bindings 扩展 |
| 阶段交接、发布、冷恢复、按需读取 | tests/analysis/materialization；优先改 test_analysis_graph_publication、test_graph_dataset_reads 及现有 recovery worker；旧恢复用例改验终端合同 |
| 时间绑定与窗口 | tests/analysis/temporal |
| 类型与发现 | tests/typing、tests/surface、tests/semantic 中的 live_help/help_examples |

开发中先跑实际改动范围，例如：

```sh
make test TESTS='tests/semantic/test_metric_authoring_surface.py tests/semantic/test_semantic_loader.py'
make typecheck TYPECHECK_TARGETS='marivo/semantic tests/typing'
make lint-agent LINT_TARGETS='marivo/semantic tests/semantic tests/typing'
```

后续阶段换成实际改动模块和测试路径；不照搬上面示例把无关目录重复跑一遍。
共享行为修改按仓库规则补 make test，最终整体检查使用：

```sh
make check-agent
```

仅当改动触及现有远端特化实现或确实要声明该路线资格时，运行对应实际路径的 `make runtime-test TESTS='...'`。
当前 pytest 默认排除 runtime/release/slow；因此默认测试通过不能写成这些验收通过。
需要的本地集成检查若位于 slow 集合，只跑相应范围，不据此启动整个 release-check。

本计划要求首期每项目标能力有一条真实可执行路线：普通来源使用 DuckDB datasource 的 Ibis 查询，
本次中间计算使用 pandas/现有数值内核，PyArrow 只负责终端输出存储/读取；不能用 DuckDB 代替本地 pandas 阶段。
名单交接以“字面量/Query 键＋DuckDB 来源”贯通。方法/类型/输入形状的声明不得超过实际已验路线。
不要求所有后端组合；超出已支持资格的输入维持明确错误，不偷偷使用另一算法或退回任意 Python。
若没有改动打包内容，不另加 wheel/install/release 验收；不为普通开发启动 MinIO 或全部远端服务。

### 5.4 冷恢复与失败验证的最小集合

重开验证复用普通多量、仅键、Top-N、比较/份额和时间输出，验证统一终端合同：

- execute 与 Session.artifact(id) 返回同一 Artifact 接口，没有 origin/fixed/cold 或 producer 引用。
- execute 提交成功 → 同进程读取 → 跨进程重开的键、值、dtype、NA、presence、投影、次序和实际 Finding/Evidence 一致；释放 producer 后仍可读取，不保留内存产物分支。
- 独立进程关闭来源与 Catalog 仍可 show/to_pandas/findings/evidence_digest。
- 旧 Store 的 Session.resume 拒绝并保留文件；同代际重开在模型改名/删除后仍读历史值。
- DSL 构造拒绝以 Artifact 充当计算依赖；保留 Query 再 execute 重新求值。
- 失败无部分产物；普通 observe 的 Finding 为空，读取范围和实际检查摘要保存为 Evidence。

现有 publication、格式拒绝、资源清理和并发回归继续承担已有职责；只为新的失败边界增加必要用例。
不构造所有算子 × 所有后端 × 生命周期矩阵，不扫描存储证明某类对象“不存在”，也不测试无关私有对象恶意变异。

nullable 专项验收与 [架构方案](../../specs/analysis/analysis-implementation-architecture.md) 同步：

- 来源/内部样本 sum 默认空输入和全 NA 为 0，显式 empty="null" 为 NA；strict 的全 NA 仍失败，不能被 empty 政策掩盖。Query Column.sum 保持固定默认合同；mean 的和/有效数只用于本次执行；Artifact 只保存完成均值，显式输出组件除外。
- 0/0、非零数/0 默认产生可导出的 NA；NA/0 在两种政策下均为 NA。显式 error 的零分母与实际覆盖检查失败使 execute 失败，不发布部分结果。
- where 的 NA 不入选；where(~p) 不替换成 Population 补集；合法 None 分组键不丢弃。
- 优化与未优化保持相同 mask/有效样本及必要失败；sum(x+y) 不跨不同缺失 mask 分解。
- 导出只有 coordinate/value/presence；旧四态格式拒绝。

### 5.5 架构验收：阶段归属与必要条件

将[架构 §15](../../specs/analysis/analysis-implementation-architecture.md#15-架构验证与完成判据)的条件并入各阶段，下表是同一工作包的验收，不建立第二份台账。
只验收可观察的合同结果；“不存在某实现”类结构约束由类型、静态导入检查和代码评审承担，不建运行时探针。

| 收齐阶段 / 工作包 | 通过条件 |
| --- | --- |
| R1.1–R1.4 定义边界 | 定义层不导入 Catalog/registry/Runtime；禁业务读取仍可构造/加载；`m+m` 使用位置与真实循环可辨 |
| R2.2–R2.4 绑定/编译/执行 | 来源与本地阶段输出的 dtype/NA/完整键一致；纯来源计划可直接发布 |
| R2.5 发布与新格式 | 宽 primary、nullable 与零轴 0/1 行往返一致；失败无部分产物，旧提交可读；旧格式拒绝 |
| R2.6 元数据打开与预览 | 禁 Parquet 数据读取仍能 artifact(id)/repr/findings；show(20) 有界，导出完整 |
| R3.4 依赖键/多来源 | 字面量/Query 完整键驱动元组谓词读取后续来源；两个不同 datasource ID 经 pandas 组合得出正确结果 |
| R3.4 → R4.4 需求闭包 | 宽中间表只消费两个量时无关 slots 不进入计划；只取键/计数不逃过原必要失败 |
| R4.4 → R6.5 优化对照 | 同一路线优化前后完整 keys/NA/次序与失败一致，值满足方法精度；覆盖除零、per、rollup 与 share/filter 屏障 |
| R4.3/R4.5、R5.2、R7.2 输出读取 | 大整数/Decimal/时区、NA、presence、空/仅键、投影与次序随对应阶段往返一致 |
| R6.1/R6.4 同输入与充分性 | share 分子/分母在计划中消费同一 stage；400/200 与 410/420 反例正确；无支持路线明确拒绝 |
| R7.1–R7.2 窗口 | 显示起点不截断 W(t)；认证日历覆盖不足拒绝；两端窗口后再对应 |

性能不设验收门槛。只有当某项优化声称减少工作时，才对该优化做一次定向测量（扫描/stage 数或耗时，注明机器与版本）；
存储参数沿用 §6.3 起点，不做压缩算法对照、RSS 或 10/100/500 节点系列基准。
架构记录的单版本 Parquet 探针不能当作实现或性能验收；本轮文档校验不填写这些项目的通过状态。

## 6. 存储切换与交付顺序

### 6.1 一次不兼容格式切换

| 协议 | 已核对现状 | 目标处理 |
| --- | --- | --- |
| Session Store | 10（R2.5 已启用） | 一次终端产物及无图输出记录格式切换；旧代际拒绝并保留 |
| graph DAG | v10 | 不进入新结果格式；停止结果图写入/解码，不发布 v11 图快照 |
| descriptor | terminal_output/v1（R2.5 已启用） | DataSchema + 输出 receipt/附件，Finding/Evidence 无图；旧 descriptor v6 不进入新格式 |
| continuation | v5 | 从结果协议移除，不发布 v6 或旁路恢复闭包 |

R2 首次贯通，后续在同一未发布代际补齐输出数据字段。实施时刷新实际版本，不按文档修订连续升代。
旧格式明确拒绝并保留原字节；旧 Session 不能 resume，更换其名称不能绕过 Store 代际。错误给出实际/期望格式，指向保留旧目录、在新格式 Store 新建 Session 后重跑脚本；无迁移、双读、删除旧项目状态或隐式重放。
测试只使用临时项目。回退代码不改写新数据，恢复合同只保证同代际输出数据读取。

### 6.2 终端输出与执行状态分开

默认保存输出键、命名 nullable 值、dtype/单位文本、presence、实际投影、展示次序与实际 Finding/Evidence。
普通 observe 的 Finding 为空，读取范围和实际检查摘要仍按[执行 §1.2](../../specs/analysis/lazy-analysis-dsl-execution.md#12-默认保存信息)保存为 Evidence。
Query/执行计划持有定义和本次中间组件；Artifact 对象及其持久化都不持有这些信息。
需要分子/分母/计数供 pandas 使用时显式输出；不为未知未来问题保留组件、图或可执行证明链。
移除固定产物输入及其扫描/准入/缓存路径；仍需保留执行内 stage、本地内核和可测的内存/物化边界。
新产物与重开产物都通过已提交 Parquet/记录读取；Arrow 编解码对象仅活跃于读写调用，不成为 Artifact 的缓存句柄。

### 6.3 输出格式随能力扩展，读写策略由存储 owner 统一管理

R2 首先写一个规范完整键序的宽 primary：完整键、多量 nullable value 与输出需要的独立 presence，物理 slot ID 映射回输出名。
零轴无量用内部 `__unit: uint8` 保存 0/1 行并核对 manifest，不能进入公开量或导出。
R3 补仅键/复合域真实输入，R4 补 Top-N order part 及 receipt 绑定的行定位，R5 补端点投影，R7 扩展窗口/期间对应的输出边界；
每项扩展同步 writer/reader，不为每个量拆文件，不写内部组件，不建立按业务维度自动分区树。
order part 只保存展示顺序对应的主表行号，发布时校验完整排列；不重复保存完整键或保留无行定位的第二格式。

R2.5 按[架构 §12.6](../../specs/analysis/analysis-implementation-architecture.md#126-parquet-布局顺序与写入参数)
落实一个内部 storage policy：Parquet version=2.6、冻结 schema、禁止时间精度截断；
zstd level 3、row group 最大 128 Ki 行、扫描 batch 最大 64 Ki 行，预览再按剩余行数缩小，保留标准统计/适用字典。
这些是工程起点，出现实际问题再调整；行组/批行数不保证计算内存上限，不增加公开 DSL 调参项。
较大输出可分段编码以减少完整 Arrow 副本，仍承认上游 eager pandas、排序和中间副本成本。

R2.6 只校验实际消费的 parts，同一读取调用内复用 receipt 结论；首次摘要仍可能读取整份文件。
行级检查仅针对本次解码部分，不再额外全表扫描值；show 的有界性指解码与内存，不保证仅少量 I/O 字节。
R4 的 Top-N 预览先取定位再读相关行组，ExportRead 始终沿规范键序完整收集；不增加结果谓词下推或计算 reader。

沿既有 journal 将 staging 文件关闭/同步并就位，再以 SQLite 事务提交 descriptor、parts、Finding/Evidence 与 Run 成功。
SQLite 提交是可见边界；rename 和数据库不构成跨系统事务，失败由 reconcile 回收未提交输出且保留已提交产物。
R8 只核对这些出口已完成，不再集中补存储代际、故障清理或恢复实现。

## 7. 文档、技能与授权边界

各 milestone 同步自己改变的公开行为；R8 收齐横跨入口的文档：

- semantic 当前对象模型、加载/校验与 authoring 工作流。
- analysis 当前设计、算子、Session/Runtime、时间语义与保存合同。
- [docs/api](../../../docs/api)、CLI 和当前示例。
- 英文 [site/src/content/docs/docs/latest](../../../site/src/content/docs/docs/latest) 与中文 [site/src/content/docs/zh-cn/docs/latest](../../../site/src/content/docs/zh-cn/docs/latest)。

当前文档已随 R3–R8 同步；本次仅校正状态与证据边界，不改变产品合同。
当前 [AGENTS.md](../../../AGENTS.md) 已区分 Entity/Source、Dimension/TimeDimension 的受限 Ibis 与零参数 Measure/Metric 声明；旧“所有表达式只返回 Ibis”不再列作待修事项。后续按真实差异核对，不因本计划刷新而修改受保护文件。
AGENTS 及两个 packaged skills 的编辑受仓库明确授权规则保护：

- [marivo-semantic](../../../marivo/skills/marivo-semantic/SKILL.md)：临时表达式、业务授权、声明与模型收录边界。
- [marivo-analysis](../../../marivo/skills/marivo-analysis/SKILL.md)：Metric-first、Population、执行前 Query 组合、终端 Artifact 和 pandas 交接。

在对应行为阶段准备必要修改稿，R1 对应 authoring/表达式规则，R2–R7 对应 Query、终端产物和工作流；R8 核对收齐。
已有授权覆盖时直接完成，否则在真正需要落地前取得明确授权。
授权不足时，产品实现与工程检查可单独报告已完成，受保护文件同步保持待办，不能声称全套公开工作流已完成。
R3–R8 实施时已获授权并完成两份 packaged skills 同步，无待授权同步项；AGENTS.md 无需修改。本次文档状态同步不编辑这些受保护文件。

## 8. 最终完成标准与当前状态

整项重构完成需要：

- R1–R7 的目标行为真实可用，必要语义反例由主场景或对应测试覆盖；不支持的边界符合提案。
- 预定义/临时表达式、来源/字面量输入、原量/格子统计的权限边界一致；没有重复入口、隐藏回源或旧格式兼容支路。
- 新产物与重开产物的数据和阅读接口一致；没有 Artifact DSL 续算或历史图恢复，Finding/Evidence 为自包含输出。
- Ibis 源端、pandas 本地、PyArrow 存储/读取各有唯一职责；无本地多引擎或批次/全表分派，本地内存限制和失败边界如实保留。
- §2 的依赖方向落实，§5.5 的通过条件满足；元数据打开不读数据、预览有界、优化保留必要检查。
- 当前 API、类型、Help、错误、英中文档及获授权的工作流文件同步；make check-agent 通过，实际需要的额外检查单独报告。
- 最后记录通过、失败、未运行与待授权事项，所引用证据只说明它实际验证的范围。

当前状态：**R1–R8 在约定的 DuckDB 首版范围内已完成本地实施与验收；远端数值 Query 能力仍需代码扩展，Runtime / release 验收未完成。** 不能将远端代码限制写成“实现已齐，只差跑测试”，也不能把历史开发期失败重新列为当前未完成项。

| 类别 | 当前状态与证据 | 剩余边界 |
| --- | --- | --- |
| 本地实现与公开切换 | `7508aca650` 完成 R3–R8；Population/Query/Column、Store 10 终端读写、有限优化、Help/类型/英中文档及授权技能已交付 | 首版明确排除的能力和未准入路线不在完成声明中 |
| 完整常规工程门禁 | `405bd006f8` 收尾后的无收窄 `make check-agent` 通过：4588 默认测试、lint/import、366 文件 typing 与 API 文档；原五项 helper typing 诊断已关闭 | 默认测试排除 slow/runtime/release；此结果不覆盖这些集合 |
| 独立本地验收 | 六条业务链及冷读 7/7、身份边界 6/6、审查问题与组合探针 27/27 通过；最终工程候选按产品文件 SHA 等价重绑定 | 重绑定没有冒充再次执行业务，也不构成第三轮正式审查 |
| 远端代码能力缺口 | [数值来源准备](../../../marivo/analysis/materialization/graph_source_execution.py) 明确拒绝非 DuckDB provider；datasource 可连接其它后端不代表新数值 Query 可执行 | 按 §8.1 补齐表达式/方法准入、pandas 类型交接、时间与依赖路线；来源无损传输不再是前置条件，仍需真实 Runtime 验证 |
| 保留的其它能力限制 | 来源 approx_quantile、内部样本 approx_nunique、原指标 approx_* 重算缺合格内核时拒绝；Catalog 的 Population/sample/window/routed-field preview 未扩权 | 如要支持，属于新增代码能力及验证，不属于本轮首版漏项；不能用 exact 静默替代 approximate |
| 未完成的远端 Runtime 验收 | 本轮没有当前版本的完整远端实服验收；helper typing 与替身/驱动本地探针只证明各自范围 | 原生类型/精度/时间、权限、超时、分布式来源及冷读仍需实际路线证据；现有 Trino/ClickHouse producer recovery 正例消费数值 Query，受上述代码缺口阻塞，不能只启动服务便视为可验收 |
| 未运行的完整 slow / release 验收 | 已有定向 slow 和跨进程冷读通过；未运行完整 slow 集合、当前候选安装 wheel/installed journeys、全后端矩阵或 `make release-check` | 这些验收按需或发布准备时运行；不能由常规门禁、API 构建或单个本地 release 标记用例替代 |

证据明细在 §9。`/tmp/marivo-external-typing-check-agent-final.log` 记录最后常规门禁；
`/tmp/marivo-r3-r8-acceptance.b0hrWX/round2-final-acceptance.md` 和 `final-source-rebinding.md`
记录独立验收及重绑定。它们仍是本地临时证据，非仓库持久资产。
随后在 `405bd006f8` 的只读核对中，Query 结果、对应、原指标、优化和两组修复回归共 129 项通过；
该定向复验不替代完整门禁，也未扩展远端或 release 资格。

历史 `3a4e741cfa` 的 3964 passed / 504 failed / 10 setup errors 是实施前基线，保留在 §9 追溯；
不能继续作为当前失败状态。两轮正式审查的已知问题与两个组合残留已关闭，无待授权技能同步项。
本次仅修改文档，检查受影响内容、链接、围栏及差异，不新增实现、Runtime 或 release 通过声明。

### 8.1 远端数值 Query 最小实施顺序

**状态：以下全部待实施、待验证。** 本节采用用户确认的 Ibis 原生返回 / pandas 类型交接取舍，
不重新打开已完成的 R1–R8 本地验收，也不把文档更新计作代码交付。
唯一交接合同见[执行 §1.5.1](../../specs/analysis/lazy-analysis-dsl-execution.md#151-ibis-原生返回与-pandas-类型交接)。
接受原生数值精度损失；不新增无损 transport、统一后端 dtype 的 Ibis cast 或全局精度配置。
单位、时间语义、明确运算规则与错误政策所必需的转换仍保留。pandas 负责实际返回类型与后续计算/存储类型衔接，
不能盲目回 cast、截断小数、吞掉错误或声称恢复精度；已提交 Artifact 保留其实际保存值。

#### 可复用实现与代码缺口

| 已有实现，可继续复用 | 必须补齐或调整的部分 |
| --- | --- |
| [SourceSession](../../../marivo/datasource/adapters.py) 的 backend 选择、绑定、SQL 归属、编译执行、只读设置、连接清理；现有 EngineProfile | 取消数值 Query 的全局 DuckDB 拒绝须随实际路线逐项准入；不直接放开全部 provider。核对 DuckDB 特化读取及为 carrier 保真/统一 dtype 添加的 cast，按业务必要性清理，不能视为新取舍已经实现 |
| [StageOutput](../../../marivo/analysis/materialization/graph_exchange.py) 的列序、键、nullable 及阶段交接；统一调度与 Store 10 | 调整严格声明 carrier 假设；普通字段、聚合值和内部组件均在 pandas 接纳原生类型差异，后续内核及输出 schema 使用有效类型；保留完整键、时间、单位和值约束 |
| [observation lowering](../../../marivo/analysis/compiler/observation.py) 和[原生数值 lowering](../../../marivo/semantic/metric_graph_lowering.py) | 为实际 backend / dtype / method 检查编译与执行能力；现有远端物理准入不含 window/sort，不能继承 DuckDB 或旧方法注册表的资格。已知静态不支持在 Run/业务读取前拒绝，动态输入在其消费前复核 |
| source_time 的解析/时区规则及[temporal_sql](../../../marivo/analysis/materialization/temporal_sql.py) 的后端重写 | [query_preparation](../../../marivo/analysis/materialization/query_preparation.py) 当前写死 engine="duckdb"，temporal rewrite 尚未接入新链；ordinary observe、read、drill 共用实际 engine 路线 |
| Query 依赖键交接、多来源本地组合、有限优化、终端冷读 | 基础未融合路线先通过；远端聚合合并须有自身等价证据，不能因同样是 int/Decimal 就继承精确重排资格；不新增跨 datasource 标量关系连接能力 |

#### 顺序与每步 Runtime 范围

各步只交付能够贯通 public Query → Artifact 的能力，复用现有 fixture 和测试 owner。
PostgreSQL 作为首个普通路线，便于先验证共有接口；这是实施顺序选择，不是已具备资格的结论。

| 顺序 | 最小代码交付 | 对应 Runtime 验证 |
| --- | --- | --- |
| 1. PostgreSQL 普通闭环 | 接通表达式编译准入、pandas 类型交接及实际输出 schema；先做普通分组 count/sum/min/max、直接值读取和发布；复用既有 SourceSession/调度/Store | 一个真实表经公开 Query 执行与导出；nullable 大整数、Decimal、空/全 NA、复合键/None 分类验证交接及分组政策，记录原生精度；失败不得发布成功。选一个产物做独立进程断源冷读，以已保存值为 oracle |
| 2. 其它后端普通路线与数值方法 | 同一入口扩展 MySQL、Trino、ClickHouse；分别准入 mean、weighted_mean、count_distinct 及 quantile 的实际类型/算法。需要 window/sort 的 quantile 路线等第 3 步完成相应能力再放行，不能静默替换成近似 | 每个新后端执行一组代表性普通 Query，验证原生返回→pandas→Artifact；方法特有用例覆盖配对、空输入、零分母与实际溢出政策，ClickHouse 核对 distinct/nullable。未准入方法验证具体拒绝原因，不能用通用 DuckDB gate 冒充方法负例 |
| 3. 时间与依赖来源 | 传入实际 engine、接入已有时间重写，补所需 window/sort；贯通期间/窗口、版本锚点、依赖 Query 完整键及 drill。共享交接入口不增加第二个执行器 | 按受影响后端验证实际时区/解析、DST 与请求范围；代表性窗口和期间结果。用包含复合键/None 的上游名单验证完整元组谓词；选两个不同 datasource 做一次依赖/本地组合；不把同源多表作为跨源证明 |
| 4. 特化来源及优化 | 在普通路线基础上准入 Trino 不同 connector 与 ClickHouse Distributed；对拟开放的合并优化补后端证据，证据不足保留未融合计划 | Trino Iceberg/非 Iceberg、ClickHouse MergeTree/Distributed 的相关 producer 用例；优化前后核对 keys、NA、次序、必要检查/失败，数值遵循所选原生方法且不要求跨后端逐位一致。只对改动影响的 native timeout、只读和清理边界追加实服检查 |

精度用例要同时记录原生 Ibis/pandas 返回和交接后的 dtype、值及 NA；不要求复制原始数据库值，
也不把 dtype 归一化当作精度恢复。完整键唯一性、样本配对、分组、过滤与错误政策仍独立验证；
精度容忍不为改变这些语义的优化提供许可。Artifact 冷读只比较已提交的结果，不另行重算来源。
不将全部本地业务链乘以全部 backend/存储组合；每个后端仅覆盖新增能力及有差异的物理路径。

测试 owner 和执行命令由 [Runtime 覆盖说明](../../testing/runtime-coverage.md#remote-numerical-query-extension)维护。
现有 source profile 只能证明 datasource 访问；period native carrier 的远端拒绝用例只能证明边界，
Trino encoded-time 的直接表达式用例只能证明时间内核；这些都不替代公开 Query 正例。
每步更新实现、定向本地测试、类型及披露文档，再执行相关 Runtime；最终代码候选运行无收窄的
`make check-agent`。服务不可用、跳过或只编译须单列，不能记作实服通过。
完整 slow、安装 wheel/installed journeys 和 `make release-check` 仍是独立范围，按发布需要执行。

## 9. R3–R8 团队实施记录（2026-10-17）

本节保留本次实际实施的时间顺序；其中开发期失败、待修、未提交等描述按记录时点解释，当前汇总以 §8 为准。
起点为 `24bb24d8083e86815b256e425dce087baed0d3e1`，启动时工作树干净。

| 职责 | 执行者 | 边界 |
| --- | --- | --- |
| 协调与提交 | 主 agent | 维护本台账、依赖顺序、候选冻结、审查轮次、证据与 scoped commit |
| 实现 | implementation，gpt-6.1-sol / xhigh | 主源码、配套新测试、类型、原生 Help/exports 与跨模块集成；不自行提交 |
| 现有测试迁移 | test_migration，gpt-6.1-sol / xhigh | 已列基线失败的现有 pytest 模块；保留业务/数值 oracle，排除主实现者的新能力测试与共享 fixture；不自行提交 |
| 统计测试迁移 | statistics_migration，gpt-6.1-sol / xhigh | `tests/analysis/statistics/`（排除 runs 两文件）；保留数值 oracle，将退休统计 DSL 场景迁为真实 Query → Artifact → pandas |
| 时间测试迁移 | time_migration，gpt-6.1-sol / xhigh | temporal 三模块、numeric time/period 三模块、local_check_inheritance 与 period guidance；保留时间/时区/日历业务 oracle 和实际冷读 |
| 比较与参照测试迁移 | comparison_migration，gpt-6.1-sol / xhigh | numeric/comparison_runtime 与 references 两模块；保留各类数值、完整键、NA/presence 与冷读 oracle |
| 对应内核与产品文档 | correspondence，gpt-6.1-sol / xhigh | 新增 correspondence 定义/DSL/绑定、query_comparison 内核及新测试；内核交付后同步 owning specs/docs/api/英中 latest，主实现者负责 Query/调度/存储集成 |
| 原指标充分信息 | original_metrics，gpt-6.1-sol / xhigh | 新增 compiler/original、methods/query_original、materialization/query_components、query_drill 与独立新测试；负责完整版本身份的 drill 属性模板，主实现者集成实际来源/调度/Query |
| 独立审查 | review，gpt-6-astra / high | 只读评审冻结候选；正式审查至多两轮 |
| 独立验收 | acceptance，gpt-6-luna / max | 隔离基线与真实本地路线验收；不修改产品以使验收通过 |

执行顺序为 R3/R4 → R5 → R6，并在 R5 对应能力具备后收齐 R7，最后完成 R8。
已交付 S0–S6/R1/R2 仅按增量影响复用和复验；不重新实现旧架构。
正式审查在完整候选冻结后启动，开发期合同核对和基线调查不计正式轮次。
每轮记录候选、发现和修复；第二轮后如仍有未关闭 P2 及以上问题，不提交、不增加第三轮审查，交用户决策。

| 阶段 | 本轮状态 | 证据 / 待办 |
| --- | --- | --- |
| 基线与验收准备 | 精确基线已验证 | 归档接入 `.venv` 且导入来自归档；3964 passed、504 failed、10 setup errors，82.83 秒。摘要/节点：`/tmp/marivo-r3-r8-acceptance.b0hrWX/baseline-summary.md`、`baseline-failure-nodeids.txt`，仅为本地临时证据 |
| R3 | 已完成本轮本地实施与验收 | Session.read、resolved Entity、复合 coord 准备期拒绝、完整键/字面量修复及多日历输出均通过 |
| R4 | 已完成本轮本地实施与验收 | Column/Top/reduce/values 与有限优化通过；Decimal、Duration 组合、NA/zero 和键排序问题关闭 |
| R5 | 已完成本轮本地实施与验收 | 比较/presence/参照通过；直接及保留端点日历 coarsen 广播、缺侧/歧义/覆盖边界通过 |
| R6 | 已完成本轮本地实施与验收 | 原量/rollup/drill/share、同来源读取 2→1 与 L8 stage 5→3 在有限准入路线通过 |
| R7 | 已完成本轮本地实施与验收 | 六链时间、DST/实际边界、单及多日历独立冷读通过；不宣称远端 Runtime |
| R8 | 已完成本轮本地实施与验收 | 精确 48 出口、Help/类型/英中文档及授权技能一致；最终 make check-agent 4588 默认通过 |
| 正式审查 | 2 / 2 完成 | 第一轮九项及第二轮两个组合残留已修复；核心残留由独立验收关闭，无第三轮正式审查 |
| 独立验收 | 全部通过 | 业务链与冷读 7/7、身份 6/6、发现与残留 probes 27/27；最终产品源码 SHA 等价重绑定 |
| 提交门禁 | 已满足 | P2 及以上已关闭，正常 hooks 后仅本地提交；提交标识以 Git 记录和最终回复为准，不推送 |

受保护 AGENTS.md 不需修改。用户已明确授权两个 packaged skills 的终端 Artifact/Query 工作流同步，
correspondence 按 `/tmp/marivo-r3-r8-workflow/packaged-skills.patch` 及最终合同核对后应用；不改 AGENTS.md。
基线失败的存在不降低 R8 的最终全绿标准；相关失败迁移、真实回归与未运行范围分别记录。
首次归档因工具链接缺失产生的 3961/507/10 计数已撤回；修复后的 514 个失败/错误节点全部命中旧清单，
零个清单外失败，六个旧节点当前不失败或未收集。此节点对照不证明失败原因完全相同，也不是全绿。
旧清单的四项远端 preview 问题当前已消退：三项 Metric 参数已退出，剩余 Entity/Measure deadline 用例通过；
本轮不因此扩张 DuckDB-only Metric preview 资格。

用户已决策：R3.2 改为唯一入口 `session.read(population, fields)`，显式确定 Session 归属；
不保留 `Population.read`，不隐式选择或恢复 Session。该决定解决无主字面量域的执行归属缺口，
目标合同、当前文档、Help、英中示例和测试随实现同步。

开发期测试迁移证据（非独立审查/验收）：`test_computation_runtime` 9 passed；
`test_metric_observation_resolution` 与 `test_expression_entity` 含 slow 冷读共 5 passed；
两个 semantic cumulative 模块按原生 `Metric.window` 迁移后共 36 passed。
`test_file_admission` 7 passed / 3 failed（HTTP pre-open），runs 迁 pandas 后 2 passed / 2 failed
（QueryKeys 到日历再观察）；失败保持暴露，由实现 owner 修复，不新增 skip/xfail。
后续修复回归：`test_query_results.py`（八项新能力）、`test_file_admission.py` 与
`test_analysis_runs.py` 合计 22 passed / 1 deselected，3.01 秒，已覆盖上述动态来源与 HTTP pre-open 问题。
该 deselected 的认证日历冷读用例随后单独运行：
`test_analysis_runs.py::test_offline_runs_keep_calendar_cold_recovery -m slow -n0` 1 passed；
真实 Parquet、删除模型并断源、两个独立读取进程保留认证 1/2/3 日桶边界，pandas 区间结果为 count=2、duration=5 days。
后续十一模块合并 focused run 92 passed（不含默认排除的 slow）；身份观察单项另 1 passed。
关系测试当前四项通过、两项外部时间分类范围问题待修；members consumer 当前四项通过、一项来源空身份键待核实。
相关 slow 后续合并 2 passed：认证日历冷读与完整机会域 cohort 冷读；不据此声明全部 slow/Runtime 通过。
R6 内核后续 12 passed：`test_per_keeps_full_internal_identity` 明确覆盖 400/200；
`test_original_constant_and_ratio_recompute_the_formula` 明确细格 110+310=420、原式上卷 410；
窗口空间上卷保留两个端点为 310/340；成员身份重新分组为 2/3、share 为 2/3 与 1/3，
组合 sum/member_count 为 340/3。完整细坐标支持仅保留于执行内 ComponentFrame.domain；公开集成与 drill 继续。
R6 后续 16 passed，四文件 lint/format/import contracts 通过；本 owner typing 无报错，共享 Query 拼接错误待主实现修复。
drill 核已验证父选纤维、重新分类和原量重算；公开来源模板必须消费完整 version_row_key，
因此由 original_metrics 继续实现 schema-only query_drill 接缝，主实现负责实际读取与调度。
开发期进一步回归：observation/公开 quantile 共 22 passed；public composition 与分布/精度
共 22 passed / 1 slow deselected，相关冷读另补。大整数字面量能精确区分 2**63 与 2**63+1；
NaN 和超出已准入精度的阈值在 Run 前拒绝。来源 approx_quantile 继续按 §2 明确拒绝未准入路线，
测试另显式选择同输入 exact quantile 验证数值，不增加隐式算法回退。
开发快照 `/tmp/marivo-r3-r8-dev-44o2fqph/snapshot` 已在三个活动实现者暂停写入时复制，
包含 1239 个 tracked/untracked 文件，旁置逐文件 SHA-256 manifest 与基线 SHA；导入路径确认来自快照。
该快照运行完整默认测试以定位剩余迁移，不是候选冻结、正式审查或独立验收；复制完成后实现继续。
快照时新 Query/对应/原量测试合计 45 passed；52 个相关源码文件 focused typing 通过。
DuckDB 原生 INTERVAL 保留现行合同：先拒绝 year/month/day 分量，再以微秒准入；正面单项已回归。
该开发快照完整默认测试为 4162 passed、352 failed、10 setup errors（89.79 秒）。
362 个失败节点中 354 个命中精确基线，8 个清单外；160 个原节点不再失败或已改名/未收集，不能等同于修复计数。
清单外包含两个业务行为节点（来源 Population×日历、独立 weekday/ordinal oracle）及六个披露节点，均已分派。
日志、JUnit、逐节点原因及清单外列表分别保存在快照父目录的 `default.log`、`default.xml`、
`failure-details.txt`、`new-failure-nodeids.txt`。这些结果仍为开发诊断，正式审查保持 0/2。
后续统计迁移暴露浮点 compare 的零分母/缺失端点空值误拒绝：共用 nullable 算术的
`Series.map` 重新推断 float dtype，使被遮蔽的 NA 变为参与有限性检查的 NaN。
实现者以保留 object dtype 的最小修复恢复原合同，新增三项回归；相关比较合计 23 passed，
修改模块 typing、lint 与 import contracts 通过。证据 `/tmp/marivo-statistics-compare-{final,types,lint}.log`。
R4.4 的 RequiredSlots、有限局部融合和 registry-aware invocation 编译复用尚未交付，
已单列为可独立开发块；主实现者保留 Query/graph_plan 接入，不将它混入测试迁移或默认完成。
公开 drill 后续真实回归：mean → where/top → 新 channel 分类 → rollup 得 20；
同主键两个版本、选择 East 后 drill 得 Store 30 / Web 10，上卷 40，模板使用完整 version_row_key。
新 Query/原量/对应/窗口默认测试合计 54 passed；Duration ns per(time).mean 的生产与两个独立冷读进程另 1 passed。
R4.4 接口交接已保存 `/tmp/marivo-r3-r8-workflow/r4-optimization-handoff.md`；三项优化仍待实现，
上述正面 drill 证据不覆盖尚未完成的需求传播、融合或编译复用。
统计分片默认测试后续为 251 passed、13 slow deselected；slow 第二轮 12 passed / 1 failed，
剩余测试 fixture 目录问题及后续增强用例仍在复验，尚未记为分片完整通过。
公开入口清理交接 `/tmp/marivo-r3-r8-workflow/r8-exports-handoff.json` 列出 49 个 canonical exports；
测试收集阶段旧运行时注解已机械迁移，未修改 conftest/shared_fixtures，主实现继续切换出口和 Help。
身份域边界核对按 DSL §5.1：member() 与明确绑定 MemberAxis 都请求完整 P，附加字段须在 P 上单值；
customer_id 字段 × channel 的五行贡献分类像使用真实普通字段及 members=P，不将字段轴伪装为 Entity 身份轴。
统计分片最终同次运行 264 passed（251 默认 + 13 slow），canonical 49 出口切换后通过；
包括三 distinct PID、删除来源/模型后的两次冷读、ns/us、纽约认证日历、四种非时间域、
1001 成员/3003 格、事务故障及损坏边界。31 文件 typing、20 个改动文件 lint/import contracts 通过。
映射为 `/tmp/marivo-r3-r8-acceptance.b0hrWX/statistics-migration-map.md`，最终日志为
`/tmp/marivo-statistics-final.log`；仍不是完整候选的独立验收。
统计分片释放并发槽后，original_metrics（gpt-6.1-sol / xhigh）接手 R4.4/R6.5 优化，
独占 optimization.py、必要 local_laws 适配与新优化测试；主实现者负责 Query/计划/执行接线。
旧测试后续迁移：premise 11 默认 + 1 冷读 slow；coordinates 21 默认 + 1 冷读 slow，
重复轴静态拒绝另有真实缺口待主实现修复。保留 147/140/3、49、200/101、50.5 独立数值 oracle。
两 Session 真实 DuckDB 阻塞重叠的 concurrency 默认四项通过；numeric_recovery 的原生生产者、
来源/模型离线、第三进程冷读 slow 一项通过。conftest 只迁 mean 声明并补具体注解，未改变 fixture 数据/scope；
连 coordinates/premise 共三模块 focused typing 通过。相关 slow 余项与比较消费者仍在继续。
优化首轮已实际接入：RequiredSlots 反向合并并裁传输，宽 literal 4→2 列；L1 连续安全整数筛选
合并为 AND 并去除中间执行 stage；invocation memo 贯穿准备/规划/降低并保留贡献 occurrence。
首轮行为对照 10 passed / 1 测试构造错误，修正该测试跨 Query Column 引用后继续复验。
values 的字段/身份键现以真实绑定 schema 准入；错误 kind、bool→int、NaN、naive 时间拒绝，
合法 None、精确 Decimal 大键和数值提升正面通过。R3 动态完整元组模板实际使用 null-safe AND/OR：
DuckDB A/Store、B/Web、C/None 结果 57，排除交叉组合 10、保留 Null 匹配 7，实际 SQL 验证通过。
优化后续 18 项定向全部通过；与原量、local-law lowering、完整公开 DSL 联合 focused 为
112 passed（10.03 秒）。实际覆盖宽输入 4→2、共享 consumer 4→3、nullable int 筛选少执行一个 stage、
share/top/check/多 consumer 屏障、相同严格失败、未选分支 mask、独立 source occurrence，
schema/registry/check 绑定/policy/kernel/slots 改变后的重编译，以及两 execute 来源变化 60→120。
属性读取的 SourceRead 字段本身属于完整 Population 键，保留全部键和 lookup 检查，不声称该路线可裁。
无证明的 source/reduce/原量融合继续明确拒绝。最终 typing/lint 收尾中，尚非正式审查结论。
旧测试后续 coordinates/public DSL/concurrency 合计 39 默认通过，并补相关独立冷读；
numeric_boundaries 仍有四个 Decimal 38 位精度正面失败，保持原 oracle，由主实现者修复。
优化分片最终源码与新测试四文件 typing、lint/import contracts 通过，记录为
`/tmp/marivo-r3-r8-workflow/r4-optimization-validation.md`；统计与优化分片均已结束其开发轮次。
time_migration 接手保留的八个时间模块（旧快照 73 失败），不修改产品源码或全局 fixture；
主实现继续 Decimal 38 位正面缺口、类型与披露收尾，原测试 owner 继续非时间旧测试。
后续修复闭环：Decimal 边界 13 passed；观察粒度 23 passed，零轴 Population 单位行和共享依赖
消费边计数已修；grain/Help/优化/typing surface 联合 53 passed，未放宽 Help 预算。
members(where=PopulationPredicate) 的实际完整复合/Null 元组路线通过；来源资格模板预验证前移到 Run 准入前，
实际实例化仍按自身表达式校验，时间 owner 正在复验原 before-Run 正面/拒绝边界。
时间远端矩阵已核对 HEAD 的 graph_source_execution.py：新数值来源仅准入 DuckDB 属已有基线，
不是本轮退化。保留 DuckDB 实际期间比较与 carrier 正面，未准入远端只验证 before-business-read 拒绝，
不启动服务或计作远端 Runtime 通过；退出公开的 holiday 仅保留仍被消费的内部日历 oracle。
时间八模块交付：最近完整分片 89 passed / 4 failed；四项随后分别定向通过（两项 encoded SQL
结构断言迁移，两项 share/reduce 接线中间态），另两条独立进程冷读 slow 通过。八文件 focused typing、
lint/import contracts 通过；尚须在源码稳定后重跑完整 93 项，不能将分轮结果写为最新整组全绿。
时间 owner 释放槽后，correspondence 恢复 docs/api、当前 specs、英中 latest 收尾。
源码新增 CalendarAxis.coarsen 纯候选构造，share/reduce/rollup 日→月实际 [60,100]，跨月周拒绝；
复合身份轴 coord 的单量值列是否准入已交用户决策，未先行收缩目标合同。
主源码稳定交接：186 项关键回归，随后 Duration/Help/display 定向验证；全源码及公共类型样例
365 文件 typing、215 文件 lint/import contracts 通过。交接记录为
`/tmp/marivo-r3-r8-workflow/implementation-source-handoff.md`。
复核 R6.5 后确认原量 L8 及共同来源块仍需正向实施，不能以 L1 正面和其余全拒绝代替。
original_metrics 继续按 `r6-fusion-handoff.md` 实现，并接手必要 compiler/runtime 接线；主实现停止编辑。
用户另待决 identities 的裸 EntityRef 与已解析 Entity 输入差异：目标示例明确使用裸 Ref，尚未擅改目标。
第二次开发快照为 `/tmp/marivo-r3-r8-dev2-ctercdoj/snapshot`：两个活动编辑者暂停后抓取 1241 文件及
逐文件 SHA-256 manifest，导入路径确认来自归档。运行完整默认诊断，不计正式审查或独立验收。
第二次快照默认结果为 4443 passed、85 failed、0 setup errors、1 skipped（134.55 秒）；
85 失败中 76 命中起点失败清单、9 清单外。九项均暴露退休公开入口/Help 的旧测试消费者，已交迁移 owner；
唯一 skip 为已退出 legacy 类型集合为空导致 pytest 自动跳过，要求清理而不计为覆盖通过。
时间八模块在此快照确认为完整 93 passed，关闭其整组待复验项；其他完整门禁仍未通过。
文档侧 make docs-api-agent、381 文件 verify:content、astro check/build、latest 链接及历史路由检查已通过，
16 个英文示例完成真实临时 DuckDB 执行；最终映射/差异核对继续，用户两项输入边界决策仍 pending。
文档分片交付 43 文件、13 对英中 latest 页面，验证与风险清单为
`/tmp/marivo-r3-r8-workflow/docs-handoff.json`。API 文档、站点和路由门禁通过；两项用户决策仍待最终同步。
文档 owner 释放槽后，comparison_migration 接手两个未迁比较/参照模块（第二快照 37 失败），
原测试 owner 继续其余模块，original_metrics 继续原量与共同来源正向融合。
原量融合首轮实际 int64/Decimal38 正面：共同 group aggregate 将两次来源读取减为一次，
与 L8 有序原量组合将五个调度 stage 减为三个，低 Decimal context 下保持精确结果；反例/最终静态检查继续。
比较迁移新暴露两个源码缺口：Duration compare 构造过早拒绝，以及 Decimal share 声明 float 输出但
未完成实际载体转换而交 Arrow 失败。原独立正面 oracle 均保留，交当前源码 owner 修复。
另外认证日历时区冲突的错误 family 和未准入跨源标量关系的 before-Run 拒绝仍按具体节点收尾。
后续数值/披露迁移合跑 92 默认通过，空参数自动 skip 已替换为真实 Artifact 类型和新建/重开回灌拒绝。
原上下文预算 slow 通过，保持七场景及总字符 85% 阈值，未提升预算或修改基线；multiroot 默认八项通过。
比较/参照两个模块默认 51 passed，Duration compare 及 Decimal Column/share 载体修复已由其真实矩阵覆盖；
Decimal 输入保留精确值，声明 float64 的除法以 Fraction 完成后一次转换，并核对 Arrow dtype。
比较/参照分片随后完整六个 slow 通过，两文件 typing、Ruff/format 与 diff 检查通过；
全局 import contracts 曾被中立时间层反向依赖阻断，已交源码 owner 修复，不能据此记录完整 lint-agent 通过。
原量与共同来源融合另列其独立正确性和物理执行证据。
独立验收者现准备六条业务链的可运行脚本与独立 oracle；候选冻结前只做脚本静态检查，
不把开发中源码的运行结果作为最终验收。正式审查仍为 0/2，尚未提交。
R6.5 源码分片最终交付：共同来源 exact sum/count 读取 2→1，L8 有序原量调度 5→3，
int64/Decimal 正面与中间溢出、完整键/NA/次序、重复贡献、消费/检查屏障和物理 binding 身份隔离均有对照。
联合 120 passed；21 源码文件 typing、lint/import contracts 通过，五项时间合同与跨源边界精确验证通过。
来源身份由准备期 typed SourceInputContract 提供，不从 Ibis AST 等价推断；binding ID 仅限本次执行。
完整证据为 `/tmp/marivo-r3-r8-workflow/r6-fusion-delivery.md`。源码 owner 已停止编辑，文档 owner
同步有限资格；完整门禁、正式审查及独立验收仍未完成，不由优化分片结论替代。
Attribution 迁移默认十项、coarsen 单项 slow 和一个既有 release 标记的本地 DuckDB 冷读节点通过；
最后一项 Duration 冷读暴露 Session.read 将 Parquet duration[ns] 读为 int64 的产品缺口，
保留原输入和正面 oracle，交源码 owner 修复。该单节点不等同于 release gate。
测试迁移 owner 还列出四个 release-only 旧 API 消费文件；正核对受本次切换影响的最小迁移范围，
不能仅因它们不在默认测试集合就把迁移记作完成。
Duration 属性读取缺口随后闭环：保留既有 PhysicalFacts 的 duration_unit，经 JoinReference
解析选定字段后恢复精确 Duration schema；原 duration-True slow 从失败转为通过，含独立冷读。
另四项控制测试、两个源码文件 typing/lint/import contracts 通过，未更换输入或修改预期。
六链独立验收 runner/fixture 已完成静态检查，尚未执行候选业务验收。
R8 剩余真实测试消费者按文件分派：test_migration 负责 reference recovery 与关系冷读 caller；
comparison_migration 负责 relation_journeys 四场景；implementation 负责 complete dispatcher 与
graph/hierarchy/versioned/association 四 helper。仅迁移本次退出 API 的实际消费者和本地验证，
不运行安装 wheel、完整 release gate 或远端服务。
第三次默认诊断快照为 `/var/folders/cx/bmfg_x3j0qsfvgsb2v8s2x5m0000gn/T/marivo-r3-r8-dev3-uolp29c_/snapshot`，
三个编辑者暂停后复制 1242 文件，逐文件 SHA-256 manifest 与起点 SHA 同存父目录，已确认导入来自快照。
复制完成后各 owner 恢复测试 helper 迁移；此次是开发诊断，仍不是候选冻结或独立验收。
第三次默认诊断为 4540 passed、2 failed、0 errors、0 skipped（126.40 秒）。两项清单外失败均来自
新时间错误转换：_AnalysisTimeScope 未获 Deferred immutable literal 准入，以及共享 TimeScope.by 的
ValueError 合同被转换。已提交 R2.2 明确其共享时间值异常边界，当前目标未改变此合同；准备撤回
不必要的桥接与子类，保留独立的冻结 ISO 时间持久化修复，并对齐新迁移用例的既有异常预期。
TimeScope 修正已完成：恢复原 _SessionTimeScope 与共享 by/coarsen 的 ValueError，不扩大 Deferred
字面量准入；两项默认失败、认证时区及冻结/民用时间往返等五项定向通过，三个源码目标 typing/lint/import
通过。后续交付 `/tmp/marivo-r3-r8-workflow/time-scope-contract-fix-delivery.md` 废止先前 bridge 说明。
关系四场景三进程最终四项 slow 通过，另完整性一项通过；Duration 修复后 attribution、reference 与
损坏拒绝联合九项 slow 通过。默认 private descriptor 内核仍有实际消费者，迁为内部 owner 明确导入后
128 项行为通过；修改文件的 typing 继续收齐。时间剩余 recovery caller/可能孤儿 worker 由 time_migration
单独接手；并不增加性能、安装或远端验收门禁。
为尽早暴露业务缺口，先冻结独立的产品源码/owning specs 子候选（389 文件，非完整工作树审查候选）：
`/var/folders/cx/bmfg_x3j0qsfvgsb2v8s2x5m0000gn/T/marivo-r3-r8-business-candidate-k90g6gab/source`，
父目录 freeze.json 记录逐文件哈希，候选 ID 为 `b715fb7d140bd5f48695b312e42eba71de361d8f0db1d058188d6d6e76f9dde5`。
acceptance（gpt-6-luna/max）开始六链独立业务执行；测试 helper 迁移、完整门禁、两项 API 决策与技能授权
仍在候选完成边界之外。正式审查维持 0/2，此执行不能单独证明 R8 全部完成。
六链独立执行首批：电商与固定 SaaS 通过；制造标准化广播发生 schema 错误，物流 P95 为 85.14
而固定 oracle 为 85.15，后者仍核对精确插值/物理类型。实验轴元数据与时间 fixture 构造问题由验收者
只在自身 runner 修正后重跑，不改产品或固定预期；其余链尚未记作通过。
首轮六链最终为电商/固定 SaaS/时间三条通过，制造/物流/实验三条失败。时间 harness 的 since 预期
已纠正为合同及固定 JSON 原有的 1230/1310；并非修改 oracle 迁就产品。
制造为需求裁剪后 broadcast 仍要求原宽 endpoint schema；物流 exact Decimal P95 被 DuckDB 原生实现
算成 85.14/66.04，而独立有理数为 85.15/66.05，错误值同样冷读；实验 Query.reduce(mean) 未按声明
decimal(38,2) 完成最终舍入，导致 100/3 在 stage_output 发布时报 rescaling 错误。三项交源码 owner 修复。
独立报告 `/tmp/marivo-r3-r8-acceptance.b0hrWX/business-candidate-b715fb7d-summary.md` 与同名 status JSON
绑定候选，389 文件哈希均匹配；正式审查仍为 0/2。
全部测试/helper 分片已停写。全工作树开发门禁快照为
`/var/folders/cx/bmfg_x3j0qsfvgsb2v8s2x5m0000gn/T/marivo-r3-r8-gate-dev-yj_pbf5w/snapshot`（1240 文件），
首次 unrestricted make check-agent 在三个测试格式处停止；另全局 Ruff 发现一个 import 排序。
测试纯格式已修且 AST 不变；收齐这些格式后继续开发门禁，产品语义修复完成后仍需最终门禁。
门禁后续全量 typing 暴露唯一旧正面 ms.bind(MetricRef)，已按现行 Field/Deferred 合同迁为合法
capture→finalize→resolve→Deferred 断言和精确负向 Metric Ref 检查。只将四处纯格式及这一 typing
文件同步到开发快照，未混入正在修复的产品语义。该快照 unrestricted make check-agent 随后完整通过：
788 文件格式、Ruff/import contracts、366 文件 typing、4542 默认测试（119 秒）及 API 文档构建均通过。
完整日志 `check-agent-typing-fixed.log` 位于上述开发门禁目录。这是首次完整工程绿，仍不覆盖后续
制造/Decimal P95/均值/literal 修复，最终候选门禁与独立业务复验仍须完成。
上述业务修复最终联合 129 项通过，七个目标 typing/lint/import 通过；冻结完整工程/业务候选二
`/var/folders/cx/bmfg_x3j0qsfvgsb2v8s2x5m0000gn/T/marivo-r3-r8-candidate2-78t416_q/snapshot`，
候选 ID `e676eba42fa30a84180f84d63ecaaafa8ea90236c78ee71549fd9d3f816586ad`，1240 文件。
luna/max 对候选二复验全部六链，root 最终工程门禁待最后合同同步后执行。
用户现明确决定：Query.coord 在准备期拒绝复合身份轴作为单量，完整键/元组谓词/导出保留；
mv.identities 只接已解析 Entity，不接裸 EntityRef；授权两份 packaged skills 工作流同步。
original_metrics 收齐代码/Help/类型与负向行为，correspondence 收齐合同/示例/英中页面和授权技能。
这批决策尚不在候选二，完成后再冻结完整正式审查候选；审查仍为 0/2，尚未提交。
候选二六条独立业务链及 cold_read 全部 PASS：制造 50/50 的 20%/12.5%、20/80 的 26%/17% 与
缺 Hard 层拒绝通过；物流 cell P95 85.15、network/cold 66.05、完整网格/NA/窗口/Top 通过；
实验精确业务 mean 仍为 100/3，已声明 decimal(38,2) 的引擎 mean 33.33/33.33、差异 0.00 通过；
时间包含 since-Sep-30-noon 的 1230/1310。运行前后 1240 文件/符号链接哈希均匹配，导入来自冻结源码。
独立证据 `/tmp/marivo-r3-r8-acceptance.b0hrWX/business-candidate2-e676eba4-summary.md` 及同名 status JSON，
真实日志 `logs/business-candidate2-e676eba4-attempt1.log`。该验收不代替最后两项 API 决策增量验证或正式审查。
最终 API 边界已收齐：coord Column 仍可惰性构造，execute 准备的实际完整 key_groups 宽度检查在业务
compile/execute/Run admit 前拒绝复合单量；完整 tuple 导出/谓词及单分量 coord 保持正面。identities 仅接
resolved Entity，裸 Ref 给结构化修复并被类型负例拒绝。联合 81 通过、八目标 typing/七文件 lint/import 通过。
文档 owner 已同步 43 文档及两份授权技能，完成 API 构建、381 内容检查、Astro 0 错误/警告、三技能测试、
12 Help 路由、16 真实 DuckDB 例子及额外目标身份例子；英中代码一致，受影响链接/围栏无新错误。
详见 `api-decisions-delivery.md`、`docs-decisions-delivery.md` 与 `docs-handoff.json`（均在本轮 workflow 临时目录）。
所有实现者已停写；准备冻结最终工程/独立边界验收候选，通过后启动正式审查第一轮。

正式第一轮候选 `ea2a0fbb6d00cd555923364056a91139ef5d0c8f4b820fe1a41b290994c91297`
已冻结 1240 个文件/符号链接。无收窄变量的 `make check-agent` 全部通过：788 文件格式、
Ruff/import contracts、366 文件 typing、4552 默认测试及 API 文档。luna/max 对同一候选的六条业务链、
冷读及六项身份边界验收全部通过，执行前后哈希无漂移；这些通过不替代独立审查结论。
astra/high 三组并行完成正式第一轮（1/2）：核心 51 文件、API/文档 67 文件、测试迁移 161 文件。
核心报告 1 P1/3 P2，API 报告 1 P1/4 P2，测试迁移无额外发现；三组候选哈希均无漂移。
九项待修复：Decimal38 一元负号精度、NA/0 的 error 政策、合法日→月广播、Duration sum/mean、
日历乘积终端轴元数据、select 无时区时间字面量、Top 等分键 None 优先、模块 time_scope 退役、
字面量 Population 错误结构。报告为本轮临时目录的 `review-round1-{core,api-docs,tests}.md`。
其中日历保存和字面量错误含继承缺口，但属于本轮 R3/R8 的明确合同闭环，仍须修复。
此前 canonical 49 出口清单只是开发清单，并非用户认可保留模块级 time_scope；目标 §4.3 明确
只保留 Session.time_scope/instant。协调者纠正此前判断，要求实现、Help、测试及文档同步退出别名。
用户确认的 Session.read、resolved Entity、复合 coord 准备期拒绝及两份技能授权均已生效，无待决问题。
original_metrics 负责四项核心修复，implementation 负责四项 API/终端修复，correspondence 负责
time_scope 出口及消费者同步。仅修改 live 工作树，保留第一轮冻结候选；修复后再进入最后一轮审查。
当前未提交；九项 P2 及以上问题关闭前不 commit，不 push。
第一轮九项功能修复已交付并停写，交接为 `round1-core-fixes.md`、`round1-api-fixes.md`、
`round1-time-surface-fixes.md`。核心原审查五项 probe 全部通过；13 项新回归及一个旧顺序节点通过，
九目标 typing/lint/import 通过。API 的终端/新回归 63 项、邻接 54 项通过，八目标 typing 通过；
日历元数据按实际轴与完整键组逐轴保存，独立冷读保留不同时间范围/时区与附件 receipt。
入口清理改为精确 48 exports，无模块 instant/time_scope；75 项 surface、163 项消费者和文档验证通过。
已修改旧终端测试的具体类型注解仍单独收齐，不以原有类型错误豁免，也不改变数值或失败 oracle。
上述为开发修复证据，九项正式关闭仍待独立验收和最后一轮审查。
类型收尾完成：旧终端测试 61 项自身诊断清零，标准 typing、53 项测试与 lint/import 通过，
142 个断言及全部参数化保持不变。时间入口消费者 17 个存续文件自身/产品 typing 诊断为零，
70 项默认、两个直接负例和一项本地 credential slow 冷恢复通过；定向 typing 仍报告五项既有远端
测试环境依赖诊断（ClickHouse 三项、MySQL 两项），与已验证基线一致，未加全局豁免。
两个无当前 caller、仅测退休 graph continuation/Artifact 续算的孤儿 benchmark 已删除：
`tests/benchmarks/graph_snapshot.py`、`tests/support/benchmark_coordinate_state.py`；不以其他指标冒名替代。
所有实现者再次停写，准备冻结第二轮候选；本轮为最后一轮正式审查，不开启第三轮。
第二轮候选 `26c84e2c0d651d99f618a39cff7164097e7ddc9dbe94288249e8ef11398f9980` 的
完整 `make check-agent` 通过：4575 默认测试、366 文件 typing、格式/lint/import/API 文档通过。
API 第二轮原五项全部关闭，无新增 P2 及以上，48 出口和 169 Help 链接可达，前后 1240 哈希一致。
核心第二轮发现原问题的两个组合残留：Duration 二元表达式后规约丢失类型；比较结果保留端点日历
投影后仍缺合法 coarsen 广播。它们没有被完整门禁发现，独立探针已在原候选复现，禁止计作关闭。
测试第二轮初始增量无新增发现，24 项定向通过；等待最终小补丁的测试与哈希增量闭环，仍属第二轮。
original_metrics 随后完成这两个确定缺口的修复：六产品文件及一个原有本轮回归文件，未引入新 registry
或持久 IR；Duration 按二元节点保留真实类型与精确完成，日历投影携明确端点 presence/authority。
顺带补合法零 timedelta 字面量的现行准入，混合输入保留既有单位推断，全零为 us，不放宽 None 准入。
原 probe 八项、新 core 26 项及五文件组合 127 项通过，七目标 typing/lint/import 通过，已停写。
交付为 `round2-core-closure.md`；最终候选仍待 luna 独立复验、第二轮测试分片增量闭环及完整门禁。
核心/API 正式审查已用满两轮，不追加第三轮；已知修复由独立验收记录关闭，不声称最终修复未经覆盖的
源码又通过了一轮完整审查。
最终修复候选 `2c6f0fa0442eb91e8caaa55869f2995c64b5da12ec55ac6c62c6c09914298820` 的
luna 独立验收完成：六条业务链及冷读 7/7、身份边界 6/6、原发现和残留组合探针 27/27 通过；
实际导入与前后 1240 哈希均匹配。两项第二轮核心残留由独立验收关闭，非第三轮正式审查。
第二轮测试分片完成最后 13 项新增回归核对，无新增发现，之前已审测试未发生未记录漂移。
该候选完整默认测试为 4587 通过、1 项旧负测阶段预期失败：Duration > integer 已从运行期错误
前移为合同要求的准备期类型拒绝。仅修正 `test_analysis_predicates.py` 的负测，精确验证 CoreRuleError
及零业务 compile/execute、Run 不变；原 Duration 正面、Arrow ns 与历史读取保留。全模块 11 项、
标准 typing/lint/import 通过，产品源码未改变。最终门禁将重新绑定这项测试及台账的候选，
独立业务验收按全部产品文件 SHA 相同重绑定，不重复相同业务执行。详见 `final-duration-refusal-alignment.md`。

最终工程候选为 `98c56b654261924534e7a0c022c60ab4b9a9e36634ce55dd973f3a5eb5e04f65`。
无收窄变量的 `make check-agent` 完整通过：788 文件格式、Ruff/import contracts、366 文件 typing、
4588 默认测试（83 项已列警告，115.35 秒）及 API 文档构建。luna 独立核对两候选全部 1240 条清单，
确认最终差异仅为负测与本台账，全部 363 个 marivo 产品文件逐字节一致，将已执行的 7/7、6/6、27/27
结果明确重绑定到此工程候选；不冒充再次执行业务。独立记录为 `final-source-rebinding.md`。
本轮实现 sol-6.1/xhigh、两轮审查 astra/high、独立验收 luna/max；所有已知 P2 及以上问题均已关闭。
本台账随后仅更新完成状态与上述证据，不改已验产品/测试；末尾负测对齐只作定向及完整工程验证，
不增加正式审查轮次。定向 typing 的五项既有远端环境依赖诊断仍单独保留，不声称该命令全绿。
本轮没有执行完整远端 Runtime、安装 wheel/全后端矩阵或 release-check，没有启动 MinIO，也不推送。

### 2026-10-17：外部依赖 typing 诊断收尾

在提交 `7508aca650` 后单独复现并修复上述五项诊断。根因不是 Lazy DSL 产品类型：
本地 `clickhouse-connect 1.3.0` 未携带 `py.typed`，三个直接导入因此产生 `import-untyped`；
MySQL 环境辅助模块缺少 `types-mysqlclient`，且 `connection()` 未声明返回类型。
[ClickHouse 1.4.0 发布说明](https://github.com/ClickHouse/clickhouse-connect/releases/tag/v1.4.0)
确认该版开始提供 PEP 561 类型信息，并明确空结果的 `first_row` 返回 `None`。

修复涉及开发依赖、两个环境辅助模块和对应依赖测试：dev 声明 `clickhouse-connect>=1.4.0` 与
`types-mysqlclient>=2.2.0`；ClickHouse 在身份行/分片计数行解包前断言非空；MySQL 返回
`Iterator[Connection]`，使用驱动已有且已标注类型的连接上下文管理器关闭连接。
已核对 mysqlclient 2.2.7 的 `Connection.__exit__` 直接调用 `close()`；stubs 的直接 `close()`
缺少注解，不以 ignore、cast、动态导入或 mypy 配置绕过。未修改产品运行依赖或公开 API。

原 17 文件 typing 命令及包含两个 helper 的 19 文件命令均零诊断，定向 lint/import 检查通过。
本地探针使用实际 MySQL 驱动上下文方法验证正常/异常退出均关闭一次并保留原异常；使用实际
ClickHouse `QueryResult` 与替身连接验证单机/集群 setup 的正常身份行及空结果拒绝。
这些探针不构成真实服务器验收。首次完整门禁为 4587 passed / 1 failed：已有 metadata 测试
以 `mysql` 子串拦截开发依赖，将纯类型包误判为原生驱动。改用 `Requirement` 解析包名和 extras，
继续禁止 `mysqlclient` 及 `ibis-framework[mysql]`；本地反例确认两条原生依赖路径仍被拒绝。
已核对安装的 `types-mysqlclient 2.3.0.20260923` 无 `Requires-Dist`，不会引入原生库。
三个 metadata 测试、三个修改 Python 模块的 typing/lint/import 检查通过。
最终无收窄变量的 `make check-agent` 完整通过：788 文件格式、Ruff/import contracts、366 文件
typing、4588 默认测试（83 项既有警告，116.31 秒）及 API 文档构建。日志为
`/tmp/marivo-external-typing-check-agent-final.log`，原 17 文件 typing 日志为
`/tmp/marivo-external-typing-consumers.log`。本次五项诊断全部关闭，未运行远端 Runtime 或
release-check，未启动服务；修复单独本地提交，不 push。
