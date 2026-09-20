# C5：表形态准入解除（全引擎、视图统一、Distributed 多分片）验收

日期：2026-09-20（实际验收日；文件名沿用计划 §6 预设的 2026-09-21 前缀）。状态：C5 已完成（逐后端表形态单元见 §2 矩阵；剩余拒绝项见 §6，均为用户口径明确的保留边界）。依据 [实施计划](2026-09-20-multisource-capability-c5-implementation-plan.md)、[总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)、[C0 清单](2026-09-16-multisource-capability-c0-implementation-plan.md) 与 [C4 验收](2026-09-20-multisource-capability-c4-acceptance.md)。

实施基线 `33c04ca69`（C4 后，计划落盘 HEAD），验收代码 `11ca608aa`（本验收日新增 PG 基线 `3c04afa69` 及其验收末轮 DDL 守护修正 `11ca608aa`）。提交链 `c110e67cb..c32945384` 共 11 个提交（末位 `c32945384` 是本文档自身的审查修正提交，因此本文档不计入该链）：计划 1、gate 解除 1、披露同步 1、既有负向翻转 1、环境 fixture 2（含审查修正）、新增验收测试 2、PostgreSQL 基线 2（含守护修正）、验收文档 2（验收记录 `6143b0a19` 及其审查修正 `c32945384`）。本记录仅覆盖 C5，不表示 C6–C9 或 C10 完成。

## 0. 范围修订记录（2026-09-20 用户口径）

C0 与总计划 §3.5 对 C5 的原始界定是"ClickHouse Distributed、Trino 无 catalog/connector 类型白名单、PostgreSQL 视图/分区补证据"。实施计划落盘当日，用户对表形态口径作出三项扩展决定（[计划 §0](2026-09-20-multisource-capability-c5-implementation-plan.md)），本阶段按扩展后口径执行并验收：

1. **ClickHouse 不限制 engine 类型**：全部 engine 进入；读不稳定（ReplacingMergeTree 等未收敛 merge 状态）被明确接受为已声明语义，不是缺陷。验收以 ReplacingMergeTree 未收敛双版本读取落实该口径（§4）。
2. **视图统一支持**：ClickHouse `View`/`MaterializedView`、Trino `VIEW`、MySQL 视图、SQLite 视图、PostgreSQL 视图全部按既有逐列类型/传输校验准入。
3. **MySQL 不限制 ENGINE 类型**：InnoDB 判定解除（MyISAM 实测正向旅程）；`utf8mb4_0900_bin` collation 门与只读账户要求不变。

口径一致的推论：gate 的角色从"能力白名单"改为"缺失关系与系统内表的拒绝 + engine/connector 观测回执"。逐列物理类型、传输、时区、float 有限值、方法闭包校验全部原样保留。C0/总计划 §3.5 与计划 §0 冲突处以 §0 用户口径为准（计划 §7 排除项同此声明）。

## 1. 交付范围与实施状态

计划 §1 五项交付全部落地：

