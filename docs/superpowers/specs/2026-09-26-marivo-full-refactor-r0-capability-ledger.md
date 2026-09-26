# R0.2 能力、公开面与消费者反查台账

Date: 2026-09-26

Status: R0.2 现状反查完成；目标接口、K 与物理资格仍须按 R0.3–R0.5 冻结。本文不代表新 DSL、后端或 Agent 验收通过。

依据：[主计划 §3、§6](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)、[R0 实施文档 §3 R0.2](2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md)、[代数 v0.5](2026-09-23-analysis-algebra-theory.md)、[接口设计](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md)、[架构设计](2026-09-24-marivo-analysis-dsl-architecture-design.md)、[R0.1 历史证据索引](2026-09-26-marivo-full-refactor-r0-evidence-index.md)。下文“目标”只引用这些已接受的方向；标「待 R0.3」的 typed 参数、规则、K 不能由实施者自行补完。

## 1. 本次可复核快照与读法

| 项 | 本次读取 |
| --- | --- |
| checkout / branch / HEAD | `/Users/lichengxiang/source/oss/marivo` / `panda` / `d5e06022c7fcd355b2a31ab6935aea593b60f0d1` |
| 起步 `git status --short --branch` | `## panda`，无未提交路径；本任务按用户指定直接在 panda 写台账，不复用 R0.1 隔离 worktree 的 HEAD 冒充本次代码 |
| 代数 / 接口 / 架构 SHA-256 | `40ce44e730a245b9a9dad50ac6fe64effa5c374960ef658ec9eaaa328f6a2e14` / `d31aa12ac4f4bffe571572e7824a7d2f33f83f6e8a372817905c5dd30ab5e4ad` / `1c8bf9f9ccae7961b165b6ace061b721962bd634489a9e6dd95d0909e4d074b5` |
| 主计划 / R0 计划 SHA-256 | `2c433063e7275e33481cf9fd33be28d32ea5236c6167d6484ee14b2239f93646` / `fd077522e35edc3450bfe6728544d5166c9002d3b8502e80ffdb4f255e382c72` |
| 扫描入口 | 三层 `__init__.py`、`analysis/public_dsl.py`、`session/`、`observation/`、`datasets/`、三层 `_capabilities/`、`tests/test_public_surface.py`、`tests/test_agent_api_drift.py`；再反查 compiler、materialization、ontology、项目工具、CLI、devtools 与 latest site |

“现状”指此 HEAD 可导入、可解析或有调用链的代码，不是文档方案。`K` 是预先声明且带参数的合法续算集合（代数 §9.2）；本台账将已可见的续算与目标要求分开，**不**把两个实现当前都拒绝的动作写成 K 保持。`H:` 后面是当前 `marivo.help("<surface>.<target>")` 的 canonical ID，不是将来的名称。`L/M` 是目标 Logical/Materialized 视图；具体公开类名若未在 owning spec 固定，记为形状而不虚构签名。`E` 是旧证据位置；默认状态均为“历史/静态定位，目标未验证”。

### 1.1 公开面和披露入口

| 面 | 当前可导入路径和披露 owner | 迁移判断 |
| --- | --- | --- |
| 顶层 | `marivo.help`、`marivo.__version__`；`tests/test_public_surface.py` 限定无顶层执行 alias | 保留单一 Help 协调器；不添 `marivo.session` |
| datasource | `marivo/datasource/__init__.py` 公开六 backend spec/factory、`table/csv/parquet/json/source_param/partition/time_range/unpruned`、`inspect/sample`、管理 `register/remove/connect/test`，**还公开 `raw_sql`**；原生 `datasource/_capabilities/registry.py` 当前 34 targets | C01；任意 SQL 执行目标删除须在 R0.3/R0.5 逐提交点处理。`md.raw_sql` 不是 provenance |
| semantic | `marivo/semantic/__init__.py` 的 Entity/变量/Relationship/Metric/Event/StateModel/日历 builder、`ref`、`parity_check`、`from_sql`；原生 `semantic/_capabilities/registry.py` 当前 108 targets | C02/C06/C17；`from_sql` 仅 provenance 值，`parity_check` 的 SQL 执行边界待 R0.3/R0.5 |
| analysis Dataset 家族 | `analysis/__init__.py` 延迟导出 `Dataset`、各 `Logical*Dataset`/`Materialized*Dataset`、descriptor/field/state/contract、Event/Lifecycle/候选/预测等；`analysis/_capabilities/dataset_registry.py` 与 `datasets/registry.py` 注册旧家族 | C03–C15；旧家族作为消费者和方法证据逐个迁移，不因类名含 Dataset 就删掉领域方法 |
| analysis 新公共 J1–J4 | `analysis/_public.py` 汇总 `public_dsl.py` 的 Domain/Category/Numeric/Ratio/Difference/Statistic/Association 等具体 L/M 类，`route/routes`、`sum/count/mean`；`session.members()` 入口 | 目标单量关系的当前窄实现；仅单列非版本成员、J1–J4 路径，不当成 C01–C18 已完成 |
| Help 与测试 | `analysis/_capabilities/registry.py` 组装的当前 registry 有 323 targets；`tests/test_public_surface.py` 固定三层 `__all__` 和 analysis 顺序 hash；`tests/test_agent_api_drift.py` 固定 `dir(mv)`、不泄露内部类型、运行时不查询公开 catalog collection | C16；Help 有 Dataset 与 `dsl.*` 两群目标，R10 随旧家族移除后才可能收束为单一入口 |

