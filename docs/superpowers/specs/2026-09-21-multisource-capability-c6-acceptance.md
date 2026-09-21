# C6：完整时间状态（日历、累计、status-time fold、剩余 validity）验收

日期：2026-09-21（实际验收日；计划落盘于 2026-09-20）。状态：C6 已完成（逐后端逐单元矩阵见 §2；剩余拒绝项见 §4，均附引擎事实与用户口径边界）。依据 [实施计划](2026-09-20-multisource-capability-c6-implementation-plan.md)、[总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)、[C0 清单](2026-09-16-multisource-capability-c0-implementation-plan.md) 与 [C5 验收](2026-09-21-multisource-capability-c5-acceptance.md)。

计划落盘基线 `79a9aaffd`（C5 验收链末位，工作区干净），验收代码至 `8946cd711`（本验收日 HEAD）。提交链 `e402aad7c..8946cd711` 共 10 个提交（计划 1、剩余 validity 1、日历桶 1、累计 2（含测试钉修正）、status-time fold 4（含边界守护修正）、披露 1）；本文档自身为链外纯文档提交，不计入该链。DuckDB 全程未改动，作为独立对照 oracle。本记录仅覆盖 C6，不表示 C7–C10 或 C10 完成。

## 1. 交付范围与实施状态

计划 §1 四项交付全部在五个远程后端（PostgreSQL、MySQL、SQLite、Trino、ClickHouse）落地并通过真实执行验收；每个 gate 解除有 RED→GREEN 变异证据（过程证据见 §8 回执）：

1. **剩余 validity**（`85d7763e3`）：`scalar_support.py` 的 `closed_open_null_validity` 参数、检查块与失效 import 全删；`mysql_support.py`、`sqlite_support.py`、`trino_support.py`、`clickhouse_support.py` 删除实参（沿 PG 无参先例）。四后端既有负向翻转为 closed-closed 与 sentinel 正向旅程（成员断言镜像 PG oracle，见 §3）；PG 既有旅程与代码零改动（`git diff` 守护为空）；Trino 的 `unresolved_decimal` 变体拆出独立负向测试保持拒绝（decimal gate 不翻转）。
2. **语义日历桶**（`69137b21c`）：`_admitted_bucket()` 新增 semantic 分支（`grain.kind == "semantic"` 且 `definition.temporal_snapshot.calendar_ref == grain.calendar` 时准入，`Ref` 相等比较）；`:444-445` 快照一律拒绝删除。fiscal 日历 day 与 fiscal-month 层级桶五后端真实执行；期间外日期以 NULL-`day` 行实现 NULL 桶语义；未认证日历（observe 结构化失败）与 calendar_ref 不匹配（compile 守卫）两个既有拒绝保持。retained：远程日历 artifact 本地 `rollup` 重分桶旅程一条（既有 `fold_temporal_snapshot` 路径）。
3. **累计 Metric graph**（`c22da912f` + `26a775f49`）：`:461-462` 三形态合一拒绝拆分为 requirement-set 判别；累计经共享端点窗口 lowering（`metric.source_cumulative@v1`，常量 `_CUMULATIVE_REQUIREMENT`）在五后端准入。三种 anchor（all-history、`grain_to_date(month)`、`trailing(2 day)`）、time_scope 端点裁剪、空窗口真零（retained 空覆盖形态）、retained 续算（远程 artifact 本地 `rollup(drop_time=True)`）全部真实执行；`source_requirements`/`requires_source_recompute` 形态与 distinct-membership 轴累计拒绝保留且诊断文本不再误提 cumulative。
4. **status-time fold**（`8cae405df` + `317525cc8` + `3b15f2e3e` + `cf8635980`）：新增 `status_folds: frozenset[str]` 准入参数（形态沿 `linear_graphs` 先例），component 级与 node 级 fold 检查共用同一资格集；`_FOLD_REQUIREMENTS` 使 requirement 判别豁免共享 fold lowering，percentile tuple 结构拒绝守护 `metric.source_quantile@v1` 豁免。各后端按 live 探测证据逐个开放（PG 优先获得远程参照证据；提交顺序为 SQLite `317525cc8` 在前、PG `3b15f2e3e` 在后） argmin/argmax/mean/min/max：PG（ARRAY_AGG）、SQLite（JSON_EXTRACT over MAX/MIN 文本）、Trino（MIN_BY/MAX_BY，服务端无 arg_min/arg_max）、ClickHouse（原生 argMin/argMax）五类全开；MySQL mean/min/max 开、first/last 保持拒绝（引擎事实见 §4）。每开后单元有 live 执行旅程（含 `__mv_status` 非空计数断言、版本化 Entity + fold 复合旅程、五后端 percentile 拒绝负向）。
5. **披露同步**（`8946cd711`，Task 5）：见 §6。

