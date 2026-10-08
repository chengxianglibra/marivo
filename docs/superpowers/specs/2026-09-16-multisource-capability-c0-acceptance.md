# C0：多数据源能力基线与目标矩阵

日期：2026-09-16。状态：**C0 完成；C1–C10 未开始**。

对应[总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)与 [C0 实施计划及后续清单](2026-09-16-multisource-capability-c0-implementation-plan.md)。本记录是提交时的基线快照，不是运行时能力注册表。

## 1. 身份、范围与证据口径

代码基线：`7ddbe940de5c379f6944182114a7278ed61ef368`（`docs: plan multi-datasource capability completion`）。开始时 `git status --short` 为空。本阶段仅新增 C0 两份文档，并修改本轮总计划与上一轮进度摘要；未修改生产代码、测试、公共 API、依赖或 packaged skills，未提交或发布。

证据标记：

- **K**：本次读取当前实现/owner spec 得出的准入或拒绝结论，不代表实时执行成功。
- **H**：已提交的历史源码 Runtime 验收，具体范围以对应记录为准，本次未重跑。
- **W**：Slice 8 最终 wheel 上的历史旅程，不能扩大为所有类型/表形态/方法组合。
- **L**：C0 本次只读身份、权限、元数据探测，不是 Dataset 验收。
- **U**：本次未验证或未找到对应专项证据；不自动解释为功能缺失。

历史证据来源：[Slice 3 PostgreSQL](2026-09-16-multisource-slice-3-acceptance.md)、[Slice 4 MySQL/SQLite](2026-09-16-multisource-slice-4-acceptance.md)、[Slice 5 Trino](2026-09-16-multisource-slice-5-acceptance.md)、[Slice 6 ClickHouse](2026-09-16-multisource-slice-6-acceptance.md)、[Slice 7 方法](2026-09-16-multisource-slice-7-acceptance.md)、[Slice 8 安装包](2026-09-16-multisource-slice-8-acceptance.md)。Slice 7/8 的机器证据分别见 [源码 reductions](2026-09-16-multisource-slice-7-execution-receipts.json)和 [wheel receipts](2026-09-16-multisource-slice-8-execution-receipts.json)。历史测试计数不作为 C0 测试计数。

当前语义所有者为 [Analysis spec](../../specs/analysis/python-analysis-design.md)、[Semantic 对象模型](../../specs/semantic/semantic-object-model.md)、[时间语义](../../specs/temporal-semantics.md)。矩阵中的“准入”始终还要求合法声明、精确 datasource binding、数值/身份/关系/版本断言通过；不是按后端名称整体授权。

## 2. 当前能力矩阵

### 2.1 后端、类型与物理输入

共同标量集合 S = `string`、`int8/int16/int32/int64`、`float32/float64`、`date`、`decimal` 及 `decimal(p,s)`，其中 `1 <= p <= 38`、`0 <= s <= p`。generic Decimal 是否可作为源还受下面各后端约束；复合结果另见方法矩阵。所有五后端目前检查依赖 Entity 的**全部声明列**。

