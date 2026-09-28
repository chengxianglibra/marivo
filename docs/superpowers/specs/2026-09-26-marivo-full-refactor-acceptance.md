# 全量分析代数与 Analysis DSL 重构阶段验收主记录

Date: 2026-09-26

Status: R0.1–R0.4 与 R0.6 的静态产物已登记，R0.5 替代可行性仍阻塞；R1.1–R1.5 分项记录后已于 2026-09-28 执行整体验收复核：出口 1、3、5 在所测范围通过，出口 2 新链闭合但旧文本路线源码待删，出口 4 含 MySQL/ClickHouse timeout 精确阻塞格，R1 整体保持部分通过（见"R1 整体验收复核"节）；R1.6（2026-09-28 用户批准的 provider 语句通道例外）已恢复六后端丰富 metadata、四家主键/唯一约束与 DuckDB scoped 认证 HTTP，DS11/DS13 保持阻塞（见"R1.6 Provider 能力通道与丰富 metadata 恢复"节）；R2.1–R2.4 及后续静态交接有有界证据；R3.1 私有纯构造与 R3.2 私有注册有有界测试证据，R3.2 已接入 J1/core 重叠方法的语义 owner 修正见末节；R3.3 私有图与纯计划有有界测试证据；R3.4 私有 lowering、固定输入局部规律及 DuckDB 基础格有有界证据，完整阶段与公共执行仍未验收。R0、R1、R2 和 R3 整体均未验收。

本文件按[主计划](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)和[R0 实施文档](2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md)续记实际证据。历史验收不自动转成新 DSL 的技术、后端、安装包或真实 Agent 资格。