1. **ClickHouse 全 engine + Distributed**：`clickhouse_execution.py::get_schema` 的 `kind != "MergeTree"` 白名单删除（`93b379cb9`）；engine 值保留为缺失关系判定（非字符串/空值 → 缺失关系诊断）与观测回执。真实 2 分片集群验收 7 项（`284f36996` + `697111282`）：全局聚合、跨分片重复身份拒绝、单分片故障结构化失败与恢复、FINAL/去重回执全文审计、ReplacingMergeTree 未收敛读取、跨分片关系命中/缺失、reader 账户矩阵。
2. **Trino 通用元数据路径**：`connector == "iceberg"` 判定删除，`connector_name` 保留观测；表形态判定改为仅拒绝 `$` 内表（`93b379cb9`）；`VIEW` 进入既有类型校验。视图旅程与 catalogs 关系翻转（`a33eba8ee`：`test_view_source_journey` + `test_catalog_relation_schema_admits_varchar_columns`）。非 Iceberg（memory connector，catalog `noniceberg`）真实旅程 4 项（`284f36996` + `697111282`，逐项见 §4 末）；计划 §1 声明的"验收样本不构成白名单"照搬成立。
3. **MySQL engine 解除 + 视图**：`ENGINE != "InnoDB"` 判定删除；同一查询改读 `ENGINE,TABLE_TYPE` 两列，行不存在给缺失关系诊断（视图 `ENGINE=NULL` 不再误判缺失）（`93b379cb9`）。MyISAM 与视图正向旅程落地（`a33eba8ee`）。
4. **SQLite 视图**：`sqlite_schema` 定义探针放宽为 `type IN ('table','view')`，视图走既有 `sge.Create` 解析与 `pragma_table_info` 类型路径；表达式列空声明类型走既有诚实拒绝（`93b379cb9` + `a33eba8ee`）。virtual 表保持拒绝。
5. **PostgreSQL 视图/分区基线核查**：PG `get_schema` 本无表形态 gate（`pg_attribute` + `to_regclass` 对视图/分区父表同样解析），本阶段只补真实旅程证据（`3c04afa69`，§3）；未发现缺口，无代码改动。

静态桩测试同批落地（`93b379cb9`，`tests/test_lazy_source_schema.py`）：四 adapter 新接受路径（CH 任意 engine 参数化、Trino 非 Iceberg connector + VIEW、MySQL `ENGINE=None/TABLE_TYPE='VIEW'`、SQLite `CREATE VIEW` 定义）与缺失关系新负向（CH engine 空值、MySQL 无行、Trino `$partitions`）各侧覆盖；RED/GREEN 变异证据见 Task 1 报告。

## 2. 六后端表形态矩阵（验收状态）

| 后端 | 视图 | 全 engine/connector | Distributed | 保持拒绝（既有，本轮确认不变） |
| --- | --- | --- | --- | --- |
| ClickHouse | ✅ 运行时验收（`test_view_source_journey`，1058.5） | ✅ 运行时验收（MergeTree 家族/Distributed/ReplacingMergeTree；静态桩 + 集群 7 项） | ✅ 真实 2 分片集群（§4） | 缺失关系（engine 空值）；`FINAL`/`final = 1`/`OPTIMIZE`/`DEDUPLICATE`/`any(` 零出现（回执全文审计） |
| Trino | ✅ 运行时验收（iceberg 视图 1058.5） | ✅ 运行时验收（非 Iceberg memory catalog 四项 + `system.metadata.catalogs` 关系按列校验） | —（非 ClickHouse 概念） | `$` 内表（`$partitions` 等，live 拒绝）；collation 无此概念 |
| MySQL | ✅ 运行时验收（`ENGINE=NULL/TABLE_TYPE='VIEW'`） | ✅ 运行时验收（MyISAM 正向旅程） | — | `utf8mb4_0900_bin` collation 门；只读账户；duplicate/null/unsigned |
| SQLite | ✅ 运行时验收（star 视图）；表达式列诚实拒绝（`actual_type=''`） | —（无 engine 概念） | — | virtual 表（`VirtualProperty`）；attached 库外关系 |
| PostgreSQL | ✅ 运行时验收（基线，§3） | —（本无 gate，`relkind` 无关准入） | — | 既有逐列类型/传输校验照旧 |
| DuckDB | —（无表形态 gate，未改动） | — | — | 未改动 |

`system.metadata.catalogs` 关系自身（全 varchar 列）经 live 探测后按列类型校验准入（`test_catalog_relation_schema_admits_varchar_columns`）；fixed qualify registry 的身份断言要求 `id` 列，该关系上的完整 Dataset 旅程不可构造，get_schema 翻转即该关系的诚实正向。Trino 拒绝类 denial（只读 DML 与整 catalog 拒绝）实测 surface 为 `PERMISSION_DENIED`（非 `ACCESS_DENIED`，Trino 483），helper 断言与之一致。

