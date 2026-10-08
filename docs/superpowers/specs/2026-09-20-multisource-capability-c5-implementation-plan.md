# C5：表形态准入解除（全引擎、视图统一、Distributed 多分片）实施计划

日期：2026-09-20。状态：计划已落盘，未开始实施。依据 [总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md) §3.5、[C0 清单](2026-09-16-multisource-capability-c0-implementation-plan.md)、[C0 验收](2026-09-16-multisource-capability-c0-acceptance.md)、[C1 验收](2026-09-16-multisource-capability-c1-acceptance.md)、[C2 验收](2026-09-16-multisource-capability-c2-acceptance.md)、[C3a 验收](2026-09-18-multisource-capability-c3a-acceptance.md)、[C3b 验收](2026-09-19-multisource-capability-c3b-acceptance.md) 与 [C4 验收](2026-09-20-multisource-capability-c4-acceptance.md)。

计划落盘时 HEAD 为 `33c04ca69f87fd78ba62cb6fae1dd2a45e43ef0d`（分支 `lazy-dataset`，工作区干净），C4 已验收提交 `04391c784`。本文件先落盘，再实现。

## 0. 范围修订记录（2026-09-20 用户口径，覆盖 C0 原始 C5 行中相冲突的表述）

C0 与总计划 §3.5 对 C5 的原始界定是"ClickHouse Distributed、Trino 无 catalog/connector 类型白名单、PostgreSQL 视图/分区补证据"。实施计划落盘后，用户对表形态口径作出三项扩展决定，本计划按扩展后口径执行：

1. **ClickHouse 不限制 engine 类型**：MergeTree 家族（含 ReplicatedMergeTree）、Distributed、Log/Memory 等全部进入；**读不稳定（ReplacingMergeTree、SummingMergeTree、AggregatingMergeTree、Collapsing 家族等未收敛 merge 状态）被明确接受**——不加 FINAL/去重的既有铁律不变，结果可能随 merge 进度漂移属于已声明语义，不是缺陷。
2. **视图统一支持**：ClickHouse `View`/`MaterializedView`、Trino `VIEW`、MySQL 视图、SQLite 视图、PostgreSQL 视图全部按既有逐列类型/传输校验准入；PostgreSQL 本就无表形态 gate，只补证据。
3. **MySQL 不限制 ENGINE 类型**：InnoDB 判定解除（MyISAM 等 engine 全部进入）；`utf8mb4_0900_bin` collation 门与只读账户要求不变（用户只解除 engine 限制）。

口径一致的推论：gate 的角色从"能力白名单"改为"缺失关系与系统内表的拒绝 + engine/connector 观测回执"。逐列物理类型、传输、时区、float 有限值、方法闭包校验全部原样保留——它们才是实际语义边界；engine 字符串不再是语义边界。读不稳定类的风险由两点既有契约兜住：身份断言（同一源内身份坐标重复仍拒绝，`decode_envelope` 的 duplicated ordinal 路径）拦住身份层面的重复；执行回放返回快照（execution-key 契约）不受源漂移影响。两次冷运行同一查询可能因 merge 进度推进返回不同数值——这是接受的语义，写入披露。

## 1. 前置与范围

进入条件为 C0 与所需类型（C1–C4 均已交付）。交付：

