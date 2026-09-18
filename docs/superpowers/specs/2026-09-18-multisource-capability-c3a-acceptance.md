# C3a：原生 timestamp、时区与实际执行记录验收

日期：2026-09-18。状态：C3a 已完成。实施基线 `d0822098329445a020f1bdeebad0926a37d2b48a`。依据 [实施计划](2026-09-18-multisource-capability-c3a-implementation-plan.md)；本记录仅覆盖 C3a，不表示 C3b、C4、C5 或 C10 完成。

## 1. 交付范围

六后端沿既有 scalar methods 准入原生 timestamp 时间轴、count=1 的 hour/day 桶、普通 timestamp 的 equality/range/NULL 谓词。物理 instant、civil 时间和报告桶分开解释；源范围先比较 instant，结果使用报告时区的 civil 坐标。源侧聚合，不把完整源数据搬到本地执行。

| 后端 | 类型与证据 | 物理约束 |
| --- | --- | --- |
| DuckDB | timestamp(0/3/6)、aware timestamp、公开 load→execute；原有 UTC 纳秒无转换路径回归 | 需要有损时区转换的纳秒输入拒绝 |
| PostgreSQL | timestamp/timestamptz(0/3/6)，微秒 scope、Arrow/Parquet、源离线冷读 | 有限合法值；保留 C2 的 infinity 拒绝 |
| MySQL | DATETIME/TIMESTAMP(0/3/6)，前者 civil，后者 aware instant | TIMESTAMP 要求已验证 UTC session；保留既有只读和存储约束 |
| SQLite | 规范六位微秒 civil 文本，NULL 与非法值校验 | 连接内确定性时间函数；不新增任意字符串解析 |
| Trino | Iceberg 实际 timestamp(6)，相邻微秒半开端点、普通谓词、冷读 | 仍以实际元数据为准；非 Iceberg、aware/nanos 不由此开放 |
| ClickHouse | DateTime64(0/3/6)、aware 非 UTC 列、Nullable、既有 DateTime 回归 | 普通 MergeTree；timestamp 执行显式验证 UTC reader，列/报告时区可非 UTC |

Reader 时区统一接收合法 IANA 或显式固定偏移。只有引擎无探测能力才使用有 provenance 的系统 fallback；探测失败、空值、非法名称保留 cause 并结构化失败。显式 parser 或 physical instant 足够决定源意义时跳过 reader authority 探测；独立的物理传输约束仍执行。Session 的报告偏移归一化并保存，重开不会随主机 TZ 改变。

Naive gap/fold 拒绝且不发布 Artifact；两个已知 instant 落入重复的报告小时则合入同一 civil 桶。微秒保持精确；小时到日 retained 续算复用保存的时间事实。无 C3b 字符串/epoch 解析、多单位桶、timestamp validity、日历/累计扩展或远程复杂表形态激活。

## 2. 执行观察边界

`ExecutionAdapter.observe()` 在 adapter 的真实 driver submission 路径记录原始 SQL、role、source/local domain 与 submitted/succeeded/failed 状态。失败保留原始异常，stream 失败更新同一回执。Runtime 的 primary/validation 计数和事件从该边界产生；移除上层 profile 预记和重复计数。Metadata 不冒充 validation；parts 与本地续算分别保留角色/执行域。

[执行回执 JSON](2026-09-18-multisource-capability-c3a-execution-receipts.json) 包含六后端各 4 个真实源旅程：上海跨日、纽约 spring gap、纽约 fall fold、reader/null 跨年范围。四个 scalar adapter 的 16 个旅程仅逐条核对 adapter cursor 调用，不是独立原生驱动抓取；DuckDB/PostgreSQL 的 8 个旅程没有第二路捕获。JSON 明确标记全部 24 个旅程的 `native_driver_capture=not_captured`，未采集的列表不作为独立证据。单独的 DuckDB 原生 execute 代理和 PostgreSQL server cursor 测试验证各自选中的语句路径，不能补齐上述旅程的捕获缺口，也不能证明所有 adapter 路径或网络协议操作。

