# C1：六后端统一列依赖契约实施计划

日期：2026-09-16。状态：经用户批准后已实施并验收；结果及实际接口说明见 [C1 验收](2026-09-16-multisource-capability-c1-acceptance.md)。

依据：[C0 实施清单](2026-09-16-multisource-capability-c0-implementation-plan.md)、[C0 基线验收](2026-09-16-multisource-capability-c0-acceptance.md)、[总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)。

## 1. 范围与基线

代码基线为 `7ddbe940de5c379f6944182114a7278ed61ef368`。开始时已有两份总计划修改及两份未跟踪 C0 文档；它们属于前序工作，本阶段不覆盖、不提交。下文保留实施前的计划范围；已交付结果、实际文件与命令退出状态以 C1 验收为准。

C1 覆盖 DuckDB、PostgreSQL、MySQL、SQLite、Trino、ClickHouse 的声明表源。改变执行所需列的确定、schema 请求、类型诊断及源投影，并补齐列 comment 的检视保真要求和下述 PostgreSQL 采集缺口；每个后端的类型、方法、物理表白名单沿用 C0 当前矩阵。必要类型受原有限制，无关类型不阻止计算。

五个远程/SQL 后端覆盖当前已支持的 Population、过滤、直接列 sum/count/min/max/mean/weighted mean/ratio、Metric slice、关系坐标、原生 date 分桶及既有 snapshot/validity 子集。DuckDB 还须保留当前复杂类型、时间、私有状态、sampling、Event/Lifecycle、Candidate、JSON/文件和 retained 行为。

排除 C2 新类型、C3 时间扩展与执行记录改造、C4 新表达式执行、C5 新表形态、远程写入/上传、公共 API/Store 格式变更、依赖升级、wheel/发布、提交和推送。表达式的依赖可解析不代表该表达式获得新的执行准入。

## 2. 已核实的改动入口

| 现有拥有者 | 当前问题与本阶段职责 |
| --- | --- |
| `compiler/normalize.py::required_entities` | 只产出 Entity 闭包；扩展为统一依赖分析入口，保留现有 retained-origin 排除规则 |
| `operators/scalar_support.py::unsupported_reason` | 对全部 `entity.columns` 做类型及 generic Decimal 检查；改为使用必要列，保留方法、表达式和结果类型检查 |
| `compiler/lowering.py::_Compiler.__init__` | 强制输入列与全部 Entity 声明列相等；须校验统一依赖指定的有序列，而不是简单删除此守卫 |
| `materialization/admission.py::_declared_table`、`DatasetRuntime._validate_source_schema` | 全量构造/校验声明列；按同一依赖投影和校验，包括 generic Decimal 的提前元数据解析 |
| `materialization/execution.py::ExecutionAdapter.get_schema` | 增加显式、带关系身份的 schema 请求；不用 adapter 环境状态猜测依赖 |
| `materialization/scalar_sql_execution.py::ScalarExecutionAdapter.prepare_dataset` | 删除全局 `_declared_columns` 的生成与缓存，保留本后端准入职责 |
| 六个 `materialization/*_execution.py::get_schema` | 四个共享后端及 PostgreSQL、DuckDB 独立实现均接收请求；先排除无关列，再解析必要列类型 |
| `semantic/_expression_binding.py::ExpressionBody`、`_physical_source_columns` | 现有 `source_columns` 没有参数位置，不能作为多 Entity 依赖的完整证明；表达式语法事实继续由此 owner 提供 |

本节未带前缀的 compiler/operators/materialization 路径均位于 `marivo/analysis/`。

对齐的现有契约为 [Analysis](../../specs/analysis/python-analysis-design.md)、[Semantic 对象模型](../../specs/semantic/semantic-object-model.md)、[loading/validation](../../specs/semantic/loading-validation-introspection.md)。上游仍检查完整声明的合法性及直接引用是否已声明；C1 不把执行裁剪应用到 `load`、readiness 或 source health。

## 3. 内部事实与传递接口

新增私有模块 `marivo/analysis/compiler/source_dependencies.py`，由 normalization 调用，计划使用以下封闭类型：

- `SourceColumnDependency`：逻辑列名、物理列名、声明类型，以及确定性去重的用途集合。用途覆盖输出/谓词、measure、identity、relationship、version、event、retained state；用途只是解释依赖，不参与后端能力注册。
- `EntitySourceDependency`：完整 `TargetEntityContract`、精确 `ObservationOwner`、datasource 身份和后端、规范化 catalog/schema/table、该 Entity 的有序列依赖。保留原始声明位置供 adapter 解释默认 namespace。类型不序列化凭据或 owner，不进入公共导出。
- `SourceDependencies`：一个逻辑源闭包的不可变 Entity 依赖序列；按 Entity 身份查找，校验来自同一个精确 owner/binding。不同 Entity 即便指向同一物理关系也保留各自记录。