1. **ClickHouse 全 engine + Distributed**：`clickhouse_execution.py::get_schema` 的 `kind != "MergeTree"` 白名单解除——engine 读取保留为缺失关系判定与观测回执，任何 engine（含 `Distributed`、`ReplicatedMergeTree`、`ReplacingMergeTree`、`View`、`MaterializedView`）不再因表形态被拒。在真实多分片环境完成 Distributed 全局聚合、跨分片身份、关系 fanout、分片/副本语义、传输与故障验收。不得默认加 `FINAL`、`SETTINGS final=1` 或任何业务级去重（回执 SQL 全文审计断言）。
2. **Trino 通用元数据路径**：`connector == "iceberg"` 判定删除（`connector_name` 保留读取为观测回执）；`table_kind` 判定改为只拒绝 `$` 内表（`$partitions`/`$files`/`$snapshots`/`$properties` 等，判定保持既有 `"$" in name` 语义——这些内表是基表名加 `$` 后缀，非 `$` 结尾）；`VIEW` 进入既有类型校验。非 Iceberg catalog 的真实旅程作为验收样本，不构成白名单。
3. **MySQL engine 解除 + 视图**：`ENGINE != "InnoDB"` 判定删除；同一查询增加 `TABLE_TYPE` 读取，行不存在时给缺失关系诊断（视图 `ENGINE=NULL`，缺失判定不能只依赖 engine 列），engine/type 观测回执；视图进入既有逐列校验。collation 门不变。
4. **SQLite 视图**：`sqlite_schema WHERE type='table'` 放宽为 `type IN ('table','view')`；视图的 `CREATE VIEW` 语句解析为 `sge.Create`（既有解析路径不变），视图列类型由 `pragma_table_info` 提供底层声明类型，表达式列类型为空时走既有 `declared_scalar_type` 失败路径（诚实拒绝，不造类型）。virtual 表（`VirtualProperty` 检查）保持拒绝。
5. **PostgreSQL 视图/分区表基线核查**：PG `get_schema` 无表形态 gate（`pg_attribute` + `to_regclass` 对视图同样解析），本阶段只补真实旅程证据；若发现缺口记录为剩余差距，不在 C5 扩大实施范围。

不新增公共 API、不扩展 authoring 参数。DuckDB 无表形态 gate，不改动。跨源联邦、远程 retained 上传、`FINAL`/去重默认、MySQL 非 `utf8mb4_0900_bin` collation、SQLite virtual/attached 表、时间轴/方法矩阵扩展保持既有边界；C1–C4 已交付单元在新增表形态上按既有准入规则逐类型验收，不自动全量授权。不修改 packaged skills（如需另获批准）、AGENTS.md、依赖或 Store 格式。

## 2. 已核实的现状（逐符号只读核实，HEAD `33c04ca69`）

### 2.1 四条 gate 与观测点

| Gate | 位置 | 现状 | C5 动作 |
| --- | --- | --- | --- |
| `if kind != "MergeTree": raise self.unsupported(f"ClickHouse engine {kind!r}; qualified methods require ordinary MergeTree")` | `marivo/analysis/materialization/clickhouse_execution.py:176-179`（查询 `system.tables.engine`） | 非 MergeTree 一律拒绝 | 删除白名单；`kind` 非 `str` 时给缺失关系诊断，否则仅观测 |
| `if connector != "iceberg": raise self.unsupported(f"Trino connector {connector!r}; Group A requires Iceberg")` | `marivo/analysis/materialization/trino_execution.py:165-166`（查询 `system.metadata.catalogs`） | 非 Iceberg connector 一律拒绝 | 删除判定；`connector_name` 读取保留为观测 |
| `if table_kind != "BASE TABLE" or "$" in name: raise self.unsupported("Trino Group A requires an ordinary Iceberg base table")` | `marivo/analysis/materialization/trino_execution.py:176-177` | `VIEW` 与含 `$` 名称一律拒绝 | 改为仅 `"$" in name` 拒绝；`VIEW` 等进入类型校验；诊断更新 |
| `if engine != "InnoDB": raise self.unsupported(f"MySQL table engine {engine!r}; scalar execution requires InnoDB")` | `marivo/analysis/materialization/mysql_execution.py:179-181`（查询 `information_schema.tables.ENGINE`） | 非 InnoDB 与视图（ENGINE NULL）一律拒绝 | 删除白名单；同查询加 `TABLE_TYPE`；行缺失给缺失关系诊断；观测 |

SQLite 修改点：`marivo/analysis/materialization/sqlite_execution.py` `get_schema` 内 `"SELECT sql FROM main.sqlite_schema WHERE type='table' AND name=?"` 放宽为 `type IN ('table','view')`，其余解析与 `pragma_table_info` 路径不变。PostgreSQL `postgres_execution.py` 无 gate，不动。