## 3. 独立预期值与证据形态（PostgreSQL 基线，本验收日执行）

全部期望值为手算常量，引擎仅作对照。PG 基线两旅程（`tests/test_lazy_postgres_forms_baseline.py`，opt-in `MARIVO_POSTGRES_ANALYSIS_TEST=1`，服务 PostgreSQL 17.11）：

- **probe 先行事实**：admin 连接实测 `pg_catalog`——基表 `relkind='r'`、普通视图 `'v'`、分区父表 `'p'`，三者经 adapter 同一查询（`pg_attribute` + `to_regclass`，`format_type` + `attnotnull`）返回逐列相同的 11 行（列名/类型串/可空性全同）；`pg_inherits` 直接子分区 2 个，标准行集按 `day` 范围切分 5 行（2 月分区）+ 1 行（3 月分区）。
- **旅程 1（视图）**：admin 建 UUID 命名 VIEW 于标准行集 `{10.25, 20.5, -2.0, 30.75, 999.0, NULL}` 之上，reader 走完整 Dataset 旅程（observe REVENUE → aggregate → execute），`sum = 10.25+20.50+30.75−2.00+999.00 = Decimal("1058.50")`（NULL 行不进 sum）；`primary_queries == 1`；`finally` 中 DROP VIEW + DROP TABLE。
- **旅程 2（分区）**：admin 建 `PARTITION BY RANGE (day)` 两分区父表（同一行集），reader 同一旅程断言同一手算常量 `Decimal("1058.50")`；`finally` 中 DROP 父表 + 两分区（先子后父）。
- 两旅程均验证残留为零（`pg_tables`/`pg_views` 复查 0 个 `dataset_%` 对象）。

其余新增证据形态：Distributed 集群与 Trino 非 Iceberg 常量见 §4/§5 与测试文件头注释；既有翻转旅程复用既有手算集（1058.5 全集、30.75 channel-b、`(30.75,'a'),(30.75,'b')` 排名对）。

## 4. Distributed 集群专项证据与故障/身份负向

环境：真实 2 分片 × 1 副本集群（profile `clickhouse-cluster`，shard A HTTP 18201 / shard B 18203，内部 native 9000 不发布宿主机；静态 `remote_servers` `marivo_multisource` 带集群 secret；pinned 镜像无 Keeper，`ON CLUSTER` 不可用 → fixture 按节点直连 DDL，边界记录于 README）。验收时 `system.clusters` 实测两行：`('marivo_multisource',1,1,'clickhouse-shard-a',0)`、`('marivo_multisource',2,1,'clickhouse-shard-b',0)`（`errors_count=0`）。

