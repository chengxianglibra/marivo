# 全量分析代数与 Analysis DSL 重构阶段验收主记录

Date: 2026-09-26

Status: R0.1–R0.5 静态产物已登记，R0.6 未开展；R1.1/R1.2 部分实施，R1.3 已提交，R1.4 披露候选已核验；R2.1 静态定义候选、R2.2 Metric 图静态候选已核验。R0、R1 和 R2 整体均未验收。

本文件按[主计划](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)和[R0 实施文档](2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md)续记实际证据。历史验收不自动转成新 DSL 的技术、后端、安装包或真实 Agent 资格。

| R0 出口 | 状态 | 当前证据或缺口 |
| --- | --- | --- |
| R0.1 工作基线、输入 hash、历史证据边界 | **通过（索引与归档）** | [R0.1 索引](2026-09-26-marivo-full-refactor-r0-evidence-index.md)与 [manifest](evidence/r01/manifest.json)；S0/组合正文及 P4 小型机器结果已复制。S4 原轨迹与 wheel 为本机专有历史附件，未授予新资格 |
| R0.2 C01–C18 与消费者反查 | **通过（静态反查）** | [能力台账 §1–§4](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#1-本次可复核快照与读法)列出 C01–C18、44 个现有子单元、导出/Help、双执行链、主要消费者与去向；目标运行资格仍未验证 |
| R0.3 必需 owning spec 决定 | **通过（目标契约文档；C01.c 已按用户决定修订）** | [Analysis R0.3](../../specs/analysis/python-analysis-design.md#r03-accepted-full-algebra-target-inactive)、[Semantic R0.3](../../specs/semantic/semantic-object-model.md#r03-full-algebra-target-decisions-inactive)、[Datasource SQL 目标](../../specs/semantic/datasource-layer.md#r03-target-ibis-owned-analysis-reads-and-terminal-raw-sql)及[能力台账 §5](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#5-r03-接受的契约索引)：C18、权重/参照、普通 ratio、业务顺序、原状态/当前行及 SQL/provenance 已给 typed 目标、Cell/部件/错误/K/反例。`md.raw_sql`/`RawSqlResult`/Help 保留为终端只读逃生通道，不能重入 Analysis；其他新 DSL 目标尚未实现 |
| R0.4 规则、方法、模块责任 | **通过（规则和迁移台账）** | [能力台账 §6](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#6-r04-六类元算子规则冻结)：六类元算子含 Pre、RequiredParts、PartTransform、Post、Transport、Eval；44 个子单元及展开方法记录版本、K、独立 oracle、阶段；§6.2 登记旧实现/消费者同迁删除点。新规则尚无产品执行证据 |
| R0.5 SQL/adapter/六后端目标资格 | **静态台账通过；替代可行性阻塞、运行未验证** | [SQL/adapter 台账](2026-09-26-marivo-full-refactor-r0-sql-ledger.md)列 DS01–DS21、AN01–AN33 的构造/调用/提交与 Ibis/驱动替代，Store SQLite 事务白名单、七项 adapter 责任及六后端目标矩阵。DS02 是具名公共终端 SQL 路线；其他内部 SQL 例外为空。DS11/DS15 等控制/认证操作尚无已证等价驱动 API；六后端、表形态、数值/时间和资源格仍未实跑 |
| R0.6 破坏性变更与 R1/R2 交接 | **未验证** | 目标入口、消费者及验收索引尚未收束 |

R0 整体不得标为通过。R0.1 没有改产品代码，也没有重跑旧 Runtime、安装包或 Agent；可移植原始 Agent 轨迹与 wheel 缺口在索引 §6 记录为历史待补证。R0.2 在 panda 的代码 HEAD 为 `d5e06022c7fcd355b2a31ab6935aea593b60f0d1`，只执行静态扫描并编写文档，未运行产品测试或远端后端。R0.3–R0.5 按用户指定直接在干净的 `panda` 起点实施，未创建隔离工作树，未改产品代码、packaged skills 或 `AGENTS.md`。

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