三处 gate 的邻接上下文（ClickHouse：engine 检查前 `join_use_nulls` 读取、检查后 `DESCRIBE TABLE` + 逐列 regex + LowCardinality/Nullable 解包 + timestamp 时区校验 + float 非有限值检查；Trino：`SHOW COLUMNS` + `information_schema.tables`；MySQL：`information_schema.columns` + 逐列 collation/类型校验）保持不变；C5 只改表形态判定。模块 docstring 与 repair 文案同步：`clickhouse_execution.py:1`（"Read-only MergeTree execution"）、`mysql_execution.py:1`（"read-only InnoDB"）、`clickhouse_execution.py:329` repair（"declared MergeTree sources"）、`mysql_execution.py:335` repair（"declared InnoDB sources"）。

### 2.2 准入与披露 owner

- 后端方法准入：`marivo/analysis/operators/{trino,clickhouse}_support.py::unsupported_reason` 与各 `*_execution.py::admit_dataset` 只看方法/类型闭包，不含表形态事实；表形态完全由 adapter `get_schema` 把关（本次改动点）。C1 契约下依赖列级事实已统一（`EntitySourceDependency`），表形态解除不新增依赖类型。
- 披露 owner：`marivo/analysis/datasets/_disclosure.py:276` effects 文本含 "Trino is qualified for Iceberg tables and ClickHouse for ordinary local MergeTree tables"；`docs/specs/analysis/python-analysis-design.md` :37（执行边界 "Trino qualification is limited to Iceberg and ClickHouse to ordinary local MergeTree tables"）、:185（"MySQL requires InnoDB and a SELECT-only account"）、:236（"a catalog merely named `iceberg` is not sufficient" 段）、:401-404（"### ClickHouse MergeTree scalar Metrics" 段）。漂移测试 `tests/test_lazy_disclosure.py:871-884`（`test_execute_help_discloses_qualified_source_boundaries`）断言 "Iceberg tables"、"ordinary local MergeTree tables" 两事实。全部同批更新；effects 替换措辞在实施期定稿并保持 ≤9000 字节预算（同时把"读不稳定已接受、重放返回快照"并入既有 "Source queries may observe different source states" 邻句，不新增独立句子以控预算）。
- 无其他库级固定：`marivo/_help/`、`marivo/skills/`、`marivo/analysis/operators/*.py` 中 grep `MergeTree|iceberg|InnoDB` 仅命中上述位置；`marivo/datasource/engines/trino.py` 的 `iceberg` 命中全部是 `$partitions` 分区探测分支（`_partitions_table_is_iceberg`），属通用元数据能力，不是准入白名单，不修改。
- `marivo/datasource/engines/clickhouse.py` 元数据层已具备 Distributed 感知（`_CH_DISTRIBUTED_ENGINE_RE` :63、`_dereference_clickhouse_distributed` :214、physical profile `scope=local_node_only`），是 Distributed 分辨 owner 的既有参考实现。

### 2.3 环境

- `tests/multisource_environment/compose.yaml`：`clickhouse` 服务单节点（`clickhouse.xml` 无集群配置），Trino 仅挂 `iceberg.properties`（JDBC catalog，backend=PostgreSQL）。`manage.sh start|stop|status|logs {trino|clickhouse|postgres-analysis|mysql-analysis}`，trino/clickhouse 两 profile 互斥启停。
- **真实多分片环境不存在**：C0 已声明"真实多分片环境和代表性非 Iceberg catalog 尚待准备，不能据此宣称 C5 已完成"。本阶段须在实施期新增：ClickHouse 集群 fixture（`docker-compose` 追加多节点 ClickHouse 或独立 compose 文件 + `cluster` 发现配置）与一个非 Iceberg Trino catalog fixture（候选：同一 Trino 实例追加 `tpch`/`memory`/`postgresql` connector catalog properties；选型与样本表在实施期固定并写入验收记录）。环境缺失或服务不可用时记录阻塞并保持目标未完成，不将目标移出第一批，不以模拟替代（总计划 §3.5）。
- Trino 服务当前停止（C3a 记录）；实施期按 `manage.sh status` 确认后启动，不进入默认 gate。