- **全局聚合 fanout 证明**：分片 A 本地 sum **1.5**（ids 0,1）+ 分片 B 本地 sum **9.75**（ids 2,3,4）= 全局 **11.25**；Distributed 表上 reader sum/count = 11.25/5，channel 排名 [5.5, 4.25, 1.5]，grouped 传输恰 3 行。
- **跨分片重复身份**：同一身份键 admin 写入两分片后 reader 旅程真实触发既有身份断言拒绝（`source_row_unique`），0 artifact、空资源——读不稳定不豁免身份契约。
- **单分片故障**：in-test 停止 shard-b 容器（finally 守护重启 + 就绪轮询），reader 旅程结构化失败（lifecycle `failed`、`failure.kind == "execution_failed"`、0 artifact、空资源），重启后重试成功发布。
- **回执审计**：clean + 失败两类旅程的全部提交 SQL 无 `FINAL`、`final = 1`、`OPTIMIZE`、` DEDUPLICATE`、` any(`（精确大小写 token + 小写 `\w*final\w*`/`optimize`/`deduplicate`/函数形 `any(` 扫描双保险）；Trino 非 Iceberg 审计同理（无 `$partitions`/`$files`/`$snapshots`/`$properties`，仅一条 adapter 允许的 `system.metadata.catalogs` 观测）。
- **ReplacingMergeTree 未收敛（用户口径的落实断言）**：同 key 双版本（ver 1 → 10.25，ver 2 → 30.75）经 adapter 读取层 sum = **Decimal("41.00")**（两物理版本之和，未收敛语义被接受），提交审计无 FINAL/OPTIMIZE/DEDUPLICATE。governed 旅程对重复身份按契约拒绝（§4 第二条），故该单元在 adapter 读取层验收——这是审查确认的形态决定，见 §9。
- **跨分片关系**：orders/lines/customers 分布式关系 join，channel 命中 a 12.0 / b 3.0 / c 30.0，miss 行落入左外 NULL 组 NA 20.0；region 轴 EU 45.0 + NA 20.0。命中与缺失两类场景均覆盖。
- **reader 账户**：两节点各 5 条写拒绝断言；reader `SELECT ON qualification.*` 与 `qualification_cluster.*`。
- **Trino 非 Iceberg 四项枚举**（`tests/test_lazy_trino_non_iceberg_runtime.py`）：`test_full_journey`（五类型全旅程，sum **1058.5**、count 5、min −2.0、max 999.0、排名对 (a,30.75)/(b,30.75)、日桶轴 02-02..02-06 其中 02-05 为 NULL 组、scope 排除 03-01 行、ratio 59.5/4、加权均值 1173.25/8 = **146.65625**、population ids 1..6）；`test_decimal_amount_journey_exact`（`Decimal("1058.50")` 值精确）；`test_receipt_audit_free_of_iceberg_metadata`（无 `$partitions`/`$files`/`$snapshots`/`$properties`，仅一条 adapter 允许的 `system.metadata.catalogs` 观测，无其他语句引用 `iceberg` catalog）；`test_partitions_internal_table_rejected`（live `orders$partitions` → `MaterializationError`）。

**契约注记（记录，不修）**：持久化 RunFailure 有意剥离 driver 消息——`RunFailure.__post_init__` 在 `execution_failed` 运行上拒绝 `backend_class`，`store.fail` 只持久化规范化的有界载荷。故障测试因此钉住**实际被保留的指纹链**：`run.failure.phase == "authority_resolution"`、通用 `safe_message`（"The Dataset action failed before publication."）、失败提交回执（`role == "validation_batch"`、`state == "failed"`）携带原始 driver 异常类 `error_type == "DatabaseError"` 且其 SQL 指名停止分片上的 `orders_<suffix>_distributed` 关系。raised 异常链层面（mid-stream transport cut 还带 `__cause__` 包装）契约成立；"持久化载荷含 driver 消息指纹"若成为验收标准，属 C5 之外的 store 契约变更。

## 5. 验证命令与结果

以下全部在本验收日复现：既有回归与集群/Trino 套件在 `3c04afa69` 内容态实跑，PG 基线两旅程在守护修正后的 `11ca608aa` 内容态复跑通过（2 passed、零残留）；验收文档提交为纯文档，不计入代码态。数据库组串行，先 `manage.sh status` 后按 profile 互斥启停。

- `make check-agent`：exit 0，**5594 passed / 16 skipped**（含 API 文档检查；C4 验收时为 5586 passed / 16 skipped，增量含 C5 新增默认门禁静态桩与既有用例拆分）。
- 计划 §5 静态块：**169 passed**。其中一处命令级偏差（修正记录，不改历史计划文本）：计划写的 `tests/test_lazy_mysql_transport.py` 从未存在（git 全历史无此文件），MySQL 传输覆盖的既有 owner 是 `tests/test_lazy_mysql_methods.py`，静态块以该文件替换后运行；该文件另在 MySQL runtime 块中实跑。
- 既有翻转旅程回归（fresh）：ClickHouse methods+runtime **72 passed**；MySQL methods+runtime **54 passed**；SQLite methods+runtime **45 passed**；PostgreSQL methods+runtime+forms baseline **47 passed**（含基线 2 项）。
- 新增集群/非 Iceberg（fresh）：`MARIVO_CLICKHOUSE_CLUSTER_TEST=1` 集群 7 项 **7 passed**（~14 s）；`MARIVO_TRINO_NON_ICEBERG_TEST=1`（trino 启动后重跑 `setup_non_iceberg()` 再跑）**4 passed**；Trino methods+runtime **67 passed**。
- `make typecheck TYPECHECK_TARGETS='marivo/analysis'`：213 files 通过；`make lint-agent LINT_TARGETS='marivo/analysis tests/test_lazy_clickhouse_distributed_runtime.py tests/test_lazy_trino_non_iceberg_runtime.py tests/test_lazy_postgres_forms_baseline.py'`：格式/lint/import contracts 全部通过；`git diff --check` 通过（覆盖本验收日四个提交：`3c04afa69`、`11ca608aa`、`6143b0a19`、`c32945384`）。
- 未运行 release-check、未启动 MinIO；opt-in 环境变量未设置时各 runtime 文件按 skipif 干净跳过（不以 skip 冒充通过）。