| R0 出口 | 状态 | 当前证据或缺口 |
| --- | --- | --- |
| R0.1 工作基线、输入 hash、历史证据边界 | **通过（索引与归档）** | [R0.1 索引](2026-09-26-marivo-full-refactor-r0-evidence-index.md)与 [manifest](evidence/r01/manifest.json)；S0/组合正文及 P4 小型机器结果已复制。S4 原轨迹与 wheel 为本机专有历史附件，未授予新资格 |
| R0.2 C01–C18 与消费者反查 | **通过（静态反查）** | [能力台账 §1–§4](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#1-本次可复核快照与读法)列出 C01–C18、44 个现有子单元、导出/Help、双执行链、主要消费者与去向；目标运行资格仍未验证 |
| R0.3 必需 owning spec 决定 | **通过（目标契约文档；C01.c 已按用户决定修订）** | [Analysis R0.3](../../specs/analysis/python-analysis-design.md#r03-accepted-full-algebra-target-inactive)、[Semantic R0.3](../../specs/semantic/semantic-object-model.md#r03-full-algebra-target-decisions-inactive)、[Datasource SQL 目标](../../specs/semantic/datasource-layer.md#r03-target-ibis-owned-analysis-reads-and-terminal-raw-sql)及[能力台账 §5](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#5-r03-接受的契约索引)：C18、权重/参照、普通 ratio、业务顺序、原状态/当前行及 SQL/provenance 已给 typed 目标、Cell/部件/错误/K/反例。`md.raw_sql`/`RawSqlResult`/Help 保留为终端 SQL 逃生通道，只读依连接/权限尽力控制，结果不能重入 Analysis；其他新 DSL 目标尚未实现 |
| R0.4 规则、方法、模块责任 | **通过（规则和迁移台账）** | [能力台账 §6](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#6-r04-六类元算子规则冻结)：六类元算子含 Pre、RequiredParts、PartTransform、Post、Transport、Eval；44 个子单元及展开方法记录版本、K、独立 oracle、阶段；§6.2 登记旧实现/消费者同迁删除点。新规则尚无产品执行证据 |
| R0.5 SQL/adapter/六后端目标资格 | **静态台账通过；替代可行性阻塞、完整运行未验证** | [SQL/adapter 台账](2026-09-26-marivo-full-refactor-r0-sql-ledger.md)列 DS01–DS21、AN01–AN33 的构造/调用/提交与 Ibis/驱动替代，Store SQLite 事务白名单、七项 adapter 责任及六后端目标矩阵。DS02 是具名公共终端 SQL 路线；其他内部 SQL 例外为空。后续 R1.3 对 PostgreSQL/Trino 的部分 DS11 控制取得实证，MySQL/ClickHouse timeout、DS15 认证、部分 metadata/时区及完整六后端方法/资源矩阵仍阻塞或未验证；不得将 R0.5 整体升级 |
| R0.6 破坏性变更与 R1/R2 交接 | **通过（静态清单与交接）** | [能力台账 §7–§8](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#7-r06-breaking-changes)给出 B01–B13 的旧使用处、目标入口/拒绝、owner/阶段、Help/CLI/site/测试消费者、R1/R2 首批格与独立反例；[Datasource R0.6 target](../../specs/semantic/datasource-layer.md#r06-public-connection-cutover-target)冻结 `md.connect` 公共原生 backend 的删除目标。本节下文记录快照、hash 与未闭合项；这是文档收口，不证明切换已实施 |

R0 整体不得标为通过。R0.1 没有改产品代码，也没有重跑旧 Runtime、安装包或 Agent；可移植原始 Agent 轨迹与 wheel 缺口在索引 §6 记录为历史待补证。R0.2 在 panda 的代码 HEAD 为 `d5e06022c7fcd355b2a31ab6935aea593b60f0d1`，只执行静态扫描并编写文档，未运行产品测试或远端后端。R0.3–R0.5 按用户指定直接在干净的 `panda` 起点实施，未创建隔离工作树，未改产品代码、packaged skills 或 `AGENTS.md`。后续各节的“R0.6 未开展”均为当时快照，以本表及末节的本次记录为当前状态。

## R1.1 实施中记录

起点仍为 `panda`、HEAD `1bd9b7e98a3c9f7878de73e07f89aeabdc740073`；R1 实施文档在起点为未跟踪文件，未由本工作修改。R0.6 的交接未完成，R0.5 控制/认证替代路线尚未实证，R0 整体仍未验收；R1.1 按可独立部分推进。

已落地：engine provider 按选中后端延迟装载；同一 provider 打开内部 `SourceSession`，会话签发的 Ibis 编译读取绑定来源、用途和 schema，伪造/跨会话句柄被拒；本地 DuckDB/SQLite 基础 scan/filter/project/group/count 的真实游标输入、空流 schema 与提前关闭已在定向测试覆盖。`md.test`、`test_no_persist` 与 Semantic source-health 的连接探测已改为 provider 提交 Ibis literal。旧 ClickHouse `postprocess_sql` 目标已删除。单表、无时间范围的 DuckDB/SQLite Population 扫描/过滤及 sum/count Metric 聚合（含直接维度分组）现由 `DatasetRuntime` 调用 `SourceSession` 编译、读取、验证后交给现有发布协议；Population 与基础 Metric 定向测试在旧 DuckDB `statement(sql)` 被钉死时仍可成功。其余旧 Dataset 来源步骤在 Run 与来源读取前结构化拒绝。共用 `ExecutionAdapter` 协议已删除 `statement(sql)`；旧 Runtime resolver 只保留 R4 固定 Artifact 的 DuckDB 实现，`prepared_source` 对旧来源绑定另有阻断。

尚未闭合：旧具体后端类还保留 `statement(sql)` 和 AN01–AN33 文本实现；其来源入口虽被阻断，仍需按迁移阶段删除。基础消费者仅获单表、无时间范围的本地资格；带时间范围、fold、mean、`metric.where` 与其他领域方法仍阻断。全量默认测试门禁仍因这些旧正例和以其建立 Artifact 的续算测试失败，R1.1 **未通过**。PostgreSQL、MySQL、Trino、ClickHouse 的基础读取和六后端表形态/精确类型/取消格均**未验证**；SQLite 只证明本地测试连接上的批次语义，不证明只读控制能力。`md.raw_sql` 仍为独立公共终端，但其完整 R1.3 边界尚未在本轮重新验收。不得把本节的 provider 注册或本地测试写成 R1 出口通过。

| 旧文本路线（SQL 台账） | R1.1 当前处置 | 后续迁移阶段 |
| --- | --- | --- |
| AN01 数值宏、AN31 时间 lowering | 基础单表 sum/count Metric 聚合已走 `SourceSession`；其余旧 Metric 来源路线在 Run 前阻断。旧具体后端文本实现尚未删除，共用 `ExecutionAdapter.statement` 已删除 | R5 方法表达式、R9 后端资格 |
| AN02–AN09 Event/Lifecycle replay、bundle 与检查 | 来源型旧 Dataset 路线在 Run 与来源读取前阻断；旧实现尚未删除 | R7 方法实现，R9 后端资格 |
| AN10 Lifecycle history 汇总 | 来源型旧 Dataset 路线已阻断；旧私有检查尚在 | R7 方法实现 |
| AN11–AN12 归因 SQL 校验 | 来源型旧 Dataset 路线已阻断；旧文本构造和提交尚在 | R6 方法实现，R9 后端资格 |
| AN13 来源计数 | `local_stage._source_count` 已改为 Ibis count 表达；基础单表 count 聚合由新 session 读取，其他旧 Dataset 来源路线仍在 Run 前阻断 | R5/R9 复核实源资格 |
| AN14 私有成员完整性 | DuckDB 手写 SQL 已改为 Ibis 检查；旧 adapter 的其他文本入口仍在 | R4/R9 复核 receipt 与实源资格 |
| AN15–AN30、AN32 控制、metadata、值校验和认证 | 旧 Dataset 来源路线在 Run 前阻断；其他控制和 metadata 使用点未闭合，不转授本地基础读资格 | R1.2/R1.3；涉及领域校验再由 R9 复核 |
| AN33 共用 `statement(sql)` | `ExecutionAdapter` 协议已删除该方法；来源 resolver 不再返回旧具体后端，只有 R4 固定 Artifact 的 DuckDB 实现仍可由旧 resolver 获得。旧具体后端的文本方法和直接测试仍在，未算完整删除 | R1.1 清理旧具体类；R4 接收新传输契约 |

本次本地证据固定在 `panda` HEAD `1bd9b7e98a3c9f7878de73e07f89aeabdc740073` 的未提交工作树：代码、测试及受影响文档的受跟踪改动（不含本验收文件）的 `git diff --binary HEAD` SHA-256 为 `63fdb1c25f7353ffcf021d307dfc4669361e27e10626614ef1a4269c9ab94789`；新增 `adapters.py`、`basic_source.py`、契约测试和旧路线阻断测试的文件 SHA-256 依次为 `a2a3306ad7b742772d9db73a1addab3a8065e65c2f7df803ca329d3ed3da8ce0`、`79bb966d62a252eb1013076a2f8ff1e25e291e0a3a2e9fb49bd8b32bb58ccf6b`、`3a88c1eb9257cc2e3f048de574eafc271847414016c9ca602f17c83174d90e4b`、`04364a307c283e511a876b01d56197803a3a516660e8e13bd079fce526fd35df`。这些 hash 只定位本次候选，不等于阶段验收。

实际执行：本轮 `make test TESTS='tests/test_lazy_contract_source_admission.py tests/test_datasource_adapter_contract.py tests/test_lazy_inspection_boundaries.py tests/test_lazy_backend_dispatch.py tests/test_lazy_postgres_errors.py'` 为 65 通过，覆盖基础 Population/Metric 的静态准入披露、来源身份、嵌套值精确解码和旧 resolver 边界；`make runtime-test TESTS='tests/test_r11_legacy_domain_block.py'` 为 11 通过，包含旧 Event/Lifecycle/Attribution/非基础 Metric/Association 阻断，以及 Population 扫描/过滤、sum/count Metric 聚合与直接维度分组的会话读取。前轮其他 datasource/raw SQL 定向结果仍列在本节较早候选，不能替代这轮门禁。最新 `make check-agent` 的 lint/import contract 与 379 个源码文件 typing 通过，默认测试在 12 个失败后停止：4636 通过、4 跳过、12 失败；失败分布于时间范围/日历、fold、mean、`metric.where` 等旧来源正例及依赖其 Artifact 的续算测试，均在 Run 前 `source_admission` 阻断，API 文档阶段未执行。单独 `make docs-api-agent` 与 `npm run build --prefix site` 成功（Astro 检查 0 错误、0 警告，321 页构建），`git diff --check` 通过。现场版本为 Ibis 12.0.0、DuckDB 1.5.3、SQLite 3.53.1、PyArrow 25.0.1。独立预期由固定的两行大整数、分组计数、空输入、伪造/跨会话编译句柄、提前关闭和异常清理断言提供；精确解码另以小数转整数、int64 溢出、float32 降精度、float→Decimal、嵌套 bool 值类型改变的拒绝及 Decimal 保真为反例。新增 Population 与基础 Metric 测试钉死旧 DuckDB `statement(sql)`；Population 用例还比对 Runtime 观察到的提交 SQL 与 `CompiledRead.sql`，并检查 session 关闭；远端中断只有 `remote_unknown`，未宣称已终止。`md.raw_sql` 仍走独立终端且结果不能绑定到新 adapter，已由定向用例验证。此证据仅覆盖本地 DuckDB/SQLite 基础形状；没有六后端远端服务运行、表形态矩阵或 R0.5 控制/认证等价能力结论。

## R1.2 六后端基础读取实施中记录

本轮以 `panda` HEAD `680f5834f51d16c9dfed5185064c6c5641a5a202` 和其未提交的 R1.1 工作树为基线；R1 实施计划已补记其他后端的单列标量启用条件及旧测试恢复要求，文件 SHA-256 为 `d940a80e4605cb6c39937f333b40f4a42de587f44f5d4d1c0fc2b26aedcbd17d`。代码、测试、owning spec 的提交前 `git diff --binary 680f5834f51d16c9dfed5185064c6c5641a5a202 -- marivo tests docs/specs` SHA-256 为 `be87abdc39976ee480ebcf023455330a30e9319d7055bab3059823840af563cf`，新增真实后端用例 `tests/test_r12_source_adapters_runtime.py` 文件 SHA-256 为 `dd2e05140f22f65a0fb25468ec076aa409df68174a5f616228251ead6390433f`。本节记录的是可复核的提交前候选，**R1.2 尚未验收**。

`SourceSession` 现绑定六后端普通表/view、DuckDB CSV/Parquet/本地 JSON/无凭据 HTTP JSON；按实际列验证投影、来源身份与 JSON 参数，并拒绝额外物理叶子、缺失参数、伪造或跨会话编译句柄。单一 session 的多表表达式逐一签发 binding；跨 datasource authoring preview 在连接前拒绝。六后端从原生游标/流取得固定 schema 的 Arrow 批次，解码拒绝类型改变、溢出、非有限 float、失真 Decimal 与时间漂移；MySQL 无效日期文本不会被驱动静默改为空值。DuckDB 的结果仍属连接所有，关闭记录为 `connection_owned`，直到 session 断连；远端 `interrupt()` 只记 `remote_unknown`。`md.inspect` 的 schema 绑定、有限 sample、snapshot、Semantic preview、source-health 业务行和 Ibis literal 连接探测已迁到来源所有者；metadata、样本、连接探测与业务校验各自只有其实际观察范围。Trino `$partitions`、ClickHouse `system.tables`/`system.parts` 分区值路线也改为绑定 Ibis 表达式。基础 Analysis 的完整复合键校验继续执行；重复键被拒，未用 `distinct` 消除。

下表每格的 SQL 对照由 `tests/test_r12_source_adapters_runtime.py::_assert_table_read` 对**实际** `session.submissions[-1].sql == read.sql` 断言；`read.sql` 是同一绑定表达式的 Ibis `backend.compile(..., limit=None)` 返回值，并非手写预期 SQL。每格另检查一行批次、空结果 schema、提前关闭 `closed_early`/`cursor_state=closed`、session 断连；`test_datasource_adapter_contract.py` 独立检查伪造 SQL、额外来源、类型漂移、异常清理和 DuckDB 连接所有状态。表中输入/预期是独立常量断言，SQL 相等本身只证明未改写提交，不证明业务语义。命令均在该独立 multisource 环境以 `RUNTIME_WORKERS=1` 串行运行，启动前复核状态，仅停止本轮启动的服务；结束时所有远端容器为 Exited，卷未清理。

| 后端/形态 | 输入与独立预期、实际命令 | SQL/资源与资格状态 |
| --- | --- | --- |
| DuckDB 表/view、CSV/Parquet/JSON、HTTP JSON | 本地 adapter、JSON 与 authoring 定向测试：两行/投影和文件绑定；HTTP POST 体与一次请求独立断言；空结果 schema 不从首批推断 | **通过本地基础读取**。提交等于 Ibis 编译产物；提前关闭是 `connection_owned`，断连后 `connection_disconnected=true`。带凭据 HTTP 仍由 R1.3 限制 |
| SQLite 表/view、存储型 | 本地 adapter 与复合键 Runtime：`(2**63,1)`、`(2**64-1,1)` 完整保留，重复 `(2**64-1,1)` 拒绝且无 Store 资源；非声明存储型拒绝 | **通过本地基础读取**。空流 schema/关闭/异常在定向契约覆盖；SQLite query-only 控制资格仍属 R1.3 |
| PostgreSQL 17.11，`public` namespace 表/view | `MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_r12_source_adapters_runtime.py::test_postgres_table_view_namespace_and_exact_decimal' RUNTIME_WORKERS=1`：`(1,10.25),(2,20.50)` 精确 Decimal，1 通过；独立环境验证只读账号不可建表/写入 | **通过所测基础形态**。每个表/view 实际提交等于各自编译 SQL；空流、提前关闭与断连断言通过；远端取消 `remote_unknown` |
| MySQL 8.4.11，InnoDB 表/view | `MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_r12_source_adapters_runtime.py::test_mysql_table_view_and_exact_decimal tests/test_r12_source_adapters_runtime.py::test_mysql_invalid_date_and_read_only_permission' RUNTIME_WORKERS=1`：同两行 Decimal；`0000-00-00` 拒绝，合法 `2026-09-26` 保留，读账号 CREATE 被拒；2 通过 | **adapter 基础读取通过**，同样 SQL/空流/提前关闭/断连对照通过；单列基础 Analysis 见下方补充格，多列主键仍在 Run 前阻断 |
| Trino 483，Iceberg 与 non-Iceberg 表/view | `MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_r12_source_adapters_runtime.py::test_trino_iceberg_and_non_iceberg_forms tests/test_r12_source_adapters_runtime.py::test_trino_iceberg_partition_metadata_uses_bound_read' RUNTIME_WORKERS=1`：`(1,10.25)` Decimal，两 catalog 表/view；Iceberg `day=2026-09-26/27` 升序分区值；2 通过，独立环境确认四类写操作被拒 | **所测形态通过**，同样 SQL/空流/提前关闭/断连对照通过；`$partitions` 是单独绑定 metadata 表，Hive connector 的 `$partitions` 实源仍未验证；远端取消 `remote_unknown` |
| ClickHouse 26.3.33.24，MergeTree 表/view、双分片 Distributed | `MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_r12_source_adapters_runtime.py::test_clickhouse_mergetree_exact_decimal tests/test_r12_source_adapters_runtime.py::test_clickhouse_partition_catalog_uses_bound_read' RUNTIME_WORKERS=1`：两行 Decimal 与 `20260926/27` 分区值，2 通过；`MARIVO_CLICKHOUSE_CLUSTER_TEST=1 make runtime-test TESTS='tests/test_r12_source_adapters_runtime.py::test_clickhouse_distributed_reads_both_shards' RUNTIME_WORKERS=1`：两分片合计 5 行，1 通过 | **所测形态通过**，同样 SQL/空流/提前关闭/断连对照通过。读账号缺 `SHOW COLUMNS ON system.tables`，该可选分区 metadata **unavailable**；有权限的账号 Ibis 分区读取通过；远端取消 `remote_unknown` |

MySQL 基础 Analysis 补充格：启动前 `mysql-analysis` 为 Exited，本轮只启动并停止该组。`MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_mysql_runtime.py::test_group_a[population] tests/test_lazy_mysql_runtime.py::test_single_key_basic_metrics_use_source_session tests/test_lazy_mysql_runtime.py::test_single_key_view_population_uses_source_session tests/test_lazy_mysql_runtime.py::test_single_key_population_rejects_invalid_identity tests/test_lazy_scalar_type_runtime.py::test_uint64_composite_identity_validation' RUNTIME_WORKERS=1` 为 9 通过、2 个未启用 ClickHouse 跳过。单列主键的 Ibis 标量 `entity_identity` 经来源批次读取后重建为单字段 Arrow struct；表/view Population 预期身份 `(1,)` 到 `(6,)`，sum/count 分别为 `1058.5`、`5`；重复和 Null 身份均拒绝且无 Artifact/Store 资源。多列 MySQL 主键仍因 Ibis 12 `StructColumn` 无编译规则在 Run 前阻断，`last_run_ref` 为空且无 Store 资源。该补充格只授予所测 MySQL 单列基础形态资格；其他五后端继续使用有序 Ibis struct，单列标量启用条件已记入后续 R9 实施项。

本地定向 `make test TESTS='tests/test_datasource_adapter_contract.py tests/test_datasource_json_source.py tests/test_datasource_authoring_acquisition.py tests/test_datasource_authoring_inspection.py tests/test_datasource_authoring_store.py tests/test_datasource_connection_timeout.py tests/test_datasource_profiles_registry.py tests/test_datasource_trino_partition_hook.py tests/test_datasource_clickhouse_partition_hook.py tests/test_semantic_source_health.py tests/test_semantic_scoped_preview.py tests/test_lazy_contract_source_admission.py tests/test_doctor.py'` 为 278 通过；追加 `make test TESTS='tests/test_lazy_compiler.py tests/test_lazy_contract_source_admission.py tests/test_datasource_adapter_contract.py'` 为 73 通过。`make runtime-test TESTS='tests/test_r11_legacy_domain_block.py tests/test_lazy_scalar_type_runtime.py::test_uint64_composite_identity_validation' RUNTIME_WORKERS=1` 为 13 通过、4 个未启用远端跳过。`make typecheck` 为 379 个源码文件通过，`make lint-agent` 与 `make docs-api-agent` 通过，`git diff --check` 通过。暂停旧正例前 `make check-agent` 的 lint/import/typing 通过，默认测试于 11 失败后停止：4415 通过、11 失败；失败均为 R1.1 已记录的日历、fold、mean、`metric.where` 等旧来源正例被 `source_admission` 阻断，故 API 文档阶段另行执行且通过。此失败不转写为 R1.2 通过，也不抹去 R1.1 原 12 失败记录。

**未闭合格：**DS03–DS07、DS09 的可选 provider metadata hook 仍有内部 SQL，不能宣称同一来源提交路线完全闭合；只读权限不足可标 unavailable，准入必需事实缺失必须拒绝。Trino Hive `$partitions`、六后端适用的 Null/非有限 float/时间精度实源矩阵、远端异常/取消后的服务器终止证明、MySQL 多列主键基础 Analysis、其他五后端单列标量启用、认证 HTTP 与控制/凭据替代尚未取得资格；后两项依计划归 R1.3。AN19–AN29 旧领域路径仍按 R5–R9 准入门阻断，不能借此次 adapter 通过转授方法资格。没有新 wheel 或真实 Agent 验收证据，R1 阶段仍未通过。

### 2026-09-27 提交门禁与待恢复测试

用户要求将当前失败的旧来源正例暂时置灰，待依赖任务完成后恢复。首次正常提交 hook 的 pytest 在 4148 通过、3 失败时提前停止；随后定向完整运行发现 13 个失败参数组合，首次全量复跑又发现 retained 用例 1 个。以下 14 项均保留原测试体和独立断言，只在对应函数加 `pytest.mark.skip`，不是通过、修复或 R1/R5 准入证据：

| 测试函数 | 暂跳过参数数 | 恢复前提 |
| --- | ---: | --- |
| `test_lazy_source_algebra.py::test_semantic_calendar_validations_publish_each_required_occurrence` | 2 | R5 时间范围 Metric 与认证日历校验发布来源路径 |
| `test_sqlite_semantic_integration.py::test_sqlite_agent_native_authoring_journey` | 1 | R5 SQLite 时间范围 Metric 来源聚合 |
| `test_lazy_local_placement.py::test_required_parts_place_locally_without_worker_or_origin_work` | 1 | R5 mean Metric 来源执行与 retained 部件发布 |
| `test_lazy_local_placement.py::test_diagnostic_version_does_not_change_selection_or_execution` | 4 | R5 `metric.where` 来源执行 |
| `test_lazy_status_fold_admission.py::test_sqlite_fold_spatial_sum_matches_hand_computed_constants` | 5 | R5 SQLite 时间 fold 与空间聚合来源执行 |
| `test_lazy_retained_compiler.py::test_retained_filter_aggregate_joins_exact_component_parts` | 1 | R5 多根 Metric 来源执行与 retained 部件发布 |

依赖方法完成时移除对应 skip 并运行原独立预期和 `make check-agent`，不能把当前置灰结果作为验收通过。置灰后定向为 14 skipped；最新 `make check-agent` 的 lint、import、379 文件 typing、默认测试和 API 文档阶段全部通过，默认测试为 5186 passed、18 skipped，其中 14 项为本表新置灰，其他 4 项为原有跳过。R1.1 原全量失败记录和 R1/R5 未验收状态保持可见。

## R1.3 SQL、控制与凭据边界候选记录（2026-09-27）

基线为 `panda` HEAD `a2a7caf8e6e738651eb9edc52e861ee2c53b380b`；本节为该基线上未提交的 R1.3 候选。R1.2 尚未验收，R1.1 旧领域来源准入仍有已记录缺口，因此 **R1 整体未通过**。用户在实施中补充两项决定：`md.raw_sql` 不解析/归类输入 SQL，只读依连接及后端权限尽力控制，无法保证只读本身不阻断；删除 `ms.from_sql` 专用属性/入口，历史 SQL 的说明放 `ai_context`。R0.5 台账记录的是迁移前调用点，以下逐行记录本轮实际处置。

| SQL 台账行 | 当前候选处置与代码证据 | 状态及边界 |
| --- | --- | --- |
| DS01、DS18 | `adapters._probe_backend` 构造 `ibis.literal(1).name("probe").as_table()`，以 `backend.compile(..., limit=None)` 的结果向 native cursor 提交；`md.test` 和 source-health 消费同一 provider 探测。`test_governed_probe_submits_exact_ibis_compilation` 捕获本地真实 DuckDB 提交并逐字比较编译结果 | **DuckDB 通过**；SQLite 的原有连接探测回归通过；四个远端在本轮 **未验证** |
| DS02 | `manage.raw_sql` 仅校验非空 SQL、理由和正数界，原文交给独立 `backend.raw_sql`；游标只取 `limit+1`、关闭并披露截断/成本/只读尽力控制。timeout 控制在用户 SQL 前失效时报告 `query_executed=False`。`RawSqlResult` 与其 `to_pandas()` 副本不得绑定 Semantic/Analysis；错误只展示异常类型，带结构化 repair | **DuckDB、SQLite、PostgreSQL、Trino 所测控制通过**：DuckDB 写入被只读连接拒绝，SQLite 递归查询被 interrupt；PostgreSQL `pg_sleep(3)` 被 1 秒 statement timeout 中断且 CREATE TEMP 被拒；Trino 1 秒 session timeout 实际返回 `EXCEEDED_TIME_LIMIT`，CREATE 被服务端拒绝。MySQL 因无可执行 timeout **阻塞**；ClickHouse 只读账号的 `max_execution_time` 不可改，用户 SQL 前 **阻塞**。只读能力本身不作为阻断条件 |
| DS03–DS07、DS09 | 六个 profile 的文本 metadata 查询与 `metadata._query_rows` 已移除；`schema_only_metadata_inspect` 返回绑定 Ibis schema，注释、分区、主键等可选事实披露 unavailable。必要事实缺失的来源形态不得获准入 | **DuckDB、SQLite schema-only 本地通过**；远端新 metadata 路径 **未验证**。原有 45 个丰富 metadata 断言保留测试体并显式 skip，恢复条件是合格公开 metadata API 或 Ibis 来源事实及真实后端证据，不能计为通过 |
| DS08、DS10、DS17、DS21 | Trino `$partitions`、ClickHouse `system.tables`/`system.parts` 与受治理业务值读取沿用 R1.2 的绑定 Ibis 来源路线；本轮没有重授领域资格 | PostgreSQL、MySQL、Trino、ClickHouse 的 R1.2 定向实源读取回归分别 **1、2、2、2 通过**；Trino/ClickHouse 分区绑定读取在所测形态通过。其余远端 metadata 组合未验证 |
| DS11–DS14 | PostgreSQL 终端连接以 `statement_timeout` 和 UTC 参数、driver `read_only`/rollback 配置；Trino 以连接 `timezone` 和 `session_properties` 配置；MySQL 无可执行 timeout，`md.raw_sql` 在连接前拒绝。SQLite 用驱动 authorizer 拒绝写入；旧 `timezone_probe_sql`、`readonly_tx_start` profile 字段已删除，旧隔离执行类转向 driver timezone 事实 | **DuckDB/SQLite、PostgreSQL、Trino 所测控制通过**；PostgreSQL 服务端 `SHOW statement_timeout=1s`、driver TimeZone=UTC，Trino 服务端 `SHOW SESSION query_max_run_time=1s`、`current_timezone()=UTC` 且实际 timeout。ClickHouse timeout 不可设置而 **阻塞**；MySQL timeout 与实际驱动无法提供的 MySQL/ClickHouse 时区必需格 **阻塞**。DS14 方言后处理钩子仍保持删除 |
| DS15、DS16、AN32 | DuckDB HTTP bearer/header 凭据在秘密解析和连接前拒绝；删除内部 `CREATE SECRET` 与 `SET force_download`。`force_download` 作为连接参数传入 | 认证 HTTP **阻塞**；无凭据 HTTP 的 R1.2 证据保留。真实 DuckDB `current_setting` 返回 UTC、1 thread、`force_download=true`，故 DS16 本地设置 **通过**；远端 HTTP 凭据形态未尝试 |
| DS19、DS20 | 删除执行 provenance SQL 的 `ms.parity_check`、`ParityResult`、`ParityStatus`、Catalog 状态与 `verification_mode`；删除 `ms.from_sql`、`SqlProvenance`、执行改写函数。`ai_context` 可记历史背景，readiness 披露未验证并指向独立业务来源或受治理 Ibis 参照 | **公开面/静态入口与定向测试通过**；本阶段没有替代 parity API，也不宣称历史 SQL 已校验 |
| AN15、AN30 | 新 DuckDB adapter 的线程/时区连接设置有真实运行断言；Trino/ClickHouse 新 provider 控制使用驱动 session properties/settings。旧领域方法的控制/SQL 路径仍受 R1.1 Run 前阻断，不接入 `SourceSession` | **DuckDB、Trino 所测新控制通过**；ClickHouse 只读账号的 timeout 控制前置 **阻塞**，其时区事实亦无法由当前 driver 证明；远端取消/恢复 **未验证**。旧 AN01–AN33 领域实现的剩余文本路径不获 R1.3 资格，依 R5–R9 逐格清理 |

本轮本地版本：Ibis 12.0.0、DuckDB 1.5.3、SQLite 3.53.1、PyArrow 25.0.1。实际命令 `make test TESTS='tests/test_r13_control_boundaries.py tests/test_datasource_raw_sql.py tests/test_datasource_engine_profiles.py tests/test_datasource_profiles_backends.py'` 为 **77 passed**；追加 `make test TESTS='tests/test_r13_control_boundaries.py'` 为 **4 passed**，包括真实 SQLite interrupt。首次最终 `make check-agent` 为 5098 passed、64 skipped、1 failed：旧 `test_lazy_postgres_errors` 要求无驱动时区事实时回退系统时区，与本次拒绝规则冲突；改为断言结构化拒绝后，`make test TESTS='tests/test_lazy_postgres_errors.py tests/test_r13_control_boundaries.py'` 为 9 passed。随后及新增远端实测后的最终 `make check-agent` 均通过 lint/import、378 个源码 typing、默认测试（**5099 passed、64 skipped**）与 API 文档；`npm --prefix site run build` 通过，Astro 构建 321 页。`make runtime-test TESTS='tests/test_r12_source_adapters_runtime.py tests/test_lazy_source_runtime_acceptance.py'` 在未启用远端 opt-in 时为 8 skipped、1 failed；失败是 R1.1 旧 `metric.limit` 来源续算的 `source_admission` 缺口，不计作 R1.3 新回归。

默认 Docker context 的 `/var/run/docker.sock` 不存在；发现 `colima-marivo-multisource` profile 正运行后，改用仓库 `tests/multisource_environment/manage.sh` 启停专用服务。起始所有服务 Exited；本轮仅按顺序启动 PostgreSQL analysis、Trino（含 catalog PostgreSQL）、ClickHouse、MySQL analysis，退出后 `docker --context colima-marivo-multisource ps -a` 证实它们全部恢复 Exited，卷未清理。实际远端命令与结果：

| 服务及版本 | R1.3 控制/负例与 R1.2 读取回归 |
| --- | --- |
| PostgreSQL 17.11 | `MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_r13_control_boundaries.py::test_postgres_terminal_control_uses_server_timeout_and_read_only' RUNTIME_WORKERS=1`：1 passed；服务端 `SHOW statement_timeout=1s`，`pg_sleep(3)` 中断，driver UTC/read_only，CREATE TEMP 拒绝，失败后 SELECT 1 可用。`tests/test_r12_source_adapters_runtime.py::test_postgres_table_view_namespace_and_exact_decimal`：1 passed |
| Trino 483 | `MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_r13_control_boundaries.py::test_trino_terminal_session_timeout_and_timezone_are_effective' RUNTIME_WORKERS=1`：1 passed；服务端 session `query_max_run_time=1s`、UTC，重查询报 `EXCEEDED_TIME_LIMIT`，CREATE 被拒。R1.2 Iceberg/non-Iceberg 表/view 与 Iceberg 分区 metadata 两项：2 passed |
| ClickHouse 26.3.33.24 | `MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_r13_control_boundaries.py::test_clickhouse_unsettable_timeout_blocks_before_user_sql' RUNTIME_WORKERS=1`：1 passed；只读账号的 `max_execution_time` 为不可修改，`md.raw_sql` 记录 `query_executed=False`。R1.2 MergeTree 表/view 与 system.parts 分区读取两项：2 passed |
| MySQL 8.4.11 | `MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_r13_control_boundaries.py::test_mysql_unverifiable_timezone_blocks_time_sensitive_read tests/test_r12_source_adapters_runtime.py::test_mysql_table_view_and_exact_decimal tests/test_r12_source_adapters_runtime.py::test_mysql_invalid_date_and_read_only_permission' RUNTIME_WORKERS=1`：3 passed；驱动无可验证时区时结构化拒绝，读账号不能 CREATE；基础表/view 与无效日期负例回归通过 |

这些命令逐一验证连接设置、实际服务端 timeout/权限或前置阻断，不把 mock 当远端资格。远端取消后服务器终止仍未验证；必要 metadata 丰富事实仍 unavailable，R1.2 整体状态不变。

## R1.4 消费者、披露和阶段收口记录（2026-09-27）

本轮起点 `panda` HEAD `a452a57f820924d52930adb3b5381f73aac7c1e6`，工作树原先干净。受影响的 Help、公共 docstring、Datasource/Semantic/Analysis owning spec 与 `site/` latest 中英文文档候选（不含本验收记录）的 `git diff --binary HEAD -- <12 个修改文件>` SHA-256 为 `d69f1892f07f92186aef645d5ae4b94fbc84f05d892e2f4eb89992e336f3e943`。此 hash 定位未提交候选，不是产品或阶段通过证明。本轮没有新公共导出、Help target 或函数签名；原有导出快照、Help reachability/drift/预算用例由全量默认测试重新执行。`marivo doctor` CLI 仍只提供静态声明检查和显式连接探测，没有新增 Dataset 方法准入命令；本轮未改 CLI 行为或 packaged skills。

消费者反查：`md.inspect`/sample/snapshot、Semantic preview/source-health 和 `md.test` 仍消费 R1.2/R1.3 的绑定来源读取或 Ibis literal 探测；metadata 可选事实缺失继续以 unavailable 披露。基础单表 Population 与无时间范围 sum/count Metric 消费 `SourceSession`；旧 Event/Lifecycle/Attribution、mean、时间范围、rank/limit 等来源路线在 `source_admission` 阻断，旧具体执行类的文本方法仍在源码中，按下表交给 R5–R9。固定 Artifact 续算是单独准入的 R4 路线，不借旧来源 SQL 兜底。`md.connect` 公开返回原生 Ibis backend，调用者可以直接 `raw_sql`，不受 `md.raw_sql` 的 reason、行界和 timeout 约束；Help、docstring 和 Datasource owning spec 已明确披露。这与 R1 “唯一公开原始 SQL 终端入口”的出口相冲突，**R1 阻塞**，后续须收紧或移除这一公共旁路并迁移其消费者，不得仅改措辞算通过。

| C01 子单元 | 本轮结论 | 证据边界与恢复条件 |
| --- | --- | --- |
| C01.a typed 连接、来源与 metadata | **部分通过 / 阻塞** | R1.2 六 provider 的实测基础形态见上表；schema-only 使可选注释、PK、物理估计等 unavailable，45 个原丰富 metadata 断言仍跳过。必需 metadata 缺失的形态拒绝；需合格 metadata API/Ibis 事实及真实后端反例后恢复 |
| C01.b Ibis 编译、提交、解码与资源 | **部分通过 / 未验证** | R1.2 记录了各所测形态 `session.submissions[-1].sql == read.sql`、独立行/Decimal/身份预期、空流与早关闭；远端终止、六后端适用 Null/非有限数/时间精度全矩阵及额外形态未验证。旧来源具体类仍有文本提交，不能宣称 R1 出口闭合 |
| C01.c 终端 raw SQL | **部分通过 / 阻塞** | R1.3 的所测 DuckDB/SQLite/PostgreSQL/Trino 超时、权限、截断、错误与 typed reentry 证据保留；MySQL 缺可执行 timeout、ClickHouse 只读账号不能设置 timeout，所测形态阻塞；`md.connect` 公共 raw backend 旁路仍在，唯一入口条件不成立 |

| SQL 台账 ID | 当前处置与状态 | 尚缺的精确证据或负责阶段 |
| --- | --- | --- |
| DS01 | **所测通过**：`md.test` 的 Ibis literal 提交；本轮本地回归 | 四远端 R1.3 连接探测未在本轮重跑 |
| DS02 | **部分通过 / 阻塞**：`md.raw_sql` 是受控终端结果；公共 `md.connect` 仍可直接调用 backend SQL | 移除或约束公共旁路；MySQL/ClickHouse timeout 格见 C01.c |
| DS03 | **schema 通过 / 丰富 metadata 阻塞**：DuckDB 可选注释、约束等 unavailable | 恢复原断言并取真实 API/表达式证据 |
| DS04 | **schema 路线已迁 / 远端未验证**：PostgreSQL 注释、分区等 unavailable | namespace/权限/丰富事实真实正反例 |
| DS05 | **schema 路线已迁 / 远端未验证**：MySQL 列/分区丰富事实 unavailable | 表/view 与只读账号 metadata 正反例 |
| DS06 | **schema 通过 / 丰富 metadata 阻塞**：SQLite 索引、约束等 unavailable | main/view/attached 原断言恢复 |
| DS07 | **schema 路线已迁 / 部分未验证**：Trino Iceberg/non-Iceberg 基础读取已测，丰富 metadata unavailable | Hive `$partitions` 及 connector metadata |
| DS08 | **所测通过**：Trino Iceberg `$partitions` 绑定 Ibis 实源读取 | Hive 形态未验证 |
| DS09 | **schema 路线已迁 / 部分阻塞**：ClickHouse 可选拓扑和 `projectable_columns` unavailable | 只读权限和 Distributed 物理事实 |
| DS10 | **所测通过**：ClickHouse `system.parts` 绑定读取 | 其他权限/分区形态未验证 |
| DS11 | **部分通过 / 阻塞**：PostgreSQL/Trino timeout 已实测 | MySQL 无可执行 timeout；ClickHouse 只读账号不能设置 timeout |
| DS12 | **所测通过**：SQLite driver authorizer 写入拒绝、终端 interrupt | 其他 SQLite 配置未转授 |
| DS13 | **部分通过 / 阻塞**：PostgreSQL/Trino UTC 有服务端事实 | MySQL/ClickHouse 必需时区事实无合格 driver API |
| DS14 | **通过静态删除**：`postprocess_sql` 钩子无生产定义 | 旧领域文本执行仍属 AN 行 |
| DS15 | **阻塞**：带认证 DuckDB HTTP 在秘密解析前拒绝 | 合格认证 API 与真实脱敏/HTTP 反例 |
| DS16 | **所测通过**：DuckDB `force_download` driver 设置实测 | 不转授 DS15 认证形态 |
| DS17 | **部分通过 / 未验证**：source-health 基础业务值检查走绑定读取 | 六后端关系/Null/重复完整正反例未齐 |
| DS18 | **所测通过**：source-health 共用 Ibis literal 探测 | 四远端本轮未重跑 |
| DS19 | **通过静态删除**：`ms.parity_check`/结果状态入口移除 | 历史 SQL 不作为运行 oracle |
| DS20 | **通过静态删除**：`ms.from_sql`/执行改写入口移除 | 历史说明只留 `ai_context` |
| DS21 | **部分通过 / 未验证**：sample、snapshot、preview 共用绑定来源 | 所有六后端作用域/空流/资源组合未齐 |
| AN13、AN14 | **所测通过**：基础 count 与私有成员完整性 Ibis 路线 | R4/R9 实源、receipt 与方法复核 |
| AN15、AN16、AN17 | **部分通过**：新 DuckDB 设置、schema、文件读取；旧具体执行类仍有 `statement` | R4 清旧固定路线文本；R9 扩展形态 |
| AN19、AN20 | **部分通过 / 阻塞**：MySQL 基础表/view、无效日期；旧日期文本校验仍在具体类 | R5/R9 迁移所有旧来源消费者；时区见 DS13 |
| AN21、AN22 | **部分通过**：SQLite 基础来源及存储型拒绝；旧具体类仍有文本 schema/控制 | R4/R9 清理与实源复核 |
| AN23、AN24、AN25 | **部分通过 / 未验证**：PostgreSQL 新基础读取有原样编译提交；旧具体类保留 schema/值检查文本 | R5–R9 方法迁移与数值矩阵 |
| AN26、AN27 | **部分通过 / 未验证**：Trino 新基础读取有原样编译提交；旧具体类保留 schema/非有限值文本 | R5–R9 方法迁移与 Iceberg/non-Iceberg 类型矩阵 |
| AN28、AN29、AN30 | **部分通过 / 阻塞**：ClickHouse 新基础读取和分区值已测；旧具体类保留 metadata/值校验文本，timeout 控制不可设 | R5–R9 迁移、控制 API 和取消实证 |
| AN31、AN32 | **部分通过 / 阻塞**：基础数值/时间 Ibis 路线；带认证 HTTP 仍拒绝 | R5/R9 领域时间 lowering、DS15 认证 API |
| AN33 | **新链通过 / 旧类阻塞**：`SourceSession` 拒伪造编译句柄、原样提交；旧具体 `statement(sql)` 仍可在源码中调用 | R4–R9 删除旧类文本入口，重做调用反查 |

本轮本地版本 Ibis 12.0.0、DuckDB 1.5.3、SQLite 3.53.1、PyArrow 25.0.1。`make test TESTS='tests/test_datasource_live_help.py tests/test_unified_help.py tests/test_datasource_raw_sql.py tests/test_datasource_profiles_registry.py tests/test_public_surface.py tests/test_r13_control_boundaries.py'` 为 **122 passed**；最终 Help 文案调整后 `make test TESTS='tests/test_datasource_live_help.py tests/test_unified_help.py tests/test_agent_api_drift.py tests/test_public_surface.py'` 为 **87 passed**。`make runtime-test TESTS='tests/test_r11_legacy_domain_block.py tests/test_r12_source_adapters_runtime.py tests/test_r13_control_boundaries.py' RUNTIME_WORKERS=1` 为 **11 passed、12 skipped**，跳过远端 opt-in，不能算远端重验。`make check-agent` 的 lint/import、378 个源码文件 typing、默认测试 **5099 passed、64 skipped**、Sphinx API 文档全部通过；64 个 skip 不计验收通过，其中 14 个 R5 旧来源正例和 45 个丰富 metadata 断言保留原断言与恢复条件。文案调整后 `make docs-api-agent` 仍通过；`npm --prefix site run build` 成功，Astro 321 页；`git diff --check` 通过。静态 `rg` 仍发现 `marivo/analysis/materialization/{scalar_sql_execution,duckdb_execution,postgres_execution,mysql_execution,sqlite_execution,trino_execution,clickhouse_execution}.py` 的旧 `statement`/`submit` 路线，以及 `compiler/driver_numeric.py` 的旧宏提交；这是 R1 第 2、5 项出口不能通过的直接反例。本轮没有重启远端服务、wheel 安装、真实 Agent 旅程或远端取消终止证据，R1 整体保持 **未通过**。

## R1.5 公共连接切换与剩余格复核（2026-09-27）

基线为 `panda` HEAD `eedb506f560f14ec10eee7059990d00b6931a879`；两份未跟踪的 R2/R3 实施文档未纳入本轮。B13 的公开 `md.connect`、`DatasourceCatalog.connect`、`DatasourceConnection` 及三个 Help callable target 已删除；连接仅由 datasource 内部持有。`md.test`、doctor 的 `test_no_persist`、`md.inspect` 和终端 `md.raw_sql` 的现有独立用途保留。`test_datasource_live_registry` 新增缺席反例，`test_datasource_live_help` 验证三个旧 target 均不可解析；公共导出快照、Help drift/reachability、API 文档与最新双语 site 同步。历史版本的 release notes 保留历史叙述，不是当前 Help 入口。

六后端 metadata owner 仍以绑定 Ibis schema 返回基础列；丰富事实未被静默恢复。`TableMetadata.is_view` 对未知表/视图类型从错误的 `False` 改为 `None`，schema-only 结果明确发出 view、nullable、physical-profile、comments、partitions 与 primary-keys unavailable 警告；已知文件来源仍为 `is_view=False`。独立探查显示 DuckDB/SQLite 普通表的 Ibis schema 可携带 NOT NULL，但 SQLite view 对同一非空 `id` 报 nullable，故不能把表形态的推断转授 view，亦不恢复仅凭 schema 推断的可空性。原 45 个丰富 metadata 断言保留原断言与恢复条件，逐 owner 数量为 DuckDB 7、PostgreSQL 1、MySQL 3、Trino 13、ClickHouse 21；这些 skip 仍非通过。新 schema-only 正反例和真实来源测试覆盖可选事实的明确 unavailable 状态，必要事实缺失仍须阻断消费格。

| C01 来源/控制格 | 本轮实测与状态 | 尚缺事实 |
| --- | --- | --- |
| DuckDB table/view、CSV/Parquet/JSON 与本地 HTTP JSON | **所测通过**：绑定来源、原样 Ibis 提交、空流/早关闭、schema-only 披露及终端控制 | DS03 丰富 metadata；DS15 带认证 HTTP 仍在秘密解析前阻断 |
| SQLite main table/view | **所测通过**：绑定读取、固定 schema、driver authorizer 拒写与终端 interrupt；view 类型/可空性未臆断 | DS06 丰富索引/约束等 metadata；attached 形态未取得新资格 |
| PostgreSQL table/view/namespace、Decimal | **所测通过**：来源与 schema-only 实证，终端只读、UTC 与服务端 timeout | 可选 metadata；服务端取消终止未证，`interrupt()` 仅记 `remote_unknown` |
| MySQL table/view、Decimal、无效日期 | **所测通过**：来源与 schema-only、无效日期拒绝；**阻塞**：终端 timeout 和时区事实 | 无合格可执行 timeout/driver 时区事实；远端终止未证；复合身份属 R9 |
| Trino Iceberg/non-Iceberg table/view、Iceberg 分区 | **所测通过**：来源与 schema-only、绑定分区读取、终端 timeout/UTC/只读 | Hive `$partitions` 与可选 metadata；远端终止未证 |
| ClickHouse MergeTree table/view、Distributed、分区 | **所测通过**：来源、schema-only、绑定分区与双 shard 读取；**阻塞**：只读账号终端 timeout 和时区事实 | reader 的 `max_execution_time=0` 且 readonly；driver 当前无可核验时区事实；远端终止未证 |

DS/AN 台账本轮处置：DS02/B13 公共旁路已封闭，终端 typed reentry 仍拒绝；DS03–DS07/DS09 的 45 个丰富 metadata 正例未恢复，新增 unavailable 实证不冒充丰富事实；DS11、DS13、DS15 保持上述精确阻塞，DS17/DS21 的完整六后端业务/资源组合仍未验证。AN33 的新 `SourceSession` 仅提交绑定表达式的未改写编译产物，旧具体执行类文本方法仍由 R4–R9 迁移，不能据此算 R1 出口第 2、5 项通过。14 个依赖 R5 的来源正例跳过保持原归属和恢复条件。远端 `interrupt()` 只证明本地 cursor/connection 关闭，不证明服务端查询已终止。

真实服务版本：PostgreSQL 17.11、MySQL 8.4.11、Trino 483、ClickHouse 26.3.33.24；本地驱动 Ibis 12.0.0、DuckDB 1.5.3、PyArrow 25.0.1、psycopg 3.3.4、mysqlclient 2.2.7、Trino Python 0.337.0、clickhouse-connect 1.3.0。使用仓库专用 `marivo-multisource` 环境，PostgreSQL/MySQL、Trino、ClickHouse 分组执行 `make runtime-test TESTS='tests/test_r12_source_adapters_runtime.py tests/test_r13_control_boundaries.py' RUNTIME_WORKERS=1`，分别为 **5 passed / 7 skipped**、**3 passed / 9 skipped**、**3 passed / 9 skipped**；独立 ClickHouse cluster Distributed 用例为 **1 passed**。这些分组有重复的本地用例，不能相加当作唯一格数。Trino 与 ClickHouse smoke 分别确认 Iceberg/current vs pinned 与本地 MergeTree 环境；尚无远端终止、带认证 HTTP 或安装包/真实 Agent 验收。C01.a/b/c **部分通过且保留精确阻塞/未验证格**，R1 整体仍未通过。

本轮实现、测试、API 文档、Datasource owning spec 和双语 latest site（不含本验收记录）的 `git diff --binary HEAD -- marivo tests docs/api docs/specs site/src/content/docs` SHA-256 为 `94d9538d4009c2a3a65e2325da0b14d02431d813ddfc19515d2fcda5f8bf2795`，仅定位提交前候选。最终 `make check-agent` 的 Ruff/import、379 个源码文件 typing、默认测试 **5128 passed / 64 skipped** 和 Sphinx API 文档均通过；`npm --prefix site run build` 完成 321 页并验证中英文安装脚本，`git diff --check` 通过。64 个 skip 包括上述 45 个丰富 metadata 与 14 个 R5 正例，均不计入通过。

## R0.3–R0.5 本次快照与核验

起点分支 `panda`，HEAD `ba79036dcd4642e609d4e9c2a76570ecf25d4542`；以下 hash 固定提交前的文档候选，与本文件一同提交后可按 Git 提交复核。五份输入在本轮开始时的 SHA-256：

| 输入 | SHA-256 |
| --- | --- |
| `2026-09-23-analysis-algebra-theory.md` | `40ce44e730a245b9a9dad50ac6fe64effa5c374960ef658ec9eaaa328f6a2e14` |
| `2026-09-24-marivo-semantic-analysis-dsl-interface-design.md` | `d31aa12ac4f4bffe571572e7824a7d2f33f83f6e8a372817905c5dd30ab5e4ad` |
| `2026-09-24-marivo-analysis-dsl-architecture-design.md` | `1c8bf9f9ccae7961b165b6ace061b721962bd634489a9e6dd95d0909e4d074b5` |
| `2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md` | `2c433063e7275e33481cf9fd33be28d32ea5236c6167d6484ee14b2239f93646` |
| `2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md` | `fd077522e35edc3450bfe6728544d5166c9002d3b8502e80ffdb4f255e382c72` |

用户随后明确要求保留 `md.raw_sql` 执行 raw SQL，作为 Marivo 分析能力之外的逃生通道。同步修订后的主计划 SHA-256 为 `e44e7f8628a3646cf9d571be25ec863ef8c0b605917315969a44f5c491f876de`，R0 计划为 `0256bb9e31be905e7d77774aee7607b455b035b08d4e9394293c3194a8d14d08`；原始输入 hash 保留作起点证据。本轮八份已跟踪文档的 `git diff --binary HEAD -- <八文件>` SHA-256 为 `9c1d52b29b5225926d1489763b19ff51bde84477b557313a207217be0b54ce78`；新 SQL 台账文件 SHA-256 为 `f281caac4696364c23873be7b36d281f35426be4e91c656a4338973f8b23565e`。本文件在这些 hash 之外，避免自引用。

静态核验：`git status --short --branch`、`git rev-parse HEAD`、`shasum -a 256 <五份输入及 SQL 台账>`、`rg -n 'raw_sql|statement\(|submit\(|\.sql\(|SELECT |WITH |SHOW |PRAGMA |CREATE |SET ' marivo/{datasource,semantic,analysis}`、`rg -n 'postprocess_sql|read_parquet|compile_event_bundle|integrity_sql' marivo`、导出/Help/消费者反查及 Markdown 相对链接检查；`git diff --check` 退出码 0。`source_health.py` 的实际路径为 `marivo/semantic/source_health.py`，扫描已覆盖。`make test TESTS='tests/test_datasource_raw_sql.py'` 在本次修订后 41 项通过，证明当前公共 raw SQL 路线的现有测试行为，不能授予新 DSL 的真实后端资格。独立反例按 owning spec/台账审阅了 DST、重叠 Anchor、25/5/70 界、权重缺层、比率缺侧与同刻顺序；这些是规则审阅，非测试执行通过。

未验证：新目标测试文件/独立 oracle 执行、真实六后端/表形态/资源、安装 wheel、真实 Agent 旅程；旧 J1–J4 或 C0–C10 成绩不转授。阻塞：R0.5 的无 SQL 控制/认证替代仍待实证，R0.6 交接未开展。后续阶段应分别记录实际代码 SHA、diff hash、owning spec 版本、通过/失败/未验证/阻塞单元及精确命令。

## R2.1 身份、版本与关系定义候选（2026-09-27）

起点 `panda` HEAD `4105c48d7c43bdf8771c7bcd0bf05fac78de804e`。本轮依据本地未跟踪的 R2 实施文档 `docs/superpowers/specs/2026-09-27-marivo-full-algebra-dsl-r2-implementation-plan.md`，其 SHA-256 为 `7c8488e862195487b05494f86ddb9f44e71b362f8649933cecd041c72299f18c`；原文件未改，也不属于本次提交。代码、测试、示例与 owning spec、`site/` latest 中英文本轮候选（不含本验收记录）的 `git diff --binary HEAD -- marivo tests devtools docs/specs site/src/content/docs` SHA-256 为 `d1094b94c6edeb416c81d9085b1c4b2d488262f2d1fbd3a95d2bc06b1b99bafe`，新增独立反例文件 `tests/test_semantic_r21_identity_relationship.py` 的 SHA-256 为 `374acf811b7df122a841f4f8efbb06001f8e3bdeb25c4b9b7a99580ba3c26ca0`。这些 hash 定位提交前候选；未作 wheel、真实来源或 Agent 验收。

| 单元 | 本轮结论 | 独立预期、证据与恢复条件 |
| --- | --- | --- |
| C02.a：Entity K、版本声明及 Ref | **R2.1 静态通过；来源未验证** | 无来源 I/O 的真实 authoring 文件声明复合 K `(tenant_id, order_id)`，规范版本行键为 `(tenant_id, order_id, snapshot_day)`；排除终点 `2026-09-27 00:00 UTC` 精确落在 `2026-09-26`，不查询可用分区或回退旧快照。非版本实体重复 K、错误投影别名与版本坐标混入 K 在加载时拒绝。R5 仍须以实际来源行检查 Null/重复、缺失快照及 validity 重叠；不通过历史 `distinct(K)` 修复。 |
| C02.b：Relationship 子集 | **R2.1 部分通过；可缺失性契约未闭合，来源未验证** | 公开构造器只接收有向端点与 join Ref；`name` 是角色。唯一规范解析把 join Ref 绑定到直接输出列，重复键和错端点结构化拒绝，并按两端完整 K 的覆盖推导结构性 `cardinality`；未覆盖的一侧只得多值性质，不由作者猜测单值。两条同端点角色路径保持歧义而不自动选；反向一对多不获可加性。版本化单值侧保留需精确版本选择的事实。`required` 已从关系声明移除：静态加载不判定每个源行是否应有目标，显式有界 `source_check.relationship_matches` 只核所选来源范围；全局可缺失性业务契约及消费门禁仍待后续设计/实现。实际多重匹配、缺失匹配、重叠贡献与 fanout 来源证据仍由 R5/消费者检查。 |
| C02.c Event/StateModel/order/calendar | **未验证** | R2.3 承接；本轮旧 Event/StateModel 测试回归不等于业务顺序目标通过。 |
| C17.a ontology Ref/Artifact 关联 | **未验证** | R2.4 承接；本轮跨实体 reverse-index 回归不授 ontology 规划或计算准入。 |

定向 `make test TESTS='tests/test_semantic_r21_identity_relationship.py tests/test_semantic_live_registry.py tests/test_semantic_catalog.py tests/test_semantic_catalog_discovery.py tests/test_semantic_source_health.py'` 为 **233 passed**；附加键覆盖与路径反例定向 **87 passed**；既有 validity、Ref、assembly、catalog、Event/StateModel 和跨实体正反例参加完整默认门禁。`make check-agent` 的 lint/import、378 个源码文件 typing、默认测试 **5107 passed、64 skipped** 及 API 文档均通过；`npm --prefix site run build` 成功，Astro 321 页，中英文安装脚本校验通过；`git diff --check` 退出码 0。64 个跳过继续保持 R1/R5 等原归属与恢复条件，不能算 R2.1 通过。此次没有修改 packaged skills 或 `AGENTS.md`，R1 的公共 SQL 旁路、metadata、远端终止等阻塞状态不变。修复静态声明失败须修改精确 Ref/输出别名或完整身份键并重新 `ms.load()`；来源行违约不能以改静态声明或回退旧数据标记通过，须在 R5 对对应来源形态重验。

## R2.2 Metric 声明与规范计算图静态候选（2026-09-27）

基线为 `panda` HEAD `0ec27acc01149ea6201c116574f8f917c20354d7`；起点没有受跟踪的未提交改动，原未跟踪的 R2 实施文档 SHA-256 仍为 `7c8488e862195487b05494f86ddb9f44e71b362f8649933cecd041c72299f18c`，未修改。用户实施中撤回 `ms.statistical_weight`：本轮不提供声明、Ref、catalog 或 Help 接口。提交前已暂存的代码、测试与文档候选（不含本记录）的 `git diff --binary HEAD -- marivo tests docs/specs docs/api site/src/content/docs` SHA-256 为 `f7d8a34b4b57472ff0e1f90bcaa06659448e088b68413810622bfc3d031ee141`；新增图契约测试文件 SHA-256 为 `1caffac8069eb2af6003e889d716e3d7cd5910a20c70c2119fe315c467933460`。这些只定位本地候选，不代表来源执行或 R2 阶段验收。

| 单元 | 状态 | 独立预期、当前证据及后续条件 |
| --- | --- | --- |
| 声明与组件 | **静态通过** | `@ms.metric` 仍由受限 Ibis body 校验；其显式 Entity 列表（多 Entity 时另有 `root_entity`）、业务时间、单位、可加性与值政策构成声明事实，opaque body 不推断 ratio 分子/分母或原量归约权。规范组件保留根、过滤、路径、事件/状态时间、fold、单位、Null/空值政策、数值方法和所需原状态；未声明的政策字段保持缺失。`test_semantic_metric_graph_lowering.py`、`test_lazy_observation_contracts.py` 与 `test_lazy_group_b_admission.py` 核对；具体 Population 和来源贡献许可仍由 R5 验证。 |
| 数值与状态手算 oracle | **静态图通过；来源执行未验证** | ratio 的分子 18、分母 6 应为 3，不能对行比率直接求和；linear 的 `8 CNY - 3 CNY` 为 `5 CNY`，`CNY + kg` 不相称；weighted mean 的 `(10,2)、(Null,9)、(20,1)` 必须用同一非 Null 配对，结果为 `40/3`，不能纳入孤立权重 9。sum/mean 保留和、非 Null 数及行数，count 保留计数与行数，min/max 保留极值与空值状态；distinct 和分位数没有通用可合并和。`test_lazy_local_fold.py`、`test_metric_unit_algebra.py`、`test_semantic_metric_graph_lowering.py`、`test_lazy_state_admission.py` 与 `test_metric_expression_graph.py` 分别提供独立数值和图边界断言；这些既有局部执行测试不授新来源路线资格。 |
| 空间与时间次序 | **静态通过；来源行重叠未验证** | 对两日 A/B 的贡献 `(10,0)` 与 `(0,20)`，先按地区取时间最大再空间求和为 30，先逐日求和再取时间最大为 20，不能交换。半可加 `max` 的图只给时间极值，禁不安全的空间归约；匹配的极值方法才可同时合并。一个 100 的贡献落在两个重叠标签时，两标签各见 100，去标签仍须 100 而非 200；R5 须以具体贡献和来源行证明。`test_lazy_observation_contracts.py` 与 `test_lazy_distinct_numeric.py` 保留现有反例。 |
| 图身份与消费者 | **静态通过** | 同内容 DAG 共享节点；绑定图指纹同时包含图与有效依赖摘要，声明变化会改变持久/准入身份而展示文案不会。Analysis 折叠消费者读取图给出的内在合并与数值方法，继续独立检查 Population、坐标、贡献与保留状态；不创建 Semantic 对象版本。`test_semantic_r22_metric_graph.py`、`test_semantic_metric_graph_lowering.py`、`test_metric_expression_graph.py` 核对。 |
| 统计权重角色 | **撤回；未实施** | 用户明确取消本轮 `ms.statistical_weight` 接口。R0.3 目标条款只保留为延期背景；没有该声明、Ref、catalog 条目、Help target 或执行资格。`ms.weighted_mean` 的 Metric 组件语义保持原契约。 |
| 公共披露 | **静态回归通过** | 本轮没有新公共导出或 Help target；已有导出快照、Help 可达性和预算随全量测试回归。`site/` latest 中英文去除 Semantic 对象版本表述；未编辑 packaged skills 或 `AGENTS.md`。 |

撤回接口后重新执行 `make check-agent`：lint/import、378 个源码文件 typing、默认测试 **5110 passed、64 skipped** 和 API 文档全部通过。定向 `make test TESTS='tests/test_semantic_r22_metric_graph.py tests/test_semantic_metric_graph_lowering.py tests/test_metric_expression_graph.py tests/test_lazy_observation_contracts.py tests/test_metric_unit_algebra.py tests/test_lazy_group_b_admission.py tests/test_cutover_documentation_examples.py tests/test_public_surface.py'` 为 **121 passed**。`npm --prefix site run build` 成功，Astro 321 页及中英文安装脚本校验通过；生成的 API 和站点目录检索不到撤回的统计权重接口；`git diff --check` 退出码 0。所有 64 个 skip 保持原断言和 R1/R5 等归属，不计 R2.2 或整体阶段通过。未执行六后端实源、wheel、冷恢复或真实 Agent 旅程；R0/R1 阻塞项不因静态图通过而解除，R2.3/R2.4 仍待实施，**R2 整体未通过**。

## R2.3 Event、StateModel、业务顺序与日历声明（2026-09-27）

基线为 `panda` HEAD `51c50301baa5c61510c8216fffc95a973fae072b`。本轮按用户批准的 R2.3 范围实施；未跟踪 R2 实施计划的 SHA-256 为 `7c8488e862195487b05494f86ddb9f44e71b362f8649933cecd041c72299f18c`，原文件未修改，也不纳入本轮改动。提交前已暂存的代码、测试、规范、API 与站点候选（不含本验收记录）的 `git diff --cached --binary HEAD -- marivo tests docs/specs docs/api site/src/content/docs` SHA-256 为 `c2b5f780e9fe52e83fbd39e445cfc61ad1bec6c3715df349290defe0020d5c64`；新增实现 `marivo/semantic/business_order.py` 的 SHA-256 为 `42dd5e9300a9ac7c828163c47fb058407390f42b31a1d9da1be53b01650780a6`，新增反例 `tests/test_semantic_r23_business_order.py` 为 `15a9db6299853870e171a10c9a98baeb9f5139c5f1705cfa8c3a305c6a85aca9`。这些 hash 定位本地候选，不代表来源执行或发布资格。

| 单元 | 本轮结论 | 证据与边界 |
| --- | --- | --- |
| C02.c Event/StateModel/业务顺序 | **R2.3 静态声明通过；来源与消费未验证** | 真实 authoring 项目的 `ms.load()`、精确 `ms.ref.business_order(...)` / `catalog.require(...)`、目录详情、定义指纹和 scoped readiness 由新增测试核对。不可变 `EventSequence` 声明整数或显式枚举值序，`EventPrecedence` 使用精确角色；加载拒绝错误角色/Subject、不同顺序契约、重复与成环 precedence、occurrence 身份直接别名及 StateModel 未覆盖的触发 Event。StateModel 指纹随所绑定顺序的值序变化。Event 来源、业务时间、身份、participant 路径与 Subject K 仅作声明校验。readiness 明示值与历史未验证，顺序声明本身不作为可执行分析输入。 |
| C02.c 日历日期轴 | **R2.3 静态角色通过；原生值未验证** | 加载拒绝已声明的 timestamp-bearing 日期轴；未声明原生类型的日期轴保留现有完整覆盖认证的 civil-date 证明要求。新增声明反例通过；本轮没有对实际日历来源重新认证。 |
| 同刻相反转移反例 | **声明边界已证明；R7 结果未验证** | 新增测试确认两个相反 Event 的顺序声明不把 occurrence ID 当序列字段，也不从 ID 推出 precedence。实际相同时间戳的输入须由 R7 检查序列值类型、同 Subject 唯一性、未知枚举值，以及所有仍允许的顺序下结果、trace、violation、interval、assignment 与 continuation 保留部件的等价性；不能用静态声明代替该检查。 |
| 公共披露与阶段边界 | **静态回归通过** | Ref、公共导出、原生 Help、目录成员、API 索引和 `site/` latest 中英文示例同步。没有新增 `session.events.match` 参数、matcher、replayer、窗口或 fold；未改 packaged skills、`AGENTS.md` 或未跟踪计划。 |

定向 Event、StateModel、日历/解析与 readiness 测试 **105 passed**；Ref、Help、公共导出和文档测试 **114 passed**；新增 R2.3 文件 **12 passed**。最终 `make check-agent` 的 lint/import、379 个源码文件 typing、默认测试 **5122 passed、64 skipped** 和 API 文档通过；`npm --prefix site run build` 成功，Astro **321 页**及中英文安装脚本校验通过；`git diff --check` 退出码 0。64 个 skip 保留原断言与 R1/R5 等归属，不计作 C02.c 来源核验。R7、实源后端、wheel、冷恢复及真实 Agent 未验证；R1 遗留格状态不变，**R2 整体未通过**。

## R2.4 加载、披露、ontology 与阶段出口（2026-09-27）

基线为 `panda` HEAD `1dafbb7f60c10a67b1ca207fbc5f83132e1bbb88`（R2.3 已提交），起点只有未跟踪的 R2 实施文档，其 SHA-256 为 `7c8488e862195487b05494f86ddb9f44e71b362f8649933cecd041c72299f18c`；本轮保留该文件且不纳入提交。提交前代码、测试、owning spec、API 和站点候选（不含本记录和新增测试文件）的 `git diff --binary HEAD -- marivo tests docs/specs docs/api site/src/content/docs ':(exclude)tests/test_semantic_r24_handoff.py'` SHA-256 为 `05b14ed93d40f868c22969b6f3c578a9896e0c7885efbcc15eb7c4b520db93c5`；新增跨模块用例 `tests/test_semantic_r24_handoff.py` 的 SHA-256 为 `dfff68265626cbcb989cdf7d3fad9bca80283acef3ecca0471afdc4e498951a2`。这些 hash 仅定位本地候选，不授来源或发布资格。

| 单元 | 本轮结论 | 独立预期、实际证据与恢复条件 |
| --- | --- | --- |
| C02.a/b/c 加载、精确 Ref 与闭包 | **R2.4 静态交接通过；C02.b 仍部分通过** | 一个实际 authoring 项目加载 Metric、Event、StateModel、业务顺序及日历，`catalog.require(...)` 精确解析四类请求根。readiness 改用已编译的唯一规范依赖图：Metric 请求的诊断闭包含 Measure、Entity 和 Datasource，但 `analysis_ready_inputs` 只返回直接请求的 Metric；错误 kind 或缺失 Ref 由既有结构化 repair 拒绝。无关定义的加载 warning 不进入 scoped 报告，依赖上的 warning 保留。`tests/test_semantic_r24_handoff.py`、`test_semantic_readiness.py`、`test_semantic_catalog.py` 提供独立断言。C02.b 的全局可缺失性契约及来源 fanout 仍未闭合；须由 owning spec 决定并实现后重验，不能因闭包通过而标为通过。 |
| 时间与业务顺序前提 | **静态通过；执行未验证** | 同一项目的 StateModel readiness 保留 `business_order_values_unverified` advisory；业务顺序声明不进入可执行输入。未认证的 period calendar 返回 `period_calendar_artifact_missing` blocker，不借来源样本放行；完整来源认证和同刻事件结果等价仍由相应 R5/R7 边界复核。 |
| preview 与 source-health | **所测 DuckDB 范围通过；其他来源未验证** | 测试库的四笔金额 `125.25 + 250.50 + 375.75 + 0` 在显式 `max_rows=10` scope 下 preview 为 `751.5`，结果注明 `sample_only`。同一来源的 `allowed_values(region={'moon-base'})` 对实际 `orbital` 返回 `failed`，披露查询及精确 scope；前后 readiness 除检查时刻外相同。此为所测本地 DuckDB/物理表证据，不能转授全源唯一性、六后端形态或 Analysis 方法资格；缺失资格仍需来源 owner 实测。 |
| C17.a ontology 身份 | **当前 Semantic Ref 关联通过；Artifact 关联未实施** | `mo.load(semantic=catalog)` 验证精确端点；ontology 指纹与 `semantic_catalog_fingerprint` 共同标识当前上下文。错误角色/缺失端点的既有反例和本轮实际项目回归通过。边不改变 readiness，也不授因果、计算或 Artifact 权限；R4/R10 接入新 Artifact 身份后须独立验证关联和错误角色，故 C17.a 不写作整体通过。 |
| 公共披露 | **静态通过** | 公共 docstring/类型、原生 Help、结构化 repair、`__all__` 快照、独立可达性与预算测试，以及 owning spec、API 和 `site/` latest 中英文说明对齐；没有新增导出、兼容 alias 或影子图。未改 packaged skills、`AGENTS.md` 或已撤回的 `ms.statistical_weight`。 |

定向 `make test TESTS='tests/test_semantic_r24_handoff.py tests/test_semantic_readiness.py tests/test_ontology_extension.py tests/test_unified_help.py tests/test_semantic_catalog.py tests/test_public_surface.py tests/test_semantic_r23_business_order.py'` 为 **228 passed**；触及的五个生产模块定向 typecheck 和九个文件的 lint/import 通过。`make check-agent` 的 lint/import、379 个源码文件 typing、默认测试 **5125 passed、64 skipped** 与 API 文档通过；`npm --prefix site run build` 成功，Astro **321 页**，中英文安装脚本校验通过；`git diff --check` 退出码 0。64 个 skip 维持原断言、依赖和恢复条件，不计 R2 通过。R0.6、R1 的 SQL/metadata/远端资格及 R5–R9 的实源执行、Runtime、冷恢复、wheel、真实 Agent 均未由本轮证明。**R2.4 交付完成，R2 整体未通过。**

## C02.b 静态契约与 C17.a Artifact 交接收口（2026-09-27）

基线代码 SHA 为 `962cda619526fb27de5925f8b6e00d5d1f423ae1`（已提交的 R2.4）。提交前实现、测试、owning spec、API、站点及 R0 能力台账候选（不含本验收记录）的 `git diff --binary HEAD -- marivo tests docs/specs docs/api site/src/content/docs docs/superpowers/specs/2026-09-26-marivo-full-refactor-r0-capability-ledger.md` SHA-256 为 `7943b8fd62fefb5d34a438a9bc939a21d116a9314963734bfd95eee00344afc8`。原未跟踪 R2 计划 SHA-256 为 `7c8488e862195487b05494f86ddb9f44e71b362f8649933cecd041c72299f18c`，未修改、未纳入提交。候选 hash 只标识本地改动，不能代表来源执行或发布资格。

| 单元 | 本轮状态 | 独立预期、实际证据与失败恢复条件 |
| --- | --- | --- |
| C02.b R2 静态映射契约 | **通过；C02.b 完整能力仍部分通过** | 完整目标 K 覆盖只能推导结构性 `many_to_one`，不能推导每个订单都匹配客户。新增 SQLite 反例在原有缺目标键 `99` 外加入空外键：有界 `relationship_matches(side="from")` 报告两笔未匹配，Relationship 详情仍为 `many_to_one`，readiness 前后除检查时刻外一致。`functional_path` 默认拒绝版本化目标路径，静态详情披露所需的版本解析；消费侧精确选择及实际匹配仍待 R5。`tests/test_semantic_r21_identity_relationship.py` 与 `test_semantic_source_health.py` 核对。若键、版本或披露回归，修正精确声明/消费前提并重跑对应正反例；不能靠 source-health 结果改写静态资格。 |
| C02.b R5 消费与来源核验 | **未实施；未验证** | 消费者须对所选成员、业务角色与精确版本定义必配或允许缺失的语义；实际必配缺失或单值路径多重匹配须产生结构化 expected/received/repair。R5 要以已具资格的后端和物理形态对真实来源行逐格实测，区分有界诊断、实际执行和 fanout/重叠贡献；完成前不能把 C02.b 整格标为通过。 |
| C17.a 精确 Semantic Ref | **R2 静态通过；本轮回归通过** | `mo.load(semantic=catalog)` 只接受当前 catalog 的精确端点，ontology 指纹与 `semantic_catalog_fingerprint` 共同标识上下文；现有错误端点、错误角色和 R2.4 真实项目断言参加定向回归。若指纹或端点校验失效，修复 ontology loader 并重新核对错误 repair；边仍不改变 readiness 或授予因果、计算资格。 |
| C17.a Artifact 关联 | **未实施；R4/R10 待验证** | R4 先建立新 Artifact 身份、可校验 receipt 和精确语义依赖；R10 才能按 ontology 上下文只读关联并拒绝错误角色、身份不符、损坏 receipt 或过期上下文。当前 Session 局部 ArtifactRef 和静态 Ref 测试不能代替这些证据；不得新增 ontology authoring 绑定或把本格写为通过。 |
| 公共披露 | **静态通过** | Relationship 无全局 `required`，Help、docstring、Semantic owning spec 与 `site/` latest 中英文说明一致；API 文档不再把尚未实现的 Analysis ontology 发现入口写成现行能力。无新增导出、别名或第二套关系图；未改 packaged skills、`AGENTS.md` 或已撤回的 `ms.statistical_weight`。若公开说明与签名/行为再漂移，按同一 owner 同步修正并重跑 Help、API 和站点检查。 |

定向 `make test TESTS='tests/test_semantic_r21_identity_relationship.py tests/test_semantic_source_health.py tests/test_semantic_r24_handoff.py tests/test_ontology_extension.py tests/test_unified_help.py'` 为 **72 passed**；`make typecheck TYPECHECK_TARGETS='marivo/semantic/_authoring_decorators.py marivo/semantic/source_health.py marivo/semantic/_capabilities/registry.py'` 和五个改动 Python 文件的定向 `make lint-agent` 通过。`make check-agent` 的 lint/import、379 个源码文件 typing、默认测试 **5126 passed、64 skipped** 与 API 文档通过；`npm --prefix site run build` 成功，Astro **321 页**及中英文安装脚本校验通过；`git diff --check` 退出码 0。64 个 skip 保持原归属，不转授 R1 或 R5 资格。本轮只关闭 R2 的可缺失性静态契约与 C17.a 的阶段交接；**C02.b、C17.a 完整能力及 R2 整体仍未通过**。

## R0.6 破坏性变更、证据与交接收口（2026-09-27）

本轮按用户要求在 `panda` 直接实施。开工 HEAD 为
`784135090b98c32b60d4b8f8cf69c02982d883d5`，跟踪文件 diff 为空，
`git diff --binary` 的 SHA-256 为
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。
开工仅有未跟踪的 R2 实施计划，SHA-256 为
`7c8488e862195487b05494f86ddb9f44e71b362f8649933cecd041c72299f18c`；
实施中工作树又出现未跟踪的 R3 计划。两份均不属于本次产物，未移动、覆盖或纳入提交。
R0.6 先建立的空隔离工作树已按用户指定移除；没有产品代码或 packaged skill 改动。

| 输入与 R0 产物 | 本次 SHA-256；实际状态 |
| --- | --- |
| [代数 v0.5](2026-09-23-analysis-algebra-theory.md)、[接口设计](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md)、[架构设计](2026-09-24-marivo-analysis-dsl-architecture-design.md) | `40ce44e730a245b9a9dad50ac6fe64effa5c374960ef658ec9eaaa328f6a2e14`、`d31aa12ac4f4bffe571572e7824a7d2f33f83f6e8a372817905c5dd30ab5e4ad`、`1c8bf9f9ccae7961b165b6ace061b721962bd634489a9e6dd95d0909e4d074b5`；受跟踪的设计输入，非运行证据 |
| [主计划](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)、[R0 实施文档](2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md) | `e44e7f8628a3646cf9d571be25ec863ef8c0b605917315969a44f5c491f876de`、`0256bb9e31be905e7d77774aee7607b455b035b08d4e9394293c3194a8d14d08`；出口依据 |
| [R1 实施文档](2026-09-26-marivo-full-algebra-dsl-r1-implementation-plan.md) | `77ccf2d5f3ca75dba164fa024f8552497b08fcf8a68f2e574f00be600d047338`；已补 B13 交接，原起草快照仍为历史语境 |
| R0.1 [历史索引](2026-09-26-marivo-full-refactor-r0-evidence-index.md)、[218 项 manifest](evidence/r01/manifest.json) | `e859557f0177ccea9a9885f25f69b055f3c3dce3796368fcbfdf44e991227daf`、`82a038e0646a674879578e0f9d00805180743315cf64feedee5ad27fd85d8414`；原 Agent 轨迹及旧 wheel 的部分附件仍仅本机可得，待新验收 |
| R0.2/R0.4/R0.6 [能力、规则、breaking 与交接台账](2026-09-26-marivo-full-refactor-r0-capability-ledger.md) | `be5b17e6032d077e4050f56aeb1e8ff5178ede978e7576eccebdcd66b6908454`；§7 B01–B13，§8 R1/R2 首批格；本轮候选内容 hash，最终同受版本控制文件核对 |
| R0.5 [SQL/adapter/六后端台账](2026-09-26-marivo-full-refactor-r0-sql-ledger.md) | `c7d3de0b4b92649258b5f83ec76957cf0ae99a277b60e35d268081facbcf0461`；DS01–DS21、AN01–AN33 与目标矩阵是静态索引 |
| R0.3/R0.6 owning specs：[Analysis](../../specs/analysis/python-analysis-design.md)、[Semantic](../../specs/semantic/semantic-object-model.md)、[Datasource](../../specs/semantic/datasource-layer.md) | `b5f3e9d4a5764293a517fe6fa3448429a9fea0e458e4b87f194fe49208d64b6b`、`08621707f6806a5a607e1690bac9e6686507d2b460c11a74ee45b59962f17f64`、`dd0477fe87ea78d4b5992eb3550d0990a333109d5b2fcbb45a7dea18fab2b723`；分别核对 C18/权重/ratio/Cell、身份/顺序/历史 SQL、Ibis/raw SQL 与 R0.6 公共连接目标；后者仍是 inactive 目标 |
| 本主验收记录 | 当前内容的 SHA-256 另记于 [R0.6 manifest](evidence/r06/manifest.json)，避免在本文自引用；本次只增 R0.6 文档证据，不继承旧运行通过 |

能力台账、Datasource owning spec 与 R1 交接补注的候选 diff（不含本主记录）的
`git diff --binary HEAD -- docs/specs/semantic/datasource-layer.md docs/superpowers/specs/2026-09-26-marivo-full-refactor-r0-capability-ledger.md docs/superpowers/specs/2026-09-26-marivo-full-algebra-dsl-r1-implementation-plan.md`
SHA-256 为 `d36675e4948654e0d724d6348686e95f805aeba37982ec516201f03304b16301`。
这些 hash 供跨 checkout 识别文档版本；不等于某方法、后端或安装包的运行证明。

| R0.6 出口 | 状态及可复核定位 |
| --- | --- |
| 破坏性变更逐项收束 | **通过（静态清单）**：[能力台账 §7](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#7-r06-breaking-changes) 的 B01–B13 覆盖旧 Population/Dataset、J1–J4 身份、旧 Help/registry/codec/协议、definition-only 命中、Artifact→DuckDB、Store 不迁移、SQL/parity、distinct/quantile K、公开参数和 `md.connect` 旁路；每行有旧位置、目标/拒绝、阶段、消费者及验收索引。已删除的 B10 与待切换行分开 |
| R1/R2 可执行交接 | **通过（静态交接）**：[能力台账 §8](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#8-r06-r1r2-handoff)按 C01/C02/C17 给 adapter/semantic/ontology 唯一责任、首批 backend/类型/形状/路线、CLI/Help/site/测试消费者、独立正反例和恢复条件；R3/R4/R5 的下游责任另列。未把未跟踪 R2/R3 计划作为验收唯一附件 |
| 受控证据与披露 | **通过（文档核对）**：本表和 R0.6 manifest 记录实际 hash、owning spec 版本及本 HEAD；R0.1 缺失原始附件与 R0.5 未验证格保留。packaged skills 的后续同步需依 AGENTS.md 取得明确用户批准，本次没有编辑 |

R0 **整体仍阻塞**：R0.5 的 MySQL/ClickHouse timeout/时区、DS15 认证及必需 Ibis/驱动
替代可行性没有逐格闭合；完整六后端/表形态/Decimal/时间/取消矩阵仍未验证。R1 的
`md.connect` 公共原生 backend 仍可绕过唯一 `md.raw_sql` 终端，此次只在 owning spec
冻结删除目标，未改公开代码。R4 的统一协议、旧 Store 不迁移和固定 Artifact 方法尚待实现；
R5–R9 的方法来源路线、R10 的 wheel/真实 Agent 均未取得新资格。R1/R2 只能继续推进
不依赖阻塞格的工作，旧 J1–J4/C0–C10、静态扫描、编译或跳过断言不能转授通过。

本轮静态核验：`rg -n 'md\.connect|from_sql|parity_check|user_version=6|execution_key\(dataset.definition_fingerprint\)|def execute_j1|attach_parquet_scan|class J1Context|class J3Observed' marivo/datasource marivo/semantic marivo/analysis`
反查目标仍在或已删；`git ls-files` 确认 R0 原台账/owning specs 受跟踪，
`git check-ignore -v` 确认旧 `docs/superpowers/plans/` 附件被忽略；
`shasum -a 256` 与 R0.6 manifest 的逐文件 SHA-256/字节数核对 **14/14**，
B01–B13 无缺号，文档相对链接所指文件存在，`git diff --check` 退出码 **0**。
未运行 Python/Runtime/六后端测试、站点构建、wheel 或真实 Agent；本轮未改产品行为。

## R3.1 内部值模型与六类规则（2026-09-27）

基线为 `panda` HEAD `f388916c1430a3e1618e6629320215e9c3b67a8d`。输入为 [Analysis owning spec](../../specs/analysis/python-analysis-design.md)（SHA-256 `b5f3e9d4a5764293a517fe6fa3448429a9fea0e458e4b87f194fe49208d64b6b`）、[R0.4 六规则台账](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#6-r04-六类元算子规则冻结)（SHA-256 `e10191bcb6ca605b3fe56fed8983ba96df2a2e26cf8b44f285bb1cff4a722a2f`）及本轮 R3 实施计划（原未跟踪文件 SHA-256 `8a8f7e5d5fb675f3f0e5f77b3f4095f6e18e71be467e091c1f74dd58d9acd987`）。原未跟踪 R2 计划 SHA-256 `7c8488e862195487b05494f86ddb9f44e71b362f8649933cecd041c72299f18c`；两份计划均未修改或纳入提交。本轮新增私有 `analysis/core` 值模型与规则，未更改公共导出、Help、站点示例、旧 J1/Dataset 消费者、packaged skills 或 `AGENTS.md`。

候选文件 SHA-256：`marivo/analysis/core/__init__.py` 为 `88572d7d567bc981cc1506b192c47835413a7305d42e5ad71a3fdb0d3abfaade`，`model.py` 为 `5601d9cadc65eeb64c914ca0a854b4e59326010822d15a87527640420677c94b`，`rules.py` 为 `4bcfd24c21789e51a027de786470812f7e38c4160e32fd949a6d03a9cbb2cc95`，独立反例 `tests/test_analysis_core_r31.py` 为 `27b97bcafc6ab888fc30c7c1aff5c119c11fe1c2bf768c71c8f858bac00f1b3a`。这些 hash 只定位本地候选，不证明来源执行或阶段总验收。

| R3.1 单元 | 本轮状态 | 固定输入、独立预期、实际证据与恢复条件 |
| --- | --- | --- |
| 域、量、Cell 与 Entity 成员身份 | **纯构造通过；实际成员未求值** | 以复合键 `(tenant_id, customer_id)` 和显式 `snapshot_day=2026-08-01` 为输入，签名须保留完整键和选中版本；缺选版、错误版本或不完整规范身份拒绝。完整键声明只给 `declared_key`；实际来源唯一性另有 `source.unique_key@v1` 待履行义务，不能以 `distinct` 消隐重复。固定键 helper 拒绝重复。Cell 的 Defined、Null、Undefined、Unknown 分立，缺侧由配对结果 `MissingCoordinate` 表示。测试无来源扫描，真实成员集合与来源重复行检查仍需 R4/R5 执行证据。 |
| `bind_project@v1` 与 `map_correspond@v1` | **规则构造通过；来源检查未履行** | 规范字段/Metric Ref、owner、graph 指纹及有向 Relationship 路径必须精确匹配；错误 kind、owner、路径和量绑定拒绝。`(1)→A,(2)→A,(3)→B` 的非单射主体集合像预期为 `{A,B}`，而非三行或强制单射。完整实际坐标 `[(east,aug),(west,sep)] ∪ [(east,sep)]` 预期三个二元组；缺侧 `(1)` 和 `(3)` 单列报告。精确配对、分组映射及来源单值性保留绑定检查义务，不能以构造成功称已完成。 |
| `cell_derive@v1` 与 `row_state@v1` | **规则构造及固定数值反例通过；真实求值未验证** | 固定 Cell `5−2=3`，`5/0=Undefined(zero_denominator)`；Null/Undefined/Unknown 不冒充 Defined，缺侧不作为 Cell。当前行 `count` 与 `count_defined` 为不同方法，`mean` 生成新的 RowStatistic 与当前行状态；其输出不能送入原量 rollup。数值有效性、Cell 政策与精确配对只有闭合检查标识和义务，尚无 R3.2 方法注册或来源检查器。 |
| `original_reduce@v1` 与 `parts_transport@v1` | **部件续算约束通过；归约数值未执行** | 仅接受同绑定、同量、同方法版本/贡献的原状态组件和范围覆盖；缺覆盖或错量部件拒绝。投影只保留主体/固定参照时撤销原状态与覆盖续算；范围收窄不能沿用旧覆盖。`OriginalReduce` 保留来源贡献分区与完整覆盖义务，尚须 R5/R9 对真实贡献和后端证明。 |
| 依据、义务与无 I/O | **构造边界通过；物理履行未实施** | 声明、builder 的字段归属推导及已完成检查分别记录；声明或 builder 依据不能声明来源唯一性等运行事实，错误 binding、未履行义务和依赖未闭合的 deduction 不成为可用依据。子节点 `Post` 在义务完成前不能向父节点传播为已证事实。六规则构造测试禁止 DuckDB/SQLite 连接、SourceSession、SessionStore 与 DatasetRuntime 初始化；闭合 check id 是将来 checker 的交接标识，不是现成检查实现。 |

定向 `make test TESTS='tests/test_analysis_core_r31.py tests/test_analysis_dsl_j1_construction.py tests/test_lazy_dataset_registry.py tests/test_analysis_dsl_j1_source.py'` 为 **103 passed**；`make typecheck TYPECHECK_TARGETS='marivo/analysis/core'`、`make lint-agent LINT_TARGETS='marivo/analysis/core tests/test_analysis_core_r31.py'` 均通过。最终 `make check-agent` 的 lint/import、382 个源码文件 typing、默认测试 **5138 passed、64 skipped** 和 API 文档全部通过。64 项 skip 保留原断言、归属及恢复条件，不计 R3.1 或 R0–R2 通过。本轮没有执行 Runtime 全量、六后端实源、wheel、冷恢复或真实 Agent；没有 R3.2 方法注册、R3.3 执行图、R3.4 lowering，也没有旧 J1/Dataset 路线迁移。后续须按 R4–R9 逐格接入真实消费者、履行检查义务并核验实际数据/后端；**R3 整体未通过**。


### R3.1 adversarial review corrections (2026-09-27)

Review-fix baseline: `77277ac085`. The private candidate now binds relational
premises to ordered, complete input domains and quantity definitions; changing
an endpoint's input, scope, domain, quantity, or side invalidates prior evidence.
Endpoint parts retain their own input bindings through unchanged-domain transport.
No public API, Help, packaged skill, or site contract changes in this correction.

Current admission is deliberately bounded: `parts_transport` preserves the exact
domain and retained part bindings; changed-domain selection or materialization
requires a subsequently implemented transport mapping. `row_state` and
`original_reduce` accept only a whole-input singleton with the exact input binding
and version. Grouped reduction remains unsupported until its mapping contract is
consumed. Original reduction admits only `sum@v1` state version `v1` with exactly
`(sum, non_null_count)`; other method/state contracts remain unsupported pending
method registration. These restrictions are not evidence of completed R3.2–R3.4.
Non-finite numeric inputs fail before the zero-denominator policy is applied.

Regression checks cover stale endpoint evidence, all five transport modes,
unmapped groups and foreign singleton inputs, missing/extra/unknown components,
unsupported state versions/methods, and NaN/Infinity with zero and nonzero divisors.
Validation: `make test TESTS='tests/test_analysis_core_r31.py'` passed **32 tests**.
`make check-agent` passed lint/import contracts, typing for **382 source files**,
the default suite (**5160 passed, 64 skipped**), and the API documentation build.
The existing skips retain their assertions and recovery conditions. No full
Runtime, backend, cold-recovery, or real-Agent acceptance was run. The preceding
candidate's hashes and gate counts remain historical evidence; R3 remains
unaccepted as a whole.


## R3.2 接入方法的私有语义与物理资格注册（2026-09-27）

实施起点为 `77277ac085` 加工作区已暂存的 R3.1 修正；工作期间这些已有修正和
R2/R3 计划由其他操作分别提交为 `96ea065561`、`dfece459aa`。本候选以当前
`dfece459aa` 为差异基线，未更改两份计划、R3.1 测试、`core/model.py`、
`AGENTS.md` 或 packaged skills。

本轮边界是已确认的逐项接入：六类规则、两个 Cell 方法、五个当前行方法及
`state_rollup@v1` 的私有注册/构造。`core.rules.derive`、当前行状态组件构造和
原状态组件准入消费唯一注册；原状态仍限定 `sum@v1`，不扩大 R3.1 的完整绑定、
whole-input singleton 或精确部件要求。旧 J1/operator/Dataset 的领域执行方法
尚未迁入，保留 R4–R8 归属；新注册不导入、查询或转发其注册，旧资格不能补路。
审查确认旧 J1 的当前行 sum/count/mean、rollup、difference 仍有独立可执行注册；
其中旧 count 合同写 `strict`，新私有 count 写 `count_all`。因此本轮只完成私有 core
入口的归并，R3.2 计划要求的跨执行路径单一 owner **未通过**，须随 R5/R6 消费者
迁移删除重叠注册后复核。保留旧公开执行路径不等于给予新注册物理资格。

| R3.2 单元 | 本轮状态 | 独立预期、证据边界与恢复条件 |
| --- | --- | --- |
| 唯一语义 owner 与闭合方法 | **私有构造通过；跨旧执行单一 owner 阻塞** | 独立枚举 11 个具体方法到六个规则的映射；重复 owner、未知方法/版本、旧 J1 ID、datasource SQL 终端、错误参数/输入拒绝。Count/defined-count/strict 政策固定，当前行状态与原量状态分离；旧 J1 重叠注册仍在。 |
| 精确物理资格键 | **纯声明与匹配通过；真实物理资格未验证** | 方法/版本、数值类型、Decimal precision/scale、有序域、backend/source/table/time/route 完整匹配；输入类型与域位置及方法元数、count 整数精度和其他数值方法精度的不一致均拒绝；冲突键、缺 checker/part/resource/evidence 拒绝。测试中的 Qualified 使用明确 test-only 证据标识，不对应生产后端资格。 |
| 义务及部件运输 | **静态准入通过；来源检查未执行** | 选择保留原 RuleDerivation 的 quantity、unit、state、Pre 与 pending obligations；补足每个本次输入绑定的检查及输出部件才可返回声明。裁剪部件、错误 contribution/method/coverage scope 会撤销原状态 rollup 的条件式 K；旧 scope 的 StatisticalWeight 既不能开启加权 K，也不能进入加权方法。后继仍须重新推导。 |
| 三态资格缺口及路线 | **精确拒绝通过；生产实现阻塞** | unsupported/unverified/blocked 的理由与 recovery 分立；缺省生产注册全部 blocked，无 qualified implementation。显式 Ibis、Ibis→Python、Artifact→Python 路线不可互补；拒绝或语义消费失败直接传播。没有执行器，因此不声称完成运行失败注入或后端无回退验收。 |
| 零 I/O 与旧注册隔离 | **定向反例通过** | 禁止 DuckDB/SQLite 连接、SourceSession、Store、Runtime 初始化/访问及旧注册查询后，装配/推导/精确选择仍完成；清空新注册时既有 core 入口拒绝，不回到本地或旧 registry。 |
| 公共披露与后续阶段 | **本轮无公共扩张；R3 整体未通过** | 未改 `__all__`、Help、CLI、site latest API 示例或 packaged skills；既有默认测试包含公共快照/披露回归。R3.3 图、R3.4 lowering、R4 Runtime 和 R5–R8 方法执行、R9 后端资格继续开放。 |

核验命令与实际结果：

- `make test TESTS='tests/test_analysis_methods_r32.py tests/test_analysis_core_r31.py'`：**63 passed**。
- `make typecheck TYPECHECK_TARGETS='marivo/analysis/core marivo/analysis/methods'`：**8 source files passed**。
- `make check-agent`：lint/format/import、**387 source files typing**、默认测试
  **5191 passed、64 skipped** 及 API 文档构建全部通过。历史 skip 未改变，不计本阶段通过。
- `make runtime-test TESTS='tests/test_analysis_dsl_public.py::test_public_j3_ratio_rollup_differs_from_current_row_mean'`：**1 passed**，确认旧 J1 重叠路线仍可执行；不计作新注册资格。
- 前一 R3.2 候选的站点 `npm run build`：**通过**；Astro check 为 0 errors / 0 warnings，构建 321 页，并通过英中安装脚本校验。本轮未重跑站点构建，未修改 `site/`。

没有运行全量 Runtime、release-check、六后端实源、wheel、冷恢复或真实 Agent。
生产恢复条件：连接 R3.3/R3.4 及对应 R4–R8 的真实消费者，证明精确物理键、
数值/资源条件，并由执行 owner 履行本次绑定检查；注册声明不替代这些证据。

代码 SHA-256（提交前内容哈希，用于定位本次实现）：

| 文件 | SHA-256 |
| --- | --- |
| `marivo/analysis/core/rules.py` | `9ebe6eab8fd11413ef32dc9a74c14a36c13ca468cffd7f0fa8fae888dd17d54e` |
| `marivo/analysis/methods/__init__.py` | `1d444ef8da6147c795d4ead808ca0eefec656505c60a6231ae8e102d3bd8432d` |
| `marivo/analysis/methods/errors.py` | `654b22197b5009c9e1c99bccff7deb84394e79cc42eea0bb2017aafd7e02d996` |
| `marivo/analysis/methods/physical.py` | `77db5ff8d67dbc918fb019fa0f896073395d09497d944dfd6c599a55c817f8ce` |
| `marivo/analysis/methods/registry.py` | `a9c44ccc4497d7aae5e01e1e0c027a09f116113f6bd0394bbcb0813a8cfea984` |
| `marivo/analysis/methods/semantics.py` | `465b03f0df34e158405400b99b66115ec2fbcccd77345135cd31330b4256cc00` |
| `tests/test_analysis_methods_r32.py` | `d979e5085df4cb4250f337a8a13656080435f39e0b46f428e690f7d543dee508` |


## R3.3 私有有类型图与纯计划（2026-09-27）

实施基线为 `panda` / `c2c9418676`，本节记录该基线上的 R3.3 交付。
`analysis/core/graph.py` 接通 R3.1 Signature/RuleDerivation 与 R3.2 注册，
`analysis/compiler/graph_plan.py` 提供私有分类、选路、阶段和 R4 检查交接。
结果类型的静态矛盾检查归 `MethodSemantics` 所有，不在图中复制方法语义。
本轮不更改生产物理资格，不迁移公共 J1/Dataset 执行，不实施 R3.4 lowering。

| R3.3 单元 | 本轮状态 | 独立预期、证据与恢复条件 |
| --- | --- | --- |
| 图身份、签名及角色 | **私有构造通过** | 独立同形节点指纹相同但 identity 不同；共享节点只产出一个阶段。重复 identity、环、错误角色、跨 owner、错误方法版本、定义指纹不匹配和篡改推导拒绝；缺少原状态部件及与已知字段/Metric 声明矛盾的输出类型在构造期拒绝。现场来源叶子只接受声明和 builder 依据；旧检查、观察或推导依据不得清除本次义务。 |
| 输入分类与早拒绝 | **私有分类通过** | 只访问执行根闭包；固定叶子不展开历史来源。深层混合、多 datasource 现场组合在选路前拒绝。BindProject 显式登记字段 owner、关系端点和 Metric computation roots 的来源依赖，来源依赖保持唯一且 scope 精确绑定，固定主体再读取现场字段仍判混合。 |
| 精确选路及阶段 | **测试专用注册通过；生产阻塞** | 独立断言 Ibis、Ibis preparation→Python、Artifact→Python 的阶段顺序、数量及共享行为。类型、表形态、时间形态不匹配拒绝；裸来源和裸固定叶子均不能产生读取计划。每个方法显式选择一个路线，不失败后换路，不把本地输出送回来源阶段。生产注册仍无合格实现；恢复需 R3.4/R4-R8 真实消费者和精确资格证据。 |
| 检查与物理事实交接 | **私有交接通过；执行未验证** | 保留原输入/范围及 consume/publish 截止义务；consume 绑定方法的首个消费阶段，publish 绑定最终输出阶段。子义务不丢失、条件 Post 不自动升级为 evidence。物理输入/输出类型、时间/表形态、精度和资源仍需 R4 实测及执行约束。 |
| 零 I/O 边界 | **哨兵反例通过** | 连接、SourceSession 构造/绑定/批读取、Artifact payload 读取、SessionStore 构造/Run admit/Artifact 查询均设置调用即失败哨兵，覆盖构造、三条测试路线计划与混合拒绝。没有实际提交、Run 分配或 Artifact 发布。 |
| 公共消费者与阶段出口 | **未迁移；R3 整体未通过** | 公共 J1/Dataset、旧 placement/source admission 和 Store 历史图保持原 owner，未作为新图 fallback。R3.2 跨执行单一 owner、R3.4 lowering、R4 Runtime/Store、R5-R8 方法执行和 R9 后端资格继续开放。 |

验证命令与结果：

- `make test TESTS='tests/test_analysis_graph_r33.py tests/test_analysis_core_r31.py tests/test_analysis_methods_r32.py'`：**90 passed**，其中 R3.3 新增 **27** 项。
- `make typecheck TYPECHECK_TARGETS='marivo/analysis/core/graph.py marivo/analysis/compiler/graph_plan.py marivo/analysis/methods/semantics.py'`：通过；最终全范围门禁覆盖其余源码。
- 定向 `make lint-agent`：通过；最终 `make check-agent` 覆盖所有修改文件的格式、lint、导入边界、**389** 个源码文件 typing、默认测试 **5218 passed / 64 skipped** 及 API 文档构建。
- `git diff --check`：通过。64 项历史 skip 不变，原断言、归属及恢复条件保留，不作为本阶段验收。

此处来源 Ref/定义指纹、Signature 与物理形态是传入的静态绑定；图不扫描来源或
读取 receipt 来证明它们。纯计划初版要求单一精确来源/时间形态，异构形态不隐式
转换；裸来源或固定叶子不能绕过方法注册授权读取。固定叶子若仍带待履行义务则拒绝。
R4 必须在消费前验证实际 schema、精确 Artifact 绑定及资源条件，并履行所有检查。
本轮没有六后端实源、全量 Runtime、wheel、冷恢复或真实 Agent 验收；没有修改
公共导出、Help、CLI、site latest 示例或 packaged skills，不将静态计划证据升级为
公共执行、后端或 R3 整体通过。


## R3.4 私有 lowering、局部规律与阶段披露（2026-09-28）

实施基线为 `panda` / `c8e782901125163d2bba382eb59ee637b1bed85e`，起点工作区干净。
本节记录未提交候选，不以基线 SHA 冒充新代码提交。候选源码与测试摘要为
`74397609172da55bff17ed15fc162459ccf9e3449ceafadcbc84186342ae26ea`：对以下
12 个仓库相对路径排序后，顺次计算 `path UTF-8 + NUL + file bytes + NUL` 的 SHA-256：
`analysis` 前缀均为 `marivo/analysis/`，清单为 `compiler/graph_lowering.py`、
`core/{local_laws,predicates,rules}.py`、`methods/{builtin,local,registry,semantics}.py`，
另含 `marivo/datasource/adapters.py`、`tests/test_analysis_lowering_r34.py`、
`tests/test_analysis_methods_r32.py`、`tests/test_datasource_adapter_contract.py`。
文档不包含在该代码摘要中。

本轮以 `analysis/compiler/graph_lowering.py` 消费 R3.3 GraphPlan，以
`analysis/methods/builtin.py` 将精确物理声明绑定到真实 lowerer 或固定本地 count
消费者。方法语义仍归既有单一注册；没有借旧 J1/Dataset registry 补路。
`core/predicates.py` 将有范围绑定的封闭 int64 谓词纳入图定义；
`core/local_laws.py` 实现明确比较层次的局部变换。
实际环境为 Ibis **12.0.0**、DuckDB **1.5.3**、PyArrow **25.0.1**。

| R3.4 单元 | 本轮状态 | 输入、独立预期、实测形态与恢复条件 |
| --- | --- | --- |
| 准入与 typed lowering | **私有交接通过** | 重验 GraphPlan，不接受篡改阶段/义务、遗漏/重复来源绑定、错 leaf identity、缺完整键/组件。只产出 Ibis 关系、带原 scope/deadline 的 SemanticCheck、输入 IntegrityCheck 或本地阶段；无 SQL 字符串、编译后修补或后端名称分支。 |
| 基础来源格 | **DuckDB table/native 与 parquet/parquet 的所列格通过** | int64 值、完整 int64 Entity 键、NoTime；直接 int64 字段绑定、projection/view/显式 where、exact_keys/one_to_one/union_keys/Subjects、全输入 count/count_defined。五行输入含 `9007199254740993` 及 Defined/Null/Undefined/Unknown；独立预期 count=5、defined_count=2、阈值 >6 只留 `(3,1,9)`，空来源保留 Defined 0 singleton。完整键并集是五个实际元组，不是各轴笛卡尔积。 |
| 来源检查与资源交接 | **表达式/真实提交通过；R4 调度与证据履行未验证** | 所有正例经真实 SourceSession 编译/批读，逐次检查提交 SQL 与 issued handle 相等并关闭流。重复身份、伪造 Cell、同定义不同节点的缺侧配对返回非空失败行，不以 distinct 修复；继承义务保持 pending。原候选关于每条表达式精确来源绑定的断言被同表独立叶子反例推翻；修正证据见下方 P2 更正。R1 PhysicalRequirement 显式列出 join/union，仅 DuckDB 接受；SQLite 拒绝新操作，未给其余后端授予资格。恢复需 R4 在消费/发布前调度检查并记录本次证据。 |
| 部件与主体集合像 | **所测运输通过** | 原 `sum/non_null_count` 与 boolean coverage 按角色和组件运输；改变保留角色顺序不串列，缺组件拒绝，裁剪原状态/coverage 撤销私有 state_rollup 续算条件。非单射主体映射得到 `{1,2,3}`；将同一映射声明为单射时产生两个重复组失败行。没有原量来源 rollup 资格。 |
| 固定本地 count | **本地方法与阶段交接通过；Artifact 读取未验证** | 固定叶子只产出 ArtifactReadStage 和已注册本地阶段/布局。四种 Cell 的独立预期 count=4、空输入=0；超 100,000 行拒绝。没有真实 receipt/Artifact 读取或历史来源展开，恢复需 R4 受控读、schema/绑定及资源核验。 |
| L1 | **int64 局部图变换通过** | 连续 >4、>6 与合并谓词得到相同固定行；签名、量定义、部件变化、依据、义务和私有 K 必须一致。不同 Unknown 政策及未资格 float64 变换拒绝；reject 政策将三类非 Defined 输入交为失败行，不悄悄丢弃。返回新节点，不自动合并原节点 identity。 |
| L7 | **固定坐标函数合成通过；语义/K 升级未授予** | 两个起点重复映射到同一主体再到同一组，独立预期保持两个映射条目和非单射性；角色链保持 participant→region，缺中间像或 scope 变化拒绝。没有重复贡献互斥或 rollup 许可。 |
| L8/L9 | **固定原状态等式通过；语义图改写/来源下推未授予** | 固定状态 `(5,1,a)、(7,1,b)、(0,0,c)` 的分层与直接结果为 `(12,2,{a,b,c})`，显式空目标仍为单位状态。L9 选空组与非空组时两侧分别保留 `(0,0,{})` 与 `(12,2,{a,b})`；选空域返回空状态函数。缺组件、RowStatistic、重叠贡献、不完整中间映射、改变范围拒绝。只返回 state 层比较，不 finish、不改量定义、不扩 K、不省略空组。 |
| 零 I/O 与公开披露 | **私有边界及既有披露回归通过** | lowering 对 SourceSession bind/qualify/compile/batches 和连接设置调用即失败哨兵；继承 R3.3 的混合早拒绝、Run/Store/Artifact 零 I/O 测试。只更新 Analysis/Datasource owning spec 和此记录；未改公共导出、Help、CLI、site latest 示例或 packaged skills。 |

核验命令与结果：

- `make test TESTS='tests/test_analysis_lowering_r34.py tests/test_analysis_core_r31.py tests/test_analysis_methods_r32.py tests/test_analysis_graph_r33.py tests/test_datasource_adapter_contract.py'`：**165 passed**，含 R3.4 的 **38** 项及两项 R1 新操作边界用例。
- 定向 typing、格式/lint 通过；最终 `make check-agent` 的格式/lint、导入边界、**394** 个源码文件 typing、默认测试 **5258 passed / 64 skipped** 和 API 文档构建通过。
- `npm --prefix site run build`：通过，API 文档、**321 页**站点和英中安装脚本输出校验通过。没有公共 API 变化，未改写 site latest 示例。
- `git diff --check`：通过。

未验证/阻塞格保持明确：R4 Run/Store、检查实际调度、receipt/Artifact 冷恢复、
R5–R8 公共方法与原 Metric 来源观察、source sum/mean/rollup、分组域执行、
Ibis preparation→Python、其余后端/类型/时间/部件形态、安装包和真实 Agent 均未验收。
恢复需 owning consumer 接入，并按完整资格键补独立结果和真实失败证据；本轮未运行
完整 Runtime 或 release-check，也未启动远端服务。历史 **64 skipped** 的原断言、
归属与恢复条件保留。R3.2 跨执行路径单一 owner 仍未通过，公共 J1/Dataset 未迁移；
**R3 整体未通过，R0/R1/R2 状态不因本轮改变**。


### R3.4 review correction: independent leaves on the same table (2026-09-28)

The P2 finding is confirmed. On the preceding candidate, two independent
SourceLeaf nodes bound to the same DuckDB table have structurally equal Ibis
relations. The exact_keys primary expression uses only the left operand, but
sources_for returned both bindings. The preceding green suite did not establish
its claimed precise source-identity handoff; its recorded results remain historical
rather than proof that this edge case passed.

The correction records ordered SourceLeaf source_ids on each LoweredRelation,
IntegrityCheck and SemanticCheck at construction. Unary expressions inherit their
operand's identities; union and field-owner joins combine their operands;
exact-key primary expressions retain the left identities while pairing checks
retain both. Deduplication applies only to repeated references to the same leaf.
sources_for resolves these records for the emitted expression object, never
Ibis relation equality. Untracked copies or derived expressions are refused,
with a repair directing the consumer to use the emitted expression or re-lower.
R1 physical ancestry validation remains independent and unchanged.

The new regressions cover table and Parquet forms for exact_keys, one_to_one and
union_keys, single-leaf validation expressions, inherited dependencies through
projection, reordered supplied bindings, repeated references to one leaf, and
rejection of structurally equal but untracked expressions. For the same-table
case, the test asserts structurally equal source relations and independently
asserts the left-only primary binding and actual R1 CompiledRead.source_identity;
two-sided checks continue to retain both identities.

- Red reproduction: `.venv/bin/pytest -q -n 0 'tests/test_analysis_lowering_r34.py::test_same_table_leaves_keep_expression_source_identity[table-exact_keys]'` failed on the preceding candidate because the returned tuple contained the right leaf as an extra dependency.
- Corrected targeted gate: `make test TESTS='tests/test_analysis_lowering_r34.py tests/test_analysis_graph_r33.py tests/test_datasource_adapter_contract.py'`: **112 passed**, including **48** R3.4 cases (**10** new cases).
- `make typecheck TYPECHECK_TARGETS='marivo/analysis/compiler/graph_lowering.py'`: passed. Final `make check-agent` passed formatting/lint, import contracts, typing for **394** source files, **5268 passed / 64 skipped**, and API documentation. `git diff --check` passed.
- Corrected uncommitted code/test digest, using the same 12-file manifest and algorithm above: `3c93838a1d41527537a240997366ae217f903b32b273154fbee8b6cab1f163b6`. The base commit remains `c8e782901125163d2bba382eb59ee637b1bed85e`.

This correction does not qualify R4 scheduling/publication, public J1/Dataset
migration, additional backends or whole-stage R3 acceptance. The historical skip
assertions, owners and recovery conditions are unchanged.


## R3 acceptance corrections (2026-09-28)

Baseline: `899eac66b555f66ff9ecc52ceedb617c0ef89dd0`. This section supersedes the
historical connected-J1 owner gap above; it does not rewrite prior test evidence
or qualify unconnected methods. Changes remain an uncommitted working-tree
candidate until separately committed.

- **Shared graph obligations:** lowering retains separate ordered input groups
  per originating check. `union_keys(paired, paired)` no longer flattens the same
  two-input pairing into four inputs. Equal symbolic obligations from independent
  pairs remain separately checked. DuckDB native-table and Parquet regressions
  independently expect the five complete keys
  `{(1,9007199254740993),(1,2),(2,1),(2,2),(3,1)}`; restricting only the second
  independent pair produces violation counts `[0,3]`, not a dropped check.
- **Connected semantic owner:** existing J1 current-row sum/count/mean, original
  sum rollup/group and difference consumers resolve through `methods.REGISTRY`.
  Their six old registrations are deleted. Consumer layouts derive Cell policy,
  units, components, checks and empty reasons from the registered semantics.
  Missing canonical methods reject; no old registration or route is a fallback.
  Existing part/check encodings, method IDs and physical qualifications compare
  equal to baseline, except the count policy disclosure is corrected from
  `strict` to `count_all`, matching unchanged count behavior. J1-specific layout
  qualifications do not qualify any additional `GraphPlan` key.
- **SQLite fetch-timeout test:** the old 10ms timer versus 50ms UDF sleep could
  lose its scheduling race under xdist. The regression now invokes the actual
  SQLite interrupt from the second row's UDF, observes that the timeout remains
  armed during fetch, and checks cancellation afterward. No product timeout
  behavior or timeout duration was changed.

Validation of this candidate:

- Targeted registration, construction, real source/local, exchange, lowering and
  SQLite checks: **153 passed** via `make test` on
  `test_analysis_methods_r32.py`, `test_analysis_dsl_j1_construction.py`,
  `test_analysis_dsl_j1_source.py`, `test_analysis_dsl_s2_p1.py`,
  `test_analysis_dsl_exchange.py`, `test_analysis_dsl_contracts.py`,
  `test_analysis_lowering_r34.py`, and `test_datasource_sqlite.py` (all under
  `tests/`). The Runtime file supplied to that default invocation is excluded by
  its marker and is accounted for separately below.
- `make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py'`:
  **6 passed**, including fixed/source execution, receipt identity, reuse,
  publication failure and mixed-input rejection.
- Final `make check-agent`: **passed**; format/lint/import boundaries, typing for
  **396 source files**, **5280 passed / 64 skipped**, and Sphinx API build.
- `git diff --check`: passed. No site source or public API changed, so a new
  site build was not required; no package/release/full-Runtime gate was run.

Changed source/test candidate digest: `ed82e5d98755242483b2464a1b7845c92276e17075474e0432a48932875b624d`.
Reproduce by sorting the union of `git diff --name-only HEAD -- marivo tests` and
`git ls-files --others --exclude-standard -- marivo tests`, then hashing each
`path UTF-8 + NUL + file bytes + NUL` in order. Documentation is excluded.

Historical 64 skips retain their assertions, owners and recovery conditions. R4 Run/Store,
new graph public execution, six-backend full qualification, installed-package and
real-Agent acceptance remain outside these corrections. R0/R1/R2 and whole-R3
acceptance are not promoted by this entry.

## R4.1 统一协议与交接冻结（2026-09-28）

实施起点为干净的 `panda` / `53e388d62007c2c2eb5f162ee72b10c19951b59d`；
R3 corrections 已在 `3d0ef2a332` 提交，不再是上一节所述的未提交候选。
本包只修改 Analysis 三份 owning specs 与 R4 实施计划。检查期间出现与本包无关的
`tests/test_r13_control_boundaries.py` 工作区改动；未纳入本包、未清理或归因于 R4.1。

| R4.1 单元 | 本轮状态 | 契约证据及后续恢复条件 |
| --- | --- | --- |
| 新代际和唯一身份 | **契约冻结；产品未实施** | Store 7 与七个新 `marivo.analysis.* /v1` 协议、source/fixed 规范 key 字段及唯一 owner 已写入 [Session/Runtime](../../specs/analysis/session-state-and-runtime.md#r41-frozen-runtime-and-store-target-inactive)和 [Analysis 设计](../../specs/analysis/python-analysis-design.md#r41-frozen-graph-to-runtime-handoff-inactive)。R4.2/R4.4 必须实现同代际 Run、key、Store 与旧代际读前拒绝。 |
| 交换、状态、检查与故障 | **契约冻结；执行未验证** | [方法/状态](../../specs/analysis/operators-and-frames.md#r41-frozen-method-state-and-evidence-target-inactive)列出封闭变体、主表/parts receipt 和本次检查证据；Session spec 固定准入、失败、提交不明和进程退出的可观察状态。R4.3/R4.4 须以真实流、注入故障及协调证明。 |
| 旧入口/codec 删除交接 | **静态盘点通过；删除未实施** | [R4 计划 §4.1](2026-09-28-marivo-full-algebra-dsl-r4-implementation-plan.md#41-r41-协议冻结与删除矩阵仅契约未切换产品)逐包映射公共 DSL、旧 Dataset/J1/J4、key、descriptor/receipt/snapshot、Store/恢复以及未迁的家族 publication/codec。新链缺方法/形状/后端资格按 owner 早拒绝，恢复条件逐行登记。R4.2–R4.6 执行删除并反查安装包。 |
| R4 V01–V12、四旅程及旧代际拒读 | **未验证** | 本轮无 Runtime/Store/公共产品改动、无新 wheel、断源冷恢复、故障或 Agent 执行证据；不能把契约冻结计作任何运行格通过。 |

静态反查命令
`rg -l 'J1Context|J1Node|execute_j1|J3Observed|marivo\.dataset_execution_key/v[12]|marivo\.j1_artifact_exchange/v[123]|marivo\.analysis\.public_continuation/v1|dataset_artifact_descriptor/v[12]' marivo/analysis`
得到 **15** 个仍含旧路线的产品文件；
`rg --files marivo/analysis/materialization | rg '(codec|publication)\.py$'`
得到 **22** 个 codec/publication 文件，均在删除矩阵按具体家族或通用发布归属。
本包四份契约文档的本地链接和新 R4.1 anchor 检查 **41 项、0 错误**；
`git diff --check` 通过。四份契约文档（不含本验收记录）的
`git diff --binary HEAD --` SHA-256 为
`b93c564d6e0da12386be87ac182aaa8d1bfe4ca56ddf2953c8deea91bd21827f`；
限定输入避免在验收记录中存放自身 diff 的循环 hash。R4.1 未改产品或测试代码，
未运行 `make test`、Runtime、typecheck、站点构建或发布门禁。
历史 **64 skipped** 的断言、owner 与恢复条件保持原状，R0–R3 整体资格和
R4 V01–V12 不因本节提升。


## R1 整体验收复核（2026-09-28）

按 [R1 实施计划](2026-09-26-marivo-full-algebra-dsl-r1-implementation-plan.md) §3 的五项出口,
在 R1.1–R1.5 各节既有分项证据之上执行整体验收。产品/测试基线为 `panda` /
`53e388d62007c2c2eb5f162ee72b10c19951b59d`(运行期间后续提交 `3d0ef2a332`、
`09fcee3ee6` 均为文档/已记录候选,`git diff --stat -- marivo tests` 在
`53e388d620..09fcee3ee6` 区间为空,不影响本节产品结论)。除下述一项 Trino
测试修正外,本轮未改产品或测试代码。本地版本:Ibis **12.0.0**、DuckDB **1.5.3**、
SQLite **3.53.1**、PyArrow **25.0.1**、psycopg **3.3.4**、mysqlclient **2.2.7**、
Trino Python **0.337.0**、clickhouse-connect **1.3.0**。

### 出口逐项判定

1. **六 adapter 同一接口正反例、未选驱动隔离、单一资格 owner:所测通过。**
   `make test TESTS='tests/test_datasource_typed_specs.py tests/test_datasource_metadata.py
   tests/test_datasource_metadata_schema_only.py tests/test_datasource_json_source.py'`
   为 **106 passed / 46 skipped**(46 项为远端 opt-in,见下);adapter contract、
   raw SQL、source-health、engine profiles、authoring 六文件合计 **234 passed**;
   `test_selected_provider_does_not_import_unselected_modules` 及契约反例在
   `test_datasource_adapter_contract.py` 全数通过。六后端实源正反例见下表。
2. **受治理读取的 Ibis 构造与原样提交:新链通过;旧文本路线仍在源码中。**
   `statement(sql)` 仅存在于
   `marivo/analysis/materialization/{duckdb,postgres,scalar_sql}_execution.py` 旧具体类,
   `marivo/analysis/materialization/execution.py` 的共用 `ExecutionAdapter` 协议无该方法;
   R1.1 阻断测试(`test_r11_legacy_domain_block.py` **11 passed**,含把旧 DuckDB
   `statement` 钉死后 Population/基础 Metric 仍成功)证明新链不调用旧文本路线。
   旧类的文本方法按计划归 R4–R9 删除,不因本节转授资格。
3. **来源、metadata、样本、探测与 source-health 事实范围及资源生命周期:所测通过。**
   本地窄测合计:typed specs/metadata/JSON 106、raw SQL/adapter contract/source-health 85、
   engine profiles/authoring 149、lazy execution adapter/admission 60、
   sqlite/control boundaries/postgres errors 14、runtime acceptance/scalar type/doctor/
   scoped preview 58、DuckDB 文件与 runtime 54,全部通过。
4. **`md.raw_sql` 终端边界:所测形态通过;MySQL/ClickHouse timeout 格保持阻塞。**
   `md.connect`/`DatasourceConnection` 公共面已删(`marivo/datasource/__init__.py`
   仅导出 `raw_sql`);`from_sql`/`parity_check`/`postprocess_sql` 生产代码零命中。
   `test_datasource_raw_sql.py` 全量通过,含 typed reentry 拒绝、截断、错误 repair。
   阻塞格见下表,与 R1.3/R1.5 记录一致。
5. **测试、typing、lint、披露与文档门禁:通过;跳过归属不变。**
   `make check-agent`:format/lint/import、**396** 个源码 typing、默认测试
   **5280 passed / 64 skipped**、API 文档全部通过。64 项 skip 维持 45 丰富
   metadata + 14 R5 正例 + 5 原有的归属与恢复条件。`npm --prefix site run build`
   通过(Astro 0 errors / 0 warnings,英中安装脚本校验);`git diff --check` 通过。

### 六后端实源复核(本轮实际重跑)

服务经 `tests/multisource_environment/manage.sh` 分组启停;起始 7 个容器全部
Exited,结束时恢复 Exited(6 个 Exited(0)、trino Exited(143)),卷未清理,轮末
`colima stop marivo-multisource` 已停机。opt-in 用例均以
`RUNTIME_WORKERS=1`(或等价 `-n 1`/`-n 0`)串行执行。

| 后端(服务版本) | 本轮命令与结果 | 相对 R1.2/R1.5 记录的变化 |
| --- | --- | --- |
| PostgreSQL 17.11 | `MARIVO_POSTGRES_ANALYSIS_TEST=1 ... tests/test_r12_source_adapters_runtime.py tests/test_r13_control_boundaries.py`:表/view/namespace/精确 Decimal 与终端只读/UTC/服务端 timeout **2 passed**,10 skipped 为其他后端 opt-in | 无变化 |
| MySQL 8.4.11 | 同组:表/view/Decimal、无效日期/只读、不可验证时区结构化拒绝 **3 passed**;单键基础 Analysis 9 项(`test_group_a[population]`、`test_single_key_basic_metrics_use_source_session`、`test_single_key_view_population_uses_source_session`、`test_single_key_population_rejects_invalid_identity`、`test_uint64_composite_identity_validation`)** 9 passed / 2 skipped**(2 项为 ClickHouse opt-in) | 无变化;多列主键仍在 Run 前阻断 |
| Trino 483 | Iceberg/non-Iceberg 表/view、Iceberg 分区绑定读取 **2 passed**;`test_trino_terminal_session_timeout_and_timezone_are_effective` 首次复跑 **FAILED: DID NOT RAISE** —— 见下方修正;修正后全组 **3 passed** | **发现并修正一项测试校准缺口**(见下) |
| ClickHouse 26.3.33.24 | MergeTree 精确 Decimal、`system.parts`/分区绑定读取、只读账号 timeout 前置阻断 **3 passed**;`MARIVO_CLICKHOUSE_CLUSTER_TEST=1` Distributed 双分片合计 **1 passed** | 无变化 |

### 本轮修正:Trino 终端 timeout 用例负载不足

`test_trino_terminal_session_timeout_and_timezone_are_effective` 原以
`sequence(1,10000) CROSS JOIN sequence(1,10000)`(1 亿对乘积)作为超过 1 秒
`query_max_run_time` 的负载。本机实测该查询 **1.2 秒内正常完成**,落在 Trino
周期性强制窗口边缘之下,故未触发 `EXCEEDED_TIME_LIMIT`,断言
`pytest.raises(TrinoQueryError)` 失败。该失败是**测试负载校准缺口**,不是控制
能力缺陷:以三路 cross join(3 亿对乘积)实测,同一 1 秒 session timeout 在
**1.06 秒**内返回 `TrinoQueryError EXCEEDED_TIME_LIMIT`,`SHOW SESSION LIKE
'query_max_run_time'` 仍为 `1s`,`current_timezone()` 仍为 `UTC`,`CREATE TABLE`
仍被服务端拒绝。已将测试负载改为三路 cross join 并附注释说明快主机上的完成窗口,
原断言(`EXCEEDED_TIME_LIMIT`、5 秒内返回、CREATE 拒绝)全部保留。
修正后该用例通过。这是对 R1.3 候选记录中该格的更正:控制有效性证据成立,
但原负载在本机不可复现,不能作为已验证据引用。

### 结论与保持开放的格

- **C01.a:部分通过。** 六后端基础表/view 形态、typed spec、schema-only metadata
  披露均有实测;丰富 metadata(45 断言)、DS15 认证 HTTP 保持 blocked/skip。
  （2026-09-28 R1.6 后已更新:丰富 metadata 45 断言恢复、DS15 闭合,见 R1.6 节。）
- **C01.b:部分通过。** 所测形态均有原样提交对照、精确解码、空流/早关闭/断连;
  远端服务器端终止证明、六后端适用 Null/非有限 float/时间精度全矩阵未验证;
  旧具体执行类文本方法仍在源码中(归 R4–R9)。
- **C01.c:部分通过。** DuckDB/SQLite/PostgreSQL/Trino 所测控制通过;MySQL 无可执行
  timeout、ClickHouse 只读账号不能设置 timeout 保持**阻塞**;公共连接旁路已删除。
  （2026-09-28 R1.6 后已更新:认证 HTTP 格闭合,timeout 格保持阻塞,见 R1.6 节。）

R1 五项出口中第 1、3、5 项在所测范围内通过;第 2 项新链闭合但旧文本路线源码待删;
第 4 项含 MySQL/ClickHouse 精确阻塞格。依据计划"未选择的阻塞格不以降级填平"的
规则,**R1 整体判定:部分通过,保持未整体通过**。本轮变更仅
`tests/test_r13_control_boundaries.py` 一处测试负载修正(+5/−2 行),该文件
SHA-256 为 `72c9b1655f24bb44b5c463525c8d5e27f7ea7c85702118b2be0917da72b41ec7`,
提交前 `git diff --binary HEAD -- tests/test_r13_control_boundaries.py` 的
SHA-256 为 `d55d1a7a92335b3038c1ef6cf04233280223d8d082ed4e3e15fa9d03eb25daa8`。
未运行完整 release-check、MinIO、wheel 安装或真实 Agent 旅程。

## R4.2 私有图协调增量（2026-09-28）

本包按 [R4 实施计划](2026-09-28-marivo-full-algebra-dsl-r4-implementation-plan.md)
修订后的私有边界实施；检查时 `panda` HEAD 为 `795edc44d6b7`。
同期存在 R1 整体验收复核的独立文档改动，本节只记录 R4.2 新增内容，
不将 R1 的测试、后端资格或结论归因于 R4.2。

| 单元 | 本轮状态 | 证据与剩余条件 |
| --- | --- | --- |
| 图准入与唯一协调 | **私有定向通过，公共未切换** | `DatasetRuntime._prepare_graph` 在现有 Session owner 下调用 R3 `GraphPlan`；mixed、跨 Session 与未取得精确资格的方法在私有阶段消费者、业务 I/O、Artifact 行读取和 Run 分配前拒绝。显式共享节点单次调度一次，独立同定义节点分别调度；每次调用重新消费阶段。实际来源读取、Run、Store 和 Artifact 发布未接入。 |
| 检查与失败边界 | **私有定向通过，执行证据未取得** | 检查保留原始 requirement 的节点、作用域和有序输入，在 consume/publish 期限按阶段调度；检查或已选阶段失败后不执行后续阶段或换路。合成消费者不产生 `completed` 检查证据，真实流耗尽、取消及资源关闭仍归 R4.3/R4.4。 |
| 新 execution key | **纯构造通过，命中未验证** | `Implementation.contract_version` 初始为 1；`marivo.analysis.execution_key/v1` source/fixed 两类使用 typed-tuple SHA-256。固定 golden digest、等价独立节点、Run ref 新求值、精确输入引用/顺序、primary/part receipt、绑定、状态和 snapshot 扰动均有定向断言。对抗性复核后补测同一固定叶子占据两个输入槽位：读取阶段仍为一个，key 必须接收两条有序记录，少一条即拒绝，交换槽位绑定会改变 key。构造器不读取 Store，不验证文件内容，也不授权缓存命中；真实 v7 Run/receipt/Store 闭环归 R4.4/R4.5。 |
| 产品切换与 V01–V12 | **未验证** | `execute_j1`、旧 Dataset 路线和 v6 公共产品链暂保留，未写新 Artifact。V01–V03 只有私有子断言，产品级 I/O、Run、命中、共享与检查计数不能据此判为通过；V04–V12、四旅程、断源冷恢复、wheel 和真实 Agent 均未执行。公共切换与旧入口删除须在 R4.3/R4.4 同代际闭环后于 R4.5 完成。 |

定向 `make test TESTS='tests/test_analysis_graph_runtime_r42.py tests/test_analysis_graph_r33.py tests/test_analysis_lowering_r34.py'`
为 **87 passed**（R4.2 新增 **8** 项）；旧公共路径
`make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py'` 为 **6 passed**。
受影响源码的 `make typecheck TYPECHECK_TARGETS='marivo/analysis/materialization/graph_execution.py marivo/analysis/materialization/execution_key.py marivo/analysis/materialization/admission.py marivo/analysis/methods/physical.py'`
和对应 `make lint-agent LINT_TARGETS='...'` 通过。最终 `make check-agent`
的格式/lint/import、**397** 个源码文件 typing、默认测试 **5288 passed / 64 skipped**
和 API 文档全部通过；`git diff --check` 通过。64 项历史 skip 的归属与恢复条件不变，
不计本包验收或整个 R4 通过。

## R4.3 统一交换与方法执行私有增量（2026-09-28）

起点为 `panda` HEAD `433ce97c9547c7ec2a9d3648ad01979335ae3b55`；
[R4 实施计划](2026-09-28-marivo-full-algebra-dsl-r4-implementation-plan.md)
SHA-256 为 `77b506be0155357fa9e620f40ba0d1f8c8a548bd5a109795695f07a20cee9bf3`。
本节不提交候选，不能把基线提交 SHA 当作新增代码 SHA。R1 整体验收复核的
既有未提交文档改动保持原样；本节只增加 R4.3 归属，不转授 R1 后端或控制证据。
本包 20 个源码、测试和 owning spec 候选按仓库相对路径排序，并依次将
`path UTF-8 + NUL + file bytes + NUL` 输入 SHA-256，得到候选摘要
`ce058c09257f6f023181a49b67622a6d760a147f0ad6ef056f8f53e17f1f0867`。
关键文件内容 SHA-256：`graph_lowering.py` 为
`6fd126c32452422f50cbeb550b63f87c37ae8e02ae620009fe6aa317a79d34ba`，
`graph_exchange.py` 为
`c952e7d72dd566dab75afa31059e629c118f728d147937eda324049dadb5b2a9`，
`graph_source_execution.py` 为
`d317f7bf165fa45afec6f1281607fc231fa8356f0d04bc7d59f465497755f9b0`，
`graph_local_execution.py` 为
`5468a4d143d01d0efd71c084dd1ace6aa08204f0db6372566e2952f462db5bc7`，
`graph_spearman_execution.py` 为
`ff724e058cb5b401206545be43eeda49842a767f7b67504269a0e59d09d264b7`。
以下每格均以该 20 文件候选摘要为共同代码 SHA；命令与结果只证明本机当次
所测私有范围，不能扩大方法或后端资格。

| 私有单元及候选 SHA | 实际命令与结果 | 未验证、阻塞及恢复条件 |
| --- | --- | --- |
| **精确资格与早拒绝**：`ce058c09…0867`；DuckDB native table/Parquet、`NoTime`、完整 int64 身份，已选来源 `bind_project`、`parts_transport`、`map_correspond`、`row.count`、`row.count_defined`、int64 `row.sum`/`row.mean`；固定 `row.count`/`row.count_defined`/int64 `row.sum`/`row.mean`；Spearman 的 ordered int64/float64 两端点分别走 `ibis`、`ibis_python` 和 fixed Python。 | `make test TESTS='tests/test_analysis_methods_r32.py tests/test_analysis_graph_r33.py tests/test_analysis_graph_runtime_r42.py tests/test_analysis_lowering_r34.py tests/test_analysis_dsl_exchange.py tests/test_datasource_adapter_contract.py'`：**232 passed**。Decimal、时间形状与未登记组合在业务读取前拒绝；已有固定 `row.count` 的 100,000 行限制保持。 | J1–J3 其他旧数值/分组/ratio/比较与原始状态归约尚未取得新 owner 的精确消费证据，保持 **阻塞**；须分别实现检查、状态/parts 和 source/fixed 反例后才能注册。其他后端、表形态和时间/Decimal 数值资格 **未验证**。 |
| **真实来源、共享与检查**：`ce058c09…0867`；`LoweredPlan.sources_for()` 定位 R1 绑定，原样编译已选表达式；共享结果由同一 `SourceSession` 临时 Arrow 表承载并在退出时清理。 | 同一 232 项定向命令：source 表/Parquet、实际 SQL 来源读取计数、原节点 consume/publish 检查、重复键、非 Defined、迭代异常及取消反例 **通过**；`make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_r12_source_adapters_runtime.py'`：**6 passed / 8 skipped**。 | 远端后端与服务端取消终止 **未验证**；8 项 opt-in skip 保留原恢复条件，不计通过。R4.2 的合成调度证据不能替代本轮实际 I/O，也不转授产品执行。 |
| **三生产者交换、receipt 与状态**：`ce058c09…0867`；source、完整 receipt Parquet、pandas→Arrow 共用 schema/四态 Cell/完整键校验，主表、part 和方法状态向量独立按键核对；待执行检查与本次 completed 证据同在 transient contract。 | 同一 232 项定向命令：三生产者空流、四态、大 int64、分批、part 换序、坏 receipt、缺 part、未耗尽及关闭失败、错误状态向量反例 **通过**。固定 `row.count`/Spearman 测试钉死 DuckDB 与来源 Session 构造入口；共享同一固定叶子只读一次。 | v7 codec、持久 receipt、Artifact 发布和断源冷恢复 **未实施**，属 R4.4/R4.5；本轮完整 receipt 仅是现有 `LocalReceipt` 物理校验，不把旧 Artifact 升为新协议。 |
| **J4 Spearman 单一语义 owner**：`ce058c09…0867`；同键配对、Null、平均秩、常量/不足对状态和 `pair_counts`，两条来源实现及 receipt 校验后的固定续算使用新注册消费者，不调用旧场景派发或 codec。 | 同一 232 项定向命令：table/Parquet × 两来源路由的空流、并列秩、反向完整对、Null、常量、未知 Cell 拒绝，及 fixed 反向/共享叶子测试 **通过**。 | 旧 `dsl_j4_source.py` 仍供 v6 公共调用，须待 R4.5 切换后删除；公开 J4 K、断源恢复和四旅程 **未验证**。 |
| **局部门禁与产品边界**：`ce058c09…0867`；本包不分配 Run、不写 Store/Artifact，不改变公共 API、Help、CLI、英中 latest 或 packaged skills。 | `make typecheck TYPECHECK_TARGETS='marivo/analysis/compiler/graph_lowering.py marivo/analysis/core/model.py marivo/analysis/core/rules.py marivo/analysis/methods/builtin.py marivo/analysis/methods/local.py marivo/analysis/methods/registry.py marivo/analysis/methods/semantics.py marivo/analysis/materialization/graph_exchange.py marivo/analysis/materialization/graph_local_execution.py marivo/analysis/materialization/graph_source_execution.py marivo/analysis/materialization/graph_spearman_execution.py marivo/datasource/adapters.py'`：**12 个源码文件通过**；对应 `make lint-agent LINT_TARGETS='…'`：**17 文件通过**；`make check-agent`：格式、lint/import、**401** 个源码文件 typing、**5341 passed / 64 skipped** 和 API 文档通过；`git diff --check` 通过。 | R4 V01–V12 产品格、Run/Store 唯一性、新 Artifact、并发、冷恢复、wheel、真实 Agent 均 **未验证**；64 项历史 skip 保持原 owner 与恢复条件，绝不计为通过。本包无提交、推送或发布。 |

本轮判定为**所列精确资格内的 R4.3 私有执行通过**，而非完整 J1–J4 产品
或整个 R4 通过。J1–J3 仍阻塞的变体、v7 发布和公共切换按上表恢复条件
继续实施；旧 v6 公共调用的既有结果不作为新链验收。

### R4.3 对抗性审查修复（2026-09-28，未提交）

基线仍为 `433ce97c9547c7ec2a9d3648ad01979335ae3b55`。此前的候选
SHA 与命令保留为历史证据；以下补充替代其涉及两个反例的验收结论，
不修改独立 R1 工作或提升 V01–V12 产品格。

| 修复及文件 SHA-256 | 命令与观察结果 | 边界 |
| --- | --- | --- |
| P1：方法 owner 按完整键校验主表与数值 part，拒绝 mean `3.5` 与 sum/count `999/4`、错误/负 count 及矛盾 Spearman pair counts。`methods/state_validation.py`: `c6c22b57a86374b5130e719ef57c280832e1026d32f1e4c02780e0364d773c24`；`materialization/graph_exchange.py`: `a8a89f786eaa1de44be277d5394e9f2a1a0e8f2b1f950a82338deecb775e92ca`。 | `make test TESTS='tests/test_analysis_dsl_exchange.py tests/test_analysis_lowering_r34.py tests/test_analysis_graph_runtime_r42.py tests/test_analysis_methods_r32.py'`：**168 passed**，覆盖 Arrow/pandas 矛盾状态拒绝以及已有 source/fixed 正常执行。 | 只验证已资格化 transient 状态；不授予原始状态归约或新 Artifact 权限。 |
| P2：固定行方法在 receipt 打开前比较 leaf 与 Arrow value 物理类型。`materialization/graph_local_execution.py`: `cd7a047259a28c9417e441efe39a5cd3980729434245728238b570b62e047f8e`；测试 `tests/test_analysis_dsl_exchange.py`: `eb1d55d4229cea0668b3c18b509b2966b36b8aab5b61b8bab8811c5c62f40260`。 | 同一 **168 passed** 命令；Decimal、timestamp、float64 与 int64 资格矛盾时结构化拒绝，测试钉死 receipt 读取入口。 | 未扩大 Decimal、时间或 float64 固定行方法资格；数据是否全 Null 不影响类型早拒绝。 |
| 修复门禁 | `make typecheck TYPECHECK_TARGETS='marivo/analysis/methods/state_validation.py marivo/analysis/materialization/graph_exchange.py marivo/analysis/materialization/graph_local_execution.py'`：**3 文件通过**；同三文件及 exchange 测试的 `make lint-agent LINT_TARGETS='…'`：**4 文件通过**。`make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_r12_source_adapters_runtime.py'`：**6 passed / 8 skipped**。 | opt-in Runtime skip 不计通过；远端后端、冷恢复、发布与真实 Agent 仍未验证。 |
| 广域门禁 | `make check-agent`：格式、lint/import、**402** 个源码文件 typing、**5341 passed / 64 skipped**、API 文档通过；`git diff --check` 通过。 | 历史 skip 保持原恢复条件，不计通过；无提交、推送或发布。 |


## R1.6 Provider 能力通道与丰富 metadata 恢复（2026-09-28）

用户于 2026-09-28 明确批准内部 SQL 例外："将这些接口必要的部分使用统一的数据源的接口抽象，每种源提供自己的实现，允许使用非 Ibis 的其他手段实现具体能力"。例外范围：操作=六后端 metadata 事实读取与 DuckDB scoped HTTP 凭据安装；后端=六后端 metadata、仅 DuckDB 凭据；用途=datasource metadata 检查与带作用域认证 HTTP JSON 读取。台账见 [R1.6 覆盖节](2026-09-26-marivo-full-refactor-r0-sql-ledger.md#r16-当前状态覆盖2026-09-28)与新增 DS22 行。五个独立提交：`f3d5a7a9e1`（能力通道，无行为变化）、`1a1822fc8d`（DS15 认证 HTTP）、`f6c5deb613`（DuckDB/SQLite metadata）、`306ef75821`（四远端后端 metadata + 主键）、R1.6e（本节文档收口）。

| 单元 | 本轮结论 | 证据与边界 |
| --- | --- | --- |
| 能力通道与注册表 | **通过** | `marivo/datasource/capabilities.py`：`ProviderStatement` 固定模板（严格字面量/标识符槽位渲染，值经转义嵌入——Trino `raw_sql` 不支持参数占位符）、注册表拒绝未注册/重复 ID、每次提交记录 `ProviderStatementSubmission`（provider/statement_id/purpose/sql/state）挂 backend。快照测试钉死全部 45 条模板文本 SHA-256；SQL 文本变更必须显式过快照关。DuckDB 连接本地结果不 close（close 会断连，adapters `_DuckDBCursor` 同语义） |
| DS15 认证 DuckDB HTTP | **通过** | 连接期参数化 `CREATE OR REPLACE SECRET marivo_http_auth (TYPE HTTP, BEARER_TOKEN ?, SCOPE ?)`——凭据值绝不进入 SQL 文本或提交记录；`DuckDbHttpCredentials.headers_for` 仅在 `url_is_in_http_scope`（scheme+netloc 相等、path 前缀）内返回凭据。非凭据 owner 的 provider 在秘密解析前结构化拒绝。实测：duckdb_secrets 计数=1、scope 外/异 host 不发送、`md.test` 往返后项目 store 只含 env 名、token 不入错误信息 |
| DuckDB/SQLite 丰富 metadata | **本地实测通过** | DuckDB：注释/可空性/序数（duckdb_columns）、主键/唯一约束（duckdb_constraints）、视图检测（duckdb_views，含 schema/database 限定与默认命名空间解析）、物理 profile（estimated_size）。SQLite：`sqlite_schema` 类型/定义、`pragma_table_info` 可空性（含 STRICT/WITHOUT ROWID/INTEGER rowid 别名启发）、`pragma_index_list`+`index_info` 唯一约束；只读模式经现有 authorizer 白名单（pragma 函数表值调用=SQLITE_FUNCTION）。7+1 个原 skip 恢复为真本地断言 |
| 四远端后端丰富 metadata | **远端实测通过** | PostgreSQL 17.11：`obj_description`/`col_description` 注释、`information_schema.columns` 可空性、`pg_get_partkeydef` 分区、`reltuples`+`pg_total_relation_size` profile、**新增** `pg_constraint`+`pg_attribute` 主键/唯一约束、**新增** `pg_class.relkind`+`pg_get_viewdef` 视图检测（旧实现未做）。MySQL 8.4.11：`information_schema.tables` 注释/行数/大小、`SHOW FULL COLUMNS` 类型/可空性、**新增** `SHOW INDEX` 主键、视图检测。Trino 483：`information_schema.columns`、`SHOW COLUMNS` 列注释、`SHOW CREATE TABLE` 表注释/分区（含 month()/bucket() 变换）、`SHOW STATS` profile、**新增** `information_schema.table_constraints`+`key_column_usage` 主键、视图检测。ClickHouse 26.3：`system.tables/columns/parts/parts_columns` 全套（分区变换解析、Distributed 解引用、MergeTree/Distributed 双分片）。opt-in 命令逐组 `MARIVO_<BACKEND>_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_r12_source_adapters_runtime.py' RUNTIME_WORKERS=1`：PG 1 passed、MySQL 2 passed、Trino 2 passed、CH 2 passed + cluster 1 passed |
| 诚实的不可用披露 | **保持** | MySQL SELECT-only 账号可见视图类型但 `VIEW_DEFINITION` 需 SHOW VIEW 权限——保持 None；ClickHouse 读账号无 `SELECT ON system.parts`——物理 profile/projectable columns 披露 unavailable 不伪造；ClickHouse 无主键概念——`primary_keys_unavailable` 由 provider 自身披露（dispatcher 与直连路径一致）。四远端 r12 断言如实钉住这些边界 |
| 主键豁免收口 | **通过** | `_with_primary_key_capability_warning` 豁免集扩为五家（duckdb/sqlite/postgres/mysql/trino 均真实填充主键）；ClickHouse 唯一真实不可用。45 个原断言中四家的主键断言按新语句实测 |

核验命令与结果：

- 本地定向 `make test TESTS='tests/test_datasource_metadata.py tests/test_datasource_metadata_schema_only.py tests/test_datasource_sqlite.py tests/test_datasource_provider_capabilities.py'`：**91 passed / 1 skipped**（skip 为 SQLite 通道失败传播路径的记录性跳过，dispatcher 回退覆盖）。**45 个丰富 metadata 断言全部恢复，0 项 R1.3 skip 残留**。
- 广域相邻 datasource 全 family + source_health + doctor + Help/live registry 定向：**223 passed / 1 skipped**。
- `make typecheck TYPECHECK_TARGETS='marivo/datasource'`：**40 源码文件通过**；`make lint-agent LINT_TARGETS='marivo/datasource tests/...'`：格式/lint/import 全过。
- 远端逐组（服务经 manage.sh 分组启停，起始/结束 7 容器全部 Exited，轮末 colima 停机）：上表 8 项 opt-in 全过。
- 全量 `make check-agent`：**5400 passed / 19 skipped**、403 个源码文件 typing、API 文档构建通过（skip 从 64 → 19：45 丰富 metadata 装饰器/46 实例恢复 + 1 项 parametrize 展开差异，14 R5 正例 + 4 原有 skip + 1 记录性 skip 保持原归属；passed 相对上一记录增加来自恢复的断言与新通道测试）。
- `npm --prefix site run build`：0 errors / 0 warnings；`git diff --check` 通过。

未变化：DS11（MySQL/ClickHouse timeout）与 DS13（时区事实）保持阻塞；远端服务器端终止证明未取得；R5–R9 方法资格、wheel 与真实 Agent 未验证。本轮不改变 R0–R3 的阶段状态；R1 的 C01.a 丰富 metadata 与 C01.c 认证 HTTP 两个原阻塞格在本轮实测范围内**闭合**，C01.a 的远端丰富 metadata、C01.b 的远端终止矩阵仍按上表边界记录。本节为 R1.6 工作包记录，不整体提升 R1 阶段状态。