### 2.4 ClickHouse 的技术事实

- Distributed 表查询自动 fanout 到各分片本地表；`MERGE` 结果不保证去重，跨分片重复身份（同一 Entity 键出现在多分片）必须由既有身份断言真实拒绝或按既有计数语义精确计数，禁止在 adapter 或 SQL 层加 FINAL/dedup 缓解。
- 读不稳定家族（ReplacingMergeTree 未 merge 时同 key 多版本共存、SummingMergeTree 部分和可见、AggregatingMergeTree 聚合状态、Collapsing 家族未折叠 ±1 行）：**用户已明确接受读不稳定**。验收写法：ReplacingMergeTree 同 key 双版本插入后，`sum` 手算期望值包含两版本（即未收敛语义），回执断言无 `FINAL`；不收敛漂移本身不是拒绝项。同一源内身份坐标重复仍由既有身份断言拒绝（`tests/lazy_multisource_qualification.py::assertion_envelope`/`decode_envelope` 的 duplicated ordinal 路径），读不稳定不豁免身份契约。
- `any` 聚合不在既有准入矩阵；当前 Group A 聚合 `sum/count/min/max` 均为全局精确可交换，跨分片语义与单节点一致，不需分支。但 `count` 经 UInt64 widen（`_lower` 的 `Decimal(76,0)` cast）与 identity envelope 固定 wire type（`clickhouse_envelope_sql`）在多分片下须重新验证（envelope 每分片返回一行 assertion 记录，重复 ordinal 判定已内建）。
- 关系 fanout：同源关系 join 在 Distributed 表上展开为分片间数据交换；身份/缺失坐标/fanout 断言沿用 C1 契约，验收必须包含跨分片关系键命中与缺失两类场景。
- 故障注入：单分片不可达（停止一个分片容器或按所选集群 fixture 等价手段）时，Dataset 执行必须结构化失败并保留原始异常，不静默降级为本地分片结果。

### 2.5 Trino 与 MySQL 的技术事实

- `SHOW COLUMNS FROM`、`information_schema.tables`、`system.metadata.catalogs` 均 connector 无关；通用路径天然成立。当前专属点是三条判定，不是元数据查询本身。
- `$` 内表（`base$partitions` 等）保持拒绝（`"$" in name` 既有语义不变）；`VIEW`、普通 `BASE TABLE`、system catalog 普通关系按实际暴露类型校验后进入。
- MySQL 视图在 `information_schema.tables` 中 `ENGINE=NULL`、`TABLE_TYPE='VIEW'`；`information_schema.columns` 对视图返回视图列类型，既有逐列校验直接适用。缺失表两种诊断要可区分：行不存在（缺失关系）与观测到的 engine/type（回执）。

## 3. 实现顺序及拥有者

