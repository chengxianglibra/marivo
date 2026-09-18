# C2：基础标量类型验收

日期：2026-09-18。状态：C2 已实现，独立审查、六后端专项及最终门禁通过。

依据 [C2 计划](2026-09-16-multisource-capability-c2-implementation-plan.md) 和 [总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)。代码基线为 `2602989af1680ddf54790e7664ebc03ca912d621`，实现保留于工作区，未提交、推送或发布。

## 1. 实现与边界

- 六后端的标量准入与具体物理 schema 校验对齐；复用 C1 必要列依赖，未增加公共导出或 authoring 参数。
- UInt8/16/32/64 在 DuckDB、MySQL、ClickHouse 保留独立类型、字面量范围、原始整数身份及 Arrow/Parquet unsigned 存储。SUM 继续遵守现有 int64 输出合同，超过范围在发布前失败；min/max 保持 UInt。ClickHouse 复用宽 Decimal 累加，避免先发生 UInt 回绕。
- Boolean 原始单元仅接受 bool、整数 0/1、NULL。MySQL 仅显式 Boolean 绑定的 TINYINT(1) 按 Boolean 解释；SQLite 要求兼容声明及 integer/null。公共 preview/sample/source_health 在 dataframe 转换前检查观察到的原始值，不能把 2 自动转换为 true；完整必要列验证由 Analysis adapter 执行，抽样不证明未采样值。
- 无时区 timestamp 保留原始 civil 字段和精度。MySQL TIMESTAMP、ClickHouse DateTime 需验证 UTC 事实。普通 timestamp 谓词经用户确认归 C3a；C2 不激活时间轴、bucket、DST 或时间解析。
- Arrow 秒级 timestamp 在写 Parquet 前无损拓宽为毫秒，因为 Parquet 无秒级存储单位；逻辑秒精度不变。毫秒/微秒按实际精度存储，冷读不依赖主机 TZ。
- 更新 datasource metadata/Help、Analysis owner/Help 和 EN/ZH site。未修改 AGENTS.md、packaged skills、依赖或 Store 格式。

## 2. 真实物理范围

| 后端 | 本次新增验收 | 明确限制 |
| --- | --- | --- |
| DuckDB | Boolean、UInt 各宽度、string、timestamp(0/3/6)、高值身份及关系、retained reducers | 保留既有高级 Runtime 方法，不向 DuckDB 倒灌远程限制 |
| PostgreSQL | Boolean、text/varchar、timestamp(0/3/6)、公开 load/inspect/execute | CHAR 尾空格语义不等同 string，拒绝；timestamptz/超微秒/infinity 不新增支持 |
| MySQL InnoDB | TINYINT(1) 显式 Boolean、各 UInt 宽度、二进制 VARCHAR、DATETIME(0/3/6) | utf8mb4_0900_bin；CHAR/ENUM/SET/BIT 拒绝；TIMESTAMP 只接受可证明 UTC 会话 |
| SQLite main | Boolean、规范六位小数 civil 文本 timestamp、整数/浮点/字符声明别名 | 全域存储检查；非法日期、非规范精度或非整数 Boolean 即使最终结果为空仍失败 |
| Trino Iceberg BASE TABLE | Boolean、varchar、timestamp(6) 实际元数据、公开 load/inspect/execute | timestamp(0/3) DDL 实际暴露为6；错误精度绑定拒绝，匹配6后执行。CHAR DDL 暴露为 varchar 时按实际类型验证，不能当作原生 CHAR 证据 |
| ClickHouse 本地 MergeTree | UInt 各宽度、Bool、LowCardinality(Nullable(String))、DateTime('UTC')、合法 Nullable | 不新增 Distributed、DateTime64、非 UTC、FixedString、复杂类型 |

测试采用 UUID 专用表，管理员仅负责 fixture DDL/清理，Dataset 使用既有 reader；本地数据库位于 pytest 临时目录。未启用的新表形态仍归 C5。

## 3. 值与失败证据

