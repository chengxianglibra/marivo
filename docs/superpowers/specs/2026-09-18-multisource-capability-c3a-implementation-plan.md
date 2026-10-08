# C3a：原生 timestamp、时区与实际执行记录

日期：2026-09-18。状态：C3a 已完成，详见 [验收记录](2026-09-18-multisource-capability-c3a-acceptance.md)。依据 [C0 清单](2026-09-16-multisource-capability-c0-implementation-plan.md)、[总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)及 [C2 验收](2026-09-16-multisource-capability-c2-acceptance.md)。

## 1. 前置与范围

用户已确认仅实施 C3a、微秒以内原生类型、按原因区分时区 fallback。开始实施时 HEAD 为 `d0822098329445a020f1bdeebad0926a37d2b48a`，工作区干净；此前计划时未提交的 C2 已进入该基线。先落盘本计划，再实现。

交付普通 timestamp 谓词、原生时间轴、hour/day 且 count=1 的桶；六后端统一时区、DST 与精度验证；所需 T2 类型映射/传输/冷读；实际提交 SQL、角色、执行域和计数。

不新增公共 API，不扩展字符串/epoch/组合解析、多单位桶、日历、累计、timestamp 版本选择、复杂表达式、表形态、跨源执行。不削弱 DuckDB 已有能力。不修改 packaged skills、AGENTS.md，不提交、推送、发布；源码证据不代替 C10。

## 2. 类型与时间契约

| 后端 | 正向物理范围 |
| --- | --- |
| DuckDB | timestamp(0/3/6)、原生 aware timestamp；保留既有能力 |
| PostgreSQL | timestamp/timestamptz(0/3/6)，有限合法值 |
| MySQL | DATETIME/TIMESTAMP(0/3/6)；前者 civil，后者 instant |
| SQLite | C2 规范六位小数 civil 文本，实际 storage class/日期校验 |
| Trino | 现有 Iceberg 实际 timestamp(6)；不以 DDL 声明替代元数据 |
| ClickHouse | DateTime、DateTime64(0/3/6)，实际列时区、Nullable |

输入为原生类型、无 parser 或匹配的 Date/Datetime/Timestamp 声明。physical instant 保持绝对时间；naive 值依次采用显式 parser、reader、允许的系统 fallback。冲突声明失败。只有引擎无时区能力才 fallback 并记录来源；需要 reader 时区而探测异常/空值/非法时区时结构化失败。已有显式 parser 或 physical instant 充分决定意义时不依赖额外探测。

合法 IANA 与固定偏移共用策略；代表性验收为 UTC、UTC+05:45、Asia/Shanghai、Asia/Kathmandu、America/New_York，不形成白名单。范围为半开 instant 区间，再生成报告时区 civil 桶。秋季两个已知 instant 归入相同重复小时桶；naive gap/fold 无法唯一解释则拒绝。微秒精确保留；需要有损转换的更高精度拒绝，不经浮点 epoch。NULL 沿既有规则。冷读及合法小时到日续算复用持久化事实。

## 3. 实现顺序及拥有者

1. 读取 temporal、datasource、analysis owner 与 live Help；新增测试遵循 marivo-test-fixtures skill。
2. `marivo/datasource/timezone.py` 统一事实解析、无能力与失败分类，adapter 仅探测实际连接。复用 `DatasourceEngineTimezone`/`SourceTimeAuthority`；保留异常 cause。
3. `materialization/execution.py::ExecutionAdapter` 增加内部执行 observer；提交路径记录顺序、source/local、role、原始 SQL 与 submitted/succeeded/failed。每次实际提交只计一次；编译失败零提交，提交失败保留 attempted 记录。移除 `admission.py::_record_statement` 上游 profile 预记及重复计数。覆盖 metadata/timezone/validation/primary/parts/本地续算，保留流/取消/资源生命周期。参数、凭据不进入记录；driver 内部未观测部分显式说明。
4. 对齐 datasource engine metadata、各 adapter `get_schema()`、逐批解码和 Arrow/Parquet；消费 C1 依赖，不另建列集合。先证明类型、精度及 instant/civil 传输，再开放方法。
5. `compiler/source_time.py::source_time`、`temporal.py::bucket` 与 predicates 拥有共同语义；具体 adapter 实现方言映射。SQLite 采用连接内确定性时间标量函数，不拉完整源表到本地；其他后端采用显式时区原生表达式和只读校验。逐后端 support 精确开放，不通过共享白名单激活未验证方法。
6. 沿现有编译/schema/materialization 错误体系区分探测失败、非法时区、冲突、gap/fold、精度损失，给出 expected/received/repair 和必要关系/列身份，不输出业务值。同步 temporal owner、Help 原生注册/预算、动态指导、漂移/可达性测试、EN/ZH latest 文档。packaged skills 如需修改须另获明确批准。

## 4. Fixture、预期与负向项