## 6. 剩余拒绝与排除项（用户口径确认不变）

- MySQL `utf8mb4_0900_bin` collation 门（`test_invalid_source` collation 参数保持拒绝）；engine 解除不豁免 collation。
- SQLite virtual 表（`VirtualProperty`）与 attached 库外关系保持拒绝。
- Trino `$` 内表保持拒绝（live 拒绝证据 + 诊断更新后可区分）。
- 无 `FINAL`/`final=1`/任何业务级去重默认（两文件回执全文审计断言）。
- 跨源联邦、远程 retained 上传、任意 SQL 入口保持既有边界。
- Trino 非 Iceberg（memory catalog）样本是验收样本，**不构成 connector 白名单**。
- 读不稳定是已接受语义而非新支持单元，不列为排除对象（计划 §7）。
- 披露 effects 句 "views and every engine or connector type enter" 不含 SQLite 的 virtual/attached 表排除：该 carve-out 由本节上文的 SQLite virtual 表拒绝条目持有，不应把该句读作"一切表形态无边界准入"。
- ClickHouse 与 Trino 的缺失关系在各自提交探针处 surface 为通用 `read_scalar` 非标量错误（"one row and one scalar" 诊断），不是专门的缺失关系诊断：新的 engine 空值分支仅在关系实际存在且探针返回 NULL engine 值时触发（近不可达路径）。此为既有形态，记录为已知边界，本轮不改。
- "Observation receipt"（engine/connector 观测回执）指探针语句出现在提交回执 trail 中，不表示回执值本身被持久化。
- C6–C10 未启动；本记录不表示其完成。

## 7. 披露对齐

`marivo/analysis/datasets/_disclosure.py` effects（`2b0bf595c`）：删除 "Trino is qualified for Iceberg tables and ClickHouse for ordinary local MergeTree tables"，改为 "Source relations are not restricted by table form: views and every engine or connector type enter, and Trino rejects only `$`-suffixed internal tables"；读不稳定并入相邻既有源状态句（"…for example ReplacingMergeTree) are accepted, results can change with merge progress between runs, and replay of a committed snapshot is unaffected"）。渲染后 actions.execute help 3170 字节（预算 9000）。同提交内 `docs/specs/analysis/python-analysis-design.md` 四处（执行边界、MySQL 段、Trino 段改题 "Trino scalar Metrics"、ClickHouse 段改题 "ClickHouse scalar Metrics" + 任意 engine + 不稳定读取句）；审查修正补齐 `docs/specs/analysis/session-state-and-runtime.md` 同类四处（ordinary Trino relation scans、改题、任意 engine、排除句改写）。漂移测试 `test_execute_help_discloses_qualified_source_boundaries` 事实清单同步（"not restricted by table form"、"$-suffixed internal tables"、"ReplacingMergeTree"、"replay of a committed snapshot is unaffected"）。双语 site latest（EN + zh-cn 的 analysis-workflow 与 semantic-layer 四文件）grep 命中被改措辞并同步改写，EN/ZH parity 经审查核对。packaged skills 只读排查零失配，无需批准项。