连接初始化、driver 内部 transaction/fetch/transport 协议不在 SQL recorder 边界；不声称捕获这些隐藏操作。记录没有连接凭据或参数值；SQL 内的 fixture 常量与 UUID 表名是原样真实提交文本。回执仅为诊断证据，不成为执行或恢复权威。

## 3. 独立预期与修复

- 上海 UTC 15:59:59.999999 / 16:00 分属两日；纽约 06:30/07:30 的春季输出 01:00/03:00；秋季两个 01:30 instant 合计 5。
- Kathmandu 与 `UTC+05:45` 使用独立午夜常量；闰日的相邻微秒范围只选择金额 3；NULL 不进入有界范围，普通 NULL 谓词保留金额 4。
- 源表删除后新进程更换主机 TZ，冷读/小时到日续算禁止重新解析源，并校验原 temporal execution facts。
- Trino 原 Ibis `FROM_ISO8601_TIMESTAMP` 会截到毫秒，改为精确 typed cast；MySQL 截桶输出规范化为 native timestamp；ClickHouse 泛型 timestamp cast 与 dateTrunc 编译保持 DateTime64，不降到 DateTime，1960/2200 年真实分桶均通过。
- ClickHouse 原生 timezone 函数拒绝显式 timezone 参数类型；当前只读执行路径依赖 UTC reader，因此增加实际查询校验。未修改只读 profile、未静默设置会话，非 UTC 连接明确失败并给出修复。
- 更新 SQLite 公开 authoring→inspect→preview→Dataset 测试为规范 civil 值的成功流程；超微秒类型继续拒绝；双语示例与 Session/谓词 Help 同步。

## 4. 验证

各组包含重复案例，不能相加为独立测试数量。所有命令在仓库根使用 Makefile/.venv，数据库组串行。

- Trino C3a：`MARIVO_TRINO_ANALYSIS_TEST=1 MARIVO_C3A_RECEIPTS=/tmp/marivo-c3a-receipts make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py -k trino' RUNTIME_WORKERS=1`，原 9 项通过；新增 reader/null、equality/range/NULL 定向复测通过。
- Trino C2/方法回归：`MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py tests/test_lazy_scalar_type_runtime.py -k trino' RUNTIME_WORKERS=1`：76 passed。
- 其余五后端 C3a：同时设置 PG/MySQL/ClickHouse 三个 opt-in 标志，`make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py -k not\ trino' RUNTIME_WORKERS=1`：63 passed；后续边界和 transport 校验补测见最终结果。
- `make test TESTS='tests/test_lazy_temporal_source.py tests/test_lazy_statement_statistics.py'`：29 passed，包含无提交的编译失败、提交/流失败、exact fixed-zone 纳秒路径。
- 新增 fixture/runtime/unit、共享 cold helper、PostgreSQL adapter 与执行统计测试 定向 mypy：6 source files 通过（`--explicit-package-bases --follow-imports=silent --ignore-missing-imports`；外部 driver 缺少 stubs 不作为本测试模块的类型证明）。生产模块由全量 typecheck 覆盖。
- `npm --prefix site run verify:content`：343 required files 通过。
- `npm --prefix site run build`：最终 Astro check/build、API 文档与双语安装脚本输出通过，生成 **321** 页。
- `make check-agent`：5168 passed，lint/import contracts、351 source files 类型检查、API 文档通过；后续一次全量通过为 5170 passed；最后 ClickHouse dateTrunc 修复后的门禁结果见下。

### 相关 Runtime 回归与修复闭环

设置 `MARIVO_POSTGRES_ANALYSIS_TEST=1 MARIVO_MYSQL_ANALYSIS_TEST=1 MARIVO_CLICKHOUSE_ANALYSIS_TEST=1` 后，执行以下合集（`RUNTIME_WORKERS=1`）：

```bash
make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py tests/test_lazy_temporal_runtime.py tests/test_lazy_temporal_public_runtime.py tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py tests/test_lazy_retained_runtime.py tests/test_lazy_scalar_type_runtime.py tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_postgres_execution_adapter.py --tb=short' RUNTIME_WORKERS=1
```

首轮 417 passed、20 skipped、3 failed：MySQL/SQLite 两项沿用 schema 算 validation 的旧口径；PostgreSQL 一项沿用 Ibis 丢失 catalog name NOT NULL 的预期。按当前 owning contract 更新测试，未修改正确的生产 schema 行为。

