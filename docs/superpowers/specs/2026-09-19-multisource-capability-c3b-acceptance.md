# C3b：既有解析声明与多单位桶验收

日期：2026-09-19。状态：C3b 已完成（Trino 未验收项见 §5）。依据 [实施计划](2026-09-18-multisource-capability-c3b-implementation-plan.md)、[总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)、[C0 清单](2026-09-16-multisource-capability-c0-implementation-plan.md) 与 [C3a 验收](2026-09-18-multisource-capability-c3a-acceptance.md)。

实施基线 `facd67b0f`，验收代码 `6fb4052e6`。本记录仅覆盖 C3b，不表示 C4、C5、C6–C9 或 C10 完成。C3b 不计入第一批。

## 1. 交付范围

六后端沿既有 scalar methods 准入字符串时间解析（`StrptimeParse`）与复合小时前缀轴（`HourPrefixParse`），并把 `count > 1` 的多单位桶收敛到统一 civil 锚点。

| 后端 | `strptime` 日期型 | `strptime` 时间型 | `hour_prefix` | 多单位桶 | 状态 |
| --- | --- | --- | --- | --- | --- |
| DuckDB | 既有 | 既有 | 既有 + 非法值回归 | 新增，六单位 | 已验收 |
| SQLite | 新增 | 新增 | 新增 | 新增 | 已验收 |
| MySQL | 新增 | 新增 | 新增 | 新增 | 已验收 |
| PostgreSQL | 新增 | 新增 | 新增 | 新增 | 已验收（含已知限制） |
| ClickHouse | 新增 | 仅可精确表达的格式 | 新增 | 新增 | 已验收 |
| Trino | 仅 SQL 生成级 | 仅 SQL 生成级 | 未验收 | 仅 SQL 生成级 | **未验收**，服务不可达 |

## 2. 本轮修正的真实缺陷

前四项由实测发现，且都不是计划初稿预判的形态。第五项由最终整体审查发现：C3b 打开的新轴并未被既有的 gap/fold 守卫覆盖。

**（一）非法小时值的跨后端 cast 分歧。** 计划初稿断言"关系路径不注入前缀列导致静默错误结果"，实测推翻：`source_dependencies.ColumnCollector.axis()` 无条件收集前缀列，`_enrich` 在所有真实 lowering 调用点注入，唯一走回退分支的 `_validate_temporal_axis` 传入的正是该轴自身 Entity 的表。真实缺陷是 `hour_range` 校验 `table[name].cast("int64")`，而该 cast 同时充当小时值：DuckDB/PostgreSQL/Trino/ClickHouse 硬报错，**SQLite 与 MySQL 静默归零**，`0` 是合法小时故校验不触发，于是**静默发布小时 00**。按用户确认的六后端统一拒绝口径，值域判定改为对原始单元格的可移植判定（文本走 `re_search` 正则 + 独立空白判定；数值走原始范围比较；布尔与 geospatial 整体拒绝）。整数列与字符串列共用同一 0–23 契约。空白填充（`' 15'`）拒绝并写入契约。

**（二）多单位桶锚点跨后端不一致。** 实测三个引擎三种行为：DuckDB 按 epoch 连续对齐（`7hour` 本地 03:00 起）、ClickHouse 按每日重建（本地 00/07/14/21）、PostgreSQL 按 `2000-01-01` 连续对齐（本地 19:00 起）。按用户确认，**仅准入 width 整除 24 小时的 count**（hour 的 1/2/3/4/6/8/12 等），锚点即该 civil 日本地午夜，表达式为 `truncate(x,"day") + (seconds_of_day // width) × width`，不含引擎桶原语、偏移常量或量化 UDF。同一契约必须同时改两个 owner：`compiler/temporal.py::bucket()` 与 `operators/rollup.py::bucket_bounds()`（后者五个调用点共享）。`7hour`/`5hour` 结构化拒绝。