本次使用 `.venv/bin/python` 读取三层 registry 的 `canonical_ids()`，计数分别为 datasource 34、semantic 108、analysis 323；它只证明本 checkout 的可解析注册，不证明每个 target 的内容正确或可执行。当前焦点 H：C01 `datasource.authoring/table/json/inspect/raw_sql`；C02 `semantic.authoring/objects/builders/checks` 及精确 builder；C03–C10 `analysis.datasets/population/observe/metric_dataset.*` 和 `analysis.session.members/dsl.*`；C11–C13 `analysis.events.match/lifecycle.replay/event_dataset.*/lifecycle_dataset.*`；C14 `analysis.discovery.*/metric_dataset.correlate/forecast`；C15 `analysis.runtime.sessions/artifacts/evidence`；C16 `analysis.entry/methods/inputs`。精确 target 以当前 registry 为准，旧 site 文字不能替代本次可解析清单。

### 1.2 双向调用链及消费者

| 入口或状态 | 当前可追到的调用/存储链 | 需同时迁移的消费者 |
| --- | --- | --- |
| `Session.members` | `session/core.py` → `observation/dsl_j1.py:J1Context.members` → `public_dsl.new_members` → L `.execute()` → `DatasetRuntime.execute_j1` → `materialization/dsl_j1_runtime.py`、`dsl_j1_artifact.py`/`dsl_j1_receipt.py` → Store；`J3Observed` 在 J3 ratio 路径 | `tests/test_analysis_dsl_public.py`、`test_analysis_dsl_public_static.py`、`test_analysis_dsl_contracts.py`、S2/S3/J1 测试、`devtools/analysis_dsl_s4_p3/{j1,j2,j3,j4,recover}.py`、latest first-analysis/analysis-workflow |
| `Session.population/observe` | `session/core.py` → `session/_lazy_sources.py` → `observation/population.py` / `observation/metric.py` → `datasets/base.py`/`handles.py`/registry → `materialization/dataset_execution.py:execute` → family codec/publication/Store | 旧 `test_lazy_*`、Runtime workers、CLI 与 `analysis._capabilities`、latest analysis-workflow；领域 Event/Lifecycle 的 `population` 参数和 `select_subjects` 也消费旧类型 |
| 单量 J1–J4 续算 | `public_dsl.py` L/M 方法 → `observation/dsl_j1.py` 节点 → `compiler/dsl_j1_source.py`、`dsl_j3_ratio.py`、`materialization/dsl_j1_runtime.py`；`wrap_materialized` 从节点 kind 重建 M 类型 | Help `dsl.*`、`tests/test_analysis_dsl_*`、S4 devtools；当前 J4 only same-Entity Spearman，不可替代完整 association 家族 |
| 旧 Dataset 方法 | `observation/metric.py` 的 `.compare/.correlate/.forecast/.discover/.with_time_axis/.aggregate/.rollup`，`operators/{delta,attribution,association,forecast_dataset,candidate_dataset}.py` 的 `.where/.rank/.limit` → `compiler/*` → `materialization/*_{codec,publication}.py` | `tests/test_lazy_*`、`analysis/_capabilities/{dataset_navigation,dataset_model,catalog_inputs}.py`、site 最新/历史版；历史版不因本次自动改写 |
| Event/Lifecycle | `session/_lazy_sources.py:LazyEvents.match/LazyLifecycle.replay` → `domains/{event,lifecycle}*.py` → `compiler/{event,lifecycle}*.py` → family publication/codec；`semantic/{event,state_model}.py` 提供声明 | C11–C13 的领域 namespace/结果、相关测试和 Help；业务顺序、覆盖、归属不得用 J1 行键替代 |
| Source/Run/Artifact | `materialization/admission.py:DatasetRuntime` 的 Dataset `execute`、J1 `execute_j1` 与 `recover`；`session/core.py:artifact/runs/graph/revalidate` → `store.py`、`reconciliation.py`、`reads.py`、`evidence/_dataset_*` | `tests/test_analysis_dsl_execution_identity.py`、`test_analysis_dsl_exchange.py`、`test_lazy_*runtime*`、`test_lazy_*publication*`、Help `artifacts/evidence/runtime.*`、CLI |
| ontology/项目工具 | `marivo/ontology/` 引用 Semantic Ref 与 analysis Artifact；`marivo/project.py`、`marivo/cli.py`、telemetry 和打包元数据消费 surface/状态 | `tests/test_ontology_extension.py`、`test_project.py`、`test_telemetry.py`、`test_datasource_packaging_metadata.py`；可选依赖和权限需 R10 复核 |