| 后端 | K：静态类型准入 | K：物理关系/类型与传输 | H / W：已有证据边界 | L / U：C0 状态 |
| --- | --- | --- | --- | --- |
| PostgreSQL | S 加 `boolean`、普通 `timestamp`；不等于 timestamp 时间轴 | `to_regclass` + `pg_attribute` 读取完整物理 schema，经 Ibis mapper 转换；拒绝跨 catalog；psycopg binary named cursor、逐批 Arrow、typed RECORD 身份 | H3：Decimal、signed >2**53、float、date、NULL、字符串、游标/故障；H7：复合指标/关系/date/version。W8：普通表上的有界共同旅程。未找到 Boolean/plain timestamp 全链专项案例，不能由允许声明推导完整覆盖 | L：reader/权限/空 public 元数据；Boolean/timestamp 边界传输与视图/分区专项验收 U |
| MySQL | S，但源 Decimal 必须显式 p/s；Boolean、UInt、timestamp 拒绝 | 仅 InnoDB；signed、float、DATE、显式 Decimal(p<=38)；文字 collation 必须 `utf8mb4_0900_bin`；SSCursor 增量 fetch，Decimal integer SUM 精确解码，结果 warnings 拒绝 | H4/H7：原有 scalar、类型/传输/失败及方法矩阵；W8：同一 collation 的 InnoDB 共同旅程 | L：SELECT 授权、analysis 无表；其他 collation/普通视图尚未开放 |
| SQLite | 仅 `string/int64/float64/date` | main 普通表；INTEGER/INT/BIGINT、REAL/DOUBLE、TEXT binary collation、DATE；逐值 storage class 与 ISO date 验证；native cursor，`query_only=ON`；虚拟表/attached/视图拒绝 | H4/H7：storage/date/数值/方法等；W8：普通表共同旅程；没有服务器账户含义 | 本次仅 K；未连接既有文件，L 不适用，C0 没有新 Runtime 证据 |
| Trino | S 去除 int8/int16；源 Decimal 必须显式 p/s | 验证真实 connector=`iceberg`、`BASE TABLE` 且名称不含 `$`；物理类型经 mapper 与 support 验证；增量 driver pages，非有限浮点拒绝 | H5/H7：单节点 Iceberg 与限定类型、方法、传输/故障；W8：Iceberg 共同旅程 | 服务停止，L 未运行；C5 目标已调整为无 catalog/connector 类型白名单，非 Iceberg 尚未实测；视图仍待选 |
| ClickHouse | S；源 Decimal 必须显式 p/s | engine 精确等于 `MergeTree`；仅 Int8/16/32/64、Float32/64、String、Date、Decimal(p,s) 及允许的 Nullable；`join_use_nulls=1`；Native stream 重建 typed identity，非有限浮点拒绝 | H6/H7：普通本地 MergeTree 的类型/传输/故障及方法；W8：共同旅程 | L：SELECT、readonly、join 设置及一个 MergeTree；Bool/UInt/LowCardinality/DateTime(64) 与其他引擎未开放 |

这里的 W8 共同旅程仅指 sum/mean/weighted mean/ratio、同源关系、native-date daily Forecast、冷进程 retained rollup、重复身份/源移除失败；没有声称 wheel 覆盖每一种声明类型。其历史 wheel 为 `marivo-0.5.3.dev0-py3-none-any.whl`，SHA-256 `ecd8ad834667b50620368fcbfa066b6fc489f461b1c84ddc3c544fe125091004`；C0 未构建或重新安装它。

物理类型路径来源：[共享准入](../../../marivo/analysis/operators/scalar_support.py)、[PostgreSQL support](../../../marivo/analysis/operators/postgres_support.py)、[MySQL support](../../../marivo/analysis/operators/mysql_support.py)、[SQLite support](../../../marivo/analysis/operators/sqlite_support.py)、[Trino support](../../../marivo/analysis/operators/trino_support.py)、[ClickHouse support](../../../marivo/analysis/operators/clickhouse_support.py)。具体校验与转换分别由 [PostgreSQL adapter](../../../marivo/analysis/materialization/postgres_execution.py)、[MySQL adapter](../../../marivo/analysis/materialization/mysql_execution.py)、[SQLite adapter](../../../marivo/analysis/materialization/sqlite_execution.py)、[Trino adapter](../../../marivo/analysis/materialization/trino_execution.py)、[ClickHouse adapter](../../../marivo/analysis/materialization/clickhouse_execution.py)负责。

### 2.2 方法与输入形态

下表逐项与上表后端类型/物理输入取交集；不能将它解释为任意类型都可用于所有方法。L 对所有方法单元均为“未运行 Dataset”。

