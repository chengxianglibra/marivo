# R0.2 能力、公开面与消费者反查台账

Date: 2026-09-26

Status: R0.2–R0.4 静态契约与 R0.6 破坏性变更/交接已登记；R0.5 物理资格见独立 SQL 台账，必需控制/认证格仍阻塞。本文不代表新 DSL、后端或 Agent 验收通过。

依据：[主计划 §3、§6](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)、[R0 实施文档](2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md)、[代数 v0.5](2026-09-23-analysis-algebra-theory.md)、[接口设计](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md)、[架构设计](2026-09-24-marivo-analysis-dsl-architecture-design.md)、[R0.1 历史证据索引](2026-09-26-marivo-full-refactor-r0-evidence-index.md)。§1–§4 保留 R0.2 当时的现状快照和待办表述；§5–§6 是本轮接受的目标，冲突时以 owning spec 为准。

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
| datasource | `marivo/datasource/__init__.py` 公开六 backend spec/factory、`table/csv/parquet/json/source_param/partition/time_range/unpruned`、`inspect/sample`、管理 `register/remove/connect/test`，**还公开 `raw_sql`**；原生 `datasource/_capabilities/registry.py` 当前 34 targets | C01；保留 `md.raw_sql` 作为终端只读 SQL 逃生通道，不能回流 Analysis；内部 SQL 提交逐点替代。`md.raw_sql` 不是 provenance |
| semantic | `marivo/semantic/__init__.py` 的 Entity/变量/Relationship/Metric/Event/StateModel/日历 builder、`ref`、`parity_check`、`from_sql`；原生 `semantic/_capabilities/registry.py` 当前 108 targets | C02/C06/C17；R0.3 已定 `from_sql` 仅 provenance、执行 SQL 的 parity 目标删除；现状仍存在 |
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

`datasets/descriptors.py`/`handles.py`/`contract.py` 当前拥有字段、行域、状态与动态续算；`observation/contracts.py`、各 `operators/*_contracts.py` 和 `dsl_j1_contracts.py` 各自还拥有方法条件。R0.4 §6 已给统一规则、方法注册和 codec 分配唯一责任；实施时不能保留两个 registry 互相转发。`materialization/execution.py:ExecutionAdapter` 与 `datasource/engines/base.py:EngineProfile` 的重叠资格/传输责任已归并至 R0.5 SQL/adapter 台账。Store 的 SQLite 事务与 datasource SQL 分开审计。

## 2. C01–C18 能力去向

本表的目标构造只记录接口设计已命名的规范入口或确定的形状；每行的现状 Help/代码、L/M/K、路线、状态及反例在 §3 子单元和 §4 共用资格约束展开。`保留` 指业务能力保留，通常伴随公开形状收紧；`删除旧入口` 不表示删除同一业务问题。

| ID | 当前代码与 Help；当前 owner/证据 | 目标唯一入口及 L/M、目标 K；去向/阶段 |
| --- | --- | --- |
| C01 | `datasource/{authoring,source,inspection,manage,json_source,engines}*`；H `datasource.authoring/table/csv/parquet/json/inspect/raw_sql`；旧 C1/C2/C5/C10 | `md` typed 声明、inspect/sample、`md.raw_sql` 终端逃生通道与 adapter；Datasource result 的现态 `.show()`/repair，非分析 K。保留公共 raw SQL，内部读取仍由 Ibis；R1 重验真实读取/元数据/JSON/认证和终端边界 |
| C02 | `semantic/{authoring,ir,resolver,validator,metric_graph*,runtime_metric*,event,state_model}*`；H `semantic.objects/*builder`；旧 C3–C9 | `ms` Entity/变量/Relationship/Metric/Event/StateModel/Calendar Ref 和 builder；Relationship 在 R2 只推导结构基数，R5 消费者核验选定成员的匹配要求；定义结果非分析 K。保留并收紧身份、版本、业务顺序依据，R2/R5 |
| C03 | `session/core.py:population,members`、`observation/{population,dsl_j1}.py`；H `analysis.population/session.members/inputs.population`；J1 | `session.members(EntityRef, at=...)` → L/M AnalysisDomain；K 为获准 `read/observe/group_by/each/execute` 与材料化后固定续算。旧 population 删除，版本化与多列身份增量 R5 |
| C04 | `session.observe`、`observation/metric.py`、`runtime_metric.py`、`dsl_j1.py:J1Observed/J3Observed`；H `analysis.observe/runtime_metric.*/dsl.LogicalAnalysisDomain.observe`；J1/J3、旧 C4 | `domain.observe(MetricRef|RuntimeMetricExpr, during, via)` → L/M NumericRelation；K 为合法 `where/group_by/summarize/rollup/compare/execute`，按部件收紧。旧多量 Dataset 删除，R5 |
| C05 | `observation/{aggregation,coordinates,rollup}.py`、`metric.py`、`public_dsl.Grouped*`；H `analysis.metric_dataset.aggregate/rollup/dsl.*`；J1/J3 | `relation.group_by(...).summarize(method)` 与 `.rollup()` → L/M NumericRelation；K 不得由当前行统计自动升级为原量上卷。保留两种归约、删除旧 `.aggregate()` 同义路径，R5 |
| C06 | `observation/temporal.py`、`compiler/{temporal,source_time}.py`、`semantic/{_authoring_temporal,metric_graph_lowering}.py`；H `analysis.time_scope/grain/metric_dataset.with_time_axis`、semantic calendar；旧 C3/C6 | `mv.time_scope/grain/time_grid`、`domain.each(grid)`、认证期间与 `rollup`；L/M 时间坐标关系。K 依时间角色、格完整性与状态，R2/R5；旧 `with_time_axis` 收束 |
| C07 | `operators/{compare,delta}.py`、`public_dsl` Difference；H `analysis.metric_dataset.compare/dsl.LogicalNumericRelation.compare/datasets.where`；J2 | `current.compare(baseline)`、`relation.where(...)`、`.members(...)` → L/M Difference/Relation/Domain；K 依键对齐和主体映射，R6；旧 DeltaDataset 删除 |
| C08 | `observation/population.py`、`operators/{row,attribute,attribute_values}.py`、`datasets/actions.py`；H `analysis.datasets.rank/limit`；旧 C8 | `cohort`、`share/penetration`、`standardize(reference=ReferenceWeights)`、`summarize(weighted_mean(...))`、`rank/limit`、terminal table；R0.3 已定参照/权重 typed 构造和 K，实施 R6 |
| C09 | `operators/{attribution,attribution_contracts}.py`、`compiler/attribution.py`；H `analysis.delta_dataset.attribute`；旧 C8 | Difference `.attribute(axes, mode, top_k)` → L/M AttributionResult；K 为合法筛选、排名、具名数值视图，不能把 residual=0 当完整证明。保留方法、删除旧 Dataset 容器，R6 |
| C10 | `observation/{distinct_contracts,distribution_contracts}.py`、`compiler/{distinct,distribution}*.py`；H 随 Metric/Dataset 方法；旧 C7 | exact distinct/quantile 直接 `observe` → L/M NumericRelation；K 仅当前行 `summarize/where` 等可证明续算，首次原量 `rollup`/对应 attribution 明确拒绝。收紧，R5/R9 |
| C11 | `semantic/event.py`、`session/_lazy_sources.py:LazyEvents.match`、`domains/event*.py`；H `analysis.events.match/event_dataset.*`；旧 C9 | `session.events.match(pattern,population=AnalysisDomain,...)` → L/M JourneyResult；K 为已保留轨迹允许的 funnel/compare/attribute/selector。保留 matcher 身份，改域输入，R7 |
| C12 | `domains/event_reducers.py`、`compiler/event_reducers.py`；H `analysis.event_dataset.time_to_event/select_subjects`；旧 C9 | `journeys.time_to_event(from_step,to_step)` 与有依据 `.select_subjects` → L/M DurationRelation/Domain；K 依完成/观察时长和 Journey→Subject 映射，R7 |
| C13 | `semantic/state_model.py`、`session/_lazy_sources.py:LazyLifecycle.replay`、`domains/lifecycle*.py`；H `analysis.lifecycle.replay/lifecycle_dataset.*`；旧 C9 | `session.lifecycle.replay(model,population=AnalysisDomain,...)` → L/M LifecycleResult；K 为 distribution/dwell/transitions/violations 与时点 read→where→members，R0.3 已定业务顺序前提，R7 |
| C14 | `operators/{discovery,association,forecast*}.py`、`compiler/{correlation,entity_candidate,driver_candidate}.py`；H `analysis.discovery.*/metric_dataset.correlate/forecast`；旧 C8、J4 | Relation `.discover/.correlate(*others)/.forecast(...)` → L/M Candidate/Association/Forecast 具体变体；K 含合法具名视图筛选、排名，不含原量相关/预测上卷。保留闭合方法，R8 |
| C15 | `materialization/{admission,dataset_execution,dsl_j1_runtime,store,reconciliation,reads}*`、`session/core.py`、`evidence/*`；H `analysis.runtime.sessions/artifacts/evidence`；J1–J4/P4 | 唯一 Session/Run/Artifact/Store owner；所有 L `.execute()` 和 M 固定续算，共用 source reevaluation / fixed exact-hit、receipt/恢复 K。保留服务、替换双运行协议，R4+ |
| C16 | `marivo/_help/*`、三层 `_capabilities/*`、`analysis/_public.py`、`errors.py`、CLI/site/skills；H `analysis.entry/methods/inputs/dsl.*` | 单一原生 Help/有界 `repr/show/contract`、类型与结构化 repair；K 披露必须与材料化部件一致。保留披露能力、删除旧 Help alias/inventory，逐阶段/R10 |
| C17 | `marivo/ontology/*`、`project.py`、`cli.py`、telemetry、打包/可选依赖；H `ontology.authoring`、各 surface project/diagnostic | R2 验证精确 Semantic Ref；R4 建立新 Artifact 身份/receipt 后由 R10 只读关联，ontology 不授分析规划/准入；项目/秘密/诊断/依赖保留。非分析 K；R2/R4/R10 |
| C18 | 当前无公开 Anchor/retention 构造；理论/接口设计 §8.2 有目标问题，不能把 `EventPattern` 绝对随访冒充相对窗口 | R0.3 已定 L/M Anchor/Retention typed 变体、固定 Ω、两种单位/量词、K+/K-/K? 与 K；R7 实施。现状：目标必需但未实现 |