关系身份必须使用现有 `SourceBinding.same_domain()` 的 owner/binding 语义；不得只用 datasource 名或裸 table 名判断相同。默认 catalog/schema 依照 datasource 已有声明解释，PostgreSQL 未指定 schema 时继续由 `to_regclass` 遵守 search path，不伪造 `current_schema` 身份。不因字符串相似合并请求。

`normalize.required_source_dependencies(dataset, *, registry=None)` 是唯一分析入口。`required_entities()` 与它共享同一次语义遍历的实体/路径收集逻辑，避免维护两份路径算法；不能让 registry、placement 与 compiler 形成循环导入。

静态准入调用该入口；源执行步骤冻结本步骤的依赖并显式传给 compiler/Runtime。`compile_dataset(..., dependencies=...)` 与 `_Compiler` 消费该事实；现有独立编译调用省略参数时仍由唯一入口产生依赖，并验证传入表包含全部必要列后执行投影。缺少必要列或依赖绑定不匹配均失败。

`get_schema(..., dependency: EntitySourceDependency | None = None)` 是明确请求接口。Runtime 的声明表路径必须传入依赖；adapter 校验请求关系和调用的 name/database/catalog 一致。`None` 只保留现有内部临时表、文件/reader 及独立 adapter 元数据调用的完整 schema 语义，不能成为声明表失败后的 fallback。`_declared_table`、`_validate_source_schema` 接收该 Entity 依赖，实际源 Ibis 表只声明必要物理列，并按逻辑别名投影。

adapter 可以读取完整元数据，但类型映射、collation、有限值及 SQLite storage-class 检查只针对本次请求的必要列。表存在性、表引擎、connector、只读和会话设置等关系级限制仍检查。保留 PostgreSQL 游标/binary RECORD、DuckDB 临时资源、各后端增量传输与关闭方式。

## 4. 完整依赖收集规则

### 4.1 列 comment 与执行依赖的边界

列 comment 是 Agent 理解字段、形成语义声明时的物理来源证据，必须考虑。其唯一拥有者仍为 `marivo/datasource/metadata.py::ColumnMetadata.comment`、`inspect_table()` 和各 datasource engine 的元数据采集；不复制到 `SourceColumnDependency`，也不增加公共字段。comment 不自动成为业务口径、类型、join key 或计算表达式，不能仅从注释推断依赖。

执行请求的必要列裁剪不改变显式检视范围：完整表检视继续展示完整列及其可用注释；声明投影检视按精确物理关系和 logical→physical binding 将注释关联到逻辑别名，不按裸列名跨表拼接。未参与当前 Dataset 的列仍可被 Agent 检视，其注释不能因为执行不使用而丢失。无需在每次 execute 中额外查询注释；注释缺失或不可读不使合法计算失败，纯注释变化不应改变 Dataset 的计算语义。

本次代码核查：DuckDB、MySQL、ClickHouse 和 Trino 已有列 comment 采集路径；SQLite 明确返回 `None` 并报告 `comments_unavailable`；PostgreSQL 当前列元数据填 `comment=None`，只有表注释查询。C1 在 `marivo/datasource/engines/postgres.py` 补齐列注释采集，依实际解析关系和列标识读取 PostgreSQL catalog，不用列名或默认 `public` 猜测归属。未设置注释与查询失败须区分，失败沿用现有 metadata warning 契约；不能伪造空字符串成功或扩大执行权限。

复用 `tests/test_datasource_metadata.py`、`tests/test_datasource_projected_inspection.py` 与 `tests/test_datasource_authoring_inspection.py`，验证有注释/无注释/不可用、列重命名、不同关系同名列、未使用复杂列注释仍可检视，以及只改注释不改变执行依赖。保留原始多语言注释内容，展示遵循现有有界渲染；注释作为外部数据，不作为指令执行。PostgreSQL 新采集需真实 reader 元数据证据，其余后端保真不能仅凭返回类型存在而宣称实测。

### 4.2 计算及断言依赖

遍历完整 `logical_roots()`，不能只看最终输出 schema；`.metric()`、rank、limit 或空输出不删除上游计算与断言依赖。