1. **ClickHouse adapter 表形态判定解除**。`clickhouse_execution.py::get_schema`：engine 查询保留；`kind` 非非空 `str` → `unsupported` 缺失关系诊断（expected/received/repair 沿用 `MaterializationError` 模板，repair 文本更新）；否则不拒绝，engine 值随既有 `source_schema` statement 记录自然成为回执。模块 docstring、`admit_dataset` repair 文案同步。DESCRIBE/类型 regex/LowCardinality/Nullable/timestamp 时区/float 有限值逻辑不动（Distributed 表 `DESCRIBE TABLE` 返回本地表结构，类型校验路径不变；视图列由 DESCRIBE 解析）。
2. **Trino adapter 通用路径**。`trino_execution.py::get_schema`：删除 connector 判定分支（`connector_name` 查询保留为观测）；`table_kind` 判定改为 `if "$" in name: raise self.unsupported(...)`，诊断与 repair 文本更新为非表形态限定措辞（英文，说明 `$` 内表被拒、视图与普通关系按列类型校验）。模块 docstring 同步。
3. **MySQL adapter engine 解除**。`mysql_execution.py::get_schema`：查询改为 `SELECT ENGINE,TABLE_TYPE FROM information_schema.tables WHERE ...`；无行 → 缺失关系诊断；有行 → 不拒绝，两值随既有记录成为回执。模块 docstring、repair 文案同步。collation/逐列校验不动。
4. **SQLite 视图放宽**。`sqlite_execution.py::get_schema`：`type='table'` → `type IN ('table','view')`；诊断文本 "missing ordinary SQLite table definition" 措辞核对（视图命中后不应再报该文案）。`VirtualProperty` 拒绝、collation 扫描、`pragma_table_info` 路径不动。
5. **准入披露同步**。`_disclosure.py:276` effects 文本更新（删 "Trino is qualified for Iceberg tables and ClickHouse for ordinary local MergeTree tables"，改为表形态不限 + 读不稳定已接受 + 重放返回快照的措辞，仍在 9000 字节预算内）；`docs/specs/analysis/python-analysis-design.md` 四处段落同步（:37 执行边界、:185 MySQL 段、:236 Trino 段、:401-404 ClickHouse 段改题并删 MergeTree 限定）；`tests/test_lazy_disclosure.py:871-884` 断言事实更新。live Help 预算测试与漂移测试同批跑。实施期 re-grep `site/src/content/docs/*/latest/` 双语文档是否含被改措辞，命中则同步双语更新。
6. **环境 fixture**。新增 ClickHouse 多分片集群 compose（决策点：追加到 `compose.yaml` 新 profile `clickhouse-cluster`，或独立 `tests/multisource_environment/cluster/` 目录独立 compose——实施期按 manage.sh 兼容性选择并记录；集群节点 2 分片 × 1 副本最小拓扑，`remote_servers` 静态配置，与 `clickhouse.xml` 同风格）；新增 Trino 非 Iceberg catalog properties（挂载到 `trino/catalog/`）。管理员 helper 扩展（集群建库建表 + Distributed 表 DDL；Trino 样本 catalog 建表）与 reader 权限矩阵对齐既有 `*_analysis.py` 模式。fixture 管理员准备和 reader 执行分离（`marivo-test-fixtures` skill 约束；新增测试前读取该 skill）。
7. **测试**。新增 `tests/test_lazy_clickhouse_distributed_runtime.py`（真实集群，opt-in env 新增 `MARIVO_CLICKHOUSE_CLUSTER_TEST=1`）、`tests/test_lazy_trino_non_iceberg_runtime.py`（真实非 Iceberg catalog，opt-in `MARIVO_TRINO_NON_ICEBERG_TEST=1`）、`tests/test_lazy_postgres_forms_baseline.py`（PG 视图/分区证据，opt-in `MARIVO_POSTGRES_ANALYSIS_TEST=1`）、`tests/test_lazy_source_schema.py` 扩展（adapter 假连接桩：engine/table_kind/`type='view'` 各新接受路径 + 缺失关系诊断的新负向）。既有负向翻转（同批修改，语义按 §1 新口径）：
   - `tests/test_lazy_clickhouse_runtime.py:391` `test_view_and_effective_join_nulls`：视图用例从 `pytest.raises(MaterializationError)` 翻转为视图完整 Dataset 旅程成功（join_use_nulls 半段不动，测试改名拆分）。
   - `tests/test_lazy_trino_runtime.py:549` `test_unqualified_physical_sources_fail`：`view` 参数翻转为视图旅程成功；`connector` 参数（`system.metadata.catalogs` 的 `get_schema`）翻转为按列类型校验通过或删除该用例——实施期按 `SHOW COLUMNS` 对 system 表的真实输出定型并记录。
   - `tests/test_lazy_mysql_runtime.py:145` `test_invalid_source` 的 `"engine"` 参数（MyISAM）：删除或翻转为 MyISAM 正向旅程；另补 MySQL 视图正向用例。
   - `tests/test_lazy_sqlite_runtime.py`：补 SQLite 视图正向用例（含表达式列诚实拒绝负向）。
   - `tests/test_lazy_multisource_qualification.py` 与其余既有 MergeTree/Iceberg 旅程不改语义。