## 3. 独立准入方法子单元

下列每一行是独立资格单元；同一行跨 backend/数值/时间/来源形状在 R0.5 §4 展开。`+` 是独立正例，`−` 是拒绝例，不是已执行结果。现有测试文件是可复用输入或旧回归，**不是**目标验收；`make test TESTS='…'` / `make runtime-test TESTS='…'` 是旧现状复核命令，目标新测试文件和示例命令已登记在 R0.5，但尚未创建或运行。R0.2 已定位方法、代码/测试和反例，未把未执行的资格格伪造为通过。

| 单元 | 当前实现/Help/测试锚点 | 目标方法、类型/前提、L/M 与 K 边界；独立 + / − |
| --- | --- | --- |
| C01.a 六后端声明/连接 | `datasource/authoring.py`、`engines/{duckdb,sqlite,postgres,mysql,trino,clickhouse}.py`；H 同名 factory、`register/connect/test`；`test_datasource_typed_specs.py` | typed Spec→Ref，真实元数据/凭据脱敏；+ 六 profile 声明 round-trip，− 缺 env/错误权限。目标格见 R0.5，R1/R9 真实运行未验证 |
| C01.b 物理来源与参数 | `source.py/json_source.py/inspection.py`；H `table/csv/parquet/json/source_param/inspect/SourceInspection.sample`；`test_datasource_json_source.py` | Table/CSV/Parquet/JSON 和作用域参数，Ibis 读取；+ 文件/JSON 样本与字段，− 非法 scope/认证泄露。SQL 移除见 C01.c/R0.5 |
| C01.c SQL/parity 边界 | `manage.py:raw_sql`、`semantic/parity.py`；H `datasource.raw_sql/semantic.parity_check`；`test_datasource_raw_sql.py`、`test_semantic_parity.py` | 保留 `md.raw_sql`/`RawSqlResult`/Help 的单条只读终端查询；`ms.from_sql` 仅 provenance，产品 parity 不执行 SQL。+ 截断/超时/终端边界，− raw 结果回流 Analysis 或内部 `backend.sql` 绕过；逐提交点在 SQL 台账处理 |
| C02.a Entity/变量/版本 | `semantic/{authoring,ir,validator,resolver}.py`；H `entity/dimension/measure/time_dimension/snapshot/validity`；`test_semantic_phase2_validity.py` | Ref-only、完整复合键和精确版本；+ exact at，− last-known/历史去重；R2/R5 |
| C02.b Relationship/Metric 静态契约 | `semantic/{authoring,validator,metric_graph*,runtime_metric*,unit_algebra}.py`；H `relationship/aggregate/count/ratio/weighted_mean/linear/metric`；`test_semantic_r21_identity_relationship.py`、`test_semantic_metric_graph_lowering.py` | Relationship 只声明有向映射并推导结构基数，无全局 `required`；声明与 builder 推导依据分开；+ 完整 K 的结构性单值，− 把该结论当作每行必配或实际唯一；R2 |
| C02.b 消费与来源核验 | `analysis` 各使用关系路径的方法、`semantic.source_health`；H 对应 Analysis 方法及 `semantic.source_check.relationship_matches`；R5 独立反例 | 消费者按成员范围、角色与精确版本规定必配或允许缺失的结果语义；实际缺失必配目标和单值路径多重匹配结构化拒绝；有界 source-health 不作全局证明；R5 |
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
| C07.c 普通 Relation ratio | 当前 `J3Observed` 是 Metric ratio，`runtime_metric.ratio` 是同观察表达式；**无两独立 Relation 普通 ratio** | R0.3 已定精确同域/显式一一对应、Undefined 零分母与 K，见 §5；+ 对应实例比值，− 仅数值可除却实例域不对应；R6 |
| C08.a cohort/份额 | 旧 `population.where`/`operators/attribute.py`；H `population/datasets.where`；旧 C8 | 全机会域资格、share/penetration 与 K+/K-/K?；+ 真/假/未知区分，− 删除未知后改分母；R6 |
| C08.b 统计权重/固定参照 | 旧 weighted_mean 图可读权重数值；当前**无** `StatisticalWeight/ReferenceWeights` 公共构造 | R0.3 已定独立 Semantic 权重角色、Relation 固定参照与 K，见 §5；+ 固定全域参照，− Top-K 后重算/缺层自动归一；R6 |
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
| C17.a Semantic Ref 关联 | `marivo/ontology/`；H `ontology.authoring`；`test_ontology_extension.py` | `mo.load(semantic=catalog)` 验证当前精确 Ref，ontology 与 semantic catalog 指纹共同定位上下文；− ontology 自动授因果/计算准入；R2 |
| C17.a Artifact 交接 | Runtime Artifact 身份、receipt、精确语义依赖及 ontology 消费；R4/R10 独立反例 | R4 先建立新 Artifact 权威，R10 才能只读关联精确上下文；错误身份/角色或损坏 receipt 拒绝，不回退当前 catalog 猜测；R4/R10 |
| C17.b 项目/遥测/依赖 | `project.py`、`cli.py`、`telemetry/`、package metadata；`test_project.py`、`test_telemetry.py` | 秘密与主体键不泄露、可选依赖隔离；+ 离线基本 import，− 缺可选驱动导致核心 import 失败；R10 |
| C18.a Anchor 相对观察 | 当前无实现；仅接口设计 §8.2/理论 §8.5 | R0.3 已定 Event/Journey typed Anchor、elapsed/calendar、共享重叠及 K，见 §5；+ DST 对照，− 七日=168h；R7 |
| C18.b retention | 当前无实现；不可用 Event 绝对窗口代替 | R0.3 已定固定 Ω、两种单位/量词、K+/K-/K?、确定性界及 K，见 §5；+ 25/5/70→[25%,95%]，− 删未知单点率；R7 |

