# R0.5 生产 SQL、适配责任与目标资格台账

Date: 2026-09-26

Status: R0.5 静态目标与调用链保留，R1.5/R1.6 当前状态覆盖见下节。`md.raw_sql` 是唯一公共终端 SQL 通道；内部 SQL 例外除用户 2026-09-28 明确批准的 provider 固定语句通道（六后端 metadata 事实与 DuckDB scoped HTTP 凭据，范围见 R1.6 覆盖节）外仍为空。六后端基础来源已有分格证据，完整资格和资源终止仍未验证。

依据：[主计划 §2、§5、§9](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)、[R0 实施文档 R0.5](2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md)、[能力台账 §6](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#6-r04-六类元算子规则冻结)。本文记录当前生产构造/提交的迁移责任，**不**批准将手写 SQL 搬进 Analysis adapter。`I`=由 typed Ibis 表达式构造及原样编译提交，`D`=经驱动公开配置/metadata API，`P`=执行前准入的 Ibis 准备→Python，`T`=唯一公共终端 `md.raw_sql`，`X`=删除，`S`=仅 Store SQLite 事务，`V`=经用户 2026-09-28 明确批准的 provider 固定语句通道（datasource.capabilities 注册表 + 快照测试钉住文本，见 R1.6 覆盖节）。每行状态均为“当前定位；目标路线未实证”；表中“证明”是以后阶段必须取得的真实证据，不是本轮通过。

## R1.5 当前状态覆盖（2026-09-27）

下表更新早期静态“当前定位”，精确实测格见阶段验收 R1.5 (historical record in Git history)；原表的目标路线与内部 SQL 例外空集不变。

| 台账 ID | 当前处置与未闭合格 |
| --- | --- |
| DS02/B13 | 公开 `md.connect`、`DatasourceCatalog.connect`、`DatasourceConnection` 及 Help 已删除；`md.raw_sql` 是唯一公开终端 SQL 入口，typed reentry 拒绝 |
| DS03–DS07、DS09 | 六后端 schema-only Ibis metadata 有实测 unavailable 披露；45 个丰富 metadata 断言仍跳过，不能记作其原目标通过 |
| DS11、DS13、DS15 | MySQL/ClickHouse 可执行 timeout、时区事实和带认证 DuckDB HTTP 仍有精确阻塞；无内部 SQL 替代 |
| DS17、DS21；AN33 | 基础来源读取/样本及新 `SourceSession` 提交有有界证据；完整来源形态和远端终止未齐，旧具体文本执行类待 R4–R9 清除 |

## R1.6 当前状态覆盖（2026-09-28）

用户于 2026-09-28 明确批准"将这些接口必要的部分使用统一的数据源的接口抽象，每种源提供自己的实现，允许使用非 Ibis 的其他手段实现具体能力"。据此建立的例外范围：**操作**=六后端 metadata 事实读取（注释/可空性/主键/唯一约束/分区拓扑/视图/物理 profile/projectable columns）与 DuckDB scoped HTTP 凭据安装；**后端**=六后端 metadata、仅 DuckDB 凭据；**用途**=datasource metadata 检查与带作用域的认证 HTTP JSON 读取。凭据值经参数化提交，绝不进入 SQL 文本或提交记录。精确实测格见阶段验收 R1.6 (historical record in Git history)。

| 台账 ID | 当前处置与未闭合格 |
| --- | --- |
| DS03–DS07、DS09 | **V**：六后端 metadata 事实经 `datasource.capabilities` 注册固定语句提交，列基线仍为绑定 Ibis schema；逐事实失败披露该事实 unavailable。45 个丰富 metadata 断言已恢复，六后端真实服务正反例见 R1.6 验收记录 |
| DS15 | **V**：连接期以参数化 `CREATE OR REPLACE SECRET` 安装限定范围临时 DuckDB secret；scope 外不发送凭据；值不入 SQL 文本/提交记录/项目状态，`md.test` 往返与脱敏有实测 |
| DS11、DS13 | 保持阻塞：MySQL/ClickHouse 无可执行 timeout/时区事实，不变 |
| 通道治理 | `register_provider_statements` 注册表关闭未注册 SQL；快照测试钉死全部模板文本；每次提交记录在 `backend._marivo_provider_submissions`；超出批准范围的新内部 SQL 仍须用户逐项批准 |

## 1. 源端与 Semantic SQL 入口

### R9.5 current disposition overlay (2026-10-06)

The R9.5 SQL ledger (historical record in Git history) owns the current
55-ID disposition and appended DS23 ClickHouse owned-query cancellation.
Historical constructor/caller rows below remain traceability records. The
legacy Analysis SQL adapters, statement constructors, compiled-text patches,
source preparation and Artifact-to-DuckDB scans are physically retired.
SourceSession-issued Ibis reads, the original-text terminal, authorized provider
templates and Store persistence retain separate ownership. Provider purposes
are now closed for every metadata/credential template; MySQL and ClickHouse
controls keep their separately approved purposes and identities. The later
independent-read amendment withdrew PostgreSQL snapshot-control registration.
No historical execution candidate or all-profile method qualification is
changed by this SQL closure.

`marivo/datasource/metadata.py` 的 `_query_rows` 是六个 engine metadata SQL 的实际 `backend.raw_sql` 提交点；`manage.py` 的 `raw_sql` 是公共任意语句提交点。`source_health.py` 实际位于 `marivo/semantic/`。下表以构造符号为行，列出其调用方和提交点；同一符号内的不同用途分列。

| ID | 当前构造/调用方 → 实际提交；分类、后端、输入 | 目标处置；owner/阶段；真实证明 |
| --- | --- | --- |
| DS01 | `manage.test/test_no_persist` → `backend.raw_sql("SELECT 1")`；连接探测、六后端、固定文本 | Ibis literal scalar 或获准 driver ping，禁止手写查询；datasource adapters/R1；真实 roundtrip/超时/权限 |
| DS02 | `manage.raw_sql/_bounded_execution_sql` → `backend.raw_sql(execution_sql)`；R0 基线的用户 SQL、sqlglot 限行改写/保序探针 | T：保留 `md.raw_sql`、`RawSqlResult`、Help；输入不做 SQL 解析或类别判定，原文终端提交；必填 reason、正数返回行界/可执行超时、显式截断，不能重入 Semantic/Analysis。只读依赖连接/后端权限，尽力控制，无法保证本身不阻断；datasource/R1；实测权限、超时、截断、typed reentry；不得供内部方法调用 |
| DS03 | `engines/duckdb._inspect_duckdb` → `metadata._query_rows`；catalog/table/view/schema/constraint 元数据，DuckDB，手写 `duckdb_tables()/duckdb_views()` 等 | D metadata API 或 Ibis schema；datasource adapters/R1；表/视图/约束实测，非业务行 |
| DS04 | `engines/postgres._inspect_postgres` → `_query_rows`；注释、列、分区、大小，PostgreSQL，`pg_catalog`/information_schema 模板 | D 或 Ibis schema；datasource adapters/R1；schema/search path/权限实测 |
| DS05 | `engines/mysql._inspect_mysql` → `_query_rows`；表/列/分区/视图，MySQL，`SHOW FULL COLUMNS`、information_schema 模板 | D 或 Ibis schema；datasource adapters/R1；视图/类型/只读账号实测 |
| DS06 | `engines/sqlite._inspect_sqlite` → `_query_rows`；sqlite_schema/pragma 表与索引，SQLite，手写 SELECT | D SQLite metadata API 或 Ibis schema；datasource adapters/R1；main/view/attached 拒绝实测 |
| DS07 | `engines/trino._inspect_trino`/`_trino_show_create_table`/`_partitions_table_is_iceberg` → `_query_rows`/`backend.raw_sql`；catalog、table、SHOW、零行探测，Trino | D connector metadata 或 Ibis schema/零行表达式；datasource adapters/R1；Iceberg/non-Iceberg 分格实测 |
| DS08 | `engines/trino.inspect_partition_values` → `backend.raw_sql`；实际分区值业务读取，Trino，手写 SELECT/ORDER/LIMIT | I typed 分区表达式；datasource adapters/R1；实际分区及非 Iceberg 拒绝，不用 SHOW 代替 |
| DS09 | `engines/clickhouse._inspect_clickhouse`/`_clickhouse_physical_profile`/`_clickhouse_projectable_columns` → `backend.raw_sql`；system.tables/columns/parts_columns 物理元数据，ClickHouse | D metadata API 或 Ibis relation；datasource adapters/R1；MergeTree/Distributed/冲突 part 类型实测 |
| DS10 | `engines/clickhouse.inspect_partition_values`/`clickhouse_system_parts_target` → `backend.raw_sql`；system.parts 分区值/拓扑，ClickHouse | I 若读业务分区值，D 仅物理拓扑；datasource adapters/R1；活跃 part 与权限实测 |
| DS11 | `engines/{mysql,postgres,trino}.authoring_timeout` → `backend.raw_sql`；SET/SHOW 会话控制，三后端，手写设置文本 | D 驱动公开 timeout/session API；不存在等价能力则阻塞，例外空；datasource adapters/R1；超时与恢复实测 |
| DS12 | `engines/sqlite.connect` → `backend.raw_sql("PRAGMA query_only = ON")`；R0 基线连接只读控制，SQLite | D SQLite 驱动 authorizer/只读连接；datasource adapters/R1；写入拒绝实测 |
| DS13 | `engines/{mysql,postgres,trino}.timezone_probe_sql`、`timezone._execute_scalar` → `backend.sql`/`raw_sql`；时区/会话读取，三后端 | D driver/session metadata；datasource adapters/R1；IANA/固定偏移及失败状态实测 |
| DS14 | `engines/clickhouse.postprocess_sql` 与 `EngineProfile.postprocess_sql`；ClickHouse 方言文本改写钩子。当前 `marivo/` 搜索仅找到定义/赋值，未找到生产调用，不能记作已提交 | X：移除未使用钩子并禁止重新接入；datasource adapters/R1/R9；静态无调用、实际提交与 Ibis 产物逐字核对 |
| DS15 | `backends._marivo_duckdb_http_auth` → native `raw_sql(CREATE OR REPLACE SECRET...)`；HTTP 凭据控制，DuckDB | D 驱动/extension 认证 API；无等价则该来源阻塞且不得把密钥打日志；datasource adapters/R1；HTTP 认证/脱敏实测 |
| DS16 | `backends._configure_http` → `raw_sql("SET force_download=true")`；下载控制，DuckDB | D 驱动 setting API 或拒绝该来源；datasource adapters/R1；HTTP 范围与重试实测 |
| DS17 | `semantic.source_health._field_frame/_execute_relationship_check` → `Ibis .execute()`；字段/关系业务事实读取，六后端 | I 保留 typed 构造但改走统一 adapter 批次/检查；semantic+adapters/R1/R2；not-null/唯一/关系反例实测 |
| DS18 | `semantic.source_health.run_source_health` → `backend.raw_sql("SELECT 1")`；连接探测，六后端 | 同 DS01 的单一 ping owner；semantic 只消费结果；R1/R2；真实连接失败/超时 |
| DS19 | `semantic.parity.parity_check` → `backend.sql(qualified_sql)`；R0 基线执行 provenance SQL oracle | X：删除 `ms.parity_check`、结果/状态和公开入口；本阶段不建替代 API；semantic/R1；历史 SQL 不进入 executor |
| DS20 | `datasource.ir.qualify_provenance_sql` → `sqlglot.parse_one/.sql`；R0 基线 provenance 文本转换 | X：删除执行改写函数和 `ms.from_sql` 字段/入口；历史说明可放 `ai_context`，无执行权限；semantic/R1 |
| DS21 | `semantic.catalog` preview、`datasource.snapshot/inspection` → bound Ibis `.execute()`/reader；有界抽样/物理检查，六后端 | I 经统一 adapter，保留范围、身份、资源；datasource adapters/R1；真实样本/空 schema/提前关闭 |
| DS22 | `datasource.capabilities.execute_provider_statement` → `backend.raw_sql(渲染后固定语句)`；六后端 metadata 事实与 DuckDB scoped HTTP 凭据，2026-09-28 新增 | V 通道（用户批准例外）；datasource/R1.6；注册表快照钉死文本 + 本地/远端逐后端实测；超出范围的新内部 SQL 须再批准 |

## 2. Analysis SQL 构造与提交链

每行包括当前 caller、用途及目标 owner。`ExecutionAdapter.statement(sql)`、`ScalarExecutionAdapter.statement/submit/batches`、`DuckDBExecutionAdapter.statement/submit` 和 `PostgresExecutionAdapter.statement/submit` 是可接收任意 SQL 的共享提交口；R1/R4 必须让受治理来源 adapter 只接收绑定 Ibis 表达身份的编译产物。`md.raw_sql` 保留独立终端提交路径，不能借共享 `statement(sql)` 注入 Analysis compiler。原生游标可保留。

| ID | 当前构造/调用方 → 提交；用途、后端与方式 | 目标处置；owner/阶段；真实证明 |
| --- | --- | --- |
| AN01 | `compiler.driver_numeric.install_driver_numeric_functions` → DuckDB `raw_sql(CREATE TEMP MACRO)`；精确 float 宏、DuckDB | I 合格数值表达或预准入 P；compiler/methods/R5/R9；float 边界独立 oracle、无宏提交 |
| AN02 | `compiler.lifecycle._ambiguity_check`/`compile_replay` → backend `sql(query)`；同刻枚举、递归 replay，DuckDB，手写 CTE | I 表达可得部分、其余预准入 P 有序重放；methods/compiler/R7；同刻反例/轨迹与无裸 SQL |
| AN03 | `compiler.lifecycle_array.{trino_replay,trino_confluence,clickhouse_replay,clickhouse_confluence}` → caller 的 statement/bundle；数组 replay/冲突，Trino/ClickHouse，手写模板 | I 准备→P 领域 replay 或经资格验证的 Ibis lowering；methods/compiler/R7；全轨迹/冲突/资源实测 |
| AN04 | `materialization.{postgres,trino,clickhouse}_event_sql._EventCompiler` → bundle；Ibis 私有编译 visitor、后处理/方言补丁，三后端 | I 使用标准 Ibis 编译或 adapter 注册的合格表达实现，不在 Marivo 改写方言 AST；compiler/adapters/R7/R9；真实提交与数值对照 |
| AN05 | `materialization.{postgres,clickhouse}_event_sql.compile_event_bundle` → `event_bundle.EventBundleStream` cursor；事件主表/证据/parts 单 statement，手写 WITH/UNION | I 分别编译 typed 输出/检查，统一 BatchStream+receipt；methods/adapter/R7；完整 parts、空 schema/提前 close 实测 |
| AN06 | `materialization.trino_event_sql.compile_event_expression`、`trino_execution._compile_sql` → scalar cursor；解析 Ibis SQL 后附手写 CTE | I 编译产物原样提交；无法等价的 Event 算法预准入 P；compiler/adapters/R7/R9；Trino 实际语句对照 |
| AN07 | `materialization.{postgres,trino}_execution` Event snapshot 前缀及控制 → cursor；手写 WITH、SET/ROLLBACK，两后端；`clickhouse_execution.open_event_bundle/open_lifecycle_bundle` 另以 `getSetting` SELECT 校验快照/CTE | I Event 读取、D 驱动事务/setting API；adapter/R7/R9；快照/取消/失败无重试，ClickHouse 同查询快照实测 |
| AN08 | `materialization.lifecycle_integrity.{integrity_sql,integrity_queries}` → `lifecycle_publication`/bundle statement；History/transition/coverage 完整性，DuckDB/ClickHouse，手写 SQL 与 sqlglot 重写 | I 检查表达或 P 对完整输入验证；methods/materialization/R7；逐项故障注入且无文本补丁 |
| AN09 | `materialization.lifecycle_bundle.LifecycleBundle` → cursor；ClickHouse 履历检查+主/部件 packet，手写 SELECT/CTE | I 或预准入 P，分别发布确切 parts；methods/materialization/R7；Distributed/资源/空批次实测 |
| AN10 | `materialization.lifecycle_publication.{native_summary,inspect_history}` → `backend.statement/submit`；History 行数/校验，来源与本地 DuckDB | I 校验或固定 pandas 检查；methods/materialization/R7；逐视图独立 count 与 receipt |
| AN11 | 历史 `materialization.duckdb_statements.attribution_summary_sql` 手写 CTE；R6.6 已删除无调用方的整个模块 | 公共归因经 Ibis scope/键检查与统一本地分配核对；raw-fact、残差和实际 native 提交见 R6 ledger；R6.7 已清理 R6 私有构造、执行、发布和 codec 消费者，实际 R7/R8 helper 归其 owner |
| AN12 | 历史 `source_stage.attribution_source_summary` 提交链及后来的拒绝钩子；R6.7 已删除钩子、收集与 summary 调用 | 公共 R6.6 使用 SourceSession 签发的 Ibis 编译句柄；driver SQL 与签发 SQL 逐条相等；Event/R8 仅保留实际 helper owner，远端资格仍归 R9 |
| AN13 | `materialization.local_stage._source_count` → `backend.statement`；来源计数探测，六后端候选 | I `count` 表达，或合格本地输入计数；compiler/adapters/R5/R9；大表/空表实测 |
| AN14 | `materialization.retained.validate_source_private_relation` → `backend.statement`；部件/来源私有关系校验，来源与本地 | I 源检查、固定 pandas 部件校验；materialization/R4/R9；损坏/缺键/重复拒绝 |
| AN15 | `materialization.duckdb_execution.{initialize,install_numeric}` → `statement(SET/MACRO)`；本地设置/宏，DuckDB | D 驱动设置；数值宏同 AN01 删除；adapter/R1/R4；时区/线程/数值实测 |
| AN16 | `materialization.duckdb_execution.{get_schema,describe_statement}` → statement；表 schema，DuckDB，sqlglot AST/手写 DESCRIBE | D/I schema API；adapter/R1；表/view/精度实测 |
| AN17 | `materialization.duckdb_execution.{table_statement,freeze_reader,read_csv,read_json,json_statement}` → statement；临时 CTAS/view 与文件 reader，DuckDB，编译结果外套 SQL | D/I 文件 reader 与受控物理绑定；adapter/R1/R4；CSV/JSON/HTTP/生命周期实测 |
| AN18 | `materialization.duckdb_execution.read_parquet`、`parquet_scan.attach_parquet_scan` → statement `CREATE VIEW ... read_parquet`；Artifact→DuckDB 固定续算 | X：receipt 检验后本地 pandas/NumPy/SciPy，永不打开 DuckDB；materialization/R4；断源新进程、监测无 DuckDB open |
| AN19 | `materialization.mysql_execution.{get_schema,timezone}` → statement；information_schema/变量，MySQL | D/I schema 与 driver/session timezone；adapter/R1；Decimal/date/时区实测 |
| AN20 | `materialization.mysql_execution` 日期 `SELECT count(*)` 检查 → statement；业务值校验，MySQL，手拼谓词 | I 精确日期/无效日期检查；adapter/R1/R9；非法日期/零时区/错误证据 |
| AN21 | `materialization.sqlite_execution.{get_schema,timezone}` → statement；sqlite_schema/pragma、时间，SQLite | D/I schema；adapter/R1；存储型/时间实测 |
| AN22 | `materialization.sqlite_execution` 存储型 `SELECT count(*)` 与 `PRAGMA query_only` → statement；业务值检查/控制，SQLite | I 存储检查，D query-only 控制；adapter/R1/R9；越界与只读拒绝 |
| AN23 | `materialization.postgres_execution.{prepare,_compile_sql}` → statement；Ibis 编译外套 `SELECT * FROM (...)`、Event CTE，PostgreSQL | I 原样编译提交；adapter/R1/R7；cursor/类型/语句身份对照 |
| AN24 | `materialization.postgres_execution.{get_schema,timezone}` → statement；pg_catalog/时区，PostgreSQL，手写 SELECT | D/I metadata；adapter/R1；namespace、类型、会话时区 |
| AN25 | `materialization.postgres_execution` 非有限数 `SELECT count(*)` → statement；业务值校验，PostgreSQL，手拼谓词 | I 校验表达；adapter/R1/R9；NaN/Infinity/Decimal 实测 |
| AN26 | `materialization.trino_execution.{get_schema,timezone}` → statement；catalog connector、SHOW COLUMNS/会话、表类型，Trino | D/I metadata；adapter/R1；Iceberg/non-Iceberg/权限 |
| AN27 | `materialization.trino_execution` 非有限数 `SELECT count(*)` → statement；业务值校验，Trino，手拼谓词 | I 校验表达；adapter/R1/R9；float/Decimal/表形态实测 |
| AN28 | `materialization.clickhouse_execution.{get_schema,timezone}` → statement；system.settings/tables、timezone，ClickHouse | D/I metadata；adapter/R1；Nullable/Distributed/时区 |
| AN29 | `materialization.clickhouse_execution` 物理时间/非有限数 `SELECT count(*)` → statement；业务值校验，ClickHouse，手拼谓词 | I 校验表达；adapter/R1/R9；DateTime64/NaN/远端资源 |
| AN30 | `materialization.{clickhouse,trino}_execution` 特性探测及 `SET SESSION` → statement；能力/控制，二后端 | D 驱动/metadata；无等价则该物理格阻塞；adapter/R1/R9；实际设置、取消、恢复 |
| AN31 | `materialization.temporal_sql.lower_temporal`、各 `*_execution._lower` → compiler；Ibis ops 自定义改写/私有 UDF，六后端 | I 标准表达或注册合格实现，不经 SQL 后补丁；compiler/adapters/R5/R9；DST/精度/提交对照 |
| AN32 | `materialization.source_preparation._ReservedJsonReader.raw_sql` → `backend.statement`；HTTP/JSON 设置，DuckDB | D source adapter 的认证/设置 API；adapter/R1；secret/HTTP/作用域实测 |
| AN33 | `materialization.scalar_sql_execution.{statement,submit,batches}`、`duckdb_execution.submit`、`postgres_execution.submit` → driver cursor；通用文本提交，六后端 | I 编译产物绑定 expression identity/purpose/schema 才可提交；adapter/R1/R4；伪造 SQL 被拒、提前 close/取消 |

`materialization/validation.py` 与 `source_stage.py` 的 `backend.prepare(expression)` 是已有 Ibis 路线的调用点；保留其语义时须核对后续是否包裹、改写 SQL。`session/`、`materialization/inspection.py` 和 `analysis/evidence/_dataset_reads.py` 中的 `SELECT` 均调用项目 Store 的 sqlite connection，而非 datasource；见 §3。`rg` 命中测试字符串、错误消息或独立 oracle 不进入生产 SQL 清零计数。

## 3. Store SQLite 白名单及七项 adapter 责任

Store 白名单仅覆盖 `materialization/store.py` 的 schema/PRAGMA/BEGIN/COMMIT/读写、`session/_lazy_{history,runtime_reads,graph}.py` 和 `session/__init__.py` 的 Run/Artifact/history 查询、`materialization/inspection.py` 与 `evidence/_dataset_reads.py` 的 Store 证据读取。它们只连接项目本地 `.marivo` Store，按 Store schema/事务 owner 审计；不得指向 datasource SQLite 或供 public SQL 执行。R4 的新单协议可以重写其 schema，但无需把事务 SQL 表达为 Ibis。任何此白名单以外的 `conn.execute(SQL)` 均重新分类，不凭文件名自动豁免。

| 内部 adapter 唯一责任 | 输入→输出与 owner；限制 |
| --- | --- |
| provider/连接 | validated backend spec + 用途→选中 SourceSession；datasource adapters；延迟导入所选驱动，声明/选择纯函数 |
| 物理来源/metadata | typed Table/File/JSON + 必要列→Ibis relation/schema/带范围事实；datasource adapters；业务行不可借 metadata SQL 读取 |
| 实现资格 | method@version + 数值/时间/来源/表形状 + 检查义务→QualifiedImplementation 或结构化拒绝；adapter 注册为单一判断 owner |
| Ibis 编译与传输 | bound Ibis expression + typed parameters/purpose/schema→原样编译产物/BatchStream；compiler 构造表达式，adapter 提交；移除任意 `statement(sql)` |
| 结果解码 | driver value + 固定 schema→Arrow batches；adapter；完整键、Decimal、nullable int64、timezone、Duration 不静默降精度 |
| 资源/取消 | owned connection/cursor/reader/transaction→close/interrupt 状态；adapter；提前停止、异常、远端未确认终止分开报告 |
| 来源覆盖依据 | exact source/Event binding + 请求范围→observed/declared/unknown 事实；adapter 提供可观测部分，Analysis 判领域覆盖；不由 count/max time 推导 |

`BatchStream` 的 schema 在首批前固定，空流仍有 schema；穷尽且成功关闭后才能发布。共用 compiler 和 Runtime 不按 backend 名称另设资格矩阵。纯来源同一执行域优先用合格 Ibis；缺算法时仅在执行前选择已注册 Ibis 准备→Python；来源失败不改道。纯 Artifact 始终 receipt 检验后受控本地 pandas/NumPy/SciPy；mixed live-source/Artifact 在数据 I/O 前拒绝。DS02 的 `RawSqlResult` 无 BatchStream/Artifact 发布和方法 K，其返回行界不能证明来源扫描成本或完整性。

## 4. 六后端目标资格矩阵

矩阵键为 `method@version × numeric type × time/source shape × backend/table form × route`，不把一个旧成功拓展成全部组合。下表每个格子是**必需目标或需显式拒绝的物理形状**，不是已获资格：`I`=真实 Ibis 来源；`P`=真实 Ibis 准备→Python；`F`=固定 Artifact 本地；`T`=公共终端 SQL；`N`=无该来源问题；`!`=必须记录拒绝反例。六后端的共同 Analysis 基线是普通表/视图、完整复合主体键、int64/float64、精确 UTC 时间或无时间、I+F；扩展 Decimal、原生/解析时间、文件/JSON、跨根/领域方法按行验证。Trino 的表形态细分 Iceberg/non-Iceberg；ClickHouse 为本地 MergeTree/Distributed；SQLite 为 main 普通表/view；各格均包括空输入、重复键、Null/Unknown、早关闭和无执行后回退负例。T 单独验证连接/后端只读权限观察、超时、截断和不可重入，不承袭 Analysis 的数值/时间资格。没有真实环境的格为**未验证或阻塞**。

| 方法单元@v1 / 数值、时间、来源形状 | DuckDB | PostgreSQL | MySQL | SQLite | Trino | ClickHouse |
| --- | --- | --- | --- | --- | --- | --- |
| C01.a/b typed 来源/metadata；文件/JSON/认证 | I/F，文件+HTTP | I/F，表/view | I/F，表/view | I/F，main 表/view | I/F，Iceberg+non-Iceberg | I/F，MergeTree+Distributed |
| C01.c `raw_sql_terminal@v1`；原文提交/只读尽力控制/reason/limit/timeout/不可重入 | T，文件/表 | T，表/view | T，表/view | T，main 表/view | T，Iceberg+non-Iceberg | T，MergeTree+Distributed |
| C02.a/b/c 定义绑定；版本/顺序/日历 | I/F | I/F | I/F | I/F | I/F | I/F |
| C03.a/b/c 成员/版本/read；复合键、时间、分类 | I/F | I/F | I/F | I/F | I/F | I/F |
| C04.a/b/c 单/多根观察与封闭 runtime；int/float/Decimal | I/F | I/F | I/F，Decimal! | I/F，Decimal! | I/F，Decimal! | I/F，Decimal! |
| C05.a/b/c 坐标/当前行/原状态；空组与 N/W | I/F | I/F | I/F | I/F | I/F | I/F |
| C06.a/b 时间网格/累计；DST、原生/解析时间 | I/F | I/F | I/F，fold! | I/F，解析! | I/F，非 Iceberg! | I/F，DateTime64! |
| C07.a/b/c compare/选人/普通 ratio；同域/显式对应 | I/F | I/F | I/F，Decimal! | I/F，类型! | I/F，表形态! | I/F，精度! |
| C08.a/b/c cohort/share/权重/排名；完整参照 | I/F | I/F | I/F | I/F | I/F | I/F，资源! |
| C09.a/b attribution joint/hierarchy/Top-K | P/F | P/F | P/F，扩展! | P/F | P/F | P/F |
| C10.a/b exact distinct/quantile；精确类型 | P/F | P/F | P/F | P/F，分位! | P/F | P/F |
| C11.a/b Event match/funnel；多 occurrence/同刻 | P/F | P/F | P/F | P/F | P/F，表形态! | P/F，资源! |
| C12.a/b duration/subject；删失/覆盖 | P/F | P/F | P/F | P/F | P/F | P/F |
| C13.a/b Lifecycle replay/视图；有序完整轨迹 | P/F | P/F | P/F | P/F | P/F | P/F |
| C14.a1 deviation zscore/MAD；原身份/拟合域/Cell/零尺度 | P/F | P/F | P/F | P/F | P/F | P/F |
| C14.a2 runs；完整格/条件状态/邻接/实际时长 | P/F | P/F | P/F | P/F | P/F | P/F |
| C14.b Pearson/Spearman/Kendall/lag；有序配对 | P/F | P/F | P/F | P/F | P/F | P/F |
| C14.c naive/drift/seasonal；完整未来格 | P/F | P/F | P/F | P/F | P/F | P/F |
| C15.a/b Runtime/发布/恢复；exact receipt/parts | I+F | I+F | I+F | I+F | I+F | I+F |
| C16.a/b、C17.a/b Help/工具/Agent/ontology | N；安装与本地资格 | N；安装与真实资格 | N；安装与真实资格 | N；安装与真实资格 | N；安装与真实资格 | N；安装与真实资格 |
| C18.a/b Anchor/retention；elapsed/calendar/覆盖 | P/F | P/F | P/F | P/F | P/F | P/F |

`P` 是执行前选择的目标路线，不是来源失败后的回退；若完整输入、资源或数值精度不能满足该方法，具体格阻塞，不能靠近似或缩小目标域通过。后续可新增单独验收的 Ibis 源端实现，但不得替换此必需目标格来跳过 P 验收。基线正例用 R0.1 §5 的原始事实与独立 SQL/Fraction/状态机/DST oracle 重新求目标结果；负例至少包含行中的 `!` 及相应 C 单元 §3 的拒绝例。计划中的统一运行命令为 `make runtime-test TESTS='tests/test_full_algebra_backend_matrix.py -k C03a'`（每个单元替换精确 ID，按真实 backend/表形态参数化），语义命令为 `make test TESTS='tests/test_full_algebra_contracts.py -k C18b'`；这两个目标测试文件尚未创建，**不能记为本轮已运行证据**。每个实际资格记录必须另附命令、服务/驱动版本、表形态、数值/时间类型、提交语句来源、资源与退出码。旧 C0–C10 只给反例线索；R1/R9 从零获得新 DSL 资格。

上表的 `T` 行沿用 R0 静态目标表述；R1.3 用户决定已改为输入不做 SQL 解析/归类、只读依连接与后端权限尽力控制。每个 `I`/`P` 格须按下面的适用形状展开，不把同一格的 float 通过转授 Decimal/时间/表形态；`F` 对每个已发布的主表和必需部件使用独立断源恢复检查。

| 形状键 | 适用方法与必需输入/拒绝 |
| --- | --- |
| `base-int64` / `base-float64` | C03–C15、C18 的数值消费：同一来源域普通表/受治理 view、无时间或 UTC exact instant、完整复合键；float 含非有限拒绝，int 含溢出拒绝；六后端各两格 |
| `decimal-exact` | C04–C10、C14 涉及 Decimal 的方法：声明 precision/scale、原始组件与输出精度、无隐式 float；六后端/表形态逐格证明，MySQL mean/div、Trino Decimal 等旧拒绝是重点负例而非新永久豁免 |
| `native-time` / `calendar-time` / `parsed-time` | C03.b、C06、C11–C14、C18：原生 timestamp/date 的精度/时区，DST 本地日 vs 168h，解析轴与累计端点；每后端证明可保留的形状，不能把字符串或 session timezone 猜成 exact instant |
| `multi-root` / `multi-occurrence` / `versioned` | C04.b、C05.a、C07、C11–C13、C18：两根完整成员×坐标并集、事件重数/业务顺序、精确版本与覆盖；不同 datasource 的现场混合固定拒绝，不承诺联邦 join |
| `physical-form` | DuckDB table/view/file/HTTP-JSON；PostgreSQL table/view 与 namespace；MySQL table/view；SQLite main ordinary table/view；Trino Iceberg 和 non-Iceberg；ClickHouse MergeTree 与 Distributed。每形态分别验证 metadata、实际读取、取消和负例，不能借同后端另一形态通过 |

此展开规则定义必需验证集合，不预判所有格可实现。若 R1/R7/R8 无法为某必需方法/形状注册 I 或 P，精确格标 **阻塞** 并报告用户所需的具体内部 SQL 例外决定；不能用 DS02 的终端结果代替该格，也不得把未实现格改写为目标“不支持”。

2026-10-02 经用户接受的 R8 设计修订将旧 C14.a 五方法目标替换为 C14.a1/a2，详见
[接口设计 §8.4.1](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md#841-从指标变化定位可继续分析的坐标)。
这是主动范围替换，不是把后端未通过格改成“不支持”，也不声称新方法与旧滑窗/排轴算法
等价。旧 Candidate、period_shifts 与 driver_axes 专属格退出；新三种方法版本各自重新
展开六后端的 P/F 资格，当前均为目标未验收。C14.b/c 相关与预测的资格要求不变。

新 C14.a 的来源路线为执行前选择的 Ibis 准备→本地方法，固定路线直接消费 Store 7 输入；
没有新增内部 SQL 例外。AN01 等共享数值 helper 仍按真实消费者与既定 owner 处理，不能
随 driver_axes 退役而误删其他方法的依赖。评分的中心/尺度/分数数值表示和误差必须在
R8.1 逐类型冻结；`decimal-exact` 格保留原始 Decimal 与方法自己的舍入/误差约定，不允许
以未经定义的 float 强转通过。runs 必须捕获完整格与条件依赖，禁止用源端 WHERE 只提取
命中格；物理行缺失/重复、来源覆盖违约与合法不可评估格分别验证。source/fixed/cold 的
等价只在同一方法、输入类型和完整 parts 的合格格内成立。

## 5. 扫描与本轮边界

本轮用 `rg -n 'raw_sql|statement\(|submit\(|\.sql\(|SELECT |WITH |SHOW |PRAGMA |CREATE |SET '` 从 `marivo/datasource/`、`marivo/semantic/`、`marivo/analysis/{compiler,materialization,session,evidence}/` 双向扫描，再用 `rg -n 'attribution_summary_sql|membership_integrity_sql|compile_event_bundle|integrity_sql|read_parquet|postprocess_sql' marivo` 追调用方。SQL 模板、控制、编译补丁、终端 DS02 和 Store 查询分开；实际提交链见 §1–§3。静态登记不能证明某条路线真实运行；除 DS02 外不批准内部 SQL 例外。R9 需重新运行同类扫描并审计 driver 实际提交；发现未在表内的生产入口即增行，不能按“metadata”或“adapter 内部”自动放行。

## R7.9 Event/Lifecycle SQL retirement amendment (2026-10-03)

The R7.9 acceptance audit (historical record in Git history) and its evidence
index supersede the historical current-consumer entries for AN02–AN10 and the
Event portion of AN23. AN02/AN03 replay/array compilers, AN08/AN09 lifecycle
integrity/bundle and AN10 statement summaries were deleted in R7.5/R7.6. R7.9
now physically removes AN04–AN06's three Event SQL modules and event_bundle,
and AN07's Event snapshot prefixes/bundle/collector callers. AN23's Event wrapper
and special source compilation route are removed. No renamed helper, private
Event compiler visitor, old packet statement or family executor replaces them.

Current unified methods use SourceSession-issued Ibis capture, explicit registered
local consumers and Store 7 typed parts/receipts. R7.2's issued/driver SQL and
resource counterexamples are staged into the installed candidate. The original
mandatory new method/native-route qualifications remain in the R7 audit; physical
retirement does not qualify those methods or a remote backend.

Generic scalar adapter lifetimes, PostgreSQL boolean-to-int4 cast lowering,
ClickHouse numeric widening and actual metadata/finite-value checks retain their
shared scalar/R8/R9 owners. AN07's remaining generic connection/setting duties and
AN23's generic adapter qualification are R9 responsibilities. This amendment closes
the exclusive Event/Lifecycle chain only, without asserting a global SQL audit,
remote-server acceptance or release qualification.


### R9.3 owned MySQL authoring deadline amendment (2026-10-06)

The user authorized `mysql.analysis.cancel_owned_query` (`KILL QUERY {thread_id}`)
for `datasource.authoring.deadline`, limited to `sample` / `raw_sql` and the
isolated reader's own current positive native thread ID. The existing parameter
range remains 1..18446744073709551615. A separate bounded same-reader-identity
control connection submits through the registered channel; the reader/control
connections are closed and timer work joined. Certification-only SET/read
purposes are unchanged. The PostgreSQL domain snapshot proposal was withdrawn;
no PostgreSQL BEGIN/ROLLBACK statements were added.