`datasets/descriptors.py`/`handles.py`/`contract.py` 当前拥有字段、行域、状态与动态续算；`observation/contracts.py`、各 `operators/*_contracts.py` 和 `dsl_j1_contracts.py` 各自还拥有方法条件。R0.4 须给统一签名、方法注册和 codec 分配唯一责任，不能保留两个 registry 互相转发。`materialization/execution.py:ExecutionAdapter` 与 `datasource/engines/base.py:EngineProfile` 的重叠资格/传输责任属于 R0.5。Store 的 SQLite 事务与 datasource SQL 分开审计。

## 2. C01–C18 能力去向

本表的目标构造只记录接口设计已命名的规范入口或确定的形状；每行的现状 Help/代码、L/M/K、路线、状态及反例在 §3 子单元和 §4 共用资格约束展开。`保留` 指业务能力保留，通常伴随公开形状收紧；`删除旧入口` 不表示删除同一业务问题。

| ID | 当前代码与 Help；当前 owner/证据 | 目标唯一入口及 L/M、目标 K；去向/阶段 |
| --- | --- | --- |
| C01 | `datasource/{authoring,source,inspection,manage,json_source,engines}*`；H `datasource.authoring/table/csv/parquet/json/inspect/raw_sql`；旧 C1/C2/C5/C10 | `md` typed 声明、inspect/sample 与 adapter；Datasource result 的现态 `.show()`/repair，非分析 K。保留并删除任意 SQL 入口，R1；真实读取/元数据/JSON/认证逐格重验 |
| C02 | `semantic/{authoring,ir,resolver,validator,metric_graph*,runtime_metric*,event,state_model}*`；H `semantic.objects/*builder`；旧 C3–C9 | `ms` Entity/变量/Relationship/Metric/Event/StateModel/Calendar Ref 和 builder；分析域/Relation 只消费 Ref；定义结果非分析 K。保留并收紧身份、版本、业务顺序依据，R2 |
| C03 | `session/core.py:population,members`、`observation/{population,dsl_j1}.py`；H `analysis.population/session.members/inputs.population`；J1 | `session.members(EntityRef, at=...)` → L/M AnalysisDomain；K 为获准 `read/observe/group_by/each/execute` 与材料化后固定续算。旧 population 删除，版本化与多列身份增量 R5 |
| C04 | `session.observe`、`observation/metric.py`、`runtime_metric.py`、`dsl_j1.py:J1Observed/J3Observed`；H `analysis.observe/runtime_metric.*/dsl.LogicalAnalysisDomain.observe`；J1/J3、旧 C4 | `domain.observe(MetricRef|RuntimeMetricExpr, during, via)` → L/M NumericRelation；K 为合法 `where/group_by/summarize/rollup/compare/execute`，按部件收紧。旧多量 Dataset 删除，R5 |
| C05 | `observation/{aggregation,coordinates,rollup}.py`、`metric.py`、`public_dsl.Grouped*`；H `analysis.metric_dataset.aggregate/rollup/dsl.*`；J1/J3 | `relation.group_by(...).summarize(method)` 与 `.rollup()` → L/M NumericRelation；K 不得由当前行统计自动升级为原量上卷。保留两种归约、删除旧 `.aggregate()` 同义路径，R5 |
| C06 | `observation/temporal.py`、`compiler/{temporal,source_time}.py`、`semantic/{_authoring_temporal,metric_graph_lowering}.py`；H `analysis.time_scope/grain/metric_dataset.with_time_axis`、semantic calendar；旧 C3/C6 | `mv.time_scope/grain/time_grid`、`domain.each(grid)`、认证期间与 `rollup`；L/M 时间坐标关系。K 依时间角色、格完整性与状态，R2/R5；旧 `with_time_axis` 收束 |
| C07 | `operators/{compare,delta}.py`、`public_dsl` Difference；H `analysis.metric_dataset.compare/dsl.LogicalNumericRelation.compare/datasets.where`；J2 | `current.compare(baseline)`、`relation.where(...)`、`.members(...)` → L/M Difference/Relation/Domain；K 依键对齐和主体映射，R6；旧 DeltaDataset 删除 |
| C08 | `observation/population.py`、`operators/{row,attribute,attribute_values}.py`、`datasets/actions.py`；H `analysis.datasets.rank/limit`；旧 C8 | `cohort`、`share/penetration`、`standardize(reference=ReferenceWeights)`、`summarize(weighted_mean(...))`、`rank/limit`、terminal table；L/M 具体关系/结果。参照/权重 typed 构造和 K 待 R0.3，实施 R6 |
| C09 | `operators/{attribution,attribution_contracts}.py`、`compiler/attribution.py`；H `analysis.delta_dataset.attribute`；旧 C8 | Difference `.attribute(axes, mode, top_k)` → L/M AttributionResult；K 为合法筛选、排名、具名数值视图，不能把 residual=0 当完整证明。保留方法、删除旧 Dataset 容器，R6 |
| C10 | `observation/{distinct_contracts,distribution_contracts}.py`、`compiler/{distinct,distribution}*.py`；H 随 Metric/Dataset 方法；旧 C7 | exact distinct/quantile 直接 `observe` → L/M NumericRelation；K 仅当前行 `summarize/where` 等可证明续算，首次原量 `rollup`/对应 attribution 明确拒绝。收紧，R5/R9 |
| C11 | `semantic/event.py`、`session/_lazy_sources.py:LazyEvents.match`、`domains/event*.py`；H `analysis.events.match/event_dataset.*`；旧 C9 | `session.events.match(pattern,population=AnalysisDomain,...)` → L/M JourneyResult；K 为已保留轨迹允许的 funnel/compare/attribute/selector。保留 matcher 身份，改域输入，R7 |
| C12 | `domains/event_reducers.py`、`compiler/event_reducers.py`；H `analysis.event_dataset.time_to_event/select_subjects`；旧 C9 | `journeys.time_to_event(from_step,to_step)` 与有依据 `.select_subjects` → L/M DurationRelation/Domain；K 依完成/观察时长和 Journey→Subject 映射，R7 |
| C13 | `semantic/state_model.py`、`session/_lazy_sources.py:LazyLifecycle.replay`、`domains/lifecycle*.py`；H `analysis.lifecycle.replay/lifecycle_dataset.*`；旧 C9 | `session.lifecycle.replay(model,population=AnalysisDomain,...)` → L/M LifecycleResult；K 为 distribution/dwell/transitions/violations 与时点 read→where→members，业务顺序前提待 R0.3，R7 |
| C14 | `operators/{discovery,association,forecast*}.py`、`compiler/{correlation,entity_candidate,driver_candidate}.py`；H `analysis.discovery.*/metric_dataset.correlate/forecast`；旧 C8、J4 | Relation `.discover/.correlate(*others)/.forecast(...)` → L/M Candidate/Association/Forecast 具体变体；K 含合法具名视图筛选、排名，不含原量相关/预测上卷。保留闭合方法，R8 |
| C15 | `materialization/{admission,dataset_execution,dsl_j1_runtime,store,reconciliation,reads}*`、`session/core.py`、`evidence/*`；H `analysis.runtime.sessions/artifacts/evidence`；J1–J4/P4 | 唯一 Session/Run/Artifact/Store owner；所有 L `.execute()` 和 M 固定续算，共用 source reevaluation / fixed exact-hit、receipt/恢复 K。保留服务、替换双运行协议，R4+ |
| C16 | `marivo/_help/*`、三层 `_capabilities/*`、`analysis/_public.py`、`errors.py`、CLI/site/skills；H `analysis.entry/methods/inputs/dsl.*` | 单一原生 Help/有界 `repr/show/contract`、类型与结构化 repair；K 披露必须与材料化部件一致。保留披露能力、删除旧 Help alias/inventory，逐阶段/R10 |
| C17 | `marivo/ontology/*`、`project.py`、`cli.py`、telemetry、打包/可选依赖；H `ontology.authoring`、各 surface project/diagnostic | ontology 仅关联新 Ref/Artifact，不授分析规划/准入；项目/秘密/诊断/依赖保留。非分析 K；R2/R10 |
| C18 | 当前无公开 Anchor/retention 构造；理论/接口设计 §8.2 有目标问题，不能把 `EventPattern` 绝对随访冒充相对窗口 | L/M Anchor/Retention 具体 typed 变体、固定 Ω、Subject 与 Subject×Anchor、K+/K-/K? 及 K **待 R0.3 owning spec**；R7 实施。现状：目标必需但未实现、公共契约阻塞 |

