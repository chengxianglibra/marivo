# C3b：既有解析声明与多单位桶

日期：2026-09-18。状态：计划已落盘，未开始实施。依据 [总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)、[C0 清单](2026-09-16-multisource-capability-c0-implementation-plan.md)、[C3a 验收](2026-09-18-multisource-capability-c3a-acceptance.md) 与 [C2 验收](2026-09-16-multisource-capability-c2-acceptance.md)。

计划落盘时 HEAD 为 `facd67b0fb75e527bcacd6a6d090a263e079c29d`，工作区干净，C3a 已提交。本文件先落盘，再实现。

## 1. 前置与范围

C0 对 C3b 的界定是“既有解析声明及多单位桶；相同时间 owner 与 semantic validator”，进入条件为 C3a 与独立计划，且**不计入第一批**。本轮只补齐这两项，不扩展任何其他能力。

交付：

1. 五个后端的字符串时间解析（`StrptimeParse`，含既有 `hour_prefix` 组合轴）；ClickHouse 按格式可表达性准入。
2. 六后端的 `count > 1` 多单位桶（second/minute/hour），采用统一 civil 锚点。
3. `hour_prefix` 复合轴六后端正向验收，以及非法小时值判定的统一（§2.3）。

不新增公共 API、不新增 authoring 参数、不扩展 epoch 解析、日历、累计、timestamp validity、复杂表达式、表形态、跨源执行。DuckDB 既有能力不退化。不修改 packaged skills、AGENTS.md、依赖或 Store 格式；不提交、推送、发布；源码证据不代替 C10。

## 2. 已确认的口径与证据

### 2.1 多单位桶锚点（用户确认：统一 civil 锚点）

当前 `bucket()` 对 `count > 1` 走 ibis `TimestampValue.bucket()`。实测三后端语义互不相同：

| 表达式 | DuckDB（本地实测） | PostgreSQL（本地实测） | ClickHouse（本地实测） |
| --- | --- | --- | --- |
| `bucket(ts, 6hour)` | epoch 对齐：本地 00/06/12/18 | `DATE_BIN(INTERVAL '6 HOUR', …, 2000-01-01)` 单位边界 | `toStartOfInterval(…, INTERVAL 6 HOUR)` 单位边界 |
| `bucket(ts, 7hour)` | epoch 对齐：本地 03/10/17 | 未验证 | 单位边界：本地 00/07/14 |
| `bucket(ts, 30minute)` | epoch 对齐＝单位边界 | 单位边界 | 单位边界 |

同一 grain 会按后端落在不同桶，因此不做统一锚点就不构成一条时间契约。本轮口径：

- **锚点**：报告桶时区（`boundary_timezone`）下的 civil 单位边界。6 小时桶为本地 00:00/06:00/12:00/18:00；30 分钟桶落在本地整点与半点。
- **值域**：`count=1` 的既有行为完全不变；`count>1` 仅对 second/minute/hour 开放，与 `_BuiltinGrain` 既有构造约束一致。
- **不整除 24 小时的 count 不予准入**（用户确认）。当 `count × unit` 不能整除 24 小时时结构化拒绝。理由：这类 count 的锚点网格没有唯一解释，实测三个引擎当前就是三种行为——DuckDB 按 epoch 连续对齐（`7hour` 在本地 03:00 起步）、ClickHouse 按每日重建（本地 00/07/14/21，次日重新从 00 起）、PostgreSQL 按 `2000-01-01` 连续对齐（本地 19:00 起步）。任选其一都会让“同一 grain 在不同后端或不同 time_scope 下落入不同桶”成为契约的一部分。可准入的 count 因此是：hour 的 {1,2,3,4,6,8,12}、minute 的 24 的因数、second 的 24 的因数；`7hour`、`5hour` 等保持拒绝并写入文档与负向邻接项。真实业务需要 7 小时桶时另立阶段决定锚点。
- **DST**：锚点按 civil 单位计算，因此 6 小时桶跨春季 gap 时该桶为 5 小时、跨秋季 fold 时为 7 小时；DST 天本身仍从本地 00:00 起算。该表述写入 owner 文档。
- **多单位桶不引入新的 gap/fold 拒绝**：civil 网格与既有语义轴合法性互不派生。源 naive gap/fold 仍由 C3a 既有 `temporal.local_time` 检查拒绝；已接受的 instant 落入重复报告小时时仍按 civil 坐标合并。