### 实施修订记录（相对计划文本的偏差，均为诚实记录）

计划文本按字面执行不可行或证据不成立处，按代码合法形态与实测事实修正：

1. **requirement-set 判别替代 `metric.cumulative` 字面拆分**（Task 3）：语义 lowering 使每个累计 metric 必带 `requires_source_recompute=True` 与 `source_requirements=("metric.source_cumulative@v1",)`，按 flag 放行会拒绝所有累计。判别式改为"仅含 `_CUMULATIVE_REQUIREMENT` 的 requirement 集豁免，其余 source requirement 一律拒绝"。
2. **ibis-12 遍历 kwargs 修正**（Task 2）：ibis `ops.Node.map` 以 kwargs 传子节点，`SearchedCase` 自带 `results` 参数，六个 rewrite helper 声明位置参数 `results` 在语义 CASE 桶出现即崩（`TypeError: got multiple values for argument 'results'`，pinned `ibis-framework>=12.0.0` 的潜在缺陷，此前旅程未触达）。`temporal_sql.py`、`scalar_projection.py` 与 sqlite/mysql/trino/clickhouse 四 adapter 的 `_lower` rewrite 改为 kwargs-only 读子节点；无 `cases` 节点的表达式行为不变（五后端全量 runtime 套件复跑证明）。
3. **native civil-date 轴收窄**（Task 2）：semantic 分支首版对任意轴开放，`make check-agent` 抓住 C3b 披露契约钉（`test_parsed_time_axes_do_not_open_semantic_calendars` 五后端）；分支收窄为 `logical_type == "date"` 且 parse 为 None/`DateParse` 的原生轴。解析（strptime）轴不开日历桶的既有契约保持。
4. **期间外 → NULL 桶行的实现形态**（Task 2）：运行期 `calendar.coordinate_coverage` 校验拒绝 in-scope 未覆盖日期，计划 §4 的"03-20 → NULL 桶行"不可直接构造；旅程改用 NULL-`day` 行承载该语义（NULL-day 行保留 metric 和、落入 NULL 桶，校验守卫职责不变）。ClickHouse 上 NULL 写入普通 `Date` 列读回 epoch `1970-01-01`，fixture 对未覆盖日行用引擎原生 `Nullable(Date)` 列。
5. **fiscal FM2 窗口截断**（Task 2）：计划 §4 的 FM2=02-16..03-15 在 19 天认证覆盖窗内不可用（同上覆盖校验），旅程按 FM1=02-01..02-15、FM2=02-16..02-19 执行；绑定常量 FM1=140.0、FM2=5.0 不变。
6. **fold 类别粒度为 `frozenset[str]`**（Task 4）：`{"first","last","mean","min","max"}` 逐类别准入而非单一开关——MySQL 实测 argmin/argmax 与 mean/min/max 分歧（ibis 无编译规则），合并开关会迫使 MySQL 三类陪闭或 first/last 无证据开放。percentile tuple 不入集合，五后端结构拒绝，C7 决策保留。
7. **Task-3 钉修正**（`26a775f49`）：审查 Important 发现 scope 裁剪旅程未钉住其宣称的 partial 状态；修正为边界裁剪的诚实覆盖钉（三行完整 86400.0 秒 + 精确 fold 计数）并新增真正 mid-bucket 变体（`time_scope` 端点落在 02-04 半天：43200.0 秒、incomplete、evaluation end 在端点）。同批修正 ClickHouse fixture 诚实性（NULL 金额原存普通 `Double` 读回 0.0，虚增非空计数；改 `Nullable(Double)` 后各引擎计数与 oracle 全同）。
8. **Task-2 死测试修复**（Task 3 批内）：`test_mismatched_snapshot_calendar_ref_fails_at_compile` 在 HEAD 为死测试（仅认证 4 行稀疏日历，`certify_period_calendar_rows` 在断言前即抛"不完全覆盖"，且 builtin-day grain 根本不读拼接快照）——即 Task 2 的 compile 守卫旅程从未真正执行过该守卫。最小修复为其文档化意图（19 行穷举认证 + fiscal semantic grain 经 fiscal-snapshot session 绑定 + compile 拒绝断言）。
9. **钉定 C3b 测试翻转**（Task 3）：`test_parsed_time_axes_do_not_open_cumulative_state` 钉的旧累计封闭不再成立，翻转为 `test_parsed_time_axes_open_cumulative_state_on_qualified_engines`（四个开放 parser 引擎 `reason is None`，Trino 保持精确 parser 拒绝）——放宽累计 gate 永不放宽 parser。
10. **累计 source-requirement 边界守护**（`cf8635980`，Task 4 修正轮）：见 §7 Important 2。
11. **两处编译层潜在缺陷修复**（Task 3，owner 处修复）：retained 覆盖秒数的 `TimestampDelta` 无 SQLite/MySQL 规则（首个远程旅程即 `OperationNotDefinedError`），改共享精确 epoch 秒差；累计窗口 join 中 civil-date 轴以文本比较导致下一桶前缀漏入端点窗口（SQLite 上桶值翻倍），编译器将 civil-date 源时间精确 cast 至午夜 timestamp，SQLite 上该 cast 经新注册的确定性 `_marivo_shift(.., 0)` 标量路由（该引擎唯一渲染规范六位小数文本的路径）。DuckDB oracle 旅程不变（20 passed）。