## 3. 独立准入方法子单元

下列每一行是独立资格单元；同一行跨 backend/数值/时间/来源形状仍须在 R0.5 展开。`+` 是独立正例，`−` 是拒绝例，不是已执行结果。现有测试文件是可复用输入或旧回归，**不是**目标验收；`make test TESTS='…'` / `make runtime-test TESTS='…'` 是旧现状复核命令，目标精确命令须在 owning spec 和 R0.4/R0.5 冻结后写入验收索引。R0.2 已定位方法、代码/测试和反例，未把未决定的签名或后端格伪造为通过。

| 单元 | 当前实现/Help/测试锚点 | 目标方法、类型/前提、L/M 与 K 边界；独立 + / − |
| --- | --- | --- |
| C01.a 六后端声明/连接 | `datasource/authoring.py`、`engines/{duckdb,sqlite,postgres,mysql,trino,clickhouse}.py`；H 同名 factory、`register/connect/test`；`test_datasource_typed_specs.py` | typed Spec→Ref，真实元数据/凭据脱敏；+ 六 profile 声明 round-trip，− 缺 env/错误权限。R1/R9 真实 backend 格待 R0.5 |
| C01.b 物理来源与参数 | `source.py/json_source.py/inspection.py`；H `table/csv/parquet/json/source_param/inspect/SourceInspection.sample`；`test_datasource_json_source.py` | Table/CSV/Parquet/JSON 和作用域参数，Ibis 读取；+ 文件/JSON 样本与字段，− 非法 scope/认证泄露。SQL 移除见 C01.c/R0.5 |
| C01.c SQL/parity 边界 | `manage.py:raw_sql`、`semantic/parity.py`；H `datasource.raw_sql/semantic.parity_check`；`test_datasource_raw_sql.py`、`test_semantic_parity.py` | 公共任意 SQL 执行删除；`ms.from_sql` 仅 provenance。+ Ibis 可表达的事实检查，− `raw_sql`/`backend.sql` 绕过；逐提交点在 SQL 台账处理 |
| C02.a Entity/变量/版本 | `semantic/{authoring,ir,validator,resolver}.py`；H `entity/dimension/measure/time_dimension/snapshot/validity`；`test_semantic_phase2_validity.py` | Ref-only、完整复合键和精确版本；+ exact at，− last-known/历史去重；R2/R5 |
| C02.b Relationship/Metric | `semantic/{metric_graph*,runtime_metric*,unit_algebra}.py`；H `relationship/aggregate/count/ratio/weighted_mean/linear/metric`；`test_semantic_metric_graph_lowering.py` | 声明与 builder 推导依据分开；+ 合格唯一映射，− fanout/单位数值碰巧一致；R2 |
| C02.c Event/State/calendar | `semantic/{event,state_model,_authoring_temporal}.py`；H `event/participant/state_model/period_calendar/temporal_set`；`test_semantic_event.py`、`test_semantic_state_model.py` | occurrence、业务顺序与日历定义；+ 明确先后，− 同时刻仅按 ID 排序；顺序契约 R0.3/R2 |
| C03.a 根成员/身份 | `dsl_j1.py:J1Context.members`、`session/core.py:members`，旧 `population`；H `session.members/population`；`test_analysis_dsl_j1_construction.py` | Entity Ref→L/M Domain；+ 两成员完整键，− 重复键/复合键截断；单列限制迁移 R5 |
| C03.b 版本成员 | `session.population` 的 `time_scope`、`semantic/validity`；H `inputs.population`；`test_semantic_phase2_validity.py` | `members(at=exact|before_end)`；+ 指定快照，− 最近可得版本；当前 J1 无该公开参数，R5 |
| C03.c read/选择/映射 | `public_dsl.LogicalAnalysisDomain.read`、旧 `population.where`；H `dsl.LogicalAnalysisDomain.read/datasets.where`；`test_analysis_dsl_public.py` | Category/Numeric/Time/Boolean L/M Relation，where→members K 需保留对应；+ 单值属性，− 多值偷选第一条；R5 |
| C04.a 单根 aggregate/slice | `dsl_j1.py`、`compiler/dsl_j1_source.py`、旧 `metric.py`；H `dsl.LogicalAnalysisDomain.observe/observe`；J1 fixture | Metric Ref→Numeric L/M，组件各自绑定来源/范围；+ J1=1000，− 无覆盖时把空作零；R5 |
| C04.b 多根 ratio | `dsl_j1.py:J3Observed`、`compiler/dsl_j3_ratio.py`；H `dsl.LogicalRatioRelation.*`；J3 fixture | 组件各根路径及原状态；+ web 160/3、总体 40，− 子组比率平均 130/3 冒作总体；R5 |
| C04.c linear/weighted/runtime | `semantic/runtime_metric*`、`analysis/runtime_metric.py`；H `runtime_metric.{aggregate,weighted_mean,slice,ratio,linear}`；`test_lazy_runtime_metric*` | 同一次 observe 的封闭计算图；+ 正确组件配对，− 数值单位相同却实例域不同；R2/R5 |
| C05.a 坐标/空组 | `observation/coordinates.py`、`dsl_j1_dataset.py`；H `metric_dataset.with_dimensions`；J3 fixture | Entity×分类×时间实际元组并集与显式空目标；+ mobile 零，− inner join 丢仅一侧坐标；R5 |
| C05.b 当前行统计 | `public_dsl.RowMethod`、旧 `aggregation.py`；H `dsl.{sum,count,mean}/dsl.LogicalNumericRelation.summarize`；`test_analysis_dsl_public.py` | `sum/mean/count/count_defined` 逐 Cell；+ mean 50.5，− 非 Defined 默默丢弃；`count_defined` 尚未在当前 J1 导出，R5 |
| C05.c 原状态上卷 | `public_dsl.rollup`、`observation/rollup.py`、`materialization/retained.py`；H `dsl.LogicalNumericRelation.rollup/metric_dataset.rollup`；J3 fixture | RequiredParts + 贡献不重叠；+ AOV 200/101，− 用当前行均值或仅 ratio 值；R5 |
| C06.a time scope/grid | `analysis/__init__.py:time_scope,grain`、`_temporal.py`；H `analysis.time_scope/grain`；`test_analysis_dsl_public_static.py` | typed `time_grid/each`、边缘格与 DST；+ 七本地日，− 168h 冒作七日；R5 |
| C06.b 认证期间/累计/fold | `semantic/_authoring_temporal.py`、`compiler/temporal.py`；H `semantic.cumulative/period_calendar/analysis.window_bucket`；旧 C6 | 期间身份、来源/观察/成员/坐标四时间角色；+ 完整认证期间，− 未验证时间轴或折叠顺序交换；R2/R5 |
| C07.a 比较/嵌套差 | `public_dsl.compare`、旧 `operators/compare.py`；H `dsl.LogicalNumericRelation.compare/metric_dataset.compare`；J2 fixture | Time/Cohort/Period change、ExactKeys/UnionKeys，L/M Difference；+ J2 A/C 下降，− 缺侧补零；R6 |
| C07.b 谓词选人与派生 | `public_dsl.where/members`、`observation/predicates.py`、旧 `datasets/actions.py`；H `dsl.LogicalDifferenceRelation.where/datasets.where`；J2 fixture | Cell 严格谓词、固定主体映射；+ {A,C}，− Unknown 当 false 或缺主体映射回源；R6 |
| C07.c 普通 Relation ratio | 当前 `J3Observed` 是 Metric ratio，`runtime_metric.ratio` 是同观察表达式；**无两独立 Relation 普通 ratio** | `left.ratio(right)` 的同域/单位/缺侧/零分母与 K 待 R0.3；+ 对应实例比值，− 仅数值可除却实例域不对应；R6 |
| C08.a cohort/份额 | 旧 `population.where`/`operators/attribute.py`；H `population/datasets.where`；旧 C8 | 全机会域资格、share/penetration 与 K+/K-/K?；+ 真/假/未知区分，− 删除未知后改分母；R6 |
| C08.b 统计权重/固定参照 | 旧 weighted_mean 图可读权重数值；当前**无** `StatisticalWeight/ReferenceWeights` 公共构造 | typed 权重/参照与 K 待 R0.3；+ 固定全域参照，− Top-K 后重算/缺层自动归一；R6 |
| C08.c rank/limit/table | `datasets/actions.py`、各旧 family `.rank/.limit`；H `datasets.rank/limit`；`test_lazy_distribution_topk.py` | 只改变展示/选取域，固定参照和原 basis；+ 并列排序，− limit 改归因范围；R6 |
| C09.a additive/component_mix | `operators/attribution*`、`compiler/attribution.py`；H `delta_dataset.attribute`；`test_lazy_attribution_masks.py` | Difference method/version 独立，保留 target/basis/rule；+ 分组增量核对，− residual=0 即称完整；R6 |
| C09.b joint/hierarchy/Top-K | `compiler/{attribution,distribution_attribution}.py`；H 同上；旧 C8 | 共同 Top-K→Other、原 scope、可筛选后核对；+ 两侧共同基准，− 各侧先限 Top-K；R6 |
| C10.a exact distinct | `observation/distinct_contracts.py`、`compiler/distinct*.py`；`test_lazy_distinct_*` | 原量直接观察、精确身份；K 不含首次 rollup/归因；+ 去重身份，− 子组 distinct 相加；R5/R9 |
| C10.b quantile/distribution | `observation/distribution_contracts.py`、`compiler/distribution*.py`；`test_lazy_distribution_*` | 精确线性插值或显式 approximate+算法披露；K 同上；+ 独立排序插值，− 分组 P95 平均；R5/R9 |
| C11.a first/every matching | `session/_lazy_sources.py:events.match`、`domains/event.py`；H `events.match/event_matching.*`；`test_lazy_event_contracts.py` | JourneyResult，assignment/重数独立；+ 两种匹配不同轨迹，− 重复归属；R7 |
| C11.b funnel/compare/attribute | `domains/event*.py`、`compiler/event_{reducers,comparison,attribution}.py`；H `event_dataset.funnel/compare/funnel_delta_dataset.attribute`；`test_lazy_event_comparison_*` | 领域方法与目标 selector；+ 合格绝对随访，− 覆盖不足判完成；R7 |
| C12.a duration | `domains/event_reducers.py`；H `event_dataset.time_to_event`；`test_lazy_event_reducer_*` | Duration L/M，完成/观察时长状态；+ 完成步骤差，− 未完成者冒完成耗时；R7 |
| C12.b subject selection | `domains/event.py:select_subjects`；H `event_dataset.select_subjects`；`test_lazy_event_population_continuations.py` | Journey→Subject 映射 K；+ known dropout，− Unknown 随访判流失；R7 |
| C13.a replay/history | `session/_lazy_sources.py:lifecycle.replay`、`compiler/lifecycle*.py`；H `lifecycle.replay`；`test_lazy_lifecycle_contracts.py` | canonical history/interval/transition/violation；+ 明确业务顺序，− 同刻 ID 稳定排序授权，R0.3/R7 |
| C13.b distribution/dwell/select | `domains/lifecycle_reducers.py`；H `lifecycle_dataset.*`；`test_lazy_lifecycle_reducer_*` | 领域结果 L/M 及 `read→where→members`；+ 指定时点，− 把当前终态当历史状态；R7 |
| C14.a 五 discover 方法 | `operators/discovery.py`、candidate family；H `discovery.{point_anomalies,interesting_windows,entity_outliers,period_shifts,driver_axes}`；`test_lazy_candidate_*` | 各方法版本与 Candidate 固定变体；+ 已知异常，− 空/零离散误称无异常；R8 |
| C14.b 相关 | `operators/association*`、`compiler/correlation.py`，窄 J4 Spearman；H `metric_dataset.correlate/dsl.LogicalNumericRelation.correlate`；`test_analysis_dsl_fixtures.py` | Pearson/Spearman/Kendall/lag，全两两配对；+ J4 −2/5，− inner join 偷丢键/常量出 NaN；R8 |
| C14.c 预测 | `operators/forecast*`；H `metric_dataset.forecast/forecast_models.*`；`test_lazy_forecast_*` | naive/drift/seasonal_naive、闭合未来网格；+ 已知序列，− 缺桶插补/区间界相加；R8 |
| C15.a execute/共享 | `admission.py:DatasetRuntime`、`dataset_execution.py` 与 `dsl_j1_runtime.py` 双入口；H `actions.execute/dsl.*execute`；`test_analysis_dsl_execution_identity.py` | 唯一来源求值身份、同图节点共享；+ 同节点共享，− 相同定义不同读取偷合并；R4 |
| C15.b publication/recovery | `store.py`、`dsl_j1_receipt.py`、各 codec/publication、`reads.py`；H `session.artifact/revalidate/evidence`；`test_analysis_dsl_exchange.py` | receipt/parts/Run 原子发布和冷恢复 K；+ 断源固定续算，− 损坏 receipt 或未知提交重复发布；R4 |
| C16.a Help/动态 K | 三层 `_capabilities/`、`_help/`、`public_dsl.AnalysisContract`；H `analysis.entry/dsl.Value.contract`；`test_agent_api_drift.py` | 具体 L/M、同 owner 静态 Help 与当前 K；+ 可达精确 target，− 展示禁用续算；逐阶段/R10 |
| C16.b 安装包/Agent | site latest EN/ZH、packaged skills、CLI、`devtools/analysis_dsl_s4_p3/`；S4 旧记录 | 新 wheel、独立 oracle、真实 Agent 各自验收；+ fresh session 完整路径，− 旧 trace/脚本冒新 DSL；R10 |
| C17.a ontology | `marivo/ontology/`；H `ontology.authoring`；`test_ontology_extension.py` | 可选关联新 Ref/Artifact；+ 正确身份，− ontology 自动授因果/计算准入；R2/R10 |
| C17.b 项目/遥测/依赖 | `project.py`、`cli.py`、`telemetry/`、package metadata；`test_project.py`、`test_telemetry.py` | 秘密与主体键不泄露、可选依赖隔离；+ 离线基本 import，− 缺可选驱动导致核心 import 失败；R10 |
| C18.a Anchor 相对观察 | 当前无实现；仅接口设计 §8.2/理论 §8.5 | typed Anchor、elapsed/calendar、重叠贡献与覆盖，L/M/K 待 R0.3；+ DST 对照，− 七日=168h；R7 |
| C18.b retention | 当前无实现；不可用 Event 绝对窗口代替 | 固定 Ω、Subject/Subject×Anchor、K+/K-/K?、确定性界；+ 25/5/70→[25%,95%]，− 删未知单点率；R0.3/R7 |