| 消费事实 | 收集内容 |
| --- | --- |
| 每个实际使用的 Entity | `primary_key`、`version_row_key`、当前源断言需要的列；不加入未使用的普通声明列 |
| Population 与 Metric 谓词 | 通过 field identity 解析 Dimension/TimeDimension，加入其逻辑列及精确路径 |
| Metric graph | 所有组件根、aggregate target、weighted value/weight、slice/filter、reference/time/status/cumulative axis；包括被最终投影隐藏的组件和 parts 输入 |
| 关系路径 | 仅实际选择的 contribution/coordinate/governed/participant 路径；用 `relationship_columns()` 加入两端 join keys，保留两端身份与 fanout/缺坐标断言 |
| 版本选择 | snapshot coordinate 或 validity from/to、版本行身份；解析时间所需的 companion/prefix 列与现有 lowering 保持一致 |
| Event/Lifecycle | occurrence identity、occurred_at、Event predicate、participant path、reducer axes；不能只收集最终展示列 |
| retained 输入 | 继续遵循 `required_entities()` 的当前源使用判定；保留原始闭包停止边界，不因原始语义 ref 重新打开来源 |
| sampling/私有状态 | 收集其逻辑输入与身份、准备及证明所需依赖；不把准备关系误当新声明源，也不改变单次求值规则 |

sidecar 解析规则：直接列使用 `source_column`；表达式绑定沿 `ExpressionBindingV1.entity_position` 和 `field_owners` 递归解析。原始参数列访问的语法分析须保留参数位置，不能继续合并成裸列集合。若需扩展 `ExpressionBody` 的私有语法事实，只在 `_expression_binding.py` 实现并由 Analysis normalization 绑定到实际 Entity；不在 adapter 中解析表达式。

只接受现有 validator 允许且依赖归属可证明的表达式。纯常量必须有无列依赖的正面证明，不能把空 `source_columns` 当作证明。缺失 body、循环绑定、动态/未知列、无法定位参数、未知 payload 或额外关系引用结构化拒绝；不执行任意用户 callable 来探测依赖，不吞异常后退回全部列。对已有合法 DuckDB 表达式补齐模型和回归后才接入，不能以“未知”拒绝代替支持现状。

不从最终 SQL 文本反推依赖；SQL 只用于独立验证投影。编译后 validation、preparation、primary、proof、parts 中每个源列引用必须落在统一依赖内；测试同时检查该不变量和独立写出的精确期望集合，不能仅用实现自身产物互证。

## 5. schema 错误契约

在 `materialization/errors.py` 新增私有 `SourceSchemaError(MaterializationError)`，保留既有 `expected/received/repair/stage/run_ref`，增加具体字段：`reason`（`missing_column`、`unsupported_physical_type`、`type_mismatch`）、Entity 身份、datasource 身份、catalog/schema/table、逻辑列、物理列、声明类型和实际类型。缺列实际类型为 `None`；错误继承现有模板，不增加公共异常导出或另一套结果协议。

必要列缺失及兼容性比较由 Runtime 拥有；必要物理类型解析失败/后端不支持由具体 adapter 转成同一错误，保留异常链。nullability、generic Decimal 与现有 timestamp scale 兼容规则不变。关系不存在、权限/断连、损坏元数据、collation/非法存储值错误保持自身原因，不统一伪装成缺列或不支持类型。

诊断给出正确 Entity、关系、logical→physical 列映射及可执行修复；保留有界输出，禁止凭据、连接 URI、业务行值或任意驱动异常文本进入安全 Run failure。验证 `RunFailure.expected/received/repair` 能保留有用事实，失败不能发布结果。

## 6. 实施顺序与验收样本

1. 新增依赖类型与纯收集器，锁定独立期望；接通 scalar 准入及 compiler 输入契约。先跑静态测试，确认未扩大类型/方法准入。
2. 显式接通 Runtime 和六个 schema adapter，移除 `_declared_columns`；补统一诊断，检查实际编译/提交投影。
3. 完成 DuckDB/SQLite 真实 Runtime；串行运行其余四后端的只读旅程。最后同步披露、广门禁和 C1 验收记录。

复用 `tests/lazy_execution_fixtures.py::make_execution_registry`、`execution_fixture`，`tests/lazy_scalar_source_fixtures.py::registry_for`，`tests/lazy_postgres_fixtures.py::registry_for`，`tests/lazy_temporal_fixtures.py::temporal_fixture`，`tests/lazy_event_fixtures.py::make_event_registry`，`tests/lazy_retained_fixtures.py::setup_retained`。新跨模块小型 builder 放入 `tests/lazy_source_dependency_fixtures.py`，不增加全局 mutable fixture。