## 2. 五后端时间状态矩阵（验收状态）

| 单元 | PostgreSQL 17.11 | MySQL 8.4.11 | SQLite（本地） | Trino 483 | ClickHouse |
| --- | --- | --- | --- | --- | --- |
| validity closed-closed | ✅（既有准入，oracle 旅程 `test_version_selection_preserves_membership` 不动） | ✅ 翻转旅程实跑 | ✅ 翻转旅程实跑 | ✅ 翻转旅程实跑 | ✅ 翻转旅程实跑 |
| validity open-end sentinel | ✅（同上） | ✅（`open_end=(None,"9999-12-31")`） | ✅（同左） | ✅（文件既 authored `open_end=("9999-12-31",)`，同一 gate 同一谓词） | ✅（同 MySQL 形态） |
| 日历桶 day 层级（native civil-date 轴 + 认证快照） | ✅ | ✅ | ✅ | ✅ | ✅（NULL-day 行用 `Nullable(Date)`） |
| 日历桶 fiscal-month 层级 + NULL 桶行 + retained 本地重分桶 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 累计 all-history / grain_to_date(month) / trailing(2 day) | ✅ | ✅ | ✅ | ✅ | ✅ |
| 累计 scope 端点裁剪（边界完整 + mid-bucket 43200.0s incomplete）+ 空窗口真零 + retained 续算 | ✅ | ✅ | ✅ | ✅ | ✅ |
| status-time fold `first`（argmin） | ✅ `ARRAY_AGG(.. ORDER BY ..)[1]` | ❌ **保持拒绝**（§4 引擎事实） | ✅ `JSON_EXTRACT(JSON_ARRAY(.., MIN(day)),'$[0]')` | ✅ `MIN_BY`（服务端无 arg_min） | ✅ 原生 `argMin` |
| status-time fold `last`（argmax） | ✅（同形 DESC） | ❌ **保持拒绝**（同上） | ✅（`MAX(day)` 形） | ✅ `MAX_BY` | ✅ 原生 `argMax` |
| status-time fold `mean`/`min`/`max` | ✅ | ✅ | ✅ | ✅ | ✅ |
| 累计 fiscal-grain composite（`grain_to_date(fiscal_month)`） | ✅ DuckDB oracle 旅程（见 §3 财政累计行；远程后端准入未单测） | ✅（同左） | ✅（同左） | ✅（同左） | ✅（同左） |
| percentile fold（tuple） | ❌ 保持拒绝（C7） | ❌ 保持拒绝（C7） | ❌ 保持拒绝（C7） | ❌ 保持拒绝（C7） | ❌ 保持拒绝（C7） |
| 版本化 Entity + status-time fold 复合旅程 | ✅（civil-DATE 快照轴承载版本） | ✅ | ✅ | ✅ | ✅ |