### 2.2 各后端可表达的量化算子（本次只读探测）

“civil 锚点”在源码中统一表达为 **epoch 量化 + civil 锚点修正**（`x - ((x - anchor) mod width)`，anchor 由边界时区的 UTC 偏移 C 决定）：该表达不依赖任何引擎的时区日期运算，六后端均能编译为原生标量。探测结论：

| 后端 | 候选原生算子 | 实测结论 |
| --- | --- | --- |
| PostgreSQL | `DATE_BIN(interval, ts, origin)`；`TO_TIMESTAMP(text, fmt)` | 可用；`date_bin` 支持任意 origin 与 unit 边界，字符串解析返回带服务器时区的 instant |
| MySQL | `FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(x)/W)*W)`；`STR_TO_DATE` | 可用；`UNIX_TIMESTAMP` 按会话时区解释，必须固定 `+00:00` |
| ClickHouse | `toStartOfInterval(x, INTERVAL n UNIT)`；`parseDateTime` | `toStartOfInterval` 仅按单位边界，**无 origin 参数**，7 小时桶给出本地 00/07/14，无法直接表达 C 修正；`parseDateTime` 用类 MySQL 格式串，对 `%M` 报错，返回秒级 `DateTime` |
| SQLite | `STRFTIME`/`DATETIME` 可用 | 已在 C3a 采用连接内确定性标量函数；本轮在 UDF 内按锚点量化 |
| Trino | `DATE_TRUNC` 只到单位；ibis 无 `TimestampBucket` 规则 | 需新增方言 lowering，见 §4.2 |
| DuckDB | `TIME_BUCKET` 仅 epoch 对齐 | 需替换为统一表达 |

### 2.3 非法小时值与跨后端 cast 分歧（本节已按实测更正）

> 更正记录（2026-09-18）：本节初稿曾断言“关系路径不注入前缀列，`_time_column` 静默回退导致结果错误，且 cross 路径没有 `hour_range` 校验”。实测**推翻**了这两条：`source_dependencies.ColumnCollector.axis()` 无条件收集前缀列，`_enrich` 在所有真实 lowering 调用点都注入了 `<name>__prefix`；唯一走回退分支的调用者是 `_validate_temporal_axis`，它传入的正是该轴自身 Entity 的表，`prefix_axis.source_column` 在构造上就正确。`temporal.hour_range` 在关系路径上确实发出（实测 `sales.line_revenue` 得到 `temporal.hour_range` 及其 occurrence 校验）。初稿引用的 `prefix_alignment` 断言在仓库中**不存在**，为误引。按初稿删除回退分支会使 `test_composite_date_hour_axis_is_localized_before_day_bucket` 失败，该做法已作废。保留前缀回退分支。

**真实缺陷**：`hour_range` 校验的是 `table[name].cast("int64")`，而这个 cast 的结果同时被当作小时值参与分桶。各引擎 cast 语义不一致（本地实测）：

| 引擎 | `CAST('oops' AS 整数)` | 后果 |
| --- | --- | --- |
| DuckDB / PostgreSQL / Trino / ClickHouse | 硬报错 | 关闭，但暴露为裸驱动异常而非结构化诊断 |
| **SQLite** | **静默 `0`** | `0` 是合法小时 → `hour_range` 不触发 → **静默发布小时 00** |
| **MySQL** | **静默 `0`** | 同上 |

`hour_prefix` 目前在五个远程后端均被拒绝，因此当前只有 DuckDB 暴露（且为关闭式失败）。C3b 正好要把 VARCHAR 小时列开到这些后端，若不修就是**把已有的关闭式失败变成静默错误结果**。

用户已确认口径：**六后端统一拒绝**。`hour_range` 的值域判定改为对**原始值**的可移植判定，不经过各引擎语义不一的隐式 cast；非整数或越界一律计为 `temporal.hour_range` 违例并在发布前拒绝，字符串列与整数列统一处理。不得保留“SQLite/MySQL 把非法值当 0”的行为，也不得以裸驱动异常代替结构化诊断。

`HourPrefixParse.prefix` 必须指向同 Entity 的 civil-date 轴，`_prefix_axis` 已强制该约束；前缀列本身的类型校验保持现状。

## 3. 六后端目标矩阵（按类型/方法分别记录）