复测及共享执行路径合集：

```bash
make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py tests/test_lazy_temporal_public_runtime.py tests/test_lazy_postgres_execution_adapter.py tests/test_lazy_mysql_runtime.py tests/test_lazy_sqlite_runtime.py tests/test_lazy_materialization_execution.py tests/test_lazy_event_runtime.py tests/test_lazy_lifecycle_runtime.py tests/test_lazy_distinct_runtime.py tests/test_lazy_distribution_runtime.py --tb=short' RUNTIME_WORKERS=1
```

219 passed、10 skipped；前述三项全部通过。另有 3 项新增边界失败，随后修复：两项 ClickHouse DateTime64 年份被 Ibis 隐式 toDateTime 窄化；一项公开 aware fixture 的 writer 没有固定 UTC。定向执行 `test_clickhouse_datetime64_bucket_does_not_narrow_to_datetime` 与整个 `test_lazy_temporal_public_runtime.py`：6 passed。源离线时间续算、Population timestamp scope、Event/Lifecycle/retained parts 的共享执行观察回归包含在合集。

Skip 只涉及本轮未启用的 Trino，以及 C2 MySQL strict insertion 的文本坏值；Trino 由独立服务轮次执行，真实数据库 NULL/损坏 Boolean 等既有测试仍执行。没有以 skip 作为通过证据。

### 最终门禁

- `MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 MARIVO_C3A_RECEIPTS=/tmp/marivo-c3a-receipts make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py tests/test_lazy_scalar_type_runtime.py -k clickhouse --tb=short' RUNTIME_WORKERS=1`：**110 passed**，包含最终 DateTime64/compiler 修复。
- `MARIVO_TRINO_ANALYSIS_TEST=1 MARIVO_C3A_RECEIPTS=/tmp/marivo-c3a-receipts make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py -k trino --tb=short' RUNTIME_WORKERS=1`：**10 passed**，包括最新 Population timestamp scope。
- 最终生产逻辑后的 `make check-agent`：**5170 passed**，**351 source files** typecheck，lint/import contracts/API docs 全部通过。随后仅收窄谓词 Help 文案并加强 DuckDB 对账断言；Help/Session 定向 **110 passed**，DuckDB runtime SQL/角色/顺序精确对账 **1 passed**。
- 最后 `make lint-agent`：**811 files** 格式检查、lint/import contracts 通过；`git diff --check` 通过。
- 回执合并前验证 **24** 个成功旅程、**205** 次实际提交，每个 primary 恰好 1 条；四个 scalar adapter 的 adapter-cursor SQL 与回执逐条完全相等，证据边界不延伸至原生驱动。JSON 附带本次修改的生产 Python 文件 SHA-256 与运行库版本。

## 5. 环境与未执行层

用户明确授权临时切换专用 Trino 环境并恢复原状态。使用仓库 manage.sh，保留专用 volumes；已通过 `manage.sh status clickhouse` 核验恢复：ClickHouse、MySQL analysis、PostgreSQL analysis 健康，Trino/catalog PostgreSQL 停止。未启动 MinIO、未运行 release-check、未发布、未提交或推送。

本次为源码/本地真实服务验收；没有安装包或真实 Agent E2E 声明，不替代 C10。未调用独立子 agent 审查，也不把本次自检写成独立审查。


## 6. 对抗性审查复核与采纳（2026-09-18）