## 4. 横向资格键、证据和阶段出口

R0.4 的每个方法行按 `方法版本 × 数值类型 × 时间/来源形状 × 后端/表类型 × 路线` 展开；此键由 [R0.5 SQL/adapter 台账](2026-09-26-marivo-full-refactor-r0-sql-ledger.md#4-六后端目标资格矩阵)与六后端矩阵拥有。当前代码可见 DuckDB、PostgreSQL、MySQL、SQLite、Trino、ClickHouse engine/adapter；这只证明实现分支存在。旧 C3b Trino 不可达、C4 MySQL/Trino Decimal 限制、C5 非 Iceberg/Distributed、C6 fold 差异、C8/C9 领域限制和 C10 抽样/资源失败仍按 [R0.1 §4](2026-09-26-marivo-full-refactor-r0-evidence-index.md#4-c0c10-后端历史证据) 保持历史边界，**没有**新的 R1/R9 通过格。目标路线为基础 Ibis 来源、复杂方法预先获准 Ibis→Python、固定 Artifact→pandas；Artifact→DuckDB 目标删除。不得把生成 SQL、旧测试、当前 Help 或旧 wheel 当真实新 DSL 后端资格。

| 状态 | R0.2 判断 |
| --- | --- |
| 通过：现状定位 | C01–C18 的入口/缺入口、主要 owner、Help 群、执行/状态链和主要消费者已反查；J1/J3/`execute_j1` 与旧家族明确分列；领域、ontology、项目工具已列入 |
| 目标已登记：目标签名 | C18、普通 Relation ratio、StatisticalWeight/ReferenceWeights、业务顺序等目标输入及 K 见 §5；`count_defined`、time_grid 仍非当前导出 |
| 目标已登记：规则/模块 | 六类元算子、44 子单元的目标方法版本/K/owner 和消费者同迁见 §6；实现未验证 |
| 目标已登记：物理资格 | SQL 提交点、adapter 七职责、六后端/类型/表形态/路线矩阵见 R0.5 SQL 台账；真实环境资格未验证 |
| 阻塞：R0 整体 | R0.6 静态交接见 §7–§8；R0.5 控制 API/必需路线可行性、`md.connect` 公共旁路及资格格尚未闭合；不能把旧 J1–J4/C0–C10 的历史通过转授新 DSL |

复核本台账的静态命令：`git status --short --branch`、`git rev-parse HEAD`、`shasum -a 256 <五份输入>`、`rg -n '__all__|class J1Context|class J3Observed|def execute_j1' marivo/{datasource,semantic,analysis}`、`rg -n 'def (members|population|observe|match|replay|artifact|revalidate)' marivo/analysis/session`、`rg -n 'J1Context|J3Observed|LogicalPopulationDataset|raw_sql|parity_check' marivo tests devtools site/src/content/docs`、`git diff --check`。这些是事实扫描，不运行产品测试。目标正反例的旧独立 oracle 和精确历史复跑命令见 [R0.1 §5](2026-09-26-marivo-full-refactor-r0-evidence-index.md#5-可复用的独立-oracle故障与业务问题)；新目标测试命令须随 R0.4/R0.5 的规则和资格格落地，不以本页的历史文件名充数。

## 5. R0.3 接受的契约索引

下表索引唯一 owning spec，不重新定义目标语义。R0.3 是 **target accepted / runtime unverified**，不是代码已导出。差异由相应 R1–R8 工作包同时迁移 Help、测试和消费者。

| 单元 | 唯一目标定义；关键决定 | 独立拒绝反例 |
| --- | --- | --- |
| C18.a/b | [Analysis R0.3](../../specs/analysis/python-analysis-design.md#relative-anchor-observation-and-retention-c18)：Event occurrence 或 Journey 起点、`Subject×Anchor`、elapsed/calendar、共享重叠、固定 Ω、`any_anchor/every_anchor`、K+/K-/K?；R7 | DST 七日≠168 小时；100 人 25/5/70 的界为 [25%,95%]；不删未知 |
| C08.b | [Analysis 权重/参照](../../specs/analysis/python-analysis-design.md#statistical-and-reference-weights-c08) 与 [Semantic 角色](../../specs/semantic/semantic-object-model.md#named-statistical-weight-role)：独立声明统计角色，固定完整分层 Relation；R2/R6 | 订单数冒充统计权重、缺层重归一化、Top-K 后重算参照 |
| C07.c | [Analysis 普通 ratio](../../specs/analysis/python-analysis-design.md#ordinary-relation-ratio-c07)：精确同域或显式一一对应；零分母 Undefined，缺侧另存；R6 | 数值可除而域不对应、缺侧当零、普通比率叫 share |
| C02.c/C11/C13 | [Semantic 业务顺序](../../specs/semantic/semantic-object-model.md#business-order-and-simultaneous-events) 和 Analysis R0.3：声明业务序号/封闭冲突规则，或验证所有允许顺序的输出及部件等价；R2/R7 | 同刻 activate/deactivate 按 occurrence ID 排序得不同终态 |
| C03.b/C05/C10 | [Analysis 余下边界](../../specs/analysis/python-analysis-design.md#remaining-r03-boundaries)：精确版本、原状态与当前行、两种 count、合法空状态、distinct/quantile K；R5/R9 | 子组均值/比率平均冒充原量、Undefined 状态当零、子组 P95 上卷 |
| C01.c | [Datasource SQL 目标](../../specs/semantic/datasource-layer.md#r03-target-ibis-owned-analysis-reads-and-terminal-raw-sql)、[Semantic provenance](../../specs/semantic/semantic-object-model.md#provenance-and-parity)：`md.raw_sql` 保留为终端只读逃生通道；内部 Analysis 读取由 Ibis 构造，provenance 文本不执行；R1 | raw 结果/SQL 文字获得 Semantic/Analysis 资格、内部 `backend.sql` 绕过 Ibis、`ms.from_sql` 被执行 |

## 6. R0.4 六类元算子：规则冻结

规则形式统一为 `InputSignatures + Parameters → OutputSignature + Pre + RequiredParts + PartTransform + Post + Transport + Eval`。`D` 是有类型实例域，`R` 是单量 Relation，`P` 是保留部件；每个规则版本进入定义/执行身份。下表冻结规则责任，不将语义授权交给 adapter。

| 规则@版本 / owner | 输入、封闭参数→输出 | Pre；RequiredParts；PartTransform | Post；Transport；Eval |
| --- | --- | --- | --- |
| `bind_project@v1` / analysis core、relations（R3/R5） | `D×(Entity/Field/Metric Ref或封闭 RuntimeMetricExpr)×时间/路径绑定 → D或R` | 精确 Ref/版本/主体与单值路径；需定义、主体映射和来源/时间绑定；保留绑定、为 read/observe 新建值或组件，不继承未证明的原量状态 | 键和量属于本次绑定；运输声明/来源假设及未履行检查；对完整实例域逐键投影/观察 |
| `map_correspond@v1` / analysis core、relations（R3/R6） | `D/R×(ExactKeys、显式一一对应、UnionKeys、group key、主体映射) → D/R` | 键类型/单射/实际覆盖与目标域、时间角色；需全键、对应及 MissingCoordinate 区分；按确切对应运输端点/主体，丢失映射撤销 members K | 输出域、重数和缺侧有据；运输配对范围和覆盖前提；精确映射、并集或分组，不作隐式笛卡尔积 |
| `cell_derive@v1` / analysis core、relations（R3/R6） | `R×(封闭谓词、difference、ratio finish、状态视图) → R/BoundPredicate` | 方法自己的四 Cell 消费政策、单位/有限性；需实际端点和值状态；保留依赖端点及原因，派生新 Cell，不把 MissingCoordinate 写成 Null | Defined/Null/Undefined/Unknown 及原因准确；运输端点身份和未决义务；逐实例按方法数学式求值，不用短路豁免硬失败 |
| `row_state@v1` / analysis methods、relations（R3/R5/R6） | `R×(sum、mean、count、count_defined、weighted_mean等封闭方法) → R` | 当前行实例单位、Cell/权重政策；需当前值、状态、统计权重绑定；新建该统计的 sum/count 或 N/W，不借用原 Metric 组件 | 输出是新当前行量；运输当前输入域/权重依据；按当前行聚合，count 与 count_defined 分开 |
| `original_reduce@v1` / analysis methods、relations（R3/R5） | `R×(目标组、合法时间粗化、原方法版本) → R` | 贡献覆盖、互斥或已批准重叠、版本/顺序/空状态；需每侧原组件与必要坐标；逐组件合并后 finish，缺件撤销 rollup K | 保持原量定义与目标域；运输原声明、检查和状态；不能平均显示比率、P95 或 Undefined 当前值 |
| `parts_transport@v1` / analysis core、materialization（R3/R4） | `R×(where、projection、compare、具名视图、materialize) → R/Artifact` | 精确节点/receipt/键绑定；需方法 K 所列端点、主体、覆盖、固定参照；按选择键裁剪或原样携带，丢失部件同步缩小 K | 公开 K 仅含真实可续算；运输依据及仍相关假设；固定输入不回源、混合输入早拒绝 |

Event matching、Lifecycle replay、归因、排名、相关、预测各用独立 `method@v1` 规则；通用六规则只提供域、Cell、部件及执行设施。Semantic 声明与显式 builder 推导分别标依据来源。物理实现注册只声明特定方法/版本/类型/后端/路线及必要检查，不能改变上表 Eval。

### 6.1 方法子单元与 K、独立 oracle

以下 `K` 表示在指定 RequiredParts、域和版本都仍成立时的**目标**续算，不是当前产品能力。`L→M` 表示惰性构造与执行后同义视图；`read` 指有界读取，不授权 source 再求值。`O` 为计划中的独立预期生成方式；R0.1 §5 的旧 fixture 只供原始输入。每行的正反例在 §3，后端资格键在 [SQL 台账 §4](2026-09-26-marivo-full-refactor-r0-sql-ledger.md#4-六后端目标资格矩阵)。新目标测试尚未编写或运行。

| 单元 | 目标方法@版本；语义 owner / 阶段 | L→M 与目标 K（有条件） | O：独立预期 |
| --- | --- | --- | --- |
| C01.a | `datasource.connect@v1`；datasource adapters/R1 | typed Spec/Ref→连接结果；inspect/test，非分析 K | 六配置与错误权限手工事实 |
| C01.b | `source.bind@v1`；datasource adapters/R1 | typed Table/File/JSON→物理 relation；inspect/sample | 原文件/JSON 与 schema 清单 |
| C01.c | `raw_sql_terminal@v1`；datasource/R1 | `Ref[DatasourceKind] × SqlText × reason/limit/timeout → RawSqlResult`，无 Analysis L/M/K；保留公开导出/Help | 单条只读、截断/超时、typed reentry 拒绝及实际提交审计 |
| C02.a | `entity.resolve@v1`；semantic/R2 | 版本化 Ref→定义；exact at/before_end | 两版本复合键手算 |
| C02.b 静态 | `metric_graph@v1` 及 Relationship 映射；semantic/R2 | 声明/组件→规范图，关系完整 K 覆盖→结构基数；不产生匹配完整性证明 | 单位与贡献分解手算；相同键覆盖下空键/缺目标不改变静态基数 |
| C02.b 消费 | 使用 Relationship 路径的 Analysis 方法；analysis/R5 | 精确成员、版本及角色→必配或允许缺失语义；必配缺失/单值多重匹配拒绝 | 有界检查与完整来源结果分离；缺失、重复、版本反例 |
| C02.c | `event_order@v1`；semantic/R2 | Event/State/calendar/order Ref→定义；领域消费 | 同刻相反顺序轨迹 |
| C03.a | `members@v1`；analysis relations/R5 | Domain L→M；read/observe/group/each/members | 原始完整主体键集合 |
| C03.b | `members_at@v1`；analysis relations/R5 | 版本 Domain L→M；同 C03.a 且精确时点 | 快照/validity 手算 |
| C03.c | `read_select@v1`；analysis relations/R5 | Category/Numeric/Time/Boolean L→M；where/members（有映射） | 字段逐键表与多值反例 |
| C04.a | `observe_aggregate@v1`；analysis methods/R5 | Numeric L→M；group/summarize/合格 rollup | J1 原事实 SQL/手算 |
| C04.b | `observe_component_ratio@v1`；analysis methods/R5 | Ratio L→M；组件保留时 rollup | J3 Fraction 分子/分母 |
| C04.c | `runtime_graph@v1`；semantic+analysis methods/R2/R5 | Numeric L→M；依组件规则收紧 | 分支逐项贡献手算 |
| C05.a | `coordinate_group@v1`；analysis relations/R5 | Grouped L→M；summarize/合格 rollup | 全元组并集与空目标手算 |
| C05.b | `row_statistic@v1`；analysis methods/R5 | Statistic L→M；where/再做当前行统计 | 当前行 50.5、四 Cell 表 |
| C05.c | `state_rollup@v1`；analysis methods/R5 | Rolled L→M；保留组件时继续 rollup | 200/101 与空状态手算 |
| C06.a | `time_grid@v1`；analysis relations/R5 | Time Domain L→M；each/group/rollup（合格） | DST 日历边界时钟表 |
| C06.b | `period_fold@v1`；semantic+analysis methods/R2/R5 | Temporal L→M；登记的时间归约 | 认证期间/累计端点手算 |
| C07.a | `compare@v1`；analysis methods/R6 | Difference L→M；where/members/当前行统计 | J2 Fraction、完整键集合 |
| C07.b | `where_members@v1`；analysis relations/R6 | Selected L→M；members/固定续算（有映射） | {A,C} 与 Unknown 真值表 |
| C07.c | `relation_ratio@v1`；analysis methods/R6 | Numeric L→M；where/当前行统计，无原量 rollup | 逐键分数、缺侧/零分母 |
| C08.a | `cohort_share@v1`；analysis methods/R6 | Domain/Share L→M；获准成员/固定参照视图 | 完整机会域三值表 |
| C08.b | `weighted_standardize@v1`；semantic+analysis methods/R2/R6 | Numeric L→M；当前行选择/统计，不原量 rollup | 分层权重 Fraction 与缺层 |
| C08.c | `rank_limit_table@v1`；analysis methods/R6 | Ranking/Table L→M；具名值/名次筛选，table 终端 | 并列排名排序手算 |
| C09.a | `attribute_additive_component@v1`；analysis methods/R6 | Attribution L→M；具名贡献/核对视图 | 守恒、残差及覆盖分别计算 |
| C09.b | `attribute_resolution@v1`；analysis methods/R6 | joint/hierarchy L→M；共同 Top-K→Other 后视图 | 两侧共同基准重算 |
| C10.a | `exact_distinct@v1`；analysis methods/R5/R9 | Numeric L→M；当前行统计，无原量 rollup/归因 | 原始身份集合去重 |
| C10.b | `quantile@v1`；analysis methods/R5/R9 | Numeric L→M；当前行统计，无原量 rollup/归因 | 独立排序/线性插值 |
| C11.a | `event_match@v1`；analysis methods/R7 | Journey L→M；具名步骤/完成视图 | 原 occurrence 逐条匹配 |
| C11.b | `funnel_compare_attribute@v1`；analysis methods/R7 | Funnel L→M；合格比较/归因/selector | 全随访机会计数 |
| C12.a | `time_to_event@v1`；analysis methods/R7 | Duration L→M；完成/观察时长视图、统计 | 时间差与删失表 |
| C12.b | `journey_subjects@v1`；analysis methods/R7 | Domain L→M；read/observe（有主体映射） | Journey→Subject 集合像 |
| C13.a | `lifecycle_replay@v1`；analysis methods/R7 | History L→M；轨迹/区间/违规视图 | 有序状态机纸面重放 |
| C13.b | `lifecycle_views@v1`；analysis methods/R7 | Distribution/Dwell L→M；at-read/where/members（有映射） | 区间裁剪与状态轨迹 |
| C14.a | `discover.{point_anomalies,interesting_windows,entity_outliers,period_shifts,driver_axes}@v1`；analysis methods/R8 | 五种 Candidate L→M；各具名分数/排名 | 独立定义逐候选计算 |
| C14.b | `correlate.{pearson,spearman,kendall,lag}@v1`；analysis methods/R8 | Association L→M；系数/配对数/排名，不原量 rollup | J4 平均秩 Fraction、常量反例 |
| C14.c | `forecast.{naive,drift,seasonal_naive}@v1`；analysis methods/R8 | Forecast L→M；点/区间具名视图 | 固定序列逐期手算 |
| C15.a | `execute@v1`；analysis session/R4 | L→M；同显式节点共享/失败不回退 | Run/读取次数故障注入 |
| C15.b | `publish_recover@v1`；analysis materialization/session/R4 | Artifact→固定 K；精确 receipt/parts | 断源恢复与损坏注入 |
| C16.a | `disclose@v1`；analysis core/R3–R10 | repr/show/contract/Help；只呈实际 K | 导出/Help/动态 K 独立枚举 |
| C16.b | `agent_journeys@v1`；analysis session/R10 | 安装包完整用户旅程；终端结果 | 新 wheel 独立 oracle/真实轨迹 |
| C17.a Ref | `ontology_handoff@v1`；semantic/R2 | 当前 catalog 的精确 Ref 与双指纹关联；无新分析权限 | 错端点与错误 Ref kind |
| C17.a Artifact | `ontology_handoff@v1` 的 Artifact 侧；Runtime/R4、ontology 消费/R10 | 新 Artifact 身份、receipt、语义依赖与 ontology 上下文只读关联；无新分析权限 | 错误角色、身份不符、损坏 receipt、过期上下文 |
| C17.b | `project_tools@v1`；项目工具/R10 | 管理/诊断；无分析 K | 无可选驱动导入与脱敏 |
| C18.a | `relative_anchor@v1`；analysis methods/R7 | Anchor Domain/Relative Relation L→M；实例视图/固定续算 | DST、重叠窗口逐事件表 |
| C18.b | `retention@v1`；analysis methods/R7 | Instance/Subject Retention L→M；状态视图/合格选人 | 25/5/70 界与量词真值表 |

组合单元中的方法仍分别注册和准入，不能把同行写法当成一个万能 reducer。以下补齐各自的语义前提、最小部件和续算差异；未列的共同条件沿用上表和六元规则。

| 方法@版本 / 子单元 | 独立前提与 RequiredParts；目标 K | 独立 oracle/拒绝 |
| --- | --- | --- |
| `runtime.aggregate@v1` / C04.c | Measure Ref、agg/时间 fold 的闭合组合；贡献根/值政策；仅已证原状态 rollup | 原始行分组；未经登记 fold 拒绝 |
| `runtime.weighted_mean@v1` / C04.c | 两 Measure Ref、共同贡献单位；N/W；先合并 N/W 再 finish | 分数 N/W；只平均组值拒绝 |
| `runtime.slice@v1` / C04.c | exact 字段 Ref 和闭合 SliceValue；保留分支身份；K 跟被切 Metric 状态收紧 | 每分支手算；裸 SQL/任意 callback 拒绝 |
| `runtime.ratio@v1` / C04.c | 两组件同一次 observe 绑定；num/den、零政策；合格组件 rollup | J3 Fraction；普通 Relation ratio 混用拒绝 |
| `runtime.linear@v1` / C04.c | 有序 ±1 项、同单位；每侧组件；合格组件 rollup | 各项手算；跨单位拒绝 |
| `cohort@v1` / C08.a | 完整机会域、主体映射、三值谓词；已决定真值才可 members | t/u/f 手算；缺机会冒 unknown 拒绝 |
| `share_of@v1` / C08.a | 同一计量的固定 Singleton 参照、支持包含；保留分母；筛选不重算 | 份额分子/分母；Top-K 重算拒绝 |
| `penetration_in@v1` / C08.a | 身份相容的 B/Ω、固定完整 Ω；保留成员集合；空 Ω Undefined | 集合交 Fraction；普通数字 ratio 冒充拒绝 |
| `weighted_mean@v1` / C08.b | named StatisticalWeight 与精确实例对应；N/W；只当前行 K | 分层 Fraction；订单数冒充拒绝 |
| `standardize@v1` / C08.b | 固定完整 ReferenceWeights 与 strata；参考权重+原值；不原量 rollup | ∑wq 手算；缺层归一拒绝 |
| `attribute.additive@v1` / C09.a | 两侧可加原量、完整 resolution；端点/覆盖/贡献；具名侧项 K | 差值守恒+覆盖双检 |
| `attribute.component_mix@v1` / C09.a | 两侧可加 N/W、非零总 W；侧项和完整 axis；具名贡献 K | 每侧 N_i/W_total；子组率均值拒绝 |
| `attribute.joint@v1` / C09.b | 完整轴元组与共同 Top-K→Other；保留 basis；视图筛选不改 basis | 两端同一轴元组手算 |
| `attribute.hierarchy@v1` / C09.b | 作者轴顺序的各前缀；各 resolution 独立；视图筛选不改 basis | 前缀逐级核对；混层相加拒绝 |
| `median@v1` / C10.b | 原始定义数值序列、exact 排序；无原量 rollup/归因 | 奇偶排序中位数；子组 median 平均拒绝 |
| `percentile@v1` / C10.b | q∈(0,1)、精确算法/数值；无原量 rollup/归因 | 独立线性插值；近似冒精确拒绝 |
| `event.first_per_subject@v1` / C11.a | 首次起点、exact Subject/occurrence、覆盖；保留 assignment | 逐 occurrence 匹配；未访问者自动失败拒绝 |
| `event.every_start@v1` / C11.a | 每起点独立实例、显式 final completion assignment；保留重数 | shared/exclusive 分配反例 |
| `event.funnel@v1` / C11.b | 完整开始机会、随访与步骤；保留到达/覆盖；合格比较 | 各步骤人数与 unknown 分列 |
| `event.compare@v1` / C11.b | exact pattern/随访定义及期间对应；两端状态；差异视图 | 两期间独立手算；不同随访拒绝 |
| `event.attribute@v1` / C11.b | 完整漏斗侧项、绑定目标与 basis；具名贡献 | 两侧配比；residual=0 冒完整拒绝 |
| `lifecycle.distribution@v1` / C13.b | 指定时点的 canonical 状态/覆盖；保留 Subject 映射 | 手工时点状态；终态冒历史拒绝 |
| `lifecycle.dwell@v1` / C13.b | 裁剪后区间与完成/删失政策；时长组件；仅有状态时 rollup | 区间时长总和；P90 平均拒绝 |
| `lifecycle.transitions/violations@v1` / C13.b | canonical 轨迹和事件身份；具名视图/合格选人 | 状态机轨迹；仅 intervals 反推拒绝 |
| `discover.point_anomalies@v1` / C14.a | 已定义点与对照窗口；Candidate score K | 固定序列逐点 oracle |
| `discover.interesting_windows@v1` / C14.a | 完整时间格、闭合窗口；Candidate score K | 全窗口枚举；缺桶拒绝 |
| `discover.entity_outliers@v1` / C14.a | 可比 Entity 量/域、分布政策；Candidate score K | 逐主体分位/距离 oracle |
| `discover.period_shifts@v1` / C14.a | 合格期间配对、差异政策；Candidate score K | 两期逐键变化 oracle |
| `discover.driver_axes@v1` / C14.a | 可加目标、有限 search_space/axis 资格；Candidate score K | 枚举候选集中度；残差冒解释拒绝 |
| `correlate.pearson@v1` / C14.b | 完整有序配对、非零方差；系数+pair count K | Fraction 协方差；常量输入拒绝 |
| `correlate.spearman@v1` / C14.b | 完整配对与平均秩；系数+pair count K | J4 -2/5、并列秩 7/9 |
| `correlate.kendall@v1` / C14.b | 完整配对与 tie 政策；系数+pair count K | 独立 concordant/discordant 表 |
| `correlate.lag@v1` / C14.b | +k 绑定左 t/右 t+k、完整时间格；各 lag 独立 pair count | 逐 lag 配对表；反转方向拒绝 |
| `forecast.naive@v1` / C14.c | 完整历史/未来格与起点；点预测视图 | 最后值逐期延续 |
| `forecast.drift@v1` / C14.c | 两有效端点/间隔；点预测视图 | 端点斜率逐期手算 |
| `forecast.seasonal_naive@v1` / C14.c | 完整季节周期/未来格；点预测视图 | 上季同位复制；缺季节拒绝 |
| `anchor.relative_observe@v1` / C18.a | Event/Journey exact 起点、typed elapsed/calendar、共享重叠及来源覆盖；实例关系固定续算 | DST/重叠逐 occurrence 表 |
| `retention.instance@v1` / C18.b | 固定 Subject×Anchor Ω、三态/覆盖；状态视图，不 bounds rollup | 25/5/70 确定性界 |
| `retention.any_anchor/every_anchor@v1` / C18.b | 显式 Subject 像与量词；主体三态/覆盖；合格真值选人 | 一真一未知、一假一未知反例 |

### 6.2 模块责任、消费者同迁与类型方向

| 当前实现和主要消费者 | 唯一目标责任；同迁/删除阶段 |
| --- | --- |
| `datasource/engines/*`、`backends.py`、metadata/inspection、`manage.py`；Semantic loader/CLI/Help | datasource adapters 负责连接、物理事实、传输及资源；R1 保留并验证 `md.raw_sql`、`RawSqlResult`、Help/CLI/测试的终端边界，移除内部读取对任意 SQL 的依赖；Semantic 只消费 typed 物理事实 |
| `semantic/{ir,resolver,metric_graph*,event,state_model,parity}.py`；Ref/ontology/Analysis binding | semantic 负责声明和显式 builder 推导；R2 同迁 ontology/Ref/Help；R1 删除执行 SQL 的 parity，不留转发 alias |
| `analysis/{datasets,observation,operators,domains,public_dsl}.py`；旧 Dataset 与新 J1–J4 Help、site、测试 | core 仅有域/Cell/部件/规则，relations 构造 L/M，methods 拥有领域 Eval；R3 建规则，R5–R8 随各方法和消费者迁移删除旧家族/registry/codec，不整包预删领域语义 |
| `analysis/compiler/*`、placement/source_admission/各 support；`materialization/source_stage.py` | compiler 将单一方法规则降至 Ibis 或预批准本地阶段；R3–R8 随消费者迁移，R9 清除 SQL 拼装/补丁；资格只在 adapter 实现注册判断一次 |
| `materialization/{execution,scalar_sql_execution,*_execution,source_preparation,parquet_scan}.py`；`EngineProfile` | datasource adapters 接管来源传输/解码/资源；R1/R4/R9 删除旧 `statement(sql)`、Artifact→DuckDB 和重复 profile 分支；core 不依赖 backend 名称 |
| `materialization/{admission,dataset_execution,dsl_j1_runtime,store,*_codec,*_publication,reads}.py`、`session/*`；CLI/evidence/ontology | session 拥有唯一 Run/Artifact/Store；materialization 拥有交换/receipt/固定续算；R4 同迁旧 Dataset/J1 双执行协议，R10 收束遗留 Help/状态；Store SQLite 仍独立事务 owner |

类型依赖方向为 `semantic Ref/definition → analysis core/method contract → relations/graph → compiler → datasource adapter`；`session → graph/compiler/adapter + materialization/Store`。adapter 不导入 Analysis 公共对象或 Store；Store 不决定方法语义。上表只冻结责任及删除时机，不预建空包或把旧名字改为 shim。

### R3.2 connected registration handoff (2026-09-27)

The R3 private core now resolves its six rules through `analysis/methods`.
Concrete `cell.difference@v1`, `cell.ratio@v1` and the five `row.*@v1`
methods retain their R3.1 quantity identities; `state_rollup@v1` is the
connected operation over the original `sum@v1` state. This is a private
construction/qualification handoff for C04/C05/C07, not completion of their
R5/R6 public methods. Original-state method identity is not the rollup operation
identity, and neither is a J1 journey ID.

The migrated consumers are `core.rules.derive`, current-row state component
construction and original-state component admission. Their duplicate dispatch
and component inventories were removed in favor of the new semantic owner.
No legacy J1/operator/Dataset execution method is migrated or requalified by
this increment. Those consumers remain assigned to R4-R8 above; the new
registry rejects their IDs and never consults their registries. There is no
compatibility alias or route that inherits historical backend qualification.
The active J1 current-row sum/count/mean, rollup and difference registrations
still overlap these private method semantics. The cross-path single-owner
deliverable in the R3.2 plan remains open until their R5/R6 consumers migrate;
private registry isolation alone does not satisfy it.

All connected production physical cells remain blocked until the graph,
lowering and execution consumers are connected and the exact key is qualified.
Unit tests cover precise method/type/time/source/backend/table/route matching,
input position and arity, numeric precision compatibility, static checker/part
completeness and invocation-bound obligations. They do not prove actual source
types, checks, resources, publication or numerical execution.
See the R3.2 section of the acceptance record for commands and measured status.

## 7. R0.6 breaking changes

本章是目标切换清单，不表示旧导出、SQL 或协议已经删除。静态反查锚点为 `panda` HEAD
`784135090b98c32b60d4b8f8cf69c02982d883d5`；R0.2 的 34/108/323 Help target 数和
44 个子单元是其原快照，不能冒充本 HEAD 的重新计数。每行以 §2–§3 的能力、§6.2 的
模块 owner 和 [R0.5 DS/AN 行](2026-09-26-marivo-full-refactor-r0-sql-ledger.md)为索引。
“删除”指目标切换后不可导入、不可经 Help 重定向，旧状态也不升级或双读；实施阶段必须
同时迁移本表的消费者。下面的 C 组在 §7.1 展开到 Help、CLI、site、测试。

| ID；能力 | 旧使用处与本 HEAD 状态 | 目标入口或结构化拒绝；owner/阶段 | 消费者组、验收定位 |
| --- | --- | --- | --- |
| B01；C03–C14 | `session/core.py:population/observe`、`session/_lazy_sources.py`、`analysis/datasets/*` 的 Population/Metric/Delta/Event/Lifecycle/Candidate/Forecast Dataset 家族仍可调用；`analysis/__init__.py` 仍导出旧类 | `session.members(EntityRef, at=...)` 起始 AnalysisDomain，随后 typed `read/observe` 与领域结果；按 §3 子单元迁 R5–R8，旧构造、旧 `Dataset` 类型及同义动作删除；未迁路线在 Run 前给结构化 `source_admission` 拒绝，不能转发到旧实现 | C-A/C-B/C-D；§2 C03–C14、§6.2、[主计划 R5–R8](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md#r5--完整成员观察坐标与数值归约) |
| B02；C04/C07/C14/C15 | `observation/dsl_j1.py:J1Context/J3Observed`、`public_dsl.py` 的窄 J1–J4 路线和 `DatasetRuntime.execute_j1` 仍在；旧 devtools 将场景名当执行入口 | J1–J4 只保留为验收旅程 ID；新方法版本、统一 graph/Run/Artifact 取代场景专属节点、receipt、执行与恢复。R3/R4 建内核和 Runtime，R5/R6/R8 迁方法，R10 清公开发现面；不留 J1 shim | C-A/C-B/C-D；§6.1 C04/C07/C14/C15、R0.1 §3 独立 oracle |
| B03；C05–C14 | `observation/metric.py` 的 `.aggregate/.with_time_axis/.compare/.correlate/.forecast/.discover` 与各 Dataset `.where/.rank/.limit` 由旧 registry/Help 披露 | 分别以 `group_by(...).summarize(method)`、有条件 `.rollup()`、`time_grid/each`、Relation/领域具名方法替换；无原部件的续算结构化拒绝，禁止把旧方法名留作 alias。relations/methods R5–R8，R10 删除旧公开符号 | C-A/C-B/C-D；§3 C05–C14、§6.1 方法行 |
| B04；C16 | `analysis/_capabilities/{registry,dataset_registry,dataset_navigation,dataset_model,catalog_inputs}.py` 和 `datasets/registry.py` 与 `public_dsl` Help/`dsl.*` 并存；`tests/test_public_surface.py` 固定旧 `__all__` | 每能力只保留一个公开目标、一个 native Help owner 和实际可达的 L/M/K；不提供旧 target 重定向或 shadow registry。随能力迁移更新 Help/docstring、reachability/drift/budget/`__all__`，R10 清旧群 | C-A/C-B/C-D；§2 C16、§6.2、[主计划 R10](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md#r10--公共契约收口安装包与完整交付验收) |
| B05；C15 | `materialization/{admission,dataset_execution,dsl_j1_runtime,*_codec,*_publication}.py` 和 `store.py` 仍承载 Dataset 与 J1 两套构造、codec、receipt；`session/core.py:artifact/runs/revalidate` 消费 | 唯一 graph→Run→Artifact、主表/parts/receipt、Store 原子提交及冷恢复；R4 同迁两链及全部恢复消费者，R10 删除残余旧 codec/协议。损坏、不同身份或未确认提交必须拒绝，不重放 | C-B/C-C/C-D；§6.1 C15、R0.1 §5 故障注入 |
| B06；C15 | `dataset_execution.py` 仍有 `execution_key(dataset.definition_fingerprint)`；旧 definition-only 来源命中会把同定义当可复用计算 | R4 将来源新求值绑定有效 execution key、Run 和实际来源身份；仅已提交固定 Artifact 的精确命中可复用。来源变更、定义同而读取不同或 mixed 图在求值/发布前拒绝；删除 definition-only cache | C-B/C-C/C-D；[主计划 R4](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md#r4--统一-runtime交换与存储吸收-mvp)、`tests/test_analysis_dsl_execution_identity.py` |
| B07；C15 | `materialization/{parquet_scan,source_preparation,inspection,duckdb_execution}.py` 仍把固定 Artifact receipt 接到 DuckDB `read_parquet`/临时视图；J1 固定续算另有独立路径 | 固定结果只由 receipt-bound Parquet→Arrow→pandas 的注册本地方法读取，主表与 parts 同样核对；R4 同迁固定续算及资源/断源消费者，删 Artifact→DuckDB。没有精确 receipt/部件时拒绝，不能读当前来源替补 | C-B/C-C/C-D；§6.1 C15、[SQL 台账 AN 行](2026-09-26-marivo-full-refactor-r0-sql-ledger.md#2-analysis-sql-构造与提交链) |
| B08；C15 | `store.py` 当前只接受 `user_version=6` 并拒绝其他版本；旧 Dataset/J1 reader、codec、状态仍在，同一项目里的旧 Session 不能凭名字获得新协议身份 | 新协议只接受其精确 Store/receipt 版本；旧 Store 原样保留，打开旧状态给结构化版本错误和新 Session 修复，不做双读、升级、回填或重算。R4 定义新格式和读前拒绝，R10 检查无旧 reader | C-C/C-D；`tests/test_analysis_dsl_exchange.py`、`test_lazy_materialization_store.py`；当前版本拒绝不等于新协议完成 |
| B09；C01/C04–C15 | `materialization/{scalar_sql_execution,*_execution}.py` 的 `statement(sql)`、`compiler/{lifecycle,driver_numeric,...}.py` 的 SQL 模板/补丁及部分 metadata/控制路径仍可见；R1.1 已阻断多数旧来源 Run | 受治理读取/校验只由绑定 Ibis 表达式及原样编译产物，复杂方法可在执行前准入 Ibis→Python；R1 迁基础来源，R5–R8 迁方法，R9 逐 DS01–DS21/AN01–AN33 清零并实测。无合格路线即精确格阻塞；不允许失败后旧 SQL fallback。`md.raw_sql` 仅是 C01.c 终端例外 | C-A/C-B/C-D；R0.5 §1–§5、R1.4 主验收 DS/AN 格 |
| B10；C01/C02 | R0.2 快照的 `ms.from_sql`/`SqlProvenance`/执行 provenance 的 `parity_check` 已在 R1.3 删除；历史 site 版本和旧契约仍可检索 | 当前目标以 `ai_context` 记录历史说明、用独立业务来源或受治理 Ibis oracle 在声明外验证；SQL 文本无执行/准入权限，不恢复 parity Help 或兼容 alias。semantic/R1.3 已实施，R10 再扫打包/历史与当前版本边界 | C-A/C-D；[Semantic owner](../../specs/semantic/semantic-object-model.md#historical-sql-context-and-verification)、R1.3 主验收记录 |
| B11；C10 | 旧 distinct/quantile Dataset 的原量 rollup、`distinct_membership`/`distribution_shapley` 等续算仍有实现/披露；部分路径受来源准入阻断 | exact distinct/quantile 可直接观察，但首次只允许有当前行依据的统计/筛选；原量 rollup 和对应归因以部件不足结构化拒绝，除非另立可验证规则。methods R5，精确类型/后端 R9，R10 删除误导 Help | C-A/C-B/C-D；§5、§6.1 C10，独立原始身份集合/排序 oracle |
| B12；C02/C03/C08/C11–C13/C18 | 旧调用允许或暗示不完整身份键、模糊版本、隐式权重/参照、occurrence ID 排序与动态列回灌；C18 现无公开构造 | 按 owning specs 要求完整 K、精确 `at/before_end`、显式 `StatisticalWeight/ReferenceWeights`、可证业务顺序、typed Anchor 和固定 Ω；无依据输入结构化拒绝，不提供任意回调/字典兼容。semantic R2 声明、R5/R6/R7 消费；C08 权重声明仍为 deferred target | C-A/C-B/C-D；§5、[Analysis owner](../../specs/analysis/python-analysis-design.md#r03-accepted-full-algebra-target-inactive)、[Semantic owner](../../specs/semantic/semantic-object-model.md#r03-full-algebra-target-decisions-inactive) |
| B13；C01.c/C16 | R1.5 已删除公开 `md.connect`、`DatasourceCatalog.connect`、`DatasourceConnection` 与对应 Help；当前无公开 backend-returning 连接对象 | [Datasource owner](../../specs/semantic/datasource-layer.md#r06-public-connection-cutover-target) 的 R1 目标已在公共面实施：内部连接仅属 adapter；连通性用 `md.test`，物理事实用 `md.inspect`，自定义 SQL 用终端 `md.raw_sql`。唯一入口的公共旁路子格已关闭，C01.c 仍受 MySQL/ClickHouse timeout 等物理格阻塞 | C-A/C-D；`tests/test_datasource_live_registry.py`、`test_datasource_live_help.py`、`test_public_surface.py`、R1.5 主验收 C01.c/DS02 |

### 7.1 同迁消费者与披露门禁

| 组 | Help/CLI/site/测试及处理 |
| --- | --- |
| C-A 公开入口 | Help：三层 `_capabilities/` 的精确旧 target 与 `__all__`；CLI：`marivo doctor --datasource ... --connect` 仍走 Datasource 诊断，`marivo init` 安装 packaged skills，CLI 无独立 Analysis Dataset 子命令；site：`site/src/content/docs/{docs,zh-cn/docs}/latest/` 的 `first-analysis`、`concepts/analysis-workflow`、`concepts/semantic-layer` 和 datasource 说明，中英文同时改；测试：`test_public_surface.py`、`test_agent_api_drift.py`、`test_unified_help.py` 与相应入口测试。历史 versioned site 仅作为旧版本记录，不静默改写 |
| C-B 方法/执行 | Help：`analysis.datasets/*`、`analysis.dsl.*`、`analysis.methods/*` 的方法与 K；CLI：无独立方法命令，核对 doctor/打包导入；site：latest `first-analysis`、`analysis-workflow` 及业务问题示例；测试：`test_analysis_dsl_public.py`、`test_analysis_dsl_fixtures.py`、`test_lazy_*`、领域/数值独立 oracle；`devtools/analysis_dsl_s4_p3/{j1,j2,j3,j4,recover}.py` 仅是历史复跑入口，须在新 wheel 重新实现旅程 |
| C-C Store/Artifact | Help：`analysis.runtime.*`、`analysis.artifacts/evidence` 与结果 `.contract()`；CLI：无公开 Store 迁移命令，不新增自动迁移；site：latest `concepts/evidence`、`analysis-workflow`；测试：`test_analysis_dsl_exchange.py`、`test_analysis_dsl_execution_identity.py`、`test_lazy_materialization_store.py`、断源/损坏/并发 Runtime worker。旧 receipt/Store 测试不能变为新协议预期 |
| C-D 最终发现面 | Help：native target、动态 `repr/show/contract` 与错误 repair 同源；CLI：`marivo init/doctor`、安装产物中导入与秘密披露；site：latest 中英文及 API 生成页；测试：reachability/drift/budget、`__all__` 快照、包安装与真实 Agent。packaged `marivo-semantic`/`marivo-analysis` skill 若需同步，先取得 AGENTS.md 要求的明确用户批准；本 R0.6 不编辑 skill |

### 7.2 当前处置与禁止推断

B10 的 R1.3 删除、B13 的 R1.5 公共旁路删除和 B08 的现存 Store 版本拒绝是已观察现状，其余多数 B 行是目标/待迁移。
R1/R2 的局部通过不能把 B01–B09、B11–B12 或整体 C01 标为
删除完成。旧 J1–J4、C0–C10、S4 wheel/Agent 记录只归 [R0.1 历史索引](2026-09-26-marivo-full-refactor-r0-evidence-index.md)；
新目标测试文件 `tests/test_full_algebra_contracts.py` 与 `tests/test_full_algebra_backend_matrix.py`
尚不存在，R0.5 写出的示例命令是待实施验收索引，不能列为已运行。

## 8. R0.6 R1/R2 handoff

冻结目标按 §2 C01–C18、§5 owning spec 决定、§6.1 方法版本/K 和 R0.5 §4 的
`方法 × 数值类型 × 时间/来源形状 × 后端/表类型 × 路线` 键读取；本节只指定第一批
owner/消费者/反例与实际状态，不另造第二份资格矩阵。R1/R2 已先行实施的条目按
[主验收](2026-09-26-marivo-full-refactor-acceptance.md)逐格读取，不能因本交接补文档而升级。

| 接收包 | 冻结目标、接口责任与首批格 | 同迁消费者、必须保留的独立正反例 | 当前交接状态与恢复条件 |
| --- | --- | --- | --- |
| R1 C01.a/b | datasource adapter 七职责，`SourceSession` 接 typed Table/File/JSON、物理 schema、Ibis 表达身份、固定 Arrow schema 与资源；首批 DuckDB table/view/file/HTTP JSON、SQLite main table/view，再逐 PostgreSQL/MySQL/Trino/ClickHouse 表形态和 `base-int64/base-float64`，目标矩阵仍有 Decimal/时间/复合键 | `md.inspect/sample/test`、Semantic preview/source-health、基础成员和 sum/count；重复/Null 复合身份、空流 schema、无效日期/Decimal/时间精度、远端取消；R0.1 原始事实与 R1.2 定向输入分别保留 | 本地及部分远端基础读取有有界证据；DS03–DS09 丰富 metadata、MySQL 复合键、远端终止、认证 HTTP/时区/timeout 格仍阻塞或未验证。完整资格须按 R0.5 §4 每格实源重验 |
| R1 C01.c 与 SQL | `md.raw_sql` 终端保留，B13 公开 backend-returning 连接目标已删除；Ibis 受治理读取与 Store SQLite 事务分权，内部 SQL 例外为空。DS01–DS21/AN01–AN33 按 R1/R5–R9 owner 和删除时点逐行交接 | Datasource Help/CLI/双语 site、Semantic 历史 SQL 说明与独立 oracle；伪造编译句柄、原样提交、终端 typed reentry、timeout/权限/秘密负例 | R1.3 已删 provenance parity 路线并完成部分后端控制；R1.5 已封闭 `md.connect` 与 catalog 公共旁路。MySQL/ClickHouse timeout、DS15 认证、旧 AN 文本路由仍未闭合。R1 不得标通过，R9 再扫实际提交 |
| R2 C02.a/b/c、C17.a | semantic 拥有完整身份 K、精确版本、变量/单位、规范 Metric 图、结构 Relationship 与业务顺序/日历定义；ontology 只关联精确 Ref。R5 才验证选定成员的关系匹配；R4 建 Artifact 身份，R10 才读其 ontology 上下文 | `ms.load/catalog.require/scoped readiness`、Analysis Ref binding 与 ontology；两版复合键、缺失/重复/fanout、同刻相反顺序、单位/组件手算，错 Ref kind/过期上下文拒绝；不把静态定义当来源完整性 | R2.1–R2.4 与随后 C02.b/C17.a 静态收口有定向证据；C02.b 的实际来源匹配、C17.a 新 Artifact 侧与 R2 整体仍未通过。权重角色仅是 deferred target，未公开 |

R1/R2 之外的首个依赖交接是 R3 的唯一规则/graph、R4 的统一 Run/Artifact/Store 和
R5 的成员/数值方法；它们不得复用 B02/B05 的场景协议或 B06 的 definition-only 命中。
R7 保留 Event/Lifecycle 领域独立方法及 C18 的 DST、重叠与 25/5/70 三态反例；R8 保留
相关的完整配对与预测未来格反例。R10 才能以新 wheel、独立 oracle 和真实 Agent 旅程
确认最终公开面。C01–C18 的旧测试、旧 Help、历史 site、生成 SQL 与静态扫描均不足以
替代上述运行/安装/Agent 资格。