`hour_prefix` 覆盖面指是否验收到该表的日期前缀与小时列联合。每格必须给出真实提交 SQL 证据。

| 后端 | 物理日期前缀 | 物理小时列 | `strptime` 日期型 | `strptime` 时间型 | `hour_prefix` | 多单位桶 |
| --- | --- | --- | --- | --- | --- | --- |
| DuckDB | DATE（既有） | VARCHAR / 整数（既有） | 正/负/边界 | 正/负/DST/NULL | 既有 + 非法值回归 | 新增，六单位 |
| PostgreSQL | DATE（本轮新增） | VARCHAR / INTEGER（本轮新增） | 正/负/边界 | 正/负/DST/NULL | 新增 + 非法值 | 新增，六单位 |
| MySQL | DATE（本轮新增） | VARCHAR / INTEGER（本轮新增） | 正/负/边界 | 正/负/DST/NULL | 新增 + 非法值 | 新增，六单位 |
| SQLite | TEXT 规范 civil（既有） | TEXT / INTEGER（本轮新增） | 正/负/边界 | 正/负/DST/NULL | 新增 + 非法值 | 新增，六单位 |
| Trino | DATE（本轮新增） | VARCHAR / INTEGER（本轮新增） | 正/负/边界 | 正/负/DST/NULL | 新增 + 非法值 | 新增，六单位 |
| ClickHouse | Date/Date32（本轮新增） | String / UInt 各宽度（本轮新增） | 正/负/边界 | 仅可精确表达的格式 | 新增 + 非法值 | 新增，六单位 |

“非法值”指 §2.3 的统一拒绝：VARCHAR/TEXT/String 小时列中的非整数值、越界值在同一 `temporal.hour_range` 契约下失败，六后端行为一致，且以结构化 `MaterializationError` 呈现而非裸驱动异常。整数型小时列与字符串型小时列不因物理类型不同而产生不同的值域契约。

ClickHouse 时间型准入规则（用户确认）：复用既有 `python_to_mysql_strptime()` 翻译，仅当格式可被 `parseDateTime` 精确表达时才准入——即翻译成功、不含亚秒指令、且不含 `%z`/`%Z` 等时区指令。带秒级以下精度或无法翻译的格式结构化拒绝并给出修复建议，不静默截断。ClickHouse 的解析结果是 `DateTime('UTC')`，按物理 instant 处理。

未选单元必须显式记录为“未验收”，不得以其他后端的通过替代。

## 4. 实现顺序及拥有者

### 4.1 第一步：统一非法小时值的判定（先于新能力）

**不改动** `_time_column` 的 `__prefix` 回退分支，也不新增前缀列注入点：现有依赖收集与 `_enrich` 注入已覆盖所有真实 lowering 路径，回退分支仅服务于 `_validate_temporal_axis` 的自表校验。（§2.3 更正记录。）

改造 `hour_range` 的判定表达式：不再以 `table[name].cast("int64")` 的结果做值域比较，而是先对**原始单元格**判定“是否为 0–23 的整数字面量”，再构造小时值。这样 DuckDB/PG/Trino/CH 不再依赖隐式 cast 报错，SQLite/MySQL 不再把非法值静默归零。

判定用可移植表达式（`re_search` 实测六方言均可编译，SQLite 走 ibis 自带的 `_IBIS_REGEX_SEARCH`）。需同时确定并测试空白填充（`' 15'`）的取舍，统一写入校验与文档。

`temporal.hour_range` 校验失败必须是结构化 `MaterializationError`（带 expected/received/repair），不得暴露裸 `_duckdb.ConversionException` 等驱动异常；发布前拒绝，不发布 Artifact。整数型与字符串型小时列共用同一值域契约。

### 4.2 第二步：把多单位桶表达收敛到一套 civil 锚点语义

`compiler/temporal.py::bucket()` 的 `count > 1` 分支改为纯 ibis 表达式。由于 §2.1 已限定 count 必整除 24 小时，网格在每个 civil 日重建，锚点就是该日本地午夜，因此表达式不需要额外的锚点常量解析：

```
width = count × unit_seconds
seconds_of_day = hour*3600 + minute*60 + second
bucket = truncate(x, "day") + ((seconds_of_day // width) × width) as interval
```