[test_lazy_scalar_type_runtime.py](../../../tests/test_lazy_scalar_type_runtime.py) 使用独立 Python int/datetime 常量，核对实际 Parquet 类型和值，并在新进程中改变 TZ、禁止 source execution 后恢复读取：

- Boolean 的 false/true/NULL 分组、过滤与重复计数；string 的空串、大小写、尾空格、非 ASCII 与 NULL 保留。
- UInt64 `0, 2**53+1, 2**63, 2**64-1` 身份和冷读，各宽度最大值分组/过滤；复合身份、重复身份失败与同源关系键；min/max 极值与 int64 SUM 成功/溢出。
- 普通 timestamp 闰日和微秒值、精度0/3/6以及 NULL。Trino 冗余 cast 移除后不再把微秒截为毫秒；MySQL 默认精度0和 PostgreSQL metadata 精度保留。
- UInt 小值 mean/weighted mean/ratio/count/min/max、retained components 与删除源表后的本地 rollup 对照。
- SQLite 非法 Boolean/日期/时间格式，MySQL/SQLite bounded decode 损坏值、固定 CHAR 类型拒绝；失败检查无已发布资源。MySQL strict insertion 拒绝文本进入整数字段，相关案例显式 skip，不伪称为 decoder 证据。
- 公开 `load → inspect → Dataset → execute` 覆盖六后端 Boolean/timestamp；损坏 Boolean 再检视/preview/source_health 不给出有效值域结论。

`test_lazy_scalar_types.py` 补充整数范围、Decimal integer 解码、拒绝 float/bool 冒充 UInt、非法时间文本和 metadata fallback。C1 依赖裁剪/关系与已有 backend methods/runtime 回归保留。

## 4. 验证结果

命令均从仓库根运行，使用 `.venv` 或 Makefile；不同组有重复案例，通过数不能相加成独立案例数。

- `make typecheck TYPECHECK_TARGETS='marivo/analysis marivo/datasource'`：247 source files 通过。
- 新测试定向 typecheck 使用 `--explicit-package-bases --follow-imports=silent`：3 files 通过。
- `make lint-agent`：806 files、lint、import contracts 通过。
- `MARIVO_TRINO_ANALYSIS_TEST=1 MARIVO_POSTGRES_ANALYSIS_TEST=1 MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_scalar_type_runtime.py tests/test_lazy_source_dependency_runtime.py tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py --tb=short' RUNTIME_WORKERS=1`：156 passed、19 skipped。跳过为未启用的 ClickHouse 与 MySQL strict insertion 文本案例；ClickHouse 另行运行。
- `npm --prefix site run verify:content`：343 required files 通过。
- `npm --prefix site run build`：API 文档、Astro check/build 和中英文安装脚本验证通过，生成 321 页。
- 全量默认测试首次 5138 passed、2 failed；两项旧测试分别将 MySQL mock 改到实际 Backend.connect seam，以及更新 SQLite parser 的拒绝诊断。定向复测 22 passed，独立审查确认没有掩盖实现问题；全量重跑通过，见下方最终门禁。

- `MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 MARIVO_POSTGRES_ANALYSIS_TEST=1 MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_scalar_type_runtime.py tests/test_lazy_source_dependency_runtime.py tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py --tb=short' RUNTIME_WORKERS=1`：**288 passed、12 skipped**；跳过为本轮关闭的 Trino 和 strict insertion 文本案例。包含全部最新 UInt 极值/键、SQLite alias、UTC 物理类型与字符串包装用例。

- `make runtime-test TESTS='tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py tests/test_lazy_materialization_execution.py tests/test_lazy_retained_runtime.py tests/test_analysis_decimal_e2e.py tests/test_lazy_temporal_runtime.py tests/test_lazy_event_runtime.py tests/test_lazy_lifecycle_runtime.py tests/test_lazy_candidate_runtime.py tests/test_lazy_driver_runtime.py tests/test_lazy_distinct_runtime.py tests/test_lazy_distribution_runtime.py tests/test_lazy_adapter_runtime_acceptance.py --tb=short' RUNTIME_WORKERS=1`：**187 passed**。