**未验收单元：无。** 五组服务全部可用，计划 §5 全量命令零 FAIL、零环境重试；不存在因服务不可用而记阻塞的后端单元。保持拒绝的格子均为用户口径明确的剩余边界（§4），不是本轮未验收。

## 3. 独立预期值与证据形态

全部期望值为手算常量（计划 §4 + 测试内自建行集扩展），引擎仅作对照；六个引擎旅程逐行对齐 DuckDB oracle（DuckDB 本轮零改动）：

- **累计 all-history**（行 02-02→10, 02-02→100；02-03→30, 02-03→NULL；02-04→0；03-01→5；NULL-day→7）：`02-02=110.0`、`02-03=140.0`、`02-04=140.0`、`03-01=145.0`（NULL 不进 sum；day NULL 行不进时间轴）。
- **`grain_to_date(month)`**：`02-*` 三桶 = 110/140/140，`03-01=5.0`（重置可见：全历史在 03-01 为 145.0）。
- **`trailing(2 day)`**（半开下界 `(end−2d, end]`，边界语义以既有 DuckDB oracle 为准）：`02-02=110.0`、`02-03=140.0`、`02-04=30.0`、`03-01=5.0`。
- **fiscal 日历**：FM1（02-01..02-15）= 140.0（10+100+30），FM2（02-16..02-19）= 5.0；NULL-`day` 行 → NULL 桶行携带 7.0（期间外语义的实现形态，见 §1 修订 4）。
- **validity 成员集**：closed-closed 与 sentinel 两变体均为 `[(1,), (2,), (3,), (4,), (5,)]`——每 id 一条不重叠区间（`start='2026-02-01'`、`day='2026-02-28'`、`end=NULL` 或 `'9999-12-31'`），镜像 PG oracle `test_version_selection_preserves_membership` 的期望集；artifact 记录 `population_authority.version_selection`。
- **fold last-by-status（按 status 时间取每键最后库存再空间 sum）**：行集 a=(10, 30)、b=(100, 40)，c 仅 null-amount status（空间 sum 为 NULL）：first=(10,100)/last=(30,40)/mean=(20,70)/min=(10,40)/max=(30,100)，空间 sums **110/70/90/50/130**（`tests/test_lazy_status_fold_admission.py` 头注 + 各 `*_methods.py` 参数化表）。
- **累计 fiscal-grain composite**（`grain_to_date(fiscal_month)`）：DuckDB oracle 旅程（`test_lazy_calendar_buckets.py`）执行日历月粒度 endpoint（FM1=140.0、FM2=5.0，内置月重置泄漏值为 145.0 以作对照）与无轴标量口径（端点 02-18 落在 FM2 → 5.0）；`endpoint_reset_start` 语义分支经单周期回退变异转红（`[145.0] == [5.0]` 失败）后恢复转绿。复合形在五远程后端准入；其远程执行证据随 C7/C8 日历工作排期。
- **scope 裁剪**：边界对齐裁剪三行保持完整覆盖（各 86400.0 秒、`coverage_complete=True`、非空 evaluation end、源 fold 计数 non-null [3,2,1]/rows [4,3,1]）；mid-bucket 变体 02-04 半天覆盖 43200.0 秒、incomplete、evaluation end 在端点。空窗口真零：恰一行 NULL 值、`coverage_seconds == 0.0`、incomplete、零支持计数——与日历 NULL 桶行（携带真实 sum）是两个不同的空语义，分别断言。