- **时区规则分歧：采纳缺陷，修正根因表述。** 本机系统 ZoneInfo 为 2026c，Python tzdata wheel 为 2026.2；2026-09-20 Casablanca 的 fold 判断确有分歧。但 DuckDB 正确识别 2026-02-15 的 fold 和 2026-03-22 的 gap，不能归因为“缺少全年斋月规则”。按用户确认，以运行时 ZoneInfo 为准。新校验用 TZif 转换点与 POSIX 延续规则划分区间，ZoneInfo 提供偏移，源侧检查唯一候选与实际转换的一致性；不依赖引擎 ±1 day 启发式判断 host fold。只回传 min/max 与违例计数，不建远程 UDF。实际 Casablanca 复现现已在 primary=0 时结构化失败。增加跨六后端的 source/report 规则分歧、Lord Howe 半小时 fold，以及 2026/2050 年独立 ZoneInfo 对照测试。SQLite 原有 Python 时间函数保留。
- **validation_queries：采纳文档缺口。** 更新 2026-09-01 Runtime owner，明确 attempted physical validation submissions 的角色、失败与编译口径；新增 source/local 角色矩阵回归。当前 diff 中两个数值期望分别为 MySQL 4→3、SQLite 5→4，均去除 schema lookup 的旧人工计数；参数化断言按用例展开不等于四个独立计数定义。Statement recorder 测试改为真实提交；不恢复虚假的 validation 提交数。
- **独立 driver 对账：采纳证据层级修正。** 采用建议中的删除“独立”措辞方案；改名 `adapter_cursor_sql`，全部旅程注明 `native_driver_capture=not_captured`，8 条 DuckDB/PostgreSQL 旅程明确无第二路捕获。没有新增 native driver 抓取，也不再以其他 adapter 测试补齐这些旅程。
- **中文排除项：部分采纳。** 补齐 SQLite 原生 aware 时间存储排除项，并增加双语正文约束测试。原中文正文仍有“字符串时间解析、带时区的字符串解析”，未复现其被删除；英文 String time parsing 亦包含该排除范围。
- **Make 命令：不采纳不可执行结论。** `make -n` 展开保留 TESTS 内的 `--tb=short`；本轮真实 Runtime 命令同样带该参数并执行成功。未修改 Makefile。
- **timestamp 谓词未下推：不成立。** 源侧 primary SQL 中存在 timestamp equality，DuckDB 用 MAKE_TIMESTAMP 表达微秒常量，不能用 SQL 是否包含 `000001` 判断。补充六后端 SQL AST 断言，验证 source 域 primary 的 WHERE 比较和微秒结果；普通维度上的 Dataset.where 是聚合后源 SQL 过滤，不要求改写为聚合前过滤。

审查前的 §4 数字保留为历史过程证据，最终审查修订验证与回执摘要如下（各组重叠，不求和）：

- 最终 `make check-agent`：**5199 passed**，**353 source files** 类型检查，**814 files** 格式、lint/import contracts/API docs 通过。
- 最终五后端 C3a：PG/MySQL/ClickHouse 三个 opt-in 同时开启，`MARIVO_C3A_RECEIPTS=/tmp/marivo-c3a-review-receipts make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py -k "not trino" --tb=short' RUNTIME_WORKERS=1`：**90 passed**。
- 最终 Trino C3a：`MARIVO_TRINO_ANALYSIS_TEST=1 MARIVO_C3A_RECEIPTS=/tmp/marivo-c3a-review-receipts make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py -k trino --tb=short' RUNTIME_WORKERS=1`：**15 passed**。
- 较宽 Runtime 合集：**207 passed、5 failed**；失败均为本次新加 SQL 断言错误地跨 execute 切片回执。Runtime 每次 execute 重置统计，修正测试后五后端谓词及规则分歧定向 **25 passed**，最终六后端 C3a 全部通过；无相应生产谓词改动。
- 规则区间/计数/双语正文定向：**38 passed**；4 个新增/修改测试模块定向 mypy 通过。2026/2050 时区对照及半小时 gap/fold 的期望来自 ZoneInfo 与独立时间常量。
- `npm --prefix site run verify:content`、`npm --prefix site run build` 通过，双语安装脚本通过；`git diff --check` 通过。
- 回执已全部重新采集：**24** 条旅程、**241** 次实际提交，24 条 primary；16 条旅程的 adapter cursor SQL 逐条相等，8 条无第二路捕获。JSON 包含当前生产文件及依赖声明 SHA-256、tzdata 版本和 ZoneInfo 权威声明。审查前 §4 的 205 次提交不代表此次新增时区校验后的数量。
- 最终 `bash tests/multisource_environment/manage.sh status clickhouse` 确认恢复原状态：ClickHouse、MySQL analysis、PostgreSQL analysis 健康，Trino/catalog PostgreSQL 停止。未提交、推送或发布。