| 方法 / 输入形态 | PostgreSQL K | MySQL / SQLite / Trino / ClickHouse K | H | W | 目标或排除 |
| --- | --- | --- | --- | --- | --- |
| source-only Population、where、observe、dimension、aggregate、metric projection、rank/limit；直接列 sum/count/min/max | 准入 | 各自限定类型上准入 | H3–H6 | W8 仅共同旅程中的子集 | 保持基线；C1 收敛依赖范围 |
| 直接列 mean、weighted mean、ratio、Metric slice、完整 sufficient components 续算 | 准入；generic Decimal 复合结果仍拒绝 | 同左 | H7，含空/NULL/零分母、关系贡献粒度 | W8 mean/ratio/weighted mean 冷 rollup | C4 扩展表达式与精度 |
| 同一精确 datasource binding 的关系路径，身份/缺失/fanout 校验 | 准入 | 准入 | H7 | W8 关系与重复目标失败 | C1 保留所有连接/断言依赖；不开放跨源 |
| 原生 date 单单位 day/week/month/quarter/year | 准入 | 准入 | H7 各桶坐标 | W8 仅 daily-to-Forecast | 保持；不当作 timestamp 证据 |
| 原生 date snapshot、closed-open/NULL-end validity | 准入 | 准入 | H7 | W8 未单独验收版本矩阵 | 保持 |
| closed-closed validity / 配置 open-end sentinel | 准入 | 拒绝 | H7 PostgreSQL | 未覆盖 | 其他后端 C6 |
| 完整公开聚合到本地 Forecast/Kendall/time discovery、非 Entity compare 和已有 retained-axis attribution | 现有本地方法准入规则 | 同左 | H7 | W8 仅 Forecast 和 retained rollup 子集 | 保持完整输入/状态流向；不等于上传 retained 到远程 |
| 未使用但已声明的复杂列、关系同名列的精确依赖裁剪 | 全声明类型检查可能阻断；PG 还解析完整 schema | 全声明类型检查，物理筛选集合按列名合并 | 无新依赖裁剪成功证据 | 未覆盖 | C1 |
| timestamp/timezone/DST、字符串 parser 时间轴、多单位桶 | 拒绝，即使普通 timestamp 类型本身获准 | 拒绝 | H7 负向边界 | 未正向覆盖 | C3a / C3b 分开 |
| 计算型 measure、Linear graph、未解析精度的 Decimal 组合 | direct-column/graph/结果精度约束拒绝相应形态 | 同左 | H7 负向边界；Decimal 输入且结果已为 float 是不同单元 | 未正向覆盖 | C4；禁止全局 float 降级 |
| 日历、累计、status-time fold | 拒绝 | 拒绝 | H7 记录边界 | 未正向覆盖 | C6，非第一批 |
| exact distinct、quantile/distribution | source-private state 拒绝 | 同左 | H7 边界 | W8 exact distinct 仅负向 | C7，非第一批 |
| sampling、Entity correlation/candidate、source driver、隐藏轴扩展归因 | 无合格远程准备/单次求值路径 | 同左 | H7 边界 | 未正向覆盖 | C8，非第一批 |
| Event/Lifecycle | registry 仅 DuckDB 源实现 | 同左 | H7 边界 | 未正向覆盖 | C9，非第一批 |
| 任意跨 datasource join、远程 retained import/upload | 拒绝 | 拒绝 | H7/H8 边界 | 未正向覆盖 | 本轮排除 |

注册与执行域依据：[registry.py](../../../marivo/analysis/operators/registry.py) 的 `backend_execution`、`_source_admissions`、`implementation`、`admit_local`，以及 [placement.py](../../../marivo/analysis/compiler/placement.py) 的精确 binding 所有权。DuckDB 的 retained import 和本地临时资源仍保留，不受远程只读边界替代。本次没有重新验收 DuckDB。

### 2.3 C1 与 PostgreSQL 表形态专项核查

`scalar_support.unsupported_reason()` 遍历 `required_entities(dataset)` 的 `entity.columns`。`required_entities()` 只提供 Entity 闭包，不是完整列级依赖分析。[共享 adapter](../../../marivo/analysis/materialization/scalar_sql_execution.py) 的 `prepare_dataset()` 将所有 `binding.source` 合并为 `frozenset[str]`，MySQL/SQLite/Trino/ClickHouse 的 `get_schema()` 消费这个集合；同名列的关系归属未编码。PostgreSQL `prepare_dataset()` 仅执行准入，`get_schema()` 解析全部物理列，不能假设共享 adapter 改动自动覆盖它。 DuckDB 的独立 `get_schema()` 同样解析完整物理 schema。共同 Runtime 的 `_validate_source_schema()` 与 `_declared_table()` 仍按全部声明列校验和构造源表。因此 C1 修订为六后端声明表源的统一列依赖契约：compiler 提供按关系归属的事实，准入、Runtime 校验、adapter 类型解析和源投影共同消费；保留物理查询/映射/传输差异，不强制共同继承。这是目标修订，不是当前实现已统一的声明。