8. **PostgreSQL 视图/分区基线核查**（只补证据）：在现有 `postgres-analysis` 服务上以管理员建普通视图与分区表各一（UUID 专用，测试后清理），reader 走完整 Dataset 旅程；记录实际结果到验收文档。不改 PG 代码；发现缺口只记录。

## 4. Fixture、独立预期值与负向邻接项

新增 fixture（复用 `tests/multisource_environment/` 既有模式与 `tests/lazy_scalar_source_fixtures.py::registry_for`、`tests/lazy_multisource_qualification.py` envelope）：

- ClickHouse 集群：2 分片，每分片 1 本地 MergeTree `orders`（跨分片行集手算定值：分片 A 含 `amount ∈ {10.25, 20.5, -2.0}`、分片 B 含 `{30.75, 999.0}` 等，全集与现有单节点 fixture 行集一致以便对照）；Distributed 表 `dataset_<uuid>` 指向集群；同结构关系表 `lines`。独立预期值：`sum(amount)`、`count`、按 `channel` 分组的 rank/limit、关系 join 后的 ratio——全部以手算常量断言，不引用执行结果反推。
- ClickHouse 读不稳定样本（单节点即可）：`ReplacingMergeTree` 表同 key 插入两版本行（`ver` 列区分），`sum(amount)` 手算期望值 = 两版本之和（未收敛语义被接受的具体断言）；回执 SQL 全文无 `FINAL`/`final = 1`。
- Trino 非 Iceberg 样本：样本表含 `varchar/bigint/double/date/decimal(p,s)` 各一列（`tpch` 内置 `orders` 无 decimal，须建 `memory` 表或用 `postgresql` connector——实施期定型）；独立预期值同模式手算。
- MySQL 视图 + SQLite 视图 + PG 视图/分区：视图 `CREATE VIEW ... AS SELECT` 既有 fixture 行集；PG 分区表 `PARTITION BY RANGE (day)` 两分区；预期值复用既有手算集。
- PostgreSQL 视图/分区表：管理员建 UUID 专用对象，测试后清理。

负向邻接项（与正向同批验收）：

- ClickHouse 引擎观测照常进行：引用不存在的表 → 缺失关系诊断（非 "engine None" 旧文案）。
- Trino `$` 内表（`$partitions`/`$files`/`$snapshots`/`$properties`）→ 保持拒绝，诊断更新后可区分。
- Distributed 表跨分片重复身份键 → 既有身份断言拒绝路径真实触发（envelope `Invalid, duplicated, or failing source assertion`）。
- 单分片故障 → Dataset 结构化失败，原始异常保留，无部分结果发布；parts/artifacts 计数回执一致。
- Distributed 验收不得出现 `FINAL`、`final = 1`、`OPTIMIZE ... DEDUPLICATE`、`GROUP BY` 内 `any()` 等缓解字样（回执 SQL 全文审计断言）。
- Trino 非 Iceberg 旅程不得出现 Iceberg 专属系统表查询（回执 SQL 审计断言 `system.metadata.catalogs` 之外的 `$partitions` 等不出现）。
- SQLite 视图表达式列（如 `SELECT a+1 AS x`）→ `pragma_table_info` 空类型走既有诚实拒绝；SQLite virtual 表（FTS 等 `VirtualProperty`）保持拒绝。
- MySQL collation 非 `utf8mb4_0900_bin` 保持拒绝（engine 解除不豁免 collation 门）；SQLite attached 库外关系保持拒绝。
- 跨源联邦、远程 retained 上传、任意 SQL 入口全部保持既有边界，不因本阶段改变。

## 5. 验证命令