## 4. 横向资格键、证据和阶段出口

R0.2 的每个方法行尚须按 `方法版本 × 数值类型 × 时间/来源形状 × 后端/表类型 × 路线` 展开；此键由 R0.5 SQL/adapter 台账与六后端矩阵唯一拥有。当前代码可见 DuckDB、PostgreSQL、MySQL、SQLite、Trino、ClickHouse engine/adapter；这只证明实现分支存在。旧 C3b Trino 不可达、C4 MySQL/Trino Decimal 限制、C5 非 Iceberg/Distributed、C6 fold 差异、C8/C9 领域限制和 C10 抽样/资源失败仍按 [R0.1 §4](2026-09-26-marivo-full-refactor-r0-evidence-index.md#4-c0c10-后端历史证据) 保持历史边界，**没有**新的 R1/R9 通过格。Ibis 来源、预先获准的 Ibis→Python、固定 Artifact→pandas 是候选路线；Artifact→DuckDB 目标删除。不得把生成 SQL、旧测试、当前 Help 或旧 wheel 当真实新 DSL 后端资格。

| 状态 | R0.2 判断 |
| --- | --- |
| 通过：现状定位 | C01–C18 的入口/缺入口、主要 owner、Help 群、执行/状态链和主要消费者已反查；J1/J3/`execute_j1` 与旧家族明确分列；领域、ontology、项目工具已列入 |
| 未验证：目标签名 | C18、普通 Relation ratio、StatisticalWeight/ReferenceWeights、业务顺序等具体 typed 输入及 K 由 R0.3 owning spec 决定；`count_defined`、time_grid 等目标增量不伪装当前导出 |
| 未验证：规则/模块 | 方法版本、元算子 RequiredParts/PartTransform、每个旧实现唯一新 owner、消费者删除阶段由 R0.4 冻结 |
| 未验证：物理资格 | SQL 提交点、adapter 七职责、六后端/类型/表形态/路线矩阵和逐格目标命令由 R0.5 冻结 |
| 阻塞：R0 整体 | 以上未闭合单元完成前不能把 R0 标通过，也不能把旧 J1–J4/C0–C10 的历史通过转授新 DSL |

复核本台账的静态命令：`git status --short --branch`、`git rev-parse HEAD`、`shasum -a 256 <五份输入>`、`rg -n '__all__|class J1Context|class J3Observed|def execute_j1' marivo/{datasource,semantic,analysis}`、`rg -n 'def (members|population|observe|match|replay|artifact|revalidate)' marivo/analysis/session`、`rg -n 'J1Context|J3Observed|LogicalPopulationDataset|raw_sql|parity_check' marivo tests devtools site/src/content/docs`、`git diff --check`。这些是事实扫描，不运行产品测试。目标正反例的旧独立 oracle 和精确历史复跑命令见 [R0.1 §5](2026-09-26-marivo-full-refactor-r0-evidence-index.md#5-可复用的独立-oracle故障与业务问题)；新目标测试命令须随 R0.4/R0.5 的规则和资格格落地，不以本页的历史文件名充数。