PostgreSQL 的 `get_schema()` 通过 `to_regclass` 定位关系并读取 `pg_attribute`，没有 `relkind` 白名单。因此不能从代码判断视图/分区被显式拒绝，也不能据此认定它们已端到端支持。检索 PostgreSQL admission/runtime/methods/adapter 测试及 H3/H7/H8，未找到以普通视图或分区表作为目标的专项成功证据。H3 的 EXPLAIN 顺序扫描记录不证明分区表支持或裁剪。C0 reader 观察到 public 无任何关系，也无法利用现有对象补足该证据。

结论：普通视图、分区表都标为 **K 无显式表种类限制 / H、W 专项证据未找到 / L 无可测对象 / U 实际能力**。C5 首先针对有需求的确切形态补验收；只有复现具体实现缺口并纳入目标后才修改 adapter，不能预设功能缺失。物化视图/外部表没有随本次核查获得支持声明。

### 2.4 后续只读复核：统一策略与记录缺口

在同一代码基线上补充无数据库探针，确认三项尚未修复的差异：

- 相同探测异常下，DuckDB `timezone()` 返回 `system_fallback`，PostgreSQL/Trino/ClickHouse 传播异常；MySQL/SQLite 当前通过无探测 SQL 的 profile 获取系统 fallback。C3a 须依时间 owner 明确共同失败策略，不将物理差异直接当成统一 fallback 的理由。
- Runtime 预记 profile 时区 SQL，而 Trino/ClickHouse adapter 提交另写的 SQL；探针确认实际文本与记录文本不同（当前为大小写和别名差异）。C3a 在实际提交边界统一记录，C10 独立核对；并未据此声称已有查询错算。
- `_validate_source_schema()` 对必要列缺失和类型错误返回相同的 expected/received/repair，不提供具体关系/列/实际类型。C1 纳入统一结构化诊断与可区分的原因。

复核命令 `make test TESTS='tests/test_lazy_statement_statistics.py tests/test_lazy_scalar_transport.py tests/test_lazy_backend_dispatch.py'` 为 **39 passed in 4.16s**，属于 C0 后续只读复核，独立于原始 33 个静态检查。探针没有连接数据库；这些证据确认当前差异，不表示优化已经实现。方案与后续验收统一见 [C0 实施计划](2026-09-16-multisource-capability-c0-implementation-plan.md)的统一诊断、时间与回执约束。

## 3. 第一批目标 / 后续 / 排除矩阵

这些是实施目标，不是已经激活的能力；所有新增正向单元仍须独立计划和真实验收。C0 锁定能力方向，精确精度/表达式/存储规则由对应阶段的进入条件约束，不能留给实现中临时猜测。

| 后端 | C1 | C2 基础类型目标 | C3a 时间目标 | C4 计算/精度目标 | C5 状态 |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL | 关系归属明确的依赖、schema 解析与投影 | 保持既有 Boolean/plain timestamp，补专项传输/极值/冷读；核对常见字符物理映射；不凭空增加原生 UInt | timestamp 小时/日及所需带时区类型，按 owner 明确 instant/civil、精度和 DST | 合法行表达式、Linear、可解析 p/s Decimal；仅精度全链满足的单元 | 视图/分区先补证据；未选新增实现 |
| MySQL | 同上 | Boolean 别名与普通整数区别、UInt、常见字符、普通 timestamp 的精确映射/传输；collation 扩展归 C5 | 选定时间物理类型的小时/日、时区/DST；不能采用服务器默认解释 | 同上，按该引擎精度上限决定具体单元 | 非现有 collation、普通视图均待明确业务需求 |
| SQLite | 同上 | 常见声明映射；Boolean/timestamp 仅在 owner 允许且实际存储类别/值域可验证后进入正向矩阵；UInt 不假装原生 int64 | 依赖获准 timestamp 存储，先定义可实现的时间映射 | 行表达式/Linear；精确 Decimal 无原生基线，不承诺仅靠类型名称实现 | 普通视图待需求；attached/virtual 未选 |
| Trino（不限制 catalog/connector 类型） | 同上 | Boolean、常见字符、普通 timestamp；按实际暴露类型判断，不假定原生 UInt | 目标 timestamp 精度/时区子集、小时/日、DST | 行表达式/Linear/精确 Decimal，保留 connector 下推与只读约束 | 已锁定无 catalog/connector 类型白名单；需通用元数据与 Iceberg/非 Iceberg 真实旅程，具体非 Iceberg 样本待选；当前服务停止 |
| ClickHouse MergeTree / Distributed | 同上 | UInt（含 UInt64 全值域）、Bool、LowCardinality(String)、DateTime，以及合法 Nullable 包装 | DateTime/DateTime64 的选定精度和时区子集、小时/日、DST；T2 映射先验收 | 行表达式/Linear/可解析 Decimal；不将更宽内部累加类型视为更宽公开精度支持 | Distributed 已锁定为首批目标，需真实多分片身份/聚合/关系与故障验收；Replicated/Replacing 仍未选；现有单节点环境不足 |