```bash
# 静态与既有回归（G1 + 披露）
make test TESTS='tests/test_lazy_source_schema.py tests/test_lazy_scalar_admission.py tests/test_lazy_backend_dispatch.py tests/test_lazy_scalar_execution_adapter.py tests/test_lazy_clickhouse_transport.py tests/test_lazy_trino_transport.py tests/test_lazy_mysql_transport.py tests/test_lazy_multisource_qualification.py tests/test_lazy_multisource_environment.py tests/test_lazy_disclosure.py'
# 既有单节点旅程回归（服务可用时串行）
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py' RUNTIME_WORKERS=1
MARIVO_SQLITE_TEST=1 make runtime-test TESTS='tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py' RUNTIME_WORKERS=1
# 新增集群/非 Iceberg/PG 基线旅程（服务可用时串行）
MARIVO_CLICKHOUSE_CLUSTER_TEST=1 make runtime-test TESTS='tests/test_lazy_clickhouse_distributed_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_NON_ICEBERG_TEST=1 make runtime-test TESTS='tests/test_lazy_trino_non_iceberg_runtime.py' RUNTIME_WORKERS=1
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_postgres_forms_baseline.py' RUNTIME_WORKERS=1
make typecheck TYPECHECK_TARGETS='marivo/analysis'
make lint-agent LINT_TARGETS='marivo/analysis tests/test_lazy_clickhouse_distributed_runtime.py tests/test_lazy_trino_non_iceberg_runtime.py tests/test_lazy_postgres_forms_baseline.py'
make check-agent
git diff --check
```

数据库组串行；先 `bash tests/multisource_environment/manage.sh status <service>` 检查，缺服务或环境缺失记为未验收（阻塞记录到验收文档），不运行 release-check 或启动 MinIO。SQLite/MySQL/PG 既有 runtime 测试的 opt-in 环境变量名以各测试文件头部既有写法为准（实施期核实后修正本节命令）。本阶段披露改动（spec 与 Help effects）属库内文件；`site/src/content/docs/*/latest/` 若实施期 grep 确认含被改措辞则同步双语更新并补 `npm --prefix site run verify:content`。环境 fixture 修改（compose/config/helper）须在实施期核对 `tests/test_lazy_multisource_environment.py` 静态断言的兼容性。

## 6. 完成条件

- §1 目标 1（全 engine 含 Distributed）有真实集群提交 SQL 回执、独立手算期望值、读不稳定样本断言、负向邻接项全批通过；目标 2/3/4（Trino 通用路径、MySQL engine 解除、SQLite 视图）有真实 catalog/单节点旅程证据与负向翻转后的全批通过。
- §1 目标 5 有 PostgreSQL 视图/分区表的真实旅程记录（成功或结构化失败均记录为证据）。
- §2.1 四条 gate 的修改有静态桩测试覆盖（接受/缺失/拒绝三侧）。
- 披露（`_disclosure.py` effects、analysis spec 四处、`test_lazy_disclosure.py` 断言）同批更新且预算测试通过。
- `make check-agent` 通过；`git diff --check` 干净。
- 环境准备（集群 compose、非 Iceberg catalog）的全部新文件与启动步骤写入验收文档；服务不可用导致某单元未验收时，验收文档记录阻塞与已完成部分，C5 状态保持未完成。

验收文档：`docs/superpowers/specs/2026-09-21-multisource-capability-c5-acceptance.md`（日期用实际验收日），机器可读回执沿用 `-execution-receipts.json` 前缀并由验收文档链接，不覆盖 `multisource-slice-N` 记录。

## 7. 排除项

不实施：MySQL 非 `utf8mb4_0900_bin` collation、SQLite virtual/attached 表、Trino `$` 内表、跨 datasource 联邦、远程 retained 上传、任意 SQL 入口、`FINAL`/去重默认、时间轴/方法矩阵扩展（C5 只解除表形态 gate，类型/方法沿既有准入逐单元验收）。读不稳定是已接受语义而非新支持单元，不再列为排除对象。第一轮的 C6–C10 不因本阶段启动；Trino 非 Iceberg 样本不构成 connector 白名单；既有单节点 MergeTree 与 Iceberg 旅程保持既有验收语义（其中被翻转的负向用例按 §3.7 记录于验收文档），不重跑历史证据。C0/总计划 §3.5 与本计划 §0 冲突处以 §0 用户口径为准，验收文档引用本节说明偏差。