**（三）SQLite 多单位桶丢失微秒。** ibis 的 SQLite `TimestampAdd` 降低发出 `DATETIME(..., '+N second', 'subsec')`，而 SQLite 的 `'subsec'` 只暴露毫秒：`.123456` → `.123`，得到 `'2026-07-01 12:00:00.000'`（三位），运行时以 `invalid timestamp representation` 拒绝。根因是 `lower_temporal` 的 SQLite 分支只改写 `ops.Literal` 区间，而 civil 桶产生的是 `ops.IntervalFromInteger`。修正为两个形状都解析到整数秒并统一走既有的 `_marivo_shift` 标量。

**（四）gap/fold 守卫未覆盖 C3b 新开的轴。** `lowering._validate_temporal_axis` 原以**物理列类型**判定（`isinstance(value, ir.TimestampValue) and value.type().timezone is None`）。native timestamp 成立，但 `StrptimeParse` 轴的物理列是 string、`HourPrefixParse` 轴是 string/int 小时，两者都不进入守卫。实测同一输入下编译期校验名：native 含 `temporal.local_time.<axis>`，strptime **不含**；端到端 `2026-11-01 01:30:00`（America/New_York 的 fold）在 native 轴被拒绝，在 strptime 轴**静默发布** `2026-11-01 06:00:00`。这违反 `temporal-semantics.md`、`python-analysis-design.md` 与双语 site 三处未按轴类型限定的契约。修正为按**解析结果**判定：`source_time` 额外返回其解析出的 naive 墙钟，守卫读它而非物理列类型。

**（五）version 坐标轴绕过同一守卫。** 分发只遍历 reference/time/dimension 轴，而 version 轴（`valid_from_ref`/`valid_to_ref`/`coordinate_ref`）在 `lowering.py:993`、`:1178`、`:1106` 直接调 `_time_column`。实测 DuckDB 上"带时区的 strptime validity 轴 + 落在 fold 的边界值"静默发布并解析到第二个 occurrence，而同样声明的 native 轴被拒绝。其余五后端因既有"version 轴必须是 civil date"的准入而恰好不受影响。修正为把 version 轴纳入同一分发与 `validated_axes` 去重，并按各 lowering 站点实际使用的时区注册权威，避免出现第二个可能不一致的权威。同时确认运行时交叉校验（`temporal_validation.py`）按设计只覆盖物理 timestamp 列，解析轴由编译期守卫覆盖，不重复建设。

前四项与第五项均以变异验证：回退生产代码后新测试转红（第五项两组变异，其中一组直接回退到真实 HEAD），再恢复。

## 3. 逐后端实现与提交

| 后端 | 解析实现 | 提交 |
| --- | --- | --- |
| DuckDB | `STRPTIME`（不变） | — |
| PostgreSQL | `TO_TIMESTAMP` + `python_to_postgres_strptime` 模板翻译 | `3e4ffbad7` |
| MySQL | `STR_TO_DATE`，`%T` 组合格式 | `3e4ffbad7`，非 UTC/微秒修正同批 |
| Trino | `DATE_PARSE`，`%T` 组合格式 | `3e4ffbad7`（未执行） |
| SQLite | 连接内确定性标量 `_marivo_strptime`（`datetime.strptime`） | `3e4ffbad7` |
| ClickHouse | `parseDateTimeOrNull` + 可精确表达性准入 | `3e4ffbad7` |

激活提交：`f6117bab1`（机制与 SQLite）、`c49534374`（MySQL、ClickHouse）、`b0794d12e`（PostgreSQL）。审查修正：`260dbb2ac`、`1fd340778`、`edb74e72c`、`f28a7742e`、`cdae49513`。

### 关键实现事实