共同 C1 目标覆盖六后端声明表源，包括已声明未使用 JSON 等复杂列及未声明物理列不阻断已有合法计算；DuckDB 保留已有文件、复杂类型、临时资源和 retained 能力，但不承诺上游 semantic load 可接受任意声明。实际使用 JSON/Array/Map/Struct 的投影、提取、聚合等 T3 正向能力不在 C0–C10 必交矩阵。

C3b（解析、多单位桶）、C6（完整时间状态）、C7（精确集合/分布）、C8（抽样/Entity 等）、C9（Event/Lifecycle）均保留后续阶段；未用第一批 C10 标记完成。用户在 C0 后续确认中锁定了 ClickHouse Distributed 和 Trino 不限制 catalog/connector 类型，均纳入首批 C5 承诺。类型前置、真实多分片环境与代表性非 Iceberg catalog 在 C5 独立计划中落实；环境缺失时记录未完成，不移出目标。Trino 验收样本不构成支持白名单，准入仍验证实际类型、表达式、只读与执行语义；不能仅删除 Iceberg 检查即宣称完成。其他未选表形态继续保留差距。C10 必须汇总实际锁定单元，不宣称全功能对等。

排除项还包括远程 retained 上传、业务数据写入、任意联邦、任意 SQL 入口、新统计定义、隐式后端/本地回退、近似替代、资源调度/预算、无关依赖升级。现有公开聚合的合法本地续算继续保留。

## 4. 本次环境核验 L

观测时间：`2026-09-16T12:45:22.622260+00:00`（北京时间 20:45）。仅通过既有 helper 的默认 reader 连接执行 SELECT/SHOW；未调用 `setup()`、管理员连接、fixture、DML/DDL 或服务生命周期操作。没有写入尝试，所以本次权限结果是目录/设置观察，不替代 H3–H6 的实际拒写验收。

`bash tests/multisource_environment/manage.sh status postgres-analysis`，退出 0，使用专用 Colima socket；输出包括整个现有项目：

| 服务 | 当次状态 | C0 判断 |
| --- | --- | --- |
| postgres-analysis | healthy，15432 | 可以进行 reader 元数据探测 |
| mysql-analysis | healthy，23306 | 同上 |
| clickhouse | healthy，18123 | 同上 |
| trino | Exited (143) | 未连接，未启动；不是本次查询失败 |
| postgres（Trino catalog） | Exited (0) | 保持不动；不是 PostgreSQL analysis 服务 |

| 后端 | 当次返回值 | 限制 |
| --- | --- | --- |
| PostgreSQL | `analysis_reader` / `analysis` / transaction read-only=`on`；database CREATE=false、TEMP=false、public CREATE=false；superuser/createdb/createrole/replication/bypassrls 全 false | public 关系分组为空；关系总数/可 SELECT/可写均为 0，**空集合不能证明对实际表已具备 SELECT 或拒写**；未查全局所有 schema/角色路径 |
| MySQL | `analysis_reader@%` / `analysis` / session transaction_read_only=0；SHOW GRANTS 为全局 USAGE 与 `analysis.*` SELECT | 账户权限只读不依赖 transaction_read_only=1；目标库可见关系为空；未创建对象验证实际拒写 |
| ClickHouse | `analysis_reader` / `qualification`；readonly=1、join_use_nulls=true；SELECT ON qualification.*；system.tables 汇总为 MergeTree 1 张 | 未读取业务行，不证明新类型/引擎；未重复故障、取消或实际拒写 |
| SQLite | 代码 `SQLiteExecutionAdapter.initialize()` 设置 `PRAGMA query_only = ON` | 本次未打开数据库，连接边界为 K，不伪装为 L；没有服务器角色 |

本次可复现只读探测命令如下。凭据仅在 helper 内存中使用，不打印连接字段中的密码，不持久化；这些 helper 的模块入口会执行 setup，因此必须按下述方式导入，而不是直接运行 helper 文件。