## 4. 剩余拒绝与排除项（用户口径确认不变）

- **MySQL first/last fold 保持拒绝**：live 探测实测 ibis 抛 `OperationNotDefinedError: Compilation rule for 'ArgMin' operation is not defined`（ArgMax 同），该引擎无 argmin/argmax 翻译；源行序模拟不替代 status 序 argmin，不改写绕过。诊断携带"此形态未在本后端 qualification 集"的结构化拒绝；`mysql_support.py` docstring 记录探测证据。mean/min/max 不受影响（native AVG/MIN/MAX）。
- **percentile/quantile fold 五后端全部保持拒绝**（C7）：PG 上 tuple 形实际可编译为 `PERCENTILE_CONT` 且能执行（探测 72.0），但 quantile fold 归 C7 契约，本阶段以结构化 component 级 tuple 检查拒绝；负向邻接项验收其拒绝仍在。
- **其余 source requirement 形态保持拒绝**：`source_requirements`/`requires_source_recompute` 非 `_CUMULATIVE_REQUIREMENT`/`_FOLD_REQUIREMENTS` 豁免集的任何组合拒绝（含 Task 4 修正后的累计×fold 复合负向，五引擎钉定）；诊断只描述 source-private state，不再误提 cumulative。
- **distinct-membership 轴上的累计**保持拒绝（`:421` 既有检查；五远程负向旅程）。
- **解析（strptime）轴不开语义日历桶**：C3b 披露契约钉五后端保持（`test_parsed_time_axes_do_not_open_semantic_calendars`）；native timestamp 版本轴与 epoch 解析既有拒绝保持。
- **未认证日历 / calendar_ref 不匹配**：既有结构化失败不变（observe 期 "missing calendar authority"；compile 期 `bucket()` "missing period authority"，修复后的钉真正执行该守卫）。
- 既有负向基线不回退：sampling、C7/C8 全部单元、跨源联邦、远程 retained 上传、任意 SQL 入口、表形态排除保持通过（`make check-agent` 全绿覆盖）。
- C7–C10 未启动；本记录不表示其完成。

## 5. 验证命令与结果

以下全部在本验收日、`8946cd711` 内容态实跑；数据库组串行（trino/clickhouse 互斥启停，postgres-analysis/mysql-analysis 与两组并存不互斥），先 `manage.sh status` 确认后逐块执行。**零 FAIL，零环境重试**；记录为各命令实际计数：

| 命令 | 结果 |
| --- | --- |
| G1 准入与方法闭包（scalar/group-b/dispatch/adapter 四文件） | **81 passed**（3.41s） |
| G3/G4 静态（temporal_source + retained_fold_matrix） | **56 passed**（4.09s） |
| G3/G4 runtime（retained/temporal/temporal_public，`RUNTIME_WORKERS=1`） | **27 passed**（10.45s） |
| `MARIVO_POSTGRES_ANALYSIS_TEST=1` PG methods+runtime | **51 passed**（13.25s） |
| `MARIVO_MYSQL_ANALYSIS_TEST=1` MySQL methods+runtime | **58 passed**（19.04s） |
| SQLite methods+runtime | **51 passed**（15.75s） |
| `MARIVO_CLICKHOUSE_ANALYSIS_TEST=1` ClickHouse methods+runtime | **78 passed**（34.96s） |
| `MARIVO_TRINO_ANALYSIS_TEST=1` Trino methods+runtime（启动后重播 `trino_analysis.setup()`） | **73 passed**（88.00s） |
| 新 C6 套件（calendar_buckets + cumulative_sources + status_fold_admission，默认引擎） | **35 passed, 48 skipped**（7.59s） |
| 新 C6 套件 `+MARIVO_POSTGRES_ANALYSIS_TEST=1` | **47 passed, 36 skipped**（10.50s） |
| 新 C6 套件 `+MARIVO_MYSQL_ANALYSIS_TEST=1` | **47 passed, 36 skipped**（10.74s） |
| 新 C6 套件 `+MARIVO_TRINO_ANALYSIS_TEST=1` | **47 passed, 36 skipped**（27.02s） |
| 新 C6 套件 `+MARIVO_CLICKHOUSE_ANALYSIS_TEST=1` | **47 passed, 36 skipped**（16.20s） |
| `make typecheck TYPECHECK_TARGETS='marivo/analysis marivo/semantic'` | Success：**263 source files** 无问题 |
| `make lint-agent LINT_TARGETS='marivo/analysis/operators + 五后端 methods 测试文件'` | All checks passed + Import contracts passed |
| `make check-agent` | exit 0，**5636 passed / 16 skipped**（53.55s），API docs built（与 Task 5 收口基线全同，无回归） |
| `git diff --check` | 干净 |
| 披露（site 改动时）：`npm --prefix site run verify:content` / `npm --prefix site run build` | **343 required site files** 验证通过；build exit 0，postbuild install-script 验证通过（输出中 "2 pages found without an \<html\> element" 为 pagefind 对非 HTML 输出文件的既有提示，非构建错误） |