该写法只做 civil 字段算术与 civil 午夜加法，不依赖各引擎的时区日期运算，因此不引入 `_marivo_*` 之外的时区函数，也不受 DST 影响（本地午夜在 DST 日依然存在，§2.1 已记录 6 小时桶跨春季 gap 为 5 小时）。实测该表达在六方言均可编译。

`count == 1` 分支保持不变；语义日历分支保持不变。准入层按 §2.1 拒绝不整除 24 小时的 count，`_BuiltinGrain` 的既有构造约束不变。

**必须同批修改的第二个 owner**：`operators/rollup.py::bucket_bounds()` 是纯 Python 侧的桶边界实现（docstring 明写 "Use the admitted DuckDB calendar anchoring without consulting a backend"），当前按 `origin = pd.Timestamp("2000-01-03")` 连续对齐。若只改 `temporal.py::bucket` 而不改它，源侧桶与本地续算桶会落在不同边界，retained rollup 静默给出错误分组。该函数有五个调用点（`rollup.py:336`、`rollup.py:419`、`forecast_values.py:51`、`forecast_values.py:73`、`candidate_values.py:178`），必须一并改为与本轮同一个 civil 午夜锚点，并补齐 `count>1` 的 rollup 续算对照用例。

由于锚点就是 civil 午夜，`lower_temporal()` **不需要**新增量化 UDF：`truncate(x, "day")` 与 `+ interval` 都已有各自的既有 lowering（SQLite 走 `_marivo_truncate`/`_marivo_shift`，Trino/ClickHouse/MySQL 走各自既有路径）。实施时只需验证 `seconds_of_day` 的整除表达式在各后端编译为原生标量；若某后端的整除或取模不可用，才按 C3a 模式新增最小 UDF，并在计划执行记录中写明实测命令与结果。

需要在 `lower_temporal()` 里确认的既有行为：ClickHouse 的 `truncate('D')` 不能经过 `toDateTime` 窄化（C3a 已修 `dateTrunc` 的年份窄化），本轮加 `D` 单位后必须回归该用例。DuckDB 原走 `TIME_BUCKET`，改表达式后其 `DateValue` 输入路径（`bucket(...).cast("date")`）需一并回归。

按 §2.1 的跨后端矩阵补 `tests/test_lazy_temporal_backends.py` 的纯函数用例（不需要数据库）。测试必须用独立 datetime + `zoneinfo` 常量做 oracle，不复用生产 `bucket()` 的输出。

### 4.3 第三步：字符串解析的逐后端编译

`compiler/source_time.py` 的 `StrptimeParse` 分支保持“解析为 naive civil”与“date-only 转 date”两条语义，但编译方式按后端分派：

- DuckDB：现状（`text.as_timestamp(fmt)`，`%z` 时不 cast 回 naive）。
- PostgreSQL：ibis `StringToTimestamp` 已生成 `TO_TIMESTAMP(text, 'YYYY-MM-DD HH24:MI:SS')`，实测正确；格式串需从 Python 翻译为 PostgreSQL TO_TIMESTAMP 模板。新增 `marivo/datasource/strptime.py::python_to_postgres_strptime()`，含 `%H`→`HH24`、`%M`→`MI`、`%S`→`SS`、`%f`→`US`、`%Y`→`YYYY`、`%m`→`MM`、`%d`→`DD`、`%j`→`DDD`、`%%`→`%`。不支持的指令按既有 `_PYTHON_DIRECTIVES_UNSAFE_FOR_MYSQL` 同样的风格拒绝并给修复。
- MySQL：复用 `python_to_mysql_strptime()`，ibis 已生成 `STR_TO_DATE`。实测 `%f` 保留微秒，`DATETIME(6)` cast 保持精度。
- Trino：沿用 `python_to_mysql_strptime()`（ibis 走 `DATE_PARSE`，MySQL 格式串）。
- SQLite：`StringToTimestamp` 无编译规则。按 C3a 既有模式新增连接内确定性标量函数 `_marivo_strptime(text, fmt)` 并在 `sqlite_execution.py::initialize()` 注册（`sqlite3.create_function(name, 2, fn, deterministic=True)`），函数体用 `datetime.strptime` 返回规范六位微秒 civil 文本；`fmt` 以 Literal 传入。不把源表拉到本地。
- ClickHouse：ibis 无 `StringToTimestamp` 规则。注册 `_clickhouse_parse(text, fmt)`，SQL 为 `parseDateTime(text, fmt)`，**fmt 使用 `python_to_mysql_strptime()` 翻译后的格式串**，并对无法翻译/含亚秒/含时区的格式在准入阶段拒绝（见 §3）。解析结果为 `DateTime('UTC')`，`source_time()` 按物理 instant 处理。