```bash
.venv/bin/python - <<'PY'
import json
from datetime import datetime, timezone
from tests.multisource_environment import postgres_analysis as pg, mysql_analysis as my, clickhouse_analysis as ch
print(json.dumps({'observed_at': datetime.now(timezone.utc).isoformat(), 'dataset_acceptance': False}))
with pg.connection() as con:
    queries = {
      'identity_permissions': "SELECT current_user, current_database(), current_setting('transaction_read_only'), has_database_privilege(current_user,current_database(),'CREATE'), has_database_privilege(current_user,current_database(),'TEMP'), has_schema_privilege(current_user,'public','CREATE')",
      'role': "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user",
      'relations': "SELECT c.relkind,count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' GROUP BY c.relkind ORDER BY c.relkind",
      'relation_privileges': "SELECT count(*),count(*) FILTER (WHERE has_table_privilege(current_user,c.oid,'SELECT')),count(*) FILTER (WHERE has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE')) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')"
    }
    for label, sql in queries.items():
        print(json.dumps({'backend':'postgres','label':label,'sql':sql,'rows':con.execute(sql).fetchall()}))
with my.connection() as con, con.cursor() as cur:
    queries = {
      'identity': 'SELECT CURRENT_USER(),DATABASE(),@@session.transaction_read_only',
      'grants': 'SHOW GRANTS FOR CURRENT_USER',
      'relations': "SELECT TABLE_TYPE,ENGINE,count(*) FROM information_schema.tables WHERE TABLE_SCHEMA=DATABASE() GROUP BY TABLE_TYPE,ENGINE ORDER BY TABLE_TYPE,ENGINE"
    }
    for label, sql in queries.items():
        cur.execute(sql)
        print(json.dumps({'backend':'mysql','label':label,'sql':sql,'rows':cur.fetchall()}))
with ch.connection() as con:
    queries = {
      'identity': "SELECT currentUser(),currentDatabase(),getSetting('readonly'),getSetting('join_use_nulls')",
      'grants': 'SHOW GRANTS FOR CURRENT_USER',
      'relations': "SELECT engine,count(*) FROM system.tables WHERE database=currentDatabase() GROUP BY engine ORDER BY engine"
    }
    for label, sql in queries.items():
        print(json.dumps({'backend':'clickhouse','label':label,'sql':sql,'rows':con.query(sql).result_rows}))
PY
```

当次该命令退出 0；三个显式连接均经 context manager 退出。应用显式 SQL 为 PostgreSQL 4 条、MySQL 3 条、ClickHouse 3 条，角色均为身份/权限/元数据；驱动内部初始化查询未完整捕获，不计入总 SQL 声明。没有 Dataset primary/parts、Evidence 或 Findings 发布；Dataset 传输行数/字节、独立业务数值、失败恢复/远程终止证据均不适用或未测，不能记成成功或零开销。

## 5. 验证结果与完成判定

| 本次检查 | 命令 / 结果 |
| --- | --- |
| 基线 | `git rev-parse HEAD` 与 `git status --short`，退出 0；身份见第 1 节 |
| 环境状态 | 上述 manage status，退出 0；未改变服务状态 |
| reader 探测 | 第 4 节 Python 命令，退出 0；没有 Dataset 成功声明 |
| 静态环境/qualification | `make test TESTS='tests/test_lazy_multisource_environment.py tests/test_lazy_multisource_qualification.py'`，退出 0，**33 passed in 5.32s**；不是真实数据库方法验收 |
| 文档范围/空白 | `git diff --check`，退出 0；最终变更仅四份计划/验收文档 |
| 链接与清单 | 检查四份文档的本地 Markdown 目标及 C0 计划引用的测试文件均存在；代码符号/事实与当前源码核对 |

本次未运行 Runtime 全套、`make check-agent`、site build、wheel 安装、release-check 或 MinIO；没有生产行为或公开披露变更需要这些门禁。H/W 仍是历史证据，不以本次 33 个静态测试刷新其日期。

C0 完成：当前与目标/排除矩阵可追溯，旧计划摘要已修正，环境与未验证项明确，C1–C10（含独立 C3a/C3b）有 owner、fixture、命令入口及必须补齐的进入条件。下一阶段仍须先完成独立实施计划；本次没有开始 C1。