未运行 release-check、未启动 MinIO；opt-in 环境变量未设置的组合按 skipif 干净跳过（不以 skip 冒充通过）。skip 计数为 opt-in 引擎未启用时的正交用例，每个后端的真实单元由对应启用轮覆盖。

## 6. 披露对齐

`8946cd711`（单提交，3 文件）：`docs/specs/analysis/python-analysis-design.md` 四处——validity 句改"五个远程后端都准入两种 validity 区间闭合与开放端哨兵"；排除清单删 cumulative/status-time/semantic calendars、保留真实剩余项，并新增 C6 事实句（日历桶限 native civil-date 轴 + 匹配认证快照、累计经共享端点窗口 lowering 且其余 source requirement 拒绝）；status-time fold 新段落按 Task 4 探测矩阵逐后端陈述（PG ARRAY_AGG、SQLite JSON_EXTRACT、Trino MIN_BY/MAX_BY、CH argMin/argMax；MySQL mean/min/max + first/last 待引擎 argmin/argmax 支持；percentile 全拒绝）；native timestamp 段改"日历桶与累计已在五后端启用……epoch 解析与新 timestamp 版本选择仍不支持"。双语 site latest（EN + zh-cn `analysis-workflow.mdx` 各 3 处，行级 parity；ZH 保留 "first/last 等待引擎 argmin/argmax 支持" 措辞）。spec-wide re-grep（C5 教训全量扫描）：三个改写点之外无过时声明（残留 `仍未启用` 句均指 sampling/source-private 高级方法/retained import，改前改后皆为真）。`_disclosure.py` effects 经全文核对不含四项措辞，不改（9000 字节预算不受影响）；`tests/test_lazy_disclosure.py` 无断言事实命中，漂移测试不需更新；help/动态指引 grep 零命中；packaged skills 只读排查零失配，无需批准项。

## 7. 审查

本轮按 subagent-driven 流程执行：五个实施任务各经独立规格/质量审查（Task 3、Task 4 各含一轮修正提交 `26a775f49`、`cf8635980`），最终整体验收由 Task 6 汇总。审查实际拦下的问题包括：