| 新增案例 / 测试拥有者 | 独立预期与负向邻接 |
| --- | --- |
| `test_lazy_source_dependencies.py`：六后端纯分析 | `id + amount` 求和只需要两列；声明未使用 JSON/array/struct 不进入依赖；改为实际使用不支持列仍拒绝。字段重命名后精确保留 logical→physical 映射 |
| 同文件：关系身份 | orders/customer 含同名列，仅一侧使用；不同 schema 同名表、不同 binding 和同物理表双 Entity 不串列。只收集实际关系路径，两端键与身份不得删除 |
| 同文件：隐藏依赖 | observe 多指标再投影、weighted mean 的 weight、slice、版本轴、Event predicate、sampling、retained parts；独立列清单须包含隐藏输入。未知表达式在 I/O 前失败 |
| `test_lazy_source_schema.py`：adapter/Runtime | 六后端完整元数据含无关未知物理类型，mapper 不接触它；必要类型逐项测试缺列/不支持/不匹配，断言结构化字段、归属、repair、无 canary 泄漏。覆盖 generic Decimal 提前解析 |
| `test_lazy_source_dependency_runtime.py`：真实旅程 | 三行 `(id, amount, weight)=(1,10,1),(2,20,2),(3,30,1)`：sum=60、count=3、mean=20、weighted mean=20；声明/物理无关列两种情形均成功，SQL 不投影它们 |
| 同文件：断言与冷读 | 重复 id、必要缺列、错误键类型、关系 fanout、版本重叠在过滤结果为空时仍失败且不发布；主结果/parts 冷 rollup 与独立总量一致，冷命中不重新读取源 |

schema/SQL 单元测试使用可控元数据和真实后端编译器；远程实测不能被这些测试替代。每后端至少新增一条声明未使用复杂列、一条未声明无关物理类型和一条关系同名列旅程，必要类型错误由静态/schema/真实 Runtime 分层覆盖。SQLite 使用其实际声明类型及 storage-class 边界，不声称拥有原生 JSON 语义。

SQL 验证解析 source projection，区分未使用源列与允许的结果/中间层 `*`。在具体 adapter/driver 边界捕获 metadata、validation、primary、parts，证明必要断言仍提交；C1 不改造 C3a 的统一统计口径，也不把投影证明写成物理扫描裁剪证明。

## 7. 验证命令与环境

下列为实施后必须执行的命令，新增文件在第 6 节定义。本次起草计划没有执行它们。

```bash
make test TESTS='tests/test_lazy_source_dependencies.py tests/test_lazy_source_schema.py tests/test_lazy_scalar_admission.py tests/test_lazy_postgres_admission.py tests/test_lazy_backend_dispatch.py'
make test TESTS='tests/test_datasource_metadata.py tests/test_datasource_projected_inspection.py tests/test_datasource_authoring_inspection.py'
make test TESTS='tests/test_lazy_scalar_execution_adapter.py tests/test_lazy_postgres_execution_adapter.py tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_postgres_errors.py tests/test_lazy_scalar_transport.py'
make test TESTS='tests/test_lazy_compiler.py tests/test_lazy_temporal_source.py tests/test_lazy_event_compiler.py tests/test_lazy_lifecycle_numeric.py tests/test_lazy_population_sampling.py tests/test_lazy_retained_compiler.py tests/test_lazy_distinct_compiler.py tests/test_lazy_distribution_compiler.py tests/test_lazy_entity_candidate_compiler.py tests/test_lazy_driver_compiler.py'
make runtime-test TESTS='tests/test_lazy_source_dependency_runtime.py tests/test_lazy_materialization_execution.py tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py tests/test_lazy_retained_runtime.py' RUNTIME_WORKERS=1
make runtime-test TESTS='tests/test_lazy_temporal_runtime.py tests/test_lazy_event_runtime.py tests/test_lazy_lifecycle_runtime.py tests/test_lazy_candidate_runtime.py tests/test_lazy_driver_runtime.py tests/test_lazy_distinct_runtime.py tests/test_lazy_distribution_runtime.py tests/test_lazy_adapter_runtime_acceptance.py' RUNTIME_WORKERS=1
```