拟新增 `tests/lazy_temporal_backend_fixtures.py`、`tests/test_lazy_temporal_backends.py`、`tests/test_lazy_temporal_backend_runtime.py`，复用已有 temporal/scalar/PostgreSQL fixture 和adapter cursor 捕获。管理员仅建/清理 UUID 专用表，Dataset 使用 reader，本地数据库用 pytest 临时目录。

- 上海：UTC 2026-07-01 15:59:59.999999 与 16:00:00 分属两日，验证半开端点、小时/日桶。
- 纽约春季：UTC 2026-03-08 06:30/07:30 对应 01:30/03:30；naive 02:30 拒绝。
- 纽约秋季：UTC 2026-11-01 05:30/06:30 对应重复 01:30；金额 2/3 合并桶为 5；naive 01:30 拒绝。
- 闰日、跨年、非整点时区、NULL、0/3/6 精度、微秒相邻端点；按实际物理范围测试。
- timestamp equality/range/NULL、Population/Metric 轴、既有 reducers、小时到日续算；独立 datetime/zoneinfo 常量，不复用生产 mapper/lowering 做 oracle。
- 损坏值、gap/fold、精度损失发布前失败；空输出不掩盖必要验证；无坏缓存/部分 Artifact。
- 源关闭且主机 TZ 改变后，新进程恢复读取及续算零源查询。
- adapter cursor 与 submission 逐条对账 SQL/角色/域/顺序/计数；不将同一适配层捕获称为独立 native driver 证据，覆盖无探测、编译失败、提交失败、流失败及成功。
- 远程字符串解析、多单位桶、纳秒有损转换、新 timestamp validity、C5 表形态保持拒绝；DuckDB 已有能力正向回归。

## 5. 验证命令及完成条件

```bash
make test TESTS='tests/test_lazy_temporal_backends.py tests/test_lazy_temporal_source.py tests/test_datasource_runtime_timezone.py tests/test_analysis_session_timezone.py tests/test_lazy_statement_statistics.py tests/test_lazy_backend_dispatch.py tests/test_lazy_scalar_admission.py tests/test_lazy_postgres_admission.py tests/test_lazy_scalar_transport.py tests/test_lazy_postgres_errors.py'
make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py tests/test_lazy_temporal_runtime.py tests/test_lazy_temporal_public_runtime.py tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py tests/test_lazy_retained_runtime.py' RUNTIME_WORKERS=1
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py' RUNTIME_WORKERS=1
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py' RUNTIME_WORKERS=1
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
make typecheck TYPECHECK_TARGETS='marivo/analysis marivo/datasource'
make lint-agent LINT_TARGETS='marivo/analysis marivo/datasource tests/lazy_temporal_backend_fixtures.py tests/test_lazy_temporal_backends.py tests/test_lazy_temporal_backend_runtime.py tests/test_datasource_runtime_timezone.py tests/test_lazy_statement_statistics.py'
make check-agent
npm --prefix site run verify:content
npm --prefix site run build
git diff --check
```

数据库测试串行；先检查现有服务，不默认启动/停止。缺服务/skip 记为未验收，不运行 release-check 或启动 MinIO。完成后写当日 c3a-acceptance 及关联 execution receipts，分别记录类型、方法、提交和冷读证据；仅全部锁定单元通过才更新为 C3a 完成，C3b 仍未开始。


## 6. 实施中确认的物理边界

- Trino 的 Ibis `FROM_ISO8601_TIMESTAMP` 路径会丢失微秒；以精确的类型化字符串 cast 生成原生字面量，真实相邻微秒范围测试验证。
- ClickHouse `toUTCTimestamp`/`fromUTCTimestamp` 拒绝带显式时区的参数。去除类型时区后的计算与驱动传输依赖 UTC execution timezone，因此新增实际连接 UTC 校验，失败明确修复 reader profile；不静默修改只读连接设置。列时区与报告时区仍支持合法非 UTC，已测试 Asia/Shanghai、Kathmandu 和纽约 DST。这是物理执行准入条件，不是对共同 timezone 解析器的名称白名单。MySQL TIMESTAMP 同样保留已有 UTC session 约束。
- 用户明确授权通过 manage.sh 临时切换 Trino 并恢复原状态；Trino 验收后已恢复 ClickHouse。

## 7. 审查修订计划

用户确认以运行时 ZoneInfo 为时区规则权威，源引擎分歧时结构化拒绝。读取源轴 min/max 聚合确定范围，通过 TZif 的明确转换点和 POSIX 延续规则构造 ZoneInfo 偏移区间，生成源侧唯一性及转换一致性断言；不回传逐行时间、不创建远程 UDF。补充规则分歧、非整点 fold、未来规则、源侧谓词及计数契约回归。校验 SQL 纳入 engine_check 物理提交计数。修正证据层级及中文排除项；不采纳未经复现的 Makefile 改动。