- **Important 1（Task 3，修正 `26a775f49`）**：scope 裁剪旅程宣称钉 partial 状态但未钉住——探测证明边界对齐裁剪保持三行完整覆盖（86400.0 秒），注释与断言不符。修正为边界裁剪的诚实覆盖钉 + 新增真正 mid-bucket 变体（43200.0 秒、incomplete），变异验证（秒常量弱化 → 四个本地 scope 用例转红）；同批修复审查揭出的 ClickHouse fixture 诚实性缺陷（NULL 金额读回 0.0 虚增计数）。
- **Important 2（Task 4，修正 `cf8635980`）**：fold 准入的 elif 重排副产品使累计 metric 搭载的任意 source requirement 被豁免——live A/B 证据（临时 worktree 于改动前基线 26a775f49，同一 fixture）证明"累计 × fold 基座"复合形在 3b15f2e3e 上被误准入（requirements `('metric.source_cumulative@v1', 'metric.source_temporal_fold@v1')` 返回 `None`）。路由选择**恢复边界**：fold 豁免按图形态 key（累计根只豁免 `_CUMULATIVE_REQUIREMENT`；非累计才豁免 fold requirements），reviewer 的无条件减法草案经 live 验证仍过宽被弃。RED：五引擎新负向 `5 failed`（reason 仍为 None）；GREEN：5/5 + 全文件 42 passed；变异证据：中间版本（无条件减法）使负向保持红、首个守护尝试使累计套件 7 sqlite 失败，最终守护两侧全绿。
- 其余拦下项：Trino 翻转旅程与 ClickHouse 变异轮各遭遇一次 trino/clickhouse 互斥导致的连接拒绝（Task 1 修复为正确串行重跑）；累计套件 case 计数口径修正（reviewer Minor，逐引擎 7+2 形态）；`_fold_registry` Literal 收窄删除 `type: ignore`；四远程 fold 旅程补 `__mv_status` 主查询断言。

## 8. 环境与回执

**服务与顺序（本验收日）**：初始全部 down（as-found）。G1/G3-G4 本地块 → `start postgres-analysis`（17.11，健康门 + reader 权限验证回执）→ PG 块 → `start mysql-analysis`（8.4.11，拒绝矩阵回执）→ MySQL 块 → SQLite 本地块 → `start clickhouse`（单节点 18123，healthy）→ CH 块 → `stop clickhouse` → `start trino`（18080，healthy，重播 `trino_analysis.setup()`；本套件走 iceberg.analysis 路径，不需 `setup_non_iceberg()`）→ Trino 块 → 新 C6 套件按 default/PG/MySQL/Trino 轮执行 → `stop trino` + `start clickhouse` → 新 C6 套件 CH 轮 → 静态与整门禁 → `stop clickhouse` / `stop mysql-analysis` / `stop postgres-analysis`。**结束时状态**：五服务全部 down（postgres-analysis 与 mysql-analysis 在 CH/Trino 块期间保持 up，与 trino/clickhouse 无互斥；trino/clickhouse 全程互斥串行）。

**回执 trail**：沿用 C4/C5 先例，本轮未生成新 `-execution-receipts.json`，以提交哈希为回执 trail——`e402aad7c`（计划）、`85d7763e3`（validity）、`69137b21c`（日历桶）、`c22da912f` + `26a775f49`（累计）、`8cae405df` + `317525cc8` + `3b15f2e3e` + `cf8635980`（fold）、`8946cd711`（披露）；RED/GREEN/变异命令与日志路径在各任务报告内。`capture_submissions` 回执审计内嵌于新增旅程（`__mv_status` 主查询断言、空窗口覆盖形态断言）并随提交入库。未覆盖 `multisource-slice-N` 记录；`MARIVO_SLICE4_RECEIPTS` 机制未动。过程证据：`.superpowers/sdd/c6/task-{1..6}-report.md`。工作树两处既有未跟踪文件（`docs/specs/analysis/README.md`、`docs/specs/analysis/evidence-engine/`）与本阶段无关，保持未跟踪。

## 9. C6 不表示什么

C6 完成**不表示**：C7–C10 已启动或完成；percentile/quantile 与 distribution 可用（C7）；distinct-membership/隐藏轴扩展归因/sampling/Entity correlation/source driver 可用（C7/C8）；Event/Lifecycle 可用（C9）；跨源联邦、远程 retained 上传、任意 SQL 入口可用（既有边界不变）；timestamp 坐标版本轴与 epoch 解析可用；表形态扩展发生；MySQL first/last fold 可用（待引擎 argmin/argmax 翻译，未来 ibis 变更将以旅程失败形式显形）；DuckDB 发生任何改动。`source_requirements`/`requires_source_recompute` 与 distinct 展开形态的累计拒绝、percentile 五后端拒绝、解析轴不开日历桶均为**保留边界**而非待办缺口。