`source_dependencies.py`、`observation/contracts.py`、`semantic/validator.py` 的 `strptime`/`hour_prefix` 消费路径不区分后端，保持现状；只新增一个后端资源模块 `marivo/datasource/strptime.py` 的 PostgreSQL 翻译函数，并在 `EngineProfile.translate_strptime_format` 的分派上接入既有 hook（该 hook 当前在生产路径无人消费，本轮把它接入 `source_time()`，使“谁翻译”只有一个 owner）。

### 4.4 第四步：逐后端准入开闸

`scalar_support.py::unsupported_reason()` 当前对 `dimension()` 的判定为：

```python
axis.parse is None
or isinstance(axis.parse, DateParse)
or (timestamp_buckets and axis.logical_type == "timestamp"
    and isinstance(axis.parse, (DatetimeParse, TimestampParse)))
```

本轮改为新增参数 `parsed_time_axes: bool = False`，当为真时额外放行 `StrptimeParse` 与 `HourPrefixParse`（后者要求前缀轴为同 Entity 的 civil date，由 `lowering._prefix_axis` 在编译期再次强制）。`date_buckets` 分支保留现有 `grain.count != 1` 的检查，改为 `grain.count != 1 and grain.unit not in {"second","minute","hour"}`，并在 `grain.unit` 白名单中加入 sub-day 单位。

逐后端 support 模块中打开该开关的顺序必须与对应 runtime 验收一致：每开启一个后端，先把该后端的正向用例跑通，再提交。任何后端若在真实执行中无法满足 §2.1 的锚点或 §3 的精度契约，保持 `parsed_time_axes=False` 并在验收记录中写明阻塞原因，不得以“编译成功”替代。

### 4.5 第五步：披露

`docs/specs/analysis/python-analysis-design.md`（第 65、100 行的“String/epoch parsing, multi-unit buckets”排除项）、`docs/specs/temporal-semantics.md`（grain 章节补 civil 锚点与 DST 表述）、C2/C3a 验收记录中对应段落、`marivo/analysis/observation/_disclosure.py` 的 grain 描述、与 `site/src/content/docs/docs/latest/concepts/analysis-workflow.mdx`（194、229 行）及 `site/src/content/docs/zh-cn/docs/latest/concepts/analysis-workflow.mdx` 双语同步。

漂移/可达性/预算测试按 C3a 同一模式更新，不新建渲染层清单。packaged skills 若需修改须另获用户明确批准。

## 5. Fixture、预期值与负向邻接项

复用 `tests/lazy_temporal_backend_fixtures.py::temporal_source`（新增 `representation="strptime"` 与 `hour_prefix` 两种物理写入）、`tests/lazy_temporal_fixtures.py::temporal_fixture`（DuckDB 路径）、`tests/lazy_scalar_type_fixtures.py::source_writer`、`tests/multisource_environment` 的 reader 连接。

拟新增 `tests/test_lazy_temporal_parsing.py`（纯函数 + 编译期准入）与扩展 `tests/test_lazy_temporal_backend_runtime.py`（真实执行）。管理员只建/清理 UUID 专用表，Dataset 使用既有 reader，本地库用 pytest 临时目录。

正向预期（全部用独立常量人工计算，不以 DuckDB 为唯一对照）：