专用环境先用 `bash tests/multisource_environment/manage.sh status <service>` 核实归属和状态，service 为 `postgres-analysis`、`mysql-analysis`、`clickhouse`、`trino`。管理员仅准备/清理专用 fixture，Dataset 全程用现有 reader。远程新增旅程使用同一新 Runtime 文件，依据现有 opt-in 环境变量选择；默认运行须明确显示跳过的远程案例。

```bash
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_source_dependency_runtime.py tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py' RUNTIME_WORKERS=1
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_source_dependency_runtime.py tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py' RUNTIME_WORKERS=1
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_source_dependency_runtime.py tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_source_dependency_runtime.py tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
```

这些命令串行执行。服务不可用时记录未运行，不能因全 skip 宣称后端通过。当前 C0 的 Trino 停止状态只是历史观察，实施前重新核实；若需要启动/切换，先明确专用服务所有权与影响，尤其 `manage.sh start trino/clickhouse` 会停止另一组。此计划起草不启动、停止或初始化任何服务，不运行 release-check/MinIO。

```bash
make typecheck TYPECHECK_TARGETS='marivo/analysis/compiler/normalize.py marivo/analysis/compiler/source_dependencies.py marivo/analysis/compiler/lowering.py marivo/analysis/compiler/placement.py marivo/analysis/operators/scalar_support.py marivo/analysis/materialization/execution.py marivo/analysis/materialization/admission.py marivo/analysis/materialization/errors.py marivo/analysis/materialization/scalar_sql_execution.py marivo/analysis/materialization/duckdb_execution.py marivo/analysis/materialization/postgres_execution.py marivo/analysis/materialization/mysql_execution.py marivo/analysis/materialization/sqlite_execution.py marivo/analysis/materialization/trino_execution.py marivo/analysis/materialization/clickhouse_execution.py marivo/semantic/_expression_binding.py'
make lint-agent LINT_TARGETS='marivo/analysis/compiler marivo/analysis/operators/scalar_support.py marivo/analysis/materialization marivo/analysis/datasets/_disclosure.py marivo/semantic/_expression_binding.py tests/lazy_source_dependency_fixtures.py tests/test_lazy_source_dependencies.py tests/test_lazy_source_schema.py tests/test_lazy_source_dependency_runtime.py'
make test TESTS='tests/test_public_surface.py tests/test_analysis_help.py tests/test_analysis_help_resolution.py tests/test_lazy_disclosure.py tests/test_lazy_disclosure_examples.py tests/test_analysis_disclosure_journeys.py'
make check-agent
npm --prefix site run verify:content
npm --prefix site run build
git diff --check
```

列 comment 补齐另运行以下定向静态检查，并在 PostgreSQL 的真实 Runtime 验收中加入 reader 检视断言：

```bash
make typecheck TYPECHECK_TARGETS='marivo/datasource/engines/postgres.py'
make lint-agent LINT_TARGETS='marivo/datasource/engines/postgres.py tests/test_datasource_metadata.py tests/test_datasource_projected_inspection.py tests/test_datasource_authoring_inspection.py'
```

type/lint 命令在实现后按实际 touched files 补齐；不使用自动修复命令扫描并改写无关文件。默认全量门禁只跑一次成功结果；失败先修复再重跑，不能把超时或资源受限写为通过。

## 8. 披露与完成条件

实现通过后同步 `docs/specs/analysis/python-analysis-design.md` 及中英文 `site/src/content/docs/{docs,zh-cn/docs}/latest/concepts/analysis-workflow.mdx` 中“全部声明列”约束，并给出未使用复杂列的实际可执行示例。native Help owner 为 `marivo/analysis/datasets/_disclosure.py` 的 `analysis.actions.execute`；保留有界通用说明，具体必要列/类型修复由结构化错误拥有，不复制后端矩阵。检查动态 contract 及 Help 漂移、可达性、预算测试，不新增 Help 别名。

公共导出及 CLI 入口保持不变，验证无错误披露即可。packaged `marivo-semantic`/`marivo-analysis` skill 只检查一致性；若确有必须修改的具体文字，列出差异并依仓库规则另获明确批准，本计划不包含该编辑授权。

C1 验收记录写入 `2026-09-16-multisource-capability-c1-acceptance.md`（跨日则采用实际日期），包含基线/工作区身份、每后端目标及命令结果、独立期望、实际投影/断言、reader 与 fixture 边界、失败未发布、清理和剩余拒绝项。只有六后端新增证据、DuckDB 回归、披露和广门禁都满足后，才能更新总计划为 C1 完成；尚缺真实环境证据时明确标为部分验收。C0 历史记录保持原有时点，不改写为 C1 证据。