## 8. 审查

本轮按 subagent-driven 流程执行：六个实施任务各经独立规格/质量审查（Task 1、Task 2 各两轮；Task 4 审查修正提交 `e832a99a6`；Task 5 批准后审查修复 `697111282`），最终整体验收由 Task 6 汇总。审查实际拦下的问题包括：**noniceberg reader 零访问**（Trino catalog 规则全串匹配，原 `iceberg|system` 规则静默落入 deny-all 兜底——写拒绝矩阵探不到读缺失，live 重探修复并补 reader 读断言）；**持久化失败载荷的 driver 消息剥离**被实证并转为契约注记 + 指纹链断言（原测试只断言 Run 结构）；任务简报常量算术错误两处（分片 B 3.75 → 9.75、02-05 桶 NA）被实施方以手算证伪后修正；`session-state-and-runtime.md` 的过期 MergeTree 措辞（初版任务范围外）由审查补入同批披露修正；SQLite 视图桩最初键在宽片段上非真红，审查要求重键后才放行 RED/GREEN 证据。验收 loop 末轮审查另拦下：PG 基线测试的 `try` 守护未覆盖 DDL（中间态失败会泄漏 fixture 对象，Task 6 报告曾误称已修），已修正提交 `11ca608aa` 并复跑通过。

## 9. 环境与回执

**端口与拓扑**：单节点 ClickHouse 18123（HTTP）；集群 shard A/B 18201/18203（内部 native 9000 不发布）；Trino 18080；postgres-analysis 15432（server 17.11）；mysql-analysis 23306（8.4.11）；postgres catalog 服务随 trino profile 启停。互斥对称：`manage.sh start` 任一 trino/clickhouse/clickhouse-cluster 组会停止另外两组（6 GiB Colima 约束下的资源防护）；`stop clickhouse-cluster` 同时停单节点。

**启动方式**：`bash tests/multisource_environment/manage.sh start clickhouse-cluster`（健康门 + 两端点 `system.clusters` 可查）；`start trino` 后 **memory catalog 不耐重启，必须每次重跑 `trino_analysis.setup_non_iceberg()`** 重建 schema/样本表（reader 读断言内建）；`start postgres-analysis` / `start mysql-analysis` 各自运行 setup（reader 权限 + 拒绝矩阵 + 探针清理）。集群 fixture：持久层（`setup_cluster`：reader、`qualification_cluster`、两节点 `orders_local` + `orders_distributed`）与一次性层（`create_cluster_tables(suffix, split_at=N)` / `drop_cluster_tables`）分离；本验收日复验 `setup_cluster()` 回执（2 shards、双节点 26.3.33.24）且两节点 `qualification_cluster` 无残留非 fixture 表。

**服务变更（验收全程）**：postgres-analysis 启动 → PG 基线 → 集群块（启动 → 7 项 → `system.clusters` 探测 → 停止）→ 单节点 clickhouse 恢复 → trino 启动 + 重播 noniceberg → Trino 块 → trino 停止 → postgres-analysis 停止 → 单节点 clickhouse 最终确认。**验收结束时状态**：单节点 clickhouse up（healthy）；shard-a/shard-b/trino/postgres/postgres-analysis down；mysql-analysis up（as-found，全程未动）。

**回执 trail**：沿用 C4 先例，本轮未生成新 `-execution-receipts.json`，以测试提交哈希为回执 trail（`93b379cb9`、`2b0bf595c`、`a33eba8ee`、`76605d2a7`、`e832a99a6`、`284f36996`、`697111282`、`3c04afa69`、`11ca608aa`）；`capture_submissions` 回执审计内嵌于集群/Trino 验收测试并随提交入库；未覆盖 `multisource-slice-N` 记录；`MARIVO_SLICE4_RECEIPTS` 机制未动。过程证据：`.superpowers/sdd/c5/task-{1..6}-report.md`。