- 字符串日期：`"20260701"` → 2026-07-01；`"2026-07-01 15:59:00"` + `timezone="UTC"` → 上海报告时区下 2026-07-01 23:59。
- 边界：闰日 `"20240229"`、跨年 `"20261231"`、非整点时区 Asia/Kathmandu 下的日桶。
- 非法值：`""`、`"2026-13-01"`、`"not-a-date"`、`"2026-02-30"`、NULL —— 必须按后端可区分地失败或保持 NULL 语义（MySQL/PostgreSQL 返回 NULL，SQLite/DuckDB 抛错），该差异必须在验收记录中逐后端写明，不得统一成一种行为描述。
- 多单位桶：`6hour`、`12hour`、`8hour` 在上海、Kathmandu、纽约（含春季 gap 日与秋季 fold 日）下的桶起点；`30minute`；`count=1` 回归。全部 count 必须整除 24 小时。
- hour_prefix：本地小时列 `"15"` 与同日前缀 → 2026-07-01 15:00（UTC 报告时区实测值），并在 cross-relationship（`sales.line_revenue` 的坐标轴来自 `sales.orders`）下给出与同表路径一致的结果。
- 非法小时值（§2.3）：字符串小时列取 `'oops'`、`'99'`、`'-3'`、`''`、NULL 时，六后端一律在发布前结构化拒绝为 `temporal.hour_range`；断言 SQLite/MySQL **不**把它当作小时 00 发布，也断言不出现裸驱动异常。空白填充 `' 15'` 的取舍在同一批用例中固定并写入文档。

负向邻接项（必须与正向同批验收）：

- 支持 `6hour` 不代表所有 count 开放：`0`、负数、非整数在构造期拒绝；`week`/`month` 的 `count>1` 仍拒绝；`7hour`、`5hour` 等不整除 24 小时的 count 在准入期结构化拒绝（§2.1）。
- 支持某后端字符串日期不代表其 epoch 整数解析或带时区字符串解析可用。
- ClickHouse 支持纯日期格式不代表支持带秒或带时区格式。
- 支持 `hour_prefix` 不代表前缀轴可以是 timestamp 或跨 Entity。
- 支持整数型小时列不代表字符串列中的非整数值被接受：两者共用同一 0–23 值域契约。
- 未知/未翻译 strptime 指令必须结构化拒绝，不回退为服务器默认格式。

## 6. 验证命令

```bash
# 静态与纯函数
make test TESTS='tests/test_lazy_temporal_parsing.py tests/test_lazy_temporal_backends.py tests/test_lazy_temporal_source.py tests/test_analysis_windows_grain.py tests/test_analysis_executor_subday_bucketing.py tests/test_analysis_grain_public_exports.py tests/test_datasource_engine_profiles.py tests/test_analysis_trino_format_regression.py'
# 本地 DuckDB/SQLite 真实执行
make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py tests/test_lazy_temporal_public_runtime.py tests/test_lazy_temporal_runtime.py tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py' RUNTIME_WORKERS=1
# 逐后端（服务可用时串行执行；不默认启动/停止服务）
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py -k postgres' RUNTIME_WORKERS=1
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py -k mysql' RUNTIME_WORKERS=1
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py -k clickhouse' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py -k trino' RUNTIME_WORKERS=1
marivo/../.venv/bin/python -m mypy --explicit-package-bases --follow-imports=silent --ignore-missing-imports tests/test_lazy_temporal_parsing.py tests/lazy_temporal_backend_fixtures.py
make typecheck TYPECHECK_TARGETS='marivo/analysis marivo/datasource marivo/semantic'
make lint-agent LINT_TARGETS='marivo/analysis marivo/datasource marivo/semantic tests/test_lazy_temporal_parsing.py tests/lazy_temporal_backend_fixtures.py tests/test_lazy_temporal_backend_runtime.py'
make check-agent
npm --prefix site run verify:content
npm --prefix site run build
git diff --check
```

服务状态以 `bash tests/multisource_environment/manage.sh status clickhouse` 为准；缺服务记为未验收，不启动 MinIO、不运行 release-check。

## 7. 完成条件

C3b 完成要求：§3 矩阵中每个“新增”单元有真实提交 SQL 与独立期望值；负向邻接项同批通过；六后端多单位桶锚点跨后端一致；披露（spec、Help、双语 site）同步；`make check-agent` 通过。任何未达成单元写在验收记录的剩余拒绝项中，C3b 状态保持未完成。

验收文档：`docs/superpowers/specs/2026-09-18-multisource-capability-c3b-acceptance.md`（日期用实际验收日），机器可读回执沿用 `-execution-receipts.json` 前缀并由验收文档链接，不覆盖 `multisource-slice-N` 记录。

## 8. 排除项

不实施：epoch/整数时间解析、带时区字符串解析（`%z`/`%Z`）、亚秒级 ClickHouse 字符串解析、日历与周期日历桶、累计与 status-time fold、validity 扩展、复杂值类型、C5 表形态、跨 datasource 联邦、远程 retained 上传。第一轮的 C4、C5、C6–C9 不因本阶段启动。