- **不能把预翻译格式交给 ibis。** sqlglot 按 Python strptime 重新映射格式字面量：`%H:%i:%s` → `%T`（正确），但裸 `%i`/`%s` 变字面文本，MySQL 返回 **NULL**。实测 `ibis` 自身发出的 `%M:%s` 在 MySQL 上返回 `None`。因此每个后端用显式标量，格式实参已是引擎终态。
- **`naive_binding` 改为 `timestamp(6)`**：裸 `TIMESTAMP` 在 MySQL 变 `DATETIME`（截微秒），`scale=6` 为 `DATETIME(6)`。
- **ClickHouse 按格式拒绝**：`%f`（亚秒会被截）、`%U`、`%a`、`%A`、`%w` 结构化拒绝。实测 `%Y-%m-%d %a` 对完全正确的输入 `'2026-07-01 Wed'` 返回 **`2025-12-31`**，且位置相关（前导位置只是被忽略而非解析）。`%b`/`%B` 与标量日历时钟指令经多变体实测正确后保留。

## 4. 验证

所有命令在仓库根使用 Makefile/.venv，数据库组串行，全部在验收代码 `6fb4052e6` 上重跑。

- 本地（DuckDB/SQLite）：`make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py tests/test_lazy_sqlite_runtime.py tests/test_lazy_temporal_runtime.py tests/test_lazy_temporal_public_runtime.py tests/test_lazy_retained_runtime.py tests/test_lazy_temporal_parsing.py' RUNTIME_WORKERS=1`：**110 passed、119 skipped**。
- 三服务（PostgreSQL + MySQL + ClickHouse opt-in 同开）：`make runtime-test TESTS='tests/test_lazy_temporal_backend_runtime.py tests/test_lazy_temporal_runtime.py tests/test_lazy_temporal_public_runtime.py tests/test_lazy_sqlite_runtime.py tests/test_lazy_retained_runtime.py' RUNTIME_WORKERS=1`：**210 passed、16 skipped**。
- 逐后端方法回归：`make runtime-test TESTS='tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py tests/test_lazy_scalar_type_runtime.py' RUNTIME_WORKERS=1`：**278 passed、10 skipped**。
- `make test`：**5517 passed、12 skipped**。
- `make typecheck TYPECHECK_TARGETS='marivo/analysis marivo/datasource marivo/semantic'`：300 files 通过。
- `make lint-agent`：816 files 格式检查、lint、import contracts 全部通过。
- `npm --prefix site run verify:content`：343 required files 通过；`npm --prefix site run build`：321 页构建与双语安装脚本校验通过。
- `git diff --check`：通过。

跳过项均为本轮未启用服务（Trino）与既有 opt-in 案例，未以 skip 作为通过证据。

验收记录与实施计划原文中的早期数字（100/183/5511）是加固与小节重构前的中间状态，保留为过程证据，最终口径以上表为准。

### 独立预期

全部期望值来自手写 `datetime`/`timedelta`/`zoneinfo` 常量或 stdlib `strptime`，不复用生产 `bucket()`、`bucket_bounds()` 或 mapper。Oracle 独立性经审查确认。

### 变异验证（防"测试空转"）

- `hour_range` 文本分支回退 → 对应测试转红。
- `temporal.py` 源表达式偏移 `+1800s` → 运行期集成测试 1 红（原先无法捕获，已按审查意见加固）。
- 折叠 oracle 偏移 `+3600s` → 运行时 1 红、纯函数 24 红。
- `timestamp(6)` 回退为 `cast("timestamp")` → 4 个参数化用例转红。
- date-only 守卫回退 → 16 红。
- ClickHouse `%a`/`%A`/`%w` 拒绝移除 → 对应用例逐一转红。
- PostgreSQL 驱动异常包装：本次不实施（见 §5），无变异项。

回归对比：SQLite `count == 1` 与其余五引擎多单位桶的降低 SQL 在修正前后**逐字节相同**（51 个形状 / 5 引擎哈希对照）。

## 5. 已知限制与未验收项