- 最终 `make check-agent`：**通过**；lint、import contracts、全量 typecheck、**5140 passed** 默认测试及 API docs build 全部成功。
- 最终 `git diff --check`：通过。

## 5. 独立审查与环境

独立子 agent 按 `marivo-review` 审查实际 diff。采纳并修复：Unknown metadata mapper fallback、PostgreSQL timestamp 精度丢失、UTC metadata 兼容与原始 Boolean 解码。最终复审 **No findings**，独立轻量测试 **67 passed**；审查未运行数据库 Runtime，不能代替上述真实测试。

环境已按开始状态恢复：ClickHouse、MySQL analysis、PostgreSQL analysis 健康运行；Trino 及其 catalog PostgreSQL 停止。仅操作专用 `marivo-multisource` 项目，没有启动 MinIO、重建 volume 或执行 release-check。源码验收不等于 C10 安装包验收；C3a、C4、C5 不由本记录宣称完成。


## 6. 外部 review 甄别与补充修复

- **采纳数值精度问题**：真实 SQLite 复现 `4611686018427387905 / NULL / 9007199254740993` 经 pandas 变 float64 并舍入。虽然 Ibis 原路径已有此行为，新增解码 owner 现从原始 cell 构造 nullable integer 列，避免先用已损失精度的 float 修复。MySQL uint64 另验证最大值。公开 `md.inspect().sample(persist_values=True)` 和 `catalog.preview()` 都核对精确整数与 NULL。
- **采纳错误结构问题**：MySQL/SQLite 共用 `checked_dataframe`；非 dataframe converter 结果和不兼容整数转换产生带 expected/received/repair 的 DatasourcePreviewError，不再抛裸 TypeError。
- **采纳 CHAR inspection 问题**：PostgreSQL/MySQL/Trino fixed CHAR 保留物理区别，typed projection 结构化拒绝，并提示改绑变长字符列；SQLite 的合格 BINARY CHAR 不受此限制。
- **采纳测试缺口**：补 PostgreSQL 正/负 infinity、MySQL MEDIUMINT UNSIGNED 的 uint32 极值映射、MySQL 非 UTC session 拒绝。UTC/+00:00 白名单是有意的保守边界，文档明确；本次不扩展 Etc/UTC/GMT。
- **不采纳事实不符的项**：Boolean 适配代码方向正是“显式 Boolean + 物理 TINYINT(1)”；真实 CHAR 测试已含 PG/MySQL/Trino；runtime 文件本身已有 SQLite aliases、UTC 类型和包装用例，验收记录的命令包含它们；最终门禁状态也已补齐。只有 MySQL strict insertion 的文本案例跳过，2/-1 实际执行并拒绝。
- **不按功能缺陷处理纯重构建议**：三个 SQL adapter 的 cast 消除条件可日后单独整理；不同层的类型语法、准入和存储验证有各自职责，不建立新的全局类型 owner。SQLite 仅允许 timestamp/timestamp(6) 是固定六位文本契约，改用0–6通用 helper 会扩大准入。内部 _cell 复用、测试物理映射和单用途冷读脚本未发现错误或违反强制规范，不为风格扩大本次改动。

补充定向验证：静态 108 passed；公开整数/Boolean/timestamp及 infinity/MEDIUMINT 真实测试 15 passed、4 skipped（本轮未启用 CH/Trino）；非 UTC session 1 passed。补充 `make check-agent` 全量通过：**5151 passed**、349 个 source files 类型检查、lint/import contracts/API docs 成功；新增两份测试模块定向 typecheck 通过。独立子 agent 复审 **No findings**，78 项轻量测试通过，并独立验证 UInt64 极值、Decimal 整数、空结果及全 NULL 列。文档内容检查 343 files 通过；文档站重建 321 页及中英文安装脚本检查通过。最终 `git diff --check` 通过。