- **Trino 未验收。** 服务停止（容器 `Exited (143)`），仅有 SQL 生成级验证（`DATE_PARSE` 与 `'%Y%m%d'`、`'%Y-%m-%d %T'`），不构成执行证据，故 `trino_support.py` 的 `parsed_time_axes` 保持关闭。未启动或停止任何服务。
- **PostgreSQL `TO_TIMESTAMP` 宽松解析（用户确认只记限制，不加往返校验）。** 实测：`to_timestamp('2026-07-01 15:59:00','YYYYMMDD')` → `2026-01-07`；短输入补零成午夜；尾部垃圾文本被忽略。这类输入返回**非 NULL 的错误 instant**，两种守卫都拦不住。因此 PostgreSQL 是本轮唯一会静默发布错误时间坐标的后端。该事实记录于 `compiler/source_time.py` 的注释与本文档。
- **裸驱动异常在 DuckDB 与 PostgreSQL 上仍存在。** 非法字符串值上 DuckDB 抛 `_duckdb.InvalidInputException`、PostgreSQL 抛 `psycopg.errors.InvalidDatetimeFormat`，均未包装为结构化诊断。用户确认 PostgreSQL 以与 DuckDB 同一标准开放，不为此阻塞。计划 §2.3 的"不得以裸驱动异常代替结构化诊断"条款按其字面范围只约束 `hour_range` 契约，该契约在六后端已满足。
- **`rollup.bucket_bounds` 的 aware 路径仍有遗留项**（生产不可达，已记录未修）：`month`/`quarter`/`year` 分支在午夜切换时区（Havana/Santiago/Cairo/Beirut/Azores）仍抛裸 `pytz.AmbiguousTimeError`；该行为在本轮改动前后逐字相同，为本轮之前既有。另有 `Antarctica/Troll` 的 2 小时 gap 简并，已随 civil 网格修正消除。
- **解析轴的运行时 ZoneInfo 交叉校验未覆盖。** `materialization/temporal_validation.py` 的 `engine_check.temporal_rules`（检测源引擎 tzdata 与运行时 ZoneInfo 不一致）按物理列类型判定，解析轴的物理列是 string，故不进入该检查。解析轴的 gap/fold 由编译期守卫覆盖（§2 第四、五项），但"引擎时区规则与运行时分歧"这一独立检查对解析轴不生效。该限制为本轮之前既有，本轮未扩大也未关闭。
- **`make test` 不含解析轴端到端执行。** 解析轴的正向与拒绝用例标记为 `runtime` 或需服务 opt-in，默认门禁只覆盖编译期路径与新增的 guard 分发测试。解析能力的执行证据位于上表的三服务与本地运行，未进入默认门禁——沿用 C3a 已确立的同一模式。

## 6. 审查

本轮按 subagent-driven 流程执行，每个任务经规格符合性与代码质量两道独立审查，最后做一次整体审查。审查实际拦下的问题包括：`hour_range` 的 geospatial 与 boolean 同类漏洞（与 `NumericValue` 子类有关）、`!= True` 注释所述理由不成立（`isnull()` 才是承载 NULL 的项）、`bucket_bounds` 桶端点造成相邻桶重叠、留用测试可通过任何自洽变异（共同失效）、date-only 解析无守卫、`timestamp(6)` 绑定无测试、ClickHouse 周几指令静默移位、以及 §2 第四、五项的守卫覆盖缺口。

审查也两次修正了实施计划中由协调方写错的事实（§2 第一项的前缀列误判、`bucket_bounds` 漂移的严重性），并多次由实施方反过来证伪协调方的假设。这些更正保留在过程中，未从记录中移除。

## 7. 环境

未启动 MinIO、未运行 release-check、未发布、未提交推送远端。数据库容器状态与开始时一致：ClickHouse、MySQL analysis、PostgreSQL analysis 健康，Trino 与 PostgreSQL catalog 停止。全部执行证据来自上述命令与实测；审查子 agent 的结论均经协调方独立复现后才采纳。
